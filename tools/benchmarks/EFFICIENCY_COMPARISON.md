# Titan efficiency comparison

This harness compares an exact clean `ee6d634` checkout with one candidate
checkout in alternating fresh processes. It uses synthetic SQLite fixtures and a
clearly labeled deterministic embedding stub; it never calls a model or reads a
live Titan namespace. All fixtures, state, and results are written below the
required external artifact directory.

Use two fresh source worktrees with no `app/` or `entrypoints/` Python bytecode
caches, and the same dependency virtual environment for both. The harness rejects
project `.pyc` files before and after measurement. `PYTHONDONTWRITEBYTECODE=1`
alone is insufficient: Python still reads valid existing caches, which can bias
startup time and retained RAM. Copy candidate source without `__pycache__`/`*.pyc`;
do not delete caches from the user's working checkout to prepare a benchmark.

Smoke test (one process pair and the 1,000-row corpus):

```bash
python3 /path/to/efficiency_comparison.py run \
  --baseline-root /path/to/clean-ee6d634 \
  --candidate-root . \
  --artifact-dir /path/outside/git/titan-efficiency-smoke \
  --counts 1000 --trials 1 --queries 3 --warmups 1 --target-iterations 2
```

Directional final comparison:

```bash
python3 /path/to/efficiency_comparison.py run \
  --baseline-root /path/to/clean-ee6d634 \
  --candidate-root . \
  --artifact-dir /path/outside/git/titan-efficiency-final \
  --counts 1000 5000 --trials 9 --queries 25 --warmups 5
```

`results.jsonl` contains raw process observations, `summary.json` reports paired
candidate/baseline ratios, and `manifest.json` records source snapshots, frozen
settings, fixture identity, and interpretation limits. A behavioral mismatch
fails the run with its first result path. Float comparisons alone allow absolute
and relative tolerance `1e-12`; keys, ordering, IDs, text, bytes, arrays, public
payloads, hydrated hit state, scene references, and cursor semantics are exact.

The 25 within-process queries are repeated measurements, not 25 independent
samples. The process pair is the independent unit. Nine pairs remain a
directional local result: use the paired ratios and same-direction count, treat
p95 as descriptive, and label RSS as coarse process residency. Do not combine
these deltas with the older `bbb454b` to `ee6d634` cleanup percentages or claim
live-provider/model latency or RAM savings.

The cursor result is a synthetic blank-spool `ingest_spool_file` microbenchmark,
not a measurement of the full background worker at idle.
