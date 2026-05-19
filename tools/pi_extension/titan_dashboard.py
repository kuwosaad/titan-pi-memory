#!/usr/bin/env python3
"""
Titan Memory Dashboard — a rich terminal UI for Titan's brain.
Run via `/titan-dashboard` inside Pi, or directly: python3 titan_dashboard.py
"""

import argparse
import os
import sys
import time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _api_base() -> str:
    return os.environ.get("TITAN_PI_API_URL", "http://127.0.0.1:8002")


def _get(path: str, **params: Any) -> Dict[str, Any]:
    """Make a GET request to the Titan API. Returns parsed JSON or error dict."""
    import urllib.request
    import urllib.parse
    import json as json_module

    url = f"{_api_base()}{path}"
    if params:
        cleaned = {k: v for k, v in params.items() if v is not None}
        if cleaned:
            url += "?" + urllib.parse.urlencode(cleaned, doseq=True)

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json_module.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if e.fp else ""
        return {"error": f"HTTP {e.code}", "detail": body[:500]}
    except urllib.error.URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def fetch_health() -> Dict[str, Any]:
    return _get("/health")


def fetch_clusters(session_id: Optional[str] = None, limit: int = 1000,
                   detail_limit: int = 6) -> Dict[str, Any]:
    return _get("/api/clusters", session_id=session_id, limit=limit, detail_limit=detail_limit)


def fetch_memories(session_id: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    return _get("/api/memories", session_id=session_id, limit=limit)


def fetch_pipeline_debug(session_id: Optional[str] = None) -> Dict[str, Any]:
    return _get("/api/debug/pipeline", session_id=session_id)


def fetch_storage_stats() -> Dict[str, Any]:
    return _get("/api/storage/stats")


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------

TITAN_BANNER = r"""
  ╔══════════════════════════════════════════════╗
  ║   ████████╗██╗████████╗ █████╗ ███╗   ██╗   ║
  ║   ╚══██╔══╝██║╚══██╔══╝██╔══██╗████╗  ██║   ║
  ║      ██║   ██║   ██║   ███████║██╔██╗ ██║   ║
  ║      ██║   ██║   ██║   ██╔══██║██║╚██╗██║   ║
  ║      ██║   ██║   ██║   ██║  ██║██║ ╚████║   ║
  ║      ╚═╝   ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝   ║
  ╚══════════════════════════════════════════════╝
"""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _truncate(text: str, limit: int = 120) -> str:
    text = " ".join(_safe_str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_timestamp(ts: Optional[str]) -> str:
    if not ts:
        return "unknown"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        if delta.seconds > 3600:
            return f"{delta.seconds // 3600}h ago"
        if delta.seconds > 60:
            return f"{delta.seconds // 60}m ago"
        return "just now"
    except Exception:
        return "?"


def build_dashboard(
    session_id: Optional[str] = None,
    *,
    use_rich: bool = True,
) -> str:
    """Fetch all Titan data and render a beautiful dashboard. Returns a string."""
    if not use_rich:
        return _build_plain_dashboard(session_id)

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich.layout import Layout
        from rich import box
        from rich.columns import Columns
    except ImportError:
        return _build_plain_dashboard(session_id)

    from io import StringIO

    # Fetch data
    health = fetch_health()
    clusters = fetch_clusters(session_id=session_id, detail_limit=6)
    memories = fetch_memories(session_id=session_id, limit=12)
    pipeline = fetch_pipeline_debug(session_id=session_id)
    storage = fetch_storage_stats()

    # Determine status
    server_ok = "status" in health and health.get("status") == "ok"

    # Colors
    PRIMARY = "bright_cyan"
    ACCENT = "magenta"
    SUCCESS = "green"
    WARNING = "yellow"
    DANGER = "red"
    DIM = "dim"

    output = StringIO()
    console = Console(file=output, width=120, force_terminal=True, color_system="standard")

    # --- Helper to build a panel ---
    def panel(title: str, content: Any, *, border_style: str = PRIMARY) -> Panel:
        return Panel(
            content,
            title=title,
            border_style=border_style,
            title_align="left",
            box=box.ROUNDED,
            padding=(1, 2),
        )

    # --- Header ---
    header = Text()
    header.append("TITAN", style=f"bold {PRIMARY}")
    header.append(" MEMORY DASHBOARD", style="bold white")
    if session_id:
        header.append(f"\nSession: {session_id[:20]}…", style=DIM)

    # --- Server health line ---
    status_text = Text()
    if server_ok:
        status_text.append("●  ", style=SUCCESS)
        status_text.append(f"Server running", style=SUCCESS)
        status_text.append(f"  ({_api_base()})", style=DIM)
    else:
        status_text.append("●  ", style=DANGER)
        status_text.append("Server offline", style=DANGER)

    # --- Stats row ---
    stats_table = Table(show_header=False, box=None, padding=(0, 3), expand=False)
    stats_table.add_column(style=DIM, justify="right")
    stats_table.add_column(style="bold white")

    # Get accurate counts. /api/memories "count" is page size; "total" is the full DB count.
    # Clusters may analyze a capped sample, so keep total vs analyzed separate.
    total_mem_count = _safe_int(
        memories.get("total", clusters.get("total_memory_count", clusters.get("raw_memory_count", 0)))
    )
    analyzed_mem_count = _safe_int(clusters.get("raw_memory_count", clusters.get("memory_count", 0)))
    cluster_count = _safe_int(clusters.get("cluster_count", 0))
    # Only count similarity edges (graph UI includes source edges which inflates the count)
    connection_count = _safe_int(clusters.get("connection_count", 0))
    skipped_no_emb = _safe_int(clusters.get("skipped_missing_embeddings", 0))

    # Pipeline stats are only available for session-scoped debug payloads.
    spool_events = pipeline.get("spool_events")
    buffer_size = pipeline.get("dedup_buffer_size")
    retry_queue_size = _safe_int(pipeline.get("retry_queue_size", 0))

    stats_table.add_row("Memories", f"{total_mem_count}")
    if analyzed_mem_count and analyzed_mem_count != total_mem_count:
        stats_table.add_row("Analyzed", f"{analyzed_mem_count} recent")
    stats_table.add_row("Clusters", f"{cluster_count}")
    stats_table.add_row("Edges", f"{connection_count} similarity")
    if skipped_no_emb:
        stats_table.add_row("No embedding", f"{skipped_no_emb} skipped")
    if spool_events is not None:
        stats_table.add_row("Spool", f"{_safe_int(spool_events)} events")
    if buffer_size is not None:
        stats_table.add_row("Buffer", f"{_safe_int(buffer_size)}")
    if retry_queue_size:
        stats_table.add_row("Retry queue", f"{retry_queue_size}")

    stats_panel = panel("📊 Stats", stats_table, border_style="bright_blue")

    # --- Storage stats ---
    storage_table = Table(show_header=False, box=None, padding=(0, 3), expand=False)
    storage_table.add_column(style=DIM, justify="right")
    storage_table.add_column(style="bold white")

    def _fmt_bytes(b: Any) -> str:
        b = _safe_int(b)
        if b >= 1048576:
            return f"{b / 1048576:.2f} MB"
        if b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"

    mem_count = storage.get("memory_count", 0)
    if mem_count:
        storage_table.add_row("Memory text", _fmt_bytes(storage.get("memory_text_bytes")))
        storage_table.add_row("Median memory", _fmt_bytes(storage.get("memory_median_text_bytes")))
        storage_table.add_row("Scenes text", _fmt_bytes(storage.get("scene_total_bytes")))
        storage_table.add_row("DB file", _fmt_bytes(storage.get("db_file_size_bytes")))
        spool_sz = storage.get("spool_size_bytes", 0)
        if spool_sz:
            storage_table.add_row("Spool", _fmt_bytes(spool_sz))
        storage_table.add_row("", "───", style="bold white")
        storage_table.add_row("Total", _fmt_bytes(storage.get("total_footprint_bytes")), style="bold")
    else:
        storage_table.add_row("Stats", "unavailable", style=DIM)

    storage_panel = panel("💾 Storage", storage_table, border_style="bright_green")

    # --- Cluster topics ---
    cluster_list = clusters.get("clusters", [])
    if cluster_list:
        cluster_rows: List[tuple] = []
        for c in cluster_list[:12]:
            cid = c.get("cluster_id", "?")
            topic = _safe_str(c.get("topic", "unknown"))
            count = _safe_int(c.get("memory_count", 0))
            links = _safe_int(c.get("connection_count", 0))
            keywords = c.get("keywords", [])[:4]

            # Build keyword badges
            kw_text = Text()
            for i, kw in enumerate(keywords):
                if i > 0:
                    kw_text.append(" ")
                kw_text.append(kw, style=f"bold {DIM}")

            cluster_rows.append((str(cid), topic, str(count), str(links), kw_text))

        cluster_table = Table(
            "ID", "Topic", "Mem", "Links", "Keywords",
            box=box.SIMPLE,
            padding=(0, 1),
        )
        cluster_table.columns[0].style = DIM
        cluster_table.columns[1].style = "bold white"
        cluster_table.columns[2].style = SUCCESS
        cluster_table.columns[3].style = ACCENT
        for row in cluster_rows:
            cluster_table.add_row(*row)
    else:
        cluster_table = Text("No clusters yet — memories need embeddings.", style=DIM)

    cluster_panel = panel("🧠 Memory Clusters", cluster_table, border_style="bright_magenta")

    # --- Recent memories ---
    recent = memories.get("memories", [])
    if recent:
        mem_lines = Text()
        for i, mem in enumerate(recent[:10], 1):
            ts = _format_timestamp(mem.get("ts"))
            text = _truncate(_safe_str(mem.get("text", "")), 100)
            mtype = _safe_str(mem.get("type", "memory"))
            stream = _safe_str(mem.get("stream", "rough"))

            color = SUCCESS if stream == "learnings" else "white"
            mem_lines.append(f"{i:>2}. ", style=DIM)
            mem_lines.append(f"[{mtype}] ", style=ACCENT)

            # Time indicator
            if ts in ("just now", "?m ago"):
                mem_lines.append(f"({ts}) ", style=SUCCESS)
            else:
                mem_lines.append(f"({ts}) ", style=DIM)

            mem_lines.append(text, style=color)
            mem_lines.append("\n")
    else:
        mem_lines = Text("No memories stored yet.", style=DIM)

    mem_panel = panel("📝 Recent Memories", mem_lines, border_style="green")

    # --- Pipeline status ---
    pipeline_ok = pipeline.get("status") == "ok" or pipeline.get("auto_ingest_running", False)
    pipeline_text = Text()
    if not session_id and "spool_latest_ts" not in pipeline:
        pipeline_text.append("●  ", style=SUCCESS)
        pipeline_text.append("Global memory view", style=SUCCESS)
        pipeline_text.append("\nUse /titan-dashboard <session_id> for ingest lag.", style=DIM)
    elif pipeline_ok:
        pipeline_text.append("●  ", style=SUCCESS)
        pipeline_text.append("Pipeline active", style=SUCCESS)
    else:
        pipeline_text.append("●  ", style=WARNING)
        pipeline_text.append("Pipeline status unknown", style=WARNING)

    last_ingest = pipeline.get("last_ingest_ts")
    if last_ingest:
        pipeline_text.append(f"\nLast ingest: {_format_timestamp(last_ingest)}", style=DIM)

    # --- Compose and print to console ---
    console.print(header)
    console.print()
    console.print(status_text)
    console.print()

    # Stats + Pipeline side by side
    console.print(Columns([stats_panel, panel("⚙️ Pipeline", pipeline_text, border_style="yellow"), storage_panel]))
    console.print()

    # Clusters full width
    console.print(cluster_panel)
    console.print()

    # Memories
    console.print(mem_panel)

    # Footer
    console.print()
    console.print("─" * 118, style=DIM)
    console.print("Run ", style=DIM, end="")
    console.print("/titan-dashboard", style=f"bold {PRIMARY}", end="")
    console.print(" to refresh  │  ", style=DIM, end="")
    console.print("/titan-graph", style=f"bold {PRIMARY}", end="")
    console.print(" for the 3D graph  │  ", style=DIM, end="")
    console.print("/titan-clusters", style=f"bold {PRIMARY}", end="")
    console.print(" for cluster details", style=DIM)

    return output.getvalue()


def _build_plain_dashboard(session_id: Optional[str] = None) -> str:
    """Fallback plain-text dashboard when rich is not available."""
    health = fetch_health()
    clusters = fetch_clusters(session_id=session_id, detail_limit=6)
    memories = fetch_memories(session_id=session_id, limit=10)
    pipeline = fetch_pipeline_debug(session_id=session_id)
    storage = fetch_storage_stats()

    server_ok = "status" in health and health.get("status") == "ok"
    lines: List[str] = []

    lines.append("")
    lines.append("  ╔════════════════════════════════╗")
    lines.append("  ║    TITAN MEMORY DASHBOARD      ║")
    lines.append("  ╚════════════════════════════════╝")
    lines.append("")

    # Status
    total_mem = _safe_int(memories.get("total", clusters.get("total_memory_count", clusters.get("raw_memory_count", 0))))
    analyzed_mem = _safe_int(clusters.get("raw_memory_count", clusters.get("memory_count", 0)))
    lines.append(f"  Server: {'● ONLINE' if server_ok else '○ OFFLINE'}  ({_api_base()})")
    lines.append(f"  Memories: {total_mem}")
    if analyzed_mem and analyzed_mem != total_mem:
        lines.append(f"  Analyzed: {analyzed_mem} recent memories")
    lines.append(f"  Clusters: {_safe_int(clusters.get('cluster_count', 0))}")
    lines.append(f"  Edges: {_safe_int(clusters.get('connection_count', 0))} similarity")
    spool_ev = pipeline.get("spool_events")
    buf = pipeline.get("dedup_buffer_size")
    retry = _safe_int(pipeline.get("retry_queue_size", 0))
    if spool_ev is not None or buf is not None:
        lines.append(f"  Spool: {_safe_int(spool_ev)} events  |  Buffer: {_safe_int(buf)}")
    elif retry:
        lines.append(f"  Retry queue: {retry}")

    # Storage
    if storage.get("memory_count", 0):
        def _fmt(b):
            b = _safe_int(b)
            if b >= 1048576: return f"{b/1048576:.2f} MB"
            if b >= 1024: return f"{b/1024:.1f} KB"
            return f"{b} B"
        lines.append(f"  Memory text: {_fmt(storage.get('memory_text_bytes'))}")
        lines.append(f"  Median memory: {_fmt(storage.get('memory_median_text_bytes'))}")
        lines.append(f"  Scenes: {_fmt(storage.get('scene_total_bytes'))}")
        lines.append(f"  DB file: {_fmt(storage.get('db_file_size_bytes'))}")
        sp = storage.get("spool_size_bytes", 0)
        if sp:
            lines.append(f"  Spool: {_fmt(sp)}")
        lines.append(f"  Total: {_fmt(storage.get('total_footprint_bytes'))}")
    lines.append("")

    # Clusters
    if clusters.get("clusters"):
        lines.append("  ── Clusters ──")
        for c in clusters["clusters"][:12]:
            cid = c.get("cluster_id", "?")
            topic = _safe_str(c.get("topic", "?"))
            count = _safe_int(c.get("memory_count", 0))
            keywords = ", ".join((c.get("keywords") or [])[:4])
            lines.append(f"  {cid:>2}. {topic}  ({count} memories)  [{keywords}]")
    else:
        lines.append("  No clusters yet.")

    # Memories
    lines.append("")
    lines.append("  ── Recent Memories ──")
    for i, mem in enumerate((memories.get("memories") or [])[:10], 1):
        ts = _format_timestamp(mem.get("ts"))
        text = _truncate(_safe_str(mem.get("text", "")), 90)
        mtype = _safe_str(mem.get("type", "memory"))
        lines.append(f"  {i:>2}. [{mtype}] ({ts}) {text}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Titan Memory Dashboard")
    parser.add_argument("--session-id", type=str, default=None,
                        help="Scope to a specific Titan session")
    parser.add_argument("--plain", action="store_true",
                        help="Force plain-text output (no rich)")
    args = parser.parse_args()

    try:
        rendered = build_dashboard(session_id=args.session_id, use_rich=not args.plain)
    except Exception as e:
        rendered = _build_plain_dashboard(args.session_id)
        rendered += f"\n\n[!] Dashboard error: {e}"

    print(rendered)


if __name__ == "__main__":
    main()
