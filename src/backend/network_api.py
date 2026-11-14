"""JSON for the Network and Profile pages: the map around one node, the
profile of whoever is in the middle, and saving your own profile.

Everything here is built for one viewer from what `NetworkService` and the
other graph services already let that viewer see. Nothing is sent about
nodes the viewer can't open.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.graph import CollectivizeGraph, ProfileAccess
from backend.graph.network import NetworkMap, NodeKind
from backend.objects import (
    Event,
    EventStatus,
    Organization,
    VerificationLevel,
    Visibility,
)
from backend.objects.person import MAX_ABOUT

MAX_UPCOMING = 3  # events listed on an org's profile
SAFE_LINK_PREFIXES = ("https://", "http://")


def network_json(
    graph: CollectivizeGraph, viewer: str, center: str | None, now: datetime
) -> dict[str, Any]:
    """The answer to GET /api/network: the map and the center's profile."""
    view = graph.network.node_map(viewer, center)
    return {**_map_json(view), "profile": _profile_json(graph, viewer, view, now)}


def clean_name(name: str) -> str:
    """A demo name without its "[Demo]" prefix or "(demo role)" suffix."""
    return name.removeprefix("[Demo] ").split(" (demo", 1)[0]


def _map_json(view: NetworkMap) -> dict[str, Any]:
    return {
        "center": {
            "id": view.center.id,
            "name": clean_name(view.center.label),
            "kind": "org" if view.center.kind is NodeKind.ORG else "person",
        },
        "is_self": view.is_self,
        "orgs": [
            {"id": o.id, "name": clean_name(o.name), "closeness": o.closeness} for o in view.orgs
        ],
        "people": [
            {"id": p.id, "name": clean_name(p.name), "is_you": p.is_viewer, "closeness": p.closeness}
            for p in view.people
        ],
        "links": [list(pair) for pair in view.links],
        "near": [list(pair) for pair in view.near],
        "truncated": view.truncated,
    }


def _profile_json(
    graph: CollectivizeGraph, viewer: str, view: NetworkMap, now: datetime
) -> dict[str, Any]:
    if view.center.kind is NodeKind.ORG:
        return _org_profile(graph, viewer, graph.store.node(view.center.id, Organization), now)
    return person_profile_json(graph, viewer, view.center.id, now)


def person_profile_json(
    graph: CollectivizeGraph, viewer: str, person_id: str, now: datetime
) -> dict[str, Any]:
    """A person's profile page as the viewer may read it (the shape
    docs/profile-backend-proposal.md describes, without usernames). Never
    the email. `settings` is only included on your own profile."""
    view = graph.profiles.view(person_id, viewer, now)
    person = view.person
    name = person.display_name
    full = view.access is ProfileAccess.FULL
    profile: dict[str, Any] = {
        "kind": "person",
        "name": clean_name(name),
        "role": name.split("(demo ", 1)[1].rstrip(")") if "(demo " in name else "",
        "verified": person.verification_level is not VerificationLevel.NONE,
        "colors": {"background": person.color_background, "outline": person.color_outline},
        "access": view.access.value,
        "connection": view.connection,
        "is_self": view.is_self,
        "in_common": None if view.is_self else _in_common(graph, viewer, person.id),
        "bio": person.bio if full else "",
        "about": person.profile[:MAX_ABOUT] if full else "",
        "top3": (
            {"title": person.top3_title, "items": list(person.top3_items)}
            if full and person.top3_title else None
        ),
        "website": person.website if full else None,
        "sections": list(person.profile_sections),
        "orgs": [{"id": o.id, "name": clean_name(o.name)} for o in view.orgs],
        "hosting": [_event_json(graph, e) for e in view.hosting],
        "going": [_event_json(graph, e) for e in view.going],
    }
    if view.is_self:
        profile["settings"] = {
            "profile_visibility": AUDIENCE_NAMES[person.profile_visibility],
            "rsvp_visibility": AUDIENCE_NAMES[person.rsvp_visibility],
            "hide_connections": person.hide_connections,
        }
    return profile


def _in_common(graph: CollectivizeGraph, viewer: str, person_id: str) -> str | None:
    """E.g. "2 mutual connections · Surf Club", or None when nothing is shared."""
    shared = graph.network.in_common(viewer, person_id)
    parts = []
    if shared.people:
        count = len(shared.people)
        parts.append(f"{count} mutual connection{'s' if count != 1 else ''}")
    parts.extend(clean_name(org.name) for org in shared.orgs)
    return " · ".join(parts) or None


def _event_json(graph: CollectivizeGraph, event: Event) -> dict[str, Any]:
    host = graph.events.host_node(graph.events.primary_host_id(event.id))
    is_org = isinstance(host, Organization)
    return {
        "id": event.id,
        "title": event.title,
        "starts_at": event.starts_at.isoformat(),
        "ends_at": event.ends_at.isoformat(),
        "location": event.location,
        "host": {
            "name": clean_name(host.name if is_org else host.display_name),
            "kind": "org" if is_org else "person",
        },
        "is_cancelled": event.status is EventStatus.CANCELLED,
    }


# --- saving your own profile ---------------------------------------------------------

AUDIENCES = {
    "network": Visibility.NETWORK,
    "connections": Visibility.CONNECTIONS,
    "private": Visibility.PRIVATE,
}


AUDIENCE_NAMES = {level: name for name, level in AUDIENCES.items()}


def profile_changes(body: dict[str, Any]) -> dict[str, Any]:
    """Turn a POST /api/profile body into `ProfileService.update` changes.
    Only keys that are present are changed. Raises ValueError (with a
    message safe to show) for anything malformed; the limits themselves
    are checked by the Person record."""
    changes: dict[str, Any] = {}
    for key in ("display_name", "bio", "about"):
        if key in body:
            changes[key] = _string(body[key], key).strip()
    if "website" in body:
        website = body["website"]
        changes["website"] = None if website in (None, "") else _string(website, "website").strip()
    if "top3" in body:
        top3 = body["top3"]
        if top3 is None:
            changes.update(top3_title="", top3_items=())
        else:
            if not isinstance(top3, dict) or not isinstance(top3.get("items"), list):
                raise ValueError("top3 must be {title, items} or null")
            changes["top3_title"] = _string(top3.get("title"), "top3 title").strip()
            changes["top3_items"] = tuple(_string(i, "top3 item").strip() for i in top3["items"])
    if "colors" in body:
        colors = body["colors"]
        if not isinstance(colors, dict):
            raise ValueError("colors must be {background, outline}")
        changes["color_background"] = _string(colors.get("background"), "background color").upper()
        changes["color_outline"] = _string(colors.get("outline"), "outline color").upper()
    if "sections" in body:
        if not isinstance(body["sections"], list):
            raise ValueError("sections must be a list")
        changes["profile_sections"] = tuple(_string(s, "section") for s in body["sections"])
    for key in ("profile_visibility", "rsvp_visibility"):
        if key in body:
            if not isinstance(body[key], str) or body[key] not in AUDIENCES:
                raise ValueError(f"{key} must be network, connections or private")
            changes[key] = AUDIENCES[body[key]]
    if "hide_connections" in body:
        if not isinstance(body["hide_connections"], bool):
            raise ValueError("hide_connections must be true or false")
        changes["hide_connections"] = body["hide_connections"]
    return changes


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")  # noqa: TRY004 (one error type for a bad body)
    return value


def _org_profile(
    graph: CollectivizeGraph, viewer: str, org: Organization, now: datetime
) -> dict[str, Any]:
    """An org's page header: what it is, its site, and what it has coming up
    (only events the viewer may see)."""
    listing = graph.calendar.host_events(org.id, viewer, starts_after=now)
    upcoming = [
        e for e in (*listing.public, *listing.private)
        if e.status is EventStatus.PUBLISHED and e.was_published
    ]
    upcoming.sort(key=lambda e: (e.starts_at, e.id))
    website = org.website_url or ""
    return {
        "kind": "org",
        "name": clean_name(org.name),
        "org_kind": org.kind.value,
        "website": website if website.startswith(SAFE_LINK_PREFIXES) else None,
        "upcoming_count": len(upcoming),
        "upcoming": [
            {
                "id": e.id,
                "title": e.title,
                "starts_at": e.starts_at.isoformat(),
                "location": e.location,
            }
            for e in upcoming[:MAX_UPCOMING]
        ],
    }
