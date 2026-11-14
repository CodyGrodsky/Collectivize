"use strict";

// Calendar page: an empty month view and day panel. No events yet; this
// only builds the grid, moves between months and selects a day.

(() => {
    const MONTH_FORMAT = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" });
    const DAY_FORMAT = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric" });
    const WEEKDAY_FORMAT = new Intl.DateTimeFormat("en-US", { weekday: "long" });
    const FULL_FORMAT = new Intl.DateTimeFormat("en-US", { dateStyle: "full" });

    const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
        && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

    class MonthCalendar {
        constructor() {
            this.grid = document.getElementById("cal-grid");
            this.title = document.getElementById("cal-title");
            this.dayEyebrow = document.getElementById("day-eyebrow");
            this.dayTitle = document.getElementById("day-title");

            const today = new Date();
            this.selected = today;
            this.shown = new Date(today.getFullYear(), today.getMonth(), 1);

            document.getElementById("cal-prev").addEventListener("click", () => this.step(-1));
            document.getElementById("cal-next").addEventListener("click", () => this.step(1));
            document.getElementById("cal-today").addEventListener("click", () => this.select(new Date()));
        }

        step(months) {
            this.shown = new Date(this.shown.getFullYear(), this.shown.getMonth() + months, 1);
            this.render();
        }

        select(date) {
            this.selected = date;
            this.shown = new Date(date.getFullYear(), date.getMonth(), 1);
            this.render();
        }

        render() {
            this.title.textContent = MONTH_FORMAT.format(this.shown);
            this.renderGrid();
            this.renderDay();
        }

        // Always six weeks, starting on the Sunday on or before the 1st, so
        // the grid keeps the same height from month to month.
        renderGrid() {
            const today = new Date();
            const first = new Date(this.shown);
            first.setDate(1 - first.getDay());

            const cells = [];
            for (let i = 0; i < 42; i++) {
                const date = new Date(first.getFullYear(), first.getMonth(), first.getDate() + i);
                const cell = document.createElement("button");
                cell.type = "button";
                cell.className = "cal-cell";
                if (date.getMonth() !== this.shown.getMonth()) cell.classList.add("is-outside");
                if (sameDay(date, today)) {
                    cell.classList.add("is-today");
                    cell.setAttribute("aria-current", "date");
                }
                cell.setAttribute("aria-pressed", String(sameDay(date, this.selected)));
                cell.setAttribute("aria-label", FULL_FORMAT.format(date));
                cell.innerHTML = `<span class="cal-date">${date.getDate()}</span>`;
                cell.addEventListener("click", () => this.select(date));
                cells.push(cell);
            }
            this.grid.replaceChildren(...cells);
        }

        renderDay() {
            const isToday = sameDay(this.selected, new Date());
            this.dayEyebrow.textContent = isToday ? "Today" : WEEKDAY_FORMAT.format(this.selected);
            this.dayTitle.textContent = DAY_FORMAT.format(this.selected);
        }
    }

    new MonthCalendar().render();
})();
