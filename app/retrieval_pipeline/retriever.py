from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np

from app.embedding.embedder import embed
from app.save_pipeline.extraction.extractor import is_hidden_metadata_memory
from app.graph.similarity import cosine_similarity
from app.storage.memories import query_memory_candidates, query_memory_candidates_with_text, unpack_embedding
from app.storage.repository import CandidateFilters, MemoryStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalRequest:
    """Immutable compatibility request for the retrieval engine."""

    query: str
    session_id: Optional[str] = None
    # A tuple prevents a caller from mutating the request after it has crossed
    # the public boundary.  ``retrieve_memories`` still accepts a list and
    # converts it at construction time, so the external call shape is intact.
    memory_types: Optional[Tuple[str, ...]] = None
    top_k: Optional[int] = None
    min_similarity: Optional[float] = None
    min_reliability: Optional[float] = None
    mode: str = "both"
    intent: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    def __post_init__(self) -> None:
        # Direct construction is supported for internal callers and tests;
        # normalize mutable sequences even when ``from_compat`` is bypassed.
        if self.memory_types is not None and not isinstance(self.memory_types, tuple):
            object.__setattr__(self, "memory_types", tuple(self.memory_types))

    @classmethod
    def from_compat(
        cls,
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
    ) -> "RetrievalRequest":
        return cls(
            query=query,
            session_id=session_id,
            memory_types=tuple(memory_types) if memory_types is not None else None,
            top_k=top_k,
            min_similarity=min_similarity,
            min_reliability=min_reliability,
            mode=mode,
            intent=intent,
            date_from=date_from,
            date_to=date_to,
        )

    def runner_kwargs(self) -> Dict[str, Any]:
        """Return the legacy keyword values expected by the implementation.

        Lists are recreated here deliberately: old adapters and tests may
        mutate their input list, but cannot mutate the immutable request held
        by the engine.
        """
        return {
            "session_id": self.session_id,
            "memory_types": list(self.memory_types) if self.memory_types is not None else None,
            "top_k": self.top_k,
            "min_similarity": self.min_similarity,
            "min_reliability": self.min_reliability,
            "mode": self.mode,
            "intent": self.intent,
            "date_from": self.date_from,
            "date_to": self.date_to,
        }


@dataclass(frozen=True)
class RetrievalPolicy:
    """Resolved policy snapshot retained for future ranking changes.

    Resolution lives here rather than in the ranking function so a future
    engine can be tested against a stable policy independently of embeddings
    or storage.  The fallback expressions intentionally mirror the historic
    implementation (notably, a false-y ``top_k`` uses the configured default).
    """

    top_k: int
    min_similarity: float
    min_reliability: float
    recency_days: Optional[int]
    session_bias: bool

    @classmethod
    def resolve(cls, settings: Dict[str, Any], request: RetrievalRequest) -> "RetrievalPolicy":
        reliability_config = settings.get("retrieval", {}) or {}
        configured_top_k = settings.get("retrieval_top_k", 8)
        configured_similarity = settings.get("retrieval_min_similarity", 0.25)
        configured_reliability = reliability_config.get("min_reliability", 0.4)
        return cls(
            top_k=int(request.top_k or configured_top_k),
            min_similarity=float(
                request.min_similarity if request.min_similarity is not None else configured_similarity
            ),
            min_reliability=float(
                request.min_reliability
                if request.min_reliability is not None
                else configured_reliability
            ),
            recency_days=settings.get("retrieval_recency_days"),
            session_bias=bool(settings.get("retrieval_session_bias", True)),
        )


class RetrievalEngine:
    """Small internal seam around the established ranking implementation.

    The callable is deliberately injectable so admission/ranking tests can
    exercise the public request contract without importing private stages.
    """

    def __init__(self, runner: Callable[..., List[Dict[str, Any]]]) -> None:
        self._runner = runner

    def retrieve(self, request: RetrievalRequest) -> List[Dict[str, Any]]:
        return self._runner(request.query, **request.runner_kwargs())

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


def apply_hidden_metadata_filter(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [mem for mem in memories if not is_hidden_metadata_memory(mem)]


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
            cues.extend(["communication style", "explanation preference"])
        if "collaboration" in lowered or "frustrat" in lowered:
            cues.extend(["collaboration preference", "working constraint"])
    elif profile_mode == "psychology" and any(term in lowered for term in ("behavior", "pattern", "personality", "psycholog")):
        cues.extend(["recurring behavior", "personality pattern"])
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
    actor_terms = tuple(str(term) for term in config.get("profile_actor_terms", ()) if str(term).strip())
    profile_mode = (
        _profile_query_mode(normalized, actor_terms)
        if bool(config.get("profile_aspect_expansion_enabled", False))
        else None
    )
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


def _query_mentions_profile_actor(query: str, actor_terms: Tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", query, re.IGNORECASE)
        for term in actor_terms
        if term
    )


def _profile_actor_terms(settings: Mapping[str, Any]) -> Tuple[str, ...]:
    identity = settings.get("identity", {})
    if not isinstance(identity, Mapping):
        identity = {}
    configured = [identity.get("user_display_name")]
    aliases = identity.get("user_aliases", ())
    if isinstance(aliases, (list, tuple, set, frozenset)):
        configured.extend(aliases)
    terms = ["user", "i", "me", "my", *configured]
    return tuple(dict.fromkeys(str(term).strip().lower() for term in terms if str(term or "").strip()))


def _profile_query_mode(query: str, actor_terms: Tuple[str, ...]) -> Optional[str]:
    lowered = str(query or "").lower()
    if not _query_mentions_profile_actor(lowered, actor_terms):
        return None
    if any(term in lowered for term in ("behavior", "pattern", "personality", "psycholog")):
        return "psychology"
    if any(term in lowered for term in ("collaboration", "explain", "frustrat", "prefer", "preference", "working style")):
        return "preference"
    return None


def _is_profile_query(query: str, actor_terms: Tuple[str, ...]) -> bool:
    return _profile_query_mode(query, actor_terms) is not None


def _profile_memory_affinity(memory: Dict[str, Any], profile_mode: Optional[str]) -> float:
    speaker_focus = str(memory.get("speaker_focus") or "").lower()
    memory_kind = str(memory.get("memory_kind") or "").lower()
    if profile_mode == "psychology":
        if memory_kind in {"user_fact", "relationship"} and speaker_focus == "user":
            return 1.0
        if memory_kind in {"user_fact", "relationship"}:
            return 0.55
        if speaker_focus == "user" and memory_kind == "user_preference":
            return 0.30
        if memory_kind in PROFILE_MEMORY_KINDS:
            return 0.20
        return 0.0
    if speaker_focus == "user" and memory_kind in PROFILE_MEMORY_KINDS:
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


def _has_meaningful_lexical_anchor(hit: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """Require more than one generic token before admitting a weak pointer."""
    lexical_coverage = float(hit.get("lexical_coverage", 0.0) or 0.0)
    if lexical_coverage <= 0.0:
        return False

    memory = hit.get("memory") or {}
    memory_terms = _lexical_terms(str(memory.get("text") or ""))
    aspects = hit.get("query_aspects") or []
    if not memory_terms or not aspects:
        return lexical_coverage >= float(config.get("keyword_short_query_min_coverage", 0.50))

    for aspect in aspects:
        aspect_terms = _lexical_terms(str(aspect))
        overlap = memory_terms & aspect_terms
        if not overlap:
            continue
        if any(
            any(char.isdigit() for char in token) and any(char.isalpha() for char in token)
            for token in overlap
        ):
            return True
        if len(aspect_terms) <= 2 or len(overlap) >= 2:
            return True
    return False


def _query_has_sufficient_evidence(hits: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
    """Keep unrelated candidate pools from reaching associative reranking.

    Direct evidence is no longer a display-admission requirement.  A lexical
    anchor makes a weak pointer viable for navigation; without one, require a
    strong semantic anchor before allowing the pool to continue.  This keeps
    nonsense queries from being amplified by LNN while leaving final ranking
    responsible for ordering viable pointers.
    """
    viable_hits = [hit for hit in hits if not bool(hit.get("query_echo_memory", False))]
    if not viable_hits:
        return False
    if any(_has_meaningful_lexical_anchor(hit, config) for hit in viable_hits):
        return True
    return any(
        float(hit.get("direct_similarity", hit.get("base_score", hit.get("score", 0.0))) or 0.0)
        >= _minimum_direct_similarity(hit, config)
        for hit in viable_hits
    )


def _selection_rank_score(hit: Dict[str, Any], config: Dict[str, Any]) -> float:
    direct_similarity = float(hit.get("direct_similarity", hit.get("base_score", hit.get("score", 0.0))) or 0.0)
    lexical_coverage = float(hit.get("lexical_coverage", 0.0) or 0.0)
    profile_boost = 0.0
    if bool(config.get("user_profile_metadata_tiebreak_enabled", False)):
        profile_boost = float(config.get("profile_score_boost", 0.0)) * float(hit.get("profile_affinity", 0.0))
    pattern_anchor_boost = 0.0
    if bool(hit.get("profile_pattern_anchor", False)):
        pattern_anchor_boost = float(config.get("profile_pattern_anchor_boost", 0.0))
    return (
        direct_similarity
        + float(config.get("lexical_coverage_weight", 0.0)) * lexical_coverage
        + profile_boost
        + pattern_anchor_boost
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
    """Allocate visible slots to distinct pointers from a viable pool.

    Similarity and lexical coverage affect ordering, but do not certify a
    pointer as correct.  Query echoes remain excluded because they merely
    repeat the question instead of pointing to evidence.
    """
    admitted = [hit for hit in hits if not bool(hit.get("query_echo_memory", False))]
    if not admitted:
        return []

    profile_score_boost = float(config.get("profile_score_boost", 0.0))
    # Rank by direct query evidence and explicit metadata preferences.
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


def _keyword_fallback_hits(
    memories: List[Dict[str, Any]],
    query: str,
    top_k: int,
    selection_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    query_terms = _lexical_terms(query)
    query_content_terms = _content_tokens(query)
    if not query_terms:
        return []

    enforce_selection = bool((selection_config or {}).get("enabled", False))
    hits = []
    for mem in memories:
        text = str(mem.get("text") or "")
        text_terms = _lexical_terms(text)
        overlap = query_terms & text_terms
        if not overlap:
            continue
        content_overlap = query_content_terms & _content_tokens(text)
        if not content_overlap:
            continue
        # A single generic word in a long, otherwise unrelated query is not a
        # useful pointer.  Keep short exact-anchor queries permissive while
        # allowing partial matches for normal multi-term queries.
        if enforce_selection and len(query_content_terms) > 2 and len(content_overlap) < 2:
            continue
        if enforce_selection and _is_query_echo_memory(text, query):
            continue
        lexical_coverage = len(content_overlap) / max(len(query_content_terms), 1)
        score = len(content_overlap) / max(len(query_content_terms), 1)
        hits.append(
            {
                "memory": mem,
                "score": score,
                "base_score": score,
                "final_score": score,
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
    repository: Optional[MemoryStore] = None,
    persist_lnn_state: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieve memories; legacy ``persist_lnn_state`` is accepted but ignored.

    Retrieval no longer maintains LNN state, even when an old caller enables it.
    """

    request = RetrievalRequest.from_compat(
        query,
        session_id=session_id,
        memory_types=memory_types,
        top_k=top_k,
        min_similarity=min_similarity,
        min_reliability=min_reliability,
        mode=mode,
        intent=intent,
        date_from=date_from,
        date_to=date_to,
    )
    if repository is None:
        return RetrievalEngine(_retrieve_memories_impl).retrieve(request)
    return _retrieve_memories_impl(
        query,
        repository=repository,
        **request.runner_kwargs(),
    )


def _retrieve_memories_impl(
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
    repository: Optional[MemoryStore] = None,
) -> List[Dict[str, Any]]:
    from .config import load_settings
    settings = load_settings()
    request = RetrievalRequest.from_compat(
        query,
        session_id=session_id,
        memory_types=memory_types,
        top_k=top_k,
        min_similarity=min_similarity,
        min_reliability=min_reliability,
        mode=mode,
        intent=intent,
        date_from=date_from,
        date_to=date_to,
    )
    policy = RetrievalPolicy.resolve(settings, request)
    selection_config = dict(settings.get("retrieval_selection", {}) or {})
    actor_terms = _profile_actor_terms(settings)
    selection_config["profile_actor_terms"] = actor_terms
    selection_enabled = bool(selection_config.get("enabled", False))
    top_k = policy.top_k
    min_similarity = policy.min_similarity
    min_reliability = policy.min_reliability

    recency_days = policy.recency_days
    session_bias = policy.session_bias

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
        lexical_candidates = (
            repository.query_candidates_with_text(fts_query, filters)
            if repository is not None
            else query_memory_candidates_with_text(fts_query, filters)
        )
        if selection_enabled and bool(selection_config.get("hybrid_candidates_enabled", True)):
            semantic_candidates = (
                repository.query_candidates(filters)
                if repository is not None
                else query_memory_candidates(filters)
            )
            filtered = _merge_candidate_lanes(lexical_candidates, semantic_candidates)
        else:
            filtered = lexical_candidates
    else:
        filtered = repository.query_candidates(filters) if repository is not None else query_memory_candidates(filters)
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
        return [
            {"memory": m, "score": 0.0, "base_score": 0.0, "final_score": 0.0}
            for m in filtered[:top_k]
        ]

    raw_query = query.strip()
    query_aspects = _query_aspects(raw_query, selection_config) if selection_enabled else [raw_query]
    profile_mode = _profile_query_mode(raw_query, actor_terms) if selection_enabled else None
    profile_query = profile_mode is not None
    entity_query = selection_enabled and _is_multi_anchor_entity_query(raw_query)
    query_embedding_inputs = query_aspects

    try:
        query_embeddings = embed(query_embedding_inputs)
        query_vector = query_embeddings[0]
        direct_aspect_vectors = query_embeddings
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

    for mem, vector in zip(filtered, vectors):
        if vector is None:
            continue
        raw_score = float(cosine_similarity(query_vector, vector))
        if raw_score < min_similarity:
            continue
        memory_id = str(mem.get("id") or "")
        if memory_id:
            embedding_by_id[memory_id] = vector

        hit = {
            "memory": mem,
            "score": raw_score,
            "base_score": raw_score,
            "final_score": raw_score,
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
            **_direct_evidence(
                vector, direct_aspect_vectors, str(mem.get("text") or ""), query_aspects,
            ),
        }
        hits.append(hit)

    def _main_sort_key(item: Dict[str, Any]) -> tuple[float, float, datetime]:
        mem = item.get("memory", {})
        ts_val = mem.get("ts") if isinstance(mem, dict) else getattr(mem, "ts", None)
        direct = float(item.get("direct_similarity", item["score"]) or 0.0)
        # Multi-part recall must retain the strongest candidate for either facet
        # long enough for final facet coverage selection to see it.
        primary_score = direct if selection_enabled else float(item["score"])
        return (primary_score, float(item["score"]), parse_timestamp(ts_val) or datetime.min.replace(tzinfo=timezone.utc))

    hits.sort(key=_main_sort_key, reverse=True)

    configured_pool_k = max(int(top_k), int(selection_config.get("candidate_pool_k", 34)))
    if selection_enabled and len(query_aspects) > 1:
        configured_pool_k = max(
            configured_pool_k,
            int(selection_config.get("multi_aspect_pool_k", configured_pool_k) or configured_pool_k),
        )
    pool_k = configured_pool_k
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

    # Candidate recall is permissive; abstain if the pool lacks query evidence.
    if selection_enabled and not _query_has_sufficient_evidence(candidate_hits, selection_config):
        return []

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
