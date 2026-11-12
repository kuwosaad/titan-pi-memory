# Selecting a small evidence-retrieval benchmark

Researched 2026-11-11 against the original benchmark repositories and scoring
code. This is the selection protocol, not a benchmark result. The initial run
tests Titan's source-passage retrieval with no model calls; it does not establish
extraction quality, automatic correction quality, or final-answer accuracy.

## Recommendation

Use four cases from **LongMemEval-S cleaned, with each selected case's entire
haystack**: the first non-abstention knowledge-update, multi-session, and
temporal-reasoning case, plus the first abstention case, in dataset order.
Freeze these IDs before querying Titan. This is a deliberately tiny diagnostic,
not a representative score or a leaderboard comparison.

LongMemEval directly covers information extraction, multi-session reasoning,
knowledge updates, temporal reasoning, and abstention. Its cleaned S dataset
contains timestamped sessions, answer-session IDs, and `has_answer` turn labels.
The oracle dataset contains only evidence sessions, so it removes the search
problem we want to measure. The S histories are approximately 115K tokens each.
[Official dataset and format](https://github.com/xiaowu0162/LongMemEval#-dataset-format),
[official cleaned dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned).

## Why these candidates differ

| Candidate | Relevant strength | Decision for this small run |
| --- | --- | --- |
| LongMemEval-S cleaned | Explicit update, temporal, multi-session and abstention tasks; source-location annotations | Best fit for a small, independently scored retrieval diagnostic |
| LoCoMo | Dialogue IDs attached to QA evidence; several questions can share one conversation | Useful later; existing Titan harness needs scoring and source-projection repairs |
| BEAM 128K | Coherent conversations with contradiction resolution, event ordering, and ten memory abilities | Strong next step when testing broader reasoning; more protocol work than this first probe |

LoCoMo releases ten conversations, with chronological sessions and dialogue IDs.
Its QA records include an evidence list when available. The original evaluator
computes evidence recall from returned dialogue/session identifiers, separately
from answer matching. This supports retrieval evaluation, but answer-token
overlap alone is not that metric.
[Official data description](https://github.com/snap-research/locomo#data),
[original evaluation code](https://github.com/snap-research/locomo/blob/main/task_eval/evaluation.py).

BEAM includes 128K, 500K, 1M and 10M conversation sizes and tasks including
contradiction resolution, event ordering and abstention. Its published answer
evaluation uses atomic rubric nuggets scored 0, 0.5 or 1 by a model; event
ordering uses an equivalence detector followed by Kendall tau-b. Those are
answer-level metrics, not a drop-in evidence-retrieval score.
[Official BEAM repository](https://github.com/mohammadtavakoli78/BEAM),
[paper evaluation protocol](https://arxiv.org/html/2510.27246v1#S2.SS4).

The recommendation in the table is our engineering judgment based on the
available annotations and this run's cost constraint, not a claim that one
benchmark is universally superior.

## Keep ingestion blind to evaluation labels

The ingestion projection receives only ordered session dates, message roles,
and original message content. Assign neutral ordinal scene and event IDs.
Preserve a separate scorer-only mapping back to the original source locations.
Never pass the question, answer, question type, `answer_session_ids`, or
`has_answer` to ingestion, extraction, indexing, ranking, or query construction.

Neutral IDs matter: the original retrieval harness identifies correct documents
through IDs containing `answer`, and rewrites some IDs according to turn labels.
Titan's lexical source search uses IDs to break ties. Copying those IDs into its
ranking surface would introduce avoidable label information. The official
harness's retrieval query is the question itself.
[Original corpus construction and retrieval code](https://github.com/xiaowu0162/LongMemEval/blob/main/src/retrieval/run_retrieval.py).

Use the question verbatim. If question date is supplied to a later reader, it
must come from the normal question metadata, not inferred from gold evidence.
Do not pick sessions because they contain an answer, manufacture compiled facts
from gold answers, tune queries after examining missing evidence, or rerun only
successful cases.

A bounded dataset download may stop after the selected cases are complete. It
must never truncate a selected case's history. Record URL/revision if available,
the exact selected-input hash, selection rule, case IDs, and any missing stratum.

## Smallest honest execution

1. Create a disposable `TITAN_HOME` for each independent case and block provider
   calls. Do not touch user memories or download model weights.
2. Store one scene per original history session, with each original turn kept as
   a separately addressed message. Apply this rule uniformly, without consulting
   labels. Preserve original role and session time; never replace dates with the
   import time.
3. Call the current public retrieval/brief path using the unmodified question,
   its ordinary source-passage cap, and a fixed 2,000-character brief budget.
   Do not create fake extracted memories to activate another retrieval lane.
4. Freeze complete outputs and timings before opening scoring labels. Score
   only text actually shown in the public brief; structured pointers and hidden
   candidate text do not count as visible evidence.

This session-sized adapter avoids deliberately making every question a test of
the 200-scene scan cutoff. It is **not** the normal event-first extraction path.
Record session/message/character counts and every source-search truncation cap;
if a source falls outside a cap, report that separately from ranking failure.
The native source-passage cap of two is part of this measurement. A later larger
cap experiment must be a separately named configuration.

## Metrics and interpretation

The original retrieval utility defines `recall_all@k` as a binary result: all
annotated documents must be present. `recall_any@k` requires one. Its reporting
script excludes abstention examples; the README explains that these lack a
ground-truth answer location. Titan's short passages are a different retrieval
unit, so do not present the following measurements as official benchmark scores.
[Metric implementation](https://github.com/xiaowu0162/LongMemEval/blob/main/src/retrieval/eval_utils.py),
[official metric reporting](https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/print_retrieval_metrics.py),
[retrieval protocol](https://github.com/xiaowu0162/LongMemEval#memory-retrieval).

For each answerable case, report:

- **Annotated turn coverage:** fraction of marked source turns addressed by
  visible, validly cited passages, and whether every marked turn was recovered.
- **Session coverage:** supporting sessions reached, separately from turn
  coverage. A matching session alone does not show the required passage.
- **Annotated-turn precision:** returned passages tied to marked turns divided
  by returned passages. Call it label precision, because unmarked context can
  still help; do not assert all unmarked passages are semantically irrelevant.
- **Visible evidence sufficiency:** manually inspect whether the exact displayed
  excerpt contains the needed information. A 500-character window can point to
  the right turn yet omit its answer-bearing clause.
- **Citation integrity:** each shown passage must match its addressed original
  message and preserve its source, role and time. Record invalid citations and
  unsupported claim text separately.
- **Cost:** provider calls/tokens, elapsed time, returned passages, rendered
  characters, and whether the fixed budget was met.

For knowledge updates, manually distinguish the old statement from the later
replacement and note whether the displayed evidence establishes the current
state. This raw-source run does not exercise the automatic supersession writer.

For the abstention case, report what was returned and whether it actually
supports an answer. A topical passage is not itself a false answer. With no
answer generator, **answer-level abstention accuracy is unmeasured**. A later
tiny reader test should require source citations or an explicit insufficient-
evidence response and be scored independently from retrieval.

## Existing Titan harnesses

The repository's [LongMemEval oracle runner](../../tools/benchmarks/longmemeval_oracle.py)
is unsuitable unchanged: its default corpus is evidence-only. The
[LoCoMo shadow runner](../../tools/benchmarks/locomo_shadow_eval.py) currently uses
answer substring/token coverage, expands scenes outside the brief budget, and
its fallback scene messages omit event IDs needed by the new source search.
Neither result should be relabeled as this experiment's evidence score.

The [scene-context scorer](../../tools/benchmarks/scene_context_eval.py) provides a
useful local precedent: credit rendered evidence and valid provenance, not merely
IDs. Its synthetic fixtures are still separate from this external diagnostic.

The protocol above does not itself establish a benchmark result. A completed
run should link its frozen input/output artifacts, source fingerprint, per-case
audit and limitations before drawing conclusions.

## Independent audit of the initial frozen run

The initial run is now complete. The evidence below comes from the local frozen
artifacts, not published benchmark results:
[selection manifest](../../../benchmark-inspection/longmemeval-selection.json),
[results and post-run scoring](../../../benchmark-inspection/longmemeval-results.json),
and the three original worker outputs
([case 0](../../../longmemeval-sandbox/case-0/result.json),
[case 1](../../../longmemeval-sandbox/case-1/result.json),
[case 2](../../../longmemeval-sandbox/case-2/result.json)).

The actual selection differs from the proposed four-case prefix sample. The
12 MiB prefix contained only single-session-user cases. Before examining
retrieval results, the runner recorded a fixed schedule of 2 MiB windows at
20%, 40%, 60%, 80% and 95% of the file. It selected the first complete target
case encountered in those windows. This yielded three cases; no abstention case
was found. The total stream was 22 MiB from pinned dataset revision
`98d7416c24c778c2fee6e6f3006e7a073259d48f`. This is a bounded convenience sample,
not the first case of each type in the full dataset or a random sample.

| Case | Visible evidence audit | Annotated turns reached | Complete evidence |
| --- | --- | --- | --- |
| `b3c15d39`, multi-session | Returned the February 5 order date and another order-related passage; omitted the February 10 arrival date needed to calculate elapsed days | 1/2 | No |
| `4dfccbf8`, temporal | Returned no passages; the marked source describes starting ukulele lessons with Rachel on February 1 | 0/1 | No |
| `e61a7584`, knowledge update | Returned a Luna weight-care question and generic assistant advice; omitted both the earlier six-month statement and later nine-month statement | 0/2 | No |

**None of the three displayed results contains complete evidence for its
question.** In particular, reaching one Luna evidence session does not recover
the duration statement inside it. The multi-session result recovers one useful
fact, but cannot establish the requested five-day interval. These judgments use
only the exact visible excerpts; no hidden source expansion receives credit.

The audit independently verified:

- All three corpus hashes and original worker-result hashes match the scoring
  manifest. The scored outputs were not rerun or tuned during this audit.
- Every ingested message exactly matches the original role/content; ingestion
  JSON contains only session timestamps and turns. Original session IDs and
  answer labels were not copied into the index. All stored scene/session/event
  IDs follow the neutral ordinal scheme.
- Read-only database checks found zero extracted memories and respectively
  51, 43 and 50 scenes, containing 502, 455 and 496 original messages.
- All four returned excerpts are exact substrings of their cited source
  messages, have the correct role, and appear in the rendered brief. Citation
  integrity passed even though answer-evidence completeness failed.
- No selected history exceeds the source search's scene/message/character scan
  caps: the largest case has 51 scenes; the largest session has 28 messages and
  24,704 source characters. These misses are not explained by the 200-scene cap.
- Each rendered brief stays within 2,000 characters. Worker artifacts report
  zero network, generation and embedding calls. No answer generator or judge
  was invoked.

The temporal worker received the question verbatim but **not its question
date**, April 1, 2023. The public query interface does not supply a reference-date
parameter. Therefore the phrase “two months ago” lacked its benchmark anchor.
This is an adapter limitation, and this run cannot establish temporal-reasoning
quality. Abstention is also unmeasured because that stratum was absent.

The narrow conclusion is that the current lexical source lane preserves exact
citations but did not supply all necessary evidence on these three probes. It is
not a score for Titan's extraction, correction writer, embedding retrieval,
answer generation, or overall memory quality. A subsequent run should preserve
this frozen result and define any different adapter or retrieval configuration
as a new experiment.
