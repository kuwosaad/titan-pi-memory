from typing import Any, Dict, List, Optional

from app.save_pipeline.extraction.extractor import classify_memory, is_hidden_metadata_memory

from .config import load_settings

_MEMORY_KIND_PRIORITY = {
    "relationship": 0,
    "user_preference": 1,
    "decision": 2,
    "commitment": 3,
    "task": 4,
    "outcome": 5,
    "user_fact": 6,
    "workflow": 7,
    "issue": 8,
}


def build_memory_notes(
    hits: List[Dict[str, Any]],
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> str:
    if not hits:
        return ""

    settings = load_settings()
    max_items = max_items or settings.get("notes_max_items", 6)
    max_chars = max_chars or settings.get("notes_max_chars", 700)

    filtered = [hit for hit in hits if not is_hidden_metadata_memory(hit.get("memory", {}))]
    if not filtered:
        return ""

    return _build_flat_notes(filtered, max_items, max_chars)


def _build_flat_notes(
    hits: List[Dict[str, Any]],
    max_items: int,
    max_chars: int,
) -> str:
    ordered_hits = sorted(
        hits,
        key=lambda hit: (
            0 if str((hit.get("memory") or {}).get("stream") or "rough") == "learnings" else 1,
            _MEMORY_KIND_PRIORITY.get(
                str((hit.get("memory") or {}).get("memory_kind") or classify_memory(
                    str((hit.get("memory") or {}).get("text") or ""), (hit.get("memory") or {}).get("type")
                )[1]),
                99,
            ),
            -float(hit.get("score") or 0.0),
        ),
    )

    lines = ["MEMORY BRIEF:"]
    total_chars = len(lines[0])

    for idx, hit in enumerate(ordered_hits[:max_items], start=1):
        mem = hit.get("memory", {})
        stream = str(mem.get("stream") or "rough")
        mem_type = str(mem.get("type") or "fact")
        text = str(mem.get("text") or "").strip()
        if not text:
            continue

        prefix = f"{idx}. [{stream}/{mem_type}]"
        line = f"{prefix} {text}"
        projected = total_chars + len(line) + 1
        if projected > max_chars:
            break

        lines.append(line)
        total_chars = projected

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def build_timeline(
    memories: List[Dict[str, Any]],
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> Dict[str, Any]:
    if not memories:
        return {"timeline": [], "timeline_summary": ""}

    settings = load_settings()
    max_items = max_items or settings.get("notes_max_items", 10)
    max_chars = max_chars or settings.get("notes_max_chars", 900)

    timeline = []
    for mem in sorted(memories, key=lambda item: str(item.get("ts") or ""))[:max_items]:
        text = str(mem.get("text") or "").strip()
        if not text:
            continue
        timeline.append(
            {
                "timestamp": mem.get("ts"),
                "stream": mem.get("stream", "rough"),
                "type": mem.get("type"),
                "text": text,
                "evidence_ids": [mem.get("id")] if mem.get("id") else [],
                "reliability": mem.get("source_reliability", 0.5),
            }
        )

    lines = []
    total_chars = 0
    for entry in timeline:
        line = f"{entry.get('timestamp') or 'unknown'} -> [{entry.get('stream')}] {entry.get('text')}"
        projected = total_chars + len(line) + 1
        if projected > max_chars:
            break
        lines.append(line)
        total_chars = projected

    return {"timeline": timeline, "timeline_summary": "\n".join(lines)}
