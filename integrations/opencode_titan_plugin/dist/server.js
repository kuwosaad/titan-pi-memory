// @bun
// src/skills.ts
var REQUIRED_SKILLS = [
  "titan-memory-workflow",
  "titan-doctor-workflow",
  "titan-cluster-graph-workflow",
  "titan-patterns-workflow",
  "memory-sync"
];

// src/namespace.ts
import { homedir } from "os";
import { basename, dirname, join, resolve } from "path";
function namespaceEnvironment() {
  const env = process.env;
  const inherited = env.TITAN_HOME?.trim() || "";
  const namedAgent = env.TITAN_AGENT_NAME?.trim();
  const inAgents = Boolean(inherited) && basename(dirname(inherited)) === "agents";
  const foreign = namedAgent && namedAgent !== "opencode" || inAgents && basename(inherited) !== "opencode";
  const shared = env.TITAN_SHARED_HOME || (inAgents ? dirname(dirname(inherited)) : join(homedir(), ".titan"));
  const home = resolve(inherited && !foreign ? inherited : join(shared, "agents", "opencode"));
  const spool = !foreign && env.TITAN_SPOOL_DIR?.trim();
  return {
    TITAN_AGENT_NAME: "opencode",
    TITAN_HOME: home,
    TITAN_BASE_DIR: home,
    TITAN_SPOOL_DIR: resolve(spool || join(home, "traces"))
  };
}

// src/server.ts
import { spawn } from "child_process";
import { readFileSync } from "fs";
import { dirname as dirname2, join as join3 } from "path";
import { fileURLToPath } from "url";

// src/capture.ts
import { chmodSync, closeSync, mkdirSync, openSync, renameSync, unlinkSync, writeSync } from "fs";
import { createHash, randomUUID } from "crypto";
import { join as join2 } from "path";
var SOURCE = "opencode";
var MAX_OUTPUT_CHARS = 1200;
var MAX_ERROR_CHARS = 600;
var MAX_ARGS_SERIALIZED_CHARS = 4000;
var MAX_DEDUPE_SESSIONS = 64;
var MAX_SYNC_ATTEMPTS = 3;
var batchSequence = 0;
var SECRET_KEY_MARKERS = [
  "token",
  "secret",
  "password",
  "api_key",
  "apikey",
  "authorization",
  "credential",
  "cookie",
  "client_key",
  "clientkey",
  "private_key",
  "privatekey",
  "access_key",
  "accesskey"
];
var SECRET_PATTERNS = [
  /\bsk[-_][A-Za-z0-9_-]{8,}\b/g,
  /\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b/g,
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g,
  /\bAIza[0-9A-Za-z_-]{20,}\b/g,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\bnpm_[A-Za-z0-9]{20,}\b/g,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b/gi,
  /\b(?:Authorization|Proxy-Authorization)\s*[:=]\s*(?:Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]+\b/gi,
  /\bntn_[A-Za-z0-9]{12,}\b/g,
  /\bsecret_[A-Za-z0-9]{12,}\b/g,
  /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g,
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/g,
  /\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*\s*=\s*(?:[^\s'"`]+|'[^']*'|"[^"]*")/gi
];
function shouldSynchronize(event) {
  const type = String(event?.type || "");
  if (type === "session.idle")
    return true;
  if (type === "message.updated") {
    const info = event?.properties?.info || {};
    if (info.role === "user")
      return true;
    if (info.role !== "assistant" || info.error || info.time?.completed === undefined)
      return false;
    return String(info.finish || "").trim().toLowerCase() !== "tool-calls";
  }
  if (type === "message.part.updated") {
    const part = event?.properties?.part || {};
    const status = String(part.state?.status || "").toLowerCase();
    return part.type === "tool" && (status === "completed" || status === "error");
  }
  return false;
}
function text(value) {
  if (typeof value === "string")
    return value;
  if (value === undefined || value === null)
    return "";
  try {
    return JSON.stringify(value, (_key, nested) => typeof nested === "bigint" ? String(nested) : nested);
  } catch {
    return String(value);
  }
}
function compact(value, limit = MAX_OUTPUT_CHARS) {
  const normalized = text(value).replace(/\s+/g, " ").trim();
  if (normalized.length <= limit)
    return normalized;
  return `${normalized.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
}
function isSensitiveKey(key) {
  const lowered = key.toLowerCase();
  return lowered === "key" || SECRET_KEY_MARKERS.some((marker) => lowered.includes(marker));
}
function retryDelay(attempt) {
  return new Promise((resolve2) => setTimeout(resolve2, 25 * attempt));
}
function redactString(value) {
  return SECRET_PATTERNS.reduce((current, pattern) => current.replace(pattern, "[REDACTED]"), value);
}
function redact(value, keyHint = "") {
  if (isSensitiveKey(keyHint))
    return "[REDACTED]";
  if (typeof value === "string")
    return redactString(value);
  if (Array.isArray(value))
    return value.map((item) => redact(item, keyHint));
  if (!value || typeof value !== "object")
    return value;
  const result = {};
  for (const [key, nested] of Object.entries(value)) {
    result[key] = isSensitiveKey(key) ? "[REDACTED]" : redact(nested, key);
  }
  return result;
}
function resolveSessionId(event) {
  const properties = event?.properties || {};
  const info = properties.info || {};
  const part = properties.part || {};
  const candidates = [
    properties.sessionID,
    properties.sessionId,
    info.sessionID,
    info.sessionId,
    part.sessionID,
    part.sessionId,
    event.sessionID,
    event.sessionId
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim())
      return candidate.trim();
  }
  return "";
}
function isTitanTool(name) {
  if (typeof name !== "string")
    return false;
  return /(?:^|[^a-z0-9])titan[-_]memory(?:[^a-z0-9]|$)/i.test(name);
}
function textParts(record) {
  return (Array.isArray(record.parts) ? record.parts : []).filter((part) => part?.type === "text" && !part.synthetic && !part.ignored && typeof part.text === "string").map((part) => part.text.trim()).filter(Boolean).join(`
`);
}
function eventBase(sessionId, eventId, eventType, payload) {
  return {
    session_id: sessionId,
    event_id: eventId,
    event_type: eventType,
    ts: new Date().toISOString(),
    schema_version: "v1",
    payload: redact(payload)
  };
}
function messageEvents(sessionId, records) {
  const events = [];
  for (const record of records) {
    const info = record?.info || {};
    const id = typeof info.id === "string" ? info.id : "";
    if (!id)
      continue;
    const content = textParts(record);
    if (!content)
      continue;
    if (info.role === "user") {
      events.push(eventBase(sessionId, `opencode:message:${id}:user`, "user_message", {
        source: SOURCE,
        raw_type: "message.updated",
        message_id: id,
        content
      }));
      continue;
    }
    if (info.role !== "assistant" || info.error)
      continue;
    const finish = typeof info.finish === "string" ? info.finish.trim().toLowerCase() : "";
    if (finish === "tool-calls")
      continue;
    if (info.time?.completed === undefined)
      continue;
    events.push(eventBase(sessionId, `opencode:message:${id}:assistant`, "assistant_message", {
      source: SOURCE,
      raw_type: "message.updated",
      message_id: id,
      parent_id: typeof info.parentID === "string" ? info.parentID : undefined,
      finish: finish || undefined,
      content
    }));
  }
  return events;
}
function toolEvents(sessionId, records) {
  const events = [];
  for (const record of records) {
    const info = record?.info || {};
    for (const part of Array.isArray(record?.parts) ? record.parts : []) {
      if (part?.type !== "tool")
        continue;
      const state = part.state || {};
      const status = String(state.status || "").toLowerCase();
      if (status !== "completed" && status !== "error")
        continue;
      if (isTitanTool(part.tool))
        continue;
      const partId = typeof part.id === "string" ? part.id : "";
      const callId = typeof part.callID === "string" ? part.callID : "";
      if (!partId || !callId || typeof part.tool !== "string" || !part.tool.trim())
        continue;
      const output = status === "completed" ? state.output : undefined;
      const error = status === "error" ? state.error : undefined;
      events.push(eventBase(sessionId, `opencode:tool:${partId}:${callId}`, "tool_execution", {
        source: SOURCE,
        raw_type: "message.part.updated",
        tool: part.tool,
        call_id: callId,
        part_id: partId,
        message_id: typeof info.id === "string" ? info.id : undefined,
        status: status === "error" ? "error" : "success",
        args: boundedArgs(state.input || {}),
        output: output === undefined ? undefined : compact(redact(output)),
        error: error === undefined ? undefined : compact(redact(error), MAX_ERROR_CHARS)
      }));
    }
  }
  return events;
}
function boundedArgs(value) {
  const sanitized = redact(value);
  let serialized = "";
  try {
    serialized = JSON.stringify(sanitized);
  } catch {
    serialized = text(sanitized);
  }
  if (serialized.length <= MAX_ARGS_SERIALIZED_CHARS)
    return sanitized;
  return { excerpt: compact(serialized, MAX_ARGS_SERIALIZED_CHARS - 20) };
}
function latestStableMessageId(records) {
  for (let index = records.length - 1;index >= 0; index -= 1) {
    const info = records[index]?.info || {};
    if (typeof info.id !== "string" || !info.id.trim())
      continue;
    if (info.role === "user")
      return info.id;
    const finish = typeof info.finish === "string" ? info.finish.trim().toLowerCase() : "";
    if (info.role === "assistant" && !info.error && info.time?.completed !== undefined && finish !== "tool-calls")
      return info.id;
  }
  return "";
}
function idleEvent(sessionId, records) {
  const anchor = latestStableMessageId(records);
  if (!anchor)
    return;
  return eventBase(sessionId, `opencode:idle:${anchor}`, "session_idle", {
    source: SOURCE,
    raw_type: "session.idle",
    anchor_message_id: anchor
  });
}
function writeBatch(sessionId, events) {
  if (events.length === 0)
    return;
  const directory = namespaceEnvironment().TITAN_SPOOL_DIR;
  mkdirSync(directory, { recursive: true, mode: 448 });
  chmodSync(directory, 448);
  const sessionHash = createHash("sha256").update(sessionId).digest("hex").slice(0, 16);
  batchSequence = (batchSequence + 1) % 1e6;
  const createdAt = String(Date.now()).padStart(13, "0");
  const sequence = String(batchSequence).padStart(6, "0");
  const target = join2(directory, `opencode-${sessionHash}-${createdAt}-${sequence}-${randomUUID()}.jsonl`);
  const temporary = join2(directory, `.opencode-${sessionHash}-${randomUUID()}.tmp`);
  const contents = events.map((event) => `${JSON.stringify(redact(event))}
`).join("");
  let fd;
  try {
    fd = openSync(temporary, "wx", 384);
    writeSync(fd, contents, undefined, "utf8");
    chmodSync(temporary, 384);
    closeSync(fd);
    fd = undefined;
    renameSync(temporary, target);
  } catch (error) {
    if (fd !== undefined) {
      try {
        closeSync(fd);
      } catch {}
    }
    try {
      unlinkSync(temporary);
    } catch {}
    throw error;
  }
}

class CaptureCoordinator {
  client;
  directory;
  queues = new Map;
  emitted = new Map;
  constructor(client, directory) {
    this.client = client;
    this.directory = directory;
  }
  onEvent(event) {
    if (!shouldSynchronize(event))
      return Promise.resolve();
    const type = String(event.type);
    const sessionId = resolveSessionId(event);
    if (!sessionId)
      return Promise.resolve();
    const queue = this.queues.get(sessionId) || { running: false, scheduled: false, pending: false, idlePending: false, idleSeen: false, attempts: 0 };
    queue.pending = true;
    queue.idlePending = queue.idlePending || type === "session.idle";
    this.queues.set(sessionId, queue);
    if (!queue.running && !queue.scheduled) {
      queue.scheduled = true;
      queueMicrotask(() => {
        queue.scheduled = false;
        if (!queue.running)
          this.drain(sessionId, queue);
      });
    }
    return Promise.resolve();
  }
  async drain(sessionId, queue) {
    queue.running = true;
    try {
      while (queue.pending) {
        queue.pending = false;
        const includeIdle = queue.idlePending;
        queue.idlePending = false;
        queue.idleSeen = queue.idleSeen || includeIdle;
        try {
          await this.synchronize(sessionId, includeIdle);
          queue.attempts = 0;
        } catch {
          queue.attempts += 1;
          if (queue.attempts < MAX_SYNC_ATTEMPTS) {
            queue.pending = true;
            queue.idlePending = queue.idlePending || includeIdle;
            await retryDelay(queue.attempts);
          }
        }
      }
    } finally {
      queue.running = false;
      if (queue.pending)
        this.drain(sessionId, queue);
      else if (queue.idleSeen)
        this.queues.delete(sessionId);
    }
  }
  async synchronize(sessionId, includeIdle) {
    if (!this.client.session?.messages)
      return;
    const response = await this.client.session.messages({ path: { id: sessionId }, query: { directory: this.directory } });
    if (response?.error || !Array.isArray(response?.data))
      throw new Error("OpenCode session transcript was unavailable");
    const records = response.data;
    const candidates = [...messageEvents(sessionId, records), ...toolEvents(sessionId, records)];
    const recordOrder = new Map;
    records.forEach((record, index) => {
      const id = typeof record?.info?.id === "string" ? record.info.id : "";
      if (id)
        recordOrder.set(id, index);
    });
    candidates.sort((left, right) => {
      const leftPayload = left.payload || {};
      const rightPayload = right.payload || {};
      const leftOrder = recordOrder.get(String(leftPayload.message_id || "")) ?? Number.MAX_SAFE_INTEGER;
      const rightOrder = recordOrder.get(String(rightPayload.message_id || "")) ?? Number.MAX_SAFE_INTEGER;
      return leftOrder - rightOrder;
    });
    if (includeIdle) {
      const idle = idleEvent(sessionId, records);
      if (idle)
        candidates.push(idle);
    }
    const emitted = this.emitted.get(sessionId) || new Set;
    const fresh = candidates.filter((event) => {
      const id = String(event.event_id || "");
      return Boolean(id) && !emitted.has(id);
    });
    writeBatch(sessionId, fresh);
    for (const event of fresh)
      emitted.add(String(event.event_id));
    this.emitted.delete(sessionId);
    this.emitted.set(sessionId, emitted);
    while (this.emitted.size > MAX_DEDUPE_SESSIONS) {
      const oldest = this.emitted.keys().next().value;
      if (oldest === undefined)
        break;
      this.emitted.delete(oldest);
    }
  }
}

// src/v2-capture.ts
var captureTypes = [
  "session.inbox.enqueued",
  "session.text.ended",
  "session.step.ended",
  "session.step.failed",
  "session.tool.input.started",
  "session.tool.called",
  "session.tool.success",
  "session.tool.failed",
  "session.execution.succeeded",
  "session.execution.failed",
  "session.execution.interrupted",
  "session.deleted"
];

class V2Capture {
  sessions = new Map;
  accept(event) {
    if (!("sessionID" in event.data))
      return;
    const sessionID = event.data.sessionID;
    const emit = (type, payload) => {
      const trace = eventBase(sessionID, event.id, type, { source: "opencode", raw_type: event.type, ...payload });
      trace.ts = new Date(event.created).toISOString();
      writeBatch(sessionID, [trace]);
    };
    if (event.type === "session.inbox.enqueued") {
      const { item, inboxID } = event.data;
      if (item.type === "user" && item.payload.text.trim())
        emit("user_message", { message_id: inboxID, content: item.payload.text });
      return;
    }
    if (event.type === "session.execution.succeeded" || event.type === "session.execution.failed" || event.type === "session.execution.interrupted" || event.type === "session.deleted") {
      this.sessions.delete(sessionID);
      if (event.type === "session.execution.succeeded")
        emit("session_idle", {});
      return;
    }
    if (!("assistantMessageID" in event.data))
      return;
    const messageID = event.data.assistantMessageID;
    let session = this.sessions.get(sessionID);
    if (!session)
      this.sessions.set(sessionID, session = new Map);
    let step = session.get(messageID);
    if (!step)
      session.set(messageID, step = { text: new Map, tools: new Map });
    switch (event.type) {
      case "session.text.ended":
        step.text.set(event.data.ordinal, event.data.text);
        break;
      case "session.tool.input.started":
        if (!isTitanTool(event.data.name))
          step.tools.set(event.data.id, { name: event.data.name });
        break;
      case "session.tool.called": {
        const tool = step.tools.get(event.data.id);
        if (tool)
          tool.args = boundedArgs(event.data.input);
        break;
      }
      case "session.tool.success":
      case "session.tool.failed": {
        const tool = step.tools.get(event.data.id);
        if (!tool)
          break;
        step.tools.delete(event.data.id);
        const result = event.type === "session.tool.failed" ? { status: "error", error: compact(redact(event.data.error), 600) } : { status: "success", output: compact(redact(event.data.content.filter((value) => value.type === "text").map((value) => value.text).join(`
`))) };
        emit("tool_execution", { message_id: messageID, call_id: event.data.id, tool: tool.name, args: tool.args, ...result });
        break;
      }
      case "session.step.ended":
      case "session.step.failed": {
        if (event.type === "session.step.ended" && event.data.finish !== "tool-calls" && event.data.finish !== "error") {
          const content = [...step.text.entries()].sort(([a], [b]) => a - b).map(([, text2]) => text2).join(`
`);
          if (content.trim())
            emit("assistant_message", { message_id: messageID, finish: event.data.finish, content });
        }
        session.delete(messageID);
        if (!session.size)
          this.sessions.delete(sessionID);
        break;
      }
    }
  }
}

// src/server.ts
var root = join3(dirname2(fileURLToPath(import.meta.url)), "..");
var launcher = join3(root, "scripts/titan_runtime.py");
function openProviderSetup() {
  return new Promise((resolve2, reject) => {
    const child = spawn("python3", [launcher, "configure", "--agent", "opencode"], {
      stdio: ["ignore", "pipe", "ignore"],
      env: { ...process.env, ...namespaceEnvironment() }
    });
    let line = "";
    child.once("error", reject);
    child.once("exit", (code) => reject(new Error(`Titan provider setup exited (${code}) before opening the form.`)));
    child.stdout.on("data", (chunk) => {
      line = (line + chunk.toString()).slice(-2048);
      if (line.includes("[titan] Open onboarding:")) {
        child.stdout.destroy();
        child.unref();
        resolve2();
      }
    });
  });
}
var server_default = {
  id: "titan-memory",
  async setup(ctx) {
    await ctx.mcp.transform((editor) => {
      const configured = editor.get("titan-memory");
      if (configured) {
        if (configured.type !== "local")
          return;
        const command = configured.command;
        const args = command.slice(-3).join(" ");
        const legacy = command.length === 4 && command[0] === "titan";
        const bundled = command.length === 5 && command[1] === launcher;
        if (args !== "mcp --agent opencode" || !(legacy || bundled))
          return;
      }
      editor.set("titan-memory", {
        ...configured,
        type: "local",
        command: ["python3", launcher, "mcp", "--agent", "opencode"],
        codemode: configured?.codemode ?? false,
        environment: { ...configured?.environment, ...namespaceEnvironment() },
        timeout: { startup: 180000, ...configured?.timeout }
      });
    });
    await ctx.command.transform((editor) => editor.add({
      name: "titan-setup",
      description: "Open the local Titan provider configuration form",
      execute: openProviderSetup
    }));
    const skillsRoot = join3(root, "skills");
    const skills = REQUIRED_SKILLS.map((name) => {
      const path = join3(skillsRoot, name, "SKILL.md");
      const source = readFileSync(path, "utf8");
      const [header, content] = source.slice(4).split(/\n---\n(.*)/s);
      return {
        id: name,
        name,
        path,
        content,
        description: header.match(/^description: (.+)$/m)?.[1] ?? name
      };
    });
    await ctx.skill.transform((editor) => {
      for (const skill of skills)
        if (!editor.get(skill.id))
          editor.add(skill);
    });
    const controller = new AbortController;
    const capture = new V2Capture;
    const run = async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal, types: [...captureTypes] })) {
        if (event.location && event.location.directory !== ctx.location.directory)
          continue;
        try {
          capture.accept(event);
        } catch (error) {
          console.warn("Titan could not write an OpenCode trace", error);
        }
      }
    };
    const running = run().catch((error) => {
      if (!controller.signal.aborted)
        console.warn("Titan OpenCode capture stopped", error);
    });
    return () => {
      controller.abort();
      return running;
    };
  }
};
export {
  server_default as default
};
