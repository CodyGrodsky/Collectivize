"""Fictional mock data for the test database.

Every org name starts with "[Demo]", every person is labelled "(demo ...)",
and every event has `is_demo=True`, so mock records can never be mistaken
for real ones. Seeding refuses to run outside the test environment.

All writes go through the normal services, so the mock data obeys every
graph rule (claims, admin checks, hierarchy kinds, co-host acceptance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from backend.config import Environment
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
)
from backend.seed_bulk import BulkScale, BulkSeedResult, seed_bulk
from backend.seed_network import seed_network
from backend.seed_profiles import seed_profiles

SEED_STAFF_ID = "seed-staff"
DEMO_INVITE_CODE = "demo-join-from-drew"  # fixed so the demo can type it in
CAMPUS = GeoPoint(42.3500, -71.1000)  # centre of the fictional campus
KM_PER_DEGREE_LAT = 111.195
# Named places: (km north, km east) of the campus centre.
PLACES = {
    "Student Center, Room 204": (0.0, 0.0),
    "North Field": (0.9, 0.2),
    "Engineering Hall": (0.3, -0.5),
    "Library, Room 3": (-0.4, 0.3),
    "Downtown night market": (2.5, 5.5),
}


def place(name: str) -> GeoPoint:
    north_km, east_km = PLACES[name]
    km_per_degree_lon = KM_PER_DEGREE_LAT * math.cos(math.radians(CAMPUS.latitude))
    return GeoPoint(
        CAMPUS.latitude + north_km / KM_PER_DEGREE_LAT,
        CAMPUS.longitude + east_km / km_per_degree_lon,
    )
DEMO_PREFIX = "[Demo]"
EVENING_HOUR = 18

TAGS = {
    "philanthropy": "Philanthropy",
    "robotics": "Robotics",
    "intramural-sports": "Intramural sports",
    "social": "Social",
    "academic": "Academic",
}
ORGS = {
    "university": ("Northbridge University", OrgKind.INSTITUTION),
    "ifc": ("Northbridge Interfraternity Council", OrgKind.COUNCIL),
    "national": ("Alpha Beta National", OrgKind.NATIONAL),
    "chapter": ("Alpha Beta, Gamma Chapter", OrgKind.CHAPTER),
    "robotics": ("Robotics Club", OrgKind.CLUB),
    "chess": ("Chess Club", OrgKind.CLUB),  # deliberately left unclaimed
    "surf": ("Surf Club", OrgKind.CLUB),
    "soccer": ("Club Soccer", OrgKind.CLUB),
    "cooking": ("Cooking Club", OrgKind.CLUB),
    "delta": ("Alpha Beta, Delta Chapter", OrgKind.CHAPTER),
    "sigma": ("Sigma Tau, Epsilon Chapter", OrgKind.CHAPTER),
}
PEOPLE = {
    "avery": "Avery (demo chapter admin)",
    "blake": "Blake (demo national admin)",
    "casey": "Casey (demo club admin)",
    "drew": "Drew (demo student)",
    "emery": "Emery (demo student)",
    "finley": "Finley (demo club admin)",
    "gray": "Gray (demo club admin)",
    "harper": "Harper (demo club admin)",
    "indigo": "Indigo (demo chapter admin)",
    "jordan": "Jordan (demo chapter admin)",
}
ADMINS = {
    "avery": "chapter",
    "blake": "national",
    "casey": "robotics",
    "finley": "surf",
    "gray": "soccer",
    "harper": "cooking",
    "indigo": "delta",
    "jordan": "sigma",
}
HIERARCHY = [
    ("chapter", "national", PartOfKind.CHAPTER_OF),
    ("chapter", "ifc", PartOfKind.MEMBER_OF_COUNCIL),
    ("ifc", "university", PartOfKind.UNDER_INSTITUTION),
    ("robotics", "university", PartOfKind.UNDER_INSTITUTION),
    ("delta", "national", PartOfKind.CHAPTER_OF),
    ("delta", "ifc", PartOfKind.MEMBER_OF_COUNCIL),
    ("sigma", "ifc", PartOfKind.MEMBER_OF_COUNCIL),
    *[(club, "university", PartOfKind.UNDER_INSTITUTION)
      for club in ("surf", "soccer", "cooking")],
]


class ProductionSeedError(DomainRuleError):
    """Raised when something tries to put mock data outside the test DB."""


@dataclass(frozen=True)
class SeedResult:
    """Ids of the seeded records, keyed by their short names above."""

    orgs: dict[str, str]
    people: dict[str, str]
    tags: dict[str, str]
    events: dict[str, str] = field(default_factory=dict)
    invite_code: str | None = None  # an open invite from Drew, for the join demo
    bulk: BulkSeedResult | None = None  # the large generated world, if requested


def seed_mock_data(
    graph: CollectivizeGraph,
    environment: Environment,
    now: datetime,
    bulk: BulkScale | None = None,
) -> SeedResult:
    """Seed the hand-built demo, plus a large generated world at `bulk`
    scale (see backend.seed_bulk) when given."""
    store_environment = getattr(graph.store, "environment", Environment.TEST)
    if environment is not Environment.TEST or store_environment is not Environment.TEST:
        raise ProductionSeedError("refusing to seed mock data outside the test database")
    with graph.store.transaction():
        result = _MockWorld(graph, now).build()
        if bulk is None:
            return result
        extra = seed_bulk(
            graph, now, scale=bulk, staff_id=SEED_STAFF_ID, campus=CAMPUS,
            tags=result.tags, institution_id=result.orgs["university"],
            ifc_id=result.orgs["ifc"],
        )
        DemoCalendar(graph, now).fill(result.people["drew"])
        return replace(result, bulk=extra)


class DemoCalendar:
    """Gives the demo student a busy calendar: going / maybe / saved on a
    spread of upcoming events, so every calendar color shows up."""

    PLANS = ("going", "maybe", "saved", "going", "maybe", "saved", "skip")
    DAYS_AHEAD = 42
    PER_DAY = 3

    def __init__(self, graph: CollectivizeGraph, now: datetime) -> None:
        self.g = graph
        self.now = now

    def fill(self, person_id: str) -> None:
        per_day: dict[Any, int] = {}
        for i, event in enumerate(self._upcoming(person_id)):
            day = event.starts_at.date()
            plan = self.PLANS[i % len(self.PLANS)]
            if plan == "skip" or per_day.get(day, 0) >= self.PER_DAY:
                continue
            try:
                if plan == "saved":
                    self.g.stars.star(event.id, person_id, self.now)
                else:
                    status = RsvpStatus.GOING if plan == "going" else RsvpStatus.INTERESTED
                    self.g.rsvps.respond(event.id, person_id, status, self.now)
            except DomainRuleError:
                continue  # cancelled, full, or otherwise closed: skip it
            per_day[day] = per_day.get(day, 0) + 1

    def _upcoming(self, person_id: str) -> list[Event]:
        horizon = self.now + timedelta(days=self.DAYS_AHEAD)
        events = [
            e for e in self.g.store.nodes(Event)
            if e.was_published and e.is_active and self.now < e.starts_at < horizon
            and self.g.privacy.can_view(person_id, e)
        ]
        return sorted(events, key=lambda e: (e.starts_at, e.id))


class _MockWorld:
    def __init__(self, graph: CollectivizeGraph, now: datetime) -> None:
        self.g = graph
        self.now = now

    def build(self) -> SeedResult:
        save = self.g.store.save_node
        tags = {
            slug: save(Tag(slug=slug, label=label, provenance=self._staff())).id
            for slug, label in TAGS.items()
        }
        orgs = {
            key: save(
                Organization(name=f"{DEMO_PREFIX} {name}", kind=kind, provenance=self._staff())
            ).id
            for key, (name, kind) in ORGS.items()
        }
        people = {key: save(self._person(name)).id for key, name in PEOPLE.items()}
        self._memberships(orgs, people)
        for child, parent, kind in HIERARCHY:
            self.g.hierarchy.staff_link(orgs[child], orgs[parent], kind, SEED_STAFF_ID, self.now)
        events = self._events(orgs, people, tags)
        people |= seed_network(
            self.g, orgs, people, ADMINS, self.now, self._person,
            event_ids=events.values(), tag_ids=tags.values(), campus=CAMPUS,
        )
        people |= seed_profiles(self.g, orgs, people, ADMINS, self.now, self._person)
        invite = self.g.invites.create(people["drew"], self.now, code=DEMO_INVITE_CODE)
        return SeedResult(
            orgs=orgs, people=people, tags=tags, events=events, invite_code=invite.code
        )

    # --- scenario ------------------------------------------------------------

    def _memberships(self, orgs: dict[str, str], people: dict[str, str]) -> None:
        m = self.g.membership
        for person, org in ADMINS.items():
            claim = m.claim_org(people[person], orgs[org], self.now)
            m.approve_claim(claim.id, SEED_STAFF_ID, self.now)
        drew = m.request(people["drew"], orgs["chapter"], self.now)
        m.confirm(drew.id, people["avery"])
        m.set_role(drew.id, MemberRole.OFFICER, people["avery"])
        m.request(people["emery"], orgs["robotics"], self.now)  # left pending

    def _events(
        self, orgs: dict[str, str], people: dict[str, str], tags: dict[str, str]
    ) -> dict[str, str]:
        ev = self.g.events
        avery, blake, casey = people["avery"], people["blake"], people["casey"]
        events: dict[str, str] = {}

        series = self.g.series.create(self._weekly_series(orgs["chapter"]), avery)
        starts = [self._evening(7 * week + 1) for week in range(4)]
        meetings = self.g.series.publish_occurrences(series.id, starts, avery, self.now)
        events |= {f"meeting_{i + 1}": e.id for i, e in enumerate(meetings)}

        fundraiser = self._published("Spring philanthropy 5K", 10, orgs["chapter"], avery,
                                     hours=3, location="North Field",
                                     description="A timed 5K loop around campus to raise money "
                                     "for the food bank. Run it hard or walk it with friends.")
        invite = ev.invite_co_host(fundraiser.id, orgs["national"], avery, self.now)
        ev.accept_co_host(invite.id, blake)
        ev.tag(fundraiser.id, tags["philanthropy"], avery, self.now)
        events["fundraiser"] = fundraiser.id

        demo_day = self._published("Robot demo day", 5, orgs["robotics"], casey,
                                   location="Engineering Hall", capacity=60,
                                   description="Teams race their robots through an obstacle "
                                   "course, then take questions on how they built them.")
        ev.tag(demo_day.id, tags["robotics"], casey, self.now)
        events["demo_day"] = demo_day.id
        # A request to join, left waiting for the club's admin to answer.
        emery = people["emery"]
        self.g.co_hosting.request(demo_day.id, emery, emery, self.now)

        mixer = self._published("Fall mixer", 3, orgs["chapter"], avery,
                                description="Music, snacks and meeting new people.")
        events["cancelled_mixer"] = ev.cancel(mixer.id, avery).id

        draft = ev.create(self._event("Formal", 30), orgs["chapter"], avery)
        events["draft_formal"] = draft.id

        # A combined event: a student hosts it, invites the Robotics Club,
        # and the club's admin accepts, so it shows on both calendars.
        drew = people["drew"]
        study_jam = self._published("Robotics study jam", 4, drew, drew,
                                    location="Library, Room 3",
                                    description="Timed coding puzzles in pairs, then we compare "
                                    "solutions. Quick rounds, lots of problem solving.")
        invite = ev.invite_co_host(study_jam.id, orgs["robotics"], drew, self.now)
        ev.accept_co_host(invite.id, casey)
        events["study_jam"] = study_jam.id

        # Off campus, to show distance at work in Discover.
        crawl = self._published("Night market food crawl", 6, orgs["cooking"],
                                people["harper"], location="Downtown night market",
                                description="A relaxed evening walking the night market and "
                                "sharing street food from a dozen stalls.")
        events["food_crawl"] = crawl.id

        ev.tag(orgs["robotics"], tags["robotics"], casey, self.now)
        ev.tag(orgs["chapter"], tags["social"], avery, self.now)
        return events

    # --- builders ------------------------------------------------------------

    def _staff(self) -> Provenance:
        return Provenance(
            created_by=SEED_STAFF_ID, method=ProvenanceMethod.STAFF, created_at=self.now
        )

    def _person(self, name: str) -> Person:
        return Person(
            display_name=name,
            verification_level=VerificationLevel.UNIVERSITY_EMAIL,
            provenance=Provenance(
                created_by=None, method=ProvenanceMethod.SELF_CLAIM, created_at=self.now
            ),
        )

    def _evening(self, days_ahead: int) -> datetime:
        day = self.now + timedelta(days=days_ahead)
        return day.replace(hour=EVENING_HOUR, minute=0, second=0, microsecond=0)

    def _event(self, title: str, days_ahead: int, hours: int = 2, **extra: Any) -> Event:
        start = self._evening(days_ahead)
        if "location" in extra and "coordinates" not in extra:
            extra["coordinates"] = place(extra["location"])
        return Event(
            title=title,
            starts_at=start,
            ends_at=start + timedelta(hours=hours),
            is_demo=True,
            provenance=self._staff(),
            **extra,
        )

    def _published(
        self, title: str, days_ahead: int, host_id: str, by_id: str, **extra: Any
    ) -> Event:
        event = self.g.events.create(self._event(title, days_ahead, **extra), host_id, by_id)
        return self.g.events.publish(event.id, by_id, self.now)

    def _weekly_series(self, org_id: str) -> EventSeries:
        return EventSeries(
            owner_org_id=org_id,
            name="Weekly chapter meeting",
            recurrence="Weekly",
            default_title="Chapter meeting",
            default_description="Weekly business meeting: committee updates, votes and "
            "planning for upcoming events.",
            default_location="Student Center, Room 204",
            default_coordinates=place("Student Center, Room 204"),
            default_duration=timedelta(hours=1),
            is_demo=True,
            provenance=self._staff(),
        )
