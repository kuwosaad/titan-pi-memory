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
os.environ.setdefault("TITAN_PI_ADAPTER", "1")
os.environ.setdefault("TITAN_PI_DEFAULT_AGENT", "pi")
os.environ.setdefault("TITAN_PI_DEFAULT_HOME", str(TITAN_HOME))

# ---------------------------------------------------------------------------
# 2. RuntimeContext resolves .env files and model paths after import.
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
    os.environ.setdefault("TITAN_PORT", str(port))
    log_level = os.getenv("TITAN_PI_LOG_LEVEL", "warning")
    access_log = os.getenv("TITAN_PI_ACCESS_LOG", "").lower() in {"1", "true", "yes"}
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level=log_level,
        access_log=access_log,
    )
