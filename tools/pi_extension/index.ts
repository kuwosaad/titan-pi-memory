/**
 * Titan Pi Extension
 *
 * Cross-session persistent memory for the Pi coding agent.
 *
 * Architecture:
 *   - Passive capture: listens to Pi lifecycle events, writes Titan-compatible
 *     spool events as JSONL files. Titan's auto-ingest worker reads these and
 *     builds scenes + extracts memories.
 *   - Active tools: native Pi tools that call Titan's HTTP API for semantic
 *     memory search, scene recovery, and diagnostics.
 *   - Server management: starts/stops a local Titan HTTP server on port 8002.
 *
 * Spool directory:  ~/.titan/agents/pi/traces/<session_id>.jsonl
 * Titan API:        http://127.0.0.1:8002
 */

import type { ExtensionAPI, ExtensionCommandContext, ToolResultEvent } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { appendFileSync, mkdirSync, existsSync, copyFileSync, writeFileSync, readFileSync } from "node:fs";
import { spawn, type ChildProcess, type SpawnOptionsWithoutStdio } from "node:child_process";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/** Resolve repo root (tools/pi_extension/ → repo root) */
const REPO_ROOT = resolve(__dirname, "../..");

/** Default TiPi agent home */
const DEFAULT_TITAN_HOME = resolve(
  process.env.HOME || "/tmp",
  ".titan/agents/pi",
);
const TITAN_HOME = process.env.TITAN_HOME || DEFAULT_TITAN_HOME;

/** Titan HTTP API base URL for Pi */
const TITAN_API_BASE = process.env.TITAN_PI_API_URL || "http://127.0.0.1:8002";

/** Spool directory for trace events */
const SPOOL_DIR =
  process.env.TITAN_SPOOL_DIR || resolve(TITAN_HOME, "traces");

/** Server log output is hidden from the Pi TUI by default and written here. */
const SERVER_LOG_PATH = resolve(TITAN_HOME, "logs", "server.log");
const TITAN_VERBOSE_LOGS = /^(1|true|yes)$/i.test(process.env.TITAN_PI_VERBOSE || "");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** Unique session ID — generated once on session_start, stable for the session */
let sessionId: string = "default";

/** Reference to the Titan server subprocess (if started by extension) */
let titanServerProcess: ChildProcess | null = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Write a single trace event to the spool file (matches OpenCode format). */
function writeSpoolEvent(
  eventType: string,
  payload: Record<string, unknown>,
): void {
  const target = resolve(SPOOL_DIR, `${sessionId}.jsonl`);
  mkdirSync(dirname(target), { recursive: true });

  const traceEvent = {
    session_id: sessionId,
    event_id: randomUUID(),
    event_type: eventType,
    ts: new Date().toISOString(),
    schema_version: "v1",
    payload,
  };

  appendFileSync(target, JSON.stringify(traceEvent) + "\n", {
    encoding: "utf-8",
  });
}

/** Append Titan server output to a log file without polluting the Pi TUI. */
function writeServerLog(stream: "stdout" | "stderr", message: string): void {
  const cleaned = message.trim();
  if (!cleaned) return;
  try {
    mkdirSync(dirname(SERVER_LOG_PATH), { recursive: true });
    appendFileSync(
      SERVER_LOG_PATH,
      `[${new Date().toISOString()}] ${stream}: ${cleaned}\n`,
      "utf-8",
    );
  } catch {
    // Logging should never break Pi startup.
  }
}

/** Compact a value to a short string for spool payloads. */
function compactText(value: unknown, limit = 1000): string {
  if (value === undefined || value === null) return "";
  const raw = typeof value === "string" ? value : JSON.stringify(value);
  const cleaned = raw.replace(/\s+/g, " ").trim();
  if (cleaned.length <= limit) return cleaned;
  return cleaned.slice(0, limit - 3).trimEnd() + "...";
}

/** Prepare the Pi workspace without touching OpenCode or global Titan state. */
function runProcess(
  command: string,
  args: string[],
  options: SpawnOptionsWithoutStdio = {},
): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolvePromise) => {
    const proc = spawn(command, args, {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    proc.stdout?.on("data", (data: Buffer) => {
      stdout += data.toString();
    });
    proc.stderr?.on("data", (data: Buffer) => {
      stderr += data.toString();
    });
    proc.on("error", (err) => {
      stderr += err.message;
      resolvePromise({ code: 1, stdout, stderr });
    });
    proc.on("exit", (code) => {
      resolvePromise({ code, stdout, stderr });
    });
  });
}

const REQUIRED_PYTHON_IMPORTS = [
  "fastapi",
  "uvicorn",
  "requests",
  "yaml",
  "numpy",
  "networkx",
  "pydantic",
];

async function hasPythonDependencies(): Promise<boolean> {
  const script = REQUIRED_PYTHON_IMPORTS.map((name) => `import ${name}`).join("; ");
  const result = await runProcess("python3", ["-c", script], { cwd: REPO_ROOT });
  return result.code === 0;
}

async function ensurePythonDependencies(): Promise<{ ok: boolean; message: string }> {
  if (await hasPythonDependencies()) {
    return { ok: true, message: "already installed" };
  }

  const requirementsPath = resolve(REPO_ROOT, "requirements.txt");
  if (!existsSync(requirementsPath)) {
    return {
      ok: false,
      message: `requirements.txt not found at ${requirementsPath}`,
    };
  }

  const result = await runProcess(
    "python3",
    ["-m", "pip", "install", "-r", requirementsPath],
    { cwd: REPO_ROOT, env: process.env },
  );

  if (await hasPythonDependencies()) {
    return { ok: true, message: result.code === 0 ? "installed" : "core deps installed" };
  }

  // Some Python installs reject system-wide package writes. Try user scope next.
  const userResult = await runProcess(
    "python3",
    ["-m", "pip", "install", "--user", "-r", requirementsPath],
    { cwd: REPO_ROOT, env: process.env },
  );

  if (await hasPythonDependencies()) {
    return { ok: true, message: userResult.code === 0 ? "installed with --user" : "core deps installed" };
  }

  const excerpt = (userResult.stderr || userResult.stdout || result.stderr || result.stdout || "unknown error")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500);
  return { ok: false, message: `install failed: ${excerpt}` };
}

function ensurePiWorkspace(): { copiedConfigs: string[]; envCreated: boolean } {
  const configDir = resolve(TITAN_HOME, "config");
  mkdirSync(configDir, { recursive: true });
  mkdirSync(SPOOL_DIR, { recursive: true });

  const copiedConfigs: string[] = [];
  for (const filename of ["extraction_models.yaml", "embedding_models.yaml"]) {
    const source = resolve(REPO_ROOT, "config", filename);
    const target = resolve(configDir, filename);
    if (!existsSync(target) && existsSync(source)) {
      copyFileSync(source, target);
      copiedConfigs.push(filename);
    }
  }

  const envPath = resolve(TITAN_HOME, ".env");
  let envCreated = false;
  if (!existsSync(envPath)) {
    writeFileSync(
      envPath,
      [
        "# Titan Pi workspace secrets",
        "# Add the key for your configured extraction model, for example:",
        "# GEMINI_API_KEY=your_key_here",
        "# OPENAI_API_KEY=your_key_here",
        "",
      ].join("\n"),
      "utf-8",
    );
    envCreated = true;
  }

  return { copiedConfigs, envCreated };
}

/** Add or replace one KEY=value in the Pi workspace .env file. */
function upsertEnvKey(keyName: string, value: string): string {
  ensurePiWorkspace();
  const envPath = resolve(TITAN_HOME, ".env");
  const lines = existsSync(envPath)
    ? readFileSync(envPath, "utf-8").split(/\r?\n/)
    : [];

  const keyPattern = new RegExp(`^\\s*${keyName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*=`);
  let replaced = false;
  const nextLines = lines.map((line) => {
    if (keyPattern.test(line)) {
      replaced = true;
      return `${keyName}=${value}`;
    }
    return line;
  });

  if (!replaced) nextLines.push(`${keyName}=${value}`);
  writeFileSync(envPath, nextLines.join("\n").replace(/\n*$/, "\n"), "utf-8");
  return envPath;
}

async function restartOwnedServer(): Promise<boolean> {
  if (titanServerProcess) {
    titanServerProcess.kill();
    titanServerProcess = null;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 800));
  }
  return ensureServerRunning();
}

/** Extract text content from a message content array or string. */
function extractTextContent(
  content: unknown,
): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (p: unknown): p is { type: string; text?: string } =>
        typeof p === "object" && p !== null && (p as Record<string, unknown>).type === "text",
    )
    .map((p) => p.text || "")
    .join("\n");
}

/** Check if Titan server is healthy. */
async function isServerHealthy(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(`${TITAN_API_BASE}/health`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);
    return res.ok;
  } catch {
    return false;
  }
}

/** Start Titan server as a subprocess. Returns true if started or already running. */
async function ensureServerRunning(): Promise<boolean> {
  if (await isServerHealthy()) return true;
  if (titanServerProcess) return false; // already tried starting

  const serverScript = resolve(REPO_ROOT, "tools/pi_extension/server.py");
  if (!existsSync(serverScript)) {
    console.warn("[titan] server.py not found at", serverScript);
    return false;
  }

  return new Promise((resolvePromise) => {
    const proc = spawn("python3", [serverScript], {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        TITAN_HOME,
        TITAN_BASE_DIR: TITAN_HOME,
        TITAN_PI_PORT: "8002",
        PYTHONPATH: REPO_ROOT,
      },
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
    });

    proc.stdout?.on("data", (data: Buffer) => {
      const msg = data.toString();
      writeServerLog("stdout", msg);
      if (TITAN_VERBOSE_LOGS && msg.trim()) console.log("[titan]", msg.trim());
    });

    proc.stderr?.on("data", (data: Buffer) => {
      const msg = data.toString();
      writeServerLog("stderr", msg);
      if (TITAN_VERBOSE_LOGS && msg.trim()) console.error("[titan]", msg.trim());
    });

    proc.on("error", (err) => {
      console.warn("[titan] Failed to start server:", err.message);
      titanServerProcess = null;
      resolvePromise(false);
    });

    proc.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        console.warn(`[titan] Server exited with code ${code}`);
      }
      titanServerProcess = null;
    });

    titanServerProcess = proc;

    // Wait for server to become healthy
    const maxWait = 15_000; // 15 seconds
    const pollInterval = 500;
    const startTime = Date.now();
    const poll = setInterval(async () => {
      if (await isServerHealthy()) {
        clearInterval(poll);
        resolvePromise(true);
      } else if (Date.now() - startTime > maxWait) {
        clearInterval(poll);
        console.warn("[titan] Server did not become healthy in time");
        resolvePromise(false);
      }
    }, pollInterval);
  });
}

// ---------------------------------------------------------------------------
// Titan API helpers
// ---------------------------------------------------------------------------

interface TitanMemory {
  id: string;
  text: string;
  type: string;
  session_id?: string;
  scene_id?: string;
  ts?: string;
  [key: string]: unknown;
}

interface TitanScene {
  scene_id: string;
  session_id: string;
  messages: Array<{ role: string; content: string }>;
  extraction_user_text: string;
  extraction_assistant_text: string;
  [key: string]: unknown;
}

interface TitanRetrieveResponse {
  memories?: TitanMemory[];
  scenes?: TitanScene[];
  brief?: string;
  scene_brief?: string;
  count: number;
  [key: string]: unknown;
}

interface TitanCluster {
  cluster_id: number;
  topic: string;
  keywords: string[];
  memory_count: number;
  connection_count: number;
  avg_similarity: number;
  types: Record<string, number>;
  streams: Record<string, number>;
  session_count: number;
  examples: TitanMemory[];
  memory_ids: string[];
  [key: string]: unknown;
}

interface TitanClustersResponse {
  scope: string;
  session_id?: string;
  total_memory_count?: number;
  memory_count: number;
  raw_memory_count: number;
  skipped_missing_embeddings: number;
  connection_count: number;
  cluster_count: number;
  clusters: TitanCluster[];
  selected_cluster?: TitanCluster;
  error?: string;
  [key: string]: unknown;
}

interface TitanCortexMemory extends TitanMemory {
  cluster_id?: number;
  score?: number;
}

interface TitanCortexBridge {
  source_cluster_id?: number;
  target_cluster_id?: number;
  similarity?: number;
  bridge_score?: number;
  shared_terms?: string[];
  source_memory?: TitanCortexMemory;
  target_memory?: TitanCortexMemory;
  [key: string]: unknown;
}

interface TitanCortexAnalysisResponse {
  scope?: string;
  session_id?: string;
  cluster_ids?: number[];
  question?: string;
  warnings?: string[];
  memory_count?: number;
  edge_count?: number;
  bridges?: TitanCortexBridge[];
  bridge_memories?: TitanCortexMemory[];
  central_memories?: TitanCortexMemory[];
  tensions?: Record<string, unknown>[];
  subclusters?: Record<string, unknown>[];
  summary?: string;
  error?: string;
  [key: string]: unknown;
}

/** Parse a fetch Response as JSON, returning an error object on failure. */
async function safeJson(res: Response): Promise<Record<string, unknown>> {
  try {
    const data = (await res.json()) as Record<string, unknown>;
    if (!res.ok && !("error" in data)) {
      data.error = `API error: HTTP ${res.status}`;
    }
    return data;
  } catch {
    return { error: `API error: HTTP ${res.status}` };
  }
}

async function apiRetrieve(
  query: string,
  limit = 8,
  date_from?: string,
  date_to?: string,
): Promise<TitanRetrieveResponse> {
  const params = new URLSearchParams();
  params.set("query", query);
  params.set("limit", String(limit));
  if (date_from) params.set("from_date", date_from);
  if (date_to) params.set("to_date", date_to);
  const url = `${TITAN_API_BASE}/api/retrieve?${params.toString()}`;
  const res = await fetch(url);
  return (await safeJson(res)) as unknown as TitanRetrieveResponse;
}

async function apiGetScene(sceneId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/scenes/${encodeURIComponent(sceneId)}`);
  return safeJson(res);
}

async function apiGetRecentMemories(limit = 8): Promise<{ memories: TitanMemory[]; count: number; total?: number }> {
  const res = await fetch(`${TITAN_API_BASE}/api/memories?limit=${limit}`);
  return (await safeJson(res)) as unknown as { memories: TitanMemory[]; count: number; total?: number };
}

async function apiGetClusters(options: {
  clusterId?: number;
  sessionId?: string;
  limit?: number;
  detailLimit?: number;
} = {}): Promise<TitanClustersResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 500));
  params.set("detail_limit", String(options.detailLimit ?? 12));
  if (options.clusterId !== undefined) params.set("cluster_id", String(options.clusterId));
  if (options.sessionId) params.set("session_id", options.sessionId);
  const res = await fetch(`${TITAN_API_BASE}/api/clusters?${params.toString()}`);
  return (await safeJson(res)) as unknown as TitanClustersResponse;
}

async function apiAnalyzeClusters(options: {
  clusterIds: number[];
  sessionId?: string;
  limit?: number;
  detailLimit?: number;
  question?: string;
}): Promise<TitanCortexAnalysisResponse> {
  const params = new URLSearchParams();
  params.set("cluster_ids", options.clusterIds.join(","));
  params.set("limit", String(options.limit ?? 500));
  params.set("detail_limit", String(options.detailLimit ?? 8));
  if (options.question) params.set("question", options.question);
  if (options.sessionId) params.set("session_id", options.sessionId);
  const res = await fetch(`${TITAN_API_BASE}/api/clusters/analyze?${params.toString()}`);
  return (await safeJson(res)) as unknown as TitanCortexAnalysisResponse;
}

async function apiStoreTracePacket(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/trace`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return safeJson(res);
}

function formatClusterSummary(data: TitanClustersResponse): string {
  if (data.error) return data.error;
  const header = `Titan clusters: ${data.cluster_count} topics · ${data.memory_count} memories · ${data.connection_count} connections`;
  const skipped = data.skipped_missing_embeddings
    ? `\nSkipped ${data.skipped_missing_embeddings} memories without embeddings.`
    : "";
  const lines = (data.clusters ?? []).map((cluster) => {
    const keywords = cluster.keywords?.slice(0, 5).join(", ");
    const suffix = keywords ? ` — ${keywords}` : "";
    return `${cluster.cluster_id}. ${cluster.topic} (${cluster.memory_count} memories, ${cluster.connection_count} links)${suffix}`;
  });
  return [header + skipped, "", ...lines, "", "Use /titan-clusters <id> for details."].join("\n");
}

function formatClusterDetail(data: TitanClustersResponse): string {
  if (data.error) return data.error;
  const cluster = data.selected_cluster;
  if (!cluster) return formatClusterSummary(data);
  const types = Object.entries(cluster.types ?? {})
    .slice(0, 6)
    .map(([name, count]) => `${name}:${count}`)
    .join(", ");
  const examples = (cluster.examples ?? []).map((memory, index) => {
    const text = String(memory.text ?? "").replace(/\s+/g, " ").trim();
    const type = memory.type ? `[${memory.type}] ` : "";
    return `${index + 1}. ${type}${text}`;
  });
  return [
    `Cluster ${cluster.cluster_id}: ${cluster.topic}`,
    `${cluster.memory_count} memories · ${cluster.connection_count} links · avg similarity ${cluster.avg_similarity}`,
    types ? `Types: ${types}` : "",
    cluster.keywords?.length ? `Keywords: ${cluster.keywords.join(", ")}` : "",
    "",
    "Representative memories:",
    ...examples,
  ]
    .filter(Boolean)
    .join("\n");
}

function formatCortexMemory(memory: TitanCortexMemory | undefined): string {
  if (!memory) return "(missing memory)";
  const score = typeof memory.score === "number" ? ` score ${memory.score.toFixed(4)}` : "";
  const cluster = typeof memory.cluster_id === "number" ? `C${memory.cluster_id}` : "C?";
  return `[${cluster}${score}] ${String(memory.text ?? "").replace(/\s+/g, " ").trim()}`;
}

function formatCortexAnalysis(data: TitanCortexAnalysisResponse): string {
  if (data.error) return data.error;
  const clusterIds = data.cluster_ids?.join(", ") || "?";
  const header = `Titan Cortex: cluster(s) ${clusterIds}`;
  const stats = `${data.memory_count ?? 0} memories · ${data.edge_count ?? 0} graph edges`;
  const warnings = (data.warnings ?? []).length ? [`Warnings: ${(data.warnings ?? []).join("; ")}`, ""] : [];

  const central = (data.central_memories ?? []).slice(0, 5).map((memory, index) => `${index + 1}. ${formatCortexMemory(memory)}`);
  const bridgeMemories = (data.bridge_memories ?? []).slice(0, 5).map((memory, index) => `${index + 1}. ${formatCortexMemory(memory)}`);
  const bridges = (data.bridges ?? []).slice(0, 5).map((bridge, index) => {
    const shared = bridge.shared_terms?.length ? ` · shared: ${bridge.shared_terms.slice(0, 5).join(", ")}` : "";
    return `${index + 1}. C${bridge.source_cluster_id} ↔ C${bridge.target_cluster_id} similarity ${bridge.similarity}${shared}\n   A: ${formatCortexMemory(bridge.source_memory)}\n   B: ${formatCortexMemory(bridge.target_memory)}`;
  });
  const tensions = (data.tensions ?? []).slice(0, 4).map((tension, index) => {
    const signal = String(tension["signal"] ?? "possible tension");
    const older = formatCortexMemory(tension["older_memory"] as TitanCortexMemory | undefined);
    const newer = formatCortexMemory(tension["newer_memory"] as TitanCortexMemory | undefined);
    return `${index + 1}. ${signal}\n   Older: ${older}\n   Newer: ${newer}`;
  });
  const subclusters = (data.subclusters ?? []).slice(0, 5).map((subcluster, index) => {
    const rawKeywords = subcluster["keywords"];
    const keywords = Array.isArray(rawKeywords) ? rawKeywords.slice(0, 6).join(", ") : "";
    return `${index + 1}. ${subcluster["memory_count"] ?? "?"} memories${keywords ? ` · ${keywords}` : ""}`;
  });

  return [
    header,
    stats,
    data.question ? `Question: ${data.question}` : "",
    data.summary ? `Summary: ${data.summary}` : "",
    "",
    ...warnings,
    central.length ? "Central memories:" : "",
    ...central,
    central.length ? "" : "",
    bridgeMemories.length ? "Bridge memories:" : "",
    ...bridgeMemories,
    bridgeMemories.length ? "" : "",
    bridges.length ? "Strongest bridges:" : "",
    ...bridges,
    bridges.length ? "" : "",
    tensions.length ? "Possible tensions:" : "",
    ...tensions,
    tensions.length ? "" : "",
    subclusters.length ? "Subclusters:" : "",
    ...subclusters,
  ]
    .filter(Boolean)
    .join("\n");
}

function parseTitanCortexArgs(rawArgs: string): { clusterIds: number[]; question?: string } | null {
  const tokens = (rawArgs || "").trim().split(/\s+/).filter(Boolean);
  const clusterIds: number[] = [];
  let index = 0;
  for (; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (!/^\d+(,\d+)*$/.test(token)) break;
    for (const item of token.split(",")) {
      const value = Number(item);
      if (Number.isFinite(value) && !clusterIds.includes(value)) clusterIds.push(value);
    }
  }
  if (clusterIds.length === 0) return null;
  const question = tokens.slice(index).join(" ").replace(/^[\"']|[\"']$/g, "").trim();
  return { clusterIds, question: question || undefined };
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function openUrl(url: string): Promise<void> {
  const platform = process.platform;
  const command = platform === "darwin" ? "open" : platform === "win32" ? "cmd" : "xdg-open";
  const args = platform === "win32" ? ["/c", "start", "", url] : [url];
  await new Promise<void>((resolvePromise, rejectPromise) => {
    const proc = spawn(command, args, { stdio: "ignore", detached: true });
    proc.on("error", rejectPromise);
    proc.on("exit", () => resolvePromise());
    proc.unref?.();
  });
}

function graphWrapperHtml(graphUrl: string, closeUrl: string): string {
  const safeGraphUrl = escapeHtml(graphUrl);
  const safeCloseUrl = escapeHtml(closeUrl);
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Titan Memory Graph</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; background: #080b10; overflow: hidden; }
    iframe { display: block; width: 100%; height: 100%; border: 0; }
  </style>
</head>
<body>
  <iframe src="${safeGraphUrl}" title="Titan Memory Graph"></iframe>
  <script>
    const closeUrl = "${safeCloseUrl}";
    let sent = false;
    function notifyClosed() {
      if (sent) return;
      sent = true;
      try { navigator.sendBeacon(closeUrl, "closed"); }
      catch (_) { try { fetch(closeUrl, { method: "POST", keepalive: true, mode: "no-cors", body: "closed" }); } catch (_) {} }
    }
    window.addEventListener("pagehide", notifyClosed);
    window.addEventListener("beforeunload", notifyClosed);
  </script>
</body>
</html>`;
}

async function openGraphAndWait(graphUrl: string): Promise<void> {
  let server: Server | undefined;
  await new Promise<void>((resolvePromise, rejectPromise) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      server?.close();
      resolvePromise();
    };

    server = createServer((req, res) => {
      if (req.url?.startsWith("/closed")) {
        res.writeHead(204, { "Access-Control-Allow-Origin": "*" });
        res.end();
        finish();
        return;
      }

      if (req.url === "/" || req.url?.startsWith("/?")) {
        const address = server?.address() as AddressInfo;
        const closeUrl = `http://localhost:${address?.port ?? 8002}/closed`;
        const html = graphWrapperHtml(graphUrl, closeUrl);
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end(html);
        return;
      }

      res.writeHead(404);
      res.end("not found");
    });

    server.on("error", rejectPromise);
    server.listen(0, "127.0.0.1", async () => {
      try {
        const address = server?.address() as AddressInfo;
        await openUrl(`http://127.0.0.1:${address.port}/`);
      } catch (err) {
        server?.close();
        rejectPromise(err);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

export default function titanPiExtension(pi: ExtensionAPI) {
  // =======================================================================
  // EVENT LISTENERS — Passive capture
  // =======================================================================

  // Session started: initialize session ID, check server, write opening event
  pi.on("session_start", async (event, ctx) => {
    sessionId = randomUUID();

    // Ensure Pi workspace/config exists
    ensurePiWorkspace();

    // Write opening event
    writeSpoolEvent("session_created", {
      raw_type: "session_start",
      reason: event.reason,
      previous_session: event.previousSessionFile || null,
    });

    // Check if Titan is configured
    const hasConfig =
      existsSync(resolve(TITAN_HOME, "config", "extraction_models.yaml")) &&
      existsSync(resolve(TITAN_HOME, "config", "embedding_models.yaml"));

    // Try starting server if not running
    const serverOk = await ensureServerRunning();
    if (serverOk && hasConfig) {
      ctx.ui.notify("Titan memory ready", "success");
    } else if (!hasConfig) {
      ctx.ui.notify(
        "Titan memory not configured. Run /titan-setup to get started.",
        "warning",
      );
    } else if (!serverOk) {
      ctx.ui.notify(
        "Titan server not running. Run /titan-start or /titan-setup.",
        "warning",
      );
    }
  });

  // User message
  pi.on("message_end", async (event) => {
    if (event.message.role !== "user") return;
    const text = extractTextContent(event.message.content);
    if (!text) return;

    writeSpoolEvent("user_message", {
      raw_type: "message_end.user",
      session_id: sessionId,
      content: compactText(text, 2000),
    });
  });

  // Assistant message
  pi.on("message_end", async (event) => {
    if (event.message.role !== "assistant") return;
    const text = extractTextContent(event.message.content);
    if (!text) return;

    writeSpoolEvent("assistant_message", {
      raw_type: "message_end.assistant",
      session_id: sessionId,
      content: compactText(text, 2000),
    });
  });

  // Tool result — captures what tools ran and their outcomes
  pi.on("tool_result", async (event: ToolResultEvent) => {
    const toolName = event.toolName;
    const input = event.input as Record<string, unknown> | undefined;
    const details = event.details as Record<string, unknown> | undefined;

    writeSpoolEvent("tool_execution", {
      raw_type: "tool_result",
      session_id: sessionId,
      tool: toolName,
      call_id: event.toolCallId,
      args: input ? compactText(JSON.stringify(input), 500) : undefined,
      output: {
        error: event.isError
          ? compactText(
              details?.error || extractTextContent(event.content),
              500,
            )
          : undefined,
        excerpt: event.isError
          ? undefined
          : compactText(extractTextContent(event.content), 1000),
      },
    });
  });

  // Turn completed
  pi.on("turn_end", async () => {
    writeSpoolEvent("turn_complete", {
      raw_type: "turn_end",
      session_id: sessionId,
    });
  });

  // Session closing
  pi.on("session_shutdown", async () => {
    writeSpoolEvent("session_closed", {
      raw_type: "session_shutdown",
      session_id: sessionId,
    });
  });

  // =======================================================================
  // TOOLS — Callable by the LLM
  // =======================================================================

  pi.registerTool({
    name: "titan_query_memories",
    label: "Titan Query Memories",
    description:
      "Search Titan memory for semantically relevant memories about a topic. " +
      "Use this when you need to recall previous work, decisions, or context from " +
      "earlier sessions.",
    promptSnippet:
      "Search Titan cross-session memory for relevant context",
    promptGuidelines: [
      "Use titan_query_memories when the user asks about previous work, decisions, or context from earlier sessions.",
    ],
    parameters: Type.Object({
      query: Type.String({
        description: "What to search for. Leave empty to retrieve all memories within a date bracket (use with date_from/date_to).",
      }),
      limit: Type.Optional(
        Type.Number({
          description: "Max results to return (default: 8)",
          default: 8,
        }),
      ),
      date_from: Type.Optional(
        Type.String({
          description: "Start of date range (ISO 8601, e.g. '2026-05-15' or '2026-05-15T00:00:00'). Filters memories at or after this timestamp.",
        }),
      ),
      date_to: Type.Optional(
        Type.String({
          description: "End of date range (ISO 8601, e.g. '2026-05-16' or '2026-05-16T00:00:00'). Filters memories at or before this timestamp.",
        }),
      ),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return {
          content: [
            {
              type: "text" as const,
              text: "Titan server is not running. Tell the user to check /titan-status.",
            },
          ],
        };
      }
      const data = await apiRetrieve(params.query, params.limit ?? 8, params.date_from, params.date_to);
      const memories = data.memories ?? [];
      if (memories.length === 0) {
        return {
          content: [{ type: "text" as const, text: "No relevant memories found." }],
        };
      }

      const lines = memories.map((m, i) => {
        const sceneRef = m.scene_id ? ` [scene: ${m.scene_id}]` : "";
        return `${i + 1}. ${m.text}${sceneRef}`;
      });

      // Include full scene context so the LLM can read details without a
      // separate titan_get_scene_context round-trip.
      const scenes = data.scenes ?? [];
      const sceneLines = scenes.length > 0
        ? ["", "--- Scene context ---", ...scenes.map((s) => {
            const msgs = (s.messages ?? []).map((m: { role: string; content: string }) => `[${m.role}] ${m.content}`).join("\n");
            return `Scene ${s.scene_id}:\n${msgs}`;
          })]
        : [];

      return {
        content: [{ type: "text" as const, text: [...lines, ...sceneLines].join("\n") }],
        details: { count: data.count, query: params.query, scene_count: scenes.length },
      };
    },
  });

  pi.registerTool({
    name: "titan_get_scene_context",
    label: "Titan Get Scene Context",
    description:
      "Get the full context of a specific scene (a chunk of conversation) by its scene ID. " +
      "Use this when a memory references a scene_id and you need the full context.",
    parameters: Type.Object({
      scene_id: Type.String({
        description: "The scene ID to retrieve (shown in memory results)",
      }),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return {
          content: [
            { type: "text" as const, text: "Titan server is not running." },
          ],
        };
      }
      const data = await apiGetScene(params.scene_id);
      if ("error" in data) {
        return {
          content: [{ type: "text" as const, text: String(data.error) }],
        };
      }
      return {
        content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
        details: { scene_id: params.scene_id },
      };
    },
  });

  pi.registerTool({
    name: "titan_store_trace_packet",
    label: "Titan Store Trace Packet",
    description:
      "Manually store a structured trace packet into Titan memory. " +
      "Use this to save important decisions, goals, or outcomes that should be " +
      "remembered across sessions.",
    promptGuidelines: [
      "Use titan_store_trace_packet after completing a significant piece of work to persist the context for future sessions.",
    ],
    parameters: Type.Object({
      goal: Type.String({
        description: "What was the goal of this work?",
      }),
      thoughts: Type.Optional(
        Type.String({ description: "Key thoughts or decisions made (optional)" }),
      ),
      outcome: Type.Optional(
        Type.String({ description: "What was the outcome?" }),
      ),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return {
          content: [
            { type: "text" as const, text: "Titan server is not running." },
          ],
        };
      }
      const payload: Record<string, unknown> = {
        goal: params.goal,
        session_id: sessionId,
      };
      if (params.thoughts) payload.thoughts = params.thoughts;
      if (params.outcome) payload.outcome = params.outcome;

      const result = await apiStoreTracePacket(payload);
      return {
        content: [
          {
            type: "text" as const,
            text: `Trace packet stored: ${JSON.stringify(result)}`,
          },
        ],
        details: result,
      };
    },
  });

  pi.registerTool({
    name: "titan_get_recent_memories",
    label: "Titan Get Recent Memories",
    description: "Get the most recent stored memories from Titan.",
    parameters: Type.Object({
      limit: Type.Optional(
        Type.Number({ description: "Max results (default: 8)", default: 8 }),
      ),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return {
          content: [
            { type: "text" as const, text: "Titan server is not running." },
          ],
        };
      }
      const data = await apiGetRecentMemories(params.limit ?? 8);
      const memories = data.memories ?? [];
      if (memories.length === 0) {
        return { content: [{ type: "text" as const, text: "No memories yet." }] };
      }
      const lines = memories.map((m, i) => {
        const sceneRef = m.scene_id ? ` [scene: ${m.scene_id}]` : "";
        return `${i + 1}. ${m.text}${sceneRef}`;
      });
      return {
        content: [{ type: "text" as const, text: lines.join("\n") }],
        details: { count: data.count },
      };
    },
  });

  pi.registerTool({
    name: "titan_inspect_clusters",
    label: "Titan Inspect Clusters",
    description:
      "Inspect the current Titan memory graph clusters and their main topics. " +
      "Use this when the user asks what graph clusters/topics exist or asks for details about a specific cluster.",
    promptSnippet: "Inspect Titan memory graph clusters and topic summaries",
    promptGuidelines: [
      "Use titan_inspect_clusters when the user asks about Titan graph clusters, memory topics, or details for a specific cluster.",
    ],
    parameters: Type.Object({
      cluster_id: Type.Optional(
        Type.Number({ description: "Specific cluster ID to inspect in detail" }),
      ),
      limit: Type.Optional(
        Type.Number({ description: "Max memories to cluster (default: 500)", default: 500 }),
      ),
      detail_limit: Type.Optional(
        Type.Number({ description: "Representative memories to include for detail (default: 12)", default: 12 }),
      ),
      session_id: Type.Optional(
        Type.String({ description: "Optional Titan session ID to inspect instead of the global graph" }),
      ),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return { content: [{ type: "text" as const, text: "Titan server is not running." }] };
      }
      const data = await apiGetClusters({
        clusterId: params.cluster_id,
        limit: params.limit ?? 500,
        detailLimit: params.detail_limit ?? 12,
        sessionId: params.session_id,
      });
      const text = params.cluster_id ? formatClusterDetail(data) : formatClusterSummary(data);
      return {
        content: [{ type: "text" as const, text }],
        details: data,
      };
    },
  });

  pi.registerTool({
    name: "titan_analyze_clusters",
    label: "Titan Analyze Clusters",
    description:
      "Apply Titan's Cortex/step-2.1 structural analysis over one or more memory clusters. " +
      "Use this to find bridge memories, central memories, possible tensions, and hidden subclusters.",
    promptSnippet: "Analyze Titan memory clusters with Cortex-style graph reasoning",
    promptGuidelines: [
      "Use titan_analyze_clusters when the user asks how clusters relate, what bridges two topics, or what tensions exist across memory clusters.",
      "Treat tensions as evidence-backed signals, not final psychological or diagnostic claims.",
    ],
    parameters: Type.Object({
      cluster_ids: Type.String({ description: "Comma-separated Titan cluster IDs, e.g. '3,8'" }),
      question: Type.Optional(
        Type.String({ description: "Optional question to guide the attention analysis" }),
      ),
      limit: Type.Optional(
        Type.Number({ description: "Max memories to cluster before analysis (default: 500)", default: 500 }),
      ),
      detail_limit: Type.Optional(
        Type.Number({ description: "Max items per analysis section (default: 8)", default: 8 }),
      ),
      session_id: Type.Optional(
        Type.String({ description: "Optional Titan session ID to analyze instead of the global graph" }),
      ),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return { content: [{ type: "text" as const, text: "Titan server is not running." }] };
      }
      const parsed = parseTitanCortexArgs(params.cluster_ids);
      if (!parsed) {
        return { content: [{ type: "text" as const, text: "cluster_ids must contain at least one numeric cluster ID." }] };
      }
      const data = await apiAnalyzeClusters({
        clusterIds: parsed.clusterIds,
        question: params.question,
        limit: params.limit ?? 500,
        detailLimit: params.detail_limit ?? 8,
        sessionId: params.session_id,
      });
      return {
        content: [{ type: "text" as const, text: formatCortexAnalysis(data) }],
        details: data,
      };
    },
  });

  pi.registerTool({
    name: "titan_doctor",
    label: "Titan Doctor",
    description:
      "Check if Titan memory system is healthy and operational. " +
      "Returns server status, workspace info, and memory stats.",
    parameters: Type.Object({}),
    async execute() {
      const healthy = await isServerHealthy();
      const spoolExists = existsSync(SPOOL_DIR);
      const hasConfig =
        existsSync(resolve(TITAN_HOME, "config", "extraction_models.yaml")) &&
        existsSync(resolve(TITAN_HOME, "config", "embedding_models.yaml"));

      let stats = {};
      if (healthy) {
        try {
          const data = await apiGetRecentMemories(1);
          stats = { memory_count: data.total ?? data.count };
        } catch {
          stats = { memory_count: "unknown" };
        }
      }

      return {
        content: [
          {
            type: "text" as const,
            text: [
              `Titan Status:`,
              `  Server:     ${healthy ? "✅ running" : "❌ not running"} (${TITAN_API_BASE})`,
              `  Workspace:  ${TITAN_HOME}`,
              `  Spool dir:  ${SPOOL_DIR} ${spoolExists ? "✅" : "⚠️ missing"}`,
              `  Config:     ${hasConfig ? "✅" : "⚠️ not configured (run /titan-setup)"}`,
              `  Session ID: ${sessionId}`,
              healthy ? `  Memories:   ${JSON.stringify(stats)}` : "",
            ]
              .filter(Boolean)
              .join("\n"),
          },
        ],
        details: { healthy, titan_home: TITAN_HOME, ...stats },
      };
    },
  });

  // =======================================================================
  // COMMANDS — User-facing slash commands
  // =======================================================================

  pi.registerCommand("titan-setup", {
    description: "Prepare the Titan Pi workspace and start the local memory server",
    handler: async (_args, ctx) => {
      const { copiedConfigs, envCreated } = ensurePiWorkspace();
      ctx.ui.notify("Checking Titan Python dependencies...", "info");
      const deps = await ensurePythonDependencies();
      const ok = deps.ok ? await ensureServerRunning() : false;

      const lines = [
        "Titan Pi setup complete.",
        `Workspace: ${TITAN_HOME}`,
        `Configs: ${copiedConfigs.length ? `copied ${copiedConfigs.join(", ")}` : "already present"}`,
        `Env file: ${envCreated ? "created" : "already present"} (${resolve(TITAN_HOME, ".env")})`,
        `Python deps: ${deps.ok ? "✅" : "❌"} ${deps.message}`,
        `Server: ${ok ? "running on port 8002" : "not running — check Python/Titan dependencies"}`,
        "",
        "If extraction needs an API key, add it to the .env file above, e.g.",
        "GEMINI_API_KEY=...",
        "OPENAI_API_KEY=...",
      ];

      ctx.ui.notify(lines.join("\n"), ok ? "success" : "warning");
    },
  });

  pi.registerCommand("titan-key", {
    description: "Add or update the Titan extraction API key",
    handler: async (_args, ctx) => {
      ensurePiWorkspace();

      const provider = await ctx.ui.select("Choose Titan extraction provider", [
        "Gemini (GEMINI_API_KEY)",
      ]);
      if (!provider) return;

      const keyName = "GEMINI_API_KEY";
      const value = await ctx.ui.input(
        `Paste your ${keyName}`,
        "AIza...",
      );
      const cleaned = value?.trim();
      if (!cleaned) {
        ctx.ui.notify("No API key entered. Nothing changed.", "warning");
        return;
      }

      const envPath = upsertEnvKey(keyName, cleaned);
      const serverOk = await restartOwnedServer();
      const suffix = cleaned.length >= 4 ? cleaned.slice(-4) : "****";

      ctx.ui.notify(
        [
          "Titan API key saved.",
          `Provider: ${provider}`,
          `Saved: ${keyName}=...${suffix}`,
          `File: ${envPath}`,
          `Server: ${serverOk ? "running" : "not running — run /titan-status"}`,
        ].join("\n"),
        serverOk ? "success" : "warning",
      );
    },
  });

  const graphCommand = {
    description: "Open the Titan memory knowledge graph in your browser",
    handler: async (args: string, ctx: ExtensionCommandContext) => {
      const ok = await ensureServerRunning();
      if (!ok) {
        ctx.ui.notify("Titan server is not running, so I can't open the graph.", "error");
        return;
      }

      const requestedSession = args?.trim();
      const graphUrl = requestedSession
        ? `${TITAN_API_BASE}/graph?session_id=${encodeURIComponent(requestedSession)}`
        : `${TITAN_API_BASE}/graph`;

      try {
        await openUrl(graphUrl);
        ctx.ui.notify(
          [
            "Titan knowledge graph opened in your browser.",
            graphUrl,
            "Pi is still usable — close the browser tab whenever you're done.",
          ].join("\n"),
          "success",
        );
      } catch (err) {
        ctx.ui.notify(`Could not open Titan graph: ${err}`, "error");
      }
    },
  };

  pi.registerCommand("titan-graph", graphCommand);
  pi.registerCommand("titangraph", {
    ...graphCommand,
    description: "Alias for /titan-graph",
  });

  const clusterCommand = {
    description: "Show Titan graph cluster topics, or inspect one with /titan-clusters <id>",
    getArgumentCompletions: (prefix: string) => {
      const trimmed = prefix.trim();
      if (!/^\d*$/.test(trimmed)) return null;
      return Array.from({ length: 20 }, (_value, index) => {
        const value = String(index + 1);
        return { value, label: value, description: `Inspect cluster ${value}` };
      }).filter((item) => item.value.startsWith(trimmed));
    },
    handler: async (args: string, ctx: ExtensionCommandContext) => {
      const ok = await ensureServerRunning();
      if (!ok) {
        ctx.ui.notify("Titan server not running. Try /titan-start.", "warning");
        return;
      }

      const raw = args?.trim() ?? "";
      const clusterId = /^\d+$/.test(raw) ? Number(raw) : undefined;
      if (raw && clusterId === undefined) {
        ctx.ui.notify("Usage: /titan-clusters or /titan-clusters <cluster-id>", "warning");
        return;
      }

      try {
        const data = await apiGetClusters({ clusterId, limit: 500, detailLimit: 12 });
        ctx.ui.notify(clusterId ? formatClusterDetail(data) : formatClusterSummary(data), data.error ? "warning" : "info");
      } catch (err) {
        ctx.ui.notify(`Failed to inspect clusters: ${err}`, "error");
      }
    },
  };

  pi.registerCommand("titan-clusters", clusterCommand);
  pi.registerCommand("titan-cluster", {
    ...clusterCommand,
    description: "Alias for /titan-clusters",
  });

  pi.registerCommand("titan-cortex", {
    description: "Analyze Titan clusters with Cortex-style graph reasoning: /titan-cortex 3 8 [question]",
    getArgumentCompletions: (prefix: string) => {
      const trimmed = prefix.trim();
      if (!/^[\d,\s]*$/.test(trimmed)) return null;
      return Array.from({ length: 20 }, (_value, index) => {
        const value = String(index + 1);
        return { value, label: value, description: `Analyze cluster ${value}` };
      }).filter((item) => item.value.startsWith(trimmed.split(/\s+/).pop() || ""));
    },
    handler: async (args: string, ctx: ExtensionCommandContext) => {
      const ok = await ensureServerRunning();
      if (!ok) {
        ctx.ui.notify("Titan server not running. Try /titan-start.", "warning");
        return;
      }

      const parsed = parseTitanCortexArgs(args ?? "");
      if (!parsed) {
        ctx.ui.notify("Usage: /titan-cortex <cluster-id> [cluster-id...] [optional question]", "warning");
        return;
      }

      try {
        const data = await apiAnalyzeClusters({
          clusterIds: parsed.clusterIds,
          question: parsed.question,
          limit: 500,
          detailLimit: 8,
        });
        ctx.ui.notify(formatCortexAnalysis(data), data.error ? "warning" : "info");
      } catch (err) {
        ctx.ui.notify(`Failed to analyze clusters: ${err}`, "error");
      }
    },
  });

  pi.registerCommand("titan-status", {
    description: "Check if Titan memory server is running and healthy",
    handler: async (_args, ctx) => {
      const healthy = await isServerHealthy();
      const spoolExists = existsSync(SPOOL_DIR);
      const hasConfig =
        existsSync(resolve(TITAN_HOME, "config", "extraction_models.yaml")) &&
        existsSync(resolve(TITAN_HOME, "config", "embedding_models.yaml"));

      const lines = [
        `Titan Memory Status`,
        `──────────────────`,
        `Server:     ${healthy ? "✅ running" : "❌ not running"}`,
        `Endpoint:   ${TITAN_API_BASE}/health`,
        `Workspace:  ${TITAN_HOME}`,
        `Spool dir:  ${SPOOL_DIR} ${spoolExists ? "✅" : "⚠️ missing"}`,
        `Config:     ${hasConfig ? "✅ configured" : "⚠️ not configured"}`,
        `Session ID: ${sessionId}`,
      ];

      ctx.ui.notify(lines.join("\n"), healthy ? "success" : "warning");
    },
  });

  pi.registerCommand("titan-query", {
    description: "Search Titan memory: /titan-query <what to search for>",
    handler: async (args, ctx) => {
      const query = args?.trim();
      if (!query) {
        ctx.ui.notify("Usage: /titan-query <search query>", "warning");
        return;
      }

      if (!(await isServerHealthy())) {
        ctx.ui.notify("Titan server not running. Try /titan-start.", "warning");
        return;
      }

      try {
        const data = await apiRetrieve(query);
        const memories = data.memories ?? [];
        if (memories.length === 0) {
          ctx.ui.notify("No relevant memories found.", "info");
          return;
        }
        const lines = memories.map(
          (m, i) => `${i + 1}. ${m.text}` + (m.scene_id ? ` [scene: ${m.scene_id}]` : ""),
        );
        ctx.ui.notify(`Memory results:\n${lines.join("\n")}`, "info");
      } catch (err) {
        ctx.ui.notify(`Query failed: ${err}`, "error");
      }
    },
  });

  pi.registerCommand("titan-recent", {
    description: "Browse recent memories stored by Titan",
    handler: async (_args, ctx) => {
      if (!(await isServerHealthy())) {
        ctx.ui.notify("Titan server not running.", "warning");
        return;
      }
      try {
        const data = await apiGetRecentMemories(10);
        const memories = data.memories ?? [];
        if (memories.length === 0) {
          ctx.ui.notify("No memories stored yet.", "info");
          return;
        }
        const lines = memories.map(
          (m, i) => `${i + 1}. ${m.text}` + (m.scene_id ? ` [scene: ${m.scene_id}]` : ""),
        );
        ctx.ui.notify(`Recent memories:\n${lines.join("\n")}`, "info");
      } catch (err) {
        ctx.ui.notify(`Failed: ${err}`, "error");
      }
    },
  });

  pi.registerCommand("titan-save", {
    description: "Manually save a trace packet: /titan-save <goal description>",
    handler: async (args, ctx) => {
      const goal = args?.trim();
      if (!goal) {
        ctx.ui.notify("Usage: /titan-save <what I accomplished>", "warning");
        return;
      }
      if (!(await isServerHealthy())) {
        ctx.ui.notify("Titan server not running.", "warning");
        return;
      }
      try {
        const result = await apiStoreTracePacket({ goal, session_id: sessionId });
        ctx.ui.notify(`Saved: ${JSON.stringify(result)}`, "success");
      } catch (err) {
        ctx.ui.notify(`Failed: ${err}`, "error");
      }
    },
  });

  pi.registerCommand("titan-start", {
    description: "Start the Titan memory server",
    handler: async (_args, ctx) => {
      const ok = await ensureServerRunning();
      if (ok) {
        ctx.ui.notify("Titan server started on port 8002", "success");
      } else {
        ctx.ui.notify("Failed to start Titan server.", "error");
      }
    },
  });

  pi.registerCommand("titan-restart", {
    description: "Restart the Titan memory server (picks up new code changes)",
    handler: async (_args, ctx) => {
      ctx.ui.notify("Restarting Titan server...", "info");
      const ok = await restartOwnedServer();
      if (ok) {
        ctx.ui.notify("Titan server restarted on port 8002 with latest code.", "success");
      } else {
        ctx.ui.notify("Failed to restart Titan server. Try /titan-start.", "error");
      }
    },
  });

  pi.registerCommand("titan-dashboard", {
    description: "Open the Titan memory dashboard — a rich TUI overview of your memory graph",
    handler: async (args: string, ctx: ExtensionCommandContext) => {
      const ok = await ensureServerRunning();
      if (!ok) {
        ctx.ui.notify("Titan server not running. Try /titan-start first.", "warning");
        return;
      }

      const dashboardScript = resolve(REPO_ROOT, "tools", "pi_extension", "titan_dashboard.py");
      if (!existsSync(dashboardScript)) {
        ctx.ui.notify("Dashboard script not found. Run /titan-setup to reinstall.", "error");
        return;
      }

      // Check rich is installed
      const richCheck = await runProcess("python3", ["-c", "import rich"], { cwd: REPO_ROOT });
      if (richCheck.code !== 0) {
        ctx.ui.notify("Installing dashboard dependency (rich)...", "info");
        // macOS Homebrew Python needs --break-system-packages
        await runProcess("python3", ["-m", "pip", "install", "--break-system-packages", "rich"], { cwd: REPO_ROOT });
      }

      const sessionArg = args?.trim() || "";
      const scriptArgs = sessionArg
        ? [dashboardScript, "--session-id", sessionArg]
        : [dashboardScript];

      const result = await runProcess("python3", scriptArgs, {
        cwd: REPO_ROOT,
        env: { ...process.env, TITAN_PI_API_URL: TITAN_API_BASE },
      });

      if (result.code === 0) {
        ctx.ui.notify(result.stdout, "success");
      } else {
        const err = (result.stderr || result.stdout || "unknown error").trim().slice(0, 1000);
        ctx.ui.notify(`Dashboard failed: ${err}`, "error");
      }
    },
  });
}
