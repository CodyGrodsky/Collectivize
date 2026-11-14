"use strict";

// Your connection requests and invite links (v1.1 spec 4.2 / 4.11), shown
// under the Network page: requests waiting for you, each with the sender's
// profile card (profile-card.js) and Accept / Decline; requests you've
// sent; and a single-use invite link to share.
//
// Usage: ConnectionsPanel.mount(container, { onChange })

(() => {
    const { Api, Ui } = window.Collectivize;
    const { el, cleanName } = Ui;

    const ConnectionsPanel = Object.freeze({
        mount(container, { onChange = () => {} } = {}) {
            const status = el("p", { className: "cp-status", attrs: { role: "status", "aria-live": "polite" } });
            const incoming = el("ul", { className: "cp-list" });
            const outgoing = el("ul", { className: "cp-list" });
            const incomingBox = el("section", { className: "nw-section", attrs: { hidden: true } }, [
                el("h3", { text: "Connection requests" }), incoming,
            ]);
            const outgoingBox = el("section", { className: "nw-section", attrs: { hidden: true } }, [
                el("h3", { text: "Requests you've sent" }), outgoing,
            ]);
            container.replaceChildren(status, incomingBox, invitePanel(status), outgoingBox);

            const say = (text, error = false) => {
                status.textContent = text;
                status.classList.toggle("is-error", error);
            };

            async function load() {
                try {
                    const data = await Api.get("/api/requests");
                    incoming.replaceChildren(...data.incoming.map(requestRow));
                    incomingBox.hidden = data.incoming.length === 0;
                    outgoing.replaceChildren(...data.outgoing.map(sentRow));
                    outgoingBox.hidden = data.outgoing.length === 0;
                } catch (error) {
                    say(error.message, true);
                }
            }

            function requestRow(card) {
                const accept = el("button", { className: "page-button page-button-solid", text: "Accept", attrs: { type: "button" } });
                const decline = el("button", { className: "page-button", text: "Decline", attrs: { type: "button" } });
                const answer = async (action) => {
                    accept.disabled = decline.disabled = true;
                    try {
                        const result = await Api.post("/api/connection", { person_id: card.id, action });
                        say(result.notice || `Declined ${cleanName(card.name)}'s request.`);
                        await load();
                        onChange();
                    } catch (error) {
                        say(error.message, true);
                        accept.disabled = decline.disabled = false;
                    }
                };
                accept.addEventListener("click", () => answer("accept"));
                decline.addEventListener("click", () => answer("decline"));
                return el("li", { className: "cp-request" }, [
                    window.ProfileCard.render(card, { compact: true, headingLevel: "h4" }),
                    el("div", { className: "cp-actions" }, [decline, accept]),
                ]);
            }

            function sentRow(person) {
                const cancel = el("button", { className: "page-button", text: "Cancel", attrs: { type: "button" } });
                cancel.addEventListener("click", async () => {
                    cancel.disabled = true;
                    try {
                        await Api.post("/api/connection", { person_id: person.id, action: "cancel" });
                        load();
                    } catch (error) {
                        say(error.message, true);
                        cancel.disabled = false;
                    }
                });
                return el("li", { className: "cp-row" }, [
                    el("span", { className: "cp-name", text: cleanName(person.name) }),
                    el("span", { className: "cp-meta", text: "Waiting for them to accept" }),
                    cancel,
                ]);
            }

            load();
            return { reload: load };
        },
    });

    function invitePanel(status) {
        const button = el("button", { className: "page-button", text: "Get an invite link", attrs: { type: "button" } });
        const link = el("input", { className: "cp-link", attrs: { readonly: true, "aria-label": "Invite link" } });
        const copy = el("button", { className: "page-button page-button-solid", text: "Copy", attrs: { type: "button" } });
        const row = el("div", { className: "cp-actions", attrs: { hidden: true } }, [link, copy]);
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                const { path } = await Api.post("/api/invites", {});
                link.value = new URL(path, location.origin).href;
                row.hidden = false;
                link.select();
            } catch (error) {
                status.textContent = error.message;
            } finally {
                button.disabled = false;
            }
        });
        copy.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(link.value);
                copy.textContent = "Copied";
            } catch {
                link.select(); // copy by hand
            }
        });
        return el("section", { className: "nw-section" }, [
            el("h3", { text: "Invite someone" }),
            el("p", { className: "cp-meta", text: "They'll see your profile card and can join with their Northeastern email. Each link works once." }),
            button,
            row,
        ]);
    }

    window.ConnectionsPanel = ConnectionsPanel;
})();
