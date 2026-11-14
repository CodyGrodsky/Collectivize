"use strict";

// Calendar page (v1.1 spec 4.7): your personal calendar, not a master
// calendar. A month grid with a day panel, showing only what you're going
// to, said maybe to, saved or host, plus your organizations' events,
// colour-coded by status. Discovery happens on Discover: the day panel
// links there with that day already set ("Find events on …").
// The day panel has the same "I'm going" / "Maybe" / save buttons as
// Discover; changing them updates the calendar straight away.
//
// Built to keep working when things go wrong: if events can't load, the
// grid still works and a banner offers Retry; an answer to an old request
// is ignored; events with broken dates are skipped; long event lists are
// trimmed with "+N more"; server text is only ever inserted as text.

(() => {
    const { Api, EventActions } = window.Collectivize;

    const MONTH_FORMAT = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" });
    const DAY_FORMAT = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric" });
    const WEEKDAY_FORMAT = new Intl.DateTimeFormat("en-US", { weekday: "long" });
    const FULL_FORMAT = new Intl.DateTimeFormat("en-US", { dateStyle: "full" });
    const TIME_FORMAT = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

    const GRID_DAYS = 42; // six weeks, so the grid keeps its height
    const CHIPS_PER_CELL = 3;
    const MAX_SPAN_DAYS = 14; // a multi-day event is shown on at most this many days
    // One status per chip, strongest first (the legend's colours).
    const STATUS_ORDER = ["going", "maybe", "starred", "hosting", "my_org"];
    const STATUS_CLASS = { going: "is-going", maybe: "is-maybe", starred: "is-saved", hosting: "is-hosting", my_org: "is-my_org" };
    const STATUS_TEXT = { going: "going", maybe: "maybe", starred: "saved", hosting: "hosting", my_org: "your org" };
    const FIND_FORMAT = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "short", day: "numeric" });

    const $ = (id) => document.getElementById(id);

    const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
        && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

    const dayKey = (date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

    const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());

    const cleanName = (name) => String(name || "")
        .replace(/^\[Demo\]\s*/, "").replace(/\s*\(demo[^)]*\)$/, "");

    /** A server entry, checked and normalized; null if it can't be shown. */
    function readEntry(raw) {
        if (!raw || typeof raw !== "object" || typeof raw.id !== "string") return null;
        const start = new Date(raw.starts_at);
        const end = new Date(raw.ends_at);
        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
        const relations = Array.isArray(raw.relations) ? raw.relations.filter((r) => typeof r === "string") : [];
        return {
            id: raw.id,
            title: typeof raw.title === "string" && raw.title.trim() ? raw.title : "Untitled event",
            start,
            end: end > start ? end : start,
            location: typeof raw.location === "string" ? raw.location : "",
            host: cleanName(raw.host),
            relations,
            cancelled: raw.is_cancelled === true,
            draft: raw.is_draft === true,
            status: STATUS_ORDER.find((r) => relations.includes(r)) || "my_org",
            myRsvp: relations.includes("going") ? "going" : relations.includes("maybe") ? "interested" : null,
            starred: relations.includes("starred"),
        };
    }

    class MonthCalendar {
        constructor() {
            this.grid = $("cal-grid");
            this.title = $("cal-title");
            this.dayEyebrow = $("day-eyebrow");
            this.dayTitle = $("day-title");

            const start = MonthCalendar.dateFromUrl() || new Date();
            this.selected = start;
            this.shown = new Date(start.getFullYear(), start.getMonth(), 1);
            this.byDay = new Map(); // day key -> entries
            this.request = 0;

            $("cal-prev").addEventListener("click", () => this.step(-1));
            $("cal-next").addEventListener("click", () => this.step(1));
            $("cal-today").addEventListener("click", () => this.select(new Date()));
            $("cal-retry").addEventListener("click", () => this.load());
        }

        async start() {
            this.render();
            this.load();
        }

        step(months) {
            this.shown = new Date(this.shown.getFullYear(), this.shown.getMonth() + months, 1);
            this.render();
            this.load();
        }

        select(date) {
            const monthChanged = date.getMonth() !== this.shown.getMonth()
                || date.getFullYear() !== this.shown.getFullYear();
            this.selected = date;
            this.shown = new Date(date.getFullYear(), date.getMonth(), 1);
            this.render();
            if (monthChanged) this.load();
        }

        gridStart() {
            const first = new Date(this.shown);
            first.setDate(1 - first.getDay());
            return startOfDay(first);
        }

        // --- loading -----------------------------------------------------------

        async load() {
            const id = ++this.request;
            const start = this.gridStart();
            const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + GRID_DAYS);
            this.setDayStatus("Loading events…");
            try {
                const data = await Api.get("/api/my-calendar", {
                    start: MonthCalendar.iso(start), end: MonthCalendar.iso(end),
                });
                if (id !== this.request) return; // a newer request has started
                this.byDay = MonthCalendar.index(data, start, end);
                this.hideBanner();
                this.setDayStatus("");
            } catch (error) {
                if (id !== this.request) return;
                this.byDay = new Map();
                this.showBanner(error);
                this.setDayStatus("");
            }
            this.render();
        }

        /** ?date=YYYY-MM-DD opens that day; anything malformed is ignored. */
        static dateFromUrl() {
            const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(new URLSearchParams(location.search).get("date") || "");
            if (!match) return null;
            const [year, month, day] = match.slice(1).map(Number);
            const date = new Date(year, month - 1, day);
            const valid = date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
            return valid && year >= 2000 && year <= 2100 ? date : null;
        }

        /** YYYY-MM-DD of a local date (for Discover's date filter). */
        static isoDate(date) {
            const pad = (n) => String(n).padStart(2, "0");
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
        }

        /** Local time with its offset, e.g. 2026-11-01T00:00:00-04:00. */
        static iso(date) {
            const pad = (n) => String(Math.abs(Math.trunc(n))).padStart(2, "0");
            const offset = -date.getTimezoneOffset();
            const sign = offset >= 0 ? "+" : "-";
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
                + `T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
                + `${sign}${pad(offset / 60)}:${pad(offset % 60)}`;
        }

        /** Put every event on each day it covers (within the grid, max MAX_SPAN_DAYS). */
        static index(data, gridStart, gridEnd) {
            const byDay = new Map();
            const add = (entry) => {
                let day = startOfDay(entry.start < gridStart ? gridStart : entry.start);
                for (let i = 0; i < MAX_SPAN_DAYS && day < gridEnd && day < entry.end; i += 1) {
                    const key = dayKey(day);
                    if (!byDay.has(key)) byDay.set(key, []);
                    byDay.get(key).push(entry);
                    day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
                }
            };
            const entries = (Array.isArray(data?.entries) ? data.entries : []).map(readEntry).filter(Boolean);
            for (const entry of entries) add(entry);
            for (const list of byDay.values()) list.sort((a, b) => a.start - b.start);
            return byDay;
        }

        // --- drawing -------------------------------------------------------------

        render() {
            this.title.textContent = MONTH_FORMAT.format(this.shown);
            this.renderGrid();
            this.renderDay();
        }

        renderGrid() {
            const today = new Date();
            const first = this.gridStart();
            const cells = [];
            for (let i = 0; i < GRID_DAYS; i += 1) {
                const date = new Date(first.getFullYear(), first.getMonth(), first.getDate() + i);
                cells.push(this.cell(date, today));
            }
            this.grid.replaceChildren(...cells);
        }

        cell(date, today) {
            const cell = document.createElement("button");
            cell.type = "button";
            cell.className = "cal-cell";
            if (date.getMonth() !== this.shown.getMonth()) cell.classList.add("is-outside");
            if (sameDay(date, today)) {
                cell.classList.add("is-today");
                cell.setAttribute("aria-current", "date");
            }
            cell.setAttribute("aria-pressed", String(sameDay(date, this.selected)));
            const number = document.createElement("span");
            number.className = "cal-date";
            number.textContent = String(date.getDate());
            cell.append(number);

            const entries = this.byDay.get(dayKey(date)) || [];
            const label = [FULL_FORMAT.format(date)];
            if (entries.length) {
                label.push(entries.map((e) => `${e.title}, ${STATUS_TEXT[e.status]}`).join("; "));
            }
            cell.setAttribute("aria-label", label.join(": "));

            if (entries.length) {
                const chips = document.createElement("span");
                chips.className = "cal-chips";
                chips.setAttribute("aria-hidden", "true");
                for (const entry of entries.slice(0, CHIPS_PER_CELL)) chips.append(MonthCalendar.chip(entry));
                const hidden = Math.max(0, entries.length - CHIPS_PER_CELL);
                if (hidden) {
                    const more = document.createElement("span");
                    more.className = "cal-more";
                    more.textContent = `+${hidden} more`;
                    chips.append(more);
                }
                cell.append(chips);
            }
            cell.addEventListener("click", () => this.select(date));
            return cell;
        }

        static chip(entry) {
            const chip = document.createElement("span");
            chip.className = `cal-chip ${STATUS_CLASS[entry.status]}`;
            if (entry.cancelled) chip.classList.add("is-cancelled");
            chip.textContent = entry.title;
            return chip;
        }

        renderDay() {
            const isToday = sameDay(this.selected, new Date());
            this.dayEyebrow.textContent = isToday ? "Today" : WEEKDAY_FORMAT.format(this.selected);
            this.dayTitle.textContent = DAY_FORMAT.format(this.selected);
            const entries = this.byDay.get(dayKey(this.selected)) || [];
            this.fillList($("day-list"), $("day-empty"), entries);
            // Discovery lives on Discover: open it with this day already set.
            const find = $("day-find");
            find.textContent = `Find events on ${FIND_FORMAT.format(this.selected)}`;
            find.href = `discover.html?${new URLSearchParams({ date: MonthCalendar.isoDate(this.selected) })}`;
        }

        fillList(list, empty, entries) {
            list.replaceChildren(...entries.map((entry) => this.dayEvent(entry)));
            empty.hidden = entries.length > 0;
        }

        dayEvent(entry) {
            const item = document.createElement("li");
            item.className = "day-event";
            const time = document.createElement("div");
            time.className = "day-event-time";
            time.textContent = `${TIME_FORMAT.format(entry.start)}–${TIME_FORMAT.format(entry.end)}`
                + (entry.location ? ` · ${entry.location}` : "");
            const title = document.createElement("p");
            title.className = "day-event-title";
            title.textContent = entry.title;
            item.append(time, title);
            if (entry.host) {
                const host = document.createElement("p");
                host.className = "day-event-host";
                host.textContent = `Hosted by ${entry.host}`;
                item.append(host);
            }
            const tags = MonthCalendar.tags(entry);
            if (tags.childElementCount) item.append(tags);
            if (!entry.draft) {
                const controls = new EventActions({
                    eventId: entry.id,
                    myRsvp: entry.myRsvp,
                    starred: entry.starred,
                    cancelled: entry.cancelled,
                    onChange: () => this.load(), // moves it in or out of "My events"
                    onError: (message) => this.setDayStatus(message, { error: true }),
                });
                item.append(controls.element);
                if (window.AddToCalendar && !entry.cancelled) {
                    item.append(window.AddToCalendar.menu({
                        id: entry.id, title: entry.title, location: entry.location, description: "",
                        starts_at: entry.start.toISOString(), ends_at: entry.end.toISOString(),
                    }));
                }
            }
            return item;
        }

        static tags(entry) {
            const tags = document.createElement("div");
            tags.className = "day-event-tags";
            const add = (text, muted = false) => {
                const tag = document.createElement("span");
                tag.className = `day-tag${muted ? " is-muted" : ""}`;
                tag.textContent = text;
                tags.append(tag);
            };
            if (entry.relations.includes("going")) add("Going");
            else if (entry.relations.includes("maybe")) add("Maybe");
            if (entry.relations.includes("starred")) add("Saved");
            if (entry.relations.includes("hosting")) add("Hosting");
            if (entry.relations.includes("my_org")) add("Your org");
            if (entry.cancelled) add("Cancelled", true);
            if (entry.draft) add("Draft", true);
            return tags;
        }

        // --- messages ------------------------------------------------------------

        showBanner(error) {
            $("cal-banner-text").textContent = `Couldn't load events: ${error instanceof Error ? error.message : "unknown problem"}`;
            $("cal-banner").hidden = false;
        }

        hideBanner() {
            $("cal-banner").hidden = true;
        }

        setDayStatus(text, { error = false } = {}) {
            const status = $("day-status");
            status.textContent = text;
            status.classList.toggle("is-error", error);
        }
    }

    new MonthCalendar().start();
})();
