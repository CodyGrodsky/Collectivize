"use strict";

// Landing / sign-in page (v1.1 spec 4.10). Signed-out visitors can reach
// only this page (and invite links); the server sends everything else here
// with ?next=<page they wanted>.

(() => {
    const { Api, Session } = window.Collectivize;
    const $ = (id) => document.getElementById(id);

    const next = Session.nextPage(new URLSearchParams(location.search).get("next"));

    // Already signed in: carry on.
    Api.get("/api/me", {}, { redirectOnSignOut: false })
        .then(() => location.replace(next))
        .catch(() => {});

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
