"""Properties of the small pure functions that every sync, card and plan goes
through, checked over generated inputs (Hypothesis) rather than a few examples.

Each test states what must hold for ANY input of its kind: a Garmin value is
either a number in range or nothing - never an exception; a card that is sound
JSON is read back unchanged; a bracket inside a string is never touched; a week
starts on a Monday. A failing example Hypothesis finds is kept as a plain test
below its property, red before the fix.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from runspector import evidence, garmin, plan
from runspector.store import week_start
from runspector.web import agent

settings.register_profile("runspector", deadline=None, max_examples=200)
settings.load_profile("runspector")

#: What Garmin's JSON can hand a parser: numbers of every size, the -1 sentinels,
#: strings, None, nested junk - and Python's json reads "Infinity" and "1e999".
garmin_values = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=12), st.sampled_from(["1e999", "-1e999", "Infinity", "NaN", "12.5", " 42 ", "-1"]),
    st.lists(st.integers(), max_size=2), st.dictionaries(st.text(max_size=3), st.integers(), max_size=2))
bounds = st.tuples(st.integers(-1000, 1000), st.integers(0, 5000)).map(lambda t: (t[0], t[0] + t[1]))


@given(garmin_values, bounds)
def test_a_garmin_value_becomes_a_whole_number_in_range_or_nothing(value, lohi):
    lo, hi = lohi
    got = garmin._coerce(value, lo, hi)
    assert got is None or (isinstance(got, int) and lo <= got <= hi)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), "1e999", "Infinity"])
def test_an_infinite_garmin_value_is_nothing_not_a_crash(value):
    """Found by the property above: `round(inf)` raises OverflowError, which
    `_coerce` did not catch - one such field made `fetch_day` fail, and the sync
    skipped that whole day after a retry."""
    assert garmin._coerce(value, 0, 200000) is None


@given(garmin_values, bounds)
def test_a_garmin_decimal_becomes_a_number_in_range_or_nothing(value, lohi):
    lo, hi = lohi
    got = garmin._coerce_float(value, lo, hi)
    assert got is None or (isinstance(got, float) and math.isfinite(got) and lo <= got <= hi)


@given(st.dates(min_value=date(1990, 1, 1), max_value=date(2100, 12, 31)))
def test_a_week_starts_on_the_monday_of_its_day(day):
    monday = week_start(day)
    assert monday.weekday() == 0 and monday <= day < monday + timedelta(days=7)


@given(st.one_of(st.integers(), st.floats(allow_nan=True, allow_infinity=True), st.booleans(),
                 st.text(max_size=8), st.none()))
def test_a_whole_number_from_json_is_exact_or_refused(value):
    try:
        got = plan._whole(value, "minutes")
    except ValueError:
        return
    assert isinstance(got, int) and not isinstance(value, bool)
    if isinstance(value, float):
        assert value.is_integer() and got == value


# ── the card's JSON ──────────────────────────────────────────────────────────

#: Text a model writes into a card: brackets, quotes, backslashes, umlauts.
card_text = st.text(alphabet=st.sampled_from(list("ab \u00c4{}[]\"\\:,.-0")), max_size=24)
json_values = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-999, 999), card_text),
    lambda inner: st.one_of(st.lists(inner, max_size=3), st.dictionaries(card_text, inner, max_size=3)),
    max_leaves=12)
cards = st.fixed_dictionaries(
    {k: card_text.filter(str.strip) if k == "headline" else
     st.lists(card_text, max_size=4) if k == "bullets" else card_text
     for k in agent.CARD_REQUIRED},
    optional={"extra": json_values})


@given(json_values)
def test_sound_json_is_never_changed_by_the_bracket_repair(value):
    text = json.dumps(value, ensure_ascii=False)
    assert agent._repair_closers(text) == text
    assert agent._repair_closers(text, drop=True) == text


@given(st.text(alphabet=st.sampled_from(list('ab{}[]"\\ ')), max_size=40))
def test_the_bracket_repair_never_adds_and_swapping_never_shortens(text):
    swapped, dropped = agent._repair_closers(text), agent._repair_closers(text, drop=True)
    assert len(swapped) == len(text)
    assert len(dropped) <= len(text)


@given(cards, st.text(alphabet=st.sampled_from(list("Here is the card: ab.\n")), max_size=30))
def test_a_sound_card_after_a_sentence_is_read_back_unchanged(card, before):
    got, problem = agent.parse_card(before + json.dumps(card, ensure_ascii=False))
    assert problem == "" and got == card


@given(st.text(max_size=200))
def test_any_answer_is_read_without_an_exception(text):
    got, problem = agent.parse_card(text)
    assert (got is None) != (problem == "")


# ── source marks ─────────────────────────────────────────────────────────────

@given(st.lists(st.one_of(card_text, st.none(), st.integers()), max_size=6))
def test_split_marks_keeps_one_entry_per_point_and_unmarked_points_as_they_are(bullets):
    texts, sources = evidence.split_marks(bullets)
    assert len(texts) == len(sources) == len(bullets)
    for b, t, s in zip(bullets, texts, sources, strict=True):
        if s is None:
            assert t == b


@given(st.sampled_from(["measured", "you", "derived", "MEASURED"]),
       st.sampled_from(["", ": get_trend", " - get_training_readiness", ": the athlete"]),
       card_text.filter(lambda t: not t.lstrip().startswith("[")))
def test_a_marked_point_loses_its_mark_and_keeps_its_words(kind, ref, words):
    texts, sources = evidence.split_marks([f"[{kind}{ref}] {words}"])
    assert sources[0]["kind"] == kind.lower()
    assert texts[0] == words.lstrip()
