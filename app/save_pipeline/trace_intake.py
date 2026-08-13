"""Trace-to-scene intake boundary.

The intake object is deliberately small: the existing event ledger, spool
adapter, and extraction pipeline remain the compatibility implementation.  A
single framework-neutral object now owns the trace use cases so HTTP, MCP,
CLI, and hooks can depend on one seam without changing their call shapes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.runtime.context import get_runtime_context
from app.storage.models import TraceEvent


_LEGACY_DEFAULT_SPOOL_DIR = ".opencode/titan/traces"


def _resolve_spool_dir(spool_dir: Optional[str]) -> str:
    """Resolve the compatibility default through RuntimeContext.

    Callers may still pass an explicit directory, while old callers that rely
    on the historical default now follow ``TITAN_SPOOL_DIR`` and agent
    isolation.  Returning a string preserves the public pipeline shape.
    """

    if spool_dir and str(spool_dir) != _LEGACY_DEFAULT_SPOOL_DIR:
        return str(Path(spool_dir).expanduser())
    return str(get_runtime_context().trace_dir)


class TraceIntake:
    """Coordinate direct event ingest, spool ingest, processing, and status.

    Methods intentionally mirror the historical free functions.  Imports are
    lazy to avoid a module cycle (the compatibility implementation still
    lives in :mod:`app.save_pipeline.pipeline` during this migration wave).
    """

    def process_session_events(self, session_id: str, limit: int = 200) -> Dict[str, Any]:
        from app.save_pipeline.pipeline import _process_session_events_impl

        return _process_session_events_impl(session_id=session_id, limit=limit)

    def ingest_trace_event(self, event: TraceEvent, process_new: bool = True) -> Dict[str, Any]:
        from app.save_pipeline.pipeline import _ingest_trace_event_impl

        return _ingest_trace_event_impl(event=event, process_new=process_new)

    def ingest_spool_session(self, session_id: str, spool_dir: Optional[str] = None) -> Dict[str, Any]:
        from app.save_pipeline.pipeline import _ingest_spool_session_impl

        return _ingest_spool_session_impl(session_id=session_id, spool_dir=_resolve_spool_dir(spool_dir))

    def debug_status(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        from app.save_pipeline.pipeline import _get_pipeline_debug_status_impl

        return _get_pipeline_debug_status_impl(session_id=session_id)


_DEFAULT_TRACE_INTAKE = TraceIntake()


def get_trace_intake() -> TraceIntake:
    """Return the process-local intake adapter.

    The object has no mutable request state; keeping one instance preserves the
    old module-level behavior while allowing callers to inject a different
    implementation in future waves.
    """

    return _DEFAULT_TRACE_INTAKE


__all__ = ["TraceIntake", "get_trace_intake"]
