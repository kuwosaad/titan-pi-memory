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
  formatTimestamp,
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

class GraphErrorBoundary extends Component<{ children: ReactNode }, { error: boolean }> {
  state = { error: false };

  static getDerivedStateFromError() {
    return { error: true };
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

export function ExplorerPanel({ threadId }: ExplorerProps) {
  const rpc = useRpc<typeof explorerContract>();
  const queryGateRef = useRef<RequestGate | null>(null);
  const pageGateRef = useRef<RequestGate | null>(null);
  const detailGateRef = useRef<RequestGate | null>(null);
  const catalogGateRef = useRef<RequestGate | null>(null);
  const activeRequestsRef = useRef<Set<string> | null>(null);
  if (!queryGateRef.current) queryGateRef.current = createRequestGate();
  if (!pageGateRef.current) pageGateRef.current = createRequestGate();
  if (!detailGateRef.current) detailGateRef.current = createRequestGate();
  if (!catalogGateRef.current) catalogGateRef.current = createRequestGate();
  if (!activeRequestsRef.current) activeRequestsRef.current = new Set<string>();
  const queryGate = queryGateRef.current;
  const pageGate = pageGateRef.current;
  const detailGate = detailGateRef.current;
  const catalogGate = catalogGateRef.current;
  const activeRequests = activeRequestsRef.current;

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
  const [loading, setLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [listVisible, setListVisible] = useState(true);
  const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter>("all");

  const cancelRequest = useCallback((request: string) => {
    if (!request || !activeRequests.delete(request)) return;
    void rpc.call("cancel", { threadId, requestId: request } as RpcInput<"cancel">).catch(() => undefined);
  }, [activeRequests, rpc, threadId]);

  const beginRequest = useCallback((gate: RequestGate, prefix: string) => {
    const id = requestId(prefix);
    const previous = gate.activate(id);
    cancelRequest(previous || "");
    activeRequests.add(id);
    return id;
  }, [activeRequests, cancelRequest]);

  const isCurrent = useCallback((gate: RequestGate, id: string) => gate.isCurrent(id), []);

  const cancelGate = useCallback((gate: RequestGate) => {
    cancelRequest(gate.activate("") || "");
  }, [cancelRequest]);

  const loadCatalog = useCallback(async () => {
    const id = beginRequest(catalogGate, "catalog");
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const response = await rpc.call("catalog", {
        threadId,
        requestId: id,
      } as RpcInput<"catalog">);
      if (!isCurrent(catalogGate, id)) return;
      setCatalog(response as CatalogResponse);
    } catch (requestError) {
      if (!isCurrent(catalogGate, id)) return;
      setCatalogError(errorMessage(requestError));
    } finally {
      if (isCurrent(catalogGate, id)) setCatalogLoading(false);
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, catalogGate, isCurrent, rpc, threadId]);

  const loadDetail = useCallback(async (memory: MemorySummary) => {
    const id = beginRequest(detailGate, "detail");
    setSelectedMemory(memory);
    setDetail({ response: null, loading: true, error: null });
    try {
      const response = await rpc.call("detail", {
        threadId,
        requestId: id,
        sourceAgent: memory.sourceAgent,
        memoryId: memory.memoryId,
      } as RpcInput<"detail">);
      if (!isCurrent(detailGate, id)) return;
      setDetail({ response: response as DetailResponse, loading: false, error: null });
    } catch (requestError) {
      if (!isCurrent(detailGate, id)) return;
      setDetail({ response: null, loading: false, error: errorMessage(requestError) });
    } finally {
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, detailGate, isCurrent, rpc, threadId]);

  const loadQuery = useCallback(async (nextFilters: ExplorerFilters) => {
    const id = beginRequest(queryGate, "query");
    cancelGate(pageGate);
    const requestFilters = cleanFilters(nextFilters);
    setLoading(true);
    setPageLoading(false);
    setGraphError(null);
    setListError(null);
    setWarnings([]);
    setSnapshot(null);
    setItems([]);
    setSearchPage(1);
    setPageInput("1");
    setTotalItems(null);
    setTotalPages(1);
    setNextCursor(null);
    try {
      const snapshotResponse = await rpc.call("snapshot", {
        threadId,
        requestId: id,
        filters: requestFilters,
      } as RpcInput<"snapshot">);
      if (!isCurrent(queryGate, id)) return;
      const bounded = boundSnapshot(snapshotResponse as SnapshotResponse);
      setSnapshot(bounded);
      setWarnings(bounded.warnings || []);
    } catch (requestError) {
      if (!isCurrent(queryGate, id)) return;
      setGraphError(errorMessage(requestError));
    }

    try {
      const searchResponse = await rpc.call("search", {
        threadId,
        requestId: id,
        filters: requestFilters,
        page: 1,
      } as RpcInput<"search">);
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
      setNextCursor(null);
    } finally {
      if (isCurrent(queryGate, id)) setLoading(false);
      activeRequests.delete(id);
    }
  }, [activeRequests, beginRequest, cancelGate, detailGate, isCurrent, pageGate, queryGate, rpc, threadId]);

  const loadPage = useCallback(async (page: number) => {
    if (pageLoading || loading || page < 1 || page > totalPages) return;
    const id = beginRequest(pageGate, "page");
    setPageLoading(true);
    setListError(null);
    try {
      const response = await rpc.call("search", {
        threadId,
        requestId: id,
        filters: cleanFilters(filters),
        page,
      } as RpcInput<"search">);
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
  }, [activeRequests, beginRequest, filters, isCurrent, loading, pageGate, pageLoading, rpc, threadId, totalPages]);

  const refreshAll = useCallback(() => {
    void loadCatalog();
    void loadQuery(filters);
    if (selectedMemory) void loadDetail(selectedMemory);
  }, [filters, loadCatalog, loadDetail, loadQuery, selectedMemory]);

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

  useEffect(() => () => {
    queryGate.close();
    pageGate.close();
    detailGate.close();
    catalogGate.close();
    for (const request of Array.from(activeRequests)) cancelRequest(request);
  }, [activeRequests, cancelRequest, catalogGate, detailGate, pageGate, queryGate]);

  const graphMemories = snapshot?.nodes || [];
  const visibleIds = useMemo(() => new Set([...graphMemories, ...items].map((memory) => memory.nodeId)), [graphMemories, items]);
  const neighbors = selectedMemory ? connectedMemories(selectedMemory.nodeId, snapshot, graphMemories) : [];
  const pageList = useMemo(() => pageTokens(searchPage, totalPages), [searchPage, totalPages]);
  const catalogAgents = (catalog?.agents || []) as CatalogAgent[];
  const catalogTypes = catalog?.types || [];
  const allWarnings = mergeWarnings(warnings, catalog?.warnings).filter(
    (warning) => !(snapshot && snapshot.nodes.length >= MAX_GRAPH_NODES && warning === `Graph limited to ${MAX_GRAPH_NODES} memories`),
  );
  const hasActiveFilters = Boolean(
    queryInput.trim() || filters.agent || filters.type || filters.dateFrom || filters.dateTo,
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
            disabled={loading || catalogLoading}
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
          />
        </label>
        <label className="titan-field">
          <span className="titan-visually-hidden">Agent</span>
          <select value={filters.agent || ""} onChange={(event) => updateFilter("agent", event.target.value)} aria-label="Filter by agent">
            <option value="">All agents</option>
            {catalogAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.id} · {agent.count}</option>)}
          </select>
        </label>
        <label className="titan-field">
          <span className="titan-visually-hidden">Type</span>
          <select value={filters.type || ""} onChange={(event) => updateFilter("type", event.target.value)} aria-label="Filter by type">
            <option value="">All types</option>
            {catalogTypes.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </label>
        <details className="titan-more-filters">
          <summary>More filters</summary>
          <div className="titan-more-filters-content">
            <label className="titan-date-field">
              <span>From</span>
              <input type="date" value={filters.dateFrom || ""} onChange={(event) => updateFilter("dateFrom", event.target.value)} aria-label="Filter from date" />
            </label>
            <label className="titan-date-field">
              <span>To</span>
              <input type="date" value={filters.dateTo || ""} onChange={(event) => updateFilter("dateTo", event.target.value)} aria-label="Filter to date" />
            </label>
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
            {showingLatestGraphCap ? <span className="titan-inline-note">Showing latest 150</span> : snapshot?.partial ? <span className="titan-badge titan-badge--warning">Partial</span> : null}
          </div>
          {loading ? <div className="titan-loading" role="status">Loading…</div> : null}
          {graphError ? <div className="titan-inline-error" role="alert">Graph unavailable: {graphError} <button type="button" className="titan-button" onClick={() => void loadQuery(filters)}>Retry</button></div> : null}
          {!loading && !graphError && snapshot && snapshot.nodes.length === 0 ? <div className="titan-empty" role="status">No memories found</div> : null}
          {!loading && snapshot && snapshot.nodes.length ? (
            <GraphErrorBoundary key={`${snapshot.nodes.length}:${snapshot.edges.length}:${snapshot.nodes[0]?.nodeId || "empty"}`}>
              <MemoryGraph
                snapshot={snapshot}
                selectedNodeId={graphMemories.some((memory) => memory.nodeId === selectedNodeId) ? selectedNodeId : null}
                onSelect={(memory) => void loadDetail(memory)}
                relationshipFilter={relationshipFilter}
                onRelationshipFilterChange={setRelationshipFilter}
              />
            </GraphErrorBoundary>
          ) : null}
        </section>

        {listVisible ? (
          <section className="titan-panel titan-panel--list" aria-labelledby="titan-list-title">
            <div className="titan-panel-heading">
              <h2 id="titan-list-title">Memories</h2>
              <span className="titan-count" aria-live="polite">{totalItems ?? items.length}</span>
            </div>
            {listError ? <div className="titan-inline-error" role="alert">Memories unavailable: {listError} <button type="button" className="titan-button" onClick={() => void loadQuery(filters)}>Retry</button></div> : null}
            {items.length === 0 && !loading && !listError ? <div className="titan-empty" role="status">No matching memories</div> : (
              <ul className="titan-memory-list">
                {items.map((memory) => (
                  <li key={memory.nodeId}>
                    <button
                      type="button"
                      className={memory.nodeId === selectedNodeId ? "titan-memory-row titan-memory-row--selected" : "titan-memory-row"}
                      aria-pressed={memory.nodeId === selectedNodeId}
                      onClick={() => void loadDetail(memory)}
                    >
                      <span className="titan-memory-row-meta"><span>{memory.type || "Memory"}</span><span>{memory.sourceAgent}</span></span>
                      <span className="titan-memory-row-text">{shortText(memory.text)}</span>
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
              <p className="titan-inspector-meta">{selectedMemory.sourceAgent}{selectedMemory.type ? ` · ${selectedMemory.type}` : ""} · Recorded {formatTimestamp(selectedMemory.timestamp)}</p>
              {!visibleIds.has(selectedMemory.nodeId) ? <p className="titan-scope-note">Outside the current graph and list</p> : null}
              <p className="titan-detail-text">{detail.response?.text || selectedMemory.text}</p>
              {detail.response?.truncated ? <p className="titan-detail-status">Text truncated</p> : null}
              {detail.response?.warnings?.map((warning) => <p className="titan-detail-status" role="status" key={warning}>{warning}</p>)}
              {detail.response?.corrections !== undefined ? (
                <section className="titan-connections" aria-label="Correction history">
                  <h3>Correction history</h3>
                  {detail.response.corrections.map((correction) => (
                    <div className="titan-correction" key={`${correction.direction}:${correction.memoryId}`}>
                      {correction.memory ? <button type="button" className="titan-connection" onClick={() => { if (correction.memory) void loadDetail(correction.memory); }}>
                        <small>{correction.direction === "replaces" ? "Replaces earlier memory" : "Replaced by newer memory"}</small>
                        <span>{shortText(correction.memory.text, 150)}</span>
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
                    <span>{shortText(memory.text, 120)}</span><small>{percent(weight)} similarity</small>
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
