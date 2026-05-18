from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI

from app.save_pipeline.pipeline import ingest_spool_session


logger = logging.getLogger(__name__)


def discover_spool_sessions(spool_dir: Path) -> List[str]:
    if not spool_dir.exists():
        return []
    return sorted(
        {
            path.stem
            for path in spool_dir.glob("*.jsonl")
            if path.is_file() and path.stem
        }
    )


def ingest_available_sessions(spool_dir: Path) -> Dict[str, Dict[str, int]]:
    results: Dict[str, Dict[str, int]] = {}
    for session_id in discover_spool_sessions(spool_dir):
        try:
            result = ingest_spool_session(session_id=session_id, spool_dir=str(spool_dir))
            results[session_id] = {
                "ingested": int(result.get("ingested", 0)),
                "processed_events": int(result.get("processed_events", 0)),
                "prompt_candidates": int(result.get("prompt_candidates", 0)),
                "stored_memories": int(result.get("stored_memories", 0)),
                "fallback_memories": int(result.get("fallback_memories", 0)),
                "queued_retries": int(result.get("queued_retries", 0)),
                "skipped_low_signal": int(result.get("skipped_low_signal", 0)),
                "retry_queue_size": int(result.get("retry_queue_size", 0)),
            }
        except Exception:
            logger.exception("Auto-ingest failed for session '%s'", session_id)
    return results


def _auto_ingest_loop(stop_event: threading.Event, spool_dir: Path, interval_seconds: float) -> None:
    while not stop_event.is_set():
        results = ingest_available_sessions(spool_dir)
        for session_id, counts in results.items():
            logger.info(
                "Auto-ingest session=%s ingested=%s processed=%s prompts=%s stored=%s fallback=%s retries=%s skipped_low_signal=%s retry_queue=%s",
                session_id,
                counts.get("ingested", 0),
                counts.get("processed_events", 0),
                counts.get("prompt_candidates", 0),
                counts.get("stored_memories", 0),
                counts.get("fallback_memories", 0),
                counts.get("queued_retries", 0),
                counts.get("skipped_low_signal", 0),
                counts.get("retry_queue_size", 0),
            )
        stop_event.wait(interval_seconds)


def start_auto_ingest_worker(app, spool_dir: Path, interval_seconds: float = 3.0) -> None:
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_auto_ingest_loop,
        args=(stop_event, spool_dir, interval_seconds),
        daemon=True,
        name="titan-auto-ingest",
    )
    worker.start()
    if app is not None:
        app.state.auto_ingest_stop_event = stop_event
        app.state.auto_ingest_worker = worker


def stop_auto_ingest_worker(app) -> None:
    stop_event = getattr(app.state, "auto_ingest_stop_event", None) if app is not None else None
    worker = getattr(app.state, "auto_ingest_worker", None) if app is not None else None
    if not stop_event or not worker:
        return
    stop_event.set()
    worker.join(timeout=2.0)
