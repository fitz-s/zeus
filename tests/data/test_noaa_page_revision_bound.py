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
from tests.test_noaa_wrh_settlement_product import _live_schema

UTC = timezone.utc


def _ledger(city: str, station: str, rows) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Prove that this fixture has no current-product owner. An absent owner
    # schema is unknown authority and must not authorize the ledger fallback.
    for statement in _live_schema()["forecasts"]:
        conn.execute(statement)
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
    conn = _ledger("Paris", "LFPB", [])
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


def test_missing_current_owner_schema_cannot_authorize_a_ledger_bound(monkeypatch):
    from contextlib import nullcontext

    from src.contracts.exceptions import ObservationUnavailableError

    clock = _t("2026-10-03T13:15")
    now = clock + timedelta(minutes=10)
    conn = _ledger("Austin", "KAUS", [(clock, 75.2, now)])
    try:
        assert _bound(conn, "Austin", "2026-10-03", "high", now) == 75.2
        conn.execute("DROP TABLE observations")
        monkeypatch.setattr(
            "src.state.db.get_forecasts_connection_with_world_read_only",
            lambda **_kwargs: nullcontext(conn),
        )
        with pytest.raises(ObservationUnavailableError, match="WRH_CURRENT_SNAPSHOT_UNAVAILABLE"):
            _bound(conn, "Austin", "2026-10-03", "high", now)
    finally:
        conn.close()


@pytest.mark.parametrize("metric,first,corrected", [
    ("high", 75.92, 75.2),
    ("low", 80.06, 80.6),
])
def test_current_grid_print_keeps_native_value_and_provisional_revision(
    tmp_path, metric, first, corrected,
):
    """The complete current owner is a revisable product, not the ledger bound."""
    import hashlib
    import json
    from dataclasses import replace

    from src.config import cities_by_name
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.noaa_wrh_timeseries import product_from_response
    from src.engine.current_day0_observation import current_wrh_day0_observation_carrier
    from src.events.day0_authority import DAY0_PROVISIONAL_CURRENT_SNAPSHOT, day0_evidence_finality
    from tests.test_noaa_wrh_settlement_product import _attached, _live_schema_db_pair

    city = cities_by_name["Austin"]
    clock = _t("2026-10-03T13:15")
    received = _t("2026-10-03T13:21")
    conn = _attached(*_live_schema_db_pair(tmp_path))
    carriers = []
    try:
        assert noaa_page_absorbing_value_f(first, observed_at=clock, metric=metric) == corrected
        for minute, value in enumerate((first, corrected)):
            now = received + timedelta(minutes=minute)
            body = json.dumps({
                "SUMMARY": {"RESPONSE_CODE": 1}, "UNITS": {"air_temp": "Fahrenheit"},
                "STATION": [{"STID": "KAUS", "OBSERVATIONS": {
                    "date_time": ["2026-10-03T08:15:00-0500"],
                    "air_temp_set_1": [value], "sea_level_pressure_set_1": [1010],
                }}],
            }).encode()
            product = replace(
                product_from_response(body, "KAUS", unit="F", fetched_at=now,
                                      source_response_sha256=hashlib.sha256(body).hexdigest()),
                request_started_at=now - timedelta(seconds=1),
                coverage_start_utc=_t("2026-10-03T05:00"),
                coverage_end_utc=now - timedelta(seconds=1),
            )
            assert append_current_noaa_wrh_product(
                conn, city=city, target_date="2026-10-03", product=product, as_of=now,
            ) == ("inserted" if minute == 0 else "revision")
            conn.commit()
            carrier = current_wrh_day0_observation_carrier(
                conn, city=city, target_date="2026-10-03", metric=metric, now=now,
            )
            payload = json.loads(carrier.payload_json)
            assert payload["raw_value"] == value
            assert payload["raw_report_identity"] == product.response_sha256
            assert payload["evidence_finality"] == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
            assert day0_evidence_finality(payload) == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
            carriers.append(carrier)
        assert carriers[0].causal_snapshot_id != carriers[1].causal_snapshot_id
        historical = current_wrh_day0_observation_carrier(
            conn, city=city, target_date="2026-10-03", metric=metric, now=received,
        )
        assert historical.causal_snapshot_id == carriers[0].causal_snapshot_id
        assert json.loads(historical.payload_json)["raw_value"] == first
    finally:
        conn.close()


@pytest.fixture
def native_hard_fact_page(tmp_path):
    from types import SimpleNamespace
    from src.config import cities_by_name
    from tests.test_noaa_wrh_settlement_product import _attached, _live_schema_db_pair

    forecasts, world = _live_schema_db_pair(tmp_path)
    conn = _attached(forecasts, world)
    case = SimpleNamespace(conn=conn, forecasts=forecasts, world=world,
                           city=cities_by_name["Austin"], day="2026-10-03",
                           now=_t("2026-10-03T13:21"))
    try:
        yield case
    finally:
        conn.close()


def _write_hard_fact_page(case, rows, *, now=None, request_started=None, coverage_end=None):
    """Native row tuples are (UTC clock, value, official-view membership)."""
    import hashlib
    import json
    from dataclasses import replace
    from zoneinfo import ZoneInfo
    from src.data.daily_obs_append import append_current_noaa_wrh_product
    from src.data.noaa_wrh_timeseries import product_from_response

    now = now or case.now
    zone = ZoneInfo(case.city.timezone)
    unit = case.city.settlement_unit
    body = json.dumps({
        "SUMMARY": {"RESPONSE_CODE": 1},
        "UNITS": {"air_temp": "Fahrenheit" if unit == "F" else "Celsius"},
        "STATION": [{"STID": case.city.wu_station, "OBSERVATIONS": {
            "date_time": [clock.astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S%z") for clock, _, _ in rows],
            "air_temp_set_1": [value for _, value, _ in rows],
            "sea_level_pressure_set_1": [1010 if official else None for _, _, official in rows],
        }}],
    }).encode()
    product = replace(
        product_from_response(body, case.city.wu_station, unit=unit, fetched_at=now,
                              source_response_sha256=hashlib.sha256(body).hexdigest()),
        request_started_at=request_started or now - timedelta(seconds=1),
        coverage_start_utc=datetime.fromisoformat(case.day).replace(tzinfo=zone).astimezone(UTC) - timedelta(hours=1),
        coverage_end_utc=coverage_end or now - timedelta(seconds=1),
    )
    append_current_noaa_wrh_product(case.conn, city=case.city, target_date=case.day,
                                   product=product, as_of=now)
    case.conn.commit()
    case.now = now
    return product


def _hard_fact_position(case, metric, direction, label):
    from types import SimpleNamespace
    return SimpleNamespace(city=case.city.name, target_date=case.day,
                           temperature_metric=metric, direction=direction, bin_label=label,
                           trade_id="grid-proof", state="day0_window")


def _hard_fact_verdict(case, pos):
    from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit
    return evaluate_hard_fact_exit(position=pos, city=case.city, now=case.now,
                                   world_conn=case.conn, durable_only=True)


def _hard_fact_evidence(case, metric, *, complete_day=False):
    from src.execution.day0_hard_fact_exit import _noaa_wrh_hard_fact_evidence
    return _noaa_wrh_hard_fact_evidence(city=case.city, target_date=case.day, metric=metric,
                                      now=case.now, world_conn=case.conn, complete_day=complete_day)


@pytest.mark.parametrize("metric,raw,label", [
    ("high", 75.92, "75°F"), ("high", 75.92, "76°F or higher"),
    ("low", 80.06, "81°F"), ("low", 80.06, "80°F or below"),
])
@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_current_grid_print_cannot_create_false_exact_payoff(native_hard_fact_page, metric, raw, label, direction):
    from src.engine.monitor_refresh import _exact_hard_fact_probability_receipt
    case = native_hard_fact_page
    _write_hard_fact_page(case, [(_t("2026-10-03T13:15"), raw, True)])
    pos = _hard_fact_position(case, metric, direction, label)
    verdict = _hard_fact_verdict(case, pos)
    assert verdict is None
    assert _exact_hard_fact_probability_receipt(pos, verdict, held_probability=0.0) is None


@pytest.mark.parametrize("metric,raw,bound,label,yes_action", [
    ("high", 75.92, 75.2, "74°F", "EXIT_DEAD_BIN"),
    ("high", 75.92, 75.2, "75°F or higher", "HOLD_STRUCTURAL_WIN"),
    ("low", 80.06, 80.6, "82°F", "EXIT_DEAD_BIN"),
    ("low", 80.06, 80.6, "81°F or below", "HOLD_STRUCTURAL_WIN"),
])
@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_current_bound_preserves_true_exact_payoff_and_provenance(
    native_hard_fact_page, metric, raw, bound, label, yes_action, direction,
):
    import json
    from src.engine.current_day0_observation import current_wrh_day0_observation_carrier
    from src.engine.monitor_refresh import _exact_hard_fact_probability_receipt
    case = native_hard_fact_page
    clock = _t("2026-10-03T13:15")
    product = _write_hard_fact_page(case, [(clock, raw, True)])
    pos = _hard_fact_position(case, metric, direction, label)
    verdict = _hard_fact_verdict(case, pos)
    expected = yes_action if direction == "buy_yes" else (
        "HOLD_STRUCTURAL_WIN" if yes_action == "EXIT_DEAD_BIN" else "EXIT_DEAD_BIN")
    assert verdict.action == expected
    evidence = verdict.evidence.as_dict()
    assert evidence["raw_extreme"] == raw
    assert evidence["payload_identity"] == product.response_sha256
    assert evidence["absorbing_bound"] == {
        "value": bound, "raw_value": raw, "observed_at": clock.isoformat(),
        "basis": "noaa_page_grid_clock_common_ending_v1",
    }
    probability = 0.0 if expected == "EXIT_DEAD_BIN" else 1.0
    receipt = _exact_hard_fact_probability_receipt(pos, verdict, held_probability=probability)
    assert receipt["hard_fact_evidence"] == evidence
    assert receipt["held_side_probability"] == probability
    carrier = current_wrh_day0_observation_carrier(case.conn, city=case.city,
        target_date=case.day, metric=metric, now=case.now)
    payload = json.loads(carrier.payload_json)
    assert payload["raw_value"] == raw
    assert payload["evidence_finality"] == "PROVISIONAL_CURRENT_SNAPSHOT"
    assert payload["raw_report_identity"] == product.response_sha256


@pytest.mark.parametrize("metric,grid,other,bound", [
    ("high", 75.92, 75.56, 75.56), ("low", 80.06, 80.42, 80.42),
])
@pytest.mark.parametrize("view", ["hourly", "all"])
def test_current_bound_reduces_transformed_rows_in_the_contract_view(
    native_hard_fact_page, monkeypatch, metric, grid, other, bound, view,
):
    from dataclasses import replace
    from src.config import cities_by_name
    case = native_hard_fact_page
    case.city = replace(case.city, settlement_page_view=view)
    monkeypatch.setitem(cities_by_name, case.city.name, case.city)
    grid_clock, other_clock = _t("2026-10-03T13:15"), _t("2026-10-03T13:13")
    excluded = 100.0 if metric == "high" else 10.0
    rows = [(grid_clock, grid, True), (other_clock, other, True),
            (_t("2026-10-03T13:11"), excluded, False),
            (_t("2026-10-03T04:53"), excluded, True)]  # previous local day
    _write_hard_fact_page(case, rows)
    evidence = _hard_fact_evidence(case, metric)
    if view == "hourly":
        assert evidence.raw_extreme == grid
        assert datetime.fromisoformat(evidence.observed_at) == grid_clock
        assert evidence.bound_value == bound and evidence.bound_raw_value == other
        assert datetime.fromisoformat(evidence.bound_observed_at) == other_clock
        # Transforming only the raw winner would wrongly abstain here. The
        # different bound-winning row still proves these exact YES/NO twins.
        from src.engine.monitor_refresh import _exact_hard_fact_probability_receipt
        label = "75°F" if metric == "high" else "81°F"
        for direction, action, probability in (
            ("buy_yes", "EXIT_DEAD_BIN", 0.0), ("buy_no", "HOLD_STRUCTURAL_WIN", 1.0),
        ):
            pos = _hard_fact_position(case, metric, direction, label)
            verdict = _hard_fact_verdict(case, pos)
            assert verdict.action == action
            receipt = _exact_hard_fact_probability_receipt(pos, verdict, held_probability=probability)
            assert receipt["hard_fact_evidence"]["absorbing_bound"] == evidence.as_dict()["absorbing_bound"]
    else:
        assert evidence.raw_extreme == evidence.bound_value == excluded
        assert datetime.fromisoformat(evidence.bound_observed_at) == _t("2026-10-03T13:11")


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("city_name,clock,raw", [
    ("Austin", "2026-10-03T13:13", 75.92),
    ("Austin", "2026-10-03T13:15", 75.2),
    ("Singapore", "2026-10-03T13:15", 24.4),
])
def test_unrevised_page_values_keep_existing_hard_fact_meaning(
    native_hard_fact_page, metric, city_name, clock, raw,
):
    from src.config import cities_by_name
    case = native_hard_fact_page
    case.city = cities_by_name[city_name]
    _write_hard_fact_page(case, [(_t(clock), raw, True)])
    evidence = _hard_fact_evidence(case, metric)
    assert evidence.raw_extreme == evidence.bound_value == raw


@pytest.mark.parametrize("metric,raw,bound", [("high", 75.92, 75.2), ("low", 80.06, 80.6)])
def test_current_final_page_value_is_not_replaced_by_directional_bound(native_hard_fact_page, metric, raw, bound):
    from src.execution.day0_hard_fact_exit import _final_daily_observation_extreme
    case = native_hard_fact_page
    rows = [(_t("2026-10-03T13:15"), raw, True)]
    _write_hard_fact_page(case, rows)
    assert _hard_fact_evidence(case, metric, complete_day=True) is None
    final_time = _t("2026-10-04T05:02")
    case.now = final_time
    assert _hard_fact_evidence(case, metric, complete_day=True) is None
    _write_hard_fact_page(case, rows, now=final_time)
    final = _final_daily_observation_extreme(city=case.city, target_date=case.day,
                                            metric=metric, now=case.now, conn=case.conn)
    assert final.raw_extreme == raw
    assert final.settled_extreme == _settled(case.city.name, raw)
    assert final.settled_extreme != _settled(case.city.name, bound)
    exact = _hard_fact_evidence(case, metric, complete_day=True)
    assert "absorbing_bound" not in exact.as_dict()
    assert _hard_fact_evidence(case, metric).bound_value == bound


@pytest.mark.parametrize("fault", ["empty", "unreadable", "missing_body", "membership", "unknown_source"])
@pytest.mark.parametrize("metric,raw", [("high", 75.92), ("low", 80.06)])
def test_current_bound_cannot_outlive_native_owner_proof(native_hard_fact_page, fault, metric, raw):
    from dataclasses import replace
    import json
    from src.config import state_path
    case = native_hard_fact_page
    product = _write_hard_fact_page(case, [(_t("2026-10-03T13:15"), raw, True)])
    assert _hard_fact_evidence(case, metric) is not None
    if fault == "empty":
        _write_hard_fact_page(case, [], now=case.now + timedelta(minutes=1))
    elif fault == "unreadable":
        case.conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and name == "observations" else sqlite3.SQLITE_OK)
    elif fault == "missing_body":
        (state_path("noaa_wrh_response_bodies") / (product.response_sha256 + ".zlib")).unlink()
    elif fault == "membership":
        row = case.conn.execute("SELECT high_provenance_metadata FROM observations").fetchone()
        proof = json.loads(row[0])
        proof["wrh_current_snapshot"]["rows"][0]["air_temp"] = 99.0
        case.conn.execute("UPDATE observations SET high_provenance_metadata=?", (json.dumps(proof),))
        case.conn.commit()
    else:
        case.city = replace(case.city, settlement_source_type="unknown")
    assert _hard_fact_evidence(case, metric) is None


@pytest.mark.parametrize("metric,raw,corrected,label", [
    ("high", 75.92, 73.4, "74°F"), ("low", 80.06, 82.4, "82°F"),
])
@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_native_bound_receipt_is_reproved_against_current_owner_at_submit(
    native_hard_fact_page, monkeypatch, metric, raw, corrected, label, direction,
):
    from src.engine.monitor_refresh import _exact_hard_fact_probability_receipt
    from src.execution import exit_lifecycle
    case = native_hard_fact_page
    # Both directions have a genuine loss: finite YES or the corresponding NO shoulder.
    if direction == "buy_no":
        label = "75°F or higher" if metric == "high" else "81°F or below"
    rows = [(_t("2026-10-03T13:15"), raw, True)]
    _write_hard_fact_page(case, rows)
    pos = _hard_fact_position(case, metric, direction, label)
    verdict = _hard_fact_verdict(case, pos)
    assert verdict.action == "EXIT_DEAD_BIN"
    receipt = _exact_hard_fact_probability_receipt(pos, verdict, held_probability=0.0)
    with sqlite3.connect(":memory:", uri=True) as trade:
        trade.row_factory = sqlite3.Row
        trade.execute("ATTACH DATABASE ? AS forecasts", (f"file:{case.forecasts}?mode=ro",))
        trade.execute("ATTACH DATABASE ? AS world", (f"file:{case.world}?mode=ro",))
        trade.execute("""CREATE TABLE position_current (position_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, bin_label TEXT, direction TEXT, condition_id TEXT,
            token_id TEXT, no_token_id TEXT, phase TEXT)""")
        trade.execute("INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?,?)", (
            pos.trade_id, pos.city, pos.target_date, metric, label, direction, "condition",
            "yes", "no", pos.state))
        trade.commit()
        monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: case.now)
        assert exit_lifecycle._protective_source_receipt_current(
            trade, position_id=pos.trade_id, receipt=receipt)
        _write_hard_fact_page(case, [(_t("2026-10-03T13:15"), corrected, True)],
                              now=case.now + timedelta(minutes=1))
        assert _hard_fact_verdict(case, pos) is None
        assert not exit_lifecycle._protective_source_receipt_current(
            trade, position_id=pos.trade_id, receipt=receipt)


@pytest.mark.parametrize("metric,raw,bound", [("high", 75.92, 75.2), ("low", 80.06, 80.6)])
def test_legacy_page_scalar_proves_only_its_own_bound(native_hard_fact_page, metric, raw, bound):
    import json
    case = native_hard_fact_page
    clock = _t("2026-10-03T13:15")
    product = _write_hard_fact_page(case, [(clock, raw, True)])
    # Retain an authentic legacy canonical scalar shape, without claiming
    # complete native membership. No unseen competing row is reconstructed.
    proof = {"upstream": "weather.gov_wrh_timeseries", "station": case.city.wu_station,
             "settlement_page_view": case.city.settlement_page_view,
             "payload_hash": "sha256:" + product.response_sha256,
             "high_local_timestamp": clock.isoformat(), "low_local_timestamp": clock.isoformat()}
    case.conn.execute("UPDATE observations SET rebuild_run_id='legacy_page', high_provenance_metadata=?, low_provenance_metadata=?",
                      (json.dumps(proof), json.dumps(proof)))
    case.conn.commit()
    # The typed current owner was UNVERIFIED intraday. Removing its native
    # proof cannot silently satisfy the older scalar reader's VERIFIED gate.
    assert _hard_fact_evidence(case, metric) is None
    case.conn.execute("UPDATE observations SET authority='VERIFIED'")
    case.conn.commit()
    evidence = _hard_fact_evidence(case, metric)
    assert evidence.raw_extreme == evidence.bound_raw_value == raw
    assert evidence.bound_value == bound
    assert evidence.rounded_extreme == _settled(case.city.name, bound)
    assert _hard_fact_evidence(case, metric, complete_day=True) is None
    case.now = _t("2026-10-04T05:02")
    case.conn.execute("UPDATE observations SET fetched_at=?,high_fetch_utc=?,low_fetch_utc=?",
                      (case.now.isoformat(),) * 3)
    case.conn.commit()
    final = _hard_fact_evidence(case, metric, complete_day=True)
    assert final.raw_extreme == raw
    assert final.rounded_extreme == _settled(case.city.name, raw)
    assert "absorbing_bound" not in final.as_dict()


@pytest.mark.parametrize("metric,raw,label,still_dead", [
    ("high", 75.92, "75°F", False), ("low", 80.06, "81°F", False),
    ("high", 75.2, "74°F", True), ("low", 80.6, "82°F", True),
])
def test_submit_reproof_rejects_old_raw_grid_zero_receipt(native_hard_fact_page, monkeypatch, metric, raw, label, still_dead):
    from dataclasses import replace
    from src.engine.monitor_refresh import _exact_hard_fact_probability_receipt
    from src.execution.day0_hard_fact_exit import HardFactVerdict
    from src.execution import exit_lifecycle
    case = native_hard_fact_page
    _write_hard_fact_page(case, [(_t("2026-10-03T13:15"), raw, True)])
    pos = _hard_fact_position(case, metric, "buy_yes", label)
    # This is the persisted receipt emitted by the previous raw-value owner,
    # not new proof: final submit must refuse it against the current reader.
    prior_evidence = replace(_hard_fact_evidence(case, metric), rounded_extreme=float(_settled(case.city.name, raw)),
                             bound_value=None, bound_raw_value=None, bound_observed_at=None, bound_basis=None)
    prior = HardFactVerdict(action="EXIT_DEAD_BIN", reason="old raw page extreme", metric=metric,
                            rounded_extreme=prior_evidence.rounded_extreme, source=prior_evidence.source,
                            evidence=prior_evidence)
    receipt = _exact_hard_fact_probability_receipt(pos, prior, held_probability=0.0)
    with sqlite3.connect(":memory:", uri=True) as trade:
        trade.row_factory = sqlite3.Row
        trade.execute("ATTACH DATABASE ? AS forecasts", (f"file:{case.forecasts}?mode=ro",))
        trade.execute("ATTACH DATABASE ? AS world", (f"file:{case.world}?mode=ro",))
        trade.execute("""CREATE TABLE position_current (position_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, bin_label TEXT, direction TEXT, condition_id TEXT,
            token_id TEXT, no_token_id TEXT, phase TEXT)""")
        trade.execute("INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?,?)", (
            pos.trade_id, pos.city, pos.target_date, metric, label, pos.direction, "condition",
            "yes", "no", pos.state))
        trade.commit()
        monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: case.now)
        current = _hard_fact_verdict(case, pos)
        assert (current is not None) is still_dead
        assert not exit_lifecycle._protective_source_receipt_current(
            trade, position_id=pos.trade_id, receipt=receipt)
        if still_dead:
            assert current.action == "EXIT_DEAD_BIN"
            assert current.rounded_extreme == prior.rounded_extreme
            fresh = _exact_hard_fact_probability_receipt(pos, current, held_probability=0.0)
            assert fresh["evidence_content_hash"] != receipt["evidence_content_hash"]
            assert exit_lifecycle._protective_source_receipt_current(
                trade, position_id=pos.trade_id, receipt=fresh)
