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
from datetime import date, datetime
from http import HTTPStatus
from typing import Any

from backend.config import ALLOWED_EMAIL_DOMAINS
from backend.demo_api import ApiError, GraphFactory, _datetime_param, _entry_json, _item_json
from backend.graph.my_calendar import Relation
from backend.graph import CollectivizeGraph
from backend.graph.query_understanding import QueryReader
from backend.graph.unified_search import PER_PAGE as SEARCH_PER_PAGE
from backend.graph.unified_search import SearchFilters, SearchResult, UnifiedSearchService
from backend.graph.visibility import (
    NETWORK_ORG_CAP,
    NETWORK_PEOPLE_CAP,
    NetworkNode,
)
from backend.objects import (
    Connected,
    DomainRuleError,
    Event,
    EventStatus,
    Hosts,
    Organization,
    ProfileImage,
    ProfileTheme,
    Rsvp,
    RsvpStatus,
    Tag,
    Tagged,
    Top3,
    Person,
    Provenance,
    ProvenanceMethod,
    VerificationLevel,
)
from backend.objects.base import utc_now
from backend.objects.person import (
    BACKGROUND_PRESETS,
    BIO_MAX_CHARS,
    OUTLINE_PRESETS,
    TOP3_ITEM_MAX_CHARS,
    TOP3_TITLE_MAX_CHARS,
)
from backend.objects.profile_image import PHOTO_SLOT, PICTURE_SLOTS, image_id

EMAIL_SHAPE = re.compile(r"^[a-z0-9._%+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)$")
MAX_EMAIL_LENGTH = 254
MAX_INVITE_CODE = 100
MAX_ID_LENGTH = 64
ADMIN_SEARCH_LIMIT = 20
MAX_NETWORK_NODES = 500
MAX_NAME = 50
UPCOMING_PREVIEW = 5
MAX_QUERY = 200
SEARCH_TYPES = ("people", "orgs", "events")
PROFILE_EVENTS = 12  # per list, newest first


class AppApi:
    def __init__(
        self,
        graphs: GraphFactory,
        *,
        clock: Callable[[], datetime] = utc_now,
        reader: QueryReader | None = None,
    ) -> None:
        self._graphs = graphs
        self._clock = clock
        # Without an extractor, sentences are read locally (no AI).
        self._reader = reader or QueryReader()

    # --- unified search (spec 4.4 / 4.5) -------------------------------------------

    def find(self, viewer: str, params: dict[str, str], *, debug: bool = False) -> dict[str, Any]:
        """People, orgs and events in one ranked list: `q`, `types`
        (comma-separated people,orgs,events), `tags` (topic slugs), `date`
        (YYYY-MM-DD), `page`. Scores only with `debug` (platform admins)."""
        text = params.get("q", "")
        if len(text) > MAX_QUERY:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"keep searches under {MAX_QUERY} characters")
        types = _csv(params.get("types"), set(SEARCH_TYPES), "type")
        page = params.get("page", "1")
        if not page.isdigit() or not 1 <= int(page) <= 1000:
            raise ApiError(HTTPStatus.BAD_REQUEST, "page must be a whole number")
        day = None
        if params.get("date"):
            try:
                day = date.fromisoformat(params["date"])
            except ValueError:
                raise ApiError(HTTPStatus.BAD_REQUEST, "date must be YYYY-MM-DD") from None
        with self._graphs() as graph:
            slugs = {t.slug for t in graph.store.nodes(Tag)}
            tags = _csv(params.get("tags"), slugs, "topic")
            service = UnifiedSearchService(
                graph.store, graph.discovery, graph.visibility, graph.privacy, self._reader
            )
            result = service.search(
                viewer, text, SearchFilters(types=frozenset(types), tag_slugs=tuple(sorted(tags)), day=day),
                self._clock(), int(page),
            )
            starred = graph.stars.starred_event_ids(viewer)
            read = result.understanding.extraction
            return {
                "items": [_result_json(graph, viewer, r, starred, debug) for r in result.results],
                "total": result.total,
                "page": result.page,
                "pages": result.pages,
                "per_page": SEARCH_PER_PAGE,
                "counts": result.counts,
                "understood": {
                    "mode": result.understanding.mode,
                    "used_ai": result.understanding.used_ai,
                    "note": result.understanding.note,
                    "keywords": list(read.keywords),
                    "summary": read.summary,
                },
                "filters": {
                    "types": sorted(result.applied.types),
                    "tags": list(result.applied.tag_slugs),
                    "date": result.applied.day.isoformat() if result.applied.day else None,
                },
            }

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
        invite = body.get("invite")
        if invite is not None and (not isinstance(invite, str) or len(invite) > MAX_INVITE_CODE):
            invite = None
        with self._graphs() as graph:
            now = self._clock()
            for person in graph.store.nodes(Person):
                if person.email == address:
                    if invite:
                        self._accept_invite(graph, invite, person.id, now)
                    return person.id
            if invite:  # a new person joining by invite connects with the inviter
                try:
                    joined = graph.invites.redeem(invite, address, _name_from_email(address), now)
                    return joined.person.id
                except DomainRuleError:
                    pass  # an unusable invite: just create the account
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

    @staticmethod
    def _accept_invite(graph: CollectivizeGraph, code: str, person_id: str, now: datetime) -> None:
        try:
            graph.invites.accept_as_existing(code, person_id, now)
        except DomainRuleError:
            pass  # unusable or their own: signing in still works

    # --- invites (spec 4.11) -----------------------------------------------------

    def invite_preview(self, params: dict[str, str]) -> dict[str, Any]:
        """Public: the inviter's profile card for an invite link."""
        code = params.get("code", "")
        if not 0 < len(code) <= MAX_INVITE_CODE:
            raise ApiError(HTTPStatus.NOT_FOUND, "this invite link isn't valid any more")
        with self._graphs() as graph:
            try:
                inviter = graph.invites.inviter_of(code, self._clock())
            except DomainRuleError:
                raise ApiError(HTTPStatus.NOT_FOUND, "this invite link isn't valid any more") from None
            return {"inviter": self._card(graph, None, inviter.id, preview=True)}

    def create_invite(self, viewer: str) -> dict[str, Any]:
        """A single-use invite link to share; whoever opens it sees your
        profile card."""
        with self._graphs() as graph:
            issued = graph.invites.create(viewer, self._clock())
            return {"code": issued.code, "path": f"/invite/{issued.code}",
                    "expires_at": issued.invite.expires_at.isoformat()}

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

    # --- profiles (spec 4.3) -----------------------------------------------------

    def profile(self, viewer: str, params: dict[str, str]) -> dict[str, Any]:
        """A profile as the viewer may see it. Self and 1st-degree
        connections: everything. Anyone else: the partial card only."""
        with self._graphs() as graph:
            subject = _person(graph, _id(params.get("id") or viewer, "person"))
            sections = graph.visibility.visible_profile_sections(viewer, subject.id)
            now = self._clock()
            result: dict[str, Any] = {
                "card": self._card(graph, viewer, subject.id),
                "is_self": subject.id == viewer,
                "sections": {"orgs": sections.orgs, "events": sections.events,
                             "network": sections.network},
            }
            if sections.orgs:
                result["orgs"] = [
                    {"id": oid, "name": graph.store.node(oid, Organization).name,
                     "is_admin": graph.membership.is_admin(subject.id, oid)}
                    for oid in sorted(graph.visibility.org_ids_of(subject.id),
                                      key=lambda i: graph.store.node(i, Organization).name)
                ]
            if sections.events:
                result["events"] = _profile_events(graph, viewer, subject.id, now)
            if subject.id == viewer:
                result["email"] = subject.email
                result["palette"] = {"outline": OUTLINE_PRESETS, "background": BACKGROUND_PRESETS}
                result["limits"] = {"bio": BIO_MAX_CHARS, "top3_title": TOP3_TITLE_MAX_CHARS,
                                    "top3_item": TOP3_ITEM_MAX_CHARS, "name": MAX_NAME}
                result["upcoming"] = _my_events(graph, viewer, now)["upcoming"][:UPCOMING_PREVIEW]
            return result

    def update_profile(self, viewer: str, body: dict[str, Any]) -> dict[str, Any]:
        """Edit your own profile: `bio`, `top3` ({title, items[3]} or null),
        `theme` ({outline, background} preset names), `name`."""
        allowed = {"bio", "top3", "theme", "name"}
        unknown = set(body) - allowed - {"viewer"}
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"can't edit: {', '.join(sorted(unknown))}")
        with self._graphs() as graph:
            person = _person(graph, viewer)
            changes: dict[str, Any] = {}
            if "name" in body:
                name = body["name"]
                if not isinstance(name, str) or not 0 < len(" ".join(name.split())) <= MAX_NAME:
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"your name needs 1-{MAX_NAME} characters")
                changes["display_name"] = " ".join(name.split())
            if "bio" in body:
                if not isinstance(body["bio"], str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "your bio must be text")
                changes["bio"] = " ".join(body["bio"].split())
            if "top3" in body:
                changes["top3"] = _top3(body["top3"])
            if "theme" in body:
                theme = body["theme"]
                if not isinstance(theme, dict) or not all(
                    isinstance(theme.get(k), str) for k in ("outline", "background")
                ):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "pick an outline and a background colour")
                changes["theme"] = ProfileTheme(outline=theme["outline"], background=theme["background"])
            updated = replace(person, **changes)  # validates (DomainRuleError -> 400)
            graph.store.save_node(
                replace(updated, provenance=updated.provenance.confirmed(self._clock()))
            )
        return self.profile(viewer, {})

    def upload_photo(self, viewer: str, body: dict[str, Any]) -> dict[str, Any]:
        """Set (`content_type`, `data_b64`) or `remove` your photo (slot 0)
        or one of your three pictures (slots 1-3)."""
        slot = body.get("slot")
        if isinstance(slot, bool) or slot not in (PHOTO_SLOT, *PICTURE_SLOTS):
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown picture slot")
        with self._graphs() as graph:
            _person(graph, viewer)
            key = image_id(viewer, slot)
            if body.get("remove") is True:
                if graph.store.has_node(key):
                    graph.store.delete_node(key)
            else:
                content_type, data = body.get("content_type"), body.get("data_b64")
                if not isinstance(content_type, str) or not isinstance(data, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "send an image")
                graph.store.save_node(ProfileImage(
                    id=key, owner_id=viewer, slot=slot, content_type=content_type, data_b64=data,
                    provenance=Provenance(created_by=viewer, method=ProvenanceMethod.SELF_CLAIM,
                                          created_at=self._clock()),
                ))
        return self.profile(viewer, {})

    def photo(self, viewer: str, params: dict[str, str]) -> tuple[str, bytes]:
        """A profile image, if the viewer may see it: the photo is part of
        every card; the other pictures only of the full card."""
        owner = _id(params.get("id"), "person")
        slot_text = params.get("slot", "0")
        if slot_text not in {str(s) for s in (PHOTO_SLOT, *PICTURE_SLOTS)}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "unknown picture slot")
        slot = int(slot_text)
        with self._graphs() as graph:
            _person(graph, owner)
            if slot != PHOTO_SLOT and not graph.visibility.visible_profile_sections(
                viewer, owner
            ).pictures:
                raise ApiError(HTTPStatus.NOT_FOUND, "no picture here")
            key = image_id(owner, slot)
            if not graph.store.has_node(key):
                raise ApiError(HTTPStatus.NOT_FOUND, "no picture here")
            image = graph.store.node(key, ProfileImage)
            return image.content_type, image.data

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

    # --- My Events (spec 4.6) ---------------------------------------------------

    def my_events(self, viewer: str) -> dict[str, Any]:
        """Everything you're going to, said maybe to, or saved: upcoming
        soonest first, past most recent first."""
        with self._graphs() as graph:
            return _my_events(graph, viewer, self._clock())

    # --- personal calendar (spec 4.7) ------------------------------------------------

    def my_calendar(self, viewer: str, params: dict[str, str]) -> dict[str, Any]:
        """Your calendar for a window (`start`, `end`, ISO 8601 with a time
        zone): events you're going to, said maybe to, saved or host, plus
        events your organizations run. Nothing else: discovery is Discover's
        job, not the calendar's."""
        start = _datetime_param(params.get("start"), "start")
        end = _datetime_param(params.get("end"), "end")
        with self._graphs() as graph:
            cal = graph.my_calendar.for_viewer(viewer, start, end)
            mine_too = [e for e in cal.community if Relation.MY_ORG in e.relations]
            entries = sorted([*cal.mine, *mine_too], key=lambda e: (e.event.starts_at, e.event.id))
            return {"start": cal.start.isoformat(), "end": cal.end.isoformat(),
                    "entries": [_entry_json(e) for e in entries]}

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


def _csv(value: str | None, allowed: set[str], what: str) -> set[str]:
    chosen = {v.strip() for v in (value or "").split(",") if v.strip()}
    unknown = chosen - allowed
    if unknown:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown {what}: {', '.join(sorted(unknown))}")
    return chosen


def _result_json(
    graph: CollectivizeGraph, viewer: str, result: SearchResult, starred: set[str], debug: bool
) -> dict[str, Any]:
    """One search result. Every result says what it is (`type`). People
    show only what their partial card shows; no degree, no path."""
    if result.kind == "event" and result.event is not None:
        item = _item_json(result.event, starred)
        item.pop("score", None)
        item.pop("distance_km", None)
        event = result.event.event
        data = {"type": "event", **item, "capacity": event.capacity,
                "is_full": graph.rsvps.is_full(event.id),
                "tags": sorted(graph.store.node(t, Tag).label
                               for t in graph.discovery.event_tag_ids(event.id))}
    elif result.kind == "person":
        person = graph.store.node(result.id, Person)
        data = {"type": "person", "id": person.id, "name": person.display_name,
                "bio": person.bio, "top3_title": person.top3.title if person.top3 else None,
                "has_photo": _has_photo(graph, person.id),
                "connection": graph.visibility.connection_state(viewer, person.id).value}
    else:
        org = graph.store.node(result.id, Organization)
        members = graph.visibility.visible_members(viewer, org.id)
        data = {"type": "org", "id": org.id, "name": org.name, "kind": org.kind.value,
                "tags": _org_tags(graph, org.id), "member_count": members.count,
                "is_member": graph.membership.is_member(viewer, org.id)}
    if debug:
        data["score"] = round(result.score, 3)
    return data


def _top3(value: Any) -> Top3 | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("title"), str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Top 3 needs a title and three items")
    items = value.get("items")
    if not isinstance(items, list) or len(items) != 3 or not all(isinstance(i, str) for i in items):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Top 3 needs exactly three items")
    try:
        return Top3(title=" ".join(value["title"].split()),
                    items=tuple(" ".join(i.split()) for i in items))
    except DomainRuleError as error:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None


def _event_json(graph: CollectivizeGraph, event: Event, **extra: Any) -> dict[str, Any]:
    try:
        host = graph.events.host_node(graph.events.primary_host_id(event.id))
        host_name = host.name if isinstance(host, Organization) else host.display_name
    except (DomainRuleError, LookupError):
        host_name = None
    return {
        "id": event.id,
        "title": event.title,
        "starts_at": event.starts_at.isoformat(),
        "ends_at": event.ends_at.isoformat(),
        "location": event.location,
        "description": event.description,
        "host": host_name,
        "is_cancelled": event.status is EventStatus.CANCELLED,
        **extra,
    }


def _listed(graph: CollectivizeGraph, viewer: str, event_id: str) -> Event | None:
    """The event if the viewer may see it listed (published, visible host)."""
    if not graph.store.has_node(event_id):
        return None
    event = graph.store.any_node(event_id)
    if not isinstance(event, Event) or not event.was_published:
        return None
    if not (graph.privacy.can_view(viewer, event) or graph.events.can_edit(viewer, event.id)):
        return None
    return event


def _profile_events(
    graph: CollectivizeGraph, viewer: str, subject: str, now: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Going to (upcoming), attended (past events they said they'd go to)
    and hosted, as far as the viewer may see each event."""
    going, attended, hosted = [], [], []
    for rsvp in graph.store.edges(Rsvp, source_id=subject):
        event = _listed(graph, viewer, rsvp.target_id)
        if event is None or rsvp.status is not RsvpStatus.GOING:
            continue
        (attended if event.ends_at < now else going).append(event)
    for edge in graph.store.edges(Hosts, source_id=subject):
        event = _listed(graph, viewer, edge.target_id)
        if event is not None and edge.accepted:
            hosted.append(event)
    going.sort(key=lambda e: e.starts_at)
    attended.sort(key=lambda e: e.starts_at, reverse=True)
    hosted.sort(key=lambda e: e.starts_at, reverse=True)
    return {
        "going": [_event_json(graph, e) for e in going[:PROFILE_EVENTS]],
        "attended": [_event_json(graph, e) for e in attended[:PROFILE_EVENTS]],
        "hosted": [_event_json(graph, e, is_past=e.ends_at < now) for e in hosted[:PROFILE_EVENTS]],
    }


def _my_events(
    graph: CollectivizeGraph, viewer: str, now: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Every event the viewer is going to, said maybe to, or saved, with
    `rsvp` (going / maybe / null) and `saved`. Upcoming soonest first; past
    most recent first."""
    plans: dict[str, dict[str, Any]] = {}
    for rsvp in graph.store.edges(Rsvp, source_id=viewer):
        if rsvp.status in (RsvpStatus.GOING, RsvpStatus.INTERESTED):
            plans.setdefault(rsvp.target_id, {})["rsvp"] = (
                "going" if rsvp.status is RsvpStatus.GOING else "maybe"
            )
    for event_id in graph.stars.starred_event_ids(viewer):
        plans.setdefault(event_id, {})["saved"] = True
    upcoming, past = [], []
    for event_id, plan in plans.items():
        event = _listed(graph, viewer, event_id)
        if event is None or not event.is_active:
            continue
        entry = _event_json(graph, event, rsvp=plan.get("rsvp"), saved=plan.get("saved", False),
                            going_count=graph.rsvps.going_count(event.id), capacity=event.capacity)
        (past if event.ends_at < now else upcoming).append((event.starts_at, entry))
    upcoming.sort(key=lambda pair: pair[0])
    past.sort(key=lambda pair: pair[0], reverse=True)
    return {"upcoming": [e for _, e in upcoming], "past": [e for _, e in past]}


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
