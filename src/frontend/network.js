"use strict";

// Network page (network_plan.md, "How the network view works").
//
// The page shows one person or community at a time: their profile page,
// with their network as a small bubble inside it. You start on yourself.
// Clicking a connection in the bubble opens their profile and bubble;
// clicking a community opens its page (what it is, what it has coming up)
// and its bubble of members. Only nodes you can open are ever in a bubble
// (you, your connections, your communities); the server leaves the rest out.
//
// A person's page is drawn by the same renderer as the Profile tab, in the
// colors and layout they chose. If they left the network section out of
// their layout, the bubble still shows here, under their page.
//
// Built to keep working when things go wrong: an answer to an old request
// is ignored, broken entries are skipped, a page you may not open falls
// back to your own with a note, and server text is only inserted as text.

(() => {
    const { Session, NetworkData, NetworkBubble, Profile } = window.Collectivize;
    const { el, ProfileRenderer } = Profile;

    const DAY_FORMAT = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" });
    const TIME_FORMAT = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

    const $ = (id) => document.getElementById(id);

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
            this.renderer = new ProfileRenderer();
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
            for (const id of ["nw-lists", "nw-home", "nw-edit"]) $(id).hidden = true;
        }

        render() {
            const { data } = this;
            $("nw-empty").hidden = true;
            $("nw-home").hidden = data.isSelf;
            $("nw-edit").hidden = !data.isSelf;
            document.title = `${data.isSelf ? "Your network" : data.center.name} · Collectivize`;
            this.bubble.draw(data);
            const page = data.center.isOrg ? this.orgPage() : this.personPage();
            $("nw-root").replaceChildren(...page);
            this.drawLists();
            if (data.truncated) this.note("This is a big network, so only part of it is shown.");
        }

        // --- a person's page ---------------------------------------------------------

        personPage() {
            const { profile, isSelf } = this.data;
            const surface = this.renderer.render(profile, isSelf ? "self" : "visitor", { network: this.bubble.element });
            if (surface.contains(this.bubble.element)) return [surface];
            // They left the network out of their layout: show it under their page.
            return [surface, this.box("Network", this.bubble.element)];
        }

        box(title, body) {
            return el("section", { className: "nw-box" }, [el("h2", { className: "nw-box-title", text: title }), body]);
        }

        // --- a community's page ------------------------------------------------------

        orgPage() {
            const { profile, center, people } = this.data;
            const members = people.filter((person) => !person.isYou).length;
            const facts = [
                `${plural(members, "member")} you're connected to`,
                profile.upcomingCount ? plural(profile.upcomingCount, "upcoming event") : "No upcoming events",
            ];
            const card = el("section", { className: "nw-org", attrs: { "aria-label": "Community" } }, [
                el("div", { className: "nw-org-mark", text: initials(center.name), attrs: { "aria-hidden": "true" } }),
                el("div", { className: "nw-org-body" }, [
                    el("p", { className: "nw-eyebrow", text: "Your community" }),
                    el("h2", { className: "nw-name", text: center.name }),
                    el("p", { className: "nw-meta", text: `${profile.orgKind} · You're a member` }),
                    el("ul", { className: "nw-facts" }, facts.map((fact) => el("li", { className: "nw-fact", text: fact }))),
                ]),
                profile.website ? el("a", {
                    className: "page-button", text: "Website",
                    attrs: { href: profile.website, target: "_blank", rel: "noopener noreferrer" },
                }) : null,
            ]);
            const upcoming = profile.upcoming.length
                ? this.box("Coming up", el("ul", { className: "nw-upcoming-list" }, profile.upcoming.map(NetworkPage.eventItem)))
                : null;
            return [card, el("div", { className: "nw-org-grid" }, [upcoming, this.box("Network", this.bubble.element)])];
        }

        static eventItem(event) {
            const day = new Date(event.starts_at);
            const pad = (n) => String(n).padStart(2, "0");
            const date = `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
            return el("li", {}, [el("a", { className: "nw-event-link", attrs: { href: `calendar.html?date=${date}` } }, [
                el("span", { className: "nw-event-when", text: `${DAY_FORMAT.format(day)} · ${TIME_FORMAT.format(day)}` }),
                el("span", { className: "nw-event-title", text: event.title }),
                event.location ? el("span", { className: "nw-event-where", text: event.location }) : null,
            ])]);
        }

        // --- the lists under the page ------------------------------------------------

        drawLists() {
            const { data } = this;
            const others = data.people.filter((p) => !p.isYou);

            $("nw-shared-title").textContent = data.isSelf ? "Your communities" : "Communities you share";
            $("nw-shared-list").replaceChildren(...data.orgs.map((org) => this.row(
                "nw-badge", org, "You're a member", `Open the page of ${org.name}`)));
            $("nw-shared").hidden = data.orgs.length === 0;

            $("nw-people-title").textContent = data.isSelf ? "Your connections"
                : data.center.isOrg ? "Members you're connected to" : "Connections you share";
            $("nw-people-list").replaceChildren(...others.map((person) => {
                const linked = data.links.filter((pair) => pair.includes(person.id)).length;
                return this.row("nw-avatar", person,
                    linked ? `Connected to ${plural(linked, "other")} here` : "Your connection",
                    `Open ${possessive(person.name)} page`);
            }));
            $("nw-people").hidden = others.length === 0;
            $("nw-lists").hidden = data.orgs.length === 0 && others.length === 0;
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
