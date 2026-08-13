import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import app.storage.scenes as scenes
from app.save_pipeline.pipeline import get_scene_context


def _sample_scene() -> dict:
    return {
        "scene_id": "s1:scene:e-1",
        "session_id": "s1",
        "turn": 1,
        "kind": "message_exchange",
        "scene_seq": 7,
        "start_event_seq": 7,
        "end_event_seq": 7,
        "anchor_event_id": "e-1",
        "source_event_ids": ["e-1"],
        "raw_events": [],
        "tool_calls": [
            {
                "name": "read",
                "call_id": "call-1",
                "status": "success",
                "summary": "Read app/storage/memories.py",
                "file_paths": ["app/storage/memories.py"],
                "excerpt": "compact output",
                "event_id": "e-1",
            }
        ],
        "messages": [
            {"role": "user", "content": "How should dedupe work?", "message_id": "u1", "event_id": None},
            {"role": "assistant", "content": "Use session_id and event_id.", "message_id": "a1", "event_id": "e-1"},
        ],
        "extraction_user_text": "How should dedupe work?",
        "extraction_assistant_text": "Use session_id and event_id.",
        "used_context_fallback": False,
        "ts": "2026-04-09T00:00:00+00:00",
    }


def _sample_scene_with_seq(scene_id: str, seq: int) -> dict:
    scene = _sample_scene()
    scene["scene_id"] = scene_id
    scene["scene_seq"] = seq
    scene["start_event_seq"] = seq
    scene["end_event_seq"] = seq
    scene["anchor_event_id"] = f"e-{seq}"
    scene["source_event_ids"] = [f"e-{seq}"]
    scene["raw_events"] = []
    scene["tool_calls"] = [{"name": "read", "summary": f"Read file {seq}", "event_id": f"e-{seq}"}]
    return scene


class SceneStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        scenes._REPO_CACHE = None
        scenes._REPO_CACHE_KEY = None

    def test_json_and_sqlite_scene_repository_parity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            json_file = tmp_path / "scenes.json"
            sqlite_file = tmp_path / "memory_store.db"
            record = _sample_scene()

            with patch.object(scenes, "SCENES_FILE", json_file):
                json_repo = scenes.JsonSceneRepository()
                sqlite_repo = scenes.SqliteSceneRepository(sqlite_file)
                json_repo.append_scenes([record])
                sqlite_repo.append_scenes([record])

                json_recent = json_repo.get_recent_scenes(limit=1, session_id="s1")
                sqlite_recent = sqlite_repo.get_recent_scenes(limit=1, session_id="s1")

            self.assertEqual(json_recent[0]["scene_id"], sqlite_recent[0]["scene_id"])
            self.assertEqual(json_recent[0]["messages"], sqlite_recent[0]["messages"])
            self.assertEqual(json_recent[0]["scene_seq"], sqlite_recent[0]["scene_seq"])
            self.assertEqual(json_recent[0]["raw_events"], sqlite_recent[0]["raw_events"])
            self.assertEqual(json_recent[0]["tool_calls"], sqlite_recent[0]["tool_calls"])

    def test_scene_helpers_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"

            with patch.object(scenes, "_resolve_sqlite_path", return_value=sqlite_file), patch.object(
                scenes, "_resolve_backend", return_value="sqlite"
            ):
                scenes.append_scene(_sample_scene())
                loaded = scenes.get_scene("s1:scene:e-1")

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.scene_id, "s1:scene:e-1")
            self.assertEqual(len(loaded.messages), 2)
            self.assertEqual(loaded.scene_seq, 7)
            self.assertEqual(loaded.raw_events, [])
            self.assertEqual(loaded.tool_calls[0].name, "read")

    def test_get_session_scenes_reconstructs_order_from_scene_sequence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"

            with patch.object(scenes, "_resolve_sqlite_path", return_value=sqlite_file), patch.object(
                scenes, "_resolve_backend", return_value="sqlite"
            ):
                scenes.append_scene(_sample_scene_with_seq("s1:scene:e-2", 2))
                scenes.append_scene(_sample_scene_with_seq("s1:scene:e-1", 1))
                loaded = scenes.get_session_scenes("s1")

            self.assertEqual([scene.scene_seq for scene in loaded], [1, 2])
            self.assertEqual([scene.tool_calls[0].event_id for scene in loaded], ["e-1", "e-2"])

    def test_sqlite_repository_migrates_legacy_scene_table_before_indexing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            with sqlite3.connect(sqlite_file) as conn:
                conn.execute(
                    """
                    CREATE TABLE scenes (
                        scene_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        anchor_event_id TEXT,
                        ts TEXT NOT NULL,
                        source_event_ids_json TEXT NOT NULL,
                        messages_json TEXT NOT NULL,
                        extraction_user_text TEXT NOT NULL,
                        extraction_assistant_text TEXT NOT NULL,
                        used_context_fallback INTEGER NOT NULL
                    )
                    """
                )

            repo = scenes.SqliteSceneRepository(sqlite_file)
            repo.append_scenes([_sample_scene_with_seq("s1:scene:e-1", 1)])

            loaded = repo.get_session_scenes("s1")
            self.assertEqual([scene["scene_seq"] for scene in loaded], [1])

    def test_sqlite_db_has_human_readable_scene_views(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            repo = scenes.SqliteSceneRepository(sqlite_file)
            repo.append_scenes([_sample_scene_with_seq("s1:scene:e-1", 1)])

            with sqlite3.connect(sqlite_file) as conn:
                conn.row_factory = sqlite3.Row
                readable = conn.execute("SELECT * FROM readable_scenes WHERE scene_id = ?", ("s1:scene:e-1",)).fetchone()
                timeline = conn.execute("SELECT * FROM conversation_timeline WHERE conversation_id = ?", ("s1",)).fetchone()

            self.assertIsNotNone(readable)
            self.assertIsNotNone(timeline)
            assert readable is not None
            assert timeline is not None
            self.assertEqual(readable["conversation_id"], "s1")
            self.assertEqual(readable["scene_seq"], 1)
            self.assertEqual(readable["raw_event_bytes"], 2)
            self.assertGreater(readable["tool_call_bytes"], 2)
            self.assertEqual(timeline["content_kind"], "message_exchange")
            self.assertGreater(timeline["tool_call_bytes"], 2)
            self.assertIn("How should dedupe work?", timeline["text_preview"])

    def test_get_scene_context_returns_one_complete_bounded_scene(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            repo = scenes.SqliteSceneRepository(sqlite_file)
            records = []
            for seq in range(1, 5):
                record = _sample_scene_with_seq(f"s1:scene:e-{seq}", seq)
                record["messages"][0]["content"] = f"User content for scene {seq}"
                record["messages"][1]["content"] = f"Assistant content for scene {seq}"
                record["extraction_user_text"] = record["messages"][0]["content"]
                record["extraction_assistant_text"] = record["messages"][1]["content"]
                records.append(record)

            long_content = "bounded-scene-3:" + ("x" * 10_000)
            records[2]["messages"][1]["content"] = long_content
            records[2]["extraction_assistant_text"] = long_content
            repo.append_scenes(records)

            with patch("app.save_pipeline.pipeline.get_scene", side_effect=lambda scene_id: scenes.Scene(**repo.get_scene(scene_id))):
                payload = get_scene_context("s1:scene:e-3")

            scene = payload["scene"]
            self.assertEqual(scene["scene_id"], "s1:scene:e-3")
            self.assertEqual(scene["scene_seq"], 3)
            self.assertEqual(scene["messages"][1]["content"], long_content)
            self.assertEqual(scene["extraction_assistant_text"], long_content)
            serialized = str(scene)
            self.assertNotIn("Assistant content for scene 1", serialized)
            self.assertNotIn("Assistant content for scene 2", serialized)
            self.assertNotIn("Assistant content for scene 4", serialized)


if __name__ == "__main__":
    unittest.main()
