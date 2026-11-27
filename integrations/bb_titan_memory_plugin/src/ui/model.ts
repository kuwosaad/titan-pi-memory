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

export const MAX_PANEL_REQUESTS = 2;
export const BUSY_RETRY_DELAYS_MS = [250, 600, 1200, 2400] as const;

/**
 * Categorical agent palette. It must equal the browser graph's --agent-1..8
 * tokens (app/graph/ui/styles.css), assigned the same way (sorted agent
 * name), so an agent has one colour on both surfaces. Entries never equal
 * the selection or correction colours, which stay reserved.
 */
export const AGENT_PALETTE = ["#56B4E9", "#F0E442", "#3CC8A4", "#B49CFF", "#A6D96A", "#7F8CFF", "#A3ADBA", "#8BE9FD"] as const;
export const SELECTION_COLOR = "#F06A8B";
export const CORRECTION_COLOR = "#E8B072";
export const LOCAL_AGENT = "local";

export interface RequestGate {
  activate(request: string): string | null;
  isCurrent(request: string): boolean;
  close(): string | null;
  reopen(): void;
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
    reopen() {
      open = true;
    },
  };
}

export class RequestSkippedError extends Error {
  constructor() {
    super("Titan explorer request was superseded");
    this.name = "RequestSkippedError";
  }
}

export function isBusyError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  if ((error as { code?: unknown }).code === "busy") return true;
  return error instanceof Error && /too many Titan explorer requests/i.test(error.message);
}

export interface RequestScheduler {
  run<T>(task: () => Promise<T>, wanted?: () => boolean): Promise<T>;
}

/**
 * Limits one panel to a few in-flight backend calls and retries the shared
 * backend's "busy" rejection with backoff, so a second panel or a quick
 * detail click waits its turn instead of failing. Queued work that is no
 * longer wanted (a newer request replaced it) is skipped before it is sent.
 */
export function createRequestScheduler(
  limit = MAX_PANEL_REQUESTS,
  delays: readonly number[] = BUSY_RETRY_DELAYS_MS,
  wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
): RequestScheduler {
  let active = 0;
  const queue: Array<() => void> = [];
  const acquire = () => {
    if (active < limit) {
      active += 1;
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => queue.push(resolve));
  };
  const release = () => {
    const next = queue.shift();
    if (next) next();
    else active -= 1;
  };
  return {
    async run(task, wanted = () => true) {
      for (let attempt = 0; ; attempt += 1) {
        await acquire();
        try {
          if (!wanted()) throw new RequestSkippedError();
          return await task();
        } catch (error) {
          if (!isBusyError(error) || attempt >= delays.length || !wanted()) throw error;
        } finally {
          release();
        }
        await wait(delays[attempt]);
      }
    },
  };
}

/** Stable colour per agent: sorted order, so filters never reshuffle colours. */
export function agentColors(agents: Iterable<string | null | undefined>): Map<string, string> {
  const names = Array.from(new Set(Array.from(agents, (agent) => agent || LOCAL_AGENT)))
    .sort((left, right) => left.localeCompare(right));
  return new Map(names.map((name, index) => [name, AGENT_PALETTE[index % AGENT_PALETTE.length]]));
}

export function mergeWarnings(...groups: Array<readonly string[] | undefined>): string[] {
  return Array.from(new Set(groups.flatMap((group) => group || []))).slice(0, MAX_WARNINGS);
}

type GraphemeSegmenter = { segment(input: string): Iterable<{ segment: string }> };
type GraphemeSegmenterConstructor = new (locale?: string, options?: { granularity: "grapheme" }) => GraphemeSegmenter;

function graphemes(text: string): string[] {
  const Segmenter = (Intl as unknown as { Segmenter?: GraphemeSegmenterConstructor }).Segmenter;
  if (Segmenter) return Array.from(new Segmenter(undefined, { granularity: "grapheme" }).segment(text), (part) => part.segment);
  return Array.from(text);
}

/** Collapses whitespace and truncates by grapheme so emoji are never split. */
export function shortText(value: string, max = MAX_PREVIEW_LENGTH): string {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  const parts = graphemes(text);
  if (parts.length <= max) return text;
  return `${parts.slice(0, Math.max(0, max - 1)).join("").trimEnd()}…`;
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
    if (edge.kind !== "similarity") continue;
    const neighborId = edge.source === nodeId ? edge.target : edge.target === nodeId ? edge.source : null;
    if (!neighborId) continue;
    const memory = byId.get(neighborId);
    if (memory) result.push({ memory, weight: Number(edge.weight) });
  }
  return result.sort((left, right) => right.weight - left.weight).slice(0, MAX_NEIGHBORS);
}

/**
 * Whether `nodeId` lies on a directed cycle of stored corrections
 * (replacement -> replaced), of any length, within the given edges.
 */
export function inCorrectionCycle(
  nodeId: string,
  edges: ReadonlyArray<{ source: string; target: string }>,
): boolean {
  const next = new Map<string, string[]>();
  for (const edge of edges) {
    const targets = next.get(edge.source);
    if (targets) targets.push(edge.target);
    else next.set(edge.source, [edge.target]);
  }
  const seen = new Set<string>();
  const stack = [...(next.get(nodeId) || [])];
  while (stack.length) {
    const current = stack.pop()!;
    if (current === nodeId) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    stack.push(...(next.get(current) || []));
  }
  return false;
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
