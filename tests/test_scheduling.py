"""Unit tests for the relative-date resolver (E2). Deterministic via a fixed now."""

from __future__ import annotations

from datetime import datetime

from agent.scheduling import IST, resolve_datetime

NOW = datetime(2026, 8, 7, 10, 0, tzinfo=IST)  # fixed reference (a Friday)


def test_kal_with_bare_hour_assumes_afternoon():
    assert resolve_datetime("kal 4 baje", now=NOW) == "2026-08-08T16:00:00+05:30"


def test_aaj_shaam_maps_to_evening():
    assert resolve_datetime("aaj shaam", now=NOW) == "2026-08-07T18:00:00+05:30"


def test_parso_defaults_neutral_time():
    assert resolve_datetime("parso", now=NOW) == "2026-08-09T11:00:00+05:30"


def test_explicit_pm():
    assert resolve_datetime("tomorrow 6:30 pm", now=NOW) == "2026-08-08T18:30:00+05:30"


def test_morning_keeps_am_hour():
    assert resolve_datetime("kal subah 9 baje", now=NOW) == "2026-08-08T09:00:00+05:30"


def test_weekday_resolves_to_next_occurrence():
    iso = resolve_datetime("saturday", now=NOW)
    assert iso is not None
    d = datetime.fromisoformat(iso)
    assert d.weekday() == 5  # Saturday
    assert d > NOW


def test_unparseable_returns_none():
    assert resolve_datetime("kabhi bhi", now=NOW) is None
    assert resolve_datetime("", now=NOW) is None
