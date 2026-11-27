import { useEffect, useMemo, useRef, useState } from "react";
import type { ConfigOptions, ForceGraph3DInstance } from "3d-force-graph";
import type { Mesh, MeshBasicMaterial, SphereGeometry } from "three";
import type { ExplorerSnapshot, MemorySummary } from "../contract.js";
import { shortText } from "./model.js";
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
};

type ForceGraph = ForceGraph3DInstance<GraphNode, GraphLink>;
type ForceGraphFactory = new (element: HTMLElement, options?: ConfigOptions) => ForceGraph;

const NODE_COLORS = ["#c7a5a4", "#acaecb", "#aec7bf", "#d1bc9f", "#a9bdcf"];
const DIM_NODE = "#505761";
const CORRECTION_COLOR = "#83d6e8";
const IDLE_WINDOW_MS = 900;

function colorForSource(sourceAgent: string | null): string {
  const value = sourceAgent || "local";
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) | 0;
  return NODE_COLORS[Math.abs(hash) % NODE_COLORS.length];
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

export function connectionColor(source: string, target: string, focus: string | null): string {
  if (!focus) return "#818b99";
  return source === focus || target === focus ? "#c3ccd6" : "#303640";
}

function graphData(snapshot: ExplorerSnapshot, points: readonly GraphPoint[]): { nodes: GraphNode[]; links: GraphLink[] } {
  const byId = new Map(points.map((point) => [point.nodeId, point]));
  const nodes = points.map((point) => ({
    ...point,
    id: point.nodeId,
    val: 1.2 + Math.min(1.7, Math.sqrt(point.degree + 1) * 0.28),
    color: colorForSource(point.sourceAgent),
    x: (point.x / GRAPH_WIDTH - 0.5) * 260,
    y: (point.y / GRAPH_HEIGHT - 0.5) * 180,
    // Keep real depth for orbit, but do not let the z extent dominate fit-to-view.
    z: point.z * 90,
  }));
  const links = snapshot.edges.flatMap((edge) => {
    if (!byId.has(edge.source) || !byId.has(edge.target)) return [];
    return [{
      source: edge.source,
      target: edge.target,
      weight: Number(edge.weight),
      kind: edge.kind,
    }];
  });
  return { nodes, links };
}

export function MemoryGraph({ snapshot, selectedNodeId, onSelect, relationshipFilter, onRelationshipFilterChange }: GraphProps) {
  const visibleEdges = useMemo(() => snapshot.edges.filter((edge) => relationshipFilter === "all" || edge.kind === relationshipFilter), [snapshot.edges, relationshipFilter]);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph | null>(null);
  const updateAppearanceRef = useRef<(() => void) | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pointerActiveRef = useRef(false);
  const disposedRef = useRef(false);
  const hoveredRef = useRef<string | null>(null);
  const selectedRef = useRef(selectedNodeId);
  const relationshipFilterRef = useRef(relationshipFilter);
  const onSelectRef = useRef(onSelect);
  const neighborsRef = useRef<Map<string, Set<string>>>(new Map());
  const data = useMemo(() => graphData(snapshot, layoutGraph(snapshot.nodes, snapshot.edges)), [snapshot]);
  const neighbors = useMemo(() => neighborMap(visibleEdges), [visibleEdges]);
  const correctionCount = visibleEdges.filter((edge) => edge.kind === "supersedes").length;
  const similarityCount = visibleEdges.length - correctionCount;
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  hoveredRef.current = hoveredNodeId;
  selectedRef.current = selectedNodeId;
  relationshipFilterRef.current = relationshipFilter;
  onSelectRef.current = onSelect;
  neighborsRef.current = neighbors;

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    let cancelled = false;
    disposedRef.current = false;
    let graph: ForceGraph | null = null;
    let rendererElement: HTMLElement | null = null;
    const listenerCleanups: Array<() => void> = [];
    let resizeObserver: ResizeObserver | null = null;
    let initialFitTimer: ReturnType<typeof setTimeout> | null = null;
    let dataLoaded = false;
    let fitted = false;
    const visualDisposables: Array<{ dispose: () => void }> = [];

    const clearIdleTimer = () => {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    };
    const pause = () => {
      clearIdleTimer();
      graph?.pauseAnimation();
    };
    const wake = (duration = IDLE_WINDOW_MS) => {
      if (disposedRef.current || !graph) return;
      graph.resumeAnimation();
      clearIdleTimer();
      idleTimerRef.current = setTimeout(() => {
        if (!pointerActiveRef.current) pause();
      }, duration);
    };
    const addListener = (target: EventTarget, type: string, listener: EventListener, options?: AddEventListenerOptions) => {
      target.addEventListener(type, listener, options);
      listenerCleanups.push(() => target.removeEventListener(type, listener, options));
    };

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
          .nodeVal((node) => {
            const focus = hoveredRef.current || selectedRef.current;
            return node.id === focus ? node.val * 1.35 : node.val;
          })
          .nodeColor((node) => {
            const focus = hoveredRef.current || selectedRef.current;
            if (!focus) return node.color;
            return node.id === focus || neighborsRef.current.get(focus)?.has(node.id) ? node.color : DIM_NODE;
          })
          .nodeOpacity(0.82)
          .nodeResolution(7)
          .linkColor((link) => {
            const focus = hoveredRef.current || selectedRef.current;
            return link.kind === "supersedes" ? (!focus || isConnected(link, focus) ? CORRECTION_COLOR : "#36454a") : connectionColor(endpointId(link.source), endpointId(link.target), focus);
          })
          .linkWidth((link) => {
            const focus = hoveredRef.current || selectedRef.current;
            return !focus ? (link.kind === "supersedes" ? 1.8 : 0.45 + link.weight * 0.35) : isConnected(link, focus) ? (link.kind === "supersedes" ? 2.3 : 1.1) : 0.12;
          })
          .linkOpacity(0.55)
          .linkVisibility((link) => relationshipFilterRef.current === "all" || link.kind === relationshipFilterRef.current)
          .linkDirectionalArrowLength((link) => link.kind === "supersedes" ? 8 : 0)
          .linkDirectionalArrowRelPos(0.82)
          .linkDirectionalParticles(0)
          .d3AlphaDecay(0.11)
          .d3VelocityDecay(0.55)
          .warmupTicks(18)
          .cooldownTicks(36)
          .cooldownTime(1200)
          .onEngineStop(() => {
            if (cancelled) return;
            if (dataLoaded && !fitted) {
              fitted = true;
              graph?.zoomToFit(0, 24);
            }
            setLoading(false);
            pause();
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

        if (cancelled || !graph || !hostRef.current) return;
        const sphere = new THREE.SphereGeometry(1, 12, 8);
        visualDisposables.push(sphere);
        const objects = new Map<string, {
          mesh: Mesh<SphereGeometry, MeshBasicMaterial>;
          radius: number;
        }>();
        graph.nodeThreeObject(node => {
          const existing = objects.get(node.id);
          if (existing) return existing.mesh;
          const radius = 2.4 + Math.min(2.5, Math.sqrt(node.degree) * 0.65);
          const core = new THREE.MeshBasicMaterial({ color: node.color, transparent: true });
          const mesh = new THREE.Mesh(sphere, core);
          mesh.scale.setScalar(radius);
          visualDisposables.push(core);
          objects.set(node.id, { mesh, radius });
          return mesh;
        });
        updateAppearanceRef.current = () => {
          graph?.linkVisibility((link) => relationshipFilterRef.current === "all" || link.kind === relationshipFilterRef.current);
          graph?.linkColor(link => {
            const focus = hoveredRef.current || selectedRef.current;
            return link.kind === "supersedes" ? (!focus || isConnected(link, focus) ? CORRECTION_COLOR : "#36454a") : connectionColor(endpointId(link.source), endpointId(link.target), focus);
          });
          const focus = hoveredRef.current || selectedRef.current;
          for (const [id, object] of objects) {
            const visible = !focus || id === focus || neighborsRef.current.get(focus)?.has(id);
            object.mesh.material.opacity = visible ? 1 : 0.24;
            object.mesh.scale.setScalar(object.radius * (id === focus ? 1.3 : 1));
          }
        };
        const charge = graph.d3Force("charge") as { strength?: (value: number) => void } | null;
        charge?.strength?.(-72);
        const linkForce = graph.d3Force("link") as { distance?: (value: number) => void; strength?: (value: number) => void } | null;
        linkForce?.distance?.(58);
        linkForce?.strength?.(0.42);
        graph.graphData(data);
        dataLoaded = true;
        // Fit immediately and once after the first settled frame. The library
        // can expose an empty bbox during graphData() before its force nodes
        // are initialized, so the finite retry avoids a tiny default camera.
        graph.zoomToFit(0, 24);
        fitted = true;
        initialFitTimer = setTimeout(() => {
          if (!cancelled && graph) {
            graph.zoomToFit(0, 24);
            fitted = true;
          }
        }, 260);
        rendererElement = graph.renderer().domElement;
        const resize = () => {
          if (!graph || !hostRef.current) return;
          const rect = hostRef.current.getBoundingClientRect();
          graph.width(Math.max(1, rect.width || GRAPH_WIDTH));
          graph.height(Math.max(1, rect.height || GRAPH_HEIGHT));
          if (dataLoaded) {
            graph.zoomToFit(0, 24);
            fitted = true;
          }
        };
        resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
        resizeObserver?.observe(hostRef.current);
        addListener(globalThis, "resize", resize);
        resize();

        const wakeOnInteraction: EventListener = () => wake(pointerActiveRef.current ? 1500 : 700);
        const pointerStart: EventListener = () => { pointerActiveRef.current = true; wake(1500); };
        const pointerEnd: EventListener = () => { pointerActiveRef.current = false; wake(900); };
        for (const type of ["pointerenter", "pointermove", "wheel", "touchstart"]) {
          addListener(rendererElement, type, wakeOnInteraction, { passive: type !== "wheel" });
        }
        addListener(rendererElement, "pointerdown", pointerStart);
        addListener(globalThis, "pointerup", pointerEnd);
        addListener(globalThis, "pointercancel", pointerEnd);
        addListener(globalThis, "blur", pointerEnd);
        const visibilityChange: EventListener = () => {
          if (document.hidden) pause();
          else wake(900);
        };
        addListener(document, "visibilitychange", visibilityChange);
        const controls = graph.controls() as { enableDamping?: boolean; dampingFactor?: number };
        controls.enableDamping = true;
        controls.dampingFactor = 0.12;
        setLoading(false);
        if (document.hidden) pause();
        else wake(1400);
      } catch (error) {
        if (!cancelled) {
          setLoading(false);
          setLoadError(error instanceof Error ? error.message : "3D graph could not load");
        }
      }
    };
    void mount();

    return () => {
      cancelled = true;
      disposedRef.current = true;
      pointerActiveRef.current = false;
      clearIdleTimer();
      if (initialFitTimer) clearTimeout(initialFitTimer);
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
      updateAppearanceRef.current = null;
      graphRef.current = null;
    };
  }, [data]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    updateAppearanceRef.current?.();
    graph.resumeAnimation();
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => {
      if (!pointerActiveRef.current) graph.pauseAnimation();
    }, 700);
  }, [hoveredNodeId, selectedNodeId, relationshipFilter]);

  const fit = () => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.resumeAnimation();
    graph.zoomToFit(420, 22);
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => graph.pauseAnimation(), 900);
  };

  return (
    <div className="titan-graph-shell">
      <div className="titan-graph-toolbar">
        <div className="titan-graph-controls" aria-label="Graph controls">
          <label className="titan-graph-filter">Relationships <select aria-label="Relationship visibility" value={relationshipFilter} onChange={(event) => {
            const value = event.target.value;
            if (value === "all" || value === "supersedes" || value === "similarity") onRelationshipFilterChange(value);
          }}><option value="all">All</option><option value="supersedes">Stored corrections</option><option value="similarity">Similarity</option></select></label>
          <button type="button" className="titan-graph-fit" onClick={fit}>Fit</button>
        </div>
      </div>
      <div className="titan-graph-stage titan-graph3d-stage">
        <div ref={hostRef} className="titan-graph3d-host" role="img" aria-label={`3D memory relationship graph with ${data.nodes.length} memories and ${visibleEdges.length} connections`} />
        {loading ? <div className="titan-graph-loading">Loading graph…</div> : null}
        {loadError ? <div className="titan-graph-load-error" role="alert">3D graph unavailable: {loadError}</div> : null}
        {hoveredNodeId ? (() => {
          const point = data.nodes.find((node) => node.id === hoveredNodeId);
          if (!point) return null;
          return (
            <div className="titan-graph-hover" role="tooltip">
              <span>{point.type || "memory"} · {point.sourceAgent || "local"}</span>
              <strong>{shortText(point.text)}</strong>
            </div>
          );
        })() : null}
        <p className="titan-graph-accessible-note">Use the memories list for keyboard navigation and full text.</p>
      </div>
      <div className="titan-graph-footer">
        <span>{data.nodes.length} {data.nodes.length === 1 ? "memory" : "memories"}</span>
        <span><i className="titan-graph-line titan-graph-line--correction" aria-hidden="true" /> {correctionCount} {correctionCount === 1 ? "correction" : "corrections"} (newer → earlier)</span>
        <span><i className="titan-graph-line titan-graph-line--similarity" aria-hidden="true" /> {similarityCount} similarity {similarityCount === 1 ? "link" : "links"}</span>
        <span>Drag to orbit · scroll to zoom</span>
      </div>
    </div>
  );
}
