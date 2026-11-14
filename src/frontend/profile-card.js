"use strict";

// ProfileCard (v1.1 spec 4.3 / 4.11): the top of a profile, and the
// preview shown with a connection request or an invite. One component, so
// what you see on your profile is what others see when you reach out.
//
// It draws whatever card the server sent: the server already cut it down
// to what this viewer may see (a partial card has no pictures).
//
// Usage: ProfileCard.render(card, { compact }) -> element

(() => {
    const { Ui } = window.Collectivize;
    const { el, cleanName } = Ui;

    const photoUrl = (id, slot) => `/api/photo?${new URLSearchParams({ id, slot: String(slot) })}`;

    const ProfileCard = Object.freeze({
        render(card, { compact = false, headingLevel = "h1" } = {}) {
            const root = el("article", { className: `pc${compact ? " is-compact" : ""}` });
            root.style.setProperty("--pc-bg", card.theme.background_hex);
            root.style.setProperty("--pc-line", card.theme.outline_hex);

            const photo = el("div", { className: "pc-photo" });
            if (card.has_photo) {
                photo.append(el("img", { attrs: { src: photoUrl(card.id, 0), alt: `Photo of ${cleanName(card.name)}` } }));
            } else {
                photo.append(el("span", { text: cleanName(card.name).charAt(0).toUpperCase() || "?", attrs: { "aria-hidden": "true" } }));
            }
            const head = el("div", { className: "pc-head" }, [
                photo,
                el("div", { className: "pc-who" }, [
                    el(headingLevel, { className: "pc-name", text: cleanName(card.name) }),
                    card.bio ? el("p", { className: "pc-bio", text: card.bio }) : null,
                ]),
            ]);
            root.append(head);

            if (card.pictures && card.pictures.length) {
                root.append(el("div", { className: "pc-pictures" }, card.pictures.map((slot) => el("img", {
                    attrs: { src: photoUrl(card.id, slot), alt: "", loading: "lazy" },
                }))));
            }
            if (card.top3) root.append(ProfileCard.top3(card.top3));
            return root;
        },

        /** "Top 3" and the numbering are fixed; the person names the category. */
        top3(top3) {
            return el("section", { className: "pc-top3", attrs: { "aria-label": `Top 3 ${top3.title}` } }, [
                el("p", { className: "pc-top3-title" }, [
                    el("span", { className: "pc-top3-label", text: "Top 3 " }),
                    document.createTextNode(top3.title),
                ]),
                el("ol", { className: "pc-top3-list" }, top3.items.map((item) => el("li", { text: item || "—" }))),
            ]);
        },
    });

    window.ProfileCard = ProfileCard;
})();
