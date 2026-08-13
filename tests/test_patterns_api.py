import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.patterns.processing import PatternProcessingLedger
from app.storage.memories import SqliteMemoryRepository
from entrypoints.main import app


def _memory_records() -> list[dict]:
    records = []
    for idx in range(1, 5):
        records.append(
            {
                "id": f"s{idx}:1:0",
                "text": f"Pattern API evidence memory {idx}",
                "type": "workflow",
                "stream": "learnings",
                "embedding": [1.0, 0.0],
                "ts": f"2026-06-0{idx}T00:00:00+00:00",
                "session_id": f"s{idx}",
                "turn": 1,
                "scene_id": f"scene-{idx}",
                "provenance": {"user": f"u{idx}", "assistant": f"a{idx}"},
                "source_event_ids": [f"e{idx}"],
                "source_type": "mixed",
                "source_reliability": 0.9,
                "verification_status": "unverified",
                "fallback_generated": False,
            }
        )
    return records


def _pattern_payload() -> dict:
    return {
        "title": "Pi extension edits need TypeScript validation",
        "kind": "workflow",
        "scope": "repo",
        "status": "candidate",
        "summary": "Pi extension implementation changes have repeatedly needed TypeScript validation.",
        "recommended_behavior": "After changing tools/pi_extension/index.ts, run npx tsc --noEmit tools/pi_extension/index.ts before reporting done.",
        "applies_when": "The task edits Pi extension TypeScript code.",
        "does_not_apply_when": "The task only edits prose docs.",
        "trigger_terms": ["pi extension", "tools/pi_extension/index.ts", "typescript", "tsc"],
        "confidence": 0.74,
        "actionability": 0.9,
        "retrieval_value": 0.8,
        "evidence": [
            {"memory_id": "s1:1:0", "scene_id": "scene-1", "role": "support", "score": 0.9},
            {"memory_id": "s2:1:0", "scene_id": "scene-2", "role": "support", "score": 0.8},
            {"memory_id": "s3:1:0", "scene_id": "scene-3", "role": "support", "score": 0.7},
        ],
    }


class PatternApiTests(unittest.TestCase):
    def test_pattern_api_create_list_get_accept_reject(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app)
                created_response = client.post("/api/patterns", json=_pattern_payload())
                self.assertEqual(created_response.status_code, 200)
                pattern_id = created_response.json()["pattern"]["id"]

                list_response = client.get("/api/patterns", params={"status": "candidate"})
                get_response = client.get(f"/api/patterns/{pattern_id}")
                accept_response = client.post(f"/api/patterns/{pattern_id}/accept")
                reject_response = client.post(f"/api/patterns/{pattern_id}/reject")

            self.assertEqual(list_response.status_code, 200)
            self.assertEqual(list_response.json()["count"], 1)
            self.assertEqual(get_response.status_code, 200)
            self.assertEqual(len(get_response.json()["evidence"]), 3)
            self.assertEqual(accept_response.status_code, 200)
            self.assertEqual(accept_response.json()["pattern"]["status"], "accepted")
            self.assertEqual(reject_response.status_code, 200)
            self.assertEqual(reject_response.json()["pattern"]["status"], "rejected")

    def test_pattern_create_rejects_insufficient_support_evidence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            payload = _pattern_payload()
            payload["evidence"] = payload["evidence"][:1]

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post("/api/patterns", json=payload)

            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json()["detail"])

    def test_pattern_status_and_mark_processed_use_current_processor_identity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app)
                status_before = client.get("/api/patterns/status")
                mark_response = client.post(
                    "/api/patterns/mark-processed",
                    json={"memory_ids": ["s1:1:0", "s2:1:0"], "pattern_ids": []},
                )
                status_after = client.get("/api/patterns/status")

            self.assertEqual(status_before.status_code, 200)
            self.assertEqual(status_before.json()["memories_total"], 4)
            self.assertEqual(status_before.json()["processor_version"], "pattern-miner-v2")
            self.assertEqual(status_before.json()["processed_current"], 0)
            self.assertEqual(mark_response.status_code, 200)
            self.assertEqual(mark_response.json()["marked_count"], 2)
            self.assertEqual(status_after.json()["processed_current"], 2)
            self.assertEqual(status_after.json()["unprocessed"], 2)

    def test_pattern_status_reports_v2_reprocessing_available(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())
            ledger = PatternProcessingLedger(sqlite_file)
            run = ledger.start_run(
                processor_version="pattern-miner-v1",
                processor_config_hash="old-config",
                mode="backfill",
            )
            ledger.mark_processed(
                ["s1:1:0", "s2:1:0"],
                processor_version="pattern-miner-v1",
                processor_config_hash="old-config",
                run_id=run.id,
            )

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file), patch(
                "app.patterns.api.load_settings",
                return_value={"patterns": {"processor_version": "pattern-miner-v2", "packet_mode": "adaptive"}},
            ):
                client = TestClient(app)
                response = client.get("/api/patterns/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["processor_version"], "pattern-miner-v2")
            self.assertEqual(payload["processed_current"], 0)
            self.assertEqual(payload["unprocessed"], 4)
            self.assertTrue(payload["migration"]["reprocess_available"])
            self.assertEqual(payload["migration"]["previous_processed_memory_count"], 2)
            self.assertEqual(payload["migration"]["previous_processors"][0]["processor_version"], "pattern-miner-v1")

    def test_evidence_packet_route_returns_unprocessed_batch_without_marking(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app)
                response = client.post(
                    "/api/patterns/evidence-packet",
                    json={"batch_size": 2, "context_limit": 1},
                )
                status = client.get("/api/patterns/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(len(payload["unprocessed_memory_ids"]), 2)
            self.assertIn("cluster_summaries", payload)
            self.assertIn("central_memories", payload)
            self.assertEqual(status.json()["processed_current"], 0)

    def test_evidence_packet_uses_config_packet_mode_when_request_mode_omitted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            records = _memory_records()
            records[1]["text"] = "Bug fixed by validating evidence packet mode handling."
            records[1]["type"] = "issue"
            SqliteMemoryRepository(sqlite_file).append_memories(records)

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file), patch(
                "app.patterns.api.load_settings",
                return_value={"patterns": {"packet_mode": "chronological"}},
            ):
                client = TestClient(app)
                response = client.post(
                    "/api/patterns/evidence-packet",
                    json={"batch_size": 2, "context_limit": 0},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["packet_type"], "chronological_fallback")
            self.assertEqual(payload["unprocessed_memory_ids"], ["s1:1:0", "s2:1:0"])

    def test_pattern_create_reports_missing_scene_ids_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            records = _memory_records()
            records[0]["scene_id"] = ""
            SqliteMemoryRepository(sqlite_file).append_memories(records)
            payload = _pattern_payload()
            payload["evidence"] = [
                {"memory_id": "s1:1:0", "role": "support", "score": 0.9},
                {"memory_id": "s2:1:0", "scene_id": "scene-2", "role": "support", "score": 0.8},
                {"memory_id": "s3:1:0", "scene_id": "scene-3", "role": "support", "score": 0.7},
            ]

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post("/api/patterns", json=payload)

            self.assertEqual(response.status_code, 400)
            self.assertIn("missing scene_id", response.json()["detail"]["error"])
            self.assertEqual(response.json()["detail"]["memory_ids"], ["s1:1:0"])

    def test_pattern_create_reports_scene_lookup_database_errors(self):
        payload = _pattern_payload()
        for item in payload["evidence"]:
            item.pop("scene_id", None)

        with patch("app.patterns.api.resolve_pattern_db_path", return_value=Path("/tmp/missing.db")), patch(
            "app.patterns.api.sqlite3.connect",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/api/patterns", json=payload)

        self.assertEqual(response.status_code, 503)
        self.assertIn("scene lookup failed", response.json()["detail"]["error"])

    def test_mark_processed_auto_run_is_closed_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file).append_memories(_memory_records())

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/patterns/mark-processed",
                    json={"memory_ids": ["s1:1:0"], "status": "not-a-status"},
                )

            self.assertEqual(response.status_code, 400)
            run_id = response.json()["detail"]["run_id"]
            with sqlite3.connect(sqlite_file) as conn:
                row = conn.execute("SELECT status, error FROM pattern_mining_runs WHERE id = ?", (run_id,)).fetchone()
            self.assertEqual(row[0], "failed")
            self.assertIn("Invalid pattern memory processing status", row[1])

    def test_get_missing_pattern_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sqlite_file = Path(tmp_dir) / "memory_store.db"
            SqliteMemoryRepository(sqlite_file)

            with patch("app.patterns.api.resolve_pattern_db_path", return_value=sqlite_file):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/api/patterns/missing")

            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
