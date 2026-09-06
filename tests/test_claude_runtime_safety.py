from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from integrations.claude_titan_plugin.runtime import RUNTIME_VERSION
from integrations.claude_titan_plugin.runtime.common import (
    atomic_write_state,
    process_alive,
    read_state,
    reserve_port,
    spawn_daemon,
)
from integrations.claude_titan_plugin.runtime.daemon import (
    EVENT_BODY_LIMIT,
    LEASE_TTL_SECONDS,
    RuntimeGateway,
    _drain_fallback,
    _fallback_monitor,
)
from integrations.claude_titan_plugin.runtime.proxy import _allowed_pattern_path, ensure_daemon


ROOT = Path(__file__).resolve().parent.parent


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent: str) -> None:
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


def _shutdown(state) -> None:
    try:
        httpx.post(
            f"http://127.0.0.1:{state.port}/shutdown",
            headers={"X-Titan-Claude-Token": state.token},
            timeout=1,
        )
    except httpx.HTTPError:
        pass


def _identity_available(state) -> bool:
    try:
        response = httpx.get(
            f"http://127.0.0.1:{state.port}/identity",
            headers={"X-Titan-Claude-Token": state.token},
            timeout=0.2,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def test_version_handoff_stops_verified_old_owner_before_replacement(monkeypatch, tmp_path):
    agent = "claude-version-handoff"
    _configure(monkeypatch, tmp_path, agent)
    first = ensure_daemon(agent, timeout=20)
    try:
        atomic_write_state(replace(first, version="old-runtime"))
        second = ensure_daemon(agent, timeout=20)
        try:
            assert second.pid != first.pid
            assert second.version == RUNTIME_VERSION
            assert not _identity_available(first)
            assert _identity_available(second)
        finally:
            _shutdown(second)
    finally:
        if _identity_available(first):
            _shutdown(first)


def test_lifetime_lease_rejects_second_mutating_daemon(monkeypatch, tmp_path):
    agent = "claude-lifetime-lease"
    _configure(monkeypatch, tmp_path, agent)
    first = ensure_daemon(agent, timeout=20)
    try:
        second = spawn_daemon(
            reserve_port(),
            "second-token",
            agent,
            "second-owner",
            time.time(),
        )
        assert second.wait(timeout=5) == 2
        assert _identity_available(first)
        assert read_state(agent).owner_nonce == first.owner_nonce
    finally:
        _shutdown(first)


def test_gateway_client_leases_block_idle_shutdown_decision():
    async def noop(scope, receive, send):
        return None

    gateway = RuntimeGateway(noop, token="token", module=object())
    gateway.leases["active"] = time.monotonic()
    assert gateway.has_active_clients() is True
    gateway.leases["active"] = time.monotonic() - LEASE_TTL_SECONDS - 1
    assert gateway.has_active_clients() is False
    gateway.close()


def test_shutdown_refuses_while_client_lease_is_active():
    async def noop(scope, receive, send):
        return None

    gateway = RuntimeGateway(noop, token="token", module=object())
    gateway.leases["active"] = time.monotonic()
    shutdown_called = False

    def shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    gateway.shutdown_callback = shutdown
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/shutdown",
        "headers": [(b"x-titan-claude-token", b"token")],
    }
    asyncio.run(gateway(scope, receive, send))

    assert sent[0]["status"] == 409
    assert shutdown_called is False
    gateway.close()


def test_fallback_drain_orders_events_by_timestamp_not_filename(monkeypatch, tmp_path):
    agent = "claude-fallback-order"
    _configure(monkeypatch, tmp_path, agent)
    fallback = tmp_path / "plugin-data" / "runtime" / agent / "fallback"
    fallback.mkdir(parents=True)
    (fallback / "a-assistant.jsonl").write_text(
        '{"session_id":"s","event_id":"a","event_type":"assistant_message","ts":"2026-01-01T00:00:02Z","payload":{}}\n',
        encoding="utf-8",
    )
    (fallback / "z-user.jsonl").write_text(
        '{"session_id":"s","event_id":"u","event_type":"user_message","ts":"2026-01-01T00:00:01Z","payload":{}}\n',
        encoding="utf-8",
    )
    ingested = []

    class FakeModule:
        class TraceEvent:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        @staticmethod
        def ingest_trace_event(event, process_new=True):
            assert process_new is False
            ingested.append(event.event_type)
            return {"status": "ingested"}

    monkeypatch.setattr("app.save_pipeline.pipeline.process_session_events", lambda session_id: {})
    _drain_fallback(FakeModule, agent)

    assert ingested == ["user_message", "assistant_message"]
    assert not list(fallback.glob("*.jsonl"))


def test_pattern_bundle_paths_are_limited_to_dedicated_json_directories(monkeypatch, tmp_path):
    home = tmp_path / "home"
    plugin_data = tmp_path / "plugin-data"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))
    allowed_import = plugin_data / "bundles" / "input.json"
    allowed_import.parent.mkdir(parents=True)
    allowed_import.write_text("{}", encoding="utf-8")
    allowed_export = home / ".titan" / "exports" / "output.json"
    memory_db = home / ".titan" / "agents" / "claude-code" / "out" / "memories" / "memory_store.db"
    daemon_state = plugin_data / "runtime" / "claude-code" / "daemon.json"

    assert _allowed_pattern_path(str(allowed_import), must_exist=True)
    assert _allowed_pattern_path(str(allowed_export), must_exist=False)
    assert not _allowed_pattern_path(str(memory_db), must_exist=False)
    assert not _allowed_pattern_path(str(daemon_state), must_exist=False)
    assert not _allowed_pattern_path(str(plugin_data / "bundles" / "not-json.txt"), must_exist=False)


def test_event_endpoint_batches_ingest_and_processes_each_session_once(monkeypatch):
    ingested = []
    processed = []

    class FakeModule:
        class TraceEvent:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        @staticmethod
        def ingest_trace_event(event, process_new=True):
            ingested.append((event.event_id, process_new))
            return {"status": "ingested", "event_id": event.event_id}

    monkeypatch.setattr(
        "app.save_pipeline.pipeline.process_session_events",
        lambda session_id: processed.append(session_id) or {},
    )

    async def noop(scope, receive, send):
        return None

    gateway = RuntimeGateway(noop, token="token", module=FakeModule())
    body = json.dumps({"events": [
        {"session_id": "s1", "event_id": "e1", "event_type": "user_message", "payload": {}},
        {"session_id": "s1", "event_id": "e2", "event_type": "assistant_message", "payload": {}},
        {"session_id": "s2", "event_id": "e3", "event_type": "user_message", "payload": {}},
    ]}).encode()
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/events",
        "headers": [(b"x-titan-claude-token", b"token")],
    }
    asyncio.run(gateway(scope, receive, send))

    assert sent[0]["status"] == 200
    assert ingested == [("e1", False), ("e2", False), ("e3", False)]
    assert processed == ["s1", "s2"]
    gateway.close()


def test_fallback_monitor_does_not_block_event_loop(monkeypatch):
    completed = False

    def slow_drain(module, agent):
        nonlocal completed
        time.sleep(0.2)
        completed = True

    monkeypatch.setattr(
        "integrations.claude_titan_plugin.runtime.daemon._drain_fallback",
        slow_drain,
    )

    async def exercise():
        task = asyncio.create_task(_fallback_monitor(object(), "agent", lambda: False))
        await asyncio.sleep(0.02)
        assert completed is False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_gateway_rejects_oversized_event_body():
    async def noop(scope, receive, send):
        return None

    gateway = RuntimeGateway(noop, token="token", module=object())
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": b"x" * (EVENT_BODY_LIMIT + 1), "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/events",
        "headers": [(b"x-titan-claude-token", b"token")],
    }
    asyncio.run(gateway(scope, receive, send))
    assert sent[0]["status"] == 413
    gateway.close()


def test_recall_capacity_stays_occupied_until_timed_out_worker_finishes(monkeypatch):
    class SlowModule:
        @staticmethod
        def retrieve_memory_brief(**kwargs):
            time.sleep(0.3)
            return {"memories": []}

    async def noop(scope, receive, send):
        return None

    monkeypatch.setenv("TITAN_CLAUDE_RECALL_TIMEOUT_SECONDS", "0.1")
    gateway = RuntimeGateway(noop, token="token", module=SlowModule())

    async def exercise():
        first, second = await asyncio.gather(
            gateway.recall({"query": "a"}),
            gateway.recall({"query": "b"}),
        )
        third = await gateway.recall({"query": "c"})
        assert first[0] == 504
        assert second[0] == 504
        assert third == (503, {"ok": False, "error": "Titan recall is busy"})
        await asyncio.sleep(0.25)

    asyncio.run(exercise())
    gateway.close()
