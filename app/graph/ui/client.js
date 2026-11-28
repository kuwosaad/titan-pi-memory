const graphData = {
    nodes: ${nodes_json},
    links: ${links_json}
};
// memory_total counts every memory in scope, including any the server left out.
const graphMeta = ${graph_meta_json};

const byId = new Map(graphData.nodes.map(node => [node.id, node]));
const qualified = (agent, id) => agent + '::' + id;
const byMemory = new Map(graphData.nodes.map(node => [qualified(node.source_agent, node.raw_id), node]));
const rootStyle = getComputedStyle(document.documentElement);
const sidebar = document.getElementById('memorySidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarScrim = document.getElementById('sidebarScrim');
const sidebarNotice = document.getElementById('sidebarNotice');
const sidebarEmpty = document.getElementById('sidebarEmpty');
const compact = window.matchMedia('(max-width: 720px)');
const workspace = document.getElementById('workspace');
const graphStage = document.getElementById('graphStage');
const canvas = document.getElementById('graph');
const searchInput = document.getElementById('sidebarSearch');
const typeFilter = document.getElementById('typeFilter');
const relationshipFilter = document.getElementById('relationshipFilter');
const graphToolbar = document.getElementById('graphToolbar');
const graphFooter = document.getElementById('graphFooter');
const graphLayout = document.getElementById('graphLayout');
const graphLayoutForm = document.getElementById('graphLayoutForm');
const graphForces = document.getElementById('graphForces');
const graphDisplay = document.getElementById('graphDisplay');
const reheatGraph = document.getElementById('reheatGraph');
const resetLayout = document.getElementById('resetLayout');
const localDepthField = document.getElementById('localDepthField');
const localDepth = graphLayoutForm.elements.namedItem('localDepth');
const graphLegend = document.getElementById('graphLegend');
const agentLegend = document.getElementById('agentLegend');
const legendEntries = document.querySelectorAll('[data-link-kind]');
const memoryList = document.getElementById('memoryList');
const memoryViewSwitch = document.getElementById('memoryViewSwitch');
const graphStats = document.getElementById('graphStats');
const scopeLabel = document.getElementById('scopeLabel');
const scopeText = scopeLabel.textContent;
const capNotice = document.getElementById('capNotice');
const physicsNotice = document.getElementById('physicsNotice');
const graphEmpty = document.getElementById('graphEmpty');
const emptyTitle = document.getElementById('emptyTitle');
const emptyDetail = document.getElementById('emptyDetail');
const clearFilters = document.getElementById('clearFilters');
const retryLoad = document.getElementById('retryLoad');
const searchHint = document.getElementById('searchHint');
const correctionCycle = document.getElementById('correctionCycle');
const graphDescription = document.getElementById('graphDescription');
const graphAnnouncer = document.getElementById('graphAnnouncer');
const focusContext = document.getElementById('focusContext');
const focusAnchor = document.getElementById('focusAnchor');
const clearFocus = document.getElementById('clearFocus');
const cameraControls = document.getElementById('cameraControls');
const zoomValue = document.getElementById('zoomValue');
const dialog = document.getElementById('memoryPreview');
const previewTitle = document.getElementById('memoryPreviewTitle');
const previewLoadStatus = document.getElementById('previewLoadStatus');
const previewScroll = document.getElementById('previewScroll');
const previewContent = document.getElementById('previewContent');
const previewText = document.getElementById('memoryPreviewBody');
const previewMeta = document.getElementById('memoryPreviewMeta');
const previewIdentifiers = document.getElementById('memoryIdentifiers');
const previewEvidence = document.getElementById('memoryPreviewEvidence');
const previewConnections = document.getElementById('memoryPreviewConnections');
const previewScope = document.getElementById('previewScope');
const similarMemories = document.getElementById('similarMemories');
const previewActions = document.getElementById('previewActions');
const focusMemory = document.getElementById('focusMemory');
const focusNote = document.getElementById('focusNote');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const DETAIL_TIMEOUT_MS = 8000;
const listRows = new Map();
const hiddenAgents = new Set();
let dateRange = graphData.nodes.some(node => node.recent) ? 'recent' : 'all';
let focusedId = null;
let selected = null;
let evidenceRequest = null;
let previewMotion = null;
let returnFocus = null;
let visibleIds = new Set();
let filteredIds = new Set();
let visibleLinks = [];
let desktopSidebarOpen = true;
let physicsAvailable = true;
let searchTimer = null;
let descriptionTimer = null;

function append(parent, tag, className, text) {
    const child = document.createElement(tag);
    if (className) child.className = className;
    if (text != null) child.textContent = text;
    parent.appendChild(child);
    return child;
}

// Memory text, types and agent names can hold any script or direction
// override, so each sits in its own direction-isolated element.
function appendText(parent, tag, className, text) {
    const child = append(parent, tag, className, text);
    child.dir = 'auto';
    return child;
}

function numberLabel(count, singular, plural = singular + 's') {
    return count + ' ' + (count === 1 ? singular : plural);
}

function formatRecorded(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return Number.isNaN(date.valueOf()) ? timestamp : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function displayText(node) {
    const text = node?.text;
    return typeof text === 'string' && text.trim() ? text : 'Untitled memory';
}

function displayType(type) {
    const text = String(type || 'memory');
    return shortenText(text.charAt(0).toUpperCase() + text.slice(1), 40);
}

function agentLabel(agent) {
    return agent || 'unknown agent';
}

const agents = [...new Set(graphData.nodes.map(node => node.source_agent || ''))].sort((a, b) => a.localeCompare(b));
const agentPalette = Array.from({ length: 8 }, (_, index) => rootStyle.getPropertyValue('--agent-' + (index + 1)).trim()).filter(Boolean);
const agentColors = new Map(agents.map((agent, index) => [agent, agentPalette[index % agentPalette.length] || '#56B4E9']));
for (const node of graphData.nodes) {
    node.color = agentColors.get(node.source_agent || '');
    node.searchKey = [node.text, node.source_agent, node.session_id].map(value => String(value || '')).join('\n').toLowerCase();
}
// Nodes carry only the opening part of long memories, and search runs on
// what the page has, so say so when any loaded memory was shortened.
const searchShortened = graphData.nodes.some(node => node.text_truncated);
if (searchShortened) searchInput.setAttribute('aria-describedby', 'searchHint');

function setSidebar(open) {
    const overlay = open && compact.matches;
    const active = document.activeElement;
    if ((!open && sidebar.contains(active)) || (overlay && graphStage.contains(active))) {
        sidebarToggle.focus({ preventScroll: true });
    }
    sidebar.hidden = !open;
    sidebarScrim.hidden = !overlay;
    graphStage.inert = overlay;
    workspace.classList.toggle('sidebar-hidden', !open);
    sidebarToggle.setAttribute('aria-expanded', String(open));
}

for (const type of [...new Set(graphData.nodes.map(node => node.type))].sort()) {
    const option = append(typeFilter, 'option', '', displayType(type));
    option.value = type;
    option.title = type;
}
for (const agent of agents) {
    const toggles = agents.length > 1;
    const chip = append(agentLegend, toggles ? 'button' : 'span', 'agent-chip');
    chip.style.setProperty('--agent-color', agentColors.get(agent));
    append(chip, 'span', 'agent-swatch').setAttribute('aria-hidden', 'true');
    appendText(chip, 'span', 'agent-name', shortenText(agentLabel(agent), 24));
    if (!toggles) continue;
    chip.type = 'button';
    chip.dataset.agent = agent;
    chip.setAttribute('aria-pressed', 'true');
    chip.title = 'Show or hide ' + agentLabel(agent) + ' memories';
}
for (const node of graphData.nodes) {
    const row = append(memoryList, 'div');
    row.setAttribute('role', 'listitem');
    const button = append(row, 'button', 'memory-item');
    button.type = 'button';
    button.dataset.id = node.id;
    button.dataset.recent = String(node.recent);
    button.setAttribute('aria-controls', 'memoryPreview');
    button.setAttribute('aria-label', [shortenText(displayText(node), 140), displayType(node.type), agentLabel(node.source_agent)].join(', '));
    button.style.setProperty('--memory-color', node.color);
    append(button, 'span', 'memory-dot').setAttribute('aria-hidden', 'true');
    const title = appendText(button, 'span', 'memory-title', shortenText(displayText(node), 400));
    title.classList.toggle('is-placeholder', displayText(node) !== node.text);
    const meta = append(button, 'span', 'memory-meta');
    appendText(meta, 'span', 'memory-type', displayType(node.type)).title = String(node.type || 'memory');
    append(meta, 'span', '', ' · ');
    appendText(meta, 'span', 'memory-agent', agentLabel(node.source_agent));
    listRows.set(node.id, { row, button });
}
memoryList.addEventListener('click', event => {
    const row = event.target.closest('.memory-item');
    if (row) openMemory(byId.get(row.dataset.id), row);
});

// The canvas area left uncovered by the toolbar, footer and details drawer.
function graphInsets() {
    const stage = canvas.getBoundingClientRect();
    const toolbar = graphToolbar.getBoundingClientRect();
    const footer = graphFooter.getBoundingClientRect();
    const insets = {
        top: Math.max(24, toolbar.bottom - stage.top + 16),
        bottom: Math.max(24, stage.bottom - footer.top + 16),
        left: 24,
        right: 24,
    };
    // Offsets ignore the drawer's opening transition. The stylesheet shows a
    // full-width bottom sheet on phones and narrow stages.
    if (dialog.open) {
        if (dialog.offsetWidth >= stage.width - 1) insets.bottom = Math.max(insets.bottom, stage.height - dialog.offsetTop + 16);
        else insets.right = Math.max(insets.right, stage.width - dialog.offsetLeft + 16);
    }
    return insets;
}

function syncSidebarLayout() { setSidebar(!compact.matches && desktopSidebarOpen); }
syncSidebarLayout();
compact.addEventListener('change', syncSidebarLayout);
const graph = createMemoryGraph(canvas, graphData, {
    onOpen: (node, via) => openMemory(node, via === 'keyboard' ? canvas : null),
    onCameraChange: zoom => { zoomValue.textContent = Math.round(zoom * 100) + '%'; },
    onCursor: (node, links) => announce(describeNode(node, links)),
    onPhysicsUnavailable: () => {
        physicsAvailable = false;
        syncControls();
    },
    getInsets: graphInsets,
    reducedMotion,
    physicsSource: JSON.parse(document.getElementById('graphPhysics').textContent),
});

function announce(message) {
    graphAnnouncer.textContent = '';
    requestAnimationFrame(() => { graphAnnouncer.textContent = message; });
}

function describeNode(node, links) {
    return [displayType(node.type), agentLabel(node.source_agent), numberLabel(links, 'link')].join(' · ') + ': ' + shortenText(displayText(node), 120);
}

function describeGraph(nodes, links) {
    if (!graph.supported) return 'The graph view is unavailable in this browser. Use the saved memories list.';
    if (!nodes.length) return emptyTitle.textContent + '.';
    const perAgent = new Map();
    for (const node of nodes) perAgent.set(node.source_agent || '', (perAgent.get(node.source_agent || '') || 0) + 1);
    const degrees = new Map();
    for (const link of links) {
        for (const id of [link.source, link.target]) degrees.set(id, (degrees.get(id) || 0) + 1);
    }
    const corrections = links.filter(link => link.kind === 'supersedes').length;
    const connected = nodes.filter(node => degrees.get(node.id)).sort((a, b) => degrees.get(b.id) - degrees.get(a.id)).slice(0, 3);
    const parts = [
        (focusedId && visibleIds.has(focusedId) ? 'Neighborhood of "' + shortenText(displayText(byId.get(focusedId)), 60) + '": ' : '') +
        numberLabel(nodes.length, 'memory', 'memories') + ' from ' +
        [...perAgent].map(([agent, count]) => agentLabel(agent) + ' (' + count + ')').join(', ') + '.',
        numberLabel(links.length - corrections, 'similarity link') + ' and ' + numberLabel(corrections, 'correction') +
        (corrections ? '; each correction points from the replacement to the memory it replaced.' : '.'),
    ];
    if (connected.length) {
        parts.push('Most connected: ' + connected.map(node => '"' + shortenText(displayText(node), 60) + '" (' + numberLabel(degrees.get(node.id), 'link') + ')').join(', ') + '.');
    }
    parts.push('Arrow keys move between memories, Enter opens one, Shift with arrow keys pans, and Escape goes back.');
    return parts.join(' ');
}

function localNeighborhood(root, nodes, relationship) {
    const eligible = new Set(nodes.map(node => node.id));
    const links = graphData.links.filter(link => eligible.has(link.source) && eligible.has(link.target) && (relationship === 'all' || link.kind === relationship));
    const visited = new Set([root]);
    let frontier = [root];
    for (let depth = 0; frontier.length && depth < localDepth.valueAsNumber; depth++) {
        const current = new Set(frontier);
        frontier = [];
        for (const link of links) {
            const neighbor = current.has(link.source) ? link.target : current.has(link.target) ? link.source : null;
            if (neighbor && !visited.has(neighbor)) {
                frontier.push(neighbor);
                visited.add(neighbor);
            }
        }
    }
    return visited;
}

// Disables controls that cannot do anything: everything in an empty store,
// the camera and layout without a canvas or with nothing shown, and the forces
// without a running simulation. Filters stay usable so they can be undone.
function syncControls() {
    const storeEmpty = !graphData.nodes.length;
    const graphless = storeEmpty || !graph.supported;
    const nothingShown = graphless || !visibleIds.size;
    for (const control of [searchInput, typeFilter, ...memoryViewSwitch.querySelectorAll('button')]) control.disabled = storeEmpty;
    relationshipFilter.disabled = nothingShown || !graphData.links.length;
    for (const button of cameraControls.querySelectorAll('button')) button.disabled = nothingShown;
    zoomValue.hidden = nothingShown;
    graphLegend.hidden = storeEmpty;
    graphLayout.inert = nothingShown;
    graphLayout.classList.toggle('is-disabled', nothingShown);
    if (nothingShown) graphLayout.open = false;
    const forcesIdle = !physicsAvailable || visibleIds.size < 2;
    graphForces.disabled = forcesIdle;
    reheatGraph.disabled = forcesIdle;
    // Without physics, Reset still restores the display sliders and the
    // starting positions of dragged memories.
    resetLayout.disabled = nothingShown;
    physicsNotice.hidden = physicsAvailable || graphless;
    canvas.tabIndex = graphless ? -1 : 0;
}

function updateView(cameraAction = 'keep') {
    const query = searchInput.value.trim().toLowerCase();
    const type = typeFilter.value;
    const relationship = relationshipFilter.value;
    const filtered = graphData.nodes.filter(node => (
        (dateRange === 'all' || node.recent) && (!type || node.type === type) &&
        !hiddenAgents.has(node.source_agent || '') && (!query || node.searchKey.includes(query))
    ));
    // A filter that hides the focused memory pauses focus instead of clearing it.
    const focusVisible = Boolean(focusedId && filtered.some(node => node.id === focusedId));
    let nodes = filtered;
    if (focusVisible) {
        const neighborhood = localNeighborhood(focusedId, filtered, relationship);
        nodes = filtered.filter(node => neighborhood.has(node.id));
    }
    visibleIds = new Set(nodes.map(node => node.id));
    filteredIds = new Set(filtered.map(node => node.id));
    for (const [id, item] of listRows) item.row.hidden = !visibleIds.has(id);
    visibleLinks = graphData.links.filter(link => visibleIds.has(link.source) && visibleIds.has(link.target) && (relationship === 'all' || link.kind === relationship));
    const similarities = visibleLinks.filter(link => link.kind === 'similarity').length;
    const corrections = visibleLinks.length - similarities;
    for (const entry of legendEntries) {
        const kind = entry.dataset.linkKind;
        entry.hidden = (relationship !== 'all' && kind !== relationship) || !graphData.links.some(link => link.kind === kind);
    }
    for (const chip of agentLegend.querySelectorAll('[data-agent]')) chip.setAttribute('aria-pressed', String(!hiddenAgents.has(chip.dataset.agent)));
    const shownAgents = agents.filter(agent => !hiddenAgents.has(agent)).map(agent => shortenText(agentLabel(agent), 24));
    const agentScope = shownAgents.length && shownAgents.length <= 2 ? shownAgents.join(', ') : shownAgents.length + ' of ' + agents.length + ' agents';
    scopeLabel.textContent = !hiddenAgents.size ? scopeText : scopeText === 'All agents' ? agentScope : scopeText + ' · ' + agentScope;

    const total = Math.max(Number(graphMeta.memory_total) || 0, graphData.nodes.length);
    const capped = total > graphData.nodes.length;
    // Narrow screens have no room for the cap notice, so the count says it.
    const counts = [capped && compact.matches ? nodes.length + ' of ' + numberLabel(total, 'memory', 'memories') : numberLabel(nodes.length, 'memory', 'memories')];
    // Narrow screens leave the similarity count to the legend.
    if (similarities && !compact.matches) counts.push(numberLabel(similarities, 'similarity link'));
    if (corrections) counts.push(numberLabel(corrections, 'correction'));
    graphStats.textContent = counts.join(' · ');
    graphStats.title = graphStats.textContent;
    document.getElementById('sidebarCount').textContent = String(nodes.length);

    const memoriesNote = capped ? 'Showing the newest ' + graphData.nodes.length + ' of ' + total + ' memories' : '';
    const omittedLinks = Number(graphMeta.link_omitted) || 0;
    const notes = [memoriesNote, omittedLinks ? numberLabel(omittedLinks, 'weaker link') + ' not drawn' : '', ...(graphMeta.warnings || [])].filter(Boolean);
    capNotice.hidden = !notes.length;
    capNotice.textContent = capNotice.title = notes.join(' · ');
    // CSS shows the toolbar copy on wide screens and the sidebar copy on narrow ones.
    sidebarNotice.hidden = !notes.length;
    sidebarNotice.textContent = notes.join(' · ');

    const storeEmpty = !graphData.nodes.length;
    searchHint.hidden = !searchShortened || !query;
    // With nothing loaded, the read warnings explain why; the empty state hides
    // the toolbar notice, so it shows them itself.
    const readFailed = storeEmpty && Boolean(graphMeta.warnings?.length);
    emptyTitle.textContent = !graph.supported ? 'Graph view unavailable' : readFailed ? 'Memories could not be read'
        : storeEmpty ? 'No memories yet' : 'No memories match these filters';
    emptyDetail.textContent = !graph.supported ? 'This browser could not draw the graph. Use the saved memories list.'
        : readFailed ? graphMeta.warnings.join(' · ')
        : storeEmpty ? 'Memories your agents save will appear here.' : 'Try another search, type, agent or date range.';
    clearFilters.hidden = storeEmpty || !graph.supported;
    retryLoad.hidden = !readFailed;
    graphEmpty.hidden = graph.supported && nodes.length > 0;
    graphStage.toggleAttribute('data-empty', storeEmpty || !graph.supported);
    sidebarEmpty.hidden = nodes.length > 0;
    sidebarEmpty.textContent = readFailed ? 'Memories could not be read' : storeEmpty ? 'No memories yet' : 'No memories match these filters';

    focusContext.hidden = !focusedId;
    localDepthField.hidden = !focusVisible;
    focusAnchor.replaceChildren();
    focusAnchor.title = focusedId ? displayText(byId.get(focusedId)) : '';
    if (focusVisible) {
        focusAnchor.append('Focused on ');
        appendText(focusAnchor, 'bdi', '', shortenText(displayText(byId.get(focusedId)), 80));
    } else if (focusedId) {
        focusAnchor.append('Focused memory hidden by filters');
    }
    for (const button of memoryViewSwitch.querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.view === dateRange));
    }
    graph.setView(visibleIds, relationship, { fit: cameraAction === 'fit' });
    graph.setSelected(selected?.id || focusedId);
    graph.setFocusRoot(focusVisible ? focusedId : null);
    syncControls();
    if (selected) syncPreviewActions();
    clearTimeout(descriptionTimer);
    descriptionTimer = setTimeout(() => {
        graphDescription.textContent = describeGraph(nodes, visibleLinks);
        if (cameraAction === 'fit' && document.activeElement !== canvas) announce(graphStats.textContent);
    }, 400);
}

function previewField(parent, label, value) {
    if (value === null || value === undefined || value === '') return;
    append(parent, 'dt', '', label);
    appendText(parent, 'dd', '', String(value));
}

// Focus neighborhood needs the memory in view and at least one link to
// another memory that the filters keep.
function syncPreviewActions() {
    const shown = visibleIds.has(selected.id);
    previewActions.hidden = !shown;
    previewScope.hidden = shown;
    const kind = relationshipFilter.value;
    const linked = shown && graphData.links.some(link => (kind === 'all' || link.kind === kind) && (
        (link.source === selected.id && filteredIds.has(link.target)) || (link.target === selected.id && filteredIds.has(link.source))
    ));
    focusMemory.disabled = !linked;
    focusNote.hidden = linked;
}

function renderSimilarity(node) {
    similarMemories.replaceChildren();
    const neighbors = [];
    const revisions = new Set();
    for (const link of graphData.links) {
        if (link.kind !== 'supersedes') continue;
        if (link.source === node.id) revisions.add(link.target);
        if (link.target === node.id) revisions.add(link.source);
    }
    for (const link of graphData.links) {
        if (link.kind !== 'similarity') continue;
        const neighborId = link.source === node.id ? link.target : link.target === node.id ? link.source : null;
        if (neighborId && !revisions.has(neighborId)) neighbors.push({ node: byId.get(neighborId), weight: link.weight });
    }
    for (const item of neighbors.sort((a, b) => b.weight - a.weight).slice(0, 8)) {
        const button = append(similarMemories, 'button', 'preview-link');
        button.type = 'button';
        appendText(button, 'span', 'preview-link-text', shortenText(displayText(item.node), 240));
        append(button, 'span', 'memory-meta', Math.round(item.weight * 100) + '% similar');
        button.addEventListener('click', () => openMemory(item.node));
    }
    if (neighbors.length > 8) append(similarMemories, 'p', 'preview-note', numberLabel(neighbors.length - 8, 'more similarity link'));
    previewConnections.hidden = !neighbors.length;
}

function evidenceSection(title) {
    const section = append(previewEvidence, 'section', 'preview-section');
    append(section, 'h3', '', title);
    return section;
}

function sourceDisclosure(parent, label, source) {
    const details = append(parent, 'details');
    append(details, 'summary', '', label);
    if (source.id) append(details, 'p', 'preview-source-id', source.id);
    if (source.userText) appendText(details, 'blockquote', '', source.userText);
    if (source.assistantText) appendText(details, 'blockquote', '', source.assistantText);
    if (source.eventIds.length) append(details, 'p', 'preview-source-id', 'Events: ' + source.eventIds.join(', '));
}

function openRevision(memory) {
    const key = qualified(memory.sourceAgent, memory.memoryId);
    openMemory(byMemory.get(key) || {
        id: key, raw_id: memory.memoryId, source_agent: memory.sourceAgent,
        type: memory.type || 'memory', stream: memory.stream, text: memory.text,
        ts: memory.timestamp, session_id: memory.sessionId,
    });
}

function renderEvidence(payload, showingPayloadText) {
    if (payload.truncated && showingPayloadText) append(previewEvidence, 'p', 'preview-note', 'Memory text shortened for display');
    if (payload.corrections.length || payload.correctionsPartial) {
        const history = evidenceSection('Correction history');
        const revisions = append(history, 'div', 'preview-links');
        for (const entry of payload.corrections) {
            // Labels follow the stored replacement, which need not match the timestamps.
            const label = entry.direction === 'replaces' ? 'Replaces' : 'Replaced by';
            const revision = append(revisions, 'div', 'correction-step');
            if (entry.memory) {
                const button = append(revision, 'button', 'preview-link');
                button.type = 'button';
                append(button, 'span', 'revision-label', label);
                appendText(button, 'span', 'preview-link-text', shortenText(displayText(entry.memory), 240));
                button.addEventListener('click', () => openRevision(entry.memory));
            } else append(revision, 'p', 'preview-note', label + ' (unavailable): ' + entry.memoryId);
            if (entry.evidence) appendText(revision, 'blockquote', '', entry.evidence);
            if (entry.timestamp) append(revision, 'p', 'memory-meta', 'Recorded ' + formatRecorded(entry.timestamp));
        }
        if (payload.correctionsPartial) append(history, 'p', 'preview-note', 'Correction history is partial');
    }
    if (payload.scene || payload.memoryEvidence) {
        const sources = evidenceSection('Source');
        if (payload.scene) sourceDisclosure(sources, 'Source scene · ' + payload.scene.status, payload.scene);
        if (payload.memoryEvidence) sourceDisclosure(sources, 'Evidence saved with this memory', payload.memoryEvidence);
    }
    if (payload.patterns.length) {
        const patterns = evidenceSection('Pattern evidence');
        for (const pattern of payload.patterns) {
            append(patterns, 'p', 'preview-note', pattern.title + ' · ' + pattern.status + ' · ' + pattern.role);
        }
    }
    for (const [key, label] of [['event_date', 'Event date'], ['event_start', 'Event start'], ['event_end', 'Event end']]) {
        previewField(previewMeta, label, payload.metadata[key]);
    }
    for (const warning of payload.warnings) append(previewEvidence, 'p', 'preview-note', warning);
    previewEvidence.hidden = !previewEvidence.childElementCount;
}

async function fetchEvidence(node, signal) {
    const params = new URLSearchParams({ source_agent: node.source_agent, memory_id: node.raw_id });
    const response = await fetch('/api/graph/memory?' + params, { signal });
    if (!response.ok) throw new Error('Memory detail unavailable');
    return response.json();
}

// Whether the loaded corrections lead from this memory back to itself
// (replacement → replaced), which leaves no consistent order.
const replaced = new Map();
for (const link of graphData.links) {
    if (link.kind !== 'supersedes') continue;
    if (!replaced.has(link.source)) replaced.set(link.source, []);
    replaced.get(link.source).push(link.target);
}
function inCorrectionCycle(id) {
    const seen = new Set();
    const stack = [...(replaced.get(id) || [])];
    while (stack.length) {
        const next = stack.pop();
        if (next === id) return true;
        if (seen.has(next)) continue;
        seen.add(next);
        stack.push(...(replaced.get(next) || []));
    }
    return false;
}

function renderMemory(node) {
    if (selected) listRows.get(selected.id)?.button.removeAttribute('aria-current');
    selected = node;
    graph.setSelected(node.id);
    listRows.get(node.id)?.button.setAttribute('aria-current', 'true');
    previewTitle.textContent = displayType(node.type);
    previewTitle.title = String(node.type || 'memory');
    previewText.textContent = displayText(node);
    previewText.classList.toggle('is-placeholder', displayText(node) !== node.text);
    previewMeta.replaceChildren();
    previewField(previewMeta, 'Agent', node.source_agent);
    previewField(previewMeta, 'Recorded', formatRecorded(node.ts));
    previewField(previewMeta, 'Stream', node.stream);
    previewIdentifiers.parentElement.open = false;
    previewIdentifiers.replaceChildren();
    append(previewIdentifiers, 'p', 'preview-source-id', node.raw_id);
    if (node.session_id) append(previewIdentifiers, 'p', 'preview-source-id', 'Session: ' + node.session_id);
    if (node.turn != null) append(previewIdentifiers, 'p', 'preview-source-id', 'Turn: ' + node.turn);
    syncPreviewActions();
    correctionCycle.hidden = !inCorrectionCycle(node.id);
    previewEvidence.replaceChildren();
    previewEvidence.hidden = true;
    renderSimilarity(node);
}

// Loads correction and source details. Switching memories, closing the
// drawer or waiting past the timeout abandons the request.
async function loadEvidence(node, controller) {
    previewLoadStatus.textContent = 'Loading details…';
    const timer = setTimeout(() => controller.abort(), DETAIL_TIMEOUT_MS);
    let payload = null;
    try {
        payload = await fetchEvidence(node, controller.signal);
    } catch {
        payload = null;
    } finally {
        clearTimeout(timer);
    }
    if (evidenceRequest !== controller || !dialog.open) return;
    evidenceRequest = null;
    previewLoadStatus.textContent = '';
    if (payload) {
        // Keep the longer text: a payload is only shorter when it was shortened.
        const showingPayloadText = typeof payload.text === 'string' && payload.text.length > String(node.text || '').length;
        if (showingPayloadText) previewText.textContent = displayText({ text: payload.text });
        renderEvidence(payload, showingPayloadText);
        return;
    }
    const note = append(previewEvidence, 'p', 'preview-note', controller.signal.aborted ? 'Details took too long to load. ' : 'Source and correction details unavailable. ');
    const retry = append(note, 'button', 'inline-action', 'Retry');
    retry.type = 'button';
    retry.addEventListener('click', () => {
        previewEvidence.replaceChildren();
        previewEvidence.hidden = true;
        evidenceRequest = new AbortController();
        loadEvidence(node, evidenceRequest);
    });
    previewEvidence.hidden = false;
}

function openMemory(node, opener = null) {
    if (!node) return;
    const navigating = dialog.open;
    evidenceRequest?.abort();
    evidenceRequest = null;
    previewLoadStatus.textContent = '';
    previewMotion?.cancel();
    renderMemory(node);
    if (!navigating) {
        returnFocus = opener || document.activeElement;
        if (compact.matches && !sidebar.hidden) setSidebar(false);
        dialog.show();
        graphStage.classList.add('drawer-open');
    } else if (!reducedMotion.matches) {
        previewMotion = previewContent.animate?.([
            { opacity: .55, transform: 'translateY(4px)' },
            { opacity: 1, transform: 'translateY(0)' },
        ], { duration: 160, easing: 'cubic-bezier(.2, .7, .2, 1)' });
    }
    // Opening from the canvas returns there on close, even mid-navigation.
    if (opener === canvas) returnFocus = canvas;
    previewScroll.scrollTop = 0;
    previewTitle.focus({ preventScroll: true });
    requestAnimationFrame(() => graph.ensureVisible(node.id));
    if (node.source_agent && node.raw_id) {
        evidenceRequest = new AbortController();
        loadEvidence(node, evidenceRequest);
    }
}

function restoreFocus() {
    const target = returnFocus;
    returnFocus = null;
    if (target?.isConnected && target.getClientRects().length && !target.closest('[inert]')) target.focus({ preventScroll: true });
    else if (graph.supported && graphData.nodes.length) canvas.focus({ preventScroll: true });
}

function clearFocusMode() {
    focusedId = null;
    updateView('fit');
    canvas.focus({ preventScroll: true });
}

function resetFilters() {
    searchInput.value = '';
    typeFilter.value = '';
    hiddenAgents.clear();
    dateRange = 'all';
    updateView('fit');
}

sidebarToggle.addEventListener('click', () => {
    const open = sidebar.hidden;
    if (!compact.matches) desktopSidebarOpen = open;
    setSidebar(open);
});
function dismissSidebar() {
    setSidebar(false);
    sidebarToggle.focus({ preventScroll: true });
}
sidebarScrim.addEventListener('click', dismissSidebar);
document.getElementById('skipToGraph').addEventListener('click', event => {
    event.preventDefault();
    if (compact.matches) setSidebar(false);
    canvas.focus({ preventScroll: true });
});
// Escape steps back one level: the layout panel, the mobile sidebar, the
// details drawer, focus mode, then the keyboard cursor.
document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    if (graphLayout.open && graphLayout.contains(document.activeElement)) {
        graphLayout.open = false;
        graphLayout.querySelector('summary').focus({ preventScroll: true });
        return;
    }
    if (compact.matches && !sidebar.hidden) {
        event.preventDefault();
        dismissSidebar();
        return;
    }
    if (event.target === searchInput && searchInput.value) return;
    if (dialog.open) {
        event.preventDefault();
        dialog.close();
    } else if (focusedId) {
        event.preventDefault();
        clearFocusMode();
    } else if (graph.clearCursor()) {
        event.preventDefault();
    }
});
for (const button of memoryViewSwitch.querySelectorAll('button')) {
    button.addEventListener('click', () => { dateRange = button.dataset.view; updateView('fit'); });
}
searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => updateView('fit'), 150);
});
typeFilter.addEventListener('change', () => updateView('fit'));
relationshipFilter.addEventListener('change', () => updateView('fit'));
agentLegend.addEventListener('click', event => {
    const chip = event.target.closest('[data-agent]');
    if (!chip) return;
    if (hiddenAgents.has(chip.dataset.agent)) hiddenAgents.delete(chip.dataset.agent);
    else hiddenAgents.add(chip.dataset.agent);
    updateView('fit');
});
clearFilters.addEventListener('click', resetFilters);
retryLoad.addEventListener('click', () => window.location.reload());
function layoutValues(section) {
    return Object.fromEntries(Array.from(section.querySelectorAll('input'), input => [input.name, input.valueAsNumber]));
}
// With reduced motion every settings change settles synchronously in the
// worker, so only send the value once the slider is released.
graphForces.addEventListener('input', () => { if (!reducedMotion.matches) graph.setForces(layoutValues(graphForces)); });
graphForces.addEventListener('change', () => { if (reducedMotion.matches) graph.setForces(layoutValues(graphForces)); });
graphDisplay.addEventListener('input', () => graph.setDisplay(layoutValues(graphDisplay)));
localDepth.addEventListener('input', () => updateView('fit'));
// Reset restores the default sliders and lays the graph out again as on load;
// Rebalance unpins dragged memories and lets the layout find a new shape.
// Both refit the camera once the layout settles. form.reset() restores the
// sliders synchronously, so the values read right after it are the defaults.
resetLayout.addEventListener('click', () => {
    graphLayoutForm.reset();
    graph.setDisplay(layoutValues(graphDisplay));
    if (focusedId) updateView();
    graph.resetLayout(layoutValues(graphForces));
    announce('Layout reset');
});
reheatGraph.addEventListener('click', () => {
    graph.rebalance();
    announce('Rebalancing layout');
});
window.addEventListener('pagehide', event => { if (!event.persisted) graph.destroy(); });
clearFocus.addEventListener('click', clearFocusMode);
document.getElementById('zoomIn').addEventListener('click', () => graph.zoomBy(1.25));
document.getElementById('zoomOut').addEventListener('click', () => graph.zoomBy(1 / 1.25));
document.getElementById('fitGraph').addEventListener('click', () => graph.fit(!reducedMotion.matches));
document.getElementById('memoryPreviewClose').addEventListener('click', () => dialog.close());
focusMemory.addEventListener('click', () => {
    if (!selected || !visibleIds.has(selected.id) || focusMemory.disabled) return;
    focusedId = selected.id;
    returnFocus = canvas;
    dialog.close();
    updateView('fit');
    canvas.focus({ preventScroll: true });
});
dialog.addEventListener('close', () => {
    if (dialog.open) return;
    evidenceRequest?.abort();
    evidenceRequest = null;
    previewLoadStatus.textContent = '';
    previewMotion?.cancel();
    if (selected) listRows.get(selected.id)?.button.removeAttribute('aria-current');
    selected = null;
    graphStage.classList.remove('drawer-open');
    graph.setSelected(focusedId);
    const active = document.activeElement;
    if (!active || active === document.body || dialog.contains(active)) restoreFocus();
    else returnFocus = null;
});
// Stats use shorter wording on narrow screens.
compact.addEventListener('change', () => updateView());
updateView('fit');
