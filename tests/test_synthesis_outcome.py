import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from app.save_pipeline.pipeline import run_memory_pipeline_outcome
from app.storage.models import Scene, SceneToolCall


def _make_scene(tool_calls=None):
    return Scene(
        scene_id="s1:scene:e-1",
        session_id="s1",
        turn=1,
        kind="message_exchange",
        scene_seq=1,
        start_event_seq=1,
        end_event_seq=1,
        anchor_event_id="e-1",
        source_event_ids=["e-1"],
        raw_events=[],
        messages=[],
        tool_calls=tool_calls or [],
        extraction_user_text="fix the login button",
        extraction_assistant_text="updated auth.py",
        used_context_fallback=False,
        ts="2026-04-09T00:00:00+00:00",
    )


class SynthesisOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.verifier = Mock()
        self.verifier.verify_memory.return_value = Mock(verified=False, confidence=0.0)

    def _run(self, scene, settings=None, extracted=None):
        if extracted is None:
            extracted = [{"text": "Fixed the login button.", "stream": "rough", "type": None}]
        with ExitStack() as stack:
            stack.enter_context(patch("app.save_pipeline.pipeline.get_extraction_adapter", return_value=object()))
            stack.enter_context(
                patch(
                    "app.save_pipeline.pipeline.extract_atomic_memories",
                    return_value=extracted,
                )
            )
            stack.enter_context(patch("app.save_pipeline.pipeline.embed", return_value=[]))
            stack.enter_context(patch("app.save_pipeline.pipeline.get_verifier", return_value=self.verifier))
            stack.enter_context(patch("app.save_pipeline.pipeline.append_memories"))
            stack.enter_context(patch("app.save_pipeline.pipeline.append_memory_notes"))
            if settings is not None:
                stack.enter_context(patch("app.retrieval_pipeline.config.load_settings", return_value=settings))

            return run_memory_pipeline_outcome(
                session_id="s1",
                turn=1,
                user_text=scene.extraction_user_text if scene else "fix the login button",
                assistant_text=scene.extraction_assistant_text if scene else "updated auth.py",
                source_event_ids=scene.source_event_ids if scene else ["e-1"],
                scene=scene,
            )

    def test_no_tool_calls_no_synthesis(self):
        scene = _make_scene()
        outcome = self._run(scene)
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("Modified files:", texts[0])

    def test_paths_already_mentioned_no_synthesis(self):
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="edit", file_paths=["app/auth.py"])]
        )
        outcome = self._run(
            scene,
            settings={"synthesize_implementation_outcomes": True},
            extracted=[{"text": "Fixed the login button in app/auth.py.", "stream": "rough", "type": None}],
        )
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("Modified files:", texts[0])

    def test_unmentioned_paths_synthesizes_memory(self):
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="edit", file_paths=["app/new_feature.py"])]
        )
        outcome = self._run(scene, settings={"synthesize_implementation_outcomes": True})
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 2)
        self.assertIn("Fixed the login button.", texts)
        self.assertIn("Modified files: app/new_feature.py", texts)

    def test_disabled_flag_no_synthesis(self):
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="edit", file_paths=["app/new_feature.py"])]
        )
        outcome = self._run(scene, settings={"synthesize_implementation_outcomes": False})
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("Modified files:", texts[0])

    def test_read_only_tool_calls_do_not_synthesize(self):
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="read", file_paths=["app/auth.py"])]
        )
        outcome = self._run(scene, settings={"synthesize_implementation_outcomes": True})
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("Modified files:", texts[0])

    def test_duplicate_paths_across_mutating_calls_deduplicated(self):
        scene = _make_scene(
            tool_calls=[
                SceneToolCall(name="edit", file_paths=["app/auth.py"]),
                SceneToolCall(name="write", file_paths=["app/auth.py"]),
            ]
        )
        outcome = self._run(scene, settings={"synthesize_implementation_outcomes": True})
        texts = [r["text"] for r in outcome["records"]]
        outcome_texts = [t for t in texts if t.startswith("Modified files:")]
        self.assertEqual(len(outcome_texts), 1)
        self.assertEqual(outcome_texts[0], "Modified files: app/auth.py")

    def test_only_unmentioned_paths_are_synthesized(self):
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="edit", file_paths=["app/a.py", "app/b.py"])]
        )
        outcome = self._run(
            scene,
            settings={"synthesize_implementation_outcomes": True},
            extracted=[{"text": "Fixed the issue in app/a.py.", "stream": "rough", "type": None}],
        )
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 2)
        self.assertIn("Modified files: app/b.py", texts)
        self.assertNotIn("Modified files: app/a.py, app/b.py", texts)

    def test_synthesized_memory_has_correct_fields(self):
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="edit", file_paths=["app/foo.py"])]
        )
        outcome = self._run(scene, settings={"synthesize_implementation_outcomes": True})
        synth = [r for r in outcome["records"] if "Modified files:" in r["text"]][0]
        self.assertEqual(synth["source_type"], "system")
        self.assertEqual(synth["source_reliability"], 1.0)
        self.assertEqual(synth["speaker_focus"], "system")
        self.assertEqual(synth["memory_kind"], "outcome")

    def test_scene_none_no_synthesis(self):
        outcome = self._run(scene=None, settings={"synthesize_implementation_outcomes": True})
        texts = [r["text"] for r in outcome["records"]]
        self.assertEqual(len(texts), 1)
        self.assertNotIn("Modified files:", texts[0])

    def test_many_paths_truncated_to_eight(self):
        many_paths = [f"app/module_{i}.py" for i in range(12)]
        scene = _make_scene(
            tool_calls=[SceneToolCall(name="edit", file_paths=many_paths)]
        )
        outcome = self._run(scene, settings={"synthesize_implementation_outcomes": True})
        texts = [r["text"] for r in outcome["records"]]
        synth = [t for t in texts if t.startswith("Modified files:")][0]
        self.assertEqual(len([t for t in texts if t.startswith("Modified files:")]), 1)
        parts = synth.replace("Modified files: ", "").split(", ")
        self.assertEqual(len(parts), 8)
        self.assertIn("app/module_0.py", parts)
        self.assertIn("app/module_5.py", parts)
        self.assertNotIn("app/module_8.py", parts)
        self.assertNotIn("app/module_9.py", parts)


if __name__ == "__main__":
    unittest.main()
