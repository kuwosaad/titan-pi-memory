"""
Manifest schema and loader for the overnight retrieval harness.

The manifest drives what the harness does in an overnight run:
- isolation config (separate TITAN_BASE_DIR)
- benchmark queries with gold-answer annotations
- runtime bounds (max_hours, max_queries)
- artifact output paths
- health check thresholds

Example manifest (YAML):
---
version: "1"
isolation:
  base_dir: ~/.titan-overnight        # overrides TITAN_BASE_DIR for this run
  label: "overnight-retrieval-v1"      # used in artifact filenames
runtime:
  max_hours: 3
  max_queries: 50
  warmup_queries: 3
health:
  min_memories: 10
  max_age_days: 7
  required_backends:
    - extraction
    - embedding
artifacts:
  output_dir: ~/.titan-overnight/artifacts
  log_level: INFO
benchmarks:
  - id: "learnings_recall_v1"
    description: "Recall test for learnings stream"
    queries:
      - q: "what decisions were made about the auth flow"
        gold_session_ids: ["session-abc"]
        expected_themes: ["auth", "decision"]
        mode: "learnings"
        top_k: 12
      - q: "what did kuwo mention about python preferences"
        gold_session_ids: ["session-xyz"]
        expected_themes: ["python", "preference"]
        mode: "learnings"
        top_k: 8
    metrics:
      - recall_at_k
      - gold_in_pool_rate
  - id: "rough_recall_v1"
    ...
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import yaml


class IsolationConfig(TypedDict, total=False):
    base_dir: str
    label: str
    no_production_writes: bool  # always True for overnight runs; enforced


class RuntimeConfig(TypedDict, total=False):
    max_hours: float
    max_queries: int
    warmup_queries: int
    query_timeout_seconds: float


class HealthConfig(TypedDict, total=False):
    min_memories: int
    max_age_days: int
    required_backends: List[str]
    check_extraction: bool
    check_embedding: bool


class ArtifactConfig(TypedDict, total=False):
    output_dir: str
    log_level: str


class BenchmarkQuery(TypedDict, total=False):
    q: str
    gold_session_ids: List[str]
    expected_themes: List[str]
    mode: str
    top_k: int
    session_id: Optional[str]


class BenchmarkDefinition(TypedDict, total=False):
    id: str
    description: str
    queries: List[BenchmarkQuery]
    metrics: List[str]


class HarnessManifest(TypedDict, total=False):
    version: str
    isolation: IsolationConfig
    runtime: RuntimeConfig
    health: HealthConfig
    artifacts: ArtifactConfig
    benchmarks: List[BenchmarkDefinition]


def expand_manifest_path(path_value: str) -> str:
    return str(Path(path_value).expanduser())


def _resolve_defaults(raw: Dict[str, Any]) -> HarnessManifest:
    defaults: HarnessManifest = {
        "version": "1",
        "isolation": {
            "base_dir": str(Path.home() / ".titan-overnight"),
            "label": datetime.now(timezone.utc).strftime("%Y%m%d"),
            "no_production_writes": True,
        },
        "runtime": {
            "max_hours": 3.0,
            "max_queries": 50,
            "warmup_queries": 3,
            "query_timeout_seconds": 30.0,
        },
        "health": {
            "min_memories": 5,
            "max_age_days": 30,
            "required_backends": [],
            "check_extraction": True,
            "check_embedding": True,
        },
        "artifacts": {
            "output_dir": str(Path.home() / ".titan-overnight" / "artifacts"),
            "log_level": "INFO",
        },
        "benchmarks": [],
    }

    def _merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                _merge(target[key], value)
            elif key not in target:
                target[key] = value
            elif key in target and isinstance(target[key], list) and isinstance(value, list):
                # Replace default lists (e.g. benchmarks) with manifest values
                target[key] = value
            else:
                # Key exists in target with incompatible type or already set scalar;
                # for nested scalar dicts (like isolation settings), prefer source.
                target[key] = value
        return target

    merged = _merge(dict(defaults), dict(raw))
    # Always enforce no_production_writes
    merged.setdefault("isolation", {})["no_production_writes"] = True
    return HarnessManifest(merged)


def load_manifest(path: Path) -> HarnessManifest:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _resolve_defaults(raw)


def load_default_manifest() -> HarnessManifest:
    """Return a minimal default manifest for discovery/probe runs."""
    return HarnessManifest(_resolve_defaults({}))


def isolation_env(manifest: HarnessManifest) -> Dict[str, str]:
    """Return the environment dict that isolates the overnight run from live Titan."""
    iso = manifest.get("isolation") or {}
    base_dir = expand_manifest_path(iso.get("base_dir", str(Path.home() / ".titan-overnight")))
    return {
        "TITAN_BASE_DIR": base_dir,
        "TITAN_HOME": base_dir,
        # Explicit flag so downstream code can assert isolation
        "TITAN_OVERNIGHT_RUN": "1",
        "TITAN_OVERNIGHT_LABEL": iso.get("label", "unknown"),
    }


def manifest_defaults_path() -> Path:
    """Path to the bundled default manifest shipped with the codebase."""
    return Path(__file__).resolve().parents[2] / "config" / "overnight_manifest.yaml"
