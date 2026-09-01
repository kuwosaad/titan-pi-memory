from typing import Optional

import os
import time as _time

from fastapi import APIRouter
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse

from app.save_pipeline.pipeline import (
    get_scene_context,
    get_pipeline_debug_status,
    handle_trace_packet,
    ingest_spool_session,
    ingest_trace_event,
    retrieve_memory_brief,
)
from app.save_pipeline.extraction.extractor import is_hidden_metadata_memory
from app.graph.builder import build_graph
from app.graph.clusters import inspect_memory_clusters
from app.graph.cortex_analysis import analyze_memory_clusters
from app.storage.memories import get_memory_count, get_recent_memories
from app.storage.models import TraceEvent, TracePacketRequest
from app.storage.sessions import ensure_dirs
from app.patterns.bundle import export_pattern_bundle, import_pattern_bundle
from app.patterns.graph import build_pattern_graph, build_pattern_graph_data
from app.patterns.api import (
    PatternCreateRequest,
    PatternApplicationCreateRequest,
    PatternApplicationOutcomeRequest,
    PatternEvidencePacketRequest,
    PatternMarkProcessedRequest,
    accept_pattern as accept_pattern_impl,
    create_pattern as create_pattern_impl,
    get_evidence_packet as get_evidence_packet_impl,
    get_pattern as get_pattern_impl,
    get_pattern_status as get_pattern_status_impl,
    get_pattern_capabilities as get_pattern_capabilities_impl,
    list_patterns as list_patterns_impl,
    mark_processed as mark_pattern_processed_impl,
    reject_pattern as reject_pattern_impl,
    restore_pattern as restore_pattern_impl,
    record_pattern_application as record_pattern_application_impl,
    update_pattern_application as update_pattern_application_impl,
    list_pattern_applications as list_pattern_applications_impl,
)


router = APIRouter()
_STARTED_AT = _time.time()


class PatternBundleExportRequest(BaseModel):
    statuses: list[str] = Field(default_factory=lambda: ["accepted"])
    scopes: list[str] = Field(default_factory=list)
    include_memory_summaries: bool = True
    include_progress: bool = True
    limit: int = 500


class PatternBundleImportRequest(BaseModel):
    bundle: dict
    overwrite: bool = False
    import_progress: bool = True


@router.get("/health")
def health() -> dict:
    ensure_dirs()
    return {"status": "ok"}


@router.get("/api/runtime")
def runtime() -> dict:
    from pathlib import Path

    from app.runtime.context import adapter_defaults_from_environment, get_runtime_context
    from app.retrieval_pipeline.config import load_settings
    from app.storage.memories import get_memory_count, get_memory_repository

    settings = load_settings()
    context = get_runtime_context(
        root_dir=Path(__file__).resolve().parents[2],
        adapter_defaults=adapter_defaults_from_environment(),
    )
    memory_repository = get_memory_repository()
    capabilities = getattr(memory_repository, "capabilities", {})
    if callable(capabilities):
        capabilities = capabilities()
    if not isinstance(capabilities, dict):
        capabilities = {}

    return {
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "port": int(os.getenv("TITAN_PORT", settings.get("port", 8000))),
        "recency_default": settings.get("retrieval_recency_days"),
        "memory_count": get_memory_count(),
        "agent_name": context.agent_name,
        "titan_home": str(context.titan_home),
        "base_dir": str(context.base_dir),
        "trace_dir": str(context.trace_dir),
        "memory_backend": context.memory_backend,
        "memory_capabilities": {
            **capabilities,
            "adapter": memory_repository.__class__.__name__,
            "lnn_status": (
                "enabled"
                if capabilities.get("lnn_state_store")
                else "unsupported for selected backend"
            ),
        },
    }


@router.get("/graph")
def graph(session_id: Optional[str] = None) -> HTMLResponse:
    html_content = build_graph(session_id=session_id)
    return HTMLResponse(content=html_content)


@router.get("/pattern-graph")
def pattern_graph(limit: int = 500) -> HTMLResponse:
    html_content = build_pattern_graph(limit=limit)
    return HTMLResponse(content=html_content)


@router.get("/api/clusters/analyze")
def analyze_clusters(
    cluster_ids: str,
    session_id: Optional[str] = None,
    limit: int = 0,
    question: Optional[str] = None,
    detail_limit: int = 8,
) -> dict:
    ensure_dirs()
    return analyze_memory_clusters(
        cluster_ids=cluster_ids,
        session_id=session_id,
        limit=limit,
        question=question,
        detail_limit=detail_limit,
    )


@router.get("/api/clusters")
def get_clusters(
    session_id: Optional[str] = None,
    limit: int = 0,
    cluster_id: Optional[int] = None,
    detail_limit: int = 12,
) -> dict:
    ensure_dirs()
    return inspect_memory_clusters(
        session_id=session_id,
        limit=limit,
        cluster_id=cluster_id,
        detail_limit=detail_limit,
    )


@router.get("/api/memories")
def get_memories(session_id: Optional[str] = None, limit: int = 8) -> dict:
    ensure_dirs()
    total_count = get_memory_count(session_id=session_id)
    serialized = []
    for mem in get_recent_memories(limit=limit * 4, session_id=session_id):
        payload = mem.model_dump() if hasattr(mem, "model_dump") else dict(mem)
        if is_hidden_metadata_memory(payload):
            continue
        serialized.append(payload)
        if len(serialized) >= limit:
            break
    return {
        "memories": serialized,
        "count": len(serialized),
        "total": total_count,
    }


@router.get("/api/patterns/status")
def patterns_status() -> dict:
    ensure_dirs()
    return get_pattern_status_impl()


@router.get("/api/patterns/graph")
def patterns_graph_data(limit: int = 500) -> dict:
    ensure_dirs()
    return build_pattern_graph_data(limit=limit)


@router.get("/api/patterns/capabilities")
def patterns_capabilities() -> dict:
    ensure_dirs()
    return get_pattern_capabilities_impl()


@router.post("/api/patterns/bundle/export")
def patterns_bundle_export(req: PatternBundleExportRequest) -> dict:
    ensure_dirs()
    try:
        return export_pattern_bundle(
            statuses=req.statuses,
            scopes=req.scopes,
            include_memory_summaries=req.include_memory_summaries,
            include_progress=req.include_progress,
            limit=req.limit,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/api/patterns/bundle/import")
def patterns_bundle_import(req: PatternBundleImportRequest) -> dict:
    ensure_dirs()
    try:
        return import_pattern_bundle(req.bundle, overwrite=req.overwrite, import_progress=req.import_progress)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.get("/api/patterns")
def patterns_list(status: Optional[str] = None, scope: Optional[str] = None, limit: int = 50) -> dict:
    ensure_dirs()
    return list_patterns_impl(status=status, scope=scope, limit=limit)


@router.get("/api/patterns/{pattern_id}")
def pattern_get(pattern_id: str) -> dict:
    ensure_dirs()
    return get_pattern_impl(pattern_id)


@router.post("/api/patterns")
def pattern_create(req: PatternCreateRequest) -> dict:
    ensure_dirs()
    return create_pattern_impl(req)


@router.post("/api/patterns/evidence-packet")
def patterns_evidence_packet(req: PatternEvidencePacketRequest) -> dict:
    ensure_dirs()
    return get_evidence_packet_impl(req)


@router.post("/api/patterns/{pattern_id}/accept")
def pattern_accept(pattern_id: str) -> dict:
    ensure_dirs()
    return accept_pattern_impl(pattern_id)


@router.post("/api/patterns/{pattern_id}/reject")
def pattern_reject(pattern_id: str) -> dict:
    ensure_dirs()
    return reject_pattern_impl(pattern_id)


@router.post("/api/patterns/{pattern_id}/restore")
def pattern_restore(pattern_id: str) -> dict:
    ensure_dirs()
    return restore_pattern_impl(pattern_id)


@router.post("/api/patterns/{pattern_id}/applications")
def pattern_application_create(pattern_id: str, req: PatternApplicationCreateRequest) -> dict:
    ensure_dirs()
    return record_pattern_application_impl(pattern_id, req)


@router.get("/api/patterns/{pattern_id}/applications")
def pattern_applications_list(pattern_id: str, limit: int = 50) -> dict:
    ensure_dirs()
    return list_pattern_applications_impl(pattern_id=pattern_id, limit=limit)


@router.get("/api/pattern-applications")
def pattern_applications_list_all(pattern_id: Optional[str] = None, limit: int = 50) -> dict:
    ensure_dirs()
    return list_pattern_applications_impl(pattern_id=pattern_id, limit=limit)


@router.patch("/api/pattern-applications/{application_id}")
def pattern_application_update(application_id: str, req: PatternApplicationOutcomeRequest) -> dict:
    ensure_dirs()
    return update_pattern_application_impl(application_id, req)


@router.post("/api/patterns/mark-processed")
def pattern_mark_processed(req: PatternMarkProcessedRequest) -> dict:
    ensure_dirs()
    return mark_pattern_processed_impl(req)


@router.get("/api/scenes/{scene_id}")
def get_scene_by_id(scene_id: str):
    payload = get_scene_context(scene_id)
    if "error" in payload:
        status_code = 400 if payload["error"] == "scene_id is required" else 404
        return JSONResponse(status_code=status_code, content=payload)
    return payload


@router.post("/api/trace")
def trace(req: TracePacketRequest) -> dict:
    return handle_trace_packet(req)


@router.post("/api/ingest/event")
def ingest_event(req: TraceEvent) -> dict:
    return ingest_trace_event(req)


@router.post("/api/ingest/spool")
def ingest_spool(session_id: str, spool_dir: str = ".opencode/titan/traces") -> dict:
    return ingest_spool_session(session_id=session_id, spool_dir=spool_dir)


@router.get("/api/retrieve")
def retrieve(
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 8,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    include_scenes: bool = False,
) -> dict:
    return retrieve_memory_brief(
        query=query or "",
        session_id=session_id,
        mode=mode,
        limit=limit,
        max_items=max_items,
        max_chars=max_chars,
        date_from=from_date,
        date_to=to_date,
        include_scenes=include_scenes,
    )


@router.get("/api/storage/stats")
def storage_stats() -> dict:
    from pathlib import Path
    from app.storage.sessions import BASE_DIR
    import os, statistics

    stats: dict = {}
    base = BASE_DIR

    # DB file
    db_path = base / "out" / "memories" / "memory_store.db"
    if db_path.exists():
        stats["db_file_size_bytes"] = db_path.stat().st_size
    else:
        stats["db_file_size_bytes"] = 0

    # Spool directory
    spool_dir = base / "traces"
    spool_size = 0
    spool_files = 0
    if spool_dir.exists():
        for f in spool_dir.iterdir():
            if f.is_file():
                spool_size += f.stat().st_size
                spool_files += 1
    stats["spool_size_bytes"] = spool_size
    stats["spool_file_count"] = spool_files

    # Memory & scene text stats from SQLite
    import sqlite3
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Memory text lengths
        mem_rows = conn.execute(
            "SELECT COUNT(*) as cnt, SUM(LENGTH(text)) as total_bytes, "
            "AVG(LENGTH(text)) as avg_bytes FROM memories"
        ).fetchone()
        if mem_rows and mem_rows["cnt"] > 0:
            stats["memory_count"] = mem_rows["cnt"]
            stats["memory_text_bytes"] = mem_rows["total_bytes"]
            stats["memory_avg_text_bytes"] = round(mem_rows["avg_bytes"], 1)

            # Median text length
            lengths = [
                row[0] for row in conn.execute(
                    "SELECT LENGTH(text) FROM memories ORDER BY LENGTH(text)"
                ).fetchall()
            ]
            if lengths:
                stats["memory_median_text_bytes"] = statistics.median(lengths)

        # Scene counts and size
        scene_rows = conn.execute(
            "SELECT COUNT(*) as cnt, "
            "SUM(LENGTH(extraction_user_text) + LENGTH(extraction_assistant_text)) as text_bytes, "
            "SUM(LENGTH(raw_events_json) + LENGTH(messages_json)) as json_bytes "
            "FROM scenes"
        ).fetchone()
        if scene_rows and scene_rows["cnt"] > 0:
            stats["scene_count"] = scene_rows["cnt"]
            stats["scene_text_bytes"] = scene_rows["text_bytes"]
            stats["scene_json_bytes"] = scene_rows["json_bytes"]
            stats["scene_total_bytes"] = (scene_rows["text_bytes"] or 0) + (scene_rows["json_bytes"] or 0)

    except Exception:
        pass
    finally:
        if conn:
            conn.close()

    stats["db_path"] = str(db_path)
    total = (stats.get("db_file_size_bytes", 0) +
             stats.get("spool_size_bytes", 0))
    stats["total_footprint_bytes"] = total

    return stats


@router.get("/api/debug/pipeline")
def debug_pipeline(session_id: Optional[str] = None) -> dict:
    return get_pipeline_debug_status(session_id=session_id)
