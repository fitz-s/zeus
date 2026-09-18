"""Two restart/entry-side channels that the settlement migration left WU-only.

1. `_durable_observation_instants_summary` — the restart-safe side of the hard-fact lane,
   per its own docstring — filtered rows with `LOWER(source) LIKE 'wu%'`. A noaa city writes
   `ogimet_metar_<icao>` (`day0_observation_reader.source_priority_for_city`), which can
   never match, so the channel was empty for all 48 noaa cities even though 104,512 such
   rows exist. Both live callers — `day0_entry_bin_still_alive` (submit-time entry recheck)
   and `classify_day0_dead_bin_entry_cancels` (resting-entry cancel sweep) — invoke it
   through `_wu_hard_fact_evidence` with NO family gate, so nothing upstream compensated;
   those lanes silently depended on the METAR fast-tail channel being warm.

2. `Day0FastObsEmitter.latest_pre_day0_low_window` returned None for every non-wu_icao city,
   while its entry-side callers (`evaluator.py`, `event_reactor_adapter.py`) call it for any
   LOW metric with no family pre-check. The computation
   `pre_day0_low_window_for_target` reads only the city's timezone, unit and station, so it
   was family-agnostic all along.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src.config import cities_by_name, load_cities
from src.data.day0_fast_obs import (
    _FAST_LANE_SETTLEMENT_SOURCE_TYPES,
    FETCH_FRESH,
    Day0FastObsEmitter,
    MetarReport,
)
from src.data.day0_observation_reader import source_priority_for_city


def _city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def _durable_channel_predicate(city, target_date: str = "2026-09-17"):
    """The set of source values the durable lane will now accept for this city.

    Mirrors what `_durable_observation_instants_summary` passes to its `source IN
    (...)` clause: the city's own settlement channels, lowercased.
    """
    return {
        channel.lower()
        for channel in source_priority_for_city(city, target_date)
        if str(channel or "").strip()
    }


def test_durable_lane_accepts_a_noaa_citys_own_canonical_channel():
    """The defect: `LIKE 'wu%'` could never match `ogimet_metar_<icao>`."""
    city = _city("Ankara")
    assert str(getattr(city, "settlement_source_type", "")).lower() == "noaa"
    accepted = _durable_channel_predicate(city)
    assert accepted, "a noaa city must resolve at least one durable channel"
    assert accepted == {f"ogimet_metar_{str(city.wu_station).lower()}"}
    assert not any(channel.startswith("wu") for channel in accepted), (
        "the old predicate admitted only wu-prefixed sources, which is exactly "
        "what excluded every noaa city"
    )


def test_durable_lane_never_accepts_a_foreign_familys_channel():
    """A city may not read another family's settlement product."""
    ankara = _city("Ankara")
    madrid = _city("Madrid")
    assert "wu_icao_history" not in _durable_channel_predicate(ankara)
    assert _durable_channel_predicate(ankara).isdisjoint(
        _durable_channel_predicate(madrid)
    )


def test_durable_lane_still_accepts_a_wu_citys_history_channel():
    """Widening must not retarget the wu_icao contract."""
    city = _city("Auckland")
    if str(getattr(city, "settlement_source_type", "")).lower() != "wu_icao":
        pytest.skip("Auckland is no longer a wu_icao city")
    assert _durable_channel_predicate(city) == {"wu_icao_history"}


def test_durable_lane_degrades_to_absence_without_a_channel():
    """No channel means no evidence, never a foreign city's rows."""
    from types import SimpleNamespace

    stationless = SimpleNamespace(
        name="Nowhere", wu_station="", settlement_source_type="noaa", timezone="UTC"
    )
    assert _durable_channel_predicate(stationless) == set()


def _warm_emitter(city):
    """An emitter whose station is current, so only the family gate discriminates."""
    emitter = Day0FastObsEmitter(fetcher=lambda *a, **k: ([], FETCH_FRESH))
    station = str(getattr(city, "wu_station", "") or "").upper() or "XXXX"
    now = dt.datetime.now(dt.timezone.utc)
    emitter._cached_reports = [
        MetarReport(
            station_id=station, obs_time=now, receipt_time=now,
            temp_c=10.0, metar_type="METAR", raw="",
        )
    ]
    emitter._station_is_current = lambda *a, **k: True
    emitter._station_statuses = lambda stations: tuple(
        (s, FETCH_FRESH, 0.0) for s in stations
    )
    return emitter


def test_low_window_gate_admits_both_settlement_families(monkeypatch):
    """The LOW carryover feature must reach the family that settles 48 cities."""
    import src.data.day0_fast_obs as module

    reached: list[str] = []
    monkeypatch.setattr(
        module,
        "pre_day0_low_window_for_target",
        lambda *a, **k: reached.append(k["city"].name) or "WINDOW",
    )
    for name in ("Ankara", "Madrid", "Auckland"):
        city = _city(name)
        assert _warm_emitter(city).latest_pre_day0_low_window(
            city, "2026-09-19", as_of=dt.datetime.now(dt.timezone.utc)
        ) == "WINDOW", name
    assert reached == ["Ankara", "Madrid", "Auckland"]


def test_low_window_gate_still_refuses_a_family_with_no_icao_station(monkeypatch):
    """Hong Kong names no ICAO station, so it has no window to carry."""
    import src.data.day0_fast_obs as module

    monkeypatch.setattr(
        module, "pre_day0_low_window_for_target", lambda *a, **k: "WINDOW"
    )
    city = _city("Hong Kong")
    if str(getattr(city, "settlement_source_type", "")).lower() != "hko":
        pytest.skip("Hong Kong is no longer an hko city")
    assert _warm_emitter(city).latest_pre_day0_low_window(
        city, "2026-09-19", as_of=dt.datetime.now(dt.timezone.utc)
    ) is None


def test_every_noaa_city_resolves_a_settlement_channel_for_the_durable_lane():
    """The durable lane needs a channel per city, or it degrades to absence."""
    for city in load_cities():
        if str(getattr(city, "settlement_source_type", "")).lower() != "noaa":
            continue
        channels = source_priority_for_city(city, "2026-09-17")
        assert channels, city.name
        assert all(c.startswith("ogimet_metar_") for c in channels), city.name


def test_both_fast_lane_families_are_the_ones_the_cache_polls():
    """The gates now share one membership set, so they cannot drift apart."""
    assert _FAST_LANE_SETTLEMENT_SOURCE_TYPES == frozenset({"wu_icao", "noaa"})
