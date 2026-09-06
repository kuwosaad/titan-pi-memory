import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { Buffer } from "node:buffer";
import { basename, resolve } from "node:path";
import { experimental_defineHostEntry } from "@get-bb/plugin-sdk/host";
import {
  MAX_HELPER_STDERR_BYTES,
  MAX_HELPER_STDOUT_BYTES,
  type HostExecuteInput,
  hostContract,
} from "./contract.js";

const MAX_ACTIVE_CHILDREN = 4;
const MAX_STDIN_BYTES = 64 * 1024;
const HELPER_TIMEOUT_MS = 25_000;
const ABORT_KILL_GRACE_MS = 250;

class HostExecutionError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "HostExecutionError";
  }
}

let disposed = false;
const activeChildren = new Set<ChildProcessWithoutNullStreams>();

function abortError(): HostExecutionError {
  return new HostExecutionError("cancelled", "explorer request was cancelled");
}

function boundedAppendUtf8(current: string, chunk: string, limitBytes: number): string {
  const remaining = limitBytes - Buffer.byteLength(current, "utf8");
  if (remaining <= 0) return current;
  const chunkBytes = Buffer.from(chunk, "utf8");
  if (chunkBytes.byteLength <= remaining) return current + chunk;
  return current + chunkBytes.subarray(0, remaining).toString("utf8");
}

function kill(child: ChildProcessWithoutNullStreams, signal: NodeJS.Signals): void {
  // ChildProcess.killed means "a signal was sent", not "the process exited".
  // Use the exit fields for the real terminal-state check so SIGKILL can
  // escalate after a helper ignores SIGTERM.
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    child.kill(signal);
  } catch {
    // The child may have exited between the state check and kill().
  }
}

function resolveSharedTitanHome(configuredValue?: string): string | undefined {
  const configured = configuredValue?.trim() || process.env.TITAN_SHARED_HOME?.trim() || process.env.TITAN_HOME?.trim();
  if (!configured) return undefined;

  const home = resolve(configured);
  // Titan adapters may export either the shared home or one agent's derived
  // home. The Python bridge expects the former, so normalize the latter on the
  // host before passing --home. This path never comes from the panel.
  if (basename(resolve(home, "..")) === "agents") {
    return resolve(home, "..", "..");
  }
  return home;
}

function pythonInvocation(command: HostExecuteInput["command"], runtime?: HostExecuteInput["runtime"]): {
  executable: string;
  args: string[];
  cwd: string | undefined;
  env: NodeJS.ProcessEnv;
} {
  const root = runtime?.titanRoot?.trim() || process.env.TITAN_ROOT?.trim() || undefined;
  const python = runtime?.titanPython?.trim() || process.env.TITAN_PYTHON?.trim() || "python3";
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
  };

  // A source checkout needs its root on PYTHONPATH; an installed Titan package
  // works without it. Both values come from the host environment, never the
  // panel request.
  if (root) {
    env.PYTHONPATH = env.PYTHONPATH ? `${root}${process.platform === "win32" ? ";" : ":"}${env.PYTHONPATH}` : root;
  }

  const home = resolveSharedTitanHome(runtime?.sharedHome);
  return {
    executable: python,
    // The helper receives the operation in-band so stdin is the exact
    // method-specific JSON payload plus the dispatch method, without BB panel
    // identity fields.
    args: ["-m", "app.graph.explorer", ...(home ? ["--home", home] : [])],
    cwd: root,
    env,
  };
}

function runHelper(input: HostExecuteInput, signal: AbortSignal): Promise<{
  stdout: string;
  stderr: string;
  exitCode: number | null;
  signal: string | null;
}> {
  if (disposed) {
    return Promise.reject(new HostExecutionError("host_disposed", "explorer host is disposed"));
  }
  if (signal.aborted) return Promise.reject(abortError());
  if (activeChildren.size >= MAX_ACTIVE_CHILDREN) {
    return Promise.reject(new HostExecutionError("busy", "too many explorer requests are running"));
  }

  const request = JSON.stringify({ method: input.command, ...input.request });
  if (Buffer.byteLength(request, "utf8") > MAX_STDIN_BYTES) {
    return Promise.reject(new HostExecutionError("input_too_large", "explorer request exceeds the input limit"));
  }

  const invocation = pythonInvocation(input.command, input.runtime);

  return new Promise((resolve, reject) => {
    let settled = false;
    let timedOut = false;
    let stdout = "";
    let stderr = "";
    let killTimer: NodeJS.Timeout | undefined;
    let timeoutTimer: NodeJS.Timeout | undefined;

    const child = spawn(invocation.executable, invocation.args, {
      cwd: invocation.cwd,
      env: invocation.env,
      stdio: "pipe",
      shell: false,
    });
    activeChildren.add(child);

    const cleanup = () => {
      activeChildren.delete(child);
      signal.removeEventListener("abort", onAbort);
      if (killTimer) clearTimeout(killTimer);
      if (timeoutTimer) clearTimeout(timeoutTimer);
    };

    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };

    const onAbort = () => {
      if (settled) return;
      kill(child, "SIGTERM");
      // Keep the child accounted for until close, then escalate if it ignores
      // TERM. This is short enough for a panel cancellation but prevents a
      // stubborn helper from leaking a concurrency slot.
      killTimer = setTimeout(() => kill(child, "SIGKILL"), ABORT_KILL_GRACE_MS);
      fail(abortError());
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      if (settled) return;
      if (Buffer.byteLength(stdout, "utf8") + Buffer.byteLength(chunk, "utf8") > MAX_HELPER_STDOUT_BYTES) {
        kill(child, "SIGKILL");
        fail(new HostExecutionError("output_too_large", "explorer response exceeds the output limit"));
        return;
      }
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      if (!settled) stderr = boundedAppendUtf8(stderr, chunk, MAX_HELPER_STDERR_BYTES);
    });
    child.once("error", (error) => {
      kill(child, "SIGTERM");
      kill(child, "SIGKILL");
      fail(new HostExecutionError("spawn_failed", `could not start Titan explorer: ${error.message}`));
    });
    child.once("close", (exitCode, exitSignal) => {
      if (!settled) {
        settled = true;
        if (timedOut) {
          reject(new HostExecutionError("timeout", "Titan explorer timed out"));
        } else {
          resolve({
            stdout,
            stderr,
            exitCode,
            signal: exitSignal,
          });
        }
      }
      // A rejected request still owns its child until close. This prevents a
      // stubborn helper from freeing a concurrency slot before it is gone.
      cleanup();
    });

    timeoutTimer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      kill(child, "SIGTERM");
      killTimer = setTimeout(() => kill(child, "SIGKILL"), ABORT_KILL_GRACE_MS);
    }, HELPER_TIMEOUT_MS);

    signal.addEventListener("abort", onAbort, { once: true });
    child.stdin.once("error", (error) => {
      kill(child, "SIGTERM");
      kill(child, "SIGKILL");
      fail(new HostExecutionError("stdin_failed", `could not send explorer request: ${error.message}`));
    });
    child.stdin.end(request);
  });
}

export const explorerHostEntry = experimental_defineHostEntry({
  contract: hostContract,
  handlers: {
    execute: async (input, context) => runHelper(input, context.signal),
  },
  dispose: async () => {
    disposed = true;
    for (const child of activeChildren) {
      kill(child, "SIGTERM");
      kill(child, "SIGKILL");
    }
  },
});

export default explorerHostEntry;

// Exported only for focused host tests; production callers use the entry.
export const __testing = {
  runHelper,
  activeCount: () => activeChildren.size,
  resetForTests: () => {
    disposed = false;
    for (const child of activeChildren) kill(child, "SIGKILL");
    activeChildren.clear();
  },
};
