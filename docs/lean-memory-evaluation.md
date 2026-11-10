# Lean memory experiment evaluation

`tools/benchmarks/lean_memory_eval.py` compares two Titan source checkouts through
`retrieve_memory_brief` and `get_scene_context`, backed by real disposable SQLite
repositories in two agent namespaces. Capture checks use
`run_memory_pipeline_outcome`: actual admission, extraction JSON parsing,
sanitization, embedding, and storage, with scripted extraction responses.
It does not call private ranking helpers or replace the storage adapter.

The baseline is a frozen sibling checkout, including its original dirty changes.
Every target/Step2 variant starts in a fresh subprocess and temporary `TITAN_HOME`.
The child does not inherit provider keys or Titan configuration paths, sets
`PYTHONDONTWRITEBYTECODE=1`, disables JSON fallback, and blocks network connections.
The report records hashes of runtime source files before/after execution and of
seed vectors. The benchmark never writes into either source checkout.

## Frozen cases

`tests/fixtures/lean_memory_cases.json` was constructed from fictional facts before
implementation conclusions. Its SHA256 is
`62a75dc2d9a75887d402bff0b1af77584ef6d7c4c7ae7785e586c9387904f188`.
The runner refuses a changed fixture unless its versioned digest is deliberately
updated. The fixture is not an extracted audit corpus or a set of self-vector
queries.

The 13 queries cover factual and paraphrased recall, explanation preferences,
database/cache facts from the same scene/event, a changed deployment decision,
negative preference, two similar API-port facts for different projects, personal
versus workplace language scope, identical claims supported by two scenes, a
budget's date/approver, and an unknown passport number. Five capture cases cover
polite durable saves, direct durable saves, courtesy only, transport only, and a
budget decision. Four malformed extraction responses test explicit schema errors.

## Reproduction

From the experiment checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/benchmarks/lean_memory_eval.py \
  --baseline ../titan-baseline --experiment . \
  --ablations default,on,off --repeats 5 \
  --output /tmp/titan-lean-synthetic.json

# Explicit opt-in: real embeddings from the local Ollama process only.
PYTHONDONTWRITEBYTECODE=1 python3 tools/benchmarks/lean_memory_eval.py \
  --baseline ../titan-baseline --experiment . \
  --embedding local --embedding-model nomic-embed-text:v1.5 \
  --ablations default,on,off --repeats 3 \
  --output /tmp/titan-lean-local.json

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_lean_memory_eval
```

`--embedding local` allows only HTTP literal loopback addresses. It makes actual
local embedding calls on each native provider invocation, with timings including
those calls. It does not use a shared vector cache or a remote extraction model.
`--local-embedding-url` can explicitly select another loopback Ollama port.
The first model load and fixture seed time are separate from query timings.

By default the runner records quality failures and exits zero so both snapshots
can be measured. `--require-all-pass` turns all case gates into an exit status;
subprocess/runtime errors always exit nonzero. Use `--baseline ''` or
`--experiment ''` for a single-checkout run.

## What is measured

- Evidence precision and recall use unique memory identities, including alternate
  `evidence_refs`, against the frozen expected facts. Extra off-topic results are
  false positives. Display precision is also recorded per case.
- Provenance retention requires the expected source agent, memory ID, scene ID,
  event lineage, original supporting scene text, and returned scene pointer. Alternate pointer event lineage is
  resolved through the public `get_scene_context` path.
- A query passes only with every expected fact/provenance item, no extra facts,
  a valid public result schema, and identical repeat output. Unknown-answer
  queries must abstain. Expected negative preferences and corrections remain
  full-text facts; the evaluator does not generate answers from them.
- Capture passes require the expected admission decision and saved-record
  presence. Scripted extraction deliberately returns an item even for noise;
  this checks the admission boundary rather than a real model's discretion.
- Invalid JSON/schema must raise `ExtractionError`. An incidental `AttributeError`
  or silently empty result fails the gate.
- Retrieval counts include native embedding provider invocations, number/bytes of embedded
  inputs, serialized response bytes, SQLite bytes after capture, and case counts. Capture embedding calls and total calls including seeding
  are reported separately. Import/bootstrap, fixture seeding, first query per case, steady query p50/p95,
  and evidence resolution times are separate. The report contains exact full
  public responses and briefs; that public API does not expose ranking scores.

## Limits

The default embeddings are deterministic synthetic topic/word vectors with tiny
lexical tie breakers. They explicitly encode semantic labels to isolate ranking,
selection, grouping, and provenance behavior. They cannot establish production
semantic retrieval quality.

The optional local mode uses genuine `nomic-embed-text:v1.5` embeddings for the
same frozen texts and queries. It is still only 13 queries over 14 fictional
records. Local timings are small, sequential samples with a warmed model, not a
load test or a production claim. Both snapshots must report the same seed-vector
hash for a direct comparison. No model-based answer generation, long-horizon
capture, passive adapter, or real user corpus is evaluated. Scene seeds preserve
legacy partial evidence; this suite does not establish complete raw-event capture.
The separate runtime/storage/capture test suites cover those contracts.

## Final frozen-source run: 11 November 2026

Raw reports are intentionally outside the source checkout:
`../titan-evaluation-synthetic.json` and `../titan-evaluation-local.json`.
Each includes the exact public response, brief, expected/returned IDs, alternate
evidence IDs, scene references, and failure reason for every case.
All 12 workers reported unchanged runtime source hashes, no blocked network
attempts, and disabled bytecode writes. Runtime hashes were:

- Baseline: `e4e6dd6cb4363cb9fa3150452fc36f4d60ac38f10d5205127af10aee200d6daa`
- Experiment: `4079239edfecbea1a245d1846ddf5a864f8183d25ac02e92b76ddd0bb0ab8e1c`

All six local runs produced the same seed-vector SHA256:
`e8287e29588f5fd33a4f56417bff1291b1f0f5ea6c78bdbddf391233c6b21e4f`.
The synthetic seed hash was also identical across its six variants.

### Real local embeddings

Each variant performed 52 public queries: one first request plus three repeated
requests for each of 13 cases. Steady latency uses the 39 repeated requests and
includes actual loopback embedding calls. Baseline default has Step2 **on**;
experiment default has Step2 **off**.

| Checkout / Step2 | TP / FP / FN | Precision | Recall / provenance | Query passes | Steady p50 / p95 ms |
| --- | --- | --- | --- | --- | --- |
| Baseline default | 12 / 5 / 2 | 0.706 | 12/14 | 10/13 | 25.23 / 40.76 |
| Baseline on | 12 / 5 / 2 | 0.706 | 12/14 | 10/13 | 23.61 / 37.66 |
| Baseline off | 12 / 5 / 2 | 0.706 | 12/14 | 10/13 | 25.28 / 37.85 |
| Experiment default | 14 / 4 / 0 | 0.778 | 14/14 | 11/13 | 20.56 / 39.72 |
| Experiment on | 14 / 4 / 0 | 0.778 | 14/14 | 11/13 | 26.03 / 41.98 |
| Experiment off | 14 / 4 / 0 | 0.778 | 14/14 | 11/13 | 19.21 / 41.72 |

The experiment recovers both database/cache facts from the shared scene and
preserves both supporting scene pointers for the duplicate deadline text.
Within each checkout, switching Step2 does not change quality counts in these
cases. Experiment on/off median latency is consistent with some attention
work being removed, but the sample is too small and host load too variable for
a robust speedup claim. Baseline on/off timing does not show a consistent median
improvement, and p95 varies.

Two experiment queries still fail:

- The deadline/source query retains both expected pieces of provenance but also
  returns two unrelated hosting facts. It has TP=2, FP=2, FN=0.
- The unknown passport query returns Neovim and a personal Python preference.
  It has TP=0, FP=2, FN=0. **Abstention is 0/1 in both checkouts.**

The suite therefore does not pass `--require-all-pass`. Perfect expected-evidence
retention in this tiny fixture is not perfect retrieval precision or abstention.

### Synthetic behavior fixtures

All Step2 variants have the same quality counts per checkout. Baseline has
TP=12, FP=2, FN=2; precision/recall/provenance=0.857, 11/13 query passes.
Experiment has TP=14, FP=2, FN=0; precision=0.875, recall/provenance=1.0,
12/13 query passes. The deadline/source query still returns two off-topic
fill hits. Both checkouts abstain in the synthetic unknown-answer case, unlike
real local embeddings: this is a concrete reason not to claim semantic quality
from synthetic fixtures.

Synthetic default steady p50/p95 was 5.88/9.22 ms for baseline and
8.36/19.66 ms for experiment. The experiment was slower in that sample.
Its separately measured Step2 off sample was 4.44/6.49 ms. This variance, including
concurrent validation activity on the host, limits timing conclusions.

### Capture, schema, calls, and bytes

All six variants per embedding mode yield the same capture/schema decisions:

| Gate | Baseline | Experiment |
| --- | --- | --- |
| Frozen capture cases | 4/5 | 5/5 |
| Explicit malformed-schema rejection | 0/4 | 4/4 |
| Public response schema errors | 0 | 0 |
| Identical outputs across repeated queries | 13/13 | 13/13 |

The baseline drops the polite durable Neovim save. Its malformed JSON/missing
list/non-list cases are silently accepted, while non-text raises an incidental
`AttributeError`; none satisfy the explicit schema gate. The experiment rejects
all four with `ExtractionError`.

| Default-variant measurement | Baseline | Experiment |
| --- | --- | --- |
| Local retrieval embedding calls / input texts | 52 / 68 | 52 / 68 |
| Local retrieval embedded input bytes | 3,264 | 3,264 |
| Save/schema embedding calls | 5 | 3 |
| Local total embedding calls including seed | 58 | 56 |
| Local serialized response bytes over 52 queries | 74,836 | 73,668 |
| Local SQLite bytes after save/schema cases | 421,888 | 421,888 |
| Synthetic retrieval embedding calls / input texts | 78 / 102 | 78 / 102 |
| Synthetic serialized response bytes over 78 queries | 96,630 | 101,550 |
| Synthetic SQLite bytes after save/schema cases | 360,448 | 360,448 |

Save/schema calls include malformed-extractor checks: the baseline performs
three unnecessary embedding calls after invalid responses, whereas the
experiment saves one additional legitimate polite preference. Retrieval call
counts are unchanged; the frozen baseline already contains query-vector reuse.
There is no demonstrated SQLite space saving. Payload size depends on both
provenance pointers and Step2 rendering: in the local Step2-off comparison,
baseline is 72,056 bytes and experiment is 73,668 bytes.

Local default import/bootstrap was 221.75 ms versus 200.15 ms; seed/provider/store
setup was 212.76 ms versus 178.38 ms. These are one bootstrap sample per fresh
process, separate from warmed query timings. The evaluator's seven focused tests,
including its isolated public-path subprocess smoke test, pass.
