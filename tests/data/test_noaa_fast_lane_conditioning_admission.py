"""The NOAA fast lane's CONSUMER must admit the print the selector already resolved.

`fast_obs_source_for_city` resolves an AWC METAR fast source for every noaa city, and the
ingest lane lands those prints (Ankara's realised 18.0C reached `observation_prints` 23 s
after publication on 2026-09-17). Yet `latest_fast_station_extreme_c` and
`build_fast_station_residual_likelihood` both required `settlement_source_type == "wu_icao"`,
so `latest_fast_station_conditioning` returned None for all 48 noaa cities and the belief
carrier saw only the slow settlement channel — Ankara's page mirror of the same 18.0C was
not fetched until 22:15Z, 8.3 h later. We sold NO on a bin our own ledger had already shown
reached. These tests pin the consumer side of that wiring, which a selector test cannot see.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.config import cities_by_name
from src.data.day0_fast_obs import (
    FAST_OBS_SOURCE_ID,
    FAST_RESIDUAL_MIN_PAIRS,
    build_fast_station_residual_likelihood,
    latest_fast_station_conditioning,
    latest_fast_station_extreme_c,
)
from src.state.schema.observation_prints_schema import append_print, ensure_table

TARGET = "2026-09-17"


def _city(name: str):
    city = cities_by_name.get(name)
    if city is None:
        pytest.skip(f"{name} is not a configured city")
    return city


def _ledger() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


def _insert(conn, *, city, station, channel, publish, value, unit="C", raw=""):
    """Land one print through the schema module's own append path."""
    append_print(
        conn,
        city=city,
        station_id=station,
        source_channel=channel,
        publish_ts_utc=publish.isoformat(),
        value_native=float(value),
        unit=unit,
        fetched_at_utc=publish.isoformat(),
        raw_report=raw,
    )


def _stock_paired_window(conn, *, city, station, settlement_channel, unit, cutoff):
    """Write enough matched (settlement, fast) pairs to clear the residual minimum."""
    pairs = FAST_RESIDUAL_MIN_PAIRS + 5
    for index in range(pairs):
        stamp = cutoff - timedelta(hours=index + 2)
        _insert(conn, city=city, station=station, channel=settlement_channel,
                publish=stamp, value=10.0, unit=unit)
        _insert(conn, city=city, station=station, channel=FAST_OBS_SOURCE_ID,
                publish=stamp + timedelta(seconds=30), value=10.0, unit=unit)


def test_noaa_city_fast_extreme_is_read_not_silently_dropped():
    """The raw same-station extreme must be readable for a noaa city."""
    city = _city("Ankara")
    assert str(getattr(city, "settlement_source_type", "")).lower() == "noaa"
    station = str(getattr(city, "wu_station", "")).upper()
    decision = datetime(2026, 9, 17, 13, 57, 9, tzinfo=timezone.utc)
    conn = _ledger()
    _insert(conn, city=city.name, station=station, channel=FAST_OBS_SOURCE_ID,
            publish=decision - timedelta(minutes=2), value=18.0)
    result = latest_fast_station_extreme_c(
        conn, city=city.name, target_date=TARGET, metric="high",
        decision_time=decision,
    )
    assert result is not None, (
        "a noaa city's own fast print must be readable; returning None here is the "
        "defect that let a reached bin be sold as unreached"
    )
    assert result[0] == pytest.approx(18.0)


def test_noaa_residual_pairs_against_the_page_that_settles():
    """The residual model must pair the fast print with the noaa page, not WU's."""
    city = _city("Ankara")
    station = str(getattr(city, "wu_station", "")).upper()
    unit = str(getattr(city, "settlement_unit", "C")).upper()
    decision = datetime(2026, 9, 17, 13, 57, 9, tzinfo=timezone.utc)
    conn = _ledger()
    _stock_paired_window(conn, city=city.name, station=station,
                         settlement_channel=f"noaa_wrh_{station.lower()}",
                         unit=unit, cutoff=decision)
    observed_at = decision - timedelta(minutes=2)
    _insert(conn, city=city.name, station=station, channel=FAST_OBS_SOURCE_ID,
            publish=observed_at, value=18.0, unit=unit)
    likelihood = build_fast_station_residual_likelihood(
        conn, city=city.name, target_date=TARGET, metric="high",
        observed_source=FAST_OBS_SOURCE_ID, observation_time=observed_at,
        decision_time=decision,
    )
    assert likelihood is not None
    assert likelihood.settlement_channel == f"noaa_wrh_{station.lower()}", (
        "a noaa city settles off its weather.gov station page; pairing against "
        "wu_icao_history finds no rows and drops the whole lane to an inert None"
    )
    assert likelihood.matched_pairs >= FAST_RESIDUAL_MIN_PAIRS


def test_noaa_fast_print_supersedes_a_lagging_settlement_channel():
    """Conditioning must fire when the fast print is ahead of settlement truth."""
    city = _city("Ankara")
    station = str(getattr(city, "wu_station", "")).upper()
    unit = str(getattr(city, "settlement_unit", "C")).upper()
    decision = datetime(2026, 9, 17, 13, 57, 9, tzinfo=timezone.utc)
    conn = _ledger()
    _stock_paired_window(conn, city=city.name, station=station,
                         settlement_channel=f"noaa_wrh_{station.lower()}",
                         unit=unit, cutoff=decision)
    _insert(conn, city=city.name, station=station, channel=FAST_OBS_SOURCE_ID,
            publish=decision - timedelta(minutes=2), value=18.0, unit=unit)
    conditioning = latest_fast_station_conditioning(
        conn, city=city.name, target_date=TARGET, metric="high",
        decision_time=decision,
        # What the slow page had actually published by then.
        settlement_extreme_native=16.0, settlement_unit="C",
    )
    assert conditioning is not None
    assert conditioning.observed_extreme_c == pytest.approx(18.0)


def test_fast_print_below_settlement_truth_does_not_supersede():
    """A fast print that does not advance the extreme must stay inert."""
    city = _city("Ankara")
    station = str(getattr(city, "wu_station", "")).upper()
    unit = str(getattr(city, "settlement_unit", "C")).upper()
    decision = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
    conn = _ledger()
    _stock_paired_window(conn, city=city.name, station=station,
                         settlement_channel=f"noaa_wrh_{station.lower()}",
                         unit=unit, cutoff=decision)
    _insert(conn, city=city.name, station=station, channel=FAST_OBS_SOURCE_ID,
            publish=decision - timedelta(minutes=5), value=15.0, unit=unit)
    assert latest_fast_station_conditioning(
        conn, city=city.name, target_date=TARGET, metric="high",
        decision_time=decision, settlement_extreme_native=18.0,
        settlement_unit="C",
    ) is None


def test_thin_residual_window_stays_inert_rather_than_guessing():
    """Below the pair minimum the lane must degrade to None, never to a bare model."""
    city = _city("Ankara")
    station = str(getattr(city, "wu_station", "")).upper()
    decision = datetime(2026, 9, 17, 13, 57, 9, tzinfo=timezone.utc)
    conn = _ledger()
    observed_at = decision - timedelta(minutes=2)
    _insert(conn, city=city.name, station=station,
            channel=f"noaa_wrh_{station.lower()}",
            publish=observed_at - timedelta(minutes=1), value=16.0)
    _insert(conn, city=city.name, station=station, channel=FAST_OBS_SOURCE_ID,
            publish=observed_at, value=18.0)
    assert build_fast_station_residual_likelihood(
        conn, city=city.name, target_date=TARGET, metric="high",
        observed_source=FAST_OBS_SOURCE_ID, observation_time=observed_at,
        decision_time=decision,
    ) is None


def test_wu_city_still_pairs_against_its_own_history_page():
    """Widening the family set must not retarget the wu_icao residual channel."""
    city = _city("Auckland")
    if str(getattr(city, "settlement_source_type", "")).lower() != "wu_icao":
        pytest.skip("Auckland is no longer a wu_icao city")
    station = str(getattr(city, "wu_station", "")).upper()
    unit = str(getattr(city, "settlement_unit", "C")).upper()
    decision = datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc)
    conn = _ledger()
    _stock_paired_window(conn, city=city.name, station=station,
                         settlement_channel="wu_icao_history", unit=unit,
                         cutoff=decision)
    observed_at = decision - timedelta(minutes=2)
    _insert(conn, city=city.name, station=station, channel=FAST_OBS_SOURCE_ID,
            publish=observed_at, value=18.0, unit=unit)
    likelihood = build_fast_station_residual_likelihood(
        conn, city=city.name, target_date=TARGET, metric="high",
        observed_source=FAST_OBS_SOURCE_ID, observation_time=observed_at,
        decision_time=decision,
    )
    assert likelihood is not None
    assert likelihood.settlement_channel == "wu_icao_history"


def test_hko_city_remains_outside_the_icao_fast_lane():
    """Hong Kong names no ICAO station; it must not be swept in by the widening."""
    city = _city("Hong Kong")
    if str(getattr(city, "settlement_source_type", "")).lower() != "hko":
        pytest.skip("Hong Kong is no longer an hko city")
    conn = _ledger()
    assert latest_fast_station_extreme_c(
        conn, city=city.name, target_date=TARGET, metric="high",
        decision_time=datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc),
    ) is None
