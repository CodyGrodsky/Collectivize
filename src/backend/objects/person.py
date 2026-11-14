"""Person node: one real human with an account.

Relationships are never stored on the node; they live on edges so each
fact carries its own visibility. Only the person may edit this node.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

from .base import DomainRuleError, GraphRecord, Visibility


BIO_MAX_CHARS = 160
TOP3_TITLE_MAX_CHARS = 30  # SPEC-ASSUMPTION: "a reasonable character limit"
TOP3_ITEM_MAX_CHARS = 40
# Profile colours come from small preset palettes (v1.1 spec 4.3): every
# outline is readable as text and lines on every background.
OUTLINE_PRESETS = {
    "ink": "#202B29", "teal": "#0F7A64", "coral": "#C2361F", "cobalt": "#1E3A9E",
    "plum": "#6B2D6B",
}
BACKGROUND_PRESETS = {
    "paper": "#F5F4F0", "white": "#FDFDFB", "mint": "#E4F3EE", "sky": "#E6ECFA",
    "blush": "#FBE9E5",
}
DEFAULT_OUTLINE = "ink"
DEFAULT_BACKGROUND = "paper"


@dataclass(frozen=True, kw_only=True)
class Top3:
    """The profile's "Top 3" block: the person names the category, and
    fills in exactly three items. The label and numbering are fixed."""

    title: str
    items: tuple[str, str, str]

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > TOP3_TITLE_MAX_CHARS:
            raise DomainRuleError(f"the Top 3 title needs 1-{TOP3_TITLE_MAX_CHARS} characters")
        if len(self.items) != 3:
            raise DomainRuleError("Top 3 needs exactly three items")
        for item in self.items:
            if not isinstance(item, str) or len(item) > TOP3_ITEM_MAX_CHARS:
                raise DomainRuleError(f"each Top 3 item is at most {TOP3_ITEM_MAX_CHARS} characters")


@dataclass(frozen=True, kw_only=True)
class ProfileTheme:
    outline: str = DEFAULT_OUTLINE
    background: str = DEFAULT_BACKGROUND

    def __post_init__(self) -> None:
        if self.outline not in OUTLINE_PRESETS:
            raise DomainRuleError("pick an outline colour from the palette")
        if self.background not in BACKGROUND_PRESETS:
            raise DomainRuleError("pick a background colour from the palette")


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
    # Profile (v1.1 spec 4.3). Photos are stored as ProfileImage records,
    # not here, so loading every person stays cheap.
    bio: str = ""
    top3: Top3 | None = None
    theme: ProfileTheme = field(default_factory=ProfileTheme)
    # Collectivize staff/moderator (not an org officer). Can open /admin.
    is_platform_admin: bool = False

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
        if len(self.bio) > BIO_MAX_CHARS:
            raise DomainRuleError(f"keep your bio under {BIO_MAX_CHARS} characters")

    def with_hide_connections(self, hidden: bool) -> Person:
        return replace(self, hide_connections=hidden)
