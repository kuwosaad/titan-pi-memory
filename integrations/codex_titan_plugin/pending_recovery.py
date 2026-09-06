from __future__ import annotations

"""Recover Codex turns that were captured before the Stop hook was enabled.

The public entry point deliberately returns counts only. Transcript and prompt text
must never leak into Doctor or CLI status output.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_STALE_AFTER = timedelta(minutes=30)
_ROLLOUT_CACHE: dict[tuple[str, str], Path] = {}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _find_rollouts(sessions_dir: Path, wanted: set[str]) -> dict[str, Path]:
    root_key = str(sessions_dir.resolve())
    found: dict[str, Path] = {
        session_id: path
        for session_id in wanted
        if (path := _ROLLOUT_CACHE.get((root_key, session_id))) is not None and path.exists()
    }
    if not sessions_dir.exists() or not wanted:
        return found
    missing = wanted - set(found)
    candidates: set[Path] = set()
    for session_id in missing:
        candidates.update(sessions_dir.rglob(f"*{session_id}*.jsonl"))
    if len(candidates) < len(missing):
        candidates.update(sessions_dir.rglob("*.jsonl"))
    for path in candidates:
        for row in _read_jsonl(path):
            if row.get("type") != "session_meta":
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            session_id = str(payload.get("id") or "")
            if session_id in wanted:
                found[session_id] = path
                _ROLLOUT_CACHE[(root_key, session_id)] = path
            break
        if len(found) == len(wanted):
            break
    return found


def inspect_codex_stop_hook(config_path: Path | None = None) -> dict[str, bool]:
    """Read the canonical plugin and Stop-hook state without exposing config data."""

    import tomllib

    path = Path(config_path or (Path.home() / ".codex" / "config.toml"))
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {"plugin_enabled": False, "stop_configured": False, "stop_enabled": False}
    plugin_id = "titan-memory@titan-pi-memory"
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    plugin = plugins.get(plugin_id) if isinstance(plugins.get(plugin_id), dict) else {}
    state = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    state = state.get("state") if isinstance(state.get("state"), dict) else {}
    key = f"{plugin_id}:hooks/hooks.json:stop:0:0"
    stop = state.get(key) if isinstance(state.get(key), dict) else None
    return {
        "plugin_enabled": plugin.get("enabled") is True,
        "stop_configured": stop is not None and bool(stop.get("trusted_hash")),
        "stop_enabled": stop is not None and stop.get("enabled", True) is not False,
    }


def pending_recovery_enabled() -> bool:
    import os

    return os.getenv("TITAN_PENDING_RECOVERY_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def recover_one_pending_turn() -> dict[str, Any]:
    if not pending_recovery_enabled():
        return {"dry_run": False, "applied_turns": 0, "stored_memories": 0}
    return recover_pending_sessions(apply=True, limit=1)


def _transcript_turns(path: Path) -> dict[str, dict[str, Any]]:
    turns: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        if row.get("type") != "event_msg":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        event_type = str(payload.get("type") or "")
        turn_id = str(payload.get("turn_id") or "")
        if not turn_id or event_type not in {"task_started", "task_complete", "turn_aborted"}:
            continue
        turn = turns.setdefault(turn_id, {})
        if event_type == "task_started":
            turn["started_at"] = _parse_time(payload.get("started_at")) or _parse_time(row.get("timestamp"))
        elif event_type == "task_complete":
            turn["status"] = "complete"
            turn["completed_at"] = _parse_time(payload.get("completed_at")) or _parse_time(row.get("timestamp"))
            turn["assistant_text"] = str(payload.get("last_agent_message") or "").strip()
        else:
            turn["status"] = "aborted"
            turn["completed_at"] = _parse_time(payload.get("completed_at")) or _parse_time(row.get("timestamp"))
    return turns


def _event_turn_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(payload.get("turn_id") or event.get("event_id") or "unknown")


def _candidate_turns(
    events: list[dict[str, Any]],
    transcript: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Attach child/tool-only events to the enclosing transcript turn."""

    intervals: list[tuple[datetime, datetime, str]] = []
    for turn_id, state in transcript.items():
        start = state.get("started_at")
        end = state.get("completed_at")
        if isinstance(start, datetime) and isinstance(end, datetime) and end >= start:
            intervals.append((start, end, turn_id))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        exact = _event_turn_id(event)
        assigned = exact
        if exact not in transcript:
            event_time = _parse_time(event.get("ts"))
            matches = [item for item in intervals if event_time is not None and item[0] <= event_time <= item[1]]
            if matches:
                assigned = min(matches, key=lambda item: (item[1] - item[0], item[0]))[2]
        grouped.setdefault(assigned, []).append(event)

    candidates: list[dict[str, Any]] = []
    for turn_id, turn_events in grouped.items():
        turn_events.sort(key=lambda event: int(event.get("seq") or 0))
        state = transcript.get(turn_id, {})
        assistant_text = str(state.get("assistant_text") or "").strip()
        if state.get("status") == "complete" and assistant_text:
            status = "complete"
        elif state.get("status") in {"complete", "aborted"}:
            status = "partial"
        else:
            reference = state.get("started_at") or _latest_event_time(turn_events)
            age = now - reference if reference is not None else None
            status = "active" if age is not None and timedelta(0) <= age < DEFAULT_STALE_AFTER else "partial"
        candidates.append(
            {
                "turn_id": turn_id,
                "events": turn_events,
                "status": status,
                "assistant_text": assistant_text,
                "completed_at": state.get("completed_at"),
            }
        )
    candidates.sort(key=lambda item: min(int(event.get("seq") or 0) for event in item["events"]))
    return candidates


def _user_text(events: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for event in events:
        if str(event.get("event_type") or "") != "user_message":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        content = str(payload.get("content") or "").strip()
        if content and content not in texts:
            texts.append(content)
    return "\n\n".join(texts)


def _next_turn(session_id: str) -> int:
    from app.storage.scenes import get_session_scenes

    turns = [int(scene.turn) for scene in get_session_scenes(session_id)]
    return max(turns, default=0) + 1


def _scene_for_candidate(session_id: str, candidate: dict[str, Any], turn: int) -> tuple[dict[str, Any], str | None]:
    from app.save_pipeline.pipeline import _summarize_tool_event
    from app.storage.traces import append_event, load_event_index, sanitize_trace_value

    turn_id = str(candidate["turn_id"])
    events = [dict(event) for event in candidate["events"]]
    assistant_event_id: str | None = None
    assistant_text = str(candidate.get("assistant_text") or "").strip()
    status = str(candidate["status"])
    if status == "complete":
        assistant_event_id = f"codex-transcript:{turn_id}:final"
        assistant_event = {
            "session_id": session_id,
            "event_id": assistant_event_id,
            "event_type": "assistant_message",
            "ts": (candidate.get("completed_at") or _latest_event_time(events) or datetime.now(timezone.utc)).isoformat(),
            "payload": sanitize_trace_value(
                {"source": "codex-transcript-recovery", "turn_id": turn_id, "content": assistant_text}
            ),
            "schema_version": "v1",
        }
        _, seq = append_event(assistant_event)
        if seq is None:
            seq = int(load_event_index().get(f"{session_id}:{assistant_event_id}") or 0)
        if seq <= 0:
            raise RuntimeError("recovered assistant event has no durable ledger sequence")
        assistant_event["seq"] = seq
        events.append(assistant_event)

    events = [sanitize_trace_value(event) for event in events]
    events.sort(key=lambda event: int(event.get("seq") or 0))
    event_ids = [str(event.get("event_id") or "") for event in events]
    seqs = [int(event.get("seq") or 0) for event in events if int(event.get("seq") or 0) > 0]
    user_text = _user_text(events)
    messages = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"user_message", "assistant_message"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        content = str(payload.get("content") or "").strip()
        if content:
            messages.append(
                {
                    "role": "user" if event_type == "user_message" else "assistant",
                    "content": content,
                    "event_id": str(event.get("event_id") or ""),
                }
            )
    tool_calls = [summary for event in events if (summary := _summarize_tool_event(event)) is not None]
    scene_id = f"{session_id}:scene:codex-turn:{turn_id}"
    return (
        {
            "scene_id": scene_id,
            "session_id": session_id,
            "turn": turn,
            "kind": "message_exchange" if status == "complete" else "raw_event",
            "scene_seq": max(seqs, default=None),
            "start_event_seq": min(seqs, default=None),
            "end_event_seq": max(seqs, default=None),
            "anchor_event_id": assistant_event_id or (event_ids[-1] if event_ids else None),
            "source_event_ids": event_ids,
            "raw_events": events,
            "evidence_version": 1,
            "evidence_status": "complete" if status == "complete" else "partial",
            "missing_source_event_ids": [],
            "messages": messages,
            "tool_calls": tool_calls,
            "extraction_user_text": user_text,
            "extraction_assistant_text": assistant_text,
            "used_context_fallback": False,
            "ts": str(events[-1].get("ts") if events else datetime.now(timezone.utc).isoformat()),
        },
        assistant_event_id,
    )


def _apply_candidate(session_id: str, candidate: dict[str, Any]) -> tuple[int, int]:
    from app.save_pipeline.pipeline import run_memory_pipeline_outcome
    from app.storage.memories import get_memories_for_session
    from app.storage.models import Scene
    from app.storage.scenes import append_scene, get_scene
    from app.storage.traces import (
        append_retry_entry,
        mark_scene_events_finalized,
        remove_pending_scene_events,
        remove_retry_entries,
    )

    scene_id = f"{session_id}:scene:codex-turn:{candidate['turn_id']}"
    existing = get_scene(scene_id)
    turn = int(existing.turn) if existing is not None else _next_turn(session_id)
    scene_payload, assistant_event_id = _scene_for_candidate(session_id, candidate, turn)
    persisted = append_scene(scene_payload)
    scene = Scene(**persisted)
    scene_event_ids = [str(event.get("event_id") or "") for event in candidate["events"]]
    scene_seqs = [int(event.get("seq") or 0) for event in scene_payload["raw_events"]]
    mark_scene_events_finalized(session_id, [seq for seq in scene_seqs if seq > 0])
    remove_pending_scene_events(session_id, scene_event_ids)

    if candidate["status"] != "complete" or not assistant_event_id:
        return 1, 0

    existing_memory = any(memory.scene_id == scene_id for memory in get_memories_for_session(session_id))
    if existing_memory:
        remove_retry_entries(session_id, {assistant_event_id})
        return 1, 0

    anchor_seq = int(scene_payload["end_event_seq"] or 0)
    append_retry_entry(
        {
            "session_id": session_id,
            "event_id": assistant_event_id,
            "seq": anchor_seq,
            "reason": "codex_pending_recovery",
        }
    )
    try:
        outcome = run_memory_pipeline_outcome(
            session_id=session_id,
            turn=turn,
            user_text=scene_payload["extraction_user_text"],
            assistant_text=scene_payload["extraction_assistant_text"],
            source_event_ids=scene_payload["source_event_ids"],
            fallback_enabled=True,
            scene=scene,
            persist_scene=False,
        )
    except Exception:
        return 1, 0
    remove_retry_entries(session_id, {assistant_event_id})
    _mark_scene_extraction(scene, "stored" if outcome.get("records") else "skipped")
    return 1, len(outcome.get("records") or [])


def _scene_extraction_status(scene: Any) -> str:
    for event in scene.raw_events:
        if str(event.get("event_id") or "") != str(scene.anchor_event_id or ""):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        return str(payload.get("titan_recovery_extraction") or "")
    return ""


def _mark_scene_extraction(scene: Any, status: str) -> None:
    from app.storage.scenes import append_scene

    payload = scene.model_dump(mode="python")
    for event in payload.get("raw_events") or []:
        if str(event.get("event_id") or "") != str(scene.anchor_event_id or ""):
            continue
        event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_payload["titan_recovery_extraction"] = status
        event["payload"] = event_payload
        break
    append_scene(payload)


def _recover_memoryless_scenes(selected: set[str], limit: int | None) -> tuple[int, int]:
    """Retry recovered complete scenes whose first extraction never finished."""

    from app.save_pipeline.pipeline import run_memory_pipeline_outcome
    from app.storage.memories import get_memories_for_session
    from app.storage.scenes import get_recent_scenes
    from app.storage.traces import append_retry_entry, remove_retry_entries

    scenes = [
        scene
        for scene in get_recent_scenes(limit=100_000)
        if ":scene:codex-turn:" in scene.scene_id
        and scene.evidence_status == "complete"
        and (not selected or scene.session_id in selected)
    ]
    scenes.sort(key=lambda scene: (scene.ts, scene.scene_id))
    memory_scene_ids: set[str] = set()
    for session_id in {scene.session_id for scene in scenes}:
        memory_scene_ids.update(
            str(memory.scene_id)
            for memory in get_memories_for_session(session_id)
            if memory.scene_id
        )

    attempted = stored = 0
    for scene in scenes:
        if limit is not None and attempted >= limit:
            break
        if scene.scene_id in memory_scene_ids:
            if not _scene_extraction_status(scene):
                _mark_scene_extraction(scene, "stored")
            continue
        if _scene_extraction_status(scene) in {"stored", "skipped"}:
            continue
        anchor = str(scene.anchor_event_id or "")
        seq = int(scene.end_event_seq or scene.scene_seq or 0)
        if not anchor or seq <= 0:
            continue
        append_retry_entry(
            {
                "session_id": scene.session_id,
                "event_id": anchor,
                "seq": seq,
                "reason": "codex_pending_recovery",
            }
        )
        attempted += 1
        try:
            outcome = run_memory_pipeline_outcome(
                session_id=scene.session_id,
                turn=scene.turn,
                user_text=scene.extraction_user_text,
                assistant_text=scene.extraction_assistant_text,
                source_event_ids=scene.source_event_ids,
                fallback_enabled=True,
                scene=scene,
                persist_scene=False,
            )
        except Exception:
            continue
        records = outcome.get("records") or []
        stored += len(records)
        remove_retry_entries(scene.session_id, {anchor})
        _mark_scene_extraction(scene, "stored" if records else "skipped")
    return attempted, stored


def _count_memoryless_recovery_scenes(selected: set[str]) -> int:
    from app.storage.memories import get_memories_for_session
    from app.storage.scenes import get_recent_scenes

    scenes = [
        scene
        for scene in get_recent_scenes(limit=100_000)
        if ":scene:codex-turn:" in scene.scene_id
        and scene.evidence_status == "complete"
        and not _scene_extraction_status(scene)
        and (not selected or scene.session_id in selected)
    ]
    memory_scene_ids: set[str] = set()
    for session_id in {scene.session_id for scene in scenes}:
        memory_scene_ids.update(
            str(memory.scene_id)
            for memory in get_memories_for_session(session_id)
            if memory.scene_id
        )
    return sum(scene.scene_id not in memory_scene_ids for scene in scenes)


def _pending_events(record: Any) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    evidence = record.get("scene_evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("events"), list):
        return []
    return [event for event in evidence["events"] if isinstance(event, dict)]


def _latest_event_time(events: Iterable[dict[str, Any]]) -> datetime | None:
    times = [_parse_time(event.get("ts")) for event in events]
    valid = [value for value in times if value is not None]
    return max(valid) if valid else None


def recover_pending_sessions(
    *,
    apply: bool = False,
    session_ids: Iterable[str] | None = None,
    limit: int | None = None,
    pending_file: Path | None = None,
    codex_sessions_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Preview or apply recovery for durable pending Codex scene evidence."""

    if pending_file is None:
        from app.storage import traces

        traces.refresh_trace_paths()
        pending_file = traces.PENDING_USER_MESSAGES_FILE
    pending_file = Path(pending_file)
    codex_sessions_dir = Path(codex_sessions_dir or (Path.home() / ".codex" / "sessions"))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    try:
        payload = json.loads(pending_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    selected = {str(value) for value in session_ids or [] if str(value)}
    records = {
        str(session_id): record
        for session_id, record in payload.items()
        if (not selected or str(session_id) in selected) and _pending_events(record)
    }
    rollouts = _find_rollouts(codex_sessions_dir, set(records))

    completed = partial = active = pending_events = 0
    candidates_by_session: dict[str, list[dict[str, Any]]] = {}
    for session_id, record in records.items():
        events = _pending_events(record)
        pending_events += len(events)
        path = rollouts.get(session_id)
        if path is None:
            continue
        session_candidates = _candidate_turns(events, _transcript_turns(path), now)
        candidates_by_session[session_id] = session_candidates
        completed += sum(item["status"] == "complete" for item in session_candidates)
        partial += sum(item["status"] == "partial" for item in session_candidates)
        active += sum(item["status"] == "active" for item in session_candidates)

    report = {
        "pending_sessions": len(records),
        "pending_events": pending_events,
        "recoverable_completed_turns": completed,
        "partial_turns": partial,
        "active_turns": active,
        "missing_transcripts": len(records) - len(rollouts),
        "dry_run": not apply,
        "applied_turns": 0,
        "stored_memories": 0,
        "recovered_retries": 0,
        "reconciled_extractions": 0,
        "memoryless_recovery_scenes": _count_memoryless_recovery_scenes(selected),
    }
    if not apply:
        return report

    from app.storage.sessions import interprocess_lock

    max_turns = None if limit is None else max(0, int(limit))
    lock_path = pending_file.parent / ".titan-ingest.lock"
    with interprocess_lock(lock_path):
        processable = [
            (session_id, candidate)
            for session_id, candidates in candidates_by_session.items()
            for candidate in candidates
            if candidate["status"] in {"complete", "partial"}
        ]
        processable.sort(
            key=lambda item: min(int(event.get("seq") or 0) for event in item[1]["events"])
        )
        if max_turns is not None:
            processable = processable[:max_turns]
        for session_id, candidate in processable:
            applied_turns, stored_memories = _apply_candidate(session_id, candidate)
            report["applied_turns"] += applied_turns
            report["stored_memories"] += stored_memories
        remaining = None if max_turns is None else max(0, max_turns - len(processable))
        attempted, stored = _recover_memoryless_scenes(selected, remaining)
        report["reconciled_extractions"] += attempted
        report["stored_memories"] += stored
        report["recovered_retries"] += stored
    return report
