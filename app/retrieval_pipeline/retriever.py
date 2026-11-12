from datetime import datetime, timedelta, timezone
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from app.embedding.embedder import embed, embed_query_inputs
from app.save_pipeline.extraction.extractor import is_hidden_metadata_memory
from app.graph.similarity import cosine_similarity
from app.storage.memories import (
    _RETRIEVAL_REPOSITORY_KEY,
    query_memory_candidates,
    query_memory_candidates_with_text,
    unpack_embedding,
)
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
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
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


def _group_display_hits(hits: List[Dict[str, Any]], *, source_isolation: bool = False) -> List[Dict[str, Any]]:
    """Group identical statements only; keep every source pointer on memory.

    Similar vectors, token overlap, and shared scenes do not establish that
    two claims have the same subject, scope, numbers, or polarity.
    """
    from .federated import _display_group_key, _qualify_evidence, _with_duplicate_evidence

    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for index, hit in enumerate(hits):
        memory = hit.get("memory") or {}
        source = str(hit.get("source_agent") or memory.get("source_agent") or "")
        if source:
            memory = _qualify_evidence(memory, source)
            hit = {**hit, "memory": memory}
        key = (str(memory.get("result_kind") or "fact"), *_display_group_key(memory)) if str(memory.get("text") or "").strip() else (index,)
        if source_isolation:
            key = (str(hit.get("source_agent") or memory.get("source_agent") or ""), *key)
        groups.setdefault(key, []).append(hit)
    result = []
    for group in groups.values():
        winner = max(group, key=lambda hit: (
            float(hit.get("direct_similarity", hit.get("score", 0.0)) or 0.0),
            parse_timestamp((hit.get("memory") or {}).get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
        ))
        result.append({**winner, "memory": _with_duplicate_evidence(
            winner.get("memory") or {}, [hit.get("memory") or {} for hit in group]
        )})
    return result


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


def _query_retrieval_candidates(
    repository: Optional[MemoryStore],
    filters: CandidateFilters,
    *,
    fts_query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Use a repository's private lean read when available."""

    if repository is None:
        if fts_query is not None:
            return query_memory_candidates_with_text(
                fts_query,
                filters,
                _retrieval_projection=True,
            )
        return query_memory_candidates(filters, _retrieval_projection=True)

    if fts_query is not None:
        query = getattr(repository, "_query_retrieval_candidates_with_text", None)
        if callable(query):
            return query(fts_query, filters)
        return repository.query_candidates_with_text(fts_query, filters)

    query = getattr(repository, "_query_retrieval_candidates", None)
    if callable(query):
        return query(filters)
    return repository.query_candidates(filters)


def _hydrate_retrieval_hits(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Restore selected lean candidates without disturbing ranking metadata."""

    repositories: Dict[int, Dict[str, Any]] = {}
    for hit in hits:
        memory = hit.get("memory") or {}
        repository = memory.get(_RETRIEVAL_REPOSITORY_KEY)
        if repository is None:
            continue
        group = repositories.setdefault(id(repository), {"repository": repository, "ids": []})
        memory_id = str(memory.get("id") or "")
        if memory_id and memory_id not in group["ids"]:
            group["ids"].append(memory_id)

    hydrated_by_repository: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for repository_id, group in repositories.items():
        repository = group["repository"]
        hydrate = getattr(repository, "_hydrate_retrieval_candidates", None)
        if not callable(hydrate):
            raise RuntimeError("retrieval candidate repository cannot hydrate selected winners")
        hydrated = hydrate(group["ids"])
        missing_ids = [memory_id for memory_id in group["ids"] if memory_id not in hydrated]
        if missing_ids:
            raise RuntimeError(
                "selected retrieval candidates disappeared before hydration: "
                + ", ".join(missing_ids)
            )
        hydrated_by_repository[repository_id] = hydrated

    result: List[Dict[str, Any]] = []
    for hit in hits:
        memory = dict(hit.get("memory") or {})
        repository = memory.pop(_RETRIEVAL_REPOSITORY_KEY, None)
        if repository is not None:
            stored = hydrated_by_repository.get(id(repository), {}).get(str(memory.get("id") or ""))
            if stored is None:
                raise RuntimeError("selected retrieval candidate is missing hydrated storage data")
            # Ranked candidate values win so annotations computed during
            # retrieval cannot be overwritten by storage hydration.
            memory = {**stored, **memory}
        result.append({**hit, "memory": memory})
    return result


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
    # A factual memory about search or retrieval may naturally share most of
    # the query's words. Require a question or a described search request
    # before treating that overlap as an echo of the user's query.
    question = re.search(r"\b(?:how|what|when|where|which|who|why)\b[^?]{0,300}\?", text, re.IGNORECASE)
    request = re.search(
        r"\b(?:asked|asks|requested|requests|instructed|told|wanted|wants)\b"
        r".{0,100}\b(?:search\w*|retriev\w*|recall\w*|find|look\s+up|quer\w*|memor(?:y|ies))\b",
        text,
        re.IGNORECASE,
    )
    if not question and not request:
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
    aspect_scores: Optional[List[float]] = None,
) -> Dict[str, Any]:
    if aspect_scores is None:
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
    """Keep unrelated candidate pools from reaching cross-memory reranking.

    Direct evidence is no longer a display-admission requirement.  A lexical
    anchor makes a weak pointer viable for navigation; without one, require a
    strong semantic anchor before allowing the pool to continue.  This keeps
    nonsense queries from being amplified by related memories while leaving final ranking
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
    if hit.get("retrieval_method") == "keyword_fallback":
        # A precise lexical pointer can compete for a result slot without
        # pretending that its coverage score is a semantic similarity.
        return float(hit.get("lexical_coverage", 0.0) or 0.0)
    direct_similarity = float(hit.get("direct_similarity", hit.get("base_score", hit.get("score", 0.0))) or 0.0)
    lexical_coverage = float(hit.get("lexical_coverage", 0.0) or 0.0)
    profile_boost = 0.0
    if bool(config.get("user_profile_metadata_tiebreak_enabled", False)):
        profile_boost = float(config.get("profile_score_boost", 0.0)) * float(hit.get("profile_affinity", 0.0))
    pattern_anchor_boost = 0.0
    if bool(hit.get("profile_pattern_anchor", False)):
        pattern_anchor_boost = float(config.get("profile_pattern_anchor_boost", 0.0))
    step2_delta = float(hit.get("final_score", direct_similarity) or 0.0) - float(
        hit.get("base_score", direct_similarity) or 0.0
    )
    return (
        direct_similarity
        + float(config.get("lexical_coverage_weight", 0.0)) * lexical_coverage
        + profile_boost
        + pattern_anchor_boost
        + float(config.get("step2_score_weight", 0.0)) * step2_delta
    )


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
    # Direct query evidence dominates; Step 2.1 contributes a small adjustment.
    ranked = []
    for hit in admitted:
        score = _selection_rank_score(hit, config)
        ranked.append({**hit, "selection_score": score, "score": score})
    admitted = ranked
    admitted.sort(key=lambda hit: hit["selection_score"], reverse=True)

    selected: List[Dict[str, Any]] = []

    def can_select(candidate: Dict[str, Any]) -> bool:
        return not any(candidate is existing for existing in selected)

    def select(candidate: Dict[str, Any]) -> None:
        selected.append(candidate)

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
        # A selected pointer can answer more than one facet. Do not spend a
        # second slot on a weaker candidate for a facet it already covers best.
        if facet_candidates and not can_select(facet_candidates[0]):
            continue
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
    overlaps: List[int] = []
    for h in hits:
        mem = h.get("memory", {})
        streams.append(str(mem.get("stream", "rough")))
        base_scores.append(float(h.get("base_score", h.get("score", 0.0))))
        mem_ids.append(str(mem.get("id", "")))
        ts_str = str(mem.get("ts", ""))
        timestamps.append(parse_timestamp(ts_str))
        overlaps.append(len(query_terms & _content_tokens(str(mem.get("text", "")))))

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

            j_overlap = overlaps[j]
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


def _step2_1_rerank(
    hits: List[Dict[str, Any]],
    query: str,
    embedding_by_id: Dict[str, np.ndarray],
    alpha: float,
    step2_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Stateless attention, centrality, temporal, and tension reranking."""
    if not hits or alpha <= 0.0:
        return hits

    query_terms = _content_tokens(query)
    if not query_terms:
        return hits

    attention_mask_enabled = bool(step2_config.get("attention_mask_enabled", False))
    centrality_enabled = bool(step2_config.get("centrality_enabled", False))
    contradiction_enabled = bool(step2_config.get("contradiction_enabled", False))
    cluster_enabled = bool(step2_config.get("cluster_compression_enabled", False))
    temporal_enabled = bool(step2_config.get("temporal_head_enabled", False))

    if not attention_mask_enabled and not centrality_enabled and not contradiction_enabled and not cluster_enabled and not temporal_enabled:
        return hits

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
        communities = _detect_clusters(A)
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

        final_score = float(base * (1 - residual_weight * min(bonus / (base + 0.001), 1.0)) + bonus)
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
                f'possible change: "{older_text[:80]}" ({older_ts_str}) '
                f'vs "{newer_text[:80]}" ({newer_ts_str})'
            )
            tension[older_i] = note
            tension[newer_i] = note

    return adjustment, tension


def _detect_clusters(A: np.ndarray) -> List[set[int]]:
    import networkx as nx
    from networkx.algorithms.community import greedy_modularity_communities

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
    date_basis: Optional[str] = None,
    source_agent: Optional[str] = None,
    scene_repository: Any = None,
    include_source_passages: bool = True,
) -> List[Dict[str, Any]]:
    """Retrieve memories; legacy ``persist_lnn_state`` is accepted but ignored.

    Retrieval no longer maintains LNN state, even when an old caller enables it.
    """

    from app.runtime.context import get_runtime_context
    from app.storage.scenes import get_scene_repository

    source = source_agent or get_runtime_context().agent_name
    hits = _retrieve_memories_impl(
        query, session_id=session_id, memory_types=memory_types,
        top_k=top_k, min_similarity=min_similarity, min_reliability=min_reliability,
        mode=mode, intent=intent, date_from=date_from, date_to=date_to,
        repository=repository, date_basis=date_basis, source_agent=source,
    )
    limit = int(top_k if top_k is not None else load_settings_for_limit())
    scene_repo = scene_repository if scene_repository is not None else (get_scene_repository() if repository is None else None)
    passages = _source_passage_hits(
        query, scene_repo, source, limit=limit, session_id=session_id,
        memory_types=memory_types, mode=mode, date_from=date_from, date_to=date_to,
        date_basis=date_basis, min_similarity=min_similarity, min_reliability=min_reliability,
        include_source_passages=include_source_passages,
    )
    return _combine_source_hits(hits, passages, limit)


def _source_passage_hits(
    query: str, repository: Any, source: str, *, limit: int,
    session_id: Optional[str] = None, memory_types: Optional[List[str]] = None,
    mode: str = "both", date_from: Optional[str] = None, date_to: Optional[str] = None,
    date_basis: Optional[str] = None, min_similarity: Optional[float] = None,
    min_reliability: Optional[float] = None, include_source_passages: bool = True,
) -> List[Dict[str, Any]]:
    """Find source evidence only when its metadata can satisfy the request."""
    from .event_time import parse_temporal_query
    from .source_search import search_source_passages

    if (not include_source_passages or limit <= 0 or repository is None
        or not callable(getattr(repository, "get_recent_scenes", None))
        or memory_types or mode not in {"both", "all"}
        or min_similarity is not None or (min_reliability is not None and min_reliability > 0)):
        return []
    explicit_dates = bool(date_from or date_to)
    if ((date_basis == "event" and explicit_dates)
        or (not explicit_dates and date_basis != "capture"
            and parse_temporal_query(query, reference_time=datetime.now(timezone.utc)))):
        return []

    # Filter capture dates before source search ranks and caps its candidates;
    # otherwise out-of-range matches can hide the only in-range passage.
    if ((date_from and parse_timestamp(date_from) is None)
        or (date_to and parse_timestamp(date_to) is None)):
        return []
    date_filters = {key: value for key, value in (
        ("date_from", date_from), ("date_to", date_to)
    ) if value}
    candidates = search_source_passages(query, repository=repository, source_agent=source,
                                        limit=100, session_id=session_id, **date_filters)
    return [{"memory": item, "result_kind": "source_passage", "score": item["score"]}
            for item in candidates]


def _combine_source_hits(
    hits: List[Dict[str, Any]], passages: List[Dict[str, Any]], limit: int,
) -> List[Dict[str, Any]]:
    """Reserve bounded source slots, expanding with larger requested limits."""
    if limit <= 0:
        return []
    first, remaining = [], []
    seen_events, seen_scenes = set(), set()
    reserved = min(max(2, limit // 4), 25, limit - 1 if hits else limit)
    source_slots = min(max(reserved, limit - min(len(hits), limit)), 100)
    for hit in passages if source_slots else []:
        memory = hit.get("memory") or {}
        source = str(memory.get("source_agent") or hit.get("source_agent") or "")
        events = memory.get("source_event_ids") or []
        keys = {(source, str(event)) for event in events if event}
        if not keys or keys & seen_events:
            continue
        seen_events.update(keys)
        scene_id = str(memory.get("scene_id") or "")
        scene = (source, str(memory.get("session_id") or ""), scene_id)
        if scene_id and scene in seen_scenes:
            remaining.append(hit)
        else:
            if scene_id:
                seen_scenes.add(scene)
            first.append(hit)
    unique = (first + remaining)[:source_slots]
    return [*hits[:limit - len(unique)], *unique]


def load_settings_for_limit() -> int:
    from .config import load_settings
    return int(load_settings().get("retrieval_top_k", 8))


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
    date_basis: Optional[str] = None,
    source_agent: str = "default",
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

    if date_basis not in (None, "capture", "event"):
        raise ValueError("date_basis must be 'capture' or 'event'")
    from .event_time import filter_records_by_event_time, parse_temporal_query
    from app.storage.memory_relations import filter_current, list_supersessions
    from app.storage.memories import get_memory_repository

    explicit_dates = bool(date_from or date_to)
    occurrence = None
    if date_basis == "event" and explicit_dates:
        start = date_from or "0001-01-01"
        end = (datetime.fromisoformat(date_to).date() + timedelta(days=1)).isoformat() if date_to else "9999-12-31"
        occurrence = (start, end)
    elif not explicit_dates and date_basis != "capture":
        occurrence = parse_temporal_query(query, reference_time=datetime.now(timezone.utc))
    if occurrence:
        date_from = date_to = None
        recency_days = None

    # Date-only capture bounds denote whole UTC days. Using explicit offsets
    # also keeps JSON comparisons consistent with stored aware timestamps.
    from app.storage.memories import JsonMemoryRepository, SqliteMemoryRepository
    capture_repo = repository if repository is not None else get_memory_repository()
    if isinstance(capture_repo, (JsonMemoryRepository, SqliteMemoryRepository)):
        if date_from and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from):
            date_from = f"{date_from}T00:00:00+00:00"
        if date_to and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to):
            date_to = f"{date_to}T23:59:59.999999+00:00"

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
    def read_candidates(candidate_filters: CandidateFilters) -> List[Dict[str, Any]]:
        if not fts_query or occurrence:
            return _query_retrieval_candidates(repository, candidate_filters)
        lexical = _query_retrieval_candidates(repository, candidate_filters, fts_query=fts_query)
        if selection_enabled and bool(selection_config.get("hybrid_candidates_enabled", True)):
            semantic = _query_retrieval_candidates(repository, candidate_filters)
            return _merge_candidate_lanes(lexical, semantic)
        return lexical

    relation_repo = repository if repository is not None else get_memory_repository()
    historical = bool(re.search(r"\b(history|historical|previous(?:ly)?|formerly|used to|superseded|correction(?:s)?|changed)\b", query, re.IGNORECASE))

    def filter_candidates(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = apply_hidden_metadata_filter(records)
        if mode in {"rough", "learnings"}:
            result = [memory for memory in result if str(memory.get("stream") or "rough") == mode]
        if occurrence:
            result = list(filter_records_by_event_time(result, occurrence, retain_unknown=False))
        try:
            if historical:
                links = list_supersessions(relation_repo, source=source_agent)
                old_ids = {link["old_id"] for link in links}
                new_ids = {link["new_id"] for link in links}
                result = [{**record, **({"correction_status": "superseded"} if str(record.get("id")) in old_ids else {"correction_status": "current"} if str(record.get("id")) in new_ids else {})} for record in result]
            else:
                result = filter_current(relation_repo, result, source=source_agent)
        except TypeError:
            # Third-party repository implementations may not support relations.
            pass
        return result

    candidates = read_candidates(filters)
    filtered = filter_candidates(candidates)
    # A preferred session can have rows that are hidden or outside the event
    # interval. Retry without bias before concluding there are no matches.
    if candidates and not filtered and session_id and session_bias:
        filtered = filter_candidates(read_candidates(replace(filters, session_bias=False)))

    if not filtered:
        return []

    if not query.strip() or (occurrence and re.fullmatch(r"\s*(?:\[[^]]+\]|\d{4}-\d{2}(?:-\d{2})?|today|yesterday|tomorrow)\s*", query, re.IGNORECASE)):
        # Date-only query: return filtered candidates sorted by recency, no semantic scoring.
        from datetime import timezone as tz
        filtered.sort(
            key=lambda m: parse_timestamp(m.get("ts")) or datetime.min.replace(tzinfo=tz.utc),
            reverse=True,
        )
        return _hydrate_retrieval_hits(_group_display_hits([
            {"memory": m, "score": 0.0, "base_score": 0.0, "final_score": 0.0}
            for m in filtered
        ])[:top_k])

    raw_query = query.strip()
    query_aspects = _query_aspects(raw_query, selection_config) if selection_enabled else [raw_query]
    profile_mode = _profile_query_mode(raw_query, actor_terms) if selection_enabled else None
    profile_query = profile_mode is not None
    entity_query = selection_enabled and _is_multi_anchor_entity_query(raw_query)
    query_embedding_inputs = query_aspects

    try:
        query_embeddings = embed_query_inputs(query_embedding_inputs, embed)
        if len(query_embeddings) != len(query_embedding_inputs):
            raise ValueError("Query embedding count does not match the requested inputs")
        query_embeddings = [np.asarray(vector, dtype=np.float32) for vector in query_embeddings]
        if not query_embeddings or any(
            vector.ndim != 1 or not vector.size or not np.isfinite(vector).all()
            or vector.shape != query_embeddings[0].shape or not np.any(vector)
            for vector in query_embeddings
        ):
            raise ValueError("Query embeddings are invalid or have inconsistent dimensions")
        query_vector = query_embeddings[0]
        direct_aspect_vectors = query_embeddings
    except Exception as exc:
        LOGGER.warning("Embedding backend unavailable for query embedding; falling back to keyword retrieval: %s", exc)
        return _hydrate_retrieval_hits(_group_display_hits(_keyword_fallback_hits(
            filtered, query, len(filtered),
            selection_config if selection_enabled else None,
        ))[:top_k])

    vectors: List[Optional[np.ndarray]] = [None for _ in filtered]
    missing_texts: List[str] = []
    missing_indices: List[int] = []

    for idx, mem in enumerate(filtered):
        stored = mem.get("embedding")
        if isinstance(stored, list) and stored:
            vectors[idx] = stored
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

    missing_keyword_mode = selection_config.get("missing_embedding_mode", "embed") == "keyword"
    if missing_texts and not missing_keyword_mode:
        try:
            embedded = embed(missing_texts)
        except Exception as exc:
            LOGGER.warning("Embedding backend unavailable for candidate embeddings; continuing with stored vectors only: %s", exc)
            embedded = []
        for pos, vector in enumerate(embedded[:len(missing_indices)]):
            vectors[missing_indices[pos]] = vector

    hits = []
    embedding_by_id: Dict[str, np.ndarray] = {}

    for mem, vector in zip(filtered, vectors):
        if vector is None:
            continue
        try:
            vector = np.asarray(vector, dtype=np.float32)
        except (TypeError, ValueError):
            LOGGER.warning("Skipping invalid embedding for memory id=%s", mem.get("id"))
            continue
        if vector.shape != query_vector.shape or not np.isfinite(vector).all():
            LOGGER.warning("Skipping incompatible embedding for memory id=%s", mem.get("id"))
            continue
        aspect_scores = [float(cosine_similarity(v, vector)) for v in direct_aspect_vectors]
        raw_score = aspect_scores[0]
        admission_score = max(aspect_scores) if selection_enabled else raw_score
        if admission_score < min_similarity:
            continue
        evidence = _direct_evidence(
            vector, direct_aspect_vectors, str(mem.get("text") or ""), query_aspects, aspect_scores,
        )
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
            **evidence,
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
    candidate_hits = _group_display_hits(hits)
    if selection_enabled:
        candidate_hits.sort(
            key=lambda hit: (
                float(hit.get("direct_similarity", hit.get("score", 0.0)) or 0.0),
                float(hit.get("score", 0.0) or 0.0),
            ),
            reverse=True,
        )
    candidate_hits = candidate_hits[:pool_k]

    # Vectorless memories can remain navigable by their own words without
    # re-embedding the same corpus rows on every read. Their lexical score is
    # labelled and kept separate from semantic similarity and reranking.
    missing_keyword_hits: List[Dict[str, Any]] = []
    if missing_keyword_mode and missing_indices:
        missing_keyword_hits = _keyword_fallback_hits(
            [filtered[index] for index in missing_indices],
            raw_query,
            len(filtered),
            selection_config if selection_enabled else None,
        )
        for hit in missing_keyword_hits:
            hit.update({
                "query_aspects": query_aspects,
                "query_echo_memory": False,
                "direct_similarity": 0.0,
                "aspect_scores": [],
            })

    # Candidate recall is permissive; abstain if the pool lacks query evidence.
    if selection_enabled and not _query_has_sufficient_evidence(
        [*candidate_hits, *missing_keyword_hits], selection_config
    ):
        return []

    step2_config = dict(settings.get("step2", {}) or {})
    if bool(step2_config.get("enabled", False)):
        candidate_hits = _step2_1_rerank(
            candidate_hits,
            raw_query,
            embedding_by_id,
            float(step2_config.get("alpha", 1.0)),
            step2_config,
        )

    candidate_hits = _group_display_hits([*candidate_hits, *missing_keyword_hits])

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
        return _hydrate_retrieval_hits(_group_display_hits(fallback_hits))
    return _hydrate_retrieval_hits(candidate_hits[:top_k])
