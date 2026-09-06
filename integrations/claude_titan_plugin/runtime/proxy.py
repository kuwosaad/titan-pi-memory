from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import RUNTIME_VERSION
from .common import (
    RuntimeState,
    agent_name,
    data_root,
    ownership_lock,
    process_alive,
    read_state,
    reserve_port,
    runtime_dir,
    spawn_daemon,
    state_path,
)


def _identity(state: RuntimeState) -> dict | None:
    if not process_alive(state.pid) or not state.owner_nonce:
        return None
    try:
        response = httpx.get(
            f"http://127.0.0.1:{state.port}/identity",
            headers={"X-Titan-Claude-Token": state.token},
            timeout=0.5,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        if (
            payload.get("ok") is True
            and int(payload.get("pid") or 0) == state.pid
            and str(payload.get("owner_nonce") or "") == state.owner_nonce
            and abs(float(payload.get("started_at") or 0.0) - state.started_at) < 0.001
        ):
            return payload
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return None


def _healthy(state: RuntimeState) -> bool:
    return state.version == RUNTIME_VERSION and _identity(state) is not None


def _wait_owner_stopped(state: RuntimeState, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _identity(state) is None:
            return True
        time.sleep(0.05)
    return _identity(state) is None


def _stop_verified_owner(state: RuntimeState, *, timeout: float = 5.0) -> None:
    if not process_alive(state.pid):
        return
    if _identity(state) is None:
        raise RuntimeError("Refusing to replace an unverified Titan Claude runtime process")
    try:
        httpx.post(
            f"http://127.0.0.1:{state.port}/shutdown",
            headers={"X-Titan-Claude-Token": state.token},
            timeout=1.0,
        )
    except httpx.HTTPError:
        pass
    if _wait_owner_stopped(state, timeout):
        return
    raise RuntimeError(
        "Previous Titan Claude runtime is still active after graceful shutdown; "
        "refusing to replace it automatically"
    )


def ensure_daemon(name: str | None = None, *, timeout: float = 20.0) -> RuntimeState:
    name = name or agent_name()
    with ownership_lock(name):
        existing = read_state(name)
        if existing and _healthy(existing):
            return existing
        if existing and process_alive(existing.pid):
            _stop_verified_owner(existing, timeout=min(5.0, timeout / 2))
        current = read_state(name)
        if current is None or existing is None or current.owner_nonce == existing.owner_nonce:
            state_path(name).unlink(missing_ok=True)

        deadline = time.monotonic() + timeout
        last_error = "runtime did not publish healthy state"
        for _attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            port = reserve_port()
            token = secrets.token_urlsafe(32)
            owner_nonce = secrets.token_urlsafe(24)
            started_at = time.time()
            process = spawn_daemon(port, token, name, owner_nonce, started_at)
            attempt_deadline = min(deadline, time.monotonic() + max(2.0, remaining / 3))
            while time.monotonic() < attempt_deadline:
                published = read_state(name)
                if (
                    published
                    and published.pid == process.pid
                    and published.owner_nonce == owner_nonce
                    and _healthy(published)
                ):
                    return published
                if process.poll() is not None:
                    last_error = f"runtime exited {process.returncode} before startup"
                    break
                time.sleep(0.1)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            state_path(name).unlink(missing_ok=True)
        raise RuntimeError(
            f"Titan Claude runtime failed to start ({last_error}); see {runtime_dir(name) / 'daemon.log'}"
        )


def _allowed_pattern_path(raw: object, *, must_exist: bool) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    unresolved = Path(raw).expanduser()
    if unresolved.suffix.lower() != ".json" or unresolved.is_symlink():
        return False
    candidate = unresolved.resolve(strict=False)
    roots = [
        (data_root() / "bundles").resolve(),
        (Path.home() / ".titan" / "exports").resolve(),
    ]
    if not any(candidate != root and candidate.is_relative_to(root) for root in roots):
        return False
    if must_exist:
        return candidate.is_file() and not candidate.is_symlink()
    return not candidate.exists() or (candidate.is_file() and not candidate.is_symlink())


def _blocked_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps({"error": message, "status_code": 403}))],
        isError=True,
    )


async def _lease_request(client: httpx.AsyncClient, state: RuntimeState, action: str, lease_id: str) -> None:
    response = await client.post(
        f"http://127.0.0.1:{state.port}/lease/{action}",
        json={"lease_id": lease_id},
        timeout=2.0,
    )
    response.raise_for_status()


async def run_proxy() -> None:
    state = await asyncio.to_thread(ensure_daemon)
    headers = {"X-Titan-Claude-Token": state.token}
    lease_id = secrets.token_urlsafe(18)
    async with httpx.AsyncClient(headers=headers, timeout=None) as client:
        await _lease_request(client, state, "open", lease_id)
        stop_heartbeat = asyncio.Event()

        async def heartbeat() -> None:
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=15.0)
                except TimeoutError:
                    try:
                        await _lease_request(client, state, "heartbeat", lease_id)
                    except httpx.HTTPError:
                        return

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            async with streamable_http_client(state.url, http_client=client) as (remote_read, remote_write, _):
                async with ClientSession(remote_read, remote_write) as remote:
                    await remote.initialize()
                    proxy = Server("titan-memory-claude-proxy", version=RUNTIME_VERSION)

                    @proxy.list_tools()
                    async def list_tools() -> list[types.Tool]:
                        return list((await remote.list_tools()).tools)

                    @proxy.call_tool(validate_input=True)
                    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
                        if name == "patterns_import_bundle" and not _allowed_pattern_path(
                            arguments.get("path"), must_exist=True
                        ):
                            return _blocked_result(
                                "Claude plugin pattern imports must be regular .json files inside "
                                "CLAUDE_PLUGIN_DATA/bundles or ~/.titan/exports"
                            )
                        if name == "patterns_export_bundle" and arguments.get("path") is not None:
                            if not _allowed_pattern_path(arguments.get("path"), must_exist=False):
                                return _blocked_result(
                                    "Claude plugin pattern exports must be regular .json files inside "
                                    "CLAUDE_PLUGIN_DATA/bundles or ~/.titan/exports"
                                )
                        return await remote.call_tool(name, arguments)

                    async with stdio_server() as (local_read, local_write):
                        await proxy.run(
                            local_read,
                            local_write,
                            proxy.create_initialization_options(),
                            raise_exceptions=False,
                        )
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await _lease_request(client, state, "close", lease_id)
            except httpx.HTTPError:
                pass


def main() -> int:
    try:
        asyncio.run(run_proxy())
    except Exception as exc:
        print(f"[titan-memory] {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
