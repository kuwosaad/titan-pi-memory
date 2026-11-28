import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";

// Swap the WebGL force graph for a fake that keeps the library's frame-loop
// contract: _animationCycle runs tickFrame (which may call onEngineStop) and
// only then requests the next frame, and meshes are built on a later digest.
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "3d-force-graph") {
      return { url: "data:text/javascript,export default globalThis.__titanFakeForceGraph", format: "module", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true };
    return nextLoad(url, context);
  },
});

const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
const browserWindow = dom.window;

let nextFrameId = 1;
const frames = new Map<number, FrameRequestCallback>();
for (const [key, value] of Object.entries({
  window: browserWindow,
  document: browserWindow.document,
  navigator: browserWindow.navigator,
  HTMLElement: browserWindow.HTMLElement,
  HTMLCanvasElement: browserWindow.HTMLCanvasElement,
  Node: browserWindow.Node,
  MutationObserver: browserWindow.MutationObserver,
  getComputedStyle: browserWindow.getComputedStyle.bind(browserWindow),
  requestAnimationFrame: (callback: FrameRequestCallback) => {
    const id = nextFrameId++;
    frames.set(id, callback);
    return id;
  },
  cancelAnimationFrame: (id: number) => { frames.delete(id); },
  // The graph listens for resize and pointerup on the global object.
  addEventListener: browserWindow.addEventListener.bind(browserWindow),
  removeEventListener: browserWindow.removeEventListener.bind(browserWindow),
})) {
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
}
// jsdom reports a hidden document by default; the graph pauses while hidden.
Object.defineProperty(browserWindow.document, "hidden", { configurable: true, get: () => false });
// Report WebGL as available so the graph mounts the (fake) renderer.
browserWindow.HTMLCanvasElement.prototype.getContext = (() => ({ getExtension: () => null })) as never;

function pumpFrames(count: number) {
  for (let index = 0; index < count; index += 1) {
    const pending = Array.from(frames.entries());
    frames.clear();
    for (const [, callback] of pending) callback(0);
  }
}

type Any = any;
const instances: FakeForceGraph[] = [];

class FakeForceGraph {
  props: Record<string, Any> = {};
  data: { nodes: Any[]; links: Any[] } = { nodes: [], links: [] };
  meshes = new Map<string, Any>();
  animationFrameRequestId: number | null = null;
  engineRunning = false;
  ticks = 0;
  canvas: HTMLCanvasElement;

  constructor(element: HTMLElement) {
    this.canvas = document.createElement("canvas");
    element.append(this.canvas);
    instances.push(this);
    this._animationCycle = this._animationCycle.bind(this);
    const self = this;
    const proxy: Any = new Proxy(this, {
      get(target: Any, key: string) {
        if (key in target) return typeof target[key] === "function" ? target[key].bind(proxy) : target[key];
        return (...args: Any[]) => {
          if (!args.length) return self.props[key];
          self.props[key] = args[0];
          return proxy;
        };
      },
    });
    this._animationCycle();
    return proxy;
  }

  graphData(next?: { nodes: Any[]; links: Any[] }) {
    if (!next) return this.data;
    this.data = next;
    this.engineRunning = true;
    this.ticks = 0;
    // three-forcegraph rebuilds node objects on a debounced digest.
    setTimeout(() => {
      this.meshes = new Map(this.data.nodes.map((node) => [node.id, this.props.nodeThreeObject(node)]));
    }, 1);
    return this;
  }

  tickFrame() {
    if (!this.engineRunning) return;
    this.ticks += 1;
    if (this.ticks >= 3) {
      this.engineRunning = false;
      this.props.onEngineStop?.();
    }
  }

  _animationCycle() {
    this.tickFrame();
    this.animationFrameRequestId = requestAnimationFrame(this._animationCycle);
  }

  pauseAnimation() {
    if (this.animationFrameRequestId !== null) {
      cancelAnimationFrame(this.animationFrameRequestId);
      this.animationFrameRequestId = null;
    }
    return this;
  }

  resumeAnimation() {
    if (this.animationFrameRequestId === null) this._animationCycle();
    return this;
  }

  zoomToFit() { return this; }
  renderer() { return { domElement: this.canvas }; }
  controls() { return {}; }
  d3Force() { return null; }
  _destructor() { this.pauseAnimation(); }
}
(globalThis as Any).__titanFakeForceGraph = FakeForceGraph;

const React = await import("react");
const { render, waitFor } = await import("@testing-library/react");
const { MemoryGraph } = await import("../../src/ui/graph.js");

function memory(id: string) {
  return {
    nodeId: `pi::${id}`,
    sourceAgent: "pi",
    memoryId: id,
    text: `Memory ${id}`,
    type: "decision",
    stream: "learnings",
    timestamp: "2026-01-01T00:00:00Z",
    sessionId: "session-1",
    hasEmbedding: true,
  };
}

function snapshotOf(...ids: string[]) {
  return { nodes: ids.map(memory), edges: [], partial: false, warnings: [] };
}

function element(snapshot: Any, selectedNodeId: string | null) {
  return React.createElement(MemoryGraph, {
    snapshot,
    selectedNodeId,
    onSelect: () => undefined,
    relationshipFilter: "all",
    onRelationshipFilterChange: () => undefined,
  });
}

async function mountedGraph(view: Any, count: number) {
  await waitFor(() => assert.equal(instances.length, count));
  const graph = instances[count - 1];
  await waitFor(() => assert.ok(graph.meshes.size > 0, view.container.textContent));
  return graph;
}

test("the renderer really pauses after the layout stops instead of looping forever", async () => {
  const view = render(element(snapshotOf("a", "b"), null));
  const graph = await mountedGraph(view, 1);
  pumpFrames(5);
  assert.equal(graph.engineRunning, false, "the fake layout stopped inside a frame callback");
  // The settle window granted for new data (1.6s) must still end in a pause.
  await waitFor(() => assert.equal(graph.animationFrameRequestId, null), { timeout: 3000 });
  assert.equal(frames.size, 0, "no frame is left scheduled");
  view.unmount();
});

test("a selection that survives a data refresh keeps its halo on the new meshes", async () => {
  const view = render(element(snapshotOf("a", "b"), "pi::a"));
  const graph = await mountedGraph(view, 2);
  const halo = (id: string) => graph.meshes.get(id).children[0].visible;
  await waitFor(() => assert.equal(halo("pi::a"), true));
  const before = graph.meshes.get("pi::a");
  view.rerender(element(snapshotOf("a", "b", "c"), "pi::a"));
  await waitFor(() => assert.equal(graph.meshes.size, 3));
  assert.notEqual(graph.meshes.get("pi::a"), before, "the library built a replacement mesh");
  assert.equal(halo("pi::a"), true);
  assert.equal(halo("pi::b"), false);
  assert.equal(graph.meshes.get("pi::c").material.opacity, 0.24, "unrelated nodes start dimmed around the focus");
  view.unmount();
});

test.after(() => {
  dom.window.close();
  hooks.deregister();
});
