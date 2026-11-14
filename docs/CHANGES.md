# Changes

This is a running log of changes made to Collectivize, newest first. Each entry says what changed, why, and any decisions made along the way.

---

## 2026-11-13 — Requesting to co-host an event

**Request:** "Add the ability to request to join an event as co-host."

### How it works
- `graph.co_hosting.request(event_id, host_id, by_person_id, now)` lets a person ask for themselves, or an org admin ask for their org. The request stays pending, and the event doesn't appear on the requester's calendar until it's approved.
- **Approving:** a request is approved by anyone acting for a current host of the event, through the same `events.accept_co_host`. That includes co-hosts who were accepted earlier. Invitations still need the invited party to approve.
- **Declining or withdrawing:** the same `events.remove_co_host` covers both. Any current host can decline, and the requester can withdraw. A declined request can be sent again.
- **What's waiting:** `co_hosting.pending(event_id, by)` shows hosts all unanswered invitations and requests on an event. `co_hosting.pending_for(host_id, by)` shows a person or org what it's been invited to or has asked to join. Each side sees only its own.

### Decisions
- **Recording the direction:** `Hosts` gained `origin: HostOrigin` (`INVITED` or `REQUESTED`), which decides who must approve. Older stored links default to `INVITED`, which matches how they were created.
- **Hidden events:** a request about an event the requester can't see fails exactly like a missing event. That covers drafts, members-only events of other orgs and bad ids. It means requests can't be used to probe for hidden events.
- **Event status:** only published events that haven't ended take requests. Cancelled, ended and archived events don't.
- **Separate service:** request lives in a new `CoHostingService` (`graph/cohosting.py`) because it needs the privacy check, and the privacy service already depends on the event service.

### Seed and tests
- **Seed:** Emery has asked to co-host "Robot demo day", and the request is left pending for Casey. The test database passes the integrity check.
- **Tests:** added `tests/backend/test_cohost_requests.py` (24 runs). In total, 363 pass and the 7 known-bug xfails are unchanged. Ruff is clean.

### Not done
- Nobody is notified when a request arrives or is answered. There's no notification system yet.
- The integrity audit can't tell which host approved a request. It only sees that the link was accepted.

---

## 2026-11-13 — People can host, combined events, host calendars, public member lists

**Request:** "The stack can be put off until the frontend is actually made. The people who can post/host events are both individual people and organizations. Add a function to allow more than one person and organization to throw the same event (if it is a combined event, the event must be requested by one party to another and the other accepts). The event should be shown on both organizations' calendars and under their events [public/private]. Member list should be public (for now, but make it easy to change who can see people for any future decision changes)."

### Decisions
- **Stack:** postponed until the frontend exists. No API work has been done.
- **Hosts:** both people and orgs can host. This overrides `user_Orgs.md`, which says HOSTS is Org → Event, and follows the hackathon plan. The spec file itself hasn't been edited.
- **Member lists:** public, controlled by one policy setting. The spec says only the person can make their membership public, so anyone who set their own membership or profile to private stays off the list. One flag (`hide_private_memberships=False`) turns that off.

### Added
- **People as hosts.** A HOSTS edge can start at a Person or an Org. `EventService.acts_for(person, host)` holds the one rule: a person acts for themselves, and for orgs they are an admin of. Creating, editing, inviting, accepting and removing all go through it. Nobody can create an event in someone else's name.
- **Combined events.** `invite_co_host(event, host, by, now)` lets any current host invite a person or an org. Only the invited person, or an admin of the invited org, can `accept_co_host`. The invited party can decline, and either the co-host or the primary host can later remove it with `remove_co_host`. There's no limit on co-hosts, and they can be any mix of people and orgs. New queries: `host_ids` (all accepted hosts) and `host_node`.
- **Host calendars** (`graph.calendar.host_events(host_id, viewer_id, starts_after=, starts_before=)`) return a `HostEvents` with `public` and `private` lists, sorted by start time.
  - **Which events appear:** an event shows on every host that accepted it. Pending invites don't appear, and an event disappears from a co-host's calendar when that co-host leaves.
  - **Private list:** members-only, connections-only or private events, plus drafts. Drafts are shown only to the hosts.
  - **Viewer filtering:** the viewer only sees events they're allowed to see. If the viewer can't see the host, they can't see its calendar either.
- **Member lists** (`graph.roster.members(org_id, viewer_id)`) list confirmed, current members only, ordered by role and then name.
  - `RosterPolicy(audience=...)` decides who can see the list: PUBLIC (the default), NETWORK, MEMBERS or PRIVATE (admins only). To change it, edit `DEFAULT_ROSTER_POLICY` in `graph/roster.py`, or pass `CollectivizeGraph(roster_policy=...)`.
  - A member list is never more visible than the org itself.
- **Discovery:** handles events hosted by a person. They get no trust bonus, show the reason "Hosted by a person", and have `host_freshness=None`.
- **Privacy:** people who host an event can always see it. A connections-only event is visible to the host's connections.
- **Erasing a host:** when a person who was primary host is erased, each of their events passes to the earliest accepted co-host. Events with no other host are deleted along with their edges.
- **Seed:** added "Robotics study jam", hosted by Drew with Robotics Club as co-host (Casey accepted). It appears on both calendars. The test database now has 9 events and passes the integrity check.
- **Tests:** added `tests/backend/test_hosting_and_rosters.py` (41 runs). In total, 339 pass and the 7 known-bug xfails are unchanged. Ruff is clean.

### Changed
- `EventService.create(event, host_id, by_person_id)`: `host_org_id` was renamed `host_id`, and it can now be the creator themselves.
- The existing test `test_co_host_invite_must_target_an_org` became `..._must_target_an_existing_host`, because inviting a person is now allowed.
- Removed an unused `STAFF` import in `test_events_and_privacy.py` and sorted `__all__`. These were lint fixes only.

### Not done
- ~~Requesting to join an event as co-host~~: added in the entry above.
- **Event series:** they still belong to orgs only.
- **Org freshness:** publishing updates only the primary host when it's an org. Co-host orgs aren't credited.
- **Logged-out visitors:** a person-hosted event is hidden from them, because a person's profile is visible only to logged-in users. This follows the rule that an event is never more visible than its host.
- **Event visibility:** it's set at creation time and can't be changed afterwards with `revise`.

---

## 2026-11-13 — Edge-case unit tests

**Request:** "Add more edge cases and test them."

The suite grew from 71 to 228 tests: 221 pass and 7 are marked as known bugs. No source code changed. Run the tests with `.venv/bin/python -m pytest`, or with `uv run pytest` if uv is installed.

### Added
- **`tests/backend/test_objects.py`:** validation boundaries: slug and blank-name rules, malformed URLs, self-loop edges, naive timestamps, and exact freshness and staleness cutoffs. Also the event lifecycle: publishing at the start time, publishing twice, editing frozen or ended events, and when changes are logged.
- **`tests/backend/test_membership_and_hierarchy.py`:** duplicate requests, rejoining after leaving, leaving twice, roles for unconfirmed members, and admin hand-off. For claims: pending claims grant no rights, double claims, and approving the wrong edge. For PART_OF: duplicates, one-sided acceptance, parent-side proposals, staff verification, pending ancestors, more disallowed kinds, and the cycle guard (exercised by loosening the kind table).
- **`tests/backend/test_events_and_privacy.py`:** co-host invite, accept, decline and removal permissions, re-inviting, who gets credit for a publish, tagging rules, and series permissions and defaults. Also every visibility level (NETWORK, MEMBERS, CONNECTIONS, PRIVATE) and the erasure cases.
- **`tests/backend/test_storage.py`:** config trimming and reading from the process environment, a codec round trip for each edge type, timezone offsets, re-validation on decode, store parity between the two backends, nested transactions, `:memory:` SQLite, Unicode, and the environment guard keeping data intact.

### Known bugs (marked `xfail(strict=True)`)
Each test flags itself as soon as its bug is fixed, so the marker can be removed.
1. **`SeriesService.publish_occurrences` is not atomic.** If one start time is invalid, the earlier occurrences stay published.
2. **Cancelling a never-published draft makes it public.** The draft becomes visible and discoverable, which breaks the "drafts are visible only to host admins" rule.
3. **Erasing a person with a pending claim leaves the org stuck in `CLAIM_PENDING`.** `release_if_last_admin` only counts confirmed admins, and no one is left to approve the claim.
4. **The two stores handle nested transactions differently.** With SQLite, a caught failure in an inner `transaction()` still commits; the in-memory store rolls it back.

---

## 2026-11-13 — Genuine connections: graph audit and fixes

**Request:** "Check that the logic is the graph and make sure that it is genuine connections between people and organizations."

### What the check found
- **The services were sound, but the storage layer wasn't.** Every rule (two-sided membership, admin-only confirmation, staff-approved claims) lived in the services. `save_edge` only checked that both endpoints existed, so storage accepted an org being a "member of" another org, a person "hosting" a tag, or a person "part of" an org. A probe script saved all of these without error.
- **Confirmations left no trail.** A confirmed membership didn't record who confirmed it. A hand-written `MemberOf(role=ADMIN, org_confirmed=True)` made someone an admin, and nothing could tell it apart from a real one.
- **People couldn't connect at all.** The `Connected` edge existed, but no code path created it. The only way to make one was to write it directly with both sides already "accepted".
- An ended membership could still be confirmed.

### Added
- **Endpoint rules on every edge type** (`SOURCE_TYPES` / `TARGET_TYPES` in `objects/edges.py`), taken from `user_Orgs.md`:
  - MEMBER_OF and FOLLOWS: Person → Org
  - PART_OF and PARTNERS_WITH: Org → Org
  - HOSTS: Org → Event
  - INSTANCE_OF: Event → EventSeries
  - RSVP and ATTENDED: Person → Event
  - CONNECTED: Person → Person
  - TAGGED: Org or Event → Tag
  - INTERESTED_IN: Person → Tag

  Both stores enforce these in `save_edge` through `check_edge_endpoints`, so nothing can bypass them.
- **`MemberOf.confirmed_by`:** records the admin who confirmed the membership, or the staff member who approved the claim. A confirmed membership without a confirmer can't be constructed, and nobody can confirm their own membership.
- **`ConnectionService`** (`graph.connections`): `request` and `accept` (only the recipient can accept), `remove` (either person can decline or disconnect), `between`, `are_connected` and `connections_of`. There is one edge per pair, and declining or removing hard-deletes it, so no record remains that two people were linked. `PrivacyService.are_connected` now delegates here.
- **`IntegrityService`** (`graph.integrity.audit()`) is read-only. It reports:
  - edges between the wrong kinds of node or pointing at missing nodes;
  - memberships confirmed by someone who was never an admin of that org and that staff didn't approve;
  - claimed orgs with no admin, and admins of unclaimed orgs;
  - connections that neither person requested;
  - PART_OF links between disallowed org kinds;
  - listed events without exactly one primary host.
- **`scripts/check_graph.py`:** runs the audit against the configured database. It exits with 1 if it finds problems.
- **Tests:** `tests/backend/test_connections_and_integrity.py` adds 48 test runs (counting both stores). In total, 148 tests pass and ruff is clean.

### Changed
- `MembershipService.confirm` records the admin and rejects ended memberships. `approve_claim` records the staff id.
- `data/test.db` was re-seeded. The old file failed the audit because its confirmations predate `confirmed_by`. The fresh seed passes, and every confirmed membership traces back to staff or a chapter admin.

### Not done
- The audit can't tell who accepted a co-host invite or a PART_OF link. It only checks the end state. Recording `accepted_by` on those edges would close that gap.
- Per-person visibility of a connection ("visible only if both people allow it") still uses one visibility field per edge.
- There's no way to reject a pending org claim, so a bogus claim leaves the org stuck in `claim_pending`.
- There's no "people closest to people the org knows" query from the README yet. These fixes make that query trustworthy, since it will only walk consented edges.
- A database with old records can't be loaded until it's re-seeded or migrated. There are no migrations yet.

---

## 2026-11-13 — Event discovery query

**Request:** "Build the discovery query."

This is the first user-facing feature. `graph.discovery.search(query, now)` returns upcoming events the viewer is allowed to see. Results can be filtered and paged, and each one is ranked with plain-language reasons.

### How to use it

```python
from backend.database import open_graph
from backend.graph import DiscoveryQuery

with open_graph() as graph:
    page = graph.discovery.search(
        DiscoveryQuery(viewer_id=person_id, text="robot",
                       preferred_tag_ids=(robotics_tag_id,), limit=20),
        now,
    )
    for item in page.items:
        item.event, item.host, item.score, item.reasons, item.is_cancelled
```

### Which events can appear
- Published events that haven't ended yet, including ones already under way.
- Cancelled events, until their original end time. They're flagged with the reason "Cancelled: this event is not happening" (always listed first) and always ranked last, so people who planned to go, even late, find out.
- Never drafts, archived events or ended events.
- Only events the viewer is allowed to see. **The host org must be visible to the viewer too,** so an event is never more exposed than the org running it.

### Filters (`DiscoveryQuery`)

| Field | Effect |
|-------|--------|
| `viewer_id` | Who is looking. `None` means a logged-out visitor. |
| `text` | Case-insensitive match on title, description and location. Max 200 characters. |
| `tag_ids` | The event must match at least one of these tags. The event's own tags and its host orgs' tags both count. |
| `preferred_tag_ids` | The viewer's interests. These **boost** matching events but never filter. |
| `org_id`, `include_related` | Events hosted by an org. By default this includes orgs below it in the hierarchy (e.g. "all IFC chapters"). |
| `starts_after`, `starts_before` | Only events starting within this time range. |
| `include_cancelled` | Default `True`. |
| `limit`, `offset` | Paging. `limit` is 1–100 (default 20). The result reports `total` and `has_more`. |

Invalid queries raise `DomainRuleError`.

### Ranking

Higher scores come first. Ties are broken by start time, then by id. Every factor that moves an event's score adds a reason:

| Factor | Score | Reason shown |
|--------|-------|--------------|
| Host claimed / claim pending / unclaimed | +3 / +1 / 0 | "Hosted by a verified org" / "Host's claim is being verified" / "Unclaimed listing: not managed by the org itself" |
| Host quiet / stale | −1 / −3 | "Host hasn't posted recently" / "Host info may be out of date" |
| Event details verified / disputed | +1 / −3 | "Event details verified" / "Event details are disputed" |
| Each matching interest tag | +2.5 | "Matches your interest: Robotics" |
| How soon it starts | up to +2, fading over 30 days | "Starts within 48 hours" (shown when it starts within 48 hours) |
| Cancelled | −100 | "Cancelled: this event is not happening" (always the first reason) |

All weights are named constants at the top of `discovery.py`, so they're easy to adjust.

### Added
- **`src/backend/graph/discovery.py`:** `DiscoveryQuery`, `DiscoveryItem`, `DiscoveryPage` and `DiscoveryService`.
- **`tests/backend/test_discovery.py`:** 15 test cases, each run against both stores, covering what can appear, visibility, every filter, ranking and reasons, paging, and input checks.

### Changed
- **`src/backend/graph/store.py`, `sqlite_store.py`:** new `nodes(cls)` method lists every node of a type. SQLite gained a `nodes_by_type` index.
- **`src/backend/graph/hierarchy.py`:** new `descendant_ids(org_id)` finds every org below an org through confirmed `PART_OF` links.
- **`src/backend/graph/__init__.py`:** `CollectivizeGraph` now has `.discovery`.

### Checked against the seeded test DB
- A logged-out visitor sees 7 of the 8 events. The draft is hidden, and the cancelled mixer is last.
- When Drew (demo) has robotics as an interest, "Robot demo day" moves to #1 with the reason "Matches your interest: Robotics".

### Decisions
- **Inherited tags:** tags on a host org also apply to its events, so tagging the Robotics Club makes its events show up under robotics.
- **Interests are passed in for now:** `preferred_tag_ids` comes with the query because people's interest edges aren't built yet. Once they are, they can feed this directly.
- **Scan everything, then filter:** each search loads every event and filters in Python. That's fine at pilot scale but should move into SQL queries as data grows.

### Not done yet
- No interest edges, embeddings, follows or "related to orgs you follow" suggestions.
- No API endpoint or frontend to call this.

---

## 2026-11-13 — Separate test and production databases

**Request:** "Make it so there is a test database that stores mockup information and then a production database that stores real data."

There are now two SQLite databases. The test database holds fictional mock data; the production database holds real data. The `COLLECTIVIZE_ENV` variable picks which one is used. Several guards keep mock data out of production and stop either database from being opened as the other.

### How to use it

| Task | Command |
|------|---------|
| Create the test DB with mock data | `uv run python scripts/seed_test_db.py` |
| Wipe and re-seed the test DB | `uv run python scripts/seed_test_db.py --reset` |
| Use the test DB in code (the default) | `with open_graph() as graph: ...` |
| Use the production DB in code | Run with `COLLECTIVIZE_ENV=production` |
| Use a different database file | Set `COLLECTIVIZE_DB_PATH=/path/to/file.db` |

The default files are `data/test.db` and `data/production.db`.

### Added
- **`src/backend/config.py`:** the `Environment` setting (`test` or `production`) and `load_config()`, which reads `COLLECTIVIZE_ENV` and `COLLECTIVIZE_DB_PATH`. It defaults to **test**, so nothing writes real data by accident. Unknown values are rejected.
- **`src/backend/graph/sqlite_store.py`:** `SqliteGraphStore`, a persistent store with the same interface as the in-memory store. It has three tables: `meta`, `nodes` and `edges` (with indexes on source and target). Records are stored as JSON. `transaction()` makes a block of writes all-or-nothing.
- **`src/backend/graph/codec.py`:** converts records to and from JSON exactly (enums, datetimes, durations, nested objects, change-log values). Loaded data goes back through each class's validation. Unknown types or fields are rejected.
- **`src/backend/database.py`:** `open_graph()`, which opens the configured database and closes it when you're done.
- **`src/backend/seed.py`:** `seed_mock_data()` creates a fictional campus: 6 orgs, 5 people, 5 tags and 8 events. That includes a weekly meeting series, a co-hosted fundraiser, a cancelled event, a draft, an unclaimed org and a pending membership request. All writes go through the normal services, so the mock data follows every graph rule.
- **`scripts/seed_test_db.py`:** a command-line script for seeding, with a `--reset` option.
- **Tests:**
  - `tests/backend/test_storage.py`: config, the JSON conversion, the SQLite store, and transactions on both stores.
  - `tests/backend/test_seed.py`: mock data is clearly fake and follows the rules; seeding production is refused.
- **`data/test.db`:** created and seeded.

### Changed
- **`src/backend/graph/store.py`:**
  - `GraphStore` is now an interface that both stores implement.
  - The in-memory store is renamed `InMemoryGraphStore`.
  - It gained `transaction()`, which restores a snapshot if the block fails, and `close()`.
- **`src/backend/graph/__init__.py`:** `CollectivizeGraph()` still uses the in-memory store by default. Pass a `SqliteGraphStore` to persist data.
- **`src/backend/graph/privacy.py`:** `erase_person` now runs in a transaction, so an account deletion can never be left half-done.
- **`tests/backend/conftest.py`:** every service test now runs against **both** stores. There are now 71 tests, up from 33.
- **`.gitignore`:** added `data/`, so database files, and especially real production data, are never committed.

### Safeguards
1. **Seeding production is refused,** both in `seed_mock_data()` and in the seed script.
2. **Each database file is stamped with its environment** the first time it's opened. Opening a production file as test, or the reverse, raises `EnvironmentMismatchError`.
3. **`--reset` only deletes a file stamped as a test database.** I confirmed it refuses when `COLLECTIVIZE_DB_PATH` points at a production file.
4. **Mock records are obviously fake:** org names start with `[Demo]`, people are labelled "(demo …)", and events have `is_demo=True`, which can never be removed.

### Decisions
- **SQLite** (built into Python, no new dependencies) is enough for the pilot. The services only use the `GraphStore` interface, so a Postgres store could be dropped in later.
- **JSON records with indexed edge columns,** rather than one table per type, so adding fields doesn't need a database migration yet.

### Not done yet
- No database migrations or schema versioning.
- No backups for production.
- No dedicated staging environment.

---

## 2026-11-13 — Graph architecture from `user_Orgs.md`

**Request:** "Create the architecture for user_Orgs.md."

The graph spec in `user_Orgs.md` is now an object-oriented Python backend, as `rules.md` requires. Every node and edge type from the spec exists. The four cross-cutting rules (provenance, visibility, recommendation consent, lifecycle) and all the MVP rules are enforced in code. The "Later" types are defined so the schema has a place for them, but their rules aren't enforced yet.

### Added

**Domain objects: `src/backend/objects/`**

All of these are immutable (frozen dataclasses). Changing one returns a new copy.

| File | Contents |
|------|----------|
| `base.py` | `GraphRecord`, the base for every node and edge: id, `Provenance`, `Visibility`, `RecordState`. Also `DomainRuleError` and `most_restrictive()`. |
| `person.py` | `Person` and `VerificationLevel` (records *which* level of identity verification was reached, not how). |
| `organization.py` | `Organization` with `OrgKind`, `ClaimState` (unclaimed, pending, claimed), external links (http/https only), and derived `freshness()` (active, quiet, stale). |
| `event.py` | `Event` with its lifecycle (draft, published, cancelled, ended, archived) and a change log for time and location edits. The demo flag can't be cleared. `EventSeries` holds defaults that its events inherit. |
| `taxonomy.py` | `Tag` (curated, kebab-case slug) and `Venue` (Later). |
| `edges.py` | All edges: `MemberOf`, `PartOf`, `PartnersWith`, `Hosts`, `InstanceOf`, `Rsvp`, `Attended`, `Follows`, `Connected`, `Tagged`, `InterestedIn`. Edges a person owns carry `RecommendationUse`, which defaults to `OWN_ONLY`. |

**Graph services: `src/backend/graph/`**

| File | Rules it enforces |
|------|-------------------|
| `store.py` | In-memory storage for nodes and edges. Hard delete is reserved for account erasure. |
| `membership.py` | The person claims a membership and the org confirms it. Only admins grant roles. Only the person controls their membership's visibility. An org must always have at least one admin. Also handles the claim flow: the org goes from unclaimed to claim pending, and becomes claimed once staff approve. |
| `hierarchy.py` | A `PART_OF` link needs both orgs to accept or staff verification. Only certain org types can link (see `ALLOWED_PART_OF`). An org can have several parents but can never become its own ancestor. |
| `events.py` | Exactly one primary host. Co-hosts count only after they accept and can remove themselves. Only admins of a host org can edit an event. Publishing updates the host org's activity. Admins tag their own orgs and events. |
| `series.py` | An event belongs to at most one series. `publish_occurrences` publishes a semester in one action. Editing the series updates only future events that still use the old default. |
| `privacy.py` | Effective visibility is the most restrictive of the edge and both endpoints. `can_view` checks who is looking. Draft events are visible only to host admins. `erase_person` truly deletes the person's edges. |
| `__init__.py` | `CollectivizeGraph`, which wires the store and all services together. |

**Tests: `tests/backend/`**
- 33 pytest tests covering the objects, membership, hierarchy, events, series and privacy.
- Run them with `uv run --group dev pytest`.

### Changed
- `src/backend/objects/organization.py`: replaced the empty stub with the full `Organization` model.
- `pyproject.toml`: added a `dev` dependency group with pytest, and pytest config (`pythonpath = ["src"]`, `testpaths = ["tests"]`).

### Removed
- `src/backend/objects/user.py`: the empty `User` stub. `Person` in `person.py` replaces it, to match the spec's terms.

### Generated
- `uv.lock` and `.venv/`, created by uv. `.venv` is already gitignored.

### Decisions (the spec doesn't specify these)
- **Visibility order:** public < network < members < connections < private.
- **Freshness:** quiet after 30 days and stale after 90. Measured from the more recent of the last profile confirmation and the last published event.
- **Allowed `PART_OF` links:** chapter → national; chapter or club → council; anything except an institution → institution.
- **Staff actions:** `staff_id` is trusted as given. A future auth layer must check it.
- **Series edits:** an event that was edited on its own keeps its edit.
- **Erasing the last admin's account:** the org goes back to unclaimed instead of blocking the deletion.
- **Hierarchy cycle check:** pending links count too, so accepting one later can never create a cycle.

### Bug fixed
- `HierarchyService.accept` let any user approve a pending link. It checked the link's existing approvals instead of whether the person approving is an admin. A test caught it, and it's fixed.

### Not done yet
- Rules for the "Later" edges (RSVP freezing, small-count suppression, attendance timing) aren't enforced.
- There's no API layer, persistent database or authentication yet.
