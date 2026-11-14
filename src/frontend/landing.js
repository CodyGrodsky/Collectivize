"use strict";

// Landing / sign-in page (v1.1 spec 4.10). Signed-out visitors can reach
// only this page (and invite links); the server sends everything else here
// with ?next=<page they wanted>.

(() => {
    const { Api, Session } = window.Collectivize;
    const $ = (id) => document.getElementById(id);

    // The animated logo (spec 4.12); the static logo stays with reduced motion.
    window.LogoParticles?.mount($("hero"), {
        mark: "../../public/brand/logo-on-paper.svg",
        word: "../../public/brand/wordmark-on-paper.svg",
    }).then((animated) => document.body.classList.toggle("has-animated-logo", animated));

    const next = Session.nextPage(new URLSearchParams(location.search).get("next"));

    // Already signed in: carry on.
    Api.get("/api/session", {}, { redirectOnSignOut: false })
        .then(({ signed_in: signedIn }) => { if (signedIn) location.replace(next); })
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
