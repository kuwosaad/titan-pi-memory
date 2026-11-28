import type { Plugin } from "@opencode/plugin"
import { boundedArgs, compact, eventBase, isTitanTool, redact, writeBatch } from "./capture"

export const captureTypes = [
  "session.inbox.enqueued", "session.text.ended", "session.step.ended", "session.step.failed",
  "session.tool.input.started", "session.tool.called", "session.tool.success", "session.tool.failed",
  "session.execution.succeeded", "session.execution.failed", "session.execution.interrupted", "session.deleted",
] as const

type Event = ReturnType<Plugin.Context["event"]["subscribe"]> extends AsyncIterable<infer Item> ? Item : never
type PendingStep = { text: Map<number, string>; tools: Map<string, { name: string; args?: unknown }> }

export class V2Capture {
  private readonly sessions = new Map<string, Map<string, PendingStep>>()

  accept(event: Event): void {
    if (!("sessionID" in event.data)) return
    const sessionID = event.data.sessionID
    const emit = (type: string, payload: Record<string, unknown>) => {
      const trace = eventBase(sessionID, event.id, type, { source: "opencode", raw_type: event.type, ...payload })
      trace.ts = new Date(event.created).toISOString()
      writeBatch(sessionID, [trace])
    }
    if (event.type === "session.inbox.enqueued") {
      const { item, inboxID } = event.data
      if (item.type === "user" && item.payload.text.trim()) emit("user_message", { message_id: inboxID, content: item.payload.text })
      return
    }
    if (event.type === "session.execution.succeeded" || event.type === "session.execution.failed" ||
        event.type === "session.execution.interrupted" || event.type === "session.deleted") {
      this.sessions.delete(sessionID)
      if (event.type === "session.execution.succeeded") emit("session_idle", {})
      return
    }
    if (!("assistantMessageID" in event.data)) return
    const messageID = event.data.assistantMessageID
    let session = this.sessions.get(sessionID)
    if (!session) this.sessions.set(sessionID, session = new Map())
    let step = session.get(messageID)
    if (!step) session.set(messageID, step = { text: new Map(), tools: new Map() })

    switch (event.type) {
      case "session.text.ended":
        step.text.set(event.data.ordinal, event.data.text)
        break
      case "session.tool.input.started":
        if (!isTitanTool(event.data.name)) step.tools.set(event.data.id, { name: event.data.name })
        break
      case "session.tool.called": {
        const tool = step.tools.get(event.data.id)
        if (tool) tool.args = boundedArgs(event.data.input)
        break
      }
      case "session.tool.success":
      case "session.tool.failed": {
        const tool = step.tools.get(event.data.id)
        if (!tool) break
        step.tools.delete(event.data.id)
        const result = event.type === "session.tool.failed"
          ? { status: "error", error: compact(redact(event.data.error), 600) }
          : { status: "success", output: compact(redact(event.data.content.filter(value => value.type === "text").map(value => value.text).join("\n"))) }
        emit("tool_execution", { message_id: messageID, call_id: event.data.id, tool: tool.name, args: tool.args, ...result })
        break
      }
      case "session.step.ended":
      case "session.step.failed": {
        if (event.type === "session.step.ended" && event.data.finish !== "tool-calls" && event.data.finish !== "error") {
          const content = [...step.text.entries()].sort(([a], [b]) => a - b).map(([, text]) => text).join("\n")
          if (content.trim()) emit("assistant_message", { message_id: messageID, finish: event.data.finish, content })
        }
        session.delete(messageID)
        if (!session.size) this.sessions.delete(sessionID)
        break
      }
    }
  }
}
