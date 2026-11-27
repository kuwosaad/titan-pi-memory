import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cli.titan import (
    CLAUDE_AGENT_NAME,
    CLAUDE_MARKETPLACE_NAME,
    CLAUDE_PLUGIN_ID,
    _claude_plugin_registration_status,
    ensure_claude_marketplace_snapshot,
    main,
    resolve_claude_marketplace_plugin_source,
    run_claude_purge,
    run_claude_reinstall_plugin,
    validate_claude_marketplace,
)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "integrations" / "claude_titan_plugin"


class ClaudeDistributionTests(unittest.TestCase):
    def setUp(self):
        # Purge tests must never discover the developer's live plugin state.
        paths = patch("tools.cli.titan._claude_runtime_state_paths", return_value=[])
        paths.start()
        self.addCleanup(paths.stop)

    def test_repo_marketplace_is_valid_and_resolves_plugin(self):
        manifest = ROOT / ".claude-plugin" / "marketplace.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))

        valid, reason = validate_claude_marketplace(payload)

        self.assertTrue(valid, reason)
        self.assertEqual(payload["name"], CLAUDE_MARKETPLACE_NAME)
        source = (ROOT / payload["plugins"][0]["source"]).resolve()
        self.assertEqual(source, PLUGIN.resolve())
        self.assertTrue((source / ".claude-plugin" / "plugin.json").exists())

    @unittest.skipUnless(shutil.which("claude"), "Claude Code CLI is not installed")
    def test_repo_marketplace_passes_strict_claude_validation(self):
        result = subprocess.run(
            ["claude", "plugin", "validate", "--strict", str(ROOT / ".claude-plugin" / "marketplace.json")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_snapshot_contains_complete_resolvable_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "marketplace"
            ok, detail = ensure_claude_marketplace_snapshot(target=target)

            self.assertTrue(ok, detail)
            self.assertEqual(
                resolve_claude_marketplace_plugin_source(marketplace_root=target),
                (target / "plugins" / "titan-memory").resolve(),
            )
            for relative in (
                ".claude-plugin/plugin.json",
                ".mcp.json",
                "hooks/hooks.json",
                "scripts/titan_claude_hook.py",
                "skills/titan-memory-workflow/SKILL.md",
                "scripts/titan_runtime.py",
                "scripts/titan_claude_launcher.py",
            ):
                self.assertTrue((target / "plugins" / "titan-memory" / relative).exists(), relative)

    def test_local_marketplace_documents_qualified_plugin_id(self):
        readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"claude plugin install {CLAUDE_PLUGIN_ID}", readme)

    def _readme_install_commands(self):
        readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
        block = readme.split("## Install", 1)[1].split("```bash\n", 1)[1].split("```", 1)[0]
        return [line.split() for line in block.splitlines() if line.strip()]

    def test_readme_marketplace_path_is_one_claude_accepts_and_holds_the_manifest(self):
        add, install = self._readme_install_commands()
        self.assertEqual(add[:4], ["claude", "plugin", "marketplace", "add"])
        source = add[4]
        # Claude Code rejects a bare "." as a marketplace source.
        self.assertTrue(source.startswith(("./", "../", "/", "~")), source)
        manifest = json.loads((ROOT / source / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        plugin, marketplace = install[3].split("@")
        self.assertEqual(marketplace, manifest["name"])
        self.assertIn(plugin, [entry["name"] for entry in manifest["plugins"]])

    @unittest.skipUnless(shutil.which("claude"), "Claude Code CLI is not installed")
    def test_readme_install_commands_work_verbatim_from_the_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "HOME": tmp, "CLAUDE_CONFIG_DIR": str(Path(tmp) / ".claude")}
            for command in self._readme_install_commands():
                result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                                        timeout=120, check=False)
                self.assertEqual(result.returncode, 0, f"{command}: {result.stdout}{result.stderr}")

    def test_python_package_declares_runtime_dependency_and_claude_files(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('"rich"', pyproject)
        self.assertIn('"integrations.claude_titan_plugin"', pyproject)
        for pattern in (".claude-plugin/*.json", "scripts/*.py", "skills/*/SKILL.md"):
            self.assertIn(pattern, pyproject)

    def test_setup_routes_claude_to_dedicated_setup(self):
        with patch("tools.cli.titan.run_setup_claude", return_value=0) as setup:
            self.assertEqual(main(["setup", "claude-code", "--dry-run", "--skip-plugin-install"]), 0)

        setup.assert_called_once_with(
            dry_run=True,
            verify=False,
            skip_plugin_install=True,
            non_interactive=False,
        )

    def test_claude_subcommands_route_to_dedicated_helpers(self):
        with patch("tools.cli.titan.run_claude_verify", return_value=0) as verify:
            self.assertEqual(main(["claude", "verify"]), 0)
        verify.assert_called_once_with()

        with patch("tools.cli.titan.run_claude_reinstall_plugin", return_value=0) as reinstall:
            self.assertEqual(main(["claude", "reinstall-plugin", "--dry-run"]), 0)
        reinstall.assert_called_once_with(dry_run=True)

    def test_reinstall_dry_run_is_non_mutating(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), patch("tools.cli.titan.ensure_claude_marketplace_snapshot") as ensure:
            self.assertEqual(run_claude_reinstall_plugin(dry_run=True), 0)

        ensure.assert_not_called()
        output = stdout.getvalue()
        self.assertIn(f"claude plugin install {CLAUDE_PLUGIN_ID}", output)
        self.assertIn(f"claude plugin marketplace add", output)

    def test_registration_rejects_loader_errors(self):
        result = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps([{"id": CLAUDE_PLUGIN_ID, "enabled": True, "errors": ["duplicate hook"]}]),
            "stderr": "",
        })()
        with patch("tools.cli.titan.subprocess.run", return_value=result):
            ok, detail = _claude_plugin_registration_status()

        self.assertFalse(ok)
        self.assertIn("duplicate hook", detail)

    def test_purge_defaults_to_dry_run_and_requires_explicit_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_home = root / "agents" / CLAUDE_AGENT_NAME
            traces = agent_home / "traces"
            traces.mkdir(parents=True)
            (traces / "one.jsonl").write_text("{}\n", encoding="utf-8")
            patches = (
                patch("tools.cli.titan.TITAN_HOME", root),
                patch("tools.cli.titan.resolve_agent_titan_home", return_value=agent_home),
                patch("tools.cli.titan.resolve_effective_spool_dir", return_value=traces),
            )
            with patches[0], patches[1], patches[2]:
                self.assertEqual(run_claude_purge(traces_only=True), 0)
                self.assertTrue(traces.exists())
                self.assertEqual(run_claude_purge(apply=True, traces_only=True), 1)
                self.assertTrue(traces.exists())
                self.assertEqual(run_claude_purge(apply=True, yes=True, traces_only=True), 0)
                self.assertFalse(traces.exists())

    def test_purge_refuses_foreign_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            foreign = root / "agents" / "codex"
            foreign.mkdir(parents=True)
            with patch("tools.cli.titan.TITAN_HOME", root), patch(
                "tools.cli.titan.resolve_agent_titan_home", return_value=foreign
            ):
                self.assertEqual(run_claude_purge(apply=True, yes=True), 1)
                self.assertTrue(foreign.exists())


if __name__ == "__main__":
    unittest.main()
