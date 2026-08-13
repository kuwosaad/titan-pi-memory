from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


PatternKind = Literal["codebase", "workflow", "failure", "preference", "product", "distribution", "other"]
PatternScope = Literal["user", "repo", "team", "agent", "global"]
PatternStatus = Literal["candidate", "accepted", "rejected", "superseded"]
PatternEvidenceRole = Literal["support", "contradict", "bridge", "central"]
PatternRunStatus = Literal["running", "completed", "failed"]
PatternProcessingStatus = Literal["processed", "skipped", "failed"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_pattern_id() -> str:
    return f"pattern:{uuid4().hex}"


def new_run_id() -> str:
    return f"pattern-run:{uuid4().hex}"


def new_application_id() -> str:
    return f"pattern-application:{uuid4().hex}"


class Pattern(BaseModel):
    id: str = Field(default_factory=new_pattern_id)
    title: str
    kind: PatternKind = "other"
    scope: PatternScope = "user"
    status: PatternStatus = "candidate"
    summary: str
    recommended_behavior: str
    trigger_terms: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    applies_when: str = ""
    does_not_apply_when: str = ""
    actionability: float = 0.0
    retrieval_value: float = 0.0
    canonical_key: Optional[str] = None
    mined_run_id: Optional[str] = None
    last_refreshed_at: Optional[str] = None
    last_applied_at: Optional[str] = None
    source: str = "agent"


class PatternEvidence(BaseModel):
    pattern_id: str
    memory_id: str
    scene_id: Optional[str] = None
    role: PatternEvidenceRole = "support"
    score: float = 0.0


class PatternMiningRun(BaseModel):
    id: str = Field(default_factory=new_run_id)
    started_at: str = Field(default_factory=now_iso)
    finished_at: Optional[str] = None
    status: PatternRunStatus = "running"
    processor_version: str
    processor_config_hash: str
    mode: str
    memory_count: int = 0
    candidate_count: int = 0
    accepted_count: int = 0
    error: Optional[str] = None


class PatternApplication(BaseModel):
    id: str = Field(default_factory=new_application_id)
    pattern_id: str
    query: str
    task_id: Optional[str] = None
    retrieved_at: str = Field(default_factory=now_iso)
    was_used: Optional[bool] = None
    outcome: Optional[str] = None
    feedback: Optional[str] = None


class PatternMiningStatus(BaseModel):
    memories_total: int
    processed_current: int
    unprocessed: int
    candidate_patterns: int
    accepted_patterns: int
    last_run: Optional[PatternMiningRun] = None
    processor_version: str
    processor_config_hash: str
