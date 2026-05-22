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

    def test_commander_dispatch_plan_allows_unlimited_matching_workers(self):
        plan = plan_orchestrator_dispatch(
            "plan requirements, architecture migration, implement frontend, QA regression, review, UX, research docs, and prototype",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_workers=0,
        )

        self.assertEqual(plan["orchestrator"], "codex-orchestrator")
        self.assertGreater(len(plan["workers"]), 3)
        self.assertIn("codex-planner", plan["workers"])
        self.assertIn("codex-builder", plan["workers"])
        self.assertIn("claude-designer", plan["workers"])

    def test_commander_dispatch_plan_all_agents_request_ignores_worker_cap(self):
        plan = plan_orchestrator_dispatch(
            "@all 請全部 agents 一起看",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_workers=1,
        )

        self.assertEqual(plan["orchestrator"], "codex-orchestrator")
        self.assertEqual(len(plan["workers"]), len(DEFAULT_TEAM_PROFILES) - 1)
        self.assertIn("claude-reviewer", plan["workers"])
        self.assertIn("codex-module-prototype-designer", plan["workers"])

    def test_plain_all_word_does_not_mean_all_agents(self):
        plan = plan_orchestrator_dispatch(
            "請把全部功能修好",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_workers=1,
        )

        self.assertNotEqual(len(plan["workers"]), len(DEFAULT_TEAM_PROFILES) - 1)

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

    def test_planning_task_selects_codex_planner(self):
        targets = select_dispatch_targets(
            "請先規劃 scope、requirements、spec 和分批 implementation plan",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets[0], "codex-planner")

    def test_code_review_prefers_codex_reviewer_when_available(self):
        targets = select_dispatch_targets(
            "code review this patch for tests, regressions, and verification gaps",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=4,
        )

        self.assertEqual(targets[0], "codex-reviewer")
        self.assertNotIn("claude-reviewer", targets)

    def test_generic_review_hard_prunes_second_reviewer_even_if_tags_match(self):
        profiles = {
            "codex-orchestrator": {
                "name": "codex-orchestrator",
                "role": "Orchestrator",
                "rank": 1,
            },
            "codex-reviewer": {
                "name": "codex-reviewer",
                "role": "Reviewer",
                "trigger_tags": ["review", "bug"],
                "rank": 1,
            },
            "claude-reviewer": {
                "name": "claude-reviewer",
                "role": "Reviewer",
                "trigger_tags": ["review", "bug"],
                "rank": 2,
            },
        }

        targets = select_dispatch_targets(
            "please review this patch for bugs",
            profiles,
            active_names=list(profiles),
            max_targets=4,
        )

        self.assertEqual(targets, ["codex-reviewer"])

    def test_cross_model_review_can_select_claude_reviewer_as_second_pass(self):
        targets = select_dispatch_targets(
            "code review this broad migration and run a cross-model second-pass for hidden regressions",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=4,
        )

        self.assertEqual(targets[0], "codex-reviewer")
        self.assertIn("claude-reviewer", targets)

    def test_near_miss_candidates_are_carried_for_orchestrator_context(self):
        profiles = {
            "codex-orchestrator": {
                "name": "codex-orchestrator",
                "role": "Orchestrator",
                "rank": 1,
            },
            "codex-auth-specialist": {
                "name": "codex-auth-specialist",
                "role": "Auth Specialist",
                "trigger_tags": ["authorization design"],
                "specialty": "OAuth token permissions authorization",
                "rank": 2,
            },
        }

        plan = plan_orchestrator_dispatch(
            "OAuth token permissions are confusing; I am not sure who owns this.",
            profiles,
            active_names=list(profiles),
            max_workers=3,
        )

        self.assertEqual(plan["workers"], [])
        self.assertEqual(plan["targets"], ["codex-orchestrator"])
        self.assertEqual(plan["near_misses"][0]["name"], "codex-auth-specialist")

    def test_generic_ui_mockup_prefers_designer_without_prototype_designer(self):
        targets = select_dispatch_targets(
            "請做 UI mockup 和畫面設計方向",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=4,
        )

        self.assertEqual(targets, ["claude-designer"])

    def test_data_table_planning_selects_architect_before_planner(self):
        targets = select_dispatch_targets(
            "請規劃資料表與 migration 邊界",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=4,
        )

        self.assertEqual(targets[0], "codex-architect")
        self.assertIn("codex-planner", targets)

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
        active_names = [name for name in DEFAULT_TEAM_PROFILES if name != "claude-researcher"]
        targets = select_dispatch_targets(
            "請查最新法規、官方 API 文件和 rate limit 變更",
            DEFAULT_TEAM_PROFILES,
            active_names=active_names,
            max_targets=3,
        )

        self.assertEqual(targets, ["codex-orchestrator"])

    def test_research_with_active_researcher_selects_claude_researcher(self):
        targets = select_dispatch_targets(
            "請查最新法規、官方 API 文件和 rate limit 變更",
            DEFAULT_TEAM_PROFILES,
            active_names=list(DEFAULT_TEAM_PROFILES),
            max_targets=3,
        )

        self.assertEqual(targets, ["claude-researcher"])

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
