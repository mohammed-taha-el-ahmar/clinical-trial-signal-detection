"""
Azure Synapse Analytics client.

Writes adverse events and signal alerts to dedicated SQL pool tables.
All operations are no-ops when SYNAPSE_SERVER is not configured,
which allows the app to run fully in-memory for local dev and CI.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from config import settings
from models import AdverseEvent, SignalAlert

log = logging.getLogger(__name__)

_SYNAPSE_AVAILABLE = bool(settings.synapse_server)

# Guard against missing ODBC driver (local dev / CI without unixodbc)
try:
    import pyodbc

    _PYODBC_AVAILABLE = True
except ImportError:
    _PYODBC_AVAILABLE = False


def _connection_string() -> str:
    return (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={settings.synapse_server};"
        f"DATABASE={settings.synapse_database};"
        f"UID={settings.synapse_username};"
        f"PWD={settings.synapse_password};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )


@contextmanager
def _cursor() -> Generator:
    if not _PYODBC_AVAILABLE:
        raise RuntimeError("pyodbc is not available — install unixodbc and pyodbc")

    conn = pyodbc.connect(_connection_string(), autocommit=True)
    try:
        cur = conn.cursor()
        yield cur
    finally:
        conn.close()


DDL_EVENTS = """
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'adverse_events')
CREATE TABLE adverse_events (
    event_id        NVARCHAR(36)   NOT NULL,
    trial_id        NVARCHAR(20)   NOT NULL,
    site_id         NVARCHAR(20)   NOT NULL,
    patient_id      NVARCHAR(20)   NOT NULL,
    arm             NVARCHAR(20)   NOT NULL,
    symptom_code    NVARCHAR(20)   NOT NULL,
    symptom_label   NVARCHAR(100)  NOT NULL,
    severity        NVARCHAR(30)   NOT NULL,
    reported_at     DATETIME2      NOT NULL,
    age_group       NVARCHAR(10)   NOT NULL,
    is_serious      BIT            NOT NULL DEFAULT 0,
    ingested_at     DATETIME2      NULL
)
WITH (DISTRIBUTION = HASH(trial_id), CLUSTERED COLUMNSTORE INDEX);
"""

DDL_SIGNALS = """
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'signal_alerts')
CREATE TABLE signal_alerts (
    signal_id           NVARCHAR(36)   NOT NULL,
    trial_id            NVARCHAR(20)   NOT NULL,
    arm                 NVARCHAR(20)   NOT NULL,
    symptom_code        NVARCHAR(20)   NOT NULL,
    symptom_label       NVARCHAR(100)  NOT NULL,
    window_start        DATETIME2      NOT NULL,
    window_end          DATETIME2      NOT NULL,
    event_count         INT            NOT NULL,
    incidence_rate      FLOAT          NOT NULL,
    confidence          NVARCHAR(10)   NOT NULL,
    detected_at         DATETIME2      NULL
)
WITH (DISTRIBUTION = REPLICATE, CLUSTERED COLUMNSTORE INDEX);
"""


def ensure_schema() -> None:
    if not _SYNAPSE_AVAILABLE or not _PYODBC_AVAILABLE:
        log.debug("Synapse not configured or pyodbc unavailable — skipping schema creation")
        return
    with _cursor() as cur:
        cur.execute(DDL_EVENTS)
        cur.execute(DDL_SIGNALS)
    log.info("Synapse schema verified")


def insert_events(events: list[AdverseEvent]) -> None:
    if not _SYNAPSE_AVAILABLE or not _PYODBC_AVAILABLE or not events:
        return
    sql = """
        INSERT INTO adverse_events
            (event_id, trial_id, site_id, patient_id, arm,
             symptom_code, symptom_label, severity, reported_at, age_group, is_serious)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """
    rows = [
        (
            e.event_id,
            e.trial_id,
            e.site_id,
            e.patient_id,
            e.arm.value,
            e.symptom_code,
            e.symptom_label,
            e.severity.value,
            e.reported_at,
            e.age_group,
            int(e.is_serious),
        )
        for e in events
    ]
    with _cursor() as cur:
        cur.executemany(sql, rows)
    log.info("Inserted %d adverse events into Synapse", len(events))


def insert_signals(signals: list[SignalAlert]) -> None:
    if not _SYNAPSE_AVAILABLE or not _PYODBC_AVAILABLE or not signals:
        return
    sql = """
        INSERT INTO signal_alerts
            (signal_id, trial_id, arm, symptom_code, symptom_label,
             window_start, window_end, event_count, incidence_rate, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """
    rows = [
        (
            s.signal_id,
            s.trial_id,
            s.arm.value,
            s.symptom_code,
            s.symptom_label,
            s.window_start,
            s.window_end,
            s.event_count,
            s.incidence_rate,
            s.confidence,
        )
        for s in signals
    ]
    with _cursor() as cur:
        cur.executemany(sql, rows)
    log.info("Inserted %d signal alerts into Synapse", len(signals))


# ── Read functions (hydrate API from Synapse when available) ─────────────────


def fetch_events(limit: int = 500) -> list[AdverseEvent]:
    """Fetch adverse events from Synapse, most recent first."""
    if not _SYNAPSE_AVAILABLE or not _PYODBC_AVAILABLE:
        return []
    sql = f"SELECT TOP {limit} * FROM adverse_events ORDER BY reported_at DESC"
    try:
        with _cursor() as cur:
            cur.execute(sql)
            columns = [col[0] for col in cur.description]
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("Failed to fetch events from Synapse: %s", exc)
        return []

    events: list[AdverseEvent] = []
    for row in rows:
        data = dict(zip(columns, row, strict=False))
        events.append(
            AdverseEvent(
                event_id=data["event_id"],
                trial_id=data["trial_id"],
                site_id=data["site_id"],
                patient_id=data["patient_id"],
                arm=data["arm"],
                symptom_code=data["symptom_code"],
                symptom_label=data["symptom_label"],
                severity=data["severity"],
                reported_at=data["reported_at"],
                age_group=data["age_group"],
                is_serious=bool(data["is_serious"]),
            )
        )
    log.info("Fetched %d events from Synapse", len(events))
    return events


def fetch_signals() -> list[SignalAlert]:
    """Fetch signal alerts from Synapse."""
    if not _SYNAPSE_AVAILABLE or not _PYODBC_AVAILABLE:
        return []
    sql = "SELECT * FROM signal_alerts ORDER BY detected_at DESC"
    try:
        with _cursor() as cur:
            cur.execute(sql)
            columns = [col[0] for col in cur.description]
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("Failed to fetch signals from Synapse: %s", exc)
        return []

    signals: list[SignalAlert] = []
    for row in rows:
        data = dict(zip(columns, row, strict=False))
        signals.append(
            SignalAlert(
                signal_id=data["signal_id"],
                trial_id=data["trial_id"],
                arm=data["arm"],
                symptom_code=data["symptom_code"],
                symptom_label=data["symptom_label"],
                window_start=data["window_start"],
                window_end=data["window_end"],
                event_count=data["event_count"],
                incidence_rate=data["incidence_rate"],
                confidence=data.get("confidence", "medium"),
                severity_distribution={},  # not stored in Synapse
                detected_at=data.get("detected_at") or data["window_end"],
            )
        )
    log.info("Fetched %d signals from Synapse", len(signals))
    return signals
