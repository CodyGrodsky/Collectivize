Rules every node and edge follows

These four rules apply to everything in the graph. Most of the Collectivize principles are enforced here rather than per type.

Provenance. Every node and edge records where it came from: who created it, by what method (submitted by an org admin, imported from an external calendar, entered by a Collectivize staffer, self-claimed by a person), when it was last confirmed accurate, and its verification status (unverified, verified, disputed). Ranking and display read these fields, so an unverified or stale record can never look identical to a confirmed one.

Visibility. Every node and edge has a visibility level: public, network (logged-in users), members only, connections only, or private. The visibility of an edge is the most restrictive of three things: the edge’s own setting and the settings of both endpoints. A public RSVP to a members-only event is effectively members-only. A membership in a private org is invisible to non-members regardless of what the person chose. No action by one party can make another party more exposed than they chose to be.

Recommendation consent. Separate from visibility, each person-owned edge (membership, RSVP, connection, interest) has a flag for whether it may be used to compute recommendations. Visibility controls who can see a fact; this flag controls whether the system may use it. Someone can keep their membership private and still let it improve their own recommendations, or the reverse. The default is the narrower choice: a fact is used only to improve the person’s own recommendations, never to place them in front of others.

Lifecycle. Nothing is hard-deleted on its first removal. Records move to removed or archived states so history (past attendance, past membership) can be kept or erased according to the person’s choice. A person deleting their account triggers a true erasure of their edges, not just hiding them.

Nodes
Person (MVP: minimal; Later: full)

Represents: one real human with an account.

Identity: one account per human. The verification approach is still an open question in the strategy doc, so the node only records which level of verification has been achieved (email, university email, other), not how.

Holds: display name, profile content, interests (as edges, below), and a profile embedding (Later). Nothing about the person’s relationships is stored on the node itself; all of that lives on edges, so each fact can carry its own visibility.

Controlled by: the person alone. No org admin can edit a person node.

MVP scope: for the pilot, the only Person nodes needed are org admins who submit and edit listings. Student accounts can come later.

Organization (MVP)

Represents: any group with its own identity: a chapter, a national fraternity or sorority, a governing council (IFC, Panhellenic), a club, and later a business or neighborhood group.

Kind: a type field (chapter, national, council, club, institution) drives which org-to-org edges are allowed.

Claim state:

Unclaimed: a listing created by Collectivize or imported from public information. Labeled as such, editable only by Collectivize staff.
Claim pending: someone has requested admin access, and verification is in progress.
Claimed: at least one verified admin manages it.

This lets the directory launch populated without implying the org endorsed the listing. The strategy doc explicitly warns against inventing chapter information or implying endorsement.

Freshness: a derived status (active, quiet, stale) based on when the profile was last confirmed and when the org last published an event. Stale orgs are shown with a warning and ranked lower.

External identity: a link to the org’s own website and calendar feed, if any. This supports the independence principle: an org’s own site is the source of truth, and Collectivize mirrors it rather than replacing it.

Controlled by: the org’s admins, via their membership edges.

Event (MVP)

Represents: one occurrence at a specific time.

Holds: title, description, start and end time, location (free text in MVP, a Venue edge Later), capacity if any, an event embedding (Later), and a “fictional/demo” flag that can never be removed from demo data.

Lifecycle:

Draft: visible only to the host org’s admins.
Published: visible according to its visibility setting.
Updated: any change to time or location is logged and flagged to people who RSVP’d.
Cancelled: stays visible with a cancellation notice until its original time passes, so people who planned to go find out.
Ended: the time has passed. RSVPs are frozen, and attendance can now be confirmed.
Archived: dropped from discovery but kept for history.

Rules: an event must have at least one primary host. An event in the past cannot be published as new. A published event whose host org is stale is ranked lower.

EventSeries (MVP, lightweight)

Represents: a recurring pattern, such as weekly chapter meetings or an annual formal.

Holds: recurrence description and default details, which individual events inherit unless they override them.

Purpose: gives repeat-attendance signals one place to accumulate, and lets an org publish a semester of meetings with a single action, which addresses the doc’s concern about whether publishing is effortless enough to sustain.

Interest / Tag (Later; a fixed set in MVP)

Represents: a topic such as philanthropy, robotics, or intramural sports.

Purpose: gives search and filtering an explicit, explainable vocabulary alongside embeddings. When ranking explains a recommendation (“matches your interest in robotics”), it points to a tag the person chose rather than an opaque score.

Rules: in MVP, a curated list only. Later, user-proposed tags with moderation.

Venue (Later)

Represents: a physical place. Lets events share location data, enables “near me,” and supports future third-space ideas from the strategy doc.

Edges
Person → Org: MEMBER_OF (MVP for admins; Later for all)

Means: this person belongs to this org.

Holds: role (member, officer, admin), start and end dates, and confirmation state.

Two-sided rule: a person can claim membership, and the org can confirm it. Only confirmed memberships count as verified affiliation. Only the org can grant the admin role. Only the person can make their membership publicly visible, and the org cannot publish its roster on its own authority.

Ending: leaving moves the edge to a past state. The person chooses whether past membership stays visible.

Admin rule: an org must always have at least one admin. The last admin can’t leave without handing off the role or returning the org to unclaimed.

Org → Org: PART_OF (MVP)

Means: a structural, hierarchical relationship. This is your “adoption” idea.

Holds: relationship kind, such as chapter of a national, member of a council, or under an institution.

Rules:

Both orgs must accept the edge, or Collectivize staff must verify it against a public source. One org can’t claim another as a child.
The hierarchy must have no cycles. An org can have several parents (a chapter belongs to its national and to IFC), but no org can end up as its own ancestor.
The allowed kinds depend on org types. A council can’t be a chapter of a fraternity.

Use: browsing (“all IFC chapters”), inheriting some trust (a chapter under a verified national gets an easier verification path), and candidate generation for recommendations (“events from orgs related to ones you follow”).

Org ↔ Org: PARTNERS_WITH (Later)

Means: a peer relationship that isn’t hierarchical, such as a recurring joint event or a sister chapter. It’s kept separate from PART_OF so hierarchy queries stay clean. Mutual consent is required, and the edge can be inferred as a suggestion (they co-host often) but is only created when both orgs confirm.

Org → Event: HOSTS (MVP)

Means: this org is responsible for this event.

Holds: role (primary host, co-host).

Rules: exactly one primary host, any number of co-hosts. Adding a co-host requires that org’s acceptance, otherwise orgs could attach each other’s names to events. Only admins of a host org can edit the event. A co-host can remove itself at any time.

Event → EventSeries: INSTANCE_OF (MVP)

Means: this event is one occurrence of a series. An event belongs to at most one series. Editing the series updates future instances, with a choice to apply to one occurrence or all future ones. Past instances are never changed.

Person → Event: RSVP (Later)

Means: stated intent: going, interested, or not going.

Holds: status, the person’s own visibility choice for this RSVP, the recommendation-consent flag, and timestamps.

Rules: only possible while the event is published and hasn’t ended. The edge freezes when the event ends. Counts are always shown in aggregate. A named attendee appears only if their own setting allows it, and aggregate labels are suppressed when they’re small enough to identify individuals.

Person → Event: ATTENDED (Later)

Means: the person actually went. This is the strongest signal in the system and the one closest to the North Star.

Holds: how it was confirmed (self-reported, host check-in, ticket scan).

Rules: only possible after the event starts. A self-report and a host confirmation are different strengths of evidence, and the confirmation method is recorded. Attendance is private by default because a history of where someone has been is more sensitive than their stated interests.

Person → Org: FOLLOWS (Later)

Means: “keep me updated about this org,” with no claim of belonging. One-sided, needs no approval, and can be private. This gives students a low-commitment way to express interest. It’s the signal orgs should see in place of being able to search people.

Person ↔ Person: CONNECTED (Later)

Means: a mutual relationship that both people confirmed.

Rules: created only when both accept. Either can remove it silently. Visibility of your connection list is set by you, and each connection is visible only if both people allow it.

Open question: whether Collectivize uses mutual connections, one-way follows, or both is unresolved in the strategy doc. Mutual connections are the safer default because no one appears in a social graph without agreeing to it. A one-way follow edge between people can be added later if the product needs it.

Person / Org / Event → Tag: INTERESTED_IN and TAGGED (MVP for orgs and events; Later for people)

Orgs and events are tagged by their admins. People’s interests are set only by themselves. The system can suggest interests but never adds one silently.

Deliberately not stored as edges

Similarity. “Likeminded” and “close” relationships are never stored as edges between people. They’re computed when needed from embeddings in the vector index, or kept as a sparse top-k neighbor list for internal ranking only. They are never displayed as relationships, never visible to orgs, and never available as a queryable fact about a person. This keeps inferred closeness from turning into a social graph that no one agreed to.

Predicted attendance. Computed when ranking and then discarded. It can raise an event’s score but never appears as a person.

Derived signals the graph should produce

These are computed from the edges above and fed into ranking and the validation metrics in the strategy doc:

Org freshness and activity: time since last confirmation and since the last event published.
Event trust: host verification status, freshness of the host, and provenance of the event itself.
Participation depth: repeat attendance within a series and return visits to the same org. This is your evidence that discovery led to belonging.
Discovery-to-participation conversion: for people who consented, whether discovering an event led to an RSVP and then attendance.
Bridging: whether someone attended an event where they had no prior connections. This measures the system’s goal of opening paths beyond existing circles.
MVP summary

For the five-chapter pilot, you need:

Nodes: Organization (with claim state and freshness), Event, EventSeries, a fixed tag list, and Person only for admins.
Edges: MEMBER_OF (admins only), PART_OF, HOSTS, INSTANCE_OF, and TAGGED.
Cross-cutting rules: provenance and visibility on everything.

Everything else is defined now so the schema has a place for it, and gets built once there are real students to serve.

I can also turn this into a doc you can edit, share, or download as Word or PDF, if you want to keep it alongside the strategy document.