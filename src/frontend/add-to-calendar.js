"use strict";

// "Add to calendar" (v1.1 spec 4.8): one-way only, no sign-in, no sync.
// - Google Calendar: a prefilled event template link.
// - Outlook (Microsoft 365, what Northeastern accounts use): a compose link.
// - Apple Calendar (and desktop Outlook): an .ics file made here (RFC 5545).
// Times are sent in UTC, so each app shows them in the viewer's own zone
// (America/New_York for Northeastern). Every field is URL-encoded or escaped.
//
// Usage: AddToCalendar.menu({ id, title, starts_at, ends_at, location, description })

(() => {
    const { Ui } = window.Collectivize;
    const { el } = Ui;

    const pad = (n) => String(n).padStart(2, "0");

    /** 20261121T230000Z: the UTC form Google and .ics use. */
    function utcStamp(date) {
        return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}`
            + `T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`;
    }

    /** 2026-11-21T23:00:00Z: the ISO form Outlook's compose link uses. */
    const isoUtc = (date) => date.toISOString().replace(/\.\d{3}Z$/, "Z");

    function details(event) {
        const text = [event.description, "Found on Collectivize."].filter(Boolean).join("\n\n");
        return text.length > 1500 ? `${text.slice(0, 1497)}…` : text;
    }

    function googleUrl(event, start, end) {
        const params = new URLSearchParams({
            action: "TEMPLATE",
            text: event.title,
            dates: `${utcStamp(start)}/${utcStamp(end)}`,
            details: details(event),
            location: event.location || "",
        });
        return `https://calendar.google.com/calendar/render?${params}`;
    }

    function outlookUrl(event, start, end) {
        const params = new URLSearchParams({
            path: "/calendar/action/compose",
            rru: "addevent",
            subject: event.title,
            startdt: isoUtc(start),
            enddt: isoUtc(end),
            body: details(event),
            location: event.location || "",
        });
        return `https://outlook.office.com/calendar/0/deeplink/compose?${params}`;
    }

    /** RFC 5545 text: escape \ ; , and newlines. */
    const icsText = (value) => String(value || "")
        .replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");

    /** Lines of at most 75 octets, continued with a leading space. */
    function fold(line) {
        const bytes = new TextEncoder();
        const parts = [];
        let current = "";
        for (const char of line) {
            const limit = parts.length ? 74 : 75; // continuation lines start with a space
            if (bytes.encode(current + char).length > limit) {
                parts.push(current);
                current = char;
            } else {
                current += char;
            }
        }
        parts.push(current);
        return parts.join("\r\n ");
    }

    function ics(event, start, end) {
        const lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Collectivize//Demo//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            `UID:${icsText(event.id)}@collectivize.example`,
            `DTSTAMP:${utcStamp(new Date())}`,
            `DTSTART:${utcStamp(start)}`,
            `DTEND:${utcStamp(end)}`,
            `SUMMARY:${icsText(event.title)}`,
            `DESCRIPTION:${icsText(details(event))}`,
            `LOCATION:${icsText(event.location)}`,
            "END:VEVENT",
            "END:VCALENDAR",
        ];
        return `${lines.map(fold).join("\r\n")}\r\n`;
    }

    function download(event, start, end) {
        const blob = new Blob([ics(event, start, end)], { type: "text/calendar;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = el("a", { attrs: { href: url, download: `${(event.title || "event").replace(/[^\w-]+/g, "-").slice(0, 60)}.ics` } });
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    const AddToCalendar = Object.freeze({
        menu(event) {
            const start = new Date(event.starts_at);
            const end = new Date(event.ends_at);
            if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return document.createTextNode("");
            const external = (text, href) => el("a", { className: "atc-item", text, attrs: { href, target: "_blank", rel: "noopener noreferrer" } });
            const apple = el("button", { className: "atc-item", text: "Apple / other (.ics)", attrs: { type: "button" } });
            apple.addEventListener("click", () => download(event, start, end));
            const box = el("details", { className: "atc" }, [
                el("summary", { className: "atc-button", text: "Add to calendar" }),
                el("div", { className: "atc-menu" }, [
                    external("Google Calendar", googleUrl(event, start, end)),
                    external("Outlook (Northeastern)", outlookUrl(event, start, end)),
                    apple,
                ]),
            ]);
            box.addEventListener("click", (e) => e.stopPropagation());
            return box;
        },
        // Exposed for checking the output.
        _internals: { googleUrl, outlookUrl, ics, utcStamp },
    });

    // One listener for every menu: close the open ones on a tap elsewhere.
    document.addEventListener("pointerdown", (e) => {
        for (const open of document.querySelectorAll("details.atc[open]")) {
            if (!open.contains(e.target)) open.open = false;
        }
    });

    window.AddToCalendar = AddToCalendar;
})();
