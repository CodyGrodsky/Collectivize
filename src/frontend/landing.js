"use strict";

// Landing / sign-in page (v1.1 spec 4.10). Signed-out visitors can reach
// only this page (and invite links); the server sends everything else here
// with ?next=<page they wanted>.

(() => {
    const { Api, Session } = window.Collectivize;
    const $ = (id) => document.getElementById(id);

    const wanted = new URLSearchParams(location.search).get("next");
    const next = Session.nextPage(wanted);
    const adminEntrance = new URLSearchParams(location.search).has("admin");

    // Already signed in: carry on to the page they asked for, or, with none
    // (e.g. the nav's wordmark link), show the home page without the sign-in
    // form, where the wordmark leads to Discover.
    Api.get("/api/session", {}, { redirectOnSignOut: false })
        .then(({ signed_in: signedIn }) => {
            if (!signedIn || adminEntrance) return; // /admin always shows the form
            if (wanted) location.replace(next);
            else showSignedIn();
        })
        .catch(() => {});

    function showSignedIn() {
        $("signin").hidden = true;
        const wordmark = document.querySelector(".cover-wordmark");
        const link = document.createElement("a");
        link.className = "cover-wordmark-link";
        link.href = "discover.html";
        wordmark.alt = "Collectivize: go to Discover";
        wordmark.replaceWith(link);
        link.append(wordmark);
    }

    // Sent here because the account was ended from somewhere else.
    const NOTICES = {
        deleted: "That account was deleted.",
        signed_out: "You were signed out.",
        expired: "Your sign-in ended. Sign in again.",
    };
    const notice = NOTICES[new URLSearchParams(location.search).get("notice")];
    if (notice) $("signin-message").textContent = notice;

    /** The one sign-in form. An ordinary email signs in at once. An admin
        account is asked for its password, then (when Duo is on) approved by
        a push to the admin's phone or a passcode. */
    class SignIn {
        constructor() {
            this.admin = false;
            $("signin").addEventListener("submit", (event) => {
                event.preventDefault();
                this.submit();
            });
            // A different name typed: start over.
            $("signin-email").addEventListener("input", () => this.setAdmin(false));
        }

        setAdmin(on) {
            this.admin = on;
            $("signin-admin").hidden = !on;
            if (!on) {
                $("signin-password").value = "";
                $("signin-passcode").value = "";
                $("signin-duo").hidden = true;
            }
        }

        say(text) {
            $("signin-message").textContent = text;
        }

        async submit() {
            const typed = $("signin-email").value.trim();
            if (!typed) {
                this.say("Enter your Northeastern email.");
                return;
            }
            $("signin-button").disabled = true;
            this.say("");
            try {
                if (this.admin) await this.adminSignIn(typed);
                else await this.signIn(typed);
            } catch (error) {
                this.say(error.message);
                if (error.data?.needs_passcode) {
                    $("signin-duo").hidden = false;
                    $("signin-passcode").focus();
                } else {
                    (this.admin ? $("signin-password") : $("signin-email")).focus();
                }
            } finally {
                $("signin-button").disabled = false;
            }
        }

        async signIn(email) {
            const answer = await Api.post("/api/signin", { email }, { redirectOnSignOut: false });
            if (!answer.admin) {
                location.replace(next);
                return;
            }
            this.setAdmin(true);
            this.say("This is an admin account. Enter its password.");
            $("signin-password").focus();
        }

        async adminSignIn(username) {
            const password = $("signin-password").value;
            if (!password) {
                this.say("Enter the admin password.");
                return;
            }
            const passcode = $("signin-passcode").value.trim();
            this.say("Checking… If Duo is on, approve the request on your phone.");
            await Api.post("/api/admin/signin", { username, password, ...(passcode ? { passcode } : {}) },
                           { redirectOnSignOut: false });
            location.replace("admin.html");
        }
    }

    new SignIn();
})();
