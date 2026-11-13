"""Bounded lexical search over original conversation messages.

This is independent of memory retrieval: only message text is searched, never
scene metadata, extraction projections, raw tool output, or stored facts.
The caller selects the namespace and is responsible for the final rendered
response budget (including citation metadata).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.storage.scenes import SceneRepository

MAX_SCAN_SCENES = 200
MAX_CANDIDATES = 100
MAX_PASSAGE_CHARS = 500
_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_SHORT_COMPOUND = re.compile(r"(?<!\w)[^\W_](?:\s*[&+]\s*[^\W_])+(?!\w)", re.UNICODE)
_STOP = frozenset("a an and are as at be been but by can could did do does for from had has have how i in is it its me my of on or our should that the their them there these they this to was we were what when where which who why will with would you your about tell find remember recall search conversation said say".split())


def _terms(text: str) -> set[str]:
    terms = {match.group().casefold() for match in _TOKEN.finditer(text)
             if len(match.group()) > 1 and match.group().casefold() not in _STOP}
    terms.update(re.sub(r"\s+", "", match.group()).casefold()
                 for match in _SHORT_COMPOUND.finditer(text))
    return terms


def _passage(text: str, query_terms: set[str]) -> tuple[str, float] | None:
    # Rank fixed-size overlapping windows so a relevant sentence late in a
    # long message can be recovered without returning the whole message.
    best: tuple[float, int, str] | None = None
    step = MAX_PASSAGE_CHARS // 2
    anchors = {term for term in query_terms if "&" in term or "+" in term}
    for start in range(0, len(text), step):
        window = text[start:start + MAX_PASSAGE_CHARS]
        matches = _terms(window) & query_terms
        if not anchors <= matches:
            continue
        required = 1 if len(query_terms) == 1 else 2
        if len(matches) < required:
            continue
        score = len(matches) / len(query_terms)
        candidate = (score, -start, window.strip())
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None
    return best[2], best[0]


def canonical_scene_messages(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Project saved conversational messages without exposing tool events.

    Existing projections take precedence; raw events fill missing event IDs.
    Raw event payloads were sanitized when saved, and are not reconstituted
    from extraction text or tool output.
    """
    raw_events = [event for event in scene.get("raw_events") or [] if isinstance(event, dict)]
    excluded = {str(item.get("event_id") or "") for item in scene.get("tool_calls") or []
                if isinstance(item, dict)}
    excluded.update(str(event.get("event_id") or "") for event in raw_events
                    if any(kind in str(event.get("event_type") or "").lower()
                           for kind in ("tool", "system")))
    raw_roles = {str(event.get("event_id") or ""): event.get("event_type")
                 for event in raw_events
                 if event.get("event_type") in {"user_message", "assistant_message"}}
    result = []
    seen = set()
    for message in scene.get("messages") or []:
        if not isinstance(message, dict):
            continue
        event_id = str(message.get("event_id") or "").strip()
        if (message.get("role") not in {"user", "assistant"} or not event_id
                or event_id in excluded or event_id in seen):
            continue
        projected = dict(message)
        if event_id in raw_roles:
            projected["role"] = "user" if raw_roles[event_id] == "user_message" else "assistant"
        result.append(projected)
        seen.add(event_id)
    for event in raw_events:
        event_type = event.get("event_type")
        if event_type not in {"user_message", "assistant_message"}:
            continue
        event_id = str(event.get("event_id") or "").strip()
        payload = event.get("payload")
        if not event_id or event_id in excluded or event_id in seen or not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        result.append({"role": "user" if event_type == "user_message" else "assistant",
                       "content": content, "message_id": payload.get("message_id"), "event_id": event_id})
        seen.add(event_id)
    return result


def _iter_scenes(repository: SceneRepository, session_id: str | None, batch_size: int):
    iterator = getattr(repository, "iter_source_scenes", None)
    if callable(iterator):
        yield from iterator(session_id=session_id, batch_size=batch_size)
        return
    # Compatibility for custom repositories with only the older API. Fetch
    # increasing prefixes until exhausted, emitting each scene exactly once.
    size = batch_size
    emitted = 0
    while True:
        scenes = repository.get_recent_scenes(limit=size, session_id=session_id)
        yield from scenes[emitted:]
        if len(scenes) < size:
            return
        emitted = len(scenes)
        size *= 2


def _capture_timestamp(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Python 3.10 requires an explicit offset for the ISO UTC designator.
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (TypeError, ValueError):
        return None
    if end_of_day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _diverse_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank the best passage from each scene before additional passages."""
    candidates.sort(key=lambda item: (-item["score"], item["scene_id"], item["source_event_ids"][0]))
    first, remaining = [], []
    seen = set()
    for item in candidates:
        key = (item["session_id"], item["scene_id"])
        if key in seen:
            remaining.append(item)
        else:
            seen.add(key)
            first.append(item)
    return (first + remaining)[:MAX_CANDIDATES]


def search_source_passages(
    query: str,
    *,
    repository: SceneRepository,
    source_agent: str,
    limit: int = 5,
    scan_limit: int = MAX_SCAN_SCENES,
    session_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Search every saved scene in the selected store and return bounded passages.

    ``scan_limit`` controls the retrieval batch size, not search eligibility.
    SQLite uses a streaming read; no indexes or store migrations are required.
    Optional capture-time bounds are applied before candidate ranking.
    """
    terms = _terms(str(query or "")[:4_000])
    source = str(source_agent or "").strip()
    if not terms or not source or limit <= 0 or scan_limit <= 0:
        return []
    start = _capture_timestamp(date_from)
    end = _capture_timestamp(date_to, end_of_day=True)
    if (date_from and start is None) or (date_to and end is None):
        return []
    candidates: list[dict[str, Any]] = []
    for scene in _iter_scenes(repository, session_id, min(int(scan_limit), 1000)):
        scene_id = str(scene.get("scene_id") or "")
        session = str(scene.get("session_id") or "")
        if not scene_id or not session or (session_id and session != session_id):
            continue
        if start or end:
            timestamp = _capture_timestamp(scene.get("ts"))
            if timestamp is None or (start and timestamp < start) or (end and timestamp > end):
                continue
        for message in canonical_scene_messages(scene):
            event_id = str(message.get("event_id") or "").strip()
            content = str(message.get("content") or "")
            passage = _passage(content, terms)
            if passage:
                text, score = passage
                candidates.append({"result_kind": "source_passage", "text": text,
                    "scene_id": scene_id, "session_id": session, "source_agent": source,
                    "source_event_ids": [event_id], "ts": str(scene.get("ts") or ""),
                    "role": message["role"], "score": score})
                if len(candidates) > MAX_CANDIDATES:
                    candidates = _diverse_candidates(candidates)
    return _diverse_candidates(candidates)[:min(int(limit), MAX_CANDIDATES)]
