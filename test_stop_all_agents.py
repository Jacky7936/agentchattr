import unittest
from unittest.mock import patch

import app as chat_app


class FakeRegistry:
    def __init__(self, names):
        self.names = list(names)
        self.deregister_called = False

    def get_all_names(self):
        return list(self.names)

    def deregister(self, name):
        self.deregister_called = True
        raise AssertionError(f"deregister should not be called for {name}")


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


class StopAllAgentsTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_all_agents_sends_stop_prompt_without_deregistering(self):
        registry = FakeRegistry(["codex-builder", "claude-reviewer"])
        agents = FakeAgents(["codex-builder", "claude-reviewer"])

        with patch.object(chat_app, "registry", registry), patch.object(chat_app, "agents", agents):
            report = await chat_app._request_registered_agents_to_stop("general")

        self.assertFalse(registry.deregister_called)
        self.assertEqual(report["registered"], ["codex-builder", "claude-reviewer"])
        self.assertEqual(report["requested"], ["codex-builder", "claude-reviewer"])
        self.assertEqual(report["unavailable"], [])
        self.assertEqual([t["agent_name"] for t in agents.triggers], ["codex-builder", "claude-reviewer"])
        for trigger in agents.triggers:
            self.assertEqual(trigger["channel"], "general")
            self.assertIn("Stop your current work", trigger["prompt"])
            self.assertIn("stay registered", trigger["prompt"])

    async def test_stop_all_agents_reports_unavailable_without_clearing_agents(self):
        registry = FakeRegistry(["codex-builder", "claude-reviewer"])
        agents = FakeAgents(["codex-builder"])

        with patch.object(chat_app, "registry", registry), patch.object(chat_app, "agents", agents):
            report = await chat_app._request_registered_agents_to_stop("general")

        self.assertFalse(registry.deregister_called)
        self.assertEqual(report["requested"], ["codex-builder"])
        self.assertEqual(report["unavailable"], ["claude-reviewer"])


if __name__ == "__main__":
    unittest.main()
