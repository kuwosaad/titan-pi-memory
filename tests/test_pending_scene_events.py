import json
from pathlib import Path

import app.storage.traces as traces


def _pending_record(*events: dict) -> dict:
    return {
        "session-a": {
            "content": "keep this user context",
            "event_id": "context-a",
            "scene_evidence": {"events": list(events), "updated_at": "2026-09-02T08:00:00Z"},
        },
        "session-b": {
            "content": "leave the other session alone",
            "scene_evidence": {"events": [{"event_id": "other-1"}]},
        },
    }


def test_remove_pending_scene_events_removes_only_requested_ids(tmp_path: Path, monkeypatch) -> None:
    pending_file = tmp_path / "pending_user_messages.json"
    original = _pending_record(
        {"event_id": "remove-1", "payload": {"content": "one"}},
        {"event_id": "keep-1", "payload": {"content": "two"}},
        {"event_id": "remove-2", "payload": {"content": "three"}},
    )
    pending_file.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(traces, "PENDING_USER_MESSAGES_FILE", pending_file)
    monkeypatch.setattr(traces, "refresh_trace_paths", lambda: None)
    monkeypatch.setattr(traces, "ensure_dirs", lambda: None)

    removed = traces.remove_pending_scene_events("session-a", ["remove-1", "missing", "remove-1"])

    assert removed == 1
    state = json.loads(pending_file.read_text(encoding="utf-8"))
    assert [event["event_id"] for event in state["session-a"]["scene_evidence"]["events"]] == [
        "keep-1",
        "remove-2",
    ]
    assert state["session-a"]["content"] == original["session-a"]["content"]
    assert state["session-b"] == original["session-b"]


def test_remove_pending_scene_events_tolerates_missing_session_and_clears_empty_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    pending_file = tmp_path / "pending_user_messages.json"
    original = _pending_record({"event_id": "only-event"})
    pending_file.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(traces, "PENDING_USER_MESSAGES_FILE", pending_file)
    monkeypatch.setattr(traces, "refresh_trace_paths", lambda: None)
    monkeypatch.setattr(traces, "ensure_dirs", lambda: None)

    assert traces.remove_pending_scene_events("missing-session", ["anything"]) == 0
    assert traces.remove_pending_scene_events("session-a", ["only-event", "missing"]) == 1

    state = json.loads(pending_file.read_text(encoding="utf-8"))
    assert state["session-a"] == {
        "content": "keep this user context",
        "event_id": "context-a",
    }
    assert state["session-b"] == original["session-b"]
