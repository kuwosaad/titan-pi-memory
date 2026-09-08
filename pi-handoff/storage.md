# Storage worker handoff

## Owned files

- `app/storage/scenes.py`
- `app/storage/memories.py`
- `tests/test_storage_integrity.py`

## Changes completed

- Recovery marker `titan_recovery_extraction` is excluded only from immutable raw-event comparison; valid `stored`/`skipped` markers persist through replay, and `stored` cannot be downgraded.
- Partial scene upgrades now require stable session/turn/kind/scene sequence/anchor identity. Every claimed source ID must be represented by incoming raw evidence or explicitly listed as missing; historical JSON rows remain untouched and unrelated scene IDs append.
- SQLite memory and scene preflight checks run under `BEGIN IMMEDIATE`, making equivalent concurrent replays no-ops and conflicting replays explicit collisions.
- JSON memory replay retains the intentional any-match behavior for legacy duplicate rows.

## Focused results

Using the dependency-complete `.venv/bin/python` with temporary `TITAN_HOME`, `TITAN_BASE_DIR`, `TITAN_RUNTIME_DIR`, and `TITAN_SPOOL_DIR`:

- Final focused storage/evidence run (`test_storage_integrity.py`, `test_scene_store.py`, `test_scene_pipeline.py`, `test_pending_scene_events.py`, `test_memory_store_sqlite.py`): **62 passed, 3 subtests passed**.
- `git diff --check` and Python compilation for all three owned Python files pass.

## Remaining blocker / limitation

No storage-specific blocker remains. The out-of-scope `test_codex_recover_pending_cli.py` was not clean under the mandated external temporary environment because its internal isolation expects `TITAN_SPOOL_DIR` to be unset; two assertions observed the externally supplied spool path. The pending-recovery tests themselves passed. No staging, commit, push, publish, version, or delete operation was performed.
