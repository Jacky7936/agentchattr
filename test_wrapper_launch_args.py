import tempfile
import unittest
import os
import json
from pathlib import Path
from unittest.mock import patch

from wrapper import _build_provider_launch, _format_profile_context
from mcp_bridge import _MCP_INSTRUCTIONS


class WrapperLaunchArgsTest(unittest.TestCase):
    def _codex_launch_args(self, extra_args):
        with tempfile.TemporaryDirectory() as tmp:
            launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                agent="codex",
                agent_cfg={},
                instance_name="codex-builder",
                data_dir=Path(tmp),
                proxy_url="http://127.0.0.1:55993/mcp",
                extra_args=extra_args,
                env={},
            )

        self.assertEqual(launch_env, {})
        self.assertEqual(inject_env, {})
        self.assertIsNone(settings_path)
        return launch_args

    def test_codex_bypass_flag_is_forwarded_without_delimiter(self):
        args = self._codex_launch_args(["--dangerously-bypass-approvals-and-sandbox"])

        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("--", args)

    def test_codex_bypass_flag_strips_wrapper_delimiter(self):
        args = self._codex_launch_args(["--", "--dangerously-bypass-approvals-and-sandbox"])

        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("--", args)

    def test_codex_mcp_proxy_args_still_precede_forwarded_args(self):
        args = self._codex_launch_args(["--dangerously-bypass-approvals-and-sandbox"])

        self.assertEqual(args[0], "-c")
        self.assertEqual(args[1], 'mcp_servers.agentchattr.url="http://127.0.0.1:55993/mcp"')
        self.assertEqual(args[2], "--dangerously-bypass-approvals-and-sandbox")

    def test_antigravity_writes_managed_plugin_with_bearer_mcp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            with patch.dict(os.environ, {"HOME": str(home)}):
                launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                    agent="antigravity",
                    agent_cfg={},
                    instance_name="antigravity",
                    data_dir=Path(tmp) / "data",
                    proxy_url=None,
                    extra_args=["--dangerously-skip-permissions"],
                    env={},
                    token="test-token",
                    mcp_cfg={"http_port": 8123},
                    project_dir=Path(tmp),
                )

            plugin_dir = home / ".gemini" / "config" / "plugins" / "agentchattr"
            plugin_json = json.loads((plugin_dir / "plugin.json").read_text("utf-8"))
            mcp_json = json.loads((plugin_dir / "mcp_config.json").read_text("utf-8"))

        self.assertEqual(launch_args, ["--dangerously-skip-permissions"])
        self.assertEqual(launch_env, {})
        self.assertEqual(inject_env, {})
        self.assertEqual(settings_path, plugin_dir / "mcp_config.json")
        self.assertEqual(plugin_json, {"name": "agentchattr"})
        self.assertEqual(
            mcp_json,
            {
                "mcpServers": {
                    "agentchattr": {
                        "serverUrl": "http://127.0.0.1:8123/mcp",
                        "headers": {"Authorization": "Bearer test-token"},
                    }
                }
            },
        )

    def test_grok_writes_managed_config_with_env_expanded_bearer_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            grok_dir = home / ".grok"
            grok_dir.mkdir(parents=True)
            config_file = grok_dir / "config.toml"
            config_file.write_text(
                '[cli]\ninstaller = "internal"\n\n[ui]\npermission_mode = "always-approve"\n',
                "utf-8",
            )

            with patch.dict(os.environ, {"HOME": str(home)}):
                launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                    agent="grok",
                    agent_cfg={},
                    instance_name="grok",
                    data_dir=Path(tmp) / "data",
                    proxy_url=None,
                    extra_args=["--always-approve"],
                    env={},
                    token="test-token",
                    mcp_cfg={"http_port": 8123},
                    project_dir=Path(tmp),
                )

            written = config_file.read_text("utf-8")

        self.assertEqual(launch_args, ["--always-approve"])
        self.assertEqual(launch_env, {})
        self.assertEqual(inject_env, {"AGENTCHATTR_GROK_TOKEN": "test-token"})
        self.assertEqual(settings_path, config_file)
        self.assertIn('[cli]\ninstaller = "internal"', written)
        self.assertIn('[ui]\npermission_mode = "always-approve"', written)
        self.assertIn("# BEGIN agentchattr managed MCP server", written)
        self.assertIn('[mcp_servers.agentchattr]', written)
        self.assertIn('url = "http://127.0.0.1:8123/mcp"', written)
        self.assertIn('[mcp_servers.agentchattr.headers]', written)
        self.assertIn('Authorization = "Bearer ${AGENTCHATTR_GROK_TOKEN}"', written)
        self.assertNotIn("test-token", written)

    def test_grok_replaces_existing_managed_config_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            grok_dir = home / ".grok"
            grok_dir.mkdir(parents=True)
            config_file = grok_dir / "config.toml"
            config_file.write_text(
                '[ui]\ncompact_mode = false\n\n'
                "# BEGIN agentchattr managed MCP server\n"
                "[mcp_servers.agentchattr]\n"
                'url = "http://127.0.0.1:9999/mcp"\n'
                "# END agentchattr managed MCP server\n",
                "utf-8",
            )

            with patch.dict(os.environ, {"HOME": str(home)}):
                _build_provider_launch(
                    agent="grok",
                    agent_cfg={},
                    instance_name="grok",
                    data_dir=Path(tmp) / "data",
                    proxy_url=None,
                    extra_args=[],
                    env={},
                    token="first-token",
                    mcp_cfg={"http_port": 8123},
                    project_dir=Path(tmp),
                )
                launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                    agent="grok",
                    agent_cfg={},
                    instance_name="grok",
                    data_dir=Path(tmp) / "data",
                    proxy_url=None,
                    extra_args=[],
                    env={},
                    token="second-token",
                    mcp_cfg={"http_port": 8124},
                    project_dir=Path(tmp),
                )

            written = config_file.read_text("utf-8")

        self.assertEqual(launch_args, [])
        self.assertEqual(launch_env, {})
        self.assertEqual(inject_env, {"AGENTCHATTR_GROK_TOKEN": "second-token"})
        self.assertEqual(settings_path, config_file)
        self.assertIn('[ui]\ncompact_mode = false', written)
        self.assertEqual(written.count("# BEGIN agentchattr managed MCP server"), 1)
        self.assertEqual(written.count("# END agentchattr managed MCP server"), 1)
        self.assertIn('url = "http://127.0.0.1:8124/mcp"', written)
        self.assertNotIn("9999", written)
        self.assertNotIn("second-token", written)

    def test_mcp_instructions_include_antigravity_base_identity(self):
        self.assertIn('base: "antigravity"', _MCP_INSTRUCTIONS)

    def test_mcp_instructions_include_grok_base_identity(self):
        self.assertIn('base: "grok"', _MCP_INSTRUCTIONS)

    def test_profile_context_includes_runtime_policy(self):
        formatted = _format_profile_context(
            {
                "role": "Reviewer",
                "model": "Claude Code Opus 4.7",
                "runtime_policy": "Start and operate in non-blocking auto-approval mode.",
            }
        )

        self.assertIn("ROLE: Reviewer", formatted)
        self.assertIn("MODEL: Claude Code Opus 4.7", formatted)
        self.assertIn("RUNTIME POLICY: Start and operate in non-blocking auto-approval mode.", formatted)
        self.assertEqual(formatted.count("RUNTIME POLICY:"), 1)

    def test_profile_context_includes_thinking_effort(self):
        formatted = _format_profile_context(
            {
                "role": "Prototyper",
                "model": "Codex GPT-5.5",
                "thinking_effort": "high",
            }
        )

        self.assertIn("MODEL: Codex GPT-5.5", formatted)
        self.assertIn("THINKING EFFORT: high", formatted)
        self.assertNotIn("RESPONSIBILITIES: None", formatted)
        self.assertNotIn("AVOID: None", formatted)


if __name__ == "__main__":
    unittest.main()
