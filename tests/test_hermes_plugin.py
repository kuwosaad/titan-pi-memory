import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integrations import hermes_titan_plugin as plugin


class HermesPluginTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Mock()
        self.ctx.call_mcp.return_value = {"ok": True}
        plugin.register(self.ctx)
        hooks = dict(call.args for call in self.ctx.register_hook.call_args_list)
        self.stage = hooks["post_llm_call"]
        self.finish = hooks["on_session_end"]
        self.turn = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "user_message": "Remember that release day is Tuesday.",
            "assistant_response": "Got it. We release on Tuesday.",
        }

    def end_turn(self, turn, **overrides):
        outcome = {
            "session_id": turn["session_id"],
            "turn_id": turn["turn_id"],
            "completed": True,
            "failed": False,
            "interrupted": False,
            "turn_exit_reason": "text_response(stop)",
        }
        self.finish(**(outcome | overrides))

    def capture(self, **turn):
        self.stage(**turn)
        self.end_turn(turn)

    def test_native_connect_preserves_store_and_unrelated_grants_on_upgrade(self):
        server = {"command": "titan", "args": ["mcp", "--agent", "hermes-archive"]}
        grants = ["other-server"]
        profile = {"mcp_servers": {"titan-memory": server},
                   "plugins": {"entries": {"titan-memory": {"mcp_allowlist": grants}}}}
        host_config = Mock()
        host_config.load_config.return_value = profile
        host_config.get_config_path.return_value = Path("/profiles/archive/config.yaml")
        modules = {"hermes_cli.config": host_config, "hermes_cli.profiles": Mock()}
        args = SimpleNamespace(agent=None, configure_providers=True)
        with patch.dict("sys.modules", modules), patch.object(Path, "is_file", return_value=True), \
                patch.object(plugin.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            plugin._connect_profile(args)
            self.assertEqual(run.call_args.args[0][-3:], ["configure", "--agent", "hermes-archive"])
            settings = {key: json.loads(value) for (key, value), _ in host_config.set_config_value.call_args_list}
            server = settings["mcp_servers.titan-memory"]
            self.assertEqual(server["args"][-3:], ["mcp", "--agent", "hermes-archive"])
            self.assertTrue(server["args"][0].endswith("titan_runtime.py"))
            self.assertEqual(settings["plugins.entries.titan-memory.mcp_allowlist"], ["other-server", "titan-memory"])
            profile["mcp_servers"]["titan-memory"] = server
            grants.append("titan-memory")
            host_config.set_config_value.reset_mock()
            plugin._connect_profile(args)
            host_config.set_config_value.assert_not_called()

    def test_native_connect_upgrades_the_documented_absolute_titan_path(self):
        helper = Path("/plugin/titan_runtime.py")
        for command in ("/Users/me/.local/bin/titan", "/usr/local/bin/titan", r"C:\npm\titan.cmd", "titan.exe"):
            server = {"command": command, "args": ["mcp", "--agent", "hermes-work"]}
            self.assertEqual(plugin._existing_store(server, helper), "hermes-work", command)

        profile = {"mcp_servers": {"titan-memory": {"command": "/Users/me/.local/bin/titan",
                                                     "args": ["mcp", "--agent", "hermes-work"]}}}
        host_config = Mock()
        host_config.load_config.return_value = profile
        host_config.get_config_path.return_value = Path("/profiles/work/config.yaml")
        modules = {"hermes_cli.config": host_config, "hermes_cli.profiles": Mock()}
        with patch.dict("sys.modules", modules), patch.object(Path, "is_file", return_value=True), \
                patch.object(plugin.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            plugin._connect_profile(SimpleNamespace(agent=None, configure_providers=False))
        settings = {key: json.loads(value) for (key, value), _ in host_config.set_config_value.call_args_list}
        server = settings["mcp_servers.titan-memory"]
        self.assertTrue(server["args"][0].endswith("titan_runtime.py"))
        self.assertEqual(server["args"][1:], ["mcp", "--agent", "hermes-work"])

    def test_native_connect_leaves_custom_mcp_endpoints_alone(self):
        helper = Path("/plugin/titan_runtime.py")
        for server in ({"url": "https://example.invalid"},
                       {"command": "/opt/bin/titan-proxy", "args": ["mcp", "--agent", "hermes"]},
                       {"command": "/usr/local/bin/titan", "args": ["serve", "--agent", "hermes"]}):
            with self.assertRaisesRegex(SystemExit, "custom titan-memory MCP server"):
                plugin._existing_store(server, helper)

    def test_discards_blocked_failed_and_interrupted_turns(self):
        for outcome in (
            {"turn_exit_reason": "blocked_by_plugin_pre_llm_call"},
            {"completed": False, "failed": True},
            {"completed": False, "interrupted": True},
            {"turn_exit_reason": None},
        ):
            with self.subTest(outcome=outcome):
                self.stage(**self.turn)
                self.end_turn(self.turn, **outcome)
                self.end_turn(self.turn)
        self.ctx.call_mcp.assert_not_called()

    def test_waits_for_the_matching_turn_and_consumes_it_once(self):
        self.stage(**self.turn)
        self.end_turn(self.turn, session_id="unrelated-session")
        self.end_turn(self.turn, turn_id="unrelated-turn")
        self.ctx.call_mcp.assert_not_called()
        self.end_turn(self.turn)
        self.end_turn(self.turn)
        self.ctx.call_mcp.assert_called_once()

    def test_captures_only_the_current_text_pair_with_stable_turn_identity(self):
        self.capture(**self.turn, conversation_history=[{"content": "private history"}])
        server, tool, packet = self.ctx.call_mcp.call_args.args
        self.assertEqual((server, tool), ("titan-memory", "store_trace_packet"))
        self.assertEqual(self.ctx.call_mcp.call_args.kwargs, {"timeout": plugin.CAPTURE_TIMEOUT_SECONDS})
        self.assertEqual(packet, {
            "session_id": "session-1",
            "event_id": packet["event_id"],
            "goal": self.turn["user_message"],
            "outcome": self.turn["assistant_response"],
            "max_text_chars": 12_000,
            "context": {"source": "hermes"},
        })

        self.capture(**self.turn)
        self.assertEqual(self.ctx.call_mcp.call_args.args[2]["event_id"], packet["event_id"])
        self.capture(**{**self.turn, "turn_id": "turn-2"})
        self.assertNotEqual(self.ctx.call_mcp.call_args.args[2]["event_id"], packet["event_id"])

    def test_sends_only_text_blocks_within_the_catalog_limit(self):
        self.capture(**{**self.turn, "user_message": [
            {"type": "text", "text": "A note"},
            {"type": "image_url", "image_url": "private attachment data"},
            {"type": "text", "text": "word " * plugin.MAX_TEXT_CHARS},
        ]})
        packet = self.ctx.call_mcp.call_args.args[2]
        self.assertTrue(packet["goal"].startswith("A note\nword word"))
        self.assertNotIn("private attachment data", packet["goal"])
        self.assertLessEqual(len(packet["goal"]), plugin.MAX_TEXT_CHARS)

    def test_limit_applies_to_both_messages_before_the_mcp_call(self):
        long_reply = "y" * (plugin.MAX_TEXT_CHARS * 3)
        self.capture(**{**self.turn, "user_message": "z " * plugin.MAX_TEXT_CHARS, "assistant_response": long_reply})
        packet = self.ctx.call_mcp.call_args.args[2]
        self.assertLessEqual(len(packet["goal"]), plugin.MAX_TEXT_CHARS)
        self.assertEqual(packet["outcome"], long_reply[: plugin.MAX_TEXT_CHARS])

    def test_credential_split_by_the_limit_is_not_sent_in_part(self):
        secret = "sk-" + "A1b2C3d4" * 8
        lead = "a" * (plugin.MAX_TEXT_CHARS - 20) + " "
        self.capture(**{**self.turn, "user_message": lead + secret + " tail"})
        goal = self.ctx.call_mcp.call_args.args[2]["goal"]
        self.assertEqual(goal, lead.rstrip())
        self.assertNotIn("sk-", goal)

    def test_text_within_the_limit_is_sent_unchanged(self):
        exact = "b" * plugin.MAX_TEXT_CHARS
        self.capture(**{**self.turn, "user_message": exact})
        self.assertEqual(self.ctx.call_mcp.call_args.args[2]["goal"], exact)

    def test_ignores_incomplete_turns(self):
        for field in self.turn:
            with self.subTest(field=field):
                self.capture(**{**self.turn, field: " "})
        self.ctx.call_mcp.assert_not_called()

    def test_mcp_errors_do_not_break_the_turn_or_log_its_contents(self):
        for error in (PermissionError("private"), TimeoutError("private"), None):
            with self.subTest(error=error):
                self.ctx.call_mcp.side_effect = error
                self.ctx.call_mcp.return_value = {"ok": False, "error": "private"}
                with self.assertLogs(plugin.LOGGER, level="WARNING") as logs:
                    self.assertIsNone(self.capture(**self.turn))
                self.assertNotIn("private", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
