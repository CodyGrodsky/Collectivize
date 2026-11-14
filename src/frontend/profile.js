"use strict";

// Profile page: your own page (the person chosen in "Viewing as"), with
// your network bubble in it, and an edit mode for everything about it:
// name and text, Top 3, website, colors, layout (which sections show and
// in what order) and who can see what.
//
// The page is drawn by ProfileRenderer (profile-render.js), the same code
// the Network tab uses for other people's pages, so what you see under
// "View as visitor" is what they get. Data comes from the demo server
// (network-data.js); edits are saved there, not in the browser.
//
// Clicking someone or a community in your bubble opens them on the Network tab.

(() => {
    const { Session, NetworkData, NetworkBubble, ProfileEditor, Profile } = window.Collectivize;
    const { el, ProfileRenderer } = Profile;

    const $ = (id) => document.getElementById(id);

    class ProfilePage {
        constructor(root) {
            this.root = root;
            this.viewer = "";
            this.data = null;
            this.asVisitor = false;
            this.request = 0;
            this.renderer = new ProfileRenderer();
            this.bubble = new NetworkBubble({ onOpen: (id) => ProfilePage.openOnNetwork(id) });

            this.root.addEventListener("click", (event) => this.onClick(event));
            $("profile-retry").addEventListener("click", () => this.load());
            this.start();
        }

        static openOnNetwork(id) {
            if (id) window.location.href = `network.html?center=${encodeURIComponent(id)}`;
        }

        async start() {
            try {
                this.viewer = (await Session.current()).id; // whoever is signed in
            } catch (error) {
                this.fail(error);
                return;
            }
            this.load();
        }

        async load() {
            const request = ++this.request;
            $("profile-banner").hidden = true;
            if (!this.viewer) {
                this.data = null;
                document.title = "Profile · Collectivize";
                this.root.replaceChildren(el("div", { className: "page-message" }, [
                    el("h2", { text: "Sign in" }),
                    el("p", { text: "Sign in to see and edit your page." }),
                ]));
                return;
            }
            try {
                const data = await NetworkData.fetch(this.viewer, "");
                if (request !== this.request) return; // a newer request took over
                this.data = data;
                this.show();
            } catch (error) {
                if (request === this.request) this.fail(error);
            }
        }

        fail(error) {
            $("profile-banner-text").textContent = error instanceof Error ? error.message : "Your page didn't load.";
            $("profile-banner").hidden = false;
        }

        show() {
            const { profile } = this.data;
            document.title = `${ProfileRenderer.shownName(profile)} · Collectivize`;
            this.bubble.draw(this.data);
            // "View as visitor" is the same page without the owner's hints.
            const mode = this.asVisitor ? "preview" : "self";
            const view = this.asVisitor ? { ...profile, is_self: false, connection: "none" } : profile;
            const surface = this.renderer.render(view, mode, { network: this.bubble.element });
            this.root.replaceChildren(this.ownerBar(), surface);
        }

        ownerBar() {
            const hidden = !this.data.profile.sections.includes("network");
            const audience = {
                network: "Everyone signed in can see your page.",
                connections: "Only your connections can see your page.",
                private: "Only you can see your page.",
            }[this.data.profile.settings?.profile_visibility] || "";
            const note = this.asVisitor
                ? "You're seeing your page the way visitors do."
                : `This is your page. ${audience}${hidden ? " Your network bubble is hidden from it." : ""}`;
            return el("div", { className: "owner-bar" }, [
                el("p", { className: "owner-note", text: note }),
                el("div", { className: "owner-actions" }, [
                    el("button", {
                        className: "page-button", text: "View as visitor",
                        attrs: { type: "button", "data-action": "toggle-visitor", "aria-pressed": String(this.asVisitor) },
                    }),
                    el("button", {
                        className: "page-button page-button-solid", text: "Edit page",
                        attrs: { type: "button", "data-action": "edit", hidden: this.asVisitor },
                    }),
                ]),
            ]);
        }

        onClick(event) {
            const button = event.target.closest("[data-action]");
            if (!button || !this.root.contains(button)) return;
            if (button.dataset.action === "toggle-visitor") {
                this.asVisitor = !this.asVisitor;
                this.show();
                this.root.querySelector('[data-action="toggle-visitor"]')?.focus();
            } else if (button.dataset.action === "edit") {
                this.edit();
            }
        }

        edit() {
            document.title = "Edit profile · Collectivize";
            const viewer = this.viewer; // the save belongs to whoever opened the editor
            new ProfileEditor({
                view: this.data.profile,
                renderer: this.renderer,
                save: (changes) => NetworkData.save(viewer, changes),
                onSaved: (data) => {
                    if (viewer !== this.viewer) return;
                    this.data = data;
                    this.show();
                },
                onCancel: () => this.show(),
            }).mount(this.root);
        }
    }

    new ProfilePage($("profile-root"));
})();
