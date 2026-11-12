# Native Step 2.1 test

This follow-up tests the original Step 2.1 through Titan's normal public
retrieval and brief-building path. The preceding scene-context experiment used
a shared experimental packet selector; its result is not a clean verdict on
the original end-to-end Step 2.1 behavior.

Before this run, the original checkout's retriever, brief renderer and settings
were verified byte-for-byte equal to the frozen `titan-baseline` snapshot. The
five Step 2.1 functions are also unchanged in the lean experimental checkout.

## Comparison fixed before outcomes

Run original Titan with Step 2.1 off and on. Separately, run the lean experiment
with Step 2.1 off and on. Within each pair, change only `step2.enabled`. Keep
shipped attention, centrality, contradiction, temporal and clustering settings,
and the existing final ranking weight. Do not use the experimental scene packet
module or replace Titan's final selector.

The main budget is six displayed facts and 700 characters, matching the shipped
brief defaults. A separate 2,000-character diagnostic checks whether extra
formatting consumes useful space; it cannot replace an unfavorable main result.
Fresh fictional cases and labels are frozen before embedding outcomes. They
include rough observations and learned conclusions, several related scenes,
timelines, corrections, distractors, repeated evidence and unanswered questions.

Use real local embeddings and disposable stores. Score complete evidence
actually rendered in the brief, not facts hidden in full-response metadata.
Verify provenance against the stored scenes. Record whether Step 2.1 actually
runs, changes scores/order and creates clusters. Separate those changes from
which memories finally appear in the brief.

The main question is whether turning it on delivers more complete useful
evidence without introducing more irrelevant facts or false change warnings.
Report per-case gains and losses, rather than treating a total score or a
changed ranking as proof of improvement. No parameter tuning on these cases.

## Results

The run used 12 fresh questions, 109 fictional memories, actual local
`nomic-embed-text:v1.5` embeddings, and four executions per case/budget/variant.
Within each checkout, only the Step 2.1 switch differed. All six integrity
checks passed, including matching corpus vectors, unchanged sources, matching
settings apart from the switch, stable outputs, bounds and durable provenance.

| Native 700-character brief | Step 2.1 off | Step 2.1 on |
| --- | ---: | ---: |
| Original: complete evidence | 6/12 | 6/12 |
| Original: irrelevant facts | 28 | 28 |
| Lean: complete evidence | 10/12 | 10/12 |
| Lean: irrelevant facts | 31 | 31 |

One date-filter case returned no candidates before Step 2.1 could run: both
explicit bounds were the date string `2026-09-08`, while the relevant timestamps
included noon. The existing literal upper-bound comparison excludes those rows.
This shared date-bound behavior is not attributed to Step 2.1. Excluding that
case gives 6/11 complete for original and 10/11 for lean, still identical on/off.
The missing-answer case failed to abstain in every variant. Every nonempty
answer also contained at least one fact outside the accepted evidence set.

Step 2.1 was genuinely active on the other 11 cases. It changed scores in all
11. Intermediate order changed in eight original cases and ten lean cases;
nonzero attention edges appeared in ten and eleven respectively. Native
cluster headings appeared in ten original and eleven lean briefs. Yet the set
of facts shown in the final brief changed in **zero cases** within either pair.
Final fact order changed in one original case and three lean cases.

Increasing the brief budget to 2,000 characters produced the same quality
results. This run therefore does not show a 700-character formatting penalty.
Each arm used 44 query embedding calls / 80 inputs per budget across the repeated
queries. Step 2.1 added no embedding calls. Timings include observer wrappers
and sequential local execution; they do not establish production performance.

There is a separate annotation error, not included in the fact-selection
counts. In the Atlas case, the brief marks “Enable debug logs in the isolated
Atlas lab environment” as `LATER VIEW`, and “Disable debug logs for Atlas
production traffic” as `EARLIER VIEW`, under `TENSION`. These rules apply to
different environments. They do not establish that one replaced the other.
The Maple explicit correction also received tension annotations, appropriately.

All rendered claim provenance resolved to actual stored scenes and source
events: 53/53 claims per original arm and 66/66 per lean arm, at both budgets.
Final verification passed 113 focused native-evaluator, prototype, existing
retrieval, federation, scene and brief tests. The complete release/adapter
suite was not rerun because this work adds isolated experiments and tests.

## Interpretation

This test demonstrates functioning association, reranking and grouping, with
no additional embedding calls. It does not demonstrate better evidence
selection from enabling Step 2.1 on these questions. It also exposes one false
change-of-view annotation. This is not proof that Step 2.1 is universally
useless. Grouped presentation's readability and downstream answer quality
were not judged by humans or a reader model here.

The original installation and defaults remain untouched. The experimental
branch retains Step 2.1 as an option; this run changes no retrieval defaults
and makes no production-promotion claim. In particular, lean's higher recall
also includes more irrelevant facts, so the cross-checkout counts alone do
not establish a universally better memory system.

## Reproduce

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/benchmarks/native_step2_eval.py \
  --original ../titan-baseline --lean . \
  --output ../native-step2-evaluation.json
```

The isolated workers allow network access only to loopback Ollama on port
11434 and use disposable stores. The report includes native compact responses,
actual rendered facts, attention/reranking/selection observations, per-case
on/off comparisons, effective settings and source fingerprints.

Frozen fixture SHA256:
`05caa7f10e09d128bb64a9baf59a57f7077a7dc5261fb40e7eaf8476fa078e3f`.
Two minimal-evidence label corrections were made before any embedding outcomes;
no fact text or ranking parameter was adjusted after seeing results.
