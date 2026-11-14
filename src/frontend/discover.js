// Discover page (v1.1 spec 4.4 / 4.5): one search for people,
// organizations and events (GET /api/find).
//
// - Type keywords ("surf club") or a sentence ("people into pottery on
//   saturday"); the server reads either and says how it understood it.
// - Filters live in a dropdown behind the funnel button, left of the
//   search box: types, topics and a date, as checkboxes. Nothing ticked
//   means everything. Clear resets them. A badge counts active filters.
// - Ranked by relevance, ten to a page, no infinite scroll. Match scores
//   are never shown (platform admins can turn them on from /admin).
// - Everything lives in the URL (?q=&types=&tags=&date=&page=), so the
//   calendar can link here with a date already set.
//
// Everything from the server is inserted as text (textContent), never as
// HTML, so names or descriptions can't inject markup.

(() => {
    const DATE = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
    const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
    const PART = (options) => new Intl.DateTimeFormat(undefined, options);
    const TYPE_LABEL = { people: "People", orgs: "Organizations", events: "Events" };
    const KIND = { chapter: "Chapter", national: "National organization", council: "Council", club: "Club", institution: "Institution" };
    const SCORES_KEY = "collectivize.admin.scores";

    const $ = (id) => document.getElementById(id);
    const { Api, EventActions, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;

    const showScores = (() => {
        try {
            return localStorage.getItem(SCORES_KEY) === "1";
        } catch {
            return false;
        }
    })();

    class DiscoverPage {
        constructor() {
            this.state = { q: "", types: [], tags: [], date: "", page: 1 };
            this.topics = [];
            this.filtersFromText = false; // were the filters read from the last query?
            this.request = 0; // ignore answers to requests that were superseded
            this.template = $("card-template");
        }

        async start() {
            this.readUrl();
            this.bindControls();
            try {
                const { tags } = await Api.get("/api/tags");
                this.topics = tags;
                this.renderTopicBoxes();
            } catch (error) {
                this.showError(error);
                return;
            }
            this.syncControls();
            this.refresh();
        }

        readUrl() {
            const url = new URLSearchParams(location.search);
            const list = (key) => (url.get(key) || "").split(",").map((v) => v.trim()).filter(Boolean);
            const date = url.get("date") || "";
            this.state = {
                q: url.get("q") || "",
                types: list("types").filter((t) => t in TYPE_LABEL),
                tags: list("tags"),
                date: /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : "",
                page: Math.max(1, parseInt(url.get("page"), 10) || 1),
            };
        }

        writeUrl() {
            const url = new URL(location.href);
            const set = (key, value) => (value ? url.searchParams.set(key, value) : url.searchParams.delete(key));
            set("q", this.state.q);
            set("types", this.state.types.join(","));
            set("tags", this.state.tags.join(","));
            set("date", this.state.date);
            set("page", this.state.page > 1 ? String(this.state.page) : "");
            history.replaceState(null, "", url);
        }

        // --- controls -----------------------------------------------------------------

        bindControls() {
            $("search-form").addEventListener("submit", (event) => {
                event.preventDefault();
                this.closeFilters();
                // Filters read from the previous sentence don't carry over to a new one.
                const reset = this.filtersFromText ? { types: [], tags: [], date: "" } : {};
                this.filtersFromText = false;
                this.update({ ...reset, q: $("search-input").value.trim(), page: 1 });
            });
            $("filter-button").addEventListener("click", () => (this.filtersOpen() ? this.closeFilters() : this.openFilters()));
            $("filter-done").addEventListener("click", () => this.closeFilters(true));
            $("filter-clear").addEventListener("click", () => {
                this.filtersFromText = false;
                this.update({ types: [], tags: [], date: "", page: 1 });
            });
            $("filter-panel").addEventListener("change", () => {
                this.filtersFromText = false; // ticked by hand now: keep them
                this.update({ ...this.readControls(), page: 1 });
            });
            // The dropdown closes on a tap anywhere else, or Escape.
            document.addEventListener("pointerdown", (event) => {
                if (this.filtersOpen() && !event.target.closest(".dc-filter")) this.closeFilters();
            });
            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape" && this.filtersOpen()) this.closeFilters(true);
            });
        }

        filtersOpen() {
            return !$("filter-panel").hidden;
        }

        openFilters() {
            $("filter-panel").hidden = false;
            $("filter-button").setAttribute("aria-expanded", "true");
            $("filter-panel").querySelector("input")?.focus();
        }

        closeFilters(focusButton = false) {
            $("filter-panel").hidden = true;
            $("filter-button").setAttribute("aria-expanded", "false");
            if (focusButton) $("filter-button").focus();
        }

        renderTopicBoxes() {
            $("topic-boxes").replaceChildren(...this.topics.map((tag) => el("label", {}, [
                el("input", { attrs: { type: "checkbox", name: "tag", value: tag.slug } }),
                document.createTextNode(` ${tag.label}`),
            ])));
        }

        readControls() {
            const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((i) => i.value);
            return { types: checked("type"), tags: checked("tag"), date: $("filter-date").value };
        }

        syncControls() {
            $("search-input").value = this.state.q;
            for (const box of document.querySelectorAll('input[name="type"]')) box.checked = this.state.types.includes(box.value);
            for (const box of document.querySelectorAll('input[name="tag"]')) box.checked = this.state.tags.includes(box.value);
            $("filter-date").value = this.state.date;
            const active = this.state.types.length + this.state.tags.length + (this.state.date ? 1 : 0);
            const badge = $("filter-count");
            badge.hidden = active === 0;
            badge.textContent = String(active);
            $("filter-button").setAttribute("aria-label", active ? `Filters (${active} on)` : "Filters");
        }

        update(changes) {
            Object.assign(this.state, changes);
            this.writeUrl();
            this.syncControls();
            this.refresh();
        }

        // --- data ---------------------------------------------------------------------

        async refresh() {
            const id = ++this.request;
            this.showLoading();
            try {
                const data = await Api.get("/api/find", {
                    q: this.state.q, types: this.state.types.join(","), tags: this.state.tags.join(","),
                    date: this.state.date, page: this.state.page,
                });
                if (id !== this.request) return;
                if (data.page !== this.state.page) {
                    this.state.page = data.page;
                    this.writeUrl();
                }
                this.renderUnderstood(data);
                this.renderResults(data.items);
                this.renderPages(data);
                this.setStatus("");
            } catch (error) {
                if (id === this.request) this.showError(error);
            }
        }

        // --- rendering ------------------------------------------------------------------

        showLoading() {
            this.setStatus(this.state.q.split(/\s+/).length >= 4 ? "Working out what you're after…" : "Loading…", { busy: true });
            $("pages").hidden = true;
            const list = $("results");
            list.replaceChildren(...Array.from({ length: 3 }, () => el("li", { className: "dc-card is-skeleton", attrs: { "aria-hidden": "true" } })));
        }

        /** "Searching for: chess · People · Sat, Nov 21", from what the server used. */
        renderUnderstood(data) {
            const line = $("understood");
            const parts = [];
            if (data.understood.keywords.length) parts.push(data.understood.keywords.join(", "));
            const types = data.filters.types.length ? data.filters.types : [];
            parts.push(...types.map((t) => TYPE_LABEL[t]));
            const labels = Object.fromEntries(this.topics.map((t) => [t.slug, t.label]));
            parts.push(...data.filters.tags.map((slug) => labels[slug] || slug));
            if (data.filters.date) parts.push(DATE.format(new Date(`${data.filters.date}T12:00:00`)));
            if (!this.state.q && !parts.length) {
                line.hidden = true;
                return;
            }
            line.hidden = false;
            const fragment = [el("span", { className: "dc-understood-label", text: "Searching for: " }),
                              document.createTextNode(parts.join(" · ") || "everything")];
            if (data.understood.used_ai && data.understood.summary) {
                fragment.push(el("span", { className: "dc-understood-note", text: ` (read as “${data.understood.summary}”)` }));
            } else if (data.understood.note) {
                fragment.push(el("span", { className: "dc-understood-note", text: ` ${data.understood.note}` }));
            }
            line.replaceChildren(...fragment);
            // Filters read from a sentence are shown as ticked, so one tap fixes a wrong guess.
            const read = { types: data.filters.types, tags: data.filters.tags, date: data.filters.date || "" };
            const changed = read.types.join() !== this.state.types.join() || read.tags.join() !== this.state.tags.join()
                || read.date !== this.state.date;
            if (changed) {
                this.filtersFromText = true;
                Object.assign(this.state, read);
                this.writeUrl();
                this.syncControls();
            }
        }

        renderResults(items) {
            const list = $("results");
            if (!items.length) {
                list.replaceChildren(el("li", { className: "dc-empty", text: this.state.q
                    ? "Nothing matches that. Try other words, or clear the filters."
                    : "Nothing matches these filters." }));
                return;
            }
            // What's already in your network comes first (the server sorts it
            // that way), under its own heading, with a divider before the rest.
            const mine = items.filter((item) => item.in_network === true);
            const rest = items.filter((item) => item.in_network !== true);
            const heading = (text, extra = "") => el("li", { className: `dc-divider ${extra}`.trim() },
                [el("h2", { className: "dc-divider-label", text })]);
            list.replaceChildren(
                ...(mine.length ? [heading("In your network"), ...mine.map((item) => this.card(item))] : []),
                ...(mine.length && rest.length ? [heading("Top results", "is-rule")] : []),
                ...rest.map((item) => this.card(item)),
            );
        }

        card(item) {
            const node = item.type === "person" ? this.personCard(item)
                : item.type === "org" ? this.orgCard(item)
                : this.eventCard(item);
            if (showScores && typeof item.score === "number") {
                node.append(el("span", { className: "dc-score", text: `score ${item.score.toFixed(3)}` }));
            }
            return node;
        }

        typeLabel(text, kind) {
            return el("span", { className: `dc-type is-${kind}`, text });
        }

        personCard(person) {
            const actions = [];
            const state = person.connection;
            if (state === "connected") actions.push(el("span", { className: "dc-badge", text: "Connected" }));
            else if (state === "pending_out") actions.push(el("span", { className: "dc-badge", text: "Requested" }));
            else {
                const connect = el("button", { className: "dc-mini-button", text: state === "pending_in" ? "Accept" : "Connect", attrs: { type: "button" } });
                connect.addEventListener("click", async (event) => {
                    event.preventDefault();
                    connect.disabled = true;
                    try {
                        const result = await Api.post("/api/connect", { person_id: person.id });
                        connect.replaceWith(el("span", { className: "dc-badge", text: result.connection === "connected" ? "Connected" : "Requested" }));
                        this.setStatus(result.notice || `Request sent to ${cleanName(person.name)}.`);
                    } catch (error) {
                        connect.disabled = false;
                        this.setStatus(error.message, { error: true });
                    }
                });
                actions.push(connect);
            }
            return el("li", { className: "dc-card dc-entity" }, [
                Ui.avatar(person, "dc-entity-icon is-person"),
                el("div", { className: "dc-body" }, [
                    el("div", { className: "dc-badges" }, [this.typeLabel("Person", "person")]),
                    el("h2", { className: "dc-card-title" }, [el("a", { text: cleanName(person.name), attrs: { href: Ui.profileHref(person.id) } })]),
                    person.bio ? el("p", { className: "dc-meta", text: person.bio }) : null,
                    person.top3_title ? el("p", { className: "dc-host", text: person.top3_title }) : null,
                ]),
                el("div", { className: "dc-side" }, actions),
            ]);
        }

        orgCard(org) {
            const meta = [KIND[org.kind] || org.kind, ...org.tags].join(" · ");
            const badges = [this.typeLabel("Organization", "org")];
            if (org.is_member) badges.push(el("span", { className: "dc-badge", text: "Your org" }));
            return el("li", { className: "dc-card dc-entity" }, [
                el("span", { className: "dc-entity-icon is-org", text: cleanName(org.name).charAt(0), attrs: { "aria-hidden": "true" } }),
                el("div", { className: "dc-body" }, [
                    el("div", { className: "dc-badges" }, badges),
                    el("h2", { className: "dc-card-title" }, [el("a", { text: cleanName(org.name), attrs: { href: Ui.orgHref(org.id) } })]),
                    el("p", { className: "dc-meta", text: meta }),
                ]),
                el("div", { className: "dc-side" }, [
                    el("p", { className: "dc-going" }, [el("strong", { text: String(org.member_count) }), document.createTextNode(` member${org.member_count === 1 ? "" : "s"}`)]),
                ]),
            ]);
        }

        eventCard(item) {
            const node = this.template.content.firstElementChild.cloneNode(true);
            const q = (selector) => node.querySelector(selector);
            const start = new Date(item.starts_at);
            const end = new Date(item.ends_at);
            node.classList.toggle("is-cancelled", item.is_cancelled);

            q(".dc-date-weekday").textContent = PART({ weekday: "short" }).format(start);
            q(".dc-date-day").textContent = PART({ day: "numeric" }).format(start);
            q(".dc-date-month").textContent = PART({ month: "short" }).format(start);

            const reasons = item.reasons[0]?.startsWith("Cancelled") ? item.reasons.slice(1) : item.reasons;
            const trust = reasons[0];
            this.badges(q(".dc-badges"), item, trust);

            q(".dc-card-title").textContent = item.title;
            q(".dc-meta").textContent = [`${DATE.format(start)}, ${TIME.format(start)}–${TIME.format(end)}`, item.location]
                .filter(Boolean).join(" · ");
            q(".dc-host").textContent = `Hosted by ${cleanName(item.host.name)}`;
            q(".dc-description").textContent = item.description;

            const shown = reasons.slice(1).filter(
                (r) => !/friends? going$|away\)?$|^Fits what you asked for|^Not much like|^Hosted by your org/.test(r),
            );
            q(".dc-reasons").replaceChildren(...shown.slice(0, 3).map((reason) => el("li", { text: reason })));
            this.friends(q(".dc-friends"), item.friends_going);
            this.going(q(".dc-going"), item);
            q(".dc-actions-slot").replaceWith(this.actions(item, node));
            return node;
        }

        /** I'm going / Maybe / Save. The card redraws itself from the server's answer. */
        actions(item, node) {
            const controls = new EventActions({
                eventId: item.id,
                myRsvp: item.my_rsvp,
                starred: item.starred,
                cancelled: item.is_cancelled,
                full: item.is_full,
                onChange: (state) => {
                    const updated = { ...item, my_rsvp: state.my_rsvp, starred: state.starred,
                                      going_count: state.going_count, is_full: state.is_full ?? item.is_full };
                    node.replaceWith(this.card(updated));
                    this.setStatus(DiscoverPage.savedText(updated));
                },
                onError: (message) => this.setStatus(message, { error: true }),
            });
            const box = el("div", { className: "dc-action-row" }, [controls.element]);
            if (window.AddToCalendar && !item.is_cancelled) box.append(window.AddToCalendar.menu(item));
            return box;
        }

        static savedText(item) {
            const parts = [];
            if (item.my_rsvp === "going") parts.push("you're going");
            else if (item.my_rsvp === "interested") parts.push("marked maybe");
            if (item.starred) parts.push("saved");
            const what = parts.length ? parts.join(" and ") : "removed from your plans";
            return `“${item.title}”: ${what}. Your calendar is updated.`;
        }

        badges(container, item, trust) {
            const add = (text, kind = "") => container.append(el("span", { className: `dc-badge ${kind}`.trim(), text }));
            container.append(this.typeLabel("Event", "event"));
            if (item.is_cancelled) add("Cancelled", "is-cancelled");
            if (item.is_full && !item.is_cancelled) add("Full", "is-full");
            if (item.my_rsvp === "going") add("You're going");
            else if (item.my_rsvp === "interested") add("Maybe");
            if (item.reasons.some((r) => r.startsWith("Hosted by your org"))) add("Your org");
            if (item.host.kind === "person") add("Hosted by a student", "is-person");
            else if (trust && trust.startsWith("Unclaimed")) add("Unverified listing", "is-unverified");
        }

        /** Friends going are shown without names or initials (Cody's change). */
        friends(container, names) {
            if (!names.length) return;
            container.hidden = false;
            container.querySelector(".dc-friends-text").textContent = "Friends are attending";
        }

        going(element, item) {
            element.replaceChildren();
            if (item.going_count == null) {
                element.textContent = item.is_cancelled ? "" : "Be one of the first";
                return;
            }
            element.append(el("strong", { text: String(item.going_count) }),
                           document.createTextNode(item.capacity ? ` of ${item.capacity} going` : " going"));
        }

        renderPages(data) {
            const nav = $("pages");
            nav.hidden = data.total === 0;
            // The range comes from what this page actually holds.
            const first = (data.page - 1) * data.per_page + 1;
            const last = first + data.items.length - 1;
            $("count").textContent = `Showing ${first}–${last} of ${data.total}`;
            const buttons = [];
            const button = (label, page, { current = false, disabled = false, aria } = {}) => {
                const b = el("button", { className: "dc-page", text: label, attrs: { type: "button", "aria-label": aria } });
                b.disabled = disabled;
                if (current) b.setAttribute("aria-current", "page");
                b.addEventListener("click", () => {
                    this.update({ page });
                    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
                });
                buttons.push(b);
            };
            button("‹", data.page - 1, { disabled: data.page === 1, aria: "Previous page" });
            for (const p of DiscoverPage.pageWindow(data.page, data.pages)) {
                if (p === null) buttons.push(el("span", { className: "dc-page-gap", text: "…", attrs: { "aria-hidden": "true" } }));
                else button(String(p), p, { current: p === data.page, aria: `Page ${p}` });
            }
            button("›", data.page + 1, { disabled: data.page === data.pages, aria: "Next page" });
            $("page-buttons").replaceChildren(...buttons);
        }

        setStatus(text, { busy = false, error = false } = {}) {
            const status = $("status");
            status.textContent = text;
            status.classList.toggle("is-busy", busy);
            status.classList.toggle("is-error", error);
        }

        showError(error) {
            $("results").replaceChildren();
            $("pages").hidden = true;
            this.setStatus(error.message, { error: true });
        }

        /** Page numbers to show: first, last, and two either side of the
         *  current page, with null where pages are skipped. */
        static pageWindow(current, total) {
            const keep = new Set([1, total]);
            for (let p = current - 2; p <= current + 2; p += 1) if (p >= 1 && p <= total) keep.add(p);
            const pages = [...keep].sort((x, y) => x - y);
            return pages.flatMap((p, i) => (i > 0 && p - pages[i - 1] > 1 ? [null, p] : [p]));
        }
    }

    new DiscoverPage().start();
})();
