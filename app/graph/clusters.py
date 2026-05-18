from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx
import numpy as np
from networkx.algorithms.community import greedy_modularity_communities

from app.graph.builder import DEFAULT_GRAPH_MEMORY_LIMIT, load_memories, load_visual_config


MIN_SIMILARITY = 0.35
DEFAULT_DETAIL_LIMIT = 12
_MAX_TOPIC_TERMS = 4
_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}

_STOPWORDS = {
    "about", "above", "after", "again", "against", "being", "because", "before", "between",
    "both", "cannot", "could", "during", "each", "from", "have", "having", "into", "itself",
    "just", "more", "most", "must", "only", "other", "over", "same", "should", "than", "that",
    "their", "there", "these", "this", "those", "through", "under", "using", "very", "when",
    "where", "which", "while", "with", "within", "without", "would", "your", "will", "were",
    "been", "they", "them", "then", "also", "such", "like", "make", "made", "makes", "needs",
    "need", "used", "uses", "use", "currently", "current", "previous", "recent", "memory",
    "memories", "system", "project", "package", "extension", "updated", "asked", "karu", "titan",
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_./-]{2,}")


def _memory_dict(memory: Any) -> Dict[str, Any]:
    return memory.model_dump() if hasattr(memory, "model_dump") else dict(memory)


def _clean_token(token: str) -> str:
    token = token.lower().strip("`'\".,:;!?()[]{}<>")
    token = token.replace("’s", "").replace("'s", "")
    return token


def _tokens(text: str) -> List[str]:
    result: List[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = _clean_token(raw)
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if token.isdigit():
            continue
        result.append(token)
    return result


def _document_frequencies(memories: Sequence[Dict[str, Any]]) -> Counter[str]:
    df: Counter[str] = Counter()
    for mem in memories:
        df.update(set(_tokens(str(mem.get("text") or ""))))
    return df


def _build_similarity_edges(vectors: Sequence[np.ndarray], top_k: int, min_sim: float) -> List[Tuple[int, int, float]]:
    if len(vectors) < 2:
        return []

    matrix = np.vstack([np.asarray(vec, dtype=np.float32).reshape(1, -1) for vec in vectors])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = matrix / norms
    sims = normalized @ normalized.T
    np.fill_diagonal(sims, -np.inf)

    top_k = max(1, min(int(top_k), len(vectors) - 1))
    best: dict[tuple[int, int], float] = {}
    for i in range(len(vectors)):
        row = sims[i]
        if top_k >= len(row):
            candidates = np.argsort(row)[::-1]
        else:
            candidates = np.argpartition(row, -top_k)[-top_k:]
            candidates = candidates[np.argsort(row[candidates])[::-1]]
        for j in candidates:
            weight = float(row[j])
            if weight < min_sim or not math.isfinite(weight):
                continue
            a, b = sorted((int(i), int(j)))
            best[(a, b)] = max(best.get((a, b), 0.0), weight)

    return [(a, b, w) for (a, b), w in best.items()]


def _cluster_keywords(
    cluster_memories: Sequence[Dict[str, Any]],
    all_count: int,
    doc_freq: Counter[str],
) -> List[str]:
    counts: Counter[str] = Counter()
    for mem in cluster_memories:
        counts.update(_tokens(str(mem.get("text") or "")))

    scored: List[Tuple[str, float]] = []
    for token, count in counts.items():
        df = max(1, doc_freq.get(token, 1))
        idf = math.log((all_count + 1) / df)
        shape_bonus = 1.25 if any(ch in token for ch in "-_/.") else 1.0
        scored.append((token, float(count) * idf * shape_bonus))

    scored.sort(key=lambda item: (-item[1], item[0]))
    return [token for token, _score in scored[:10]]


def _topic_from_keywords(keywords: Sequence[str], types: Counter[str]) -> str:
    if keywords:
        return " / ".join(keywords[:_MAX_TOPIC_TERMS])
    if types:
        return " / ".join(kind for kind, _count in types.most_common(3))
    return "miscellaneous memories"


def _shorten(text: str, limit: int = 220) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _cache_key(session_id: Optional[str], limit: int, memories: Sequence[Dict[str, Any]]) -> tuple[Any, ...]:
    latest_ts = max((str(mem.get("ts") or "") for mem in memories), default="")
    latest_id = memories[0].get("id") if memories else ""
    return (session_id or "", int(limit), len(memories), latest_ts, latest_id)


def inspect_memory_clusters(
    session_id: Optional[str] = None,
    limit: int = DEFAULT_GRAPH_MEMORY_LIMIT,
    cluster_id: Optional[int] = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
) -> Dict[str, Any]:
    """Return fast, deterministic topic summaries for the graph communities."""
    safe_limit = max(1, min(int(limit or DEFAULT_GRAPH_MEMORY_LIMIT), 1000))
    detail_limit = max(1, min(int(detail_limit or DEFAULT_DETAIL_LIMIT), 50))
    raw_memories = [_memory_dict(mem) for mem in load_memories(session_id=session_id, limit=safe_limit)]

    key = _cache_key(session_id, safe_limit, raw_memories)
    if key in _CACHE:
        payload = _CACHE[key]
    else:
        indexed_memories: List[Dict[str, Any]] = []
        vectors: List[np.ndarray] = []
        skipped_missing_embeddings = 0
        for mem in raw_memories:
            embedding = mem.get("embedding")
            if isinstance(embedding, list) and embedding:
                indexed_memories.append(mem)
                vectors.append(np.asarray(embedding, dtype=np.float32))
            else:
                skipped_missing_embeddings += 1

        config = load_visual_config()
        top_k = int(config.get("physics", {}).get("forces", {}).get("link", {}).get("iterations", 2) or 2)
        edges = _build_similarity_edges(vectors, top_k=top_k, min_sim=MIN_SIMILARITY)

        graph = nx.Graph()
        graph.add_nodes_from(range(len(indexed_memories)))
        for a, b, weight in edges:
            graph.add_edge(a, b, weight=weight)

        if graph.number_of_edges() > 0:
            communities = list(greedy_modularity_communities(graph))
        else:
            communities = [frozenset([idx]) for idx in range(len(indexed_memories))]

        doc_freq = _document_frequencies(indexed_memories)
        cluster_records: List[Dict[str, Any]] = []
        for idx, community in enumerate(communities, start=1):
            member_indices = sorted(int(node) for node in community)
            cluster_memories = [indexed_memories[node] for node in member_indices]
            subgraph = graph.subgraph(member_indices)
            degrees = dict(subgraph.degree())
            representative_indices = sorted(
                member_indices,
                key=lambda node: (-degrees.get(node, 0), str(indexed_memories[node].get("ts") or "")),
            )[:detail_limit]
            weights = [float(data.get("weight", 0.0)) for _u, _v, data in subgraph.edges(data=True)]
            types: Counter[str] = Counter(str(mem.get("type") or "memory") for mem in cluster_memories)
            streams: Counter[str] = Counter(str(mem.get("stream") or "rough") for mem in cluster_memories)
            sessions = {str(mem.get("session_id") or "") for mem in cluster_memories if mem.get("session_id")}
            keywords = _cluster_keywords(cluster_memories, len(indexed_memories), doc_freq)
            topic = _topic_from_keywords(keywords, types)
            examples = [
                {
                    "id": mem.get("id"),
                    "text": _shorten(str(mem.get("text") or "")),
                    "type": mem.get("type") or "memory",
                    "stream": mem.get("stream") or "rough",
                    "session_id": mem.get("session_id"),
                    "scene_id": mem.get("scene_id"),
                    "ts": mem.get("ts"),
                    "turn": mem.get("turn"),
                }
                for mem in (indexed_memories[node] for node in representative_indices)
            ]

            cluster_records.append(
                {
                    "cluster_id": idx,
                    "topic": topic,
                    "keywords": keywords,
                    "memory_count": len(member_indices),
                    "connection_count": subgraph.number_of_edges(),
                    "avg_similarity": round(sum(weights) / len(weights), 3) if weights else 0.0,
                    "types": dict(types.most_common()),
                    "streams": dict(streams.most_common()),
                    "session_count": len(sessions),
                    "examples": examples,
                    "memory_ids": [indexed_memories[node].get("id") for node in member_indices],
                }
            )

        cluster_records.sort(key=lambda rec: (-int(rec["memory_count"]), int(rec["cluster_id"])))
        payload = {
            "scope": "session" if session_id else "global",
            "session_id": session_id,
            "memory_count": len(indexed_memories),
            "raw_memory_count": len(raw_memories),
            "skipped_missing_embeddings": skipped_missing_embeddings,
            "connection_count": len(edges),
            "cluster_count": len(cluster_records),
            "clusters": cluster_records,
        }
        _CACHE.clear()
        _CACHE[key] = payload

    if cluster_id is not None:
        match = next((cluster for cluster in payload["clusters"] if int(cluster["cluster_id"]) == int(cluster_id)), None)
        if not match:
            return {**payload, "error": f"cluster {cluster_id} not found"}
        return {**payload, "selected_cluster": match}

    return payload
