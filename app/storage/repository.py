from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class CandidateFilters:
    recency_days: Optional[int]
    session_id: Optional[str]
    session_bias: bool
    memory_types: Optional[List[str]]
    mode: str
    min_reliability: float
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class MemoryRepository(Protocol):
    def append_memories(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ...

    def load_all_memories(self) -> List[Dict[str, Any]]:
        ...

    def get_recent_memories(self, limit: int = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    def get_memory_count(self, session_id: Optional[str] = None) -> int:
        ...

    def query_candidates(self, filters: CandidateFilters) -> List[Dict[str, Any]]:
        ...

    def query_by_ids(self, memory_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        ...

    def get_strong_neighbors(self, memory_id: str, min_weight: float, max_neighbors: int) -> List[Tuple[str, float, float]]:
        ...

    def update_lnn_state(self, memory_id: str, h: Optional[float] = None, tau: Optional[float] = None,
                         outgoing_weights: Optional[Dict[str, float]] = None,
                         incoming_weights: Optional[Dict[str, float]] = None) -> None:
        ...

    def batch_update_weights(self, weight_deltas: List[Tuple[str, str, float]]) -> None:
        ...

    def decay_all_activations(self, tau_disuse_decay: float, dt_minutes: float) -> None:
        ...

    def decay_all_tau(self, tau_disuse_decay: float, dt_minutes: float) -> None:
        ...

    def decay_all_weights(self, weight_decay: float) -> None:
        ...

