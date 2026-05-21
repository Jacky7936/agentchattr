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
            self.assertIn("codex-dispatcher", profiles)
            self.assertIn("gemini-researcher", profiles)
            self.assertIn("gemini-challenger", profiles)
            self.assertIn("codex-architecture-reviewer", profiles)
            self.assertIn("codex-challenger", profiles)
            self.assertIn("gemini-prototyper", profiles)
            self.assertIn("grok-prototyper", profiles)
            self.assertEqual(profiles["codex-dispatcher"]["role"], "Dispatcher")
            self.assertEqual(profiles["codex-architecture-reviewer"]["base"], "codex")
            self.assertEqual(profiles["codex-architecture-reviewer"]["role"], "Architecture Reviewer")
            self.assertEqual(profiles["codex-challenger"]["role"], "Engineering Challenger")
            self.assertEqual(profiles["gemini-researcher"]["base"], "antigravity")
            self.assertEqual(profiles["gemini-researcher"]["model"], "Gemini 3.5 Flash (High)")
            self.assertIn("法規", profiles["gemini-researcher"]["trigger_tags"])
            self.assertIn("api docs", profiles["gemini-researcher"]["trigger_tags"])
            self.assertIn("official docs", profiles["gemini-researcher"]["trigger_tags"])
            self.assertEqual(profiles["gemini-challenger"]["base"], "antigravity")
            self.assertEqual(profiles["gemini-challenger"]["model"], "Gemini 3.5 Flash (High)")
            self.assertEqual(profiles["gemini-prototyper"]["base"], "antigravity")
            self.assertEqual(profiles["gemini-prototyper"]["model"], "Gemini 3.5 Flash (High)")
            self.assertEqual(profiles["gemini-prototyper"]["role"], "Prototyper")
            self.assertEqual(profiles["grok-prototyper"]["role"], "Prototyper")
            self.assertEqual(profiles["claude-reviewer"]["role"], "My Custom Reviewer")
            self.assertEqual(profiles["claude-reviewer"]["model"], "Custom Claude Model")
            self.assertIn("trigger_tags", profiles["claude-reviewer"])
            self.assertIn("auto-approval mode", profiles["claude-reviewer"]["runtime_policy"])

    def test_default_team_migrates_gemini_profiles_to_antigravity_runtime(self):
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
                        }
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            store = AgentProfileStore(profiles_path, AGENTS)
            profile = store.get("gemini-researcher")
            self.assertEqual(profile["base"], "antigravity")
            self.assertEqual(profile["model"], "Gemini 3.5 Flash (High)")

    def test_default_team_preserves_custom_gemini_model_when_migrating_runtime(self):
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
                            "model": "Custom Gemini Model",
                        }
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            store = AgentProfileStore(profiles_path, AGENTS)
            profile = store.get("gemini-researcher")
            self.assertEqual(profile["base"], "antigravity")
            self.assertEqual(profile["model"], "Custom Gemini Model")

    def test_default_team_migrates_grok_prototyper_legacy_builder_role(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profiles_path = data_dir / "agent_profiles.json"
            profiles_path.write_text(
                json.dumps(
                    {
                        "grok-prototyper": {
                            "base": "grok",
                            "name": "grok-prototyper",
                            "label": "Grok Prototyper",
                            "role": "Builder",
                            "model": "Grok Build",
                        }
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            store = AgentProfileStore(profiles_path, AGENTS)
            profile = store.get("grok-prototyper")
            self.assertEqual(profile["role"], "Prototyper")

    def test_default_team_syncs_roles_file_when_legacy_role_is_migrated(self):
        from team_config import apply_default_team_profiles

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "roles.json").write_text(
                json.dumps({"grok-prototyper": "Builder"}),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            roles = json.loads((data_dir / "roles.json").read_text("utf-8"))
            self.assertEqual(roles["grok-prototyper"], "Prototyper")

    def test_default_team_migrates_legacy_research_and_architect_metadata(self):
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
                        "gemini-researcher": {
                            "base": "antigravity",
                            "name": "gemini-researcher",
                            "label": "Gemini Researcher",
                            "role": "Researcher",
                            "model": "Gemini 3.5 Flash (High)",
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
                    }
                ),
                "utf-8",
            )

            apply_default_team_profiles(data_dir, AGENTS)

            profiles = AgentProfileStore(profiles_path, AGENTS).get_all()
            self.assertNotIn("api", profiles["codex-architect"]["trigger_tags"])
            self.assertIn("api design", profiles["codex-architect"]["trigger_tags"])
            self.assertIn("web/current sources", profiles["gemini-researcher"]["specialty"])
            self.assertIn("official docs", profiles["gemini-researcher"]["trigger_tags"])
            self.assertIn("法規", profiles["gemini-researcher"]["trigger_tags"])
            self.assertIn(
                "Prefer primary sources such as official API docs, laws, standards, and release notes.",
                profiles["gemini-researcher"]["responsibilities"],
            )


if __name__ == "__main__":
    unittest.main()
