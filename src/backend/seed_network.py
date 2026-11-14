"""The fictional demo social network: students, memberships, friendships
and RSVPs.

Dense enough that the network graph looks alive, clustered so friends
mostly share orgs, and deterministic (a fixed random seed) so the demo is
the same every time. Every link goes through the real services, so each
membership is confirmed by an org admin and each friendship is accepted
by both people, and each RSVP follows the RSVP rules.

RSVPs cluster the way real ones do: members tend to go to their own org's
events, and people whose friends are going are more likely to go too, so
"N friends going" shows real numbers for the demo personas.

Tastes and travel: students pick a couple of interests, and each RSVP
"going" passes a made-up position near campus (as the app would from a
phone) so travel habits are learned the same way as for real users. The
positions exist only while seeding and are never stored.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime

from backend.graph import CollectivizeGraph
from backend.objects import (
    DomainRuleError,
    Event,
    EventStatus,
    GeoPoint,
    Person,
    RsvpStatus,
)

RANDOM_SEED = 2026
STUDENT_NAMES = (
    "Kai", "Logan", "Morgan", "Noor", "Oakley", "Parker", "Quinn", "Riley",
    "Sage", "Taylor", "Uma", "Val", "Wren", "Xan", "Yael", "Zion",
    "Ari", "Blair", "Cam", "Dev", "Eli", "Fern", "Gale", "Hollis",
    "Ira", "Jules", "Kit", "Lane", "Marlow", "Nico", "Onyx", "Peyton",
)
HIDDEN_CONNECTIONS = ("quinn",)  # shows off the "hide my connections" setting
NO_ANALYTICS = ("quinn", "kai", "wren")  # most demo students opt in to analytics; these don't
MIN_FRIENDS, MAX_FRIENDS = 3, 6
SAME_ORG_FRIEND_CHANCE = 0.75
MAX_ATTEMPTS_PER_PERSON = 40
MEMBER_GOES_CHANCE = 0.55  # to an event one of your orgs hosts
FRIEND_GOES_CHANCE = 0.30  # to an event a friend is going to
ANYONE_GOES_CHANCE = 0.04
GOING_SHARE = 0.85  # the rest say "interested"
MAX_INTERESTS = 2
HOME_SPREAD_DEGREES = 0.025  # where people are when they RSVP: within ~2-3 km


def seed_network(
    graph: CollectivizeGraph,
    orgs: dict[str, str],
    people: dict[str, str],
    admins: dict[str, str],
    now: datetime,
    make_person: Callable[[str], Person],
    event_ids: Iterable[str] = (),
    tag_ids: Iterable[str] = (),
    campus: GeoPoint | None = None,
) -> dict[str, str]:
    """Add students, their memberships, friendships and RSVPs to
    `event_ids`. `admins` maps a person key to the org key they
    administer. Returns the student ids."""
    rng = random.Random(RANDOM_SEED)
    students = {
        name.lower(): graph.store.save_node(make_person(f"{name} (demo student)")).id
        for name in STUDENT_NAMES
    }
    for key, person_id in students.items():
        if key not in NO_ANALYTICS:
            person = graph.store.node(person_id, Person)
            graph.store.save_node(replace(person, analytics_consent=True))
    everyone = people | students
    org_admin = {org: everyone[person] for person, org in admins.items()}
    belongs = {key: {admins[key]} if key in admins else set() for key in everyone}
    belongs["drew"].add("chapter")

    for key, person_id in students.items():
        for org in rng.sample(sorted(org_admin), rng.randint(1, 3)):
            edge = graph.membership.request(person_id, orgs[org], now)
            graph.membership.confirm(edge.id, org_admin[org])
            belongs[key].add(org)

    _befriend(graph, rng, everyone, belongs, now)
    for key in HIDDEN_CONNECTIONS:
        graph.connections.set_hide_connections(students[key], students[key], True)
    member_of = {
        everyone[key]: {orgs[org] for org in org_keys} for key, org_keys in belongs.items()
    }
    tags = sorted(tag_ids)
    for person_id in students.values():
        for tag in rng.sample(tags, rng.randint(0, min(MAX_INTERESTS, len(tags)))):
            graph.preferences.add_interest(person_id, tag, person_id, now)
    whereabouts = {
        person_id: _nearby(rng, campus) for person_id in sorted(everyone.values())
    } if campus else {}
    _rsvp(graph, rng, sorted(everyone.values()), member_of, event_ids, now, whereabouts)
    return students


def _nearby(rng: random.Random, campus: GeoPoint) -> GeoPoint:
    return GeoPoint(
        campus.latitude + rng.uniform(-HOME_SPREAD_DEGREES, HOME_SPREAD_DEGREES),
        campus.longitude + rng.uniform(-HOME_SPREAD_DEGREES, HOME_SPREAD_DEGREES),
    )


def _befriend(
    graph: CollectivizeGraph,
    rng: random.Random,
    everyone: dict[str, str],
    belongs: dict[str, set[str]],
    now: datetime,
) -> None:
    keys = sorted(everyone)
    for key in keys:
        wanted = rng.randint(MIN_FRIENDS, MAX_FRIENDS)
        same_org = [k for k in keys if k != key and belongs[k] & belongs[key]]
        for _ in range(MAX_ATTEMPTS_PER_PERSON):
            if len(graph.connections.connections_of(everyone[key])) >= wanted:
                break
            pool = same_org if same_org and rng.random() < SAME_ORG_FRIEND_CHANCE else keys
            other = rng.choice(pool)
            a, b = everyone[key], everyone[other]
            if other == key or graph.connections.between(a, b) is not None:
                continue
            request = graph.connections.request(a, b, now)
            graph.connections.accept(request.id, b)


def _rsvp(
    graph: CollectivizeGraph,
    rng: random.Random,
    person_ids: list[str],
    member_of: dict[str, set[str]],
    event_ids: Iterable[str],
    now: datetime,
    whereabouts: dict[str, GeoPoint],
) -> None:
    """Two passes: members of host orgs first, then friends of people going."""
    events = [graph.store.node(i, Event) for i in sorted(event_ids)]
    open_events = [e for e in events if e.status_at(now) is EventStatus.PUBLISHED]
    for event in open_events:
        hosts = graph.events.host_ids(event.id)
        for person_id in person_ids:
            if member_of[person_id] & hosts and rng.random() < MEMBER_GOES_CHANCE:
                _respond(graph, rng, event, person_id, now, whereabouts.get(person_id))
    for event in open_events:
        for person_id in person_ids:
            if graph.rsvps.mine(event.id, person_id) is not None:
                continue
            has_friend = bool(graph.rsvps.friends_going(person_id, event.id))
            chance = FRIEND_GOES_CHANCE if has_friend else ANYONE_GOES_CHANCE
            if rng.random() < chance:
                _respond(graph, rng, event, person_id, now, whereabouts.get(person_id))


def _respond(
    graph: CollectivizeGraph,
    rng: random.Random,
    event: Event,
    person_id: str,
    now: datetime,
    origin: GeoPoint | None,
) -> None:
    status = RsvpStatus.GOING if rng.random() < GOING_SHARE else RsvpStatus.INTERESTED
    try:
        graph.rsvps.respond(event.id, person_id, status, now, origin=origin)
    except DomainRuleError:  # full: say interested instead
        graph.rsvps.respond(event.id, person_id, RsvpStatus.INTERESTED, now)
