# Collectivize Prototype: V1 → V1.1 Change Spec

> Source: a team design-review session (transcribed) walking through the current V1 prototype on screen.
> Audience: Claude Code, implementing the changes. Date: Nov 14, 2026.

---

## 0. Read this first (instructions for Claude Code)

You are editing an existing prototype. This spec was written **without access to the codebase**. Its "Current state" lines describe what the team saw on screen, so they may not match the code exactly.

Before writing any code:

1. **Explore the repo.** Identify the framework, routing, data layer (mock data vs. database), styling system, and where these live: Discover/search, event card and event detail, calendar, profile, the network/graph placeholder, the "Viewing as" switcher, sign-in, and the logo.
2. **Reply with a short plan.** For each section in §4, list the files you'll touch and any place where the code contradicts a "Current state" line. If a contradiction changes the approach, stop and wait for confirmation.
3. **Implement in the priority order in §3.** One section per commit, with the section number in the message (e.g. `4.4 unified search + filter dropdown`).
4. **Reuse what exists.** Use the existing components, styles, and data patterns. Don't add a new framework, state library, or backend unless a section requires it and you said so in the plan.
5. **Respect scope.** Never build anything in §6 (Backlog). Build §5 (Stretch) only after everything in §4 passes the §8 checklist.
6. **When something is ambiguous,** pick the simplest option consistent with §1, leave a `// SPEC-ASSUMPTION:` comment, and list it in your final summary.

The brand name is **Collectivize**. (The transcript sometimes says "collectivized"; that's a transcription error.)

### Tag legend

- **[Decided]**: the team explicitly agreed.
- **[Leaning]**: discussed and favored, not fully settled. Implement it, but keep it easy to change.
- **[Suggested]**: not from the team. Added by the spec author for safety, feasibility, or consistency. Implement unless it conflicts with existing code, and flag it if you skip it.
- **[Verify]**: something to check, not necessarily change.

---

## 1. Principles that should shape implementation choices

- **Real life first.** Features exist to get people to events and into communities, not to maximize screen time. No infinite scroll and no engagement bait.
- **Privacy by structure.** What a user can see depends on their connection to the subject, and that rule lives in **one place** in the code (§4.1).
- **Start simple, extend later.** Limited customization now, more later.
- **Demo data is demo data.** [Suggested] Seed content should be obviously fictional. Don't invent real details about real chapters or imply university endorsement.

---

## 2. Glossary

- **Node:** any searchable entity: a person (user), an organization, or an event. Search covers all three. The personal network graph shows people and organizations only.
- **Connection:** a mutual link between two users, created when one sends a request and the other accepts.
- **1st / 2nd / 3rd+ degree:** LinkedIn-style distance. 1st = direct connection. 2nd = a connection of a connection. 3rd+ = further away.
- **Platform admin:** Collectivize staff or moderator. Not the same as an org officer.
- **Org admin:** a user with the admin role in one organization (e.g. a chapter VP).
- **RSVP states:** Going and Maybe.
- **Saved:** bookmarked via the bookmark icon (the team also calls it "starred").

---

## 3. Priority order

**P0, demo-critical, in this order:**

1. 4.1 Data model and the visibility module
2. 4.9 Remove "Viewing as"; add the admin page and org admin role (quick, and removes confusing demo UI)
3. 4.10 Access gate; remove the on-campus toggle
4. 4.2 Connections and the network graph (the largest item; the team considers it the last missing piece of the first prototype)
5. 4.3 Profile page
6. 4.4 Unified search and filter dropdown
7. 4.5 Result ranking and pagination
8. 4.6 RSVP, Saved, and My Events
9. 4.7 Personal calendar
10. 4.12 Logo (timeboxed)

**P1, cheap, after P0:** 4.8 Add to external calendar; 4.11 Invite/connection preview card.

---

## 4. Changes

### 4.1 Data model and the visibility module (prerequisite)

**Current state:** unknown. V1 has users, organizations, and events (likely mock data), and the "Viewing as" switcher implies a notion of current user.

**Requirements.** Adapt names to the existing code and add only what's missing:

- `User`: id, name, email, photo, bio, `top3` { title, items[3] }, `theme` { outline, background }, `isPlatformAdmin`
- `Organization`: existing fields, plus category if missing
- `Membership`: userId, orgId, role `member` | `admin`
- `Connection`: requesterId, recipientId, status `pending` | `accepted` | `declined`, createdAt. Accepted means mutual 1st degree.
- `Event`: existing fields, plus category, start, end, optional capacity, hostOrgId and/or hostUserId
- `RSVP`: userId, eventId, status `going` | `maybe`
- `Saved`: userId, eventId

**Seed data** [Suggested]: enough mock users and connections to demo 1st and 2nd degree. At minimum, an Alice–Bob–Carol chain where Alice is not connected to Carol, plus one org with an org admin and one user with 100+ connections (to test render caps).

**Visibility module** [Suggested structure; it implements Decided rules]. Create one module (e.g. `lib/visibility.ts`) that every screen uses:

- `degree(viewerId, userId)` → 0 (self), 1, 2, 3 (meaning 3+), or null (no path). BFS, depth capped at 3.
- `canViewNetwork(viewer, subject)` → true only if self or 1st degree.
- `visibleProfileSections(viewer, subject)` → per the table in §4.3.
- `visibleNetwork(viewer, subject)` → nodes and edges per §4.2.

If there is a real backend, enforce these rules where data is fetched (API, server, or row-level security), not only by hiding UI. Otherwise the privacy model is cosmetic: anyone can open dev tools and see the whole graph. If the demo is all client-side mock data, keep the rules in this module and note the gap in your summary.

---

### 4.2 Connections and the network graph

**Current state:** not built. The profile has a placeholder circle under "Network", and the team sketched a circle containing nodes.

**Rules** [Decided unless tagged]:

1. Connections are mutual. Send a request; the recipient accepts; both become 1st degree. There are no restrictions on who can connect (e.g. someone you just met at an event).
2. **Your network view:** you at the center in a distinct color, your 1st-degree connections, and your organizations.
3. **A connection's network:** open the profile of a 1st-degree connection and you see their network: their connections and their organizations. These are your 2nd degree. Nothing beyond that.
4. **One hop only.** From a connection's network you cannot open a 2nd-degree node's network. No chained hopping.
5. **Non-connections:** you cannot see their network at all.
6. **From a 2nd-degree node** in a connection's network you can send a connection request ("quick add"). Inviting them to an event is [Leaning]: build it if an event-invite flow exists, otherwise stub the button.
7. **Organization membership cannot be hidden.** If you're a member, that membership edge appears in your network wherever your network is visible. (See open questions in §7.)
8. Once you connect with anyone, at any distance, they become 1st degree.
9. Events are **not** nodes in the network view. They are nodes in search (§4.4).

**Rendering:**

- One fixed-size circular graph component, reused in two places: your own profile ("Network") and a connection's profile. [Decided]
- Mobile-first: drag with a finger to pan, and the component stays the same size. [Decided] Pinch-zoom is optional. [Suggested]
- Never render the whole network. Cap visible nodes and offer "Show more" to load the next batch. [Decided] Suggested default cap: 25 people plus up to 10 orgs. [Suggested numbers]
- Ordering within the cap: random or selective, but expandable. [Leaning] Suggested order: mutual connections, then orgs, then others.
- In a connection's network, mark nodes that are already your 1st degree, and show a "Connect" affordance on the rest. [Suggested]
- Tapping a node opens that person's profile or that org's page, subject to §4.3 visibility.
- Library: use whatever is in the repo. Otherwise use something lightweight (d3-force or a small hand-rolled layout), not a heavy graph framework. [Suggested]

**Connection request flow:**

- A "Connect" button on profiles of non-connections creates a pending request.
- The recipient sees the request with the requester's profile card (§4.11) and Accept / Decline.
- On accept, show a one-line notice: "Your connections will be able to see that you're connected to [name]." Under rule 3, your connections' connections can see you in their friend's network, and the team named this consequence out loud. [Suggested wording]

**Acceptance criteria:**

- [ ] Alice is connected to Bob, Bob is connected to Carol, Alice is not connected to Carol. Alice's network shows Bob, not Carol.
- [ ] Alice opens Bob's profile and sees Bob's network, including Carol and Bob's orgs.
- [ ] Alice taps Carol in Bob's network. Carol's profile opens in the non-connected view, Carol's network is not shown, and "Connect" is available.
- [ ] Alice sends a request and Carol accepts. Carol now appears in Alice's network.
- [ ] A user with 100+ connections renders at most the cap, with "Show more".
- [ ] The graph pans by touch on a phone-width viewport.

---

### 4.3 Profile page

**Current state:** a simple layout with a "Top 3" block (example title: "study spots") and the network placeholder. The team likes the simplicity.

**Direction** [Decided]: a mix of LinkedIn and Instagram, with a nod to early-MySpace self-expression. Start simple; add customization later. Too much creative freedom up front just produces messy profiles.

**Structure:**

- **Profile card** (top section): photo, name, short bio, pictures, and the Top 3 block. This is also the preview shown when you send a connection request or invite (§4.11).
- **Below the card (scroll down):** organizations you belong to, and events you've attended, are going to, and have hosted. Photos linked to events are [Leaning] and can slip to P1.
- **Network:** the graph from §4.2.
- **Your own profile only:** access to your personal calendar (Going / Maybe / Saved). The simplest version is a compact upcoming list or a link to §4.7. [Decided that it belongs here; Leaning on format]

**Top 3 block** [Decided]: the "Top 3" label and the 1/2/3 numbering are fixed. The user types the category title (e.g. "study spots", "hobbies") and the three items. Free text with a reasonable character limit. [Suggested limit]

**Customization v1** [Leaning]: outline/accent color and background color, chosen from a small preset palette. Nothing else yet. Make sure text stays readable on every preset. [Suggested]

**Auto-fill** [Decided]: organizations and events come from data. The user never types them.

**Visibility** [Decided except where tagged]:

| Section | You | 1st-degree connection | Non-connected NU user | Preview card (request or invite) |
|---|---|---|---|---|
| Profile card | Full | Full | Partial: photo, name, bio, Top 3 [Leaning on which parts] | Full |
| Organizations list | Yes | Yes | No | No |
| Events (attended / going / hosted) | Yes | Yes | No | No |
| Network graph | Yours | Theirs (one hop) | No | No |

Notes:

- Events never appear on the profile card itself. They change constantly and have mixed privacy.
- Per-block or per-event visibility toggles (public / friends / private) were explicitly rejected as too complex for the value. Don't build them. [Decided]
- Organizations are hidden from non-connections on the profile page, but membership can't be hidden in the graph (§4.2 rule 7). Both are true. See §7 on org member lists.

**Acceptance criteria:**

- [ ] Your own profile has edit controls for photo, bio, pictures, Top 3 title and items, outline color, and background color.
- [ ] A 1st-degree connection's profile shows every section in that column of the table.
- [ ] A non-connection's profile shows only the partial card plus a Connect button.
- [ ] Events appear only below the card, only to self and connections.

---

### 4.4 Unified search and filter dropdown

**Current state:** Discover search returns events only. A date filter exists.

**Requirements** [Decided unless tagged]:

- Search returns **people, organizations, and events** in one result list.
- A filter button (funnel icon) sits immediately **left of the search input**. Tapping it opens a dropdown. Filters are not permanently shown under the search bar, to save space.
- The dropdown uses **checkboxes** (multi-select, not radio buttons):
  - Type: People, Organizations, Events
  - Subject/category: the existing categories
  - Keep the existing date filter; moving it into the dropdown is fine if it fits the layout. [Suggested]
- **No "All" checkbox.** Use a **Clear** button instead. With nothing checked, everything shows; that's the default.
- Every result clearly shows its type (icon or label).
- Show a small count badge on the funnel icon when filters are active. [Suggested]

Use cases to sanity-check against: find like-minded people to invite to your event (People only); find a club to join (Organizations only); see what's on Saturday (Events plus date).

**Acceptance criteria:**

- [ ] A default search term matches across all three types.
- [ ] Checking People + Organizations excludes events.
- [ ] Clear resets every filter.
- [ ] The dropdown closes on an outside tap, and filter state persists while paging.
- [ ] Discover accepts a date preset from the URL (e.g. `/discover?date=2026-11-21`), which §4.7 needs. [Suggested implementation]

---

### 4.5 Result ranking and pagination

- Results stay ranked by relevance, but **the match percentage is no longer shown to users**. The team's phrasing: rank it that way, don't show it that way. [Decided]
- The score may be shown in the platform admin view (§4.9) for debugging. [Leaning]
- People results should favor graph proximity: closer people rank higher, because search should connect you to the closest people for what you're looking for. [Decided]
- **Don't show degree badges** (2nd/3rd) on results. [Decided, lightly discussed] **Never show the path** of intermediaries between you and a distant person. [Decided]
- Pagination stays: 10 per page, controls at the bottom, no infinite scroll. [Decided]
- [Verify] The range label on the last page. The team saw something like "Showing 41–44" with 45 results. Confirm whether that's an off-by-one.

---

### 4.6 RSVP, Saved, and My Events

**Current state:** "I'm going" changes its label to "You're going", but nothing is stored. The bookmark (top-left of the event card) saves to the calendar, but there's no saved-events list. A "Maybe" state appears to exist.

**Requirements:**

- Persist RSVP (Going / Maybe) and Saved per user, using whatever persistence V1 uses. A localStorage or mock store is fine for the demo if there's no backend. [Decided; storage choice Suggested]
- Both are reversible (un-going, un-saving). [Suggested]
- New **My Events** page (the team calls it the "catalog"): Going, Maybe, and Saved as tabs or sections, upcoming first. [Decided] Past events go in a collapsed section. [Suggested]
- RSVP and Saved changes update the personal calendar (§4.7) immediately. [Decided]
- Event capacity [Leaning]: if an event has a capacity and the Going count reaches it, RSVP closes and the event shows "Full".

**Acceptance criteria:**

- [ ] Mark Going, reload: still Going, and the event appears in My Events and on the calendar.
- [ ] Bookmark an event: it appears under Saved.
- [ ] Removing either one removes it everywhere.

---

### 4.7 Personal calendar

**Current state:** a "master calendar" of all events, with day labels like "1 on campus" and "2 on campus".

**Requirements:**

- The calendar becomes the user's **personal** calendar, not a master calendar. Discovery happens in Discover, not here. [Decided]
- It shows events you're Going to, Maybe, and Saved, plus events from organizations you're a member of. Color-code by status (going / maybe / saved / my-org event) with a small legend. [Decided]
- Remove the "N on campus" day labels and replace them with colored chips or dots per event status. [Decided to remove; Leaning on the replacement visual]
- Tap a day to expand that day's schedule. Inside it, a button: **"Find events on [Saturday, Nov 21]"** opens Discover with that date filter applied. [Decided]

Not now: two-way calendar sync (§6). In-app conflict warnings are Stretch (§5).

**Acceptance criteria:**

- [ ] The calendar shows only my Going / Maybe / Saved events and my orgs' events.
- [ ] No "on campus" labels remain.
- [ ] Day tap → "Find events" → Discover filtered to that date.

---

### 4.8 Add to external calendar (P1, one-way only) [Decided]

- On event detail and in My Events, add an "Add to calendar" menu: **Google Calendar, Outlook, Apple (.ics)**. The team noted that Northeastern .edu accounts use Outlook.
- **Google:** template URL: `https://calendar.google.com/calendar/render?action=TEMPLATE&text=…&dates=YYYYMMDDTHHMMSSZ/YYYYMMDDTHHMMSSZ&details=…&location=…`
- **Outlook (Microsoft 365):** `https://outlook.office.com/calendar/0/deeplink/compose?subject=…&startdt=…&enddt=…&body=…&location=…` with ISO datetimes. [Verify the URL format works with a Northeastern account]
- **Apple Calendar** (also works for desktop Outlook): generate an `.ics` file client-side (RFC 5545) and download it.
- URL-encode every field and convert times to UTC correctly. Check each link opens with the right time in America/New_York.
- No OAuth and no sync.

---

### 4.9 Admin: remove "Viewing as", add the admin page and org admin role

**Current state:** a "Viewing as" switcher in the main UI lets anyone switch between users.

**Requirements:**

- Remove "Viewing as" from all regular UI. [Decided]
- Add a **platform admin page** (e.g. `/admin`), accessible only when `isPlatformAdmin` is true. An admin can search any user and "View as" them to see the app from their perspective (what they've seen and sent), for investigating rule-breaking. [Decided]
- Guardrails [Suggested, strongly]: make view-as **read-only** (no actions taken as the user), show a persistent "Viewing as [name] (admin)" banner with a one-click exit, and log each session (admin, target, timestamp). Unguarded impersonation is exactly the kind of thing that destroys trust if it ever surfaces.
- The admin page may show match scores (§4.5). [Leaning]
- **Org admin role** [Decided]: org admins can edit their org's page, invite or add members, and remove members. They **cannot** impersonate anyone or see member profile content beyond the normal visibility rules.
- For the demo, a hard-coded platform admin account is fine. [Suggested]

**Acceptance criteria:**

- [ ] No "Viewing as" control is visible to normal users or org admins.
- [ ] `/admin` redirects non-admins away.
- [ ] An org admin can add and remove members of their own org only.

---

### 4.10 Access gate

**Requirements:**

- Campus-only for now. Sign-in requires a Northeastern email (`@northeastern.edu`). [Decided] Keep the allowed domain(s) in one config value. [Suggested]
- Signed-out visitors can reach the landing/sign-in page and nothing else. Every other route redirects there. [Decided]
- For the demo, it's just a sign-in. Real email verification and invite-only access are future work; don't build them. [Decided]
- The landing page should still be worth seeing: the logo animation (§4.12) and a one-line description. [Suggested]
- **Remove the on-campus/off-campus toggle entirely.** Everything is on-campus now. [Decided]

**Acceptance criteria:**

- [ ] `someone@gmail.com` cannot sign in, and the message explains that a Northeastern email is required.
- [ ] Visiting `/discover` while signed out redirects to the landing page.
- [ ] No on/off-campus toggle exists anywhere.

---

### 4.11 Invite / connection preview card (P1)

**Concept** [Decided]: when you send a connection request or share an invite link, the recipient sees **your profile card** as the preview. The growth loop: people see well-made profiles and want to build their own.

- Build one reusable `ProfileCard` (the same component as the top of the profile in §4.3). Use it in incoming connection requests and on the invite landing page.
- Invite links (e.g. `/invite/:code`) open a landing page with the inviter's card plus sign-in, consistent with the access gate.
- Rich previews inside iMessage or WhatsApp (Open Graph images) need server-side image rendering. That's backlog unless the stack makes it trivial. [Suggested]

---

### 4.12 Logo (timeboxed)

**Current state:** the logo is a screenshot image. Replace it.

**Direction** (the team's latest consensus) [Decided concept, Leaning on details]: the logo is made of nodes. The mark breaks apart into particles ("the pixels are nodes"), the particles fly out and spell **Collectivize**, then they reform into the mark, looping continuously. The word reacts to the cursor or finger. The particles aren't plain dots: each one is a glyph (`@ # * + = - .`), with heavier glyphs where the source image is denser. This was inspired by an ASCII-particle website the team looked at.

**Spec:**

- **Two assets:** a static logo for the nav and favicon (SVG or a crisp PNG from the source file, not the screenshot), and the animated hero on the landing/sign-in page. Keep the nav logo static. [Suggested placement; the team didn't decide]
- **Implementation:** one `<canvas>` driven by `requestAnimationFrame`.
  1. Draw the source logo to an offscreen canvas and sample pixels on a grid to get target points and brightness for state A (the mark).
  2. Draw "Collectivize" in the brand font offscreen and sample it the same way for state B (the wordmark).
  3. Use one particle pool sized to the larger state. Extra particles fade out in the smaller state.
  4. Loop: hold the mark (~1.5s) → scatter and swirl outward ("unravel") → ease into the wordmark → hold (~3s) with pointer interaction (particles near the pointer get pushed away and spring back) → contract back into the mark. Durations are placeholders.
  5. Choose each particle's glyph by sampled brightness, and take colors from the logo's palette.
- Support mouse and touch, scale for `devicePixelRatio`, pause when the tab is hidden or the canvas is offscreen, and respect `prefers-reduced-motion` by showing the static logo. [Suggested]
- **Timebox: about 2 hours.** [Suggested] The team itself said this could eat the rest of the hackathon. If it isn't convincing by then, ship the simpler version the team also liked: the mark spins like a fidget spinner while shrinking, slides right, the wordmark spells out beside it, then it reverses. CSS/SVG is enough for that.
- **Needs the original logo file** (SVG or high-res PNG). If the repo only has the screenshot, stop and ask.

---

## 5. Stretch (only after §4 passes §8)

- **Event audience and reach** [Leaning; the builder's idea]. When creating an event, choose an audience: "My organization only" or "Northeastern". For Northeastern-wide events, choose reach: Low, Moderate, or High. Reach controls how many people the event is surfaced to, picked by breadth-first traversal of the graph from the host (and the host org's members) outward, closest people first. Traversal stops when the reach limit or the event capacity is hit. Placeholder limits: 25 / 100 / 400. Low suits a small pottery night; High suits a big Halloween party. Default surfacing mechanism: Discover recommendations only, no notifications (see §7).
- **In-app time-conflict badge** [Suggested]: on event cards, flag overlap with events you're already Going to. (Conflicts with external calendars need two-way sync, which is backlog.)
- **Photos linked to events** on the profile, if not done in §4.3.

---

## 6. Backlog: do not build now

- **Two-way calendar sync** (Google / Outlook / Apple): pull external events in for free/busy and conflicts, push Going events out. Explicitly deferred. The team's concern: users don't want homework or interviews inside Collectivize, so any future import should be free/busy only.
- Invite-only access or a referral gate.
- Public (non-.edu) access to public events, including NEU-hosted public events.
- Per-section or per-event visibility toggles on profiles.
- Richer, MySpace-level profile customization.
- Open Graph link-preview images.
- Showing connection degree or the path between users.

---

## 7. Open questions for the team

Claude Code: don't decide these. Use the default shown and flag it in your summary.

1. **Org member lists.** Can any signed-in Northeastern student see an organization's full member list? *Default: everyone sees the member count; you see names of members who are your connections; org members see the full list.*
2. **Sensitive organizations.** Unhideable membership is fine for a fraternity but riskier for a religious, cultural, LGBTQ+, or support group, and it sits in tension with the Core Strategy principle against automatic exposure of someone's social graph. Should some orgs be able to mark membership private? *Default: no change for the demo.*
3. **Non-connected profile view.** Exactly which parts of the card show? *Default: photo, name, bio, Top 3.*
4. **Background customization.** Preset colors only, or uploaded images? *Default: preset colors.*
5. **Reach mechanism** (if §5 is built). Does reach mean appearing in recommendations, or sending notifications? *Default: recommendations only.*
6. **Mutual connections.** Should search results show a mutual-connection count ("3 mutual") even though degree and path are hidden? *Default: no.*
7. **Animated logo placement.** *Default: landing/sign-in page only.*

---

## 8. QA checklist before calling it done

- [ ] Every acceptance criterion above passes at phone width (~390px) and on desktop.
- [ ] No "Viewing as" control, match percentage, on-campus toggle or label, or screenshot logo remains.
- [ ] All visibility decisions go through the module from §4.1. Search the code for any screen that shows network, orgs, or events without using it.
- [ ] A signed-out user can't reach any page except landing and invite pages.
- [ ] No console errors, and reduced-motion is respected.
- [ ] Your final summary lists every `SPEC-ASSUMPTION`, everything you skipped, and anything in the code that contradicted this spec.
