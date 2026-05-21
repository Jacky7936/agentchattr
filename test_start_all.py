import re
import unittest
from pathlib import Path


class StartAllTeamLaunchTest(unittest.TestCase):
    def test_model_team_launches_with_expected_provider_and_nonblocking_permission_flags(self):
        script = Path("macos-linux/start_all.sh").read_text("utf-8")
        expected = {
            "codex-orchestrator": ("wrapper.py codex", "--dangerously-bypass-approvals-and-sandbox"),
            "codex-builder": ("wrapper.py codex", "--dangerously-bypass-approvals-and-sandbox"),
            "codex-architect": ("wrapper.py codex", "--dangerously-bypass-approvals-and-sandbox"),
            "codex-qa": ("wrapper.py codex", "--dangerously-bypass-approvals-and-sandbox"),
            "codex-module-prototype-designer": ("wrapper.py codex", "--dangerously-bypass-approvals-and-sandbox"),
            "claude-reviewer": ("wrapper.py claude", "--permission-mode auto"),
            "claude-designer": ("wrapper.py claude", "--permission-mode auto"),
        }

        for profile, (provider, flag) in expected.items():
            with self.subTest(profile=profile):
                command = self._launch_command(script, profile)
                self.assertIn(provider, command)
                self.assertIn(flag, command)

        self.assertIn('AGENT_START_DELAY_SECONDS="${AGENTCHATTR_AGENT_START_DELAY_SECONDS:-5}"', script)
        self.assertGreaterEqual(script.count("wait_between_agent_starts"), len(expected))
        self.assertNotIn("--profile codex-architecture-reviewer", script)
        self.assertNotIn("--profile codex-spike-prototyper", script)
        self.assertNotIn("--profile codex-dispatcher", script)
        self.assertNotIn("--profile codex-planner", script)
        self.assertNotIn("--profile codex-reviewer", script)
        self.assertNotIn("--profile codex-challenger", script)
        self.assertNotIn("--profile codex-prototyper", script)
        self.assertNotIn("--profile claude-researcher", script)
        self.assertNotIn("--profile claude-challenger", script)

    def _launch_command(self, script: str, profile: str) -> str:
        match = re.search(rf'(?m)^(?:start_agent\s+)?"([^"]*--profile {re.escape(profile)}[^"]*)"', script)
        if not match:
            match = re.search(rf"(?m)^([^\n]*--profile {re.escape(profile)}[^\n]*)$", script)
        self.assertIsNotNone(match, f"missing launch command for {profile}")
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
