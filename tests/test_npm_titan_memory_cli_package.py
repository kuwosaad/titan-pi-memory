import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT_DIR / "packages" / "titan-memory-cli"


class NpmTitanMemoryCliPackageTests(unittest.TestCase):
    def test_mcp_dependency_is_pinned_to_supported_fastmcp_v1_api(self):
        expected = "mcp>=1.5.0,<2"
        self.assertIn(expected, (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn(expected, (PACKAGE_DIR / "runtime" / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn(f'"{expected}"', (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))

        for requirements_path in (ROOT_DIR / "requirements.txt", PACKAGE_DIR / "runtime" / "requirements.txt"):
            requirements = {
                line.strip()
                for line in requirements_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            }
            self.assertNotIn("mcp", requirements)
            self.assertIn(expected, requirements)

    def test_package_exposes_titan_bin_and_prepares_runtime(self):
        payload = json.loads((PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["name"], "titan-memory-cli")
        self.assertEqual(
            payload["bin"],
            {
                "titan": "bin/titan.js",
                "titan-memory-cli": "bin/titan.js",
            },
        )
        self.assertIn("titan-pi-memory", payload["repository"]["url"])
        self.assertIn("titan-pi-memory", payload["homepage"])
        self.assertEqual(payload["scripts"]["prepack"], "node scripts/prepare-runtime.js")
        self.assertIn("runtime/", payload["files"])

        bin_script = (PACKAGE_DIR / "bin" / "titan.js").read_text(encoding="utf-8")
        self.assertTrue(bin_script.startswith("#!/usr/bin/env node"))
        self.assertIn(".titan", bin_script)
        self.assertIn("python", bin_script.lower())
        self.assertIn("tools", bin_script)
        self.assertIn("cli", bin_script)
        self.assertIn("titan.py", bin_script)
        self.assertIn("versions", bin_script)
        self.assertIn("current.json", bin_script)
        self.assertIn("codex-marketplace", bin_script)
        self.assertIn("sys.executable", bin_script)
        self.assertIn("path.resolve(executable)", bin_script)
        self.assertNotIn("if (path.isAbsolute(candidate)) return candidate", bin_script)

    def test_prepare_runtime_copies_required_titan_surfaces(self):
        script = (PACKAGE_DIR / "scripts" / "prepare-runtime.js").read_text(encoding="utf-8")

        for required in ("app", "config", "entrypoints", "integrations", "tools", "requirements.txt"):
            with self.subTest(required=required):
                self.assertIn(required, script)

        self.assertIn("__pycache__", script)
        self.assertIn(".pyc", script)

    def test_runtime_activation_preserves_previous_pointer_and_marketplace_tree(self):
        script = (PACKAGE_DIR / "bin" / "titan.js").read_text(encoding="utf-8")
        self.assertIn("previous.json", script)
        self.assertIn(".agents", script)
        self.assertIn("plugin.json", script)
        self.assertIn("marketplace", script)

    def test_repeated_activation_preserves_previous_pointer(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            runtime_home = root / "runtime"
            current = runtime_home / "current.json"
            current.parent.mkdir(parents=True)
            legacy = {
                "schema_version": 1,
                "package": "titan-memory-cli",
                "version": "0.1.2",
                "runtime_root": str(root / "legacy"),
                "python": str(root / "legacy" / "python"),
                "entrypoint": str(root / "legacy" / "entrypoint.py"),
                "marketplace": str(root / "legacy-marketplace"),
            }
            current.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "HOME": str(root / "home"),
                "CODEX_HOME": str(root / "codex"),
                "TITAN_RUNTIME_HOME": str(runtime_home),
                "TITAN_NPM_NO_VENV": "1",
                "PYTHON": "/opt/miniconda3/bin/python3",
            })
            command = ["node", str(PACKAGE_DIR / "bin" / "titan.js"), "--help"]
            first = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            previous = runtime_home / "previous.json"
            self.assertEqual(json.loads(previous.read_text(encoding="utf-8")), legacy)
            second = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(previous.read_text(encoding="utf-8")), legacy)

    def test_runtime_cache_files_do_not_trigger_reinstallation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            runtime_home = root / "runtime"
            env = os.environ.copy()
            env.update({
                "HOME": str(root / "home"),
                "CODEX_HOME": str(root / "codex"),
                "TITAN_RUNTIME_HOME": str(runtime_home),
                "TITAN_NPM_NO_VENV": "1",
                "PYTHON": "/opt/miniconda3/bin/python3",
            })
            command = ["node", str(PACKAGE_DIR / "bin" / "titan.js"), "--help"]
            first = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)

            version_dir = runtime_home / "versions" / "0.1.3"
            sentinel = version_dir / ".venv" / "preserved"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text("keep", encoding="utf-8")
            cache = version_dir / "app" / "__pycache__" / "generated.pyc"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(b"cache")

            second = subprocess.run(command, env=env, capture_output=True, text=True, check=False)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(sentinel.exists())

    def test_marketplace_refreshes_when_plugin_script_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = os.environ.copy()
            env.update({
                "TITAN_RUNTIME_HOME": str(Path(tmp_dir) / "runtime"),
                "TITAN_HOME": str(Path(tmp_dir) / "home"),
                "TITAN_NPM_NO_VENV": "1",
            })
            command = ["node", str(PACKAGE_DIR / "bin" / "titan.js"), "--help"]
            first = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
            self.assertTrue((Path(tmp_dir) / "runtime" / "current.json").exists(), first.stderr)

            runtime_script = Path(tmp_dir) / "runtime" / "versions" / "0.1.3" / "tools" / "cli" / "titan.py"
            runtime_original = runtime_script.read_text(encoding="utf-8")
            runtime_script.write_text("stale runtime\n", encoding="utf-8")
            launcher = Path(tmp_dir) / "codex-marketplace" / "scripts" / "titan_mcp_launcher.py"
            original = launcher.read_text(encoding="utf-8")
            launcher.write_text("stale launcher\n", encoding="utf-8")
            second = subprocess.run(command, env=env, capture_output=True, text=True, check=False)

            self.assertEqual(runtime_script.read_text(encoding="utf-8"), runtime_original)
            self.assertEqual(launcher.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
