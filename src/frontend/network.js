"use strict";

// Network page (network_plan.md, "How the network view works").
//
// The network has this page to itself; profiles live on the Profile tab.
// The bubble fills the page, centred on one person or community at a time.
// You start on yourself. Clicking a connection in the bubble puts their
// network in the middle; clicking a community shows its members. Only
// nodes you can open are ever in a bubble (you, your connections, your
// communities); the server leaves the rest out.
//
// A slim header names who is in the middle and links to their profile (or
// the community's page). Someone you aren't connected to, or a community
// you aren't in: their network stays closed, and a short note says so.
//
// Built to keep working when things go wrong: an answer to an old request
// is ignored, broken entries are skipped, a page you may not open falls
// back to your own with a note, and server text is only inserted as text.

(() => {
    const { Session, NetworkData, NetworkBubble, Profile, Ui } = window.Collectivize;
    const { el } = Profile;

    const $ = (id) => document.getElementById(id);

    const TOP = 3; // list entries shown before "More"

    const plural = (count, word, many = `${word}s`) => `${count} ${count === 1 ? word : many}`;

    const possessive = (name) => (name.endsWith("s") ? `${name}'` : `${name}'s`);

    const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2)
        .map((word) => word[0].toUpperCase()).join("");

    class NetworkPage {
        constructor() {
            this.viewer = "";
            this.centerId = NetworkPage.centerFromUrl();
            this.data = null;
            this.request = 0;
            this.bubble = new NetworkBubble({ onOpen: (id) => this.open(id) });

            $("nw-home").addEventListener("click", () => this.open(""));
            $("nw-retry").addEventListener("click", () => this.load());
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
                this.viewer = (await Session.current()).id; // whoever is signed in
                window.ConnectionsPanel?.mount($("nw-connections"), { onChange: () => this.load() });
            } catch (error) {
                this.fail(error);
                return;
            }
            this.load();
        }

        /** Put someone or some community in the middle ("" = you) and remember it in the URL. */
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
                this.showEmpty("Sign in to see your network.");
                return;
            }
            $("nw-root").setAttribute("aria-busy", "true");
            try {
                const data = await this.fetchPage();
                if (request !== this.request) return; // a newer request took over
                this.data = data;
                this.render();
            } catch (error) {
                if (request === this.request) this.fail(error);
            } finally {
                if (request === this.request) $("nw-root").removeAttribute("aria-busy");
            }
        }

        async fetchPage() {
            if (!this.centerId) return NetworkData.fetch(this.viewer, "");
            try {
                return await NetworkData.fetch(this.viewer, this.centerId);
            } catch (error) {
                const own = await NetworkData.fetch(this.viewer, ""); // if this fails too, the banner explains
                this.setCenter("", true);
                const why = error.message.replace(/\.$/, "");
                this.note(`${why[0].toUpperCase()}${why.slice(1)}. Showing your own page instead.`);
                return own;
            }
        }

        fail(error) {
            $("nw-banner-text").textContent = error instanceof Error ? error.message : "The page didn't load.";
            $("nw-banner").hidden = false;
        }

        note(message) {
            $("nw-notice").textContent = message;
            $("nw-notice").hidden = !message;
        }

        showEmpty(message) {
            this.data = null;
            $("nw-root").replaceChildren();
            $("nw-empty").textContent = message;
            $("nw-empty").hidden = false;
            for (const id of ["nw-lists", "nw-home"]) $(id).hidden = true;
        }

        render() {
            const { data } = this;
            $("nw-empty").hidden = true;
            $("nw-home").hidden = data.isSelf;
            document.title = `${data.isSelf ? "My network" : data.center.name} · Collectivize`;
            if (data.networkOpen) this.bubble.draw(data);
            $("nw-root").replaceChildren(this.stage());
            this.drawLists();
            if (data.truncated) this.note("This is a big network, so only part of it is shown.");
        }

        // --- the network, filling the page --------------------------------------------

        stage() {
            const { center, isSelf, networkOpen } = this.data;
            const title = isSelf ? "My network"
                : center.isOrg ? `${center.name}: members you know` : `${possessive(center.name)} network`;
            const link = center.isOrg
                ? el("a", { className: "page-button", text: "Open community page", attrs: { href: Ui.orgHref(center.id) } })
                : el("a", { className: "page-button", text: isSelf ? "My profile" : `${possessive(center.name.split(" ")[0])} profile`,
                            attrs: { href: isSelf ? "profile.html" : Ui.profileHref(center.id) } });
            const head = el("div", { className: "nw-stage-head" }, [
                el("h2", { className: "nw-stage-title", text: title }),
                link,
            ]);
            const body = networkOpen ? this.bubble.element : el("p", {
                className: "nw-closed",
                text: center.isOrg
                    ? "You aren't a member, so its network of members isn't shown."
                    : `You and ${center.name.split(" ")[0]} aren't connected, so their network isn't shown.`,
            });
            return el("section", { className: "nw-stage", attrs: { "aria-label": title } }, [head, body]);
        }

        // --- the lists under the page ------------------------------------------------

        drawLists() {
            const { data } = this;
            const others = data.people.filter((p) => !p.isYou);

            $("nw-shared-title").textContent = data.isSelf ? "My communities" : "Communities you share";
            this.topThree("nw-shared-list", NetworkPage.closestFirst(data.orgs), (org) => this.row(
                "nw-badge", org, "You're a member", `Open the page of ${org.name}`));
            $("nw-shared").hidden = data.orgs.length === 0;

            $("nw-people-title").textContent = data.isSelf ? "My connections"
                : data.center.isOrg ? "Members you're connected to" : "Connections you share";
            this.topThree("nw-people-list", NetworkPage.closestFirst(others), (person) => {
                const linked = data.links.filter((pair) => pair.includes(person.id)).length;
                return this.row("nw-avatar", person,
                    linked ? `Connected to ${plural(linked, "other")} here` : "Your connection",
                    `Open ${possessive(person.name)} page`);
            });
            $("nw-people").hidden = others.length === 0;
            $("nw-lists").hidden = data.orgs.length === 0 && others.length === 0;
        }

        /** The top three, then "More" to show the rest (and "Less" to fold them again). */
        topThree(listId, items, toRow) {
            const list = $(listId);
            const rows = items.map(toRow);
            rows.slice(TOP).forEach((row) => { row.hidden = true; });
            list.replaceChildren(...rows);
            list.parentElement.querySelector(".nw-more")?.remove();
            if (rows.length <= TOP) return;
            const more = el("button", { className: "nw-link-button nw-more", text: "More",
                                        attrs: { type: "button", "aria-expanded": "false", "aria-controls": listId } });
            more.addEventListener("click", () => {
                const open = more.getAttribute("aria-expanded") !== "true";
                rows.slice(TOP).forEach((row) => { row.hidden = !open; });
                more.setAttribute("aria-expanded", String(open));
                more.textContent = open ? "Less" : "More";
            });
            list.after(more);
        }

        /** Closest first (the map's own closeness score), keeping the server's order on ties. */
        static closestFirst(items) {
            return items.map((item, index) => ({ item, index }))
                .sort((a, b) => (b.item.closeness ?? 0) - (a.item.closeness ?? 0) || a.index - b.index)
                .map(({ item }) => item);
        }

        row(markClass, node, about, label) {
            const open = el("button", { className: "nw-open", text: "Open", attrs: { type: "button", "aria-label": label } });
            open.addEventListener("click", () => {
                this.open(node.id);
                window.scrollTo({ top: 0 });
            });
            return el("li", { className: "nw-item nw-row" }, [
                el("span", { className: markClass, text: initials(node.name) }),
                el("span", { className: "nw-row-text" }, [
                    el("span", { className: "nw-item-name", text: node.name }),
                    el("span", { className: "nw-item-sub", text: about }),
                ]),
                open,
            ]);
        }
    }

    new NetworkPage();
})();
