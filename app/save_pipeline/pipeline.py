from __future__ import annotations
"""
titan v2 pipeline logic (save + retrieval)

save flow (event-first):
1) input arrives as TraceEvent (or legacy TracePacketRequest).
2) event is appended to the event ledger with idempotent dedupe by (session_id, event_id).
3) only new events are processed (using per-session checkpoints).
4) each event is converted to extraction prompt text.
5) extractor emits atomic memories typed as rough/learnings.
6) memories are embedded and persisted with source_event_ids lineage.

retrieval flow:
1) router picks retrieval mode (rough / learnings / both) from query intent.
2) retriever runs filtered semantic search.
3) brief builder compacts top hits into a small memory brief.

design goal:
- keep save/retrieve reliable and independent from sidecars like graph rendering.
"""

import json
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.embedding.embedder import embed
from app.save_pipeline.extraction.adapters import get_extraction_adapter, get_extraction_adapter_with_config
from app.save_pipeline.extraction.extractor import (
    assess_memory_worthiness,
    build_safe_fallback_memories,
    extract_atomic_memories,
    _contains_durable_relational_signal,
    _contains_shallow_relational_signal,
)
from app.retrieval_pipeline.brief import build_memory_notes, build_scene_notes, build_timeline
from app.retrieval_pipeline.config import load_settings
from app.retrieval_pipeline.router import route_query
from app.storage.memories import (
    append_memories,
    create_memory_record,
    get_recent_memories,
)
from app.storage.models import IngestResult, Scene, SceneMessage, TraceEvent, TracePacketRequest
from app.storage.notes import append_memory_notes
from app.storage.scenes import append_scene, get_scene, get_scenes
from app.storage.sessions import BASE_DIR, ensure_dirs
from app.storage.traces import (
    append_retry_entry,
    append_event,
    append_trace,
    get_ledger_latest_ts,
    get_session_checkpoint,
    get_spool_cursor,
    get_spool_latest_ts,
    get_retry_queue_size,
    get_next_trace_turn,
    ingest_spool_file,
    load_message_context,
    load_unprocessed_events,
    cleanup_processed_spool_file,
    prune_processed_events,
    remove_retry_entries,
    update_session_checkpoint,
    get_pending_user_message,
    set_pending_user_message,
    clear_pending_user_message,
)
from app.storage.verifier import get_verifier
from app.save_pipeline.dedup_buffer import add_to_dedup_buffer
import logging


_TRACE_PROMPT_TEMPLATE = Template(
    """<role>
You are processing a trace packet from an agent execution.
</role>

<task>
Extract atomic memories from the following agent execution trace.
</task>

<input>
Goal: $goal
Thoughts: $thoughts
Tool Calls: $tool_block
Intent Phrase: $intent_phrase
Context: $context_block
</input>"""
)

LOGGER = logging.getLogger(__name__)


def _is_dedup_active(settings: Optional[Dict[str, Any]] = None) -> bool:
    env_val = os.getenv("TITAN_DEDUP_ENABLED")
    if env_val is not None:
        return env_val.strip().lower() not in {"0", "false", "no", "off"}
    if settings is not None:
        return bool(settings.get("dedup", {}).get("enabled", False))
    from app.retrieval_pipeline.config import load_settings
    return bool(load_settings().get("dedup", {}).get("enabled", False))


def run_memory_pipeline(
    session_id: str,
    turn: int,
    user_text: str,
    assistant_text: str,
    config_path: Optional[str] = None,
    source_event_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    outcome = run_memory_pipeline_outcome(
        session_id=session_id,
        turn=turn,
        user_text=user_text,
        assistant_text=assistant_text,
        config_path=config_path,
        source_event_ids=source_event_ids,
        fallback_enabled=True,
    )
    return outcome["records"]


def _memory_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def run_memory_pipeline_outcome(
    session_id: str,
    turn: int,
    user_text: str,
    assistant_text: str,
    config_path: Optional[str] = None,
    source_event_ids: Optional[List[str]] = None,
    fallback_enabled: bool = True,
    existing_text_hashes: Optional[set[str]] = None,
    scene: Optional[Scene] = None,
) -> Dict[str, Any]:
    # Core save stage: extract -> embed -> persist memory records.
    from app.retrieval_pipeline.config import load_settings

    worthiness = assess_memory_worthiness(user_text, assistant_text)
    if not worthiness["should_extract"]:
        return {
            "records": [],
            "fallback_used": False,
            "skipped_low_signal": True,
            "skip_reason": worthiness["skip_reason"] or "failed_quality_gate",
        }

    adapter = get_extraction_adapter_with_config(config_path) if config_path else get_extraction_adapter()

    settings = load_settings()
    extracted = extract_atomic_memories(user_text, assistant_text, adapter)
    fallback_used = False
    if not extracted and fallback_enabled and worthiness["allow_fallback"]:
        extracted = build_safe_fallback_memories(user_text, assistant_text)
        fallback_used = bool(extracted)

    if existing_text_hashes is not None and extracted:
        deduped: List[Dict[str, Any]] = []
        for memory in extracted:
            text = str(memory.get("text") or "").strip()
            if not text:
                continue
            text_hash = _memory_text_hash(text)
            if text_hash in existing_text_hashes:
                continue
            existing_text_hashes.add(text_hash)
            deduped.append(memory)
        extracted = deduped

    if settings.get("synthesize_implementation_outcomes", False) and scene is not None:
        synth = _synthesize_file_outcome_memory(scene, extracted)
        if synth is not None:
            extracted.append(synth)

    if not extracted:
        return {
            "records": [],
            "fallback_used": fallback_used,
            "skipped_low_signal": True,
            "skip_reason": "empty_after_filter",
        }

    texts = [mem["text"] for mem in extracted]
    try:
        vectors = embed(texts) if texts else []
    except Exception as exc:
        # Retrieval already supports keyword fallback when embeddings are unavailable,
        # so benchmark and low-cost runs can proceed without blocking on vector generation.
        LOGGER.warning("Embedding unavailable during memory save; storing records without vectors: %s", exc)
        vectors = []

    verifier = get_verifier()
    verification_enabled = settings.get("verification", {}).get("enabled", True)

    records: List[Dict[str, Any]] = []
    for idx, mem in enumerate(extracted):
        vector = vectors[idx] if idx < len(vectors) else None
        source_type = mem.get("source", "unknown")
        source_reliability = mem.get("reliability", 0.5)

        verification_status = "unverified"
        if verification_enabled and source_type != "user":
            result = verifier.verify_memory(mem["text"])
            if result.verified and result.confidence > 0.7:
                verification_status = "verified"
                source_reliability = max(source_reliability, result.confidence)

        records.append(
            create_memory_record(
                session_id=session_id,
                turn=turn,
                index=idx,
                text=mem["text"],
                user_text=user_text,
                assistant_text=assistant_text,
                scene_id=scene.scene_id if scene else None,
                memory_type=mem.get("type"),
                stream=mem.get("stream", "rough"),
                embedding=vector.tolist() if vector is not None else None,
                source_event_ids=source_event_ids,
                source_type=source_type,
                source_reliability=source_reliability,
                verification_status=verification_status,
                fallback_generated=fallback_used,
                speaker_focus=mem.get("speaker_focus"),
                memory_kind=mem.get("memory_kind"),
            )
        )

    append_memories(records)
    if _is_dedup_active(settings):
        add_to_dedup_buffer(records)
    if scene is not None:
        append_scene(scene)
    append_memory_notes(records)
    return {"records": records, "fallback_used": fallback_used, "skipped_low_signal": False, "skip_reason": None}


def _build_trace_prompt(req: TracePacketRequest) -> tuple[str, str]:
    tool_calls = [call.model_dump() for call in req.tool_calls]
    tool_block = json.dumps(tool_calls, indent=2, default=str)
    thoughts = req.thoughts or ""
    context_block = json.dumps(req.context, indent=2, default=str) if req.context else ""
    intent_phrase = req.intent_phrase or ""

    user_text = _TRACE_PROMPT_TEMPLATE.substitute(
        goal=req.goal,
        thoughts=thoughts,
        tool_block=tool_block,
        intent_phrase=intent_phrase,
        context_block=context_block,
    )
    assistant_text = f"Outcome: {req.outcome}"
    return user_text, assistant_text


def _clean_trace_message_text(value: Optional[str]) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if cleaned.lower().startswith("conversation:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    return cleaned


def _is_transport_bridge_trace(req: TracePacketRequest) -> bool:
    context = req.context if isinstance(req.context, dict) else {}
    channel = str(context.get("channel") or "").strip().lower()
    source = str(context.get("source") or "").strip().lower()
    intent_phrase = str(req.intent_phrase or "").strip().lower()
    return (
        source == "openclaw-hook:titan-karu-bridge"
        or (channel in {"telegram", "discord"} and bool(context.get("conversation_key")))
        or intent_phrase.endswith("inbound memory capture")
        or intent_phrase.endswith("outbound memory capture")
    )


_GENERIC_USER_OUTCOME_PREFIXES = (
    "user message in conversation with ",
    "user message in a conversation with ",
)

_GENERIC_ASSISTANT_GOAL_PREFIXES = (
    "assistant response in conversation with ",
    "assistant response in a conversation with ",
)


def _transport_trace_mode(channel: str) -> str:
    normalized = str(channel or "").strip().lower()
    if normalized == "telegram":
        return "telegram_legacy_bridge"
    if normalized:
        return f"{normalized}_bridge"
    return "transport_bridge"


def _normalize_transport_trace_prompt(req: TracePacketRequest) -> Dict[str, Any]:
    context = req.context if isinstance(req.context, dict) else {}
    channel = str(context.get("channel") or "").strip().lower()
    direction = str(context.get("direction") or "inbound").strip().lower()
    trace_mode = _transport_trace_mode(channel)

    raw_goal = _clean_trace_message_text(req.goal)
    raw_thoughts = _clean_trace_message_text(req.thoughts)
    raw_outcome = _clean_trace_message_text(req.outcome)
    paired_user_text = _clean_trace_message_text(context.get("paired_user_text"))

    transport_context = {
        "channel": channel or None,
        "direction": direction or None,
        "conversation_key": context.get("conversation_key"),
        "inbound_message_id": context.get("inbound_message_id") or context.get("paired_inbound_message_id"),
        "outbound_message_id": context.get("outbound_message_id"),
        "trace_mode": trace_mode,
    }

    normalized_user = ""
    normalized_assistant = ""

    if direction == "outbound":
        normalized_user = paired_user_text
        if not normalized_user and not any(raw_goal.lower().startswith(prefix) for prefix in _GENERIC_ASSISTANT_GOAL_PREFIXES):
            normalized_user = raw_goal
        normalized_assistant = raw_outcome or raw_thoughts
    else:
        normalized_user = raw_goal or raw_thoughts
        normalized_assistant = "" if raw_outcome.lower().startswith(_GENERIC_USER_OUTCOME_PREFIXES) else raw_outcome

    if not normalized_user and not normalized_assistant:
        return {
            "user_text": "",
            "assistant_text": "",
            "trace_mode": trace_mode,
            "transport_context": transport_context,
            "skip_reason": "transport_bridge_empty",
        }

    if direction == "outbound" and not normalized_user:
        return {
            "user_text": "",
            "assistant_text": normalized_assistant,
            "trace_mode": trace_mode,
            "transport_context": transport_context,
            "skip_reason": "transport_bridge_outbound_unpaired",
        }

    if normalized_user and _contains_shallow_relational_signal(normalized_user) and not _contains_durable_relational_signal(normalized_user):
        return {
            "user_text": normalized_user,
            "assistant_text": "",
            "trace_mode": trace_mode,
            "transport_context": transport_context,
            "skip_reason": "transport_bridge_shallow_social",
        }

    return {
        "user_text": normalized_user,
        "assistant_text": normalized_assistant,
        "trace_mode": trace_mode,
        "transport_context": transport_context,
        "skip_reason": None,
    }


def _extract_message_updated_metadata(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    payload = event.get("payload") or {}
    if str(payload.get("raw_type") or "") != "message.updated":
        return None, None, None

    info = (((payload.get("body") or {}).get("properties") or {}).get("info") or {})
    message_id = info.get("id")
    role = info.get("role")
    parent_id = info.get("parentID")
    if not isinstance(message_id, str) or not isinstance(role, str):
        return None, None, None
    if not isinstance(parent_id, str):
        parent_id = None
    return message_id, role, parent_id


def _extract_message_updated_text(event: Dict[str, Any]) -> Optional[str]:
    payload = event.get("payload") or {}
    if str(payload.get("raw_type") or "") != "message.updated":
        return None

    body = payload.get("body") or {}
    properties = body.get("properties") or {}
    info = properties.get("info") or {}
    candidates = [
        info.get("summary"),
        info.get("text"),
        info.get("content"),
        properties.get("text"),
        properties.get("content"),
        body.get("text"),
        body.get("content"),
    ]
    for value in candidates:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _extract_message_part(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    payload = event.get("payload") or {}
    if str(payload.get("raw_type") or "") != "message.part.updated":
        return None, None

    part = ((((payload.get("body") or {}).get("properties") or {}).get("part") or {}))
    if part.get("type") != "text":
        return None, None

    message_id = part.get("messageID")
    text = part.get("text")
    if not isinstance(message_id, str) or not isinstance(text, str):
        return None, None
    text = text.strip()
    if not text:
        return None, None
    return message_id, text


def _is_latest_message_part_snapshot(events: List[Dict[str, Any]], index: int, message_id: str, text: str) -> bool:
    current_text = text.strip()
    if not current_text:
        return False

    for later in events[index + 1 :]:
        later_message_id, later_text = _extract_message_part(later)
        if later_message_id != message_id or not later_text:
            continue
        later_clean = later_text.strip()
        if len(later_clean) >= len(current_text) and later_clean.startswith(current_text):
            return False
    return True


def _build_event_prompt(
    event: Dict[str, Any],
    *,
    role_by_message_id: Optional[Dict[str, str]] = None,
    parent_by_message_id: Optional[Dict[str, str]] = None,
    latest_text_by_message_id: Optional[Dict[str, str]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    index: Optional[int] = None,
    fallback_user_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    event_type = str(event.get("event_type") or "unknown")
    payload = event.get("payload") or {}

    if event_type == "trace_packet":
        req = TracePacketRequest(
            goal=str(payload.get("goal") or ""),
            thoughts=payload.get("thoughts"),
            tool_calls=payload.get("tool_calls") or [],
            outcome=str(payload.get("outcome") or ""),
            session_id=event.get("session_id"),
            event_id=event.get("event_id"),
            save_intent=payload.get("save_intent"),
            intent_phrase=payload.get("intent_phrase"),
            context=payload.get("context"),
        )
        if _is_transport_bridge_trace(req):
            normalized = _normalize_transport_trace_prompt(req)
            return {
                "user_text": normalized["user_text"],
                "assistant_text": normalized["assistant_text"],
                "used_context_fallback": False,
                "trace_mode": normalized["trace_mode"],
                "transport_context": normalized["transport_context"],
                "skip_reason": normalized["skip_reason"],
            }
        user_text, assistant_text = _build_trace_prompt(req)
        return {
            "user_text": user_text,
            "assistant_text": assistant_text,
            "used_context_fallback": False,
            "trace_mode": "generic_trace",
            "transport_context": {},
            "skip_reason": None,
        }

    if event_type == "assistant_message":
        assistant_text = str(payload.get("content") or "").strip()
        user_text = str(fallback_user_text or "").strip()
        if not user_text or not assistant_text:
            return None
        return {
            "user_text": user_text,
            "assistant_text": assistant_text,
            "used_context_fallback": False,
            "trace_mode": "pi_message_pair",
            "transport_context": {},
            "skip_reason": None,
        }

    message_id, text = _extract_message_part(event)
    if message_id and text:
        role = (role_by_message_id or {}).get(message_id, "")
        if role != "assistant":
            return None
        if events is None or index is None:
            return None
        if not _is_latest_message_part_snapshot(events, index, message_id, text):
            return None
        parent_id = (parent_by_message_id or {}).get(message_id)
        user_text = ""
        used_context_fallback = False
        if parent_id:
            user_text = ((latest_text_by_message_id or {}).get(parent_id) or "").strip()
        if not user_text and fallback_user_text:
            user_text = f"[approximate prior user context] {fallback_user_text.strip()}"
            used_context_fallback = True
        assistant_text = text.strip()
        if not user_text or not assistant_text:
            return None
        return {
            "user_text": user_text,
            "assistant_text": assistant_text,
            "used_context_fallback": used_context_fallback,
            "trace_mode": "message_pair",
            "transport_context": {},
            "skip_reason": None,
        }

    return None


def _make_scene_id(session_id: str, anchor_event_id: Optional[str], turn: int) -> str:
    cleaned_session = str(session_id or "default")
    cleaned_anchor = str(anchor_event_id or f"turn-{turn}")
    return f"{cleaned_session}:scene:{cleaned_anchor}"


def _scene_raw_events(*events: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Raw events are temporary ingest scaffolding. Scenes keep compact, structured content instead.
    return []


_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")


def _compact_text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except TypeError:
            text = str(value)
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


_MUTATING_FILE_TOOL_NAMES = {
    "edit",
    "write",
    "file_event",
    "file_edit",
    "apply_patch",
    "patch",
    "write_file",
    "replace_file",
}


def _is_mutating_file_tool(name: str) -> bool:
    normalized = str(name or "").strip().lower().replace("-", "_")
    short_name = normalized.rsplit(".", 1)[-1]
    return normalized in _MUTATING_FILE_TOOL_NAMES or short_name in _MUTATING_FILE_TOOL_NAMES


def _synthesize_file_outcome_memory(
    scene: Scene,
    extracted: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    paths: List[str] = []
    seen: set[str] = set()
    for tc in scene.tool_calls:
        if not _is_mutating_file_tool(tc.name):
            continue
        for fp in tc.file_paths:
            if fp not in seen:
                seen.add(fp)
                paths.append(fp)

    if not paths:
        return None

    lower_texts = [str(mem.get("text") or "").lower() for mem in extracted]
    paths = [p for p in paths if not any(p.lower() in t for t in lower_texts)]
    if not paths:
        return None

    paths.sort()
    if len(paths) > 8:
        paths = paths[:8]

    return {
        "text": "Modified files: " + ", ".join(paths),
        "stream": "rough",
        "source": "system",
        "reliability": 1.0,
        "speaker_focus": "system",
        "memory_kind": "outcome",
    }


def _extract_file_paths(*values: Any) -> List[str]:
    paths: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact_text(value, limit=4000)
        for match in _PATH_PATTERN.findall(text):
            cleaned = match.strip().strip("'\"`.,:;()[]{}")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                paths.append(cleaned)
    return paths[:12]


def _tool_status(output: Any) -> str:
    if isinstance(output, dict):
        if output.get("error") or output.get("stderr"):
            return "error"
        if output.get("status"):
            return str(output.get("status"))
    return "success" if output is not None else "unknown"


def _summarize_tool_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = event.get("payload") or {}
    event_type = str(event.get("event_type") or "")
    raw_type = str(payload.get("raw_type") or "")
    event_id = str(event.get("event_id") or "").strip() or None

    if event_type == "tool_execution" or raw_type == "tool.execute.after":
        tool_name = str(payload.get("tool") or payload.get("name") or "tool").strip() or "tool"
        args = payload.get("args") or {}
        output = payload.get("output")
        file_paths = _extract_file_paths(args, output)
        target = f" on {', '.join(file_paths[:3])}" if file_paths else ""
        return {
            "name": tool_name,
            "call_id": payload.get("call_id"),
            "status": _tool_status(output),
            "summary": f"{tool_name}{target}",
            "file_paths": file_paths,
            "excerpt": _compact_text(output, limit=500) or None,
            "event_id": event_id,
        }

    if event_type == "file_edit" or raw_type.startswith("file."):
        body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
        file_paths = _extract_file_paths(body)
        return {
            "name": "file_event",
            "call_id": None,
            "status": "success",
            "summary": f"file event for {', '.join(file_paths[:3])}" if file_paths else "file event",
            "file_paths": file_paths,
            "excerpt": None,
            "event_id": event_id,
        }

    return None


def _build_scene_candidate(
    event: Dict[str, Any],
    turn: int,
    prompt: Dict[str, Any],
    *,
    assistant_message_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Scene:
    event_type = str(event.get("event_type") or "unknown")
    session_id = str(event.get("session_id") or "default")
    event_id = str(event.get("event_id") or "").strip() or None
    seq = int(event.get("seq") or 0)
    timestamp = str(event.get("ts") or datetime.now().isoformat())
    user_text = str(prompt.get("user_text") or "")
    assistant_text = str(prompt.get("assistant_text") or "")
    used_context_fallback = bool(prompt.get("used_context_fallback", False))

    if event_type == "trace_packet":
        messages = [
            SceneMessage(role="system", content=user_text, message_id=None, event_id=event_id),
            SceneMessage(role="assistant", content=assistant_text, message_id=None, event_id=event_id),
        ]
        kind = "trace_packet"
    else:
        messages = [
            SceneMessage(role="user", content=user_text, message_id=parent_message_id, event_id=None),
            SceneMessage(role="assistant", content=assistant_text, message_id=assistant_message_id, event_id=event_id),
        ]
        kind = "message_exchange"

    compact_tool_calls = list(tool_calls or [])
    source_event_ids = [event_id] if event_id else []
    for tool_call in compact_tool_calls:
        tool_event_id = str(tool_call.get("event_id") or "").strip()
        if tool_event_id and tool_event_id not in source_event_ids:
            source_event_ids.append(tool_event_id)

    return Scene(
        scene_id=_make_scene_id(session_id, event_id, turn),
        session_id=session_id,
        turn=turn,
        kind=kind,
        scene_seq=seq or None,
        start_event_seq=seq or None,
        end_event_seq=seq or None,
        anchor_event_id=event_id,
        source_event_ids=source_event_ids,
        raw_events=_scene_raw_events(event),
        messages=messages,
        tool_calls=compact_tool_calls,
        extraction_user_text=user_text,
        extraction_assistant_text=assistant_text,
        used_context_fallback=used_context_fallback,
        ts=timestamp,
    )


def _build_raw_event_scene(event: Dict[str, Any]) -> Scene:
    session_id = str(event.get("session_id") or "default")
    event_id = str(event.get("event_id") or "").strip() or None
    seq = int(event.get("seq") or 0)
    timestamp = str(event.get("ts") or datetime.now().isoformat())
    turn = seq or 0
    return Scene(
        scene_id=_make_scene_id(session_id, event_id, turn),
        session_id=session_id,
        turn=turn,
        kind="raw_event",
        scene_seq=seq or None,
        start_event_seq=seq or None,
        end_event_seq=seq or None,
        anchor_event_id=event_id,
        source_event_ids=[event_id] if event_id else [],
        raw_events=_scene_raw_events(event),
        messages=[],
        tool_calls=[],
        extraction_user_text="",
        extraction_assistant_text="",
        used_context_fallback=False,
        ts=timestamp,
    )


def _recap_from_records(records: List[Dict[str, Any]]) -> str:
    texts = [str(record.get("text")) for record in records if record.get("text")]
    if not texts:
        return "No memories extracted."
    return " ".join(texts[:3])


def process_session_events(session_id: str, limit: int = 200) -> Dict[str, Any]:
    # Process only events that were not previously checkpointed for this session.
    events = load_unprocessed_events(session_id, limit=limit)
    if not events:
        return {
            "processed_events": 0,
            "prompt_candidates": 0,
            "stored_memories": 0,
            "fallback_memories": 0,
            "queued_retries": 0,
            "skipped_low_signal": 0,
            "skip_reasons": {},
        }

    stored_memories = 0
    prompt_candidates = 0
    fallback_memories = 0
    queued_retries = 0
    skipped_low_signal = 0
    skip_reasons: Dict[str, int] = {}
    turn = get_next_trace_turn(session_id)

    role_by_message_id, parent_by_message_id, latest_text_by_message_id = load_message_context(session_id)
    recent_user_text = get_pending_user_message(session_id)
    pending_tool_calls: List[Dict[str, Any]] = []
    for message_id, role in role_by_message_id.items():
        if role != "user":
            continue
        text = (latest_text_by_message_id.get(message_id) or "").strip()
        if text:
            recent_user_text = text

    for index, event in enumerate(events):
        event_id = str(event.get("event_id") or "")
        seq = int(event.get("seq", 0))
        tool_summary = _summarize_tool_event(event)
        if tool_summary:
            pending_tool_calls.append(tool_summary)
            update_session_checkpoint(session_id, seq)
            continue

        payload = event.get("payload") or {}
        event_type = str(event.get("event_type") or "")
        if event_type == "user_message":
            user_text = str(payload.get("content") or "").strip()
            if user_text:
                recent_user_text = user_text
                set_pending_user_message(session_id, user_text, seq=seq, event_id=event_id or None)
            update_session_checkpoint(session_id, seq)
            continue

        message_id, role, parent_id = _extract_message_updated_metadata(event)
        if message_id and role:
            role_by_message_id[message_id] = role
            if parent_id:
                parent_by_message_id[message_id] = parent_id
            if role == "user":
                user_text_from_update = _extract_message_updated_text(event)
                if user_text_from_update:
                    latest_text_by_message_id[message_id] = user_text_from_update
                    recent_user_text = user_text_from_update
                    set_pending_user_message(session_id, user_text_from_update, seq=seq, event_id=event_id or None)

        message_id, text = _extract_message_part(event)
        if message_id and text and _is_latest_message_part_snapshot(events, index, message_id, text):
            latest_text_by_message_id[message_id] = text
            if role_by_message_id.get(message_id) == "user":
                recent_user_text = text
                set_pending_user_message(session_id, text, seq=seq, event_id=event_id or None)

        payload = event.get("payload") or {}
        save_intent = payload.get("save_intent")
        if save_intent is None:
            save_intent = True

        if save_intent:
            prompt = _build_event_prompt(
                event,
                role_by_message_id=role_by_message_id,
                parent_by_message_id=parent_by_message_id,
                latest_text_by_message_id=latest_text_by_message_id,
                events=events,
                index=index,
                fallback_user_text=recent_user_text,
            )
            if prompt is None:
                update_session_checkpoint(session_id, seq)
                continue
            prompt_skip_reason = str(prompt.get("skip_reason") or "")
            scene = _build_scene_candidate(
                event,
                turn,
                prompt,
                assistant_message_id=message_id,
                parent_message_id=parent_id,
                tool_calls=pending_tool_calls,
            )
            append_scene(scene)
            pending_tool_calls = []
            if str(prompt.get("trace_mode") or "") == "pi_message_pair":
                clear_pending_user_message(session_id)
            if prompt_skip_reason:
                skipped_low_signal += 1
                skip_reasons[prompt_skip_reason] = skip_reasons.get(prompt_skip_reason, 0) + 1
                update_session_checkpoint(session_id, seq)
                continue
            prompt_candidates += 1
            try:
                outcome = run_memory_pipeline_outcome(
                    session_id=session_id,
                    turn=turn,
                    user_text=prompt["user_text"],
                    assistant_text=prompt["assistant_text"],
                    source_event_ids=[event_id] if event_id else None,
                    fallback_enabled=True,
                    scene=scene,
                )
            except Exception as exc:  # pragma: no cover - defensive retry path
                if event_id and seq > 0:
                    append_retry_entry(
                        {
                            "session_id": session_id,
                            "event_id": event_id,
                            "seq": seq,
                            "reason": f"pipeline_error:{exc.__class__.__name__}",
                        }
                    )
                    queued_retries += 1
                update_session_checkpoint(session_id, seq)
                continue

            records = outcome["records"]
            if records:
                stored_memories += len(records)
                if outcome.get("fallback_used"):
                    fallback_memories += len(records)
                if event_id:
                    remove_retry_entries(session_id, {event_id})
                turn += 1
            else:
                skipped_low_signal += 1
                skip_reason = str(outcome.get("skip_reason") or "empty_after_filter")
                skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1

        else:
            pending_tool_calls = []

        update_session_checkpoint(session_id, seq)

    return {
        "processed_events": len(events),
        "prompt_candidates": prompt_candidates,
        "stored_memories": stored_memories,
        "fallback_memories": fallback_memories,
        "queued_retries": queued_retries,
        "skipped_low_signal": skipped_low_signal,
        "skip_reasons": skip_reasons,
    }


def ingest_trace_event(event: TraceEvent, process_new: bool = True) -> Dict[str, Any]:
    # Idempotent ingest boundary for event-first pipeline.
    ensure_dirs()
    status, seq = append_event(event.model_dump())

    result = IngestResult(
        status="duplicate" if status == "duplicate" else "ingested",
        session_id=event.session_id,
        event_id=event.event_id,
        message="already ingested" if status == "duplicate" else "ingested",
        seq=seq,
    )

    payload = result.model_dump()
    if status != "duplicate" and process_new:
        payload.update(process_session_events(event.session_id))
    return payload


def ingest_spool_session(session_id: str, spool_dir: str = ".opencode/titan/traces") -> Dict[str, Any]:
    ensure_dirs()
    spool_path = Path(spool_dir)
    ingest_counts = ingest_spool_file(session_id, spool_path)
    sessions_from_spool = ingest_counts.get("sessions_touched") or []
    processed_sessions = sorted({str(item) for item in sessions_from_spool if str(item)})
    if not processed_sessions:
        processed_sessions = [session_id]

    aggregate_counts = {
        "processed_events": 0,
        "prompt_candidates": 0,
        "stored_memories": 0,
        "fallback_memories": 0,
        "queued_retries": 0,
        "skipped_low_signal": 0,
        "skip_reasons": {},
    }
    for processed_session_id in processed_sessions:
        process_counts = process_session_events(processed_session_id)
        for key in aggregate_counts:
            if key == "skip_reasons":
                for reason, count in (process_counts.get("skip_reasons") or {}).items():
                    aggregate_counts["skip_reasons"][reason] = aggregate_counts["skip_reasons"].get(reason, 0) + int(count)
            else:
                aggregate_counts[key] += int(process_counts.get(key) or 0)

    pruned = {"before": 0, "after": 0, "removed": 0}
    spool_cleanup = {"deleted": False, "reason": "not_attempted"}
    unprocessed_after = sum(len(load_unprocessed_events(session_id=item, limit=1)) for item in processed_sessions)
    if int(aggregate_counts.get("queued_retries") or 0) == 0 and unprocessed_after == 0:
        pruned = prune_processed_events(processed_sessions)
        spool_cleanup = cleanup_processed_spool_file(
            spool_path / f"{session_id}.jsonl",
            ingest_counts,
            allow_delete=not bool(ingest_counts.get("hit_cap")) and not bool(ingest_counts.get("partial_line")),
        )
    elif unprocessed_after:
        spool_cleanup = {"deleted": False, "reason": "unprocessed_ledger_events", "remaining_sessions": unprocessed_after}

    return {
        "session_id": session_id,
        "spool_file": str(spool_path / f"{session_id}.jsonl"),
        **ingest_counts,
        **aggregate_counts,
        "processed_sessions": processed_sessions,
        "unprocessed_after": unprocessed_after,
        "pruned_events": pruned,
        "spool_cleanup": spool_cleanup,
        "retry_queue_size": sum(get_retry_queue_size(session_id=item) for item in processed_sessions),
    }


def get_pipeline_debug_status(session_id: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "session_id": session_id,
        "retry_queue_size": get_retry_queue_size(session_id=session_id),
    }
    if not session_id:
        return payload

    settings = load_settings()
    debug_enabled = bool(settings.get("ingest_debug_metrics_enabled", True))
    if not debug_enabled:
        return payload
    # Match the auto-ingest worker's default. Older settings may still contain
    # the OpenCode-specific `.opencode/titan/traces` path, which is misleading
    # for Pi's workspace unless TITAN_SPOOL_DIR explicitly overrides it.
    configured_spool_dir = os.getenv("TITAN_SPOOL_DIR")
    if configured_spool_dir:
        spool_dir_value = str(configured_spool_dir)
    else:
        spool_dir_value = str(Path(os.getenv("TITAN_HOME", str(BASE_DIR))) / "traces")
    spool_dir = Path(spool_dir_value)
    if not spool_dir.is_absolute():
        spool_dir = BASE_DIR / spool_dir
    spool_file = spool_dir / f"{session_id}.jsonl"

    spool_cursor = get_spool_cursor(spool_file)
    spool_latest_ts = get_spool_latest_ts(session_id=session_id, spool_file=spool_file)
    ledger_latest_ts = get_ledger_latest_ts(session_id=session_id)
    checkpoint_seq = get_session_checkpoint(session_id)
    unprocessed_event_count = len(load_unprocessed_events(session_id))

    lag_seconds: Optional[float] = None
    if spool_latest_ts and ledger_latest_ts:
        spool_dt = _safe_parse_iso(spool_latest_ts)
        ledger_dt = _safe_parse_iso(ledger_latest_ts)
        if spool_dt and ledger_dt:
            lag_seconds = max(0.0, (spool_dt - ledger_dt).total_seconds())

    payload.update(
        {
            "spool_file": str(spool_file),
            "spool_cursor": spool_cursor,
            "spool_latest_ts": spool_latest_ts,
            "ledger_latest_ts": ledger_latest_ts,
            "checkpoint_seq": checkpoint_seq,
            "unprocessed_event_count": unprocessed_event_count,
            "lag_seconds": lag_seconds,
        }
    )
    return payload


def _safe_parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def handle_trace_packet(req: TracePacketRequest) -> Dict[str, Any]:
    # Compatibility bridge: converts legacy trace packets into TraceEvent.
    ensure_dirs()
    session_id = req.session_id or "trace"

    append_trace(
        {
            "session_id": session_id,
            "goal": req.goal,
            "thoughts": req.thoughts,
            "tool_calls": [call.model_dump() for call in req.tool_calls],
            "outcome": req.outcome,
            "save_intent": req.save_intent,
            "intent_phrase": req.intent_phrase,
            "context": req.context,
        }
    )

    event = TraceEvent(
        session_id=session_id,
        event_id=req.event_id or uuid4().hex,
        event_type="trace_packet",
        ts=None,
        payload={
            "goal": req.goal,
            "thoughts": req.thoughts,
            "tool_calls": [call.model_dump() for call in req.tool_calls],
            "outcome": req.outcome,
            "save_intent": req.save_intent,
            "intent_phrase": req.intent_phrase,
            "context": req.context,
        },
        schema_version="v1",
    )

    ingest_result = ingest_trace_event(event)
    save_intent = req.save_intent if req.save_intent is not None else True
    records: List[Dict[str, Any]] = []

    if save_intent and ingest_result.get("stored_memories"):
        recent = get_recent_memories(limit=3, session_id=session_id)
        records = [mem.model_dump() for mem in recent]

    recap = _recap_from_records(records) if save_intent else "Memory storage skipped (save_intent=false)."
    memory_status = "stored" if save_intent else "skipped"
    if ingest_result.get("status") == "duplicate":
        memory_status = "duplicate"

    return {
        "session_id": session_id,
        "memory_status": memory_status,
        "recap": recap,
        "stored": bool(save_intent and ingest_result.get("status") != "duplicate"),
        "store_reason": None if save_intent else "save_intent=false",
        "ingest": ingest_result,
    }


def retrieve_memory_brief(
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 8,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_scenes: bool = True,
) -> Dict[str, Any]:
    from app.retrieval_pipeline.retriever import retrieve_memories
    from app.retrieval_pipeline.config import load_settings

    settings = load_settings()
    safe_query = query or ""
    route = route_query(safe_query)
    if not bool(route.get("use_memory", True)):
        return {
            "query": safe_query,
            "mode": "none",
            "count": 0,
            "memories": [],
            "scenes": [],
            "brief": "Memory disabled for this query (fresh context requested).",
            "scene_brief": "",
            "route": route,
        }

    selected_mode = mode or str(route.get("mode") or "both")
    selected_limit = limit if limit is not None else int(route.get("top_k") or 8)
    selected_intent = str(route.get("intent") or "balanced")
    hits = retrieve_memories(
        safe_query,
        session_id=session_id,
        top_k=selected_limit,
        mode=selected_mode,
        intent=selected_intent,
        date_from=date_from,
        date_to=date_to,
    )
    brief = build_memory_notes(
        hits, max_items=max_items, max_chars=max_chars,
        cluster_mode=settings.get("step2", {}).get("cluster_compression_enabled", False),
    )

    memories = []
    for hit in hits:
        memory = dict(hit.get("memory", {}))
        # Internal embedding storage fields are useful inside retrieval, but must not leak into JSON responses.
        memory.pop("_embedding_blob", None)
        memory.pop("_embedding_dim", None)
        memory.pop("_embedding_dtype", None)
        memories.append(memory)

    response: Dict[str, Any] = {
        "query": safe_query,
        "mode": selected_mode,
        "count": len(memories),
        "memories": memories,
        "scenes": [],
        "brief": brief,
        "scene_brief": "",
        "route": route,
    }

    if include_scenes:
        ordered_scene_ids: List[str] = []
        seen_scene_ids: set[str] = set()
        for memory in memories:
            scene_id = str(memory.get("scene_id") or "").strip()
            if not scene_id or scene_id in seen_scene_ids:
                continue
            seen_scene_ids.add(scene_id)
            ordered_scene_ids.append(scene_id)
        if ordered_scene_ids:
            scenes = [scene.model_dump() for scene in get_scenes(ordered_scene_ids)]
            response["scenes"] = scenes
            response["scene_brief"] = build_scene_notes(scenes, max_items=max_items, max_chars=max_chars)

    if route.get("summary_mode") == "timeline":
        response.update(build_timeline(memories, max_items=max_items, max_chars=max_chars))

    return response


def get_scene_context(scene_id: str) -> Dict[str, Any]:
    normalized_scene_id = str(scene_id or "").strip()
    if not normalized_scene_id:
        return {"error": "scene_id is required", "scene_id": normalized_scene_id}

    scene = get_scene(normalized_scene_id)
    if not scene:
        return {"error": "scene not found", "scene_id": normalized_scene_id}

    return {"scene": scene.model_dump()}
