// Shared by the Discover and Calendar pages (load before the page script):
//   Collectivize.Api           JSON calls to the local demo server
//   Collectivize.Viewer        who you're viewing as (remembered across pages)
//   Collectivize.PersonaPicker fills a <select> with the demo people
//   Collectivize.EventActions  the "I'm going" / "Maybe" / star buttons
//
// Server text is always inserted with textContent, never as HTML.

"use strict";

(() => {
    const VIEWER_KEY = "collectivize.viewer";
    const OLD_DISCOVER_KEY = "collectivize.discover";

    class Api {
        static async get(path, params = {}) {
            const query = new URLSearchParams(
                Object.entries(params).filter(([, v]) => v !== "" && v != null),
            );
            return Api.send(`${path}?${query}`, { headers: { Accept: "application/json" } });
        }

        static async post(path, body) {
            return Api.send(path, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify(body),
            });
        }

        static async send(url, options) {
            let response;
            try {
                response = await fetch(url, options);
            } catch {
                throw new Error("Can't reach the Collectivize server. Is scripts/serve.py running?");
            }
            let data = null;
            try {
                data = await response.json();
            } catch {
                // Not JSON: usually the page was opened without the demo server.
            }
            if (!response.ok || data === null || typeof data !== "object") {
                throw new Error(data?.error || "The server didn't answer properly. Start it with scripts/serve.py.");
            }
            return data;
        }
    }

    class Viewer {
        static get() {
            try {
                const saved = localStorage.getItem(VIEWER_KEY);
                if (saved !== null) return saved;
                return JSON.parse(localStorage.getItem(OLD_DISCOVER_KEY) || "{}").viewer || "";
            } catch {
                return "";
            }
        }

        static set(id) {
            try {
                localStorage.setItem(VIEWER_KEY, id || "");
            } catch {
                // Storage blocked: the choice just won't be remembered.
            }
        }
    }

    class PersonaPicker {
        /** Fill `select` with the demo people; returns the chosen id. */
        static async fill(select) {
            const { personas } = await Api.get("/api/personas");
            if (!Array.isArray(personas)) throw new Error("The list of people didn't load.");
            const group = (label) => Object.assign(document.createElement("optgroup"), { label });
            const featured = group("Suggested");
            const others = group("Everyone else");
            select.replaceChildren(new Option("Logged out", ""));
            for (const person of personas) {
                if (!person || typeof person.id !== "string") continue;
                const label = `${person.name} — ${person.description || "no orgs yet"}`;
                (person.featured ? featured : others).append(new Option(label, person.id));
            }
            select.append(featured, others);
            const saved = Viewer.get();
            const known = personas.some((p) => p && p.id === saved);
            const chosen = known ? saved : (personas[0]?.id ?? "");
            select.value = chosen;
            Viewer.set(chosen);
            return chosen;
        }
    }

    // Icon shapes as data (no HTML strings anywhere in this file).
    const ICONS = {
        check: [["path", { d: "m5 12.5 4.5 4.5L19 7.5" }]],
        maybe: [
            ["circle", { cx: "12", cy: "12", r: "8.5" }],
            ["path", { d: "M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.5v.5M12 16.8v.1" }],
        ],
        star: [["path", { d: "m12 3.8 2.5 5.1 5.6.8-4 4 .9 5.6L12 16.6l-5 2.7.9-5.6-4-4 5.6-.8z" }]],
    };
    const SVG = "http://www.w3.org/2000/svg";

    function icon(name) {
        const svg = document.createElementNS(SVG, "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("aria-hidden", "true");
        for (const [tag, attributes] of ICONS[name]) {
            const shape = document.createElementNS(SVG, tag);
            for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, value);
            svg.append(shape);
        }
        return svg;
    }

    /**
     * "I'm going", "Maybe" and a star for one event. Each toggles: clicking
     * an active choice clears it. Updates show at once and are undone if
     * the server says no (for example when the event is full).
     */
    class EventActions {
        constructor({ eventId, myRsvp = null, starred = false, viewer, cancelled = false, onChange, onError }) {
            this.eventId = eventId;
            this.state = { my_rsvp: myRsvp, starred: Boolean(starred) };
            this.viewer = viewer;
            this.cancelled = cancelled;
            this.onChange = onChange || (() => {});
            this.onError = onError || (() => {});
            this.busy = false;
            this.element = this.build();
            this.paint();
        }

        build() {
            const box = document.createElement("div");
            box.className = "ev-actions";
            box.setAttribute("role", "group");
            box.setAttribute("aria-label", "Your plans for this event");
            const make = (cls, iconName, text, handler) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = `ev-action ${cls}`;
                button.append(icon(iconName));
                if (text) {
                    const label = document.createElement("span");
                    label.textContent = text;
                    button.append(label);
                }
                button.addEventListener("click", (event) => {
                    event.stopPropagation(); // don't trigger the card or calendar cell
                    handler();
                });
                box.append(button);
                return button;
            };
            this.going = make("is-going", "check", "I'm going", () => this.rsvp("going"));
            this.maybe = make("is-maybe", "maybe", "Maybe", () => this.rsvp("maybe"));
            this.star = make("is-star", "star", "", () => this.toggleStar());
            return box;
        }

        paint() {
            const rsvp = this.state.my_rsvp;
            const noViewer = !this.viewer;
            const why = noViewer ? "Choose who you are in “Viewing as” first" : "";
            this.going.setAttribute("aria-pressed", String(rsvp === "going"));
            this.maybe.setAttribute("aria-pressed", String(rsvp === "interested"));
            this.star.setAttribute("aria-pressed", String(this.state.starred));
            this.star.setAttribute("aria-label", this.state.starred ? "Remove star" : "Star this event");
            this.going.title = why || (rsvp === "going" ? "You're going — click to undo" : "");
            this.maybe.title = why || (rsvp === "interested" ? "Marked maybe — click to undo" : "");
            this.star.title = why || (this.state.starred ? "Starred — click to remove" : "Save to your calendar");
            for (const button of [this.going, this.maybe]) {
                button.disabled = this.busy || noViewer || (this.cancelled && button.getAttribute("aria-pressed") !== "true");
            }
            this.star.disabled = this.busy || noViewer;
            this.element.classList.toggle("is-busy", this.busy);
        }

        async rsvp(choice) {
            const current = this.state.my_rsvp;
            const same = (choice === "going" && current === "going") || (choice === "maybe" && current === "interested");
            await this.save("/api/rsvp", { status: same ? null : choice }, {
                my_rsvp: same ? null : (choice === "going" ? "going" : "interested"),
            });
        }

        async toggleStar() {
            const starred = !this.state.starred;
            await this.save("/api/star", { starred }, { starred });
        }

        async save(path, body, optimistic) {
            if (this.busy || !this.viewer) return;
            const before = { ...this.state };
            this.state = { ...this.state, ...optimistic };
            this.busy = true;
            this.paint();
            try {
                const result = await Api.post(path, { viewer: this.viewer, event_id: this.eventId, ...body });
                this.state = { my_rsvp: result.my_rsvp ?? null, starred: Boolean(result.starred) };
                this.busy = false;
                this.paint();
                this.onChange({ ...result });
            } catch (error) {
                this.state = before; // the server said no: put it back
                this.busy = false;
                this.paint();
                this.onError(error instanceof Error ? error.message : "That didn't save.");
            }
        }
    }

    window.Collectivize = Object.freeze({ Api, Viewer, PersonaPicker, EventActions });
})();
