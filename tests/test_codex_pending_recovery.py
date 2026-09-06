import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from integrations.codex_titan_plugin.pending_recovery import recover_pending_sessions


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _pending_event(seq: int, event_id: str, event_type: str, turn_id: str, content: str = "") -> dict:
    payload = {"source": "codex", "turn_id": turn_id}
    if content:
        payload["content"] = content
    return {
        "seq": seq,
        "ts": f"2026-09-02T08:00:{seq:02d}+00:00",
        "session_id": "session-1",
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "schema_version": "v1",
    }


def test_preview_reports_completed_and_aborted_turns_without_mutating_pending_data(tmp_path: Path) -> None:
    pending_file = tmp_path / "titan" / "pending_user_messages.json"
    pending_file.parent.mkdir(parents=True)
    original = {
        "session-1": {
            "content": "Private unfinished request",
            "ts": "2026-09-02T08:00:04+00:00",
            "scene_evidence": {
                "updated_at": "2026-09-02T08:00:04+00:00",
                "events": [
                    _pending_event(1, "user-1", "user_message", "turn-complete", "Fix the parser"),
                    _pending_event(2, "tool-1", "tool_execution", "turn-complete"),
                    _pending_event(3, "user-2", "user_message", "turn-aborted", "Try another approach"),
                ],
            },
        }
    }
    pending_file.write_text(json.dumps(original), encoding="utf-8")

    rollout = tmp_path / "codex" / "sessions" / "2026" / "09" / "02" / "rollout-session-1.jsonl"
    _write_jsonl(
        rollout,
        [
            {"timestamp": "2026-09-02T08:00:00Z", "type": "session_meta", "payload": {"id": "session-1"}},
            {"timestamp": "2026-09-02T08:00:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-complete", "started_at": 1788336000}},
            {"timestamp": "2026-09-02T08:00:02Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-complete", "completed_at": 1788336002, "last_agent_message": "Implemented and verified the parser fix."}},
            {"timestamp": "2026-09-02T08:00:03Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-aborted", "started_at": 1788336003}},
            {"timestamp": "2026-09-02T08:00:04Z", "type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "turn-aborted", "completed_at": 1788336004, "reason": "interrupted"}},
        ],
    )

    report = recover_pending_sessions(
        apply=False,
        pending_file=pending_file,
        codex_sessions_dir=tmp_path / "codex" / "sessions",
        now=datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
    )

    assert report["pending_sessions"] == 1
    assert report["pending_events"] == 3
    assert report["recoverable_completed_turns"] == 1
    assert report["partial_turns"] == 1
    assert report["active_turns"] == 0
    assert report["missing_transcripts"] == 0
    assert report["dry_run"] is True
    assert "Implemented and verified" not in json.dumps(report)
    assert json.loads(pending_file.read_text(encoding="utf-8")) == original


def test_apply_creates_one_complete_scene_and_memory_then_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    import app.save_pipeline.pipeline as pipeline
    import app.storage.memories as memories
    import app.storage.scenes as scenes
    import app.storage.sessions as sessions
    import app.storage.traces as traces

    pending_file = tmp_path / "pending_user_messages.json"
    event = _pending_event(1, "user-1", "user_message", "turn-1", "Remember the durable decision")
    pending_file.write_text(json.dumps({"session-1": {"scene_evidence": {"events": [event]}}}), encoding="utf-8")
    rollout = tmp_path / "codex" / "sessions" / "rollout.jsonl"
    _write_jsonl(
        rollout,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            {"timestamp": "2026-09-02T08:00:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
            {"timestamp": "2026-09-02T08:00:02Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "The durable decision is implemented."}},
        ],
    )

    stored_scenes = {}
    stored_memories = []
    event_index = {}
    monkeypatch.setattr(traces, "PENDING_USER_MESSAGES_FILE", pending_file)
    monkeypatch.setattr(traces, "refresh_trace_paths", lambda: None)
    monkeypatch.setattr(traces, "ensure_dirs", lambda: None)
    monkeypatch.setattr(traces, "append_event", lambda item: (event_index.setdefault(f"{item['session_id']}:{item['event_id']}", 2) and "ingested", 2))
    monkeypatch.setattr(traces, "load_event_index", lambda: event_index)
    monkeypatch.setattr(traces, "mark_scene_events_finalized", lambda _session, seqs: max(seqs))
    monkeypatch.setattr(traces, "load_retry_queue", lambda: [])
    monkeypatch.setattr(traces, "append_retry_entry", lambda _entry: None)
    monkeypatch.setattr(traces, "remove_retry_entries", lambda _session, _ids: 1)
    monkeypatch.setattr(sessions, "interprocess_lock", lambda _path: nullcontext())
    monkeypatch.setattr(scenes, "get_scene", lambda scene_id: SimpleNamespace(**stored_scenes[scene_id]) if scene_id in stored_scenes else None)
    monkeypatch.setattr(scenes, "get_session_scenes", lambda session_id: [SimpleNamespace(**value) for value in stored_scenes.values() if value["session_id"] == session_id])
    monkeypatch.setattr(scenes, "append_scene", lambda scene: stored_scenes.__setitem__(scene["scene_id"], scene) or scene)
    monkeypatch.setattr(scenes, "get_recent_scenes", lambda limit=8: [SimpleNamespace(**value) for value in stored_scenes.values()])
    monkeypatch.setattr(memories, "get_memories_for_session", lambda _session: stored_memories)
    monkeypatch.setattr(pipeline, "_retry_failed_extractions", lambda _session: {"retried_memories": 0, "recovered_retries": 0})
    monkeypatch.setattr(pipeline, "_summarize_tool_event", lambda _event: None)

    def fake_pipeline(**kwargs):
        stored_memories.append(SimpleNamespace(scene_id=kwargs["scene"].scene_id))
        return {"records": [{"id": "memory-1"}]}

    monkeypatch.setattr(pipeline, "run_memory_pipeline_outcome", fake_pipeline)

    first = recover_pending_sessions(apply=True, pending_file=pending_file, codex_sessions_dir=rollout.parent)
    second = recover_pending_sessions(apply=True, pending_file=pending_file, codex_sessions_dir=rollout.parent)

    assert first["applied_turns"] == 1
    assert first["stored_memories"] == 1
    assert second["applied_turns"] == 0
    assert len(stored_scenes) == 1
    assert len(stored_memories) == 1
    scene = next(iter(stored_scenes.values()))
    assert scene["scene_id"] == "session-1:scene:codex-turn:turn-1"
    assert scene["evidence_status"] == "complete"
    assert scene["source_event_ids"] == ["user-1", "codex-transcript:turn-1:final"]


def test_preview_attaches_child_tool_events_and_leaves_recent_turn_active(tmp_path: Path) -> None:
    pending_file = tmp_path / "pending_user_messages.json"
    parent = _pending_event(1, "user-1", "user_message", "parent", "Do the work")
    child = _pending_event(2, "tool-1", "tool_execution", "child-tool-turn")
    active = _pending_event(3, "user-2", "user_message", "active", "Still running")
    pending_file.write_text(
        json.dumps({"session-1": {"scene_evidence": {"events": [parent, child, active]}}}),
        encoding="utf-8",
    )
    rollout = tmp_path / "sessions" / "rollout-session-1.jsonl"
    _write_jsonl(
        rollout,
        [
            {"type": "session_meta", "payload": {"id": "session-1"}},
            {"timestamp": "2026-09-02T08:00:00Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "parent"}},
            {"timestamp": "2026-09-02T08:00:02Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "parent", "last_agent_message": "Done"}},
            {"timestamp": "2026-09-02T08:00:03Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "active"}},
        ],
    )

    report = recover_pending_sessions(
        pending_file=pending_file,
        codex_sessions_dir=rollout.parent,
        now=datetime(2026, 9, 2, 8, 10, tzinfo=timezone.utc),
    )

    assert report["recoverable_completed_turns"] == 1
    assert report["active_turns"] == 1
    assert report["partial_turns"] == 0


def test_recovery_marker_path_updates_real_repositories_and_preserves_stored():
    import tempfile
    from unittest.mock import patch
    from app.storage.models import Scene
    from app.storage.scenes import JsonSceneRepository, SqliteSceneRepository
    from integrations.codex_titan_plugin.pending_recovery import _mark_scene_extraction
    from test_storage_integrity import _complete_scene

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        for repo in (JsonSceneRepository(root / "scenes.json"), SqliteSceneRepository(root / "scenes.db")):
            scene = Scene(**_complete_scene())
            repo.append_scenes([scene.model_dump(mode="python")])
            with patch("app.storage.scenes.get_scene_write_repository", return_value=repo):
                _mark_scene_extraction(scene, "skipped")
                loaded = repo.get_scene(scene.scene_id)
                assert loaded["raw_events"][1]["payload"]["titan_recovery_extraction"] == "skipped"

                _mark_scene_extraction(scene, "stored")
                _mark_scene_extraction(scene, "skipped")
                repo.append_scenes([_complete_scene()])

            loaded = repo.get_scene(scene.scene_id)
            assert loaded["raw_events"][1]["payload"]["titan_recovery_extraction"] == "stored"
