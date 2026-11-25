function createMemoryGraph(canvas, data, callbacks) {
  const context = canvas.getContext('2d');
  if (!context) return { setView() {}, fit() {}, reveal() {}, zoomBy() {}, setSelected() {}, setForces() {}, setDisplay() {}, reheat() {}, destroy() {} };
  const nodes = data.nodes;
  const nodeMap = new Map(nodes.map(node => [node.id, node]));
  const positions = new Map();
  const links = data.links.filter(link => nodeMap.has(link.source) && nodeMap.has(link.target));
  const options = callbacks || {};
  const reducedMotion = options.reducedMotion;
  let worker = null;
  let revision = 0, gesture = 0;
  let followLayout = true;
  let layoutStarted = null, lastAutoFit = -Infinity;
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
  const selection = color('--graph-selection', '#F06A8B');
  const edgePaint = {
    similarity: { color: color('--edge-similarity', '#B8B0B2'), strength: .3, highlight: .85 },
    supersedes: { color: color('--edge-correction', '#E8B072'), strength: .74, highlight: 1 },
  };
  let visibleIds = new Set(nodes.map(node => node.id));
  let relationship = 'all';
  let visibleNodes = nodes.slice();
  let visibleLinks = links.slice();
  let neighbors = new Map();
  let selected = null;
  let hovered = null;
  let drag = null;
  let secondTouch = null;
  const labelBoxes = [];
  let width = 0, height = 0, dpr = 1, frame = null, fitted = false, destroyed = false, portrait = false;
  let overviewZoom = 1;
  let nodeSize = 1, linkWidth = 1;
  const camera = { x: 0, y: 0, zoom: 1 };
  let cameraFit = null;
  const minZoom = 0.08, maxZoom = 5;
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
    if (drag?.id && !visibleIds.has(drag.id)) {
      releaseNode(drag);
      drag.id = null;
      drag.moved = true;
    }
    if (updatePhysics && worker) {
      if (revision) followLayout = false;
      worker.postMessage({ type: 'setData', revision: ++revision, graph: {
        nodes: visibleNodes.map(({ id }) => ({ id, ...positions.get(id) })),
        links: visibleLinks.map(({ source, target }) => ({ source, target })),
      } });
    }
    if (hovered && !visibleIds.has(hovered)) updateHover(null);
    schedule();
  }
  function schedule() {
    if (!destroyed && frame === null) frame = requestAnimationFrame(draw);
  }
  function receiveLayout({ data }) {
    if (data.revision !== revision || data.gesture !== gesture) return;
    for (const { id, x, y } of data.nodes) {
      if (visibleIds.has(id) && drag?.id !== id) positions.set(id, { x, y });
    }
    if (followLayout && data.nodes.length) {
      const now = performance.now();
      layoutStarted ??= now;
      if (now - lastAutoFit >= 220) {
        fit(!reducedMotion?.matches);
        lastAutoFit = now;
      }
      if (reducedMotion?.matches || now - layoutStarted >= 2500) followLayout = false;
    }
    schedule();
  }
  function motionPreferenceChanged() {
    if (reducedMotion?.matches) stopFit();
    worker?.postMessage({ type: 'motion', reduced: reducedMotion?.matches || false });
  }
  function radius(node) {
    return nodeSize * clamp(3.4 + Math.sqrt(Math.max(0, Number(node.deg) || 0)) * 0.85, 3.4, 8) / Math.sqrt(camera.zoom);
  }
  function worldOffset(x, y) {
    return portrait ? { x: y / camera.zoom, y: -x / camera.zoom } : { x: x / camera.zoom, y: y / camera.zoom };
  }
  function screenToWorld(point) {
    const offset = worldOffset(point.x - width / 2, point.y - height / 2);
    return { x: offset.x + camera.x, y: offset.y + camera.y };
  }
  function worldToScreen(point) {
    const x = (point.x - camera.x) * camera.zoom;
    const y = (point.y - camera.y) * camera.zoom;
    return { x: width / 2 + (portrait ? -y : x), y: height / 2 + (portrait ? x : y) };
  }
  function draw(time) {
    frame = null;
    labelBoxes.length = 0;
    if (!width || !height) return;
    if (cameraFit) {
      cameraFit.startTime ??= time;
      const progress = Math.min(1, (time - cameraFit.startTime) / 220);
      const eased = 1 - (1 - progress) ** 3;
      const { start, target } = cameraFit;
      camera.x = start.x + (target.x - start.x) * eased;
      camera.y = start.y + (target.y - start.y) * eased;
      camera.zoom = start.zoom + (target.zoom - start.zoom) * eased;
      if (progress < 1) schedule();
      else {
        overviewZoom = target.zoom;
        stopFit();
      }
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    context.translate(width / 2, height / 2);
    context.scale(camera.zoom, camera.zoom);
    if (portrait) context.rotate(Math.PI / 2);
    context.translate(-camera.x, -camera.y);
    const active = hovered || (selected && visibleIds.has(selected) ? selected : null);
    const adjacent = active ? neighbors.get(active) : null;
    const margin = 35 / camera.zoom;
    const halfWidth = (portrait ? height : width) / (2 * camera.zoom);
    const halfHeight = (portrait ? width : height) / (2 * camera.zoom);
    const left = camera.x - halfWidth - margin;
    const right = camera.x + halfWidth + margin;
    const top = camera.y - halfHeight - margin;
    const bottom = camera.y + halfHeight + margin;
    const labels = [];
    for (const kind of ['similarity', 'supersedes']) {
      const paint = edgePaint[kind];
      for (const highlighted of [false, true]) {
        context.beginPath();
        context.setLineDash(kind === 'supersedes' ? [5 / camera.zoom, 4 / camera.zoom] : []);
        context.lineWidth = linkWidth * (highlighted ? 1.2 : 0.8) / camera.zoom;
        context.strokeStyle = paint.color;
        context.globalAlpha = highlighted ? paint.highlight : paint.strength * (active ? .24 : 1);
        for (const link of visibleLinks) {
          if (link.kind !== kind || (!!active && (link.source === active || link.target === active)) !== highlighted) continue;
          const a = positions.get(link.source), b = positions.get(link.target);
          if (Math.max(a.x, b.x) < left || Math.min(a.x, b.x) > right || Math.max(a.y, b.y) < top || Math.min(a.y, b.y) > bottom) continue;
          context.moveTo(a.x, a.y); context.lineTo(b.x, b.y);
        }
        context.stroke(); context.globalAlpha = 1;
      }
    }
    context.setLineDash([]);
    for (const node of visibleNodes) {
      const p = positions.get(node.id);
      if (p.x < left || p.x > right || p.y < top || p.y > bottom) continue;
      const isActive = node.id === active;
      const related = !active || isActive || (adjacent && adjacent.has(node.id));
      const r = radius(node);
      context.globalAlpha = related ? 1 : 0.25;
      context.fillStyle = node.color || '#B8B0B2';
      context.beginPath(); context.arc(p.x, p.y, r, 0, Math.PI * 2); context.fill();
      if (isActive || node.id === selected) {
        context.strokeStyle = node.id === selected ? selection : ink; context.lineWidth = 1.4 / camera.zoom;
        context.beginPath(); context.arc(p.x, p.y, r + 3 / camera.zoom, 0, Math.PI * 2); context.stroke();
      }
      const showLabel = isActive || node.id === selected || (
        !active && !cameraFit && visibleNodes.length <= 48 && camera.zoom >= Math.max(1.2, overviewZoom * 1.45)
      );
      if (showLabel) labels.push({ node, p, r, priority: isActive ? 2 : node.id === selected ? 1 : 0 });
    }
    context.globalAlpha = 1;
    context.fillStyle = ink;
    context.textAlign = 'center'; context.textBaseline = 'top';
    for (const { node, p, r, priority } of labels.sort((a, b) => b.priority - a.priority)) {
      const text = String(node.text || '').replace(/\s+/g, ' ').trim();
      if (!text) continue;
      const label = text.length > 44 ? text.slice(0, 41) + '…' : text;
      const fontSize = priority === 2 ? 12 : 11;
      context.font = fontSize / camera.zoom + 'px "Source Serif 4", Georgia, serif';
      const screen = worldToScreen(p);
      if (screen.x < 0 || screen.x > width || screen.y < 0 || screen.y > height) continue;
      const labelWidth = Math.min(context.measureText(label).width * camera.zoom, Math.max(1, width - 16));
      const halfLabel = labelWidth / 2 + 2;
      const center = priority ? clamp(screen.x, halfLabel + 4, width - halfLabel - 4) : screen.x;
      const belowDot = screen.y + r * camera.zoom + 5;
      const y = priority ? clamp(belowDot, 5, height - fontSize - 4) : belowDot;
      const box = { id: node.id, active: priority === 2, left: center - halfLabel, right: center + halfLabel, top: y - 5, bottom: y + fontSize + 3 };
      if (labelBoxes.some(other => box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top)) continue;
      if (!priority && (box.left < 0 || box.right > width || box.bottom > height)) continue;
      context.save();
      context.translate(p.x, p.y);
      if (portrait) context.rotate(-Math.PI / 2);
      context.fillText(label, (center - screen.x) / camera.zoom, (y - screen.y) / camera.zoom, labelWidth / camera.zoom);
      context.restore();
      labelBoxes.push(box);
    }
    context.globalAlpha = 1;
  }
  function reportCamera() { if (options.onCameraChange) options.onCameraChange(camera.zoom / overviewZoom); }
  function stopFit() {
    if (!cameraFit) return;
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
    if ((!fitted || orientationChanged) && width > 1 && height > 1 && visibleNodes.length) fit();
    else schedule();
  }
  function fit(smooth = false) {
    if (!visibleNodes.length || width <= 1 || height <= 1) return;
    labelBoxes.length = 0;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of visibleNodes) {
      const p = positions.get(node.id);
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    const availableWidth = Math.max(1, width - (portrait ? 56 : 112));
    const availableHeight = Math.max(1, height - 144);
    const fitX = (portrait ? availableHeight : availableWidth) / Math.max(100, maxX - minX);
    const fitY = (portrait ? availableWidth : availableHeight) / Math.max(100, maxY - minY);
    const target = { x: (minX + maxX) / 2, y: (minY + maxY) / 2, zoom: clamp(Math.min(fitX, fitY), minZoom, visibleNodes.length === 1 ? 1 : 2.5) };
    if (smooth && fitted && !drag) {
      cameraFit = { start: { ...camera }, target, startTime: null };
      updateHover(null);
    } else {
      cameraFit = null;
      Object.assign(camera, target);
      overviewZoom = target.zoom;
      reportCamera();
    }
    fitted = true;
    schedule();
  }
  function reveal() {
    const inView = visibleNodes.some(node => {
      const point = positions.get(node.id);
      return Math.abs(point.x - camera.x) * camera.zoom < (portrait ? height : width) / 2 - 12 &&
        Math.abs(point.y - camera.y) * camera.zoom < (portrait ? width : height) / 2 - 12;
    });
    if (!inView) fit();
  }
  function zoomAt(factor, point, previousPoint = point) {
    followLayout = false;
    stopFit();
    const anchor = point || { x: width / 2, y: height / 2 };
    const world = screenToWorld(previousPoint || anchor);
    camera.zoom = clamp(camera.zoom * factor, minZoom, maxZoom);
    const offset = worldOffset(anchor.x - width / 2, anchor.y - height / 2);
    camera.x = world.x - offset.x;
    camera.y = world.y - offset.y;
    labelBoxes.length = 0;
    reportCamera(); schedule();
  }
  function localPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  function hitTest(point) {
    const world = screenToWorld(point);
    let nearest = null, dot = null;
    let nearestDistance = Infinity, dotDistance = Infinity;
    for (let i = visibleNodes.length - 1; i >= 0; i--) {
      const node = visibleNodes[i], p = positions.get(node.id);
      const distance = Math.hypot(world.x - p.x, world.y - p.y);
      const dotRadius = radius(node);
      if (distance <= dotRadius && distance < dotDistance) {
        dot = node.id;
        dotDistance = distance;
      }
      if (distance <= 8 / camera.zoom && distance < nearestDistance) {
        nearest = node.id;
        nearestDistance = distance;
      }
    }
    if (dot || nearest) return dot || nearest;
    let label = null;
    for (let i = labelBoxes.length - 1; i >= 0; i--) {
      const box = labelBoxes[i];
      if (point.x < box.left || point.x > box.right || point.y < box.top || point.y > box.bottom) continue;
      if (box.active) return box.id;
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
  function pointerDown(event) {
    if (event.button !== 0) return;
    followLayout = false;
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
    const point = localPoint(event), id = hitTest(point), world = screenToWorld(point);
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
    if (!drag) { if (event.pointerType !== 'touch') updateHover(hitTest(point)); return; }
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
    if (!drag.moved && Math.hypot(point.x - drag.start.x, point.y - drag.start.y) > 5) drag.moved = true;
    if (drag.moved) {
      labelBoxes.length = 0;
      if (drag.id) {
        moveNode(drag, point);
      } else {
        const offset = worldOffset(point.x - drag.last.x, point.y - drag.last.y);
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
    if (event.type === 'pointerup' && !ended.moved && ended.id && hitTest(localPoint(event)) === ended.id && options.onOpen) options.onOpen(nodeMap.get(ended.id));
    updateHover(event.type === 'pointerup' && ended.pointerType !== 'touch' ? hitTest(localPoint(event)) : null);
    canvas.style.cursor = hovered ? 'pointer' : 'grab';
  }
  function wheel(event) {
    event.preventDefault();
    zoomAt(Math.exp(-event.deltaY * 0.0015), localPoint(event));
  }
  function keydown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const step = event.shiftKey ? 100 : 25;
    const directions = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const direction = directions[event.key];
    if (direction && Array.isArray(direction)) {
      followLayout = false;
      stopFit();
      const offset = worldOffset(...direction);
      camera.x += offset.x; camera.y += offset.y;
      labelBoxes.length = 0;
    }
    else if (event.key === '+' || event.key === '=' || event.code === 'NumpadAdd') { event.preventDefault(); zoomAt(1.5); return; }
    else if (event.key === '-' || event.key === '_' || event.code === 'NumpadSubtract') { event.preventDefault(); zoomAt(1 / 1.5); return; }
    else return;
    event.preventDefault(); reportCamera(); schedule();
  }
  function pointerLeave() { if (!drag) updateHover(null); }
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
    motionPreferenceChanged();
  }
  rebuildView(false); resize();
  canvas.ownerDocument?.fonts?.ready.then(schedule);
  return {
    setView(ids, kind) { visibleIds = new Set(ids); relationship = kind; rebuildView(); },
    fit(smooth) {
      if (smooth) followLayout = false;
      fit(smooth);
    },
    setForces(settings) {
      followLayout = false;
      worker?.postMessage({ type: 'settings', settings });
    },
    setDisplay(settings) {
      nodeSize = settings.nodeSize;
      linkWidth = settings.linkWidth;
      schedule();
    },
    reheat() { worker?.postMessage({ type: 'reheat' }); },
    reveal,
    zoomBy(factor) { if (Number.isFinite(factor) && factor > 0) zoomAt(factor); },
    setSelected(id) { selected = id; schedule(); },
    destroy() {
      destroyed = true;
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
