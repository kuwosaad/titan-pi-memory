import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "integrations" / "claude_titan_plugin"


class ClaudePluginFileTests(unittest.TestCase):
    def test_plugin_manifest_uses_standard_component_discovery(self):
        manifest_path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "titan-memory")
        self.assertTrue((PLUGIN_ROOT / "skills").is_dir())
        self.assertTrue((PLUGIN_ROOT / ".mcp.json").is_file())
        self.assertTrue((PLUGIN_ROOT / "hooks" / "hooks.json").is_file())
        for standard_component in ("skills", "mcpServers", "hooks"):
            self.assertNotIn(
                standard_component,
                manifest,
                f"{standard_component} is auto-discovered; declaring its standard path loads it twice",
            )

    def test_mcp_config_uses_plugin_local_managed_launcher(self):
        config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["titan-memory"]
        serialized = json.dumps(server, sort_keys=True)

        self.assertNotEqual(server["command"], "titan")
        self.assertIn("CLAUDE_PLUGIN_ROOT", serialized)
        self.assertIn("CLAUDE_PLUGIN_DATA", serialized)
        self.assertIn("${user_config.agent_name}", serialized)

    def test_hooks_cover_current_claude_lifecycle(self):
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        expected = {
            "SessionStart",
            "UserPromptSubmit",
            "PostToolUse",
            "PostToolUseFailure",
            "PostCompact",
            "Stop",
            "SubagentStop",
            "SessionEnd",
        }
        self.assertTrue(expected.issubset(config["hooks"]))

    def test_hooks_use_cross_platform_plugin_local_launcher(self):
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = {
            hook["command"]
            for entries in config["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        }

        self.assertEqual(len(commands), 1)
        command = commands.pop()
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
        self.assertFalse(command.rstrip().endswith(".py"), "Claude hooks must not rely on direct .py execution")

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
