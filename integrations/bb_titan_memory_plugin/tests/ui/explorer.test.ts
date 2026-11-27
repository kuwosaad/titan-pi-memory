import assert from "node:assert/strict";
import test, { after } from "node:test";
import { registerHooks } from "node:module";
const cssHook = registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true };
    return nextLoad(url, context);
  },
});
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
const browserWindow = dom.window;
for (const [key, value] of Object.entries({
  window: browserWindow,
  document: browserWindow.document,
  navigator: browserWindow.navigator,
  HTMLElement: browserWindow.HTMLElement,
  SVGElement: browserWindow.SVGElement,
  Node: browserWindow.Node,
  MutationObserver: browserWindow.MutationObserver,
  getComputedStyle: browserWindow.getComputedStyle.bind(browserWindow),
  requestAnimationFrame:
    browserWindow.requestAnimationFrame?.bind(browserWindow) ||
    ((callback: FrameRequestCallback) => setTimeout(callback, 0)),
  cancelAnimationFrame:
    browserWindow.cancelAnimationFrame?.bind(browserWindow) ||
    ((id: number) => clearTimeout(id)),
})) {
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
}

const { fireEvent, waitFor } = await import("@testing-library/react");
const { loadPluginApp, renderSlot } = await import("@get-bb/plugin-sdk/testing/app");
const app = await loadPluginApp(() => import("../../src/app.tsx"));
const action = app.threadPanelActions.find((entry) => entry.id === "titan-memory-explorer")!;
if (!action) throw new Error("Titan memory panel action was not registered");

const memory = {
  nodeId: "pi::m1",
  sourceAgent: "pi",
  memoryId: "m1",
  text: "A stored memory",
  type: "decision",
  stream: "learnings",
  timestamp: "2026-01-01T00:00:00Z",
  sessionId: "session-1",
  hasEmbedding: true,
};
const snapshot = { nodes: [memory], edges: [], partial: false, warnings: [] };
const detail = { memory, text: "Full stored memory", metadata: {}, truncated: false };
const catalog = {
  agents: [{ id: "pi", count: 1 }, { id: "grok", count: 0 }],
  types: ["decision", "learning"],
  warnings: [],
};

function handlers(overrides: Record<string, (input: any) => any> = {}) {
  return {
    catalog: async () => catalog,
    snapshot: async () => snapshot,
    search: async (input: any) => ({ items: [memory], page: input.page || 1, totalItems: 1, totalPages: 1, nextCursor: null, warnings: [] }),
    detail: async () => detail,
    cancel: async () => ({ cancelled: true }),
    ...overrides,
  } as any;
}

function mount(rpc: any = handlers()) {
  return renderSlot(action, { threadId: "thread-123", params: null }, { rpc, context: { threadId: "thread-123" } });
}

function calls(view: any, method: string) {
  return view.inspection.rpcCalls.filter((call: any) => call.method === method);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

test("catalog drives stable agent/type options and filters apply immediately", async () => {
  const view = mount();
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  const agent = view.container.querySelector('[aria-label="Filter by agent"]') as HTMLSelectElement;
  assert.match(agent.textContent || "", /grok/);
  await fireEvent.change(agent, { target: { value: "grok" } });
  const type = view.container.querySelector('[aria-label="Filter by type"]') as HTMLSelectElement;
  const from = view.container.querySelector('[aria-label="Filter from date"]') as HTMLInputElement;
  await fireEvent.change(type, { target: { value: "decision" } });
  await fireEvent.change(from, { target: { value: "2026-01-01" } });
  await waitFor(() => assert.equal(calls(view, "snapshot").at(-1)?.input.filters.agent, "grok"));
  const snapshotFilters = calls(view, "snapshot").at(-1)?.input.filters;
  const searchFilters = calls(view, "search").at(-1)?.input.filters;
  assert.deepEqual(searchFilters, snapshotFilters);
  assert.deepEqual(snapshotFilters, { agent: "grok", type: "decision", dateFrom: "2026-01-01" });
  view.unmount();
});

test("search input is debounced and first page omits cursor", async () => {
  const view = mount();
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  const input = view.container.querySelector('[aria-label="Search memories"]') as HTMLInputElement;
  await fireEvent.input(input, { target: { value: "needle" } });
  await new Promise((resolve) => setTimeout(resolve, 240));
  await waitFor(() => assert.equal(calls(view, "search").at(-1)?.input.filters.query, "needle"));
  assert.equal("cursor" in calls(view, "search").at(-1).input, false);
  assert.equal(calls(view, "search").at(-1).input.page, 1);
  view.unmount();
});

test("hiding records expands the graph and remains reversible", async () => {
  const view = mount();
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  await fireEvent.click(view.container.querySelector('[aria-label="Hide memories"]')!);
  assert.equal(view.container.querySelector(".titan-memory-list"), null);
  assert.ok(view.container.querySelector(".titan-explorer-grid--list-hidden"));
  await fireEvent.click(view.container.querySelector('[aria-label="Show memories"]')!);
  assert.ok(view.container.querySelector(".titan-memory-list"));
  view.unmount();
});

test("direct page navigation requests page mode without walking cursors", async () => {
  const pageItems = (page: number) => Array.from({ length: 50 }, (_, index) => ({
    ...memory,
    nodeId: `pi::m${page}-${index}`,
    memoryId: `m${page}-${index}`,
    text: `Page ${page} memory ${index}`,
  }));
  const view = mount(handlers({
    search: async (input: any) => ({ items: pageItems(input.page || 1), page: input.page || 1, totalItems: 500, totalPages: 10, nextCursor: null, warnings: [] }),
  }));
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 50));
  const pageFive = Array.from(view.container.querySelectorAll(".titan-page-button")).find((button) => button.textContent === "5") as HTMLButtonElement;
  await fireEvent.click(pageFive);
  await waitFor(() => assert.match(view.container.textContent || "", /Page 5 memory/));
  assert.equal(calls(view, "search").at(-1)?.input.page, 5);
  assert.equal("cursor" in calls(view, "search").at(-1).input, false);
  assert.equal(calls(view, "search").filter((call: any) => call.input.page > 1).length, 1);
  view.unmount();
});

test("closing details hides the panel, cancels detail, and ignores its late result", async () => {
  const pending = deferred<any>();
  const view = mount(handlers({ detail: async () => pending.promise }));
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.equal(calls(view, "detail").length, 1));
  const request = calls(view, "detail")[0].input;
  await fireEvent.click(view.container.querySelector('[aria-label="Close memory details"]')!);
  assert.equal(view.container.querySelector(".titan-panel--details"), null);
  assert.ok(calls(view, "cancel").some((call: any) => call.input.requestId === request.requestId));
  pending.resolve(detail);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(view.container.querySelector(".titan-panel--details"), null);
  view.unmount();
});

test("unmount cancels every active surface and ignores late snapshot results", async () => {
  const pending = deferred<any>();
  const view = mount(handlers({ snapshot: async () => pending.promise }));
  await waitFor(() => assert.equal(calls(view, "snapshot").length, 1));
  const request = calls(view, "snapshot")[0].input;
  view.unmount();
  await waitFor(() => assert.ok(calls(view, "cancel").some((call: any) => call.input.requestId === request.requestId)));
  pending.resolve(snapshot);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls(view, "search").length, 0);
});

test("correction history follows an off-snapshot memory and shows evidence limits", async () => {
  const earlier = { ...memory, nodeId: "pi::earlier", memoryId: "earlier", text: "Earlier belief" };
  const view = mount(handlers({ detail: async (input: any) => input.memoryId === "m1" ? {
    ...detail, corrections: [
      { direction: "replaces", memory: earlier, memoryId: "earlier", evidence: "Stored correction", timestamp: null },
      { direction: "replacedBy", memory: null, memoryId: "missing", evidence: "", timestamp: null },
    ], correctionsPartial: true,
    scene: { id: "scene-1", status: "partial", userText: "User excerpt", assistantText: "Assistant excerpt", eventIds: ["event-1"] },
    patterns: [{ id: "pattern-1", title: "A pattern", status: "active", role: "evidence" }], warnings: ["Evidence budget reached"],
  } : { ...detail, memory: earlier, text: "Full earlier belief", corrections: [], scene: null } }));
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Replaces earlier memory/));
  for (const pattern of [/Newer memory unavailable: missing/, /Correction history is partial/, /partial · scene-1/, /event-1/, /A pattern/, /Evidence budget reached/]) assert.match(view.container.textContent || "", pattern);
  const correction = Array.from(view.container.querySelectorAll("button")).find((button) => button.textContent?.includes("Earlier belief"));
  assert.ok(correction);
  await fireEvent.click(correction);
  await waitFor(() => assert.match(view.container.textContent || "", /Full earlier belief/));
  assert.match(view.container.textContent || "", /Outside the current graph and list/);
  assert.match(view.container.textContent || "", /No source scene linked/);
  assert.equal(calls(view, "detail").at(-1)?.input.memoryId, "earlier");
  view.unmount();
});

test("relationship filter distinguishes correction direction and restores both types", async () => {
  const other = { ...memory, nodeId: "pi::m2", memoryId: "m2" };
  const view = mount(handlers({ snapshot: async () => ({ nodes: [memory, other], edges: [
    { source: memory.nodeId, target: other.nodeId, kind: "supersedes", weight: 1 },
    { source: memory.nodeId, target: other.nodeId, kind: "similarity", weight: 0.8 },
  ], partial: false, warnings: [] }) }));
  await waitFor(() => assert.ok(view.container.querySelector('[aria-label="Relationship visibility"]')));
  const select = view.container.querySelector('[aria-label="Relationship visibility"]') as HTMLSelectElement;
  assert.match(view.container.textContent || "", /1 correction \(newer → earlier\)/);
  assert.match(view.container.textContent || "", /1 similarity/);
  await fireEvent.change(select, { target: { value: "supersedes" } });
  assert.match(view.container.textContent || "", /0 similarity/);
  await fireEvent.change(select, { target: { value: "similarity" } });
  assert.match(view.container.textContent || "", /0 corrections/);
  await fireEvent.change(select, { target: { value: "all" } });
  assert.match(view.container.textContent || "", /1 correction/);
  assert.match(view.container.textContent || "", /1 similarity/);
  view.unmount();
});

test("a search failure leaves a successfully loaded graph available", async () => {
  const view = mount(handlers({ search: async () => { throw new Error("Search unavailable"); } }));
  await waitFor(() => assert.match(view.container.textContent || "", /Search unavailable/));
  assert.ok(view.container.querySelector(".titan-graph3d-host"));
  assert.doesNotMatch(view.container.textContent || "", /No matching memories/);
  view.unmount();
});

test("snapshot failure leaves search and detail usable", async () => {
  const view = mount(handlers({ snapshot: async () => { throw new Error("Graph service unavailable"); } }));
  await waitFor(() => assert.match(view.container.textContent || "", /Graph service unavailable/));
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Full stored memory/));
  assert.equal(calls(view, "search").length, 1);
  view.unmount();
});

test("refresh reloads selected detail and partial empty history does not claim absence", async () => {
  let detailReads = 0;
  const view = mount(handlers({ detail: async () => ({ ...detail, text: `Detail ${++detailReads}`, corrections: [], correctionsPartial: true, scene: null }) }));
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Detail 1/));
  assert.match(view.container.textContent || "", /Correction history is partial/);
  assert.doesNotMatch(view.container.textContent || "", /No stored corrections found/);
  await fireEvent.click(view.container.querySelector('[aria-label="Refresh Titan data"]')!);
  await waitFor(() => assert.match(view.container.textContent || "", /Detail 2/));
  assert.ok(view.container.querySelector(".titan-panel--details"));
  view.unmount();
});

after(() => {
  dom.window.close();
  cssHook.deregister();
});
