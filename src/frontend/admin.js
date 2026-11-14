"use strict";

// Platform admin page (v1.1 spec 4.9). Only platform admins get past the
// check below; the server refuses every admin call from anyone else too.
// "View as" shows the app through a user's eyes, read-only and logged.

(() => {
    const { Api, Session, Ui } = window.Collectivize;
    const { el } = Ui;
    const $ = (id) => document.getElementById(id);
    const SCORES_KEY = "collectivize.admin.scores";
    const TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

    async function start() {
        let me;
        try {
            me = await Session.current();
        } catch {
            return;
        }
        if (!me.is_platform_admin) {
            location.replace("discover.html"); // not for regular users or org admins
            return;
        }
        $("admin").hidden = false;
        $("user-search").addEventListener("submit", (event) => {
            event.preventDefault();
            search($("user-query").value.trim());
        });
        const scores = $("show-scores");
        try {
            scores.checked = localStorage.getItem(SCORES_KEY) === "1";
        } catch { /* storage blocked */ }
        scores.addEventListener("change", () => {
            try {
                localStorage.setItem(SCORES_KEY, scores.checked ? "1" : "0");
            } catch { /* not remembered */ }
        });
        search("");
        loadAudit();
    }

    async function search(q) {
        const status = $("users-status");
        status.textContent = "Searching…";
        status.classList.remove("is-error");
        try {
            const data = await Api.get("/api/admin/users", { q });
            status.textContent = `${data.total} user${data.total === 1 ? "" : "s"}${data.total > data.users.length ? ` (first ${data.users.length})` : ""}`;
            $("users").replaceChildren(...data.users.map(userRow));
        } catch (error) {
            status.textContent = error.message;
            status.classList.add("is-error");
        }
    }

    function userRow(user) {
        const view = el("button", { className: "button", text: "View as", attrs: { type: "button" } });
        view.addEventListener("click", async () => {
            view.disabled = true;
            try {
                await Api.post("/api/admin/view-as", { person_id: user.id });
                location.href = "discover.html";
            } catch (error) {
                $("users-status").textContent = error.message;
                view.disabled = false;
            }
        });
        return el("li", { className: "list-item" }, [
            Ui.avatar(user),
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title", text: user.name }),
                el("p", { className: "list-meta",
                          text: `${user.email || "no email"} · ${user.connections} connections · ${user.orgs} orgs` }),
            ]),
            view,
        ]);
    }

    async function loadAudit() {
        try {
            const { entries } = await Api.get("/api/admin/audit");
            $("audit").replaceChildren(...entries.map((e) => el("li", { className: "list-item" }, [
                el("div", { className: "list-main" }, [
                    el("p", { className: "list-title", text: `${e.action === "start" ? "Started" : "Stopped"} viewing as ${e.target}` }),
                    el("p", { className: "list-meta", text: `${TIME.format(new Date(e.at))} · admin ${e.admin}` }),
                ]),
            ])));
            $("audit-empty").hidden = entries.length > 0;
        } catch {
            $("audit-empty").textContent = "The log couldn't load.";
        }
    }

    start();
})();
