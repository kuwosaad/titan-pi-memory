#!/usr/bin/env python3
"""Verify npm's exact file list and run the canonical package audit on it.

The privacy/content policy lives in the package's authoritative
``audit-runtime.js``.  This file only handles npm metadata, tar safety,
materializing the exact files, and bounded subprocess execution.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

SUBPROCESS_TIMEOUT_SEC = 30


class GateError(RuntimeError):
    """A release-gate finding that must block packaging."""


def _relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("package/"):
        normalized = normalized[len("package/") :]
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise GateError(f"unsafe artifact path: {path}")
    return pure.as_posix()


def _root_path(root: Path, relative: str) -> Path:
    path = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise GateError(f"path escapes package root: {relative}") from exc
    return path


def _npm_json(stdout: str) -> list[dict]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise GateError(f"npm returned non-JSON pack metadata: {exc}") from exc
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise GateError("npm returned an unexpected pack metadata shape")
    return data


def npm_command() -> str:
    name = "npm.cmd" if os.name == "nt" else "npm"
    executable = shutil.which(name) or shutil.which("npm")
    if not executable:
        raise GateError("npm executable was not found on PATH")
    return executable


def npm_pack_metadata(
    package_root: Path, *, actual: bool, destination: Path | None = None
) -> tuple[list[str], Path | None]:
    command = ["npm", "pack", "--ignore-scripts", "--json"]
    if not actual:
        command.append("--dry-run")
    elif destination is not None:
        command.extend(["--pack-destination", str(destination)])
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="titan-memory-cli-npm-cache-") as cache:
        env["npm_config_cache"] = cache
        try:
            result = subprocess.run(
                [npm_command(), *command[1:]],
                cwd=package_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=SUBPROCESS_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired as exc:
            raise GateError(f"npm pack timed out after {SUBPROCESS_TIMEOUT_SEC}s") from exc
    if result.returncode:
        raise GateError(f"npm pack failed: {(result.stderr or result.stdout).strip()}")
    metadata = _npm_json(result.stdout)
    files = metadata[0].get("files")
    if not isinstance(files, list):
        raise GateError("npm pack metadata has no files list")
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise GateError("npm pack metadata contains an invalid file entry")
        paths.append(_relative_path(item["path"]))
    archive: Path | None = None
    if actual and destination is not None:
        filename = metadata[0].get("filename")
        if isinstance(filename, str):
            archive = destination / filename
        if archive is None or not archive.is_file():
            candidates = sorted(destination.glob("*.tgz"))
            if len(candidates) != 1:
                raise GateError("npm pack did not produce a discoverable tarball")
            archive = candidates[0]
    return paths, archive


def validate_packlist(paths: Iterable[str]) -> list[str]:
    """Validate only path safety; file policy is the canonical JS audit."""
    return sorted({_relative_path(path) for path in paths})


def _materialize_paths(source_root: Path, paths: Sequence[str], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in paths:
        source = _root_path(source_root, relative)
        if not source.is_file():
            raise GateError(f"npm pack listed a missing source file: {relative}")
        target = _root_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _audit_script() -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    package_root = repo_root / "packages" / "titan-memory-cli"
    script = package_root / "scripts" / "audit-runtime.js"
    if not script.is_file():
        raise GateError(f"canonical runtime audit is missing: {script}")
    return repo_root, script


def _run_canonical_audit(package_root: Path) -> None:
    repo_root, script = _audit_script()
    try:
        result = subprocess.run(
            ["node", str(script), str(package_root)],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"canonical runtime audit timed out after {SUBPROCESS_TIMEOUT_SEC}s") from exc
    if result.returncode:
        raise GateError((result.stderr or result.stdout).strip())


def scan_archive(archive: Path) -> list[str]:
    """Extract safely, then run the one canonical audit against exact bytes."""
    paths: list[str] = []
    with tempfile.TemporaryDirectory(prefix="titan-memory-cli-audit-") as directory:
        extracted_root = Path(directory) / "package"
        extracted_root.mkdir()
        try:
            with tarfile.open(archive, "r:gz") as handle:
                for member in handle.getmembers():
                    if not member.name.startswith("package/"):
                        raise GateError(f"archive member is outside package/: {member.name}")
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise GateError(f"archive contains a non-regular member: {member.name}")
                    relative = _relative_path(member.name)
                    paths.append(relative)
                    target = _root_path(extracted_root, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = handle.extractfile(member)
                    if source is None:
                        raise GateError(f"could not read archive member: {member.name}")
                    target.write_bytes(source.read())
        except tarfile.TarError as exc:
            raise GateError(f"invalid npm tarball {archive}: {exc}") from exc
        normalized = validate_packlist(paths)
        _run_canonical_audit(extracted_root)
        return normalized


def check_source(package_root: Path) -> list[str]:
    paths, _ = npm_pack_metadata(package_root, actual=False)
    normalized = validate_packlist(paths)
    with tempfile.TemporaryDirectory(prefix="titan-memory-cli-source-audit-") as directory:
        exact_root = Path(directory) / "package"
        _materialize_paths(package_root, normalized, exact_root)
        _run_canonical_audit(exact_root)
    return normalized


def check_pack(package_root: Path) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="titan-memory-cli-pack-") as directory:
        destination = Path(directory)
        paths, archive = npm_pack_metadata(package_root, actual=True, destination=destination)
        if archive is None:
            raise GateError("npm pack did not return a tarball")
        packlist = validate_packlist(paths)
        archive_paths = scan_archive(archive)
        if packlist != archive_paths:
            raise GateError("npm metadata file list differs from the actual tarball")
        return archive_paths


def resolve_package_root(root: Path) -> Path:
    candidate = root / "packages" / "titan-memory-cli"
    if (candidate / "package.json").is_file():
        return candidate
    if (root / "package.json").is_file():
        return root
    raise GateError(f"npm package root is missing: {root}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", action="store_true", help="audit npm's exact source pack list")
    mode.add_argument("--pack", action="store_true", help="create and audit an actual temporary npm tarball")
    mode.add_argument("--artifact", type=Path, help="audit an existing npm tarball")
    parser.add_argument("--root", type=Path, help="canonical repository root or npm package root")
    parser.add_argument("--quiet", action="store_true", help="suppress the success message")
    args = parser.parse_args(argv)
    try:
        package_root = resolve_package_root((args.root or Path(__file__).resolve().parents[1]).resolve())
        if args.source:
            paths, mode_name = check_source(package_root), "source pack list"
        elif args.pack:
            paths, mode_name = check_pack(package_root), "tarball"
        else:
            paths, mode_name = scan_archive(args.artifact.resolve()), "existing tarball"
    except (GateError, OSError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print(f"package gate failed: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"package gate passed: {mode_name} ({len(paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
