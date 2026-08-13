"""Immutable, explicit runtime context for Titan.

The context is deliberately small and dependency-free.  Entrypoints can build
one explicitly, while legacy call sites use :func:`get_runtime_context`.
Environment values win over dotenv files, and dotenv files win over adapter
defaults.  We never mutate ``os.environ`` while resolving a context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, MutableMapping, Optional

try:  # PyYAML is a declared dependency, but keep import-time compatibility lightweight.
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised only in minimal installations
    yaml = None  # type: ignore


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _freeze(value: Any) -> Any:
    """Recursively freeze YAML containers exposed through RuntimeContext."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RuntimeContext:
    """Resolved paths and settings for one Titan process.

    ``settings`` is exposed as an immutable mapping so a worker cannot mutate
    process-wide configuration behind another worker's back.
    """

    agent_name: str
    titan_home: Path
    base_dir: Path
    trace_dir: Path
    settings_path: Path
    extraction_config_path: Optional[Path]
    embedding_config_path: Optional[Path]
    memory_db_path: Path
    memory_backend: str
    read_fallback: str
    settings: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    environment: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    dotenv_environment: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_environment(
        cls,
        *,
        root_dir: Optional[Path] = None,
        agent_name: Optional[str] = None,
        default_home: Optional[Path] = None,
        adapter_defaults: Optional[Mapping[str, str]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "RuntimeContext":
        root = (root_dir or _default_root()).expanduser().resolve()
        process: Mapping[str, str] = os.environ if environ is None else environ
        defaults: dict[str, str] = {
            "TITAN_AGENT_NAME": agent_name or "default",
            "TITAN_HOME": str((default_home or root).expanduser()),
        }
        if adapter_defaults:
            defaults.update({str(k): str(v) for k, v in adapter_defaults.items()})

        # Resolve file locations without allowing a lower-precedence value to
        # shadow an explicit process setting.
        repo_env = _read_env_file(root / ".env")
        provisional_home = Path(
            process.get("TITAN_HOME", repo_env.get("TITAN_HOME", defaults["TITAN_HOME"]))
        ).expanduser()
        shared_env = _read_env_file(provisional_home / ".env")
        # A shared dotenv may itself select the Titan home.  Re-resolve once so
        # agent dotenv files are read from the selected namespace.
        selected_home = process.get("TITAN_HOME", shared_env.get("TITAN_HOME", str(provisional_home)))
        if Path(selected_home).expanduser() != provisional_home:
            provisional_home = Path(selected_home).expanduser()
            shared_env = _read_env_file(provisional_home / ".env")
        agent = process.get(
            "TITAN_AGENT_NAME",
            repo_env.get("TITAN_AGENT_NAME", shared_env.get("TITAN_AGENT_NAME", defaults["TITAN_AGENT_NAME"])),
        )
        agent_env_path = provisional_home / "agents" / str(agent) / ".env"
        agent_env = _read_env_file(agent_env_path)

        dotenv_values: MutableMapping[str, str] = {}
        dotenv_values.update(repo_env)
        dotenv_values.update(shared_env)
        dotenv_values.update(agent_env)

        merged: MutableMapping[str, str] = dict(defaults)
        merged.update(repo_env)
        merged.update(shared_env)
        merged.update(agent_env)
        merged.update({str(k): str(v) for k, v in process.items()})

        resolved_agent = str(merged.get("TITAN_AGENT_NAME") or agent or "default")
        titan_home = Path(merged.get("TITAN_HOME", str(provisional_home))).expanduser().resolve()
        base_dir = Path(merged.get("TITAN_BASE_DIR", str(titan_home))).expanduser().resolve()
        trace_dir = Path(merged.get("TITAN_SPOOL_DIR", str(base_dir / "traces"))).expanduser().resolve()
        settings_path = Path(
            merged.get("TITAN_SETTINGS_PATH", str(root / "config" / "settings.yaml"))
        ).expanduser().resolve()

        raw_settings: dict[str, object] = {}
        if yaml is not None:
            try:
                loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw_settings = dict(loaded)
            except (OSError, yaml.YAMLError):
                raw_settings = {}

        backend = str(merged.get("TITAN_MEMORY_BACKEND") or raw_settings.get("memory_store_backend", "sqlite")).strip().lower() or "sqlite"
        fallback = str(merged.get("TITAN_MEMORY_READ_FALLBACK") or raw_settings.get("memory_store_read_fallback", "json")).strip().lower() or "json"
        configured_db = str(merged.get("TITAN_MEMORY_DB_PATH") or raw_settings.get("memory_store_sqlite_path", "") or "").strip()
        memory_db = Path(configured_db).expanduser() if configured_db else base_dir / "out" / "memories" / "memory_store.db"
        if not memory_db.is_absolute():
            memory_db = base_dir / memory_db

        def _model_path(env_name: str, filename: str) -> Path:
            value = merged.get(env_name)
            if value:
                return Path(value).expanduser().resolve()
            agent_default = titan_home / "config" / filename
            if agent_default.exists():
                return agent_default.resolve()
            return (root / "config" / filename).resolve()

        return cls(
            agent_name=resolved_agent,
            titan_home=titan_home,
            base_dir=base_dir,
            trace_dir=trace_dir,
            settings_path=settings_path,
            extraction_config_path=_model_path("TITAN_EXTRACTION_CONFIG_PATH", "extraction_models.yaml"),
            embedding_config_path=_model_path("TITAN_EMBEDDING_CONFIG_PATH", "embedding_models.yaml"),
            memory_db_path=memory_db.resolve(),
            memory_backend=backend,
            read_fallback=fallback,
            settings=_freeze(raw_settings),
            environment=MappingProxyType(dict(merged)),
            dotenv_environment=MappingProxyType(dict(dotenv_values)),
        )


def hydrate_process_environment(context: RuntimeContext) -> None:
    """Expose dotenv values to legacy ``os.getenv`` adapters.

    Existing process values are authoritative, so hydration only fills missing
    keys. Context resolution itself remains side-effect free.
    """

    for key, value in context.dotenv_environment.items():
        # Keep the resolved home explicit in RuntimeContext. Legacy CLI code
        # treats the presence of TITAN_HOME in the process environment as an
        # explicit single-home override, which would collapse agent isolation.
        if key == "TITAN_HOME":
            continue
        os.environ.setdefault(str(key), str(value))

    # Older extraction/embedding adapters resolve their config files from
    # process environment rather than accepting RuntimeContext directly.
    # Publish the already-resolved paths without changing explicit overrides.
    resolved_paths = {
        "TITAN_EXTRACTION_CONFIG_PATH": context.extraction_config_path,
        "TITAN_EMBEDDING_CONFIG_PATH": context.embedding_config_path,
    }
    for key, path in resolved_paths.items():
        if path is not None:
            os.environ.setdefault(key, str(path))


_CACHE: dict[tuple[object, ...], RuntimeContext] = {}


def adapter_defaults_from_environment() -> Optional[Mapping[str, str]]:
    """Return defaults supplied by a hosting adapter without elevating them.

    Pi uses this marker so its default namespace participates at the lowest
    precedence level. Explicit process values and all dotenv files still win.
    """

    if os.getenv("TITAN_PI_ADAPTER") != "1":
        return None
    return {
        "TITAN_AGENT_NAME": os.getenv("TITAN_PI_DEFAULT_AGENT", "pi"),
        "TITAN_HOME": os.getenv(
            "TITAN_PI_DEFAULT_HOME",
            str(Path.home() / ".titan" / "agents" / "pi"),
        ),
    }


def get_runtime_context(**kwargs: object) -> RuntimeContext:
    """Return a cached context for the current environment.

    Passing any ``RuntimeContext.from_environment`` keyword (for example
    ``root_dir`` or ``environ``) bypasses ambient state in a deterministic way.
    """

    if kwargs:
        return RuntimeContext.from_environment(**kwargs)  # type: ignore[arg-type]
    signature = tuple(sorted((key, value) for key, value in os.environ.items() if key.startswith("TITAN_")))
    key = (str(_default_root()), signature)
    context = _CACHE.get(key)
    if context is None:
        context = RuntimeContext.from_environment()
        _CACHE[key] = context
    return context


def reset_runtime_context_cache() -> None:
    _CACHE.clear()
