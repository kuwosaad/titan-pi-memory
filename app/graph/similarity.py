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
    n = len(vectors)
    edges = []

    for i in range(n):
        sims = []
        for j in range(n):
            if i == j:
                continue
            sim = cosine_similarity(vectors[i], vectors[j])
            sims.append((j, sim))

        sims.sort(key=lambda x: x[1], reverse=True)

        for j, sim in sims[:top_k]:
            if sim >= min_sim:
                a, b = sorted((i, j))
                edges.append((a, b, sim))

    best = {}
    for a, b, w in edges:
        best[(a, b)] = max(best.get((a, b), 0.0), w)

    return [(a, b, w) for (a, b), w in best.items()]
