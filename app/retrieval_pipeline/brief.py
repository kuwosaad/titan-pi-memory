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
    cluster_mode: bool = False,
) -> str:
    if not hits:
        return ""

    settings = load_settings()
    max_items = max_items or settings.get("notes_max_items", 6)
    max_chars = max_chars or settings.get("notes_max_chars", 700)

    filtered = [hit for hit in hits if not is_hidden_metadata_memory(hit.get("memory", {}))]
    if not filtered:
        return ""

    if cluster_mode and any(h.get("cluster_id") is not None for h in filtered):
        return _build_clustered_notes(filtered, max_items, max_chars)

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

        tension_note = hit.get("tension_note")
        if tension_note:
            tension_line = f"   \u26a0 TENSION: {tension_note}"
            tension_projected = total_chars + len(tension_line) + 1
            if tension_projected <= max_chars:
                lines.append(tension_line)
                total_chars = tension_projected

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _build_clustered_notes(
    hits: List[Dict[str, Any]],
    max_items: int,
    max_chars: int,
) -> str:
    clusters: Dict[int, List[Dict[str, Any]]] = {}
    singletons: List[Dict[str, Any]] = []
    seen_clusters: Dict[int, Dict[str, Any]] = {}

    for hit in hits:
        cid = hit.get("cluster_id")
        if cid is not None and hit.get("cluster_size", 1) > 1:
            clusters.setdefault(cid, []).append(hit)
            if cid not in seen_clusters:
                seen_clusters[cid] = hit
        else:
            singletons.append(hit)

    lines = ["MEMORY BRIEF:"]
    total_chars = len(lines[0])
    item_idx = 0

    cluster_list = sorted(
        clusters.items(),
        key=lambda pair: (
            -pair[1][0].get("cluster_size", 0),
            -max(float(h.get("score", 0.0)) for h in pair[1]),
        ),
    )

    for cid, cluster_hits in cluster_list:
        if item_idx >= max_items:
            break
        meta = seen_clusters.get(cid, {})
        size = meta.get("cluster_size", len(cluster_hits))
        rep_text = str(meta.get("cluster_representative_text", ""))[:120]
        has_tension = meta.get("cluster_has_tension", False)
        oldest = meta.get("cluster_oldest_ts", "")
        newest = meta.get("cluster_newest_ts", "")

        tension_flag = " \u00b7 \u26a0 TENSION" if has_tension else ""
        temporal = f" ({oldest} \u2192 {newest})" if oldest and newest else ""

        header = f"INSIGHT {cid + 1} [{size} related{tension_flag}]: {rep_text}{temporal}"
        header_projected = total_chars + len(header) + 1
        if header_projected > max_chars:
            break
        lines.append(header)
        total_chars = header_projected

        sorted_cluster_hits = sorted(
            cluster_hits,
            key=lambda h: -float(h.get("score", 0.0)),
        )[:max_items - item_idx]

        for hit in sorted_cluster_hits:
            mem = hit.get("memory", {})
            stream = str(mem.get("stream") or "rough")
            mem_type = str(mem.get("type") or "fact")
            text = str(mem.get("text") or "").strip()
            if not text:
                continue

            more_recent = " \u2190 MORE RECENT" if hit.get("step2_contradiction_delta", 0.0) > 0 else ""
            contradicted = " \u2190 CONTRADICTED" if hit.get("step2_contradiction_delta", 0.0) < 0 else ""

            evidence_line = f"  \u2192 [{stream}/{mem_type}] {text}{more_recent}{contradicted}"
            evidence_projected = total_chars + len(evidence_line) + 1
            if evidence_projected > max_chars:
                break
            lines.append(evidence_line)
            total_chars = evidence_projected

        item_idx += 1

    singletons_sorted = sorted(
        singletons,
        key=lambda h: (
            0 if str((h.get("memory") or {}).get("stream") or "rough") == "learnings" else 1,
            -float(h.get("score") or 0.0),
        ),
    )[:max_items - item_idx]

    flat_idx = item_idx + 1
    for hit in singletons_sorted:
        mem = hit.get("memory", {})
        stream = str(mem.get("stream") or "rough")
        mem_type = str(mem.get("type") or "fact")
        text = str(mem.get("text") or "").strip()
        if not text:
            continue

        line = f"{flat_idx}. [{stream}/{mem_type}] {text}"
        projected = total_chars + len(line) + 1
        if projected > max_chars:
            break
        lines.append(line)
        total_chars = projected
        flat_idx += 1

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def build_scene_notes(
    scenes: List[Dict[str, Any]],
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> str:
    if not scenes:
        return ""

    settings = load_settings()
    max_items = max_items or settings.get("notes_max_items", 4)
    max_chars = max_chars or settings.get("notes_max_chars", 700)

    lines = ["SCENE BRIEF:"]
    total_chars = len(lines[0])

    for idx, scene in enumerate(scenes[:max_items], start=1):
        messages = scene.get("messages") or []
        parts = []
        for message in messages[:3]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "system")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            parts.append(f"{role}: {content}")
        if not parts:
            continue

        line = f"{idx}. [{scene.get('kind') or 'scene'}] {' | '.join(parts)}"
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
