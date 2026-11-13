"""Errors raised by graph services."""

from backend.objects import DomainRuleError


class NotFoundError(LookupError):
    """A referenced node or edge does not exist."""


class PermissionDeniedError(DomainRuleError):
    """The acting person is not allowed to perform this operation."""
