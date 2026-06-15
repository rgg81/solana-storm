"""report.py must tolerate both dict and string headline entries.

Bug (tick-138, 2026-06-15): build_report crashed with
`AttributeError: 'str' object has no attribute 'get'` at the news-sentiment
table. A specialist's `news_sentiment.headlines_used` is shaped by the LLM and
varies: the role-file example uses dicts ({"source":..,"title":..}) but agents
sometimes emit bare strings ("SOL ecosystem (generic)"). report.py assumed dict
and called h0.get("title"), exploding the whole report on a string entry.

Fix: _headline_title(h) normalizes either shape to a title string.
"""
from __future__ import annotations

from predictions.fund import report


def test_dict_headline_returns_title():
    h = {"source": "decrypt", "title": "Polymarket Taps Jupiter Exec"}
    assert report._headline_title(h) == "Polymarket Taps Jupiter Exec"


def test_string_headline_returned_as_is():
    assert report._headline_title("SOL ecosystem (generic)") == "SOL ecosystem (generic)"


def test_dict_without_title_returns_empty():
    assert report._headline_title({"source": "x"}) == ""


def test_none_returns_empty():
    assert report._headline_title(None) == ""


def test_non_str_non_dict_coerced_to_str():
    # A stray number or list shouldn't crash; coerce to str defensively.
    assert report._headline_title(123) == "123"
