import unittest
import tempfile
import json
import sqlite3
from contextlib import ExitStack, closing
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

    def _store(self, home: Path, agent: str, memories: list[dict], path: Path | None = None) -> Path:
        from app.storage.memories import SqliteMemoryRepository

        path = path or home / "agents" / agent / "out" / "memories" / "memory_store.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        SqliteMemoryRepository(path).append_memories([
            {"session_id": "s", "turn": 1, "ts": "2026-01-01T00:00:00+00:00", "embedding": [1.0, 0.0], **memory}
            for memory in memories
        ])
        return path

    def _client(self, context):
        stack = ExitStack()
        for target in ("app.api.routes.get_runtime_context", "app.graph.builder.get_runtime_context"):
            stack.enter_context(patch(target, return_value=context))
        self.addCleanup(stack.close)
        return TestClient(app)

    def test_graph_endpoint_passes_canonical_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self._store(home, "pi", [{"id": "s:1:0", "text": "pi memory"}])
            client = self._client(SimpleNamespace(shared_home=home, agent_name="codex"))
            with patch("app.api.routes.build_graph", return_value="<html></html>") as build:
                exact = client.get("/graph", params={"session_id": "abc", "agent": "pi"})
                cased = client.get("/graph", params={"agent": "PI"})
        self.assertEqual((exact.status_code, cased.status_code), (200, 200))
        self.assertEqual(build.call_args_list[0].kwargs, {"session_id": "abc", "agent": "pi"})
        self.assertEqual(build.call_args_list[1].kwargs, {"session_id": None, "agent": "pi"})

    def test_invalid_agent_is_a_client_error(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self._client(SimpleNamespace(shared_home=Path(temp), agent_name="codex"))
            with patch("app.api.routes.build_graph") as build:
                responses = [client.get("/graph", params={"agent": value}) for value in ("../../x", "team/a", "<script>")]
        for response in responses:
            self.assertEqual(response.status_code, 400)
            self.assertIn("Invalid agent name", response.text)
            self.assertNotIn("<script>", response.text)
        build.assert_not_called()

    def test_unknown_agent_is_not_reported_as_an_empty_store(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self._store(home, "codex", [{"id": "s:1:0", "text": "codex memory"}])
            (home / "agents" / "stale").mkdir()
            client = self._client(SimpleNamespace(shared_home=home, agent_name="codex"))
            response = client.get("/graph", params={"agent": "nobody"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("No Titan agent named nobody", response.text)
        self.assertIn('href="/graph?agent=codex"', response.text)
        self.assertNotIn("stale", response.text)

    def test_graph_route_reads_selected_namespace_from_isolated_stores(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            for agent in ("pi", "codex"):
                self._store(home, agent, [{"id": "same:1:0", "text": agent + " unique"}])
            client = self._client(SimpleNamespace(shared_home=home, agent_name="codex"))
            unscoped = client.get("/graph")
            scoped = client.get("/graph", params={"agent": "pi"})
        self.assertEqual(unscoped.status_code, 200)
        self.assertIn("codex unique", unscoped.text)
        self.assertIn("pi unique", unscoped.text)
        self.assertEqual(scoped.status_code, 200)
        self.assertIn("pi unique", scoped.text)
        self.assertNotIn("codex unique", scoped.text)

    def test_mixed_case_agent_keeps_correction_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            db = self._store(home, "codex", [
                {"id": "s:1:0", "text": "Release Friday"},
                {"id": "s:1:1", "text": "Release Tuesday", "ts": "2026-01-02T00:00:00+00:00"},
            ])
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS memory_supersessions "
                    "(source TEXT, old_id TEXT, new_id TEXT, evidence TEXT, created_at TEXT)"
                )
                conn.execute("INSERT INTO memory_supersessions VALUES ('codex', 's:1:0', 's:1:1', 'moved', '2026-01-02')")
            client = self._client(SimpleNamespace(shared_home=home, agent_name="codex"))
            page = client.get("/graph", params={"agent": "CODEX"})
        self.assertEqual(page.status_code, 200)
        self.assertIn('"kind": "supersedes"', page.text)
        self.assertIn('"source_agent": "codex"', page.text)
        self.assertNotIn('"source_agent": "CODEX"', page.text)

    def test_configured_memory_db_path_backs_graph_corrections_and_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            (home / "agents" / "codex").mkdir(parents=True)
            custom = self._store(home, "codex", [{"id": "custom:1:0", "text": "configured store"}],
                                 path=Path(temp) / "elsewhere" / "custom_store.db")
            context = SimpleNamespace(
                shared_home=home, agent_name="codex", memory_db_path=custom,
                environment={"TITAN_MEMORY_DB_PATH": str(custom)}, settings={},
            )
            client = self._client(context)
            page = client.get("/graph", params={"agent": "codex"})
            detail = client.get("/api/graph/memory", params={"source_agent": "codex", "memory_id": "custom:1:0"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("configured store", page.text)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["text"], "configured store")

    def test_json_backend_reads_json_store_not_a_stale_sqlite_file(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            base_dir = home / "agents" / "codex"
            stale_db = self._store(home, "codex", [{"id": "stale:1:0", "text": "stale sqlite row"}])
            memory_dir = stale_db.parent
            common = {"ts": "2026-01-02T00:00:00+00:00", "session_id": "s", "turn": 1}
            (memory_dir / "memories.json").write_text(json.dumps([
                {**common, "id": "old", "text": "json old row"},
                {**common, "id": "new", "text": "json new row"},
            ]))
            (memory_dir / "memories.json.relations.json").write_text(json.dumps([
                {"source": "codex", "old_id": "old", "new_id": "new", "evidence": "moved"},
            ]))
            context = SimpleNamespace(
                shared_home=home, agent_name="codex", base_dir=base_dir, memory_backend="json",
                memory_db_path=stale_db, environment={}, settings={},
            )
            client = self._client(context)
            page = client.get("/graph")
            detail = client.get("/api/graph/memory", params={"source_agent": "codex", "memory_id": "new"})
            stale = client.get("/api/graph/memory", params={"source_agent": "codex", "memory_id": "stale:1:0"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("json new row", page.text)
        self.assertNotIn("stale sqlite row", page.text)
        self.assertIn('"kind": "supersedes"', page.text)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["corrections"][0]["memoryId"], "old")
        self.assertEqual(stale.status_code, 404)

    def test_default_store_outside_agents_is_read_with_other_namespaces(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            # A single-home layout: the resolved default store is
            # <home>/out/memories/memory_store.db and settings name no path.
            default_db = self._store(home, "default", [{"id": "d:1:0", "text": "default home row"}],
                                     path=home / "out" / "memories" / "memory_store.db")
            self._store(home, "pi", [{"id": "p:1:0", "text": "pi namespace row"}])
            context = SimpleNamespace(
                shared_home=home, agent_name="default", base_dir=home, memory_backend="sqlite",
                memory_db_path=default_db, environment={}, settings={},
            )
            client = self._client(context)
            page = client.get("/graph")
            scoped = client.get("/graph", params={"agent": "default"})
            detail = client.get("/api/graph/memory", params={"source_agent": "default", "memory_id": "d:1:0"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("default home row", page.text)
        self.assertIn("pi namespace row", page.text)
        self.assertEqual(scoped.status_code, 200)
        self.assertIn("default home row", scoped.text)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["text"], "default home row")

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
