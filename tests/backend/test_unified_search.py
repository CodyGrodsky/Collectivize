"""The unified search engine (v1.1 spec 4.4 / 4.5): reading queries
(keywords vs sentences), and searching people, orgs and events together.

No real API calls, ever: sentence reading uses fake extractors here, the
Claude extractor is tested with a fake client, and conftest blocks the
real Anthropic client as a backstop."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import anthropic
import pytest

from backend.app_api import AppApi
from backend.config import Environment
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.graph.ai_search import InterpreterError
from backend.graph.query_understanding import (
    ClaudeQueryExtractor,
    ExtractRequest,
    Extraction,
    QueryReader,
    classify,
    local_extract,
    parse_extraction,
)
from backend.graph.unified_search import (
    SearchFilters,
    UnifiedSearchService,
    keyword_fit,
)
from backend.seed import seed_mock_data

from .conftest import NOW

TODAY = date(2026, 11, 13)  # NOW's date on campus: a Friday


# --- fakes ---------------------------------------------------------------------------


class FakeExtractor:
    """Returns a fixed reading and records every call."""

    name = "fake"

    def __init__(self, answer: Extraction | None = None, error: Exception | None = None):
        self.answer = answer or Extraction()
        self.error = error
        self.calls: list[ExtractRequest] = []

    def extract(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.answer


class NeverCalled:
    name = "never"

    def extract(self, request):
        raise AssertionError("keyword searches must not use the extractor")


def fake_client(text=None, stop_reason="end_turn", error=None):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return SimpleNamespace(stop_reason=stop_reason,
                               content=[SimpleNamespace(type="text", text=text)])

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


@pytest.fixture(scope="module")
def world():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)
    return graph, result


def engine(graph, extractor=None):
    return UnifiedSearchService(graph.store, graph.discovery, graph.visibility, graph.privacy,
                                QueryReader(extractor))


def search(graph, viewer, text, extractor=None, **filters):
    return engine(graph, extractor).search(viewer, text, SearchFilters(**filters), NOW)


def kinds(page):
    return {r.kind for r in page.results}


# --- the quick look: keywords or a sentence? -----------------------------------------


@pytest.mark.parametrize(("text", "mode"), [
    ("", "empty"), ("   ", "empty"), ("?!", "empty"),
    ("pottery", "keywords"), ("surf club", "keywords"), ("Robot demo day", "keywords"),
    ("chess people", "keywords"),
    ("something chill on saturday", "sentence"),
    ("find people into chess", "sentence"),
    ("people to invite to my game night", "sentence"),
    ("who likes surfing?", "sentence"),
    ("what's on saturday", "sentence"),
])
def test_classify(text, mode):
    assert classify(text) == mode


# --- reading keywords locally ----------------------------------------------------------


def test_type_words_become_filters_not_keywords():
    read = local_extract("surf club", TODAY)
    assert read.keywords == ("surf",) and read.types == {"orgs"}
    read = local_extract("chess people", TODAY)
    assert read.keywords == ("chess",) and read.types == {"people"}


def test_day_words_become_a_date_and_imply_events():
    saturday = local_extract("what's on saturday", TODAY)
    assert saturday.day == date(2026, 11, 14) and saturday.types == {"events"}
    assert saturday.keywords == ()
    assert local_extract("friday", TODAY).day == TODAY  # today counts
    assert local_extract("tomorrow pottery", TODAY).day == date(2026, 11, 14)
    assert local_extract("tonight", TODAY).day == TODAY
    assert local_extract("2026-11-21", TODAY).day == date(2026, 11, 21)
    assert local_extract("2026-02-31", TODAY).day is None


def test_topic_words_stay_keywords():
    assert local_extract("board games party", TODAY).keywords == ("board", "games", "party")
    assert local_extract("pottery workshop", TODAY).keywords == ("pottery", "workshop")


# --- reading sentences -----------------------------------------------------------------


def test_keywords_never_reach_the_extractor():
    reading = QueryReader(NeverCalled()).read("surf club", TODAY)
    assert reading.mode == "keywords" and not reading.used_ai


def test_sentences_use_the_extractor_once_then_the_cache():
    fake = FakeExtractor(Extraction(keywords=("chess",), types=frozenset({"people"})))
    reader = QueryReader(fake)
    first = reader.read("find people who play chess", TODAY, ("Social",))
    again = reader.read("Find  people who play CHESS", TODAY, ("Social",))
    assert first.mode == "sentence" and first.used_ai and again.used_ai
    assert first.extraction.keywords == ("chess",) and first.extraction.types == {"people"}
    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.text == "find people who play chess" and request.today == TODAY
    assert request.tag_labels == ("Social",)


def test_a_failing_extractor_falls_back_to_the_local_reading():
    fake = FakeExtractor(error=InterpreterError("offline"))
    reading = QueryReader(fake).read("looking for pottery people", TODAY)
    assert not reading.used_ai and reading.note
    assert reading.extraction.keywords == ("pottery",) and reading.extraction.types == {"people"}


def test_without_an_extractor_sentences_are_read_locally():
    reading = QueryReader(None).read("something with robots on saturday", TODAY)
    assert reading.mode == "sentence" and not reading.used_ai
    assert reading.extraction.keywords == ("robots",)
    assert reading.extraction.day == date(2026, 11, 14)


def test_extractor_output_is_never_trusted():
    fake = FakeExtractor(Extraction(
        keywords=("Chess", "chess", "the", "x" * 80, *[f"k{i}" for i in range(20)]),
        types=frozenset({"people", "spaceships"}),
        tags=("social", "Made Up"),
        day=date(2031, 1, 1),
        summary="  a   b " * 50,
    ))
    read = QueryReader(fake).read("i want to find chess people", TODAY, ("Social",)).extraction
    assert read.keywords[0] == "chess" and "the" not in read.keywords
    assert len(read.keywords) <= 8 and all(len(k) <= 30 for k in read.keywords)
    assert read.types == {"people"} and read.tags == ("Social",)
    assert read.day is None and len(read.summary) <= 120


def test_an_empty_extraction_falls_back_to_local():
    reading = QueryReader(FakeExtractor(Extraction())).read("looking for robotics stuff", TODAY)
    assert reading.extraction.keywords == ("robotics",)


@pytest.mark.parametrize("text", ["not json", "[1]", '{"keywords": 5}'])
def test_parse_extraction_rejects_or_tolerates_bad_shapes(text):
    if text == '{"keywords": 5}':
        assert parse_extraction(text).keywords == ()
    else:
        with pytest.raises(InterpreterError):
            parse_extraction(text)


def test_parse_extraction_reads_a_good_answer():
    read = parse_extraction('{"keywords": ["pottery", "ceramics"], "types": ["events"], '
                            '"tags": [], "date": "2026-11-14", "summary": "pottery on saturday"}')
    assert read.keywords == ("pottery", "ceramics") and read.types == {"events"}
    assert read.day == date(2026, 11, 14) and read.summary == "pottery on saturday"


# --- the Claude extractor, with a fake client (no network) -----------------------------


def test_claude_extractor_sends_only_the_text_date_and_topics():
    client, calls = fake_client('{"keywords": ["chess"], "types": ["people"], "tags": [], '
                                '"date": "", "summary": "chess players"}')
    extractor = ClaudeQueryExtractor(client=client)
    read = extractor.extract(ExtractRequest("people who play chess", TODAY, ("Social",)))
    assert read.keywords == ("chess",) and read.types == {"people"}
    sent = calls[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    message = sent["messages"][0]["content"]
    assert "people who play chess" in message and "2026-11-13" in message and "Social" in message


@pytest.mark.parametrize("client", [
    fake_client(stop_reason="refusal", text="{}")[0],
    fake_client(stop_reason="max_tokens", text="{")[0],
    fake_client(error=anthropic.AnthropicError("no key"))[0],
    fake_client(text="nonsense")[0],
])
def test_claude_extractor_failures_become_interpreter_errors(client):
    with pytest.raises(InterpreterError):
        ClaudeQueryExtractor(client=client).extract(ExtractRequest("x y z w", TODAY))


def test_without_a_client_the_blocked_api_falls_back(world):
    """No fake client: the real one is blocked in tests, so it falls back."""
    graph, r = world
    page = search(graph, r.people["drew"], "looking for robot things", ClaudeQueryExtractor())
    assert not page.understanding.used_ai and page.total > 0


# --- matching ----------------------------------------------------------------------------


def test_keyword_fit_weights_where_words_are_found():
    assert keyword_fit(["robot"], [("Robotics Club", 1.0)]) == 1.0  # shared stem
    assert keyword_fit(["robot"], [("", 1.0), ("we build robots", 0.6)]) == 0.6
    assert keyword_fit(["robot", "chess"], [("Robotics", 1.0)]) == 0.5
    assert keyword_fit(["demo day"], [("Robot demo day", 1.0)]) == 1.0
    assert keyword_fit(["zebra"], [("Robotics", 1.0)]) == 0.0
    assert keyword_fit(["ai"], [("Pottery and chairs", 1.0)]) == 0.0  # no stem for short words


# --- searching all three kinds -------------------------------------------------------------


def test_one_term_matches_people_orgs_and_events(world):
    graph, r = world
    page = search(graph, r.people["drew"], "robot")
    assert page.counts["person"] > 0 and page.counts["org"] > 0 and page.counts["event"] > 0
    ids = {res.id for res in engine(graph).search(r.people["drew"], "robot", SearchFilters(), NOW,
                                                  per_page=100).results}
    assert r.orgs["robotics"] in ids and r.events["demo_day"] in ids and r.people["carol"] in ids


def test_type_toggles(world):
    graph, r = world
    no_events = search(graph, r.people["drew"], "robot", types=frozenset({"people", "orgs"}))
    assert no_events.counts["event"] == 0 and no_events.counts["org"] > 0
    only_people = search(graph, r.people["drew"], "robot", types=frozenset({"people"}))
    assert kinds(only_people) == {"person"}


def test_closer_people_rank_higher(world):
    graph, r = world
    p = r.people
    # Alice and Drew both list "study spots"; for Bob, Alice is 1st degree, Drew 2nd.
    page = search(graph, p["bob"], "study spots", types=frozenset({"people"}))
    order = [res.id for res in page.results]
    assert order.index(p["alice"]) < order.index(p["drew"])


def test_people_are_never_matched_on_their_orgs(world):
    graph, r = world
    page = engine(graph).search(r.people["carol"], "cooking", SearchFilters(types=frozenset({"people"})),
                                NOW, per_page=100)
    assert r.people["alice"] not in {res.id for res in page.results}  # she's in Cooking Club


def test_no_query_and_no_filters_is_discover(world):
    graph, r = world
    page = search(graph, r.people["drew"], "")
    assert page.understanding.mode == "empty" and kinds(page) == {"event"} and page.total > 0


def test_browsing_people_lists_your_network_closest_first(world):
    graph, r = world
    page = engine(graph).search(r.people["alice"], "", SearchFilters(types=frozenset({"people"})),
                                NOW, per_page=200)
    ids = [res.id for res in page.results]
    assert ids[0] in graph.connections.connections_of(r.people["alice"])
    assert r.people["alice"] not in ids


def test_a_day_filter_shows_only_that_days_events(world):
    graph, r = world
    zone = ZoneInfo("America/New_York")
    day = graph.store.any_node(r.events["demo_day"]).starts_at.astimezone(zone).date()
    page = engine(graph).search(r.people["drew"], "", SearchFilters(day=day), NOW, per_page=100)
    assert kinds(page) == {"event"} and r.events["demo_day"] in {res.id for res in page.results}
    assert all(res.event.event.starts_at.astimezone(zone).date() == day for res in page.results)


def test_ticked_filters_beat_what_the_sentence_said(world):
    graph, r = world
    fake = FakeExtractor(Extraction(keywords=("robot",), types=frozenset({"people"})))
    page = search(graph, r.people["drew"], "find me some robot people", fake,
                  types=frozenset({"orgs"}))
    assert page.applied.types == {"orgs"} and kinds(page) == {"org"}


def test_a_sentence_searches_its_main_ideas(world):
    graph, r = world
    fake = FakeExtractor(Extraction(keywords=("robot",), types=frozenset({"events"}),
                                    summary="robot events"))
    page = search(graph, r.people["drew"], "anything fun with robots this week?", fake)
    assert page.understanding.used_ai and kinds(page) == {"event"}
    assert r.events["demo_day"] in {res.id for res in page.results}


def test_pages_of_ten_and_the_last_page(world):
    graph, r = world
    svc = engine(graph)
    first = svc.search(r.people["drew"], "", SearchFilters(types=frozenset({"orgs"})), NOW)
    assert first.total > 10 and len(first.results) == 10 and first.pages == -(-first.total // 10)
    last = svc.search(r.people["drew"], "", SearchFilters(types=frozenset({"orgs"})), NOW,
                      page=first.pages)
    assert len(last.results) == first.total - 10 * (first.pages - 1)
    beyond = svc.search(r.people["drew"], "", SearchFilters(types=frozenset({"orgs"})), NOW,
                        page=999)
    assert beyond.page == first.pages
    seen = set()
    for n in range(1, first.pages + 1):
        page = svc.search(r.people["drew"], "", SearchFilters(types=frozenset({"orgs"})), NOW, page=n)
        seen |= {res.id for res in page.results}
    assert len(seen) == first.total  # no duplicates or gaps across pages


def test_search_is_deterministic(world):
    graph, r = world
    a = search(graph, r.people["drew"], "robot")
    b = search(graph, r.people["drew"], "robot")
    assert [x.id for x in a.results] == [x.id for x in b.results]


# --- the API: what reaches the browser ------------------------------------------------------


@pytest.fixture(scope="module")
def app(world):
    graph, _ = world

    @contextmanager
    def factory():
        yield graph

    return AppApi(factory, clock=lambda: NOW)


def test_results_say_their_type_and_hide_scores_degrees_and_paths(app, world):
    _, r = world
    data = app.find(r.people["drew"], {"q": "robot", "types": "people,orgs,events"})
    assert {i["type"] for i in data["items"]} <= {"person", "org", "event"}
    for item in data["items"]:
        assert "score" not in item
        assert not {"degree", "path", "distance", "mutual"} & set(item)
    debug = app.find(r.people["drew"], {"q": "robot"}, debug=True)
    assert all("score" in i for i in debug["items"])


def test_api_reports_how_the_query_was_read_and_the_filters_used(app, world):
    _, r = world
    data = app.find(r.people["drew"], {"q": "surf club"})
    assert data["understood"]["mode"] == "keywords" and data["understood"]["keywords"] == ["surf"]
    assert data["filters"]["types"] == ["orgs"]
    dated = app.find(r.people["drew"], {"date": "2026-11-21"})
    assert dated["filters"]["date"] == "2026-11-21"


@pytest.mark.parametrize("params", [
    {"types": "people,spaceships"}, {"tags": "not-a-topic"}, {"date": "saturday"},
    {"page": "0"}, {"page": "x"}, {"q": "x" * 201},
])
def test_bad_search_input_is_refused(app, world, params):
    with pytest.raises(ApiError):
        app.find(world[1].people["drew"], params)
