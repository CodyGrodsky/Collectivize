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
            $("email-form").addEventListener("submit", (event) => {
                event.preventDefault();
                this.changeEmail();
            });
            // A different address typed: its code has to be asked for again.
            $("email-new").addEventListener("input", () => this.emailStep(null));
        }

        /** `pending`: the new address a code was sent to, or null to start over. */
        emailStep(pending) {
            this.pendingEmail = pending;
            $("email-code-field").hidden = !pending;
            $("email-button").textContent = pending ? "Confirm new email" : "Send code";
            if (!pending) $("email-code").value = "";
        }

        /** Changing your sign-in email: a code goes to the new address, and
            typing it back makes the change. */
        async changeEmail() {
            const status = $("email-status");
            const email = $("email-new").value.trim();
            status.classList.remove("is-error");
            if (!email) {
                status.textContent = "Enter the new email.";
                return;
            }
            $("email-button").disabled = true;
            try {
                if (!this.pendingEmail) {
                    const answer = await Api.post("/api/account/email", { email });
                    this.emailStep(answer.email);
                    status.textContent = answer.emailed
                        ? `We emailed a code to ${answer.email}. Enter it to confirm the change.`
                        : "We couldn't email a code to that address. Ask whoever runs this site for the code, then enter it here.";
                    $("email-code").focus();
                } else {
                    const account = await Api.post("/api/account/email/verify",
                        { email: this.pendingEmail, code: $("email-code").value.trim() });
                    this.show(account);
                    this.emailStep(null);
                    $("email-new").value = "";
                    status.textContent = "Your sign-in email was changed. Your other browsers were signed out.";
                }
            } catch (error) {
                status.textContent = error.message;
                status.classList.add("is-error");
            } finally {
                $("email-button").disabled = false;
            }
        }

        show(account) {
            this.account = account;
            const { settings, counts, profile } = account;
            $("account-summary").textContent =
                `${account.email || "No email"} · joined ${DAY.format(new Date(account.created_at))}`;
            $("email-current").textContent = `You sign in with ${account.email || "no email"}.`;
            $("email-panel").hidden = account.is_platform_admin; // an admin's is set by the owner
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
