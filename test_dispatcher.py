import unittest

from dispatcher import select_dispatch_targets


PROFILES = {
    "codex-dispatcher": {
        "name": "codex-dispatcher",
        "role": "Dispatcher",
        "trigger_tags": ["dispatch", "triage", "誰", "不確定"],
        "rank": 1,
    },
    "codex-builder": {
        "name": "codex-builder",
        "role": "Builder",
        "trigger_tags": ["implement", "fix", "實作", "build"],
        "rank": 1,
    },
    "codex-architect": {
        "name": "codex-architect",
        "role": "Architect",
        "trigger_tags": ["architecture", "migration", "schema", "資料流"],
        "rank": 1,
    },
    "claude-designer": {
        "name": "claude-designer",
        "role": "Designer",
        "trigger_tags": ["ui", "ux", "layout", "設計"],
        "rank": 1,
    },
    "claude-reviewer": {
        "name": "claude-reviewer",
        "role": "Reviewer",
        "trigger_tags": ["review", "bug", "regression", "測試"],
        "rank": 1,
    },
    "gemini-challenger": {
        "name": "gemini-challenger",
        "role": "Red Team",
        "trigger_tags": ["risk", "edge case", "security", "風險"],
        "rank": 1,
    },
    "grok-prototyper": {
        "name": "grok-prototyper",
        "role": "Builder",
        "trigger_tags": ["prototype", "spike", "demo"],
        "rank": 3,
    },
}


class DispatcherSelectionTests(unittest.TestCase):
    def test_ui_implementation_selects_designer_then_builder(self):
        targets = select_dispatch_targets(
            "請幫我重新設計 UI/UX layout，然後實作到前端",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=4,
        )

        self.assertEqual(targets[:2], ["claude-designer", "codex-builder"])
        self.assertNotIn("grok-prototyper", targets)

    def test_specialized_same_role_agent_only_joins_when_its_tags_match(self):
        targets = select_dispatch_targets(
            "build a quick prototype spike for this alternative UI",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=4,
        )

        self.assertIn("grok-prototyper", targets)

    def test_review_task_selects_reviewer_and_challenger(self):
        targets = select_dispatch_targets(
            "review this migration for bugs, risk, and edge cases",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets[0], "claude-reviewer")
        self.assertIn("gemini-challenger", targets[:3])

    def test_unclear_task_falls_back_to_dispatcher(self):
        targets = select_dispatch_targets(
            "這件事我不確定該找誰處理",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-dispatcher"])

    def test_offline_profiles_are_not_selected(self):
        targets = select_dispatch_targets(
            "請幫我重新設計 UI layout",
            PROFILES,
            active_names=["codex-dispatcher", "codex-builder"],
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-dispatcher"])


if __name__ == "__main__":
    unittest.main()
