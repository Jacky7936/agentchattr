import unittest

from dispatcher import plan_orchestrator_dispatch, select_dispatch_targets
from team_config import DEFAULT_TEAM_PROFILES


PROFILES = {
    "codex-orchestrator": {
        "name": "codex-orchestrator",
        "role": "Orchestrator",
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
    "codex-qa": {
        "name": "codex-qa",
        "role": "QA Engineer",
        "trigger_tags": ["qa", "regression test", "e2e", "browser qa", "verify fix", "reproduce", "驗收"],
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
    "codex-module-prototype-designer": {
        "name": "codex-module-prototype-designer",
        "role": "Module Prototype Designer",
        "trigger_tags": ["module prototype", "ui prototype", "prototype", "spike", "demo", "草案"],
        "rank": 1,
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

    def test_commander_dispatch_plan_adds_dispatcher_to_parallel_workers(self):
        plan = plan_orchestrator_dispatch(
            "請幫我重新設計 UI/UX layout，然後實作到前端",
            PROFILES,
            active_names=list(PROFILES),
            max_workers=3,
        )

        self.assertEqual(plan["orchestrator"], "codex-orchestrator")
        self.assertEqual(plan["workers"][:2], ["claude-designer", "codex-builder"])
        self.assertEqual(plan["targets"][:3], ["codex-orchestrator", "claude-designer", "codex-builder"])

    def test_commander_dispatch_plan_limits_parallel_workers(self):
        plan = plan_orchestrator_dispatch(
            "review this migration for bugs, regression tests, verify fix, and release risk",
            PROFILES,
            active_names=list(PROFILES),
            max_workers=2,
        )

        self.assertEqual(plan["orchestrator"], "codex-orchestrator")
        self.assertEqual(len(plan["workers"]), 2)
        self.assertEqual(plan["targets"][0], "codex-orchestrator")

    def test_commander_dispatch_plan_falls_back_when_dispatcher_offline(self):
        plan = plan_orchestrator_dispatch(
            "請幫我重新設計 UI layout 並實作到前端",
            PROFILES,
            active_names=["codex-builder", "claude-designer"],
            max_workers=3,
        )

        self.assertEqual(plan["orchestrator"], "")
        self.assertEqual(plan["targets"], ["claude-designer", "codex-builder"])

    def test_review_task_selects_reviewer_and_qa(self):
        targets = select_dispatch_targets(
            "review this migration for bugs, regression tests, verify fix, and release risk",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets[0], "claude-reviewer")
        self.assertIn("codex-qa", targets[:3])

    def test_explicit_qa_verification_selects_codex_qa_first(self):
        targets = select_dispatch_targets(
            "QA verify fix with browser qa, e2e, and acceptance checks",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets[0], "codex-qa")

    def test_code_review_uses_single_reviewer_without_retired_codex_reviewer(self):
        targets = select_dispatch_targets(
            "code review this patch for tests, regressions, and verification gaps",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=4,
        )

        self.assertIn("claude-reviewer", targets)
        self.assertNotIn("codex-reviewer", targets)

    def test_module_ui_prototype_selects_module_prototype_designer(self):
        targets = select_dispatch_targets(
            "依據模組計畫產出 UI原型，讓我檢查流程確認與功能確認",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=4,
        )

        self.assertIn("codex-module-prototype-designer", targets)

    def test_generic_prototype_rolls_into_module_prototype_designer(self):
        targets = select_dispatch_targets(
            "make a quick prototype spike demo",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-module-prototype-designer"])

    def test_research_without_active_researcher_falls_back_to_orchestrator(self):
        targets = select_dispatch_targets(
            "請查最新法規、官方 API 文件和 rate limit 變更",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-orchestrator"])

    def test_unclear_task_falls_back_to_dispatcher(self):
        targets = select_dispatch_targets(
            "這件事我不確定該找誰處理",
            PROFILES,
            active_names=list(PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-orchestrator"])

    def test_offline_profiles_are_not_selected(self):
        targets = select_dispatch_targets(
            "請幫我重新設計 UI layout",
            PROFILES,
            active_names=["codex-orchestrator", "codex-builder"],
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-orchestrator"])


if __name__ == "__main__":
    unittest.main()
