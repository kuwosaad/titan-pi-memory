import unittest
from unittest.mock import patch

import numpy as np

from app.retrieval_pipeline import retriever
from app.retrieval_pipeline.retriever import retrieve_memories


class SecondBrainRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "retrieval_top_k": 8,
            "retrieval_min_similarity": 0.5,
            "retrieval": {"min_reliability": 0},
            "retrieval_selection": {
                "enabled": True,
                "hybrid_candidates_enabled": False,
                "query_aspects_enabled": True,
                "min_direct_similarity": 0.5,
            },
        }

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_facet_match_survives_full_query_threshold(self, settings, embed, candidates):
        settings.return_value = self.settings
        candidates.return_value = [{"id": "facet", "text": "Deployment process", "embedding": [0, 1]}]
        embed.return_value = [np.array([1, 0]), np.array([1, 0]), np.array([0, 1])]
        hits = retrieve_memories("How does testing work, and what is the deployment process?")
        self.assertEqual([h["memory"]["id"] for h in hits], ["facet"])
        self.assertEqual(hits[0]["query_similarity"], 0)
        self.assertEqual(hits[0]["direct_similarity"], 1)

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_incompatible_and_nonfinite_stored_vectors_do_not_break_search(self, settings, embed, candidates):
        settings.return_value = self.settings
        candidates.return_value = [
            {"id": "old-model", "text": "Testing old model", "embedding": [1, 0, 0]},
            {"id": "corrupt", "text": "Testing corrupted model", "embedding": [float("nan"), 0]},
            {"id": "good", "text": "Testing works", "embedding": [1, 0]},
        ]
        embed.return_value = [np.array([1, 0])]
        hits = retrieve_memories("testing")
        self.assertEqual([h["memory"]["id"] for h in hits], ["good"])
        embed.assert_called_once()

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_invalid_query_embedding_uses_keyword_fallback(self, settings, embed, candidates):
        settings.return_value = self.settings
        candidates.return_value = [{"id": "good", "text": "Testing works", "embedding": [1, 0]}]
        embed.return_value = [np.array([float("nan"), 0])]
        hits = retrieve_memories("testing")
        self.assertEqual([h["memory"]["id"] for h in hits], ["good"])
        self.assertEqual(hits[0]["retrieval_method"], "keyword_fallback")

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_hidden_only_session_falls_back_to_visible_other_session(self, settings, embed, candidates):
        settings.return_value = self.settings
        hidden = {"id": "hidden", "text": "A user message was received", "embedding": [1, 0]}
        visible = {"id": "visible", "text": "Testing works", "embedding": [1, 0]}
        candidates.side_effect = lambda _query, filters, **_kwargs: [hidden] if filters.session_bias else [hidden, visible]
        embed.return_value = [np.array([1, 0])]
        hits = retrieve_memories("testing", session_id="current")
        self.assertEqual([h["memory"]["id"] for h in hits], ["visible"])
        self.assertEqual(candidates.call_count, 2)
        self.assertEqual(embed.call_count, 1)

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=[])
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_no_candidates_does_not_invoke_embedding(self, settings, embed, _candidates):
        settings.return_value = self.settings
        self.assertEqual(retrieve_memories("testing"), [])
        embed.assert_not_called()

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_empty_query_returns_recent_memories_without_embedding(self, settings, embed, candidates):
        settings.return_value = self.settings
        candidates.return_value = [{"id": "good", "text": "Testing works", "embedding": [1, 0]}]
        self.assertEqual([h["memory"]["id"] for h in retrieve_memories("")], ["good"])
        embed.assert_not_called()

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_attention_tokenizes_each_candidate_once(self, settings, embed, candidates):
        settings.return_value = {
            "retrieval_min_similarity": 0.1,
            "retrieval": {"min_reliability": 0},
            "step2": {"enabled": True, "attention_mask_enabled": True},
        }
        candidates.return_value = [
            {"id": str(i), "text": f"Testing example {i}", "embedding": [1, 0]}
            for i in range(4)
        ]
        embed.return_value = [np.array([1, 0])]
        original_matrix = retriever._build_attention_matrix
        original_tokens = retriever._content_tokens
        counts = []

        def measured_matrix(*args, **kwargs):
            with patch.object(retriever, "_content_tokens", wraps=original_tokens) as tokens:
                matrix = original_matrix(*args, **kwargs)
                counts.append(tokens.call_count)
            return matrix

        with patch.object(retriever, "_build_attention_matrix", side_effect=measured_matrix):
            hits = retrieve_memories("testing")
        self.assertEqual(len(hits), 4)
        self.assertEqual(counts, [4])

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_vectorless_keyword_mode_avoids_repeat_embedding_and_keeps_exact_match(
        self, settings, embed, candidates
    ):
        memories = [
            {"id": str(i), "text": f"Gardening chore number {i}"}
            for i in range(21)
        ]
        memories.append({"id": "match", "text": "Encrypted backups use offline disks"})
        candidates.return_value = memories
        embed.side_effect = lambda texts: [np.array([1, 0]) for _ in texts]

        settings.return_value = self.settings
        retrieve_memories("encrypted backups")
        self.assertEqual([len(call.args[0]) for call in embed.call_args_list], [1, 22])

        embed.reset_mock()
        settings.return_value = {
            **self.settings,
            "retrieval_selection": {**self.settings["retrieval_selection"], "missing_embedding_mode": "keyword"},
        }
        for _ in range(2):
            hits = retrieve_memories("encrypted backups")
            self.assertEqual([h["memory"]["id"] for h in hits], ["match"])
            self.assertEqual(hits[0]["retrieval_method"], "keyword_fallback")
            self.assertEqual(hits[0]["direct_similarity"], 0)
        self.assertEqual([len(call.args[0]) for call in embed.call_args_list], [1, 1])

        candidates.return_value = [
            *memories,
            {"id": "semantic", "text": "Cloud storage works", "embedding": [0.7, 0.714]},
        ]
        hits = retrieve_memories("encrypted backups", top_k=1)
        self.assertEqual([h["memory"]["id"] for h in hits], ["match"])

    @patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text")
    @patch("app.retrieval_pipeline.retriever.embed")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_factual_retrieval_statement_is_not_a_query_echo(self, settings, embed, candidates):
        query = "How does Titan memory use SQLite for durable storage?"
        rows = [
            {"id": "fact", "text": "Titan memory uses SQLite for durable storage"},
            {"id": "request", "text": "The user asked to search memory for how Titan memory uses SQLite for durable storage"},
            {"id": "question", "text": "A retrieval issue: How does Titan memory use SQLite for durable storage? returned irrelevant memories."},
        ]
        embed.return_value = [np.array([1, 0])]
        for keyword_mode in (False, True):
            with self.subTest(keyword_mode=keyword_mode):
                settings.return_value = {
                    **self.settings,
                    "retrieval_selection": {
                        **self.settings["retrieval_selection"],
                        "missing_embedding_mode": "keyword" if keyword_mode else "embed",
                    },
                }
                candidates.return_value = [
                    {**row, **({} if keyword_mode else {"embedding": [1, 0]})} for row in rows
                ]
                hits = retrieve_memories(query)
                self.assertEqual([h["memory"]["id"] for h in hits], ["fact"])


if __name__ == "__main__":
    unittest.main()
