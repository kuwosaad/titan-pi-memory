"""Framework-neutral PatternMemory facade.

This is intentionally small: the existing ``PatternStore`` and processing
ledger remain the durable SQLite adapters, while this facade gives HTTP, MCP,
CLI, and future adapters one typed seam and one place for lifecycle/error
semantics. Existing store methods remain available and unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

from .errors import PatternNotFound, PatternStorageUnavailable, PatternValidation
from .models import Pattern, PatternApplication, PatternEvidence
from .processing import PatternProcessingLedger
from .store import PatternStore, PatternValidationError
from .storage import resolve_pattern_db_path


class PatternMemory:
    """Durable pattern-card and processing lifecycle service."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        store: Optional[PatternStore] = None,
        ledger: Optional[PatternProcessingLedger] = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else resolve_pattern_db_path()
        # Injection keeps this seam easy to characterize and preserves the
        # historical ``pattern_store``/``pattern_ledger`` patch points.
        try:
            self.store = store if store is not None else PatternStore(self.db_path)
            self.ledger = ledger if ledger is not None else PatternProcessingLedger(self.db_path)
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def get(self, pattern_id: str) -> Pattern:
        try:
            pattern = self.store.get_pattern(pattern_id)
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc
        if pattern is None:
            raise PatternNotFound(pattern_id)
        return pattern

    def list(self, *, status: Optional[str] = None, scope: Optional[str] = None, limit: int = 50) -> list[Pattern]:
        try:
            return self.store.list_patterns(status=status, scope=scope, limit=limit)
        except PatternValidationError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def create(
        self,
        pattern: Pattern,
        evidence: Sequence[PatternEvidence],
        *,
        validate_memory_ids: bool = True,
        min_support_evidence: int = 1,
    ) -> Pattern:
        try:
            return self.store.create_pattern(
                pattern,
                evidence,
                validate_memory_ids=validate_memory_ids,
                min_support_evidence=min_support_evidence,
            )
        except PatternValidationError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def set_status(self, pattern_id: str, status: str) -> Pattern:
        try:
            return self.store.update_status(pattern_id, status)
        except KeyError as exc:
            raise PatternNotFound(pattern_id) from exc
        except PatternValidationError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def evidence(self, pattern_id: str, *, role: Optional[str] = None) -> list[PatternEvidence]:
        try:
            return self.store.list_evidence(pattern_id, role=role)
        except PatternValidationError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def record_application(self, application: PatternApplication) -> PatternApplication:
        try:
            return self.store.record_application(application)
        except PatternValidationError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def status(self, *, processor_version: str, processor_config_hash: str):
        try:
            return self.ledger.status(
                processor_version=processor_version,
                processor_config_hash=processor_config_hash,
            )
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def start_run(self, *, processor_version: str, processor_config_hash: str, mode: str):
        try:
            return self.ledger.start_run(
                processor_version=processor_version,
                processor_config_hash=processor_config_hash,
                mode=mode,
            )
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def mark_processed(self, memory_ids: Sequence[str], **kwargs: Any) -> int:
        try:
            return self.ledger.mark_processed(memory_ids, **kwargs)
        except KeyError as exc:
            raise PatternNotFound(str(exc)) from exc
        except ValueError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc

    def finish_run(self, run_id: str, **kwargs: Any):
        try:
            return self.ledger.finish_run(run_id, **kwargs)
        except KeyError as exc:
            raise PatternNotFound(str(exc)) from exc
        except ValueError as exc:
            raise PatternValidation(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise PatternStorageUnavailable(str(exc)) from exc


__all__ = ["PatternMemory"]
