"""
Smoke tests for the FastAPI application.

These tests validate that the API starts, serves all endpoints correctly,
and the signal detection pipeline works end-to-end without Azure dependencies.

Run with:  uv run pytest tests/test_api_smoke.py -v
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import api.main as api_module
from agent.detector import detect_signals
from api.main import app
from config import settings
from simulator.runner import generate_fixtures, load_fixtures


@pytest.fixture(autouse=True, scope="module")
def _bootstrap_app_state():
    """Pre-populate the in-memory store (lifespan doesn't fire in ASGI transport tests)."""
    generate_fixtures(n=200)
    events = load_fixtures()
    signals = detect_signals(
        events,
        window_minutes=settings.signal_window_minutes,
        rate_threshold=settings.signal_rate_threshold,
        min_events=settings.signal_min_events,
    )
    api_module._events.clear()
    api_module._events.extend(events)
    api_module._signals.clear()
    api_module._signals.extend(signals)


@pytest.fixture
async def client():
    """Async test client against the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Health & liveness ─────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_returns_ok(self, client: AsyncClient):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "utc" in data

    async def test_health_has_iso_timestamp(self, client: AsyncClient):
        resp = await client.get("/api/health")
        data = resp.json()
        # ISO format check (contains T and timezone info)
        assert "T" in data["utc"]


# ── Dashboard index ───────────────────────────────────────────────────────────


class TestDashboardIndex:
    async def test_root_serves_html(self, client: AsyncClient):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "PharmaSight" in resp.text

    async def test_static_css_served(self, client: AsyncClient):
        resp = await client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    async def test_static_js_served(self, client: AsyncClient):
        resp = await client.get("/static/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]


# ── Events endpoint ───────────────────────────────────────────────────────────


class TestEventsEndpoint:
    async def test_events_returns_list(self, client: AsyncClient):
        resp = await client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    async def test_events_limit_param(self, client: AsyncClient):
        resp = await client.get("/api/events?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 5

    async def test_events_have_required_fields(self, client: AsyncClient):
        resp = await client.get("/api/events?limit=1")
        event = resp.json()[0]
        required = {
            "event_id",
            "trial_id",
            "site_id",
            "patient_id",
            "arm",
            "symptom_code",
            "symptom_label",
            "severity",
            "reported_at",
            "age_group",
        }
        assert required.issubset(event.keys())

    async def test_events_invalid_limit_rejected(self, client: AsyncClient):
        resp = await client.get("/api/events?limit=0")
        assert resp.status_code == 422  # validation error


# ── Signals endpoint ──────────────────────────────────────────────────────────


class TestSignalsEndpoint:
    async def test_signals_returns_list(self, client: AsyncClient):
        resp = await client.get("/api/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_signal_fields_populated(self, client: AsyncClient):
        resp = await client.get("/api/signals")
        data = resp.json()
        if data:  # signals may or may not exist depending on fixtures
            sig = data[0]
            required = {
                "signal_id",
                "trial_id",
                "arm",
                "symptom_code",
                "symptom_label",
                "incidence_rate",
                "confidence",
                "event_count",
                "window_start",
                "window_end",
            }
            assert required.issubset(sig.keys())
            assert sig["confidence"] in ("low", "medium", "high")


# ── Stats endpoint ────────────────────────────────────────────────────────────


class TestStatsEndpoint:
    async def test_stats_returns_all_fields(self, client: AsyncClient):
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "total_events_today",
            "active_signals",
            "trials_monitored",
            "sites_reporting",
            "recent_events",
            "active_signal_alerts",
            "events_by_severity",
            "events_by_arm",
            "top_symptoms",
        }
        assert expected_keys.issubset(data.keys())

    async def test_stats_trials_monitored_positive(self, client: AsyncClient):
        resp = await client.get("/api/stats")
        data = resp.json()
        assert data["trials_monitored"] > 0

    async def test_stats_severity_distribution_valid(self, client: AsyncClient):
        resp = await client.get("/api/stats")
        data = resp.json()
        valid_keys = {"mild", "moderate", "severe", "life_threatening"}
        assert all(k in valid_keys for k in data["events_by_severity"])

    async def test_stats_arm_distribution_valid(self, client: AsyncClient):
        resp = await client.get("/api/stats")
        data = resp.json()
        valid_keys = {"treatment", "placebo", "control"}
        assert all(k in valid_keys for k in data["events_by_arm"])

    async def test_stats_top_symptoms_structured(self, client: AsyncClient):
        resp = await client.get("/api/stats")
        data = resp.json()
        for symptom in data["top_symptoms"]:
            assert "symptom_label" in symptom
            assert "count" in symptom
            assert symptom["count"] > 0


# ── Detection endpoint ────────────────────────────────────────────────────────


class TestDetectEndpoint:
    async def test_detect_returns_count(self, client: AsyncClient):
        resp = await client.post("/api/detect")
        assert resp.status_code == 200
        data = resp.json()
        assert "signals_detected" in data
        assert isinstance(data["signals_detected"], int)

    async def test_detect_idempotent(self, client: AsyncClient):
        resp1 = await client.post("/api/detect")
        resp2 = await client.post("/api/detect")
        assert resp1.json()["signals_detected"] == resp2.json()["signals_detected"]


# ── End-to-end smoke ──────────────────────────────────────────────────────────


class TestEndToEndSmoke:
    async def test_full_pipeline_flow(self, client: AsyncClient):
        """Validate the full local pipeline: fixtures → detect → signals → stats."""
        # 1. Events are loaded
        events_resp = await client.get("/api/events?limit=10")
        assert events_resp.status_code == 200
        assert len(events_resp.json()) > 0

        # 2. Run detection
        detect_resp = await client.post("/api/detect")
        assert detect_resp.status_code == 200
        signal_count = detect_resp.json()["signals_detected"]

        # 3. Signals endpoint matches detection
        signals_resp = await client.get("/api/signals")
        assert signals_resp.status_code == 200
        assert len(signals_resp.json()) == signal_count

        # 4. Stats includes signals
        stats_resp = await client.get("/api/stats")
        assert stats_resp.status_code == 200
        assert stats_resp.json()["active_signals"] == signal_count

    async def test_404_for_unknown_route(self, client: AsyncClient):
        resp = await client.get("/api/nonexistent")
        assert resp.status_code == 404
