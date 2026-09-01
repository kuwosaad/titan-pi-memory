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
import { delimiter, dirname, resolve } from "node:path";
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

let titanPythonCommand: string | null = null;

function pythonCandidates(): string[] {
  const executableNames = process.platform === "win32"
    ? ["python.exe", "python3.exe"]
    : ["python3", "python"];
  const fromPath = (process.env.PATH || "")
    .split(delimiter)
    .filter(Boolean)
    .flatMap((directory) => executableNames.map((name) => resolve(directory, name)))
    .filter((candidate) => existsSync(candidate));
  return Array.from(new Set([
    process.env.TITAN_PYTHON || "",
    ...fromPath,
    ...executableNames,
  ].filter(Boolean)));
}

async function pythonIsCompatible(command: string, requireDependencies: boolean): Promise<boolean> {
  const imports = requireDependencies
    ? REQUIRED_PYTHON_IMPORTS.map((name) => `import ${name}`).join("; ")
    : "import pip";
  const script = `import sys; assert sys.version_info >= (3, 10); ${imports}`;
  const result = await runProcess(command, ["-c", script], { cwd: REPO_ROOT });
  return result.code === 0;
}

async function resolveTitanPython(): Promise<string | null> {
  if (titanPythonCommand && await pythonIsCompatible(titanPythonCommand, true)) {
    return titanPythonCommand;
  }
  for (const candidate of pythonCandidates()) {
    if (await pythonIsCompatible(candidate, true)) {
      titanPythonCommand = candidate;
      return candidate;
    }
  }
  titanPythonCommand = null;
  return null;
}

async function ensurePythonDependencies(): Promise<{ ok: boolean; message: string }> {
  const existing = await resolveTitanPython();
  if (existing) {
    return { ok: true, message: `already installed (${existing})` };
  }

  const requirementsPath = resolve(REPO_ROOT, "requirements.txt");
  if (!existsSync(requirementsPath)) {
    return {
      ok: false,
      message: `requirements.txt not found at ${requirementsPath}`,
    };
  }

  let installPython: string | null = null;
  for (const candidate of pythonCandidates()) {
    if (await pythonIsCompatible(candidate, false)) {
      installPython = candidate;
      break;
    }
  }
  if (!installPython) {
    return { ok: false, message: "Python 3.10+ with pip was not found" };
  }

  const result = await runProcess(
    installPython,
    ["-m", "pip", "install", "-r", requirementsPath],
    { cwd: REPO_ROOT, env: process.env },
  );

  if (await pythonIsCompatible(installPython, true)) {
    titanPythonCommand = installPython;
    return { ok: true, message: `installed (${installPython})` };
  }

  // Some Python installs reject system-wide package writes. Try user scope next.
  const userResult = await runProcess(
    installPython,
    ["-m", "pip", "install", "--user", "-r", requirementsPath],
    { cwd: REPO_ROOT, env: process.env },
  );

  if (await pythonIsCompatible(installPython, true)) {
    titanPythonCommand = installPython;
    return { ok: true, message: `installed with --user (${installPython})` };
  }

  const excerpt = (userResult.stderr || userResult.stdout || result.stderr || result.stdout || "unknown error")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500);
  return { ok: false, message: `install failed: ${excerpt}` };
}

/** Ensure the optional Rich dashboard dependency is importable by this Python. */
async function ensureRichDependency(pythonCommand: string): Promise<boolean> {
  const canImportRich = async (): Promise<boolean> => {
    const result = await runProcess(
      pythonCommand,
      ["-c", "import rich"],
      { cwd: REPO_ROOT, env: process.env },
    );
    return result.code === 0;
  };

  if (await canImportRich()) return true;

  // Try the normal install first, then user-scoped installs for externally
  // managed interpreters (PEP 668). Never launch rich mode until the import
  // check succeeds after an install attempt; the caller may use plain mode.
  for (const installArgs of [
    ["-m", "pip", "install", "rich"],
    ["-m", "pip", "install", "--user", "rich"],
    // pip 23+ may require this explicit opt-in even for a user-site install
    // when the selected interpreter carries the PEP 668 marker. User scope
    // keeps the override out of the managed interpreter's site-packages.
    ["-m", "pip", "install", "--user", "--break-system-packages", "rich"],
  ]) {
    await runProcess(pythonCommand, installArgs, {
      cwd: REPO_ROOT,
      env: process.env,
    });
    if (await canImportRich()) return true;
  }

  return false;
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
        "# OPENCODE_GO_API_KEY=your_key_here",
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

function setExtractionProvider(provider: "opencode_go" | "gemini"): string {
  ensurePiWorkspace();
  const configPath = resolve(TITAN_HOME, "config", "extraction_models.yaml");
  if (!existsSync(configPath)) return configPath;

  let text = readFileSync(configPath, "utf-8");
  text = text.replace(/^current:\s*\S+\s*$/m, `current: ${provider}`);

  if (!/^opencode_go:\s*$/m.test(text)) {
    const opencodeBlock = [
      "opencode_go:",
      "  enabled: true",
      "  api_key_env: OPENCODE_GO_API_KEY",
      "  base_url: https://opencode.ai/zen/go/v1",
      "  model: deepseek-v4-flash",
      "  temperature: 0.1",
      "",
    ].join("\n");
    text = text.replace(/gemini:\n/, `${opencodeBlock}gemini:\n`);
  }

  const dedupBlock = provider === "opencode_go"
    ? [
        "dedup:",
        "  enabled: true",
        "  backend: opencode_go",
        "  api_key_env: OPENCODE_GO_API_KEY",
        "  base_url: https://opencode.ai/zen/go/v1",
        "  model: deepseek-v4-flash",
        "  temperature: 0.1",
        "",
      ].join("\n")
    : [
        "dedup:",
        "  enabled: true",
        "  backend: gemini",
        "  api_key_env: GEMINI_API_KEY",
        "  base_url: https://generativelanguage.googleapis.com/v1beta",
        "  model: gemini-2.5-flash",
        "  temperature: 0.1",
        "  request_timeout: 120",
        "  max_retries: 2",
        "  retry_backoff_seconds: 1.0",
        "",
      ].join("\n");

  text = text.replace(/dedup:\n(?:  .*\n?)*/, dedupBlock);

  writeFileSync(configPath, text, "utf-8");
  return configPath;
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

  const pythonCommand = await resolveTitanPython();
  if (!pythonCommand) {
    console.warn("[titan] Python 3.10+ with Titan dependencies was not found. Run /titan-setup.");
    return false;
  }

  return new Promise((resolvePromise) => {
    const proc = spawn(pythonCommand, [serverScript], {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        TITAN_PI_ADAPTER: "1",
        TITAN_PI_DEFAULT_AGENT: process.env.TITAN_AGENT_NAME || "pi",
        TITAN_PI_DEFAULT_HOME: TITAN_HOME,
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
  /** Agent namespace that owns this memory (for federated recall). */
  source_agent?: string;
  ts?: string;
  [key: string]: unknown;
}

/** Add federated provenance to human-readable memory result lines. */
function memorySourceRef(memory: TitanMemory): string {
  return memory.source_agent ? ` [source: ${memory.source_agent}]` : "";
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

interface TitanRuntimeResponse {
  agent_name?: string;
  titan_home?: string;
  base_dir?: string;
  trace_dir?: string;
  memory_backend?: string;
  memory_capabilities?: {
    memory_store?: boolean;
    lnn_state_store?: boolean;
    lnn_status?: string;
    adapter?: string;
    [key: string]: unknown;
  };
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

interface TitanPatternStatus {
  memories_total?: number;
  processed_current?: number;
  unprocessed?: number;
  candidate_patterns?: number;
  accepted_patterns?: number;
  processor_version?: string;
  processor_config_hash?: string;
  last_run?: Record<string, unknown> | null;
  error?: string;
  [key: string]: unknown;
}

interface TitanPatternEvidenceInput {
  memory_id: string;
  scene_id?: string;
  role?: string;
  score?: number;
}

interface TitanPatternCreateInput {
  title: string;
  kind?: string;
  scope?: string;
  status?: string;
  summary: string;
  recommended_behavior: string;
  trigger_terms?: string[];
  evidence?: TitanPatternEvidenceInput[];
  confidence?: number;
  applies_when?: string;
  does_not_apply_when?: string;
  actionability?: number;
  retrieval_value?: number;
  canonical_key?: string;
  mined_run_id?: string;
  source?: string;
}

interface TitanPatternMarkProcessedInput {
  memory_ids: string[];
  run_id?: string;
  status?: string;
  pattern_ids?: string[];
  error?: string;
  mode?: string;
}

interface TitanPatternEvidencePacket {
  packet_id?: string;
  packet_type?: string;
  seed_memory_ids?: string[];
  context_memory_ids?: string[];
  selection_reasons?: string[];
  temporal_context?: unknown[];
  semantic_context?: unknown[];
  entity_context?: unknown[];
  pattern_context?: unknown[];
  graph_context?: unknown;
  questions_for_agent?: string[];
  unprocessed_memory_ids?: string[];
  related_old_memory_ids?: string[];
  cluster_summaries?: Record<string, unknown>[];
  central_memories?: Record<string, unknown>[];
  bridge_memories?: Record<string, unknown>[];
  tensions?: Record<string, unknown>[];
  suggested_trigger_terms?: string[];
  suggested_kind?: string;
  suggested_scope?: string;
  confidence_hints?: Record<string, unknown>;
  processor_version?: string;
  processor_config_hash?: string;
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
  params.set("include_scenes", "false");
  if (date_from) params.set("from_date", date_from);
  if (date_to) params.set("to_date", date_to);
  const url = `${TITAN_API_BASE}/api/retrieve?${params.toString()}`;
  const res = await fetch(url);
  return (await safeJson(res)) as unknown as TitanRetrieveResponse;
}

async function apiGetScene(
  sceneId: string,
  source_agent?: string,
): Promise<Record<string, unknown>> {
  const params = new URLSearchParams();
  if (source_agent) params.set("source_agent", source_agent);
  const query = params.toString();
  const url = `${TITAN_API_BASE}/api/scenes/${encodeURIComponent(sceneId)}${query ? `?${query}` : ""}`;
  const res = await fetch(url);
  return safeJson(res);
}

async function apiGetRecentMemories(limit = 8): Promise<{ memories: TitanMemory[]; count: number; total?: number }> {
  const res = await fetch(`${TITAN_API_BASE}/api/memories?limit=${limit}`);
  return (await safeJson(res)) as unknown as { memories: TitanMemory[]; count: number; total?: number };
}

async function apiGetRuntime(): Promise<TitanRuntimeResponse> {
  const res = await fetch(`${TITAN_API_BASE}/api/runtime`);
  return (await safeJson(res)) as TitanRuntimeResponse;
}

async function apiGetClusters(options: {
  clusterId?: number;
  sessionId?: string;
  limit?: number;
  detailLimit?: number;
} = {}): Promise<TitanClustersResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 0));
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
  params.set("limit", String(options.limit ?? 0));
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

async function apiGetPatternStatus(): Promise<TitanPatternStatus> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/status`);
  return (await safeJson(res)) as unknown as TitanPatternStatus;
}

async function apiGetPatternEvidencePacket(options: {
  batchSize?: number;
  contextLimit?: number;
  sessionId?: string;
  mode?: string;
  packetType?: string;
} = {}): Promise<TitanPatternEvidencePacket> {
  const body: Record<string, unknown> = {};
  if (options.batchSize !== undefined) body.batch_size = options.batchSize;
  if (options.contextLimit !== undefined) body.context_limit = options.contextLimit;
  if (options.sessionId) body.session_id = options.sessionId;
  if (options.mode) body.mode = options.mode;
  if (options.packetType) body.packet_type = options.packetType;
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/evidence-packet`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await safeJson(res)) as unknown as TitanPatternEvidencePacket;
}

async function apiListPatterns(options: { status?: string; scope?: string; limit?: number } = {}): Promise<Record<string, unknown>> {
  const params = new URLSearchParams();
  if (options.status) params.set("status", options.status);
  if (options.scope) params.set("scope", options.scope);
  params.set("limit", String(options.limit ?? 50));
  const suffix = params.toString();
  const res = await fetch(`${TITAN_API_BASE}/api/patterns${suffix ? `?${suffix}` : ""}`);
  return safeJson(res);
}

async function apiGetPattern(patternId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/${encodeURIComponent(patternId)}`);
  return safeJson(res);
}

async function apiCreatePattern(payload: TitanPatternCreateInput): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return safeJson(res);
}

async function apiUpdatePatternStatus(patternId: string, status: "accept" | "reject"): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/${encodeURIComponent(patternId)}/${status}`, { method: "POST" });
  return safeJson(res);
}

async function apiMarkPatternsProcessed(payload: TitanPatternMarkProcessedInput): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/mark-processed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return safeJson(res);
}

async function apiExportPatternBundle(options: {
  statuses?: string[];
  includeMemorySummaries?: boolean;
  includeProgress?: boolean;
  limit?: number;
} = {}): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/bundle/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      statuses: options.statuses ?? ["accepted"],
      include_memory_summaries: options.includeMemorySummaries ?? true,
      include_progress: options.includeProgress ?? true,
      limit: options.limit ?? 500,
    }),
  });
  return safeJson(res);
}

async function apiImportPatternBundle(bundle: Record<string, unknown>, options: { overwrite?: boolean; importProgress?: boolean } = {}): Promise<Record<string, unknown>> {
  const res = await fetch(`${TITAN_API_BASE}/api/patterns/bundle/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bundle,
      overwrite: options.overwrite ?? false,
      import_progress: options.importProgress ?? true,
    }),
  });
  return safeJson(res);
}

function formatClusterSummary(data: TitanClustersResponse): string {
  if (data.error) return data.error;
  const analyzed = data.raw_memory_count ?? data.memory_count;
  const limited = data.total_memory_count && analyzed < data.total_memory_count ? ` of ${data.total_memory_count}` : "";
  const header = `Titan clusters: ${data.cluster_count} topics · ${analyzed}${limited} memories analyzed · ${data.connection_count} connections`;
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

function formatPatternStatus(data: TitanPatternStatus): string {
  if (data.error) return String(data.error);
  const lastRun = data.last_run && typeof data.last_run === "object"
    ? `${data.last_run["status"] ?? "unknown"} · ${data.last_run["finished_at"] ?? data.last_run["started_at"] ?? "no timestamp"}`
    : "none";
  return [
    "Titan Pattern Status",
    "────────────────────",
    `Total memories:      ${data.memories_total ?? 0}`,
    `Processed current:   ${data.processed_current ?? 0}`,
    `Unprocessed:         ${data.unprocessed ?? 0}`,
    `Candidate patterns:  ${data.candidate_patterns ?? 0}`,
    `Accepted patterns:   ${data.accepted_patterns ?? 0}`,
    `Processor:           ${data.processor_version ?? "?"}`,
    `Config hash:         ${data.processor_config_hash ?? "?"}`,
    `Last run:            ${lastRun}`,
  ].join("\n");
}

function formatPatternList(data: Record<string, unknown>): string {
  if (data.error) return String(data.error);
  const patterns = Array.isArray(data.patterns) ? data.patterns as Record<string, unknown>[] : [];
  if (patterns.length === 0) return "No Titan patterns found.";
  const lines = patterns.map((pattern, index) => {
    const title = String(pattern.title ?? "untitled");
    const status = String(pattern.status ?? "?");
    const kind = String(pattern.kind ?? "?");
    const confidence = typeof pattern.confidence === "number" ? pattern.confidence.toFixed(2) : String(pattern.confidence ?? "?");
    return `${index + 1}. ${title} (${status}, ${kind}, confidence ${confidence})\n   id: ${pattern.id ?? "?"}`;
  });
  return [`Titan patterns (${patterns.length})`, "", ...lines].join("\n");
}

function parseTitanPatternsArgs(args: string): { action: string; rest: string; flags: Record<string, string | true> } {
  const tokens = (args || "").trim().split(/\s+/).filter(Boolean);
  const action = tokens.shift() || "list";
  const restTokens: string[] = [];
  const flags: Record<string, string | true> = {};
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (!token.startsWith("--")) {
      restTokens.push(token);
      continue;
    }
    const name = token.slice(2).replace(/-/g, "_");
    const next = tokens[index + 1];
    if (next && !next.startsWith("--")) {
      flags[name] = next;
      index += 1;
    } else {
      flags[name] = true;
    }
  }
  return { action, rest: restTokens.join(" "), flags };
}

function numberFlag(flags: Record<string, string | true>, name: string): number | undefined {
  const raw = flags[name];
  if (typeof raw !== "string") return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function buildPatternBackfillPrompt(packet: TitanPatternEvidencePacket, status: TitanPatternStatus): string {
  const unprocessedIds = packet.unprocessed_memory_ids ?? [];
  return [
    "Run the Titan pattern mining workflow for this evidence packet.",
    "",
    "Goal:",
    "1. Inspect the evidence packet below.",
    "2. Draft only evidence-backed candidate pattern cards. Do not invent patterns.",
    "3. For each useful repeated behavior, call the `titan_patterns` tool with action `create_pattern`.",
    "4. Each created pattern must include title, kind, scope, summary, recommended_behavior, trigger_terms, confidence, and evidence memory ids/scene ids.",
    "   The backend requires at least 3 evidence items with role `support` and at least 2 evidence scenes.",
    "5. If no durable pattern is justified, create nothing.",
    "6. When inspection is complete, call `titan_patterns` with action `mark_processed` for every seed/unprocessed memory id from this packet, including memories that produced no pattern.",
    "7. Summarize created pattern ids, skipped evidence, and any tensions that need human review.",
    "",
    "Rules:",
    "- keep new patterns as status `candidate`; do not accept them",
    "- recommended_behavior must tell future agents what to do differently",
    "- use support/central/bridge/contradict evidence roles when clear",
    "- treat tensions as signals to inspect, not final contradictions",
    "- when pattern_context is present, compare it against new evidence and call out stale or superseded guidance instead of blindly creating duplicates",
    "- mark only seed/unprocessed memory ids as processed; related/context memories are support evidence and must not be marked processed unless they are also seed ids",
    "- if evidence has fewer than 3 support memories or fewer than 2 scenes, only create a pattern when it is clearly a user preference/decision",
    "",
    `Current status: ${status.unprocessed ?? 0} unprocessed · ${status.candidate_patterns ?? 0} candidate · ${status.accepted_patterns ?? 0} accepted`,
    `Processor: ${packet.processor_version ?? status.processor_version ?? "?"} / ${packet.processor_config_hash ?? status.processor_config_hash ?? "?"}`,
    `Unprocessed ids to mark when done: ${unprocessedIds.join(", ") || "none"}`,
    "",
    "Evidence packet JSON:",
    "```json",
    JSON.stringify(packet, null, 2),
    "```",
  ].join("\n");
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
    ctx.ui.setStatus("titan-memory", "TITAN CHECKING");
    const serverOk = await ensureServerRunning();
    if (serverOk && hasConfig) {
      ctx.ui.setStatus("titan-memory", "TITAN READY");
      ctx.ui.notify("Titan memory ready", "success");
    } else if (!hasConfig) {
      ctx.ui.setStatus("titan-memory", "TITAN UNCONFIGURED");
      ctx.ui.notify(
        "Titan memory not configured. Run /titan-setup to get started.",
        "warning",
      );
    } else if (!serverOk) {
      ctx.ui.setStatus("titan-memory", "TITAN OFFLINE");
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
      content: text,
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
      content: text,
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
    name: "titan",
    label: "Titan Memory",
    description:
      "First-class Titan memory tool for recall, scene recovery, manual saves, recent memories, graph clusters, Cortex analysis, and health checks.",
    promptSnippet:
      "Use Titan memory for cross-session recall, project history, decisions, scene recovery, memory graph analysis, and durable memory saves",
    promptGuidelines: [
      "Before using titan for recall, project history, decisions, implementation archaeology, work reports, or any non-trivial memory query, first read and apply the titan-memory-workflow skill.",
      "Treat Titan memories as semantic pointers; after titan query_memories returns scene IDs, expand important scenes and verify concrete repo facts with git or file inspection when needed.",
    ],
    parameters: Type.Object({
      action: Type.Union([
        Type.Literal("query_memories"),
        Type.Literal("get_scene_context"),
        Type.Literal("store_trace_packet"),
        Type.Literal("recent"),
        Type.Literal("inspect_clusters"),
        Type.Literal("analyze_clusters"),
        Type.Literal("doctor"),
      ], {
        description:
          "Titan operation to run. Use query_memories for recall, get_scene_context for scene IDs, store_trace_packet to remember decisions/outcomes, recent for latest memories, inspect_clusters/analyze_clusters for graph work, doctor for health.",
      }),
      query: Type.Optional(Type.String({ description: "Search query for query_memories. Leave empty only with date_from/date_to." })),
      limit: Type.Optional(Type.Number({ description: "Max results or memories to process (default depends on action)." })),
      date_from: Type.Optional(Type.String({ description: "Start date/time filter for query_memories, ISO 8601." })),
      date_to: Type.Optional(Type.String({ description: "End date/time filter for query_memories, ISO 8601." })),
      scene_id: Type.Optional(Type.String({ description: "Scene ID for get_scene_context." })),
      source_agent: Type.Optional(Type.String({ description: "Optional owning agent namespace for federated scene recall." })),
      goal: Type.Optional(Type.String({ description: "Goal for store_trace_packet." })),
      thoughts: Type.Optional(Type.String({ description: "Optional thoughts/decisions for store_trace_packet." })),
      outcome: Type.Optional(Type.String({ description: "Optional outcome for store_trace_packet." })),
      cluster_id: Type.Optional(Type.Number({ description: "Cluster ID for inspect_clusters detail mode." })),
      cluster_ids: Type.Optional(Type.String({ description: "Comma-separated cluster IDs for analyze_clusters, e.g. '3,8'." })),
      question: Type.Optional(Type.String({ description: "Optional question to guide analyze_clusters." })),
      detail_limit: Type.Optional(Type.Number({ description: "Representative item limit for cluster actions." })),
      session_id: Type.Optional(Type.String({ description: "Optional Titan session ID; use this for an external source such as a Glass meeting." })),
      event_id: Type.Optional(Type.String({ description: "Stable dedupe event ID for store_trace_packet." })),
    }),
    async execute(_toolCallId, params) {
      if (params.action === "doctor") {
        const healthy = await isServerHealthy();
        let stats = {};
        let runtime: TitanRuntimeResponse = {};
        if (healthy) {
          try {
            const data = await apiGetRecentMemories(1);
            stats = { memory_count: data.total ?? data.count };
          } catch {
            stats = { memory_count: "unknown" };
          }
          try {
            runtime = await apiGetRuntime();
          } catch {
            runtime = {};
          }
        }

        const reportedHome = runtime.titan_home || TITAN_HOME;
        const reportedSpoolDir = runtime.trace_dir || SPOOL_DIR;
        const hasConfig =
          existsSync(resolve(reportedHome, "config", "extraction_models.yaml")) &&
          existsSync(resolve(reportedHome, "config", "embedding_models.yaml"));
        const spoolExists = existsSync(reportedSpoolDir);
        const capability = runtime.memory_capabilities;
        const lnnStatus = capability?.lnn_status || (
          capability?.lnn_state_store === false
            ? "unsupported for selected backend"
            : capability?.lnn_state_store === true
              ? "enabled"
              : "unknown"
        );

        return {
          content: [{
            type: "text" as const,
            text: [
              `Titan Status:`,
              `  Server:     ${healthy ? "✅ running" : "❌ not running"} (${TITAN_API_BASE})`,
              `  Workspace:  ${reportedHome}`,
              `  Spool dir:  ${reportedSpoolDir} ${spoolExists ? "✅" : "⚠️ missing"}`,
              `  Config:     ${hasConfig ? "✅" : "⚠️ not configured (run /titan-setup)"}`,
              runtime.memory_backend ? `  Backend:    ${runtime.memory_backend}` : "",
              capability ? `  LNN:        ${lnnStatus}` : "",
              `  Session ID: ${sessionId}`,
              healthy ? `  Memories:   ${JSON.stringify(stats)}` : "",
            ].filter(Boolean).join("\n"),
          }],
          details: { healthy, titan_home: reportedHome, trace_dir: reportedSpoolDir, runtime, ...stats },
        };
      }

      if (!(await isServerHealthy())) {
        return {
          content: [{ type: "text" as const, text: "Titan server is not running. Tell the user to check /titan-status." }],
        };
      }

      if (params.action === "query_memories") {
        const query = params.query ?? "";
        const data = await apiRetrieve(query, params.limit ?? 8, params.date_from, params.date_to);
        const memories = data.memories ?? [];
        if (memories.length === 0) {
          return { content: [{ type: "text" as const, text: "No sufficiently relevant memories found." }] };
        }
        const lines = memories.map((m, i) => {
          const sceneRef = m.scene_id ? ` [scene: ${m.scene_id}]` : "";
          return `${i + 1}. ${m.text}${sceneRef}${memorySourceRef(m)}`;
        });
        return {
          content: [{ type: "text" as const, text: lines.join("\n") }],
          details: { count: data.count, query, scene_count: 0 },
        };
      }

      if (params.action === "get_scene_context") {
        if (!params.scene_id) {
          return { content: [{ type: "text" as const, text: "scene_id is required for get_scene_context." }] };
        }
        const data = await apiGetScene(params.scene_id, params.source_agent);
        if ("error" in data) {
          return { content: [{ type: "text" as const, text: String(data.error) }] };
        }
        return {
          content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
          details: { scene_id: params.scene_id },
        };
      }

      if (params.action === "store_trace_packet") {
        if (!params.goal) {
          return { content: [{ type: "text" as const, text: "goal is required for store_trace_packet." }] };
        }
        const payload: Record<string, unknown> = {
          goal: params.goal,
          session_id: params.session_id || sessionId,
        };
        if (params.event_id) payload.event_id = params.event_id;
        if (params.thoughts) payload.thoughts = params.thoughts;
        if (params.outcome) payload.outcome = params.outcome;
        const result = await apiStoreTracePacket(payload);
        return {
          content: [{ type: "text" as const, text: `Trace packet stored: ${JSON.stringify(result)}` }],
          details: result,
        };
      }

      if (params.action === "recent") {
        const data = await apiGetRecentMemories(params.limit ?? 8);
        const memories = data.memories ?? [];
        if (memories.length === 0) {
          return { content: [{ type: "text" as const, text: "No memories yet." }] };
        }
        const lines = memories.map((m, i) => {
          const sceneRef = m.scene_id ? ` [scene: ${m.scene_id}]` : "";
          return `${i + 1}. ${m.text}${sceneRef}${memorySourceRef(m)}`;
        });
        return {
          content: [{ type: "text" as const, text: lines.join("\n") }],
          details: { count: data.count },
        };
      }

      if (params.action === "inspect_clusters") {
        const data = await apiGetClusters({
          clusterId: params.cluster_id,
          limit: params.limit ?? 500,
          detailLimit: params.detail_limit ?? 12,
          sessionId: params.session_id,
        });
        return {
          content: [{ type: "text" as const, text: params.cluster_id ? formatClusterDetail(data) : formatClusterSummary(data) }],
          details: data,
        };
      }

      if (params.action === "analyze_clusters") {
        if (!params.cluster_ids) {
          return { content: [{ type: "text" as const, text: "cluster_ids is required for analyze_clusters." }] };
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
      }

      return { content: [{ type: "text" as const, text: `Unknown Titan action: ${params.action}` }] };
    },
  });

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
      "Before using titan_query_memories for recall, project history, decisions, implementation archaeology, work reports, or any non-trivial memory query, first read and apply the titan-memory-workflow skill.",
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
          content: [{ type: "text" as const, text: "No sufficiently relevant memories found." }],
        };
      }

      const lines = memories.map((m, i) => {
        const sceneRef = m.scene_id ? ` [scene: ${m.scene_id}]` : "";
        return `${i + 1}. ${m.text}${sceneRef}${memorySourceRef(m)}`;
      });

      return {
        content: [{ type: "text" as const, text: lines.join("\n") }],
        details: { count: data.count, query: params.query, scene_count: 0 },
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
      source_agent: Type.Optional(Type.String({
        description: "Optional owning agent namespace for federated scene recall",
      })),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return {
          content: [
            { type: "text" as const, text: "Titan server is not running." },
          ],
        };
      }
      const data = await apiGetScene(params.scene_id, params.source_agent);
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
      session_id: Type.Optional(
        Type.String({ description: "Stable external source session ID, such as glass:<session-id>." }),
      ),
      event_id: Type.Optional(
        Type.String({ description: "Stable dedupe event ID, such as glass-meeting:<session-id>:<transcript-hash>." }),
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
        session_id: params.session_id || sessionId,
      };
      if (params.event_id) payload.event_id = params.event_id;
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
        return `${i + 1}. ${m.text}${sceneRef}${memorySourceRef(m)}`;
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
        Type.Number({ description: "Max memories to cluster; use 0 for all memories (default: all)", default: 0 }),
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
        limit: params.limit ?? 0,
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
        Type.Number({ description: "Max memories to cluster before analysis; use 0 for all memories (default: all)", default: 0 }),
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
        limit: params.limit ?? 0,
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
    name: "titan_patterns",
    label: "Titan Patterns",
    description:
      "Manage Titan learned patterns. Use for pattern mining workflows: get status, fetch evidence packets, create candidate patterns, list/show/accept/reject patterns, and mark inspected memories processed.",
    promptSnippet: "Create and manage evidence-backed Titan pattern candidates",
    promptGuidelines: [
      "Use titan_patterns during /titan-patterns backfill to create only evidence-backed candidate patterns.",
      "Never auto-accept a pattern during mining; created patterns should stay candidate until explicitly accepted.",
      "After inspecting an evidence packet, mark only seed/unprocessed memory ids processed; use context ids only as evidence.",
    ],
    parameters: Type.Object({
      action: Type.Union([
        Type.Literal("status"),
        Type.Literal("evidence_packet"),
        Type.Literal("create_pattern"),
        Type.Literal("list_patterns"),
        Type.Literal("get_pattern"),
        Type.Literal("accept_pattern"),
        Type.Literal("reject_pattern"),
        Type.Literal("mark_processed"),
      ], { description: "Pattern operation to run." }),
      pattern_id: Type.Optional(Type.String({ description: "Pattern id for get/accept/reject." })),
      status: Type.Optional(Type.String({ description: "Pattern status filter or processing status." })),
      scope: Type.Optional(Type.String({ description: "Pattern scope or list filter." })),
      limit: Type.Optional(Type.Number({ description: "List limit." })),
      batch_size: Type.Optional(Type.Number({ description: "Evidence packet batch size." })),
      context_limit: Type.Optional(Type.Number({ description: "Evidence packet related-context limit." })),
      session_id: Type.Optional(Type.String({ description: "Optional session id for evidence packet scope." })),
      packet_mode: Type.Optional(Type.String({ description: "Evidence packet mode, e.g. adaptive or chronological." })),
      packet_type: Type.Optional(Type.String({ description: "Adaptive packet type: high_signal, semantic_cluster, entity, bridge, contradiction, scene_episode, or chronological_fallback." })),
      title: Type.Optional(Type.String({ description: "Candidate pattern title." })),
      kind: Type.Optional(Type.String({ description: "Pattern kind: codebase, workflow, failure, preference, product, distribution." })),
      summary: Type.Optional(Type.String({ description: "Short evidence-backed pattern summary." })),
      recommended_behavior: Type.Optional(Type.String({ description: "What future agents should do differently." })),
      applies_when: Type.Optional(Type.String({ description: "When the pattern applies." })),
      does_not_apply_when: Type.Optional(Type.String({ description: "When the pattern should not apply." })),
      confidence: Type.Optional(Type.Number({ description: "Confidence from 0 to 1." })),
      actionability: Type.Optional(Type.Number({ description: "Actionability score from 0 to 1." })),
      retrieval_value: Type.Optional(Type.Number({ description: "Retrieval value score from 0 to 1." })),
      trigger_terms: Type.Optional(Type.Array(Type.String(), { description: "Trigger terms for retrieval." })),
      evidence: Type.Optional(Type.Array(Type.Object({
        memory_id: Type.String(),
        scene_id: Type.Optional(Type.String()),
        role: Type.Optional(Type.String()),
        score: Type.Optional(Type.Number()),
      }), { description: "Evidence records for create_pattern." })),
      memory_ids: Type.Optional(Type.Array(Type.String(), { description: "Memory ids for mark_processed." })),
      pattern_ids: Type.Optional(Type.Array(Type.String(), { description: "Created pattern ids for mark_processed." })),
      error: Type.Optional(Type.String({ description: "Optional processing error." })),
      mode: Type.Optional(Type.String({ description: "Processing mode, e.g. backfill or incremental." })),
    }),
    async execute(_toolCallId, params) {
      if (!(await isServerHealthy())) {
        return { content: [{ type: "text" as const, text: "Titan server is not running. Tell the user to run /titan-start." }] };
      }

      if (params.action === "status") {
        const data = await apiGetPatternStatus();
        return { content: [{ type: "text" as const, text: formatPatternStatus(data) }], details: data };
      }

      if (params.action === "evidence_packet") {
        const data = await apiGetPatternEvidencePacket({
          batchSize: params.batch_size,
          contextLimit: params.context_limit,
          sessionId: params.session_id,
          mode: params.packet_mode,
          packetType: params.packet_type,
        });
        return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }], details: data };
      }

      if (params.action === "list_patterns") {
        const data = await apiListPatterns({ status: params.status, scope: params.scope, limit: params.limit });
        return { content: [{ type: "text" as const, text: formatPatternList(data) }], details: data };
      }

      if (params.action === "get_pattern") {
        if (!params.pattern_id) return { content: [{ type: "text" as const, text: "pattern_id is required." }] };
        const data = await apiGetPattern(params.pattern_id);
        return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }], details: data };
      }

      if (params.action === "accept_pattern" || params.action === "reject_pattern") {
        if (!params.pattern_id) return { content: [{ type: "text" as const, text: "pattern_id is required." }] };
        const data = await apiUpdatePatternStatus(params.pattern_id, params.action === "accept_pattern" ? "accept" : "reject");
        return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }], details: data };
      }

      if (params.action === "mark_processed") {
        if (!params.memory_ids || params.memory_ids.length === 0) {
          return { content: [{ type: "text" as const, text: "memory_ids is required for mark_processed." }] };
        }
        const data = await apiMarkPatternsProcessed({
          memory_ids: params.memory_ids,
          pattern_ids: params.pattern_ids,
          status: params.status,
          error: params.error,
          mode: params.mode ?? "backfill",
        });
        return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }], details: data };
      }

      if (params.action === "create_pattern") {
        if (!params.title || !params.summary || !params.recommended_behavior) {
          return { content: [{ type: "text" as const, text: "title, summary, and recommended_behavior are required for create_pattern." }] };
        }
        const data = await apiCreatePattern({
          title: params.title,
          kind: params.kind ?? "workflow",
          scope: params.scope ?? "repo",
          status: "candidate",
          summary: params.summary,
          recommended_behavior: params.recommended_behavior,
          trigger_terms: params.trigger_terms ?? [],
          evidence: params.evidence ?? [],
          confidence: params.confidence ?? 0,
          applies_when: params.applies_when,
          does_not_apply_when: params.does_not_apply_when,
          actionability: params.actionability ?? 0,
          retrieval_value: params.retrieval_value ?? 0,
          source: "pi-agent",
        });
        return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }], details: data };
      }

      return { content: [{ type: "text" as const, text: `Unknown Titan pattern action: ${params.action}` }] };
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
        "OPENCODE_GO_API_KEY=...",
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
        "OpenCode Go / DeepSeek V4 Flash (OPENCODE_GO_API_KEY)",
        "Gemini / AI Studio (GEMINI_API_KEY)",
      ]);
      if (!provider) return;

      const useOpenCodeGo = provider.startsWith("OpenCode Go");
      const keyName = useOpenCodeGo ? "OPENCODE_GO_API_KEY" : "GEMINI_API_KEY";
      const value = await ctx.ui.input(
        `Paste your ${keyName}`,
        useOpenCodeGo ? "sk-opencode-..." : "AIza...",
      );
      const cleaned = value?.trim();
      if (!cleaned) {
        ctx.ui.notify("No API key entered. Nothing changed.", "warning");
        return;
      }

      const envPath = upsertEnvKey(keyName, cleaned);
      const configPath = setExtractionProvider(useOpenCodeGo ? "opencode_go" : "gemini");
      const serverOk = await restartOwnedServer();
      const suffix = cleaned.length >= 4 ? cleaned.slice(-4) : "****";

      ctx.ui.notify(
        [
          "Titan API key saved.",
          `Provider: ${provider}`,
          `Saved: ${keyName}=...${suffix}`,
          `Secrets: ${envPath}`,
          `Config: ${configPath}`,
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

  pi.registerCommand("titan-pattern-graph", {
    description: "Open the Titan learned pattern graph in your browser",
    handler: async (args: string, ctx: ExtensionCommandContext) => {
      const ok = await ensureServerRunning();
      if (!ok) {
        ctx.ui.notify("Titan server is not running, so I can't open the pattern graph.", "error");
        return;
      }

      const trimmed = args?.trim();
      const url = new URL(`${TITAN_API_BASE}/pattern-graph`);
      if (trimmed) {
        const maybeLimit = Number(trimmed);
        if (!Number.isFinite(maybeLimit) || maybeLimit <= 0) {
          ctx.ui.notify("Usage: /titan-pattern-graph [limit]", "warning");
          return;
        }
        url.searchParams.set("limit", String(Math.floor(maybeLimit)));
      }

      try {
        await openUrl(url.toString());
        ctx.ui.notify(
          [
            "Titan pattern graph opened in your browser.",
            url.toString(),
            "Node color = status. Node size = confidence + evidence count. Edges = shared evidence, triggers, supports, contradicts, supersedes.",
          ].join("\n"),
          "success",
        );
      } catch (err) {
        ctx.ui.notify(`Could not open Titan pattern graph: ${err}`, "error");
      }
    },
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
        const data = await apiGetClusters({ clusterId, limit: 0, detailLimit: 12 });
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

  pi.registerCommand("titan-patterns", {
    description: "Review learned Titan patterns or run pattern mining: /titan-patterns status|backfill|mine|accept <id>|reject <id>",
    getArgumentCompletions: (prefix: string) => {
      const options = ["status", "backfill", "mine", "list", "candidates", "accepted", "accept", "reject", "show", "export", "import"];
      const token = (prefix || "").trim().split(/\s+/).pop() || "";
      return options
        .filter((value) => value.startsWith(token))
        .map((value) => ({ value, label: value, description: `/titan-patterns ${value}` }));
    },
    handler: async (args: string, ctx: ExtensionCommandContext) => {
      const ok = await ensureServerRunning();
      if (!ok) {
        ctx.ui.notify("Titan server not running. Try /titan-start.", "warning");
        return;
      }

      const parsed = parseTitanPatternsArgs(args ?? "");
      const action = parsed.action;
      try {
        if (action === "status") {
          const data = await apiGetPatternStatus();
          ctx.ui.notify(formatPatternStatus(data), data.error ? "warning" : "info");
          return;
        }

        if (action === "list" || action === "candidates" || action === "accepted") {
          const data = await apiListPatterns({
            status: action === "candidates" ? "candidate" : action === "accepted" ? "accepted" : (typeof parsed.flags.status === "string" ? parsed.flags.status : undefined),
            scope: typeof parsed.flags.scope === "string" ? parsed.flags.scope : undefined,
            limit: numberFlag(parsed.flags, "limit") ?? 50,
          });
          ctx.ui.notify(formatPatternList(data), data.error ? "warning" : "info");
          return;
        }

        if (action === "show") {
          const patternId = parsed.rest.trim();
          if (!patternId) {
            ctx.ui.notify("Usage: /titan-patterns show <pattern-id>", "warning");
            return;
          }
          const data = await apiGetPattern(patternId);
          ctx.ui.notify(JSON.stringify(data, null, 2), data.error ? "warning" : "info");
          return;
        }

        if (action === "accept" || action === "reject") {
          const patternId = parsed.rest.trim();
          if (!patternId) {
            ctx.ui.notify(`Usage: /titan-patterns ${action} <pattern-id>`, "warning");
            return;
          }
          const data = await apiUpdatePatternStatus(patternId, action === "accept" ? "accept" : "reject");
          ctx.ui.notify(JSON.stringify(data, null, 2), data.error ? "warning" : "success");
          return;
        }

        if (action === "export") {
          const outputPath = resolve(TITAN_HOME, parsed.rest.trim() || "titan-patterns.json");
          const statuses = parsed.flags.include_candidates ? ["accepted", "candidate"] : ["accepted"];
          const bundle = await apiExportPatternBundle({
            statuses,
            includeMemorySummaries: !parsed.flags.no_memory_summaries,
            includeProgress: !parsed.flags.no_progress,
            limit: numberFlag(parsed.flags, "limit") ?? 500,
          });
          if (bundle.error) {
            ctx.ui.notify(String(bundle.error), "warning");
            return;
          }
          writeFileSync(outputPath, `${JSON.stringify(bundle, null, 2)}\n`, "utf-8");
          const patterns = Array.isArray(bundle.patterns) ? bundle.patterns.length : 0;
          const evidence = Array.isArray(bundle.evidence) ? bundle.evidence.length : 0;
          ctx.ui.notify(`Titan pattern bundle exported: ${outputPath}\nPatterns: ${patterns}; evidence: ${evidence}`, "success");
          return;
        }

        if (action === "import") {
          const inputPath = parsed.rest.trim();
          if (!inputPath) {
            ctx.ui.notify("Usage: /titan-patterns import <path> [--overwrite] [--no-progress]", "warning");
            return;
          }
          const bundle = JSON.parse(readFileSync(resolve(TITAN_HOME, inputPath), "utf-8")) as Record<string, unknown>;
          const result = await apiImportPatternBundle(bundle, {
            overwrite: Boolean(parsed.flags.overwrite),
            importProgress: !parsed.flags.no_progress,
          });
          ctx.ui.notify(JSON.stringify(result, null, 2), result.error ? "warning" : "success");
          return;
        }

        if (action === "backfill" || action === "mine") {
          const status = await apiGetPatternStatus();
          if (status.error) {
            ctx.ui.notify(formatPatternStatus(status), "warning");
            return;
          }
          const packet = await apiGetPatternEvidencePacket({
            batchSize: numberFlag(parsed.flags, "batch_size"),
            contextLimit: numberFlag(parsed.flags, "context_limit"),
            sessionId: action === "mine" ? sessionId : (typeof parsed.flags.session_id === "string" ? parsed.flags.session_id : undefined),
            mode: typeof parsed.flags.mode === "string" ? parsed.flags.mode : undefined,
            packetType: typeof parsed.flags.packet_type === "string" ? parsed.flags.packet_type : undefined,
          });
          if (packet.error) {
            ctx.ui.notify(String(packet.error), "warning");
            return;
          }
          const ids = packet.unprocessed_memory_ids ?? [];
          if (ids.length === 0) {
            ctx.ui.notify("No unprocessed memories for the current pattern miner.", "info");
            return;
          }
          const prompt = buildPatternBackfillPrompt(packet, status);
          ctx.ui.notify(`Pattern evidence packet ready (${ids.length} unprocessed memories). Handing it to the Pi agent.`, "info");
          await ctx.waitForIdle();
          pi.sendUserMessage(prompt);
          return;
        }

        ctx.ui.notify(
          [
            "Usage:",
            "/titan-patterns status",
            "/titan-patterns backfill [--batch-size N] [--context-limit N] [--packet-type high_signal]",
            "/titan-patterns mine",
            "/titan-patterns list|candidates|accepted",
            "/titan-patterns show <id>",
            "/titan-patterns accept <id>",
            "/titan-patterns reject <id>",
            "/titan-patterns export [path] [--include-candidates]",
            "/titan-patterns import <path> [--overwrite]",
          ].join("\n"),
          "info",
        );
      } catch (err) {
        ctx.ui.notify(`Titan patterns command failed: ${err}`, "error");
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
          ctx.ui.notify("No sufficiently relevant memories found.", "info");
          return;
        }
        const lines = memories.map(
          (m, i) => `${i + 1}. ${m.text}` + (m.scene_id ? ` [scene: ${m.scene_id}]` : "") + memorySourceRef(m),
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
          (m, i) => `${i + 1}. ${m.text}` + (m.scene_id ? ` [scene: ${m.scene_id}]` : "") + memorySourceRef(m),
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

      const pythonCommand = await resolveTitanPython();
      if (!pythonCommand) {
        ctx.ui.notify("Python 3.10+ with Titan dependencies was not found. Run /titan-setup.", "error");
        return;
      }

      ctx.ui.notify("Checking dashboard dependency (rich)...", "info");
      const richReady = await ensureRichDependency(pythonCommand);

      const sessionArg = args?.trim() || "";
      const scriptArgs = sessionArg
        ? [dashboardScript, "--session-id", sessionArg]
        : [dashboardScript];
      if (!richReady) {
        scriptArgs.push("--plain");
        ctx.ui.notify("Rich is unavailable; launching the plain-text dashboard.", "warning");
      }

      const result = await runProcess(pythonCommand, scriptArgs, {
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
