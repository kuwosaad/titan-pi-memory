const graphData = {
    nodes: ${nodes_json},
    links: ${links_json}
};

const byId = new Map(graphData.nodes.map(node => [node.id, node]));
const qualified = (agent, id) => agent + '::' + id;
const byMemory = new Map(graphData.nodes.map(node => [qualified(node.source_agent, node.raw_id), node]));
const sidebar = document.getElementById('memorySidebar');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarScrim = document.getElementById('sidebarScrim');
const compact = window.matchMedia('(max-width: 720px)');
const workspace = document.getElementById('workspace');
const graphStage = document.getElementById('graphStage');
const canvas = document.getElementById('graph');
const searchInput = document.getElementById('sidebarSearch');
const typeFilter = document.getElementById('typeFilter');
const relationshipFilter = document.getElementById('relationshipFilter');
const graphLayout = document.getElementById('graphLayout');
const graphLayoutForm = document.getElementById('graphLayoutForm');
const localDepthField = document.getElementById('localDepthField');
const localDepth = graphLayoutForm.elements.namedItem('localDepth');
const legendEntries = document.querySelectorAll('[data-link-kind]');
const memoryList = document.getElementById('memoryList');
const memoryViewSwitch = document.getElementById('memoryViewSwitch');
const graphStats = document.getElementById('graphStats');
const graphEmpty = document.getElementById('graphEmpty');
const focusContext = document.getElementById('focusContext');
const focusAnchor = document.getElementById('focusAnchor');
const clearFocus = document.getElementById('clearFocus');
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
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const listRows = new Map();
let dateRange = graphData.nodes.some(node => node.recent) ? 'recent' : 'all';
let focusedId = null;
let selected = null;
let evidenceRequest = null;
let previewMotion = null;
let visibleIds = new Set();
let desktopSidebarOpen = true;

function append(parent, tag, className, text) {
    const child = document.createElement(tag);
    if (className) child.className = className;
    if (text != null) child.textContent = text;
    parent.appendChild(child);
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

function titleCase(text) {
    return text.charAt(0).toUpperCase() + text.slice(1);
}

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
    const option = append(typeFilter, 'option', '', titleCase(type));
    option.value = type;
}
for (const node of graphData.nodes) {
    const row = append(memoryList, 'div');
    row.setAttribute('role', 'listitem');
    const button = append(row, 'button', 'memory-item');
    button.type = 'button';
    button.dataset.id = node.id;
    button.dataset.recent = String(node.recent);
    button.setAttribute('aria-pressed', 'false');
    button.style.setProperty('--memory-color', node.color);
    append(button, 'span', 'memory-dot').setAttribute('aria-hidden', 'true');
    append(button, 'span', 'memory-title', node.text || 'Untitled memory');
    append(button, 'span', 'memory-meta', [titleCase(node.type), node.source_agent].filter(Boolean).join(' · '));
    listRows.set(node.id, { row, button });
}
memoryList.addEventListener('click', event => {
    const row = event.target.closest('.memory-item');
    if (row) openMemory(byId.get(row.dataset.id));
});

function syncSidebarLayout() { setSidebar(!compact.matches && desktopSidebarOpen); }
syncSidebarLayout();
compact.addEventListener('change', syncSidebarLayout);
const graph = createMemoryGraph(canvas, graphData, {
    onOpen: openMemory,
    onCameraChange: zoom => { zoomValue.textContent = Math.round(zoom * 100) + '%'; },
    reducedMotion,
    physicsSource: JSON.parse(document.getElementById('graphPhysics').textContent),
});

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

function updateView(cameraAction = 'keep') {
    const query = searchInput.value.trim().toLowerCase();
    const type = typeFilter.value;
    const relationship = relationshipFilter.value;
    let nodes = graphData.nodes.filter(node => (
        (dateRange === 'all' || node.recent) && (!type || node.type === type) && (
            !query || [node.text, node.source_agent, node.session_id].some(value => String(value || '').toLowerCase().includes(query))
        )
    ));
    if (focusedId && !nodes.some(node => node.id === focusedId)) focusedId = null;
    if (focusedId) {
        const neighborhood = localNeighborhood(focusedId, nodes, relationship);
        nodes = nodes.filter(node => neighborhood.has(node.id));
    }
    visibleIds = new Set(nodes.map(node => node.id));
    for (const [id, item] of listRows) item.row.hidden = !visibleIds.has(id);
    const links = graphData.links.filter(link => visibleIds.has(link.source) && visibleIds.has(link.target));
    const similarities = links.filter(link => link.kind === 'similarity').length;
    const corrections = links.length - similarities;
    for (const entry of legendEntries) entry.hidden = relationship !== 'all' && entry.dataset.linkKind !== relationship;
    const counts = [numberLabel(nodes.length, 'memory', 'memories')];
    if (relationship !== 'supersedes') counts.push(numberLabel(similarities, 'similarity link'));
    if (relationship !== 'similarity') counts.push(numberLabel(corrections, 'correction'));
    graphStats.textContent = counts.join(' · ');
    document.getElementById('sidebarCount').textContent = String(nodes.length);
    document.getElementById('sidebarEmpty').hidden = Boolean(nodes.length);
    graphEmpty.hidden = Boolean(nodes.length);
    document.getElementById('emptyDetail').textContent = graphData.nodes.length ? 'Clear search or change the memory filters.' : 'Saved memories will appear here.';
    focusContext.hidden = !focusedId;
    localDepthField.hidden = !focusedId;
    focusAnchor.textContent = focusedId ? 'Focused on ' + (byId.get(focusedId).text || 'Untitled memory') : '';
    for (const button of memoryViewSwitch.querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.view === dateRange));
    }
    graph.setView(visibleIds, relationship);
    graph.setSelected(selected?.id || focusedId);
    if (cameraAction === 'fit') graph.fit();
    if (cameraAction === 'focus') graph.fit(!reducedMotion.matches);
    if (cameraAction === 'reveal') graph.reveal();
}

function previewField(parent, label, value) {
    if (value === null || value === undefined || value === '') return;
    append(parent, 'dt', '', label);
    append(parent, 'dd', '', String(value));
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
        const button = append(similarMemories, 'button', 'preview-link', item.node.text || 'Untitled memory');
        button.type = 'button';
        append(button, 'span', 'memory-meta', ' · ' + Math.round(item.weight * 100) + '% similar');
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
    if (source.userText) append(details, 'blockquote', '', source.userText);
    if (source.assistantText) append(details, 'blockquote', '', source.assistantText);
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

function renderEvidence(payload) {
    if (payload.truncated) append(previewEvidence, 'p', 'preview-note', 'Memory text shortened for display');
    if (payload.corrections.length || payload.correctionsPartial) {
        const history = evidenceSection('Correction history');
        const revisions = append(history, 'div', 'preview-links');
        for (const entry of payload.corrections) {
            const label = entry.direction === 'replaces' ? 'Replaces earlier memory' : 'Replaced by newer memory';
            const revision = append(revisions, 'div');
            if (entry.memory) {
                const button = append(revision, 'button', 'preview-link');
                button.type = 'button';
                append(button, 'span', 'revision-label', label);
                append(button, 'span', '', entry.memory.text || 'Untitled memory');
                button.addEventListener('click', () => openRevision(entry.memory));
            } else append(revision, 'p', 'preview-note', label + ' (unavailable): ' + entry.memoryId);
            if (entry.evidence) append(revision, 'blockquote', '', entry.evidence);
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

function renderMemory(node, text) {
    if (selected) listRows.get(selected.id)?.button.setAttribute('aria-pressed', 'false');
    selected = node;
    graph.setSelected(node.id);
    listRows.get(node.id)?.button.setAttribute('aria-pressed', 'true');
    previewTitle.textContent = titleCase(node.type || 'Memory');
    previewText.textContent = text || node.text || 'Untitled memory';
    previewMeta.replaceChildren();
    previewField(previewMeta, 'Agent', node.source_agent);
    previewField(previewMeta, 'Recorded', formatRecorded(node.ts));
    previewField(previewMeta, 'Stream', node.stream);
    previewIdentifiers.parentElement.open = false;
    previewIdentifiers.replaceChildren();
    append(previewIdentifiers, 'p', 'preview-source-id', node.raw_id);
    if (node.session_id) append(previewIdentifiers, 'p', 'preview-source-id', 'Session: ' + node.session_id);
    if (node.turn != null) append(previewIdentifiers, 'p', 'preview-source-id', 'Turn: ' + node.turn);
    previewActions.hidden = !visibleIds.has(node.id);
    previewScope.hidden = visibleIds.has(node.id);
    previewEvidence.replaceChildren();
    previewEvidence.hidden = true;
    previewConnections.hidden = true;
}

async function openMemory(node) {
    if (!node) return;
    const navigating = dialog.open;
    evidenceRequest?.abort();
    const controller = new AbortController();
    evidenceRequest = controller;
    if (!navigating) {
        previewMotion?.cancel();
        renderMemory(node);
        dialog.showModal();
        previewScroll.scrollTop = 0;
    }

    let payload;
    let unavailable = false;
    if (node.source_agent && node.raw_id) {
        focusMemory.disabled = navigating;
        previewLoadStatus.textContent = 'Loading details…';
        try {
            payload = await fetchEvidence(node, controller.signal);
        } catch {
            unavailable = true;
        }
    }
    if (controller.signal.aborted || !dialog.open) return;
    evidenceRequest = null;
    focusMemory.disabled = false;
    previewLoadStatus.textContent = '';
    if (navigating) renderMemory(node, payload?.text);
    if (payload) {
        if (!navigating && payload.text) previewText.textContent = payload.text;
        renderEvidence(payload);
    } else if (unavailable) {
        append(previewEvidence, 'p', 'preview-note', 'Source and correction details unavailable');
        previewEvidence.hidden = false;
    }
    renderSimilarity(node);
    if (!navigating) return;
    previewScroll.scrollTop = 0;
    previewText.focus({ preventScroll: true });
    previewMotion?.cancel();
    if (!reducedMotion.matches) {
        previewMotion = previewContent.animate?.([
            { opacity: .55, transform: 'translateY(4px)' },
            { opacity: 1, transform: 'translateY(0)' },
        ], { duration: 160, easing: 'cubic-bezier(.2, .7, .2, 1)' });
    }
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
document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && graphLayout.open && graphLayout.contains(document.activeElement)) {
        graphLayout.open = false;
        graphLayout.querySelector('summary').focus({ preventScroll: true });
        return;
    }
    if (event.key !== 'Escape' || !compact.matches || sidebar.hidden || dialog.open || event.defaultPrevented) return;
    event.preventDefault();
    dismissSidebar();
});
for (const button of memoryViewSwitch.querySelectorAll('button')) {
    button.addEventListener('click', () => { dateRange = button.dataset.view; updateView('reveal'); });
}
searchInput.addEventListener('input', () => updateView('reveal'));
typeFilter.addEventListener('change', () => updateView('reveal'));
relationshipFilter.addEventListener('change', () => updateView(focusedId ? 'fit' : 'reveal'));
function layoutValues(section) {
    return Object.fromEntries(Array.from(section.querySelectorAll('input'), input => [input.name, input.valueAsNumber]));
}
const graphForces = document.getElementById('graphForces');
const graphDisplay = document.getElementById('graphDisplay');
graphForces.addEventListener('input', () => graph.setForces(layoutValues(graphForces)));
graphDisplay.addEventListener('input', () => graph.setDisplay(layoutValues(graphDisplay)));
localDepth.addEventListener('input', () => updateView('focus'));
graphLayoutForm.addEventListener('reset', () => {
    queueMicrotask(() => {
        graph.setForces(layoutValues(graphForces));
        graph.setDisplay(layoutValues(graphDisplay));
        if (focusedId) updateView('focus');
    });
});
document.getElementById('reheatGraph').addEventListener('click', () => graph.reheat());
window.addEventListener('pagehide', event => { if (!event.persisted) graph.destroy(); });
clearFocus.addEventListener('click', () => {
    focusedId = null;
    updateView('focus');
    canvas.focus({ preventScroll: true });
});
document.getElementById('zoomIn').addEventListener('click', () => graph.zoomBy(1.25));
document.getElementById('zoomOut').addEventListener('click', () => graph.zoomBy(1 / 1.25));
document.getElementById('fitGraph').addEventListener('click', () => graph.fit(!reducedMotion.matches));
document.getElementById('memoryPreviewClose').addEventListener('click', () => dialog.close());
focusMemory.addEventListener('click', () => {
    if (focusMemory.disabled || !selected || !visibleIds.has(selected.id)) return;
    focusedId = selected.id;
    dialog.close();
    if (compact.matches) setSidebar(false);
    updateView('focus');
    canvas.focus({ preventScroll: true });
});
dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
});
dialog.addEventListener('close', () => {
    if (dialog.open) return;
    evidenceRequest?.abort();
    evidenceRequest = null;
    focusMemory.disabled = false;
    previewLoadStatus.textContent = '';
    if (selected) listRows.get(selected.id)?.button.setAttribute('aria-pressed', 'false');
    selected = null;
    graph.setSelected(focusedId);
});
updateView('fit');
