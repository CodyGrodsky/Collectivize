// Shared sidebar navigation, built on every page that loads it.
//
// Usage: in a page's <head>, after its own stylesheet, add
//     <link rel="stylesheet" href="nav.css">
//     <script src="nav.js"></script>
// Load it without `defer` so the open/closed state is set before the page
// paints (no jump). To add a page to the nav, add an entry to PAGES.

(() => {
    const PAGES = [
        { label: "Home", href: "index.html", icon: "home" },
        { label: "Calendar", href: "calendar.html", icon: "calendar" },
        { label: "Discover", href: "discover.html", icon: "discover" },
        { label: "Network", href: "network.html", icon: "graph" },
        { label: "Profile", href: "profile.html", icon: "profile" },
        { label: "Demo", href: "main.html", icon: "demo" },
    ];

    // Inline icon paths (24x24, stroked) so the nav needs no extra files.
    const ICONS = {
        sidebar: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
        home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
        graph: '<circle cx="6" cy="7" r="2.5"/><circle cx="18" cy="7" r="2.5"/>'
            + '<circle cx="12" cy="17.5" r="2.5"/><path d="M8.5 7h7M7.1 9.3l3.8 6M16.9 9.3l-3.8 6"/>',
        calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
        discover: '<circle cx="12" cy="12" r="8.5"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
        profile: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-3.5 3.6-5.5 7-5.5s6.2 2 7 5.5"/>',
        demo: '<path d="M9.5 3.5h5M10.5 3.5v5.5L5.3 18.2A1.5 1.5 0 0 0 6.6 20.5h10.8a1.5 1.5 0 0 0 1.3-2.3L13.5 9V3.5M7.8 14.5h8.4"/>',
    };

    const STORAGE_KEY = "collectivize.nav.open";
    const PHONE = window.matchMedia("(max-width: 640px)");

    class Sidebar {
        constructor(pages) {
            this.pages = pages;
            this.root = document.documentElement;
            // Phones always start closed; elsewhere, remember the last choice.
            this.setOpen(!PHONE.matches && Sidebar.loadOpen(), { save: false });
        }

        get isOpen() {
            return this.root.dataset.nav === "open";
        }

        setOpen(open, { save = true } = {}) {
            this.root.dataset.nav = open ? "open" : "closed";
            if (this.toggle) {
                this.toggle.setAttribute("aria-expanded", String(open));
                this.toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
            }
            if (save && !PHONE.matches) Sidebar.saveOpen(open);
        }

        mount() {
            const nav = document.createElement("nav");
            nav.className = "sidebar";
            nav.setAttribute("aria-label", "Main");

            this.toggle = document.createElement("button");
            this.toggle.type = "button";
            this.toggle.className = "sidebar-toggle";
            this.toggle.setAttribute("aria-controls", "sidebar-links");
            this.toggle.innerHTML = Sidebar.icon("sidebar");
            this.toggle.addEventListener("click", () => this.setOpen(!this.isOpen));

            const list = document.createElement("ul");
            list.className = "sidebar-links";
            list.id = "sidebar-links";
            for (const page of this.pages) list.append(this.makeLink(page));

            const scrim = document.createElement("div");
            scrim.className = "sidebar-scrim";
            scrim.addEventListener("click", () => this.setOpen(false));

            nav.append(this.toggle, list);
            document.body.prepend(nav, scrim);
            this.setOpen(this.isOpen, { save: false });

            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape" && PHONE.matches && this.isOpen) {
                    this.setOpen(false);
                    this.toggle.focus();
                }
            });
            PHONE.addEventListener("change", () => {
                this.setOpen(!PHONE.matches && Sidebar.loadOpen(), { save: false });
            });
        }

        makeLink(page) {
            const link = document.createElement("a");
            link.className = "sidebar-link";
            link.href = page.href;
            if (Sidebar.isCurrent(page.href)) link.setAttribute("aria-current", "page");

            const label = document.createElement("span");
            label.className = "sidebar-label";
            label.textContent = page.label;

            link.innerHTML = Sidebar.icon(page.icon);
            link.append(label);
            const item = document.createElement("li");
            item.append(link);
            return item;
        }

        static icon(name) {
            return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name]}</svg>`;
        }

        static isCurrent(href) {
            const here = window.location.pathname.replace(/\/$/, "/index.html");
            return new URL(href, window.location.href).pathname === here;
        }

        // Storage can be blocked (private windows, strict settings); the nav
        // then just starts closed on every page.
        static loadOpen() {
            try {
                return window.localStorage.getItem(STORAGE_KEY) === "true";
            } catch {
                return false;
            }
        }

        static saveOpen(open) {
            try {
                window.localStorage.setItem(STORAGE_KEY, String(open));
            } catch {
                // Not remembered; nothing else to do.
            }
        }
    }

    const sidebar = new Sidebar(PAGES);
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => sidebar.mount());
    } else {
        sidebar.mount();
    }
})();
