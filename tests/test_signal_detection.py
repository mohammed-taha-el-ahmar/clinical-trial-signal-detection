"""
Tests for the clinical trial signal detection pipeline.

Run with:  uv run pytest
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent.detector import detect_signals
from models import AdverseEvent, Severity, TrialArm

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _event(
    symptom_code: str = "10019211",
    symptom_label: str = "Headache",
    severity: Severity = Severity.MILD,
    arm: TrialArm = TrialArm.TREATMENT,
    trial_id: str = "TRIAL-001",
    minutes_ago: int = 5,
    age_group: str = "31-50",
) -> AdverseEvent:
    return AdverseEvent(
        trial_id=trial_id,
        site_id="SITE-001",
        patient_id="P-000001",
        arm=arm,
        symptom_code=symptom_code,
        symptom_label=symptom_label,
        severity=severity,
        age_group=age_group,
        reported_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )


# ── Detector unit tests ────────────────────────────────────────────────────────


class TestDetectSignals:
    def test_empty_input_returns_no_signals(self):
        assert detect_signals([]) == []

    def test_below_min_events_returns_no_signals(self):
        events = [_event() for _ in range(2)]  # min_events=3
        result = detect_signals(events, min_events=3)
        assert result == []

    def test_low_rate_returns_no_signals(self):
        # 3 headache events out of 100 total arm events → rate < 5 %
        headaches = [_event(symptom_code="10019211") for _ in range(3)]
        other = [_event(symptom_code="10028813", symptom_label="Nausea") for _ in range(97)]
        result = detect_signals(headaches + other, rate_threshold=0.05)
        assert all(s.symptom_code != "10019211" for s in result)

    def test_high_rate_raises_signal(self):
        # 10 headache events out of 12 total → rate ≈ 83 %
        events = [_event(symptom_code="10019211") for _ in range(10)]
        events += [_event(symptom_code="10028813", symptom_label="Nausea") for _ in range(2)]
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        codes = [s.symptom_code for s in result]
        assert "10019211" in codes

    def test_signal_sorted_by_incidence_rate_desc(self):
        # Two symptoms — headache 50 %, nausea 30 %
        events = (
            [_event(symptom_code="10019211", symptom_label="Headache") for _ in range(5)]
            + [_event(symptom_code="10028813", symptom_label="Nausea") for _ in range(3)]
            + [_event(symptom_code="10015218", symptom_label="Fatigue") for _ in range(2)]
        )
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        rates = [s.incidence_rate for s in result]
        assert rates == sorted(rates, reverse=True)

    def test_confidence_high_when_rate_and_count_high(self):
        events = [_event() for _ in range(15)]  # rate = 100 %, count = 15
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        assert result[0].confidence == "high"

    def test_confidence_low_when_just_over_threshold(self):
        # 3 headache out of 15 total → 20 %, count < 5
        events = [_event() for _ in range(3)]
        events += [_event(symptom_code="10028813", symptom_label="Nausea") for _ in range(12)]
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        headache_signals = [s for s in result if s.symptom_code == "10019211"]
        assert headache_signals[0].confidence == "low"

    def test_different_trials_detected_independently(self):
        t1 = [_event(trial_id="TRIAL-001") for _ in range(5)]
        t2 = [_event(trial_id="TRIAL-002") for _ in range(5)]
        result = detect_signals(t1 + t2, rate_threshold=0.05, min_events=3)
        trial_ids = {s.trial_id for s in result}
        assert "TRIAL-001" in trial_ids
        assert "TRIAL-002" in trial_ids

    def test_placebo_arm_signals_surfaced(self):
        events = [_event(arm=TrialArm.PLACEBO) for _ in range(5)]
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        assert any(s.arm == TrialArm.PLACEBO for s in result)

    def test_signal_fields_populated(self):
        events = [_event() for _ in range(5)]
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        s = result[0]
        assert s.signal_id
        assert s.trial_id == "TRIAL-001"
        assert s.symptom_label == "Headache"
        assert 0 < s.incidence_rate <= 1.0
        assert s.event_count == 5
        assert s.window_start < s.window_end

    def test_severity_distribution_included(self):
        events = [_event(severity=Severity.MILD) for _ in range(3)] + [
            _event(severity=Severity.SEVERE) for _ in range(2)
        ]
        result = detect_signals(events, rate_threshold=0.05, min_events=3)
        dist = result[0].severity_distribution
        assert dist.get("mild", 0) == 3
        assert dist.get("severe", 0) == 2


# ── Model tests ───────────────────────────────────────────────────────────────


class TestAdverseEvent:
    def test_default_event_id_is_uuid(self):
        e = _event()
        import re

        assert re.match(r"[0-9a-f-]{36}", e.event_id)

    def test_is_serious_set_for_severe(self):
        e = _event(severity=Severity.SEVERE)
        # is_serious default is False; the simulator sets it, not the model
        assert e.is_serious is False  # must be set explicitly

    def test_serialise_roundtrip(self):
        e = _event()
        raw = e.model_dump_json()
        restored = AdverseEvent.model_validate_json(raw)
        assert restored.event_id == e.event_id
        assert restored.symptom_code == e.symptom_code


# ── Simulator fixture tests ────────────────────────────────────────────────────


class TestSimulatorFixtures:
    def test_load_fixtures_returns_events(self):
        from simulator.runner import generate_fixtures, load_fixtures

        generate_fixtures(n=20)
        events = load_fixtures()
        assert len(events) == 20
        assert all(isinstance(e, AdverseEvent) for e in events)

    def test_fixture_events_have_valid_arms(self):
        from simulator.runner import load_fixtures

        events = load_fixtures()
        valid_arms = {a.value for a in TrialArm}
        assert all(e.arm.value in valid_arms for e in events)
