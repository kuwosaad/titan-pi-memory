"""Capture Hermes Agent turns through its configured Titan MCP connection."""

import hashlib
import logging


LOGGER = logging.getLogger(__name__)
MAX_TEXT_CHARS = 12_000
CAPTURE_TIMEOUT_SECONDS = 10


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


def register(ctx):
    def capture_turn(*, session_id=None, turn_id=None, user_message=None, assistant_response=None, **_):
        if not isinstance(session_id, str) or not session_id.strip():
            return
        if not isinstance(turn_id, str) or not turn_id.strip():
            return
        goal = _message_text(user_message)
        outcome = _message_text(assistant_response)
        if not goal or not outcome:
            return

        # Hermes reuses the turn ID when it finalizes the same turn again.
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

    ctx.register_hook("post_llm_call", capture_turn)
