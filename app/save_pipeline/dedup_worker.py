from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

from app.retrieval_pipeline.config import load_settings
from app.save_pipeline.extraction.adapters import dedup_model_enabled, get_dedup_adapter
from app.save_pipeline.extraction.dedup_prompts import build_dedup_input, build_dedup_messages
from app.save_pipeline.dedup_buffer import (
    confirm_dedup_buffer_flush,
    dedup_buffer_size,
    flush_dedup_buffer,
    recover_dedup_buffer,
)
from app.storage.memories import append_memories

LOGGER = logging.getLogger(__name__)


def _run_dedup_pass() -> Dict[str, Any]:
    if not _dedup_active():
        return {"status": "dedup_disabled"}

    recovered = recover_dedup_buffer()
    if recovered:
        LOGGER.info("dedup_worker: processing %d recovered entries", len(recovered))
        _fallback_store(recovered)

    size = dedup_buffer_size()
    settings = load_settings()
    min_size = int(settings.get("dedup", {}).get("min_buffer_size", 5))
    if size < min_size:
        return {"status": "below_min_size", "buffer_size": size, "min_size": min_size}
    if not dedup_model_enabled():
        return {"status": "dedup_model_disabled"}

    entries = flush_dedup_buffer()
    if not entries:
        return {"status": "empty_buffer"}

    slim = build_dedup_input(entries)
    payload = json.dumps(slim, indent=2)

    try:
        adapter = get_dedup_adapter()
        messages = build_dedup_messages(payload)
        response = adapter.chat(messages, temperature=0.1)
        result = _parse_dedup_response(response, entries)
    except Exception as exc:
        LOGGER.error("dedup_worker: LLM dedup failed: %s", exc)
        _fallback_store(entries)
        confirm_dedup_buffer_flush(entries)
        return {"status": "dedup_error_fallback", "stored": len(entries), "error": str(exc)}

    merged = result.get("merged", [])
    if merged:
        stored = _store_merged(merged, entries)
        confirm_dedup_buffer_flush(entries)
        LOGGER.info("dedup_worker: merged %d → %d, discarded %d",
                     len(entries), len(stored), len(result.get("discarded_ids", [])))
        return {
            "status": "dedup_complete",
            "buffer_size_before": len(entries),
            "merged_count": len(merged),
            "stored_count": len(stored),
            "discarded_count": len(result.get("discarded_ids", [])),
        }
    else:
        _fallback_store(entries)
        confirm_dedup_buffer_flush(entries)
        return {"status": "dedup_empty_response_fallback", "stored": len(entries)}


def _parse_dedup_response(response: str, original_entries: list[dict]) -> dict:
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) > 1:
            text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        try:
            inner = text.split("```json")[1].split("```")[0].strip()
            return json.loads(inner)
        except (json.JSONDecodeError, IndexError):
            pass
    LOGGER.warning("dedup_worker: could not parse LLM response as JSON, falling back")
    return {}


def _store_merged(merged: list[dict], original_entries: list[dict]) -> list[dict]:
    originals_by_id = {str(entry.get("id") or ""): entry for entry in original_entries if entry.get("id")}
    records: list[dict] = []
    for item in merged:
        merged_from_ids = [str(value) for value in item.get("merged_from_ids", []) if value]
        if not merged_from_ids and item.get("id"):
            merged_from_ids = [str(item.get("id"))]

        base = next((originals_by_id[mid] for mid in merged_from_ids if mid in originals_by_id), None)
        if base is None:
            LOGGER.warning("dedup_worker: skipping merged memory with no matching source ids: %s", merged_from_ids)
            continue

        source_event_ids: list[str] = []
        for mid in merged_from_ids:
            original = originals_by_id.get(mid)
            if not original:
                continue
            for event_id in original.get("source_event_ids") or []:
                if event_id not in source_event_ids:
                    source_event_ids.append(event_id)

        record = {
            "id": base.get("id"),
            "session_id": base.get("session_id", ""),
            "turn": base.get("turn", 0),
            "scene_id": base.get("scene_id"),
            "text": item.get("text", ""),
            "type": item.get("type", base.get("type")),
            "stream": item.get("stream", "rough"),
            "ts": base.get("ts", ""),
            "source_type": base.get("source_type", "unknown"),
            "source_reliability": float(base.get("source_reliability", 0.5)),
            "verification_status": base.get("verification_status", "unverified"),
            "fallback_generated": bool(base.get("fallback_generated", False)),
            "source_event_ids": source_event_ids or list(base.get("source_event_ids") or []),
            "provenance": base.get("provenance", {"user": "", "assistant": ""}),
            "speaker_focus": item.get("speaker_focus", base.get("speaker_focus")),
            "memory_kind": item.get("memory_kind", base.get("memory_kind")),
            "embedding": base.get("embedding"),
            "h": base.get("h", 0.0),
            "tau": base.get("tau", 0.5),
        }
        records.append(record)
    if records:
        append_memories(records)
    return records


def _fallback_store(entries: list[dict]) -> None:
    clean = []
    for entry in entries:
        entry.pop("_buffer_ts", None)
        clean.append(entry)
    if clean:
        append_memories(clean)
        LOGGER.info("dedup_worker: fallback store %d records", len(clean))


def _dedup_active() -> bool:
    try:
        settings = load_settings()
        return bool(settings.get("dedup", {}).get("enabled", False))
    except Exception:
        return False


def _dedup_loop(stop_event: threading.Event, interval_seconds: float) -> None:
    while not stop_event.is_set():
        try:
            _run_dedup_pass()
        except Exception:
            LOGGER.exception("dedup_worker: unhandled error in dedup pass")
        stop_event.wait(interval_seconds)


def start_dedup_worker(stop_event: threading.Event, interval_seconds: float = 300.0) -> threading.Thread:
    settings = load_settings()
    window = float(settings.get("dedup", {}).get("buffer_window_seconds", interval_seconds))
    worker = threading.Thread(
        target=_dedup_loop,
        args=(stop_event, window),
        daemon=True,
        name="titan-dedup-worker",
    )
    worker.start()
    LOGGER.info("dedup_worker: started (flush every %.0fs)", window)
    return worker
