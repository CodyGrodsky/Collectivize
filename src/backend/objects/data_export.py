"""DataExport node: an audit record of every bulk export of user data."""

from __future__ import annotations

from dataclasses import dataclass

from .base import DomainRuleError, GraphRecord, Visibility


@dataclass(frozen=True, kw_only=True)
class DataExport(GraphRecord):
    requested_by: str  # staff id
    purpose: str
    people: int
    rows: int
    visibility: Visibility = Visibility.PRIVATE

    def __post_init__(self) -> None:
        if not self.requested_by.strip():
            raise DomainRuleError("an export needs the requesting staff member")
        if len(self.purpose.strip()) < 10:
            raise DomainRuleError("say what the export is for (at least 10 characters)")
