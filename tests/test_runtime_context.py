import os
from pathlib import Path
from unittest.mock import patch

from app.runtime.context import RuntimeContext, get_runtime_context, hydrate_process_environment


def test_runtime_context_precedence_and_explicit_memory_overrides(tmp_path: Path):
    home = tmp_path / "home"
    agent_home = home / "agents" / "pi"
    agent_home.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        f"TITAN_HOME={home}\nTITAN_BASE_DIR={tmp_path / 'repo-base'}\nTITAN_AGENT_NAME=pi\n",
        encoding="utf-8",
    )
    (home / ".env").write_text(
        f"TITAN_BASE_DIR={tmp_path / 'shared-base'}\nTITAN_MEMORY_BACKEND=json\n",
        encoding="utf-8",
    )
    (agent_home / ".env").write_text(
        f"TITAN_BASE_DIR={tmp_path / 'agent-base'}\n",
        encoding="utf-8",
    )

    context = RuntimeContext.from_environment(
        root_dir=tmp_path,
        default_home=tmp_path / "default",
        environ={"TITAN_BASE_DIR": str(tmp_path / "process-base"), "TITAN_MEMORY_BACKEND": "sqlite"},
    )

    assert context.agent_name == "pi"
    assert context.base_dir == (tmp_path / "process-base").resolve()
    assert context.memory_backend == "sqlite"
    assert context.trace_dir == (tmp_path / "process-base" / "traces").resolve()


def test_runtime_context_agent_env_wins_over_shared_and_repo(tmp_path: Path):
    home = tmp_path / "home"
    agent_home = home / "agents" / "codex"
    agent_home.mkdir(parents=True)
    (tmp_path / ".env").write_text(f"TITAN_HOME={home}\nTITAN_AGENT_NAME=codex\n", encoding="utf-8")
    (home / ".env").write_text(f"TITAN_BASE_DIR={tmp_path / 'shared'}\n", encoding="utf-8")
    (agent_home / ".env").write_text(f"TITAN_BASE_DIR={tmp_path / 'agent'}\n", encoding="utf-8")

    context = RuntimeContext.from_environment(root_dir=tmp_path, default_home=tmp_path / "default", environ={})

    assert context.base_dir == (tmp_path / "agent").resolve()


def test_adapter_defaults_remain_lower_precedence_than_dotenv(tmp_path: Path):
    configured_home = tmp_path / "configured-home"
    (tmp_path / ".env").write_text(
        f"TITAN_HOME={configured_home}\nTITAN_AGENT_NAME=pi\n",
        encoding="utf-8",
    )

    context = RuntimeContext.from_environment(
        root_dir=tmp_path,
        default_home=tmp_path / "fallback-home",
        adapter_defaults={
            "TITAN_HOME": str(tmp_path / "adapter-home"),
            "TITAN_AGENT_NAME": "adapter",
        },
        environ={},
    )

    assert context.titan_home == configured_home.resolve()
    assert context.agent_name == "pi"


def test_hydrated_adapter_identity_survives_later_context_lookups(tmp_path: Path):
    pi_home = tmp_path / "agents" / "pi"
    adapter_defaults = {
        "TITAN_AGENT_NAME": "pi",
        "TITAN_HOME": str(pi_home),
    }

    with patch.dict(
        os.environ,
        {
            "TITAN_PI_ADAPTER": "1",
            "TITAN_PI_DEFAULT_AGENT": "pi",
            "TITAN_PI_DEFAULT_HOME": str(pi_home),
        },
        clear=True,
    ):
        initial = RuntimeContext.from_environment(
            root_dir=tmp_path,
            adapter_defaults=adapter_defaults,
            environ=os.environ,
        )
        hydrate_process_environment(initial)
        resolved = get_runtime_context()

        assert resolved.agent_name == "pi"
        assert resolved.base_dir == pi_home.resolve()


def test_runtime_context_derives_shared_home_from_agent_workspace(tmp_path: Path):
    shared_home = tmp_path / "titan"
    agent_home = shared_home / "agents" / "codex"

    context = RuntimeContext.from_environment(
        root_dir=tmp_path,
        environ={
            "TITAN_AGENT_NAME": "codex",
            "TITAN_HOME": str(agent_home),
            "TITAN_BASE_DIR": str(agent_home),
        },
    )

    assert context.shared_home == shared_home.resolve()


def test_explicit_shared_home_wins_for_custom_agent_workspace(tmp_path: Path):
    shared_home = tmp_path / "shared"
    custom_workspace = tmp_path / "custom-codex"

    context = RuntimeContext.from_environment(
        root_dir=tmp_path,
        environ={
            "TITAN_AGENT_NAME": "codex",
            "TITAN_HOME": str(custom_workspace),
            "TITAN_BASE_DIR": str(custom_workspace),
            "TITAN_SHARED_HOME": str(shared_home),
        },
    )

    assert context.shared_home == shared_home.resolve()


def test_agent_settings_override_bundled_defaults_without_replacing_them(tmp_path: Path):
    bundled_config = tmp_path / "config"
    bundled_config.mkdir()
    (bundled_config / "settings.yaml").write_text(
        "port: 8000\nlnn:\n  enabled: true\n  tau_boost: 0.05\n",
        encoding="utf-8",
    )
    agent_home = tmp_path / "titan" / "agents" / "pi"
    local_config = agent_home / "config"
    local_config.mkdir(parents=True)
    local_settings = local_config / "settings.yaml"
    local_settings.write_text("lnn:\n  tau_boost: 0.10\n", encoding="utf-8")

    context = RuntimeContext.from_environment(
        root_dir=tmp_path,
        environ={
            "TITAN_AGENT_NAME": "pi",
            "TITAN_HOME": str(agent_home),
            "TITAN_BASE_DIR": str(agent_home),
        },
    )

    assert context.settings_path == local_settings.resolve()
    assert context.settings["port"] == 8000
    assert context.settings["lnn"]["enabled"] is True
    assert context.settings["lnn"]["tau_boost"] == 0.10


def test_explicit_settings_path_remains_a_full_replacement(tmp_path: Path):
    bundled_config = tmp_path / "config"
    bundled_config.mkdir()
    (bundled_config / "settings.yaml").write_text("port: 8000\nlnn:\n  enabled: true\n", encoding="utf-8")
    explicit = tmp_path / "benchmark.yaml"
    explicit.write_text("port: 9000\n", encoding="utf-8")

    context = RuntimeContext.from_environment(
        root_dir=tmp_path,
        environ={"TITAN_SETTINGS_PATH": str(explicit)},
    )

    assert context.settings_path == explicit.resolve()
    assert dict(context.settings) == {"port": 9000}
