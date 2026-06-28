"""
Integration tests for the simulator and detector working together.

Validates that the synthetic event generation produces events that
the detector can actually process and surface signals from.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent.detector import detect_signals
from models import AdverseEvent, Severity, TrialArm
from simulator.runner import _make_event, generate_fixtures, load_fixtures


class TestSimulatorDetectorIntegration:
    """Test the simulator → detector pipeline end-to-end."""

    def test_fixture_events_produce_signals(self):
        """Fixtures with signal injection should produce at least one signal."""
        generate_fixtures(n=200)
        events = load_fixtures()
        # Set all events to be within the same window
        now = datetime.now(UTC)
        for e in events:
            e.reported_at = now - timedelta(minutes=2)
        signals = detect_signals(events, rate_threshold=0.05, min_events=3)
        # With 200 events and signal injection, should get at least one signal
        assert len(signals) >= 1

    def test_signal_injection_creates_clusters(self):
        """Events generated with inject_signal=True cluster in top 3 symptoms."""
        injected = [_make_event(inject_signal=True) for _ in range(50)]
        top_3_codes = {"10019211", "10028813", "10015218"}
        assert all(e.symptom_code in top_3_codes for e in injected)

    def test_no_signal_on_uniform_distribution(self):
        """Uniformly distributed events should not trigger signals at low counts."""
        now = datetime.now(UTC)
        events = []
        # 1 event per each of 10 symptoms — no cluster
        for i, (code, label) in enumerate(
            [
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
        ):
            events.append(
                AdverseEvent(
                    trial_id="TRIAL-001",
                    site_id="SITE-001",
                    patient_id=f"P-{i:06d}",
                    arm=TrialArm.TREATMENT,
                    symptom_code=code,
                    symptom_label=label,
                    severity=Severity.MILD,
                    age_group="31-50",
                    reported_at=now - timedelta(minutes=3),
                )
            )
        signals = detect_signals(events, rate_threshold=0.05, min_events=3)
        assert len(signals) == 0

    def test_multi_window_detection(self):
        """Events in different windows produce independent signals."""
        now = datetime.now(UTC)
        # Window 1: 5 headaches within last 5 minutes
        w1_events = [
            AdverseEvent(
                trial_id="TRIAL-001",
                site_id="SITE-001",
                patient_id=f"P-{i:06d}",
                arm=TrialArm.TREATMENT,
                symptom_code="10019211",
                symptom_label="Headache",
                severity=Severity.MILD,
                age_group="31-50",
                reported_at=now - timedelta(minutes=3),
            )
            for i in range(5)
        ]
        # Window 2: 5 headaches 15 minutes ago (different window)
        w2_events = [
            AdverseEvent(
                trial_id="TRIAL-001",
                site_id="SITE-001",
                patient_id=f"P-{i + 10:06d}",
                arm=TrialArm.TREATMENT,
                symptom_code="10019211",
                symptom_label="Headache",
                severity=Severity.MILD,
                age_group="31-50",
                reported_at=now - timedelta(minutes=15),
            )
            for i in range(5)
        ]
        signals = detect_signals(
            w1_events + w2_events,
            window_minutes=10,
            rate_threshold=0.05,
            min_events=3,
            reference_time=now,
        )
        # Should detect signal in at least one window
        assert len(signals) >= 1

    def test_serious_events_flagged_in_severity_distribution(self):
        """Severity distribution in signals includes serious event types."""
        now = datetime.now(UTC)
        events = [
            AdverseEvent(
                trial_id="TRIAL-001",
                site_id="SITE-001",
                patient_id=f"P-{i:06d}",
                arm=TrialArm.TREATMENT,
                symptom_code="10019211",
                symptom_label="Headache",
                severity=Severity.LIFE_THREATENING if i < 2 else Severity.SEVERE,
                age_group="31-50",
                reported_at=now - timedelta(minutes=3),
                is_serious=True,
            )
            for i in range(5)
        ]
        signals = detect_signals(events, rate_threshold=0.05, min_events=3)
        assert len(signals) >= 1
        dist = signals[0].severity_distribution
        assert "life_threatening" in dist or "severe" in dist

    def test_generated_events_have_valid_structure(self):
        """Every generated event passes Pydantic validation."""
        events = [_make_event(inject_signal=False) for _ in range(100)]
        assert all(isinstance(e, AdverseEvent) for e in events)
        assert all(e.event_id for e in events)
        assert all(e.trial_id.startswith("TRIAL-") for e in events)
        assert all(e.site_id.startswith("SITE-") for e in events)
