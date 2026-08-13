import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import app.storage.memories as memories
from app.storage.repository import CandidateFilters


def _sample_records() -> list[dict]:
    return [
        {
            "id": "s1:1:0",
            "text": "Use event_id + session_id for idempotency.",
            "type": "decision",
            "stream": "learnings",
            "embedding": [1.0, 0.0, 0.5],
            "ts": "2026-02-01T00:00:00+00:00",
            "session_id": "s1",
            "turn": 1,
            "scene_id": "s1:scene:e-1",
            "provenance": {"user": "u1", "assistant": "a1"},
            "source_event_ids": ["e-1"],
            "source_type": "user",
            "source_reliability": 0.9,
            "verification_status": "unverified",
            "fallback_generated": False,
        },
        {
            "id": "s1:2:0",
            "text": "Track retry queue dedupe failures.",
            "type": "fact",
            "stream": "rough",
            "embedding": [0.3, 0.2, 0.1],
            "ts": "2026-02-02T00:00:00+00:00",
            "session_id": "s1",
            "turn": 2,
            "scene_id": "s1:scene:e-2",
            "provenance": {"user": "u2", "assistant": "a2"},
            "source_event_ids": ["e-2"],
            "source_type": "assistant",
            "source_reliability": 0.6,
            "verification_status": "unverified",
            "fallback_generated": False,
        },
    ]


class MemoryStoreSqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        memories._REPO_CACHE = None
        memories._REPO_CACHE_KEY = None

    def test_get_recent_memories_normalizes_legacy_memory_kind(self):
        class StubRepo:
            def get_recent_memories(self, limit: int = 8, session_id: str | None = None):
                return [
                    {
                        "id": "s1:1:0",
                        "text": "Constraint: the pipeline must stay idempotent.",
                        "type": "constraint",
                        "stream": "learnings",
                        "ts": "2026-02-01T00:00:00+00:00",
                        "session_id": "s1",
                        "turn": 1,
                        "provenance": {"user": "u1", "assistant": "a1"},
                        "source_event_ids": ["e-1"],
                        "source_type": "mixed",
                        "source_reliability": 0.9,
                        "verification_status": "unverified",
                        "fallback_generated": False,
                        "speaker_focus": "system",
                        "memory_kind": "constraint",
                    }
                ]

        with patch.object(memories, "get_memory_repository", return_value=StubRepo()):
            recent = memories.get_recent_memories(limit=1)

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].memory_kind, "decision")

    def test_get_recent_memories_normalizes_legacy_speaker_focus(self):
        class StubRepo:
            def get_recent_memories(self, limit: int = 8, session_id: str | None = None):
                return [
                    {
                        "id": "s1:1:0",
                        "text": "The user and agent worked together on the graph.",
                        "type": "fact",
                        "stream": "rough",
                        "ts": "2026-02-01T00:00:00+00:00",
                        "session_id": "s1",
                        "turn": 1,
                        "provenance": {"user": "u1", "assistant": "a1"},
                        "source_event_ids": ["e-1"],
                        "source_type": "mixed",
                        "source_reliability": 0.9,
                        "verification_status": "unverified",
                        "fallback_generated": False,
                        "speaker_focus": "mixed",
                        "memory_kind": "workflow",
                    }
                ]

        with patch.object(memories, "get_memory_repository", return_value=StubRepo()):
            recent = memories.get_recent_memories(limit=1)

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].speaker_focus, "shared")

    def test_blob_pack_unpack_roundtrip(self):
        vector = np.array([0.1, -0.2, 0.3], dtype=np.float32)
        blob, dim, dtype = memories.pack_embedding(vector)
        self.assertEqual(dim, 3)
        self.assertEqual(dtype, "f32")
        decoded = memories.unpack_embedding(blob, dim, dtype)
        self.assertTrue(np.allclose(decoded, vector, atol=1e-7))

    def test_json_and_sqlite_repository_parity_for_recent_and_count(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            json_file = tmp_path / "memories.json"
            sqlite_file = tmp_path / "memory_store.db"
            records = _sample_records()

            with patch.object(memories, "MEMORIES_FILE", json_file):
                json_repo = memories.JsonMemoryRepository()
                sqlite_repo = memories.SqliteMemoryRepository(sqlite_file)
                json_repo.append_memories(records)
                sqlite_repo.append_memories(records)

                self.assertEqual(json_repo.get_memory_count(), sqlite_repo.get_memory_count())
                json_recent = [item["id"] for item in json_repo.get_recent_memories(limit=2)]
                sqlite_recent = [item["id"] for item in sqlite_repo.get_recent_memories(limit=2)]
                self.assertEqual(json_recent, sqlite_recent)

    def test_sqlite_query_candidates_filters(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            repo = memories.SqliteMemoryRepository(sqlite_file)
            repo.append_memories(_sample_records())

            filters = CandidateFilters(
                recency_days=None,
                session_id="s1",
                session_bias=True,
                memory_types=["decision"],
                mode="learnings",
                min_reliability=0.5,
            )
            candidates = repo.query_candidates(filters)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["id"], "s1:1:0")
            self.assertEqual(candidates[0]["scene_id"], "s1:scene:e-1")

    def test_sqlite_db_has_human_readable_metadata_and_memory_view(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            repo = memories.SqliteMemoryRepository(sqlite_file)
            repo.append_memories(_sample_records())

            with sqlite3.connect(sqlite_file) as conn:
                conn.row_factory = sqlite3.Row
                metadata = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM metadata")}
                row = conn.execute("SELECT * FROM readable_memories WHERE memory_id = ?", ("s1:1:0",)).fetchone()

            self.assertEqual(metadata["schema_name"], "titan_memory_store")
            self.assertEqual(metadata["storage_model"], "scene_first")
            self.assertEqual(metadata["portable_unit"], "memory_store.db")
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["conversation_id"], "s1")
            self.assertEqual(row["memory_text"], "Use event_id + session_id for idempotency.")

    def test_locked_sqlite_backend_does_not_fall_back_to_empty_json(self):
        memories._REPO_CACHE = None
        memories._REPO_CACHE_KEY = None

        with patch.object(memories, "_resolve_backend", return_value="sqlite"), patch.object(
            memories,
            "_resolve_sqlite_path",
            return_value=Path("/tmp/locked-memory-store.db"),
        ), patch.object(memories, "_resolve_read_fallback", return_value="json"), patch.object(
            memories,
            "SqliteMemoryRepository",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                memories.get_memory_repository()

    def test_json_to_sqlite_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            json_file = tmp_path / "memories.json"
            sqlite_file = tmp_path / "memory_store.db"
            records = _sample_records()

            with patch.object(memories, "MEMORIES_FILE", json_file):
                memories.write_json(json_file, records)
                first = memories.migrate_json_to_sqlite(sqlite_path=sqlite_file)
                second = memories.migrate_json_to_sqlite(sqlite_path=sqlite_file)
                repo = memories.SqliteMemoryRepository(sqlite_file)

            self.assertEqual(first["inserted"], 2)
            self.assertEqual(first["updated"], 0)
            self.assertEqual(first["skipped"], 0)
            self.assertEqual(second["inserted"], 0)
            self.assertEqual(second["updated"], 2)
            self.assertEqual(repo.get_memory_count(), 2)


if __name__ == "__main__":
    unittest.main()
