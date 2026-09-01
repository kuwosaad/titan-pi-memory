import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT_DIR / "integrations" / "codex_titan_plugin" / "scripts" / "titan_mcp_launcher.py"
RUNTIME_PATH = ROOT_DIR / "integrations" / "codex_titan_plugin" / "scripts" / "titan_runtime.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load(LAUNCHER_PATH, "titan_mcp_launcher")
runtime = _load(RUNTIME_PATH, "titan_runtime")


class ExecCalled(Exception):
    def __init__(self, file, argv, env):
        self.file, self.argv, self.env = file, list(argv), dict(env)


class CodexMcpLauncherTests(unittest.TestCase):
    def _manifest(self, root):
        runtime_root = root / "versions" / "0.1.3"
        runtime_root.mkdir(parents=True)
        python = runtime_root / "python"
        entrypoint = runtime_root / "tools.py"
        python.write_text("", encoding="utf-8")
        entrypoint.write_text("", encoding="utf-8")
        manifest = root / "current.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "version": "0.1.3",
            "runtime_root": str(runtime_root),
            "python": str(python),
            "entrypoint": str(entrypoint),
        }), encoding="utf-8")
        return manifest, python, entrypoint

    def test_valid_manifest_execs_managed_python_without_lookup_or_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest, python, entrypoint = self._manifest(Path(temp))
            contract = json.loads((ROOT_DIR / "integrations/codex_titan_plugin/.mcp.json").read_text(encoding="utf-8"))["mcpServers"]["titan-memory"]
            self.assertEqual(contract["command"], "python3")
            self.assertEqual(contract["cwd"], ".")
            launcher_command = str((ROOT_DIR / "integrations/codex_titan_plugin" / contract["args"][0]).resolve())
            self.assertEqual(Path(launcher_command).resolve(), LAUNCHER_PATH.resolve())
            raised = None

            def exec_fn(file, argv, env):
                raise ExecCalled(file, argv, env)

            try:
                launcher.run(
                    ["--agent", "codex"],
                    stderr=io.StringIO(),
                    base_env={"TITAN_RUNTIME_MANIFEST": str(manifest), "TITAN_HOME": str(Path(temp) / "agent")},
                    exec_fn=exec_fn,
                )
            except ExecCalled as exc:
                raised = exc
            self.assertIsNotNone(raised)
            self.assertEqual(raised.file, str(python))
            self.assertEqual(raised.argv, [str(python), str(entrypoint), "mcp", "--agent", "codex"])
            self.assertEqual(raised.env["TITAN_AGENT_NAME"], "codex")
            self.assertNotIn("npx", raised.argv)

    def test_missing_manifest_is_actionable_and_does_not_fall_back_to_npx(self):
        stderr = io.StringIO()
        result = launcher.run(
            ["--agent", "codex"],
            stderr=stderr,
            base_env={"TITAN_RUNTIME_MANIFEST": "/tmp/does-not-exist/current.json"},
            exec_fn=lambda *_: None,
        )
        self.assertEqual(result, 127)
        self.assertIn("manifest missing", stderr.getvalue())
        self.assertIn("setup codex", stderr.getvalue())

    def test_stale_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest, python, _ = self._manifest(Path(temp))
            python.unlink()
            with self.assertRaises(RuntimeError):
                runtime.load_manifest(manifest)

    def test_manifest_rejects_entrypoint_outside_runtime_root(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest, _, _ = self._manifest(Path(temp))
            outside = Path(temp) / "outside.py"
            outside.write_text("", encoding="utf-8")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["entrypoint"] = str(outside)
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "inside its runtime directory"):
                runtime.load_manifest(manifest)

    def test_agent_namespace_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            runtime.build_environment("../../outside")

    def test_build_runtime_env_respects_custom_agent(self):
        env = launcher.build_runtime_env("codex", {"TITAN_HOME": "/tmp/custom-titan"})
        self.assertEqual(env["TITAN_AGENT_NAME"], "codex")
        self.assertEqual(env["TITAN_HOME"], "/tmp/custom-titan")
        self.assertEqual(env["TITAN_BASE_DIR"], env["TITAN_HOME"])


if __name__ == "__main__":
    unittest.main()
