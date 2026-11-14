// Shared by every page (load before the page script):
//   Collectivize.Api           JSON calls to the local demo server
//   Collectivize.Session       who is signed in (GET /api/me), sign-out
//   Collectivize.EventActions  the "I'm going" / "Maybe" / save buttons
//
// Who you are comes from your sign-in session on the server; pages never
// send a viewer id.
//
// Server text is always inserted with textContent, never as HTML.

"use strict";

(() => {
    const LANDING = "/src/frontend/index.html";
    const HOME = "/src/frontend/discover.html";

    class Api {
        static async get(path, params = {}, settings = {}) {
            const query = new URLSearchParams(
                Object.entries(params).filter(([, v]) => v !== "" && v != null),
            );
            return Api.send(`${path}?${query}`, { headers: { Accept: "application/json" } }, settings);
        }

        static async post(path, body, settings = {}) {
            return Api.send(path, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                body: JSON.stringify(body),
            }, settings);
        }

        /** Signed out (401): go to the sign-in page, then come back here. */
        static async send(url, options, { redirectOnSignOut = true } = {}) {
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
            if (response.status === 401 && redirectOnSignOut) {
                Session.toSignIn();
                throw new Error("Signed out. Taking you to sign in…");
            }
            if (!response.ok || data === null || typeof data !== "object") {
                throw new Error(data?.error || "The server didn't answer properly. Start it with scripts/serve.py.");
            }
            return data;
        }
    }

    class Session {
        static me = null;
        static pending = null;

        /** The signed-in person: {id, name, is_platform_admin, viewing_as}. */
        static async current() {
            if (!Session.pending) Session.pending = Api.get("/api/me").then((me) => (Session.me = me));
            return Session.pending;
        }

        static async signOut() {
            try {
                await Api.post("/api/signout", {}, { redirectOnSignOut: false });
            } finally {
                location.replace(LANDING);
            }
        }

        static toSignIn() {
            const here = location.pathname + location.search;
            location.replace(`${LANDING}?${new URLSearchParams({ next: here })}`);
        }

        /** Where to go after signing in: a page of this app, else Discover. */
        static nextPage(next) {
            if (typeof next === "string" && next.startsWith("/src/frontend/") && !next.includes("//")
                && !next.startsWith(LANDING)) {
                return next;
            }
            return HOME;
        }
    }

    // Icon shapes as data (no HTML strings anywhere in this file).
    const ICONS = {
        check: [["path", { d: "m5 12.5 4.5 4.5L19 7.5" }]],
        maybe: [
            ["circle", { cx: "12", cy: "12", r: "8.5" }],
            ["path", { d: "M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.5v.5M12 16.8v.1" }],
        ],
        star: [["path", { d: "M7 4h10a1 1 0 0 1 1 1v15l-6-4-6 4V5a1 1 0 0 1 1-1z" }]], // bookmark ("Saved")
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
        constructor({ eventId, myRsvp = null, starred = false, cancelled = false, full = false, onChange, onError }) {
            this.eventId = eventId;
            this.state = { my_rsvp: myRsvp, starred: Boolean(starred) };
            this.cancelled = cancelled;
            this.full = full;
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
            const why = "";
            this.going.setAttribute("aria-pressed", String(rsvp === "going"));
            this.maybe.setAttribute("aria-pressed", String(rsvp === "interested"));
            this.star.setAttribute("aria-pressed", String(this.state.starred));
            this.star.setAttribute("aria-label", this.state.starred ? "Remove from saved" : "Save this event");
            this.going.title = why || (rsvp === "going" ? "You're going — click to undo" : "");
            this.maybe.title = why || (rsvp === "interested" ? "Marked maybe — click to undo" : "");
            this.star.title = why || (this.state.starred ? "Saved — click to remove" : "Save to your calendar");
            for (const button of [this.going, this.maybe]) {
                button.disabled = this.busy || (this.cancelled && button.getAttribute("aria-pressed") !== "true");
            }
            // A full event takes no new "going" (you can still undo yours).
            if (this.full && rsvp !== "going") {
                this.going.disabled = true;
                this.going.title = "This event is full";
            }
            this.star.disabled = this.busy;
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
            if (this.busy) return;
            const before = { ...this.state };
            this.state = { ...this.state, ...optimistic };
            this.busy = true;
            this.paint();
            try {
                const result = await Api.post(path, { event_id: this.eventId, ...body });
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

    /** Small DOM helpers for the v1.1 pages. Text always goes in through
     *  textContent, so server data can never inject markup. */
    const Ui = Object.freeze({
        el(tag, options = {}, children = []) {
            const node = document.createElement(tag);
            if (options.className) node.className = options.className;
            if (options.text != null) node.textContent = options.text;
            for (const [name, value] of Object.entries(options.attrs || {})) {
                if (value === true) node.setAttribute(name, "");
                else if (value !== false && value != null) node.setAttribute(name, value);
            }
            for (const child of children) if (child) node.append(child);
            return node;
        },

        /** Demo names without their "[Demo]" / "(demo …)" labels. */
        cleanName(name) {
            return String(name || "").replace(/^\[Demo\]\s*/, "").replace(/\s*\(demo[^)]*\)$/, "");
        },

        /** A round photo, or the person's initial. */
        avatar(person, className = "avatar") {
            const box = Ui.el("span", { className, attrs: { "aria-hidden": "true" } });
            if (person.has_photo) {
                box.append(Ui.el("img", { attrs: {
                    src: `/api/photo?${new URLSearchParams({ id: person.id, slot: "0" })}`, alt: "",
                } }));
            } else {
                box.textContent = Ui.cleanName(person.name).charAt(0).toUpperCase() || "?";
            }
            return box;
        },

        profileHref(id) {
            return `profile.html?${new URLSearchParams({ id })}`;
        },

        orgHref(id) {
            return `org.html?${new URLSearchParams({ id })}`;
        },
    });

    window.Collectivize = Object.freeze({ Api, Session, EventActions, Ui, icon });
})();
