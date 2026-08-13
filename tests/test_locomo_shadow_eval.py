import unittest

from tools.benchmarks.locomo_shadow_eval import answer_matches, iter_session_keys


class LocomoShadowEvalTests(unittest.TestCase):
    def test_iter_session_keys_orders_sessions_numerically(self):
        conversation = {
            "session_10": [],
            "session_2": [],
            "speaker_a": "A",
            "session_1": [],
        }
        self.assertEqual(iter_session_keys(conversation), ["session_1", "session_2", "session_10"])

    def test_answer_matches_uses_memory_and_scene_context(self):
        response = {
            "brief": "",
            "scene_brief": "SCENE BRIEF:\n1. [message_exchange] user: caroline mentioned sweden",
            "memories": [{"text": "Caroline moved from Sweden four years ago."}],
            "scenes": [
                {
                    "scene_id": "scene-1",
                    "messages": [
                        {"role": "user", "content": "I moved from Sweden four years ago."},
                        {"role": "assistant", "content": "That must have been a big change."},
                    ],
                }
            ],
        }
        self.assertTrue(answer_matches("Sweden", response))
        self.assertFalse(answer_matches("Norway", response))


if __name__ == "__main__":
    unittest.main()
