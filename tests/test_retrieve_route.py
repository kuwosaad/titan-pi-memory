import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


class RetrieveRouteTests(unittest.TestCase):
    def test_invalid_sources_are_client_errors_before_retrieval(self):
        for sources in ("../escape", "[bad", '["valid", "../escape"]'):
            with self.subTest(sources=sources), patch("app.api.routes.retrieve_memory_brief") as recall:
                response = TestClient(app).get("/api/retrieve", params={"query": "example", "sources": sources})
                self.assertEqual(response.status_code, 400)
                recall.assert_not_called()

    def test_health_endpoint_returns_ok(self):
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_runtime_endpoint_reports_resolved_context_and_capabilities(self):
        class FauxRepository:
            capabilities = {"memory_store": True, "lnn_state_store": False}

        with (
            patch("app.retrieval_pipeline.config.load_settings", return_value={"port": 8002}),
            patch("app.storage.memories.get_memory_count", return_value=4),
            patch("app.storage.memories.get_memory_repository", return_value=FauxRepository()),
        ):
            client = TestClient(app)
            response = client.get("/api/runtime")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["memory_count"], 4)
        self.assertIn("agent_name", payload)
        self.assertIn("titan_home", payload)
        self.assertIn("trace_dir", payload)
        self.assertEqual(payload["memory_backend"], "sqlite")
        self.assertEqual(payload["memory_capabilities"]["lnn_status"], "unsupported for selected backend")

    def test_retrieve_endpoint_returns_json_from_pipeline(self):
        expected_payload = {
            "query": "what changed?",
            "mode": "both",
            "count": 0,
            "memories": [],
            "scene_refs": [],
            "brief": "",
            "route": {
                "schema_version": "v2",
                "use_memory": True,
                "mode": "both",
                "top_k": 8,
                "reason": "Ambiguous query; searching rough and learnings.",
                "summary_mode": None,
            },
        }

        with patch("app.api.routes.retrieve_memory_brief", return_value=expected_payload) as mock_retrieve:
            client = TestClient(app)
            response = client.get("/api/retrieve", params={"query": "what changed?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected_payload)
        mock_retrieve.assert_called_once_with(
            query="what changed?",
            session_id=None,
            mode=None,
            limit=8,
            max_items=None,
            max_chars=None,
            date_from=None,
            date_to=None,
            include_scenes=False,
            sources=None,
        )

    def test_retrieve_endpoint_passes_all_adapter_filters_to_pipeline(self):
        expected_payload = {"query": "what changed?", "count": 0, "memories": [], "scene_refs": []}

        with patch("app.api.routes.retrieve_memory_brief", return_value=expected_payload) as mock_retrieve:
            client = TestClient(app)
            response = client.get(
                "/api/retrieve",
                params={
                    "query": "what changed?",
                    "session_id": "session-42",
                    "mode": "rough",
                    "limit": "11",
                    "max_items": "5",
                    "max_chars": "1200",
                    "from_date": "2026-08-01",
                    "to_date": "2026-08-31",
                    "include_scenes": "true",
                    "sources": "codex,pi",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected_payload)
        mock_retrieve.assert_called_once_with(
            query="what changed?",
            session_id="session-42",
            mode="rough",
            limit=11,
            max_items=5,
            max_chars=1200,
            date_from="2026-08-01",
            date_to="2026-08-31",
            include_scenes=True,
            sources="codex,pi",
        )

    def test_retrieve_endpoint_can_return_scene_pointers_without_scene_bodies(self):
        expected_payload = {"query": "bounded scene", "count": 1, "memories": [{"scene_id": "s1:scene:e-1"}], "scenes": []}
        with patch("app.api.routes.retrieve_memory_brief", return_value=expected_payload) as mock_retrieve:
            client = TestClient(app)
            response = client.get(
                "/api/retrieve",
                params={"query": "bounded scene", "include_scenes": "false"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected_payload)
        self.assertFalse(mock_retrieve.call_args.kwargs["include_scenes"])

    def test_memories_endpoint_survives_mixed_memory_objects(self):
        class FauxMemory(dict):
            pass

        faux = FauxMemory(
            {
                "id": "s1:1:0",
                "text": "Keep Discord bridge traces normalized.",
                "stream": "rough",
                "type": "fact",
                "session_id": "s1",
                "ts": "2026-02-05T00:00:00+00:00",
                "source_reliability": 0.9,
            }
        )

        with patch("app.api.routes.get_recent_memories", return_value=[faux]):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/memories")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["memories"][0]["id"], "s1:1:0")

    def test_retrieve_endpoint_survives_embedding_backend_failure(self):
        candidate_memory = {
            "id": "s1:1:0",
            "text": "Use session_id and event_id for dedupe.",
            "stream": "learnings",
            "type": "decision",
            "session_id": "s1",
            "ts": "2026-02-05T00:00:00+00:00",
            "source_reliability": 0.9,
            "embedding": [1.0, 0.0],
        }

        with (
            patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=[candidate_memory]),
            patch("app.retrieval_pipeline.retriever.query_memory_candidates", return_value=[candidate_memory]),
            patch("app.retrieval_pipeline.retriever.embed", side_effect=ConnectionError("embedding backend unavailable")),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/retrieve", params={"query": "what rule should we use for dedupe"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["memories"][0]["id"], "s1:1:0")
        self.assertIn("MEMORY BRIEF:", payload["brief"])

    def test_retrieve_endpoint_omits_internal_embedding_fields_from_json(self):
        candidate_memory = {
            "id": "s1:1:0",
            "text": "SQLite stores embeddings as blobs.",
            "stream": "learnings",
            "type": "decision",
            "session_id": "s1",
            "ts": "2026-02-05T00:00:00+00:00",
            "source_reliability": 0.9,
            "_embedding_blob": b"binary-embedding",
            "_embedding_dim": 2,
            "_embedding_dtype": "f32",
        }

        with (
            patch("app.retrieval_pipeline.retriever.query_memory_candidates_with_text", return_value=[candidate_memory]),
            patch("app.retrieval_pipeline.retriever.query_memory_candidates", return_value=[candidate_memory]),
            patch("app.retrieval_pipeline.retriever.embed", side_effect=ConnectionError("embedding backend unavailable")),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/retrieve", params={"query": "sqlite embeddings"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertNotIn("_embedding_blob", payload["memories"][0])
        self.assertNotIn("_embedding_dim", payload["memories"][0])
        self.assertNotIn("_embedding_dtype", payload["memories"][0])


if __name__ == "__main__":
    unittest.main()
