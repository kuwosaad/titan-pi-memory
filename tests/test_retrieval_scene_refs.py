import unittest
from unittest.mock import patch

from app.save_pipeline.pipeline import retrieve_memory_brief


class RetrievalSceneReferenceTests(unittest.TestCase):
    @patch("app.patterns.retrieval.retrieve_accepted_patterns", return_value=[])
    @patch("app.retrieval_pipeline.retriever.retrieve_memories")
    @patch("app.save_pipeline.pipeline.route_query")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_search_returns_deduped_bounded_scene_refs_by_default(
        self,
        mock_load_settings,
        mock_route_query,
        mock_retrieve_memories,
        _mock_patterns,
    ):
        mock_load_settings.return_value = {"step2": {"cluster_compression_enabled": False}}
        mock_route_query.return_value = {
            "use_memory": True,
            "mode": "both",
            "top_k": 8,
            "summary_mode": None,
        }
        mock_retrieve_memories.return_value = [
            {
                "memory": {
                    "id": "m1",
                    "text": "bounded result",
                    "scene_id": "scene-1",
                    "evidence_status": "complete",
                    "evidence_version": 1,
                    "missing_source_event_ids": [],
                    "provenance": {"user": "private source text"},
                    "embedding": [0.1, 0.2],
                    "_embedding_blob": b"private-vector",
                    "h": 0.9,
                    "tau": 0.3,
                    "outgoing_weights": {"m2": 1.0},
                    "raw_events": [{"event_id": "e-private"}],
                    "messages": [{"role": "user", "content": "private"}],
                    "tool_calls": [{"name": "read", "result": "private"}],
                }
            },
            {
                "memory": {
                    "id": "m2",
                    "text": "same scene",
                    "scene_id": "scene-1",
                }
            },
        ]

        result = retrieve_memory_brief(query="bounded scene")

        self.assertEqual(
            result["scene_refs"],
            [
                {
                    "scene_id": "scene-1",
                    "evidence_status": "complete",
                    "evidence_version": 1,
                    "missing_source_event_ids": [],
                }
            ],
        )
        self.assertEqual(result["scenes"], [])
        self.assertEqual(result["scene_brief"], "")
        self.assertNotIn("raw_events", result["scene_refs"][0])
        for private_field in (
            "provenance",
            "embedding",
            "_embedding_blob",
            "h",
            "tau",
            "outgoing_weights",
            "raw_events",
            "messages",
            "tool_calls",
            "evidence_status",
            "evidence_version",
            "missing_source_event_ids",
        ):
            self.assertNotIn(private_field, result["memories"][0])

    @patch("app.patterns.retrieval.retrieve_accepted_patterns", return_value=[])
    @patch("app.retrieval_pipeline.retriever.retrieve_memories")
    @patch("app.save_pipeline.pipeline.route_query")
    @patch("app.retrieval_pipeline.config.load_settings")
    def test_include_scenes_is_legacy_alias_for_lightweight_refs(
        self,
        mock_load_settings,
        mock_route_query,
        mock_retrieve_memories,
        _mock_patterns,
    ):
        mock_load_settings.return_value = {"step2": {"cluster_compression_enabled": False}}
        mock_route_query.return_value = {
            "use_memory": True,
            "mode": "both",
            "top_k": 8,
            "summary_mode": None,
        }
        mock_retrieve_memories.return_value = [
            {"memory": {"id": "m1", "text": "result", "scene_id": "scene-1"}}
        ]

        result = retrieve_memory_brief(query="bounded scene", include_scenes=True)

        self.assertEqual(result["scenes"], result["scene_refs"])
        self.assertEqual(
            set(result["scenes"][0]),
            {"scene_id", "evidence_status", "evidence_version", "missing_source_event_ids"},
        )


if __name__ == "__main__":
    unittest.main()
