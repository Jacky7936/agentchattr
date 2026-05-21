import unittest

from dispatcher import select_dispatch_targets
from team_config import DEFAULT_TEAM_PROFILES


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
    "codex-architecture-reviewer": {
        "name": "codex-architecture-reviewer",
        "role": "Architecture Reviewer",
        "trigger_tags": ["architecture review", "implementation review", "repo pattern", "migration review", "feasibility"],
        "rank": 2,
    },
    "codex-challenger": {
        "name": "codex-challenger",
        "role": "Engineering Challenger",
        "trigger_tags": ["engineering risk", "migration risk", "overengineering", "repo pattern", "smaller path"],
        "rank": 2,
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
    "gemini-researcher": {
        "name": "gemini-researcher",
        "role": "Researcher",
        "trigger_tags": ["research", "docs", "官方文件", "法規", "api docs", "最新"],
        "rank": 1,
    },
    "gemini-prototyper": {
        "name": "gemini-prototyper",
        "role": "Prototyper",
        "trigger_tags": ["prototype from docs", "ui prototype", "context prototype", "multi-option", "草案"],
        "rank": 2,
    },
    "grok-prototyper": {
        "name": "grok-prototyper",
        "role": "Prototyper",
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

    def test_architecture_review_adds_codex_architecture_reviewer(self):
        targets = select_dispatch_targets(
            "review this migration for repo pattern, implementation feasibility, and architecture review",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertIn("claude-reviewer", targets)
        self.assertIn("codex-architecture-reviewer", targets)

    def test_engineering_challenge_selects_codex_challenger(self):
        targets = select_dispatch_targets(
            "challenge this migration plan for overengineering, repo pattern, migration risk, and smaller path",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets[0], "codex-challenger")

    def test_context_ui_prototype_selects_gemini_prototyper(self):
        targets = select_dispatch_targets(
            "make a UI prototype from docs and context, with multi-option草案",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=4,
        )

        self.assertIn("gemini-prototyper", targets)

    def test_web_api_regulation_research_selects_gemini_researcher(self):
        targets = select_dispatch_targets(
            "請查最新法規、官方 API 文件和 rate limit 變更",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets[0], "gemini-researcher")

    def test_generic_prototype_does_not_false_match_ui_or_pr_keywords(self):
        targets = select_dispatch_targets(
            "make a quick prototype spike demo",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["grok-prototyper"])

    def test_api_docs_research_does_not_pull_architecture_reviewers(self):
        targets = select_dispatch_targets(
            "請查最新法規、官方 API 文件和 rate limit 變更",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["gemini-researcher"])

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
