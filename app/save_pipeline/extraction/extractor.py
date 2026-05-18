import json
import re
from typing import Any, Dict, List, Tuple

from .prompts import build_extract_prompt
from app.save_pipeline.extraction.adapters import ExtractionAdapter


ROUGH_HINTS = [
    "last",
    "previous",
    "earlier",
    "session",
    "timeline",
    "happened",
    "worked on",
    "progress",
    "today",
    "yesterday",
]

LOW_SIGNAL_MARKERS = (
    "captured and stored for memory processing",
    "event was captured and stored",
    "message.updated event",
    "message part update event",
    "message.part.updated",
    "session.updated event",
    "session.created event",
    "session status event",
)

LOW_SIGNAL_MEMORY_PATTERNS = (
    r"^the agent'?s (goal|outcome|intent phrase)\b",
    r"^the agent is in a conversation\b",
    r"^the assistant is in a conversation\b",
    r"^a user message was received\b",
    r"^an assistant message was sent\b",
    r"^karu received (a|an)\b",
    r"^the conversation (is happening|with karu originated)\b",
    r"^the conversation key\b",
    r"^the trace packet\b",
    r"^the agent received (an )?(inbound|outbound|telegram)\b",
    r"^an inbound message\b",
    r"^a new session started\b",
    r"^the inbound message\b",
    r"^the agent memory namespace\b",
)

MEANINGFUL_SIGNAL_PATTERNS = (
    r"\b(prefers?|wants?|asked|request(?:ed)?|needs?|plans?|decided|promised|committed|remember|important|should|must|use|uses)\b",
    r"\b(bug|issue|problem|fix|failed|failure|frustrat(?:ed|ing)|surpris(?:e|ing))\b",
    r"\b(working|implemented|configured|integrated|investigat(?:e|ed)|research(?:ed)?|dedupe|idempotent)\b",
    r"\b(Kuwo|Karu)\b",
)

SHALLOW_EXCHANGE_PATTERNS = (
    r"^(hi|hello|hey|ok|okay|thanks|thank you|cool|nice|sure|yes|no)\W*$",
    r"^(that sounds (good|great|amazing)|i can help\.?)$",
)

TRACE_METADATA_MARKERS = (
    "conversation_key",
    "intent phrase",
    "agent_memory_namespace",
    "memory capture",
    "source openclaw-hook",
    "direction inbound",
    "direction outbound",
    "account_id",
)

TRACE_GENERIC_OUTCOMES = (
    "user message in conversation with karu",
    "user message in a conversation with karu",
    "outcome: user message in conversation with karu",
)

TRACE_DURABLE_SIGNAL_PATTERNS = (
    r"\b(prefers?|wants?|asked|request(?:ed)?|needs?|plans?|decided|promised|remember|investigat(?:e|ed)|research(?:ed)?)\b",
    r"\b(bug|issue|problem|fix|restart|configure|build|implement|repository|github|titan|memory|skill|workflow)\b",
    r"https?://",
    r"/[A-Za-z0-9._/-]+",
)

TELEGRAM_METADATA_PATTERNS = (
    r"\btelegram\b",
    r"\bconversation key\b",
    r"\bmessage id\b",
    r"\bopenclaw-hook\b",
    r"\bbridge integration\b",
    r"\bnamespace\b",
    r"\binbound message\b",
    r"\baccount '?default'?\b",
)

DURABLE_RELATIONAL_PATTERNS = (
    r"\b(we are friends|became friends|family now|always be honest|trust|remember this moment)\b",
    r"\b(friendship|relationship)\b",
)

SHALLOW_RELATIONAL_PATTERNS = (
    r"\b(you'?re the best|ur the best|good job|thanks karu|thank you karu|love you|you are amazing)\b",
)


def _is_low_signal_transport_text(text: str) -> bool:
    lowered = text.lower()
    if not lowered:
        return True
    if "\"event_type\"" in lowered and "\"payload\"" in lowered:
        return True
    if any(marker in lowered for marker in LOW_SIGNAL_MARKERS):
        return True
    return any(re.search(pattern, lowered) for pattern in LOW_SIGNAL_MEMORY_PATTERNS)


def _first_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return parts[0].strip() if parts else cleaned


def _normalize_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if cleaned and cleaned[-1] not in ".!?":
        cleaned = f"{cleaned}."
    return cleaned


def _contains_meaningful_signal(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in MEANINGFUL_SIGNAL_PATTERNS)


def _contains_trace_durable_signal(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in TRACE_DURABLE_SIGNAL_PATTERNS)


def _contains_durable_relational_signal(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in DURABLE_RELATIONAL_PATTERNS)


def _contains_shallow_relational_signal(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in SHALLOW_RELATIONAL_PATTERNS)


def _is_shallow_exchange(text: str) -> bool:
    cleaned = _first_sentence(text).lower()
    return any(re.search(pattern, cleaned) for pattern in SHALLOW_EXCHANGE_PATTERNS)


def _looks_like_trace_prompt(text: str) -> bool:
    lowered = text.lower()
    return "goal:" in lowered and "thoughts:" in lowered and "tool calls:" in lowered


def _extract_trace_field(text: str, field_name: str) -> str:
    pattern = rf"{re.escape(field_name)}:\s*(.*?)(?=\n[A-Za-z ]+:\s|\Z)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def classify_memory(memory_text: str, mem_type: str | None = None) -> Tuple[str, str]:
    lowered = memory_text.lower()
    type_lower = (mem_type or "").lower()

    if "kuwo and karu" in lowered or "they discussed" in lowered:
        speaker_focus = "shared"
    elif "kuwo" in lowered or "the user" in lowered:
        speaker_focus = "kuwo"
    elif "karu" in lowered or "assistant" in lowered:
        speaker_focus = "karu"
    else:
        speaker_focus = "system"

    if type_lower in {"preference", "profile"}:
        memory_kind = "user_preference" if speaker_focus == "kuwo" else "relationship"
    elif type_lower in {"decision", "plan", "constraint"}:
        memory_kind = "decision"
    elif type_lower in {"bug", "risk", "question"}:
        memory_kind = "issue"
    elif type_lower in {"fix", "workflow", "integration", "schema"}:
        memory_kind = "workflow"
    else:
        if any(token in lowered for token in ("prefers", "likes", "wants karu to", "asked karu to")):
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
            memory_kind = "user_fact" if speaker_focus == "kuwo" else "workflow"

    return speaker_focus, memory_kind


def is_hidden_metadata_memory(memory: Dict[str, Any] | str) -> bool:
    text = memory if isinstance(memory, str) else str(memory.get("text") or "")
    lowered = text.lower().strip()
    if not lowered:
        return True
    if _is_low_signal_transport_text(lowered):
        return True
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in TELEGRAM_METADATA_PATTERNS):
        if not _contains_durable_relational_signal(lowered) and not any(
            token in lowered
            for token in ("asked", "requested", "wants", "prefers", "decided", "promised", "problem", "issue", "fix")
        ):
            return True
    return False


def assess_memory_worthiness(user_text: str, assistant_text: str) -> Dict[str, Any]:
    combined = f"{user_text}\n{assistant_text}".strip()
    lowered = combined.lower()

    if not combined:
        return {"should_extract": False, "allow_fallback": False, "skip_reason": "empty_exchange"}

    if _contains_shallow_relational_signal(combined) and not _contains_durable_relational_signal(combined):
        return {"should_extract": False, "allow_fallback": False, "skip_reason": "telegram_shallow_social"}

    if _looks_like_trace_prompt(user_text):
        goal = _extract_trace_field(user_text, "Goal").lower()
        thoughts = _extract_trace_field(user_text, "Thoughts")
        tool_calls = _extract_trace_field(user_text, "Tool Calls")
        outcome = _extract_trace_field(assistant_text, "Outcome").lower() if _looks_like_trace_prompt(assistant_text) else assistant_text.lower()
        metadata_hits = sum(marker in lowered for marker in TRACE_METADATA_MARKERS)
        generic_goal = goal in {
            "",
            "conversation",
            "a conversation",
            "to have a conversation",
            "to start a new conversation",
        } or goal.startswith("conversation:")
        tool_is_empty = tool_calls in {"", "[]"}
        durable_trace_signal = _contains_trace_durable_signal(thoughts)
        generic_outcome = any(marker in outcome for marker in TRACE_GENERIC_OUTCOMES)
        if generic_goal and tool_is_empty and generic_outcome and not durable_trace_signal:
            return {"should_extract": False, "allow_fallback": False, "skip_reason": "telegram_transport_only"}
        if generic_goal and tool_is_empty and not _contains_meaningful_signal(thoughts):
            return {"should_extract": False, "allow_fallback": False, "skip_reason": "thin_trace"}
        if metadata_hits >= 2 and not _contains_meaningful_signal(thoughts):
            return {"should_extract": False, "allow_fallback": False, "skip_reason": "telegram_metadata_only"}

    if _is_low_signal_transport_text(combined):
        return {"should_extract": False, "allow_fallback": False, "skip_reason": "transport_noise"}

    if _is_shallow_exchange(user_text) and _is_shallow_exchange(assistant_text):
        return {"should_extract": False, "allow_fallback": False, "skip_reason": "shallow_exchange"}

    meaningful = _contains_meaningful_signal(combined)
    metadata_hits = sum(marker in lowered for marker in TRACE_METADATA_MARKERS)
    if metadata_hits >= 2 and not meaningful:
        return {"should_extract": False, "allow_fallback": False, "skip_reason": "metadata_only"}

    return {
        "should_extract": meaningful or len(_first_sentence(combined)) > 40,
        "allow_fallback": meaningful and not _is_shallow_exchange(user_text),
        "skip_reason": None if meaningful or len(_first_sentence(combined)) > 40 else "failed_quality_gate",
    }


def build_safe_fallback_memories(user_text: str, assistant_text: str) -> List[dict]:
    """
    Deterministic fallback when model extraction yields no useful output.
    Produces short plain-language memories from the final user/assistant exchange.
    """
    user_line = _normalize_sentence(_first_sentence(user_text))
    assistant_line = _normalize_sentence(_first_sentence(assistant_text))
    candidates: List[dict] = []

    if user_line and not _is_shallow_exchange(user_line):
        speaker_focus, memory_kind = classify_memory(user_line)
        candidates.append(
            {
                "text": f"The user asked about {user_line[:-1].lower()}." if user_line.endswith("?") else user_line,
                "stream": "rough",
                "speaker_focus": speaker_focus,
                "memory_kind": memory_kind,
            }
        )
    if assistant_line and _contains_meaningful_signal(assistant_line) and not _is_shallow_exchange(assistant_line):
        speaker_focus, memory_kind = classify_memory(assistant_line)
        candidates.append(
            {
                "text": assistant_line,
                "stream": "rough",
                "speaker_focus": speaker_focus,
                "memory_kind": memory_kind,
            }
        )

    return sanitize_memories(candidates[:2])


def infer_stream(text: str, mem_type: str | None = None) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ROUGH_HINTS):
        return "rough"
    if (mem_type or "").lower() in {"decision", "preference", "plan"}:
        return "learnings"
    return "learnings"


def sanitize_memories(memories: List[dict]) -> List[dict]:
    cleaned = []
    seen = set()
    for mem in memories:
        text = re.sub(r"^[\-\d\.\s]+", "", str(mem.get("text", ""))).strip()
        if not text or len(text) < 6:
            continue
        if _is_low_signal_transport_text(text) or is_hidden_metadata_memory(text):
            continue
        if not _contains_meaningful_signal(text) and len(text) < 40:
            continue

        normalized_text = text.lower()
        if normalized_text in seen:
            continue

        mem_type = mem.get("type")
        stream = mem.get("stream") or infer_stream(text, mem_type)
        if stream not in {"rough", "learnings"}:
            stream = infer_stream(text, mem_type)
        speaker_focus, memory_kind = classify_memory(text, mem_type)

        cleaned.append(
            {
                "text": _normalize_sentence(text),
                "type": mem_type,
                "stream": stream,
                "source": mem.get("source"),
                "reliability": mem.get("reliability"),
                "speaker_focus": mem.get("speaker_focus") or speaker_focus,
                "memory_kind": mem.get("memory_kind") or memory_kind,
            }
        )
        seen.add(normalized_text)
    return cleaned


def fallback_memories(user_text: str, max_sentences: int = 3) -> List[dict]:
    parts = re.split(r"(?<=[.!?])\s+", user_text.strip())
    candidates = []
    for part in parts[:max_sentences]:
        candidates.append({"text": part, "stream": "rough"})
    return sanitize_memories(candidates)


def detect_source_attribution(memory_text: str, user_text: str, assistant_text: str) -> Tuple[str, float]:
    memory_lower = memory_text.lower().strip()
    user_lower = user_text.lower().strip()
    assistant_lower = assistant_text.lower().strip()

    if memory_lower in user_lower:
        return ("user", 0.9)
    if memory_lower in assistant_lower:
        return ("assistant", 0.3)

    user_words = set(user_lower.split())
    assistant_words = set(assistant_lower.split())
    memory_words = set(memory_lower.split())

    if not memory_words:
        return ("unknown", 0.5)

    user_overlap = len(memory_words & user_words) / len(memory_words)
    assistant_overlap = len(memory_words & assistant_words) / len(memory_words)

    if user_overlap > 0.7:
        return ("user", 0.9)
    if assistant_overlap > 0.7:
        return ("assistant", 0.3)
    if user_overlap > assistant_overlap:
        return ("mixed", 0.5)
    if assistant_overlap > user_overlap:
        return ("mixed", 0.5)
    return ("unknown", 0.5)


def extract_atomic_memories(user_text: str, assistant_text: str, adapter: ExtractionAdapter) -> List[dict]:
    from app.retrieval_pipeline.config import load_settings

    prompt = build_extract_prompt(user_text, assistant_text)

    content = adapter.chat(
        [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        format_hint="json",
        temperature=0.1,
    )

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return fallback_memories(user_text)

    if isinstance(data, list):
        sanitized = sanitize_memories([{"text": str(x), "stream": "rough"} for x in data])
        for mem in sanitized:
            source, reliability = detect_source_attribution(mem["text"], user_text, assistant_text)
            mem["source"] = source
            mem["reliability"] = reliability
        return sanitized

    memories = data.get("memories") if isinstance(data, dict) else None
    if not isinstance(memories, list):
        return fallback_memories(user_text)

    settings = load_settings()
    reliability_map = settings.get("source_reliability", {})

    normalized = []
    for item in memories:
        if isinstance(item, dict):
            text = item.get("text", "")
            mem_type = item.get("type")
            source = item.get("source") or "unknown"
            stream = item.get("stream") or infer_stream(text, mem_type)

            if source == "unknown":
                source, reliability = detect_source_attribution(text, user_text, assistant_text)
            else:
                reliability = reliability_map.get(source, 0.5)

            normalized.append(
                {
                    "text": text,
                    "type": mem_type,
                    "stream": stream,
                    "source": source,
                    "reliability": reliability,
                    "speaker_focus": item.get("speaker_focus"),
                    "memory_kind": item.get("memory_kind"),
                }
            )
        else:
            text = str(item)
            source, reliability = detect_source_attribution(text, user_text, assistant_text)
            normalized.append(
                {
                    "text": text,
                    "stream": infer_stream(text),
                    "source": source,
                    "reliability": reliability,
                    "speaker_focus": None,
                    "memory_kind": None,
                }
            )

    return sanitize_memories(normalized)
