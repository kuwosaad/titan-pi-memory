from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from integrations.claude_titan_plugin.runtime.client import recall_context, submit_events
from integrations.claude_titan_plugin.runtime.common import read_state, state_path
from integrations.claude_titan_plugin.runtime.daemon import RuntimeGateway, _compact_recall_payload
from integrations.claude_titan_plugin.runtime.proxy import ensure_daemon


ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "integrations" / "claude_titan_plugin" / "scripts" / "titan_claude_mcp.py"


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent: str = "claude-runtime-test") -> dict[str, str]:
    env = os.environ.copy()
    values = {
        "HOME": str(tmp_path / "home"),
        "TITAN_CLAUDE_DATA": str(tmp_path / "plugin-data"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "TITAN_AGENT_NAME": agent,
        "TITAN_CLAUDE_IDLE_SECONDS": "30",
        "PYTHONPATH": str(ROOT),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
        env[key] = value
    return env


def _stop(state) -> None:
    try:
        httpx.post(
            f"http://127.0.0.1:{state.port}/shutdown",
            headers={"X-Titan-Claude-Token": state.token},
            timeout=1,
        )
    except httpx.HTTPError:
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if read_state(state.agent_name) is None:
            return
        time.sleep(0.05)
    if os.name != "nt":
        try:
            os.kill(state.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    raise AssertionError("Claude test daemon did not shut down within five seconds")


async def _remote_tools(state) -> list[str]:
    async with httpx.AsyncClient(headers={"X-Titan-Claude-Token": state.token}, timeout=None) as client:
        async with streamable_http_client(state.url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [tool.name for tool in (await session.list_tools()).tools]


def test_one_daemon_serves_multiple_mcp_clients(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    async def exercise() -> None:
        state = await asyncio.to_thread(ensure_daemon, timeout=20)
        try:
            again = await asyncio.to_thread(ensure_daemon, timeout=20)
            first, second = await asyncio.gather(_remote_tools(state), _remote_tools(again))
            assert again.pid == state.pid
            assert first == second
            assert len(first) == 18
            assert {"query_memories", "store_trace_event", "get_scene_context"}.issubset(first)
            if os.name != "nt":
                assert state_path(state.agent_name).stat().st_mode & 0o077 == 0
            unauthorized = httpx.get(f"http://127.0.0.1:{state.port}/health", timeout=1)
            assert unauthorized.status_code == 401
            event = {
                "session_id": "s1",
                "event_id": "owner-e1",
                "event_type": "user_message",
                "schema_version": "v1",
                "payload": {"content": "hello"},
            }
            assert await asyncio.to_thread(submit_events, [event], name=state.agent_name) is True
            unauthorized_recall = httpx.post(
                f"http://127.0.0.1:{state.port}/recall",
                json={"query": "hello"},
                timeout=1,
            )
            assert unauthorized_recall.status_code == 401
            bounded = httpx.post(
                f"http://127.0.0.1:{state.port}/recall",
                headers={"X-Titan-Claude-Token": state.token},
                json={"query": "", "limit": 99},
                timeout=3,
            )
            assert bounded.status_code == 200
            assert bounded.json()["limit"] == 8
            assert "memories" not in bounded.json()
            compact = await asyncio.to_thread(
                recall_context,
                "",
                name=state.agent_name,
                limit=99,
                timeout=3,
            )
            assert compact is not None and compact["limit"] == 8
        finally:
            _stop(state)

    asyncio.run(exercise())


def test_stdio_proxy_mirrors_tools_and_blocks_external_pattern_paths(monkeypatch, tmp_path):
    env = _configure(monkeypatch, tmp_path, "claude-proxy-test")

    async def exercise() -> None:
        params = StdioServerParameters(command=sys.executable, args=[str(LAUNCHER)], env=env, cwd=ROOT)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [tool.name for tool in (await session.list_tools()).tools]
                assert len(tools) == 18
                inline = await session.call_tool("patterns_export_bundle", {})
                assert inline.isError is not True
                blocked = await session.call_tool("patterns_export_bundle", {"path": str(tmp_path / "outside.json")})
                assert blocked.isError is True

    try:
        asyncio.run(exercise())
    finally:
        state = read_state("claude-proxy-test")
        if state:
            _stop(state)


def test_event_submission_uses_owner_or_private_fallback(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, "claude-event-test")
    event = {
        "session_id": "s1",
        "event_id": "e1",
        "event_type": "user_message",
        "schema_version": "v1",
        "payload": {"content": "hello"},
    }
    assert submit_events([event], name="claude-event-test", timeout=0.05) is False
    files = list((tmp_path / "plugin-data" / "runtime" / "claude-event-test" / "fallback").glob("*.jsonl"))
    assert len(files) == 1
    if os.name != "nt":
        assert files[0].stat().st_mode & 0o077 == 0


def test_recall_helper_is_fail_open_and_never_starts_owner(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, "claude-recall-absent")
    assert recall_context("history", name="claude-recall-absent", timeout=0.05) is None
    assert read_state("claude-recall-absent") is None


def test_compact_recall_has_provenance_and_no_full_memory_payload():
    payload = {
        "memories": [
            {
                "id": f"m{i}",
                "text": f"memory text {i}",
                "source_agent": "pi" if i % 2 else "codex",
                "scene_id": f"scene-{i}",
                "verification_status": "verified" if i % 2 else "unverified",
            }
            for i in range(20)
        ]
    }
    compact = _compact_recall_payload(payload, limit=8)
    assert compact["count"] == 8
    assert compact["limit"] == 8
    assert len(compact["scene_refs"]) == 8
    assert "[source:codex scene:scene-0 status:unverified]" in compact["brief"]
    assert "memories" not in compact
    assert len(compact["brief"]) <= 4000


def test_recall_timeout_and_failure_return_clean_non_200(monkeypatch):
    class SlowModule:
        @staticmethod
        def retrieve_memory_brief(**kwargs):
            time.sleep(0.25)
            return {"memories": []}

        @staticmethod
        def load_recent_memories(**kwargs):
            return []

    class FailingModule(SlowModule):
        @staticmethod
        def retrieve_memory_brief(**kwargs):
            raise RuntimeError("backend offline")

    async def noop_app(scope, receive, send):
        return None

    monkeypatch.setenv("TITAN_CLAUDE_RECALL_TIMEOUT_SECONDS", "0.1")
    slow = RuntimeGateway(noop_app, token="token", module=SlowModule())
    status, result = asyncio.run(slow.recall({"query": "history", "limit": 100}))
    assert status == 504
    assert result == {"ok": False, "error": "Titan recall timed out"}

    failing = RuntimeGateway(noop_app, token="token", module=FailingModule())
    status, result = asyncio.run(failing.recall({"query": "history", "limit": 0}))
    assert status == 503
    assert result["ok"] is False
    assert "backend offline" in result["error"]
