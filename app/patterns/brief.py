from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.retrieval_pipeline.config import load_settings


def build_pattern_brief(
    hits: List[Dict[str, Any]],
    *,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> str:
    """Render accepted pattern hits as agent guidance before memory notes."""

    if not hits:
        return ""
    settings = load_settings()
    max_items = max_items or int(settings.get("patterns", {}).get("retrieve_limit", 3) or 3)
    max_chars = max_chars or int(settings.get("notes_max_chars", 700))

    lines = ["PATTERN BRIEF:"]
    total_chars = len(lines[0])
    for idx, hit in enumerate(hits[:max_items], start=1):
        pattern = hit.get("pattern") or {}
        title = _clean(pattern.get("title")) or "Untitled pattern"
        kind = _clean(pattern.get("kind")) or "pattern"
        scope = _clean(pattern.get("scope")) or "scope"
        confidence = pattern.get("confidence")
        confidence_text = f" confidence {float(confidence):.2f}" if isinstance(confidence, (int, float)) else ""
        matched = hit.get("matched_terms") or []
        matched_text = f" · matched: {', '.join(str(term) for term in matched[:5])}" if matched else ""
        header = f"{idx}. [{kind}/{scope}{confidence_text}] {title}{matched_text}"
        if not _append(lines, header, max_chars, total_chars):
            break
        total_chars += len(header) + 1

        applies_when = _clean(pattern.get("applies_when"))
        if applies_when:
            line = f"   When: {applies_when}"
            if _append(lines, line, max_chars, total_chars):
                total_chars += len(line) + 1

        behavior = _clean(pattern.get("recommended_behavior"))
        if behavior:
            line = f"   Do: {behavior}"
            if _append(lines, line, max_chars, total_chars):
                total_chars += len(line) + 1

        scene_count = len(hit.get("scene_ids") or [])
        evidence_count = int(hit.get("evidence_count") or len(hit.get("evidence") or []))
        evidence_line = f"   Evidence: {evidence_count} memories"
        if scene_count:
            evidence_line += f" across {scene_count} scenes"
        if _append(lines, evidence_line, max_chars, total_chars):
            total_chars += len(evidence_line) + 1

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _append(lines: List[str], line: str, max_chars: int, total_chars: int) -> bool:
    projected = total_chars + len(line) + 1
    if projected > max_chars:
        return False
    lines.append(line)
    return True
