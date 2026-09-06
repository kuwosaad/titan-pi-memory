import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";

const cssHook = registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true };
    return nextLoad(url, context);
  },
});

const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
const browserWindow = dom.window;
for (const [key, value] of Object.entries({
  window: browserWindow,
  document: browserWindow.document,
  navigator: browserWindow.navigator,
  HTMLElement: browserWindow.HTMLElement,
  HTMLCanvasElement: browserWindow.HTMLCanvasElement,
  Node: browserWindow.Node,
  MutationObserver: browserWindow.MutationObserver,
  getComputedStyle: browserWindow.getComputedStyle.bind(browserWindow),
})) {
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
}

const React = await import("react");
const { fireEvent, render } = await import("@testing-library/react");
const { MemoryGraph, connectionColor } = await import("../../src/ui/graph.js");

const memory = {
  nodeId: "pi::m1",
  sourceAgent: "pi",
  memoryId: "m1",
  text: "A graph memory",
  type: "decision",
  stream: "learnings",
  timestamp: "2026-01-01T00:00:00Z",
  sessionId: "session-1",
  hasEmbedding: true,
};
const snapshot = {
  nodes: [memory],
  edges: [],
  partial: false,
  warnings: [],
};

function mount() {
  return render(React.createElement(MemoryGraph, {
    snapshot,
    selectedNodeId: null,
    onSelect: () => undefined,
  }));
}

test("uses the real 3D force graph host rather than the legacy flat SVG renderer", () => {
  const view = mount();
  assert.ok(view.container.querySelector(".titan-graph3d-stage"));
  assert.ok(view.container.querySelector(".titan-graph3d-host"));
  assert.equal(view.container.querySelector("svg"), null);
  const fit = view.container.querySelector(".titan-graph-fit") as HTMLButtonElement;
  assert.ok(fit);
  fireEvent.click(fit);
  view.unmount();
});

test("3D canvas has a keyboard-accessible memory-list alternative", () => {
  const view = mount();
  assert.match(view.container.textContent || "", /Use the memories list for keyboard navigation/);
  const host = view.container.querySelector(".titan-graph3d-host") as HTMLElement;
  assert.ok(host.getAttribute("role") === "img");
  view.unmount();
});

test.after(() => {
  dom.window.close();
  cssHook.deregister();
});


test("the idle graph keeps connections visible and focus dims only unrelated links", () => {
  const idle = connectionColor("pi::a", "codex::b", null);
  const dimmed = connectionColor("pi::a", "codex::b", "pi::c");
  const brightness = (hex: string) => [1, 3, 5].reduce((sum, index) => sum + parseInt(hex.slice(index, index + 2), 16), 0);
  assert.ok(brightness(idle) > brightness(dimmed) * 2);
  assert.ok(brightness(connectionColor("pi::a", "codex::b", "codex::b")) > brightness(idle));
  assert.equal(connectionColor("pi::a", "codex::b", "pi::a"), connectionColor("codex::b", "pi::a", "pi::a"));
});
