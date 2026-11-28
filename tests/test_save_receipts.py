from unittest.mock import Mock, patch

from app.save_pipeline import pipeline
from app.storage.models import TracePacketRequest


def receipt(ingest_result, retry_queue=(), memories=(), save_intent=True):
    request = TracePacketRequest(goal="goal", outcome="outcome", session_id="session", event_id="current", save_intent=save_intent)
    repository = Mock()
    repository.get_recent_memories.return_value = list(memories)
    with (
        patch.object(pipeline, "ensure_dirs"),
        patch.object(pipeline, "append_trace"),
        patch.object(pipeline, "ingest_trace_event", return_value=ingest_result),
        patch.object(pipeline, "load_retry_queue", return_value=list(retry_queue)),
        patch.object(pipeline, "get_memory_repository", return_value=repository),
    ):
        return pipeline.handle_trace_packet(request)


def test_extraction_failure_reports_pending_without_claiming_save():
    result = receipt(
        {"status": "ingested", "stored_memories": 0, "queued_retries": 1},
        retry_queue=[{"event_id": "current"}],
    )
    assert result["memory_status"] == "pending"
    assert result["stored"] is False


def test_successful_receipt_reports_the_current_event_and_excludes_older_retries():
    result = receipt(
        {"status": "ingested", "stored_memories": 2},
        memories=[
            {"text": "Older retry", "source_event_ids": ["older"]},
            {"text": "New memory", "source_event_ids": ["current"]},
        ],
    )
    assert result["memory_status"] == "stored"
    assert result["stored"] is True
    assert result["recap"] == "New memory"

    older_retry_only = receipt(
        {"status": "ingested", "stored_memories": 1},
        memories=[{"text": "Older retry", "source_event_ids": ["older"]}],
    )
    assert older_retry_only["memory_status"] == "captured"
    assert older_retry_only["stored"] is False
