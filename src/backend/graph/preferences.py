"""What a person likes and how far they usually go, for their own Discover.

Three sources of taste, blended into one tag vector:
- Interests the person chose (INTERESTED_IN edges). Only the person can add
  or remove them (user_Orgs.md: "People's interests are set only by
  themselves").
- Tags of the orgs they belong to (weaker).
- Categories they have gone to: tags of events they RSVP'd to, with
  "going" counting double "interested", and older RSVPs fading (half-life
  CATEGORY_HALF_LIFE). Worked out at search time, never stored.

Travel: when someone RSVPs "going" and their device shares where they are,
the distance to the event updates a running average on their own Person
record. The position itself is never stored, and neither is any single
distance (many distances to known places could reveal where someone lives).

Consent (user_Orgs.md, "Recommendation consent"): an RSVP or interest whose
`recommendation_use` is NONE is never learned from. Everything here only
improves the person's own results. `forget_travel` resets the average, and
erasing the account removes everything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from backend.objects import (
    Event,
    Hosts,
    InterestedIn,
    MemberOf,
    Person,
    Provenance,
    ProvenanceMethod,
    RecommendationUse,
    Rsvp,
    RsvpStatus,
    Tag,
    Tagged,
)

from .errors import PermissionDeniedError
from .store import GraphStore

CHOSEN_INTEREST_WEIGHT = 1.0
ORG_TAG_WEIGHT = 0.5
LEARNED_WEIGHT = 0.8
GOING_WEIGHT, INTERESTED_WEIGHT = 1.0, 0.5
CATEGORY_HALF_LIFE = timedelta(days=120)
MIN_TRAVEL_SAMPLES = 3  # before this, Discover uses a default distance
TRAVEL_SMOOTHING = 0.1  # weight of each new trip once there's some history
TRAVEL_OFFSET_KM = 0.1  # keeps log() finite for zero-distance trips
MAX_TRIP_KM = 500.0  # longer "trips" (e.g. travelling) are ignored


@dataclass(frozen=True)
class TasteProfile:
    chosen: frozenset[str] = frozenset()  # tag ids the person picked
    org_tags: frozenset[str] = frozenset()
    learned: dict[str, float] = field(default_factory=dict)  # tag id -> 0..1
    typical_trip_km: float | None = None  # None until MIN_TRAVEL_SAMPLES
    trip_samples: int = 0

    def vector(self, extra_chosen: frozenset[str] = frozenset()) -> dict[str, float]:
        """One weight per tag, combining every source."""
        weights: dict[str, float] = {}
        for tag in self.chosen | extra_chosen:
            weights[tag] = weights.get(tag, 0.0) + CHOSEN_INTEREST_WEIGHT
        for tag in self.org_tags:
            weights[tag] = weights.get(tag, 0.0) + ORG_TAG_WEIGHT
        for tag, strength in self.learned.items():
            weights[tag] = weights.get(tag, 0.0) + LEARNED_WEIGHT * strength
        return weights


EMPTY_PROFILE = TasteProfile()


class PreferenceService:
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    # --- interests (set only by the person) -----------------------------------

    def add_interest(
        self, person_id: str, tag_id: str, by_person_id: str, now: datetime
    ) -> InterestedIn:
        self._require_self(person_id, by_person_id)
        self._store.node(tag_id, Tag)
        existing = self._store.edges(InterestedIn, source_id=person_id, target_id=tag_id)
        if existing:
            return existing[0]
        return self._store.save_edge(
            InterestedIn(
                source_id=person_id,
                target_id=tag_id,
                provenance=Provenance(
                    created_by=person_id, method=ProvenanceMethod.SELF_CLAIM, created_at=now
                ),
            )
        )

    def remove_interest(self, person_id: str, tag_id: str, by_person_id: str) -> None:
        self._require_self(person_id, by_person_id)
        for edge in self._store.edges(InterestedIn, source_id=person_id, target_id=tag_id):
            self._store.delete_edge(edge.id)

    def interests_of(self, person_id: str, by_person_id: str) -> list[Tag]:
        self._require_self(person_id, by_person_id)
        return [
            self._store.node(e.target_id, Tag)
            for e in self._store.edges(InterestedIn, source_id=person_id)
        ]

    # --- travel ----------------------------------------------------------------

    def record_trip(self, person_id: str, distance_km: float) -> Person:
        """Fold one trip's distance into the person's running average.
        Called by RsvpService; the distance itself is not kept."""
        person = self._store.node(person_id, Person)
        if not math.isfinite(distance_km) or not 0 <= distance_km <= MAX_TRIP_KM:
            return person
        sample = math.log(distance_km + TRAVEL_OFFSET_KM)
        count = person.travel_samples + 1
        if person.travel_log_km is None:
            average = sample
        else:
            step = max(1.0 / count, TRAVEL_SMOOTHING)
            average = person.travel_log_km + step * (sample - person.travel_log_km)
        return self._store.save_node(
            replace(person, travel_log_km=average, travel_samples=count)
        )

    def forget_travel(self, person_id: str, by_person_id: str) -> Person:
        self._require_self(person_id, by_person_id)
        person = self._store.node(person_id, Person)
        return self._store.save_node(replace(person, travel_log_km=None, travel_samples=0))

    # --- the profile -----------------------------------------------------------

    def profile(self, person_id: str | None, now: datetime) -> TasteProfile:
        """Everything Discover may use for this person. Computed fresh each
        time and never stored. Logged-out visitors get an empty profile."""
        if person_id is None:
            return EMPTY_PROFILE
        person = self._store.node(person_id, Person)
        return TasteProfile(
            chosen=frozenset(
                e.target_id
                for e in self._store.edges(InterestedIn, source_id=person_id)
                if e.recommendation_use is not RecommendationUse.NONE
            ),
            org_tags=frozenset(self._org_tags(person_id)),
            learned=self._learned_categories(person_id, now),
            typical_trip_km=typical_trip_km(person),
            trip_samples=person.travel_samples,
        )

    def my_profile(self, person_id: str, by_person_id: str, now: datetime) -> TasteProfile:
        """Lets a person see what Discover has learned about them."""
        self._require_self(person_id, by_person_id)
        return self.profile(person_id, now)

    # --- internals -------------------------------------------------------------

    def _org_tags(self, person_id: str) -> set[str]:
        return {
            tagged.target_id
            for membership in self._store.edges(MemberOf, source_id=person_id)
            if membership.is_confirmed_current
            for tagged in self._store.edges(Tagged, source_id=membership.target_id)
        }

    def _learned_categories(self, person_id: str, now: datetime) -> dict[str, float]:
        totals: dict[str, float] = {}
        for rsvp in self._store.edges(Rsvp, source_id=person_id):
            if rsvp.recommendation_use is RecommendationUse.NONE:
                continue
            if rsvp.status is RsvpStatus.NOT_GOING or not self._store.has_node(rsvp.target_id):
                continue
            base = GOING_WEIGHT if rsvp.status is RsvpStatus.GOING else INTERESTED_WEIGHT
            weight = base * decay(now - rsvp.updated_at, CATEGORY_HALF_LIFE)
            for tag in self._event_tags(rsvp.target_id):
                totals[tag] = totals.get(tag, 0.0) + weight
        top = max(totals.values(), default=0.0)
        if top <= 0:
            return {}
        return {tag: value / top for tag, value in totals.items()}

    def _event_tags(self, event_id: str) -> set[str]:
        if not isinstance(self._store.any_node(event_id), Event):
            return set()
        hosts = {h.source_id for h in self._store.edges(Hosts, target_id=event_id) if h.is_effective}
        return {
            t.target_id
            for subject in {event_id} | hosts
            for t in self._store.edges(Tagged, source_id=subject)
        }

    @staticmethod
    def _require_self(person_id: str, by_person_id: str) -> None:
        if person_id != by_person_id:
            raise PermissionDeniedError("only you can see or change your preferences")


def typical_trip_km(person: Person) -> float | None:
    """The person's usual travel distance, once there's enough history."""
    if person.travel_log_km is None or person.travel_samples < MIN_TRAVEL_SAMPLES:
        return None
    capped = min(person.travel_log_km, math.log(MAX_TRIP_KM + TRAVEL_OFFSET_KM))
    return max(math.exp(capped) - TRAVEL_OFFSET_KM, 0.0)


def decay(age: timedelta, half_life: timedelta) -> float:
    """1 for now or the future, 0.5 after one half-life, and so on."""
    return 0.5 ** (max(age.total_seconds(), 0.0) / half_life.total_seconds())
