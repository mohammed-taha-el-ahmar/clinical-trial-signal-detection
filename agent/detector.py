"""
Local signal detector — mirrors the Stream Analytics tumbling-window query.

Used in:
  - Unit tests (no Azure dependency)
  - API /detect endpoint (processes fixture events in-process)

The Stream Analytics query (stream_analytics/signal_query.sql) is the
production equivalent; this implementation must stay in sync with it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from models import AdverseEvent, SignalAlert

log = logging.getLogger(__name__)


def _confidence(rate: float, count: int) -> str:
    if rate >= 0.15 and count >= 10:
        return "high"
    if rate >= 0.08 and count >= 5:
        return "medium"
    return "low"


def detect_signals(
    events: list[AdverseEvent],
    window_minutes: int = 10,
    rate_threshold: float = 0.05,
    min_events: int = 3,
    reference_time: datetime | None = None,
) -> list[SignalAlert]:
    """
    Tumbling-window signal detection over a list of AdverseEvent objects.

    Groups events into non-overlapping windows of `window_minutes` and flags
    any (trial_id, arm, symptom_code) combination whose incidence rate
    exceeds `rate_threshold`.

    Args:
        events:           Raw adverse event records.
        window_minutes:   Tumbling window size in minutes.
        rate_threshold:   Incidence rate (0–1) that triggers a signal.
        min_events:       Minimum events in a window before evaluating.
        reference_time:   Anchor time for windows (defaults to utcnow).

    Returns:
        List of SignalAlert objects — one per flagged (trial, arm, symptom, window).
    """
    if not events:
        return []

    ref = reference_time or datetime.now(UTC)
    window_delta = timedelta(minutes=window_minutes)

    # Bucket events into tumbling windows keyed by (trial, arm, symptom, window_start)
    buckets: dict[tuple, list[AdverseEvent]] = defaultdict(list)
    for e in events:
        reported = e.reported_at
        if reported.tzinfo is None:
            reported = reported.replace(tzinfo=UTC)
        window_idx = int((ref - reported).total_seconds() // (window_minutes * 60))
        window_start = ref - timedelta(minutes=window_minutes * (window_idx + 1))
        key = (e.trial_id, e.arm, e.symptom_code, window_start)
        buckets[key].append(e)

    # Count total arm population per (trial, arm, window) for denominator
    arm_totals: dict[tuple, int] = defaultdict(int)
    for (trial, arm, _symptom, window_start), evts in buckets.items():
        arm_totals[(trial, arm, window_start)] += len(evts)

    signals: list[SignalAlert] = []
    for (trial, arm, symptom_code, window_start), evts in buckets.items():
        if len(evts) < min_events:
            continue

        denom = arm_totals[(trial, arm, window_start)]
        rate = len(evts) / denom if denom else 0.0

        if rate < rate_threshold:
            continue

        severity_dist = defaultdict(int)
        for e in evts:
            severity_dist[e.severity.value] += 1

        signal = SignalAlert(
            trial_id=trial,
            arm=arm,
            symptom_code=symptom_code,
            symptom_label=evts[0].symptom_label,
            window_start=window_start,
            window_end=window_start + window_delta,
            event_count=len(evts),
            incidence_rate=round(rate, 4),
            severity_distribution=dict(severity_dist),
            confidence=_confidence(rate, len(evts)),
        )
        signals.append(signal)
        log.info(
            "Signal detected: trial=%s arm=%s symptom=%s rate=%.1f%% count=%d [%s]",
            trial,
            arm.value,
            evts[0].symptom_label,
            rate * 100,
            len(evts),
            signal.confidence,
        )

    return sorted(signals, key=lambda s: s.incidence_rate, reverse=True)
