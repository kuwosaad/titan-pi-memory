import unittest
from unittest.mock import patch

import numpy as np

from app.retrieval_pipeline.retriever import _query_aspects, retrieve_memories
from app.save_pipeline.pipeline import retrieve_memory_brief


class RetrievalQualityRegressionTests(unittest.TestCase):
    def test_profile_aspects_expand_only_actor_scoped_personal_questions(self):
        config = {
            "query_aspects_enabled": True,
            "profile_aspect_expansion_enabled": True,
            "max_query_aspects": 3,
            "min_aspect_tokens": 2,
        }
        personal = _query_aspects(
            "How does Saad prefer agents to explain things, and what frustrates him in collaboration?",
            config,
        )
        technical = _query_aspects("How does this retrieval pipeline explain ranking?", config)

        self.assertEqual(len(personal), 3)
        self.assertIn("root cause", personal[1])
        self.assertIn("delegation", personal[2])
        self.assertEqual(technical, ["How does this retrieval pipeline explain ranking?"])

    @patch("app.patterns.retrieval.retrieve_accepted_patterns", return_value=[])
    @patch("app.save_pipeline.pipeline.get_scenes")
    @patch("app.retrieval_pipeline.retriever.retrieve_memories")
    @patch("app.save_pipeline.pipeline.route_query")
    @patch("app.retrieval_pipeline.config.load_settings")
    @patch("app.save_pipeline.dedup_buffer.peek_dedup_buffer")
    def test_pending_buffer_entries_do_not_bypass_ranking_or_limit(
        self,
        mock_peek_buffer,
        mock_load_settings,
        mock_route_query,
        mock_retrieve_memories,
        mock_get_scenes,
        _mock_patterns,
    ):
        mock_load_settings.return_value = {
            "dedup": {"enabled": True},
            "step2": {"cluster_compression_enabled": False},
        }
        mock_route_query.return_value = {
            "schema_version": "v2",
            "use_memory": True,
            "mode": "both",
            "top_k": 1,
            "summary_mode": None,
            "intent": "pattern",
        }
        mock_retrieve_memories.return_value = [
            {
                "score": 0.91,
                "memory": {
                    "id": "relevant-1",
                    "text": "Kuwo turns discontinuity into systems of continuity.",
                    "stream": "learnings",
                    "type": "pattern",
                    "session_id": "psychology",
                    "scene_id": "psychology:scene:1",
                },
            }
        ]
        mock_peek_buffer.return_value = [
            {
                "id": "irrelevant-buffer-1",
                "text": "Local Pi psycho skills shadow packaged copies when names collide.",
                "stream": "rough",
                "type": "issue",
                "session_id": "recent-maintenance",
                "scene_id": "recent-maintenance:scene:1",
                "_buffer_ts": "2026-07-12T00:00:00+00:00",
            }
        ]

        result = retrieve_memory_brief(
            query="What psychological patterns describe Kuwo?",
            limit=1,
            include_scenes=False,
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual([memory["id"] for memory in result["memories"]], ["relevant-1"])
        self.assertEqual(result["scenes"], [])
        mock_peek_buffer.assert_not_called()
        mock_get_scenes.assert_not_called()

    @patch("app.retrieval_pipeline.config.load_settings")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    def test_semantic_paraphrases_collapse_before_final_results(
        self,
        mock_query_candidates,
        mock_embed,
        mock_load_settings,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {
                "enabled": True,
                "token_jaccard_threshold": 0.82,
                "embedding_similarity_threshold": 0.93,
                "embedding_min_token_containment": 0.75,
            },
        }
        mock_query_candidates.return_value = [
            {
                "id": "conflict-1",
                "text": "Local Pi skills override packaged skills when their names collide.",
                "stream": "rough",
                "type": "issue",
                "session_id": "s1",
                "scene_id": "s1:scene:1",
                "ts": "2026-07-11T00:00:00+00:00",
                "embedding": [1.0, 0.0],
                "source_reliability": 0.9,
                "verification_status": "unverified",
            },
            {
                "id": "conflict-2",
                "text": "When names collide, local Pi skills override the packaged skills.",
                "stream": "rough",
                "type": "issue",
                "session_id": "s2",
                "scene_id": "s2:scene:2",
                "ts": "2026-07-12T00:00:00+00:00",
                "embedding": [0.94, 0.341],
                "source_reliability": 0.9,
                "verification_status": "unverified",
            },
            {
                "id": "scene-1",
                "text": "Titan preserves complete content inside each bounded scene.",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s3",
                "scene_id": "s3:scene:3",
                "ts": "2026-07-10T00:00:00+00:00",
                "embedding": [0.8, 0.6],
                "source_reliability": 0.9,
                "verification_status": "verified",
            },
        ]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(
            query="How do local Pi skill name collisions work?",
            top_k=8,
            min_similarity=0.0,
        )

        ids = [hit["memory"]["id"] for hit in hits]
        self.assertEqual(len({memory_id for memory_id in ids if memory_id.startswith("conflict-")}), 1)
        duplicate_hit = next(hit for hit in hits if hit["memory"]["id"].startswith("conflict-"))
        self.assertEqual(duplicate_hit["duplicate_count"], 2)
        self.assertEqual(set(duplicate_hit["duplicate_memory_ids"]), {"conflict-1", "conflict-2"})
        self.assertEqual(set(duplicate_hit["duplicate_scene_ids"]), {"s1:scene:1", "s2:scene:2"})

    @patch("app.retrieval_pipeline.config.load_settings")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    def test_near_duplicate_collapse_preserves_opposing_decisions(
        self,
        mock_query_candidates,
        mock_embed,
        mock_load_settings,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {
                "enabled": True,
                "token_jaccard_threshold": 0.82,
                "embedding_similarity_threshold": 0.93,
                "embedding_min_token_containment": 0.75,
            },
        }
        mock_query_candidates.return_value = [
            {
                "id": "decision-enable",
                "text": "Enable automatic scene expansion for memory queries.",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "scene_id": "s1:scene:1",
                "ts": "2026-07-11T00:00:00+00:00",
                "embedding": [1.0, 0.0],
                "source_reliability": 0.9,
            },
            {
                "id": "decision-disable",
                "text": "Disable automatic scene expansion for memory queries.",
                "stream": "learnings",
                "type": "decision",
                "session_id": "s2",
                "scene_id": "s2:scene:2",
                "ts": "2026-07-12T00:00:00+00:00",
                "embedding": [0.999, 0.001],
                "source_reliability": 0.9,
            },
        ]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(query="automatic scene expansion", top_k=8, min_similarity=0.0)

        self.assertEqual(
            {hit["memory"]["id"] for hit in hits},
            {"decision-enable", "decision-disable"},
        )

    @patch("app.retrieval_pipeline.retriever._step2_1_rerank")
    @patch("app.retrieval_pipeline.config.load_settings")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    def test_configured_rerank_pool_bounds_step2_candidates(
        self,
        mock_query_candidates,
        mock_embed,
        mock_load_settings,
        mock_step2,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 2,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": True,
            "retrieval_rerank_alpha": 1.0,
            "retrieval_rerank_pool_k": 2,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "step2": {"attention_mask_enabled": True},
            "step1": {"enabled": False},
            "lnn": {"enabled": False},
        }
        mock_query_candidates.return_value = [
            {
                "id": f"m-{index}",
                "text": f"Distinct retrieval candidate number {index}.",
                "stream": "rough",
                "type": "fact",
                "session_id": f"s-{index}",
                "scene_id": f"s-{index}:scene:1",
                "ts": f"2026-07-{10 + index:02d}T00:00:00+00:00",
                "embedding": [1.0 - index * 0.1, index * 0.1],
                "source_reliability": 0.9,
                "verification_status": "unverified",
            }
            for index in range(4)
        ]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        mock_step2.side_effect = lambda hits, *_args, **_kwargs: hits

        retrieve_memories(query="retrieval candidate", top_k=2, min_similarity=0.0)

        step2_hits = mock_step2.call_args.args[0]
        self.assertEqual(len(step2_hits), 2)

    @patch("app.retrieval_pipeline.retriever._step2_1_rerank")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_irrelevant_query_abstains_before_lnn_reranking(
        self,
        mock_load_settings,
        mock_embed,
        mock_lexical_candidates,
        mock_semantic_candidates,
        mock_step2,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": True,
            "retrieval_rerank_alpha": 1.0,
            "retrieval_rerank_pool_k": 8,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": True,
                "query_aspects_enabled": False,
                "min_direct_similarity": 0.70,
                "strong_lexical_coverage": 0.80,
                "lexical_override_min_similarity": 0.25,
            },
            "step1": {"enabled": False},
            "step2": {"attention_mask_enabled": True},
            "lnn": {"enabled": True, "use_ode_rerank": True},
        }
        weak_candidate = {
            "id": "preference-memory",
            "text": "Saad prefers concise explanations and root-cause analysis.",
            "stream": "learnings",
            "type": "user_preference",
            "session_id": "preferences",
            "scene_id": "preferences:scene:1",
            "ts": "2026-07-01T00:00:00+00:00",
            "embedding": [0.40, 0.9165],
            "source_reliability": 0.9,
        }
        mock_lexical_candidates.return_value = [weak_candidate]
        mock_semantic_candidates.return_value = [weak_candidate]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(
            query="purple giraffe quantum bakery underwater violin preference",
            top_k=8,
            min_similarity=0.0,
        )

        self.assertEqual(hits, [])
        mock_step2.assert_not_called()

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_hybrid_candidates_recover_broad_preference_facets_missing_from_fts(
        self,
        mock_load_settings,
        mock_embed,
        mock_lexical_candidates,
        mock_semantic_candidates,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": True,
                "query_aspects_enabled": True,
                "max_query_aspects": 3,
                "min_aspect_tokens": 2,
                "min_direct_similarity": 0.70,
                "strong_lexical_coverage": 0.80,
                "lexical_override_min_similarity": 0.25,
            },
            "step1": {"enabled": False},
            "step2": {},
            "lnn": {},
        }
        generic = {
            "id": "lexical-generic",
            "text": "Agent collaboration research discussed general preferences.",
            "stream": "rough",
            "type": "fact",
            "session_id": "research",
            "scene_id": "research:scene:1",
            "ts": "2026-07-03T00:00:00+00:00",
            "embedding": [0.4, 0.4],
            "source_reliability": 0.9,
        }
        explanation = {
            "id": "simple-explanations",
            "text": "Saad prefers concise, direct explanations with simple wording first.",
            "stream": "learnings",
            "type": "user_preference",
            "session_id": "preferences",
            "scene_id": "preferences:scene:1",
            "ts": "2026-06-01T00:00:00+00:00",
            "embedding": [1.0, 0.0],
            "source_reliability": 0.9,
        }
        collaboration = {
            "id": "delegation-frustration",
            "text": "Saad is frustrated by slow or unreliable delegation.",
            "stream": "learnings",
            "type": "user_preference",
            "session_id": "preferences",
            "scene_id": "preferences:scene:2",
            "ts": "2026-06-02T00:00:00+00:00",
            "embedding": [0.0, 1.0],
            "source_reliability": 0.9,
        }
        mock_lexical_candidates.return_value = [generic]
        mock_semantic_candidates.return_value = [generic, explanation, collaboration]
        mock_embed.return_value = [
            np.array([0.7071, 0.7071], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]

        hits = retrieve_memories(
            query="How does Saad prefer agents to explain things, and what frustrates him in collaboration?",
            top_k=8,
            min_similarity=0.0,
        )

        self.assertEqual(mock_lexical_candidates.call_count, 1)
        self.assertEqual(mock_semantic_candidates.call_count, 1)
        self.assertTrue({"simple-explanations", "delegation-frustration"}.issubset(
            {hit["memory"]["id"] for hit in hits}
        ))

    @patch("app.retrieval_pipeline.retriever._step2_1_rerank")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_duplicate_collapse_preserves_best_query_facet_for_rerank_pool(
        self,
        mock_load_settings,
        mock_embed,
        mock_candidates,
        mock_step2,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 1,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": True,
            "retrieval_rerank_alpha": 1.0,
            "retrieval_rerank_pool_k": 1,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {
                "enabled": True,
                "token_jaccard_threshold": 0.99,
                "embedding_similarity_threshold": 0.999,
                "embedding_min_token_containment": 1.0,
                "candidate_scan_multiplier": 3,
            },
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": False,
                "query_aspects_enabled": True,
                "max_query_aspects": 3,
                "min_aspect_tokens": 2,
                "multi_aspect_pool_k": 1,
                "min_direct_similarity": 0.0,
                "strong_lexical_coverage": 1.0,
                "lexical_override_min_similarity": 1.0,
            },
            "step1": {"enabled": False},
            "step2": {"attention_mask_enabled": True},
            "lnn": {"enabled": False},
        }
        mock_candidates.return_value = [
            {
                "id": "full-query-match",
                "text": "Explanation preferences are documented here.",
                "stream": "learnings", "type": "fact", "session_id": "s1", "scene_id": "s1:scene:1",
                "ts": "2026-07-01T00:00:00+00:00", "embedding": [0.90, 0.40], "source_reliability": 0.9,
            },
            {
                "id": "second-facet-match",
                "text": "Delegation reliability is documented here.",
                "stream": "learnings", "type": "fact", "session_id": "s2", "scene_id": "s2:scene:1",
                "ts": "2026-07-01T00:00:00+00:00", "embedding": [0.40, 0.9165], "source_reliability": 0.9,
            },
        ]
        mock_embed.return_value = [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]
        mock_step2.side_effect = lambda hits, *_args, **_kwargs: hits

        retrieve_memories(
            query="How should Saad explain things, and what frustrates collaboration?",
            top_k=1,
            min_similarity=0.0,
        )

        rerank_input = mock_step2.call_args.args[0]
        self.assertEqual([hit["memory"]["id"] for hit in rerank_input], ["second-facet-match"])

    @patch("app.retrieval_pipeline.retriever._step2_1_rerank")
    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_final_selection_diversifies_scenes_events_and_semantic_duplicates(
        self,
        mock_load_settings,
        mock_embed,
        mock_candidates,
        mock_step2,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": True,
            "retrieval_rerank_alpha": 1.0,
            "retrieval_rerank_pool_k": 8,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": False,
                "query_aspects_enabled": False,
                "min_direct_similarity": 0.70,
                "strong_lexical_coverage": 0.80,
                "lexical_override_min_similarity": 0.25,
                "max_per_scene": 1,
                "max_per_source_event": 1,
                "semantic_redundancy_threshold": 0.995,
            },
            "step1": {"enabled": False},
            "step2": {"attention_mask_enabled": True},
            "lnn": {"enabled": False},
        }

        def memory(memory_id, text, embedding, scene_id, source_event_ids):
            return {
                "id": memory_id,
                "text": text,
                "stream": "learnings",
                "type": "decision",
                "session_id": "s1",
                "scene_id": scene_id,
                "source_event_ids": source_event_ids,
                "ts": "2026-07-01T00:00:00+00:00",
                "embedding": embedding,
                "source_reliability": 0.9,
            }

        mock_candidates.return_value = [
            memory("scene-primary", "Takashi will recommend Titan to the Investment Committee.", [1.0, 0.0, 0.0], "scene-1", ["event-1"]),
            memory("scene-secondary", "Takashi requested a short Titan investment briefing.", [0.99, 0.14, 0.0], "scene-1", ["event-2"]),
            memory("event-primary", "The investor committee recommendation is planned for next week.", [0.80, 0.60, 0.0], "scene-2", ["shared-event"]),
            memory("event-secondary", "Takashi expects to introduce Titan to the committee next week.", [0.79, 0.61, 0.0], "scene-3", ["shared-event"]),
            memory("semantic-primary", "Titan positioning emphasizes durable memory across agents.", [0.80, 0.0, 0.60], "scene-4", ["event-4"]),
            memory("semantic-secondary", "Titan is positioned as persistent memory shared between agents.", [0.799, 0.0, 0.601], "scene-5", ["event-5"]),
            memory("decision-enable", "Enable automatic scene expansion for memory queries.", [0.75, 0.40, 0.524], "scene-6", ["event-6"]),
            memory("decision-disable", "Disable automatic scene expansion for memory queries.", [0.75, 0.40, 0.524], "scene-7", ["event-7"]),
        ]
        mock_embed.return_value = [np.array([1.0, 0.0, 0.0], dtype=np.float32)]
        mock_step2.side_effect = lambda hits, *_args, **_kwargs: hits

        hits = retrieve_memories(query="Takashi Titan recommendation", top_k=8, min_similarity=0.0)

        ids = {hit["memory"]["id"] for hit in hits}
        self.assertIn("scene-primary", ids)
        self.assertNotIn("scene-secondary", ids)
        self.assertIn("event-primary", ids)
        self.assertNotIn("event-secondary", ids)
        self.assertIn("semantic-primary", ids)
        self.assertNotIn("semantic-secondary", ids)
        self.assertTrue({"decision-enable", "decision-disable"}.issubset(ids))
        mock_step2.assert_called_once()

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed", side_effect=ConnectionError("embedding unavailable"))
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_keyword_fallback_rejects_single_generic_overlap_for_nonsense_query(
        self,
        mock_load_settings,
        _mock_embed,
        mock_candidates,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": False,
                "query_aspects_enabled": False,
                "min_direct_similarity": 0.70,
                "strong_lexical_coverage": 0.80,
                "lexical_override_min_similarity": 0.25,
            },
            "step1": {"enabled": False},
            "step2": {},
            "lnn": {},
        }
        mock_candidates.return_value = [{
            "id": "preference-memory",
            "text": "Saad has a preference for concise explanations.",
            "stream": "learnings",
            "type": "user_preference",
            "session_id": "preferences",
            "scene_id": "preferences:scene:1",
            "ts": "2026-07-01T00:00:00+00:00",
            "source_reliability": 0.9,
        }]

        hits = retrieve_memories(
            query="purple giraffe quantum bakery underwater violin preference",
            top_k=8,
            min_similarity=0.0,
        )

        self.assertEqual(hits, [])

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_short_exact_slug_query_can_use_lexical_admission_override(
        self,
        mock_load_settings,
        mock_embed,
        mock_candidates,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": False,
                "query_aspects_enabled": False,
                "min_direct_similarity": 0.55,
                "strong_lexical_coverage": 0.80,
                "lexical_override_min_similarity": 0.25,
                "max_lexical_override_terms": 3,
            },
            "step1": {"enabled": False},
            "step2": {},
            "lnn": {},
        }
        mock_candidates.return_value = [{
            "id": "t3code-memory",
            "text": "Kuwo uses the t3code project as a GUI for coding agents.",
            "stream": "learnings",
            "type": "user_fact",
            "session_id": "t3code",
            "scene_id": "t3code:scene:1",
            "ts": "2026-07-01T00:00:00+00:00",
            "embedding": [0.50, 0.866],
            "source_reliability": 0.9,
        }]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(query="T3 Code", top_k=8, min_similarity=0.0)

        self.assertEqual([hit["memory"]["id"] for hit in hits], ["t3code-memory"])

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_query_echo_memory_cannot_answer_its_quoted_query(
        self,
        mock_load_settings,
        mock_embed,
        mock_candidates,
    ):
        mock_load_settings.return_value = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.0,
            "retrieval_recency_days": None,
            "retrieval_session_bias": False,
            "retrieval_rerank_enabled": False,
            "retrieval": {"min_reliability": 0.0},
            "retrieval_dedup": {"enabled": False},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": False,
                "query_aspects_enabled": False,
                "min_direct_similarity": 0.55,
                "profile_min_direct_similarity": 0.50,
                "strong_lexical_coverage": 0.80,
                "lexical_override_min_similarity": 0.25,
                "max_lexical_override_terms": 3,
                "user_profile_metadata_tiebreak_enabled": True,
                "profile_score_boost": 0.20,
            },
            "step1": {"enabled": False},
            "step2": {},
            "lnn": {},
        }
        mock_candidates.return_value = [
            {
                "id": "retrieval-diagnosis",
                "text": "A retrieval issue: How does Saad prefer agents to explain things? returned irrelevant memories.",
                "stream": "rough",
                "type": "issue",
                "memory_kind": "issue",
                "speaker_focus": "kuwo",
                "session_id": "diagnosis",
                "scene_id": "diagnosis:scene:1",
                "ts": "2026-07-01T00:00:00+00:00",
                "embedding": [1.0, 0.0],
                "source_reliability": 0.9,
            },
            {
                "id": "actual-preference",
                "text": "Saad prefers concise, direct explanations with simple wording first.",
                "stream": "learnings",
                "type": "user_preference",
                "memory_kind": "user_preference",
                "speaker_focus": "kuwo",
                "session_id": "preferences",
                "scene_id": "preferences:scene:1",
                "ts": "2026-07-01T00:00:00+00:00",
                "embedding": [0.60, 0.80],
                "source_reliability": 0.9,
            },
        ]
        mock_embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        hits = retrieve_memories(
            query="How does Saad prefer agents to explain things?",
            top_k=8,
            min_similarity=0.0,
        )

        self.assertEqual([hit["memory"]["id"] for hit in hits], ["actual-preference"])


if __name__ == "__main__":
    unittest.main()
