# LongMemEval-S: Titan R5 memory with eight reader models

This page reports one experiment. We ran Titan's R5 memory with eight different
reader models on [LongMemEval-S](https://github.com/xiaowu0162/LongMemEval), 500 questions
(Wu et al., *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory*,
ICLR 2025).

Every reader got the same setup:

- **Memory:** the same frozen R5 Titan stores (one hash-verified copy per case).
- **Code:** the same R5 code (459-file manifest).
- **Instructions:** the same workflow skill and prompt.
- **Tools:** the same case-bound Titan tools: `search`, `scene` and `recent`.
- **Attempts:** one original attempt per case, with no semantic retries.

One judge graded all answers: GPT-6.2 Sol at high effort, applying the frozen official
LongMemEval rubric through `codex exec`.

Machine-readable summary: [`longmemeval-s-results.json`](longmemeval-s-results.json).

## Results

**Strict delivery** scores as zero any answer that needed format recovery, meaning the reader
wrapped its single fenced JSON answer block in prose and we had to parse the block out. The
rubric score accepts those recovered answers. Malformed JSON was never repaired and counts as
zero under both scores.

| Reader | Rubric | Strict delivery |
|---|---:|---:|
| Claude Opus 5.5 | 486/500 (97.2%) | 466/500 (93.2%) |
| Gemini 4 Neon | 480/500 (96.0%) | 480/500 (96.0%) |
| Claude Sonnet 5.5 | 468/500 (93.6%) | 402/500 (80.4%) |
| GPT-6.2 Sol | 467/500 (93.4%) | 467/500 (93.4%) |
| DeepSeek V4.1 Flash | 459/500 (91.8%) | 454/500 (90.8%) |
| GLM 5.3 | 458/500 (91.6%) | 120/500 (24.0%) |
| Claude Haiku 5.5 | 455/500 (91.0%) | 455/500 (91.0%) |
| GPT-6.1 Terra | 448/500 (89.6%) | 448/500 (89.6%) |

All 500 questions stay in the denominator. No cases were excluded.

## Caveats

Read these before the detailed tables.

- **Development set, not held-out.** R5 was tuned on these same 500 cases. These numbers do
  not show how Titan generalizes to unseen tasks.
- **System-level scores.** These results measure the reader and Titan memory together. They
  are not an isolated retrieval score.
- **Self-grading.** GPT-6.2 Sol was the judge, and it also graded its own answers as a reader.
- **Source audit not run.** We did not run the strict source audit (strict evidentiary credit)
  for this multi-model run. The [attribution audit](#attribution-did-titan-surface-the-evidence)
  below is a separate check that uses traces only.
- **Gemini 4 Neon used its own harness** (the neon-r5 harness, run against the Gemini API).
  - The Gemini monthly billing cap stopped the run. After the cap was removed, it finished
    245 cases. The 2 cases that were interrupted resumed from their saved turns.
  - Grading reused the 27 batches that were already graded before the cap.
  - Its times include spend-gate and rate-limit waits, so you cannot compare them directly
    with the other readers' times.
- **Sonnet and Haiku were interrupted.** The Claude subscription 5-hour usage limit stopped the
  Claude Sonnet 5.5 and Claude Haiku 5.5 runs (157 and 246 cases). Those cases produced no
  answer. They were re-run fresh after the limit reset.
- **Different harness routes.** Readers reached their models through different routes (`claude -p`,
  `codex exec`, OpenCode Go, the Gemini API). The Titan tools, skill and prompt were the same.
  The client around them was not.

## By question type

Correct answers out of *n*, same judge for all readers.

| Type | n | Gemini 4 Neon | Claude Opus 5.5 | GPT-6.2 Sol | DeepSeek V4.1 Flash | GLM 5.3 | Claude Sonnet 5.5 | Claude Haiku 5.5 | GPT-6.1 Terra |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| knowledge-update | 78 | 78 | 76 | 76 | 72 | 71 | 72 | 71 | 74 |
| multi-session | 133 | 123 | 125 | 116 | 117 | 114 | 118 | 113 | 105 |
| single-session-assistant | 56 | 56 | 56 | 56 | 55 | 55 | 55 | 56 | 55 |
| single-session-preference | 30 | 29 | 30 | 29 | 26 | 28 | 28 | 24 | 28 |
| single-session-user | 70 | 66 | 70 | 67 | 66 | 70 | 69 | 66 | 67 |
| temporal-reasoning | 133 | 128 | 129 | 123 | 123 | 120 | 126 | 125 | 119 |

## Paired comparisons (McNemar)

Each row compares Claude Opus 5.5 with one other reader on the same 500 cases, using rubric
labels. The counts are cases that only one of the two readers got right.

| Comparison | Only Claude Opus 5.5 correct | Only other reader correct | McNemar p |
|---|---:|---:|---:|
| vs Gemini 4 Neon | 11 | 5 | 0.2101 |
| vs GPT-6.2 Sol | 24 | 5 | 0.0005 |
| vs DeepSeek V4.1 Flash | 32 | 5 | <0.0001 |
| vs GLM 5.3 | 32 | 4 | <0.0001 |
| vs Claude Sonnet 5.5 | 24 | 6 | 0.0014 |
| vs Claude Haiku 5.5 | 34 | 3 | <0.0001 |
| vs GPT-6.1 Terra | 41 | 3 | <0.0001 |

The source reports p=0.0000 for the rows shown here as <0.0001. The difference between Claude
Opus 5.5 and Gemini 4 Neon is not statistically significant in this run.

## Speed and search depth

All values are per question.

| Reader | Mean time | Median time | Titan tool calls | Route |
|---|---:|---:|---:|---|
| Claude Opus 5.5 (high) | 18.1 s | 16.3 s | 5.0 | `claude -p` |
| GPT-6.2 Sol (high) | 35.7 s | 31.8 s | 12.0 | `codex exec` |
| DeepSeek V4.1 Flash (high) | 51.4 s | 40.5 s | 12.9 | OpenCode Go |
| GLM 5.3 (high) | 71.3 s | 55.8 s | 7.6 | OpenCode Go |
| Claude Sonnet 5.5 (high) | 13.2 s | n/a | 5.9 | `claude -p` |
| Claude Haiku 5.5 (high) | 17.6 s | n/a | 8.5 | `claude -p` |
| GPT-6.1 Terra (high) | 21.3 s | n/a | 6.0 | `codex exec` |
| Gemini 4 Neon (High thinking) | 144.6 s\* | 103.3 s\* | 20.2 | Gemini API (neon-r5 harness) |

\*Gemini 4 Neon times include spend-gate and rate-limit waits.

Times are end-to-end for the reader, so they include model latency. Reader cost as reported by
the Claude Code CLI (API-equivalent) was $0.156 per question for Claude Opus 5.5, $0.084 for
Claude Sonnet 5.5 and $0.0083 for Claude Haiku 5.5.

## Delivery

This table shows where the rubric and strict scores diverge.

| Reader | Format-recovered answers | Malformed (zero) |
|---|---:|---:|
| Claude Opus 5.5 | 21 | 0 |
| GPT-6.2 Sol | 0 | 0 |
| DeepSeek V4.1 Flash | 5 | 4 |
| GLM 5.3 | 370 | 5 |
| Gemini 4 Neon | 0 | 0 |
| Claude Sonnet 5.5 | 78 | 7 |
| Claude Haiku 5.5 | 0 | 1 |
| GPT-6.1 Terra | 0 | 0 |

GLM 5.3's low strict score comes from output formatting. Its answers were not less accurate:
370 of its answers wrapped the JSON block in prose.

## Attribution: did Titan surface the evidence?

This audit used saved traces only. It made no reruns and no model calls.

- **Gold evidence:** R5's scorer-only annotated event IDs.
- **"Surfaced":** a gold event's ID appeared in output that Titan actually returned to that
  reader.
- **Scope:** the 470 answerable cases, which are the 500 questions minus the 30 abstention
  questions.
- **Readers covered:** six of the eight. The audit script does not cover GPT-6.2 Sol and
  GPT-6.1 Terra.

| Reader | Correct (answerable) | Gold fully surfaced | Wrong despite full evidence | Wrong, evidence never surfaced |
|---|---:|---:|---:|---:|
| Claude Opus 5.5 | 458/470 | 450 (95.7%) | 9 | 0 |
| Gemini 4 Neon | 450/470 | 470 (100.0%) | 20 | 0 |
| DeepSeek V4.1 Flash | 435/470 | 467 (99.4%) | 35 | 0 |
| GLM 5.3 | 438/470 | 449 (95.5%) | 27 | 3 |
| Claude Sonnet 5.5 | 445/470 | 446 (94.9%) | 20 | 1 |
| Claude Haiku 5.5 | 430/470 | 456 (97.0%) | 34 | 2 |

Findings:

- **Titan's retrieval reached the evidence every time.** In 470 of 470 answerable cases, Titan
  surfaced all of the gold evidence to at least one of the six audited readers. No case had
  gold evidence that Titan never surfaced to any reader.
- **Most wrong answers came with full evidence in hand.** Across the six readers there were
  164 wrong answers on answerable cases. In 145 of them, Titan had already returned all of the
  gold evidence to that reader. In only 6 had the reader never been shown any gold evidence.
  We got these totals by summing the per-reader columns of `attribution-cases.csv`.
- **Model reasoning is the main remaining error source.** The remaining errors come mostly from
  how readers use the evidence, not from what Titan retrieves. In this setup, Titan's search
  strategy and depth account for a much smaller share.

The audit checks whether evidence was surfaced. It does not check whether the reader *cited* the
right evidence, which is the job of the strict source audit that was not run (see Caveats).

## Background: the original R5 run

R5 was first evaluated with a single reader. GPT-6.1 Sol (high) was the reader and GPT-6 Astra
(high) was the judge. That run scored 477/500 (95.4%) on the rubric. When GPT-6.2 Sol, the judge
used on this page, re-graded those same original answers, the score was 469/500 (93.8%).
Compare runs only when they use the same judge.

## Reproduction

These results correspond to the R5 retrieval implementation in this repository,
[`app/retrieval_pipeline/`](../../app/retrieval_pipeline/).

- **The code is close to the frozen copy, but not identical.** The runs used a frozen snapshot
  of the code. In `app/retrieval_pipeline/`, that snapshot differs from the current tree in one
  place: a timestamp-parsing compatibility fix in `source_search.py`. Other parts of `app/` have
  changed since the snapshot, for example the graph UI and `save_pipeline`.
- **There is no public harness for this run.** The eight-reader harness (reader dispatch, the
  MCP case server, judge batching and delivery normalization) is not in this repository. Neither
  are the frozen per-case memory stores.
- **`tools/benchmarks/` cannot reproduce it either.**
  [`tools/benchmarks/longmemeval_oracle.py`](../../tools/benchmarks/longmemeval_oracle.py) runs
  Titan on the LongMemEval *oracle* split, with a fixed top-k retrieval brief and a single
  reader call. It is a different setup and will not reproduce the numbers on this page.

For the dataset and the official evaluation code, see the
[LongMemEval repository](https://github.com/xiaowu0162/LongMemEval).
