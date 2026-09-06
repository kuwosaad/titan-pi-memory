from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .common import _chmod_private, agent_name, fallback_dir, read_state


def _spool(events: list[dict[str, Any]], name: str) -> Path:
    root = fallback_dir(name)
    target = root / f"{time.time_ns():020d}-{os.getpid()}-{uuid.uuid4().hex}.jsonl"
    fd, temporary = tempfile.mkstemp(prefix=".event-", dir=root)
    tmp = Path(temporary)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        _chmod_private(target)
        return target
    finally:
        tmp.unlink(missing_ok=True)


def recall_context(
    query: str = "",
    *,
    session_id: str | None = None,
    sources: list[str] | str | None = None,
    limit: int = 5,
    name: str | None = None,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    """Read compact recall from an already-running owner; never starts or spools."""

    name = name or agent_name()
    state = read_state(name)
    if state is None:
        return None
    payload: dict[str, Any] = {
        "query": query,
        "session_id": session_id,
        "sources": sources,
        "limit": limit,
    }
    try:
        response = httpx.post(
            f"http://127.0.0.1:{state.port}/recall",
            headers={"X-Titan-Claude-Token": state.token},
            json=payload,
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        result = response.json()
        return result if isinstance(result, dict) and result.get("ok") is True else None
    except (httpx.HTTPError, ValueError):
        return None


def submit_events(events: list[dict[str, Any]], *, name: str | None = None, timeout: float = 1.5) -> bool:
    if not events:
        return True
    name = name or agent_name()
    state = read_state(name)
    if state is not None:
        try:
            response = httpx.post(
                f"http://127.0.0.1:{state.port}/events",
                headers={"X-Titan-Claude-Token": state.token},
                json={"events": events},
                timeout=timeout,
            )
            if response.status_code == 200 and response.json().get("ok") is True:
                return True
        except (httpx.HTTPError, ValueError):
            pass
    _spool(events, name)
    return False
