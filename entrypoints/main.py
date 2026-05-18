import sys
import os
import threading
from pathlib import Path

from fastapi import FastAPI

ROOT_DIR = Path(__file__).resolve().parent.parent
_default_home = ROOT_DIR if (ROOT_DIR / ".git").exists() else (Path.home() / ".titan")
TITAN_HOME = Path(os.getenv("TITAN_HOME", str(_default_home))).expanduser()
os.environ.setdefault("TITAN_BASE_DIR", str(TITAN_HOME))


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


_load_env_file(TITAN_HOME / ".env")
if TITAN_HOME != ROOT_DIR:
    _load_env_file(ROOT_DIR / ".env")
sys.path.insert(0, str(ROOT_DIR))

from app.api.routes import router
from app.storage.sessions import ensure_dirs
from app.save_pipeline.auto_ingest import start_auto_ingest_worker, stop_auto_ingest_worker
from app.save_pipeline.dedup_worker import start_dedup_worker
from app.save_pipeline.lnn_tick_worker import start_lnn_tick_worker
from app.retrieval_pipeline.config import load_settings as _load_settings

app = FastAPI()

app.include_router(router)

ensure_dirs()


def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@app.on_event("startup")
async def _startup_auto_ingest() -> None:
    if not _env_true("TITAN_AUTO_INGEST_ENABLED", default=True):
        return
    spool_dir = Path(os.getenv("TITAN_SPOOL_DIR", str(TITAN_HOME / "traces")))
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


@app.on_event("shutdown")
async def _shutdown_auto_ingest() -> None:
    stop_auto_ingest_worker(app)
    dedup_stop = getattr(app.state, "dedup_stop_event", None)
    if dedup_stop:
        dedup_stop.set()
    lnn_stop = getattr(app.state, "lnn_stop_event", None)
    if lnn_stop:
        lnn_stop.set()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "entrypoints.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
