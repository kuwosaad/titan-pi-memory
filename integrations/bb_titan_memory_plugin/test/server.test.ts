import assert from "node:assert/strict";
import test from "node:test";
import { createFakePluginHost } from "@get-bb/plugin-sdk/testing";
import plugin from "../src/server.js";

const snapshot = {
  nodes: [
    {
      nodeId: "pi::memory_1",
      sourceAgent: "pi",
      memoryId: "memory_1",
      text: "preview",
      type: null,
      stream: "rough",
      timestamp: null,
      sessionId: null,
      hasEmbedding: false,
    },
  ],
  edges: [],
  partial: false,
  warnings: [],
};

function hostResult(value: unknown) {
  return {
    stdout: JSON.stringify(value),
    stderr: "",
    exitCode: 0,
    signal: null,
  };
}

function sdkStubs() {
  return {
    threads: {
      get: async () => ({ environmentId: "environment_1" }),
    },
    environments: {
      get: async () => ({ hostId: "host_1" }),
    },
  };
}

test("server registers the explorer RPC methods and routes to the thread host", async () => {
  const calls: Array<{ method: string; input: unknown; hostId: string }> = [];
  const { bb, harness } = createFakePluginHost({
    pluginId: "bb-plugin-titan-memory",
    sdk: sdkStubs(),
    settings: {
      titanRoot: "/configured/titan",
      titanPython: "python-custom",
      sharedHome: "/configured/.titan",
    },
    experimental_callHostRpc: async (call) => {
      calls.push({ method: call.method, input: call.input, hostId: call.hostId });
      return hostResult(snapshot);
    },
  });
  await plugin(bb);

  assert.deepEqual(harness.inspection.registrations.rpcMethods.sort(), ["cancel", "catalog", "detail", "search", "snapshot"]);
  const result = await harness.behavior.callRpc("snapshot", {
    threadId: "thread_1",
    requestId: "request_1",
    filters: { agent: "pi" },
  });
  assert.deepEqual(result, snapshot);
  assert.deepEqual(calls, [
    {
      method: "execute",
      hostId: "host_1",
      input: {
        command: "snapshot",
        request: { filters: { agent: "pi" } },
        runtime: {
          titanRoot: "/configured/titan",
          titanPython: "python-custom",
          sharedHome: "/configured/.titan",
        },
      },
    },
  ]);

  await harness.behavior.setSettings({
    titanRoot: "/updated/titan",
    titanPython: "python-updated",
    sharedHome: "/updated/.titan",
  });
  await harness.behavior.callRpc("snapshot", {
    threadId: "thread_1",
    requestId: "request_2",
    filters: {},
  });
  assert.deepEqual(calls[1]?.input, {
    command: "snapshot",
    request: { filters: {} },
    runtime: {
      titanRoot: "/updated/titan",
      titanPython: "python-updated",
      sharedHome: "/updated/.titan",
    },
  });
  await harness.lifecycle.dispose();
});

test("server forwards catalog and direct page search without undefined fields", async () => {
  const calls: unknown[] = [];
  const { bb, harness } = createFakePluginHost({
    pluginId: "bb-plugin-titan-memory",
    sdk: sdkStubs(),
    experimental_callHostRpc: async (call) => {
      calls.push(call.input);
      const input = call.input as { command?: string };
      if (input.command === "catalog") {
        return hostResult({ agents: [{ id: "pi", count: 1 }], types: ["decision"], warnings: [] });
      }
      return hostResult({ items: [], nextCursor: null, warnings: [], page: 5, totalItems: 0, totalPages: 0 });
    },
  });
  await plugin(bb);

  assert.deepEqual(
    await harness.behavior.callRpc("catalog", { threadId: "thread_1", requestId: "catalog_1" }),
    { agents: [{ id: "pi", count: 1 }], types: ["decision"], warnings: [] },
  );
  await harness.behavior.callRpc("search", {
    threadId: "thread_1",
    requestId: "page_5",
    filters: { agent: "pi" },
    page: 5,
  });
  assert.deepEqual(calls, [
    { command: "catalog", request: {} },
    { command: "search", request: { filters: { agent: "pi" }, page: 5 } },
  ]);
  await harness.lifecycle.dispose();
});

test("simultaneous requests for one thread remain independent", async () => {
  let callCount = 0;
  let releaseFirst!: () => void;
  let firstStarted!: () => void;
  const firstStartedPromise = new Promise<void>((resolve) => { firstStarted = resolve; });
  const firstReleasePromise = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const { bb, harness } = createFakePluginHost({
    pluginId: "bb-plugin-titan-memory",
    sdk: sdkStubs(),
    experimental_callHostRpc: async (call) => {
      callCount += 1;
      if (callCount === 1) {
        firstStarted();
        await firstReleasePromise;
      }
      const input = call.input as { command?: string };
      return hostResult(input.command === "search"
        ? { items: snapshot.nodes, nextCursor: null, warnings: [] }
        : snapshot);
    },
  });
  await plugin(bb);

  const first = harness.behavior.callRpc("snapshot", {
    threadId: "thread_1",
    requestId: "request_graph",
    filters: {},
  });
  await firstStartedPromise;
  const second = harness.behavior.callRpc("search", {
    threadId: "thread_1",
    requestId: "request_list",
    filters: {},
  });

  assert.deepEqual(await second, { items: snapshot.nodes, nextCursor: null, warnings: [] });
  releaseFirst();
  assert.deepEqual(await first, snapshot);
  assert.equal(callCount, 2);
  await harness.lifecycle.dispose();
});

test("cancel targets exactly one request and aborts its host signal", async () => {
  let hostStarted!: () => void;
  let aborted = false;
  const hostStartedPromise = new Promise<void>((resolve) => { hostStarted = resolve; });
  const { bb, harness } = createFakePluginHost({
    pluginId: "bb-plugin-titan-memory",
    sdk: sdkStubs(),
    experimental_callHostRpc: async (call) => {
      hostStarted();
      await new Promise<never>((_resolve, reject) => {
        call.signal?.addEventListener("abort", () => {
          aborted = true;
          reject(Object.assign(new Error("cancelled"), { code: "cancelled" }));
        }, { once: true });
      });
      return hostResult(snapshot);
    },
  });
  await plugin(bb);

  const pending = harness.behavior.callRpc("detail", {
    threadId: "thread_1",
    requestId: "request_detail",
    sourceAgent: "pi",
    memoryId: "memory_1",
  });
  await hostStartedPromise;
  assert.deepEqual(
    await harness.behavior.callRpc("cancel", { threadId: "thread_1", requestId: "request_detail" }),
    { cancelled: true },
  );
  await assert.rejects(pending);
  assert.equal(aborted, true);
  assert.deepEqual(
    await harness.behavior.callRpc("cancel", { threadId: "thread_1", requestId: "request_detail" }),
    { cancelled: false },
  );
  await harness.lifecycle.dispose();
});

test("structured Python failures become bounded RPC failures", async () => {
  const { bb, harness } = createFakePluginHost({
    pluginId: "bb-plugin-titan-memory",
    sdk: sdkStubs(),
    experimental_callHostRpc: async () => hostResult({
      error: { code: "database_busy", message: "memory store is busy" },
    }),
  });
  await plugin(bb);

  await assert.rejects(
    harness.behavior.callRpc("detail", {
      threadId: "thread_1",
      requestId: "request_1",
      sourceAgent: "pi",
      memoryId: "memory_1",
    }),
    (error: unknown) => error instanceof Error && error.message.includes("memory store is busy"),
  );
  await harness.lifecycle.dispose();
});
