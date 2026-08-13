import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT_DIR / "integrations" / "codex_titan_plugin" / "scripts" / "titan_mcp_launcher.py"


def _load_launcher_module():
    spec = importlib.util.spec_from_file_location("titan_mcp_launcher", LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher_module()


class ExecCalled(Exception):
    def __init__(self, file, argv, env):
        self.file = file
        self.argv = list(argv)
        self.env = dict(env)


class CodexMcpLauncherTests(unittest.TestCase):
    def test_uses_valid_local_titan_cli_and_preserves_stdio_with_exec(self):
        calls = []

        def which(command):
            return "/usr/local/bin/titan" if command == "titan" else None

        def run_fn(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="usage: titan mcp", stderr="")

        def exec_fn(file, argv, env):
            raise ExecCalled(file, argv, env)

        with self.assertRaises(ExecCalled) as raised:
            launcher.run(
                ["--agent", "codex"],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                base_env={"PATH": "/usr/local/bin"},
                which_fn=which,
                run_fn=run_fn,
                exec_fn=exec_fn,
            )

        self.assertEqual(calls[0][0], ["/usr/local/bin/titan", "mcp", "--help"])
        self.assertEqual(raised.exception.file, "/usr/local/bin/titan")
        self.assertEqual(raised.exception.argv, ["/usr/local/bin/titan", "mcp", "--agent", "codex"])
        self.assertEqual(raised.exception.env["TITAN_AGENT_NAME"], "codex")
        self.assertTrue(raised.exception.env["TITAN_HOME"].endswith("/.titan/agents/codex"))
        self.assertEqual(raised.exception.env["TITAN_BASE_DIR"], raised.exception.env["TITAN_HOME"])

    def test_detects_missing_cli_without_package_fallback(self):
        def which(_command):
            return None

        def run_fn(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not found")

        stderr = io.StringIO()
        result = launcher.run(
            ["--agent", "codex", "--no-package-fallback"],
            stdout=io.StringIO(),
            stderr=stderr,
            base_env={"PATH": ""},
            which_fn=which,
            run_fn=run_fn,
            exec_fn=lambda *_args: None,
        )

        self.assertEqual(result, 127)
        rendered = stderr.getvalue()
        self.assertIn("could not start", rendered)
        self.assertIn("local Titan CLI was not found", rendered)
        self.assertIn("npm install -g titan-memory-cli", rendered)

    def test_detects_stale_cli_or_import_error_and_redacts_errors(self):
        def which(command):
            return "/tmp/titan" if command == "titan" else None

        def run_fn(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv,
                1,
                stdout="",
                stderr="Traceback: ImportError OPENAI_API_KEY=sk-thisshouldnotbevisible123456789",
            )

        stderr = io.StringIO()
        result = launcher.run(
            ["--agent", "codex", "--no-package-fallback"],
            stdout=io.StringIO(),
            stderr=stderr,
            base_env={"PATH": "/tmp", "OPENAI_API_KEY": "sk-thisshouldnotbevisible123456789"},
            which_fn=which,
            run_fn=run_fn,
            exec_fn=lambda *_args: None,
        )

        rendered = stderr.getvalue()
        self.assertEqual(result, 127)
        self.assertIn("local Titan CLI is not healthy", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotIn("thisshouldnotbevisible", rendered)

    def test_falls_back_to_npx_when_local_titan_is_missing(self):
        def which(command):
            if command == "npx":
                return "/usr/bin/npx"
            return None

        def run_fn(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="missing")

        def exec_fn(file, argv, env):
            raise ExecCalled(file, argv, env)

        with self.assertRaises(ExecCalled) as raised:
            launcher.run(
                ["--agent", "codex"],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
                base_env={"PATH": "/usr/bin"},
                which_fn=which,
                run_fn=run_fn,
                exec_fn=exec_fn,
            )

        self.assertEqual(raised.exception.file, "/usr/bin/npx")
        self.assertEqual(
            raised.exception.argv,
            ["/usr/bin/npx", "-y", "titan-memory-cli", "mcp", "--agent", "codex"],
        )
        self.assertEqual(raised.exception.env["TITAN_AGENT_NAME"], "codex")

    def test_build_runtime_env_respects_custom_agent(self):
        env = launcher.build_runtime_env("codex", {"TITAN_HOME": "/tmp/custom-titan"})
        self.assertEqual(env["TITAN_AGENT_NAME"], "codex")
        self.assertEqual(env["TITAN_HOME"], "/tmp/custom-titan")
        self.assertEqual(env["TITAN_BASE_DIR"], "/tmp/custom-titan")


if __name__ == "__main__":
    unittest.main()
