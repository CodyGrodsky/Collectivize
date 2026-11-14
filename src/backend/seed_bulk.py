"""A large fictional world on top of the hand-built demo: several campuses,
dozens of clubs and chapters, hundreds of students, and months of event
history with RSVPs.

Like the hand-built demo, every org is "[Demo] ...", every person is
"(demo ...)" and every event has `is_demo=True`. Every write goes through
the normal services, so the bulk data obeys the same graph rules
(claims, admin checks, hierarchy kinds, co-host acceptance, RSVP rules).

History is simulated by passing past times as `now`: an org is claimed
months ago, publishes events weeks before they start, and students RSVP
before those events happen. Freshness, ended events and travel habits
therefore come out the way they would for real use. A fixed random seed
keeps the world the same every time.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.graph import CollectivizeGraph
from backend.objects import (
    DomainRuleError,
    Event,
    EventSeries,
    GeoPoint,
    MemberRole,
    Organization,
    OrgKind,
    PartOfKind,
    Person,
    Provenance,
    ProvenanceMethod,
    RsvpStatus,
    Tag,
    VerificationLevel,
    Visibility,
)
from backend.seed_catalog import (
    CAMPUSES,
    CHAPTER_EVENTS,
    CHAPTER_NAMES,
    CLUBS,
    COUNCIL_EVENTS,
    EXTRA_TAGS,
    FIRST_NAMES,
    GREEK_NATIONALS,
    LAST_INITIALS,
    NATIONAL_EVENTS,
    STUDENT_EVENTS,
    CampusTemplate,
    EventTemplate,
    OrgTemplate,
)

RANDOM_SEED = 4242
DEMO_PREFIX = "[Demo]"
KM_PER_DEGREE_LAT = 111.195
BUILDING_SPREAD_KM = 0.9
HOME_SPREAD_DEGREES = 0.02
START_HOURS = (11, 13, 16, 18, 19, 20)

CLAIMED_SHARE = 0.88  # the rest stay unclaimed listings
DORMANT_SHARE = 0.10  # claimed orgs that stopped posting (show as stale)
SERIES_SHARE = 0.6  # orgs with a meeting template that run it as a series
SERIES_WEEKS = 6  # past and upcoming occurrences each
SECOND_ADMIN_SHARE = 0.2
PENDING_SHARE = 0.05  # membership requests never answered
HIDDEN_MEMBERSHIP_SHARE = 0.06
OFF_CAMPUS_ORG_SHARE = 0.15
ANALYTICS_SHARE = 0.8
HIDE_CONNECTIONS_SHARE = 0.03
STUDENT_HOST_SHARE = 0.03
DRAFT_SHARE = 0.08
CANCEL_SHARE = 0.06
ARCHIVE_SHARE = 0.04
CO_HOST_SHARE = 0.10
FRIENDS = (2, 7)
SAME_ORG_FRIEND_CHANCE = 0.7
SAME_CAMPUS_FRIEND_CHANCE = 0.9
EXTRA_RSVP_POOL = 20  # non-members on the same campus who might see an event
MEMBER_GOES_CHANCE = 0.5
FRIEND_GOES_CHANCE = 0.25
ANYONE_GOES_CHANCE = 0.04
SERIES_RSVP_SCALE = 0.5
GOING_SHARE = 0.85  # the rest say "interested"
RSVP_VISIBILITY_WEIGHTS = (
    (Visibility.CONNECTIONS, 0.7), (Visibility.NETWORK, 0.2), (Visibility.PRIVATE, 0.1),
)


@dataclass(frozen=True)
class BulkScale:
    """How big a world to build."""

    campuses: int = 3
    clubs_per_campus: int = 22
    chapters_per_campus: int = 5
    students_per_campus: int = 250
    past_events: tuple[int, int] = (3, 6)  # per active org, inclusive range
    upcoming_events: tuple[int, int] = (2, 5)


LARGE = BulkScale()
SMALL = BulkScale(
    campuses=2, clubs_per_campus=4, chapters_per_campus=2, students_per_campus=15,
    past_events=(1, 1), upcoming_events=(1, 2),
)


@dataclass(frozen=True)
class BulkSeedResult:
    orgs: dict[str, str]
    people: dict[str, str]
    tags: dict[str, str]
    events: tuple[str, ...]


@dataclass
class _Campus:
    template: CampusTemplate
    center: GeoPoint
    buildings: dict[str, GeoPoint]
    institution_id: str
    councils: dict[str, str] = field(default_factory=dict)  # role -> org id
    org_keys: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)


@dataclass
class _Org:
    key: str
    id: str
    kind: OrgKind
    campus: _Campus | None
    tags: tuple[str, ...]
    events: tuple[EventTemplate, ...]
    meeting: str | None = None
    admin: str | None = None
    claimed_at: datetime | None = None
    dormant: bool = False
    members: set[str] = field(default_factory=set)  # confirmed person ids


@dataclass
class _Person:
    id: str
    campus: _Campus
    home: GeoPoint


@dataclass(frozen=True)
class _Plan:
    """One event (or series) to publish, ordered by publish time so each
    org's "last published" ends on its newest event."""

    publish_at: datetime
    org: _Org
    template: EventTemplate
    starts_at: datetime
    outcome: str = "publish"  # publish | draft | cancel | archive
    series: bool = False  # a batch of weekly occurrences from starts_at


def seed_bulk(
    graph: CollectivizeGraph,
    now: datetime,
    *,
    scale: BulkScale = LARGE,
    staff_id: str,
    campus: GeoPoint,
    tags: dict[str, str],
    institution_id: str,
    ifc_id: str,
) -> BulkSeedResult:
    """Build the bulk world. The first campus reuses the hand-built
    `institution_id` and `ifc_id` so both demos share one university."""
    world = _BulkWorld(graph, now, scale, staff_id, campus)
    return world.build(tags, institution_id, ifc_id)


class _BulkWorld:
    def __init__(
        self,
        graph: CollectivizeGraph,
        now: datetime,
        scale: BulkScale,
        staff_id: str,
        campus: GeoPoint,
    ) -> None:
        self.g = graph
        self.now = now
        self.scale = scale
        self.staff_id = staff_id
        self.origin = campus
        self.rng = random.Random(RANDOM_SEED)
        self.names = _names(self.rng)
        self.tags: dict[str, str] = {}
        self.orgs: dict[str, _Org] = {}
        self.people: dict[str, _Person] = {}
        self.rank: dict[str, int] = {}  # person id -> creation order
        self.person_keys: dict[str, str] = {}
        self.edges: dict[tuple[str, str], str] = {}  # (person, org) -> membership id
        self.friends: dict[str, set[str]] = {}
        self.series: dict[str, str] = {}  # org key -> series id
        self.events: list[str] = []

    def build(self, tags: dict[str, str], institution_id: str, ifc_id: str) -> BulkSeedResult:
        self.tags = self._tags(tags)
        nationals = self._nationals()
        campuses = [
            self._campus(template, i, institution_id, ifc_id, nationals)
            for i, template in enumerate(CAMPUSES[: self.scale.campuses])
        ]
        self._claim_all(campuses)
        for c in campuses:
            self._students(c)
        self._memberships(campuses)
        self._tag_orgs()
        self._befriend()
        self._interests()
        self._schedule()
        self._student_events()
        return BulkSeedResult(
            orgs={key: org.id for key, org in self.orgs.items()},
            people=dict(self.person_keys),
            tags=self.tags,
            events=tuple(self.events),
        )

    # --- orgs ----------------------------------------------------------------

    def _tags(self, existing: dict[str, str]) -> dict[str, str]:
        tags = dict(existing)
        for slug, label in EXTRA_TAGS.items():
            if slug not in tags:
                tags[slug] = self.g.store.save_node(
                    Tag(slug=slug, label=label, provenance=self._staff())
                ).id
        return tags

    def _nationals(self) -> list[_Org]:
        count = min(len(GREEK_NATIONALS), self.scale.chapters_per_campus * 2)
        return [
            self._org(f"national:{name}", name, OrgKind.NATIONAL, None,
                      ("greek-life", "philanthropy"), NATIONAL_EVENTS)
            for name in GREEK_NATIONALS[:count]
        ]

    def _campus(
        self,
        template: CampusTemplate,
        index: int,
        institution_id: str,
        ifc_id: str,
        nationals: list[_Org],
    ) -> _Campus:
        center = _offset(self.origin, *template.offset_km)
        buildings = {
            b: _offset(center, self.rng.uniform(-BUILDING_SPREAD_KM, BUILDING_SPREAD_KM),
                       self.rng.uniform(-BUILDING_SPREAD_KM, BUILDING_SPREAD_KM))
            for b in template.buildings
        }
        reuse = index == 0
        inst = institution_id if reuse else self._new_org(template.name, OrgKind.INSTITUTION)
        campus = _Campus(template, center, buildings, inst)
        short = template.name.split()[0]
        ifc = ifc_id if reuse else None
        for role, name, tag in (
            ("ifc", f"{short} Interfraternity Council", "greek-life"),
            ("panhellenic", f"{short} Panhellenic Council", "greek-life"),
            ("sports", f"{short} Club Sports Council", "intramural-sports"),
        ):
            if role == "ifc" and ifc is not None:
                campus.councils[role] = ifc  # hand-built and already linked
                continue
            org = self._org(f"{template.key}:{role}", name, OrgKind.COUNCIL, campus,
                            (tag,), COUNCIL_EVENTS)
            campus.councils[role] = org.id
            self._link(org.id, inst, PartOfKind.UNDER_INSTITUTION)

        for i, national in enumerate(self.rng.sample(nationals, self.scale.chapters_per_campus)):
            letter = CHAPTER_NAMES[(index * self.scale.chapters_per_campus + i) % len(CHAPTER_NAMES)]
            national_name = national.key.split(":", 1)[1]
            org = self._org(f"{template.key}:chapter:{national_name}",
                            f"{national_name}, {letter} Chapter ({short})", OrgKind.CHAPTER,
                            campus, ("greek-life", "social", "philanthropy"), CHAPTER_EVENTS,
                            meeting="Chapter meeting")
            self._link(org.id, national.id, PartOfKind.CHAPTER_OF)
            council = "ifc" if i % 2 == 0 else "panhellenic"
            self._link(org.id, campus.councils[council], PartOfKind.MEMBER_OF_COUNCIL)
            self._link(org.id, inst, PartOfKind.UNDER_INSTITUTION)

        for club in self.rng.sample(CLUBS, min(self.scale.clubs_per_campus, len(CLUBS))):
            org = self._club(club, campus, short)
            self._link(org.id, inst, PartOfKind.UNDER_INSTITUTION)
            if club.sports:
                self._link(org.id, campus.councils["sports"], PartOfKind.MEMBER_OF_COUNCIL)
        return campus

    def _club(self, club: OrgTemplate, campus: _Campus, short: str) -> _Org:
        return self._org(f"{campus.template.key}:club:{club.name}", f"{short} {club.name}",
                         OrgKind.CLUB, campus, club.tags, club.events, meeting=club.meeting)

    def _org(
        self,
        key: str,
        name: str,
        kind: OrgKind,
        campus: _Campus | None,
        tags: tuple[str, ...],
        events: tuple[EventTemplate, ...],
        meeting: str | None = None,
    ) -> _Org:
        org = _Org(key, self._new_org(name, kind), kind, campus, tags, events, meeting)
        self.orgs[key] = org
        if campus is not None:
            campus.org_keys.append(key)
        return org

    def _new_org(self, name: str, kind: OrgKind) -> str:
        slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
        org = Organization(
            name=f"{DEMO_PREFIX} {name}",
            kind=kind,
            website_url=f"https://example.org/demo/{slug}" if self.rng.random() < 0.4 else None,
            provenance=self._staff(),
        )
        return self.g.store.save_node(org).id

    def _link(self, child: str, parent: str, kind: PartOfKind) -> None:
        self.g.hierarchy.staff_link(child, parent, kind, self.staff_id, self.now)

    def _claim_all(self, campuses: list[_Campus]) -> None:
        """Most orgs get a verified admin, claimed long enough ago to have
        a history; a few stay unclaimed listings."""
        m = self.g.membership
        home_campus = campuses[0]
        for org in self.orgs.values():
            if org.kind is not OrgKind.COUNCIL and self.rng.random() > CLAIMED_SHARE:
                continue
            campus = org.campus or home_campus
            role = {OrgKind.CHAPTER: "chapter", OrgKind.NATIONAL: "national",
                    OrgKind.COUNCIL: "council"}.get(org.kind, "club")
            person = self._person(campus, f"demo {role} admin", f"admin:{org.key}")
            org.claimed_at = self._days_ago(330, 700)
            claim = m.claim_org(person.id, org.id, org.claimed_at)
            m.approve_claim(claim.id, self.staff_id, org.claimed_at)
            org.admin = person.id
            org.members.add(person.id)
            self.edges[(person.id, org.id)] = claim.id
            org.dormant = org.kind is OrgKind.CLUB and self.rng.random() < DORMANT_SHARE

    def _tag_orgs(self) -> None:
        for org in self.orgs.values():
            if org.admin is None:
                continue
            for slug in org.tags:
                self.g.events.tag(org.id, self.tags[slug], org.admin, org.claimed_at)

    # --- people --------------------------------------------------------------

    def _students(self, campus: _Campus) -> None:
        for i in range(self.scale.students_per_campus):
            self._person(campus, "demo student", f"student:{campus.template.key}:{i}")

    def _person(self, campus: _Campus, label: str, key: str) -> _Person:
        rng = self.rng
        node = Person(
            display_name=f"{next(self.names)} ({label})",
            verification_level=VerificationLevel.UNIVERSITY_EMAIL,
            analytics_consent=rng.random() < ANALYTICS_SHARE,
            hide_connections=rng.random() < HIDE_CONNECTIONS_SHARE,
            rsvp_visibility=_weighted(rng, RSVP_VISIBILITY_WEIGHTS),
            provenance=Provenance(
                created_by=None, method=ProvenanceMethod.SELF_CLAIM,
                created_at=self._days_ago(330, 900),
            ),
        )
        self.g.store.save_node(node)
        home = GeoPoint(
            campus.center.latitude + rng.uniform(-HOME_SPREAD_DEGREES, HOME_SPREAD_DEGREES),
            campus.center.longitude + rng.uniform(-HOME_SPREAD_DEGREES, HOME_SPREAD_DEGREES),
        )
        person = _Person(node.id, campus, home)
        self.people[node.id] = person
        self.rank[node.id] = len(self.rank)
        self.person_keys[key] = node.id
        self.friends[node.id] = set()
        campus.people.append(node.id)
        return person

    def _memberships(self, campuses: list[_Campus]) -> None:
        m = self.g.membership
        joinable = {
            id(c): [self.orgs[k] for k in c.org_keys if self.orgs[k].kind is not OrgKind.COUNCIL]
            for c in campuses
        }
        everywhere = [org for orgs in joinable.values() for org in orgs]
        for person_id in self.people:
            person = self.people[person_id]
            local = joinable[id(person.campus)]
            for _ in range(self.rng.randint(1, 4)):
                pool = everywhere if self.rng.random() < OFF_CAMPUS_ORG_SHARE else local
                org = self.rng.choice(pool)
                if (person_id, org.id) in self.edges:
                    continue
                edge = m.request(person_id, org.id, self._days_ago(160, 700))
                self.edges[(person_id, org.id)] = edge.id
                if org.admin is None or self.rng.random() < PENDING_SHARE:
                    continue  # waiting on an admin who never answers
                m.confirm(edge.id, org.admin)
                org.members.add(person_id)
                if self.rng.random() < HIDDEN_MEMBERSHIP_SHARE:
                    level = self.rng.choice((Visibility.MEMBERS, Visibility.PRIVATE))
                    m.set_visibility(edge.id, person_id, level)
        for org in self.orgs.values():
            others = self._in_order(org.members - {org.admin})
            if org.admin is None or not others:
                continue
            for person_id in self.rng.sample(others, min(2, len(others))):
                m.set_role(self.edges[(person_id, org.id)], MemberRole.OFFICER, org.admin)
            if self.rng.random() < SECOND_ADMIN_SHARE:
                m.set_role(self.edges[(others[0], org.id)], MemberRole.ADMIN, org.admin)

    def _befriend(self) -> None:
        """Friends mostly share an org, and almost always a campus."""
        shared: dict[str, set[str]] = {pid: set() for pid in self.people}
        for org in self.orgs.values():
            for pid in org.members:
                shared[pid] |= org.members
        everyone = list(self.people)
        for pid in everyone:
            same_org = self._in_order(shared[pid] - {pid})
            same_campus = self.people[pid].campus.people
            wanted = self.rng.randint(*FRIENDS)
            for _ in range(wanted * 4):
                if len(self.friends[pid]) >= wanted:
                    break
                roll = self.rng.random()
                if same_org and roll < SAME_ORG_FRIEND_CHANCE:
                    other = self.rng.choice(same_org)
                elif roll < SAME_CAMPUS_FRIEND_CHANCE:
                    other = self.rng.choice(same_campus)
                else:
                    other = self.rng.choice(everyone)
                if other == pid or other in self.friends[pid]:
                    continue
                when = self._days_ago(5, 300)
                request = self.g.connections.request(pid, other, when)
                self.g.connections.accept(request.id, other)
                self.friends[pid].add(other)
                self.friends[other].add(pid)

    def _interests(self) -> None:
        """Students mostly like what their orgs do, plus the odd surprise."""
        org_tags: dict[str, set[str]] = {pid: set() for pid in self.people}
        for org in self.orgs.values():
            for pid in org.members:
                org_tags[pid] |= set(org.tags)
        all_tags = sorted(self.tags)
        for pid in self.people:
            pool = sorted(org_tags[pid]) or all_tags
            picks = set(self.rng.sample(pool, min(len(pool), self.rng.randint(1, 3))))
            if self.rng.random() < 0.3:
                picks.add(self.rng.choice(all_tags))
            for slug in sorted(picks):
                self.g.preferences.add_interest(pid, self.tags[slug], pid, self._days_ago(1, 200))

    # --- events --------------------------------------------------------------

    def _schedule(self) -> None:
        plans: list[_Plan] = []
        for org in self.orgs.values():
            if org.admin is not None:
                plans += self._plans_for(org)
        plans.sort(key=lambda p: (p.publish_at, p.org.key))
        for plan in plans:
            if plan.series:
                self._run_series(plan)
            else:
                self._run_event(plan)

    def _plans_for(self, org: _Org) -> list[_Plan]:
        rng = self.rng
        plans = []
        past_range = (100, 300) if org.dormant else (3, 150)
        for _ in range(rng.randint(*self.scale.past_events)):
            starts = self._at_hour(-rng.randint(*past_range))
            outcome = "archive" if rng.random() < ARCHIVE_SHARE else "publish"
            plans.append(_Plan(starts - timedelta(days=rng.randint(4, 21)), org,
                               rng.choice(org.events), starts, outcome))
        if not org.dormant:
            for _ in range(rng.randint(*self.scale.upcoming_events)):
                starts = self._at_hour(rng.randint(1, 60))
                roll = rng.random()
                outcome = ("draft" if roll < DRAFT_SHARE
                           else "cancel" if roll < DRAFT_SHARE + CANCEL_SHARE else "publish")
                plans.append(_Plan(self.now - timedelta(days=rng.randint(0, 14)), org,
                                   rng.choice(org.events), starts, outcome))
            if org.meeting and rng.random() < SERIES_SHARE:
                meeting = EventTemplate(org.meeting, "")
                first = self._at_hour(-7 * SERIES_WEEKS, hour=19)
                nxt = self._at_hour(7, hour=19)
                plans.append(_Plan(first - timedelta(days=7), org, meeting, first, series=True))
                plans.append(_Plan(self.now - timedelta(days=1), org, meeting, nxt, series=True))
        return plans

    def _run_event(self, plan: _Plan) -> None:
        org, t = plan.org, plan.template
        created = plan.publish_at - timedelta(days=1)
        event = self._event(org, t.title, t.description, plan.starts_at, t.hours, created,
                            capacity=t.capacity,
                            visibility=Visibility.MEMBERS if t.members_only else Visibility.PUBLIC)
        ev = self.g.events
        ev.create(event, org.id, org.admin)
        self.events.append(event.id)
        for slug in self.rng.sample(org.tags, min(2, len(org.tags))):
            ev.tag(event.id, self.tags[slug], org.admin, created)
        if plan.outcome == "draft":
            return
        ev.publish(event.id, org.admin, plan.publish_at)
        hosts = [org]
        co_host = self._co_host_for(org)
        if co_host is not None:
            invite = ev.invite_co_host(event.id, co_host.id, org.admin, plan.publish_at)
            ev.accept_co_host(invite.id, co_host.admin)
            hosts.append(co_host)
        self._rsvps(event, hosts, plan.publish_at, members_only=t.members_only)
        if plan.outcome == "cancel":
            ev.cancel(event.id, org.admin)
        elif plan.outcome == "archive":
            ev.archive(event.id, org.admin)

    def _run_series(self, plan: _Plan) -> None:
        week = timedelta(days=7)
        starts = [plan.starts_at + week * i for i in range(SERIES_WEEKS)]
        series_id = self._series_for(plan.org, plan.template.title)
        occurrences = self.g.series.publish_occurrences(
            series_id, starts, plan.org.admin, plan.publish_at
        )
        for event in occurrences:
            self.events.append(event.id)
            self._rsvps(event, [plan.org], plan.publish_at, scale=SERIES_RSVP_SCALE, extras=0)

    def _series_for(self, org: _Org, title: str) -> str:
        if org.key in self.series:
            return self.series[org.key]
        venue = self._venue(org)
        series = EventSeries(
            owner_org_id=org.id,
            name=f"Weekly {title.lower()}",
            recurrence="Weekly",
            default_title=title,
            default_description=f"The weekly {title.lower()}. "
                                "Regulars and newcomers both welcome.",
            default_location=venue,
            default_coordinates=self._coordinates(org, venue),
            default_duration=timedelta(hours=1),
            is_demo=True,
            provenance=self._staff(),
        )
        self.series[org.key] = self.g.series.create(series, org.admin).id
        return self.series[org.key]

    def _student_events(self) -> None:
        for pid in self.people:
            if self.rng.random() >= STUDENT_HOST_SHARE:
                continue
            person = self.people[pid]
            t = self.rng.choice(STUDENT_EVENTS)
            published = self.now - timedelta(days=self.rng.randint(0, 5))
            venue = self.rng.choice(sorted(person.campus.buildings))
            event = Event(
                title=t.title, description=t.description,
                starts_at=(starts := self._at_hour(self.rng.randint(1, 21))),
                ends_at=starts + timedelta(hours=t.hours),
                location=venue, coordinates=person.campus.buildings[venue],
                capacity=t.capacity, is_demo=True,
                provenance=Provenance(created_by=pid, method=ProvenanceMethod.SELF_CLAIM,
                                      created_at=published),
            )
            self.g.events.create(event, pid, pid)
            self.g.events.publish(event.id, pid, published)
            self.events.append(event.id)
            pool = self._in_order(self.friends[pid])
            self._respond_all(event, pool, set(), published, scale=1.0, first_chance=0.6)

    def _event(
        self,
        org: _Org,
        title: str,
        description: str,
        starts_at: datetime,
        hours: int,
        created: datetime,
        **extra: object,
    ) -> Event:
        venue = self._venue(org)
        return Event(
            title=title,
            description=description,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=hours),
            location=venue,
            coordinates=self._coordinates(org, venue),
            is_demo=True,
            provenance=Provenance(created_by=org.admin, method=ProvenanceMethod.ORG_ADMIN,
                                  created_at=created),
            **extra,  # type: ignore[arg-type]
        )

    def _venue(self, org: _Org) -> str:
        campus = org.campus or self._any_campus()
        building = self.rng.choice(sorted(campus.buildings))
        if self.rng.random() < 0.5:
            return f"{building}, Room {self.rng.randint(100, 450)}"
        return building

    def _coordinates(self, org: _Org, venue: str) -> GeoPoint:
        campus = org.campus or self._any_campus()
        return campus.buildings[venue.split(",")[0]]

    def _any_campus(self) -> _Campus:
        """Nationals have no campus; their events happen at the first one."""
        return next(o.campus for o in self.orgs.values() if o.campus is not None)

    def _co_host_for(self, org: _Org) -> _Org | None:
        if org.campus is None or self.rng.random() >= CO_HOST_SHARE:
            return None
        options = [
            self.orgs[k] for k in org.campus.org_keys
            if self.orgs[k] is not org and self.orgs[k].admin is not None
            and self.orgs[k].admin != org.admin
        ]
        return self.rng.choice(options) if options else None

    # --- RSVPs ---------------------------------------------------------------

    def _rsvps(
        self,
        event: Event,
        hosts: list[_Org],
        published_at: datetime,
        *,
        members_only: bool = False,
        scale: float = 1.0,
        extras: int = EXTRA_RSVP_POOL,
    ) -> None:
        members = set().union(*(h.members for h in hosts))
        pool = set(members)
        campus = hosts[0].campus
        if campus is not None and not members_only and extras:
            pool |= set(self.rng.sample(campus.people, min(extras, len(campus.people))))
        self._respond_all(event, self._in_order(pool), members, published_at, scale)

    def _respond_all(
        self,
        event: Event,
        pool: list[str],
        members: set[str],
        published_at: datetime,
        scale: float,
        first_chance: float | None = None,
    ) -> None:
        """Members go first and most often; friends of people going follow."""
        window_end = min(event.starts_at, self.now)
        window = max((window_end - published_at).total_seconds(), 60)
        going: set[str] = set()
        ordered = sorted(pool, key=lambda p: (p not in members, self.rank[p]))
        for pid in ordered:
            if pid in members:
                chance = MEMBER_GOES_CHANCE
            elif first_chance is not None:
                chance = first_chance
            elif self.friends[pid] & going:
                chance = FRIEND_GOES_CHANCE
            else:
                chance = ANYONE_GOES_CHANCE
            if self.rng.random() >= chance * scale:
                continue
            when = published_at + timedelta(seconds=self.rng.uniform(0, window - 1))
            status = RsvpStatus.GOING if self.rng.random() < GOING_SHARE else RsvpStatus.INTERESTED
            if self._respond(event.id, pid, status, when) is RsvpStatus.GOING:
                going.add(pid)

    def _respond(
        self, event_id: str, person_id: str, status: RsvpStatus, when: datetime
    ) -> RsvpStatus | None:
        rsvps = self.g.rsvps
        origin = self.people[person_id].home
        try:
            rsvps.respond(event_id, person_id, status, when, origin=origin)
            return status
        except LookupError:  # the event is hidden from this person
            return None
        except DomainRuleError:  # full: say interested instead
            rsvps.respond(event_id, person_id, RsvpStatus.INTERESTED, when)
            return RsvpStatus.INTERESTED

    # --- helpers -------------------------------------------------------------

    def _staff(self) -> Provenance:
        return Provenance(
            created_by=self.staff_id, method=ProvenanceMethod.STAFF, created_at=self.now
        )

    def _in_order(self, person_ids: set[str]) -> list[str]:
        """Ids are random, so order by creation to keep the world repeatable."""
        return sorted(person_ids, key=self.rank.__getitem__)

    def _days_ago(self, low: int, high: int) -> datetime:
        return self.now - timedelta(days=self.rng.randint(low, high),
                                    minutes=self.rng.randint(0, 1439))

    def _at_hour(self, days_ahead: int, hour: int | None = None) -> datetime:
        day = self.now + timedelta(days=days_ahead)
        hour = hour if hour is not None else self.rng.choice(START_HOURS)
        return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def _offset(center: GeoPoint, north_km: float, east_km: float) -> GeoPoint:
    km_per_degree_lon = KM_PER_DEGREE_LAT * math.cos(math.radians(center.latitude))
    return GeoPoint(
        center.latitude + north_km / KM_PER_DEGREE_LAT,
        center.longitude + east_km / km_per_degree_lon,
    )


def _names(rng: random.Random) -> Iterator[str]:
    """Unique "First L." names in a shuffled order, with a counter once
    every combination is used."""
    combos = [f"{first} {initial}." for first in FIRST_NAMES for initial in LAST_INITIALS]
    rng.shuffle(combos)
    round_ = 1
    while True:
        for name in combos:
            yield name if round_ == 1 else f"{name[:-1]}{round_}."
        round_ += 1


def _weighted(rng: random.Random, choices: tuple[tuple[Visibility, float], ...]) -> Visibility:
    values, weights = zip(*choices)
    return rng.choices(values, weights=weights)[0]


__all__ = ["LARGE", "SMALL", "BulkScale", "BulkSeedResult", "seed_bulk"]
