from typing import List, Tuple
import numpy as np


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def build_similarity_edges(
    vectors: List[np.ndarray],
    top_k: int = 2,
    min_sim: float = 0.35
) -> List[Tuple[int, int, float]]:
    """Link each vector to its ``top_k`` most similar peers (vectors share one dimension).

    Scores come from one normalized matrix product instead of a Python loop
    over every pair. Ties keep the lower index first, as the pairwise loop did.
    """
    n = len(vectors)
    if n < 2 or top_k <= 0:
        return []
    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1)
    unit = matrix / np.where(norms > 0, norms, 1.0)[:, None]
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    nearest = np.argsort(-sims, axis=1, kind="stable")[:, : min(top_k, n - 1)]

    best: dict[Tuple[int, int], float] = {}
    for i in range(n):
        for j in nearest[i]:
            sim = float(sims[i, j])
            if sim >= min_sim:
                a, b = sorted((i, int(j)))
                best[(a, b)] = max(best.get((a, b), 0.0), sim)

    return [(a, b, w) for (a, b), w in best.items()]
