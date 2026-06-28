"""
Adverse event simulator.

Two modes:
  - fixture  : replays static JSON events from simulator/fixtures.json (CI/tests)
  - live     : generates synthetic events and pushes to Azure Event Hub (demo)

Usage:
    uv run python -m simulator.runner --mode fixture   # CI
    uv run python -m simulator.runner --mode live      # demo
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, datetime
from pathlib import Path

from faker import Faker

from config import settings
from models import AdverseEvent, Severity, TrialArm

log = logging.getLogger(__name__)
fake = Faker()

# ── deterministic seed data ──────────────────────────────────────────────────

TRIALS = ["TRIAL-001", "TRIAL-002", "TRIAL-003"]
SITES = [f"SITE-{i:03d}" for i in range(1, 9)]

SYMPTOMS = [
    ("10019211", "Headache"),
    ("10028813", "Nausea"),
    ("10015218", "Fatigue"),
    ("10037087", "Rash"),
    ("10013395", "Dizziness"),
    ("10047700", "Vomiting"),
    ("10003553", "Back pain"),
    ("10006482", "Chest pain"),
    ("10021097", "Hypertension"),
    ("10000486", "Abdominal pain"),
]

AGE_GROUPS = ["18-30", "31-50", "51-65", "65+"]
SEVERITY_WEIGHTS = [0.45, 0.35, 0.15, 0.05]  # mild→life-threatening


def _make_event(inject_signal: bool = False) -> AdverseEvent:
    """Generate one synthetic adverse event."""
    symptom = random.choice(SYMPTOMS if not inject_signal else SYMPTOMS[:3])
    severity = random.choices(list(Severity), weights=SEVERITY_WEIGHTS)[0]
    arm = random.choices(
        list(TrialArm),
        weights=[0.5, 0.3, 0.2],  # skew toward treatment arm
    )[0]
    return AdverseEvent(
        trial_id=random.choice(TRIALS),
        site_id=random.choice(SITES),
        patient_id=f"P-{fake.numerify('######')}",
        arm=arm,
        symptom_code=symptom[0],
        symptom_label=symptom[1],
        severity=severity,
        age_group=random.choice(AGE_GROUPS),
        is_serious=severity in (Severity.SEVERE, Severity.LIFE_THREATENING),
    )


# ── fixture mode ─────────────────────────────────────────────────────────────

FIXTURE_PATH = Path(__file__).parent / "fixtures.json"


def generate_fixtures(n: int = 200) -> None:
    """Write n synthetic events to fixtures.json for CI use."""
    events = []
    for i in range(n):
        inject = i % 12 == 0  # inject a signal-worthy cluster every 12 events
        e = _make_event(inject_signal=inject)
        events.append(e.model_dump(mode="json"))
    FIXTURE_PATH.write_text(json.dumps(events, indent=2, default=str))
    log.info("Wrote %d fixture events to %s", n, FIXTURE_PATH)


def load_fixtures() -> list[AdverseEvent]:
    if not FIXTURE_PATH.exists():
        generate_fixtures()
    raw = json.loads(FIXTURE_PATH.read_text())
    return [AdverseEvent(**r) for r in raw]


# ── live mode (Event Hub) ────────────────────────────────────────────────────


async def _send_to_eventhub(events: list[AdverseEvent]) -> None:
    """Push a batch of events to Azure Event Hub."""
    from azure.eventhub import EventData
    from azure.eventhub.aio import EventHubProducerClient

    async with EventHubProducerClient.from_connection_string(
        settings.eventhub_connection_string,
        eventhub_name=settings.eventhub_name,
    ) as producer:
        batch = await producer.create_batch()
        for event in events:
            payload = event.model_dump_json().encode()
            batch.add(EventData(payload))
        await producer.send_batch(batch)


async def run_live(duration_seconds: int | None = None) -> None:
    """Continuously generate and publish events. Runs until cancelled or duration elapses."""
    log.info(
        "Live simulator starting: %.1f events/s → Event Hub '%s'",
        settings.simulator_events_per_second,
        settings.eventhub_name,
    )
    interval = 1.0 / settings.simulator_events_per_second
    elapsed = 0.0
    total = 0

    while True:
        inject = random.random() < settings.simulator_signal_injection_rate
        event = _make_event(inject_signal=inject)
        event.reported_at = datetime.now(UTC)

        try:
            await _send_to_eventhub([event])
            total += 1
            log.debug(
                "Sent event %s [%s / %s]", event.event_id, event.symptom_label, event.severity
            )
        except Exception as exc:
            log.warning("Failed to send event: %s", exc)

        await asyncio.sleep(interval)
        elapsed += interval
        if duration_seconds and elapsed >= duration_seconds:
            break

    log.info("Simulator finished. Total events sent: %d", total)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    parser.add_argument("--duration", type=int, default=None, help="seconds (live mode)")
    args = parser.parse_args()

    if args.mode == "fixture":
        generate_fixtures()
    else:
        asyncio.run(run_live(duration_seconds=args.duration))
