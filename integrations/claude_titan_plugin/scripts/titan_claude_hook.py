#!/usr/bin/env python3
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable


DEFAULT_AGENT_NAME = "claude-code"
SOURCE = "claude-code"
TEXT_LIMIT = 2000
TOOL_LIMIT = 1000

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*)[^\s'\"`]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{10,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z")


def _redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def compact_text(value: Any, limit: int = TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", _as_text(value)).strip()
    text = _redact_text(text)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _first_string(payload: dict[str, Any], names: Iterable[str]) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def resolve_agent_name(env: dict[str, str] | None = None) -> str:
    env = env or os.environ
    for key in ("TITAN_AGENT_NAME", "CLAUDE_PLUGIN_OPTION_agent_name"):
        value = env.get(key, "").strip()
        if value:
            return value
    return DEFAULT_AGENT_NAME


def resolve_trace_dir(agent_name: str, env: dict[str, str] | None = None) -> Path:
    env = env or os.environ
    if env.get("TITAN_SPOOL_DIR"):
        return Path(env["TITAN_SPOOL_DIR"]).expanduser()
    return Path.home() / ".titan" / "agents" / agent_name / "traces"


def resolve_session_id(payload: dict[str, Any]) -> str:
    candidates: list[Any] = [
        payload.get("session_id"),
        payload.get("sessionId"),
        payload.get("sessionID"),
        _nested(payload, "session", "id"),
        _nested(payload, "conversation", "id"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "default"


def _safe_session_filename(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._")
    return safe or "default"


def _event_base(session_id: str, event_type: str, event_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "ts": _now_iso(),
        "schema_version": "v1",
        "payload": event_payload,
    }


def _tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
    response = payload.get("tool_response", payload.get("response", payload.get("result", payload.get("output"))))
    tool_input = payload.get("tool_input", payload.get("input", payload.get("arguments", payload.get("args"))))
    error = payload.get("error") or _nested(payload, "tool_response", "error") or _nested(payload, "response", "error")
    return {
        "source": SOURCE,
        "raw_type": payload.get("hook_event_name", "PostToolUse"),
        "tool": payload.get("tool_name") or payload.get("toolName") or tool.get("name"),
        "call_id": payload.get("tool_use_id") or payload.get("toolUseID") or payload.get("call_id") or tool.get("id"),
        "args": compact_text(tool_input, TOOL_LIMIT) if tool_input is not None else None,
        "output": compact_text(response, TOOL_LIMIT) if response is not None else None,
        "error": compact_text(error, 500) if error else None,
    }


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None and value != ""}


def build_trace_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    event_name = str(payload.get("hook_event_name") or payload.get("event_name") or payload.get("event") or "unknown")
    session_id = resolve_session_id(payload)

    if event_name == "SessionStart":
        event_payload = _clean_payload(
            {
                "source": SOURCE,
                "raw_type": event_name,
                "cwd": _first_string(payload, ("cwd", "workspace_dir", "project_dir")),
                "transcript_path": _first_string(payload, ("transcript_path", "transcriptPath")),
                "model": _first_string(payload, ("model", "model_name")),
            }
        )
        return [_event_base(session_id, "session_created", event_payload)]

    if event_name == "UserPromptSubmit":
        prompt = payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or _nested(payload, "message", "content")
        return [
            _event_base(
                session_id,
                "user_message",
                _clean_payload({"source": SOURCE, "raw_type": event_name, "content": compact_text(prompt)}),
            )
        ]

    if event_name == "PostToolUse":
        return [_event_base(session_id, "tool_execution", _clean_payload(_tool_payload(payload)))]

    if event_name == "PostCompact":
        context = payload.get("context") or payload.get("summary") or payload.get("trigger")
        return [
            _event_base(
                session_id,
                "session_compacted",
                _clean_payload({"source": SOURCE, "raw_type": event_name, "context": compact_text(context)}),
            )
        ]

    if event_name == "Stop":
        events: list[dict[str, Any]] = []
        assistant = payload.get("assistant_message") or payload.get("message") or payload.get("response") or _nested(payload, "message", "content")
        if assistant:
            events.append(
                _event_base(
                    session_id,
                    "assistant_message",
                    _clean_payload({"source": SOURCE, "raw_type": event_name, "content": compact_text(assistant)}),
                )
            )
        events.append(_event_base(session_id, "turn_complete", {"source": SOURCE, "raw_type": event_name}))
        return events

    if event_name == "SessionEnd":
        reason = payload.get("reason") or payload.get("exit_reason")
        return [
            _event_base(
                session_id,
                "session_closed",
                _clean_payload({"source": SOURCE, "raw_type": event_name, "reason": compact_text(reason, 500)}),
            )
        ]

    return [
        _event_base(
            session_id,
            "claude_hook_event",
            _clean_payload({"source": SOURCE, "raw_type": event_name, "summary": compact_text(payload)}),
        )
    ]


def append_trace_events(events: list[dict[str, Any]], trace_dir: Path) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    for event in events:
        session_id = str(event.get("session_id") or "default")
        target = trace_dir / f"{_safe_session_filename(session_id)}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


def read_stdin_payload(stdin: Any = sys.stdin) -> dict[str, Any]:
    raw = stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def main(stdin: Any = sys.stdin, stdout: Any = sys.stdout) -> int:
    try:
        payload = read_stdin_payload(stdin)
        agent_name = resolve_agent_name()
        trace_dir = resolve_trace_dir(agent_name)
        append_trace_events(build_trace_events(payload), trace_dir)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
