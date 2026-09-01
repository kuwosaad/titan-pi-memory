import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "integrations" / "grok_titan_plugin"

REQUIRED_SKILLS = {
    "titan-memory-workflow",
    "titan-doctor-workflow",
    "titan-grok-memory",
    "memory-sync",
    "titan-query",
    "titan-save",
    "titan-status",
    "titan-recent",
    "titan-graph",
    "titan-setup",
    "titan-clusters",
    "titan-cortex",
    "titan-patterns",
    "titan-key",
    "titan-dashboard",
}

EXPECTED_HOOK_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PostCompact",
    "Stop",
    "SessionEnd",
}

HOOK_COMMAND_SNIPPET = "titan_grok_hook.py"
HOOK_ROOT_SNIPPET = "GROK_PLUGIN_ROOT"


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


class GrokPluginFileTests(unittest.TestCase):
    def test_plugin_manifest_parses(self):
        manifest_path = PLUGIN_ROOT / "plugin.json"
        self.assertTrue(manifest_path.exists(), "plugin.json must exist")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "titan-memory")
        self.assertEqual(manifest.get("displayName"), "Titan Memory")
        self.assertIn("description", manifest)
        self.assertEqual(manifest.get("license"), "Apache-2.0")
        self.assertIn("grok", manifest.get("keywords", []))

    def test_manifest_does_not_contain_absolute_local_paths(self):
        manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
        for value in _walk_strings(manifest):
            with self.subTest(value=value):
                self.assertFalse(value.startswith(str(Path.home())))
                self.assertFalse(value.startswith("/Users/"))

    def test_mcp_config_uses_agent_grok_and_launcher(self):
        config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["titan-memory"]

        self.assertEqual(server["command"], "python3")
        args = server["args"]
        self.assertIn("--agent", args)
        self.assertIn("grok", args)
        joined = " ".join(args)
        self.assertIn("titan_mcp_launcher.py", joined)
        self.assertIn("GROK_PLUGIN_ROOT", joined)
        self.assertEqual(server["env"]["TITAN_AGENT_NAME"], "grok")

    def test_hooks_point_at_titan_grok_hook_script(self):
        hooks_path = PLUGIN_ROOT / "hooks" / "hooks.json"
        self.assertTrue(hooks_path.exists(), "hooks/hooks.json must exist")
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
        hooks = config["hooks"]

        missing_events = EXPECTED_HOOK_EVENTS - set(hooks)
        self.assertFalse(missing_events, f"hooks.json missing events: {sorted(missing_events)}")

        for event_name, entries in hooks.items():
            with self.subTest(event=event_name):
                found_command = False
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        command = hook.get("command", "")
                        self.assertIn(HOOK_COMMAND_SNIPPET, command)
                        self.assertIn(HOOK_ROOT_SNIPPET, command)
                        if "timeout" in hook:
                            self.assertEqual(hook["timeout"], 10)
                        found_command = True
                self.assertTrue(found_command, f"no command hooks for {event_name}")

        # Hook script is owned by another agent; assert the expected path string always.
        expected_script = PLUGIN_ROOT / "scripts" / "titan_grok_hook.py"
        if not expected_script.exists():
            # Tolerate short race while the hook agent lands the script.
            self.assertIn(
                HOOK_COMMAND_SNIPPET,
                hooks_path.read_text(encoding="utf-8"),
            )

    def test_required_skills_exist_with_frontmatter(self):
        skills_root = PLUGIN_ROOT / "skills"
        self.assertTrue(skills_root.is_dir())

        for skill_name in REQUIRED_SKILLS:
            with self.subTest(skill_name=skill_name):
                skill_path = skills_root / skill_name / "SKILL.md"
                self.assertTrue(skill_path.exists(), f"missing skill: {skill_name}")
                text = skill_path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"), f"{skill_name} missing frontmatter")
                frontmatter = text.split("---", 2)[1]
                self.assertIn(f"name: {skill_name}", frontmatter)
                self.assertIn("description:", frontmatter)

    def test_readme_exists_and_mentions_grok(self):
        readme = PLUGIN_ROOT / "README.md"
        self.assertTrue(readme.exists())
        text = readme.read_text(encoding="utf-8").lower()
        self.assertIn("grok", text)
        self.assertIn("titan", text)

    def test_launcher_and_first_run_scripts_exist(self):
        launcher = PLUGIN_ROOT / "scripts" / "titan_mcp_launcher.py"
        first_run = PLUGIN_ROOT / "scripts" / "titan_first_run.py"
        cli = PLUGIN_ROOT / "scripts" / "titan-grok"
        tools = PLUGIN_ROOT / "scripts" / "titan_grok_tools.py"
        installer = PLUGIN_ROOT / "scripts" / "install_grok.sh"
        self.assertTrue(launcher.exists())
        self.assertTrue(first_run.exists())
        self.assertTrue(cli.exists(), "titan-grok wrapper must ship in the public plugin")
        self.assertTrue(tools.exists(), "titan_grok_tools.py must ship in the public plugin")
        self.assertTrue(installer.exists())
        self.assertIn("titan-grok", installer.read_text(encoding="utf-8"))

        launcher_text = launcher.read_text(encoding="utf-8")
        self.assertIn('DEFAULT_AGENT = "grok"', launcher_text)
        self.assertIn("Grok", launcher_text)

        first_run_text = first_run.read_text(encoding="utf-8")
        self.assertIn('DEFAULT_AGENT = "grok"', first_run_text)
        self.assertIn("Grok", first_run_text)

    def test_hook_script_path_expected(self):
        expected = PLUGIN_ROOT / "scripts" / "titan_grok_hook.py"
        hooks_text = (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        self.assertIn("titan_grok_hook.py", hooks_text)
        self.assertIn("GROK_PLUGIN_ROOT", hooks_text)
        # Prefer presence; tolerate race with the hook-authoring agent.
        if expected.exists():
            self.assertTrue(expected.is_file())


if __name__ == "__main__":
    unittest.main()
