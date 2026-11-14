from dataclasses import replace
from datetime import timedelta

import pytest

from backend.graph.discovery import DiscoveryQuery
from backend.objects import (
    DomainRuleError,
    Event,
    HostRole,
    Hosts,
    Organization,
    OrgKind,
    PartOfKind,
    RsvpStatus,
    Tag,
    Visibility,
)

from .conftest import NOW, STAFF, make_event, make_org, staff_provenance


def publish(world, title: str, org=None, admin=None, days: int = 3, **extra) -> Event:
    org = org or world.chapter
    admin = admin or world.alice
    event = replace(make_event(title, days_ahead=days), **extra)
    world.graph.events.create(event, org.id, admin.id)
    return world.graph.events.publish(event.id, admin.id, NOW)


def titles(page) -> list[str]:
    return [item.event.title for item in page.items]


def discover(world, viewer=None, **filters):
    viewer_id = viewer.id if viewer else None
    return world.graph.discovery.search(DiscoveryQuery(viewer_id=viewer_id, **filters), NOW)


def make_tag(world, slug: str) -> Tag:
    return world.graph.store.save_node(
        Tag(slug=slug, label=slug.title(), provenance=staff_provenance())
    )


# --- what is discoverable ----------------------------------------------------


def test_only_published_upcoming_events_are_listed(world):
    publish(world, "Upcoming")
    world.graph.events.create(make_event("Draft"), world.chapter.id, world.alice.id)
    later = NOW + timedelta(days=30)
    page = world.graph.discovery.search(DiscoveryQuery(), later)
    assert titles(page) == []
    assert titles(discover(world)) == ["Upcoming"]


def test_cancelled_events_shown_last_and_flagged_until_end(world):
    publish(world, "On")
    off = publish(world, "Off", days=1)
    world.graph.events.cancel(off.id, world.alice.id)
    page = discover(world)
    assert titles(page) == ["On", "Off"]
    assert page.items[1].is_cancelled
    assert page.items[1].reasons[0] == "Cancelled: this event is not happening"
    assert titles(discover(world, include_cancelled=False)) == ["On"]
    during = world.graph.discovery.search(DiscoveryQuery(), off.starts_at)
    assert "Off" in titles(during)
    assert "Off" not in titles(world.graph.discovery.search(DiscoveryQuery(), off.ends_at))


def test_visibility_is_enforced(world):
    publish(world, "Members only", visibility=Visibility.MEMBERS)
    publish(world, "Logged in", visibility=Visibility.NETWORK)
    assert titles(discover(world)) == []
    assert titles(discover(world, viewer=world.carol)) == ["Logged in"]
    assert set(titles(discover(world, viewer=world.alice))) == {"Members only", "Logged in"}


def test_events_of_a_hidden_host_org_are_hidden(world):
    publish(world, "Secret society social")
    hidden = world.graph.store.node(world.chapter.id, Organization).with_visibility(
        Visibility.MEMBERS
    )
    world.graph.store.save_node(hidden)
    assert titles(discover(world, viewer=world.carol)) == []
    assert titles(discover(world, viewer=world.alice)) == ["Secret society social"]


# --- filters -----------------------------------------------------------------


def test_text_filter_matches_title_description_and_location(world):
    publish(world, "Robot build night")
    publish(world, "Bake sale", description="Fund the robot team")
    publish(world, "Mixer", location="Robotics lab")
    publish(world, "Formal")
    assert set(titles(discover(world, text="ROBOT"))) == {
        "Robot build night", "Bake sale", "Mixer",
    }


def test_tag_filter_includes_tags_inherited_from_host_org(world):
    robotics = make_tag(world, "robotics")
    tagged = publish(world, "Tagged event")
    publish(world, "Untagged event", org=world.national, admin=world.bob)
    world.graph.events.tag(tagged.id, robotics.id, world.alice.id, NOW)
    assert titles(discover(world, tag_ids=(robotics.id,))) == ["Tagged event"]

    world.graph.events.tag(world.national.id, robotics.id, world.bob.id, NOW)
    assert set(titles(discover(world, tag_ids=(robotics.id,)))) == {
        "Tagged event", "Untagged event",
    }


def test_org_filter_can_include_descendant_orgs(world):
    world.graph.hierarchy.staff_link(
        world.chapter.id, world.council.id, PartOfKind.MEMBER_OF_COUNCIL, STAFF, NOW
    )
    publish(world, "Chapter event")
    publish(world, "National event", org=world.national, admin=world.bob)
    assert titles(discover(world, org_id=world.council.id)) == ["Chapter event"]
    assert titles(discover(world, org_id=world.council.id, include_related=False)) == []


def test_time_window_filter(world):
    publish(world, "Soon", days=1)
    publish(world, "Later", days=20)
    page = discover(world, starts_before=NOW + timedelta(days=7))
    assert titles(page) == ["Soon"]
    page = discover(world, starts_after=NOW + timedelta(days=7))
    assert titles(page) == ["Later"]


# --- ranking and explanations ------------------------------------------------


def test_unclaimed_and_stale_hosts_rank_lower_with_reasons(world):
    unclaimed = make_org(world.graph, "Chess Club", OrgKind.CLUB)
    publish(world, "Verified host event", days=5)
    event = make_event("Unclaimed host event", days_ahead=5)
    world.graph.store.save_node(event.published(NOW))
    world.graph.store.save_edge(
        Hosts(source_id=unclaimed.id, target_id=event.id, role=HostRole.PRIMARY,
              accepted=True, provenance=staff_provenance())
    )
    page = discover(world)
    assert titles(page) == ["Verified host event", "Unclaimed host event"]
    assert any("verified" in r.lower() for r in page.items[0].reasons)
    assert any("unclaimed" in r.lower() for r in page.items[1].reasons)

    stale_now = NOW + timedelta(days=200)
    publish(world, "Far future", days=210)
    page = world.graph.discovery.search(DiscoveryQuery(), stale_now)
    assert titles(page) == ["Far future"]  # still listed, but flagged
    assert any("out of date" in r for r in page.items[0].reasons)


def test_preferred_tags_boost_and_explain(world):
    robotics = make_tag(world, "robotics")
    publish(world, "Sooner plain event", days=1)
    boosted = publish(world, "Robotics night", days=6)
    world.graph.events.tag(boosted.id, robotics.id, world.alice.id, NOW)
    page = discover(world, preferred_tag_ids=(robotics.id,))
    assert titles(page)[0] == "Robotics night"
    assert "Matches your interest: Robotics" in page.items[0].reasons


def test_sooner_events_rank_higher_when_otherwise_equal(world):
    publish(world, "Next month", days=25)
    publish(world, "Tomorrow", days=1)
    assert titles(discover(world)) == ["Tomorrow", "Next month"]


# --- paging and validation ---------------------------------------------------


def test_pagination(world):
    for day in range(1, 6):
        publish(world, f"Event {day}", days=day)
    page = discover(world, limit=2, offset=2)
    assert page.total == 5
    assert titles(page) == ["Event 3", "Event 4"]
    assert page.has_more


@pytest.mark.parametrize("bad", [
    {"limit": 0},
    {"limit": 101},
    {"offset": -1},
    {"text": "x" * 201},
    {"starts_after": NOW + timedelta(days=2), "starts_before": NOW},
])
def test_invalid_queries_are_rejected(bad):
    with pytest.raises(DomainRuleError):
        DiscoveryQuery(**bad)


# --- the plan's formula: my orgs and their tags ------------------------------


def join(world, person, org, admin):
    edge = world.graph.membership.request(person.id, org.id, NOW)
    world.graph.membership.confirm(edge.id, admin.id)


def by_title(page) -> dict:
    return {item.event.title: item for item in page.items}


def test_events_hosted_by_my_org_rank_higher_and_say_so(world):
    join(world, world.carol, world.chapter, world.alice)
    publish(world, "Chapter social")
    publish(world, "National social", org=world.national, admin=world.bob)
    items = by_title(discover(world, world.carol))
    assert items["Chapter social"].score > items["National social"].score
    assert "Hosted by your org: Alpha Chapter" in items["Chapter social"].reasons
    effect = {f.name: f.effect for f in items["Chapter social"].factors}
    assert effect["my_org_host"] > 0


def test_co_hosting_counts_and_the_bonus_is_given_once(world):
    join(world, world.carol, world.chapter, world.alice)
    join(world, world.carol, world.national, world.bob)
    event = publish(world, "Joint 5K", org=world.national, admin=world.bob)
    invite = world.graph.events.invite_co_host(event.id, world.chapter.id, world.bob.id, NOW)
    world.graph.events.accept_co_host(invite.id, world.alice.id)
    publish(world, "Plain", org=world.national, admin=world.bob)
    items = by_title(discover(world, world.alice))
    assert items["Joint 5K"].score > items["Plain"].score
    carol = by_title(discover(world, world.carol))  # member of both hosts
    values = {f.name: f.value for f in carol["Joint 5K"].factors}
    assert values["my_org_host"] == 1.0  # counted once, not once per org
    reasons = carol["Joint 5K"].reasons
    assert sum(r.startswith("Hosted by your org") for r in reasons) == 1


def test_pending_memberships_and_logged_out_visitors_get_no_org_bonus(world):
    world.graph.membership.request(world.carol.id, world.chapter.id, NOW)
    publish(world, "Chapter social")
    publish(world, "National social", org=world.national, admin=world.bob)
    for viewer in (world.carol, None):
        items = by_title(discover(world, viewer))
        assert items["Chapter social"].score == pytest.approx(items["National social"].score)


def test_sharing_more_of_my_orgs_tags_ranks_higher(world):
    robotics, social = make_tag(world, "robotics"), make_tag(world, "social")
    join(world, world.carol, world.chapter, world.alice)
    for tag in (robotics, social):
        world.graph.events.tag(world.chapter.id, tag.id, world.alice.id, NOW)
    one = publish(world, "One shared tag", org=world.national, admin=world.bob)
    two = publish(world, "Two shared tags", org=world.national, admin=world.bob)
    publish(world, "No shared tags", org=world.national, admin=world.bob)
    world.graph.events.tag(one.id, robotics.id, world.bob.id, NOW)
    for tag in (robotics, social):
        world.graph.events.tag(two.id, tag.id, world.bob.id, NOW)
    items = by_title(discover(world, world.carol))
    assert (
        items["Two shared tags"].score
        > items["One shared tag"].score
        > items["No shared tags"].score
    )
    assert "Like your orgs: Robotics, Social" in items["Two shared tags"].reasons


def test_network_order_friends_beat_my_org_which_beats_shared_tags(world):
    robotics = make_tag(world, "robotics")
    join(world, world.carol, world.chapter, world.alice)
    world.graph.events.tag(world.chapter.id, robotics.id, world.alice.id, NOW)
    friends = publish(world, "Friends going", org=world.national, admin=world.bob)
    publish(world, "My org hosts")  # chapter event: your org, and its tag
    tagged = publish(world, "Shares a tag", org=world.national, admin=world.bob)
    world.graph.events.tag(tagged.id, robotics.id, world.bob.id, NOW)
    for friend in (world.alice, world.bob):
        request = world.graph.connections.request(world.carol.id, friend.id, NOW)
        world.graph.connections.accept(request.id, friend.id)
        world.graph.rsvps.respond(friends.id, friend.id, RsvpStatus.GOING, NOW)
    assert titles(discover(world, world.carol)) == [
        "Friends going", "My org hosts", "Shares a tag",
    ]
