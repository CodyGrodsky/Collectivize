from datetime import datetime, timedelta

import pytest

from backend.objects import (
    DomainRuleError,
    EventSeries,
    EventStatus,
    Freshness,
    Organization,
    OrgKind,
    Visibility,
    most_restrictive,
)

from .conftest import NOW, make_event, staff_provenance


def test_most_restrictive_picks_narrowest_level():
    assert most_restrictive(Visibility.PUBLIC, Visibility.MEMBERS) is Visibility.MEMBERS


def test_naive_datetimes_are_rejected():
    with pytest.raises(DomainRuleError):
        make_event().revised(NOW, starts_at=datetime(2030, 1, 1))


def test_org_freshness_uses_latest_activity():
    org = Organization(name="Club", kind=OrgKind.CLUB, provenance=staff_provenance())
    assert org.freshness(NOW + timedelta(days=10)) is Freshness.ACTIVE
    assert org.freshness(NOW + timedelta(days=45)) is Freshness.QUIET
    assert org.freshness(NOW + timedelta(days=120)) is Freshness.STALE
    revived = org.with_event_published(NOW + timedelta(days=100))
    assert revived.freshness(NOW + timedelta(days=120)) is Freshness.ACTIVE


def test_org_rejects_non_http_urls():
    with pytest.raises(DomainRuleError):
        Organization(
            name="Club",
            kind=OrgKind.CLUB,
            website_url="javascript:alert(1)",
            provenance=staff_provenance(),
        )


def test_past_event_cannot_be_published():
    event = make_event()
    with pytest.raises(DomainRuleError):
        event.published(event.starts_at + timedelta(minutes=1))


def test_published_event_logs_time_and_location_changes_only():
    event = make_event().published(NOW)
    revised = event.revised(NOW, location="Room 101", title="New title")
    assert [c.field_name for c in revised.change_log] == ["location"]
    assert make_event() is not revised  # original untouched


def test_demo_flag_cannot_be_edited():
    with pytest.raises(DomainRuleError):
        make_event().revised(NOW, is_demo=False)


def test_cancelled_event_visible_until_original_start():
    event = make_event().published(NOW).cancelled()
    assert event.is_discoverable(NOW)
    assert not event.is_discoverable(event.starts_at)


def test_published_event_reports_ended_after_end_time():
    event = make_event().published(NOW)
    assert event.status_at(event.ends_at) is EventStatus.ENDED


def test_series_instances_inherit_defaults():
    series = EventSeries(
        owner_org_id="org",
        name="Weekly meeting",
        recurrence="Weekly on Tuesdays",
        default_title="Chapter meeting",
        default_location="Hall",
        default_duration=timedelta(hours=1),
        provenance=staff_provenance(),
    )
    event = series.instantiate(NOW + timedelta(days=1), staff_provenance(), location="Gym")
    assert (event.title, event.location) == ("Chapter meeting", "Gym")
    assert event.ends_at - event.starts_at == timedelta(hours=1)
