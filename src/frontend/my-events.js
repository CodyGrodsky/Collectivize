"use strict";

// My Events (v1.1 spec 4.6, the team's "catalog"): Going, Maybe and
// Saved as tabs, upcoming first, past events folded away. Changing a plan
// here (un-going, un-saving) updates it everywhere, calendar included,
// because it all comes from the same server state.

(() => {
    const { Api, EventActions, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;
    const $ = (id) => document.getElementById(id);
    const DATE = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
    const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
    const TABS = {
        going: { match: (e) => e.rsvp === "going", empty: "You're not going to anything yet. Find something on Discover." },
        maybe: { match: (e) => e.rsvp === "maybe", empty: "Nothing marked maybe." },
        saved: { match: (e) => e.saved, empty: "Nothing saved. Tap the bookmark on any event to save it." },
    };

    const initial = new URLSearchParams(location.search).get("tab");
    let tab = initial in TABS ? initial : "going";
    let data = { upcoming: [], past: [] };

    for (const button of document.querySelectorAll(".tab")) {
        button.addEventListener("click", () => select(button.dataset.tab));
        button.addEventListener("keydown", (event) => {
            const order = Object.keys(TABS);
            const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
            if (!step) return;
            const next = order[(order.indexOf(tab) + step + order.length) % order.length];
            select(next);
            $(`tab-${next}`).focus();
        });
    }

    function select(name) {
        tab = name;
        const url = new URL(location.href);
        url.searchParams.set("tab", name);
        history.replaceState(null, "", url);
        render();
    }

    async function load() {
        try {
            data = await Api.get("/api/my-events");
            render();
        } catch (error) {
            $("status").textContent = error.message;
            $("status").classList.add("is-error");
        }
    }

    function render() {
        for (const button of document.querySelectorAll(".tab")) {
            const name = button.dataset.tab;
            const selected = name === tab;
            button.setAttribute("aria-selected", String(selected));
            button.tabIndex = selected ? 0 : -1;
            button.querySelector(".tab-count").textContent = String(data.upcoming.filter(TABS[name].match).length);
        }
        $("panel").setAttribute("aria-labelledby", `tab-${tab}`);
        const upcoming = data.upcoming.filter(TABS[tab].match);
        const past = data.past.filter(TABS[tab].match);
        $("upcoming").replaceChildren(...upcoming.map((e) => row(e, false)));
        $("upcoming-empty").hidden = upcoming.length > 0;
        $("upcoming-empty").textContent = TABS[tab].empty;
        $("past-box").hidden = past.length === 0;
        $("past-summary").textContent = `Past events (${past.length})`;
        $("past").replaceChildren(...past.map((e) => row(e, true)));
    }

    function row(event, isPast) {
        const start = new Date(event.starts_at);
        const meta = [`${DATE.format(start)}, ${TIME.format(start)}`, event.location,
                      event.host ? `by ${cleanName(event.host)}` : ""].filter(Boolean).join(" · ");
        const badges = [];
        if (event.is_cancelled) badges.push(el("span", { className: "pill pill-amber", text: "Cancelled" }));
        if (event.capacity && event.going_count != null && event.going_count >= event.capacity) {
            badges.push(el("span", { className: "pill", text: "Full" }));
        }
        const parts = [
            el("div", { className: "me-date", attrs: { "aria-hidden": "true" } }, [
                el("span", { text: start.toLocaleDateString(undefined, { month: "short" }) }),
                el("strong", { text: String(start.getDate()) }),
            ]),
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title", text: event.title }),
                el("p", { className: "list-meta", text: meta }),
                badges.length ? el("div", { className: "row" }, badges) : null,
            ]),
        ];
        if (!isPast) {
            const controls = new EventActions({
                eventId: event.id,
                myRsvp: event.rsvp === "going" ? "going" : event.rsvp === "maybe" ? "interested" : null,
                starred: event.saved,
                cancelled: event.is_cancelled,
                onChange: () => load(), // it may leave this tab
                onError: (message) => {
                    $("status").textContent = message;
                    $("status").classList.add("is-error");
                },
            });
            const actions = el("div", { className: "me-actions" }, [controls.element]);
            if (window.AddToCalendar && !event.is_cancelled) actions.append(window.AddToCalendar.menu(event));
            parts.push(actions);
        }
        return el("li", { className: `list-item me-item${event.is_cancelled ? " is-cancelled" : ""}` }, parts);
    }

    select(tab);
    load();
})();
