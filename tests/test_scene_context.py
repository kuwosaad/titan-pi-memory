import unittest
from unittest.mock import patch

from app.save_pipeline.pipeline import get_scene_context
from app.storage.models import Scene, SceneMessage


class SceneContextTests(unittest.TestCase):
    def test_get_scene_context_returns_scene_payload(self):
        scene = Scene(
            scene_id="s1:scene:e-1",
            session_id="s1",
            turn=1,
            kind="message_exchange",
            anchor_event_id="e-1",
            source_event_ids=["e-1"],
            messages=[
                SceneMessage(role="user", content="How should dedupe work?", message_id="u1", event_id=None),
                SceneMessage(role="assistant", content="Use session_id and event_id.", message_id="a1", event_id="e-1"),
            ],
            extraction_user_text="How should dedupe work?",
            extraction_assistant_text="Use session_id and event_id.",
            used_context_fallback=False,
            ts="2026-04-09T00:00:00+00:00",
        )

        with patch("app.save_pipeline.pipeline.get_scene", return_value=scene) as mock_get_scene:
            payload = get_scene_context("s1:scene:e-1")

        self.assertEqual(payload["scene"]["scene_id"], "s1:scene:e-1")
        self.assertEqual(payload["scene"]["messages"][0]["role"], "user")
        mock_get_scene.assert_called_once_with("s1:scene:e-1")

    def test_get_scene_context_returns_error_for_missing_scene(self):
        with patch("app.save_pipeline.pipeline.get_scene", return_value=None) as mock_get_scene:
            payload = get_scene_context("missing-scene")

        self.assertEqual(payload, {"error": "scene not found", "scene_id": "missing-scene"})
        mock_get_scene.assert_called_once_with("missing-scene")

    def test_get_scene_context_requires_scene_id(self):
        payload = get_scene_context("   ")

        self.assertEqual(payload, {"error": "scene_id is required", "scene_id": ""})


if __name__ == "__main__":
    unittest.main()
