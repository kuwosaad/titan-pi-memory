"""Memory classification and visibility policy.

Transport extraction produces candidate records; this module decides how a
record is classified and whether it is safe to expose as durable memory.
The extractor re-exports these functions for import compatibility.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


_LOW_SIGNAL_MARKERS = (
    "captured and stored for memory processing", "event was captured and stored",
    "message.updated event", "message part update event", "message.part.updated",
    "session.updated event", "session.created event", "session status event",
)
_LOW_SIGNAL_PATTERNS = (
    r"^the agent'?s (goal|outcome|intent phrase)\b", r"^the agent is in a conversation\b",
    r"^the assistant is in a conversation\b", r"^a user message was received\b",
    r"^an assistant message was sent\b", r"^the assistant received (a|an)\b",
    r"^the conversation (is happening|originated)\b", r"^the conversation key\b",
    r"^the trace packet\b", r"^the agent received (an )?(inbound|outbound|telegram)\b",
    r"^an inbound message\b", r"^a new session started\b", r"^the inbound message\b",
    r"^the agent memory namespace\b",
)
_TELEGRAM_METADATA_PATTERNS = (
    r"\btelegram\b", r"\bconversation key\b", r"\bmessage id\b", r"\bopenclaw-hook\b",
    r"\bbridge integration\b", r"\bnamespace\b", r"\binbound message\b", r"\baccount '?default'?\b",
)
_DURABLE_RELATIONAL_PATTERNS = (
    r"\b(we are friends|became friends|family now|always be honest|trust|remember this moment)\b",
    r"\b(friendship|relationship)\b",
)


def _contains_durable_relational_signal(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _DURABLE_RELATIONAL_PATTERNS)


def _is_low_signal_transport_text(text: str) -> bool:
    lowered = text.lower()
    if not lowered:
        return True
    if '"event_type"' in lowered and '"payload"' in lowered:
        return True
    if any(marker in lowered for marker in _LOW_SIGNAL_MARKERS):
        return True
    return any(re.search(pattern, lowered) for pattern in _LOW_SIGNAL_PATTERNS)


_SPEAKER_FOCUS_ALIASES = {
    "both": "shared",
    "mixed": "shared",
}
_SPEAKER_FOCUS_VALUES = {"user", "assistant", "shared", "system"}


def _mentions_actor(text: str, names: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.IGNORECASE)
        for name in names
        if name
    )


def _actor_has_user_signal(text: str, names: tuple[str, ...]) -> bool:
    return any(
        re.search(
            rf"(?<![\w-]){re.escape(name)}(?![\w-]).{{0,48}}\b(asked|directed|prefers|requested|wants)\b",
            text,
            re.IGNORECASE,
        )
        for name in names
        if name
    )


def classify_memory(
    memory_text: str,
    mem_type: str | None = None,
    *,
    speaker_focus: Optional[str] = None,
    user_display_name: Optional[str] = None,
    assistant_display_name: Optional[str] = None,
    user_aliases: tuple[str, ...] = (),
    assistant_aliases: tuple[str, ...] = (),
) -> Tuple[str, str]:
    lowered = memory_text.lower()
    type_lower = (mem_type or "").lower()
    focus = str(speaker_focus or "").strip().lower()
    focus = _SPEAKER_FOCUS_ALIASES.get(focus, focus)
    if focus not in _SPEAKER_FOCUS_VALUES:
        user_names = (str(user_display_name or "").strip(), *user_aliases, "the user", "user")
        assistant_names = (
            str(assistant_display_name or "").strip(),
            *assistant_aliases,
            "the assistant",
            "assistant",
        )
        mentions_user = _mentions_actor(memory_text, user_names)
        mentions_assistant = _mentions_actor(memory_text, assistant_names)
        if mentions_user and mentions_assistant and _actor_has_user_signal(memory_text, user_names):
            focus = "user"
        elif (mentions_user and mentions_assistant) or "they discussed" in lowered:
            focus = "shared"
        elif mentions_user:
            focus = "user"
        elif mentions_assistant:
            focus = "assistant"
        else:
            focus = "system"

    if type_lower in {"preference", "profile"}:
        memory_kind = "user_preference" if focus == "user" else "relationship"
    elif type_lower in {"decision", "plan", "constraint"}:
        memory_kind = "decision"
    elif type_lower in {"bug", "risk", "question"}:
        memory_kind = "issue"
    elif type_lower in {"fix", "workflow", "integration", "schema"}:
        memory_kind = "workflow"
    elif any(token in lowered for token in ("prefers", "likes", "wants", "asked")) and focus == "user":
        memory_kind = "user_preference"
    elif any(token in lowered for token in ("promised", "will remember", "should remember", "committed")):
        memory_kind = "commitment"
    elif any(token in lowered for token in ("discussed", "friends", "family", "relationship")):
        memory_kind = "relationship"
    elif any(token in lowered for token in ("implemented", "configured", "completed", "finished", "did")):
        memory_kind = "outcome"
    elif any(token in lowered for token in ("task", "todo", "investigate", "research", "build")):
        memory_kind = "task"
    elif any(token in lowered for token in ("bug", "issue", "problem", "failed", "frustration")):
        memory_kind = "issue"
    else:
        memory_kind = "user_fact" if focus == "user" else "workflow"
    return focus, memory_kind


def is_hidden_metadata_memory(memory: Dict[str, Any] | str) -> bool:
    text = memory if isinstance(memory, str) else str(memory.get("text") or "")
    lowered = text.lower().strip()
    if not lowered or _is_low_signal_transport_text(lowered):
        return True
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _TELEGRAM_METADATA_PATTERNS):
        if not _contains_durable_relational_signal(lowered) and not any(
            token in lowered for token in ("asked", "requested", "wants", "prefers", "decided", "promised", "problem", "issue", "fix")
        ):
            return True
    return False


__all__ = ["classify_memory", "is_hidden_metadata_memory"]
