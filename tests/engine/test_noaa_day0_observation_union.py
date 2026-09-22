# Created: 2026-09-22
# Last reused/audited: 2026-09-22
# Authority basis: KMA AMO raw-METAR Day0 transport port.
"""KMA canonical-window monitor antibodies."""
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


def test_kma_canonical_raw_window_does_not_resurrect_retracted_projection(monkeypatch):
    direct = replace(
        _ctx(high=28.0, low=26.0, source="aviationweather_metar",
             when="2026-09-22T05:00:00+00:00", unit="C"),
        data_version="same_station_metar_canonical_window_v1",
        provider_reported_time="typed_causal_availability",
    )
    stale = _ctx(high=29.0, low=25.0, source="ogimet_metar_rkpk",
                 when="2026-09-22T04:00:00+00:00", unit="C")

    assert _fuse(monkeypatch, direct, stale, city_name="Busan") is direct


def test_kma_conflicting_raw_revisions_cannot_fall_back_to_old_projection(monkeypatch):
    import src.data.day0_fast_obs as fast_obs
    import src.data.day0_observation_reader as reader
    import src.engine.monitor_refresh as monitor
    import src.state.db as db

    def conflict(*args, **kwargs):
        raise fast_obs.KmaObservationConflict("RKPK conflicting COR")

    monkeypatch.setattr(fast_obs, "read_noaa_fast_obs_context_from_ledger", conflict)
    monkeypatch.setattr(
        reader,
        "read_day0_observation_context_from_instants",
        lambda *args, **kwargs: _ctx(
            high=29.0, low=25.0, source="ogimet_metar_rkpk",
            when="2026-09-22T04:00:00+00:00", unit="C",
        ),
    )

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(db, "get_world_connection_read_only", Connection)
    assert monitor._fetch_noaa_day0_observation(
        _city("Busan"), dt.date(2026, 9, 22),
        reference_time=dt.datetime(2026, 9, 22, 5, 1, tzinfo=dt.timezone.utc),
    ) is None
