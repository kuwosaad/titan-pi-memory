import { expect, test } from "bun:test"
import { mkdtempSync, readdirSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { V2Capture } from "../src/v2-capture"
import { namespaceEnvironment } from "../src/namespace"

test("modern capture writes terminal user, tool and assistant content and excludes recalled text", () => {
  const capture = new V2Capture()
  const spool = mkdtempSync(join(tmpdir(), "titan-opencode-v2-"))
  const previous = process.env.TITAN_SPOOL_DIR
  process.env.TITAN_SPOOL_DIR = spool
  let sequence = 0
  const emit = (type: string, data: object) => capture.accept({
    id: "record-" + ++sequence, type, created: 123456, data: { sessionID: "session", assistantMessageID: "a", ...data },
  } as Parameters<V2Capture["accept"]>[0])
  try {
    emit("session.inbox.enqueued", { item: { type: "user", payload: { text: "remember" } }, inboxID: "user" })
    emit("session.tool.input.started", { id: "lookup", name: "titan-memory_query_memories" })
    emit("session.tool.success", { id: "lookup", content: [{ type: "text", text: "recalled private fact" }] })
    emit("session.tool.input.started", { id: "shell", name: "bash" })
    emit("session.tool.called", { id: "shell", input: { token: "private" } })
    emit("session.tool.success", { id: "shell", content: [{ type: "text", text: "done" }] })
    emit("session.text.ended", { ordinal: 0, text: "completed" })
    emit("session.step.ended", { finish: "stop" })
    emit("session.execution.succeeded", {})
    const events = readdirSync(spool).sort().flatMap(path => JSON.parse(readFileSync(join(spool, path), "utf8")))
    expect(events.map(e => e.event_type)).toEqual(["user_message", "tool_execution", "assistant_message", "session_idle"])
    expect(JSON.stringify(events)).not.toContain("private")
    expect(events[1].payload.args.token).toBe("[REDACTED]")
    expect(events[0].ts).toBe(new Date(123456).toISOString())
  } finally {
    if (previous === undefined) delete process.env.TITAN_SPOOL_DIR
    else process.env.TITAN_SPOOL_DIR = previous
  }
})

test("foreign Titan process configuration cannot select a foreign store", () => {
  const previous = { ...process.env }
  try {
    process.env.TITAN_AGENT_NAME = "claude-code"
    process.env.TITAN_HOME = "/tmp/shared/agents/claude-code"
    process.env.TITAN_SPOOL_DIR = "/tmp/other"
    delete process.env.TITAN_SHARED_HOME
    const env = namespaceEnvironment()
    expect(env.TITAN_HOME).toBe("/tmp/shared/agents/opencode")
    expect(env.TITAN_SPOOL_DIR).toBe("/tmp/shared/agents/opencode/traces")
  } finally {
    for (const key of ["TITAN_AGENT_NAME", "TITAN_HOME", "TITAN_SPOOL_DIR", "TITAN_SHARED_HOME"]) {
      if (previous[key] === undefined) delete process.env[key]
      else process.env[key] = previous[key]
    }
  }
})
