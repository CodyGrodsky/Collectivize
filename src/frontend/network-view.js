"use strict";

// The network map: a window onto the network that fills its box.
//   const bubble = new Collectivize.NetworkBubble({ onOpen })
//   bubble.element          put this on the page (the Network tab)
//   bubble.draw(map)        lay out and draw the server's map (network-layout.js)
//
// NetworkBubble is the map plus its "Back to the middle" button;
// NetworkView is the map alone.
//
// Whatever fits inside the box is shown; the rest is cut off at its edge.
// Every network (yours or a friend's) can be moved around by dragging
// (mouse or touch) or with the arrow keys, and tabbing to a node that is
// out of sight brings it into view.
//
// Server text is only ever inserted as text.

(() => {
    const SVG = "http://www.w3.org/2000/svg";
    const RADIUS = 210; // half the box's shorter side, in map units (the SVG scales to its box)
    const LABEL_ROOM = 52; // a node this close to the edge would lose its label
    const EDGE = 12; // breathing room between a node (or its label) and the box's edge
    const KEY_STEP = 70; // one arrow-key press
    const MIN_PAN = RADIUS * 0.6; // how far any network can be dragged from the middle
    const DRAG_START = 6; // pointer travel (px) before a press counts as a drag
    const NODE = { center: 34, org: 27, person: 21 };
    const LABEL_LINE = 18; // characters per line of a label
    const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

    const firstName = (name) => name.split(" ")[0];

    const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2)
        .map((word) => word[0].toUpperCase()).join("");

    function svg(tag, attributes = {}, className) {
        const node = document.createElementNS(SVG, tag);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
        if (className) node.setAttribute("class", className);
        return node;
    }

    /** Up to two short lines for a label under a node. */
    let measureContext = null;

    /** Width of a line of label text, in map units (label font sizes are map units). */
    function textWidth(text, size, weight) {
        measureContext ||= document.createElement("canvas").getContext("2d");
        const family = getComputedStyle(document.body).fontFamily || "sans-serif";
        measureContext.font = `${weight} ${size}px ${family}`;
        return measureContext.measureText(text).width;
    }

    function wrapLabel(text) {
        if (text.length <= LABEL_LINE) return [text];
        const cut = text.lastIndexOf(" ", LABEL_LINE);
        const first = text.slice(0, cut > 4 ? cut : LABEL_LINE).trim();
        const rest = text.slice(first.length).trim();
        return [first, rest.length > LABEL_LINE ? `${rest.slice(0, LABEL_LINE - 1).trim()}…` : rest];
    }

    class NetworkView {
        constructor(element, { onOpen, onMove }) {
            this.svg = element;
            this.onOpen = onOpen;
            this.onMove = onMove || (() => {});
            this.offset = { x: 0, y: 0 }; // which map point is at the circle's middle
            this.bounds = null; // the area every node and label takes up, in map units
            this.range = { x: [0, 0], y: [0, 0] }; // where the offset may go, per axis
            this.drag = null;
            this.justDragged = false;

            // The map fills its box, whatever its shape: the shorter side is
            // 2 * RADIUS map units, the longer one grows with the box.
            this.half = { x: RADIUS, y: RADIUS };
            this.fit();
            new ResizeObserver(() => this.fit()).observe(this.svg);
            this.svg.addEventListener("keydown", (event) => this.onKey(event));
            this.svg.addEventListener("pointerdown", (event) => this.onPointerDown(event));
            this.svg.addEventListener("pointermove", (event) => this.onPointerMove(event));
            for (const type of ["pointerup", "pointercancel"]) {
                this.svg.addEventListener(type, (event) => this.onPointerUp(event));
            }
            this.svg.addEventListener("focusin", (event) => this.reveal(event.target));
        }

        /** Match the map's coordinates to the box's shape (no stretching). */
        fit() {
            const box = this.svg.getBoundingClientRect();
            const ratio = box.width && box.height ? box.width / box.height : 1;
            this.half = ratio >= 1 ? { x: RADIUS * ratio, y: RADIUS } : { x: RADIUS, y: RADIUS / ratio };
            const { x, y } = this.half;
            this.svg.setAttribute("viewBox", `${-x} ${-y} ${x * 2} ${y * 2}`);
            this.clipRect?.setAttribute("x", -x);
            this.clipRect?.setAttribute("y", -y);
            this.clipRect?.setAttribute("width", x * 2);
            this.clipRect?.setAttribute("height", y * 2);
            // A new box shape changes how far the view can move.
            this.updateRange();
            if (this.world) this.moveTo(this.offset.x, this.offset.y, false);
        }

        get canMove() {
            return this.range.x[0] < this.range.x[1] || this.range.y[0] < this.range.y[1];
        }

        clear() {
            this.svg.replaceChildren();
            this.bounds = null;
            this.range = { x: [0, 0], y: [0, 0] };
        }

        /** How far the view may move on each axis: at least MIN_PAN either
         *  way, and further if needed to bring every node and label fully
         *  into the box. */
        updateRange() {
            const b = this.bounds;
            const axis = (low, high, half) => [Math.min(-MIN_PAN, low + half), Math.max(MIN_PAN, high - half)];
            this.range = b ? { x: axis(b.left, b.right, this.half.x), y: axis(b.top, b.bottom, this.half.y) }
                : { x: [0, 0], y: [0, 0] };
            this.svg.classList.toggle("can-move", this.canMove);
        }

        /** The box around every node and its (up to two-line) label. */
        static bounds(layout) {
            const parts = [
                { x: layout.center.x, y: layout.center.y, r: NODE.center },
                ...layout.orgs.map((o) => ({ x: o.x, y: o.y, r: NODE.org })),
                ...layout.people.map((p) => ({ x: p.x, y: p.y, r: NODE.person })),
            ];
            const half = 60; // half a label's width
            return {
                left: Math.min(...parts.map((p) => p.x - Math.max(p.r, half))) - EDGE,
                right: Math.max(...parts.map((p) => p.x + Math.max(p.r, half))) + EDGE,
                top: Math.min(...parts.map((p) => p.y - p.r)) - EDGE,
                bottom: Math.max(...parts.map((p) => p.y + p.r + 17 + 15 + 4)) + EDGE, // label below
            };
        }

        draw(layout, { isSelf }) {
            this.isSelf = isSelf;
            // Every network can be dragged around, even one that fits the box
            // (a friend's small network moves just like your own big one);
            // a big one can be dragged until every node and label is in view.
            this.bounds = NetworkView.bounds(layout);
            this.offset = { x: 0, y: 0 };

            // No circle: the map is clipped to its whole box.
            const clip = svg("clipPath", { id: "nw-clip" });
            this.clipRect = svg("rect", {});
            clip.append(this.clipRect);
            const defs = svg("defs");
            defs.append(clip);

            const links = svg("g", {}, "nw-links");
            for (const link of layout.links) {
                links.append(svg("line", { x1: link.from.x, y1: link.from.y, x2: link.to.x, y2: link.to.y },
                    `nw-link is-${link.kind}`));
            }
            const nodes = svg("g", {}, "nw-nodes");
            layout.orgs.forEach((org, i) => nodes.append(this.orgNode(org, i)));
            layout.people.forEach((person, i) => nodes.append(this.personNode(person, i + layout.orgs.length)));
            nodes.append(this.centerNode(layout.center));

            this.world = svg("g", {}, "nw-world");
            this.world.append(links, nodes);
            const window_ = svg("g", { "clip-path": "url(#nw-clip)" });
            window_.append(this.world);
            this.svg.replaceChildren(defs, window_);
            this.fit();
            this.place(false);
        }

        // --- moving around -------------------------------------------------------

        /** Move the view so map point (x, y) is at the circle's middle. */
        moveTo(x, y, animate = true) {
            const keep = (value, [min, max]) => Math.min(max, Math.max(min, value)); // stay on the network
            this.offset = { x: keep(x, this.range.x), y: keep(y, this.range.y) };
            this.place(animate);
        }

        recenter() {
            this.moveTo(0, 0);
        }

        place(animate) {
            this.world.classList.toggle("is-gliding", animate);
            this.world.style.transform = `translate(${(-this.offset.x).toFixed(1)}px, ${(-this.offset.y).toFixed(1)}px)`;
            this.onMove({ canMove: this.canMove, moved: Math.hypot(this.offset.x, this.offset.y) > 1 });
        }

        onKey(event) {
            const direction = ARROWS[event.key];
            if (!direction || !this.canMove) return;
            event.preventDefault(); // don't scroll the page
            this.moveTo(this.offset.x + direction[0] * KEY_STEP, this.offset.y + direction[1] * KEY_STEP);
        }

        /** Map units per screen pixel (the SVG is scaled to fit its box). */
        get unitsPerPixel() {
            return (this.half.x * 2) / (this.svg.getBoundingClientRect().width || this.half.x * 2);
        }

        onPointerDown(event) {
            if (!this.canMove || (event.pointerType === "mouse" && event.button !== 0)) return;
            this.drag = { id: event.pointerId, x: event.clientX, y: event.clientY, from: { ...this.offset }, moved: false };
        }

        onPointerMove(event) {
            const { drag } = this;
            if (!drag || event.pointerId !== drag.id) return;
            const dx = event.clientX - drag.x;
            const dy = event.clientY - drag.y;
            if (!drag.moved) {
                if (Math.hypot(dx, dy) < DRAG_START) return;
                drag.moved = true;
                try {
                    this.svg.setPointerCapture(drag.id); // keep the drag if the pointer leaves the circle
                } catch {
                    // Not capturable (rare): the drag still works while over the map.
                }
                this.svg.classList.add("is-dragging");
            }
            const scale = this.unitsPerPixel;
            this.moveTo(drag.from.x - dx * scale, drag.from.y - dy * scale, false);
        }

        onPointerUp(event) {
            const { drag } = this;
            if (!drag || event.pointerId !== drag.id) return;
            this.drag = null;
            this.svg.classList.remove("is-dragging");
            if (!drag.moved) return;
            // The press that ends a drag must not also open the node under it.
            this.justDragged = true;
            setTimeout(() => { this.justDragged = false; }, 0);
        }

        /** Bring a node that got keyboard focus into view. */
        reveal(target) {
            const node = target.closest?.(".nw-node");
            if (!node || !this.canMove) return;
            const x = Number(node.dataset.x);
            const y = Number(node.dataset.y);
            const outX = Math.abs(x - this.offset.x) > this.half.x - LABEL_ROOM;
            const outY = Math.abs(y - this.offset.y) > this.half.y - LABEL_ROOM - NODE.org;
            if (outX || outY) this.moveTo(x, y);
        }

        // --- nodes ---------------------------------------------------------------

        /** A positioned node: shape, initials and a label underneath. */
        node({ x, y, name, label, kind, radius, order, shape }) {
            const group = svg("g", { transform: `translate(${x.toFixed(1)} ${y.toFixed(1)})` }, `nw-node ${kind}`);
            group.dataset.x = x.toFixed(1);
            group.dataset.y = y.toFixed(1);
            const inner = svg("g", { style: `--order: ${Math.min(order, 30)}` }, "nw-node-inner");
            const title = svg("title");
            title.textContent = name;
            const mark = svg("text", { y: 1, "aria-hidden": "true" }, "nw-initials");
            mark.textContent = initials(name);
            const caption = svg("text", { y: radius + 17 }, "nw-label");
            wrapLabel(label).forEach((line, i) => {
                const span = svg("tspan", { x: 0, dy: i === 0 ? 0 : 15 });
                span.textContent = line;
                caption.append(span);
            });
            inner.append(title, shape, mark, caption);
            group.append(inner);
            return group;
        }

        /**
         * The room a node needs on the map: its shape plus the name under it
         * (the same text, sizes and line breaks `node()` draws), so the layout
         * can keep every name clear of other nodes and names.
         */
        static footprint(node, isCenter, isSelf) {
            const radius = isCenter ? NODE.center : node.isOrg ? NODE.org : NODE.person;
            const label = isCenter ? (isSelf ? "You" : node.name)
                : node.isOrg ? node.name : node.isYou ? "You" : firstName(node.name);
            const size = isCenter ? 14.5 : 13; // as in network.css
            const weight = isCenter ? 650 : node.isOrg || node.isYou ? 600 : 400;
            const lines = wrapLabel(label);
            const halo = 4; // the label's background stroke
            const width = Math.max(...lines.map((line) => textWidth(line, size, weight))) + halo;
            return {
                half: Math.max(radius, width / 2),
                up: radius,
                down: radius + 17 + (lines.length - 1) * 15 + 5 + halo / 2,
            };
        }

        /** Make a node act as a button for mouse, touch and keyboard. */
        pressable(group, label, id) {
            const press = () => {
                if (!this.justDragged) this.onOpen(id);
            };
            group.setAttribute("role", "button");
            group.setAttribute("tabindex", "0");
            group.setAttribute("aria-label", label);
            group.classList.add("is-pressable");
            group.addEventListener("click", press);
            group.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                press();
            });
        }

        static square(radius) {
            return svg("rect", { x: -radius, y: -radius, width: radius * 2, height: radius * 2, rx: radius / 3 }, "nw-shape");
        }

        centerNode(center) {
            const radius = NODE.center;
            const group = this.node({
                ...center, label: this.isSelf ? "You" : center.name,
                kind: `is-center ${center.isOrg ? "is-org" : "is-person"}`, radius, order: 0,
                shape: center.isOrg ? NetworkView.square(radius) : svg("circle", { r: radius }, "nw-shape"),
            });
            // You, in the middle of your own network: opens your profile.
            if (this.isSelf) this.pressable(group, "You. Open your profile", "");
            return group;
        }

        orgNode(org, order) {
            const radius = NODE.org;
            const group = this.node({
                ...org, label: org.name, kind: "is-org", radius, order, shape: NetworkView.square(radius),
            });
            this.pressable(group, `${org.name}, your community. Open its page`, org.id);
            return group;
        }

        personNode(person, order) {
            const radius = NODE.person;
            const group = this.node({
                ...person, label: person.isYou ? "You" : firstName(person.name),
                kind: `is-person ${person.isYou ? "is-you" : ""}`, radius, order,
                shape: svg("circle", { r: radius }, "nw-shape"),
            });
            // "You" on someone else's map: back to your own network.
            const label = person.isYou ? "You. Back to your own network"
                : `${person.name}, your connection. Open their network`;
            this.pressable(group, label, person.isYou ? "" : person.id);
            return group;
        }
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    /** The network as one element: the map and a "Back to the middle" button (no key). */
    class NetworkBubble {
        constructor({ onOpen }) {
            const map = document.createElementNS(SVG, "svg");
            map.setAttribute("class", "nw-map");
            map.setAttribute("role", "group");
            map.setAttribute("tabindex", "0");
            this.map = map;

            this.recenter = el("button", "nw-link-button", "Back to the middle");
            this.recenter.type = "button";
            this.move = el("p", "nw-move");
            this.move.append(this.recenter);
            this.move.hidden = true;
            this.empty = el("p", "nw-bubble-empty");
            this.empty.hidden = true;

            const circle = el("div", "nw-circle");
            circle.append(map);
            this.element = el("div", "nw-bubble");
            this.element.append(circle, this.empty, this.move);

            this.view = new NetworkView(map, {
                onOpen,
                onMove: ({ canMove, moved }) => {
                    this.move.hidden = !canMove;
                    this.recenter.hidden = !moved;
                },
            });
            this.recenter.addEventListener("click", () => this.view.recenter());
        }

        /** Draw a map as read by the page (center, orgs, people, links, near, isSelf). */
        draw(map) {
            const alone = map.orgs.length === 0 && map.people.length === 0;
            this.empty.hidden = !alone;
            this.empty.textContent = map.isSelf
                ? "No connections or communities yet."
                : "Nothing you both share yet.";
            this.map.setAttribute("aria-label", map.isSelf
                ? "Map of your connections and communities"
                : `Map of your connections and communities linked to ${map.center.name}`);
            const footprint = (node, isCenter) => NetworkView.footprint(node, isCenter, map.isSelf);
            this.view.draw(window.Collectivize.NetworkLayout.arrange(map, Math.random, footprint), { isSelf: map.isSelf });
        }
    }

    window.Collectivize = Object.freeze({ ...window.Collectivize, NetworkView, NetworkBubble });
})();
