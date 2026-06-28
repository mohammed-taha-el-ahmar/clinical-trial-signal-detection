"""
FastAPI backend for the clinical trial safety signal dashboard.

Endpoints:
  GET  /api/stats          — aggregated dashboard statistics
  GET  /api/events         — recent adverse events (last N)
  GET  /api/signals        — active signal alerts
  POST /api/detect         — run local detection over loaded fixtures (CI / demo)
  GET  /api/health         — liveness probe
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agent.detector import detect_signals
from agent.synapse_client import (
    ensure_schema,
    fetch_events,
    fetch_signals,
    insert_events,
    insert_signals,
)
from config import settings
from models import AdverseEvent, DashboardStats, SignalAlert
from simulator.runner import load_fixtures

log = logging.getLogger(__name__)

# ── in-memory store (replaces Synapse when not configured) ───────────────────
_events: list[AdverseEvent] = []
_signals: list[SignalAlert] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Load data on startup.
    Priority: Synapse (real data) → local fixtures (demo / CI fallback).
    """
    global _events, _signals
    ensure_schema()

    # Try Synapse first — populated by Event Hub → ASA pipeline
    synapse_events = fetch_events(limit=500)
    synapse_signals = fetch_signals()

    if synapse_events:
        _events = synapse_events
        _signals = synapse_signals or detect_signals(
            _events,
            window_minutes=settings.signal_window_minutes,
            rate_threshold=settings.signal_rate_threshold,
            min_events=settings.signal_min_events,
        )
        log.info(
            "Startup: loaded %d events and %d signals from Synapse",
            len(_events),
            len(_signals),
        )
    else:
        # Fallback to fixtures for local dev / CI
        _events = load_fixtures()
        _signals = detect_signals(
            _events,
            window_minutes=settings.signal_window_minutes,
            rate_threshold=settings.signal_rate_threshold,
            min_events=settings.signal_min_events,
        )
        insert_events(_events)
        insert_signals(_signals)
        log.info(
            "Startup: loaded %d events from fixtures, detected %d signals",
            len(_events),
            len(_signals),
        )
    yield


app = FastAPI(title="Clinical Trial Signal Detection", version="0.1.0", lifespan=lifespan)


# ── API routes ───────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "utc": datetime.now(UTC).isoformat()}


@app.get("/api/events", response_model=list[AdverseEvent])
async def get_events(limit: int = Query(50, ge=1, le=500)) -> list[AdverseEvent]:
    return _events[-limit:]


@app.get("/api/signals", response_model=list[SignalAlert])
async def get_signals() -> list[SignalAlert]:
    return _signals


@app.get("/api/stats", response_model=DashboardStats)
async def get_stats() -> DashboardStats:
    today = datetime.now(UTC).date()
    today_events = [
        e
        for e in _events
        if (
            e.reported_at.date()
            if e.reported_at.tzinfo
            else e.reported_at.replace(tzinfo=UTC).date()
        )
        == today
    ]

    severity_counts: dict[str, int] = Counter(e.severity.value for e in _events)
    arm_counts: dict[str, int] = Counter(e.arm.value for e in _events)

    symptom_counter: dict[str, int] = defaultdict(int)
    for e in _events:
        symptom_counter[e.symptom_label] += 1
    top_symptoms = [
        {"symptom_label": label, "count": cnt}
        for label, cnt in sorted(symptom_counter.items(), key=lambda x: -x[1])[:8]
    ]

    return DashboardStats(
        total_events_today=len(today_events),
        active_signals=len(_signals),
        trials_monitored=len({e.trial_id for e in _events}),
        sites_reporting=len({e.site_id for e in _events}),
        recent_events=_events[-20:],
        active_signal_alerts=_signals,
        events_by_severity=dict(severity_counts),
        events_by_arm=dict(arm_counts),
        top_symptoms=top_symptoms,
    )


@app.post("/api/detect")
async def run_detection() -> dict:
    """Re-run signal detection over the current in-memory event store."""
    global _signals
    _signals = detect_signals(
        _events,
        window_minutes=settings.signal_window_minutes,
        rate_threshold=settings.signal_rate_threshold,
        min_events=settings.signal_min_events,
    )
    insert_signals(_signals)
    return {"signals_detected": len(_signals)}


# ── static frontend ──────────────────────────────────────────────────────────

_FRONTEND = Path(__file__).parent.parent / "frontend" / "static"
app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (_FRONTEND / "index.html").read_text()
    return HTMLResponse(html)
