import pytest

from backend.config import DatabaseConfig, Environment
from backend.database import open_graph
from backend.graph import CollectivizeGraph, InMemoryGraphStore
from backend.objects import ClaimState, Event, EventStatus, InstanceOf, Organization
from backend.seed import DEMO_PREFIX, ProductionSeedError, seed_mock_data

from .conftest import NOW


def test_seed_refuses_production():
    graph = CollectivizeGraph(InMemoryGraphStore())
    with pytest.raises(ProductionSeedError):
        seed_mock_data(graph, Environment.PRODUCTION, NOW)


def test_seeded_test_db_is_clearly_fictional_and_rule_abiding(tmp_path):
    config = DatabaseConfig(Environment.TEST, tmp_path / "test.db")
    with open_graph(config) as graph:
        result = seed_mock_data(graph, config.environment, NOW)

    with open_graph(config) as graph:
        store = graph.store
        orgs = {k: store.node(i, Organization) for k, i in result.orgs.items()}
        events = {k: store.node(i, Event) for k, i in result.events.items()}

        assert all(o.name.startswith(DEMO_PREFIX) for o in orgs.values())
        assert all(e.is_demo for e in events.values())
        assert orgs["chess"].claim_state is ClaimState.UNCLAIMED
        assert orgs["chapter"].claim_state is ClaimState.CLAIMED

        parents = {o.id for o in graph.hierarchy.parents(result.orgs["chapter"])}
        assert parents == {result.orgs["national"], result.orgs["ifc"]}
        assert len(graph.series.instances(_series_id(graph, result))) == 4
        assert graph.events.host_org_ids(result.events["fundraiser"]) == {
            result.orgs["chapter"], result.orgs["national"],
        }
        assert graph.events.host_ids(result.events["study_jam"]) == {
            result.people["drew"], result.orgs["robotics"],
        }
        waiting = graph.co_hosting.pending(result.events["demo_day"], result.people["casey"])
        assert [h.source_id for h in waiting] == [result.people["emery"]]
        robotics = graph.calendar.host_events(result.orgs["robotics"], None)
        assert result.events["study_jam"] in {e.id for e in robotics.public}
        assert events["cancelled_mixer"].status is EventStatus.CANCELLED
        assert events["draft_formal"].status is EventStatus.DRAFT
        assert not graph.membership.is_member(result.people["emery"], result.orgs["robotics"])


def _series_id(graph: CollectivizeGraph, result) -> str:
    edge = graph.store.edges(InstanceOf, source_id=result.events["meeting_1"])[0]
    return edge.target_id


def test_seeded_demo_invite_joins_you_to_drew():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    joined = graph.invites.redeem(result.invite_code, "demo@northeastern.edu", "You", NOW)
    assert graph.connections.are_connected(joined.person.id, result.people["drew"])
    assert graph.integrity.audit() == ()


def test_seeded_demo_has_places_tastes_and_travel_habits():
    from backend.graph.preferences import typical_trip_km
    from backend.objects import Event, InterestedIn, Person
    from backend.seed import CAMPUS

    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    crawl = graph.store.node(result.events["food_crawl"], Event)
    assert 5 < CAMPUS.distance_km(crawl.coordinates) < 7
    people = graph.store.nodes(Person)
    assert sum(typical_trip_km(p) is not None for p in people) >= 10
    assert len(graph.store.edges(InterestedIn)) >= 20
    assert all(p.travel_log_km is None or p.travel_samples > 0 for p in people)


def test_bulk_world_is_large_fictional_and_rule_abiding():
    from backend.objects import Person, Rsvp
    from backend.seed_bulk import SMALL

    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW, SMALL)
    bulk = result.bulk
    store = graph.store

    assert graph.integrity.audit() == ()
    orgs = store.nodes(Organization)
    events = store.nodes(Event)
    assert len(orgs) > len(result.orgs) + 15
    assert len(store.nodes(Person)) > 40
    assert all(o.name.startswith(DEMO_PREFIX) for o in orgs)
    assert all(e.is_demo for e in events)
    # The first generated campus shares the hand-built university.
    assert result.orgs["university"] not in bulk.orgs.values()
    statuses = {e.status_at(NOW) for e in events if e.id in set(bulk.events)}
    assert {EventStatus.ENDED, EventStatus.PUBLISHED} <= statuses
    assert any(store.edges(Rsvp, target_id=i) for i in bulk.events)


def test_bulk_world_is_the_same_every_time():
    from backend.seed_bulk import SMALL

    def titles():
        graph = CollectivizeGraph()
        seed_mock_data(graph, Environment.TEST, NOW, SMALL)
        return sorted((e.title, e.starts_at) for e in graph.store.nodes(Event))

    assert titles() == titles()
