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


def test_list_missions_empty(client):
    c, _ = client
    r = c.get("/api/missions")
    assert r.status_code == 200
    assert r.json() == []


def test_list_missions_after_create(client):
    c, _ = client
    c.post("/api/missions", json=_briefing_payload(title="A"))
    c.post("/api/missions", json=_briefing_payload(title="B"))
    r = c.get("/api/missions")
    assert r.status_code == 200
    titles = [m["title"] for m in r.json()]
    assert titles == ["A", "B"]


def test_get_mission_by_id(client):
    c, _ = client
    created = c.post("/api/missions", json=_briefing_payload(title="solo")).json()
    r = c.get(f"/api/missions/{created['id']}")
    assert r.status_code == 200
    assert r.json()["title"] == "solo"


def test_get_mission_404_for_unknown(client):
    c, _ = client
    r = c.get("/api/missions/mission-999")
    assert r.status_code == 404


def test_mission_create_broadcasts(client):
    c, app_module = client
    events = []
    app_module.missions.on_change(lambda action, m: events.append((action, m["id"])))
    body = c.post("/api/missions", json=_briefing_payload(title="bcast")).json()
    assert ("create", body["id"]) in events
    assert ("update", body["id"]) in events  # status transition to active


def test_launch_posts_kickoff_message(client):
    c, app_module = client
    body = c.post("/api/missions", json=_briefing_payload(title="Kickoff")).json()
    channel = body["transcript_channel_id"]
    msgs = c.get(f"/api/messages?channel={channel}").json()
    assert isinstance(msgs, list) and len(msgs) >= 1
    kickoff = msgs[0]
    assert "@codex" in kickoff["text"]
    assert "@claude" in kickoff["text"]
    assert "Kickoff" in kickoff["text"] or "Replace JWT" in kickoff["text"]
    assert kickoff.get("sender") in ("system", "human", "user")


def test_intervene_freeze(client):
    c, _ = client
    body = c.post("/api/missions", json=_briefing_payload(title="iv")).json()
    mid = body["id"]
    channel = body["transcript_channel_id"]
    r = c.post(f"/api/missions/{mid}/intervene",
               json={"action": "freeze", "agent": "codex"})
    assert r.status_code == 200
    msgs = c.get(f"/api/messages?channel={channel}").json()
    texts = [m["text"] for m in msgs]
    assert any("/freeze @codex" in t for t in texts)


def test_intervene_redirect(client):
    c, _ = client
    body = c.post("/api/missions", json=_briefing_payload(title="iv2")).json()
    mid = body["id"]
    channel = body["transcript_channel_id"]
    r = c.post(f"/api/missions/{mid}/intervene",
               json={"action": "redirect", "agent": "codex",
                     "text": "stop and write audit first"})
    assert r.status_code == 200
    msgs = c.get(f"/api/messages?channel={channel}").json()
    redirect_msg = [m for m in msgs if "@codex" in m["text"]
                    and "audit first" in m["text"]]
    assert len(redirect_msg) >= 1


def test_intervene_stop(client):
    c, _ = client
    body = c.post("/api/missions", json=_briefing_payload(title="iv3")).json()
    mid = body["id"]
    channel = body["transcript_channel_id"]
    r = c.post(f"/api/missions/{mid}/intervene",
               json={"action": "stop", "agent": "codex"})
    assert r.status_code == 200
    msgs = c.get(f"/api/messages?channel={channel}").json()
    has_freeze = any("/freeze @codex" in m["text"] for m in msgs)
    has_stop_note = any("stopped" in m["text"].lower() and "codex" in m["text"].lower()
                        for m in msgs)
    assert has_freeze, "stop must include /freeze command"
    assert has_stop_note, "stop must include a human-readable note"


def test_intervene_404_for_unknown_mission(client):
    c, _ = client
    r = c.post("/api/missions/mission-999/intervene",
               json={"action": "freeze", "agent": "codex"})
    assert r.status_code == 404


def test_intervene_400_for_bad_action(client):
    c, _ = client
    body = c.post("/api/missions", json=_briefing_payload(title="iv4")).json()
    r = c.post(f"/api/missions/{body['id']}/intervene",
               json={"action": "explode", "agent": "codex"})
    assert r.status_code == 400


def test_intervene_records_decision(client):
    c, _ = client
    body = c.post("/api/missions", json=_briefing_payload(title="dec")).json()
    mid = body["id"]
    c.post(f"/api/missions/{mid}/intervene",
           json={"action": "freeze", "agent": "codex"})
    c.post(f"/api/missions/{mid}/intervene",
           json={"action": "redirect", "agent": "codex", "text": "do audit"})
    mission = c.get(f"/api/missions/{mid}").json()
    decisions = mission.get("decisions") or []
    types = [d["type"] for d in decisions]
    assert "intervention" in types
    intv = [d for d in decisions if d["type"] == "intervention"]
    assert len(intv) >= 2
    assert all(d.get("agent") == "codex" for d in intv)


def test_complete_mission(client):
    c, _ = client
    body = c.post("/api/missions", json=_briefing_payload(title="done")).json()
    mid = body["id"]
    r = c.post(f"/api/missions/{mid}/complete")
    assert r.status_code == 200
    m = r.json()
    assert m["status"] == "complete"
    assert m.get("completed_at") is not None


def test_complete_404_for_unknown(client):
    c, _ = client
    r = c.post("/api/missions/mission-999/complete")
    assert r.status_code == 404
