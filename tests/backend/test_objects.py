from datetime import datetime, timedelta

import pytest

from backend.objects import (
    DomainRuleError,
    EventSeries,
    EventStatus,
    Freshness,
    MemberOf,
    Organization,
    OrgKind,
    Person,
    Provenance,
    ProvenanceMethod,
    Tag,
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


def test_cancelled_event_visible_until_original_end():
    event = make_event().published(NOW).cancelled()
    assert event.is_discoverable(NOW)
    assert event.is_discoverable(event.starts_at)
    assert not event.is_discoverable(event.ends_at)


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


# --- edge cases --------------------------------------------------------------


@pytest.mark.parametrize("slug", ["Robotics", "robotics-", "-robotics", "robo--tics", "robo tics", ""])
def test_tag_rejects_non_kebab_case_slugs(slug):
    with pytest.raises(DomainRuleError):
        Tag(slug=slug, label="Robotics", provenance=staff_provenance())


@pytest.mark.parametrize("build", [
    lambda: Organization(name="   ", kind=OrgKind.CLUB, provenance=staff_provenance()),
    lambda: Person(display_name="\t", provenance=staff_provenance()),
    lambda: Tag(slug="robotics", label=" ", provenance=staff_provenance()),
    lambda: make_event(title="  "),
])
def test_whitespace_only_names_are_rejected(build):
    with pytest.raises(DomainRuleError):
        build()


@pytest.mark.parametrize("url", ["http://", "ftp://example.com", "example.com", "https:/example.com"])
def test_org_rejects_malformed_calendar_feed_urls(url):
    with pytest.raises(DomainRuleError):
        Organization(
            name="Club", kind=OrgKind.CLUB, calendar_feed_url=url, provenance=staff_provenance()
        )


def test_edge_cannot_point_to_itself():
    with pytest.raises(DomainRuleError):
        MemberOf(source_id="x", target_id="x", provenance=staff_provenance())


def test_naive_provenance_timestamps_are_rejected():
    with pytest.raises(DomainRuleError):
        Provenance(created_by=None, method=ProvenanceMethod.STAFF, created_at=datetime(2026, 1, 1))


def test_provenance_goes_stale_only_after_max_age():
    provenance = staff_provenance()
    max_age = timedelta(days=30)
    assert not provenance.is_stale(NOW + max_age, max_age)
    assert provenance.is_stale(NOW + max_age + timedelta(seconds=1), max_age)
    assert not provenance.confirmed(NOW + max_age).is_stale(NOW + max_age * 2, max_age)


def test_org_freshness_boundaries_are_exclusive():
    org = Organization(name="Club", kind=OrgKind.CLUB, provenance=staff_provenance())
    second = timedelta(seconds=1)
    assert org.freshness(NOW + timedelta(days=30)) is Freshness.ACTIVE
    assert org.freshness(NOW + timedelta(days=30) + second) is Freshness.QUIET
    assert org.freshness(NOW + timedelta(days=90)) is Freshness.QUIET
    assert org.freshness(NOW + timedelta(days=90) + second) is Freshness.STALE


def test_event_must_end_strictly_after_it_starts():
    with pytest.raises(DomainRuleError):
        make_event().revised(NOW, ends_at=make_event().starts_at)


@pytest.mark.parametrize("capacity", [0, -5])
def test_event_capacity_must_be_positive(capacity):
    with pytest.raises(DomainRuleError):
        make_event().revised(NOW, capacity=capacity)


def test_event_cannot_be_published_at_its_start_time_or_twice():
    event = make_event()
    with pytest.raises(DomainRuleError):
        event.published(event.starts_at)
    with pytest.raises(DomainRuleError):
        event.published(NOW).published(NOW)


def test_cancelled_and_archived_events_are_frozen():
    for frozen in (make_event().cancelled(), make_event().archived()):
        with pytest.raises(DomainRuleError):
            frozen.revised(NOW, title="Renamed")
        with pytest.raises(DomainRuleError):
            frozen.cancelled()


def test_ended_event_cannot_be_edited():
    event = make_event().published(NOW)
    with pytest.raises(DomainRuleError):
        event.revised(event.ends_at, title="Too late")


def test_only_real_changes_to_published_events_are_logged():
    draft = make_event()
    assert draft.revised(NOW, location="Gym").change_log == ()
    published = draft.published(NOW)
    assert published.revised(NOW, location=published.location).change_log == ()


def test_only_published_events_derive_ended_status():
    draft = make_event()
    assert draft.status_at(draft.ends_at) is EventStatus.DRAFT
    published = draft.published(NOW)
    assert published.status_at(published.ends_at - timedelta(seconds=1)) is EventStatus.PUBLISHED


def test_drafts_and_archived_events_are_never_discoverable():
    assert not make_event().is_discoverable(NOW)
    assert not make_event().published(NOW).archived().is_discoverable(NOW)


@pytest.mark.parametrize("duration", [timedelta(0), timedelta(hours=-1)])
def test_series_duration_must_be_positive(duration):
    with pytest.raises(DomainRuleError):
        EventSeries(
            owner_org_id="org", name="s", recurrence="weekly", default_title="t",
            default_duration=duration, provenance=staff_provenance(),
        )


def test_series_instances_carry_demo_flag_and_visibility():
    series = EventSeries(
        owner_org_id="org", name="s", recurrence="weekly", default_title="t",
        default_duration=timedelta(hours=1), is_demo=True,
        visibility=Visibility.MEMBERS, provenance=staff_provenance(),
    )
    event = series.instantiate(NOW + timedelta(days=1), staff_provenance())
    assert event.is_demo and event.visibility is Visibility.MEMBERS
    with pytest.raises(DomainRuleError):
        series.with_defaults(recurrence="daily")
