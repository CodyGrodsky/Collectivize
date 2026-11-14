"use strict";

// Organization page: what it is, who's in it, what it hosts. Member lists
// follow the visibility rules on the server (members see everyone;
// others see the count and the members they're connected to). Org admins
// can edit the page and add or remove members (v1.1 spec 4.9).

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;
    const root = document.getElementById("org");
    const DATE = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const KIND = { chapter: "Chapter", national: "National organization", council: "Council", club: "Club", institution: "Institution" };

    const id = new URLSearchParams(location.search).get("id") || "";
    let org = null;

    async function load() {
        try {
            org = await Api.get("/api/org", { id });
            render();
        } catch (error) {
            root.replaceChildren(el("p", { className: "status is-error", text: error.message }));
        }
    }

    function render() {
        document.title = `${cleanName(org.name)} · Collectivize`;
        const status = el("p", { className: "status", attrs: { role: "status", "aria-live": "polite" } });
        const meta = [KIND[org.kind] || org.kind, ...org.tags].join(" · ");
        const header = [
            el("h1", { className: "page-title", text: cleanName(org.name) }),
            el("p", { className: "page-subtitle", text: meta }),
        ];
        if (org.website_url) {
            header.push(el("p", {}, [el("a", { text: org.website_url, attrs: { href: org.website_url, rel: "noopener noreferrer", target: "_blank" } })]));
        }
        if (org.is_member) header.push(el("p", {}, [el("span", { className: "pill pill-green", text: org.can_manage ? "You're an admin" : "You're a member" })]));
        root.replaceChildren(...header, status, members(status), events(), ...(org.can_manage ? [editor(status)] : []));
    }

    function members(status) {
        const n = org.member_count;
        const note = org.members_complete ? "" : org.members.length
            ? ` · showing the ${org.members.length} you're connected to`
            : " · you aren't connected to any of them yet";
        const list = el("ul", { className: "list" }, org.members.map((m) => memberRow(m, status)));
        return el("section", { className: "panel", attrs: { "aria-labelledby": "members-title" } }, [
            el("h2", { className: "panel-title", text: `${n} member${n === 1 ? "" : "s"}${note}`, attrs: { id: "members-title" } }),
            list,
            ...(org.can_manage ? [addForm(status)] : []),
        ]);
    }

    function memberRow(member, status) {
        const children = [
            Ui.avatar(member),
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title" }, [el("a", { text: cleanName(member.name), attrs: { href: Ui.profileHref(member.id) } })]),
                ...(member.admin ? [el("p", { className: "list-meta", text: "Admin" })] : []),
            ]),
        ];
        if (org.can_manage) {
            const remove = el("button", { className: "button button-danger", text: "Remove", attrs: { type: "button" } });
            remove.addEventListener("click", () => change({ action: "remove", person_id: member.id }, status, remove));
            children.push(remove);
        }
        return el("li", { className: "list-item" }, children);
    }

    function addForm(status) {
        const input = el("input", { className: "input", attrs: { type: "email", placeholder: "Their Northeastern email", maxlength: "254", "aria-label": "Add a member by email" } });
        const button = el("button", { className: "button button-solid", text: "Add member", attrs: { type: "submit" } });
        const form = el("form", { className: "row", attrs: { style: "margin-top:12px" } }, [input, button]);
        form.addEventListener("submit", (event) => {
            event.preventDefault();
            if (input.value.trim()) change({ action: "add", email: input.value.trim() }, status, button);
        });
        return form;
    }

    async function change(body, status, button) {
        button.disabled = true;
        try {
            org = await Api.post("/api/org/members", { org_id: org.id, ...body });
            render();
        } catch (error) {
            status.textContent = error.message;
            status.classList.add("is-error");
            button.disabled = false;
        }
    }

    function events() {
        const list = el("ul", { className: "list" }, org.events.map((e) => el("li", { className: "list-item" }, [
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title", text: e.title }),
                el("p", { className: "list-meta", text: [DATE.format(new Date(e.starts_at)), e.location, e.is_cancelled ? "Cancelled" : ""].filter(Boolean).join(" · ") }),
            ]),
        ])));
        return el("section", { className: "panel" }, [
            el("h2", { className: "panel-title", text: "Upcoming events" }),
            org.events.length ? list : el("p", { className: "muted", text: "Nothing coming up." }),
        ]);
    }

    function editor(status) {
        const name = el("input", { className: "input", attrs: { value: org.name, maxlength: "80", "aria-label": "Name" } });
        const site = el("input", { className: "input", attrs: { value: org.website_url || "", type: "url", placeholder: "https://…", "aria-label": "Website" } });
        const save = el("button", { className: "button button-solid", text: "Save", attrs: { type: "submit" } });
        const form = el("form", {}, [
            el("label", { className: "row", attrs: { style: "margin-bottom:8px" } }, [el("span", { text: "Name", attrs: { style: "width:70px" } }), name]),
            el("label", { className: "row", attrs: { style: "margin-bottom:8px" } }, [el("span", { text: "Website", attrs: { style: "width:70px" } }), site]),
            save,
        ]);
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            save.disabled = true;
            try {
                org = await Api.post("/api/org", { id: org.id, name: name.value, website_url: site.value || null });
                render();
            } catch (error) {
                status.textContent = error.message;
                status.classList.add("is-error");
                save.disabled = false;
            }
        });
        return el("section", { className: "panel" }, [el("h2", { className: "panel-title", text: "Edit this page (admins)" }), form]);
    }

    load();
})();
