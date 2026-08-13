from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sqlite3
import sys
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, TypedDict
from urllib.parse import urlencode
from uuid import uuid4

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_AGENT_NAME = "opencode"
CODEX_AGENT_NAME = "codex"
CODEX_PLUGIN_ID = "titan-memory@titan-local"
CODEX_PLUGIN_DIR = ROOT_DIR / "integrations" / "codex_titan_plugin"
DEFAULT_GRAPH_PORT = 8010
_explicit_titan_home = os.getenv("TITAN_HOME")
_default_home = Path.home() / ".titan"
TITAN_HOME = Path(_explicit_titan_home or str(_default_home)).expanduser()
os.environ.setdefault("TITAN_BASE_DIR", str(TITAN_HOME))

from app.storage.models import TracePacketRequest
from tools.cli import titan_voice as voice
from tools.opencode.install_plugin import InstallScope, install_opencode_plugin

_REQUIREMENTS_IMPORT_MAP = {
    "pyyaml": "yaml",
}


class EnvWriteResult(TypedDict):
    updated_keys: List[str]
    created: bool


class SmokeTestResult(TypedDict):
    ok: bool
    issues: List[str]
    detail: str
    ingest_status: str
    retrieval_count: int


class DoctorSummary(TypedDict):
    ok: bool
    issues: List[str]
    warnings: List[str]
    plugin_path: str
    trace_dir: str
    detail: str


class OpenCodeConfigPatchResult(TypedDict):
    ok: bool
    path: str
    backup_path: Optional[str]
    status: str
    detail: str


class CodexConfigPatchResult(TypedDict):
    ok: bool
    path: str
    backup_path: Optional[str]
    status: str
    detail: str


def _normalize_agent_name(agent: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", agent.strip().lower()).strip("-")
    if not normalized:
        raise ValueError("Agent name must contain at least one letter or number.")
    return normalized


def resolve_agent_titan_home(agent: Optional[str] = None) -> Path:
    if _explicit_titan_home:
        return TITAN_HOME
    if not agent:
        return TITAN_HOME
    return TITAN_HOME / "agents" / _normalize_agent_name(agent)


def bootstrap_agent_home(agent: Optional[str] = None) -> Path:
    agent_home = resolve_agent_titan_home(agent)
    agent_home.mkdir(parents=True, exist_ok=True)

    shared_env = TITAN_HOME / ".env"
    agent_env = agent_home / ".env"
    if agent and agent_home != TITAN_HOME and shared_env.exists():
        if not agent_env.exists():
            shutil.copyfile(shared_env, agent_env)
        else:
            shared_values = load_env_file(shared_env)
            agent_values = load_env_file(agent_env)
            missing_values = {key: value for key, value in shared_values.items() if value and not agent_values.get(key)}
            if missing_values:
                upsert_env_keys(agent_env, missing_values)

    return agent_home


def configure_runtime_for_agent(agent: Optional[str] = None) -> Path:
    agent_home = bootstrap_agent_home(agent)
    os.environ["TITAN_HOME"] = str(agent_home)
    os.environ["TITAN_BASE_DIR"] = str(agent_home)
    if agent:
        os.environ["TITAN_AGENT_NAME"] = _normalize_agent_name(agent)
    else:
        os.environ.pop("TITAN_AGENT_NAME", None)
    return agent_home


def resolve_agent_trace_dir(agent: Optional[str] = None) -> Path:
    return resolve_agent_titan_home(agent) / "traces"


def resolve_effective_spool_dir(agent: Optional[str] = None) -> Path:
    return Path(os.getenv("TITAN_SPOOL_DIR", str(resolve_agent_trace_dir(agent)))).expanduser()


def _normalize_yes_no(value: str) -> Optional[bool]:
    lowered = value.strip().lower()
    if lowered in {"y", "yes"}:
        return True
    if lowered in {"n", "no", ""}:
        return False
    return None


def load_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        data[key] = value
    return data


def _parse_env_keys_from_example(path: Path) -> List[str]:
    keys: List[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key:
            keys.append(key)
    return keys


def upsert_env_keys(path: Path, updates: Dict[str, str]) -> EnvWriteResult:
    updates = {k: v for k, v in updates.items() if v}
    if not updates:
        return {"updated_keys": [], "created": not path.exists()}

    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing_map: Dict[str, int] = {}
    key_pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

    for idx, line in enumerate(existing_lines):
        match = key_pattern.match(line)
        if match:
            existing_map[match.group(1)] = idx

    updated_keys: List[str] = []
    for key, value in updates.items():
        rendered = f"{key}={value}"
        if key in existing_map:
            existing_lines[existing_map[key]] = rendered
        else:
            existing_lines.append(rendered)
        updated_keys.append(key)

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(existing_lines).rstrip() + "\n"
    path.write_text(payload, encoding="utf-8")
    return {"updated_keys": updated_keys, "created": not bool(existing_map)}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    return content or {}


def _resolve_current_backend_key_env(config: dict, config_name: str) -> Dict[str, Optional[str]]:
    current = config.get("current")
    if not isinstance(current, str) or not current:
        return {"backend": None, "required_env": None, "warning": f"{config_name} current backend not set"}

    backend_cfg = config.get(current)
    if not isinstance(backend_cfg, dict):
        return {"backend": current, "required_env": None, "warning": f"{config_name} backend '{current}' missing config block"}

    env_name = backend_cfg.get("api_key_env")
    if isinstance(env_name, str) and env_name:
        return {"backend": current, "required_env": env_name, "warning": None}
    if current == "ollama":
        return {"backend": current, "required_env": None, "warning": None}
    return {
        "backend": current,
        "required_env": None,
        "warning": f"{config_name} backend '{current}' has no api_key_env; test may fail until credentials are configured",
    }


def get_required_provider_envs(root_dir: Path, env: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    env = env or {}
    extraction_path = Path(env.get("TITAN_EXTRACTION_CONFIG_PATH", "")).expanduser() if env.get("TITAN_EXTRACTION_CONFIG_PATH") else root_dir / "config" / "extraction_models.yaml"
    embedding_path = Path(env.get("TITAN_EMBEDDING_CONFIG_PATH", "")).expanduser() if env.get("TITAN_EMBEDDING_CONFIG_PATH") else root_dir / "config" / "embedding_models.yaml"
    extraction_cfg = _load_yaml(extraction_path)
    embedding_cfg = _load_yaml(embedding_path)

    extraction_info = _resolve_current_backend_key_env(extraction_cfg, "extraction")
    embedding_info = _resolve_current_backend_key_env(embedding_cfg, "embedding")

    required = []
    warnings = []
    for info in (extraction_info, embedding_info):
        env_name = info.get("required_env")
        warning = info.get("warning")
        if isinstance(env_name, str):
            required.append(env_name)
        if isinstance(warning, str):
            warnings.append(warning)

    return {
        "required_envs": sorted(set(required)),
        "warnings": warnings,
        "extraction_backend": extraction_info.get("backend"),
        "embedding_backend": embedding_info.get("backend"),
        "extraction_config_path": str(extraction_path),
        "embedding_config_path": str(embedding_path),
    }


def verify_python_dependencies(requirements_path: Path) -> List[str]:
    missing: List[str] = []
    deps: List[str] = []
    if requirements_path.exists():
        for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            package = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
            if package:
                deps.append(package)
    if not deps:
        deps = ["fastapi", "uvicorn", "requests", "pyyaml", "numpy", "networkx", "pydantic", "mcp"]
    for package in deps:
        module_name = _REQUIREMENTS_IMPORT_MAP.get(package.lower(), package.replace("-", "_"))
        if importlib.util.find_spec(module_name) is None:
            missing.append(package)
    return missing


AGENT_CONFIG_PATHS = {
    "opencode": [
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".config" / "opencode" / "config.json",
    ],
    "claude-code": [
        Path.home() / ".claude" / "projects",
        Path.home() / ".config" / "claude-code" / "settings.json",
    ],
}


def _find_agent_config(agent: str) -> tuple[Optional[Path], bool]:
    """
    Find the config file for the given agent.
    Returns (path, confirmed) where confirmed=True means the user already confirmed.
    """
    normalized = _normalize_agent_name(agent)
    candidates = AGENT_CONFIG_PATHS.get(normalized, [])

    found_path: Optional[Path] = None
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            found_path = candidate
            break

    if found_path is None:
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                found_path = candidate
                break

    return found_path, False


def _check_required_ollama_models(models: List[str]) -> tuple[bool, List[str]]:
    seen: set[str] = set()
    for model in models:
        if not model or model in seen:
            continue
        seen.add(model)
        ok, guidance = _check_ollama_status(model)
        if not ok:
            return ok, guidance
    return True, []


def _check_ollama_status(required_embedding_model: str = "nomic-embed-text:v1.5") -> tuple[bool, List[str]]:
    """
    Check if Ollama is running and has the required model.
    Returns (ok, guidance_lines) where guidance_lines tells the user how to fix.
    """
    import subprocess

    guidance: List[str] = []

    sock_ok = False
    try:
        with socket.create_connection(("localhost", 11434), timeout=3.0) as sock:
            sock_ok = True
    except OSError:
        pass

    if not sock_ok:
        guidance.append("I don't see Ollama running on your system.")
        guidance.append("1. Install it: brew install ollama  (Mac)")
        guidance.append("   or: curl -fsSL https://ollama.ai/install.sh | sh  (Linux)")
        guidance.append("2. Start it: ollama serve")
        guidance.append("3. Pull the model: ollama pull " + required_embedding_model)
        return False, guidance

    list_ok = False
    installed_models: List[str] = []
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            list_ok = True
            for line in result.stdout.splitlines()[1:]:
                line = line.strip()
                if line:
                    installed_models.append(line.split()[0])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if required_embedding_model not in installed_models:
        guidance.append(f"You're running Ollama, but I need: {required_embedding_model}")
        guidance.append(f"Run: ollama pull {required_embedding_model}")
        return False, guidance

    return True, []


def _get_ollama_installed_models() -> List[str]:
    import subprocess

    try:
        with socket.create_connection(("localhost", 11434), timeout=2.0):
            pass
    except OSError:
        return []

    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=8)
        if result.returncode != 0:
            return []
        models: List[str] = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if line:
                models.append(line.split()[0])
        return models
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _prompt_api_key_for_provider(provider: str, key_env: str, agent_home: Path) -> bool:
    from app.storage.sessions import BASE_DIR

    if os.environ.get(key_env):
        return True
    agent_env = _read_agent_effective_env(agent_home, {})
    if agent_env.get(key_env):
        os.environ[key_env] = agent_env[key_env]
        return True

    import tools.cli.titan_voice as voice
    value = voice.api_key_prompt(provider)
    if not value:
        voice.warn(f"I won't be able to use {provider} without a key.")
        return False
    os.environ[key_env] = value
    upsert_env_keys(agent_home / ".env", {key_env: value})
    voice.key_saved(provider)
    return True


def _ollama_model_picker(title: str, recommended: List[tuple[str, str]], agent_home: Path) -> Optional[str]:
    import subprocess
    import tools.cli.titan_voice as voice

    installed = _get_ollama_installed_models()

    options: List[tuple[str, str]] = []
    seen = set()
    for name, desc in recommended:
        if name not in seen:
            exists = name in installed
            label = f"{desc} {CHECK if exists else '(not installed — I can download it)'}"
            options.append((name, label))
            seen.add(name)
    for installed_name in installed:
        if installed_name not in seen:
            options.append((installed_name, f"{installed_name} (installed)"))
            seen.add(installed_name)

    voice.section(title)
    for i, (_key, desc) in enumerate(options, 1):
        voice.info(f"[{i}] {desc}")
    voice.info("[c] Type a custom model name")

    def _pull_model(name: str) -> bool:
        voice.step(f"Downloading {name}...")
        try:
            subprocess.run(["ollama", "pull", name], check=True, text=True, timeout=300)
            voice.check(True, f"{name} is ready")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            voice.warn(f"Could not download {name}. Run: ollama pull {name}")
            return False

    while True:
        raw = voice.prompt("Pick a model number, or type a model name:")
        if not raw:
            return recommended[0][0] if recommended else None
        if raw.lower() == "c":
            custom = voice.prompt("Enter model name:")
            if custom:
                return custom
            continue
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                chosen = options[idx][0]
                if chosen not in installed:
                    _pull_model(chosen)
                return chosen
        except ValueError:
            pass
        if raw and ":" not in raw:
            raw = raw.strip()
            if raw not in installed:
                _pull_model(raw)
            return raw
        voice.info("Hmm, pick a number or type a name.")


def generate_mcp_block(agent: str = DEFAULT_AGENT_NAME, mode: str = "stdio") -> str:
    if mode != "stdio":
        raise ValueError(f"Unsupported mode: {mode}")

    normalized_agent = _normalize_agent_name(agent)
    command = generate_agent_runtime_command(normalized_agent)
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "titan-memory": {
                "type": "local",
                "command": command,
                "enabled": True,
            }
        },
    }
    return json.dumps(payload, indent=2)


def generate_agent_runtime_command(agent: str = DEFAULT_AGENT_NAME) -> List[str]:
    normalized_agent = _normalize_agent_name(agent)
    wrapper_command = os.getenv("TITAN_CLI_WRAPPER_COMMAND")
    if not wrapper_command and shutil.which("titan"):
        wrapper_command = "titan"
    if wrapper_command:
        return [wrapper_command, "mcp", "--agent", normalized_agent]
    return ["python3", str(ROOT_DIR / "tools" / "cli" / "titan.py"), "mcp", "--agent", normalized_agent]


def _doctor_command_for_agent(agent: str = DEFAULT_AGENT_NAME) -> str:
    normalized_agent = _normalize_agent_name(agent)
    if normalized_agent == CODEX_AGENT_NAME:
        return "titan codex doctor"
    if normalized_agent == DEFAULT_AGENT_NAME:
        return "titan doctor"
    return f"titan doctor {normalized_agent}"


def generate_agent_connection_guide(example_agent: str = DEFAULT_AGENT_NAME) -> str:
    normalized_agent = _normalize_agent_name(example_agent)
    mcp_block = generate_mcp_block(agent=normalized_agent, mode="stdio")
    return "\n".join(
        [
            "Paste this into OpenCode config, then restart OpenCode:",
            "",
            "```json",
            mcp_block,
            "```",
            "",
            "After restart, use OpenCode normally.",
            f"Titan will store this agent's traces under {resolve_agent_trace_dir(normalized_agent)}.",
            f"Run `{_doctor_command_for_agent(normalized_agent)}` any time to verify setup.",
        ]
    )


def generate_graph_url(*, host: str = "127.0.0.1", port: int = DEFAULT_GRAPH_PORT, session_id: Optional[str] = None) -> str:
    base = f"http://{host}:{port}/graph"
    if not session_id:
        return base
    return f"{base}?{urlencode({'session_id': session_id})}"


def generate_pattern_graph_url(*, host: str = "127.0.0.1", port: int = DEFAULT_GRAPH_PORT, limit: Optional[int] = None) -> str:
    base = f"http://{host}:{port}/pattern-graph"
    if limit is None:
        return base
    return f"{base}?{urlencode({'limit': limit})}"


def _is_tcp_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _select_graph_port(host: str, preferred_port: int, attempts: int = 20) -> tuple[int, bool]:
    if _is_tcp_port_available(host, preferred_port):
        return preferred_port, False
    for port in range(preferred_port + 1, preferred_port + attempts + 1):
        if _is_tcp_port_available(host, port):
            return port, True
    raise RuntimeError(f"No free port found starting from {preferred_port} on {host}.")


def run_graph(
    *,
    agent: str = DEFAULT_AGENT_NAME,
    session_id: Optional[str] = None,
    open_browser: bool = False,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GRAPH_PORT,
) -> int:
    normalized_agent = _normalize_agent_name(agent)
    agent_home = configure_runtime_for_agent(normalized_agent)
    selected_port, used_fallback_port = _select_graph_port(host, port)
    url = generate_graph_url(host=host, port=selected_port, session_id=session_id)

    from app.storage.memories import get_memory_count, list_memory_session_ids

    memory_count = get_memory_count(session_id=session_id)
    session_ids = list_memory_session_ids(limit=5)

    print(f"[titan] Starting graph server for agent '{normalized_agent}'")
    print(f"[titan] Agent home: {agent_home}")
    print(f"[titan] Memory count: {memory_count}")
    if session_ids:
        preview = ", ".join(session_ids[:5])
        print(f"[titan] Sessions: {preview}")
    if used_fallback_port:
        print(f"[titan] Port {port} is busy. Using {selected_port} instead.")
    print(f"[titan] Graph URL: {url}")
    if session_id:
        print(f"[titan] Viewing session: {session_id}")
    if open_browser:
        webbrowser.open(url)

    import uvicorn

    os.environ.setdefault("TITAN_AUTO_INGEST_ENABLED", "0")
    try:
        uvicorn.run("entrypoints.main:app", host=host, port=selected_port, reload=False)
    except KeyboardInterrupt:
        print("[titan] Graph server stopped.")
    return 0


def run_pattern_graph(
    *,
    agent: str = DEFAULT_AGENT_NAME,
    open_browser: bool = False,
    host: str = "127.0.0.1",
    port: int = DEFAULT_GRAPH_PORT,
    limit: int = 500,
) -> int:
    normalized_agent = _normalize_agent_name(agent)
    agent_home = configure_runtime_for_agent(normalized_agent)
    selected_port, used_fallback_port = _select_graph_port(host, port)
    url = generate_pattern_graph_url(host=host, port=selected_port, limit=limit)

    from app.patterns.store import PatternStore
    from app.storage.memories import _resolve_sqlite_path

    db_path = _resolve_sqlite_path()
    PatternStore(db_path)  # ensures pattern tables exist before counting
    with sqlite3.connect(db_path) as conn:
        accepted_count = conn.execute("SELECT COUNT(*) FROM patterns WHERE status = 'accepted'").fetchone()[0]
        candidate_count = conn.execute("SELECT COUNT(*) FROM patterns WHERE status = 'candidate'").fetchone()[0]

    print(f"[titan] Starting pattern graph server for agent '{normalized_agent}'")
    print(f"[titan] Agent home: {agent_home}")
    print(f"[titan] Patterns: {accepted_count} accepted, {candidate_count} candidate")
    if used_fallback_port:
        print(f"[titan] Port {port} is busy. Using {selected_port} instead.")
    print(f"[titan] Pattern graph URL: {url}")
    if open_browser:
        webbrowser.open(url)

    import uvicorn

    os.environ.setdefault("TITAN_AUTO_INGEST_ENABLED", "0")
    try:
        uvicorn.run("entrypoints.main:app", host=host, port=selected_port, reload=False)
    except KeyboardInterrupt:
        print("[titan] Pattern graph server stopped.")
    return 0


def run_onboarding_smoke_test(
    session_id: Optional[str] = None,
    plugin_path: Optional[Path] = None,
    agent: Optional[str] = None,
) -> SmokeTestResult:
    issues: List[str] = []
    test_session = session_id or f"titan-init-{uuid4().hex[:8]}"

    configure_runtime_for_agent(agent)

    if plugin_path is not None and not plugin_path.exists():
        issues.append("plugin path issue")

    try:
        import entrypoints.main  # noqa: F401
    except Exception:
        issues.append("server startup issue")
        return {
            "ok": False,
            "issues": issues,
            "detail": "Failed to import server entrypoint.",
            "ingest_status": "error",
            "retrieval_count": 0,
        }

    try:
        from app.save_pipeline.pipeline import handle_trace_packet, retrieve_memory_brief

        ingest = handle_trace_packet(
            TracePacketRequest(
                goal="Verify Titan init save/retrieve pipeline",
                thoughts="Smoke test trace packet",
                tool_calls=[],
                outcome="Stored onboarding validation memory.",
                session_id=test_session,
                save_intent=True,
                intent_phrase="onboarding smoke test",
                context={"source": "titan_init"},
            )
        )
        ingest_status = str(ingest.get("memory_status") or "unknown")
    except Exception as exc:
        message = str(exc).lower()
        if "missing required env var" in message or "api key" in message:
            issues.append("env key issue")
        else:
            issues.append("server startup issue")
        return {
            "ok": False,
            "issues": issues,
            "detail": f"Ingest failed: {exc}",
            "ingest_status": "error",
            "retrieval_count": 0,
        }

    try:
        result = retrieve_memory_brief(
            query="onboarding validation memory",
            session_id=test_session,
            mode="both",
            limit=5,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "missing required env var" in message or "api key" in message:
            issues.append("env key issue")
        else:
            issues.append("server startup issue")
        return {
            "ok": False,
            "issues": issues,
            "detail": f"Retrieve failed: {exc}",
            "ingest_status": ingest_status,
            "retrieval_count": 0,
        }

    count = int(result.get("count") or 0)
    brief = str(result.get("brief") or "").strip()
    if count <= 0 and not brief:
        causes: List[str] = []
        if ingest_status in ("error", "unknown"):
            causes.append("brain could not extract memories")
        if ingest_status == "stored":
            causes.append("memory was saved but retrieval came back empty")
        cause = ", ".join(causes) if causes else "no memories found"
        issues.append(f"retrieval returned empty: {cause} (ingest: {ingest_status})")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "detail": "Smoke test passed." if not issues else "Smoke test finished with issues.",
        "ingest_status": ingest_status,
        "retrieval_count": count,
    }


def run_connected_loop_test(
    spool_dir: Optional[Path] = None,
    plugin_path: Optional[Path] = None,
    agent: Optional[str] = None,
) -> SmokeTestResult:
    issues: List[str] = []
    configure_runtime_for_agent(agent)
    if plugin_path is not None and not plugin_path.exists():
        issues.append("plugin path issue")

    try:
        import entrypoints.main  # noqa: F401
    except Exception:
        issues.append("server startup issue")
        return {
            "ok": False,
            "issues": issues,
            "detail": "Failed to import server entrypoint.",
            "ingest_status": "error",
            "retrieval_count": 0,
        }

    from app.save_pipeline.auto_ingest import discover_spool_sessions
    from app.save_pipeline.pipeline import ingest_spool_session, retrieve_memory_brief
    from app.storage.memories import get_recent_memories

    effective_spool_dir = spool_dir or resolve_effective_spool_dir(agent)
    sessions = discover_spool_sessions(effective_spool_dir)
    if not sessions:
        return {
            "ok": False,
            "issues": ["no plugin events found"],
            "detail": f"No OpenCode events found in {effective_spool_dir}. Open OpenCode, send one message, then rerun `{_doctor_command_for_agent(agent or DEFAULT_AGENT_NAME)}`.",
            "ingest_status": "error",
            "retrieval_count": 0,
        }

    # Pick most recently touched session spool file.
    sessions.sort(
        key=lambda sid: (effective_spool_dir / f"{sid}.jsonl").stat().st_mtime if (effective_spool_dir / f"{sid}.jsonl").exists() else 0.0,
        reverse=True,
    )
    session_id = sessions[0]

    max_batches_raw = os.getenv("TITAN_CONNECTED_TEST_MAX_BATCHES", "10")
    try:
        max_batches = max(1, int(max_batches_raw))
    except ValueError:
        max_batches = 10

    aggregate = {
        "processed_events": 0,
        "prompt_candidates": 0,
        "stored_memories": 0,
        "fallback_memories": 0,
        "queued_retries": 0,
        "skipped_low_signal": 0,
    }
    processed_sessions: set[str] = set()
    batches_processed = 0
    last_ingest_result: Dict[str, object] = {}

    try:
        for _ in range(max_batches):
            ingest_result = ingest_spool_session(session_id=session_id, spool_dir=str(effective_spool_dir))
            last_ingest_result = ingest_result
            batches_processed += 1

            for key in aggregate:
                aggregate[key] += int(ingest_result.get(key) or 0)
            for sid in ingest_result.get("processed_sessions") or []:
                if isinstance(sid, str) and sid:
                    processed_sessions.add(sid)

            if aggregate["stored_memories"] > 0:
                break
            if int(ingest_result.get("processed_events") or 0) <= 0:
                break
    except Exception as exc:
        message = str(exc).lower()
        if "missing required env var" in message or "api key" in message:
            issues.append("env key issue")
        else:
            issues.append("server startup issue")
        return {
            "ok": False,
            "issues": issues,
            "detail": f"Spool ingest failed: {exc}",
            "ingest_status": "error",
            "retrieval_count": 0,
        }

    candidate_sessions = [session_id] + [sid for sid in sorted(processed_sessions) if sid != session_id]
    recent = []
    retrieval_session_id = session_id
    for candidate in candidate_sessions:
        records = get_recent_memories(limit=1, session_id=candidate)
        if records:
            recent = records
            retrieval_session_id = candidate
            break

    if not recent and aggregate["stored_memories"] <= 0:
        issues.append("retrieval returned empty")
        if aggregate["prompt_candidates"] <= 0:
            detail = (
                f"No prompt candidates were found after {batches_processed} ingest batch(es). "
                "The captured events did not contain extractable user/assistant text pairs."
            )
        else:
            detail = (
                f"Prompt candidates were found ({aggregate['prompt_candidates']}) but no memories were stored after "
                f"{batches_processed} batch(es). This usually means extraction returned empty or low-signal output."
            )
        processed_sessions_list = last_ingest_result.get("processed_sessions") or candidate_sessions
        return {
            "ok": False,
            "issues": issues,
            "detail": f"{detail} Processed sessions: {processed_sessions_list}.",
            "ingest_status": "ingested",
            "retrieval_count": 0,
        }

    query = "what happened earlier"
    if recent:
        query = str(recent[0].text).strip()[:60] or query

    try:
        result = retrieve_memory_brief(
            query=query,
            session_id=retrieval_session_id,
            mode="both",
            limit=5,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "missing required env var" in message or "api key" in message:
            issues.append("env key issue")
        else:
            issues.append("server startup issue")
        return {
            "ok": False,
            "issues": issues,
            "detail": f"Retrieve failed: {exc}",
            "ingest_status": "ingested",
            "retrieval_count": 0,
        }

    count = int(result.get("count") or 0)
    brief = str(result.get("brief") or "").strip()
    if count <= 0 and not brief:
        issues.append("retrieval returned empty")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "detail": (
            f"Connected loop tested for session '{session_id}' over {batches_processed} batch(es); "
            f"retrieval session '{retrieval_session_id}'."
        ),
        "ingest_status": "ingested",
        "retrieval_count": count,
    }


def _prompt_missing_keys(missing_keys: List[str]) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    if not missing_keys:
        return updates

    print("[titan] API key setup:")
    for key in missing_keys:
        value = input(f"Enter {key} (or press Enter to skip): ").strip()
        if value:
            updates[key] = value
    return updates


def _prompt_required_provider_keys(required_keys: List[str]) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    if not required_keys:
        return updates

    print("[titan] Provider key confirmation:")
    for key in required_keys:
        while True:
            value = input(f"Enter {key} (required): ").strip()
            if value:
                updates[key] = value
                break
            print(f"[titan] {key} is required for the current backend. Please enter a value.")
    return updates


def _prompt_choice(title: str, options: List[tuple[str, str]], default: int = 1) -> str:
    print(title)
    for idx, (value, label) in enumerate(options, start=1):
        suffix = " [recommended]" if idx == default else ""
        print(f"  {idx}. {label}{suffix}")

    while True:
        raw = input(f"Choose (1-{len(options)}) [{default}]: ").strip()
        if not raw:
            return options[default - 1][0]
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        except ValueError:
            pass
        print("[titan] Invalid choice.")


def _choose_model(provider: str, models: List[tuple[str, str]], default: int = 1) -> str:
    choice = _prompt_choice(f"Choose {provider} model:", models + [("custom", "Custom model name")], default=default)
    if choice != "custom":
        return choice
    while True:
        model = input("Model name: ").strip()
        if model:
            return model
        print("[titan] Model name cannot be empty.")


def _set_config_backend_model(config: dict, backend: str, model: str) -> dict:
    updated = dict(config)
    block = dict(updated.get(backend, {}))
    block["enabled"] = True
    block["model"] = model
    updated[backend] = block
    updated["current"] = backend
    return updated


def _write_agent_model_configs(agent_home: Path, extraction_cfg: dict, embedding_cfg: dict) -> tuple[Path, Path]:
    config_dir = agent_home / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    extraction_path = config_dir / "extraction_models.yaml"
    embedding_path = config_dir / "embedding_models.yaml"
    extraction_path.write_text(yaml.safe_dump(extraction_cfg, sort_keys=False), encoding="utf-8")
    embedding_path.write_text(yaml.safe_dump(embedding_cfg, sort_keys=False), encoding="utf-8")
    return extraction_path, embedding_path


def _list_yaml_choices(cfg: dict, config_name: str) -> List[tuple[str, str, bool, Optional[str]]]:
    choices = []
    for name, block in cfg.items():
        if name == "current" or not isinstance(block, dict):
            continue
        enabled = block.get("enabled", False)
        api_key_env = block.get("api_key_env")
        model = block.get("model", "unknown")
        choices.append((name, model, enabled, api_key_env))
    return choices


def _print_model_row(name: str, model: str, enabled: bool, api_key_env: Optional[str], current_name: str) -> None:
    marker = " (active)" if name == current_name else ""
    status = "enabled" if enabled else "disabled"
    key_info = f", key={api_key_env}" if api_key_env else ""
    print(f"  [{status}] {name}{marker} — {model}{key_info}")


def run_config_show() -> int:
    extraction_cfg = _load_yaml(ROOT_DIR / "config" / "extraction_models.yaml")
    embedding_cfg = _load_yaml(ROOT_DIR / "config" / "embedding_models.yaml")
    current_extraction = extraction_cfg.get("current", "unknown")
    current_embedding = embedding_cfg.get("current", "unknown")

    print("[titan] Model configuration")
    print()
    print("Extraction models:")
    for name, model, enabled, key_env in _list_yaml_choices(extraction_cfg, "extraction"):
        _print_model_row(name, model, enabled, key_env, current_extraction)

    print()
    print("Embedding models:")
    for name, model, enabled, key_env in _list_yaml_choices(embedding_cfg, "embedding"):
        _print_model_row(name, model, enabled, key_env, current_embedding)

    print()
    provider_info = get_required_provider_envs(ROOT_DIR, env=_read_effective_env(TITAN_HOME / ".env", {}))
    required = provider_info["required_envs"]
    warnings = provider_info["warnings"]
    if required:
        print(f"Required API keys: {', '.join(required)}")
    for w in warnings:
        print(f"[titan] Warning: {w}")
    return 0


def _prompt_model_choice(prompt_prefix: str, choices: List[tuple[str, str, bool, Optional[str]]], current_name: str) -> Optional[str]:
    active_idx = None
    for i, (name, model, enabled, key_env) in enumerate(choices):
        marker = " (active)" if name == current_name else ""
        status = "enabled" if enabled else "disabled"
        key_info = f", needs {key_env}" if key_env else ""
        print(f"  {i + 1}. {name}{marker} — {model} [{status}]{key_info}")
        if name == current_name:
            active_idx = i + 1

    while True:
        raw = input(f"{prompt_prefix} (1-{len(choices)}) [{active_idx}]: ").strip()
        if not raw:
            return current_name
        try:
            idx = int(raw)
            if 1 <= idx <= len(choices):
                return choices[idx - 1][0]
        except ValueError:
            pass
        print("[titan] Invalid choice.")


def run_config_set_model() -> int:
    extraction_cfg = _load_yaml(ROOT_DIR / "config" / "extraction_models.yaml")
    embedding_cfg = _load_yaml(ROOT_DIR / "config" / "embedding_models.yaml")
    current_extraction = extraction_cfg.get("current", "unknown")
    current_embedding = embedding_cfg.get("current", "unknown")

    print("[titan] Set model configuration")
    print()

    extraction_choices = _list_yaml_choices(extraction_cfg, "extraction")
    embedding_choices = _list_yaml_choices(embedding_cfg, "embedding")

    chosen_extraction = _prompt_model_choice("Extraction model", extraction_choices, current_extraction)
    chosen_embedding = _prompt_model_choice("Embedding model", embedding_choices, current_embedding)

    extraction_cfg["current"] = chosen_extraction
    embedding_cfg["current"] = chosen_embedding

    (ROOT_DIR / "config" / "extraction_models.yaml").write_text(
        yaml.safe_dump(extraction_cfg, sort_keys=False), encoding="utf-8"
    )
    (ROOT_DIR / "config" / "embedding_models.yaml").write_text(
        yaml.safe_dump(embedding_cfg, sort_keys=False), encoding="utf-8"
    )

    print(f"[titan] Extraction model set to: {chosen_extraction}")
    print(f"[titan] Embedding model set to: {chosen_embedding}")

    extraction_key_env = next((v[3] for v in extraction_choices if v[0] == chosen_extraction), None)
    embedding_key_env = next((v[3] for v in embedding_choices if v[0] == chosen_embedding), None)

    for key_env, model_name in [(extraction_key_env, chosen_extraction), (embedding_key_env, chosen_embedding)]:
        if key_env:
            env_val = os.getenv(key_env)
            if not env_val:
                existing = None
                check_paths = [TITAN_HOME / ".env"]
                agent_name = os.getenv("TITAN_AGENT_NAME")
                if agent_name:
                    check_paths.append(TITAN_HOME / "agents" / agent_name / ".env")
                for p in check_paths:
                    if p.exists():
                        existing = load_env_file(p).get(key_env)
                        if existing:
                            break
                if not existing:
                    response = input(f"[titan] {key_env} is needed for {model_name}. Enter now (or press Enter to skip): ").strip()
                    if response:
                        upsert_env_keys(TITAN_HOME / ".env", {key_env: response})
                        print(f"[titan] Saved {key_env}")

    print("[titan] Model configuration updated.")
    return 0


def run_set_key(*, key_name: str, value: Optional[str] = None, agent: Optional[str] = None) -> int:
    import tools.cli.titan_voice as voice

    key_name = key_name.strip().upper()
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key_name):
        voice.error(f"That's not a valid key name: {key_name}")
        return 2

    key_value = value
    if not key_value:
        provider = key_name.replace("_API_KEY", "")
        voice.step(f"Updating your {provider} key")
        while True:
            entered = getpass.getpass(f"Enter {key_name}: ").strip()
            if entered:
                key_value = entered
                break
            print("[titan] Value cannot be empty.")

    if agent:
        normalized_agent = _normalize_agent_name(agent)
        agent_home = bootstrap_agent_home(normalized_agent)
        env_files = [agent_home / ".env"]
    else:
        env_files = [TITAN_HOME / ".env"]
        agents_dir = TITAN_HOME / "agents"
        if agents_dir.exists():
            env_files.extend(sorted(path / ".env" for path in agents_dir.iterdir() if path.is_dir()))

    updated_files = []
    for env_file in env_files:
        result = upsert_env_keys(env_file, {key_name: key_value})
        if result["updated_keys"]:
            updated_files.append(env_file)

    if updated_files:
        provider = key_name.replace("_API_KEY", "")
        voice.success(f"Got it — {provider} key saved.")
        return 0
    voice.error(f"Something went wrong — I couldn't save {key_name}.")
    return 1


def run_setup(
    *,
    agent: str = DEFAULT_AGENT_NAME,
    scope: InstallScope = "global",
    non_interactive: bool = False,
    yes: bool = False,
    config_path: Optional[Path] = None,
    cli_keys: Optional[List[str]] = None,
) -> int:
    import tools.cli.titan_voice as voice

    normalized_agent = _normalize_agent_name(agent)
    voice.section(f"Setting up Titan for {normalized_agent}...")

    agent_home = bootstrap_agent_home(normalized_agent)
    effective_env = _read_agent_effective_env(agent_home, {})

    if cli_keys:
        for item in cli_keys:
            if "=" not in item:
                voice.warn(f"Skipping malformed key entry (expected NAME=VALUE): {item}")
                continue
            key_name, key_value = item.split("=", 1)
            key_name = key_name.strip()
            key_value = key_value.strip()
            if not key_name or not key_value:
                continue
            os.environ[key_name] = key_value
            upsert_env_keys(agent_home / ".env", {key_name: key_value})
            voice.success(f"Key saved: {key_name}")

    voice.step("Checking Python dependencies")
    missing_deps = verify_python_dependencies(ROOT_DIR / "requirements.txt")
    if missing_deps:
        voice.check(False, f"Python dependencies — I'm missing: {', '.join(missing_deps)}")
        voice.outro_blocked(
            "I need those Python packages to run.",
            [
                f"Run: pip install -r requirements.txt",
                f"Then: titan setup {normalized_agent}",
            ],
        )
        return 1
    voice.check(True, "Python dependencies — looks good")

    voice.step("Finding OpenCode on your system")
    config_candidate: Optional[Path]
    if config_path is not None:
        config_candidate = config_path
    else:
        config_candidate, _ = _find_agent_config(normalized_agent)
    if config_candidate is None:
        voice.agent_not_found(normalized_agent)
        return 1
    voice.agent_found(normalized_agent, str(config_candidate), confirmed=True)

    provider_info: Optional[Dict[str, object]] = None
    if non_interactive:
        provider_info = get_required_provider_envs(ROOT_DIR)
        effective_env = _read_agent_effective_env(agent_home, {})
        missing_keys = [k for k in provider_info.get("required_envs", []) if not effective_env.get(k)]
        if missing_keys:
            voice.error(
                f"Missing required key(s): {', '.join(missing_keys)}",
                fix=f"titan key set {missing_keys[0]} --agent {normalized_agent}",
            )
            return 1

    extraction_cfg = _load_yaml(ROOT_DIR / "config" / "extraction_models.yaml")
    embedding_cfg = _load_yaml(ROOT_DIR / "config" / "embedding_models.yaml")

    if non_interactive:
        provider_info = provider_info or {}
        extraction_choice = str(provider_info.get("extraction_backend") or extraction_cfg.get("current", ""))
        extraction_model = extraction_cfg.get(extraction_choice, {}).get("model", "")
        embedding_choice = str(provider_info.get("embedding_backend") or embedding_cfg.get("current", ""))
        embedding_model = embedding_cfg.get(embedding_choice, {}).get("model", "")
    else:
        voice.section(
            "I need a brain to read your conversations and extract memories from them.\n"
            "Think of it as the part that understands what's happening in your chats."
        )
        extraction_choice = voice.prompt_choice(
            "How should the brain work?",
            [
                ("ollama", "Local — free, private, runs on your machine (Ollama)"),
                ("openai", "Cloud — fast, no setup, uses your API key (OpenAI)"),
                ("gemini", "Cloud — Gemini"),
                ("openrouter", "Cloud — OpenRouter"),
            ],
            default=1,
        )

        if extraction_choice == "ollama":
            extraction_model = _ollama_model_picker(
                "Pick a local brain model — installed models are marked with ✓:",
                [
                    ("llama3.1:8b", "llama3.1:8b (recommended)"),
                    ("qwen2.5:7b", "qwen2.5:7b"),
                    ("mistral", "mistral"),
                ],
                agent_home,
            ) or "llama3.1:8b"
        else:
            provider_map = {"openai": ("OpenAI", "OPENAI_API_KEY"), "gemini": ("Gemini", "GEMINI_API_KEY"), "openrouter": ("OpenRouter", "OPENROUTER_API_KEY")}
            prov_name, key_env = provider_map.get(extraction_choice, ("", ""))
            if prov_name and key_env:
                _prompt_api_key_for_provider(prov_name, key_env, agent_home)

            if extraction_choice == "openai":
                extraction_model = voice.prompt_choice(
                    "Which OpenAI model?",
                    [
                        ("gpt-4o-mini", "gpt-4o-mini (recommended)"),
                        ("gpt-4.1-mini", "gpt-4.1-mini"),
                        ("gpt-4.1", "gpt-4.1"),
                    ],
                    default=1,
                )
            elif extraction_choice == "gemini":
                extraction_model = voice.prompt_choice(
                    "Which Gemini model?",
                    [
                        ("gemini-2.5-flash", "gemini-2.5-flash (recommended)"),
                        ("gemini-2.5-pro", "gemini-2.5-pro"),
                    ],
                    default=1,
                )
            else:
                extraction_model = voice.prompt_choice(
                    "Which OpenRouter model?",
                    [
                        ("anthropic/claude-3.5-sonnet", "claude-3.5-sonnet (recommended)"),
                        ("openai/gpt-4o-mini", "gpt-4o-mini"),
                        ("google/gemini-2.5-flash", "gemini-2.5-flash"),
                    ],
                    default=1,
                )

        voice.section(
            "Now I need a way to search through your memories by meaning, not just keywords.\n"
            "That's what this next part does."
        )
        embedding_choice = voice.prompt_choice(
            "How should memory search work?",
            [
                ("ollama", "Local with Ollama (recommended — fast, free, private)"),
                ("openai", "Cloud — OpenAI embeddings"),
            ],
            default=1,
        )

        if embedding_choice == "openai":
            _prompt_api_key_for_provider("OpenAI", "OPENAI_API_KEY", agent_home)
            embedding_model = "text-embedding-3-small"
        else:
            embedding_model = _ollama_model_picker(
                "Pick a local search model — installed models are marked with ✓:",
                [
                    ("nomic-embed-text:v1.5", "nomic-embed-text:v1.5 (recommended)"),
                    ("mxbai-embed-large", "mxbai-embed-large"),
                ],
                agent_home,
            ) or "nomic-embed-text:v1.5"

    extraction_cfg = _set_config_backend_model(extraction_cfg, extraction_choice, extraction_model)
    embedding_cfg = _set_config_backend_model(embedding_cfg, embedding_choice, embedding_model)

    extraction_path, embedding_path = _write_agent_model_configs(agent_home, extraction_cfg, embedding_cfg)

    env_updates: Dict[str, str] = {
        "TITAN_EXTRACTION_CONFIG_PATH": str(extraction_path),
        "TITAN_EMBEDDING_CONFIG_PATH": str(embedding_path),
    }

    required_keys: List[str] = []
    for cfg, backend in ((extraction_cfg, extraction_choice), (embedding_cfg, embedding_choice)):
        key_name = cfg.get(backend, {}).get("api_key_env")
        if isinstance(key_name, str) and key_name and key_name not in required_keys:
            required_keys.append(key_name)

    new_env = _read_agent_effective_env(agent_home, env_updates)
    missing_keys = [key for key in required_keys if not new_env.get(key)]
    if missing_keys:
        voice.warn(f"Missing key(s): {', '.join(missing_keys)}. Your brain may not work until you set them.")
        for key in missing_keys:
            voice.info(f"Run: titan key set {key} --agent {normalized_agent}")

    _save_env_updates_for_agent(agent_home, env_updates)

    voice.step("Writing your configuration")
    voice.success(f"Config saved to {agent_home}")

    voice.step("Installing OpenCode plugin")
    install_result = install_opencode_plugin(
        scope=scope, root_dir=ROOT_DIR,
        global_config_root=config_path.parent if config_path else None,
    )
    plugin_path = Path(install_result["target_path"])
    voice.success(f"Plugin installed at {plugin_path}")

    target_config = config_path or config_candidate
    if not yes:
        if non_interactive:
            voice.warn("OpenCode config changes need approval. Rerun with --yes to patch automatically.")
            _print_manual_opencode_config(normalized_agent)
            return 1
        voice.section(
            f"I found OpenCode config at:\n  {target_config}\n\n"
            "I will add Titan's MCP server and create a backup first."
        )
        if not voice.confirm("Continue?", default_yes=True):
            voice.warn("No problem — I left your OpenCode config unchanged.")
            _print_manual_opencode_config(normalized_agent)
            return 1

    patch_result = patch_opencode_config(agent=normalized_agent, config_path=target_config)
    if patch_result["ok"]:
        voice.success(f"OpenCode config patched: {patch_result['path']}")
    else:
        voice.warn("I couldn't safely edit OpenCode config automatically.")
        _print_manual_opencode_config(normalized_agent)

    voice.step("Testing your memory brain")
    smoke = run_onboarding_smoke_test(plugin_path=plugin_path, agent=normalized_agent)
    if not smoke["ok"]:
        issue_summary = ", ".join(smoke["issues"])
        detail = str(smoke.get("detail") or "").strip()
        if detail:
            voice.warn(f"Brain not ready yet: {detail}")
        else:
            voice.warn(f"Brain not ready yet: {issue_summary}")
        voice.info(f"Everything else is wired up. Run {_doctor_command_for_agent(normalized_agent)} to check your brain.")
        return 0

    voice.outro_success(
        "You're all set!",
        [
            f"Brain: {extraction_model} ({extraction_choice})",
            f"Search: {embedding_model} ({embedding_choice})",
            f"Agent: {normalized_agent}",
        ],
        f"Restart OpenCode, send one message, then run: {_doctor_command_for_agent(normalized_agent)}\n"
        "I'll keep your memories safe from here. \U0001f9e0",
    )
    return 0


def _read_effective_env(path: Path, pending_updates: Dict[str, str]) -> Dict[str, str]:
    merged = load_env_file(path)
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    merged.update({k: v for k, v in pending_updates.items() if v})
    return merged


def _read_agent_effective_env(agent_home: Path, pending_updates: Dict[str, str]) -> Dict[str, str]:
    merged = load_env_file(TITAN_HOME / ".env")
    merged.update(load_env_file(agent_home / ".env"))
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    merged.update({k: v for k, v in pending_updates.items() if v})
    return merged


def _default_opencode_config_path() -> Path:
    return Path.home() / ".config" / "opencode" / "opencode.json"


def patch_opencode_config(*, agent: str = DEFAULT_AGENT_NAME, config_path: Optional[Path] = None) -> OpenCodeConfigPatchResult:
    target = config_path or _default_opencode_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    backup_path: Optional[Path] = None
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "path": str(target),
                "backup_path": None,
                "status": "manual_required",
                "detail": f"Could not parse existing OpenCode config: {exc}",
            }
        if not isinstance(existing, dict):
            return {
                "ok": False,
                "path": str(target),
                "backup_path": None,
                "status": "manual_required",
                "detail": "Existing OpenCode config is not a JSON object.",
            }
        backup_path = target.with_name(f"{target.name}.titan-backup")
        shutil.copyfile(target, backup_path)

    desired = json.loads(generate_mcp_block(agent=agent, mode="stdio"))
    mcp = existing.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
    mcp["titan-memory"] = desired["mcp"]["titan-memory"]
    existing["$schema"] = existing.get("$schema", desired["$schema"])
    existing["mcp"] = mcp

    before = target.read_text(encoding="utf-8") if target.exists() else None
    rendered = json.dumps(existing, indent=2) + "\n"
    if before == rendered:
        return {
            "ok": True,
            "path": str(target),
            "backup_path": str(backup_path) if backup_path else None,
            "status": "already_configured",
            "detail": "OpenCode config already contains Titan MCP bridge.",
        }

    target.write_text(rendered, encoding="utf-8")
    return {
        "ok": True,
        "path": str(target),
        "backup_path": str(backup_path) if backup_path else None,
        "status": "configured" if before is None else "updated",
        "detail": "OpenCode config now contains Titan MCP bridge.",
    }


def _default_codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def generate_codex_mcp_enable_block() -> str:
    return "\n".join(
        [
            f'[plugins."{CODEX_PLUGIN_ID}".mcp_servers."titan-memory"]',
            "enabled = true",
            'default_tools_approval_mode = "prompt"',
            "",
        ]
    )


def patch_codex_config(*, config_path: Optional[Path] = None) -> CodexConfigPatchResult:
    target = config_path or _default_codex_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    backup_path: Optional[Path] = None
    header = f'[plugins."{CODEX_PLUGIN_ID}".mcp_servers."titan-memory"]'
    desired_block = generate_codex_mcp_enable_block().rstrip()

    lines = before.splitlines()
    start_idx = next((idx for idx, line in enumerate(lines) if line.strip() == header), None)
    if start_idx is None:
        rendered = before.rstrip()
        rendered = f"{rendered}\n\n{desired_block}\n" if rendered else f"{desired_block}\n"
    else:
        end_idx = len(lines)
        for idx in range(start_idx + 1, len(lines)):
            stripped = lines[idx].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end_idx = idx
                break
        replacement = desired_block.splitlines()
        rendered = "\n".join(lines[:start_idx] + replacement + lines[end_idx:]).rstrip() + "\n"

    if rendered == before:
        return {
            "ok": True,
            "path": str(target),
            "backup_path": None,
            "status": "already_configured",
            "detail": "Codex config already enables Titan MCP.",
        }

    if target.exists():
        backup_path = target.with_name(f"{target.name}.titan-backup")
        shutil.copyfile(target, backup_path)
    target.write_text(rendered, encoding="utf-8")
    return {
        "ok": True,
        "path": str(target),
        "backup_path": str(backup_path) if backup_path else None,
        "status": "configured" if not before else "updated",
        "detail": "Codex config now enables Titan MCP.",
    }


async def _list_titan_mcp_tools() -> List[str]:
    from entrypoints import mcp_server

    tools = await mcp_server.server.list_tools()
    return [tool.name for tool in tools]


async def list_titan_mcp_tools_async(agent: str = CODEX_AGENT_NAME) -> List[str]:
    configure_runtime_for_agent(agent)
    return await _list_titan_mcp_tools()


def list_titan_mcp_tools_for_agent(agent: str = CODEX_AGENT_NAME) -> List[str]:
    configure_runtime_for_agent(agent)
    return asyncio.run(_list_titan_mcp_tools())


CODEX_REQUIRED_MCP_TOOLS = [
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
]


def _codex_plugin_files_ok() -> tuple[bool, List[str]]:
    required_paths = [
        CODEX_PLUGIN_DIR / ".codex-plugin" / "plugin.json",
        CODEX_PLUGIN_DIR / ".mcp.json",
        CODEX_PLUGIN_DIR / "hooks" / "hooks.json",
        CODEX_PLUGIN_DIR / "scripts" / "titan_codex_hook.py",
        CODEX_PLUGIN_DIR / "scripts" / "titan_first_run.py",
        CODEX_PLUGIN_DIR / "README.md",
        CODEX_PLUGIN_DIR / "PRIVACY.md",
        CODEX_PLUGIN_DIR / "TERMS.md",
        CODEX_PLUGIN_DIR / ".agents" / "plugins" / "marketplace.json",
        CODEX_PLUGIN_DIR / "skills" / "titan-memory-workflow" / "SKILL.md",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    return not missing, missing


def run_codex_list_tools(*, json_output: bool = False) -> int:
    tools = list_titan_mcp_tools_for_agent(CODEX_AGENT_NAME)
    if json_output:
        print(json.dumps({"server": "titan-memory", "count": len(tools), "tools": tools}, indent=2, sort_keys=True))
    else:
        print(f"titan-memory: {len(tools)} tools")
        for tool in tools:
            print(tool)
    return 0


def run_codex_reinstall_plugin(*, dry_run: bool = False) -> int:
    commands = [
        ["codex", "plugin", "remove", CODEX_PLUGIN_ID, "--json"],
        ["codex", "plugin", "add", CODEX_PLUGIN_ID, "--json"],
    ]
    if dry_run:
        print("[titan] Codex plugin reinstall plan:")
        for command in commands:
            print("  " + " ".join(command))
        return 0

    if shutil.which("codex") is None:
        print("[titan] Codex CLI not found on PATH.")
        print("[titan] Install Codex, then rerun: titan codex reinstall-plugin")
        return 1

    for idx, command in enumerate(commands):
        result = subprocess.run(command, cwd=ROOT_DIR, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            if idx == 0:
                print(f"[titan] Warning: plugin remove exited {result.returncode} — plugin may not have been installed yet.")
                if result.stderr.strip():
                    print(f"  stderr: {result.stderr.strip()}")
            else:
                if result.stderr.strip():
                    print(result.stderr.strip())
                return result.returncode
    print(f"[titan] Codex plugin installed: {CODEX_PLUGIN_ID}")
    return 0


def run_codex_verify(*, config_path: Optional[Path] = None) -> int:
    config_target = config_path or _default_codex_config_path()
    trace_dir = resolve_effective_spool_dir(CODEX_AGENT_NAME)
    ok = True

    print("[titan] Codex verification")
    if shutil.which("codex"):
        print("[ok] Codex CLI found")
    else:
        ok = False
        print("[missing] Codex CLI not found on PATH")

    plugin_ok, missing_plugin_files = _codex_plugin_files_ok()
    if plugin_ok:
        print(f"[ok] Codex plugin files: {CODEX_PLUGIN_DIR}")
    else:
        ok = False
        print("[missing] Codex plugin files:")
        for path in missing_plugin_files:
            print(f"  {path}")

    if config_target.exists() and generate_codex_mcp_enable_block().splitlines()[0] in config_target.read_text(encoding="utf-8"):
        print(f"[ok] Codex config enables Titan MCP: {config_target}")
    else:
        ok = False
        print(f"[missing] Codex config enable block: {config_target}")

    if trace_dir.exists():
        trace_count = len(list(trace_dir.glob("*.jsonl")))
        print(f"[ok] Codex trace dir: {trace_dir} ({trace_count} trace file(s))")
    else:
        ok = False
        print(f"[missing] Codex trace dir: {trace_dir}")

    try:
        tools = list_titan_mcp_tools_for_agent(CODEX_AGENT_NAME)
    except Exception as exc:
        ok = False
        print(f"[missing] Titan MCP tool introspection failed: {exc}")
        tools = []
    missing_tools = [tool for tool in CODEX_REQUIRED_MCP_TOOLS if tool not in tools]
    if missing_tools:
        ok = False
        print(f"[missing] Titan MCP tools: {', '.join(missing_tools)}")
    else:
        print(f"[ok] Titan MCP tools: {len(tools)} exported")

    if ok:
        print("[titan] Codex setup looks ready. In Codex, run /mcp and /hooks to verify the live session.")
        return 0
    print("[titan] Fix: titan setup codex")
    return 1


def run_codex_doctor(*, config_path: Optional[Path] = None) -> int:
    return run_codex_verify(config_path=config_path)


def _setup_codex_model_config(agent_home: Path) -> Dict[str, str]:
    """Configure Codex model files with a simple public-install wizard."""
    import tools.cli.titan_voice as voice

    extraction_cfg = _load_yaml(ROOT_DIR / "config" / "extraction_models.yaml")
    embedding_cfg = _load_yaml(ROOT_DIR / "config" / "embedding_models.yaml")

    voice.section(
        "I need a model to read your conversations and decide what to remember.\n"
        "Pick the one you want Titan Memory to use with Codex."
    )
    provider_pick = voice.prompt_choice(
        "Which model provider should Titan use?",
        [
            ("openai", "OpenAI — simple and reliable"),
            ("anthropic", "Anthropic — Claude models via OpenRouter"),
            ("deepseek", "DeepSeek — DeepSeek models via OpenRouter"),
        ],
        default=1,
    )

    if provider_pick == "openai":
        _prompt_api_key_for_provider("OpenAI", "OPENAI_API_KEY", agent_home)
        extraction_choice = "openai"
        extraction_model = voice.prompt_choice(
            "Which OpenAI model?",
            [
                ("gpt-4o-mini", "gpt-4o-mini (recommended)"),
                ("gpt-4o", "gpt-4o"),
                ("gpt-4.1-mini", "gpt-4.1-mini"),
            ],
            default=1,
        )
    elif provider_pick == "anthropic":
        _prompt_api_key_for_provider("OpenRouter", "OPENROUTER_API_KEY", agent_home)
        extraction_choice = "openrouter"
        extraction_model = voice.prompt_choice(
            "Which Anthropic model?",
            [
                ("anthropic/claude-sonnet-4", "Claude Sonnet 4 (recommended)"),
                ("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet"),
                ("anthropic/claude-3.5-haiku", "Claude 3.5 Haiku"),
            ],
            default=1,
        )
    else:
        _prompt_api_key_for_provider("OpenRouter", "OPENROUTER_API_KEY", agent_home)
        extraction_choice = "openrouter"
        extraction_model = voice.prompt_choice(
            "Which DeepSeek model?",
            [
                ("deepseek/deepseek-chat", "DeepSeek Chat (recommended)"),
                ("deepseek/deepseek-reasoner", "DeepSeek Reasoner"),
                ("deepseek/deepseek-chat-v3.1", "DeepSeek Chat v3.1"),
            ],
            default=1,
        )

    embedding_choice = "ollama"
    embedding_model = "nomic-embed-text:v1.5"
    voice.section(
        "For memory search, Titan uses nomic-embed-text:v1.5 through Ollama.\n"
        "This model runs locally and is required for semantic search."
    )
    if voice.confirm("Download nomic-embed-text:v1.5 now?", default_yes=True):
        voice.step("Downloading nomic-embed-text:v1.5")
        try:
            subprocess.run(["ollama", "pull", embedding_model], check=True, text=True, timeout=300)
            voice.success("nomic-embed-text:v1.5 is ready")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            voice.warn(f"I couldn't download {embedding_model}. Run: ollama pull {embedding_model}")
    else:
        voice.warn(f"Skipping download. Run this later before using memory search: ollama pull {embedding_model}")

    extraction_cfg = _set_config_backend_model(extraction_cfg, extraction_choice, extraction_model)
    embedding_cfg = _set_config_backend_model(embedding_cfg, embedding_choice, embedding_model)
    extraction_path, embedding_path = _write_agent_model_configs(agent_home, extraction_cfg, embedding_cfg)

    env_updates: Dict[str, str] = {
        "TITAN_EXTRACTION_CONFIG_PATH": str(extraction_path),
        "TITAN_EMBEDDING_CONFIG_PATH": str(embedding_path),
    }
    _save_env_updates_for_agent(agent_home, env_updates)

    effective_env = _read_agent_effective_env(agent_home, env_updates)
    required_keys: List[str] = []
    for cfg, backend in ((extraction_cfg, extraction_choice), (embedding_cfg, embedding_choice)):
        key_name = cfg.get(backend, {}).get("api_key_env")
        if isinstance(key_name, str) and key_name and key_name not in required_keys:
            required_keys.append(key_name)
    missing_keys = [key for key in required_keys if not effective_env.get(key)]
    if missing_keys:
        voice.warn(f"Missing key(s): {', '.join(missing_keys)}. Memory extraction may not work until set.")
        for key in missing_keys:
            voice.info(f"Run: titan key set {key} --agent codex")

    voice.success(f"Config saved to {agent_home}")
    voice.model_picked(extraction_model, extraction_choice)
    voice.model_picked(embedding_model, embedding_choice)
    return env_updates


def run_setup_codex(
    *,
    dry_run: bool = False,
    verify: bool = False,
    config_path: Optional[Path] = None,
    skip_plugin_install: bool = False,
) -> int:
    config_target = config_path or _default_codex_config_path()
    trace_dir = resolve_effective_spool_dir(CODEX_AGENT_NAME)

    if verify:
        return run_codex_verify(config_path=config_target)

    if dry_run:
        print("[titan] Codex setup dry run")
        print(f"- Ensure agent home: {resolve_agent_titan_home(CODEX_AGENT_NAME)}")
        print(f"- Ensure trace dir: {trace_dir}")
        print("- Configure extraction model and nomic embedding model")
        print(f"- Verify plugin files: {CODEX_PLUGIN_DIR}")
        print(f"- Patch Codex config: {config_target}")
        if not skip_plugin_install:
            print(f"- Reinstall Codex plugin: {CODEX_PLUGIN_ID}")
        print("- Smoke-test local Titan MCP tool exports")
        print("- Show first-run guidance: python3 integrations/codex_titan_plugin/scripts/titan_first_run.py")
        print("- Manual final step: trust Titan hooks inside Codex with /hooks")
        return 0

    agent_home = bootstrap_agent_home(CODEX_AGENT_NAME)
    trace_dir.mkdir(parents=True, exist_ok=True)
    _setup_codex_model_config(agent_home)
    plugin_ok, missing_plugin_files = _codex_plugin_files_ok()
    if not plugin_ok:
        print("[titan] Codex plugin files are incomplete:")
        for path in missing_plugin_files:
            print(f"  {path}")
        return 1

    patch_result = patch_codex_config(config_path=config_target)
    print(f"[titan] {patch_result['detail']} ({patch_result['path']})")
    if patch_result["backup_path"]:
        print(f"[titan] Backup: {patch_result['backup_path']}")

    if not skip_plugin_install:
        reinstall_code = run_codex_reinstall_plugin()
        if reinstall_code != 0:
            return reinstall_code

    tools = list_titan_mcp_tools_for_agent(CODEX_AGENT_NAME)
    missing_tools = [tool for tool in CODEX_REQUIRED_MCP_TOOLS if tool not in tools]
    if missing_tools:
        print(f"[titan] MCP started, but tools are missing: {', '.join(missing_tools)}")
        return 1

    print(f"[titan] Titan MCP exports {len(tools)} tools for Codex.")
    print("[titan] Next manual step: open Codex, run /hooks, and trust the Titan hook commands.")
    print("[titan] Then run: titan codex verify")
    return 0


def _save_env_updates_for_agent(agent_home: Path, updates: Dict[str, str]) -> List[Path]:
    written: List[Path] = []
    if not updates:
        return written
    for env_file in (TITAN_HOME / ".env", agent_home / ".env"):
        result = upsert_env_keys(env_file, updates)
        if result["updated_keys"]:
            written.append(env_file)
    return written


def _print_manual_opencode_config(agent: str) -> None:
    print("[titan] Paste this block into your OpenCode config manually:")
    print()
    print(generate_mcp_block(agent=agent, mode="stdio"))


def run_init(
    *,
    agent: str = DEFAULT_AGENT_NAME,
    scope: InstallScope = "global",
    non_interactive: bool = False,
    skip_test: bool = False,
) -> int:
    import tools.cli.titan_voice as voice

    voice.section("Starting onboarding...\n")
    voice.info("Tip: `titan setup` is the new command for this. It does the same thing.")
    voice.info("Run: titan setup opencode")
    return 1

    normalized_agent = _normalize_agent_name(agent)
    agent_home = bootstrap_agent_home(normalized_agent)

    missing_deps = verify_python_dependencies(ROOT_DIR / "requirements.txt")
    if missing_deps:
        print(f"[titan] Warning: missing Python dependencies: {', '.join(missing_deps)}")
        print("[titan] Run: pip install -r requirements.txt")
    else:
        print("[titan] Dependency check passed.")

    env_example = ROOT_DIR / ".env.example"
    env_file = TITAN_HOME / ".env"
    template_keys = _parse_env_keys_from_example(env_example) if env_example.exists() else []
    existing_env = _read_agent_effective_env(agent_home, {})
    missing_template_keys = [key for key in template_keys if not existing_env.get(key)]

    pending_updates: Dict[str, str] = {}
    if missing_template_keys:
        print(f"[titan] Warning: missing keys in environment/.env: {', '.join(missing_template_keys)}")
        print("[titan] Titan can connect now, but extraction may stay limited until you add keys.")
    else:
        print("[titan] API key setup already satisfied from environment/.env.")

    provider_info = get_required_provider_envs(ROOT_DIR)
    required_envs = list(provider_info["required_envs"])
    warnings = list(provider_info["warnings"])

    effective_env = _read_agent_effective_env(agent_home, pending_updates)
    missing_required = [name for name in required_envs if not effective_env.get(name)]

    for warning in warnings:
        print(f"[titan] Warning: {warning}")
    if missing_required:
        print(f"[titan] Warning: required provider keys missing for current model config: {', '.join(missing_required)}")
        print("[titan] Test may fail until provider credentials are configured.")

    install_result = install_opencode_plugin(scope=scope, root_dir=ROOT_DIR)
    plugin_path = Path(install_result["target_path"])
    print("[titan] Titan connection files are ready.")
    print(f"[titan] Plugin {install_result['status']}: {plugin_path}")
    print(f"[titan] Agent home ({normalized_agent}): {agent_home}")
    print(f"[titan] Trace directory: {resolve_effective_spool_dir(normalized_agent)}")
    print()
    print(generate_agent_connection_guide(example_agent=normalized_agent))
    print()
    print("[titan] Next steps:")
    print("1. Save the JSON block above in OpenCode config.")
    print("2. Restart OpenCode.")
    print("3. Use OpenCode normally.")
    print(f"4. Run `{_doctor_command_for_agent(normalized_agent)}` to verify capture.")
    if missing_required:
        print(f"[titan] Optional next step: run `titan key set {missing_required[0]}` to enable the current model backend.")
    return 0


def run_doctor(*, agent: str = DEFAULT_AGENT_NAME) -> int:
    import tools.cli.titan_voice as voice

    normalized_agent = _normalize_agent_name(agent)
    agent_home = bootstrap_agent_home(normalized_agent)
    trace_dir = resolve_effective_spool_dir(normalized_agent)
    effective_env = _read_agent_effective_env(agent_home, {})
    provider_info = get_required_provider_envs(ROOT_DIR, env=effective_env)
    missing_required = [name for name in provider_info["required_envs"] if not effective_env.get(name)]

    plugin_candidates = [
        Path.home() / ".config" / "opencode" / "plugins" / "titan_v2_spool_plugin.ts",
        ROOT_DIR / ".opencode" / "plugins" / "titan_v2_spool_plugin.ts",
    ]
    plugin_path = next((path for path in plugin_candidates if path.exists()), plugin_candidates[0])

    voice.section(f"Let me check on you...\n")

    if missing_required:
        voice.check(False, f"Memory brain — missing API key(s): {', '.join(missing_required)}")
        fix_key = missing_required[0]
        voice.error(
            f"I need your {fix_key} to check the memory brain.",
            fix=f"titan key set {fix_key} --agent {normalized_agent}",
        )
        return 1
    else:
        backend = provider_info.get("extraction_backend", "unknown")
        voice.check(True, f"Memory brain is working ({backend})")

    if plugin_path.exists():
        voice.check(True, f"OpenCode plugin — found at {plugin_path}")
    else:
        voice.check(False, "OpenCode plugin — not found")
        voice.error(
            "The plugin isn't installed yet.",
            fix=f"titan setup {normalized_agent}",
        )
        return 1

    if missing_required:
        voice.error(
            "Titan isn't memory-ready yet.",
            fix=f"titan key set {missing_required[0]} --agent {normalized_agent}",
        )
        return 1

    pid_file = agent_home / "server.pid"
    pid_file_stale = False
    default_runtime_port = "8002" if normalized_agent == "pi" else "8000"
    runtime_port = int(os.getenv("TITAN_PORT", os.getenv("TITAN_PI_PORT", default_runtime_port)))
    if pid_file.exists():
        try:
            stored_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            stored_pid = None
        try:
            import urllib.request
            req = urllib.request.urlopen(f"http://127.0.0.1:{runtime_port}/api/runtime", timeout=3)
            runtime = json.loads(req.read())
            actual_pid = runtime.get("pid")
            if actual_pid and stored_pid and actual_pid != stored_pid:
                pid_file_stale = True
        except Exception:
            actual_pid = None
        if pid_file_stale:
            voice.warn(f"server.pid ({stored_pid}) is stale — actual pid is {actual_pid}")
        elif actual_pid:
            voice.check(True, f"Server running — pid {actual_pid} on port {runtime_port}")

    result = run_connected_loop_test(plugin_path=plugin_path, agent=normalized_agent)
    if result["ok"]:
        retrieval_count = result.get("retrieval_count", 0)
        voice.check(True, f"Live retrieval — got {retrieval_count} memory" + ("ies" if retrieval_count != 1 else ""))
        if retrieval_count > 0:
            voice.outro_success(
                "Everything's working. I'm actively remembering. \U0001f9e0",
                [],
                f"Keep chatting — I'll keep capturing memories.",
            )
        else:
            voice.outro_success(
                "Titan is working! OpenCode is now capturing your conversations.",
                [],
                f"Send another message and run: {_doctor_command_for_agent(normalized_agent)}",
            )
        return 0

    if "no plugin events found" in result["issues"]:
        voice.warn("I haven't caught any events from OpenCode yet.")
        voice.info(f"Start a conversation in OpenCode, then run: {_doctor_command_for_agent(normalized_agent)}")
        voice.outro_success(
            "Waiting for first capture.",
            [],
            f"Once you've chatted, run: {_doctor_command_for_agent(normalized_agent)}",
        )
        return 0

    issue_summary = ", ".join(result["issues"])
    voice.error(
        f"Something isn't working: {issue_summary}",
        fix=_doctor_command_for_agent(normalized_agent),
    )
    if result.get("detail"):
        voice.info(result["detail"])
    return 1


def run_patterns_command(args: argparse.Namespace) -> int:
    configure_runtime_for_agent(args.agent)
    from app.patterns.api import (
        PatternEvidencePacketRequest,
        PatternMarkProcessedRequest,
        accept_pattern,
        get_evidence_packet,
        get_pattern,
        get_pattern_status,
        list_patterns,
        mark_processed,
        reject_pattern,
    )

    if args.patterns_command == "status":
        payload = get_pattern_status()
    elif args.patterns_command == "list":
        payload = list_patterns(status=args.status, scope=args.scope, limit=args.limit)
    elif args.patterns_command == "show":
        payload = get_pattern(args.pattern_id)
    elif args.patterns_command == "evidence":
        payload = get_evidence_packet(
            PatternEvidencePacketRequest(
                batch_size=args.batch_size,
                context_limit=args.context_limit,
                session_id=args.session_id,
                mode=args.mode,
                packet_type=args.packet_type,
            )
        )
    elif args.patterns_command == "accept":
        payload = accept_pattern(args.pattern_id)
    elif args.patterns_command == "reject":
        payload = reject_pattern(args.pattern_id)
    elif args.patterns_command == "mark-processed":
        payload = mark_processed(
            PatternMarkProcessedRequest(
                memory_ids=args.memory_id,
                pattern_ids=args.pattern_id or [],
                status=args.status,
                mode=args.mode,
                error=args.error,
            )
        )
    else:
        raise ValueError(f"Unsupported patterns command: {args.patterns_command}")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def run_share(args: argparse.Namespace) -> int:
    if not args.patterns:
        raise ValueError("Only `titan share --patterns` is supported right now.")
    configure_runtime_for_agent(args.agent)
    from app.patterns.bundle import export_pattern_bundle

    statuses = list(args.status or ["accepted"])
    if args.include_candidates and "candidate" not in statuses:
        statuses.append("candidate")
    bundle = export_pattern_bundle(
        statuses=statuses,
        scopes=list(args.scope or []),
        include_memory_summaries=not args.no_memory_summaries,
        include_progress=not args.no_progress,
        limit=args.limit,
    )
    payload = json.dumps(bundle, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
        print(f"[titan] Pattern bundle written: {output_path}")
        print(f"[titan] Patterns: {len(bundle.get('patterns', []))}; evidence: {len(bundle.get('evidence', []))}")
    else:
        print(payload)
    return 0


def run_import_bundle(args: argparse.Namespace) -> int:
    if not args.patterns:
        raise ValueError("Only `titan import --patterns <path>` is supported right now.")
    configure_runtime_for_agent(args.agent)
    from app.patterns.bundle import import_pattern_bundle

    input_path = Path(args.patterns).expanduser()
    bundle = json.loads(input_path.read_text(encoding="utf-8"))
    result = import_pattern_bundle(bundle, overwrite=args.overwrite, import_progress=not args.no_progress)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="titan", description="Titan CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Set up Titan end-to-end for an agent")
    setup_parser.add_argument("agent", nargs="?", default=DEFAULT_AGENT_NAME, help="Agent to set up (default: opencode).")
    setup_scope_group = setup_parser.add_mutually_exclusive_group()
    setup_scope_group.add_argument("--global", dest="scope", action="store_const", const="global", default="global")
    setup_scope_group.add_argument("--project", dest="scope", action="store_const", const="project")
    setup_parser.add_argument("--non-interactive", action="store_true", help="Do not prompt; fail if required keys are missing.")
    setup_parser.add_argument("--yes", action="store_true", help="Approve safe OpenCode config edits without prompting.")
    setup_parser.add_argument("--opencode-config", type=Path, help="OpenCode config path to patch. Defaults to ~/.config/opencode/opencode.json.")
    setup_parser.add_argument("--codex-config", type=Path, help="Codex config path to patch when running `titan setup codex`.")
    setup_parser.add_argument("--dry-run", action="store_true", help="For `titan setup codex`, print planned changes without writing files.")
    setup_parser.add_argument("--verify", action="store_true", help="For `titan setup codex`, verify the current Codex setup without applying changes.")
    setup_parser.add_argument("--skip-plugin-install", action="store_true", help="For `titan setup codex`, patch config but do not run codex plugin install commands.")
    setup_parser.add_argument("--key", action="append", default=[], dest="cli_keys", metavar="NAME=VALUE", help="Set an API key. Repeatable. Example: --key GEMINI_API_KEY=xxx")

    init_parser = subparsers.add_parser("init", help="Initialize Titan onboarding")
    scope_group = init_parser.add_mutually_exclusive_group()
    scope_group.add_argument("--global", dest="scope", action="store_const", const="global", default="global")
    scope_group.add_argument("--project", dest="scope", action="store_const", const="project")
    init_parser.add_argument("--non-interactive", action="store_true", help="Require existing environment values and skip prompts.")
    init_parser.add_argument("--skip-test", action="store_true", help="Deprecated: init no longer runs smoke tests.")
    init_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name for the first runtime home (default: opencode).")

    doctor_parser = subparsers.add_parser("doctor", help="Verify Titan setup and recent OpenCode capture")
    doctor_parser.add_argument("agent_positional", nargs="?", help="Agent name to check (default: opencode).")
    doctor_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose Titan setup should be checked.")

    graph_parser = subparsers.add_parser("graph", help="Serve Titan memory graph for an agent")
    graph_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose graph should be shown.")
    graph_parser.add_argument("--session-id", help="Optional session id to view a session-specific graph.")
    graph_parser.add_argument("--open", action="store_true", dest="open_browser", help="Open the graph URL in your browser.")
    graph_parser.add_argument("--host", default="127.0.0.1", help="Host for the graph web server.")
    graph_parser.add_argument("--port", type=int, default=DEFAULT_GRAPH_PORT, help="Port for the graph web server.")

    pattern_graph_parser = subparsers.add_parser("pattern-graph", help="Serve Titan learned pattern graph for an agent")
    pattern_graph_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose pattern graph should be shown.")
    pattern_graph_parser.add_argument("--open", action="store_true", dest="open_browser", help="Open the pattern graph URL in your browser.")
    pattern_graph_parser.add_argument("--host", default="127.0.0.1", help="Host for the graph web server.")
    pattern_graph_parser.add_argument("--port", type=int, default=DEFAULT_GRAPH_PORT, help="Port for the graph web server.")
    pattern_graph_parser.add_argument("--limit", type=int, default=500, help="Maximum patterns to render.")

    share_parser = subparsers.add_parser("share", help="Export shareable Titan bundles")
    share_parser.add_argument("--patterns", action="store_true", help="Export pattern cards as a titan.pattern_bundle.v1 file.")
    share_parser.add_argument("--output", "-o", help="Output JSON path. Prints to stdout when omitted.")
    share_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose Titan pattern store should be used.")
    share_parser.add_argument("--status", action="append", choices=["candidate", "accepted", "rejected", "superseded"], help="Pattern status to include. Repeatable. Defaults to accepted.")
    share_parser.add_argument("--scope", action="append", choices=["user", "repo", "team", "agent", "global"], help="Pattern scope to include. Repeatable.")
    share_parser.add_argument("--include-candidates", action="store_true", help="Also include candidate patterns.")
    share_parser.add_argument("--no-memory-summaries", action="store_true", help="Omit redacted evidence memory summaries.")
    share_parser.add_argument("--no-progress", action="store_true", help="Omit pattern processing progress records.")
    share_parser.add_argument("--limit", type=int, default=500, help="Maximum patterns to export.")

    import_parser = subparsers.add_parser("import", help="Import Titan bundles")
    import_parser.add_argument("--patterns", help="Path to a titan.pattern_bundle.v1 JSON file.")
    import_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose Titan pattern store should be used.")
    import_parser.add_argument("--overwrite", action="store_true", help="Replace existing patterns with matching ids.")
    import_parser.add_argument("--no-progress", action="store_true", help="Do not import pattern processing progress records.")

    key_parser = subparsers.add_parser("key", help="Manage Titan API keys")
    key_subparsers = key_parser.add_subparsers(dest="key_command", required=True)
    key_set_parser = key_subparsers.add_parser("set", help="Set an API key in Titan home .env")
    key_set_parser.add_argument("key_name", help="Environment key name, e.g. GEMINI_API_KEY")
    key_set_parser.add_argument("--value", help="Key value. If omitted, prompts securely.")
    key_set_parser.add_argument("--agent", help="Write the key to a specific agent runtime instead of the shared Titan home.")

    mcp_parser = subparsers.add_parser("mcp", help="Run Titan MCP server over stdio")
    mcp_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose Titan memory home should be used.")

    codex_parser = subparsers.add_parser("codex", help="Codex-specific Titan setup and verification helpers")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)
    codex_doctor_parser = codex_subparsers.add_parser("doctor", help="Check Codex plugin, config, hooks, traces, and MCP tools")
    codex_doctor_parser.add_argument("--config", type=Path, help="Codex config path. Defaults to ~/.codex/config.toml.")
    codex_verify_parser = codex_subparsers.add_parser("verify", help="Strict Codex setup verification")
    codex_verify_parser.add_argument("--config", type=Path, help="Codex config path. Defaults to ~/.codex/config.toml.")
    codex_list_tools_parser = codex_subparsers.add_parser("list-tools", help="List local Titan MCP tools exposed to Codex")
    codex_list_tools_parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON.")
    codex_reinstall_parser = codex_subparsers.add_parser("reinstall-plugin", help="Reinstall the local Titan Codex plugin")
    codex_reinstall_parser.add_argument("--dry-run", action="store_true", help="Print codex plugin commands without running them.")

    patterns_parser = subparsers.add_parser("patterns", help="Inspect and manage Titan learned patterns")
    patterns_parser.add_argument("--agent", default=DEFAULT_AGENT_NAME, help="Agent name whose Titan pattern store should be used.")
    patterns_subparsers = patterns_parser.add_subparsers(dest="patterns_command", required=True)
    patterns_status_parser = patterns_subparsers.add_parser("status", help="Show pattern mining and review status")
    patterns_status_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_list_parser = patterns_subparsers.add_parser("list", help="List candidate or accepted patterns")
    patterns_list_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_list_parser.add_argument("--status", choices=["candidate", "accepted", "rejected", "superseded"], help="Filter by pattern status.")
    patterns_list_parser.add_argument("--scope", choices=["user", "repo", "team", "agent", "global"], help="Filter by pattern scope.")
    patterns_list_parser.add_argument("--limit", type=int, default=50, help="Maximum patterns to show.")
    patterns_show_parser = patterns_subparsers.add_parser("show", help="Show one pattern and its evidence")
    patterns_show_parser.add_argument("pattern_id", help="Pattern id to inspect.")
    patterns_show_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_evidence_parser = patterns_subparsers.add_parser("evidence", help="Build a read-only evidence packet for agent-led pattern mining")
    patterns_evidence_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_evidence_parser.add_argument("--new", action="store_true", help="Process new/unprocessed memories for the current miner version. Default behavior.")
    patterns_evidence_parser.add_argument("--batch-size", type=int, help="Maximum unprocessed memories to include.")
    patterns_evidence_parser.add_argument("--context-limit", type=int, help="Maximum related old memories to include.")
    patterns_evidence_parser.add_argument("--session-id", help="Optional session id to scope the packet.")
    patterns_evidence_parser.add_argument("--mode", choices=["adaptive", "chronological"], help="Evidence packet selection mode. Defaults to patterns.packet_mode in config.")
    patterns_evidence_parser.add_argument("--packet-type", choices=["high_signal", "semantic_cluster", "entity", "bridge", "contradiction", "scene_episode", "chronological_fallback"], help="Ask the adaptive planner for a specific packet type.")
    patterns_accept_parser = patterns_subparsers.add_parser("accept", help="Accept a candidate pattern for retrieval")
    patterns_accept_parser.add_argument("pattern_id", help="Pattern id to accept.")
    patterns_accept_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_reject_parser = patterns_subparsers.add_parser("reject", help="Reject a noisy or wrong pattern")
    patterns_reject_parser.add_argument("pattern_id", help="Pattern id to reject.")
    patterns_reject_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_mark_parser = patterns_subparsers.add_parser("mark-processed", help="Low-level support command: mark inspected memories processed")
    patterns_mark_parser.add_argument("--agent", default=argparse.SUPPRESS, help="Agent name whose Titan pattern store should be used.")
    patterns_mark_parser.add_argument("--memory-id", action="append", required=True, help="Memory id to mark processed. Repeatable.")
    patterns_mark_parser.add_argument("--pattern-id", action="append", help="Pattern id produced from this inspection. Repeatable.")
    patterns_mark_parser.add_argument("--status", default="processed", choices=["processed", "failed"], help="Processing status to record.")
    patterns_mark_parser.add_argument("--mode", default="incremental", help="Processing mode label.")
    patterns_mark_parser.add_argument("--error", help="Optional error message when status=failed.")

    config_parser = subparsers.add_parser("config", help="View and change Titan model configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("show", help="Show current model configuration")
    config_subparsers.add_parser("set-model", help="Interactively change extraction and embedding models")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "setup":
        if _normalize_agent_name(args.agent) == CODEX_AGENT_NAME:
            return run_setup_codex(
                dry_run=args.dry_run,
                verify=args.verify,
                config_path=args.codex_config,
                skip_plugin_install=args.skip_plugin_install,
            )
        return run_setup(
            agent=args.agent,
            scope=args.scope,
            non_interactive=args.non_interactive,
            yes=args.yes,
            config_path=args.opencode_config,
            cli_keys=list(args.cli_keys) if hasattr(args, "cli_keys") else None,
        )
    if args.command == "init":
        return run_init(
            agent=args.agent,
            scope=args.scope,
            non_interactive=args.non_interactive,
            skip_test=args.skip_test,
        )
    if args.command == "mcp":
        configure_runtime_for_agent(args.agent)
        from entrypoints.mcp_server import run as run_mcp

        run_mcp()
        return 0
    if args.command == "codex":
        if args.codex_command == "doctor":
            return run_codex_doctor(config_path=args.config)
        if args.codex_command == "verify":
            return run_codex_verify(config_path=args.config)
        if args.codex_command == "list-tools":
            return run_codex_list_tools(json_output=args.json_output)
        if args.codex_command == "reinstall-plugin":
            return run_codex_reinstall_plugin(dry_run=args.dry_run)
        parser.error(f"Unsupported codex command: {args.codex_command}")
        return 2
    if args.command == "doctor":
        agent_arg = args.agent_positional if args.agent_positional is not None else args.agent
        return run_doctor(agent=agent_arg)
    if args.command == "graph":
        return run_graph(
            agent=args.agent,
            session_id=args.session_id,
            open_browser=args.open_browser,
            host=args.host,
            port=args.port,
        )
    if args.command == "pattern-graph":
        return run_pattern_graph(
            agent=args.agent,
            open_browser=args.open_browser,
            host=args.host,
            port=args.port,
            limit=args.limit,
        )
    if args.command == "patterns":
        return run_patterns_command(args)
    if args.command == "share":
        return run_share(args)
    if args.command == "import":
        return run_import_bundle(args)
    if args.command == "key":
        if args.key_command == "set":
            return run_set_key(key_name=args.key_name, value=args.value, agent=args.agent)
        parser.error(f"Unsupported key command: {args.key_command}")
        return 2
    if args.command == "config":
        if args.config_command == "show":
            return run_config_show()
        if args.config_command == "set-model":
            return run_config_set_model()
        parser.error(f"Unsupported config command: {args.config_command}")
        return 2
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
