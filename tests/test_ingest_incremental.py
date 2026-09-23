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
    def test_processed_and_committed_checkpoints_are_separate_and_contiguous(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            scene_checkpoints = tmp_path / "scene_checkpoints.json"
            retry_queue = tmp_path / "retry_queue.jsonl"
            events = [
                {"session_id": "s1", **_event("evt-1")},
                {"session_id": "s1", **_event("evt-2")},
            ]

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SCENE_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "COMMITTED_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "RETRY_QUEUE_FILE", retry_queue),
            ):
                batch = traces.append_events_batch(events)
                first_seq = int(batch["item_results"][0]["seq"])
                second_seq = int(batch["item_results"][1]["seq"])

                traces.update_session_checkpoint("s1", second_seq)
                self.assertEqual(traces.get_session_checkpoint("s1"), second_seq)
                self.assertEqual(traces.get_scene_checkpoint("s1"), 0)
                self.assertEqual(traces.prune_committed_events(["s1"])["removed"], 0)

                # The later event cannot commit while the earlier event is
                # unresolved, even though it is already processed.
                self.assertEqual(traces.mark_scene_events_finalized("s1", [second_seq]), 0)
                self.assertEqual(traces.mark_scene_events_finalized("s1", [first_seq]), second_seq)
                self.assertEqual(traces.prune_committed_events(["s1"])["removed"], 2)

    def test_pruning_preserves_committed_event_while_retry_is_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ledger = tmp_path / "events.jsonl"
            index = tmp_path / "event_index.json"
            checkpoints = tmp_path / "checkpoints.json"
            scene_checkpoints = tmp_path / "scene_checkpoints.json"
            retry_queue = tmp_path / "retry_queue.jsonl"
            event = {"session_id": "s1", **_event("evt-retry")}

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SCENE_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "COMMITTED_CHECKPOINT_FILE", scene_checkpoints),
                patch.object(traces, "RETRY_QUEUE_FILE", retry_queue),
            ):
                _status, seq = traces.append_event(event)
                traces.mark_scene_events_finalized("s1", [seq])
                traces.append_retry_entry({"session_id": "s1", "event_id": "evt-retry", "seq": seq})

                self.assertEqual(traces.prune_committed_events(["s1"])["removed"], 0)
                traces.remove_retry_entries("s1", {"evt-retry"})
                self.assertEqual(traces.prune_committed_events(["s1"])["removed"], 1)

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

    def test_unchanged_eof_does_not_rewrite_the_cursor(self):
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
                with patch.object(traces, "now_iso", return_value="2026-09-22T10:00:00+00:00"):
                    first = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                first_cursor = traces.get_spool_cursor(spool_file)
                with patch.object(traces, "now_iso", return_value="2026-09-22T10:01:00+00:00"):
                    second = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                second_cursor = traces.get_spool_cursor(spool_file)

            self.assertEqual(first["end_offset"], spool_file.stat().st_size)
            self.assertEqual(second["start_offset"], first["end_offset"])
            self.assertEqual(second["end_offset"], first["end_offset"])
            self.assertEqual(second["bytes_read"], 0)
            self.assertEqual(second_cursor, first_cursor)

    def test_legacy_eof_cursor_is_upgraded_before_same_size_replacement_can_skip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            spool_dir = tmp_path / "spool"
            spool_dir.mkdir(parents=True, exist_ok=True)
            spool_file = spool_dir / "default.jsonl"
            first_event = json.dumps(_event("evt-1")) + "\n"
            replacement_event = json.dumps(_event("evt-2")) + "\n"
            self.assertEqual(len(first_event), len(replacement_event))
            spool_file.write_text(first_event, encoding="utf-8")

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
                traces.save_spool_cursors(
                    {str(spool_file.resolve()): {"offset": spool_file.stat().st_size}}
                )
                upgraded = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                upgraded_cursor = traces.get_spool_cursor(spool_file)
                spool_file.write_text(replacement_event, encoding="utf-8")
                replacement = traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)

            self.assertEqual(upgraded["bytes_read"], 0)
            self.assertTrue(upgraded_cursor["head_hash_256"])
            self.assertGreater(upgraded_cursor["head_size"], 0)
            self.assertEqual(replacement["start_offset"], 0)
            self.assertEqual(replacement["ingested"], 1)

    def test_public_spool_ingest_still_runs_recovery_at_unchanged_eof(self):
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
            recovered = {
                "processed_events": 0,
                "prompt_candidates": 0,
                "stored_memories": 1,
                "fallback_memories": 0,
                "queued_retries": 0,
                "recovered_retries": 1,
                "skipped_low_signal": 0,
                "skip_reasons": {},
            }

            with (
                patch.object(traces, "EVENT_LEDGER_FILE", ledger),
                patch.object(traces, "EVENT_INDEX_FILE", index),
                patch.object(traces, "CHECKPOINT_FILE", checkpoints),
                patch.object(traces, "SPOOL_CURSOR_FILE", cursors),
                patch.object(traces, "_load_ingest_settings", return_value={"mode": "incremental", "max_lines_per_pass": 20000}),
            ):
                traces.ingest_spool_file(session_id="default", spool_dir=spool_dir)
                with (
                    patch.object(pipeline, "_process_session_events_impl", return_value=recovered),
                    patch.object(pipeline, "load_unprocessed_events", return_value=[]),
                    patch.object(pipeline, "prune_processed_events", return_value={"before": 0, "after": 0, "removed": 0}),
                    patch.object(pipeline, "cleanup_processed_spool_file", return_value={"deleted": False, "reason": "test"}),
                    patch.object(pipeline, "get_retry_queue_size", return_value=0),
                ):
                    result = pipeline.ingest_spool_session(session_id="default", spool_dir=str(spool_dir))

            self.assertEqual(result["bytes_read"], 0)
            self.assertEqual(result["recovered_retries"], 1)
            self.assertEqual(result["stored_memories"], 1)

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
