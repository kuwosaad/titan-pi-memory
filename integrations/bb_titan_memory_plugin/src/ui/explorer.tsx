import {
  Component,
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useRpc,
  type StandardSchemaV1,
  type StandardSchemaV1InferInput,
  type StandardSchemaV1InferOutput,
} from "@get-bb/plugin-sdk/app";
import { explorerContract } from "../contract.js";
import { MemoryGraph, type RelationshipFilter } from "./graph.js";
import {
  boundSnapshot,
  cleanFilters,
  connectedMemories,
  createRequestGate,
  createRequestScheduler,
  formatTimestamp,
  inCorrectionCycle,
  MAX_GRAPH_NODES,
  MAX_SEARCH_ITEMS,
  mergeWarnings,
  pageTokens,
  percent,
  requestId,
  shortText,
  type ExplorerFilters,
  type MemorySummary,
  type RequestGate,
  type RequestScheduler,
} from "./model.js";
import type { ExplorerDetail, ExplorerSnapshot } from "../contract.js";

interface ExplorerProps {
  threadId: string;
}

type Contract = typeof explorerContract;
type RpcInput<Method extends keyof Contract> = Contract[Method] extends {
  input: infer Schema extends StandardSchemaV1;
}
  ? StandardSchemaV1InferInput<Schema>
  : never;
type RpcOutput<Method extends keyof Contract> = Contract[Method] extends {
  output: infer Schema extends StandardSchemaV1;
}
  ? StandardSchemaV1InferOutput<Schema>
  : never;
type SnapshotResponse = ExplorerSnapshot;
type SearchResponse = RpcOutput<"search">;
type CatalogResponse = RpcOutput<"catalog">;
type DetailResponse = ExplorerDetail;
type CatalogAgent = CatalogResponse["agents"][number];
type DetailState = {
  response: DetailResponse | null;
  loading: boolean;
  error: string | null;
};

const EMPTY_FILTERS: ExplorerFilters = {};

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Titan could not load this read-only view.";
}

function metadataEntries(metadata: DetailResponse["metadata"]): Array<[string, string]> {
  const shown = new Set(["type", "stream", "timestamp", "session_id", "scene_id", "event_date", "event_start", "event_end"]);
  return Object.entries(metadata).flatMap(([key, value]) => (
    shown.has(key) || value == null ? [] : [[key.replaceAll("_", " "), String(value)]]
  ));
}

function EmptyState({ title, filtered }: { title: string; filtered: boolean }) {
  return (
    <div className="titan-empty" role="status">
      <strong>{title}</strong>
      <span>{filtered ? "Try clearing the filters." : "Titan has not stored any memories in this scope yet."}</span>
    </div>
  );
}

function MemoryText({ text }: { text: string }) {
  const value = shortText(text);
  return value
    ? <span className="titan-memory-row-text" dir="auto">{value}</span>
    : <span className="titan-memory-row-text titan-memory-row-text--empty">(empty memory)</span>;
}

/** Remounts its children after an error once `resetKey` changes (a new snapshot). */
class GraphErrorBoundary extends Component<{ children: ReactNode; resetKey: number }, { error: boolean }> {
  state = { error: false };

  static getDerivedStateFromError() {
    return { error: true };
  }

  componentDidUpdate(previous: { resetKey: number }) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: false });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="titan-inline-error" role="alert">
          <strong>Graph unavailable</strong>
          <span>The records view remains available.</span>
        </div>
      );
    }
    return this.props.children;
  }
}

/**
 * A different thread can resolve to a different Titan home, so nothing a
 * panel loaded (rows, graph, selection, filters, in-flight requests) may
 * outlive its thread: keying by threadId discards it all on the first render
 * for the new thread, before a stale row could be clicked.
 */
export function ExplorerPanel({ threadId }: ExplorerProps) {
  return <ThreadExplorer key={threadId} threadId={threadId} />;
}

function ThreadExplorer({ threadId }: ExplorerProps) {
  const rpc = useRpc<typeof explorerContract>();
  const queryGateRef = useRef<RequestGate | null>(null);
  const pageGateRef = useRef<RequestGate | null>(null);
  const detailGateRef = useRef<RequestGate | null>(null);
  const catalogGateRef = useRef<RequestGate | null>(null);
  // Request id -> the thread it was sent for, so a cancel always names the
  // right thread even after the panel moves to another one.
  const activeRequestsRef = useRef<Map<string, string> | null>(null);
  const schedulerRef = useRef<RequestScheduler | null>(null);
  if (!queryGateRef.current) queryGateRef.current = createRequestGate();
  if (!pageGateRef.current) pageGateRef.current = createRequestGate();
  if (!detailGateRef.current) detailGateRef.current = createRequestGate();
  if (!catalogGateRef.current) catalogGateRef.current = createRequestGate();
  if (!activeRequestsRef.current) activeRequestsRef.current = new Map<string, string>();
  if (!schedulerRef.current) schedulerRef.current = createRequestScheduler();
  const queryGate = queryGateRef.current;
  const pageGate = pageGateRef.current;
  const detailGate = detailGateRef.current;
  const catalogGate = catalogGateRef.current;
  const activeRequests = activeRequestsRef.current;
  const scheduler = schedulerRef.current;

  const [filters, setFilters] = useState<ExplorerFilters>(EMPTY_FILTERS);
  const [queryInput, setQueryInput] = useState("");
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<SnapshotResponse | null>(null);
  const [items, setItems] = useState<MemorySummary[]>([]);
  const [searchPage, setSearchPage] = useState(1);
  const [totalItems, setTotalItems] = useState<number | null>(null);
  const [totalPages, setTotalPages] = useState(1);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pageInput, setPageInput] = useState("1");
  const [selectedMemory, setSelectedMemory] = useState<MemorySummary | null>(null);
  const selectedNodeId = selectedMemory?.nodeId ?? null;
  const [detail, setDetail] = useState<DetailState>({ response: null, loading: false, error: null });
  // The graph and the list load independently: the graph renders as soon as
  // its snapshot arrives, and a reload keeps the previous graph mounted.
  const [graphLoading, setGraphLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [snapshotVersion, setSnapshotVersion] = useState(0);
  const [pageLoading, setPageLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [listVisible, setListVisible] = useState(true);
  const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter>("all");

  const cancelRequest = useCallback((request: string) => {
    const requestThread = request ? activeRequests.get(request) : undefined;
    if (requestThread === undefined) return;
    activeRequests.delete(request);
    void rpc.call("cancel", { threadId: requestThread, requestId: request } as RpcInput<"cancel">).catch(() => undefined);
  }, [activeRequests, rpc]);
  const cancelRequestRef = useRef(cancelRequest);
  cancelRequestRef.current = cancelRequest;

  const beginRequest = useCallback((gate: RequestGate, prefix: string) => {
    const id = requestId(prefix);
    const previous = gate.activate(id);
    cancelRequest(previous || "");
    activeRequests.set(id, threadId);
    return id;
  }, [activeRequests, cancelRequest, threadId]);

  const isCurrent = useCallback((gate: RequestGate, id: string) => gate.isCurrent(id), []);

  // Queue behind this panel's other requests and retry the backend's "busy"
  // rejection, dropping the call if a newer request replaced it meanwhile.
  const schedule = useCallback(<T,>(gate: RequestGate, id: string, task: () => Promise<T>) => (
    scheduler.run(task, () => gate.isCurrent(id))
  ), [scheduler]);

  const cancelGate = useCallback((gate: RequestGate) => {
    cancelRequest(gate.activate("") || "");
  }, [cancelRequest]);

  const loadCatalog = useCallback(async () => {
    const id = beginRequest(catalogGate, "catalog");
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const response = await schedule(catalogGate, id, () => rpc.call("catalog", {
        threadId,
        requestId: id,
      } as RpcInput<"catalog">));
      if (!isCurrent(catalogGate, id)) return;
      setCatalog(response as CatalogResponse);
    } catch (requestError) {
      if (!isCurrent(catalogGate, id)) return;
      setCatalogError(errorMessage(requestError));
    } finally {
      if (isCurrent(catalogGate, id)) setCatalogLoading(false);
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, catalogGate, isCurrent, rpc, schedule, threadId]);

  const loadDetail = useCallback(async (memory: MemorySummary) => {
    const id = beginRequest(detailGate, "detail");
    setSelectedMemory(memory);
    setDetail({ response: null, loading: true, error: null });
    try {
      const response = await schedule(detailGate, id, () => rpc.call("detail", {
        threadId,
        requestId: id,
        sourceAgent: memory.sourceAgent,
        memoryId: memory.memoryId,
      } as RpcInput<"detail">));
      if (!isCurrent(detailGate, id)) return;
      setDetail({ response: response as DetailResponse, loading: false, error: null });
    } catch (requestError) {
      if (!isCurrent(detailGate, id)) return;
      setDetail({ response: null, loading: false, error: errorMessage(requestError) });
    } finally {
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, detailGate, isCurrent, rpc, schedule, threadId]);

  const loadQuery = useCallback(async (nextFilters: ExplorerFilters) => {
    const id = beginRequest(queryGate, "query");
    cancelGate(pageGate);
    const requestFilters = cleanFilters(nextFilters);
    // Keep the previous graph and rows on screen until their replacements
    // arrive; the graph heading shows "Updating…" meanwhile.
    setGraphLoading(true);
    setLoading(true);
    setPageLoading(false);
    setGraphError(null);
    setListError(null);
    setWarnings([]);
    try {
      const snapshotResponse = await schedule(queryGate, id, () => rpc.call("snapshot", {
        threadId,
        requestId: id,
        filters: requestFilters,
      } as RpcInput<"snapshot">));
      if (!isCurrent(queryGate, id)) return;
      const bounded = boundSnapshot(snapshotResponse as SnapshotResponse);
      setSnapshot(bounded);
      setSnapshotVersion((version) => version + 1);
      setWarnings(bounded.warnings || []);
    } catch (requestError) {
      if (!isCurrent(queryGate, id)) return;
      setSnapshot(null);
      setGraphError(errorMessage(requestError));
    } finally {
      if (isCurrent(queryGate, id)) setGraphLoading(false);
    }

    try {
      const searchResponse = await schedule(queryGate, id, () => rpc.call("search", {
        threadId,
        requestId: id,
        filters: requestFilters,
        page: 1,
      } as RpcInput<"search">));
      if (!isCurrent(queryGate, id)) return;
      const result = searchResponse as SearchResponse;
      const resultPage = typeof result.page === "number" && result.page > 0 ? result.page : 1;
      const reportedPages = typeof result.totalPages === "number" && result.totalPages > 0
        ? result.totalPages
        : result.nextCursor ? resultPage + 1 : resultPage;
      setItems(Array.isArray(result.items) ? result.items.slice(0, MAX_SEARCH_ITEMS) : []);
      setSearchPage(resultPage);
      setPageInput(String(resultPage));
      setTotalItems(typeof result.totalItems === "number" ? result.totalItems : null);
      setTotalPages(Math.max(resultPage, reportedPages));
      setNextCursor(result.nextCursor || null);
      setWarnings((current) => mergeWarnings(current, result.warnings));
    } catch (requestError) {
      if (!isCurrent(queryGate, id)) return;
      setListError(errorMessage(requestError));
      setItems([]);
      setSearchPage(1);
      setPageInput("1");
      setTotalItems(null);
      setTotalPages(1);
      setNextCursor(null);
    } finally {
      if (isCurrent(queryGate, id)) setLoading(false);
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, cancelGate, isCurrent, pageGate, queryGate, rpc, schedule, threadId]);

  const loadPage = useCallback(async (page: number) => {
    if (pageLoading || loading || page < 1 || page > totalPages) return;
    const id = beginRequest(pageGate, "page");
    setPageLoading(true);
    setListError(null);
    try {
      const response = await schedule(pageGate, id, () => rpc.call("search", {
        threadId,
        requestId: id,
        filters: cleanFilters(filters),
        page,
      } as RpcInput<"search">));
      if (!isCurrent(pageGate, id)) return;
      const result = response as SearchResponse;
      const resultPage = typeof result.page === "number" && result.page > 0 ? result.page : page;
      const reportedPages = typeof result.totalPages === "number" && result.totalPages > 0
        ? result.totalPages
        : result.nextCursor ? Math.max(totalPages, resultPage + 1) : resultPage;
      setItems(Array.isArray(result.items) ? result.items.slice(0, MAX_SEARCH_ITEMS) : []);
      setSearchPage(resultPage);
      setPageInput(String(resultPage));
      setTotalItems(typeof result.totalItems === "number" ? result.totalItems : null);
      setTotalPages(Math.max(resultPage, reportedPages));
      setNextCursor(result.nextCursor || null);
      setWarnings((current) => mergeWarnings(current, result.warnings));
    } catch (requestError) {
      if (isCurrent(pageGate, id)) setListError(errorMessage(requestError));
    } finally {
      if (isCurrent(pageGate, id)) setPageLoading(false);
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, filters, isCurrent, loading, pageGate, pageLoading, rpc, schedule, threadId, totalPages]);

  const refreshAll = useCallback(() => {
    void loadCatalog();
    void loadQuery(filters);
    if (selectedMemory) void loadDetail(selectedMemory);
  }, [filters, loadCatalog, loadDetail, loadQuery, selectedMemory]);

  // Mount/unmount only (a new thread remounts this component). This runs
  // before the load effects below, so a StrictMode remount reopens the gates
  // before new requests start. A new rpc object must not close the gates.
  useEffect(() => {
    const gates = [queryGate, pageGate, detailGate, catalogGate];
    for (const gate of gates) gate.reopen();
    return () => {
      for (const gate of gates) gate.close();
      for (const request of Array.from(activeRequests.keys())) cancelRequestRef.current(request);
    };
  }, []);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    void loadQuery(filters);
  }, [filters, loadQuery]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const nextQuery = queryInput.trim();
      setFilters((current) => {
        if ((current.query || "") === nextQuery) return current;
        const next = { ...current };
        if (nextQuery) next.query = nextQuery;
        else delete next.query;
        return next;
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [queryInput]);

  const graphMemories = snapshot?.nodes || [];
  const visibleIds = useMemo(() => new Set([...graphMemories, ...items].map((memory) => memory.nodeId)), [graphMemories, items]);
  const neighbors = selectedMemory ? connectedMemories(selectedMemory.nodeId, snapshot, graphMemories) : [];
  const pageList = useMemo(() => pageTokens(searchPage, totalPages), [searchPage, totalPages]);
  const catalogAgents = (catalog?.agents || []) as CatalogAgent[];
  const catalogTypes = catalog?.types || [];
  const legendAgents = useMemo(
    () => ((catalog?.agents || []) as CatalogAgent[]).filter((agent) => agent.count > 0).map((agent) => agent.id),
    [catalog],
  );
  const invertedDates = Boolean(filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo);
  const corrections = detail.response?.corrections || [];
  // Stored corrections should only ever point from a replacement to what it
  // replaced. Flag the selected memory when the loaded correction edges (the
  // graph's plus this memory's own history) lead back to it, at any length.
  const conflictingCorrections = useMemo(() => {
    if (!selectedMemory || !corrections.length) return false;
    const selected = selectedMemory.nodeId;
    const edges = (snapshot?.edges || []).filter((edge) => edge.kind === "supersedes");
    for (const correction of corrections) {
      const other = correction.memory?.nodeId || `${selectedMemory.sourceAgent}::${correction.memoryId}`;
      edges.push(correction.direction === "replaces"
        ? { source: selected, target: other, kind: "supersedes", weight: 1 }
        : { source: other, target: selected, kind: "supersedes", weight: 1 });
    }
    return inCorrectionCycle(selected, edges);
  }, [corrections, selectedMemory, snapshot]);
  const allWarnings = mergeWarnings(warnings, catalog?.warnings).filter(
    (warning) => !(snapshot && snapshot.nodes.length >= MAX_GRAPH_NODES && warning === `Graph limited to ${MAX_GRAPH_NODES} memories`),
  );
  const hasActiveFilters = Boolean(
    queryInput.trim() || filters.agent || filters.type || filters.dateFrom || filters.dateTo,
  );
  // Nothing to search or filter: the catalog (read without warnings) counts no
  // memories at all and the unfiltered graph agrees.
  const storeEmpty = Boolean(
    catalog && !catalogError && !catalog.warnings.length && !hasActiveFilters
      && catalogTypes.length === 0 && catalogAgents.every((agent) => agent.count === 0)
      && (!snapshot || snapshot.nodes.length === 0),
  );
  const showingLatestGraphCap = snapshot?.warnings.includes(`Graph limited to ${MAX_GRAPH_NODES} memories`);
  const selectedMetadata = detail.response?.metadata || {};
  const gridClassName = ["titan-explorer-grid", !listVisible && "titan-explorer-grid--list-hidden", selectedMemory && "titan-explorer-grid--inspecting"].filter(Boolean).join(" ");

  function updateFilter<K extends keyof ExplorerFilters>(key: K, value: string) {
    setFilters((current) => {
      const next = { ...current };
      if (value) next[key] = value as ExplorerFilters[K];
      else delete next[key];
      return next;
    });
  }

  function onSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextQuery = queryInput.trim();
    setFilters((current) => {
      const next = { ...current };
      if (nextQuery) next.query = nextQuery;
      else delete next.query;
      return next;
    });
  }

  function clearFilters() {
    setQueryInput("");
    setFilters(EMPTY_FILTERS);
  }

  function goToPage() {
    const requested = Number.parseInt(pageInput, 10);
    if (!Number.isFinite(requested)) {
      setPageInput(String(searchPage));
      return;
    }
    void loadPage(Math.max(1, Math.min(totalPages, requested)));
  }

  return (
    <main className="titan-explorer" aria-labelledby="titan-explorer-title">
      <header className="titan-explorer-header">
        <h1 id="titan-explorer-title">Titan memory</h1>
        <div className="titan-header-actions">
          <button
            type="button"
            className="titan-button titan-button--quiet"
            aria-label={listVisible ? "Hide memories" : "Show memories"}
            title={listVisible ? "Hide memories" : "Show memories"}
            aria-expanded={listVisible}
            aria-controls="titan-memory-list-panel"
            onClick={() => setListVisible((visible) => !visible)}
          >
            {listVisible ? "Hide memories" : "Show memories"}
          </button>
          <button
            type="button"
            className="titan-icon-button titan-icon-button--quiet"
            aria-label="Refresh Titan data"
            title="Refresh Titan data"
            onClick={refreshAll}
            disabled={graphLoading || loading || catalogLoading}
          >
            ↻
          </button>
        </div>
      </header>

      <form className="titan-toolbar" onSubmit={onSearchSubmit} aria-label="Memory filters">
        <label className="titan-field titan-field--query">
          <span className="titan-visually-hidden">Search</span>
          <input
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            placeholder="Search memories"
            aria-label="Search memories"
            autoComplete="off"
            disabled={storeEmpty}
          />
        </label>
        <label className="titan-field">
          <span className="titan-visually-hidden">Agent</span>
          <select value={filters.agent || ""} onChange={(event) => updateFilter("agent", event.target.value)} aria-label="Filter by agent" disabled={storeEmpty}>
            <option value="">All agents</option>
            {catalogAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.id} · {agent.count}</option>)}
          </select>
        </label>
        <label className="titan-field">
          <span className="titan-visually-hidden">Type</span>
          <select value={filters.type || ""} onChange={(event) => updateFilter("type", event.target.value)} aria-label="Filter by type" disabled={storeEmpty}>
            <option value="">All types</option>
            {catalogTypes.map((type) => <option key={type} value={type} title={type}>{shortText(type, 40)}</option>)}
          </select>
        </label>
        <details className="titan-more-filters" open={storeEmpty ? false : undefined}>
          <summary
            aria-disabled={storeEmpty || undefined}
            tabIndex={storeEmpty ? -1 : undefined}
            onClick={storeEmpty ? (event) => event.preventDefault() : undefined}
          >More filters</summary>
          <div className="titan-more-filters-content">
            {/* The backend matches whole UTC calendar days; say so, since rows show local times. */}
            <label className="titan-date-field" title="Dates are whole UTC calendar days">
              <span>From (UTC)</span>
              <input type="date" value={filters.dateFrom || ""} max={filters.dateTo || undefined} onChange={(event) => updateFilter("dateFrom", event.target.value)} aria-label="Filter from date" />
            </label>
            <label className="titan-date-field" title="Dates are whole UTC calendar days">
              <span>To (UTC)</span>
              <input type="date" value={filters.dateTo || ""} min={filters.dateFrom || undefined} onChange={(event) => updateFilter("dateTo", event.target.value)} aria-label="Filter to date" />
            </label>
            {invertedDates ? <p className="titan-date-note" role="status">From is after To, so no memories can match.</p> : null}
          </div>
        </details>
        {hasActiveFilters ? <button type="button" className="titan-button titan-button--quiet" onClick={clearFilters}>Clear</button> : null}
      </form>

      {catalogError ? (
        <div className="titan-alert titan-alert--error" role="alert">
          <span>{catalogError}</span>
          <button type="button" className="titan-button" onClick={() => void loadCatalog()}>Retry catalog</button>
        </div>
      ) : null}
      {allWarnings.length ? (
        <div className="titan-alert titan-alert--warning" role="status">
          {Array.from(new Set(allWarnings)).map((warning) => <span key={warning}>{warning}</span>)}
        </div>
      ) : null}

      <div className={gridClassName}>
        <section className="titan-panel titan-panel--graph" aria-labelledby="titan-graph-title">
          <div className="titan-panel-heading">
            <h2 id="titan-graph-title">Memory graph</h2>
            {graphLoading && snapshot
              ? <span className="titan-inline-note" role="status">Updating…</span>
              : showingLatestGraphCap ? <span className="titan-inline-note">Showing latest 150</span> : snapshot?.partial ? <span className="titan-badge titan-badge--warning">Partial</span> : null}
          </div>
          {graphLoading && !snapshot ? <div className="titan-loading" role="status">Loading…</div> : null}
          {graphError ? <div className="titan-inline-error" role="alert">Graph unavailable: {graphError} <button type="button" className="titan-button" onClick={() => void loadQuery(filters)}>Retry</button></div> : null}
          {!graphLoading && !graphError && snapshot && snapshot.nodes.length === 0 ? <EmptyState title="No memories found" filtered={hasActiveFilters} /> : null}
          {snapshot && snapshot.nodes.length ? (
            // Stays mounted across filter and search changes so the WebGL
            // context, layout and camera survive; it resets after an error
            // once a new snapshot arrives.
            <GraphErrorBoundary resetKey={snapshotVersion}>
              <MemoryGraph
                snapshot={snapshot}
                selectedNodeId={graphMemories.some((memory) => memory.nodeId === selectedNodeId) ? selectedNodeId : null}
                onSelect={(memory) => void loadDetail(memory)}
                relationshipFilter={relationshipFilter}
                onRelationshipFilterChange={setRelationshipFilter}
                agents={legendAgents}
                agentFilter={filters.agent || null}
                onAgentFilterChange={(agent) => updateFilter("agent", agent || "")}
              />
            </GraphErrorBoundary>
          ) : null}
        </section>

        {listVisible ? (
          <section id="titan-memory-list-panel" className="titan-panel titan-panel--list" aria-labelledby="titan-list-title" aria-busy={loading || pageLoading}>
            <div className="titan-panel-heading">
              <h2 id="titan-list-title">Memories</h2>
              <span className="titan-count" aria-live="polite">{totalItems ?? items.length}</span>
            </div>
            {listError ? <div className="titan-inline-error" role="alert">Memories unavailable: {listError} <button type="button" className="titan-button" onClick={() => void loadQuery(filters)}>Retry</button></div> : null}
            {items.length === 0 && !loading && !listError ? <EmptyState title="No matching memories" filtered={hasActiveFilters} /> : (
              <ul className="titan-memory-list">
                {items.map((memory) => (
                  <li key={memory.nodeId}>
                    <button
                      type="button"
                      className={memory.nodeId === selectedNodeId ? "titan-memory-row titan-memory-row--selected" : "titan-memory-row"}
                      aria-current={memory.nodeId === selectedNodeId ? "true" : undefined}
                      onClick={() => void loadDetail(memory)}
                    >
                      <span className="titan-memory-row-meta">
                        <bdi title={memory.type || undefined}>{memory.type || "Memory"}</bdi>
                        <bdi>{memory.sourceAgent}</bdi>
                      </span>
                      <MemoryText text={memory.text} />
                      <span className="titan-memory-row-footer">{memory.stream || "Unclassified"} · {formatTimestamp(memory.timestamp)}{!memory.hasEmbedding ? " · no vector" : ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {totalPages > 1 ? (
              <nav className="titan-pagination" aria-label="Memory pages">
                <button type="button" className="titan-button" onClick={() => void loadPage(searchPage - 1)} disabled={pageLoading || searchPage <= 1}>Previous</button>
                <div className="titan-page-buttons">
                  {pageList.map((token, index) => token === "…" ? <span key={`ellipsis-${index}`}>…</span> : (
                    <button key={token} type="button" className={token === searchPage ? "titan-page-button titan-page-button--current" : "titan-page-button"} aria-current={token === searchPage ? "page" : undefined} onClick={() => void loadPage(token)} disabled={pageLoading || token === searchPage}>{token}</button>
                  ))}
                </div>
                <button type="button" className="titan-button" onClick={() => void loadPage(searchPage + 1)} disabled={pageLoading || searchPage >= totalPages}>Next</button>
                {totalPages > 7 ? (
                  <div className="titan-go-page">
                    <label htmlFor="titan-go-page">Go to</label>
                    <input id="titan-go-page" inputMode="numeric" value={pageInput} onChange={(event) => setPageInput(event.target.value)} aria-label="Go to page" />
                    <button type="button" className="titan-button" onClick={goToPage} disabled={pageLoading}>Go</button>
                  </div>
                ) : null}
              </nav>
            ) : null}
          </section>
        ) : null}

        {selectedMemory ? (
          <aside className="titan-panel titan-panel--details" aria-labelledby="titan-details-title">
            <div className="titan-panel-heading">
              <h2 id="titan-details-title">Memory details</h2>
              <button
                type="button"
                className="titan-icon-button"
                aria-label="Close memory details"
                onClick={() => {
                  cancelGate(detailGate);
                  setSelectedMemory(null);
                  setDetail({ response: null, loading: false, error: null });
                }}
              >×</button>
            </div>
            <div className="titan-detail-body">
              {detail.loading ? <p className="titan-detail-status" role="status">Loading…</p> : null}
              {detail.error ? <div className="titan-inline-error" role="alert">{detail.error}</div> : null}
              <p className="titan-inspector-meta"><bdi>{selectedMemory.sourceAgent}</bdi>{selectedMemory.type ? <> · <bdi>{selectedMemory.type}</bdi></> : null} · Recorded {formatTimestamp(selectedMemory.timestamp)}</p>
              {!visibleIds.has(selectedMemory.nodeId) ? <p className="titan-scope-note">Outside the current graph and list</p> : null}
              {(detail.response?.text || selectedMemory.text).trim()
                ? <p className="titan-detail-text" dir="auto">{detail.response?.text || selectedMemory.text}</p>
                : <p className="titan-detail-text titan-detail-text--empty">(empty memory)</p>}
              {detail.response?.truncated ? <p className="titan-detail-status">Text truncated</p> : null}
              {detail.response?.warnings?.map((warning) => <p className="titan-detail-status" role="status" key={warning}>{warning}</p>)}
              {detail.response?.corrections !== undefined ? (
                <section className="titan-connections" aria-label="Correction history">
                  <h3>Correction history</h3>
                  {conflictingCorrections ? <p className="titan-detail-status" role="status">Conflicting correction history: these stored corrections form a cycle back to this memory.</p> : null}
                  {detail.response.corrections.map((correction) => (
                    <div className="titan-correction" key={`${correction.direction}:${correction.memoryId}`}>
                      {correction.memory ? <button type="button" className="titan-connection" onClick={() => { if (correction.memory) void loadDetail(correction.memory); }}>
                        <small>{correction.direction === "replaces" ? "Replaces earlier memory" : "Replaced by newer memory"}</small>
                        <span dir="auto">{shortText(correction.memory.text, 150) || "(empty memory)"}</span>
                        {!visibleIds.has(correction.memory.nodeId) ? <small>Outside current graph and list</small> : null}
                      </button> : <p className="titan-detail-status">{correction.direction === "replaces" ? "Earlier memory" : "Newer memory"} unavailable: {correction.memoryId}</p>}
                      {correction.evidence ? <p className="titan-detail-status">{correction.evidence}</p> : null}
                      {correction.timestamp ? <p className="titan-detail-status">Recorded {formatTimestamp(correction.timestamp)}</p> : null}
                    </div>
                  ))}
                  {!detail.response.corrections.length && !detail.response.correctionsPartial ? <p className="titan-detail-status">No stored corrections found.</p> : null}
                  {detail.response.correctionsPartial ? <p className="titan-detail-status">Correction history is partial.</p> : null}
                </section>
              ) : null}
              {detail.response?.scene !== undefined || detail.response?.memoryEvidence ? (
                <section className="titan-connections" aria-label="Source scene">
                  <h3>Source scene</h3>
                  {detail.response.scene ? <>
                    <p className="titan-detail-status">{detail.response.scene.status} · {detail.response.scene.id}</p>
                    {detail.response.scene.eventIds.length || detail.response.scene.userText || detail.response.scene.assistantText ? <details className="titan-evidence"><summary>Event IDs and excerpts</summary>
                      {detail.response.scene.eventIds.length ? <p>Events: {detail.response.scene.eventIds.join(", ")}</p> : null}
                      {detail.response.scene.userText ? <p><strong>User and system context</strong><br />{detail.response.scene.userText}</p> : null}
                      {detail.response.scene.assistantText ? <p><strong>Assistant and tool context</strong><br />{detail.response.scene.assistantText}</p> : null}
                    </details> : null}
                  </> : <p className="titan-detail-status">No source scene linked.</p>}
                  {detail.response.memoryEvidence ? <details className="titan-evidence"><summary>Evidence saved with this memory</summary>
                    {detail.response.memoryEvidence.eventIds.length ? <p>Cited events: {detail.response.memoryEvidence.eventIds.join(", ")}</p> : null}
                    {detail.response.memoryEvidence.userText ? <p><strong>User-side excerpt</strong><br />{detail.response.memoryEvidence.userText}</p> : null}
                    {detail.response.memoryEvidence.assistantText ? <p><strong>Assistant-side excerpt</strong><br />{detail.response.memoryEvidence.assistantText}</p> : null}
                  </details> : null}
                </section>
              ) : null}
              {detail.response?.patterns?.length ? <section className="titan-connections" aria-label="Pattern evidence">
                <h3>Pattern evidence</h3>
                {detail.response.patterns.map((pattern) => <p className="titan-detail-status" key={`${pattern.id}:${pattern.role}`}>{pattern.title} · {pattern.status} · {pattern.role} · {pattern.id}</p>)}
              </section> : null}
              {selectedMetadata.event_date || selectedMetadata.event_start || selectedMetadata.event_end ? (
                <dl className="titan-metadata titan-event-time">
                  {selectedMetadata.event_date ? <div><dt>Event date</dt><dd>{selectedMetadata.event_date}</dd></div> : null}
                  {selectedMetadata.event_start ? <div><dt>Event start</dt><dd>{selectedMetadata.event_start}</dd></div> : null}
                  {selectedMetadata.event_end ? <div><dt>Event end</dt><dd>{selectedMetadata.event_end}</dd></div> : null}
                </dl>
              ) : null}
              <div className="titan-connections">
                <h3>Similar memories in graph</h3>
                {neighbors.length ? neighbors.map(({ memory, weight }) => (
                  <button type="button" className="titan-connection" key={memory.nodeId} onClick={() => void loadDetail(memory)}>
                    <span dir="auto">{shortText(memory.text, 120) || "(empty memory)"}</span><small>{percent(weight)} similarity</small>
                  </button>
                )) : <p className="titan-detail-status">No similarity links in this view</p>}
              </div>
              <details className="titan-evidence titan-technical-details">
                <summary>Memory metadata</summary>
                <dl className="titan-metadata">
                  <div><dt>Memory ID</dt><dd>{selectedMemory.memoryId}</dd></div>
                  {selectedMemory.stream ? <div><dt>Stream</dt><dd>{selectedMemory.stream}</dd></div> : null}
                  {selectedMemory.sessionId ? <div><dt>Session</dt><dd>{selectedMemory.sessionId}</dd></div> : null}
                  {metadataEntries(selectedMetadata).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
                </dl>
              </details>
            </div>
          </aside>
        ) : null}
      </div>
    </main>
  );
}
