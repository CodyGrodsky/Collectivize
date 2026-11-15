"""Search holds up against almost any input: it never crashes, answers
quickly, and reads short words, plurals, typos, accents, flooding words,
"this weekend" and misleading type words the way a person would mean them.
AI is off throughout (the conftest blocks real calls)."""

import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import date

import pytest

from backend.app_api import AppApi
from backend.config import Environment
from backend.demo_api import ApiError
from backend.graph import CollectivizeGraph
from backend.graph.ai_search import InterpreterError
from backend.graph.query_understanding import (
    AI_REST_SECONDS,
    ClaudeQueryExtractor,
    Extraction,
    ExtractRequest,
    QueryReader,
    local_extract,
)
from backend.graph.sqlite_store import SqliteGraphStore
from backend.graph.text_match import (
    KeywordMatcher,
    fold,
    keyword_fit,
    one_edit_apart,
    stem,
)
from backend.graph.unified_search import SearchFilters, UnifiedSearchService
from backend.objects import MemberOf, Person, Visibility
from backend.seed import seed_mock_data

from .conftest import NOW

FRIDAY = date(2026, 11, 13)  # NOW's date on campus
SLOW_SECONDS = 2.0  # generous: the point is "never hangs", not a benchmark

ANY_INPUT = [
    "", "   ", "\t\n", "a", "ai", "rob", "robot", "ROBOTICS  ", "the and of", "club", "people", "events",
    "o'brien", "rock-climbing", "café", "naïve", "日本語", "مرحبا", "🎉", "🎉 party", "!!!", "???", "?", "...",
    "-", "_", "%", "*", ".*", "\\", "'", '"', "' OR 1=1 --", "<script>alert(1)</script>", "robot\x00ics",
    "robot\u200bics", "ｒｏｂｏｔ", "évent", "2026", "cs3500", "2026-11-15", "2026-13-45", "today",
    "tomorrow", "this weekend", "next friday", "free food tomorrow", "show me something rad",
    "what's happening this weekend?", "people named alice", "i want to meet people who like hiking",
    "x" * 200, "robot " * 33, "a b c d e f g h i j k l m n o p", "zzzzqqqq", "robtics", "alice demo",
    chr(0x202E) + "robot", "robot\r\nclub", "ROBOT" * 40, "İstanbul", "ß", "ǅ", chr(0x1F600), chr(0xFFFD),
]


@pytest.fixture(scope="module")
def world():
    graph = CollectivizeGraph()
    result = seed_mock_data(graph, Environment.TEST, NOW)

    @contextmanager
    def factory():
        yield graph

    return AppApi(factory, clock=lambda: NOW), graph, result


def find(world, q, **params):
    app, _, result = world
    return app.find(result.people["drew"], {"q": q, **params})


def names(data):
    return [item.get("name") or item.get("title") for item in data["items"]]


# --- any input -------------------------------------------------------------------------


@pytest.mark.parametrize("text", ANY_INPUT)
def test_any_input_gets_a_well_formed_answer_quickly(world, text):
    started = time.perf_counter()
    data = find(world, text)
    assert time.perf_counter() - started < SLOW_SECONDS
    assert set(data) >= {"items", "total", "page", "pages", "counts", "understood", "filters"}
    assert data["total"] == sum(data["counts"].values()) and len(data["items"]) <= data["per_page"]
    assert 1 <= data["page"] <= data["pages"]
    assert all(isinstance(k, str) and k for k in data["understood"]["keywords"])


@pytest.mark.parametrize("params", [
    {"page": "²"}, {"page": "٣٣٣٣٣"}, {"page": ""}, {"page": " 1"}, {"page": "1" * 50},
    {"types": "people;drop"}, {"tags": "a" * 500}, {"date": "2026-02-30"}, {"date": "0000-00-00"},
    {"q": "x" * 201},
])
def test_bad_parameters_are_refused_not_crashed_on(world, params):
    with pytest.raises(ApiError):
        find(world, params.pop("q", "robot"), **params)


def test_a_page_past_the_end_is_the_last_page(world):
    data = find(world, "robot", page="999")
    assert data["page"] == data["pages"] and data["items"]


# --- matching --------------------------------------------------------------------------


def test_results_appear_while_typing(world):
    full = set(names(find(world, "robotics")))
    assert full and full <= set(names(find(world, "rob"))) | set(names(find(world, "rob", page="2")))
    assert set(names(find(world, "robot"))) == full
    assert keyword_fit(["ro"], [("Robotics Club", 1.0)]) == 0.8  # a short start counts a little less
    assert keyword_fit(["r"], [("Robotics Club", 1.0)]) == 0.0
    assert keyword_fit(["ob"], [("Robotics Club", 1.0)]) == 0.0  # the start of a word, not its middle


@pytest.mark.parametrize(("typed", "same_as"), [
    ("robots", "robot"), ("ROBOTS", "robot"), ("clubs surf", "surf club"), ("surfing", "surf"),
    ("surfers", "surfer"),
])
def test_plurals_and_endings_find_the_same_things(world, typed, same_as):
    assert names(find(world, typed)) == names(find(world, same_as)) != []


@pytest.mark.parametrize(("a", "b"), [
    ("hike", "hiking"), ("hikes", "hike"), ("parties", "party"), ("classes", "class"),
    ("dancing", "dance"), ("alice's", "alice"), ("studying", "study"),
])
def test_stems_agree(a, b):
    assert stem(a) == stem(b) or keyword_fit([a], [(b, 1.0)]) == 1.0
    assert keyword_fit([a], [(b, 1.0)]) == keyword_fit([b], [(a, 1.0)]) == 1.0


def test_accents_case_and_width_do_not_matter():
    assert fold("Café") == fold("CAFÉ") == fold("ｃａｆｅ") == "cafe"
    assert keyword_fit(["cafe"], [("Language exchange café", 1.0)]) == 1.0
    assert keyword_fit(["café"], [("Language exchange cafe", 1.0)]) == 1.0
    assert keyword_fit(["日本"], [("日本語 exchange", 1.0)]) > 0  # prefixes work in any script


def test_invisible_characters_do_not_split_a_word(world):
    assert names(find(world, "robot\u200bics")) == names(find(world, "robotics"))
    assert find(world, "robot\x00ics")["understood"]["keywords"] == ["robotics"]


# --- typos -----------------------------------------------------------------------------


@pytest.mark.parametrize("typo", ["robtics", "rrobotics", "rpbotics"])
def test_one_wrong_letter_still_finds_it_and_says_so(world, typo):
    data = find(world, typo)
    assert names(data) and set(names(data)) <= set(names(find(world, "robotics")))
    assert "robotics" in data["understood"]["note"] and typo in data["understood"]["note"]


def test_swapped_letters_are_forgiven_too(world):
    data = find(world, "sruf")
    assert names(data) == ["[Demo] Surf Club"] and "“surf”" in data["understood"]["note"]


def test_typos_are_only_forgiven_when_nothing_matched_and_never_for_short_words(world):
    assert find(world, "surf")["understood"]["note"] == ""  # exact matches: no second guess
    assert find(world, "zzzzqqqq")["total"] == 0
    assert one_edit_apart("robtics", "robotics") and one_edit_apart("robot", "robto")
    assert not one_edit_apart("robot", "robot") and not one_edit_apart("robot", "rabit")
    assert KeywordMatcher(["cot"], fuzzy=True).fit([("cat", 1.0)]).score == 0  # too short to guess
    assert KeywordMatcher(["hiking"], fuzzy=True).fit([("hi there", 1.0)]).score == 0


def test_a_typo_correction_only_comes_from_what_the_viewer_can_see(world):
    _, graph, result = world
    hidden = graph.store.save_node(Person(
        display_name="Zyxwvut Quorble", visibility=Visibility.PRIVATE,
        provenance=graph.store.node(result.people["drew"], Person).provenance,
    ))
    try:
        data = find(world, "quorbel")
        assert data["total"] == 0 and "quorble" not in data["understood"]["note"].casefold()
    finally:
        graph.store.delete_node(hidden.id)


# --- several words ---------------------------------------------------------------------


def test_results_with_every_word_come_before_results_with_some(world):
    _, graph, result = world
    service = UnifiedSearchService(graph.store, graph.discovery, graph.visibility, graph.privacy,
                                   QueryReader())
    page = service.search(result.people["drew"], "study jam robot", SearchFilters(), NOW, per_page=50)
    for group in (True, False):  # your network first, then the rest; each full-first
        fulls = [r.full for r in page.results if r.connected is group]
        assert fulls == sorted(fulls, reverse=True)
    assert {r.full for r in page.results} == {True, False}
    matcher = KeywordMatcher(["surf", "club"])
    assert matcher.fit([("Surf Club", 1.0)]).full and not matcher.fit([("Chess Club", 1.0)]).full


def test_a_word_that_matches_almost_everything_is_left_out(world):
    data = find(world, "alice demo")
    assert names(data) == ["Alice (demo student)"]
    assert data["understood"]["keywords"] == ["alice"] and "demo" in data["understood"]["note"]
    alone = find(world, "demo")  # on its own it is all there is to go on
    assert alone["total"] > 50 and alone["understood"]["note"] == ""


# --- dates -----------------------------------------------------------------------------


def test_this_weekend_is_saturday_and_sunday():
    read = local_extract("this weekend", FRIDAY)
    assert (read.day, read.days) == (date(2026, 11, 14), 2)
    assert (local_extract("weekend", date(2026, 11, 14)).day, local_extract("weekend", date(2026, 11, 14)).days) == (date(2026, 11, 14), 2)
    sunday = local_extract("weekend plans", date(2026, 11, 15))
    assert (sunday.day, sunday.days) == (date(2026, 11, 15), 1)  # what's left of it
    assert local_extract("saturday", FRIDAY).days == 1


def test_weekend_results_cover_both_days_and_page_two_keeps_them(world):
    data = find(world, "this weekend")
    assert data["filters"]["date"] == "2026-11-14" and data["filters"]["date_end"] == "2026-11-15"
    saturday = find(world, "", date="2026-11-14")["total"]
    sunday = find(world, "", date="2026-11-15")["total"]
    assert data["total"] == saturday + sunday
    assert find(world, "", date="2026-11-14")["filters"]["date_end"] is None
    # The page ticks the date it was told; sending it back must not shrink the range.
    again = find(world, "this weekend", date="2026-11-14")
    assert again["total"] == data["total"] and again["filters"]["date_end"] == "2026-11-15"
    other = find(world, "this weekend", date="2026-11-21")  # a different day ticked wins
    assert other["filters"]["date"] == "2026-11-21" and other["filters"]["date_end"] is None


# --- filler and type words -------------------------------------------------------------


@pytest.mark.parametrize(("text", "keywords"), [
    ("clubs about robotics", ("robotics",)), ("people named alice", ("alice",)),
    ("a club called surf", ("surf",)), ("who likes chess", ("chess",)),
    ("upcoming events about robots", ("robots",)), ("tell me about surfing", ("surfing",)),
])
def test_joining_words_are_not_keywords(text, keywords):
    assert local_extract(text, FRIDAY).keywords == keywords


def test_a_type_word_that_leaves_nothing_is_dropped(world):
    data = find(world, "people who like chess")
    assert names(data) == ["[Demo] Chess Club"] and data["filters"]["types"] == []
    assert "searched everything" in data["understood"]["note"]
    kept = find(world, "people named alice")  # it found people: the type stays
    assert kept["filters"]["types"] == ["people"] and kept["understood"]["note"] == ""
    ticked = find(world, "zebra", types="people")  # ticked by hand: never widened
    assert ticked["total"] == 0 and ticked["filters"]["types"] == ["people"]


# --- AI timing -------------------------------------------------------------------------


class Failing:
    name = "failing"

    def __init__(self):
        self.calls = 0

    def extract(self, request):
        self.calls += 1
        raise InterpreterError("timed out")


def test_after_ai_fails_searches_answer_at_once_for_a_while():
    now = [0.0]
    extractor = Failing()
    reader = QueryReader(extractor, clock=lambda: now[0])
    first = reader.read("something chill with robots please", FRIDAY)
    second = reader.read("another long sentence about surfing", FRIDAY)
    assert extractor.calls == 1 and "robots" in first.extraction.keywords
    assert "surfing" in second.extraction.keywords and second.note == first.note != ""
    now[0] = AI_REST_SECONDS + 1
    reader.read("a third long sentence about chess", FRIDAY)
    assert extractor.calls == 2  # it is tried again once the rest is over


def test_the_ai_reader_gives_up_after_a_few_seconds(monkeypatch):
    made = {}

    def fake_make_client(timeout, max_retries):
        made.update(timeout=timeout, max_retries=max_retries)
        raise InterpreterError("stop here")

    monkeypatch.setattr("backend.graph.claude_interpreter.make_client", fake_make_client)
    with pytest.raises(InterpreterError):
        ClaudeQueryExtractor().extract(ExtractRequest("robots please now ok", FRIDAY))
    assert made == {"timeout": 4.0, "max_retries": 0}


def test_a_weekend_read_by_ai_still_covers_both_days():
    class Weekend:
        name = "weekend"

        def extract(self, request):
            return Extraction(keywords=("music",), day=date(2026, 11, 14))

    read = QueryReader(Weekend()).read("some live music this weekend maybe", FRIDAY).extraction
    assert (read.day, read.days) == (date(2026, 11, 14), 2)


# --- the per-request read cache --------------------------------------------------------


def test_a_store_with_the_read_cache_sees_its_own_writes_and_rollbacks(tmp_path):
    with_cache = CollectivizeGraph(SqliteGraphStore(tmp_path / "t.db", Environment.TEST, read_cache=True))
    result = seed_mock_data(with_cache, Environment.TEST, NOW)
    store, drew = with_cache.store, result.people["drew"]
    before = store.edges(MemberOf, source_id=drew)
    assert store.edges(MemberOf, source_id=drew) == before  # answered from memory
    before.clear()  # a caller changing its copy must not change the next answer
    assert store.edges(MemberOf, source_id=drew)
    renamed = store.save_node(replace(store.node(drew, Person), bio="new bio"))
    assert store.node(drew, Person).bio == renamed.bio == "new bio"
    with pytest.raises(RuntimeError), store.transaction():
        store.save_node(replace(renamed, bio="undone"))
        assert store.node(drew, Person).bio == "undone"
        raise RuntimeError
    assert store.node(drew, Person).bio == "new bio"
    store.delete_node(drew)
    assert not store.has_node(drew)
    with pytest.raises(LookupError):
        store.node(drew, Person)
