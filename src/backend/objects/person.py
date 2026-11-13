"""Person node: one real human with an account.

Relationships are never stored on the node; they live on edges so each
fact carries its own visibility. Only the person may edit this node.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    profile: str = ""
    verification_level: VerificationLevel = VerificationLevel.NONE
    embedding: tuple[float, ...] | None = None  # Later: profile embedding
    visibility: Visibility = Visibility.NETWORK

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise DomainRuleError("display_name is required")
