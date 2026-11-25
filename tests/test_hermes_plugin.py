import unittest
from unittest.mock import Mock

from integrations import hermes_titan_plugin as plugin


class HermesPluginTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Mock()
        self.ctx.call_mcp.return_value = {"ok": True}
        plugin.register(self.ctx)
        hook, self.capture = self.ctx.register_hook.call_args.args
        self.assertEqual(hook, "post_llm_call")
        self.turn = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "user_message": "Remember that release day is Tuesday.",
            "assistant_response": "Got it. We release on Tuesday.",
        }

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

    def test_passes_multimodal_text_to_titan_for_redaction_before_truncation(self):
        self.capture(**{**self.turn, "user_message": [
            {"type": "text", "text": "A note"},
            {"type": "image_url", "image_url": "private attachment data"},
            {"type": "text", "text": "x" * (plugin.MAX_TEXT_CHARS + 1)},
        ]})
        packet = self.ctx.call_mcp.call_args.args[2]
        self.assertEqual(packet["goal"], "A note\n" + "x" * (plugin.MAX_TEXT_CHARS + 1))

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
