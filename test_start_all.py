import re
import unittest
from pathlib import Path


class StartAllTeamLaunchTest(unittest.TestCase):
    def test_model_team_launches_with_nonblocking_permission_flags(self):
        script = Path("macos-linux/start_all.sh").read_text("utf-8")
        expected_flags = {
            "codex-dispatcher": "--dangerously-bypass-approvals-and-sandbox",
            "codex-planner": "--dangerously-bypass-approvals-and-sandbox",
            "codex-builder": "--dangerously-bypass-approvals-and-sandbox",
            "codex-architect": "--dangerously-bypass-approvals-and-sandbox",
            "claude-reviewer": "--dangerously-skip-permissions",
            "claude-designer": "--dangerously-skip-permissions",
            "gemini-researcher": "--yolo",
            "gemini-challenger": "--yolo",
            "grok-prototyper": "--always-approve",
        }

        for profile, flag in expected_flags.items():
            with self.subTest(profile=profile):
                command = self._launch_command(script, profile)
                self.assertIn(flag, command)

    def _launch_command(self, script: str, profile: str) -> str:
        match = re.search(rf'(?m)^(?:start_agent\s+)?"([^"]*--profile {re.escape(profile)}[^"]*)"', script)
        if not match:
            match = re.search(rf"(?m)^([^\n]*--profile {re.escape(profile)}[^\n]*)$", script)
        self.assertIsNotNone(match, f"missing launch command for {profile}")
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
