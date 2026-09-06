import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "integrations" / "claude_titan_plugin" / "scripts" / "titan_claude_hook.js"
RUNTIME_VERSION = json.loads(
    (ROOT / "integrations" / "claude_titan_plugin" / "package.json").read_text(encoding="utf-8")
)["dependencies"]["titan-memory-cli"]


def _isolated_launcher_env(root: Path) -> dict[str, str]:
    plugin_data = root / "claude-plugin-data"
    runtime = root / "runtime"
    return {
        **os.environ,
        "TITAN_HOME": str(root / "titan-home"),
        "TITAN_BASE_DIR": str(root / "base"),
        "TITAN_RUNTIME_HOME": str(runtime),
        "TITAN_RUNTIME_DIR": str(runtime),
        "TITAN_RUNTIME_MANIFEST": str(runtime / "current.json"),
        "TITAN_AGENT_NAME": "claude-code",
        "CLAUDE_PLUGIN_DATA": str(plugin_data),
        "TITAN_CLAUDE_DATA": str(plugin_data),
        "TITAN_CLAUDE_HOOK_BOOTSTRAP_WAIT_MS": "0",
    }


def _seed_forwarding_runtime(root: Path, env: dict[str, str]) -> tuple[Path, Path]:
    """Configure a temporary plugin and Python hook that records launcher stdin."""
    capture_path = root / "hook-input.json"
    plugin_root = root / "plugin"
    scripts_root = plugin_root / "scripts"
    scripts_root.mkdir(parents=True)
    source_scripts = ROOT / "integrations" / "claude_titan_plugin" / "scripts"
    for script_name in ("titan_claude_hook.js", "titan_claude_runtime.js"):
        shutil.copyfile(source_scripts / script_name, scripts_root / script_name)
    shutil.copyfile(
        ROOT / "integrations" / "claude_titan_plugin" / "package.json",
        plugin_root / "package.json",
    )
    (scripts_root / "titan_claude_hook.py").write_text(
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "Path(os.environ[\"TITAN_TEST_HOOK_CAPTURE\"]).write_text(\n"
        "    sys.stdin.read(), encoding=\"utf-8\"\n"
        ")\n",
        encoding="utf-8",
    )

    runtime_root = root / "runtime-root"
    runtime_root.mkdir()
    plugin_data = Path(env["CLAUDE_PLUGIN_DATA"])
    manifest = plugin_data / "managed-runtime" / "current.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "package": "titan-memory-cli",
                "version": RUNTIME_VERSION,
                "python": str(Path(sys.executable).resolve()),
                "runtime_root": str(runtime_root),
            }
        ),
        encoding="utf-8",
    )
    env["TITAN_TEST_HOOK_CAPTURE"] = str(capture_path)
    return capture_path, scripts_root / "titan_claude_hook.js"


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class ClaudeHookLauncherTests(unittest.TestCase):
    def test_default_messages_mode_skips_tool_process_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _isolated_launcher_env(root)
            env["TITAN_SPOOL_DIR"] = str(root / "spool")
            result = subprocess.run(
                ["node", str(LAUNCHER)],
                input=json.dumps({
                    "hook_event_name": "PostToolUse",
                    "session_id": "s1",
                    "tool_name": "Read",
                    "tool_response": "contents",
                }),
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "claude-plugin-data" / "managed-runtime").exists())
            self.assertFalse((root / "spool").exists())

    def test_full_mode_forwards_buffered_payload_to_python_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _isolated_launcher_env(root)
            capture_path, launcher = _seed_forwarding_runtime(root, env)
            env.update(
                {
                    "TITAN_SPOOL_DIR": str(root / "spool"),
                    "CLAUDE_PLUGIN_OPTION_capture_mode": "full",
                    "CLAUDE_PLUGIN_OPTION_capture_tool_io": "true",
                }
            )
            payload = {
                "hook_event_name": "PostToolUse",
                "session_id": "s1",
                "tool_name": "Read",
                "tool_use_id": "call-1",
                "tool_input": {"file_path": "README.md"},
                "tool_response": "contents",
            }
            result = subprocess.run(
                ["node", str(launcher)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(capture_path.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
