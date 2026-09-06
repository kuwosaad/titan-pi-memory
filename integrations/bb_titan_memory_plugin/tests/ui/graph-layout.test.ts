import assert from "node:assert/strict";
import test from "node:test";
import {
  GRAPH_HEIGHT,
  GRAPH_WIDTH,
  MAX_ZOOM,
  MIN_ZOOM,
  clampTransform,
  layoutGraph,
  movePoint,
  neighborMap,
  resetTransform,
  zoomAt,
} from "../../src/ui/graph-layout.ts";

function memory(index: number) {
  return {
    nodeId: `source-${index % 3}:memory-${index}`,
    sourceAgent: index % 3 === 0 ? "pi" : index % 3 === 1 ? "codex" : "grok",
    memoryId: `memory-${index}`,
    text: `Memory ${index}`,
    type: index % 2 ? "decision" : "note",
    stream: "learnings",
    timestamp: "2026-01-01T00:00:00Z",
    sessionId: `session-${Math.floor(index / 10)}`,
    hasEmbedding: true,
  };
}

function fixture(count = 18) {
  const nodes = Array.from({ length: count }, (_, index) => memory(index));
  const edges = Array.from({ length: Math.min(40, count * 2) }, (_, index) => ({
    source: nodes[index % nodes.length].nodeId,
    target: nodes[(index * 7 + 3) % nodes.length].nodeId,
    kind: "similarity" as const,
    weight: 0.42 + (index % 6) / 10,
  })).filter((edge) => edge.source !== edge.target);
  return { nodes, edges };
}

test("layout is deterministic, bounded, and finite for the full 150-node cap", () => {
  const { nodes: smallNodes, edges: smallEdges } = fixture();
  const nodes = Array.from({ length: 150 }, (_, index) => memory(index));
  const edges = Array.from({ length: 450 }, (_, index) => ({
    source: nodes[index % nodes.length].nodeId,
    target: nodes[(index * 11 + 17) % nodes.length].nodeId,
    kind: "similarity" as const,
    weight: 0.35 + (index % 65) / 100,
  })).filter((edge) => edge.source !== edge.target);

  const started = performance.now();
  const first = layoutGraph(nodes, edges);
  const elapsed = performance.now() - started;
  const second = layoutGraph(nodes, edges);

  assert.equal(first.length, 150);
  assert.deepEqual(first, second);
  assert.ok(first.every((point) => (
    Number.isFinite(point.x) &&
    Number.isFinite(point.y) &&
    point.x >= 0 &&
    point.x <= GRAPH_WIDTH &&
    point.y >= 0 &&
    point.y <= GRAPH_HEIGHT
  )));
  assert.ok(elapsed < 500, `150-node layout took ${elapsed.toFixed(1)}ms`);
  console.info(`graph-layout 150 nodes / ${edges.length} edges: ${elapsed.toFixed(1)}ms`);
  assert.ok(smallNodes.length > 0 && smallEdges.length > 0);
});

test("neighbor highlighting follows only explicit relationship edges", () => {
  const { nodes, edges } = fixture(8);
  const map = neighborMap(edges);
  const first = nodes[0].nodeId;
  const expected = new Set(edges.flatMap((edge) => {
    if (edge.source === first) return [edge.target];
    if (edge.target === first) return [edge.source];
    return [];
  }));
  assert.deepEqual(map.get(first), expected);
  assert.equal(map.get("missing-node"), undefined);
});

test("zoom keeps the pointer anchor and reset returns to fit", () => {
  const fit = resetTransform();
  const zoomed = zoomAt(fit, { x: 200, y: 120 }, 1.8);
  assert.equal(zoomed.zoom, 1.8);
  assert.equal((200 - GRAPH_WIDTH / 2 - zoomed.panX) / zoomed.zoom, 200 - GRAPH_WIDTH / 2);
  assert.equal((120 - GRAPH_HEIGHT / 2 - zoomed.panY) / zoomed.zoom, 120 - GRAPH_HEIGHT / 2);
  assert.equal(resetTransform().zoom, 1);
  assert.equal(clampTransform({ yaw: 0, pitch: 0, zoom: 99, panX: 9999, panY: -9999 }, { width: GRAPH_WIDTH, height: GRAPH_HEIGHT }).zoom, MAX_ZOOM);
  assert.equal(clampTransform({ yaw: 0, pitch: 0, zoom: 0, panX: 0, panY: 0 }, { width: GRAPH_WIDTH, height: GRAPH_HEIGHT }).zoom, MIN_ZOOM);
});

test("dragging moves one point while respecting graph bounds", () => {
  const { nodes, edges } = fixture();
  const [point] = layoutGraph(nodes, edges);
  const moved = movePoint(point, { x: -100, y: 9999 });
  assert.equal(moved.nodeId, point.nodeId);
  assert.equal(moved.x, point.radius + 3);
  assert.equal(moved.y, GRAPH_HEIGHT - point.radius - 3);
  assert.notEqual(moved.x, point.x);
});
