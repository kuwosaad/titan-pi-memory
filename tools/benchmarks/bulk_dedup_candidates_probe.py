#!/usr/bin/env python3
"""Offline deterministic candidate-generation probe; never opens Titan stores."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import random
import re
import time
import tracemalloc
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Record:
    text: str
    source: str
    scene: str
    vector: tuple[float, ...]
    duplicate_group: int | None


def make_corpus(n: int, seed: int = 731) -> tuple[list[Record], set[tuple[int, int]]]:
    """Create near-copy families plus unrelated records with deterministic metadata."""
    rng = random.Random(seed + n)
    vocab = [f"term{i}" for i in range(2500)]
    dim, groups = 32, max(1, n // 25)
    centers = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(groups)]
    records: list[Record] = []
    truths: set[tuple[int, int]] = set()
    family_size = 3
    n_dupes = min(n, groups * family_size)
    family_bases = [rng.sample(vocab, 18) for _ in range(groups)]
    for i in range(n):
        group = i // family_size if i < n_dupes else None
        if group is not None:
            words = family_bases[group].copy()
            for _ in range(2):
                words[rng.randrange(len(words))] = rng.choice(vocab)
            text = " ".join(words)
            vector = tuple(x + rng.gauss(0, .025) for x in centers[group])
            source, scene = f"src{i % 4}", f"scene{i % 3}"
            if i % family_size:
                for earlier in range(i - i % family_size, i):
                    truths.add((earlier, i))
        else:
            words = rng.sample(vocab, 18)
            text = " ".join(words)
            vector = tuple(rng.gauss(0, 1) for _ in range(dim))
            source, scene = f"src{i % 4}", f"scene{i % 3}"
        records.append(Record(text, source, scene, vector, group))
    return records, truths


def _pairs_from_buckets(buckets):
    out = set()
    for ids in buckets.values():
        for a, b in itertools.combinations(ids, 2):
            out.add((a, b) if a < b else (b, a))
    return out


def candidates(records: list[Record], method: str) -> set[tuple[int, int]]:
    buckets = defaultdict(list)
    if method == "exact_hash":
        for i, r in enumerate(records):
            buckets[hashlib.sha256(r.text.encode()).digest()].append(i)
    elif method in {"token_overlap", "minhash_lsh"}:
        token_sets = [set(re.findall(r"\w+", r.text.lower())) for r in records]
        if method == "token_overlap":
            for i, toks in enumerate(token_sets):
                for token in toks:
                    buckets[token].append(i)
        else:
            signatures = []
            # Stable affine permutations over token IDs approximate independent
            # MinHash permutations without doing cryptographic work per token.
            token_ids = {token: int(token[4:]) for toks in token_sets for token in toks}
            permutations = [(a, b) for a, b in ((i * 7919 % 65521 or 1, i * 104729 % 65521) for i in range(1, 65))]
            for toks in token_sets:
                ids = [token_ids[t] for t in toks]
                signatures.append(tuple(min((a * t + b) % 65521 for t in ids) for a, b in permutations))
            for i, sig in enumerate(signatures):
                for band in range(8):
                    buckets[(band, sig[band * 8:(band + 1) * 8])].append(i)
    elif method == "embedding_lsh":
        rng = random.Random(992)
        dim = len(records[0].vector)
        planes = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(16)]
        for i, r in enumerate(records):
            code = tuple(sum(a * b for a, b in zip(p, r.vector)) >= 0 for p in planes)
            buckets[code].append(i)
            for bit in range(len(code)):
                flipped = list(code)
                flipped[bit] = not flipped[bit]
                buckets[tuple(flipped)].append(i)
    elif method == "scene_source":
        for i, r in enumerate(records):
            buckets[(r.source, r.scene)].append(i)
    elif method == "source_only":
        for i, r in enumerate(records):
            buckets[r.source].append(i)
    elif method in {"embedding_top5", "embedding_top10"}:
        # Exact cosine top-k is used as a small-corpus reference. It scores all
        # vectors, so only the retained candidate set (not search work) is O(Nk).
        k = int(method.removeprefix("embedding_top"))
        norms = [sum(x * x for x in r.vector) ** 0.5 for r in records]
        out = set()
        for i, r in enumerate(records):
            scored = []
            for j, other in enumerate(records):
                if i == j:
                    continue
                cosine = sum(a * b for a, b in zip(r.vector, other.vector)) / (norms[i] * norms[j])
                scored.append((cosine, j))
            for _, j in sorted(scored, reverse=True)[:k]:
                out.add((i, j) if i < j else (j, i))
        return out
    else:
        raise ValueError(method)
    return _pairs_from_buckets(buckets)


def evaluate(n: int) -> list[dict]:
    records, truth = make_corpus(n)
    rows = []
    methods = ["exact_hash", "token_overlap", "minhash_lsh", "embedding_lsh", "scene_source", "source_only"]
    if n <= 1000:
        methods.extend(("embedding_top5", "embedding_top10"))
    for method in methods:
        tracemalloc.start()
        start = time.perf_counter()
        pairs = candidates(records, method)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append({"method": method, "n": n, "truth_pairs": len(truth), "candidate_pairs": len(pairs), "recall": len(pairs & truth) / len(truth) if truth else 1.0, "seconds": elapsed, "peak_mib": peak / 1048576})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000,5000,10000")
    args = parser.parse_args()
    print("method,n,truth_pairs,candidate_pairs,recall,seconds,peak_mib")
    for n in map(int, args.sizes.split(",")):
        for row in evaluate(n):
            print("{method},{n},{truth_pairs},{candidate_pairs},{recall:.4f},{seconds:.3f},{peak_mib:.1f}".format(**row))


if __name__ == "__main__":
    main()
