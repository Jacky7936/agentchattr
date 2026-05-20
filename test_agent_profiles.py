import json
import tempfile
import unittest
from pathlib import Path

from agent_profiles import AgentProfileStore
from registry import RuntimeRegistry


AGENTS = {
    "codex": {"label": "Codex", "color": "#10a37f"},
    "claude": {"label": "Claude", "color": "#da7756"},
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


if __name__ == "__main__":
    unittest.main()
