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
            location.replace("discover"); // not for regular users or org admins
            return;
        }
        Rights.me = me;
        $("admin").hidden = false;
        $("admin-subtitle").textContent = (me.is_owner ? "You are the owner: you manage accounts and the other admins. "
            : `Your powers: ${me.powers.map((p) => POWER_NAMES[p] || p).join(", ") || "view the list only"}. `)
            + "Viewing as someone is read-only, and every change you make here is logged.";
        $("create").hidden = !Rights.can("create");
        if (me.is_owner) window.CollectivizeAdminTeam.start(refresh);
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
        Manage.bind();
        $("create-form").addEventListener("submit", (event) => {
            event.preventDefault();
            create();
        });
        search("");
        loadAudit();
        // admin?id=<person id> opens that account straight away.
        const id = new URLSearchParams(location.search).get("id");
        if (id) Manage.open(id);
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
        // Admin accounts are the owner's to manage.
        const mine = Rights.me.is_owner || !user.is_platform_admin;
        const view = el("button", { className: "button", text: "View as",
                                    attrs: { type: "button", hidden: !(mine && Rights.can("view_as")) } });
        view.addEventListener("click", () => viewAs(user.id, (message) => {
            $("users-status").textContent = message;
        }));
        const manage = el("button", { className: "button button-solid", text: "Manage",
                                      attrs: { type: "button", hidden: !(mine && Rights.can("view_info")) } });
        manage.addEventListener("click", () => Manage.open(user.id));
        return el("li", { className: "list-item" }, [
            Ui.avatar(user),
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title", text: user.name }),
                el("p", { className: "list-meta",
                          text: [user.is_platform_admin ? "platform admin" : null, user.email,
                                 `${user.connections} connections`, `${user.orgs} orgs`]
                              .filter(Boolean).join(" · ") }),
            ]),
            manage,
            view,
        ]);
    }

    async function viewAs(id, onError) {
        try {
            await Api.post("/api/admin/view-as", { person_id: id });
            location.href = "discover";
        } catch (error) {
            onError(error.message);
        }
    }

    const POWER_NAMES = {
        view_info: "view all info", view_as: "view as", edit: "edit", create: "create accounts",
        sign_out: "sign people out", delete: "delete accounts",
    };

    /** What the signed-in admin may do (from /api/me). The server checks
        every action again; this only hides buttons that would be refused. */
    class Rights {
        static me = null;

        static can(power) {
            return Rights.me.powers.includes(power);
        }
    }

    /** The "Manage account" panel: one account, opened from the list. */
    class Manage {
        static account = null;

        static bind() {
            $("manage-close").addEventListener("click", () => Manage.show(null));
            $("manage-form").addEventListener("submit", (event) => {
                event.preventDefault();
                Manage.save();
            });
            $("m-view").addEventListener("click", () => viewAs(Manage.account.id, Manage.say));
            $("m-signout").addEventListener("click", () => Manage.run("/api/admin/account/signout", {},
                (data) => `Signed out of ${data.sessions_ended} session${data.sessions_ended === 1 ? "" : "s"}.`));
            $("delete-form").addEventListener("submit", (event) => {
                event.preventDefault();
                Manage.remove();
            });
            $("m-make-admin").addEventListener("click", async () => {
                try {
                    await Api.post("/api/admin/admins", { person_id: Manage.account.id, is_admin: true });
                    await Manage.open(Manage.account.id);
                    Manage.say("They are now a platform admin with no powers yet. Tick their powers below.", false);
                    window.CollectivizeAdminTeam.load();
                    refresh();
                } catch (error) {
                    Manage.say(error.message);
                }
            });
        }

        static say(message, isError = true) {
            const status = $("manage-status");
            status.textContent = message;
            status.classList.toggle("is-error", isError);
        }

        static async open(id) {
            try {
                Manage.show(await Api.get("/api/admin/account", { id }));
                Manage.say("", false);
                $("manage").scrollIntoView({ block: "start" });
            } catch (error) {
                $("users-status").textContent = error.message;
            }
        }

        static show(account) {
            Manage.account = account;
            $("manage").hidden = !account;
            if (!account) return;
            const { settings, counts } = account;
            $("manage-title").textContent = `Manage ${account.name}`;
            $("manage-facts").textContent = [
                `Joined ${TIME.format(new Date(account.created_at))}`,
                `${counts.connections} connections`, `${counts.orgs} orgs`, `${counts.rsvps} RSVPs`,
                `${account.sessions} active session${account.sessions === 1 ? "" : "s"}`,
                `AI uses history: ${settings.ai_uses_history ? "on" : "off"}`,
                `analytics consent: ${settings.analytics_consent ? "on" : "off"} (theirs to change)`,
            ].join(" · ");
            $("m-name").value = account.name;
            $("m-email").value = account.email || "";
            $("m-bio").value = account.profile.bio;
            $("m-profile-visibility").value = settings.profile_visibility;
            $("m-rsvp-visibility").value = settings.rsvp_visibility;
            $("m-hide-connections").checked = settings.hide_connections;
            const editable = Rights.can("edit");
            for (const id of ["m-name", "m-bio", "m-profile-visibility", "m-rsvp-visibility", "m-hide-connections"]) {
                $(id).disabled = !editable;
            }
            $("m-email").disabled = !Rights.me.is_owner; // a new sign-in email is the owner's call
            $("m-save").hidden = !editable;
            $("m-view").hidden = !Rights.can("view_as");
            $("m-signout").hidden = !Rights.can("sign_out");
            $("delete-form").hidden = !Rights.can("delete") || account.id === Rights.me.id;
            $("m-make-admin").hidden = !Rights.me.is_owner || account.is_platform_admin;
            $("m-confirm").value = "";
        }

        /** Only what was changed is sent. */
        static changes() {
            const { account } = Manage;
            const now = {
                display_name: $("m-name").value.trim(),
                email: $("m-email").value.trim().toLowerCase(),
                bio: $("m-bio").value.trim(),
                profile_visibility: $("m-profile-visibility").value,
                rsvp_visibility: $("m-rsvp-visibility").value,
                hide_connections: $("m-hide-connections").checked,
            };
            const before = {
                display_name: account.name, email: account.email || "", bio: account.profile.bio,
                profile_visibility: account.settings.profile_visibility,
                rsvp_visibility: account.settings.rsvp_visibility,
                hide_connections: account.settings.hide_connections,
            };
            return Object.fromEntries(Object.entries(now).filter(([key, value]) => value !== before[key]));
        }

        static async save() {
            const changes = Manage.changes();
            if (!Object.keys(changes).length) {
                Manage.say("Nothing to save.", false);
                return;
            }
            $("m-save").disabled = true;
            try {
                Manage.show(await Api.post("/api/admin/account", { person_id: Manage.account.id, ...changes }));
                Manage.say("Saved.", false);
                refresh();
            } catch (error) {
                Manage.say(error.message);
            } finally {
                $("m-save").disabled = false;
            }
        }

        static async run(path, body, done) {
            try {
                const data = await Api.post(path, { person_id: Manage.account.id, ...body });
                Manage.say(done(data), false);
                refresh();
                return data;
            } catch (error) {
                Manage.say(error.message);
                return null;
            }
        }

        static async remove() {
            const data = await Manage.run("/api/admin/account/delete", { confirm: $("m-confirm").value }, () => "");
            if (!data) return;
            Manage.show(null);
            $("users-status").textContent = "Account deleted.";
        }
    }

    async function create() {
        const status = $("create-status");
        const body = { email: $("c-email").value.trim() };
        if ($("c-name").value.trim()) body.display_name = $("c-name").value.trim();
        try {
            const account = await Api.post("/api/admin/account/create", body);
            status.textContent = `Created ${account.name}.`;
            status.classList.remove("is-error");
            $("create-form").reset();
            refresh();
            Manage.show(account);
        } catch (error) {
            status.textContent = error.message;
            status.classList.add("is-error");
        }
    }

    function refresh() {
        search($("user-query").value.trim());
        loadAudit();
    }

    const ACTIONS = {
        "view-as start": "Started viewing as", "view-as stop": "Stopped viewing as",
        update: "Edited", create: "Created", delete: "Deleted", "sign-out": "Signed out",
        "make admin": "Made admin:", "remove admin": "Removed admin:", powers: "Changed powers of",
        "sign-in": "Admin signed in:", "sign-in failed": "Failed admin sign-in",
    };

    async function loadAudit() {
        try {
            const { entries } = await Api.get("/api/admin/audit");
            $("audit").replaceChildren(...entries.map((e) => el("li", { className: "list-item" }, [
                el("div", { className: "list-main" }, [
                    el("p", { className: "list-title",
                              text: `${ACTIONS[e.action] || e.action} ${e.target}${e.detail ? ` (${e.detail})` : ""}` }),
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
