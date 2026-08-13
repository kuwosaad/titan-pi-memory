"""
Titan-Karu benchmark manifest loader.

Provides a typed interface to tools/benchmarks/manifest.json.
No side effects on import.

Usage:
    from load_manifest import load_manifest, get_benchmark, DEFAULT_BOUNDS

    manifest = load_manifest()
    bench = get_benchmark("longmemeval-oracle")
    bounds = DEFAULT_BOUNDS
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT_DIR / "tools" / "benchmarks" / "manifest.json"


class ManifestError(Exception):
    pass


def _resolve_manifest() -> Path:
    path = os.environ.get("TITAN_BENCHMARK_MANIFEST")
    if path:
        resolved = Path(path).expanduser().resolve()
        if resolved.exists():
            return resolved
        raise ManifestError(f"TITAN_BENCHMARK_MANIFEST set but file not found: {resolved}")

    if MANIFEST_PATH.exists():
        return MANIFEST_PATH.resolve()

    raise ManifestError(
        f"manifest.json not found at {MANIFEST_PATH} "
        "(set TITAN_BENCHMARK_MANIFEST env var to override)"
    )


def load_manifest() -> Dict[str, Any]:
    path = _resolve_manifest()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest.json is not valid JSON: {exc}") from exc

    if "benchmarks" not in data:
        raise ManifestError("manifest.json is missing required 'benchmarks' key")

    return data


def get_benchmark(name: str) -> Dict[str, Any]:
    manifest = load_manifest()
    benchmarks = manifest.get("benchmarks", {})
    if name not in benchmarks:
        available = list(benchmarks.keys())
        raise ManifestError(f"unknown benchmark '{name}'. Available: {available}")
    return benchmarks[name]


def get_default_bounds() -> Dict[str, Any]:
    manifest = load_manifest()
    return dict(manifest.get("default_bounds", {}))


def get_harness_config() -> Dict[str, Any]:
    manifest = load_manifest()
    return dict(manifest.get("harness", {}))


def resolve_benchmark_path(rel: Optional[str], benchmark_name: str) -> Optional[Path]:
    """Resolve a relative path in a benchmark entry against ROOT_DIR."""
    if not rel:
        return None
    resolved = (ROOT_DIR / rel).expanduser().resolve()
    return resolved


def resolve_script_path(benchmark: Dict[str, Any]) -> Path:
    script_rel = benchmark.get("script", "")
    if not script_rel:
        raise ManifestError(f"benchmark is missing 'script' field: {benchmark.get('name', 'unknown')}")
    path = (ROOT_DIR / script_rel).expanduser().resolve()
    if not path.exists():
        raise ManifestError(f"benchmark script not found: {path}")
    return path


def resolve_dataset_path(benchmark: Dict[str, Any]) -> Path:
    dataset_rel = benchmark.get("dataset", "")
    if not dataset_rel:
        raise ManifestError(f"benchmark is missing 'dataset' field")
    path = (ROOT_DIR / dataset_rel).expanduser().resolve()
    return path


def check_benchmark_health(benchmark: Dict[str, Any]) -> Dict[str, Any]:
    """Run basic pre-flight checks for a benchmark entry. Returns dict of warnings/errors."""
    issues: Dict[str, Any] = {"errors": [], "warnings": []}

    try:
        script_path = resolve_script_path(benchmark)
    except ManifestError as exc:
        issues["errors"].append(str(exc))

    dataset_rel = benchmark.get("dataset")
    if dataset_rel:
        dataset_path = resolve_benchmark_path(dataset_rel, "")
        if not dataset_path.exists():
            issues["warnings"].append(f"dataset not found: {dataset_path}")
        elif dataset_path.stat().st_size == 0:
            issues["warnings"].append(f"dataset is empty: {dataset_path}")

    isolated_home = resolve_benchmark_path(benchmark.get("isolated_home"), "")
    if isolated_home and not isolated_home.exists():
        issues["warnings"].append(f"isolated_home dir does not exist (will be created): {isolated_home}")

    return issues


DEFAULT_BOUNDS: Dict[str, Any] = {}
try:
    DEFAULT_BOUNDS = get_default_bounds()
except ManifestError:
    pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspect the benchmark manifest")
    parser.add_argument("--check", metavar="BENCHMARK",
                        help="Run pre-flight health checks for the named benchmark")
    args = parser.parse_args()

    manifest = load_manifest()
    print(f"Manifest version: {manifest.get('version', 'unknown')}")
    print(f"Available benchmarks: {list(manifest.get('benchmarks', {}).keys())}")
    print(f"Default bounds: {DEFAULT_BOUNDS}")

    if args.check:
        bench = get_benchmark(args.check)
        health = check_benchmark_health(bench)
        print(f"\nHealth check for '{args.check}':")
        if health["errors"]:
            print(f"  ERRORS: {health['errors']}")
        if health["warnings"]:
            print(f"  WARNINGS: {health['warnings']}")
        if not health["errors"] and not health["warnings"]:
            print("  OK — no issues found")
