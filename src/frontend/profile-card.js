"use strict";

// ProfileCard (v1.1 spec 4.11): the preview shown with a connection
// request or on an invite link: name, bio, Top 3, in the person's colours
// (the same fields and colours as their profile page).
//
// Usage: ProfileCard.render(card, { compact }) -> element

(() => {
    const { Ui } = window.Collectivize;
    const { el, cleanName } = Ui;

    const ProfileCard = Object.freeze({
        render(card, { compact = false, headingLevel = "h1" } = {}) {
            const root = el("article", { className: `pc${compact ? " is-compact" : ""}` });
            root.style.setProperty("--pc-bg", card.colors.background);
            root.style.setProperty("--pc-line", card.colors.outline);
            root.append(el("div", { className: "pc-head" }, [
                el("div", { className: "pc-photo" }, [
                    el("span", { text: cleanName(card.name).charAt(0).toUpperCase() || "?", attrs: { "aria-hidden": "true" } }),
                ]),
                el("div", { className: "pc-who" }, [
                    el(headingLevel, { className: "pc-name", text: cleanName(card.name) }),
                    card.bio ? el("p", { className: "pc-bio", text: card.bio }) : null,
                ]),
            ]));
            if (card.top3) root.append(ProfileCard.top3(card.top3));
            return root;
        },

        /** The person's Top 3: their title (e.g. "Top 3 study spots") and three items. */
        top3(top3) {
            return el("section", { className: "pc-top3", attrs: { "aria-label": top3.title } }, [
                el("p", { className: "pc-top3-title", text: top3.title }),
                el("ol", { className: "pc-top3-list" }, top3.items.map((item) => el("li", { text: item || "—" }))),
            ]);
        },
    });

    window.ProfileCard = ProfileCard;
})();
