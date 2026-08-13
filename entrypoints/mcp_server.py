from __future__ import annotations

import json
import sqlite3
import sys
import os
import threading
from pathlib import Path
from typing import Any, Optional

import yaml
from mcp.server import FastMCP
from pydantic import ValidationError

ROOT_DIR = Path(__file__).resolve().parent.parent
_default_home = ROOT_DIR if (ROOT_DIR / ".git").exists() else (Path.home() / ".titan-memory")
sys.path.insert(0, str(ROOT_DIR))

from app.runtime.context import get_runtime_context, hydrate_process_environment

_RUNTIME_CONTEXT = get_runtime_context(root_dir=ROOT_DIR, default_home=_default_home)
hydrate_process_environment(_RUNTIME_CONTEXT)
TITAN_HOME = _RUNTIME_CONTEXT.titan_home
os.environ.setdefault("TITAN_BASE_DIR", str(_RUNTIME_CONTEXT.base_dir))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key, value)


from app.graph.clusters import inspect_memory_clusters
from app.graph.cortex_analysis import analyze_memory_clusters
from app.patterns import api as patterns_api
from app.patterns.errors import PatternError
from app.patterns.bundle import export_pattern_bundle, import_pattern_bundle
from app.save_pipeline.pipeline import get_scene_context as build_scene_context, handle_trace_packet, ingest_trace_event, retrieve_memory_brief
from app.storage.memories import get_memory_count, get_recent_memories as load_recent_memories, get_memory_repository, get_lnn_state_repository
from app.storage.models import TraceEvent, TracePacketRequest, TraceToolCall
from app.save_pipeline.auto_ingest import _auto_ingest_loop
from app.save_pipeline.dedup_worker import start_dedup_worker
from app.save_pipeline.lnn_tick_worker import start_lnn_tick_worker


server = FastMCP("titan-memory")


def _mcp_pattern_call(fn, *args, **kwargs) -> dict:
    """Translate framework-neutral Pattern errors into stable MCP payloads."""

    try:
        return fn(*args, **kwargs)
    except PatternError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return {**detail, "status_code": exc.status_code}


def _mcp_bad_request(exc: Exception) -> dict:
    """Keep malformed pattern tool arguments on the stable MCP error shape."""

    return {"error": str(exc), "status_code": 400}


def _normalize_tool_calls(tool_calls: Optional[list[dict]]) -> list[TraceToolCall]:
    if not tool_calls:
        return []
    normalized = []
    for call in tool_calls:
        name = call.get("name") or "unknown"
        raw_args = call.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        result = call.get("result")
        normalized.append(TraceToolCall(name=name, args=dict(args), result=result))
    return normalized


def _parse_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError("expected a JSON list")
            return [str(item) for item in loaded if str(item)]
        return [item for item in (part.strip() for part in raw.replace("\n", ",").split(",")) if item]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _parse_evidence_json(value: Any) -> list[dict]:
    if value is None or value == "":
        return []
    loaded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(loaded, list):
        raise ValueError("evidence_json must be a JSON list")
    if not all(isinstance(item, dict) for item in loaded):
        raise ValueError("evidence_json items must be JSON objects")
    return loaded


@server.tool()
async def store_trace_packet(
    goal: str,
    thoughts: Optional[str] = None,
    tool_calls: Optional[list[dict]] = None,
    outcome: str = "",
    session_id: Optional[str] = None,
    event_id: Optional[str] = None,
    save_intent: Optional[bool] = None,
    intent_phrase: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict:
    req = TracePacketRequest(
        goal=goal,
        thoughts=thoughts,
        tool_calls=_normalize_tool_calls(tool_calls),
        outcome=outcome,
        session_id=session_id,
        event_id=event_id,
        save_intent=save_intent,
        intent_phrase=intent_phrase,
        context=context,
    )
    return handle_trace_packet(req)


@server.tool()
async def store_trace_event(
    session_id: str,
    event_id: str,
    event_type: str,
    payload: Optional[dict] = None,
    ts: Optional[str] = None,
    schema_version: str = "v1",
) -> dict:
    req = TraceEvent(
        session_id=session_id,
        event_id=event_id,
        event_type=event_type,
        payload=payload or {},
        ts=ts,
        schema_version=schema_version,
    )
    return ingest_trace_event(req)


def _serialize_memory(mem: Any) -> dict:
    if hasattr(mem, "id"):
        return {
            "id": mem.id,
            "text": mem.text,
            "type": mem.type,
            "stream": mem.stream,
            "session_id": mem.session_id,
            "turn": mem.turn,
            "scene_id": mem.scene_id,
            "source_type": mem.source_type,
            "source_reliability": mem.source_reliability,
            "verification_status": mem.verification_status,
            "ts": mem.ts,
            "source_event_ids": mem.source_event_ids,
        }
    return {
        "id": mem.get("id"),
        "text": mem.get("text"),
        "type": mem.get("type"),
        "stream": mem.get("stream"),
        "session_id": mem.get("session_id"),
        "turn": mem.get("turn"),
        "scene_id": mem.get("scene_id"),
        "source_type": mem.get("source_type"),
        "source_reliability": mem.get("source_reliability"),
        "verification_status": mem.get("verification_status"),
        "ts": mem.get("ts"),
        "source_event_ids": mem.get("source_event_ids") or [],
    }


@server.tool()
async def query_memories(
    session_id: Optional[str] = None,
    limit: int = 20,
    query: Optional[str] = None,
    mode: Optional[str] = None,
) -> dict:
    if query:
        payload = retrieve_memory_brief(
            query=query,
            session_id=session_id,
            mode=mode,
            limit=limit,
            include_scenes=False,
        )
        payload["memories"] = [_serialize_memory(mem) for mem in payload.get("memories", [])]
        return payload

    records = load_recent_memories(limit=limit, session_id=session_id)
    memories = [_serialize_memory(mem) for mem in records]
    return {"count": len(memories), "memories": memories}


@server.tool()
async def get_recent_memories(session_id: Optional[str] = None, limit: int = 20) -> dict:
    records = load_recent_memories(limit=limit, session_id=session_id)
    memories = [_serialize_memory(mem) for mem in records]
    return {"count": len(memories), "memories": memories}


def _current_titan_home() -> Path:
    return _RUNTIME_CONTEXT.titan_home


def _read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _count_agent_namespace_memories(agent_name: str) -> int:
    namespace = Path.home() / ".titan" / "agents" / agent_name
    memories_dir = namespace / "memories"
    sqlite_path = memories_dir / "memory_store.db"
    if sqlite_path.exists():
        try:
            with sqlite3.connect(sqlite_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
                return int(row[0] or 0) if row else 0
        except Exception:
            pass

    json_path = memories_dir / "memories.json"
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            return len(payload) if isinstance(payload, list) else 0
        except Exception:
            return 0
    return 0


def _cross_agent_memory_status(agent_name: str, current_memory_count: int) -> dict:
    other_agents = []
    for other_agent in ("pi", "claude-code", "aider", "opencode"):
        if other_agent == agent_name:
            continue
        count = _count_agent_namespace_memories(other_agent)
        if count > 0:
            other_agents.append({"agent": other_agent, "memory_count": count})

    note = None
    if agent_name == "codex" and current_memory_count == 0:
        pi_count = next((item["memory_count"] for item in other_agents if item["agent"] == "pi"), 0)
        if pi_count:
            note = (
                "Codex memory is empty, but Pi has Titan memories. "
                "Default recall remains scoped to Codex; ask explicitly to search Pi memories or run memory-sync."
            )

    return {
        "default_scope": agent_name,
        "other_agents_with_memories": other_agents,
        "note": note,
    }


def _provider_key_status(agent_home: Path) -> dict:
    env = {
        **_read_env_values(Path.home() / ".titan" / ".env"),
        **_read_env_values(agent_home / ".env"),
        **os.environ,
    }
    config_paths = {
        "extraction": Path(env.get("TITAN_EXTRACTION_CONFIG_PATH") or ROOT_DIR / "config" / "extraction_models.yaml"),
        "embedding": Path(env.get("TITAN_EMBEDDING_CONFIG_PATH") or ROOT_DIR / "config" / "embedding_models.yaml"),
    }
    backends: dict[str, Optional[str]] = {}
    required_envs: list[str] = []
    missing_envs: list[str] = []

    for config_name, path in config_paths.items():
        cfg = _load_yaml(path.expanduser())
        current = cfg.get("current")
        backends[config_name] = current if isinstance(current, str) else None
        block = cfg.get(current) if isinstance(current, str) else None
        api_key_env = block.get("api_key_env") if isinstance(block, dict) else None
        if not isinstance(api_key_env, str) or not api_key_env:
            continue
        required_envs.append(api_key_env)
        if not env.get(api_key_env):
            missing_envs.append(api_key_env)

    required_envs = sorted(set(required_envs))
    missing_envs = sorted(set(missing_envs))
    return {
        "ok": len(missing_envs) == 0,
        "required_envs": required_envs,
        "missing_envs": missing_envs,
        "backends": backends,
        "config_paths": {name: str(path.expanduser()) for name, path in config_paths.items()},
    }


@server.tool()
async def doctor() -> dict:
    agent_name = os.getenv("TITAN_AGENT_NAME", "codex")
    agent_home = _current_titan_home()
    titan_home = Path.home() / ".titan"
    spool_dir_env = os.getenv("TITAN_SPOOL_DIR")
    if spool_dir_env:
        trace_dir = Path(spool_dir_env).expanduser()
    else:
        trace_dir = Path.home() / ".titan" / "agents" / agent_name / "traces"
    trace_files = sorted(trace_dir.glob("*.jsonl")) if trace_dir.exists() else []
    tools = await server.list_tools()
    ingest_interval = float(os.getenv("TITAN_AUTO_INGEST_INTERVAL_SECONDS", "3"))
    config_files = {
        "settings": ROOT_DIR / "config" / "settings.yaml",
        "extraction_models": ROOT_DIR / "config" / "extraction_models.yaml",
        "embedding_models": ROOT_DIR / "config" / "embedding_models.yaml",
    }
    required_config_files = {name: path.exists() for name, path in config_files.items()}
    provider_keys = _provider_key_status(agent_home)
    memory_count = get_memory_count()
    memory_repository = get_memory_repository()
    lnn_supported = get_lnn_state_repository() is not None

    return {
        "agent_name": agent_name,
        "agent_home": str(agent_home),
        "agent_namespace": str(Path.home() / ".titan" / "agents" / agent_name),
        "titan_home": str(titan_home),
        "trace_dir": str(trace_dir),
        "trace_dir_exists": trace_dir.exists(),
        "trace_file_count": len(trace_files),
        "memory_count": memory_count,
        "memory_backend": str(_RUNTIME_CONTEXT.memory_backend),
        "memory_capabilities": {
            "memory_store": True,
            "lnn_state_store": lnn_supported,
            "lnn_status": "enabled" if lnn_supported else "unsupported for selected backend",
            "adapter": memory_repository.__class__.__name__,
        },
        "cross_agent_memory": _cross_agent_memory_status(agent_name, memory_count),
        "mcp_tool_count": len(tools),
        "mcp_tools": [tool.name for tool in tools],
        "auto_ingest": {
            "starts_with_mcp_server": True,
            "spool_dir": str(Path(os.getenv("TITAN_SPOOL_DIR", str(TITAN_HOME / "traces"))).expanduser()),
            "interval_seconds": ingest_interval,
        },
        "required_config_files": required_config_files,
        "provider_keys": provider_keys,
        "recent_trace_files_exist": len(trace_files) > 0,
        "recent_trace_files": [path.name for path in trace_files[-5:]],
    }


@server.tool()
async def inspect_clusters(
    session_id: Optional[str] = None,
    limit: int = 0,
    cluster_id: Optional[int] = None,
    detail_limit: int = 12,
) -> dict:
    return inspect_memory_clusters(session_id=session_id, limit=limit, cluster_id=cluster_id, detail_limit=detail_limit)


@server.tool()
async def analyze_clusters(
    cluster_ids: str,
    session_id: Optional[str] = None,
    limit: int = 0,
    question: Optional[str] = None,
    detail_limit: int = 8,
) -> dict:
    return analyze_memory_clusters(cluster_ids=cluster_ids, session_id=session_id, limit=limit, question=question, detail_limit=detail_limit)


@server.tool()
async def patterns_status() -> dict:
    return _mcp_pattern_call(patterns_api.get_pattern_status)


@server.tool()
async def patterns_list(status: Optional[str] = None, scope: Optional[str] = None, limit: int = 50) -> dict:
    return _mcp_pattern_call(patterns_api.list_patterns, status=status, scope=scope, limit=limit)


@server.tool()
async def pattern_get(pattern_id: str) -> dict:
    return _mcp_pattern_call(patterns_api.get_pattern, pattern_id)


@server.tool()
async def pattern_create(
    title: str,
    summary: str,
    recommended_behavior: str,
    kind: str = "other",
    scope: str = "user",
    status: str = "candidate",
    trigger_terms: Optional[str] = None,
    evidence_json: Optional[str] = None,
    confidence: float = 0.0,
    applies_when: str = "",
    does_not_apply_when: str = "",
    actionability: float = 0.0,
    retrieval_value: float = 0.0,
    canonical_key: Optional[str] = None,
    mined_run_id: Optional[str] = None,
    last_refreshed_at: Optional[str] = None,
    last_applied_at: Optional[str] = None,
    source: str = "agent",
) -> dict:
    try:
        req = patterns_api.PatternCreateRequest(
            title=title,
            kind=kind,
            scope=scope,
            status=status,
            summary=summary,
            recommended_behavior=recommended_behavior,
            trigger_terms=_parse_string_list(trigger_terms),
            evidence=_parse_evidence_json(evidence_json),
            confidence=confidence,
            applies_when=applies_when,
            does_not_apply_when=does_not_apply_when,
            actionability=actionability,
            retrieval_value=retrieval_value,
            canonical_key=canonical_key,
            mined_run_id=mined_run_id,
            last_refreshed_at=last_refreshed_at,
            last_applied_at=last_applied_at,
            source=source,
        )
    except (ValueError, ValidationError) as exc:
        return _mcp_bad_request(exc)
    return _mcp_pattern_call(patterns_api.create_pattern, req)


@server.tool()
async def pattern_accept(pattern_id: str) -> dict:
    return _mcp_pattern_call(patterns_api.accept_pattern, pattern_id)


@server.tool()
async def pattern_reject(pattern_id: str) -> dict:
    return _mcp_pattern_call(patterns_api.reject_pattern, pattern_id)


@server.tool()
async def patterns_evidence_packet(
    batch_size: Optional[int] = None,
    context_limit: Optional[int] = None,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    packet_type: Optional[str] = None,
    processor_version: Optional[str] = None,
    processor_config_hash: Optional[str] = None,
) -> dict:
    req = patterns_api.PatternEvidencePacketRequest(
        batch_size=batch_size,
        context_limit=context_limit,
        session_id=session_id,
        mode=mode,
        packet_type=packet_type,
        processor_version=processor_version,
        processor_config_hash=processor_config_hash,
    )
    return _mcp_pattern_call(patterns_api.get_evidence_packet, req)


@server.tool()
async def patterns_mark_processed(
    memory_ids: str,
    run_id: Optional[str] = None,
    status: str = "processed",
    pattern_ids: Optional[str] = None,
    error: Optional[str] = None,
    mode: str = "incremental",
    processor_version: Optional[str] = None,
    processor_config_hash: Optional[str] = None,
) -> dict:
    try:
        req = patterns_api.PatternMarkProcessedRequest(
            memory_ids=_parse_string_list(memory_ids),
            run_id=run_id,
            status=status,
            pattern_ids=_parse_string_list(pattern_ids),
            error=error,
            mode=mode,
            processor_version=processor_version,
            processor_config_hash=processor_config_hash,
        )
    except (ValueError, ValidationError) as exc:
        return _mcp_bad_request(exc)
    return _mcp_pattern_call(patterns_api.mark_processed, req)


@server.tool()
async def patterns_export_bundle(
    path: Optional[str] = None,
    include_candidates: bool = False,
    statuses: Optional[str] = None,
    scopes: Optional[str] = None,
    include_memory_summaries: bool = True,
    include_progress: bool = True,
    limit: int = 500,
) -> dict:
    selected_statuses = _parse_string_list(statuses) or ["accepted"]
    if include_candidates and "candidate" not in selected_statuses:
        selected_statuses.append("candidate")
    bundle = export_pattern_bundle(
        statuses=selected_statuses,
        scopes=_parse_string_list(scopes),
        include_memory_summaries=include_memory_summaries,
        include_progress=include_progress,
        limit=limit,
    )
    if not path:
        return bundle

    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": str(output_path),
        "schema": bundle.get("schema"),
        "patterns": len(bundle.get("patterns", [])),
        "evidence": len(bundle.get("evidence", [])),
    }


@server.tool()
async def patterns_import_bundle(path: str, overwrite: bool = False, mark_progress: bool = True) -> dict:
    input_path = Path(path).expanduser()
    bundle = json.loads(input_path.read_text(encoding="utf-8"))
    return import_pattern_bundle(bundle, overwrite=overwrite, import_progress=mark_progress)


async def get_scene_context(scene_id: str) -> dict:
    return build_scene_context(scene_id)


server.tool()(get_scene_context)


def run() -> None:
    spool_dir = _RUNTIME_CONTEXT.trace_dir
    ingest_interval = float(os.getenv("TITAN_AUTO_INGEST_INTERVAL_SECONDS", "3"))
    ing_stop = threading.Event()
    threading.Thread(
        target=_auto_ingest_loop,
        args=(ing_stop, spool_dir, ingest_interval),
        daemon=True,
        name="titan-auto-ingest",
    ).start()

    dedup_stop = threading.Event()
    start_dedup_worker(dedup_stop)

    from app.retrieval_pipeline.config import load_settings
    settings = load_settings()
    lnn_stop = threading.Event()
    if settings.get("lnn", {}).get("enabled") and settings.get("lnn", {}).get("tick_enabled", True):
        tick_interval = float(settings.get("lnn", {}).get("decay_tick_seconds", 60.0))
        tau_disuse = float(settings.get("lnn", {}).get("tau_disuse_decay", 0.01))
        weight_decay = float(settings.get("lnn", {}).get("weight_decay", 0.001))
        start_lnn_tick_worker(lnn_stop, interval_seconds=tick_interval, tau_disuse_decay=tau_disuse, weight_decay=weight_decay)
    try:
        server.run("stdio")
    finally:
        ing_stop.set()
        dedup_stop.set()
        lnn_stop.set()


if __name__ == "__main__":
    run()
