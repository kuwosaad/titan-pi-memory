# Audit of the blind investigator probe

Audited 2026-11-11. Result: **two fully supported answers and one partially
supported extrapolation** on three frozen LongMemEval-S cases. This is a small
investigator-workflow diagnostic, not an official benchmark score or a test of
Titan's production memory extractor.

Inputs and outputs:
[experiment plan](../../../investigation-sandbox/plan.json),
[frozen answers and usage](../../../investigation-sandbox/frozen-results.json),
[index builder](../../../investigation-sandbox/prepare.py),
[budgeted tool wrapper](../../../investigation-sandbox/tool.py),
[original case selection](../../../benchmark-inspection/longmemeval-selection.json).

## What was tested

The same three full histories were indexed with question-independent,
deterministic source checkpoints: 800-character windows with 80-character
overlap, preserving original speaker, recorded date, scene and event references.
All source text was covered. Local `nomic-embed-text:v1.5` embeddings fed native
Titan retrieval. No generative memory writer was used.

Fresh investigators received a question, its reference date and a restricted
search/scene-reading interface. Each case allowed six searches, four scene
reads and 20,000 returned characters. Scene reads supported event-centered
windows, literal filtering and pagination. Answers were frozen without feedback
or retries after finalization. The orchestration identifies the investigators
as independent Terra agents without inherited root history; the retained CLI
logs contain only search and scene actions.

This corrects the earlier one-shot interpretation of memory retrieval: hints
can guide follow-up searches and source inspection. It also changes the index,
embedding retrieval and temporal reference supplied to the agent. Consequently,
the difference from the earlier source-only probe cannot be attributed to
iteration alone.

## Per-case judgments

| Case | Judgment | Evidence and limitation |
| --- | --- | --- |
| `b3c15d39`: shutter delivery interval | **Supported success** | Opened sources state ordered February 5 and arrived February 10; five elapsed days follows directly |
| `4dfccbf8`: activity with Rachel | **Supported success** | Opened February 1 source says the user started ukulele lessons with Rachel “today”; April 1 question date supplies the two-month anchor |
| `e61a7584`: duration of owning Luna | **Partial; not a strict current-state success** | Cited a genuine August 11 estimate of six months, extrapolated to about 9½ months, and missed the explicit November 30 statement of about nine months |

The Luna answer is not fabricated: its citation is real and the arithmetic is
understandable. Both source durations are approximate, so the half-month
difference alone is not evidence of a gross factual contradiction. However,
the final answer does not recover the newer direct statement that the update
task asks the system to use. Its stated uncertainty concerns approximation,
not the unresolved newer source. It should not receive an unqualified pass.
No arbitrary numerical partial-credit value is assigned.

Exact answer artifacts:
[shutter](../../../investigation-sandbox/case-0/answer.json),
[Rachel](../../../investigation-sandbox/case-1/answer.json),
[Luna](../../../investigation-sandbox/case-2/answer.json).

## Why the update case stopped short

The third Luna search exposed a November 30 hint at `s0031/e0031_0007`.
Opening that event and its neighbors would include the preceding user statement
at `e0031_0006`. Instead, the investigator requested the whole scene filtered by
“Luna.” That produced 11,246 selected characters; only the first 4,000 were
returned, with an explicit next-page offset. The nine-month statement starts at
character 7,874 and was not displayed. A final search again returned older
duration evidence and generic cat-care material.

The agent stopped with two searches, two scene reads and 2,051 characters still
available. A literal “months” read of the already-discovered scene would contain
356 source characters, including the newer statement, and fit the remaining
budget with metadata. This is a post-run explanation, not a rerun or a prompt
given to the investigator. The observed failure combines noisy hints, an
overbroad scene read and premature stopping; it was not forced by exhausted
storage or the tool-call budget.
[Frozen Luna tool trace](../../../investigation-sandbox/case-2/tools.jsonl).

## Integrity checks

The audit independently verified, without modifying the databases:

- All three answer and tool-log SHA-256 hashes match the frozen summary.
- Every stored checkpoint exactly matches the predetermined source slice and
  role/date prefix. All 2,890 records have the correct neutral scene/event
  references and stored embeddings; no gold-derived facts were added.
- Every final cited quotation occurs in its addressed original message and in
  a scene response actually opened by that investigator. This includes the
  shutter arrival citation, which is a valid repeated statement even though it
  is not the benchmark's marked turn.
- Serialized response lengths sum to the recorded character budgets. Usage was
  respectively 1 search/2 reads/8,531 characters, 2 searches/1 read/9,507
  characters, and 4 searches/2 reads/17,949 characters.
- The index builder uses the full neutral histories, not the questions, gold
  answers, evidence labels or answer-bearing IDs, to construct checkpoints.

One isolation claim needs precise wording. `prepare.py` parses the prior raw
`result.json` solely to obtain its database path. That file also contains the
earlier question. Static data flow shows the question is not used in indexing,
and all stored text was independently reconstructed from the uniform source
rule. Thus **question-independent indexing and no gold use are supported**;
the stronger literal claim that the ingestion process never read question bytes
is not. The frozen run was left unchanged.

## What remains unmeasured

There is no abstention case in this bounded selection. Production extraction,
automatic supersession and generalized performance are unmeasured. Zero
generative indexing calls does not mean zero model use: the investigators
themselves generated queries and answers. Their token usage is separate from
local embedding work and is not established by these index counters.

The supported conclusion is narrow: semantic hints plus source investigation
produced two evidence-backed answers on these cases. The update case shows why
an agent must inspect available newer evidence before replacing an approximate
old statement with a calculated current answer.
