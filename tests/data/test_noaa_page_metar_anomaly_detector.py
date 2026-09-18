"""The divergence detector must cover the family whose product now settles 48 cities.

`Day0FastObsEmitter.cached_anomaly_actions` built its rotation from
`settlement_source_type == "wu_icao"` only, so after the 2026-09-12 migration
(aeb934e00) the detector covered 4 cities and excluded 48. The durable flags table's
newest row predates that migration: the guard went silent exactly when it started
mattering. `is_day0_family_paused` gates q construction, the hard-fact exit lane, and the
resting-order cancel sweep (day0_oracle_anomaly.py:255-258), so for a noaa city all three
ran against a pause nothing could arm.

The gate was not simply wrong: the live callback compared METAR against WU via
`get_live_wu_observation`, and WU is not what a noaa city settles on. The comparison core
`check_wu_metar_divergence` was already family-agnostic in shape — it takes the comparand's
extremes, clock and coverage as plain arguments — so the missing piece was a page-side
supplier, not a second detector. The supplier reads page prints already durable in
`observation_prints` and never issues a request: the page's fetcher is per-IP quota limited
with an unretryable 403 (noaa_wrh_timeseries.py `_MIN_REQUEST_INTERVAL_SECONDS` = 2.0).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.config import cities_by_name
from src.data.day0_oracle_anomaly import (
    _page_running_extremes_from_ledger,
    page_metar_anomaly_action,
    settlement_metar_anomaly_action,
    wu_metar_anomaly_action,
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


def _page_print(conn, city, *, publish: datetime, value: float) -> None:
    append_print(
        conn,
        city=city.name,
        station_id=str(city.wu_station).upper(),
        source_channel=f"noaa_wrh_{str(city.wu_station).lower()}",
        publish_ts_utc=publish.isoformat(),
        value_native=float(value),
        unit=str(getattr(city, "settlement_unit", "C")).upper(),
        fetched_at_utc=publish.isoformat(),
        raw_report="",
    )


def _stock_page_day(conn, city, *, hours: int = 8, value: float = 18.0):
    """Page prints from local midnight onward, so coverage can read OK."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(str(city.timezone))
    start = datetime.fromisoformat(TARGET).replace(tzinfo=zone).astimezone(timezone.utc)
    for index in range(hours):
        _page_print(conn, city, publish=start + timedelta(hours=index), value=value)
    return start


def test_page_supplier_reads_the_running_extreme_from_the_ledger():
    """The comparand comes from durable prints, with no outgoing request."""
    city = _city("Ankara")
    assert str(getattr(city, "settlement_source_type", "")).lower() == "noaa"
    conn = _ledger()
    start = _stock_page_day(conn, city, hours=6, value=15.0)
    _page_print(conn, city, publish=start + timedelta(hours=7), value=18.0)
    page = _page_running_extremes_from_ledger(city, TARGET, conn=conn)
    assert page is not None
    high, low, last_publish, coverage, samples = page
    assert high == pytest.approx(18.0)
    assert low == pytest.approx(15.0)
    assert coverage == "OK"
    assert samples == 7
    assert last_publish == start + timedelta(hours=7)


def test_page_supplier_returns_none_when_the_ledger_has_no_page_print():
    """Absence of evidence is inert upstream, never a pause."""
    city = _city("Ankara")
    assert _page_running_extremes_from_ledger(city, TARGET, conn=_ledger()) is None


def test_page_supplier_ignores_a_reading_in_a_unit_the_city_does_not_settle_in():
    """Converting here would invent precision the page never published."""
    city = _city("Ankara")
    conn = _ledger()
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(str(city.timezone))
    start = datetime.fromisoformat(TARGET).replace(tzinfo=zone).astimezone(timezone.utc)
    append_print(
        conn,
        city=city.name,
        station_id=str(city.wu_station).upper(),
        source_channel=f"noaa_wrh_{str(city.wu_station).lower()}",
        publish_ts_utc=start.isoformat(),
        value_native=64.0,
        unit="F",
        fetched_at_utc=start.isoformat(),
        raw_report="",
    )
    assert _page_running_extremes_from_ledger(city, TARGET, conn=conn) is None


def test_router_sends_a_noaa_city_to_the_page_supplier():
    """The settlement product decides the comparand, not the caller."""
    city = _city("Ankara")
    seen: list[str] = []

    class _Extremes:
        target_date = TARGET

    import src.data.day0_oracle_anomaly as module

    original = module.page_metar_anomaly_action
    module.page_metar_anomaly_action = (
        lambda c, e, r, **kw: seen.append("page") or None
    )
    try:
        settlement_metar_anomaly_action(city, _Extremes(), [])
    finally:
        module.page_metar_anomaly_action = original
    assert seen == ["page"]


def test_router_still_sends_a_wu_city_to_the_wu_supplier():
    """Widening the rotation must not retarget the wu_icao comparand."""
    city = _city("Auckland")
    if str(getattr(city, "settlement_source_type", "")).lower() != "wu_icao":
        pytest.skip("Auckland is no longer a wu_icao city")
    seen: list[str] = []

    class _Extremes:
        target_date = TARGET

    import src.data.day0_oracle_anomaly as module

    original = module.wu_metar_anomaly_action
    module.wu_metar_anomaly_action = lambda c, e, r: seen.append("wu") or None
    try:
        settlement_metar_anomaly_action(city, _Extremes(), [])
    finally:
        module.wu_metar_anomaly_action = original
    assert seen == ["wu"]


def test_router_is_inert_for_a_family_with_no_icao_comparand():
    """Hong Kong publishes its own products and names no ICAO station."""
    city = _city("Hong Kong")
    if str(getattr(city, "settlement_source_type", "")).lower() != "hko":
        pytest.skip("Hong Kong is no longer an hko city")

    class _Extremes:
        target_date = TARGET

    assert settlement_metar_anomaly_action(city, _Extremes(), []) is None


def test_noaa_cities_are_rotation_eligible_for_the_detector():
    """The rotation must admit the family the pause now has to protect."""
    from src.data.day0_fast_obs import _FAST_LANE_SETTLEMENT_SOURCE_TYPES

    assert "noaa" in _FAST_LANE_SETTLEMENT_SOURCE_TYPES
    assert "wu_icao" in _FAST_LANE_SETTLEMENT_SOURCE_TYPES
    assert "hko" not in _FAST_LANE_SETTLEMENT_SOURCE_TYPES


def test_page_side_unavailable_does_not_flag():
    """A missing comparand must not pause a family; it arms the retry throttle."""
    city = _city("Ankara")
    import src.data.day0_oracle_anomaly as module

    with module._WU_CHECK_MEMO_LOCK:
        module._WU_CHECK_MEMO.pop(city.name, None)
        module._WU_CHECK_FAILURE_MEMO.pop(city.name, None)

    class _Extremes:
        target_date = TARGET

    action = page_metar_anomaly_action(city, _Extremes(), [], conn=_ledger())
    assert action is None
    with module._WU_CHECK_MEMO_LOCK:
        assert city.name in module._WU_CHECK_FAILURE_MEMO
        assert city.name not in module._WU_CHECK_MEMO
