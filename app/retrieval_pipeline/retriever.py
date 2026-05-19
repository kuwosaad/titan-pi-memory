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

LOGGER = logging.getLogger(__name__)

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
    return {token for token in _tokenize(text) if token not in STOPWORDS and len(token) > 2}


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
) -> List[Dict[str, Any]]:
    query_terms = set(_tokenize(query))
    if not query_terms:
        return []

    hits = []
    for mem in memories:
        text = str(mem.get("text") or "")
        text_terms = set(_tokenize(text))
        overlap = query_terms & text_terms
        if not overlap:
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
    top_k = top_k or settings.get("retrieval_top_k", 8)
    rerank = _resolve_rerank_config(settings, int(top_k))
    min_similarity = min_similarity if min_similarity is not None else settings.get("retrieval_min_similarity", 0.25)

    reliability_config = settings.get("retrieval", {})
    min_reliability = min_reliability if min_reliability is not None else reliability_config.get("min_reliability", 0.4)

    recency_days = settings.get("retrieval_recency_days")
    session_bias = settings.get("retrieval_session_bias", True)

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
    filtered = query_memory_candidates(filters)
    filtered = apply_hidden_metadata_filter(filtered)
    filtered = _dedupe_prefer_latest(filtered)

    if not filtered or not query.strip():
        return []

    from .router import route_intent
    resolved_intent = intent if intent else route_intent(query.lower().strip())
    conditioned_query = _condition_query(query.strip(), resolved_intent, step1_config)

    try:
        query_vector = embed([conditioned_query])[0]
    except Exception as exc:
        LOGGER.warning("Embedding backend unavailable for query embedding; falling back to keyword retrieval: %s", exc)
        return _keyword_fallback_hits(filtered, query, int(top_k))

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

    def _main_sort_key(item: Dict[str, Any]) -> tuple[float, datetime]:
        mem = item.get("memory", {})
        ts_val = mem.get("ts") if isinstance(mem, dict) else getattr(mem, "ts", None)
        return (item["score"], parse_timestamp(ts_val) or datetime.min.replace(tzinfo=timezone.utc))

    hits.sort(key=_main_sort_key, reverse=True)

    candidate_hits = hits
    if _step1_enabled(step1_config, "adaptive_pool_k_enabled") and rerank["enabled"]:
        base_k = int(step1_config.get("adaptive_pool_k_base", 34))
        max_k = int(step1_config.get("adaptive_pool_k_max", 96))
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
            candidate_hits = hits[:pool_k]

    if rerank["enabled"]:
        if rerank.get("use_step2_1"):
            candidate_hits = _step2_1_rerank(candidate_hits, query, embedding_by_id, rerank["alpha"],
                                             rerank["step2_config"], lnn_config=lnn_config, filters=filters,
                                             query_vector=query_vector)
        else:
            candidate_hits = _cross_memory_rerank(candidate_hits, query, embedding_by_id, rerank["alpha"])

    if not candidate_hits and any(vector is None for vector in vectors):
        fallback_hits = _keyword_fallback_hits(filtered, query, int(top_k))
        if min_reliability and min_reliability > 0:
            fallback_hits = [h for h in fallback_hits if float(h.get("memory", {}).get("source_reliability", 0)) >= min_reliability]
        return fallback_hits
    return candidate_hits[:top_k]
