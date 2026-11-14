"""The v1.1 API's behaviour, independent of HTTP (server.py wraps it):
sign-in, profiles, networks, connections, unified search, orgs, My Events
and the platform admin tools.

Every method takes the signed-in viewer's id from the server (never from
the request), and every privacy decision goes through
graph.visibility (backend/graph/visibility.py), so nothing a viewer may
not see is ever sent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from http import HTTPStatus
from typing import Any

from backend.config import ALLOWED_EMAIL_DOMAINS
from backend.demo_api import ApiError, GraphFactory
from backend.graph import CollectivizeGraph
from backend.graph.visibility import (
    NETWORK_ORG_CAP,
    NETWORK_PEOPLE_CAP,
    NetworkNode,
)
from backend.objects import (
    Connected,
    Organization,
    Person,
    Provenance,
    ProvenanceMethod,
    VerificationLevel,
)
from backend.objects.base import utc_now
from backend.objects.person import BACKGROUND_PRESETS, OUTLINE_PRESETS
from backend.objects.profile_image import PHOTO_SLOT, PICTURE_SLOTS, image_id

EMAIL_SHAPE = re.compile(r"^[a-z0-9._%+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)$")
MAX_EMAIL_LENGTH = 254
MAX_ID_LENGTH = 64
ADMIN_SEARCH_LIMIT = 20
MAX_NETWORK_NODES = 500


class AppApi:
    def __init__(
        self, graphs: GraphFactory, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._graphs = graphs
        self._clock = clock

    # --- sign-in (spec 4.10) -----------------------------------------------------

    def sign_in(self, body: dict[str, Any]) -> str:
        """The person id for a Northeastern email. A new address gets a new,
        empty account. Demo only: there is no email verification yet."""
        email = body.get("email")
        if not isinstance(email, str) or len(email) > MAX_EMAIL_LENGTH:
            raise ApiError(HTTPStatus.BAD_REQUEST, "enter your Northeastern email")
        address = email.strip().lower()
        match = EMAIL_SHAPE.match(address)
        if match is None:
            raise ApiError(HTTPStatus.BAD_REQUEST, "enter a valid email address")
        if match.group(1) not in ALLOWED_EMAIL_DOMAINS:
            raise ApiError(
                HTTPStatus.FORBIDDEN,
                "Collectivize is campus-only for now: sign in with your Northeastern "
                f"email (@{' or @'.join(ALLOWED_EMAIL_DOMAINS)})",
            )
        with self._graphs() as graph:
            for person in graph.store.nodes(Person):
                if person.email == address:
                    return person.id
            # SPEC-ASSUMPTION: a valid address with no account creates one.
            person = graph.store.save_node(Person(
                display_name=_name_from_email(address),
                email=address,
                verification_level=VerificationLevel.UNIVERSITY_EMAIL,
                provenance=Provenance(
                    created_by=None, method=ProvenanceMethod.SELF_CLAIM, created_at=self._clock()
                ),
            ))
            return person.id

    def me(self, person_id: str, view_as: str | None) -> dict[str, Any]:
        with self._graphs() as graph:
            person = _person(graph, person_id)
            viewing = _person(graph, view_as) if view_as else None
            return {
                "id": person.id,
                "name": person.display_name,
                "is_platform_admin": person.is_platform_admin,
                "viewing_as": None if viewing is None else {
                    "id": viewing.id, "name": viewing.display_name,
                },
            }

    # --- platform admin (spec 4.9) -------------------------------------------------

    def require_platform_admin(self, person_id: str) -> None:
        with self._graphs() as graph:
            if not graph.visibility.is_platform_admin(person_id):
                raise ApiError(HTTPStatus.FORBIDDEN, "only platform admins can do that")

    def admin_users(self, admin_id: str, params: dict[str, str]) -> dict[str, Any]:
        """Search every user by name or email (platform admins only)."""
        self.require_platform_admin(admin_id)
        needle = params.get("q", "").strip().casefold()[:100]
        with self._graphs() as graph:
            people = [
                p for p in graph.store.nodes(Person)
                if not needle or needle in p.display_name.casefold()
                or needle in (p.email or "")
            ]
            people.sort(key=lambda p: p.display_name.casefold())
            return {
                "total": len(people),
                "users": [
                    {"id": p.id, "name": p.display_name, "email": p.email,
                     "connections": len(graph.connections.connections_of(p.id)),
                     "orgs": len(graph.visibility.org_ids_of(p.id))}
                    for p in people[:ADMIN_SEARCH_LIMIT]
                ],
            }

    def check_view_as_target(self, admin_id: str, target: Any) -> str | None:
        self.require_platform_admin(admin_id)
        if target is None:
            return None
        with self._graphs() as graph:
            return _person(graph, _id(target, "person")).id

    # --- connections and networks (spec 4.2) --------------------------------------

    def network(self, viewer: str, params: dict[str, str]) -> dict[str, Any]:
        """A network as the viewer may see it: their own, or a 1st-degree
        connection's. `people` / `orgs` raise the caps for "Show more"."""
        people = _bounded(params.get("people"), NETWORK_PEOPLE_CAP, MAX_NETWORK_NODES)
        orgs = _bounded(params.get("orgs"), NETWORK_ORG_CAP, MAX_NETWORK_NODES)
        with self._graphs() as graph:
            subject = _person(graph, _id(params.get("id") or viewer, "person"))
            view = graph.visibility.visible_network(
                viewer, subject.id, people_limit=people, org_limit=orgs
            )
            if view is None:
                return {"visible": False, "id": subject.id}
            node = lambda n: _node_json(graph, n)  # noqa: E731
            return {
                "visible": True,
                "id": subject.id,
                "is_own": subject.id == viewer,
                "center": node(view.center),
                "people": [node(n) for n in view.people],
                "orgs": [node(n) for n in view.orgs],
                "links": [list(link) for link in view.links],
                "people_total": view.people_total,
                "orgs_total": view.orgs_total,
                "connections_hidden": view.connections_hidden,
            }

    def connect(self, viewer: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send a connection request. Anyone can ask anyone (spec 4.2 rule 1)."""
        with self._graphs() as graph:
            other = _person(graph, _id(body.get("person_id"), "person"))
            state = graph.visibility.connection_state(viewer, other.id).value
            if state == "pending_in":  # they already asked: asking back accepts
                return self._answer(graph, viewer, other.id, "accept")
            if state == "none":
                graph.connections.request(viewer, other.id, self._clock())
            return {"person_id": other.id,
                    "connection": graph.visibility.connection_state(viewer, other.id).value}

    def connection(self, viewer: str, body: dict[str, Any]) -> dict[str, Any]:
        """Answer or undo: `accept` or `decline` a request to you, `cancel`
        your own request, or `remove` a connection."""
        action = body.get("action")
        if action not in ("accept", "decline", "cancel", "remove"):
            raise ApiError(HTTPStatus.BAD_REQUEST, "action must be accept, decline, cancel or remove")
        with self._graphs() as graph:
            other = _person(graph, _id(body.get("person_id"), "person"))
            return self._answer(graph, viewer, other.id, action)

    def _answer(
        self, graph: CollectivizeGraph, viewer: str, other_id: str, action: str
    ) -> dict[str, Any]:
        edge = graph.connections.between(viewer, other_id)
        state = graph.visibility.connection_state(viewer, other_id).value
        allowed = {"accept": "pending_in", "decline": "pending_in",
                   "cancel": "pending_out", "remove": "connected"}
        if edge is None or state != allowed[action]:
            raise ApiError(HTTPStatus.BAD_REQUEST, "there's no request or connection to change")
        if action == "accept":
            graph.connections.accept(edge.id, viewer)
        else:  # declined, cancelled and removed connections leave no record
            graph.connections.remove(edge.id, viewer)
        other = graph.store.node(other_id, Person)
        result: dict[str, Any] = {
            "person_id": other_id,
            "connection": graph.visibility.connection_state(viewer, other_id).value,
        }
        if action == "accept":
            result["notice"] = (
                f"You're connected. Your connections will be able to see that you're "
                f"connected to {other.display_name}."
            )
        return result

    def requests(self, viewer: str) -> dict[str, Any]:
        """Connection requests waiting for the viewer, each with the sender's
        profile card (the preview, spec 4.11), and requests they've sent."""
        with self._graphs() as graph:
            incoming = graph.store.edges(Connected, target_id=viewer)
            outgoing = graph.store.edges(Connected, source_id=viewer)
            return {
                "incoming": [
                    self._card(graph, viewer, e.source_id, preview=True)
                    for e in incoming if not e.is_established
                ],
                "outgoing": [
                    _person_ref(graph, e.target_id, viewer)
                    for e in outgoing if not e.is_established
                ],
            }

    def _card(
        self, graph: CollectivizeGraph, viewer: str | None, subject_id: str, *, preview: bool = False
    ) -> dict[str, Any]:
        """The profile card (the top of a profile, and the preview on a
        request or invite), cut down to what the viewer may see."""
        person = _person(graph, subject_id)
        if viewer is None:  # invite landing page: the inviter's preview card
            sections = graph.visibility.visible_profile_sections(subject_id, subject_id, preview=True)
            connection = "none"
        else:
            sections = graph.visibility.visible_profile_sections(viewer, subject_id, preview=preview)
            connection = graph.visibility.connection_state(viewer, subject_id).value
        theme = person.theme
        return {
            "id": person.id,
            "name": person.display_name,
            "bio": person.bio,
            "top3": None if person.top3 is None else {
                "title": person.top3.title, "items": list(person.top3.items),
            },
            "theme": {
                "outline": theme.outline,
                "background": theme.background,
                "outline_hex": OUTLINE_PRESETS[theme.outline],
                "background_hex": BACKGROUND_PRESETS[theme.background],
            },
            "has_photo": _has_photo(graph, person.id),
            "pictures": [
                slot for slot in PICTURE_SLOTS
                if graph.store.has_node(image_id(person.id, slot))
            ] if sections.pictures else [],
            "card": sections.card.value,
            "connection": connection,
        }

    # --- organizations (spec 4.9 org admins, 7.1 member lists) ---------------------

    def org(self, viewer: str, params: dict[str, str]) -> dict[str, Any]:
        with self._graphs() as graph:
            org = _org(graph, _id(params.get("id"), "organization"))
            members = graph.visibility.visible_members(viewer, org.id)
            return {
                "id": org.id,
                "name": org.name,
                "kind": org.kind.value,
                "website_url": org.website_url,
                "tags": _org_tags(graph, org.id),
                "is_member": graph.membership.is_member(viewer, org.id),
                "can_manage": graph.visibility.can_manage_org(viewer, org.id),
                "member_count": members.count,
                "members_complete": members.is_full,
                "members": [
                    _person_ref(graph, pid, viewer, admin=graph.membership.is_admin(pid, org.id))
                    for pid in members.member_ids
                ],
                "events": _org_events(graph, org.id, viewer, self._clock()),
            }

    def update_org(self, viewer: str, body: dict[str, Any]) -> dict[str, Any]:
        """Org admins edit their org's page: its name and website."""
        with self._graphs() as graph:
            org = _org(graph, _id(body.get("id"), "organization"))
            if not graph.visibility.can_manage_org(viewer, org.id):
                raise ApiError(HTTPStatus.FORBIDDEN, "only this org's admins can edit it")
            changes: dict[str, Any] = {}
            if "name" in body:
                name = body["name"]
                if not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "the name needs 1-80 characters")
                changes["name"] = " ".join(name.split())
            if "website_url" in body:
                url = body["website_url"]
                if url is not None and not isinstance(url, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "the website must be a link")
                changes["website_url"] = (url or "").strip() or None
            updated = replace(org, **changes)
            updated = replace(updated, provenance=updated.provenance.confirmed(self._clock()))
            graph.store.save_node(updated)
        return self.org(viewer, {"id": org.id})

    def org_members(self, viewer: str, body: dict[str, Any]) -> dict[str, Any]:
        """Org admins add (`add`, by `email` or `person_id`) or remove
        (`remove`, by `person_id`) members of their own org only."""
        action = body.get("action")
        if action not in ("add", "remove"):
            raise ApiError(HTTPStatus.BAD_REQUEST, "action must be add or remove")
        with self._graphs() as graph:
            org = _org(graph, _id(body.get("org_id"), "organization"))
            if not graph.visibility.can_manage_org(viewer, org.id):
                raise ApiError(HTTPStatus.FORBIDDEN, "only this org's admins can change its members")
            if action == "add" and isinstance(body.get("email"), str):
                person = _person_by_email(graph, body["email"])
            else:
                person = _person(graph, _id(body.get("person_id"), "person"))
            now = self._clock()
            current = graph.membership.current(person.id, org.id)
            with graph.store.transaction():
                if action == "add":
                    if current is not None and current.org_confirmed:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "they're already a member")
                    edge = current or graph.membership.request(person.id, org.id, now)
                    graph.membership.confirm(edge.id, viewer)
                else:
                    if current is None:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "they aren't a member")
                    if current.is_admin and len(graph.membership.admins(org.id)) == 1:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "an org must keep at least one admin")
                    graph.store.save_edge(current.ended(now))
        return self.org(viewer, {"id": org.id})


# --- helpers ---------------------------------------------------------------------------


def _name_from_email(address: str) -> str:
    local = address.split("@", 1)[0]
    words = [w for w in re.split(r"[._+-]+", local) if w and not w.isdigit()]
    return " ".join(w.capitalize() for w in words) or local


def _id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_ID_LENGTH:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown {what}")
    return value


def _person(graph: CollectivizeGraph, person_id: str) -> Person:
    if not graph.store.has_node(person_id):
        raise ApiError(HTTPStatus.NOT_FOUND, "that person isn't on Collectivize")
    node = graph.store.any_node(person_id)
    if not isinstance(node, Person):
        raise ApiError(HTTPStatus.NOT_FOUND, "that person isn't on Collectivize")
    return node


def _bounded(value: str | None, default: int, high: int) -> int:
    if value is None or value == "":
        return default
    if not value.isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, "node counts must be whole numbers")
    return max(1, min(int(value), high))


def _node_json(graph: CollectivizeGraph, node: NetworkNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind,
        "label": node.label,
        "is_center": node.is_center,
        "is_viewer": node.is_viewer,
        "viewer_connected": node.viewer_connected,
        "connection": node.connection.value,
        "has_photo": node.kind == "person" and _has_photo(graph, node.id),
    }


def _person_by_email(graph: CollectivizeGraph, email: str) -> Person:
    address = email.strip().lower()[:MAX_EMAIL_LENGTH]
    for person in graph.store.nodes(Person):
        if address and person.email == address:
            return person
    raise ApiError(HTTPStatus.NOT_FOUND, "no one has signed up with that email yet")


def _org(graph: CollectivizeGraph, org_id: str) -> Organization:
    if not graph.store.has_node(org_id):
        raise ApiError(HTTPStatus.NOT_FOUND, "that organization isn't on Collectivize")
    node = graph.store.any_node(org_id)
    if not isinstance(node, Organization):
        raise ApiError(HTTPStatus.NOT_FOUND, "that organization isn't on Collectivize")
    return node


def _person_ref(
    graph: CollectivizeGraph, person_id: str, viewer: str, **extra: Any
) -> dict[str, Any]:
    person = graph.store.node(person_id, Person)
    return {
        "id": person.id,
        "name": person.display_name,
        "has_photo": _has_photo(graph, person.id),
        "connection": graph.visibility.connection_state(viewer, person.id).value,
        **extra,
    }


def _has_photo(graph: CollectivizeGraph, person_id: str) -> bool:
    return graph.store.has_node(image_id(person_id, PHOTO_SLOT))


def _org_tags(graph: CollectivizeGraph, org_id: str) -> list[str]:
    from backend.objects import Tag, Tagged

    labels = []
    for edge in graph.store.edges(Tagged, source_id=org_id):
        node = graph.store.any_node(edge.target_id)
        if isinstance(node, Tag):
            labels.append(node.label)
    return sorted(labels)


def _org_events(
    graph: CollectivizeGraph, org_id: str, viewer: str, now: datetime
) -> list[dict[str, Any]]:
    from backend.graph import DiscoveryQuery

    page = graph.discovery.search(
        DiscoveryQuery(viewer_id=viewer, org_id=org_id, include_related=False, limit=20), now
    )
    items = sorted(page.items, key=lambda i: i.event.starts_at)
    return [
        {"id": i.event.id, "title": i.event.title, "starts_at": i.event.starts_at.isoformat(),
         "location": i.event.location, "is_cancelled": i.is_cancelled}
        for i in items
    ]
