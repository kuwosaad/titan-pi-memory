"""Shared SQLite plumbing for the pattern adapters.

Keeping connection setup here prevents the API, bundle exporter, graph view,
and processing ledger from slowly acquiring different timeout/pragma behavior.
The schema itself remains owned by :mod:`app.storage.sqlite_schema`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from app.storage.memories import _resolve_sqlite_path

SQLITE_TIMEOUT_SECONDS = 30.0


def resolve_pattern_db_path() -> Path:
    """Return Titan's configured SQLite path (compatibility fallback included)."""

    return Path(_resolve_sqlite_path())


def connect_pattern_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a configured pattern connection with the canonical SQLite pragmas."""

    path = Path(db_path) if db_path is not None else resolve_pattern_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def pattern_connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = connect_pattern_db(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
