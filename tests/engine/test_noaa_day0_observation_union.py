"""A running extreme is monotone, so the two noaa lanes union rather than compete.

`_fetch_noaa_day0_observation` returned `max(candidates, key=_causal_time)` — the freshest
lane alone — which DISCARDS an already-observed extreme whenever the other lane holds it.
Measured against the live world DB on 2026-09-17: 3 of 48 noaa cities were losing their own
higher high (Los Angeles 73.92 vs 75.92 F, Manila 29 vs 31 C, Wellington 13 vs 15 C). A
2-unit loss on a whole-degree bin grid can cross a bin edge, which is the difference between
a live bin and a dead one.

The wu_icao lane already unions, for the reason its own docstring gives
(`observation_client._fuse_wu_prefix_with_same_station_tail`): both lanes read the SAME
physical settlement station, so max/min across them is a single-sensor running extreme, not
a cross-source blend. The freshness clock stays the freshest lane's; only the extremes union.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from src.config import cities_by_name
from src.data.observation_client import Day0ObservationContext


def _city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def _ctx(*, high: float, low: float, source: str, when: str, unit: str = "F"):
    return Day0ObservationContext(
        current_temp=low,
        high_so_far=high,
        low_so_far=low,
        source=source,
        observation_time=when,
        unit=unit,
        station_id="KLAX",
        sample_count=24,
        coverage_status="OK",
    )


def _fuse(monkeypatch, ledger, canonical, *, city_name: str = "Los Angeles"):
    """Run the shipped fusion with both readers stubbed and no DB."""
    import src.data.day0_fast_obs as fast_obs
    import src.data.day0_observation_reader as reader
    import src.engine.monitor_refresh as monitor
    import src.state.db as db

    monkeypatch.setattr(
        fast_obs, "read_noaa_fast_obs_context_from_ledger", lambda *a, **k: ledger
    )
    monkeypatch.setattr(
        reader,
        "read_day0_observation_context_from_instants",
        lambda *a, **k: canonical,
    )

    class _Conn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(db, "get_world_connection_read_only", lambda *a, **k: _Conn())
    return monitor._fetch_noaa_day0_observation(
        _city(city_name),
        dt.date(2026, 9, 17),
        reference_time=dt.datetime.now(dt.timezone.utc),
    )


def test_a_stale_lanes_higher_high_survives(monkeypatch):
    """The defect: the freshest lane's lower high replaced a real observation."""
    fused = _fuse(
        monkeypatch,
        _ctx(high=73.92, low=71.08, source="aviationweather_metar",
             when="2026-09-18T02:53:00+00:00"),
        _ctx(high=75.92, low=62.96, source="ogimet_metar_klax",
             when="2026-09-18T01:53:00+00:00"),
    )
    assert fused is not None
    assert fused.high_so_far == pytest.approx(75.92)
    assert fused.low_so_far == pytest.approx(62.96)


def test_canonical_raw_window_does_not_resurrect_retracted_projection(monkeypatch):
    direct = replace(
        _ctx(high=28.0, low=26.0, source="aviationweather_metar",
             when="2026-09-22T05:00:00+00:00", unit="C"),
        data_version="same_station_metar_canonical_window_v1",
        provider_reported_time="typed_causal_availability",
    )
    stale = _ctx(high=29.0, low=25.0, source="ogimet_metar_rkpk",
                 when="2026-09-22T04:00:00+00:00", unit="C")
    fused = _fuse(monkeypatch, direct, stale, city_name="Busan")
    assert fused is direct


def test_conflicting_raw_revisions_cannot_fall_back_to_old_projection(monkeypatch):
    import src.data.day0_fast_obs as fast_obs
    import src.data.day0_observation_reader as reader
    import src.engine.monitor_refresh as monitor
    import src.state.db as db

    def conflict(*args, **kwargs):
        raise fast_obs.KmaObservationConflict("RKPK conflicting COR")

    monkeypatch.setattr(fast_obs, "read_noaa_fast_obs_context_from_ledger", conflict)
    monkeypatch.setattr(reader, "read_day0_observation_context_from_instants",
                        lambda *args, **kwargs: _ctx(
                            high=29.0, low=25.0, source="ogimet_metar_rkpk",
                            when="2026-09-22T04:00:00+00:00", unit="C"))
    class Connection:
        def close(self):
            pass
    monkeypatch.setattr(db, "get_world_connection_read_only", Connection)
    assert monitor._fetch_noaa_day0_observation(
        _city("Busan"), dt.date(2026, 9, 22),
        reference_time=dt.datetime(2026, 9, 22, 5, 1, tzinfo=dt.timezone.utc),
    ) is None


def test_the_freshness_clock_stays_the_freshest_lanes(monkeypatch):
    """Unioning extremes must not rewind the observation clock."""
    fused = _fuse(
        monkeypatch,
        _ctx(high=73.92, low=71.08, source="aviationweather_metar",
             when="2026-09-18T02:53:00+00:00"),
        _ctx(high=75.92, low=62.96, source="ogimet_metar_klax",
             when="2026-09-18T01:53:00+00:00"),
    )
    assert fused.source == "aviationweather_metar"
    assert fused.observation_time == "2026-09-18T02:53:00+00:00"


def test_mismatched_units_refuse_to_union(monkeypatch):
    """Never blend across units; fall back to the freshest lane untouched."""
    fused = _fuse(
        monkeypatch,
        _ctx(high=23.0, low=21.0, source="aviationweather_metar",
             when="2026-09-18T02:53:00+00:00", unit="C"),
        _ctx(high=75.92, low=62.96, source="ogimet_metar_klax",
             when="2026-09-18T01:53:00+00:00", unit="F"),
    )
    assert fused.high_so_far == pytest.approx(23.0)
    assert fused.unit == "C"


def test_a_single_lane_is_returned_unchanged(monkeypatch):
    """With one candidate there is nothing to union."""
    only = _ctx(high=73.92, low=71.08, source="aviationweather_metar",
                when="2026-09-18T02:53:00+00:00")
    fused = _fuse(monkeypatch, only, None)
    assert fused.high_so_far == pytest.approx(73.92)
    assert fused.low_so_far == pytest.approx(71.08)


def test_the_freshest_lane_already_holding_the_extreme_is_unaffected(monkeypatch):
    """The common case must be a no-op, not a rewrite."""
    fused = _fuse(
        monkeypatch,
        _ctx(high=80.0, low=60.0, source="aviationweather_metar",
             when="2026-09-18T02:53:00+00:00"),
        _ctx(high=75.92, low=62.96, source="ogimet_metar_klax",
             when="2026-09-18T01:53:00+00:00"),
    )
    assert fused.high_so_far == pytest.approx(80.0)
    assert fused.low_so_far == pytest.approx(60.0)
    assert fused.source == "aviationweather_metar"
