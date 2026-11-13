import json
from types import SimpleNamespace

import pytest

from tools.release import verify_python_artifact as verifier


@pytest.mark.parametrize("profile", ["default", "core", "full"])
@pytest.mark.parametrize("surface", ["CLI", "MCP"])
def test_missing_tools_fail_with_profile_and_surface(profile, surface):
    expected = verifier.REQUIRED_MCP_TOOLS if profile == "full" else verifier.CORE_MCP_TOOLS
    with pytest.raises(RuntimeError, match=f"{surface} {profile}.*doctor"):
        verifier.check_tools(expected - {"doctor"}, surface=surface, profile=profile)


@pytest.mark.parametrize("profile", ["default", "core"])
def test_core_rejects_full_inventory(profile):
    with pytest.raises(RuntimeError, match="unexpected=.*store_trace_event"):
        verifier.check_tools(verifier.REQUIRED_MCP_TOOLS, surface="MCP", profile=profile)


def test_full_allows_additional_tools():
    verifier.check_tools(verifier.REQUIRED_MCP_TOOLS | {"future_tool"}, surface="MCP", profile="full")


def test_verifies_default_and_explicit_profiles_for_both_surfaces(monkeypatch, tmp_path):
    artifact = tmp_path / "titan.whl"
    artifact.touch()
    monkeypatch.setenv("TITAN_MCP_TOOL_PROFILE", "full")
    seen = []

    def inventory(surface, env):
        profile = env.get("TITAN_MCP_TOOL_PROFILE")
        seen.append((surface, profile))
        return sorted(verifier.REQUIRED_MCP_TOOLS if profile == "full" else verifier.CORE_MCP_TOOLS)

    def run(command, *, cwd, env, **kwargs):
        if "list-tools" in command:
            return SimpleNamespace(stdout=json.dumps({"tools": inventory("CLI", env)}))
        return SimpleNamespace(stdout="")

    def handshake(command, *, cwd, env):
        return inventory("MCP", env)

    monkeypatch.setattr(verifier, "run_checked", run)
    monkeypatch.setattr(verifier, "mcp_stdio_handshake", handshake)
    verifier.verify(artifact)
    assert seen == [(surface, profile) for profile in (None, "core", "full") for surface in ("CLI", "MCP")]
