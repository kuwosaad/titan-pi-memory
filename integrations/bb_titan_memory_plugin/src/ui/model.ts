import type {
  ExplorerSnapshot,
  ExplorerSnapshotInput,
  MemorySummary,
} from "../contract.js";

export type ExplorerFilters = ExplorerSnapshotInput["filters"];
export type { ExplorerSnapshot, MemorySummary } from "../contract.js";

export const MAX_GRAPH_NODES = 150;
export const MAX_GRAPH_EDGES = 450;
export const MAX_SEARCH_ITEMS = 50;
export const MAX_NEIGHBORS = 8;
export const MAX_PREVIEW_LENGTH = 240;
export const MAX_WARNINGS = 64;

export interface RequestGate {
  activate(request: string): string | null;
  isCurrent(request: string): boolean;
  close(): string | null;
}

export function createRequestGate(): RequestGate {
  let open = true;
  let active: string | null = null;
  return {
    activate(request) {
      if (!open) return null;
      const previous = active;
      active = request;
      return previous;
    },
    isCurrent(request) {
      return open && active === request;
    },
    close() {
      open = false;
      const previous = active;
      active = null;
      return previous;
    },
  };
}

export interface GraphPoint extends MemorySummary {
  x: number;
  y: number;
  radius: number;
  degree: number;
}

export function mergeWarnings(...groups: Array<readonly string[] | undefined>): string[] {
  return Array.from(new Set(groups.flatMap((group) => group || []))).slice(0, MAX_WARNINGS);
}

export function shortText(value: string, max = MAX_PREVIEW_LENGTH): string {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

export function requestId(prefix = "explorer"): string {
  const random = globalThis.crypto?.randomUUID?.();
  if (random) return `${prefix}-${random}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function cleanFilters(filters: ExplorerFilters): ExplorerFilters {
  const cleaned: ExplorerFilters = {};
  for (const key of ["agent", "type", "dateFrom", "dateTo", "query"] as const) {
    const value = filters[key]?.trim();
    if (value) cleaned[key] = value;
  }
  return cleaned;
}

export function boundSnapshot(snapshot: ExplorerSnapshot): ExplorerSnapshot {
  const nodes = Array.isArray(snapshot.nodes)
    ? snapshot.nodes.slice(0, MAX_GRAPH_NODES)
    : [];
  const nodeIds = new Set(nodes.map((node) => node.nodeId));
  const edges = (Array.isArray(snapshot.edges) ? snapshot.edges : [])
    .filter(
      (edge) =>
        nodeIds.has(edge.source) &&
        nodeIds.has(edge.target) &&
        edge.source !== edge.target &&
        Number.isFinite(Number(edge.weight)),
    )
    .sort((left, right) => Number(right.weight) - Number(left.weight))
    .slice(0, MAX_GRAPH_EDGES);

  const wasBound = nodes.length < (snapshot.nodes?.length ?? 0) || edges.length < (snapshot.edges?.length ?? 0);
  return {
    ...snapshot,
    nodes,
    edges,
    partial: Boolean(snapshot.partial || wasBound),
    warnings: Array.isArray(snapshot.warnings) ? snapshot.warnings : [],
  };
}

export function uniqueValues(
  memories: readonly MemorySummary[],
  field: "sourceAgent" | "type",
): string[] {
  return Array.from(
    new Set(
      memories
        .map((memory) => memory[field])
        .filter((value): value is string => Boolean(value?.trim())),
    ),
  ).sort((left, right) => left.localeCompare(right));
}

export function connectedMemories(
  nodeId: string,
  snapshot: ExplorerSnapshot | null,
  memories: readonly MemorySummary[],
): Array<{ memory: MemorySummary; weight: number }> {
  if (!snapshot) return [];
  const byId = new Map(memories.map((memory) => [memory.nodeId, memory]));
  const result: Array<{ memory: MemorySummary; weight: number }> = [];
  for (const edge of snapshot.edges) {
    const neighborId = edge.source === nodeId ? edge.target : edge.target === nodeId ? edge.source : null;
    if (!neighborId) continue;
    const memory = byId.get(neighborId);
    if (memory) result.push({ memory, weight: Number(edge.weight) });
  }
  return result.sort((left, right) => right.weight - left.weight).slice(0, MAX_NEIGHBORS);
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/**
 * A deterministic, bounded layout. It intentionally does not run a force
 * simulation: the side panel gets a stable picture with no background work.
 */
export function layoutGraph(
  memories: readonly MemorySummary[],
  edges: ExplorerSnapshot["edges"],  width = 760,
  height = 330,
): GraphPoint[] {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }

  const centerX = width / 2;
  const centerY = height / 2;
  const radius = Math.max(36, Math.min(width, height) * 0.40);
  const ordered = [...memories].sort((left, right) => {
    const degreeDelta = (degree.get(right.nodeId) ?? 0) - (degree.get(left.nodeId) ?? 0);
    return degreeDelta || left.nodeId.localeCompare(right.nodeId);
  });

  return ordered.map((memory, index) => {
    const hash = stableHash(memory.nodeId);
    const angle = (index / Math.max(1, ordered.length)) * Math.PI * 2 + (hash % 1000) / 1000;
    const orbit = radius * (0.62 + ((hash >>> 8) % 36) / 100);
    const pointDegree = degree.get(memory.nodeId) ?? 0;
    return {
      ...memory,
      x: centerX + Math.cos(angle) * orbit,
      y: centerY + Math.sin(angle) * orbit,
      degree: pointDegree,
      radius: Math.min(8, 3.5 + Math.sqrt(pointDegree + 1)),
    };
  });
}

export type PageToken = number | "…";

export function pageTokens(current: number, total: number): PageToken[] {
  const page = Math.max(1, Math.min(current, total));
  const last = Math.max(1, total);
  if (last <= 7) return Array.from({ length: last }, (_, index) => index + 1);

  const values = new Set<number>([1, last]);
  if (page <= 4) {
    for (let value = 2; value <= 5; value += 1) values.add(value);
  } else if (page >= last - 3) {
    for (let value = last - 4; value < last; value += 1) values.add(value);
  } else {
    values.add(page - 1);
    values.add(page);
    values.add(page + 1);
  }
  const ordered = Array.from(values).filter((value) => value > 0 && value <= last).sort((left, right) => left - right);
  const result: PageToken[] = [];
  for (const value of ordered) {
    const previous = result[result.length - 1];
    if (typeof previous === "number" && value - previous > 1) result.push("…");
    result.push(value);
  }
  return result;
}

export function percent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

export function formatTimestamp(value: string | null): string {
  if (!value) return "unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
