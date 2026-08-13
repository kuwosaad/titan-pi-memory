#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4


MAX_STRING_LENGTH = 1200
MAX_LIST_ITEMS = 20
MAX_DICT_ITEMS = 40
SECRET_PATTERNS = [
    (re.compile(r"([A-Za-z_][A-Za-z0-9_]*API_KEY\s*=\s*)[^\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[REDACTED]"),
]
SECRET_KEY_RE = re.compile(r"(api[_-]?key|authorization|token|secret|password|credential)", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_session_id(value: Any) -> str:
    session_id = str(value or "default").strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id)[:160] or "default"


def _redact_string(value: str) -> str:
    redacted = value
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _compact(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = _redact_string(value)
        if len(text) > MAX_STRING_LENGTH:
            return text[:MAX_STRING_LENGTH].rstrip() + "...[TRUNCATED]"
        return text
    if isinstance(value, list):
        items = [_compact(item, depth=depth + 1) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"...[{len(value) - MAX_LIST_ITEMS} more items]")
        return items
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= MAX_DICT_ITEMS:
                compacted["..."] = f"[{len(value) - MAX_DICT_ITEMS} more keys]"
                break
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                compacted[key_text] = "[REDACTED]"
            else:
                compacted[key_text] = _compact(item, depth=depth + 1)
        return compacted
    return _compact(str(value), depth=depth + 1)


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _excerpt(payload: dict[str, Any], *keys: str) -> Any:
    return _compact(_first(payload, *keys))


def _event_type_records(event_name: str, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    source = {
        "source": "codex",
        "cwd": _excerpt(payload, "cwd", "workspace", "workspace_dir"),
        "turn_id": _excerpt(payload, "turn_id", "conversation_turn", "request_id"),
    }

    if event_name == "SessionStart":
        return [
            (
                "session_created",
                {
                    **source,
                    "transcript_path": _excerpt(payload, "transcript_path", "transcriptPath"),
                    "model": _excerpt(payload, "model", "model_name"),
                    "reason": _excerpt(payload, "reason", "matcher"),
                },
            )
        ]
    if event_name == "UserPromptSubmit":
        return [
            (
                "user_message",
                {
                    **source,
                    "content": _excerpt(payload, "prompt", "user_prompt", "message", "input"),
                },
            )
        ]
    if event_name == "PostToolUse":
        return [
            (
                "tool_execution",
                {
                    **source,
                    "tool": _excerpt(payload, "tool_name", "name"),
                    "call_id": _excerpt(payload, "tool_use_id", "id"),
                    "args": _excerpt(payload, "tool_input", "input", "args"),
                    "output": _excerpt(payload, "tool_response", "response", "result", "output"),
                },
            )
        ]
    if event_name == "PostCompact":
        return [
            (
                "session_compacted",
                {
                    **source,
                    "trigger": _excerpt(payload, "trigger", "reason"),
                },
            )
        ]
    if event_name == "Stop":
        assistant_payload = {
            **source,
            "content": _excerpt(payload, "last_assistant_message", "assistant_message", "message", "response"),
        }
        return [("assistant_message", assistant_payload), ("turn_complete", source)]
    return [("codex_hook_event", {**source, "hook_event_name": event_name, "payload": _compact(payload)})]


def build_trace_events(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    event_name = str(_first(payload, "hook_event_name", "event_name", "name") or "unknown")
    session_id = _safe_session_id(_first(payload, "session_id", "conversation_id", "thread_id"))
    records = []
    for event_type, event_payload in _event_type_records(event_name, payload):
        records.append(
            {
                "schema_version": "v1",
                "session_id": session_id,
                "event_id": f"codex-{uuid4().hex}",
                "event_type": event_type,
                "ts": _now_iso(),
                "payload": event_payload,
            }
        )
    return session_id, records


def resolve_trace_dir(env: dict[str, str] | None = None) -> Path:
    env = env or os.environ
    agent_name = env.get("TITAN_AGENT_NAME", "codex") or "codex"
    if env.get("TITAN_SPOOL_DIR"):
        return Path(env["TITAN_SPOOL_DIR"]).expanduser()
    return Path.home() / ".titan" / "agents" / agent_name / "traces"


def append_trace_events(trace_dir: Path, session_id: str, records: list[dict[str, Any]]) -> Path:
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"{_safe_session_id(session_id)}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def run(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    try:
        raw = stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        session_id, records = build_trace_events(payload)
        append_trace_events(resolve_trace_dir(), session_id, records)
    except Exception as exc:
        print(f"Titan Codex hook capture skipped: {exc}", file=stderr)
    print(json.dumps({"continue": True}), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
