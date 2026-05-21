import unittest
from unittest.mock import AsyncMock, patch

import app as chat_app
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


if __name__ == "__main__":
    unittest.main()
