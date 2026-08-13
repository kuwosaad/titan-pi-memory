#!/usr/bin/env python3
"""Migrate legacy memories.json records into SQLite memory_store.db."""
import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from app.storage.memories import migrate_json_to_sqlite, migrate_legacy_memories


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Titan memory store from JSON to SQLite.")
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default="",
        help="Optional SQLite path override. Defaults to settings.memory_store_sqlite_path.",
    )
    parser.add_argument(
        "--json-only-legacy-fix",
        action="store_true",
        help="Only apply legacy JSON field migration without moving records to SQLite.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("Starting Titan memory migration...")
    legacy_migrated = migrate_legacy_memories()
    print(f"Legacy JSON normalization updated: {legacy_migrated} records")

    if args.json_only_legacy_fix:
        print("Skipping SQLite import (--json-only-legacy-fix enabled).")
        return 0

    sqlite_path = Path(args.sqlite_path).expanduser() if args.sqlite_path else None
    report = migrate_json_to_sqlite(sqlite_path=sqlite_path)
    print("SQLite import summary:")
    print(json.dumps(report, indent=2))
    print("Migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
