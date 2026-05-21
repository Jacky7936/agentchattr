import json
import tempfile
import unittest
from pathlib import Path

from agent_profiles import AgentProfileStore
from registry import RuntimeRegistry


AGENTS = {
    "codex": {"label": "Codex", "color": "#10a37f"},
    "claude": {"label": "Claude", "color": "#da7756"},
    "gemini": {"label": "Gemini", "color": "#4285f4"},
    "antigravity": {"label": "Antigravity", "color": "#7c3aed"},
    "grok": {"label": "Grok Build", "color": "#06b6d4"},
}


class AgentProfileStoreTest(unittest.TestCase):
    def test_bootstraps_roles_and_migrates_reviwer_typo(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            roles_path = data_dir / "roles.json"
            roles_path.write_text(
                json.dumps({"codex-reviwer": "Reviewer", "claude-designer": "Designer"}),
                "utf-8",
            )

            store = AgentProfileStore(data_dir / "agent_profiles.json", AGENTS)
            store.bootstrap_from_roles(roles_path)
            profiles = store.get_all()

            self.assertIn("codex-reviewer", profiles)
            self.assertNotIn("codex-reviwer", profiles)
            self.assertEqual(profiles["codex-reviewer"]["name"], "codex-reviewer")
            self.assertEqual(profiles["codex-reviewer"]["role"], "Reviewer")
            self.assertEqual(profiles["claude-designer"]["label"], "Claude Designer")

    def test_profile_registration_duplicate_and_restore_after_deregister(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            store = AgentProfileStore(data_dir / "agent_profiles.json", AGENTS)
            profile, err = store.ensure_profile("codex-builder", "codex")
            self.assertIsNone(err)

            registry = RuntimeRegistry(data_dir=str(data_dir))
            registry.seed(AGENTS)

            first = registry.register(
                "codex",
                profile["label"],
                requested_name=profile["name"],
                profile_id="codex-builder",
            )
            self.assertIsInstance(first, dict)
            self.assertEqual(first["name"], "codex-builder")
            self.assertEqual(first["profile_id"], "codex-builder")

            duplicate = registry.register(
                "codex",
                profile["label"],
                requested_name=profile["name"],
                profile_id="codex-builder",
            )
            self.assertIsInstance(duplicate, str)
            self.assertIn("Profile already active", duplicate)

            registry.deregister("codex-builder")
            saved = store.get("codex-builder")
            self.assertEqual(saved["role"], "Builder")

            restored = registry.register(
                "codex",
                saved["label"],
                requested_name=saved["name"],
                profile_id="codex-builder",
            )
            self.assertEqual(restored["name"], "codex-builder")
            self.assertEqual(restored["profile_id"], "codex-builder")

    def test_profile_registration_clears_stale_rename_redirects(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "renames.json").write_text(
                json.dumps(
                    {
                        "codex-builder": "codex-reviewer",
                        "codex-reviewer": "codex-architect",
                        "codex-4": "codex-reviewer",
                    }
                ),
                "utf-8",
            )

            registry = RuntimeRegistry(data_dir=str(data_dir))
            registry.seed(AGENTS)
            result = registry.register(
                "codex",
                "Codex Reviewer",
                requested_name="codex-reviewer",
                profile_id="codex-reviewer",
            )

            self.assertIsInstance(result, dict)
            self.assertEqual(result["name"], "codex-reviewer")
            self.assertEqual(registry.resolve_name("codex-reviewer"), "codex-reviewer")

            renames = json.loads((data_dir / "renames.json").read_text("utf-8"))
            self.assertNotIn("codex-reviewer", renames)
            self.assertNotIn("codex-reviewer", renames.values())
            self.assertEqual(renames, {})

    def test_role_and_rename_sync_keep_stable_profile_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentProfileStore(Path(tmp) / "agent_profiles.json", AGENTS)
            store.update_role_for_name(
                "codex-builder",
                "Builder",
                base="codex",
                profile_id="codex-builder",
                label="Codex Builder",
            )
            store.update_identity(
                old_name="codex-builder",
                new_name="codex-reviewer",
                label="Codex Reviewer",
                base="codex",
                profile_id="codex-builder",
                role="Reviewer",
            )

            profile = store.get("codex-builder")
            self.assertEqual(profile["name"], "codex-reviewer")
            self.assertEqual(profile["label"], "Codex Reviewer")
            self.assertEqual(profile["role"], "Reviewer")

    def test_profile_metadata_survives_load_and_role_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "claude-reviewer": {
                            "base": "claude",
                            "name": "claude-reviewer",
                            "label": "Claude Reviewer",
                            "role": "Reviewer",
                            "model": "Claude Code Opus 4.7",
                            "specialty": "Find regressions and missing tests.",
                            "trigger_tags": ["review", "bug", "regression"],
                            "rank": 1,
                            "responsibilities": ["Lead bug review"],
                            "avoid": ["Do not implement unless asked"],
                            "output_contract": "Findings first.",
                        }
                    }
                ),
                "utf-8",
            )

            store = AgentProfileStore(profiles_path, AGENTS)
            profile = store.get("claude-reviewer")
            self.assertEqual(profile["model"], "Claude Code Opus 4.7")
            self.assertEqual(profile["specialty"], "Find regressions and missing tests.")
            self.assertEqual(profile["trigger_tags"], ["review", "bug", "regression"])
            self.assertEqual(profile["rank"], 1)

            store.update_role_for_name(
                "claude-reviewer",
                "Security Reviewer",
                base="claude",
                profile_id="claude-reviewer",
                label="Claude Reviewer",
            )
            updated = store.get("claude-reviewer")
            self.assertEqual(updated["role"], "Security Reviewer")
            self.assertEqual(updated["specialty"], "Find regressions and missing tests.")
            self.assertEqual(updated["responsibilities"], ["Lead bug review"])

    def test_default_team_profiles_seed_missing_metadata_without_overwriting_user_changes(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profiles_path = data_dir / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "claude-reviewer": {
                            "base": "claude",
                            "name": "claude-reviewer",
                            "label": "Claude Reviewer",
                            "role": "My Custom Reviewer",
                            "model": "Custom Claude Model",
                        }
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            store = AgentProfileStore(profiles_path, AGENTS)
            profiles = store.get_all()
            self.assertIn("codex-orchestrator", profiles)
            self.assertIn("codex-module-prototype-designer", profiles)
            self.assertIn("codex-qa", profiles)
            self.assertNotIn("codex-architecture-reviewer", profiles)
            self.assertNotIn("codex-planner", profiles)
            self.assertNotIn("codex-reviewer", profiles)
            self.assertNotIn("codex-challenger", profiles)
            self.assertNotIn("codex-researcher", profiles)
            self.assertNotIn("codex-prototyper", profiles)
            self.assertNotIn("codex-spike-prototyper", profiles)
            self.assertNotIn("claude-researcher", profiles)
            self.assertNotIn("claude-challenger", profiles)
            self.assertNotIn("gemini-researcher", profiles)
            self.assertNotIn("gemini-challenger", profiles)
            self.assertNotIn("gemini-prototyper", profiles)
            self.assertNotIn("grok-prototyper", profiles)
            self.assertEqual(profiles["codex-orchestrator"]["role"], "Orchestrator")
            self.assertNotIn("codex-dispatcher", profiles)
            self.assertEqual(profiles["codex-qa"]["base"], "codex")
            self.assertEqual(profiles["codex-qa"]["role"], "QA Engineer")
            self.assertEqual(profiles["codex-qa"]["model"], "Codex GPT-5.5")
            self.assertEqual(profiles["codex-qa"]["launch_model"], "gpt-5.5")
            self.assertEqual(profiles["codex-qa"]["thinking_effort"], "high")
            self.assertIn("browser qa", profiles["codex-qa"]["trigger_tags"])
            self.assertIn("regression test", profiles["codex-qa"]["trigger_tags"])
            self.assertEqual(profiles["codex-module-prototype-designer"]["base"], "codex")
            self.assertEqual(profiles["codex-module-prototype-designer"]["model"], "Codex GPT-5.5")
            self.assertEqual(profiles["codex-module-prototype-designer"]["thinking_effort"], "high")
            self.assertEqual(profiles["codex-module-prototype-designer"]["role"], "Module Prototype Designer")
            self.assertIn("UI原型", profiles["codex-module-prototype-designer"]["trigger_tags"])
            self.assertIn("prototype", profiles["codex-module-prototype-designer"]["trigger_tags"])
            self.assertEqual(profiles["claude-reviewer"]["role"], "My Custom Reviewer")
            self.assertEqual(profiles["claude-reviewer"]["model"], "Custom Claude Model")
            self.assertNotIn("launch_model", profiles["claude-reviewer"])
            self.assertIn("trigger_tags", profiles["claude-reviewer"])
            self.assertIn("auto-approval mode", profiles["claude-reviewer"]["runtime_policy"])

    def test_default_team_fills_existing_minimal_codex_qa_metadata(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profiles_path = data_dir / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "codex-qa": {
                            "base": "codex",
                            "name": "codex-qa",
                            "label": "Codex QA",
                            "role": "QA Engineer",
                        }
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            profile = AgentProfileStore(profiles_path, AGENTS).get("codex-qa")
            self.assertEqual(profile["model"], "Codex GPT-5.5")
            self.assertEqual(profile["thinking_effort"], "high")
            self.assertIn("verify fix", profile["trigger_tags"])

    def test_default_team_profiles_all_declare_model_and_thinking_effort(self):
        from team_config import DEFAULT_TEAM_PROFILES

        for profile_id, profile in DEFAULT_TEAM_PROFILES.items():
            with self.subTest(profile=profile_id):
                self.assertTrue(profile.get("model"))
                self.assertTrue(profile.get("thinking_effort"))
                self.assertTrue(profile.get("launch_model"))
                self.assertTrue(profile.get("launch_effort"))

    def test_default_team_thinking_effort_policy(self):
        from team_config import DEFAULT_TEAM_PROFILES

        codex_xhigh = {
            "codex-architect",
            "codex-builder",
        }
        for profile_id in codex_xhigh:
            with self.subTest(profile=profile_id):
                self.assertEqual(DEFAULT_TEAM_PROFILES[profile_id]["thinking_effort"], "xhigh")

        for profile_id, profile in DEFAULT_TEAM_PROFILES.items():
            if profile["model"] == "Claude Code Opus 4.7":
                with self.subTest(profile=profile_id):
                    self.assertEqual(profile["thinking_effort"], "max")
                    self.assertEqual(profile["launch_model"], "claude-opus-4-7[1m]")
                    self.assertEqual(profile["launch_effort"], "max")

        self.assertEqual(DEFAULT_TEAM_PROFILES["codex-orchestrator"]["thinking_effort"], "high")
        self.assertEqual(DEFAULT_TEAM_PROFILES["codex-qa"]["thinking_effort"], "high")
        self.assertEqual(DEFAULT_TEAM_PROFILES["codex-module-prototype-designer"]["thinking_effort"], "high")

    def test_default_team_updates_existing_thinking_effort_policy(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profiles_path = data_dir / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "codex-planner": {
                            "base": "codex",
                            "name": "codex-planner",
                            "label": "Codex Planner",
                            "role": "Planner",
                            "model": "Codex GPT-5.5",
                            "thinking_effort": "high",
                        },
                        "codex-qa": {
                            "base": "codex",
                            "name": "codex-qa",
                            "label": "Codex QA",
                            "role": "QA Engineer",
                            "model": "Codex GPT-5.5",
                            "thinking_effort": "low",
                        },
                        "codex-dispatcher": {
                            "base": "codex",
                            "name": "codex-dispatcher",
                            "label": "Codex Dispatcher",
                            "role": "Dispatcher",
                            "model": "Codex GPT-5.5",
                            "thinking_effort": "low",
                        },
                        "claude-reviewer": {
                            "base": "claude",
                            "name": "claude-reviewer",
                            "label": "Claude Reviewer",
                            "role": "Reviewer",
                            "model": "Claude Code Opus 4.7",
                            "thinking_effort": "high",
                        },
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            profiles = AgentProfileStore(profiles_path, AGENTS).get_all()
            self.assertNotIn("codex-planner", profiles)
            self.assertNotIn("codex-dispatcher", profiles)
            self.assertEqual(profiles["codex-orchestrator"]["thinking_effort"], "high")
            self.assertEqual(profiles["codex-qa"]["thinking_effort"], "high")
            self.assertEqual(profiles["codex-module-prototype-designer"]["thinking_effort"], "high")
            self.assertEqual(profiles["claude-reviewer"]["thinking_effort"], "max")
            self.assertEqual(profiles["claude-reviewer"]["launch_model"], "claude-opus-4-7[1m]")
            self.assertEqual(profiles["claude-reviewer"]["launch_effort"], "max")

    def test_default_team_removes_retired_profiles(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profiles_path = data_dir / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "gemini-researcher": {
                            "base": "gemini",
                            "name": "gemini-researcher",
                            "label": "Gemini Researcher",
                            "role": "Researcher",
                            "model": "Gemini 3.5 Flash",
                        },
                        "gemini-challenger": {
                            "base": "antigravity",
                            "name": "gemini-challenger",
                            "label": "Gemini Challenger",
                            "role": "Red Team",
                            "model": "Gemini 3.5 Flash (High)",
                        },
                        "gemini-prototyper": {
                            "base": "antigravity",
                            "name": "gemini-prototyper",
                            "label": "Gemini Prototyper",
                            "role": "Prototyper",
                            "model": "Gemini 3.5 Flash (High)",
                        },
                        "grok-prototyper": {
                            "base": "grok",
                            "name": "grok-prototyper",
                            "label": "Grok Prototyper",
                            "role": "Prototyper",
                            "model": "Grok Build",
                        },
                        "codex-researcher": {
                            "base": "codex",
                            "name": "codex-researcher",
                            "label": "Codex Researcher",
                            "role": "Researcher",
                        },
                        "codex-planner": {
                            "base": "codex",
                            "name": "codex-planner",
                            "label": "Codex Planner",
                            "role": "Planner",
                        },
                        "codex-reviewer": {
                            "base": "codex",
                            "name": "codex-reviewer",
                            "label": "Codex Reviewer",
                            "role": "Reviewer",
                        },
                        "codex-challenger": {
                            "base": "codex",
                            "name": "codex-challenger",
                            "label": "Codex Challenger",
                            "role": "Engineering Challenger",
                        },
                        "codex-prototyper": {
                            "base": "codex",
                            "name": "codex-prototyper",
                            "label": "Codex Prototyper",
                            "role": "Prototyper",
                        },
                        "claude-researcher": {
                            "base": "claude",
                            "name": "claude-researcher",
                            "label": "Claude Researcher",
                            "role": "Researcher",
                        },
                        "claude-challenger": {
                            "base": "claude",
                            "name": "claude-challenger",
                            "label": "Claude Challenger",
                            "role": "Red Team",
                        },
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            store = AgentProfileStore(profiles_path, AGENTS)
            profiles = store.get_all()
            self.assertNotIn("gemini-researcher", profiles)
            self.assertNotIn("gemini-challenger", profiles)
            self.assertNotIn("gemini-prototyper", profiles)
            self.assertNotIn("grok-prototyper", profiles)
            self.assertNotIn("codex-researcher", profiles)
            self.assertNotIn("codex-planner", profiles)
            self.assertNotIn("codex-reviewer", profiles)
            self.assertNotIn("codex-challenger", profiles)
            self.assertNotIn("codex-prototyper", profiles)
            self.assertNotIn("claude-researcher", profiles)
            self.assertNotIn("claude-challenger", profiles)
            self.assertEqual(profiles["codex-qa"]["model"], "Codex GPT-5.5")
            self.assertEqual(profiles["codex-module-prototype-designer"]["model"], "Codex GPT-5.5")
            self.assertNotIn("codex-spike-prototyper", profiles)

    def test_default_team_syncs_roles_file_when_retired_profiles_are_replaced(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "roles.json").write_text(
                json.dumps(
                    {
                        "gemini-researcher": "Researcher",
                        "gemini-challenger": "Red Team",
                        "gemini-prototyper": "Prototyper",
                        "grok-prototyper": "Builder",
                        "codex-researcher": "Researcher",
                        "codex-planner": "Planner",
                        "codex-reviewer": "Reviewer",
                        "codex-challenger": "Engineering Challenger",
                        "codex-prototyper": "Prototyper",
                        "claude-researcher": "Researcher",
                        "claude-challenger": "Red Team",
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            roles = json.loads((data_dir / "roles.json").read_text("utf-8"))
            self.assertNotIn("gemini-researcher", roles)
            self.assertNotIn("gemini-challenger", roles)
            self.assertNotIn("gemini-prototyper", roles)
            self.assertNotIn("grok-prototyper", roles)
            self.assertNotIn("codex-researcher", roles)
            self.assertNotIn("codex-planner", roles)
            self.assertNotIn("codex-reviewer", roles)
            self.assertNotIn("codex-challenger", roles)
            self.assertNotIn("codex-prototyper", roles)
            self.assertNotIn("claude-researcher", roles)
            self.assertNotIn("claude-challenger", roles)
            self.assertEqual(roles["codex-qa"], "QA Engineer")
            self.assertEqual(roles["codex-module-prototype-designer"], "Module Prototype Designer")
            self.assertNotIn("codex-spike-prototyper", roles)

    def test_default_team_migrates_legacy_orchestrator_and_architect_metadata(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profiles_path = data_dir / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "codex-architect": {
                            "base": "codex",
                            "name": "codex-architect",
                            "label": "Codex Architect",
                            "role": "Architect",
                            "trigger_tags": [
                                "architecture",
                                "architect",
                                "schema",
                                "database",
                                "migration",
                                "api",
                                "data flow",
                                "架構",
                                "資料流",
                            ],
                        },
                        "codex-dispatcher": {
                            "base": "codex",
                            "name": "codex-dispatcher",
                            "label": "Codex Dispatcher",
                            "role": "Dispatcher",
                            "specialty": "Classify incoming tasks and dispatch the smallest useful set of agents by role and specialty.",
                            "responsibilities": [
                                "Choose agents when the user did not mention anyone explicitly.",
                                "Keep the team small and explain the handoff when needed.",
                            ],
                            "avoid": ["Do not implement directly unless no better specialist is available."],
                            "output_contract": "Name the selected agents and why, then hand off or summarise.",
                        },
                        "claude-researcher": {
                            "base": "claude",
                            "name": "claude-researcher",
                            "label": "Claude Researcher",
                            "role": "Researcher",
                            "model": "Claude Code Sonnet 4.6",
                            "specialty": "Scan large context, compare documents, find contradictions, and summarise evidence quickly.",
                            "trigger_tags": [
                                "research",
                                "docs",
                                "compare",
                                "scan",
                                "summarize",
                                "context",
                                "legacy",
                                "文件",
                                "整理",
                                "研究",
                                "大量",
                            ],
                            "responsibilities": [
                                "Collect evidence and cite where it came from.",
                                "Surface missing context before planning.",
                            ],
                        },
                        "codex-challenger": {
                            "base": "codex",
                            "name": "codex-challenger",
                            "label": "Codex Challenger",
                            "role": "Engineering Challenger",
                            "avoid": ["Do not duplicate Gemini Challenger's broad spec/context challenge."],
                        },
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            profiles = AgentProfileStore(profiles_path, AGENTS).get_all()
            self.assertNotIn("codex-dispatcher", profiles)
            self.assertIn("parallel-dispatch", profiles["codex-orchestrator"]["specialty"])
            self.assertIn("track progress", profiles["codex-orchestrator"]["output_contract"])
            self.assertIn(
                "Use commander controls to keep active workers from waking each other into loops.",
                profiles["codex-orchestrator"]["responsibilities"],
            )
            self.assertNotIn("api", profiles["codex-architect"]["trigger_tags"])
            self.assertIn("api design", profiles["codex-architect"]["trigger_tags"])
            self.assertNotIn("claude-researcher", profiles)
            self.assertNotIn("codex-challenger", profiles)
            self.assertEqual(profiles["codex-qa"]["role"], "QA Engineer")


if __name__ == "__main__":
    unittest.main()
