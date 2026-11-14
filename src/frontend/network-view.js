"use strict";

// The network map: a small fixed-size circle ("bubble") you look through.
//   const bubble = new Collectivize.NetworkBubble({ onOpen })
//   bubble.element          put this on the page (e.g. in a profile section)
//   bubble.draw(map)        lay out and draw the server's map (network-layout.js)
//
// NetworkBubble is the circle plus its hint and key; NetworkView is the
// circle alone.
//
// Whatever fits inside the circle is shown; the rest is cut off at its
// edge. When the network is bigger than the circle you can move around it
// by dragging (mouse or touch) or with the arrow keys, and tabbing to a
// node that is out of sight brings it into view. A network that fits
// doesn't move.
//
// Server text is only ever inserted as text.

(() => {
    const SVG = "http://www.w3.org/2000/svg";
    const RADIUS = 210; // the circle, in map units (the SVG scales to its box)
    const LABEL_ROOM = 52; // a node this close to the edge would lose its label
    const KEY_STEP = 70; // one arrow-key press
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
            this.limit = 0; // how far the offset may go from the middle
            this.drag = null;
            this.justDragged = false;

            this.svg.setAttribute("viewBox", `${-RADIUS} ${-RADIUS} ${RADIUS * 2} ${RADIUS * 2}`);
            this.svg.addEventListener("keydown", (event) => this.onKey(event));
            this.svg.addEventListener("pointerdown", (event) => this.onPointerDown(event));
            this.svg.addEventListener("pointermove", (event) => this.onPointerMove(event));
            for (const type of ["pointerup", "pointercancel"]) {
                this.svg.addEventListener(type, (event) => this.onPointerUp(event));
            }
            this.svg.addEventListener("focusin", (event) => this.reveal(event.target));
        }

        get canMove() {
            return this.limit > 0;
        }

        clear() {
            this.svg.replaceChildren();
            this.limit = 0;
        }

        draw(layout, { isSelf }) {
            this.isSelf = isSelf;
            this.limit = layout.reach + LABEL_ROOM > RADIUS ? layout.reach + LABEL_ROOM - RADIUS * 0.6 : 0;
            this.offset = { x: 0, y: 0 };

            const clip = svg("clipPath", { id: "nw-clip" });
            clip.append(svg("circle", { r: RADIUS - 1 }));
            const fade = svg("radialGradient", { id: "nw-fade" });
            fade.append(svg("stop", { offset: "80%", "stop-opacity": 0 }, "nw-fade-stop"),
                svg("stop", { offset: "100%", "stop-opacity": 0.95 }, "nw-fade-stop"));
            const defs = svg("defs");
            defs.append(clip, fade);

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
            window_.append(svg("circle", { r: RADIUS - 1 }, "nw-backdrop"), this.world);
            // When there is more beyond the edge, fade the rim so it reads as "keeps going".
            if (this.canMove) window_.append(svg("circle", { r: RADIUS - 1, fill: "url(#nw-fade)" }, "nw-rim-fade"));
            this.svg.replaceChildren(defs, window_, svg("circle", { r: RADIUS - 1 }, "nw-rim"));
            this.svg.classList.toggle("can-move", this.canMove);
            this.place(false);
        }

        // --- moving around -------------------------------------------------------

        /** Move the view so map point (x, y) is at the circle's middle. */
        moveTo(x, y, animate = true) {
            const distance = Math.hypot(x, y);
            const scale = distance > this.limit ? this.limit / (distance || 1) : 1; // stay near the network
            this.offset = { x: x * scale, y: y * scale };
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
            return (RADIUS * 2) / (this.svg.getBoundingClientRect().width || RADIUS * 2);
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
            const fromMiddle = Math.hypot(x - this.offset.x, y - this.offset.y);
            if (fromMiddle > RADIUS - LABEL_ROOM - NODE.org) this.moveTo(x, y);
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
            return this.node({
                ...center, label: this.isSelf ? "You" : center.name,
                kind: `is-center ${center.isOrg ? "is-org" : "is-person"}`, radius, order: 0,
                shape: center.isOrg ? NetworkView.square(radius) : svg("circle", { r: radius }, "nw-shape"),
            });
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
            const label = person.isYou ? "You. Back to your profile"
                : `${person.name}, your connection. Open their profile`;
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

    const KEY = [
        ["nw-key is-org", "Your community"],
        ["nw-key", "Your connection"],
        ["nw-key is-line", "Connected"],
        ["nw-key is-line is-near", "Close, not connected"],
    ];

    /** The bubble as one element: circle, "there's more" hint, and key. */
    class NetworkBubble {
        constructor({ onOpen }) {
            const map = document.createElementNS(SVG, "svg");
            map.setAttribute("class", "nw-map");
            map.setAttribute("role", "group");
            map.setAttribute("tabindex", "0");
            this.map = map;

            const hint = el("span", "", "There's more than fits. Drag, or use the arrow keys, to look around.");
            this.recenter = el("button", "nw-link-button", "Back to the middle");
            this.recenter.type = "button";
            this.move = el("p", "nw-move");
            this.move.append(hint, this.recenter);
            this.move.hidden = true;
            this.empty = el("p", "nw-bubble-empty");
            this.empty.hidden = true;

            const circle = el("div", "nw-circle");
            circle.append(map);
            const key = el("ul", "nw-legend");
            key.setAttribute("aria-label", "Key");
            for (const [className, label] of KEY) {
                const item = el("li");
                item.append(el("span", className), label);
                key.append(item);
            }
            this.element = el("div", "nw-bubble");
            this.element.append(circle, this.empty, this.move, key);

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
            this.view.draw(window.Collectivize.NetworkLayout.arrange(map), { isSelf: map.isSelf });
        }
    }

    window.Collectivize = Object.freeze({ ...window.Collectivize, NetworkView, NetworkBubble });
})();
