const graphemeSegmenter = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;

function splitGraphemes(text) {
  return graphemeSegmenter ? Array.from(graphemeSegmenter.segment(text), part => part.segment) : Array.from(text);
}

// Shortens text to at most `max` user-perceived characters, so an emoji or a
// combining sequence is never split.
function shortenText(text, max) {
  const value = String(text ?? '');
  if (value.length <= max) return value;
  const sample = value.slice(0, max * 8);
  const parts = splitGraphemes(sample);
  if (parts.length <= max && sample.length === value.length) return value;
  // A cut sample can end inside a grapheme, so never keep its last part.
  const keep = sample.length === value.length ? max - 1 : Math.min(max - 1, parts.length - 1);
  return parts.slice(0, keep).join('').trimEnd() + '…';
}

// One line of plain text for canvas labels: no control or bidi override
// characters, collapsed whitespace.
function labelSource(text) {
  return String(text || '')
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, ' ')
    .replace(/[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function createMemoryGraph(canvas, data, callbacks) {
  const options = callbacks || {};
  const context = canvas.getContext('2d');
  if (!context) {
    const noop = () => {};
    return {
      supported: false, setView: () => false, fit: noop, zoomBy: noop, setSelected: noop, setFocusRoot: noop,
      setForces: noop, setDisplay: noop, rebalance: noop, resetLayout: noop, ensureVisible: noop, clearCursor: () => false, destroy: noop,
    };
  }
  const nodes = data.nodes;
  const nodeMap = new Map(nodes.map(node => [node.id, node]));
  const positions = new Map();
  const links = data.links.filter(link => nodeMap.has(link.source) && nodeMap.has(link.target));
  const reducedMotion = options.reducedMotion;
  const smoothCamera = () => !reducedMotion?.matches;
  // Graphs this small label every node at the fitted zoom.
  const smallGraph = 12;
  // Below this alpha the worker's layout is treated as settled.
  const settledAlpha = 0.01;
  let worker = null, physicsTimer = null, layoutReceived = false;
  let revision = 0, gesture = 0, initialized = false;
  let autoFit = null, userMoved = false;
  if (options.physicsSource && typeof Worker !== 'undefined') {
    const url = URL.createObjectURL(new Blob([options.physicsSource], { type: 'text/javascript' }));
    try {
      worker = new Worker(url);
    } catch (error) {
      console.warn('Titan graph physics could not start', error);
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  const style = canvas.ownerDocument?.defaultView?.getComputedStyle(canvas);
  const color = (property, fallback) => style?.getPropertyValue(property).trim() || fallback;
  const ink = color('--ink', '#F5F1F0');
  const muted = color('--muted', '#C2BABB');
  const halo = color('--canvas', '#050508');
  const selection = color('--graph-selection', '#F06A8B');
  const fontFamily = color('--font-sans', 'system-ui, sans-serif');
  const similarityAlpha = Number(color('--edge-similarity-alpha', '0.5')) || 0.5;
  const edgePaint = {
    similarity: { color: color('--edge-similarity', '#B8B0B2'), rest: similarityAlpha, dim: similarityAlpha * .35, highlight: .95 },
    supersedes: { color: color('--edge-correction', '#E8B072'), rest: .9, dim: .35, highlight: 1 },
  };
  let visibleIds = new Set(nodes.map(node => node.id));
  let relationship = 'all';
  let visibleNodes = nodes.slice();
  let visibleLinks = links.slice();
  let ranked = visibleNodes;
  let reciprocal = new Set();
  let neighbors = new Map();
  let selected = null;
  let focusRoot = null;
  let hovered = null;
  let cursor = null;
  let drag = null;
  let secondTouch = null;
  const labelBoxes = [];
  const arrowHeads = [];
  const labelCache = new Map();
  let width = 0, height = 0, dpr = 1, frame = null, fitted = false, destroyed = false, portrait = false;
  let overviewZoom = 1;
  let nodeSize = 1, linkWidth = 1;
  const camera = { x: 0, y: 0, zoom: 1 };
  let cameraFit = null;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const groups = new Map();
  for (const node of nodes) {
    const key = node.group == null ? null : node.group;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  }
  const grouped = Array.from(groups.entries()).sort((a, b) => {
    if (a[0] === null) return 1;
    if (b[0] === null) return -1;
    return String(a[0]).localeCompare(String(b[0]), undefined, { numeric: true });
  });
  const columns = Math.max(1, Math.ceil(Math.sqrt(grouped.length * 1.5)));
  const rows = Math.ceil(grouped.length / columns);
  const largest = Math.max(1, ...grouped.map(entry => entry[1].length));
  const spacing = 70 + 36 * Math.sqrt(largest);
  grouped.forEach(([, members], groupIndex) => {
    const row = Math.floor(groupIndex / columns);
    const itemsInRow = Math.min(columns, grouped.length - row * columns);
    const column = groupIndex % columns;
    const center = {
      x: (column - (itemsInRow - 1) / 2) * spacing * 1.2 + (row % 2 ? spacing * 0.12 : -spacing * 0.12),
      y: (row - (rows - 1) / 2) * spacing * 0.8
    };
    members.forEach((node, index) => {
      const angle = index * 2.399963229728653;
      const radius = 19 * Math.sqrt(index);
      positions.set(node.id, { x: center.x + Math.cos(angle) * radius * 1.15, y: center.y + Math.sin(angle) * radius * 0.85 });
    });
  });
  // Reset lays the graph out again from these starting points.
  const initialPositions = new Map(Array.from(positions, ([id, p]) => [id, { ...p }]));
  function degree(id) {
    return neighbors.get(id)?.size || 0;
  }
  function rebuildView(updatePhysics = true) {
    stopFit();
    labelBoxes.length = 0;
    visibleNodes = nodes.filter(node => visibleIds.has(node.id));
    visibleLinks = links.filter(link => visibleIds.has(link.source) && visibleIds.has(link.target) && (relationship === 'all' || link.kind === relationship));
    neighbors = new Map();
    for (const link of visibleLinks) {
      if (!neighbors.has(link.source)) neighbors.set(link.source, new Set());
      if (!neighbors.has(link.target)) neighbors.set(link.target, new Set());
      neighbors.get(link.source).add(link.target);
      neighbors.get(link.target).add(link.source);
    }
    const corrections = new Set(visibleLinks.filter(link => link.kind === 'supersedes').map(link => link.source + '\n' + link.target));
    reciprocal = new Set(visibleLinks.filter(link => link.kind === 'supersedes' && corrections.has(link.target + '\n' + link.source)));
    // Labels go to the best connected, then the most recent, memories first.
    ranked = visibleNodes.slice().sort((a, b) => degree(b.id) - degree(a.id) ||
      Number(Boolean(b.recent)) - Number(Boolean(a.recent)) || String(b.ts || '').localeCompare(String(a.ts || '')));
    if (drag?.id && !visibleIds.has(drag.id)) {
      releaseNode(drag);
      drag.id = null;
      drag.moved = true;
    }
    if (updatePhysics && worker) {
      worker.postMessage({ type: 'setData', revision: ++revision, graph: layoutGraph() });
      if (!layoutReceived && physicsTimer === null) physicsTimer = setTimeout(() => physicsFailed('no layout reply'), 4000);
    }
    if (hovered && !visibleIds.has(hovered)) updateHover(null);
    if (cursor && !visibleIds.has(cursor)) cursor = null;
    schedule();
  }
  function layoutGraph() {
    return {
      nodes: visibleNodes.map(({ id }) => ({ id, ...positions.get(id) })),
      links: visibleLinks.map(({ source, target }) => ({ source, target })),
    };
  }
  function schedule() {
    if (!destroyed && frame === null) frame = requestAnimationFrame(draw);
  }
  function physicsFailed(reason) {
    if (!worker) return;
    console.warn('Titan graph physics stopped', reason);
    worker.terminate();
    worker = null;
    clearTimeout(physicsTimer);
    physicsTimer = null;
    if (autoFit) {
      autoFit = null;
      fit(smoothCamera());
    }
    if (options.onPhysicsUnavailable) options.onPhysicsUnavailable();
  }
  function receiveLayout({ data: message }) {
    if (!layoutReceived) {
      layoutReceived = true;
      clearTimeout(physicsTimer);
      physicsTimer = null;
    }
    if (message.revision !== revision || message.gesture !== gesture) return;
    for (const { id, x, y } of message.nodes) {
      if (visibleIds.has(id) && drag?.id !== id) positions.set(id, { x, y });
    }
    if (autoFit && autoFit.revision === revision) followLayout(message.alpha);
    schedule();
  }
  // Fit once the new positions have mostly settled, and once more when they
  // finish if the layout drifted away from that view.
  function followLayout(alpha) {
    const settled = !(alpha > settledAlpha);
    if (!autoFit.fitted) {
      if (!autoFit.immediate && !settled && alpha > 0.08 && performance.now() - autoFit.started < 700) return;
      fit(!autoFit.immediate && smoothCamera());
      autoFit.fitted = true;
    } else if (settled && (!allInView() || driftedFromFit())) {
      fit(smoothCamera());
    }
    if (settled) autoFit = null;
  }
  function requestFit() {
    if (!worker) {
      fit(fitted && smoothCamera());
      return;
    }
    autoFit = { revision, started: performance.now(), immediate: !layoutReceived, fitted: false };
  }
  function userInteracted() {
    autoFit = null;
    userMoved = true;
    stopFit();
  }
  function motionPreferenceChanged() {
    if (reducedMotion?.matches) stopFit();
    worker?.postMessage({ type: 'motion', reduced: reducedMotion?.matches || false });
  }
  // Radius in screen pixels. Size is relative to the fitted zoom, so dots read
  // the same at the overview whatever the graph's extent.
  function screenRadius(node) {
    const base = clamp(3.5 + Math.sqrt(Math.max(0, degree(node.id))) * 1.6, visibleNodes.length <= 3 ? 6 : 3.5, 10);
    return nodeSize * base * clamp(Math.sqrt(camera.zoom / overviewZoom), 0.7, 2.2);
  }
  function radius(node) {
    return screenRadius(node) / camera.zoom;
  }
  function screenOffset(x, y, zoom) {
    return portrait ? { x: y / zoom, y: -x / zoom } : { x: x / zoom, y: y / zoom };
  }
  function screenToWorld(point) {
    const offset = screenOffset(point.x - width / 2, point.y - height / 2, camera.zoom);
    return { x: offset.x + camera.x, y: offset.y + camera.y };
  }
  function worldToScreen(point) {
    const x = (point.x - camera.x) * camera.zoom;
    const y = (point.y - camera.y) * camera.zoom;
    return { x: width / 2 + (portrait ? -y : x), y: height / 2 + (portrait ? x : y) };
  }
  // The part of the canvas not covered by the toolbar, footer or details drawer.
  function viewBox() {
    const inset = { top: 24, right: 24, bottom: 24, left: 24, ...(options.getInsets ? options.getInsets() : {}) };
    let { top, right, bottom, left } = inset;
    if (width - left - right < width * 0.4) {
      const scale = width * 0.6 / Math.max(1, left + right);
      left *= scale; right *= scale;
    }
    if (height - top - bottom < height * 0.4) {
      const scale = height * 0.6 / Math.max(1, top + bottom);
      top *= scale; bottom *= scale;
    }
    return { left, top, right: width - right, bottom: height - bottom };
  }
  function allInView() {
    const box = viewBox();
    return visibleNodes.every(node => {
      const point = worldToScreen(positions.get(node.id));
      return point.x >= box.left && point.x <= box.right && point.y >= box.top && point.y <= box.bottom;
    });
  }
  function draw(time) {
    frame = null;
    labelBoxes.length = 0;
    arrowHeads.length = 0;
    if (!width || !height) return;
    if (cameraFit) {
      cameraFit.startTime ??= time;
      const progress = Math.min(1, (time - cameraFit.startTime) / 260);
      const eased = 1 - (1 - progress) ** 3;
      const { start, target } = cameraFit;
      camera.x = start.x + (target.x - start.x) * eased;
      camera.y = start.y + (target.y - start.y) * eased;
      camera.zoom = start.zoom + (target.zoom - start.zoom) * eased;
      overviewZoom = start.overview + (target.overview - start.overview) * eased;
      if (progress < 1) schedule();
      else stopFit();
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    context.save();
    context.translate(width / 2, height / 2);
    context.scale(camera.zoom, camera.zoom);
    if (portrait) context.rotate(Math.PI / 2);
    context.translate(-camera.x, -camera.y);
    const active = hovered || cursor || (selected && visibleIds.has(selected) ? selected : null);
    const adjacent = active ? neighbors.get(active) : null;
    const margin = 35 / camera.zoom;
    const halfWidth = (portrait ? height : width) / (2 * camera.zoom);
    const halfHeight = (portrait ? width : height) / (2 * camera.zoom);
    const left = camera.x - halfWidth - margin;
    const right = camera.x + halfWidth + margin;
    const top = camera.y - halfHeight - margin;
    const bottom = camera.y + halfHeight + margin;
    const offscreen = (a, b) => Math.max(a.x, b.x) < left || Math.min(a.x, b.x) > right || Math.max(a.y, b.y) < top || Math.min(a.y, b.y) > bottom;
    const touches = link => Boolean(active) && (link.source === active || link.target === active);
    const similarity = edgePaint.similarity;
    context.strokeStyle = similarity.color;
    for (const highlighted of [false, true]) {
      context.beginPath();
      context.lineWidth = linkWidth * (highlighted ? 1.5 : 1) / camera.zoom;
      context.globalAlpha = highlighted ? similarity.highlight : active ? similarity.dim : similarity.rest;
      for (const link of visibleLinks) {
        if (link.kind !== 'similarity' || touches(link) !== highlighted) continue;
        const a = positions.get(link.source), b = positions.get(link.target);
        if (offscreen(a, b)) continue;
        context.moveTo(a.x, a.y); context.lineTo(b.x, b.y);
      }
      context.stroke();
    }
    const correction = edgePaint.supersedes;
    context.strokeStyle = correction.color;
    context.fillStyle = correction.color;
    for (const link of visibleLinks) {
      if (link.kind !== 'supersedes') continue;
      const a = positions.get(link.source), b = positions.get(link.target);
      if (offscreen(a, b)) continue;
      const highlighted = touches(link);
      context.globalAlpha = highlighted ? correction.highlight : active ? correction.dim : correction.rest;
      context.lineWidth = linkWidth * (highlighted ? 2 : 1.4) / camera.zoom;
      drawCorrection(a, b, reciprocal.has(link), radius(nodeMap.get(link.target)));
    }
    for (const node of visibleNodes) {
      const p = positions.get(node.id);
      if (p.x < left || p.x > right || p.y < top || p.y > bottom) continue;
      const isActive = node.id === active;
      const related = !active || isActive || (adjacent && adjacent.has(node.id));
      const r = radius(node);
      context.globalAlpha = related ? 1 : 0.28;
      context.fillStyle = node.color || muted;
      context.beginPath(); context.arc(p.x, p.y, r, 0, Math.PI * 2); context.fill();
      const ring = node.id === selected || node.id === focusRoot ? selection : isActive || node.id === cursor ? ink : null;
      if (ring) {
        context.globalAlpha = 1;
        context.strokeStyle = ring; context.lineWidth = 1.6 / camera.zoom;
        context.beginPath(); context.arc(p.x, p.y, r + 3 / camera.zoom, 0, Math.PI * 2); context.stroke();
      }
    }
    context.restore();
    context.globalAlpha = 1;
    drawLabels(active, adjacent);
  }
  // A correction points from the replacement to the memory it replaced.
  // Reciprocal corrections bow to opposite sides so both stay visible.
  function drawCorrection(a, b, bend, targetRadius) {
    const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
    if (length < 1e-6) return;
    const zoom = camera.zoom;
    const offset = bend ? Math.min(length * 0.25, 18 / zoom) : 0;
    const control = { x: (a.x + b.x) / 2 - dy / length * offset, y: (a.y + b.y) / 2 + dx / length * offset };
    const tangent = Math.hypot(b.x - control.x, b.y - control.y) || 1;
    const ux = (b.x - control.x) / tangent, uy = (b.y - control.y) / tangent;
    const headLength = 7 / zoom, headWidth = 3.2 / zoom;
    const gap = targetRadius + 2 / zoom;
    const showHead = length > gap + headLength * 1.5;
    const tip = { x: b.x - ux * gap, y: b.y - uy * gap };
    const end = showHead ? { x: tip.x - ux * headLength, y: tip.y - uy * headLength } : b;
    context.beginPath();
    context.moveTo(a.x, a.y);
    if (offset) context.quadraticCurveTo(control.x, control.y, end.x, end.y);
    else context.lineTo(end.x, end.y);
    context.stroke();
    if (!showHead) return;
    context.beginPath();
    context.moveTo(tip.x, tip.y);
    context.lineTo(end.x - uy * headWidth, end.y + ux * headWidth);
    context.lineTo(end.x + uy * headWidth, end.y - ux * headWidth);
    context.closePath();
    context.fill();
    arrowHeads.push({ x: (tip.x + end.x) / 2, y: (tip.y + end.y) / 2 });
  }
  function labelBudget() {
    const ratio = camera.zoom / overviewZoom;
    return Math.round(clamp(width * height / 60000 * ratio * ratio, 3, 36));
  }
  function drawLabels(active, adjacent) {
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.textAlign = 'center';
    context.textBaseline = 'top';
    context.lineJoin = 'round';
    const box = viewBox();
    const everyNode = visibleNodes.length <= smallGraph;
    const obstacles = labelObstacles();
    const pinned = [];
    for (const id of [active, cursor, selected, focusRoot]) {
      if (id && visibleIds.has(id) && !pinned.includes(id)) pinned.push(id);
    }
    for (const id of pinned) placeLabel(nodeMap.get(id), true, 260, box, obstacles);
    if (cameraFit && !everyNode) return;
    const pool = active && !everyNode ? ranked.filter(node => adjacent?.has(node.id)) : ranked;
    let budget = everyNode ? visibleNodes.length : labelBudget();
    for (const node of pool) {
      if (budget <= 0) break;
      if (!pinned.includes(node.id) && placeLabel(node, false, everyNode ? 260 : 180, box, obstacles, everyNode)) budget -= 1;
    }
  }
  // Dots and correction arrowheads, in screen pixels, that labels avoid.
  function labelObstacles() {
    const boxes = [];
    for (const node of visibleNodes) {
      const point = worldToScreen(positions.get(node.id));
      const r = screenRadius(node) + 2;
      if (point.x < -r || point.x > width + r || point.y < -r || point.y > height + r) continue;
      boxes.push({ id: node.id, left: point.x - r, right: point.x + r, top: point.y - r, bottom: point.y + r });
    }
    for (const head of arrowHeads) {
      const point = worldToScreen(head);
      boxes.push({ id: null, left: point.x - 6, right: point.x + 6, top: point.y - 6, bottom: point.y + 6 });
    }
    return boxes;
  }
  // Total area of `others` that a box covers, ignoring the label's own dot.
  function overlap(box, others, ownId = null) {
    let area = 0;
    for (const other of others) {
      if (ownId !== null && other.id === ownId) continue;
      const x = Math.min(box.right, other.right) - Math.max(box.left, other.left);
      const y = Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top);
      if (x > 0 && y > 0) area += x * y;
    }
    return area;
  }
  function labelText(node, maxWidth) {
    const key = node.id + '\n' + context.font + '\n' + maxWidth;
    if (labelCache.has(key)) return labelCache.get(key);
    const text = labelSource(node.text) || 'Untitled memory';
    let label = text;
    if (context.measureText(text).width > maxWidth) {
      const sample = text.slice(0, 160);
      const parts = splitGraphemes(sample);
      let low = 0, high = sample.length === text.length ? parts.length : parts.length - 1;
      while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (context.measureText(parts.slice(0, middle).join('').trimEnd() + '…').width <= maxWidth) low = middle;
        else high = middle - 1;
      }
      label = parts.slice(0, low).join('').trimEnd() + '…';
    }
    labelCache.set(key, label);
    return label;
  }
  // Draws a label beside a node (below, above, right or left, whichever is
  // clear), inside the uncovered part of the canvas. Pinned labels (hovered,
  // selected, focused) and, in small graphs, every label always show, in the
  // least crowded place; others skip nodes with no clear place.
  function placeLabel(node, pinned, maxWidth, area, obstacles, always = false) {
    const point = worldToScreen(positions.get(node.id));
    if (!pinned && (point.x < area.left || point.x > area.right || point.y < area.top || point.y > area.bottom)) return false;
    // Truncate in the regular font so an emphasized label keeps the same text.
    context.font = '11px ' + fontFamily;
    const text = labelText(node, maxWidth);
    const size = pinned ? 12 : 11;
    context.font = (pinned ? '500 ' : '') + size + 'px ' + fontFamily;
    const half = context.measureText(text).width / 2 + 3;
    const r = screenRadius(node);
    const places = [
      { x: point.x, y: point.y + r + 4 },
      { x: point.x, y: point.y - r - size - 5 },
      { x: point.x + r + 6 + half, y: point.y - size / 2 },
      { x: point.x - r - 6 - half, y: point.y - size / 2 },
    ];
    let best = null, bestOverlap = Infinity;
    for (const place of places) {
      const x = clamp(place.x, Math.min(4 + half, width / 2), Math.max(area.right - half, width / 2));
      const y = pinned ? clamp(place.y, 4, height - size - 6) : place.y;
      const box = { id: node.id, pinned, x, y, left: x - half, right: x + half, top: y - 2, bottom: y + size + 3 };
      if (!pinned && (box.top < 0 || box.bottom > height)) continue;
      // Covering another label costs more than covering a dot.
      const crowding = overlap(box, labelBoxes) * 3 + overlap(box, obstacles, node.id);
      if (crowding < bestOverlap) { best = box; bestOverlap = crowding; }
      if (!crowding) break;
    }
    if (!best || (bestOverlap > 0 && !pinned && !always)) return false;
    context.lineWidth = 4;
    context.strokeStyle = halo;
    context.globalAlpha = 0.9;
    context.strokeText(text, best.x, best.y);
    context.globalAlpha = 1;
    context.fillStyle = pinned ? ink : muted;
    context.fillText(text, best.x, best.y);
    labelBoxes.push(best);
    return true;
  }
  function reportCamera() { if (options.onCameraChange) options.onCameraChange(camera.zoom / overviewZoom); }
  function stopFit() {
    if (!cameraFit) return;
    overviewZoom = cameraFit.target.overview;
    cameraFit = null;
    reportCamera();
  }
  function resize() {
    labelBoxes.length = 0;
    const rect = canvas.getBoundingClientRect();
    width = rect.width; height = rect.height;
    const nextPortrait = width <= 540 && height > width * 1.25;
    const orientationChanged = portrait !== nextPortrait;
    portrait = nextPortrait;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    if ((!fitted || orientationChanged || !userMoved) && width > 1 && height > 1 && visibleNodes.length) fit();
    else schedule();
  }
  function moveCamera(target, smooth) {
    if (smooth && fitted && !drag) {
      cameraFit = { start: { ...camera, overview: overviewZoom }, target, startTime: null };
      updateHover(null);
    } else {
      cameraFit = null;
      camera.x = target.x; camera.y = target.y; camera.zoom = target.zoom;
      overviewZoom = target.overview;
      reportCamera();
    }
    schedule();
  }
  // The camera that frames every visible node in the uncovered area. A single
  // node, or nodes on one line, get the small-graph zoom.
  function fitTarget() {
    if (!visibleNodes.length || width <= 1 || height <= 1) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of visibleNodes) {
      const p = positions.get(node.id);
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    const box = viewBox();
    // Leave room for the dots and the labels under them.
    const margin = 20;
    const spanX = portrait ? maxY - minY : maxX - minX;
    const spanY = portrait ? maxX - minX : maxY - minY;
    const zoom = clamp(Math.min(
      (box.right - box.left - 2 * margin) / Math.max(80, spanX),
      (box.bottom - box.top - 2 * margin) / Math.max(80, spanY),
    ), 0.01, visibleNodes.length <= smallGraph ? 1.6 : 2.5);
    const offset = screenOffset((box.left + box.right - width) / 2, (box.top + box.bottom - height) / 2, zoom);
    return { x: (minX + maxX) / 2 - offset.x, y: (minY + maxY) / 2 - offset.y, zoom, overview: zoom };
  }
  function fit(smooth = false) {
    const target = fitTarget();
    if (!target) return;
    labelBoxes.length = 0;
    moveCamera(target, smooth);
    userMoved = false;
    fitted = true;
  }
  // Whether the fitted view is now off-center by more than a few percent of
  // the view, or needs a noticeably different zoom.
  function driftedFromFit() {
    const target = fitTarget();
    if (!target) return false;
    const current = cameraFit ? cameraFit.target : camera;
    const box = viewBox();
    const shift = Math.hypot(target.x - current.x, target.y - current.y) * target.zoom;
    return shift > 0.05 * Math.min(box.right - box.left, box.bottom - box.top) || Math.abs(target.zoom / current.zoom - 1) > 0.15;
  }
  // Pans, keeping the zoom, so a node is clear of the toolbar and drawer.
  function ensureVisible(id) {
    const p = positions.get(id);
    if (!p || !visibleIds.has(id) || !fitted) return;
    const box = viewBox();
    const point = worldToScreen(p);
    // Leave room for the label under the node.
    const margin = 40;
    if (point.x >= box.left + margin && point.x <= box.right - margin && point.y >= box.top + margin && point.y <= box.bottom - margin - 16) return;
    const offset = screenOffset((box.left + box.right - width) / 2, (box.top + box.bottom - height) / 2, camera.zoom);
    moveCamera({ x: p.x - offset.x, y: p.y - offset.y, zoom: camera.zoom, overview: overviewZoom }, smoothCamera());
  }
  // Zoom stays within the same range around the fitted view, so the readout
  // always runs from 25% to 800%.
  function zoomAt(factor, point, previousPoint = point) {
    userInteracted();
    const anchor = point || { x: width / 2, y: height / 2 };
    const world = screenToWorld(previousPoint || anchor);
    camera.zoom = clamp(camera.zoom * factor, overviewZoom * 0.25, overviewZoom * 8);
    const offset = screenOffset(anchor.x - width / 2, anchor.y - height / 2, camera.zoom);
    camera.x = world.x - offset.x;
    camera.y = world.y - offset.y;
    labelBoxes.length = 0;
    reportCamera(); schedule();
  }
  function pan(x, y) {
    userInteracted();
    const offset = screenOffset(x, y, camera.zoom);
    camera.x += offset.x; camera.y += offset.y;
    labelBoxes.length = 0;
    reportCamera(); schedule();
  }
  function localPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  // Picks the dot under the pointer, or the nearest one within a 24px target
  // (44px for touch), then falls back to label boxes.
  function hitTest(point, pointerType) {
    const reach = pointerType === 'touch' || pointerType === 'pen' ? 22 : 12;
    let best = null, bestScore = Infinity;
    for (const node of visibleNodes) {
      const screen = worldToScreen(positions.get(node.id));
      const distance = Math.hypot(point.x - screen.x, point.y - screen.y);
      const dot = screenRadius(node);
      if (distance > Math.max(dot, reach)) continue;
      const score = distance <= dot ? distance - 1000 : distance;
      if (score < bestScore) {
        best = node.id;
        bestScore = score;
      }
    }
    if (best) return best;
    let label = null;
    for (let i = labelBoxes.length - 1; i >= 0; i--) {
      const box = labelBoxes[i];
      if (point.x < box.left || point.x > box.right || point.y < box.top || point.y > box.bottom) continue;
      if (box.pinned) return box.id;
      if (!label) label = box.id;
    }
    return label;
  }
  function updateHover(id) {
    if (hovered === id) return;
    hovered = id;
    canvas.style.cursor = drag && drag.moved ? 'grabbing' : id ? 'pointer' : 'grab';
    if (options.onHover) options.onHover(id ? nodeMap.get(id) : null);
    schedule();
  }
  function setCursor(id) {
    cursor = id;
    schedule();
    if (!id) return;
    ensureVisible(id);
    if (options.onCursor) options.onCursor(nodeMap.get(id), degree(id));
  }
  // Moves the keyboard cursor to the nearest node in a screen direction.
  function moveCursor([dx, dy]) {
    if (!visibleNodes.length) return;
    const from = [cursor, selected].find(id => id && visibleIds.has(id));
    let next = null, bestScore = Infinity;
    if (!from) {
      const box = viewBox();
      const center = { x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2 };
      for (const node of visibleNodes) {
        const point = worldToScreen(positions.get(node.id));
        const distance = Math.hypot(point.x - center.x, point.y - center.y);
        if (distance < bestScore) { bestScore = distance; next = node.id; }
      }
    } else {
      const origin = worldToScreen(positions.get(from));
      for (const node of visibleNodes) {
        if (node.id === from) continue;
        const point = worldToScreen(positions.get(node.id));
        const along = (point.x - origin.x) * dx + (point.y - origin.y) * dy;
        if (along <= 1) continue;
        const across = Math.abs((point.x - origin.x) * dy - (point.y - origin.y) * dx);
        const score = along + across * 2.5;
        if (score < bestScore) { bestScore = score; next = node.id; }
      }
    }
    if (next) setCursor(next);
  }
  function pointerDown(event) {
    if (event.button !== 0) return;
    stopFit();
    if (drag) {
      if (drag.pointerType === 'touch' && event.pointerType === 'touch' && !secondTouch && event.pointerId !== drag.pointerId) {
        releaseNode(drag);
        drag.id = null;
        secondTouch = { pointerId: event.pointerId, last: localPoint(event) };
        drag.moved = true;
        canvas.setPointerCapture(event.pointerId);
      }
      return;
    }
    if (event.pointerType === 'touch') updateHover(null);
    const point = localPoint(event), id = hitTest(point, event.pointerType), world = screenToWorld(point);
    const p = id ? positions.get(id) : null;
    drag = { pointerId: event.pointerId, pointerType: event.pointerType, start: point, last: point, id, moved: false,
      offsetX: p ? world.x - p.x : 0, offsetY: p ? world.y - p.y : 0 };
    canvas.setPointerCapture(event.pointerId);
  }
  function moveNode(gestureState, point) {
    const world = screenToWorld(point);
    const position = positions.get(gestureState.id);
    position.x = world.x - gestureState.offsetX;
    position.y = world.y - gestureState.offsetY;
    worker?.postMessage({ type: 'pin', gesture, id: gestureState.id, x: position.x, y: position.y });
  }
  function releaseNode(gestureState) {
    if (!gestureState.id || !gestureState.moved || !worker) return;
    worker.postMessage({ type: 'pin', gesture: ++gesture, id: gestureState.id, x: null, y: null });
  }
  function pinchGeometry(a, b) {
    return { midpoint: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)) };
  }
  function pointerMove(event) {
    const point = localPoint(event);
    if (!drag) { if (event.pointerType !== 'touch') updateHover(hitTest(point, event.pointerType)); return; }
    if (secondTouch) {
      const finger = event.pointerId === drag.pointerId ? drag : event.pointerId === secondTouch.pointerId ? secondTouch : null;
      if (!finger) return;
      const before = pinchGeometry(drag.last, secondTouch.last);
      finger.last = point;
      const after = pinchGeometry(drag.last, secondTouch.last);
      zoomAt(after.distance / before.distance, after.midpoint, before.midpoint);
      return;
    }
    if (drag.pointerId !== event.pointerId) return;
    if (!drag.moved && Math.hypot(point.x - drag.start.x, point.y - drag.start.y) > 5) {
      drag.moved = true;
      userInteracted();
    }
    if (drag.moved) {
      labelBoxes.length = 0;
      if (drag.id) {
        moveNode(drag, point);
      } else {
        const offset = screenOffset(point.x - drag.last.x, point.y - drag.last.y, camera.zoom);
        camera.x -= offset.x;
        camera.y -= offset.y;
        reportCamera();
      }
      canvas.style.cursor = 'grabbing'; schedule();
    }
    drag.last = point;
  }
  function pointerEnd(event) {
    if (!drag) return;
    if (secondTouch && (event.pointerId === drag.pointerId || event.pointerId === secondTouch.pointerId)) {
      if (event.pointerId === drag.pointerId) {
        drag.pointerId = secondTouch.pointerId;
        drag.last = secondTouch.last;
      }
      secondTouch = null;
      drag.id = null;
      return;
    }
    if (drag.pointerId !== event.pointerId) return;
    const ended = drag; drag = null;
    releaseNode(ended);
    if (event.type === 'pointerup' && !ended.moved && ended.id && hitTest(localPoint(event), ended.pointerType) === ended.id && options.onOpen) {
      options.onOpen(nodeMap.get(ended.id), 'pointer');
    }
    updateHover(event.type === 'pointerup' && ended.pointerType !== 'touch' ? hitTest(localPoint(event), ended.pointerType) : null);
    canvas.style.cursor = hovered ? 'pointer' : 'grab';
  }
  function wheel(event) {
    event.preventDefault();
    zoomAt(Math.exp(-event.deltaY * 0.0015), localPoint(event));
  }
  // Arrow keys move between nodes (Shift+arrow pans), Enter opens the node
  // under the cursor, and plus or minus zoom.
  function keydown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const direction = directions[event.key];
    if (direction) {
      event.preventDefault();
      if (event.shiftKey) pan(direction[0] * 80, direction[1] * 80);
      else moveCursor(direction);
    }
    else if ((event.key === 'Enter' || event.key === ' ') && cursor) {
      event.preventDefault();
      if (options.onOpen) options.onOpen(nodeMap.get(cursor), 'keyboard');
    }
    else if (event.key === '+' || event.key === '=' || event.code === 'NumpadAdd') { event.preventDefault(); zoomAt(1.5); }
    else if (event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract') { event.preventDefault(); zoomAt(1 / 1.5); }
  }
  function pointerLeave() { if (!drag) updateHover(null); }
  function fontsReady() {
    labelCache.clear();
    schedule();
  }
  canvas.addEventListener('pointerdown', pointerDown);
  canvas.addEventListener('pointermove', pointerMove);
  canvas.addEventListener('pointerup', pointerEnd);
  canvas.addEventListener('pointercancel', pointerEnd);
  canvas.addEventListener('lostpointercapture', pointerEnd);
  canvas.addEventListener('pointerleave', pointerLeave);
  canvas.addEventListener('wheel', wheel, { passive: false });
  canvas.addEventListener('keydown', keydown);
  const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
  if (observer) observer.observe(canvas);
  window.addEventListener('resize', resize);
  reducedMotion?.addEventListener('change', motionPreferenceChanged);
  canvas.style.cursor = 'grab';
  if (worker) {
    worker.onmessage = receiveLayout;
    worker.onerror = event => {
      event.preventDefault();
      physicsFailed(event.message || 'worker error');
    };
    worker.onmessageerror = () => physicsFailed('unreadable worker message');
    motionPreferenceChanged();
  } else if (options.onPhysicsUnavailable) {
    queueMicrotask(options.onPhysicsUnavailable);
  }
  rebuildView(false); resize();
  canvas.ownerDocument?.fonts?.ready.then(fontsReady);
  return {
    supported: true,
    // Shows a set of nodes. With `fit`, the camera fits them once their layout
    // has settled. Returns whether the view changed.
    setView(ids, kind, { fit: fitView = false } = {}) {
      const next = new Set(ids);
      const changed = !initialized || kind !== relationship || next.size !== visibleIds.size || [...next].some(id => !visibleIds.has(id));
      initialized = true;
      if (!changed) return false;
      visibleIds = next;
      relationship = kind;
      rebuildView();
      if (fitView) requestFit();
      return true;
    },
    fit,
    setForces(settings) {
      worker?.postMessage({ type: 'settings', settings });
    },
    setDisplay(settings) {
      nodeSize = settings.nodeSize;
      linkWidth = settings.linkWidth;
      schedule();
    },
    // Unpins dragged nodes and shakes the layout into a new arrangement. A new
    // revision drops frames already queued from the old layout, so only the
    // new layout can settle the pending refit.
    rebalance() {
      if (!worker || !visibleNodes.length) return;
      worker.postMessage({ type: 'rebalance', revision: ++revision });
      requestFit();
    },
    // Lays the graph out again from its starting positions with these forces.
    // Without physics, it puts dragged nodes back and refits.
    resetLayout(settings) {
      if (!visibleNodes.length) return;
      if (!worker) {
        for (const [id, p] of initialPositions) positions.set(id, { ...p });
        fit(fitted && smoothCamera());
        return;
      }
      for (const [id, p] of initialPositions) {
        if (!visibleIds.has(id)) positions.set(id, { ...p });
      }
      const graph = layoutGraph();
      for (const node of graph.nodes) Object.assign(node, initialPositions.get(node.id));
      worker.postMessage({ type: 'reset', revision: ++revision, settings, graph });
      requestFit();
    },
    ensureVisible,
    zoomBy(factor) { if (Number.isFinite(factor) && factor > 0) zoomAt(factor); },
    setSelected(id) { selected = id; schedule(); },
    setFocusRoot(id) { focusRoot = id; schedule(); },
    clearCursor() {
      if (!cursor) return false;
      setCursor(null);
      return true;
    },
    destroy() {
      destroyed = true;
      clearTimeout(physicsTimer);
      worker?.terminate();
      worker = null;
      if (frame !== null) cancelAnimationFrame(frame);
      if (observer) observer.disconnect();
      window.removeEventListener('resize', resize);
      reducedMotion?.removeEventListener('change', motionPreferenceChanged);
      canvas.removeEventListener('pointerdown', pointerDown);
      canvas.removeEventListener('pointermove', pointerMove);
      canvas.removeEventListener('pointerup', pointerEnd);
      canvas.removeEventListener('pointercancel', pointerEnd);
      canvas.removeEventListener('lostpointercapture', pointerEnd);
      canvas.removeEventListener('pointerleave', pointerLeave);
      canvas.removeEventListener('wheel', wheel);
      canvas.removeEventListener('keydown', keydown);
    }
  };
}
