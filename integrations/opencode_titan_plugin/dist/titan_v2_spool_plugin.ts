// @bun
// src/capture.ts
import { chmodSync, closeSync, mkdirSync, openSync, renameSync, unlinkSync, writeSync } from "fs";
import { createHash, randomUUID } from "crypto";
import { join } from "path";
var SOURCE = "opencode";
var MAX_OUTPUT_CHARS = 1200;
var MAX_ERROR_CHARS = 600;
var MAX_ARGS_SERIALIZED_CHARS = 4000;
var MAX_DEDUPE_IDS_PER_SESSION = 4096;
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
  return new Promise((resolve) => setTimeout(resolve, 25 * attempt));
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
function spoolDir() {
  const explicit = process.env.TITAN_SPOOL_DIR?.trim();
  if (explicit)
    return explicit;
  const titanHome = process.env.TITAN_HOME?.trim();
  if (titanHome)
    return join(titanHome, "traces");
  const sharedHome = join(process.env.HOME || ".", ".titan");
  const agent = process.env.TITAN_AGENT_NAME?.trim() || "opencode";
  return join(sharedHome, "agents", agent, "traces");
}
function writeBatch(sessionId, events) {
  if (events.length === 0)
    return;
  const directory = spoolDir();
  mkdirSync(directory, { recursive: true, mode: 448 });
  chmodSync(directory, 448);
  const sessionHash = createHash("sha256").update(sessionId).digest("hex").slice(0, 16);
  batchSequence = (batchSequence + 1) % 1e6;
  const createdAt = String(Date.now()).padStart(13, "0");
  const sequence = String(batchSequence).padStart(6, "0");
  const target = join(directory, `opencode-${sessionHash}-${createdAt}-${sequence}-${randomUUID()}.jsonl`);
  const temporary = join(directory, `.opencode-${sessionHash}-${randomUUID()}.tmp`);
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
    const emitted = this.emitted.get(sessionId) || new Map;
    const fresh = candidates.filter((event) => {
      const id = String(event.event_id || "");
      return Boolean(id) && !emitted.has(id);
    });
    writeBatch(sessionId, fresh);
    for (const event of fresh) {
      const id = String(event.event_id);
      emitted.delete(id);
      emitted.set(id, true);
      while (emitted.size > MAX_DEDUPE_IDS_PER_SESSION) {
        const oldest = emitted.keys().next().value;
        if (oldest === undefined)
          break;
        emitted.delete(oldest);
      }
    }
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

// src/index.ts
var TitanMemoryPlugin = async ({ client, directory }) => {
  const coordinator = new CaptureCoordinator(client, directory);
  return {
    event: async ({ event }) => {
      await coordinator.onEvent(event);
    }
  };
};
export {
  TitanMemoryPlugin
};
