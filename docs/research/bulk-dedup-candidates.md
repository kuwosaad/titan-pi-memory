# Scalable candidate generation for bulk duplicate search

## Decision

Do not compare every memory pair. Use a layered candidate union: canonical-text SHA-256 buckets for exact duplicates, plus bounded top-k neighbors from embeddings already stored with the memories (or an offline ANN index), and a token MinHash/LSH route as a complementary text-based signal where embedding coverage is missing. Run the more expensive duplicate verifier only on that union. Keep source and scene as ranking/filter metadata, never as a hard blocking key: the probe shows those keys split duplicate families across unrelated metadata values and miss every planted pair.

For a simple first implementation, exact hash plus embedding neighbors is the smallest useful path if most memories already have compatible stored vectors. Make the candidate cap explicit, record candidate recall on labeled real examples, and include exact hash / token fallback for memories without vectors. The embedding experiment here is deliberately easy (near duplicates share a tight synthetic vector center); its recall is not evidence of real-corpus recall.

## Probe

`tools/benchmarks/bulk_dedup_candidates_probe.py` constructs deterministic, in-memory-only corpora. It never opens a Titan store or invokes a model. Each corpus contains groups of three records sharing a token base with two randomized token edits per record, and vectors perturbed around a per-group center. Known positive pairs are the within-family pairs. Metadata rotates source and scene independently. Timings include candidate generation and pair materialization; peak memory uses Python `tracemalloc` (native allocations are not included).

Reproduce with:

```sh
python3 tools/benchmarks/bulk_dedup_candidates_probe.py --sizes 1000,5000,10000
```

Observed on the current development machine:

| Method | N | Positive pairs recalled | Candidate pairs | Recall | Seconds | Peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Exact text hash | 1,000 | 0/120 | 0 | 0.000 | 0.002 | 0.2 |
| Token overlap (any shared token) | 1,000 | 120/120 | 61,220 | 1.000 | 0.025 | 7.7 |
| MinHash LSH (64 hashes, 8 bands) | 1,000 | 36/120 | 94 | 0.300 | 0.270 | 6.3 |
| Embedding sign-LSH (16 planes, one-bit probes) | 1,000 | 120/120 | 2,211 | 1.000 | 0.095 | 3.9 |
| Scene + source blocking | 1,000 | 0/120 | 41,168 | 0.000 | 0.009 | 4.5 |
| Source-only blocking | 1,000 | 0/120 | 124,500 | 0.000 | 0.032 | 11.5 |
| Exact cosine top-5 | 1,000 | 120/120 | 3,139 | 1.000 | 10.371 | 0.5 |
| Exact cosine top-10 | 1,000 | 120/120 | 5,943 | 1.000 | 10.736 | 1.1 |
| Exact text hash | 5,000 | 0/600 | 0 | 0.000 | 0.008 | 1.0 |
| Token overlap (any shared token) | 5,000 | 600/600 | 1,531,815 | 1.000 | 0.455 | 181.5 |
| MinHash LSH (64 hashes, 8 bands) | 5,000 | 201/600 | 2,195 | 0.335 | 1.518 | 31.0 |
| Embedding sign-LSH (16 planes, one-bit probes) | 5,000 | 598/600 | 53,107 | 0.997 | 0.433 | 15.6 |
| Scene + source blocking | 5,000 | 0/600 | 1,039,168 | 0.000 | 0.231 | 95.5 |
| Source-only blocking | 5,000 | 0/600 | 3,122,500 | 0.000 | 0.840 | 345.7 |
| Exact text hash | 10,000 | 0/1,200 | 0 | 0.000 | 0.017 | 2.0 |
| Token overlap (any shared token) | 10,000 | 1,200/1,200 | 6,115,952 | 1.000 | 1.854 | 708.5 |
| MinHash LSH (64 hashes, 8 bands) | 10,000 | 382/1,200 | 7,853 | 0.318 | 3.387 | 62.5 |
| Embedding sign-LSH (16 planes, one-bit probes) | 10,000 | 1,199/1,200 | 213,524 | 0.999 | 0.883 | 36.5 |
| Scene + source blocking | 10,000 | 0/1,200 | 4,161,668 | 0.000 | 0.958 | 382.3 |
| Source-only blocking | 10,000 | 0/1,200 | 12,495,000 | 0.000 | 3.585 | 1,382.7 |

Exact cosine top-k retains only 3,139 / 5,943 unordered pairs at k=5 / 10 and recalls all planted positives in this especially clean vector setup. The search work is still exhaustive here: it scores every vector against every other vector (about 1 million comparisons at N=1,000). It is a useful small-corpus quality reference, not the scalable retrieval implementation. A production ANN index should approximate this top-k result while controlling recall and latency. The top-k methods are only included at N <= 1,000 by default.

The all-pairs baseline is N(N-1)/2: 499,500 / 12,497,500 / 49,995,000 pairs at these sizes. Token overlap and metadata blocking can therefore be quadratic even though their indexes are simple. The embedding bucket result grows quickly too: common sign codes create large buckets, so it should be treated as evidence for ANN/top-k retrieval, not as a production bucket design. MinHash LSH kept candidate counts small but its chosen bands missed roughly two thirds of positives, making this particular parameterization unsuitable without tuning.

Exact hash returned no pairs because the planted positives intentionally differ in wording; exact hashing remains a cheap, high-precision first pass for byte-identical content. The probe measures recall only, not false-positive rate or verifier precision. Token overlap is intentionally generous (any shared term), and candidate volume shows why an overlap threshold or rare-token filtering would be required.

## Limits and next validation

The synthetic set does not model real Titan language, repeated boilerplate, duplicate prevalence, embedding drift, missing vectors, or cross-agent/source identity. The low MinHash recall is a warning about this exact configuration, not about the family of methods. Before selecting thresholds, label a representative sample of known duplicates and hard negatives, including paraphrases and cross-source copies. Report recall by duplicate type and source, then cap candidates per record and inspect how the cap changes recall. Keep source-qualified IDs during verification.

MinHash estimates set resemblance and locality-sensitive hashing trades missed neighbors against candidate volume; these are established candidate-generation techniques, not semantic truth tests ([Broder, “On the Resemblance and Containment of Documents”](https://skeptric.com/resources/broder97resemblance.pdf); [Indyk and Motwani, “Approximate Nearest Neighbors”](https://doi.org/10.1145/276698.276876)). For existing dense vectors, an ANN index such as HNSW is a candidate retrieval structure with tunable recall/latency ([Malkov and Yashunin, HNSW](https://arxiv.org/abs/1603.09320)). All returned candidates still need duplicate verification; none of these indexes establishes equivalence.
