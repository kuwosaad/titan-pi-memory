import sys
import os
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
    maintenance_callback = None
    if _RUNTIME_CONTEXT.agent_name == "codex":
        from integrations.codex_titan_plugin.pending_recovery import recover_one_pending_turn

        maintenance_callback = recover_one_pending_turn
    start_auto_ingest_worker(
        app,
        spool_dir=spool_dir,
        interval_seconds=interval_seconds,
        maintenance_callback=maintenance_callback,
    )



async def _shutdown_auto_ingest() -> None:
    stop_auto_ingest_worker(app)


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
