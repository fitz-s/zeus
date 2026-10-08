# Created: 2026-10-08
# Last audited: 2026-10-08
# Authority basis: G9 (task_2026-10-06_fast_obs_closure): the degF NOAA page rewrites a
#   5-minute-grid SPECI cell from its T-group tenths to the ASOS 5-minute whole degree C
#   (31/31 revisions 2026-10-01..05; live page re-read 2026-10-07 20:05Z shows the whole
#   degree for all 31). A MONOTONE page boundary may only claim what both endings share.
# Purpose: The page boundary never claims more than either ending of a grid-clock print.
# Reuse: Run when _latest_authorized_day0_fact's page reduction, the page finality law or
#   noaa_page_absorbing_value_f changes.
"""The degF page boundary holds under the tenths-to-whole-degree rewrite."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.contracts.settlement_semantics import SettlementSemantics
from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
from src.events.day0_authority import noaa_page_absorbing_value_f
from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc


def _ledger(city: str, station: str, rows) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    for clock, value, receipt in rows:
        append_print(conn, city=city, station_id=station,
                     source_channel=f"noaa_wrh_{station.lower()}",
                     publish_ts_utc=clock.isoformat(), value_native=value, unit="F",
                     fetched_at_utc=receipt.isoformat(), raw_report=None)
    conn.commit()
    return conn


def _bound(conn, city, target_date, metric, at):
    fact = _latest_authorized_day0_fact(conn, city=city, target_date=target_date,
                                        temperature_metric=metric, decision_time=at,
                                        require_settlement_channel=True)
    return fact["observed_extreme_native"]


def _settled(city: str, value: float) -> int:
    from src.config import cities_by_name

    return int(SettlementSemantics.for_city(cities_by_name[city]).round_single(value))


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# KAUS 2026-10-02 (Austin local day 05Z..05Z), verbatim noaa_wrh_kaus ledger rows: the
# 11:30Z SPECI (T02330228) first read 73.94 (23.3 C), then 73.4 (23 C) 3.3 min later.
# The page settled LOW 73 (settlement_outcomes Austin 2026-10-02 low = 73, bin 72-73).
_KAUS_1002 = [
    ("2026-10-02T05:53", 75.02, "2026-10-02T05:58:40"),
    ("2026-10-02T09:53", 75.02, "2026-10-02T09:57:00"),
    ("2026-10-02T10:33", 73.94, "2026-10-02T10:38:56"),
    ("2026-10-02T10:53", 73.94, "2026-10-02T10:57:36"),
    ("2026-10-02T11:30", 73.94, "2026-10-02T11:35:34"),
    ("2026-10-02T11:30", 73.4, "2026-10-02T11:38:51"),
    ("2026-10-02T11:53", 73.94, "2026-10-02T11:58:02"),
]


def test_kaus_2026_10_02_low_boundary_stays_true_through_the_revision():
    conn = _ledger("Austin", "KAUS", [(_t(c), v, _t(r)) for c, v, r in _KAUS_1002])
    try:
        # A LOW boundary B asserts min <= B. In the 3.3-minute window only the tenths
        # version exists: 73.94 (74) is the weaker side of {73.94, 73.4}, true under
        # either ending, and it never excludes the settled 73.
        stale = _bound(conn, "Austin", "2026-10-02", "low", _t("2026-10-02T11:36:00"))
        assert stale == 73.94 and _settled("Austin", stale) >= 73
        revised = _bound(conn, "Austin", "2026-10-02", "low", _t("2026-10-02T12:00:00"))
        assert revised == 73.4 and _settled("Austin", revised) == 73
        assert _bound(conn, "Austin", "2026-10-02", "high", _t("2026-10-02T11:36:00")) == 75.02
    finally:
        conn.close()


# KMIA 2026-10-04 (Miami local day 04Z..04Z), verbatim noaa_wrh_kmia ledger rows: the 04:05Z
# SPECI (T02670228) first read 80.06 (26.7 C), then 80.6 (27 C) 13.4 min later. LOW revised
# up: the old reduction's LOW 80.06 (80) asserted min <= 80 while the page said 80.6 (81).
_KMIA_1004 = [
    ("2026-10-04T04:05", 80.06, "2026-10-04T04:09:11"),
    ("2026-10-04T04:05", 80.6, "2026-10-04T04:22:33"),
    ("2026-10-04T04:53", 80.06, "2026-10-04T04:58:04"),
]


def test_kmia_2026_10_04_low_revised_up_never_claims_below_the_page():
    conn = _ledger("Miami", "KMIA", [(_t(c), v, _t(r)) for c, v, r in _KMIA_1004])
    try:
        in_window = _bound(conn, "Miami", "2026-10-04", "low", _t("2026-10-04T04:15:00"))
        assert in_window == 80.6 and _settled("Miami", in_window) == 81
        assert _bound(conn, "Miami", "2026-10-04", "low", _t("2026-10-04T04:30:00")) == 80.6
        # The routine 04:53Z report (off-grid, final as published) later sets LOW 80.06.
        assert _bound(conn, "Miami", "2026-10-04", "low", _t("2026-10-04T05:00:00")) == 80.06
        assert _bound(conn, "Miami", "2026-10-04", "high", _t("2026-10-04T04:15:00")) == 80.06
    finally:
        conn.close()


def test_synthetic_high_revised_down_never_sets_a_boundary_above_the_final_page():
    # A grid-clock SPECI first reads 75.92 (24.4 C tenths) and is revised to 75.2 (24 C).
    # The old reduction held HIGH 76 until the revision; the page's final cell settles 75.
    rows = [
        (_t("2026-10-03T12:53"), 73.94, _t("2026-10-03T12:57")),
        (_t("2026-10-03T13:15"), 75.92, _t("2026-10-03T13:21")),
        (_t("2026-10-03T13:15"), 75.2, _t("2026-10-03T13:28")),
    ]
    conn = _ledger("Austin", "KAUS", rows)
    try:
        stale = _bound(conn, "Austin", "2026-10-03", "high", _t("2026-10-03T13:22:00"))
        final = _bound(conn, "Austin", "2026-10-03", "high", _t("2026-10-03T14:00:00"))
        assert _settled("Austin", stale) <= _settled("Austin", final) == 75
    finally:
        conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_a_kept_tenths_value_still_bounds_in_its_absorbing_direction(metric):
    # KSEA 2026-10-07 10:20Z kept its tenths value (55.04 = 12.8 C) on the page; the bound
    # claims only what 55.04 and 55.4 share, so a merge that never comes is not assumed.
    clock = _t("2026-10-07T10:20")
    conn = _ledger("Seattle", "KSEA", [(clock, 55.04, _t("2026-10-07T10:24:33"))])
    try:
        bound = _bound(conn, "Seattle", "2026-10-07", metric, _t("2026-10-07T11:00:00"))
        assert bound == (55.04 if metric == "high" else 55.4)
    finally:
        conn.close()


@pytest.mark.parametrize("clock,value,metric,expected", [
    # Grid clock, tenths value: the bound is the side both endings share.
    ("2026-10-04T04:05", 80.06, "low", 80.6),
    ("2026-10-04T04:05", 80.06, "high", 80.06),
    ("2026-10-03T13:15", 75.92, "high", 75.2),
    ("2026-10-03T13:15", 75.92, "low", 75.92),
    ("2026-10-02T11:30", 73.94, "low", 73.94),
    ("2026-10-02T11:30", 73.94, "high", 73.4),
    ("2026-10-05T19:45", 55.94, "high", 55.4),
    ("2026-10-01T17:25", 78.08, "high", 78.08),
    ("2026-10-01T17:25", 78.08, "low", 78.8),
    # Whole degree C at a grid clock, off-grid SPECIs and routine reports are final.
    ("2026-10-04T04:05", 80.6, "low", 80.6),
    ("2026-10-04T04:53", 80.06, "low", 80.06),
    ("2026-10-02T10:33", 73.94, "low", 73.94),
    # A value that is not a 0.1 C conversion is not a page tenths cell: unchanged.
    ("2026-10-04T04:05", 80.0, "low", 80.0),
])
def test_page_absorbing_value_is_true_under_either_ending(clock, value, metric, expected):
    assert noaa_page_absorbing_value_f(value, observed_at=_t(clock), metric=metric) == expected


def test_every_measured_revision_is_bounded_by_its_first_version():
    """The 31 measured revisions: the bound from the first version never passes the final."""
    measured = [  # (clock, first, final) from observation_prints 2026-10-01..05
        ("2026-10-01T05:40", 71.06, 71.6), ("2026-10-01T13:05", 78.98, 78.8),
        ("2026-10-01T13:55", 69.98, 69.8), ("2026-10-01T15:45", 57.02, 57.2),
        ("2026-10-01T17:25", 78.08, 78.8), ("2026-10-01T21:20", 73.94, 73.4),
        ("2026-10-01T21:45", 75.02, 75.2), ("2026-10-02T03:30", 73.94, 73.4),
        ("2026-10-02T05:30", 82.04, 82.4), ("2026-10-02T06:15", 75.02, 75.2),
        ("2026-10-02T11:30", 73.94, 73.4), ("2026-10-02T13:40", 73.94, 73.4),
        ("2026-10-02T14:30", 57.02, 57.2), ("2026-10-02T15:25", 75.02, 75.2),
        ("2026-10-02T17:15", 75.02, 75.2), ("2026-10-02T17:30", 75.02, 75.2),
        ("2026-10-03T02:05", 73.94, 73.4), ("2026-10-03T12:35", 71.06, 71.6),
        ("2026-10-03T13:15", 75.92, 75.2), ("2026-10-04T04:05", 80.06, 80.6),
        ("2026-10-04T06:10", 71.96, 71.6), ("2026-10-04T12:40", 53.96, 53.6),
        ("2026-10-04T12:45", 71.06, 71.6), ("2026-10-04T14:30", 53.06, 53.6),
        ("2026-10-04T18:20", 55.04, 55.4), ("2026-10-04T18:35", 73.94, 73.4),
        ("2026-10-04T23:00", 57.92, 57.2), ("2026-10-05T11:20", 69.08, 69.8),
        ("2026-10-05T13:40", 69.08, 69.8), ("2026-10-05T18:00", 55.04, 55.4),
        ("2026-10-05T19:45", 55.94, 55.4),
    ]
    assert len(measured) == 31
    for clock, first, final in measured:
        at = _t(clock)
        assert noaa_page_absorbing_value_f(first, observed_at=at, metric="high") <= final + 1e-9
        assert noaa_page_absorbing_value_f(first, observed_at=at, metric="low") >= final - 1e-9
        assert noaa_page_absorbing_value_f(final, observed_at=at, metric="high") == final
        assert noaa_page_absorbing_value_f(final, observed_at=at, metric="low") == final


def test_fast_residual_settlement_extreme_reads_the_latest_page_version(monkeypatch):
    """The residual's settlement_extreme truncates every scenario; a superseded page
    version must not stay in it (all-versions LOW 80.06 vs latest 80.6, KMIA 2026-10-04)."""
    from src.data import day0_fast_obs as fast
    from src.config import cities_by_name

    city = cities_by_name["Miami"]
    cutoff = _t("2026-10-04T12:00")
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    for index in range(fast.FAST_RESIDUAL_MIN_PAIRS):
        observed = cutoff - timedelta(hours=index + 1, minutes=7)  # :53, off-grid, final
        raw = f"KMIA {observed:%d%H%M}Z 00000KT 10SM CLR 27/23 A3000 RMK AO2 T02720228"
        for channel, value, unit in (("noaa_wrh_kmia", 81.14, "F"),
                                     ("aviationweather_metar", 27.2, "C")):
            append_print(conn, city="Miami", station_id="KMIA", source_channel=channel,
                         publish_ts_utc=observed.isoformat(), value_native=value, unit=unit,
                         fetched_at_utc=(observed + timedelta(minutes=4)).isoformat(),
                         raw_report=raw)
    spec = _t("2026-10-04T04:05")
    for value, receipt in ((80.06, "2026-10-04T04:09:11"), (80.6, "2026-10-04T04:22:33")):
        append_print(conn, city="Miami", station_id="KMIA", source_channel="noaa_wrh_kmia",
                     publish_ts_utc=spec.isoformat(), value_native=value, unit="F",
                     fetched_at_utc=_t(receipt).isoformat(),
                     raw_report="KMIA 040405Z 19005KT 10SM TS 27/23 A3006 RMK AO2 T02670228")
    conn.commit()
    try:
        model = fast.build_fast_station_residual_likelihood(
            conn, city=city.name, target_date="2026-10-04", metric="low",
            observed_source=fast.FAST_OBS_SOURCE_ID, observation_time=cutoff,
            decision_time=cutoff)
        assert model is not None
        assert _settled("Miami", model.settlement_extreme_c * 1.8 + 32.0) == 81
    finally:
        conn.close()


def test_celsius_page_values_pass_through_the_reducer_unchanged():
    # degC page rows are whole degrees (0 revisions in 33,439); the reducer applies the
    # bound to degF page channels only.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    clock = _t("2026-10-05T06:00")
    append_print(conn, city="Paris", station_id="LFPB", source_channel="noaa_wrh_lfpb",
                 publish_ts_utc=clock.isoformat(), value_native=14.0, unit="C",
                 fetched_at_utc=(clock + timedelta(minutes=5)).isoformat(), raw_report=None)
    conn.commit()
    try:
        for metric in ("high", "low"):
            assert _bound(conn, "Paris", "2026-10-05", metric, clock + timedelta(hours=1)) == 14.0
    finally:
        conn.close()
