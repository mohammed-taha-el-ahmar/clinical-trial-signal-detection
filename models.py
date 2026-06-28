"""Domain models for adverse event reporting."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    LIFE_THREATENING = "life_threatening"


class TrialArm(StrEnum):
    TREATMENT = "treatment"
    PLACEBO = "placebo"
    CONTROL = "control"


class AdverseEvent(BaseModel):
    """A single adverse event report emitted by a trial site."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trial_id: str
    site_id: str
    patient_id: str
    arm: TrialArm
    symptom_code: str  # MedDRA-style code e.g. "10019211" (headache)
    symptom_label: str
    severity: Severity
    reported_at: datetime = Field(default_factory=datetime.utcnow)
    age_group: str  # "18-30", "31-50", "51-65", "65+"
    is_serious: bool = False  # SAE flag


class SignalAlert(BaseModel):
    """A safety signal surfaced by Stream Analytics or the local detector."""

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trial_id: str
    arm: TrialArm
    symptom_code: str
    symptom_label: str
    window_start: datetime
    window_end: datetime
    event_count: int
    incidence_rate: float  # events / total arm population in window
    severity_distribution: dict[str, int]
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    confidence: str  # "low" | "medium" | "high"


class DashboardStats(BaseModel):
    """Aggregated stats returned by the API to the frontend."""

    total_events_today: int
    active_signals: int
    trials_monitored: int
    sites_reporting: int
    recent_events: list[AdverseEvent]
    active_signal_alerts: list[SignalAlert]
    events_by_severity: dict[str, int]
    events_by_arm: dict[str, int]
    top_symptoms: list[dict]  # [{symptom_label, count}]
