import { defineRpcContract } from "@get-bb/plugin-sdk";
import type { PluginRpcContract } from "@get-bb/plugin-sdk";
import { z } from "zod";

/**
 * The explorer intentionally speaks a small, versioned subset of Titan's
 * memory model. Do not add raw provenance, embeddings, or LNN state here.
 */
const identifier = (max: number) =>
  z.string().min(1).max(max).refine((value) => !/[\r\n]/.test(value), "must not contain newlines");

const agentName = z
  .string()
  .min(1)
  .max(128)
  .refine((value) => !/[\\/\r\n]/.test(value), "must be an agent name, not a path");

export const filtersSchema = z
  .object({
    agent: agentName.optional(),
    type: z.string().min(1).max(128).optional(),
    dateFrom: z.string().min(1).max(64).optional(),
    dateTo: z.string().min(1).max(64).optional(),
    query: z.string().max(512).optional(),
  })
  .strict();

const threadRequestFields = {
  threadId: identifier(256),
  requestId: identifier(128),
};

export const snapshotInputSchema = z
  .object({
    ...threadRequestFields,
    filters: filtersSchema,
  })
  .strict();

const pageNumberSchema = z.number().int().positive().max(1_000_000);

export const searchInputSchema = z
  .object({
    ...threadRequestFields,
    filters: filtersSchema,
    cursor: z.string().max(512).optional(),
    page: pageNumberSchema.optional(),
  })
  .strict()
  .refine((value) => !(value.cursor !== undefined && value.page !== undefined), {
    message: "cursor and page are mutually exclusive",
  });

export const catalogInputSchema = z
  .object({
    ...threadRequestFields,
  })
  .strict();

export const detailInputSchema = z
  .object({
    ...threadRequestFields,
    sourceAgent: agentName,
    memoryId: identifier(256),
  })
  .strict();

/** Payloads written to app.graph.explorer's stdin. */
export const snapshotPythonInputSchema = z
  .object({
    filters: filtersSchema,
  })
  .strict();

export const searchPythonInputSchema = z
  .object({
    filters: filtersSchema,
    cursor: z.string().max(512).optional(),
    page: pageNumberSchema.optional(),
  })
  .strict()
  .refine((value) => !(value.cursor !== undefined && value.page !== undefined), {
    message: "cursor and page are mutually exclusive",
  });

export const catalogPythonInputSchema = z.object({}).strict();

export const detailPythonInputSchema = z
  .object({
    sourceAgent: agentName,
    memoryId: identifier(256),
  })
  .strict();

export const memorySummarySchema = z
  .object({
    nodeId: identifier(512),
    sourceAgent: identifier(128),
    memoryId: identifier(256),
    text: z.string().max(240),
    type: z.string().max(128).nullable(),
    stream: z.string().max(64).nullable(),
    timestamp: z.string().max(128).nullable(),
    sessionId: z.string().max(256).nullable(),
    hasEmbedding: z.boolean(),
  })
  .strict();

export const graphEdgeSchema = z
  .object({
    source: identifier(512),
    target: identifier(512),
    kind: z.literal("similarity"),
    weight: z.number().finite().min(0).max(1),
  })
  .strict();

const warningSchema = z.string().max(512);

export const snapshotOutputSchema = z
  .object({
    nodes: z.array(memorySummarySchema).max(150),
    edges: z.array(graphEdgeSchema).max(450),
    partial: z.boolean(),
    warnings: z.array(warningSchema).max(32),
  })
  .strict();

export const searchOutputSchema = z
  .object({
    items: z.array(memorySummarySchema).max(50),
    nextCursor: z.string().max(512).nullable(),
    warnings: z.array(warningSchema).max(32),
    page: pageNumberSchema.optional(),
    totalItems: z.number().int().nonnegative().optional(),
    totalPages: z.number().int().nonnegative().optional(),
  })
  .strict();

export const catalogOutputSchema = z
  .object({
    agents: z.array(z.object({ id: agentName, count: z.number().int().nonnegative() }).strict()).max(64),
    types: z.array(z.string().max(128)).max(256),
    warnings: z.array(warningSchema).max(32),
  })
  .strict();

const metadataValue = z.union([z.string().max(1024), z.number().finite(), z.boolean(), z.null()]);

export const detailOutputSchema = z
  .object({
    memory: memorySummarySchema,
    text: z.string().max(16 * 1024),
    metadata: z.record(z.string().max(128), metadataValue),
    truncated: z.boolean(),
  })
  .strict();

/** Helper responses are deliberately smaller than BB's 8 MiB host limit. */
export const MAX_HELPER_STDOUT_BYTES = 480 * 1024;
export const MAX_HELPER_STDERR_BYTES = 16 * 1024;

/**
 * The public server-to-app contract. All methods retain the panel identity
 * fields so the server can cancel obsolete requests for one chat panel.
 */
export const explorerContract = defineRpcContract({
  catalog: {
    input: catalogInputSchema,
    output: catalogOutputSchema,
  },
  snapshot: {
    input: snapshotInputSchema,
    output: snapshotOutputSchema,
  },
  search: {
    input: searchInputSchema,
    output: searchOutputSchema,
  },
  detail: {
    input: detailInputSchema,
    output: detailOutputSchema,
  },
  cancel: {
    input: z.object({ ...threadRequestFields }).strict(),
    output: z.object({ cancelled: z.boolean() }).strict(),
  },
});

export type ExplorerContract = typeof explorerContract;
export type ExplorerCatalogInput = z.infer<typeof catalogInputSchema>;
export type ExplorerSnapshotInput = z.infer<typeof snapshotInputSchema>;
export type ExplorerSearchInput = z.infer<typeof searchInputSchema>;
export type ExplorerDetailInput = z.infer<typeof detailInputSchema>;
export type CatalogPythonInput = z.infer<typeof catalogPythonInputSchema>;
export type SnapshotPythonInput = z.infer<typeof snapshotPythonInputSchema>;
export type SearchPythonInput = z.infer<typeof searchPythonInputSchema>;
export type DetailPythonInput = z.infer<typeof detailPythonInputSchema>;
export type ExplorerCatalog = z.infer<typeof catalogOutputSchema>;
export type ExplorerSnapshot = z.infer<typeof snapshotOutputSchema>;
export type ExplorerSearch = z.infer<typeof searchOutputSchema>;
export type ExplorerDetail = z.infer<typeof detailOutputSchema>;
export type MemorySummary = z.infer<typeof memorySummarySchema>;

export const explorerCommands = ["catalog", "snapshot", "search", "detail"] as const;
export type ExplorerCommand = (typeof explorerCommands)[number];

/** Trusted server-owned host configuration, never accepted from the app. */
export const runtimeSettingSchema = z
  .string()
  .max(4096)
  .refine(
    (value) => !value.includes("\0") && !value.includes("\r") && !value.includes("\n"),
    "must not contain control characters",
  );

export const hostRuntimeSchema = z
  .object({
    titanRoot: runtimeSettingSchema.optional(),
    titanPython: runtimeSettingSchema.optional(),
    sharedHome: runtimeSettingSchema.optional(),
  })
  .strict();

export type HostRuntimeConfig = z.infer<typeof hostRuntimeSchema>;

/**
 * The host receives the operation as an argv value and the exact Python JSON
 * request separately. Thus stdin is the public request minus threadId and
 * requestId, as required by the Python helper contract. Cancellation is a
 * server-only operation and never reaches Python.
 */
export const hostContract = {
  execute: {
    input: z.discriminatedUnion("command", [
      z.object({
        command: z.literal("catalog"),
        request: catalogPythonInputSchema,
        runtime: hostRuntimeSchema.optional(),
      }),
      z.object({
        command: z.literal("snapshot"),
        request: snapshotPythonInputSchema,
        runtime: hostRuntimeSchema.optional(),
      }),
      z.object({
        command: z.literal("search"),
        request: searchPythonInputSchema,
        runtime: hostRuntimeSchema.optional(),
      }),
      z.object({
        command: z.literal("detail"),
        request: detailPythonInputSchema,
        runtime: hostRuntimeSchema.optional(),
      }),
    ]),
    output: z
      .object({
        // Zod's max is a character bound; the host additionally enforces the
        // UTF-8 byte bound before returning this value.
        stdout: z.string().max(MAX_HELPER_STDOUT_BYTES),
        stderr: z.string().max(MAX_HELPER_STDERR_BYTES),
        exitCode: z.number().int().nullable(),
        signal: z.string().max(32).nullable(),
      })
      .strict(),
  },
} satisfies PluginRpcContract;

export type ExplorerHostContract = typeof hostContract;
export type HostExecuteInput = z.infer<typeof hostContract.execute.input>;
export type HostExecuteOutput = z.infer<typeof hostContract.execute.output>;

/** Python's only structured failure shape. */
export const pythonErrorSchema = z
  .object({
    error: z
      .object({
        code: identifier(128),
        message: z.string().min(1).max(512),
      })
      .strict(),
  })
  .strict();
