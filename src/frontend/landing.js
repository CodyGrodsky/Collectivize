"use strict";

// Landing / sign-in page (v1.1 spec 4.10). Signed-out visitors can reach
// only this page (and invite links); the server sends everything else here
// with ?next=<page they wanted>.

(() => {
    const { Api, Session } = window.Collectivize;
    const $ = (id) => document.getElementById(id);

    const next = Session.nextPage(new URLSearchParams(location.search).get("next"));

    // Already signed in: carry on.
    Api.get("/api/session", {}, { redirectOnSignOut: false })
        .then(({ signed_in: signedIn }) => { if (signedIn) location.replace(next); })
        .catch(() => {});

    // Sent here because the account was ended from somewhere else.
    const NOTICES = {
        deleted: "That account was deleted.",
        signed_out: "You were signed out.",
        expired: "Your sign-in ended. Sign in again.",
    };
    const notice = NOTICES[new URLSearchParams(location.search).get("notice")];
    if (notice) $("signin-message").textContent = notice;

    $("signin").addEventListener("submit", async (event) => {
        event.preventDefault();
        const email = $("signin-email").value.trim();
        const message = $("signin-message");
        if (!email) {
            message.textContent = "Enter your Northeastern email.";
            return;
        }
        $("signin-button").disabled = true;
        message.textContent = "";
        try {
            await Api.post("/api/signin", { email }, { redirectOnSignOut: false });
            location.replace(next);
        } catch (error) {
            message.textContent = error.message;
            $("signin-button").disabled = false;
            $("signin-email").focus();
        }
    });
})();
