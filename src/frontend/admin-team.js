"use strict";

// The owner's "Admins and their powers" panel on the admin page. Only the
// owner gets it (the server refuses everyone else). Ticking a box saves
// that admin's powers at once.

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el } = Ui;
    const $ = (id) => document.getElementById(id);
    const LABELS = {
        view_info: "View all info", view_as: "View as (impersonate, read-only)", edit: "Edit accounts",
        create: "Create accounts", sign_out: "Sign people out", delete: "Delete accounts",
    };

    class AdminTeam {
        static onChange = () => {};

        static start(onChange) {
            AdminTeam.onChange = onChange;
            $("team").hidden = false;
            AdminTeam.load();
        }

        static say(message, isError = false) {
            $("team-status").textContent = message;
            $("team-status").classList.toggle("is-error", isError);
        }

        static async load() {
            try {
                const { admins, all_powers: all } = await Api.get("/api/admin/admins");
                $("team-list").replaceChildren(...admins.map((admin) => AdminTeam.row(admin, all)));
            } catch (error) {
                AdminTeam.say(error.message, true);
            }
        }

        static async save(admin, body, done) {
            try {
                await Api.post("/api/admin/admins", { person_id: admin.id, ...body });
                AdminTeam.say(done);
                AdminTeam.onChange();
            } catch (error) {
                AdminTeam.say(error.message, true);
            }
            AdminTeam.load();
        }

        static row(admin, all) {
            const fixed = admin.is_owner; // an owner has everything; set in the credentials file
            const boxes = all.map((power) => {
                const box = el("input", { attrs: { type: "checkbox", disabled: fixed } });
                box.checked = admin.powers.includes(power);
                box.dataset.power = power;
                return el("label", {}, [box, document.createTextNode(LABELS[power] || power)]);
            });
            const powers = el("div", { className: "team-powers" }, boxes);
            powers.addEventListener("change", () => {
                const chosen = [...powers.querySelectorAll("input:checked")].map((b) => b.dataset.power);
                AdminTeam.save(admin, { powers: chosen }, `Saved ${Ui.cleanName(admin.name)}'s powers.`);
            });
            const remove = el("button", { className: "button button-danger", text: "Remove admin",
                                          attrs: { type: "button", hidden: fixed } });
            remove.addEventListener("click", () => {
                AdminTeam.save(admin, { is_admin: false }, `${Ui.cleanName(admin.name)} is no longer an admin.`);
            });
            const notes = [admin.is_owner ? "Owner" : "Admin", admin.is_you ? "you" : null, admin.email,
                           admin.has_login ? null : "no admin login yet"].filter(Boolean).join(" · ");
            return el("li", { className: "list-item" }, [
                Ui.avatar(admin),
                el("div", { className: "list-main" }, [
                    el("p", { className: "list-title", text: admin.name }),
                    el("p", { className: "list-meta", text: notes }),
                    powers,
                ]),
                remove,
            ]);
        }
    }

    window.CollectivizeAdminTeam = AdminTeam;
})();
