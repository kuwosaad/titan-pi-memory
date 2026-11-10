"""Memory classification and visibility policy.

Transport extraction produces candidate records; this module decides how a
record is classified and whether it is safe to expose as durable memory.
The extractor re-exports these functions for import compatibility.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


# Match complete transport boilerplate, never words that can also name a real
# project or integration. A sentence containing Telegram is not transport noise.
_TRANSPORT_PATTERNS = (
    r"a message\.updated event was captured and stored for memory processing[.!]?",
    r"a user message was received in a conversation with the assistant[.!]?",
    r"(?:the )?(?:event|message) was captured and stored(?: for memory processing)?[.!]?",
    r"(?:message\.updated|message part update|message\.part\.updated|session\.updated|session\.created|session status) event[.!]?",
    r"the agent'?s goal is to have a conversation(?: with [\w'-]+(?: [\w'-]+){0,2})?[.!]?",
    r"(?:a user message was received|an assistant message was sent|a new session started)[.!]?",
    r"[\w -]+ received (?:a |an )?(?:inbound |outbound )?telegram message from user [\w-]+, message id [\w-]+, via (?:the )?[\w:@/-]+(?: integration)?[.!]?",
)


def _is_low_signal_transport_text(text: str) -> bool:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return True
    return any(re.fullmatch(pattern, cleaned, re.IGNORECASE) for pattern in _TRANSPORT_PATTERNS)


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
    return _is_low_signal_transport_text(text)


__all__ = ["classify_memory", "is_hidden_metadata_memory"]
