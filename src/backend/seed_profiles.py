"""Fictional demo data for v1.1: sign-in emails, profiles, and the people
the spec's checks need (v1.1 spec 4.1).

- Every hand-built person and demo student gets a fictional sign-in email
  `<name>.demo@northeastern.edu` (the access gate needs a Northeastern
  address). Nothing here describes a real person, chapter or club.
- Alice, Bob and Carol: Alice-Bob and Bob-Carol are connected, Alice and
  Carol are not, so Carol is Alice's 2nd degree.
- Rowan has more than 100 connections, to test the network's render cap.
- Pat is the platform admin (Collectivize staff) for /admin.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from backend.graph import CollectivizeGraph
from backend.objects import Person, ProfileTheme, Top3

EMAIL_DOMAIN = "northeastern.edu"
HUB_CONNECTIONS = 110
PLATFORM_ADMIN = "pat"

PROFILES = {
    "alice": ("Pottery on weekends, cold brew on weekdays.",
              Top3(title="study spots", items=("Library 4th floor", "Corner café", "The quad")),
              ProfileTheme(outline="teal", background="mint")),
    "bob": ("Surfer, sometimes before sunrise.",
            Top3(title="breakfast spots", items=("Diner on 5th", "Bagel cart", "Dining hall")),
            ProfileTheme(outline="cobalt", background="sky")),
    "carol": ("Building small robots and bigger playlists.",
              Top3(title="hobbies", items=("Robotics", "Bouldering", "Making playlists")),
              ProfileTheme(outline="plum", background="blush")),
    "drew": ("Chapter officer. Always down for a study jam.",
             Top3(title="study spots", items=("Library, Room 3", "Student Center", "Anywhere quiet")),
             ProfileTheme(outline="coral", background="white")),
    "avery": ("Running the chapter calendar this year.",
              None, ProfileTheme()),
    "rowan": ("Says hi to everyone. Knows everyone.",
              Top3(title="campus events", items=("Demo day", "Food crawl", "Fall mixer")),
              ProfileTheme(outline="ink", background="paper")),
}


def email_for(key: str) -> str:
    return f"{key}.demo@{EMAIL_DOMAIN}"


def seed_profiles(
    graph: CollectivizeGraph,
    orgs: dict[str, str],
    people: dict[str, str],
    admins: dict[str, str],
    now: datetime,
    make_person: Callable[[str], Person],
) -> dict[str, str]:
    """Add emails and profiles to `people`, create the v1.1 demo people,
    and return the new people's ids by key."""
    org_admin = {org: people[person] for person, org in admins.items()}
    new = {
        "alice": "Alice (demo student)",
        "bob": "Bob (demo student)",
        "carol": "Carol (demo student)",
        "rowan": "Rowan (demo student)",
        PLATFORM_ADMIN: "Pat (demo platform admin)",
    }
    added = {key: graph.store.save_node(make_person(name)).id for key, name in new.items()}
    everyone = people | added

    connect = _connector(graph, now)
    connect(added["alice"], added["bob"])
    connect(added["bob"], added["carol"])
    connect(added["alice"], everyone["drew"])
    for person, org in (("alice", "cooking"), ("bob", "surf"), ("carol", "robotics")):
        edge = graph.membership.request(added[person], orgs[org], now)
        graph.membership.confirm(edge.id, org_admin[org])

    # Rowan: connected to (almost) everyone, plus extra demo students as needed.
    skip = {added[k] for k in ("alice", "bob", "carol", "rowan", PLATFORM_ADMIN)}
    friends = [pid for pid in sorted(everyone.values()) if pid not in skip]
    guests = {}
    for n in range(1, max(0, HUB_CONNECTIONS - len(friends)) + 1):
        guest = graph.store.save_node(make_person(f"Guest {n:03d} (demo student)"))
        guests[f"guest{n:03d}"] = guest.id
        friends.append(guest.id)
    for pid in friends[:HUB_CONNECTIONS]:
        connect(added["rowan"], pid)

    for key, pid in (everyone | guests).items():
        person = graph.store.node(pid, Person)
        bio, top3, theme = PROFILES.get(key, ("", None, ProfileTheme()))
        graph.store.save_node(replace(
            person, email=email_for(key), bio=bio, top3=top3, theme=theme,
            is_platform_admin=key == PLATFORM_ADMIN,
        ))
    return added | guests


def _connector(graph: CollectivizeGraph, now: datetime) -> Callable[[str, str], None]:
    def connect(a: str, b: str) -> None:
        if graph.connections.between(a, b) is None:
            request = graph.connections.request(a, b, now)
            graph.connections.accept(request.id, b)
    return connect
