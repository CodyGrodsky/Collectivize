"""Person node: one real human with an account.

Relationships are never stored on the node; they live on edges so each
fact carries its own visibility. Only the person may edit this node.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import urlparse

from .base import DomainRuleError, GraphRecord, Visibility

# Profile page limits (docs/profile-backend-proposal.md).
MAX_DISPLAY_NAME = 50
MAX_BIO = 120
MAX_ABOUT = 1000
MAX_TOP3_TITLE = 30
MAX_TOP3_ITEM = 40
TOP3_ITEMS = 3
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_BACKGROUND = "#F5F4F0"
DEFAULT_OUTLINE = "#202B29"
# The blocks of a profile page, in their default order. A person's
# `profile_sections` is the order they chose; a block left out is hidden.
PROFILE_SECTIONS = ("about", "top3", "network", "orgs", "events")
# Who may read a profile: any logged-in user, connections only, or only you.
PROFILE_AUDIENCES = (Visibility.NETWORK, Visibility.CONNECTIONS, Visibility.PRIVATE)


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
    # --- the profile page ---------------------------------------------------
    # `profile` above is the longer "About" text. Everything here is the
    # person's own to edit (ProfileService.update).
    bio: str = ""  # one line under the name
    top3_title: str = ""  # e.g. "Top 3 study spots"; empty = no Top 3
    top3_items: tuple[str, ...] = ()  # exactly three when there is a title
    website: str | None = None  # https only
    color_background: str = DEFAULT_BACKGROUND
    color_outline: str = DEFAULT_OUTLINE
    # Who may read the full profile. Separate from `visibility`, which
    # decides whether the person exists for a viewer at all.
    profile_visibility: Visibility = Visibility.NETWORK
    profile_sections: tuple[str, ...] = PROFILE_SECTIONS

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise DomainRuleError("display_name is required")
        self._check_profile()
        if self.travel_samples < 0:
            raise DomainRuleError("travel_samples cannot be negative")
        has_average = self.travel_log_km is not None
        if has_average != (self.travel_samples > 0):
            raise DomainRuleError("travel average and sample count must agree")
        if has_average and not math.isfinite(self.travel_log_km):  # type: ignore[arg-type]
            raise DomainRuleError("travel average must be finite")

    def _check_profile(self) -> None:
        if "\n" in self.bio or "\r" in self.bio or _length(self.bio) > MAX_BIO:
            raise DomainRuleError(f"bio must be one line of at most {MAX_BIO} characters")
        if _length(self.top3_title) > MAX_TOP3_TITLE:
            raise DomainRuleError(f"the Top 3 title can be at most {MAX_TOP3_TITLE} characters")
        expected = TOP3_ITEMS if self.top3_title.strip() else 0
        if len(self.top3_items) != expected or not all(i.strip() for i in self.top3_items):
            raise DomainRuleError("a Top 3 needs a title and exactly three items")
        if any(_length(item) > MAX_TOP3_ITEM for item in self.top3_items):
            raise DomainRuleError(f"each Top 3 item can be at most {MAX_TOP3_ITEM} characters")
        if self.website is not None:
            parsed = urlparse(self.website)
            if parsed.scheme != "https" or "." not in (parsed.hostname or ""):
                raise DomainRuleError("website must be a full https:// address")
        for color in (self.color_background, self.color_outline):
            if not HEX_COLOR.match(color):
                raise DomainRuleError("colors must look like #RRGGBB")
        if self.profile_visibility not in PROFILE_AUDIENCES:
            raise DomainRuleError("a profile is for everyone, connections only, or only you")
        sections = self.profile_sections
        if len(set(sections)) != len(sections) or not set(sections) <= set(PROFILE_SECTIONS):
            raise DomainRuleError(f"profile sections must be chosen from {list(PROFILE_SECTIONS)}")

    def with_hide_connections(self, hidden: bool) -> Person:
        return replace(self, hide_connections=hidden)


def _length(text: str) -> int:
    """Characters as people count them (close enough: code points)."""
    return len(text)
