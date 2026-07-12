from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

from app.embedding.embedder import embed
from app.save_pipeline.extraction.extractor import is_hidden_metadata_memory
from app.graph.similarity import cosine_similarity
from app.storage.memories import query_memory_candidates, unpack_embedding
from app.storage.repository import CandidateFilters


# titan-pi-memory currently exposes one filtered candidate lane. Keep the
# retrieval-selection contract without forcing a storage/FTS migration here.
def query_memory_candidates_with_text(_fts_query: str, filters: CandidateFilters) -> List[Dict[str, Any]]:
    return query_memory_candidates(filters)


LOGGER = logging.getLogger(__name__)

DUPLICATE_NEGATION_TERMS = {
    "avoid",
    "disable",
    "disallow",
    "dont",
    "never",
    "no",
    "not",
    "reject",
    "remove",
    "without",
}

DUPLICATE_OPPOSING_TERM_PAIRS = (
    ("accept", "reject"),
    ("add", "drop"),
    ("allow", "disallow"),
    ("enable", "disable"),
    ("keep", "remove"),
    ("prefer", "dislike"),
    ("use", "avoid"),
)

PROFILE_ACTOR_TERMS = ("kuwo", "saad", "mohammad")
PROFILE_QUERY_TERMS = (
    "behavior",
    "collaboration",
    "explain",
    "frustrat",
    "pattern",
    "personality",
    "prefer",
    "preference",
    "psycholog",
    "working style",
)
PROFILE_MEMORY_KINDS = {"relationship", "user_fact", "user_preference", "workflow"}
QUERY_ECHO_MARKERS = {"memory", "memories", "query", "queries", "result", "results", "retrieval", "search"}
QUESTION_WORDS = {"how", "what", "when", "where", "which", "who", "why"}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "that",
    "the",
    "to",
    "use",
    "we",
    "what",
    "when",
    "with",
}


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def apply_recency(memories: List[Dict[str, Any]], recency_days: Optional[int]) -> List[Dict[str, Any]]:
    if not recency_days:
        return memories
    cutoff = datetime.now(timezone.utc) - timedelta(days=recency_days)
    filtered = []
    for mem in memories:
        ts = parse_timestamp(mem.get("ts"))
        if ts is None or ts >= cutoff:
            filtered.append(mem)
    return filtered


def apply_session_bias(memories: List[Dict[str, Any]], session_id: Optional[str], session_bias: bool) -> List[Dict[str, Any]]:
    if not session_id or not session_bias:
        return memories
    session_memories = [mem for mem in memories if mem.get("session_id") == session_id]
    return session_memories if session_memories else memories


def apply_types(memories: List[Dict[str, Any]], memory_types: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not memory_types:
        return memories
    allowed = {t.lower() for t in memory_types}
    return [mem for mem in memories if str(mem.get("type", "")).lower() in allowed]


def apply_stream_mode(memories: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    if mode == "both":
        return memories
    if mode not in {"rough", "learnings"}:
        return memories
    return [mem for mem in memories if str(mem.get("stream", "rough")) == mode]


def apply_reliability_filter(memories: List[Dict[str, Any]], min_reliability: float) -> List[Dict[str, Any]]:
    return [mem for mem in memories if mem.get("source_reliability", 0.0) >= min_reliability]


def apply_hidden_metadata_filter(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [mem for mem in memories if not is_hidden_metadata_memory(mem)]


def _memory_matches_candidate_filters(memory: Dict[str, Any], filters: Optional[CandidateFilters]) -> bool:
    if not filters:
        return True
    if is_hidden_metadata_memory(memory):
        return False
    if filters.recency_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=filters.recency_days)
        if (parse_timestamp(memory.get("ts")) or cutoff) < cutoff:
            return False
    if filters.date_from:
        date_from_dt = parse_timestamp(filters.date_from)
        mem_ts = parse_timestamp(memory.get("ts"))
        if date_from_dt and (mem_ts is None or mem_ts < date_from_dt):
            return False
    if filters.date_to:
        date_to_dt = parse_timestamp(filters.date_to)
        mem_ts = parse_timestamp(memory.get("ts"))
        if date_to_dt and (mem_ts is None or mem_ts > date_to_dt):
            return False
    if filters.session_id and filters.session_bias and memory.get("session_id") != filters.session_id:
        return False
    if filters.memory_types:
        allowed = {item.lower() for item in filters.memory_types}
        if str(memory.get("type", "")).lower() not in allowed:
            return False
    if filters.mode in {"rough", "learnings"} and str(memory.get("stream", "rough")) != filters.mode:
        return False
    return float(memory.get("source_reliability", 0.0)) >= filters.min_reliability


def _canonical_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _content_tokens(text: str) -> set[str]:
    return _expand_tokens(text)


def _expand_tokens(text: str) -> set[str]:
    base = _tokenize(text)
    tokens = {t for t in base if t not in STOPWORDS and len(t) > 2}
    for i in range(len(base) - 1):
        a, b = base[i], base[i + 1]
        if len(a) <= 3 and a not in STOPWORDS and any(c.isdigit() for c in a) and any(c.isalpha() for c in b):
            tokens.add(a + b)
        if len(b) <= 3 and b not in STOPWORDS and any(c.isdigit() for c in b) and any(c.isalpha() for c in a):
            tokens.add(a + b)
    return tokens


def _build_fts_query(text: str) -> str:
    if not text or not text.strip():
        return ""
    tokens = _expand_tokens(text)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in sorted(tokens))


def extract_date_brackets(text: str) -> Dict[str, Optional[str]]:
    if not text or not text.strip():
        return {"date_from": None, "date_to": None}
    lower = text.lower().strip()
    now = datetime.now(timezone.utc)

    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|through|–)\s*(\d{4}-\d{2}-\d{2})", lower)
    if m:
        return {"date_from": m.group(1), "date_to": m.group(2)}

    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lower)
    if m:
        d = m.group(1)
        return {"date_from": d, "date_to": d}

    if "last week" in lower:
        today = now.date()
        last_monday = today - timedelta(days=today.weekday() + 7)
        last_sunday = last_monday + timedelta(days=6)
        return {"date_from": last_monday.isoformat(), "date_to": last_sunday.isoformat()}

    if "yesterday" in lower:
        d = (now - timedelta(days=1)).date()
        return {"date_from": d.isoformat(), "date_to": d.isoformat()}

    if "today" in lower:
        d = now.date()
        return {"date_from": d.isoformat(), "date_to": d.isoformat()}

    months = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    month_map = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
        "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }

    m = re.search(r"\b(" + months + r")\s+(\d{1,2})\s*(?:to|-|–)\s*(?:(" + months + r")\s+)?(\d{1,2})\b", lower)
    if m:
        mn1 = month_map.get(m.group(1))
        d1 = int(m.group(2))
        mn2 = month_map.get(m.group(3)) if m.group(3) else mn1
        d2 = int(m.group(4))
        year = now.year
        if mn1 and mn2:
            try:
                return {"date_from": datetime(year, mn1, d1).date().isoformat(),
                        "date_to": datetime(year, mn2, d2).date().isoformat()}
            except ValueError:
                pass

    m = re.search(r"\bin\s+(" + months + r")\b(?:\s+(\d{4}))?", lower)
    if m:
        mn = month_map.get(m.group(1))
        year = int(m.group(2)) if m.group(2) else now.year
        if mn:
            try:
                from calendar import monthrange
                _, last_day = monthrange(year, mn)
                return {"date_from": datetime(year, mn, 1).date().isoformat(),
                        "date_to": datetime(year, mn, last_day).date().isoformat()}
            except ValueError:
                pass

    return {"date_from": None, "date_to": None}


def _dedupe_prefer_latest(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_hash: Dict[str, Dict[str, Any]] = {}
    for mem in memories:
        text = str(mem.get("text") or "").strip()
        if not text:
            continue
        key = hashlib.sha1(_canonical_text(text).encode("utf-8")).hexdigest()
        current = latest_by_hash.get(key)
        if current is None:
            latest_by_hash[key] = mem
            continue

        current_ts = parse_timestamp(current.get("ts"))
        candidate_ts = parse_timestamp(mem.get("ts"))
        if candidate_ts and (not current_ts or candidate_ts >= current_ts):
            latest_by_hash[key] = mem

    deduped = list(latest_by_hash.values())
    deduped.sort(key=lambda item: (parse_timestamp(item.get("ts")) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return deduped


def _duplicate_hit_quality(hit: Dict[str, Any]) -> tuple[float, int, float, datetime]:
    memory = hit.get("memory") or {}
    verification_status = str(memory.get("verification_status") or "").lower()
    return (
        float(hit.get("base_score") or hit.get("score") or 0.0),
        1 if verification_status == "verified" else 0,
        float(memory.get("source_reliability") or 0.0),
        parse_timestamp(memory.get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
    )


def _texts_have_opposition(left_text: str, right_text: str) -> bool:
    left_raw_tokens = set(_tokenize(left_text))
    right_raw_tokens = set(_tokenize(right_text))
    left_has_negation = bool(left_raw_tokens & DUPLICATE_NEGATION_TERMS)
    right_has_negation = bool(right_raw_tokens & DUPLICATE_NEGATION_TERMS)
    if left_has_negation != right_has_negation:
        return True
    return any(
        (positive in left_raw_tokens and negative in right_raw_tokens)
        or (negative in left_raw_tokens and positive in right_raw_tokens)
        for positive, negative in DUPLICATE_OPPOSING_TERM_PAIRS
    )


def _hits_are_near_duplicates(
    left: Dict[str, Any],
    right: Dict[str, Any],
    embedding_by_id: Dict[str, np.ndarray],
    *,
    token_jaccard_threshold: float,
    embedding_similarity_threshold: float,
    embedding_min_token_containment: float,
) -> bool:
    left_memory = left.get("memory") or {}
    right_memory = right.get("memory") or {}
    left_text = str(left_memory.get("text") or "").strip()
    right_text = str(right_memory.get("text") or "").strip()
    if not left_text or not right_text:
        return False
    if _canonical_text(left_text) == _canonical_text(right_text):
        return True

    if _texts_have_opposition(left_text, right_text):
        return False

    left_tokens = _content_tokens(left_text)
    right_tokens = _content_tokens(right_text)
    if not left_tokens or not right_tokens:
        return False

    intersection_size = len(left_tokens & right_tokens)
    union_size = len(left_tokens | right_tokens)
    jaccard = intersection_size / max(union_size, 1)
    if jaccard >= token_jaccard_threshold:
        return True

    containment = intersection_size / max(min(len(left_tokens), len(right_tokens)), 1)
    if containment < embedding_min_token_containment:
        return False

    left_vector = embedding_by_id.get(str(left_memory.get("id") or ""))
    right_vector = embedding_by_id.get(str(right_memory.get("id") or ""))
    if left_vector is None or right_vector is None:
        return False
    return float(cosine_similarity(left_vector, right_vector)) >= embedding_similarity_threshold


def _collapse_near_duplicate_hits(
    hits: List[Dict[str, Any]],
    embedding_by_id: Dict[str, np.ndarray],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Collapse visible query duplicates without deleting stored memories or scene lineage."""
    if not hits or not bool(config.get("enabled", False)):
        return hits

    token_jaccard_threshold = float(config.get("token_jaccard_threshold", 0.82))
    embedding_similarity_threshold = float(config.get("embedding_similarity_threshold", 0.93))
    embedding_min_token_containment = float(config.get("embedding_min_token_containment", 0.75))

    groups: List[List[Dict[str, Any]]] = []
    for hit in hits:
        matching_group: Optional[List[Dict[str, Any]]] = None
        for group in groups:
            representative = max(group, key=_duplicate_hit_quality)
            if _hits_are_near_duplicates(
                hit,
                representative,
                embedding_by_id,
                token_jaccard_threshold=token_jaccard_threshold,
                embedding_similarity_threshold=embedding_similarity_threshold,
                embedding_min_token_containment=embedding_min_token_containment,
            ):
                matching_group = group
                break
        if matching_group is None:
            groups.append([hit])
        else:
            matching_group.append(hit)

    collapsed: List[Dict[str, Any]] = []
    for group in groups:
        representative = max(group, key=_duplicate_hit_quality)
        if len(group) == 1:
            collapsed.append(representative)
            continue

        memory_ids: List[str] = []
        scene_ids: List[str] = []
        for member in group:
            memory = member.get("memory") or {}
            memory_id = str(memory.get("id") or "").strip()
            scene_id = str(memory.get("scene_id") or "").strip()
            if memory_id and memory_id not in memory_ids:
                memory_ids.append(memory_id)
            if scene_id and scene_id not in scene_ids:
                scene_ids.append(scene_id)

        collapsed.append(
            {
                **representative,
                "duplicate_memory_ids": memory_ids,
                "duplicate_scene_ids": scene_ids,
                "duplicate_count": len(group),
            }
        )

    collapsed.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            parse_timestamp((item.get("memory") or {}).get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return collapsed


def _expand_profile_aspect(facet: str, profile_mode: Optional[str]) -> str:
    """Add stable, general semantic cues to a personal-query facet only."""
    if not profile_mode:
        return facet
    lowered = facet.lower()
    cues: List[str] = []
    if profile_mode == "preference":
        if "explain" in lowered:
            cues.extend(["clarity", "concise", "direct", "simple wording", "abstraction", "root cause"])
        if "collaboration" in lowered or "frustrat" in lowered:
            cues.extend(["delegation", "reliability", "speed", "slow", "unreliable"])
    elif profile_mode == "psychology" and any(term in lowered for term in ("behavior", "pattern", "personality", "psycholog")):
        cues.extend(["recurring behavior", "continuity", "proof", "evidence"])
    return facet if not cues else f"{facet}. {' '.join(cues)}"


def _query_aspects(query: str, config: Dict[str, Any]) -> List[str]:
    """Return the full query plus a small number of unambiguous question facets."""
    normalized = " ".join(str(query or "").split())
    if not normalized:
        return []
    if not bool(config.get("query_aspects_enabled", False)):
        return [normalized]

    max_aspects = max(1, int(config.get("max_query_aspects", 3) or 3))
    min_tokens = max(1, int(config.get("min_aspect_tokens", 2) or 2))
    profile_mode = _profile_query_mode(normalized) if bool(config.get("profile_aspect_expansion_enabled", False)) else None
    aspects = [normalized]
    clause_pattern = re.compile(
        r"(?:[,;]\s*|\s+)and\s+(?=(?:what|how|why|which|when|where|who)\b)",
        flags=re.IGNORECASE,
    )
    clauses = clause_pattern.split(normalized)
    if len(clauses) <= 1:
        if profile_mode:
            return [normalized, _expand_profile_aspect(normalized, profile_mode)][:max_aspects]
        return aspects

    for clause in clauses:
        facet = clause.strip(" ,;?")
        if len(_content_tokens(facet)) < min_tokens:
            continue
        if _canonical_text(facet) == _canonical_text(normalized):
            continue
        facet = _expand_profile_aspect(facet, profile_mode)
        if any(_canonical_text(facet) == _canonical_text(existing) for existing in aspects):
            continue
        aspects.append(facet)
        if len(aspects) >= max_aspects:
            break
    return aspects


def _merge_candidate_lanes(
    lexical_candidates: List[Dict[str, Any]],
    semantic_candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Stably merge candidate lanes without dropping candidate provenance."""
    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in [*lexical_candidates, *semantic_candidates]:
        memory_id = str(candidate.get("id") or "").strip()
        if memory_id and memory_id in seen_ids:
            continue
        if memory_id:
            seen_ids.add(memory_id)
        merged.append(candidate)
    return merged


def _lexical_terms(text: str) -> set[str]:
    terms = set(_content_tokens(text))
    for token in list(terms):
        if len(token) > 4 and token.endswith("ies"):
            terms.add(token[:-3] + "y")
        elif len(token) > 3 and token.endswith("s"):
            terms.add(token[:-1])
        if len(token) > 5 and token.endswith("ing"):
            terms.add(token[:-3])
    return terms


def _lexical_coverage(text: str, aspects: List[str]) -> float:
    memory_tokens = _lexical_terms(text)
    if not memory_tokens:
        return 0.0
    coverage = 0.0
    for aspect in aspects:
        aspect_tokens = _lexical_terms(aspect)
        if not aspect_tokens:
            continue
        coverage = max(coverage, len(memory_tokens & aspect_tokens) / len(aspect_tokens))
        # A compact alphanumeric slug such as `t3code` is an exact anchor for
        # its spaced form `T3 Code`; do not require the generic trailing word.
        slug_matches = [
            token for token in aspect_tokens
            if any(char.isdigit() for char in token) and token in memory_tokens
        ]
        if slug_matches:
            coverage = 1.0
    return coverage


def _is_query_echo_memory(text: str, query: str) -> bool:
    memory_terms = _lexical_terms(text)
    query_terms = _lexical_terms(query)
    if not memory_terms or not query_terms or not (memory_terms & QUERY_ECHO_MARKERS):
        return False
    raw_query_tokens = _tokenize(query)
    normalized_text = _canonical_text(text)
    if len(raw_query_tokens) >= 4:
        for start in range(len(raw_query_tokens) - 3):
            quoted_phrase = " ".join(raw_query_tokens[start:start + 4])
            if quoted_phrase in normalized_text:
                return True
    overlap = len(memory_terms & query_terms) / len(query_terms)
    return overlap >= (1.0 if len(query_terms) <= 3 else 0.60)


def _is_multi_anchor_entity_query(query: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9]+", str(query or ""))
    anchors = [
        token for token in tokens
        if token.lower() not in QUESTION_WORDS
        and (any(char.isdigit() for char in token) or (token[:1].isupper() and len(token) > 2))
    ]
    return len(anchors) >= 2


def _profile_query_mode(query: str) -> Optional[str]:
    lowered = str(query or "").lower()
    if not any(actor in lowered for actor in PROFILE_ACTOR_TERMS):
        return None
    if any(term in lowered for term in ("behavior", "pattern", "personality", "psycholog")):
        return "psychology"
    if any(term in lowered for term in ("collaboration", "explain", "frustrat", "prefer", "preference", "working style")):
        return "preference"
    return None


def _is_profile_query(query: str) -> bool:
    return _profile_query_mode(query) is not None


def _profile_memory_affinity(memory: Dict[str, Any], profile_mode: Optional[str]) -> float:
    speaker_focus = str(memory.get("speaker_focus") or "").lower()
    memory_kind = str(memory.get("memory_kind") or "").lower()
    if profile_mode == "psychology":
        text = str(memory.get("text") or "").lower()
        if memory_kind in {"user_fact", "relationship"} and (
            speaker_focus == "kuwo" or any(actor in text for actor in PROFILE_ACTOR_TERMS)
        ):
            return 1.0
        if memory_kind in {"user_fact", "relationship"}:
            return 0.55
        if speaker_focus == "kuwo" and memory_kind == "user_preference":
            return 0.30
        if memory_kind in PROFILE_MEMORY_KINDS:
            return 0.20
        return 0.0
    if speaker_focus == "kuwo" and memory_kind in PROFILE_MEMORY_KINDS:
        return 1.0
    if memory_kind in PROFILE_MEMORY_KINDS:
        return 0.45
    # A user-focused task or issue is not itself a durable personal fact.
    # Keep it eligible through direct relevance, but do not give it a profile boost.
    return 0.0


def _direct_evidence(
    memory_vector: np.ndarray,
    query_aspect_vectors: List[np.ndarray],
    text: str,
    aspects: List[str],
) -> Dict[str, Any]:
    aspect_scores = [float(cosine_similarity(vector, memory_vector)) for vector in query_aspect_vectors]
    if not aspect_scores:
        return {
            "query_similarity": 0.0,
            "aspect_scores": [],
            "direct_similarity": 0.0,
            "best_aspect_index": 0,
            "lexical_coverage": 0.0,
        }
    best_index = int(np.argmax(np.asarray(aspect_scores)))
    return {
        "query_similarity": aspect_scores[0],
        "aspect_scores": aspect_scores,
        "direct_similarity": aspect_scores[best_index],
        "best_aspect_index": best_index,
        "lexical_coverage": _lexical_coverage(text, aspects),
    }


def _minimum_direct_similarity(hit: Dict[str, Any], config: Dict[str, Any]) -> float:
    min_direct_similarity = float(config.get("min_direct_similarity", 1.0))
    if bool(hit.get("profile_query", False)) and float(hit.get("profile_affinity", 0.0)) > 0.0:
        return float(config.get("profile_min_direct_similarity", min_direct_similarity))
    return min_direct_similarity


def _hit_has_sufficient_direct_evidence(hit: Dict[str, Any], config: Dict[str, Any]) -> bool:
    if not bool(config.get("enabled", False)):
        return True
    if bool(hit.get("query_echo_memory", False)):
        return False
    direct_similarity = float(hit.get("direct_similarity", hit.get("base_score", hit.get("score", 0.0))) or 0.0)
    lexical_coverage = float(hit.get("lexical_coverage", 0.0) or 0.0)
    if bool(hit.get("entity_query", False)) and not bool(hit.get("profile_query", False)):
        entity_min_lexical_coverage = float(config.get("entity_min_lexical_coverage", 0.0))
        entity_direct_similarity_override = float(config.get("entity_direct_similarity_override", 1.0))
        if (
            lexical_coverage < entity_min_lexical_coverage
            and direct_similarity < entity_direct_similarity_override
        ):
            return False
    min_direct_similarity = _minimum_direct_similarity(hit, config)
    if direct_similarity >= min_direct_similarity:
        return True
    strong_lexical_coverage = float(config.get("strong_lexical_coverage", 1.0))
    lexical_override_min_similarity = float(config.get("lexical_override_min_similarity", 1.0))
    max_lexical_override_terms = max(1, int(config.get("max_lexical_override_terms", 3) or 3))
    return (
        bool(hit.get("lexical_override_allowed", True))
        and lexical_coverage >= strong_lexical_coverage
        and direct_similarity >= lexical_override_min_similarity
        and min(len(_content_tokens(aspect)) for aspect in hit.get("query_aspects", [""])) <= max_lexical_override_terms
    )


def _query_has_sufficient_evidence(hits: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
    return any(_hit_has_sufficient_direct_evidence(hit, config) for hit in hits)


def _selection_rank_score(hit: Dict[str, Any], config: Dict[str, Any]) -> float:
    direct_similarity = float(hit.get("direct_similarity", hit.get("base_score", hit.get("score", 0.0))) or 0.0)
    lexical_coverage = float(hit.get("lexical_coverage", 0.0) or 0.0)
    profile_boost = 0.0
    if bool(config.get("user_profile_metadata_tiebreak_enabled", False)):
        profile_boost = float(config.get("profile_score_boost", 0.0)) * float(hit.get("profile_affinity", 0.0))
    lnn_bonus = max(
        0.0,
        float(hit.get("final_score", hit.get("score", 0.0)) or 0.0)
        - float(hit.get("base_score", 0.0) or 0.0),
    )
    pattern_anchor_boost = 0.0
    if bool(hit.get("profile_pattern_anchor", False)):
        pattern_anchor_boost = float(config.get("profile_pattern_anchor_boost", 0.0))
    return (
        direct_similarity
        + float(config.get("lexical_coverage_weight", 0.0)) * lexical_coverage
        + profile_boost
        + pattern_anchor_boost
        + float(config.get("lnn_selection_bonus_weight", 0.0)) * lnn_bonus
    )


def _source_event_ids(hit: Dict[str, Any]) -> set[str]:
    memory = hit.get("memory") or {}
    raw_ids = memory.get("source_event_ids") or []
    if not isinstance(raw_ids, list):
        return set()
    return {str(event_id).strip() for event_id in raw_ids if str(event_id).strip()}


def _is_display_redundant(
    candidate: Dict[str, Any],
    selected: List[Dict[str, Any]],
    embedding_by_id: Dict[str, np.ndarray],
    semantic_redundancy_threshold: float,
) -> bool:
    candidate_memory = candidate.get("memory") or {}
    candidate_id = str(candidate_memory.get("id") or "")
    candidate_vector = embedding_by_id.get(candidate_id)
    if candidate_vector is None:
        return False
    candidate_text = str(candidate_memory.get("text") or "")
    for existing in selected:
        existing_memory = existing.get("memory") or {}
        existing_id = str(existing_memory.get("id") or "")
        existing_vector = embedding_by_id.get(existing_id)
        if existing_vector is None:
            continue
        if _texts_have_opposition(candidate_text, str(existing_memory.get("text") or "")):
            continue
        if float(cosine_similarity(candidate_vector, existing_vector)) >= semantic_redundancy_threshold:
            return True
    return False


def _select_diverse_hits(
    hits: List[Dict[str, Any]],
    embedding_by_id: Dict[str, np.ndarray],
    top_k: int,
    aspect_count: int,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Allocate visible slots to distinct, directly relevant memory evidence."""
    admitted = [hit for hit in hits if _hit_has_sufficient_direct_evidence(hit, config)]
    if not admitted:
        return []

    profile_score_boost = float(config.get("profile_score_boost", 0.0))
    # Step 2.1 remains a positive tie-breaker, but immutable direct evidence
    # retains control of final order so associative activation cannot dominate.
    admitted.sort(key=lambda hit: _selection_rank_score(hit, config), reverse=True)

    max_per_scene = max(1, int(config.get("max_per_scene", 1) or 1))
    max_per_source_event = max(1, int(config.get("max_per_source_event", 1) or 1))
    semantic_redundancy_threshold = float(config.get("semantic_redundancy_threshold", 0.98))
    selected: List[Dict[str, Any]] = []
    scene_counts: Dict[str, int] = {}
    source_event_counts: Dict[str, int] = {}

    def can_select(candidate: Dict[str, Any]) -> bool:
        memory = candidate.get("memory") or {}
        candidate_id = str(memory.get("id") or "").strip()
        if candidate_id and any(
            candidate_id == str((existing.get("memory") or {}).get("id") or "").strip()
            for existing in selected
        ):
            return False
        scene_id = str(memory.get("scene_id") or "").strip()
        if scene_id and scene_counts.get(scene_id, 0) >= max_per_scene:
            return False
        for event_id in _source_event_ids(candidate):
            if source_event_counts.get(event_id, 0) >= max_per_source_event:
                return False
        candidate_semantic_threshold = semantic_redundancy_threshold
        if bool(candidate.get("profile_query", False)) and float(candidate.get("profile_affinity", 0.0)) > 0.0:
            candidate_semantic_threshold = float(
                config.get("profile_semantic_redundancy_threshold", candidate_semantic_threshold)
            )
        return not _is_display_redundant(
            candidate,
            selected,
            embedding_by_id,
            candidate_semantic_threshold,
        )

    def select(candidate: Dict[str, Any]) -> None:
        selected.append(candidate)
        memory = candidate.get("memory") or {}
        scene_id = str(memory.get("scene_id") or "").strip()
        if scene_id:
            scene_counts[scene_id] = scene_counts.get(scene_id, 0) + 1
        for event_id in _source_event_ids(candidate):
            source_event_counts[event_id] = source_event_counts.get(event_id, 0) + 1

    # Cover independently asked facets before the global rank order can consume
    # every slot with a single aspect of a multi-part question.
    for aspect_index in range(1, max(1, aspect_count)):
        facet_candidates = [
            hit for hit in admitted
            if len(hit.get("aspect_scores") or []) > aspect_index
            and float(hit["aspect_scores"][aspect_index]) >= _minimum_direct_similarity(hit, config)
        ]
        facet_candidates.sort(
            key=lambda hit: (
                float((hit.get("aspect_scores") or [0.0])[aspect_index])
                + profile_score_boost * float(hit.get("profile_affinity", 0.0)),
                _selection_rank_score(hit, config),
            ),
            reverse=True,
        )
        for candidate in facet_candidates:
            if can_select(candidate):
                select(candidate)
                break
        if len(selected) >= top_k:
            return selected[:top_k]

    for candidate in admitted:
        if len(selected) >= top_k:
            break
        if can_select(candidate):
            select(candidate)
    return selected[:top_k]


def _resolve_rerank_config(settings: Dict[str, Any], final_top_k: int) -> Dict[str, Any]:
    enabled = bool(settings.get("retrieval_rerank_enabled", True))
    alpha = float(settings.get("retrieval_rerank_alpha", 1.0) or 0.0)
    step2_config = settings.get("step2", {}) or {}
    use_step2_1 = bool(
        step2_config.get("attention_mask_enabled", False)
        or step2_config.get("centrality_enabled", False)
        or step2_config.get("contradiction_enabled", False)
        or step2_config.get("cluster_compression_enabled", False)
        or step2_config.get("temporal_head_enabled", False)
    )
    if not enabled or alpha <= 0.0:
        return {"enabled": False, "alpha": 0.0, "pool_k": final_top_k, "step2_config": step2_config, "use_step2_1": use_step2_1}
    pool_k = max(final_top_k, int(settings.get("retrieval_rerank_pool_k", 34) or 34))
    return {"enabled": True, "alpha": alpha, "pool_k": pool_k, "step2_config": step2_config, "use_step2_1": use_step2_1}


def _cross_memory_rerank(
    hits: List[Dict[str, Any]],
    query: str,
    embedding_by_id: Dict[str, np.ndarray],
    alpha: float,
) -> List[Dict[str, Any]]:
    if not hits or alpha <= 0.0:
        return hits

    query_terms = _content_tokens(query)
    if not query_terms:
        return hits

    sim_floor = 0.45
    scaling_temp = 0.4
    softmax_temp = 0.5
    gate_offset = 2.0
    gate_steepness = 0.8
    residual_weight = 0.3

    rescored: List[Dict[str, Any]] = []
    for item in hits:
        memory = item.get("memory") or {}
        memory_id = str(memory.get("id") or "")
        base = float(item.get("base_score") or item.get("score") or 0.0)
        support = 0.0
        bonus = 0.0

        memory_terms = _content_tokens(str(memory.get("text") or ""))
        overlap = len(query_terms & memory_terms)

        if str(memory.get("stream") or "rough") == "learnings" and overlap >= 1:
            vector_i = embedding_by_id.get(memory_id)
            if vector_i is not None:
                raw_sims: List[float] = []
                rough_overlaps: List[int] = []
                for other in hits:
                    other_memory = other.get("memory") or {}
                    other_id = str(other_memory.get("id") or "")
                    if other_id == memory_id:
                        continue
                    if str(other_memory.get("stream") or "rough") != "rough":
                        continue
                    vector_j = embedding_by_id.get(other_id)
                    if vector_j is None:
                        continue
                    other_overlap = len(query_terms & _content_tokens(str(other_memory.get("text") or "")))
                    if other_overlap == 0:
                        continue
                    raw_sims.append(float(cosine_similarity(vector_i, vector_j)))
                    rough_overlaps.append(other_overlap)

                support = 0.0
                if raw_sims:
                    min_sim = min(raw_sims)
                    max_sim = max(raw_sims)
                    sim_range = max_sim - min_sim if max_sim != min_sim else 1.0
                    max_overlap = max(rough_overlaps) if rough_overlaps else 1
                    scaled_sims: List[float] = []
                    base_values: List[float] = []

                    for other in hits:
                        other_memory = other.get("memory") or {}
                        other_id = str(other_memory.get("id") or "")
                        if other_id == memory_id:
                            continue
                        if str(other_memory.get("stream") or "rough") != "rough":
                            continue
                        vector_j = embedding_by_id.get(other_id)
                        if vector_j is None:
                            continue

                        other_overlap = len(query_terms & _content_tokens(str(other_memory.get("text") or "")))
                        if other_overlap == 0:
                            continue

                        sim = float(cosine_similarity(vector_i, vector_j))
                        normalized_sim = (sim - min_sim) / sim_range
                        if normalized_sim < sim_floor:
                            continue

                        scaled_sims.append(normalized_sim / scaling_temp)
                        q_boost = 1.0 + (other_overlap / max_overlap)
                        base_values.append((float(other.get("base_score") or other.get("score") or 0.0) ** 2) * q_boost)

                    if scaled_sims:
                        exp_scores = [np.exp(value / softmax_temp) for value in scaled_sims]
                        total = float(sum(exp_scores))
                        if total > 0.0:
                            weights = [value / total for value in exp_scores]
                            support = sum(weight * base_value for weight, base_value in zip(weights, base_values))

                gate = 1.0 / (1.0 + np.exp(-(overlap - gate_offset) * gate_steepness))
                bonus = alpha * gate * support

        final_score = base * (1 - residual_weight * min(bonus / (base + 0.001), 1.0)) + bonus
        rescored.append(
            {
                **item,
                "score": final_score,
                "final_score": final_score,
                "step2_bonus": bonus,
                "support_score": support,
            }
        )

    rescored.sort(
        key=lambda item: (
            float(item.get("final_score") or item.get("score") or 0.0),
            parse_timestamp((item.get("memory") or {}).get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return rescored


def _build_attention_matrix(
    hits: List[Dict[str, Any]],
    query_terms: set[str],
    embedding_by_id: Dict[str, np.ndarray],
    intent: str,
    step2_config: Dict[str, Any],
) -> np.ndarray:
    N = len(hits)
    A = np.zeros((N, N), dtype=np.float32)

    sim_floor = float(step2_config.get("sim_floor", 0.45))
    mask = step2_config.get("attention_mask", {})
    intent_mask = mask.get(intent, mask.get("balanced", {}))
    if not intent_mask:
        intent_mask = {"rough_to_rough": 0.25, "rough_to_learnings": 0.25, "learnings_to_rough": 0.25, "learnings_to_learnings": 0.25}
    gate_offset = float(step2_config.get("gate_offset", 2.0))
    gate_steepness = float(step2_config.get("gate_steepness", 0.8))
    temporal_enabled = bool(step2_config.get("temporal_head_enabled", False))
    temporal_tau_minutes = float(step2_config.get("temporal_tau_minutes", 30))
    temporal_head_weight = float(step2_config.get("temporal_head_weight", 0.2))
    semantic_head_weight = float(step2_config.get("semantic_head_weight", 0.8))

    streams: List[str] = []
    base_scores: List[float] = []
    mem_ids: List[str] = []
    timestamps: List[Optional[datetime]] = []
    for h in hits:
        mem = h.get("memory", {})
        streams.append(str(mem.get("stream", "rough")))
        base_scores.append(float(h.get("base_score", h.get("score", 0.0))))
        mem_ids.append(str(mem.get("id", "")))
        ts_str = str(mem.get("ts", ""))
        timestamps.append(parse_timestamp(ts_str))

    for i in range(N):
        vec_i = embedding_by_id.get(mem_ids[i])
        if vec_i is None:
            continue

        for j in range(N):
            if i == j:
                continue

            pair_key = f"{streams[j]}_to_{streams[i]}"
            stream_weight = float(intent_mask.get(pair_key, 0.0))
            if stream_weight <= 0.0:
                continue

            vec_j = embedding_by_id.get(mem_ids[j])
            if vec_j is None:
                continue

            j_text = str(hits[j].get("memory", {}).get("text", ""))
            j_overlap = len(query_terms & _content_tokens(j_text))
            if j_overlap == 0:
                continue

            sim = float(cosine_similarity(vec_i, vec_j))
            if sim < sim_floor:
                continue

            gate = 1.0 / (1.0 + np.exp(-(j_overlap - gate_offset) * gate_steepness))

            if temporal_enabled and timestamps[i] is not None and timestamps[j] is not None:
                delta_minutes = abs((timestamps[i] - timestamps[j]).total_seconds() / 60.0)
                temporal_sim = np.exp(-delta_minutes / temporal_tau_minutes)
                combined_sim = semantic_head_weight * sim + temporal_head_weight * temporal_sim
                A[i][j] = combined_sim * stream_weight * gate * (base_scores[j] ** 2)
            else:
                A[i][j] = sim * stream_weight * gate * (base_scores[j] ** 2)

    return A


def _ode_settle(
    base_scores: np.ndarray,
    embedding_by_id: Dict[str, np.ndarray],
    tau_values: np.ndarray,
    memory_ids: List[str],
    lnn_config: Dict[str, Any],
    persisted_weights: Optional[Dict[str, Dict[str, float]]] = None,
    initial_h: Optional[np.ndarray] = None,
    query_vector: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, Dict[str, Dict[str, float]]]:
    if len(base_scores) == 0:
        return base_scores, {}

    ode_dt = float(lnn_config.get("ode_dt", 0.1))
    ode_steps = int(lnn_config.get("ode_steps", 10))
    alpha = float(lnn_config.get("alpha", 0.3))
    beta = float(lnn_config.get("beta", 1.0))
    sim_floor = float(lnn_config.get("weights_sim_floor", 0.45))
    stored_blend = float(lnn_config.get("stored_weight_blend", 0.7))
    max_weight = float(lnn_config.get("max_weight", 1.0))

    if query_vector is not None and len(base_scores) > 0:
        seed_sims = []
        for i in range(min(len(memory_ids), len(base_scores))):
            vec_i = embedding_by_id.get(memory_ids[i])
            if vec_i is not None:
                seed_sims.append(float(cosine_similarity(query_vector, vec_i)))
        if seed_sims:
            avg_seed_sim = sum(seed_sims) / len(seed_sims)
            if avg_seed_sim < 0.30:
                stored_blend = max(0.2, stored_blend - 0.35)
            elif avg_seed_sim < 0.40:
                stored_blend = max(0.3, stored_blend - 0.20)
    learning_rate = float(lnn_config.get("learning_rate", 0.01))
    hebbian_threshold = float(lnn_config.get("hebbian_threshold", 0.3))

    n = len(base_scores)
    W = np.zeros((n, n), dtype=np.float32)

    for i in range(n):
        vec_i = embedding_by_id.get(memory_ids[i])
        if vec_i is None:
            continue
        stored_out = (persisted_weights or {}).get(memory_ids[i], {})
        for j in range(n):
            if i == j:
                continue
            vec_j = embedding_by_id.get(memory_ids[j])
            if vec_j is None:
                continue
            fresh_sim = float(cosine_similarity(vec_i, vec_j))
            stored_w = float(stored_out.get(memory_ids[j], 0.0))
            stored_w = min(stored_w, max_weight)
            if stored_w != 0.0:
                W[i, j] = stored_blend * stored_w + (1.0 - stored_blend) * fresh_sim
            elif fresh_sim >= sim_floor:
                W[i, j] = fresh_sim

    h = initial_h.copy().astype(np.float32) if initial_h is not None else base_scores.copy().astype(np.float32)
    for _ in range(ode_steps):
        excitation = alpha * (W @ np.tanh(h))
        decay = -h / np.maximum(tau_values, 0.01)
        input_current = beta * base_scores
        h += ode_dt * (decay + excitation + input_current)
        h = np.maximum(h, 0.0)

    weight_deltas: Dict[str, Dict[str, float]] = {}
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if h[i] >= hebbian_threshold and h[j] >= hebbian_threshold:
                delta = learning_rate * float(h[i]) * float(h[j])
                existing = weight_deltas.get(memory_ids[i], {}).get(memory_ids[j], 0.0)
                weight_deltas.setdefault(memory_ids[i], {})[memory_ids[j]] = min(
                    existing + delta, max_weight
                )

    return h, weight_deltas


def _ode_rerank_hits(
    hits: List[Dict[str, Any]],
    query: str,
    embedding_by_id: Dict[str, np.ndarray],
    lnn_config: Dict[str, Any],
    query_vector: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    n = len(hits)
    if n == 0:
        return hits

    from app.storage.memories import get_memory_repository

    base_scores = np.array([float(h.get("base_score") or h.get("score") or 0.0) for h in hits], dtype=np.float32)
    memory_ids = [str((h.get("memory") or {}).get("id") or "") for h in hits]
    tau_values = np.array([float((h.get("memory") or {}).get("tau") or lnn_config.get("tau_default", 0.5)) for h in hits], dtype=np.float32)

    persisted_weights: Dict[str, Dict[str, float]] = {}
    for h in hits:
        mem = h.get("memory") or {}
        outgoing = mem.get("outgoing_weights")
        if isinstance(outgoing, dict) and outgoing:
            persisted_weights[str(mem.get("id") or "")] = outgoing

    evolved_h, hebbian_deltas = _ode_settle(base_scores, embedding_by_id, tau_values, memory_ids, lnn_config,
                                            persisted_weights=persisted_weights, query_vector=query_vector)

    tau_boost = float(lnn_config.get("tau_boost", 0.05))
    repo = get_memory_repository()
    weight_delta_pairs: List[Tuple[str, str, float]] = []

    rescored: List[Dict[str, Any]] = []
    for i, item in enumerate(hits):
        ode_score = float(evolved_h[i])
        rescored.append({
            **item,
            "score": ode_score,
            "final_score": ode_score,
            "step2_bonus": ode_score - base_scores[i],
            "support_score": 0.0,
            "ode_activation": ode_score,
            "ode_base_score": float(base_scores[i]),
        })

        mem_id = memory_ids[i]
        new_tau = min(0.95, tau_values[i] + tau_boost)
        repo.update_lnn_state(mem_id, tau=new_tau)

        if mem_id in hebbian_deltas:
            for target_id, delta in hebbian_deltas[mem_id].items():
                weight_delta_pairs.append((mem_id, target_id, delta))

    if weight_delta_pairs:
        repo.batch_update_weights(weight_delta_pairs)

    rescored.sort(
        key=lambda item: (
            float(item.get("final_score") or item.get("score") or 0.0),
            parse_timestamp((item.get("memory") or {}).get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return rescored


def _expanding_ode_rerank_hits(
    hits: List[Dict[str, Any]],
    query: str,
    embedding_by_id: Dict[str, np.ndarray],
    lnn_config: Dict[str, Any],
    filters: Optional[CandidateFilters] = None,
    query_vector: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    n = len(hits)
    if n == 0:
        return hits

    from app.storage.memories import get_memory_repository

    repo = get_memory_repository()

    base_scores = np.array([float(h.get("base_score") or h.get("score") or 0.0) for h in hits], dtype=np.float32)
    memory_ids = [str((h.get("memory") or {}).get("id") or "") for h in hits]
    tau_values = np.array([float((h.get("memory") or {}).get("tau") or lnn_config.get("tau_default", 0.5)) for h in hits], dtype=np.float32)

    persisted_weights: Dict[str, Dict[str, float]] = {}
    for h in hits:
        mem = h.get("memory") or {}
        outgoing = mem.get("outgoing_weights")
        if isinstance(outgoing, dict) and outgoing:
            persisted_weights[str(mem.get("id") or "")] = outgoing

    expansion_threshold = float(lnn_config.get("expansion_threshold", 0.35))
    min_edge_weight = float(lnn_config.get("min_edge_weight", 0.30))
    neighbors_per_memory = int(lnn_config.get("neighbors_per_active_memory", 8))
    max_active = int(lnn_config.get("max_active_memories", 200))
    max_hops = int(lnn_config.get("max_expansion_hops", 2))
    min_expansion_score = float(lnn_config.get("min_expansion_score", 0.12))
    expansion_relevance_floor = float(lnn_config.get("expansion_relevance_floor", 0.25))
    gamma = float(lnn_config.get("inherited_activation_gamma", 0.5))
    tau_default = float(lnn_config.get("tau_default", 0.5))
    tau_boost = float(lnn_config.get("tau_boost", 0.05))

    seed_h, seed_deltas = _ode_settle(base_scores, embedding_by_id, tau_values, memory_ids, lnn_config,
                                      persisted_weights=persisted_weights, query_vector=query_vector)
    weight_delta_pairs: List[Tuple[str, str, float]] = []
    for source_id, deltas in seed_deltas.items():
        for target_id, delta in deltas.items():
            weight_delta_pairs.append((source_id, target_id, delta))

    current_ids: set[str] = set(memory_ids)
    all_h = seed_h.copy()
    all_scores = base_scores.copy()
    all_tau = tau_values.copy()
    all_ids = list(memory_ids)
    activation_paths: Dict[str, List[str]] = {mid: ["seed"] for mid in memory_ids}
    expanded_records_by_id: Dict[str, Dict[str, Any]] = {}
    seed_ids = set(memory_ids)

    for hop in range(max_hops):
        if len(all_ids) >= max_active:
            break

        pending_neighbors: Dict[str, List[float]] = {}
        pending_paths: Dict[str, set[str]] = {}

        for i, mid in enumerate(all_ids):
            h_val = float(all_h[i])
            if h_val < expansion_threshold:
                continue
            neighbors = repo.get_strong_neighbors(mid, min_weight=min_edge_weight,
                                                  max_neighbors=neighbors_per_memory)
            for neighbor_id, weight, tau_n in neighbors:
                if neighbor_id in current_ids:
                    continue
                exp_score = h_val * weight * tau_n
                if exp_score < min_expansion_score:
                    continue
                contribution = gamma * h_val * weight
                pending_neighbors.setdefault(neighbor_id, []).append(contribution)
                pending_paths.setdefault(neighbor_id, set()).add(mid)

        if not pending_neighbors:
            break

        available_slots = max_active - len(all_ids)
        if available_slots <= 0:
            break
        neighbor_ids = list(pending_neighbors.keys())[:available_slots]
        neighbor_data = repo.query_by_ids(neighbor_ids)

        new_bases: List[float] = []
        new_taus: List[float] = []
        new_embedding_by_id: Dict[str, np.ndarray] = dict(embedding_by_id)
        added_ids: List[str] = []

        for nid in neighbor_ids:
            inherited = min(1.0, sum(pending_neighbors[nid]))
            nrec = neighbor_data.get(nid)
            if not nrec or not _memory_matches_candidate_filters(nrec, filters):
                continue
            n_emb = nrec.get("embedding")
            if query_vector is not None and isinstance(n_emb, list) and n_emb:
                n_sim_to_query = float(cosine_similarity(query_vector, np.array(n_emb, dtype=np.float32)))
                if n_sim_to_query < expansion_relevance_floor:
                    continue
            n_tau = float(nrec.get("tau") or tau_default)
            if isinstance(n_emb, list) and n_emb:
                new_embedding_by_id[nid] = np.array(n_emb, dtype=np.float32)
            n_outgoing = nrec.get("outgoing_weights")
            if isinstance(n_outgoing, dict) and n_outgoing:
                persisted_weights.setdefault(nid, {}).update(n_outgoing)

            new_bases.append(0.0)
            new_taus.append(n_tau)
            current_ids.add(nid)
            all_ids.append(nid)
            added_ids.append(nid)
            expanded_records_by_id[nid] = nrec
            activation_paths[nid] = [f"{src} -> {nid}" for src in sorted(pending_paths[nid])]

        if not added_ids:
            break

        all_scores = np.concatenate([all_scores, np.array(new_bases, dtype=np.float32)])
        all_tau = np.concatenate([all_tau, np.array(new_taus, dtype=np.float32)])
        embedding_by_id = new_embedding_by_id

        initial_h = all_h.copy()
        inherited_arr = np.array([sum(pending_neighbors.get(nid, [])) if nid in pending_neighbors else 0.0
                                   for nid in added_ids], dtype=np.float32)
        initial_h = np.concatenate([initial_h, np.minimum(inherited_arr, 1.0)])

        all_h, hop_deltas = _ode_settle(
            all_scores, embedding_by_id, all_tau, all_ids, lnn_config,
            persisted_weights=persisted_weights, initial_h=initial_h,
            query_vector=query_vector,
        )
        for source_id, deltas in hop_deltas.items():
            for target_id, delta in deltas.items():
                weight_delta_pairs.append((source_id, target_id, delta))

    score_config = lnn_config.get("final_score", {})
    activation_weight = float(score_config.get("activation_weight", 0.55))
    similarity_weight = float(score_config.get("similarity_weight", 0.25))
    support_weight = float(score_config.get("support_weight", 0.15))
    tau_weight = float(score_config.get("tau_weight", 0.05))
    density_penalty = float(lnn_config.get("density_penalty", 0.30))
    debug_trace = bool(lnn_config.get("debug_activation_trace", True))

    cluster_degree: Dict[int, int] = {}
    if density_penalty > 0 and len(all_ids) > 1:
        n_all = len(all_ids)
        sim_buffer: List[Tuple[int, int, float]] = []
        for i in range(n_all):
            vec_i = embedding_by_id.get(all_ids[i])
            if vec_i is None:
                continue
            for j in range(i + 1, n_all):
                vec_j = embedding_by_id.get(all_ids[j])
                if vec_j is None:
                    continue
                s = float(cosine_similarity(vec_i, vec_j))
                if s >= 0.55:
                    sim_buffer.append((i, j, s))
        for i, j, _ in sim_buffer:
            cluster_degree[i] = cluster_degree.get(i, 0) + 1
            cluster_degree[j] = cluster_degree.get(j, 0) + 1
        max_deg = max(cluster_degree.values()) if cluster_degree else 1

    rescored: List[Dict[str, Any]] = []

    for i, mid in enumerate(all_ids):
        ode_h = float(all_h[i])
        base = float(all_scores[i])
        tau_val = float(all_tau[i])

        if density_penalty > 0 and i in cluster_degree and max_deg > 0:
            deg = cluster_degree[i]
            penalty = density_penalty * (deg / max_deg)
            ode_h = ode_h * (1.0 - penalty)

        support = 0.0
        for j in range(len(all_ids)):
            if i != j and all_h[j] >= expansion_threshold:
                support += float(all_h[j])

        final_score = (activation_weight * ode_h
                       + similarity_weight * base
                       + support_weight * min(support / max(1.0, len(all_ids) - 1), 1.0)
                       + tau_weight * tau_val)

        if mid in seed_ids:
            orig_hit = next(h for h in hits if str((h.get("memory") or {}).get("id") or "") == mid)
            entry = {**orig_hit}
        else:
            nrec = expanded_records_by_id.get(mid)
            if not nrec:
                continue
            entry = {
                "memory": nrec,
                "score": final_score,
                "base_score": base,
                "final_score": final_score,
                "step2_bonus": 0.0,
                "support_score": support,
            }

        entry.update({
            "score": final_score,
            "final_score": final_score,
            "step2_bonus": final_score - base,
            "support_score": support,
            "ode_activation": ode_h,
            "ode_base_score": base,
            "expanded_activation": True,
        })
        if debug_trace and mid in activation_paths:
            entry["activation_path"] = activation_paths[mid]

        rescored.append(entry)
        new_tau = min(0.95, tau_val + tau_boost)
        repo.update_lnn_state(mid, tau=new_tau)

    if weight_delta_pairs:
        repo.batch_update_weights(weight_delta_pairs)

    rescored.sort(
        key=lambda item: (
            float(item.get("final_score") or item.get("score") or 0.0),
            parse_timestamp((item.get("memory") or {}).get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return rescored


def _step2_1_rerank(
    hits: List[Dict[str, Any]],
    query: str,
    embedding_by_id: Dict[str, np.ndarray],
    alpha: float,
    step2_config: Dict[str, Any],
    lnn_config: Optional[Dict[str, Any]] = None,
    filters: Optional[CandidateFilters] = None,
    query_vector: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    if not hits or alpha <= 0.0:
        return hits

    if lnn_config is None:
        lnn_config = {}
    if lnn_config.get("enabled") and lnn_config.get("use_ode_rerank"):
        if lnn_config.get("expanding_activation"):
            return _expanding_ode_rerank_hits(hits, query, embedding_by_id, lnn_config, filters=filters, query_vector=query_vector)
        return _ode_rerank_hits(hits, query, embedding_by_id, lnn_config, query_vector=query_vector)

    query_terms = _content_tokens(query)
    if not query_terms:
        return hits

    attention_mask_enabled = bool(step2_config.get("attention_mask_enabled", False))
    centrality_enabled = bool(step2_config.get("centrality_enabled", False))
    contradiction_enabled = bool(step2_config.get("contradiction_enabled", False))
    cluster_enabled = bool(step2_config.get("cluster_compression_enabled", False))
    temporal_enabled = bool(step2_config.get("temporal_head_enabled", False))

    if not attention_mask_enabled and not centrality_enabled and not contradiction_enabled and not cluster_enabled and not temporal_enabled:
        return _cross_memory_rerank(hits, query, embedding_by_id, alpha)

    from .router import route_intent
    intent = route_intent(query.lower().strip()) if attention_mask_enabled else "balanced"

    softmax_temp = float(step2_config.get("softmax_temp", 0.5))
    gate_offset = float(step2_config.get("gate_offset", 2.0))
    gate_steepness = float(step2_config.get("gate_steepness", 0.8))
    residual_weight = float(step2_config.get("residual_weight", 0.3))

    A = _build_attention_matrix(hits, query_terms, embedding_by_id, intent, step2_config)

    centrality_scores: Dict[int, float] = {}
    if centrality_enabled:
        centrality_scores = _compute_centrality(
            hits, query_terms, embedding_by_id, A, step2_config,
        )

    contradiction_adjustment: Dict[int, float] = {}
    tension_notes: Dict[int, str] = {}
    if contradiction_enabled:
        contradiction_adjustment, tension_notes = _detect_contradictions(
            hits, embedding_by_id, step2_config,
        )

    cluster_map: Dict[int, Dict[str, Any]] = {}
    if cluster_enabled:
        sim_floor = float(step2_config.get("sim_floor", 0.45))
        communities = _detect_clusters(A, sim_floor)
        for cluster_id, community in enumerate(communities):
            if not community:
                continue
            cluster_size = len(community)
            cluster_items = sorted(community, key=lambda idx: float(
                hits[idx].get("base_score", hits[idx].get("score", 0.0))), reverse=True)
            rep_idx = cluster_items[0]
            rep_text = str(hits[rep_idx].get("memory", {}).get("text", ""))
            has_tension = any(idx in tension_notes for idx in community)
            timestamps = []
            for idx in community:
                ts = parse_timestamp(str(hits[idx].get("memory", {}).get("ts", "")))
                if ts:
                    timestamps.append(ts)
            oldest_ts = min(timestamps).strftime("%Y-%m-%d") if timestamps else ""
            newest_ts = max(timestamps).strftime("%Y-%m-%d") if timestamps else ""
            for idx in community:
                cluster_map[idx] = {
                    "cluster_id": cluster_id,
                    "cluster_size": cluster_size,
                    "cluster_representative_text": rep_text,
                    "cluster_has_tension": has_tension,
                    "cluster_oldest_ts": oldest_ts,
                    "cluster_newest_ts": newest_ts,
                }

    rescored: List[Dict[str, Any]] = []
    for i, item in enumerate(hits):
        memory = item.get("memory") or {}
        memory_id = str(memory.get("id") or "")
        base = float(item.get("base_score") or item.get("score") or 0.0)
        bonus = 0.0
        support = 0.0

        memory_terms = _content_tokens(str(memory.get("text") or ""))
        overlap = len(query_terms & memory_terms)

        if overlap >= 1:
            row = A[i]
            nonzero_mask = row > 0.0
            if np.any(nonzero_mask):
                support_values = row[nonzero_mask]
                if len(support_values) > 0:
                    scaled = support_values / softmax_temp
                    scaled = scaled - np.max(scaled)
                    exp_scores = np.exp(scaled)
                    weights = exp_scores / np.sum(exp_scores)
                    support = float(np.sum(weights * support_values))

            gate = 1.0 / (1.0 + np.exp(-(overlap - gate_offset) * gate_steepness))
            bonus = alpha * gate * support

            if centrality_enabled and i in centrality_scores:
                centrality_lambda = float(step2_config.get("centrality_lambda", 0.3))
                centrality_bonus = alpha * centrality_lambda * centrality_scores[i]
                bonus += centrality_bonus

            if contradiction_enabled and i in contradiction_adjustment:
                bonus += contradiction_adjustment[i]

        final_score = base * (1 - residual_weight * min(bonus / (base + 0.001), 1.0)) + bonus
        hit_dict = {
            **item,
            "score": final_score,
            "final_score": final_score,
            "step2_bonus": bonus,
            "support_score": support,
        }
        if contradiction_enabled and i in tension_notes:
            hit_dict["tension_note"] = tension_notes[i]
            hit_dict["step2_contradiction_delta"] = contradiction_adjustment.get(i, 0.0)
        if cluster_enabled and i in cluster_map:
            hit_dict.update(cluster_map[i])
        rescored.append(hit_dict)

    rescored.sort(
        key=lambda item: (
            float(item.get("final_score") or item.get("score") or 0.0),
            parse_timestamp((item.get("memory") or {}).get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return rescored


def _compute_centrality(
    hits: List[Dict[str, Any]],
    query_terms: set[str],
    embedding_by_id: Dict[str, np.ndarray],
    A: np.ndarray,
    step2_config: Dict[str, Any],
) -> Dict[int, float]:
    N = len(hits)
    if N == 0:
        return {}

    centrality_lambda = float(step2_config.get("centrality_lambda", 0.3))
    iterations = int(step2_config.get("centrality_iterations", 2))
    diversity_penalty = bool(step2_config.get("centrality_diversity_penalty", True))

    base_scores = np.array([float(h.get("base_score", h.get("score", 0.0))) for h in hits], dtype=np.float32)
    scores = base_scores.copy()

    for _ in range(iterations):
        new_scores = np.zeros(N, dtype=np.float32)
        for i in range(N):
            row = A[i]
            weighted_sum = float(np.dot(row, scores))
            new_scores[i] = base_scores[i] * (1.0 - centrality_lambda) + centrality_lambda * weighted_sum
        scores = new_scores

    if diversity_penalty and N > 1:
        mem_ids = [str(h.get("memory", {}).get("id", "")) for h in hits]
        sim_dup_threshold = 0.85
        for i in range(N):
            vec_i = embedding_by_id.get(mem_ids[i])
            if vec_i is None:
                continue
            high_sim_count = 0
            for j in range(N):
                if i == j:
                    continue
                vec_j = embedding_by_id.get(mem_ids[j])
                if vec_j is None:
                    continue
                if float(cosine_similarity(vec_i, vec_j)) > sim_dup_threshold:
                    high_sim_count += 1
            if high_sim_count > 1:
                scores[i] = scores[i] / (1.0 + (high_sim_count - 1) * 0.5)

    return {i: float(scores[i]) for i in range(N)}


def _detect_contradictions(
    hits: List[Dict[str, Any]],
    embedding_by_id: Dict[str, np.ndarray],
    step2_config: Dict[str, Any],
) -> Tuple[Dict[int, float], Dict[int, str]]:
    N = len(hits)
    if N < 2:
        return {}, {}

    sim_threshold = float(step2_config.get("contradiction_sim_threshold", 0.7))
    tension_weight = float(step2_config.get("contradiction_tension_weight", 0.3))
    antonym_pairs = step2_config.get("contradiction_antonyms", [])
    if not antonym_pairs:
        return {}, {}

    mem_ids = [str(h.get("memory", {}).get("id", "")) for h in hits]
    content_tokens_list = [
        _content_tokens(str(h.get("memory", {}).get("text", ""))) for h in hits
    ]
    raw_tokens_list = [
        set(_tokenize(str(h.get("memory", {}).get("text", "")))) for h in hits
    ]
    timestamps = [
        parse_timestamp(str(h.get("memory", {}).get("ts", ""))) for h in hits
    ]

    adjustment: Dict[int, float] = {}
    tension: Dict[int, str] = {}

    for i in range(N):
        vec_i = embedding_by_id.get(mem_ids[i])
        if vec_i is None:
            continue
        tokens_i = content_tokens_list[i]
        raw_i = raw_tokens_list[i]
        ts_i = timestamps[i]

        for j in range(i + 1, N):
            vec_j = embedding_by_id.get(mem_ids[j])
            if vec_j is None:
                continue
            tokens_j = content_tokens_list[j]
            raw_j = raw_tokens_list[j]
            ts_j = timestamps[j]

            sim = float(cosine_similarity(vec_i, vec_j))
            if sim < sim_threshold:
                continue

            antonym_i: Optional[str] = None
            antonym_j: Optional[str] = None
            for a1, a2 in antonym_pairs:
                has_a1_i = a1 in raw_i
                has_a2_i = a2 in raw_i
                has_a1_j = a1 in raw_j
                has_a2_j = a2 in raw_j

                if has_a1_i and has_a2_j:
                    antonym_i = a1
                    antonym_j = a2
                    break
                if has_a2_i and has_a1_j:
                    antonym_i = a2
                    antonym_j = a1
                    break

            if antonym_i is None:
                continue

            antonym_set = {antonym_i, antonym_j}
            shared_context = tokens_i & tokens_j
            shared_content = shared_context - antonym_set
            if not shared_content:
                continue

            if ts_i is None or ts_j is None or ts_i == ts_j:
                continue

            if ts_j > ts_i:
                older_i, newer_i = i, j
                older_text = str(hits[i].get("memory", {}).get("text", ""))
                newer_text = str(hits[j].get("memory", {}).get("text", ""))
            else:
                older_i, newer_i = j, i
                older_text = str(hits[j].get("memory", {}).get("text", ""))
                newer_text = str(hits[i].get("memory", {}).get("text", ""))

            delta = tension_weight * sim
            adjustment[older_i] = adjustment.get(older_i, 0.0) - delta
            adjustment[newer_i] = adjustment.get(newer_i, 0.0) + delta

            older_ts = timestamps[older_i]
            newer_ts = timestamps[newer_i]
            older_ts_str = older_ts.strftime("%Y-%m-%d") if older_ts else "unknown"
            newer_ts_str = newer_ts.strftime("%Y-%m-%d") if newer_ts else "unknown"

            note = (
                f"preference appears to have changed from "
                f'"{older_text[:80]}" ({older_ts_str}) '
                f'to "{newer_text[:80]}" ({newer_ts_str})'
            )
            tension[older_i] = note
            tension[newer_i] = note

    return adjustment, tension


def _detect_clusters(
    A: np.ndarray,
    sim_floor: float,
) -> List[set[int]]:
    N = A.shape[0]
    if N < 2:
        return [set() for _ in range(N)]

    graph = nx.Graph()
    for i in range(N):
        graph.add_node(i)
        for j in range(i + 1, N):
            weight = float(A[i][j])
            if weight > 0.0:
                graph.add_edge(i, j, weight=weight)

    if graph.number_of_edges() == 0:
        return [{i} for i in range(N)]

    communities = list(greedy_modularity_communities(graph))
    return [set(c) for c in communities]


def _sinusoidal_position(position: float, dim: int) -> np.ndarray:
    enc = np.zeros(dim, dtype=np.float32)
    for i in range(dim // 2):
        angle = position / (10000.0 ** (2.0 * i / dim))
        enc[2 * i] = np.sin(angle)
        enc[2 * i + 1] = np.cos(angle)
    if dim % 2 == 1:
        enc[dim - 1] = np.sin(position / (10000.0 ** (2.0 * (dim - 1) / dim)))
    return enc


def _memory_sequence_position(mem: Dict[str, Any]) -> Dict[str, float]:
    ts_str = str(mem.get("ts") or "")
    parsed = parse_timestamp(ts_str)
    if parsed:
        days_since_epoch = (parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() / 86400.0
    else:
        days_since_epoch = 0.0

    turn = int(mem.get("turn") or 0)
    session_id = str(mem.get("session_id") or "")
    memory_id = str(mem.get("id") or "")
    parts = memory_id.split(":")
    index_in_turn = int(parts[-1]) if len(parts) >= 3 and parts[-1].isdigit() else 0

    scene_id = str(mem.get("scene_id") or "")
    scene_index = hash(scene_id) % 1000 if scene_id else 0.0

    return {
        "global_position": days_since_epoch,
        "session_position": float(turn),
        "local_position": float(index_in_turn),
        "scene_position": float(scene_index),
    }


def _sequence_encoding_vector(mem: Dict[str, Any], dim: int) -> np.ndarray:
    pos = _memory_sequence_position(mem)
    global_enc = _sinusoidal_position(pos["global_position"] / 365.0, dim)
    session_enc = _sinusoidal_position(pos["session_position"], dim)
    local_enc = _sinusoidal_position(pos["local_position"], dim)
    return (global_enc + session_enc + local_enc) / 3.0


def _compute_sequence_similarity_to_anchors(
    candidate_mem: Dict[str, Any],
    anchor_mems: List[Dict[str, Any]],
    embedding_by_id: Dict[str, np.ndarray],
    config: Dict[str, Any],
) -> float:
    if not anchor_mems:
        return 0.0

    dim = int(config.get("sequence_encoding_dim", 16))
    window = int(config.get("sequence_neighbor_window", 3))

    cand_vec = _sequence_encoding_vector(candidate_mem, dim)
    cand_id = str(candidate_mem.get("id") or "")

    scores: List[float] = []
    for anchor in anchor_mems:
        anchor_id = str(anchor.get("id") or "")
        if anchor_id == cand_id:
            continue

        anchor_vec = _sequence_encoding_vector(anchor, dim)
        sim = float(cosine_similarity(cand_vec, anchor_vec))
        scores.append(sim)

    if not scores:
        return 0.0

    max_sim = max(scores)
    mean_sim = sum(scores) / len(scores)
    return 0.4 * max_sim + 0.6 * mean_sim


def _sequence_encoding_score(
    candidates: List[Dict[str, Any]],
    above_threshold_indices: List[int],
    embedding_by_id: Dict[str, np.ndarray],
    intent: str,
    config: Dict[str, Any],
) -> Dict[int, float]:
    if not candidates or not above_threshold_indices:
        return {}

    intent_weights = config.get("sequence_intent_weights", {})
    weight = float(intent_weights.get(intent, intent_weights.get("balanced", 0.10)))

    if weight <= 0.0:
        return {}

    dim = int(config.get("sequence_encoding_dim", 16))
    window = int(config.get("sequence_neighbor_window", 3))

    anchor_mems = [candidates[i] for i in above_threshold_indices if i < len(candidates)]

    seq_scores: Dict[int, float] = {}
    for i, cand in enumerate(candidates):
        seq_sim = _compute_sequence_similarity_to_anchors(cand, anchor_mems, embedding_by_id, config)
        seq_scores[i] = seq_sim * weight

    return seq_scores


def _subspace_slices(vector: np.ndarray, num_heads: int) -> List[np.ndarray]:
    return np.array_split(vector, num_heads)


def _subspace_activation_weights(query_vector: np.ndarray, num_heads: int) -> List[float]:
    subspace_vectors = _subspace_slices(query_vector, num_heads)
    weights = []
    for subspace in subspace_vectors:
        norm_val = float(np.linalg.norm(subspace))
        if norm_val > 0:
            weights.append(norm_val)
        else:
            weights.append(0.0)
    total = sum(weights)
    if total > 0:
        return [w / total for w in weights]
    return [1.0 / num_heads] * num_heads


def _multi_head_similarity(
    query_vector: np.ndarray,
    memory_vector: np.ndarray,
    intent: str,
    step1_config: Dict[str, Any],
) -> Dict[str, float]:
    if not _step1_enabled(step1_config, "multi_head_enabled"):
        raw = float(cosine_similarity(query_vector, memory_vector))
        return {"raw_similarity": raw, "multi_head_score": raw, "match_score": raw}

    num_heads = int(step1_config.get("multi_head_num_subspaces", 4))
    blend = float(step1_config.get("multi_head_subspace_blend", 0.3))
    alpha = float(step1_config.get("multi_head_aspect_alpha", 0.5))

    raw = float(cosine_similarity(query_vector, memory_vector))

    subspace_weights = _subspace_activation_weights(query_vector, num_heads)
    subspace_scores = []
    offset = 0
    for i, subspace_q in enumerate(_subspace_slices(query_vector, num_heads)):
        subspace_k = memory_vector[offset:offset + len(subspace_q)]
        if len(subspace_q) == 0 or len(subspace_k) == 0:
            subspace_scores.append(0.0)
        else:
            sub_sim = float(cosine_similarity(subspace_q, subspace_k))
            subspace_scores.append(sub_sim * subspace_weights[i])
        offset += len(subspace_q)

    intent_weights = step1_config.get("multi_head_intent_weights", {}).get(intent, {})
    intent_subspace_mult = {}
    for key, val in intent_weights.items():
        if key.startswith("subspace_"):
            idx = int(key.split("_")[1]) - 1
            intent_subspace_mult[idx] = float(val)

    weighted_subspace_scores = []
    for i, score in enumerate(subspace_scores):
        mult = intent_subspace_mult.get(i, 1.0)
        weighted_subspace_scores.append(score * mult)

    subspace_score = sum(weighted_subspace_scores) / num_heads if weighted_subspace_scores else 0.0

    multi_head_score = (1 - alpha) * subspace_score + alpha * raw

    match_score = (1 - blend) * raw + blend * multi_head_score

    return {
        "raw_similarity": raw,
        "multi_head_score": multi_head_score,
        "match_score": match_score,
        "subspace_scores": subspace_scores,
        "subspace_weights": subspace_weights,
    }


def _candidate_token_stats(memories: List[Dict[str, Any]]) -> Dict[str, int]:
    token_counts: Dict[str, int] = {}
    for mem in memories:
        tokens = _content_tokens(str(mem.get("text", "")))
        for token in tokens:
            token_counts[token] = token_counts.get(token, 0) + 1
    return token_counts


def _compute_value_score(
    memory: Dict[str, Any],
    token_counts: Dict[str, int],
    total_memories: int,
    now: datetime,
    step1_config: Dict[str, Any],
) -> Dict[str, float]:
    if not _step1_enabled(step1_config, "value_assessment_enabled"):
        return {"value_score": 0.0, "components": {}}

    beta = float(step1_config.get("value_beta", 0.3))
    info_density_w = float(step1_config.get("value_info_density_weight", 0.25))
    reliability_w = float(step1_config.get("value_reliability_weight", 0.25))
    specificity_w = float(step1_config.get("value_specificity_weight", 0.20))
    stream_w = float(step1_config.get("value_stream_weight", 0.15))
    connectivity_w = float(step1_config.get("value_connectivity_weight", 0.10))
    recency_w = float(step1_config.get("value_recency_weight", 0.05))
    saturation = int(step1_config.get("value_info_density_saturation", 200))

    info_density = min(1.0, len(str(memory.get("text", ""))) / saturation)

    reliability = float(memory.get("source_reliability", 0.5))

    tokens = _content_tokens(str(memory.get("text", "")))
    if tokens and total_memories > 0:
        avg_freq = sum(token_counts.get(t, 1) for t in tokens) / len(tokens)
        specificity = max(0.0, 1.0 - (avg_freq / max(total_memories, 1)))
    else:
        specificity = 0.5

    stream = str(memory.get("stream", "rough"))
    stream_score = 1.0 if stream == "learnings" else 0.0

    ts_str = str(memory.get("ts") or "")
    ts = parse_timestamp(ts_str)
    if ts:
        age_days = (now - ts).total_seconds() / 86400.0
    else:
        age_days = 999.0
    recency_score = max(0.0, 1.0 - (age_days / 30.0))

    components = {
        "info_density": info_density,
        "reliability": reliability,
        "specificity": specificity,
        "stream_match": stream_score,
        "connectivity": 0.0,
        "recency": recency_score,
    }

    total_w = info_density_w + reliability_w + specificity_w + stream_w + connectivity_w + recency_w
    if total_w <= 0:
        return {"value_score": 0.0, "beta": float(step1_config.get("value_beta", 0.3)), "components": {}}
    value_score = (
        info_density_w * info_density +
        reliability_w * reliability +
        specificity_w * specificity +
        stream_w * stream_score +
        connectivity_w * 0.0 +
        recency_w * recency_score
    ) / total_w

    return {
        "value_score": value_score,
        "beta": beta,
        "components": components,
    }


def _temporal_score(
    memory: Dict[str, Any],
    now: datetime,
    resolved_intent: str,
    step1_config: Dict[str, Any],
) -> Dict[str, float]:
    if not _step1_enabled(step1_config, "recency_weighted_enabled"):
        return {"temporal_score": 0.0, "recency_weight": 0.0}

    tau_days = float(step1_config.get("recency_tau_days", 30))
    intent_weights = step1_config.get("recency_intent_weights", {})
    recency_weight = float(intent_weights.get(resolved_intent, intent_weights.get("balanced", 0.15)))

    ts_str = str(memory.get("ts") or "")
    ts = parse_timestamp(ts_str)
    if ts:
        age_days = (now - ts).total_seconds() / 86400.0
    else:
        age_days = 999.0

    temporal = max(0.0, min(1.0, np.exp(-age_days / tau_days)))

    return {
        "temporal_score": temporal,
        "recency_weight": recency_weight,
    }


def _compute_neighborhood_density(
    vectors: List[np.ndarray],
    neighborhood_sim_min: float,
) -> List[float]:
    n = len(vectors)
    if n == 0:
        return []
    densities = []
    for i in range(n):
        vi = vectors[i]
        if vi is None:
            densities.append(0.0)
            continue
        count = 0
        for j in range(n):
            if i == j:
                continue
            vj = vectors[j]
            if vj is None:
                continue
            sim = float(cosine_similarity(vi, vj))
            if sim >= neighborhood_sim_min:
                count += 1
        density = count / max(n - 1, 1)
        densities.append(density)
    return densities

def _keyword_fallback_hits(
    memories: List[Dict[str, Any]],
    query: str,
    top_k: int,
    selection_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    query_terms = set(_tokenize(query))
    query_content_terms = _content_tokens(query)
    if not query_terms:
        return []

    strong_lexical_coverage = float((selection_config or {}).get("strong_lexical_coverage", 0.0))
    if len(query_content_terms) <= 2:
        strong_lexical_coverage = float(
            (selection_config or {}).get("keyword_short_query_min_coverage", 0.50)
        )
    enforce_selection = bool((selection_config or {}).get("enabled", False))
    hits = []
    for mem in memories:
        text = str(mem.get("text") or "")
        text_terms = set(_tokenize(text))
        overlap = query_terms & text_terms
        if not overlap:
            continue
        content_overlap = query_content_terms & _content_tokens(text)
        lexical_coverage = len(content_overlap) / max(len(query_content_terms), 1)
        if enforce_selection and lexical_coverage < strong_lexical_coverage:
            continue
        score = len(overlap) / max(len(query_terms), 1)
        hits.append(
            {
                "memory": mem,
                "score": score,
                "base_score": score,
                "final_score": score,
                "step2_bonus": 0.0,
                "support_score": 0.0,
                "lexical_coverage": lexical_coverage,
                "retrieval_method": "keyword_fallback",
            }
        )

    def _sort_key(item: Dict[str, Any]) -> tuple[float, datetime]:
        mem = item.get("memory", {})
        ts_val = mem.get("ts") if isinstance(mem, dict) else getattr(mem, "ts", None)
        return (item["score"], parse_timestamp(ts_val) or datetime.min.replace(tzinfo=timezone.utc))

    hits.sort(key=_sort_key, reverse=True)
    return hits[:top_k]


def _step1_enabled(step1_config: Dict[str, Any], extension_key: str) -> bool:
    if not step1_config.get("enabled", False):
        return False
    return bool(step1_config.get(extension_key, False))


def _condition_query(query: str, intent: str, step1_config: Dict[str, Any]) -> str:
    if not _step1_enabled(step1_config, "query_conditioning_enabled"):
        return query
    prefixes = step1_config.get("query_conditioning_prefixes", {})
    prefix = str(prefixes.get(intent, prefixes.get("balanced", "")) or "")
    return prefix + query


def retrieve_memories(
    query: str,
    session_id: Optional[str] = None,
    memory_types: Optional[List[str]] = None,
    top_k: Optional[int] = None,
    min_similarity: Optional[float] = None,
    min_reliability: Optional[float] = None,
    mode: str = "both",
    intent: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from .config import load_settings
    settings = load_settings()
    lnn_config = settings.get("lnn") or {}
    step1_config = settings.get("step1", {}) or {}
    selection_config = settings.get("retrieval_selection", {}) or {}
    selection_enabled = bool(selection_config.get("enabled", False))
    top_k = top_k or settings.get("retrieval_top_k", 8)
    rerank = _resolve_rerank_config(settings, int(top_k))
    min_similarity = min_similarity if min_similarity is not None else settings.get("retrieval_min_similarity", 0.25)

    reliability_config = settings.get("retrieval", {})
    min_reliability = min_reliability if min_reliability is not None else reliability_config.get("min_reliability", 0.4)

    recency_days = settings.get("retrieval_recency_days")
    session_bias = settings.get("retrieval_session_bias", True)

    extracted = extract_date_brackets(query)
    date_from = date_from or extracted["date_from"]
    date_to = date_to or extracted["date_to"]

    filters = CandidateFilters(
        recency_days=recency_days,
        session_id=session_id,
        session_bias=session_bias,
        memory_types=memory_types,
        mode=mode,
        min_reliability=min_reliability,
        date_from=date_from,
        date_to=date_to,
    )

    fts_query = _build_fts_query(query)
    if fts_query:
        lexical_candidates = query_memory_candidates_with_text(fts_query, filters)
        if selection_enabled and bool(selection_config.get("hybrid_candidates_enabled", True)):
            semantic_candidates = query_memory_candidates(filters)
            filtered = _merge_candidate_lanes(lexical_candidates, semantic_candidates)
        else:
            filtered = lexical_candidates
    else:
        filtered = query_memory_candidates(filters)
    filtered = apply_hidden_metadata_filter(filtered)
    filtered = _dedupe_prefer_latest(filtered)

    if not filtered:
        return []

    if not query.strip():
        # Date-only query: return filtered candidates sorted by recency, no semantic scoring.
        from datetime import timezone as tz
        filtered.sort(
            key=lambda m: parse_timestamp(m.get("ts")) or datetime.min.replace(tzinfo=tz.utc),
            reverse=True,
        )
        return [{"memory": m, "score": 0.0, "base_score": 0.0, "final_score": 0.0,
                 "step2_bonus": 0.0, "support_score": 0.0} for m in filtered[:top_k]]

    from .router import route_intent
    resolved_intent = intent if intent else route_intent(query.lower().strip())
    raw_query = query.strip()
    conditioned_query = _condition_query(raw_query, resolved_intent, step1_config)
    query_aspects = _query_aspects(raw_query, selection_config) if selection_enabled else [raw_query]
    profile_mode = _profile_query_mode(raw_query) if selection_enabled else None
    profile_query = profile_mode is not None
    entity_query = selection_enabled and _is_multi_anchor_entity_query(raw_query)
    query_embedding_inputs = [conditioned_query]
    aspect_embedding_offset = 0
    if selection_enabled and _canonical_text(conditioned_query) != _canonical_text(raw_query):
        query_embedding_inputs.append(raw_query)
        aspect_embedding_offset = 1
    query_embedding_inputs.extend(query_aspects[1:])

    try:
        query_embeddings = embed(query_embedding_inputs)
        query_vector = query_embeddings[0]
        direct_aspect_vectors = query_embeddings[
            aspect_embedding_offset:aspect_embedding_offset + len(query_aspects)
        ]
    except Exception as exc:
        LOGGER.warning("Embedding backend unavailable for query embedding; falling back to keyword retrieval: %s", exc)
        return _keyword_fallback_hits(
            filtered,
            query,
            int(top_k),
            selection_config if selection_enabled else None,
        )

    vectors: List[Optional[np.ndarray]] = [None for _ in filtered]
    missing_texts: List[str] = []
    missing_indices: List[int] = []

    for idx, mem in enumerate(filtered):
        stored = mem.get("embedding")
        if isinstance(stored, list) and stored:
            vectors[idx] = np.array(stored, dtype=np.float32)
            continue
        blob = mem.get("_embedding_blob")
        dim = mem.get("_embedding_dim")
        dtype = mem.get("_embedding_dtype")
        if blob and dim:
            try:
                decoded = unpack_embedding(blob, dim, dtype)
                if decoded is not None:
                    vectors[idx] = decoded
                    continue
            except ValueError:
                LOGGER.warning("Skipping invalid embedding blob for memory id=%s", mem.get("id"))
                vectors[idx] = None
        else:
            missing_texts.append(str(mem.get("text", "")))
            missing_indices.append(idx)

    if missing_texts:
        try:
            embedded = embed(missing_texts)
        except Exception as exc:
            LOGGER.warning("Embedding backend unavailable for candidate embeddings; continuing with stored vectors only: %s", exc)
            embedded = []
        for pos, vector in enumerate(embedded):
            vectors[missing_indices[pos]] = vector

    hits = []
    embedding_by_id: Dict[str, np.ndarray] = {}

    now = datetime.now(timezone.utc)
    token_counts = _candidate_token_stats(filtered) if _step1_enabled(step1_config, "value_assessment_enabled") else {}

    soft_threshold_enabled = _step1_enabled(step1_config, "soft_threshold_enabled")
    neighborhood_weight = 0.0
    value_weight = 0.0
    neighborhood_sim_min = 0.5
    soft_floor = 0.10
    if soft_threshold_enabled:
        neighborhood_weight = float(step1_config.get("soft_threshold_neighborhood_weight", 0.3))
        value_weight = float(step1_config.get("soft_threshold_value_weight", 0.15))
        neighborhood_sim_min = float(step1_config.get("soft_threshold_neighborhood_sim_min", 0.5))
        soft_floor = float(step1_config.get("soft_threshold_floor", 0.10))

    candidate_data: List[Dict[str, Any]] = []
    for idx, mem in enumerate(filtered):
        vector = vectors[idx]
        if vector is None:
            continue
        memory_id = str(mem.get("id") or "")
        if memory_id:
            embedding_by_id[memory_id] = vector

        if _step1_enabled(step1_config, "multi_head_enabled"):
            mh = _multi_head_similarity(query_vector, vector, resolved_intent, step1_config)
            raw_score = mh["raw_similarity"]
            match_score = mh["match_score"]
        else:
            raw_score = float(cosine_similarity(query_vector, vector))
            match_score = raw_score

        value_info = _compute_value_score(mem, token_counts, len(filtered), now, step1_config)
        if value_info["value_score"] > 0 and value_info.get("beta", 0) > 0:
            beta = value_info["beta"]
            valued_score = match_score * (1.0 + beta * value_info["value_score"])
        else:
            valued_score = match_score

        temporal_info = _temporal_score(mem, now, resolved_intent, step1_config)
        if temporal_info["recency_weight"] > 0:
            recency_boost = 1.0 + temporal_info["recency_weight"] * temporal_info["temporal_score"]
            valued_score = valued_score * recency_boost

        candidate_data.append({
            "mem": mem,
            "vector": vector,
            "memory_id": memory_id,
            "raw_score": raw_score,
            "match_score": match_score,
            "valued_score": valued_score,
            "value_info": value_info,
            "temporal_info": temporal_info,
            "mh": mh if _step1_enabled(step1_config, "multi_head_enabled") else None,
            "direct_evidence": _direct_evidence(
                vector,
                direct_aspect_vectors,
                str(mem.get("text") or ""),
                query_aspects,
            ),
        })

    neighborhood_densities: List[float] = []
    if soft_threshold_enabled and candidate_data:
        neighborhood_densities = _compute_neighborhood_density(
            [c["vector"] for c in candidate_data],
            neighborhood_sim_min,
        )

    for i, cand in enumerate(candidate_data):
        mem = cand["mem"]
        raw_score = cand["raw_score"]
        valued_score = cand["valued_score"]
        value_info = cand["value_info"]
        temporal_info = cand["temporal_info"]
        mh = cand["mh"]

        if soft_threshold_enabled:
            density = neighborhood_densities[i] if i < len(neighborhood_densities) else 0.0
            adaptive_threshold = max(
                soft_floor,
                neighborhood_weight * density + value_weight * value_info["value_score"],
            )
            if valued_score < adaptive_threshold:
                continue
        else:
            if valued_score < min_similarity:
                continue

        hit = {
            "memory": mem,
            "score": valued_score,
            "base_score": raw_score,
            "final_score": valued_score,
            "step2_bonus": 0.0,
            "support_score": 0.0,
            "value_score": value_info["value_score"],
            "value_components": value_info.get("components", {}),
            "query_aspects": query_aspects,
            "profile_query": profile_query,
            "profile_mode": profile_mode,
            "profile_pattern_anchor": (
                profile_mode == "psychology" and "pattern" in _lexical_terms(str(mem.get("text") or ""))
            ),
            "entity_query": entity_query,
            "query_echo_memory": selection_enabled and _is_query_echo_memory(
                str(mem.get("text") or ""), raw_query
            ),
            "profile_affinity": (
                _profile_memory_affinity(mem, profile_mode)
                if profile_query and bool(selection_config.get("user_profile_metadata_tiebreak_enabled", False))
                else 0.0
            ),
            **cand["direct_evidence"],
        }
        if mh is not None:
            hit["multi_head_score"] = mh["multi_head_score"]
            hit["subspace_scores"] = mh.get("subspace_scores", [])
        if _step1_enabled(step1_config, "recency_weighted_enabled"):
            hit["temporal_score"] = temporal_info["temporal_score"]
            hit["recency_weight"] = temporal_info["recency_weight"]
        hits.append(hit)

    if _step1_enabled(step1_config, "sequence_encoding_enabled") and hits:
        above_threshold_indices = list(range(len(hits)))
        seq_scores = _sequence_encoding_score(
            [h["memory"] for h in hits],
            above_threshold_indices,
            embedding_by_id,
            resolved_intent,
            step1_config,
        )

        if seq_scores:
            for i, hit in enumerate(hits):
                if i in seq_scores:
                    seq_boost = seq_scores[i]
                    hit["score"] = hit["score"] + seq_boost
                    hit["final_score"] = hit["score"]
                    hit["sequence_score"] = seq_boost

    def _main_sort_key(item: Dict[str, Any]) -> tuple[float, float, datetime]:
        mem = item.get("memory", {})
        ts_val = mem.get("ts") if isinstance(mem, dict) else getattr(mem, "ts", None)
        direct = float(item.get("direct_similarity", item["score"]) or 0.0)
        # Multi-part recall must retain the strongest candidate for either facet
        # long enough for final facet coverage selection to see it.
        primary_score = direct if selection_enabled else float(item["score"])
        return (primary_score, float(item["score"]), parse_timestamp(ts_val) or datetime.min.replace(tzinfo=timezone.utc))

    hits.sort(key=_main_sort_key, reverse=True)

    configured_pool_k = int(rerank["pool_k"] if rerank["enabled"] else top_k)
    if selection_enabled and len(query_aspects) > 1:
        configured_pool_k = max(
            configured_pool_k,
            int(selection_config.get("multi_aspect_pool_k", configured_pool_k) or configured_pool_k),
        )
    pool_k = configured_pool_k
    if _step1_enabled(step1_config, "adaptive_pool_k_enabled") and rerank["enabled"]:
        base_k = int(step1_config.get("adaptive_pool_k_base", configured_pool_k))
        max_k = int(step1_config.get("adaptive_pool_k_max", max(base_k, configured_pool_k)))
        inspect_k = min(base_k * 2, len(hits))
        if inspect_k >= 2:
            top_scores = np.array([h["score"] for h in hits[:inspect_k]], dtype=np.float32)
            mean_score = float(np.mean(top_scores))
            std_score = float(np.std(top_scores))
            if mean_score > 0:
                cv = std_score / mean_score
                pool_k = base_k if cv > 0.25 else min(max_k, inspect_k)
            else:
                pool_k = base_k

    dedup_config = settings.get("retrieval_dedup", {}) or {}
    scan_multiplier = max(1, int(dedup_config.get("candidate_scan_multiplier", 3) or 3))
    dedup_scan_k = min(len(hits), max(int(top_k), pool_k) * scan_multiplier)
    candidate_hits = _collapse_near_duplicate_hits(
        hits[:dedup_scan_k],
        embedding_by_id,
        dedup_config,
    )
    if selection_enabled:
        candidate_hits.sort(
            key=lambda hit: (
                float(hit.get("direct_similarity", hit.get("score", 0.0)) or 0.0),
                float(hit.get("score", 0.0) or 0.0),
            ),
            reverse=True,
        )
    candidate_hits = candidate_hits[:pool_k]

    # Candidate recall is deliberately permissive. Before the LNN can amplify
    # a coherent but irrelevant cluster, require immutable query-to-memory evidence.
    if selection_enabled and not _query_has_sufficient_evidence(candidate_hits, selection_config):
        return []

    if rerank["enabled"]:
        if rerank.get("use_step2_1"):
            candidate_hits = _step2_1_rerank(candidate_hits, query, embedding_by_id, rerank["alpha"],
                                             rerank["step2_config"], lnn_config=lnn_config, filters=filters,
                                             query_vector=direct_aspect_vectors[0])
        else:
            candidate_hits = _cross_memory_rerank(candidate_hits, query, embedding_by_id, rerank["alpha"])

    if selection_enabled:
        candidate_hits = _select_diverse_hits(
            candidate_hits,
            embedding_by_id,
            int(top_k),
            len(query_aspects),
            selection_config,
        )

    if not candidate_hits and not selection_enabled and any(vector is None for vector in vectors):
        fallback_hits = _keyword_fallback_hits(
            filtered,
            query,
            int(top_k),
            selection_config if selection_enabled else None,
        )
        if min_reliability and min_reliability > 0:
            fallback_hits = [h for h in fallback_hits if float(h.get("memory", {}).get("source_reliability", 0)) >= min_reliability]
        return fallback_hits
    return candidate_hits[:top_k]
