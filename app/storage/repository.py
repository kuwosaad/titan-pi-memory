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


class LnnStateStore(Protocol):
    """Mutation and graph-state operations backed by the LNN-capable adapter.

    JSON remains a read/write compatibility store, but does not implement this
    capability.  Callers should check ``supports_lnn`` (or use
    :func:`get_lnn_state_store`) before invoking these operations.
    """

    supports_lnn: bool

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


class MemoryStore(Protocol):
    """Feature-complete durable memory query/append contract.

    LNN graph mutation intentionally lives in :class:`LnnStateStore` rather
    than being part of this storage contract.  This lets the JSON compatibility
    adapter provide the durable memory basics without silently claiming LNN
    support.
    """

    def append_memories(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ...

    def load_all_memories(self) -> List[Dict[str, Any]]:
        ...

    def get_recent_memories(self, limit: Optional[int] = 8, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    def get_memory_count(self, session_id: Optional[str] = None) -> int:
        ...

    def query_candidates(self, filters: CandidateFilters) -> List[Dict[str, Any]]:
        ...

    def query_candidates_with_text(self, fts_query: str, filters: CandidateFilters) -> List[Dict[str, Any]]:
        ...

    def query_by_ids(self, memory_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        ...

    def list_memory_session_ids(self, limit: int = 100) -> List[str]:
        ...


class MemoryRepository(MemoryStore, LnnStateStore, Protocol):
    """Legacy combined protocol retained for existing imports and mocks."""

    # This protocol deliberately stays as the compatibility name while new
    # internal code can type against MemoryStore and LnnStateStore separately.
    pass


def get_lnn_state_store(store: object) -> Optional[LnnStateStore]:
    """Return the LNN capability when the selected adapter supports it.

    Keeping this check in one place prevents JSON's historical no-op methods
    from being mistaken for a functioning LNN backend.
    """

    if bool(getattr(store, "supports_lnn", False)):
        return store  # type: ignore[return-value]
    return None
