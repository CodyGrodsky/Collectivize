"""Tag (fixed curated set in MVP) and Venue (Later) nodes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import DomainRuleError, GraphRecord

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, kw_only=True)
class Tag(GraphRecord):
    """An explicit, explainable topic, e.g. "robotics"."""

    slug: str
    label: str
    is_curated: bool = True  # Later: user-proposed tags pending moderation

    def __post_init__(self) -> None:
        if not _SLUG.match(self.slug):
            raise DomainRuleError("slug must be lowercase kebab-case")
        if not self.label.strip():
            raise DomainRuleError("label is required")


@dataclass(frozen=True, kw_only=True)
class Venue(GraphRecord):
    """Later: a physical place shared by events, enabling "near me"."""

    name: str
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
