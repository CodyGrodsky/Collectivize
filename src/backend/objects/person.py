"""Person node: one real human with an account.

Relationships are never stored on the node; they live on edges so each
fact carries its own visibility. Only the person may edit this node.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from .base import DomainRuleError, GraphRecord, Visibility


class VerificationLevel(Enum):
    """Which level of identity verification was achieved (not how)."""

    NONE = "none"
    EMAIL = "email"
    UNIVERSITY_EMAIL = "university_email"
    OTHER = "other"


@dataclass(frozen=True, kw_only=True)
class Person(GraphRecord):
    display_name: str
    # Normalized (lowercase) sign-in email. Private: never send it to other
    # users; network and roster results expose only the display name.
    email: str | None = None
    profile: str = ""
    verification_level: VerificationLevel = VerificationLevel.NONE
    embedding: tuple[float, ...] | None = None  # Later: profile embedding
    visibility: Visibility = Visibility.NETWORK
    # When on, nobody else sees this person's connections, so they never
    # appear as a link, a step on someone's path, or a mutual friend.
    hide_connections: bool = False
    # Who can see this person's RSVPs: NETWORK, CONNECTIONS or PRIVATE.
    # Copied onto each RSVP; change it through RsvpService.
    rsvp_visibility: Visibility = Visibility.CONNECTIONS
    # How far this person usually travels to events, as a running average
    # of log-distance. Only the average is kept: never positions, never
    # per-event distances. Private like `email`; see PreferenceService.
    travel_log_km: float | None = None
    travel_samples: int = 0
    # May AI search read this person's history (interests, orgs, past
    # events) to personalise their own results? Their own data, for their
    # own results, so on unless they turn it off.
    ai_uses_history: bool = True
    # May this person's data be included in pseudonymized datasets for
    # analytics and machine learning across users? Off until they opt in
    # (user_Orgs.md: "The default is the narrower choice").
    analytics_consent: bool = False

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise DomainRuleError("display_name is required")
        if self.travel_samples < 0:
            raise DomainRuleError("travel_samples cannot be negative")
        has_average = self.travel_log_km is not None
        if has_average != (self.travel_samples > 0):
            raise DomainRuleError("travel average and sample count must agree")
        if has_average and not math.isfinite(self.travel_log_km):  # type: ignore[arg-type]
            raise DomainRuleError("travel average must be finite")

    def with_hide_connections(self, hidden: bool) -> Person:
        return replace(self, hide_connections=hidden)
