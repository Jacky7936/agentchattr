import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from router import Router


class RouterMentionTests(unittest.TestCase):
    def test_hyphenated_agent_name_is_parsed_as_full_mention(self):
        router = Router(["telegram-bridge"], default_mention="none")

        self.assertEqual(
            set(router.parse_mentions("please ask @telegram-bridge to check")),
            {"telegram-bridge"},
        )

    def test_shorter_agent_name_does_not_match_prefix_of_hyphenated_unknown(self):
        router = Router(["telegram"], default_mention="none")

        self.assertEqual(router.parse_mentions("@telegram-bridge check"), [])
        self.assertEqual(router.get_targets("ben", "@telegram-bridge check"), [])

    def test_longest_hyphenated_name_wins_when_prefix_agent_also_exists(self):
        router = Router(["telegram", "telegram-bridge"], default_mention="none")

        self.assertEqual(
            set(router.parse_mentions("@telegram-bridge check")),
            {"telegram-bridge"},
        )

    def test_unknown_exact_handle_still_does_not_route(self):
        router = Router(["telegram-bridge"], default_mention="none")

        self.assertEqual(router.parse_mentions("@telegram-bot check"), [])
        self.assertEqual(router.get_targets("ben", "@telegram-bot check"), [])

    def test_commander_lock_blocks_non_active_agent_fanout(self):
        router = Router(["codex-orchestrator", "codex-builder", "claude-reviewer"], default_mention="none")
        router.set_commander_lock("design", active_agent="codex-builder", updated_by="codex-orchestrator")

        self.assertEqual(
            router.get_targets("claude-reviewer", "@codex-builder ACK", channel="design"),
            [],
        )

    def test_commander_lock_allows_dispatcher_to_wake_only_active_agent(self):
        router = Router(["codex-orchestrator", "codex-builder", "claude-reviewer"], default_mention="none")
        router.set_commander_lock("design", active_agent="codex-builder", updated_by="codex-orchestrator")

        self.assertEqual(
            router.get_targets(
                "codex-orchestrator",
                "@codex-builder @claude-reviewer first handoff only",
                channel="design",
            ),
            ["codex-builder"],
        )

    def test_commander_lock_supports_parallel_active_agents(self):
        router = Router(
            ["codex-orchestrator", "codex-builder", "codex-qa", "claude-reviewer"],
            default_mention="all",
        )
        status = router.set_commander_lock(
            "design",
            active_agents=["codex-builder", "codex-qa"],
            updated_by="codex-orchestrator",
        )

        self.assertEqual(status["active_agents"], ["codex-builder", "codex-qa"])
        self.assertEqual(status["active_agent"], "codex-builder")
        self.assertEqual(
            router.get_targets(
                "codex-orchestrator",
                "@codex-builder @codex-qa @claude-reviewer split work",
                channel="design",
            ),
            ["codex-builder", "codex-qa"],
        )
        self.assertEqual(
            router.get_targets("Jacky", "please continue", channel="design"),
            ["codex-builder", "codex-qa"],
        )

    def test_parallel_active_agent_can_wake_lane_peer_and_dispatcher_only(self):
        router = Router(
            ["codex-orchestrator", "codex-builder", "codex-qa", "claude-reviewer"],
            default_mention="none",
        )
        router.set_commander_lock(
            "design",
            active_agents=["codex-builder", "codex-qa"],
            updated_by="codex-orchestrator",
        )

        self.assertEqual(
            router.get_targets(
                "codex-builder",
                "@codex-qa please continue and @codex-orchestrator status @claude-reviewer wait",
                channel="design",
            ),
            ["codex-qa", "codex-orchestrator"],
        )

    def test_commander_lock_allows_active_agent_to_wake_dispatcher(self):
        router = Router(["codex-orchestrator", "codex-builder", "claude-reviewer"], default_mention="none")
        router.set_commander_lock("design", active_agent="codex-builder", updated_by="codex-orchestrator")

        self.assertEqual(
            router.get_targets("codex-builder", "@codex-orchestrator handoff ready", channel="design"),
            ["codex-orchestrator"],
        )
        self.assertEqual(
            router.get_targets("codex-builder", "@claude-reviewer please review", channel="design"),
            [],
        )

    def test_human_explicit_mentions_override_commander_lock(self):
        router = Router(["codex-orchestrator", "codex-builder", "claude-reviewer"], default_mention="none")
        router.set_commander_lock("design", active_agent="codex-builder", updated_by="codex-orchestrator")

        self.assertEqual(
            router.get_targets("Jacky", "@claude-reviewer please inspect", channel="design"),
            ["claude-reviewer"],
        )

    def test_human_default_routing_follows_active_commander_lane(self):
        router = Router(["codex-orchestrator", "codex-builder", "claude-reviewer"], default_mention="all")
        router.set_commander_lock("design", active_agent="codex-builder", updated_by="codex-orchestrator")

        self.assertEqual(router.get_targets("Jacky", "continue", channel="design"), ["codex-builder"])


if __name__ == "__main__":
    unittest.main()
