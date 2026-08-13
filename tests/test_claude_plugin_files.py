import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "integrations" / "claude_titan_plugin"


class ClaudePluginFileTests(unittest.TestCase):
    def test_plugin_manifest_parses_and_paths_exist(self):
        manifest_path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "titan-memory")
        for key in ("skills", "mcpServers", "hooks"):
            value = manifest[key]
            self.assertTrue(value.startswith("./"))
            self.assertTrue((PLUGIN_ROOT / value).exists())

    def test_mcp_config_uses_titan_memory_server(self):
        config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["titan-memory"]

        self.assertEqual(server["command"], "titan")
        self.assertEqual(server["args"], ["mcp", "--agent", "${user_config.agent_name}"])
        self.assertEqual(server["env"]["TITAN_AGENT_NAME"], "${user_config.agent_name}")

    def test_hooks_point_at_titan_hook_script(self):
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for entries in config["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    self.assertEqual(hook["type"], "command")
                    self.assertEqual(hook["command"], '"${CLAUDE_PLUGIN_ROOT}"/scripts/titan_claude_hook.py')
                    self.assertEqual(hook["timeout"], 10)

    def test_hook_script_is_executable(self):
        script = PLUGIN_ROOT / "scripts" / "titan_claude_hook.py"
        self.assertTrue(os.access(script, os.X_OK))

    def test_skill_frontmatter_includes_name_and_description(self):
        for skill_path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md"):
            text = skill_path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            frontmatter = text.split("---", 2)[1]
            self.assertIn("name:", frontmatter)
            self.assertIn("description:", frontmatter)


if __name__ == "__main__":
    unittest.main()
