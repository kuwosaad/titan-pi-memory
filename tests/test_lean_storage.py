"""Exact evidence retries and backend failures, using isolated stores only."""

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage import memories


def _record(turn=1, **changes):
    record = memories.create_memory_record(
        session_id="retry-session", turn=turn, index=0,
        text="Use the stable event ID.", user_text="How should replay work?",
        assistant_text="Use the stable event ID.", scene_id="scene-1",
        source_event_ids=["user-1", "assistant-1"], memory_type="decision",
        memory_kind="decision", speaker_focus="assistant", source_type="assistant",
        embedding=[1.0, 0.5],
    )
    return {**record, **changes}


class LeanStorageTests(unittest.TestCase):
    def _repositories(self, root):
        return (
            memories.JsonMemoryRepository(root / "memories.json"),
            memories.SqliteMemoryRepository(root / "memories.db"),
        )

    def test_fresh_turn_retries_of_exact_evidence_keep_one_original_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            for repo in self._repositories(Path(tmp)):
                with self.subTest(backend=type(repo).__name__):
                    original = _record()
                    repo.append_memories([original])
                    retry = _record(2, embedding=[0.4, 0.8])
                    accepted = repo.append_memories([retry, retry, _record(3)])
                    self.assertEqual([record["id"] for record in accepted], [original["id"]] * 3)
                    self.assertEqual(repo.query_by_ids([record["id"] for record in accepted])[original["id"]]["text"],
                                     original["text"])
                    loaded = repo.load_all_memories()
                    self.assertEqual(len(loaded), 1)
                    self.assertEqual(loaded[0]["id"], original["id"])
                    self.assertEqual(loaded[0]["embedding"], original["embedding"])
                    self.assertEqual(loaded[0]["provenance"], original["provenance"])

    def test_same_id_retry_returns_the_persisted_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            for repo in self._repositories(Path(tmp)):
                original = _record(ts="2026-09-05T00:00:00+00:00", h=0.7, tau=0.8)
                repo.append_memories([original])
                accepted = repo.append_memories([{**original, "ts": "2026-09-06T00:00:00+00:00", "h": 0.0}])
                self.assertEqual(accepted[0]["ts"], original["ts"])
                self.assertEqual(accepted[0]["h"], original["h"])

    def test_dedup_preserves_other_evidence_claims_context_and_metadata(self):
        changes = (
            {"scene_id": "scene-2"},
            {"source_event_ids": ["user-2", "assistant-2"]},
            {"text": "Use a different event ID."},
            {"provenance": {"user": "New context", "assistant": "Use the stable event ID."}},
            {"session_id": "another-session"},
            {"stream": "learnings"},
            {"verification_status": "verified"},
            {"source_reliability": 0.9},
        )
        with tempfile.TemporaryDirectory() as tmp:
            for repo in self._repositories(Path(tmp)):
                with self.subTest(backend=type(repo).__name__):
                    records = [_record()]
                    records.extend(_record(turn, **change) for turn, change in enumerate(changes, 2))
                    repo.append_memories(records)
                    self.assertEqual(repo.get_memory_count(), len(records))

    def test_no_evidence_address_does_not_dedup_repeated_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            for repo in self._repositories(Path(tmp)):
                repo.append_memories([
                    _record(scene_id=None, source_event_ids=[]),
                    _record(2, scene_id=None, source_event_ids=[]),
                ])
                self.assertEqual(repo.get_memory_count(), 2)

    def test_event_only_retries_dedup_without_merging_other_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            for repo in self._repositories(Path(tmp)):
                repo.append_memories([
                    _record(scene_id=None), _record(2, scene_id=None),
                    _record(3, scene_id=None, source_event_ids=["new-event"]),
                ])
                self.assertEqual(repo.get_memory_count(), 2)

    def test_same_evidence_in_separate_namespaces_remains_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("agent-a", "agent-b"):
                for repo in self._repositories(root / name):
                    repo.append_memories([_record()])
                    self.assertEqual(repo.get_memory_count(), 1)

    def test_collision_rejects_whole_batch_even_after_exact_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            for repo in self._repositories(Path(tmp)):
                original = _record()
                repo.append_memories([original])
                with self.assertRaisesRegex(ValueError, "memory collision"):
                    repo.append_memories([
                        _record(2), _record(3, text="A new claim"),
                        _record(2, text="An inconsistent claim under the retry ID"),
                    ])
                self.assertEqual(repo.get_memory_count(), 1)
                self.assertEqual(repo.load_all_memories()[0]["id"], original["id"])

    def test_concurrent_fresh_ids_serialize_exact_evidence_check_and_insert(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memories.db"
            repositories = [memories.SqliteMemoryRepository(path) for _ in range(4)]
            start = threading.Barrier(len(repositories))
            errors = []

            def append(repo, turn):
                try:
                    start.wait(timeout=5)
                    repo.append_memories([_record(turn)])
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=append, args=(repo, turn))
                       for turn, repo in enumerate(repositories, 1)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(repositories[0].get_memory_count(), 1)

    def test_sqlite_corruption_never_switches_to_writable_json_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sqlite_path = root / "corrupt.db"
            sqlite_path.write_bytes(b"not a sqlite database")
            json_path = root / "memories.json"
            json_path.write_text(json.dumps([_record()]), encoding="utf-8")
            original_json = json_path.read_bytes()
            with patch.object(memories, "_refresh_json_paths"), \
                 patch.object(memories, "_resolve_backend", return_value="sqlite"), \
                 patch.object(memories, "_resolve_sqlite_path", return_value=sqlite_path), \
                 patch.object(memories, "_resolve_read_fallback", return_value="json"), \
                 patch.object(memories, "MEMORIES_FILE", json_path), \
                 patch.object(memories, "_REPO_CACHE", None), \
                 patch.object(memories, "_REPO_CACHE_KEY", None):
                with self.assertRaises(sqlite3.DatabaseError):
                    memories.append_memories([_record(2)])
                self.assertIsNone(memories._REPO_CACHE)
            self.assertEqual(json_path.read_bytes(), original_json)

    def test_read_only_repository_never_initializes_missing_foreign_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "foreign-agent"
            repo = memories.SqliteMemoryRepository(root / "memories.db", initialize=False)
            self.assertFalse(root.exists())
            with self.assertRaises(sqlite3.OperationalError):
                repo.get_memory_count()
            self.assertFalse(root.exists())

    def test_new_records_have_no_lnn_calculation_but_legacy_state_stays_readable(self):
        record = _record(memory_kind="decision", source_reliability=0.9)
        self.assertNotIn("h", record)
        self.assertNotIn("tau", record)
        with tempfile.TemporaryDirectory() as tmp:
            repo = memories.SqliteMemoryRepository(Path(tmp) / "memories.db")
            repo.append_memories([{**record, "h": 0.42, "tau": 0.73}])
            stored = repo.query_by_ids([record["id"]])[record["id"]]
            self.assertEqual(stored["h"], 0.42)
            self.assertEqual(stored["tau"], 0.73)

    def test_migration_preserves_historical_ids_state_and_pattern_evidence_pointers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "memories.json"
            db_path = root / "memories.db"
            originals = [
                _record(h=0.2, tau=0.4, embedding=[1.0, 0.5]),
                _record(2, h=0.8, tau=0.9, embedding=[0.5, 1.0]),
            ]
            source.write_text(json.dumps(originals), encoding="utf-8")
            original_bytes = source.read_bytes()
            repo = memories.SqliteMemoryRepository(db_path)
            with repo._connect() as conn:
                conn.execute("""
                    INSERT INTO patterns (id, title, kind, scope, summary,
                                          recommended_behavior, created_at, updated_at)
                    VALUES ('pattern-1', 'Replay', 'workflow', 'global', 'Use stable IDs',
                            'Reuse stable IDs', '2026-11-11', '2026-11-11')
                """)
                conn.executemany(
                    "INSERT INTO pattern_evidence (pattern_id, memory_id, scene_id) VALUES ('pattern-1', ?, ?)",
                    [(record["id"], record["scene_id"]) for record in originals],
                )
            with patch.object(memories, "_refresh_json_paths"), patch.object(memories, "MEMORIES_FILE", source):
                first = memories.migrate_json_to_sqlite(sqlite_path=db_path)
                second = memories.migrate_json_to_sqlite(sqlite_path=db_path)
            self.assertEqual((first["inserted"], first["updated"], first["skipped"]), (2, 0, 0))
            self.assertEqual((second["inserted"], second["updated"], second["skipped"]), (0, 2, 0))
            loaded = repo.query_by_ids([record["id"] for record in originals])
            self.assertEqual(set(loaded), {record["id"] for record in originals})
            for original in originals:
                for field in ("id", "turn", "ts", "scene_id", "source_event_ids", "provenance", "embedding", "h", "tau"):
                    self.assertEqual(loaded[original["id"]][field], original[field], field)
            with repo._connect() as conn:
                pointers = conn.execute("""
                    SELECT e.memory_id, m.id FROM pattern_evidence e
                    LEFT JOIN memories m ON m.id = e.memory_id
                    WHERE e.pattern_id = 'pattern-1'
                """).fetchall()
            self.assertEqual({row["id"] for row in pointers}, set(loaded))
            self.assertEqual(source.read_bytes(), original_bytes)
            accepted = repo.append_memories([_record(3)])
            self.assertIn(accepted[0]["id"], loaded)
            self.assertEqual(repo.get_memory_count(), 2)

    def test_historical_import_still_atomically_rejects_immutable_id_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = memories.SqliteMemoryRepository(Path(tmp) / "memories.db")
            original = _record()
            repo._import_memories([original])
            with self.assertRaisesRegex(ValueError, "memory collision"):
                repo._import_memories([_record(2), {**original, "text": "Conflicting claim"}])
            self.assertEqual(repo.get_memory_count(), 1)
            self.assertEqual(repo.query_by_ids([original["id"]])[original["id"]]["text"], original["text"])

    def test_migration_per_record_recovery_preserves_ids_and_skips_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "memories.json"
            db_path = root / "memories.db"
            original, historical_retry = _record(), _record(2)
            repo = memories.SqliteMemoryRepository(db_path)
            repo.append_memories([original])
            source.write_text(json.dumps([
                original, historical_retry, {**original, "text": "Conflicting historical claim"},
            ]), encoding="utf-8")
            with patch.object(memories, "_refresh_json_paths"), patch.object(memories, "MEMORIES_FILE", source):
                report = memories.migrate_json_to_sqlite(sqlite_path=db_path)
            self.assertEqual((report["inserted"], report["updated"], report["skipped"]), (1, 1, 1))
            loaded = repo.query_by_ids([original["id"], historical_retry["id"]])
            self.assertEqual(set(loaded), {original["id"], historical_retry["id"]})
            self.assertEqual(loaded[original["id"]]["text"], original["text"])


if __name__ == "__main__":
    unittest.main()
