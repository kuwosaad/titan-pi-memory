# Titan cleanup audit — 2026-09-22

## Decision

The authorized cleanup is now implemented on
`cleanup/core-pipeline-audit-2026-09-22`, uncommitted. The original audit below is
retained as the reasoning record; its file references describe the pre-cleanup base.

Removed the retired dedup machinery, false automatic verification, unreachable code,
unused configuration, obsolete internal helpers, LNN, and the experimental ranking
stack. The durable capture/evidence/recovery path remains intact.

Patterns, Cortex, graphs, and Markdown exports are separate product features. Some
are expensive and optional, but they are not dead code. Their retirement should be
an explicit reduction of the supported product, not disguised as a harmless cleanup.

No memories were deleted, and no commit, push, live installation, or merge was
performed. Deleted source remains recoverable from the base Git commit.

## Implementation and verification

- Retrieval now uses lexical/embedding candidates, direct query evidence, opposing
  statement preservation, duplicate collapse, and diversity/facet selection. Removed
  the Step 1/Step 2 attention/centrality/temporal/subspace stack and LNN ODE/expansion,
  state updates, and decay worker. Old `persist_lnn_state` callers remain accepted as
  a no-op; experimental per-hit diagnostic fields are intentionally retired.
- Removed the nonfunctional save-time dedup worker, its prompts/model adapter, and
  unused buffer-writing machinery. Both legacy active and processing buffers remain
  readable without changing their files. Save-time event idempotency remains.
- Removed function-name-based automatic verification: recognizing a function name
  cannot verify a claim about its behavior. New extracted claims stay unverified;
  their original source reliability is retained. Existing records are not rewritten.
- Removed the forwarding trace-intake module, unused session CRUD/models, obsolete
  clustered brief rendering, unused graph/pattern helpers, old BB 2D layout, dead
  initialization code, and the orphan experimental reranking benchmark.
- Preserved SQLite legacy columns and serialized fields, capture/replay/recovery,
  scene lineage, cross-agent identity, lexical fallback, explicit graph/cluster
  analysis, patterns, and supported adapter/package boundaries. Cluster analysis
  has its own small config section with an old `step2` config read fallback.
- Python dependency checking now respects simple Python-version markers, avoiding a
  false missing-`tomli` report on Python 3.11.

Verification so far:

- Full isolated Python suite: **630 passed, 132 subtests passed**. One upstream
  Starlette/httpx deprecation warning. Temporary Titan directories prevented tests
  from touching live memories. Localhost and cold dependency-bootstrap checks also
  passed when run outside the restricted network/socket sandbox.
- npm CLI runtime generation, runtime audit, and packed artifact gate passed
  (119 runtime files; 123 packed files).
- Pi extension Bun compilation passed. BB full TypeScript check and **38 tests**
  passed in a temporary staging copy; no live plugin build/reload was performed.
  jsdom emitted expected WebGL-unavailable warnings. Actual WebGL rendering was not
  exercised; the only UI-source change removed unused 2D layout code.
- Independent implementation reviews found no internal broken imports, worker
  lifecycle regressions, or packaging closure errors. `git diff --check` passed.
- Approximately 3,000 production/configuration lines were removed net; obsolete
  experimental tests and one benchmark were also retired. New regression tests
  cover legacy buffer reads, false verification, existing database readability,
  frozen retrieval behavior, and ignored legacy LNN query arguments.

The initial sandbox run's seven failures were environmental: four localhost socket
checks, two unprepared generated-runtime checks, and one cold dependency download.
After generating the runtime and allowing isolated socket/network checks, all passed.

Production quality remains the release gate: synthetic regression fixtures and
passing tests do **not** establish real-corpus recall, precision, or latency. This
branch is ready for review and real-query evaluation, not an automatic main merge.

The committed base and cleanup were also compared with each checkout's actual
`config/settings.yaml`, identical frozen candidate rows and synthetic embeddings,
and the same `top_k=3`/minimum-similarity overrides. All five fixtures produced
identical returned IDs (exact identifier, paraphrase, multiple aspects, opposition,
and unrelated-query abstention), including across repeated reads. This supports
preservation on that small fixture set, **not** a retrieval-quality improvement.
Both versions missed one desired facet in the multiple-aspect fixture; this is a
known limitation of that default-settings/synthetic-vector case, not a new cleanup
regression. Earlier hand-configured comparisons are excluded from quality claims.

## Scope and evidence

- Checkout: `/Users/mohammadsaad/Code/titan-pi-memory`.
- Base: `bbb454b213db7bcdb987c1abc3557ab79517c3de`.
- Created branch: `cleanup/core-pipeline-audit-2026-09-22`.
- Inventoried all 410 tracked files; ten GPT-5.6 Luna lanes inspected the major
  subsystems and their callers. This is a repository-wide architecture/removal audit,
  not a claim that every line or binary asset was exhaustively reviewed.
- Preserved the existing untracked personal research files and `.grok/` directory.
- Earlier September 7 cleanup discussions were checked against source scenes. The
  earlier LNN audit explicitly distinguished ranking changes from demonstrated
  retrieval benefit. It did not establish that LNN removal was quality-neutral.
- Current source outranks those historical reports. This checkout does not contain
  the separate personal-context implementation; changes in other worktrees and the
  currently installed Titan runtime are outside this branch's implementation scope.
- File/line references below refer to the base above, before any cleanup edits.

| Audit lane | Inspected surface | Result |
| --- | --- | --- |
| LNN | ODE rerankers, state, tick workers, schema and callers | Live but unproven ranking; unused persistent state and config |
| Retrieval | Retrieval, federation, routing, briefs, embedding | Preserve hybrid recall; remove old helpers and unused switches |
| Save | Extraction, event intake, ingestion, dedup, retry | Retired worker; orphan buffer machinery; unnecessary forwarding |
| Storage | Memory/scene repositories, traces, sessions, runtime | Protect evidence/replay; legacy session API candidate |
| Patterns | Mining, planning, storage, retrieval, sharing, APIs | Active optional product; a few dead helpers |
| Graph | Graph/Cortex/clusters and BB explorer | Shared analytics; obsolete BB 2D layout; separate browser UI |
| Interfaces | HTTP, MCP, CLI, configuration | Unreachable init implementation and dead environment loader |
| Adapters | Pi, Codex, Claude, Grok, OpenCode | Active capture paths; ignored local legacy OpenCode files |
| Packaging | Python/Pi/npm CLI, manifests, release gates, CI, assets | Preserve distribution boundaries; no large dependency cut proved |
| Evaluation | Tests, benchmarks, overnight tooling, research | See evaluation section and cleanup gates below |

## What the smaller core must still do

```text
Agent capture / explicit save
  -> durable event admission and replay protection
  -> scenes retaining source evidence
  -> extracted memories with source lineage
  -> SQLite storage and lexical / embedding candidates
  -> evidence filtering and diverse, deduplicated results
  -> source-qualified pointers and scene expansion
```

Keep cross-agent reads read-only, and writes confined to the owning agent. Keep
capture redaction, atomic spool writes, retry/recovery, permanent event IDs, scene
completeness, explicit query opt-out, date/session/stream filters, vectorless saves,
lexical fallback, and opposing-statement preservation.

## First removal batch: concrete dead or harmful machinery

### 1. Retired save-time LLM dedup worker

`app/save_pipeline/dedup_worker.py:12` returns `dedup_disabled`; its loop does no
deduplication. Nevertheless it starts from `entrypoints/main.py:62`,
`entrypoints/mcp_server.py:721`, and
`integrations/claude_titan_plugin/runtime/daemon.py:321`.

Remove the worker, startup/shutdown events, dedup-only prompt and adapter functions,
the dedup model block in `config/extraction_models.yaml:29`, and dead settings.
Update lifecycle mocks and dedup-specific tests in the same change. Preserve
save-time event/text idempotency and retrieval-time duplicate suppression.

The buffer needs a separate compatibility check: `federated.py:232` still reads it
for **recent-memory** responses. It is not a semantic-query buffer producer.
`dedup_buffer.py` has no production writer/drain caller, but historical
`dedup_buffer.jsonl` or `.processing.jsonl` may contain records. Recover or retain
read compatibility for those before removing the final reader. Do not delete old
buffer files as part of code cleanup.

### 2. Automatic code-claim verification and reliability promotion

`app/storage/verifier.py:98` verifies an entire claim by locating a function name;
`app/save_pipeline/pipeline.py:200` then promotes its reliability/status.

Reproduced on current source:

```python
CodebaseVerifier("app/graph").verify_memory(
    "The function cosine_similarity encrypts every memory with AES-256 before saving it."
)
# verified=True, confidence=0.9, method='function_search'
```

The actual function (`app/graph/similarity.py:5`) only calculates cosine similarity.
This is demonstrably false confidence, not merely an unproven feature. Remove the
automatic promotion and its recursive code-scanning machinery. Keep provenance and
verification fields, and leave new claims unverified absent real claim-specific
evidence. Existing incorrectly promoted rows require a separate evidence-aware
repair; do not blanket-rewrite stored memories in this cleanup.

### 3. Unreachable CLI and unused environment parser

- Delete `tools/cli/titan.py:3149` through the old `run_init` body after the
  unconditional `return 1`. Keep the small `init` -> `setup` guidance behavior.
- Delete uncalled `_load_env_file` at `entrypoints/mcp_server.py:28`; runtime context
  owns environment resolution.
- `patterns evidence --new` is accepted but not consumed. It can remain a cheap
  compatibility spelling while its misleading documentation is corrected; deleting
  the public option offers almost no runtime benefit.

### 4. Internal dead helpers and obsolete UI layout

| Candidate | Evidence and boundary |
| --- | --- |
| Five old `apply_*` retrieval filters | `retriever.py:220`–`256`; current filtering uses `CandidateFilters`. Keep `apply_hidden_metadata_filter` and `_memory_matches_candidate_filters`. |
| `build_scene_notes` | `retrieval_pipeline/brief.py:200`; no callers found. Preserve the live memory brief/timeline builders. |
| `namespace_memories_json_path`, unused `agents_dir` local | `federated.py:45`, `:153`; preserve namespace discovery and source qualification. |
| Legacy session-record CRUD | `storage/sessions.py:250`; no production callers outside that module. Keep its shared path, directory, locking, migration, and atomic-write utilities. |
| Pattern connection wrapper and unused timeout | `patterns/storage.py:18`, `:35`; the live `connect_pattern_db` uses shared SQLite plumbing. |
| Old BB 2D layout | `integrations/bb_titan_memory_plugin/src/ui/model.ts:45`, `:133`; production imports the 3D implementation from `graph-layout.ts`. Remove only the obsolete layout and its dedicated tests. |
| Unused import/allocation | `save_pipeline/auto_ingest.py:8` (`FastAPI`); `graph/builder.py:85` (`facts`). |

These are in-repository reachability findings. Public exported aliases deserve a
small compatibility decision; an imagined external caller should not keep every
private helper forever.

### 5. Configuration with no implementation reader

Remove after a final repository-wide reference check in the implementation diff:

- `retrieval_default_window_days`, `entity_min_lexical_coverage`,
  `entity_direct_similarity_override`.
- `step2.cluster_llm_synthesis`, `step2.cluster_max_clusters`.
- `step1.multi_head_query_decompose`, `step1.sequence_score_weight`.
- LNN `ode_method`, `activation_default`, `activation_floor`,
  `tau_contradiction_penalty`, `weights_init_sigma`,
  `inhibitory_by_contradiction`, `seed_top_n`, `final_top_k`.

References: `config/settings.yaml:54`, `:88`, `:227`, `:246`, `:304`, `:341` onward.
Do not remove `debug_activation_trace`: the expansion branch actually reads it.

### 6. Simplify the trace forwarding round trip

Current public function -> singleton `TraceIntake` method -> private implementation
round trip appears at `pipeline.py:2152` and `trace_intake.py:34`. It adds little
current isolation. Collapse it while retaining the public function signatures.

**Required correction to the initial lane finding:** this is not entirely a no-op
wrapper. `_resolve_spool_dir` at `trace_intake.py:21` maps the old default to the
agent's configured spool. Preserve that behavior when removing the class/file, or
capture may go to the wrong namespace. Update onboarding package-file assertions.

## Second batch: retire ranking experiments without guessing about quality

### LNN is the largest clear target, but not dead code

- Default-enabled LNN takes over reranking at `retriever.py:1590`.
- `_ode_settle` and its rerankers occupy `retriever.py:1180`–`1570`; they evolve
  candidate scores and persist tau/edge changes. This is real behavior.
- LNN returns before the alternative Step 2 rerankers. Disabling LNN alone activates
  another complex ranking path; it does not produce a simple semantic baseline.
- The worker decays activation, tau, and weights on startup and periodically.
- Persisted `h` is neither read nor updated by current retrieval. It initializes
  activation from query scores instead (`retriever.py:1238`, `:1287`).
- `incoming_weights` has storage/serialization support but no retrieval consumer.
- Tau reinforcement rewards retrieved candidates; it is not evidence that the user
  found those memories useful. Outgoing edge learning also adds state and writes.

Recommendation: retire ODE, expanding activation, persistent learning, and the tick
worker together once the paired comparison establishes an acceptable replacement.
Do not preserve them indefinitely as another experimental engine inside production.

Removal closure includes retrieval selection's LNN bonus, all three worker startup
paths, `LnnStateStore`/capability shims, model serializers, runtime diagnostics,
settings, tests, and explanatory docs. Preserve database readability. Initially
leave obsolete columns in existing SQLite stores; physically dropping historical
columns is unnecessary to eliminate runtime cost and complicates rollback.

### Inactive Step 1 and mutually exclusive reranking stacks

`config/settings.yaml:239` disables the Step 1 multi-head/QKV block and its optional
value/sequence/density/query-conditioning branches. They are configurable/tested,
not unreachable. Retire these experiments unless a current use case demonstrates
value. Keep production query-aspect/lexical selection separate: that is not the
same thing as the disabled multi-head experiment.

Compare only the relevant alternatives: current LNN, non-LNN Step 2, and the hybrid
candidate/evidence/diversity path with associative reranking off. Choose one
supported path. Keep direct query evidence, lexical fallback, source/date filters,
opposition protection, and result diversity constant in the comparison.

Measure judged relevant hits, irrelevant results, latency, and memory-store writes
on frozen fixtures or isolated corpus copies. A rank change is not a quality gain.
No live store should be mutated for this experiment, and query-order effects must
not contaminate comparisons. If the simple path loses a specific needed case,
retain/fix the smallest evidenced mechanism rather than restoring the whole stack.

## Product features: optional does not mean dead

| Surface | Current evidence | Recommendation |
| --- | --- | --- |
| Patterns | About 4,000 lines in `app/patterns`; accepted patterns are prepended to normal recall at `pipeline.py:2418`; retrieval records applications; mining is a manual evidence workflow. | Keep out of the first dead-code batch. Review whether automatic pattern injection/application logging earns its place before deleting accepted user knowledge. |
| Cortex/clusters | `patterns/miner.py:109` uses graph corpus analysis; CLI/MCP/HTTP expose it. | Optional analysis feature, not required for basic capture/recall. Retire only with its pattern/tool consumers. |
| Browser graph | `graph/builder.py`, `graph/ui`, `/graph`, `titan graph`; can embed missing vectors. | Candidate to retire if the browser graph is no longer a supported interface. |
| BB explorer | Separate read-only helper, RPCs, 3D renderer and accessible list. | Preserve. Browser graph deletion does not require deleting BB. |
| Markdown notes | `pipeline.py:247` emits an idempotent human-readable projection. | Optional output; remove only if that output is no longer wanted. |
| JSON storage and legacy trace migration | Live fallback/import paths; `sessions.py:211`, `memories.py:1000`, `scenes.py:805`. | Keep compatibility reads/import until old stores are accounted for. Do not confuse memory extraction records with duplicate raw scenes. |

Full pattern retirement would also remove HTTP/MCP/CLI/Pi tools, adapter skills,
bundle/graph commands, runtime schema initialization, packaging required-tool
checks, and pattern tests. Existing pattern tables should remain recoverable;
removing code does not justify deleting their data.

`networkx` is used in retrieval and Cortex/clusters as well as the browser graph.
Removing one graph renderer is not sufficient to remove that dependency.

## Packaging, adapters, and workspace residue

Keep the five agent capture adapters, Claude daemon ownership/fallback logic, Codex
pending recovery, privacy redaction, retry/dedup/atomic spool guarantees, and the
HTTP and MCP entrypoints. They serve different active consumers.

Keep the committed OpenCode `integrations/opencode_titan_plugin/dist` bundle:
installer and npm preparation consume it. Similar skill files in different adapter
packages are distribution payloads; deleting copies can break standalone installs.

Python/PyPI, Pi npm, and CLI npm manifests intentionally have separate version and
release authority. `requirements.txt` is consumed by bundled bootstrap. A future
single dependency source is sensible, but deleting manifests/locks blindly is not.
The root dependency-free `package-lock.json` is a tiny optional housekeeping cut;
the nested dependency locks matter. Release/privacy/artifact checks stay.

Ignored legacy OpenCode files found locally are not branch cleanup savings:
`tools/opencode/titan_v2_spool_plugin.ts`, `start_titan.py`, and
`backfill_recent_memories.py`. Likewise `build/`, `dist/`, caches, `out/`, and traces
are local artifacts, not tracked implementation. Do not delete memory directories
or recovery utilities merely to make the working directory look smaller.

The 63 tracked assets include website drafts, logos, fonts, and active package art.
They do not add retrieval runtime complexity. Archive obsolete design iterations
separately if desired; preserve active README/plugin references.

## Evaluation machinery and the old LNN pilots

Two ignored local LongMemEval runs were located under
`.bench/longmemeval-oracle/runs/`: `lnn-disabled-pilot-20` and `lnn-pilot-20`.
Parent inspection confirmed both summaries say **scored questions: 0**, **overall
accuracy: not available**, and `evaluation.status: skipped`. The lane also found
different ingestion/failure counts and unmatched configuration provenance. These
are historical smoke runs, not a causal comparison of LNN quality. No private
benchmark contents are copied into this report.

`tools/benchmarks/longmemeval_oracle.py:95` has an LNN switch and can provide useful
evaluation scaffolding. Two fresh extraction runs with different inputs or provider
outputs would still be a confounded comparison; freeze the corpus/embeddings and
judge expected results. `--skip-eval` cannot establish answer quality.

`retrieval_sweep.py` varies candidate settings, not LNN. The unreferenced
`tools/benchmarks/retrieval_with_rerank.py` is an experimental-script removal
candidate. Keep one useful benchmark route instead of maintaining overlapping
probes. LoCoMo and `entrypoints/overnight` are developer evaluation/smoke tooling,
not default save/retrieval services; retire them only if those evaluation targets
are abandoned. Their exclusion from normal distributions already limits cost.

**Correction to the evaluation lane:** LNN does have numerical/mechanical tests:
`tests/test_retrieval_brief.py:1680` and `:1767`, including a direct `_ode_settle`
call at `:1738`. They passed in the parent run. The missing evidence is comparative
retrieval quality, not the absence of any test of the ODE implementation.

Secondary simplifications found in current source are duplicate corpus snapshot
construction (`graph/clusters.py:126`, `:178`) and duplicate scene normalization
(`storage/scenes.py:593`, `:681`). They are smaller follow-ups: preserve cache
invalidation and validation boundaries. They do not justify replacing those systems.

## Pre-implementation audit verification and limitations

Parent-run checks used `.venv/bin/python` and disposable Titan storage:

- Core storage/save/recall batch: **125 passed, 3 subtests passed**.
- Broader retrieval/graph/runtime/extraction batch: initially **123 passed, 6 failed,
  3 subtests passed**. All six failures were scene-migration fixtures whose local
  `root/traces` conflicted with the externally supplied `TITAN_SPOOL_DIR`.
- Re-ran the complete migration module with that one override unset, retaining
  temporary home/base/runtime: **9 passed**. Thus **254 distinct tests across 24
  modules, plus 6 subtests, passed after correcting test isolation**.
- Current false verification behavior was directly reproduced as described above.
- Adapter lane reported **30 OpenCode Bun tests passed**; tracked diff remained clean.
- Interface lane reported **99 passed, 5 setup failures**. Parent inspection found
  the cause: `verify_python_dependencies` at `tools/cli/titan.py:291` ignores the
  `python_version < '3.11'` marker and demands `tomli` on Python 3.11.11. This is a
  pre-existing dependency-check bug, not a reason to install an unnecessary package.

Some lane attempts used system Python without the repository dependencies and
failed at import. Those attempts are not evidence of broken product behavior.
No full-suite/platform/package-install/BB visual validation was performed. No new
real-corpus LNN quality evaluation was performed. Current tests validate behavior
and regression cases, not that a ranking experiment improves real-world recall.

Core batch modules: `test_retrieval_quality_regression`, `test_retrieval_wave1`,
`test_save_integrity`, `test_storage_integrity`, `test_scene_store`,
`test_scene_pipeline`, `test_pending_scene_events`, `test_memory_store_sqlite`,
`test_federated_recall`, `test_retry_queue`.

Broader batch modules: `test_retrieval_brief`, `test_retrieve_route`,
`test_retrieve_opt_out`, `test_retrieval_scene_refs`, `test_auto_ingest`,
`test_dedup_worker`, `test_extraction_adapters`, `test_graph_builder`,
`test_graph_explorer`, `test_cortex_analysis`, `test_graph_route`,
`test_runtime_context`, `test_scene_migration`, `test_memory_notes_export`.

## Original execution order and stopping rule

1. Remove the no-op worker, unreachable code, dead configuration/helpers, and false
   verifier promotion. Preserve buffer compatibility, spool resolution, and data.
2. Run the relevant behavior/adapter/lifecycle suites; update package assertions
   wherever a removed module was bundled. Keep each removal independently reviewable.
3. Compare the three ranking modes on fixed evidence, select the smallest adequate
   path, then remove the losing implementations and their state workers together.
4. Reconsider optional patterns/analysis/rendering/export features separately. Avoid
   inventing another plugin framework to house code we are trying to remove.
5. Before any later main-branch merge: run broader shared-contract and artifact
   checks, demonstrate existing-store readability/replay, and review the actual diff.

Terminal state: one understandable save/retrieval core, no retired background work,
no unsupported config promises or false verification, and measured preservation of
capture integrity and useful recall. The present bottleneck is evidence for the
ranking replacement, not the availability of additional features. If a removal
breaks an evidenced contract, restore or preserve that narrow contract. Finish the
cleanup only when the runtime, adapters, schemas, tests, and shipped manifests agree.
