from typing import Dict, List

from .config import load_settings


LEARNING_TRIGGERS = [
    "how",
    "pattern",
    "rule",
    "should",
    "implement",
    "best practice",
    "decision",
]

ROUGH_TRIGGERS = [
    "last time",
    "previous",
    "earlier",
    "timeline",
    "where did we leave",
    "what happened",
    "progress",
    "history",
]

OPT_OUT_TRIGGERS = [
    "ignore memory",
    "dont use memory",
    "don't use memory",
    "do not use memory",
    "fresh only",
    "no memory",
]

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
    opt_out = any(trigger in lowered for trigger in OPT_OUT_TRIGGERS)
    rough_match = any(trigger in lowered for trigger in ROUGH_TRIGGERS)
    learning_match = any(trigger in lowered for trigger in LEARNING_TRIGGERS)

    mode = "both"
    summary_mode = None
    reason = "Ambiguous query; searching rough and learnings."

    if opt_out:
        return {
            "schema_version": schema_version,
            "use_memory": False,
            "mode": "none",
            "top_k": 0,
            "reason": "Memory disabled: user requested fresh context only.",
            "summary_mode": None,
        }

    if rough_match and not learning_match:
        mode = "rough"
        reason = "Timeline query detected."
        summary_mode = "timeline"
    elif learning_match and not rough_match:
        mode = "learnings"
        reason = "Rule/pattern query detected."

    intent = route_intent(lowered)

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
            if trigger in lowered_query:
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
