import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from entrypoints import mcp_server


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_memories_returns_scene_pointers_without_expanding_scenes(self):
        expected = {
            "count": 1,
            "memories": [
                {
                    "id": "m1",
                    "text": "Remember the bounded scene decision.",
                    "type": "decision",
                    "stream": "learnings",
                    "session_id": "s1",
                    "turn": 1,
                    "scene_id": "s1:scene:e-1",
                    "source_type": "user",
                    "source_reliability": 0.9,
                    "verification_status": "unverified",
                    "ts": "2026-07-12T00:00:00+00:00",
                    "source_event_ids": ["e-1"],
                    "provenance": {"user": "private source text"},
                    "raw_events": [{"event_id": "private"}],
                    "embedding": [0.1, 0.2],
                }
            ],
            "scenes": [],
            "scene_refs": [],
        }
        with patch.object(mcp_server, "retrieve_memory_brief", return_value=expected) as mock_retrieve:
            payload = await mcp_server.query_memories(query="bounded scene", limit=4)

        self.assertEqual(payload["scenes"], [])
        self.assertEqual(payload["memories"][0]["scene_id"], "s1:scene:e-1")
        self.assertNotIn("provenance", payload["memories"][0])
        self.assertNotIn("raw_events", payload["memories"][0])
        self.assertNotIn("embedding", payload["memories"][0])
        mock_retrieve.assert_called_once_with(
            query="bounded scene",
            session_id=None,
            mode=None,
            limit=4,
            include_scenes=False,
        )

    async def test_get_scene_context_delegates_to_core_helper(self):
        expected = {"scene": {"scene_id": "s1:scene:e-1"}}

        with patch.object(mcp_server, "build_scene_context", return_value=expected) as mock_build_scene_context:
            payload = await mcp_server.get_scene_context("s1:scene:e-1")

        self.assertEqual(payload, expected)
        mock_build_scene_context.assert_called_once_with("s1:scene:e-1")

    async def test_get_scene_context_returns_error_payloads_unchanged(self):
        expected = {"error": "scene not found", "scene_id": "missing-scene"}

        with patch.object(mcp_server, "build_scene_context", return_value=expected) as mock_build_scene_context:
            payload = await mcp_server.get_scene_context("missing-scene")

        self.assertEqual(payload, expected)
        mock_build_scene_context.assert_called_once_with("missing-scene")

    async def test_get_recent_memories_serializes_records(self):
        records = [
            {
                "id": "s1:1:0",
                "text": "remember this",
                "type": "decision",
                "stream": "rough",
                "session_id": "s1",
                "turn": 1,
                "scene_id": "scene-1",
                "source_type": "mixed",
                "source_reliability": 0.8,
                "verification_status": "unverified",
                "ts": "2026-06-11T00:00:00+00:00",
                "source_event_ids": ["e1"],
            }
        ]

        with patch.object(mcp_server, "load_recent_memories", return_value=records) as mock_recent:
            payload = await mcp_server.get_recent_memories(session_id="s1", limit=5)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["memories"][0]["id"], "s1:1:0")
        self.assertEqual(payload["memories"][0]["source_event_ids"], ["e1"])
        mock_recent.assert_called_once_with(limit=5, session_id="s1")

    async def test_inspect_clusters_delegates_to_cluster_helper(self):
        expected = {"clusters": []}

        with patch.object(mcp_server, "inspect_memory_clusters", return_value=expected) as mock_inspect:
            payload = await mcp_server.inspect_clusters(session_id="s1", limit=100, cluster_id=2, detail_limit=7)

        self.assertEqual(payload, expected)
        mock_inspect.assert_called_once_with(session_id="s1", limit=100, cluster_id=2, detail_limit=7)

    async def test_analyze_clusters_delegates_to_cortex_helper(self):
        expected = {"summary": "ok"}

        with patch.object(mcp_server, "analyze_memory_clusters", return_value=expected) as mock_analyze:
            payload = await mcp_server.analyze_clusters(cluster_ids="1,2", session_id="s1", limit=100, question="why", detail_limit=4)

        self.assertEqual(payload, expected)
        mock_analyze.assert_called_once_with(cluster_ids="1,2", session_id="s1", limit=100, question="why", detail_limit=4)

    async def test_server_exports_codex_parity_tools(self):
        tools = await mcp_server.server.list_tools()
        names = {tool.name for tool in tools}

        self.assertGreaterEqual(
            names,
            {
                "get_recent_memories",
                "doctor",
                "inspect_clusters",
                "analyze_clusters",
                "patterns_status",
                "patterns_list",
                "pattern_get",
                "pattern_create",
                "pattern_accept",
                "pattern_reject",
                "patterns_evidence_packet",
                "patterns_mark_processed",
                "patterns_export_bundle",
                "patterns_import_bundle",
            },
        )

        schemas = {tool.name: tool.inputSchema for tool in tools}
        self.assertEqual(schemas["analyze_clusters"]["properties"]["cluster_ids"]["type"], "string")
        self.assertEqual(schemas["pattern_create"]["properties"]["evidence_json"]["anyOf"][0]["type"], "string")
        self.assertNotIn("evidence", schemas["pattern_create"]["properties"])
        self.assertEqual(schemas["patterns_mark_processed"]["properties"]["memory_ids"]["type"], "string")
        self.assertEqual(schemas["patterns_export_bundle"]["properties"]["statuses"]["anyOf"][0]["type"], "string")
        self.assertEqual(schemas["patterns_import_bundle"]["properties"]["path"]["type"], "string")

    async def test_pattern_wrappers_delegate_to_api_functions(self):
        with patch.object(mcp_server.patterns_api, "get_pattern_status", return_value={"ok": True}) as mock_status, patch.object(
            mcp_server.patterns_api, "list_patterns", return_value={"count": 0, "patterns": []}
        ) as mock_list, patch.object(mcp_server.patterns_api, "get_pattern", return_value={"pattern": {"id": "p1"}}) as mock_get, patch.object(
            mcp_server.patterns_api, "accept_pattern", return_value={"pattern": {"status": "accepted"}}
        ) as mock_accept, patch.object(
            mcp_server.patterns_api, "reject_pattern", return_value={"pattern": {"status": "rejected"}}
        ) as mock_reject:
            self.assertEqual(await mcp_server.patterns_status(), {"ok": True})
            self.assertEqual(await mcp_server.patterns_list(status="candidate", scope="repo", limit=3), {"count": 0, "patterns": []})
            self.assertEqual(await mcp_server.pattern_get("p1"), {"pattern": {"id": "p1"}})
            self.assertEqual(await mcp_server.pattern_accept("p1"), {"pattern": {"status": "accepted"}})
            self.assertEqual(await mcp_server.pattern_reject("p1"), {"pattern": {"status": "rejected"}})

        mock_status.assert_called_once_with()
        mock_list.assert_called_once_with(status="candidate", scope="repo", limit=3)
        mock_get.assert_called_once_with("p1")
        mock_accept.assert_called_once_with("p1")
        mock_reject.assert_called_once_with("p1")

    async def test_pattern_create_builds_request(self):
        with patch.object(mcp_server.patterns_api, "create_pattern", return_value={"pattern": {"id": "p1"}}) as mock_create:
            payload = await mcp_server.pattern_create(
                title="Run focused tests",
                summary="A workflow pattern",
                recommended_behavior="Run pytest for changed modules.",
                trigger_terms="pytest",
                evidence_json=json.dumps([{"memory_id": "m1", "scene_id": "scene-1", "role": "support", "score": 0.9}]),
            )

        self.assertEqual(payload, {"pattern": {"id": "p1"}})
        req = mock_create.call_args.args[0]
        self.assertEqual(req.title, "Run focused tests")
        self.assertEqual(req.trigger_terms, ["pytest"])
        self.assertEqual(req.evidence[0].memory_id, "m1")

    async def test_pattern_evidence_and_mark_processed_build_requests(self):
        with patch.object(mcp_server.patterns_api, "get_evidence_packet", return_value={"memory_ids": ["m1"]}) as mock_packet, patch.object(
            mcp_server.patterns_api, "mark_processed", return_value={"marked_count": 1}
        ) as mock_mark:
            packet = await mcp_server.patterns_evidence_packet(batch_size=2, context_limit=10, session_id="s1", mode="adaptive", packet_type="contradiction")
            marked = await mcp_server.patterns_mark_processed(memory_ids="m1", pattern_ids="p1")

        self.assertEqual(packet, {"memory_ids": ["m1"]})
        self.assertEqual(marked, {"marked_count": 1})
        packet_req = mock_packet.call_args.args[0]
        mark_req = mock_mark.call_args.args[0]
        self.assertEqual(packet_req.batch_size, 2)
        self.assertEqual(packet_req.session_id, "s1")
        self.assertEqual(packet_req.mode, "adaptive")
        self.assertEqual(packet_req.packet_type, "contradiction")
        self.assertEqual(mark_req.memory_ids, ["m1"])
        self.assertEqual(mark_req.pattern_ids, ["p1"])

    async def test_patterns_export_bundle_delegates_to_bundle_api(self):
        expected = {"schema": "titan.pattern_bundle.v1", "patterns": [{"id": "p1"}], "evidence": [{"pattern_id": "p1"}]}

        with patch.object(mcp_server, "export_pattern_bundle", return_value=expected) as mock_export:
            payload = await mcp_server.patterns_export_bundle(include_candidates=True, statuses="accepted", scopes="repo", limit=25)

        self.assertEqual(payload, expected)
        mock_export.assert_called_once_with(
            statuses=["accepted", "candidate"],
            scopes=["repo"],
            include_memory_summaries=True,
            include_progress=True,
            limit=25,
        )

    async def test_patterns_export_bundle_writes_path_when_requested(self):
        bundle = {"schema": "titan.pattern_bundle.v1", "patterns": [{"id": "p1"}], "evidence": []}

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "patterns.json"
            with patch.object(mcp_server, "export_pattern_bundle", return_value=bundle):
                payload = await mcp_server.patterns_export_bundle(path=str(output_path))

            written = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(written, bundle)
        self.assertEqual(payload["patterns"], 1)
        self.assertEqual(payload["evidence"], 0)

    async def test_patterns_import_bundle_delegates_to_bundle_api(self):
        bundle = {"schema": "titan.pattern_bundle.v1", "patterns": [], "evidence": []}

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "patterns.json"
            input_path.write_text(json.dumps(bundle), encoding="utf-8")
            with patch.object(mcp_server, "import_pattern_bundle", return_value={"imported_patterns": 0}) as mock_import:
                payload = await mcp_server.patterns_import_bundle(str(input_path), overwrite=True, mark_progress=False)

        self.assertEqual(payload, {"imported_patterns": 0})
        mock_import.assert_called_once_with(bundle, overwrite=True, import_progress=False)

    async def test_doctor_returns_stable_local_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent_home = Path(tmp_dir) / "codex"
            trace_dir = agent_home / "traces"
            trace_dir.mkdir(parents=True)
            (trace_dir / "session.jsonl").write_text("{}\n", encoding="utf-8")

            with patch.object(mcp_server, "_current_titan_home", return_value=agent_home), patch.object(
                mcp_server, "get_memory_count", return_value=12
            ), patch.dict(mcp_server.os.environ, {"TITAN_AGENT_NAME": "codex", "TITAN_SPOOL_DIR": str(trace_dir)}, clear=False):
                payload = await mcp_server.doctor()

        self.assertEqual(payload["agent_name"], "codex")
        self.assertEqual(payload["memory_count"], 12)
        self.assertTrue(payload["recent_trace_files_exist"])
        self.assertTrue(payload["trace_dir_exists"])
        self.assertEqual(payload["trace_file_count"], 1)
        self.assertEqual(payload["agent_namespace"], str(agent_home))
        self.assertGreaterEqual(payload["mcp_tool_count"], 18)
        self.assertIn("patterns_export_bundle", payload["mcp_tools"])
        self.assertEqual(payload["auto_ingest"]["spool_dir"], str(trace_dir))
        self.assertTrue(payload["auto_ingest"]["starts_with_mcp_server"])
        self.assertTrue(payload["required_config_files"]["settings"])
        self.assertIn("provider_keys", payload)

    async def test_doctor_discovers_valid_agent_namespaces_and_reports_recall_sources(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            titan_root = Path(tmp_dir) / ".titan"
            agents_dir = titan_root / "agents"
            for agent in ("codex", "pi", "grok", "claude-code", "bad namespace", ".scratch"):
                (agents_dir / agent).mkdir(parents=True)
            agent_home = agents_dir / "codex"
            trace_dir = agent_home / "traces"
            trace_dir.mkdir(parents=True)

            counts = {"codex": 4, "pi": 8, "grok": 3, "claude-code": 0}
            with patch.object(mcp_server.Path, "home", return_value=Path(tmp_dir)), patch.object(
                mcp_server, "_current_titan_home", return_value=agent_home
            ), patch.object(
                mcp_server,
                "_count_agent_namespace_memories",
                side_effect=lambda agent, shared_home=None: counts.get(agent, 0),
            ), patch.object(
                mcp_server, "get_memory_count", return_value=4
            ), patch.object(mcp_server, "get_memory_repository"), patch.object(
                mcp_server, "get_lnn_state_repository", return_value=None
            ), patch.dict(
                mcp_server.os.environ,
                {
                    "TITAN_AGENT_NAME": "codex",
                    "TITAN_SPOOL_DIR": str(trace_dir),
                    "TITAN_SHARED_HOME": str(titan_root),
                },
                clear=False,
            ):
                payload = await mcp_server.doctor()

        self.assertEqual(payload["cross_agent_memory"]["discovered_agents"], ["claude-code", "codex", "grok", "pi"])
        self.assertEqual(
            payload["cross_agent_memory"]["other_agents_with_memories"],
            [{"agent": "grok", "memory_count": 3}, {"agent": "pi", "memory_count": 8}],
        )
        self.assertEqual(payload["active_write_workspace"]["agent"], "codex")
        self.assertEqual(payload["active_write_workspace"]["home"], str(agent_home))
        self.assertEqual(payload["default_recall"]["scope"], "federated")
        self.assertEqual(payload["default_recall"]["sources"], ["codex", "claude-code", "grok", "pi"])


if __name__ == "__main__":
    unittest.main()
