import time
import tempfile
import unittest
from pathlib import Path

import mcp_bridge
from agents import AgentTrigger
from registry import RuntimeRegistry


AGENTS = {
    "codex": {"label": "Codex", "color": "#10a37f"},
}


class AgentThinkingIndicatorTests(unittest.TestCase):
    def setUp(self):
        with mcp_bridge._presence_lock:
            mcp_bridge._presence.clear()
            mcp_bridge._activity.clear()
            mcp_bridge._activity_ts.clear()
            if hasattr(mcp_bridge, "_activity_channel"):
                mcp_bridge._activity_channel.clear()

    def test_agent_status_includes_busy_channel_for_thinking_indicator(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = RuntimeRegistry(data_dir=tmp)
            registry.seed(AGENTS)
            registry.register(
                "codex",
                "Codex Builder",
                requested_name="codex-builder",
                profile_id="codex-builder",
            )
            trigger = AgentTrigger(registry, data_dir=tmp)

            with mcp_bridge._presence_lock:
                mcp_bridge._presence["codex-builder"] = time.time()
            mcp_bridge.set_active("codex-builder", True, channel="design")

            status = trigger.get_status()

        self.assertTrue(status["codex-builder"]["busy"])
        self.assertEqual(status["codex-builder"]["busy_channel"], "design")

    def test_active_heartbeat_without_channel_clears_stale_channel(self):
        mcp_bridge.set_active("codex-builder", True, channel="design")
        self.assertEqual(mcp_bridge.get_activity_channel("codex-builder"), "design")

        mcp_bridge.set_active("codex-builder", True)

        self.assertEqual(mcp_bridge.get_activity_channel("codex-builder"), "")

    def test_frontend_renders_status_busy_agents_as_typing_bubbles(self):
        chat_js = Path("static/chat.js").read_text("utf-8")
        style_css = Path("static/style.css").read_text("utf-8")

        self.assertIn("updateThinkingIndicators(data)", chat_js)
        self.assertIn("function renderThinkingIndicators", chat_js)
        self.assertIn("busy_channel", chat_js)
        self.assertIn("typing-message", chat_js)
        self.assertIn("typing-bubble", chat_js)
        self.assertIn("activeChannel", chat_js)
        self.assertIn(".typing-message", style_css)
        self.assertIn(".typing-bubble", style_css)


if __name__ == "__main__":
    unittest.main()
