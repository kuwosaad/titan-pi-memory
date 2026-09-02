import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATHS = (
    ROOT / "integrations" / "codex_titan_plugin" / "skills" / "titan-memory-workflow" / "SKILL.md",
    ROOT / "integrations" / "claude_titan_plugin" / "skills" / "titan-memory-workflow" / "SKILL.md",
    ROOT / "integrations" / "grok_titan_plugin" / "skills" / "titan-memory-workflow" / "SKILL.md",
    ROOT / "tools" / "pi_extension" / "skills" / "titan-memory-workflow" / "SKILL.md",
)


class TitanMemoryWorkflowSkillTests(unittest.TestCase):
    def test_packaged_agent_workflows_stay_identical(self):
        canonical = WORKFLOW_PATHS[0].read_text(encoding="utf-8")

        for path in WORKFLOW_PATHS[1:]:
            with self.subTest(path=path):
                self.assertEqual(path.read_text(encoding="utf-8"), canonical)

    def test_pi_package_exposes_only_the_canonical_general_workflow(self):
        package = (ROOT / "package.json").read_text(encoding="utf-8")

        self.assertIn("./tools/pi_extension/skills/titan-memory-workflow", package)
        self.assertNotIn("./tools/pi_extension/skills/titan-pi-memory", package)


if __name__ == "__main__":
    unittest.main()
