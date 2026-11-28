// Adapted from Graph Workspace, src/graph/physics.ts (25d1209).
const { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } = d3;

const defaultForces = {
    centerStrength: 0.1,
    repelStrength: 10,
    linkStrength: 1,
    linkDistance: 250,
};
// d3's default decay settles in about 300 ticks; filter changes use a faster
// decay so a gentle reheat settles in about a second and a half.
const defaultDecay = 1 - Math.pow(0.001, 1 / 300);
const settleDecay = 0.045;
// Ticks run before the first reply so the initial layout appears mostly settled.
const presettleTicks = 100;
function bounded(value, min, max, fallback) {
    return Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;
}
function hashText(text) {
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
        hash = Math.imul(hash ^ text.charCodeAt(index), 16777619);
    }
    return hash >>> 0;
}
function normalizeForces(settings) {
    return {
        centerStrength: bounded(settings.centerStrength, 0, 1, defaultForces.centerStrength),
        repelStrength: bounded(settings.repelStrength, 0, 20, defaultForces.repelStrength),
        linkStrength: bounded(settings.linkStrength, 0, 1, defaultForces.linkStrength),
        linkDistance: bounded(settings.linkDistance, 30, 500, defaultForces.linkDistance),
    };
}
// Give each ID a stable starting point, regardless of the order of the input.
function initialPosition(id) {
    const unsigned = hashText(id);
    const angle = (unsigned / 0x100000000) * Math.PI * 2;
    const radius = 30 + ((unsigned >>> 16) % 200);
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}
/** Owns one cooling simulation. It runs only after a change or explicit reheat. */
function createPhysics(onPositions) {
    let nodes = [];
    // Keep hidden nodes so removing a filter restores their previous layout.
    const savedNodes = new Map();
    let edgeCounts = new Map();
    let hasData = false;
    let reducedMotion = false;
    let rebalances = 0;
    const pinned = new Set();
    let forces = { ...defaultForces };
    let degrees = new Map();
    const links = forceLink().id((node) => node.id);
    const repulsion = forceManyBody().distanceMin(30);
    const xCenter = forceX(0);
    const yCenter = forceY(0);
    const simulation = forceSimulation([])
        .alphaMin(0.001)
        .alphaTarget(0)
        .velocityDecay(0.4)
        .force('links', links)
        .force('repulsion', repulsion)
        .force('collision', forceCollide(60).strength(0.5))
        .force('centerX', xCenter)
        .force('centerY', yCenter)
        .stop();
    function positions() {
        return nodes.map((node) => ({ id: node.id, x: node.x ?? 0, y: node.y ?? 0 }));
    }
    function emit() {
        onPositions(positions(), simulation.alpha());
    }
    function applyForces() {
        links.strength((link) => {
            const source = typeof link.source === 'string' ? link.source : link.source.id;
            const target = typeof link.target === 'string' ? link.target : link.target.id;
            return forces.linkStrength / Math.max(1, Math.min(degrees.get(source) ?? 0, degrees.get(target) ?? 0));
        }).distance(forces.linkDistance);
        // The reference simulation retains a minimum repulsion even at zero.
        repulsion.strength(-Math.max(1, forces.repelStrength ** 3));
        xCenter.strength(forces.centerStrength);
        yCenter.strength(forces.centerStrength);
    }
    function unpinAll() {
        for (const node of savedNodes.values()) {
            node.fx = null;
            node.fy = null;
        }
        pinned.clear();
    }
    function restart(alpha, decay = defaultDecay) {
        if (nodes.length === 0) {
            simulation.stop();
            return;
        }
        simulation.alphaDecay(decay);
        simulation.alpha(Math.max(simulation.alpha(), alpha)).alphaTarget(pinned.size && !reducedMotion ? 0.3 : 0);
        if (reducedMotion) {
            simulation.stop();
            if (!pinned.size) {
                simulation.tick(300);
                emit();
            }
        } else {
            simulation.restart();
        }
    }
    applyForces();
    simulation.on('tick', emit).on('end', emit);
    const api = {
        setData(data) {
            const seeds = new Map(data.nodes.map(node => [node.id, node]));
            const ids = [...seeds.keys()];
            const nextLinks = data.links
                .filter(({ source, target }) => source !== target && seeds.has(source) && seeds.has(target))
                .map(({ source, target }) => ({ source, target }));
            const nextEdgeCounts = new Map();
            for (const { source, target } of nextLinks) {
                const key = JSON.stringify([source, target]);
                nextEdgeCounts.set(key, (nextEdgeCounts.get(key) ?? 0) + 1);
            }
            const sameTopology = hasData && ids.length === nodes.length &&
                nodes.every((node) => seeds.has(node.id)) && nextEdgeCounts.size === edgeCounts.size &&
                [...nextEdgeCounts].every(([key, count]) => edgeCounts.get(key) === count);
            // Acknowledge every revision, even when nothing moves, so the client
            // never waits on a stale one.
            if (sameTopology) {
                emit();
                return;
            }
            for (const node of nodes) {
                if (!seeds.has(node.id)) {
                    node.fx = null;
                    node.fy = null;
                    node.vx = 0;
                    node.vy = 0;
                    pinned.delete(node.id);
                }
            }
            const fresh = ids.some((id) => !savedNodes.has(id));
            nodes = ids.map((id) => {
                let node = savedNodes.get(id);
                if (!node) {
                    const seed = seeds.get(id);
                    const position = Number.isFinite(seed.x) && Number.isFinite(seed.y) ? { x: seed.x, y: seed.y } : initialPosition(id);
                    node = { id, ...position };
                    savedNodes.set(id, node);
                }
                return node;
            });
            edgeCounts = nextEdgeCounts;
            // Count both ends, as d3's default link strength does.
            degrees = new Map();
            for (const { source, target } of nextLinks) {
                for (const id of [source, target]) {
                    if (typeof id === 'string')
                        degrees.set(id, (degrees.get(id) ?? 0) + 1);
                }
            }
            // Clear old endpoints before replacing nodes, then let d3 resolve new IDs.
            links.links([]);
            simulation.nodes(nodes);
            links.links(nextLinks);
            const firstLayout = !hasData;
            hasData = true;
            if (!nodes.length || reducedMotion) {
                if (!nodes.length) emit();
                restart(firstLayout ? 1 : 0.3);
                return;
            }
            if (firstLayout || fresh) {
                // Lay out new nodes before showing them, then let the rest settle.
                simulation.alpha(firstLayout ? 1 : 0.5).alphaDecay(defaultDecay);
                simulation.tick(firstLayout ? presettleTicks : presettleTicks / 2);
            }
            else {
                simulation.alpha(Math.max(simulation.alpha(), 0.12));
            }
            emit();
            restart(0, firstLayout ? defaultDecay : settleDecay);
        },
        setSettings(settings) {
            const next = normalizeForces(settings);
            if (Object.keys(next).every((key) => next[key] === forces[key]))
                return;
            forces = next;
            applyForces();
            restart(0.3);
        },
        pin(id, x, y) {
            const node = nodes.find((candidate) => candidate.id === id);
            if (!node)
                return;
            if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) {
                node.fx = null;
                node.fy = null;
                pinned.delete(id);
            }
            else {
                pinned.add(id);
                node.fx = x;
                node.fy = y;
                node.x = x;
                node.y = y;
                node.vx = 0;
                node.vy = 0;
                emit();
            }
            restart(0.3);
        },
        // Unpins every node, nudges each by a small deterministic offset that
        // differs per press, and lets the layout settle again from full heat.
        rebalance() {
            unpinAll();
            rebalances += 1;
            for (const node of nodes) {
                const unsigned = hashText(node.id + '\n' + rebalances);
                const angle = (unsigned / 0x100000000) * Math.PI * 2;
                const distance = 20 + ((unsigned >>> 16) % 40);
                node.x += Math.cos(angle) * distance;
                node.y += Math.sin(angle) * distance;
                node.vx = 0;
                node.vy = 0;
            }
            restart(1, settleDecay);
        },
        // Forgets every saved position and pin, then lays the graph out from the
        // given starting positions as on first load.
        reset(data, settings) {
            unpinAll();
            savedNodes.clear();
            nodes = [];
            edgeCounts = new Map();
            hasData = false;
            forces = normalizeForces(settings || {});
            applyForces();
            api.setData(data);
        },
        setReducedMotion(value) {
            if (value === reducedMotion) return;
            reducedMotion = value;
            if (value) restart(0);
        },
    };
    return api;
}

let revision = 0;
let gesture = 0;
const physics = createPhysics((nodes, alpha) => self.postMessage({ revision, gesture, nodes, alpha }));

self.onmessage = ({ data }) => {
    switch (data.type) {
        case 'setData':
            revision = data.revision;
            physics.setData(data.graph);
            break;
        case 'pin':
            gesture = data.gesture;
            physics.pin(data.id, data.x, data.y);
            break;
        case 'settings':
            physics.setSettings(data.settings);
            break;
        case 'motion':
            physics.setReducedMotion(data.reduced);
            break;
        case 'rebalance':
            revision = data.revision;
            physics.rebalance();
            break;
        case 'reset':
            revision = data.revision;
            physics.reset(data.graph, data.settings);
            break;
    }
};
