"""Bounded old facts supplied only as correction comparison context."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

COMPARISON_MEMORIES = ContextVar("titan_comparison_memories", default=())
_CORRECTION = re.compile(r"\b(?:correction|correcting|supersedes|replaces?|instead|moved|no longer)\b", re.I)


def correction_candidates(user_text, repository, source):
    if not _CORRECTION.search(user_text):
        return []
    from app.storage.memory_relations import filter_current
    words = set(re.findall(r"[a-z0-9_]+", user_text.lower()))
    candidates = filter_current(repository, repository.get_recent_memories(limit=100), source=source)
    ranked = []
    for record in candidates:
        if not all(record.get(key) for key in ("subject", "scope", "attribute")):
            continue
        anchors = set(re.findall(r"[a-z0-9_]+", " ".join(str(record[k]) for k in ("subject", "scope", "attribute")).lower()))
        score = len(words & anchors)
        if score:
            ranked.append((score, record))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [{key: (str(row[key])[:700] if row.get(key) is not None else None) for key in ("id", "text", "subject", "scope", "attribute")}
            for _, row in ranked[:3]]


@contextmanager
def comparison_scope(records):
    token = COMPARISON_MEMORIES.set(tuple(records))
    try:
        yield
    finally:
        COMPARISON_MEMORIES.reset(token)
