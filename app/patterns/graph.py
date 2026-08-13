from __future__ import annotations

import json
import sqlite3
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import Pattern, PatternEvidence
from .storage import resolve_pattern_db_path
from .store import PatternStore

DEFAULT_PATTERN_GRAPH_LIMIT = 500
STATUS_COLORS = {
    "candidate": "#f2c94c",
    "accepted": "#4ade80",
    "rejected": "#f87171",
    "superseded": "#94a3b8",
}
EDGE_COLORS = {
    "shared_trigger": "rgba(96, 165, 250, 0.55)",
    "shared_evidence": "rgba(168, 85, 247, 0.58)",
    "supports": "rgba(74, 222, 128, 0.58)",
    "contradicts": "rgba(248, 113, 113, 0.62)",
    "supersedes": "rgba(251, 191, 36, 0.65)",
}


def resolve_pattern_graph_db_path() -> Path:
    return resolve_pattern_db_path()


def build_pattern_graph_data(*, limit: int = DEFAULT_PATTERN_GRAPH_LIMIT, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Build a graph payload where pattern cards are nodes.

    Edges are deterministic and evidence-backed where possible:
    shared evidence/scenes, shared trigger terms, compatible support evidence,
    contradicting evidence roles, and same canonical lineage/superseded status.
    """

    safe_limit = max(1, min(int(limit or DEFAULT_PATTERN_GRAPH_LIMIT), 2000))
    path = Path(db_path) if db_path is not None else resolve_pattern_graph_db_path()
    store = PatternStore(path)
    patterns = store.list_patterns(limit=safe_limit)
    evidence_by_pattern = {pattern.id: store.list_evidence(pattern.id) for pattern in patterns}

    nodes = [_pattern_node(pattern, evidence_by_pattern.get(pattern.id, [])) for pattern in patterns]
    links = _pattern_links(patterns, evidence_by_pattern)
    status_counts: Dict[str, int] = {}
    for pattern in patterns:
        status_counts[pattern.status] = status_counts.get(pattern.status, 0) + 1

    return {
        "nodes": nodes,
        "links": links,
        "count": len(nodes),
        "edge_count": len(links),
        "status_counts": status_counts,
        "legend": {
            "node_color": "pattern status",
            "node_size": "confidence + evidence count",
            "edge_kinds": list(EDGE_COLORS.keys()),
        },
    }


def build_pattern_graph(*, limit: int = DEFAULT_PATTERN_GRAPH_LIMIT) -> str:
    try:
        data = build_pattern_graph_data(limit=limit)
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            return (
                "<html><body>"
                "<h1>Pattern database is busy</h1>"
                "<p>Titan found the pattern store, but another Titan process is holding a SQLite lock. "
                "Stop duplicate Titan processes or retry after the writer finishes.</p>"
                "</body></html>"
            )
        raise

    if not data["nodes"]:
        return "<html><body><h1>No patterns found</h1><p>Create or accept patterns, then reopen this graph.</p></body></html>"

    graph_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = "Titan Pattern Graph"
    summary = _summary_text(data)
    return f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>{escape(title)}</title>
  <script src=\"https://unpkg.com/3d-force-graph\"></script>
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; background: #080b10; color: #e5e7eb; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; overflow: hidden; }}
    #graph {{ width: 100vw; height: 100vh; }}
    #panel {{ position: fixed; top: 16px; left: 16px; z-index: 10; width: min(420px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; background: rgba(8, 11, 16, 0.82); border: 1px solid rgba(148, 163, 184, 0.28); border-radius: 16px; padding: 16px; box-shadow: 0 20px 60px rgba(0,0,0,0.35); backdrop-filter: blur(14px); }}
    h1 {{ font-size: 18px; margin: 0 0 6px; }}
    .muted {{ color: #94a3b8; font-size: 12px; line-height: 1.4; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .pill {{ display: inline-flex; align-items: center; gap: 6px; border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 999px; padding: 4px 8px; font-size: 12px; color: #cbd5e1; }}
    .dot {{ width: 9px; height: 9px; border-radius: 999px; display: inline-block; }}
    #detail {{ margin-top: 14px; white-space: pre-wrap; font-size: 13px; line-height: 1.45; color: #dbe4f0; }}
  </style>
</head>
<body>
  <div id=\"panel\">
    <h1>{escape(title)}</h1>
    <div class=\"muted\">{escape(summary)}</div>
    <div class=\"muted\">Node color = status · node size = confidence + evidence count · edges = shared evidence, triggers, supports, contradicts, supersedes.</div>
    <div class=\"legend\">
      <span class=\"pill\"><span class=\"dot\" style=\"background:{STATUS_COLORS['accepted']}\"></span>accepted</span>
      <span class=\"pill\"><span class=\"dot\" style=\"background:{STATUS_COLORS['candidate']}\"></span>candidate</span>
      <span class=\"pill\"><span class=\"dot\" style=\"background:{STATUS_COLORS['rejected']}\"></span>rejected</span>
      <span class=\"pill\"><span class=\"dot\" style=\"background:{STATUS_COLORS['superseded']}\"></span>superseded</span>
    </div>
    <div id=\"detail\">Click a pattern node to inspect its guidance and evidence.</div>
  </div>
  <div id=\"graph\"></div>
  <script>
    const graphData = {graph_json};
    const detail = document.getElementById('detail');
    const graph = ForceGraph3D()(document.getElementById('graph'))
      .graphData(graphData)
      .backgroundColor('#080b10')
      .nodeLabel(node => node.title)
      .nodeColor(node => node.color)
      .nodeVal(node => node.val)
      .linkColor(link => link.color)
      .linkWidth(link => link.width)
      .linkDirectionalParticles(link => link.kind === 'contradicts' || link.kind === 'supersedes' ? 2 : 0)
      .linkDirectionalParticleWidth(1.2)
      .onNodeClick(node => {{
        detail.textContent = [
          node.title,
          '',
          `Status: ${{node.status}} · Kind: ${{node.pattern_kind}} · Scope: ${{node.scope}} · Confidence: ${{Number(node.confidence || 0).toFixed(2)}}`,
          `Evidence: ${{node.evidence_count}} memories across ${{node.scene_count}} scenes`,
          '',
          node.summary || '',
          '',
          node.recommended_behavior ? `Recommended behavior: ${{node.recommended_behavior}}` : '',
          node.triggers?.length ? `Triggers: ${{node.triggers.join(', ')}}` : ''
        ].filter(Boolean).join('\\n');
      }});
  </script>
</body>
</html>"""


def _pattern_node(pattern: Pattern, evidence: List[PatternEvidence]) -> Dict[str, Any]:
    scene_ids = sorted({item.scene_id for item in evidence if item.scene_id})
    evidence_count = len(evidence)
    confidence = max(0.0, min(float(pattern.confidence or 0.0), 1.0))
    val = 4.0 + (confidence * 8.0) + min(evidence_count, 10) * 0.65
    return {
        "id": pattern.id,
        "label": pattern.title,
        "title": pattern.title,
        "status": pattern.status,
        "pattern_kind": pattern.kind,
        "scope": pattern.scope,
        "summary": pattern.summary,
        "recommended_behavior": pattern.recommended_behavior,
        "triggers": list(pattern.trigger_terms),
        "confidence": confidence,
        "evidence_count": evidence_count,
        "scene_count": len(scene_ids),
        "scene_ids": scene_ids,
        "canonical_key": pattern.canonical_key,
        "color": STATUS_COLORS.get(pattern.status, "#cbd5e1"),
        "val": round(val, 3),
    }


def _pattern_links(patterns: List[Pattern], evidence_by_pattern: Dict[str, List[PatternEvidence]]) -> List[Dict[str, Any]]:
    links: List[Dict[str, Any]] = []
    for index, left in enumerate(patterns):
        for right in patterns[index + 1:]:
            links.extend(_pair_links(left, right, evidence_by_pattern.get(left.id, []), evidence_by_pattern.get(right.id, [])))
    return links


def _pair_links(
    left: Pattern,
    right: Pattern,
    left_evidence: List[PatternEvidence],
    right_evidence: List[PatternEvidence],
) -> List[Dict[str, Any]]:
    links: List[Dict[str, Any]] = []
    left_triggers = _normalized_set(left.trigger_terms)
    right_triggers = _normalized_set(right.trigger_terms)
    shared_triggers = sorted(left_triggers & right_triggers)
    if shared_triggers:
        links.append(_link(left.id, right.id, "shared_trigger", len(shared_triggers), shared_triggers[:8]))

    left_mem_roles = _memory_roles(left_evidence)
    right_mem_roles = _memory_roles(right_evidence)
    shared_memory_ids = sorted(set(left_mem_roles) & set(right_mem_roles))
    if shared_memory_ids:
        if any("contradict" in left_mem_roles[mid] or "contradict" in right_mem_roles[mid] for mid in shared_memory_ids):
            links.append(_link(left.id, right.id, "contradicts", len(shared_memory_ids), shared_memory_ids[:8]))
        elif any((left_mem_roles[mid] & {"support", "central", "bridge"}) and (right_mem_roles[mid] & {"support", "central", "bridge"}) for mid in shared_memory_ids):
            links.append(_link(left.id, right.id, "supports", len(shared_memory_ids), shared_memory_ids[:8]))
        else:
            links.append(_link(left.id, right.id, "shared_evidence", len(shared_memory_ids), shared_memory_ids[:8]))

    left_scenes = {item.scene_id for item in left_evidence if item.scene_id}
    right_scenes = {item.scene_id for item in right_evidence if item.scene_id}
    shared_scenes = sorted(left_scenes & right_scenes)
    if shared_scenes and not shared_memory_ids:
        links.append(_link(left.id, right.id, "shared_evidence", len(shared_scenes), shared_scenes[:8]))

    if left.canonical_key and left.canonical_key == right.canonical_key and "superseded" in {left.status, right.status}:
        source, target = (right.id, left.id) if left.status == "superseded" else (left.id, right.id)
        links.append(_link(source, target, "supersedes", 1, [left.canonical_key]))

    return links


def _normalized_set(values: Iterable[str]) -> set[str]:
    return {" ".join(str(value).lower().split()) for value in values if str(value).strip()}


def _memory_roles(evidence: Iterable[PatternEvidence]) -> Dict[str, set[str]]:
    roles: Dict[str, set[str]] = {}
    for item in evidence:
        roles.setdefault(item.memory_id, set()).add(item.role)
    return roles


def _link(source: str, target: str, kind: str, weight: int, shared: List[str]) -> Dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "kind": kind,
        "label": kind.replace("_", " "),
        "weight": weight,
        "shared": shared,
        "color": EDGE_COLORS.get(kind, "rgba(148, 163, 184, 0.4)"),
        "width": round(0.5 + min(weight, 8) * 0.22, 3),
    }


def _summary_text(data: Dict[str, Any]) -> str:
    counts = data.get("status_counts") or {}
    parts = [f"{data.get('count', 0)} patterns", f"{data.get('edge_count', 0)} edges"]
    for status in ["accepted", "candidate", "rejected", "superseded"]:
        if counts.get(status):
            parts.append(f"{counts[status]} {status}")
    return " · ".join(parts)
