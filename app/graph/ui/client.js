/* ── Starfield ── */
(function(){
    const c = document.getElementById('starfield');
    const ctx = c.getContext('2d');
    let stars = [];
    function resize() { c.width = window.innerWidth; c.height = window.innerHeight; init(); }
    function init() {
        stars = [];
        for (let i = 0; i < 90; i++) {
            stars.push({
                x: Math.random() * c.width,
                y: Math.random() * c.height,
                r: Math.random() * 1.1 + 0.2,
                a: Math.random() * 0.35 + 0.08,
                dx: (Math.random() - 0.5) * 0.06,
                dy: (Math.random() - 0.5) * 0.04,
                flicker: Math.random() * Math.PI * 2
            });
        }
    }
    function draw() {
        ctx.clearRect(0, 0, c.width, c.height);
        const now = Date.now() * 0.001;
        for (const s of stars) {
            s.x += s.dx; s.y += s.dy;
            if (s.x < 0) s.x = c.width;
            if (s.x > c.width) s.x = 0;
            if (s.y < 0) s.y = c.height;
            if (s.y > c.height) s.y = 0;
            const flickerAlpha = s.a * (0.7 + 0.3 * Math.sin(now * 0.8 + s.flicker));
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(200, 180, 220, ' + flickerAlpha + ')';
            ctx.fill();
        }
        requestAnimationFrame(draw);
    }
    resize();
    window.addEventListener('resize', resize);
    draw();
})();

/* ── Stat count-up animation ── */
document.querySelectorAll('.stat-num[data-count]').forEach(el => {
    const target = parseInt(el.dataset.count, 10) || 0;
    let current = 0;
    const duration = 800;
    const start = performance.now();
    function tick(now) {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        current = Math.round(eased * target);
        el.textContent = current;
        if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
});

/* ── Sidebar search & filter ── */
const searchInput = document.getElementById('sidebarSearch');
const filterContainer = document.getElementById('sidebarFilters');
const memoryItems = Array.from(document.querySelectorAll('.memory-item'));
const sidebarSubtitle = document.getElementById('memorySidebarSubtitle');
const memoryViewSwitch = document.getElementById('memoryViewSwitch');
const memoryEmptyState = document.getElementById('memoryEmptyState');
const showAllMemoriesBtn = document.getElementById('showAllMemoriesBtn');
const memoryList = document.getElementById('memoryList');
const types = new Set();
memoryItems.forEach(item => { if (item.dataset.type) types.add(item.dataset.type); });
types.forEach(t => {
    const pill = document.createElement('span');
    pill.className = 'filter-pill';
    pill.dataset.filter = t;
    pill.textContent = t;
    filterContainer.appendChild(pill);
});
const sidebarCounts = { recent: ${recent_count}, all: ${total_count} };
let activeView = 'recent';
let activeFilter = 'all';
function updateSidebarSubtitle(visibleCount) {
    const total = sidebarCounts.all;
    if (activeView === 'recent') {
        sidebarSubtitle.textContent = '${scope_label} \u00b7 ' + visibleCount + ' of ' + sidebarCounts.recent + ' recent \u00b7 ' + total + ' total';
    } else {
        sidebarSubtitle.textContent = '${scope_label} \u00b7 ' + visibleCount + ' of ' + total + ' memories';
    }
}
function setActiveView(nextView) {
    activeView = nextView;
    memoryViewSwitch.querySelectorAll('.memory-view-pill').forEach(pill => {
        pill.classList.toggle('active', pill.dataset.view === nextView);
    });
    applyFilters();
}
memoryViewSwitch.addEventListener('click', e => {
    const pill = e.target.closest('.memory-view-pill');
    if (!pill) return;
    setActiveView(pill.dataset.view);
});
if (showAllMemoriesBtn) {
    showAllMemoriesBtn.addEventListener('click', () => setActiveView('all'));
}
filterContainer.addEventListener('click', e => {
    const pill = e.target.closest('.filter-pill');
    if (!pill) return;
    filterContainer.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    activeFilter = pill.dataset.filter;
    applyFilters();
});

let searchDebounce = null;
searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => applyFilters(), 120);
});

function applyFilters() {
    const q = searchInput.value.toLowerCase();
    let visibleCount = 0;
    memoryItems.forEach(item => {
        const matchView = activeView === 'all' || item.dataset.recent === 'true';
        const matchType = activeFilter === 'all' || item.dataset.type === activeFilter;
        const matchSearch = !q || (item.dataset.text || '').includes(q) || (item.dataset.session || '').includes(q);
        const visible = matchView && matchType && matchSearch;
        item.classList.toggle('hidden-filter', !visible);
        if (visible) visibleCount += 1;
    });
    if (memoryEmptyState) memoryEmptyState.classList.toggle('visible', visibleCount === 0 && activeView === 'recent');
    updateSidebarSubtitle(visibleCount);
}
applyFilters();

/* ── Force Graph ── */
const graphData = {
    nodes: ${nodes_json},
    links: ${links_json}
};

const nodeMap = {};
graphData.nodes.forEach(n => { nodeMap[n.id] = n; });

const Graph = ForceGraph3D()
    (document.getElementById('graph'))
    .backgroundColor('rgba(0,0,0,0)')
    .graphData(graphData)
    .nodeLabel('')
    .nodeVal(node => Math.max(node.val * 2.2, 4))
    .nodeColor(node => node.color)
    .nodeResolution(${node_resolution})
    .nodeOpacity(${node_opacity})
    .linkColor(link => link.color)
    .linkWidth(link => link.width)
    .linkOpacity(${link_opacity})
    .linkCurvature(0)
    .linkDirectionalParticles(link => link.kind === 'similarity' ? Math.ceil(link.weight * 3) : 0)
    .linkDirectionalParticleWidth(0.4)
    .linkDirectionalParticleSpeed(0.004)
    .linkDirectionalParticleColor(link => link.color)
    .enableNodeDrag(true)
    .enableNavigationControls(true)
    .controlType('orbit')
    .cameraPosition({ x: ${cam_x}, y: ${cam_y}, z: ${cam_z} })
    .fov(${cam_fov})
    .width(window.innerWidth)
    .height(window.innerHeight);

/* ── Scene lighting ── */
Graph.scene().add(new THREE.AmbientLight('${ambient_light_color}', ${ambient_light_intensity}));
const keyLight = new THREE.DirectionalLight('${key_light_color}', ${key_light_intensity});
keyLight.position.set(${key_light_x}, ${key_light_y}, ${key_light_z});
Graph.scene().add(keyLight);
const fillLight = new THREE.DirectionalLight('${fill_light_color}', ${fill_light_intensity});
fillLight.position.set(${fill_light_x}, ${fill_light_y}, ${fill_light_z});
Graph.scene().add(fillLight);

${auto_rotate_code}

/* ── Physics ── */
Graph.d3Force('charge').strength(${charge_strength});
Graph.d3Force('link').distance(${link_distance});
Graph.d3Force('link').strength(${link_strength});
if (Graph.d3Force('collision')) Graph.d3Force('collision').radius(${collision_radius});

/* ── Node Inspector interactions ── */
const nodeInfoEl = document.getElementById('nodeInfo');
const inspectorPanel = document.getElementById('nodeInspector');
const closeBtn = document.getElementById('inspectorClose');
const graphContainer = document.getElementById('graph');
const memoryPreview = document.getElementById('memoryPreview');
const memoryPreviewClose = document.getElementById('memoryPreviewClose');
const memoryPreviewKicker = document.getElementById('memoryPreviewKicker');
const memoryPreviewTitle = document.getElementById('memoryPreviewTitle');
const memoryPreviewBody = document.getElementById('memoryPreviewBody');
const memoryPreviewMeta = document.getElementById('memoryPreviewMeta');
const memoryPreviewConnections = document.getElementById('memoryPreviewConnections');
const memoryPreviewDim = document.getElementById('memoryPreviewDim');
const nodeHoverCard = document.getElementById('nodeHoverCard');
const nodeHoverKicker = document.getElementById('nodeHoverKicker');
const nodeHoverText = document.getElementById('nodeHoverText');
let selectedNode = null;
let hoveredNode = null;
let lastPointerEvent = null;
let pointerDown = null;
let lastOpenAt = 0;
let lastOpenNodeId = null;

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function getConnectedNodes(nodeId) {
    const connected = [];
    for (const link of graphData.links) {
        const sid = link.source.id || link.source;
        const tid = link.target.id || link.target;
        let neighborId = null;
        if (sid === nodeId) neighborId = tid;
        else if (tid === nodeId) neighborId = sid;
        if (neighborId && nodeMap[neighborId] && nodeMap[neighborId].kind === 'memory') {
            connected.push({ node: nodeMap[neighborId], weight: link.weight, kind: link.kind });
        }
    }
    connected.sort((a, b) => b.weight - a.weight);
    return connected;
}

function showHoverCard(node, event) {
    if (!nodeHoverCard || !node || !event) return;
    const label = node.kind === 'memory' ? (node.type || 'memory') : 'source';
    const text = node.kind === 'memory'
        ? (node.text || node.label || 'memory')
        : (node.title || node.label || 'source');
    nodeHoverKicker.textContent = label;
    nodeHoverText.textContent = text;
    const padding = 14;
    const cardWidth = Math.min(320, nodeHoverCard.offsetWidth || 220);
    const x = Math.min(window.innerWidth - cardWidth - padding, event.clientX + 16);
    const y = Math.min(window.innerHeight - 72, event.clientY + 16);
    nodeHoverCard.style.left = x + 'px';
    nodeHoverCard.style.top = y + 'px';
    nodeHoverCard.classList.add('visible');
    nodeHoverCard.setAttribute('aria-hidden', 'false');
}

function hideHoverCard() {
    if (!nodeHoverCard) return;
    nodeHoverCard.classList.remove('visible');
    nodeHoverCard.setAttribute('aria-hidden', 'true');
}

function maybeOpenNode(node, shouldFly = false) {
    if (!node) return null;
    const now = Date.now();
    if (lastOpenNodeId === node.id && now - lastOpenAt < 180) {
        return node;
    }
    lastOpenNodeId = node.id;
    lastOpenAt = now;
    return openMemoryCard(node, shouldFly);
}

function ensurePreviewVisible(node, connectionCount) {
    if (!memoryPreview || !memoryPreviewBody || !memoryPreviewMeta) return;
    try {
        renderPreview(node, connectionCount);
    } catch (err) {
        console.warn('memory preview render failed', err);
        memoryPreviewKicker.textContent = node.kind === 'memory' ? 'memory preview' : 'graph scope';
        memoryPreviewTitle.textContent = node.kind === 'memory' ? (node.type || 'memory') : (node.title || node.label || 'source overview');
        memoryPreviewBody.textContent = node.text || node.label || node.title || 'No text';
        memoryPreviewMeta.innerHTML = '<span>fallback preview</span>';
        memoryPreviewConnections.innerHTML = '';
        memoryPreviewConnections.style.display = 'none';
        memoryPreview.classList.add('visible');
        memoryPreviewDim.classList.add('visible');
        memoryPreview.setAttribute('aria-hidden', 'false');
    }
}

function renderPreview(node, connectionCount) {
    const meta = [];
    if (node.kind === 'memory') {
        memoryPreviewKicker.textContent = 'memory preview';
        memoryPreviewTitle.textContent = node.type || 'memory';
        memoryPreviewBody.textContent = node.text || node.label || 'No text';
        if (node.ts) meta.push('<span>' + escapeHtml(node.ts) + '</span>');
        if (node.session_id) meta.push('<span>session ' + escapeHtml(node.session_id) + '</span>');
        if (node.turn !== null && node.turn !== undefined) meta.push('<span>turn ' + escapeHtml(node.turn) + '</span>');
        meta.push('<span>' + connectionCount + ' connection' + (connectionCount !== 1 ? 's' : '') + '</span>');
    } else {
        memoryPreviewKicker.textContent = 'graph scope';
        memoryPreviewTitle.textContent = 'source overview';
        memoryPreviewBody.textContent = node.title || node.label || 'Source';
        if (node.session_id) meta.push('<span>session ' + escapeHtml(node.session_id) + '</span>');
        meta.push('<span>' + escapeHtml(node.deg) + ' memories</span>');
    }
    memoryPreviewMeta.innerHTML = meta.join('');

    const connected = getConnectedNodes(node.id);
    if (connected.length > 0) {
        let connHtml = '<div class="memory-preview-connections-title">connected memories</div><div class="memory-preview-conn-list">';
        const shown = connected.slice(0, 12);
        for (const c of shown) {
            const text = escapeHtml((c.node.text || c.node.label || '').substring(0, 120));
            const sim = Math.round(c.weight * 100);
            connHtml += '<div class="memory-preview-conn-item" data-node-id="' + escapeHtml(c.node.id) + '">'
                + '<div class="memory-preview-conn-item-text">' + text + '</div>'
                + '<div class="memory-preview-conn-item-meta">' + (c.node.type || 'memory') + ' \u00b7 ' + sim + '% similar</div>'
                + '</div>';
        }
        if (connected.length > 12) {
            connHtml += '<div style="font-size:10px;color:#605058;padding:4px 0;">+ ' + (connected.length - 12) + ' more</div>';
        }
        connHtml += '</div>';
        memoryPreviewConnections.innerHTML = connHtml;
        memoryPreviewConnections.style.display = 'block';
    } else {
        memoryPreviewConnections.innerHTML = '';
        memoryPreviewConnections.style.display = 'none';
    }

    memoryPreview.classList.add('visible');
    memoryPreviewDim.classList.add('visible');
    memoryPreview.setAttribute('aria-hidden', 'false');
    memoryPreview.scrollTop = 0;
}

function hidePreview() {
    memoryPreview.classList.remove('visible');
    memoryPreviewDim.classList.remove('visible');
    memoryPreview.setAttribute('aria-hidden', 'true');
}

function flyToNode(node) {
    const dist = 80;
    Graph.cameraPosition(
        { x: node.x + dist, y: node.y + dist * 0.4, z: node.z + dist },
        { x: node.x, y: node.y, z: node.z },
        1200
    );
}

function highlightSidebarItem(nodeId) {
    memoryItems.forEach(item => {
        item.style.borderColor = '';
        item.style.background = '';
    });
    if (!nodeId) return;
    const matchItem = memoryItems.find(item => item.dataset.id === nodeId);
    if (matchItem) {
        matchItem.style.borderColor = 'rgba(200, 120, 140, 0.35)';
        matchItem.style.background = 'rgba(28, 14, 20, 0.60)';
        const listRect = memoryList.getBoundingClientRect();
        const itemRect = matchItem.getBoundingClientRect();
        if (itemRect.top < listRect.top || itemRect.bottom > listRect.bottom) {
            const offset = matchItem.offsetTop - memoryList.clientHeight / 2 + matchItem.clientHeight / 2;
            memoryList.scrollTo({ top: Math.max(0, offset), behavior: 'smooth' });
        }
    }
}

function deselectNode() {
    selectedNode = null;
    nodeInfoEl.innerHTML = '';
    closeBtn.style.display = 'none';
    inspectorPanel.querySelector('.hint').style.display = 'flex';
    hidePreview();
    hideHoverCard();
    highlightSidebarItem(null);
    Graph.nodeColor(n => n.color);
    Graph.linkColor(l => l.color);
}

function openMemoryCard(node, shouldFly = false) {
    if (!node) return null;
    selectedNode = node;
    console.info('selected memory node', node.id);
    closeBtn.style.display = 'flex';
    inspectorPanel.querySelector('.hint').style.display = 'none';

    let details = '';
    let previewConnectionCount = 0;
    if (node.kind === 'memory') {
        const conn = graphData.links.filter(l =>
            (l.source.id || l.source) === node.id || (l.target.id || l.target) === node.id
        ).length;
        previewConnectionCount = conn;
        details = '<div class="detail-field"><span class="detail-label">memory</span><span class="detail-value">' + (node.label || 'No text') + '</span></div>';
        details += '<div class="detail-field"><span class="detail-label">group</span><span class="detail-value">' + node.group + '</span></div>';
        details += '<div class="conn-count">\u2B21 ' + conn + ' connection' + (conn !== 1 ? 's' : '') + '</div>';
    } else {
        previewConnectionCount = node.deg || 0;
        details = '<div class="detail-field"><span class="detail-label">source</span><span class="detail-value">' + (node.title || node.label) + '</span></div>';
        details += '<div class="conn-count">\u2B21 ' + node.deg + ' memories</div>';
    }
    nodeInfoEl.innerHTML = details;

    ensurePreviewVisible(node, previewConnectionCount);

    highlightSidebarItem(node.id);
    hideHoverCard();

    Graph.nodeColor(n => {
        if (n.id === selectedNode.id) return '${selected_node_color}';
        return n.color;
    });
    Graph.linkColor(l => {
        if (selectedNode) {
            const sid = l.source.id || l.source;
            const tid = l.target.id || l.target;
            if (sid === selectedNode.id || tid === selectedNode.id) return '${selected_link_color}';
        }
        return l.color;
    });

    if (shouldFly) {
        flyToNode(node);
    }
    ${pause_rotation}
    return node;
}

/* ── Sidebar memory item click → select node ── */
memoryList.addEventListener('click', e => {
    const item = e.target.closest('.memory-item');
    if (!item) return;
    const memId = item.dataset.id;
    const node = nodeMap[memId];
    if (node) openMemoryCard(node, true);
});

closeBtn.addEventListener('click', deselectNode);
memoryPreviewClose.addEventListener('click', deselectNode);
memoryPreviewDim.addEventListener('click', deselectNode);
memoryPreview.addEventListener('click', e => {
    const connItem = e.target.closest('.memory-preview-conn-item');
    if (!connItem) return;
    const nodeId = connItem.dataset.nodeId;
    const node = nodeMap[nodeId];
    if (node) maybeOpenNode(node, true);
});

graphContainer.addEventListener('pointerdown', event => {
    pointerDown = { x: event.clientX, y: event.clientY };
});
graphContainer.addEventListener('pointermove', event => {
    lastPointerEvent = event;
    if (hoveredNode && !selectedNode) {
        showHoverCard(hoveredNode, event);
    }
});
graphContainer.addEventListener('pointerup', event => {
    lastPointerEvent = event;
    if (!pointerDown) return;
    const moved = Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y);
    pointerDown = null;
    if (hoveredNode && moved <= 8) {
        maybeOpenNode(hoveredNode, true);
    }
});
graphContainer.addEventListener('click', () => {
    if (hoveredNode) {
        maybeOpenNode(hoveredNode, true);
    }
});
graphContainer.addEventListener('pointerleave', () => {
    pointerDown = null;
});

Graph.onNodeClick(node => {
    maybeOpenNode(node, true);
});

Graph.onNodeDragEnd(node => {
    maybeOpenNode(node, false);
});

Graph.onBackgroundClick(deselectNode);

Graph.onNodeHover(node => {
    hoveredNode = node || null;
    document.body.style.cursor = node ? 'pointer' : 'default';
    if (node && !selectedNode) {
        showHoverCard(node, lastPointerEvent);
        Graph.nodeColor(n => n.id === node.id ? '${hover_node_color}' : n.color);
        Graph.linkColor(l => {
            const sid = l.source.id || l.source;
            const tid = l.target.id || l.target;
            return (sid === node.id || tid === node.id) ? '${hover_link_color}' : l.color;
        });
    } else if (!selectedNode) {
        hideHoverCard();
        Graph.nodeColor(n => n.color);
        Graph.linkColor(l => l.color);
    }
});

window.addEventListener('resize', () => {
    Graph.width(window.innerWidth).height(window.innerHeight);
});

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') deselectNode();
});
