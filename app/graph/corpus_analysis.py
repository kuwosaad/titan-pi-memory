"""Bounded, reusable analysis snapshots for Titan's memory corpus.

The graph and Cortex paths used to materialise a dense ``n x n`` cosine
matrix.  That is convenient for small fixtures, but it makes the 10,000
memory safety limit a lie: the matrix alone can exceed hundreds of MB.  This
module keeps the same exact cosine values while calculating rows in bounded
blocks and retaining only sparse top-k edges.

``CorpusAnalysis`` is deliberately framework neutral.  Callers provide the
memories (and, when available, an adapter content revision); no storage or
HTTP imports are required.  LNN activation fields are intentionally absent
from the revision so activation-only updates do not invalidate a snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


def _memory_dict(memory: Any) -> Dict[str, Any]:
    return memory.model_dump() if hasattr(memory, "model_dump") else dict(memory)


def content_revision(memories: Sequence[Mapping[str, Any]]) -> str:
    """Return a stable revision for content/embedding changes only.

    Timestamps, LNN state and other mutable bookkeeping fields are excluded.
    Embeddings are included because changing one changes cosine edges.
    """

    digest = hashlib.sha256()
    for memory in memories:
        embedding = memory.get("embedding")
        if isinstance(embedding, np.ndarray):
            embedding = embedding.astype(np.float32, copy=False).tolist()
        payload = {
            "id": memory.get("id"),
            "text": memory.get("text"),
            "embedding": embedding,
        }
        digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class CorpusAnalysis:
    """Immutable corpus snapshot with exact blockwise cosine operations."""

    memories: Tuple[Dict[str, Any], ...]
    vectors: np.ndarray
    revision: str
    block_size: int = 256

    @classmethod
    def from_memories(
        cls,
        memories: Sequence[Any],
        *,
        revision: Optional[str] = None,
        block_size: int = 256,
    ) -> "CorpusAnalysis":
        normalized_memories = tuple(_memory_dict(memory) for memory in memories)
        embedded_memories: List[Dict[str, Any]] = []
        rows: List[np.ndarray] = []
        dimension: Optional[int] = None
        for memory in normalized_memories:
            value = memory.get("embedding")
            if not isinstance(value, (list, tuple, np.ndarray)) or not len(value):
                continue
            try:
                vector = np.asarray(value, dtype=np.float32).reshape(-1)
            except (TypeError, ValueError):
                continue
            if vector.size == 0 or not np.all(np.isfinite(vector)):
                continue
            if dimension is None:
                dimension = int(vector.size)
            if vector.size == dimension:
                rows.append(vector)
                embedded_memories.append(memory)

        # A zero-row (or mixed-dimension) corpus remains a valid snapshot.
        matrix = np.vstack(rows).astype(np.float32, copy=False) if rows else np.empty((0, dimension or 0), dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True) if len(matrix) else np.empty((0, 1), dtype=np.float32)
        norms[norms == 0] = 1.0
        matrix = matrix / norms if len(matrix) else matrix
        return cls(
            # A snapshot represents the analyzable (embedded) corpus. Missing
            # embeddings remain visible to storage callers but cannot appear
            # as graph nodes, matching existing cluster behavior.
            memories=tuple(embedded_memories),
            vectors=matrix,
            revision=revision or content_revision(normalized_memories),
            block_size=max(16, int(block_size or 256)),
        )

    @classmethod
    def from_vectors(
        cls,
        vectors: Sequence[np.ndarray],
        *,
        revision: Optional[str] = None,
        block_size: int = 256,
    ) -> "CorpusAnalysis":
        memories = tuple({"embedding": np.asarray(vector).tolist()} for vector in vectors)
        return cls.from_memories(memories, revision=revision, block_size=block_size)

    @property
    def count(self) -> int:
        return int(self.vectors.shape[0])

    def similarity(self, left: int, right: int) -> float:
        """Return exact cosine similarity for two indexed vectors."""

        if left == right or left < 0 or right < 0 or left >= self.count or right >= self.count:
            return 0.0
        value = float(np.dot(self.vectors[left], self.vectors[right]))
        return value if math.isfinite(value) else 0.0

    def subset(self, indices: Sequence[int]) -> "CorpusAnalysis":
        """Return a zero-copy view over selected embedded memories."""

        selected = tuple(int(index) for index in indices if 0 <= int(index) < self.count)
        return CorpusAnalysis(
            memories=tuple(self.memories[index] for index in selected),
            vectors=self.vectors[list(selected)].copy() if selected else np.empty((0, self.vectors.shape[1]), dtype=np.float32),
            revision=self.revision,
            block_size=self.block_size,
        )

    def top_k_edges(
        self,
        *,
        top_k: int,
        min_sim: float,
        indices: Optional[Sequence[int]] = None,
    ) -> List[Tuple[int, int, float]]:
        """Build exact sparse top-k undirected edges without a dense matrix."""

        if self.count < 2:
            return []
        selected = [int(index) for index in indices] if indices is not None else list(range(self.count))
        selected = [index for index in selected if 0 <= index < self.count]
        if len(selected) < 2:
            return []
        limit = max(1, min(int(top_k), len(selected) - 1))
        # Keep the current graph semantics: each row selects its own top-k,
        # then mirrored selections collapse to one undirected edge.
        best: dict[tuple[int, int], float] = {}
        normalized = self.vectors[selected]
        for start in range(0, len(selected), self.block_size):
            stop = min(start + self.block_size, len(selected))
            block = normalized[start:stop] @ normalized.T
            for offset, row in enumerate(block):
                row_index = start + offset
                # Exclude the row itself in the selected-coordinate space.
                row = np.asarray(row, dtype=np.float32).copy()
                row[row_index] = -np.inf
                if limit >= len(selected) - 1:
                    candidates = np.argsort(row)[::-1]
                else:
                    candidates = np.argpartition(row, -limit)[-limit:]
                    candidates = candidates[np.argsort(row[candidates])[::-1]]
                left = selected[row_index]
                for candidate in candidates:
                    weight = float(row[int(candidate)])
                    if weight < float(min_sim) or not math.isfinite(weight):
                        continue
                    right = selected[int(candidate)]
                    edge = tuple(sorted((left, right)))
                    best[edge] = max(best.get(edge, 0.0), weight)
        return [(left, right, weight) for (left, right), weight in best.items()]

    def pair_similarities(
        self,
        *,
        indices: Optional[Sequence[int]] = None,
        min_sim: float = -math.inf,
    ) -> Iterable[Tuple[int, int, float]]:
        """Yield exact pair similarities using bounded row blocks."""

        selected = [int(index) for index in indices] if indices is not None else list(range(self.count))
        selected = [index for index in selected if 0 <= index < self.count]
        normalized = self.vectors[selected]
        for start in range(0, len(selected), self.block_size):
            stop = min(start + self.block_size, len(selected))
            block = normalized[start:stop] @ normalized.T
            for offset, row in enumerate(block):
                left_pos = start + offset
                for right_pos in range(left_pos + 1, len(selected)):
                    value = float(row[right_pos])
                    if math.isfinite(value) and value >= min_sim:
                        yield selected[left_pos], selected[right_pos], value


_SNAPSHOT_CACHE: "OrderedDict[tuple[str, int], CorpusAnalysis]" = OrderedDict()
_SNAPSHOT_CACHE_LIMIT = 2


def snapshot_for_memories(
    memories: Sequence[Any],
    *,
    revision: Optional[str] = None,
    block_size: int = 256,
) -> CorpusAnalysis:
    """Reuse a snapshot for an unchanged content revision.

    The cache is intentionally tiny. It bounds retained vector memory while
    allowing cluster inspection followed immediately by Cortex analysis to
    share one immutable snapshot.
    """

    normalized = [_memory_dict(memory) for memory in memories]
    snapshot_revision = revision or content_revision(normalized)
    resolved_block_size = max(16, int(block_size or 256))
    key = (snapshot_revision, resolved_block_size)
    existing = _SNAPSHOT_CACHE.get(key)
    if existing is not None:
        _SNAPSHOT_CACHE.move_to_end(key)
        return existing
    snapshot = CorpusAnalysis.from_memories(
        normalized,
        revision=snapshot_revision,
        block_size=resolved_block_size,
    )
    _SNAPSHOT_CACHE[key] = snapshot
    while len(_SNAPSHOT_CACHE) > _SNAPSHOT_CACHE_LIMIT:
        _SNAPSHOT_CACHE.popitem(last=False)
    return snapshot


def clear_snapshot_cache() -> None:
    _SNAPSHOT_CACHE.clear()
