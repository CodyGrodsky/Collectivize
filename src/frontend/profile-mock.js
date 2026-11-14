"use strict";

// Fictional sample profiles for the profile page, used only until the page
// is connected to the backend. Every person, org, event and website here is
// made up (websites use the reserved .example domain).
//
// Each record holds what the backend would store, plus `visitor`: how a
// signed-in visitor relates to this person. ProfileDataSource turns a record
// into the GET /api/profile shape (docs/profile-backend-proposal.md).

window.COLLECTIVIZE_PROFILE_MOCK = (() => {
    // Event times relative to today, at a given hour, like the backend seed.
    const at = (daysAhead, hour, hours = 2) => {
        const start = new Date();
        start.setDate(start.getDate() + daysAhead);
        start.setHours(hour, 0, 0, 0);
        const end = new Date(start.getTime() + hours * 3600 * 1000);
        return { starts_at: start.toISOString(), ends_at: end.toISOString() };
    };

    const event = (id, title, location, host, daysAhead, hour, extra = {}) => ({
        id, title, location, host, is_cancelled: false, ...at(daysAhead, hour), ...extra,
    });

    const SURF = { name: "Surf Club", kind: "org" };
    const DESIGN = { name: "Design Collective", kind: "org" };

    return [
        {
            id: "sample-maya",
            username: "maya_r",
            display_name: "Maya Reyes",
            verified: true,
            bio: "Design student, early-morning surfer, always up for a pottery night.",
            about: "Third-year design student. I run the Sunday dawn patrol for Surf Club "
                + "and help put on Design Collective's monthly critique nights.\n\n"
                + "If you want to learn to surf, come find me. I've got spare wetsuits "
                + "and a lot of patience.",
            top3: { title: "Top 3 study spots", items: ["Library 4th floor", "The corner café", "Anywhere with a window"] },
            website: "https://maya-makes.example/portfolio",
            colors: { background: "#FDFDFB", outline: "#19A88A" },
            profile_visibility: "network",
            orgs: [{ id: "sample-surf", name: "Surf Club" }, { id: "sample-design", name: "Design Collective" }],
            hosting: [
                event("sample-ev-1", "Dawn patrol: beginner session", "North Beach lot", SURF, 2, 6),
                event("sample-ev-2", "Critique night", "Art Building, Room 110", DESIGN, 6, 19),
            ],
            going: [
                event("sample-ev-3", "Pottery open studio", "Craft Center", { name: "Ceramics Club", kind: "org" }, 4, 18),
                event("sample-ev-4", "Beach cleanup", "South Pier", SURF, 9, 10, { is_cancelled: true }),
            ],
            visitor: { connection: "none", in_common: "2 friends, Surf Club" },
        },
        {
            id: "sample-theo",
            username: "theo_k",
            display_name: "Theo Kim",
            verified: true,
            bio: "Chess, cycling and too much coffee.",
            about: "Only my connections can read this.",
            top3: null,
            website: null,
            colors: { background: "#1E3A9E", outline: "#FDFDFB" },
            profile_visibility: "connections",
            orgs: [{ id: "sample-chess", name: "Chess Club" }],
            hosting: [],
            going: [],
            visitor: { connection: "none", in_common: "1 friend" },
        },
        {
            id: "sample-sam",
            username: "sam_2027",
            display_name: "",
            verified: true,
            bio: "",
            about: "",
            top3: null,
            website: null,
            colors: { background: "#F5F4F0", outline: "#202B29" },
            profile_visibility: "network",
            orgs: [],
            hosting: [],
            going: [],
            visitor: { connection: "none", in_common: null },
        },
    ];
})();
