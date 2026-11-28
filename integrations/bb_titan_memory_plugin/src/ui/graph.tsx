import { useEffect, useMemo, useRef, useState } from "react";
import type { ConfigOptions, ForceGraph3DInstance } from "3d-force-graph";
import type { Mesh, MeshBasicMaterial, SphereGeometry } from "three";
import type { ExplorerSnapshot, MemorySummary } from "../contract.js";
import {
  AGENT_PALETTE,
  agentColors,
  CORRECTION_COLOR,
  LOCAL_AGENT,
  SELECTION_COLOR,
  shortText,
} from "./model.js";
import {
  GRAPH_HEIGHT,
  GRAPH_WIDTH,
  layoutGraph,
  neighborMap,
  type GraphPoint,
} from "./graph-layout.js";
import "./graph.css";

export type RelationshipFilter = "all" | ExplorerSnapshot["edges"][number]["kind"];

interface GraphProps {
  snapshot: ExplorerSnapshot;
  selectedNodeId: string | null;
  onSelect: (memory: MemorySummary) => void;
  relationshipFilter: RelationshipFilter;
  onRelationshipFilterChange: (value: RelationshipFilter) => void;
  /** Every agent in scope, so colours stay stable while filters change. */
  agents?: readonly string[];
  agentFilter?: string | null;
  onAgentFilterChange?: (agent: string | null) => void;
}

type GraphNode = GraphPoint & {
  id: string;
  val: number;
  color: string;
  x: number;
  y: number;
  z: number;
};

type GraphLink = {
  source: string | GraphNode;
  target: string | GraphNode;
  weight: number;
  kind: "similarity" | "supersedes";
  curvature: number;
};

type ForceGraph = ForceGraph3DInstance<GraphNode, GraphLink>;
type ForceGraphFactory = new (element: HTMLElement, options?: ConfigOptions) => ForceGraph;
type NodeObject = { mesh: Mesh<SphereGeometry, MeshBasicMaterial>; halo: Mesh; radius: number };

export type GraphColors = {
  correction: string;
  correctionDim: string;
  similarity: string;
  similarityFocus: string;
  similarityDim: string;
  selection: string;
};

// Mirrors the custom properties in graph.css. The canvas reads the CSS values
// at mount so the legend swatches and the drawn links cannot drift apart.
export const DEFAULT_GRAPH_COLORS: GraphColors = {
  correction: CORRECTION_COLOR,
  correctionDim: "#4A3D2E",
  similarity: "#B8B0B2",
  similarityFocus: "#E4DEDF",
  similarityDim: "#3A3738",
  selection: SELECTION_COLOR,
};

const COLOR_TOKENS: Record<keyof GraphColors, string> = {
  correction: "--titan-edge-correction",
  correctionDim: "--titan-edge-correction-dim",
  similarity: "--titan-edge-similarity",
  similarityFocus: "--titan-edge-similarity-focus",
  similarityDim: "--titan-edge-dim",
  selection: "--titan-graph-selection",
};

const IDLE_WINDOW_MS = 900;
// Frames kept after the layout stops (or after a camera tween) so damping and
// the last tween step land before the renderer pauses.
const STOP_GRACE_MS = 160;
const SETTLE_WINDOW_MS = 1600;
const FIT_PADDING = 24;
export const WEBGL_UNAVAILABLE = "The 3D graph needs WebGL, which is not available here. Use the memories list to browse.";

export function readGraphColors(element: Element | null): GraphColors {
  if (!element || typeof getComputedStyle !== "function") return DEFAULT_GRAPH_COLORS;
  const style = getComputedStyle(element);
  const colors = { ...DEFAULT_GRAPH_COLORS };
  for (const key of Object.keys(COLOR_TOKENS) as Array<keyof GraphColors>) {
    const value = style.getPropertyValue(COLOR_TOKENS[key]).trim();
    if (/^#[0-9a-f]{6}$/i.test(value)) colors[key] = value;
  }
  return colors;
}

export function webglAvailable(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2") || canvas.getContext("webgl");
    // Release the probe context right away; browsers cap live WebGL contexts.
    context?.getExtension("WEBGL_lose_context")?.loseContext();
    return Boolean(context);
  } catch {
    return false;
  }
}

function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function titleCase(value: string): string {
  return value.replace(/(^|[\s_-])(\p{L})/gu, (_match, gap: string, letter: string) => `${gap}${letter.toUpperCase()}`);
}

function endpointId(endpoint: string | GraphNode): string {
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

function isConnected(link: GraphLink, nodeId: string | null): boolean {
  if (!nodeId) return true;
  const source = endpointId(link.source);
  const target = endpointId(link.target);
  return source === nodeId || target === nodeId;
}

export function connectionColor(source: string, target: string, focus: string | null, colors = DEFAULT_GRAPH_COLORS): string {
  if (!focus) return colors.similarity;
  return source === focus || target === focus ? colors.similarityFocus : colors.similarityDim;
}

function nodeRadius(degree: number): number {
  return 2.4 + Math.min(2.5, Math.sqrt(degree) * 0.65);
}

function pairKey(left: string, right: string): string {
  return left < right ? `${left}\u0000${right}` : `${right}\u0000${left}`;
}

function graphData(
  snapshot: ExplorerSnapshot,
  points: readonly GraphPoint[],
  palette: ReadonlyMap<string, string>,
): { nodes: GraphNode[]; links: GraphLink[] } {
  const byId = new Map(points.map((point) => [point.nodeId, point]));
  const nodes = points.map((point) => ({
    ...point,
    id: point.nodeId,
    val: 1.2 + Math.min(1.7, Math.sqrt(point.degree + 1) * 0.28),
    color: palette.get(point.sourceAgent || LOCAL_AGENT) || AGENT_PALETTE[0],
    x: (point.x / GRAPH_WIDTH - 0.5) * 260,
    y: (point.y / GRAPH_HEIGHT - 0.5) * 180,
    // Keep real depth for orbit, but do not let the z extent dominate fit-to-view.
    z: point.z * 90,
  }));
  const pairCounts = new Map<string, number>();
  for (const edge of snapshot.edges) {
    const key = pairKey(edge.source, edge.target);
    pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
  }
  const links = snapshot.edges.flatMap((edge) => {
    if (!byId.has(edge.source) || !byId.has(edge.target)) return [];
    // Curve a correction that shares its endpoints with another link, so a
    // reciprocal correction (curving the other way) or a similarity line stays visible.
    const shared = (pairCounts.get(pairKey(edge.source, edge.target)) || 0) > 1;
    return [{
      source: edge.source,
      target: edge.target,
      weight: Number(edge.weight),
      kind: edge.kind,
      curvature: edge.kind === "supersedes" && shared ? 0.3 : 0,
    }];
  });
  return { nodes, links };
}

export function MemoryGraph({
  snapshot,
  selectedNodeId,
  onSelect,
  relationshipFilter,
  onRelationshipFilterChange,
  agents,
  agentFilter = null,
  onAgentFilterChange,
}: GraphProps) {
  const visibleEdges = useMemo(() => snapshot.edges.filter((edge) => relationshipFilter === "all" || edge.kind === relationshipFilter), [snapshot.edges, relationshipFilter]);
  const legendAgents = useMemo(() => {
    const names = new Set<string>(agents || []);
    for (const node of snapshot.nodes) names.add(node.sourceAgent || LOCAL_AGENT);
    if (agentFilter) names.add(agentFilter);
    return Array.from(names).sort((left, right) => left.localeCompare(right));
  }, [agents, agentFilter, snapshot.nodes]);
  const palette = useMemo(() => agentColors(legendAgents), [legendAgents]);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph | null>(null);
  const applyDataRef = useRef<((next: { nodes: GraphNode[]; links: GraphLink[] }) => void) | null>(null);
  const updateAppearanceRef = useRef<(() => void) | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wakeRef = useRef<((duration?: number) => void) | null>(null);
  const pointerActiveRef = useRef(false);
  const hoveredRef = useRef<string | null>(null);
  const selectedRef = useRef(selectedNodeId);
  const relationshipFilterRef = useRef(relationshipFilter);
  const onSelectRef = useRef(onSelect);
  const neighborsRef = useRef<Map<string, Set<string>>>(new Map());
  const data = useMemo(() => graphData(snapshot, layoutGraph(snapshot.nodes, snapshot.edges), palette), [snapshot, palette]);
  const dataRef = useRef(data);
  const neighbors = useMemo(() => neighborMap(visibleEdges), [visibleEdges]);
  const totalCorrections = snapshot.edges.filter((edge) => edge.kind === "supersedes").length;
  const totalSimilarity = snapshot.edges.length - totalCorrections;
  const correctionCount = visibleEdges.filter((edge) => edge.kind === "supersedes").length;
  const similarityCount = visibleEdges.length - correctionCount;
  const [webgl] = useState(webglAvailable);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(webgl);
  const [loadError, setLoadError] = useState<string | null>(webgl ? null : WEBGL_UNAVAILABLE);
  const graphReady = webgl && !loadError;

  hoveredRef.current = hoveredNodeId;
  selectedRef.current = selectedNodeId;
  relationshipFilterRef.current = relationshipFilter;
  onSelectRef.current = onSelect;
  neighborsRef.current = neighbors;
  dataRef.current = data;

  useEffect(() => {
    if (!webgl) return;
    let cancelled = false;
    let graph: ForceGraph | null = null;
    const listenerCleanups: Array<() => void> = [];
    let resizeObserver: ResizeObserver | null = null;
    let earlyFitTimer: ReturnType<typeof setTimeout> | null = null;
    let pendingFit = false;
    let firstFit = true;
    const reducedMotion = prefersReducedMotion();
    const colors = readGraphColors(hostRef.current);
    const visualDisposables: Array<{ dispose: () => void }> = [];
    const objects = new Map<string, NodeObject>();

    const clearIdleTimer = () => {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    };
    const pause = () => {
      clearIdleTimer();
      graph?.pauseAnimation();
    };
    // Keeps frames running for at least `duration` ms, never shortening a
    // longer window already granted (a camera tween or gesture may need it).
    // The pause always runs from a timer, outside the library's frame
    // callback: pausing inside onEngineStop would cancel the frame that is
    // already running, and the library would then schedule the next one.
    let idleDeadline = 0;
    const wake = (duration = IDLE_WINDOW_MS) => {
      if (cancelled || !graph) return;
      graph.resumeAnimation();
      const now = Date.now();
      if (idleTimerRef.current && idleDeadline >= now + duration) return;
      clearIdleTimer();
      idleDeadline = now + duration;
      const idle = () => {
        idleTimerRef.current = null;
        if (cancelled) return;
        // An active gesture keeps rendering; its pointerup wakes a new window.
        if (pointerActiveRef.current) return;
        const remaining = idleDeadline - Date.now();
        if (remaining > 0) idleTimerRef.current = setTimeout(idle, remaining);
        else graph?.pauseAnimation();
      };
      idleTimerRef.current = setTimeout(idle, duration);
    };
    wakeRef.current = wake;
    const cancelPendingFit = () => {
      pendingFit = false;
      if (earlyFitTimer) clearTimeout(earlyFitTimer);
      earlyFitTimer = null;
    };
    const addListener = (target: EventTarget, type: string, listener: EventListener, options?: AddEventListenerOptions) => {
      target.addEventListener(type, listener, options);
      listenerCleanups.push(() => target.removeEventListener(type, listener, options));
    };
    const linkColor = (link: GraphLink) => {
      const focus = hoveredRef.current || selectedRef.current;
      if (link.kind === "supersedes") return !focus || isConnected(link, focus) ? colors.correction : colors.correctionDim;
      return connectionColor(endpointId(link.source), endpointId(link.target), focus, colors);
    };
    const linkVisible = (link: GraphLink) => relationshipFilterRef.current === "all" || link.kind === relationshipFilterRef.current;

    const mount = async () => {
      try {
        const [{ default: ForceGraph3D }, THREE] = await Promise.all([
          import("3d-force-graph"),
          import("three"),
        ]);
        if (cancelled || !hostRef.current) return;
        const ForceGraph = ForceGraph3D as unknown as ForceGraphFactory;
        graph = new ForceGraph(hostRef.current, {
          controlType: "orbit",
          rendererConfig: { antialias: true, alpha: true, powerPreference: "low-power" },
        });
        graphRef.current = graph;
        graph
          .backgroundColor("rgba(0, 0, 0, 0)")
          .showNavInfo(false)
          .enableNavigationControls(true)
          .enableNodeDrag(true)
          .nodeId("id")
          .nodeLabel(() => "")
          .nodeRelSize(3.7)
          .nodeOpacity(0.82)
          .nodeResolution(7)
          .linkColor(linkColor)
          .linkWidth((link) => {
            const focus = hoveredRef.current || selectedRef.current;
            return !focus ? (link.kind === "supersedes" ? 1.8 : 0.45 + link.weight * 0.35) : isConnected(link, focus) ? (link.kind === "supersedes" ? 2.3 : 1.1) : 0.12;
          })
          .linkOpacity(0.6)
          .linkCurvature((link) => link.curvature)
          .linkVisibility(linkVisible)
          .linkDirectionalArrowLength((link) => link.kind === "supersedes" ? 8 : 0)
          .linkDirectionalArrowRelPos(0.82)
          .linkDirectionalParticles(0)
          .d3AlphaDecay(0.11)
          .d3VelocityDecay(0.55)
          // Reduced motion settles during warmup, before anything is drawn.
          .warmupTicks(reducedMotion ? 90 : 18)
          .cooldownTicks(reducedMotion ? 0 : 36)
          .cooldownTime(1200)
          .onEngineStop(() => {
            if (cancelled) return;
            let tween = 0;
            if (pendingFit) {
              // Fit once per data change, after the layout settles.
              tween = firstFit || reducedMotion ? 0 : 320;
              graph?.zoomToFit(tween, FIT_PADDING);
              firstFit = false;
              cancelPendingFit();
            }
            setLoading(false);
            // Runs inside the library's frame callback; let wake's timer pause
            // once the fit tween has finished.
            wake(tween + STOP_GRACE_MS);
          })
          .onNodeHover((node) => {
            setHoveredNodeId(node?.id || null);
          })
          .onNodeClick((node) => {
            wake(1100);
            onSelectRef.current(node);
          })
          .onNodeDragEnd(() => wake(1300))
          .onBackgroundClick(() => {
            setHoveredNodeId(null);
            wake(700);
          });

        const sphere = new THREE.SphereGeometry(1, 12, 8);
        const haloMaterial = new THREE.MeshBasicMaterial({ color: colors.selection, side: THREE.BackSide, transparent: true, opacity: 0.95 });
        visualDisposables.push(sphere, haloMaterial);
        // three-forcegraph binds objects to node identity, so every graphData()
        // call creates fresh meshes and deallocates the ones it removes.
        const styleNode = (id: string, object: NodeObject) => {
          const focus = hoveredRef.current || selectedRef.current;
          const visible = !focus || id === focus || neighborsRef.current.get(focus)?.has(id);
          object.mesh.material.opacity = visible ? 1 : 0.24;
          object.mesh.scale.setScalar(object.radius * (id === focus ? 1.3 : 1));
          object.halo.visible = id === selectedRef.current;
        };
        // The library builds meshes on its own (debounced) digest after
        // graphData(), so each new mesh starts from the current selection and
        // hover instead of waiting for the next appearance update.
        graph.nodeThreeObject((node) => {
          const material = new THREE.MeshBasicMaterial({ color: node.color, transparent: true });
          const mesh = new THREE.Mesh(sphere, material);
          const halo = new THREE.Mesh(sphere, haloMaterial);
          halo.scale.setScalar(1.45);
          mesh.add(halo);
          const object = { mesh, halo, radius: nodeRadius(node.degree) };
          styleNode(node.id, object);
          objects.set(node.id, object);
          return mesh;
        });
        updateAppearanceRef.current = () => {
          graph?.linkVisibility(linkVisible);
          graph?.linkColor(linkColor);
          for (const [id, object] of objects) styleNode(id, object);
        };
        const charge = graph.d3Force("charge") as { strength?: (value: number) => void } | null;
        charge?.strength?.(-72);
        const linkForce = graph.d3Force("link") as { distance?: (value: number) => void; strength?: (value: number) => void } | null;
        linkForce?.distance?.(58);
        linkForce?.strength?.(0.42);

        applyDataRef.current = (next) => {
          if (!graph) return;
          // Reuse settled positions for memories that stay in view, so a
          // filter or search change reheats gently instead of starting over.
          const previous = new Map(graph.graphData().nodes.map((node) => [node.id, node]));
          const nodes = next.nodes.map((node) => {
            const known = previous.get(node.id);
            return known && Number.isFinite(known.x) ? { ...node, x: known.x, y: known.y, z: known.z } : { ...node };
          });
          objects.clear();
          graph.graphData({ nodes, links: next.links.map((link) => ({ ...link })) });
          updateAppearanceRef.current?.();
          cancelPendingFit();
          pendingFit = true;
          if (firstFit) {
            // The library can report an empty bbox until its force nodes are
            // initialized; fit early once so the camera never starts tiny.
            earlyFitTimer = setTimeout(() => {
              earlyFitTimer = null;
              if (!cancelled && pendingFit) graph?.zoomToFit(0, FIT_PADDING);
            }, 260);
          }
          if (document.hidden) pause();
          else wake(SETTLE_WINDOW_MS);
        };

        // Resizing only changes the viewport; it never discards the camera.
        const resize = () => {
          if (!graph || !hostRef.current) return;
          const rect = hostRef.current.getBoundingClientRect();
          graph.width(Math.max(1, rect.width || GRAPH_WIDTH));
          graph.height(Math.max(1, rect.height || GRAPH_HEIGHT));
        };
        resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
        resizeObserver?.observe(hostRef.current);
        addListener(globalThis, "resize", resize);
        resize();
        applyDataRef.current(dataRef.current);

        // Any direct camera interaction wins over a pending auto-fit.
        const rendererElement = graph.renderer().domElement;
        const wakeOnInteraction: EventListener = () => wake(pointerActiveRef.current ? 1500 : 700);
        const takeCamera: EventListener = () => {
          cancelPendingFit();
          wake(1500);
        };
        const pointerStart: EventListener = (event) => {
          pointerActiveRef.current = true;
          takeCamera(event);
        };
        const pointerEnd: EventListener = () => { pointerActiveRef.current = false; wake(900); };
        for (const type of ["pointerenter", "pointermove"]) {
          addListener(rendererElement, type, wakeOnInteraction, { passive: true });
        }
        addListener(rendererElement, "wheel", takeCamera, { passive: true });
        addListener(rendererElement, "touchstart", takeCamera, { passive: true });
        addListener(rendererElement, "pointerdown", pointerStart);
        addListener(globalThis, "pointerup", pointerEnd);
        addListener(globalThis, "pointercancel", pointerEnd);
        addListener(globalThis, "blur", pointerEnd);
        addListener(document, "visibilitychange", () => {
          if (document.hidden) pause();
          else wake(900);
        });
        const controls = graph.controls() as { enableDamping?: boolean; dampingFactor?: number };
        controls.enableDamping = !reducedMotion;
        controls.dampingFactor = 0.12;
        setLoading(false);
      } catch {
        if (!cancelled) {
          setLoading(false);
          setLoadError(WEBGL_UNAVAILABLE);
        }
      }
    };
    void mount();

    return () => {
      cancelled = true;
      pointerActiveRef.current = false;
      clearIdleTimer();
      cancelPendingFit();
      resizeObserver?.disconnect();
      for (const removeListener of listenerCleanups) removeListener();
      const renderer = graph?.renderer();
      const controls = graph?.controls() as { dispose?: () => void } | undefined;
      try { graph?.pauseAnimation(); } catch { /* already torn down */ }
      try { graph?._destructor(); } catch { /* already torn down */ }
      try { controls?.dispose?.(); } catch { /* already torn down */ }
      try { renderer?.dispose?.(); } catch { /* already torn down */ }
      try { renderer?.forceContextLoss?.(); } catch { /* already torn down */ }
      for (const resource of visualDisposables) resource.dispose();
      applyDataRef.current = null;
      updateAppearanceRef.current = null;
      wakeRef.current = null;
      graphRef.current = null;
    };
  }, [webgl]);

  useEffect(() => {
    applyDataRef.current?.(data);
  }, [data]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    updateAppearanceRef.current?.();
    wakeRef.current?.(700);
  }, [hoveredNodeId, selectedNodeId, relationshipFilter]);

  const fit = () => {
    const graph = graphRef.current;
    if (!graph) return;
    const tween = prefersReducedMotion() ? 0 : 420;
    wakeRef.current?.(tween + STOP_GRACE_MS);
    graph.zoomToFit(tween, 22);
  };

  const hovered = hoveredNodeId ? data.nodes.find((node) => node.id === hoveredNodeId) : null;
  const emptyKindNote = relationshipFilter === "supersedes" && !totalCorrections
    ? "No stored corrections in this view."
    : relationshipFilter === "similarity" && !totalSimilarity
      ? "No similarity links in this view."
      : null;
  const stageClassName = graphReady ? "titan-graph-stage titan-graph3d-stage" : "titan-graph-stage titan-graph3d-stage titan-graph3d-stage--unavailable";

  return (
    <div className="titan-graph-shell">
      <div className="titan-graph-toolbar">
        <div className="titan-graph-controls" aria-label="Graph controls">
          <label className="titan-graph-filter">Relationships <select aria-label="Relationship visibility" value={relationshipFilter} disabled={!graphReady} onChange={(event) => {
            const value = event.target.value;
            if (value === "all" || value === "supersedes" || value === "similarity") onRelationshipFilterChange(value);
          }}>
            <option value="all">All</option>
            <option value="supersedes" disabled={!totalCorrections && relationshipFilter !== "supersedes"}>Stored corrections</option>
            <option value="similarity" disabled={!totalSimilarity && relationshipFilter !== "similarity"}>Similarity</option>
          </select></label>
          <button type="button" className="titan-graph-fit" onClick={fit} disabled={!graphReady}>Fit</button>
        </div>
      </div>
      <div className={stageClassName}>
        <div ref={hostRef} className="titan-graph3d-host" role="img" aria-label={`3D memory relationship graph with ${data.nodes.length} memories and ${visibleEdges.length} connections`} />
        {loading && !loadError ? <div className="titan-graph-loading" role="status">Loading graph…</div> : null}
        {loadError ? <div className="titan-graph-load-error" role="status">{loadError}</div> : null}
        {emptyKindNote && graphReady ? <div className="titan-graph-note" role="status">{emptyKindNote}</div> : null}
        {hovered ? (
          <div className="titan-graph-hover" role="tooltip">
            <span><bdi>{titleCase(shortText(hovered.type || "memory", 40))}</bdi> · <bdi>{hovered.sourceAgent || LOCAL_AGENT}</bdi></span>
            <strong dir="auto">{shortText(hovered.text) || "(empty memory)"}</strong>
          </div>
        ) : null}
        <p className="titan-graph-accessible-note">Use the memories list for keyboard navigation and full text.</p>
      </div>
      <div className="titan-graph-footer">
        <span>{data.nodes.length} {data.nodes.length === 1 ? "memory" : "memories"}</span>
        {/* A key only appears for a link kind the snapshot actually has. */}
        {totalCorrections ? <span><i className="titan-graph-line titan-graph-line--correction" aria-hidden="true" /> {correctionCount} {correctionCount === 1 ? "correction" : "corrections"} (newer → earlier)</span> : null}
        {totalSimilarity ? <span><i className="titan-graph-line titan-graph-line--similarity" aria-hidden="true" /> {similarityCount} similarity {similarityCount === 1 ? "link" : "links"}</span> : null}
        {graphReady ? <span>Drag to orbit · scroll to zoom</span> : null}
      </div>
      {legendAgents.length ? (
        <div className="titan-agent-legend" role="group" aria-label="Agents">
          {legendAgents.map((agent) => {
            const active = agentFilter === agent;
            return (
              <button
                key={agent}
                type="button"
                className={active ? "titan-agent-chip titan-agent-chip--active" : "titan-agent-chip"}
                aria-pressed={active}
                title={active ? "Show all agents" : `Show only ${agent}`}
                disabled={!onAgentFilterChange}
                onClick={() => onAgentFilterChange?.(active ? null : agent)}
              >
                <i className="titan-agent-dot" style={{ background: palette.get(agent) }} aria-hidden="true" />
                <bdi>{agent}</bdi>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
