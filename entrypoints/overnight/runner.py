"""
Manifest-driven runner for the Titan overnight retrieval harness.

Usage:
    python -m entrypoints.overnight.runner [--manifest PATH]

This module is the primary CLI entry point for the overnight harness.
It loads the manifest, applies isolation, runs health checks,
executes benchmarks, and logs all artifacts.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="titan-overnight",
        description="Titan-Karu overnight retrieval benchmarking harness",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest YAML. Defaults to config/overnight_manifest.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load manifest and print summary without running benchmarks",
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=None,
        dest="max_hours",
        help="Override runtime.max_hours from manifest",
    )
    parser.add_argument(
        "--isolation-dir",
        type=Path,
        default=None,
        dest="isolation_dir",
        help="Override isolation.base_dir (TITAN_BASE_DIR for this run)",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Override isolation.label for this run",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override artifacts.log_level",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from .manifest import HarnessManifest, isolation_env, load_manifest, manifest_defaults_path

    args = parse_args(argv)

    # Resolve manifest path
    manifest_path = args.manifest
    if manifest_path is None:
        default_path = manifest_defaults_path()
        if default_path.exists():
            manifest_path = default_path
        else:
            print(
                f"[titan-overnight] No manifest specified and no default at {default_path}.",
                file=sys.stderr,
            )
            print("[titan-overnight] Use --manifest to specify a manifest YAML file.", file=sys.stderr)
            return 1

    # Load manifest
    try:
        manifest: HarnessManifest = load_manifest(manifest_path)
    except FileNotFoundError:
        print(f"[titan-overnight] Manifest not found: {manifest_path}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[titan-overnight] Failed to load manifest: {exc}", file=sys.stderr)
        return 1

    # Apply CLI overrides to manifest
    if args.max_hours is not None:
        manifest.setdefault("runtime", {})["max_hours"] = args.max_hours
    if args.isolation_dir is not None:
        manifest.setdefault("isolation", {})["base_dir"] = str(args.isolation_dir.expanduser())
    if args.label is not None:
        manifest.setdefault("isolation", {})["label"] = args.label
    if args.log_level is not None:
        manifest.setdefault("artifacts", {})["log_level"] = args.log_level

    print(f"[titan-overnight] Manifest loaded: {manifest_path}")
    print(f"[titan-overnight]   version:   {manifest.get('version')}")
    print(f"[titan-overnight]   isolation: {manifest.get('isolation', {}).get('base_dir')}")
    print(f"[titan-overnight]   label:     {manifest.get('isolation', {}).get('label')}")
    print(f"[titan-overnight]   max_hours: {manifest.get('runtime', {}).get('max_hours')}")
    print(f"[titan-overnight]   benchmarks: {len(manifest.get('benchmarks', []))}")

    if args.dry_run:
        print("[titan-overnight] Dry-run: skipping benchmark execution")
        return 0

    # Apply isolation env before importing benchmark/app code so storage paths bind correctly.
    for key, value in isolation_env(manifest).items():
        os.environ[key] = value

    from .benchmark import run_overnight_benchmark

    result = run_overnight_benchmark(manifest=manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
