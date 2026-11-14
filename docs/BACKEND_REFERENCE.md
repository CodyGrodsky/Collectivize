# Backend reference

Every public class and function in the Collectivize backend (`src/backend/`): what it does and where it lives. Each entry links to its line in the source, for example [L35](../src/backend/graph/membership.py#L35).

Line numbers were current on 2026-11-14 (updated for the demo server and the generated demo world). If one has moved, search the file for the function name.

## Start here

All backend work goes through one object, **`CollectivizeGraph`** ([`src/backend/graph/__init__.py`](../src/backend/graph/__init__.py)). It holds the store and every service:

```python
from backend.database import open_graph

with open_graph() as graph:              # test database unless COLLECTIVIZE_ENV=production
    page = graph.discovery.search(DiscoveryQuery(viewer_id=person_id), now)
```

In tests, use `CollectivizeGraph()` for an in-memory graph, or pass a store: `CollectivizeGraph(SqliteGraphStore(path, Environment.TEST))`.

**Constructor options:**

| Option | What it changes |
|---|---|
| `store` | Where data lives. The default is in-memory; production code uses `SqliteGraphStore` through `open_graph()`. |
| `roster_policy` | Who may see member lists (`RosterPolicy`; public by default). |
| `invite_email_domains` | Email domains allowed when joining (default `("northeastern.edu",)`). |
| `ranking_weights` | The weights of the "would they go?" model (`RankingWeights`). |
| `search_interpreter` | What scores AI search requests. The default is Claude; tests pass a fake. |
| `agent_client` | The Anthropic client the search agent uses. By default one is made on first use; tests pass a fake. |

**The services on `graph`:**

| Attribute | Service | File | Job |
|---|---|---|---|
| `graph.store` | `GraphStore` | `graph/store.py`, `graph/sqlite_store.py` | Save and load nodes and edges |
| `graph.membership` | `MembershipService` | `graph/membership.py` | Joining orgs, admin roles, claiming an org |
| `graph.roster` | `RosterService` | `graph/roster.py` | Member lists and who can see them |
| `graph.hierarchy` | `HierarchyService` | `graph/hierarchy.py` | Orgs inside orgs (chapter → national, council, school) |
| `graph.connections` | `ConnectionService` | `graph/connections.py` | Person-to-person connections |
| `graph.invites` | `InviteService` | `graph/invites.py` | Joining by invite |
| `graph.network` | `NetworkService` | `graph/network.py` | Network graph: neighborhoods, paths, in common |
| `graph.events` | `EventService` | `graph/events.py` | Event lifecycle, hosts, co-host invites, tags |
| `graph.co_hosting` | `CoHostingService` | `graph/cohosting.py` | Asking to co-host someone else's event |
| `graph.series` | `SeriesService` | `graph/series.py` | Recurring events |
| `graph.calendar` | `CalendarService` | `graph/calendar.py` | A host's calendar (public and private) |
| `graph.rsvps` | `RsvpService` | `graph/rsvps.py` | RSVPs, friends going, counts |
| `graph.discovery` | `DiscoveryService` | `graph/discovery.py` | The ranked, filtered event list |
| `graph.preferences` | `PreferenceService` | `graph/preferences.py` | Interests, learned tastes, travel distance |
| `graph.activity` | `ActivityService` | `graph/activity.py` | Most-interacted orgs and recent events |
| `graph.ai_search` | `AiSearchService` | `graph/ai_search.py` | Search in your own words (one AI call) |
| `graph.search_agent` | `SearchAgentService` | `graph/search_agent.py` | Search agent (Claude with tools) |
| `graph.privacy` | `PrivacyService` | `graph/privacy.py` | Who may see what; account erasure |
| `graph.integrity` | `IntegrityService` | `graph/integrity.py` | Audit for relationships that aren't genuine |
| `graph.data_access` | `DataAccessService` | `graph/data_access.py` | A person's own data; the ML dataset |

## Conventions

- **The person acting is always passed in** (`by_person_id`, `viewer_id`, or `person_id` for their own actions). Services check what that person may do. `None` as a viewer means a logged-out visitor. There's no login yet, so the future API layer must pass the logged-in person's id.
- **Time is passed in** (`now`), never read from the clock inside services, so behaviour is testable. All datetimes must be time-zone aware.
- **Records are immutable.** Changes return a new copy, which a service then saves.
- **Errors:**
  - `DomainRuleError` (`objects/base.py`): a rule was broken (bad input, wrong state).
  - `PermissionDeniedError` (`graph/errors.py`): this person isn't allowed.
  - `NotFoundError` (`graph/errors.py`): it doesn't exist, **or the viewer may not know it exists**. Hidden things look missing on purpose.
- **Privacy is enforced in the backend.** Lists and searches only return what the viewer may see; callers should never filter by visibility themselves.
- **AI calls** happen only in `ai_search`, `search_agent` and `claude_interpreter`. Credentials come from `backend/credentials.py`, and tests can't reach the API.

## Scripts (`scripts/`)

| Script | What it does |
|---|---|
| [`seed_test_db.py`](../scripts/seed_test_db.py) | Fill the test database with the fictional demo world (`--reset` to start over; refuses production). |
| [`check_graph.py`](../scripts/check_graph.py) | Run the integrity audit on the configured database. |
| [`ai_search.py`](../scripts/ai_search.py) | Try a search from the terminal: the agent by default, or one AI call with `--single` (costs a little). |
| [`serve.py`](../scripts/serve.py) | Run the local demo server: the frontend pages plus the JSON API behind Discover (`--no-ai` for keyword search only). |
| [`export_ml_dataset.py`](../scripts/export_ml_dataset.py) | Export the consent-gated, pseudonymized dataset to `data/exports/` (needs `COLLECTIVIZE_EXPORT_KEY`). |

## Contents

1. [Membership, orgs and member lists](#membership-orgs-and-member-lists)
2. [People and the network](#people-and-the-network)
3. [Events](#events)
4. [Discover and ranking](#discover-and-ranking)
5. [AI search](#ai-search)
6. [Privacy, integrity and data access](#privacy-integrity-and-data-access)
7. [Storage](#storage)
8. [Domain objects (nodes and edges)](#domain-objects-nodes-and-edges)
9. [Setup, configuration, demo server and demo data](#setup-configuration-demo-server-and-demo-data)

In the lists below, **bold names** are classes, the indented items are their methods, and *(property)* marks a value read without calling it.

## Membership, orgs and member lists

Who belongs to which org, admin rights, claiming an org, how orgs nest, and who can see member lists.

### `src/backend/graph/membership.py`

MEMBER_OF rules: two-sided membership, admin role, and org claiming.

- **`MembershipService`**: Memberships: requesting, confirming, roles, leaving, and claiming an org. Available as `graph.membership`. [L29](../src/backend/graph/membership.py#L29)
  - `current(person_id: str, org_id: str) -> MemberOf | None`: A person's current membership edge in an org, if any. [L35](../src/backend/graph/membership.py#L35)
  - `is_member(person_id: str, org_id: str) -> bool`: Whether the person is a confirmed, current member. [L43](../src/backend/graph/membership.py#L43)
  - `is_admin(person_id: str, org_id: str) -> bool`: Whether the person is a confirmed, current admin. [L47](../src/backend/graph/membership.py#L47)
  - `require_admin(person_id: str, org_id: str) -> None`: Raise `PermissionDeniedError` unless the person is an admin of the org. [L51](../src/backend/graph/membership.py#L51)
  - `admins(org_id: str) -> list[MemberOf]`: The org's current admin memberships. [L55](../src/backend/graph/membership.py#L55)
  - `request(person_id: str, org_id: str, now: datetime) -> MemberOf`: The person claims membership; the org must still confirm it. [L60](../src/backend/graph/membership.py#L60)
  - `set_visibility(edge_id: str, by_person_id: str, visibility: Visibility) -> MemberOf`: Only the person decides how visible their membership is. [L76](../src/backend/graph/membership.py#L76)
  - `leave(edge_id: str, by_person_id: str, now: datetime, *, keep_visible: bool = True, release_org: bool = False) -> MemberOf`: End a membership. The last admin must hand off the role first or pass `release_org=True` to return the org to unclaimed. [L83](../src/backend/graph/membership.py#L83)
  - `confirm(edge_id: str, by_person_id: str) -> MemberOf`: Confirm a membership request (org admins only); records who confirmed it. [L112](../src/backend/graph/membership.py#L112)
  - `set_role(edge_id: str, role: MemberRole, by_person_id: str) -> MemberOf`: Only the org (an admin) can grant or change roles. [L119](../src/backend/graph/membership.py#L119)
  - `claim_org(person_id: str, org_id: str, now: datetime) -> MemberOf`: Request admin access to an unclaimed listing. [L137](../src/backend/graph/membership.py#L137)
  - `approve_claim(edge_id: str, staff_id: str, now: datetime) -> MemberOf`: Staff approve a pending org claim: the claimant becomes admin and the org becomes claimed. [L150](../src/backend/graph/membership.py#L150)
  - `release_if_last_admin(edge: MemberOf) -> None`: Used by account erasure so an org is never left adminless, or stuck waiting on a claim from someone who no longer exists. [L181](../src/backend/graph/membership.py#L181)

### `src/backend/graph/hierarchy.py`

PART_OF rules: mutual acceptance (or staff verification), allowed kinds per org type, multiple parents, and no cycles.

- `check_part_of_kinds(child: Organization, parent: Organization, kind: PartOfKind) -> None`: Raise unless this kind of PART_OF link is allowed between these org kinds. [L38](../src/backend/graph/hierarchy.py#L38)
- **`HierarchyService`**: Orgs inside orgs (PART_OF): proposing, accepting, staff links, and walking up or down. `graph.hierarchy`. [L46](../src/backend/graph/hierarchy.py#L46)
  - `parents(org_id: str) -> list[Organization]`: Orgs this org is established as part of. [L53](../src/backend/graph/hierarchy.py#L53)
  - `children(org_id: str) -> list[Organization]`: Orgs established as part of this org. [L60](../src/backend/graph/hierarchy.py#L60)
  - `descendant_ids(org_id: str) -> set[str]`: Established descendants, e.g. every chapter under a council. [L67](../src/backend/graph/hierarchy.py#L67)
  - `ancestor_ids(org_id: str, *, include_pending: bool = False) -> set[str]`: Every org above this one (optionally counting pending links). [L79](../src/backend/graph/hierarchy.py#L79)
  - `propose(child_id: str, parent_id: str, kind: PartOfKind, by_person_id: str, now: datetime) -> PartOf`: An admin of either side proposes; that side is auto-accepted. [L94](../src/backend/graph/hierarchy.py#L94)
  - `accept(edge_id: str, by_person_id: str) -> PartOf`: Accept a proposed PART_OF link as an admin of the other side. [L115](../src/backend/graph/hierarchy.py#L115)
  - `staff_link(child_id: str, parent_id: str, kind: PartOfKind, staff_id: str, now: datetime) -> PartOf`: Staff verified the relation against a public source. [L128](../src/backend/graph/hierarchy.py#L128)
  - `staff_verify(edge_id: str, now: datetime) -> PartOf`: Staff-verify an existing PART_OF link. [L142](../src/backend/graph/hierarchy.py#L142)

### `src/backend/graph/roster.py`

Org member lists, governed by one policy object.

- **`RosterPolicy`**: Who may see an org's member list. [L43](../src/backend/graph/roster.py#L43)
- **`RosterEntry`**: One member on a member list: the person, their role, and since when. [L62](../src/backend/graph/roster.py#L62)
- **`RosterService`**: Member lists, governed by `RosterPolicy`. `graph.roster`. [L68](../src/backend/graph/roster.py#L68)
  - `can_see(viewer_id: str | None, org_id: str) -> bool`: Whether a viewer may see an org's member list (roster policy, capped by the org's visibility). [L79](../src/backend/graph/roster.py#L79)
  - `shows(viewer_id: str | None, edge: MemberOf) -> bool`: Whether this one membership appears on the list for this viewer. [L92](../src/backend/graph/roster.py#L92)
  - `members(org_id: str, viewer_id: str | None) -> tuple[RosterEntry, ...]`: An org's confirmed members visible to the viewer; raises if they can't see the list. [L98](../src/backend/graph/roster.py#L98)

## People and the network

Person-to-person connections, joining by invite, and the network graph (neighborhoods, paths, in common).

### `src/backend/graph/connections.py`

CONNECTED rules: a person-to-person connection exists only when both people agree, and either can remove it silently.

- **`ConnectionService`**: Person-to-person connections (both must agree). `graph.connections`. [L29](../src/backend/graph/connections.py#L29)
  - `between(a: str, b: str) -> Connected | None`: The edge (pending or established) linking two people, if any. [L35](../src/backend/graph/connections.py#L35)
  - `are_connected(a: str, b: str) -> bool`: Whether two people have an accepted connection. [L43](../src/backend/graph/connections.py#L43)
  - `connections_of(person_id: str) -> list[str]`: Ids of people this person is mutually connected with. [L47](../src/backend/graph/connections.py#L47)
  - `request(from_person_id: str, to_person_id: str, now: datetime) -> Connected`: Send a connection request; pending until the other person accepts. [L60](../src/backend/graph/connections.py#L60)
  - `connect_via_invite(inviter_id: str, invitee_id: str, now: datetime) -> Connected`: The inviter agreed by sending the invite and the invitee by redeeming it, so the connection starts out accepted by both. Only InviteService calls this, after checking the invite. [L79](../src/backend/graph/connections.py#L79)
  - `accept(edge_id: str, by_person_id: str) -> Connected`: Accept a connection request; only the person who was asked can. [L99](../src/backend/graph/connections.py#L99)
  - `set_hide_connections(person_id: str, by_person_id: str, hidden: bool) -> Person`: Hide or show your connection list. Only you can change it. [L105](../src/backend/graph/connections.py#L105)
  - `remove(edge_id: str, by_person_id: str) -> None`: Decline a request or end a connection. Either person may do it. [L114](../src/backend/graph/connections.py#L114)

### `src/backend/graph/invites.py`

Joining Collectivize by invite.

- **`IssuedInvite`**: `code` is shown to the inviter once; it can't be recovered later. [L52](../src/backend/graph/invites.py#L52)
- **`Redemption`**: Result of joining by invite: the new person, the inviter, and their connection. [L60](../src/backend/graph/invites.py#L60)
- **`InviteService`**: Creating, revoking and redeeming invites. `graph.invites`. [L66](../src/backend/graph/invites.py#L66)
  - `create(inviter_id: str, now: datetime, *, code: str | None = None) -> IssuedInvite`: `code` may be fixed only for demo data; real invites use a random one. [L79](../src/backend/graph/invites.py#L79)
  - `revoke(invite_id: str, by_person_id: str) -> Invite`: Cancel one of your unused invites. [L101](../src/backend/graph/invites.py#L101)
  - `invites_of(person_id: str, by_person_id: str) -> list[Invite]`: Your own invites (never their codes). [L107](../src/backend/graph/invites.py#L107)
  - `redeem(code: str, email: str, display_name: str, now: datetime) -> Redemption`: Join with an invite code and university email: creates the person, connected to the inviter. [L114](../src/backend/graph/invites.py#L114)
  - `validate_email(email: str) -> str`: Normalized address, or DomainRuleError if it isn't an allowed, unused university email. [L139](../src/backend/graph/invites.py#L139)
- `fingerprint(code: str) -> str`: The stored id for a code: its SHA-256 hex digest. [L177](../src/backend/graph/invites.py#L177)

### `src/backend/graph/network.py`

The network view: people and orgs as nodes, linked by accepted connections (person <-> person) and confirmed memberships (person -> org).

- **`NodeKind`** (enum): Whether a network node is a person or an org. [L48](../src/backend/graph/network.py#L48)
- **`LinkKind`** (enum): Whether a network link is a connection or a membership. [L53](../src/backend/graph/network.py#L53)
- **`NetworkNode`**: A dot in the network view: id, kind, label. [L59](../src/backend/graph/network.py#L59)
- **`NetworkLink`**: A line in the network view: two node ids and the kind of link. [L66](../src/backend/graph/network.py#L66)
- **`NetworkPath`**: A route through the network, as nodes and links. [L73](../src/backend/graph/network.py#L73)
  - `hops` (property): Number of links in the path. [L78](../src/backend/graph/network.py#L78)
- **`NetworkView`**: What the network screen draws: nodes, links, whether it was cut short, and (in focus view) the path. [L83](../src/backend/graph/network.py#L83)
- **`InCommon`**: Mutual friends and shared orgs between two people, with a ready-made summary. [L92](../src/backend/graph/network.py#L92)
  - `summary` (property): E.g. "2 mutual friends · Surf Club". [L97](../src/backend/graph/network.py#L97)
- **`NetworkService`**: The network graph for one viewer: neighborhoods, paths, focus, in common. `graph.network`. [L107](../src/backend/graph/network.py#L107)
  - `neighborhood(viewer_id: str | None, center_id: str | None = None, *, depth: int = 1, max_nodes: int = DEFAULT_MAX_NODES) -> NetworkView`: The center (default: you) plus everything within `depth` links. [L117](../src/backend/graph/network.py#L117)
  - `find_path(viewer_id: str | None, target_id: str, *, max_hops: int = MAX_PATH_HOPS) -> NetworkPath | None`: Shortest route from you to someone or some org, or None when there's no close connection (more than `max_hops` links away). [L153](../src/backend/graph/network.py#L153)
  - `focus(viewer_id: str | None, focus_id: str) -> NetworkView`: What the graph shows when you click a node: the node, its direct links, and the path from you to it (if there's a close one). [L163](../src/backend/graph/network.py#L163)
  - `in_common(viewer_id: str | None, other_id: str) -> InCommon`: Mutual friends and shared orgs, as the viewer is allowed to see them. [L180](../src/backend/graph/network.py#L180)

## Events

Creating, publishing and editing events; people and orgs as hosts; co-hosting by invite or request; series; calendars; RSVPs.

### `src/backend/graph/events.py`

HOSTS and TAGGED rules plus the event lifecycle.

- **`EventService`**: Event lifecycle, hosts (people or orgs), co-host invites, and tagging. `graph.events`. [L38](../src/backend/graph/events.py#L38)
  - `host_edges(event_id: str) -> list[Hosts]`: An event's accepted host links. [L45](../src/backend/graph/events.py#L45)
  - `primary_host_id(event_id: str) -> str`: Id of the event's primary host (error unless there is exactly one). [L48](../src/backend/graph/events.py#L48)
  - `host_ids(event_id: str) -> set[str]`: Every accepted host, people and orgs. [L54](../src/backend/graph/events.py#L54)
  - `host_org_ids(event_id: str) -> set[str]`: Ids of the accepted hosts that are orgs. [L58](../src/backend/graph/events.py#L58)
  - `acts_for(person_id: str, host_id: str) -> bool`: A person acts for themselves, and for orgs they are an admin of. [L65](../src/backend/graph/events.py#L65)
  - `can_edit(person_id: str, event_id: str) -> bool`: Whether a person may edit the event (they act for an accepted host). [L72](../src/backend/graph/events.py#L72)
  - `host_node(host_id: str) -> Person | Organization`: Load a host (person or org); `NotFoundError` for anything else. [L75](../src/backend/graph/events.py#L75)
  - `create(event: Event, host_id: str, by_person_id: str) -> Event`: `host_id` is the primary host: the creator themselves, or an org the creator is an admin of. [L83](../src/backend/graph/events.py#L83)
  - `publish(event_id: str, by_person_id: str, now: datetime) -> Event`: Publish a draft (must start in the future); updates the primary org's last-published time. [L103](../src/backend/graph/events.py#L103)
  - `revise(event_id: str, by_person_id: str, now: datetime, **changes) -> Event`: Edit an event's details; time and location changes are logged once it's published. [L111](../src/backend/graph/events.py#L111)
  - `cancel(event_id: str, by_person_id: str) -> Event`: Cancel an event (a published one stays listed as cancelled until it ends). [L117](../src/backend/graph/events.py#L117)
  - `archive(event_id: str, by_person_id: str) -> Event`: Archive an event: dropped from discovery, kept for history. [L121](../src/backend/graph/events.py#L121)
  - `invite_co_host(event_id: str, host_id: str, by_person_id: str, now: datetime) -> Hosts`: An existing host asks a person or org to co-host. Nothing changes for the invited party until it accepts. [L127](../src/backend/graph/events.py#L127)
  - `accept_co_host(edge_id: str, by_person_id: str) -> Hosts`: Approve a pending co-host link. An invitation is approved by the invited person (or an admin of the invited org); a request to join is approved by anyone acting for a current host of the event. [L144](../src/backend/graph/events.py#L144)
  - `remove_co_host(edge_id: str, by_person_id: str) -> Hosts`: Decline, withdraw, leave or remove. The co-host itself and the primary host can always do it; any current host can also decline a pending request to join. [L156](../src/backend/graph/events.py#L156)
  - `release_host(host_id: str) -> None`: Called when a host's account is erased. Events they were primary host of pass to the earliest accepted co-host, or are deleted when nobody else is hosting them. [L174](../src/backend/graph/events.py#L174)
  - `tag(subject_id: str, tag_id: str, by_person_id: str, now: datetime) -> Tagged`: Orgs and events are tagged by their admins. [L189](../src/backend/graph/events.py#L189)

### `src/backend/graph/cohosting.py`

Asking to co-host someone else's event, and seeing what's waiting.

- **`CoHostingService`**: Asking to co-host someone else's event, and listing pending invites and requests. `graph.co_hosting`. [L39](../src/backend/graph/cohosting.py#L39)
  - `request(event_id: str, host_id: str, by_person_id: str, now: datetime) -> Hosts`: `host_id` (the asker themselves, or an org they admin) asks to co-host. It counts only once a current host approves. [L47](../src/backend/graph/cohosting.py#L47)
  - `pending(event_id: str, by_person_id: str) -> list[Hosts]`: Invitations and requests on an event still awaiting an answer. Only the event's hosts can see them. [L72](../src/backend/graph/cohosting.py#L72)
  - `pending_for(host_id: str, by_person_id: str) -> list[Hosts]`: What a person or org has been invited to, or has asked to join, that is still waiting. Only that party can see it. [L79](../src/backend/graph/cohosting.py#L79)

### `src/backend/graph/series.py`

INSTANCE_OF rules and EventSeries operations.

- **`SeriesService`**: Recurring events (series) and their instances. `graph.series`. [L33](../src/backend/graph/series.py#L33)
  - `create(series: EventSeries, by_person_id: str) -> EventSeries`: Create a recurring series for an org (its admins only). [L41](../src/backend/graph/series.py#L41)
  - `instances(series_id: str) -> list[Event]`: The series' events, by start time. [L45](../src/backend/graph/series.py#L45)
  - `add_instance(event_id: str, series_id: str, by_person_id: str, now: datetime) -> InstanceOf`: Attach an event to a series (admin of the series' org, which must host it). [L54](../src/backend/graph/series.py#L54)
  - `publish_occurrences(series_id: str, starts: Sequence[datetime], by_person_id: str, now: datetime) -> list[Event]`: Publish e.g. a semester of meetings in one action. All or nothing: if any occurrence is rejected, none are created. [L75](../src/backend/graph/series.py#L75)
  - `update_defaults(series_id: str, by_person_id: str, now: datetime, **changes) -> EventSeries`: Change series defaults; future instances still using the old value follow. [L100](../src/backend/graph/series.py#L100)

### `src/backend/graph/calendar.py`

A host's calendar: every event a person or org is hosting.

- **`HostEvents`**: A host's events, split into `public` and `private` tuples. [L30](../src/backend/graph/calendar.py#L30)
- **`CalendarService`**: A host's calendar for a viewer. `graph.calendar`. [L36](../src/backend/graph/calendar.py#L36)
  - `host_events(host_id: str, viewer_id: str | None, *, starts_after: datetime | None = None, starts_before: datetime | None = None) -> HostEvents`: A person's or org's events, split into public and private, filtered to what the viewer may see; optional date window. [L44](../src/backend/graph/calendar.py#L44)

### `src/backend/graph/rsvps.py`

RSVP rules (user_Orgs.md, Person -> Event: RSVP).

- **`RsvpService`**: RSVPs, who can see them, friends going, and counts. `graph.rsvps`. [L50](../src/backend/graph/rsvps.py#L50)
  - `respond(event_id: str, person_id: str, status: RsvpStatus | None, now: datetime, *, origin: GeoPoint | None = None) -> Rsvp | None`: Set your RSVP, or clear it with `status=None`. `person_id` is the person acting; nobody can RSVP for someone else. `origin` is where the person is right now, if they chose to share it. [L65](../src/backend/graph/rsvps.py#L65)
  - `mine(event_id: str, person_id: str) -> Rsvp | None`: A person's RSVP to an event, if any. [L128](../src/backend/graph/rsvps.py#L128)
  - `set_rsvp_visibility(person_id: str, by_person_id: str, level: Visibility) -> Person`: Who can see your RSVPs: NETWORK, CONNECTIONS or PRIVATE (nobody). [L132](../src/backend/graph/rsvps.py#L132)
  - `friends_going(viewer_id: str | None, event_id: str, *, friend_ids: set[str] | None = None) -> tuple[Person, ...]`: Your connections who are going and let you see it. Pass `friend_ids` to reuse a viewer's connections across many events. [L149](../src/backend/graph/rsvps.py#L149)
  - `going_count(event_id: str) -> int | None`: Everyone going, or None while it's under MIN_PUBLIC_COUNT. [L175](../src/backend/graph/rsvps.py#L175)

## Discover and ranking

The event list and the "would they go?" model: filters, location, tastes, friends going, and the reasons shown.

### `src/backend/graph/discovery.py`

Event discovery: a visibility-aware, filterable, explainable event list.

- **`DiscoveryQuery`**: What to search for: viewer, text, tags, org, dates, location and radius, paging. Validated on creation. [L72](../src/backend/graph/discovery.py#L72)
- **`RequestMatch`**: How well one event fits a request made in words (from AI search). [L112](../src/backend/graph/discovery.py#L112)
- **`DiscoveryItem`**: One result: the event, its host, the likelihood score, reasons, friends going, your RSVP, count, distance, factors. [L124](../src/backend/graph/discovery.py#L124)
- **`DiscoveryPage`**: One page of results, with the total and paging info. [L139](../src/backend/graph/discovery.py#L139)
  - `has_more` (property): Whether more results follow this page. [L146](../src/backend/graph/discovery.py#L146)
- **`DiscoveryService`**: The ranked, filtered list of events a viewer may see. `graph.discovery`. [L166](../src/backend/graph/discovery.py#L166)
  - `search(query: DiscoveryQuery, now: datetime, *, request_matches: Mapping[str, RequestMatch] | None = None) -> DiscoveryPage`: The main Discover call: every event the viewer may see, filtered by the query and ranked by likelihood with reasons. `request_matches` (from AI search) adds how well each event fits a request. [L187](../src/backend/graph/discovery.py#L187)
  - `event_tag_ids(event_id: str) -> set[str]`: An event's own tags plus those inherited from its host orgs. [L293](../src/backend/graph/discovery.py#L293)
- `normalize_text(text: str) -> str`: Comparable form: Unicode-normalized (so "café" typed either way matches) and case-folded. [L385](../src/backend/graph/discovery.py#L385)

### `src/backend/graph/ranking.py`

The "would they go?" model behind Discover.

- **`RankingWeights`**: The weights of the likelihood model, one per signal, plus a bias. Must be finite numbers. [L55](../src/backend/graph/ranking.py#L55)
- **`EventSignals`**: What we know about one event, from one viewer's point of view. [L84](../src/backend/graph/ranking.py#L84)
- **`Contribution`**: One signal's part in the estimate. `effect` is in log-odds. [L119](../src/backend/graph/ranking.py#L119)
- **`Estimate`**: A likelihood and each signal's contribution to it. [L128](../src/backend/graph/ranking.py#L128)
  - `effect(name: str) -> float`: Total log-odds effect of one named signal. [L132](../src/backend/graph/ranking.py#L132)
- `estimate(signals: EventSignals, weights: RankingWeights = DEFAULT_WEIGHTS) -> Estimate`: The likelihood (0-1) that the viewer would go, with each signal's contribution. [L136](../src/backend/graph/ranking.py#L136)
- `signal_values(s: EventSignals) -> Mapping[str, float]`: Each signal's raw value for an event, before weighting. [L148](../src/backend/graph/ranking.py#L148)
- `sigmoid(x: float) -> float`: 1 / (1 + e^-x), without overflow for very large |x|. [L170](../src/backend/graph/ranking.py#L170)
- `closeness(distance_km: float, scale_km: float) -> float`: 1 at distance 0, exactly 0.5 at `scale_km`, falling toward 0. [L178](../src/backend/graph/ranking.py#L178)
- `soonness(days_until: float) -> float`: 1 for an event starting now (or under way), fading over about a week. [L187](../src/backend/graph/ranking.py#L187)
- `staleness(inactive_days: float) -> float`: 0 within the grace period, then rising smoothly toward 1. [L192](../src/backend/graph/ranking.py#L192)
- `taste_match(taste: Mapping[str, float], event_tags: Iterable[str]) -> float`: Affinity signal: cosine(taste, event tags) x strength of your taste, where a chosen interest (weight >= 1) is full strength. In [0, 1]. [L198](../src/backend/graph/ranking.py#L198)
- `cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float`: Cosine similarity of two non-negative sparse vectors, in [0, 1]. Empty or all-zero vectors have similarity 0. [L205](../src/backend/graph/ranking.py#L205)

### `src/backend/graph/explain.py`

Plain-language reasons for a Discover result.

- `explain(signals: EventSignals, result: Estimate, host: Organization | Person, my_hosts: list[str], tags: set[str], profile: TasteProfile, preferred: frozenset[str], labels: dict[str, str], request_reason: str = '') -> tuple[str, ...]`: Plain-language reasons for a Discover result, built from the model's own contributions. [L30](../src/backend/graph/explain.py#L30)
- `format_distance(km: float) -> str`: Readable distance: "under 50 m", "450 m", "2.3 km", "40 km". [L126](../src/backend/graph/explain.py#L126)

### `src/backend/graph/preferences.py`

What a person likes and how far they usually go, for their own Discover.

- **`TasteProfile`**: What a person likes: chosen interests, org tags, learned categories, and usual travel distance. [L59](../src/backend/graph/preferences.py#L59)
  - `vector(extra_chosen: frozenset[str] = frozenset()) -> dict[str, float]`: One weight per tag, combining every source. [L66](../src/backend/graph/preferences.py#L66)
- **`PreferenceService`**: Interests, learned tastes and travel habits. `graph.preferences`. [L81](../src/backend/graph/preferences.py#L81)
  - `add_interest(person_id: str, tag_id: str, by_person_id: str, now: datetime) -> InterestedIn`: Add a tag to your interests (only you can). [L87](../src/backend/graph/preferences.py#L87)
  - `remove_interest(person_id: str, tag_id: str, by_person_id: str) -> None`: Remove one of your interests. [L105](../src/backend/graph/preferences.py#L105)
  - `interests_of(person_id: str, by_person_id: str) -> list[Tag]`: Your interest tags. [L110](../src/backend/graph/preferences.py#L110)
  - `record_trip(person_id: str, distance_km: float) -> Person`: Fold one trip's distance into the person's running average. Called by RsvpService; the distance itself is not kept. [L119](../src/backend/graph/preferences.py#L119)
  - `forget_travel(person_id: str, by_person_id: str) -> Person`: Reset your learned travel distance. [L136](../src/backend/graph/preferences.py#L136)
  - `profile(person_id: str | None, now: datetime) -> TasteProfile`: Everything Discover may use for this person. Computed fresh each time and never stored. Logged-out visitors get an empty profile. [L143](../src/backend/graph/preferences.py#L143)
  - `my_profile(person_id: str, by_person_id: str, now: datetime) -> TasteProfile`: Lets a person see what Discover has learned about them. [L161](../src/backend/graph/preferences.py#L161)
- `typical_trip_km(person: Person) -> float | None`: The person's usual travel distance, once there's enough history. [L208](../src/backend/graph/preferences.py#L208)
- `decay(age: timedelta, half_life: timedelta) -> float`: 1 for now or the future, 0.5 after one half-life, and so on. [L216](../src/backend/graph/preferences.py#L216)

### `src/backend/graph/activity.py`

What a person interacts with most: their top orgs and recent events.

- **`OrgInteraction`**: An org and how strongly a person interacts with it. [L46](../src/backend/graph/activity.py#L46)
- **`EventInteraction`**: An event a person RSVP'd to, with status, time and tags. [L53](../src/backend/graph/activity.py#L53)
- **`ActivityService`**: What a person interacts with most. `graph.activity`. [L60](../src/backend/graph/activity.py#L60)
  - `top_orgs(person_id: str, now: datetime, limit: int = DEFAULT_TOP_ORGS) -> list[OrgInteraction]`: The orgs a person interacts with most (membership, follows, RSVPs to their events), strongest first. [L64](../src/backend/graph/activity.py#L64)
  - `recent_events(person_id: str, limit: int = DEFAULT_RECENT_EVENTS) -> list[EventInteraction]`: Events the person recently RSVP'd going or interested to, newest first. [L91](../src/backend/graph/activity.py#L91)

## AI search

Searching in your own words: the single-call AI search, the search agent and its tools, and the Claude client.

### `src/backend/graph/ai_search.py`

AI search: "find me something fast paced that uses my brain".

- **`CandidateEvent`**: An event as described to the AI: no ids, no names of people. [L65](../src/backend/graph/ai_search.py#L65)
- **`ViewerContext`**: The searcher as described to the AI: interests, tastes, top orgs, recent events, travel distance. [L79](../src/backend/graph/ai_search.py#L79)
- **`SearchRequest`**: Everything one AI search sends: the sentence, the date, the viewer context, the candidates. [L89](../src/backend/graph/ai_search.py#L89)
- **`Interpretation`**: The AI's answer: how it understood the request, and a match per candidate. [L97](../src/backend/graph/ai_search.py#L97)
- **`InterpreterError`** (Exception): The interpreter couldn't produce a usable answer; fall back. [L102](../src/backend/graph/ai_search.py#L102)
- **`RequestInterpreter`** (Protocol): Interface for anything that scores a search request against candidates. [L106](../src/backend/graph/ai_search.py#L106)
  - `interpret(request: SearchRequest) -> Interpretation`: Interface: score how well each candidate fits the request (Claude, or a fake in tests). [L109](../src/backend/graph/ai_search.py#L109)
- **`AiSearchResult`**: The result page, how the request was understood, whether AI was used, and why not if it wasn't. [L116](../src/backend/graph/ai_search.py#L116)
- **`AiSearchService`**: Search in your own words with one AI call, keyword fallback. `graph.ai_search`. [L123](../src/backend/graph/ai_search.py#L123)
  - `search(viewer_id: str | None, sentence: str, now: datetime, *, origin: GeoPoint | None = None, max_distance_km: float | None = None, limit: int = DEFAULT_RESULTS, offset: int = 0) -> AiSearchResult`: Single-call AI search: candidates from Discover, one interpreter call, re-ranked page with fit reasons; keyword fallback. [L138](../src/backend/graph/ai_search.py#L138)
- `clean_sentence(sentence: str) -> str`: Tidy and validate a search sentence (not blank, at most 500 characters). [L250](../src/backend/graph/ai_search.py#L250)
- `keyword_match(sentence: str, candidate: CandidateEvent) -> float`: Fallback when AI isn't available: the share of the request's words (ignoring filler words) found in the event, 0..1. Words match on a shared stem ("robot" ~ "robotics"). No meaningful words -> 0. [L261](../src/backend/graph/ai_search.py#L261)
- `meaningful_words(text: str) -> set[str]`: The words of a request worth matching: normalized, minus filler words. [L277](../src/backend/graph/ai_search.py#L277)

### `src/backend/graph/search_agent.py`

Agentic search: Claude works out what the person wants, looks up what matters about them, runs its own searches, and picks the best events.

- **`AgentError`** (Exception): The agent run didn't finish properly; fall back. [L87](../src/backend/graph/search_agent.py#L87)
- **`AgentStep`**: One tool call the search agent made: tool, input, whether it errored. [L92](../src/backend/graph/search_agent.py#L92)
- **`AgentSearchResult`**: The agent's goal, its ranked picks, its steps, and whether it fell back. [L99](../src/backend/graph/search_agent.py#L99)
- **`SearchAgentService`**: The search agent: Claude with tools, in a capped loop. `graph.search_agent`. [L107](../src/backend/graph/search_agent.py#L107)
  - `search(viewer_id: str | None, sentence: str, now: datetime, *, origin: GeoPoint | None = None) -> AgentSearchResult`: Agentic search: Claude works out the goal, reads the person, runs searches and submits picks; returns ranked picks and the steps taken; falls back. [L129](../src/backend/graph/search_agent.py#L129)

### `src/backend/graph/search_tools.py`

The tools the search agent may call, bound to one viewer and one search.

- **`ToolInputError`** (ValueError): The agent sent unusable input; the message goes back to it. [L38](../src/backend/graph/search_tools.py#L38)
- **`Pick`**: One event the agent picked: ref, fit score, reason. [L43](../src/backend/graph/search_tools.py#L43)
- **`SearchSession`**: State for one agent run: which events the agent has seen, and its picks. [L50](../src/backend/graph/search_tools.py#L50)
  - `ref_for(item: DiscoveryItem) -> str`: The short ref ("e1") for an event in this search, assigning a new one if needed. [L61](../src/backend/graph/search_tools.py#L61)
- **`SearchTools`**: The tools the agent may call, bound to one viewer and one search; runs them with validation. [L136](../src/backend/graph/search_tools.py#L136)
  - `run(name: str, tool_input: Any) -> tuple[str, bool]`: Run one tool. Returns (JSON text, is_error). [L161](../src/backend/graph/search_tools.py#L161)

### `src/backend/graph/claude_interpreter.py`

The Claude-backed interpreter for AI search.

- **`ClaudeInterpreter`**: The Claude-backed `RequestInterpreter` (one structured call per search). [L97](../src/backend/graph/claude_interpreter.py#L97)
  - `interpret(request: SearchRequest) -> Interpretation`: One Claude call (structured JSON) scoring the candidates; raises `InterpreterError` on any failure. [L111](../src/backend/graph/claude_interpreter.py#L111)
- `make_client(timeout: float = TIMEOUT_SECONDS) -> Any`: An Anthropic client using the key from backend.credentials, or the SDK's own credential sources when no key is stored. [L152](../src/backend/graph/claude_interpreter.py#L152)
- `build_user_message(request: SearchRequest) -> str`: The JSON Claude sees. Only fields defined on the request types, which hold no names of people, emails or positions. [L162](../src/backend/graph/claude_interpreter.py#L162)
- `parse_answer(text: str, refs: set[str]) -> Interpretation`: Validate Claude's JSON. Unknown refs are dropped, scores must be real numbers in [0, 1] (slightly out-of-range values are clamped), duplicate refs keep the first answer, and reasons are trimmed. [L174](../src/backend/graph/claude_interpreter.py#L174)

## Privacy, integrity and data access

Who may see what, account erasure, the integrity audit, and getting user data out.

### `src/backend/graph/privacy.py`

Visibility enforcement and account erasure.

- **`PrivacyService`**: Who may see what, and true account erasure. `graph.privacy`. [L36](../src/backend/graph/privacy.py#L36)
  - `effective_visibility(record: GraphRecord) -> Visibility`: The strictest visibility level among a record and (for edges) its two ends. [L49](../src/backend/graph/privacy.py#L49)
  - `can_view(viewer_id: str | None, record: GraphRecord) -> bool`: Whether a viewer may see a record: every party's setting must allow it; owners and event editors always can. [L58](../src/backend/graph/privacy.py#L58)
  - `can_view_listing(viewer_id: str | None, event: Event) -> bool`: Whether an event may be shown or acted on: the viewer must be able to see the event and its primary host (an event is never more visible than whoever runs it). Used by Discover, RSVPs and co-host requests. [L80](../src/backend/graph/privacy.py#L80)
  - `are_connected(a: str, b: str) -> bool`: Whether two people are connected. [L110](../src/backend/graph/privacy.py#L110)
  - `erase_person(person_id: str) -> None`: True erasure: hard-delete every edge touching the person, then the node. Orgs left without an admin return to unclaimed; events they were primary host of pass to a co-host or are deleted; invites they sent or redeemed are deleted. [L113](../src/backend/graph/privacy.py#L113)

### `src/backend/graph/integrity.py`

Integrity audit: checks that every relationship stored in the graph is genuine, i.e. it connects the right kinds of node and carries evidence that the right parties agreed to it.

- **`IntegrityIssue`**: One problem the audit found: the record id and what's wrong. [L39](../src/backend/graph/integrity.py#L39)
- **`IntegrityService`**: The integrity audit. `graph.integrity`. [L44](../src/backend/graph/integrity.py#L44)
  - `audit() -> tuple[IntegrityIssue, ...]`: Read-only check for relationships that aren't genuine; returns the problems found. [L48](../src/backend/graph/integrity.py#L48)

### `src/backend/graph/data_access.py`

Getting user data out: for the person themselves, and for analysis.

- **`MlDataset`**: Tables of pseudonymized user activity for analytics and ML, with when and why it was made. [L71](../src/backend/graph/data_access.py#L71)
  - `tables() -> dict[str, list[Row]]`: The dataset's tables by name: people, interactions, memberships, connections, events, orgs. [L81](../src/backend/graph/data_access.py#L81)
- `export_key_from_env() -> bytes`: The pseudonymization key. Never hard-coded: set COLLECTIVIZE_EXPORT_KEY. [L89](../src/backend/graph/data_access.py#L89)
- **`DataAccessService`**: A person's own data, and the ML dataset. `graph.data_access`. [L97](../src/backend/graph/data_access.py#L97)
  - `person_data(person_id: str, requested_by: str, now: datetime, *, staff_purpose: str | None = None) -> Row`: Everything held about one person: for themselves, or for staff with a recorded purpose. [L107](../src/backend/graph/data_access.py#L107)
  - `ml_dataset(staff_id: str, purpose: str, now: datetime, key: bytes) -> MlDataset`: Consent-gated, pseudonymized tables of all users for analytics/ML; records a `DataExport`. [L160](../src/backend/graph/data_access.py#L160)

## Storage

Where nodes and edges are kept (in memory or SQLite), how they're encoded, and the errors services raise.

### `src/backend/graph/store.py`

Storage interface for nodes and edges, plus an in-memory implementation.

- **`GraphStore`** (Protocol): The storage interface every service uses. [L25](../src/backend/graph/store.py#L25)
  - `save_node(node: N) -> N`: Save a node (insert, or replace the stored version). [L26](../src/backend/graph/store.py#L26)
  - `has_node(node_id: str) -> bool`: Whether a node with this id exists. [L27](../src/backend/graph/store.py#L27)
  - `node(node_id: str) -> N`: Load a node of the given type; `NotFoundError` if missing or a different type. [L28](../src/backend/graph/store.py#L28)
  - `any_node(node_id: str) -> GraphRecord`: Load a node of any type; `NotFoundError` if missing. [L29](../src/backend/graph/store.py#L29)
  - `delete_node(node_id: str) -> None`: Hard-delete a node. Reserved for account erasure. [L30](../src/backend/graph/store.py#L30)
  - `nodes() -> list[N]`: All stored nodes of a type (including subclasses). [L31](../src/backend/graph/store.py#L31)
  - `save_edge(edge: E) -> E`: Save an edge after checking both endpoints exist and are kinds this edge may connect. [L32](../src/backend/graph/store.py#L32)
  - `edge(edge_id: str) -> E`: Load an edge of the given type; `NotFoundError` if missing. [L33](../src/backend/graph/store.py#L33)
  - `edges(*, source_id: str | None = None, target_id: str | None = None, include_inactive: bool = False) -> list[E]`: Edges of a type, optionally filtered by source and/or target; active ones unless `include_inactive`. [L34](../src/backend/graph/store.py#L34)
  - `edges_touching(node_id: str) -> list[Edge]`: Every edge (any type, any state) that starts or ends at a node. [L42](../src/backend/graph/store.py#L42)
  - `delete_edge(edge_id: str) -> None`: Hard-delete an edge. Reserved for account erasure. [L43](../src/backend/graph/store.py#L43)
  - `transaction() -> AbstractContextManager[None]`: All-or-nothing block: every write inside is undone if it raises. Nested blocks roll back on their own. [L44](../src/backend/graph/store.py#L44)
  - `close() -> None`: Release the underlying connection. [L45](../src/backend/graph/store.py#L45)
- `check_edge_endpoints(edge: Edge, load: Callable[[str], GraphRecord | None]) -> None`: Both endpoints must exist and be the node kinds the edge allows. [L48](../src/backend/graph/store.py#L48)
- **`InMemoryGraphStore`**: Non-persistent store, used by tests and `CollectivizeGraph()`. [L59](../src/backend/graph/store.py#L59)
  - `save_node(node: N) -> N`: Save a node (insert, or replace the stored version). [L66](../src/backend/graph/store.py#L66)
  - `has_node(node_id: str) -> bool`: Whether a node with this id exists. [L72](../src/backend/graph/store.py#L72)
  - `node(node_id: str) -> N`: Load a node of the given type; `NotFoundError` if missing or a different type. [L75](../src/backend/graph/store.py#L75)
  - `any_node(node_id: str) -> GraphRecord`: Load a node of any type; `NotFoundError` if missing. [L81](../src/backend/graph/store.py#L81)
  - `delete_node(node_id: str) -> None`: Hard delete. Reserved for account erasure. [L87](../src/backend/graph/store.py#L87)
  - `nodes() -> list[N]`: All stored nodes of a type (including subclasses). [L91](../src/backend/graph/store.py#L91)
  - `save_edge(edge: E) -> E`: Save an edge after checking both endpoints exist and are kinds this edge may connect. [L96](../src/backend/graph/store.py#L96)
  - `edge(edge_id: str) -> E`: Load an edge of the given type; `NotFoundError` if missing. [L101](../src/backend/graph/store.py#L101)
  - `edges(*, source_id: str | None = None, target_id: str | None = None, include_inactive: bool = False) -> list[E]`: Edges of a type, optionally filtered by source and/or target; active ones unless `include_inactive`. [L107](../src/backend/graph/store.py#L107)
  - `edges_touching(node_id: str) -> list[Edge]`: Every edge (any type, any state) that starts or ends at a node. [L124](../src/backend/graph/store.py#L124)
  - `delete_edge(edge_id: str) -> None`: Hard delete. Reserved for account erasure. [L129](../src/backend/graph/store.py#L129)
  - `transaction() -> Iterator[None]`: All-or-nothing: restores the previous state if the block raises. Records are immutable, so shallow copies are a full snapshot. [L136](../src/backend/graph/store.py#L136)
  - `close() -> None`: Nothing to release. [L146](../src/backend/graph/store.py#L146)

### `src/backend/graph/sqlite_store.py`

SQLite-backed GraphStore used for the test and production databases.

- **`EnvironmentMismatchError`** (RuntimeError): A database file was opened under the wrong environment. [L53](../src/backend/graph/sqlite_store.py#L53)
- `read_environment_stamp(path: Path | str) -> str | None`: The environment a database file is stamped with, read without changing the file. None if it isn't a stamped Collectivize database. [L57](../src/backend/graph/sqlite_store.py#L57)
- **`SqliteGraphStore`**: Persistent store for the test and production databases; each file is stamped with its environment. [L73](../src/backend/graph/sqlite_store.py#L73)
  - `save_node(node: N) -> N`: Save a node (insert, or replace the stored version). [L87](../src/backend/graph/sqlite_store.py#L87)
  - `has_node(node_id: str) -> bool`: Whether a node with this id exists. [L97](../src/backend/graph/sqlite_store.py#L97)
  - `node(node_id: str) -> N`: Load a node of the given type; `NotFoundError` if missing or a different type. [L101](../src/backend/graph/sqlite_store.py#L101)
  - `any_node(node_id: str) -> GraphRecord`: Load a node of any type; `NotFoundError` if missing. [L107](../src/backend/graph/sqlite_store.py#L107)
  - `delete_node(node_id: str) -> None`: Hard delete. Reserved for account erasure. [L113](../src/backend/graph/sqlite_store.py#L113)
  - `nodes() -> list[N]`: All stored nodes of a type (including subclasses). [L117](../src/backend/graph/sqlite_store.py#L117)
  - `save_edge(edge: E) -> E`: Save an edge after checking both endpoints exist and are kinds this edge may connect. [L126](../src/backend/graph/sqlite_store.py#L126)
  - `edge(edge_id: str) -> E`: Load an edge of the given type; `NotFoundError` if missing. [L145](../src/backend/graph/sqlite_store.py#L145)
  - `edges(*, source_id: str | None = None, target_id: str | None = None, include_inactive: bool = False) -> list[E]`: Edges of a type, optionally filtered by source and/or target; active ones unless `include_inactive`. [L154](../src/backend/graph/sqlite_store.py#L154)
  - `edges_touching(node_id: str) -> list[Edge]`: Every edge (any type, any state) that starts or ends at a node. [L178](../src/backend/graph/sqlite_store.py#L178)
  - `delete_edge(edge_id: str) -> None`: Hard delete. Reserved for account erasure. [L185](../src/backend/graph/sqlite_store.py#L185)
  - `transaction() -> Iterator[None]`: All-or-nothing: rolls back every write in the block if it raises. [L192](../src/backend/graph/sqlite_store.py#L192)
  - `close() -> None`: Release the underlying connection. [L223](../src/backend/graph/sqlite_store.py#L223)

### `src/backend/graph/codec.py`

Convert graph records to and from JSON-safe dicts for persistence.

- `record_types() -> dict[str, type[GraphRecord]]`: Every node/edge class, keyed by the type name stored in the DB. [L29](../src/backend/graph/codec.py#L29)
- `type_names_for(cls: type) -> list[str]`: Stored type names that are `cls` or one of its subclasses. [L38](../src/backend/graph/codec.py#L38)
- `encode_record(record: GraphRecord) -> dict[str, Any]`: Turn a node or edge into JSON-safe data for storage. [L43](../src/backend/graph/codec.py#L43)
- `decode_record(type_name: str, data: dict[str, Any]) -> GraphRecord`: Rebuild a node or edge from stored data, re-running its validation (`ValueError` if it can't). [L47](../src/backend/graph/codec.py#L47)

### `src/backend/graph/errors.py`

Errors raised by graph services.

- **`NotFoundError`** (LookupError): A referenced node or edge does not exist. [L6](../src/backend/graph/errors.py#L6)
- **`PermissionDeniedError`** (DomainRuleError): The acting person is not allowed to perform this operation. [L10](../src/backend/graph/errors.py#L10)

## Domain objects (nodes and edges)

The immutable records the graph is made of. Updates return changed copies; validation runs on creation.

### `src/backend/objects/base.py`

Cross-cutting rules every node and edge follows (see user_Orgs.md).

- **`DomainRuleError`** (ValueError): Raised when an operation would violate a rule of the graph. [L20](../src/backend/objects/base.py#L20)
- `utc_now() -> datetime`: The current time in UTC. [L24](../src/backend/objects/base.py#L24)
- `new_id() -> str`: A new random id. [L28](../src/backend/objects/base.py#L28)
- `require_aware(value: datetime | None, name: str) -> None`: Raise `DomainRuleError` if a datetime has no time zone (`None` is allowed). [L32](../src/backend/objects/base.py#L32)
- **`Visibility`** (IntEnum): Audience levels, ordered from least to most restrictive. [L37](../src/backend/objects/base.py#L37)
- `most_restrictive(*levels) -> Visibility`: The strictest of several visibility levels. [L47](../src/backend/objects/base.py#L47)
- **`VerificationStatus`** (enum): Whether a record's facts were checked: unverified, verified or disputed. [L51](../src/backend/objects/base.py#L51)
- **`ProvenanceMethod`** (enum): How a record was created: by an org admin, an import, staff, or a person's own claim. [L57](../src/backend/objects/base.py#L57)
- **`RecordState`** (enum): Lifecycle of a record: active, removed or archived. [L64](../src/backend/objects/base.py#L64)
- **`Provenance`**: Who created a record, how and when, when it was last confirmed, and its verification. [L71](../src/backend/objects/base.py#L71)
  - Fields: `created_by`, `method`, `created_at`, `last_confirmed_at`, `verification`
  - `last_known_good` (property): When the record was last confirmed, or created if never. [L83](../src/backend/objects/base.py#L83)
  - `confirmed(at: datetime) -> Provenance`: A copy marked as confirmed at a time. [L86](../src/backend/objects/base.py#L86)
  - `with_verification(status: VerificationStatus) -> Provenance`: A copy with a new verification status. [L89](../src/backend/objects/base.py#L89)
  - `is_stale(now: datetime, max_age: timedelta) -> bool`: Whether the record hasn't been confirmed recently enough. [L92](../src/backend/objects/base.py#L92)
- **`GraphRecord`**: Base for every node and edge. Instances are immutable; use the `with_*` / state methods to derive updated copies. [L97](../src/backend/objects/base.py#L97)
  - Fields: `provenance`, `id`, `visibility`, `state`
  - `is_active` (property): Not removed or archived. [L107](../src/backend/objects/base.py#L107)
  - `with_visibility(visibility: Visibility) -> Self`: A copy with a new visibility level. [L110](../src/backend/objects/base.py#L110)
  - `with_provenance(provenance: Provenance) -> Self`: A copy with new provenance. [L113](../src/backend/objects/base.py#L113)
  - `removed() -> Self`: A copy marked removed. [L116](../src/backend/objects/base.py#L116)
  - `archived() -> Self`: A copy marked archived. [L119](../src/backend/objects/base.py#L119)

### `src/backend/objects/person.py`

Person node: one real human with an account.

- **`VerificationLevel`** (enum): Which level of identity verification was achieved (not how). [L16](../src/backend/objects/person.py#L16)
- **`Person`** (GraphRecord): A person with an account: display name, private email, settings, and a private travel average. [L26](../src/backend/objects/person.py#L26)
  - Fields: `display_name`, `email`, `profile`, `verification_level`, `embedding`, `visibility`, `hide_connections`, `rsvp_visibility`, `travel_log_km`, `travel_samples`, `ai_uses_history`, `analytics_consent`
  - `with_hide_connections(hidden: bool) -> Person`: A copy with "hide my connections" turned on or off. [L66](../src/backend/objects/person.py#L66)

### `src/backend/objects/organization.py`

Organization node: any group with its own identity.

- **`OrgKind`** (enum): Chapter, national, council, club or institution. [L16](../src/backend/objects/organization.py#L16)
- **`ClaimState`** (enum): Unclaimed, claim pending, or claimed (has a verified admin). [L24](../src/backend/objects/organization.py#L24)
- **`Freshness`** (enum): How recently an org was active: active, quiet or stale. [L30](../src/backend/objects/organization.py#L30)
- **`Organization`** (GraphRecord): An org: name, kind, claim state, website links, and when it last published an event. [L45](../src/backend/objects/organization.py#L45)
  - Fields: `name`, `kind`, `claim_state`, `website_url`, `calendar_feed_url`, `last_event_published_at`
  - `is_claimed` (property): Whether the org has a verified admin. [L61](../src/backend/objects/organization.py#L61)
  - `inactive_days(now: datetime) -> float`: Days since the latest of profile confirmation and last event (0 if that is in the future). [L64](../src/backend/objects/organization.py#L64)
  - `freshness(now: datetime) -> Freshness`: Derived from the latest of profile confirmation and last event. [L72](../src/backend/objects/organization.py#L72)
  - `with_claim_state(claim_state: ClaimState) -> Organization`: A copy with a new claim state. [L84](../src/backend/objects/organization.py#L84)
  - `with_event_published(at: datetime) -> Organization`: A copy recording that it just published an event. [L87](../src/backend/objects/organization.py#L87)

### `src/backend/objects/event.py`

Event and EventSeries nodes.

- **`EventStatus`** (enum): Draft, published, cancelled, ended or archived. [L19](../src/backend/objects/event.py#L19)
- **`EventChange`**: A logged change to time or location, flagged to people who RSVP'd. [L28](../src/backend/objects/event.py#L28)
  - Fields: `changed_at`, `field_name`, `old_value`, `new_value`
- **`Event`** (GraphRecord): An event: title, times, description, location and coordinates, capacity, status, change log. [L38](../src/backend/objects/event.py#L38)
  - Fields: `title`, `starts_at`, `ends_at`, `description`, `location`, `coordinates`, `capacity`, `is_demo`, `status`, `published_at`, `change_log`, `embedding`
  - `status_at(now: datetime) -> EventStatus`: The status at a moment (ENDED once a published event's end time passes). [L63](../src/backend/objects/event.py#L63)
  - `was_published` (property): Whether the event was ever made public to its audience. A draft that is cancelled before publishing was not, and stays hidden. (Records stored before `published_at` existed count as published if their status says so.) [L69](../src/backend/objects/event.py#L69)
  - `has_ended(now: datetime) -> bool`: Whether the event's end time has passed. [L76](../src/backend/objects/event.py#L76)
  - `is_discoverable(now: datetime) -> bool`: Cancelled events stay visible until their original end time, so people who planned to go find out; never-published ones never show. [L79](../src/backend/objects/event.py#L79)
  - `published(now: datetime) -> Event`: A published copy (must be a draft starting in the future); records when. [L86](../src/backend/objects/event.py#L86)
  - `revised(now: datetime, **changes) -> Event`: A copy with edited fields; logs time and location changes on published events. [L93](../src/backend/objects/event.py#L93)
  - `cancelled() -> Event`: A cancelled copy (drafts and published events only). [L110](../src/backend/objects/event.py#L110)
  - `archived() -> Event`: An archived copy. [L115](../src/backend/objects/event.py#L115)
- **`EventSeries`** (GraphRecord): A recurring pattern whose defaults instances inherit unless overridden. [L120](../src/backend/objects/event.py#L120)
  - Fields: `owner_org_id`, `name`, `recurrence`, `default_title`, `default_duration`, `default_description`, `default_location`, `default_coordinates`, `default_capacity`, `is_demo`
  - `defaults() -> dict[str, Any]`: The values new instances inherit. [L138](../src/backend/objects/event.py#L138)
  - `instantiate(starts_at: datetime, provenance: Provenance, **overrides) -> Event`: A new draft event from the defaults, starting at a time. [L147](../src/backend/objects/event.py#L147)
  - `with_defaults(**changes) -> EventSeries`: A copy with changed defaults. [L160](../src/backend/objects/event.py#L160)

### `src/backend/objects/edges.py`

Edges of the Collectivize graph.

- **`RecommendationUse`** (enum): Whether a person-owned fact may feed recommendations. [L32](../src/backend/objects/edges.py#L32)
- **`Edge`** (GraphRecord): Base class of every relationship: source, target, and which kinds of node it may connect. [L41](../src/backend/objects/edges.py#L41)
  - Fields: `source_id`, `target_id`
  - `check_endpoints(source: GraphRecord, target: GraphRecord) -> None`: Raise unless this kind of edge may connect these kinds of node. [L53](../src/backend/objects/edges.py#L53)
- **`PersonOwnedEdge`** (Edge): A relationship owned by a person, with their recommendation-consent setting. [L67](../src/backend/objects/edges.py#L67)
  - Fields: `recommendation_use`
  - `with_recommendation_use(use: RecommendationUse) -> PersonOwnedEdge`: A copy with a new recommendation-consent setting. [L70](../src/backend/objects/edges.py#L70)
- **`MemberRole`** (enum): Member, officer or admin. [L80](../src/backend/objects/edges.py#L80)
- **`MemberOf`** (PersonOwnedEdge): Person (source) belongs to Org (target). Two-sided: the person claims it, the org confirms it. `confirmed_by` records who confirmed it (an org admin, or staff approving a claim); never the person. [L87](../src/backend/objects/edges.py#L87)
  - Fields: `role`, `started_at`, `ended_at`, `org_confirmed`, `confirmed_by`, `was_admin`, `visibility`
  - `is_current` (property): Active and not ended. [L113](../src/backend/objects/edges.py#L113)
  - `is_confirmed_current` (property): Current and confirmed by the org. [L117](../src/backend/objects/edges.py#L117)
  - `is_admin` (property): Confirmed, current, with the admin role. [L121](../src/backend/objects/edges.py#L121)
  - `confirmed(by: str) -> MemberOf`: A copy confirmed by the given admin or staff member. [L124](../src/backend/objects/edges.py#L124)
  - `with_role(role: MemberRole) -> MemberOf`: A copy with a new role. [L127](../src/backend/objects/edges.py#L127)
  - `ended(at: datetime) -> MemberOf`: A copy ended at a time. [L130](../src/backend/objects/edges.py#L130)
- **`Follows`** (PersonOwnedEdge): Later. One-sided "keep me updated"; no approval, private by default. [L135](../src/backend/objects/edges.py#L135)
  - Fields: `visibility`
- **`PartOfKind`** (enum): Chapter of, member of council, or under institution. [L147](../src/backend/objects/edges.py#L147)
- **`PartOf`** (Edge): Child org (source) is structurally part of parent org (target). [L154](../src/backend/objects/edges.py#L154)
  - Fields: `kind`, `child_accepted`, `parent_accepted`, `staff_verified`
  - `is_established` (property): Active, and accepted by both sides or staff-verified. [L166](../src/backend/objects/edges.py#L166)
- **`PartnersWith`** (Edge): Later. Peer (non-hierarchical) relation; needs both confirmations. [L172](../src/backend/objects/edges.py#L172)
  - Fields: `source_confirmed`, `target_confirmed`
  - `is_established` (property): Active and confirmed by both orgs. [L182](../src/backend/objects/edges.py#L182)
- **`HostRole`** (enum): Primary host or co-host. [L189](../src/backend/objects/edges.py#L189)
- **`HostOrigin`** (enum): How a co-host link started, which decides who must approve it. [L194](../src/backend/objects/edges.py#L194)
- **`Hosts`** (Edge): Org or Person (source) is responsible for Event (target). An event has one primary host and any number of co-hosts, people or orgs. A co-host counts only once the other side approves: the invited party for an invitation, or a current host for a request to join. [L202](../src/backend/objects/edges.py#L202)
  - Fields: `role`, `accepted`, `origin`
  - `is_effective` (property): Active and accepted. [L216](../src/backend/objects/edges.py#L216)
- **`InstanceOf`** (Edge): Event (source) is one occurrence of EventSeries (target). [L221](../src/backend/objects/edges.py#L221)
- **`RsvpStatus`** (enum): Going, interested or not going. [L231](../src/backend/objects/edges.py#L231)
- **`Rsvp`** (PersonOwnedEdge): A person's RSVP to an event (Person → Event). [L238](../src/backend/objects/edges.py#L238)
  - Fields: `status`, `updated_at`, `trip_recorded`
- **`AttendanceMethod`** (enum): How attendance was recorded: self-reported, host check-in or ticket scan. [L247](../src/backend/objects/edges.py#L247)
- **`Attended`** (PersonOwnedEdge): Strongest signal; private by default (location history is sensitive). [L254](../src/backend/objects/edges.py#L254)
  - Fields: `method`, `visibility`
- **`ConnectionOrigin`** (enum): How a connection started: a request, or an invite. [L267](../src/backend/objects/edges.py#L267)
- **`Connected`** (PersonOwnedEdge): Mutual connection; exists only when both people accept. The source is whoever sent the request. Visible to logged-in users by default; either person can hide their whole list (`Person.hide_connections`), and a connection shows only if neither person has. [L273](../src/backend/objects/edges.py#L273)
  - Fields: `source_accepted`, `target_accepted`, `origin`, `visibility`
  - `is_established` (property): Active and accepted by both people. [L288](../src/backend/objects/edges.py#L288)
- **`Tagged`** (Edge): Org or Event (source) is tagged with Tag (target) by its admins. [L296](../src/backend/objects/edges.py#L296)
- **`InterestedIn`** (PersonOwnedEdge): Later. Person (source) -> Tag (target). Set only by the person. [L304](../src/backend/objects/edges.py#L304)
  - Fields: `visibility`

### `src/backend/objects/taxonomy.py`

Tag (fixed curated set in MVP) and Venue (Later) nodes.

- **`Tag`** (GraphRecord): An explicit, explainable topic, e.g. "robotics". [L14](../src/backend/objects/taxonomy.py#L14)
  - Fields: `slug`, `label`, `is_curated`
- **`Venue`** (GraphRecord): Later: a physical place shared by events, enabling "near me". [L29](../src/backend/objects/taxonomy.py#L29)
  - Fields: `name`, `address`, `latitude`, `longitude`

### `src/backend/objects/geo.py`

Geographic points and great-circle distance.

- **`GeoPoint`**: A latitude/longitude point (validated). [L14](../src/backend/objects/geo.py#L14)
  - Fields: `latitude`, `longitude`
  - `distance_km(other: GeoPoint) -> float`: Great-circle distance to another point, in km. [L28](../src/backend/objects/geo.py#L28)
- `haversine_km(a: GeoPoint, b: GeoPoint) -> float`: Great-circle distance in km. Numerically safe for identical and antipodal points (the inner value is clamped to [0, 1]). [L32](../src/backend/objects/geo.py#L32)

### `src/backend/objects/invite.py`

Invite node: a single-use, expiring invitation from one person.

- **`Invite`** (GraphRecord): A single-use, expiring invite. Its id is the fingerprint of the code; the code isn't stored. [L17](../src/backend/objects/invite.py#L17)
  - Fields: `inviter_id`, `expires_at`, `used_by`, `used_at`, `revoked`, `visibility`
  - `is_open(now: datetime) -> bool`: Unused, not revoked, not expired. [L29](../src/backend/objects/invite.py#L29)
  - `used(person_id: str, now: datetime) -> Invite`: A copy marked used by a person. [L37](../src/backend/objects/invite.py#L37)
  - `revoked_now() -> Invite`: A revoked copy. [L40](../src/backend/objects/invite.py#L40)

### `src/backend/objects/data_export.py`

DataExport node: an audit record of every bulk export of user data.

- **`DataExport`** (GraphRecord): Audit record of a bulk data export: who, why, how much. [L11](../src/backend/objects/data_export.py#L11)
  - Fields: `requested_by`, `purpose`, `people`, `rows`, `visibility`

## Setup, configuration, demo server and demo data

Choosing the test or production database, credentials, the local demo web server (pages + JSON API), and the fictional demo world.

### `src/backend/config.py`

Database environment configuration.

- **`Environment`** (enum): Test or production. [L26](../src/backend/config.py#L26)
- **`ConfigError`** (ValueError): The environment configuration is invalid. [L31](../src/backend/config.py#L31)
- **`DatabaseConfig`**: Which environment and database file to use. [L36](../src/backend/config.py#L36)
  - `is_production` (property): True when this config points at the production database. [L41](../src/backend/config.py#L41)
- `load_config(env: Mapping[str, str] | None = None) -> DatabaseConfig`: Database config from `COLLECTIVIZE_ENV` (test by default) and `COLLECTIVIZE_DB_PATH`. [L51](../src/backend/config.py#L51)

### `src/backend/database.py`

Open the graph backed by the configured (test or production) database.

- `open_graph(config: DatabaseConfig | None = None) -> Iterator[CollectivizeGraph]`: Context manager: open the configured SQLite database as a `CollectivizeGraph`, and close it after. [L20](../src/backend/database.py#L20)

### `src/backend/credentials.py`

Where secrets come from. Never from source code or files in the repo.

- `anthropic_api_key() -> str | None`: The Anthropic API key: `ANTHROPIC_API_KEY`, else the macOS Keychain, else `None`. [L29](../src/backend/credentials.py#L29)

### `src/backend/server.py`

A small local web server for the demo: the frontend pages plus a JSON API.

- **`ApiError`** (Exception): [L74](../src/backend/server.py#L74)
- `demo_graph_factory(config: DatabaseConfig, *, use_ai: bool) -> GraphFactory`: Each request opens its own connection (SQLite connections can't be shared across the server's threads). [L100](../src/backend/server.py#L100)
- **`DiscoverApi`**: The API's behaviour, independent of HTTP (so it can be tested directly). [L124](../src/backend/server.py#L124)
  - `personas() -> dict[str, Any]`: Every demo person you can view Discover as, suggested ones first. [L137](../src/backend/server.py#L137)
  - `tags() -> dict[str, Any]`: All topic tags, by label (for the topic filter). [L159](../src/backend/server.py#L159)
  - `discover(params: dict[str, str]) -> dict[str, Any]`: One page of Discover for a viewer: `viewer`, `page`, `near=1`, `tag`. [L165](../src/backend/server.py#L165)
  - `search(body: dict[str, Any]) -> dict[str, Any]`: AI search via the search agent: `query`, `viewer`, `near`. Repeat searches come from the cache; one runs at a time. [L181](../src/backend/server.py#L181)
- **`DemoServer`** (ThreadingHTTPServer): [L308](../src/backend/server.py#L308)
- **`RequestHandler`** (BaseHTTPRequestHandler): [L316](../src/backend/server.py#L316)
  - `do_GET() -> None`: API routes, the home redirect, or a static file. [L321](../src/backend/server.py#L321)
  - `do_POST() -> None`: POST /api/search only. [L337](../src/backend/server.py#L337)
  - `log_message(format: str, *args) -> None`: One short log line per request (method and path, no query). [L344](../src/backend/server.py#L344)
- `resolve_static(url_path: str, root: Path = ROOT) -> Path | None`: The file a URL path refers to, or None unless it is a real file inside one of STATIC_DIRS. Blocks '..', encoded traversal, dotfiles and symlinks that lead outside. [L420](../src/backend/server.py#L420)
- `serve(api: DiscoverApi, port: int = 8000) -> DemoServer`: Create the server on 127.0.0.1 (call .serve_forever() to run it). [L436](../src/backend/server.py#L436)

### `src/backend/seed.py`

Fictional mock data for the test database.

- `place(name: str) -> GeoPoint`: Coordinates of a named demo campus place. [L52](../src/backend/seed.py#L52)
- **`ProductionSeedError`** (DomainRuleError): Raised when something tries to put mock data outside the test DB. [L117](../src/backend/seed.py#L117)
- **`SeedResult`**: Ids of the seeded records, keyed by their short names above. [L122](../src/backend/seed.py#L122)
- `seed_mock_data(graph: CollectivizeGraph, environment: Environment, now: datetime, bulk: BulkScale | None = None) -> SeedResult`: Seed the hand-built demo, plus a large generated world at `bulk` scale (see backend.seed_bulk) when given. [L133](../src/backend/seed.py#L133)

### `src/backend/seed_network.py`

The fictional demo social network: students, memberships, friendships and RSVPs.

- `seed_network(graph: CollectivizeGraph, orgs: dict[str, str], people: dict[str, str], admins: dict[str, str], now: datetime, make_person: Callable[[str], Person], event_ids: Iterable[str] = (), tag_ids: Iterable[str] = (), campus: GeoPoint | None = None) -> dict[str, str]`: Add students, their memberships, friendships and RSVPs to `event_ids`. `admins` maps a person key to the org key they administer. Returns the student ids. [L57](../src/backend/seed_network.py#L57)

### `src/backend/seed_bulk.py`

A large fictional world on top of the hand-built demo: several campuses, dozens of clubs and chapters, hundreds of students, and months of event history with RSVPs.

- **`BulkScale`**: How big a world to build. [L97](../src/backend/seed_bulk.py#L97)
- **`BulkSeedResult`**: [L116](../src/backend/seed_bulk.py#L116)
- `seed_bulk(graph: CollectivizeGraph, now: datetime, *, scale: BulkScale = LARGE, staff_id: str, campus: GeoPoint, tags: dict[str, str], institution_id: str, ifc_id: str) -> BulkSeedResult`: Build the bulk world. The first campus reuses the hand-built `institution_id` and `ifc_id` so both demos share one university. [L169](../src/backend/seed_bulk.py#L169)

### `src/backend/seed_catalog.py`

Fictional content for the bulk mock data: campuses, org templates, event templates and names.

- **`EventTemplate`**: [L15](../src/backend/seed_catalog.py#L15)
- **`OrgTemplate`**: [L24](../src/backend/seed_catalog.py#L24)
- **`CampusTemplate`**: [L33](../src/backend/seed_catalog.py#L33)
