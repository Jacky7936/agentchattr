import tempfile
import unittest
from pathlib import Path

from wrapper import _build_provider_launch


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


if __name__ == "__main__":
    unittest.main()
