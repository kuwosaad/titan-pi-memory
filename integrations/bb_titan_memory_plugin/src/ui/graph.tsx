import { useEffect, useMemo, useRef, useState } from "react";
import type { ConfigOptions, ForceGraph3DInstance } from "3d-force-graph";
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

interface GraphProps {
  snapshot: ExplorerSnapshot;
  selectedNodeId: string | null;
  onSelect: (memory: MemorySummary) => void;
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
  kind: "similarity";
  color: string;
};

type ForceGraph = ForceGraph3DInstance<GraphNode, GraphLink>;
type ForceGraphFactory = new (element: HTMLElement, options?: ConfigOptions) => ForceGraph;

const NODE_COLORS = ["#ff9cb5", "#c9b0ff", "#98ddcf", "#f5cc98", "#a6cbff"];
const DIM_NODE = "#3b323d";
const EDGE_COLOR = "#d08c9c";
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
  if (!focus) return "#bb8baf";
  return source === focus || target === focus ? "#ffd1dc" : "#302735";
}

function graphData(snapshot: ExplorerSnapshot, points: readonly GraphPoint[]): { nodes: GraphNode[]; links: GraphLink[] } {
  const byId = new Map(points.map((point) => [point.nodeId, point]));
  const nodes = points.map((point) => ({
    ...point,
    id: point.nodeId,
    // The original visual used small spheres, not score-encoded bubbles.
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
      kind: "similarity" as const,
      color: EDGE_COLOR,
    }];
  });
  return { nodes, links };
}

export function MemoryGraph({ snapshot, selectedNodeId, onSelect }: GraphProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph | null>(null);
  const updateAppearanceRef = useRef<(() => void) | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pointerActiveRef = useRef(false);
  const disposedRef = useRef(false);
  const hoveredRef = useRef<string | null>(null);
  const selectedRef = useRef(selectedNodeId);
  const onSelectRef = useRef(onSelect);
  const neighborsRef = useRef<Map<string, Set<string>>>(new Map());
  const data = useMemo(() => graphData(snapshot, layoutGraph(snapshot.nodes, snapshot.edges)), [snapshot.edges, snapshot.nodes]);
  const neighbors = useMemo(() => neighborMap(snapshot.edges), [snapshot.edges]);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  hoveredRef.current = hoveredNodeId;
  selectedRef.current = selectedNodeId;
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
            return connectionColor(endpointId(link.source), endpointId(link.target), focus);
          })
          .linkWidth((link) => {
            const focus = hoveredRef.current || selectedRef.current;
            return !focus ? 0.45 + link.weight * 0.35 : isConnected(link, focus) ? 1.1 : 0.12;
          })
          .linkOpacity(0.55)
          .linkDirectionalParticles((link) => {
            const focus = hoveredRef.current || selectedRef.current;
            return focus && isConnected(link, focus) ? 1 : 0;
          })
          .linkDirectionalParticleWidth(0.65)
          .linkDirectionalParticleSpeed(0.006)
          .linkDirectionalParticleColor(() => "#f0b0bf")
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
        // Shared small glow texture: depth and luminosity without a bloom pass.
        const glowCanvas = document.createElement("canvas");
        glowCanvas.width = glowCanvas.height = 64;
        const glowContext = glowCanvas.getContext("2d");
        if (!glowContext) throw new Error("Canvas textures are unavailable");
        const gradient = glowContext.createRadialGradient(32, 32, 0, 32, 32, 32);
        gradient.addColorStop(0, "rgba(255,255,255,0.85)");
        gradient.addColorStop(0.18, "rgba(255,255,255,0.4)");
        gradient.addColorStop(0.48, "rgba(255,255,255,0.09)");
        gradient.addColorStop(1, "rgba(255,255,255,0)");
        glowContext.fillStyle = gradient;
        glowContext.fillRect(0, 0, 64, 64);
        const glowTexture = new THREE.CanvasTexture(glowCanvas);
        const sphere = new THREE.SphereGeometry(1, 12, 8);
        visualDisposables.push(glowTexture, sphere);
        const objects = new Map<string, {
          group: InstanceType<typeof THREE.Group>;
          core: InstanceType<typeof THREE.MeshBasicMaterial>;
          halo: InstanceType<typeof THREE.SpriteMaterial>;
          radius: number;
        }>();
        graph.nodeThreeObject(node => {
          const existing = objects.get(node.id);
          if (existing) return existing.group;
          const group = new THREE.Group();
          const radius = 1.7 + Math.min(2.5, Math.sqrt(node.degree) * 0.65);
          const core = new THREE.MeshBasicMaterial({ color: node.color, transparent: true });
          const mesh = new THREE.Mesh(sphere, core);
          mesh.scale.setScalar(radius);
          group.add(mesh);
          const halo = new THREE.SpriteMaterial({ map: glowTexture, color: node.color,
            transparent: true, opacity: 0.7, depthWrite: false, blending: THREE.AdditiveBlending });
          const glow = new THREE.Sprite(halo);
          glow.scale.setScalar(radius * 7);
          group.add(glow);
          visualDisposables.push(core, halo);
          objects.set(node.id, { group, core, halo, radius });
          return group;
        });
        updateAppearanceRef.current = () => {
          graph?.linkColor(link => {
            const focus = hoveredRef.current || selectedRef.current;
            return connectionColor(endpointId(link.source), endpointId(link.target), focus);
          });
          const focus = hoveredRef.current || selectedRef.current;
          for (const [id, object] of objects) {
            const visible = !focus || id === focus || neighborsRef.current.get(focus)?.has(id);
            object.core.opacity = visible ? 1 : 0.16;
            object.halo.opacity = visible ? (id === focus ? 1 : 0.65) : 0.03;
            object.group.scale.setScalar(id === focus ? 1.4 : 1);
          }
        };
        const ambient = new THREE.AmbientLight("#e6d5da", 2.2);
        const key = new THREE.DirectionalLight("#ffdce4", 2.4);
        key.position.set(1, 1, 2);
        const fill = new THREE.DirectionalLight("#9eafc8", 1.2);
        fill.position.set(-2, -1, -1);
        graph.lights([ambient, key, fill]);
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
  }, [hoveredNodeId, selectedNodeId]);

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
          <button type="button" className="titan-graph-fit" onClick={fit}>Fit</button>
        </div>
      </div>
      <div className="titan-graph-stage titan-graph3d-stage">
        <div ref={hostRef} className="titan-graph3d-host" role="img" aria-label={`3D memory relationship graph with ${data.nodes.length} memories and ${data.links.length} connections`} />
        {loading ? <div className="titan-graph-loading">Settling the memory field…</div> : null}
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
        <span>{data.nodes.length} memories · {data.links.length} connections</span>
        <span>orbit · pan · scroll to zoom</span>
      </div>
    </div>
  );
}
