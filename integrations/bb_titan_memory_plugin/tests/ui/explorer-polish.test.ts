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
browserWindow.HTMLCanvasElement.prototype.getContext = (() => null) as never;

const { createElement, StrictMode } = await import("react");
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
const catalog = { agents: [{ id: "pi", count: 1 }, { id: "codex", count: 3 }], types: ["decision"], warnings: [] };

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

function mount(rpc: any = handlers(), threadId = "thread-123", slot: any = action) {
  return renderSlot(slot, { threadId, params: null }, { rpc, context: { threadId } });
}

function calls(view: any, method: string) {
  return view.inspection.rpcCalls.filter((call: any) => call.method === method);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

const rows = (view: any) => view.container.querySelectorAll(".titan-memory-row").length;
const refresh = (view: any) => view.container.querySelector('[aria-label="Refresh Titan data"]') as HTMLButtonElement;

test("a mounted panel that receives a new threadId reloads instead of sticking on Loading", async () => {
  const view = mount(handlers(), "thread-A");
  await waitFor(() => assert.equal(rows(view), 1));
  view.rerender(createElement(action.component as any, { threadId: "thread-B", params: null }));
  await waitFor(() => assert.ok(calls(view, "search").some((call: any) => call.input.threadId === "thread-B")));
  await waitFor(() => assert.equal(refresh(view).disabled, false));
  assert.equal(rows(view), 1);
  assert.equal(view.container.querySelector(".titan-loading"), null);
  assert.ok(calls(view, "snapshot").some((call: any) => call.input.threadId === "thread-B"));
  view.unmount();
});

test("StrictMode's double mount still loads rows", async () => {
  const strict = { ...action, component: (props: any) => createElement(StrictMode, null, createElement(action.component as any, props)) };
  const view = mount(handlers(), "thread-strict", strict);
  await waitFor(() => assert.equal(rows(view), 1));
  await waitFor(() => assert.equal(refresh(view).disabled, false));
  view.unmount();
});

test("a busy backend is retried, so a quick detail click still opens", async () => {
  let detailCalls = 0;
  const view = mount(handlers({
    detail: async () => {
      detailCalls += 1;
      if (detailCalls === 1) throw Object.assign(new Error("too many Titan explorer requests are running"), { code: "busy" });
      return detail;
    },
  }));
  await waitFor(() => assert.equal(rows(view), 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Full stored memory/), { timeout: 2000 });
  assert.equal(detailCalls, 2);
  assert.doesNotMatch(view.container.textContent || "", /too many Titan explorer requests/);
  view.unmount();
});

test("the graph stays mounted during a reload and does not wait for the list", async () => {
  const nextSnapshot = deferred<any>();
  const nextSearch = deferred<any>();
  let snapshots = 0;
  let searches = 0;
  const view = mount(handlers({
    snapshot: async () => (++snapshots === 1 ? snapshot : nextSnapshot.promise),
    search: async (input: any) => (++searches === 1
      ? { items: [memory], page: input.page || 1, totalItems: 1, totalPages: 1, nextCursor: null, warnings: [] }
      : nextSearch.promise),
  }));
  await waitFor(() => assert.equal(rows(view), 1));
  const shell = view.container.querySelector(".titan-graph-shell");
  assert.ok(shell);
  await fireEvent.change(view.container.querySelector('[aria-label="Filter by type"]')!, { target: { value: "decision" } });
  await waitFor(() => assert.equal(calls(view, "snapshot").length, 2));
  assert.equal(view.container.querySelector(".titan-graph-shell"), shell, "the same graph element stays mounted");
  assert.match(view.container.textContent || "", /Updating…/);
  assert.equal(rows(view), 1, "previous rows stay visible while loading");

  nextSnapshot.resolve({ ...snapshot, nodes: [memory, { ...memory, nodeId: "pi::m2", memoryId: "m2" }] });
  await waitFor(() => assert.match(view.container.textContent || "", /2 memories/));
  assert.doesNotMatch(view.container.textContent || "", /Updating…/);
  assert.equal(view.container.querySelector(".titan-graph-shell"), shell);
  nextSearch.resolve({ items: [memory], page: 1, totalItems: 1, totalPages: 1, nextCursor: null, warnings: [] });
  await waitFor(() => assert.equal(refresh(view).disabled, false));
  view.unmount();
});

test("agent legend chips filter by agent", async () => {
  const view = mount();
  await waitFor(() => assert.ok(view.container.querySelector(".titan-agent-chip")));
  const chips = Array.from(view.container.querySelectorAll(".titan-agent-chip")) as HTMLButtonElement[];
  assert.deepEqual(chips.map((chip) => chip.textContent), ["codex", "pi"]);
  await fireEvent.click(chips[0]);
  await waitFor(() => assert.equal(calls(view, "snapshot").at(-1)?.input.filters.agent, "codex"));
  assert.equal((view.container.querySelector('[aria-label="Filter by agent"]') as HTMLSelectElement).value, "codex");
  view.unmount();
});

test("long types, empty text and bidi overrides cannot break rows", async () => {
  const longType = "t".repeat(128);
  const items = [
    { ...memory, type: longType },
    { ...memory, nodeId: "pi::empty", memoryId: "empty", text: "   " },
    { ...memory, nodeId: "pi::rlo", memoryId: "rlo", text: "‮override שלום text" },
  ];
  const view = mount(handlers({
    catalog: async () => ({ ...catalog, types: [longType] }),
    search: async () => ({ items, page: 1, totalItems: 3, totalPages: 1, nextCursor: null, warnings: [] }),
  }));
  await waitFor(() => assert.equal(rows(view), 3));
  const option = view.container.querySelector(`option[value="${longType}"]`) as HTMLOptionElement;
  assert.ok(option.textContent!.length <= 40);
  assert.equal(option.title, longType);
  const typeLabel = view.container.querySelector(".titan-memory-row-meta bdi") as HTMLElement;
  assert.equal(typeLabel.title, longType);
  assert.match(view.container.textContent || "", /\(empty memory\)/);
  const texts = Array.from(view.container.querySelectorAll(".titan-memory-row-text")) as HTMLElement[];
  assert.equal(texts.find((text) => text.textContent?.includes("override"))?.getAttribute("dir"), "auto");
  assert.equal(view.container.querySelector("[aria-pressed]:not(.titan-agent-chip)"), null, "rows that open details are not toggle buttons");
  view.unmount();
});

test("both empty states share one treatment and hint at the filters", async () => {
  const view = mount(handlers({
    snapshot: async () => ({ nodes: [], edges: [], partial: false, warnings: [] }),
    search: async () => ({ items: [], page: 1, totalItems: 0, totalPages: 1, nextCursor: null, warnings: [] }),
  }));
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-empty").length, 2));
  const hints = Array.from(view.container.querySelectorAll(".titan-empty span")).map((hint) => hint.textContent);
  assert.equal(new Set(hints).size, 1);
  assert.match(hints[0] || "", /not stored any memories/);
  await fireEvent.change(view.container.querySelector('[aria-label="Filter by type"]')!, { target: { value: "decision" } });
  await waitFor(() => assert.match(view.container.textContent || "", /Try clearing the filters/));
  view.unmount();
});

test("an empty store disables the search and filter controls", async () => {
  const empty = { nodes: [], edges: [], partial: false, warnings: [] };
  const noItems = async () => ({ items: [], page: 1, totalItems: 0, totalPages: 1, nextCursor: null, warnings: [] });
  const controls = (view: any) => ["Search memories", "Filter by agent", "Filter by type"]
    .map((label) => (view.container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement).disabled);
  const view = mount(handlers({
    catalog: async () => ({ agents: [{ id: "pi", count: 0 }], types: [], warnings: [] }),
    snapshot: async () => empty,
    search: noItems,
  }));
  await waitFor(() => assert.deepEqual(controls(view), [true, true, true]));
  const summary = view.container.querySelector(".titan-more-filters summary") as HTMLElement;
  assert.equal(summary.getAttribute("aria-disabled"), "true");
  await fireEvent.click(summary);
  assert.equal((view.container.querySelector(".titan-more-filters") as HTMLDetailsElement).open, false);
  view.unmount();

  // An empty current view of a store that has memories keeps every control usable.
  const filtered = mount(handlers({ snapshot: async () => empty, search: noItems }));
  await waitFor(() => assert.equal(filtered.container.querySelectorAll(".titan-empty").length, 2));
  await waitFor(() => assert.deepEqual(controls(filtered), [false, false, false]));
  assert.equal((filtered.container.querySelector(".titan-more-filters summary") as HTMLElement).hasAttribute("aria-disabled"), false);
  filtered.unmount();
});

test("an inverted date range is flagged", async () => {
  const view = mount();
  await waitFor(() => assert.equal(rows(view), 1));
  await fireEvent.change(view.container.querySelector('[aria-label="Filter from date"]')!, { target: { value: "2026-02-01" } });
  await fireEvent.change(view.container.querySelector('[aria-label="Filter to date"]')!, { target: { value: "2026-01-01" } });
  await waitFor(() => assert.match(view.container.textContent || "", /From is after To/));
  view.unmount();
});

test("a stored correction cycle is called out instead of listing contradictory labels silently", async () => {
  const other = { ...memory, nodeId: "pi::m2", memoryId: "m2", text: "Other belief" };
  const view = mount(handlers({
    detail: async () => ({
      ...detail,
      corrections: [
        { direction: "replaces", memory: other, memoryId: "m2", evidence: "", timestamp: null },
        { direction: "replacedBy", memory: other, memoryId: "m2", evidence: "", timestamp: null },
      ],
      correctionsPartial: false,
      scene: null,
    }),
  }));
  await waitFor(() => assert.equal(rows(view), 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Conflicting correction history/));
  view.unmount();
});

test("switching threads drops the previous thread's rows at once, so a stale memory cannot be sent to the new host", async () => {
  const pendingSnapshot = deferred<any>();
  const pendingSearch = deferred<any>();
  const view = mount(handlers({
    snapshot: async (input: any) => (input.threadId === "thread-B" ? pendingSnapshot.promise : snapshot),
    search: async (input: any) => (input.threadId === "thread-B"
      ? pendingSearch.promise
      : { items: [memory], page: 1, totalItems: 1, totalPages: 1, nextCursor: null, warnings: [] }),
  }), "thread-A");
  await waitFor(() => assert.equal(rows(view), 1));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Full stored memory/));

  view.rerender(createElement(action.component as any, { threadId: "thread-B", params: null }));
  // Thread B is still loading; nothing from thread A may remain clickable.
  assert.equal(rows(view), 0);
  assert.equal(view.container.querySelector(".titan-graph-shell"), null);
  assert.equal(view.container.querySelector(".titan-panel--details"), null);
  for (const button of Array.from(view.container.querySelectorAll(".titan-memory-row, .titan-connection"))) {
    await fireEvent.click(button);
  }
  assert.equal(calls(view, "detail").filter((call: any) => call.input.threadId === "thread-B").length, 0);
  // Thread A's requests were cancelled against thread A, not the new thread.
  assert.ok(calls(view, "cancel").every((call: any) => call.input.threadId === "thread-A"));

  const other = { ...memory, nodeId: "pi::b1", memoryId: "b1", text: "Thread B memory" };
  pendingSnapshot.resolve({ ...snapshot, nodes: [other] });
  pendingSearch.resolve({ items: [other], page: 1, totalItems: 1, totalPages: 1, nextCursor: null, warnings: [] });
  await waitFor(() => assert.match(view.container.querySelector(".titan-memory-row")?.textContent || "", /Thread B memory/));
  view.unmount();
});

test("a longer correction cycle in the loaded edges is flagged", async () => {
  const m2 = { ...memory, nodeId: "pi::m2", memoryId: "m2", text: "Second" };
  const m3 = { ...memory, nodeId: "pi::m3", memoryId: "m3", text: "Third" };
  const chain = (closing: "replacedBy" | "replaces") => mount(handlers({
    snapshot: async () => ({
      nodes: [memory, m2, m3],
      edges: [
        { source: memory.nodeId, target: m2.nodeId, kind: "supersedes", weight: 1 },
        { source: m2.nodeId, target: m3.nodeId, kind: "supersedes", weight: 1 },
      ],
      partial: false,
      warnings: [],
    }),
    detail: async () => ({
      ...detail,
      corrections: [
        { direction: "replaces", memory: m2, memoryId: "m2", evidence: "", timestamp: null },
        { direction: closing, memory: m3, memoryId: "m3", evidence: "", timestamp: null },
      ],
      correctionsPartial: false,
      scene: null,
    }),
  }));

  // m1 -> m2 -> m3 -> m1 is a three-memory cycle.
  const cycle = chain("replacedBy");
  await waitFor(() => assert.equal(rows(cycle), 1));
  await fireEvent.click(cycle.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(cycle.container.textContent || "", /Conflicting correction history/));
  cycle.unmount();

  // m1 -> m2 -> m3 and m1 -> m3 is consistent: no warning.
  const consistent = chain("replaces");
  await waitFor(() => assert.equal(rows(consistent), 1));
  await fireEvent.click(consistent.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(consistent.container.textContent || "", /Correction history/));
  assert.doesNotMatch(consistent.container.textContent || "", /Conflicting correction history/);
  consistent.unmount();
});

after(() => {
  dom.window.close();
  cssHook.deregister();
});
