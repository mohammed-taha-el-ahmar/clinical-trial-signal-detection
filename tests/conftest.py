"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Ensure tests never hit real Azure services."""
    monkeypatch.setenv("EVENTHUB_CONNECTION_STRING", "")
    monkeypatch.setenv("SYNAPSE_SERVER", "")
    # Disable Synapse at module level so all read/write functions become no-ops
    monkeypatch.setattr("agent.synapse_client._SYNAPSE_AVAILABLE", False)
    monkeypatch.setattr("agent.synapse_client.fetch_events", lambda *a, **kw: [])
    monkeypatch.setattr("agent.synapse_client.fetch_signals", lambda *a, **kw: [])
