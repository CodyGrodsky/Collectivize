"""Plain-language reasons for a Discover result.

Reasons come from the model's own contributions, so they always agree with
the ranking: a factor is mentioned when it moved the estimate noticeably
(at least NOTABLE_EFFECT in log-odds), biggest first. Facts that explain an
event (cancelled, who hosts it, which of your interests it matches) are
always shown.
"""

from __future__ import annotations

from backend.objects import ClaimState, Organization, Person

from .preferences import TasteProfile
from .ranking import Estimate, EventSignals

NOTABLE_EFFECT = 0.2
LEARNED_MENTION = 0.25  # learned categories weaker than this aren't named
STARTING_SOON_DAYS = 2.0
OUT_OF_DATE_STALENESS = 0.5
CANCELLED_REASON = "Cancelled: this event is not happening"
HOST_TRUST_REASON = {
    ClaimState.CLAIMED: "Hosted by a verified org",
    ClaimState.CLAIM_PENDING: "Host's claim is being verified",
    ClaimState.UNCLAIMED: "Unclaimed listing: not managed by the org itself",
}
PERSON_HOST_REASON = "Hosted by a person"


def explain(
    signals: EventSignals,
    result: Estimate,
    host: Organization | Person,
    my_hosts: list[str],
    tags: set[str],
    profile: TasteProfile,
    preferred: frozenset[str],
    labels: dict[str, str],
    request_reason: str = "",
) -> tuple[str, ...]:
    head = [CANCELLED_REASON] if signals.is_cancelled else []
    head.append(
        HOST_TRUST_REASON[host.claim_state] if isinstance(host, Organization)
        else PERSON_HOST_REASON
    )
    ranked: list[tuple[float, str]] = []

    def add(effect: float, text: str) -> None:
        ranked.append((abs(effect), text))

    request_effect = result.effect("request_match")
    if request_effect >= NOTABLE_EFFECT:
        detail = f": {request_reason}" if request_reason else ""
        add(request_effect, f"Fits what you asked for{detail}")
    elif request_effect <= -NOTABLE_EFFECT:
        add(request_effect, "Not much like what you asked for")
    if signals.friends_going:
        n = signals.friends_going
        add(result.effect("friends"), f"{n} friend{'s' if n != 1 else ''} going")
    if my_hosts:
        add(result.effect("my_org_host"), f"Hosted by your org: {', '.join(my_hosts)}")
    for effect, text in _taste_reasons(result, tags, profile, preferred, labels):
        add(effect, text)
    proximity = _proximity_reason(signals, result.effect("proximity"))
    if proximity:
        add(result.effect("proximity"), proximity)
    if not signals.is_cancelled and signals.days_until < 0:
        add(result.effect("soonness"), "Happening now")
    elif not signals.is_cancelled and signals.days_until <= STARTING_SOON_DAYS:
        add(result.effect("soonness"), "Starts within 48 hours")
    staleness_effect = result.effect("staleness")
    if abs(staleness_effect) >= NOTABLE_EFFECT:
        value = next(c.value for c in result.contributions if c.name == "staleness")
        text = (
            "Host info may be out of date" if value >= OUT_OF_DATE_STALENESS
            else "Host hasn't posted recently"
        )
        add(staleness_effect, text)
    if result.effect("verified"):
        add(result.effect("verified"), "Event details verified")
    if result.effect("disputed"):
        add(result.effect("disputed"), "Event details are disputed")
    ranked.sort(key=lambda pair: -pair[0])
    return tuple(head + [text for _, text in ranked])


def _taste_reasons(
    result: Estimate,
    tags: set[str],
    profile: TasteProfile,
    preferred: frozenset[str],
    labels: dict[str, str],
) -> list[tuple[float, str]]:
    """Which of your tastes this event matches, grouped by where they come from."""
    effect = result.effect("affinity")
    if effect <= 0:
        return []
    chosen = sorted(tags & (profile.chosen | preferred), key=lambda t: labels.get(t, t))
    from_orgs = sorted(tags & profile.org_tags - set(chosen), key=lambda t: labels.get(t, t))
    learned = sorted(
        (t for t in tags - set(chosen) - set(from_orgs)
         if profile.learned.get(t, 0.0) >= LEARNED_MENTION),
        key=lambda t: labels.get(t, t),
    )
    reasons = [(effect, f"Matches your interest: {labels.get(t, t)}") for t in chosen]
    if from_orgs:
        names = ", ".join(labels.get(t, t) for t in from_orgs)
        reasons.append((effect, f"Like your orgs: {names}"))
    if learned:
        names = ", ".join(labels.get(t, t) for t in learned)
        reasons.append((effect, f"Like events you've gone to: {names}"))
    return reasons


def _proximity_reason(signals: EventSignals, effect: float) -> str | None:
    if signals.distance_km is None:
        return None
    away = format_distance(signals.distance_km)
    if effect >= NOTABLE_EFFECT:
        return f"Close to you ({away} away)"
    if effect <= -NOTABLE_EFFECT:
        return f"Farther than you usually go ({away} away)"
    return f"{away} away"


def format_distance(km: float) -> str:
    if km < 0.05:
        return "under 50 m"
    if km < 1:
        return f"{round(km * 1000 / 50) * 50:.0f} m"
    if km < 10:
        return f"{km:.1f} km"
    return f"{km:.0f} km"
