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
from typing import Any, Dict, List, Literal, Optional, Tuple
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
from app.retrieval_pipeline.brief import build_memory_notes, build_timeline
from app.retrieval_pipeline.config import load_settings
from app.retrieval_pipeline.router import route_query
from app.storage.memories import (
    append_memories,
    create_memory_record,
    get_recent_memories,
)
from app.storage.models import IngestResult, Scene, SceneMessage, SceneReference, TraceEvent, TracePacketRequest
from app.storage.notes import append_memory_notes
from app.storage.scenes import append_scene, get_scene, get_scene_references, get_scenes, get_session_scenes  # compatibility for retrieval test patches
from app.storage.sessions import BASE_DIR, ensure_dirs, interprocess_lock
from app.storage.traces import (
    append_retry_entry,
    append_event,
    append_trace,
    get_ledger_latest_ts,
    get_session_checkpoint,
    get_spool_cursor,
    get_spool_latest_ts,
    get_retry_queue_size,
    get_scene_checkpoint,
    get_next_trace_turn,
    load_event_index,
    load_retry_queue,
    ingest_spool_file,
    load_message_context,
    load_events_for_session,
    load_unprocessed_events,
    cleanup_processed_spool_file,
    prune_processed_events,
    remove_retry_entries,
    update_session_checkpoint,
    get_pending_user_message,
    get_pending_user_message_seq,
    load_pending_user_messages,
    mark_scene_events_finalized,
    sanitize_trace_value,
    set_pending_scene_events,
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
    persist_scene: bool = True,
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
    if scene is not None and persist_scene:
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


_SCENE_EVIDENCE_EVENT_TYPES = {
    "assistant_message",
    "file_edit",
    "message",
    "message_part",
    "tool_call",
    "tool_execution",
    "tool_result",
    "trace_packet",
    "user_message",
}


def _is_scene_evidence_event(event: Dict[str, Any]) -> bool:
    """Return whether an admitted event can be evidence inside a scene."""

    event_type = str(event.get("event_type") or "").strip()
    if event_type in _SCENE_EVIDENCE_EVENT_TYPES:
        return True
    payload = event.get("payload") or {}
    raw_type = str(payload.get("raw_type") or "").strip()
    return raw_type.startswith(("message.", "tool.", "file."))


def _canonical_scene_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Build the sanitized, normalized event representation stored in a scene."""

    try:
        seq = int(event.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0

    return sanitize_trace_value(
        {
            "seq": seq,
            "ts": str(event.get("ts") or ""),
            "session_id": str(event.get("session_id") or "default"),
            "event_id": str(event.get("event_id") or ""),
            "event_type": str(event.get("event_type") or "unknown"),
            "payload": event.get("payload") or {},
            "schema_version": str(event.get("schema_version") or "v1"),
        }
    )


def _scene_event_key(event: Dict[str, Any]) -> Tuple[str, str]:
    return (
        str(event.get("session_id") or "default"),
        str(event.get("event_id") or ""),
    )


def _scene_raw_events(*events: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return ordered, deduplicated, sanitized canonical event evidence.

    The event ledger already applies this normalization at ingest time, but
    scene construction also receives events from tests, replay paths, and
    adapters. Reapplying the boundary here keeps scenes self-contained and
    prevents an unsanitized adapter payload from becoming durable evidence.
    Lifecycle-only events are deliberately omitted; they are finalized by the
    processing checkpoint without becoming conversation evidence.
    """

    normalized: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or not _is_scene_evidence_event(event):
            continue
        canonical = _canonical_scene_event(event)
        if not str(canonical.get("event_id") or "").strip():
            continue
        key = _scene_event_key(canonical)
        current = normalized.get(key)
        if current is None or int(canonical.get("seq") or 0) < int(current.get("seq") or 0):
            normalized[key] = canonical

    return sorted(
        normalized.values(),
        key=lambda item: (int(item.get("seq") or 0), str(item.get("event_id") or "")),
    )


def _load_pending_scene_events(session_id: str) -> List[Dict[str, Any]]:
    pending = load_pending_user_messages().get(session_id)
    if not isinstance(pending, dict):
        return []
    evidence = pending.get("scene_evidence")
    if not isinstance(evidence, dict):
        return []
    raw_events = evidence.get("events")
    if not isinstance(raw_events, list):
        return []
    return _scene_raw_events(*[item for item in raw_events if isinstance(item, dict)])


def _save_pending_scene_events(session_id: str, events: List[Dict[str, Any]]) -> None:
    canonical_events = _scene_raw_events(*events)
    set_pending_scene_events(session_id, canonical_events)


def _set_pending_user_context_preserving_scene(
    session_id: str,
    content: str,
    *,
    seq: int,
    event_id: Optional[str],
    scene_events: List[Dict[str, Any]],
) -> None:
    """Update user context without replacing the durable scene evidence record."""

    set_pending_user_message(session_id, content, seq=seq, event_id=event_id)


def _event_message_role(event: Dict[str, Any], role_by_message_id: Optional[Dict[str, str]] = None) -> str:
    event_type = str(event.get("event_type") or "")
    if event_type == "user_message":
        return "user"
    if event_type == "assistant_message":
        return "assistant"
    payload = event.get("payload") or {}
    body = payload.get("body") or {}
    properties = body.get("properties") or {}
    info = properties.get("info") or {}
    part = properties.get("part") or {}
    message_id = info.get("id") or part.get("messageID") or part.get("id")
    return str(info.get("role") or part.get("role") or (role_by_message_id or {}).get(message_id) or "")


def _latest_pending_user_text(
    events: List[Dict[str, Any]],
    role_by_message_id: Optional[Dict[str, str]] = None,
) -> str:
    candidates: List[Tuple[int, str]] = []
    for event in events:
        if _event_message_role(event, role_by_message_id) != "user":
            continue
        payload = event.get("payload") or {}
        text = str(payload.get("content") or "").strip()
        if not text:
            text = str(_extract_message_updated_text(event) or "").strip()
        if not text:
            _message_id, part_text = _extract_message_part(event)
            text = str(part_text or "").strip()
        if text:
            candidates.append((int(event.get("seq") or 0), text))
    return sorted(candidates)[-1][1] if candidates else ""


def _pending_scene_waits_for_assistant(
    events: List[Dict[str, Any]],
    role_by_message_id: Optional[Dict[str, str]] = None,
) -> bool:
    """Keep an open user turn durable until an assistant boundary arrives."""

    latest_user_seq = 0
    latest_assistant_event_seq = 0
    latest_assistant_text_seq = 0
    for event in events:
        role = _event_message_role(event, role_by_message_id)
        seq = int(event.get("seq") or 0)
        if role == "user":
            latest_user_seq = max(latest_user_seq, seq)
        elif role == "assistant":
            latest_assistant_event_seq = max(latest_assistant_event_seq, seq)
            payload = event.get("payload") or {}
            has_text = bool(str(payload.get("content") or "").strip())
            if not has_text:
                has_text = bool(_extract_message_updated_text(event) or _extract_message_part(event)[1])
            if has_text:
                latest_assistant_text_seq = max(latest_assistant_text_seq, seq)
    return (
        latest_user_seq > latest_assistant_text_seq
        or latest_assistant_event_seq > latest_assistant_text_seq
    )


def _is_explicit_scene_boundary_event(event: Dict[str, Any]) -> bool:
    boundary_types = {"session_end", "session_ended", "session_idle", "session_complete", "session_completed"}
    if str(event.get("event_type") or "").lower() in boundary_types:
        return True
    raw_type = str((event.get("payload") or {}).get("raw_type") or "").lower()
    return raw_type in {"session.idle", "session.ended", "session.completed"}


class SceneEvidenceAssembler:
    """Small durable boundary between event processing and scene construction."""

    def __init__(self, session_id: str) -> None:
        self.session_id = str(session_id or "default")
        self._events = _load_pending_scene_events(self.session_id)

    @property
    def events(self) -> List[Dict[str, Any]]:
        return [dict(event) for event in self._events]

    def observe(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not _is_scene_evidence_event(event):
            return self.events
        self._events = _scene_raw_events(*self._events, event)
        _save_pending_scene_events(self.session_id, self._events)
        return self.events

    def commit(self, scene: Scene) -> None:
        committed_ids = {str(event_id) for event_id in scene.source_event_ids if str(event_id).strip()}
        committed_seqs = [int(event.get("seq") or 0) for event in self._events if str(event.get("event_id") or "") in committed_ids]
        if scene.missing_source_event_ids:
            event_index = load_event_index()
            committed_seqs.extend(
                int(event_index[f"{self.session_id}:{event_id}"])
                for event_id in scene.missing_source_event_ids
                if f"{self.session_id}:{event_id}" in event_index
            )
        if committed_seqs:
            mark_scene_events_finalized(self.session_id, committed_seqs)
        self._events = [
            event for event in self._events if str(event.get("event_id") or "") not in committed_ids
        ]
        _save_pending_scene_events(self.session_id, self._events)

    def reconcile_durable_scene(self) -> Optional[Scene]:
        """Recover the crash window after scene persistence but before cleanup."""

        if not self._events:
            return None
        pending_ids = {str(event.get("event_id") or "") for event in self._events}
        pending_ids.discard("")
        if not pending_ids:
            return None
        for scene in get_session_scenes(self.session_id):
            if pending_ids.issubset(set(scene.source_event_ids)):
                return scene
        return None


def _retry_failed_extractions(session_id: str) -> Dict[str, int]:
    """Repair derived memories from already-durable scenes.

    Retry entries point at the event that anchored extraction. Scene evidence is
    already committed, so replay reads the portable scene instead of depending
    on a ledger payload that may later be pruned.
    """

    entries = load_retry_queue(session_id=session_id)
    if not entries:
        return {"retried_memories": 0, "recovered_retries": 0, "fallback_memories": 0}

    scenes = get_session_scenes(session_id)
    scene_by_event_id: Dict[str, Scene] = {}
    for scene in scenes:
        for event_id in scene.source_event_ids:
            scene_by_event_id.setdefault(str(event_id), scene)

    existing_scene_ids = {
        str(memory.scene_id)
        for memory in get_recent_memories(limit=None, session_id=session_id)
        if memory.scene_id
    }
    retried_memories = 0
    recovered_retries = 0
    fallback_memories = 0
    for entry in entries:
        event_id = str(entry.get("event_id") or "")
        scene = scene_by_event_id.get(event_id)
        if scene is None:
            continue
        if scene.scene_id in existing_scene_ids:
            remove_retry_entries(session_id, {event_id})
            recovered_retries += 1
            continue
        try:
            outcome = run_memory_pipeline_outcome(
                session_id=session_id,
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
        retried_memories += len(records)
        if records and outcome.get("fallback_used"):
            fallback_memories += len(records)
        remove_retry_entries(session_id, {event_id})
        recovered_retries += 1
        existing_scene_ids.add(scene.scene_id)
    return {
        "retried_memories": retried_memories,
        "recovered_retries": recovered_retries,
        "fallback_memories": fallback_memories,
    }


def _message_event_id_for_content(
    events: List[Dict[str, Any]],
    *,
    role: str,
    content: str,
    message_id: Optional[str] = None,
    role_by_message_id: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Find the event carrying the canonical text for a scene message."""

    target = str(content or "").strip()
    candidates: List[Tuple[int, str]] = []
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type in {"user_message", "assistant_message"}:
            canonical_role = "user" if event_type == "user_message" else "assistant"
            if canonical_role != role:
                continue
            payload = event.get("payload") or {}
            candidate_text = str(payload.get("content") or "")
            if target and candidate_text.strip() == target:
                candidates.append((int(event.get("seq") or 0), str(event.get("event_id") or "")))
            continue

        payload = event.get("payload") or {}
        raw_type = str(payload.get("raw_type") or "")
        body = payload.get("body") or {}
        properties = body.get("properties") or {}
        info = properties.get("info") or {}
        part = properties.get("part") or {}
        candidate_message_id = info.get("id") or part.get("messageID") or part.get("id")
        event_role = str(info.get("role") or part.get("role") or (role_by_message_id or {}).get(candidate_message_id) or "")
        if event_role != role or (message_id and candidate_message_id != message_id):
            continue
        candidate_text = ""
        if raw_type == "message.updated":
            candidate_text = _extract_message_updated_text(event) or ""
        elif raw_type == "message.part.updated":
            candidate_text = str(part.get("text") or "")
        if target and candidate_text.strip() == target:
            candidates.append((int(event.get("seq") or 0), str(event.get("event_id") or "")))
    if candidates:
        return sorted(candidates)[-1][1] or None
    return None


def _source_event_ids_by_message_id(events: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map provider message IDs to the canonical ledger events that carry them."""

    result: Dict[str, Tuple[int, str]] = {}
    for event in events:
        payload = event.get("payload") or {}
        body = payload.get("body") or {}
        properties = body.get("properties") or {}
        info = properties.get("info") or {}
        part = properties.get("part") or {}
        message_id = (
            payload.get("message_id")
            or info.get("id")
            or part.get("messageID")
            or part.get("id")
        )
        event_id = str(event.get("event_id") or "").strip()
        if not message_id or not event_id:
            continue
        seq = int(event.get("seq") or 0)
        current = result.get(str(message_id))
        if current is None or seq >= current[0]:
            result[str(message_id)] = (seq, event_id)
    return {message_id: value[1] for message_id, value in result.items()}


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


def _trace_packet_tool_calls(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    if str(event.get("event_type") or "") != "trace_packet":
        return []
    event_id = str(event.get("event_id") or "").strip() or None
    summaries: List[Dict[str, Any]] = []
    for item in (event.get("payload") or {}).get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "tool").strip() or "tool"
        args = item.get("args") or {}
        result = item.get("result")
        file_paths = _extract_file_paths(args, result)
        target = f" on {', '.join(file_paths[:3])}" if file_paths else ""
        summaries.append(
            {
                "name": name,
                "call_id": item.get("call_id"),
                "status": _tool_status(result),
                "summary": f"{name}{target}",
                "file_paths": file_paths,
                "excerpt": _compact_text(result, limit=500) or None,
                "event_id": event_id,
            }
        )
    return summaries


def _build_scene_candidate(
    event: Dict[str, Any],
    turn: int,
    prompt: Dict[str, Any],
    *,
    assistant_message_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    scene_events: Optional[List[Dict[str, Any]]] = None,
    role_by_message_id: Optional[Dict[str, str]] = None,
    source_event_id_by_message_id: Optional[Dict[str, str]] = None,
) -> Scene:
    event_type = str(event.get("event_type") or "unknown")
    session_id = str(event.get("session_id") or "default")
    event_id = str(event.get("event_id") or "").strip() or None
    seq = int(event.get("seq") or 0)
    timestamp = str(event.get("ts") or datetime.now().isoformat())
    user_text = str(prompt.get("user_text") or "")
    assistant_text = str(prompt.get("assistant_text") or "")
    used_context_fallback = bool(prompt.get("used_context_fallback", False))

    raw_events = _scene_raw_events(*(scene_events or []), event)
    raw_event_ids = [str(raw_event.get("event_id") or "") for raw_event in raw_events if raw_event.get("event_id")]
    user_message_event_id = _message_event_id_for_content(
        raw_events,
        role="user",
        content=user_text,
        message_id=parent_message_id,
        role_by_message_id=role_by_message_id,
    )
    assistant_event_id = _message_event_id_for_content(
        raw_events,
        role="assistant",
        content=assistant_text,
        message_id=assistant_message_id,
        role_by_message_id=role_by_message_id,
    ) or event_id

    missing_source_event_ids: List[str] = []
    if event_type == "trace_packet":
        messages = [
            SceneMessage(role="system", content=user_text, message_id=None, event_id=event_id),
            SceneMessage(role="assistant", content=assistant_text, message_id=None, event_id=assistant_event_id),
        ]
        kind = "trace_packet"
    else:
        if not user_message_event_id:
            missing_user_event_id = str(
                (source_event_id_by_message_id or {}).get(str(parent_message_id or "")) or ""
            ).strip()
            if missing_user_event_id:
                missing_source_event_ids.append(missing_user_event_id)
        messages = [
            SceneMessage(role="user", content=user_text, message_id=parent_message_id, event_id=user_message_event_id),
            SceneMessage(role="assistant", content=assistant_text, message_id=assistant_message_id, event_id=assistant_event_id),
        ]
        kind = "message_exchange"

    compact_tool_calls = list(tool_calls or []) + _trace_packet_tool_calls(event)
    source_event_ids = list(raw_event_ids)
    for tool_call in compact_tool_calls:
        tool_event_id = str(tool_call.get("event_id") or "").strip()
        if tool_event_id and tool_event_id not in source_event_ids:
            source_event_ids.append(tool_event_id)

    source_event_ids = [event_id for event_id in source_event_ids if event_id]
    if not source_event_ids and event_id:
        source_event_ids = [event_id]
    complete_evidence = bool(raw_events) and not missing_source_event_ids and all(
        message.event_id and message.event_id in source_event_ids for message in messages
    )
    if complete_evidence:
        start_event_seq = int(raw_events[0].get("seq") or seq or 0) or None
        end_event_seq = int(raw_events[-1].get("seq") or seq or 0) or None
    else:
        start_event_seq = int(raw_events[0].get("seq") or seq or 0) if raw_events else seq or None
        end_event_seq = int(raw_events[-1].get("seq") or seq or 0) if raw_events else seq or None

    return Scene(
        scene_id=_make_scene_id(session_id, event_id, turn),
        session_id=session_id,
        turn=turn,
        kind=kind,
        scene_seq=seq or None,
        start_event_seq=start_event_seq,
        end_event_seq=end_event_seq,
        anchor_event_id=event_id,
        source_event_ids=source_event_ids,
        raw_events=raw_events,
        evidence_version=1 if raw_events else 0,
        evidence_status="complete" if complete_evidence else "partial",
        missing_source_event_ids=missing_source_event_ids,
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
    raw_events = _scene_raw_events(event)
    source_event_ids = [str(item.get("event_id") or "") for item in raw_events if item.get("event_id")]
    turn = seq or 0
    return Scene(
        scene_id=_make_scene_id(session_id, event_id, turn),
        session_id=session_id,
        turn=turn,
        kind="raw_event",
        scene_seq=seq or None,
        start_event_seq=int(raw_events[0].get("seq") or seq) if raw_events else seq or None,
        end_event_seq=int(raw_events[-1].get("seq") or seq) if raw_events else seq or None,
        anchor_event_id=event_id,
        source_event_ids=source_event_ids or ([event_id] if event_id else []),
        raw_events=raw_events,
        evidence_version=1 if raw_events else 0,
        evidence_status="complete" if raw_events and source_event_ids else "partial",
        missing_source_event_ids=[] if raw_events and source_event_ids else ([event_id] if event_id else []),
        messages=[],
        tool_calls=[],
        extraction_user_text="",
        extraction_assistant_text="",
        used_context_fallback=False,
        ts=timestamp,
    )


def _build_pending_raw_event_scene(session_id: str, events: List[Dict[str, Any]], turn: int) -> Optional[Scene]:
    """Persist an unmatched evidence buffer as one self-contained raw scene."""

    raw_events = _scene_raw_events(*events)
    if not raw_events:
        return None
    source_event_ids = [str(item.get("event_id") or "") for item in raw_events if item.get("event_id")]
    if not source_event_ids:
        return None
    timestamp = str(raw_events[-1].get("ts") or datetime.now().isoformat())
    return Scene(
        scene_id=_make_scene_id(session_id, source_event_ids[-1], turn),
        session_id=session_id,
        turn=turn,
        kind="raw_event",
        scene_seq=int(raw_events[-1].get("seq") or 0) or None,
        start_event_seq=int(raw_events[0].get("seq") or 0) or None,
        end_event_seq=int(raw_events[-1].get("seq") or 0) or None,
        anchor_event_id=source_event_ids[-1],
        source_event_ids=source_event_ids,
        raw_events=raw_events,
        evidence_version=1,
        evidence_status="complete",
        missing_source_event_ids=[],
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


def _process_session_events_impl(session_id: str, limit: int = 200) -> Dict[str, Any]:
    # Process only events that were not previously checkpointed for this session.
    evidence_assembler = SceneEvidenceAssembler(session_id)
    reconciled_scene = evidence_assembler.reconcile_durable_scene()
    if (
        reconciled_scene is not None
        and reconciled_scene.anchor_event_id
        and int(reconciled_scene.scene_seq or reconciled_scene.end_event_seq or 0) > 0
        and reconciled_scene.extraction_assistant_text
    ):
        append_retry_entry(
            {
                "session_id": session_id,
                "event_id": reconciled_scene.anchor_event_id,
                "seq": int(reconciled_scene.scene_seq or reconciled_scene.end_event_seq or 0),
                "reason": "recovered_after_scene_commit",
            }
        )
    if reconciled_scene is not None:
        evidence_assembler.commit(reconciled_scene)
    retry_counts = _retry_failed_extractions(session_id)
    events = load_unprocessed_events(session_id, limit=limit)
    if not events:
        if evidence_assembler.events and not _pending_scene_waits_for_assistant(evidence_assembler.events):
            # The processed checkpoint may already be ahead after a crash. Replay
            # the durable assembly state to rebuild the intended scene rather
            # than degrading it into an unrelated raw-event scene.
            events = evidence_assembler.events
        else:
            return {
                "processed_events": 0,
                "prompt_candidates": 0,
                "stored_memories": retry_counts["retried_memories"],
                "fallback_memories": retry_counts["fallback_memories"],
                "queued_retries": 0,
                "recovered_retries": retry_counts["recovered_retries"],
                "skipped_low_signal": 0,
                "skip_reasons": {},
            }

    stored_memories = retry_counts["retried_memories"]
    prompt_candidates = 0
    fallback_memories = retry_counts["fallback_memories"]
    queued_retries = 0
    skipped_low_signal = 0
    skip_reasons: Dict[str, int] = {}
    turn = get_next_trace_turn(session_id)

    role_by_message_id, parent_by_message_id, latest_text_by_message_id = load_message_context(session_id)
    source_event_id_by_message_id = _source_event_ids_by_message_id(load_events_for_session(session_id))
    recent_user_text = get_pending_user_message(session_id)
    if not recent_user_text:
        recent_user_text = _latest_pending_user_text(evidence_assembler.events, role_by_message_id)
    # A pending context record is valid only when it predates this batch.  In
    # addition to preventing stale user text from leaking into unrelated
    # sessions, this keeps replay/tests isolated from an existing on-disk
    # pending-state file while preserving cross-batch pairing.
    pending_seq = get_pending_user_message_seq(session_id)
    pending_context_is_stale = bool(
        pending_seq and events and pending_seq >= min(int(item.get("seq") or 0) for item in events)
    )
    if pending_context_is_stale:
        recent_user_text = ""
    pending_tool_calls: List[Dict[str, Any]] = [
        summary
        for pending_event in evidence_assembler.events
        for summary in [_summarize_tool_event(pending_event)]
        if summary is not None
    ]
    for message_id, role in role_by_message_id.items():
        if role != "user":
            continue
        text = (latest_text_by_message_id.get(message_id) or "").strip()
        if text and not pending_context_is_stale:
            recent_user_text = text

    for index, event in enumerate(events):
        event_id = str(event.get("event_id") or "")
        seq = int(event.get("seq", 0))
        is_evidence_event = _is_scene_evidence_event(event)
        scene_events = evidence_assembler.observe(event) if is_evidence_event else evidence_assembler.events
        if is_evidence_event:
            # Processed means the evidence reference is durable. The scene
            # checkpoint is advanced later, only after append_scene succeeds.
            update_session_checkpoint(session_id, seq)
        else:
            if _is_explicit_scene_boundary_event(event) and evidence_assembler.events:
                boundary_scene = _build_pending_raw_event_scene(session_id, evidence_assembler.events, turn)
                if boundary_scene is not None:
                    persisted = append_scene(boundary_scene)
                    if isinstance(persisted, dict):
                        boundary_scene = Scene(**persisted)
                    evidence_assembler.commit(boundary_scene)
            mark_scene_events_finalized(session_id, [seq])
            update_session_checkpoint(session_id, seq)

        tool_summary = _summarize_tool_event(event)
        if tool_summary:
            pending_tool_calls.append(tool_summary)
            continue

        payload = event.get("payload") or {}
        event_type = str(event.get("event_type") or "")
        if event_type == "user_message":
            user_text = str(payload.get("content") or "").strip()
            if user_text:
                recent_user_text = user_text
                _set_pending_user_context_preserving_scene(
                    session_id,
                    user_text,
                    seq=seq,
                    event_id=event_id or None,
                    scene_events=scene_events,
                )
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
                    _set_pending_user_context_preserving_scene(
                        session_id,
                        user_text_from_update,
                        seq=seq,
                        event_id=event_id or None,
                        scene_events=scene_events,
                    )

        message_id, text = _extract_message_part(event)
        if message_id and text and _is_latest_message_part_snapshot(events, index, message_id, text):
            latest_text_by_message_id[message_id] = text
            if role_by_message_id.get(message_id) == "user":
                recent_user_text = text
                _set_pending_user_context_preserving_scene(
                    session_id,
                    text,
                    seq=seq,
                    event_id=event_id or None,
                    scene_events=scene_events,
                )

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
                continue
            prompt_skip_reason = str(prompt.get("skip_reason") or "")
            scene = _build_scene_candidate(
                event,
                turn,
                prompt,
                assistant_message_id=message_id,
                parent_message_id=parent_id,
                tool_calls=pending_tool_calls,
                scene_events=scene_events,
                role_by_message_id=role_by_message_id,
                source_event_id_by_message_id=source_event_id_by_message_id,
            )
            persisted = append_scene(scene)
            if isinstance(persisted, dict):
                scene = Scene(**persisted)
            if event_id and seq > 0 and not prompt_skip_reason:
                append_retry_entry(
                    {
                        "session_id": session_id,
                        "event_id": event_id,
                        "seq": seq,
                        "reason": "derived_memory_pending",
                    }
                )
            evidence_assembler.commit(scene)
            pending_tool_calls = []
            if str(prompt.get("trace_mode") or "") == "pi_message_pair":
                clear_pending_user_message(session_id)
            if prompt_skip_reason:
                if event_id:
                    remove_retry_entries(session_id, {event_id})
                skipped_low_signal += 1
                skip_reasons[prompt_skip_reason] = skip_reasons.get(prompt_skip_reason, 0) + 1
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
                    persist_scene=False,
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
                continue

            records = outcome["records"]
            if event_id:
                remove_retry_entries(session_id, {event_id})
            if records:
                stored_memories += len(records)
                if outcome.get("fallback_used"):
                    fallback_memories += len(records)
                turn += 1
            else:
                skipped_low_signal += 1
                skip_reason = str(outcome.get("skip_reason") or "empty_after_filter")
                skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1

        else:
            pending_tool_calls = []

    # A batch may end with a user message, tool event, or otherwise incomplete
    # evidence sequence. Keep it durable and truthful as a raw scene instead
    # of leaving the committed checkpoint permanently behind.
    if not _pending_scene_waits_for_assistant(evidence_assembler.events, role_by_message_id):
        pending_scene = _build_pending_raw_event_scene(session_id, evidence_assembler.events, turn)
        if pending_scene is not None:
            persisted = append_scene(pending_scene)
            if isinstance(persisted, dict):
                pending_scene = Scene(**persisted)
            evidence_assembler.commit(pending_scene)

    return {
        "processed_events": len(events),
        "prompt_candidates": prompt_candidates,
        "stored_memories": stored_memories,
        "fallback_memories": fallback_memories,
        "queued_retries": queued_retries,
        "recovered_retries": retry_counts["recovered_retries"],
        "skipped_low_signal": skipped_low_signal,
        "skip_reasons": skip_reasons,
    }


def _ingest_trace_event_impl(event: TraceEvent, process_new: bool = True) -> Dict[str, Any]:
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
        payload.update(_process_session_events_impl(event.session_id))
    return payload


def _ingest_spool_session_impl(session_id: str, spool_dir: str = ".opencode/titan/traces") -> Dict[str, Any]:
    """Serialize a complete spool ingest across duplicate MCP processes."""

    spool_path = Path(spool_dir).expanduser()
    lock_path = spool_path / ".titan-ingest.lock"
    with interprocess_lock(lock_path):
        return _ingest_spool_session_unlocked(session_id=session_id, spool_dir=spool_dir)


def _ingest_spool_session_unlocked(session_id: str, spool_dir: str = ".opencode/titan/traces") -> Dict[str, Any]:
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
        "recovered_retries": 0,
        "skipped_low_signal": 0,
        "skip_reasons": {},
    }
    for processed_session_id in processed_sessions:
        process_counts = _process_session_events_impl(processed_session_id)
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


def _get_pipeline_debug_status_impl(session_id: Optional[str] = None) -> Dict[str, Any]:
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
    elif settings.get("plugin_spool_dir"):
        # Keep the historical settings key as a compatibility fallback.  An
        # explicit TITAN_SPOOL_DIR always wins, as documented by RuntimeContext.
        spool_dir_value = str(settings.get("plugin_spool_dir"))
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
    scene_checkpoint_seq = get_scene_checkpoint(session_id)
    pending_scene_events = _load_pending_scene_events(session_id)
    session_scenes = get_session_scenes(session_id)
    partial_scene_count = sum(1 for scene in session_scenes if scene.evidence_status == "partial")
    missing_event_count = sum(len(scene.missing_source_event_ids) for scene in session_scenes)
    evidence_bytes = sum(len(json.dumps(scene.raw_events, default=str)) for scene in session_scenes)
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
            "processed_checkpoint_seq": checkpoint_seq,
            "scene_checkpoint_seq": scene_checkpoint_seq,
            "checkpoint_gap": max(0, checkpoint_seq - scene_checkpoint_seq),
            "earliest_pending_scene_seq": min(
                (int(event.get("seq") or 0) for event in pending_scene_events),
                default=None,
            ),
            "pending_scene_event_count": len(pending_scene_events),
            "partial_scene_count": partial_scene_count,
            "missing_event_count": missing_event_count,
            "scene_evidence_bytes": evidence_bytes,
            "unprocessed_event_count": unprocessed_event_count,
            "lag_seconds": lag_seconds,
        }
    )
    return payload


# Compatibility forwarding interfaces.  Keep the historical imports stable
# while routing all trace use cases through the framework-neutral TraceIntake
# seam introduced for the architecture deepening program.
def process_session_events(session_id: str, limit: int = 200) -> Dict[str, Any]:
    from app.save_pipeline.trace_intake import get_trace_intake

    return get_trace_intake().process_session_events(session_id=session_id, limit=limit)


def ingest_trace_event(event: TraceEvent, process_new: bool = True) -> Dict[str, Any]:
    from app.save_pipeline.trace_intake import get_trace_intake

    return get_trace_intake().ingest_trace_event(event=event, process_new=process_new)


def ingest_spool_session(session_id: str, spool_dir: str = ".opencode/titan/traces") -> Dict[str, Any]:
    from app.save_pipeline.trace_intake import get_trace_intake

    return get_trace_intake().ingest_spool_session(session_id=session_id, spool_dir=spool_dir)


def get_pipeline_debug_status(session_id: Optional[str] = None) -> Dict[str, Any]:
    from app.save_pipeline.trace_intake import get_trace_intake

    return get_trace_intake().debug_status(session_id=session_id)


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


def _scene_reference_from_memory(memory: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scene_id = str(memory.get("scene_id") or "").strip()
    if not scene_id:
        return None

    status: Literal["complete", "partial"] = "partial"
    if str(memory.get("evidence_status") or "").strip().lower() == "complete":
        status = "complete"
    try:
        version = int(memory.get("evidence_version") or 0)
    except (TypeError, ValueError):
        version = 0

    missing = memory.get("missing_source_event_ids") or []
    if not isinstance(missing, list):
        missing = list(missing) if isinstance(missing, (tuple, set)) else []

    return SceneReference(
        scene_id=scene_id,
        evidence_status=status,
        evidence_version=version,
        missing_source_event_ids=[str(event_id) for event_id in missing if event_id],
    ).model_dump()


def scene_references_from_memories(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered_scene_ids: List[str] = []
    seen_scene_ids: set[str] = set()
    for memory in memories:
        scene_id = str(memory.get("scene_id") or "").strip()
        if scene_id and scene_id not in seen_scene_ids:
            seen_scene_ids.add(scene_id)
            ordered_scene_ids.append(scene_id)

    stored_refs: Dict[str, Dict[str, Any]] = {}
    try:
        for reference in get_scene_references(ordered_scene_ids):
            scene_id = str(reference.get("scene_id") or "").strip()
            if scene_id:
                stored_refs[scene_id] = SceneReference(
                    scene_id=scene_id,
                    evidence_status="complete"
                    if str(reference.get("evidence_status") or "partial") == "complete"
                    else "partial",
                    evidence_version=int(reference.get("evidence_version") or 0),
                    missing_source_event_ids=[
                        str(event_id)
                        for event_id in reference.get("missing_source_event_ids") or []
                        if str(event_id).strip()
                    ],
                ).model_dump()
    except Exception:
        # Retrieval must remain useful if an older or unavailable scene store
        # cannot answer metadata-only lookups. Memory-carried metadata is the
        # safe compatibility fallback and never expands scene evidence.
        stored_refs = {}

    scene_refs: List[Dict[str, Any]] = []
    for memory in memories:
        scene_ref = _scene_reference_from_memory(memory)
        if not scene_ref:
            continue
        scene_ref = stored_refs.get(scene_ref["scene_id"], scene_ref)
        if any(existing["scene_id"] == scene_ref["scene_id"] for existing in scene_refs):
            continue
        scene_refs.append(scene_ref)
    return scene_refs


_scene_references_from_memories = scene_references_from_memories


_PUBLIC_MEMORY_FIELDS = (
    "id",
    "text",
    "type",
    "stream",
    "session_id",
    "turn",
    "scene_id",
    "source_type",
    "source_reliability",
    "verification_status",
    "fallback_generated",
    "speaker_focus",
    "memory_kind",
    "ts",
    "source_event_ids",
    "source_agent",
)


def serialize_public_memory(memory: Any) -> Dict[str, Any]:
    """Return the stable public memory shape without internal retrieval state."""

    if isinstance(memory, dict):
        source = memory
    elif hasattr(memory, "model_dump"):
        source = memory.model_dump()
    else:
        source = {field: getattr(memory, field, None) for field in _PUBLIC_MEMORY_FIELDS}
    payload = {field: source.get(field) for field in _PUBLIC_MEMORY_FIELDS}
    payload["source_event_ids"] = list(source.get("source_event_ids") or [])
    return payload


def retrieve_memory_brief(
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 8,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_scenes: bool = False,
    sources: Optional[list[str] | tuple[str, ...] | str] = None,
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
            "scene_refs": [],
            "brief": "Memory disabled for this query (fresh context requested).",
            "scene_brief": "",
            "route": route,
        }

    selected_mode = mode or str(route.get("mode") or "both")
    selected_limit = limit if limit is not None else int(route.get("top_k") or 8)
    selected_intent = str(route.get("intent") or "balanced")
    try:
        from app.runtime.context import get_runtime_context
        active_agent = get_runtime_context().agent_name
    except Exception:
        active_agent = "default"
    if sources is not None or active_agent == "codex":
        from app.retrieval_pipeline.federated import FederatedRecall

        recall = FederatedRecall(active_agent=active_agent)
        federated_hits = recall.query_hits(
            safe_query,
            session_id=session_id,
            limit=selected_limit,
            mode=selected_mode,
            sources=sources,
            intent=selected_intent,
            date_from=date_from,
            date_to=date_to,
        )
        hits = federated_hits
    else:
        hits = retrieve_memories(
            safe_query,
            session_id=session_id,
            top_k=selected_limit,
            mode=selected_mode,
            intent=selected_intent,
            date_from=date_from,
            date_to=date_to,
        )
    memory_brief = build_memory_notes(
        hits, max_items=max_items, max_chars=max_chars,
        cluster_mode=settings.get("step2", {}).get("cluster_compression_enabled", False),
    )
    pattern_hits: List[Dict[str, Any]] = []
    pattern_brief = ""
    try:
        from app.patterns.brief import build_pattern_brief
        from app.patterns.retrieval import retrieve_accepted_patterns

        pattern_hits = retrieve_accepted_patterns(safe_query)
        pattern_brief = build_pattern_brief(pattern_hits, max_items=max_items, max_chars=max_chars)
    except Exception as exc:
        logging.getLogger(__name__).warning("Pattern retrieval failed: %s", exc)
    brief = "\n\n".join(part for part in [pattern_brief, memory_brief] if part)

    retrieved_memories = []
    for hit in hits:
        retrieved_memories.append(dict(hit.get("memory", {})))
    memories = [serialize_public_memory(memory) for memory in retrieved_memories]

    response: Dict[str, Any] = {
        "query": safe_query,
        "mode": selected_mode,
        "count": len(memories),
        "memories": memories,
        "scenes": [],
        "scene_refs": [],
        "brief": brief,
        "pattern_brief": pattern_brief,
        "patterns": [hit.get("pattern", {}) for hit in pattern_hits],
        "scene_brief": "",
        "route": route,
    }

    if sources is not None or active_agent == "codex":
        scene_refs = FederatedRecall(active_agent=active_agent).scene_references(retrieved_memories, sources=sources)
    else:
        scene_refs = scene_references_from_memories(retrieved_memories)
    response["scene_refs"] = scene_refs
    if include_scenes:
        response["scenes"] = scene_refs

    if route.get("summary_mode") == "timeline":
        response.update(build_timeline(memories, max_items=max_items, max_chars=max_chars))

    return response


def get_scene_context(scene_id: str, source_agent: Optional[str] = None) -> Dict[str, Any]:
    normalized_scene_id = str(scene_id or "").strip()
    if not normalized_scene_id:
        return {"error": "scene_id is required", "scene_id": normalized_scene_id}

    try:
        from app.runtime.context import get_runtime_context
        active_agent = get_runtime_context().agent_name
    except Exception:
        active_agent = "default"
    if source_agent is not None or active_agent == "codex":
        from app.retrieval_pipeline.federated import FederatedRecall

        return FederatedRecall(active_agent=active_agent).get_scene_context(
            normalized_scene_id, source_agent=source_agent
        )

    scene = get_scene(normalized_scene_id)
    if not scene:
        return {"error": "scene not found", "scene_id": normalized_scene_id}
    return {"scene": scene.model_dump()}
