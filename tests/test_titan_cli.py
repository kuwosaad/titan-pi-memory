import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import yaml
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.patterns.models import Pattern
from app.patterns.store import PatternStore
from tools.cli.titan import (
    CODEX_REQUIRED_MCP_TOOLS,
    codex_mcp_stdio_handshake,
    load_codex_effective_mcp_transport,
    load_codex_mcp_contract,
    _select_graph_port,
    _setup_codex_model_config,
    bootstrap_agent_home,
    generate_codex_mcp_enable_block,
    generate_agent_connection_guide,
    generate_graph_url,
    generate_pattern_graph_url,
    generate_mcp_block,
    main,
    patch_codex_config,
    patch_opencode_config,
    ensure_codex_marketplace_snapshot,
    resolve_codex_marketplace_plugin_source,
    resolve_agent_trace_dir,
    run_codex_reinstall_plugin,
    run_codex_verify,
    run_doctor,
    run_connected_loop_test,
    run_graph,
    run_init,
    run_pattern_graph,
    run_onboarding_smoke_test,
    run_setup_codex,
    run_setup,
    run_set_key,
    run_share,
    run_import_bundle,
    upsert_env_keys,
    validate_codex_marketplace,
)


class TitanCliTests(unittest.TestCase):
    def test_codex_mcp_handshake_falls_back_when_selector_fails_after_registration(self):
        class ExplodingSelector:
            def register(self, _stream, _events):
                return None

            def select(self, _timeout):
                raise OSError("anonymous pipes are not selectable")

            def close(self):
                return None

        class FakeProcess:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(
                    '{"jsonrpc":"2.0","id":1,"result":{}}\n'
                    '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"doctor"}]}}\n'
                )
                self.stderr = io.StringIO()

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_root = Path(tmp_dir) / "plugin"
            plugin_root.mkdir()
            (plugin_root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"titan-memory": {
                    "command": "python3",
                    "args": ["./scripts/titan_mcp_launcher.py"],
                    "cwd": ".",
                }}}),
                encoding="utf-8",
            )
            with patch("tools.cli.titan.selectors.DefaultSelector", ExplodingSelector):
                ok, tools, detail = codex_mcp_stdio_handshake(
                    plugin_root=plugin_root,
                    timeout_sec=1,
                    popen_fn=lambda *_args, **_kwargs: FakeProcess(),
                )

        self.assertTrue(ok, detail)
        self.assertEqual(tools, ["doctor"])

    def test_codex_mcp_handshake_times_out_with_real_silent_child(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_root = Path(tmp_dir) / "plugin"
            plugin_root.mkdir()
            (plugin_root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"titan-memory": {
                    "command": sys.executable,
                    "args": ["-c", "import time; time.sleep(30)"],
                    "cwd": ".",
                }}}),
                encoding="utf-8",
            )
            started = time.monotonic()
            ok, tools, detail = codex_mcp_stdio_handshake(
                plugin_root=plugin_root,
                timeout_sec=0.1,
            )
            elapsed = time.monotonic() - started

        self.assertFalse(ok)
        self.assertEqual(tools, [])
        self.assertIn("timed out", detail)
        self.assertLess(elapsed, 5.0)

    def test_codex_mcp_handshake_timeout_survives_unselectable_stdout(self):
        class BlockingStdout:
            def __init__(self):
                self.release = threading.Event()

            def readline(self):
                self.release.wait()
                return ""

        class FakeProcess:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = BlockingStdout()
                self.stderr = io.StringIO()

            def terminate(self):
                self.stdout.release.set()

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_root = Path(tmp_dir) / "plugin"
            plugin_root.mkdir()
            (plugin_root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"titan-memory": {
                    "command": "python3",
                    "args": ["./scripts/titan_mcp_launcher.py"],
                    "cwd": ".",
                }}}),
                encoding="utf-8",
            )
            started = time.monotonic()
            ok, tools, detail = codex_mcp_stdio_handshake(
                plugin_root=plugin_root,
                timeout_sec=0.05,
                popen_fn=lambda *_args, **_kwargs: FakeProcess(),
            )
            elapsed = time.monotonic() - started

        self.assertFalse(ok)
        self.assertEqual(tools, [])
        self.assertIn("timed out", detail)
        self.assertLess(elapsed, 1.0)

    def test_codex_effective_mcp_transport_parses_codex_json_contract(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"name": "titan-memory", "transport": {
                "type": "stdio",
                "command": "python3",
                "args": ["./scripts/titan_mcp_launcher.py", "--agent", "codex"],
                "cwd": "/tmp/titan-cache/.",
                "env": {"TITAN_AGENT_NAME": "codex", "TITAN_CONTRACT_TEST": "yes"},
            }}),
            stderr="",
        )
        calls = []

        def run_fn(command, **kwargs):
            calls.append(command)
            return completed

        transport = load_codex_effective_mcp_transport(run_fn=run_fn)

        self.assertEqual(transport["command"], "python3")
        self.assertEqual(transport["args"], ["./scripts/titan_mcp_launcher.py", "--agent", "codex"])
        self.assertEqual(transport["cwd"], "/tmp/titan-cache/.")
        self.assertEqual(transport["env"]["TITAN_CONTRACT_TEST"], "yes")
        # The injected runner receives Codex's exact MCP lookup command.
        self.assertEqual(calls[0], ["codex", "mcp", "get", "titan-memory", "--json"])

    def test_codex_effective_mcp_transport_rejects_legacy_placeholder(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"transport": {
                "type": "stdio",
                "command": "python3",
                "args": ["${PLUGIN_ROOT}/scripts/titan_mcp_launcher.py"],
                "cwd": "/tmp/titan-cache/.",
                "env": {},
            }}),
            stderr="",
        )
        with patch("tools.cli.titan.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(ValueError, "unsupported.*PLUGIN_ROOT"):
                load_codex_effective_mcp_transport(run_fn=lambda *args, **kwargs: completed)

    def test_codex_mcp_contract_rejects_legacy_plugin_root_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_root = Path(tmp_dir)
            (plugin_root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"titan-memory": {
                    "command": "python3",
                    "args": ["${PLUGIN_ROOT}/scripts/titan_mcp_launcher.py"],
                }}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsupported.*PLUGIN_ROOT"):
                load_codex_mcp_contract(plugin_root=plugin_root)

    def test_codex_mcp_handshake_executes_materialized_contract_from_plugin_root(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(
                    '{"jsonrpc":"2.0","id":1,"result":{}}\n'
                    '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"doctor"}]}}\n'
                )
                self.stderr = io.StringIO()

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_root = Path(tmp_dir) / "plugin"
            plugin_root.mkdir()
            (plugin_root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"titan-memory": {
                    "command": "python3",
                    "args": ["./scripts/titan_mcp_launcher.py", "--agent", "codex"],
                    "cwd": ".",
                    "env": {"TITAN_CONTRACT_TEST": "yes"},
                }}}),
                encoding="utf-8",
            )
            calls = []
            process = FakeProcess()

            def popen(command, **kwargs):
                calls.append((command, kwargs))
                return process

            ok, tools, detail = codex_mcp_stdio_handshake(
                plugin_root=plugin_root,
                timeout_sec=1,
                popen_fn=popen,
            )

        self.assertTrue(ok, detail)
        self.assertEqual(tools, ["doctor"])
        self.assertEqual(calls[0][0], ["python3", "./scripts/titan_mcp_launcher.py", "--agent", "codex"])
        self.assertEqual(calls[0][1]["cwd"], str(plugin_root.resolve()))
        self.assertEqual(calls[0][1]["env"]["TITAN_CONTRACT_TEST"], "yes")

    def test_upsert_env_keys_preserves_existing_order_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "# first comment\n"
                "OPENAI_API_KEY=old-openai\n"
                "GEMINI_API_KEY=old-gemini\n",
                encoding="utf-8",
            )

            upsert_env_keys(
                env_path,
                {
                    "OPENAI_API_KEY": "new-openai",
                    "OPENROUTER_API_KEY": "new-openrouter",
                },
            )

            lines = env_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "# first comment")
            self.assertEqual(lines[1], "OPENAI_API_KEY=new-openai")
            self.assertEqual(lines[2], "GEMINI_API_KEY=old-gemini")
            self.assertEqual(lines[3], "OPENROUTER_API_KEY=new-openrouter")

    def test_generate_mcp_block_has_stdio_command_and_path(self):
        with patch("tools.cli.titan.shutil.which", return_value=None):
            block = generate_mcp_block(agent="opencode", mode="stdio")
        data = json.loads(block)
        self.assertEqual(data["$schema"], "https://opencode.ai/config.json")
        self.assertIn("mcp", data)
        self.assertIn("titan-memory", data["mcp"])
        titan = data["mcp"]["titan-memory"]
        self.assertEqual(titan["type"], "local")
        self.assertEqual(titan["enabled"], True)
        self.assertEqual(titan["command"][0], "python3")
        self.assertIn("tools/cli/titan.py", titan["command"][1])
        self.assertEqual(titan["command"][2:], ["mcp", "--agent", "opencode"])

    def test_generate_mcp_block_uses_wrapper_command_when_available(self):
        with patch.dict(os.environ, {"TITAN_CLI_WRAPPER_COMMAND": "titan"}, clear=False):
            block = generate_mcp_block(agent="opencode", mode="stdio")
        data = json.loads(block)
        titan = data["mcp"]["titan-memory"]
        self.assertEqual(titan["command"], ["titan", "mcp", "--agent", "opencode"])

    def test_bootstrap_agent_home_copies_shared_env_once(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            shared_home = Path(tmp_dir)
            (shared_home / ".env").write_text("GEMINI_API_KEY=shared-key\n", encoding="utf-8")
            with patch("tools.cli.titan.TITAN_HOME", shared_home):
                agent_home = bootstrap_agent_home("OpenCode")
            self.assertEqual((agent_home / ".env").read_text(encoding="utf-8"), "GEMINI_API_KEY=shared-key\n")

    def test_bootstrap_agent_home_merges_new_shared_keys_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            shared_home = Path(tmp_dir)
            agent_home = shared_home / "agents" / "opencode"
            agent_home.mkdir(parents=True)
            (shared_home / ".env").write_text("GEMINI_API_KEY=shared-key\nOPENAI_API_KEY=shared-openai\n", encoding="utf-8")
            (agent_home / ".env").write_text("GEMINI_API_KEY=agent-key\n", encoding="utf-8")
            with patch("tools.cli.titan.TITAN_HOME", shared_home):
                bootstrap_agent_home("OpenCode")
            env_text = (agent_home / ".env").read_text(encoding="utf-8")
            self.assertIn("GEMINI_API_KEY=agent-key", env_text)
            self.assertIn("OPENAI_API_KEY=shared-openai", env_text)

    def test_generate_agent_connection_guide_includes_generic_steps_and_example(self):
        with patch("tools.cli.titan.shutil.which", return_value="/usr/local/bin/titan"):
            guide = generate_agent_connection_guide("OpenCode")
        self.assertIn("Paste this into OpenCode config", guide)
        self.assertIn('"command": [', guide)
        self.assertIn("Run `titan doctor`", guide)

    def test_resolve_agent_trace_dir_uses_agent_home(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)):
                trace_dir = resolve_agent_trace_dir("OpenCode")
        self.assertEqual(trace_dir, Path(tmp_dir) / "agents" / "opencode" / "traces")

    def test_generate_graph_url_supports_global_and_session_views(self):
        self.assertEqual(generate_graph_url(), "http://127.0.0.1:8010/graph")
        self.assertEqual(
            generate_graph_url(session_id="ses_123"),
            "http://127.0.0.1:8010/graph?session_id=ses_123",
        )

    def test_generate_pattern_graph_url_supports_limit(self):
        self.assertEqual(generate_pattern_graph_url(), "http://127.0.0.1:8010/pattern-graph")
        self.assertEqual(
            generate_pattern_graph_url(limit=25),
            "http://127.0.0.1:8010/pattern-graph?limit=25",
        )

    def test_select_graph_port_uses_requested_port_when_free(self):
        with patch("tools.cli.titan._is_tcp_port_available", return_value=True):
            port, used_fallback = _select_graph_port("127.0.0.1", 8000)
        self.assertEqual(port, 8000)
        self.assertFalse(used_fallback)

    def test_select_graph_port_falls_forward_when_busy(self):
        with patch("tools.cli.titan._is_tcp_port_available", side_effect=[False, False, True]):
            port, used_fallback = _select_graph_port("127.0.0.1", 8000)
        self.assertEqual(port, 8002)
        self.assertTrue(used_fallback)

    def test_run_graph_uses_agent_home_and_session_url(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch("tools.cli.titan._select_graph_port", return_value=(8010, False)), patch(
            "tools.cli.titan.webbrowser.open"
        ) as open_mock, patch("uvicorn.run") as uvicorn_mock, patch(
            "app.storage.memories.get_memory_count",
            return_value=1,
        ):
            exit_code = run_graph(agent="opencode", session_id="ses_123", open_browser=True, port=8010)
        self.assertEqual(exit_code, 0)
        open_mock.assert_called_once_with("http://127.0.0.1:8010/graph?session_id=ses_123")
        uvicorn_mock.assert_called_once_with("entrypoints.main:app", host="127.0.0.1", port=8010, reload=False)
        output = stdout.getvalue()
        self.assertIn("Graph URL: http://127.0.0.1:8010/graph?session_id=ses_123", output)
        self.assertIn("Memory count: 1", output)

    def test_run_graph_reports_port_fallback(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch("tools.cli.titan._select_graph_port", return_value=(8010, True)), patch(
            "uvicorn.run"
        ) as uvicorn_mock, patch(
            "app.storage.memories.get_memory_count",
            return_value=0,
        ):
            exit_code = run_graph(agent="opencode", port=8000)
        self.assertEqual(exit_code, 0)
        uvicorn_mock.assert_called_once_with("entrypoints.main:app", host="127.0.0.1", port=8010, reload=False)
        self.assertIn("Port 8000 is busy. Using 8010 instead.", stdout.getvalue())

    def test_run_pattern_graph_uses_agent_home_and_pattern_graph_url(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "memory_store.db"
            store = PatternStore(db_path)
            for idx, status in enumerate(["accepted", "candidate"], start=1):
                store.create_pattern(
                    Pattern(
                        title=f"Pattern {idx}",
                        kind="workflow",
                        scope="repo",
                        status=status,  # type: ignore[arg-type]
                        summary="summary",
                        recommended_behavior="do the thing",
                        trigger_terms=["thing"],
                        confidence=0.8,
                    ),
                    [],
                    validate_memory_ids=False,
                    min_support_evidence=0,
                )
            with patch("sys.stdout", stdout), patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)), patch(
                "app.storage.memories._resolve_sqlite_path",
                return_value=db_path,
            ), patch("tools.cli.titan._select_graph_port", return_value=(8010, False)), patch(
                "tools.cli.titan.webbrowser.open"
            ) as open_mock, patch("uvicorn.run") as uvicorn_mock:
                exit_code = run_pattern_graph(agent="opencode", open_browser=True, port=8010, limit=25)
        self.assertEqual(exit_code, 0)
        open_mock.assert_called_once_with("http://127.0.0.1:8010/pattern-graph?limit=25")
        uvicorn_mock.assert_called_once_with("entrypoints.main:app", host="127.0.0.1", port=8010, reload=False)
        output = stdout.getvalue()
        self.assertIn("Pattern graph URL: http://127.0.0.1:8010/pattern-graph?limit=25", output)
        self.assertIn("Patterns: 1 accepted, 1 candidate", output)

    def test_run_share_writes_pattern_bundle(self):
        stdout = io.StringIO()
        bundle = {"schema": "titan.pattern_bundle.v1", "patterns": [{"id": "p1"}], "evidence": [{"memory_id": "m1"}]}
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch("app.patterns.bundle.export_pattern_bundle", return_value=bundle) as mock_export:
            output_path = Path(tmp_dir) / "patterns.json"
            args = SimpleNamespace(
                patterns=True,
                agent="opencode",
                status=None,
                scope=None,
                include_candidates=True,
                no_memory_summaries=False,
                no_progress=False,
                limit=25,
                output=str(output_path),
            )
            exit_code = run_share(args)
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), bundle)
            self.assertEqual(mock_export.call_args.kwargs["statuses"], ["accepted", "candidate"])
            self.assertEqual(mock_export.call_args.kwargs["limit"], 25)
            self.assertIn("Pattern bundle written", stdout.getvalue())

    def test_run_import_bundle_reads_pattern_bundle(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)), patch(
            "app.patterns.bundle.import_pattern_bundle",
            return_value={"imported_patterns": 1},
        ) as mock_import, patch("sys.stdout", io.StringIO()):
            input_path = Path(tmp_dir) / "patterns.json"
            input_path.write_text(json.dumps({"schema": "titan.pattern_bundle.v1"}), encoding="utf-8")
            args = SimpleNamespace(patterns=str(input_path), agent="opencode", overwrite=True, no_progress=False)
            exit_code = run_import_bundle(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_import.call_args.args[0]["schema"], "titan.pattern_bundle.v1")
        self.assertTrue(mock_import.call_args.kwargs["overwrite"])

    def test_smoke_test_reports_retrieval_empty_issue(self):
        with patch("app.save_pipeline.pipeline.handle_trace_packet", return_value={"memory_status": "stored"}), patch(
            "app.save_pipeline.pipeline.retrieve_memory_brief",
            return_value={"count": 0, "brief": ""},
        ):
            result = run_onboarding_smoke_test(plugin_path=Path(__file__))
        self.assertFalse(result["ok"])
        issues_text = " ".join(result["issues"])
        self.assertIn("retrieval returned empty", issues_text)

    def test_smoke_test_reports_env_key_issue(self):
        with patch(
            "app.save_pipeline.pipeline.handle_trace_packet",
            side_effect=ValueError("Missing required env var GEMINI_API_KEY for extraction backend 'gemini'"),
        ):
            result = run_onboarding_smoke_test(plugin_path=Path(__file__))
        self.assertFalse(result["ok"])
        self.assertIn("env key issue", result["issues"])

    def test_connected_loop_reports_missing_plugin_events(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_connected_loop_test(spool_dir=Path(tmp_dir), plugin_path=Path(__file__))
        self.assertFalse(result["ok"])
        self.assertIn("no plugin events found", result["issues"])

    def test_connected_loop_defaults_spool_dir_to_titan_home_when_env_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            expected_spool_dir = Path(tmp_dir) / "agents" / "opencode" / "traces"
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TITAN_SPOOL_DIR", None)
                with patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)), patch(
                    "app.save_pipeline.auto_ingest.discover_spool_sessions",
                    return_value=[],
                ) as discover_mock:
                    result = run_connected_loop_test(plugin_path=Path(__file__), agent="opencode")
        self.assertFalse(result["ok"])
        self.assertIn("no plugin events found", result["issues"])
        discover_mock.assert_called_once_with(expected_spool_dir)

    def test_run_init_completes_without_smoke_test(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ):
            exit_code = run_init(agent="opencode", non_interactive=True)
        self.assertEqual(exit_code, 1)
        output = stdout.getvalue()
        self.assertIn("titan setup", output)

    def test_patch_opencode_config_creates_mcp_bridge(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "opencode.json"
            result = patch_opencode_config(agent="opencode", config_path=config_path)
            self.assertTrue(result["ok"])
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["mcp"]["titan-memory"]["command"][-2:], ["--agent", "opencode"])

    def test_patch_opencode_config_backs_up_and_preserves_existing_mcp(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "opencode.json"
            config_path.write_text(
                json.dumps({"mcp": {"other": {"type": "local", "command": ["x"]}}}),
                encoding="utf-8",
            )
            result = patch_opencode_config(agent="opencode", config_path=config_path)
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertIn("other", data["mcp"])
            self.assertIn("titan-memory", data["mcp"])

    def test_patch_codex_config_adds_enable_block_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text("model = \"gpt-5.5\"\n", encoding="utf-8")

            result = patch_codex_config(config_path=config_path)
            self.assertTrue(result["ok"])
            text = config_path.read_text(encoding="utf-8")
            self.assertIn("model = \"gpt-5.5\"", text)
            self.assertIn(generate_codex_mcp_enable_block().strip(), text)
            self.assertTrue(Path(result["backup_path"]).exists())

            second = patch_codex_config(config_path=config_path)
            self.assertEqual(second["status"], "already_configured")

    def test_patch_codex_config_replaces_existing_titan_block_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text(
                "[plugins.\"other\"]\n"
                "enabled = true\n\n"
                "[plugins.\"titan-memory@titan-local\".mcp_servers.\"titan-memory\"]\n"
                "enabled = false\n\n"
                "[profiles.default]\n"
                "approval_policy = \"on-request\"\n",
                encoding="utf-8",
            )

            patch_codex_config(config_path=config_path)
            text = config_path.read_text(encoding="utf-8")
            self.assertIn("[plugins.\"other\"]", text)
            self.assertIn("[profiles.default]", text)
            self.assertIn("enabled = true", text)
            self.assertIn('default_tools_approval_mode = "prompt"', text)

    def test_patch_codex_config_dedupes_all_legacy_and_canonical_blocks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text(
                '[plugins."titan-memory@titan-local".mcp_servers."titan-memory"]\n'
                "enabled = false\n\n"
                '[plugins."titan-memory@titan-pi-memory".mcp_servers."titan-memory"]\n'
                "enabled = true\n\n"
                '[plugins."other"]\nvalue = 1\n',
                encoding="utf-8",
            )
            result = patch_codex_config(config_path=config_path)
            text = config_path.read_text(encoding="utf-8")
            self.assertTrue(result["ok"])
            self.assertEqual(text.count('mcp_servers."titan-memory"]'), 1)
            self.assertIn('[plugins."other"]', text)

    def test_codex_verify_rejects_orphan_legacy_mcp_block(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            marketplace_root = tmp_path / "codex-marketplace"
            ok, detail = ensure_codex_marketplace_snapshot(target=marketplace_root)
            self.assertTrue(ok, detail)
            config_path = tmp_path / "config.toml"
            config_path.write_text(
                generate_codex_mcp_enable_block()
                + "\n"
                + '[plugins."titan-memory@titan-local".mcp_servers."titan-memory"]\n'
                + "enabled = true\n",
                encoding="utf-8",
            )
            trace_dir = tmp_path / "traces"
            trace_dir.mkdir()
            with patch("sys.stdout", stdout), patch("tools.cli.titan.CODEX_MARKETPLACE_DIR", marketplace_root), patch(
                "tools.cli.titan.shutil.which", return_value="/usr/local/bin/codex"
            ), patch("tools.cli.titan._codex_plugin_files_ok", return_value=(True, [])), patch(
                "tools.cli.titan.resolve_effective_spool_dir", return_value=trace_dir
            ), patch(
                "tools.cli.titan.codex_mcp_stdio_handshake",
                return_value=(True, CODEX_REQUIRED_MCP_TOOLS, "ok"),
            ) as handshake_mock:
                result = run_codex_verify(config_path=config_path)

        self.assertEqual(result, 1)
        self.assertIn("legacy Titan MCP blocks", stdout.getvalue())
        handshake_mock.assert_called_once_with(agent="codex")

    def test_codex_marketplace_snapshot_is_a_resolvable_official_tree(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            marketplace_root = Path(tmp_dir) / "codex-marketplace"
            ok, detail = ensure_codex_marketplace_snapshot(target=marketplace_root)
            self.assertTrue(ok, detail)
            self.assertEqual(resolve_codex_marketplace_plugin_source(marketplace_root=marketplace_root), marketplace_root.resolve())
            self.assertTrue((marketplace_root / ".agents/plugins/marketplace.json").exists())
            self.assertTrue((marketplace_root / ".codex-plugin/plugin.json").exists())

    def test_codex_marketplace_snapshot_refreshes_when_script_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            marketplace_root = Path(tmp_dir) / "codex-marketplace"
            ok, detail = ensure_codex_marketplace_snapshot(target=marketplace_root)
            self.assertTrue(ok, detail)
            script = marketplace_root / "scripts" / "titan_mcp_launcher.py"
            original = script.read_text(encoding="utf-8")
            script.write_text("stale launcher\n", encoding="utf-8")

            ok, detail = ensure_codex_marketplace_snapshot(target=marketplace_root)

            self.assertTrue(ok, detail)
            self.assertEqual(script.read_text(encoding="utf-8"), original)

    def test_codex_marketplace_rejects_source_escape(self):
        payload = {
            "name": "titan-pi-memory",
            "interface": {"displayName": "Titan"},
            "plugins": [{
                "name": "titan-memory",
                "source": {"source": "local", "path": "../outside"},
                "policy": {"installation": "AVAILABLE"},
            }],
        }
        valid, reason = validate_codex_marketplace(payload)
        self.assertFalse(valid)
        self.assertIn("inside the marketplace root", reason)

    def test_main_codex_commands_route_to_helpers(self):
        with patch("tools.cli.titan.run_codex_doctor", return_value=0) as doctor_mock:
            self.assertEqual(main(["codex", "doctor"]), 0)
        doctor_mock.assert_called_once_with(config_path=None)

        with patch("tools.cli.titan.run_codex_list_tools", return_value=0) as list_mock:
            self.assertEqual(main(["codex", "list-tools", "--json"]), 0)
        list_mock.assert_called_once_with(json_output=True)

    def test_main_setup_codex_routes_to_codex_setup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            with patch("tools.cli.titan.run_setup_codex", return_value=0) as setup_mock:
                exit_code = main(["setup", "codex", "--dry-run", "--codex-config", str(config_path), "--skip-plugin-install"])
            self.assertEqual(exit_code, 0)
            setup_mock.assert_called_once_with(
                dry_run=True,
                verify=False,
                config_path=config_path,
                skip_plugin_install=True,
            )

    def test_run_setup_codex_dry_run_does_not_write_config(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir) / "titan-home"
        ):
            config_path = Path(tmp_dir) / "config.toml"
            exit_code = run_setup_codex(dry_run=True, config_path=config_path)
        self.assertEqual(exit_code, 0)
        self.assertFalse(config_path.exists())
        self.assertIn("Codex setup dry run", stdout.getvalue())

    def test_setup_codex_model_config_writes_extraction_and_embedding_configs(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir) / "titan-home"
        ), patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-openrouter-key"}), patch(
            "tools.cli.titan_voice.prompt_choice",
            side_effect=["deepseek", "deepseek/deepseek-chat"],
        ), patch(
            "tools.cli.titan_voice.confirm",
            return_value=False,
        ):
            agent_home = Path(tmp_dir) / "titan-home" / "agents" / "codex"
            agent_home.mkdir(parents=True)
            env_updates = _setup_codex_model_config(agent_home)
            extraction_path = agent_home / "config" / "extraction_models.yaml"
            embedding_path = agent_home / "config" / "embedding_models.yaml"
            extraction_cfg = yaml.safe_load(extraction_path.read_text(encoding="utf-8"))
            embedding_cfg = yaml.safe_load(embedding_path.read_text(encoding="utf-8"))
            agent_env = (agent_home / ".env").read_text(encoding="utf-8")

        self.assertEqual(extraction_cfg["current"], "openrouter")
        self.assertEqual(extraction_cfg["openrouter"]["model"], "deepseek/deepseek-chat")
        self.assertEqual(embedding_cfg["current"], "ollama")
        self.assertEqual(embedding_cfg["ollama"]["model"], "nomic-embed-text:v1.5")
        self.assertEqual(env_updates["TITAN_EXTRACTION_CONFIG_PATH"], str(extraction_path))
        self.assertEqual(env_updates["TITAN_EMBEDDING_CONFIG_PATH"], str(embedding_path))
        self.assertIn("TITAN_EXTRACTION_CONFIG_PATH=", agent_env)
        self.assertIn("TITAN_EMBEDDING_CONFIG_PATH=", agent_env)

    def test_run_setup_codex_patches_config_without_mcp_introspection(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir) / "titan-home"
        ), patch(
            "tools.cli.titan.CODEX_MARKETPLACE_DIR", Path(tmp_dir) / "codex-marketplace"
        ), patch(
            "tools.cli.titan._setup_codex_model_config",
            return_value={},
        ) as model_setup_mock:
            config_path = Path(tmp_dir) / "config.toml"
            exit_code = run_setup_codex(config_path=config_path, skip_plugin_install=True)
            config_text = config_path.read_text(encoding="utf-8")
            trace_dir_exists = (Path(tmp_dir) / "titan-home" / "agents" / "codex" / "traces").exists()
        self.assertEqual(exit_code, 0)
        model_setup_mock.assert_called_once()
        self.assertIn(generate_codex_mcp_enable_block().strip(), config_text)
        self.assertTrue(trace_dir_exists)
        self.assertIn("exact MCP launcher", stdout.getvalue())

    def test_run_setup_codex_passes_custom_config_to_post_install_repair(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / "custom-config.toml"
            with patch("tools.cli.titan.TITAN_HOME", tmp_path / "titan-home"), patch(
                "tools.cli.titan.CODEX_MARKETPLACE_DIR", tmp_path / "codex-marketplace"
            ), patch("tools.cli.titan._setup_codex_model_config", return_value={}), patch(
                "tools.cli.titan.run_codex_reinstall_plugin", return_value=0
            ) as reinstall_mock, patch("tools.cli.titan.patch_codex_config") as patch_config_mock:
                self.assertEqual(run_setup_codex(config_path=config_path), 0)

        reinstall_mock.assert_called_once_with(config_path=config_path)
        patch_config_mock.assert_not_called()

    def test_run_codex_reinstall_plugin_dry_run_prints_commands(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = run_codex_reinstall_plugin(dry_run=True)
        self.assertEqual(exit_code, 0)
        self.assertIn("codex plugin remove titan-memory@titan-local --json", stdout.getvalue())
        self.assertIn("codex plugin add titan-memory@titan-pi-memory --json", stdout.getvalue())
        self.assertIn("after final plugin add: patch Codex config", stdout.getvalue())

    def test_run_codex_reinstall_plugin_cleans_legacy_marketplaces_after_canonical_add(self):
        calls = []
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")

        def run(command, **kwargs):
            calls.append(command)
            return completed

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            with patch("tools.cli.titan.ensure_codex_marketplace_snapshot", return_value=(True, "ok")), patch(
                "tools.cli.titan.shutil.which", return_value="/usr/local/bin/codex"
            ), patch("tools.cli.titan.subprocess.run", side_effect=run):
                self.assertEqual(run_codex_reinstall_plugin(config_path=config_path), 0)

            self.assertIn(generate_codex_mcp_enable_block().strip(), config_path.read_text(encoding="utf-8"))

        self.assertEqual(calls[0][0:4], ["codex", "plugin", "marketplace", "add"])
        self.assertEqual(calls[1][0:5], ["codex", "plugin", "marketplace", "remove", "titan-karu-lab"])
        self.assertEqual(calls[-1][0:4], ["codex", "plugin", "add", "titan-memory@titan-pi-memory"])

    def test_codex_verify_rejects_legacy_plugin_registration(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"installed": [
                {"pluginId": "titan-memory@titan-pi-memory", "installed": True, "enabled": True},
                {"pluginId": "titan-memory@titan-local", "installed": True, "enabled": True},
            ]}),
            stderr="",
        )
        with patch("tools.cli.titan.subprocess.run", return_value=completed), patch(
            "tools.cli.titan.shutil.which", return_value="/usr/local/bin/codex"
        ):
            from tools.cli.titan import _codex_plugin_registration_status
            ok, detail = _codex_plugin_registration_status()
        self.assertFalse(ok)
        self.assertIn("legacy Titan plugin registrations remain", detail)

    def test_codex_verify_rejects_missing_canonical_plugin_registration(self):
        completed = SimpleNamespace(returncode=0, stdout=json.dumps({"installed": []}), stderr="")
        with patch("tools.cli.titan.subprocess.run", return_value=completed), patch(
            "tools.cli.titan.shutil.which", return_value="/usr/local/bin/codex"
        ):
            from tools.cli.titan import _codex_plugin_registration_status
            ok, detail = _codex_plugin_registration_status()
        self.assertFalse(ok)
        self.assertIn("not installed and enabled", detail)

    def test_main_doctor_defaults_to_opencode(self):
        with patch("tools.cli.titan.run_doctor", return_value=0) as doctor_mock:
            exit_code = main(["doctor"])
        self.assertEqual(exit_code, 0)
        doctor_mock.assert_called_once_with(agent="opencode")

    def test_main_doctor_accepts_positional_agent(self):
        with patch("tools.cli.titan.run_doctor", return_value=0) as doctor_mock:
            exit_code = main(["doctor", "opencode"])
        self.assertEqual(exit_code, 0)
        doctor_mock.assert_called_once_with(agent="opencode")

    def test_run_setup_non_interactive_requires_memory_key(self):
        stdout = io.StringIO()
        config_path = Path(tempfile.mkdtemp()) / "opencode.json"
        config_path.write_text("{}", encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch(
            "tools.cli.titan._check_ollama_status", return_value=(True, [])
        ), patch(
            "tools.cli.titan._find_agent_config", return_value=(config_path, True)
        ), patch(
            "tools.cli.titan.get_required_provider_envs",
            return_value={"required_envs": ["GEMINI_API_KEY"], "warnings": [], "embedding_backend": "ollama"},
        ), patch.dict(os.environ, {}, clear=True):
            exit_code = run_setup(agent="opencode", non_interactive=True)
        self.assertEqual(exit_code, 1)
        self.assertIn("GEMINI_API_KEY", stdout.getvalue())

    def test_run_setup_interactive_writes_openai_agent_model_config(self):
        stdout = io.StringIO()
        config_path = Path(tempfile.mkdtemp()) / "opencode.json"
        config_path.write_text("{}", encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch.dict(os.environ, {}, clear=True), patch(
            "builtins.input",
            side_effect=["2", "sk-openai", "1", "1", "1", "y"],
        ), patch(
            "tools.cli.titan._check_ollama_status", return_value=(True, [])
        ), patch(
            "tools.cli.titan._find_agent_config", return_value=(config_path, True)
        ), patch(
            "tools.cli.titan.install_opencode_plugin",
            return_value={"status": "installed", "scope": "global", "target_path": str(Path(tmp_dir) / "plugin.ts")},
        ), patch(
            "tools.cli.titan.run_onboarding_smoke_test",
            return_value={"ok": True, "issues": [], "detail": "ok", "ingest_status": "stored", "retrieval_count": 1},
        ):
            exit_code = run_setup(agent="opencode", config_path=config_path)
            self.assertEqual(exit_code, 0)
            agent_home = Path(tmp_dir) / "agents" / "opencode"
            extraction_cfg = yaml.safe_load((agent_home / "config" / "extraction_models.yaml").read_text(encoding="utf-8"))
            embedding_cfg = yaml.safe_load((agent_home / "config" / "embedding_models.yaml").read_text(encoding="utf-8"))
            env_text = (agent_home / ".env").read_text(encoding="utf-8")
            self.assertEqual(extraction_cfg["current"], "openai")
            self.assertEqual(extraction_cfg["openai"]["model"], "gpt-4o-mini")
            self.assertEqual(embedding_cfg["current"], "ollama")
            self.assertIn("OPENAI_API_KEY=sk-openai", env_text)
            self.assertIn("TITAN_EXTRACTION_CONFIG_PATH=", env_text)
            self.assertIn("TITAN_EMBEDDING_CONFIG_PATH=", env_text)

    def test_run_setup_custom_supports_openrouter_and_openai_embeddings(self):
        stdout = io.StringIO()
        config_path = Path(tempfile.mkdtemp()) / "opencode.json"
        config_path.write_text("{}", encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch.dict(os.environ, {}, clear=True), patch(
            "builtins.input",
            side_effect=["4", "router-key", "1", "2", "openai-key", "y"],
        ), patch(
            "tools.cli.titan._check_ollama_status", side_effect=AssertionError("Ollama should not be checked")
        ), patch(
            "tools.cli.titan._find_agent_config", return_value=(config_path, True)
        ), patch(
            "tools.cli.titan.install_opencode_plugin",
            return_value={"status": "installed", "scope": "global", "target_path": str(Path(tmp_dir) / "plugin.ts")},
        ), patch(
            "tools.cli.titan.run_onboarding_smoke_test",
            return_value={"ok": True, "issues": [], "detail": "ok", "ingest_status": "stored", "retrieval_count": 1},
        ):
            exit_code = run_setup(agent="opencode", config_path=config_path)
            self.assertEqual(exit_code, 0)
            agent_home = Path(tmp_dir) / "agents" / "opencode"
            extraction_cfg = yaml.safe_load((agent_home / "config" / "extraction_models.yaml").read_text(encoding="utf-8"))
            embedding_cfg = yaml.safe_load((agent_home / "config" / "embedding_models.yaml").read_text(encoding="utf-8"))
            env_text = (agent_home / ".env").read_text(encoding="utf-8")
            self.assertEqual(extraction_cfg["current"], "openrouter")
            self.assertEqual(extraction_cfg["openrouter"]["model"], "anthropic/claude-3.5-sonnet")
            self.assertEqual(embedding_cfg["current"], "openai")
            self.assertEqual(embedding_cfg["openai"]["model"], "text-embedding-3-small")
            self.assertIn("OPENROUTER_API_KEY=router-key", env_text)
            self.assertIn("OPENAI_API_KEY=openai-key", env_text)

    def test_run_setup_asks_before_patching_opencode_config(self):
        stdout = io.StringIO()
        config_path = Path(tempfile.mkdtemp()) / "opencode.json"
        config_path.write_text("{}", encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai"}, clear=True), patch(
            "builtins.input",
            side_effect=["2", "1", "2", "n"],
        ), patch(
            "tools.cli.titan._check_ollama_status", side_effect=AssertionError("Ollama should not be checked")
        ), patch(
            "tools.cli.titan._find_agent_config", return_value=(config_path, True)
        ), patch(
            "tools.cli.titan.install_opencode_plugin",
            return_value={"status": "installed", "scope": "global", "target_path": str(Path(tmp_dir) / "plugin.ts")},
        ), patch(
            "tools.cli.titan.patch_opencode_config"
        ) as patch_config_mock, patch(
            "tools.cli.titan.run_onboarding_smoke_test"
        ) as smoke_mock:
            exit_code = run_setup(agent="opencode", config_path=config_path)
        self.assertEqual(exit_code, 1)
        self.assertEqual(config_path.read_text(encoding="utf-8"), "{}")
        patch_config_mock.assert_not_called()
        smoke_mock.assert_not_called()
        self.assertIn("left your OpenCode config unchanged", stdout.getvalue())

    def test_run_setup_non_interactive_requires_yes_before_patching(self):
        stdout = io.StringIO()
        config_path = Path(tempfile.mkdtemp()) / "opencode.json"
        config_path.write_text("{}", encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai"}, clear=True), patch(
            "builtins.input",
            side_effect=["2", "1", "2"],
        ), patch(
            "tools.cli.titan._check_ollama_status", side_effect=AssertionError("Ollama should not be checked")
        ), patch(
            "tools.cli.titan._find_agent_config", return_value=(config_path, True)
        ), patch(
            "tools.cli.titan.get_required_provider_envs",
            return_value={"required_envs": [], "warnings": [], "embedding_backend": "openai"},
        ), patch(
            "tools.cli.titan.install_opencode_plugin",
            return_value={"status": "installed", "scope": "global", "target_path": str(Path(tmp_dir) / "plugin.ts")},
        ), patch(
            "tools.cli.titan.patch_opencode_config"
        ) as patch_config_mock, patch(
            "tools.cli.titan.run_onboarding_smoke_test"
        ) as smoke_mock:
            exit_code = run_setup(agent="opencode", non_interactive=True, config_path=config_path)
        self.assertEqual(exit_code, 1)
        patch_config_mock.assert_not_called()
        smoke_mock.assert_not_called()
        self.assertIn("Rerun with --yes", stdout.getvalue())

    def test_run_doctor_returns_success_when_waiting_for_first_event(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir, patch("sys.stdout", stdout), patch(
            "tools.cli.titan.TITAN_HOME", Path(tmp_dir)
        ), patch("tools.cli.titan.Path.home", return_value=Path(tmp_dir)), patch(
            "tools.cli.titan.get_required_provider_envs",
            return_value={"required_envs": [], "warnings": [], "extraction_backend": "unknown"},
        ), patch(
            "tools.cli.titan.run_connected_loop_test",
            return_value={
                "ok": False,
                "issues": ["no plugin events found"],
                "detail": "no sessions",
                "ingest_status": "unknown",
                "retrieval_count": 0,
            },
        ):
            plugin_path = Path(tmp_dir) / ".config" / "opencode" / "plugins" / "titan_v2_spool_plugin.ts"
            plugin_path.parent.mkdir(parents=True, exist_ok=True)
            plugin_path.write_text("test", encoding="utf-8")
            exit_code = run_doctor(agent="opencode")
        self.assertEqual(exit_code, 0)
        self.assertIn("haven't caught any events", stdout.getvalue())

    def test_connected_loop_uses_titan_spool_dir_env_when_set(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_spool_dir = Path(tmp_dir) / "explicit-spool"
            with patch.dict(os.environ, {"TITAN_SPOOL_DIR": str(env_spool_dir)}, clear=False):
                with patch("app.save_pipeline.auto_ingest.discover_spool_sessions", return_value=[]) as discover_mock:
                    result = run_connected_loop_test(plugin_path=Path(__file__))
        self.assertFalse(result["ok"])
        self.assertIn("no plugin events found", result["issues"])
        discover_mock.assert_called_once_with(env_spool_dir)

    def test_connected_loop_reports_retrieval_empty_when_no_memories_stored(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            spool_dir = Path(tmp_dir)
            (spool_dir / "s1.jsonl").write_text("{}", encoding="utf-8")
            with patch("app.save_pipeline.pipeline.ingest_spool_session", return_value={"stored_memories": 0}), patch(
                "app.storage.memories.get_recent_memories",
                return_value=[],
            ):
                result = run_connected_loop_test(spool_dir=spool_dir, plugin_path=Path(__file__))
        self.assertFalse(result["ok"])
        self.assertIn("retrieval returned empty", result["issues"])

    def test_connected_loop_processes_multiple_batches_until_memories_found(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            spool_dir = Path(tmp_dir)
            (spool_dir / "s1.jsonl").write_text("{}", encoding="utf-8")
            ingest_side_effect = [
                {
                    "processed_events": 200,
                    "prompt_candidates": 0,
                    "stored_memories": 0,
                    "processed_sessions": ["s1"],
                },
                {
                    "processed_events": 40,
                    "prompt_candidates": 1,
                    "stored_memories": 1,
                    "processed_sessions": ["s1"],
                },
            ]
            with patch("app.save_pipeline.pipeline.ingest_spool_session", side_effect=ingest_side_effect) as ingest_mock, patch(
                "app.storage.memories.get_recent_memories",
                return_value=[SimpleNamespace(text="important conversation detail")],
            ), patch(
                "app.save_pipeline.pipeline.retrieve_memory_brief",
                return_value={"count": 1, "brief": "found"},
            ):
                result = run_connected_loop_test(spool_dir=spool_dir, plugin_path=Path(__file__))
        self.assertTrue(result["ok"])
        self.assertEqual(ingest_mock.call_count, 2)

    def test_run_set_key_writes_value_to_titan_home_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)):
                exit_code = run_set_key(key_name="GEMINI_API_KEY", value="abc123")
            self.assertEqual(exit_code, 0)
            env_text = (Path(tmp_dir) / ".env").read_text(encoding="utf-8")
            self.assertIn("GEMINI_API_KEY=abc123", env_text)

    def test_run_set_key_can_write_to_agent_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)):
                exit_code = run_set_key(key_name="GEMINI_API_KEY", value="abc123", agent="opencode")
            self.assertEqual(exit_code, 0)
            env_text = (Path(tmp_dir) / "agents" / "opencode" / ".env").read_text(encoding="utf-8")
            self.assertIn("GEMINI_API_KEY=abc123", env_text)

    def test_run_set_key_prompts_when_value_not_provided(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)), patch(
                "tools.cli.titan.getpass.getpass",
                return_value="from-prompt",
            ):
                exit_code = run_set_key(key_name="OPENAI_API_KEY")
            self.assertEqual(exit_code, 0)
            env_text = (Path(tmp_dir) / ".env").read_text(encoding="utf-8")
            self.assertIn("OPENAI_API_KEY=from-prompt", env_text)

    def test_run_set_key_rejects_invalid_key_name(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("tools.cli.titan.TITAN_HOME", Path(tmp_dir)):
                exit_code = run_set_key(key_name="gemini-api-key", value="x")
            self.assertEqual(exit_code, 2)
            self.assertFalse((Path(tmp_dir) / ".env").exists())

    def test_list_yaml_choices_skips_current_and_non_dict(self):
        from tools.cli.titan import _list_yaml_choices
        cfg = {
            "current": "gemini",
            "gemini": {"enabled": True, "model": "gemini-2.5-pro", "api_key_env": "GEMINI_API_KEY"},
            "openai": {"enabled": False, "model": "gpt-4o-mini", "api_key_env": "OPENAI_API_KEY"},
        }
        choices = _list_yaml_choices(cfg, "extraction")
        names = [c[0] for c in choices]
        self.assertNotIn("current", names)
        self.assertIn("gemini", names)
        self.assertIn("openai", names)
        gemini_choice = next(c for c in choices if c[0] == "gemini")
        self.assertEqual(gemini_choice[1], "gemini-2.5-pro")
        self.assertEqual(gemini_choice[3], "GEMINI_API_KEY")


if __name__ == "__main__":
    unittest.main()
