from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from app.retrieval_pipeline.config import load_settings

LOGGER = logging.getLogger(__name__)


def _run_dedup_pass() -> Dict[str, Any]:
    """Keep the legacy worker inert without touching existing buffer files.

    Save-time LLM deduplication is retired because rewriting a stored memory
    cannot safely preserve its embedding and scene lineage. The buffer module
    remains available for read-only compatibility with existing pending files.
    """
    settings = load_settings()
    if settings.get("dedup", {}).get("enabled", False):
        LOGGER.warning("dedup_worker: save-time LLM deduplication is retired")
    return {"status": "dedup_disabled"}


def _dedup_loop(stop_event: threading.Event, interval_seconds: float) -> None:
    while not stop_event.is_set():
        try:
            _run_dedup_pass()
        except Exception:
            LOGGER.exception("dedup_worker: unhandled error in dedup pass")
        stop_event.wait(interval_seconds)


def start_dedup_worker(stop_event: threading.Event, interval_seconds: float = 300.0) -> threading.Thread:
    settings = load_settings()
    window = float(settings.get("dedup", {}).get("buffer_window_seconds", interval_seconds))
    worker = threading.Thread(
        target=_dedup_loop,
        args=(stop_event, window),
        daemon=True,
        name="titan-dedup-worker",
    )
    worker.start()
    LOGGER.info("dedup_worker: retired save-time LLM deduplication worker")
    return worker
