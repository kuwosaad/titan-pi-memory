from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.graph.similarity import cosine_similarity


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "should", "that", "the", "to", "use",
    "we", "what", "when", "with",
}

LEARNING_PREFIXES = (
    "the user wants ",
    "the user requested ",
    "the user prefers ",
    "the user is ",
    "karu recommends ",
    "karu proposed ",
    "karu suggests ",
    "the biggest risk is ",
    "the simplest workflow ",
    "to evaluate retrieval, ",
    "the current retrieval logic ",
)


@dataclass(frozen=True)
class MemoryRow:
    id: str
    session_id: str
    turn: int
    text: str
    stream: str
    memory_type: str
    ts: str
    source_reliability: float
    embedding: np.ndarray


@dataclass(frozen=True)
class EvalCase:
    gold_id: str
    support_id: str
    session_id: str
    query: str
    gold_text: str
    support_text: str
    overlap: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run combined step1 retrieval + step2 cross-memory reranking "
                    "against an isolated shadow memory store."
    )
    parser.add_argument(
        "--live-db",
        required=True,
        help="Path to the source memory_store.db to copy into the isolated run.",
    )
    parser.add_argument(
        "--run-root",
        required=True,
        help="Night-run root directory for this experiment.",
    )
    parser.add_argument(
        "--sample-size", type=int, default=80,
        help="Maximum number of mined evaluation cases.",
    )
    parser.add_argument(
        "--max-turn-gap", type=int, default=8,
        help="Maximum turn distance between support rough memory and target learning.",
    )
    parser.add_argument(
        "--min-overlap", type=int, default=2,
        help="Minimum lexical overlap between support and gold memory.",
    )
    parser.add_argument(
        "--candidate-pool", type=int, default=16,
        help="Step1 retrieval depth before reranking (top_k equivalent).",
    )
    parser.add_argument(
        "--final-k", type=int, default=5,
        help="Final truncation depth for metrics.",
    )
    parser.add_argument(
        "--alpha-values",
        type=str,
        default="0.0,0.15,0.25,0.35,0.5",
        help="Comma-separated alpha values to sweep for the reranker.",
    )
    parser.add_argument(
        "--show-cases", type=int, default=8,
        help="How many illustrative cases to print.",
    )
    return parser.parse_args()


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def content_tokens(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in STOPWORDS and len(token) > 2}


def normalize_learning_text(text: str) -> str:
    lowered = text.strip().lower()
    for prefix in LEARNING_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip() or text.strip()
    return text.strip()


def anchor_tokens(text: str) -> set[str]:
    anchors = set(re.findall(r"`([^`]+)`", text))
    anchors.update(re.findall(r"\b[a-zA-Z0-9_./:-]{6,}\b", text))
    return {anchor.lower() for anchor in anchors}


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def unpack_embedding(blob: memoryview, dim: int, dtype: str) -> Optional[np.ndarray]:
    import array
    if dtype == "float32":
        return np.frombuffer(blob, dtype=np.float32, count=dim).copy()
    elif dtype == "float16":
        arr = array.array("e", bytes(blob))
        return arr.array("f")
    return None


def load_rows(db_path: Path) -> List[MemoryRow]:
    import app.storage.memories as memory_store
    query = """
    SELECT id, session_id, turn, text, type, stream, ts, source_reliability,
           embedding_blob, embedding_dim, embedding_dtype
    FROM memories
    WHERE embedding_blob IS NOT NULL
      AND embedding_dim IS NOT NULL
      AND COALESCE(text, '') != ''
    ORDER BY session_id, turn, ts
    """
    rows: List[MemoryRow] = []
    with connect_readonly(db_path) as conn:
        for row in conn.execute(query).fetchall():
            vector = memory_store.unpack_embedding(
                row["embedding_blob"], row["embedding_dim"], row["embedding_dtype"]
            )
            if vector is None:
                continue
            rows.append(
                MemoryRow(
                    id=str(row["id"]),
                    session_id=str(row["session_id"]),
                    turn=int(row["turn"]),
                    text=str(row["text"]),
                    stream=str(row["stream"] or "rough"),
                    memory_type=str(row["type"] or "unknown"),
                    ts=str(row["ts"]),
                    source_reliability=float(row["source_reliability"] or 0.0),
                    embedding=np.asarray(vector, dtype=np.float32),
                )
            )
    return rows


def mine_eval_cases(
    rows: Sequence[MemoryRow],
    sample_size: int,
    min_overlap: int,
    max_turn_gap: int,
) -> List[EvalCase]:
    by_session: Dict[str, List[MemoryRow]] = {}
    for row in rows:
        by_session.setdefault(row.session_id, []).append(row)

    mined: List[Tuple[Tuple[int, int, int], EvalCase]] = []
    seen_gold: set[str] = set()

    for session_id, session_rows in by_session.items():
        rough_rows = [row for row in session_rows if row.stream == "rough"]
        learnings = [row for row in session_rows if row.stream == "learnings"]

        for learning in learnings:
            if learning.id in seen_gold:
                continue
            gold_tokens = content_tokens(normalize_learning_text(learning.text))
            gold_anchors = anchor_tokens(learning.text)
            if len(gold_tokens) < 1 and not gold_anchors:
                continue

            best_support: Optional[MemoryRow] = None
            best_overlap = 0
            best_anchor_overlap = 0
            best_gap = 999999

            for rough in rough_rows:
                turn_gap = abs(learning.turn - rough.turn)
                if turn_gap > max_turn_gap:
                    continue
                rough_tokens = content_tokens(rough.text)
                rough_anchors = anchor_tokens(rough.text)
                overlap = len(gold_tokens & rough_tokens)
                anchor_overlap = len(gold_anchors & rough_anchors)
                if not (anchor_overlap >= 1 or overlap >= min_overlap or (turn_gap <= 1 and overlap >= 1)):
                    continue

                if (
                    anchor_overlap > best_anchor_overlap
                    or (anchor_overlap == best_anchor_overlap and overlap > best_overlap)
                    or (anchor_overlap == best_anchor_overlap and overlap == best_overlap and turn_gap < best_gap)
                ):
                    best_support = rough
                    best_overlap = overlap
                    best_anchor_overlap = anchor_overlap
                    best_gap = turn_gap

            if best_support is None:
                continue

            seen_gold.add(learning.id)
            mined.append(
                (
                    (-best_anchor_overlap, -best_overlap, best_gap),
                    EvalCase(
                        gold_id=learning.id,
                        support_id=best_support.id,
                        session_id=session_id,
                        query=best_support.text,
                        gold_text=learning.text,
                        support_text=best_support.text,
                        overlap=best_overlap,
                    ),
                )
            )

    mined.sort(key=lambda item: item[0])
    return [case for _, case in mined[:sample_size]]


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


def canonical_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def dedupe_prefer_latest(rows: Sequence[MemoryRow]) -> List[MemoryRow]:
    latest_by_hash: Dict[str, MemoryRow] = {}
    for row in rows:
        if not row.text.strip():
            continue
        key = canonical_text(row.text)
        key = key.encode("utf-8")
        import hashlib
        key = hashlib.sha1(key).hexdigest()
        current = latest_by_hash.get(key)
        if current is None or parse_timestamp(row.ts) >= parse_timestamp(current.ts):
            latest_by_hash[key] = row
    deduped = list(latest_by_hash.values())
    deduped.sort(key=lambda row: parse_timestamp(row.ts), reverse=True)
    return deduped


def retrieve_step1(
    rows: Sequence[MemoryRow],
    query: str,
    session_id: str,
    candidate_pool: int,
    min_reliability: float,
) -> List[dict]:
    scoped = [row for row in rows if row.source_reliability >= min_reliability]
    if not scoped:
        return []

    session_scoped = [row for row in scoped if row.session_id == session_id]
    if session_scoped:
        scoped = session_scoped

    scoped = dedupe_prefer_latest(scoped)
    if not scoped:
        return []

    from app.embedding.embedder import embed
    query_vector = embed([query.strip()])[0]

    hits: List[dict] = []
    for row in scoped:
        score = float(cosine_similarity(query_vector, row.embedding))
        if score < 0.0:
            continue
        hits.append(
            {
                "memory": {
                    "id": row.id,
                    "text": row.text,
                    "stream": row.stream,
                    "type": row.memory_type,
                    "session_id": row.session_id,
                    "ts": row.ts,
                    "source_reliability": row.source_reliability,
                },
                "score": score,
            }
        )

    hits.sort(
        key=lambda item: (item["score"], parse_timestamp(str(item["memory"].get("ts") or ""))),
        reverse=True,
    )
    return hits[:candidate_pool]


def rank_lookup(items: Sequence[str], target: str) -> Optional[int]:
    for index, item in enumerate(items, start=1):
        if item == target:
            return index
    return None


def rerank_hits(
    hits: Sequence[dict],
    query: str,
    embedding_by_id: Dict[str, np.ndarray],
    alpha: float,
) -> List[Tuple[float, str, float, float]]:
    query_terms = content_tokens(query)
    rescored: List[Tuple[float, str, float, float]] = []

    for item in hits:
        memory = item["memory"]
        memory_id = str(memory.get("id"))
        memory_terms = content_tokens(str(memory.get("text") or ""))
        overlap = len(query_terms & memory_terms)
        base = float(item["score"])
        bonus = 0.0

        if alpha > 0.0 and str(memory.get("stream")) == "learnings" and overlap >= 1:
            vector_i = embedding_by_id.get(memory_id)
            if vector_i is not None:
                support = 0.0
                for other in hits:
                    other_memory = other["memory"]
                    other_id = str(other_memory.get("id"))
                    if other_id == memory_id:
                        continue
                    if str(other_memory.get("stream")) != "rough":
                        continue
                    vector_j = embedding_by_id.get(other_id)
                    if vector_j is None:
                        continue
                    other_overlap = len(query_terms & content_tokens(str(other_memory.get("text") or "")))
                    if other_overlap == 0:
                        continue
                    support = max(
                        support,
                        float(other["score"]) * float(cosine_similarity(vector_i, vector_j)),
                    )
                gate = min(overlap / 2.0, 1.0)
                bonus = alpha * gate * support

        rescored.append((base + bonus, memory_id, base, bonus))

    rescored.sort(key=lambda item: item[0], reverse=True)
    return rescored


def safe_mrr(rank: Optional[int]) -> float:
    return 0.0 if rank is None else 1.0 / float(rank)


def evaluate_with_alpha(
    cases: Sequence[EvalCase],
    memory_rows: Sequence[MemoryRow],
    candidate_pool: int,
    final_k: int,
    min_reliability: float,
    alpha: float,
    embedding_by_id: Dict[str, np.ndarray],
) -> dict:
    baseline_hits = {1: 0, 3: 0, 5: 0}
    rerank_hit_counts = {1: 0, 3: 0, 5: 0}
    pool_coverage = 0
    baseline_mrr = 0.0
    rerank_mrr = 0.0
    case_rows: List[dict] = []

    for case in cases:
        hits = retrieve_step1(
            rows=memory_rows,
            query=case.query,
            session_id=case.session_id,
            candidate_pool=candidate_pool,
            min_reliability=min_reliability,
        )
        baseline_ids = [str(item["memory"].get("id")) for item in hits]
        reranked = rerank_hits(hits, case.query, embedding_by_id, alpha)
        rerank_ids = [item[1] for item in reranked]

        baseline_rank = rank_lookup(baseline_ids, case.gold_id)
        rerank_rank = rank_lookup(rerank_ids, case.gold_id)

        if baseline_rank is not None:
            pool_coverage += 1

        for cutoff in (1, 3, 5):
            if cutoff > final_k:
                continue
            if baseline_rank is not None and baseline_rank <= cutoff:
                baseline_hits[cutoff] += 1
            if rerank_rank is not None and rerank_rank <= cutoff:
                rerank_hit_counts[cutoff] += 1

        baseline_mrr += safe_mrr(baseline_rank)
        rerank_mrr += safe_mrr(rerank_rank)

        case_rows.append(
            {
                "query": case.query,
                "session_id": case.session_id,
                "gold_id": case.gold_id,
                "support_id": case.support_id,
                "baseline_rank": baseline_rank,
                "rerank_rank": rerank_rank,
                "overlap": case.overlap,
                "gold_text": case.gold_text,
                "support_text": case.support_text,
                "baseline_top3": baseline_ids[:3],
                "rerank_top3": rerank_ids[:3],
            }
        )

    total = max(len(cases), 1)
    return {
        "total_cases": len(cases),
        "pool_coverage": pool_coverage,
        "baseline_hits": baseline_hits,
        "rerank_hits": rerank_hit_counts,
        "baseline_mrr": baseline_mrr / total,
        "rerank_mrr": rerank_mrr / total,
        "rows": case_rows,
    }


def format_rate(value: float) -> str:
    return f"{value:.1%}"


def print_summary(results: dict, final_k: int, alpha: float, show_cases: int) -> None:
    total_cases = results["total_cases"]
    pool = results["pool_coverage"]
    print(f"\n{'='*60}")
    print(f"Step1+Step2  |  alpha={alpha}  |  pool={pool}/{total_cases} gold-present")
    print(f"{'='*60}")
    print(f"step1-only MRR : {results['baseline_mrr']:.3f}")
    print(f"step1+step2 MRR: {results['rerank_mrr']:.3f}")
    print(f"step1-only Hit@1: {results['baseline_hits'][1]}/{total_cases} ({format_rate(results['baseline_hits'][1] / max(total_cases, 1))})")
    print(f"step1+step2 Hit@1: {results['rerank_hits'][1]}/{total_cases} ({format_rate(results['rerank_hits'][1] / max(total_cases, 1))})")
    for cutoff in (3, 5):
        if cutoff > final_k:
            continue
        print(
            f"step1-only Hit@{cutoff}: {results['baseline_hits'][cutoff]}/{total_cases} "
            f"({format_rate(results['baseline_hits'][cutoff] / max(total_cases, 1))})"
        )
        print(
            f"step1+step2 Hit@{cutoff}: {results['rerank_hits'][cutoff]}/{total_cases} "
            f"({format_rate(results['rerank_hits'][cutoff] / max(total_cases, 1))})"
        )

    interesting = [
        row for row in results["rows"]
        if (row["baseline_rank"] or 999) != (row["rerank_rank"] or 999)
    ]
    print(f"\nillustrative cases (rank changed between step1 and step1+step2):")
    for row in interesting[:show_cases]:
        delta = (row["baseline_rank"] or 999) - (row["rerank_rank"] or 999)
        sign = "+" if delta > 0 else ""
        print(f"  query: {row['query'][:100]}")
        print(f"  gold: {row['gold_id']} | overlap={row['overlap']}")
        print(f"  step1 rank: {row['baseline_rank']} | step1+step2 rank: {row['rerank_rank']} ({sign}{delta})")
        print(f"  step1 top3: {row['baseline_top3']}")
        print(f"  step1+2 top3: {row['rerank_top3']}")
        print()


def main() -> int:
    args = parse_args()
    started = time.time()

    live_db = Path(args.live_db).expanduser()
    run_root = Path(args.run_root).expanduser()

    shadow_home = run_root / "shadow-home"
    output_dir = run_root / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    shadow_db = shadow_home / "out" / "memories" / "memory_store.db"
    (shadow_home / "out" / "memories").mkdir(parents=True, exist_ok=True)
    shutil.copy2(live_db, shadow_db)

    alpha_values = [float(a.strip()) for a in args.alpha_values.split(",") if a.strip()]

    print(f"loading shadow DB: {shadow_db}")
    rows = load_rows(shadow_db)
    if not rows:
        raise RuntimeError("no embedded memories found in the shadow store")

    print(f"mining eval cases (sample={args.sample_size}, max_turn_gap={args.max_turn_gap})...")
    cases = mine_eval_cases(
        rows=rows,
        sample_size=args.sample_size,
        min_overlap=args.min_overlap,
        max_turn_gap=args.max_turn_gap,
    )
    if not cases:
        raise RuntimeError(
            "could not mine any evaluation cases; "
            "try lowering --min-overlap or increasing --max-turn-gap"
        )
    print(f"mined {len(cases)} evaluation cases")

    embedding_by_id = {row.id: row.embedding for row in rows}

    from app.embedding.embedder import embed
    try:
        embed([cases[0].query])
    except Exception as exc:
        raise RuntimeError(
            "query embedding is unavailable. Start the configured embedding backend first, then rerun."
        ) from exc

    best_alpha = None
    best_alpha_result = None
    all_results: List[dict] = []

    for alpha in alpha_values:
        print(f"\n[alpha={alpha}] evaluating...")
        results = evaluate_with_alpha(
            cases=cases,
            memory_rows=rows,
            candidate_pool=args.candidate_pool,
            final_k=args.final_k,
            min_reliability=0.0,
            alpha=alpha,
            embedding_by_id=embedding_by_id,
        )
        print_summary(results, args.final_k, alpha, args.show_cases)

        result_entry = {
            "alpha": alpha,
            "total_cases": results["total_cases"],
            "pool_coverage": results["pool_coverage"],
            "pool_coverage_rate": results["pool_coverage"] / max(results["total_cases"], 1),
            "baseline_mrr": results["baseline_mrr"],
            "rerank_mrr": results["rerank_mrr"],
            "baseline_hit_at_1": results["baseline_hits"][1],
            "rerank_hit_at_1": results["rerank_hits"][1],
            "baseline_hit_at_3": results["baseline_hits"][3],
            "rerank_hit_at_3": results["rerank_hits"][3],
            "baseline_hit_at_5": results["baseline_hits"][5],
            "rerank_hit_at_5": results["rerank_hits"][5],
        }
        all_results.append(result_entry)

        if best_alpha_result is None or results["rerank_mrr"] > best_alpha_result["rerank_mrr"]:
            best_alpha = alpha
            best_alpha_result = results

    duration = time.time() - started

    print(f"\n{'='*60}")
    print(f"BEST RESULT: alpha={best_alpha}")
    print(f"gold-present rate: {best_alpha_result['pool_coverage']}/{best_alpha_result['total_cases']} ({format_rate(best_alpha_result['pool_coverage'] / max(best_alpha_result['total_cases'], 1))})")
    print(f"step1-only MRR : {best_alpha_result['baseline_mrr']:.3f}")
    print(f"step1+step2 MRR: {best_alpha_result['rerank_mrr']:.3f}")
    print(f"step1-only Hit@1: {best_alpha_result['baseline_hits'][1]}/{best_alpha_result['total_cases']}")
    print(f"step1+step2 Hit@1: {best_alpha_result['rerank_hits'][1]}/{best_alpha_result['total_cases']}")

    payload = {
        "experiment": "step1_step2_combined",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration, 2),
        "run_root": str(run_root),
        "shadow_db": str(shadow_db),
        "live_db": str(live_db),
        "config": {
            "sample_size": args.sample_size,
            "candidate_pool": args.candidate_pool,
            "final_k": args.final_k,
            "min_reliability": 0.0,
            "alpha_values": alpha_values,
            "best_alpha": best_alpha,
        },
        "cases_mined": len(cases),
        "alpha_sweep": all_results,
        "best": {
            "alpha": best_alpha,
            "pool_coverage": best_alpha_result["pool_coverage"],
            "total_cases": best_alpha_result["total_cases"],
            "baseline_mrr": best_alpha_result["baseline_mrr"],
            "rerank_mrr": best_alpha_result["rerank_mrr"],
            "baseline_hit_at_1": best_alpha_result["baseline_hits"][1],
            "rerank_hit_at_1": best_alpha_result["rerank_hits"][1],
            "baseline_hit_at_3": best_alpha_result["baseline_hits"][3],
            "rerank_hit_at_3": best_alpha_result["rerank_hits"][3],
            "baseline_hit_at_5": best_alpha_result["baseline_hits"][5],
            "rerank_hit_at_5": best_alpha_result["rerank_hits"][5],
            "example_cases": [
                {
                    "query": row["query"],
                    "gold_id": row["gold_id"],
                    "support_id": row["support_id"],
                    "overlap": row["overlap"],
                    "baseline_rank": row["baseline_rank"],
                    "rerank_rank": row["rerank_rank"],
                    "baseline_top3": row["baseline_top3"],
                    "rerank_top3": row["rerank_top3"],
                }
                for row in best_alpha_result["rows"]
                if (row["baseline_rank"] or 999) != (row["rerank_rank"] or 999)
            ][: args.show_cases],
        },
    }

    artifact_path = output_dir / "step1_step2_results.json"
    artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nresults written to: {artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
