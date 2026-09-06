import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";
const cssHook = registerHooks({ load(url, context, nextLoad) {
  if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true };
  return nextLoad(url, context);
} });
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
const app = await loadPluginApp(() => import("../src/app.tsx"));
const action = app.threadPanelActions.find((entry) => entry.id === "titan-memory-explorer")!;
assert.ok(action);

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
const detail = {
  memory,
  text: "Full stored memory",
  metadata: { source_type: "test" },
  truncated: false,
};

function handlers(overrides: Record<string, (input: any) => any> = {}) {
  return {
    catalog: async () => ({agents:[{id:"pi",count:1}],types:["decision"],warnings:[]}),
    snapshot: async () => snapshot,
    search: async () => ({ items: [memory], nextCursor: null, warnings: [] }),
    detail: async () => detail,
    cancel: async () => ({ cancelled: true }),
    ...overrides,
  } as any;
}

function mount(rpc: any = handlers()) {
  return renderSlot(action, { threadId: "thread-123", params: null }, { rpc, context: { threadId: "thread-123" } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

function calls(view: any, method: string) {
  return view.inspection.rpcCalls.filter((call: any) => call.method === method);
}

test("registers native slot and performs no RPC before mount", () => {
  assert.equal(app.threadPanelActions.length, 1);
  assert.equal(action.title, "Titan Memory");
  assert.equal(action.layout, "flush");
});

test("mount fetches snapshot then search and selection loads detail", async () => {
  const view = mount();
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
  assert.deepEqual(calls(view, "snapshot").length, 1);
  assert.deepEqual(calls(view, "search").length, 1);
  assert.equal("cursor" in calls(view, "search")[0].input, false, "optional cursor is omitted on first page");

  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Full stored memory/));
  assert.equal(calls(view, "detail").length, 1);
  view.unmount();
});

test("empty and renderer-error states retain accessible fallback content", async () => {
  const emptyView = mount(
    handlers({
      snapshot: async () => ({ nodes: [], edges: [], partial: false, warnings: [] }),
      search: async () => ({ items: [], nextCursor: null, warnings: [] }),
    }),
  );
  await waitFor(() => assert.match(emptyView.container.textContent || "", /No memories found/));
  assert.match(emptyView.container.textContent || "", /No matching memories/);
  emptyView.unmount();

  const backendErrorView = mount(handlers({ snapshot: async () => { throw new Error("backend down"); } }));
  await waitFor(() => assert.match(backendErrorView.container.textContent || "", /backend down/));
  assert.doesNotMatch(backendErrorView.container.textContent || "", /remembered work.*Memory similarity graph/s);
  backendErrorView.unmount();

  const malformedNode = { ...memory, nodeId: null };
  const graphErrorView = mount(
    handlers({ snapshot: async () => ({ nodes: [malformedNode], edges: [], partial: false, warnings: [] }) }),
  );
  await waitFor(() => assert.match(graphErrorView.container.textContent || "", /Graph unavailable/));
  assert.match(graphErrorView.container.textContent || "", /A stored memory/);
  graphErrorView.unmount();
});

test("a stale pagination response does not strand loading after detail selection", async () => {
  const pendingPage = deferred<any>();
  const view = mount(
    handlers({
      search: async (input) => (input.page === 2 ? pendingPage.promise : { items: [memory], nextCursor: null, warnings: [], page:1, totalItems:100, totalPages:2 }),
    }),
  );
  await waitFor(() => assert.ok(view.container.querySelector(".titan-pagination")));
  const next = Array.from(view.container.querySelectorAll(".titan-pagination button")).find((button) => button.textContent === "Next")! as HTMLButtonElement;
  await fireEvent.click(next);
  await waitFor(() => assert.equal((Array.from(view.container.querySelectorAll(".titan-pagination button")).find((button) => button.textContent === "Next")! as HTMLButtonElement).disabled, true));
  await fireEvent.click(view.container.querySelector(".titan-memory-row")!);
  await waitFor(() => assert.match(view.container.textContent || "", /Full stored memory/));
  assert.equal((Array.from(view.container.querySelectorAll(".titan-pagination button")).find((button) => button.textContent === "Next")! as HTMLButtonElement).disabled, true);
  pendingPage.resolve({ items: [memory], nextCursor: null, warnings: [], page:2, totalItems:150, totalPages:3 });
  await waitFor(() => assert.equal((Array.from(view.container.querySelectorAll(".titan-pagination button")).find((button) => button.textContent === "Next")! as HTMLButtonElement).textContent, "Next"));
  view.unmount();
});

test("pagination replaces the page instead of silently accumulating past 500", async () => {
  const pageItems = (page: number) => Array.from({ length: 50 }, (_, index) => ({
    ...memory,
    nodeId: `pi::m${page * 50 + index}`,
    memoryId: `m${page * 50 + index}`,
    text: `Memory ${page * 50 + index}`,
  }));
  const view = mount(
    handlers({
      search: async (input) => {
        const page = (input.page || 1) - 1;
        return { items: pageItems(page), nextCursor: null, warnings: [], page:page+1,totalItems:550,totalPages:11 };
      },
    }),
  );
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 50));
  const next = () => Array.from(view.container.querySelectorAll(".titan-pagination button")).find((button) => button.textContent === "Next")! as HTMLButtonElement;
  await fireEvent.click(next());
  await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 50));
  assert.match(view.container.textContent || "", /Memory 50/);
  assert.doesNotMatch(view.container.textContent || "", /Memory 0\b/);
  assert.equal(view.container.querySelector("[aria-current=page]")?.textContent,"2");
  assert.equal(next().disabled, false);
  view.unmount();
});

test("twenty mount/unmount cycles settle without outstanding RPC handlers", async () => {
  let active = 0;
  for (let cycle = 0; cycle < 20; cycle += 1) {
    const rpc = handlers({
      snapshot: async () => {
        active += 1;
        await Promise.resolve();
        active -= 1;
        return snapshot;
      },
      search: async () => {
        active += 1;
        await Promise.resolve();
        active -= 1;
        return { items: [memory], nextCursor: null, warnings: [] };
      },
    });
    const view = mount(rpc);
    await waitFor(() => assert.equal(view.container.querySelectorAll(".titan-memory-row").length, 1));
    view.unmount();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.equal(active, 0);
});

test("closing during a snapshot cancels it and prevents the follow-up search", async () => {
  const pending = deferred<any>();
  const view = mount(handlers({ snapshot: async () => pending.promise }));
  await waitFor(() => assert.equal(calls(view, "snapshot").length, 1));
  const request = calls(view, "snapshot")[0].input;
  view.unmount();
  await waitFor(() => assert.ok(calls(view, "cancel").some((call: any) =>
    call.input.requestId === request.requestId && call.input.threadId === request.threadId)));
  pending.resolve(snapshot);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls(view, "search").length, 0);
});

test.after(() => { dom.window.close(); cssHook.deregister(); });
