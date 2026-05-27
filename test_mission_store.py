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
