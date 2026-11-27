"""Capture Hermes Agent turns through its configured Titan MCP connection."""

import hashlib
import logging
import re
from collections import OrderedDict
from threading import Lock


LOGGER = logging.getLogger(__name__)
MAX_TEXT_CHARS = 12_000
CAPTURE_TIMEOUT_SECONDS = 10
MAX_PENDING_TURNS = 64
POLICY_VETO = "blocked_by_plugin_pre_llm_call"
_PARTIAL_TRAILING_TOKEN = re.compile(r"\s\S*\Z")
_REDACTED = "[redacted]"
# Copy of Titan's credential patterns (app/storage/traces.py). The plugin is
# installed on its own and cannot import Titan, so they are duplicated here and
# kept in sync by tests/test_hermes_plugin.py.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk[-_][A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bntn_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bsecret_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}\b", re.IGNORECASE),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(
        r"\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*\s*=\s*(?:[^\s'\"]+|'[^']*'|\"[^\"]*\")",
        re.IGNORECASE,
    ),
)


def _message_text(message):
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, list):
        return ""

    parts = []
    for block in message:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and (text := text.strip()):
            parts.append(text)
    return "\n".join(parts)


def _redact(text):
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def _bounded(text):
    """Redact recognized credentials, then cap text before it leaves Hermes.

    Redaction runs on the full text. A cut can leave a fragment Titan's
    patterns no longer match (a key block without its END line, a quoted
    value without its closing quote, a token shorter than its minimum), so
    cutting first would send such fragments. A word split by the limit is
    dropped whole; text with no whitespace keeps the hard cut.
    """
    text = _redact(text)
    if len(text) <= MAX_TEXT_CHARS:
        return text
    clipped = text[:MAX_TEXT_CHARS]
    if not text[MAX_TEXT_CHARS].isspace():
        partial = _PARTIAL_TRAILING_TOKEN.search(clipped)
        if partial:
            clipped = clipped[: partial.start()]
    return clipped.rstrip()


def register(ctx):
    pending = OrderedDict()
    pending_lock = Lock()

    def stage_turn(*, session_id=None, turn_id=None, user_message=None, assistant_response=None, **_):
        if not isinstance(session_id, str) or not session_id.strip():
            return
        if not isinstance(turn_id, str) or not turn_id.strip():
            return
        goal = _bounded(_message_text(user_message))
        outcome = _bounded(_message_text(assistant_response))
        if not goal or not outcome:
            return

        with pending_lock:
            key = (session_id, turn_id)
            pending[key] = (goal, outcome)
            pending.move_to_end(key)
            if len(pending) > MAX_PENDING_TURNS:
                pending.popitem(last=False)

    def finish_turn(
        *, session_id=None, turn_id=None, completed=None, failed=None,
        interrupted=None, turn_exit_reason=None, **_
    ):
        if not isinstance(session_id, str) or not isinstance(turn_id, str):
            return
        with pending_lock:
            messages = pending.pop((session_id, turn_id), None)
        if (
            messages is None
            or completed is not True
            or failed is not False
            or interrupted is not False
            or not isinstance(turn_exit_reason, str)
            or not turn_exit_reason
            or turn_exit_reason == POLICY_VETO
        ):
            return

        goal, outcome = messages
        digest = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
        packet = {
            "session_id": session_id,
            "event_id": f"hermes:{digest}",
            "goal": goal,
            "outcome": outcome,
            "max_text_chars": MAX_TEXT_CHARS,
            "context": {"source": "hermes"},
        }
        try:
            result = ctx.call_mcp("titan-memory", "store_trace_packet", packet, timeout=CAPTURE_TIMEOUT_SECONDS)
        except PermissionError:
            LOGGER.warning("Titan capture needs titan-memory in the plugin MCP allowlist.")
        except Exception:
            LOGGER.warning("Titan capture could not reach the titan-memory MCP server.")
        else:
            if not result.get("ok"):
                LOGGER.warning("The titan-memory MCP server did not confirm capture.")

    ctx.register_hook("post_llm_call", stage_turn)
    ctx.register_hook("on_session_end", finish_turn)
