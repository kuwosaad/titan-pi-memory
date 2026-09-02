import json
import os
import re
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
        self.assertEqual(
            payload["scripts"]["prepack"],
            "node scripts/prepare-runtime.js && node scripts/audit-runtime.js",
        )
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

    def test_prepare_runtime_uses_committed_opencode_bundle(self):
        script = (PACKAGE_DIR / "scripts" / "prepare-runtime.js").read_text(encoding="utf-8")

        self.assertIn(
            "integrations/opencode_titan_plugin/dist/titan_v2_spool_plugin.ts",
            script,
        )
        self.assertNotIn("tools/opencode/titan_v2_spool_plugin.ts", script)

        for relative_path in (
            "integrations/opencode_titan_plugin/dist/titan_v2_spool_plugin.ts",
            "tools/opencode/install_plugin.py",
        ):
            with self.subTest(relative_path=relative_path):
                tracked = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", relative_path],
                    cwd=ROOT_DIR,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(tracked.returncode, 0, f"runtime source is not committed: {relative_path}")

    def test_packed_runtime_excludes_personal_and_development_material(self):
        prepared = subprocess.run(
            ["node", "scripts/prepare-runtime.js"],
            cwd=PACKAGE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)

        packed = subprocess.run(
            ["npm", "pack", "--dry-run", "--json", "--ignore-scripts"],
            cwd=PACKAGE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(packed.returncode, 0, packed.stderr)
        package = json.loads(packed.stdout)[0]
        paths = {entry["path"] for entry in package["files"]}

        forbidden_prefixes = (
            "runtime/entrypoints/overnight/",
            "runtime/tools/benchmarks/",
            "runtime/tools/dev/",
            "runtime/tools/pi_extension/",
            "runtime/tools/presentations/",
            "runtime/tools/scripts/",
        )
        self.assertFalse(
            sorted(path for path in paths if path.startswith(forbidden_prefixes)),
            "npm contains development-only runtime files",
        )

        personal_terms = re.compile(
            r"(?:"
            r"/Users/|"
            r"PROFILE_ACTOR_TERMS\s*=|"
            r"when the exchange clearly refers to Kuwo and Karu|"
            r"Kuwo is a beginner learning Python|"
            r"Kuwo prefers direct instructions|"
            r"['\"]karu['\"]\s*:\s*['\"]assistant['\"]|"
            r"['\"]kuwo['\"]\s*:\s*['\"]user['\"]|"
            r"['\"]saad['\"]\s*,\s*['\"]kuwo['\"]"
            r")",
            re.IGNORECASE,
        )
        leaks = []
        for relative_path in sorted(paths):
            packaged_path = PACKAGE_DIR / relative_path
            if not packaged_path.is_file():
                continue
            try:
                text = packaged_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if personal_terms.search(text):
                leaks.append(relative_path)
        self.assertEqual(leaks, [], f"npm contains founder-specific material: {leaks}")

    def test_prepack_audit_rejects_a_local_home_path(self):
        payload = json.loads((PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            payload["scripts"]["prepack"],
            "node scripts/prepare-runtime.js && node scripts/audit-runtime.js",
        )

        prepared = subprocess.run(
            ["node", "scripts/prepare-runtime.js"],
            cwd=PACKAGE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)

        leak = PACKAGE_DIR / "runtime" / "private-leak.txt"
        try:
            leak.write_text("/Users/example/private-memory.db\n", encoding="utf-8")
            audited = subprocess.run(
                ["node", "scripts/audit-runtime.js"],
                cwd=PACKAGE_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            leak.unlink(missing_ok=True)

        self.assertNotEqual(audited.returncode, 0)
        self.assertIn("private-leak.txt", audited.stderr)

    def test_prepack_audit_rejects_new_founder_specific_examples(self):
        prepared = subprocess.run(
            ["node", "scripts/prepare-runtime.js"],
            cwd=PACKAGE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)

        leak = PACKAGE_DIR / "runtime" / "founder-example.txt"
        try:
            leak.write_text("Kuwo prefers concise replies from Karu.\n", encoding="utf-8")
            audited = subprocess.run(
                ["node", "scripts/audit-runtime.js"],
                cwd=PACKAGE_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            leak.unlink(missing_ok=True)

        self.assertNotEqual(audited.returncode, 0)
        self.assertIn("founder-example.txt", audited.stderr)

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
