"use strict";

// Network page (v1.1 spec 4.2): your own network graph, plus connection
// requests waiting for you (each shown with the sender's profile card,
// spec 4.11) and the requests you've sent.

(() => {
    const { Api, Session, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;
    const $ = (id) => document.getElementById(id);

    let graph;

    async function start() {
        let me;
        try {
            me = await Session.current();
        } catch {
            return;
        }
        graph = new window.NetworkGraph($("graph"));
        graph.load(me.id);
        loadRequests();
        bindInvite();
    }

    async function loadRequests() {
        try {
            const data = await Api.get("/api/requests");
            $("requests").replaceChildren(...data.incoming.map(requestRow));
            $("requests-panel").hidden = data.incoming.length === 0;
            $("sent").replaceChildren(...data.outgoing.map(sentRow));
            $("sent-panel").hidden = data.outgoing.length === 0;
        } catch (error) {
            $("requests-panel").hidden = false;
            $("requests-status").textContent = error.message;
        }
    }

    function requestRow(card) {
        const accept = el("button", { className: "button button-solid", text: "Accept", attrs: { type: "button" } });
        const decline = el("button", { className: "button", text: "Decline", attrs: { type: "button" } });
        const answer = async (action) => {
            accept.disabled = decline.disabled = true;
            try {
                const result = await Api.post("/api/connection", { person_id: card.id, action });
                $("requests-status").textContent = result.notice || `Declined ${cleanName(card.name)}'s request.`;
                $("requests-status").classList.remove("is-error");
                await loadRequests();
                graph.load(graph.personId);
            } catch (error) {
                $("requests-status").textContent = error.message;
                $("requests-status").classList.add("is-error");
                accept.disabled = decline.disabled = false;
            }
        };
        accept.addEventListener("click", () => answer("accept"));
        decline.addEventListener("click", () => answer("decline"));
        return el("li", { className: "request" }, [
            window.ProfileCard.render(card, { compact: true, headingLevel: "h3" }),
            el("div", { className: "row request-actions" }, [
                el("a", { className: "button", text: "View profile", attrs: { href: Ui.profileHref(card.id) } }),
                decline, accept,
            ]),
        ]);
    }

    function sentRow(person) {
        const cancel = el("button", { className: "button", text: "Cancel", attrs: { type: "button" } });
        cancel.addEventListener("click", async () => {
            cancel.disabled = true;
            try {
                await Api.post("/api/connection", { person_id: person.id, action: "cancel" });
                loadRequests();
            } catch (error) {
                cancel.disabled = false;
                $("requests-status").textContent = error.message;
            }
        });
        return el("li", { className: "list-item" }, [
            Ui.avatar(person),
            el("div", { className: "list-main" }, [
                el("p", { className: "list-title" }, [el("a", { text: cleanName(person.name), attrs: { href: Ui.profileHref(person.id) } })]),
                el("p", { className: "list-meta", text: "Waiting for them to accept" }),
            ]),
            cancel,
        ]);
    }

    function bindInvite() {
        $("invite-button").addEventListener("click", async () => {
            $("invite-button").disabled = true;
            try {
                const { path } = await Api.post("/api/invites", {});
                $("invite-link").value = new URL(path, location.origin).href;
                $("invite-link-row").hidden = false;
                $("invite-link").select();
            } catch (error) {
                $("invite-help").textContent = error.message;
            } finally {
                $("invite-button").disabled = false;
            }
        });
        $("invite-copy").addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText($("invite-link").value);
                $("invite-copy").textContent = "Copied";
            } catch {
                $("invite-link").select(); // copy by hand
            }
        });
    }

    start();
})();
