"""The "would they go?" model behind Discover.

Each event gets a likelihood between 0 and 1:

    likelihood = sigmoid(bias + sum(weight_i * signal_i))

Every signal is continuous, so small differences in the world make small
differences in the ranking instead of jumping between fixed point values:

- friends:      log(1 + friends going). The first friend matters most.
- my_org_host:  1 when one of your orgs hosts or co-hosts, else 0.
- affinity:     how well the event's tags match your tastes (interests you
                chose, your orgs' tags, categories you have gone to; see
                preferences.py): cosine similarity scaled by how strong
                your strongest taste is, so a match on interests you chose
                counts more than a match on your orgs' tags alone. 0..1.
- proximity:    closeness - 0.5, where closeness = 1 / (1 + (d / s)^2):
                1 next door, 0.5 at your usual distance s, toward 0 beyond.
                Unknown distance counts as 0 (neutral), not as far away.
- soonness:     exp(-days until start / SOONNESS_DAYS).
- host_trust:   1 verified org, 0.4 claim pending, 0 unclaimed or a person.
- staleness:    0 while the host was active in the last STALENESS_GRACE_DAYS,
                then rising smoothly toward 1.
- verified / disputed: whether the event's details were checked.
- request_match: how well the event fits what you asked for in words
                (AI search), as 2 x (match - 0.5): -1 the opposite, 0
                neutral or no request, +1 exactly it. Weighted heavily
                because you asked for it directly.

The weights are hand-set defaults chosen so the network signals dominate
(collectivize_plan.md: friends > your orgs > shared tags). They live in one
place, RankingWeights, so they can later be fitted to real attendance with
logistic regression. Until then the likelihood is a ranking estimate, not a
calibrated probability. Per user_Orgs.md it is computed per search and never
stored.

Cancelled events get likelihood 0 so they always sort last.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields

from backend.objects import DomainRuleError, VerificationStatus

SOONNESS_DAYS = 7.0
STALENESS_GRACE_DAYS = 30.0
STALENESS_DAYS = 60.0
MIN_TRAVEL_SCALE_KM = 0.25  # a typical distance can't shrink the curve to nothing


@dataclass(frozen=True, kw_only=True)
class RankingWeights:
    # Tuned so an event with nothing going for it sits near 10%, a strong
    # match lands around 85-95%, and the plan's order holds (friends going >
    # your org hosting > sharing your orgs' tags).
    bias: float = -3.0
    friends: float = 1.5
    my_org_host: float = 0.8
    affinity: float = 1.5
    proximity: float = 1.6
    soonness: float = 0.5
    host_trust: float = 0.5
    staleness: float = -1.5
    verified: float = 0.4
    disputed: float = -2.0
    request_match: float = 2.5

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DomainRuleError(f"weight {field.name} must be a number")
            if not math.isfinite(value):
                raise DomainRuleError(f"weight {field.name} must be finite")


DEFAULT_WEIGHTS = RankingWeights()


@dataclass(frozen=True)
class EventSignals:
    """What we know about one event, from one viewer's point of view."""

    friends_going: int = 0
    my_org_hosts: bool = False
    affinity: float = 0.0
    distance_km: float | None = None
    travel_scale_km: float = 2.0
    days_until: float = 0.0
    host_trust: float = 0.0
    host_inactive_days: float | None = None  # None for a person host
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    is_cancelled: bool = False
    request_match: float | None = None  # 0..1 from AI search; None = no request

    def __post_init__(self) -> None:
        if self.request_match is not None:
            _require_unit("request_match", self.request_match)
        if self.friends_going < 0:
            raise DomainRuleError("friends_going cannot be negative")
        _require_unit("affinity", self.affinity)
        _require_unit("host_trust", self.host_trust)
        _require_finite("days_until", self.days_until)
        _require_finite("travel_scale_km", self.travel_scale_km)
        if self.travel_scale_km <= 0:
            raise DomainRuleError("travel_scale_km must be positive")
        if self.distance_km is not None:
            _require_finite("distance_km", self.distance_km)
            if self.distance_km < 0:
                raise DomainRuleError("distance_km cannot be negative")
        if self.host_inactive_days is not None:
            _require_finite("host_inactive_days", self.host_inactive_days)


@dataclass(frozen=True)
class Contribution:
    """One signal's part in the estimate. `effect` is in log-odds."""

    name: str
    value: float
    effect: float


@dataclass(frozen=True)
class Estimate:
    likelihood: float
    contributions: tuple[Contribution, ...]

    def effect(self, name: str) -> float:
        return sum(c.effect for c in self.contributions if c.name == name)


def estimate(signals: EventSignals, weights: RankingWeights = DEFAULT_WEIGHTS) -> Estimate:
    values = signal_values(signals)
    contributions = tuple(
        Contribution(name=name, value=value, effect=getattr(weights, name) * value)
        for name, value in values.items()
    )
    if signals.is_cancelled:
        return Estimate(likelihood=0.0, contributions=contributions)
    logit = weights.bias + sum(c.effect for c in contributions)
    return Estimate(likelihood=sigmoid(logit), contributions=contributions)


def signal_values(s: EventSignals) -> Mapping[str, float]:
    return {
        "friends": math.log1p(s.friends_going),
        "my_org_host": 1.0 if s.my_org_hosts else 0.0,
        "affinity": s.affinity,
        "proximity": (
            0.0
            if s.distance_km is None
            else closeness(s.distance_km, s.travel_scale_km) - 0.5
        ),
        "soonness": soonness(s.days_until),
        "host_trust": s.host_trust,
        "staleness": 0.0 if s.host_inactive_days is None else staleness(s.host_inactive_days),
        "verified": 1.0 if s.verification is VerificationStatus.VERIFIED else 0.0,
        "disputed": 1.0 if s.verification is VerificationStatus.DISPUTED else 0.0,
        "request_match": 0.0 if s.request_match is None else 2.0 * (s.request_match - 0.5),
    }


# --- the curves (each maps to a bounded range and is monotonic) ---------------


def sigmoid(x: float) -> float:
    """1 / (1 + e^-x), without overflow for very large |x|."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def closeness(distance_km: float, scale_km: float) -> float:
    """1 at distance 0, exactly 0.5 at `scale_km`, falling toward 0."""
    scale = max(scale_km, MIN_TRAVEL_SCALE_KM)
    ratio = max(distance_km, 0.0) / scale
    if ratio > 1e150:  # squaring would overflow; the answer is 0 anyway
        return 0.0
    return 1.0 / (1.0 + ratio * ratio)


def soonness(days_until: float) -> float:
    """1 for an event starting now (or under way), fading over about a week."""
    return math.exp(-max(days_until, 0.0) / SOONNESS_DAYS)


def staleness(inactive_days: float) -> float:
    """0 within the grace period, then rising smoothly toward 1."""
    overdue = max(inactive_days - STALENESS_GRACE_DAYS, 0.0)
    return 1.0 - math.exp(-overdue / STALENESS_DAYS)


def taste_match(taste: Mapping[str, float], event_tags: Iterable[str]) -> float:
    """Affinity signal: cosine(taste, event tags) x strength of your taste,
    where a chosen interest (weight >= 1) is full strength. In [0, 1]."""
    strength = min(1.0, max(taste.values(), default=0.0))
    return cosine(taste, dict.fromkeys(event_tags, 1.0)) * max(strength, 0.0)


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Cosine similarity of two non-negative sparse vectors, in [0, 1].
    Empty or all-zero vectors have similarity 0."""
    dot = sum(weight * b[key] for key, weight in a.items() if key in b)
    norm = math.sqrt(sum(w * w for w in a.values())) * math.sqrt(sum(w * w for w in b.values()))
    if norm == 0 or not math.isfinite(norm):
        return 0.0
    return min(1.0, max(0.0, dot / norm))


def _require_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DomainRuleError(f"{name} must be a finite number")


def _require_unit(name: str, value: float) -> None:
    _require_finite(name, value)
    if not 0.0 <= value <= 1.0:
        raise DomainRuleError(f"{name} must be between 0 and 1")
