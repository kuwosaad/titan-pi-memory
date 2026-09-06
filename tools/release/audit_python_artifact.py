#!/usr/bin/env python3
"""Reject private or development-only files from Python release artifacts."""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator


FORBIDDEN_PREFIXES = (
    "config/overnight",
    "docs/research/",
    "entrypoints/overnight/",
    "out/",
    "tests/",
    "tools/benchmarks/",
    "tools/dev/",
    "tools/presentations/",
    "tools/scripts/",
    "traces/",
)

FORBIDDEN_FILE_PATTERNS = (
    re.compile(r"(^|/)(?:memory_store|memories|scenes|sessions|traces?)\.(?:db|json|jsonl|sqlite3?)$", re.I),
    re.compile(r"\.(?:pem|key|p12|pfx)$", re.I),
    re.compile(r"(^|/)(?:AGENTS|CONTEXT)\.md$", re.I),
)

FORBIDDEN_TEXT_PATTERNS = (
    ("macOS home path", re.compile(r"/Users/[A-Za-z0-9._-]+/")),
    ("Linux home path", re.compile(r"/home/[A-Za-z0-9._-]+/")),
    ("Windows home path", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\", re.I)),
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("personal preference example", re.compile(r"Kuwo is a beginner learning Python", re.I)),
    ("personal preference example", re.compile(r"Kuwo prefers direct instructions", re.I)),
    ("personal identity example", re.compile(r"arbitrary karu\.md", re.I)),
    ("OpenAI-style secret", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("GitHub-style secret", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Google-style secret", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}", re.I)),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "credential assignment",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)\s*[:=]\s*"
            r"(?!(?:[\"']?)(?:YOUR(?:_[A-Z0-9]+)*|<[^>]+>|\$\{[^}]+\}|\[REDACTED\])(?:[\"']?))"
            r"(?:[\"'][^\"'\n]{12,}[\"']|[A-Za-z0-9][A-Za-z0-9./+=:-]{11,})",
            re.I,
        ),
    ),
)

FOUNDER_TERM_PATTERN = re.compile(r"\b(?:Kuwo|Karu|Saad|Mohammad|Ayanokoji)\b", re.I)
ALLOWED_LEGACY_REFERENCES = {
    "app/save_pipeline/pipeline.py": (re.compile(r"openclaw-hook:titan-karu-bridge", re.I),),
    "tools/cli/titan.py": (
        re.compile(r"titan-memory@titan-karu-lab", re.I),
        re.compile(r'"titan-karu-lab"', re.I),
    ),
}


class UnsafeArtifactPath(ValueError):
    """An archive member is not a safe relative artifact path."""


def _relative_name(name: str) -> str:
    raw = str(name).replace("\\", "/")
    raw_parts = raw.split("/")
    if (
        not raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:/", raw)
        or any(part in {".", ".."} for part in raw_parts)
    ):
        raise UnsafeArtifactPath(f"unsafe archive member path: {name}")
    normalized = PurePosixPath(raw).as_posix()
    parts = normalized.split("/", 1)
    if parts[0].startswith("titan_memory_cli-") and len(parts) == 2:
        return parts[1]
    return normalized


def _artifact_members(path: Path) -> Iterator[tuple[str, bytes]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                relative = _relative_name(info.filename)
                if not info.is_dir():
                    yield relative, archive.read(info)
        return

    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            relative = _relative_name(member.name)
            if member.isfile():
                handle = archive.extractfile(member)
                if handle is not None:
                    yield relative, handle.read()


def _artifact_paths(path: Path) -> Iterable[Path]:
    if path.is_dir():
        return sorted(
            item
            for item in path.iterdir()
            if item.is_file() and (item.name.endswith(".whl") or item.name.endswith((".tar.gz", ".tgz")))
        )
    return (path,)


def audit(path: Path) -> list[str]:
    artifacts = list(_artifact_paths(path))
    violations: list[str] = []
    for artifact in artifacts:
        try:
            for relative, data in _artifact_members(artifact):
                if relative.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{artifact.name}:{relative}: development-only path")
                if any(pattern.search(relative) for pattern in FORBIDDEN_FILE_PATTERNS):
                    violations.append(f"{artifact.name}:{relative}: private-data file type")
                if b"\x00" in data or len(data) > 2_000_000:
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for label, pattern in FORBIDDEN_TEXT_PATTERNS:
                    if pattern.search(text):
                        violations.append(f"{artifact.name}:{relative}: {label}")
                allowed_lines = ALLOWED_LEGACY_REFERENCES.get(relative, ())
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if FOUNDER_TERM_PATTERN.search(line) and not any(
                        pattern.search(line) for pattern in allowed_lines
                    ):
                        violations.append(f"{artifact.name}:{relative}:{line_number}: founder-specific text")
        except (OSError, tarfile.TarError, zipfile.BadZipFile, UnsafeArtifactPath) as error:
            violations.append(f"{artifact}: could not read artifact ({error})")
    if path.is_dir() and not artifacts:
        violations.append(f"{path}: no Python release artifacts found")
    return sorted(set(violations))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="A wheel, source archive, or directory containing them")
    args = parser.parse_args()
    violations = audit(args.artifact)
    if violations:
        print("Python release privacy audit failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print(f"Python release privacy audit passed: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
