import re
from typing import Dict

from .config import load_settings


# Only a direct instruction can disable recall. Mentions inside factual
# questions ("why did 'no memory' appear?") must remain searchable.
_QUOTED_TEXT = re.compile(r'''"[^"\n]*"|“[^”\n]*”|(?<!\w)'[^'\n]*'(?!\w)''')
_OPT_OUT = re.compile(
    r"(?:^|[.!?;]\s+)(?:please\s+)?(?:for this (?:answer|query|request)[,:]?\s+)?"
    r"(?:ignore memory|don['’]?t use memory|do not use memory|no memory|fresh only)"
    r"(?=$|[,.!?;:]|\s+(?:for|in|on|this|please|and|to)\b)",
    re.IGNORECASE,
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


INTENT_TRIGGERS = {
    "temporal": [
        "when did",
        "when was",
        "what date",
        "which day",
        "first time",
        "on what day",
        "at what time",
        "which date",
    ],
    "timeline": [
        "what happened",
        "timeline",
        "history",
        "progress",
        "last time",
        "previous",
        "earlier",
        "where did we leave",
        "what changed",
        "since",
    ],
    "explanatory": [
        "why",
        "reason",
        "because",
        "explain",
        "cause",
        "motivation",
    ],
    "pattern": [
        "pattern",
        "rule",
        "how",
        "best practice",
        "convention",
        "usually",
        "typically",
        "always",
    ],
    "decision": [
        "should",
        "implement",
        "approach",
        "plan",
        "next step",
        "recommend",
        "propose",
    ],
}


def route_query(query: str) -> Dict[str, object]:
    settings = load_settings()
    schema_version = settings.get("router_schema_version", "v2")
    top_k = settings.get("retrieval_top_k", 8)

    lowered = query.lower().strip()
    opt_out = bool(_OPT_OUT.search(_QUOTED_TEXT.sub(" ", lowered)))

    mode = "both"
    summary_mode = None
    reason = "Searching rough and learnings; explicit mode filters streams."

    if opt_out:
        return {
            "schema_version": schema_version,
            "use_memory": False,
            "mode": "none",
            "top_k": 0,
            "reason": "Memory disabled: user requested fresh context only.",
            "summary_mode": None,
        }

    intent = route_intent(lowered)
    if intent == "timeline":
        summary_mode = "timeline"

    return {
        "schema_version": schema_version,
        "use_memory": True,
        "mode": mode,
        "top_k": top_k,
        "reason": reason,
        "summary_mode": summary_mode,
        "intent": intent,
    }


def route_intent(lowered_query: str) -> str:
    hits = {}
    for intent_name, triggers in INTENT_TRIGGERS.items():
        for trigger in triggers:
            if _contains_phrase(lowered_query, trigger):
                hits[intent_name] = hits.get(intent_name, 0) + 1

    if not hits:
        return "balanced"

    if hits.get("temporal", 0) > 0 and (
        hits["temporal"] >= hits.get("timeline", 0)
        and hits["temporal"] >= hits.get("pattern", 0)
        and hits["temporal"] >= hits.get("decision", 0)
        and hits["temporal"] >= hits.get("explanatory", 0)
    ):
        return "temporal"

    if hits.get("timeline", 0) > hits.get("pattern", 0) and hits.get("timeline", 0) > hits.get("decision", 0):
        return "timeline"
    if hits.get("explanatory", 0):
        return "explanatory"
    if hits.get("pattern", 0) > hits.get("timeline", 0):
        return "pattern"
    if hits.get("decision", 0) > hits.get("timeline", 0):
        return "decision"
    return "balanced"
