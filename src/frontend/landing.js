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
    // form: a click anywhere but the logo (which spins) opens Discover.
    Api.get("/api/session", {}, { redirectOnSignOut: false })
        .then(({ signed_in: signedIn }) => {
            if (!signedIn || adminEntrance) return; // /admin always shows the form
            if (wanted) location.replace(next);
            else showSignedIn();
        })
        .catch(() => {});

    function showSignedIn() {
        $("signin").hidden = true;
        document.body.classList.add("is-signed-in");
        // The logo's canvas is far bigger than the logo, so only the dots
        // themselves count as the logo (while the 3D logo loads, its image).
        const box = $("logo3d");
        const isLogo = (event) => {
            if (!(event.target instanceof Element) || !box.contains(event.target)) return false;
            return box.logo3d ? box.logo3d.hits(event.clientX, event.clientY) : event.target.tagName === "IMG";
        };
        // A drag (tilting the logo) isn't a click, wherever it ends.
        let downAt = null;
        document.addEventListener("pointerdown", (event) => { downAt = [event.clientX, event.clientY]; }, true);
        const dragged = (event) => downAt && Math.hypot(event.clientX - downAt[0], event.clientY - downAt[1]) > 5;
        document.addEventListener("click", (event) => {
            if (!dragged(event) && !isLogo(event)) location.assign("discover");
        });
        // Over the logo's empty space, show the "opens something" hand, not "grab".
        box.addEventListener("pointermove", (event) => {
            const canvas = box.querySelector("canvas");
            if (canvas && event.buttons === 0) canvas.style.cursor = isLogo(event) ? "grab" : "pointer";
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") location.assign("discover");
        });
    }

    // Sent here because the account was ended from somewhere else.
    const NOTICES = {
        deleted: "That account was deleted.",
        signed_out: "You were signed out.",
        expired: "Your sign-in ended. Sign in again.",
    };
    const notice = NOTICES[new URLSearchParams(location.search).get("notice")];
    if (notice) $("signin-message").textContent = notice;

    new window.SignInForm({ done: () => location.replace(next) });
})();
