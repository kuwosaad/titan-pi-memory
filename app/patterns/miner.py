from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.graph.clusters import inspect_memory_clusters
from app.graph.cortex_analysis import analyze_memory_clusters
from app.graph.corpus_analysis import snapshot_for_memories
from app.storage.memories import SqliteMemoryRepository, get_memory_repository

from .planner import EvidencePacketPlan, PatternMiningPlanner
from .processing import PatternProcessingLedger
from .storage import resolve_pattern_db_path
from .store import PatternStore, PatternValidationError

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_./-]{2,}")
_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "because", "before", "both", "but",
    "can", "could", "for", "from", "has", "have", "into", "its", "memory", "memories",
    "need", "needs", "not", "only", "our", "should", "that", "the", "their", "then", "this",
    "those", "titan", "used", "uses", "using", "was", "when", "where", "with", "work",
    "would", "you", "your",
}
_ANTONYM_PAIRS = [
    ("accept", "reject"),
    ("add", "remove"),
    ("allow", "block"),
    ("enable", "disable"),
    ("keep", "remove"),
    ("prefer", "avoid"),
    ("use", "avoid"),
]


def build_evidence_packet(
    *,
    processor_version: str,
    processor_config_hash: str,
    db_path: Optional[Path] = None,
    batch_size: int = 100,
    context_limit: int = 300,
    session_id: Optional[str] = None,
    mode: str = "adaptive",
    packet_type: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    snapshot_cutoff: Optional[str] = None,
) -> Dict[str, Any]:
    """Prepare an evidence packet for an agent-authored pattern mining pass.

    This function deliberately does not create patterns and does not mark memories
    processed. It only gathers unprocessed memories plus related context so Pi (or
    another trusted agent) can author evidence-backed candidate pattern cards.
    """

    safe_batch_size = max(1, min(int(batch_size or 100), 500))
    safe_context_limit = max(0, min(int(context_limit or 300), 1000))
    resolved_db_path = Path(db_path) if db_path is not None else resolve_pattern_db_path()
    ledger = PatternProcessingLedger(resolved_db_path)
    repo = SqliteMemoryRepository(resolved_db_path) if db_path is not None else get_memory_repository()

    normalized_mode = str(mode or "adaptive")
    adaptive_mode = normalized_mode == "adaptive"
    unprocessed_limit = safe_batch_size * 4 if adaptive_mode or session_id else safe_batch_size
    unprocessed_ids = ledger.list_unprocessed_memory_ids(
        processor_version=processor_version,
        processor_config_hash=processor_config_hash,
        limit=unprocessed_limit,
        session_id=session_id,
        from_ts=from_ts,
        to_ts=to_ts,
        snapshot_cutoff=snapshot_cutoff,
    )
    unprocessed_total = ledger.count_unprocessed_memory_ids(
        processor_version=processor_version,
        processor_config_hash=processor_config_hash,
        session_id=session_id,
        from_ts=from_ts,
        to_ts=to_ts,
        snapshot_cutoff=snapshot_cutoff,
    )
    by_unprocessed = repo.query_by_ids(unprocessed_ids)
    ordered_unprocessed = [by_unprocessed[mid] for mid in unprocessed_ids if mid in by_unprocessed]
    if session_id:
        ordered_unprocessed = [mem for mem in ordered_unprocessed if mem.get("session_id") == session_id]

    all_memories = repo.load_all_memories()
    if snapshot_cutoff:
        cutoff = _parse_rfc3339(snapshot_cutoff)
        if cutoff is not None:
            all_memories = [
                mem
                for mem in all_memories
                if (memory_ts := _parse_rfc3339(mem.get("ts"))) is not None and memory_ts <= cutoff
            ]
    if session_id:
        all_memories = [mem for mem in all_memories if mem.get("session_id") == session_id]
    allowed_memory_ids = {str(mem.get("id")) for mem in all_memories if mem.get("id")}
    ordered_unprocessed = [
        mem for mem in ordered_unprocessed if str(mem.get("id")) in allowed_memory_ids
    ]
    # Build one immutable evidence snapshot for this planning pass. Cluster
    # inspection and Cortex analysis can reuse its normalized vectors and
    # content revision instead of rebuilding the graph independently.
    corpus = snapshot_for_memories(all_memories)

    if adaptive_mode:
        cluster_payload = inspect_memory_clusters(session_id=session_id, limit=0, detail_limit=50, corpus=corpus)
        cluster_analysis_payload = None
        if packet_type in (None, "bridge", "contradiction"):
            candidate_cluster_ids = _bridge_cluster_ids(cluster_payload, ordered_unprocessed, all_memories)
            if candidate_cluster_ids and (packet_type != "bridge" or len(candidate_cluster_ids) >= 2):
                cluster_analysis_payload = analyze_memory_clusters(
                    candidate_cluster_ids,
                    session_id=session_id,
                    limit=0,
                    detail_limit=25,
                    corpus=corpus,
                )
        plans = PatternMiningPlanner(
            unprocessed_memories=ordered_unprocessed,
            all_memories=all_memories,
            batch_size=safe_batch_size,
            context_limit=safe_context_limit,
            packet_type=packet_type,
            cluster_payload=cluster_payload,
            cluster_analysis_payload=cluster_analysis_payload,
        ).plan()
        selected = plans[0] if plans else EvidencePacketPlan(
            packet_id="chronological_fallback:empty",
            packet_type="chronological_fallback",
            seed_memory_ids=[],
            context_memory_ids=[],
            score=0.0,
            reasons=["No unprocessed memories were available."],
        )
        return build_packet_from_plan(
            selected,
            all_memories=all_memories,
            processor_version=processor_version,
            processor_config_hash=processor_config_hash,
            batch_size=safe_batch_size,
            context_limit=safe_context_limit,
            session_id=session_id,
            from_ts=from_ts,
            to_ts=to_ts,
            snapshot_cutoff=snapshot_cutoff,
            unprocessed_total=unprocessed_total,
            pattern_db_path=resolved_db_path,
        )

    batch_memories = ordered_unprocessed[:safe_batch_size]
    batch_ids = [str(mem.get("id")) for mem in batch_memories if mem.get("id")]
    old_memories = [mem for mem in all_memories if str(mem.get("id")) not in set(batch_ids)]
    related_context = _related_context(batch_memories, old_memories, limit=safe_context_limit)
    plan = EvidencePacketPlan(
        packet_id="chronological_fallback:legacy",
        packet_type="chronological_fallback",
        seed_memory_ids=batch_ids,
        context_memory_ids=[str(mem.get("id")) for mem, _score in related_context if mem.get("id")],
        score=0.35 if batch_ids else 0.0,
        reasons=["Used legacy chronological packet construction."],
    )
    return _build_packet_payload(
        plan=plan,
        batch_memories=batch_memories,
        related_context=related_context,
        processor_version=processor_version,
        processor_config_hash=processor_config_hash,
        batch_size=safe_batch_size,
        context_limit=safe_context_limit,
        session_id=session_id,
        from_ts=from_ts,
        to_ts=to_ts,
        snapshot_cutoff=snapshot_cutoff,
        unprocessed_total=unprocessed_total,
        pattern_context=_pattern_context(
            resolved_db_path,
            packet_memories=[*batch_memories, *[item[0] for item in related_context]],
            packet_type=plan.packet_type,
            snapshot_cutoff=snapshot_cutoff,
        ),
    )


def build_packet_from_plan(
    plan: EvidencePacketPlan,
    *,
    all_memories: Sequence[Dict[str, Any]],
    processor_version: str,
    processor_config_hash: str,
    batch_size: int,
    context_limit: int,
    session_id: Optional[str] = None,
    pattern_db_path: Optional[Path] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    snapshot_cutoff: Optional[str] = None,
    unprocessed_total: Optional[int] = None,
) -> Dict[str, Any]:
    by_id = {str(mem.get("id")): mem for mem in all_memories if mem.get("id")}
    batch_memories = [by_id[memory_id] for memory_id in plan.seed_memory_ids if memory_id in by_id]
    context_memories = [by_id[memory_id] for memory_id in plan.context_memory_ids if memory_id in by_id]
    related_context = [
        (mem, max((_memory_similarity(mem, seed) for seed in batch_memories), default=0.0))
        for mem in context_memories
    ]
    pattern_context = _pattern_context(
        pattern_db_path,
        packet_memories=[*batch_memories, *context_memories],
        packet_type=plan.packet_type,
        snapshot_cutoff=snapshot_cutoff,
    )
    return _build_packet_payload(
        plan=plan,
        batch_memories=batch_memories,
        related_context=related_context,
        processor_version=processor_version,
        processor_config_hash=processor_config_hash,
        batch_size=batch_size,
        context_limit=context_limit,
        session_id=session_id,
        from_ts=from_ts,
        to_ts=to_ts,
        snapshot_cutoff=snapshot_cutoff,
        unprocessed_total=unprocessed_total,
        pattern_context=pattern_context,
    )


def _build_packet_payload(
    *,
    plan: EvidencePacketPlan,
    batch_memories: Sequence[Dict[str, Any]],
    related_context: Sequence[Tuple[Dict[str, Any], float]],
    processor_version: str,
    processor_config_hash: str,
    batch_size: int,
    context_limit: int,
    session_id: Optional[str],
    pattern_context: Sequence[Dict[str, Any]],
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    snapshot_cutoff: Optional[str] = None,
    unprocessed_total: Optional[int] = None,
) -> Dict[str, Any]:
    batch_ids = [str(mem.get("id")) for mem in batch_memories if mem.get("id")]
    related_context_memories = [item[0] for item in related_context]

    packet_memories = batch_memories + related_context_memories
    relation_scores = {str(mem.get("id")): score for mem, score in related_context}
    clusters = _cluster_summaries(packet_memories)
    central = _central_memories(packet_memories, relation_scores=relation_scores)
    bridges = _bridge_memories(batch_memories, related_context_memories)
    tensions = _tensions(packet_memories)
    trigger_terms = _suggested_trigger_terms(packet_memories)

    scene_ids = sorted({str(mem.get("scene_id")) for mem in packet_memories if mem.get("scene_id")})
    stream_counts = Counter(str(mem.get("stream") or "rough") for mem in batch_memories)
    type_counts = Counter(str(mem.get("memory_kind") or mem.get("type") or "memory") for mem in batch_memories)

    graph_context = {
        "central_memories": central,
        "bridge_memories": bridges,
        "tensions": tensions,
        "subclusters": [],
    }
    source_cluster = plan.metadata.get("source_cluster") if plan.metadata else None
    if source_cluster:
        graph_context["source_cluster"] = source_cluster
    source_entity = plan.metadata.get("source_entity") if plan.metadata else None
    if source_entity:
        graph_context["source_entity"] = source_entity
    source_analysis = plan.metadata.get("source_analysis") if plan.metadata else None
    if source_analysis:
        graph_context["source_analysis"] = source_analysis
    source_tensions = plan.metadata.get("source_tensions") if plan.metadata else None
    if source_tensions:
        graph_context["source_tensions"] = source_tensions
    if pattern_context:
        graph_context["source_patterns"] = list(pattern_context)

    return {
        "processor_version": processor_version,
        "processor_config_hash": processor_config_hash,
        "batch_size": batch_size,
        "context_limit": context_limit,
        "session_id": session_id,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "snapshot_cutoff": snapshot_cutoff,
        "unprocessed_remaining": max(0, int(unprocessed_total or 0) - len(batch_ids)),
        "unprocessed_memory_ids": batch_ids,
        "related_old_memory_ids": [str(mem.get("id")) for mem in related_context_memories if mem.get("id")],
        "memories": {
            "unprocessed": [_serialize_memory(mem) for mem in batch_memories],
            "related_context": [
                {**_serialize_memory(mem), "relatedness": round(float(score), 4)}
                for mem, score in related_context
            ],
        },
        "cluster_summaries": clusters,
        "central_memories": central,
        "bridge_memories": bridges,
        "tensions": tensions,
        "scene_ids": scene_ids,
        "suggested_trigger_terms": trigger_terms,
        "suggested_kind": _suggest_kind(type_counts),
        "suggested_scope": "repo",
        "confidence_hints": _confidence_hints(batch_memories, related_context_memories, tensions),
        "processing_note": "This packet is read-only. Mark only seed/unprocessed memory ids processed after the agent workflow finishes; context memory ids are support evidence only.",
        "packet_id": plan.packet_id,
        "packet_type": plan.packet_type,
        "seed_memory_ids": list(plan.seed_memory_ids),
        "context_memory_ids": list(plan.context_memory_ids),
        "selection_reasons": list(plan.reasons),
        "packet_score": plan.score,
        "temporal_context": [_serialize_memory(mem) for mem in sorted(packet_memories, key=_timeline_key)],
        "semantic_context": clusters,
        "entity_context": [{"term": term} for term in _top_terms(packet_memories, limit=12)],
        "pattern_context": list(pattern_context),
        "graph_context": graph_context,
        "questions_for_agent": [
            "what repeats here?",
            "what should future agents do differently?",
            "what evidence supports it?",
            "where does it not apply?",
        ],
    }


def _timeline_key(memory: Dict[str, Any]) -> tuple[int, datetime, int, str]:
    timestamp = _parse_rfc3339(memory.get("ts"))
    turn = memory.get("turn")
    try:
        turn_value = int(turn)
    except (TypeError, ValueError):
        turn_value = 0
    return (
        0 if timestamp is None else 1,
        timestamp or datetime.min.replace(tzinfo=timezone.utc),
        turn_value,
        str(memory.get("id") or ""),
    )


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Legacy memory rows may contain a timezone-naive ISO timestamp;
        # storage has historically treated those values as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bridge_cluster_ids(
    cluster_payload: Dict[str, Any],
    unprocessed_memories: Sequence[Dict[str, Any]],
    all_memories: Sequence[Dict[str, Any]],
) -> list[int]:
    clusters = cluster_payload.get("clusters") if isinstance(cluster_payload, dict) else None
    if not isinstance(clusters, list):
        return []

    unprocessed_ids = {str(mem.get("id")) for mem in unprocessed_memories if mem.get("id")}
    known_ids = {str(mem.get("id")) for mem in all_memories if mem.get("id")}
    candidates: list[tuple[tuple[int, int, int, float, int], int]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict) or cluster.get("cluster_id") is None:
            continue
        try:
            cluster_id = int(cluster["cluster_id"])
        except (TypeError, ValueError):
            continue
        memory_ids = [str(memory_id) for memory_id in (cluster.get("memory_ids") or []) if str(memory_id) in known_ids]
        if not memory_ids:
            continue
        unprocessed_count = len([memory_id for memory_id in memory_ids if memory_id in unprocessed_ids])
        candidates.append(
            (
                (
                    1 if unprocessed_count else 0,
                    unprocessed_count,
                    int(cluster.get("connection_count") or 0),
                    float(cluster.get("avg_similarity") or 0.0),
                    len(memory_ids),
                ),
                cluster_id,
            )
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates or candidates[0][0][1] == 0:
        return []
    return [cluster_id for _score, cluster_id in candidates[:4]]


def _pattern_context(
    db_path: Optional[Path],
    *,
    packet_memories: Sequence[Dict[str, Any]],
    packet_type: str,
    limit: int = 8,
    snapshot_cutoff: Optional[str] = None,
) -> list[Dict[str, Any]]:
    if not db_path or not packet_memories:
        return []

    packet_terms = set(_top_terms(packet_memories, limit=40))
    if not packet_terms:
        return []

    try:
        store = PatternStore(db_path)
        pattern_query = {"created_before": snapshot_cutoff} if snapshot_cutoff else {}
        patterns = [
            *store.list_patterns(status="accepted", limit=100, **pattern_query),
            *store.list_patterns(status="candidate", limit=100, **pattern_query),
        ]
    except (OSError, sqlite3.Error, PatternValidationError):
        return []

    cutoff = _parse_rfc3339(snapshot_cutoff) if snapshot_cutoff else None
    if cutoff is not None:
        patterns = [
            pattern
            for pattern in patterns
            if (created_at := _parse_rfc3339(pattern.created_at)) is not None and created_at <= cutoff
        ]

    scored: list[tuple[float, Dict[str, Any]]] = []
    for pattern in patterns:
        pattern_terms = set(_tokens(" ".join([
            pattern.title,
            pattern.summary,
            pattern.recommended_behavior,
            pattern.applies_when,
            pattern.does_not_apply_when,
            " ".join(pattern.trigger_terms),
        ])))
        overlap = sorted(packet_terms & pattern_terms)
        trigger_overlap = sorted(packet_terms & set(_tokens(" ".join(pattern.trigger_terms))))
        if not overlap:
            continue
        relevance = len(overlap) + (0.5 * len(trigger_overlap)) + float(pattern.confidence)
        if packet_type == "contradiction":
            relevance += 0.5
        if relevance <= 0.0:
            continue
        scored.append(
            (
                relevance,
                {
                    "pattern_id": pattern.id,
                    "title": pattern.title,
                    "status": pattern.status,
                    "kind": pattern.kind,
                    "scope": pattern.scope,
                    "summary": _shorten(pattern.summary),
                    "recommended_behavior": _shorten(pattern.recommended_behavior),
                    "trigger_terms": list(pattern.trigger_terms)[:12],
                    "confidence": round(float(pattern.confidence), 4),
                    "match_terms": overlap[:12],
                    "relevance": round(float(relevance), 4),
                },
            )
        )

    scored.sort(key=lambda item: (-item[0], str(item[1]["status"]), str(item[1]["pattern_id"])))
    return [item for _score, item in scored[:limit]]


def _serialize_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": memory.get("id"),
        "text": _shorten(str(memory.get("text") or "")),
        "type": memory.get("type") or "memory",
        "memory_kind": memory.get("memory_kind"),
        "stream": memory.get("stream") or "rough",
        "session_id": memory.get("session_id"),
        "scene_id": memory.get("scene_id"),
        "ts": memory.get("ts"),
        "turn": memory.get("turn"),
        # Evidence must remain auditable when it crosses the agent boundary.
        "provenance": dict(memory.get("provenance") or {}),
        "source_event_ids": list(memory.get("source_event_ids") or []),
        "source_type": memory.get("source_type"),
        "source_reliability": memory.get("source_reliability"),
        "verification_status": memory.get("verification_status"),
        "fallback_generated": bool(memory.get("fallback_generated", False)),
        "speaker_focus": memory.get("speaker_focus"),
    }


def _shorten(text: str, limit: int = 260) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _tokens(text: str) -> List[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(str(text or "")):
        token = raw.lower().strip("`'\".,:;!?()[]{}<>")
        token = token.replace("’s", "").replace("'s", "")
        if len(token) < 3 or token in _STOPWORDS or token.isdigit():
            continue
        tokens.append(token)
    return tokens


def _token_set(memory: Dict[str, Any]) -> set[str]:
    return set(_tokens(str(memory.get("text") or "")))


def _vector(memory: Dict[str, Any]) -> Optional[np.ndarray]:
    embedding = memory.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        return None
    try:
        vec = np.asarray(embedding, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if vec.size == 0 or not np.all(np.isfinite(vec)):
        return None
    return vec


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size != b.size:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    value = float(np.dot(a, b) / denom)
    return value if math.isfinite(value) else 0.0


def _memory_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    a_tokens = _token_set(a)
    b_tokens = _token_set(b)
    token_score = 0.0
    if a_tokens and b_tokens:
        token_score = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    a_vec = _vector(a)
    b_vec = _vector(b)
    vector_score = max(0.0, _cosine(a_vec, b_vec)) if a_vec is not None and b_vec is not None else 0.0
    return max(token_score, (0.7 * vector_score) + (0.3 * token_score))


def _related_context(
    batch_memories: Sequence[Dict[str, Any]],
    old_memories: Sequence[Dict[str, Any]],
    *,
    limit: int,
) -> List[Tuple[Dict[str, Any], float]]:
    if not batch_memories or limit <= 0:
        return []
    scored: list[tuple[Dict[str, Any], float]] = []
    for old in old_memories:
        score = max((_memory_similarity(old, new) for new in batch_memories), default=0.0)
        if score <= 0.05:
            continue
        scored.append((old, score))
    scored.sort(key=lambda item: (-item[1], *_timeline_key(item[0])))
    return scored[:limit]


def _cluster_summaries(memories: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not memories:
        return []
    parent = {idx: idx for idx in range(len(memories))}

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(memories)):
        for j in range(i + 1, len(memories)):
            if _memory_similarity(memories[i], memories[j]) >= 0.32:
                union(i, j)

    grouped: dict[int, list[Dict[str, Any]]] = defaultdict(list)
    for idx, mem in enumerate(memories):
        grouped[find(idx)].append(mem)

    clusters: list[Dict[str, Any]] = []
    for cluster_idx, cluster_memories in enumerate(sorted(grouped.values(), key=lambda vals: -len(vals)), start=1):
        keywords = _top_terms(cluster_memories, limit=8)
        types = Counter(str(mem.get("memory_kind") or mem.get("type") or "memory") for mem in cluster_memories)
        scene_ids = {str(mem.get("scene_id")) for mem in cluster_memories if mem.get("scene_id")}
        clusters.append(
            {
                "cluster_id": cluster_idx,
                "topic": " / ".join(keywords[:4]) if keywords else "miscellaneous memories",
                "keywords": keywords,
                "memory_count": len(cluster_memories),
                "scene_count": len(scene_ids),
                "types": dict(types.most_common()),
                "memory_ids": [str(mem.get("id")) for mem in cluster_memories if mem.get("id")],
                "examples": [_serialize_memory(mem) for mem in cluster_memories[:5]],
            }
        )
    return clusters


def _top_terms(memories: Sequence[Dict[str, Any]], *, limit: int) -> List[str]:
    counts: Counter[str] = Counter()
    for mem in memories:
        counts.update(_tokens(str(mem.get("text") or "")))
    return [token for token, _count in counts.most_common(limit)]


def _central_memories(memories: Sequence[Dict[str, Any]], *, relation_scores: Dict[str, float], limit: int = 8) -> List[Dict[str, Any]]:
    scored: list[tuple[Dict[str, Any], float]] = []
    for mem in memories:
        similarity_sum = sum(_memory_similarity(mem, other) for other in memories if other is not mem)
        score = similarity_sum + relation_scores.get(str(mem.get("id")), 1.0)
        scored.append((mem, score))
    scored.sort(key=lambda item: (-item[1], str(item[0].get("id") or "")))
    return [{**_serialize_memory(mem), "centrality": round(float(score), 4)} for mem, score in scored[:limit]]


def _bridge_memories(batch_memories: Sequence[Dict[str, Any]], context_memories: Sequence[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    bridges: list[tuple[Dict[str, Any], list[str], float]] = []
    for old in context_memories:
        linked: list[tuple[str, float]] = []
        for new in batch_memories:
            score = _memory_similarity(old, new)
            if score > 0.18 and new.get("id"):
                linked.append((str(new["id"]), score))
        if len(linked) >= 2:
            bridges.append((old, [mid for mid, _score in linked], sum(score for _mid, score in linked) / len(linked)))
    bridges.sort(key=lambda item: (-item[2], str(item[0].get("id") or "")))
    return [
        {**_serialize_memory(mem), "bridges_unprocessed_memory_ids": linked_ids, "bridge_score": round(float(score), 4)}
        for mem, linked_ids, score in bridges[:limit]
    ]


def _tensions(memories: Sequence[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    result: list[Dict[str, Any]] = []
    token_sets = [(mem, _token_set(mem)) for mem in memories]
    for i, (left, left_tokens) in enumerate(token_sets):
        for right, right_tokens in token_sets[i + 1 :]:
            shared = sorted((left_tokens & right_tokens) - {word for pair in _ANTONYM_PAIRS for word in pair})
            if not shared:
                continue
            for a, b in _ANTONYM_PAIRS:
                if (a in left_tokens and b in right_tokens) or (b in left_tokens and a in right_tokens):
                    result.append(
                        {
                            "left_memory_id": left.get("id"),
                            "right_memory_id": right.get("id"),
                            "signal": f"{a} vs {b}",
                            "shared_terms": shared[:6],
                            "note": "Lexical tension signal only; inspect scenes before treating it as a contradiction.",
                        }
                    )
                    break
            if len(result) >= limit:
                return result
    return result


def _suggested_trigger_terms(memories: Sequence[Dict[str, Any]], limit: int = 12) -> List[str]:
    return _top_terms(memories, limit=limit)


def _suggest_kind(type_counts: Counter[str]) -> str:
    if not type_counts:
        return "other"
    joined = " ".join(type_counts.keys()).lower()
    if "issue" in joined or "failure" in joined or "bug" in joined:
        return "failure"
    if "workflow" in joined or "task" in joined:
        return "workflow"
    if "preference" in joined:
        return "preference"
    if "decision" in joined:
        return "codebase"
    return "other"


def _confidence_hints(
    batch_memories: Sequence[Dict[str, Any]],
    context_memories: Sequence[Dict[str, Any]],
    tensions: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    support_memories = list(batch_memories) + list(context_memories)
    scene_count = len({str(mem.get("scene_id")) for mem in support_memories if mem.get("scene_id")})
    session_count = len({str(mem.get("session_id")) for mem in support_memories if mem.get("session_id")})
    learning_count = sum(1 for mem in support_memories if str(mem.get("stream") or "") == "learnings")
    evidence_count_score = min(1.0, len(support_memories) / 6.0)
    scene_diversity_score = min(1.0, scene_count / 3.0)
    learning_ratio_score = learning_count / len(support_memories) if support_memories else 0.0
    contradiction_penalty = min(1.0, len(tensions) / 3.0)
    rough_confidence = max(
        0.0,
        min(
            1.0,
            0.40 * evidence_count_score
            + 0.30 * scene_diversity_score
            + 0.20 * learning_ratio_score
            + 0.10 * min(1.0, session_count / 3.0)
            - 0.20 * contradiction_penalty,
        ),
    )
    return {
        "evidence_count": len(support_memories),
        "scene_count": scene_count,
        "session_count": session_count,
        "learning_ratio": round(float(learning_ratio_score), 4),
        "tension_count": len(tensions),
        "rough_confidence": round(float(rough_confidence), 4),
    }
