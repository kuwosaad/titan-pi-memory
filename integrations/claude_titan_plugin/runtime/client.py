from __future__ import annotations

import http.client
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

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


def _post(name: str, route: str, payload: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    state = read_state(name)
    if state is None:
        return None
    connection = http.client.HTTPConnection("127.0.0.1", state.port, timeout=timeout)
    try:
        connection.request(
            "POST", route, body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Titan-Claude-Token": state.token},
        )
        response = connection.getresponse()
        if response.status == 200:
            result = json.loads(response.read())
            if isinstance(result, dict) and result.get("ok") is True:
                return result
    except (OSError, ValueError, http.client.HTTPException):
        pass
    finally:
        connection.close()
    return None


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

    return _post(
        name or agent_name(), "/recall",
        {"query": query, "session_id": session_id, "sources": sources, "limit": limit}, timeout,
    )


def submit_events(events: list[dict[str, Any]], *, name: str | None = None, timeout: float = 1.5) -> bool:
    if not events:
        return True
    name = name or agent_name()
    if _post(name, "/events", {"events": events}, timeout) is not None:
        return True
    _spool(events, name)
    return False
