"""Lightweight request schemas shared by HTTP, MCP, and the pattern API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


_RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


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
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    snapshot_cutoff: Optional[str] = None

    @model_validator(mode="after")
    def validate_time_window(self) -> "PatternEvidencePacketRequest":
        parsed: dict[str, datetime] = {}
        for field_name in ("from_ts", "to_ts", "snapshot_cutoff"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not _RFC3339_RE.fullmatch(value):
                raise ValueError(f"{field_name} must be an RFC3339 timestamp with timezone")
            try:
                parsed[field_name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a valid RFC3339 timestamp") from exc
        from_value = parsed.get("from_ts")
        to_value = parsed.get("to_ts")
        cutoff_value = parsed.get("snapshot_cutoff")
        if from_value and to_value and from_value > to_value:
            raise ValueError("from_ts must be earlier than or equal to to_ts")
        if from_value and cutoff_value and from_value > cutoff_value:
            raise ValueError("from_ts must be earlier than or equal to snapshot_cutoff")
        return self


class PatternApplicationCreateRequest(BaseModel):
    query: str
    task_id: Optional[str] = None
    retrieved_at: Optional[str] = None
    was_used: Optional[bool] = None
    outcome: Optional[str] = None
    feedback: Optional[str] = None
    shown_at: Optional[str] = None
    used_at: Optional[str] = None
    outcome_observed_at: Optional[str] = None


class PatternApplicationOutcomeRequest(BaseModel):
    was_used: Optional[bool] = None
    outcome: Optional[str] = None
    feedback: Optional[str] = None


class PatternMarkProcessedRequest(BaseModel):
    memory_ids: List[str]
    run_id: Optional[str] = None
    status: str = "processed"
    pattern_ids: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    mode: str = "incremental"
    processor_version: Optional[str] = None
    processor_config_hash: Optional[str] = None


__all__ = [
    "PatternApplicationCreateRequest",
    "PatternApplicationOutcomeRequest",
    "PatternCreateRequest",
    "PatternEvidenceInput",
    "PatternEvidencePacketRequest",
    "PatternMarkProcessedRequest",
]
