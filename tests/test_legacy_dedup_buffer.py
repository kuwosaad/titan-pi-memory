import json
from unittest.mock import patch

from app.save_pipeline import dedup_buffer


def test_legacy_active_and_processing_entries_are_read_without_mutation(tmp_path):
    active = tmp_path / "dedup_buffer.jsonl"
    processing = tmp_path / "dedup_buffer.processing.jsonl"
    active.write_text(json.dumps({"id": "new", "session_id": "s"}) + "\ncorrupt\n")
    processing.write_text(json.dumps({"id": "old", "session_id": "s"}) + "\n")
    original = [path.read_bytes() for path in (active, processing)]
    with (
        patch.object(dedup_buffer, "DEDUP_BUFFER_FILE", active),
        patch.object(dedup_buffer, "DEDUP_PROCESSING_FILE", processing),
    ):
        assert [row["id"] for row in dedup_buffer.peek_dedup_buffer()] == ["old", "new"]
        assert [row["id"] for row in dedup_buffer.peek_dedup_buffer(limit=1)] == ["new"]
        assert dedup_buffer.peek_dedup_buffer(session_id="other") == []
        assert dedup_buffer.peek_dedup_buffer(limit=0) == []
    assert [path.read_bytes() for path in (active, processing)] == original
