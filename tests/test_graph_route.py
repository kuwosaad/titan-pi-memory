import unittest
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


class GraphRouteTests(unittest.TestCase):
    def test_graph_endpoint_returns_html_response(self):
        expected_html = "<html><body>graph ok</body></html>"

        with patch("app.api.routes.build_graph", return_value=expected_html) as mock_build_graph:
            client = TestClient(app)
            response = client.get("/graph", params={"session_id": "abc"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertEqual(response.text, expected_html)
        mock_build_graph.assert_called_once_with(session_id="abc")

    def test_graph_endpoint_passes_explicit_agent(self):
        with patch("app.api.routes.build_graph", return_value="<html></html>") as build:
            response = TestClient(app).get("/graph", params={"session_id": "abc", "agent": "pi"})
        self.assertEqual(response.status_code, 200)
        build.assert_called_once_with(session_id="abc", agent="pi")

    def test_graph_route_reads_selected_namespace_from_isolated_stores(self):
        from app.retrieval_pipeline.federated import FederatedRecall
        from app.storage.memories import SqliteMemoryRepository

        with tempfile.TemporaryDirectory() as temp:
            repos = {agent: SqliteMemoryRepository(Path(temp) / (agent + ".db")) for agent in ("pi", "codex")}
            for agent, repo in repos.items():
                repo.append_memories([{
                    "id": "same:1:0", "text": agent + " unique", "session_id": "same", "turn": 1,
                    "ts": "2026-01-01T00:00:00+00:00", "embedding": [1.0, 0.0],
                }])
            recall = FederatedRecall(active_agent="codex", memory_repositories=repos, include_active_buffer=False)
            repository = lambda: repos["codex"]
            with patch("app.runtime.context.get_runtime_context", return_value=SimpleNamespace(agent_name="codex")), \
                 patch("app.retrieval_pipeline.federated.get_federated_recall", return_value=recall), \
                 patch("app.storage.memories.get_memory_repository", repository), \
                 patch("app.storage.memories._REAL_GET_MEMORY_REPOSITORY", repository):
                client = TestClient(app)
                unscoped = client.get("/graph")
                scoped = client.get("/graph", params={"agent": "pi"})
            self.assertEqual(unscoped.status_code, 200)
            self.assertIn("codex unique", unscoped.text)
            self.assertIn("pi unique", unscoped.text)
            self.assertEqual(scoped.status_code, 200)
            self.assertIn("pi unique", scoped.text)
            self.assertNotIn("codex unique", scoped.text)

    def test_browser_graph_uses_stored_corrections_and_selected_scene(self):
        from app.retrieval_pipeline.federated import FederatedRecall

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            store = home / "agents" / "codex" / "out" / "memories"
            store.mkdir(parents=True)
            common = {"ts": "2026-11-23T10:00:00+00:00", "session_id": "release", "turn": 1}
            memories = [
                {**common, "id": "old", "text": "Release Friday", "type": "fact"},
                {**common, "id": "new", "text": "Release Tuesday", "type": "decision", "scene_id": "scene"},
            ]
            (store / "memories.json").write_text(json.dumps(memories))
            (store / "memories.json.relations.json").write_text(json.dumps([
                {"source": "codex", "old_id": "old", "new_id": "new", "evidence": "Move to Tuesday"},
            ]))
            (store / "scenes.json").write_text(json.dumps([
                {"scene_id": "scene", "evidence_version": 1, "evidence_status": "complete", "extraction_user_text": "Move to Tuesday"},
            ]))
            context = SimpleNamespace(shared_home=home, agent_name="pi")
            recall = FederatedRecall(active_agent="pi", titan_home=home, include_active_buffer=False)
            with patch("app.runtime.context.get_runtime_context", return_value=context), \
                 patch("app.graph.builder.get_runtime_context", return_value=context), \
                 patch("app.api.routes.get_runtime_context", return_value=context), \
                 patch("app.retrieval_pipeline.federated.get_federated_recall", return_value=recall):
                client = TestClient(app)
                page = client.get("/graph", params={"agent": "codex"})
                detail = client.get("/api/graph/memory", params={"source_agent": "codex", "memory_id": "new"})
                missing = client.get("/api/graph/memory", params={"source_agent": "pi", "memory_id": "new"})

        self.assertEqual(page.status_code, 200)
        self.assertIn('"kind": "supersedes"', page.text)
        self.assertIn('id="memoryPreviewEvidence"', page.text)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["corrections"][0]["memory"]["memoryId"], "old")
        self.assertEqual(detail.json()["scene"]["userText"], "Move to Tuesday")
        self.assertEqual(detail.headers["Cache-Control"], "no-store")
        self.assertEqual(missing.status_code, 404)

    def test_cluster_analysis_endpoint_delegates_to_cortex_analysis(self):
        expected = {"summary": "ok", "cluster_ids": [1, 2]}

        with patch("app.api.routes.analyze_memory_clusters", return_value=expected) as mock_analyze:
            client = TestClient(app)
            response = client.get(
                "/api/clusters/analyze",
                params={"cluster_ids": "1,2", "session_id": "abc", "question": "why", "detail_limit": 5},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        mock_analyze.assert_called_once_with(
            cluster_ids="1,2",
            session_id="abc",
            limit=0,
            question="why",
            detail_limit=5,
        )

    def test_clusters_endpoint_defaults_to_all_memories(self):
        expected = {"cluster_count": 3, "raw_memory_count": 2840}

        with patch("app.api.routes.inspect_memory_clusters", return_value=expected) as mock_inspect:
            client = TestClient(app)
            response = client.get("/api/clusters", params={"detail_limit": 7})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        mock_inspect.assert_called_once_with(
            session_id=None,
            limit=0,
            cluster_id=None,
            detail_limit=7,
        )


if __name__ == "__main__":
    unittest.main()
