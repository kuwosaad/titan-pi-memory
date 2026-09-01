#!/usr/bin/env python3
"""Pi-parity Titan tools for Grok.

Pi registers native LLM tools. Grok cannot do that, so this CLI is the
equivalent surface. Skills and this Grok session call:

    titan-grok query "what did we decide about X"
    titan-grok recent
    titan-grok scene <scene_id>
    titan-grok save --goal "..." --outcome "..."
    titan-grok doctor
    titan-grok clusters
    titan-grok cortex 1,2 --question "..."
    titan-grok patterns status
    titan-grok graph --open

Default agent namespace is grok (~/.titan/agents/grok).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENT = "grok"


def _pipx_python() -> Path | None:
    candidate = Path.home() / ".local" / "pipx" / "venvs" / "titan-memory-cli" / "bin" / "python"
    return candidate if candidate.exists() else None


def _reexec_if_needed() -> None:
    try:
        import numpy  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        runtime = _pipx_python()
        if runtime is None:
            print("Titan Python deps missing. Install titan-memory-cli or pip install -e . from titan-pi-memory.", file=sys.stderr)
            raise SystemExit(2)
        os.execv(str(runtime), [str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


def bootstrap_grok_runtime(agent: str = DEFAULT_AGENT) -> Path:
    safe = (agent or DEFAULT_AGENT).strip() or DEFAULT_AGENT
    # Grok is commonly launched from a shell shared with another adapter.  Do
    # not let that adapter's generic TITAN_HOME leak into this namespace.  A
    # Grok-specific override remains available for custom installations.
    home = Path(
        os.environ.get("GROK_TITAN_HOME")
        or (Path.home() / ".titan" / "agents" / safe)
    ).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["TITAN_AGENT_NAME"] = safe
    os.environ["TITAN_HOME"] = str(home)
    os.environ["TITAN_BASE_DIR"] = str(home)
    _load_env_file(home / ".env")
    extraction = home / "config" / "extraction_models.yaml"
    embedding = home / "config" / "embedding_models.yaml"
    if extraction.exists():
        os.environ.setdefault("TITAN_EXTRACTION_CONFIG_PATH", str(extraction))
    if embedding.exists():
        os.environ.setdefault("TITAN_EMBEDDING_CONFIG_PATH", str(embedding))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return home


def _emit(payload: Any, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    if isinstance(payload, str):
        print(payload)
        return 0
    if isinstance(payload, dict) and payload.get("error"):
        print(str(payload["error"]), file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _format_memories(memories: list[Any], heading: str) -> str:
    if not memories:
        return "No sufficiently relevant memories found."
    lines = [heading, ""]
    for index, memory in enumerate(memories, start=1):
        if hasattr(memory, "text"):
            text = memory.text
            scene_id = getattr(memory, "scene_id", None)
            kind = getattr(memory, "type", None)
        else:
            text = str(memory.get("text") or "")
            scene_id = memory.get("scene_id")
            kind = memory.get("type")
        prefix = f"[{kind}] " if kind else ""
        suffix = f"  [scene: {scene_id}]" if scene_id else ""
        lines.append(f"{index}. {prefix}{text}{suffix}")
    return "\n".join(lines)


def cmd_query(args: argparse.Namespace) -> int:
    from app.save_pipeline.pipeline import retrieve_memory_brief

    result = retrieve_memory_brief(
        query=args.query or "",
        limit=args.limit,
        date_from=args.date_from,
        date_to=args.date_to,
        include_scenes=False,
    )
    if args.json:
        result["memories"] = [
            {
                "text": getattr(m, "text", m.get("text") if isinstance(m, dict) else str(m)),
                "type": getattr(m, "type", m.get("type") if isinstance(m, dict) else None),
                "scene_id": getattr(m, "scene_id", m.get("scene_id") if isinstance(m, dict) else None),
                "ts": getattr(m, "ts", m.get("ts") if isinstance(m, dict) else None),
                "session_id": getattr(m, "session_id", m.get("session_id") if isinstance(m, dict) else None),
            }
            for m in result.get("memories") or []
        ]
        return _emit(result, True)
    brief = (result.get("brief") or "").strip()
    memories = result.get("memories") or []
    if brief:
        print(brief)
        return 0
    print(_format_memories(memories, f"Titan query ({result.get('count') or 0} hits):"))
    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    from app.storage.memories import get_recent_memories

    memories = get_recent_memories(limit=args.limit)
    if args.json:
        return _emit(
            {
                "count": len(memories),
                "memories": [
                    {
                        "text": m.text,
                        "type": m.type,
                        "scene_id": m.scene_id,
                        "ts": m.ts,
                        "stream": m.stream,
                    }
                    for m in memories
                ],
            },
            True,
        )
    print(_format_memories(memories, f"Recent Titan memories ({len(memories)}):"))
    return 0


def cmd_scene(args: argparse.Namespace) -> int:
    from app.save_pipeline.pipeline import get_scene_context

    result = get_scene_context(args.scene_id)
    return _emit(result, args.json or True) if args.json else _emit(result, True)


def cmd_save(args: argparse.Namespace) -> int:
    from app.storage.models import TracePacketRequest
    from app.save_pipeline.pipeline import handle_trace_packet

    req = TracePacketRequest(
        goal=args.goal,
        thoughts=args.thoughts,
        outcome=args.outcome or "",
        session_id=args.session_id,
        event_id=args.event_id or f"grok-{uuid4().hex}",
        save_intent=True,
        intent_phrase=args.intent or "titan-grok save",
    )
    result = handle_trace_packet(req)
    if args.json:
        return _emit(result, True)
    status = result.get("memory_status") or "unknown"
    recap = (result.get("recap") or "").strip()
    print(f"Titan save: {status}")
    if recap:
        print(recap)
    return 0 if result.get("stored") is not False else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from app.storage.memories import get_memory_count

    home = Path(os.environ["TITAN_HOME"])
    traces = home / "traces"
    extraction = home / "config" / "extraction_models.yaml"
    embedding = home / "config" / "embedding_models.yaml"
    payload = {
        "agent_name": os.environ.get("TITAN_AGENT_NAME", DEFAULT_AGENT),
        "workspace": str(home),
        "spool_dir": str(traces),
        "spool_exists": traces.exists(),
        "trace_files": len(list(traces.glob("*.jsonl"))) if traces.exists() else 0,
        "config": extraction.exists() and embedding.exists(),
        "memory_count": get_memory_count(),
        "extraction_config": str(extraction) if extraction.exists() else None,
        "cli": "titan-grok",
        "mcp": "titan mcp --agent grok",
        "note": "Grok does not need Pi's port-8002 HTTP server. Use this CLI or MCP.",
    }
    if args.json:
        return _emit(payload, True)
    print(
        "\n".join(
            [
                "Titan Status (Grok)",
                "──────────────────",
                f"  Agent:      {payload['agent_name']}",
                f"  Workspace:  {payload['workspace']}",
                f"  Spool dir:  {payload['spool_dir']} {'✅' if payload['spool_exists'] else '⚠️ missing'}",
                f"  Traces:     {payload['trace_files']} jsonl",
                f"  Config:     {'✅' if payload['config'] else '⚠️ missing yaml'}",
                f"  Memories:   {payload['memory_count']}",
                "  Server:     not required (CLI/MCP talk to the local store)",
            ]
        )
    )
    return 0


def cmd_clusters(args: argparse.Namespace) -> int:
    from app.graph.clusters import inspect_memory_clusters

    data = inspect_memory_clusters(
        session_id=args.session_id,
        limit=args.limit,
        cluster_id=args.cluster_id,
        detail_limit=args.detail_limit,
    )
    if args.json:
        return _emit(data, True)
    if data.get("error"):
        print(data["error"], file=sys.stderr)
        return 1
    if args.cluster_id is not None and data.get("selected_cluster"):
        cluster = data["selected_cluster"]
        print(f"Cluster {cluster.get('cluster_id')}: {cluster.get('topic')}")
        print(f"{cluster.get('memory_count')} memories · {cluster.get('connection_count')} links")
        for index, memory in enumerate(cluster.get("examples") or [], start=1):
            text = str(memory.get("text") if isinstance(memory, dict) else getattr(memory, "text", memory))
            print(f"{index}. {text}")
        return 0
    print(
        f"Titan clusters: {data.get('cluster_count')} topics · "
        f"{data.get('memory_count')} memories · {data.get('connection_count')} connections"
    )
    for cluster in data.get("clusters") or []:
        keywords = ", ".join((cluster.get("keywords") or [])[:5])
        suffix = f" — {keywords}" if keywords else ""
        print(
            f"{cluster.get('cluster_id')}. {cluster.get('topic')} "
            f"({cluster.get('memory_count')} memories, {cluster.get('connection_count')} links){suffix}"
        )
    return 0


def cmd_cortex(args: argparse.Namespace) -> int:
    from app.graph.cortex_analysis import analyze_memory_clusters

    data = analyze_memory_clusters(
        cluster_ids=args.cluster_ids,
        session_id=args.session_id,
        question=args.question,
        limit=args.limit,
        detail_limit=args.detail_limit,
    )
    if args.json:
        return _emit(data, True)
    if data.get("error"):
        print(data["error"], file=sys.stderr)
        return 1
    print(data.get("summary") or json.dumps(data, indent=2, default=str))
    for memory in (data.get("central_memories") or [])[:5]:
        text = memory.get("text") if isinstance(memory, dict) else getattr(memory, "text", memory)
        print(f"- {text}")
    return 0


def cmd_patterns(args: argparse.Namespace) -> int:
    from app.patterns import api as patterns_api
    from app.patterns.errors import PatternError

    action = args.action
    try:
        if action == "status":
            data = patterns_api.get_pattern_status()
        elif action == "list":
            data = patterns_api.list_patterns(status=args.status, scope=args.scope, limit=args.limit)
        elif action == "get":
            if not args.pattern_id:
                print("pattern_id required", file=sys.stderr)
                return 2
            data = patterns_api.get_pattern(args.pattern_id)
        else:
            print(f"Unknown patterns action: {action}", file=sys.stderr)
            return 2
    except PatternError as exc:
        data = {"error": str(exc), "detail": exc.detail}
    return _emit(data, True)


def cmd_graph(args: argparse.Namespace) -> int:
    titan = os.environ.get("TITAN_CLI_COMMAND") or "titan"
    cmd = [titan, "graph", "--agent", os.environ.get("TITAN_AGENT_NAME", DEFAULT_AGENT)]
    if args.open:
        cmd.append("--open")
    if args.session_id:
        cmd.extend(["--session-id", args.session_id])
    if args.port:
        cmd.extend(["--port", str(args.port)])
    print(" ".join(cmd))
    if args.print_only:
        return 0
    return subprocess.call(cmd)


def cmd_help(_: argparse.Namespace) -> int:
    print(
        """Titan tools for Grok (Pi parity)

  titan-grok query "what did we decide about X"
  titan-grok query "" --from 2026-08-13 --to 2026-08-14
  titan-grok recent
  titan-grok scene <scene_id>
  titan-grok save --goal "..." --outcome "..."
  titan-grok doctor
  titan-grok clusters
  titan-grok clusters --id 2
  titan-grok cortex 1,2 --question "how do these relate"
  titan-grok patterns status
  titan-grok graph --open

MCP equivalents when connected: titan-memory__query_memories, etc.
"""
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="titan-grok", description="Pi-parity Titan tools for Grok.")
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON.")
    sub = parser.add_subparsers(dest="command")

    query = sub.add_parser("query", help="Semantic memory search")
    query.add_argument("query", nargs="?", default="", help="Search text. Empty + --from/--to = all in range.")
    query.add_argument("--limit", type=int, default=8)
    query.add_argument("--from", dest="date_from")
    query.add_argument("--to", dest="date_to")

    recent = sub.add_parser("recent", help="Browse recent memories")
    recent.add_argument("--limit", type=int, default=8)

    scene = sub.add_parser("scene", help="Open a scene by id")
    scene.add_argument("scene_id")

    save = sub.add_parser("save", help="Store a trace packet")
    save.add_argument("--goal", required=True)
    save.add_argument("--thoughts")
    save.add_argument("--outcome", default="")
    save.add_argument("--session-id")
    save.add_argument("--event-id")
    save.add_argument("--intent")

    sub.add_parser("doctor", help="Health / workspace check")

    clusters = sub.add_parser("clusters", help="Inspect memory graph clusters")
    clusters.add_argument("--id", dest="cluster_id", type=int)
    clusters.add_argument("--session-id")
    clusters.add_argument("--limit", type=int, default=500)
    clusters.add_argument("--detail-limit", type=int, default=8)

    cortex = sub.add_parser("cortex", help="Analyze one or more clusters")
    cortex.add_argument("cluster_ids")
    cortex.add_argument("--question")
    cortex.add_argument("--session-id")
    cortex.add_argument("--limit", type=int, default=500)
    cortex.add_argument("--detail-limit", type=int, default=8)

    patterns = sub.add_parser("patterns", help="Pattern status/list/get")
    patterns.add_argument("action", choices=["status", "list", "get"])
    patterns.add_argument("--pattern-id")
    patterns.add_argument("--status")
    patterns.add_argument("--scope")
    patterns.add_argument("--limit", type=int, default=50)

    graph = sub.add_parser("graph", help="Open or print the knowledge graph command")
    graph.add_argument("--open", action="store_true")
    graph.add_argument("--session-id")
    graph.add_argument("--port", type=int)
    graph.add_argument("--print-only", action="store_true")

    sub.add_parser("tools", help="List the Pi-parity tool map")
    return parser


def main(argv: list[str] | None = None) -> int:
    _reexec_if_needed()
    parser = build_parser()
    args = parser.parse_args(argv)
    bootstrap_grok_runtime(args.agent)
    command = args.command or "tools"
    if command == "tools":
        return cmd_help(args)
    handlers = {
        "query": cmd_query,
        "recent": cmd_recent,
        "scene": cmd_scene,
        "save": cmd_save,
        "doctor": cmd_doctor,
        "clusters": cmd_clusters,
        "cortex": cmd_cortex,
        "patterns": cmd_patterns,
        "graph": cmd_graph,
    }
    return handlers[command](args)


if __name__ == "__main__":
    raise SystemExit(main())
