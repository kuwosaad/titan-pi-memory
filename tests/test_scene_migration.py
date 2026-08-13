import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.runtime.context import reset_runtime_context_cache
from app.storage.scene_migration import backfill_scene_evidence
from tools.cli.titan import main


class SceneEvidenceMigrationTests(unittest.TestCase):
    def setUp(self):
        self._environment = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environment)
        reset_runtime_context_cache()

    def _use_runtime(self, root: Path, *, backend: str = "json", db_path: Path | None = None) -> None:
        os.environ["TITAN_HOME"] = str(root)
        os.environ["TITAN_BASE_DIR"] = str(root)
        os.environ["TITAN_AGENT_NAME"] = "opencode"
        os.environ["TITAN_MEMORY_BACKEND"] = backend
        if db_path is not None:
            os.environ["TITAN_MEMORY_DB_PATH"] = str(db_path)
        else:
            os.environ.pop("TITAN_MEMORY_DB_PATH", None)
        reset_runtime_context_cache()

    @staticmethod
    def _write_ledger(root: Path, events: list[dict], index: dict[str, int] | None = None) -> None:
        trace_dir = root / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        (trace_dir / "event_index.json").write_text(json.dumps(index or {}), encoding="utf-8")

    @staticmethod
    def _write_json_scenes(root: Path, scenes: list[dict]) -> Path:
        scene_path = root / "out" / "memories" / "scenes.json"
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_path.write_text(json.dumps(scenes, indent=2), encoding="utf-8")
        return scene_path

    def test_dry_run_reports_recovery_without_mutating_json_scene(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._use_runtime(root)
            scene = {
                "scene_id": "scene-1",
                "session_id": "session-1",
                "turn": 1,
                "kind": "message_exchange",
                "source_event_ids": ["event-1", "event-2"],
                "raw_events": [],
            }
            scene_path = self._write_json_scenes(root, [scene])
            self._write_ledger(
                root,
                [{"seq": 7, "session_id": "session-1", "event_id": "event-1", "event_type": "user_message", "payload": {"text": "hi"}}],
                {"session-1:event-1": 7, "session-1:event-2": 8},
            )

            before = scene_path.read_text(encoding="utf-8")
            report = backfill_scene_evidence()

            self.assertTrue(report["dry_run"])
            self.assertEqual(report["recovered_event_count"], 1)
            self.assertEqual(report["missing_event_count"], 1)
            self.assertEqual(scene_path.read_text(encoding="utf-8"), before)
            self.assertFalse((scene_path.parent / "scene_evidence_migrations.json").exists())

    def test_apply_recovers_surviving_events_and_records_missing_ids(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._use_runtime(root)
            scene_path = self._write_json_scenes(
                root,
                [{
                    "scene_id": "scene-1",
                    "session_id": "session-1",
                    "turn": 1,
                    "kind": "message_exchange",
                    "source_event_ids": ["event-1", "event-2"],
                    "raw_events": [],
                }],
            )
            self._write_ledger(
                root,
                [{"seq": 7, "session_id": "session-1", "event_id": "event-1", "event_type": "user_message", "payload": {"text": "hi"}}],
                {"session-1:event-1": 7, "session-1:event-2": 8},
            )

            report = backfill_scene_evidence(apply=True)
            migrated = json.loads(scene_path.read_text(encoding="utf-8"))[0]

            self.assertEqual(report["changed_scene_count"], 1)
            self.assertEqual(migrated["evidence_version"], 0)
            self.assertEqual(migrated["evidence_status"], "partial")
            self.assertEqual(migrated["missing_source_event_ids"], ["event-2"])
            self.assertEqual([event["event_id"] for event in migrated["raw_events"]], ["event-1"])
            receipt = json.loads((scene_path.parent / "scene_evidence_migrations.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["scenes"]["scene-1"]["missing_source_event_ids"], ["event-2"])

    def test_unverifiable_legacy_scene_is_never_promoted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._use_runtime(root)
            scene_path = self._write_json_scenes(
                root,
                [{
                    "scene_id": "scene-unknown",
                    "session_id": "session-1",
                    "turn": 1,
                    "kind": "raw_event",
                    "source_event_ids": [],
                    "raw_events": [],
                }],
            )

            report = backfill_scene_evidence(apply=True)
            migrated = json.loads(scene_path.read_text(encoding="utf-8"))[0]

            self.assertEqual(report["unverifiable_scene_count"], 1)
            self.assertEqual(migrated["evidence_status"], "partial")
            self.assertEqual(migrated["evidence_version"], 0)
            self.assertEqual(migrated["missing_source_event_ids"], [])
            self.assertEqual(report["scenes"][0]["lineage_source"], "unverifiable")

    def test_apply_is_idempotent_and_does_not_duplicate_raw_events(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._use_runtime(root)
            scene_path = self._write_json_scenes(
                root,
                [{
                    "scene_id": "scene-1",
                    "session_id": "session-1",
                    "turn": 1,
                    "kind": "message_exchange",
                    "source_event_ids": ["event-1"],
                    "raw_events": [],
                }],
            )
            self._write_ledger(
                root,
                [{"seq": 1, "session_id": "session-1", "event_id": "event-1", "payload": {"text": "hi"}}],
                {"session-1:event-1": 1},
            )

            backfill_scene_evidence(apply=True)
            second = backfill_scene_evidence(apply=True)
            migrated = json.loads(scene_path.read_text(encoding="utf-8"))[0]

            self.assertEqual(second["recovered_event_count"], 0)
            self.assertEqual(second["dedupe_index_entries_added"], 0)
            self.assertEqual(len(migrated["raw_events"]), 1)

    def test_apply_repairs_permanent_dedupe_keys_for_pruned_scene_lineage(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._use_runtime(root)
            self._write_json_scenes(
                root,
                [{
                    "scene_id": "scene-pruned",
                    "session_id": "session-1",
                    "turn": 1,
                    "kind": "raw_event",
                    "source_event_ids": ["event-pruned"],
                    "raw_events": [],
                    "evidence_version": 0,
                    "evidence_status": "partial",
                    "missing_source_event_ids": ["event-pruned"],
                }],
            )
            self._write_ledger(root, [], {})

            dry_run = backfill_scene_evidence()
            index_path = root / "traces" / "event_index.json"
            self.assertEqual(dry_run["dedupe_index_entries_added"], 1)
            self.assertEqual(json.loads(index_path.read_text(encoding="utf-8")), {})

            applied = backfill_scene_evidence(apply=True)
            repaired = json.loads(index_path.read_text(encoding="utf-8"))

            self.assertEqual(applied["dedupe_index_entries_added"], 1)
            self.assertEqual(repaired, {"session-1:event-pruned": 0})
            self.assertEqual(backfill_scene_evidence(apply=True)["dedupe_index_entries_added"], 0)

    def test_sqlite_apply_adds_evidence_columns_and_persists_partial_record(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = root / "out" / "memories" / "memory_store.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._use_runtime(root, backend="sqlite", db_path=db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE scenes (
                        scene_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        scene_seq INTEGER,
                        start_event_seq INTEGER,
                        end_event_seq INTEGER,
                        anchor_event_id TEXT,
                        ts TEXT NOT NULL,
                        source_event_ids_json TEXT NOT NULL,
                        raw_events_json TEXT NOT NULL,
                        messages_json TEXT NOT NULL,
                        tool_calls_json TEXT NOT NULL,
                        extraction_user_text TEXT NOT NULL,
                        extraction_assistant_text TEXT NOT NULL,
                        used_context_fallback INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO scenes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "scene-sqlite", "session-1", 1, "message_exchange", None, None, None,
                        None, "2026-01-01T00:00:00Z", json.dumps(["event-1", "event-2"]),
                        "[]", "[]", "[]", "", "", 0,
                    ),
                )
                conn.commit()
            self._write_ledger(
                root,
                [{"seq": 1, "session_id": "session-1", "event_id": "event-1", "payload": {"text": "hi"}}],
                {"session-1:event-1": 1, "session-1:event-2": 2},
            )

            report = backfill_scene_evidence(apply=True)

            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
                row = conn.execute(
                    "SELECT evidence_version, evidence_status, missing_source_event_ids_json, raw_events_json FROM scenes WHERE scene_id = ?",
                    ("scene-sqlite",),
                ).fetchone()
            self.assertEqual(report["missing_event_count"], 1)
            self.assertTrue({"evidence_version", "evidence_status", "missing_source_event_ids_json"}.issubset(columns))
            self.assertEqual(row[0:2], (0, "partial"))
            self.assertEqual(json.loads(row[2]), ["event-2"])
            self.assertEqual(json.loads(row[3])[0]["event_id"], "event-1")

    def test_sqlite_apply_upgrades_table_missing_raw_event_column_and_views(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = root / "out" / "memories" / "memory_store.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._use_runtime(root, backend="sqlite", db_path=db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE scenes (
                        scene_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        scene_seq INTEGER,
                        start_event_seq INTEGER,
                        end_event_seq INTEGER,
                        anchor_event_id TEXT,
                        ts TEXT NOT NULL,
                        source_event_ids_json TEXT NOT NULL,
                        messages_json TEXT NOT NULL,
                        tool_calls_json TEXT NOT NULL,
                        extraction_user_text TEXT NOT NULL,
                        extraction_assistant_text TEXT NOT NULL,
                        used_context_fallback INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO scenes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("legacy", "s1", 1, "raw_event", 1, 1, 1, "e1", "2026-01-01Z", "[\"e1\"]", "[]", "[]", "", "", 0),
                )
            self._write_ledger(
                root,
                [{"seq": 1, "session_id": "s1", "event_id": "e1", "event_type": "user_message", "payload": {"content": "hi"}}],
                {"s1:e1": 1},
            )

            backfill_scene_evidence(apply=True)

            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(scenes)").fetchall()}
                schema_version = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()[0]
                views = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
            self.assertIn("raw_events_json", columns)
            self.assertEqual(schema_version, "3")
            self.assertIn("readable_scenes", views)

    def test_migration_ignores_cross_session_embedded_event_with_same_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._use_runtime(root, backend="json")
            scene_path = self._write_json_scenes(
                root,
                [
                    {
                        "scene_id": "scene-1",
                        "session_id": "s1",
                        "turn": 1,
                        "kind": "raw_event",
                        "source_event_ids": ["shared-id"],
                        "raw_events": [{"seq": 9, "session_id": "s2", "event_id": "shared-id", "payload": {"content": "wrong"}}],
                    }
                ],
            )
            self._write_ledger(
                root,
                [{"seq": 1, "session_id": "s1", "event_id": "shared-id", "payload": {"content": "right"}}],
                {"s1:shared-id": 1},
            )

            backfill_scene_evidence(apply=True)
            migrated = json.loads(scene_path.read_text(encoding="utf-8"))[0]

            self.assertEqual(len(migrated["raw_events"]), 1)
            self.assertEqual(migrated["raw_events"][0]["session_id"], "s1")
            self.assertEqual(migrated["raw_events"][0]["payload"]["content"], "right")

    def test_cli_routes_dry_run_and_apply_without_changing_other_commands(self):
        with patch("tools.cli.titan.run_scene_backfill_evidence", return_value=0) as command:
            self.assertEqual(main(["scenes", "backfill-evidence"]), 0)
            command.assert_called_once_with(agent="opencode", apply=False)

        with patch("tools.cli.titan.run_scene_backfill_evidence", return_value=0) as command:
            self.assertEqual(main(["scenes", "backfill-evidence", "--apply", "--agent", "codex"]), 0)
            command.assert_called_once_with(agent="codex", apply=True)


if __name__ == "__main__":
    unittest.main()
