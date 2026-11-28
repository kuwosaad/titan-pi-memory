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
// jsdom has no WebGL; keep its "not implemented" canvas warning out of the output.
browserWindow.HTMLCanvasElement.prototype.getContext = (() => null) as never;

const React = await import("react");
const { fireEvent, render } = await import("@testing-library/react");
const { DEFAULT_GRAPH_COLORS, MemoryGraph, WEBGL_UNAVAILABLE, readGraphColors } = await import("../../src/ui/graph.js");
const { AGENT_PALETTE, CORRECTION_COLOR, SELECTION_COLOR } = await import("../../src/ui/model.js");

function memory(id: string, sourceAgent: string) {
  return {
    nodeId: `${sourceAgent}::${id}`,
    sourceAgent,
    memoryId: id,
    text: `Memory ${id}`,
    type: "decision",
    stream: "learnings",
    timestamp: "2026-01-01T00:00:00Z",
    sessionId: "session-1",
    hasEmbedding: true,
  };
}

const nodes = [memory("a", "pi"), memory("b", "codex")];
const similarityOnly = {
  nodes,
  edges: [{ source: nodes[0].nodeId, target: nodes[1].nodeId, kind: "similarity" as const, weight: 0.8 }],
  partial: false,
  warnings: [],
};

function mount(props: Record<string, unknown> = {}) {
  return render(React.createElement(MemoryGraph, {
    snapshot: similarityOnly,
    selectedNodeId: null,
    onSelect: () => undefined,
    relationshipFilter: "all",
    onRelationshipFilterChange: () => undefined,
    ...props,
  }));
}

test("without WebGL the graph explains itself and disables 3D controls", () => {
  const view = mount();
  assert.match(view.container.textContent || "", new RegExp(WEBGL_UNAVAILABLE.slice(0, 40)));
  assert.doesNotMatch(view.container.textContent || "", /THREE|WebGLRenderer|Loading graph/);
  assert.equal((view.container.querySelector(".titan-graph-fit") as HTMLButtonElement).disabled, true);
  assert.equal((view.container.querySelector('[aria-label="Relationship visibility"]') as HTMLSelectElement).disabled, true);
  assert.doesNotMatch(view.container.textContent || "", /Drag to orbit/);
  assert.ok(view.container.querySelector(".titan-graph3d-stage--unavailable"));
  view.unmount();
});

test("agent legend chips use the shared palette and toggle the agent filter", () => {
  const changes: Array<string | null> = [];
  const view = mount({ agents: ["pi", "codex", "grok"], onAgentFilterChange: (agent: string | null) => changes.push(agent) });
  const chips = Array.from(view.container.querySelectorAll(".titan-agent-chip")) as HTMLButtonElement[];
  assert.deepEqual(chips.map((chip) => chip.textContent), ["codex", "grok", "pi"]);
  const dots = chips.map((chip) => (chip.querySelector(".titan-agent-dot") as HTMLElement).style.background);
  const rgb = (hex: string) => `rgb(${[1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16)).join(", ")})`;
  assert.deepEqual(dots, AGENT_PALETTE.slice(0, 3).map(rgb));
  assert.equal(chips[2].getAttribute("aria-pressed"), "false");
  fireEvent.click(chips[2]);
  view.rerender(React.createElement(MemoryGraph, {
    snapshot: similarityOnly,
    selectedNodeId: null,
    onSelect: () => undefined,
    relationshipFilter: "all",
    onRelationshipFilterChange: () => undefined,
    agents: ["pi", "codex", "grok"],
    agentFilter: "pi",
    onAgentFilterChange: (agent: string | null) => changes.push(agent),
  }));
  const active = view.container.querySelector('.titan-agent-chip[aria-pressed="true"]') as HTMLButtonElement;
  assert.equal(active.textContent, "pi");
  fireEvent.click(active);
  assert.deepEqual(changes, ["pi", null]);
  view.unmount();
});

test("a relationship kind with no links cannot be chosen", () => {
  const view = mount();
  const options = Array.from(view.container.querySelectorAll('[aria-label="Relationship visibility"] option')) as HTMLOptionElement[];
  assert.equal(options.find((option) => option.value === "supersedes")?.disabled, true);
  assert.equal(options.find((option) => option.value === "similarity")?.disabled, false);
  view.unmount();
});

test("the legend only keys link kinds the snapshot has", () => {
  const view = mount();
  const footer = view.container.querySelector(".titan-graph-footer")?.textContent || "";
  assert.match(footer, /1 similarity link/);
  assert.doesNotMatch(footer, /correction/);
  assert.equal(view.container.querySelector(".titan-graph-line--correction"), null);
  view.unmount();
});

test("drawn edge colours default to the reserved correction and selection colours", () => {
  assert.equal(DEFAULT_GRAPH_COLORS.correction, CORRECTION_COLOR);
  assert.equal(DEFAULT_GRAPH_COLORS.selection, SELECTION_COLOR);
  const host = document.createElement("div");
  host.style.setProperty("--titan-edge-correction", "#123456");
  host.style.setProperty("--titan-edge-similarity", "not-a-colour");
  document.body.append(host);
  const colors = readGraphColors(host);
  assert.equal(colors.correction, "#123456");
  assert.equal(colors.similarity, DEFAULT_GRAPH_COLORS.similarity);
  host.remove();
});

test("similarity links at rest keep about 3:1 contrast against the stage", () => {
  const channels = (hex: string) => [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));
  const luminance = (rgb: number[]) => {
    const [r, g, b] = rgb.map((value) => {
      const unit = value / 255;
      return unit <= 0.03928 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const stage = channels("#171a1f");
  const opacity = 0.6; // linkOpacity in graph.tsx and --titan-edge-opacity in graph.css
  const drawn = channels(DEFAULT_GRAPH_COLORS.similarity).map((value, index) => value * opacity + stage[index] * (1 - opacity));
  const contrast = (luminance(drawn) + 0.05) / (luminance(stage) + 0.05);
  assert.ok(contrast >= 3, `similarity contrast ${contrast.toFixed(2)}`);
});

test.after(() => {
  dom.window.close();
  cssHook.deregister();
});
