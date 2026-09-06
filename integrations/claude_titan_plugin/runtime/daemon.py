from __future__ import annotations

import argparse
import asyncio
import heapq
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import uvicorn

from .common import (
    RuntimeState,
    atomic_write_state,
    daemon_lease,
    fallback_dir,
    read_state,
    state_path,
)

EVENT_BODY_LIMIT = 1024 * 1024
RECALL_BODY_LIMIT = 64 * 1024
LEASE_TTL_SECONDS = 45.0
MAX_RECALL_WORKERS = 2
FALLBACK_DRAIN_FILE_LIMIT = 64


def _memory_dict(memory: Any) -> dict[str, Any]:
    if isinstance(memory, dict):
        return memory
    if hasattr(memory, "model_dump"):
        return memory.model_dump()
    return {
        key: getattr(memory, key, None)
        for key in ("id", "text", "source_agent", "scene_id", "verification_status", "ts")
    }


def _compact_recall_payload(payload: dict[str, Any], *, limit: int) -> dict[str, Any]:
    memories = [_memory_dict(memory) for memory in list(payload.get("memories") or [])[:limit]]
    refs: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str]] = set()
    supplied_refs = list(payload.get("scene_refs") or [])
    for memory in memories:
        source = str(memory.get("source_agent") or "unknown")
        scene_id = str(memory.get("scene_id") or "")
        key = (source, scene_id)
        if scene_id and key not in seen_refs:
            seen_refs.add(key)
            refs.append({"source_agent": source, "scene_id": scene_id})
    for raw_ref in supplied_refs:
        ref = _memory_dict(raw_ref)
        source = str(ref.get("source_agent") or "unknown")
        scene_id = str(ref.get("scene_id") or "")
        key = (source, scene_id)
        if scene_id and key not in seen_refs and len(refs) < limit:
            seen_refs.add(key)
            refs.append({"source_agent": source, "scene_id": scene_id})

    lines: list[str] = []
    remaining = 4000
    for memory in memories:
        source = str(memory.get("source_agent") or "unknown")
        scene_id = str(memory.get("scene_id") or "none")
        status = str(memory.get("verification_status") or "unverified")
        text = " ".join(str(memory.get("text") or "").split())
        line = f"- [source:{source} scene:{scene_id} status:{status}] {text}"
        if len(line) > 700:
            line = line[:697].rstrip() + "..."
        if len(line) + 1 > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 1

    return {
        "ok": True,
        "count": len(lines),
        "limit": limit,
        "brief": "\n".join(lines),
        "scene_refs": refs[:limit],
    }


def _ingest_event_batch(
    module: Any,
    events: Any,
    *,
    collect_results: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    touched: set[str] = set()
    for event in events:
        request = module.TraceEvent(**event)
        result = module.ingest_trace_event(request, process_new=False)
        if collect_results:
            results.append(result)
        if str(result.get("status") or "") != "duplicate":
            touched.add(request.session_id)

    if touched:
        from app.save_pipeline.pipeline import process_session_events

        for session_id in sorted(touched):
            process_session_events(session_id)
    return results, sorted(touched)


class RuntimeGateway:
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        token: str,
        module: Any,
        owner_nonce: str = "",
        started_at: float = 0.0,
    ):
        self.app = app
        self.token = token.encode("utf-8")
        self.module = module
        self.owner_nonce = owner_nonce
        self.started_at = started_at
        self.last_activity = time.monotonic()
        self.active_requests = 0
        self.leases: dict[str, float] = {}
        self.shutdown_callback: Callable[[], None] | None = None
        self.recall_executor = ThreadPoolExecutor(
            max_workers=MAX_RECALL_WORKERS,
            thread_name_prefix="titan-claude-recall",
        )
        self.recall_slots = asyncio.Semaphore(MAX_RECALL_WORKERS)

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        if not secrets.compare_digest(headers.get(b"x-titan-claude-token", b""), self.token):
            await self._json(send, 401, {"error": "unauthorized"})
            return
        self.last_activity = time.monotonic()
        path = scope.get("path", "")
        method = scope.get("method", "")
        if path in {"/health", "/identity"}:
            await self._json(
                send,
                200,
                {
                    "ok": True,
                    "pid": os.getpid(),
                    "owner_nonce": self.owner_nonce,
                    "started_at": self.started_at,
                },
            )
            return
        if path == "/shutdown" and method == "POST":
            if self.has_active_clients():
                await self._json(send, 409, {"ok": False, "error": "active Claude clients are still connected"})
                return
            await self._json(send, 200, {"ok": True})
            if self.shutdown_callback is not None:
                self.shutdown_callback()
            return
        if path.startswith("/lease/") and method == "POST":
            try:
                payload = json.loads((await self._body(receive, RECALL_BODY_LIMIT)).decode("utf-8"))
                lease_id = str(payload.get("lease_id") or "").strip()
                if not lease_id:
                    raise ValueError("lease_id is required")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                await self._json(send, 400, {"ok": False, "error": str(exc)})
                return
            if path == "/lease/close":
                self.leases.pop(lease_id, None)
            elif path in {"/lease/open", "/lease/heartbeat"}:
                self.leases[lease_id] = time.monotonic()
            else:
                await self._json(send, 404, {"ok": False, "error": "unknown lease endpoint"})
                return
            await self._json(send, 200, {"ok": True})
            return
        if path == "/events" and method == "POST":
            try:
                payload = json.loads((await self._body(receive, EVENT_BODY_LIMIT)).decode("utf-8"))
                raw_events = payload if isinstance(payload, list) else payload.get("events", [])
                if not isinstance(raw_events, list):
                    raise ValueError("events must be a list")
                results, touched = await asyncio.to_thread(_ingest_event_batch, self.module, raw_events)
                await self._json(
                    send,
                    200,
                    {"ok": True, "results": results, "processed_sessions": touched},
                )
            except Exception as exc:
                status = 413 if "request body exceeds" in str(exc) else 400
                await self._json(send, status, {"ok": False, "error": str(exc)})
            return
        if path == "/recall" and method == "POST":
            try:
                payload = json.loads((await self._body(receive, RECALL_BODY_LIMIT)).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("recall payload must be an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                status = 413 if "request body exceeds" in str(exc) else 400
                await self._json(send, status, {"ok": False, "error": str(exc)})
                return
            status, result = await self.recall(payload)
            await self._json(send, status, result)
            return

        if path.startswith("/mcp"):
            self.active_requests += 1
            try:
                await self.app(scope, receive, send)
            finally:
                self.active_requests = max(0, self.active_requests - 1)
                self.last_activity = time.monotonic()
            return
        await self.app(scope, receive, send)

    async def recall(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        query = str(payload.get("query") or "").strip()
        session_id = payload.get("session_id")
        sources = payload.get("sources")
        try:
            requested_limit = int(payload.get("limit", 5))
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "limit must be an integer"}
        limit = min(8, max(1, requested_limit))
        timeout = min(5.0, max(0.1, float(os.getenv("TITAN_CLAUDE_RECALL_TIMEOUT_SECONDS", "3"))))

        def load() -> dict[str, Any]:
            if query:
                kwargs: dict[str, Any] = {
                    "query": query,
                    "session_id": session_id,
                    "limit": limit,
                    "include_scenes": False,
                }
                if sources is not None:
                    kwargs["sources"] = sources
                return self.module.retrieve_memory_brief(**kwargs)
            kwargs = {"limit": limit, "session_id": session_id}
            if sources is not None:
                kwargs["sources"] = sources
            return {"memories": self.module.load_recent_memories(**kwargs), "scene_refs": []}

        try:
            await asyncio.wait_for(self.recall_slots.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            return 503, {"ok": False, "error": "Titan recall is busy"}

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.recall_executor, load)
        release_now = True
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            release_now = False

            async def release_when_finished() -> None:
                try:
                    await future
                except Exception:
                    pass
                self.recall_slots.release()

            asyncio.create_task(release_when_finished())
            return 504, {"ok": False, "error": "Titan recall timed out"}
        except Exception as exc:
            return 503, {"ok": False, "error": f"Titan recall unavailable: {exc}"}
        finally:
            if release_now:
                self.recall_slots.release()
        return 200, _compact_recall_payload(result, limit=limit)

    def has_active_clients(self) -> bool:
        cutoff = time.monotonic() - LEASE_TTL_SECONDS
        self.leases = {lease_id: seen for lease_id, seen in self.leases.items() if seen >= cutoff}
        return self.active_requests > 0 or bool(self.leases)

    def close(self) -> None:
        self.recall_executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    async def _body(receive: Callable, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"request body exceeds {max_bytes} bytes")
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    async def _json(send: Callable, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _start_workers(module: Any) -> tuple[threading.Event, threading.Event, threading.Event]:
    ingest_stop = threading.Event()
    interval = float(os.getenv("TITAN_AUTO_INGEST_INTERVAL_SECONDS", "3"))
    threading.Thread(
        target=module._auto_ingest_loop,
        args=(ingest_stop, module._RUNTIME_CONTEXT.trace_dir, interval),
        daemon=True,
        name="titan-auto-ingest",
    ).start()
    dedup_stop = threading.Event()
    module.start_dedup_worker(dedup_stop)
    lnn_stop = threading.Event()
    settings = module.load_settings() if hasattr(module, "load_settings") else None
    if settings is None:
        from app.retrieval_pipeline.config import load_settings

        settings = load_settings()
    if settings.get("lnn", {}).get("enabled") and settings.get("lnn", {}).get("tick_enabled", True):
        module.start_lnn_tick_worker(
            lnn_stop,
            interval_seconds=float(settings.get("lnn", {}).get("decay_tick_seconds", 60.0)),
            tau_disuse_decay=float(settings.get("lnn", {}).get("tau_disuse_decay", 0.01)),
            weight_decay=float(settings.get("lnn", {}).get("weight_decay", 0.001)),
        )
    return ingest_stop, dedup_stop, lnn_stop


def _fallback_event_sort_key(event: dict[str, Any], source: Path, line_number: int) -> tuple:
    raw_ts = str(event.get("ts") or "")
    try:
        parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        timestamp = parsed.timestamp()
    except (ValueError, OverflowError):
        timestamp = float("inf")
    return (
        timestamp,
        raw_ts,
        source.name,
        line_number,
        str(event.get("session_id") or ""),
        str(event.get("event_id") or ""),
    )


def _iter_fallback_events(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle):
            if not line.strip():
                continue
            event = json.loads(line)
            yield _fallback_event_sort_key(event, path, line_number), event


def _drain_fallback(module: Any, agent: str) -> None:
    root = fallback_dir(agent)
    claimed: list[tuple[Path, Path]] = []
    for path in sorted(root.glob("*.jsonl"))[:FALLBACK_DRAIN_FILE_LIMIT]:
        processing = path.with_suffix(path.suffix + f".processing-{os.getpid()}")
        try:
            path.replace(processing)
        except FileNotFoundError:
            continue
        claimed.append((path, processing))
    if not claimed:
        return

    try:
        streams = [_iter_fallback_events(processing) for _original, processing in claimed]
        merged = heapq.merge(*streams, key=lambda item: item[0])
        _ingest_event_batch(
            module,
            (event for _key, event in merged),
            collect_results=False,
        )
    except Exception:
        for original, processing in claimed:
            if processing.exists() and not original.exists():
                processing.replace(original)
    else:
        for _original, processing in claimed:
            processing.unlink(missing_ok=True)


async def _fallback_monitor(module: Any, agent: str, should_exit: Callable[[], bool]) -> None:
    while not should_exit():
        await asyncio.to_thread(_drain_fallback, module, agent)
        await asyncio.sleep(1.0)


async def _serve_owned(args: argparse.Namespace) -> None:
    os.environ["TITAN_AGENT_NAME"] = args.agent
    from tools.cli.titan import configure_runtime_for_agent

    configure_runtime_for_agent(args.agent)
    import entrypoints.mcp_server as titan_mcp

    workers = _start_workers(titan_mcp)
    gateway = RuntimeGateway(
        titan_mcp.server.streamable_http_app(),
        token=args.token,
        module=titan_mcp,
        owner_nonce=args.owner_nonce,
        started_at=args.started_at,
    )
    state = RuntimeState(
        pid=os.getpid(),
        port=args.port,
        token=args.token,
        version=args.version,
        agent_name=args.agent,
        owner_nonce=args.owner_nonce,
        started_at=args.started_at,
    )
    atomic_write_state(state)
    config = uvicorn.Config(gateway, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    gateway.shutdown_callback = lambda: setattr(server, "should_exit", True)

    async def idle_monitor() -> None:
        timeout = max(30.0, float(os.getenv("TITAN_CLAUDE_IDLE_SECONDS", "600")))
        while not server.should_exit:
            await asyncio.sleep(min(5.0, timeout / 4))
            if not gateway.has_active_clients() and time.monotonic() - gateway.last_activity >= timeout:
                server.should_exit = True

    monitor = asyncio.create_task(idle_monitor())
    fallback_task = asyncio.create_task(
        _fallback_monitor(titan_mcp, args.agent, lambda: server.should_exit)
    )
    try:
        await server.serve()
    finally:
        monitor.cancel()
        fallback_task.cancel()
        gateway.close()
        for event in workers:
            event.set()
        current = read_state(args.agent)
        if current and current.pid == os.getpid() and current.owner_nonce == args.owner_nonce:
            state_path(args.agent).unlink(missing_ok=True)


async def _serve(args: argparse.Namespace) -> None:
    with daemon_lease(args.agent):
        await _serve_owned(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--owner-nonce", required=True)
    parser.add_argument("--started-at", type=float, required=True)
    return parser


def main() -> int:
    try:
        asyncio.run(_serve(build_parser().parse_args()))
    except BlockingIOError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
