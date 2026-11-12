# Safe offline duplicate screening for Titan memories

## Recommendation

Use local embeddings to propose a small set of likely neighbors, then run an optional local Laya classifier as a conservative review queue. Never auto-delete or auto-merge: a human must compare source evidence, preserve complementary details, and approve each proposed group. Keep the first run as a dry-run report. This is a candidate design, not a production feature.

## What the existing Laya evidence says

The previous `experiment/jev-retrieval-dedup` work used Laya's premise/hypothesis entailment task in each direction and forwarded a pair when either direction entailed and neither contradicted. On the 18-record NASA sample, it flagged 3 pairs, caught 2 of 3 known duplicate links (66.7% link recall), and had 2 true duplicate links among 3 suggestions (66.7% link precision). It missed complementary-detail duplicates where neither statement entailed the other. The short same/different/unclear format found all 3 links but sent 128/153 pairs to review.

On a broader frozen sample of 71 memories across four technical conversations (626 within-conversation pairs, with 27 known duplicate links), Laya's directional entailment policy caught 10/27 duplicate links (37.0% recall). It sent 25 pairs to review: 10 true positives and 12 clear false positives, with 3 borderline cases separately; precision excluding borderline pairs was 45.5%. It fully connected 6 of 12 duplicate groups. This is a warning against trusting either the model score or transitive candidate chains. [Laya XNLI benchmark implementation](https://github.com/NandhaKishorM/laya/blob/main/research/scripts/build_benchmark_nb.py) documents the premise/hypothesis relationship format; Titan's previous experiment reports are on the `experiment/jev-retrieval-dedup` branch.

The sample is small and from technical conversations. These figures are pair-link precision/recall against source-reviewed labels, not the probability a proposed merged paragraph is safe. They do not establish performance on a user's full memory corpus.

## Five-case synthetic probe

`tools/benchmarks/bulk_dedup_laya_probe.py` defines five hand-written cases: paraphrase duplicate, one-way entailment with an extra detail, contradiction, changed/superseded value, and unrelated fact. It uses the same bidirectional Laya entailment rule and reports pair-level precision/recall for only the one duplicate case. It accepts only an already-present model path and never accesses a Titan DB, memory directory, embeddings, or network.

This checkout does not have the `laya` or `torch` Python packages installed, and no checkpoint is present in the inspected `.bench/laya*` locations. Therefore the synthetic inference run could not be executed here; no synthetic precision/recall is claimed. The older branch has actual Laya runs but they were conducted on real frozen Titan-derived examples, not this requested synthetic isolation. To run the isolated probe, prepare the pinned environment/checkpoint described in the historical report, then:

```bash
python tools/benchmarks/bulk_dedup_laya_probe.py \
  --model-path /path/to/pinned-laya-checkpoint \
  --output /tmp/laya-bulk-dedup.json
```

It performs ten CPU directional calls. Five examples are a smoke test only; a useful estimate needs a larger, held-out, source-reviewed set, with duplicate groups counted as groups as well as pairs.

## Availability and cost

Current Titan config selects local Ollama `nomic-embed-text:v1.5` for embeddings; the extraction config selects hosted OpenCode Go `deepseek-v4-flash`. Neither config selects Laya. Laya's published English checkpoint is a 421M-parameter model, distributed through Hugging Face; prior Titan runs loaded it on CPU. Its model card documents local loading, and the upstream benchmark describes its decision-task interface. [Laya model card](https://huggingface.co/convaiinnovations/laya), [Laya benchmark code](https://github.com/NandhaKishorM/laya/blob/main/research/scripts/build_benchmark_nb.py). The embedding model is available from Ollama as a local 274 MB package with an `/api/embed` endpoint. [Ollama model page](https://registry.ollama.com/library/nomic-embed-text), [Ollama embedding API guide](https://ollama.com/blog/embedding-models).

If both local models are already installed, marginal inference has no per-request API charge; it consumes CPU/GPU time and local memory. Laya is not currently installed in this checkout, so reproducing it requires package installation and downloading its checkpoint (about 808 MB per its current model card). Current extraction is configured to use a hosted provider, so a truly offline end-to-end scan must not call extraction. For an offline scan, operate only on already stored memory text and existing vectors; do not regenerate missing vectors unless explicitly configured for the local Ollama embedder. The live configured model endpoint is localhost, but Ollama availability was not established during this investigation.

## Safe scaling path

For `N` records, exhaustive comparison creates `N(N-1)/2` pairs and Laya's two-direction policy doubles the per-pair calls. Do not apply it corpus-wide. A safer bounded workflow is:

1. Read a consistent SQLite snapshot in read-only mode; preserve `(agent, memory_id)` identity and source/provenance fields. Exclude cross-agent comparisons by default.
2. Bucket candidates by compatible memory type and time window only where those fields are trustworthy. Add exact normalized-text matches.
3. Use existing vectors to retrieve a capped top-k neighbor list per record (for example, k=10), with a minimum similarity threshold fixed before seeing predictions. If vectors are missing, omit those rows and list them; do not silently invoke embedding generation.
4. Deduplicate candidate edges, then locally score each edge. Bound total edges and call volume. Record model/checkpoint, prompt, vector model, thresholds, pair inputs, directional outputs, and errors in an append-only dry-run artifact.
5. Build connected components only as reviewer work queues, not as automatic merge groups. Similarity chains can join distinct facts. Reviewer must verify each edge and write merged wording that preserves all unique information and provenance.
6. Save approved changes only to a separate scratch DB first; read it back and verify record partition, text, vectors, provenance and source events. Production write-back is a distinct step.

Embedding similarity is a recall-oriented prefilter, not a duplicate decision: related or contradictory statements can be close, and complementary-detail duplicates may score less similarly. Candidate caps/thresholds trade recall for runtime and must be measured on a held-out corpus before use. For very large corpora, approximate nearest-neighbor indexes reduce neighbor-search cost; keep the returned candidate cap and all approvals auditable. Ollama's own documentation describes embeddings as vectors for semantic similarity/search, not as duplicate truth labels.

## Known failure modes

- Complementary facts: each memory contains a different extra detail, so neither entails the other; Laya misses a duplicate relation.
- Entailment is broader than identity: a more detailed claim can entail a shorter one without being safe to collapse.
- Contradictions and supersession may be mistaken for duplicates; two historical values can both matter.
- Candidate graph transitivity can bridge separate claims. Never union and merge automatically.
- Similarity prefilters may omit a valid duplicate before Laya sees it; track prefilter recall separately.
- Source extraction can be wrong. Preserve the original records and evidence, and don't treat model agreement as source verification.
- Pair-level metrics overcount dense duplicate groups. Report group recovery and the percentage of records in safely reviewed groups too.
