import unittest
from unittest.mock import patch

import numpy as np

from app.retrieval_pipeline.brief import build_memory_notes
from app.retrieval_pipeline.retriever import retrieve_memories
from app.save_pipeline.pipeline import retrieve_memory_brief


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
                "text": "The assistant received a telegram message from user 123456789 via the openclaw-hook:titan-karu-bridge integration.",
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
    def test_retrieval_uses_direct_scores_despite_legacy_rerank_settings(self, mock_query_memory_candidates, mock_embed, mock_load_settings):
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
            persist_lnn_state=True,
        )

        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["memory"]["id"], "s1:1:0")
        for hit in hits:
            self.assertEqual(hit["final_score"], hit["base_score"])
            self.assertNotIn("ode_activation", hit)

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
