# test_mission_api.py
"""API tests for /api/missions (Slice 2)."""

import os
import tempfile
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Boot the app pointing at a temp data dir + a real session token; client sends the token in every request."""
    monkeypatch.setenv("AGENTCHATTR_DATA_DIR", str(tmp_path))
    # Reload app freshly so it picks up the env-overridden data dir
    import importlib, sys
    for m in ["app", "missions", "jobs", "store"]:
        sys.modules.pop(m, None)
    import app as app_module
    real_token = "test-session-token-abc"
    app_module.configure({"server": {"data_dir": str(tmp_path)}}, session_token=real_token)
    tc = TestClient(app_module.app)
    tc.headers.update({"x-session-token": real_token})
    return tc, app_module


def _briefing_payload(**overrides):
    base = {
        "title": "Replace JWT cookie flow",
        "objective": "Refresh-token rotation + compat tests.",
        "crew": [{"agent": "codex", "role": "builder"},
                 {"agent": "claude", "role": "researcher"}],
        "reviewer": "gemini",
        "deliverables": [
            {"text": "migration plan", "required": True},
            {"text": "diff", "required": True},
            {"text": "deploy runbook", "required": False},
        ],
        "eta_minutes": 30,
        "hop_budget": 16,
        "auto_pause_blockers": 2,
    }
    base.update(overrides)
    return base


def test_create_mission_returns_201(client):
    c, _ = client
    r = c.post("/api/missions", json=_briefing_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("mission-")
    assert body["status"] == "active"  # transition out of briefing on create
    assert body["title"] == "Replace JWT cookie flow"


def test_create_mission_requires_title(client):
    c, _ = client
    r = c.post("/api/missions", json=_briefing_payload(title=""))
    assert r.status_code == 400


def test_create_mission_requires_crew(client):
    c, _ = client
    r = c.post("/api/missions", json=_briefing_payload(crew=[]))
    assert r.status_code == 400
