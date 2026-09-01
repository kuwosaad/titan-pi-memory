import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PI_EXTENSION = ROOT_DIR / "tools" / "pi_extension" / "index.ts"


class PiExtensionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PI_EXTENSION.read_text(encoding="utf-8")

    def test_primary_messages_are_not_truncated_before_scene_ingest(self):
        self.assertNotIn("content: compactText(text, 2000)", self.source)
        self.assertGreaterEqual(self.source.count("content: text,"), 2)

    def test_memory_queries_request_scene_pointers_without_scene_bodies(self):
        self.assertIn('params.set("include_scenes", "false")', self.source)
        self.assertNotIn('"--- Scene context ---"', self.source)

    def test_tool_outputs_remain_compact(self):
        self.assertIn("compactText(JSON.stringify(input), 500)", self.source)
        self.assertIn("compactText(extractTextContent(event.content), 1000)", self.source)

    def test_query_surfaces_explain_abstention_consistently(self):
        self.assertEqual(self.source.count("No sufficiently relevant memories found."), 3)
        self.assertNotIn("No relevant memories found.", self.source)

    def test_existing_startup_check_publishes_keyed_memory_status(self):
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN CHECKING")', self.source)
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN READY")', self.source)
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN UNCONFIGURED")', self.source)
        self.assertIn('ctx.ui.setStatus("titan-memory", "TITAN OFFLINE")', self.source)

    def test_server_selects_a_compatible_python_instead_of_first_path_match(self):
        self.assertIn("function pythonCandidates()", self.source)
        self.assertIn("process.env.TITAN_PYTHON", self.source)
        self.assertIn("assert sys.version_info >= (3, 10)", self.source)
        self.assertIn("const proc = spawn(pythonCommand, [serverScript]", self.source)
        self.assertNotIn('spawn("python3", [serverScript]', self.source)

    def test_dashboard_rich_install_is_verified_and_has_user_scope_fallback(self):
        self.assertIn("async function ensureRichDependency", self.source)
        self.assertIn('["-m", "pip", "install", "--user", "rich"]', self.source)
        self.assertIn('["-m", "pip", "install", "--user", "--break-system-packages", "rich"]', self.source)
        self.assertIn("const richReady = await ensureRichDependency(pythonCommand);", self.source)
        self.assertIn("if (!richReady)", self.source)
        self.assertIn('scriptArgs.push("--plain");', self.source)
        self.assertIn("Rich is unavailable; launching the plain-text dashboard.", self.source)

    def test_dashboard_rich_mode_is_gated_by_verified_import(self):
        dashboard = self.source[self.source.index('pi.registerCommand("titan-dashboard"'):]
        check = dashboard.index("const richReady = await ensureRichDependency(pythonCommand);")
        launch = dashboard.index("const result = await runProcess(pythonCommand, scriptArgs")
        self.assertLess(check, launch)
        self.assertIn("if (await canImportRich()) return true;", self.source)

    def test_dashboard_unavailable_rich_uses_explicit_plain_mode(self):
        dashboard = self.source[self.source.index('pi.registerCommand("titan-dashboard"'):]
        fallback = dashboard.index('scriptArgs.push("--plain");')
        launch = dashboard.index("const result = await runProcess(pythonCommand, scriptArgs")
        self.assertLess(fallback, launch)
        self.assertIn('ctx.ui.notify("Rich is unavailable; launching the plain-text dashboard.", "warning")', dashboard)


if __name__ == "__main__":
    unittest.main()
