# test_mission_store.py
"""Tests for MissionStore (Slice 2)."""

import tempfile
from pathlib import Path
import pytest
from missions import MissionStore, Mission


def test_create_mission_assigns_id():
    with tempfile.TemporaryDirectory() as tmp:
        store = MissionStore(str(Path(tmp) / "missions.json"))
        m = store.create(
            title="Replace JWT",
            objective="Rotation + audit",
            crew=[{"agent": "codex", "role": "builder"}],
            reviewer="gemini",
            deliverables=[{"text": "migration plan", "required": True}],
            eta_minutes=30,
            hop_budget=16,
            auto_pause_blockers=2,
        )
        assert m["id"] == "mission-1"
        assert m["status"] == "briefing"
        assert m["title"] == "Replace JWT"
        assert m["transcript_channel_id"] == "mission-1"


def test_list_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        store = MissionStore(str(Path(tmp) / "missions.json"))
        a = store.create(title="A", objective="", crew=[], reviewer="",
                         deliverables=[], eta_minutes=0, hop_budget=0,
                         auto_pause_blockers=0)
        b = store.create(title="B", objective="", crew=[], reviewer="",
                         deliverables=[], eta_minutes=0, hop_budget=0,
                         auto_pause_blockers=0)
        assert {m["id"] for m in store.list_all()} == {a["id"], b["id"]}
        assert store.get(a["id"])["title"] == "A"
        assert store.get("nope") is None


def test_persistence_across_instances():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "missions.json")
        store1 = MissionStore(path)
        m = store1.create(title="persist me", objective="", crew=[],
                          reviewer="", deliverables=[], eta_minutes=0,
                          hop_budget=0, auto_pause_blockers=0)
        store2 = MissionStore(path)
        assert store2.get(m["id"])["title"] == "persist me"
        m2 = store2.create(title="next", objective="", crew=[],
                           reviewer="", deliverables=[], eta_minutes=0,
                           hop_budget=0, auto_pause_blockers=0)
        assert m2["id"] == "mission-2"


def test_on_change_callback_fires_on_create():
    with tempfile.TemporaryDirectory() as tmp:
        store = MissionStore(str(Path(tmp) / "missions.json"))
        events = []
        store.on_change(lambda action, data: events.append((action, data["id"])))
        m = store.create(title="x", objective="", crew=[], reviewer="",
                         deliverables=[], eta_minutes=0, hop_budget=0,
                         auto_pause_blockers=0)
        assert events == [("create", m["id"])]


def test_app_imports_mission_store():
    """app.py must wire MissionStore (import + module-level + configure init)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
    assert "from missions import MissionStore" in src, \
        "app.py missing `from missions import MissionStore`"
    assert "missions: MissionStore | None" in src, \
        "app.py missing module-level `missions: MissionStore | None`"
    assert "MissionStore(str(missions_path))" in src, \
        "app.py missing MissionStore init in configure()"


def test_append_decision():
    import tempfile
    from pathlib import Path
    from missions import MissionStore
    with tempfile.TemporaryDirectory() as tmp:
        store = MissionStore(str(Path(tmp) / "missions.json"))
        m = store.create(title="x", objective="", crew=[{"agent": "codex"}],
                         reviewer="", deliverables=[], eta_minutes=0,
                         hop_budget=0, auto_pause_blockers=0)
        d = store.append_decision(m["id"], type="intervention",
                                  agent="codex", body="human · froze codex")
        assert d is not None
        updated = store.get(m["id"])
        assert len(updated["decisions"]) == 1
        assert updated["decisions"][0]["type"] == "intervention"
        assert updated["decisions"][0]["agent"] == "codex"
        assert "froze" in updated["decisions"][0]["body"]
        assert isinstance(updated["decisions"][0].get("ts"), (int, float))


def test_set_deliverable_met():
    import tempfile
    from pathlib import Path
    from missions import MissionStore
    with tempfile.TemporaryDirectory() as tmp:
        store = MissionStore(str(Path(tmp) / "missions.json"))
        m = store.create(title="x", objective="", crew=[],
                         reviewer="", eta_minutes=0, hop_budget=0,
                         auto_pause_blockers=0,
                         deliverables=[
                             {"text": "a", "required": True},
                             {"text": "b", "required": False},
                         ])
        updated = store.set_deliverable_met(m["id"], 0, True)
        assert updated["deliverables"][0]["met"] is True
        updated2 = store.set_deliverable_met(m["id"], 1, True)
        assert updated2["deliverables"][1]["met"] is True
        again = store.set_deliverable_met(m["id"], 0, True)
        assert again["deliverables"][0]["met"] is True
        off = store.set_deliverable_met(m["id"], 0, False)
        assert off["deliverables"][0]["met"] is False
        assert store.set_deliverable_met(m["id"], 99, True) is None
