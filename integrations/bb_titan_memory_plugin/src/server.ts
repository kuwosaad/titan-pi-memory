import type { BbPluginApi } from "@get-bb/plugin-sdk";
import {
  catalogOutputSchema,
  detailOutputSchema,
  explorerContract,
  hostContract,
  pythonErrorSchema,
  searchOutputSchema,
  snapshotOutputSchema,
  type ExplorerCatalogInput,
  type ExplorerCommand,
  type ExplorerDetailInput,
  type ExplorerSearchInput,
  type ExplorerSnapshotInput,
  type HostExecuteInput,
  type HostExecuteOutput,
  type HostRuntimeConfig,
  runtimeSettingSchema,
} from "./contract.js";

const MAX_ACTIVE_REQUESTS = 4;
const MAX_ERROR_MESSAGE = 512;

class ExplorerRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message.slice(0, MAX_ERROR_MESSAGE));
    this.name = "ExplorerRequestError";
  }
}

type RequestInput = ExplorerCatalogInput | ExplorerSnapshotInput | ExplorerSearchInput | ExplorerDetailInput;
type CancelInput = { threadId: string; requestId: string };
type InFlightRequest = {
  threadId: string;
  requestId: string;
  controller: AbortController;
};

function requestKey(threadId: string, requestId: string): string {
  return `${threadId}\u0000${requestId}`;
}

function errorCode(error: unknown): string {
  if (error instanceof ExplorerRequestError) return error.code;
  if (error && typeof error === "object" && "code" in error && typeof error.code === "string") {
    return error.code.slice(0, 128);
  }
  return "backend_error";
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message.slice(0, MAX_ERROR_MESSAGE);
  return "Titan explorer request failed";
}

function normalizeError(error: unknown): ExplorerRequestError {
  if (error instanceof ExplorerRequestError) return error;
  return new ExplorerRequestError(errorCode(error), errorMessage(error));
}

function isAbort(error: unknown): boolean {
  return error instanceof ExplorerRequestError && error.code === "cancelled";
}

function helperInput(command: ExplorerCommand, input: RequestInput, runtime?: HostRuntimeConfig): HostExecuteInput {
  switch (command) {
    case "catalog":
      return {
        command,
        request: {},
        ...(runtime ? { runtime } : {}),
      };
    case "snapshot":
      return {
        command,
        request: { filters: (input as ExplorerSnapshotInput).filters },
        ...(runtime ? { runtime } : {}),
      };
    case "search":
      return {
        command,
        request: {
          filters: (input as ExplorerSearchInput).filters,
          ...((input as ExplorerSearchInput).cursor === undefined
            ? {}
            : { cursor: (input as ExplorerSearchInput).cursor }),
          ...((input as ExplorerSearchInput).page === undefined
            ? {}
            : { page: (input as ExplorerSearchInput).page }),
        },
        ...(runtime ? { runtime } : {}),
      };
    case "detail":
      return {
        command,
        request: {
          sourceAgent: (input as ExplorerDetailInput).sourceAgent,
          memoryId: (input as ExplorerDetailInput).memoryId,
        },
        ...(runtime ? { runtime } : {}),
      };
  }
}

function parseHelperJson(raw: HostExecuteOutput): unknown {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.stdout);
  } catch {
    throw new ExplorerRequestError(
      raw.exitCode === 0 ? "invalid_helper_response" : "helper_failed",
      raw.exitCode === 0 ? "Titan explorer returned invalid JSON" : "Titan explorer failed without a structured error",
    );
  }

  const structuredError = pythonErrorSchema.safeParse(parsed);
  if (structuredError.success) {
    throw new ExplorerRequestError(structuredError.data.error.code, structuredError.data.error.message);
  }
  if (raw.exitCode !== 0 || raw.signal !== null) {
    throw new ExplorerRequestError(
      "helper_failed",
      raw.stderr.trim() || "Titan explorer failed",
    );
  }
  return parsed;
}

function validateHelperOutput<T>(raw: HostExecuteOutput, schema: { safeParse(value: unknown): { success: true; data: T } | { success: false } }): T {
  const parsed = parseHelperJson(raw);
  const result = schema.safeParse(parsed);
  if (!result.success) {
    throw new ExplorerRequestError("invalid_helper_response", "Titan explorer returned an unexpected response");
  }
  return result.data;
}

async function resolveHostId(bb: BbPluginApi, threadId: string, signal: AbortSignal): Promise<string> {
  const thread = await bb.sdk.threads.get({ threadId, signal });
  if (thread.environmentId) {
    const environment = await bb.sdk.environments.get({ environmentId: thread.environmentId, signal });
    if (environment.hostId) return environment.hostId;
  }

  const system = await bb.sdk.system.config({ signal });
  if (system.primaryHostId) return system.primaryHostId;
  throw new ExplorerRequestError("host_unavailable", "no connected host is available for Titan");
}

type RuntimeSettingValues = {
  titanRoot: string;
  titanPython: string;
  sharedHome: string;
};

function normalizeRuntime(values: RuntimeSettingValues): HostRuntimeConfig | undefined {
  const runtime: HostRuntimeConfig = {};
  if (values.titanRoot.trim()) runtime.titanRoot = values.titanRoot.trim();
  if (values.titanPython.trim()) runtime.titanPython = values.titanPython.trim();
  if (values.sharedHome.trim()) runtime.sharedHome = values.sharedHome.trim();
  return Object.keys(runtime).length > 0 ? runtime : undefined;
}

export default async function plugin(bb: BbPluginApi) {
  const settings = bb.settings.define({
    titanRoot: {
      type: "string",
      label: "Titan root on target host",
      description: "Optional source checkout or installed-package root; never supplied by the panel.",
      default: "",
      experimental_schema: runtimeSettingSchema,
    },
    titanPython: {
      type: "string",
      label: "Python executable on target host",
      description: "Defaults to python3.",
      default: "",
      experimental_schema: runtimeSettingSchema,
    },
    sharedHome: {
      type: "string",
      label: "Shared Titan home on target host",
      description: "Optional ~/.titan-style shared home; agent paths are normalized host-side.",
      default: "",
      experimental_schema: runtimeSettingSchema,
    },
  });
  let runtime = normalizeRuntime(await settings.get());
  settings.onChange((next) => {
    runtime = normalizeRuntime(next);
  });

  const host = bb.hosts.experimental_client({ contract: hostContract });
  const inFlight = new Map<string, InFlightRequest>();

  const cancelAll = () => {
    for (const request of inFlight.values()) request.controller.abort();
    inFlight.clear();
  };
  bb.onDispose(cancelAll);

  async function execute<Output>(
    command: ExplorerCommand,
    input: RequestInput,
    validate: (value: HostExecuteOutput) => Output,
  ): Promise<Output> {
    if (inFlight.size >= MAX_ACTIVE_REQUESTS) {
      throw new ExplorerRequestError("busy", "too many Titan explorer requests are running");
    }

    const controller = new AbortController();
    const current = { threadId: input.threadId, requestId: input.requestId, controller };
    const key = requestKey(input.threadId, input.requestId);
    inFlight.set(key, current);

    try {
      const hostId = await resolveHostId(bb, input.threadId, controller.signal);
      const raw = await host.call("execute", helperInput(command, input, runtime), {
        hostId,
        signal: controller.signal,
      });
      return validate(raw);
    } catch (error) {
      const normalized = normalizeError(error);
      if (!isAbort(normalized)) {
        bb.log.warn(`Titan explorer ${command} failed: ${normalized.code}`);
      }
      throw normalized;
    } finally {
      if (inFlight.get(key)?.requestId === input.requestId) {
        inFlight.delete(key);
      }
    }
  }

  bb.rpc.register(explorerContract, {
    catalog: (input) =>
      execute("catalog", input, (raw) => validateHelperOutput(raw, catalogOutputSchema)),
    snapshot: (input) =>
      execute("snapshot", input, (raw) => validateHelperOutput(raw, snapshotOutputSchema)),
    search: (input) =>
      execute("search", input, (raw) => validateHelperOutput(raw, searchOutputSchema)),
    detail: (input) =>
      execute("detail", input, (raw) => validateHelperOutput(raw, detailOutputSchema)),
    cancel: async (input: CancelInput) => {
      const request = inFlight.get(requestKey(input.threadId, input.requestId));
      if (!request) return { cancelled: false };
      request.controller.abort();
      return { cancelled: true };
    },
  });

  bb.log.info("Titan Memory Explorer backend loaded");
}
