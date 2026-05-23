import tempfile
import unittest
import os
import json
from pathlib import Path
from unittest.mock import patch

from wrapper import _build_provider_launch, _format_profile_context
from mcp_bridge import _MCP_INSTRUCTIONS


class WrapperLaunchArgsTest(unittest.TestCase):
    def _codex_launch_args(self, extra_args, profile=None):
        with tempfile.TemporaryDirectory() as tmp:
            launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                agent="codex",
                agent_cfg={},
                instance_name="codex-builder",
                data_dir=Path(tmp),
                proxy_url="http://127.0.0.1:55993/mcp",
                extra_args=extra_args,
                env={},
                profile=profile,
            )

        self.assertEqual(launch_env, {})
        self.assertEqual(inject_env, {})
        self.assertIsNone(settings_path)
        return launch_args

    def _claude_launch_args(self, extra_args, profile=None):
        with tempfile.TemporaryDirectory() as tmp:
            launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                agent="claude",
                agent_cfg={},
                instance_name="claude-reviewer",
                data_dir=Path(tmp),
                proxy_url=None,
                extra_args=extra_args,
                env={},
                token="test-token",
                mcp_cfg={"http_port": 8123},
                profile=profile,
            )

        self.assertEqual(launch_env, {})
        self.assertEqual(settings_path.name, "claude-reviewer-mcp.json")
        return launch_args, inject_env

    def _cursor_launch_args(self, extra_args, profile=None, agent_cfg=None):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            cfg = agent_cfg or {
                "mcp_inject": "settings_file",
                "mcp_settings_path": "~/.cursor/mcp.json",
                "mcp_transport": "http",
                "mcp_http_key": "url",
            }
            with patch.dict(os.environ, {"HOME": str(home)}):
                launch_args, launch_env, inject_env, settings_path = _build_provider_launch(
                    agent="cursor",
                    agent_cfg=cfg,
                    instance_name="cursor-builder",
                    data_dir=Path(tmp) / "data",
                    proxy_url=None,
                    extra_args=extra_args,
                    env={},
                    token="test-token",
                    mcp_cfg={"http_port": 8123},
                    project_dir=Path(tmp) / "project",
                    profile=profile,
                )
                settings_data = json.loads(settings_path.read_text("utf-8")) if settings_path else {}

        self.assertEqual(launch_env, {})
        self.assertEqual(inject_env, {})
        return launch_args, settings_path, settings_data

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

    def test_codex_profile_model_and_effort_are_launch_args(self):
        args = self._codex_launch_args(
            ["--dangerously-bypass-approvals-and-sandbox"],
            profile={
                "model": "Codex GPT-5.5",
                "thinking_effort": "xhigh",
            },
        )

        self.assertIn("-m", args)
        self.assertEqual(args[args.index("-m") + 1], "gpt-5.5")
        self.assertIn("-c", args)
        self.assertIn('model_reasoning_effort="xhigh"', args)
        self.assertLess(
            args.index('model_reasoning_effort="xhigh"'),
            args.index("--dangerously-bypass-approvals-and-sandbox"),
        )

    def test_codex_profile_does_not_override_explicit_forwarded_model_args(self):
        args = self._codex_launch_args(
            ["--model", "gpt-5.4", "-c", 'model_reasoning_effort="low"'],
            profile={
                "model": "Codex GPT-5.5",
                "thinking_effort": "xhigh",
            },
        )

        self.assertEqual(args.count("--model"), 1)
        self.assertIn("gpt-5.4", args)
        self.assertNotIn("gpt-5.5", args)
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertNotIn('model_reasoning_effort="xhigh"', args)

    def test_claude_profile_model_and_effort_are_launch_args(self):
        args, inject_env = self._claude_launch_args(
            ["--permission-mode", "auto"],
            profile={
                "model": "Claude Code Opus 4.7",
                "thinking_effort": "max",
            },
        )

        self.assertEqual(inject_env, {})
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-4-7[1m]")
        self.assertIn("--effort", args)
        self.assertEqual(args[args.index("--effort") + 1], "max")
        self.assertLess(args.index("--effort"), args.index("--permission-mode"))

    def test_claude_launch_effort_overrides_prompt_effort_metadata(self):
        args, _ = self._claude_launch_args(
            ["--permission-mode", "auto"],
            profile={
                "model": "Claude Code Opus 4.7",
                "thinking_effort": "high",
                "launch_effort": "max",
            },
        )

        self.assertEqual(args[args.index("--effort") + 1], "max")

    def test_cursor_profile_model_is_launch_arg(self):
        args, _, _ = self._cursor_launch_args(
            ["--yolo", "--sandbox", "disabled", "--approve-mcps"],
            profile={
                "model": "Cursor Composer 2.5 Fast",
            },
        )

        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "composer-2.5-fast")
        self.assertLess(args.index("--model"), args.index("--yolo"))
        self.assertIn("--sandbox", args)
        self.assertIn("disabled", args)
        self.assertIn("--approve-mcps", args)

    def test_cursor_profile_does_not_override_explicit_forwarded_model_args(self):
        args, _, _ = self._cursor_launch_args(
            ["--model", "composer-2.5", "--yolo"],
            profile={
                "model": "Cursor Composer 2.5 Fast",
            },
        )

        self.assertEqual(args.count("--model"), 1)
        self.assertEqual(args[args.index("--model") + 1], "composer-2.5")

    def test_cursor_writes_global_mcp_config_with_standard_url_key(self):
        args, settings_path, data = self._cursor_launch_args(
            ["--model", "composer-2.5-fast", "--yolo"],
            profile={},
        )

        self.assertEqual(args, ["--model", "composer-2.5-fast", "--yolo"])
        self.assertIsNotNone(settings_path)
        self.assertEqual(
            data["mcpServers"]["agentchattr"],
            {
                "type": "http",
                "url": "http://127.0.0.1:8123/mcp",
                "trust": True,
                "headers": {"Authorization": "Bearer test-token"},
            },
        )
        self.assertNotIn("httpUrl", data["mcpServers"]["agentchattr"])

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

    def test_mcp_instructions_include_traditional_chinese_response_rule(self):
        self.assertIn("Traditional Chinese", _MCP_INSTRUCTIONS)
        self.assertIn("繁體中文", _MCP_INSTRUCTIONS)

    def test_mcp_instructions_require_structured_lane_state_before_final_reply(self):
        self.assertIn("before the final chat_send reply", _MCP_INSTRUCTIONS)
        self.assertIn("chat_update_lane_item", _MCP_INSTRUCTIONS)
        self.assertIn("approved / needs_fix / ready_for_review / blocked", _MCP_INSTRUCTIONS)

    def test_mcp_instructions_describe_task_complete_summary(self):
        self.assertIn("TASK COMPLETE", _MCP_INSTRUCTIONS)
        self.assertIn("final chat_send summary", _MCP_INSTRUCTIONS)

    def test_mcp_instructions_require_ui_screenshot_handoff(self):
        self.assertIn("UI-facing", _MCP_INSTRUCTIONS)
        self.assertIn("chat_send(image_path=", _MCP_INSTRUCTIONS)
        self.assertIn("reviewer", _MCP_INSTRUCTIONS)

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

    def test_profile_context_includes_response_language_rule(self):
        formatted = _format_profile_context(
            {
                "role": "Builder",
                "response_language": "Always reply in Traditional Chinese (繁體中文).",
            }
        )

        self.assertIn("ROLE: Builder", formatted)
        self.assertIn("RESPONSE LANGUAGE: Always reply in Traditional Chinese (繁體中文).", formatted)

    def test_profile_context_includes_lane_state_contract(self):
        formatted = _format_profile_context(
            {
                "role": "Reviewer",
                "lane_state_contract": "Before final lane replies, write structured state.",
            }
        )

        self.assertIn("LANE STATE CONTRACT: Before final lane replies, write structured state.", formatted)

    def test_profile_context_includes_ui_visual_handoff_contract(self):
        formatted = _format_profile_context(
            {
                "role": "Builder",
                "ui_visual_handoff_contract": "For UI-facing work, attach a screenshot with chat_send(image_path=...).",
            }
        )

        self.assertIn(
            "UI VISUAL HANDOFF CONTRACT: For UI-facing work, attach a screenshot with chat_send(image_path=...).",
            formatted,
        )

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

    def test_profile_context_includes_orchestrator_roster_and_routing(self):
        formatted = _format_profile_context(
            {
                "role": "Orchestrator",
                "team_roster": ["@codex-planner: fuzzy scope and staged plans."],
                "routing_guidelines": ["Bug or regression: ask @codex-qa first."],
            }
        )

        self.assertIn("TEAM ROSTER: @codex-planner: fuzzy scope and staged plans.", formatted)
        self.assertIn("ROUTING GUIDELINES: Bug or regression: ask @codex-qa first.", formatted)


if __name__ == "__main__":
    unittest.main()
