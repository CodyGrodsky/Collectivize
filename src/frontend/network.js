"use strict";

// Network page (network_plan.md, "How the network view works").
//
// One node sits in the middle with what is directly linked to it around
// it. You start in the middle with your own connections and orgs.
// Clicking a connection puts them in the middle; clicking an org puts it
// in the middle with its members. Only nodes you can open are ever shown:
// you, your connections and your orgs. Anything else is left off the map
// by the server, not hidden by this page.
//
// Lines between people mean one of two things, whoever is in the middle:
//   bold  = a connection both people accepted;
//   light = not connected, but close in the network (they share a
//           connection or a community);
//   none  = no real tie.
// Nodes that share more with the one in the middle sit closer to it, and
// the starting layout is random.
//
// Built to keep working when things go wrong: an answer to an old request
// is ignored, broken entries are skipped, a map you may not open falls
// back to your own with a note, and server text is only inserted as text.

(() => {
    const { Api, Viewer, PersonaPicker, NetworkLayout } = window.Collectivize;

    const SVG = "http://www.w3.org/2000/svg";
    const LABEL_LINE = 18; // characters per line of an org label
    const NODE = { center: 34, org: 27, person: 21 };

    const $ = (id) => document.getElementById(id);

    const plural = (count, word, many = `${word}s`) => `${count} ${count === 1 ? word : many}`;

    const possessive = (name) => (name.endsWith("s") ? `${name}'` : `${name}'s`);

    const firstName = (name) => name.split(" ")[0];

    const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2)
        .map((word) => word[0].toUpperCase()).join("");

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

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

    /** The server's answer, checked and normalized; null if it can't be shown. */
    function readMap(raw) {
        const named = (item) => item && typeof item === "object"
            && typeof item.id === "string" && typeof item.name === "string" && item.name.trim();
        if (!raw || !named(raw.center) || !Array.isArray(raw.orgs) || !Array.isArray(raw.people)) return null;
        const closeness = (item) => (Number.isInteger(item.closeness) && item.closeness > 0 ? item.closeness : 0);
        const orgs = raw.orgs.filter(named).map((org) => ({
            id: org.id, name: org.name, closeness: closeness(org),
        }));
        const people = raw.people.filter(named).map((person) => ({
            id: person.id, name: person.name, isYou: person.is_you === true, closeness: closeness(person),
        }));
        const ids = new Set(people.map((person) => person.id));
        const pairs = (list) => (Array.isArray(list) ? list : []).filter((pair) => Array.isArray(pair)
            && pair.length === 2 && pair[0] !== pair[1] && ids.has(pair[0]) && ids.has(pair[1]));
        return {
            center: { id: raw.center.id, name: raw.center.name, isOrg: raw.center.kind === "org" },
            isSelf: raw.is_self === true,
            orgs,
            people,
            links: pairs(raw.links), // accepted connections between people shown
            near: pairs(raw.near), // not connected, but close in the network
            truncated: raw.truncated === true,
        };
    }

    class NetworkPage {
        constructor() {
            this.map = $("nw-map");
            this.viewer = "";
            this.centerId = NetworkPage.centerFromUrl();
            this.data = null;
            this.request = 0;

            $("nw-home").addEventListener("click", () => this.open(""));
            $("nw-retry").addEventListener("click", () => this.load());
            $("nw-persona").addEventListener("change", (event) => {
                this.viewer = event.target.value;
                Viewer.set(this.viewer);
                this.open("", { replace: true });
            });
            window.addEventListener("popstate", () => {
                this.centerId = NetworkPage.centerFromUrl();
                this.load();
            });
            this.start();
        }

        static centerFromUrl() {
            const value = new URLSearchParams(window.location.search).get("center") || "";
            return /^[\w-]{1,64}$/.test(value) ? value : "";
        }

        async start() {
            try {
                this.viewer = await PersonaPicker.fill($("nw-persona"));
            } catch (error) {
                this.fail(error);
                return;
            }
            this.load();
        }

        /** Put someone at the center ("" = you) and remember it in the URL. */
        open(centerId, { replace = false } = {}) {
            this.setCenter(centerId, replace);
            this.load();
        }

        setCenter(centerId, replace) {
            this.centerId = centerId === this.viewer ? "" : centerId;
            const url = new URL(window.location.href);
            if (this.centerId) url.searchParams.set("center", this.centerId);
            else url.searchParams.delete("center");
            window.history[replace ? "replaceState" : "pushState"]({}, "", url);
        }

        async load() {
            const request = ++this.request;
            $("nw-banner").hidden = true;
            this.note("");
            if (!this.viewer) {
                this.showEmpty("Choose who you are in “Viewing as” to see a network.");
                return;
            }
            this.map.setAttribute("aria-busy", "true");
            try {
                const data = await this.fetchMap();
                if (request !== this.request) return; // a newer request took over
                this.data = data;
                this.render();
            } catch (error) {
                if (request === this.request) this.fail(error);
            } finally {
                if (request === this.request) this.map.removeAttribute("aria-busy");
            }
        }

        async fetchMap() {
            const ask = async (center) => {
                const map = readMap(await Api.get("/api/network", { viewer: this.viewer, center }));
                if (!map) throw new Error("The network didn't load properly.");
                return map;
            };
            if (!this.centerId) return ask("");
            try {
                return await ask(this.centerId);
            } catch (error) {
                const own = await ask(""); // if this fails too, the banner explains
                this.setCenter("", true);
                const why = error.message.replace(/\.$/, "");
                this.note(`${why[0].toUpperCase()}${why.slice(1)}. Showing your own network instead.`);
                return own;
            }
        }

        fail(error) {
            $("nw-banner-text").textContent = error instanceof Error ? error.message : "The network didn't load.";
            $("nw-banner").hidden = false;
        }

        note(text) {
            $("nw-notice").textContent = text;
            $("nw-notice").hidden = !text;
        }

        showEmpty(text) {
            this.data = null;
            this.map.replaceChildren();
            this.map.parentElement.hidden = true;
            $("nw-empty").textContent = text;
            $("nw-empty").hidden = false;
            $("nw-home").hidden = true;
            $("nw-title").textContent = "Network";
            for (const id of ["nw-shared", "nw-people"]) $(id).hidden = true;
            for (const id of ["nw-eyebrow", "nw-panel-title", "nw-summary"]) $(id).textContent = "";
        }

        // --- drawing -------------------------------------------------------------

        render() {
            const { data } = this;
            const who = data.center.isOrg ? data.center.name : firstName(data.center.name);
            $("nw-title").textContent = data.isSelf ? "Your network"
                : data.center.isOrg ? data.center.name : `${possessive(who)} network`;
            $("nw-subtitle").textContent = data.isSelf
                ? "Your connections and communities. Click one to put it in the middle."
                : data.center.isOrg
                    ? "The members of this community you're connected to."
                    : `The connections and communities you and ${who} have in common.`;
            $("nw-home").hidden = data.isSelf;
            $("nw-empty").hidden = true;
            this.map.parentElement.hidden = false;
            this.map.setAttribute("aria-label", data.isSelf
                ? "Map of your connections and communities"
                : `Map of what is directly linked to ${data.center.name}`);
            this.drawMap(NetworkLayout.arrange(data));
            this.drawPanel();
            if (data.truncated) this.note("This is a big network, so only part of it is shown.");
        }

        drawMap(layout) {
            this.map.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
            const links = svg("g", {}, "nw-links");
            for (const link of layout.links) {
                links.append(svg("line", { x1: link.from.x, y1: link.from.y, x2: link.to.x, y2: link.to.y },
                    `nw-link is-${link.kind}`));
            }
            const nodes = svg("g", {}, "nw-nodes");
            layout.orgs.forEach((org, i) => nodes.append(this.orgNode(org, i)));
            layout.people.forEach((person, i) => nodes.append(this.personNode(person, i + layout.orgs.length)));
            nodes.append(this.centerNode(layout.center));
            this.map.replaceChildren(links, nodes);
        }

        /** A positioned node: shape, initials and a label underneath. */
        node({ x, y, name, label, kind, radius, order, shape }) {
            const group = svg("g", { transform: `translate(${x.toFixed(1)} ${y.toFixed(1)})` }, `nw-node ${kind}`);
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
        static pressable(group, label, onPress) {
            group.setAttribute("role", "button");
            group.setAttribute("tabindex", "0");
            group.setAttribute("aria-label", label);
            group.classList.add("is-pressable");
            group.addEventListener("click", onPress);
            group.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                onPress();
            });
        }

        static square(radius) {
            return svg("rect", { x: -radius, y: -radius, width: radius * 2, height: radius * 2, rx: radius / 3 }, "nw-shape");
        }

        centerNode(center) {
            const radius = NODE.center;
            return this.node({
                ...center, label: this.data.isSelf ? "You" : center.name,
                kind: `is-center ${center.isOrg ? "is-org" : "is-person"}`, radius, order: 0,
                shape: center.isOrg ? NetworkPage.square(radius) : svg("circle", { r: radius }, "nw-shape"),
            });
        }

        orgNode(org, order) {
            const radius = NODE.org;
            const group = this.node({
                ...org, label: org.name, kind: "is-org", radius, order, shape: NetworkPage.square(radius),
            });
            NetworkPage.pressable(group, `${org.name}, your community. Show its members`, () => this.open(org.id));
            return group;
        }

        personNode(person, order) {
            const radius = NODE.person;
            const group = this.node({
                ...person, label: person.isYou ? "You" : firstName(person.name),
                kind: `is-person ${person.isYou ? "is-you" : ""}`, radius, order,
                shape: svg("circle", { r: radius }, "nw-shape"),
            });
            const label = person.isYou ? "You. Back to your network"
                : `${person.name}, your connection. Open their network`;
            NetworkPage.pressable(group, label, () => this.open(person.isYou ? "" : person.id));
            return group;
        }

        // --- side panel ----------------------------------------------------------

        drawPanel() {
            const { data } = this;
            const { isOrg } = data.center;
            const who = isOrg ? data.center.name : firstName(data.center.name);
            const others = data.people.filter((p) => !p.isYou);

            $("nw-eyebrow").textContent = data.isSelf ? "You" : isOrg ? "Your community" : "Your connection";
            $("nw-panel-title").textContent = data.center.name;
            $("nw-summary").textContent = data.isSelf
                ? `${plural(others.length, "connection")} and ${plural(data.orgs.length, "community", "communities")}.`
                : isOrg
                    ? `${plural(others.length, "member")} you're connected to.`
                    : NetworkPage.shareSummary(who, data.orgs.length, others.length);

            $("nw-shared-title").textContent = data.isSelf ? "Your communities" : "Communities you share";
            $("nw-shared-list").replaceChildren(...data.orgs.map((org) => this.orgItem(org)));
            $("nw-shared").hidden = data.orgs.length === 0;

            $("nw-people-title").textContent = data.isSelf ? "Your connections"
                : isOrg ? "Members you're connected to" : "Connections you share";
            $("nw-people-hint").textContent = "Open someone to put them in the middle.";
            $("nw-people-list").replaceChildren(...others.map((person) => this.personItem(person)));
            $("nw-people").hidden = others.length === 0;

            if (data.orgs.length === 0 && others.length === 0) {
                $("nw-summary").textContent = data.isSelf
                    ? "No connections or communities yet. They'll appear here as you join orgs and connect with people."
                    : isOrg ? "None of your connections are members yet."
                        : `You and ${who} have no connections or communities in common yet.`;
            }
        }

        static shareSummary(who, orgCount, mutualCount) {
            const parts = [];
            if (orgCount) parts.push(plural(orgCount, "community", "communities"));
            if (mutualCount) parts.push(plural(mutualCount, "connection"));
            return parts.length
                ? `You share ${parts.join(" and ")} with ${who}.`
                : `You and ${who} are connected, with no communities or connections in common yet.`;
        }

        orgItem(org) {
            const item = el("li", "nw-item nw-row");
            const text = el("span", "nw-row-text");
            text.append(el("span", "nw-item-name", org.name), el("span", "nw-item-sub", "You're a member"));
            item.append(el("span", "nw-badge", initials(org.name)), text,
                this.openButton(org.id, `Show the members of ${org.name}`, "Members"));
            return item;
        }

        personItem(person) {
            const item = el("li", "nw-item nw-row");
            const linked = this.data.links.filter((pair) => pair.includes(person.id)).length;
            const text = el("span", "nw-row-text");
            text.append(el("span", "nw-item-name", person.name), el("span", "nw-item-sub",
                linked ? `Connected to ${plural(linked, "other")} here` : "Your connection"));
            // (Light "close" lines are shown on the map only.)
            item.append(el("span", "nw-avatar", initials(person.name)), text,
                this.openButton(person.id, `Open ${possessive(person.name)} network`, "Open"));
            return item;
        }

        openButton(id, label, text) {
            const button = el("button", "nw-open", text);
            button.type = "button";
            button.setAttribute("aria-label", label);
            button.addEventListener("click", () => this.open(id));
            return button;
        }
    }

    new NetworkPage();
})();
