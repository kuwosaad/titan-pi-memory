from typing import Optional

from fastapi import APIRouter
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


router = APIRouter()


@router.get("/health")
def health() -> dict:
    ensure_dirs()
    return {"status": "ok"}


@router.get("/graph")
def graph(session_id: Optional[str] = None) -> HTMLResponse:
    html_content = build_graph(session_id=session_id)
    return HTMLResponse(content=html_content)


@router.get("/api/clusters/analyze")
def analyze_clusters(
    cluster_ids: str,
    session_id: Optional[str] = None,
    limit: int = 500,
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
    limit: int = 500,
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
    query: str,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 8,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> dict:
    return retrieve_memory_brief(
        query=query,
        session_id=session_id,
        mode=mode,
        limit=limit,
        max_items=max_items,
        max_chars=max_chars,
        date_from=from_date,
        date_to=to_date,
    )


@router.get("/api/debug/pipeline")
def debug_pipeline(session_id: Optional[str] = None) -> dict:
    return get_pipeline_debug_status(session_id=session_id)
