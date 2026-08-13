from pathlib import Path

from app.runtime.context import RuntimeContext


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
