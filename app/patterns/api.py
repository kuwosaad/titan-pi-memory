from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.retrieval_pipeline.config import load_settings
from app.storage.memories import _resolve_sqlite_path

from .miner import build_evidence_packet
from .memory import PatternMemory
from .models import Pattern, PatternEvidence
from .processing import PatternProcessingLedger
from .errors import PatternDisabled, PatternNotFound, PatternStorageUnavailable, PatternValidation
from .storage import connect_pattern_db
from .store import PatternStore


logger = logging.getLogger(__name__)


DEFAULT_PATTERN_CONFIG = {
    "enabled": True,
    "processor_version": "pattern-miner-v2",
    "packet_mode": "adaptive",
    "batch_size": 100,
    "context_limit": 300,
    "min_evidence_count": 3,
    "min_scene_count": 2,
    "min_confidence_to_show": 0.45,
    "min_confidence_to_retrieve": 0.60,
    "retrieve_limit": 3,
    "coactivation_enabled": False,
    "high_signal_enabled": True,
    "semantic_cluster_enabled": True,
    "scene_episode_enabled": True,
    "entity_packets_enabled": True,
    "bridge_packets_enabled": True,
    "contradiction_packets_enabled": True,
    "cluster_min_memories": 3,
    "cluster_min_scenes": 2,
}


def _dump_model(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class PatternEvidenceInput(BaseModel):
    memory_id: str
    scene_id: Optional[str] = None
    role: str = "support"
    score: float = 0.0


class PatternCreateRequest(BaseModel):
    title: str
    kind: str = "other"
    scope: str = "user"
    status: str = "candidate"
    summary: str
    recommended_behavior: str
    trigger_terms: List[str] = Field(default_factory=list)
    evidence: List[PatternEvidenceInput] = Field(default_factory=list)
    confidence: float = 0.0
    applies_when: str = ""
    does_not_apply_when: str = ""
    actionability: float = 0.0
    retrieval_value: float = 0.0
    canonical_key: Optional[str] = None
    mined_run_id: Optional[str] = None
    last_refreshed_at: Optional[str] = None
    last_applied_at: Optional[str] = None
    source: str = "agent"


class PatternEvidencePacketRequest(BaseModel):
    batch_size: Optional[int] = None
    context_limit: Optional[int] = None
    session_id: Optional[str] = None
    mode: Optional[str] = None
    packet_type: Optional[str] = None
    processor_version: Optional[str] = None
    processor_config_hash: Optional[str] = None


class PatternMarkProcessedRequest(BaseModel):
    memory_ids: List[str]
    run_id: Optional[str] = None
    status: str = "processed"
    pattern_ids: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    mode: str = "incremental"
    processor_version: Optional[str] = None
    processor_config_hash: Optional[str] = None


def pattern_config() -> dict:
    settings = load_settings()
    configured = settings.get("patterns") if isinstance(settings.get("patterns"), dict) else {}
    return {**DEFAULT_PATTERN_CONFIG, **configured}


def processor_identity() -> tuple[str, str]:
    config = pattern_config()
    version = str(config.get("processor_version") or DEFAULT_PATTERN_CONFIG["processor_version"])
    hash_payload = {
        key: value
        for key, value in config.items()
        if key not in {"enabled", "auto_mine_enabled", "auto_mine_every_memories"}
    }
    config_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return version, config_hash


def resolve_pattern_db_path() -> Path:
    # Keep this function as a patchable compatibility seam for HTTP tests and
    # integrations.  ``_resolve_sqlite_path`` was historically patched by
    # callers, so retain that exact private import while the storage module
    # remains the canonical implementation behind it.
    return Path(_resolve_sqlite_path())


def pattern_store() -> PatternStore:
    return PatternStore(resolve_pattern_db_path())


def pattern_ledger() -> PatternProcessingLedger:
    return PatternProcessingLedger(resolve_pattern_db_path())


def pattern_memory() -> PatternMemory:
    """Return the framework-neutral pattern service for this request.

    The store/ledger factories remain deliberately injectable compatibility
    seams for existing integrations and characterization tests.
    """

    try:
        store = pattern_store()
        ledger = pattern_ledger()
    except (OSError, sqlite3.Error) as exc:
        raise PatternStorageUnavailable({"error": "Pattern storage is unavailable"}) from exc
    return PatternMemory(resolve_pattern_db_path(), store=store, ledger=ledger)


def get_evidence_packet(req: PatternEvidencePacketRequest) -> dict:
    config = pattern_config()
    if not bool(config.get("enabled", True)):
        raise PatternDisabled({"error": "patterns are disabled"})
    default_version, default_hash = processor_identity()
    try:
        return build_evidence_packet(
            processor_version=req.processor_version or default_version,
            processor_config_hash=req.processor_config_hash or default_hash,
            db_path=resolve_pattern_db_path(),
            batch_size=int(req.batch_size or config.get("batch_size", 100)),
            context_limit=int(req.context_limit or config.get("context_limit", 300)),
            session_id=req.session_id,
            mode=req.mode or str(config.get("packet_mode") or "adaptive"),
            packet_type=req.packet_type,
        )
    except (OSError, sqlite3.Error) as exc:
        raise PatternStorageUnavailable({"error": "Pattern evidence storage is unavailable"}) from exc


def get_pattern_status() -> dict:
    version, config_hash = processor_identity()
    status = pattern_memory().status(processor_version=version, processor_config_hash=config_hash)
    payload = _dump_model(status)
    payload["migration"] = _pattern_migration_status(
        resolve_pattern_db_path(),
        current_processor_version=version,
        current_processor_config_hash=config_hash,
        current_status=payload,
    )
    return payload


def _pattern_migration_status(
    db_path: Path,
    *,
    current_processor_version: str,
    current_processor_config_hash: str,
    current_status: dict,
) -> dict:
    migration = {
        "current_processor_version": current_processor_version,
        "current_processor_config_hash": current_processor_config_hash,
        "previous_processors": [],
        "previous_processed_memory_count": 0,
        "current_processed_memory_count": int(current_status.get("processed_current") or 0),
        "reprocess_available": False,
    }
    try:
        with connect_pattern_db(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pattern_memory_processing'"
            ).fetchone()
            if row is None:
                return migration
            rows = conn.execute(
                """
                SELECT processor_version, processor_config_hash, COUNT(DISTINCT memory_id) AS processed_count
                FROM pattern_memory_processing
                GROUP BY processor_version, processor_config_hash
                ORDER BY processor_version ASC, processor_config_hash ASC
                """
            ).fetchall()
            previous_count_row = conn.execute(
                """
                SELECT COUNT(DISTINCT memory_id) AS processed_count
                FROM pattern_memory_processing
                WHERE NOT (processor_version = ? AND processor_config_hash = ?)
                """,
                (current_processor_version, current_processor_config_hash),
            ).fetchone()
    except sqlite3.Error:
        return migration

    previous: list[dict] = []
    for row in rows:
        processor_version = str(row[0])
        processor_config_hash = str(row[1])
        processed_count = int(row[2])
        if processor_version == current_processor_version and processor_config_hash == current_processor_config_hash:
            continue
        previous.append(
            {
                "processor_version": processor_version,
                "processor_config_hash": processor_config_hash,
                "processed_count": processed_count,
            }
        )

    migration["previous_processors"] = previous
    migration["previous_processed_memory_count"] = int(previous_count_row[0]) if previous_count_row else 0
    migration["reprocess_available"] = migration["previous_processed_memory_count"] > 0 and int(current_status.get("unprocessed") or 0) > 0
    if migration["reprocess_available"]:
        migration["note"] = "Current processor identity differs from prior processed memories; reprocessing is expected."
    return migration


def list_patterns(status: Optional[str] = None, scope: Optional[str] = None, limit: int = 50) -> dict:
    patterns = pattern_memory().list(status=status, scope=scope, limit=limit)
    return {"patterns": [_dump_model(pattern) for pattern in patterns], "count": len(patterns)}


def get_pattern(pattern_id: str) -> dict:
    memory = pattern_memory()
    pattern = memory.get(pattern_id)
    evidence = memory.evidence(pattern_id)
    return {
        "pattern": _dump_model(pattern),
        "evidence": [_dump_model(item) for item in evidence],
    }


def create_pattern(req: PatternCreateRequest) -> dict:
    config = pattern_config()
    if not bool(config.get("enabled", True)):
        raise PatternDisabled({"error": "patterns are disabled"})

    try:
        pattern = Pattern(
            title=req.title,
            kind=req.kind,  # type: ignore[arg-type]
            scope=req.scope,  # type: ignore[arg-type]
            status=req.status,  # type: ignore[arg-type]
            summary=req.summary,
            recommended_behavior=req.recommended_behavior,
            trigger_terms=req.trigger_terms,
            confidence=req.confidence,
            applies_when=req.applies_when,
            does_not_apply_when=req.does_not_apply_when,
            actionability=req.actionability,
            retrieval_value=req.retrieval_value,
            canonical_key=req.canonical_key,
            mined_run_id=req.mined_run_id,
            last_refreshed_at=req.last_refreshed_at,
            last_applied_at=req.last_applied_at,
            source=req.source,
        )
    except ValidationError as exc:
        raise PatternValidation({"error": str(exc)}) from exc
    try:
        evidence = [
            PatternEvidence(
                pattern_id=pattern.id,
                memory_id=item.memory_id,
                scene_id=item.scene_id,
                role=item.role,  # type: ignore[arg-type]
                score=item.score,
            )
            for item in req.evidence
        ]
    except ValidationError as exc:
        raise PatternValidation({"error": str(exc)}) from exc
    evidence = _hydrate_evidence_scene_ids(evidence)
    _validate_scene_diversity(pattern, evidence, min_scene_count=int(config.get("min_scene_count", 2)))
    created = pattern_memory().create(
        pattern,
        evidence,
        validate_memory_ids=True,
        min_support_evidence=int(config.get("min_evidence_count", 3)),
    )
    return {"pattern": _dump_model(created), "evidence": [_dump_model(item) for item in evidence]}


def accept_pattern(pattern_id: str) -> dict:
    pattern = pattern_memory().set_status(pattern_id, "accepted")
    return {"pattern": _dump_model(pattern)}


def reject_pattern(pattern_id: str) -> dict:
    pattern = pattern_memory().set_status(pattern_id, "rejected")
    return {"pattern": _dump_model(pattern)}


def mark_processed(req: PatternMarkProcessedRequest) -> dict:
    default_version, default_hash = processor_identity()
    version = req.processor_version or default_version
    config_hash = req.processor_config_hash or default_hash
    memory = pattern_memory()
    run = None
    run_id = req.run_id
    auto_run = False
    if not run_id:
        run = memory.start_run(processor_version=version, processor_config_hash=config_hash, mode=req.mode)
        run_id = run.id
        auto_run = True
    try:
        marked = memory.mark_processed(
            req.memory_ids,
            processor_version=version,
            processor_config_hash=config_hash,
            run_id=run_id,
            status=req.status,
            pattern_ids=req.pattern_ids,
            error=req.error,
        )
        if auto_run:
            run = memory.finish_run(
                run_id,
                status="failed" if req.status == "failed" else "completed",
                memory_count=marked,
                candidate_count=len(req.pattern_ids),
                error=req.error,
            )
    except (KeyError, ValueError) as exc:
        if auto_run and run_id:
            try:
                run = memory.finish_run(
                    run_id,
                    status="failed",
                    memory_count=0,
                    candidate_count=len(req.pattern_ids),
                    error=str(exc),
                )
            except Exception:
                logger.exception("Failed to close auto-created pattern mining run after mark_processed error")
        raise PatternValidation({"error": str(exc), "run_id": run_id}) from exc
    return {
        "run_id": run_id,
        "marked_count": marked,
        "processor_version": version,
        "processor_config_hash": config_hash,
        "run": _dump_model(run) if run is not None else None,
    }


def _hydrate_evidence_scene_ids(evidence: list[PatternEvidence]) -> list[PatternEvidence]:
    missing_scene_ids = sorted({item.memory_id for item in evidence if not item.scene_id})
    if not missing_scene_ids:
        return evidence
    placeholders = ",".join("?" for _ in missing_scene_ids)
    scene_by_memory: dict[str, str | None] = {}
    try:
        with connect_pattern_db(resolve_pattern_db_path()) as conn:
            rows = conn.execute(
                f"SELECT id, scene_id FROM memories WHERE id IN ({placeholders})",
                tuple(missing_scene_ids),
            ).fetchall()
            scene_by_memory = {row["id"]: row["scene_id"] for row in rows}
    except (OSError, sqlite3.DatabaseError) as exc:
        logger.warning("Pattern evidence scene lookup failed for %s memory ids: %s", len(missing_scene_ids), exc)
        raise PatternStorageUnavailable(
            {"error": "Pattern evidence scene lookup failed; retry when the memory database is available"}
        ) from exc
    hydrated: list[PatternEvidence] = []
    for item in evidence:
        scene_id = scene_by_memory.get(item.memory_id)
        if not item.scene_id and scene_id:
            if hasattr(item, "model_copy"):
                hydrated.append(item.model_copy(update={"scene_id": scene_id}))
            else:
                hydrated.append(item.copy(update={"scene_id": scene_id}))
        else:
            hydrated.append(item)
    return hydrated


def _validate_scene_diversity(pattern: Pattern, evidence: list[PatternEvidence], *, min_scene_count: int) -> None:
    support = [item for item in evidence if item.role == "support"]
    if not support:
        return
    if pattern.kind == "preference":
        return
    missing_scene_ids = [item.memory_id for item in support if not item.scene_id]
    if missing_scene_ids:
        raise PatternValidation(
            {
                "error": "Pattern support evidence is missing scene_id values after scene lookup",
                "memory_ids": missing_scene_ids,
            }
        )
    scenes = {item.scene_id for item in support if item.scene_id}
    if len(scenes) < min_scene_count:
        raise PatternValidation(
            {
                "error": f"Pattern requires support evidence across at least {min_scene_count} scenes",
                "scene_count": len(scenes),
            }
        )
