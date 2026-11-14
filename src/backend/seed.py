"""Fictional mock data for the test database.

Every org name starts with "[Demo]", every person is labelled "(demo ...)",
and every event has `is_demo=True`, so mock records can never be mistaken
for real ones. Seeding refuses to run outside the test environment.

All writes go through the normal services, so the mock data obeys every
graph rule (claims, admin checks, hierarchy kinds, co-host acceptance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from backend.config import Environment
from backend.graph import CollectivizeGraph
from backend.objects import (
    DomainRuleError,
    Event,
    EventSeries,
    MemberRole,
    Organization,
    OrgKind,
    PartOfKind,
    Person,
    Provenance,
    ProvenanceMethod,
    Tag,
    VerificationLevel,
)

SEED_STAFF_ID = "seed-staff"
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
}
PEOPLE = {
    "avery": "Avery (demo chapter admin)",
    "blake": "Blake (demo national admin)",
    "casey": "Casey (demo club admin)",
    "drew": "Drew (demo student)",
    "emery": "Emery (demo student)",
}
ADMINS = {"avery": "chapter", "blake": "national", "casey": "robotics"}
HIERARCHY = [
    ("chapter", "national", PartOfKind.CHAPTER_OF),
    ("chapter", "ifc", PartOfKind.MEMBER_OF_COUNCIL),
    ("ifc", "university", PartOfKind.UNDER_INSTITUTION),
    ("robotics", "university", PartOfKind.UNDER_INSTITUTION),
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


def seed_mock_data(
    graph: CollectivizeGraph, environment: Environment, now: datetime
) -> SeedResult:
    if environment is not Environment.TEST:
        raise ProductionSeedError("refusing to seed mock data outside the test database")
    with graph.store.transaction():
        return _MockWorld(graph, now).build()


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
        return SeedResult(orgs=orgs, people=people, tags=tags, events=events)

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
                                     hours=3, location="North Field")
        invite = ev.invite_co_host(fundraiser.id, orgs["national"], avery, self.now)
        ev.accept_co_host(invite.id, blake)
        ev.tag(fundraiser.id, tags["philanthropy"], avery, self.now)
        events["fundraiser"] = fundraiser.id

        demo_day = self._published("Robot demo day", 5, orgs["robotics"], casey,
                                   location="Engineering Hall", capacity=60)
        ev.tag(demo_day.id, tags["robotics"], casey, self.now)
        events["demo_day"] = demo_day.id
        # A request to join, left waiting for the club's admin to answer.
        emery = people["emery"]
        self.g.co_hosting.request(demo_day.id, emery, emery, self.now)

        mixer = self._published("Fall mixer", 3, orgs["chapter"], avery)
        events["cancelled_mixer"] = ev.cancel(mixer.id, avery).id

        draft = ev.create(self._event("Formal", 30), orgs["chapter"], avery)
        events["draft_formal"] = draft.id

        # A combined event: a student hosts it, invites the Robotics Club,
        # and the club's admin accepts, so it shows on both calendars.
        drew = people["drew"]
        study_jam = self._published("Robotics study jam", 4, drew, drew,
                                    location="Library, Room 3")
        invite = ev.invite_co_host(study_jam.id, orgs["robotics"], drew, self.now)
        ev.accept_co_host(invite.id, casey)
        events["study_jam"] = study_jam.id

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
            default_location="Student Center, Room 204",
            default_duration=timedelta(hours=1),
            is_demo=True,
            provenance=self._staff(),
        )
