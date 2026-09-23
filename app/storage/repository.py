from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


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


class MemoryStore(Protocol):
    """Feature-complete durable memory query/append contract."""

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


MemoryRepository = MemoryStore
