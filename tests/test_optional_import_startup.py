import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OptionalImportStartupTests(unittest.TestCase):
    def _run_isolated(self, source: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp_dir:
            titan_home = Path(tmp_dir) / "agents" / "startup-test"
            base_dir = titan_home / "runtime"
            env = {
                **{key: value for key, value in os.environ.items() if not key.startswith("TITAN_")},
                "PYTHONPATH": str(ROOT),
                "TITAN_AGENT_NAME": "startup-test",
                "TITAN_HOME": str(titan_home),
                "TITAN_BASE_DIR": str(base_dir),
                "TITAN_SHARED_HOME": str(Path(tmp_dir) / "agents"),
                "TITAN_SPOOL_DIR": str(base_dir / "traces"),
                "TITAN_MEMORY_DB_PATH": str(base_dir / "out" / "memories" / "memory_store.db"),
                "TITAN_AUTO_INGEST_ENABLED": "false",
            }
            completed = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(source)],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        return json.loads(completed.stdout)

    def test_http_startup_registers_routes_without_loading_optional_graph_stack(self):
        result = self._run_isolated(
            """
            import importlib.abc
            import json
            import sys

            class BlockNetworkx(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "networkx" or fullname.startswith("networkx."):
                        raise AssertionError(f"startup imported optional dependency: {fullname}")
                    return None

            sys.meta_path.insert(0, BlockNetworkx())
            from entrypoints.main import app
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                memories_response = client.get("/api/memories", params={"limit": 1})
                trace_response = client.post(
                    "/api/trace",
                    json={
                        "goal": "Verify startup isolation",
                        "outcome": "No optional feature requested",
                        "session_id": "lazy-import-smoke",
                        "event_id": "lazy-import-http-1",
                        "save_intent": False,
                    },
                )

            print(json.dumps({
                "paths": sorted(app.openapi()["paths"]),
                "memories_status": memories_response.status_code,
                "trace_status": trace_response.status_code,
                "heavy_modules": sorted(
                    name for name in sys.modules
                    if name in {
                        "app.graph.builder",
                        "app.graph.clusters",
                        "app.graph.cortex_analysis",
                        "app.patterns.api",
                        "app.patterns.bundle",
                        "app.patterns.graph",
                        "app.patterns.miner",
                        "networkx",
                    }
                ),
            }))
            """
        )

        self.assertGreaterEqual(
            set(result["paths"]),
            {
                "/graph",
                "/api/clusters",
                "/api/clusters/analyze",
                "/api/patterns",
                "/api/patterns/graph",
                "/api/patterns/bundle/export",
            },
        )
        self.assertEqual(result["memories_status"], 200)
        self.assertEqual(result["trace_status"], 200)
        self.assertEqual(result["heavy_modules"], [])

    def test_mcp_startup_registers_tools_without_loading_optional_graph_stack(self):
        result = self._run_isolated(
            """
            import asyncio
            import importlib.abc
            import json
            import sys

            class BlockNetworkx(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "networkx" or fullname.startswith("networkx."):
                        raise AssertionError(f"startup imported optional dependency: {fullname}")
                    return None

            sys.meta_path.insert(0, BlockNetworkx())
            from entrypoints import mcp_server

            tools = asyncio.run(mcp_server.server.list_tools())
            memories = asyncio.run(mcp_server.get_recent_memories(limit=1))
            trace = asyncio.run(mcp_server.store_trace_packet(
                goal="Verify startup isolation",
                outcome="No optional feature requested",
                session_id="lazy-import-smoke",
                event_id="lazy-import-mcp-1",
                save_intent=False,
            ))
            print(json.dumps({
                "tools": sorted(tool.name for tool in tools),
                "schemas": {tool.name: tool.inputSchema for tool in tools},
                "memories_count": memories["count"],
                "trace_session_id": trace["session_id"],
                "heavy_modules": sorted(
                    name for name in sys.modules
                    if name in {
                        "app.graph.builder",
                        "app.graph.clusters",
                        "app.graph.cortex_analysis",
                        "app.patterns.api",
                        "app.patterns.bundle",
                        "app.patterns.graph",
                        "app.patterns.miner",
                        "networkx",
                    }
                ),
            }))
            """
        )

        self.assertGreaterEqual(
            set(result["tools"]),
            {
                "query_memories",
                "store_trace_packet",
                "inspect_clusters",
                "analyze_clusters",
                "patterns_status",
                "pattern_create",
                "patterns_export_bundle",
            },
        )
        self.assertEqual(
            result["schemas"]["analyze_clusters"]["properties"]["cluster_ids"]["type"],
            "string",
        )
        self.assertGreaterEqual(result["memories_count"], 0)
        self.assertEqual(result["trace_session_id"], "lazy-import-smoke")
        self.assertEqual(result["heavy_modules"], [])

    def test_http_optional_handlers_load_their_implementations_on_first_call(self):
        result = self._run_isolated(
            """
            import json
            import sys

            from fastapi.testclient import TestClient
            from entrypoints.main import app

            with TestClient(app) as client:
                graph_response = client.get("/graph")
                pattern_response = client.get("/api/patterns/capabilities")

            print(json.dumps({
                "graph_status": graph_response.status_code,
                "graph_content_type": graph_response.headers["content-type"],
                "pattern_status": pattern_response.status_code,
                "capability_version": pattern_response.json()["capability_version"],
                "loaded": {
                    "graph": "app.graph.builder" in sys.modules,
                    "patterns": "app.patterns.api" in sys.modules,
                    "networkx": "networkx" in sys.modules,
                },
            }))
            """
        )

        self.assertEqual(result["graph_status"], 200)
        self.assertTrue(result["graph_content_type"].startswith("text/html"))
        self.assertEqual(result["pattern_status"], 200)
        self.assertEqual(result["capability_version"], "zenkai-v1")
        self.assertEqual(result["loaded"], {"graph": True, "patterns": True, "networkx": True})

    def test_mcp_optional_tools_load_their_implementations_on_first_call(self):
        result = self._run_isolated(
            """
            import asyncio
            import json
            import sys

            from entrypoints import mcp_server

            clusters = asyncio.run(mcp_server.inspect_clusters(limit=1))
            patterns = asyncio.run(mcp_server.patterns_status())
            print(json.dumps({
                "cluster_count": clusters["cluster_count"],
                "unprocessed_patterns": patterns["unprocessed"],
                "loaded": {
                    "clusters": "app.graph.clusters" in sys.modules,
                    "patterns": "app.patterns.api" in sys.modules,
                    "networkx": "networkx" in sys.modules,
                },
            }))
            """
        )

        self.assertGreaterEqual(result["cluster_count"], 0)
        self.assertGreaterEqual(result["unprocessed_patterns"], 0)
        self.assertEqual(result["loaded"], {"clusters": True, "patterns": True, "networkx": True})


if __name__ == "__main__":
    unittest.main()
