"use strict";

// Where everything sits on the network map. Pure maths, no DOM:
//   Collectivize.NetworkLayout.arrange(map, random?) -> { center, orgs, people, links, width, height }
//
// The node in focus is pinned in the middle. Everything else starts at a
// random spot and is then pulled into place:
//   - every node is tied to the center, on a shorter tie the more it
//     shares with the center (its `closeness`), so close ones sit nearer;
//   - people who are connected to each other are tied together, so
//     friends end up side by side; people who are only close in the
//     network (map.near) are tied more loosely;
//   - all nodes push each other apart so nothing overlaps.
// The starting spots are random, so the same network is drawn a little
// differently each time. Pass a `random` function to make it repeatable.

(() => {
    const WIDTH = 900;
    const HEIGHT = 640;
    const MARGIN = { x: 60, top: 44, bottom: 64 }; // room for nodes and their labels
    const STEPS = 320;
    const FAR = 250; // tie to the center for a node that shares nothing with it
    const NEAR = 120; // ...and for one that shares a lot
    const PEER_TIE = 105; // between two connected people
    const NEAR_TIE = 150; // between two people who are close but not connected
    const PUSH = 5200; // how hard nodes push each other apart
    const MIN_GAP = 74; // nodes closer than this are pushed apart firmly
    const CENTER_PULL = 0.05;
    const PEER_PULL = 0.03;
    const NEAR_PULL = 0.012;

    const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

    /** Tie length to the center: shorter the more the node shares with it. */
    const tieLength = (closeness, most) => (most > 0 ? FAR - (FAR - NEAR) * (closeness / most) : (FAR + NEAR) / 2);

    function arrange(map, random = Math.random) {
        const center = { x: WIDTH / 2, y: HEIGHT / 2 };
        const nodes = [...map.orgs.map((org) => ({ ...org, isOrg: true })), ...map.people];
        const most = Math.max(0, ...nodes.map((node) => node.closeness));
        // Crowded maps get longer ties so the rings don't jam.
        const stretch = 1 + Math.max(0, nodes.length - 14) * 0.025;

        const spots = nodes.map((node) => {
            const angle = random() * Math.PI * 2;
            const distance = 90 + random() * 170;
            return {
                node,
                x: center.x + Math.cos(angle) * distance,
                y: center.y + Math.sin(angle) * distance,
                tie: tieLength(node.closeness, most) * stretch,
            };
        });
        const byId = new Map(spots.map((spot) => [spot.node.id, spot]));
        const tied = (pairs, kind, length, pull) => (pairs || [])
            .map(([a, b]) => ({ a: byId.get(a), b: byId.get(b), kind, length, pull }))
            .filter((tie) => tie.a && tie.b);
        const ties = [
            ...tied(map.links, "peer", PEER_TIE, PEER_PULL),
            ...tied(map.near, "near", NEAR_TIE, NEAR_PULL),
        ];

        for (let step = 0; step < STEPS; step += 1) {
            const cooling = 1 - step / STEPS;
            const moves = spots.map(() => ({ x: 0, y: 0 }));
            spots.forEach((spot, i) => {
                // Tie to the center.
                const dx = spot.x - center.x;
                const dy = spot.y - center.y;
                const length = Math.hypot(dx, dy) || 1;
                const pull = (length - spot.tie) * CENTER_PULL;
                moves[i].x -= (dx / length) * pull;
                moves[i].y -= (dy / length) * pull;
                // Push away from every other node.
                for (let j = i + 1; j < spots.length; j += 1) {
                    const gx = spot.x - spots[j].x;
                    const gy = spot.y - spots[j].y;
                    const gap = Math.hypot(gx, gy) || 1;
                    const push = PUSH / (gap * gap) + (gap < MIN_GAP ? (MIN_GAP - gap) * 0.3 : 0);
                    moves[i].x += (gx / gap) * push;
                    moves[i].y += (gy / gap) * push;
                    moves[j].x -= (gx / gap) * push;
                    moves[j].y -= (gy / gap) * push;
                }
            });
            for (const tie of ties) {
                const { a, b } = tie;
                const dx = b.x - a.x;
                const dy = b.y - a.y;
                const length = Math.hypot(dx, dy) || 1;
                const pull = (length - tie.length) * tie.pull;
                const ia = spots.indexOf(a);
                const ib = spots.indexOf(b);
                moves[ia].x += (dx / length) * pull;
                moves[ia].y += (dy / length) * pull;
                moves[ib].x -= (dx / length) * pull;
                moves[ib].y -= (dy / length) * pull;
            }
            spots.forEach((spot, i) => {
                const limit = 4 + 26 * cooling; // big moves early, small ones late
                spot.x = clamp(spot.x + clamp(moves[i].x, -limit, limit), MARGIN.x, WIDTH - MARGIN.x);
                spot.y = clamp(spot.y + clamp(moves[i].y, -limit, limit), MARGIN.top, HEIGHT - MARGIN.bottom);
            });
        }

        const placed = spots.map((spot) => ({ ...spot.node, x: spot.x, y: spot.y }));
        const at = new Map(placed.map((node) => [node.id, node]));
        const links = [
            // A tie to or from a community is a membership; between people, a connection.
            ...placed.map((node) => ({
                from: center, to: node, kind: node.isOrg || map.center.isOrg ? "org" : "direct",
            })),
            ...ties.map((tie) => ({ from: at.get(tie.a.node.id), to: at.get(tie.b.node.id), kind: tie.kind })),
        ];
        return {
            center: { ...map.center, ...center },
            orgs: placed.filter((node) => node.isOrg),
            people: placed.filter((node) => !node.isOrg),
            links,
            width: WIDTH,
            height: HEIGHT,
        };
    }

    window.Collectivize = Object.freeze({ ...window.Collectivize, NetworkLayout: Object.freeze({ arrange }) });
})();
