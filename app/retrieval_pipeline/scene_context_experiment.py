"""Pure, opt-in scene-context benchmark; never called by normal retrieval.

Admission is supplied by the caller and does not establish answerability. The
caller must apply the original source/session/date/type/stream filters to BOTH
inputs before calling. This module performs no storage, provider, or embedding
calls. It inspects one direct scene hop only; evidence_refs remain pointers,
not additional scene traversal roots. Similarity measures association, not
entailment, causation, correctness, or completeness.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any, Mapping, Sequence

Identity = tuple[str, str]
Vector = Sequence[float]
_MAX_SCENES = 3
_MAX_SIBLINGS = 2
_MIN_QUERY_SIMILARITY = 0.25
_MIN_SEED_SIMILARITY = 0.25
_MAX_BONUS = 0.1
_PUBLIC_FIELDS = (
    "id", "text", "type", "stream", "session_id", "turn", "scene_id",
    "source_type", "source_reliability", "verification_status", "fallback_generated",
    "speaker_focus", "memory_kind", "ts", "source_event_ids", "source_agent",
    "evidence_refs", "evidence_refs_truncated", "evidence_status", "evidence_version",
    "missing_source_event_ids", "scope", "project", "project_id", "project_path",
    "repository", "entity_id", "subject",
)


def _identity(memory: Mapping[str, Any]) -> Identity:
    source, memory_id = str(memory.get("source_agent") or ""), str(memory.get("id") or "")
    if not source.strip() or not memory_id.strip():
        raise ValueError("Every seed and sibling requires source_agent and id")
    return source, memory_id


def _public(memory: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: deepcopy(memory[key]) for key in _PUBLIC_FIELDS if key in memory and memory[key] is not None}
    source, memory_id = _identity(result)
    result["source_agent"], result["id"] = source, memory_id
    for ref in result.get("evidence_refs") or []:
        if isinstance(ref, dict) and not ref.get("source_agent"):
            ref["source_agent"] = result["source_agent"]
    return result


def _cosine(left: Vector | None, right: Vector | None) -> float:
    if left is None or right is None:
        return 0.0
    try:
        a, b = tuple(float(value) for value in left), tuple(float(value) for value in right)
        if not a or len(a) != len(b) or not all(math.isfinite(value) for value in (*a, *b)):
            return 0.0
        norm_a, norm_b = math.hypot(*a), math.hypot(*b)
        if not norm_a or not norm_b:
            return 0.0
        return max(-1.0, min(1.0, sum((x / norm_a) * (y / norm_b) for x, y in zip(a, b))))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _score(hit: Mapping[str, Any]) -> float:
    for field in ("selection_score", "score", "final_score", "base_score"):
        if hit.get(field) is not None:
            score = float(hit[field])
            if not math.isfinite(score):
                raise ValueError("Seed scores must be finite")
            return score
    return 0.0


def build_scene_context_result(
    admitted_hits: Sequence[Mapping[str, Any]],
    allowed_siblings: Sequence[Mapping[str, Any]],
    *,
    query_vector: Vector | None,
    vectors: Mapping[Identity, Vector],
    contextual_vectors: Mapping[Identity, Vector] | None = None,
    arm: str = "expansion_only",
    top_k: int = 8,
    max_chars: int = 700,
    relationship_bonus_cap: float = 0.1,
) -> dict[str, Any]:
    """Build and select whole packets under a TOTAL record and character budget.

    Both arms construct identical packets from the first three distinct direct
    source/scene pairs in admitted order. Up to two new siblings per scene
    must match both query and seed by the fixed cosine gates. An admitted fact
    is never duplicated as a sibling. Later siblings cannot initiate a hop.

    Expansion scores equal the supplied seed score. Contextual scores add the
    capped maximum association product, optionally competing with the positive
    query-cosine delta of a supplied contextual seed vector. Repeated support
    never adds votes. No text is concatenated or embedded here.

    ``rendered`` is the exact JSON-lines packet representation charged against
    max_chars, including all public record metadata and linkage labels. All
    returned fact text appears there. Selection is descending score with
    admitted-order ties, not utility per character or a coverage optimizer.
    Expanded packets are tried first; a seed-only fallback at the unchanged
    base score becomes eligible only when its full packet cannot fit. Omitted
    context never contributes a retained score bonus. Diagnostics identify
    candidates but contain no hidden fact text.
    """
    if arm not in {"expansion_only", "contextual_selection"}:
        raise ValueError("Unknown scene context experiment arm")
    cap = float(relationship_bonus_cap)
    if not math.isfinite(cap) or not 0 <= cap <= _MAX_BONUS:
        raise ValueError("relationship_bonus_cap must be between 0 and 0.1")
    if int(top_k) < 0 or int(max_chars) < 0:
        raise ValueError("Budgets must be nonnegative")

    seeds = []
    admitted_ids: set[Identity] = set()
    for hit in admitted_hits:
        memory = _public(hit.get("memory") or {})
        identity = _identity(memory)
        if identity in admitted_ids:
            raise ValueError("Admitted seed identities must be unique")
        admitted_ids.add(identity)
        seeds.append((memory, _score(hit)))

    # There is deliberately no recall rescue when the public path abstains.
    scene_owners: dict[Identity, Identity] = {}
    for memory, _ in seeds:
        scene = str(memory.get("scene_id") or "")
        scene_key = (memory["source_agent"], scene)
        if scene and scene_key not in scene_owners and len(scene_owners) < _MAX_SCENES:
            scene_owners[scene_key] = _identity(memory)
    siblings_by_scene: dict[Identity, dict[Identity, dict[str, Any]]] = {}
    for sibling in allowed_siblings if scene_owners else []:
        memory = _public(sibling)
        identity = _identity(memory)
        scene_key = (memory["source_agent"], str(memory.get("scene_id") or ""))
        if scene_key in scene_owners and identity not in admitted_ids:
            group = siblings_by_scene.setdefault(scene_key, {})
            if identity in group and group[identity] != memory:
                raise ValueError("Conflicting records share one sibling identity")
            group[identity] = memory

    packets = []
    inspected_ids: list[Identity] = []
    for index, (seed, base_score) in enumerate(seeds):
        identity = _identity(seed)
        scene_key = (seed["source_agent"], str(seed.get("scene_id") or ""))
        connections = []
        if scene_owners.get(scene_key) == identity:
            for sibling_id, sibling in sorted(siblings_by_scene.get(scene_key, {}).items()):
                inspected_ids.append(sibling_id)
                query_similarity = _cosine(query_vector, vectors.get(sibling_id))
                seed_similarity = _cosine(vectors.get(identity), vectors.get(sibling_id))
                if query_similarity >= _MIN_QUERY_SIMILARITY and seed_similarity >= _MIN_SEED_SIMILARITY:
                    connections.append((query_similarity * seed_similarity, sibling_id, sibling))
        connections.sort(key=lambda item: (-item[0], item[1]))
        selected_context = connections[:_MAX_SIBLINGS]
        relationship = max((item[0] for item in selected_context), default=0.0)
        contextual_delta = 0.0
        if selected_context and contextual_vectors and identity in contextual_vectors:
            contextual_delta = max(0.0, _cosine(query_vector, contextual_vectors[identity])
                                   - _cosine(query_vector, vectors.get(identity)))
        bonus = min(cap, max(cap * relationship, contextual_delta)) if selected_context else 0.0
        score = base_score + bonus if arm == "contextual_selection" else base_score
        records = [{"role": "seed", "memory": seed}] + [
            {"role": "scene_context", "memory": item[2]} for item in selected_context]
        packet = {"linkage": "scene_context_not_causation", "seed_id": list(identity),
                  "base_score": base_score, "score": score, "records": records}
        rendered = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        packets.append((score, index, packet, rendered))
        if selected_context:
            fallback = {**packet, "score": base_score, "records": records[:1]}
            fallback_rendered = json.dumps(fallback, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            packets.append((base_score, index, fallback, fallback_rendered))

    packets.sort(key=lambda item: (-item[0], item[1]))
    selected, rendered_parts, hits = [], [], []
    chars = 0
    selected_seeds: set[Identity] = set()
    for _, _, packet, rendered in packets:
        seed_id = tuple(packet["seed_id"])
        if seed_id in selected_seeds:
            continue
        record_count = len(packet["records"])
        cost = len(rendered) + bool(rendered_parts)
        if len(hits) + record_count > int(top_k) or chars + cost > int(max_chars):
            continue
        selected.append(packet)
        selected_seeds.add(seed_id)
        rendered_parts.append(rendered)
        chars += cost
        for record in packet["records"]:
            hits.append({"memory": record["memory"], "score": packet["score"],
                         "context_role": record["role"], "linked_seed": packet["seed_id"]})
    selected_ids = [_identity(hit["memory"]) for hit in hits]
    return {
        "hits": hits, "packets": selected, "rendered": "\n".join(rendered_parts),
        "candidate_ids": [_identity(memory) for memory, _ in seeds],
        "selected_ids": selected_ids,
        "context_ids": [_identity(hit["memory"]) for hit in hits if hit["context_role"] == "scene_context"],
        "inspected_sibling_ids": inspected_ids,
        "stats": {"seed_count": len(seeds), "seed_scene_count": len(scene_owners),
                  "inspected_sibling_count": len(inspected_ids), "selected_record_count": len(hits),
                  "selected_packet_count": len(selected), "rendered_chars": chars,
                  "embedding_calls": 0, "provider_calls": 0},
    }
