# Scalable near-duplicate detection and semantic adjudication

## Scope and evidence labels

“Near duplicate” can mean high token overlap, while “semantic duplicate” means two texts convey substantially the same useful information; these are different labels and need separate evaluation sets. Method descriptions below are established results from linked papers or official docs. Composition recommendations are marked **Inference**.

## Candidate generation

### Exact hashes and lexical similarity

An exact normalized-text digest is the cheapest first filter for repeated records. Normalization must be conservative: aggressive normalization can erase meaningful distinctions such as dates, negation, names, or numbers. **Inference:** preserve raw text and record the normalization recipe so a proposed merge is auditable.

For near-verbatim copies, represent text as word or character shingles and use Jaccard similarity. Broder’s document-resemblance work describes set representation and randomized sketches; MinHash estimates Jaccard because the probability two sets share a min-hash is their Jaccard resemblance. [Broder et al., *Identifying and Filtering Near-Duplicate Documents*](https://cs.brown.edu/courses/cs253/papers/nearduplicate.pdf). LSH banding makes candidate lookup subquadratic in practice, but is probabilistic: it can miss true pairs, and bucket membership is not a final similarity decision. [Har-Peled, Indyk, and Motwani, *Approximate Nearest Neighbor: Towards Removing the Curse of Dimensionality*](https://theoryofcomputing.org/articles/v008a014/); [datasketch MinHash LSH docs](https://ekzhu.com/datasketch/lsh.html).

Verify LSH candidates against exact shingle similarity (or a signature estimate followed by exact similarity when originals are available). Tune bands and rows for recall at the desired operating threshold; candidate recall is an index property, while pair precision belongs to downstream decisions. [datasketch docs](https://ekzhu.com/datasketch/lsh.html). **Inference:** word shingles handle phrase-preserving edits; character shingles can add robustness to small spelling or formatting changes, but measure both on corpus edits.

SimHash is a compact fingerprint for weighted feature vectors: Charikar’s random-hyperplane construction makes bit agreement encode angular similarity, so Hamming distance is a fast proxy for cosine similarity. [Charikar, *Similarity Estimation Techniques from Rounding Algorithms*](https://www.cs.princeton.edu/courses/archive/spring04/cos598B/bib/CharikarEstim.pdf). It is a candidate score, not an exact semantic measure. **Inference:** choose MinHash when the desired signal is set overlap/Jaccard; consider SimHash for a compact angular sketch of weighted features. Validate on labeled pairs.

### Semantic neighbors and embeddings

Semantic embeddings can surface paraphrases with low lexical overlap. SemDeDup uses pretrained-model embeddings to find semantically similar, non-identical examples in large training corpora. [Abbas et al., *SemDeDup*](https://arxiv.org/abs/2303.09540). Embedding similarity remains a candidate score, not proof that a nearest pair is safe to merge.

Exact duplicate texts can reuse a stored embedding instead of re-encoding. The cache must be keyed by the full encoding recipe: normalized text, model/revision, prompt or task mode, truncation/chunking, and normalization options can change output. Sentence Transformers documents prompt and task modes, truncation, and normalization options. [Computing embeddings](https://sbert.net/examples/sentence_transformer/applications/computing-embeddings/README.html); [SentenceTransformer API](https://sbert.net/docs/package_reference/sentence_transformer/model.html). **Inference:** content digest plus those recipe fields avoids stale vectors; reuse vectors only for identical inputs and recipe versions. It does not eliminate comparisons between different texts.

For large collections, HNSW is a graph-based approximate k-nearest-neighbor index. Its paper describes a hierarchical proximity graph for efficient approximate search; Faiss documents search-depth parameters that trade speed for accuracy, plus HNSW memory and update/removal constraints. [Malkov and Yashunin, *HNSW*](https://arxiv.org/abs/1603.09320); [Faiss index documentation](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes); [Faiss FAQ](https://github.com/facebookresearch/faiss/wiki/FAQ). Use ANN to find likely neighbors, then rerank with exact cosine similarity and/or a pair classifier. Measure neighbor recall against exact search; HNSW is approximate by design. **Inference:** at modest scale, exact vector search is a useful baseline and may be simpler to operate.

## Pair adjudication

Treat duplicate status as a relation over a pair, not as a similarity threshold alone. Directional NLI labels are entailment, contradiction, and neutral/unrelated. SNLI formalizes those labels; MultiNLI broadens evaluation across genres. [Bowman et al., *A large annotated corpus for learning natural language inference*](https://nlp.stanford.edu/pubs/snli_paper.pdf); [Williams et al., *A Broad-Coverage Challenge Corpus for Sentence Understanding through Inference*](https://aclanthology.org/N18-1101/).

NLI is not a duplicate oracle. If A entails B but B does not entail A, they may be a specific claim and a broader summary, not interchangeable. Contradiction is strong evidence to keep both; neutral may be complementary information. **Inference:** for deletion/merge decisions, require bidirectional entailment (or a task-specific equivalence label) and check entity/time/number changes; one-way entailment should usually be “related, review.” Models and datasets vary; SNLI is English sentence-level, and MultiNLI coverage does not guarantee reliability on every domain or long memory. [SNLI paper](https://nlp.stanford.edu/pubs/snli_paper.pdf); [MultiNLI paper](https://aclanthology.org/N18-1101/).

A practical adjudicator can combine exact normalized hash → lexical candidate score → semantic ANN score → pairwise NLI/STS or human review. Keep component scores and evidence so thresholds can be revised without losing provenance. **Inference:** avoid inference on all O(n²) pairs; cheap candidate generators first, expensive adjudication on the smaller candidate set.

## Graph and cluster pitfalls

A graph edge means a pair passed a candidate or pair threshold. It does not imply every node in a connected component is equivalent. Single-linkage clustering can chain through bridge pairs, joining endpoints that are not similar to each other; the clustering literature describes this as the chaining effect. [Jardine and Sibson, *Mathematical Taxonomy*](https://doi.org/10.1016/0020-0255(75)90045-6). **Inference:** connected components alone must not authorize destructive merging. Keep pair evidence, inspect component diameter or minimum pair confidence, and require complete-link constraints or explicit membership checks for every proposed merge.

This matters when a broad statement is close to several narrower memories, or when dates, agents, sources, and provenance differ. **Inference:** store a duplicate-family relation separately from “merge/delete”; candidate-family membership can guide review without collapsing distinct records.

## Review, thresholds, and calibration

Human review is useful where false merges cost more than retained duplicates. Interactive deduplication research uses active learning to surface informative record pairs and reports reducing labels needed for high accuracy. [Sarawagi and Bhamidipaty, *Interactive deduplication using active learning*](https://doi.org/10.1145/775047.775087). Active learning selects examples for labeling to improve a model rather than label a random pool indiscriminately. [Settles, *Active Learning Literature Survey*](https://burrsettles.com/pub/settles.activelearning_20090109.pdf).

Neural confidence scores are not automatically calibrated probabilities; research finds modern networks can be miscalibrated and describes temperature scaling as a simple post-hoc method. [Guo et al., *On Calibration of Modern Neural Networks*](https://proceedings.mlr.press/v70/guo17a.html). **Inference:** sample uncertain/borderline pairs and random pairs from auto-merge/auto-keep regions. Borderline review improves the decision boundary, while random audits estimate hidden error rates. Calibrate and set separate auto-merge, human-review, and keep thresholds on held-out corpus-specific labels.

Record reviewer disagreement and adjudicate a shared subset; unclear duplicate definitions create a human-label ceiling. **Inference:** define “duplicate” in terms of the intended action (safe to discard one? safe to merge facts?) before collecting labels.

## Architecture options

1. **Lexical-first, conservative:** exact normalized hashes + MinHash/LSH candidates + exact shingle verification; defer semantic-only pairs. Best when duplicates are mostly copied text and false merges are costly. **Inference:** simplest and easiest to audit.
2. **Hybrid two-stage (recommended):** lexical detection first; embed once per versioned content recipe; HNSW (or exact vector scan at smaller scale) finds semantic neighbors; rerank with bidirectional NLI/equivalence rules; auto-merge only high-confidence pairs, route calibrated uncertainty to review. **Inference:** handles copies and paraphrases while bounding expensive inference and keeping destructive decisions conservative.
3. **Review-first semantic clustering:** semantic ANN produces candidate families; active-learning review gathers labels; a calibrated pair classifier automates decisions, while cluster membership remains non-destructive until pair/cluster constraints pass. Best when semantic redundancy dominates and reviewers can label examples. **Inference:** requires feedback operations and drift monitoring.

## Recommended evaluation metrics

- **Candidate generation:** pair recall against an exhaustive labeled pair set; candidates per record; reduction versus all pairs; index build time, query latency, and memory. For ANN, recall@k versus exact vector search. Candidate recall and ANN recall are different: duplicate-pair discovery versus vector-neighbor recovery.
- **Pair decision:** precision/PPV for auto-merge (prioritize high precision), duplicate-pair recall, F1 or precision-recall curve, false merges per 1,000 processed pairs. Report by duplicate type (exact, edited, paraphrase, related, contradiction) and source/type/language.
- **Review and calibration:** review fraction, reviewer time, label agreement, expected calibration error or reliability plot, precision/recall at the auto-merge threshold. Track random-audit error separately from uncertainty-queue yield.
- **Cluster quality:** component size distribution, worst/minimum pairwise score within each component, sampled cluster purity, and bridge cases where endpoints are not mutually duplicates. **Inference:** these catch chaining failures hidden by pair metrics.
- **Operational safety:** provenance preserved, source-qualified identity retained, rollback/reversal success for merge proposals. **Inference:** no dedup score substitutes for recoverable merge history.

## Suggested first benchmark

Create a manually adjudicated set stratified across exact repeats, formatting edits, partial overlap, paraphrase, related-but-distinct, and contradiction pairs. Compare (a) exact + MinHash/LSH, (b) exact + SimHash, and (c) lexical + embeddings/ANN + pair adjudication. Label pair action as duplicate/related/distinct and record directional entailment separately. Freeze thresholds on one split and report metrics on another. **Inference:** include hard negatives with shared topic/entity but different dates or polarity, where semantic similarity can produce destructive false positives.

