"""Shared SQLite connection and path policy for Titan storage adapters.

This module is intentionally small: it centralizes timeout/busy handling while
leaving each adapter's schema and transactions unchanged.  The public helper
names are additive so existing private path imports remain valid during the
architecture migration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SQLITE_TIMEOUT_SECONDS = 30.0


def resolve_sqlite_path(default_path: Path, *, base_dir: Optional[Path] = None) -> Path:
    """Resolve the configured memory DB path relative to Titan's base dir."""

    from app.runtime.context import get_runtime_context

    context = get_runtime_context()
    configured_by_env = bool(context.environment.get("TITAN_MEMORY_DB_PATH"))
    configured_by_settings = bool(context.settings.get("memory_store_sqlite_path"))
    if configured_by_env or configured_by_settings:
        return Path(context.memory_db_path)

    fallback = Path(default_path).expanduser()
    if fallback.is_absolute():
        return fallback
    root = Path(base_dir).expanduser().resolve() if base_dir is not None else context.base_dir
    return (root / fallback).resolve()


def connect_sqlite(path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection with consistent lock behavior."""

    conn = sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn
