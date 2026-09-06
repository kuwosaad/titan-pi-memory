import type { ExplorerSnapshot, MemorySummary } from "../contract.js";

export const GRAPH_WIDTH = 760;
export const GRAPH_HEIGHT = 440;
export const GRAPH_MAX_NODES = 150;
export const GRAPH_MAX_EDGES = 450;
export const MIN_ZOOM = 0.58;
export const MAX_ZOOM = 2.6;
const TAU = Math.PI * 2;

export interface GraphPoint extends MemorySummary {
  x: number;
  y: number;
  z: number;
  radius: number;
  degree: number;
}

export interface GraphTransform {
  yaw: number;
  pitch: number;
  zoom: number;
  panX: number;
  panY: number;
}

export interface GraphBounds {
  width: number;
  height: number;
}

export interface GraphPointer {
  x: number;
  y: number;
}

export interface ProjectedPoint {
  x: number;
  y: number;
  depth: number;
  scale: number;
}

function hash(value: string): number {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return result >>> 0;
}

function unit(value: number): number {
  return (value >>> 0) / 4294967296;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function boundedEdges(
  memories: readonly MemorySummary[],
  edges: readonly ExplorerSnapshot["edges"][number][],
): ExplorerSnapshot["edges"] {
  const ids = new Set(memories.map((memory) => memory.nodeId));
  return edges
    .filter(
      (edge) =>
        ids.has(edge.source) &&
        ids.has(edge.target) &&
        edge.source !== edge.target &&
        Number.isFinite(Number(edge.weight)),
    )
    .sort((left, right) => {
      const weightDelta = Number(right.weight) - Number(left.weight);
      return weightDelta || JSON.stringify([left.source, left.target]).localeCompare(JSON.stringify([right.source, right.target]));
    })
    .slice(0, GRAPH_MAX_EDGES);
}

/**
 * Deterministic bounded 3D layout. Stored similarity edges act as springs;
 * the short fixed pass settles an inspectable point cloud without a live
 * simulation or a background animation loop. The final cloud is normalized
 * into a compact unit volume for perspective projection.
 */
export function layoutGraph(
  memories: readonly MemorySummary[],
  edges: readonly ExplorerSnapshot["edges"][number][],
  width = GRAPH_WIDTH,
  height = GRAPH_HEIGHT,
): GraphPoint[] {
  const nodes = memories.slice(0, GRAPH_MAX_NODES);
  const bounded = boundedEdges(nodes, edges);
  const indexById = new Map(nodes.map((node, index) => [node.nodeId, index]));
  const degree = new Array<number>(nodes.length).fill(0);
  for (const edge of bounded) {
    const source = indexById.get(edge.source);
    const target = indexById.get(edge.target);
    if (source === undefined || target === undefined) continue;
    degree[source] += 1;
    degree[target] += 1;
  }

  const points = nodes.map((node) => {
    const first = hash(node.nodeId);
    const second = hash(`${node.nodeId}:y`);
    const third = hash(`${node.nodeId}:z`);
    return {
      x: (unit(first) * 2 - 1) * 1.05,
      y: (unit(second) * 2 - 1) * 0.78,
      z: (unit(third) * 2 - 1) * 0.9,
    };
  });
  const springs = bounded.flatMap((edge) => {
    const source = indexById.get(edge.source);
    const target = indexById.get(edge.target);
    return source === undefined || target === undefined
      ? []
      : [{ source, target, weight: clamp(Number(edge.weight), 0, 1) }];
  });

  // Thirty-six ticks is enough to reveal local neighborhoods while remaining
  // cheap enough for the panel's first render. No tick is scheduled later.
  for (let iteration = 0; iteration < 36; iteration += 1) {
    const forces = points.map(() => ({ x: 0, y: 0, z: 0 }));
    for (let left = 0; left < points.length; left += 1) {
      for (let right = left + 1; right < points.length; right += 1) {
        let dx = points[right].x - points[left].x;
        let dy = points[right].y - points[left].y;
        let dz = points[right].z - points[left].z;
        let distance = Math.hypot(dx, dy, dz);
        if (distance < 0.001) {
          const nudge = ((hash(`${nodes[left].nodeId}:${nodes[right].nodeId}`) % 17) + 1) / 100;
          dx = nudge;
          dy = nudge * 0.7;
          dz = nudge * 0.4;
          distance = Math.hypot(dx, dy, dz);
        }
        const repulsion = 0.012 / (distance * distance + 0.045);
        const nx = dx / distance;
        const ny = dy / distance;
        const nz = dz / distance;
        forces[left].x -= nx * repulsion;
        forces[left].y -= ny * repulsion;
        forces[left].z -= nz * repulsion;
        forces[right].x += nx * repulsion;
        forces[right].y += ny * repulsion;
        forces[right].z += nz * repulsion;
      }
    }

    for (const spring of springs) {
      const source = points[spring.source];
      const target = points[spring.target];
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const dz = target.z - source.z;
      const distance = Math.max(0.001, Math.hypot(dx, dy, dz));
      const desired = 0.28 + (1 - spring.weight) * 0.2;
      const attraction = (distance - desired) * (0.035 + spring.weight * 0.045);
      const nx = dx / distance;
      const ny = dy / distance;
      const nz = dz / distance;
      forces[spring.source].x += nx * attraction;
      forces[spring.source].y += ny * attraction;
      forces[spring.source].z += nz * attraction;
      forces[spring.target].x -= nx * attraction;
      forces[spring.target].y -= ny * attraction;
      forces[spring.target].z -= nz * attraction;
    }

    for (let index = 0; index < points.length; index += 1) {
      const point = points[index];
      const force = forces[index];
      force.x -= point.x * 0.006;
      force.y -= point.y * 0.006;
      force.z -= point.z * 0.006;
      point.x += force.x * 0.42;
      point.y += force.y * 0.42;
      point.z += force.z * 0.42;
    }
  }

  const center = points.reduce((sum, point) => ({
    x: sum.x + point.x / Math.max(1, points.length),
    y: sum.y + point.y / Math.max(1, points.length),
    z: sum.z + point.z / Math.max(1, points.length),
  }), { x: 0, y: 0, z: 0 });
  const centered = points.map((point) => ({
    x: point.x - center.x,
    y: point.y - center.y,
    z: point.z - center.z,
  }));
  const extent = Math.max(0.001, ...centered.map((point) => Math.max(Math.abs(point.x), Math.abs(point.y), Math.abs(point.z))));
  const scale = 0.9 / extent;
  const centerX = width / 2;
  const centerY = height / 2;

  return nodes.map((node, index) => ({
    ...node,
    x: centerX + centered[index].x * scale * width * 0.42,
    y: centerY + centered[index].y * scale * height * 0.42,
    z: clamp(centered[index].z * scale, -0.9, 0.9),
    degree: degree[index],
    radius: Math.min(7, 2.7 + Math.sqrt(degree[index] + 1) * 0.9),
  }));
}

export function neighborMap(
  edges: readonly ExplorerSnapshot["edges"][number][],
): Map<string, Set<string>> {
  const neighbors = new Map<string, Set<string>>();
  for (const edge of edges.slice(0, GRAPH_MAX_EDGES)) {
    if (!neighbors.has(edge.source)) neighbors.set(edge.source, new Set());
    if (!neighbors.has(edge.target)) neighbors.set(edge.target, new Set());
    neighbors.get(edge.source)?.add(edge.target);
    neighbors.get(edge.target)?.add(edge.source);
  }
  return neighbors;
}

export function resetTransform(): GraphTransform {
  return { yaw: 0.34, pitch: -0.18, zoom: 1, panX: 0, panY: 0 };
}

export function clampTransform(transform: GraphTransform, bounds: GraphBounds): GraphTransform {
  const scale = clamp(transform.zoom, MIN_ZOOM, MAX_ZOOM);
  const panRangeX = bounds.width * (0.38 + Math.max(0, scale - 1) * 0.55);
  const panRangeY = bounds.height * (0.38 + Math.max(0, scale - 1) * 0.55);
  return {
    yaw: ((transform.yaw + Math.PI) % TAU + TAU) % TAU - Math.PI,
    pitch: clamp(transform.pitch, -1.2, 1.2),
    zoom: scale,
    panX: clamp(transform.panX, -panRangeX, panRangeX),
    panY: clamp(transform.panY, -panRangeY, panRangeY),
  };
}

export function zoomAt(
  transform: GraphTransform,
  pointer: GraphPointer,
  nextZoom: number,
  bounds: GraphBounds = { width: GRAPH_WIDTH, height: GRAPH_HEIGHT },
): GraphTransform {
  const zoom = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
  const worldX = (pointer.x - bounds.width / 2 - transform.panX) / transform.zoom;
  const worldY = (pointer.y - bounds.height / 2 - transform.panY) / transform.zoom;
  return clampTransform({
    ...transform,
    zoom,
    panX: pointer.x - bounds.width / 2 - worldX * zoom,
    panY: pointer.y - bounds.height / 2 - worldY * zoom,
  }, bounds);
}

export function orbitBy(transform: GraphTransform, deltaX: number, deltaY: number, bounds: GraphBounds): GraphTransform {
  return clampTransform({
    ...transform,
    yaw: transform.yaw + deltaX * 0.008,
    pitch: transform.pitch + deltaY * 0.008,
  }, bounds);
}

export function panBy(transform: GraphTransform, deltaX: number, deltaY: number, bounds: GraphBounds): GraphTransform {
  return clampTransform({ ...transform, panX: transform.panX + deltaX, panY: transform.panY + deltaY }, bounds);
}

export function projectPoint(point: GraphPoint, transform: GraphTransform, bounds: GraphBounds): ProjectedPoint {
  const x = (point.x / GRAPH_WIDTH - 0.5) * 2;
  const y = (point.y / GRAPH_HEIGHT - 0.5) * 2;
  const cosYaw = Math.cos(transform.yaw);
  const sinYaw = Math.sin(transform.yaw);
  const yawX = x * cosYaw + point.z * sinYaw;
  const yawZ = -x * sinYaw + point.z * cosYaw;
  const cosPitch = Math.cos(transform.pitch);
  const sinPitch = Math.sin(transform.pitch);
  const rotatedY = y * cosPitch - yawZ * sinPitch;
  const depth = y * sinPitch + yawZ * cosPitch;
  const perspective = 3.4 / (3.4 - depth);
  const scale = Math.min(bounds.width, bounds.height) * 0.32 * transform.zoom * perspective;
  return {
    x: bounds.width / 2 + transform.panX + yawX * scale,
    y: bounds.height / 2 + transform.panY + rotatedY * scale,
    depth,
    scale: perspective * transform.zoom,
  };
}

export function movePoint(
  point: GraphPoint,
  position: GraphPointer,
  bounds: GraphBounds = { width: GRAPH_WIDTH, height: GRAPH_HEIGHT },
): GraphPoint {
  const inset = point.radius + 3;
  return {
    ...point,
    x: clamp(position.x, inset, Math.max(inset, bounds.width - inset)),
    y: clamp(position.y, inset, Math.max(inset, bounds.height - inset)),
  };
}
