import unittest
from unittest.mock import patch

import numpy as np

from app.retrieval_pipeline.brief import build_memory_notes
from app.retrieval_pipeline.retriever import (
    _build_attention_matrix,
    _compute_centrality,
    _content_tokens,
    _detect_clusters,
    _detect_contradictions,
    _expanding_ode_rerank_hits,
    _ode_settle,
    _step2_1_rerank,
    parse_timestamp,
    retrieve_memories,
)
from app.save_pipeline.pipeline import retrieve_memory_brief
from app.storage.repository import CandidateFilters


class RetrievalBriefTests(unittest.TestCase):
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    def test_retrieval_mode_and_latest_dedupe(
        self,
        mock_query_memory_candidates,
        mock_semantic_candidates,
        mock_embed,
    ):
        mock_query_memory_candidates.return_value = [
            {
                "id": "s1:0:0",
                "text": "Karu received a telegram message from user 876708125 via the openclaw-hook:titan-karu-bridge integration.",
                "stream": "rough",
                "type": "fact",
                "session_id": "s1",
                "ts": "2026-02-06T00:00:00+00:00",
                "embedding": [1.0, 0.0],
                "source_reliability": 0.9,
            },
            {
                "id": "s1:1:0",
                "text": "Use session_id and event_id for dedupe.",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "ts": "2026-02-01T00:00:00+00:00",
                "embedding": [1.0, 0.0],
                "source_reliability": 0.9,
            },
            {
                "id": "s1:2:0",
                "text": "Use session_id and event_id for dedupe.",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "ts": "2026-02-05T00:00:00+00:00",
                "embedding": [1.0, 0.0],
                "source_reliability": 0.9,
            },
        ]
        mock_semantic_candidates.return_value = mock_query_memory_candidates.return_value
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(
            query="what rule should we use for dedupe",
            session_id="s1",
            mode="learnings",
            top_k=5,
            min_similarity=0.0,
        )

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["memory"]["id"], "s1:2:0")
        self.assertEqual(hits[0]["memory"]["stream"], "learnings")

        brief = build_memory_notes(hits, max_items=3, max_chars=200)
        self.assertIn("MEMORY BRIEF:", brief)
        self.assertIn("[learnings/decision]", brief)

    @patch("app.retrieval_pipeline.config.load_settings")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    def test_step2_rerank_is_applied_in_production_retriever(self, mock_query_memory_candidates, mock_embed, mock_load_settings):
        mock_load_settings.return_value = {
            "retrieval_top_k": 2,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": True,
            "retrieval_rerank_enabled": True,
            "retrieval_rerank_alpha": 1.0,
            "retrieval_rerank_pool_k": 4,
            "retrieval": {"min_reliability": 0.0},
        }
        mock_query_memory_candidates.return_value = [
            {
                "id": "s1:1:0",
                "text": "Vector embeddings ranking baseline.",
                "stream": "rough",
                "type": "fact",
                "session_id": "s1",
                "ts": "2026-02-05T00:00:00+00:00",
                "embedding": [0.9, 0.0],
                "source_reliability": 0.9,
            },
            {
                "id": "s1:2:0",
                "text": "Use session_id and event_id for dedupe.",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "ts": "2026-02-04T00:00:00+00:00",
                "embedding": [0.8, 0.6],
                "source_reliability": 0.9,
            },
            {
                "id": "s1:3:0",
                "text": "The dedupe bug came from session_id and event_id collisions.",
                "stream": "rough",
                "type": "fact",
                "session_id": "s1",
                "ts": "2026-02-03T00:00:00+00:00",
                "embedding": [0.82, 0.57],
                "source_reliability": 0.9,
            },
            {
                "id": "s1:4:0",
                "text": "The event_id fix replaced the old dedupe collision rule.",
                "stream": "rough",
                "type": "fact",
                "session_id": "s1",
                "ts": "2026-02-02T00:00:00+00:00",
                "embedding": [0.5, 0.86],
                "source_reliability": 0.9,
            },
        ]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(
            query="what dedupe rule uses session_id and event_id",
            session_id="s1",
            top_k=2,
            min_similarity=0.0,
        )

        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["memory"]["id"], "s1:2:0")
        self.assertGreater(hits[0]["step2_bonus"], 0.0)
        self.assertGreater(hits[0]["final_score"], hits[0]["base_score"])

    @patch("app.save_pipeline.pipeline.get_scene_references")
    @patch("app.retrieval_pipeline.retriever.retrieve_memories")
    @patch("app.save_pipeline.pipeline.route_query")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_retrieve_memory_brief_returns_lightweight_scene_reference(self, mock_load_settings, mock_route_query, mock_retrieve_memories, mock_get_scene_references):
        mock_load_settings.return_value = {
            "dedup": {"enabled": False},
            "step2": {"cluster_compression_enabled": False},
        }
        mock_route_query.return_value = {
            "schema_version": "v2",
            "use_memory": True,
            "mode": "both",
            "top_k": 5,
            "summary_mode": None,
        }
        mock_retrieve_memories.return_value = [
            {
                "score": 0.9,
                "memory": {
                    "id": "s1:2:0",
                    "text": "Use session_id and event_id for dedupe.",
                    "stream": "learnings",
                    "type": "decision",
                    "session_id": "s1",
                    "scene_id": "s1:scene:e-2",
                },
            }
        ]
        mock_get_scene_references.return_value = [
            {
                "scene_id": "s1:scene:e-2",
                "evidence_status": "complete",
                "evidence_version": 1,
                "missing_source_event_ids": [],
            }
        ]

        result = retrieve_memory_brief(query="what rule should we use for dedupe", session_id="s1")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["memories"][0]["scene_id"], "s1:scene:e-2")
        self.assertEqual(result["scenes"], [])
        self.assertEqual(result["scene_refs"][0]["scene_id"], "s1:scene:e-2")
        self.assertEqual(result["scene_refs"][0]["evidence_status"], "complete")
        self.assertEqual(result["scene_brief"], "")


DEFAULT_STEP2_CONFIG = {
    "sim_floor": 0.45,
    "softmax_temp": 0.5,
    "gate_offset": 2.0,
    "gate_steepness": 0.8,
    "residual_weight": 0.3,
    "attention_mask": {
        "balanced": {
            "rough_to_rough": 0.25,
            "rough_to_learnings": 0.25,
            "learnings_to_rough": 0.25,
            "learnings_to_learnings": 0.25,
        },
    },
    "centrality_lambda": 0.3,
    "centrality_iterations": 2,
    "centrality_diversity_penalty": True,
    "centrality_sim_floor": 0.45,
    "attention_mask_enabled": True,
    "centrality_enabled": True,
}


def _make_hit(mem_id, text, stream, base_score):
    return {
        "memory": {
            "id": mem_id,
            "text": text,
            "stream": stream,
            "type": "fact",
            "session_id": "test",
            "ts": "2026-01-01T00:00:00+00:00",
            "source_reliability": 0.9,
        },
        "score": base_score,
        "base_score": base_score,
        "final_score": base_score,
        "step2_bonus": 0.0,
        "support_score": 0.0,
    }


class CentralityTests(unittest.TestCase):
    """Phase B: Explanatory Centrality — unit and integration tests."""

    def setUp(self):
        self.query = "what dedupe rule should we use"
        self.query_terms = _content_tokens(self.query)

    def test_build_attention_matrix_has_correct_shape(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        embedding_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }

        A = _build_attention_matrix(hits, self.query_terms, embedding_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        self.assertEqual(A.shape, (3, 3))
        self.assertTrue(np.all(A.diagonal() == 0.0), "diagonal should be zero (no self-attention)")

    def test_build_attention_matrix_no_overlap_source_contributes_zero(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("iso", "deployment unrelated topic random words", "rough", 0.4),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        embedding_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "iso": np.array([0.0, 1.0], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }

        A = _build_attention_matrix(hits, self.query_terms, embedding_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        iso_idx = 1
        iso_col_sum = float(np.sum(A[:, iso_idx]))
        self.assertEqual(
            iso_col_sum, 0.0,
            "iso memory contributes zero attention to others (j=iso has j_overlap=0)",
        )

        l1_idx = 2
        l1_row_sum = float(np.sum(A[l1_idx]))
        self.assertGreater(l1_row_sum, 0.0, "connected memory L1 should receive attention from R1")

        r1_to_l1 = A[l1_idx][0]
        self.assertGreater(r1_to_l1, 0.0, "R1→L1 attention should be non-zero (rough_to_learnings with overlap)")

    def test_compute_centrality_boosts_well_connected_memories(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("iso", "deployment unrelated pipeline tools unknown", "rough", 0.4),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        embedding_by_id = {
            "r1": np.array([1.0, 0.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5, 0.0], dtype=np.float32),
            "iso": np.array([0.0, 0.0, 1.0], dtype=np.float32),
            "l1": np.array([0.5, 0.87, 0.0], dtype=np.float32),
        }

        A = _build_attention_matrix(hits, self.query_terms, embedding_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        centrality = _compute_centrality(hits, self.query_terms, embedding_by_id, A, DEFAULT_STEP2_CONFIG)

        self.assertEqual(len(centrality), 4)
        self.assertIn(0, centrality)
        self.assertIn(3, centrality)

        c_iso = centrality[2]
        c_l1 = centrality[3]
        base_iso = hits[2]["base_score"]

        iso_center_ratio = c_iso / base_iso if base_iso > 0 else 0

        self.assertAlmostEqual(
            iso_center_ratio, 0.7, delta=0.01,
            msg=f"Isolated memory (orthogonal in 3D, no query-term overlap) should get "
                f"centrality ≈ base * (1-lambda) = {base_iso} * 0.7 = {base_iso * 0.7:.3f}, "
                f"got {c_iso:.4f} (ratio={iso_center_ratio:.4f})",
        )

        self.assertGreater(
            c_l1, c_iso,
            f"Connected L1 ({c_l1:.4f}) should have higher absolute centrality "
            f"than truly isolated memory ({c_iso:.4f})",
        )

    def test_compute_centrality_iterations_increase_effect(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        embedding_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }
        A = _build_attention_matrix(hits, self.query_terms, embedding_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        cfg_1 = {**DEFAULT_STEP2_CONFIG, "centrality_iterations": 1}
        cfg_3 = {**DEFAULT_STEP2_CONFIG, "centrality_iterations": 3}

        c1 = _compute_centrality(hits, self.query_terms, embedding_by_id, A.copy(), cfg_1)
        c3 = _compute_centrality(hits, self.query_terms, embedding_by_id, A.copy(), cfg_3)

        scores_1 = [c1[i] for i in sorted(c1)]
        scores_3 = [c3[i] for i in sorted(c3)]

        self.assertNotEqual(
            scores_1, scores_3,
            "1-iteration and 3-iteration centrality must produce different score vectors. "
            "This catches the no-op iteration bug.",
        )

        spread_1 = max(scores_1) - min(scores_1)
        spread_3 = max(scores_3) - min(scores_3)

        self.assertGreater(
            spread_3,
            spread_1,
            "More iterations should produce greater score spread "
            f"(1 iter spread={spread_1:.4f}, 3 iter spread={spread_3:.4f})",
        )

    def test_compute_centrality_diversity_penalty_dampens_near_duplicates(self):
        near_dup = np.array([1.0, 0.0], dtype=np.float32)
        slight_var = np.array([0.99, 0.14], dtype=np.float32)

        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe using session id again", "rough", 0.8),
            _make_hit("r3", "dedupe using session id third", "rough", 0.8),
        ]
        embedding_by_id = {
            "r1": near_dup,
            "r2": slight_var,
            "r3": near_dup,
        }
        A = _build_attention_matrix(hits, self.query_terms, embedding_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        cfg_with = {**DEFAULT_STEP2_CONFIG, "centrality_diversity_penalty": True}
        cfg_without = {**DEFAULT_STEP2_CONFIG, "centrality_diversity_penalty": False}

        c_with = _compute_centrality(hits, self.query_terms, embedding_by_id, A.copy(), cfg_with)
        c_without = _compute_centrality(hits, self.query_terms, embedding_by_id, A.copy(), cfg_without)

        max_with = max(c_with.values())
        max_without = max(c_without.values())

        self.assertLess(
            max_with,
            max_without,
            f"Diversity penalty should reduce max centrality: "
            f"with={max_with:.4f} < without={max_without:.4f}",
        )

    def test_step2_1_rerank_with_centrality_applies_bonus(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("iso", "deployment unrelated topic random words", "rough", 0.35),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        embedding_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "iso": np.array([0.0, 1.0], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }

        result = _step2_1_rerank(
            hits, self.query, embedding_by_id, alpha=1.0, step2_config=DEFAULT_STEP2_CONFIG,
        )

        self.assertEqual(len(result), 4)

        bonus_values = [h.get("step2_bonus", 0.0) for h in result]
        has_positive_bonus = any(b > 0.0 for b in bonus_values)
        self.assertTrue(has_positive_bonus, f"At least one hit should have step2_bonus > 0, got {bonus_values}")

        l1_hit = next(h for h in result if h["memory"]["id"] == "l1")
        self.assertGreater(l1_hit["final_score"], l1_hit["base_score"], "L1 should be boosted above its base score")

        iso_hit = next(h for h in result if h["memory"]["id"] == "iso")
        iso_score = iso_hit.get("final_score") or iso_hit.get("score") or 0

        self.assertLess(
            iso_score,
            result[0]["final_score"],
            "Isolated rough (no query-term overlap, low centrality) should not top the rankings",
        )

    def test_step2_1_rerank_centrality_off_changes_rankings(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("iso", "deployment unrelated topic random words", "rough", 0.4),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        embedding_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "iso": np.array([0.0, 1.0], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }

        cfg_on = {**DEFAULT_STEP2_CONFIG, "centrality_enabled": True}
        cfg_off = {**DEFAULT_STEP2_CONFIG, "centrality_enabled": False}

        result_on = _step2_1_rerank(hits, self.query, embedding_by_id, alpha=1.0, step2_config=cfg_on)
        result_off = _step2_1_rerank(hits, self.query, embedding_by_id, alpha=1.0, step2_config=cfg_off)

        scores_on = {h["memory"]["id"]: h["final_score"] for h in result_on}
        scores_off = {h["memory"]["id"]: h["final_score"] for h in result_off}

        for mem_id in scores_on:
            self.assertIn(mem_id, scores_off, f"memory {mem_id} should appear in both result sets")

        l1_on = scores_on["l1"]
        l1_off = scores_off["l1"]
        self.assertNotEqual(
            l1_on,
            l1_off,
            f"Centrality should change L1's score (on={l1_on:.4f}, off={l1_off:.4f})",
        )


class CentralityMRRBenchmark(unittest.TestCase):
    """Phase B: Measure MRR impact of centrality on synthetic rough→learning pairs."""

    @staticmethod
    def _make_memory(mem_id, text, stream, mem_type="fact"):
        return {
            "id": mem_id,
            "text": text,
            "stream": stream,
            "type": mem_type,
            "session_id": "bench",
            "ts": "2026-01-01T00:00:00+00:00",
            "source_reliability": 0.9,
        }

    @staticmethod
    def _make_benchmark_embedding(vec):
        return np.array(vec, dtype=np.float32)

    def _run_retrieval_with_config(self, query, memories, embeddings, step2_overrides):
        from app.retrieval_pipeline.config import load_settings as _load_settings

        base_settings = {
            "retrieval_top_k": 6,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": True,
            "retrieval_rerank_alpha": 1.0,
            "retrieval_rerank_pool_k": 10,
            "retrieval": {"min_reliability": 0.0},
            "step2": {
                "attention_mask_enabled": True,
                "centrality_enabled": True,
                "centrality_lambda": 0.3,
                "centrality_iterations": 2,
                "centrality_diversity_penalty": True,
                "sim_floor": 0.45,
                "softmax_temp": 0.5,
                "gate_offset": 2.0,
                "gate_steepness": 0.8,
                "residual_weight": 0.3,
                "attention_mask": {
                    "balanced": {
                        "rough_to_rough": 0.25,
                        "rough_to_learnings": 0.25,
                        "learnings_to_rough": 0.25,
                        "learnings_to_learnings": 0.25,
                    },
                },
                **step2_overrides,
            },
        }

        with patch("app.retrieval_pipeline.config.load_settings", return_value=base_settings):
            with patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=memories):
                with patch("app.retrieval_pipeline.retriever.embed", side_effect=lambda texts: [embeddings.get(t, np.zeros(2)) for t in texts]):
                    return retrieve_memories(
                        query=query,
                        session_id=None,
                        mode="both",
                        top_k=6,
                        min_similarity=0.0,
                    )

    def _compute_mrr(self, query, gold_id, memories, embeddings, centrality_on):
        step2_overrides = {"centrality_enabled": centrality_on}
        hits = self._run_retrieval_with_config(query, memories, embeddings, step2_overrides)
        for rank, hit in enumerate(hits, 1):
            if hit["memory"]["id"] == gold_id:
                return 1.0 / rank
        return 0.0

    def test_centrality_improves_mrr_over_baseline(self):
        """Measure MRR with centrality on vs off across synthetic rough→learning pairs."""
        texts = {
            "r1": "dedupe bug found in session ids",
            "r2": "event_id collision caused duplicate memories",
            "r3": "user asked about dedupe strategy",
            "l1": "use session_id and event_id for dedupe",
            "l2": "deployment pipeline green after CI fix",
            "r4": "CI pipeline broke on staging deploy",
            "l3": "always test deploy on staging before production",
            "r5": "staging deploy failed due to missing env vars",
            "l4": "the user prefers TypeScript over JavaScript",
            "r6": "TypeScript strict mode added to tsconfig",
            "l5": "karu recommends preview deploys for iteration",
            "r7": "user requested CI pipeline setup for preview",
            "l6": "the user wants iterative deployments with preview",
            "r8": "preview deploy succeeded on staging yesterday",
            "l7": "use Fly.io for all new service deployments",
            "r9": "user asked about deployment platform options",
        }

        embeddings = {
            texts["r1"]: self._make_benchmark_embedding([1.00, 0.00]),
            texts["r2"]: self._make_benchmark_embedding([0.95, 0.31]),
            texts["r3"]: self._make_benchmark_embedding([0.87, 0.50]),

            texts["l1"]: self._make_benchmark_embedding([0.97, 0.24]),

            texts["l2"]: self._make_benchmark_embedding([0.20, 0.98]),
            texts["r4"]: self._make_benchmark_embedding([0.25, 0.97]),

            texts["l3"]: self._make_benchmark_embedding([0.30, 0.95]),
            texts["r5"]: self._make_benchmark_embedding([0.35, 0.94]),

            texts["l4"]: self._make_benchmark_embedding([0.60, 0.80]),
            texts["r6"]: self._make_benchmark_embedding([0.65, 0.76]),

            texts["l5"]: self._make_benchmark_embedding([0.50, 0.87]),
            texts["r7"]: self._make_benchmark_embedding([0.55, 0.84]),

            texts["l6"]: self._make_benchmark_embedding([0.45, 0.89]),
            texts["r8"]: self._make_benchmark_embedding([0.48, 0.88]),

            texts["l7"]: self._make_benchmark_embedding([0.40, 0.92]),
            texts["r9"]: self._make_benchmark_embedding([0.42, 0.91]),
        }

        eval_cases = [
            {
                "desc": "dedupe rule (3 related roughs → 1 gold learning)",
                "query": texts["r3"],
                "gold_id": "l1",
                "memories": [
                    self._make_memory("r1", texts["r1"], "rough"),
                    self._make_memory("r2", texts["r2"], "rough"),
                    self._make_memory("r3", texts["r3"], "rough"),
                    self._make_memory("l1", texts["l1"], "learnings"),
                    self._make_memory("l2", texts["l2"], "learnings"),
                    self._make_memory("l4", texts["l4"], "learnings"),
                ],
            },
            {
                "desc": "CI/staging deploy (2 related roughs → 1 gold learning)",
                "query": texts["r4"],
                "gold_id": "l3",
                "memories": [
                    self._make_memory("r4", texts["r4"], "rough"),
                    self._make_memory("r5", texts["r5"], "rough"),
                    self._make_memory("r1", texts["r1"], "rough"),
                    self._make_memory("l3", texts["l3"], "learnings"),
                    self._make_memory("l1", texts["l1"], "learnings"),
                    self._make_memory("l4", texts["l4"], "learnings"),
                ],
            },
            {
                "desc": "preview deploys (2 related roughs → 1 gold learning)",
                "query": texts["r7"],
                "gold_id": "l6",
                "memories": [
                    self._make_memory("r7", texts["r7"], "rough"),
                    self._make_memory("r8", texts["r8"], "rough"),
                    self._make_memory("r4", texts["r4"], "rough"),
                    self._make_memory("l6", texts["l6"], "learnings"),
                    self._make_memory("l5", texts["l5"], "learnings"),
                    self._make_memory("l2", texts["l2"], "learnings"),
                ],
            },
            {
                "desc": "deployment platform (1 related rough → 1 gold learning)",
                "query": texts["r9"],
                "gold_id": "l7",
                "memories": [
                    self._make_memory("r9", texts["r9"], "rough"),
                    self._make_memory("r4", texts["r4"], "rough"),
                    self._make_memory("r1", texts["r1"], "rough"),
                    self._make_memory("l7", texts["l7"], "learnings"),
                    self._make_memory("l3", texts["l3"], "learnings"),
                    self._make_memory("l1", texts["l1"], "learnings"),
                ],
            },
            {
                "desc": "TypeScript preference (1 related rough → 1 gold, but unrelated denser cluster present)",
                "query": texts["r6"],
                "gold_id": "l4",
                "memories": [
                    self._make_memory("r6", texts["r6"], "rough"),
                    self._make_memory("r1", texts["r1"], "rough"),
                    self._make_memory("r2", texts["r2"], "rough"),
                    self._make_memory("l4", texts["l4"], "learnings"),
                    self._make_memory("l1", texts["l1"], "learnings"),
                    self._make_memory("l5", texts["l5"], "learnings"),
                ],
            },
        ]

        mrr_on = 0.0
        mrr_off = 0.0
        results = []

        for case in eval_cases:
            rr_on = self._compute_mrr(case["query"], case["gold_id"], case["memories"], embeddings, centrality_on=True)
            rr_off = self._compute_mrr(case["query"], case["gold_id"], case["memories"], embeddings, centrality_on=False)
            mrr_on += rr_on
            mrr_off += rr_off
            results.append((case["desc"], rr_on, rr_off))

        mrr_on /= len(eval_cases)
        mrr_off /= len(eval_cases)

        print(f"\n{'='*60}")
        print("CENTRALITY MRR BENCHMARK (synthetic rough→learning pairs)")
        print(f"{'='*60}")
        print(f"{'Case':<50} {'RR (on)':>8} {'RR (off)':>8} {'delta':>8}")
        print(f"{'-'*50} {'-'*8} {'-'*8} {'-'*8}")
        for desc, rr_on, rr_off in results:
            delta = rr_on - rr_off
            sign = "+" if delta > 0 else " "
            print(f"{desc[:48]:<50} {rr_on:8.4f} {rr_off:8.4f} {sign}{delta:7.4f}")
        print(f"{'-'*50} {'-'*8} {'-'*8} {'-'*8}")
        mrr_delta = mrr_on - mrr_off
        print(f"{'MEAN RECIPROCAL RANK':<50} {mrr_on:8.4f} {mrr_off:8.4f} {'+' if mrr_delta > 0 else ''}{mrr_delta:.4f}")
        print(f"{'='*60}")

        self.assertGreaterEqual(
            mrr_on,
            mrr_off,
            f"Centrality MRR ({mrr_on:.4f}) should not be worse than baseline ({mrr_off:.4f})",
        )

        improved_cases = sum(1 for _, r_on, r_off in results if r_on > r_off)
        same_cases = sum(1 for _, r_on, r_off in results if r_on == r_off)
        worse_cases = sum(1 for _, r_on, r_off in results if r_on < r_off)
        print(f"Improved: {improved_cases}, Same: {same_cases}, Regressed: {worse_cases}")

        self.assertGreaterEqual(
            improved_cases, worse_cases,
            f"Centrality should improve or maintain ranking for most cases "
            f"(+{improved_cases} improved, -{worse_cases} regressed, ={same_cases} unchanged)",
        )


CONTRADICTION_CONFIG = {
    "contradiction_enabled": True,
    "contradiction_sim_threshold": 0.7,
    "contradiction_tension_weight": 0.3,
    "contradiction_antonyms": [
        ["dark", "light"],
        ["accept", "reject"],
        ["use", "avoid"],
        ["prefer", "dislike"],
        ["work", "break"],
        ["enable", "disable"],
        ["keep", "remove"],
        ["add", "drop"],
    ],
}


class ContradictionTests(unittest.TestCase):
    """Phase C: Contradiction Detection — unit and integration tests."""

    def test_detect_contradiction_dark_light_with_shared_context(self):
        hits = [
            _make_hit("r1", "the user prefers dark mode", "rough", 0.9),
            _make_hit("r2", "the user switched to light mode yesterday", "rough", 0.8),
        ]
        hits[0]["memory"]["ts"] = "2026-01-01T00:00:00+00:00"
        hits[1]["memory"]["ts"] = "2026-03-15T00:00:00+00:00"
        emb_by_id = {
            "r1": np.array([0.71, 0.71], dtype=np.float32),
            "r2": np.array([0.70, 0.72], dtype=np.float32),
        }

        adj, tension = _detect_contradictions(hits, emb_by_id, CONTRADICTION_CONFIG)

        self.assertIn(0, adj)
        self.assertIn(1, adj)
        self.assertLess(adj[0], 0.0, "older memory should receive penalty")
        self.assertGreater(adj[1], 0.0, "newer memory should receive boost")
        self.assertIn(0, tension)
        self.assertIn(1, tension)
        self.assertIn("dark", tension[0].lower())
        self.assertIn("light", tension[0].lower())

    def test_no_contradiction_without_shared_context(self):
        hits = [
            _make_hit("r1", "the user prefers dark mode", "rough", 0.9),
            _make_hit("r2", "the light is broken in the office", "rough", 0.8),
        ]
        hits[0]["memory"]["ts"] = "2026-01-01T00:00:00+00:00"
        hits[1]["memory"]["ts"] = "2026-03-15T00:00:00+00:00"
        emb_by_id = {
            "r1": np.array([0.71, 0.71], dtype=np.float32),
            "r2": np.array([0.70, 0.72], dtype=np.float32),
        }

        adj, tension = _detect_contradictions(hits, emb_by_id, CONTRADICTION_CONFIG)

        self.assertEqual(len(adj), 0, "no contradiction when no shared context tokens")
        self.assertEqual(len(tension), 0)

    def test_no_contradiction_below_sim_threshold(self):
        hits = [
            _make_hit("r1", "the user prefers dark mode", "rough", 0.9),
            _make_hit("r2", "the user switched to light mode yesterday", "rough", 0.8),
        ]
        hits[0]["memory"]["ts"] = "2026-01-01T00:00:00+00:00"
        hits[1]["memory"]["ts"] = "2026-03-15T00:00:00+00:00"
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.0, 1.0], dtype=np.float32),
        }

        adj, tension = _detect_contradictions(hits, emb_by_id, CONTRADICTION_CONFIG)

        self.assertEqual(len(adj), 0, "orthogonal embeddings should fall below sim threshold")

    def test_contradiction_boosts_newer_penalizes_older(self):
        hits = [
            _make_hit("old", "use npm for package management", "rough", 0.8),
            _make_hit("new", "avoid npm, use pnpm instead", "rough", 0.9),
        ]
        hits[0]["memory"]["ts"] = "2026-01-01T00:00:00+00:00"
        hits[1]["memory"]["ts"] = "2026-06-01T00:00:00+00:00"
        emb_by_id = {
            "old": np.array([0.50, 0.87], dtype=np.float32),
            "new": np.array([0.52, 0.86], dtype=np.float32),
        }

        adj, tension = _detect_contradictions(hits, emb_by_id, CONTRADICTION_CONFIG)

        self.assertIn(0, adj)
        self.assertIn(1, adj)
        self.assertLess(adj[0], 0.0, f"old 'use npm' should be penalized, got {adj[0]}")
        self.assertGreater(adj[1], 0.0, f"new 'avoid npm' should be boosted, got {adj[1]}")
        self.assertAlmostEqual(abs(adj[0]), abs(adj[1]), delta=0.01,
                               msg="penalty and bonus should be symmetric")

    def test_contradiction_skips_when_timestamps_missing(self):
        hits = [
            _make_hit("r1", "the user prefers dark mode", "rough", 0.9),
            _make_hit("r2", "the user switched to light mode yesterday", "rough", 0.8),
        ]
        emb_by_id = {
            "r1": np.array([0.71, 0.71], dtype=np.float32),
            "r2": np.array([0.70, 0.72], dtype=np.float32),
        }

        adj, tension = _detect_contradictions(hits, emb_by_id, CONTRADICTION_CONFIG)

        self.assertEqual(len(adj), 0, "no contradiction when timestamps are missing")
        self.assertEqual(len(tension), 0)

    def test_step2_1_rerank_with_contradiction_applies_tension(self):
        hits = [
            _make_hit("r1", "the user prefers dark mode", "rough", 0.9),
            _make_hit("r2", "the user switched to light mode yesterday", "rough", 0.8),
        ]
        hits[0]["memory"]["ts"] = "2026-01-01T00:00:00+00:00"
        hits[1]["memory"]["ts"] = "2026-03-15T00:00:00+00:00"
        emb_by_id = {
            "r1": np.array([0.71, 0.71], dtype=np.float32),
            "r2": np.array([0.70, 0.72], dtype=np.float32),
        }

        result = _step2_1_rerank(
            hits, "mode preference", emb_by_id, alpha=1.0,
            step2_config={**CONTRADICTION_CONFIG, "attention_mask_enabled": True, "centrality_enabled": True},
        )

        self.assertEqual(len(result), 2)

        older = next(h for h in result if h["memory"]["id"] == "r1")
        newer = next(h for h in result if h["memory"]["id"] == "r2")

        self.assertIn("tension_note", older)
        self.assertIn("tension_note", newer)
        self.assertIn("dark", older.get("tension_note", "").lower())
        self.assertIn("light", older.get("tension_note", "").lower())
        self.assertLess(older.get("step2_contradiction_delta", 0.0), 0.0)
        self.assertGreater(newer.get("step2_contradiction_delta", 0.0), 0.0)

    def test_brief_renders_tension_marker(self):
        hits = [
            {
                "memory": {
                    "id": "r1",
                    "text": "the user prefers dark mode",
                    "stream": "rough",
                    "type": "user_preference",
                },
                "score": 0.9,
                "base_score": 0.9,
                "final_score": 0.9,
                "step2_bonus": 0.0,
                "support_score": 0.0,
                "tension_note": "preference appears to have changed from "
                               "\"the user prefers dark mode\" (2026-01-01) "
                               "to \"the user switched to light mode\" (2026-03-15)",
            },
            {
                "memory": {
                    "id": "r2",
                    "text": "the user switched to light mode yesterday",
                    "stream": "rough",
                    "type": "user_preference",
                },
                "score": 0.85,
                "base_score": 0.85,
                "final_score": 0.85,
                "step2_bonus": 0.0,
                "support_score": 0.0,
                "tension_note": "preference appears to have changed from "
                               "\"the user prefers dark mode\" (2026-01-01) "
                               "to \"the user switched to light mode\" (2026-03-15)",
            },
        ]

        brief = build_memory_notes(hits, max_items=3, max_chars=500)

        self.assertIn("MEMORY BRIEF:", brief)
        self.assertIn("TENSION:", brief)
        self.assertIn("dark", brief)


class ContradictionMRRBenchmark(unittest.TestCase):
    """Measure MRR impact of contradiction on preference-change scenarios."""

    def _make_memory(self, mem_id, text, stream, ts="2026-01-01T00:00:00+00:00"):
        return {"id": mem_id, "text": text, "stream": stream,
                "type": "fact", "session_id": "bench", "ts": ts, "source_reliability": 0.9}

    def _run(self, query, memories, embeddings, step2_overrides):
        step2 = {
            "attention_mask_enabled": True, "centrality_enabled": True,
            "contradiction_enabled": True, "sim_floor": 0.45, "softmax_temp": 0.5,
            "gate_offset": 2.0, "gate_steepness": 0.8, "residual_weight": 0.3,
            "centrality_lambda": 0.3, "centrality_iterations": 2, "centrality_diversity_penalty": True,
            "contradiction_sim_threshold": 0.5, "contradiction_tension_weight": 0.3,
            "contradiction_antonyms": [["dark", "light"], ["use", "avoid"], ["prefer", "dislike"]],
            "attention_mask": {"balanced": {"rough_to_rough": 0.25, "rough_to_learnings": 0.25,
                                             "learnings_to_rough": 0.25, "learnings_to_learnings": 0.25}},
            **step2_overrides,
        }
        settings = {"retrieval_top_k": 6, "retrieval_min_similarity": 0.0, "retrieval_recency_days": None,
                    "retrieval_session_bias": False, "retrieval_rerank_enabled": True,
                    "retrieval_rerank_alpha": 1.0, "retrieval_rerank_pool_k": 10,
                    "retrieval": {"min_reliability": 0.0}, "step2": step2}
        with patch("app.retrieval_pipeline.config.load_settings", return_value=settings):
            with patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=memories):
                with patch("app.retrieval_pipeline.retriever.embed",
                           side_effect=lambda texts: [embeddings.get(t, np.zeros(2)) for t in texts]):
                    return retrieve_memories(query=query, session_id=None, mode="both", top_k=6, min_similarity=0.0)

    def _rr(self, query, gold_id, memories, embeddings, step2_overrides):
        hits = self._run(query, memories, embeddings, step2_overrides)
        for rank, hit in enumerate(hits, 1):
            if hit["memory"]["id"] == gold_id:
                return 1.0 / rank
        return 0.0

    def test_contradiction_improves_preference_change_retrieval(self):
        texts = {
            "dark_rough_old": "the user prefers dark mode for reading",
            "dark_learning_old": "always use dark mode for reading preferences",
            "light_rough_new": "the user switched to light mode for reading",
            "light_learning_new": "always use light mode for reading preferences",
            "unrelated_rough": "use Python for backend services",
            "unrelated_learning": "all backend services written in Python",
        }
        embeddings = {
            texts["dark_rough_old"]:       np.array([0.80, 0.35, 0.49], dtype=np.float32),
            texts["dark_learning_old"]:    np.array([0.85, 0.30, 0.43], dtype=np.float32),
            texts["light_rough_new"]:      np.array([0.30, 0.85, 0.43], dtype=np.float32),
            texts["light_learning_new"]:   np.array([0.35, 0.80, 0.49], dtype=np.float32),
            texts["unrelated_rough"]:      np.array([0.15, 0.15, 0.98], dtype=np.float32),
            texts["unrelated_learning"]:   np.array([0.15, 0.20, 0.96], dtype=np.float32),
            "dark mode reading":           np.array([0.72, 0.55, 0.43], dtype=np.float32),
        }

        query = "dark mode reading"
        gold_id = "l_new"
        memories = [
            self._make_memory("r_old", texts["dark_rough_old"], "rough", "2025-06-01T00:00:00+00:00"),
            self._make_memory("l_old", texts["dark_learning_old"], "learnings", "2025-06-01T00:00:00+00:00"),
            self._make_memory("r_new", texts["light_rough_new"], "rough", "2026-03-15T00:00:00+00:00"),
            self._make_memory("l_new", texts["light_learning_new"], "learnings", "2026-03-15T00:00:00+00:00"),
            self._make_memory("r_un", texts["unrelated_rough"], "rough"),
            self._make_memory("l_un", texts["unrelated_learning"], "learnings"),
        ]

        rr_on = self._rr(query, gold_id, memories, embeddings,
                         {"contradiction_enabled": True, "centrality_enabled": True, "attention_mask_enabled": True})
        rr_off = self._rr(query, gold_id, memories, embeddings,
                          {"contradiction_enabled": False, "centrality_enabled": False, "attention_mask_enabled": False})

        print(f"\n[ContradictionMRRBenchmark]")
        print(f"  Query: '{query}'")
        print(f"  Gold:  '{texts['light_learning_new']}'")
        print(f"  RR (with contradiction): {rr_on:.4f}")
        print(f"  RR (without):            {rr_off:.4f}")
        print(f"  Delta:                   {rr_on - rr_off:+.4f}")

        self.assertGreaterEqual(
            rr_on, rr_off,
            f"Contradiction should help preference-change retrieval "
            f"(RR {rr_on:.4f} vs {rr_off:.4f})",
        )


class ClusterCompressionTests(unittest.TestCase):
    """Phase D: Cluster-Based Memory Compression — unit and integration tests."""

    def setUp(self):
        self.query = "what dedupe rule should we use"
        self.query_terms = _content_tokens(self.query)

    def test_detect_clusters_groups_connected_memories(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }
        A = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        communities = _detect_clusters(A, sim_floor=0.45)

        total_covered = sum(len(c) for c in communities)
        self.assertEqual(total_covered, 3, "all 3 nodes should be covered by communities")

    def test_detect_clusters_isolates_orthogonal_entries(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("iso", "deployment unrelated pipeline tools unknown", "rough", 0.4),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0, 0.0], dtype=np.float32),
            "iso": np.array([0.0, 0.0, 1.0], dtype=np.float32),
            "l1": np.array([0.5, 0.87, 0.0], dtype=np.float32),
        }
        A = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", DEFAULT_STEP2_CONFIG)

        communities = _detect_clusters(A, sim_floor=0.45)

        iso_idx = 1
        iso_community = next(c for c in communities if iso_idx in c)
        self.assertEqual(len(iso_community), 1, "orthogonal element should form singleton cluster")

    def test_detect_clusters_empty_matrix_all_singletons(self):
        A = np.zeros((3, 3), dtype=np.float32)
        communities = _detect_clusters(A, sim_floor=0.45)
        self.assertEqual(len(communities), 3)
        for c in communities:
            self.assertEqual(len(c), 1)

    def test_step2_1_rerank_attaches_cluster_metadata(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        hits[0]["memory"]["ts"] = "2025-01-01T00:00:00+00:00"
        hits[1]["memory"]["ts"] = "2026-01-01T00:00:00+00:00"
        hits[2]["memory"]["ts"] = "2026-03-01T00:00:00+00:00"
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }

        cluster_cfg = {**DEFAULT_STEP2_CONFIG, "cluster_compression_enabled": True}
        result = _step2_1_rerank(hits, self.query, emb_by_id, alpha=1.0, step2_config=cluster_cfg)

        self.assertEqual(len(result), 3)
        for h in result:
            self.assertIn("cluster_id", h, f"hit {h['memory']['id']} should have cluster_id")
            self.assertIn("cluster_size", h, f"hit {h['memory']['id']} should have cluster_size")
            self.assertIn("cluster_representative_text", h)

        r1_hit = next(h for h in result if h["memory"]["id"] == "r1")
        l1_hit = next(h for h in result if h["memory"]["id"] == "l1")

        self.assertIsNotNone(r1_hit.get("cluster_id"))
        self.assertIsNotNone(l1_hit.get("cluster_id"))

        self.assertIn("cluster_oldest_ts", r1_hit)
        self.assertIn("cluster_newest_ts", l1_hit)

    def test_brief_cluster_mode_renders_insight_header(self):
        hits = [
            {
                "memory": {"id": "r1", "text": "dedupe bug found in session ids",
                           "stream": "rough", "type": "fact"},
                "score": 0.9, "cluster_id": 0, "cluster_size": 2,
                "cluster_representative_text": "use session_id and event_id for dedupe",
                "cluster_has_tension": False,
                "cluster_oldest_ts": "2025-01-01", "cluster_newest_ts": "2026-03-01",
            },
            {
                "memory": {"id": "l1", "text": "use session_id and event_id for dedupe",
                           "stream": "learnings", "type": "decision"},
                "score": 0.95, "cluster_id": 0, "cluster_size": 2,
                "cluster_representative_text": "use session_id and event_id for dedupe",
                "cluster_has_tension": False,
                "cluster_oldest_ts": "2025-01-01", "cluster_newest_ts": "2026-03-01",
            },
            {
                "memory": {"id": "r2", "text": "deployment pipeline green",
                           "stream": "rough", "type": "fact"},
                "score": 0.5, "cluster_id": None, "cluster_size": 1,
            },
        ]

        brief = build_memory_notes(hits, max_items=5, max_chars=600, cluster_mode=True)

        self.assertIn("MEMORY BRIEF:", brief)
        self.assertIn("INSIGHT", brief)
        self.assertIn("related", brief)
        self.assertIn("2.", brief)

    def test_brief_cluster_mode_with_tension(self):
        hits = [
            {
                "memory": {"id": "r1", "text": "user prefers dark mode",
                           "stream": "rough", "type": "user_preference"},
                "score": 0.8, "cluster_id": 0, "cluster_size": 2,
                "cluster_representative_text": "user switched to light mode",
                "cluster_has_tension": True,
                "cluster_oldest_ts": "2025-01-01", "cluster_newest_ts": "2026-03-01",
                "step2_contradiction_delta": -0.15,
            },
            {
                "memory": {"id": "r2", "text": "user switched to light mode yesterday",
                           "stream": "rough", "type": "user_fact"},
                "score": 0.85, "cluster_id": 0, "cluster_size": 2,
                "cluster_representative_text": "user switched to light mode",
                "cluster_has_tension": True,
                "cluster_oldest_ts": "2025-01-01", "cluster_newest_ts": "2026-03-01",
                "step2_contradiction_delta": 0.15,
            },
        ]

        brief = build_memory_notes(hits, max_items=5, max_chars=600, cluster_mode=True)

        self.assertIn("TENSION", brief)
        self.assertIn("MORE RECENT", brief)
        self.assertIn("CONTRADICTED", brief)

    def test_brief_flat_mode_unchanged(self):
        hits = [
            {
                "memory": {"id": "r1", "text": "dedupe bug found in session ids",
                           "stream": "rough", "type": "fact"},
                "score": 0.9, "cluster_id": 0, "cluster_size": 2,
                "cluster_representative_text": "use session_id for dedupe",
            },
            {
                "memory": {"id": "l1", "text": "use session_id and event_id for dedupe",
                           "stream": "learnings", "type": "decision"},
                "score": 0.95, "cluster_id": 0, "cluster_size": 2,
                "cluster_representative_text": "use session_id for dedupe",
            },
        ]

        brief_flat = build_memory_notes(hits, max_items=5, max_chars=600, cluster_mode=False)

        self.assertIn("MEMORY BRIEF:", brief_flat)
        self.assertNotIn("INSIGHT", brief_flat)
        self.assertIn("[learnings/decision]", brief_flat)
        self.assertIn("[rough/fact]", brief_flat)

    def test_brief_cluster_mode_all_singletons_falls_back(self):
        hits = [
            {"memory": {"id": "r1", "text": "dedupe bug found", "stream": "rough", "type": "fact"},
             "score": 0.9, "cluster_id": None, "cluster_size": 1},
            {"memory": {"id": "l1", "text": "use session_id for dedupe", "stream": "learnings", "type": "decision"},
             "score": 0.95, "cluster_id": None, "cluster_size": 1},
        ]

        brief = build_memory_notes(hits, max_items=5, max_chars=600, cluster_mode=True)

        self.assertIn("MEMORY BRIEF:", brief)
        self.assertNotIn("INSIGHT", brief)


class TemporalHeadTests(unittest.TestCase):
    """Phase E: Temporal Attention Head — unit and integration tests."""

    def setUp(self):
        self.query = "what dedupe rule should we use"
        self.query_terms = _content_tokens(self.query)

    def test_temporal_sim_same_time_boosts_attention(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }
        cfg_off = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": False}
        cfg_on = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": True,
                   "temporal_tau_minutes": 30, "temporal_head_weight": 0.2, "semantic_head_weight": 0.8}

        A_off = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_off)
        A_on = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_on)

        self.assertGreater(A_on[0][1], A_off[0][1],
                           "same-time temporal boost should increase attention weight")

    def test_temporal_sim_decays_with_distance(self):
        hits = [
            {
                "memory": {
                    "id": "r1", "text": "dedupe using session id",
                    "stream": "rough", "type": "fact",
                    "session_id": "test", "ts": "2026-01-01T00:00:00+00:00",
                    "source_reliability": 0.9,
                },
                "score": 0.8, "base_score": 0.8, "final_score": 0.8, "step2_bonus": 0.0,
            },
            {
                "memory": {
                    "id": "r2", "text": "dedupe collision event fix",
                    "stream": "rough", "type": "fact",
                    "session_id": "test", "ts": "2026-01-01T05:00:00+00:00",
                    "source_reliability": 0.9,
                },
                "score": 0.7, "base_score": 0.7, "final_score": 0.7, "step2_bonus": 0.0,
            },
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }
        cfg_near = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": True,
                     "temporal_tau_minutes": 30, "temporal_head_weight": 0.2, "semantic_head_weight": 0.8}
        cfg_far = {**cfg_near, "temporal_tau_minutes": 1}

        A_near = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_near)
        A_far = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_far)

        self.assertGreater(A_near[0][1], A_far[0][1],
                           "memories 5 hrs apart with tau=30 should have higher attention than tau=1")

    def test_temporal_missing_timestamps_no_effect(self):
        hits = [
            {
                "memory": {
                    "id": "r1", "text": "dedupe using session id",
                    "stream": "rough", "type": "fact",
                    "session_id": "test",
                    "source_reliability": 0.9,
                },
                "score": 0.8, "base_score": 0.8, "final_score": 0.8, "step2_bonus": 0.0,
            },
            {
                "memory": {
                    "id": "r2", "text": "dedupe collision event fix",
                    "stream": "rough", "type": "fact",
                    "session_id": "test",
                    "source_reliability": 0.9,
                },
                "score": 0.7, "base_score": 0.7, "final_score": 0.7, "step2_bonus": 0.0,
            },
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }
        cfg_off = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": False}
        cfg_on = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": True,
                   "temporal_tau_minutes": 30, "temporal_head_weight": 0.2, "semantic_head_weight": 0.8}

        A_off = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_off)
        A_on = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_on)

        self.assertAlmostEqual(A_on[0][1], A_off[0][1], delta=1e-6,
                               msg="missing timestamps should produce same A[i][j] as temporal off")

    def test_temporal_head_off_no_change(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }
        cfg_off = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": False}

        A = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_off)

        self.assertEqual(A.shape, (2, 2))
        self.assertAlmostEqual(float(A[0][0]), 0.0, delta=1e-6)
        self.assertAlmostEqual(float(A[1][1]), 0.0, delta=1e-6)

    def test_step2_1_rerank_temporal_boosts_adjacent_memories(self):
        hits = [
            {
                "memory": {
                    "id": "r_near", "text": "dedupe strategy applied to event pipeline",
                    "stream": "rough", "type": "fact",
                    "session_id": "test", "ts": "2026-01-01T00:10:00+00:00",
                    "source_reliability": 0.9,
                },
                "score": 0.65, "base_score": 0.65, "final_score": 0.65, "step2_bonus": 0.0,
            },
            {
                "memory": {
                    "id": "l_near", "text": "use session_id for dedupe",
                    "stream": "learnings", "type": "decision",
                    "session_id": "test", "ts": "2026-01-01T00:05:00+00:00",
                    "source_reliability": 0.9,
                },
                "score": 0.85, "base_score": 0.85, "final_score": 0.85, "step2_bonus": 0.0,
            },
            {
                "memory": {
                    "id": "r_far", "text": "dedupe collision event fix",
                    "stream": "rough", "type": "fact",
                    "session_id": "test", "ts": "2026-01-15T00:00:00+00:00",
                    "source_reliability": 0.9,
                },
                "score": 0.68, "base_score": 0.68, "final_score": 0.68, "step2_bonus": 0.0,
            },
        ]
        emb_by_id = {
            "r_near": np.array([0.8, 0.2, 0.55], dtype=np.float32),
            "l_near": np.array([0.7, 0.3, 0.65], dtype=np.float32),
            "r_far": np.array([0.75, 0.25, 0.60], dtype=np.float32),
        }

        cfg_on = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": True,
                   "temporal_tau_minutes": 30, "temporal_head_weight": 0.2, "semantic_head_weight": 0.8}
        cfg_off = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": False}

        result_on = _step2_1_rerank(hits, self.query, emb_by_id, alpha=1.0, step2_config=cfg_on)
        result_off = _step2_1_rerank(hits, self.query, emb_by_id, alpha=1.0, step2_config=cfg_off)

        on_scores = {h["memory"]["id"]: h["final_score"] for h in result_on}
        off_scores = {h["memory"]["id"]: h["final_score"] for h in result_off}

        on_boost = on_scores["r_near"] - on_scores["r_far"]
        off_boost = off_scores["r_near"] - off_scores["r_far"]

        self.assertGreater(on_boost, off_boost,
                           "temporal head should give more advantage to temporally near memory over far memory")

    def test_temporal_config_weights_scale_effect(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
        ]
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }
        cfg_low = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": True,
                    "temporal_tau_minutes": 30, "temporal_head_weight": 0.05, "semantic_head_weight": 0.95}
        cfg_high = {**DEFAULT_STEP2_CONFIG, "temporal_head_enabled": True,
                     "temporal_tau_minutes": 30, "temporal_head_weight": 0.4, "semantic_head_weight": 0.6}

        A_low = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_low)
        A_high = _build_attention_matrix(hits, self.query_terms, emb_by_id, "balanced", cfg_high)

        self.assertGreater(A_high[0][1], A_low[0][1],
                           "higher temporal_head_weight should produce larger attention boost for same-time pair")


class SequenceEncodingTests(unittest.TestCase):
    """Tests for S1-G: Sequence Positional Encoding."""

    def test_sinusoidal_position_deterministic(self):
        """Same position always produces same vector."""
        from app.retrieval_pipeline.retriever import _sinusoidal_position
        vec1 = _sinusoidal_position(10.0, 16)
        vec2 = _sinusoidal_position(10.0, 16)
        np.testing.assert_array_almost_equal(vec1, vec2)

    def test_sinusoidal_position_different_positions_different_vectors(self):
        """Different positions produce different vectors."""
        from app.retrieval_pipeline.retriever import _sinusoidal_position
        vec1 = _sinusoidal_position(10.0, 16)
        vec2 = _sinusoidal_position(20.0, 16)
        self.assertFalse(np.allclose(vec1, vec2))

    def test_memory_sequence_position_extracts_metadata(self):
        """Memory sequence position correctly extracts ts, turn, and session."""
        from app.retrieval_pipeline.retriever import _memory_sequence_position
        mem = {
            "id": "s1:5:2",
            "ts": "2026-03-15T10:30:00+00:00",
            "session_id": "s1",
            "turn": 5,
            "scene_id": "s1:scene:e-5",
        }
        pos = _memory_sequence_position(mem)
        self.assertGreater(pos["global_position"], 0.0)
        self.assertEqual(pos["session_position"], 5.0)
        self.assertEqual(pos["local_position"], 2.0)

    def test_memory_sequence_position_missing_fields(self):
        """Missing ts, turn, or scene_id does not crash."""
        from app.retrieval_pipeline.retriever import _memory_sequence_position
        mem = {"id": "bad:0:0"}
        pos = _memory_sequence_position(mem)
        self.assertEqual(pos["global_position"], 0.0)
        self.assertEqual(pos["session_position"], 0.0)
        self.assertEqual(pos["local_position"], 0.0)

    def test_sequence_encoding_disabled_by_default(self):
        """When sequence_encoding_enabled is false, behavior is unchanged."""
        base_settings = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval_rerank_pool_k": 34,
            "retrieval": {"min_reliability": 0.0},
            "step1": {
                "enabled": False,
                "sequence_encoding_enabled": False,
            },
        }
        memories = [
            {
                "id": "s1:1:0",
                "text": "use session_id for dedupe",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "ts": "2026-01-01T00:00:00+00:00",
                "source_reliability": 0.9,
                "embedding": [0.9, 0.0],
            },
        ]

        with patch("app.retrieval_pipeline.config.load_settings", return_value=base_settings):
            with patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=memories):
                with patch("app.retrieval_pipeline.retriever.embed", return_value=[np.array([0.9, 0.0], dtype=np.float32)]):
                    hits = retrieve_memories(
                        query="dedupe rule",
                        session_id=None,
                        mode="both",
                        top_k=5,
                        min_similarity=0.0,
                    )

        self.assertEqual(len(hits), 1)
        self.assertNotIn("sequence_score", hits[0])

    def test_sequence_encoding_boosts_nearby_memories(self):
        """Sequence encoding boosts memories close to strong semantic anchors."""
        base_settings = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval_rerank_pool_k": 34,
            "retrieval": {"min_reliability": 0.0},
            "step1": {
                "enabled": True,
                "sequence_encoding_enabled": True,
                "sequence_encoding_dim": 16,
                "sequence_score_weight": 0.15,
                "sequence_neighbor_window": 3,
                "sequence_intent_weights": {
                    "timeline": 0.35,
                    "explanatory": 0.25,
                    "decision": 0.20,
                    "pattern": 0.05,
                    "balanced": 0.10,
                },
            },
        }
        memories = [
            {
                "id": "s1:1:0",
                "text": "chose SQLite for database",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "turn": 1,
                "ts": "2026-01-01T00:00:00+00:00",
                "source_reliability": 0.9,
                "embedding": [0.95, 0.31],
            },
            {
                "id": "s1:2:0",
                "text": "SQLite locking issues detected",
                "stream": "rough",
                "type": "fact",
                "session_id": "s1",
                "turn": 2,
                "ts": "2026-01-02T00:00:00+00:00",
                "source_reliability": 0.9,
                "embedding": [0.90, 0.35],
            },
            {
                "id": "s1:3:0",
                "text": "migrated to Postgres",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "turn": 3,
                "ts": "2026-01-03T00:00:00+00:00",
                "source_reliability": 0.9,
                "embedding": [0.92, 0.38],
            },
        ]

        with patch("app.retrieval_pipeline.config.load_settings", return_value=base_settings):
            with patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=memories):
                with patch("app.retrieval_pipeline.retriever.embed", return_value=[np.array([0.91, 0.36], dtype=np.float32)]):
                    hits = retrieve_memories(
                        query="database migration",
                        session_id=None,
                        mode="both",
                        top_k=3,
                        min_similarity=0.0,
                    )

        self.assertEqual(len(hits), 3)
        for hit in hits:
            self.assertIn("sequence_score", hit)


class MultiHeadTests(unittest.TestCase):
    """Tests for S1-A: Multi-Head Q/K Search."""

    def test_subspace_slices_even_split(self):
        """Array splits evenly into subspaces."""
        from app.retrieval_pipeline.retriever import _subspace_slices
        vec = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        slices = _subspace_slices(vec, 2)
        self.assertEqual(len(slices), 2)
        np.testing.assert_array_almost_equal(slices[0], [1.0, 2.0])
        np.testing.assert_array_almost_equal(slices[1], [3.0, 4.0])

    def test_subspace_slices_handles_odd(self):
        """Odd-length arrays split into roughly equal parts."""
        from app.retrieval_pipeline.retriever import _subspace_slices
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        slices = _subspace_slices(vec, 2)
        self.assertEqual(len(slices), 2)

    def test_subspace_activation_weights(self):
        """Activation weights sum to 1 and reflect subspace energy."""
        from app.retrieval_pipeline.retriever import _subspace_activation_weights
        vec = np.array([3.0, 4.0, 0.0, 0.0], dtype=np.float32)
        weights = _subspace_activation_weights(vec, 2)
        self.assertAlmostEqual(sum(weights), 1.0)
        self.assertGreater(weights[0], weights[1])

    def test_multi_head_disabled_gives_raw_cosine(self):
        """When multi_head is disabled, returns raw cosine similarity."""
        from app.retrieval_pipeline.retriever import _multi_head_similarity
        q = np.array([1.0, 0.0], dtype=np.float32)
        k = np.array([0.8, 0.6], dtype=np.float32)
        result = _multi_head_similarity(q, k, "balanced", {"enabled": False, "multi_head_enabled": False})
        self.assertAlmostEqual(result["raw_similarity"], result["match_score"])

    def test_multi_head_enabled_alters_score(self):
        """Multi-head can change the score vs raw cosine."""
        from app.retrieval_pipeline.retriever import _multi_head_similarity
        q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        k = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.float32)
        config = {
            "enabled": True,
            "multi_head_enabled": True,
            "multi_head_num_subspaces": 2,
            "multi_head_subspace_blend": 0.3,
            "multi_head_aspect_alpha": 0.5,
            "multi_head_intent_weights": {},
        }
        result = _multi_head_similarity(q, k, "balanced", config)
        self.assertIn("raw_similarity", result)
        self.assertIn("multi_head_score", result)
        self.assertIn("match_score", result)

    def test_multi_head_uses_intent_weights(self):
        """Intent weights affect head contribution."""
        from app.retrieval_pipeline.retriever import _multi_head_similarity
        q = np.array([1.0, 0.5, 0.0, 0.0], dtype=np.float32)
        k = np.array([0.8, 0.6, 0.0, 0.0], dtype=np.float32)
        config_timeline = {
            "enabled": True,
            "multi_head_enabled": True,
            "multi_head_num_subspaces": 2,
            "multi_head_subspace_blend": 0.3,
            "multi_head_aspect_alpha": 0.5,
            "multi_head_intent_weights": {
                "timeline": {"subspace_1": 2.0, "subspace_2": 0.5},
            },
        }
        config_balanced = {
            "enabled": True,
            "multi_head_enabled": True,
            "multi_head_num_subspaces": 2,
            "multi_head_subspace_blend": 0.3,
            "multi_head_aspect_alpha": 0.5,
            "multi_head_intent_weights": {
                "balanced": {},
            },
        }
        result_tl = _multi_head_similarity(q, k, "timeline", config_timeline)
        result_bl = _multi_head_similarity(q, k, "balanced", config_balanced)
        self.assertNotEqual(result_tl["multi_head_score"], result_bl["multi_head_score"])

    def test_retrieve_memories_multi_head_disabled_by_default(self):
        """Default config does not change behavior when multi_head is off."""
        base_settings = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval_rerank_pool_k": 34,
            "retrieval": {"min_reliability": 0.0},
            "step1": {
                "enabled": False,
                "multi_head_enabled": False,
            },
        }
        memories = [
            {
                "id": "s1:1:0",
                "text": "use session_id for dedupe",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "ts": "2026-01-01T00:00:00+00:00",
                "source_reliability": 0.9,
                "embedding": [0.9, 0.0],
            },
        ]

        with patch("app.retrieval_pipeline.config.load_settings", return_value=base_settings):
            with patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=memories):
                with patch("app.retrieval_pipeline.retriever.embed", return_value=[np.array([0.9, 0.0], dtype=np.float32)]):
                    hits = retrieve_memories(
                        query="dedupe rule",
                        session_id=None,
                        mode="both",
                        top_k=5,
                        min_similarity=0.0,
                    )

        self.assertEqual(len(hits), 1)
        self.assertNotIn("multi_head_score", hits[0])

    def test_retrieve_memories_multi_head_stores_debug_fields(self):
        """When multi_head is on, debug fields are stored in hits."""
        base_settings = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval_rerank_pool_k": 34,
            "retrieval": {"min_reliability": 0.0},
            "step1": {
                "enabled": True,
                "multi_head_enabled": True,
                "multi_head_num_subspaces": 2,
                "multi_head_subspace_blend": 0.3,
                "multi_head_aspect_alpha": 0.5,
                "multi_head_intent_weights": {},
            },
        }
        memories = [
            {
                "id": "s1:1:0",
                "text": "use session_id for dedupe",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "ts": "2026-01-01T00:00:00+00:00",
                "source_reliability": 0.9,
                "embedding": [0.9, 0.0, 0.0, 0.0],
            },
        ]

        with patch("app.retrieval_pipeline.config.load_settings", return_value=base_settings):
            with patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=memories):
                with patch("app.retrieval_pipeline.retriever.embed", return_value=[np.array([0.9, 0.0, 0.0, 0.0], dtype=np.float32)]):
                    hits = retrieve_memories(
                        query="dedupe rule",
                        session_id=None,
                        mode="both",
                        top_k=5,
                        min_similarity=0.0,
                    )

        self.assertEqual(len(hits), 1)
        self.assertIn("multi_head_score", hits[0])
        self.assertIn("subspace_scores", hits[0])


class LnnOdeRerankTests(unittest.TestCase):
    def setUp(self):
        self.query = "dedupe rule"
        self.lnn_cfg = {
            "enabled": True,
            "use_ode_rerank": True,
            "ode_dt": 0.1,
            "ode_steps": 10,
            "alpha": 0.3,
            "beta": 1.0,
            "weights_sim_floor": 0.45,
            "learning_rate": 0.01,
            "tau_default": 0.5,
            "tau_boost": 0.05,
            "hebbian_threshold": 0.3,
        }

    def test_ode_settle_activates_connected_neurons(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
            _make_hit("iso", "deployment unrelated topic random words", "rough", 0.4),
            _make_hit("l1", "dedupe strategy apply rule", "learnings", 0.9),
        ]
        hits[0]["memory"]["tau"] = 0.7
        hits[1]["memory"]["tau"] = 0.6
        hits[2]["memory"]["tau"] = 0.3
        hits[3]["memory"]["tau"] = 0.8

        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "iso": np.array([0.0, 1.0], dtype=np.float32),
            "l1": np.array([0.5, 0.87], dtype=np.float32),
        }

        result = _step2_1_rerank(hits, self.query, emb_by_id, alpha=1.0,
                                 step2_config={"attention_mask_enabled": True, "centrality_enabled": True},
                                 lnn_config=self.lnn_cfg)

        self.assertEqual(len(result), len(hits))
        for h in result:
            self.assertIn("ode_activation", h)
            self.assertIn("ode_base_score", h)
            self.assertGreaterEqual(h["ode_activation"], 0.0)

    def test_ode_settle_propagates_activation_through_weights(self):
        base_scores = np.array([0.6, 0.3, 0.5], dtype=np.float32)
        emb_by_id = {
            "a": np.array([1.0, 0.0, 0.0], dtype=np.float32),
            "b": np.array([0.0, 1.0, 0.0], dtype=np.float32),
            "c": np.array([0.95, 0.0, 0.3], dtype=np.float32),
        }
        tau_values = np.array([0.8, 0.3, 0.6], dtype=np.float32)
        memory_ids = ["a", "b", "c"]

        lnn_cfg = {"ode_dt": 0.1, "ode_steps": 10, "alpha": 0.3, "beta": 1.0, "weights_sim_floor": 0.45}

        h, _ = _ode_settle(base_scores, emb_by_id, tau_values, memory_ids, lnn_cfg)

        self.assertEqual(len(h), 3)
        self.assertGreater(h[0], 0.0, "a should stay active")
        self.assertGreater(h[0], h[1], "a (connected to c) should beat b (isolated)")

    def test_ode_rerank_rescored_fields(self):
        hits = [
            _make_hit("r1", "dedupe using session id", "rough", 0.8),
            _make_hit("r2", "dedupe collision event fix", "rough", 0.7),
        ]
        hits[0]["memory"]["tau"] = 0.7
        hits[1]["memory"]["tau"] = 0.6
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }

        result = _step2_1_rerank(hits, self.query, emb_by_id, alpha=1.0,
                                 step2_config={"attention_mask_enabled": True, "centrality_enabled": True},
                                 lnn_config=self.lnn_cfg)

        self.assertEqual(len(result), 2)
        for h in result:
            self.assertIn("ode_activation", h)
            self.assertIn("ode_base_score", h)
            self.assertIn("step2_bonus", h)


class ExpandingActivationTests(unittest.TestCase):
    def setUp(self):
        self.query = "database fix"
        self.lnn_cfg = {
            "enabled": True,
            "use_ode_rerank": True,
            "ode_dt": 0.1,
            "ode_steps": 10,
            "alpha": 0.3,
            "beta": 1.0,
            "weights_sim_floor": 0.45,
            "learning_rate": 0.01,
            "tau_default": 0.5,
            "tau_boost": 0.05,
            "hebbian_threshold": 0.3,
            "expansion_threshold": 0.35,
            "min_edge_weight": 0.30,
            "neighbors_per_active_memory": 8,
            "max_active_memories": 200,
            "max_expansion_hops": 2,
            "min_expansion_score": 0.12,
            "inherited_activation_gamma": 0.5,
            "debug_activation_trace": True,
        }

    def _make_expanding_hits(self):
        hits = [
            _make_hit("r1", "sqlite migration issue", "rough", 0.82),
            _make_hit("r2", "dedupe collision bug", "rough", 0.74),
            _make_hit("r3", "auth schema problem", "rough", 0.48),
        ]
        hits[0]["memory"]["tau"] = 0.7
        hits[1]["memory"]["tau"] = 0.6
        hits[2]["memory"]["tau"] = 0.4

        hits[0]["memory"]["outgoing_weights"] = {"hidden_h": 0.90, "hidden_f": 0.50}

        hidden_h = {
            "id": "hidden_h",
            "text": "session_id + event_id dedupe rule",
            "stream": "learnings",
            "type": "decision",
            "session_id": "test",
            "ts": "2026-02-01T00:00:00+00:00",
            "source_reliability": 0.9,
            "tau": 0.65,
            "outgoing_weights": None,
            "embedding": None,
        }
        hidden_f = {
            "id": "hidden_f",
            "text": "backup workflow clean",
            "stream": "workflow",
            "type": "workflow",
            "session_id": "test",
            "ts": "2026-02-01T00:00:00+00:00",
            "source_reliability": 0.9,
            "tau": 0.55,
            "outgoing_weights": None,
            "embedding": None,
        }

        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
            "r3": np.array([0.0, 1.0], dtype=np.float32),
        }

        return hits, emb_by_id, hidden_h, hidden_f

    def _make_mock_repo(self, hidden_h, hidden_f):
        mock_repo = unittest.mock.MagicMock()

        def get_neighbors(memory_id, min_weight=0.35, max_neighbors=8):
            if memory_id == "r1":
                candidates = [("hidden_h", 0.90, 0.65), ("hidden_f", 0.50, 0.55)]
                return [(nid, w, t) for nid, w, t in candidates if w >= min_weight]
            return []

        def query_ids(memory_ids):
            result = {}
            if "hidden_h" in memory_ids:
                result["hidden_h"] = hidden_h
            if "hidden_f" in memory_ids:
                result["hidden_f"] = hidden_f
            return result

        mock_repo.get_strong_neighbors.side_effect = get_neighbors
        mock_repo.query_by_ids.side_effect = query_ids
        mock_repo.update_lnn_state = unittest.mock.MagicMock()
        return mock_repo

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_pulls_in_graph_neighbor(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_get_repo.return_value = self._make_mock_repo(hidden_h, hidden_f)

        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, self.lnn_cfg)

        result_ids = {str(r.get("memory", {}).get("id") or "") for r in result}
        self.assertIn("hidden_h", result_ids,
                      "expanding activation should pull hidden_h into results")

        hidden_result = next(r for r in result
                            if str(r.get("memory", {}).get("id") or "") == "hidden_h")
        self.assertIn("activation_path", hidden_result)
        self.assertTrue(hidden_result.get("expanded_activation"))

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_no_neighbors_when_all_below_threshold(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_get_repo.return_value = self._make_mock_repo(hidden_h, hidden_f)

        no_expand_cfg = dict(self.lnn_cfg, expansion_threshold=0.99)
        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, no_expand_cfg)

        result_ids = {str(r.get("memory", {}).get("id") or "") for r in result}
        self.assertNotIn("hidden_h", result_ids,
                         "high expansion_threshold should prevent neighbor expansion")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_respects_max_active_limit(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_get_repo.return_value = self._make_mock_repo(hidden_h, hidden_f)

        small_cfg = dict(self.lnn_cfg, max_active_memories=3)
        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, small_cfg)

        self.assertLessEqual(len(result), 3,
                             "should respect max_active_memories")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_respects_candidate_filters(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_get_repo.return_value = self._make_mock_repo(hidden_h, hidden_f)
        filters = CandidateFilters(
            recency_days=None,
            session_id="test",
            session_bias=True,
            memory_types=None,
            mode="rough",
            min_reliability=0.8,
        )

        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, self.lnn_cfg, filters=filters)

        result_ids = {str(r.get("memory", {}).get("id") or "") for r in result}
        self.assertNotIn("hidden_h", result_ids,
                         "expansion should not bypass mode/reliability/session filters")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_skips_missing_neighbor_records(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_repo = self._make_mock_repo(hidden_h, hidden_f)
        mock_repo.query_by_ids.return_value = {}
        mock_repo.query_by_ids.side_effect = None
        mock_get_repo.return_value = mock_repo

        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, self.lnn_cfg)

        self.assertTrue(all(r.get("memory") for r in result),
                        "missing graph targets should not produce empty memory records")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_persists_hebbian_weight_updates(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_repo = self._make_mock_repo(hidden_h, hidden_f)
        mock_get_repo.return_value = mock_repo

        _expanding_ode_rerank_hits(hits, self.query, emb_by_id, self.lnn_cfg)

        self.assertTrue(mock_repo.batch_update_weights.called,
                        "expanding activation should persist co-activation deltas")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_activation_path_tracing(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_get_repo.return_value = self._make_mock_repo(hidden_h, hidden_f)

        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, self.lnn_cfg)

        for r in result:
            mid = str(r.get("memory", {}).get("id") or "")
            if mid in ("r1", "r2", "r3"):
                self.assertIn("seed", r.get("activation_path") or [],
                              f"seed memory {mid} should have seed activation path")
            elif mid == "hidden_h":
                path = r.get("activation_path") or []
                self.assertIn("r1", str(path),
                              f"hidden_h should be activated by r1")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_weak_edges_filtered(self, mock_get_repo):
        hits, emb_by_id, hidden_h, hidden_f = self._make_expanding_hits()
        mock_get_repo.return_value = self._make_mock_repo(hidden_h, hidden_f)

        strict_cfg = dict(self.lnn_cfg, min_edge_weight=0.55)
        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, strict_cfg)

        result_ids = {str(r.get("memory", {}).get("id") or "") for r in result}
        self.assertNotIn("hidden_f", result_ids,
                         "hidden_f with edge weight 0.50 should be filtered at min_edge_weight 0.55")

    @patch("app.storage.memories.get_memory_repository")
    def test_expanding_still_included_when_no_graph_edges(self, mock_get_repo):
        hits = [
            _make_hit("r1", "sqlite migration issue", "rough", 0.82),
            _make_hit("r2", "dedupe bug", "rough", 0.74),
        ]
        hits[0]["memory"]["tau"] = 0.7
        hits[1]["memory"]["tau"] = 0.6
        emb_by_id = {
            "r1": np.array([1.0, 0.0], dtype=np.float32),
            "r2": np.array([0.87, 0.5], dtype=np.float32),
        }

        mock_repo = unittest.mock.MagicMock()
        mock_repo.get_strong_neighbors.return_value = []
        mock_repo.query_by_ids.return_value = {}
        mock_repo.update_lnn_state = unittest.mock.MagicMock()
        mock_get_repo.return_value = mock_repo

        result = _expanding_ode_rerank_hits(hits, self.query, emb_by_id, self.lnn_cfg)

        self.assertEqual(len(result), 2)
        for r in result:
            self.assertIn("ode_activation", r)
            self.assertIn("expanded_activation", r)
            self.assertTrue(r["expanded_activation"])


if __name__ == "__main__":
    unittest.main()
