import unittest

from app.save_pipeline.pipeline import _build_event_prompt


class TransportTraceNormalizationTests(unittest.TestCase):
    def test_normalizes_telegram_inbound_trace_packet_into_human_prompt(self):
        event = {
            "event_type": "trace_packet",
            "session_id": "telegram:session",
            "event_id": "evt-1",
            "payload": {
                "goal": "Conversation: please plan first and then implement efficiently",
                "thoughts": "please plan first and then implement efficiently",
                "tool_calls": [],
                "outcome": "User message in conversation with Karu",
                "intent_phrase": "telegram inbound memory capture",
                "context": {
                    "source": "openclaw-hook:titan-karu-bridge",
                    "direction": "inbound",
                    "channel": "telegram",
                    "conversation_key": "telegram:default:876708125",
                    "inbound_message_id": "1663",
                    "agent_memory_namespace": "titan-karu",
                },
            },
        }

        prompt = _build_event_prompt(event)

        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["user_text"], "please plan first and then implement efficiently")
        self.assertEqual(prompt["assistant_text"], "")
        self.assertEqual(prompt["trace_mode"], "telegram_legacy_bridge")
        self.assertEqual(prompt["skip_reason"], None)

    def test_skips_shallow_social_transport_trace_packet(self):
        event = {
            "event_type": "trace_packet",
            "session_id": "discord:session",
            "event_id": "evt-2",
            "payload": {
                "goal": "Conversation: Ur the best karu",
                "thoughts": "Ur the best karu",
                "tool_calls": [],
                "outcome": "User message in conversation with Karu",
                "intent_phrase": "discord inbound memory capture",
                "context": {
                    "source": "openclaw-hook:titan-karu-bridge",
                    "direction": "inbound",
                    "channel": "discord",
                    "conversation_key": "discord:default:channel:1485519506104123565",
                    "inbound_message_id": "1664",
                },
            },
        }

        prompt = _build_event_prompt(event)

        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["trace_mode"], "discord_bridge")
        self.assertEqual(prompt["skip_reason"], "transport_bridge_shallow_social")

    def test_normalizes_discord_outbound_trace_packet_with_paired_user_text(self):
        event = {
            "event_type": "trace_packet",
            "session_id": "discord:session",
            "event_id": "evt-3",
            "payload": {
                "goal": "Assistant response in conversation with Karu",
                "thoughts": "We should split this into two stages.",
                "tool_calls": [],
                "outcome": "We should split this into two stages.",
                "intent_phrase": "discord outbound memory capture",
                "context": {
                    "source": "openclaw-hook:titan-karu-bridge",
                    "direction": "outbound",
                    "channel": "discord",
                    "conversation_key": "discord:default:channel:1485519506104123565",
                    "paired_user_text": "Can you fix Titan memory routing?",
                    "paired_inbound_message_id": "1665",
                    "outbound_message_id": "1666",
                },
            },
        }

        prompt = _build_event_prompt(event)

        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["user_text"], "Can you fix Titan memory routing?")
        self.assertEqual(prompt["assistant_text"], "We should split this into two stages.")
        self.assertEqual(prompt["trace_mode"], "discord_bridge")
        self.assertEqual(prompt["skip_reason"], None)


if __name__ == "__main__":
    unittest.main()
