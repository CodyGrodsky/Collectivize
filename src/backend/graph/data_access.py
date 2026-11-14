"""Getting user data out: for the person themselves, and for analysis.

`person_data(person_id, requested_by, now)` - everything Collectivize holds
about one person, in plain JSON-safe form. The person can always ask for
their own. Staff can ask for anyone's by giving a purpose, and that access
is recorded (DataExport).

`ml_dataset(staff_id, purpose, now, key)` - every user's activity as tables
for analytics and machine learning:
- Only people with `analytics_consent` on are included, and only facts not
  marked `recommendation_use = NONE`. A connection appears only when both
  people consented.
- People are pseudonymized: each id becomes an HMAC-SHA256 of the real id
  under a secret key (from COLLECTIVIZE_EXPORT_KEY), so the same person has
  the same pseudonym across exports made with the same key, but it can't be
  reversed without the key.
- No names, emails, positions or invites. Events and orgs are public
  entities and keep their ids.
- Every export is recorded (DataExport: who, why, how much).

Note: files written from a dataset are outside the graph, so erasing an
account can't reach them. Keep exports short-lived and regenerate them.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.config import ConfigError
from backend.objects import (
    Connected,
    DataExport,
    Event,
    Hosts,
    InterestedIn,
    Invite,
    MemberOf,
    Organization,
    Person,
    Provenance,
    ProvenanceMethod,
    RecommendationUse,
    Rsvp,
    Tag,
    Tagged,
)

from .activity import ActivityService
from .errors import PermissionDeniedError
from .preferences import PreferenceService, typical_trip_km
from .store import GraphStore

EXPORT_KEY_ENV = "COLLECTIVIZE_EXPORT_KEY"
MIN_KEY_BYTES = 16
PSEUDONYM_CHARS = 24
TRIP_ROUNDING_KM = 0.5

Row = dict[str, Any]


@dataclass(frozen=True)
class MlDataset:
    generated_at: str
    purpose: str
    people: list[Row] = field(default_factory=list)
    interactions: list[Row] = field(default_factory=list)  # RSVPs
    memberships: list[Row] = field(default_factory=list)
    connections: list[Row] = field(default_factory=list)
    events: list[Row] = field(default_factory=list)
    orgs: list[Row] = field(default_factory=list)

    def tables(self) -> dict[str, list[Row]]:
        return {
            "people": self.people, "interactions": self.interactions,
            "memberships": self.memberships, "connections": self.connections,
            "events": self.events, "orgs": self.orgs,
        }


def export_key_from_env() -> bytes:
    """The pseudonymization key. Never hard-coded: set COLLECTIVIZE_EXPORT_KEY."""
    value = os.environ.get(EXPORT_KEY_ENV, "")
    if len(value.encode()) < MIN_KEY_BYTES:
        raise ConfigError(f"set {EXPORT_KEY_ENV} to a secret of at least {MIN_KEY_BYTES} bytes")
    return value.encode()


class DataAccessService:
    def __init__(
        self, store: GraphStore, preferences: PreferenceService, activity: ActivityService
    ) -> None:
        self._store = store
        self._preferences = preferences
        self._activity = activity

    # --- one person ------------------------------------------------------------

    def person_data(
        self,
        person_id: str,
        requested_by: str,
        now: datetime,
        *,
        staff_purpose: str | None = None,
    ) -> Row:
        person = self._store.node(person_id, Person)
        if requested_by != person_id:
            if staff_purpose is None:
                raise PermissionDeniedError("only you, or staff with a stated purpose, can see this")
            self._record(requested_by, staff_purpose, people=1, rows=1, now=now)
        return {
            "person": {
                "id": person.id, "display_name": person.display_name, "email": person.email,
                "profile": person.profile, "verification": person.verification_level.value,
                "visibility": person.visibility.name.lower(),
                "settings": {
                    "hide_connections": person.hide_connections,
                    "rsvp_visibility": person.rsvp_visibility.name.lower(),
                    "ai_uses_history": person.ai_uses_history,
                    "analytics_consent": person.analytics_consent,
                },
                "typical_trip_km": typical_trip_km(person),
                "trips_recorded": person.travel_samples,
            },
            "memberships": [self._membership_row(m) for m in self._store.edges(
                MemberOf, source_id=person_id, include_inactive=True)],
            "connections": self._connections_of(person_id),
            "rsvps": [self._rsvp_row(r) for r in self._store.edges(Rsvp, source_id=person_id)],
            "interests": [self._tag_slug(e.target_id) for e in self._store.edges(
                InterestedIn, source_id=person_id)],
            "invites_sent": [
                {"created_at": _iso(i.provenance.created_at), "expires_at": _iso(i.expires_at),
                 "used": i.used_by is not None, "revoked": i.revoked}
                for i in self._store.nodes(Invite) if i.inviter_id == person_id
            ],
            "hosted_events": [e.target_id for e in self._store.edges(Hosts, source_id=person_id)],
            "learned": {
                "categories": {
                    self._tag_slug(t): round(s, 3)
                    for t, s in self._preferences.profile(person_id, now).learned.items()
                },
                "top_orgs": [
                    {"org_id": o.org.id, "name": o.org.name, "score": round(o.score, 3)}
                    for o in self._activity.top_orgs(person_id, now)
                ],
            },
        }

    # --- everyone, for analysis -----------------------------------------------

    def ml_dataset(
        self, staff_id: str, purpose: str, now: datetime, key: bytes
    ) -> MlDataset:
        if len(key) < MIN_KEY_BYTES:
            raise ConfigError(f"the pseudonymization key needs at least {MIN_KEY_BYTES} bytes")
        consenting = {p.id: p for p in self._store.nodes(Person) if p.analytics_consent}
        pid = lambda person_id: _pseudonym(key, person_id)
        dataset = MlDataset(generated_at=_iso(now), purpose=purpose.strip())
        for person in consenting.values():
            dataset.people.append(self._person_row(person, pid(person.id)))
            dataset.interactions.extend(
                {**self._rsvp_row(r), "person": pid(person.id)}
                for r in self._store.edges(Rsvp, source_id=person.id) if _usable(r)
            )
            dataset.memberships.extend(
                {**self._membership_row(m), "person": pid(person.id)}
                for m in self._store.edges(MemberOf, source_id=person.id, include_inactive=True)
                if _usable(m)
            )
        dataset.connections.extend(self._consented_connections(consenting, pid))
        people_ids = {p.id for p in self._store.nodes(Person)}
        dataset.events.extend(
            self._event_row(e, consenting, pid, people_ids) for e in self._store.nodes(Event)
        )
        dataset.orgs.extend(
            {"org_id": o.id, "name": o.name, "kind": o.kind.value, "claim_state": o.claim_state.value}
            for o in self._store.nodes(Organization)
        )
        rows = sum(len(t) for t in dataset.tables().values())
        self._record(staff_id, purpose, people=len(dataset.people), rows=rows, now=now)
        return dataset

    # --- rows ------------------------------------------------------------------

    def _person_row(self, person: Person, pseudonym: str) -> Row:
        trip = typical_trip_km(person)
        return {
            "person": pseudonym,
            "verification": person.verification_level.value,
            "joined_month": person.provenance.created_at.strftime("%Y-%m"),
            "interests": sorted(
                self._tag_slug(e.target_id)
                for e in self._store.edges(InterestedIn, source_id=person.id) if _usable(e)
            ),
            "typical_trip_km": (
                None if trip is None else round(trip / TRIP_ROUNDING_KM) * TRIP_ROUNDING_KM
            ),
        }

    def _rsvp_row(self, rsvp: Rsvp) -> Row:
        return {"event_id": rsvp.target_id, "status": rsvp.status.value, "at": _iso(rsvp.updated_at)}

    def _membership_row(self, edge: MemberOf) -> Row:
        return {
            "org_id": edge.target_id, "role": edge.role.value,
            "confirmed": edge.org_confirmed, "current": edge.is_current,
            "started_at": _iso(edge.started_at), "ended_at": _iso(edge.ended_at),
        }

    def _event_row(
        self, event: Event, consenting: dict[str, Person], pid: Any, people_ids: set[str]
    ) -> Row:
        hosts = [h for h in self._store.edges(Hosts, target_id=event.id) if h.is_effective]
        org_hosts = [h.source_id for h in hosts if h.source_id not in people_ids]
        person_hosts = [pid(h.source_id) for h in hosts if h.source_id in consenting]
        point = event.coordinates
        return {
            "event_id": event.id, "title": event.title, "status": event.status.value,
            "visibility": event.visibility.name.lower(),
            "starts_at": _iso(event.starts_at), "ends_at": _iso(event.ends_at),
            "capacity": event.capacity, "is_demo": event.is_demo,
            "latitude": point.latitude if point else None,
            "longitude": point.longitude if point else None,
            "tags": sorted(
                self._tag_slug(t.target_id)
                for subject in [event.id, *org_hosts]
                for t in self._store.edges(Tagged, source_id=subject)
            ),
            "host_org_ids": sorted(org_hosts),
            "host_people": sorted(person_hosts),  # only hosts who consented
        }

    def _consented_connections(self, consenting: dict[str, Person], pid: Any) -> list[Row]:
        rows = []
        for edge in self._store.edges(Connected):
            if not (edge.is_established and _usable(edge)):
                continue
            if edge.source_id in consenting and edge.target_id in consenting:
                a, b = sorted((pid(edge.source_id), pid(edge.target_id)))
                rows.append({"person_a": a, "person_b": b, "origin": edge.origin.value})
        return sorted(rows, key=lambda r: (r["person_a"], r["person_b"]))

    def _connections_of(self, person_id: str) -> list[Row]:
        rows = []
        for edge in self._store.edges(Connected, source_id=person_id) + self._store.edges(
            Connected, target_id=person_id
        ):
            other = edge.target_id if edge.source_id == person_id else edge.source_id
            rows.append({
                "with": self._store.node(other, Person).display_name,
                "established": edge.is_established, "origin": edge.origin.value,
            })
        return rows

    # --- internals -------------------------------------------------------------

    def _tag_slug(self, tag_id: str) -> str:
        node = self._store.any_node(tag_id) if self._store.has_node(tag_id) else None
        return node.slug if isinstance(node, Tag) else tag_id

    def _record(self, staff_id: str, purpose: str, *, people: int, rows: int, now: datetime) -> None:
        self._store.save_node(
            DataExport(
                requested_by=staff_id, purpose=purpose.strip(), people=people, rows=rows,
                provenance=Provenance(
                    created_by=staff_id, method=ProvenanceMethod.STAFF, created_at=now
                ),
            )
        )


def _pseudonym(key: bytes, person_id: str) -> str:
    return hmac.new(key, person_id.encode(), hashlib.sha256).hexdigest()[:PSEUDONYM_CHARS]


def _usable(edge: Any) -> bool:
    return getattr(edge, "recommendation_use", RecommendationUse.OWN_ONLY) is not RecommendationUse.NONE


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
