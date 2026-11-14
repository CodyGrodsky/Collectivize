"use strict";

// Profile page (v1.1 spec 4.3). URL: ?id=<person> (yours when missing).
//
// - Top: the profile card (profile-card.js), the same card people see on
//   your connection requests and invites.
// - Below, for you and your connections only: organizations, events
//   (going / attended / hosted) and the network graph. The server leaves
//   these out for anyone else, so they are never in the page at all.
// - Your own profile: an editor (photo, pictures, bio, Top 3, colours from
//   a preset palette, with a live preview) and your upcoming plans.
//
// Organizations and events come from data; nobody types them in.

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;
    const root = document.getElementById("profile-root");
    const DATE = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
    const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
    const PHOTO_PX = 320;
    const PICTURE_PX = 640;

    const subjectId = new URLSearchParams(location.search).get("id") || "";
    let profile = null;

    async function load() {
        try {
            profile = await Api.get("/api/profile", { id: subjectId });
            render();
        } catch (error) {
            root.replaceChildren(el("p", { className: "page-message", text: error.message }));
        }
    }

    function render() {
        const card = profile.card;
        document.title = `${cleanName(card.name)} · Collectivize`;
        const status = el("p", { className: "status", attrs: { role: "status", "aria-live": "polite" } });
        const parts = [
            el("div", { className: "profile-top" }, [
                window.ProfileCard.render(card),
                el("div", { className: "profile-actions" }, actions(status)),
            ]),
            status,
        ];
        if (!profile.sections.orgs && !profile.is_self) {
            parts.push(el("p", { className: "profile-locked",
                text: `Connect with ${cleanName(card.name).split(" ")[0]} to see their organizations, events and network.` }));
        }
        if (profile.is_self) parts.push(plans());
        if (profile.sections.orgs) parts.push(orgs());
        if (profile.sections.events) parts.push(events());
        if (profile.sections.network) {
            const box = el("div");
            parts.push(el("section", { className: "panel", attrs: { "aria-labelledby": "network-title" } }, [
                el("h2", { className: "panel-title", text: "Network", attrs: { id: "network-title" } }), box,
            ]));
            new window.NetworkGraph(box).load(card.id);
        }
        root.replaceChildren(...parts);
    }

    // --- actions under the card ------------------------------------------------------

    function actions(status) {
        const card = profile.card;
        if (profile.is_self) {
            const edit = el("button", { className: "button button-solid", text: "Edit profile", attrs: { type: "button" } });
            edit.addEventListener("click", () => openEditor());
            return [edit];
        }
        const button = (text, solid, handler) => {
            const b = el("button", { className: `button${solid ? " button-solid" : ""}`, text, attrs: { type: "button" } });
            b.addEventListener("click", async () => {
                b.disabled = true;
                try {
                    const result = await handler();
                    await load();
                    if (result?.notice) root.querySelector(".status").textContent = result.notice;
                } catch (error) {
                    status.textContent = error.message;
                    status.classList.add("is-error");
                    b.disabled = false;
                }
            });
            return b;
        };
        const post = (action) => () => Api.post("/api/connection", { person_id: card.id, action });
        switch (card.connection) {
            case "connected":
                return [el("span", { className: "pill pill-green", text: "Connected" }),
                        button("Remove connection", false, post("remove"))];
            case "pending_out":
                return [el("span", { className: "pill", text: "Request sent" }), button("Cancel request", false, post("cancel"))];
            case "pending_in":
                return [button("Accept request", true, post("accept")), button("Decline", false, post("decline"))];
            default:
                return [button("Connect", true, () => Api.post("/api/connect", { person_id: card.id }))];
        }
    }

    // --- sections below the card ---------------------------------------------------

    function eventRow(event, note = "") {
        const start = new Date(event.starts_at);
        const meta = [`${DATE.format(start)}, ${TIME.format(start)}`, event.location,
                      event.host ? `by ${cleanName(event.host)}` : "", event.is_cancelled ? "Cancelled" : "", note]
            .filter(Boolean).join(" · ");
        return el("li", { className: "list-item" }, [
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title", text: event.title }),
                el("p", { className: "list-meta", text: meta }),
            ]),
        ]);
    }

    function eventList(title, list, empty, note) {
        return el("div", { className: "profile-events" }, [
            el("h3", { className: "profile-subtitle", text: title }),
            list.length ? el("ul", { className: "list" }, list.map((e) => eventRow(e, note ? note(e) : "")))
                : el("p", { className: "muted", text: empty }),
        ]);
    }

    function orgs() {
        const list = profile.orgs;
        return el("section", { className: "panel" }, [
            el("h2", { className: "panel-title", text: "Organizations" }),
            list.length
                ? el("ul", { className: "chip-list" }, list.map((o) => el("li", {}, [
                    el("a", { className: "chip", text: cleanName(o.name) + (o.is_admin ? " · admin" : ""), attrs: { href: Ui.orgHref(o.id) } }),
                ])))
                : el("p", { className: "muted", text: "Not in any organizations yet." }),
        ]);
    }

    function events() {
        const e = profile.events;
        return el("section", { className: "panel" }, [
            el("h2", { className: "panel-title", text: "Events" }),
            el("div", { className: "profile-event-grid" }, [
                eventList("Going to", e.going, "Nothing coming up."),
                eventList("Hosted", e.hosted, "Hasn't hosted anything yet.", (ev) => (ev.is_past ? "Past" : "Upcoming")),
                eventList("Attended", e.attended, "No past events yet."),
            ]),
        ]);
    }

    /** Your own profile only: your next plans, and the way to all of them. */
    function plans() {
        const label = (e) => [e.rsvp === "going" ? "Going" : e.rsvp === "maybe" ? "Maybe" : "", e.saved ? "Saved" : ""]
            .filter(Boolean).join(" · ");
        return el("section", { className: "panel" }, [
            el("div", { className: "panel-head" }, [
                el("h2", { className: "panel-title", text: "Your plans" }),
                el("div", { className: "row" }, [
                    el("a", { className: "button", text: "My Events", attrs: { href: "my-events.html" } }),
                    el("a", { className: "button", text: "Calendar", attrs: { href: "calendar.html" } }),
                ]),
            ]),
            profile.upcoming.length
                ? el("ul", { className: "list" }, profile.upcoming.map((e) => eventRow(e, label(e))))
                : el("p", { className: "muted", text: "No plans yet. Find something on Discover." }),
        ]);
    }

    // --- editing your own profile -------------------------------------------------

    function openEditor() {
        const card = profile.card;
        const limits = profile.limits;
        const draft = {
            name: card.name,
            bio: card.bio,
            title: card.top3?.title || "",
            items: card.top3 ? [...card.top3.items] : ["", "", ""],
            outline: card.theme.outline,
            background: card.theme.background,
        };
        const preview = el("div", { className: "editor-preview" });
        const message = el("p", { className: "status", attrs: { role: "status", "aria-live": "polite" } });

        const repaint = () => {
            const top3 = draft.title.trim() ? { title: draft.title, items: draft.items } : null;
            preview.replaceChildren(window.ProfileCard.render({
                ...card, name: draft.name, bio: draft.bio, top3,
                theme: { ...card.theme, outline_hex: profile.palette.outline[draft.outline],
                         background_hex: profile.palette.background[draft.background] },
            }, { headingLevel: "h3" }));
        };
        const field = (label, value, max, onInput, { multiline = false } = {}) => {
            const input = el(multiline ? "textarea" : "input", { className: "input", attrs: { maxlength: String(max) } });
            input.value = value;
            const counter = el("span", { className: "field-count", text: `${value.length}/${max}` });
            input.addEventListener("input", () => {
                counter.textContent = `${input.value.length}/${max}`;
                onInput(input.value);
                repaint();
            });
            return el("label", { className: "field" }, [el("span", { className: "field-label" }, [document.createTextNode(label), counter]), input]);
        };
        const swatches = (label, presets, key) => {
            const group = el("div", { className: "swatches", attrs: { role: "radiogroup", "aria-label": label } });
            for (const [name, hex] of Object.entries(presets)) {
                const swatch = el("button", { className: "swatch", attrs: { type: "button", role: "radio", "aria-label": name, title: name,
                                                                              "aria-checked": String(draft[key] === name) } });
                swatch.style.background = hex;
                swatch.addEventListener("click", () => {
                    draft[key] = name;
                    for (const s of group.children) s.setAttribute("aria-checked", String(s === swatch));
                    repaint();
                });
                group.append(swatch);
            }
            return el("div", { className: "field" }, [el("span", { className: "field-label", text: label }), group]);
        };

        const save = el("button", { className: "button button-solid", text: "Save", attrs: { type: "submit" } });
        const cancel = el("button", { className: "button", text: "Cancel", attrs: { type: "button" } });
        cancel.addEventListener("click", () => render());

        const form = el("form", { className: "editor-form" }, [
            el("h2", { className: "panel-title", text: "Edit profile" }),
            photos(message),
            field("Name", draft.name, limits.name, (v) => { draft.name = v; }),
            field("Bio", draft.bio, limits.bio, (v) => { draft.bio = v; }, { multiline: true }),
            el("fieldset", { className: "field top3-fields" }, [
                el("legend", { className: "field-label", text: "Top 3 (leave the title empty to hide it)" }),
                field("Title, like “study spots”", draft.title, limits.top3_title, (v) => { draft.title = v; }),
                ...draft.items.map((item, i) => field(`${i + 1}.`, item, limits.top3_item, (v) => { draft.items[i] = v; })),
            ]),
            swatches("Outline colour", profile.palette.outline, "outline"),
            swatches("Background colour", profile.palette.background, "background"),
            message,
            el("div", { className: "row" }, [save, cancel]),
        ]);
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const title = draft.title.trim();
            if (title && draft.items.some((i) => !i.trim())) {
                message.textContent = "Fill in all three Top 3 items, or clear the title.";
                message.classList.add("is-error");
                return;
            }
            save.disabled = true;
            try {
                profile = await Api.post("/api/profile", {
                    name: draft.name, bio: draft.bio,
                    top3: title ? { title, items: draft.items.map((i) => i.trim()) } : null,
                    theme: { outline: draft.outline, background: draft.background },
                });
                render();
            } catch (error) {
                message.textContent = error.message;
                message.classList.add("is-error");
                save.disabled = false;
            }
        });

        root.replaceChildren(el("div", { className: "editor" }, [
            el("section", { className: "panel" }, [form]),
            el("aside", { className: "editor-side", attrs: { "aria-label": "Preview" } }, [
                el("p", { className: "field-label", text: "Preview" }), preview,
            ]),
        ]));
        repaint();
    }

    /** Photo and the three pictures: resized here, then uploaded at once. */
    function photos(message) {
        const card = profile.card;
        const slot = (n, label) => {
            const has = n === 0 ? card.has_photo : card.pictures.includes(n);
            const input = el("input", { className: "sr-only", attrs: { type: "file", accept: "image/jpeg,image/png,image/webp" } });
            const pick = el("label", { className: "button", text: has ? `Change ${label}` : `Add ${label}` }, [input]);
            input.addEventListener("change", async () => {
                const file = input.files?.[0];
                if (!file) return;
                message.textContent = "Uploading…";
                message.classList.remove("is-error");
                try {
                    const data_b64 = await resize(file, n === 0 ? PHOTO_PX : PICTURE_PX);
                    profile = await Api.post("/api/profile/photo", { slot: n, content_type: "image/jpeg", data_b64 });
                    openEditor();
                } catch (error) {
                    message.textContent = error.message;
                    message.classList.add("is-error");
                }
            });
            const items = [pick];
            if (has) {
                const remove = el("button", { className: "button button-danger", text: "Remove", attrs: { type: "button" } });
                remove.addEventListener("click", async () => {
                    try {
                        profile = await Api.post("/api/profile/photo", { slot: n, remove: true });
                        openEditor();
                    } catch (error) {
                        message.textContent = error.message;
                    }
                });
                items.push(remove);
            }
            return el("div", { className: "row" }, items);
        };
        return el("div", { className: "field" }, [
            el("span", { className: "field-label", text: "Photo and pictures" }),
            slot(0, "photo"),
            slot(1, "picture 1"), slot(2, "picture 2"), slot(3, "picture 3"),
        ]);
    }

    /** A JPEG (base64, no data: prefix) at most `max` px on its long side. */
    function resize(file, max) {
        return new Promise((resolve, reject) => {
            const url = URL.createObjectURL(file);
            const img = new Image();
            img.onload = () => {
                const scale = Math.min(1, max / Math.max(img.width, img.height));
                const canvas = document.createElement("canvas");
                canvas.width = Math.max(1, Math.round(img.width * scale));
                canvas.height = Math.max(1, Math.round(img.height * scale));
                const ctx = canvas.getContext("2d");
                ctx.fillStyle = "#fff";
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                URL.revokeObjectURL(url);
                resolve(canvas.toDataURL("image/jpeg", 0.85).split(",", 2)[1]);
            };
            img.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error("That file isn't an image we can read."));
            };
            img.src = url;
        });
    }

    load();
})();
