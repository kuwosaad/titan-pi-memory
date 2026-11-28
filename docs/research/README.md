# Research archive

Working notes from building Titan: experiments, investigations, and design
research. They record what was tried and measured at the time. They are not
user documentation, and some describe code paths that have since changed or
link to local artifacts that are not in this repository.

For current behavior, start with the [main README](../../README.md) and the
[documentation index](../README.md).

## Retrieval and efficiency

| Note | Topic |
| --- | --- |
| [titan-retrieval-experience-2026-09-01.md](titan-retrieval-experience-2026-09-01.md) | Retrieval experience review |
| [titan-efficiency-research-2026-09-22.md](titan-efficiency-research-2026-09-22.md) | Efficiency research |
| [titan-efficiency-results-2026-09-22.md](titan-efficiency-results-2026-09-22.md) | Efficiency implementation and comparison |
| [second-brain-efficiency-2026-11-08.md](second-brain-efficiency-2026-11-08.md) | Useful continuity with bounded context |
| [lean-memory-evaluation.md](lean-memory-evaluation.md) | Lean memory checkout comparison |
| [scene-first-memory-research.md](scene-first-memory-research.md) | Is scene-first memory established practice? |

## Duplicate detection

| Note | Topic |
| --- | --- |
| [bulk-dedup-architecture-2026-09-24.md](bulk-dedup-architecture-2026-09-24.md) | Architecture for finding duplicates in stored memories |
| [bulk-dedup-primary-sources.md](bulk-dedup-primary-sources.md) | Near-duplicate detection literature |
| [bulk-dedup-candidates.md](bulk-dedup-candidates.md) | Scalable candidate generation |
| [bulk-dedup-evaluation.md](bulk-dedup-evaluation.md) | Offline evaluation |
| [bulk-dedup-jev.md](bulk-dedup-jev.md) · [calibration](bulk-dedup-jev-calibration.md) | Jev probe |
| [bulk-dedup-laya.md](bulk-dedup-laya.md) | Laya screening probe |
| [bulk-dedup-live-inventory.md](bulk-dedup-live-inventory.md) | Read-only store inventory |
| [bulk-dedup-save-integration.md](bulk-dedup-save-integration.md) | Save-pipeline integration |
| [bulk-dedup-storage-safety.md](bulk-dedup-storage-safety.md) | Scan, review, apply, rollback |
| [bulk-dedup-cli-workflow.md](bulk-dedup-cli-workflow.md) | CLI workflow |

## Experiments

| Note | Topic |
| --- | --- |
| [benchmark-selection.md](experiments/benchmark-selection.md) | Choosing an evidence-retrieval benchmark |
| [benchmark-investigation-audit.md](experiments/benchmark-investigation-audit.md) | Blind investigator probe audit |
| [scene-context-evaluation.md](experiments/scene-context-evaluation.md) | Scene-context retrieval |
| [native-step2-evaluation.md](experiments/native-step2-evaluation.md) | Native Step 2.1 test |
| [ayanokoji-memory.md](experiments/ayanokoji-memory.md) | Evidence-first experiment |
| [memory-features-plan.md](experiments/memory-features-plan.md) · [results](experiments/memory-features-results.md) | Corrections, occurrence time, source recall |

## Supermemory comparison

Static inspection of Supermemory's public source and shipped local server, used to inform Titan's design.

| Note | Topic |
| --- | --- |
| [supermemory-server-investigation.md](experiments/supermemory-server-investigation.md) | Overview: what it teaches Titan |
| [supermemory-public-mechanisms.md](experiments/supermemory-public-mechanisms.md) | Public mechanisms |
| [supermemory-save-internals.md](experiments/supermemory-save-internals.md) | Save orchestration |
| [supermemory-retrieval-internals.md](experiments/supermemory-retrieval-internals.md) | Retrieval |
| [supermemory-vector-internals.md](experiments/supermemory-vector-internals.md) | Vectors and embeddings |
| [supermemory-correctness-review.md](experiments/supermemory-correctness-review.md) | Correctness contracts |
