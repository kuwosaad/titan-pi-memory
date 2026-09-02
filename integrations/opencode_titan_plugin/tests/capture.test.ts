import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { mkdtemp, readdir, readFile, stat } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { TitanMemoryPlugin } from "../src/index"

type MessageRecord = {
  info: Record<string, unknown>
  parts: Array<Record<string, unknown>>
}

const sessions = new Map<string, MessageRecord[]>()
let spoolDir = ""

function userMessage(id: string, text: string, sessionID = "session-1"): MessageRecord {
  return {
    info: { id, sessionID, role: "user", time: { created: 1 } },
    parts: [{ id: `${id}-part`, sessionID, messageID: id, type: "text", text }],
  }
}

function assistantMessage(id: string, text: string, finish = "stop"): MessageRecord {
  return {
    info: {
      id,
      sessionID: "session-1",
      role: "assistant",
      parentID: "user-1",
      finish,
      time: { created: 2, completed: 3 },
    },
    parts: [{ id: `${id}-part`, sessionID: "session-1", messageID: id, type: "text", text }],
  }
}

function toolMessage(id: string, tool: string, status: "completed" | "error" = "completed"): MessageRecord {
  return {
    info: {
      id,
      sessionID: "session-1",
      role: "assistant",
      parentID: "user-1",
      finish: "tool-calls",
      time: { created: 2 },
    },
    parts: [{
      id: `${id}-tool-part`,
      sessionID: "session-1",
      messageID: id,
      type: "tool",
      callID: `${id}-call`,
      tool,
      state: status === "completed"
        ? { status, input: { command: "printf hello" }, output: "hello".repeat(1000) }
        : { status, input: { command: "false" }, error: "command failed" },
    }],
  }
}

function makeClient() {
  return {
    session: {
      messages: async ({ path, query }: { path: { id: string }; query: { directory: string } }) => {
        expect(query.directory).toBe("/workspace")
        return { data: sessions.get(path.id) ?? [], error: undefined }
      },
    },
  }
}

async function waitForSpool(): Promise<string[]> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const files = (await readdir(spoolDir)).filter((name) => name.endsWith(".jsonl"))
    if (files.length > 0) return files
    await Bun.sleep(2)
  }
  return []
}

async function waitForFileCount(count: number): Promise<string[]> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const files = (await readdir(spoolDir)).filter((name) => name.endsWith(".jsonl")).sort()
    if (files.length >= count) return files
    await Bun.sleep(2)
  }
  return []
}

async function readEvents(): Promise<Record<string, unknown>[]> {
  const files = await waitForSpool()
  if (files.length === 0) return []
  const content = await readFile(join(spoolDir, files[0]), "utf8")
  return content
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Record<string, unknown>)
}

async function readAllEvents(): Promise<Record<string, unknown>[]> {
  const files = (await readdir(spoolDir)).filter((name) => name.endsWith(".jsonl")).sort()
  const events: Record<string, unknown>[] = []
  for (const file of files) {
    const content = await readFile(join(spoolDir, file), "utf8")
    events.push(...content.split("\n").filter(Boolean).map((line) => JSON.parse(line) as Record<string, unknown>))
  }
  return events
}

beforeEach(async () => {
  spoolDir = await mkdtemp(join(tmpdir(), "titan-opencode-test-"))
  process.env.TITAN_SPOOL_DIR = spoolDir
  sessions.clear()
})

afterEach(() => {
  delete process.env.TITAN_SPOOL_DIR
  delete process.env.TITAN_HOME
  delete process.env.TITAN_AGENT_NAME
})

describe("TitanMemoryPlugin capture hook", () => {
  test("captures persisted user and completed assistant messages", async () => {
    sessions.set("session-1", [userMessage("user-1", "remember this"), assistantMessage("assistant-1", "I will")])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.updated", properties: { info: sessions.get("session-1")![1].info } } } as never)
    const events = await readEvents()

    expect(events.map((event) => event.event_type)).toEqual(["user_message", "assistant_message"])
    expect(events[0]).toMatchObject({
      session_id: "session-1",
      event_id: "opencode:message:user-1:user",
      payload: { source: "opencode", message_id: "user-1", content: "remember this" },
    })
    expect(events[1]).toMatchObject({
      event_id: "opencode:message:assistant-1:assistant",
      payload: { message_id: "assistant-1", content: "I will" },
    })
  })

  test("does not capture an unfinished or tool-calling assistant", async () => {
    const unfinished = assistantMessage("assistant-1", "partial text", "stop")
    unfinished.info.time = { created: 2 }
    const toolCalling = assistantMessage("assistant-2", "working", "tool-calls")
    sessions.set("session-1", [unfinished, toolCalling])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.updated", properties: { info: { id: "assistant-1", sessionID: "session-1", role: "assistant" } } } } as never)
    await Bun.sleep(5)
    const files = (await readdir(spoolDir)).filter((name) => name.endsWith(".jsonl"))
    expect(files).toHaveLength(0)
  })

  test("writes private immutable batches using a hashed session filename", async () => {
    sessions.set("secret/session", [userMessage("user-1", "hello", "secret/session")])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.updated", properties: { info: { id: "user-1", sessionID: "secret/session", role: "user" } } } } as never)
    const files = await waitForSpool()
    expect(files[0]).toMatch(/^opencode-[a-f0-9]{16}-\d{13}-\d{6}-[0-9a-f-]+\.jsonl$/)
    expect((await stat(spoolDir)).mode & 0o777).toBe(0o700)
    expect((await stat(join(spoolDir, files[0]))).mode & 0o777).toBe(0o600)
    expect((await readdir(spoolDir)).filter((name) => name.endsWith(".tmp"))).toHaveLength(0)
  })

  test("treats TITAN_HOME as the active agent workspace", async () => {
    delete process.env.TITAN_SPOOL_DIR
    const agentHome = await mkdtemp(join(tmpdir(), "titan-opencode-agent-home-"))
    process.env.TITAN_HOME = agentHome
    sessions.set("session-1", [userMessage("user-1", "hello")])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.updated", properties: { info: { id: "user-1", sessionID: "session-1", role: "user" } } } } as never)
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const traceDir = join(agentHome, "traces")
      const files = await readdir(traceDir).catch(() => [])
      if (files.some((name) => name.endsWith(".jsonl"))) break
      await Bun.sleep(2)
    }

    expect((await readdir(join(agentHome, "traces"))).some((name) => name.endsWith(".jsonl"))).toBe(true)
    expect(await readdir(join(agentHome, "agents")).catch(() => [])).toHaveLength(0)
  })

  test("captures terminal tools, compacts output, and excludes every Titan MCP namespace", async () => {
    sessions.set("session-1", [
      userMessage("user-1", "inspect it"),
      toolMessage("assistant-1", "bash"),
      toolMessage("assistant-2", "mcp__titan-memory_recall"),
      toolMessage("assistant-3", "mcp.titan_memory_search", "error"),
    ])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.part.updated", properties: { part: { sessionID: "session-1", type: "tool", state: { status: "completed" } } } } } as never)
    const events = await readEvents()
    expect(events).toHaveLength(2)
    expect(events[1]).toMatchObject({
      event_type: "tool_execution",
      event_id: "opencode:tool:assistant-1-tool-part:assistant-1-call",
      payload: { tool: "bash", call_id: "assistant-1-call", status: "success", raw_type: "message.part.updated" },
    })
    expect(String((events[1].payload as Record<string, unknown>).output).length).toBeLessThanOrEqual(1200)
    expect(JSON.stringify(events)).not.toContain("titan-memory")
  })

  test("redacts secrets before they reach the spool", async () => {
    sessions.set("session-1", [
      userMessage("user-1", "OPENAI_API_KEY=sk-thismustnotpersist123456789 ntn_abcdefghijklmnop secret_abcdefghijklmnop eyJabcdefghijklmnop.abcdefghijklmnop.abcdefghijklmnop Authorization: Basic dXNlcjpwYXNz"),
      toolMessage("assistant-1", "bash"),
    ])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.updated", properties: { info: { id: "user-1", sessionID: "session-1", role: "user" } } } } as never)
    const events = await readEvents()
    const rendered = JSON.stringify(events)
    expect(rendered).toContain("[REDACTED]")
    expect(rendered).not.toContain("thismustnotpersist")
    expect(rendered).not.toContain("abcdefghijklmnop")
    expect(rendered).not.toContain("dXNlcjpwYXNz")
  })

  test("emits one stable idle boundary anchored to the latest stable message", async () => {
    sessions.set("session-1", [userMessage("user-1", "hello"), assistantMessage("assistant-1", "done")])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)
    const idle = { type: "session.idle", properties: { sessionID: "session-1" } }

    await hooks.event?.({ event: idle } as never)
    await hooks.event?.({ event: idle } as never)
    const events = await readEvents()
    expect(events.map((event) => event.event_id)).toContain("opencode:idle:assistant-1")
    expect(events.filter((event) => event.event_type === "session_idle")).toHaveLength(1)
  })

  test("coalesces concurrent triggers into one transcript write", async () => {
    sessions.set("session-1", [userMessage("user-1", "hello"), assistantMessage("assistant-1", "done")])
    let calls = 0
    const client = makeClient()
    client.session.messages = async (options) => {
      calls += 1
      await Bun.sleep(10)
      return { data: sessions.get(options.path.id) ?? [], error: undefined }
    }
    const hooks = await TitanMemoryPlugin({ client: client as never, directory: "/workspace" } as never)

    await Promise.all([
      hooks.event?.({ event: { type: "message.updated", properties: { info: { id: "user-1", sessionID: "session-1", role: "user" } } } } as never),
      hooks.event?.({ event: { type: "message.part.updated", properties: { part: { sessionID: "session-1", type: "tool", state: { status: "running" } } } } } as never),
      hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "session-1" } } } as never),
    ])
    const events = await readEvents()
    expect(calls).toBe(1)
    expect(events.filter((event) => event.event_id === "opencode:message:user-1:user")).toHaveLength(1)
    expect(events.filter((event) => event.event_id === "opencode:idle:assistant-1")).toHaveLength(1)
  })

  test("ignores streaming assistant and non-terminal tool updates", async () => {
    let calls = 0
    const client = makeClient()
    client.session.messages = async () => {
      calls += 1
      return { data: [], error: undefined }
    }
    const hooks = await TitanMemoryPlugin({ client: client as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.updated", properties: { info: { id: "assistant-1", sessionID: "session-1", role: "assistant", finish: "stop", time: { created: 1 } } } } } as never)
    await hooks.event?.({ event: { type: "message.part.updated", properties: { part: { id: "part-1", sessionID: "session-1", type: "text", text: "partial" } } } } as never)
    await hooks.event?.({
      event: {
        type: "message.part.updated",
        properties: {
          part: { id: "tool-1", sessionID: "session-1", type: "tool", state: { status: "running" } },
        },
      },
    } as never)
    await Bun.sleep(5)

    expect(calls).toBe(0)
    expect((await readdir(spoolDir)).filter((name) => name.endsWith(".jsonl"))).toHaveLength(0)
  })

  test("captures one canonical user, tool, assistant, and idle sequence", async () => {
    const user = userMessage("user-1", "inspect it")
    const tool = toolMessage("assistant-tool", "bash")
    const assistant = assistantMessage("assistant-final", "inspection complete")
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    sessions.set("session-1", [user])
    await hooks.event?.({ event: { type: "message.updated", properties: { info: user.info } } } as never)
    await waitForFileCount(1)

    sessions.set("session-1", [user, tool])
    await hooks.event?.({ event: { type: "message.part.updated", properties: { part: tool.parts[0] } } } as never)
    await waitForFileCount(2)

    sessions.set("session-1", [user, tool, assistant])
    await hooks.event?.({ event: { type: "message.updated", properties: { info: assistant.info } } } as never)
    await waitForFileCount(3)

    await hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "session-1" } } } as never)
    await waitForFileCount(4)

    const events = await readAllEvents()
    expect(events.map((event) => event.event_type)).toEqual([
      "user_message",
      "tool_execution",
      "assistant_message",
      "session_idle",
    ])
    expect(new Set(events.map((event) => event.event_id)).size).toBe(4)
  })

  test("replays with stable IDs after a fresh plugin instance", async () => {
    sessions.set("session-1", [userMessage("user-1", "hello"), assistantMessage("assistant-1", "done")])
    const event = { event: { type: "session.idle", properties: { sessionID: "session-1" } } }
    const first = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)
    await first.event?.(event as never)
    const firstEvents = await readEvents()

    const second = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)
    await second.event?.(event as never)
    const allEvents = await readAllEvents()
    expect(allEvents).toHaveLength(firstEvents.length * 2)
    expect(allEvents.map((item) => item.event_id)).toEqual([...firstEvents, ...firstEvents].map((item) => item.event_id))
  })

  test("swallows SDK failures so the event callback never rejects", async () => {
    const client = makeClient()
    client.session.messages = async () => {
      throw new Error("temporary SDK failure")
    }
    const hooks = await TitanMemoryPlugin({ client: client as never, directory: "/workspace" } as never)
    await expect(hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "session-1" } } } as never)).resolves.toBeUndefined()
    await Bun.sleep(100)
    expect((await readdir(spoolDir)).filter((name) => name.endsWith(".jsonl"))).toHaveLength(0)
  })

  test("retries a transient final idle synchronization failure", async () => {
    sessions.set("session-1", [userMessage("user-1", "hello"), assistantMessage("assistant-1", "done")])
    let calls = 0
    const client = makeClient()
    client.session.messages = async (options) => {
      calls += 1
      if (calls === 1) throw new Error("temporary SDK failure")
      return { data: sessions.get(options.path.id) ?? [], error: undefined }
    }
    const hooks = await TitanMemoryPlugin({ client: client as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "session.idle", properties: { sessionID: "session-1" } } } as never)
    await waitForSpool()

    expect(calls).toBe(2)
    expect((await readEvents()).map((event) => event.event_type)).toContain("session_idle")
  })

  test("bounds serialized tool arguments while retaining small argument objects", async () => {
    const record = toolMessage("assistant-1", "bash")
    record.parts[0].state = {
      status: "completed",
      input: { command: "x".repeat(10000) },
      output: "ok",
    }
    sessions.set("session-1", [record])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.part.updated", properties: { part: { sessionID: "session-1", type: "tool", state: { status: "completed" } } } } } as never)
    const events = await readEvents()
    const args = (events[0].payload as Record<string, unknown>).args
    expect(JSON.stringify(args).length).toBeLessThanOrEqual(4000)
    expect(JSON.stringify(args)).not.toContain("x".repeat(10000))
  })

  test("redacts exact and camel-case credential keys in tool arguments", async () => {
    const record = toolMessage("assistant-1", "bash")
    record.parts[0].state = {
      status: "completed",
      input: {
        key: "SUPERSECRET123456",
        clientKey: "CLIENTSECRET123456",
        privateKey: "PRIVATESECRET123456",
        accessKey: "ACCESSSECRET123456",
        keyboard: "allowed-value",
      },
      output: "ok",
    }
    sessions.set("session-1", [record])
    const hooks = await TitanMemoryPlugin({ client: makeClient() as never, directory: "/workspace" } as never)

    await hooks.event?.({ event: { type: "message.part.updated", properties: { part: record.parts[0] } } } as never)
    const rendered = JSON.stringify(await readEvents())

    expect(rendered).not.toContain("SUPERSECRET")
    expect(rendered).toContain("allowed-value")
  })
})
