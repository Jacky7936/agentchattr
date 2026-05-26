import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as chat_app
from commander_ledger import CommanderLedger
from router import Router


class FakeStore:
    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def add(self, sender, text, **kwargs):
        msg = {"sender": sender, "text": text, "type": kwargs.get("msg_type", "chat"), **kwargs}
        self.messages.append(msg)
        return msg

    def get_recent(self, count=50, channel=None):
        msgs = self.messages
        if channel:
            msgs = [m for m in msgs if m.get("channel", "general") == channel]
        return list(msgs[-count:])


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


class CommanderHandoffContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_commander_handoff_uses_latest_human_instruction_as_task_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")
            router = Router(["codex-orchestrator", "codex-planner"], default_mention="none", max_hops=100)
            store = FakeStore(
                [
                    {
                        "sender": "Jacky",
                        "text": "@codex-orchestrator 請指派 Planner 修 implementation plan 的 blocker 清單",
                        "type": "chat",
                        "channel": "general",
                    }
                ]
            )
            agents = FakeAgents(["codex-planner"])

            with (
                patch.object(chat_app, "router", router),
                patch.object(chat_app, "registry", None),
                patch.object(chat_app, "store", store),
                patch.object(chat_app, "agents", agents),
                patch.object(chat_app, "commander_ledger", ledger),
                patch.object(chat_app, "room_settings", {"channels": ["general"], "username": "Jacky"}),
                patch.object(chat_app, "broadcast_status", AsyncMock()),
            ):
                handled = await chat_app._handle_commander_command(
                    "codex-orchestrator",
                    "/handoff @codex-planner",
                    "general",
                )
                lane = ledger.get("general")
                actionable = chat_app._commander_watchdog_has_actionable_work(
                    "general",
                    router.get_commander_status("general"),
                )

        self.assertTrue(handled)
        self.assertTrue(actionable)
        self.assertTrue(router.get_commander_status("general")["locked"])
        self.assertEqual(lane["active_agents"], ["codex-planner"])
        self.assertIn("請指派 Planner 修 implementation plan", lane["task"])
        self.assertEqual([trigger["agent_name"] for trigger in agents.triggers], ["codex-planner"])
        self.assertIn("請指派 Planner 修 implementation plan", agents.triggers[0]["prompt"])

    async def test_agent_commander_handoff_skips_generic_human_nudge_for_prior_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CommanderLedger(Path(tmp) / "commander_ledger.json")
            router = Router(["codex-orchestrator", "codex-planner"], default_mention="none", max_hops=100)
            store = FakeStore(
                [
                    {
                        "sender": "Jacky",
                        "text": (
                            "@codex-reviewer @claude-reviewer @codex-architect review "
                            "2026-05-23-infra-first-vertical-slice-implementation-plan.md；"
                            "@codex-planner 統整並判定是否修 plan；@codex-orchestrator 你來指揮"
                        ),
                        "type": "chat",
                        "channel": "general",
                    },
                    {
                        "sender": "Jacky",
                        "text": "@codex-orchestrator 應該要由你指揮推進啊",
                        "type": "chat",
                        "channel": "general",
                    },
                ]
            )
            agents = FakeAgents(["codex-planner"])

            with (
                patch.object(chat_app, "router", router),
                patch.object(chat_app, "registry", None),
                patch.object(chat_app, "store", store),
                patch.object(chat_app, "agents", agents),
                patch.object(chat_app, "commander_ledger", ledger),
                patch.object(chat_app, "room_settings", {"channels": ["general"], "username": "Jacky"}),
                patch.object(chat_app, "broadcast_status", AsyncMock()),
            ):
                handled = await chat_app._handle_commander_command(
                    "codex-orchestrator",
                    "/handoff @codex-planner",
                    "general",
                )
                lane = ledger.get("general")

        self.assertTrue(handled)
        self.assertIn("2026-05-23-infra-first-vertical-slice-implementation-plan.md", lane["task"])
        self.assertIn("2026-05-23-infra-first-vertical-slice-implementation-plan.md", agents.triggers[0]["prompt"])
        self.assertNotIn("應該要由你指揮推進啊", lane["task"])


if __name__ == "__main__":
    unittest.main()
