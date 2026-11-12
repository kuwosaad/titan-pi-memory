import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from entrypoints.main import app


class RetrieveDetailRouteTests(unittest.TestCase):
    def test_compact_is_opt_in_and_passed_to_pipeline(self):
        with patch("app.api.routes.retrieve_memory_brief", return_value={"brief": "useful"}) as recall:
            response = TestClient(app).get("/api/retrieve", params={"query": "test", "detail": "compact", "max_chars": 800})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recall.call_args.kwargs["detail"], "compact")
        self.assertEqual(recall.call_args.kwargs["max_chars"], 800)

    def test_invalid_detail_and_bounds_are_rejected_before_retrieval(self):
        with patch("app.api.routes.retrieve_memory_brief") as recall:
            client = TestClient(app)
            for params in ({"detail": "verbose"}, {"limit": -1}, {"max_items": 101}, {"max_chars": -1}):
                with self.subTest(params=params):
                    self.assertEqual(client.get("/api/retrieve", params=params).status_code, 400)
            recall.assert_not_called()

    def test_zero_brief_limits_are_passed_through(self):
        with patch("app.api.routes.retrieve_memory_brief", return_value={}) as recall:
            response = TestClient(app).get("/api/retrieve", params={"max_items": 0, "max_chars": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recall.call_args.kwargs["max_items"], 0)
        self.assertEqual(recall.call_args.kwargs["max_chars"], 0)


class MCPToolProfileTests(unittest.TestCase):
    def run_profile(self, profile=None, *, inside_loop=False):
        with tempfile.TemporaryDirectory() as home:
            env = {key: value for key, value in os.environ.items() if not key.startswith("TITAN_")}
            env.update({"HOME": home, "TITAN_HOME": home, "TITAN_SHARED_HOME": home,
                        "TITAN_BASE_DIR": home})
            if profile is not None:
                env["TITAN_MCP_TOOL_PROFILE"] = profile
            script = (
                "import asyncio,json; from entrypoints.mcp_server import server; "
                "tools=asyncio.run(server.list_tools()); "
                "print(json.dumps({t.name:t.inputSchema for t in tools}, sort_keys=True))"
            )
            if inside_loop:
                script = ("import asyncio, json\nasync def main():\n"
                          " from entrypoints.mcp_server import server\n"
                          " tools = await server.list_tools()\n"
                          " print(json.dumps({t.name: t.inputSchema for t in tools}, sort_keys=True))\n"
                          "asyncio.run(main())")
            return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)

    def test_core_profile_limits_tool_inventory_and_schema_size(self):
        full_process = self.run_profile()
        core_process = self.run_profile("core")
        self.assertEqual(full_process.returncode, 0, full_process.stderr)
        self.assertEqual(core_process.returncode, 0, core_process.stderr)
        full, core = json.loads(full_process.stdout), json.loads(core_process.stdout)
        self.assertEqual(set(core), {"query_memories", "store_trace_packet", "get_scene_context", "doctor"})
        self.assertGreater(len(full), len(core))
        self.assertTrue(set(core) <= set(full))
        self.assertEqual(core, {name: full[name] for name in core})
        self.assertLess(len(core_process.stdout), len(full_process.stdout))
        explicit = self.run_profile("full")
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(json.loads(explicit.stdout), full)

    def test_core_import_works_inside_running_event_loop(self):
        result = self.run_profile("core", inside_loop=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 4)

    def test_invalid_profile_fails_clearly(self):
        result = self.run_profile("cor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TITAN_MCP_TOOL_PROFILE must be 'core' or 'full'", result.stderr)
