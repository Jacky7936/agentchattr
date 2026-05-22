import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app as chat_app
import mcp_bridge
from commander_ledger import CommanderLedger
from router import Router


class FakeStore:
    def __init__(self):
        self.messages = []

    def add(self, sender, text, **kwargs):
        self.messages.append({"sender": sender, "text": text, **kwargs})


class FakeAgents:
    def __init__(self, available):
        self.available = set(available)
        self.triggers = []

    def is_available(self, name):
        return name in self.available

    async def trigger(self, agent_name, message="", channel="general", **kwargs):
        self.triggers.append(
            {
                "agent_name": agent_name,
                "message": message,
                "channel": channel,
                "prompt": kwargs.get("prompt", ""),
            }
        )


class FakeSyncAgents(FakeAgents):
    def trigger_sync(self, agent_name, message="", channel="general", **kwargs):
        self.triggers.append(
            {
                "agent_name": agent_name,
                "message": message,
                "channel": channel,
                "prompt": kwargs.get("prompt", ""),
            }
        )


class FakeProfiles:
    def __init__(self, profiles):
        self.profiles = profiles

    def get_all(self):
        return dict(self.profiles)


class FakeRegistry:
    def __init__(self, names):
        self.names = list(names)

    def get_active_names(self):
        return list(self.names)

    def get_all_names(self):
        return list(self.names)

    def resolve_to_instances(self, name):
        return [name]

    def get_instance(self, name):
        return None


class CommanderCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_multiline_handoff_updates_active_lane_from_command_line_only(self):
        router = Router(
            [
                "codex-orchestrator",
                "codex-module-prototype-designer",
                "codex-reviewer",
                "claude-reviewer",
                "claude-designer",
            ],
            default_mention="none",
            max_hops=100,
        )
        router.set_commander_lock(
            "design",
            active_agents=["codex-module-prototype-designer"],
            updated_by="codex-orchestrator",
            reason="/freeze @codex-module-prototype-designer",
        )
        store = FakeStore()
        agents = FakeAgents(["codex-reviewer", "claude-reviewer", "claude-designer"])
        text = (
            "@Jacky polish slice completed, now opening final gate.\n\n"
            "/handoff @codex-reviewer @claude-reviewer @claude-designer\n\n"
            "@codex-module-prototype-designer: pause and wait for final gate."
        )

        with (
            patch.object(chat_app, "router", router),
            patch.object(chat_app, "registry", None),
            patch.object(chat_app, "store", store),
            patch.object(chat_app, "agents", agents),
            patch.object(chat_app, "broadcast_status", AsyncMock()),
        ):
            handled = await chat_app._handle_commander_command("codex-orchestrator", text, "design")

        self.assertTrue(handled)
        self.assertEqual(
            router.get_commander_status("design")["active_agents"],
            ["codex-reviewer", "claude-reviewer", "claude-designer"],
        )
        self.assertEqual(
            [trigger["agent_name"] for trigger in agents.triggers],
            ["codex-reviewer", "claude-reviewer", "claude-designer"],
        )
        self.assertNotIn(
            "codex-module-prototype-designer",
            router.get_commander_status("design")["active_agents"],
        )
        self.assertIn("Commander lock: only @codex-reviewer", store.messages[0]["text"])
        self.assertIn("active worker lane", agents.triggers[0]["prompt"])
        self.assertIn("outside the active lane", agents.triggers[0]["prompt"])
        self.assertIn("report done or blocked once and stop", agents.triggers[0]["prompt"])

    async def test_auto_dispatch_prompts_keep_workers_in_active_lane(self):
        commander_prompt = chat_app._auto_dispatch_prompt(
            "general",
            target="codex-orchestrator",
            commander="codex-orchestrator",
            workers=["codex-builder", "codex-qa"],
            near_misses=[{"name": "codex-architect", "role": "Architect", "score": 8}],
        )
        worker_prompt = chat_app._auto_dispatch_prompt(
            "general",
            target="codex-builder",
            commander="codex-orchestrator",
            workers=["codex-builder", "codex-qa"],
        )

        self.assertIn("parallel-dispatch", commander_prompt)
        self.assertIn("no fixed worker cap", commander_prompt)
        self.assertIn("near-miss", commander_prompt)
        self.assertIn("@codex-architect", commander_prompt)
        self.assertIn("/release", commander_prompt)
        self.assertIn("stop", commander_prompt)
        self.assertIn("chat_set_lane_backlog", commander_prompt)
        self.assertIn("@codex-qa", worker_prompt)
        self.assertIn("outside the active lane", worker_prompt)
        self.assertIn("report done/blockers once and stop", worker_prompt)
        self.assertIn("chat_update_lane_item", worker_prompt)

    async def test_explicit_all_routes_through_orchestrator_lane(self):
        profiles = {
            "codex-orchestrator": {"name": "codex-orchestrator", "role": "Orchestrator", "rank": 1},
            "codex-builder": {"name": "codex-builder", "role": "Builder", "rank": 1},
            "codex-qa": {"name": "codex-qa", "role": "QA Engineer", "rank": 1},
        }
        router = Router(
            ["codex-orchestrator", "codex-builder", "codex-qa"],
            default_mention="none",
            max_hops=4,
            online_checker=lambda: {"codex-orchestrator", "codex-builder", "codex-qa"},
        )
        store = FakeStore()
        agents = FakeAgents(["codex-orchestrator", "codex-builder", "codex-qa"])

        with (
            patch.object(chat_app, "router", router),
            patch.object(chat_app, "registry", FakeRegistry(profiles)),
            patch.object(chat_app, "agent_profiles", FakeProfiles(profiles)),
            patch.object(chat_app, "store", store),
            patch.object(chat_app, "agents", agents),
            patch.object(chat_app, "commander_ledger", None),
            patch.object(chat_app, "config", {"routing": {"auto_dispatch": True, "auto_dispatch_max_targets": 0, "auto_dispatch_announce": True}}),
            patch.object(chat_app, "room_settings", {"channels": ["general"], "username": "Jacky"}),
            patch.object(chat_app, "broadcast", AsyncMock()),
            patch.object(chat_app, "broadcast_status", AsyncMock()),
            patch("mcp_bridge.is_online", return_value=True),
        ):
            await chat_app._handle_new_message(
                {"sender": "Jacky", "text": "@all 請全部 agents 一起處理", "channel": "general", "type": "chat"}
            )

        self.assertEqual(
            router.get_commander_status("general")["active_agents"],
            ["codex-builder", "codex-qa"],
        )
        self.assertEqual(
            [trigger["agent_name"] for trigger in agents.triggers],
            ["codex-orchestrator", "codex-builder", "codex-qa"],
        )
        self.assertIn("Orchestrator auto-dispatched", store.messages[0]["text"])

    async def test_commander_lane_uses_separate_hop_budget(self):
        router = Router(
            ["codex-orchestrator", "codex-builder", "codex-qa"],
            default_mention="none",
            max_hops=1,
            commander_max_hops=3,
        )
        router.set_commander_lock(
            "general",
            active_agents=["codex-builder", "codex-qa"],
            updated_by="codex-orchestrator",
            reason="/handoff @codex-builder @codex-qa",
        )

        self.assertEqual(router.get_targets("codex-builder", "@codex-qa one"), ["codex-qa"])
        self.assertEqual(router.get_targets("codex-qa", "@codex-builder two"), ["codex-builder"])
        self.assertEqual(router.get_targets("codex-builder", "@codex-qa three"), ["codex-qa"])
        self.assertEqual(router.get_targets("codex-qa", "@codex-builder four"), [])
        self.assertTrue(router.is_paused("general"))

    async def test_commander_restore_waits_until_full_lane_is_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")
            ledger.start_lane(
                "general",
                commander="codex-orchestrator",
                active_agents=["codex-builder", "codex-qa"],
                task="Previous long task",
                reason="auto-dispatch",
                now=1000,
            )
            router = Router(
                ["codex-orchestrator", "codex-builder", "codex-qa"],
                default_mention="none",
                max_hops=4,
            )
            store = FakeStore()
            agents = FakeAgents(["codex-orchestrator", "codex-builder", "codex-qa"])
            registry = FakeRegistry(["codex-orchestrator", "codex-builder", "codex-qa"])

            chat_app._restored_commander_lanes.clear()
            chat_app._restore_reconcile_notified.clear()
            with (
                patch.object(chat_app, "router", router),
                patch.object(chat_app, "registry", registry),
                patch.object(chat_app, "store", store),
                patch.object(chat_app, "agents", agents),
                patch.object(chat_app, "commander_ledger", ledger),
                patch.object(chat_app, "config", {"routing": {"commander_restore_max_age_seconds": 43200}}),
                patch("mcp_bridge.is_online", side_effect=lambda name: name in {"codex-orchestrator", "codex-builder"}),
            ):
                restored = await chat_app._maybe_restore_commander_lanes(now=1200)
                repeated = await chat_app._maybe_restore_commander_lanes(now=1210)

            self.assertEqual(restored, [])
            self.assertEqual(repeated, [])
            self.assertFalse(router.get_commander_status("general")["locked"])
            self.assertEqual(len(agents.triggers), 1)
            self.assertIn("restore decision", agents.triggers[0]["prompt"])

            with (
                patch.object(chat_app, "router", router),
                patch.object(chat_app, "registry", registry),
                patch.object(chat_app, "store", store),
                patch.object(chat_app, "agents", agents),
                patch.object(chat_app, "commander_ledger", ledger),
                patch.object(chat_app, "config", {"routing": {"commander_restore_max_age_seconds": 43200}}),
                patch("mcp_bridge.is_online", return_value=True),
            ):
                restored = await chat_app._maybe_restore_commander_lanes(now=1220)

        self.assertEqual(restored, ["general"])
        self.assertEqual(
            router.get_commander_status("general")["active_agents"],
            ["codex-builder", "codex-qa"],
        )

    async def test_commander_restore_releases_stale_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")
            ledger.start_lane(
                "general",
                commander="codex-orchestrator",
                active_agents=["codex-builder"],
                task="Old task",
                reason="auto-dispatch",
                now=1000,
            )
            router = Router(["codex-orchestrator", "codex-builder"], default_mention="none", max_hops=4)
            store = FakeStore()
            registry = FakeRegistry(["codex-orchestrator", "codex-builder"])

            chat_app._restored_commander_lanes.clear()
            chat_app._restore_reconcile_notified.clear()
            with (
                patch.object(chat_app, "router", router),
                patch.object(chat_app, "registry", registry),
                patch.object(chat_app, "store", store),
                patch.object(chat_app, "agents", FakeAgents(["codex-orchestrator", "codex-builder"])),
                patch.object(chat_app, "commander_ledger", ledger),
                patch.object(chat_app, "config", {"routing": {"commander_restore_max_age_seconds": 100}}),
                patch("mcp_bridge.is_online", return_value=True),
            ):
                restored = await chat_app._maybe_restore_commander_lanes(now=1200)

            restored_ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")

        self.assertEqual(restored, [])
        self.assertFalse(router.get_commander_status("general")["locked"])
        self.assertEqual(restored_ledger.get("general")["status"], "released")
        self.assertIn("stale", store.messages[0]["text"])

    async def test_commander_watchdog_nudges_orchestrator_when_active_worker_is_quiet(self):
        router = Router(
            ["codex-orchestrator", "claude-designer", "claude-reviewer"],
            default_mention="none",
            max_hops=100,
        )
        router.set_commander_lock(
            "design",
            active_agents=["claude-designer"],
            updated_by="codex-orchestrator",
            reason="/handoff @claude-designer",
            now=1000,
        )
        store = FakeStore()
        agents = FakeAgents(["codex-orchestrator", "claude-designer"])

        with (
            patch.object(chat_app, "router", router),
            patch.object(chat_app, "registry", None),
            patch.object(chat_app, "store", store),
            patch.object(chat_app, "agents", agents),
            patch.object(chat_app, "config", {"routing": {"commander_watchdog_seconds": 180}}),
            patch.object(chat_app, "room_settings", {"channels": ["general", "design"], "username": "Jacky"}),
            patch.object(chat_app, "broadcast_status", AsyncMock()),
        ):
            nudges = await chat_app._run_commander_watchdog(now=1181)

        self.assertEqual(nudges, ["design"])
        self.assertEqual(len(agents.triggers), 1)
        self.assertEqual(agents.triggers[0]["agent_name"], "codex-orchestrator")
        self.assertIn("@claude-designer has been quiet", store.messages[0]["text"])
        self.assertIn("handoff/ETA", agents.triggers[0]["prompt"])

    async def test_commander_watchdog_resets_after_active_worker_status(self):
        router = Router(
            ["codex-orchestrator", "claude-designer", "claude-reviewer"],
            default_mention="none",
            max_hops=100,
        )
        router.set_commander_lock(
            "design",
            active_agents=["claude-designer"],
            updated_by="codex-orchestrator",
            reason="/handoff @claude-designer",
            now=1000,
        )
        router.note_message("design", "claude-designer", timestamp=1170)
        store = FakeStore()
        agents = FakeAgents(["codex-orchestrator", "claude-designer"])

        with (
            patch.object(chat_app, "router", router),
            patch.object(chat_app, "registry", None),
            patch.object(chat_app, "store", store),
            patch.object(chat_app, "agents", agents),
            patch.object(chat_app, "config", {"routing": {"commander_watchdog_seconds": 180}}),
            patch.object(chat_app, "room_settings", {"channels": ["design"], "username": "Jacky"}),
            patch.object(chat_app, "broadcast_status", AsyncMock()),
        ):
            nudges = await chat_app._run_commander_watchdog(now=1300)

        self.assertEqual(nudges, [])
        self.assertEqual(store.messages, [])
        self.assertEqual(agents.triggers, [])

    async def test_commander_watchdog_waits_for_structured_progress_eta(self):
        router = Router(
            ["codex-orchestrator", "claude-designer"],
            default_mention="none",
            max_hops=100,
        )
        router.set_commander_lock(
            "design",
            active_agents=["claude-designer"],
            updated_by="codex-orchestrator",
            reason="/handoff @claude-designer",
            now=1000,
        )
        router.note_progress(
            "design",
            "claude-designer",
            state="running",
            eta_seconds=600,
            note="rendering mobile and desktop screenshots",
            timestamp=1010,
        )
        store = FakeStore()
        agents = FakeAgents(["codex-orchestrator", "claude-designer"])

        with (
            patch.object(chat_app, "router", router),
            patch.object(chat_app, "registry", None),
            patch.object(chat_app, "store", store),
            patch.object(chat_app, "agents", agents),
            patch.object(chat_app, "commander_ledger", None),
            patch.object(chat_app, "config", {"routing": {"commander_watchdog_seconds": 180}}),
            patch.object(chat_app, "room_settings", {"channels": ["design"], "username": "Jacky"}),
            patch.object(chat_app, "broadcast_status", AsyncMock()),
        ):
            early = await chat_app._run_commander_watchdog(now=1191)
            expired = await chat_app._run_commander_watchdog(now=1611)

        self.assertEqual(early, [])
        self.assertEqual(expired, ["design"])
        self.assertEqual(len(agents.triggers), 1)
        self.assertIn("last progress", agents.triggers[0]["prompt"])
        self.assertIn("rendering mobile and desktop screenshots", agents.triggers[0]["prompt"])

    async def test_chat_report_progress_updates_router_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")
            ledger.start_lane(
                "general",
                commander="codex-orchestrator",
                active_agents=["codex-builder"],
                task="Run tests",
                reason="auto-dispatch",
                now=1000,
            )
            router = Router(["codex-orchestrator", "codex-builder"], default_mention="none", max_hops=100)
            router.set_commander_lock(
                "general",
                active_agents=["codex-builder"],
                updated_by="codex-orchestrator",
                reason="/handoff @codex-builder",
                now=1000,
            )

            with (
                patch.object(mcp_bridge, "router", router),
                patch.object(mcp_bridge, "commander_ledger", ledger),
                patch.object(mcp_bridge, "registry", None),
            ):
                result = mcp_bridge.chat_report_progress(
                    sender="codex-builder",
                    state="running",
                    eta_seconds=300,
                    note="unit tests running",
                    channel="general",
                )

            restored = CommanderLedger(Path(tmp) / "commander_ledger.json")

        self.assertIn('"ok": true', result)
        self.assertEqual(router.get_commander_status("general")["worker_progress"]["codex-builder"]["state"], "running")
        self.assertEqual(restored.get("general")["progress"]["codex-builder"]["note"], "unit tests running")

    async def test_lane_item_approval_auto_starts_worker_for_next_backlog_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")
            ledger.start_lane(
                "design",
                commander="codex-orchestrator",
                active_agents=["codex-module-prototype-designer", "codex-reviewer"],
                task="Refresh mockups",
                reason="auto-dispatch",
                now=1000,
            )
            ledger.set_backlog(
                "design",
                items=["mockups/dashboard.html", "mockups/index.html"],
                created_by="codex-orchestrator",
                worker="codex-module-prototype-designer",
                reviewer="codex-reviewer",
                now=1010,
            )
            agents = FakeSyncAgents(["codex-orchestrator", "codex-module-prototype-designer", "codex-reviewer"])

            with (
                patch.object(mcp_bridge, "commander_ledger", ledger),
                patch.object(mcp_bridge, "agents", agents),
                patch.object(mcp_bridge, "store", FakeStore()),
                patch.object(mcp_bridge, "router", None),
                patch.object(mcp_bridge, "registry", None),
            ):
                result = mcp_bridge.chat_update_lane_item(
                    sender="codex-reviewer",
                    state="approved",
                    note="handoff ready",
                    channel="design",
                )

            restored = CommanderLedger(Path(tmp) / "commander_ledger.json")

        self.assertIn('"ok": true', result)
        self.assertEqual(restored.next_backlog_item("design")["text"], "mockups/index.html")
        self.assertEqual(len(agents.triggers), 1)
        self.assertEqual(agents.triggers[0]["agent_name"], "codex-module-prototype-designer")
        self.assertIn("mockups/index.html", agents.triggers[0]["prompt"])
        self.assertIn("auto-start", agents.triggers[0]["prompt"])
        self.assertIn("chat_update_lane_item(state='running')", agents.triggers[0]["prompt"])

    async def test_commander_watchdog_escalates_repeated_quiet_lanes(self):
        router = Router(
            ["codex-orchestrator", "claude-designer"],
            default_mention="none",
            max_hops=100,
        )
        router.set_commander_lock(
            "design",
            active_agents=["claude-designer"],
            updated_by="codex-orchestrator",
            reason="/handoff @claude-designer",
            now=1000,
        )
        store = FakeStore()
        agents = FakeAgents(["codex-orchestrator", "claude-designer"])

        with (
            patch.object(chat_app, "router", router),
            patch.object(chat_app, "registry", None),
            patch.object(chat_app, "store", store),
            patch.object(chat_app, "agents", agents),
            patch.object(chat_app, "commander_ledger", None),
            patch.object(chat_app, "config", {"routing": {"commander_watchdog_seconds": 180}}),
            patch.object(chat_app, "room_settings", {"channels": ["design"], "username": "Jacky"}),
            patch.object(chat_app, "broadcast_status", AsyncMock()),
        ):
            await chat_app._run_commander_watchdog(now=1181)
            await chat_app._run_commander_watchdog(now=1362)
            await chat_app._run_commander_watchdog(now=1543)

        self.assertIn("escalation 2", agents.triggers[1]["prompt"])
        self.assertIn("human", agents.triggers[2]["prompt"])


if __name__ == "__main__":
    unittest.main()
