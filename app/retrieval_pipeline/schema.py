ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "use_memory": {"type": "boolean"},
        "mode": {"type": "string", "enum": ["none", "rough", "learnings", "both"]},
        "top_k": {"type": "integer"},
        "reason": {"type": "string"},
        "summary_mode": {"type": ["string", "null"]},
    },
    "required": ["schema_version", "use_memory", "mode", "top_k", "reason", "summary_mode"],
}
