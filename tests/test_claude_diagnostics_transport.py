import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cli.titan import (
    CLAUDE_AGENT_NAME,
    CODEX_REQUIRED_MCP_TOOLS,
    _CLAUDE_PLUGIN_RELEASE_FILES,
    _claude_plugin_files_ok,
    load_claude_mcp_contract,
    run_claude_list_tools,
    run_claude_verify,
)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "integrations" / "claude_titan_plugin"


class ClaudeDiagnosticsTransportTests(unittest.TestCase):
    def test_contract_resolves_plugin_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "plugin-data"
            contract = load_claude_mcp_contract(
                plugin_root=PLUGIN_ROOT,
                plugin_data=data,
                agent=CLAUDE_AGENT_NAME,
            )

        self.assertEqual(contract["command"], "node")
        self.assertEqual(
            [Path(item) for item in contract["args"]],
            [PLUGIN_ROOT.resolve() / "scripts" / "titan_claude_mcp.js"],
        )
        self.assertEqual(contract["env"]["TITAN_CLAUDE_DATA"], str(data.resolve()))
        self.assertEqual(contract["env"]["TITAN_AGENT_NAME"], CLAUDE_AGENT_NAME)

    def test_doctor_completeness_uses_release_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "plugin"
            for relative in _CLAUDE_PLUGIN_RELEASE_FILES:
                path = plugin / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("safe", encoding="utf-8")
            with patch("tools.cli.titan.CLAUDE_PLUGIN_DIR", plugin):
                self.assertEqual(_claude_plugin_files_ok(), (True, []))
                missing_path = plugin / "runtime" / "daemon.py"
                missing_path.unlink()
                ok, missing = _claude_plugin_files_ok()

        self.assertFalse(ok)
        self.assertIn(str(missing_path), missing)

    def test_list_tools_fails_when_real_launcher_handshake_fails(self):
        output = io.StringIO()
        with patch(
            "tools.cli.titan.claude_mcp_stdio_handshake",
            return_value=(False, [], "launcher failed"),
        ), patch("sys.stdout", output):
            result = run_claude_list_tools()

        self.assertEqual(result, 1)
        self.assertIn("launcher failed", output.getvalue())

    def test_list_tools_reports_handshake_tools(self):
        tools = sorted(CODEX_REQUIRED_MCP_TOOLS)
        output = io.StringIO()
        with patch(
            "tools.cli.titan.claude_mcp_stdio_handshake",
            return_value=(True, tools, "stdio handshake succeeded"),
        ), patch("sys.stdout", output):
            result = run_claude_list_tools(json_output=True)

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["tools"], tools)

    def test_verify_fails_when_plugin_transport_is_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marketplace = root / "marketplace"
            manifest = marketplace / ".claude-plugin" / "marketplace.json"
            plugin = marketplace / "plugins" / "titan-memory" / ".claude-plugin" / "plugin.json"
            manifest.parent.mkdir(parents=True)
            plugin.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({
                    "name": "titan-pi-memory",
                    "owner": {"name": "Titan"},
                    "plugins": [{"name": "titan-memory", "source": "./plugins/titan-memory"}],
                }),
                encoding="utf-8",
            )
            plugin.write_text("{}", encoding="utf-8")
            traces = root / "traces"
            traces.mkdir()
            output = io.StringIO()
            with patch("tools.cli.titan.shutil.which", return_value="/usr/bin/claude"), patch(
                "tools.cli.titan._claude_plugin_registration_status",
                return_value=(True, "registered"),
            ), patch("tools.cli.titan._claude_plugin_files_ok", return_value=(True, [])), patch(
                "tools.cli.titan.CLAUDE_MARKETPLACE_DIR", marketplace
            ), patch("tools.cli.titan.resolve_effective_spool_dir", return_value=traces), patch(
                "tools.cli.titan.claude_mcp_stdio_handshake",
                return_value=(False, [], "proxy unavailable"),
            ), patch(
                "tools.cli.titan.get_required_provider_envs",
                return_value={"required_envs": [], "extraction_backend": "openai", "embedding_backend": "openai"},
            ), patch("sys.stdout", output):
                result = run_claude_verify()

        self.assertEqual(result, 1)
        self.assertIn("proxy unavailable", output.getvalue())


if __name__ == "__main__":
    unittest.main()
