import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import {
  AGENT_PALETTE,
  CORRECTION_COLOR,
  RequestSkippedError,
  SELECTION_COLOR,
  agentColors,
  createRequestGate,
  createRequestScheduler,
  inCorrectionCycle,
  isBusyError,
  shortText,
} from "../../src/ui/model.ts";

const busy = () => Object.assign(new Error("too many Titan explorer requests are running"), { code: "busy" });
const noWait = async () => undefined;

test("a closed gate can be reopened for a remounted panel", () => {
  const gate = createRequestGate();
  gate.close();
  assert.equal(gate.activate("ignored"), null);
  assert.equal(gate.isCurrent("ignored"), false);
  gate.reopen();
  gate.activate("fresh");
  assert.equal(gate.isCurrent("fresh"), true);
});

test("the scheduler keeps a panel to its request limit and drains the queue in order", async () => {
  const scheduler = createRequestScheduler(2, [], noWait);
  const releases: Array<() => void> = [];
  const started: string[] = [];
  const task = (name: string) => () => new Promise<string>((resolve) => {
    started.push(name);
    releases.push(() => resolve(name));
  });
  const results = Promise.all([scheduler.run(task("a")), scheduler.run(task("b")), scheduler.run(task("c"))]);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(started, ["a", "b"]);
  releases[0]();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(started, ["a", "b", "c"]);
  releases[1]();
  releases[2]();
  assert.deepEqual(await results, ["a", "b", "c"]);
});

test("busy rejections are retried with backoff; other errors are not", async () => {
  const waits: number[] = [];
  const scheduler = createRequestScheduler(2, [10, 20, 30], async (ms) => { waits.push(ms); });
  let attempts = 0;
  const value = await scheduler.run(async () => {
    attempts += 1;
    if (attempts < 3) throw busy();
    return "ok";
  });
  assert.equal(value, "ok");
  assert.deepEqual(waits, [10, 20]);

  let failures = 0;
  await assert.rejects(scheduler.run(async () => { failures += 1; throw new Error("backend down"); }), /backend down/);
  assert.equal(failures, 1);

  let exhausted = 0;
  await assert.rejects(scheduler.run(async () => { exhausted += 1; throw busy(); }), (error) => isBusyError(error));
  assert.equal(exhausted, 4);
});

test("queued work that is no longer wanted is skipped before it is sent", async () => {
  const scheduler = createRequestScheduler(1, [], noWait);
  let release!: () => void;
  const first = scheduler.run(() => new Promise<void>((resolve) => { release = resolve; }));
  let wanted = true;
  let sent = false;
  const second = scheduler.run(async () => { sent = true; }, () => wanted);
  wanted = false;
  await new Promise((resolve) => setTimeout(resolve, 0));
  release();
  await first;
  await assert.rejects(second, RequestSkippedError);
  assert.equal(sent, false);
});

test("busy errors are recognized by code or by the backend message", () => {
  assert.equal(isBusyError({ code: "busy" }), true);
  assert.equal(isBusyError(new Error("handler_error: too many Titan explorer requests are running")), true);
  assert.equal(isBusyError(new Error("backend down")), false);
  assert.equal(isBusyError(null), false);
});

test("agent colours are stable, distinct and never reuse the reserved colours", () => {
  const palette = AGENT_PALETTE.map((color) => color.toLowerCase());
  assert.equal(new Set(palette).size, palette.length);
  assert.equal(palette.includes(SELECTION_COLOR.toLowerCase()), false);
  assert.equal(palette.includes(CORRECTION_COLOR.toLowerCase()), false);

  const forward = agentColors(["pi", "codex", "claude", null]);
  const shuffled = agentColors(["claude", null, "pi", "codex", "pi"]);
  assert.deepEqual(Array.from(forward), Array.from(shuffled));
  assert.equal(forward.get("claude"), AGENT_PALETTE[0]);
  assert.ok(forward.has("local"));

  const six = agentColors(["codex", "claude", "pi", "grok", "hermes", "gemini"]);
  assert.equal(new Set(six.values()).size, 6);
});

// The browser graph lives in the same repository; when it is there, an agent
// must get the same colour on both surfaces.
const browserStyles = new URL("../../../../app/graph/ui/styles.css", import.meta.url);
test("the agent palette equals the browser graph's --agent tokens", { skip: !existsSync(browserStyles) && "browser graph not present" }, () => {
  const css = readFileSync(browserStyles, "utf8");
  const tokens = Array.from({ length: 8 }, (_, index) => {
    const match = css.match(new RegExp(`--agent-${index + 1}:\\s*(#[0-9a-fA-F]{6})`));
    return match?.[1]?.toUpperCase();
  });
  assert.deepEqual(tokens, AGENT_PALETTE.map((color) => color.toUpperCase()));
});

test("previews truncate by grapheme so emoji are never split", () => {
  const value = shortText(`${"a".repeat(40)}😀 and more text`, 42);
  assert.equal(value, `${"a".repeat(40)}😀…`);
  assert.doesNotMatch(value, /[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);

  const family = "👨‍👩‍👧‍👦";
  const truncated = shortText(`${family.repeat(5)} tail`, 3);
  assert.equal(truncated, `${family}${family}…`);
});

test("correction cycles are found at any length, and only through the memory asked about", () => {
  const edge = (source: string, target: string) => ({ source, target });
  assert.equal(inCorrectionCycle("a", [edge("a", "b"), edge("b", "a")]), true);
  assert.equal(inCorrectionCycle("a", [edge("a", "b"), edge("b", "c"), edge("c", "d"), edge("d", "a")]), true);
  assert.equal(inCorrectionCycle("a", [edge("a", "b"), edge("b", "c"), edge("a", "c")]), false);
  // A cycle elsewhere in the graph is not this memory's conflict.
  assert.equal(inCorrectionCycle("a", [edge("a", "b"), edge("b", "c"), edge("c", "b")]), false);
  assert.equal(inCorrectionCycle("a", []), false);
});
