import json
import tomllib
import unittest
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT_DIR / "integrations" / "codex_titan_plugin"


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


class CodexPluginFileTests(unittest.TestCase):
    def test_plugin_manifest_parses_and_paths_exist(self):
        manifest_path = PLUGIN_DIR / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "titan-memory")
        for key in ("skills", "mcpServers", "hooks"):
            self.assertTrue(manifest[key].startswith("./"))

        self.assertTrue((PLUGIN_DIR / manifest["skills"]).exists())
        self.assertTrue((PLUGIN_DIR / manifest["mcpServers"]).exists())
        self.assertTrue((PLUGIN_DIR / manifest["hooks"]).exists())

    def test_manifest_has_marketplace_metadata_and_assets(self):
        manifest = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

        for key in ("author", "homepage", "repository", "license", "keywords", "interface"):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["author"]["name"], "Titan Memory")
        self.assertTrue(manifest["author"]["url"].startswith("https://"))
        self.assertEqual(manifest["license"], "Apache-2.0")
        self.assertGreaterEqual(set(manifest["keywords"]), {"memory", "codex", "mcp"})

        interface = manifest["interface"]
        for key in (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "websiteURL",
            "privacyPolicyURL",
            "termsOfServiceURL",
            "defaultPrompt",
            "brandColor",
            "composerIcon",
            "logo",
            "screenshots",
        ):
            self.assertIn(key, interface)
        self.assertEqual(interface["displayName"], "Titan Memory")
        self.assertTrue(interface["privacyPolicyURL"].startswith("https://"))
        self.assertTrue(interface["termsOfServiceURL"].startswith("https://"))
        self.assertRegex(interface["brandColor"], r"^#[0-9a-fA-F]{6}$")

        asset_paths = [interface["composerIcon"], interface["logo"], *interface["screenshots"]]
        for asset_path in asset_paths:
            with self.subTest(asset_path=asset_path):
                self.assertTrue(asset_path.startswith("./assets/"))
                resolved = PLUGIN_DIR / asset_path
                self.assertTrue(resolved.exists())
                self.assertGreater(resolved.stat().st_size, 0)

    def test_manifest_does_not_contain_absolute_local_paths(self):
        manifest = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for value in _walk_strings(manifest):
            with self.subTest(value=value):
                self.assertFalse(value.startswith(str(Path.home())))
                self.assertFalse(value.startswith("/Users/"))

    def test_mcp_config_uses_titan_memory_server(self):
        payload = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))
        server = payload["mcpServers"]["titan-memory"]

        # The plugin must not require a globally installed `titan` binary.
        # Codex launches Titan through npm so the one-command setup remains enough.
        self.assertEqual(server["command"], "npx")
        self.assertEqual(server["args"], ["-y", "titan-memory-cli@latest", "mcp", "--agent", "codex"])
        self.assertEqual(server["env"]["TITAN_AGENT_NAME"], "codex")
        self.assertTrue((PLUGIN_DIR / "scripts" / "titan_mcp_launcher.py").exists())
        self.assertTrue((PLUGIN_DIR / "scripts" / "titan_first_run.py").exists())

    def test_hooks_point_at_titan_codex_hook_script(self):
        payload = json.loads((PLUGIN_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        script = "python3 ${PLUGIN_ROOT}/scripts/titan_codex_hook.py"

        for hook_entries in payload["hooks"].values():
            for entry in hook_entries:
                for hook_config in entry["hooks"]:
                    self.assertEqual(hook_config["command"], script)
        self.assertTrue((PLUGIN_DIR / "scripts" / "titan_codex_hook.py").exists())

    def test_skills_have_required_frontmatter(self):
        expected_skills = {
            "titan-memory-workflow",
            "titan-patterns-workflow",
            "titan-cluster-graph-workflow",
            "memory-sync",
            "titan-doctor-workflow",
        }

        skill_dirs = {path.name for path in (PLUGIN_DIR / "skills").iterdir() if path.is_dir()}
        self.assertGreaterEqual(skill_dirs, expected_skills)

        for skill_name in expected_skills:
            with self.subTest(skill_name=skill_name):
                skill_path = PLUGIN_DIR / "skills" / skill_name / "SKILL.md"
                content = skill_path.read_text(encoding="utf-8")

                self.assertTrue(content.startswith("---\n"))
                self.assertIn(f"name: {skill_name}", content)
                self.assertIn("description:", content)

    def test_repo_local_marketplace_points_to_plugin(self):
        payload = json.loads((ROOT_DIR / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        plugin = payload["plugins"][0]

        self.assertEqual(plugin["name"], "titan-memory")
        self.assertEqual(plugin["source"]["source"], "local")
        self.assertEqual(plugin["source"]["path"], "./integrations/codex_titan_plugin")

    def test_pyproject_packages_codex_plugin_bundle(self):
        pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        find_config = pyproject["tool"]["setuptools"]["packages"]["find"]
        package_data = pyproject["tool"]["setuptools"]["package-data"]

        self.assertIn("integrations*", find_config["include"])
        codex_data = package_data["integrations.codex_titan_plugin"]
        for pattern in (
            ".mcp.json",
            ".agents/plugins/*.json",
            ".codex-plugin/*.json",
            "hooks/*.json",
            "scripts/*.py",
            "skills/*/SKILL.md",
            "assets/*.png",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, codex_data)

    def test_public_package_docs_and_marketplace_are_present(self):
        for relative_path in ("README.md", "PRIVACY.md", "TERMS.md"):
            with self.subTest(relative_path=relative_path):
                path = PLUGIN_DIR / relative_path
                self.assertTrue(path.exists())
                content = path.read_text(encoding="utf-8")
                self.assertIn("Titan", content)
                self.assertNotIn("/Users/", content)

        payload = json.loads((PLUGIN_DIR / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        plugin = payload["plugins"][0]
        self.assertEqual(payload["name"], "titan-memory-codex")
        self.assertEqual(payload["interface"]["displayName"], "Titan Memory for Codex")
        self.assertEqual(plugin["name"], "titan-memory")
        self.assertEqual(plugin["source"], {"source": "local", "path": "./"})
        self.assertEqual(plugin["policy"]["installation"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
