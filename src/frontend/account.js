"use strict";

// The Account page: your sign-in email, how far along you are (signed in,
// profile, connections, communities, first RSVP), your data settings,
// signing out everywhere and deleting your account. Everything here acts on
// the signed-in person only; the server never takes "who" from the page.

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el } = Ui;
    const $ = (id) => document.getElementById(id);
    const DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

    class AccountPage {
        async start() {
            try {
                this.show(await Api.get("/api/account"));
            } catch {
                return; // signed out: Api already sent us to sign-in
            }
            $("account").hidden = false;
            $("ai-history").addEventListener("change", (e) => this.set("ai_uses_history", e.target));
            $("analytics").addEventListener("change", (e) => this.set("analytics_consent", e.target));
            $("signout-all").addEventListener("click", () => this.signOutEverywhere());
            $("delete-form").addEventListener("submit", (event) => {
                event.preventDefault();
                this.remove();
            });
        }

        show(account) {
            this.account = account;
            const { settings, counts, profile } = account;
            $("account-summary").textContent =
                `${account.email || "No email"} · joined ${DAY.format(new Date(account.created_at))}`;
            $("ai-history").checked = settings.ai_uses_history;
            $("analytics").checked = settings.analytics_consent;
            $("sessions-text").textContent = account.sessions === 1
                ? "You're signed in on this browser only."
                : `You're signed in on ${account.sessions} browsers.`;
            $("steps").replaceChildren(
                AccountPage.step(true, "Signed in with your Northeastern email", null),
                AccountPage.step(Boolean(profile.bio || profile.about), "Fill in your profile", "profile"),
                AccountPage.step(counts.connections > 0, "Connect with someone you know", "network"),
                AccountPage.step(counts.orgs > 0, "Join a community", "discover"),
                AccountPage.step(counts.rsvps > 0, "RSVP to your first event", "discover"),
            );
        }

        static step(done, text, href) {
            const title = el("p", { className: "list-title" },
                [done || !href ? document.createTextNode(text) : el("a", { text, attrs: { href } })]);
            return el("li", { className: "list-item" }, [
                el("span", { text: done ? "✓" : "○", attrs: { "aria-hidden": "true" } }),
                el("div", { className: "list-main" }, [
                    title, el("p", { className: "list-meta", text: done ? "Done" : "Not yet" }),
                ]),
            ]);
        }

        async set(key, box) {
            const status = $("settings-status");
            box.disabled = true;
            try {
                this.show(await Api.post("/api/account", { [key]: box.checked }));
                status.textContent = "Saved.";
                status.classList.remove("is-error");
            } catch (error) {
                box.checked = !box.checked; // put it back: nothing was saved
                status.textContent = error.message;
                status.classList.add("is-error");
            } finally {
                box.disabled = false;
            }
        }

        async signOutEverywhere() {
            try {
                await Api.post("/api/account/signout-all", {}, { redirectOnSignOut: false });
            } finally {
                location.replace("/");
            }
        }

        async remove() {
            const status = $("delete-status");
            try {
                await Api.post("/api/account/delete", { confirm: $("confirm").value },
                               { redirectOnSignOut: false });
                location.replace("/");
            } catch (error) {
                status.textContent = error.message;
                status.classList.add("is-error");
            }
        }
    }

    new AccountPage().start();
})();
