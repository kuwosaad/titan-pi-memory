import json
import sqlite3
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict, List, NamedTuple, Optional

import networkx as nx
import numpy as np
from networkx.algorithms.community import greedy_modularity_communities
import yaml

from app.graph.adapter import graph_store_paths
from app.graph.explorer import ExplorerFilters, WarningCollector, correction_edges, count_records, newest_records
from app.graph.similarity import build_similarity_edges
from app.runtime.context import get_runtime_context
from app.storage.memories import get_recent_memories


# Cluster inspection and analysis load through load_memories() with this limit.
DEFAULT_GRAPH_MEMORY_LIMIT = 500
# The browser graph uses the same caps as the BB explorer snapshot.
MAX_GRAPH_MEMORIES = 150
MAX_GRAPH_LINKS = 450
# Nodes carry a preview; the detail drawer loads full text from /api/graph/memory.
NODE_PREVIEW_CHARS = 600


def load_visual_config() -> Dict[str, Any]:
    base_dir = Path(__file__).resolve().parents[2]
    config_path = base_dir / "config" / "visual_config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_ui_file(name: str) -> str:
    ui_dir = Path(__file__).resolve().parent / "ui"
    path = ui_dir / name
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_physics_worker() -> str:
    libraries = ("d3-dispatch", "d3-quadtree", "d3-timer", "d3-force")
    return "\n".join([*(_load_ui_file(f"vendor/{name}.js") for name in libraries), _load_ui_file("physics.js")])


@lru_cache(maxsize=1)
def _graph_brand_assets() -> tuple[str, str]:
    assets = Path(__file__).resolve().parents[2] / "tools" / "cli" / "onboarding_ui"
    try:
        font = b64encode((assets / "source-serif-regular.ttf").read_bytes()).decode("ascii")
        logo = b64encode((assets / "titan-logo.svg").read_bytes()).decode("ascii")
    except FileNotFoundError:
        return "", ""
    font_style = (
        "@font-face{font-family:'Source Serif 4';font-weight:400;font-display:swap;"
        f"src:url(data:font/ttf;base64,{font}) format('truetype');}}\n"
    )
    return font_style, f"data:image/svg+xml;base64,{logo}"


def load_memories(session_id: Optional[str] = None, limit: int = DEFAULT_GRAPH_MEMORY_LIMIT, *, agent: Optional[str] = None) -> List[Dict[str, Any]]:
    kwargs = {"limit": limit, "session_id": session_id}
    if agent:
        kwargs["sources"] = [agent]
    memories = get_recent_memories(**kwargs)
    return [mem.model_dump() if hasattr(mem, "model_dump") else dict(mem) for mem in memories]


class GraphMemories(NamedTuple):
    memories: List[Dict[str, Any]]
    total: int
    warnings: List[str]


def load_graph_memories(session_id: Optional[str] = None, *, agent: Optional[str] = None) -> GraphMemories:
    """Load the newest memories for the browser graph.

    This reads each namespace store directly, the same way correction edges
    and /api/graph/memory do, so every visible node can be opened. Records
    stay keyed by (source_agent, id): identical text saved by two agents
    yields two nodes, and a malformed legacy field only drops that field.
    """
    context = get_runtime_context()
    stores = graph_store_paths(context)
    warnings = WarningCollector()
    filters = ExplorerFilters(agent=agent, session_id=session_id)
    records, more = newest_records(
        context.shared_home, filters, MAX_GRAPH_MEMORIES, warnings,
        stores=stores, text_limit=NODE_PREVIEW_CHARS + 1,
    )
    total = count_records(context.shared_home, filters, warnings, stores=stores) if more else len(records)
    memories = [{
        "id": record.memory_id,
        "source_agent": record.source_agent,
        "text": record.text,
        "type": record.memory_type,
        "stream": record.stream,
        "ts": record.timestamp,
        "session_id": record.session_id,
        "turn": record.turn,
        "embedding": list(record.embedding) if record.embedding else None,
    } for record in records]
    return GraphMemories(memories, max(total, len(memories)), warnings.as_list())


def _preview(text: str) -> tuple[str, bool]:
    if len(text) <= NODE_PREVIEW_CHARS:
        return text, False
    return text[: NODE_PREVIEW_CHARS - 1].rstrip() + "…", True


def _script_json(value: Any) -> str:
    # JSON is embedded directly in a script element, where </script> closes it
    # even inside a JavaScript string.
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def parse_memory_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        # e.g. 0001-01-01T00:00:00+01:00 parses but has no UTC equivalent.
        return None


def graph_payload(session_id: Optional[str] = None, *, agent: Optional[str] = None) -> Dict[str, Any]:
    """Nodes, links and display metadata (counts, caps, warnings) for the browser graph."""
    loaded = load_graph_memories(session_id, agent=agent)
    memories = loaded.memories
    warnings = WarningCollector()
    warnings.extend(loaded.warnings)
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=24)
    nodes = []
    for memory in memories:
        owner = memory.get("source_agent") or ""
        memory_id = memory["id"]
        recorded = parse_memory_timestamp(memory.get("ts"))
        text, text_truncated = _preview(memory.get("text") or "")
        nodes.append({
            "id": "memory:" + json.dumps([owner, memory_id], separators=(",", ":")),
            "raw_id": memory_id,
            "source_agent": owner,
            "text": text,
            "text_truncated": text_truncated,
            "type": memory.get("type") or "memory",
            "stream": memory.get("stream") or "rough",
            "ts": memory.get("ts"),
            "session_id": memory.get("session_id"),
            "turn": memory.get("turn"),
            "recent": bool(recorded and recorded >= recent_cutoff),
            "kind": "memory",
        })

    config = load_visual_config() or {}
    top_k = config.get("physics", {}).get("forces", {}).get("link", {}).get("iterations", 2)
    vectors_by_dimension: dict[int, list[tuple[str, np.ndarray]]] = {}
    for node, memory in zip(nodes, memories):
        stored = memory.get("embedding")
        if not isinstance(stored, (list, tuple)) or not stored:
            continue
        try:
            vector = np.asarray(stored, dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            continue
        if vector.ndim != 1 or not np.isfinite(vector).all():
            continue
        norm = np.linalg.norm(vector)
        if np.isfinite(norm) and norm > 0:
            vectors_by_dimension.setdefault(len(vector), []).append((node["id"], vector))

    links = []
    for entries in vectors_by_dimension.values():
        for left, right, weight in build_similarity_edges(
            [vector for _, vector in entries], top_k=top_k, min_sim=0.35,
        ):
            links.append({"source": entries[left][0], "target": entries[right][0], "kind": "similarity", "weight": weight})

    # Correction edges only join visible memories (both endpoints are passed in).
    corrections = []
    qualified = {(node["source_agent"], node["raw_id"]): node["id"] for node in nodes if node["source_agent"]}
    if qualified:
        context = get_runtime_context()
        by_key = {f"{owner}::{memory_id}": node_id for (owner, memory_id), node_id in qualified.items()}
        stored = correction_edges(context.shared_home, qualified, warnings, stores=graph_store_paths(context))
        corrections = [{**edge, "source": by_key[edge["source"]], "target": by_key[edge["target"]]} for edge in stored]

    # Corrections are kept first; the weakest similarity links give way to the cap.
    link_total = len(links) + len(corrections)
    corrections = corrections[:MAX_GRAPH_LINKS]
    room = MAX_GRAPH_LINKS - len(corrections)
    if len(links) > room:
        links = sorted(links, key=lambda link: link["weight"], reverse=True)[:room]

    similarity_graph = nx.Graph()
    similarity_graph.add_edges_from((link["source"], link["target"]) for link in links)
    groups = {
        node_id: index
        for index, community in enumerate(greedy_modularity_communities(similarity_graph))
        for node_id in community
    } if links else {}
    degrees = dict(similarity_graph.degree())
    # The UI colours nodes by agent; communities only seed the initial layout.
    for node in nodes:
        node["group"] = groups.get(node["id"])
        node["deg"] = degrees.get(node["id"], 0)

    links.extend(corrections)

    scope_parts = [f"{agent} memories" if agent else "", f"Session {session_id}" if session_id else ""]
    scope = " · ".join(part for part in scope_parts if part) or "All agents"
    graph_meta = {
        "scope": scope,
        "agent": agent,
        "session_id": session_id,
        "memory_total": loaded.total,
        "memory_shown": len(nodes),
        "memory_omitted": max(0, loaded.total - len(nodes)),
        "memory_limit": MAX_GRAPH_MEMORIES,
        "link_total": link_total,
        "link_shown": len(links),
        "link_omitted": link_total - len(links),
        "link_limit": MAX_GRAPH_LINKS,
        "warnings": warnings.as_list(),
    }
    return {"nodes": nodes, "links": links, "meta": graph_meta}


def build_graph(session_id: Optional[str] = None, *, agent: Optional[str] = None) -> str:
    try:
        payload = graph_payload(session_id, agent=agent)
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            return (
                "<html><body>"
                "<h1>Memory database is busy</h1>"
                "<p>Titan found the memory store, but another Titan process is holding a SQLite write lock. "
                "Stop duplicate Titan MCP processes or retry after the writer finishes.</p>"
                "</body></html>"
            )
        raise

    font_style, brand_mark_url = _graph_brand_assets()
    return Template(_load_ui_file("template.html")).substitute(
        scope_label=escape(payload["meta"]["scope"]),
        brand_mark_url=brand_mark_url,
        styles=font_style + _load_ui_file("styles.css"),
        canvas_script=_load_ui_file("canvas.js"),
        physics_script=_script_json(_load_physics_worker()),
        client_script=Template(_load_ui_file("client.js")).substitute(
            nodes_json=_script_json(payload["nodes"]), links_json=_script_json(payload["links"]),
            graph_meta_json=_script_json(payload["meta"]),
        ),
    )
