import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import app.storage.scenes as scenes
from app.save_pipeline.pipeline import get_scene_context
from app.storage.models import SceneReference


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


def _complete_scene() -> dict:
    scene = _sample_scene()
    scene.update(
        {
            "scene_id": "s1:scene:e-8",
            "scene_seq": 8,
            "start_event_seq": 7,
            "end_event_seq": 8,
            "anchor_event_id": "e-8",
            "source_event_ids": ["e-7", "e-8"],
            "evidence_version": 1,
            "evidence_status": "complete",
            "missing_source_event_ids": [],
            "raw_events": [
                {
                    "seq": 7,
                    "session_id": "s1",
                    "event_id": "e-7",
                    "event_type": "user_message",
                    "payload": {"content": "How should dedupe work?"},
                },
                {
                    "seq": 8,
                    "session_id": "s1",
                    "event_id": "e-8",
                    "event_type": "assistant_message",
                    "payload": {"content": "Use session_id and event_id."},
                },
            ],
            "messages": [
                {"role": "user", "content": "How should dedupe work?", "message_id": "u1", "event_id": "e-7"},
                {"role": "assistant", "content": "Use session_id and event_id.", "message_id": "a1", "event_id": "e-8"},
            ],
            "tool_calls": [
                {
                    "name": "read",
                    "call_id": "call-1",
                    "status": "success",
                    "summary": "Read app/storage/memories.py",
                    "file_paths": ["app/storage/memories.py"],
                    "excerpt": "compact output",
                    "event_id": "e-8",
                }
            ],
        }
    )
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
            self.assertEqual(loaded.evidence_version, 0)
            self.assertEqual(loaded.evidence_status, "partial")
            self.assertEqual(loaded.tool_calls[0].name, "read")

    def test_sqlite_scene_writes_do_not_fall_back_to_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            with (
                patch.object(scenes, "_resolve_sqlite_path", return_value=sqlite_file),
                patch.object(scenes, "_resolve_backend", return_value="sqlite"),
                patch.object(scenes, "_resolve_read_fallback", return_value="json"),
                patch.object(scenes.SqliteSceneRepository, "__init__", side_effect=RuntimeError("sqlite unavailable")),
                patch.object(scenes.JsonSceneRepository, "append_scenes") as json_append,
            ):
                with self.assertRaisesRegex(RuntimeError, "sqlite unavailable"):
                    scenes.append_scene(_sample_scene())

            json_append.assert_not_called()

    def test_scene_reference_is_lightweight_and_has_evidence_contract(self):
        reference = SceneReference(
            scene_id="s1:scene:e-8",
            evidence_version=1,
            evidence_status="partial",
            missing_source_event_ids=["e-9"],
        )

        self.assertEqual(reference.model_dump(), {
            "scene_id": "s1:scene:e-8",
            "evidence_status": "partial",
            "evidence_version": 1,
            "missing_source_event_ids": ["e-9"],
        })

    def test_complete_v1_evidence_round_trips_in_json_and_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            record = _complete_scene()

            with patch.object(scenes, "SCENES_FILE", tmp_path / "scenes.json"):
                json_repo = scenes.JsonSceneRepository()
                json_repo.append_scenes([record])
                json_loaded = json_repo.get_scene(record["scene_id"])

            sqlite_repo = scenes.SqliteSceneRepository(tmp_path / "memory_store.db")
            sqlite_repo.append_scenes([record])
            sqlite_loaded = sqlite_repo.get_scene(record["scene_id"])

            assert json_loaded is not None
            assert sqlite_loaded is not None
            for loaded in (json_loaded, sqlite_loaded):
                self.assertEqual(loaded["evidence_version"], 1)
                self.assertEqual(loaded["evidence_status"], "complete")
                self.assertEqual(loaded["missing_source_event_ids"], [])
                self.assertEqual([event["event_id"] for event in loaded["raw_events"]], ["e-7", "e-8"])
                self.assertEqual([message["event_id"] for message in loaded["messages"]], ["e-7", "e-8"])

    def test_complete_v1_scene_requires_ordered_self_contained_evidence(self):
        invalid = _complete_scene()
        invalid["source_event_ids"] = ["e-8", "e-7"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with patch.object(scenes, "SCENES_FILE", tmp_path / "scenes.json"):
                with self.assertRaises(ValueError):
                    scenes.JsonSceneRepository().append_scenes([invalid])
            with self.assertRaises(ValueError):
                scenes.SqliteSceneRepository(tmp_path / "memory_store.db").append_scenes([invalid])

    def test_complete_v1_scene_rejects_malformed_or_unproven_evidence(self):
        malformed = _complete_scene()
        malformed["raw_events"] = list(malformed["raw_events"]) + ["not-an-event"]
        unproven_tool = _complete_scene()
        unproven_tool["tool_calls"] = [{"name": "read", "event_id": None}]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for invalid in (malformed, unproven_tool):
                with patch.object(scenes, "SCENES_FILE", tmp_path / "scenes.json"):
                    with self.assertRaises(ValueError):
                        scenes.JsonSceneRepository().append_scenes([invalid])
                with self.assertRaises(ValueError):
                    scenes.SqliteSceneRepository(tmp_path / "memory_store.db").append_scenes([invalid])

    def test_partial_v1_scene_round_trips_with_missing_ids_in_both_backends(self):
        partial = _complete_scene()
        partial.update(
            {
                "evidence_status": "partial",
                "missing_source_event_ids": ["e-missing"],
                "source_event_ids": ["e-8"],
                "raw_events": [partial["raw_events"][1]],
                "start_event_seq": 8,
                "messages": [partial["messages"][1]],
                "tool_calls": [],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with patch.object(scenes, "SCENES_FILE", tmp_path / "scenes.json"):
                json_repo = scenes.JsonSceneRepository()
                json_repo.append_scenes([partial])
                json_scene = json_repo.get_scene(partial["scene_id"])
                json_ref = json_repo.get_scene_references([partial["scene_id"]])[0]
            sqlite_repo = scenes.SqliteSceneRepository(tmp_path / "memory_store.db")
            sqlite_repo.append_scenes([partial])
            sqlite_scene = sqlite_repo.get_scene(partial["scene_id"])
            sqlite_ref = sqlite_repo.get_scene_references([partial["scene_id"]])[0]

        self.assertEqual(json_scene, sqlite_scene)
        self.assertEqual(json_ref, sqlite_ref)
        self.assertEqual(json_ref["evidence_status"], "partial")
        self.assertEqual(json_ref["evidence_version"], 1)
        self.assertEqual(json_ref["missing_source_event_ids"], ["e-missing"])

    def test_complete_scene_cannot_be_downgraded_to_partial_in_either_backend(self):
        complete = _complete_scene()
        downgrade = dict(complete)
        downgrade.update(
            {
                "evidence_version": 0,
                "evidence_status": "partial",
                "missing_source_event_ids": [],
                "raw_events": [],
                "messages": [],
                "tool_calls": [],
            }
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with patch.object(scenes, "SCENES_FILE", tmp_path / "scenes.json"):
                json_repo = scenes.JsonSceneRepository()
                json_repo.append_scenes([complete])
                json_repo.append_scenes([downgrade])
                json_loaded = json_repo.get_scene(complete["scene_id"])

            sqlite_repo = scenes.SqliteSceneRepository(tmp_path / "memory_store.db")
            sqlite_repo.append_scenes([complete])
            sqlite_repo.append_scenes([downgrade])
            sqlite_loaded = sqlite_repo.get_scene(complete["scene_id"])

            assert json_loaded is not None
            assert sqlite_loaded is not None
            self.assertEqual(json_loaded["evidence_status"], "complete")
            self.assertEqual(sqlite_loaded["evidence_status"], "complete")
            self.assertEqual(json_loaded["raw_events"], sqlite_loaded["raw_events"])

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
