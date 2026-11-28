"""Lightweight forwarding boundary for optional graph features."""

from pathlib import Path
from typing import Any, Dict, Optional


def graph_store_paths(context: Any) -> Dict[str, Path]:
    """The active agent's store, exactly as the runtime resolved it.

    The browser graph, its correction edges and /api/graph/memory all read
    through this map. It mirrors ``get_memory_repository()``: the JSON backend
    maps to ``<base_dir>/out/memories/memories.json`` (a ``.json`` path, read
    even if a stale SQLite file sits beside it), and the SQLite backend maps to
    ``memory_db_path`` wherever it resolved (an override, or a default outside
    ``<shared_home>/agents``). Other namespaces are still discovered under
    ``<shared_home>/agents``, so federated reads keep working.
    """
    agent = getattr(context, "agent_name", None)
    if not agent:
        return {}
    backend = str(getattr(context, "memory_backend", None) or "sqlite").strip().lower()
    if backend == "json":
        base_dir = getattr(context, "base_dir", None)
        if base_dir is None:
            return {}
        return {str(agent): Path(base_dir) / "out" / "memories" / "memories.json"}
    db_path = getattr(context, "memory_db_path", None)
    return {str(agent): Path(db_path)} if db_path else {}


def build_graph(session_id: Optional[str] = None, *, agent: Optional[str] = None) -> str:
    from .builder import build_graph as build_graph_impl

    if agent:
        return build_graph_impl(session_id=session_id, agent=agent)
    return build_graph_impl(session_id=session_id)


def inspect_memory_clusters(**kwargs) -> dict:
    from .clusters import inspect_memory_clusters as inspect_memory_clusters_impl

    return inspect_memory_clusters_impl(**kwargs)


def analyze_memory_clusters(**kwargs) -> dict:
    from .cortex_analysis import analyze_memory_clusters as analyze_memory_clusters_impl

    return analyze_memory_clusters_impl(**kwargs)
