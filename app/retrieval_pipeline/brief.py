from typing import Any, Dict, List, Optional

from app.save_pipeline.extraction.extractor import is_hidden_metadata_memory

from .config import load_settings

def _source(hit: Dict[str, Any]) -> str:
    return str(hit.get("source_agent") or (hit.get("memory") or {}).get("source_agent") or "")


def _memory_line(hit: Dict[str, Any], prefix: str) -> str:
    mem = hit.get("memory") or {}
    stream = str(mem.get("stream") or "rough")
    mem_type = str(mem.get("type") or "fact")
    text = str(mem.get("text") or "").strip()
    source = _source(hit)
    label = f"{source} · " if source else ""
    if mem.get("result_kind") == "source_passage":
        return f"{prefix}[{label}{mem.get('role') or 'conversation'} source passage · recorded {str(mem.get('ts') or 'unknown')[:10]} · {mem.get('scene_id') or 'unknown scene'}] {text}"
    status = f" · {mem['correction_status']}" if mem.get("correction_status") else ""
    return f"{prefix}[{label}{stream}/{mem_type}{status}] {text}"


def build_memory_notes(
    hits: List[Dict[str, Any]],
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    cluster_mode: bool = False,
    included_hits: Optional[List[Dict[str, Any]]] = None,
) -> str:
    if not hits:
        return ""

    settings = load_settings()
    max_items = settings.get("notes_max_items", 6) if max_items is None else max_items
    max_chars = settings.get("notes_max_chars", 700) if max_chars is None else max_chars
    if max_items <= 0 or max_chars <= 0:
        return ""

    # Preserve the retriever’s selected order, including diversity or facet coverage.
    filtered = []
    for hit in hits:
        memory = hit.get("memory") or {}
        text = str(memory.get("text") or "").strip()
        if not text or is_hidden_metadata_memory(memory):
            continue
        filtered.append(hit)

    # Direct brief callers share the same conservative evidence grouping as
    # retrieval, while distinct named sources keep their labels here.
    from .retriever import _group_display_hits
    filtered = _group_display_hits(filtered, source_isolation=True)

    if cluster_mode and any(h.get("cluster_id") is not None for h in filtered):
        return _build_clustered_notes(filtered, max_items, max_chars, included_hits)
    return _build_flat_notes(filtered, max_items, max_chars, included_hits)


def _build_flat_notes(hits: List[Dict[str, Any]], max_items: int, max_chars: int, included_hits: Optional[List[Dict[str, Any]]]) -> str:
    lines = ["MEMORY BRIEF:"]
    total_chars = len(lines[0])
    count = 0
    for hit in hits:
        if count >= max_items:
            break
        line = _memory_line(hit, f"{count + 1}. ")
        projected = total_chars + 1 + len(line)
        if projected > max_chars:
            continue
        lines.append(line)
        total_chars = projected
        count += 1
        if included_hits is not None:
            included_hits.append(hit)
        if hit.get("tension_note"):
            tension_line = f"   ⚠ TENSION: {hit['tension_note']}"
            if total_chars + 1 + len(tension_line) <= max_chars:
                lines.append(tension_line)
                total_chars += 1 + len(tension_line)
    return "\n".join(lines) if count else ""


def _build_clustered_notes(hits: List[Dict[str, Any]], max_items: int, max_chars: int, included_hits: Optional[List[Dict[str, Any]]]) -> str:
    lines = ["MEMORY BRIEF:"]
    total_chars = len(lines[0])
    count = 0
    previous_cluster = None
    for hit in hits:
        if count >= max_items:
            break
        cid = hit.get("cluster_id")
        clustered = cid is not None and hit.get("cluster_size", 1) > 1
        if not clustered:
            line = _memory_line(hit, f"{count + 1}. ")
            if total_chars + 1 + len(line) <= max_chars:
                lines.append(line)
                total_chars += 1 + len(line)
                count += 1
                if included_hits is not None:
                    included_hits.append(hit)
                previous_cluster = None
            continue

        key = (_source(hit), cid)
        size = hit.get("cluster_size", 1)
        tension = " · ⚠ TENSION" if hit.get("cluster_has_tension") else ""
        oldest, newest = hit.get("cluster_oldest_ts"), hit.get("cluster_newest_ts")
        temporal = f" ({oldest} → {newest})" if oldest and newest else ""
        source = f"{key[0]} · " if key[0] else ""
        header = f"{source}INSIGHT {cid + 1} [{size} related{tension}]{temporal}"
        delta = hit.get("step2_contradiction_delta", 0.0)
        marker = " ← LATER VIEW" if delta > 0 else " ← EARLIER VIEW" if delta < 0 else ""
        mem = hit.get("memory") or {}
        line = _memory_line(hit, "  → ") + marker
        needs_header = key != previous_cluster
        needed = 1 + len(line) + (1 + len(header) if needs_header else 0)
        if total_chars + needed > max_chars:
            continue
        if needs_header:
            lines.append(header)
        lines.append(line)
        total_chars += needed
        count += 1
        if included_hits is not None:
            included_hits.append(hit)
        previous_cluster = key
    return "\n".join(lines) if count else ""


def build_timeline(
    memories: List[Dict[str, Any]],
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> Dict[str, Any]:
    if not memories:
        return {"timeline": [], "timeline_summary": ""}

    settings = load_settings()
    max_items = settings.get("notes_max_items", 10) if max_items is None else max_items
    max_chars = settings.get("notes_max_chars", 900) if max_chars is None else max_chars

    timeline = []
    lines = []
    total_chars = 0
    for mem in sorted(memories, key=lambda item: str(item.get("ts") or "")):
        if len(timeline) >= max_items:
            break
        text = str(mem.get("text") or "").strip()
        if not text or is_hidden_metadata_memory(mem):
            continue
        line = _memory_line({"memory": mem}, f"{mem.get('ts') or 'unknown'} -> ")
        projected = total_chars + (1 if lines else 0) + len(line)
        if projected > max_chars:
            continue
        entry = {
            "timestamp": mem.get("ts"),
            "stream": mem.get("stream") if mem.get("result_kind") == "source_passage" else (mem.get("stream") or "rough"),
            "type": mem.get("type"),
            "text": text,
            "evidence_ids": (list(mem.get("source_event_ids") or []) if mem.get("result_kind") == "source_passage"
                             else [mem.get("id")] if mem.get("id") else []),
            "reliability": mem.get("source_reliability", 0.5),
        }
        for field in ("source_agent", "result_kind", "correction_status", "role", "scene_id", "source_event_ids", "evidence_refs"):
            if mem.get(field) is not None:
                entry[field] = mem[field]
        timeline.append(entry)
        lines.append(line)
        total_chars = projected

    return {"timeline": timeline, "timeline_summary": "\n".join(lines)}
