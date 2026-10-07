# Created: 2026-06-10
# Last reused/audited: 2026-10-07
# Lifecycle: created=2026-06-10; last_reviewed=2026-10-07; last_reused=2026-10-07
# Purpose: Defend causal same-station Day0 observations, source clocks and held q binding.
# Reuse: Inspect raw source/event identity, unit laws and private SQLite fixtures before reuse.
# Authority basis: operator green-light 2026-06-10 items A/C/E (free METAR fast
#   lane, live-obs hook wiring, WU-vs-METAR oracle anomaly guard); day0
#   first-principles review /tmp/day0_first_principles_review.md §6.2;
#   API shape verified live 2026-06-10 against aviationweather.gov
#   /api/data/metar?format=json (KLGA T-group tenths, RKSI whole-C, receiptTime
#   3-6 min behind obsTime);
#   operator patch pr404_live_final_patch.diff 2026-06-10 (fast-lane duplicate
#   memo fix + inconclusive METAR window retry fix).
"""Relationship tests for the day0 fast METAR lane + oracle anomaly guard.

Contracts:
  R5. UNIT LAW: F-settled cities consume only T-group (tenths-C) reports;
      whole-C reports are skipped (understating the running extreme is
      monotone-safe; a 1F conversion error could falsely kill an alive bin).
      C-settled cities consume whole-C verbatim.
  R6. MONOTONE EMISSION: a (city,date,metric) emits only on first sight or
      when the rounded extreme moves in the absorbing direction; emitted
      events pass the reactor hard-fact gate; provenance carries the feed
      receiptTime as observation_available_at (the honest publication clock).
  R7. ORACLE ANOMALY: WU and METAR running extremes are compared over the
      SAME window (METAR truncated at WU's last obs time); divergence beyond
      conversion noise pauses the family's day0 q construction fail-closed;
      latency (METAR extreme moving after WU's last report) is NOT divergence.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.data.day0_fast_obs import (
    Day0FastObsEmitter,
    Day0PublicationLedgerUnavailable,
    FastObsSource,
    FAST_OBS_SOURCE_ID,
    FAST_RESIDUAL_CONDITIONING_SOURCE_ID,
    MetarReport,
    NoaaMetarCycleCursor,
    build_fast_station_residual_likelihood,
    fast_obs_source_for_city,
    fast_obs_to_day0_observation,
    latest_fast_station_conditioning,
    latest_fast_station_extreme_c,
    parse_metar_api_payload,
    parse_noaa_metar_cycle_payload,
    read_noaa_fast_obs_context_from_ledger,
    running_extremes_for_local_day,
    settlement_temp_for_report,
)
from src.data.day0_oracle_anomaly import (
    _reset_registry_for_tests,
    check_wu_metar_divergence,
    clear_day0_oracle_anomaly,
    flag_day0_oracle_anomaly,
    is_day0_family_paused,
)

UTC = timezone.utc


def _shanghai_transition_prints(monkeypatch, *, target_date, include_wrh=True):
    """Controlled WU/WRH/AWC bodies through normal parsers and ledger writers.

    This proves product selection, not historical weather or full-q licensing.
    Query/station/unit are actual configuration; only values and clocks are inputs.
    """
    from src.config import cities_by_name
    from src.data import day0_fast_obs as fast, noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import _wu_history_observations, _append_noaa_wrh_prints
    from src.data.wu_hourly_client import _aggregate_hourly
    from scripts.obs_live_tick import _hourly_observation_prints, _append_hourly_prints_to_ledger
    from src.state.schema.observation_prints_schema import ensure_table

    city = cities_by_name["Shanghai"]
    cutoff = datetime.combine(date.fromisoformat(target_date), datetime.min.time(), UTC) + timedelta(hours=6)
    assert cutoff.astimezone(ZoneInfo(city.timezone)).date().isoformat() == target_date
    fetch = cutoff - timedelta(hours=1)
    observed = [cutoff - timedelta(hours=index) for index in range(24, 4, -1)]
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    body = {"metadata": {"location_id": "ZSPD:9:CN", "units": "m"},
            "observations": [{"obs_id": "ZSPD", "valid_time_gmt": int(at.timestamp()), "temp": 27.0}
                             for at in observed]}
    wu_rows = _wu_history_observations(body, icao=city.wu_station, cc="CN", unit=city.settlement_unit)
    hourly = _aggregate_hourly(wu_rows, icao=city.wu_station, unit=city.settlement_unit,
        timezone_name=city.timezone, city_name=city.name,
        start_date=observed[0].date() - timedelta(days=1), end_date=cutoff.date())
    for row in hourly:
        _append_hourly_prints_to_ledger(conn, _hourly_observation_prints(row,
            source_channel="wu_icao_history", fetched_at_utc=fetch.isoformat()))
    reports = [f"ZSPD {at:%d%H%M}Z 00000KT CAVOK 26/20 Q1010" for at in observed]
    if include_wrh:
        wrh_body = {"STATION": [{"STID": "ZSPD", "OBSERVATIONS": {
            "date_time": [at.astimezone(ZoneInfo(city.timezone)).strftime("%Y-%m-%dT%H:%M:%S%z") for at in observed],
            "air_temp_set_1": [28.0] * 20, "sea_level_pressure_set_1": [1010] * 20,
            "metar_set_1": reports}}]}
        rows = wrh.rows_from_payload(wrh_body, station=city.wu_station)
        for local_day in {date.fromisoformat(row.local_date) for row in rows}:
            _append_noaa_wrh_prints(conn, city_name=city.name, station=city.wu_station,
                unit=city.settlement_unit, rows=rows, target_date_local=local_day,
                view=city.settlement_page_view, fetch_utc=fetch)
    awc = fast.parse_metar_api_payload([{"icaoId": "ZSPD", "obsTime": at.timestamp(),
        "receiptTime": (at + timedelta(minutes=5)).isoformat(), "temp": 26.0,
        "metarType": "METAR", "rawOb": report} for at, report in zip(observed, reports)])

    class WriterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fetch.astimezone(tz) if tz else fetch.replace(tzinfo=None)

    monkeypatch.setattr(fast, "datetime", WriterClock)
    source = fast.fast_obs_source_for_city(city, target_date)
    assert fast._append_metar_prints_to_ledger(conn, ((city, source, target_date),), awc)
    assert conn.execute("SELECT source_channel, COUNT(*) FROM observation_prints GROUP BY source_channel ORDER BY source_channel").fetchall() == (
        [("aviationweather_metar", 20), ("noaa_wrh_zspd", 20), ("wu_icao_history", 20)]
        if include_wrh else [("aviationweather_metar", 20), ("wu_icao_history", 20)])
    assert conn.execute("SELECT COUNT(*) FROM observation_prints WHERE julianday(publish_ts_utc)>=julianday(?) OR julianday(fetched_at_utc)>=julianday(?)", (cutoff.isoformat(), cutoff.isoformat())).fetchone()[0] == 0
    return conn, city, cutoff


@pytest.mark.parametrize("target_date,channel,residual", (
    ("2026-08-23", "wu_icao_history", 1.0),
    ("2026-08-24", "noaa_wrh_zspd", 2.0),
    ("2026-10-01", "noaa_wrh_zspd", 2.0),
))
@pytest.mark.parametrize("metric", ("high", "low"))
def test_fast_residual_selects_the_target_dates_configured_product(monkeypatch, target_date, channel, residual, metric):
    from src.config import settlement_source_type_for_city

    conn, city, cutoff = _shanghai_transition_prints(monkeypatch, target_date=target_date)
    try:
        original = conn.execute("SELECT * FROM observation_prints ORDER BY id").fetchall()
        expected_type = "wu_icao" if channel == "wu_icao_history" else "noaa"
        assert settlement_source_type_for_city(city, target_date) == expected_type
        model = build_fast_station_residual_likelihood(conn, city=city.name,
            target_date=target_date, metric=metric, observed_source=FAST_OBS_SOURCE_ID,
            observation_time=cutoff, decision_time=cutoff)
        assert model is not None and model.matched_pairs == 20
        assert model.settlement_channel == channel
        assert model.residual_weights_c == ((residual, 0.05 ** (1.0 / 20.0)),)
        assert model.settlement_extreme_c == residual + 26.0
        assert conn.execute("SELECT * FROM observation_prints ORDER BY id").fetchall() == original
    finally:
        conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_before_transition_qualified_wu_pairs_do_not_need_the_future_product(monkeypatch, metric):
    conn, city, cutoff = _shanghai_transition_prints(monkeypatch, target_date="2026-08-23", include_wrh=False)
    try:
        model = build_fast_station_residual_likelihood(conn, city=city.name,
            target_date="2026-08-23", metric=metric, observed_source=FAST_OBS_SOURCE_ID,
            observation_time=cutoff, decision_time=cutoff)
        assert model is not None and model.settlement_channel == "wu_icao_history"
        assert model.matched_pairs == 20
        # Same rows cannot claim qualification for the subsequently selected product.
        assert build_fast_station_residual_likelihood(conn, city=city.name,
            target_date="2026-08-24", metric=metric, observed_source=FAST_OBS_SOURCE_ID,
            observation_time=cutoff, decision_time=cutoff) is None
    finally:
        conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_scoped_carrier_replay_rejects_a_self_consistent_other_target_product(monkeypatch, metric):
    """Normal residual identity -> pure carrier -> existing scoped reader seam.

    This is not full forecast/public-q licensing: future inputs are controlled.
    The wrong carrier is fully rebuilt, not merely damaged by a stale hash.
    """
    from src.config import ensemble_n_mc
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data.day0_hourly_vectors import (
        build_day0_remaining_probability_carrier, day0_remaining_carrier_identity_inputs,
        DAY0_REMAINING_CARRIER_OPERATOR_V2,
    )
    from src.data.replacement_forecast_bundle_reader import _wu_fast_pinned_carrier_reason
    from src.events.day0_authority import (
        DAY0_PROBABILITY_MIXTURE_POLICY, DAY0_REMAINING_CENTER_POLICY,
    )
    from src.signal.ensemble_signal import sigma_instrument_for_city
    from src.data import day0_fast_obs as fast

    conn, city, cutoff = _shanghai_transition_prints(monkeypatch, target_date="2026-08-23")
    try:
        def projection(target):
            model = build_fast_station_residual_likelihood(conn, city=city.name,
                target_date=target, metric=metric, observed_source=FAST_OBS_SOURCE_ID,
                observation_time=cutoff, decision_time=cutoff)
            assert model is not None
            likelihood = model.as_payload()
            # Fast+residual and the unknown tail both meet this product bound.
            likelihood["scenario_weights"] = [
                {"observed_bound_c": model.settlement_extreme_c, "weight": 1.0}]
            current = {"value_native": 26.0, "observed_at_utc": cutoff.isoformat(), "source": FAST_OBS_SOURCE_ID}
            inputs = day0_remaining_carrier_identity_inputs(city=city.name, unit=city.settlement_unit,
                decision_time_utc=cutoff.isoformat(), station_id=city.wu_station,
                preliminary_survival_identity=model.identity_hash)
            inputs["current_path_state"] = current
            inputs["day0_remaining_center_policy"] = DAY0_REMAINING_CENTER_POLICY
            inputs["day0_probability_mixture_policy"] = DAY0_PROBABILITY_MIXTURE_POLICY
            bounds = ((None, 27.0), (28.0, None))
            carrier = build_day0_remaining_probability_carrier(future_extremes_c=(26.0, 28.0),
                boundary_scenarios=((None, 1.0),), metric=metric, path_error_sigma_c=0.5,
                instrument_sigma_c=float(sigma_instrument_for_city(city).to("C").value),
                bin_bounds_c=bounds, n_point=ensemble_n_mc(), n_samples=500, identity_inputs=inputs,
                settlement_semantics=SettlementSemantics.for_city(city), operator=DAY0_REMAINING_CARRIER_OPERATOR_V2)
            conditioning = {"active": True, "source": FAST_RESIDUAL_CONDITIONING_SOURCE_ID,
                "metric": metric, "unit": "C", "observation_time": cutoff.isoformat(),
                "observed_extreme_c": 26.0, "sample_count": 20, "fast_residual_likelihood": likelihood}
            assert fast.validated_fast_residual_day0_conditioning(conditioning) is conditioning
            return {"q_shape": "fused_day0_fast_residual_likelihood", "day0_provisional_observation": conditioning,
                "day0_preliminary_report_survival_likelihood": {},
                "day0_remaining_center_policy": DAY0_REMAINING_CENTER_POLICY, "day0_remaining_center_bias_c": 0.0,
                "day0_probability_mixture_policy": DAY0_PROBABILITY_MIXTURE_POLICY,
                "day0_remaining_carrier_probability_cutoff_utc": cutoff.isoformat(), "day0_current_temperature_state": current,
                "bin_topology": [{"lower_c": low, "upper_c": high} for low, high in bounds],
                "day0_remaining_carrier_future_extremes_c": (26.0, 28.0), "day0_remaining_carrier_path_error_sigma_c": 0.5,
                "day0_remaining_carrier_content_identity": carrier["content_identity"],
                "day0_remaining_carrier_q": carrier["q"], "day0_remaining_carrier_probability_samples": carrier["samples"],
                "day0_remaining_carrier_operator": carrier["operator"], "day0_remaining_carrier_sample_count": carrier["sample_count"]}

        expected = dict(city=city.name, target_date="2026-08-23", metric=metric, decision_time=cutoff)
        correct = projection("2026-08-23")
        assert _wu_fast_pinned_carrier_reason(correct, **expected) is None
        wrong = projection("2026-08-24")
        assert wrong["day0_remaining_carrier_content_identity"] != correct["day0_remaining_carrier_content_identity"]
        assert _wu_fast_pinned_carrier_reason(wrong, **expected) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID"
        assert projection("2026-08-23") == correct
        assert _wu_fast_pinned_carrier_reason(correct, **expected) is None
    finally:
        conn.close()


def _native_product_residual_context(monkeypatch, *, city_name, product_c=14.0, fault=None):
    """Controlled original WRH/AWC bodies through their ordinary print writers.

    This is a source-quantity relationship, not a licensed forecast or public q.
    The two products deliberately render one observation differently.
    """
    from src.config import cities_by_name
    from src.data import day0_fast_obs as fast, noaa_wrh_timeseries as wrh
    from src.data.daily_obs_append import _append_noaa_wrh_prints
    from src.state.schema.observation_prints_schema import ensure_table

    city = cities_by_name[city_name]
    assert city.settlement_source_type == "noaa"
    cutoff = datetime(2026, 9, 30, 20, tzinfo=UTC)
    fetch = cutoff + timedelta(minutes=1) if fault == "future_fetch" else cutoff - timedelta(minutes=1)
    station = city.wu_station
    product_station = "KAAA" if fault == "foreign_station" else station
    source = fast.fast_obs_source_for_city(city, cutoff.date())
    assert source is not None and source.station_id == station
    timestamps, native_values, product_reports, awc_rows = [], [], [], []
    pair_count = 19 if fault == "thin" else fast.FAST_RESIDUAL_MIN_PAIRS
    for index in range(pair_count):
        observed = cutoff - timedelta(hours=index + 2)
        if fault == "outside_window":
            observed -= timedelta(days=7)
        product_observed = observed + timedelta(minutes=5) if fault == "foreign_observation" else observed
        raw = f"{station} {observed:%d%H%M}Z 00000KT 10SM CLR 14/10 A3000 RMK AO2 T01440100"
        product_raw = f"{product_station} {product_observed:%d%H%M}Z 00000KT 10SM CLR 14/10 A3000 RMK AO2 T01440100"
        timestamps.append(product_observed.astimezone(ZoneInfo(city.timezone)).strftime("%Y-%m-%dT%H:%M:%S%z"))
        native = product_c if city.settlement_unit == "C" else product_c * 9.0 / 5.0 + 32.0
        native_values.append(None if fault == "missing_native" else float("nan") if fault == "nonfinite_native" else native)
        product_reports.append(product_raw)
        awc_rows.append({"icaoId": station, "obsTime": observed.timestamp(),
                         "receiptTime": (observed + timedelta(seconds=30)).isoformat(),
                         "temp": 14.4, "metarType": "METAR", "rawOb": raw})
    product_body = json.dumps({"STATION": [{"STID": product_station, "OBSERVATIONS": {
        "date_time": timestamps, "air_temp_set_1": native_values,
        "sea_level_pressure_set_1": [1013.0] * pair_count, "metar_set_1": product_reports,
    }}]})
    if fault == "nonfinite_native":
        # A non-finite native value invalidates the whole response; no row enters.
        with pytest.raises(wrh.WrhPayloadInvalid):
            wrh.rows_from_payload(json.loads(product_body), product_station)
        rows = []
    else:
        rows = wrh.rows_from_payload(json.loads(product_body), product_station)
    reports = fast.parse_metar_api_payload(json.loads(json.dumps(awc_rows)))
    assert len(reports) == pair_count
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    for target in {date.fromisoformat(row.local_date) for row in rows}:
        _append_noaa_wrh_prints(conn, city_name=city.name, station=product_station,
            unit=city.settlement_unit, rows=rows, target_date_local=target,
            view=city.settlement_page_view, fetch_utc=fetch)

    class ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    writer_time = [fetch]

    class WriterClock(datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None):
            return writer_time[0].astimezone(tz) if tz else writer_time[0].replace(tzinfo=None)

    monkeypatch.setattr(fast, "datetime", WriterClock)
    assert fast._append_metar_prints_to_ledger(conn, ((city, source, cutoff.date()),), reports)
    current_observed = cutoff - timedelta(minutes=1)
    current_report = fast.parse_metar_api_payload([{
        "icaoId": station, "obsTime": current_observed.timestamp(),
        "receiptTime": cutoff.isoformat(), "temp": 14.4, "metarType": "METAR",
        "rawOb": f"{station} {current_observed:%d%H%M}Z 14/10 T01440100",
    }])
    writer_time[0] = cutoff + timedelta(seconds=5)
    assert fast._append_metar_prints_to_ledger(conn, ((city, source, cutoff.date()),), current_report)
    conn.commit()
    return conn, city, cutoff


@pytest.mark.parametrize("city_name", ("Chicago", "Paris"))
@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("product_c", (14.0, 14.8))
def test_fast_residual_uses_native_product_not_raw_t_group(monkeypatch, city_name, metric, product_c):
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data import day0_fast_obs as fast

    conn, city, cutoff = _native_product_residual_context(monkeypatch, city_name=city_name, product_c=product_c)
    try:
        original_rows = conn.execute("SELECT * FROM observation_prints ORDER BY id").fetchall()
        assert len(original_rows) == 2 * fast.FAST_RESIDUAL_MIN_PAIRS + 1
        target = cutoff.astimezone(ZoneInfo(city.timezone)).date().isoformat()
        current = fast.latest_fast_station_extreme_c(conn, city=city.name, target_date=target,
            metric=metric, decision_time=cutoff + timedelta(minutes=5))
        # Chicago's local day starts at 05Z; six training prints are yesterday.
        expected_day_count = 15 if city_name == "Chicago" else 21
        assert current == (14.4, cutoff.isoformat(), expected_day_count, city.settlement_unit)
        likelihood = fast.build_fast_station_residual_likelihood(conn, city=city.name,
            target_date=target, metric=metric, observed_source=fast.FAST_OBS_SOURCE_ID,
            observation_time=current[1], decision_time=cutoff + timedelta(minutes=5))
        assert likelihood is not None
        expected_unknown = 1.0 - 0.05 ** (1.0 / 20.0)
        assert likelihood.matched_pairs == 20
        assert likelihood.window_start == (cutoff - timedelta(days=7)).isoformat()
        assert likelihood.residual_weights_c == ((round(product_c - 14.4, 6), 1.0 - expected_unknown),)
        assert likelihood.unknown_weight == expected_unknown
        assert likelihood.settlement_extreme_c == pytest.approx(product_c)
        assert likelihood.settlement_channel == f"noaa_wrh_{city.wu_station.lower()}"
        assert conn.execute("SELECT * FROM observation_prints ORDER BY id").fetchall() == original_rows
        if city.settlement_unit == "F" and product_c == 14.0:
            semantics = SettlementSemantics.for_city(city)
            assert semantics.round_single(57.2) == 57
            assert semantics.round_single(14.4 * 9 / 5 + 32) == 58
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Chicago", "Paris"))
@pytest.mark.parametrize("fault", ("future_fetch", "foreign_station", "foreign_observation", "outside_window", "thin", "missing_native", "nonfinite_native"))
def test_native_product_residual_keeps_causal_pair_and_thin_boundaries(monkeypatch, city_name, fault):
    conn, city, cutoff = _native_product_residual_context(monkeypatch, city_name=city_name, fault=fault)
    try:
        for metric in ("high", "low"):
            assert build_fast_station_residual_likelihood(conn, city=city.name,
                target_date=cutoff.astimezone(ZoneInfo(city.timezone)).date().isoformat(), metric=metric,
                observed_source=FAST_OBS_SOURCE_ID, observation_time=cutoff,
                decision_time=cutoff + timedelta(minutes=5)) is None
    finally:
        conn.close()


@pytest.mark.parametrize("value,unit", ((None, "F"), (float("nan"), "C"), (float("inf"), "F"), (57.2, "K")))
@pytest.mark.parametrize("settlement_unit", ("C", "F"))
def test_product_residual_missing_native_never_uses_valid_raw_t(value, unit, settlement_unit):
    from src.data.day0_fast_obs import _fast_residual_value_c

    assert _fast_residual_value_c(channel="noaa_wrh_kord", value_native=value,
        unit=unit, raw_report="KORD 301315Z 14/10 T01440100",
        settlement_unit=settlement_unit) is None


@pytest.mark.parametrize("settlement_unit,native,raw,expected", (
    ("C", 14.0, "KORD 301315Z 14/10 T01440100", 14.0),
    ("F", 14.0, "KORD 301315Z 14/10 T01440100", 14.4),
    ("C", 14.0, "KORD 301315Z 14/10", 14.0),
    ("F", 14.0, "KORD 301315Z 14/10", None),
))
def test_fast_physical_quantity_keeps_its_original_native_unit_law(settlement_unit, native, raw, expected):
    from src.data.day0_fast_obs import _fast_residual_value_c

    assert _fast_residual_value_c(channel=FAST_OBS_SOURCE_ID, value_native=native,
        unit="C", raw_report=raw, settlement_unit=settlement_unit) == expected


@pytest.mark.parametrize("channel", ("wu_icao_history", "noaa_wrh_kord"))
@pytest.mark.parametrize("unit,native", (("C", 14.0), ("F", 57.2)))
def test_cfg_selected_product_quantity_stays_native_even_with_same_report(channel, unit, native):
    from src.data.day0_fast_obs import _fast_residual_value_c

    assert _fast_residual_value_c(channel=channel, value_native=native, unit=unit,
        raw_report="KORD 301315Z 14/10 T01440100", settlement_unit=unit) == pytest.approx(14.0)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_native_product_likelihood_revision_rebuilds_identity_without_relabeling_old(monkeypatch, metric):
    import copy
    import hashlib
    from src.data import day0_fast_obs as fast

    conn, city, cutoff = _native_product_residual_context(monkeypatch, city_name="Chicago")
    try:
        likelihood = fast.build_fast_station_residual_likelihood(conn, city=city.name,
            target_date=cutoff.astimezone(ZoneInfo(city.timezone)).date().isoformat(), metric=metric,
            observed_source=FAST_OBS_SOURCE_ID, observation_time=cutoff,
            decision_time=cutoff + timedelta(minutes=5))
        assert likelihood is not None
        assert likelihood.semantics_revision == "same_station_causal_product_minus_fast_residual_v2"
        payload = likelihood.as_payload()
        payload["scenario_weights"] = [{"observed_bound_c": 14.0, "weight": 1.0}]
        conditioning = {"active": True, "source": FAST_OBS_SOURCE_ID, "metric": metric,
            "unit": "F", "observation_time": cutoff.isoformat(), "observed_extreme_c": 14.4,
            "sample_count": 15, "fast_residual_likelihood": payload}
        assert fast.validated_fast_residual_day0_conditioning(conditioning) is conditioning
        old = copy.deepcopy(conditioning)
        old_payload = old["fast_residual_likelihood"]
        old_payload["semantics_revision"] = "same_station_causal_residual_v1"
        old_identity = {key: old_payload[key] for key in (
            "semantics_revision", "station_id", "settlement_channel", "fast_channel", "unit",
            "as_of", "window_start", "matched_pairs", "unknown_weight", "settlement_extreme_c")}
        old_identity["residual_weights_c"] = tuple(
            (row["residual_c"], row["weight"]) for row in old_payload["residual_weights_c"])
        old_payload["identity_hash"] = hashlib.sha256(json.dumps(
            old_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        immutable_old = copy.deepcopy(old)
        with pytest.raises(ValueError, match="GLOBAL_DAY0_FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID"):
            fast.validated_fast_residual_day0_conditioning(old)
        assert old == immutable_old
        assert old_payload["identity_hash"] != likelihood.identity_hash
        rebuilt = fast.build_fast_station_residual_likelihood(conn, city=city.name,
            target_date=cutoff.astimezone(ZoneInfo(city.timezone)).date().isoformat(), metric=metric,
            observed_source=FAST_OBS_SOURCE_ID, observation_time=cutoff,
            decision_time=cutoff + timedelta(minutes=5))
        assert rebuilt == likelihood
    finally:
        conn.close()


def test_fast_station_residual_likelihood_is_causal_station_local_and_thin_inert(
    monkeypatch,
) -> None:
    from src import config as config_module

    monkeypatch.setitem(
        config_module.cities_by_name,
        "Residual City",
        SimpleNamespace(
            settlement_source_type="wu_icao",
            wu_station="TEST",
            settlement_unit="C",
            timezone="UTC",
        ),
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_prints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            station_id TEXT NOT NULL,
            source_channel TEXT NOT NULL,
            publish_ts_utc TEXT NOT NULL,
            value_native REAL NOT NULL,
            unit TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            raw_report TEXT
        )
        """
    )
    start = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    for index in range(20):
        published = start + timedelta(minutes=30 * index)
        value = 10.0 + float(index % 5)
        fetched = published + timedelta(minutes=1)
        conn.execute(
            "INSERT INTO observation_prints "
            "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "Residual City",
                "TEST",
                "wu_icao_history",
                published.isoformat(),
                value,
                "C",
                fetched.isoformat(),
                "",
            ),
        )
        conn.execute(
            "INSERT INTO observation_prints "
            "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "Residual City",
                "TEST",
                FAST_OBS_SOURCE_ID,
                published.isoformat(),
                value,
                "C",
                fetched.isoformat(),
                f"TEST {published:%d%H%M}Z 10/05",
            ),
        )
    current_time = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    decision_time = current_time + timedelta(minutes=5)
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual City",
            "TEST",
            FAST_OBS_SOURCE_ID,
            current_time.isoformat(),
            31.0,
            "C",
            (current_time + timedelta(minutes=1)).isoformat(),
            "TEST 271000Z 31/05",
        ),
    )
    # A correction fetched after the decision must not leak back into training.
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual City",
            "TEST",
            "wu_icao_history",
            (start + timedelta(minutes=30 * 19)).isoformat(),
            99.0,
            "C",
            (decision_time + timedelta(minutes=1)).isoformat(),
            "",
        ),
    )
    post_peak_time = current_time + timedelta(minutes=3)
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual City",
            "TEST",
            FAST_OBS_SOURCE_ID,
            post_peak_time.isoformat(),
            30.0,
            "C",
            (post_peak_time + timedelta(seconds=30)).isoformat(),
            "TEST 271003Z 30/05",
        ),
    )
    # A WU value published before the fast print but fetched only afterwards
    # was not available when that fast print became the conditioning fact.
    late_pair_time = current_time - timedelta(minutes=15)
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual City",
            "TEST",
            FAST_OBS_SOURCE_ID,
            late_pair_time.isoformat(),
            20.0,
            "C",
            (late_pair_time + timedelta(minutes=1)).isoformat(),
            "TEST 270945Z 20/05",
        ),
    )
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual City",
            "TEST",
            "wu_icao_history",
            late_pair_time.isoformat(),
            99.0,
            "C",
            (current_time + timedelta(minutes=4)).isoformat(),
            "",
        ),
    )

    fast = latest_fast_station_extreme_c(
        conn,
        city="Residual City",
        target_date="2026-07-27",
        metric="high",
        decision_time=decision_time,
    )
    assert fast == (31.0, post_peak_time.isoformat(), 23, "C")
    likelihood = build_fast_station_residual_likelihood(
        conn,
        city="Residual City",
        target_date="2026-07-27",
        metric="high",
        observed_source=FAST_OBS_SOURCE_ID,
        observation_time=post_peak_time,
        decision_time=decision_time,
    )
    assert likelihood is not None
    assert likelihood.matched_pairs == 20
    expected_unknown = 1.0 - 0.05 ** (1.0 / 20.0)
    assert likelihood.residual_weights_c == ((0.0, 1.0 - expected_unknown),)
    assert likelihood.unknown_weight == expected_unknown
    assert likelihood.settlement_extreme_c == 14.0
    composite_likelihood = build_fast_station_residual_likelihood(
        conn,
        city="Residual City",
        target_date="2026-07-27",
        metric="high",
        observed_source=FAST_RESIDUAL_CONDITIONING_SOURCE_ID,
        observation_time=post_peak_time,
        decision_time=decision_time,
    )
    assert composite_likelihood is not None
    assert composite_likelihood.identity_hash == likelihood.identity_hash
    conditioning = latest_fast_station_conditioning(
        conn,
        city="Residual City",
        target_date="2026-07-27",
        metric="high",
        decision_time=decision_time,
        settlement_extreme_native=14.0,
        settlement_unit="C",
    )
    assert conditioning is not None
    assert conditioning.observed_extreme_c == 31.0
    assert conditioning.observation_time == post_peak_time.isoformat()
    assert conditioning.likelihood.identity_hash == likelihood.identity_hash
    assert (
        latest_fast_station_conditioning(
            conn,
            city="Residual City",
            target_date="2026-07-27",
            metric="high",
            decision_time=decision_time,
            settlement_extreme_native=32.0,
            settlement_unit="C",
        )
        is None
    )
    assert (
        build_fast_station_residual_likelihood(
            conn,
            city="Residual City",
            target_date="2026-07-27",
            metric="high",
            observed_source=f"prefix-{FAST_OBS_SOURCE_ID}",
            observation_time=post_peak_time,
            decision_time=decision_time,
        )
        is None
    )

    conn.execute(
        "DELETE FROM observation_prints WHERE publish_ts_utc = ?",
        (start.isoformat(),),
    )
    assert (
        build_fast_station_residual_likelihood(
            conn,
            city="Residual City",
            target_date="2026-07-27",
            metric="high",
            observed_source=FAST_OBS_SOURCE_ID,
            observation_time=post_peak_time,
            decision_time=decision_time,
        )
        is None
    )
    assert (
        latest_fast_station_conditioning(
            conn,
            city="Residual City",
            target_date="2026-07-27",
            metric="high",
            decision_time=decision_time,
            settlement_extreme_native=14.0,
            settlement_unit="C",
        )
        is None
    )


def test_fast_station_residual_likelihood_bounds_city_history_before_filtering(
    monkeypatch,
) -> None:
    """The seven-day residual read must not scan a city's old print ledger."""
    from src import config as config_module

    city = "Bounded Residual City"
    station = "TEST"
    monkeypatch.setitem(
        config_module.cities_by_name,
        city,
        SimpleNamespace(
            settlement_source_type="wu_icao",
            wu_station=station,
            settlement_unit="C",
            timezone="UTC",
        ),
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_prints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            station_id TEXT NOT NULL,
            source_channel TEXT NOT NULL,
            publish_ts_utc TEXT NOT NULL,
            value_native REAL NOT NULL,
            unit TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            raw_report TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_observation_prints_city_publish "
        "ON observation_prints(city, publish_ts_utc)"
    )
    cutoff = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    recent_rows: list[tuple[object, ...]] = []
    for index in range(20):
        published = cutoff - timedelta(minutes=30 * (20 - index))
        for channel in ("wu_icao_history", FAST_OBS_SOURCE_ID):
            recent_rows.append(
                (
                    city,
                    station.lower(),
                    channel,
                    published.isoformat(),
                    20.0 + float(index % 3),
                    "C",
                    (published + timedelta(minutes=1)).isoformat(),
                    "",
                )
            )
    # Valid ISO offsets can place exact in-window instants on the adjacent
    # lexical date.  These are the regression edges the index guard must not
    # discard before julianday applies the causal window.
    lower_boundary = (cutoff - timedelta(days=7) + timedelta(minutes=30)).astimezone(
        timezone(timedelta(hours=-13))
    )
    upper_boundary = (cutoff - timedelta(minutes=30)).astimezone(
        timezone(timedelta(hours=13))
    )
    assert lower_boundary.date() < (cutoff - timedelta(days=7)).date()
    assert upper_boundary.date() > cutoff.date()
    for published in (lower_boundary, upper_boundary):
        for channel in ("wu_icao_history", FAST_OBS_SOURCE_ID):
            recent_rows.append(
                (
                    city,
                    station.lower(),
                    channel,
                    published.isoformat(),
                    23.0,
                    "C",
                    (published + timedelta(minutes=1)).isoformat(),
                    "",
                )
            )
    conn.executemany(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        recent_rows,
    )
    old_start = datetime(2025, 1, 1, tzinfo=UTC)
    conn.executemany(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                city,
                "OTHER",
                "unrelated_channel",
                (old_start + timedelta(minutes=index)).isoformat(),
                5.0,
                "C",
                (old_start + timedelta(minutes=index + 1)).isoformat(),
                "",
            )
            for index in range(12_000)
        ],
    )
    traced: list[str] = []
    progress_calls = 0

    def progress() -> int:
        nonlocal progress_calls
        progress_calls += 1
        return 0

    conn.set_trace_callback(traced.append)
    conn.set_progress_handler(progress, 1_000)
    try:
        likelihood = build_fast_station_residual_likelihood(
            conn,
            city=city,
            target_date="2026-07-27",
            metric="high",
            observed_source=FAST_OBS_SOURCE_ID,
            observation_time=cutoff,
            decision_time=cutoff,
        )
    finally:
        conn.set_trace_callback(None)
        conn.set_progress_handler(None, 0)

    assert likelihood is not None
    assert likelihood.matched_pairs == 22
    residual_query = next(
        statement
        for statement in traced
        if "FROM observation_prints" in statement
        and "source_channel IN" in statement
    )
    plan = conn.execute(f"EXPLAIN QUERY PLAN {residual_query}").fetchall()
    assert any(
        "idx_observation_prints_city_publish" in str(row[-1])
        and "publish_ts_utc>? AND publish_ts_utc<?" in str(row[-1])
        for row in plan
    )
    window_start = cutoff - timedelta(days=7)
    exact_rows = conn.execute(
        """
        SELECT source_channel, publish_ts_utc, value_native, fetched_at_utc
          FROM observation_prints
         WHERE city = ?
           AND upper(station_id) = ?
           AND source_channel IN ('wu_icao_history', ?)
           AND julianday(publish_ts_utc) >= julianday(?)
           AND julianday(publish_ts_utc) < julianday(?)
           AND julianday(fetched_at_utc) < julianday(?)
         ORDER BY publish_ts_utc, id
        """,
        (
            city,
            station,
            FAST_OBS_SOURCE_ID,
            window_start.isoformat(),
            cutoff.isoformat(),
            cutoff.isoformat(),
        ),
    ).fetchall()
    bounded_exact_rows = conn.execute(
        """
        SELECT source_channel, publish_ts_utc, value_native, fetched_at_utc
          FROM observation_prints
         WHERE city = ?
           AND publish_ts_utc >= ?
           AND publish_ts_utc < ?
           AND upper(station_id) = ?
           AND source_channel IN ('wu_icao_history', ?)
           AND julianday(publish_ts_utc) >= julianday(?)
           AND julianday(publish_ts_utc) < julianday(?)
           AND julianday(fetched_at_utc) < julianday(?)
         ORDER BY publish_ts_utc, id
        """,
        (
            city,
            (window_start - timedelta(days=1)).date().isoformat(),
            (cutoff + timedelta(days=2)).date().isoformat(),
            station,
            FAST_OBS_SOURCE_ID,
            window_start.isoformat(),
            cutoff.isoformat(),
            cutoff.isoformat(),
        ),
    ).fetchall()
    assert bounded_exact_rows == exact_rows
    # 12k same-city, out-of-window rows must stay outside the VM scan.  A
    # city-only read regresses into a ledger scan and crosses this bound.
    assert progress_calls < 40


def test_fast_station_extreme_invalid_timezone_fails_soft(monkeypatch) -> None:
    from src import config as config_module

    monkeypatch.setitem(
        config_module.cities_by_name,
        "Invalid Timezone City",
        SimpleNamespace(
            settlement_source_type="wu_icao",
            wu_station="TEST",
            settlement_unit="C",
            timezone="Invalid/Timezone",
        ),
    )

    assert (
        latest_fast_station_extreme_c(
            sqlite3.connect(":memory:"),
            city="Invalid Timezone City",
            target_date="2026-07-27",
            metric="high",
            decision_time=datetime(2026, 7, 27, tzinfo=UTC),
        )
        is None
    )


def _kfst_raw(published, *, t_group: bool) -> str:
    """A KFST METAR whose valid-time group is this print's own instant.

    One observation republished at twenty different instants is a shape no live
    feed produces, and it collapses every pair onto a single observation key.
    """

    raw = f"KFST {published.astimezone(UTC):%d%H%M}Z 20/10"
    return f"{raw} T02000100" if t_group else raw


def test_fast_residual_low_fahrenheit_requires_t_group(
    monkeypatch,
) -> None:
    from src import config as config_module

    monkeypatch.setitem(
        config_module.cities_by_name,
        "Residual F City",
        SimpleNamespace(
            settlement_source_type="wu_icao",
            wu_station="KFST",
            settlement_unit="F",
            timezone="UTC",
        ),
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_prints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            station_id TEXT NOT NULL,
            source_channel TEXT NOT NULL,
            publish_ts_utc TEXT NOT NULL,
            value_native REAL NOT NULL,
            unit TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            raw_report TEXT
        )
        """
    )
    start = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    for index in range(20):
        published = start + timedelta(minutes=20 * index)
        fetched = published + timedelta(minutes=1)
        conn.execute(
            "INSERT INTO observation_prints "
            "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "Residual F City",
                "KFST",
                "wu_icao_history",
                published.isoformat(),
                68.0,
                "F",
                fetched.isoformat(),
                # The mirror republishes the very report the fast feed carries,
                # so both renderings key to one observation.
                _kfst_raw(published, t_group=index != 0),
            ),
        )
        conn.execute(
            "INSERT INTO observation_prints "
            "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "Residual F City",
                "KFST",
                FAST_OBS_SOURCE_ID,
                published.isoformat(),
                20.0,
                "C",
                fetched.isoformat(),
                _kfst_raw(published, t_group=index != 0),
            ),
        )
    observation_time = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
    decision_time = observation_time + timedelta(minutes=5)
    assert (
        build_fast_station_residual_likelihood(
            conn,
            city="Residual F City",
            target_date="2026-07-27",
            metric="low",
            observed_source=FAST_OBS_SOURCE_ID,
            observation_time=observation_time,
            decision_time=decision_time,
        )
        is None
    )

    conn.execute(
        "UPDATE observation_prints SET raw_report = ? "
        "WHERE source_channel = ? AND publish_ts_utc = ?",
        (
            _kfst_raw(start, t_group=True),
            FAST_OBS_SOURCE_ID,
            start.isoformat(),
        ),
    )
    late_pair_time = observation_time - timedelta(minutes=15)
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual F City",
            "KFST",
            FAST_OBS_SOURCE_ID,
            late_pair_time.isoformat(),
            15.0,
            "C",
            (late_pair_time + timedelta(minutes=1)).isoformat(),
            "KFST 270745Z 15/05 T01500050",
        ),
    )
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "Residual F City",
            "KFST",
            "wu_icao_history",
            late_pair_time.isoformat(),
            59.0,
            "F",
            (observation_time + timedelta(minutes=4)).isoformat(),
            "",
        ),
    )
    for published, value_c, raw_report in (
        (observation_time, 10.0, "KFST 270800Z 10/05 T01000050"),
        (
            observation_time + timedelta(minutes=3),
            12.0,
            "KFST 270803Z 12/05 T01200050",
        ),
    ):
        conn.execute(
            "INSERT INTO observation_prints "
            "(city,station_id,source_channel,publish_ts_utc,value_native,unit,fetched_at_utc,raw_report) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "Residual F City",
                "KFST",
                FAST_OBS_SOURCE_ID,
                published.isoformat(),
                value_c,
                "C",
                (published + timedelta(seconds=30)).isoformat(),
                raw_report,
            ),
        )
    post_trough_time = observation_time + timedelta(minutes=3)
    assert latest_fast_station_extreme_c(
        conn,
        city="Residual F City",
        target_date="2026-07-27",
        metric="low",
        decision_time=decision_time,
    ) == (10.0, post_trough_time.isoformat(), 23, "F")
    likelihood = build_fast_station_residual_likelihood(
        conn,
        city="Residual F City",
        target_date="2026-07-27",
        metric="low",
        observed_source=FAST_OBS_SOURCE_ID,
        observation_time=post_trough_time,
        decision_time=decision_time,
    )
    assert likelihood is not None
    assert likelihood.unit == "F"
    assert likelihood.matched_pairs == 20
    assert likelihood.residual_weights_c[0][0] == pytest.approx(0.0)
    assert likelihood.settlement_extreme_c == pytest.approx(20.0)
    conditioning = latest_fast_station_conditioning(
        conn,
        city="Residual F City",
        target_date="2026-07-27",
        metric="low",
        decision_time=decision_time,
        settlement_extreme_native=68.0,
        settlement_unit="F",
    )
    assert conditioning is not None
    assert conditioning.observed_extreme_c == 10.0
    assert conditioning.observation_time == post_trough_time.isoformat()
    assert (
        latest_fast_station_conditioning(
            conn,
            city="Residual F City",
            target_date="2026-07-27",
            metric="low",
            decision_time=decision_time,
            settlement_extreme_native=40.0,
            settlement_unit="F",
        )
        is None
    )


@pytest.fixture(autouse=True)
def _clean_anomaly_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _nyc():
    return SimpleNamespace(
        name="NYC", timezone="America/New_York", settlement_unit="F",
        wu_station="KLGA", settlement_source_type="wu_icao",
    )


def _seoul():
    return SimpleNamespace(
        name="Seoul", timezone="Asia/Seoul", settlement_unit="C",
        wu_station="RKSI", settlement_source_type="wu_icao",
    )


def _tokyo():
    # Tokyo: settlement-FAITHFUL C city (measured, margin 0). Seoul is
    # margin-absorbed rather than excluded as of 2026-07-16 (day0 defect-5,
    # see TestMetarMarginAbsorption) but most emitter tests still use Tokyo
    # for a margin-free baseline. JST is UTC+9 like KST — the same UTC
    # fixtures map to the same local day.
    return SimpleNamespace(
        name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
        wu_station="RJTT", settlement_source_type="wu_icao",
        lat=35.549678, lon=139.786958,
    )


def _london():
    return SimpleNamespace(
        name="London", timezone="Europe/London", settlement_unit="C",
        wu_station="EGLC", settlement_source_type="wu_icao",
    )


def _scheduler_hourly_vector(city, model, decision_time, *, omit_local_time=None):
    from src.data.day0_hourly_vectors import Day0HourlyVector, DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL

    local_day = decision_time.astimezone(ZoneInfo(city.timezone)).date()
    times = [
        f"{(local_day + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in (0, 1, 2)
        for hour in range(24)
    ]
    if omit_local_time in times:
        times.remove(omit_local_time)
    return Day0HourlyVector(
        model=model,
        city=city.name,
        target_date=local_day.isoformat(),
        timezone_name=city.timezone,
        captured_at=decision_time.isoformat(),
        times=tuple(times),
        temps_c=tuple(18.0 + index * 0.1 for index in range(len(times))),
        source_run_meta_json=json.dumps({
            "fetch_started_at": decision_time.isoformat(),
            "fetch_finished_at": decision_time.isoformat(),
            "request_params_json": json.dumps({"metadata_model": DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL}),
            "provider_source_cycle_time_utc": (decision_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)).isoformat(),
            "provider_source_available_at_utc": (decision_time - timedelta(minutes=1)).isoformat(),
            "request_hash": "scheduler-ensemble" if model.startswith("ecmwf_ifs025_member") else "scheduler-" + model,
            "provider_run_id": "scheduler-ensemble" if model.startswith("ecmwf_ifs025_member") else "scheduler-" + model,
        }),
    )


def _install_scheduler_forecast_db(monkeypatch, tmp_path, city, *, authorized_fact):
    import src.config as config_module
    import src.data.day0_hourly_vectors as vectors_module
    import src.state.db as db_module

    db_path = tmp_path / "scheduler-forecasts.db"
    now = datetime.now(UTC)
    observation_time = now - timedelta(minutes=5)
    target_date = now.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, station_id TEXT,
            local_timestamp TEXT, utc_timestamp TEXT, imported_at TEXT,
            temp_unit TEXT, running_max REAL, running_min REAL,
            authority TEXT, training_allowed INTEGER, causality_status TEXT,
            source_role TEXT, raw_response TEXT
        )
        """
    )
    if authorized_fact:
        conn.execute(
            """
            INSERT INTO observation_instants (
                city, target_date, source, station_id, local_timestamp,
                utc_timestamp, imported_at, temp_unit, running_max, running_min,
                authority, training_allowed, causality_status, source_role,
                raw_response
            ) VALUES (?, ?, 'wu_icao_history', ?, ?, ?, ?, ?, 25.0, 12.0,
                      'VERIFIED', 1, 'OK', 'historical_hourly', '{}')
            """,
            (
                city.name,
                target_date,
                city.wu_station,
                observation_time.astimezone(ZoneInfo(city.timezone)).isoformat(),
                observation_time.isoformat(),
                observation_time.isoformat(),
                city.settlement_unit,
            ),
        )
    conn.commit()
    conn.close()

    def connect(*_args, **_kwargs):
        opened = sqlite3.connect(db_path)
        opened.row_factory = sqlite3.Row
        return opened

    monkeypatch.setattr(config_module, "runtime_cities", lambda: [city])
    monkeypatch.setattr(
        config_module,
        "runtime_cities_by_name",
        lambda: {city.name: city},
    )
    monkeypatch.setattr(db_module, "ZEUS_FORECASTS_DB_PATH", db_path)
    monkeypatch.setattr(db_module, "get_forecasts_connection", connect)
    monkeypatch.setattr(db_module, "get_forecasts_connection_read_only", connect)
    monkeypatch.setattr(db_module, "get_world_connection_read_only", connect)
    monkeypatch.setattr(vectors_module, "in_domain_models_for_city", lambda _city, **_kw: [])
    def ensemble_fetch(city, *, now, **_kwargs):
        return ([_scheduler_hourly_vector(city, model, now)
                 for model in vectors_module.day0_source_clock_ensemble_member_models()],
                "sha256:scheduler-ensemble")
    monkeypatch.setattr(vectors_module, "fetch_day0_source_clock_ensemble_vectors", ensemble_fetch)
    monkeypatch.setattr(vectors_module, "_probe_day0_source_clock_ensemble_run_hwm", lambda **_: None)
    vectors_module._LAST_REFRESH_MONOTONIC.clear()
    vectors_module._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    vectors_module._INCOMPLETE_RETRY_STREAK.clear()
    return db_path, target_date


def _istanbul():
    return SimpleNamespace(
        name="Istanbul", timezone="Europe/Istanbul", settlement_unit="C",
        wu_station="LTFM", settlement_source_type="noaa",
    )


def _report(station, obs_time, temp_c, *, t_group=True, receipt_offset_min=4.0):
    raw = (
        f"METAR {station} {obs_time.astimezone(UTC):%d%H%M}Z "
        "16008KT 10SM 21/15 A3004"
    )
    if t_group:
        raw += " RMK AO2 T02110150"
    return MetarReport(
        station_id=station,
        obs_time=obs_time,
        receipt_time=obs_time + timedelta(minutes=receipt_offset_min),
        temp_c=temp_c,
        metar_type="METAR",
        raw=raw,
    )


# ===========================================================================
# Parsing (real API shape, verified live 2026-06-10)
# ===========================================================================

class TestParsePayload:
    SAMPLE = [
        {
            "icaoId": "KLGA", "receiptTime": "2026-06-10T00:54:16.580Z",
            "obsTime": 1781052660, "reportTime": "2026-06-10T01:00:00.000Z",
            "temp": 21.1, "metarType": "METAR",
            "rawOb": "METAR KLGA 100051Z 16008KT 10SM FEW250 21/15 A3004 RMK AO2 SLP170 T02110150",
        },
        {
            "icaoId": "RKSI", "receiptTime": "2026-06-10T01:04:35.841Z",
            "obsTime": 1781053200, "reportTime": "2026-06-10T01:00:00.000Z",
            "temp": 21, "metarType": "METAR",
            "rawOb": "METAR RKSI 100100Z 23004KT 160V310 8000 BKN015 21/17 Q1009 NOSIG",
        },
        {"icaoId": "", "obsTime": 1781053200},      # malformed: no station
        {"icaoId": "KXXX"},                            # malformed: no obsTime
        "not-a-dict",
    ]

    def test_parses_valid_rows_and_skips_junk(self):
        reports = parse_metar_api_payload(self.SAMPLE)
        assert [r.station_id for r in reports] == ["KLGA", "RKSI"]
        klga = reports[0]
        assert klga.temp_c == pytest.approx(21.1)
        assert klga.has_t_group is True
        assert klga.obs_time == datetime.fromtimestamp(1781052660, tz=UTC)
        # receiptTime is the publication clock (provenance for available_at)
        assert klga.receipt_time is not None and klga.receipt_time > klga.obs_time
        rksi = reports[1]
        assert rksi.has_t_group is False

    def test_non_list_payload_returns_empty(self):
        assert parse_metar_api_payload({"error": "nope"}) == []
        assert parse_metar_api_payload(None) == []


class TestNoaaMetarCycleFeed:
    PUBLISHED = datetime(2026, 7, 18, 4, 16, tzinfo=UTC)

    def test_parser_filters_stations_dedups_and_preserves_tenths(self):
        payload = """2026/07/18 04:15
KORD 180415Z 24006KT 10SM CLR 28/22 A2993 RMK AO2 T02830222

2026/07/18 04:15
KORD 180415Z 24006KT 10SM CLR 28/22 A2993 RMK AO2 T02830222

2026/07/18 04:10
LFPB 180410Z AUTO 35004KT CAVOK 17/13 Q1017 NOSIG
"""

        reports = parse_noaa_metar_cycle_payload(
            payload,
            stations=("KORD",),
            published_at=self.PUBLISHED,
        )

        assert len(reports) == 1
        assert reports[0].station_id == "KORD"
        assert reports[0].temp_c == pytest.approx(28.3)
        assert reports[0].receipt_time == self.PUBLISHED

    def test_cursor_reads_only_appended_bytes_after_cold_start(self):
        initial = b"""2026/07/18 04:00
KORD 180400Z 24006KT 10SM CLR 27/22 A2993 RMK AO2 T02720222

2026/07/18 04:15
KORD 180415Z 24006KT 10SM CLR 28/22 A2993 RMK AO2 T02830222
"""
        delta = b"""
2026/07/18 04:20
KORD 180420Z 24006KT 10SM CLR 29/22 A2993 RMK AO2 T02940222
"""

        class _Response:
            def __init__(self, status_code, content=b"", headers=None):
                self.status_code = status_code
                self.content = content
                self.headers = headers or {}

        class _Client:
            def __init__(self):
                self.calls = []
                self.responses = [
                    _Response(
                        200,
                        initial,
                        {"last-modified": "Sat, 18 Jul 2026 04:16:00 GMT"},
                    ),
                    _Response(
                        416,
                        headers={"content-range": f"bytes */{len(initial)}"},
                    ),
                    _Response(
                        206,
                        delta,
                        {
                            "last-modified": "Sat, 18 Jul 2026 04:21:00 GMT",
                            "content-range": (
                                f"bytes {len(initial)}-"
                                f"{len(initial) + len(delta) - 1}/"
                                f"{len(initial) + len(delta)}"
                            ),
                        },
                    ),
                ]

            def get(self, url, *, headers, timeout):
                self.calls.append((url, headers, timeout))
                return self.responses.pop(0)

        client = _Client()
        cursor = NoaaMetarCycleCursor()
        as_of = datetime(2026, 7, 18, 4, 20, tzinfo=UTC)

        first, first_ok = cursor.poll(
            client=client,
            stations=("KORD",),
            as_of=as_of,
        )
        unchanged, unchanged_ok = cursor.poll(
            client=client,
            stations=("KORD",),
            as_of=as_of,
        )
        appended, appended_ok = cursor.poll(
            client=client,
            stations=("KORD",),
            as_of=as_of,
        )

        assert first_ok and unchanged_ok and appended_ok
        assert [report.obs_time.minute for report in first] == [15]
        assert unchanged == []
        assert [report.obs_time.minute for report in appended] == [20]
        assert "Range" not in client.calls[0][1]
        assert "Accept-Encoding" not in client.calls[0][1]
        assert client.calls[1][1]["Accept-Encoding"] == "identity"
        assert client.calls[1][1]["Range"] == f"bytes={len(initial)}-"
        assert client.calls[2][1]["Range"] == f"bytes={len(initial)}-"

    def test_priority_station_cursor_isolates_a_stalled_station(self):
        from src.data.day0_fast_obs import NoaaMetarStationCursor

        blocked = threading.Event()

        class _Response:
            status_code = 200

            def __init__(self, station):
                self.content = (
                    f"2026/07/18 04:15\n{station} 180415Z 24006KT 10SM CLR "
                    "28/22 A2993 RMK AO2 T02830222\n"
                ).encode()
                self.headers = {
                    "last-modified": "Sat, 18 Jul 2026 04:16:00 GMT",
                }

        class _Client:
            def get(self, url, *, headers, timeout):
                station = url.rsplit("/", 1)[-1].removesuffix(".TXT")
                if station == "KORD":
                    blocked.wait(1.0)
                return _Response(station)

        cursor = NoaaMetarStationCursor(max_workers=2)
        started = time.monotonic()
        reports, source_ok = cursor.poll(
            client=_Client(),
            stations=("KORD", "KAUS"),
            budget_s=0.2,
        )
        elapsed = time.monotonic() - started

        assert source_ok is True
        assert [report.station_id for report in reports] == ["KAUS"]
        assert elapsed < 0.5
        assert "KORD" in cursor._in_flight

        blocked.set()
        recovered, recovered_ok = cursor.poll(
            client=_Client(),
            stations=(),
            budget_s=0.5,
        )
        cursor.close()
        assert recovered_ok is True
        assert [report.station_id for report in recovered] == ["KORD"]

    def test_awc_reconciles_silent_global_cycle_rewrites(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        report = _report(
            "RKPK",
            datetime(2026, 8, 8, 5, 0, tzinfo=UTC),
            36.0,
        )
        recovery_calls = []

        def _recovery(stations, *, hours, client):
            recovery_calls.append((tuple(stations), hours, client))
            return [report]

        class _SyntacticallyCurrentCycle:
            def poll(self, **_kwargs):
                return [], True

        client = object()
        emitter = Day0FastObsEmitter(fetcher=_recovery)
        emitter._cycle_cursor = _SyntacticallyCurrentCycle()

        reports, source_ok, history_loaded = emitter._fetch_global_sources(
            client=client,
            stations=("RKPK",),
            fetch_hours=0.5,
            awc_due=True,
            history_missing=False,
            attempt_monotonic=1.0,
        )

        assert reports == [report]
        assert source_ok is True
        assert history_loaded is True
        assert recovery_calls == [(("RKPK",), 0.5, client)]

    def test_priority_station_fact_bypasses_global_cycle_wait(self):
        import src.data.day0_fast_obs as fast_obs

        report = _report(
            "KORD",
            datetime(2026, 7, 18, 4, 15, tzinfo=UTC),
            28.3,
        )

        class _StationCursor:
            def poll(self, **_kwargs):
                return [report], True

        cycle_started = threading.Event()
        release_cycle = threading.Event()

        class _CycleCursor:
            def poll(self, **_kwargs):
                cycle_started.set()
                release_cycle.wait(1.0)
                return [], True

        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._station_cursor = _StationCursor()
        emitter._cycle_cursor = _CycleCursor()
        emitter._full_window_loaded = True
        emitter._last_awc_attempt_monotonic = time.monotonic()

        started = time.monotonic()
        try:
            reports, status, _age = emitter._reports_with_status(
                ["KORD"],
                priority_stations=("KORD",),
            )
            elapsed = time.monotonic() - started

            assert cycle_started.wait(0.5)
            assert elapsed < 0.2
            assert reports == [report]
            assert status == fast_obs.FETCH_FRESH
            assert emitter._priority_http_client is not emitter._http_client
            assert (
                fast_obs.METAR_PRIORITY_HTTP_LIMITS.max_connections
                >= fast_obs.NoaaMetarStationCursor().max_workers
            )
        finally:
            release_cycle.set()
            if emitter._global_fetch_future is not None:
                emitter._global_fetch_future.result(timeout=1.0)
            if emitter._global_fetch_executor is not None:
                emitter._global_fetch_executor.shutdown(wait=True)

    def test_unchanged_priority_station_is_not_held_behind_global_cycle(self):
        import src.data.day0_fast_obs as fast_obs

        cached = _report(
            "KORD",
            datetime(2026, 7, 18, 4, 15, tzinfo=UTC),
            28.3,
        )
        cycle_started = threading.Event()
        release_cycle = threading.Event()
        cycle_calls = 0

        class _StationCursor:
            def poll(self, **_kwargs):
                return [], True

        class _CycleCursor:
            def poll(self, **_kwargs):
                nonlocal cycle_calls
                cycle_calls += 1
                cycle_started.set()
                release_cycle.wait(1.0)
                return [], True

        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._station_cursor = _StationCursor()
        emitter._cycle_cursor = _CycleCursor()
        emitter._cached_reports = [cached]
        emitter._cache_fetched_monotonic = time.monotonic()
        emitter._full_window_loaded = True
        emitter._last_awc_attempt_monotonic = time.monotonic()

        started = time.monotonic()
        try:
            reports, status, _age = emitter._reports_with_status(
                ["KORD"],
                priority_stations=("KORD",),
            )
            elapsed = time.monotonic() - started

            assert cycle_started.wait(0.5)
            assert elapsed < 0.2
            assert reports == [cached]
            assert status == fast_obs.FETCH_CACHE_HIT

            second_started = time.monotonic()
            second_reports, second_status, _age = emitter._reports_with_status(
                ["KORD"],
                priority_stations=("KORD",),
            )
            assert time.monotonic() - second_started < 0.2
            assert second_reports == [cached]
            assert second_status == fast_obs.FETCH_CACHE_HIT
            assert cycle_calls == 1
        finally:
            release_cycle.set()
            if emitter._global_fetch_future is not None:
                emitter._global_fetch_future.result(timeout=1.0)
            if emitter._global_fetch_executor is not None:
                emitter._global_fetch_executor.shutdown(wait=True)

    def test_completed_global_cycle_is_harvested_on_next_priority_tick(self):
        import src.data.day0_fast_obs as fast_obs

        report = _report(
            "KORD",
            datetime(2026, 7, 18, 4, 15, tzinfo=UTC),
            28.3,
        )

        class _StationCursor:
            def poll(self, **_kwargs):
                return [], True

        class _CycleCursor:
            def poll(self, **_kwargs):
                return [report], True

        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._station_cursor = _StationCursor()
        emitter._cycle_cursor = _CycleCursor()
        emitter._full_window_loaded = True
        emitter._last_awc_attempt_monotonic = time.monotonic()

        try:
            first_reports, first_status, _age = emitter._reports_with_status(
                ["KORD"],
                priority_stations=("KORD",),
            )
            assert first_reports == []
            assert first_status == fast_obs.FETCH_NO_DATA
            assert emitter._global_fetch_future is not None
            emitter._global_fetch_future.result(timeout=1.0)

            reports, status, _age = emitter._reports_with_status(
                ["KORD"],
                priority_stations=("KORD",),
            )
            assert reports == [report]
            assert status == fast_obs.FETCH_FRESH
        finally:
            if emitter._global_fetch_future is not None:
                emitter._global_fetch_future.result(timeout=1.0)
            if emitter._global_fetch_executor is not None:
                emitter._global_fetch_executor.shutdown(wait=True)

    def test_priority_success_cannot_authorize_stale_global_station_cache(self):
        """A KORD priority success must not emit an hour-old LFPB report while
        the shared global poll is still pending."""
        import src.data.day0_fast_obs as fast_obs

        conn = _world_conn()
        decision_time = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        chicago = SimpleNamespace(
            name="Chicago", timezone="America/Chicago", settlement_unit="C",
            wu_station="KORD", settlement_source_type="wu_icao",
        )
        paris = SimpleNamespace(
            name="Paris", timezone="Europe/Paris", settlement_unit="C",
            wu_station="LFPB", settlement_source_type="wu_icao",
        )
        kord = _report("KORD", decision_time - timedelta(minutes=4), 28.0, t_group=False)
        lfpb = _report("LFPB", decision_time - timedelta(minutes=5), 30.0, t_group=False)
        release_global = threading.Event()

        class _StationCursor:
            def poll(self, **_kwargs):
                return [kord], True

            def close(self):
                pass

        class _CycleCursor:
            def poll(self, **_kwargs):
                release_global.wait(1.0)
                return [], True

        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._station_cursor = _StationCursor()
        emitter._cycle_cursor = _CycleCursor()
        emitter._full_window_loaded = True
        emitter._last_awc_attempt_monotonic = time.monotonic()
        emitter._cached_reports = [lfpb]
        emitter._station_authority_initialized = True
        emitter._station_cache_fetched_monotonic["LFPB"] = time.monotonic() - 3600.0

        try:
            prefetch = emitter.prefetch(
                cities=[chicago, paris],
                decision_time=decision_time,
                priority_scopes=(("Chicago", "2026-07-18"),),
            )
            statuses = dict(
                (station, status)
                for station, status, _age in prefetch.station_statuses
            )
            assert statuses == {"KORD": fast_obs.FETCH_FRESH, "LFPB": fast_obs.FETCH_STALE_AFTER_FAILURE}
            assert emitter.emit_prefetched(
                world_conn=conn,
                prefetch=prefetch,
                received_at=decision_time.isoformat(),
            ) == 2
            cities = {
                row[0]
                for row in conn.execute(
                    "SELECT json_extract(payload_json, '$.city') FROM opportunity_events"
                )
            }
            assert cities == {"Chicago"}
        finally:
            release_global.set()
            emitter.close()

    def test_empty_global_success_does_not_refresh_absent_station_cache(self):
        """A successful cursor transport with no LFPB row is not current
        evidence for the old LFPB cache."""
        import src.data.day0_fast_obs as fast_obs

        decision_time = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        paris = SimpleNamespace(
            name="Paris", timezone="Europe/Paris", settlement_unit="C",
            wu_station="LFPB", settlement_source_type="wu_icao",
        )
        lfpb = _report("LFPB", decision_time - timedelta(minutes=5), 30.0, t_group=False)

        class _CycleCursor:
            def poll(self, **_kwargs):
                return [], True

        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._cycle_cursor = _CycleCursor()
        emitter._full_window_loaded = True
        emitter._last_awc_attempt_monotonic = time.monotonic()
        emitter._cached_reports = [lfpb]
        emitter._station_authority_initialized = True
        emitter._station_cache_fetched_monotonic["LFPB"] = time.monotonic() - 3600.0

        try:
            prefetch = emitter.prefetch(cities=[paris], decision_time=decision_time)
            assert prefetch.freshness_status == fast_obs.FETCH_CACHE_HIT
            assert prefetch.station_statuses == (
                ("LFPB", fast_obs.FETCH_STALE_AFTER_FAILURE, pytest.approx(3600.0, abs=1.0)),
            )
        finally:
            emitter.close()

    def test_subset_global_success_refreshes_only_reported_station(self):
        """A current KORD row may be fresh while an absent old LFPB row stays
        stale in the same global poll."""
        import src.data.day0_fast_obs as fast_obs

        decision_time = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        chicago = SimpleNamespace(
            name="Chicago", timezone="America/Chicago", settlement_unit="C",
            wu_station="KORD", settlement_source_type="wu_icao",
        )
        paris = SimpleNamespace(
            name="Paris", timezone="Europe/Paris", settlement_unit="C",
            wu_station="LFPB", settlement_source_type="wu_icao",
        )
        kord = _report("KORD", decision_time - timedelta(minutes=4), 28.0, t_group=False)
        lfpb = _report("LFPB", decision_time - timedelta(minutes=5), 30.0, t_group=False)

        class _CycleCursor:
            def poll(self, **_kwargs):
                return [kord], True

        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._cycle_cursor = _CycleCursor()
        emitter._full_window_loaded = True
        emitter._last_awc_attempt_monotonic = time.monotonic()
        emitter._cached_reports = [lfpb]
        emitter._station_authority_initialized = True
        emitter._station_cache_fetched_monotonic["LFPB"] = time.monotonic() - 3600.0

        try:
            prefetch = emitter.prefetch(cities=[chicago, paris], decision_time=decision_time)
            statuses = {
                station: status
                for station, status, _age in prefetch.station_statuses
            }
            assert statuses == {
                "KORD": fast_obs.FETCH_FRESH,
                "LFPB": fast_obs.FETCH_STALE_AFTER_FAILURE,
            }
        finally:
            emitter.close()

    def test_emitter_close_shuts_down_global_executor(self):
        import src.data.day0_fast_obs as fast_obs

        emitter = fast_obs.Day0FastObsEmitter()
        executor = fast_obs.ThreadPoolExecutor(max_workers=1)
        emitter._global_fetch_executor = executor
        emitter._global_fetch_future = executor.submit(lambda: None)

        emitter.close()

        assert emitter._global_fetch_executor is None
        assert executor._shutdown is True

    def test_awc_history_fetch_is_not_repeated_each_source_clock_poll(
        self,
        monkeypatch,
    ):
        import src.data.day0_fast_obs as fast_obs

        report = _report(
            "KORD",
            datetime(2026, 7, 18, 4, 15, tzinfo=UTC),
            28.3,
        )
        awc_calls = []

        def _awc(stations, **kwargs):
            awc_calls.append((stations, kwargs))
            return [report]

        class _Cursor:
            def poll(self, **_kwargs):
                return [], True

        monkeypatch.setattr(fast_obs, "fetch_metar_reports", _awc)
        emitter = fast_obs.Day0FastObsEmitter(fetcher=_awc, min_fetch_interval_s=0.0)
        emitter._cycle_cursor = _Cursor()

        first = emitter._reports_with_status(["KORD"])
        second = emitter._reports_with_status(["KORD"])
        emitter._last_awc_attempt_monotonic = 0.0
        third = emitter._reports_with_status(["KORD"])

        assert first[1] == fast_obs.FETCH_FRESH
        assert second[1] == fast_obs.FETCH_CACHE_HIT
        assert third[1] == fast_obs.FETCH_FRESH
        assert len(awc_calls) == 2

    def test_cycle_fact_survives_awc_recovery_failure(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        cycle_report = _report(
            "KORD",
            datetime(2026, 7, 18, 4, 15, tzinfo=UTC),
            28.3,
        )

        def _awc(*_args, **_kwargs):
            raise TimeoutError("recovery unavailable")

        class _Cursor:
            def poll(self, **_kwargs):
                return [cycle_report], True

        monkeypatch.setattr(fast_obs, "fetch_metar_reports", _awc)
        emitter = fast_obs.Day0FastObsEmitter(
            fetcher=_awc,
            min_fetch_interval_s=0.0,
        )
        emitter._cycle_cursor = _Cursor()

        reports, status, _age = emitter._reports_with_status(["KORD"])

        assert reports == [cycle_report]
        assert status == fast_obs.FETCH_FRESH

    def test_cycle_and_awc_mirror_keep_earliest_publication(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        observed = datetime(2026, 7, 18, 4, 15, tzinfo=UTC)
        early = _report("KORD", observed, 28.3)
        late = MetarReport(
            station_id=early.station_id,
            obs_time=early.obs_time,
            receipt_time=early.receipt_time + timedelta(seconds=40),
            temp_c=early.temp_c,
            metar_type=early.metar_type,
            raw=f"METAR {early.raw}",
        )

        def _awc(*_args, **_kwargs):
            return [late]

        class _Cursor:
            def poll(self, **_kwargs):
                return [early], True

        monkeypatch.setattr(fast_obs, "fetch_metar_reports", _awc)
        emitter = fast_obs.Day0FastObsEmitter(
            fetcher=_awc,
            min_fetch_interval_s=0.0,
        )
        emitter._cycle_cursor = _Cursor()

        reports, status, _age = emitter._reports_with_status(["KORD"])

        assert reports == [early]
        assert status == fast_obs.FETCH_FRESH
        assert list(emitter._pending_ledger_reports.values()) == [early]

    def test_incremental_cycle_fact_does_not_suppress_due_awc_recovery(
        self,
        monkeypatch,
    ):
        import src.data.day0_fast_obs as fast_obs

        report = _report(
            "KORD",
            datetime(2026, 7, 18, 4, 15, tzinfo=UTC),
            28.3,
        )
        awc_calls = []

        def _awc(*_args, **_kwargs):
            awc_calls.append(True)
            return []

        class _Cursor:
            def poll(self, **_kwargs):
                return [report], True

        monkeypatch.setattr(fast_obs, "fetch_metar_reports", _awc)
        emitter = fast_obs.Day0FastObsEmitter(
            fetcher=_awc,
            min_fetch_interval_s=0.0,
        )
        emitter._cycle_cursor = _Cursor()
        emitter._full_window_loaded = True

        reports, status, _age = emitter._reports_with_status(["KORD"])

        assert reports == [report]
        assert status == fast_obs.FETCH_FRESH
        assert awc_calls == [True]
        assert emitter._last_awc_attempt_monotonic > 0.0


# ===========================================================================
# R5 — unit law
# ===========================================================================

class TestUnitLaw:
    def test_f_city_with_t_group_converts_exactly(self):
        r = _report("KLGA", datetime(2026, 6, 10, 18, 51, tzinfo=UTC), 21.1, t_group=True)
        assert settlement_temp_for_report(r, "F") == pytest.approx(21.1 * 9 / 5 + 32)

    def test_f_city_without_t_group_is_skipped_fail_closed(self):
        r = _report("KLGA", datetime(2026, 6, 10, 18, 51, tzinfo=UTC), 21.0, t_group=False)
        assert settlement_temp_for_report(r, "F") is None

    def test_c_city_whole_degree_is_exact(self):
        r = _report("RKSI", datetime(2026, 6, 10, 5, 0, tzinfo=UTC), 21.0, t_group=False)
        assert settlement_temp_for_report(r, "C") == pytest.approx(21.0)

    def test_missing_temp_is_skipped(self):
        r = _report("KLGA", datetime(2026, 6, 10, 18, 51, tzinfo=UTC), None)
        assert settlement_temp_for_report(r, "F") is None


# ===========================================================================
# Running extremes: local-day membership, truncation, station filter
# ===========================================================================

class TestRunningExtremes:
    def test_local_day_membership_is_city_timezone(self):
        seoul = _seoul()
        # 2026-06-09T14:00Z = Jun 9 23:00 KST (prev local day);
        # 2026-06-09T16:00Z = Jun 10 01:00 KST (target day).
        reports = [
            _report("RKSI", datetime(2026, 6, 9, 14, 0, tzinfo=UTC), 28.0, t_group=False),
            _report("RKSI", datetime(2026, 6, 9, 16, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RKSI", datetime(2026, 6, 9, 18, 0, tzinfo=UTC), 19.0, t_group=False),
        ]
        ex = running_extremes_for_local_day(reports, city=seoul, target_date="2026-06-10")
        assert ex.sample_count == 2
        assert ex.high_so_far == pytest.approx(21.0)  # the 28C report belongs to Jun 9 local
        assert ex.low_so_far == pytest.approx(19.0)
        assert ex.current_temp == pytest.approx(19.0)

    def test_europe_low_boundary_excludes_tminus1_23_and_includes_target_00_01_23(self):
        london = _london()
        reports = [
            # 2026-06-17T22:00Z = Jun 17 23:00 BST, previous local day.
            _report("EGLC", datetime(2026, 6, 17, 22, 0, tzinfo=UTC), 10.0, t_group=False),
            # Target local day starts at 2026-06-17T23:00Z.
            _report("EGLC", datetime(2026, 6, 17, 23, 0, tzinfo=UTC), 16.0, t_group=False),
            _report("EGLC", datetime(2026, 6, 18, 0, 0, tzinfo=UTC), 14.0, t_group=False),
            _report("EGLC", datetime(2026, 6, 18, 22, 0, tzinfo=UTC), 12.0, t_group=False),
        ]

        before_midnight = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 17, 22, 30, tzinfo=UTC),
        )
        at_00 = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 17, 23, 30, tzinfo=UTC),
        )
        at_01 = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 18, 0, 30, tzinfo=UTC),
        )
        late_day = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 18, 22, 30, tzinfo=UTC),
        )

        assert before_midnight.sample_count == 0
        assert at_00.sample_count == 1
        assert at_00.low_so_far == pytest.approx(16.0)
        assert at_01.sample_count == 2
        assert at_01.low_so_far == pytest.approx(14.0)
        assert late_day.sample_count == 3
        assert late_day.low_so_far == pytest.approx(12.0)

    def test_as_of_truncation_excludes_later_reports(self):
        seoul = _seoul()
        reports = [
            _report("RKSI", datetime(2026, 6, 9, 16, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RKSI", datetime(2026, 6, 9, 20, 0, tzinfo=UTC), 26.0, t_group=False),
        ]
        ex = running_extremes_for_local_day(
            reports, city=seoul, target_date="2026-06-10",
            as_of=datetime(2026, 6, 9, 18, 0, tzinfo=UTC),
        )
        assert ex.sample_count == 1
        assert ex.high_so_far == pytest.approx(21.0)

    def test_other_station_reports_ignored_and_unit_law_skips_counted(self):
        nyc = _nyc()
        t = datetime(2026, 6, 10, 16, 51, tzinfo=UTC)
        reports = [
            _report("KJFK", t, 25.0, t_group=True),                 # wrong station
            _report("KLGA", t, 21.1, t_group=True),                 # used
            _report("KLGA", t + timedelta(hours=1), 23.0, t_group=False),  # unit-law skip
        ]
        ex = running_extremes_for_local_day(reports, city=nyc, target_date="2026-06-10")
        assert ex.sample_count == 1
        assert ex.skipped_unit_law == 1
        assert ex.high_so_far == pytest.approx(21.1 * 9 / 5 + 32)


# ===========================================================================
# Hard-fact statuses + provenance
# ===========================================================================

class TestObservationStatuses:
    def _extremes(self, city, **over):
        t = datetime(2026, 6, 10, 16, 51, tzinfo=UTC)
        reports = [_report(city.wu_station, t, 21.1, t_group=True)]
        return running_extremes_for_local_day(reports, city=city, target_date=over.pop("target_date", "2026-06-10"))

    def test_valid_observation_is_live_authority_and_passes_reactor_gate(self):
        nyc = _nyc()
        source = fast_obs_source_for_city(nyc)
        assert source is not None and source.source_id == "aviationweather_metar"
        obs = fast_obs_to_day0_observation(
            city=nyc, extremes=self._extremes(nyc), metric="high", source=source
        )
        assert obs["live_authority_status"] == "live"
        assert obs["source_authorized_status"] == "AUTHORIZED"
        assert obs["dst_status"] == "UNAMBIGUOUS"
        # available_at is the feed receiptTime, not our wall clock
        assert obs["observation_available_at"].startswith("2026-06-10T16:55")
        # Field-by-field equivalent of the reactor's 8-field hard-fact gate:
        assert all(
            obs[k] == v
            for k, v in {
                "source_match_status": "MATCH",
                "local_date_status": "MATCH",
                "station_match_status": "MATCH",
                "dst_status": "UNAMBIGUOUS",
                "metric_match_status": "MATCH",
                "rounding_status": "MATCH",
                "source_authorized_status": "AUTHORIZED",
                "live_authority_status": "live",
            }.items()
        )

    def test_wrong_local_date_is_not_live_authority(self):
        nyc = _nyc()
        source = fast_obs_source_for_city(nyc)
        ex = self._extremes(nyc)
        # claim the obs belongs to tomorrow -> local_date MISMATCH
        obs = fast_obs_to_day0_observation(
            city=nyc,
            extremes=ex.__class__(**{**ex.__dict__, "target_date": "2026-06-11"}),
            metric="high",
            source=source,
        )
        assert obs["local_date_status"] == "MISMATCH"
        assert obs["live_authority_status"] == "blocked"

    def test_non_wu_icao_city_has_no_fast_source(self):
        hko = SimpleNamespace(
            name="Hong Kong", timezone="Asia/Hong_Kong", settlement_unit="C",
            wu_station="VHHH", settlement_source_type="hko",
        )
        assert fast_obs_source_for_city(hko) is None

    def test_noaa_city_uses_direct_same_station_source(self):
        city = _istanbul()
        source = fast_obs_source_for_city(city)
        assert source is not None
        assert source.source_id == "aviationweather_metar"
        assert source.station_id == "LTFM"
        assert source.margin_units == 0.0

        obs = fast_obs_to_day0_observation(
            city=city,
            extremes=self._extremes(city, target_date="2026-06-10"),
            metric="high",
            source=source,
        )
        assert obs["source_match_status"] == "MATCH"
        assert obs["source_authorized_status"] == "AUTHORIZED"

    def test_transitioned_city_rejects_current_source_for_historical_target(self):
        from src.config import cities_by_name

        city = cities_by_name["Chicago"]
        historical_source = fast_obs_source_for_city(city, target_date="2026-08-22")
        current_source = fast_obs_source_for_city(city, target_date="2026-08-23")
        assert historical_source is not None
        assert current_source is not None
        assert historical_source != current_source

        reports = [
            _report(
                "KORD",
                datetime(2026, 8, 22, 20, 0, tzinfo=UTC),
                20.0,
                t_group=True,
            )
        ]
        extremes = running_extremes_for_local_day(
            reports,
            city=city,
            target_date="2026-08-22",
        )
        rejected = fast_obs_to_day0_observation(
            city=city,
            extremes=extremes,
            metric="high",
            source=current_source,
        )
        accepted = fast_obs_to_day0_observation(
            city=city,
            extremes=extremes,
            metric="high",
            source=historical_source,
        )

        assert rejected["source_match_status"] == "MISMATCH"
        assert rejected["live_authority_status"] == "blocked"
        assert accepted["source_match_status"] == "MATCH"
        assert accepted["live_authority_status"] == "live"


# ===========================================================================
# R6 — monotone emission through the real event store
# ===========================================================================

def _world_conn():
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


class TestEmitterMonotone:
    def _emit(self, emitter, conn, reports, when):
        return emitter.emit_events(
            world_conn=conn,
            cities=[_tokyo()],
            decision_time=when,
            received_at=when.isoformat(),
            limit=20,
        )

    def test_first_sight_emits_then_unchanged_is_silent_then_move_emits(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 01:00 JST
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)

        n1 = self._emit(emitter, conn, reports, t0 + timedelta(minutes=10))
        assert n1 == 2  # high + low first sight

        n2 = self._emit(emitter, conn, reports, t0 + timedelta(minutes=20))
        assert n2 == 0  # unchanged extreme -> monotone memo holds emission

        reports.append(_report("RJTT", t0 + timedelta(hours=1), 24.0, t_group=False))
        n3 = self._emit(emitter, conn, reports, t0 + timedelta(minutes=80))
        assert n3 == 2  # HIGH moved; LOW plateau carries a newer source version

        rows = conn.execute(
            "SELECT payload_json FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchall()
        assert len(rows) == 4
        import json as _json

        payloads = [_json.loads(r["payload_json"]) for r in rows]
        assert all(p["settlement_source"] == "aviationweather_metar" for p in payloads)
        assert all(p["live_authority_status"] == "live" for p in payloads)

    def test_noaa_print_advances_before_slower_ogimet_mirror(self, monkeypatch):
        """Istanbul loss replay: direct LTFM 32C must wake redecision even
        while the canonical Ogimet hourly mirror still ends at 31C."""
        conn = _world_conn()
        first = datetime(2026, 7, 27, 12, 50, tzinfo=UTC)
        latest = datetime(2026, 7, 27, 13, 20, tzinfo=UTC)
        clock = [first + timedelta(minutes=3)]
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0].astimezone(tz or UTC)
        monkeypatch.setattr("src.data.day0_fast_obs.datetime", Clock)
        monkeypatch.setattr("src.events.triggers.day0_extreme_updated.runtime_cities_by_name",
                            lambda: {"Istanbul": _istanbul()})
        reports = [
            _report("LTFM", first, 31.0, t_group=False, receipt_offset_min=2.0)
        ]
        emitter = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: reports,
            min_fetch_interval_s=0.0,
        )

        assert emitter.emit_events(
            world_conn=conn,
            cities=[_istanbul()],
            decision_time=first + timedelta(minutes=3),
            received_at=(first + timedelta(minutes=3)).isoformat(),
            limit=20,
        ) == 2
        conn.commit()

        reports.append(
            _report("LTFM", latest, 32.0, t_group=False, receipt_offset_min=2.0)
        )
        second_decision = latest + timedelta(minutes=3)
        clock[0] = second_decision
        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        assert emitter.emit_events(
            world_conn=conn,
            cities=[_istanbul()],
            decision_time=second_decision,
            received_at=second_decision.isoformat(),
            limit=20,
        ) == 2
        conn.commit()
        conn.set_trace_callback(None)

        ledger_insert = next(
            index
            for index, sql in enumerate(traced)
            if "INSERT OR IGNORE INTO observation_prints" in sql
        )
        event_insert = next(
            index
            for index, sql in enumerate(traced)
            if "INSERT OR IGNORE INTO opportunity_events" in sql
        )
        assert ledger_insert < event_insert
        high = conn.execute(
            """
            SELECT payload_json
              FROM opportunity_events
             WHERE event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(payload_json, '$.metric') = 'high'
             ORDER BY rowid DESC
             LIMIT 1
            """
        ).fetchone()
        payload = json.loads(high["payload_json"])
        assert payload["settlement_source"] == "aviationweather_metar"
        assert payload["settlement_source_type"] == "noaa"
        assert payload["station_id"] == "LTFM"
        assert payload["rounded_value"] == 32
        assert payload["observation_time"].startswith("2026-07-27T13:20")
        assert payload["observation_available_at"].startswith("2026-07-27T13:22")
        assert payload["live_authority_status"] == "live"

        context = read_noaa_fast_obs_context_from_ledger(
            conn,
            city=_istanbul(),
            target_date="2026-07-27",
            decision_time=second_decision,
        )
        assert context is not None
        assert context.source == "aviationweather_metar"
        assert context.station_id == "LTFM"
        assert context.current_temp == pytest.approx(32.0)
        assert context.high_so_far == pytest.approx(32.0)
        assert context.observation_time.startswith("2026-07-27T13:20")

    def test_noaa_fast_source_never_enters_wu_divergence_comparator(self):
        first = datetime(2026, 7, 27, 12, 50, tzinfo=UTC)
        reports = [_report("LTFM", first, 31.0, t_group=False)]
        checked: list[str] = []
        emitter = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: reports,
            min_fetch_interval_s=0.0,
        )

        prefetch = emitter.prefetch(
            cities=[_istanbul()],
            decision_time=first + timedelta(minutes=3),
            anomaly_check=lambda city, *_args: checked.append(city.name),
        )

        assert len(prefetch.eligible) == 1
        assert checked == []

    def test_restart_short_window_cannot_emit_regressed_high(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 01:00 JST

        first_reports = [_report("RJTT", t0, 25.0, t_group=False)]
        first = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: first_reports,
            min_fetch_interval_s=0.0,
        )
        assert self._emit(first, conn, first_reports, t0 + timedelta(minutes=10)) == 2

        short_window_reports = [_report("RJTT", t0 + timedelta(hours=1), 24.0, t_group=False)]
        restarted = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: short_window_reports,
            min_fetch_interval_s=0.0,
        )
        restarted.emit_events(
            world_conn=conn,
            cities=[_tokyo()],
            decision_time=t0 + timedelta(hours=1, minutes=10),
            received_at=(t0 + timedelta(hours=1, minutes=10)).isoformat(),
            limit=20,
        )

        high_values = [
            row[0]
            for row in conn.execute(
                """
                SELECT CAST(json_extract(payload_json, '$.rounded_value') AS INTEGER)
                  FROM opportunity_events
                 WHERE event_type='DAY0_EXTREME_UPDATED'
                   AND json_extract(payload_json, '$.city') = 'Tokyo'
                   AND json_extract(payload_json, '$.metric') = 'high'
                 ORDER BY created_at
                """
            ).fetchall()
        ]
        assert high_values == [25], "restart recovery must suppress lower later high=24"

    def test_emitted_event_passes_reactor_hard_fact_gate(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)
        assert self._emit(emitter, conn, reports, t0 + timedelta(minutes=10)) == 2

        import json as _json
        from src.events.reactor import _day0_hard_fact_payload_live_eligible

        row = conn.execute(
            "SELECT payload_json FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED' LIMIT 1"
        ).fetchone()
        event = SimpleNamespace(payload_json=row["payload_json"], payload=_json.loads(row["payload_json"]))
        assert _day0_hard_fact_payload_live_eligible(event) is True

    def test_f_city_with_only_whole_c_reports_emits_nothing(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 10, 16, 51, tzinfo=UTC)
        reports = [_report("KLGA", t0, 21.0, t_group=False)]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)
        n = emitter.emit_events(
            world_conn=conn, cities=[_nyc()],
            decision_time=t0 + timedelta(minutes=10),
            received_at=(t0 + timedelta(minutes=10)).isoformat(), limit=20,
        )
        assert n == 0

    def test_fetch_failure_is_fail_soft_zero(self):
        conn = _world_conn()
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        n = emitter.emit_events(
            world_conn=conn, cities=[_tokyo()],
            decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            received_at="2026-06-10T04:00:00+00:00", limit=20,
        )
        assert n == 0


# ===========================================================================
# R7 — oracle anomaly guard
# ===========================================================================

class TestOracleAnomaly:
    def _reports(self):
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 KST early
        return [
            _report("RKSI", t0, 21.0, t_group=False),
            _report("RKSI", t0 + timedelta(hours=1), 22.0, t_group=False),
            _report("RKSI", t0 + timedelta(hours=2), 26.0, t_group=False),  # after WU's last obs
        ]

    def test_matching_extremes_do_not_diverge(self):
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=22.0, wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
        )
        assert verdict.compared is True and verdict.diverged is False

    def test_metar_rise_after_wu_last_obs_is_latency_not_divergence(self):
        """R7 truncation contract: the 26C report (after WU's last obs) must be
        excluded from the comparison — METAR freshness is not an anomaly."""
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=22.0, wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
        )
        assert verdict.high_delta == pytest.approx(0.0)

    def test_true_divergence_flags_and_pauses(self):
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=27.0,  # WU claims 5C above the same-window METAR max
            wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
        )
        assert verdict.compared and verdict.diverged
        flag_day0_oracle_anomaly("Seoul", "2026-06-10", detail=verdict.detail)
        assert is_day0_family_paused("Seoul", "2026-06-10") is True
        assert is_day0_family_paused("Seoul", "2026-06-11") is False
        assert clear_day0_oracle_anomaly("Seoul", "2026-06-10") is True
        assert is_day0_family_paused("Seoul", "2026-06-10") is False

    def test_pause_expires_after_ttl(self):
        flag_day0_oracle_anomaly(
            "Seoul", "2026-06-10", detail="t",
            now=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
        )
        assert is_day0_family_paused(
            "Seoul", "2026-06-10", now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
        ) is True
        assert is_day0_family_paused(
            "Seoul", "2026-06-10", now=datetime(2026, 6, 12, 1, 0, tzinfo=UTC)
        ) is False

    def test_missing_wu_side_is_not_compared_and_not_paused(self):
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=None, wu_low_so_far=None, wu_last_obs_time=None,
        )
        assert verdict.compared is False and verdict.diverged is False

    def test_era_day0_q_path_raises_fail_closed_when_paused(self):
        """Enforcement relationship: a paused family's DAY0 q construction must
        raise (-> LIVE_INFERENCE_INPUTS_MISSING:DAY0_ORACLE_ANOMALY_PAUSED
        deterministic no-submit receipt at the proofs boundary)."""
        from src.engine.event_reactor_adapter import _live_yes_probabilities

        flag_day0_oracle_anomaly("Seoul", "2026-06-10", detail="test")
        event = SimpleNamespace(event_type="DAY0_EXTREME_UPDATED")
        family = SimpleNamespace(city="Seoul", target_date="2026-06-10", candidates=[])
        with pytest.raises(ValueError, match="DAY0_ORACLE_ANOMALY_PAUSED"):
            _live_yes_probabilities(
                event=event, payload={}, family=family,
                conn=None, calibration_conn=None, native_costs={},
                decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            )

    def test_unpaused_family_does_not_raise_anomaly_error(self):
        """Counterfactual: same call without a flag must NOT raise the anomaly
        error (it will fail later/differently on the None conn — anything but
        DAY0_ORACLE_ANOMALY_PAUSED is acceptable here)."""
        from src.engine.event_reactor_adapter import _live_yes_probabilities

        event = SimpleNamespace(event_type="DAY0_EXTREME_UPDATED")
        family = SimpleNamespace(city="Seoul", target_date="2026-06-10", candidates=[])
        try:
            _live_yes_probabilities(
                event=event, payload={"rounded_value": 25.0, "metric": "high"},
                family=family, conn=None, calibration_conn=None, native_costs={},
                decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            )
        except ValueError as exc:
            assert "DAY0_ORACLE_ANOMALY_PAUSED" not in str(exc)
        except Exception:
            pass  # any non-anomaly failure mode is out of scope here


# ===========================================================================
# R12 — empirical divergence thresholds (operator correction 2026-06-10:
# the 1.5F/1.0C guess replaced by measured per-city thresholds; provenance
# recorded; non-settlement-faithful cities excluded from the fast lane)
# ===========================================================================

class TestEmpiricalThresholds:
    def test_measured_city_uses_empirical_threshold(self):
        from src.data.day0_oracle_anomaly import divergence_threshold_for_city

        threshold, provenance = divergence_threshold_for_city("Tokyo", "C")
        assert provenance == "empirical"
        assert threshold == pytest.approx(1.0)  # feeds byte-identical post-rounding
        threshold, provenance = divergence_threshold_for_city("Seoul", "C")
        assert provenance == "empirical"
        assert threshold == pytest.approx(1.0)  # current bounded seven-day fit

    @pytest.mark.parametrize(
        ("city_name", "station_id"),
        (
            ("Beijing", "ZBAA"),
            ("Guangzhou", "ZGGG"),
            ("Wellington", "NZWN"),
            ("Ankara", "LTAC"),
            ("Karachi", "OPKC"),
        ),
    )
    def test_recent_loss_cities_use_measured_zero_margin_fast_lane(
        self, city_name, station_id,
    ):
        from src.data.day0_oracle_anomaly import (
            divergence_threshold_for_city,
            metar_margin_units_for_city,
        )

        # 2026-09-17: these assertions used to pin threshold 1.0 / margin 0.0. That pinned
        # the WU-era values; refit against the NOAA settlement page, Wellington disagrees on
        # 2 % of settlement days and carries 2.0. The property under test is that the fast
        # lane serves EXACTLY what the measurement says for a measured city — the number is
        # the measurement's to set.
        threshold, provenance = divergence_threshold_for_city(city_name, "C")
        assert provenance == "empirical"
        assert threshold >= 1.0
        expected_margin = metar_margin_units_for_city(city_name, "C")
        assert expected_margin is not None, f"{city_name} must stay measured, not excluded"

        source = fast_obs_source_for_city(SimpleNamespace(
            name=city_name,
            timezone="UTC",
            settlement_unit="C",
            wu_station=station_id,
            settlement_source_type="wu_icao",
        ))
        assert source is not None
        assert source.station_id == station_id
        assert source.margin_units == pytest.approx(expected_margin)

    def test_recent_measurements_match_city_contract_and_record_window(self):
        from pathlib import Path

        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        root = Path(__file__).resolve().parents[1]
        model = json.loads(
            (root / "config" / "wu_metar_divergence.json").read_text()
        )
        divergence = model["cities"]
        cities = {
            row["name"]: row
            for row in json.loads((root / "config" / "cities.json").read_text())["cities"]
        }
        measurement_date = datetime.fromisoformat(model["window"][1]).date()
        wu_cities = {
            name
            for name, city in cities.items()
            if (
                (city.get("settlement_source_type") or "wu_icao") == "wu_icao"
                or (
                    city.get("previous_settlement_source_type") == "wu_icao"
                    and city.get("settlement_source_type_effective_date")
                    and measurement_date
                    < date.fromisoformat(city["settlement_source_type_effective_date"])
                )
            )
        }
        # 2026-09-17: the artifact is no longer a single WU measurement. The settlement
        # product for 48 cities became the NOAA page (4f48d461e) and those cities are now
        # measured page-vs-mirror by scripts/measure_settlement_page_metar_divergence.py,
        # while the five still-wu_icao cities keep their WU measurement and are carried
        # through tagged `carried_from_method`. So the artifact's city set is no longer
        # equal to the WU city set, and the per-city WU provenance fields below exist only
        # on WU-measured rows. Production reads exactly three keys from any row
        # (empirical_threshold, settlement_faithful, threshold_provenance — see
        # day0_oracle_anomaly._load_divergence_model consumers); the rest is measurement
        # provenance, so this contract applies to the WU rows it was written for.
        wu_measured = {
            name
            for name in wu_cities
            if name in divergence and "measurement_window" in divergence[name]
        }
        assert wu_measured, "no WU-measured rows remain in the artifact"
        assert wu_measured <= set(divergence)
        for city_name in sorted(wu_measured):
            measurement = divergence[city_name]
            city = cities[city_name]
            assert measurement["station_id"] == city["wu_station"]
            assert measurement["unit"] == city["unit"]
            assert measurement["measurement_window_days"] == 7
            # A carried row's window/generated_at belong to the measurement that produced
            # it, not to this artifact's header, so they are self-consistent rather than
            # equal to the header.
            assert datetime.fromisoformat(measurement["measurement_generated_at"]) >= (
                datetime.fromisoformat(measurement["measurement_window"][1])
            )
            assert datetime.fromisoformat(measurement["measurement_generated_at"]) >= (
                datetime.fromisoformat(measurement["measurement_window"][1])
            )
            if measurement["matched_pairs"] >= 100:
                assert measurement["threshold_provenance"] == "empirical"
                assert metar_margin_units_for_city(
                    city_name,
                    city["unit"],
                ) is not None
            else:
                assert measurement["threshold_provenance"] == "thin_sample"
                assert metar_margin_units_for_city(
                    city_name,
                    city["unit"],
                ) is None

    def test_unmeasured_city_falls_back_to_conservative_default(self):
        from src.data.day0_oracle_anomaly import (
            DIVERGENCE_THRESHOLD,
            divergence_threshold_for_city,
        )

        threshold_f, _ = divergence_threshold_for_city("NoSuchCity", "F")
        assert threshold_f == pytest.approx(DIVERGENCE_THRESHOLD["F"])

    def test_missing_model_file_degrades_to_defaults(self, tmp_path):
        """2026-07-26 (Shenzhen class): a missing/unreadable model file means
        EVERY city is unmeasured for this call — the conservative direction,
        not a blanket faithful assumption. See test_settlement_faithfulness_
        verdicts for the measured-city cases."""
        from src.data.day0_oracle_anomaly import (
            DIVERGENCE_THRESHOLD,
            city_metar_settlement_faithful,
            divergence_threshold_for_city,
        )

        bogus = tmp_path / "nope.json"
        threshold, provenance = divergence_threshold_for_city("Tokyo", "C", path=bogus)
        assert provenance == "default_guess"
        assert threshold == pytest.approx(DIVERGENCE_THRESHOLD["C"])
        assert city_metar_settlement_faithful("Seoul", path=bogus) is False

    def test_settlement_faithfulness_verdicts(self):
        """The verdict must follow the measurement, and absence must not read as trust.

        2026-09-17: this used to assert NYC faithful. Refit against the NOAA settlement
        page NYC disagrees on 38 % of settlement days and is correctly not faithful, so
        pinning it asserted a fact the measurement contradicts. What the artifact says
        about a city is the artifact's to say; what this test guards is that the verdict
        is READ from it and that an unmeasured city is not trusted by default.
        """
        import json
        from pathlib import Path

        from src.data.day0_oracle_anomaly import city_metar_settlement_faithful

        artifact = json.loads(
            (Path(__file__).resolve().parents[1] / "config" / "wu_metar_divergence.json")
            .read_text(encoding="utf-8")
        )
        entries = artifact.get("cities") or {}
        checked = 0
        for name, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("settlement_faithful") is None:
                continue
            checked += 1
            assert city_metar_settlement_faithful(str(name)) is bool(
                entry["settlement_faithful"]
            ), name
        assert checked >= 40, f"expected the measured city set, saw {checked}"
        # 2026-07-26 (Shenzhen class): a city with NO entry at all is no
        # longer assumed faithful — an unmeasured city carries LESS evidence
        # than a measured-thin sample, not more trust than one.
        assert city_metar_settlement_faithful("UnmeasuredCity") is False

    def test_well_measured_city_uses_current_empirical_margin(self):
        """The live source resolves the current bounded empirical margin.

        2026-09-17: the three cities here were pinned at 0.0. That pinned the WU-era
        artifact's values rather than this property — NYC now carries its measured 2.0
        against the settlement page. The property is that the source serves EXACTLY what
        the lookup returns, whatever that is.
        """
        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        for city in (_seoul(), _tokyo(), _nyc()):
            name = str(getattr(city, "name", "") or "")
            unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
            expected = metar_margin_units_for_city(name, unit)
            source = fast_obs_source_for_city(city)
            if expected is None:
                assert source is None, f"{name}: unmeasured city must be excluded"
                continue
            assert source is not None, name
            assert source.margin_units == pytest.approx(expected), name

    def test_guard_verdict_records_threshold_provenance(self):
        verdict = check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10",
            metar_reports=[
                MetarReport(
                    station_id="RJTT",
                    obs_time=datetime(2026, 6, 9, 16, 0, tzinfo=UTC),
                    receipt_time=datetime(2026, 6, 9, 16, 4, tzinfo=UTC),
                    temp_c=21.0, metar_type="METAR", raw="METAR RJTT 21/15",
                ),
            ],
            wu_high_so_far=21.0, wu_low_so_far=21.0,
            # within the round-2 coverage tolerance of the METAR window (the
            # detector now refuses to conclude when METAR lags WU's last obs)
            wu_last_obs_time=datetime(2026, 6, 9, 16, 4, tzinfo=UTC),
        )
        assert verdict.compared is True
        assert "threshold_provenance=empirical" in verdict.detail

    def test_empirical_tightening_one_unit_divergence_now_flags_for_clean_city(self):
        """For a measured-identical city the threshold tightened from the 1.5F
        guess to 1.0 — a 1.4F rounded-extreme divergence that the guess would
        have ignored now flags (sharper tamper detector).

        2026-09-17: this used to name NYC as the identical city. NYC is no longer
        one — refit against the NOAA settlement page (the product the market now
        resolves against) it disagrees on 38 % of settlement days and carries a
        threshold of 2.0. The property under test is the TIGHTENING, not NYC, so
        the city is now taken from the artifact's own verdict: any city the
        measurement calls identical must sit at the 1.0 floor, strictly below the
        1.5F guess it replaced. If no city is identical the premise is gone and the
        test says so rather than passing vacuously.
        """
        from src.data.day0_oracle_anomaly import divergence_threshold_for_city
        from src.config import cities_by_name

        identical = []
        for name, city in cities_by_name.items():
            unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
            threshold, provenance = divergence_threshold_for_city(str(name), unit)
            if provenance == "empirical" and threshold == pytest.approx(1.0):
                identical.append((str(name), unit, threshold))
        assert identical, (
            "no city is measured identical to its settlement product; the "
            "tightening this test guards has no subject left"
        )
        for name, _unit, threshold in identical:
            # 1.0 is the floor, and it is strictly sharper than the 1.5F guess.
            assert 1.4 > threshold, name


# ===========================================================================
# day0 defect-5 (2026-07-16) — margin absorption replaces binary exclusion
# for a measured-but-not-settlement-faithful METAR station. Seoul/RKSI type
# specimen: a raw 30.0C reading used to enter NOTHING (fast_obs_source_for_city
# returned None); it now enters the running belief at 28.0C (30.0 - the
# measured 2.0C margin), not at face value and not excluded.
# ===========================================================================

class TestMetarMarginAbsorption:
    def _reports(self, temps_with_minutes, station="RKSI"):
        base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
        return [
            _report(station, base + timedelta(minutes=m), t, t_group=False)
            for m, t in temps_with_minutes
        ]

    def test_seoul_type_specimen_reading_enters_belief_shifted_by_margin(self):
        """The type specimen: METAR 30.0C at Seoul/RKSI, margin 2.0C ->
        high_so_far == 28.0C, not 30.0 (face value) and not None (excluded,
        the pre-fix behavior — fast_obs_source_for_city(_seoul()) used to
        return None, so this reading previously entered nothing at all)."""
        reports = self._reports([(0, 30.0)])
        ex = running_extremes_for_local_day(
            reports, city=_seoul(), target_date="2026-06-10", margin_units=2.0,
        )
        assert ex.high_so_far == pytest.approx(28.0)
        assert ex.current_temp == pytest.approx(30.0)  # diagnostic field stays raw

    def test_faithful_city_margin_zero_is_unchanged_face_value(self):
        reports = self._reports([(0, 30.0)], station="RJTT")
        ex = running_extremes_for_local_day(
            reports, city=_tokyo(), target_date="2026-06-10", margin_units=0.0,
        )
        assert ex.high_so_far == pytest.approx(30.0)

    def test_low_metric_mirror_margin_direction_flips(self):
        """LOW metric: a reading proves the true min is AT MOST reading +
        margin (margin adds, not subtracts, for the low side)."""
        reports = self._reports([(0, 10.0)])
        ex = running_extremes_for_local_day(
            reports, city=_seoul(), target_date="2026-06-10", margin_units=2.0,
        )
        assert ex.low_so_far == pytest.approx(12.0)

    def test_seoul_source_resolves_measured_margin_from_real_config(self):
        source = fast_obs_source_for_city(_seoul())
        assert source is not None
        assert source.margin_units == pytest.approx(0.0)

    def test_emitted_observation_records_margin_and_shifted_raw_value(self):
        """End-to-end through fast_obs_to_day0_observation: raw_value in the
        emitted payload is the ALREADY-shifted value (consistent with what
        gets rounded and stored), and metar_margin_units_applied records the
        margin so the pre-shift reading stays reconstructable."""
        source = fast_obs_source_for_city(_seoul())
        assert source is not None
        source = FastObsSource(
            source_id=source.source_id,
            station_id=source.station_id,
            authority=source.authority,
            settlement_source_type=source.settlement_source_type,
            notes=source.notes,
            margin_units=2.0,
        )
        reports = self._reports([(0, 30.0)])
        extremes = running_extremes_for_local_day(
            reports, city=_seoul(), target_date="2026-06-10",
            margin_units=source.margin_units,
        )
        obs = fast_obs_to_day0_observation(
            city=_seoul(), extremes=extremes, metric="high", source=source,
        )
        assert obs["raw_value"] == pytest.approx(28.0)
        assert obs["metar_margin_units_applied"] == pytest.approx(2.0)
        # pre-shift reading is reconstructable: raw_value + margin for HIGH
        assert obs["raw_value"] + obs["metar_margin_units_applied"] == pytest.approx(30.0)

    def test_thin_sample_unfaithful_city_still_excluded(self, tmp_path):
        """A measured-but-not-faithful city whose divergence sample is too
        thin to trust (threshold_provenance != 'empirical') stays excluded —
        margin-absorption requires a well-sampled measurement, not just any
        unfaithful verdict."""
        import json

        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        path = tmp_path / "divergence.json"
        path.write_text(json.dumps({
            "cities": {
                "ThinCity": {
                    "matched_pairs": 12,
                    "empirical_threshold": 2.5,
                    "threshold_provenance": "thin_sample",
                    "settlement_faithful": False,
                },
            },
        }))
        assert metar_margin_units_for_city("ThinCity", "C", path=path) is None

    def test_thin_sample_apparent_faithfulness_is_not_executable(self, tmp_path):
        """Zero disagreements in a tiny sample do not prove source fidelity."""
        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        path = tmp_path / "divergence.json"
        path.write_text(json.dumps({
            "cities": {
                "ThinLuckyCity": {
                    "matched_pairs": 4,
                    "empirical_threshold": 1.0,
                    "threshold_provenance": "thin_sample",
                    "settlement_faithful": True,
                },
            },
        }))
        assert metar_margin_units_for_city("ThinLuckyCity", "C", path=path) is None

    def test_never_measured_city_is_now_excluded_not_default_margin(self):
        """2026-07-26 (Shenzhen class, day0 defect-6): a city with NO entry at
        all in wu_metar_divergence.json used to default to
        settlement_faithful=True and get the generic 1.0C/1.5F margin —
        Shenzhen ran on that default against a live measured p99=4.0C/
        max=11.0C divergence (83% of hours disagreeing >=1C). Absence of a
        measurement is now excluded (None), the SAME bucket as a
        measured-but-thin-sample city — not defaulted to the faithful
        bucket. Only a MEASURED city (empirical or default-guess threshold on
        record) keeps a non-None margin."""
        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        assert metar_margin_units_for_city("CityNeverMeasured", "C") is None
        assert metar_margin_units_for_city("CityNeverMeasured", "F") is None

    def test_shenzhen_class_large_real_divergence_not_treated_faithful(self, tmp_path):
        """Antibody: a city with a large REAL divergence (Shenzhen decisive
        test: median|delta|=1.0C, p99=4.0C, max=11.0C, disagree>=1C=83%) must
        not be treated as settlement-faithful merely because it lacks a
        wu_metar_divergence.json entry. Absence of measurement is not
        evidence of faithfulness."""
        from src.data.day0_oracle_anomaly import (
            city_metar_settlement_faithful,
            metar_margin_units_for_city,
        )

        path = tmp_path / "divergence.json"
        path.write_text(json.dumps({"cities": {}}))
        assert city_metar_settlement_faithful("Shenzhen", path=path) is False
        assert metar_margin_units_for_city("Shenzhen", "C", path=path) is None

    def test_previously_measured_cities_keep_executable_margin(self):
        """Regression: the (b) default-direction fix changes behavior for
        UNMEASURED cities only. Every already-measured city in the real
        config/wu_metar_divergence.json must keep an EXECUTABLE margin — a
        real number the fast lane can absorb, never None (exclusion).

        2026-09-17: this assertion used to pin each city at 0.0. That pinned the
        WU-era artifact's VALUES, not this fix's invariant. The artifact has since
        been refit against the NOAA settlement page (the product the 48 markets now
        resolve against) instead of against a second METAR mirror, so a city whose
        METAR genuinely disagrees with the page now carries its measured allowance
        (Denver/Dallas/NYC 2.0, Lucknow 7.0) rather than 0.0. A measured city
        keeping an executable margin is the property this regression guards; the
        specific number is the measurement's to decide, and pinning it made this
        test fail for a refit rather than for the regression it exists to catch.
        """
        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        f_cities = {
            "NYC", "Chicago", "Miami", "Dallas", "Denver", "Atlanta",
            "Los Angeles", "Houston", "Austin", "San Francisco", "Seattle",
        }
        measured_cities = f_cities | {
            "London", "Paris", "Amsterdam", "Milan", "Munich", "Madrid",
            "Tokyo", "Singapore", "Taipei", "Toronto", "Beijing",
            "Guangzhou", "Wellington", "Ankara", "Karachi", "Seoul",
        }
        for city_name in sorted(measured_cities):
            unit = "F" if city_name in f_cities else "C"
            margin = metar_margin_units_for_city(city_name, unit)
            assert margin is not None, f"{city_name}: measured city must not be excluded"
            assert margin >= 0.0, f"{city_name}: a margin is never negative"


# ===========================================================================
# day0 defect-ledger (2026-07-16) — boot hydration. A fresh process's
# _cached_reports is empty until the first successful HTTP fetch; hydration
# seeds it from observation_prints instead, so the belief isn't silently
# empty for the restart window. The kill-memo recovery path
# (_recover_kill_memo_from_events) is untouched and stays as defense in
# depth — these tests are scoped to the in-process cache path only.
# ===========================================================================

class TestLedgerPublicationDelta:
    def test_only_unconfirmed_publications_are_sent_to_sqlite(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        first = _report("RJTT", t0, 21.0, t_group=False)
        second = _report(
            "RJTT",
            t0 + timedelta(minutes=30),
            22.0,
            t_group=False,
        )
        source_reports = [first]
        emitter = Day0FastObsEmitter(
            fetcher=lambda _stations, **_kw: list(source_reports),
            min_fetch_interval_s=0.0,
        )

        pf1 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=5),
        )
        assert pf1.ledger_reports == (first,)
        inserted_event_ids: list[str] = []
        inserted_families: list[tuple[str, str, str]] = []
        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=pf1,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            inserted_event_ids=inserted_event_ids,
            inserted_families=inserted_families,
        ) == 2
        assert len(inserted_event_ids) == 2
        assert len(set(inserted_event_ids)) == 2
        assert inserted_families == [
            ("Tokyo", "2026-06-10", "high"),
            ("Tokyo", "2026-06-10", "low"),
        ]

        pf2 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=6),
        )
        assert pf2.ledger_reports == ()

        source_reports.append(second)
        pf3 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=36),
        )
        assert pf3.ledger_reports == (second,)

    def test_failed_ledger_append_does_not_acknowledge_publication(self, monkeypatch):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        emitter = Day0FastObsEmitter(
            fetcher=lambda _stations, **_kw: [report],
            min_fetch_interval_s=0.0,
        )
        monkeypatch.setattr(
            "src.state.schema.observation_prints_schema.append_print",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("busy")
            ),
        )

        pf1 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=5),
        )
        with pytest.raises(Day0PublicationLedgerUnavailable):
            emitter.emit_prefetched(
                world_conn=conn,
                prefetch=pf1,
                received_at=(t0 + timedelta(minutes=5)).isoformat(),
            )
        pf2 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=6),
        )

        assert pf2.ledger_reports == (report,)


class TestLedgerHydration:
    def test_cold_start_hydrates_cache_and_returns_todays_ledger_max(self):
        from src.data.day0_fast_obs import fast_obs_source_for_city
        from src.state.schema.observation_prints_schema import append_print

        conn = _world_conn()
        tokyo = _tokyo()
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T16:00:00+00:00", value_native=21.0, unit="C",
            fetched_at_utc="2026-06-09T16:04:00+00:00", raw_report="METAR RJTT 21/15",
        )
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T17:00:00+00:00", value_native=24.0, unit="C",
            fetched_at_utc="2026-06-09T17:04:00+00:00", raw_report="METAR RJTT 24/15",
        )
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        source = fast_obs_source_for_city(tokyo)
        eligible = ((tokyo, source, "2026-06-10"),)

        hydrated_count = emitter.hydrate_from_ledger(conn, eligible)
        assert hydrated_count == 2

        extremes = emitter.latest_extremes(
            tokyo, "2026-06-10", as_of=datetime(2026, 6, 9, 18, 0, tzinfo=UTC),
        )
        assert extremes is not None
        assert extremes.high_so_far == pytest.approx(24.0)

    def test_hydration_is_noop_once_the_cache_is_warm(self):
        from src.data.day0_fast_obs import fast_obs_source_for_city
        from src.state.schema.observation_prints_schema import append_print

        conn = _world_conn()
        tokyo = _tokyo()
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T16:00:00+00:00", value_native=21.0, unit="C",
            fetched_at_utc="2026-06-09T16:04:00+00:00",
        )
        emitter = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: [_report("RJTT", datetime(2026, 6, 9, 16, 0, tzinfo=UTC), 30.0, t_group=False)],
            min_fetch_interval_s=0.0,
        )
        emitter._reports_with_status(["RJTT"])  # a live fetch already warmed the cache
        source = fast_obs_source_for_city(tokyo)
        eligible = ((tokyo, source, "2026-06-10"),)

        hydrated_count = emitter.hydrate_from_ledger(conn, eligible)
        assert hydrated_count == 0  # no-op -- must not overwrite the live 30.0 with the ledger's 21.0

    def test_hydration_with_no_ledger_data_is_a_safe_noop(self):
        from src.data.day0_fast_obs import fast_obs_source_for_city

        conn = _world_conn()
        tokyo = _tokyo()
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        source = fast_obs_source_for_city(tokyo)
        eligible = ((tokyo, source, "2026-06-10"),)

        assert emitter.hydrate_from_ledger(conn, eligible) == 0
        assert emitter._cached_reports == []

    def test_emit_prefetched_hydrates_on_a_cold_start_with_no_fetch_this_cycle(self):
        """The real entry point: emit_prefetched calls hydration even when
        THIS cycle's own fetch produced nothing -- the exact scenario
        hydration exists for (an outage spanning multiple cycles)."""
        from src.data.day0_fast_obs import FastObsPrefetch, fast_obs_source_for_city
        from src.state.schema.observation_prints_schema import append_print

        conn = _world_conn()
        tokyo = _tokyo()
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T16:00:00+00:00", value_native=21.0, unit="C",
            fetched_at_utc="2026-06-09T16:04:00+00:00",
        )
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        source = fast_obs_source_for_city(tokyo)
        decision_time = datetime(2026, 6, 9, 18, 0, tzinfo=UTC)
        prefetch = FastObsPrefetch(
            eligible=((tokyo, source, "2026-06-10"),),
            reports=(),  # this cycle's own fetch produced nothing
            freshness_status="no_data",
            cache_age_s=None,
            decision_time=decision_time,
        )

        emitter.emit_prefetched(world_conn=conn, prefetch=prefetch, received_at=decision_time.isoformat())

        assert len(emitter._cached_reports) == 1
        assert emitter._cached_reports[0].temp_c == pytest.approx(21.0)


# ===========================================================================
# R19 — source-failure discipline + mutex/no-HTTP split (PR#404 P0-2 / P0-3)
# ===========================================================================

class TestFetchFailureDiscipline:
    """PR#404 P0-3: a fetch failure after a populated cache must (a) arm the
    failure throttle (no tight retry storm), (b) serve the old cache ONLY with
    an explicit stale status, and (c) never emit live-authority events from a
    cache older than the city's staleness budget."""

    def _emitter_with_cache(self, reports, interval=300.0):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        calls = {"n": 0}

        def fetcher(stations, **kw):
            calls["n"] += 1
            return list(reports) if calls["n"] == 1 else []

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=interval)
        return emitter, calls

    def test_failure_serves_stale_with_explicit_status_and_throttles(self):
        from src.data.day0_fast_obs import (
            FETCH_CACHE_HIT,
            FETCH_FRESH,
            FETCH_STALE_AFTER_FAILURE,
        )

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter, calls = self._emitter_with_cache(reports, interval=0.0)

        out, status, _age = emitter._reports_with_status(["RJTT"])
        assert status == FETCH_FRESH and out and calls["n"] == 1

        # cache now exists; interval 0 -> next call attempts again and FAILS
        out, status, age = emitter._reports_with_status(["RJTT"])
        assert calls["n"] == 2
        assert status == FETCH_STALE_AFTER_FAILURE
        assert out, "old cache is served, but never silently as fresh"

        # failure-throttle: with a real interval, the next pass must NOT
        # re-invoke the fetcher (no retry storm during an outage)
        emitter.min_fetch_interval_s = 3600.0
        out, status, _age = emitter._reports_with_status(["RJTT"])
        assert calls["n"] == 2, "failed attempt must arm the throttle"
        assert status in (FETCH_STALE_AFTER_FAILURE, FETCH_CACHE_HIT)

    def test_slow_success_does_not_double_the_start_to_start_poll_interval(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        calls = {"n": 0}
        clock = iter((100.0, 100.7, 105.1, 105.3))

        def fetcher(_stations, **_kwargs):
            calls["n"] += 1
            return reports

        monkeypatch.setattr(fast_obs.time, "monotonic", lambda: next(clock))
        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=5.0)

        assert emitter._reports_with_status(["RJTT"])[1] == fast_obs.FETCH_FRESH
        assert emitter._reports_with_status(["RJTT"])[1] == fast_obs.FETCH_FRESH
        assert calls["n"] == 2

    def test_stale_cache_beyond_budget_emits_no_live_event_but_updates_kill_memo(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        calls = {"n": 0}

        def fetcher(stations, **kw):
            calls["n"] += 1
            return list(reports) if calls["n"] == 1 else []

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        # pass 1: fresh fetch fills cache
        pf = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert pf.freshness_status == "fresh_fetch"
        # pass 2: fetch fails -> stale-after-failure; age the cache far beyond
        # Tokyo's staleness budget (60 min) by rewinding the cache clock.
        import time as _time

        emitter._cache_fetched_monotonic = _time.monotonic() - 7200.0
        pf2 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=10))
        assert pf2.freshness_status == "stale_cache_after_failure"
        assert pf2.cache_age_s is not None and pf2.cache_age_s > 3600.0

        n = emitter.emit_prefetched(
            world_conn=conn, prefetch=pf2,
            received_at=(t0 + timedelta(minutes=10)).isoformat(), limit=20,
        )
        assert n == 0, "stale-beyond-budget cache must NOT emit live-authority events"
        rows = conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0]
        assert rows == 0
        # the monotone hard-fact KILL memo still advances (staleness-safe direction)
        assert emitter.latest_rounded_extreme("Tokyo", "2026-06-10", "high") == 21

    def test_no_cache_failure_is_no_data(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter, FETCH_NO_DATA

        emitter = Day0FastObsEmitter(fetcher=lambda s, **kw: [], min_fetch_interval_s=0.0)
        out, status, age = emitter._reports_with_status(["RJTT"])
        assert out == [] and status == FETCH_NO_DATA and age is None


class TestIncrementalFetchWindow:
    def test_cold_fetch_is_full_then_warm_fetch_merges_recent_delta(self):
        from src.data.day0_fast_obs import (
            Day0FastObsEmitter,
            METAR_FULL_FETCH_HOURS,
            METAR_INCREMENTAL_FETCH_HOURS,
        )

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        first = _report("RJTT", t0, 21.0, t_group=False)
        second = _report("RJTT", t0 + timedelta(hours=1), 24.0, t_group=False)
        hours = []
        payloads = [[first], [second]]

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return payloads.pop(0)

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        first_window, _, _ = emitter._reports_with_status(["RJTT"])
        second_window, _, _ = emitter._reports_with_status(["RJTT"])

        assert hours == [METAR_FULL_FETCH_HOURS, METAR_INCREMENTAL_FETCH_HOURS]
        assert first_window == [first]
        assert second_window == [first, second]

    def test_warm_fetch_periodically_backfills_late_publications(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        hours = []
        clock = iter((100.0, 100.1, 1000.2, 1000.3))

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return [report]

        monkeypatch.setattr(fast_obs.time, "monotonic", lambda: next(clock))
        emitter = fast_obs.Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter._reports_with_status(["RJTT"])
        emitter._reports_with_status(["RJTT"])

        assert hours == [
            fast_obs.METAR_FULL_FETCH_HOURS,
            fast_obs.METAR_BACKFILL_FETCH_HOURS,
        ]

    def test_identical_warm_payload_skips_full_window_merge(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        emitter = fast_obs.Day0FastObsEmitter(
            fetcher=lambda _stations, **_kwargs: [report],
            min_fetch_interval_s=0.0,
        )

        first_window, _, _ = emitter._reports_with_status(["RJTT"])
        monkeypatch.setattr(
            fast_obs,
            "_merge_report_windows",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("identical payload rebuilt the retained window")
            ),
        )
        second_window, status, _ = emitter._reports_with_status(["RJTT"])

        assert first_window == [report]
        assert second_window == [report]
        assert status == fast_obs.FETCH_FRESH

    def test_fetch_window_expands_across_an_outage(self):
        import time as _time

        from src.data.day0_fast_obs import (
            Day0FastObsEmitter,
            METAR_FULL_FETCH_HOURS,
            METAR_RECOVERY_OVERLAP_HOURS,
        )

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        hours = []

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return [report]

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter._reports_with_status(["RJTT"])
        emitter._cache_fetched_monotonic = _time.monotonic() - 3 * 3600.0
        emitter._reports_with_status(["RJTT"])

        assert hours == [
            METAR_FULL_FETCH_HOURS,
            pytest.approx(3.0 + METAR_RECOVERY_OVERLAP_HOURS, abs=0.01),
        ]

    def test_ledger_hydration_does_not_skip_full_network_recovery(self):
        import time as _time

        from src.data.day0_fast_obs import Day0FastObsEmitter, METAR_FULL_FETCH_HOURS

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        hours = []

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return [_report("RJTT", t0, 22.0, t_group=False)]

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter._cached_reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter._cache_fetched_monotonic = _time.monotonic()
        emitter._reports_with_status(["RJTT"])

        assert hours == [METAR_FULL_FETCH_HOURS]
        assert emitter._cached_reports[0].temp_c == pytest.approx(22.0)


class TestMetarConnectionReuse:
    def test_fetch_uses_injected_http_client(self):
        from src.data.day0_fast_obs import fetch_metar_reports

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return []

        class Client:
            def __init__(self):
                self.calls = []

            def get(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return Response()

        client = Client()
        assert fetch_metar_reports(["RJTT"], hours=2.0, client=client) == []
        assert len(client.calls) == 1
        assert client.calls[0][1]["params"]["hours"] == 2.0

    def test_emitter_reuses_isolated_clients_across_polls(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        clients = []

        class Client:
            def __init__(self, **_kwargs):
                self.calls = 0
                clients.append(self)

            def get(self, url, **_kwargs):
                self.calls += 1
                if "tgftp.nws.noaa.gov" in url:
                    return SimpleNamespace(
                        status_code=200,
                        content=b"",
                        headers={
                            "last-modified": "Sat, 18 Jul 2026 04:16:00 GMT",
                        },
                    )
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: [
                        {
                            "icaoId": report.station_id,
                            "obsTime": report.obs_time.timestamp(),
                            "receiptTime": report.receipt_time.isoformat(),
                            "temp": report.temp_c,
                            "metarType": report.metar_type,
                            "rawOb": report.raw,
                        }
                    ],
                )

        monkeypatch.setattr(fast_obs.httpx, "Client", Client)
        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._reports_with_status(["RJTT"])
        emitter._reports_with_status(["RJTT"])

        assert len(clients) == 2
        assert emitter._http_client in clients
        assert emitter._priority_http_client in clients
        assert emitter._http_client is not emitter._priority_http_client
        # Cold start reads the cycle plus AWC history; the next source-clock
        # poll reads only the cycle cursor. Priority station I/O has its own
        # pool and cannot consume global cycle/recovery connections.
        assert emitter._http_client.calls == 3
        assert emitter._priority_http_client.calls == 0


class TestMutexNoHttpSplit:
    """PR#404 P0-2: the world-write mutex must never span HTTP. The write phase
    (emit_prefetched) performs zero network IO; main.py prefetches BEFORE
    acquiring the mutex."""

    def test_emit_prefetched_never_invokes_the_fetcher(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter, FastObsPrefetch

        def forbidden_fetcher(stations, **kw):
            raise AssertionError("HTTP fetch invoked inside the write phase")

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = (_report("RJTT", t0, 21.0, t_group=False),)
        emitter = Day0FastObsEmitter(fetcher=forbidden_fetcher, min_fetch_interval_s=0.0)
        from src.data.day0_fast_obs import fast_obs_source_for_city

        city = _tokyo()
        prefetch = FastObsPrefetch(
            eligible=((city, fast_obs_source_for_city(city), "2026-06-10"),),
            reports=reports,
            freshness_status="fresh_fetch",
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
        )
        conn = _world_conn()
        n = emitter.emit_prefetched(
            world_conn=conn, prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(), limit=20,
        )
        assert n == 2  # high + low emitted with ZERO fetcher invocations

    def test_emit_prefetched_only_recomputes_changed_stations(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        osaka = SimpleNamespace(
            name="Osaka",
            timezone="Asia/Tokyo",
            settlement_unit="C",
            wu_station="RJOO",
            settlement_source_type="wu_icao",
        )
        tokyo_report = _report("RJTT", t0, 21.0, t_group=False)
        osaka_report = _report("RJOO", t0, 22.0, t_group=False)
        prefetch = fast_obs.FastObsPrefetch(
            eligible=(
                (
                    tokyo,
                    fast_obs.FastObsSource(
                        source_id=fast_obs.FAST_OBS_SOURCE_ID,
                        station_id="RJTT",
                        authority="ICAO_STATION_NATIVE",
                        settlement_source_type="wu_icao",
                    ),
                    "2026-06-10",
                ),
                (
                    osaka,
                    fast_obs.FastObsSource(
                        source_id=fast_obs.FAST_OBS_SOURCE_ID,
                        station_id="RJOO",
                        authority="ICAO_STATION_NATIVE",
                        settlement_source_type="wu_icao",
                    ),
                    "2026-06-10",
                ),
            ),
            reports=(tokyo_report, osaka_report),
            freshness_status=fast_obs.FETCH_FRESH,
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
            ledger_reports=(tokyo_report,),
        )
        original = fast_obs.running_extremes_for_local_day
        seen: list[str] = []

        def _running_extremes(*args, **kwargs):
            seen.append(kwargs["city"].name)
            return original(*args, **kwargs)

        monkeypatch.setattr(fast_obs, "running_extremes_for_local_day", _running_extremes)

        emitted = fast_obs.Day0FastObsEmitter().emit_prefetched(
            world_conn=_world_conn(),
            prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            limit=20,
        )

        assert emitted == 2
        assert seen == ["Tokyo"]

    def test_committed_event_evaluation_skips_only_ledgered_publications(self):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        city = _tokyo()
        report = _report("RJTT", t0, 21.0, t_group=False)
        prefetch = fast_obs.FastObsPrefetch(
            eligible=((city, fast_obs.fast_obs_source_for_city(city), "2026-06-10"),),
            reports=(report,),
            freshness_status=fast_obs.FETCH_FRESH,
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
            ledger_reports=(report,),
        )
        emitter = fast_obs.Day0FastObsEmitter()
        conn = _world_conn()
        evaluated: list[tuple[str, str, float]] = []

        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            evaluated_report_keys=evaluated,
            persist_ledger=False,
        ) == 2
        assert evaluated
        assert emitter.prefetched_events_evaluated(prefetch) is False

        conn.commit()
        emitter.mark_prefetched_events_evaluated(evaluated)
        assert emitter.prefetched_events_evaluated(prefetch) is True

        assert emitter.persist_prefetched_ledger(
            world_conn=conn,
            prefetch=prefetch,
        ) is True
        assert emitter.prefetched_events_evaluated(prefetch) is False

    def test_deferred_memos_do_not_advance_before_commit(self):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        city = _tokyo()
        report = _report("RJTT", t0, 21.0, t_group=False)
        prefetch = fast_obs.FastObsPrefetch(
            eligible=((city, fast_obs.fast_obs_source_for_city(city), "2026-06-10"),),
            reports=(report,),
            freshness_status=fast_obs.FETCH_FRESH,
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
            ledger_reports=(report,),
        )
        emitter = fast_obs.Day0FastObsEmitter()
        updates = {}

        assert emitter.emit_prefetched(
            world_conn=_world_conn(),
            prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            deferred_memo_updates=updates,
            persist_ledger=False,
        ) == 2
        high_key = ("Tokyo", "2026-06-10", "high")
        low_key = ("Tokyo", "2026-06-10", "low")
        assert high_key not in emitter._last_live_emitted_rounded
        assert low_key not in emitter._last_live_emitted_rounded
        assert updates == {
            high_key: (21, 21, t0.isoformat()),
            low_key: (21, 21, t0.isoformat()),
        }

        emitter.apply_memo_updates(updates)

        assert emitter._last_live_emitted_rounded[high_key] == 21
        assert emitter._last_live_emitted_rounded[low_key] == 21
        assert emitter._last_live_emitted_observation_time[high_key] == t0.isoformat()
        assert emitter._last_live_emitted_observation_time[low_key] == t0.isoformat()

    def test_plateau_new_observation_version_emits_once(self):
        """Equal extreme plus later source time shrinks the remaining window."""

        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        t1 = t0 + timedelta(minutes=30)
        city = _tokyo()
        emitter = fast_obs.Day0FastObsEmitter()
        conn = _world_conn()

        def prefetch(at):
            report = _report("RJTT", at, 21.0, t_group=False)
            return fast_obs.FastObsPrefetch(
                eligible=((city, fast_obs.fast_obs_source_for_city(city), "2026-06-10"),),
                reports=(report,),
                freshness_status=fast_obs.FETCH_FRESH,
                cache_age_s=0.0,
                decision_time=at + timedelta(minutes=5),
                ledger_reports=(report,),
            )

        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=prefetch(t0),
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            persist_ledger=False,
        ) == 2
        conn.commit()

        later = prefetch(t1)
        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=later,
            received_at=(t1 + timedelta(minutes=5)).isoformat(),
            persist_ledger=False,
        ) == 2
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0] == 4

        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=later,
            received_at=(t1 + timedelta(minutes=6)).isoformat(),
            persist_ledger=False,
        ) == 0

    @pytest.mark.parametrize("older_day0_rows", [0, 10000])
    def test_event_memo_watermark_ignores_non_day0_appends(self, older_day0_rows):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        city = _tokyo()
        report = _report("RJTT", t0, 21.0, t_group=False)
        eligible = ((city, fast_obs.fast_obs_source_for_city(city), "2026-06-10"),)
        prefetch = fast_obs.FastObsPrefetch(
            eligible=eligible,
            reports=(report,),
            freshness_status=fast_obs.FETCH_FRESH,
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
        )
        conn = _world_conn()
        writer = fast_obs.Day0FastObsEmitter()
        assert writer.emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
        ) == 2
        conn.commit()

        conn.executemany(
            """
            INSERT INTO opportunity_events (
                event_id, event_type, entity_key, source,
                observed_at, available_at, received_at,
                causal_snapshot_id, payload_hash, idempotency_key,
                priority, expires_at, payload_json, schema_version, created_at
            )
            SELECT ?, event_type, entity_key, source,
                   observed_at, available_at, received_at,
                   causal_snapshot_id, ?, ?, priority, expires_at,
                   payload_json, schema_version, created_at
              FROM opportunity_events WHERE rowid = 1
            """,
            ((f"old-{i}", f"old-hash-{i}", f"old-key-{i}")
             for i in range(older_day0_rows)),
        )
        conn.commit()
        fresh = fast_obs.Day0FastObsEmitter()
        assert fresh.hydrate_event_memos_from_events(conn, eligible) == 2
        conn.execute(
            """
            INSERT INTO opportunity_events (
                event_id, event_type, entity_key, source,
                observed_at, available_at, received_at,
                causal_snapshot_id, payload_hash, idempotency_key,
                priority, expires_at, payload_json, schema_version, created_at
            )
            SELECT 'non-day0-row', 'BOOK_SNAPSHOT', entity_key, source,
                   observed_at, available_at, received_at,
                   causal_snapshot_id, 'non-day0-hash', 'non-day0-key',
                   priority, expires_at, payload_json, schema_version, created_at
              FROM opportunity_events
             WHERE event_type = 'DAY0_EXTREME_UPDATED'
             LIMIT 1
            """
        )
        conn.commit()
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        vm_blocks = []
        conn.set_progress_handler(lambda: (vm_blocks.append(1) or 0), 100)
        try:
            assert fresh.hydrate_event_memos_from_events(conn, eligible) == 0
        finally:
            conn.set_progress_handler(None, 0)
        assert not any("GROUP BY json_extract" in sql for sql in statements)
        # The one-row suffix must not scan the already-consumed DAY0 archive.
        assert len(vm_blocks) < 100


    def test_emit_prefetched_persists_anomaly_actions_with_world_conn(self, monkeypatch):
        from src.data import day0_oracle_anomaly as oa
        from src.data.day0_fast_obs import Day0FastObsEmitter, FastObsPrefetch
        from src.state import db_writer_lock

        def forbidden_lock(*_args, **_kwargs):
            raise AssertionError("emit-phase anomaly persistence must use world_conn")

        monkeypatch.setattr(db_writer_lock, "db_writer_lock", forbidden_lock)
        conn = _world_conn()
        action = oa.Day0OracleAnomalyAction(
            action="flag",
            city="Tokyo",
            target_date="2026-06-10",
            detail="paris-class",
        )
        prefetch = FastObsPrefetch(
            eligible=(),
            reports=(),
            freshness_status="fresh_fetch",
            cache_age_s=0.0,
            decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            anomaly_actions=(action,),
        )

        emitted = Day0FastObsEmitter().emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at="2026-06-10T04:00:00+00:00",
        )

        assert emitted == 0
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo",
            "2026-06-10",
            now=datetime(2026, 6, 10, 5, 0, tzinfo=UTC),
            conn=conn,
        ) is True

    def test_prefetch_anomaly_check_returns_action_without_writer_lock(self, monkeypatch):
        from src.data import day0_oracle_anomaly as oa
        from src.data.day0_fast_obs import Day0FastObsEmitter
        from src.state import db_writer_lock

        def forbidden_lock(*_args, **_kwargs):
            raise AssertionError("prefetch-phase anomaly check must not acquire a writer lock")

        def wu_obs(city, target_date=None, **kw):
            return SimpleNamespace(
                source="wu_api",
                coverage_status="OK",
                observation_time="2026-06-09T15:05:00+00:00",
                high_so_far=26.0,
                low_so_far=21.0,
            )

        monkeypatch.setattr(db_writer_lock, "db_writer_lock", forbidden_lock)
        monkeypatch.setattr("src.data.observation_client.get_live_wu_observation", wu_obs)

        t0 = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)  # Jun 10 00:00 JST
        reports = [
            _report("RJTT", t0, 21.0, t_group=False),
            _report("RJTT", t0 + timedelta(minutes=5), 21.0, t_group=False),
        ]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)

        prefetch = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=10),
            anomaly_check=oa.wu_metar_anomaly_check,
        )

        assert len(prefetch.anomaly_actions) == 1
        action = prefetch.anomaly_actions[0]
        assert action.action == "flag"
        assert action.city == "Tokyo"
        assert action.target_date == "2026-06-10"
        assert oa.is_day0_family_paused(
            "Tokyo",
            "2026-06-10",
            now=datetime(2026, 6, 9, 15, 15, tzinfo=UTC),
            conn=sqlite3.connect(":memory:"),
        ) is True

        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo",
            "2026-06-10",
            now=datetime(2026, 6, 9, 15, 15, tzinfo=UTC),
            conn=sqlite3.connect(":memory:"),
        ) is False, "prefetch must not make restart durability depend on a standalone write"

    def test_reactor_does_not_duplicate_source_clock_metar_fetch(self):
        """The data-ingest source clock exclusively owns fast METAR HTTP.

        Reactor Day0 emission is durable-state catch-up only. Open-Meteo
        hourly-vector refresh remains an independent scheduler job.

        Pin home: the EDLI reactor body moved from src/main.py to
        src/events/reactor.py (R4-b2 slimming + 57c426dc3); the scheduler
        job id stays in src/main.py."""
        source = open("src/events/reactor.py", encoding="utf-8").read()
        main_source = open("src/main.py", encoding="utf-8").read()
        assert "_edli_prefetch_day0_fast_obs" not in source

        start = source.index("def _edli_emit_day0_extreme_events(")
        end = source.index("def _edli_day0_settlement_semantics(")
        emit_body = source[start:end]
        for forbidden in (
            "emit_events(",
            "emit_prefetched(",
            "get_fast_obs_emitter",
            "httpx",
            "maybe_refresh_day0_hourly_vectors",
            ".prefetch(",
        ):
            assert forbidden not in emit_body, f"write phase must not contain {forbidden!r}"

        import inspect

        from src.events import reactor as reactor_module

        hourly_src = inspect.getsource(reactor_module.run_edli_day0_hourly_refresh_cycle)
        assert "maybe_refresh_day0_hourly_vectors" in hourly_src
        assert 'id="edli_day0_hourly_refresh"' in main_source

    def test_hourly_refresh_reads_latest_auction_day0_gaps(self, monkeypatch, tmp_path):
        from src.events import reactor as reactor_module
        from src.events.candidate_binding import weather_family_id

        decision_time = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)
        london = _london()
        target_date = decision_time.astimezone(
            ZoneInfo(london.timezone)
        ).date().isoformat()
        blocked_id = weather_family_id(
            city="London",
            target_date=target_date,
            metric="high",
        )
        db_path = tmp_path / "trades.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE decision_log (id INTEGER PRIMARY KEY, mode TEXT, artifact_json TEXT)"
        )
        conn.execute(
            "INSERT INTO decision_log (id, mode, artifact_json) VALUES (?, ?, ?)",
            (
                1,
                "global_single_order_auction_delta",
                json.dumps(
                    {
                        "summary": {
                            "probability_ineligible_by_family": {
                                blocked_id: "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:"
                                "FamilyAuthorityUnavailable:"
                                "DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE"
                            }
                        }
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(
            "src.state.db._zeus_trade_db_path",
            lambda: db_path,
        )

        assert reactor_module._edli_latest_day0_hourly_blocked_families(
            cities=[_tokyo(), london],
            decision_time=decision_time,
        ) == {("London", target_date, "high")}

    def test_hourly_refresh_keeps_held_day0_truth_fresh_while_trading(
        self, monkeypatch, tmp_path
    ):
        import src.data.day0_hourly_vectors as vectors_module
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker, PRIORITY_DAILY_LIMIT
        from src.events import reactor as reactor_module

        tokyo = _tokyo()
        db_path, target_date = _install_scheduler_forecast_db(
            monkeypatch, tmp_path, tokyo, authorized_fact=False
        )
        tracker = OpenMeteoQuotaTracker()
        tracker._count = PRIORITY_DAILY_LIMIT
        fetches = []
        monkeypatch.setattr(vectors_module, "quota_tracker", tracker)
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: {("Tokyo", target_date, "high")},
        )
        monkeypatch.setattr(
            vectors_module,
            "fetch_day0_hourly_vectors",
            lambda city, *, models, now, **_kw: (
                fetches.append((city.name, tuple(models)))
                or [_scheduler_hourly_vector(city, model, now) for model in models],
                "sha256:held-critical",
            ),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        assert [name for name, _models in fetches] == ["Tokyo"]
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM day0_hourly_vectors WHERE model NOT LIKE 'ecmwf_ifs025_member%'").fetchone()[0] == 6
        conn.close()

    def test_hourly_refresh_reserves_strict_bundle_priority_slot_while_trading(
        self, monkeypatch, tmp_path
    ):
        import src.data.day0_hourly_vectors as vectors_module
        from src.data.openmeteo_quota import MAINTENANCE_DAILY_LIMIT, OpenMeteoQuotaTracker
        from src.events import reactor as reactor_module

        tokyo = _tokyo()
        db_path, target_date = _install_scheduler_forecast_db(
            monkeypatch, tmp_path, tokyo, authorized_fact=True
        )
        conn = sqlite3.connect(db_path)
        conn.execute(vectors_module._TABLE_DDL)
        conn.execute(vectors_module._INDEX_DDL)
        conn.commit()
        conn.close()
        tracker = OpenMeteoQuotaTracker()
        tracker._count = MAINTENANCE_DAILY_LIMIT
        clock = {"now": 60.0}
        fetches = {"count": 0, "complete": False}
        monkeypatch.setattr(vectors_module, "quota_tracker", tracker)
        monkeypatch.setattr(vectors_module.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: set(),
        )

        def fetch(city, *, models, now, **_kw):
            fetches["count"] += 1
            omitted = None
            if not fetches["complete"]:
                omitted = f"{target_date}T23:00"
            return (
                [
                    _scheduler_hourly_vector(
                        city,
                        model,
                        now,
                        omit_local_time=omitted if index == 0 else None,
                    )
                    for index, model in enumerate(models)
                ],
                "sha256:scheduler-priority",
            )

        monkeypatch.setattr(vectors_module, "fetch_day0_hourly_vectors", fetch)

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM day0_hourly_vectors WHERE model NOT LIKE 'ecmwf_ifs025_member%' AND target_date = ?", (target_date,)).fetchone()[0] == 0
        conn.close()
        refresh_key = f"Tokyo|{target_date}|ens={target_date}"
        assert vectors_module._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] == (
            clock["now"] + vectors_module.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
        )

        fetches["complete"] = True
        clock["now"] += vectors_module.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        conn = sqlite3.connect(db_path)
        # Tomorrow's complete bundle may already have committed on the first
        # attempt. Append-only recapture is another version, not another model.
        assert conn.execute("SELECT COUNT(DISTINCT model || '|' || target_date) FROM day0_hourly_vectors WHERE model NOT LIKE 'ecmwf_ifs025_member%'").fetchone()[0] == 6
        conn.close()
        probe = reactor_module._edli_day0_hourly_refresh_due_families(
            cities=[tokyo], decision_time=datetime.now(UTC)
        )
        assert probe.proved is True
        assert probe.refresh_due_families == frozenset()
        assert refresh_key not in vectors_module._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)
        assert fetches["count"] == 2

    @pytest.mark.parametrize("failure", ["runtime_cities_by_name", "priority_families"])
    def test_hourly_refresh_priority_probe_failure_preserves_maintenance_sweep(
        self, monkeypatch, tmp_path, failure
    ):
        import src.config as config_module
        import src.data.day0_hourly_vectors as vectors_module
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker
        from src.events import reactor as reactor_module

        tokyo = _tokyo()
        db_path, _target_date = _install_scheduler_forecast_db(
            monkeypatch, tmp_path, tokyo, authorized_fact=False
        )
        if failure == "runtime_cities_by_name":
            monkeypatch.setattr(
                config_module,
                "runtime_cities_by_name",
                lambda: (_ for _ in ()).throw(
                    RuntimeError("runtime city map unavailable")
                ),
            )
        else:
            monkeypatch.setattr(
                reactor_module,
                "_edli_day0_hourly_priority_families",
                lambda **_kw: (_ for _ in ()).throw(
                    RuntimeError("priority family probe unavailable")
                ),
            )
        monkeypatch.setattr(vectors_module, "quota_tracker", OpenMeteoQuotaTracker())
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: set(),
        )
        fetches = []
        monkeypatch.setattr(
            vectors_module,
            "fetch_day0_hourly_vectors",
            lambda city, *, models, now, **_kw: (
                fetches.append(city.name)
                or [_scheduler_hourly_vector(city, model, now) for model in models],
                "sha256:maintenance-fallback",
            ),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        assert fetches == ["Tokyo"]
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM day0_hourly_vectors WHERE model NOT LIKE 'ecmwf_ifs025_member%'").fetchone()[0] == 6
        conn.close()

    def test_hourly_refresh_cursor_advances_across_full_held_segment_when_throttled(
        self, monkeypatch
    ):
        """A throttled microbatch cannot starve held cities beyond its first page."""
        import src.config as config_module
        import src.main  # load settings consumers before replacing the config singleton
        from src.events import reactor as reactor_module

        cities = [
            SimpleNamespace(name=name, timezone="UTC")
            for name in ("A", "B", "C", "D", "E")
        ]
        target_date = datetime.now(UTC).date().isoformat()
        held = {(city.name, target_date, "high") for city in cities}
        calls = []

        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(_data={"edli": {"enabled": True}}),
        )
        monkeypatch.setattr(config_module, "runtime_cities", lambda: cities)
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: held,
        )
        monkeypatch.setattr(
            reactor_module,
            "_edli_day0_hourly_priority_families",
            lambda **_: sorted(held),
        )
        monkeypatch.setattr(reactor_module, "_DAY0_HOURLY_REFRESH_CURSOR", 0)
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", "3")
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_PRIORITY_CITY_CAP", "3")
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.maybe_refresh_day0_hourly_vectors",
            lambda selected, **_kwargs: calls.append(
                [city.name for city in selected]
            )
            or SimpleNamespace(
                vectors_written=0,
                cities_attempted=0,
                cities_skipped_throttle=len(selected),
                cities_skipped_quota=0,
                incomplete_expected_bundles=0,
                budget_exhausted=False,
            ),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)
        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        # Every held city is offered every cycle; the fetcher skips throttled
        # cities without spending a slot, and the cursor rotates the order.
        assert calls == [["A", "B", "C", "D", "E"], ["B", "C", "D", "E", "A"]]
        assert reactor_module._DAY0_HOURLY_REFRESH_CURSOR == 2

    def test_hourly_refresh_due_held_bundle_preserves_discovery_progress(
        self, monkeypatch
    ):
        """One failed held bundle cannot starve every discovery probability."""
        import src.config as config_module
        import src.main  # load settings consumers before replacing the config singleton
        from src.events import reactor as reactor_module

        cities = [
            SimpleNamespace(name=name, timezone="UTC")
            for name in ("A", "B", "C", "D", "E", "F", "G", "H")
        ]
        target_date = datetime.now(UTC).date().isoformat()
        held = {(name, target_date, "high") for name in ("A", "B", "C", "D", "E")}
        discovery = {
            (name, target_date, "high") for name in ("F", "G", "H")
        }
        calls = []

        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(_data={"edli": {"enabled": True}}),
        )
        monkeypatch.setattr(config_module, "runtime_cities", lambda: cities)
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: held,
        )
        monkeypatch.setattr(
            reactor_module,
            "_edli_day0_hourly_refresh_due_families",
            lambda **_kwargs: reactor_module._Day0HourlyPriorityProbe(
                refresh_due_families=frozenset(held | discovery),
                proved=True,
            ),
        )
        monkeypatch.setattr(reactor_module, "_DAY0_HOURLY_REFRESH_CURSOR", 0)
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", "3")
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_PRIORITY_CITY_CAP", "3")
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.maybe_refresh_day0_hourly_vectors",
            lambda selected, **kwargs: calls.append(
                {
                    "selected": [city.name for city in selected],
                    "critical": kwargs["quota_critical_cities"],
                    "priority": kwargs["quota_priority_cities"],
                    "critical_attempts": kwargs["critical_max_attempts"],
                }
            )
            or SimpleNamespace(
                vectors_written=0,
                cities_attempted=1,
                cities_skipped_throttle=0,
                cities_skipped_quota=0,
                incomplete_expected_bundles=1,
                budget_exhausted=True,
            ),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)
        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        # Due held capital keeps max_cities - 1 attempts; discovery keeps the
        # rest, and both segments rotate.
        assert calls == [
            {
                "selected": ["A", "B", "C", "D", "E", "F", "G", "H"],
                "critical": 5, "priority": 3, "critical_attempts": 2,
            },
            {
                "selected": ["B", "C", "D", "E", "A", "G", "H", "F"],
                "critical": 5, "priority": 3, "critical_attempts": 2,
            },
        ]

    @pytest.mark.parametrize("held_due", (False, True))
    def test_hourly_refresh_throttled_held_page_cannot_starve_due_cities(
        self, monkeypatch, held_due
    ):
        """Only due cities consume fetch slots (2026-10-04: 291/453 empty cycles).

        Six same-day held cities sit inside the refresh interval.  The real
        fetcher throttle skips them, so the cycle's attempts reach the due
        discovery cities instead of ending on an all-throttled held page.
        A held city that is itself due is still fetched first.
        """
        import src.config as config_module
        import src.data.day0_hourly_vectors as vectors_module
        import src.main  # load settings consumers before replacing the config singleton
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker
        from src.events import reactor as reactor_module

        held_names = ("H1", "H2", "H3", "H4", "H5", "H6")
        due_names = ("Toronto", "Warsaw", "Wellington")
        cities = [
            SimpleNamespace(name=name, timezone="UTC")
            for name in (*held_names, *due_names)
        ]
        target_date = datetime.now(UTC).date().isoformat()
        held = {(name, target_date, "low") for name in held_names}
        due = {(name, target_date, "low") for name in due_names}
        if held_due:
            due.add(("H6", target_date, "low"))
        fetched = []

        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(_data={"edli": {"enabled": True}}),
        )
        monkeypatch.setattr(config_module, "runtime_cities", lambda: cities)
        monkeypatch.setattr(
            reactor_module, "_edli_current_held_position_family_keys", lambda: held
        )
        monkeypatch.setattr(
            reactor_module,
            "_edli_day0_hourly_refresh_due_families",
            lambda **_kwargs: reactor_module._Day0HourlyPriorityProbe(
                refresh_due_families=frozenset(due), proved=True,
            ),
        )
        monkeypatch.setattr(reactor_module, "_DAY0_HOURLY_REFRESH_CURSOR", 0)
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", "3")
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_PRIORITY_CITY_CAP", "3")
        monkeypatch.setattr(vectors_module, "quota_tracker", OpenMeteoQuotaTracker())
        monkeypatch.setattr(
            vectors_module, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"]
        )
        monkeypatch.setattr(
            vectors_module, "day0_source_clock_ensemble_target_dates", lambda **_kw: ()
        )
        monkeypatch.setattr(
            vectors_module,
            "fetch_day0_hourly_vectors",
            lambda city, **_kw: (fetched.append(city.name) or [], ""),
        )
        now = vectors_module.time.monotonic()
        with vectors_module._REFRESH_LOCK:
            vectors_module._LAST_REFRESH_MONOTONIC.clear()
            vectors_module._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
            vectors_module._INCOMPLETE_RETRY_STREAK.clear()
            for name in held_names:
                if held_due and name == "H6":
                    continue
                vectors_module._LAST_REFRESH_MONOTONIC[f"{name}|{target_date}"] = now

        reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        if held_due:
            # The due held city leads; discovery spends the remaining slots.
            assert fetched == ["H6", "Toronto", "Warsaw"]
        else:
            assert fetched == ["Toronto", "Warsaw", "Wellington"]

    @staticmethod
    def _install_probe_universe(monkeypatch, tmp_path, names, *, slow_per_city_s):
        """Real due-probe over a private vector DB; facts and the clock are fixed.

        Each city reads one authorized fact; the strict per-city read costs
        ``slow_per_city_s`` of a fake monotonic clock, as live strict reads do.
        """
        import src.config as config_module
        import src.data.day0_hourly_vectors as vectors_module
        import src.data.replacement_forecast_current_target_plan as target_plan
        import src.state.db as db_module
        from src.events import reactor as reactor_module

        cities = [SimpleNamespace(name=name, timezone="UTC") for name in names]
        db_path = tmp_path / "probe-vectors.db"
        conn = sqlite3.connect(db_path)
        conn.execute(vectors_module._TABLE_DDL)
        conn.execute(vectors_module._INDEX_DDL)
        conn.commit()
        conn.close()
        clock = {"now": 0.0}
        strict_reads = []

        def connect(**_kwargs):
            return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        def strict_read(*, city, **_kwargs):
            strict_reads.append(city)
            clock["now"] += slow_per_city_s
            return []

        monkeypatch.setattr(reactor_module.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(config_module, "runtime_cities", lambda: cities)
        monkeypatch.setattr(
            config_module, "runtime_cities_by_name", lambda: {c.name: c for c in cities}
        )
        monkeypatch.setattr(db_module, "get_world_connection_read_only", connect)
        monkeypatch.setattr(db_module, "get_forecasts_connection_read_only", connect)
        monkeypatch.setattr(
            vectors_module, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"]
        )
        monkeypatch.setattr(
            vectors_module, "day0_source_clock_ensemble_target_dates", lambda **_kw: ()
        )
        monkeypatch.setattr(
            vectors_module, "read_day0_current_temperature_state", lambda **_kw: None
        )
        monkeypatch.setattr(vectors_module, "read_freshest_day0_hourly_vectors", strict_read)
        monkeypatch.setattr(
            target_plan,
            "_latest_authorized_day0_fact",
            lambda *_a, temperature_metric, decision_time, **_kw: (
                {"observation_time": (decision_time - timedelta(minutes=5)).isoformat()}
                if temperature_metric == "high" else None
            ),
        )
        return cities, db_path, clock, strict_reads

    def test_hourly_refresh_probe_reaches_alphabetical_tail_in_bounded_cycles(
        self, monkeypatch, tmp_path
    ):
        """A deadline-bounded due-probe resumes where it stopped.

        2026-10-04: the probe always restarted at the first city, its budget
        expired after the same alphabetical prefix, and the 18 cities with
        <60% bundle freshness were all index >= 26.  Every city must now be
        proven due within ceil(N / cities-per-cycle) cycles.
        """
        import src.data.day0_hourly_vectors as vectors_module
        import src.main  # noqa: F401 -- load settings consumers first
        from src.events import reactor as reactor_module

        names = [f"C{index:02d}" for index in range(12)]
        _cities, db_path, _clock, _reads = self._install_probe_universe(
            monkeypatch, tmp_path, names, slow_per_city_s=0.5,
        )
        # A fresh rolling capture per city forces the strict read (0.5 s each):
        # the 2 s preflight budget completes three cities per cycle (the
        # fourth expires mid-city and is rescanned next cycle).
        now = datetime.now(UTC)
        conn = sqlite3.connect(db_path)
        for name in names:
            conn.execute(
                "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
                "timezone_name, captured_at, endpoint, request_hash, times_json, "
                "temps_c_json) VALUES (?, 'ecmwf_ifs', ?, ?, 'UTC', ?, 'e', 'h', '[]', '[]')",
                (f"v-{name}", name, now.date().isoformat(), now.isoformat()),
            )
        conn.commit()
        conn.close()
        monkeypatch.setattr(reactor_module, "_edli_current_held_position_family_keys", lambda: set())
        monkeypatch.setattr(reactor_module, "_DAY0_HOURLY_REFRESH_CURSOR", 0)
        monkeypatch.setattr(reactor_module, "_DAY0_HOURLY_PROBE_CURSOR", 0)
        monkeypatch.setattr(reactor_module, "_day0_hourly_refresh_budget_seconds", lambda: 6.0)
        monkeypatch.setattr(reactor_module, "_day0_hourly_fetch_timeout_seconds", lambda: 4.0)
        proved_due = set()
        real_probe = reactor_module._edli_day0_hourly_refresh_due_families

        def probe(**kwargs):
            result = real_probe(**kwargs)
            proved_due.update(city for city, _date, _metric in result.refresh_due_families)
            return result

        monkeypatch.setattr(reactor_module, "_edli_day0_hourly_refresh_due_families", probe)
        monkeypatch.setattr(
            vectors_module,
            "maybe_refresh_day0_hourly_vectors",
            lambda *_a, **_kw: SimpleNamespace(
                vectors_written=0, cities_attempted=0, cities_skipped_throttle=0,
                cities_skipped_quota=0, incomplete_expected_bundles=0,
                budget_exhausted=False,
            ),
        )

        for _cycle in range(4):  # ceil(12 cities / 3 completed per cycle)
            reactor_module.run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)

        assert proved_due == set(names)

    def test_hourly_refresh_probe_isolates_one_city_fact_refusal(
        self, monkeypatch, tmp_path
    ):
        """One city's refused Day0 fact leaves every other city's verdict.

        2026-10-06 03Z: two RKSI reports with different temperatures for one
        observation time made Seoul's fact read raise KmaObservationConflict,
        the probe aborted unproved on every pass, and Day0 ENS carriers stopped
        refreshing fleet-wide (24 HIGH families without probability).
        """
        import src.data.replacement_forecast_current_target_plan as target_plan
        from src.data.day0_fast_obs import KmaObservationConflict
        from src.events import reactor as reactor_module

        names = ["Paris", "Seoul", "Tokyo"]
        cities, _db_path, _clock, _reads = self._install_probe_universe(
            monkeypatch, tmp_path, names, slow_per_city_s=0.0,
        )

        def fact(_conn, *, city, temperature_metric, decision_time, **_kw):
            if city == "Seoul":
                raise KmaObservationConflict("conflicting observations: RKSI")
            if temperature_metric != "high":
                return None
            return {"observation_time": (decision_time - timedelta(minutes=5)).isoformat()}

        monkeypatch.setattr(target_plan, "_latest_authorized_day0_fact", fact)
        now = datetime.now(UTC)

        probe = reactor_module._edli_day0_hourly_refresh_due_families(
            cities=cities, decision_time=now,
        )

        target_date = now.date().isoformat()
        assert probe.proved is True
        assert probe.cities_scanned == 3
        assert probe.refresh_due_families == frozenset(
            (name, target_date, "high") for name in ("Paris", "Tokyo")
        )

    def test_hourly_refresh_probe_stale_capture_skips_strict_reads(
        self, monkeypatch, tmp_path
    ):
        """One indexed seek proves a city with no current rolling capture due.

        The strict bundle read cannot succeed without a rolling row inside the
        producer horizon, so the probe must not spend ~33 ms per city on it.
        """
        from src.events import reactor as reactor_module

        names = ["Toronto", "Warsaw", "Wellington"]
        cities, db_path, _clock, strict_reads = self._install_probe_universe(
            monkeypatch, tmp_path, names, slow_per_city_s=0.0,
        )
        now = datetime.now(UTC)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
            "timezone_name, captured_at, endpoint, request_hash, times_json, "
            "temps_c_json) VALUES ('v1', 'ecmwf_ifs', 'Warsaw', ?, 'UTC', ?, 'e', 'h', '[]', '[]')",
            (now.date().isoformat(), (now - timedelta(hours=2, minutes=5)).isoformat()),
        )
        conn.execute(
            "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
            "timezone_name, captured_at, endpoint, request_hash, times_json, "
            "temps_c_json) VALUES ('v2', 'ecmwf_ifs', 'Wellington', ?, 'UTC', ?, 'e', 'h', '[]', '[]')",
            (now.date().isoformat(), (now - timedelta(minutes=10)).isoformat()),
        )
        conn.commit()
        conn.close()

        probe = reactor_module._edli_day0_hourly_refresh_due_families(
            cities=cities, decision_time=now,
        )

        target_date = now.date().isoformat()
        assert probe.proved is True
        assert probe.cities_scanned == 3
        assert probe.refresh_due_families == frozenset(
            (name, target_date, "high") for name in names
        )
        # Only the city with a current capture reaches the strict read.
        assert strict_reads == ["Wellington"]

    def test_vector_missing_verdict_marks_city_due_until_newer_capture(
        self, monkeypatch, tmp_path
    ):
        """The materializer's VECTOR_MISSING verdict wakes the hourly refresher.

        2026-10-04: 3,061 VECTOR_MISSING blocks (71 families, 23 cities) never
        reached the producer.  The producer's strict read judged a fresher
        bundle at its own clock while the consumer, at its cut, had none: the
        verdict is the authority.  It stays due until a capture newer than the
        verdict's cutoff exists, then resets.
        """
        import src.data.replacement_forecast_live_materialization_queue as queue_mod
        import src.data.replacement_forecast_production as production_mod
        from src.events import reactor as reactor_module

        names = ["Tokyo", "Warsaw"]
        cities, db_path, _clock, strict_reads = self._install_probe_universe(
            monkeypatch, tmp_path, names, slow_per_city_s=0.0,
        )
        now = datetime.now(UTC)
        target_date = now.date().isoformat()
        verdict_cutoff = now - timedelta(minutes=30)
        processed = tmp_path / "live" / "processed"
        processed.mkdir(parents=True)
        monkeypatch.setattr(
            production_mod,
            "_replacement_forecast_live_materialization_queue_config",
            lambda: {"processed_dir": processed},
        )
        # A current rolling capture for both cities, older than the verdict.
        conn = sqlite3.connect(db_path)
        for name in names:
            conn.execute(
                "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
                "timezone_name, captured_at, endpoint, request_hash, times_json, "
                "temps_c_json) VALUES (?, 'ecmwf_ifs', ?, ?, 'UTC', ?, 'e', 'h', '[]', '[]')",
                (f"old-{name}", name, target_date,
                 (verdict_cutoff - timedelta(minutes=20)).isoformat()),
            )
        conn.commit()
        conn.close()
        # The strict read is satisfied at the producer clock: without the
        # verdict nothing is due.
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kwargs: strict_reads.append(kwargs["city"]) or [object()],
        )
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.day0_conditional_high_run_proof",
            lambda *_a, **_kw: None,
        )
        request = tmp_path / "live" / "requests" / "tokyo.json"
        request.parent.mkdir(parents=True)
        request.write_text("{}")
        queue_mod._record_latest_terminal_request(
            request,
            processed_path=processed,
            request_payload={
                "city": "Tokyo", "target_date": target_date,
                "temperature_metric": "low",
                "computed_at": verdict_cutoff.isoformat(),
            },
            receipt_dir_name="blocked_latest",
            status="BLOCKED_MISSING_PROBABILITY_AUTHORITY",
            reason_codes=(
                queue_mod._BLOCKED_INPUT_RECEIPT_REASON,
                queue_mod._DAY0_CARRIER_VECTOR_MISSING_REASON,
            ),
        )

        def probe(decision_time=now):
            return reactor_module._edli_day0_hourly_refresh_due_families(
                cities=cities, decision_time=decision_time,
            )

        def capture(vector_id, captured_at):
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
                "timezone_name, captured_at, endpoint, request_hash, times_json, "
                "temps_c_json) VALUES (?, 'ecmwf_ifs', 'Tokyo', ?, 'UTC', ?, "
                "'e', 'h', '[]', '[]')",
                (vector_id, target_date, captured_at.isoformat()),
            )
            conn.commit()
            conn.close()

        due = probe()
        assert due.proved is True
        assert due.refresh_due_families == frozenset({("Tokyo", target_date, "low")})

        # A capture after the request cut but before the verdict was written
        # was already seen by the refusing run (its clock lifts past
        # computed_at); it cannot clear the debt.
        capture("seen-Tokyo", verdict_cutoff + timedelta(minutes=5))
        assert probe().refresh_due_families == frozenset({("Tokyo", target_date, "low")})

        recorded_at = datetime.fromisoformat(
            json.loads(
                (tmp_path / "live" / "blocked_latest"
                 / f"Tokyo.{target_date}.low.json").read_text()
            )["recorded_at"]
        )
        capture("new-Tokyo", recorded_at + timedelta(seconds=1))
        assert probe(recorded_at + timedelta(seconds=2)).refresh_due_families == frozenset()

    def test_hourly_refresh_preserves_full_missing_authority_priority_prefix(
        self, monkeypatch
    ):
        """A throttled front page cannot demote later authority gaps."""
        import src.config as config_module
        import src.main  # load settings consumers before replacing the config singleton
        from src.events import reactor as reactor_module

        cities = [
            SimpleNamespace(name=name, timezone="UTC")
            for name in ("A", "B", "C", "D", "E")
        ]
        target_date = datetime.now(UTC).date().isoformat()
        missing = frozenset(
            (city.name, target_date, "high") for city in cities
        )
        calls = []

        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(_data={"edli": {"enabled": True}}),
        )
        monkeypatch.setattr(config_module, "runtime_cities", lambda: cities)
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: set(),
        )
        monkeypatch.setattr(
            reactor_module,
            "_edli_day0_hourly_refresh_due_families",
            lambda **_kwargs: reactor_module._Day0HourlyPriorityProbe(
                refresh_due_families=missing,
                proved=True,
            ),
        )
        monkeypatch.setattr(reactor_module, "_DAY0_HOURLY_REFRESH_CURSOR", 0)
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", "3")
        monkeypatch.setenv("ZEUS_DAY0_HOURLY_REFRESH_PRIORITY_CITY_CAP", "3")
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.maybe_refresh_day0_hourly_vectors",
            lambda selected, **kwargs: calls.append(
                {
                    "selected": [city.name for city in selected],
                    "max_cities": kwargs["max_cities"],
                    "priority_prefix": kwargs["quota_priority_cities"],
                }
            )
            or SimpleNamespace(
                vectors_written=0,
                cities_attempted=3,
                cities_skipped_throttle=2,
                cities_skipped_quota=0,
                incomplete_expected_bundles=1,
                budget_exhausted=False,
            ),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(
            trading_lane_active=False
        )

        assert calls == [
            {
                "selected": ["A", "B", "C", "D", "E"],
                "max_cities": 3,
                "priority_prefix": 5,
            }
        ]

    def test_hourly_refresh_defers_unprioritized_universe_while_trading(
        self, monkeypatch, tmp_path
    ):
        from src.events import reactor as reactor_module

        tokyo = _tokyo()
        _install_scheduler_forecast_db(
            monkeypatch, tmp_path, tokyo, authorized_fact=False
        )
        monkeypatch.setattr(
            reactor_module,
            "_edli_current_held_position_family_keys",
            lambda: set(),
        )
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.maybe_refresh_day0_hourly_vectors",
            lambda *_args, **_kwargs: pytest.fail("unprioritized universe must remain deferred"),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(
            trading_lane_active=True,
        )

    def test_hourly_refresh_observes_trading_lanes_without_owning_them(self):
        source = open("src/main.py", encoding="utf-8").read()
        hook_start = source.index('@_scheduler_job("edli_day0_hourly_refresh")')
        hook_end = source.index("def _edli_is_sqlite_lock_error", hook_start)
        hook = source[hook_start:hook_end]
        assert "_held_position_monitor_active.is_set()" in hook
        assert "_held_position_monitor_canonical_debt.is_set()" in hook
        assert "_edli_redecision_screen_lock.locked()" in hook
        assert "_edli_reactor_active_lock.locked()" in hook
        assert "_edli_reactor_active_lock.acquire" not in hook
        assert "_edli_reactor_active_lock.release" not in hook

        schedule_at = source.index('id="edli_day0_hourly_refresh"')
        schedule = source[schedule_at - 500 : schedule_at + 500]
        assert "OPENING_HUNT_FIRST_DELAY_SECONDS + 36.0" in schedule

    def test_hourly_refresh_reduces_work_while_held_monitor_is_active(self, monkeypatch):
        import threading

        import src.main as main
        from src.events import reactor as reactor_module

        calls = []
        monitor = threading.Event()
        monitor.set()
        monkeypatch.setattr(main, "_held_position_monitor_active", monitor)
        monkeypatch.setattr(main, "_edli_redecision_screen_lock", threading.Lock())
        monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
        monkeypatch.setattr(
            reactor_module,
            "run_edli_day0_hourly_refresh_cycle",
            lambda **kwargs: calls.append(kwargs),
        )

        main._edli_day0_hourly_refresh_cycle()

        assert calls == [{"trading_lane_active": True}]
        assert main._edli_reactor_active_lock.locked() is False

    def test_blocked_hourly_refresh_never_pins_reactor_lock(self, monkeypatch):
        import threading

        import src.main as main
        from src.events import reactor as reactor_module

        started = threading.Event()
        release = threading.Event()

        def blocked_refresh(**_kwargs):
            started.set()
            assert release.wait(timeout=5.0)

        monkeypatch.setattr(main, "_consume_live_control_commands", lambda: None)
        monkeypatch.setattr(main, "_edli_redecision_screen_lock", threading.Lock())
        monkeypatch.setattr(main, "_edli_reactor_active_lock", threading.Lock())
        monkeypatch.setattr(main, "_held_position_monitor_active", threading.Event())
        monkeypatch.setattr(
            main, "_held_position_monitor_canonical_debt", threading.Event()
        )
        monkeypatch.setattr(
            reactor_module,
            "run_edli_day0_hourly_refresh_cycle",
            blocked_refresh,
        )

        producer = threading.Thread(target=main._edli_day0_hourly_refresh_cycle)
        producer.start()
        assert started.wait(timeout=5.0)

        assert main._edli_reactor_active_lock.acquire(blocking=False)
        main._edli_reactor_active_lock.release()

        release.set()
        producer.join(timeout=5.0)
        assert not producer.is_alive()

    def test_live_family_admission_scopes_market_seek_to_runtime_cities(self, monkeypatch):
        from src.events import reactor as reactor_module

        forecasts = sqlite3.connect(":memory:")
        forecasts.execute(
            """
            CREATE TABLE market_events (
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
            )
            """
        )
        forecasts.execute(
            "CREATE INDEX idx_market_events_city_date_metric "
            "ON market_events(city, target_date, temperature_metric)"
        )
        forecasts.executemany(
            "INSERT INTO market_events VALUES (?, ?, ?)",
            (
                ("Paris", "2026-07-16", "high"),
                ("Paris", "2026-07-16", "high"),
                ("Unconfigured City", "2026-07-16", "high"),
            ),
        )
        trade = sqlite3.connect(":memory:")
        monkeypatch.setattr(reactor_module, "_open_rest_family_rows_for_refresh", lambda _: ())
        monkeypatch.setattr(
            "src.data.replacement_cycle_advance_trigger._held_position_families",
            lambda _: (),
        )
        traced: list[str] = []
        forecasts.set_trace_callback(traced.append)

        admission = reactor_module._edli_day0_live_family_admission(
            forecasts,
            trade,
            decision_time=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )

        assert ("paris", "2026-07-16", "high") in admission.admitted_families
        assert admission.scan_cities == frozenset({"Paris"})
        assert all(family[0] != "unconfigured city" for family in admission.admitted_families)
        assert any("city IN (" in statement for statement in traced)

    def test_publication_clock_missing_denies_live_authority(self):
        """PR#404 P2: receiptTime absent -> available_at falls back to the obs
        valid time (never our wall clock) AND live status is blocked."""
        from src.data.day0_fast_obs import (
            fast_obs_source_for_city,
            fast_obs_to_day0_observation,
            running_extremes_for_local_day,
        )
        from src.data.day0_fast_obs import MetarReport

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = MetarReport(
            station_id="RJTT", obs_time=t0, receipt_time=None,
            temp_c=21.0, metar_type="METAR", raw="METAR RJTT 21/15",
        )
        city = _tokyo()
        ex = running_extremes_for_local_day([report], city=city, target_date="2026-06-10")
        obs = fast_obs_to_day0_observation(
            city=city, extremes=ex, metric="high", source=fast_obs_source_for_city(city),
        )
        assert obs["observation_available_at"] == obs["observation_time"]
        assert obs["live_authority_status"] == "blocked"


# ===========================================================================
# R21 — anomaly pause persistence + WU-check memo discipline (PR#404 P1)
# ===========================================================================

class TestAnomalyPausePersistence:
    """PR#404 P1: a Paris-CDG-class anomaly is a settlement-authority integrity
    event — the pause must survive a daemon restart, and a WU outage must not
    consume the success-check memo."""

    def _flags_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_pause_survives_process_restart(self):
        from src.data import day0_oracle_anomaly as oa

        conn = self._flags_conn()
        oa.flag_day0_oracle_anomaly(
            "Tokyo", "2026-06-10", detail="paris-class",
            now=datetime(2026, 6, 10, 4, 0, tzinfo=UTC), conn=conn,
        )
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 10, 4, 30, tzinfo=UTC), conn=conn,
        ) is True

        # SIMULATED RESTART: in-process registry wiped; durable flags remain.
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC), conn=conn,
        ) is True, "pause must be re-hydrated from the durable world-DB flags"

        # TTL is enforced from the DURABLE flagged_at, even post-restart.
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 12, 5, 0, tzinfo=UTC), conn=conn,
        ) is False

    def test_clear_removes_durable_flag_too(self):
        from src.data import day0_oracle_anomaly as oa

        conn = self._flags_conn()
        oa.flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="t", conn=conn)
        assert oa.clear_day0_oracle_anomaly("Tokyo", "2026-06-10", conn=conn) is True
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10", conn=conn) is False

    def test_persist_failure_is_loud_but_pause_holds_in_process(self):
        from src.data import day0_oracle_anomaly as oa

        class _BrokenConn:
            def execute(self, *a, **kw):
                raise sqlite3.OperationalError("disk full")

        oa.flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="t", conn=_BrokenConn())
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10", conn=_BrokenConn()) is True

    def test_anomaly_flag_uses_blocking_live_writer_lock(self, monkeypatch):
        from contextlib import contextmanager
        from src.data import day0_oracle_anomaly as oa
        from src.state import db as state_db
        from src.state import db_writer_lock

        raw_conn = self._flags_conn()

        class _Conn:
            def execute(self, *args, **kwargs):
                return raw_conn.execute(*args, **kwargs)

            def commit(self):
                return raw_conn.commit()

            def close(self):
                return None

        conn = _Conn()
        calls = []

        @contextmanager
        def _lock(db_path, write_class, *, blocking=True):
            calls.append((db_path, write_class, blocking))
            yield

        monkeypatch.setattr(state_db, "get_world_connection", lambda **_kwargs: conn)
        monkeypatch.setattr(db_writer_lock, "db_writer_lock", _lock)

        oa.flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="t")

        assert calls, "production anomaly flag path must acquire the world writer lock"
        assert calls[-1][1] == db_writer_lock.WriteClass.LIVE
        assert calls[-1][2] is True
        rows = raw_conn.execute(
            "SELECT COUNT(*) FROM day0_oracle_anomaly_flags WHERE city='Tokyo'"
        ).fetchone()[0]
        assert rows == 1

    def test_wu_outage_does_not_consume_success_memo(self, monkeypatch):
        """The old code armed the 10-min memo BEFORE calling WU — an outage
        silenced the cross-check for the full window. Now: failure arms only a
        short retry throttle; the next eligible pass retries WU."""
        from src.data import day0_oracle_anomaly as oa

        calls = {"n": 0}

        def failing_wu(city, target_date=None, **kw):
            calls["n"] += 1
            raise RuntimeError("WU outage")

        monkeypatch.setattr(
            "src.data.observation_client.get_live_wu_observation", failing_wu
        )
        city = _tokyo()
        extremes = SimpleNamespace(target_date="2026-06-10")
        oa.wu_metar_anomaly_check(city, extremes, [])
        assert calls["n"] == 1
        # within the FAILURE retry throttle: no call
        oa.wu_metar_anomaly_check(city, extremes, [])
        assert calls["n"] == 1
        # past the failure throttle (rewind the failure memo), well within what
        # the OLD code would have treated as the consumed 10-min success memo:
        import time as _time

        with oa._WU_CHECK_MEMO_LOCK:
            oa._WU_CHECK_FAILURE_MEMO["Tokyo"] = _time.monotonic() - 200.0
        oa.wu_metar_anomaly_check(city, extremes, [])
        assert calls["n"] == 2, "WU must be retried after the short failure throttle"

    def test_inconclusive_metar_window_does_not_consume_success_memo(self, monkeypatch):
        """WU fetch success is not enough to arm the 10-min success memo. If
        the METAR side cannot cover WU's last observation window, the comparison
        is inconclusive and must retry on the short failure throttle."""
        from src.data import day0_oracle_anomaly as oa

        calls = {"n": 0}

        def wu_obs(city, target_date=None, **kw):
            calls["n"] += 1
            return SimpleNamespace(
                observation_time="2026-06-10T12:00:00+00:00",
                high_so_far=26.0,
                low_so_far=21.0,
            )

        monkeypatch.setattr(
            "src.data.observation_client.get_live_wu_observation", wu_obs
        )
        city = _tokyo()
        extremes = SimpleNamespace(target_date="2026-06-10")
        # METAR window is stale relative to WU's 12:00 observation.
        stale_reports = [
            _report("RJTT", datetime(2026, 6, 10, 9, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 10, 0, tzinfo=UTC), 22.0, t_group=False),
        ]

        oa.wu_metar_anomaly_check(city, extremes, stale_reports)
        assert calls["n"] == 1
        with oa._WU_CHECK_MEMO_LOCK:
            assert "Tokyo" not in oa._WU_CHECK_MEMO
            assert "Tokyo" in oa._WU_CHECK_FAILURE_MEMO

        # Within the short retry throttle: no call.
        oa.wu_metar_anomaly_check(city, extremes, stale_reports)
        assert calls["n"] == 1

        # After short retry throttle: WU is called again; the old implementation
        # would have consumed the 10-min success memo and skipped this.
        import time as _time
        with oa._WU_CHECK_MEMO_LOCK:
            oa._WU_CHECK_FAILURE_MEMO["Tokyo"] = _time.monotonic() - 200.0
        oa.wu_metar_anomaly_check(city, extremes, stale_reports)
        assert calls["n"] == 2


# ===========================================================================
# R24 — PR#404 ROUND-2: split memos, anomaly freshness gates, TTL'd miss cache
# ===========================================================================

class TestSplitMemos:
    """Round-2 P0-1: the kill memo (hard-fact exits) and the live-emission memo
    are SEPARATE state with separate update rules — a stale-withheld kill-memo
    advance must never suppress the later fresh live event."""

    def _flaky_emitter(self, reports):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        plan = {"fail": False, "calls": 0}

        def fetcher(stations, **kw):
            plan["calls"] += 1
            return [] if plan["fail"] else list(reports)

        return Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0), plan

    def test_operator_scenario_stale_withholding_does_not_suppress_fresh_live_emit(self):
        """THE mandated scenario: (1) fresh prefetch fills the cache but the
        write phase never runs; (2) fetch fails, cache aged beyond budget ->
        emit pass updates the KILL memo only (no live event); (3) a later
        FRESH fetch confirms the SAME rounded extreme -> the live event MUST
        still emit (the old coupled memo saw moved=False and never emitted)."""
        import time as _time

        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter, plan = self._flaky_emitter(reports)

        # (1) fresh prefetch fills the cache; write phase intentionally not run
        pf1 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert pf1.freshness_status == "fresh_fetch"

        # (2) outage + cache aged beyond Tokyo's 60-min budget -> kill memo only
        plan["fail"] = True
        emitter._cache_fetched_monotonic = _time.monotonic() - 7200.0
        pf2 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=10))
        assert pf2.freshness_status == "stale_cache_after_failure"
        n2 = emitter.emit_prefetched(
            world_conn=conn, prefetch=pf2,
            received_at=(t0 + timedelta(minutes=10)).isoformat(), limit=20,
        )
        assert n2 == 0
        assert emitter.latest_rounded_extreme("Tokyo", "2026-06-10", "high") == 21

        # (3) recovery: fresh fetch, SAME rounded extreme -> live event STILL emits
        plan["fail"] = False
        pf3 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=15))
        assert pf3.freshness_status == "fresh_fetch"
        n3 = emitter.emit_prefetched(
            world_conn=conn, prefetch=pf3,
            received_at=(t0 + timedelta(minutes=15)).isoformat(), limit=20,
        )
        assert n3 == 2, (
            "fresh confirmation of a kill-memo-only extreme must STILL emit the "
            f"live events (entry/exit lane state divergence) — emitted {n3}"
        )
        rows = conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0]
        assert rows == 2

    def test_only_inserted_live_events_advance_the_live_memo(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter, _plan = self._flaky_emitter(reports)
        pf = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=pf, received_at=t0.isoformat(), limit=20,
        ) == 2
        key = ("Tokyo", "2026-06-10", "high")
        assert emitter._last_live_emitted_rounded[key] == 21
        assert emitter._last_kill_memo_rounded[key] == 21
        # unchanged extreme: neither memo moves, nothing emits
        pf2 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=8))
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=pf2, received_at=t0.isoformat(), limit=20,
        ) == 0

    def test_duplicate_live_event_after_restart_advances_live_memo(self):
        """A persisted duplicate is already a live event. After a daemon restart
        the in-process live memo is empty, so the first write attempt may return
        duplicate. That duplicate must advance the live memo, or the daemon will
        retry the same INSERT OR IGNORE forever until the rounded extreme moves."""
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]

        emitter1, _ = self._flaky_emitter(reports)
        pf1 = emitter1.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert emitter1.emit_prefetched(
            world_conn=conn, prefetch=pf1,
            received_at=(t0 + timedelta(minutes=5)).isoformat(), limit=20,
        ) == 2

        # Simulated restart: new emitter has empty in-process memos but the
        # immutable events already exist in the world DB.
        emitter2, _ = self._flaky_emitter(reports)
        pf2 = emitter2.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=6))
        assert emitter2.emit_prefetched(
            world_conn=conn, prefetch=pf2,
            received_at=(t0 + timedelta(minutes=6)).isoformat(), limit=20,
        ) == 0
        assert emitter2._last_live_emitted_rounded[("Tokyo", "2026-06-10", "high")] == 21
        assert emitter2._last_live_emitted_rounded[("Tokyo", "2026-06-10", "low")] == 21


class TestAnomalyFreshnessGates:
    """Round-2 P0-2: the WU-vs-METAR detector must never CONCLUDE from a stale
    METAR window — at the prefetch layer (A) and inside the detector (B)."""

    def test_prefetch_skips_anomaly_check_on_stale_cache(self):
        import time as _time

        from src.data.day0_fast_obs import Day0FastObsEmitter

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        plan = {"fail": False}

        def fetcher(stations, **kw):
            return [] if plan["fail"] else list(reports)

        calls = {"n": 0}

        def check(city, extremes, rpts):
            calls["n"] += 1

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        pf = emitter.prefetch(
            cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5), anomaly_check=check,
        )
        assert pf.freshness_status == "fresh_fetch" and calls["n"] == 1

        plan["fail"] = True
        emitter._cache_fetched_monotonic = _time.monotonic() - 600.0
        emitter._station_cache_fetched_monotonic["RJTT"] = _time.monotonic() - 600.0
        pf2 = emitter.prefetch(
            cities=[_tokyo()], decision_time=t0 + timedelta(minutes=10), anomaly_check=check,
        )
        assert pf2.freshness_status == "stale_cache_after_failure"
        assert calls["n"] == 1, "a stale METAR cache must not feed the divergence detector"

    def test_prefetch_bounds_anomaly_checks_before_scanning_all_cities(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        tokyo_b = SimpleNamespace(
            name="Tokyo-B",
            timezone=tokyo.timezone,
            settlement_unit=tokyo.settlement_unit,
            wu_station="RJTT",
            settlement_source_type=tokyo.settlement_source_type,
        )
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        calls: list[str] = []

        def check(city, extremes, rpts):
            calls.append(city.name)

        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)
        prefetch = emitter.prefetch(
            cities=[tokyo, tokyo_b],
            decision_time=t0 + timedelta(minutes=5),
            anomaly_check=check,
            anomaly_check_budget_s=60.0,
            anomaly_check_max_cities=1,
        )

        assert prefetch.freshness_status == "fresh_fetch"
        assert calls == ["Tokyo"]

    def test_cached_anomaly_checks_rotate_without_another_metar_fetch(self, monkeypatch):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        # Fixture cities share Tokyo's real station under fictitious names
        # (Tokyo-B, ...) to test rotation/caching, not per-city faithfulness
        # -- stub the (b) fail-closed unmeasured-city lookup so these
        # synthetic names still resolve a fast-lane source (2026-07-26).
        monkeypatch.setattr(
            "src.data.day0_oracle_anomaly.metar_margin_units_for_city",
            lambda *_a, **_k: 0.0,
        )
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        tokyo_b = SimpleNamespace(
            name="Tokyo-B",
            timezone=tokyo.timezone,
            settlement_unit=tokyo.settlement_unit,
            wu_station="RJTT",
            settlement_source_type=tokyo.settlement_source_type,
        )
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        fetches = {"n": 0}

        def fetcher(stations, **kw):
            fetches["n"] += 1
            return reports

        checked: list[str] = []
        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter.prefetch(cities=[tokyo, tokyo_b], decision_time=t0)

        for offset in (5, 10):
            emitter.cached_anomaly_actions(
                cities=[tokyo, tokyo_b],
                decision_time=t0 + timedelta(seconds=offset),
                anomaly_check=lambda city, *_args: checked.append(city.name),
                max_cities=1,
            )

        assert fetches["n"] == 1
        assert checked == ["Tokyo", "Tokyo-B"]

    def test_cached_anomaly_check_prioritizes_flag_without_starving_rotation(self, monkeypatch):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        # See test_cached_anomaly_checks_rotate_without_another_metar_fetch:
        # fictitious per-station city names must still resolve a source.
        monkeypatch.setattr(
            "src.data.day0_oracle_anomaly.metar_margin_units_for_city",
            lambda *_a, **_k: 0.0,
        )
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        tokyo_b = SimpleNamespace(
            name="Tokyo-B",
            timezone=tokyo.timezone,
            settlement_unit=tokyo.settlement_unit,
            wu_station=tokyo.wu_station,
            settlement_source_type=tokyo.settlement_source_type,
        )
        tokyo_c = SimpleNamespace(
            name="Tokyo-C",
            timezone=tokyo.timezone,
            settlement_unit=tokyo.settlement_unit,
            wu_station=tokyo.wu_station,
            settlement_source_type=tokyo.settlement_source_type,
        )
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        checked: list[str] = []
        emitter = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: reports,
            min_fetch_interval_s=0.0,
        )
        emitter.prefetch(cities=[tokyo, tokyo_b, tokyo_c], decision_time=t0)

        for offset in (5, 10):
            emitter.cached_anomaly_actions(
                cities=[tokyo, tokyo_b, tokyo_c],
                decision_time=t0 + timedelta(seconds=offset),
                anomaly_check=lambda city, *_args: checked.append(city.name),
                max_cities=2,
                priority_city_names=("Tokyo-C",),
            )

        assert checked == ["Tokyo-C", "Tokyo", "Tokyo-C", "Tokyo-B"]

    def test_cached_anomaly_priority_rotates_with_bounded_api_budget(self, monkeypatch):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        # See test_cached_anomaly_checks_rotate_without_another_metar_fetch:
        # fictitious per-station city names must still resolve a source.
        monkeypatch.setattr(
            "src.data.day0_oracle_anomaly.metar_margin_units_for_city",
            lambda *_a, **_k: 0.0,
        )
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        cities = [
            SimpleNamespace(
                name=name,
                timezone=tokyo.timezone,
                settlement_unit=tokyo.settlement_unit,
                wu_station=tokyo.wu_station,
                settlement_source_type=tokyo.settlement_source_type,
            )
            for name in ("Tokyo-A", "Tokyo-B", "Tokyo-C", "Tokyo-D")
        ]
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        checked: list[str] = []
        emitter = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: reports,
            min_fetch_interval_s=0.0,
        )
        emitter.prefetch(cities=cities, decision_time=t0)

        for offset in (5, 10):
            emitter.cached_anomaly_actions(
                cities=cities,
                decision_time=t0 + timedelta(seconds=offset),
                anomaly_check=lambda city, *_args: checked.append(city.name),
                max_cities=3,
                priority_city_names=("Tokyo-B", "Tokyo-C", "Tokyo-D"),
            )

        assert checked == [
            "Tokyo-B", "Tokyo-C", "Tokyo-A",
            "Tokyo-D", "Tokyo-B", "Tokyo-A",
        ]

    def test_ledger_projection_cold_load_then_primary_key_delta(self):
        from src.data.day0_fast_obs import (
            FAST_OBS_SOURCE_ID,
            Day0FastObsEmitter,
        )
        from src.state.schema.observation_prints_schema import (
            append_print,
            ensure_table,
        )

        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        first = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
        append_print(
            conn,
            city="Tokyo",
            station_id="RJTT",
            source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=first.isoformat(),
            value_native=21.0,
            unit="C",
            fetched_at_utc=first.isoformat(),
            raw_report="METAR RJTT 091500Z T0210",
        )
        conn.commit()

        emitter = Day0FastObsEmitter(fetcher=lambda *_args, **_kw: [])
        assert emitter.sync_from_ledger(
            conn,
            [_tokyo()],
            as_of=first + timedelta(minutes=1),
        ) == 1

        second = first + timedelta(minutes=5)
        append_print(
            conn,
            city="Tokyo",
            station_id="RJTT",
            source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=second.isoformat(),
            value_native=22.0,
            unit="C",
            fetched_at_utc=second.isoformat(),
            raw_report="METAR RJTT 091505Z T0220",
        )
        conn.commit()
        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        assert emitter.sync_from_ledger(
            conn,
            [_tokyo()],
            as_of=second + timedelta(minutes=1),
        ) == 1
        conn.set_trace_callback(None)

        assert any("WHERE id >" in sql for sql in traced)
        extremes = emitter.latest_extremes(
            _tokyo(),
            "2026-06-10",
            as_of=second + timedelta(minutes=1),
        )
        assert extremes is not None
        assert extremes.high_so_far == pytest.approx(22.0)
        assert extremes.sample_count == 2

    def test_ledger_identity_seed_removes_cold_fetch_history_from_write_delta(self):
        from src.data.day0_fast_obs import FAST_OBS_SOURCE_ID, Day0FastObsEmitter
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        observed = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
        report = _report("RJTT", observed, 21.0)
        append_print(
            conn,
            city="Tokyo",
            station_id="RJTT",
            source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=report.receipt_time.isoformat(),
            value_native=21.0,
            unit="C",
            fetched_at_utc=report.receipt_time.isoformat(),
            raw_report=report.raw,
        )
        conn.commit()
        emitter = Day0FastObsEmitter(
            fetcher=lambda *_args, **_kwargs: [report],
            min_fetch_interval_s=0.0,
        )

        assert emitter.sync_ledger_report_keys(
            conn,
            [_tokyo()],
            as_of=report.receipt_time + timedelta(minutes=1),
        ) == 1
        assert emitter.ledger_report_keys_loaded()
        prefetch = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=report.receipt_time + timedelta(minutes=1),
        )

        assert prefetch.reports == (report,)
        assert prefetch.ledger_reports == ()

        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        assert emitter.sync_ledger_report_keys(conn, [_tokyo()]) == 0
        conn.set_trace_callback(None)
        assert not traced

    @pytest.mark.parametrize("old_temp,new_temp,metric", [(27.0, 24.0, "high"), (21.0, 24.0, "low")])
    def test_startup_retains_day_extreme_through_first_partial_history(self, old_temp, new_temp, metric):
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        old_time = datetime(2026, 6, 9, 22, tzinfo=UTC)
        new_time = datetime(2026, 6, 10, 0, 30, tzinfo=UTC)
        old = _report("RJTT", old_time, old_temp, t_group=False)
        new = _report("RJTT", new_time, new_temp, t_group=False)
        conn = _world_conn()
        ensure_table(conn)
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=old.receipt_time.isoformat(), value_native=old_temp,
            unit="C", fetched_at_utc=old.receipt_time.isoformat(), raw_report=old.raw,
        )
        conn.commit()
        emitter = Day0FastObsEmitter(fetcher=lambda *_args, **_kwargs: [new], min_fetch_interval_s=0.0)
        key = ("Tokyo", "2026-06-10", metric)
        emitter._last_live_emitted_rounded[key] = int(old_temp)
        emitter._last_live_emitted_observation_time[key] = old_time.isoformat()
        as_of = new.receipt_time + timedelta(minutes=1)

        assert emitter.sync_ledger_report_keys(conn, [_tokyo()], as_of=as_of) == 1
        assert emitter._cached_reports == [old]
        assert emitter._cache_fetched_monotonic == 0.0
        assert emitter._station_cache_fetched_monotonic == {}
        assert emitter._last_attempt_monotonic == 0.0
        assert emitter._full_window_loaded is False
        prefetch = emitter.prefetch(cities=[_tokyo()], decision_time=as_of)
        extremes = running_extremes_for_local_day(
            list(prefetch.reports), city=_tokyo(), target_date="2026-06-10", as_of=as_of,
        )
        assert getattr(extremes, f"{metric}_so_far") == old_temp
        assert extremes.current_temp == new_temp
        assert extremes.last_obs_time == new_time
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=prefetch, received_at=as_of.isoformat(), persist_ledger=False,
        ) >= 1
        assert emitter._last_live_emitted_rounded[key] == int(old_temp)
        assert emitter._last_live_emitted_observation_time[key] == new_time.isoformat()

    @pytest.mark.parametrize("defect", ["raw_station", "station", "raw_empty", "raw_time", "future_observation", "future_publication", "future_fetch", "naive", "unit", "nonfinite", "source"])
    def test_startup_ledger_window_rejects_unqualified_reports(self, defect):
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        now = datetime(2026, 9, 15, 0, 40, tzinfo=UTC)
        row = dict(
            city="Tokyo", station_id="RJTT", source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc="2026-09-15T00:38:00+00:00", value_native=24.0, unit="C",
            fetched_at_utc="2026-09-15T00:39:00+00:00", raw_report="RJTT 150030Z 24/23 Q1014",
        )
        changes = {
            "raw_station": {"raw_report": "RKSI 150030Z 24/23 Q1014"},
            "station": {"station_id": "RKSI"},
            "raw_empty": {"raw_report": ""},
            "raw_time": {"raw_report": "RJTT 159999Z 24/23 Q1014"},
            "future_observation": {"raw_report": "RJTT 150045Z 24/23 Q1014"},
            "future_publication": {"publish_ts_utc": "2026-09-15T00:41:00+00:00"},
            "future_fetch": {"fetched_at_utc": "2026-09-15T00:41:00+00:00"},
            "naive": {"publish_ts_utc": "2026-09-15T00:38:00"},
            "unit": {"unit": "F"},
            "nonfinite": {"value_native": float("inf")},
            "source": {"source_channel": "unrelated_feed"},
        }
        row.update(changes[defect])
        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        if defect == "naive":
            # The ledger writer refuses a naive clock outright.
            with pytest.raises(ValueError, match="naive"):
                append_print(conn, **row)
        else:
            append_print(conn, **row)
        conn.commit()
        emitter = Day0FastObsEmitter()
        emitter.sync_ledger_report_keys(conn, [_tokyo()], as_of=now)
        assert emitter._cached_reports == []
        assert emitter._station_cache_fetched_monotonic == {}
        assert emitter._cache_fetched_monotonic == 0.0
        assert emitter._full_window_loaded is False

    def test_startup_recovers_valid_time_across_month_and_local_day(self):
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc="2026-10-01T00:05:00+00:00", value_native=24.0, unit="C",
            fetched_at_utc="2026-10-01T00:06:00+00:00", raw_report="RJTT 302330Z 24/23 Q1014",
        )
        conn.commit()
        now = datetime(2026, 10, 1, 0, 10, tzinfo=UTC)
        emitter = Day0FastObsEmitter()
        emitter.sync_ledger_report_keys(conn, [_tokyo()], as_of=now)
        report, = emitter._cached_reports
        assert report.obs_time == datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
        assert report.receipt_time == datetime(2026, 10, 1, 0, 5, tzinfo=UTC)
        extremes = running_extremes_for_local_day(
            emitter._cached_reports, city=_tokyo(), target_date="2026-10-01", as_of=now,
        )
        assert extremes.sample_count == 1
        assert extremes.last_obs_time == report.obs_time

    def test_detector_refuses_conclusion_when_metar_window_lags_wu(self):
        """Operator scenario: METAR outage since 10:00, WU moved at 12:00 —
        comparing a 2h-stale METAR window vs current WU is NOT divergence."""
        from src.data import day0_oracle_anomaly as oa

        # METAR reports through 10:00 UTC only
        reports = [
            _report("RJTT", datetime(2026, 6, 10, 9, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 10, 0, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=26.0,  # WU moved 4C since the METAR outage began
            wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        )
        assert verdict.compared is False and verdict.diverged is False
        assert "metar_side_stale_for_wu_window" in verdict.detail
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10",
                                        conn=sqlite3.connect(":memory:")) is False

    def test_detector_still_fires_on_real_mismatch_with_coverage(self):
        """2026-07-26 (HIGH-side start-coverage fix): the METAR window must
        genuinely cover from local-day onset for this to be a coverage-OK real
        mismatch test — Tokyo local midnight on 2026-06-10 is 2026-06-09T15:00Z.
        A window starting at 15:10Z (within the 2h grace) plus samples spanning
        through wu_last_obs_time gives real start-coverage, so a real tamper on
        the HIGH must still fire (this is the genuine-tamper counterpart to
        test_detector_refuses_conclusion_when_metar_window_lags_wu and the new
        false-pause regression test below)."""
        from src.data import day0_oracle_anomaly as oa

        reports = [
            _report("RJTT", datetime(2026, 6, 9, 15, 10, tzinfo=UTC), 21.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 9, 21, 0, tzinfo=UTC), 21.5, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 3, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 9, 0, tzinfo=UTC), 21.5, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 11, 30, tzinfo=UTC), 22.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 12, 0, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=26.0,  # real same-window mismatch (4C > threshold)
            wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        )
        assert "metar_high_coverage=OK" in verdict.detail
        assert verdict.compared is True and verdict.diverged is True

    def test_high_side_coverage_gap_does_not_false_pause(self):
        """2026-07-26 fix (frozen-posterior-adjacent Day0 anomaly item): live
        artifact shape world.day0_oracle_anomaly_flags 2026-07-25 — 32/37 flagged
        rows carried nonzero high_delta with low_delta=None/metar_low_coverage=
        WINDOW_INCOMPLETE and low_delta_raw~0 (the covered side agreed). This is
        the exact reproduction: a METAR window starting late in the local day
        (missed the full-day history) must NOT conclude a HIGH divergence — the
        window cannot prove it saw the true running high any more than it can
        prove it saw the true running low."""
        from src.data import day0_oracle_anomaly as oa

        reports = [
            _report("RJTT", datetime(2026, 6, 10, 11, 30, tzinfo=UTC), 22.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 12, 0, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=26.0,  # would be a 4C mismatch if the window were trusted
            wu_low_so_far=21.95,  # covered side agrees near-perfectly (low_delta_raw~0.05)
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        )
        assert verdict.compared is True
        assert verdict.diverged is False
        assert verdict.high_delta is None
        assert verdict.low_delta is None
        assert "metar_high_coverage=WINDOW_INCOMPLETE" in verdict.detail
        assert "metar_low_coverage=WINDOW_INCOMPLETE" in verdict.detail
        assert "low_delta_raw=0.05" in verdict.detail

    def test_coverage_within_tolerance_still_compares(self):
        from src.data import day0_oracle_anomaly as oa

        reports = [
            _report("RJTT", datetime(2026, 6, 10, 11, 57, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=22.0, wu_low_so_far=22.0,
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),  # 3 min ahead
        )
        assert verdict.compared is True and verdict.diverged is False


class TestTtlMissCacheAndPersistedTtl:
    """Round-2 P1-A: the negative-miss cache is TTL'd (cross-process flags
    become visible without restart) and the persisted TTL is the authority."""

    def test_flag_from_another_process_visible_after_miss_cache_ttl(self, monkeypatch):
        from src.data import day0_oracle_anomaly as oa

        conn = sqlite3.connect(":memory:")
        # process A reads -> negative miss cached
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10", conn=conn) is False
        # external process/operator writes the durable flag directly
        conn.execute(oa._FLAGS_TABLE_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO day0_oracle_anomaly_flags VALUES (?,?,?,?,?)",
            ("Tokyo", "2026-06-10", datetime(2026, 6, 10, 4, 0, tzinfo=UTC).isoformat(),
             24.0, "external"),
        )
        conn.commit()
        # within the miss-cache TTL the stale negative may persist…
        # …but once the TTL lapses the flag MUST become visible (no restart).
        monkeypatch.setattr(oa, "_DB_MISS_TTL_S", 0.0)
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC), conn=conn,
        ) is True

    def test_persisted_custom_ttl_survives_restart_and_governs_expiry(self):
        from src.data import day0_oracle_anomaly as oa

        conn = sqlite3.connect(":memory:")
        oa.flag_day0_oracle_anomaly(
            "Tokyo", "2026-06-10", detail="short-lived",
            now=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            ttl_hours=2.0, conn=conn,
        )
        oa._reset_registry_for_tests()  # simulated restart
        # +1h: paused (within the persisted 2h TTL)
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10", now=datetime(2026, 6, 10, 5, 0, tzinfo=UTC), conn=conn,
        ) is True
        oa._reset_registry_for_tests()
        # +3h: the PERSISTED 2h TTL governs — NOT the 24h call-site default
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10", now=datetime(2026, 6, 10, 7, 0, tzinfo=UTC), conn=conn,
        ) is False
        # expired durable row was best-effort deleted (no restart re-hydration)
        rows = conn.execute("SELECT COUNT(*) FROM day0_oracle_anomaly_flags").fetchone()[0]
        assert rows == 0


def test_fast_conditioning_deduplicates_same_metar_across_writer_prefixes(
    monkeypatch,
) -> None:
    """A second rendering of one raw report cannot advance the Day0 identity."""
    from src import config as config_module

    monkeypatch.setitem(
        config_module.cities_by_name,
        "Wellington",
        SimpleNamespace(
            settlement_source_type="wu_icao",
            wu_station="NZWN",
            settlement_unit="C",
            timezone="Pacific/Auckland",
        ),
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_prints (
            city TEXT, station_id TEXT, source_channel TEXT,
            publish_ts_utc TEXT, value_native REAL, unit TEXT,
            fetched_at_utc TEXT, raw_report TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO observation_prints VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "Wellington", "NZWN", FAST_OBS_SOURCE_ID,
                "2026-07-28T19:34:12+00:00", 7.0, "C",
                "2026-07-28T19:34:20+00:00",
                "NZWN 281930Z AUTO 02014KT 9999 NCD 07/04 Q1025",
            ),
            (
                "Wellington", "NZWN", FAST_OBS_SOURCE_ID,
                "2026-07-28T19:34:11.503000+00:00", 7.0, "C",
                "2026-07-28T19:35:41+00:00",
                "METAR NZWN 281930Z AUTO 02014KT 9999 NCD 07/04 Q1025",
            ),
        ),
    )

    assert latest_fast_station_extreme_c(
        conn,
        city="Wellington",
        target_date="2026-07-29",
        metric="high",
        decision_time=datetime(2026, 7, 28, 19, 40, tzinfo=UTC),
    ) == (7.0, "2026-07-28T19:34:12+00:00", 1, "C")
    conn.close()


def _kma_cell(raw: str) -> str:
    return f"<table><tr><td>{raw}</td></tr></table>"


def _kma_contract_symbols():
    from src.data.day0_fast_obs import (
        AVAILABILITY_LOCAL_FIRST_SEEN_AFTER_COMPLETE_RESPONSE,
        KMA_METAR_TRANSPORT_ID,
        KmaDay0EventState,
        KmaMetarCursor,
        parse_kma_metar_html,
    )
    return (
        AVAILABILITY_LOCAL_FIRST_SEEN_AFTER_COMPLETE_RESPONSE,
        KMA_METAR_TRANSPORT_ID,
        KmaDay0EventState,
        KmaMetarCursor,
        parse_kma_metar_html,
    )


def test_kma_parser_reads_real_euckr_sample_and_deduplicates_a_q() -> None:
    availability_basis, transport_id, _state_cls, _cursor_cls, parse = _kma_contract_symbols()
    payload = "".join(
        (
            _kma_cell("METAR RKPK 220300Z 05016KT 9999 FEW050 27/14 A1016"),
            _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 A1016"),
            _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016"),
        )
    ).encode("euc-kr")
    as_of = datetime(2026, 9, 22, 4, 30, tzinfo=UTC)
    first_seen = datetime(2026, 9, 22, 4, 20, tzinfo=UTC)

    reports = parse(
        payload,
        station_id="RKPK",
        as_of=as_of,
        first_seen_at=first_seen,
    )

    assert reports
    latest = max(reports, key=lambda report: report.obs_time)
    assert latest.station_id == "RKPK"
    assert latest.obs_time == datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    assert latest.temp_c == 28.0
    assert " Q" in f" {latest.raw} "
    assert latest.availability_basis == availability_basis
    assert latest.available_at == first_seen
    assert latest.transport_id == transport_id
    assert latest.raw_report_identity
    assert sum(report.obs_time == latest.obs_time for report in reports) == 1


def test_kma_parser_rejects_wrong_station_malformed_and_future_reports() -> None:
    _availability, _transport, _state_cls, _cursor_cls, parse = _kma_contract_symbols()
    as_of = datetime(2026, 9, 22, 4, 30, tzinfo=UTC)
    first_seen = datetime(2026, 9, 22, 4, 31, tzinfo=UTC)
    payload = "".join(
        (
            _kma_cell("METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016"),
            _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 Q1016"),
            _kma_cell("METAR RKPK 230400Z 05016KT 9999 FEW050 29/14 Q1016"),
        )
    )

    assert parse(
        payload, station_id="RKPK", as_of=as_of, first_seen_at=first_seen
    ) == []


def test_kma_parser_prefers_cor_and_rejects_conflicting_cor() -> None:
    availability_basis, transport_id, _state_cls, _cursor_cls, parse = _kma_contract_symbols()
    from src.data.day0_fast_obs import KmaObservationConflict
    as_of = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)
    first_seen = datetime(2026, 9, 22, 5, 1, tzinfo=UTC)
    selected = parse(
        "".join(
            (
                _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016"),
                _kma_cell("METAR COR RKPK 220400Z 05016KT 9999 FEW050 29/14 Q1016"),
            )
        ),
        station_id="RKPK",
        as_of=as_of,
        first_seen_at=first_seen,
    )
    assert len(selected) == 1
    assert selected[0].metar_type == "COR"
    assert selected[0].temp_c == 29.0

    with pytest.raises(KmaObservationConflict):
        parse(
            "".join(
                (
                    _kma_cell("METAR COR RKPK 220400Z 05016KT 9999 FEW050 29/14 Q1016"),
                    _kma_cell("METAR COR RKPK 220400Z 05016KT 9999 FEW050 30/14 Q1016"),
                )
            ),
            station_id="RKPK",
            as_of=as_of,
            first_seen_at=first_seen,
        )


def test_kma_parser_requires_t_group_for_f_settlement() -> None:
    _availability, _transport, _state_cls, _cursor_cls, parse = _kma_contract_symbols()
    report = parse(
        _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016"),
        station_id="RKPK",
        as_of=datetime(2026, 9, 22, 5, 0, tzinfo=UTC),
        first_seen_at=datetime(2026, 9, 22, 5, 1, tzinfo=UTC),
    )[0]
    assert settlement_temp_for_report(report, "F") is None


def test_kma_cursor_throttles_per_station_and_records_complete_response_first_seen(
    monkeypatch,
) -> None:
    _availability, _transport, _state_cls, cursor_cls, _parse = _kma_contract_symbols()
    payload = _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016")
    calls: list[tuple[str, dict[str, str]]] = []

    class _Response:
        status_code = 200
        content = payload.encode("euc-kr")

    class _Client:
        def post(self, endpoint, *, data, timeout, headers):
            calls.append((endpoint, data))
            return _Response()

    now = [100.0]
    monkeypatch.setattr("src.data.day0_fast_obs.time.monotonic", lambda: now[0])
    cursor = cursor_cls(min_fetch_interval_s=60.0)
    try:
        before = datetime.now(UTC)
        reports, ok = cursor.poll(
            client=_Client(),
            stations=("RKPK", "KORD"),
            as_of=datetime(2026, 9, 22, 4, 30, tzinfo=UTC),
            budget_s=0.5,
        )
        after = datetime.now(UTC)
        assert ok is True
        assert len(reports) == 1
        assert before <= reports[0].first_seen_at <= after
        assert calls == [
            (
                "https://global.amo.go.kr/observation/PkObsMetarList.do",
                {"stnCd": "RKPK", "tm": "2026.09.22 13:30"},
            )
        ]
        now[0] += 1.0
        assert cursor.poll(
            client=_Client(),
            stations=("RKPK",),
            as_of=datetime(2026, 9, 22, 4, 30, tzinfo=UTC),
            budget_s=0.05,
        ) == ([], False)
        assert len(calls) == 1
    finally:
        cursor.close()


def test_kma_cursor_slow_station_does_not_block_fast_station() -> None:
    _availability, _transport, _state_cls, cursor_cls, _parse = _kma_contract_symbols()
    payloads = {
        "RKPK": _kma_cell("METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016"),
        "RKSI": _kma_cell("METAR RKSI 220400Z 05016KT 9999 FEW050 27/15 Q1016"),
    }

    class _Response:
        status_code = 200

        def __init__(self, body: str):
            self.content = body.encode("euc-kr")

    class _Client:
        def post(self, _endpoint, *, data, timeout, headers):
            if data["stnCd"] == "RKPK":
                time.sleep(0.2)
            return _Response(payloads[data["stnCd"]])

    cursor = cursor_cls(min_fetch_interval_s=0.0, max_workers=2)
    try:
        reports, ok = cursor.poll(
            client=_Client(),
            stations=("RKPK", "RKSI"),
            as_of=datetime(2026, 9, 22, 4, 30, tzinfo=UTC),
            budget_s=0.05,
        )
        assert ok is True
        assert {report.station_id for report in reports} == {"RKSI"}
    finally:
        cursor.close()


def _reports_with_fake_kma_polls(monkeypatch, polls):
    import src.data.day0_fast_obs as fast_obs

    class _Cursor:
        def __init__(self):
            self._polls = iter(polls)
            self._last_successful_stations = frozenset()
            self._last_conflicts = {}

        def poll(self, **_kwargs):
            reports, ok, stations, conflicts = next(self._polls)
            self._last_successful_stations = frozenset(stations)
            self._last_conflicts = dict(conflicts)
            return list(reports), ok

    emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
    emitter._kma_cursor = _Cursor()
    monkeypatch.setattr(emitter._station_cursor, "poll", lambda **_kwargs: ([], False))
    monkeypatch.setattr(
        emitter,
        "_poll_global_sources_in_background",
        lambda **_kwargs: ([], False, False),
    )
    return emitter._reports_with_status(
        ["RKSI"],
        priority_stations=("RKSI",),
    ), emitter


def test_kma_poll_keeps_second_only_result_and_deduplicates_both_polls(monkeypatch):
    availability, transport, _state, _cursor, _parse = _kma_contract_symbols()
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    report = MetarReport(
        "RKSI", observed, None, 28.0, "METAR",
        "METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        observed + timedelta(minutes=1), availability, transport,
    )
    (second_only, _emitter) = _reports_with_fake_kma_polls(
        monkeypatch,
        [([], False, (), {}), ([report], True, ("RKSI",), {})],
    )
    reports, status, _age = second_only
    assert status == "fresh_fetch"
    assert reports == [report]

    (duplicate, _emitter) = _reports_with_fake_kma_polls(
        monkeypatch,
        [([report], True, ("RKSI",), {}), ([report], True, ("RKSI",), {})],
    )
    reports, status, _age = duplicate
    assert status == "fresh_fetch"
    assert reports == [report]


def test_kma_empty_second_poll_retains_first_conflict(monkeypatch):
    import src.data.day0_fast_obs as fast_obs

    availability, transport, _state, _cursor, _parse = _kma_contract_symbols()
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    bad_one = MetarReport(
        "RKSI", observed, None, 28.0, "COR",
        "METAR COR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        observed + timedelta(minutes=1), availability, transport,
    )
    bad_two = MetarReport(
        "RKSI", observed, None, 29.0, "COR",
        "METAR COR RKSI 220400Z 05016KT 9999 FEW050 29/14 Q1016",
        observed + timedelta(minutes=1), availability, transport,
    )
    conflict = fast_obs.KmaObservationConflict(
        "conflict", station_id="RKSI", obs_time=observed,
        raw_reports=(bad_one.raw, bad_two.raw), correction_rank="COR",
        reports=(bad_one, bad_two),
    )
    (result, emitter) = _reports_with_fake_kma_polls(
        monkeypatch,
        [([], False, (), {"RKSI": conflict}), ([], False, (), {})],
    )
    reports, status, _age = result
    assert reports == []
    assert status == "no_data"
    assert emitter._last_kma_conflicts == {"RKSI": conflict}


def test_kma_second_success_does_not_clear_first_poll_conflict(monkeypatch):
    import src.data.day0_fast_obs as fast_obs

    availability, transport, _state, _cursor, _parse = _kma_contract_symbols()
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    bad_one = MetarReport(
        "RKSI", observed, None, 28.0, "COR",
        "METAR COR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        observed + timedelta(minutes=1), availability, transport,
    )
    bad_two = MetarReport(
        "RKSI", observed, None, 29.0, "COR",
        "METAR COR RKSI 220400Z 05016KT 9999 FEW050 29/14 Q1016",
        observed + timedelta(minutes=1), availability, transport,
    )
    good = MetarReport(
        "RKSI", observed + timedelta(minutes=1), None, 30.0, "METAR",
        "METAR RKSI 220501Z 05016KT 9999 FEW050 30/14 Q1016",
        observed + timedelta(minutes=2), availability, transport,
    )
    conflict = fast_obs.KmaObservationConflict(
        "conflict", station_id="RKSI", obs_time=observed,
        raw_reports=(bad_one.raw, bad_two.raw), correction_rank="COR",
        reports=(bad_one, bad_two),
    )
    (result, emitter) = _reports_with_fake_kma_polls(
        monkeypatch,
        [([], False, (), {"RKSI": conflict}), ([good], True, ("RKSI",), {})],
    )
    reports, status, _age = result
    assert reports == [good]
    assert status == "fresh_fetch"
    assert emitter._last_kma_conflicts == {"RKSI": conflict}


def test_kma_prefetch_first_poll_reaches_emit_without_publication_ledger(
    monkeypatch,
):
    import src.data.day0_fast_obs as fast_obs

    availability, transport, _state, _cursor, _parse = _kma_contract_symbols()
    city = _seoul()
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    decision = datetime(2026, 9, 22, 4, 5, tzinfo=UTC)
    report = MetarReport(
        "RKSI", observed, None, 28.0, "METAR",
        "METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        observed + timedelta(minutes=1), availability, transport,
    )
    emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)

    class _Cursor:
        _last_successful_stations = frozenset()
        _last_conflicts = {}

        def __init__(self):
            self.calls = 0

        def poll(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                self._last_successful_stations = frozenset({"RKSI"})
                return [report], True
            self._last_successful_stations = frozenset()
            self._last_conflicts = {}
            return [], False

    emitter._kma_cursor = _Cursor()
    monkeypatch.setattr(emitter._station_cursor, "poll", lambda **_kwargs: ([], False))
    monkeypatch.setattr(
        emitter,
        "_poll_global_sources_in_background",
        lambda **_kwargs: ([], False, False),
    )
    monkeypatch.setattr(
        fast_obs,
        "_append_metar_prints_to_ledger",
        lambda *_args, **_kwargs: pytest.fail("KMA report entered NOAA publication ledger"),
    )

    prefetch = emitter.prefetch(
        cities=[city],
        decision_time=decision,
        priority_scopes=[("Seoul", "2026-09-22")],
    )
    assert prefetch.reports == (report,)
    assert prefetch.event_reports == (report,)
    assert prefetch.ledger_reports == ()

    conn = _world_conn()
    try:
        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at=decision.isoformat(),
            persist_ledger=True,
        ) == 2
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM opportunity_events "
                "WHERE event_type='DAY0_EXTREME_UPDATED' ORDER BY rowid LIMIT 1"
            ).fetchone()[0]
        )
        assert payload["observation_transport"] == transport
        assert payload["kma_report_window"][0]["raw_report"] == report.raw
    finally:
        conn.close()


def test_kma_and_noaa_duplicate_keeps_earliest_availability() -> None:
    availability_basis, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    kma = MetarReport(
        station_id="RKPK",
        obs_time=observed,
        receipt_time=None,
        temp_c=28.0,
        metar_type="METAR",
        raw="METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        first_seen_at=datetime(2026, 9, 22, 4, 1, tzinfo=UTC),
        availability_basis=availability_basis,
        transport_id=transport_id,
    )
    noaa = MetarReport(
        station_id="RKPK",
        obs_time=observed,
        receipt_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC),
        temp_c=28.0,
        metar_type="METAR",
        raw="RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        transport_id="noaa_cycle",
    )
    from src.data.day0_fast_obs import _merge_report_windows

    assert _merge_report_windows([noaa], [kma]) == [kma]


def test_kma_restart_hydrates_earliest_first_seen_before_canonical_emit(monkeypatch) -> None:
    import src.data.day0_fast_obs as fast_obs
    from dataclasses import replace
    availability_basis, transport_id, state_cls, _cursor_cls, _parse = _kma_contract_symbols()

    city = _seoul()
    source = fast_obs.fast_obs_source_for_city(city, target_date="2026-09-22")
    assert source is not None
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    earliest = datetime(2026, 9, 22, 4, 1, tzinfo=UTC)
    late = datetime(2026, 9, 22, 4, 3, tzinfo=UTC)
    raw = "METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016"
    old = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=28.0,
        metar_type="METAR", raw=raw,
        first_seen_at=earliest,
        availability_basis=availability_basis,
        transport_id=transport_id,
    )
    fetched_again = replace(old, first_seen_at=late, raw=raw.replace("05016", "05017"))
    state = state_cls(
        observed_at=observed,
        available_at=earliest,
        current_temp_c=28.0,
        high_native=28.0,
        low_native=28.0,
        raw_report=old.raw,
        raw_identity=old.raw_report_identity,
        corrected=False,
        margin_units=0.0,
        event_id="prior-kma-event",
        reports=(old,),
    )
    monkeypatch.setattr(fast_obs, "_latest_kma_day0_event_state", lambda *args, **kwargs: state)
    prefetch = fast_obs.FastObsPrefetch(
        eligible=((city, source, "2026-09-22"),),
        reports=(fetched_again,),
        freshness_status=fast_obs.FETCH_FRESH,
        cache_age_s=0.0,
        decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC),
        ledger_reports=(),
        station_statuses=(("RKSI", fast_obs.FETCH_FRESH, 0.0),),
        event_reports=(fetched_again,),
    )
    conn = _world_conn()
    try:
        emitter = fast_obs.Day0FastObsEmitter()
        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at="2026-09-22T04:05:00+00:00",
            persist_ledger=False,
        ) == 2
        rows = conn.execute(
            "SELECT payload_json FROM opportunity_events "
            "WHERE event_type='DAY0_EXTREME_UPDATED' ORDER BY rowid DESC"
        ).fetchall()
        assert rows
        import json

        window = json.loads(rows[-1][0])["kma_report_window"]
        assert window[0]["first_seen_at"] == earliest.isoformat()
    finally:
        conn.close()


def test_kma_same_observation_correction_emits_canonical_revision(monkeypatch) -> None:
    import src.data.day0_fast_obs as fast_obs
    availability_basis, transport_id, state_cls, _cursor_cls, _parse = _kma_contract_symbols()

    city = _seoul()
    source = fast_obs.fast_obs_source_for_city(city, target_date="2026-09-22")
    assert source is not None
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    old = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=28.0,
        metar_type="METAR", raw="METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        first_seen_at=datetime(2026, 9, 22, 4, 1, tzinfo=UTC),
        availability_basis=availability_basis,
        transport_id=transport_id,
    )
    corrected = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=27.0,
        metar_type="COR", raw="METAR COR RKSI 220400Z 05016KT 9999 FEW050 27/14 Q1016",
        first_seen_at=datetime(2026, 9, 22, 4, 3, tzinfo=UTC),
        availability_basis=availability_basis,
        transport_id=transport_id,
    )
    state = state_cls(
        observed_at=observed, available_at=old.available_at,
        current_temp_c=old.temp_c, high_native=old.temp_c, low_native=old.temp_c,
        raw_report=old.raw, raw_identity=old.raw_report_identity,
        corrected=False, margin_units=0.0, event_id="prior-kma-event", reports=(old,),
    )
    monkeypatch.setattr(fast_obs, "_latest_kma_day0_event_state", lambda *args, **kwargs: state)
    prefetch = fast_obs.FastObsPrefetch(
        eligible=((city, source, "2026-09-22"),), reports=(corrected,),
        freshness_status=fast_obs.FETCH_FRESH, cache_age_s=0.0,
        decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC), ledger_reports=(),
        station_statuses=(("RKSI", fast_obs.FETCH_FRESH, 0.0),),
        event_reports=(corrected,),
    )
    conn = _world_conn()
    emitter = fast_obs.Day0FastObsEmitter()
    emitter._last_live_emitted_rounded.update({
        ("Seoul", "2026-09-22", "high"): 29,
        ("Seoul", "2026-09-22", "low"): 29,
    })
    emitter._last_live_emitted_observation_time.update({
        ("Seoul", "2026-09-22", "high"): observed.isoformat(),
        ("Seoul", "2026-09-22", "low"): observed.isoformat(),
    })
    try:
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=prefetch,
            received_at="2026-09-22T04:05:00+00:00", persist_ledger=False,
        ) >= 1
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM opportunity_events "
            "WHERE event_type='DAY0_EXTREME_UPDATED' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0])
        assert payload["current_observation_temp_c"] == 27.0
        assert payload["kma_report_window"][0]["raw_report"].startswith("METAR COR RKSI")
        assert emitter._last_live_emitted_rounded[("Seoul", "2026-09-22", "high")] == 29
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=prefetch,
            received_at="2026-09-22T04:06:00+00:00", persist_ledger=False,
        ) == 0
    finally:
        conn.close()


def test_kma_helper_reads_attached_world_and_drains_conflict_to_valid(monkeypatch) -> None:
    import dataclasses
    import src.data.day0_fast_obs as fast_obs
    from src.events.day0_authority import DAY0_LIVE_AUTHORITY_MATCHES
    from src.events.opportunity_event import (
        Day0ExtremeUpdatedPayload,
        make_day0_extreme_updated_event,
    )
    from src.state.schema.opportunity_events_schema import CREATE_TABLE_SQL
    availability_basis, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()

    city = _seoul()
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    first_seen = datetime(2026, 9, 22, 4, 1, tzinfo=UTC)
    raw = "METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016"
    item = {
        "raw_report": raw,
        "raw_report_identity": fast_obs._normalized_raw_report_identity(raw),
        "first_seen_at": first_seen.isoformat(),
    }
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(CREATE_TABLE_SQL.replace(
        "CREATE TABLE IF NOT EXISTS opportunity_events",
        "CREATE TABLE world.opportunity_events",
    ))
    conn.execute("CREATE TABLE world.observation_prints (city TEXT, station_id TEXT, source_channel TEXT, publish_ts_utc TEXT, value_native REAL, unit TEXT, raw_report TEXT, fetched_at_utc TEXT)")

    def insert_event(*, conflict: dict[str, object] | None, received: str, metric: str = "high") -> None:
        payload = Day0ExtremeUpdatedPayload(
            city="Seoul", target_date="2026-09-22", metric=metric,
            settlement_source=FAST_OBS_SOURCE_ID, station_id="RKSI",
            observation_time=observed.isoformat(), observation_available_at=first_seen.isoformat(),
            raw_value=None if conflict else 28.0, rounded_value=None if conflict else 28,
            high_so_far=None if conflict else 28.0, low_so_far=None if conflict else 28.0,
            settlement_source_type="noaa", kma_report_window=[item],
            observation_availability_basis=availability_basis,
            observation_transport=transport_id,
            current_observation_temp_c=None if conflict else 28.0,
            current_observation_raw_report=None if conflict else raw,
            raw_report_identity=None if conflict else item["raw_report_identity"],
            observation_conflict=conflict,
            **({"source_authorized_status": "UNAUTHORIZED", "live_authority_status": "blocked"}
               if conflict else DAY0_LIVE_AUTHORITY_MATCHES),
        )
        event = make_day0_extreme_updated_event(
            entity_key=f"Seoul|2026-09-22|high|{'conflict' if conflict else 'valid'}|{received}",
            source="day0_extreme_updated_trigger", observed_at=observed.isoformat(), received_at=received, payload=payload,
        )
        values = dataclasses.asdict(event)
        cols = ",".join(values)
        conn.execute(
            f"INSERT INTO world.opportunity_events ({cols}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )

    conflict = {"station_id": "RKSI", "obs_time": observed.isoformat(), "correction_rank": "COR"}
    insert_event(conflict=conflict, received="2026-09-22T04:02:00+00:00")
    with pytest.raises(fast_obs.KmaObservationConflict):
        fast_obs._latest_kma_day0_event_state(
            conn, city=city, target_date="2026-09-22",
            decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC), metric=None,
        )
    insert_event(conflict=None, received="2026-09-22T04:04:00+00:00", metric="high")
    insert_event(conflict=None, received="2026-09-22T04:04:00+00:00", metric="low")
    state = fast_obs._latest_kma_day0_event_state(
        conn, city=city, target_date="2026-09-22",
        decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC), metric=None,
    )
    assert state is not None
    assert state.current_temp_c == 28.0
    high = fast_obs._latest_kma_day0_event_state(
        conn, city=city, target_date="2026-09-22",
        decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC), metric="high",
    )
    low = fast_obs._latest_kma_day0_event_state(
        conn, city=city, target_date="2026-09-22",
        decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC), metric="low",
    )
    assert high is not None and low is not None
    assert high.raw_identity == low.raw_identity == state.raw_identity
    conn.close()


def _kma_source_clock_context(city_name, metric, *, with_raw=True):
    """Real raw emitter -> posterior bridge, with an older lawful AWC print."""
    import src.data.day0_fast_obs as fast
    from src.config import cities_by_name
    from src.events.event_store import EventStore
    from src.events.opportunity_event import make_opportunity_event
    from src.events.reactor import _build_day0_posterior_redecision_events

    city = cities_by_name[city_name]
    source = fast.fast_obs_source_for_city(city, "2026-10-03")
    assert source is not None and source.margin_units == 0
    conn = _world_conn()
    observed = datetime(2026, 10, 3, 4, tzinfo=UTC)
    available = observed + timedelta(minutes=2, seconds=33, microseconds=916532)
    decision = observed + timedelta(minutes=5)
    reports = tuple(
        MetarReport(
            city.wu_station, observed - timedelta(hours=13-index), None,
            float(14 + index * 11 // 13), "METAR",
            f"METAR {city.wu_station} {(observed - timedelta(hours=13-index)):%d%H%M}Z 00000KT 9999 {14 + index * 11 // 13:02d}/12 Q1016",
            available, fast.AVAILABILITY_LOCAL_FIRST_SEEN_AFTER_COMPLETE_RESPONSE,
            fast.KMA_METAR_TRANSPORT_ID,
        )
        for index in range(14)
    )
    prior = reports[-2]
    conn.execute(
        "INSERT INTO observation_prints "
        "(city,station_id,source_channel,publish_ts_utc,value_native,unit,raw_report,fetched_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (city.name, city.wu_station, FAST_OBS_SOURCE_ID,
         (prior.obs_time + timedelta(minutes=4)).isoformat(), prior.temp_c, "C",
         prior.raw, (prior.obs_time + timedelta(minutes=4, seconds=1)).isoformat()),
    )
    prefetch = fast.FastObsPrefetch(
        eligible=((city, source, "2026-10-03"),), reports=reports,
        freshness_status=fast.FETCH_FRESH, cache_age_s=0.0,
        decision_time=observed + timedelta(minutes=2, seconds=58, microseconds=567939),
        ledger_reports=(), station_statuses=((city.wu_station, fast.FETCH_FRESH, 0.0),),
        event_reports=reports,
    )
    if with_raw:
        assert fast.Day0FastObsEmitter().emit_prefetched(
            world_conn=conn, prefetch=prefetch,
            received_at=prefetch.decision_time.isoformat(), persist_ledger=False,
        ) == 2
    posterior = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY", entity_key=f"{city.name}|2026-10-03|{metric}|729837",
        source="test_posterior", observed_at=observed.isoformat(),
        available_at="2026-10-03T04:03:04.293103+00:00",
        received_at="2026-10-03T04:03:37.571338+00:00",
        payload={"city": city.name, "target_date": "2026-10-03", "metric": metric},
        causal_snapshot_id="posterior729837",
    )
    bridge = _build_day0_posterior_redecision_events(
        conn, (posterior,), day0_families={(city.name, "2026-10-03", metric)},
        received_at=posterior.received_at,
    )
    for event in bridge:
        EventStore(conn).insert_or_ignore(event)
    return conn, city, observed, available, decision, prefetch, bridge


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric,expected", (("high", 25.0), ("low", 14.0)))
def test_kma_raw_source_clock_survives_real_posterior_bridge(city_name, metric, expected):
    import src.data.day0_fast_obs as fast
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    conn, city, observed, available, decision, _prefetch, bridge = _kma_source_clock_context(city_name, metric)
    try:
        assert len(bridge) == 1 and bridge[0].source == "day0_posterior_advanced"
        assert datetime.fromisoformat(bridge[0].available_at) > available
        assert json.loads(bridge[0].payload_json)["observation_available_at"] == available.isoformat()
        state = fast._latest_kma_day0_event_state(
            conn, city=city, target_date="2026-10-03", decision_time=decision, metric=metric,
        )
        assert state is not None
        assert (state.high_native, state.low_native, len(state.reports)) == (25.0, 14.0, 14)
        assert state.observed_at == observed and state.available_at == available
        assert state.event_id != bridge[0].event_id
        assert fast.latest_fast_station_extreme_c(
            conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision,
        ) == (expected, observed.isoformat(), 14, "C")
        context = fast.read_noaa_fast_obs_context_from_ledger(
            world_conn=conn, city=city, target_date="2026-10-03", decision_time=decision,
        )
        assert context is not None
        assert (context.current_temp, context.high_so_far, context.low_so_far, context.sample_count) == (25.0, 25.0, 14.0, 14)
        assert context.observation_time == observed.isoformat()
        assert context.observation_available_at == available.isoformat()
        current = read_day0_current_temperature_state(conn=conn, city=city, target_date="2026-10-03", decision_time=decision)
        assert current is not None and current.value_native == 25.0 and current.observed_at == observed
        fact = _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-03", temperature_metric=metric, decision_time=decision)
        assert fact is not None and fact["observed_extreme_native"] == expected
        assert fact["observation_time"] == observed.isoformat()
        assert fact["observation_available_at"] == available.isoformat()
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("fault", (
    "missing_available", "unknown_available", "future_available", "regressed_available",
    "invented_available", "missing_received", "unknown_received", "naive_received",
    "foreign_station", "foreign_raw_station", "foreign_date", "foreign_unit",
    "unknown_source", "malformed_window", "foreign_observation_clock", "future_first_seen",
    "missing_transport", "wrong_transport",
    "missing_transport_malformed_window",
))
def test_kma_latest_invalid_raw_is_unknown_never_older_fallback(city_name, metric, fault):
    from dataclasses import replace
    from src.data import day0_fast_obs as fast
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.events.event_store import EventStore
    from src.events.opportunity_event import make_opportunity_event

    conn, city, observed, available, decision, _prefetch, _bridge = _kma_source_clock_context(city_name, metric)
    try:
        raw = conn.execute(
            "SELECT payload_json FROM opportunity_events WHERE source='day0_extreme_updated_trigger' "
            "AND json_extract(payload_json, '$.metric')=? LIMIT 1", (metric,),
        ).fetchone()
        payload = json.loads(raw[0])
        source = "day0_extreme_updated_trigger"
        row_available = available.isoformat()
        received = "2026-10-03T04:04:00.100001+00:00"
        if fault == "missing_available":
            payload.pop("observation_available_at")
        elif fault == "unknown_available":
            row_available = payload["observation_available_at"] = "UNKNOWN"
        elif fault in {"future_available", "regressed_available", "invented_available"}:
            clock = decision + timedelta(seconds=1) if fault == "future_available" else available - timedelta(seconds=1) if fault == "regressed_available" else available + timedelta(seconds=1)
            row_available = payload["observation_available_at"] = clock.isoformat()
        elif fault == "missing_received":
            received = ""
        elif fault == "unknown_received":
            received = "UNKNOWN"
        elif fault == "naive_received":
            received = "2026-10-03T04:04:00.100001"
        elif fault == "foreign_station":
            payload["station_id"] = "RKSI" if city.wu_station == "RKPK" else "RKPK"
        elif fault == "foreign_raw_station":
            item = payload["kma_report_window"][-1]
            item["raw_report"] = item["raw_report"].replace(city.wu_station, "KAAA")
            item["raw_report_identity"] = fast._normalized_raw_report_identity(item["raw_report"])
        elif fault == "foreign_date":
            item = payload["kma_report_window"][-1]
            item["raw_report"] = item["raw_report"].replace("030400Z", "020400Z")
            item["raw_report_identity"] = fast._normalized_raw_report_identity(item["raw_report"])
        elif fault == "foreign_unit":
            payload["settlement_unit"] = "F"
        elif fault == "unknown_source":
            source = "unproven_source"
        elif fault == "malformed_window":
            payload["kma_report_window"] = []
        elif fault == "foreign_observation_clock":
            payload["observation_time"] = (observed - timedelta(hours=1)).isoformat()
        elif fault == "future_first_seen":
            payload["kma_report_window"][-1]["first_seen_at"] = (decision + timedelta(seconds=1)).isoformat()
        elif fault == "missing_transport":
            payload.pop("observation_transport")
        elif fault == "wrong_transport":
            payload["observation_transport"] = "unproven_transport"
        elif fault == "missing_transport_malformed_window":
            payload.pop("observation_transport")
            payload["kma_report_window"] = {"malformed": True}
        event = make_opportunity_event(
            event_type="DAY0_EXTREME_UPDATED", entity_key=f"{city.name}|{metric}|{fault}",
            source=source, observed_at=observed.isoformat(), available_at=available.isoformat(),
            received_at="2026-10-03T04:04:00.100001+00:00", payload=payload,
        )
        # Bypass only construction-time clock validation to inspect existing
        # damaged canonical rows; append-only persistence stays intact.
        EventStore(conn).insert_or_ignore(replace(event, available_at=row_available, received_at=received))
        with pytest.raises(fast.KmaObservationUnavailable):
            fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=decision, metric=metric)
        assert fast.latest_fast_station_extreme_c(conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) is None
        assert fast.read_noaa_fast_obs_context_from_ledger(world_conn=conn, city=city, target_date="2026-10-03", decision_time=decision) is None
        assert read_day0_current_temperature_state(conn=conn, city=city, target_date="2026-10-03", decision_time=decision) is None
        assert _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-03", temperature_metric=metric, decision_time=decision) is None
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric", ("high", "low"))
def test_kma_genuine_absence_preserves_awc_and_derived_only_is_unavailable(city_name, metric):
    import src.data.day0_fast_obs as fast
    from src.events.event_store import EventStore
    conn, city, _observed, _available, decision, _prefetch, bridge = _kma_source_clock_context(city_name, metric, with_raw=False)
    try:
        assert bridge == []
        assert fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=decision, metric=metric) is None
        assert fast.latest_fast_station_extreme_c(conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) is not None
    finally:
        conn.close()
    raw_conn, city, _observed, _available, decision, _prefetch, bridge = _kma_source_clock_context(city_name, metric)
    conn = _world_conn()
    try:
        EventStore(conn).insert_or_ignore(bridge[0])
        with pytest.raises(fast.KmaObservationUnavailable, match="ORIGINAL_SOURCE_EVENT_MISSING"):
            fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=decision, metric=metric)
        assert fast.latest_fast_station_extreme_c(conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) is None
    finally:
        conn.close()
        raw_conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric", ("high", "low"))
def test_kma_received_microseconds_choose_latest_raw_and_cut_off_future(city_name, metric):
    from src.data import day0_fast_obs as fast
    from src.events.event_store import EventStore
    from src.events.opportunity_event import make_opportunity_event

    conn, city, observed, available, decision, _prefetch, _bridge = _kma_source_clock_context(city_name, metric)
    try:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM opportunity_events WHERE source='day0_extreme_updated_trigger' "
            "AND json_extract(payload_json, '$.metric')=?", (metric,),
        ).fetchone()[0])
        older = make_opportunity_event(
            event_type="DAY0_EXTREME_UPDATED", entity_key=f"{city.name}|{metric}|older",
            source="day0_extreme_updated_trigger", observed_at=observed.isoformat(),
            available_at=available.isoformat(), received_at="2026-10-03T04:04:00.100001+00:00",
            payload=payload,
        )
        newer = make_opportunity_event(
            event_type="DAY0_EXTREME_UPDATED", entity_key=f"{city.name}|{metric}|newer",
            source="day0_extreme_updated_trigger", observed_at=observed.isoformat(),
            available_at=available.isoformat(), received_at="2026-10-03T04:04:00.100002+00:00",
            payload={**payload, "kma_report_window": []},
        )
        EventStore(conn).insert_or_ignore(newer)
        EventStore(conn).insert_or_ignore(older)  # Later append must not beat the later receipt.
        before_new = datetime(2026, 10, 3, 4, 4, 0, 100001, tzinfo=UTC)
        state = fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=before_new, metric=metric)
        assert state is not None and state.event_id == older.event_id
        with pytest.raises(fast.KmaObservationUnavailable):
            fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=decision, metric=metric)
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric", ("high", "low"))
def test_kma_unavailable_is_station_scoped_and_new_raw_delta_resets(city_name, metric):
    from dataclasses import replace
    from src.config import cities_by_name
    from src.data import day0_fast_obs as fast
    from src.events.event_store import EventStore
    from src.events.opportunity_event import make_opportunity_event

    conn, city, observed, available, decision, prefetch, bridge = _kma_source_clock_context(city_name, metric)
    try:
        damaged = make_opportunity_event(
            event_type="DAY0_EXTREME_UPDATED", entity_key=f"{city.name}|{metric}|damaged",
            source="day0_extreme_updated_trigger", observed_at=observed.isoformat(),
            available_at=bridge[0].available_at, received_at="2026-10-03T04:04:00+00:00",
            payload=json.loads(bridge[0].payload_json),
        )
        EventStore(conn).insert_or_ignore(damaged)  # Raw type carrying a derived clock is invalid.
        assert fast.latest_fast_station_extreme_c(conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) is None
        other = cities_by_name["Seoul" if city.name == "Busan" else "Busan"]
        other_source = fast.fast_obs_source_for_city(other, "2026-10-03")
        assert other_source is not None
        other_report = replace(prefetch.reports[-1], station_id=other.wu_station,
                               raw=prefetch.reports[-1].raw.replace(city.wu_station, other.wu_station),
                               raw_report_identity="")
        emitter = fast.Day0FastObsEmitter()
        no_delta = replace(prefetch, eligible=(*prefetch.eligible, (other, other_source, "2026-10-03")),
                           reports=(other_report,), event_reports=(other_report,),
                           station_statuses=((other.wu_station, fast.FETCH_FRESH, 0.0),))
        assert emitter.emit_prefetched(world_conn=conn, prefetch=no_delta, received_at=decision.isoformat(), persist_ledger=False) == 2
        assert fast.latest_fast_station_extreme_c(conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) is None
        new_obs = observed + timedelta(minutes=30)
        new_available = observed + timedelta(minutes=31)
        new_report = replace(prefetch.reports[-1], obs_time=new_obs, first_seen_at=new_available,
                             raw=prefetch.reports[-1].raw.replace("030400Z", "030430Z"), raw_report_identity="")
        refreshed = replace(prefetch, reports=(*prefetch.reports, new_report), event_reports=(new_report,),
                            decision_time=observed + timedelta(minutes=32))
        assert emitter.emit_prefetched(world_conn=conn, prefetch=refreshed, received_at=refreshed.decision_time.isoformat(), persist_ledger=False) == 2
        state = fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=refreshed.decision_time, metric=metric)
        assert state is not None and state.observed_at == new_obs and state.available_at == new_available
        assert (state.high_native, state.low_native, len(state.reports)) == (25.0, 14.0, 15)
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric,expected", (("high", 25.0), ("low", 14.0)))
def test_kma_real_bridge_reaches_held_q_guard_and_value_drift_still_rejects(city_name, metric, expected):
    from src.data import day0_fast_obs as fast
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.engine.event_reactor_adapter import _global_day0_execution_payload

    conn, city, observed, _available, decision, _prefetch, bridge = _kma_source_clock_context(city_name, metric)
    try:
        # Original matching products establish a real residual witness, outside
        # the current local-day KMA window. No probability guard is mocked.
        for index in range(fast.FAST_RESIDUAL_MIN_PAIRS):
            timestamp = observed - timedelta(hours=24 + index)
            raw = f"METAR {city.wu_station} {timestamp:%d%H%M}Z 00000KT 9999 14/12 Q1016"
            for channel in (FAST_OBS_SOURCE_ID, f"noaa_wrh_{city.wu_station.lower()}"):
                conn.execute(
                    "INSERT INTO observation_prints "
                    "(city,station_id,source_channel,publish_ts_utc,value_native,unit,raw_report,fetched_at_utc) VALUES (?,?,?,?,?,?,?,?)",
                    (city.name, city.wu_station, channel, (timestamp + timedelta(seconds=30)).isoformat(),
                     14.0, "C", raw, (timestamp + timedelta(minutes=1)).isoformat()),
                )
        likelihood = fast.build_fast_station_residual_likelihood(
            conn, city=city.name, target_date="2026-10-03", metric=metric,
            observed_source=FAST_OBS_SOURCE_ID, observation_time=observed, decision_time=decision,
        )
        assert likelihood is not None
        residual = likelihood.as_payload()
        residual["scenario_weights"] = [{"observed_bound_c": expected, "weight": 1.0}]
        conditioning = {
            "active": True, "metric": metric, "unit": "C", "source": FAST_OBS_SOURCE_ID,
            "observed_extreme_c": expected, "observation_time": observed.isoformat(),
            "sample_count": 14, "fast_residual_likelihood": residual,
        }
        fact = _latest_authorized_day0_fact(conn, city=city.name, target_date="2026-10-03", temperature_metric=metric, decision_time=decision)
        assert fact is not None
        kwargs = dict(
            family=SimpleNamespace(city=city.name, target_date="2026-10-03", metric=metric),
            resolution=SimpleNamespace(measurement_unit="C", station_id=city.wu_station),
            conditioning=conditioning, observation_conn=conn, decision_time=decision,
            posterior_id=729837, current_day0_facts=(None, fact),
            allow_equivalent_conditioning_clock_advance=True,
        )
        held = _global_day0_execution_payload(bridge[0], **kwargs)
        assert held["_edli_global_day0_binding"]["observed_extreme_native"] == expected
        with pytest.raises(ValueError, match="GLOBAL_DAY0_FAST_RESIDUAL_CURRENT_OBSERVATION_MISMATCH"):
            _global_day0_execution_payload(bridge[0], **{**kwargs, "conditioning": {**conditioning, "observed_extreme_c": expected + 1.0}})
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric,expected", (("high", 25.0), ("low", 14.0)))
def test_kma_late_interior_ledger_report_keeps_original_source_clock(city_name, metric, expected):
    from src.data import day0_fast_obs as fast
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state

    conn, city, observed, available, decision, _prefetch, bridge = _kma_source_clock_context(city_name, metric)
    try:
        interior = observed - timedelta(minutes=30)
        publication = observed + timedelta(minutes=4)
        conn.execute(
            "INSERT INTO observation_prints "
            "(city,station_id,source_channel,publish_ts_utc,value_native,unit,raw_report,fetched_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (city.name, city.wu_station, FAST_OBS_SOURCE_ID, publication.isoformat(), 23.0, "C",
             f"METAR {city.wu_station} {interior:%d%H%M}Z 00000KT 9999 23/12 Q1016",
             (publication + timedelta(seconds=1)).isoformat()),
        )
        state = fast._latest_kma_day0_event_state(conn, city=city, target_date="2026-10-03", decision_time=decision, metric=metric)
        assert state is not None and state.observed_at == observed
        assert (state.high_native, state.low_native, len(state.reports)) == (25.0, 14.0, 15)
        assert state.available_at == publication
        original = conn.execute("SELECT available_at FROM opportunity_events WHERE event_id=?", (state.event_id,)).fetchone()[0]
        assert original == available.isoformat()
        assert json.loads(bridge[0].payload_json)["observation_available_at"] == available.isoformat()
        assert fast.latest_fast_station_extreme_c(conn, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) == (expected, observed.isoformat(), 15, "C")
        current = read_day0_current_temperature_state(conn=conn, city=city, target_date="2026-10-03", decision_time=decision)
        assert current is not None and current.value_native == 25.0 and current.observed_at == observed
    finally:
        conn.close()


@pytest.mark.parametrize("city_name", ("Busan", "Seoul"))
@pytest.mark.parametrize("metric", ("high", "low"))
def test_kma_source_query_failure_is_unknown_not_absence(city_name, metric):
    from src.data import day0_fast_obs as fast
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    conn, city, _observed, _available, decision, _prefetch, _bridge = _kma_source_clock_context(city_name, metric)

    class InterruptedSourceRead:
        def execute(self, sql, *args):
            if "MAX(CASE WHEN source" in sql:
                raise sqlite3.OperationalError("interrupted")
            return conn.execute(sql, *args)

    interrupted = InterruptedSourceRead()
    try:
        with pytest.raises(fast.KmaObservationUnavailable, match="SOURCE_QUERY_UNAVAILABLE"):
            fast._latest_kma_day0_event_state(interrupted, city=city, target_date="2026-10-03", decision_time=decision, metric=metric)
        assert fast.latest_fast_station_extreme_c(interrupted, city=city.name, target_date="2026-10-03", metric=metric, decision_time=decision) is None
        assert fast.read_noaa_fast_obs_context_from_ledger(world_conn=interrupted, city=city, target_date="2026-10-03", decision_time=decision) is None
        assert read_day0_current_temperature_state(conn=interrupted, city=city, target_date="2026-10-03", decision_time=decision) is None
        assert _latest_authorized_day0_fact(interrupted, city=city.name, target_date="2026-10-03", temperature_metric=metric, decision_time=decision) is None
    finally:
        conn.close()


def test_kma_mixed_window_uses_latest_contributing_noaa_clock() -> None:
    _availability, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()
    city = _seoul()
    kma = MetarReport(
        station_id="RKSI", obs_time=datetime(2026, 9, 22, 4, 0, tzinfo=UTC),
        receipt_time=None, temp_c=28.0, metar_type="METAR",
        raw="METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        first_seen_at=datetime(2026, 9, 22, 4, 1, tzinfo=UTC),
        availability_basis=_availability,
        transport_id=transport_id,
    )
    noaa = MetarReport(
        station_id="RKSI", obs_time=datetime(2026, 9, 22, 4, 30, tzinfo=UTC),
        receipt_time=datetime(2026, 9, 22, 4, 35, tzinfo=UTC), temp_c=30.0,
        metar_type="METAR", raw="METAR RKSI 220430Z 05016KT 9999 FEW050 30/14 Q1016",
        transport_id="noaa_ledger",
    )
    extremes = running_extremes_for_local_day(
        [kma, noaa], city=city, target_date="2026-09-22",
        as_of=datetime(2026, 9, 22, 5, 0, tzinfo=UTC), margin_units=0.0,
    )
    assert extremes.high_so_far == 30.0
    assert extremes.current_temp == 30.0
    assert extremes.last_obs_time == noaa.obs_time
    assert extremes.last_available_at == noaa.receipt_time


def test_kma_extrema_canonicalization_is_station_local() -> None:
    _availability, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()
    observed = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
    first_seen = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
    kma_other_station = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=25.0,
        metar_type="METAR", raw="METAR RKSI 230000Z 25/20",
        first_seen_at=first_seen, availability_basis=_availability,
        transport_id=transport_id,
    )
    rpll_a = MetarReport(
        station_id="RPLL", obs_time=observed, receipt_time=observed, temp_c=29.0,
        metar_type="METAR", raw="RPLL 230000Z 29/26",
    )
    rpll_b = MetarReport(
        station_id="RPLL", obs_time=observed, receipt_time=observed, temp_c=30.0,
        metar_type="METAR", raw="RPLL 230000Z 30/26",
    )
    epwa = MetarReport(
        station_id="EPWA", obs_time=observed, receipt_time=observed, temp_c=18.0,
        metar_type="METAR", raw="EPWA 230000Z 18/10",
    )
    warsaw = SimpleNamespace(
        name="Warsaw", timezone="Europe/Warsaw", settlement_unit="C", wu_station="EPWA",
    )
    manila = SimpleNamespace(
        name="Manila", timezone="Asia/Manila", settlement_unit="C", wu_station="RPLL",
    )

    extremes = running_extremes_for_local_day(
        [kma_other_station, rpll_a, rpll_b, epwa],
        city=warsaw, target_date="2026-09-23",
        as_of=datetime(2026, 9, 23, 2, 0, tzinfo=UTC),
    )
    assert extremes.sample_count == 1
    assert extremes.high_so_far == 18.0
    assert extremes.low_so_far == 18.0
    legacy_only = running_extremes_for_local_day(
        [kma_other_station, rpll_a, rpll_b, epwa],
        city=manila, target_date="2026-09-23",
        as_of=datetime(2026, 9, 23, 2, 0, tzinfo=UTC),
    )
    assert legacy_only.high_so_far == 30.0
    assert legacy_only.low_so_far == 29.0


def test_kma_extrema_keeps_same_station_conflict_and_cor_precedence() -> None:
    import src.data.day0_fast_obs as fast_obs
    _availability, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()
    observed = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
    first_seen = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
    seoul = SimpleNamespace(
        name="Seoul", timezone="Asia/Seoul", settlement_unit="C", wu_station="RKSI",
    )
    warsaw = SimpleNamespace(
        name="Warsaw", timezone="Europe/Warsaw", settlement_unit="C", wu_station="EPWA",
    )
    bad_a = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=25.0,
        metar_type="COR", raw="METAR COR RKSI 230000Z 25/20",
        first_seen_at=first_seen, availability_basis=_availability,
        transport_id=transport_id,
    )
    bad_b = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=26.0,
        metar_type="COR", raw="METAR COR RKSI 230000Z 26/20",
        first_seen_at=first_seen, availability_basis=_availability,
        transport_id=transport_id,
    )
    epwa = MetarReport(
        station_id="EPWA", obs_time=observed, receipt_time=observed, temp_c=18.0,
        metar_type="METAR", raw="EPWA 230000Z 18/10",
    )

    with pytest.raises(fast_obs.KmaObservationConflict):
        running_extremes_for_local_day(
            [bad_a, bad_b, epwa], city=seoul, target_date="2026-09-23",
            as_of=datetime(2026, 9, 23, 2, 0, tzinfo=UTC),
        )
    extremes = running_extremes_for_local_day(
        [bad_a, bad_b, epwa], city=warsaw, target_date="2026-09-23",
        as_of=datetime(2026, 9, 23, 2, 0, tzinfo=UTC),
    )
    assert extremes.high_so_far == 18.0

    base = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=25.0,
        metar_type="METAR", raw="METAR RKSI 230000Z 25/20",
        first_seen_at=first_seen, availability_basis=_availability,
        transport_id=transport_id,
    )
    correction = MetarReport(
        station_id="RKSI", obs_time=observed, receipt_time=None, temp_c=26.0,
        metar_type="COR", raw="METAR COR RKSI 230000Z 26/20",
        first_seen_at=first_seen, availability_basis=_availability,
        transport_id=transport_id,
    )
    corrected = running_extremes_for_local_day(
        [base, correction], city=seoul, target_date="2026-09-23",
        as_of=datetime(2026, 9, 23, 2, 0, tzinfo=UTC),
    )
    assert corrected.high_so_far == 26.0


def test_kma_conflict_station_does_not_block_other_station_emit() -> None:
    import src.data.day0_fast_obs as fast_obs
    _availability, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()
    seoul = _seoul()
    busan = SimpleNamespace(
        name="Busan", timezone="Asia/Seoul", settlement_unit="C",
        wu_station="RKPK", settlement_source_type="noaa",
    )
    source_seoul = fast_obs.fast_obs_source_for_city(seoul, target_date="2026-09-22")
    source_busan = fast_obs.fast_obs_source_for_city(busan, target_date="2026-09-22")
    assert source_seoul is not None and source_busan is not None
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    first_seen = datetime(2026, 9, 22, 4, 1, tzinfo=UTC)
    bad1 = MetarReport("RKSI", observed, None, 28.0, "COR", "METAR COR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016", first_seen, _availability, transport_id)
    bad2 = MetarReport("RKSI", observed, None, 29.0, "COR", "METAR COR RKSI 220400Z 05016KT 9999 FEW050 29/14 Q1016", first_seen, _availability, transport_id)
    good = MetarReport("RKPK", observed, None, 28.0, "METAR", "METAR RKPK 220400Z 05016KT 9999 FEW050 28/14 Q1016", first_seen, _availability, transport_id)
    conflict = fast_obs.KmaObservationConflict(
        "conflict", station_id="RKSI", obs_time=observed,
        raw_reports=(bad1.raw, bad2.raw), correction_rank="COR", reports=(bad1, bad2),
    )
    prefetch = fast_obs.FastObsPrefetch(
        eligible=((seoul, source_seoul, "2026-09-22"), (busan, source_busan, "2026-09-22")),
        reports=(good,), freshness_status=fast_obs.FETCH_FRESH, cache_age_s=0.0,
        decision_time=datetime(2026, 9, 22, 4, 5, tzinfo=UTC), ledger_reports=(),
        station_statuses=(("RKSI", fast_obs.FETCH_FRESH, 0.0), ("RKPK", fast_obs.FETCH_FRESH, 0.0)),
        event_reports=(good,), kma_conflicts=(("RKSI", conflict),),
    )
    conn = _world_conn()
    try:
        assert fast_obs.Day0FastObsEmitter().emit_prefetched(
            world_conn=conn, prefetch=prefetch,
            received_at="2026-09-22T04:05:00+00:00", persist_ledger=False,
        ) >= 2
        rows = conn.execute("SELECT payload_json FROM opportunity_events").fetchall()
        assert any('"observation_conflict"' in row[0] for row in rows)
        assert any('"city":"Busan"' in row[0] for row in rows)
    finally:
        conn.close()


def test_kma_complete_cor_response_recovers_prior_durable_conflict() -> None:
    """Only a same-instant COR from this emitter's fresh response can recover."""
    import json
    import src.data.day0_fast_obs as fast_obs

    availability_basis, transport_id, _state_cls, _cursor_cls, _parse = _kma_contract_symbols()
    city = _seoul()
    source = fast_obs.fast_obs_source_for_city(city)
    assert source is not None
    busan = SimpleNamespace(
        name="Busan", timezone="Asia/Seoul", settlement_unit="C",
        wu_station="RKPK", settlement_source_type="noaa",
    )
    busan_source = fast_obs.fast_obs_source_for_city(busan)
    assert busan_source is not None
    observed = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    first_seen = datetime(2026, 9, 22, 4, 1, tzinfo=UTC)
    decision = datetime(2026, 9, 22, 4, 5, tzinfo=UTC)
    bad_one = MetarReport(
        "RKSI", observed, None, 28.0, "COR",
        "METAR COR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
        first_seen, availability_basis, transport_id,
    )
    bad_two = MetarReport(
        "RKSI", observed, None, 29.0, "COR",
        "METAR COR RKSI 220400Z 05016KT 9999 FEW050 29/14 Q1016",
        first_seen, availability_basis, transport_id,
    )
    conflict = fast_obs.KmaObservationConflict(
        "conflicting COR", station_id="RKSI", obs_time=observed,
        raw_reports=(bad_one.raw, bad_two.raw), correction_rank="COR",
        reports=(bad_one, bad_two),
    )
    emitter = fast_obs.Day0FastObsEmitter()
    conn = _world_conn()
    try:
        conflict_prefetch = fast_obs.FastObsPrefetch(
            eligible=((city, source, "2026-09-22"),), reports=(),
            freshness_status=fast_obs.FETCH_FRESH, cache_age_s=0.0,
            decision_time=decision, ledger_reports=None,
            station_statuses=(("RKSI", fast_obs.FETCH_FRESH, 0.0),),
            kma_conflicts=(("RKSI", conflict),),
        )
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=conflict_prefetch,
            received_at="2026-09-22T04:02:00+00:00", persist_ledger=False,
        ) == 2

        corrected = MetarReport(
            "RKSI", observed, None, 28.0, "COR",
            "METAR COR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
            decision, availability_basis, transport_id,
        )
        normal = MetarReport(
            "RKSI", observed, None, 28.0, "METAR",
            "METAR RKSI 220400Z 05016KT 9999 FEW050 28/14 Q1016",
            decision, availability_basis, transport_id,
        )
        busan_report = MetarReport(
            "RKPK", observed, None, 27.0, "METAR",
            "METAR RKPK 220400Z 05016KT 9999 FEW050 27/14 Q1016",
            decision, availability_basis, transport_id,
        )
        normal_prefetch = fast_obs.FastObsPrefetch(
            eligible=(
                (city, source, "2026-09-22"),
                (busan, busan_source, "2026-09-22"),
            ),
            reports=(normal, busan_report),
            freshness_status=fast_obs.FETCH_FRESH, cache_age_s=0.0,
            decision_time=decision, ledger_reports=(),
            station_statuses=(
                ("RKSI", fast_obs.FETCH_FRESH, 0.0),
                ("RKPK", fast_obs.FETCH_FRESH, 0.0),
            ),
            event_reports=(normal, busan_report),
        )
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=normal_prefetch,
            received_at="2026-09-22T04:05:00+00:00", persist_ledger=False,
        ) == 2
        assert any(
            '"city":"Busan"' in row[0]
            for row in conn.execute("SELECT payload_json FROM opportunity_events")
        )

        later = MetarReport(
            "RKSI", observed + timedelta(hours=1), None, 30.0, "METAR",
            "METAR RKSI 220500Z 05016KT 9999 FEW050 30/14 Q1016",
            observed + timedelta(hours=1, minutes=1), availability_basis, transport_id,
        )
        busan_later = MetarReport(
            "RKPK", observed + timedelta(hours=1), None, 28.0, "METAR",
            "METAR RKPK 220500Z 05016KT 9999 FEW050 28/14 Q1016",
            observed + timedelta(hours=1, minutes=1), availability_basis, transport_id,
        )
        cached_cor_prefetch = fast_obs.FastObsPrefetch(
            eligible=(
                (city, source, "2026-09-22"),
                (busan, busan_source, "2026-09-22"),
            ),
            reports=(corrected, later, busan_later),
            freshness_status=fast_obs.FETCH_FRESH, cache_age_s=0.0,
            decision_time=observed + timedelta(hours=1, minutes=2), ledger_reports=(),
            station_statuses=(
                ("RKSI", fast_obs.FETCH_FRESH, 0.0),
                ("RKPK", fast_obs.FETCH_FRESH, 0.0),
            ),
            event_reports=(later, busan_later),
        )
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=cached_cor_prefetch,
            received_at="2026-09-22T05:03:00+00:00", persist_ledger=False,
        ) == 2

        corrected_prefetch = fast_obs.FastObsPrefetch(
            eligible=((city, source, "2026-09-22"),), reports=(corrected,),
            freshness_status=fast_obs.FETCH_FRESH, cache_age_s=0.0,
            decision_time=decision, ledger_reports=(),
            station_statuses=(("RKSI", fast_obs.FETCH_FRESH, 0.0),),
            event_reports=(corrected,),
        )
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=corrected_prefetch,
            received_at="2026-09-22T04:05:00+00:00", persist_ledger=False,
        ) == 2
        payloads = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM opportunity_events"
        ).fetchall()]
        recovered = [
            payload for payload in payloads
            if payload.get("city") == "Seoul"
            and payload.get("observation_transport") == transport_id
            and payload.get("observation_conflict") is None
        ]
        assert len(recovered) == 2
        assert all(
            payload["current_observation_raw_report"].startswith("METAR COR RKSI")
            for payload in recovered
        )
    finally:
        conn.close()


# ===========================================================================
# Local-day windows end at the next LOCAL midnight, not 24 h after the start
# ===========================================================================

# Europe/Helsinki DST days: (target date, local-day start Z, next local midnight Z, hours).
# Spring 2026-03-29 skips 03:00 -> 04:00 (23 h); autumn 2026-10-25 repeats 03:00-04:00 (25 h).
_HELSINKI_DST_DAYS = (
    ("2026-03-29", datetime(2026, 3, 28, 22, 0, tzinfo=UTC), datetime(2026, 3, 29, 21, 0, tzinfo=UTC), 23),
    ("2026-10-25", datetime(2026, 10, 24, 21, 0, tzinfo=UTC), datetime(2026, 10, 25, 22, 0, tzinfo=UTC), 25),
)


@pytest.mark.parametrize("target_date,start,end,hours", _HELSINKI_DST_DAYS)
def test_helsinki_dst_day_fixture_matches_the_zone_database(target_date, start, end, hours):
    tz = ZoneInfo("Europe/Helsinki")
    day = date.fromisoformat(target_date)
    assert datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC) == start
    assert datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(UTC) == end
    assert end - start == timedelta(hours=hours)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("target_date,start,end,hours", _HELSINKI_DST_DAYS)
def test_fast_residual_settlement_extreme_window_is_the_local_calendar_day(
    monkeypatch, target_date, start, end, hours, metric,
) -> None:
    from src import config as config_module
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    monkeypatch.setitem(
        config_module.cities_by_name,
        "Helsinki DST",
        SimpleNamespace(
            settlement_source_type="wu_icao",
            wu_station="TEST",
            settlement_unit="C",
            timezone="Europe/Helsinki",
        ),
    )
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    sign = 1.0 if metric == "high" else -1.0

    def put(channel, at, value, raw=""):
        assert append_print(
            conn, city="Helsinki DST", station_id="TEST", source_channel=channel,
            publish_ts_utc=at.isoformat(), value_native=value, unit="C",
            fetched_at_utc=(at + timedelta(minutes=1)).isoformat(), raw_report=raw,
        )

    # 21 matched settlement/fast pairs, all inside the local day, residual 0.
    for k in range(21):
        at = start + timedelta(hours=k)
        put("wu_icao_history", at, 5.0)
        put(FAST_OBS_SOURCE_ID, at, 5.0, f"TEST {at:%d%H%M}Z 05/00")
    # Settlement prints straddling both edges of the local day.  Only the
    # last local minute is inside; the next local midnight itself is outside.
    put("wu_icao_history", start - timedelta(minutes=1), sign * 98.0)
    put("wu_icao_history", end - timedelta(minutes=1), sign * 50.0)
    put("wu_icao_history", end, sign * 99.0)

    cutoff = end + timedelta(hours=2)
    likelihood = build_fast_station_residual_likelihood(
        conn,
        city="Helsinki DST",
        target_date=target_date,
        metric=metric,
        observed_source=FAST_OBS_SOURCE_ID,
        observation_time=cutoff,
        decision_time=cutoff + timedelta(minutes=5),
    )
    assert likelihood is not None
    assert likelihood.matched_pairs == 21
    assert likelihood.settlement_extreme_c == sign * 50.0, (
        f"{target_date} is {hours} h: the window must close at {end.isoformat()}"
    )


@pytest.mark.parametrize("target_date,start,end,hours", _HELSINKI_DST_DAYS)
def test_ledger_hydration_window_is_the_local_calendar_day(target_date, start, end, hours) -> None:
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    city = SimpleNamespace(name="Helsinki", timezone="Europe/Helsinki")
    source = FastObsSource(source_id=FAST_OBS_SOURCE_ID, station_id="EFHK", authority="test")
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    in_day = (start, start + timedelta(hours=hours // 2), end - timedelta(minutes=1))
    outside = (start - timedelta(minutes=1), end)
    for at in in_day + outside:
        assert append_print(
            conn, city="Helsinki", station_id="EFHK", source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=at.isoformat(), value_native=5.0, unit="C",
            fetched_at_utc=(at + timedelta(minutes=1)).isoformat(),
            raw_report=f"METAR EFHK {at:%d%H%M}Z 05/00",
        )

    emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
    hydrated = emitter.hydrate_from_ledger(conn, ((city, source, target_date),))

    assert hydrated == len(in_day), (
        f"{target_date} is {hours} h: the window must close at {end.isoformat()}"
    )
    assert sorted(report.obs_time for report in emitter._cached_reports) == sorted(in_day)
