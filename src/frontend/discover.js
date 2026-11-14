// Discover page: talks to the local demo server's JSON API (src/backend/server.py).
//
// Browse mode: GET /api/discover, ranked for the person you're viewing as,
// in numbered pages of 10. Search mode: POST /api/search runs the search
// agent on what you typed and shows its picks, its understanding of your
// goal, and the steps it took.
//
// Everything from the server is inserted as text (textContent), never as
// HTML, so event titles or descriptions can't inject markup.

(() => {
    const STORE_KEY = "collectivize.discover";
    const DATE = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
    const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
    const PART = (options) => new Intl.DateTimeFormat(undefined, options);

    const $ = (id) => document.getElementById(id);

    const { Api, Viewer, PersonaPicker, EventActions } = window.Collectivize;

    class Settings {
        static load() {
            try {
                return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
            } catch {
                return {};
            }
        }

        static save(values) {
            try {
                localStorage.setItem(STORE_KEY, JSON.stringify(values));
            } catch {
                // Private mode or storage blocked: settings just won't persist.
            }
        }
    }

    class DiscoverPage {
        constructor() {
            this.state = { viewer: "", near: true, page: 1, tag: "", query: "" };
            this.request = 0; // ignore answers to requests that were superseded
            this.template = $("card-template");
        }

        async start() {
            const saved = Settings.load();
            const url = new URLSearchParams(location.search);
            this.state.near = saved.near ?? true;
            this.state.query = url.get("q") || "";
            this.state.page = Math.max(1, parseInt(url.get("page"), 10) || 1);
            this.bindControls();
            try {
                const [viewer] = await Promise.all([PersonaPicker.fill($("persona")), this.loadTopics()]);
                this.state.viewer = viewer;
            } catch (error) {
                this.showError(error);
                return;
            }
            $("near").checked = this.state.near;
            $("search-input").value = this.state.query;
            this.refresh();
        }

        bindControls() {
            $("persona").addEventListener("change", (e) => {
                Viewer.set(e.target.value);
                this.update({ viewer: e.target.value, page: 1 });
            });
            $("near").addEventListener("change", (e) => this.update({ near: e.target.checked, page: 1 }));
            $("search-form").addEventListener("submit", (e) => {
                e.preventDefault();
                const query = $("search-input").value.trim();
                this.update({ query, page: 1 });
            });
            $("search-clear").addEventListener("click", () => {
                $("search-input").value = "";
                this.update({ query: "", page: 1 });
                $("search-input").focus();
            });
        }

        update(changes) {
            Object.assign(this.state, changes);
            Settings.save({ near: this.state.near });
            const url = new URL(location.href);
            this.state.query ? url.searchParams.set("q", this.state.query) : url.searchParams.delete("q");
            this.state.page > 1 ? url.searchParams.set("page", this.state.page) : url.searchParams.delete("page");
            history.replaceState(null, "", url);
            this.refresh();
        }

        refresh() {
            return this.state.query ? this.runSearch() : this.runBrowse();
        }

        // --- data ---------------------------------------------------------------

        async loadTopics() {
            const { tags } = await Api.get("/api/tags", {});
            const nav = $("topics");
            const all = [{ slug: "", label: "All" }, ...tags];
            for (const tag of all) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "dc-topic";
                button.textContent = tag.label;
                button.dataset.slug = tag.slug;
                button.setAttribute("aria-pressed", String(tag.slug === this.state.tag));
                button.addEventListener("click", () => {
                    for (const b of nav.children) b.setAttribute("aria-pressed", String(b === button));
                    this.update({ tag: tag.slug, page: 1 });
                });
                nav.append(button);
            }
        }

        async runBrowse() {
            const id = ++this.request;
            this.setSearchMode(false);
            this.showLoading("Loading events…", false);
            try {
                const data = await Api.get("/api/discover", {
                    viewer: this.state.viewer, page: this.state.page,
                    near: this.state.near ? "1" : "0", tag: this.state.tag,
                });
                if (id !== this.request) return;
                if (data.page > data.pages && data.total > 0) {
                    this.update({ page: data.pages });
                    return;
                }
                this.renderCards(data.items, { emptyText: "No upcoming events match. Try another topic." });
                this.renderPages(data);
                this.setStatus("");
            } catch (error) {
                if (id === this.request) this.showError(error);
            }
        }

        async runSearch() {
            const id = ++this.request;
            this.setSearchMode(true);
            this.showLoading("Searching — working out what you're after. This can take a few seconds.", true);
            try {
                const data = await Api.post("/api/search", {
                    viewer: this.state.viewer || null, query: this.state.query, near: this.state.near,
                });
                if (id !== this.request) return;
                this.renderSummary(data);
                this.renderCards(data.items, {
                    emptyText: "Nothing fits that yet. Try describing it differently.", picks: true,
                });
                $("pages").hidden = true;
                const n = data.items.length;
                this.setStatus(n ? `${n} event${n === 1 ? "" : "s"} picked for you` : "");
            } catch (error) {
                if (id === this.request) this.showError(error);
            }
        }

        // --- rendering ------------------------------------------------------------

        setSearchMode(on) {
            $("topics").hidden = on;
            $("search-summary").hidden = !on;
            $("search-button").disabled = false;
            if (!on) $("search-input").value = "";
        }

        showLoading(text, busy) {
            this.setStatus(text, { busy: true });
            $("search-button").disabled = busy;
            $("pages").hidden = true;
            const list = $("results");
            list.replaceChildren();
            for (let i = 0; i < 3; i += 1) {
                const placeholder = document.createElement("li");
                placeholder.className = "dc-card is-skeleton";
                placeholder.setAttribute("aria-hidden", "true");
                list.append(placeholder);
            }
            if (busy) {
                $("search-goal").replaceChildren(this.goalText("Looking for", this.state.query));
                $("search-note").hidden = true;
                $("search-steps").hidden = true;
            }
        }

        renderSummary(data) {
            $("search-button").disabled = false;
            $("search-goal").replaceChildren(this.goalText("Looking for", data.goal));
            const note = $("search-note");
            note.hidden = data.used_ai;
            note.textContent = data.used_ai ? "" : "AI search isn't available right now, so these are your top events, with keyword matches moved up.";
            const steps = $("search-steps");
            const list = $("search-steps-list");
            list.replaceChildren(...data.steps.map((step) => {
                const item = document.createElement("li");
                item.textContent = step.text;
                if (step.error) {
                    item.classList.add("is-error");
                    item.title = "This step was retried";
                }
                return item;
            }));
            steps.hidden = data.steps.length === 0;
        }

        goalText(label, text) {
            const fragment = document.createDocumentFragment();
            const span = document.createElement("span");
            span.textContent = `${label}: `;
            fragment.append(span, document.createTextNode(text));
            return fragment;
        }

        renderCards(items, { emptyText, picks = false }) {
            const list = $("results");
            if (!items.length) {
                const empty = document.createElement("li");
                empty.className = "dc-empty";
                empty.textContent = emptyText;
                list.replaceChildren(empty);
                return;
            }
            list.replaceChildren(...items.map((item) => this.card(item, picks)));
        }

        card(item, isPick) {
            const node = this.template.content.firstElementChild.cloneNode(true);
            const q = (selector) => node.querySelector(selector);
            const start = new Date(item.starts_at);
            const end = new Date(item.ends_at);
            node.classList.toggle("is-cancelled", item.is_cancelled);
            node.classList.toggle("is-pick", isPick);

            q(".dc-date-weekday").textContent = PART({ weekday: "short" }).format(start);
            q(".dc-date-day").textContent = PART({ day: "numeric" }).format(start);
            q(".dc-date-month").textContent = PART({ month: "short" }).format(start);

            const [trust, ...reasons] = item.reasons[0]?.startsWith("Cancelled") ? item.reasons.slice(1) : item.reasons;
            this.badges(q(".dc-badges"), item, trust);

            q(".dc-card-title").textContent = item.title;
            const where = [item.location, item.distance_km != null ? DiscoverPage.distance(item.distance_km) : ""]
                .filter(Boolean).join(" · ");
            q(".dc-meta").textContent = [`${DATE.format(start)}, ${TIME.format(start)}–${TIME.format(end)}`, where]
                .filter(Boolean).join(" · ");
            q(".dc-host").textContent = `Hosted by ${DiscoverPage.cleanName(item.host.name)}`;
            q(".dc-description").textContent = item.description;

            const fit = reasons.find((r) => r.startsWith("Fits what you asked for"));
            if (fit) {
                q(".dc-fit").hidden = false;
                q(".dc-fit").textContent = fit.replace("Fits what you asked for: ", "");
            }
            const shown = reasons.filter(
                (r) => !/friends? going$|away\)?$|^Fits what you asked for|^Not much like|^Hosted by your org/.test(r),
            );
            q(".dc-reasons").replaceChildren(...shown.slice(0, 3).map((reason) => {
                const li = document.createElement("li");
                li.textContent = reason;
                return li;
            }));

            this.friends(q(".dc-friends"), item.friends_going);

            const percent = Math.round(item.score * 100);
            q(".dc-match-value").textContent = item.is_cancelled ? "—" : `${percent}%`;
            q(".dc-match-bar span").style.width = `${item.is_cancelled ? 0 : percent}%`;
            q(".dc-match").setAttribute("aria-label", item.is_cancelled ? "Cancelled" : `${percent}% likely for you`);
            this.going(q(".dc-going"), item);
            q(".dc-actions-slot").replaceWith(this.actions(item, isPick, node));
            return node;
        }

        /** I'm going / Maybe / star. The card redraws itself from the server's answer. */
        actions(item, isPick, node) {
            const controls = new EventActions({
                eventId: item.id,
                myRsvp: item.my_rsvp,
                starred: item.starred,
                viewer: this.state.viewer,
                cancelled: item.is_cancelled,
                onChange: (state) => {
                    const updated = { ...item, my_rsvp: state.my_rsvp, starred: state.starred,
                                      going_count: state.going_count };
                    node.replaceWith(this.card(updated, isPick));
                    this.setStatus(DiscoverPage.savedText(updated));
                },
                onError: (message) => this.setStatus(message, { error: true }),
            });
            return controls.element;
        }

        static savedText(item) {
            const parts = [];
            if (item.my_rsvp === "going") parts.push("you're going");
            else if (item.my_rsvp === "interested") parts.push("marked maybe");
            if (item.starred) parts.push("starred");
            const what = parts.length ? parts.join(" and ") : "removed from your plans";
            return `\u201c${item.title}\u201d: ${what}. Your calendar is updated.`;
        }

        badges(container, item, trust) {
            const add = (text, kind = "") => {
                const badge = document.createElement("span");
                badge.className = `dc-badge ${kind}`.trim();
                badge.textContent = text;
                container.append(badge);
            };
            if (item.is_cancelled) add("Cancelled", "is-cancelled");
            if (item.my_rsvp === "going") add("You're going");
            else if (item.my_rsvp === "interested") add("You're interested");
            if (item.reasons.some((r) => r.startsWith("Hosted by your org"))) add("Your org");
            if (item.host.kind === "person") add("Hosted by a student", "is-person");
            else if (trust && trust.startsWith("Unclaimed")) add("Unverified listing", "is-unverified");
        }

        friends(container, names) {
            if (!names.length) return;
            container.hidden = false;
            const first = names.map(DiscoverPage.cleanName);
            container.querySelector(".dc-avatars").replaceChildren(...first.slice(0, 4).map((name) => {
                const avatar = document.createElement("span");
                avatar.className = "dc-avatar";
                avatar.textContent = name.charAt(0).toUpperCase();
                return avatar;
            }));
            const shown = first.slice(0, 2).join(", ");
            const more = first.length > 2 ? ` and ${first.length - 2} more` : "";
            container.querySelector(".dc-friends-text").textContent = `${shown}${more} going`;
        }

        going(element, item) {
            element.replaceChildren();
            if (item.going_count == null) {
                element.textContent = item.is_cancelled ? "" : "Be one of the first";
                return;
            }
            const strong = document.createElement("strong");
            strong.textContent = String(item.going_count);
            element.append(strong, document.createTextNode(" going"));
        }

        renderPages(data) {
            const nav = $("pages");
            nav.hidden = data.total === 0;
            const first = (data.page - 1) * data.per_page + 1;
            const last = Math.min(data.total, data.page * data.per_page);
            $("count").textContent = `Showing ${first}–${last} of ${data.total}`;
            const buttons = [];
            const button = (label, page, { current = false, disabled = false, aria } = {}) => {
                const b = document.createElement("button");
                b.type = "button";
                b.className = "dc-page";
                b.textContent = label;
                b.disabled = disabled;
                if (aria) b.setAttribute("aria-label", aria);
                if (current) b.setAttribute("aria-current", "page");
                b.addEventListener("click", () => {
                    this.update({ page });
                    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
                });
                buttons.push(b);
            };
            button("‹", data.page - 1, { disabled: data.page === 1, aria: "Previous page" });
            for (const p of DiscoverPage.pageWindow(data.page, data.pages)) {
                if (p === null) {
                    const gap = document.createElement("span");
                    gap.className = "dc-page-gap";
                    gap.textContent = "\u2026";
                    gap.setAttribute("aria-hidden", "true");
                    buttons.push(gap);
                } else {
                    button(String(p), p, { current: p === data.page, aria: `Page ${p}` });
                }
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
            $("search-button").disabled = false;
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

        static cleanName(name) {
            return name.replace(/^\[Demo\]\s*/, "").replace(/\s*\(demo[^)]*\)$/, "");
        }

        static distance(km) {
            if (km < 0.05) return "on campus";
            return km < 1 ? `${Math.round(km * 1000 / 50) * 50} m away` : `${km.toFixed(1)} km away`;
        }
    }

    new DiscoverPage().start();
})();
