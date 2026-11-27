# Scene-context retrieval experiment

## Question and decision rule

Can bounded scene lookup deliver useful evidence that flat retrieval misses,
within the same output budget? Does contextual ranking improve on simply
looking up those scene members?

This is an opt-in prototype on `reinventing-titan`, based on
`f84e221`. It does not enable a new production retrieval path. The cases are
fictional, the stores disposable, and embedding inference local. Original
memories and the original checkout are outside this experiment.

Before inspecting outcomes, the comparison is defined as:

1. Current experimental flat retrieval, Step2 disabled.
2. The existing Step2 reranker enabled.
3. One-hop scene expansion with ordinary query scoring.
4. The same expansion with capped contextual scoring.

The two expansion arms must inspect the same candidate set and use the same
packet selector. Their difference is contextual scoring. Expansion uses at
most three distinct source-qualified seed scenes and at most two additional
records per seed. It cannot start without admitted retrieval hits, recurse
through newly discovered records, or treat proximity as proof of causation.

All arms share the corpus, embedding model, filters, item limit and character
budget. Only fully rendered evidence counts as delivered; a scene pointer
alone is not an answer. Metadata and context labels consume the budget too.
Record embedding calls, lookup work and elapsed time instead of treating the
extra work as free.

The primary readout is complete sufficient evidence delivered per question.
Also report irrelevant evidence, unanswered-question behavior, wrong-project
leakage, provenance, output size and local latency. Cases include decisions
and reasons, events and outcomes, pronouns, crowded candidate pools,
mixed-topic scenes, repeated evidence, corrections and missing attributes.
The fixture and parameters are frozen before observing embedding outcomes.

The first implementation uses score-priority whole packets, with a seed-only
fallback if an expanded packet cannot fit. It does not implement the earlier
idea of optimizing new semantic coverage per character. The test therefore
evaluates bounded scene expansion and its association bonus, not that broader
selection proposal.

Sibling selection uses the same query/seed cosine gates in both expansion
arms. Contextual embeddings can change packet ranking but cannot rescue a
sibling excluded by those gates. Only output growth is bounded: the prototype
still scans the supplied sibling universe. This run must report inspected
records and cannot establish bounded retrieval cost for large stores.

The shared output limit is three records and 2,400 characters of a common
full-metadata JSON representation. Before any embedding outcomes, a mechanical
size check showed the initially proposed 900-character limit could not fit a
typical two-record packet (1,061 characters). Only that budget was corrected;
case texts and labels were unchanged. This representation differs from Titan's
normal compact brief. The harness takes three seeds from the public reader's
34 returned hits. “Outside the returned pool” does not mean outside the internal
candidate pool, which may be larger for compound questions.

Keep contextual ranking only if it improves complete evidence delivery over
all three alternatives without worsening no-answer or wrong-project errors,
and preserves source identity and output bounds. If simple expansion matches
it, prefer simple expansion. If neither improves on flat retrieval, retain
the negative result and leave defaults unchanged. A small authored benchmark
cannot establish production superiority even if this gate passes.

## Results

The first four-arm run used actual local `nomic-embed-text:v1.5` embeddings,
13 frozen fictional cases, and one initial plus three repeated executions per
case/arm. No parameters were tuned after observing results.

| Version | Complete evidence | Complete with no irrelevant facts | Irrelevant records | Unknown-answer abstentions |
| --- | ---: | ---: | ---: | ---: |
| Flat | 8/13 | 1/13 | 20 | 0/2 |
| Existing Step2 | 8/13 | 1/13 | 20 | 0/2 |
| Scene expansion | 8/13 | 1/13 | 21 | 0/2 |
| Expansion + association bonus | 8/13 | 1/13 | 21 | 0/2 |
| Supplemental contextual packet embeddings | 8/13 | 1/13 | 21 | 0/2 |

The aggregate hides three gains and three regressions. Expansion recovered
Lumen's reason, Kestrel's exact retry limit, and Ember's deployment reason.
Ember's reason was outside the 34 returned hits in a 47-record corpus. But
expansion displaced Harbor's measured outcome, Opal's database reason, and
Juniper's image-format reason with unrelated scene facts. In the Atlas case,
it added a second fact about the wrong project. The association bonus changed
none of the selected evidence compared with expansion alone.

The three displaced facts were already present among the public seeds. The
prototype excluded those IDs from sibling candidates, bundled other neighbors
with the scene owner, and exhausted the record budget before retaining the
useful seeds. This is a coordination failure in this packet rule, not evidence
that scene lookup itself is useless. The rule is preserved for this test;
repairing it against these exposed labels would not constitute a held-out win.

This is a negative result for enabling the prototype. Co-occurrence can expose
missing evidence, but does not reliably identify which context deserves the
limited output space. Similarity and shared scenes also fail to distinguish a
missing answer from a related topic.

Before inspecting that first result, a supplemental fifth recipe was fixed:
embed each complete candidate packet (seed text and the same selected sibling
texts, joined with newlines) in one additional local batch, then apply the
existing capped positive query-similarity delta. This tests packet embeddings;
it still cannot change sibling admission or selection. The original four-arm
report is retained independently.

The supplemental embedding arm changed no selected facts compared with simple
expansion. It made 84 embedding calls over 52 query executions, versus 52 calls
for each other arm: 32 additional batches and 32 additional embedded texts for
no evidence gain. These counts exclude corpus seeding. In that run, request
latency was approximately 26 ms median versus 15 ms for simple expansion;
this is a small sequential local sample, not a throughput benchmark.

The supplemental audit resolved every rendered claim to its actual SQLite
scene, checked the scene text and event IDs, and retained 36/36 source-qualified
claims per arm. Output bounds held. The initial report checked metadata against
fixture rows only; the stronger audit and acceptance checks are measurement
corrections, not algorithm changes. Neither contextual arm passes the declared
gate. Defaults remain unchanged.

Final verification: 107 focused prototype, evaluator, scene, federation, brief
and existing retrieval tests passed. All seven final report integrity flags
passed. The final report SHA256 is
`52b99da813c41883c6c500e1458d3d5bf431ab848d48bac62bf4bc476a3a19f8`.
This test did not rerun the complete release/adapter suite.

## Reproduce

From the experimental checkout, with Titan's Python dependencies and local
Ollama `nomic-embed-text:v1.5` available:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/benchmarks/scene_context_eval.py \
  --embedding local --output ../scene-context-evaluation-final-local.json
```

The runner creates disposable stores and strips inherited Titan paths from its
worker environment. It blocks network destinations except the local embedding
service. Without `--embedding local`, label-blind synthetic vectors exercise
the harness only; they do not measure semantic retrieval quality.

The frozen fixture SHA256 is
`7eaa9154928fc9660b5623ac8e34f683f80ddfa8604282df058e810cc3e4ee91`.
The report records the algorithm, evaluator, runtime/configuration and embedding
fingerprints, per-case outputs, extra work, source audits and failed gates.

## Limitations and next decision

These are authored cases with fictional memories, not an independent human
judgment of production questions. No live corpus or real extraction model was
evaluated here. All variants use the common packet renderer/selector, which
reorders two native retrieval outputs; native-order diagnostics are retained.
Distinct IDs containing the same text cannot inflate the maximum bonus but can
still occupy both context slots. Source/date boundaries were exercised; no
strict session or project permission claim is made.

Reject this packet rule for default activation. The useful finding is that
scene lookup can recover missing reasons and exact attributes. The next design
must preserve already useful evidence while adding context, and distinguish
an answer from a related fact. It needs fresh evaluation cases: these cases
are now exposed and must not become a tuning target presented as held-out proof.
