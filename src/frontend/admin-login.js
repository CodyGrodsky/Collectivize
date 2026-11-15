"use strict";

// The admin entrance (/admin): username and password. Nothing links here.

(() => {
    const { Api } = window.Collectivize;
    const $ = (id) => document.getElementById(id);

    $("admin-signin").addEventListener("submit", async (event) => {
        event.preventDefault();
        const message = $("admin-message");
        $("admin-button").disabled = true;
        message.textContent = "";
        try {
            await Api.post("/api/admin/signin", {
                username: $("admin-username").value.trim(),
                password: $("admin-password").value,
            }, { redirectOnSignOut: false });
            location.replace("admin.html");
        } catch (error) {
            message.textContent = error.message;
            $("admin-password").value = "";
            $("admin-button").disabled = false;
            $("admin-password").focus();
        }
    });
})();
