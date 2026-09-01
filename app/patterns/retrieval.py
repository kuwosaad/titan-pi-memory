from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.retrieval_pipeline.config import load_settings
from .models import Pattern, PatternApplication, now_iso
from .storage import resolve_pattern_db_path as _resolve_pattern_db_path
from .store import PatternStore, PatternValidationError

_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./-]{1,}")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it",
    "of", "on", "or", "should", "that", "the", "to", "use", "we", "what", "when", "with", "you", "your",
}


def resolve_pattern_db_path() -> Path:
    return _resolve_pattern_db_path()


def retrieve_accepted_patterns(
    query: str,
    *,
    scope: Optional[str] = None,
    limit: Optional[int] = None,
    min_confidence: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Retrieve accepted learned patterns that should guide a query.

    V1 is intentionally simple and fast: accepted patterns only, keyword/trigger
    matching only, trigger overlap sorted ahead of weaker text overlap.
    """

    safe_query = str(query or "").strip()
    if not safe_query:
        return []

    settings = load_settings()
    config = settings.get("patterns") if isinstance(settings.get("patterns"), dict) else {}
    if not bool(config.get("enabled", True)):
        return []

    safe_limit = int(limit if limit is not None else config.get("retrieve_limit", 3) or 3)
    safe_limit = max(1, min(safe_limit, 10))
    threshold = float(min_confidence if min_confidence is not None else config.get("min_confidence_to_retrieve", 0.60))
    path = Path(db_path) if db_path is not None else resolve_pattern_db_path()
    store = PatternStore(path)

    try:
        candidates = store.list_patterns(status="accepted", limit=200)
    except PatternValidationError:
        return []

    query_tokens = _tokens(safe_query)
    query_lower = safe_query.lower()
    hits: list[Dict[str, Any]] = []
    for pattern in candidates:
        if pattern.confidence < threshold:
            continue
        if not _scope_matches(pattern.scope, scope):
            continue
        score, trigger_overlap, matched_terms, reason = _score_pattern(pattern, query_tokens, query_lower)
        if score <= 0.0:
            continue
        evidence = store.list_evidence(pattern.id)
        scene_ids = sorted({item.scene_id for item in evidence if item.scene_id})
        hits.append(
            {
                "pattern": _pattern_to_dict(pattern),
                "evidence": [_evidence_to_dict(item) for item in evidence],
                "evidence_count": len(evidence),
                "scene_ids": scene_ids,
                "score": round(float(score), 4),
                "trigger_overlap": trigger_overlap,
                "matched_terms": matched_terms,
                "match_reason": reason,
            }
        )

    hits.sort(
        key=lambda item: (
            -int(item.get("trigger_overlap") or 0),
            -float(item.get("score") or 0.0),
            -float((item.get("pattern") or {}).get("confidence") or 0.0),
            str((item.get("pattern") or {}).get("title") or ""),
        )
    )
    selected = hits[:safe_limit]
    for hit in selected:
        try:
            retrieved_at = now_iso()
            store.record_application(
                PatternApplication(
                    pattern_id=hit["pattern"]["id"],
                    query=safe_query,
                    retrieved_at=retrieved_at,
                    shown_at=retrieved_at,
                )
            )
        except Exception:
            # Retrieval guidance should never fail the main memory response.
            pass
    return selected


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    raw = _TOKEN_RE.findall(str(text or "").lower())
    for token in raw:
        cleaned = token.strip("`'\".,:;!?()[]{}<>")
        if len(cleaned) < 2 or cleaned in _STOPWORDS or cleaned.isdigit():
            continue
        tokens.add(cleaned)
    for i in range(len(raw) - 1):
        phrase = f"{raw[i]} {raw[i + 1]}"
        if raw[i] not in _STOPWORDS and raw[i + 1] not in _STOPWORDS:
            tokens.add(phrase)
    return tokens


def _scope_matches(pattern_scope: str, requested_scope: Optional[str]) -> bool:
    if not requested_scope:
        return True
    return pattern_scope in {requested_scope, "global"}


def _score_pattern(pattern: Pattern, query_tokens: set[str], query_lower: str) -> tuple[float, int, List[str], str]:
    trigger_terms = [str(term).strip().lower() for term in pattern.trigger_terms if str(term).strip()]
    trigger_tokens = set(trigger_terms)
    for term in trigger_terms:
        trigger_tokens.update(_tokens(term))

    pattern_text = " ".join(
        [
            pattern.title,
            pattern.summary,
            pattern.recommended_behavior,
            pattern.applies_when,
            pattern.does_not_apply_when,
            " ".join(pattern.trigger_terms),
        ]
    )
    content_tokens = _tokens(pattern_text)
    trigger_matches = sorted(term for term in trigger_tokens if term in query_tokens or term in query_lower)
    content_matches = sorted((content_tokens & query_tokens) - set(trigger_matches))
    phrase_bonus = sum(1.0 for term in trigger_terms if len(term) > 3 and term in query_lower)
    trigger_overlap = len(trigger_matches)
    content_overlap = len(content_matches)
    if trigger_overlap == 0 and content_overlap == 0 and phrase_bonus == 0.0:
        return 0.0, 0, [], "no_match"
    reason = "trigger" if trigger_overlap or phrase_bonus else "keyword"
    score = (3.0 * trigger_overlap) + (0.75 * content_overlap) + phrase_bonus + float(pattern.confidence)
    return score, trigger_overlap, (trigger_matches + content_matches)[:12], reason


def _pattern_to_dict(pattern: Pattern) -> Dict[str, Any]:
    if hasattr(pattern, "model_dump"):
        return pattern.model_dump()
    return pattern.dict()


def _evidence_to_dict(evidence: Any) -> Dict[str, Any]:
    if hasattr(evidence, "model_dump"):
        return evidence.model_dump()
    return evidence.dict()
