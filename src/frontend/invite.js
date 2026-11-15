"use strict";

// Invite landing page (v1.1 spec 4.11): /invite/<code> shows the inviter's
// profile card, the same card as everywhere else, plus sign-in. Signing in
// (or joining) through it connects you with the inviter. Reachable signed
// out, like the landing page.

(() => {
    const { Api, Ui } = window.Collectivize;
    const $ = (id) => document.getElementById(id);
    const code = new URLSearchParams(location.search).get("code") || "";
    let inviter = null;

    async function load() {
        try {
            const data = await Api.get("/api/invite", { code }, { redirectOnSignOut: false });
            inviter = data.inviter;
            const first = Ui.cleanName(inviter.name).split(" ")[0];
            $("invite-line").textContent = `${first} invited you to Collectivize.`;
            $("invite-card").replaceChildren(window.ProfileCard.render(inviter, { headingLevel: "h2" }));
        } catch (error) {
            $("invite-line").textContent = `${error.message}. You can still sign in.`;
            $("signin-button").textContent = "Sign in";
        }
    }

    new window.SignInForm({
        extra: () => ({ invite: inviter ? code : null }),
        done: () => location.replace(inviter ? Ui.profileHref(inviter.id) : "discover"),
    });

    load();
})();
