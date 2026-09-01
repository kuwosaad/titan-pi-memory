import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.patterns.models import Pattern, PatternEvidence
from app.patterns.processing import PatternProcessingLedger
from app.patterns.store import PatternStore, PatternValidationError
from app.storage.memories import SqliteMemoryRepository
from app.storage.sqlite_schema import ensure_pattern_tables


def _memory_records() -> list[dict]:
    return [
        {
            "id": "s1:1:0",
            "text": "Pi extension changes should run TypeScript checks.",
            "type": "workflow",
            "stream": "learnings",
            "embedding": [1.0, 0.0],
            "ts": "2026-06-01T00:00:00+00:00",
            "session_id": "s1",
            "turn": 1,
            "scene_id": "scene-1",
            "provenance": {"user": "u1", "assistant": "a1"},
            "source_event_ids": ["e1"],
            "source_type": "mixed",
            "source_reliability": 0.9,
            "verification_status": "unverified",
            "fallback_generated": False,
        },
        {
            "id": "s2:1:0",
            "text": "After editing tools/pi_extension/index.ts, npx tsc caught export drift.",
            "type": "issue",
            "stream": "learnings",
            "embedding": [0.8, 0.2],
            "ts": "2026-06-02T00:00:00+00:00",
            "session_id": "s2",
            "turn": 1,
            "scene_id": "scene-2",
            "provenance": {"user": "u2", "assistant": "a2"},
            "source_event_ids": ["e2"],
            "source_type": "assistant",
            "source_reliability": 0.8,
            "verification_status": "unverified",
            "fallback_generated": False,
        },
        {
            "id": "s3:1:0",
            "text": "Sometimes docs-only Pi extension changes do not need compile checks.",
            "type": "fact",
            "stream": "rough",
            "embedding": [0.0, 1.0],
            "ts": "2026-06-03T00:00:00+00:00",
            "session_id": "s3",
            "turn": 1,
            "scene_id": "scene-3",
            "provenance": {"user": "u3", "assistant": "a3"},
            "source_event_ids": ["e3"],
            "source_type": "assistant",
            "source_reliability": 0.6,
            "verification_status": "unverified",
            "fallback_generated": False,
        },
    ]


def _pattern() -> Pattern:
    return Pattern(
        id="pattern-pi-tsc",
        title="Pi extension edits need TypeScript validation",
        kind="workflow",
        scope="repo",
        status="candidate",
        summary="Pi extension implementation changes have repeatedly needed TypeScript validation.",
        recommended_behavior="After changing tools/pi_extension/index.ts, run npx tsc --noEmit tools/pi_extension/index.ts before reporting done.",
        applies_when="The task edits Pi extension TypeScript code.",
        does_not_apply_when="The task only edits prose docs.",
        trigger_terms=["pi extension", "tools/pi_extension/index.ts", "typescript", "tsc"],
        confidence=0.74,
        actionability=0.9,
        retrieval_value=0.8,
    )


class PatternStoreTests(unittest.TestCase):
    def test_old_application_table_migrates_and_retains_compatibility_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            with sqlite3.connect(sqlite_file) as conn:
                conn.execute(
                    """
                    CREATE TABLE pattern_applications (
                        id TEXT PRIMARY KEY,
                        pattern_id TEXT NOT NULL,
                        query TEXT NOT NULL,
                        task_id TEXT,
                        retrieved_at TEXT NOT NULL,
                        was_used INTEGER,
                        outcome TEXT,
                        feedback TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO pattern_applications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("app-old", "pattern-old", "old query", None, "2026-06-01T00:00:00+00:00", 1, "worked", "legacy"),
                )

            store = PatternStore(sqlite_file)
            applications = store.list_applications()

            self.assertEqual(len(applications), 1)
            self.assertEqual(applications[0].id, "app-old")
            self.assertIsNone(applications[0].shown_at)
            self.assertIsNone(applications[0].used_at)
            self.assertIsNone(applications[0].outcome_observed_at)

    def test_pattern_tables_are_created_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            with sqlite3.connect(sqlite_file) as conn:
                ensure_pattern_tables(conn)
                ensure_pattern_tables(conn)
                table_names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pattern%'"
                    ).fetchall()
                }

            self.assertIn("patterns", table_names)
            self.assertIn("pattern_evidence", table_names)
            self.assertIn("pattern_applications", table_names)
            self.assertIn("pattern_mining_runs", table_names)
            self.assertIn("pattern_memory_processing", table_names)

    def test_create_list_evidence_and_update_status(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            store = PatternStore(sqlite_file)
            pattern = _pattern()
            evidence = [
                PatternEvidence(pattern_id=pattern.id, memory_id="s1:1:0", scene_id="scene-1", role="support", score=0.9),
                PatternEvidence(pattern_id=pattern.id, memory_id="s2:1:0", scene_id="scene-2", role="support", score=0.8),
                PatternEvidence(pattern_id=pattern.id, memory_id="s3:1:0", scene_id="scene-3", role="contradict", score=0.4),
            ]

            created = store.create_pattern(pattern, evidence, min_support_evidence=2)
            candidates = store.list_patterns(status="candidate")
            stored_evidence = store.list_evidence(pattern.id)
            accepted = store.update_status(pattern.id, "accepted")
            accepted_patterns = store.list_patterns(status="accepted")

            self.assertEqual(created.id, pattern.id)
            self.assertEqual([item.id for item in candidates], [pattern.id])
            self.assertEqual({item.role for item in stored_evidence}, {"support", "contradict"})
            self.assertEqual(accepted.status, "accepted")
            self.assertEqual([item.id for item in accepted_patterns], [pattern.id])

    def test_create_pattern_rejects_missing_evidence_memory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            store = PatternStore(sqlite_file)
            pattern = _pattern()

            with self.assertRaises(PatternValidationError):
                store.create_pattern(
                    pattern,
                    [PatternEvidence(pattern_id=pattern.id, memory_id="missing", role="support", score=0.5)],
                )

    def test_processing_ledger_marks_memories_by_processor_version_and_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            ledger = PatternProcessingLedger(sqlite_file)

            run = ledger.start_run(processor_version="pattern-miner-v1", processor_config_hash="abc", mode="backfill")
            marked = ledger.mark_processed(
                ["s1:1:0", "s2:1:0", "s2:1:0"],
                processor_version="pattern-miner-v1",
                processor_config_hash="abc",
                run_id=run.id,
                pattern_ids=["pattern-pi-tsc"],
            )
            status = ledger.status(processor_version="pattern-miner-v1", processor_config_hash="abc")
            next_version_status = ledger.status(processor_version="pattern-miner-v2", processor_config_hash="abc")
            unprocessed = ledger.list_unprocessed_memory_ids(
                processor_version="pattern-miner-v1",
                processor_config_hash="abc",
                limit=10,
            )

            self.assertEqual(marked, 2)
            self.assertEqual(status.memories_total, 3)
            self.assertEqual(status.processed_current, 2)
            self.assertEqual(status.unprocessed, 1)
            self.assertEqual(next_version_status.processed_current, 0)
            self.assertEqual(next_version_status.unprocessed, 3)
            self.assertEqual(unprocessed, ["s3:1:0"])


if __name__ == "__main__":
    unittest.main()
