"""
Titan Pi — HTTP server entry point.

Starts the Titan FastAPI server with auto-ingest for the Pi agent workspace.
Uses a separate port (8002) from OpenCode's default (8000).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Resolve repo root and Pi agent home
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

TITAN_HOME = Path(
    os.getenv("TITAN_HOME", str(Path.home() / ".titan" / "agents" / "pi"))
).expanduser()
os.environ.setdefault("TITAN_BASE_DIR", str(TITAN_HOME))
TITAN_HOME.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 2. Load .env from Pi agent home (API keys, config paths)
# ---------------------------------------------------------------------------
_env_file = TITAN_HOME / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and "=" in _line and not _line.startswith("#"):
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

# Point to Pi-specific model config
for _cfg_key, _cfg_file in [
    ("TITAN_EXTRACTION_CONFIG_PATH", TITAN_HOME / "config" / "extraction_models.yaml"),
    ("TITAN_EMBEDDING_CONFIG_PATH", TITAN_HOME / "config" / "embedding_models.yaml"),
]:
    if _cfg_file.exists():
        os.environ[_cfg_key] = str(_cfg_file)

# ---------------------------------------------------------------------------
# 3. Build the app (import triggers module-level init in entrypoints.main)
# ---------------------------------------------------------------------------
from entrypoints.main import app

# ---------------------------------------------------------------------------
# 4. Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("TITAN_PI_PORT", "8002"))
    log_level = os.getenv("TITAN_PI_LOG_LEVEL", "warning")
    access_log = os.getenv("TITAN_PI_ACCESS_LOG", "").lower() in {"1", "true", "yes"}
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level=log_level,
        access_log=access_log,
    )
