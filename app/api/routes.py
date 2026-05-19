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
    query: Optional[str] = None,
    session_id: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 8,
    max_items: Optional[int] = None,
    max_chars: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
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
