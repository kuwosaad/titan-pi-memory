import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogInputSchema,
  catalogOutputSchema,
  detailInputSchema,
  detailOutputSchema,
  explorerContract,
  hostContract,
  searchInputSchema,
  snapshotInputSchema,
  snapshotOutputSchema,
} from "../src/contract.js";

const ids = { threadId: "thread_1", requestId: "request_1" };
const summary = {
  nodeId: "pi::memory_1",
  sourceAgent: "pi",
  memoryId: "memory_1",
  text: "A bounded memory preview",
  type: null,
  stream: "rough",
  timestamp: null,
  sessionId: null,
  hasEmbedding: false,
};

test("public explorer inputs preserve the required request identity fields", () => {
  assert.equal(catalogInputSchema.safeParse(ids).success, true);
  assert.equal(snapshotInputSchema.safeParse({ ...ids, filters: {} }).success, true);
  assert.equal(searchInputSchema.safeParse({ ...ids, filters: {}, cursor: "next" }).success, true);
  assert.equal(searchInputSchema.safeParse({ ...ids, filters: {}, page: 5 }).success, true);
  assert.equal(searchInputSchema.safeParse({ ...ids, filters: {}, page: 5, cursor: "next" }).success, false);
  assert.equal(
    detailInputSchema.safeParse({ ...ids, sourceAgent: "pi", memoryId: "memory_1" }).success,
    true,
  );

  assert.equal(snapshotInputSchema.safeParse({ ...ids, filters: {}, extra: true }).success, false);
  assert.equal(snapshotInputSchema.safeParse({ ...ids, filters: { agent: "../../secret" } }).success, false);
});

test("response schemas enforce the lightweight graph bounds", () => {
  const catalog = {
    agents: [{ id: "pi", count: 3 }, { id: "claude-code", count: 0 }],
    types: ["decision"],
    warnings: [],
  };
  assert.equal(catalogOutputSchema.safeParse(catalog).success, true);

  const snapshot = {
    nodes: [summary],
    edges: [],
    partial: false,
    warnings: [],
  };
  assert.equal(snapshotOutputSchema.safeParse(snapshot).success, true);
  assert.equal(
    snapshotOutputSchema.safeParse({ ...snapshot, edges: new Array(451).fill({}) }).success,
    false,
  );
  assert.equal(
    detailOutputSchema.safeParse({
      memory: summary,
      text: "detail",
      metadata: { turn: 1, scene_id: null },
      truncated: false,
    }).success,
    true,
  );
});

test("host contract keeps helper operations and request payloads discriminated", () => {
  const parsed = hostContract.execute.input.safeParse({
    command: "snapshot",
    request: { filters: {} },
  });
  assert.equal(parsed.success, true);
  assert.equal(
    hostContract.execute.input.safeParse({
      command: "detail",
      request: { filters: {} },
    }).success,
    false,
  );
  assert.equal(Object.keys(explorerContract).sort().join(","), "cancel,catalog,detail,search,snapshot");
});
