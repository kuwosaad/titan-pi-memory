#!/usr/bin/env python3
"""Pack, install, and exercise the real npm artifact in isolated state.

This verifier deliberately runs the installed CLI from a temporary working
folder with no inherited Python import path.  A source checkout must not be
able to make an incomplete artifact look healthy.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

# Most verifier subprocesses and the warm MCP protocol path should stay short.
# The first CLI invocation is different: titan.js lazily creates a venv and
# installs the bundled requirements before it can answer list-tools.
WARM_STARTUP_TIMEOUT_SEC = 30
COLD_BOOTSTRAP_TIMEOUT_SEC = 5 * 60
SUBPROCESS_TIMEOUT_SEC = WARM_STARTUP_TIMEOUT_SEC
DIAGNOSTIC_LIMIT = 8 * 1024
REQUIRED_MCP_TOOLS = {
    "store_trace_packet",
    "store_trace_event",
    "query_memories",
    "get_scene_context",
    "get_recent_memories",
    "doctor",
    "inspect_clusters",
    "analyze_clusters",
    "patterns_status",
    "patterns_list",
    "pattern_get",
    "pattern_create",
    "pattern_accept",
    "pattern_reject",
    "patterns_evidence_packet",
    "patterns_mark_processed",
    "patterns_export_bundle",
    "patterns_import_bundle",
}


def _diagnostics(stdout: object, stderr: object) -> str:
    """Render bounded child diagnostics, including timeout partial output."""
    details: list[str] = []
    for name, value in (("stderr", stderr), ("stdout", stdout)):
        if value is None:
            continue
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        text = str(value).strip()
        if text:
            if len(text) > DIAGNOSTIC_LIMIT:
                text = text[-DIAGNOSTIC_LIMIT:]
                text = f"[...truncated...]\n{text}"
            details.append(f"{name}: {text}")
    return "\n".join(details)


def run_checked(
    command: list[str], *, cwd: Path, env: Mapping[str, str], timeout: float = SUBPROCESS_TIMEOUT_SEC
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _diagnostics(exc.stdout, exc.stderr)
        if detail:
            detail = f"\n{detail}"
        raise RuntimeError(
            f"command timed out after {timeout:g}s: {' '.join(command)}{detail}"
        ) from exc
    if result.returncode:
        detail = _diagnostics(result.stdout, result.stderr)
        if detail:
            detail = f"\n{detail}"
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}{detail}")
    return result


def clean_environment(state_root: Path) -> dict[str, str]:
    """Remove ambient Python/Titan overrides and bind Python to this verifier."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("TITAN_") or key.startswith("PYTHON"):
            env.pop(key, None)
    # Do not replace HOME: the verifier uses explicit Titan homes and caches.
    # Keep the active interpreter path verbatim.  Resolving a venv's launcher
    # symlink points at the base Python and drops dependency-complete packages
    # such as ``yaml`` when the artifact intentionally skips its own venv.
    env["PYTHON"] = sys.executable
    env.update(
        {
            "npm_config_cache": str(state_root / "npm-cache"),
            "PIP_CACHE_DIR": str(state_root / "pip-cache"),
            "XDG_CACHE_HOME": str(state_root / "xdg-cache"),
            "TITAN_HOME": str(state_root / "titan-home"),
            "TITAN_BASE_DIR": str(state_root / "titan-base"),
            "TITAN_SPOOL_DIR": str(state_root / "titan-spool"),
            "TITAN_RUNTIME_HOME": str(state_root / "runtime-home"),
            "TITAN_RUNTIME_MANIFEST": str(state_root / "runtime-home" / "current.json"),
            "TITAN_AUTO_INGEST_ENABLED": "0",
        }
    )
    return env


def resolve_package_root(root: Path) -> tuple[Path, Path]:
    if (root / "packages" / "titan-memory-cli" / "package.json").is_file():
        return root, root / "packages" / "titan-memory-cli"
    if (root / "package.json").is_file():
        return root.parent.parent if root.parent.name == "packages" else root, root
    raise RuntimeError(f"npm package root is missing: {root}")


def _parse_npm_metadata(stdout: str) -> list[dict]:
    # npm lifecycle hooks may write status lines before --json metadata.
    for offset, character in enumerate(stdout):
        if character != "[":
            continue
        try:
            metadata = json.loads(stdout[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(metadata, list) and metadata and isinstance(metadata[0], dict):
            return metadata
    raise RuntimeError(f"npm pack returned unexpected metadata: {stdout!r}")


def npm_command() -> str:
    name = "npm.cmd" if os.name == "nt" else "npm"
    executable = shutil.which(name) or shutil.which("npm")
    if not executable:
        raise RuntimeError("npm executable was not found on PATH")
    return executable


def pack_repository(package_root: Path, destination: Path, env: Mapping[str, str]) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    result = run_checked(
        [npm_command(), "pack", "--json", "--pack-destination", str(destination)],
        cwd=package_root,
        env=env,
    )
    try:
        filename = _parse_npm_metadata(result.stdout)[0]["filename"]
    except (IndexError, KeyError, TypeError) as exc:
        raise RuntimeError(f"npm pack returned unexpected metadata: {result.stdout!r}") from exc
    archive = destination / filename
    if not archive.is_file():
        raise RuntimeError(f"npm pack did not create {archive}")
    return archive


def archive_file_count(archive: Path) -> int:
    with tarfile.open(archive, "r:gz") as handle:
        return sum(1 for member in handle.getmembers() if member.isfile())


def _read_stream(stream, output: queue.Queue[tuple[str, str | None]], name: str) -> None:
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            output.put((name, line))
    finally:
        output.put((name, None))


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        else:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def mcp_stdio_handshake(
    command: list[str], *, cwd: Path, env: Mapping[str, str], timeout: float = WARM_STARTUP_TIMEOUT_SEC
) -> list[str]:
    """Perform initialize and tools/list, with bounded group cleanup and drains."""
    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": dict(env),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "bufsize": 1,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    process: subprocess.Popen[str] | None = None
    output: queue.Queue[tuple[str, str | None]] = queue.Queue()
    readers: list[threading.Thread] = []
    stderr_lines: list[str] = []
    effective_timeout = min(timeout, WARM_STARTUP_TIMEOUT_SEC)
    deadline = time.monotonic() + effective_timeout
    try:
        process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            reader = threading.Thread(target=_read_stream, args=(stream, output, name), daemon=True)
            reader.start()
            readers.append(reader)

        def request(payload: dict, expected_id: int) -> dict:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"MCP handshake timed out after {effective_timeout:g}s")
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"MCP handshake timed out after {effective_timeout:g}s")
                try:
                    stream_name, line = output.get(timeout=remaining)
                except queue.Empty as exc:
                    raise RuntimeError(f"MCP handshake timed out after {effective_timeout:g}s") from exc
                if stream_name == "stderr":
                    if line:
                        stderr_lines.append(line)
                    continue
                if line is None:
                    raise RuntimeError("MCP process exited before responding")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != expected_id:
                    continue
                if "error" in message:
                    raise RuntimeError(f"MCP request failed: {message['error']}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("MCP response has no result object")
                return result

        request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "titan-artifact-verifier", "version": "1"},
                },
            },
            1,
        )
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
        process.stdin.flush()
        result = request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, 2)
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list result has no tools array")
        names = [item.get("name") for item in tools if isinstance(item, dict) and isinstance(item.get("name"), str)]
        if not names:
            raise RuntimeError("MCP tools/list returned no named tools")
        return names
    except (OSError, RuntimeError, BrokenPipeError) as exc:
        detail = str(exc)
        if stderr_lines:
            detail += f"; stderr: {''.join(stderr_lines[-8:]).strip()}"
        raise RuntimeError(detail) from exc
    finally:
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except (OSError, ValueError):
                pass
            _stop_process_group(process)
        for reader in readers:
            reader.join(timeout=2)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass


def list_installed_tools(
    installed_cli: Path, *, cwd: Path, env: Mapping[str, str]
) -> set[str]:
    """List tools after the cold bootstrap, without masking command failures."""
    list_result = run_checked(
        ["node", str(installed_cli), "codex", "list-tools", "--json"],
        cwd=cwd,
        env=env,
        timeout=COLD_BOOTSTRAP_TIMEOUT_SEC,
    )
    try:
        payload = json.loads(list_result.stdout)
        raw_tools = payload["tools"]
        if not isinstance(raw_tools, list) or not all(isinstance(item, str) for item in raw_tools):
            raise TypeError("tools must be a list of names")
        return set(raw_tools)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"installed CLI returned invalid MCP tool JSON: {list_result.stdout!r}") from exc


def verify(root: Path, requested_archive: Path | None) -> tuple[str, int, int, int]:
    repo_root, package_root = resolve_package_root(root)
    with tempfile.TemporaryDirectory(prefix="titan-memory-cli-artifact-") as directory:
        temporary = Path(directory)
        env = clean_environment(temporary)
        if requested_archive:
            archive = requested_archive.resolve()
        else:
            archive = pack_repository(package_root, temporary / "artifact", env)

        gate = repo_root / "scripts" / "package_gate.py"
        run_checked([sys.executable, str(gate), "--artifact", str(archive)], cwd=temporary, env=env)

        install_prefix = temporary / "install"
        run_checked(
            [
                npm_command(), "install", "--ignore-scripts", "--no-audit", "--no-fund",
                "--prefix", str(install_prefix), str(archive),
            ],
            cwd=temporary,
            env=env,
        )
        installed_cli = install_prefix / "node_modules" / "titan-memory-cli" / "bin" / "titan.js"
        if not installed_cli.is_file():
            raise RuntimeError(f"installed CLI is missing: {installed_cli}")

        tools = list_installed_tools(installed_cli, cwd=temporary, env=env)
        missing = sorted(REQUIRED_MCP_TOOLS - tools)
        if missing:
            raise RuntimeError(f"installed CLI tool listing is missing: {', '.join(missing)}")

        handshake_tools = mcp_stdio_handshake(
            ["node", str(installed_cli), "mcp", "--agent", "codex"],
            cwd=temporary,
            env=env,
        )
        missing_handshake = sorted(REQUIRED_MCP_TOOLS - set(handshake_tools))
        if missing_handshake:
            raise RuntimeError(f"MCP handshake tool listing is missing: {', '.join(missing_handshake)}")
        return archive.name, archive.stat().st_size, archive_file_count(archive), len(handshake_tools)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, help="verify an existing npm tarball instead of packing")
    parser.add_argument("--root", type=Path, help="canonical repository root or npm package root")
    args = parser.parse_args(argv)
    try:
        artifact, size, count, handshake_count = verify(
            (args.root or Path(__file__).resolve().parents[1]).resolve(), args.artifact
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"artifact startup verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"artifact startup verification passed: {artifact} ({count} files, {size} bytes)")
    print(f"required MCP tools verified: {len(REQUIRED_MCP_TOOLS)} (+ handshake: {handshake_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
