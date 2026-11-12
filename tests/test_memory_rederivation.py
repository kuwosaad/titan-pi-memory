import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.save_pipeline.pipeline as pipeline
from app.storage.memories import SqliteMemoryRepository
from app.storage.models import Scene, SceneMessage


class MemoryRederivationTests(unittest.TestCase):
    def test_additive_stable_derivations_preserve_scene_and_time(self):
        scene = Scene(scene_id="scene-original", session_id="session-a", turn=7,
                      kind="message_exchange", ts="2021-08-09T10:11:12Z",
                      source_event_ids=["event-user"],
                      messages=[SceneMessage(role="user", content="I started ceramics and now visit twice monthly.", event_id="event-user")],
                      extraction_user_text="I started ceramics and now visit twice monthly.",
                      extraction_assistant_text="")
        old = {"text": "The user started ceramics.", "type": "fact", "source": "user", "speaker_focus": "user"}
        new = {"text": "The user visits ceramics twice monthly.", "type": "fact", "source": "user", "speaker_focus": "user"}
        other = {"text": "The user chose a ceramics studio.", "type": "fact", "source": "user", "speaker_focus": "user"}
        with tempfile.TemporaryDirectory() as directory:
            repo = SqliteMemoryRepository(Path(directory) / "memories.db")
            with (patch("app.storage.memories.get_memory_repository", return_value=repo),
                  patch.object(pipeline, "get_extraction_adapter", return_value=object()),
                  patch.object(pipeline, "extract_atomic_memories") as extract,
                  patch.object(pipeline, "embed", return_value=[]),
                  patch.object(pipeline, "append_scene") as append_scene,
                  patch("app.save_pipeline.extraction.comparison.correction_candidates", return_value=[])):
                def save(items, version=None):
                    extract.return_value = items
                    return pipeline.run_memory_pipeline_outcome(
                        session_id=scene.session_id, turn=scene.turn,
                        user_text=scene.extraction_user_text, assistant_text="",
                        source_event_ids=scene.source_event_ids, scene=scene,
                        persist_scene=False, derivation_version=version)["records"]

                baseline = save([old])
                revised = save([old, new, other], "repair-v1")
                retry = save([other, new, old], "repair-v1")
                changed = save([dict(new, text="The user visits ceramics three times monthly.")], "repair-v1")
                scene = scene.model_copy(update={"scene_id": "scene-second", "source_event_ids": ["event-second"],
                                                "messages": [SceneMessage(role="user", content=scene.extraction_user_text, event_id="event-second")]})
                distinct = save([new], "repair-v1")
                append_scene.assert_not_called()

            stored = repo.load_all_memories()
            self.assertEqual(len(stored), 5)
            self.assertEqual(baseline[0]["id"], "scene-original:memory:0")
            self.assertEqual(revised[0]["id"], baseline[0]["id"])
            self.assertEqual({r["text"]: r["id"] for r in revised}, {r["text"]: r["id"] for r in retry})
            self.assertNotEqual(changed[0]["id"], revised[1]["id"])
            self.assertEqual({r["scene_id"] for r in stored}, {"scene-original", "scene-second"})
            self.assertNotEqual(distinct[0]["id"], revised[1]["id"])
            self.assertEqual({r["ts"] for r in stored if r["id"] != baseline[0]["id"]}, {scene.ts})
            self.assertEqual(distinct[0]["source_event_ids"], ["event-second"])
            self.assertTrue(all(r["source_event_ids"] == ["event-user"] for r in stored if r["scene_id"] == "scene-original"))

    def test_version_requires_original_scene_and_nonempty_string(self):
        for version in ("", " ", 1):
            with self.subTest(version=version), self.assertRaises(ValueError):
                pipeline.run_memory_pipeline_outcome("s", 1, "A substantive statement.", "", derivation_version=version)
        with self.assertRaises(ValueError):
            pipeline.run_memory_pipeline_outcome("s", 1, "A substantive statement.", "", derivation_version="v1")


if __name__ == "__main__":
    unittest.main()
