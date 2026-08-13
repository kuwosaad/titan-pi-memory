import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.save_pipeline.pipeline as pipeline
import app.storage.traces as traces


def _event(event_id: str, ts: str = "2026-02-25T20:00:00+00:00") -> dict:
    return {
        "event_id": event_id,
        "event_type": "user_message",
        "ts": ts,
        "payload": {"text": f"message {event_id}"},
    }


class IncrementalIngestTests(unittest.TestCase):
    def test_append_event_and_batch_share_behavior(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"

            event = {"session_id": "s1", **_event("evt-1")}
            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
            ):
                status, seq = traces.append_event(event)
                batch = traces.append_events_batch([event])

            self.assertEqual(status, "ingested")
            self.assertEqual(seq, 1)
            self.assertEqual(batch["ingested"], 0)
            self.assertEqual(batch["duplicate"], 1)
            lines = ledger.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)

    def test_incremental_ingest_noop_and_append_only_progress(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(json.dumps(_event("evt-1")) + "\n", encoding="utf-8")

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                second = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                with spool_file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(_event("evt-2")) + "\n")
                third = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(first["ingested"], 1)
            self.assertEqual(second["ingested"], 0)
            self.assertEqual(second["duplicate"], 0)
            self.assertEqual(third["ingested"], 1)
            self.assertGreaterEqual(third["start_offset"], second["end_offset"])

    def test_cursor_commits_at_line_cap_boundary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(
                json.dumps(_event("evt-1")) + "\n" + json.dumps(_event("evt-2")) + "\n" + json.dumps(_event("evt-3")) + "\n",
                encoding="utf-8",
            )

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 2}),
            ):
                first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                second = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(first["ingested"], 2)
            self.assertTrue(first["hit_cap"])
            self.assertLess(first["end_offset"], spool_file.stat().st_size)
            self.assertEqual(second["ingested"], 1)

    def test_partial_line_waits_for_newline(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            second = json.dumps(_event("evt-2"))
            spool_file.write_text(json.dumps(_event("evt-1")) + "\n" + second, encoding="utf-8")

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                with spool_file.open("a", encoding="utf-8") as handle:
                    handle.write("\n")
                second_run = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(first["ingested"], 1)
            self.assertTrue(first["partial_line"])
            self.assertEqual(second_run["ingested"], 1)

    def test_rewind_on_file_recreate_detected_by_head_hash(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(json.dumps(_event("evt-1")) + "\n", encoding="utf-8")

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                spool_file.write_text(
                    json.dumps(_event("evt-2")) + "\n" + json.dumps(_event("evt-3")) + "\n",
                    encoding="utf-8",
                )
                second = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(first["ingested"], 1)
            self.assertEqual(second["start_offset"], 0)
            self.assertEqual(second["ingested"], 2)

    def test_rewind_when_file_truncates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(
                json.dumps(_event("evt-1")) + "\n" + json.dumps(_event("evt-2")) + "\n",
                encoding="utf-8",
            )

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                spool_file.write_text(json.dumps(_event("evt-3")) + "\n", encoding="utf-8")
                second = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(first["ingested"], 2)
            self.assertEqual(second["start_offset"], 0)
            self.assertEqual(second["ingested"], 1)

    def test_mtime_change_only_does_not_rewind(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(json.dumps(_event("evt-1")) + "\n", encoding="utf-8")

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                os.utime(spool_file, None)
                second = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(first["ingested"], 1)
            self.assertEqual(second["ingested"], 0)
            self.assertEqual(second["start_offset"], first["end_offset"])

    def test_debug_payload_adds_ingest_metrics_compatibly(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            spool_file.write_text(json.dumps(_event("evt-1")) + "\n", encoding="utf-8")

            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            cursors = tmp_path / "spool_cursors.json"

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                with patch.object(
                    pipeline,
                    "load_settings",
                    return_value={
                        "plugin_spool_dir": str(spool_dir),
                        "ingest_debug_metrics_enabled": True,
                    },
                ):
                    status = pipeline.get_pipeline_debug_status(session_id="default")

            self.assertIn("retry_queue_size", status)
            self.assertIn("spool_cursor", status)
            self.assertIn("spool_latest_ts", status)
            self.assertIn("ledger_latest_ts", status)
            self.assertIn("checkpoint_seq", status)
            self.assertIn("unprocessed_event_count", status)
            self.assertIn("lag_seconds", status)
            self.assertEqual(status["spool_file"], str(spool_file))


if __name__ == "__main__":
    unittest.main()
