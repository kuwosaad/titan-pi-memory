from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx
import numpy as np
from networkx.algorithms.community import greedy_modularity_communities

from app.embedding.embedder import embed
from app.graph.builder import DEFAULT_GRAPH_MEMORY_LIMIT, load_memories
from app.graph.clusters import inspect_memory_clusters
from app.graph.similarity import cosine_similarity
from app.retrieval_pipeline.config import load_settings


_TOKEN_RE = re.compile(r"[a-z0-9_./-]+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is",
    "it", "of", "on", "or", "should", "that", "the", "to", "use", "we", "what", "when",
    "with", "this", "these", "those", "then", "than", "there", "their", "them", "they", "your",
    "you", "about", "into", "over", "under", "will", "would", "could", "also", "memory", "memories",
}


def _memory_dict(memory: Any) -> Dict[str, Any]:
    return memory.model_dump() if hasattr(memory, "model_dump") else dict(memory)


def _shorten(text: str, limit: int = 220) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(str(text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS and not token.isdigit()
    }


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _normalize_vectors(vectors: Sequence[np.ndarray]) -> np.ndarray:
    matrix = np.vstack([np.asarray(vec, dtype=np.float32).reshape(1, -1) for vec in vectors])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _safe_cosine(a: np.ndarray, b: np.ndarray) -> float:
    try:
        value = float(cosine_similarity(a, b))
    except Exception:
        return 0.0
    return value if math.isfinite(value) else 0.0


def _serialize_memory(memory: Dict[str, Any], cluster_id: Optional[int] = None, score: Optional[float] = None) -> Dict[str, Any]:
    payload = {
        "id": memory.get("id"),
        "text": _shorten(str(memory.get("text") or "")),
        "type": memory.get("type") or "memory",
        "stream": memory.get("stream") or "rough",
        "session_id": memory.get("session_id"),
        "scene_id": memory.get("scene_id"),
        "ts": memory.get("ts"),
        "turn": memory.get("turn"),
    }
    if cluster_id is not None:
        payload["cluster_id"] = cluster_id
    if score is not None:
        payload["score"] = round(float(score), 4)
    return payload


def _parse_cluster_ids(cluster_ids: Iterable[int] | str | None) -> List[int]:
    if cluster_ids is None:
        return []
    if isinstance(cluster_ids, str):
        raw_items = re.split(r"[\s,]+", cluster_ids.strip())
    else:
        raw_items = [str(item) for item in cluster_ids]
    parsed: List[int] = []
    for item in raw_items:
        if not item:
            continue
        try:
            value = int(item)
        except ValueError:
            continue
        if value not in parsed:
            parsed.append(value)
    return parsed


def _query_scores(question: Optional[str], vectors: Sequence[np.ndarray]) -> Tuple[List[float], Optional[str]]:
    if not question or not question.strip():
        return [1.0 for _ in vectors], None
    try:
        query_vec = np.asarray(embed([question.strip()])[0], dtype=np.float32)
    except Exception as exc:  # pragma: no cover - depends on local model config
        return [1.0 for _ in vectors], f"question embedding unavailable; used structural scoring only: {exc}"
    return [max(0.0, _safe_cosine(query_vec, vec)) for vec in vectors], None


def _make_summary(
    cluster_ids: Sequence[int],
    memory_count: int,
    bridge_pairs: Sequence[Dict[str, Any]],
    tensions: Sequence[Dict[str, Any]],
    subclusters: Sequence[Dict[str, Any]],
) -> str:
    if memory_count == 0:
        return "No embedded memories were available for the selected clusters."
    parts = [
        f"Analyzed {memory_count} memories from cluster(s) {', '.join(str(cid) for cid in cluster_ids)}.",
        f"Found {len(bridge_pairs)} cross-cluster bridge pair(s), {len(tensions)} possible tension(s), and {len(subclusters)} subcluster(s).",
    ]
    if bridge_pairs:
        top = bridge_pairs[0]
        parts.append(
            f"Strongest bridge: cluster {top.get('source_cluster_id')} ↔ {top.get('target_cluster_id')} "
            f"with similarity {top.get('similarity')}."
        )
    if tensions:
        parts.append("Tensions are lexical/semantic signals, not final judgments; inspect the original scenes before treating them as true contradictions.")
    return " ".join(parts)


def analyze_memory_clusters(
    cluster_ids: Iterable[int] | str,
    session_id: Optional[str] = None,
    limit: int = DEFAULT_GRAPH_MEMORY_LIMIT,
    question: Optional[str] = None,
    detail_limit: int = 8,
) -> Dict[str, Any]:
    """Apply a Cortex/step-2.1-style structural pass over one or more Titan clusters.

    This is intentionally deterministic and local: it uses existing Titan memory embeddings,
    builds a weighted memory graph, then surfaces bridges, central memories, possible
    tensions, and subcommunities for agent interpretation.
    """
    requested_cluster_ids = _parse_cluster_ids(cluster_ids)
    if not requested_cluster_ids:
        return {"error": "at least one cluster id is required"}

    safe_limit = max(1, min(int(limit or DEFAULT_GRAPH_MEMORY_LIMIT), 1000))
    detail_limit = max(1, min(int(detail_limit or 8), 25))

    cluster_payload = inspect_memory_clusters(session_id=session_id, limit=safe_limit, detail_limit=50)
    clusters = cluster_payload.get("clusters") or []
    by_cluster_id = {int(cluster.get("cluster_id")): cluster for cluster in clusters if cluster.get("cluster_id") is not None}
    missing = [cid for cid in requested_cluster_ids if cid not in by_cluster_id]
    if missing:
        return {
            "error": f"cluster id(s) not found: {', '.join(str(cid) for cid in missing)}",
            "available_cluster_ids": sorted(by_cluster_id.keys()),
        }

    raw_memories = [_memory_dict(mem) for mem in load_memories(session_id=session_id, limit=safe_limit)]
    memory_by_id = {str(mem.get("id")): mem for mem in raw_memories if mem.get("id")}

    selected_memories: List[Dict[str, Any]] = []
    selected_vectors: List[np.ndarray] = []
    origin_by_id: Dict[str, int] = {}
    seen_ids: set[str] = set()
    skipped_missing_embeddings = 0

    for cluster_id in requested_cluster_ids:
        for memory_id in by_cluster_id[cluster_id].get("memory_ids") or []:
            memory = memory_by_id.get(str(memory_id))
            if not memory or str(memory_id) in seen_ids:
                continue
            vec = _vector(memory)
            if vec is None:
                skipped_missing_embeddings += 1
                continue
            seen_ids.add(str(memory_id))
            origin_by_id[str(memory_id)] = cluster_id
            selected_memories.append(memory)
            selected_vectors.append(vec)

    if not selected_memories:
        return {
            "scope": "session" if session_id else "global",
            "session_id": session_id,
            "cluster_ids": requested_cluster_ids,
            "memory_count": 0,
            "skipped_missing_embeddings": skipped_missing_embeddings,
            "bridges": [],
            "bridge_memories": [],
            "central_memories": [],
            "tensions": [],
            "subclusters": [],
            "summary": "No embedded memories were available for the selected clusters.",
        }

    settings = load_settings()
    step2_config = settings.get("step2", {}) or {}
    sim_floor = float(step2_config.get("sim_floor", 0.45) or 0.45)
    bridge_floor = max(0.25, sim_floor - 0.1)
    contradiction_threshold = float(step2_config.get("contradiction_sim_threshold", 0.7) or 0.7)
    antonym_pairs = step2_config.get("contradiction_antonyms", []) or []

    normalized = _normalize_vectors(selected_vectors)
    similarity = normalized @ normalized.T
    np.fill_diagonal(similarity, 0.0)
    query_scores, query_warning = _query_scores(question, selected_vectors)

    graph = nx.Graph()
    for idx, memory in enumerate(selected_memories):
        memory_id = str(memory.get("id"))
        graph.add_node(idx, memory_id=memory_id, cluster_id=origin_by_id.get(memory_id))

    for i in range(len(selected_memories)):
        for j in range(i + 1, len(selected_memories)):
            sim = float(similarity[i][j])
            if sim < sim_floor or not math.isfinite(sim):
                continue
            relevance = (query_scores[i] + query_scores[j]) / 2.0
            weight = sim * (0.35 + 0.65 * relevance)
            graph.add_edge(i, j, weight=weight, similarity=sim)

    if graph.number_of_edges() > 0:
        centrality = nx.pagerank(graph, weight="weight")
    else:
        total = float(sum(query_scores)) or 1.0
        centrality = {idx: query_scores[idx] / total for idx in range(len(selected_memories))}

    bridge_pairs: List[Dict[str, Any]] = []
    bridge_score_by_idx: Dict[int, float] = defaultdict(float)
    for i in range(len(selected_memories)):
        cluster_i = origin_by_id.get(str(selected_memories[i].get("id")))
        for j in range(i + 1, len(selected_memories)):
            cluster_j = origin_by_id.get(str(selected_memories[j].get("id")))
            if cluster_i == cluster_j:
                continue
            sim = float(similarity[i][j])
            if sim < bridge_floor or not math.isfinite(sim):
                continue
            bridge_score = sim * (centrality.get(i, 0.0) + centrality.get(j, 0.0)) / 2.0
            shared_terms = sorted(_tokens(str(selected_memories[i].get("text") or "")) & _tokens(str(selected_memories[j].get("text") or "")))[:8]
            bridge_score_by_idx[i] += bridge_score
            bridge_score_by_idx[j] += bridge_score
            bridge_pairs.append(
                {
                    "source_cluster_id": cluster_i,
                    "target_cluster_id": cluster_j,
                    "similarity": round(sim, 4),
                    "bridge_score": round(bridge_score, 6),
                    "shared_terms": shared_terms,
                    "source_memory": _serialize_memory(selected_memories[i], cluster_i),
                    "target_memory": _serialize_memory(selected_memories[j], cluster_j),
                }
            )
    bridge_pairs.sort(key=lambda item: (float(item["bridge_score"]), float(item["similarity"])), reverse=True)
    bridge_pairs = bridge_pairs[:detail_limit]

    bridge_memories = [
        _serialize_memory(selected_memories[idx], origin_by_id.get(str(selected_memories[idx].get("id"))), score=score)
        for idx, score in sorted(bridge_score_by_idx.items(), key=lambda item: item[1], reverse=True)[:detail_limit]
    ]

    central_memories = [
        _serialize_memory(selected_memories[idx], origin_by_id.get(str(selected_memories[idx].get("id"))), score=score)
        for idx, score in sorted(centrality.items(), key=lambda item: item[1], reverse=True)[:detail_limit]
    ]

    tensions: List[Dict[str, Any]] = []
    raw_tokens = [_tokens(str(mem.get("text") or "")) for mem in selected_memories]
    timestamps = [_parse_ts(mem.get("ts")) for mem in selected_memories]
    for i in range(len(selected_memories)):
        for j in range(i + 1, len(selected_memories)):
            sim = float(similarity[i][j])
            if sim < contradiction_threshold or not math.isfinite(sim):
                continue
            found_pair: Optional[Tuple[str, str]] = None
            for a, b in antonym_pairs:
                a = str(a).lower()
                b = str(b).lower()
                if (a in raw_tokens[i] and b in raw_tokens[j]) or (b in raw_tokens[i] and a in raw_tokens[j]):
                    found_pair = (a, b)
                    break
            if not found_pair:
                continue
            shared = sorted((raw_tokens[i] & raw_tokens[j]) - set(found_pair))[:8]
            if not shared:
                continue
            ts_i = timestamps[i]
            ts_j = timestamps[j]
            if ts_i and ts_j and ts_i != ts_j:
                older_idx, newer_idx = (i, j) if ts_i < ts_j else (j, i)
            else:
                older_idx, newer_idx = i, j
            tensions.append(
                {
                    "similarity": round(sim, 4),
                    "signal": f"possible shift around '{found_pair[0]}' vs '{found_pair[1]}'",
                    "shared_terms": shared,
                    "older_memory": _serialize_memory(
                        selected_memories[older_idx], origin_by_id.get(str(selected_memories[older_idx].get("id")))
                    ),
                    "newer_memory": _serialize_memory(
                        selected_memories[newer_idx], origin_by_id.get(str(selected_memories[newer_idx].get("id")))
                    ),
                }
            )
    tensions.sort(key=lambda item: float(item["similarity"]), reverse=True)
    tensions = tensions[:detail_limit]

    if graph.number_of_edges() > 0:
        communities = list(greedy_modularity_communities(graph, weight="weight"))
    else:
        communities = [frozenset([idx]) for idx in range(len(selected_memories))]

    subclusters: List[Dict[str, Any]] = []
    for subcluster_id, community in enumerate(communities, start=1):
        indices = sorted(int(idx) for idx in community)
        cluster_counts = Counter(origin_by_id.get(str(selected_memories[idx].get("id"))) for idx in indices)
        token_counts: Counter[str] = Counter()
        for idx in indices:
            token_counts.update(_tokens(str(selected_memories[idx].get("text") or "")))
        representative = sorted(indices, key=lambda idx: centrality.get(idx, 0.0), reverse=True)[:3]
        subclusters.append(
            {
                "subcluster_id": subcluster_id,
                "memory_count": len(indices),
                "source_clusters": {str(k): v for k, v in cluster_counts.items() if k is not None},
                "keywords": [token for token, _count in token_counts.most_common(8)],
                "representative_memories": [
                    _serialize_memory(selected_memories[idx], origin_by_id.get(str(selected_memories[idx].get("id"))), score=centrality.get(idx, 0.0))
                    for idx in representative
                ],
            }
        )
    subclusters.sort(key=lambda item: int(item["memory_count"]), reverse=True)
    subclusters = subclusters[:detail_limit]

    summary = _make_summary(requested_cluster_ids, len(selected_memories), bridge_pairs, tensions, subclusters)

    return {
        "scope": "session" if session_id else "global",
        "session_id": session_id,
        "cluster_ids": requested_cluster_ids,
        "question": question,
        "warnings": [query_warning] if query_warning else [],
        "memory_count": len(selected_memories),
        "skipped_missing_embeddings": skipped_missing_embeddings,
        "edge_count": graph.number_of_edges(),
        "sim_floor": sim_floor,
        "bridge_floor": bridge_floor,
        "bridges": bridge_pairs,
        "bridge_memories": bridge_memories,
        "central_memories": central_memories,
        "tensions": tensions,
        "subclusters": subclusters,
        "summary": summary,
    }
