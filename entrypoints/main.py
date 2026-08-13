import sys
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.runtime.context import (
    adapter_defaults_from_environment,
    get_runtime_context,
    hydrate_process_environment,
)
from app.patterns.errors import PatternError

_RUNTIME_CONTEXT = get_runtime_context(
    root_dir=ROOT_DIR,
    adapter_defaults=adapter_defaults_from_environment(),
)
hydrate_process_environment(_RUNTIME_CONTEXT)
TITAN_HOME = _RUNTIME_CONTEXT.titan_home
# Legacy integrations inspect this environment variable at import time; keep
# the observable bootstrap while the context remains the source of truth.
os.environ.setdefault("TITAN_BASE_DIR", str(_RUNTIME_CONTEXT.base_dir))

from app.api.routes import router
from app.storage.sessions import ensure_dirs
from app.save_pipeline.auto_ingest import start_auto_ingest_worker, stop_auto_ingest_worker
from app.save_pipeline.dedup_worker import start_dedup_worker
from app.save_pipeline.lnn_tick_worker import start_lnn_tick_worker
from app.retrieval_pipeline.config import load_settings as _load_settings

def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


async def _startup_auto_ingest() -> None:
    if not _env_true("TITAN_AUTO_INGEST_ENABLED", default=True):
        return
    spool_dir = _RUNTIME_CONTEXT.trace_dir
    interval_seconds = float(os.getenv("TITAN_AUTO_INGEST_INTERVAL_SECONDS", "3"))
    start_auto_ingest_worker(app, spool_dir=spool_dir, interval_seconds=interval_seconds)

    dedup_stop = threading.Event()
    app.state.dedup_stop_event = dedup_stop
    start_dedup_worker(dedup_stop)

    settings = _load_settings()
    if settings.get("lnn", {}).get("enabled") and settings.get("lnn", {}).get("tick_enabled", True):
        lnn_stop = threading.Event()
        app.state.lnn_stop_event = lnn_stop
        tick_interval = float(settings.get("lnn", {}).get("decay_tick_seconds", 60.0))
        tau_disuse = float(settings.get("lnn", {}).get("tau_disuse_decay", 0.01))
        weight_decay = float(settings.get("lnn", {}).get("weight_decay", 0.001))
        start_lnn_tick_worker(lnn_stop, interval_seconds=tick_interval, tau_disuse_decay=tau_disuse, weight_decay=weight_decay)


async def _shutdown_auto_ingest() -> None:
    stop_auto_ingest_worker(app)
    dedup_stop = getattr(app.state, "dedup_stop_event", None)
    if dedup_stop:
        dedup_stop.set()
    lnn_stop = getattr(app.state, "lnn_stop_event", None)
    if lnn_stop:
        lnn_stop.set()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Keep worker startup/shutdown in one application-owned lifecycle."""

    await _startup_auto_ingest()
    try:
        yield
    finally:
        await _shutdown_auto_ingest()


app = FastAPI(lifespan=_lifespan)


@app.exception_handler(PatternError)
async def _pattern_error_handler(_request: Request, exc: PatternError) -> JSONResponse:
    """Translate framework-neutral Pattern errors at the HTTP adapter seam."""

    # FastAPI's historical HTTPException shape wraps the payload in ``detail``;
    # preserve that wire contract while keeping the implementation framework-free.
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(router)

ensure_dirs()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "entrypoints.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
