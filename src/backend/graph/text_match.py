"""How typed keywords are matched against names, topics and descriptions.

A keyword matches a word in a field when, after folding (case, accents,
width) and light stemming (plurals, -ing):

- they are the same word or share a stem: "robots" ~ "robotics",
  "café" ~ "cafe", "studying" ~ "study";
- the keyword is the start of the word, from two letters up, so results
  appear while typing: "rob" ~ "robotics". Two- and three-letter starts
  count a little less than a whole word;
- with `fuzzy` (used only when nothing matched exactly): one letter is
  wrong, missing, extra or swapped: "robtics" ~ "robotics".

`KeywordMatcher.fit` scores one record: each keyword scores the weight of
the best field it was found in, averaged over the keywords. It also says
whether *every* keyword was found, so those results can be listed first,
and counts how often each keyword hit, so a word that matches nearly
everything can be spotted.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import NamedTuple

WORD = re.compile(r"\w+(?:'\w+)*")
STEM_PREFIX = 4  # from this length, a shared start counts as the same word
MIN_PREFIX = 2  # the shortest keyword that matches the start of a word
SHORT_PREFIX_QUALITY = 0.8  # "ro" for "robotics": a match, but a weaker one
FUZZY_QUALITY = 0.7
FUZZY_MIN_CHARS = 4  # shorter words have too many one-letter neighbours
FIELD_CACHE_SIZE = 50_000


def fold(text: str) -> str:
    """Comparable form: case-folded, accents removed, full-width letters
    narrowed ("Café", "CAFE" and "ｃａｆｅ" all become "cafe")."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def stem(word: str) -> str:
    """A light stem: drops a possessive, a plural, -ing or a final -e
    ("hikes", "hiking" and "hike" all become "hik"). Only ever used to
    compare, never shown."""
    word = word.removesuffix("'s")
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        return word[:-1]
    return word


def one_edit_apart(a: str, b: str) -> bool:
    """True when one letter is wrong, missing, extra, or two neighbours are
    swapped. Linear time."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        if a[i + 1:] == b[i + 1:]:
            return True  # one wrong letter
        return a[i:i + 2] == b[i:i + 2][::-1] and a[i + 2:] == b[i + 2:]  # a swap
    return a[i:] == b[i + 1:]  # one missing letter


@lru_cache(maxsize=FIELD_CACHE_SIZE)
def _field(text: str) -> tuple[tuple[tuple[str, str], ...], str]:
    """A field's (word, stem) pairs and its folded text. Cached: the same
    names and descriptions are compared on every search."""
    folded = fold(text)
    words = tuple(dict.fromkeys((w, stem(w)) for w in WORD.findall(folded)))
    return words, folded


def word_quality(keyword: str, keyword_stem: str, word: str, word_stem: str,
                 *, fuzzy: bool = False) -> float:
    """0 (no match) to 1 (the same word)."""
    if keyword == word or keyword_stem == word_stem:
        return 1.0
    shorter, longer = sorted((keyword_stem, word_stem), key=len)
    if len(shorter) >= STEM_PREFIX and longer.startswith(shorter):
        return 1.0
    if MIN_PREFIX <= len(keyword) < STEM_PREFIX and word.startswith(keyword):
        return SHORT_PREFIX_QUALITY
    if fuzzy and min(len(keyword), len(word)) >= FUZZY_MIN_CHARS and (
        one_edit_apart(keyword, word)
        or (len(shorter) >= FUZZY_MIN_CHARS and one_edit_apart(keyword_stem, word_stem))
    ):
        return FUZZY_QUALITY
    return 0.0


class Fit(NamedTuple):
    score: float  # 0..1
    full: bool  # every keyword was found somewhere


class KeywordMatcher:
    """Scores records against one search's keywords."""

    def __init__(self, keywords: list[str], *, fuzzy: bool = False) -> None:
        self.keywords = list(keywords)
        self.fuzzy = fuzzy
        self._parts = [
            (keyword, fold(" ".join(keyword.split())),
             tuple((w, stem(w)) for w in WORD.findall(fold(keyword))))
            for keyword in keywords
        ]
        self.seen = 0  # records scored
        self.hits = dict.fromkeys(keywords, 0)  # records each keyword was found in
        self.corrections: dict[str, str] = {}  # typed word -> the word it was read as

    def fit(self, fields: list[tuple[str, float]]) -> Fit:
        """`fields`: (text, weight) pairs, e.g. a name at 1.0, a bio at 0.6."""
        if not self._parts:
            return Fit(1.0, True)
        prepared = [(*_field(text), weight) for text, weight in fields if text]
        self.seen += 1
        total, full = 0.0, True
        for keyword, phrase, parts in self._parts:
            best = 0.0
            for words, folded, weight in prepared:
                if len(parts) > 1 and phrase in folded:
                    quality = 1.0  # a phrase found whole
                else:
                    quality = self._all_parts(parts, words)
                best = max(best, weight * quality)
            if best > 0:
                self.hits[keyword] += 1
            else:
                full = False
            total += best
        return Fit(total / len(self._parts), full)

    def _all_parts(self, parts: tuple[tuple[str, str], ...],
                   words: tuple[tuple[str, str], ...]) -> float:
        """Every word of the keyword must be found; the weakest decides."""
        if not parts:
            return 0.0
        lowest = 1.0
        for part, part_stem in parts:
            best, matched = 0.0, ""
            for word, word_stem in words:
                quality = word_quality(part, part_stem, word, word_stem, fuzzy=self.fuzzy)
                if quality > best:
                    best, matched = quality, word
                    if best == 1.0:
                        break
            if best == 0.0:
                return 0.0
            if best == FUZZY_QUALITY:
                self.corrections.setdefault(part, matched)
            lowest = min(lowest, best)
        return lowest

    @property
    def can_be_fuzzy(self) -> bool:
        """Whether a typo-tolerant pass could find anything more."""
        return any(len(part) >= FUZZY_MIN_CHARS for _, _, parts in self._parts for part, _ in parts)

    def flooding(self, share: float) -> list[str]:
        """Keywords found in more than `share` of the records scored, when
        at least one other keyword is more selective."""
        if self.seen == 0 or len(self.keywords) < 2:
            return []
        common = [k for k in self.keywords if self.hits[k] / self.seen > share]
        return common if len(common) < len(self.keywords) else []


def keyword_fit(keywords: list[str], fields: list[tuple[str, float]]) -> float:
    """The 0..1 fit of one record (see KeywordMatcher.fit)."""
    return KeywordMatcher(keywords).fit(fields).score
