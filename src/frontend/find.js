"use strict";

// Name search for the Discover search bar (load after shared.js):
//   const names = new Collectivize.NameSearch({ input, panel, getViewer })
//   names.refresh()   run again (e.g. after the viewer changes or the box is cleared)
//
// As you type (two characters or more) it lists people, organizations and
// events whose names match, in a panel under the search bar. It is a plain
// name lookup: instant and free, unlike the AI search that runs when you
// press Search. People and organizations open their page on the Network
// tab; events open their day on the Calendar.
//
// An answer to an older keystroke is ignored, broken entries are skipped,
// and server text is only ever inserted as text.

(() => {
    const { Api } = window.Collectivize;

    const MIN_LENGTH = 2;
    const MAX_LENGTH = 100;
    const TYPING_PAUSE_MS = 220;
    const WHEN = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const ORG_KINDS = {
        chapter: "Chapter", national: "National organization", council: "Council",
        club: "Club", institution: "Institution",
    };

    const text = (value) => (typeof value === "string" ? value.trim() : "");

    const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2)
        .map((word) => word[0].toUpperCase()).join("");

    const capital = (word) => (word ? `${word[0].toUpperCase()}${word.slice(1)}` : "");

    function el(tag, className, content) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (content !== undefined) node.textContent = content;
        return node;
    }

    /** One kind's results from the server, checked: { items, total }. */
    function readGroup(raw, keep) {
        const items = (Array.isArray(raw?.items) ? raw.items : [])
            .filter((item) => item && typeof item === "object" && text(item.id) && keep(item));
        return { items, total: Number.isInteger(raw?.total) ? Math.max(raw.total, items.length) : items.length };
    }

    class NameSearch {
        constructor({ input, panel, getViewer }) {
            this.input = input;
            this.panel = panel;
            this.getViewer = getViewer;
            this.request = 0;
            this.timer = null;
            input.addEventListener("input", () => this.schedule());
        }

        get query() {
            return this.input.value.trim().replace(/\s+/g, " ").slice(0, MAX_LENGTH);
        }

        schedule() {
            clearTimeout(this.timer);
            this.timer = setTimeout(() => this.refresh(), TYPING_PAUSE_MS);
        }

        async refresh() {
            clearTimeout(this.timer);
            const request = ++this.request;
            const { query } = this;
            if (query.length < MIN_LENGTH) {
                this.panel.hidden = true;
                this.panel.replaceChildren();
                return;
            }
            try {
                const data = await Api.get("/api/find", { viewer: this.getViewer(), q: query });
                if (request === this.request) this.render(query, data);
            } catch (error) {
                if (request === this.request) this.showMessage(error instanceof Error ? error.message : "Name search didn't work.");
            }
        }

        showMessage(message) {
            this.panel.replaceChildren(el("p", "find-empty", message));
            this.panel.hidden = false;
        }

        render(query, data) {
            const people = readGroup(data?.people, (p) => text(p.name));
            const orgs = readGroup(data?.orgs, (o) => text(o.name));
            const events = readGroup(data?.events, (e) => text(e.title) && !Number.isNaN(new Date(e.starts_at).getTime()));
            if (people.total + orgs.total + events.total === 0) {
                const who = this.getViewer() ? "people, organizations or events" : "organizations or events";
                this.showMessage(`No ${who} are named “${query}”. Press Search to look for events by description instead.`);
                return;
            }
            const groups = [
                this.group("People", people, (person) => this.row({
                    href: `network.html?center=${encodeURIComponent(person.id)}`,
                    mark: el("span", "find-mark is-person", initials(text(person.name))),
                    name: text(person.name),
                    about: [capital(text(person.role)), person.connected === true ? "Connected" : "", text(person.in_common)]
                        .filter(Boolean).join(" · "),
                })),
                this.group("Organizations", orgs, (org) => this.row({
                    href: `network.html?center=${encodeURIComponent(org.id)}`,
                    mark: el("span", "find-mark is-org", initials(text(org.name))),
                    name: text(org.name),
                    about: [ORG_KINDS[org.kind] || "Community", org.is_member === true ? "You're a member" : ""]
                        .filter(Boolean).join(" · "),
                })),
                this.group("Events", events, (event) => {
                    const start = new Date(event.starts_at);
                    const pad = (n) => String(n).padStart(2, "0");
                    const day = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}`;
                    return this.row({
                        href: `calendar.html?date=${day}`,
                        mark: el("span", "find-mark is-event", String(start.getDate())),
                        name: text(event.title),
                        about: [WHEN.format(start), text(event.host), event.is_cancelled === true ? "Cancelled" : ""]
                            .filter(Boolean).join(" · "),
                    });
                }),
            ].filter(Boolean);
            const heading = el("h2", "find-title", `Names matching “${query}”`);
            const columns = el("div", "find-groups");
            columns.append(...groups);
            this.panel.replaceChildren(heading, columns);
            this.panel.hidden = false;
        }

        group(title, { items, total }, toRow) {
            if (total === 0) return null;
            const section = el("section", "find-group");
            const heading = el("h3", "find-group-title", title);
            heading.append(el("span", "find-count", String(total)));
            const list = el("ul", "find-list");
            list.append(...items.map(toRow));
            section.append(heading, list);
            if (total > items.length) {
                section.append(el("p", "find-more", `+${total - items.length} more. Type more of the name to narrow it down.`));
            }
            return section;
        }

        row({ href, mark, name, about }) {
            const link = el("a", "find-row");
            link.href = href;
            const words = el("span", "find-row-text");
            words.append(el("span", "find-name", name));
            if (about) words.append(el("span", "find-about", about));
            link.append(mark, words);
            const item = el("li");
            item.append(link);
            return item;
        }
    }

    window.Collectivize = Object.freeze({ ...window.Collectivize, NameSearch });
})();
