# Collectivize: Concept, Architecture & Hackathon Plan

**Version:** 0.2 (hackathon working doc), November 13, 2026
**Domain:** collectivize.co
**Team:** James (frontend + UX), Keaton (backend + data + graph logic), three business teammates (seed data, validation, pitch)

> This is a working plan for a weekend build, not a product spec. Everything here is a hypothesis we're choosing to demo, not something users have validated yet. When this doc and a teammate disagree, talk, then update the doc.

---

## 0. TL;DR

We're building a website where Northeastern students join by invite, see exactly how they connect to any person or organization on campus, and find real events through their own network instead of an algorithmic feed. Organizations and people get expressive, MySpace-level customizable pages that link out to their own websites. Events are posted once and show up everywhere they should, with visibility the host controls.

**The pitch in one line:** *Your network is the best discovery engine on campus, but no tool actually uses it.*

**The build strategy in one line:** One flawless demo path, built frontend-first on seed data, with a clean data layer so a real backend can slot in later.

---

## 1. Concept

### What Collectivize is

Collectivize is human-centered social infrastructure: digital tools whose success is measured offline, in people who show up and communities that grow. The thesis is *"build digital infrastructure that makes the physical world perform better for humans."*

It has two equal pillars connected by events. **People** have independent, expressive digital identities. **Organizations** have independent identities and the tools to reach people. **Events** are where the two meet in real life.

### Why it's different

Engage has a directory and events. Instagram has identity and a feed. Party-invite apps have invites. None of them use the actual social network around you to make communities and events findable and trustworthy. Collectivize does three things together:

1. **The network is visible and navigable.** You can see how you connect to anyone and what you have in common, which turns strangers into "oh, we both know Maria."
2. **Discovery runs on your network, not surveillance.** Events rank higher because your friends are going or your orgs are hosting, not because we tracked your scrolling.
3. **Your identity is yours.** Pages are deeply customizable, and your own website lives on your page as a featured layer rather than being replaced by a uniform template.

### The three node types

These match the whiteboard key.

| Node | Shape / color | Who creates it | Notes |
|---|---|---|---|
| Person | Circle, blue | Invite-only signup with a northeastern.edu email | Your inviter becomes your first connection, which you can remove |
| Organization | Square, red | Seeded for the demo; later claimed by verified admins | Has admins and accepted members; can have a parent org (e.g., a council and its chapters) |
| Event | Triangle, green | Orgs or individual people | Not drawn in the graph for v1; attendance edges are a stretch goal |

### Principles that change the product

These are design rules, not slogans. Each one changes a concrete decision.

| Principle | What it changes |
|---|---|
| Real life first | Success metric is RSVPs and attendance, not time on site |
| No infinite feed | Discover uses numbered pages ("1–10 of 214"), no infinite scroll, no autoplay |
| Network over surveillance | Ranking uses friends going, hosting orgs, and tags; no behavioral tracking in v1 |
| Identity is yours | Constrained but rich customization; external website featured on the page |
| Private means private | Members-only and invite-only events are invisible to everyone else, not just labeled |
| Organizational autonomy | Org pages link to the org's official site and complement it |

### Who it's for (this weekend)

Invite-only for northeastern.edu emails. We lead with Greek life because that's where we have relationships and where we expect demand, but any Northeastern organization or individual can participate. Outside venues and businesses come later as verified non-student accounts and are out of scope now.

---

## 2. The Demo Path (this is the spec)

If a feature isn't needed for this path, it doesn't get built this weekend.

1. **Join.** Land on collectivize.co (logo, wordmark, "Host events and invite your community"). Enter an invite code and a northeastern.edu email. See yourself appear in the graph, connected to your inviter. Pick the orgs you're in to request membership.
2. **Explore the network.** Click your inviter to expand their connections. Search someone you've never met and watch the path light up. The side panel shows what you have in common: "2 mutual friends · Surf Club."
3. **Visit an org.** Open an org's page: customized look, About, upcoming events calendar, members, and a "View official website" button.
4. **Post once.** Switch to an org admin. Post an event and choose its visibility (everyone at Northeastern / members only / invite specific people). It immediately appears on the org page, the master calendar, and Discover, for exactly the right people.
5. **Discover and go.** Switch back to a student. Search "surf this weekend." Results are paginated cards with "3 friends going." Open the event and RSVP.
6. **Close.** No infinite feed, no data selling. We measure success by people who show up.

The demo uses a **persona switcher** (log in as Maria / as a Surf Club admin) so it never depends on email delivery. The invite screen is real UI, but the demo doesn't rely on real login.

---
## 3. Product Scope

### Navigation

The sidebar order is **Home, Calendar, Discover, Network, Profile, Settings**.

**Home** is one scrolling page: the hero (logo, wordmark, slogan) at the top, then the calendar, then Discover. The Calendar and Discover sidebar links jump to those sections. **Network**, **Profile**, and **Settings** are separate pages. Org admin tools live on the org page itself in an edit mode, not in the sidebar. Explore, Debate, Collaborate, and Projects are hidden for now; at most they appear greyed out as "coming soon."

### Screens

| Screen | Must have | Stretch |
|---|---|---|
| Landing / Home hero | Logo, wordmark, slogan, "Join with invite" button, persona switcher | Animated graph background |
| Invite + onboarding | Invite code, .edu check, "You're connected to [inviter]", pick your orgs | Suggested people |
| Calendar | Month view; day panel with **My Events** (hosting, going, my orgs) and **Community Events** | Week/day views (FullCalendar makes these cheap); collapse a busy day into one chip per org |
| Discover | Search box, tag/date filters, wide result cards, numbered pagination, "N friends going" | Natural-language search ("surf this weekend") |
| Event detail (modal) | Title, host, date/time, location, visibility badge, description/agenda, friends going, RSVP | Add to my calendar (.ics) |
| Network | Your node plus direct connections; click to focus; search to show path; in-common panel | Events as triangles in the graph |
| Person page | Customized theme, blocks (About, Orgs, Hosted events, Links, Photo, Website), "View as visitor" | Music/embed block |
| Org page | Customized theme, About, upcoming events, members, "View official website" | Parent/child org display |
| Org admin mode | Edit page, post event, approve/decline membership requests | Co-hosted events |
| Profile editor | Theme preset, accent color, font, banner, reorder blocks, live preview | Drag-and-drop |
| Settings | Hide my connections; who can see my RSVPs (connections / nobody) | |

**Graph interaction decision:** A single click on a node *focuses* it (expands its neighborhood, highlights the path from you, and opens the side panel). The side panel has a **View profile** button. This resolves the conflict between "click to explore" and "click to open the page."

### Event visibility rules

"Everyone" means everyone on the platform, which in v1 is only verified Northeastern students.

| Visibility | Who sees it in Calendar and Discover | Who sees it on the host's page |
|---|---|---|
| `public` (Everyone at Northeastern) | All users | All users |
| `members` (Members only) | Accepted members of the host org | Accepted members only |
| `invite` (Invite specific people) | Host plus invited people | Host plus invited people |

Non-viewers never see that the event exists: no title, no time, no "members only" placeholder. All of this is enforced by one function, `canSee(viewer, event)`, in the data layer. **UI code never decides visibility.**

### Customization ("MySpace, but safe")

Users and orgs choose from structured options: a theme preset, an accent color, one of a few fonts, a banner image, and an ordered list of content blocks. Your external website appears as a featured card block. **No raw HTML or CSS from users**, ever. That was MySpace's famous security hole, and a hackathon judge may ask. "View as visitor" renders your page exactly as an outsider would see it, with your privacy settings applied.

### Must / Stretch / Cut

**Must:** persona switcher, invite + onboarding UI, person and org pages with customization, memberships and connections, graph with focus, path, and in-common, event creation with three visibility levels, month calendar with day panel, paginated Discover with filters, event detail with RSVP, "friends going" ranking, org admin approvals, and dense seed data.

**Stretch (in priority order):** natural-language search, per-org .ics calendar feed, week/day calendar views, collapsing busy days into org chips, co-hosted events, Supabase backend with real login.

**Cut this weekend:** Instagram scraping, two-way Google Calendar sync, messaging, notifications, alumni directories, photo galleries, outside venues and businesses, Explore, Debate, Collaborate, and Projects.

---

## 4. Architecture

### Stack

| Layer | Choice | Why |
|---|---|---|
| Framework | Next.js (App Router) + TypeScript | One codebase for pages and small server routes; deploys to Vercel with zero config |
| Styling | Tailwind CSS + shadcn/ui | Fast, consistent components; easy theming via CSS variables |
| Graph | `react-force-graph-2d` | Custom canvas drawing for circles vs squares; handles hundreds of nodes easily |
| Calendar | FullCalendar (React) | Month/week/day views out of the box; the free core covers everything we need |
| Data, Phase 1 | Seed JSON + `localStorage` for new writes | Zero backend setup; the demo works identically on the demo laptop |
| Data, Phase 2 | Supabase (Postgres + Auth + row-level security) | Swap in behind the same functions if time allows |
| AI search | One server route calling the Claude API (a fast model like `claude-haiku-5-5`) | Turns plain English into structured filters; the API key stays server-side |
| Hosting | Vercel (Hobby tier), GoDaddy DNS pointed at Vercel | `main` → collectivize.co; every branch gets a preview URL |

Your CS cofounder has final say. If he's faster in something else, use that, but keep the data-layer contract below.

### The core idea: a data-layer contract

Every screen gets and changes data **only** through the functions in `lib/data/`. In Phase 1 those functions read the seed JSON and write to `localStorage`. In Phase 2 they call Supabase. Screens never change. **Every function is `async` from day one**, even with fake data, so the swap is painless.

### Folder structure and ownership

```
collectivize/
├── app/                      ← James: every page/screen
│   ├── page.tsx              ← Home (hero → calendar → discover)
│   ├── join/                 ← invite + onboarding
│   ├── network/              ← graph page
│   ├── p/[handle]/           ← person page
│   ├── o/[slug]/             ← org page (+ admin edit mode)
│   ├── profile/edit/         ← customization editor
│   ├── settings/
│   └── api/                  ← CS cofounder: server routes
│       ├── search/route.ts   ← natural-language → filters (stretch)
│       └── orgs/[slug]/calendar.ics/route.ts   ← .ics feed (stretch)
├── components/               ← James: cards, blocks, calendar UI, graph styling
├── lib/
│   ├── types.ts              ← BOTH: change only after telling each other
│   ├── data/                 ← CS cofounder: the contract (mock now, real later)
│   │   ├── index.ts          ← exports every data function
│   │   ├── mock.ts           ← seed-backed implementation
│   │   └── visibility.ts     ← canSee()
│   ├── graph.ts              ← CS cofounder: build graph, findPath, inCommon
│   └── ranking.ts            ← CS cofounder: Discover scoring
├── data/seed.json            ← business team: fictional demo dataset
├── docs/PLAN.md              ← this file
└── CLAUDE.md                 ← rules for Claude Code (see Section 12)
```

The graph page is the one place to pair: the CS cofounder wires up `lib/graph.ts`, James makes it look good.

### Data shapes (`lib/types.ts`)

Agree on these in the first hour. They are a starting point; adjust together.

```ts
export type ID = string;

export type Theme = {
  preset: "classic" | "midnight" | "sunset" | "forest" | "paper";
  accent: string;                 // hex, e.g. "#2F6FEB"
  font: "sans" | "serif" | "mono" | "display";
  bannerUrl?: string;
};

export type Block =
  | { type: "about"; text: string }
  | { type: "orgs" }                       // auto-filled from memberships
  | { type: "events" }                     // auto-filled: hosted/upcoming
  | { type: "members" }                    // orgs only
  | { type: "links"; links: { label: string; url: string }[] }
  | { type: "photo"; url: string; caption?: string }
  | { type: "website"; url: string }       // featured external site card
  | { type: "text"; title: string; body: string };

export type Person = {
  id: ID;
  name: string;
  handle: string;                 // used in /p/[handle]
  email: string;                  // must end in @northeastern.edu
  avatarUrl?: string;
  bio: string;
  theme: Theme;
  blocks: Block[];
  invitedBy?: ID;
  privacy: { hideConnections: boolean; rsvpVisibility: "connections" | "nobody" };
};

export type Org = {
  id: ID;
  name: string;
  slug: string;                   // used in /o/[slug]
  kind: "greek" | "club" | "sport" | "council" | "other";
  description: string;
  websiteUrl?: string;
  parentOrgId?: ID;
  tags: string[];
  theme: Theme;
  blocks: Block[];
};

export type Membership = { personId: ID; orgId: ID; role: "admin" | "member"; status: "pending" | "accepted" };
export type Connection = { a: ID; b: ID; source: "invite" | "request"; status: "pending" | "accepted" };

export type Visibility = "public" | "members" | "invite";

export type Event = {
  id: ID;
  title: string;
  description: string;
  start: string;                  // ISO datetime
  end: string;
  location: string;
  hostType: "person" | "org";
  hostId: ID;
  coHostOrgIds?: ID[];
  visibility: Visibility;
  inviteeIds?: ID[];              // only for "invite"
  tags: string[];
};

export type RSVP = { eventId: ID; personId: ID; status: "going" | "interested" };
export type Invite = { code: string; inviterId: ID; usedBy?: ID };

// What screens receive (already filtered by canSee)
export type EventCard = Event & {
  hostName: string;
  hostHref: string;               // /o/slug or /p/handle
  friendsGoing: { id: ID; name: string }[];
  myRsvp?: RSVP["status"];
};

// What the graph page receives
export type GraphNode = { id: ID; kind: "person" | "org"; label: string; href: string; hidden?: boolean };
export type GraphLink = { source: ID; target: ID; kind: "connection" | "membership" };

export type EventQuery = {
  text?: string;
  tags?: string[];
  from?: string;
  to?: string;
  hostOrgId?: ID;
  sort?: "for-you" | "soonest";
};
```

### Data functions (`lib/data/index.ts`)

```ts
// Session (persona switcher in Phase 1)
getCurrentUser(): Promise<Person>
setCurrentUser(personId: ID): Promise<void>
listPersonas(): Promise<Person[]>

// People & orgs
getPerson(handle: string): Promise<Person | null>
getOrg(slug: string): Promise<Org | null>
listOrgs(): Promise<Org[]>
getMembers(orgId: ID): Promise<Person[]>
getOrgsFor(personId: ID): Promise<Org[]>
updatePerson(personId: ID, patch: Partial<Person>): Promise<Person>
updateOrg(orgId: ID, patch: Partial<Org>): Promise<Org>

// Membership & connections
requestMembership(orgId: ID): Promise<void>
decideMembership(orgId: ID, personId: ID, accept: boolean): Promise<void>
getPendingRequests(orgId: ID): Promise<Person[]>
redeemInvite(code: string, email: string): Promise<Person>   // validates @northeastern.edu

// Graph
getGraph(): Promise<{ nodes: GraphNode[]; links: GraphLink[] }>
findPath(fromId: ID, toId: ID): Promise<ID[] | null>
getInCommon(aId: ID, bId: ID): Promise<{ people: Person[]; orgs: Org[] }>

// Events
getEvents(query: EventQuery, page: number): Promise<{ items: EventCard[]; total: number }>  // 10 per page
getCalendar(from: string, to: string): Promise<EventCard[]>
getEvent(id: ID): Promise<EventCard | null>
createEvent(input: Omit<Event, "id">): Promise<Event>
rsvp(eventId: ID, status: RSVP["status"] | null): Promise<void>

// Search (stretch)
parseSearch(text: string): Promise<EventQuery>   // calls /api/search; falls back to { text }
```

### Graph logic (`lib/graph.ts`)

The graph is undirected. Nodes are people and orgs. Edges are accepted connections (person to person) and accepted memberships (person to org). Pending items are not edges.

**Focus view.** The viewer starts centered with their direct neighbors. Focusing node X shows X's neighbors plus the path from the viewer to X. Earlier neighborhoods collapse except for nodes on the current path, which keeps the screen readable and rendering cheap. Aim for no more than about 150 visible nodes.

**Path.** A breadth-first search from viewer to target returns the shortest path, capped at 4 hops. Beyond that, show "No close connection yet" rather than a meaningless long chain.

**In common.** Mutual people are the intersection of both people's accepted connections; shared orgs are the intersection of their accepted memberships. Show this first in the side panel, with the path highlighted in the graph.

**Privacy in v1.** If a person on the path has `hideConnections` turned on, render them as an anonymous "a mutual connection" node and leave them out of the in-common list. Member lists of orgs are visible to all signed-in users in v1; this is one of the questions for the full privacy breakdown (Section 11).

**Rendering.** `react-force-graph-2d` uses the browser's `window`, so load it with `next/dynamic` and `{ ssr: false }`. Draw people as blue circles and orgs as red squares (matching the whiteboard key), with membership edges and friendship edges styled differently.

### Discover ranking (`lib/ranking.ts`)

Filter with `canSee()` first, then apply the query, then score:

```
score = 3 × (number of connections going)
      + 2 × (hosted or co-hosted by one of my orgs)
      + 1 × (tag overlap with my orgs' tags)
ties → soonest first; past events excluded
```

Return 10 per page with numbered pagination and a "Showing 1–10 of N" label. No infinite scroll, ever. "Friends going" only counts people whose `rsvpVisibility` is `"connections"`.

### Natural-language search (stretch)

`/api/search` sends the user's text to the Claude API with instructions to return only JSON matching `EventQuery`, using today's date to resolve phrases like "this weekend." The route validates the JSON and falls back to `{ text }` keyword search on any failure, so search never breaks during the demo. The API key lives in a Vercel environment variable (`ANTHROPIC_API_KEY`), never in frontend code.

### Calendar feed (stretch)

`/api/orgs/[slug]/calendar.ics` returns the org's public events in iCalendar format. An admin can subscribe to it in Google Calendar or embed it on the org's own website. This is the honest, cheap version of "post once, appears everywhere."

### Phase 2: Supabase (only if time allows)

Create tables that mirror `types.ts`. Enforce visibility with row-level security policies that implement the same rules as `canSee()`. Use magic-link auth with a northeastern.edu domain check at signup. Supabase's default email sender is rate-limited, so a custom email provider is needed before real users; keep the persona switcher for the demo either way.

### Deployment

The GitHub repo and Vercel account are owned by the CS cofounder (Vercel Hobby is single-user). James is a collaborator on the repo. `main` deploys to collectivize.co. Every other branch deploys to its own preview URL for testing on a phone or sharing with teammates. Day-to-day work happens on localhost with `npm run dev`.

---

## 5. Seed Data Spec (business teammate)

Deliver a first version by **hour 3**. It must be dense enough that the graph looks alive.

| Item | Target | Requirements |
|---|---|---|
| People | ~40 | Fictional names; realistic bios; 3–8 connections each; 1–3 org memberships each; a few with `websiteUrl` |
| Orgs | ~10 | Mix of Greek chapters, clubs, a sport, and one council with 2–3 child chapters; distinct themes |
| Events | ~30 | Spread across 3 weeks around demo day; all three visibility levels; overlapping tags (surf, food, night, service, sports); at least one co-hosted |
| RSVPs | ~80 | Clustered so "friends going" shows real numbers for the demo personas |
| Personas | 3 | A new student (demo protagonist), an org admin, a well-connected senior |

**Rules.** All people and events are clearly fictional. Do not use real members' names or real chapter member lists. A real org (for example, linking a chapter's official website) appears only with that org's permission. Nothing should imply Northeastern University endorsement.

Build it in a spreadsheet with one tab per type, then convert it to `data/seed.json` (Claude can do the conversion).

---
## 6. Team & Roles

| Person | Owns | Done means |
|---|---|---|
| James | Every screen in `app/` and `components/`; UX decisions; demo narrative | The demo path looks and feels finished on the live site |
| CS cofounder | `lib/`, `app/api/`, repo, Vercel, deployment | Every data function works on seed data; visibility, graph, and ranking are correct |
| Teammate A | Seed data | `seed.json` is dense, fictional, and makes the demo personas look great |
| Teammate B | Validation | 10–15 short interviews; one slide of real quotes and counts |
| Teammate C | Pitch | Deck, script, timing, rubric check, backup demo video |

**Interview questions for Teammate B:** How do you find out about events and clubs right now? When did you last miss something you would have gone to, and why? If you could see how you're connected to anyone on campus and what your friends are going to, would you use that? What would make you stop using it? Write down exact quotes and count answers. Do not claim more than you collected.

---

## 7. Timeline (about 18 build hours)

| Hours | Frontend (James + Claude Code) | Backend (CS cofounder + Claude Code) | Business team |
|---|---|---|---|
| 0–1 | **Together:** walk the demo path, agree on `types.ts`, folder ownership, and this doc | Repo, Next.js app, Vercel, domain, CLAUDE.md | Find the judging rubric and deadline; start seed sheet; start interviews; deck skeleton |
| 1–4 | App shell: sidebar, Home hero, routing; person and org page layouts with blocks | `mock.ts` from seed, persona switcher, `canSee()`, people/org functions | Seed data v1 by hour 3 |
| 4–9 | Network page: graph styling, side panel, search box, focus behavior | `graph.ts`: build graph, `findPath`, `getInCommon`; wire into the page | Interviews; deck draft |
| **9** | **Checkpoint:** if the graph isn't working well, simplify to focus view plus a path list and move on | | |
| 9–13 | Calendar with day panel, Discover cards and pagination, event modal, post-event form | `getEvents` with ranking, filters, pagination; `createEvent`, `rsvp`, `localStorage` writes | Interview synthesis slide; demo script v2 |
| 13–15 | Profile editor with live preview and "View as visitor"; org admin mode | Membership requests and approvals; privacy settings wired into graph and RSVPs | Deck v2; first rehearsal |
| 15–17 | Polish the demo path; check on a phone and a projector-sized screen | Stretch in order: natural-language search, then .ics feed | Rehearse against the live site |
| 17–18 | **Code freeze.** Only fixes to the demo path | **Code freeze.** Record the backup demo video | Final rehearsal; submission materials |

Pitch prep and rehearsal continue after the freeze. Nothing new gets merged after the freeze, no matter how small.

---

## 8. Git Workflow (beginner-friendly)

1. Never work directly on `main`. Each feature gets its own branch: `frontend-calendar`, `backend-graph`, and so on.
2. Before starting anything new, pull the latest `main`.
3. Commit small and often. Push your branch and check its Vercel preview URL.
4. Merge through a pull request on GitHub, several times a day. Waiting to merge everything at the end is how hackathon projects die.
5. You can ask Claude Code to do the git steps for you, for example: "pull the latest main, then make a new branch called frontend-calendar," and later "commit my changes and push this branch."

---

## 9. Pitch

**Structure (about 3 minutes plus the demo):**

1. **Problem (30s).** A real story of missing something good because it was buried in a group chat or an Instagram story. Information about campus life is scattered across platforms built for other things.
2. **Insight (15s).** Your network is the best discovery engine on campus, but no tool actually uses it.
3. **Live demo (90s).** The five beats from Section 2.
4. **Evidence (20s).** What students told us this weekend, with real quotes and counts.
5. **Principles (20s).** No infinite feed. No selling data. Private events stay invisible. Success is measured by people who show up.
6. **Vision (20s).** Northeastern first, then other universities, then cities. People and communities with their own identities, connected.
7. **Business (one line).** Free core platform; later revenue from hosted events, sponsorships, and website-builder referrals. Not validated yet, and we'll say so.

**Positioning.** Present Engage as something we complement, not something we attack; the judges may be Northeastern people. Don't claim traction, partnerships, or university affiliation we don't have.

**Likely judge questions to prepare:** How do you handle privacy in the graph? Why would students use this instead of Instagram? How do you solve the empty-calendar problem? How do organizations keep it up to date? How will you make money without ads?

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Graph takes too long | Hour-9 checkpoint; fall back to focus view plus a path list |
| Graph crashes on load in Next.js | Load it with `next/dynamic` and `ssr: false` |
| Scope creep | If it's not in Section 2, it waits; the stretch list is in priority order |
| Merge conflicts | Folder ownership; small, frequent merges |
| Live demo fails | Backup video; demo laptop with the site already loaded; localhost as a fallback |
| Graph looks empty | Dense seed data with deliberate clusters |
| AI search fails mid-demo | Automatic fallback to keyword search |
| Privacy questions from judges | Use the v1 rules in this doc and say a full privacy breakdown is next |

---

## 11. Open Questions (after the hackathon)

1. **Privacy breakdown.** Who can see your connections, org member lists, RSVPs, and paths through you? What are the defaults? How do people control them? This needs its own thorough session.
2. **Real identity.** Real login, .edu verification, invite limits, and how organizations prove an admin is legitimate.
3. **Cold start.** Concierge seeding of real orgs with their approval versus any form of importing public posts; the ethics of each.
4. **Calendar integration.** Two-way sync with Google Calendar and org websites.
5. **Northeastern Greek.** Is it still a separate brand and pilot, or has Collectivize absorbed it?
6. **Revenue.** Website-builder referrals (e.g., GoDaddy), events, and sponsorships, and which ones fit a no-feed, no-data-selling product.
7. **Measurement.** How to measure discovery-to-attendance without tracking people more than they've agreed to.
8. **Strategy doc.** Update Core Strategy & Vision with this weekend's decisions.

---

## 12. Rules for Claude Code (copy into `CLAUDE.md`)

```markdown
# CLAUDE.md: Collectivize

Read docs/PLAN.md before doing anything. The demo path in Section 2 is the spec.

## Scope
- Build only what is in the Must list or the Stretch list, in that order. Ask before adding anything else.
- Hidden for now: Explore, Debate, Collaborate, Projects. Do not build them.

## Ownership
- Frontend sessions edit only app/ (except app/api/) and components/.
- Backend sessions edit only lib/, app/api/, and config.
- Never change lib/types.ts without saying so explicitly in the summary of your work.

## Data
- Screens get and change data ONLY through functions exported from lib/data/index.ts.
- Every data function is async, even when backed by mock data.
- Visibility is decided only by canSee() in lib/data/visibility.ts. UI code never filters by visibility itself.
- Members-only and invite-only events must be completely invisible to non-viewers.

## Product rules
- Discover uses numbered pagination, 10 per page. Never add infinite scroll or autoplay.
- User customization is structured (theme, accent, font, banner, blocks). Never render user-supplied HTML or CSS; never use dangerouslySetInnerHTML on user content.
- All demo data is fictional. Never invent real organization details or imply Northeastern University endorsement.

## Technical
- Next.js App Router + TypeScript (strict) + Tailwind + shadcn/ui.
- Load react-force-graph-2d with next/dynamic and { ssr: false }.
- Secrets (e.g. ANTHROPIC_API_KEY) live only in environment variables and server routes.
- Keep changes small. Summarize what you changed and how to test it.
```