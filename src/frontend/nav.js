// Shared sidebar navigation, built on every page that loads it. With
// shared.js on the page it also adds the Admin link (platform admins only),
// Sign out, and the "Viewing as … (admin)" banner during view-as.
//
// Usage: in a page's <head>, after its own stylesheet, add
//     <link rel="stylesheet" href="nav.css">
//     <script src="nav.js"></script>
// Load it without `defer` so the open/closed state is set before the page
// paints (no jump). To add a page to the nav, add an entry to PAGES.

(() => {
    const PAGES = [
        { label: "Discover", href: "discover.html", icon: "discover" },
        { label: "Calendar", href: "calendar.html", icon: "calendar" },
        { label: "My Events", href: "my-events.html", icon: "bookmark" },
        { label: "Network", href: "network.html", icon: "graph" },
        { label: "Profile", href: "profile.html", icon: "profile" },
    ];
    const ADMIN_PAGE = { label: "Admin", href: "admin.html", icon: "shield" };

    // Inline icon paths (24x24, stroked) so the nav needs no extra files.
    const ICONS = {
        sidebar: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
        home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
        graph: '<circle cx="6" cy="7" r="2.5"/><circle cx="18" cy="7" r="2.5"/>'
            + '<circle cx="12" cy="17.5" r="2.5"/><path d="M8.5 7h7M7.1 9.3l3.8 6M16.9 9.3l-3.8 6"/>',
        calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
        discover: '<circle cx="12" cy="12" r="8.5"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
        profile: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-3.5 3.6-5.5 7-5.5s6.2 2 7 5.5"/>',
        bookmark: '<path d="M7 4h10a1 1 0 0 1 1 1v15l-6-4-6 4V5a1 1 0 0 1 1-1z"/>',
        shield: '<path d="M12 3.5 19 6v5.5c0 4.3-2.9 7.6-7 9-4.1-1.4-7-4.7-7-9V6z"/>',
        signout: '<path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 8l-4 4 4 4M6 12h10"/>',

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

            this.list = list;
            const signOut = document.createElement("button");
            signOut.type = "button";
            signOut.className = "sidebar-link sidebar-signout";
            signOut.innerHTML = Sidebar.icon("signout");
            const signOutLabel = document.createElement("span");
            signOutLabel.className = "sidebar-label";
            signOutLabel.textContent = "Sign out";
            signOut.append(signOutLabel);
            signOut.addEventListener("click", () => window.Collectivize?.Session.signOut());

            nav.append(this.toggle, list, signOut);
            document.body.prepend(nav, scrim);
            this.showAccount();
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

        /** The Admin link for platform admins, and the view-as banner. */
        async showAccount() {
            const session = window.Collectivize?.Session;
            if (!session) return;
            let me;
            try {
                me = await session.current();
            } catch {
                return; // signed out: the page's own calls redirect to sign-in
            }
            if (me.is_platform_admin) this.list.append(this.makeLink(ADMIN_PAGE));
            if (me.viewing_as) Sidebar.banner(me.viewing_as.name);
        }

        /** Persistent while a platform admin views the app as someone. */
        static banner(name) {
            const bar = document.createElement("div");
            bar.className = "viewas-banner";
            bar.setAttribute("role", "status");
            const text = document.createElement("span");
            text.textContent = `Viewing as ${name} (admin) · read-only`;
            const exit = document.createElement("button");
            exit.type = "button";
            exit.textContent = "Exit";
            exit.addEventListener("click", async () => {
                exit.disabled = true;
                try {
                    await window.Collectivize.Api.post("/api/admin/view-as", { person_id: null });
                } finally {
                    location.href = "admin.html";
                }
            });
            bar.append(text, exit);
            document.body.prepend(bar);
            document.documentElement.classList.add("has-viewas");
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
