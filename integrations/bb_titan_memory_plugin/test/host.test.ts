import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import type { ExperimentalHostRpcContext } from "@get-bb/plugin-sdk/host";
import { __testing, explorerHostEntry } from "../src/host.js";
import { detailOutputSchema, snapshotOutputSchema } from "../src/contract.js";
import type { HostExecuteInput } from "../src/contract.js";

function context(signal: AbortSignal): ExperimentalHostRpcContext {
  return {
    signal,
    lifecycle: { signal },
    experimental_paths: { dataDir: "/tmp/titan-explorer-test", tempDir: "/tmp" },
    experimental_emitSignal: async () => undefined,
    experimental_watch: async () => ({ dispose: async () => undefined }),
    experimental_retainWorker: () => ({ dispose: async () => undefined }),
  };
}

async function withHostEnvironment(root: string, home: string, callback: () => Promise<void>): Promise<void> {
  const previous = {
    root: process.env.TITAN_ROOT,
    home: process.env.TITAN_HOME,
    sharedHome: process.env.TITAN_SHARED_HOME,
    python: process.env.TITAN_PYTHON,
  };
  process.env.TITAN_ROOT = root;
  process.env.TITAN_HOME = join(home, "agents", "pi");
  delete process.env.TITAN_SHARED_HOME;
  process.env.TITAN_PYTHON = "python3";
  try {
    await callback();
  } finally {
    if (previous.root === undefined) delete process.env.TITAN_ROOT;
    else process.env.TITAN_ROOT = previous.root;
    if (previous.home === undefined) delete process.env.TITAN_HOME;
    else process.env.TITAN_HOME = previous.home;
    if (previous.sharedHome === undefined) delete process.env.TITAN_SHARED_HOME;
    else process.env.TITAN_SHARED_HOME = previous.sharedHome;
    if (previous.python === undefined) delete process.env.TITAN_PYTHON;
    else process.env.TITAN_PYTHON = previous.python;
    __testing.resetForTests();
  }
}

async function waitForFile(path: string): Promise<string> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return await readFile(path, "utf8");
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
  throw new Error(`timed out waiting for ${path}`);
}

async function waitForHostIdle(): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (__testing.activeCount() === 0) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error("host child did not exit");
}

async function waitForPidExit(pid: number): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      process.kill(pid, 0);
    } catch {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`helper pid ${pid} is still alive`);
}

async function makeHelper(source: string): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "titan-explorer-python-"));
  await mkdir(join(root, "app", "graph"), { recursive: true });
  await writeFile(join(root, "app", "graph", "explorer.py"), source, "utf8");
  return root;
}

const execute = explorerHostEntry.handlers.execute;

test("host sends the method-specific JSON request to one Python helper", async () => {
  const home = await mkdtemp(join(tmpdir(), "titan-explorer-home-"));
  const root = await makeHelper(`
import json
import sys
payload = json.load(sys.stdin)
print(json.dumps({"payload": payload, "args": sys.argv[1:]}))
`);
  try {
    await withHostEnvironment(root, home, async () => {
      const signal = new AbortController().signal;
      const input: HostExecuteInput = { command: "snapshot", request: { filters: {} } };
      const result = await execute(input, context(signal));
      assert.equal(result.exitCode, 0);
      const output = JSON.parse(result.stdout);
      assert.deepEqual(output.payload, { method: "snapshot", filters: {} });
      assert.deepEqual(output.args, ["--home", home]);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(home, { recursive: true, force: true });
  }
});

test("the real Python bridge returns renderable correction and scene data", async () => {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const home = await mkdtemp(join(tmpdir(), "titan-explorer-evidence-"));
  const store = join(home, "agents", "pi", "out", "memories");
  const previousText = "🗓️".repeat(121);
  const evidence = "The release is now Tuesday.";
  try {
    await mkdir(store, { recursive: true });
    await Promise.all([
      writeFile(join(store, "memories.json"), JSON.stringify([
        { id: "old", text: previousText, ts: "2026-11-22T00:00:00Z" },
        { id: "new", text: "Release Tuesday", ts: "2026-11-23T00:00:00Z", scene_id: "scene" },
      ])),
      writeFile(join(store, "memories.json.relations.json"), JSON.stringify([
        { source: "pi", old_id: "old", new_id: "new", evidence, created_at: "2026-11-23T00:00:00Z" },
      ])),
      writeFile(join(store, "scenes.json"), JSON.stringify([
        { scene_id: "scene", evidence_status: "complete", evidence_version: 1, extraction_user_text: evidence, source_event_ids: ["event"] },
      ])),
    ]);
    await withHostEnvironment(root, home, async () => {
      const host = context(new AbortController().signal);
      const snapshot = await execute({ command: "snapshot", request: { filters: { agent: "pi" } } }, host);
      assert.equal(snapshot.exitCode, 0, snapshot.stderr);
      const graph = snapshotOutputSchema.parse(JSON.parse(snapshot.stdout));
      assert.deepEqual(graph.edges, [{ source: "pi::new", target: "pi::old", kind: "supersedes", weight: 1 }]);

      const response = await execute({ command: "detail", request: { sourceAgent: "pi", memoryId: "new" } }, host);
      assert.equal(response.exitCode, 0, response.stderr);
      const detail = detailOutputSchema.parse(JSON.parse(response.stdout));
      assert.equal(detail.corrections?.[0]?.memory?.nodeId, "pi::old");
      assert.equal(detail.corrections?.[0]?.evidence, evidence);
      assert.equal(detail.scene?.userText, evidence);
      assert.deepEqual(detail.scene?.eventIds, ["event"]);
    });
  } finally {
    await rm(home, { recursive: true, force: true });
  }
});

test("host enforces helper stdout limits in UTF-8 bytes", async () => {
  const home = await mkdtemp(join(tmpdir(), "titan-explorer-home-"));
  const root = await makeHelper(`
print("é" * 245760)
`);
  try {
    await withHostEnvironment(root, home, async () => {
      await assert.rejects(
        Promise.resolve(execute(
          { command: "snapshot", request: { filters: {} } },
          context(new AbortController().signal),
        )),
        (error: unknown) => error instanceof Error && "code" in error && error.code === "output_too_large",
      );
    });
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(home, { recursive: true, force: true });
  }
});

test("host cancellation escalates past SIGTERM and frees the slot after close", async () => {
  const home = await mkdtemp(join(tmpdir(), "titan-explorer-home-"));
  const root = await makeHelper(`
import os
import pathlib
import signal
import sys
import time
pathlib.Path(os.environ["TITAN_TEST_PID_FILE"]).write_text(str(os.getpid()))
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdin.read()
time.sleep(10)
`);
  const pidFile = join(home, "helper.pid");
  const previousPidFile = process.env.TITAN_TEST_PID_FILE;
  process.env.TITAN_TEST_PID_FILE = pidFile;
  try {
    await withHostEnvironment(root, home, async () => {
      const controller = new AbortController();
      const pending = Promise.resolve(execute(
        { command: "snapshot", request: { filters: {} } },
        context(controller.signal),
      ));
      const pid = Number(await waitForFile(pidFile));
      assert.equal(__testing.activeCount(), 1);
      controller.abort();
      await assert.rejects(pending, (error: unknown) => {
        return error instanceof Error && "code" in error && error.code === "cancelled";
      });
      // The request is rejected before the child closes, so its slot remains
      // occupied while the TERM->KILL escalation completes.
      assert.equal(__testing.activeCount(), 1);
      await waitForHostIdle();
      await waitForPidExit(pid);
    });
  } finally {
    if (previousPidFile === undefined) delete process.env.TITAN_TEST_PID_FILE;
    else process.env.TITAN_TEST_PID_FILE = previousPidFile;
    await rm(root, { recursive: true, force: true });
    await rm(home, { recursive: true, force: true });
  }
});

test("host cancellation terminates an obsolete helper request", async () => {
  const home = await mkdtemp(join(tmpdir(), "titan-explorer-home-"));
  const root = await makeHelper(`
import time
import sys
sys.stdin.read()
time.sleep(10)
`);
  try {
    await withHostEnvironment(root, home, async () => {
      const controller = new AbortController();
      const pending = Promise.resolve(execute(
        { command: "search", request: { filters: {}, cursor: "next" } },
        context(controller.signal),
      ));
      setTimeout(() => controller.abort(), 25);
      await assert.rejects(pending, (error: unknown) => {
        return error instanceof Error && "code" in error && error.code === "cancelled";
      });
    });
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(home, { recursive: true, force: true });
  }
});
