import json
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

import networkx as nx
import numpy as np
from networkx.algorithms.community import greedy_modularity_communities
import yaml

from app.embedding.embedder import embed
from app.graph.similarity import build_similarity_edges
from app.storage.memories import get_recent_memories


DEFAULT_GRAPH_MEMORY_LIMIT = 500


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.strip().lstrip("#")
    if len(value) != 6:
        return (160, 170, 185)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def rgba(color: str, alpha: float) -> str:
    r, g, b = hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


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


def load_memories(session_id: Optional[str] = None, limit: int = DEFAULT_GRAPH_MEMORY_LIMIT) -> List[Dict[str, Any]]:
    memories = get_recent_memories(limit=limit, session_id=session_id)
    return [mem.model_dump() if hasattr(mem, "model_dump") else dict(mem) for mem in memories]


def parse_memory_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_graph(session_id: Optional[str] = None) -> str:
    try:
        memories = load_memories(session_id)
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

    if not memories:
        return "<html><body><h1>No memories found</h1></body></html>"

    config = load_visual_config()
    facts = [mem["text"] for mem in memories]
    vectors: List[Optional[np.ndarray]] = [None for _ in memories]
    missing_texts = []
    missing_indices = []

    for idx, mem in enumerate(memories):
        stored = mem.get("embedding")
        if isinstance(stored, list) and stored:
            vectors[idx] = np.array(stored, dtype=np.float32)
        else:
            missing_texts.append(mem.get("text", ""))
            missing_indices.append(idx)

    if missing_texts:
        embedded = embed(missing_texts)
        for pos, vector in enumerate(embedded):
            vectors[missing_indices[pos]] = vector

    resolved_vectors = [vector for vector in vectors if vector is not None]
    if len(resolved_vectors) != len(memories):
        return "<html><body><h1>Error: Unable to generate embeddings for all memories</h1></body></html>"

    theme = config.get("theme", {})
    physics = config.get("physics", {})
    node_settings = config.get("nodes", {})
    edge_settings = config.get("edges", {})
    interaction = config.get("interaction", {})
    hover_settings = interaction.get("hover", {})
    camera_settings = config.get("camera", {})
    hud_settings = config.get("hud", {})
    theme_background = theme.get("background", {})
    node_style = node_settings.get("style", {})
    edge_style = edge_settings.get("style", {})

    sim_top_k = physics.get("forces", {}).get("link", {}).get("iterations", 1)
    sim_min = 0.35
    sim_edges = build_similarity_edges(resolved_vectors, top_k=sim_top_k, min_sim=sim_min)

    graph = nx.Graph()
    scope_label = "SESSION" if session_id else "GLOBAL"
    scope_title = f"Session {session_id}" if session_id else "All sessions"
    source_id = f"source:{session_id or 'global'}"

    graph.add_node(source_id, kind="document", title=scope_title)

    memory_ids = []
    for mem in memories:
        mem_id = mem["id"]
        memory_ids.append(mem_id)
        mem_type = mem.get("type")
        title = mem.get("text", "")
        if mem_type:
            title = f"[{mem_type}] {title}"
        if not session_id:
            session_value = mem.get("session_id")
            if session_value:
                title = f"{title}\nSession: {session_value[:6]}"
        graph.add_node(
            mem_id,
            kind="memory",
            title=title,
            session_id=mem.get("session_id"),
            memory_type=mem_type,
            text=mem.get("text", ""),
            stream=mem.get("stream"),
            ts=mem.get("ts"),
            turn=mem.get("turn"),
        )
        graph.add_edge(source_id, mem_id, kind="source", weight=1.0)

    for a, b, w in sim_edges:
        graph.add_edge(memories[a]["id"], memories[b]["id"], kind="similarity", weight=float(w))

    memory_subgraph = graph.subgraph(memory_ids).copy()
    communities = list(greedy_modularity_communities(memory_subgraph)) if memory_subgraph.number_of_edges() else []
    node_to_group = {}
    for group_index, community in enumerate(communities):
        for node in community:
            node_to_group[node] = group_index

    degrees = dict(list(memory_subgraph.degree())) if memory_subgraph.number_of_edges() else {}
    max_deg = max(degrees.values()) if degrees else 1

    palette = node_settings.get("colors", {}).get(
        "palette",
        ["#c3cad4", "#aab2bf", "#d5dbe3", "#b8c0cb", "#9ea8b8", "#ced4de"],
    )
    hub_threshold = node_settings.get("hub_threshold", 5)
    hub_color = node_settings.get("colors", {}).get("hub_node_color", "#e0e5ee")
    source_color = node_settings.get("colors", {}).get("source_node_color", "#9aa8bb")
    base_node_size = float(node_settings.get("base_size", 1.2))
    hub_size_multiplier = float(node_settings.get("hub_size_multiplier", 1.35))

    nodes_data = []
    for mem_id in memory_ids:
        deg = degrees.get(mem_id, 0)
        group = node_to_group.get(mem_id, 0) % len(palette)
        color = palette[group]

        if deg >= hub_threshold:
            color = hub_color
            size = base_node_size * hub_size_multiplier
        else:
            size = base_node_size

        nodes_data.append({
            "id": mem_id,
            "label": graph.nodes[mem_id].get("text", ""),
            "title": graph.nodes[mem_id].get("title", ""),
            "text": graph.nodes[mem_id].get("text", ""),
            "type": graph.nodes[mem_id].get("memory_type", ""),
            "stream": graph.nodes[mem_id].get("stream", ""),
            "ts": graph.nodes[mem_id].get("ts", ""),
            "session_id": graph.nodes[mem_id].get("session_id", ""),
            "turn": graph.nodes[mem_id].get("turn"),
            "color": color,
            "val": size,
            "group": group,
            "kind": "memory",
            "deg": deg
        })

    nodes_data.append({
        "id": source_id,
        "label": scope_label,
        "title": scope_title,
        "text": scope_title,
        "type": "scope",
        "stream": "",
        "ts": "",
        "session_id": session_id or "",
        "turn": None,
        "color": source_color,
        "val": base_node_size * 1.2,
        "group": -1,
        "kind": "document",
        "deg": len(memory_ids)
    })

    edge_colors = edge_settings.get("colors", {})
    edge_widths = edge_settings.get("width", {})
    min_width = float(edge_widths.get("min", 0.08))
    max_width = float(edge_widths.get("max", 0.42))
    source_width = float(edge_widths.get("source_width", 0.1))
    source_base_color = edge_colors.get("source", "#8f9daf")
    similarity_base_color = edge_colors.get("similarity", "#b6bfcc")
    accent_color = edge_colors.get("accent", "#8f97f8")
    source_opacity = float(edge_style.get("source_opacity", 0.1))
    similarity_min_opacity = float(edge_style.get("similarity_min_opacity", 0.06))
    similarity_max_opacity = float(edge_style.get("similarity_max_opacity", 0.22))
    accent_min_weight = float(edge_style.get("accent_min_weight", 0.72))
    accent_opacity = float(edge_style.get("accent_opacity", 0.24))

    links_data = []
    for u, v, data in graph.edges(data=True):
        kind = data.get("kind")
        weight = float(data.get("weight", 0.0))

        if kind == "source":
            color = rgba(source_base_color, source_opacity)
            width = source_width
            link_kind = "source"
        else:
            t = (weight - 0.35) / (1.0 - 0.35) if weight > 0.35 else 0.0
            t = max(0.0, min(1.0, t))
            width = min_width + (max_width - min_width) * t
            opacity = similarity_min_opacity + (similarity_max_opacity - similarity_min_opacity) * t
            if weight >= accent_min_weight:
                color = rgba(accent_color, accent_opacity)
            else:
                color = rgba(similarity_base_color, opacity)
            link_kind = "similarity"

        links_data.append({
            "source": u,
            "target": v,
            "color": color,
            "width": width,
            "kind": link_kind,
            "weight": weight
        })

    auto_rotate = interaction.get("auto_rotation", {})
    rotate_enabled = auto_rotate.get("enabled", False)
    rotate_speed = auto_rotate.get("speed", 0.002)

    hud_enabled = hud_settings.get("enabled", True)
    hud_styling = hud_settings.get("styling", {})
    hud_content = hud_settings.get("content", {})
    hud_class = "hud" if hud_enabled else "hud hidden"
    hud_hint_text = hud_content.get("hint_text", "click a node to inspect memory details")
    node_opacity = float(node_settings.get("transparency", {}).get("opacity", 0.7))
    link_opacity = float(edge_style.get("global_opacity", 0.16))
    selected_node_color = node_style.get("selected_color", "#eef2f8")
    selected_link_color = edge_colors.get("selected", rgba("#ccd6e4", 0.28))
    hover_node_color = hover_settings.get("highlight_color", "#cad3e2")
    hover_link_color = hover_settings.get("link_highlight_color", rgba("#b8c6d8", 0.2))
    node_resolution = int(node_style.get("sphere_resolution", 24))
    node_resolution = max(6, min(64, node_resolution))
    ambient_light_color = theme.get("ambient_light_color", "#6aa2ff")
    ambient_light_intensity = float(theme.get("ambient_light_intensity", 0.85))
    key_light_color = theme.get("key_light_color", "#6af7ff")
    key_light_intensity = float(theme.get("key_light_intensity", 0.95))
    key_light_position = theme.get("key_light_position", {"x": 120, "y": 140, "z": 220})
    fill_light_color = theme.get("fill_light_color", "#b44dff")
    fill_light_intensity = float(theme.get("fill_light_intensity", 0.45))
    fill_light_position = theme.get("fill_light_position", {"x": -180, "y": -80, "z": -160})

    base_background_color = theme.get("background_color", "#05070b")
    gradient_enabled = bool(theme_background.get("gradient_enabled", True))
    gradient_color = theme_background.get("gradient_color", "#1c2534")
    gradient_opacity = float(theme_background.get("gradient_opacity", 0.24))
    gradient_position = theme_background.get("gradient_position", "50% 42%")
    gradient_size = int(theme_background.get("gradient_size", 65))
    grid_enabled = bool(theme_background.get("grid_enabled", True))
    grid_size = int(theme_background.get("grid_size", 24))
    grid_color = theme_background.get("grid_color", "#2d3a4f")
    grid_opacity = float(theme_background.get("grid_opacity", 0.14))

    background_layers = [f"linear-gradient(180deg, {base_background_color} 0%, #02050a 100%)"]
    background_sizes = ["auto"]
    if gradient_enabled:
        background_layers.insert(
            0,
            f"radial-gradient(circle at {gradient_position}, {rgba(gradient_color, gradient_opacity)} 0%, {rgba(gradient_color, 0.0)} {gradient_size}%)",
        )
        background_sizes.insert(0, "auto")
    if grid_enabled:
        background_layers.insert(0, f"linear-gradient({rgba(grid_color, grid_opacity)} 1px, transparent 1px)")
        background_layers.insert(0, f"linear-gradient(90deg, {rgba(grid_color, grid_opacity)} 1px, transparent 1px)")
        background_sizes.insert(0, f"{grid_size}px {grid_size}px")
        background_sizes.insert(0, f"{grid_size}px {grid_size}px")
    background_image_css = ", ".join(background_layers)
    background_size_css = ", ".join(background_sizes)

    scope_label_html = "Session graph" if session_id else "Global graph"
    num_clusters = len(communities) if communities else 0
    stats_label = f"{len(memory_ids)} memories · {len(links_data)} connections · {num_clusters} clusters"
    sidebar_memories = []
    now_utc = datetime.now(timezone.utc)
    recent_cutoff = now_utc - timedelta(hours=24)
    for order_index, mem in enumerate(memories, start=1):
        parsed_ts = parse_memory_timestamp(mem.get("ts"))
        sidebar_memories.append(
            {
                "order": order_index,
                "id": mem.get("id", ""),
                "text": mem.get("text", ""),
                "type": mem.get("type") or "memory",
                "stream": mem.get("stream") or "rough",
                "session_id": mem.get("session_id", ""),
                "turn": mem.get("turn"),
                "ts": mem.get("ts", ""),
                "parsed_ts": parsed_ts,
                "is_recent": bool(parsed_ts and parsed_ts >= recent_cutoff),
            }
        )
    sidebar_memories.sort(
        key=lambda mem: mem.get("parsed_ts") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    recent_sidebar_memories = [mem for mem in sidebar_memories if mem["is_recent"]]
    sidebar_cards_html = []
    for mem in sidebar_memories:
        turn_value = "?" if mem.get("turn") is None else str(mem.get("turn"))
        type_label = escape(str(mem.get("type", "memory")))
        stream_label = escape(str(mem.get("stream", "rough")))
        text_label = escape(str(mem.get("text", "") or "(empty memory)"))
        session_label = escape(str(mem.get("session_id", "")))
        ts_label = escape(str(mem.get("ts", "")))
        ts_chunk = f" · {ts_label}" if ts_label else ""
        stream_dot_color = "#45ff8d" if mem.get("stream") == "learnings" else "#8a6dff"
        recent_flag = "true" if mem.get("is_recent") else "false"
        sidebar_cards_html.append(
            (
                f'<article class="memory-item" data-id="{escape(str(mem.get("id", "")))}" '
                f'data-type="{type_label}" data-stream="{stream_label}" '
                f'data-text="{text_label.lower()}" data-session="{session_label.lower()}" '
                f'data-view="all" data-recent="{recent_flag}">'
                f'<div class="memory-item-meta"><span>#{mem.get("order", "?")}</span>'
                f'<span class="memory-item-state"><span class="stream-dot" style="background:{stream_dot_color}"></span>{type_label} · {stream_label}</span></div>'
                f'<div class="memory-item-text">{text_label}</div>'
                f'<div class="memory-item-footer">session {session_label} · turn {turn_value}{ts_chunk}</div>'
                "</article>"
            )
        )
    sidebar_cards_markup = "".join(sidebar_cards_html)
    recent_count = len(recent_sidebar_memories)
    total_count = len(sidebar_memories)
    sidebar_subtitle = f"{scope_label_html} · {recent_count} recent · {total_count} total"
    recent_empty_markup = (
        '<div class="memory-empty-state" id="memoryEmptyState">'
        '<div class="memory-empty-title">no memories from the last 24 hours</div>'
        '<div class="memory-empty-copy">switch to all memories to explore older context stored in the graph.</div>'
        '<button type="button" class="memory-empty-action" id="showAllMemoriesBtn">show all memories</button>'
        "</div>"
    )

    template_html = _load_ui_file("template.html")
    css_content = _load_ui_file("styles.css")
    js_content = _load_ui_file("client.js")

    template_html = template_html.replace("{{CSS}}", css_content)
    template_html = template_html.replace("{{JS}}", js_content)

    hud_position = hud_settings.get("position", {})
    hud_pos_top = hud_position.get("top", 20)
    hud_pos_right = hud_position.get("right", 20)
    hud_width = hud_settings.get("width", 280)
    hud_max_height = hud_settings.get("max_height", 200)

    cam_pos = camera_settings.get("initial_position", {})
    cam_x = cam_pos.get("x", 0)
    cam_y = cam_pos.get("y", 0)
    cam_z = cam_pos.get("z", 400)
    cam_fov = camera_settings.get("fov", 60)

    physics_forces = physics.get("forces", {})
    charge_force = physics_forces.get("charge", {})
    link_force = physics_forces.get("link", {})
    collision_force = physics_forces.get("collision", {})

    if rotate_enabled:
        auto_rotate_code = f"""
        let rotationPaused = false;

        const autoRotate = () => {{
            if (!rotationPaused) {{
                Graph.cameraPosition({{
                    x: cam_x * Math.cos(Date.now() * {rotate_speed}) - cam_z * Math.sin(Date.now() * {rotate_speed}),
                    y: cam_y,
                    z: cam_x * Math.sin(Date.now() * {rotate_speed}) + cam_z * Math.cos(Date.now() * {rotate_speed})
                }});
            }}
            requestAnimationFrame(autoRotate);
        }};

        autoRotate();
        """
        pause_rotation_code = """
        if (!rotationPaused) {
            rotationPaused = true;
            setTimeout(() => { rotationPaused = false; }, 3000);
        }
        """
    else:
        auto_rotate_code = ""
        pause_rotation_code = ""

    html_content = Template(template_html).substitute(
        background_color=base_background_color,
        background_image=background_image_css,
        background_size=background_size_css,
        hud_width=f"{hud_width}px",
        hud_max_height=f"{hud_max_height}px",
        hud_title_text=hud_content.get("title_text", "node inspector"),
        hud_class=hud_class,
        scope_label=scope_label_html,
        sidebar_subtitle=escape(sidebar_subtitle),
        stats_label=stats_label,
        stat_memories=len(memory_ids),
        stat_connections=len(links_data),
        stat_clusters=num_clusters,
        nodes_json=json.dumps(nodes_data),
        links_json=json.dumps(links_data),
        sidebar_cards_markup=sidebar_cards_markup,
        recent_empty_markup=recent_empty_markup,
        recent_count=recent_count,
        total_count=total_count,
        node_opacity=node_opacity,
        link_opacity=link_opacity,
        selected_node_color=selected_node_color,
        selected_link_color=selected_link_color,
        hover_node_color=hover_node_color,
        hover_link_color=hover_link_color,
        node_resolution=node_resolution,
        ambient_light_color=ambient_light_color,
        ambient_light_intensity=ambient_light_intensity,
        key_light_color=key_light_color,
        key_light_intensity=key_light_intensity,
        key_light_x=key_light_position.get("x", 120),
        key_light_y=key_light_position.get("y", 140),
        key_light_z=key_light_position.get("z", 220),
        fill_light_color=fill_light_color,
        fill_light_intensity=fill_light_intensity,
        fill_light_x=fill_light_position.get("x", -180),
        fill_light_y=fill_light_position.get("y", -80),
        fill_light_z=fill_light_position.get("z", -160),
        cam_x=cam_x,
        cam_y=cam_y,
        cam_z=cam_z,
        cam_fov=cam_fov,
        auto_rotate_code=auto_rotate_code,
        pause_rotation=pause_rotation_code,
        charge_strength=charge_force.get("strength", -120),
        link_distance=link_force.get("distance", 65),
        link_strength=link_force.get("strength", 1.1),
        collision_radius=collision_force.get("radius", 8),
    )

    return html_content
