#!/usr/bin/env python3
"""Build an isolated local release before asking BB to load it."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess


def run(args, cwd, timeout):
    print("Running: " + " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install the verified build into local BB")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent.parent
    releases = Path.home() / ".bb" / "local-plugin-builds" / "titan-memory"
    # Keep Tailwind's ancestor discovery outside the working repository.
    for parent in (releases, *releases.parents):
        if (parent / ".git").exists():
            raise RuntimeError(f"Release location must be outside Git repositories: {parent}")
    release = releases / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    release.mkdir(parents=True)
    for name in ("package.json", "package-lock.json", "tsconfig.json", "README.md", "src", "assets"):
        item = source / name
        if item.is_dir():
            shutil.copytree(item, release / name)
        elif item.is_file():
            shutil.copy2(item, release / name)
    run(["npm", "ci", "--ignore-scripts", "--prefer-offline", "--no-audit", "--no-fund"], release, 180)
    run(["bb", "plugin", "build", str(release)], release, 30)
    for kind in ("server", "host", "app"):
        artifact = release / "dist" / f"{kind}.js"
        meta = json.loads((release / "dist" / f"{kind}.meta.json").read_text())
        if not artifact.stat().st_size or meta.get("pluginId") != "titan-memory":
            raise RuntimeError(f"Invalid {kind} build artifact")
    # BB checks directory mtimes as well as files. Copying files after a build
    # can otherwise cause a redundant in-server Tailwind scan on installation.
    for artifact in (release / "dist").iterdir():
        if artifact.is_file():
            artifact.touch()
    print(f"Built local release: {release}", flush=True)
    if args.install:
        run(["bb", "plugin", "install", str(release), "--yes", "--json"], release, 30)
    print("Previous release directories are preserved for rollback.", flush=True)


if __name__ == "__main__":
    main()
