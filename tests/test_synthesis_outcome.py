import unittest
from unittest.mock import patch

from app.save_pipeline.pipeline import run_memory_pipeline_outcome
from app.storage.models import Scene, SceneToolCall


class SynthesisOutcomeTests(unittest.TestCase):
    def test_tool_paths_do_not_fabricate_completed_work_even_with_old_flag(self):
        for name in ("edit", "write", "apply_patch", "read"):
            for status in ("success", "error", "unknown"):
                with self.subTest(name=name, status=status):
                    scene = Scene(
                        scene_id="s:scene:e", session_id="s", turn=1, kind="message_exchange",
                        source_event_ids=["e"], extraction_user_text="Fix the login button.",
                        extraction_assistant_text="Investigating the login button.",
                        tool_calls=[SceneToolCall(name=name, status=status, file_paths=["app/auth.py"])],
                        ts="2026-04-09T00:00:00+00:00",
                    )
                    extracted = [{"text": "The login button fix is being investigated.", "stream": "rough"}]
                    with (
                        patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()),
                        patch("app.save_pipeline.pipeline.extract_atomic_memories", return_value=extracted),
                        patch("app.retrieval_pipeline.config.load_settings", return_value={"synthesize_implementation_outcomes": True}),
                        patch("app.save_pipeline.pipeline.embed", return_value=[]),
                        patch("app.save_pipeline.pipeline.append_memories", side_effect=lambda records: records),
                        patch("app.save_pipeline.pipeline.append_scene"),
                        patch("app.save_pipeline.pipeline.append_memory_notes") as export,
                    ):
                        result = run_memory_pipeline_outcome("s", 1, scene.extraction_user_text, scene.extraction_assistant_text, scene=scene)
                    self.assertEqual([memory["text"] for memory in result["records"]], [extracted[0]["text"]])
                    self.assertEqual(scene.tool_calls[0].file_paths, ["app/auth.py"])
                    export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
