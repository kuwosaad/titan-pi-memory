"""Lightweight forwarding boundary for optional graph features."""

from typing import Optional


def build_graph(session_id: Optional[str] = None) -> str:
    from .builder import build_graph as build_graph_impl

    return build_graph_impl(session_id=session_id)


def inspect_memory_clusters(**kwargs) -> dict:
    from .clusters import inspect_memory_clusters as inspect_memory_clusters_impl

    return inspect_memory_clusters_impl(**kwargs)


def analyze_memory_clusters(**kwargs) -> dict:
    from .cortex_analysis import analyze_memory_clusters as analyze_memory_clusters_impl

    return analyze_memory_clusters_impl(**kwargs)
