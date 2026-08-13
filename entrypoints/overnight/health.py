"""
Pre-flight health checks for overnight retrieval harness.

Each check is independent and returns (ok: bool, detail: str).
Checks are run before any benchmark queries are dispatched.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

LOGGER = logging.getLogger(__name__)


class HealthCheckResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self._checks: List[Tuple[str, bool, str]] = []

    def add(self, check_name: str, ok: bool, detail: str = "") -> None:
        self._checks.append((check_name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self._checks)

    def summary(self) -> str:
        lines = [f"[{self.name}]"]
        for check_name, ok, detail in self._checks:
            status = "✓" if ok else "✗"
            lines.append(f"  {status} {check_name}: {detail or ('OK' if ok else 'FAILED')}")
        return "\n".join(lines)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "checks": [
                {"check": cn, "ok": ok, "detail": detail}
                for cn, ok, detail in self._checks
            ],
        }


def check_storage_dirs(base_dir: Path) -> HealthCheckResult:
    result = HealthCheckResult("storage_dirs")
    required = ["out/sessions", "out/memories", "out/traces"]
    for rel in required:
        path = base_dir / rel
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
                result.add(f"created_{rel}", True, f"created {path}")
            except Exception as exc:
                result.add(f"create_{rel}", False, str(exc))
        elif not os.access(path, os.W_OK):
            result.add(f"writable_{rel}", False, f"not writable: {path}")
        else:
            result.add(f"exists_{rel}", True, str(path))
    return result


def check_memory_store(manifest: Dict[str, Any]) -> HealthCheckResult:
    result = HealthCheckResult("memory_store")
    try:
        from app.storage.memories import get_memory_repository

        repo = get_memory_repository()
        total = repo.get_memory_count()
        result.add("repository_accessible", True, f"total memories: {total}")
    except Exception as exc:
        result.add("repository_accessible", False, str(exc))
        return result

    health_cfg = manifest.get("health") or {}
    min_memories = health_cfg.get("min_memories", 5)
    if total < min_memories:
        result.add(
            "min_memories",
            False,
            f"only {total} memories found, minimum {min_memories} required for meaningful benchmarks",
        )
    else:
        result.add("min_memories", True, f"{total} >= {min_memories} threshold")

    max_age_days = health_cfg.get("max_age_days", 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    try:
        from app.storage.memories import get_memory_repository
        from app.storage.repository import CandidateFilters

        filters = CandidateFilters(
            recency_days=max_age_days,
            session_id=None,
            session_bias=False,
            memory_types=None,
            mode="both",
            min_reliability=0.0,
        )
        candidates = repo.query_candidates(filters)
        result.add(
            "recent_memories",
            len(candidates) > 0,
            f"{len(candidates)} memories in last {max_age_days} days",
        )
    except Exception as exc:
        result.add("recent_memories", False, str(exc))

    return result


def check_config_loading() -> HealthCheckResult:
    result = HealthCheckResult("config_loading")
    try:
        from app.retrieval_pipeline.config import load_settings

        settings = load_settings()
        result.add("settings_load", True, "settings loaded OK")
    except Exception as exc:
        result.add("settings_load", False, str(exc))
        return result

    retrieval_enabled = settings.get("retrieval_enabled", False)
    result.add("retrieval_enabled", bool(retrieval_enabled), f"retrieval_enabled={retrieval_enabled}")
    return result


def check_backend_imports(manifest: Dict[str, Any]) -> HealthCheckResult:
    result = HealthCheckResult("backend_imports")
    health_cfg = manifest.get("health") or {}

    if health_cfg.get("check_extraction", True):
        try:
            from app.save_pipeline.extraction.extractor import extract_atomic_memories
            from app.save_pipeline.extraction.adapters import get_extraction_adapter

            adapter = get_extraction_adapter()
            result.add("extraction_adapter", True, str(type(adapter).__name__))
        except Exception as exc:
            result.add("extraction_adapter", False, str(exc))

    if health_cfg.get("check_embedding", True):
        try:
            from app.embedding.embedder import embed

            test_vec = embed(["health check probe"])
            result.add("embedding_backend", True, f"vector dim={len(test_vec[0])}")
        except Exception as exc:
            result.add("embedding_backend", False, str(exc))

    return result


def check_env_isolation() -> HealthCheckResult:
    result = HealthCheckResult("env_isolation")
    overnight_run = os.getenv("TITAN_OVERNIGHT_RUN", "0")
    if overnight_run == "1":
        result.add("TITAN_OVERNIGHT_RUN", True, "running in isolation mode")
        label = os.getenv("TITAN_OVERNIGHT_LABEL", "unknown")
        result.add("TITAN_OVERNIGHT_LABEL", True, f"label={label}")
    else:
        result.add("TITAN_OVERNIGHT_RUN", False, "TITAN_OVERNIGHT_RUN not set; may affect live Titan storage")
    return result


def check_required_env_vars() -> HealthCheckResult:
    result = HealthCheckResult("required_env_vars")
    # Check the extraction and embedding backends have their required keys
    try:
        from app.retrieval_pipeline.config import load_settings
        from tools.cli.titan import get_required_provider_envs

        settings = load_settings()
        backend_info = get_required_provider_envs(Path(__file__).resolve().parents[2])
        extraction_backend = settings.get("extraction_backend") or backend_info.get("extraction_backend") or "gemini"
        embedding_backend = settings.get("embedding_backend") or backend_info.get("embedding_backend") or "gemini"

        result.add("extraction_backend", True, extraction_backend)
        result.add("embedding_backend", True, embedding_backend)

        # Check critical env vars based on backend
        if extraction_backend == "gemini":
            key = os.getenv("GEMINI_API_KEY") or ""
            result.add("GEMINI_API_KEY", bool(key), "set" if key else "MISSING")
        if embedding_backend == "gemini":
            key = os.getenv("GEMINI_API_KEY") or ""
            result.add("GEMINI_API_KEY_for_embedding", bool(key), "set" if key else "MISSING")
    except Exception as exc:
        result.add("env_var_check", False, str(exc))

    return result


def run_all_health_checks(manifest: Dict[str, Any]) -> HealthCheckResult:
    """
    Run all pre-flight health checks and return an aggregate result.
    Sets up storage dirs before checking memory store.
    """
    from app.storage.sessions import BASE_DIR, ensure_dirs

    # Ensure dirs first so storage checks can proceed
    try:
        ensure_dirs()
    except Exception as exc:
        LOGGER.warning("ensure_dirs failed: %s", exc)

    checks = [
        check_env_isolation(),
        check_required_env_vars(),
        check_storage_dirs(BASE_DIR),
        check_config_loading(),
        check_memory_store(manifest),
        check_backend_imports(manifest),
    ]

    aggregate = HealthCheckResult("all_health_checks")
    for check in checks:
        for cn, ok, detail in check._checks:
            aggregate.add(cn, ok, detail)
    return aggregate
