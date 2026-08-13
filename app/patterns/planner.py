from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_./-]{2,}")
_HIGH_SIGNAL_TERMS = {
    "avoid",
    "broken",
    "bug",
    "corrected",
    "failed",
    "fixed",
    "never",
    "always",
    "should",
}
_HIGH_SIGNAL_PHRASES = ("root cause",)
_ENTITY_STOPWORDS = {
    "about",
    "after",
    "always",
    "before",
    "bug",
    "fixed",
    "memory",
    "memories",
    "never",
    "notes",
    "should",
    "that",
    "this",
    "when",
    "with",
    "workflow",
}


@dataclass(frozen=True)
class EvidencePacketPlan:
    packet_id: str
    packet_type: str
    seed_memory_ids: list[str]
    context_memory_ids: list[str]
    score: float
    reasons: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


class PatternMiningPlanner:
    def __init__(
        self,
        *,
        unprocessed_memories: Sequence[Dict[str, Any]],
        all_memories: Sequence[Dict[str, Any]],
        batch_size: int,
        context_limit: int,
        packet_type: Optional[str] = None,
        cluster_payload: Optional[Dict[str, Any]] = None,
        cluster_analysis_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.unprocessed_memories = [mem for mem in unprocessed_memories if mem.get("id")]
        self.all_memories = [mem for mem in all_memories if mem.get("id")]
        self.batch_size = max(1, int(batch_size or 100))
        self.context_limit = max(0, int(context_limit or 0))
        self.packet_type = packet_type
        self.cluster_payload = cluster_payload or {}
        self.cluster_analysis_payload = cluster_analysis_payload or {}

    def plan(self) -> list[EvidencePacketPlan]:
        builders = {
            "high_signal": self._build_high_signal_packet,
            "semantic_cluster": self._build_semantic_cluster_packet,
            "entity": self._build_entity_packet,
            "bridge": self._build_bridge_packet,
            "contradiction": self._build_contradiction_packet,
            "scene_episode": self._build_scene_episode_packet,
            "chronological_fallback": self._build_chronological_fallback_packet,
        }
        if self.packet_type:
            builder = builders.get(self.packet_type)
            if builder is None:
                return [self._build_chronological_fallback_packet([f"Unsupported packet_type '{self.packet_type}'; using chronological fallback."])]
            plan = builder()
            return [plan] if plan is not None else [self._build_chronological_fallback_packet([f"No {self.packet_type} packet was available; using chronological fallback."])]

        plans = [plan for plan in (builder() for builder in builders.values()) if plan is not None]
        plans.sort(key=lambda plan: (-plan.score, plan.packet_type, plan.packet_id))
        return plans

    def _build_high_signal_packet(self) -> Optional[EvidencePacketPlan]:
        signal_scores: dict[str, float] = {}
        for mem in self.unprocessed_memories:
            score = _high_signal_score(mem)
            if score > 0.0:
                signal_scores[str(mem["id"])] = score
        signal_ids = set(signal_scores)
        if not signal_ids:
            return None

        scored_ids: dict[str, float] = dict(signal_scores)
        signal_memories = [mem for mem in self.unprocessed_memories if str(mem.get("id")) in signal_ids]
        for mem in self.unprocessed_memories:
            memory_id = str(mem.get("id"))
            if memory_id in signal_ids:
                continue
            relatedness = max((_text_similarity(mem, signal) for signal in signal_memories), default=0.0)
            if relatedness >= 0.05:
                scored_ids[memory_id] = relatedness

        selected = self._ordered_unprocessed_ids(scored_ids)[: self.batch_size]
        if not selected:
            return None

        scene_count = self._scene_count(selected)
        score = min(1.0, 0.72 + (0.08 * min(3, len(signal_ids))) + (0.05 * min(2, scene_count)))
        return self._make_plan(
            packet_type="high_signal",
            seed_memory_ids=selected,
            score=score,
            reasons=[
                "Found unprocessed memories with failure/correction/behavior-rule language.",
                "Included nearby semantically related unprocessed memories as supporting seeds.",
            ],
        )

    def _build_scene_episode_packet(self) -> Optional[EvidencePacketPlan]:
        grouped: dict[str, list[Dict[str, Any]]] = defaultdict(list)
        for mem in self.unprocessed_memories:
            scene_id = str(mem.get("scene_id") or "")
            session_id = str(mem.get("session_id") or "")
            if scene_id:
                grouped[f"scene:{scene_id}"].append(mem)
            elif session_id:
                grouped[f"session:{session_id}"].append(mem)

        candidates = [items for items in grouped.values() if len(items) >= 2]
        if not candidates:
            return None

        def candidate_key(items: list[Dict[str, Any]]) -> tuple[float, int, str]:
            signal = sum(_high_signal_score(mem) for mem in items)
            latest = max(str(mem.get("ts") or "") for mem in items)
            return (signal, len(items), latest)

        selected_memories = sorted(max(candidates, key=candidate_key), key=_timeline_key)[: self.batch_size]
        selected = [str(mem["id"]) for mem in selected_memories]
        score = min(0.95, 0.62 + (0.06 * min(5, len(selected))) + (0.08 * min(2, sum(_high_signal_score(mem) for mem in selected_memories))))
        return self._make_plan(
            packet_type="scene_episode",
            seed_memory_ids=selected,
            score=score,
            reasons=[
                "Grouped unprocessed memories from the same scene/session to preserve causal order.",
                "Sorted seeds by turn and timestamp so the agent can inspect sequence.",
            ],
        )

    def _build_semantic_cluster_packet(self) -> Optional[EvidencePacketPlan]:
        clusters = self.cluster_payload.get("clusters") if isinstance(self.cluster_payload, dict) else None
        if not isinstance(clusters, list):
            return None

        unprocessed_ids = {str(mem["id"]) for mem in self.unprocessed_memories}
        by_id = {str(mem["id"]): mem for mem in self.all_memories if mem.get("id")}
        candidates: list[tuple[tuple[float, int, int, int, str], Dict[str, Any], list[str], list[str]]] = []
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            cluster_ids = [str(memory_id) for memory_id in (cluster.get("memory_ids") or []) if str(memory_id) in by_id]
            seed_ids = [memory_id for memory_id in cluster_ids if memory_id in unprocessed_ids]
            if not seed_ids:
                continue
            cluster_memories = [by_id[memory_id] for memory_id in cluster_ids]
            scene_count = len({str(mem.get("scene_id")) for mem in cluster_memories if mem.get("scene_id")})
            if len(cluster_ids) < 3 or scene_count < 2:
                continue

            signal = sum(_high_signal_score(by_id[memory_id]) for memory_id in seed_ids)
            avg_similarity = float(cluster.get("avg_similarity") or 0.0)
            connection_count = int(cluster.get("connection_count") or 0)
            score = signal + avg_similarity + (0.15 * len(seed_ids)) + (0.08 * scene_count) + (0.03 * connection_count)
            cluster_key = str(cluster.get("cluster_id") or "")
            candidates.append(((score, len(seed_ids), len(cluster_ids), scene_count, cluster_key), cluster, seed_ids, cluster_ids))

        if not candidates:
            return None

        _candidate_key, cluster, seed_ids, cluster_ids = max(candidates, key=lambda item: item[0])
        seed_memories = sorted((by_id[memory_id] for memory_id in seed_ids), key=_timeline_key)[: self.batch_size]
        selected = [str(mem["id"]) for mem in seed_memories]
        selected_set = set(selected)
        context_memories = sorted(
            (by_id[memory_id] for memory_id in cluster_ids if memory_id not in selected_set),
            key=_timeline_key,
        )
        context_ids = [
            str(mem["id"])
            for mem in context_memories
            if mem.get("id")
        ][: self.context_limit]
        scene_ids = {by_id[memory_id].get("scene_id") for memory_id in cluster_ids if by_id[memory_id].get("scene_id")}
        score = min(
            0.98,
            0.66
            + (0.05 * min(5, len(selected)))
            + (0.04 * min(5, len(context_ids)))
            + (0.04 * min(3, len(scene_ids))),
        )
        return EvidencePacketPlan(
            packet_id=_packet_id("semantic_cluster", selected, context_ids),
            packet_type="semantic_cluster",
            seed_memory_ids=selected,
            context_memory_ids=context_ids,
            score=round(float(score), 4),
            reasons=[
                "Selected a dense graph cluster containing unprocessed memories.",
                "Preserved timeline order for unprocessed cluster seeds and used other cluster members as context.",
            ],
            metadata={"source_cluster": _compact_cluster(cluster)},
        )

    def _build_entity_packet(self) -> Optional[EvidencePacketPlan]:
        unprocessed_ids = {str(mem["id"]) for mem in self.unprocessed_memories}
        by_id = {str(mem["id"]): mem for mem in self.all_memories if mem.get("id")}
        candidate_terms: dict[str, dict[str, Any]] = {}

        for mem in self.all_memories:
            memory_id = str(mem.get("id") or "")
            if not memory_id:
                continue
            for term in _entity_terms(mem):
                bucket = candidate_terms.setdefault(term, {"seed_ids": [], "context_ids": [], "scene_ids": set()})
                if mem.get("scene_id"):
                    bucket["scene_ids"].add(str(mem.get("scene_id")))
                if memory_id in unprocessed_ids:
                    bucket["seed_ids"].append(memory_id)
                else:
                    bucket["context_ids"].append(memory_id)

        candidates: list[tuple[tuple[float, int, int, int], str, list[str], list[str], set[str]]] = []
        for term, bucket in candidate_terms.items():
            seed_ids = _dedupe(bucket["seed_ids"])
            if not seed_ids:
                continue
            context_ids = _dedupe(bucket["context_ids"])
            scene_ids = bucket["scene_ids"]
            if len(seed_ids) < 2 and len(scene_ids) < 2:
                continue
            signal = sum(_high_signal_score(by_id[memory_id]) for memory_id in seed_ids if memory_id in by_id)
            score = signal + (0.22 * len(seed_ids)) + (0.12 * len(context_ids)) + (0.1 * len(scene_ids))
            candidates.append(((score, len(seed_ids), len(context_ids), len(scene_ids)), term, seed_ids, context_ids, scene_ids))

        if not candidates:
            return None

        candidate_key, term, seed_ids, context_ids, scene_ids = max(candidates, key=lambda item: item[0])
        selected_memories = sorted((by_id[memory_id] for memory_id in seed_ids if memory_id in by_id), key=_timeline_key)[: self.batch_size]
        selected = [str(mem["id"]) for mem in selected_memories]
        selected_set = set(selected)
        context_memories = sorted(
            (by_id[memory_id] for memory_id in context_ids if memory_id in by_id and memory_id not in selected_set),
            key=_timeline_key,
        )
        selected_context = [str(mem["id"]) for mem in context_memories[: self.context_limit]]
        score = min(0.94, 0.58 + (0.06 * min(5, len(selected))) + (0.04 * min(5, len(selected_context))) + (0.04 * min(3, len(scene_ids))))
        return EvidencePacketPlan(
            packet_id=_packet_id("entity", selected, selected_context),
            packet_type="entity",
            seed_memory_ids=selected,
            context_memory_ids=selected_context,
            score=round(float(score), 4),
            reasons=[
                f"Grouped unprocessed memories around repeated entity/term '{term}'.",
                "Used older memories mentioning the same entity as supporting context.",
            ],
            metadata={
                "source_entity": {
                    "term": term,
                    "seed_count": len(seed_ids),
                    "context_count": len(context_ids),
                    "scene_count": len(scene_ids),
                    "score_components": list(candidate_key[:4]),
                }
            },
        )

    def _build_bridge_packet(self) -> Optional[EvidencePacketPlan]:
        analysis = self.cluster_analysis_payload if isinstance(self.cluster_analysis_payload, dict) else {}
        bridges = analysis.get("bridges")
        if not isinstance(bridges, list) or not bridges:
            return None

        unprocessed_ids = {str(mem["id"]) for mem in self.unprocessed_memories}
        by_id = {str(mem["id"]): mem for mem in self.all_memories if mem.get("id")}
        seed_scores: dict[str, float] = {}
        context_scores: dict[str, float] = {}
        for bridge in bridges:
            if not isinstance(bridge, dict):
                continue
            raw = bridge.get("bridge_score")
            if raw is None:
                raw = bridge.get("similarity") or 0.0
            bridge_score = float(raw)
            for key in ("source_memory", "target_memory"):
                memory = bridge.get(key)
                if not isinstance(memory, dict) or not memory.get("id"):
                    continue
                memory_id = str(memory["id"])
                if memory_id not in by_id:
                    continue
                if memory_id in unprocessed_ids:
                    seed_scores[memory_id] = max(seed_scores.get(memory_id, 0.0), bridge_score)
                else:
                    context_scores[memory_id] = max(context_scores.get(memory_id, 0.0), bridge_score)

        if not seed_scores:
            return None

        for section in ("bridge_memories", "central_memories"):
            items = analysis.get(section)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                memory_id = str(item["id"])
                if memory_id in by_id and memory_id not in unprocessed_ids:
                    context_scores[memory_id] = max(context_scores.get(memory_id, 0.0), float(item.get("score") or 0.0))

        selected = self._ordered_unprocessed_ids(seed_scores)[: self.batch_size]
        selected_set = set(selected)
        context_memories = [by_id[memory_id] for memory_id in context_scores if memory_id in by_id and memory_id not in selected_set]
        context_memories.sort(key=lambda mem: (-context_scores[str(mem["id"])], str(mem.get("ts") or ""), str(mem.get("id") or "")))
        context_ids = [str(mem["id"]) for mem in context_memories[: self.context_limit]]
        score = min(0.97, 0.64 + (0.08 * min(4, len(selected))) + (0.04 * min(4, len(context_ids))) + (0.03 * min(4, len(bridges))))
        return EvidencePacketPlan(
            packet_id=_packet_id("bridge", selected, context_ids),
            packet_type="bridge",
            seed_memory_ids=selected,
            context_memory_ids=context_ids,
            score=round(float(score), 4),
            reasons=[
                "Selected unprocessed memories that Cortex surfaced as cross-cluster bridge endpoints.",
                "Used processed bridge/central memories from the analyzed clusters as supporting context.",
            ],
            metadata={"source_analysis": _compact_cluster_analysis(analysis, bridges)},
        )

    def _build_contradiction_packet(self) -> Optional[EvidencePacketPlan]:
        analysis = self.cluster_analysis_payload if isinstance(self.cluster_analysis_payload, dict) else {}
        tensions = analysis.get("tensions")
        if not isinstance(tensions, list) or not tensions:
            return None

        unprocessed_ids = {str(mem["id"]) for mem in self.unprocessed_memories}
        by_id = {str(mem["id"]): mem for mem in self.all_memories if mem.get("id")}
        seed_scores: dict[str, float] = {}
        context_scores: dict[str, float] = {}
        endpoint_scores: dict[str, float] = {}
        source_tensions: list[Dict[str, Any]] = []

        for tension in tensions:
            if not isinstance(tension, dict):
                continue
            raw_score = tension.get("similarity")
            score = float(raw_score) if raw_score is not None else 0.0
            endpoint_ids: list[str] = []
            for key in ("older_memory", "newer_memory"):
                memory = tension.get(key)
                if not isinstance(memory, dict) or not memory.get("id"):
                    continue
                memory_id = str(memory["id"])
                if memory_id not in by_id:
                    continue
                endpoint_ids.append(memory_id)
                endpoint_scores[memory_id] = max(endpoint_scores.get(memory_id, 0.0), score)
                if memory_id in unprocessed_ids:
                    seed_scores[memory_id] = max(seed_scores.get(memory_id, 0.0), score)
                else:
                    context_scores[memory_id] = max(context_scores.get(memory_id, 0.0), score)
            if any(memory_id in unprocessed_ids for memory_id in endpoint_ids):
                source_tensions.append(tension)

        if not seed_scores:
            return None

        selected = self._ordered_unprocessed_ids(seed_scores)[: self.batch_size]
        selected_set = set(selected)
        for memory_id, score in endpoint_scores.items():
            if memory_id not in selected_set:
                context_scores[memory_id] = max(context_scores.get(memory_id, 0.0), score)
        context_memories = [by_id[memory_id] for memory_id in context_scores if memory_id in by_id and memory_id not in selected_set]
        context_memories.sort(key=lambda mem: (-context_scores[str(mem["id"])], str(mem.get("ts") or ""), str(mem.get("id") or "")))
        context_ids = [str(mem["id"]) for mem in context_memories[: self.context_limit]]
        score = min(0.97, 0.66 + (0.08 * min(4, len(selected))) + (0.05 * min(4, len(context_ids))) + (0.04 * min(4, len(source_tensions))))
        return EvidencePacketPlan(
            packet_id=_packet_id("contradiction", selected, context_ids),
            packet_type="contradiction",
            seed_memory_ids=selected,
            context_memory_ids=context_ids,
            score=round(float(score), 4),
            reasons=[
                "Selected unprocessed memories that Cortex surfaced in possible contradiction/tension pairs.",
                "Included the opposing endpoint as context so the agent can decide whether this is a real rule change.",
            ],
            metadata={"source_tensions": _compact_tensions(source_tensions)},
        )

    def _build_chronological_fallback_packet(self, extra_reasons: Optional[list[str]] = None) -> EvidencePacketPlan:
        selected = [str(mem["id"]) for mem in self.unprocessed_memories[: self.batch_size]]
        reasons = extra_reasons or []
        reasons.append("Used oldest unprocessed memories as a robust chronological fallback.")
        return self._make_plan(
            packet_type="chronological_fallback",
            seed_memory_ids=selected,
            score=0.35 if selected else 0.0,
            reasons=reasons,
        )

    def _make_plan(self, *, packet_type: str, seed_memory_ids: list[str], score: float, reasons: list[str]) -> EvidencePacketPlan:
        context_ids = self._context_ids(seed_memory_ids)
        return EvidencePacketPlan(
            packet_id=_packet_id(packet_type, seed_memory_ids, context_ids),
            packet_type=packet_type,
            seed_memory_ids=seed_memory_ids,
            context_memory_ids=context_ids,
            score=round(float(score), 4),
            reasons=reasons,
        )

    def _ordered_unprocessed_ids(self, scored_ids: dict[str, float]) -> list[str]:
        memories = [mem for mem in self.unprocessed_memories if str(mem.get("id")) in scored_ids]
        memories.sort(key=lambda mem: (-scored_ids[str(mem["id"])], str(mem.get("ts") or ""), str(mem.get("id") or "")))
        return [str(mem["id"]) for mem in memories]

    def _context_ids(self, seed_memory_ids: Sequence[str]) -> list[str]:
        if self.context_limit <= 0 or not seed_memory_ids:
            return []
        seed_set = set(seed_memory_ids)
        by_id = {str(mem.get("id")): mem for mem in self.all_memories}
        seed_memories = [by_id[memory_id] for memory_id in seed_memory_ids if memory_id in by_id]
        scored: list[tuple[Dict[str, Any], float]] = []
        for mem in self.all_memories:
            memory_id = str(mem.get("id"))
            if memory_id in seed_set:
                continue
            score = max((_text_similarity(mem, seed) for seed in seed_memories), default=0.0)
            same_scene = any(mem.get("scene_id") and mem.get("scene_id") == seed.get("scene_id") for seed in seed_memories)
            same_session = any(mem.get("session_id") and mem.get("session_id") == seed.get("session_id") for seed in seed_memories)
            if same_scene:
                score += 0.35
            elif same_session:
                score += 0.15
            if score <= 0.05:
                continue
            scored.append((mem, score))
        scored.sort(key=lambda item: (-item[1], str(item[0].get("ts") or ""), str(item[0].get("id") or "")))
        return [str(mem.get("id")) for mem, _score in scored[: self.context_limit] if mem.get("id")]

    def _scene_count(self, memory_ids: Sequence[str]) -> int:
        wanted = set(memory_ids)
        return len({str(mem.get("scene_id")) for mem in self.unprocessed_memories if str(mem.get("id")) in wanted and mem.get("scene_id")})


def _packet_id(packet_type: str, seed_memory_ids: Sequence[str], context_memory_ids: Sequence[str]) -> str:
    digest = hashlib.sha1("\n".join([packet_type, *seed_memory_ids, *context_memory_ids]).encode("utf-8")).hexdigest()[:12]
    return f"{packet_type}:{digest}"


def _compact_cluster(cluster: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cluster_id": cluster.get("cluster_id"),
        "topic": cluster.get("topic"),
        "keywords": list(cluster.get("keywords") or [])[:10],
        "memory_count": cluster.get("memory_count"),
        "connection_count": cluster.get("connection_count"),
        "avg_similarity": cluster.get("avg_similarity"),
        "memory_ids": [str(memory_id) for memory_id in (cluster.get("memory_ids") or [])[:50]],
    }


def _compact_cluster_analysis(analysis: Dict[str, Any], bridges: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "cluster_ids": list(analysis.get("cluster_ids") or []),
        "summary": analysis.get("summary"),
        "memory_count": analysis.get("memory_count"),
        "edge_count": analysis.get("edge_count"),
        "bridges": [
            {
                "source_cluster_id": bridge.get("source_cluster_id"),
                "target_cluster_id": bridge.get("target_cluster_id"),
                "similarity": bridge.get("similarity"),
                "bridge_score": bridge.get("bridge_score"),
                "source_memory_id": (bridge.get("source_memory") or {}).get("id") if isinstance(bridge.get("source_memory"), dict) else None,
                "target_memory_id": (bridge.get("target_memory") or {}).get("id") if isinstance(bridge.get("target_memory"), dict) else None,
            }
            for bridge in bridges[:5]
            if isinstance(bridge, dict)
        ],
    }


def _compact_tensions(tensions: Sequence[Dict[str, Any]]) -> list[Dict[str, Any]]:
    compact: list[Dict[str, Any]] = []
    for tension in tensions[:5]:
        if not isinstance(tension, dict):
            continue
        older = tension.get("older_memory")
        newer = tension.get("newer_memory")
        compact.append(
            {
                "signal": tension.get("signal"),
                "similarity": tension.get("similarity"),
                "shared_terms": list(tension.get("shared_terms") or [])[:8],
                "older_memory_id": older.get("id") if isinstance(older, dict) else None,
                "newer_memory_id": newer.get("id") if isinstance(newer, dict) else None,
            }
        )
    return compact


def _entity_terms(memory: Dict[str, Any]) -> list[str]:
    return [term for term in dict.fromkeys(_tokens(str(memory.get("text") or ""))) if term not in _ENTITY_STOPWORDS]


def _dedupe(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in items if item))


def _timeline_key(memory: Dict[str, Any]) -> tuple[str, int, str]:
    turn = memory.get("turn")
    try:
        turn_value = int(turn)
    except (TypeError, ValueError):
        turn_value = 0
    return (str(memory.get("ts") or ""), turn_value, str(memory.get("id") or ""))


def _high_signal_score(memory: Dict[str, Any]) -> float:
    text = str(memory.get("text") or "").lower()
    tokens = set(_tokens(text))
    score = float(len(tokens & _HIGH_SIGNAL_TERMS))
    score += float(sum(1 for phrase in _HIGH_SIGNAL_PHRASES if phrase in text))
    kind = str(memory.get("memory_kind") or memory.get("type") or "").lower()
    if "issue" in kind or "failure" in kind or "bug" in kind:
        score += 1.0
    return score


def _text_similarity(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    left_tokens = set(_tokens(str(left.get("text") or "")))
    right_tokens = set(_tokens(str(right.get("text") or "")))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _tokens(text: str) -> List[str]:
    return [token.lower().strip("`'\".,:;!?()[]{}<>") for token in _TOKEN_RE.findall(str(text or "")) if len(token) >= 3]
