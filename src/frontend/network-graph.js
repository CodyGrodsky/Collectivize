"use strict";

// The network graph (v1.1 spec 4.2): one fixed-size circle, reused on the
// Network page (yours) and on a connection's profile (theirs). The server
// decides what's in it (backend/graph/visibility.py); this only draws.
//
// - You (or the profile's owner) in the centre, in a distinct colour, with
//   their connections and orgs around them. Events are never nodes here.
// - Never the whole network: the server caps it; "Show more" asks for more.
// - Drag (finger or mouse) to pan; the circle itself never changes size.
//   Pinch or the wheel zooms.
// - In a friend's network, people you already know are marked, and the
//   rest offer "Connect" (quick add). Tap a node for its card.
//
// Usage: new NetworkGraph(container).load(personId)
// Text is only ever inserted as text.

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;
    const SVG = "http://www.w3.org/2000/svg";
    const MORE_PEOPLE = 25;
    const MORE_ORGS = 10;
    const VIEW = 440; // viewBox size: what fits in the circle at zoom 1
    const FIRST_RING = 78;
    const RING_GAP = 58;
    const NODE_SPACING = 50;
    const DRAG_THRESHOLD = 6;
    const MIN_ZOOM = 0.5;
    const MAX_ZOOM = 2.5;

    function svg(tag, attrs = {}, children = []) {
        const node = document.createElementNS(SVG, tag);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        for (const child of children) node.append(child);
        return node;
    }

    const firstName = (label) => cleanName(label).split(" ")[0];

    /** Positions on rings around the centre: people first (inner rings),
     *  then orgs on the next ring out. */
    function layout(people, orgs) {
        const placed = [];
        let ring = 0;
        const fill = (nodes) => {
            let i = 0;
            while (i < nodes.length) {
                const radius = FIRST_RING + ring * RING_GAP;
                const capacity = Math.max(6, Math.floor((2 * Math.PI * radius) / NODE_SPACING));
                const batch = nodes.slice(i, i + capacity);
                const offset = ring % 2 ? Math.PI / batch.length : 0; // stagger rings
                batch.forEach((node, k) => {
                    const angle = -Math.PI / 2 + offset + (2 * Math.PI * k) / batch.length;
                    placed.push({ node, x: radius * Math.cos(angle), y: radius * Math.sin(angle) });
                });
                i += batch.length;
                ring += 1;
            }
        };
        fill(people);
        fill(orgs);
        return { placed, radius: FIRST_RING + Math.max(0, ring - 1) * RING_GAP };
    }

    class NetworkGraph {
        constructor(container) {
            this.container = container;
            this.limits = { people: MORE_PEOPLE, orgs: MORE_ORGS };
            this.view = { x: 0, y: 0, zoom: 1 };
            this.data = null;
            this.selected = null;
            this.build();
        }

        build() {
            this.status = el("p", { className: "ng-status", attrs: { role: "status", "aria-live": "polite" } });
            this.svg = svg("svg", { class: "ng-svg", viewBox: `${-VIEW / 2} ${-VIEW / 2} ${VIEW} ${VIEW}`,
                                    role: "group", "aria-label": "Network" });
            this.scene = svg("g");
            this.svg.append(this.scene);
            this.circle = el("div", { className: "ng-circle" }, [this.svg]);
            this.recenter = el("button", { className: "ng-recenter", text: "Re-center", attrs: { type: "button" } });
            this.recenter.addEventListener("click", () => this.setView({ x: 0, y: 0, zoom: 1 }));
            this.more = el("button", { className: "button", text: "Show more", attrs: { type: "button" } });
            this.more.addEventListener("click", () => this.showMore());
            this.count = el("p", { className: "ng-count" });
            this.card = el("div", { className: "ng-card", attrs: { "aria-live": "polite" } });
            this.container.replaceChildren(
                el("div", { className: "ng-frame" }, [this.circle, this.recenter]),
                el("div", { className: "ng-footer" }, [this.count, this.more]),
                this.status,
                this.card,
            );
            this.bindPan();
        }

        async load(personId) {
            this.personId = personId;
            this.status.textContent = "Loading network…";
            try {
                const data = await Api.get("/api/network", { id: personId, ...this.limits });
                this.data = data;
                this.status.textContent = "";
                this.render();
                return data;
            } catch (error) {
                this.status.textContent = error.message;
                return null;
            }
        }

        showMore() {
            this.limits = {
                people: this.limits.people + MORE_PEOPLE,
                orgs: this.limits.orgs + MORE_ORGS,
            };
            this.load(this.personId);
        }

        // --- drawing -----------------------------------------------------------------

        render() {
            const data = this.data;
            if (!data.visible) {
                this.container.hidden = true;
                return;
            }
            const { placed, radius } = layout(data.people, data.orgs);
            this.radius = radius;
            const lines = svg("g", { class: "ng-links" });
            const nodes = svg("g", { class: "ng-nodes" });
            for (const { node, x, y } of placed) {
                lines.append(svg("line", { x1: 0, y1: 0, x2: x, y2: y,
                                           class: node.kind === "org" ? "is-org" : "" }));
                nodes.append(this.node(node, x, y));
            }
            nodes.append(this.node(data.center, 0, 0));
            this.scene.replaceChildren(lines, nodes);

            const shown = data.people.length + data.orgs.length;
            const total = data.people_total + data.orgs_total;
            const who = data.is_own ? "You" : firstName(data.center.label);
            this.count.textContent = data.connections_hidden
                ? `${who} chose not to show connections · ${data.orgs_total} orgs`
                : `${data.people_total} connection${data.people_total === 1 ? "" : "s"} · ${data.orgs_total} org${data.orgs_total === 1 ? "" : "s"}`
                  + (shown < total ? ` · showing ${shown}` : "");
            this.more.hidden = shown >= total;
            this.setView(this.view);
            if (this.selected) {
                const again = [data.center, ...data.people, ...data.orgs].find((n) => n.id === this.selected.id);
                this.select(again || null);
            }
        }

        node(node, x, y) {
            const center = node.is_center;
            const size = center ? 24 : node.kind === "org" ? 15 : 16;
            const classes = ["ng-node", `is-${node.kind}`];
            if (center) classes.push("is-center");
            if (node.is_viewer) classes.push("is-viewer");
            if (!this.data.is_own && node.kind === "person" && !center) {
                classes.push(node.viewer_connected || node.is_viewer ? "is-known" : "is-new");
            }
            const group = svg("g", { class: classes.join(" "), transform: `translate(${x} ${y})`,
                                     tabindex: "0", role: "button", "data-id": node.id });
            const label = node.is_viewer && !center ? "You" : node.kind === "org" ? cleanName(node.label) : firstName(node.label);
            group.setAttribute("aria-label", NetworkGraph.describe(node, this.data.is_own));

            const shape = node.kind === "org"
                ? svg("rect", { x: -size, y: -size, width: size * 2, height: size * 2, rx: 6 })
                : svg("circle", { r: size });
            group.append(shape);
            if (node.has_photo) {
                const clip = `ng-clip-${node.id}`;
                group.append(
                    svg("clipPath", { id: clip }, [svg("circle", { r: size - 2 })]),
                    svg("image", { href: `/api/photo?${new URLSearchParams({ id: node.id, slot: "0" })}`,
                                   x: -(size - 2), y: -(size - 2), width: (size - 2) * 2, height: (size - 2) * 2,
                                   "clip-path": `url(#${clip})`, preserveAspectRatio: "xMidYMid slice" }),
                );
            } else {
                const initial = svg("text", { class: "ng-initial", "text-anchor": "middle", dy: "0.35em" });
                initial.textContent = (node.kind === "org" ? cleanName(node.label) : label).charAt(0).toUpperCase();
                group.append(initial);
            }
            if (classes.includes("is-new")) {
                const badge = svg("g", { class: "ng-badge", transform: `translate(${size * 0.75} ${-size * 0.75})` });
                badge.append(svg("circle", { r: 7 }), svg("path", { d: "M-3.5 0h7M0-3.5v7" }));
                group.append(badge);
            }
            const text = svg("text", { class: "ng-label", "text-anchor": "middle", y: size + 13 });
            text.textContent = label.length > 16 ? `${label.slice(0, 15)}…` : label;
            group.append(text);

            const pick = () => this.select(node);
            group.addEventListener("click", (event) => {
                if (this.dragged) return;
                event.stopPropagation();
                pick();
            });
            group.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    pick();
                }
            });
            return group;
        }

        static describe(node, isOwn) {
            if (node.kind === "org") return `${cleanName(node.label)}, organization`;
            const parts = [cleanName(node.label)];
            if (node.is_center) parts.push(isOwn ? "you" : "this profile");
            else if (node.is_viewer) parts.push("you");
            else if (!isOwn) parts.push(node.viewer_connected ? "already your connection" : "not connected to you");
            return parts.join(", ");
        }

        // --- the card under the graph (tap a node) -----------------------------------

        select(node) {
            this.selected = node;
            for (const g of this.scene.querySelectorAll(".ng-node")) g.classList.remove("is-selected");
            if (!node) {
                this.card.replaceChildren();
                return;
            }
            const match = [...this.scene.querySelectorAll(".ng-node")].find((g) => g.getAttribute("data-id") === node.id);
            if (match) match.classList.add("is-selected");

            const isOrg = node.kind === "org";
            const href = isOrg ? Ui.orgHref(node.id) : Ui.profileHref(node.id);
            const actions = [el("a", { className: "button", text: isOrg ? "View organization" : "View profile", attrs: { href } })];
            if (!isOrg && !node.is_viewer) actions.push(...this.connectActions(node));
            const meta = isOrg ? "Organization"
                : node.is_viewer ? "You"
                : node.connection === "connected" ? "Connected"
                : this.data.is_own ? "" : "Connection of " + firstName(this.data.center.label);
            this.card.replaceChildren(el("div", { className: "ng-card-inner" }, [
                Ui.avatar(node),
                el("div", { className: "list-main" }, [
                    el("p", { className: "list-title", text: cleanName(node.label) }),
                    meta ? el("p", { className: "list-meta", text: meta }) : null,
                ]),
                el("div", { className: "row" }, actions),
            ]));
        }

        connectActions(node) {
            const state = node.connection;
            if (state === "connected" || state === "self") return [];
            if (state === "pending_out") return [el("span", { className: "pill", text: "Requested" })];
            const button = el("button", {
                className: "button button-solid",
                text: state === "pending_in" ? "Accept request" : "Connect",
                attrs: { type: "button" },
            });
            button.addEventListener("click", async () => {
                button.disabled = true;
                try {
                    const result = await Api.post("/api/connect", { person_id: node.id });
                    if (result.notice) this.status.textContent = result.notice;
                    node.connection = result.connection;
                    await this.load(this.personId);
                } catch (error) {
                    this.status.textContent = error.message;
                    button.disabled = false;
                }
            });
            const actions = [button];
            if (!this.data.is_own) {
                // SPEC-ASSUMPTION: no event-invite flow exists yet, so this is a stub.
                actions.push(el("button", { className: "button", text: "Invite to an event",
                                            attrs: { type: "button", disabled: true, title: "Coming soon" } }));
            }
            return actions;
        }

        // --- pan and zoom ------------------------------------------------------------

        setView({ x, y, zoom }) {
            const limit = (this.radius || 0) + 40;
            this.view = {
                zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom)),
                x: Math.max(-limit, Math.min(limit, x)),
                y: Math.max(-limit, Math.min(limit, y)),
            };
            const { x: vx, y: vy, zoom: z } = this.view;
            this.scene.setAttribute("transform", `scale(${z}) translate(${-vx} ${-vy})`);
            this.recenter.hidden = Math.abs(vx) < 1 && Math.abs(vy) < 1 && Math.abs(z - 1) < 0.01;
        }

        bindPan() {
            const pointers = new Map();
            let start = null;
            const scale = () => VIEW / this.svg.getBoundingClientRect().width;

            this.svg.addEventListener("pointerdown", (event) => {
                pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
                this.svg.setPointerCapture(event.pointerId);
                this.dragged = false;
                start = { view: { ...this.view }, pointers: new Map(pointers) };
            });
            this.svg.addEventListener("pointermove", (event) => {
                if (!pointers.has(event.pointerId) || !start) return;
                pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
                const [a, b] = [...pointers.values()];
                const [a0, b0] = [...start.pointers.values()];
                if (pointers.size >= 2 && a0 && b0) { // pinch to zoom
                    const ratio = Math.hypot(a.x - b.x, a.y - b.y) / (Math.hypot(a0.x - b0.x, a0.y - b0.y) || 1);
                    this.dragged = true;
                    this.setView({ ...start.view, zoom: start.view.zoom * ratio });
                    return;
                }
                const first = start.pointers.get(event.pointerId);
                if (!first) return;
                const dx = event.clientX - first.x;
                const dy = event.clientY - first.y;
                if (Math.hypot(dx, dy) > DRAG_THRESHOLD) this.dragged = true;
                if (!this.dragged) return;
                const s = scale() / start.view.zoom;
                this.setView({ ...start.view, x: start.view.x - dx * s, y: start.view.y - dy * s });
            });
            const end = (event) => {
                pointers.delete(event.pointerId);
                start = pointers.size ? { view: { ...this.view }, pointers: new Map(pointers) } : null;
                // Let the click that follows a drag see `dragged`, then reset it.
                setTimeout(() => { if (!pointers.size) this.dragged = false; }, 0);
            };
            this.svg.addEventListener("pointerup", end);
            this.svg.addEventListener("pointercancel", end);
            this.svg.addEventListener("wheel", (event) => {
                event.preventDefault();
                this.setView({ ...this.view, zoom: this.view.zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1) });
            }, { passive: false });
        }
    }

    window.NetworkGraph = NetworkGraph;
})();
