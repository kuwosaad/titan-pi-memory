# Titan efficiency implementation and comparison — 2026-09-22

Status: implementation integrated, regression suite passed, and cache-matched
before/after measurements completed.

## Scope

Comparison baseline: `ee6d6345e17bb0cdece53c1f5599ad88c7b0fa85`, after the earlier cleanup.
No deployments, model switches, or dependency removals were made in this pass.

## Implemented

1. **Lean SQLite retrieval reads.** Rank using only required fields and raw
   embedding blobs; fetch full legacy/detail fields for selected winners.
   Public repository candidate methods retain their original full payload.
   The candidate universe, filters, lexical/semantic session fallback, scores,
   order, and selection rules are unchanged. JSON storage is unchanged.
2. **Request-local query embeddings.** Federated recall reuses the exact
   query/aspect batch for matching provider configurations. It does not cache
   across requests or cache missing-candidate embeddings, failed calls, or
   malformed responses. OpenAI response indices are validated and ordered so
   query aspects cannot receive another input's vector.
3. **Batched scene references.** One lookup per healthy source replaces one
   lookup per scene. Global encounter order, source-qualified identity, evidence
   metadata, and per-scene failure fallback remain intact.
4. **Unchanged EOF cursor fast path.** After existing replacement/truncation
   validation, an unchanged fully consumed spool avoids empty reads and cursor
   rewrites. Full pipeline processing still runs for retries and pending-scene
   recovery. Legacy cursors are upgraded before the fast path is used.
5. **On-demand optional imports.** HTTP/MCP startup retains registered routes,
   tools, and request schemas without importing the heavy graph/pattern stack.
   Actual graph/pattern calls load their implementations normally. NetworkX and
   NumPy remain installed dependencies; this is a residency/startup change.

Duplicate/opposition/diversity algorithms, process sharing, stored embeddings,
public HTTP payload shapes, extraction models, and durability settings were not
changed. There is no approximate search or new persistent result cache.

## Verification design

The verification boundaries were retrieval, save/recovery, and startup interfaces.
Existing regression tests and focused new
cases cover full SQLite result hydration, early-return fallbacks, provider
identity and malformed responses, scene-reference order, spool replacement and
recovery, and first invocation of lazily loaded features.

The artifact-isolation test now invokes the actual optional Cortex tool instead
of expecting tool listing to eagerly import it. A malformed installed artifact
must still fail that invocation without borrowing the module from the source
checkout, and the exact artifact gate must still reject the missing file.

Final combined suite: **649 passed, 132 subtests passed**. The sole warning is
the existing Starlette/httpx deprecation. A subsequently added benchmark-cache
guard test also passed separately; no runtime source changed after the full
suite. An AST comparison also confirms that
nine duplicate/opposition/diversity/federation-merge function bodies are
unchanged from `ee6d634`.

Benchmark tooling: `tools/benchmarks/efficiency_comparison.py`.
The raw benchmark artifacts and test report were kept outside the repository.

The benchmark uses an untouched baseline worktree, frozen baseline settings,
fresh isolated processes, and nine alternating before/after pairs. Local
fixtures match the previous 1,000/5,000-record, 768-dimensional synthetic
workload. Query vectors are deterministic; no model endpoint is called.
Native numerical-library threads are limited to one for both versions.

Both measured source worktrees have no project Python bytecode caches and use
the same virtual environment/dependency caches. The candidate source is a copy
of the integrated worktree with bytecode excluded; source fingerprints must
match. `PYTHONDONTWRITEBYTECODE=1` prevents new caches during the run.

An earlier run is exploratory,
not authoritative: the original candidate checkout had valid project bytecode
while the fresh baseline did not. This could bias cold imports and retained
process RSS even after query warmup. The whole comparison was rerun with matched
cache conditions rather than reporting those potentially inflated gains.

Environment: macOS, Python 3.11.11, NumPy 2.4.6, SQLite 3.47.1.
Competing host activity was not controlled.

Each measured retrieval result is compared structurally, including IDs, order,
text, scores, source/scene metadata, legacy fields, and embedding bytes. Float
values alone allow a tolerance of `1e-12`; byte/array contents are fingerprinted
exactly. The harness rejects source changes during a run. It excludes unrelated
personal files from source fingerprints.

## Results

### Retrieval: nine cache-matched process pairs per workload

Each process executes five warmups and 25 measured queries. Percentages below
are the median of the nine paired changes, not a division of separately computed
before/after medians. A win means the candidate used less CPU or peak RSS in that
pair. These are directional local measurements, not confidence intervals.
The planned headline threshold was at least 8/9 wins plus a median reduction
of at least 10% for CPU/wall time or 5 MiB for RSS; smaller/noisier observations
are still disclosed below, but not treated as strong performance wins.

| Workload | Median CPU/query, before → after | Paired CPU change; wins | Median peak RSS, before → after | Paired peak RSS change; wins |
| --- | --- | --- | --- | --- |
| Local, 1,000 memories | 141.25 → 133.95 ms | −6.4%; 8/9 | 99.98 → 86.91 MiB | −13.4%; 9/9 |
| Local, 5,000 memories | 405.57 → 354.24 ms | −11.8%; 9/9 | 130.63 → 113.33 MiB | −12.6%; 9/9 |
| Three sources, 1,000 total memories | 92.96 → 87.87 ms | −4.0%; 6/9 | 72.72 → 72.38 MiB | −0.4%; 9/9 |
| Three sources, 5,000 total memories | 680.83 → 577.63 ms | −19.9%; 6/9 | 84.02 → 81.58 MiB | −3.0%; 8/9 |

| Workload | Median wall time/query, before → after | Paired wall-time change; wins |
| --- | --- | --- |
| Local, 1,000 memories | 142.05 → 134.77 ms | −9.8%; 8/9 |
| Local, 5,000 memories | 407.78 → 360.61 ms | −11.3%; 8/9 |
| Three sources, 1,000 total memories | 93.35 → 89.41 ms | −4.0%; 6/9 |
| Three sources, 5,000 total memories | 700.97 → 581.70 ms | −22.9%; 6/9 |

The clearest findings are lower local-process RAM and lower CPU cost for the
larger local workload. The smaller local CPU improvement is modest. Federated
timing is inconsistent across pairs: its larger median percentage is **not** a
reliable speedup claim. Its RAM reduction is also small in absolute terms.

For both corpus sizes, each three-source query made **one embedding-provider
call instead of three**: 25 instead of 75 calls per measured process, in every
pair. This is a structural reduction in repeated model work; the deterministic
stub cannot establish real-provider latency or RAM savings.

### Startup and the first MCP query handler

These percentages use the same paired calculation as the retrieval table.

| Fresh-process target | Paired CPU change; wins | Paired wall-time change; wins | Median peak RSS, before → after | Paired peak RSS change; wins |
| --- | --- | --- | --- | --- |
| Storage module import | +0.6%; 3/9 | +2.4%; 3/9 | 54.31 → 54.63 MiB | +0.6%; 2/9 |
| Retriever module import | +0.9%; 4/9 | +0.9%; 4/9 | 65.17 → 65.50 MiB | +0.2%; 2/9 |
| MCP server import | −16.9%; 9/9 | −17.2%; 8/9 | 99.72 → 87.08 MiB | −12.7%; 9/9 |
| HTTP entrypoint import | −18.8%; 9/9 | −19.1%; 9/9 | 86.98 → 74.44 MiB | −14.4%; 9/9 |
| First MCP query handler, after import | −0.2%; 5/9 | +0.2%; 3/9 | 112.27 → 99.03 MiB | −11.8%; 9/9 |

Storage/retrieval imports have tiny increases, not an optimization win. The
meaningful import gains are at the HTTP/MCP service entrypoints: MCP loaded
755 new modules instead of 1,059; HTTP loaded 516 instead of 821. NetworkX
was absent after candidate service startup and the first ordinary MCP query.

The first query handler itself is effectively unchanged in speed; its slightly
higher paired wall time is not evidence of a material regression. The import
segment preceding that handler improved by 14.9% CPU and 13.2% wall time, with
8/9 wins each. Optional-feature first-use cost is deferred, not eliminated.
The first-handler RSS row measures whole-process peak residency after import
and invocation, not incremental memory used or saved by the handler itself.

### Targeted work reductions

| Operation | Median CPU, before → after | Paired CPU reduction; wins | Structural change |
| --- | --- | --- | --- |
| Scene references: four sources, 100 existing + one missing ID each | 190.27 → 9.99 ms | 95.7%; 9/9 | 404 lookups → 4 per operation |
| Unchanged EOF spool tick | 3.22 → 1.51 ms | 56.3%; 9/9 | One cursor save → zero per tick |

Paired wall-time reductions were 95.7% and 57.0%, respectively, with 9/9 wins.
These are deliberately targeted stress/microbenchmarks, not overall application
speedups. Normal scene requests may contain far fewer IDs; the cursor test does
not measure all work performed by an idle ingestion worker.
Neither microbenchmark showed a meaningful RAM reduction: scene peak RSS was
67.78 → 67.78 MiB (paired +0.02%, 4/9 wins); idle peak RSS was 64.91 → 64.86 MiB
(paired +0.05%, 4/9 wins).

### Run integrity and reproduction

The authoritative run exited successfully with **198 process observations**:
99 before/after pairs across 11 workload groups. Retrieval alone covered 1,800
measured logical queries, plus 360 warmups. All measured result comparisons
passed, including full retrieval payloads and source-qualified scene references.
Both worktrees remained bytecode-free; their source fingerprints remained fixed,
and the candidate fingerprint still matched the canonical checkout afterward.
An independent measurement reviewer checked the raw results, arithmetic,
equivalence, source/cache guards, and report claims and found no blocker.

```bash
.venv/bin/python tools/benchmarks/efficiency_comparison.py run \
  --baseline-root <baseline-checkout> \
  --candidate-root <candidate-checkout> \
  --artifact-dir <new-artifact-directory> \
  --counts 1000 5000 --trials 9 --queries 25 --warmups 5
```

Use a new, nonexistent artifact directory when repeating the command. The initial
cache-matched attempt stopped before recording observations because the sandbox
denied `/bin/ps`; the successful run had permission to inspect its test processes'
RSS. The raw JSON, summary, manifest, frozen settings, and synthetic fixtures are
outside Git. The one-pair smoke and cache-confounded run are not used for final
performance claims. No stable, material end-to-end regression was established;
neither was a reliable federated or first-handler speedup.

## Interpretation limits

- CPU time per operation is not Activity Monitor CPU percentage. Percentage
  reductions compare the same operation before and after, not whole-machine load.
- RSS is process residency, including shared/mapped pages. It excludes the
  embedding/extraction model and is not private-memory accounting.
- The provider is a deterministic stub. Fewer provider requests are measurable;
  real Ollama/API latency, resource use, and cost savings are not measured here.
- Scene-reference stress measurements and blank-spool EOF microbenchmarks are
  targeted operations, not overall retrieval or full-worker idle measurements.
- Local/federated timings exclude result normalization. Scene and cursor
  microbenchmarks include common harness normalization; cursor timings also
  include cursor-file hashing.
- Local retrieval retains the previous benchmark's `entrypoints.main` import
  footprint. The federated microbenchmark imports retrieval directly; compare
  each workload only against its own baseline, not their absolute RAM to each
  other. The first MCP query invokes its Python handler directly, excluding
  protocol transport and client latency. Startup means a fresh process, not a
  cold operating-system filesystem cache.
- This is a working laptop, not a controlled performance lab. Process pairs,
  not individual repeated queries, are the independent observations.
- Lazy loading can defer work to the first optional-feature call. It does not
  eliminate that feature's eventual memory requirement.
- Runtime checks were performed on this macOS machine, not a Windows/Linux or
  low-RAM hardware matrix.

## Previous cleanup results — historical, not added to these deltas

The earlier `bbb454b` to `ee6d634` experiment reported median query CPU savings
of 32.6% at 1,000 records and 27.5% at 5,000 records, and peak process RSS savings
of 4.8% and 7.5%. Those measured removal of the old active-store work, including
LNN writes. They are a different comparison and must not be summed with this
pass's percentages. The earlier larger-store measurements were noisy.

Absolute timing also moved substantially between experiments: the earlier
`ee6d634` post-cleanup CPU medians were 77.67 ms (1,000 records) and 364.09 ms
(5,000), versus this run's `ee6d634` baseline medians of 141.25 and 405.57 ms.
Cross-run host state and benchmark context were not controlled. Only the new
within-run paired comparisons support this pass's performance deltas; comparing
its candidate directly to the old run would be misleading.

The earlier benchmark artifacts were kept outside the repository.

## Remaining risks

Winner hydration is a second SQLite read. An exceptional database replacement
or deletion between selection and hydration now raises rather than returning
an incomplete record; ordinary append-only operation is covered by the tests.

The review also identified pre-existing spool-cleanup concerns: unresolved
retries can lose automatic rediscovery, and append-versus-unlink has a race.
This optimization does not change those cleanup paths; it preserves recovery
processing rather than adding a broader skip gate around them.

Separate deduplication changes were outside this comparison; combined behavior
still requires integration testing.
