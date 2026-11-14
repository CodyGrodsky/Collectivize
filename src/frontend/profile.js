"use strict";

// Profile page: view a profile (full, locked or not found), view your own
// with an edit mode and live preview. UI only for now: all data comes from
// ProfileDataSource, which reads fictional samples (profile-mock.js) and
// keeps edits in this browser. Swapping in the real API means changing only
// that class; see docs/profile-backend-proposal.md for the planned shape.
//
// URL: ?u=<username> picks the profile, ?as=self or ?as=visitor picks who
// is looking (visitor by default).

(() => {
    const DEFAULT_USERNAME = "maya_r";
    const STORE_KEY = "collectivize.profile.samples";
    const SWATCHES = ["#E8452F", "#19A88A", "#1E3A9E", "#3D6BFF", "#000000", "#FDFDFB", "#F5F4F0", "#202B29"];

    // --- small DOM helper ------------------------------------------------------

    // Builds an element. Text always goes in through textContent, so profile
    // content can never inject markup.
    function el(tag, options = {}, children = []) {
        const node = document.createElement(tag);
        if (options.className) node.className = options.className;
        if (options.text != null) node.textContent = options.text;
        for (const [name, value] of Object.entries(options.attrs || {})) {
            if (value === true) node.setAttribute(name, "");
            else if (value !== false && value != null) node.setAttribute(name, value);
        }
        for (const child of children) if (child) node.append(child);
        return node;
    }

    // --- data ----------------------------------------------------------------------

    // The only place profile data comes from. Methods are async and return
    // the GET /api/profile shape, so the real endpoint can replace the
    // samples without changing anything else on the page.
    class ProfileDataSource {
        constructor(samples, storageKey) {
            this.samples = samples;
            this.storageKey = storageKey;
        }

        // A profile as `viewer` ("self" or "visitor") sees it, or null.
        async getProfile(username, viewer) {
            const record = this.find(username);
            return record ? ProfileDataSource.toView(record, viewer) : null;
        }

        // Every other username, for the "already taken" check while editing.
        async usernamesExcept(username) {
            return this.records().map((r) => r.username).filter((name) => name !== username);
        }

        // Save your own edits. Returns your updated profile.
        async saveProfile(username, changes) {
            const record = this.find(username);
            if (!record) throw new Error("That profile no longer exists.");
            if (changes.username !== username && this.find(changes.username)) {
                throw new Error("That username is taken.");
            }
            const store = this.load();
            store.edits[record.id] = { ...store.edits[record.id], ...changes };
            this.save(store);
            return ProfileDataSource.toView({ ...record, ...changes }, "self");
        }

        // Send a connection request as the visitor. Returns the updated view.
        async requestConnection(username) {
            const record = this.find(username);
            if (!record) throw new Error("That profile no longer exists.");
            const store = this.load();
            store.connections[record.id] = "pending_out";
            this.save(store);
            return this.getProfile(username, "visitor");
        }

        // --- internals ---

        records() {
            const store = this.load();
            return this.samples.map((sample) => ({
                ...sample,
                ...store.edits[sample.id],
                visitor: {
                    ...sample.visitor,
                    connection: store.connections[sample.id] || sample.visitor.connection,
                },
            }));
        }

        find(username) {
            return this.records().find((r) => r.username === username) || null;
        }

        // Storage can be blocked (private windows); the page then still
        // works, it just forgets edits on reload.
        load() {
            try {
                const saved = JSON.parse(window.localStorage.getItem(this.storageKey));
                if (saved && typeof saved === "object") {
                    return { edits: saved.edits || {}, connections: saved.connections || {} };
                }
            } catch {
                // fall through to an empty store
            }
            return { edits: {}, connections: {} };
        }

        save(store) {
            try {
                window.localStorage.setItem(this.storageKey, JSON.stringify(store));
            } catch {
                // Not saved; nothing else to do.
            }
        }

        static toView(record, viewer) {
            const isSelf = viewer === "self";
            const connection = isSelf ? "self" : record.visitor.connection;
            const locked = !isSelf && record.profile_visibility !== "network" && connection !== "connected";
            const base = {
                username: record.username,
                name: record.display_name,
                verified: record.verified,
                colors: { ...record.colors },
                access: locked ? "locked" : "full",
                connection,
                in_common: isSelf ? null : record.visitor.in_common,
                is_self: isSelf,
            };
            if (locked) {
                return { ...base, bio: "", about: "", top3: null, website: null, orgs: [], hosting: [], going: [] };
            }
            return {
                ...base,
                bio: record.bio,
                about: record.about,
                top3: record.top3 ? { title: record.top3.title, items: [...record.top3.items] } : null,
                website: record.website,
                orgs: record.orgs.map((o) => ({ ...o })),
                hosting: record.hosting.map((e) => ({ ...e })),
                going: record.going.map((e) => ({ ...e })),
            };
        }
    }

    // --- rules -----------------------------------------------------------------------

    // Field limits and validation, matching the backend proposal.
    class ProfileRules {
        static LIMITS = { display_name: 50, bio: 120, about: 1000, top3_title: 30, top3_item: 40 };
        static USERNAME = /^[a-z0-9_]{3,20}$/;
        static HEX = /^#[0-9a-f]{6}$/i;

        // Characters as people count them (an emoji is one).
        static length(text) {
            return Array.from(text).length;
        }

        // Errors by field key; empty when the draft is valid.
        static validate(draft, takenUsernames) {
            const errors = {};
            const tooLong = (key, value, max) => {
                if (ProfileRules.length(value) > max) errors[key] = `Keep it to ${max} characters.`;
            };
            tooLong("display_name", draft.display_name, ProfileRules.LIMITS.display_name);
            tooLong("bio", draft.bio, ProfileRules.LIMITS.bio);
            tooLong("about", draft.about, ProfileRules.LIMITS.about);

            const username = draft.username;
            if (!ProfileRules.USERNAME.test(username)) {
                errors.username = "3–20 characters: lowercase letters, numbers and _ only.";
            } else if (takenUsernames.includes(username)) {
                errors.username = "That username is taken.";
            }

            const items = draft.top3_items;
            const usingTop3 = draft.top3_title.trim() || items.some((item) => item.trim());
            tooLong("top3_title", draft.top3_title, ProfileRules.LIMITS.top3_title);
            if (usingTop3 && !draft.top3_title.trim()) errors.top3_title = "Give your Top 3 a title.";
            items.forEach((item, i) => {
                const key = `top3_${i}`;
                tooLong(key, item, ProfileRules.LIMITS.top3_item);
                if (usingTop3 && !item.trim()) errors[key] = "Fill in all three, or clear the Top 3.";
            });

            if (draft.website.trim() && !ProfileRules.domain(draft.website.trim())) {
                errors.website = "Use a full https:// address, like https://example.com.";
            }
            for (const key of ["background", "outline"]) {
                if (!ProfileRules.HEX.test(draft[key])) errors[key] = "Pick a color.";
            }
            return errors;
        }

        // The site's domain for display, or null unless it's a valid https URL.
        static domain(url) {
            try {
                const parsed = new URL(url);
                if (parsed.protocol !== "https:" || !parsed.hostname.includes(".")) return null;
                return parsed.hostname.replace(/^www\./, "");
            } catch {
                return null;
            }
        }

        // A valid draft as profile changes (trimmed, Top 3 null when empty).
        static toChanges(draft) {
            const title = draft.top3_title.trim();
            const items = draft.top3_items.map((item) => item.trim());
            return {
                display_name: draft.display_name.trim(),
                username: draft.username,
                bio: draft.bio.trim(),
                about: draft.about.trim(),
                top3: title ? { title, items } : null,
                website: draft.website.trim() || null,
                colors: { background: draft.background.toUpperCase(), outline: draft.outline.toUpperCase() },
            };
        }
    }

    // --- theme -------------------------------------------------------------------------

    // Applies a profile's colors to its area, picking dark or light text
    // (whichever contrasts more) so it stays readable on any background.
    class ProfileTheme {
        static DARK = "#202B29";
        static LIGHT = "#FDFDFB";

        static apply(element, colors) {
            element.style.setProperty("--p-bg", colors.background);
            element.style.setProperty("--p-line", colors.outline);
            element.style.setProperty("--p-ink", ProfileTheme.readableOn(colors.background));
            element.style.setProperty("--p-on-line", ProfileTheme.readableOn(colors.outline));
        }

        static readableOn(hex) {
            const dark = ProfileTheme.contrast(hex, ProfileTheme.DARK);
            const light = ProfileTheme.contrast(hex, ProfileTheme.LIGHT);
            return dark >= light ? ProfileTheme.DARK : ProfileTheme.LIGHT;
        }

        // True when the outline would be hard to see against the background.
        static outlineIsFaint(colors) {
            return ProfileTheme.contrast(colors.background, colors.outline) < 1.5;
        }

        // WCAG contrast ratio between two #RRGGBB colors (1 to 21).
        static contrast(a, b) {
            const [high, low] = [ProfileTheme.luminance(a), ProfileTheme.luminance(b)].sort((x, y) => y - x);
            return (high + 0.05) / (low + 0.05);
        }

        static luminance(hex) {
            const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
            const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        }
    }

    // --- rendering ---------------------------------------------------------------------

    const DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
        weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });

    // Draws a profile. Used for the page and for the editor's live preview.
    // mode: "self" (your own page), "visitor", or "preview" (inert visitor view).
    class ProfileRenderer {
        render(view, mode) {
            const surface = el("section", { className: "profile-surface", attrs: { "aria-label": "Profile" } });
            ProfileTheme.apply(surface, view.colors);
            if (view.access === "locked") {
                surface.classList.add("is-locked");
                surface.append(this.card(view, mode, true));
                return surface;
            }
            surface.append(el("div", { className: "profile-grid" }, [
                this.card(view, mode, false),
                this.sections(view, mode),
            ]));
            return surface;
        }

        static shownName(view) {
            return view.name || `@${view.username}`;
        }

        static initials(view) {
            const words = (view.name || view.username).trim().split(/\s+/);
            const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : Array.from(words[0])[0];
            return (letters || "?").toUpperCase();
        }

        card(view, mode, locked) {
            const card = el("div", { className: "p-box p-card" }, [
                el("div", { className: "p-ring", attrs: { "aria-hidden": "true" } }, [
                    el("span", { text: ProfileRenderer.initials(view) }),
                ]),
                el("h1", { className: "p-name", text: ProfileRenderer.shownName(view) }),
                view.name ? el("p", { className: "p-username", text: `@${view.username}` }) : null,
                view.verified ? this.badge() : null,
            ]);
            if (locked) {
                card.append(el("p", { className: "p-locked-note", text: "This profile is visible to connections only." }));
            } else {
                if (view.bio) card.append(el("p", { className: "p-bio", text: view.bio }));
                const domain = view.website && ProfileRules.domain(view.website);
                if (domain) {
                    card.append(el("a", {
                        className: "p-button p-button-outline p-site",
                        attrs: { href: view.website, target: "_blank", rel: "noopener noreferrer nofollow" },
                    }, [el("span", { text: "Visit my site" }), el("span", { className: "p-site-domain", text: domain })]));
                }
            }
            if (mode !== "self") card.append(this.connectButton(view, mode));
            if (mode !== "self" && view.in_common && !locked) {
                card.append(el("p", { className: "p-share" }, [
                    el("span", { className: "p-share-label", text: "You share" }),
                    el("span", { text: view.in_common }),
                ]));
            }
            return card;
        }

        badge() {
            const icon = el("span", { className: "p-badge-icon", attrs: { "aria-hidden": "true" } });
            icon.innerHTML = '<svg viewBox="0 0 16 16"><path d="m4 8.5 2.5 2.5L12 5.5"/></svg>';
            return el("p", { className: "p-badge" }, [icon, el("span", { text: "Verified Northeastern" })]);
        }

        connectButton(view, mode) {
            const states = {
                none: ["Connect", true],
                pending_out: ["Request sent", false],
                pending_in: ["Accept connection", true],
                connected: ["Connected", false],
            };
            const [label, actionable] = states[view.connection] || states.none;
            const live = actionable && mode === "visitor";
            return el("button", {
                className: "p-button p-button-solid p-connect",
                text: label,
                attrs: {
                    type: "button",
                    "data-action": live ? "connect" : null,
                    disabled: !live,
                    tabindex: mode === "preview" ? "-1" : null,
                },
            });
        }

        sections(view, mode) {
            const own = mode === "self";
            const column = el("div", { className: "p-sections" });
            if (view.about || own) {
                column.append(this.section("About", view.about
                    ? el("p", { className: "p-about", text: view.about })
                    : this.empty("Add a few lines about yourself in Edit page.")));
            }
            if (view.top3) {
                column.append(this.section(view.top3.title, el("ol", { className: "p-top3" },
                    view.top3.items.map((item) => el("li", { text: item })))));
            }
            column.append(this.section("Orgs", view.orgs.length
                ? el("ul", { className: "p-orgs" }, view.orgs.map((org) => el("li", { text: org.name })))
                : this.empty(own ? "Orgs you join will show up here." : "No orgs to show.")));
            column.append(this.section("Events", el("div", { className: "p-events" }, [
                this.eventList("Hosting", view.hosting),
                this.eventList("Going", view.going),
            ])));
            return column;
        }

        section(title, body) {
            return el("section", { className: "p-box p-section" }, [el("h2", { className: "p-section-title", text: title }), body]);
        }

        eventList(title, events) {
            const group = el("div", { className: "p-event-group" }, [el("h3", { className: "p-event-heading", text: title })]);
            if (!events.length) {
                group.append(this.empty("Nothing coming up."));
                return group;
            }
            group.append(el("ul", { className: "p-event-list" }, events.map((event) => el("li", {
                className: event.is_cancelled ? "p-event is-cancelled" : "p-event",
            }, [
                el("span", { className: "p-event-date", text: DATE_FORMAT.format(new Date(event.starts_at)) }),
                el("span", { className: "p-event-title", text: event.title }),
                el("span", { className: "p-event-meta", text: [event.host.name, event.location].filter(Boolean).join(" · ") }),
                event.is_cancelled ? el("span", { className: "p-event-tag", text: "Cancelled" }) : null,
            ]))));
            return group;
        }

        empty(text) {
            return el("p", { className: "p-empty", text });
        }
    }

    // --- editing -----------------------------------------------------------------------

    // Edit mode: a form on the left and a live preview (the same renderer)
    // on the right. Saving goes through ProfileDataSource.
    class ProfileEditor {
        constructor({ view, dataSource, renderer, onSaved, onCancel }) {
            this.view = view;
            this.dataSource = dataSource;
            this.renderer = renderer;
            this.onSaved = onSaved;
            this.onCancel = onCancel;
            this.taken = [];
            this.fields = {};
            const top3 = view.top3 || { title: "", items: ["", "", ""] };
            this.draft = {
                display_name: view.name || "",
                username: view.username,
                bio: view.bio || "",
                about: view.about || "",
                top3_title: top3.title,
                top3_items: [...top3.items],
                website: view.website || "",
                background: view.colors.background,
                outline: view.colors.outline,
            };
        }

        async mount(container) {
            this.taken = await this.dataSource.usernamesExcept(this.view.username);
            this.form = el("form", { className: "edit-form", attrs: { novalidate: true, "aria-label": "Edit profile" } });
            this.form.append(
                this.group("Name", [
                    this.textField("display_name", "Display name", { max: ProfileRules.LIMITS.display_name, hint: "Optional. Shown instead of your username." }),
                    this.textField("username", "Username", { prefix: "@", hint: "3–20 characters: a–z, 0–9 and _", counter: false }),
                ]),
                this.group("About you", [
                    this.textField("bio", "Bio", { max: ProfileRules.LIMITS.bio, hint: "One line." }),
                    this.textField("about", "About", { max: ProfileRules.LIMITS.about, multiline: true }),
                ]),
                this.group("Top 3", [
                    this.textField("top3_title", "Title", { max: ProfileRules.LIMITS.top3_title, placeholder: "Top 3 study spots" }),
                    ...[0, 1, 2].map((i) => this.textField(`top3_${i}`, `Item ${i + 1}`, { max: ProfileRules.LIMITS.top3_item })),
                ], "Optional. Leave all four empty to hide it."),
                this.group("Links", [
                    this.textField("website", "Website", { type: "url", placeholder: "https://", hint: "Optional. Must start with https://", counter: false }),
                ]),
                this.group("Colors", [
                    this.colorField("background", "Background"),
                    this.colorField("outline", "Outline"),
                    this.outlineNote = el("p", { className: "edit-note", attrs: { hidden: true } }),
                ]),
                this.actions(),
            );
            this.form.addEventListener("submit", (event) => {
                event.preventDefault();
                this.save();
            });

            this.preview = el("div", { className: "edit-preview-body" });
            container.replaceChildren(el("div", { className: "edit-layout" }, [
                this.form,
                el("section", { className: "edit-preview", attrs: { "aria-label": "Live preview" } }, [
                    el("p", { className: "edit-preview-label", text: "Live preview · as visitors see it" }),
                    this.preview,
                ]),
            ]));
            this.refresh();
            this.fields.display_name.input.focus();
        }

        group(title, children, note) {
            return el("fieldset", { className: "edit-group" }, [
                el("legend", { className: "edit-legend", text: title }),
                note ? el("p", { className: "edit-hint", text: note }) : null,
                ...children,
            ]);
        }

        textField(key, label, { max, hint, placeholder, prefix, multiline = false, type = "text", counter = true }) {
            const id = `edit-${key}`;
            const input = el(multiline ? "textarea" : "input", {
                className: "edit-input",
                attrs: {
                    id, name: key, placeholder,
                    type: multiline ? null : type,
                    rows: multiline ? "6" : null,
                    autocomplete: "off",
                    autocapitalize: key === "username" || key === "website" ? "none" : null,
                    spellcheck: key === "username" || key === "website" ? "false" : null,
                    "aria-describedby": `${id}-hint ${id}-error`,
                },
            });
            input.value = this.read(key);
            input.addEventListener("input", () => {
                this.write(key, key === "username" ? input.value.toLowerCase() : input.value);
                if (key === "username" && input.value !== this.draft.username) input.value = this.draft.username;
                this.refresh();
            });
            const count = counter && max ? el("span", { className: "edit-count", attrs: { "aria-hidden": "true" } }) : null;
            const error = el("p", { className: "edit-error", attrs: { id: `${id}-error` } });
            this.fields[key] = { input, count, error, max };
            const control = prefix
                ? el("div", { className: "edit-prefixed" }, [el("span", { className: "edit-prefix", text: prefix, attrs: { "aria-hidden": "true" } }), input])
                : input;
            return el("div", { className: "edit-field" }, [
                el("div", { className: "edit-label-row" }, [el("label", { attrs: { for: id }, text: label }), count]),
                control,
                el("p", { className: "edit-hint", text: hint || "", attrs: { id: `${id}-hint` } }),
                error,
            ]);
        }

        colorField(key, label) {
            const swatches = SWATCHES.map((hex) => {
                const button = el("button", {
                    className: "edit-swatch",
                    attrs: { type: "button", "aria-label": `${label} ${hex}`, title: hex },
                });
                button.style.setProperty("--swatch", hex);
                button.addEventListener("click", () => this.setColor(key, hex));
                return button;
            });
            const picker = el("input", {
                className: "edit-picker",
                attrs: { type: "color", id: `edit-${key}`, "aria-label": `Custom ${label.toLowerCase()} color` },
            });
            picker.addEventListener("input", () => this.setColor(key, picker.value));
            const value = el("span", { className: "edit-hex" });
            const error = el("p", { className: "edit-error" });
            this.fields[key] = { swatches, picker, value, error };
            return el("div", { className: "edit-field" }, [
                el("div", { className: "edit-label-row" }, [el("span", { className: "edit-label", text: label }), value]),
                el("div", { className: "edit-swatches", attrs: { role: "group", "aria-label": `${label} color` } }, [
                    ...swatches,
                    el("label", { className: "edit-custom", attrs: { title: "Custom color" } }, [picker, el("span", { text: "Custom" })]),
                ]),
                error,
            ]);
        }

        setColor(key, hex) {
            this.draft[key] = hex.toUpperCase();
            this.refresh();
        }

        actions() {
            this.status = el("p", { className: "edit-status", attrs: { role: "status" } });
            this.saveButton = el("button", { className: "page-button page-button-solid", text: "Save", attrs: { type: "submit" } });
            const cancel = el("button", { className: "page-button", text: "Cancel", attrs: { type: "button" } });
            cancel.addEventListener("click", () => this.onCancel());
            return el("div", { className: "edit-actions" }, [this.saveButton, cancel, this.status]);
        }

        read(key) {
            const match = key.match(/^top3_(\d)$/);
            return match ? this.draft.top3_items[Number(match[1])] : this.draft[key];
        }

        write(key, value) {
            const match = key.match(/^top3_(\d)$/);
            if (match) this.draft.top3_items[Number(match[1])] = value;
            else this.draft[key] = value;
        }

        // Update counters, inline errors, color controls and the preview.
        refresh() {
            const errors = ProfileRules.validate(this.draft, this.taken);
            for (const [key, field] of Object.entries(this.fields)) {
                const message = errors[key] || "";
                field.error.textContent = message;
                if (field.input) {
                    field.input.setAttribute("aria-invalid", String(Boolean(message)));
                    if (field.count) {
                        const used = ProfileRules.length(this.read(key));
                        field.count.textContent = `${used}/${field.max}`;
                        field.count.classList.toggle("is-over", used > field.max);
                    }
                } else {
                    const current = this.draft[key];
                    field.value.textContent = current;
                    field.picker.value = current.toLowerCase();
                    for (const swatch of field.swatches) {
                        swatch.setAttribute("aria-pressed", String(swatch.title === current));
                    }
                }
            }
            const colors = { background: this.draft.background, outline: this.draft.outline };
            const faint = !errors.background && !errors.outline && ProfileTheme.outlineIsFaint(colors);
            this.outlineNote.hidden = !faint;
            this.outlineNote.textContent = faint ? "The outline is hard to see on this background." : "";
            this.saveButton.disabled = Object.keys(errors).length > 0;
            this.status.textContent = "";
            this.renderPreview(errors);
        }

        renderPreview(errors) {
            const changes = ProfileRules.toChanges(this.draft);
            const valid = (key) => !errors[key];
            const view = {
                ...this.view,
                access: "full",
                connection: "none",
                in_common: null,
                name: changes.display_name,
                username: valid("username") ? changes.username : this.view.username,
                bio: changes.bio,
                about: changes.about,
                top3: changes.top3 && [0, 1, 2].every((i) => valid(`top3_${i}`)) && valid("top3_title") ? changes.top3 : null,
                website: valid("website") ? changes.website : null,
                colors: changes.colors,
            };
            this.preview.replaceChildren(this.renderer.render(view, "preview"));
        }

        async save() {
            const errors = ProfileRules.validate(this.draft, this.taken);
            if (Object.keys(errors).length) return;
            this.saveButton.disabled = true;
            try {
                const saved = await this.dataSource.saveProfile(this.view.username, ProfileRules.toChanges(this.draft));
                this.onSaved(saved);
            } catch (error) {
                this.status.textContent = error.message;
                this.saveButton.disabled = false;
            }
        }
    }

    // --- page --------------------------------------------------------------------------

    // Reads the URL, picks the view (full, locked, not found, own, editing)
    // and handles the page's buttons.
    class ProfilePage {
        constructor(root, dataSource) {
            this.root = root;
            this.dataSource = dataSource;
            this.renderer = new ProfileRenderer();
            const params = new URLSearchParams(window.location.search);
            this.username = (params.get("u") || DEFAULT_USERNAME).trim().toLowerCase();
            this.isOwner = params.get("as") === "self";
            this.asVisitor = !this.isOwner;
            this.root.addEventListener("click", (event) => this.onClick(event));
        }

        async show() {
            const viewer = this.asVisitor ? "visitor" : "self";
            const view = await this.dataSource.getProfile(this.username, viewer);
            if (!view) {
                this.showNotFound();
                return;
            }
            this.view = view;
            document.title = `${ProfileRenderer.shownName(view)} · Collectivize`;
            // Owners viewing as a visitor get an inert copy (no connecting to yourself).
            const mode = !this.asVisitor ? "self" : this.isOwner ? "preview" : "visitor";
            const surface = this.renderer.render(view, mode);
            this.root.replaceChildren(...(this.isOwner ? [this.ownerBar(), surface] : [surface]));
        }

        ownerBar() {
            const toggle = el("button", {
                className: "page-button",
                text: "View as visitor",
                attrs: { type: "button", "data-action": "toggle-visitor", "aria-pressed": String(this.asVisitor) },
            });
            const edit = el("button", {
                className: "page-button page-button-solid",
                text: "Edit page",
                attrs: { type: "button", "data-action": "edit", hidden: this.asVisitor },
            });
            const note = el("p", {
                className: "owner-note",
                text: this.asVisitor ? "You're seeing your page the way visitors do." : "This is your page.",
            });
            return el("div", { className: "owner-bar" }, [note, el("div", { className: "owner-actions" }, [toggle, edit])]);
        }

        showNotFound() {
            document.title = "Profile not found · Collectivize";
            this.root.replaceChildren(el("div", { className: "page-message" }, [
                el("h1", { text: "We couldn't find that profile" }),
                el("p", { text: `No one here goes by @${this.username}. Check the spelling, or head back home.` }),
                el("a", { className: "page-button", text: "Go to Home", attrs: { href: "index.html" } }),
            ]));
        }

        async onClick(event) {
            const button = event.target.closest("[data-action]");
            if (!button || !this.root.contains(button)) return;
            const action = button.dataset.action;
            if (action === "toggle-visitor") {
                this.asVisitor = !this.asVisitor;
                await this.show();
                this.root.querySelector('[data-action="toggle-visitor"]')?.focus();
            } else if (action === "edit") {
                this.edit();
            } else if (action === "connect") {
                button.disabled = true;
                this.view = await this.dataSource.requestConnection(this.username);
                await this.show();
            }
        }

        edit() {
            document.title = "Edit profile · Collectivize";
            const editor = new ProfileEditor({
                view: this.view,
                dataSource: this.dataSource,
                renderer: this.renderer,
                onSaved: (saved) => {
                    this.setUsername(saved.username);
                    this.show();
                },
                onCancel: () => this.show(),
            });
            editor.mount(this.root);
        }

        // Keep the URL pointing at the profile after a username change.
        setUsername(username) {
            this.username = username;
            const params = new URLSearchParams(window.location.search);
            params.set("u", username);
            window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
        }
    }

    const samples = window.COLLECTIVIZE_PROFILE_MOCK || [];
    new ProfilePage(document.getElementById("profile-root"), new ProfileDataSource(samples, STORE_KEY)).show();
})();
