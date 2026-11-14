"""The ranking model's maths: distances, curves, and the likelihood estimate.
Property-based tests (Hypothesis) check invariants over thousands of inputs,
including extreme and awkward values."""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.graph.ranking import (
    DEFAULT_WEIGHTS,
    MIN_TRAVEL_SCALE_KM,
    EventSignals,
    RankingWeights,
    closeness,
    cosine,
    estimate,
    sigmoid,
    soonness,
    staleness,
    taste_match,
)
from backend.objects import DomainRuleError, GeoPoint, VerificationStatus, haversine_km

latitudes = st.floats(min_value=-90, max_value=90, allow_nan=False)
longitudes = st.floats(min_value=-180, max_value=180, allow_nan=False)
points = st.builds(GeoPoint, latitudes, longitudes)
distances = st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False)
scales = st.floats(min_value=1e-9, max_value=1e5, allow_nan=False, allow_infinity=False)
days = st.floats(min_value=-1e5, max_value=1e5, allow_nan=False, allow_infinity=False)
units = st.floats(min_value=0, max_value=1, allow_nan=False)
statuses = st.sampled_from(list(VerificationStatus))


@st.composite
def event_signals(draw, **fixed):
    values = {
        "friends_going": draw(st.integers(min_value=0, max_value=10_000)),
        "my_org_hosts": draw(st.booleans()),
        "affinity": draw(units),
        "distance_km": draw(st.none() | distances),
        "travel_scale_km": draw(scales),
        "days_until": draw(days),
        "host_trust": draw(units),
        "host_inactive_days": draw(st.none() | days),
        "verification": draw(statuses),
        "is_cancelled": draw(st.booleans()),
        "request_match": draw(st.none() | units),
    }
    values.update(fixed)
    return EventSignals(**values)


# --- geography -----------------------------------------------------------------


def test_known_distances():
    boston, nyc = GeoPoint(42.3601, -71.0589), GeoPoint(40.7128, -74.0060)
    assert haversine_km(boston, nyc) == pytest.approx(306, abs=2)
    assert haversine_km(boston, boston) == 0.0
    pole_to_pole = haversine_km(GeoPoint(90, 0), GeoPoint(-90, 0))
    assert pole_to_pole == pytest.approx(math.pi * 6371.0088, rel=1e-9)


def test_across_the_date_line_is_short():
    west, east = GeoPoint(0, 179.9), GeoPoint(0, -179.9)
    assert haversine_km(west, east) == pytest.approx(22.24, abs=0.05)


def test_antipodes_do_not_crash():
    assert haversine_km(GeoPoint(0, 0), GeoPoint(0, 180)) == pytest.approx(20015.1, abs=0.5)


@pytest.mark.parametrize(("lat", "lon"), [
    (91, 0), (-90.0001, 0), (0, 180.5), (0, -181),
    (math.nan, 0), (0, math.inf), (-math.inf, 0), (True, 0), ("42", 0), (None, 0),
])
def test_invalid_points_are_rejected(lat, lon):
    with pytest.raises(DomainRuleError):
        GeoPoint(lat, lon)


@given(points, points)
def test_distance_is_symmetric_non_negative_and_bounded(a, b):
    d = haversine_km(a, b)
    assert 0 <= d <= math.pi * 6371.0088 + 1e-6
    assert d == pytest.approx(haversine_km(b, a), abs=1e-6)


@given(points, points, points)
@settings(max_examples=300)
def test_triangle_inequality(a, b, c):
    assert haversine_km(a, c) <= haversine_km(a, b) + haversine_km(b, c) + 1e-6


# --- curves --------------------------------------------------------------------


@given(st.floats(allow_nan=False))
def test_sigmoid_is_bounded_and_never_overflows(x):
    y = sigmoid(x)
    assert 0.0 <= y <= 1.0
    assert math.isfinite(y)


@given(st.floats(min_value=-700, max_value=700), st.floats(min_value=0, max_value=50))
def test_sigmoid_is_monotonic(x, step):
    assert sigmoid(x + step) >= sigmoid(x)


def test_sigmoid_reference_values():
    assert sigmoid(0) == 0.5
    assert sigmoid(1e308) == 1.0
    assert sigmoid(-1e308) == 0.0
    assert sigmoid(2) + sigmoid(-2) == pytest.approx(1.0)


@given(distances, scales)
def test_closeness_is_in_unit_range(d, s):
    assert 0.0 <= closeness(d, s) <= 1.0


@given(distances, distances, scales)
def test_closeness_never_rises_with_distance(d1, d2, s):
    near, far = sorted((d1, d2))
    assert closeness(near, s) >= closeness(far, s)


@given(st.floats(min_value=MIN_TRAVEL_SCALE_KM, max_value=1e4))
def test_closeness_is_one_half_at_your_usual_distance(s):
    assert closeness(s, s) == pytest.approx(0.5)
    assert closeness(0, s) == 1.0


def test_closeness_floors_tiny_scales_and_handles_huge_distances():
    assert closeness(MIN_TRAVEL_SCALE_KM, 1e-12) == pytest.approx(0.5)
    assert closeness(1e308, 1.0) == 0.0
    assert closeness(-5, 1.0) == 1.0  # treated as zero, never above 1


@given(days)
def test_soonness_bounds(d):
    assert 0.0 <= soonness(d) <= 1.0


def test_soonness_reference_values():
    assert soonness(0) == 1.0
    assert soonness(-3) == 1.0  # already under way
    assert soonness(7) == pytest.approx(math.exp(-1))
    assert soonness(1) > soonness(2) > soonness(30)


@given(days)
def test_staleness_bounds(d):
    assert 0.0 <= staleness(d) <= 1.0


def test_staleness_reference_values():
    assert staleness(0) == staleness(30) == 0.0
    assert 0 < staleness(31) < staleness(90) < staleness(400) < 1
    assert staleness(-100) == 0.0


# --- similarity ----------------------------------------------------------------


tag_vectors = st.dictionaries(
    st.sampled_from(list("abcdefgh")), st.floats(min_value=0, max_value=1e6), max_size=8
)


@given(tag_vectors, tag_vectors)
def test_cosine_is_bounded_and_symmetric(a, b):
    assert 0.0 <= cosine(a, b) <= 1.0
    assert cosine(a, b) == pytest.approx(cosine(b, a))


def test_cosine_reference_values():
    assert cosine({}, {"a": 1}) == 0.0
    assert cosine({"a": 0.0}, {"a": 1}) == 0.0
    assert cosine({"a": 2}, {"a": 1}) == pytest.approx(1.0)
    assert cosine({"a": 1}, {"b": 1}) == 0.0
    assert cosine({"a": 1, "b": 1}, {"a": 1}) == pytest.approx(1 / math.sqrt(2))


def test_cosine_survives_overflowing_weights():
    assert cosine({"a": 1e308, "b": 1e308}, {"a": 1.0}) == 0.0  # norm overflows: no match


@given(tag_vectors, st.sets(st.sampled_from(list("abcdefgh"))))
def test_taste_match_is_bounded(taste, tags):
    assert 0.0 <= taste_match(taste, tags) <= 1.0


def test_chosen_interests_count_more_than_org_tags_alone():
    assert taste_match({"robotics": 1.0}, {"robotics"}) == pytest.approx(1.0)
    assert taste_match({"robotics": 0.5}, {"robotics"}) == pytest.approx(0.5)
    assert taste_match({}, {"robotics"}) == 0.0
    assert taste_match({"robotics": 1.0}, set()) == 0.0


# --- the estimate --------------------------------------------------------------


@given(event_signals())
@settings(max_examples=500)
def test_likelihood_is_always_a_finite_probability(signals):
    result = estimate(signals)
    assert 0.0 <= result.likelihood <= 1.0
    assert all(math.isfinite(c.effect) for c in result.contributions)


@given(event_signals(is_cancelled=True))
def test_cancelled_events_are_always_zero(signals):
    assert estimate(signals).likelihood == 0.0


def _monotone(field, low, high, signals):
    a = estimate(EventSignals(**{**vars(signals), field: low})).likelihood
    b = estimate(EventSignals(**{**vars(signals), field: high})).likelihood
    return a <= b + 1e-12


@given(event_signals(is_cancelled=False), st.integers(0, 500), st.integers(0, 500))
def test_more_friends_never_lowers_the_estimate(signals, a, b):
    assert _monotone("friends_going", min(a, b), max(a, b), signals)


@given(event_signals(is_cancelled=False), units, units)
def test_better_taste_match_never_lowers_the_estimate(signals, a, b):
    assert _monotone("affinity", min(a, b), max(a, b), signals)


@given(event_signals(is_cancelled=False), distances, distances)
def test_closer_never_lowers_the_estimate(signals, a, b):
    assert _monotone("distance_km", max(a, b), min(a, b), signals)


@given(event_signals(is_cancelled=False), units, units)
def test_more_trusted_hosts_never_lower_the_estimate(signals, a, b):
    assert _monotone("host_trust", min(a, b), max(a, b), signals)


@given(event_signals(is_cancelled=False), days, days)
def test_sooner_never_lowers_the_estimate(signals, a, b):
    assert _monotone("days_until", max(a, b), min(a, b), signals)


@given(event_signals(is_cancelled=False), days, days)
def test_a_longer_quiet_host_never_raises_the_estimate(signals, a, b):
    assert _monotone("host_inactive_days", max(a, b), min(a, b), signals)


@given(event_signals(is_cancelled=False), units, units)
def test_a_better_fit_to_the_request_never_lowers_the_estimate(signals, a, b):
    assert _monotone("request_match", min(a, b), max(a, b), signals)


@given(event_signals(is_cancelled=False))
def test_no_request_and_a_neutral_fit_are_the_same(signals):
    none = estimate(EventSignals(**{**vars(signals), "request_match": None})).likelihood
    half = estimate(EventSignals(**{**vars(signals), "request_match": 0.5})).likelihood
    assert none == pytest.approx(half)


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan])
def test_impossible_request_matches_are_rejected(bad):
    with pytest.raises(DomainRuleError):
        EventSignals(request_match=bad)


@given(event_signals(is_cancelled=False))
def test_your_org_hosting_never_lowers_the_estimate(signals):
    assert _monotone("my_org_hosts", False, True, signals)


@given(event_signals(is_cancelled=False))
def test_disputed_details_lower_and_verified_raise(signals):
    by_status = {
        status: estimate(EventSignals(**{**vars(signals), "verification": status})).likelihood
        for status in VerificationStatus
    }
    assert (
        by_status[VerificationStatus.DISPUTED]
        <= by_status[VerificationStatus.UNVERIFIED]
        <= by_status[VerificationStatus.VERIFIED]
    )


@given(event_signals(is_cancelled=False))
def test_unknown_distance_is_neutral(signals):
    unknown = estimate(EventSignals(**{**vars(signals), "distance_km": None}))
    assert unknown.effect("proximity") == 0.0
    at_usual = estimate(
        EventSignals(**{**vars(signals), "distance_km": max(signals.travel_scale_km,
                                                            MIN_TRAVEL_SCALE_KM)})
    )
    assert at_usual.effect("proximity") == pytest.approx(0.0, abs=1e-9)


@given(event_signals(is_cancelled=False))
def test_contributions_add_up_to_the_likelihood(signals):
    result = estimate(signals)
    logit = DEFAULT_WEIGHTS.bias + sum(c.effect for c in result.contributions)
    assert result.likelihood == pytest.approx(sigmoid(logit))


def test_diminishing_returns_from_friends():
    one, two, ten = (
        estimate(EventSignals(friends_going=n)).effect("friends") for n in (1, 2, 10)
    )
    assert one > 0
    assert two - one < one  # the second friend adds less than the first
    assert ten < 10 * one


# --- validation ----------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    {"friends_going": -1},
    {"affinity": 1.5}, {"affinity": -0.1}, {"affinity": math.nan},
    {"host_trust": 2.0},
    {"distance_km": -1.0}, {"distance_km": math.inf}, {"distance_km": math.nan},
    {"travel_scale_km": 0.0}, {"travel_scale_km": -2.0}, {"travel_scale_km": math.inf},
    {"days_until": math.nan}, {"days_until": math.inf},
    {"host_inactive_days": math.nan},
])
def test_impossible_signals_are_rejected(bad):
    with pytest.raises(DomainRuleError):
        EventSignals(**bad)


@pytest.mark.parametrize("bad", [
    {"friends": math.nan}, {"bias": math.inf}, {"proximity": True}, {"affinity": "2"},
])
def test_weights_must_be_finite_numbers(bad):
    with pytest.raises(DomainRuleError):
        RankingWeights(**bad)


def test_custom_weights_change_the_estimate():
    signals = EventSignals(friends_going=3)
    louder = RankingWeights(friends=10.0)
    assert estimate(signals, louder).likelihood > estimate(signals).likelihood
    silent = RankingWeights(**{name: 0.0 for name in vars(DEFAULT_WEIGHTS)})
    assert estimate(signals, silent).likelihood == 0.5
