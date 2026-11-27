"""Capture Hermes Agent turns through its configured Titan MCP connection."""

import hashlib
import json
import logging
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from threading import Lock


LOGGER = logging.getLogger(__name__)
MAX_TEXT_CHARS = 12_000
CAPTURE_TIMEOUT_SECONDS = 10
MAX_PENDING_TURNS = 64
POLICY_VETO = "blocked_by_plugin_pre_llm_call"
_PARTIAL_TRAILING_TOKEN = re.compile(r"\s\S*\Z")


def _message_text(message):
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, list):
        return ""

    parts = []
    for block in message:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and (text := text.strip()):
            parts.append(text)
    return "\n".join(parts)


def _bounded(text):
    """Cap text before it leaves Hermes.

    Credentials are whitespace-free tokens. A cut inside one would leave a
    fragment Titan's redaction no longer recognizes, so a token split by the
    limit is dropped whole rather than sent in part. Text with no whitespace
    at all keeps the hard cut; its prefix still matches Titan's patterns.
    """
    if len(text) <= MAX_TEXT_CHARS:
        return text
    clipped = text[:MAX_TEXT_CHARS]
    if not text[MAX_TEXT_CHARS].isspace():
        partial = _PARTIAL_TRAILING_TOKEN.search(clipped)
        if partial:
            clipped = clipped[: partial.start()]
    return clipped.rstrip()


def _connect_options(parser):
    parser.add_argument("--agent", help="Titan store name for this Hermes profile.")
    parser.add_argument("--configure-providers", action="store_true",
                        help="Open Titan's extraction and embedding provider setup.")


def _is_titan_cli(command):
    # Earlier instructions used `titan` or, for services, the absolute path
    # printed by `command -v titan` (npm installs titan.cmd on Windows).
    if not isinstance(command, str):
        return False
    return re.split(r"[\\/]", command.strip())[-1].lower() in {"titan", "titan.exe", "titan.cmd"}


def _existing_store(server, helper):
    if server is None:
        return None
    if isinstance(server, dict) and set(server) == {"command", "args"}:
        arguments = server["args"]
        if isinstance(arguments, list):
            if _is_titan_cli(server["command"]):
                prefix = ["mcp", "--agent"]
            else:
                prefix = [str(helper), "mcp", "--agent"]
            if arguments[:-1] == prefix:
                return arguments[-1]
    raise SystemExit("This profile already has a custom titan-memory MCP server. "
                     "Review that server in the Hermes configuration before connecting.")


def _connect_profile(args):
    from hermes_cli.config import get_config_path, load_config, set_config_value
    from hermes_cli.profiles import get_active_profile_name

    helper = Path(__file__).resolve().with_name("titan_runtime.py")
    if not helper.is_file():
        raise SystemExit("Titan's bundled launcher is missing. Reinstall this plugin release.")
    config = load_config()
    server = (config.get("mcp_servers") or {}).get("titan-memory")
    previous_agent = _existing_store(server, helper)
    agent = args.agent or previous_agent
    if not agent:
        profile = get_active_profile_name()
        if profile == "default":
            agent = "hermes"
        elif profile == "custom":
            home = str(get_config_path().parent.resolve())
            agent = "hermes-custom-" + hashlib.sha256(home.encode()).hexdigest()[:10]
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", profile).strip("-")
            agent = "hermes-" + slug
            if slug != profile:
                agent += "-" + hashlib.sha256(profile.encode()).hexdigest()[:10]
    if not isinstance(agent, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", agent):
        raise SystemExit("Choose a Titan store name made of lowercase letters, numbers, and hyphens.")
    entry = (config.get("plugins") or {}).get("entries", {}).get("titan-memory", {})
    grants = entry.get("mcp_allowlist", [])
    if not isinstance(grants, list):
        raise SystemExit("Expected a list in plugins.entries.titan-memory.mcp_allowlist.")
    python = getattr(sys, "_base_executable", None) or sys.executable
    local_server = {"command": python, "args": [str(helper), "mcp", "--agent", agent]}
    # Prepare outside the MCP startup deadline, before changing the active profile.
    setup_args = ["configure", "--agent", agent] if args.configure_providers else ["--runtime-python"]
    result = subprocess.run([sys.executable, str(helper), *setup_args],
                            stdout=None if args.configure_providers else subprocess.DEVNULL)
    if result.returncode:
        raise SystemExit(result.returncode)
    if server != local_server:
        set_config_value("mcp_servers.titan-memory", json.dumps(local_server))
    if "titan-memory" not in grants:
        set_config_value("plugins.entries.titan-memory.mcp_allowlist", json.dumps([*grants, "titan-memory"]))
    print(f"Titan is connected to {get_config_path()} using store {agent}. Restart Hermes chat or gateway.")
    if not args.configure_providers:
        print("To select extraction and embedding providers, run hermes titan-connect --configure-providers.")


def register(ctx):
    ctx.register_cli_command("titan-connect", "Connect this profile to local Titan and allow turn capture.",
                             _connect_options, _connect_profile)
    pending = OrderedDict()
    pending_lock = Lock()

    def stage_turn(*, session_id=None, turn_id=None, user_message=None, assistant_response=None, **_):
        if not isinstance(session_id, str) or not session_id.strip():
            return
        if not isinstance(turn_id, str) or not turn_id.strip():
            return
        goal = _bounded(_message_text(user_message))
        outcome = _bounded(_message_text(assistant_response))
        if not goal or not outcome:
            return

        with pending_lock:
            key = (session_id, turn_id)
            pending[key] = (goal, outcome)
            pending.move_to_end(key)
            if len(pending) > MAX_PENDING_TURNS:
                pending.popitem(last=False)

    def finish_turn(
        *, session_id=None, turn_id=None, completed=None, failed=None,
        interrupted=None, turn_exit_reason=None, **_
    ):
        if not isinstance(session_id, str) or not isinstance(turn_id, str):
            return
        with pending_lock:
            messages = pending.pop((session_id, turn_id), None)
        if (
            messages is None
            or completed is not True
            or failed is not False
            or interrupted is not False
            or not isinstance(turn_exit_reason, str)
            or not turn_exit_reason
            or turn_exit_reason == POLICY_VETO
        ):
            return

        goal, outcome = messages
        digest = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
        packet = {
            "session_id": session_id,
            "event_id": f"hermes:{digest}",
            "goal": goal,
            "outcome": outcome,
            "max_text_chars": MAX_TEXT_CHARS,
            "context": {"source": "hermes"},
        }
        try:
            result = ctx.call_mcp("titan-memory", "store_trace_packet", packet, timeout=CAPTURE_TIMEOUT_SECONDS)
        except PermissionError:
            LOGGER.warning("Run hermes titan-connect to grant Titan capture in this profile.")
        except Exception:
            LOGGER.warning("Titan capture could not reach the titan-memory MCP server.")
        else:
            if not result.get("ok"):
                LOGGER.warning("The titan-memory MCP server did not confirm capture.")

    ctx.register_hook("post_llm_call", stage_turn)
    ctx.register_hook("on_session_end", finish_turn)
