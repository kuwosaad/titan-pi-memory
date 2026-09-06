import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_GRAPH_EDGES,
  MAX_GRAPH_NODES,
  boundSnapshot,
  createRequestGate,
  connectedMemories,
  layoutGraph,
  mergeWarnings,
  pageTokens,
  shortText,
} from "../../src/ui/model.ts";

function memory(index: number) {
  return {
    nodeId: `agent-a:memory-${index}`,
    sourceAgent: "agent-a",
    memoryId: `memory-${index}`,
    text: `Memory ${index}`,
    type: index % 2 ? "decision" : null,
    stream: "learnings",
    timestamp: "2026-01-01T00:00:00Z",
    sessionId: "session-1",
    hasEmbedding: index % 3 !== 0,
  };
}

test("bounds graph nodes and edges without inventing source edges", () => {
  const nodes = Array.from({ length: MAX_GRAPH_NODES + 20 }, (_, index) => memory(index));
  const edges = Array.from({ length: MAX_GRAPH_EDGES + 20 }, (_, index) => ({
    source: nodes[index % nodes.length].nodeId,
    target: nodes[(index + 1) % nodes.length].nodeId,
    kind: "similarity" as const,
    weight: index / 1000,
  }));
  const bounded = boundSnapshot({ nodes, edges, partial: false, warnings: [] });

  assert.equal(bounded.nodes.length, MAX_GRAPH_NODES);
  assert.ok(bounded.edges.length <= MAX_GRAPH_EDGES);
  assert.ok(bounded.edges.length > 0);
  assert.equal(bounded.partial, true);
  assert.ok(bounded.edges.every((edge) => edge.kind === "similarity"));
});

test("layout is deterministic and finite for a bounded dataset", () => {
  const memories = [memory(1), memory(2), memory(3)];
  const edges = [{ source: memories[0].nodeId, target: memories[1].nodeId, kind: "similarity" as const, weight: 0.8 }];
  const first = layoutGraph(memories, edges);
  const second = layoutGraph(memories, edges);

  assert.deepEqual(first, second);
  assert.ok(first.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y)));
});

test("connections preserve collision-safe qualified node ids", () => {
  const first = memory(1);
  const second = memory(2);
  const snapshot = {
    nodes: [first, second],
    edges: [{ source: first.nodeId, target: second.nodeId, kind: "similarity" as const, weight: 0.72 }],
    partial: false,
    warnings: [],
  };

  const result = connectedMemories(first.nodeId, snapshot, [first, second]);
  assert.equal(result.length, 1);
  assert.equal(result[0].memory.nodeId, second.nodeId);
  assert.equal(result[0].weight, 0.72);
});

test("late responses become stale after a newer request or unmount", () => {
  const gate = createRequestGate();
  assert.equal(gate.activate("first"), null);
  assert.equal(gate.isCurrent("first"), true);
  assert.equal(gate.activate("second"), "first");
  assert.equal(gate.isCurrent("first"), false);
  assert.equal(gate.isCurrent("second"), true);
  assert.equal(gate.close(), "second");
  assert.equal(gate.isCurrent("second"), false);
  assert.equal(gate.activate("third"), null);
});

test("warnings are deduplicated and bounded", () => {
  const warnings = mergeWarnings(["same", "same"], Array.from({ length: 80 }, (_, index) => `warning-${index}`));
  assert.equal(warnings[0], "same");
  assert.equal(new Set(warnings).size, warnings.length);
  assert.equal(warnings.length, 64);
});

test("page tokens support compact numbered navigation and direct jumps", () => {
  assert.deepEqual(pageTokens(1, 10), [1, 2, 3, 4, 5, "…", 10]);
  assert.deepEqual(pageTokens(6, 10), [1, "…", 5, 6, 7, "…", 10]);
  assert.deepEqual(pageTokens(1, 4), [1, 2, 3, 4]);
});

test("long text is bounded for list/graph presentation", () => {
  const value = shortText("a".repeat(300));
  assert.equal(value.length, 240);
  assert.equal(value.endsWith("…"), true);
});
