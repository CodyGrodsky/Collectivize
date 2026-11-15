"use strict";

// Landing / sign-in page (v1.1 spec 4.10). Signed-out visitors can reach
// only this page (and invite links); the server sends everything else here
// with ?next=<page they wanted>.

(() => {
    const { Api, Session } = window.Collectivize;
    const $ = (id) => document.getElementById(id);

    const wanted = new URLSearchParams(location.search).get("next");
    const next = Session.nextPage(wanted);

    // Already signed in: carry on to the page they asked for, or, with none
    // (e.g. the nav's wordmark link), show the home page without the sign-in
    // form, where the wordmark leads to Discover.
    Api.get("/api/session", {}, { redirectOnSignOut: false })
        .then(({ signed_in: signedIn }) => {
            if (!signedIn) return;
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
