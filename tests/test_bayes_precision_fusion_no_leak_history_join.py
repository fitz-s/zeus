# Created: 2026-06-08
# Last reused or audited: 2026-08-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 (causal fixed-lead history; previous-runs
#   for gridded models; positive-lead named-station single-runs exception with local-day cutoff;
#   run_time != source_available_at), §5 (walk-forward, no same-day leak), §7 antibodies
#   ("top-K-uses-target-truth (walk-forward only)", "previous-runs-for-live-decision",
#   "C/F unit mix (settlement-unit residual)"); CONTINUITY_AND_WIRING.md §4 step 4 + IRON
#   RULE #3 (provenance/no-leak). RELATIONSHIP TEST (Fitz: test the cross-module boundary,
#   not the function): the invariant that holds across the raw_model_forecasts ->
#   settlement_outcomes JOIN is what is asserted here, written BEFORE the provider impl.
"""Relationship test for the BAYES_PRECISION_FUSION walk-forward history provider (no-leak JOIN).

The cross-module invariant under test (raw_model_forecasts -> settlement_outcomes):
  (1) NO LEAK: only rows with target_date STRICTLY < decision_date enter the history.
  (2) PROVENANCE GATE: only settlement authority='VERIFIED' rows contribute (UNVERIFIED /
      DISPUTED excluded).
  (3) PHYSICAL/CAUSAL GATE: gridded models train only on a current-equivalent Open-Meteo
      product; single-runs require positive lead plus source/capture before the target local day.
      Station products use their typed role and the same positive-lead causal boundary.
  (4) UNIT COHERENCE: residual = forecast_value_c - settlement_in_C; an F-settlement city's
      settlement_value (degF) is converted to degC before the residual (no C/F mix).
  (5) FAIL-SOFT: the provider NEVER raises; any failure -> {} (anchor fallback / equal-weight).
  (6) CROSSING MIN_TRAIN: with >=25 VERIFIED previous-runs rows per model strictly before the
      decision date, n_train >= MIN_TRAIN so the fusion can reach T2_BAYES.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.forecast.bayes_precision_fusion import MIN_TRAIN
from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
from src.data.bayes_precision_fusion_download import (
    BAYES_PRECISION_FUSION_CELL_SELECTION,
    OPENMETEO_PREVIOUS_RUNS_SOURCE_ID,
    OPENMETEO_PROVIDER,
    PREVIOUS_RUNS_SOURCE_FAMILY,
    SINGLE_RUNS_SOURCE_FAMILY,
    STANDARD_META_STAMPED_SOURCE_FAMILY,
)
from src.data.openmeteo_client import PREVIOUS_RUNS_URL
from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL, STANDARD_FORECAST_URL
from src.state.schema.v2_schema import (
    apply_canonical_schema,
    ensure_replacement_forecast_live_schema,
)


AS_OF = datetime(2026, 7, 1, tzinfo=UTC)
_TEST_CITIES = {
    "Paris": SimpleNamespace(name="Paris", settlement_source_type="wu_icao", previous_settlement_source_type=None, settlement_source_type_effective_date=None, wu_station="LFPG", settlement_unit="C", settlement_page_view="all", lat=48.8566, lon=2.3522, timezone="Europe/Paris"),
    "Taipei": SimpleNamespace(name="Taipei", settlement_source_type="wu_icao", previous_settlement_source_type=None, settlement_source_type_effective_date=None, wu_station="RCSS", settlement_unit="C", settlement_page_view="all", lat=25.0330, lon=121.5654, timezone="Asia/Taipei"),
    "Hong Kong": SimpleNamespace(name="Hong Kong", settlement_source_type="hko", previous_settlement_source_type=None, settlement_source_type_effective_date=None, wu_station=None, settlement_unit="C", settlement_page_view="all", lat=22.3193, lon=114.1694, timezone="Asia/Hong_Kong"),
    "NewYork": SimpleNamespace(name="NewYork", settlement_source_type="wu_icao", previous_settlement_source_type=None, settlement_source_type_effective_date=None, wu_station="KJFK", settlement_unit="F", settlement_page_view="all", lat=40.7128, lon=-74.0060, timezone="America/New_York"),
    "Chicago": SimpleNamespace(name="Chicago", settlement_source_type="noaa", previous_settlement_source_type="wu_icao", settlement_source_type_effective_date="2026-08-23", wu_station="KORD", settlement_unit="F", settlement_page_view="hourly", lat=41.8781, lon=-87.6298, timezone="America/Chicago"),
}


def _provider(conn: sqlite3.Connection, *, as_of: datetime = AS_OF):
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider
    return BayesPrecisionFusionHistoryProvider(conn, as_of=as_of, cities_by_name=_TEST_CITIES)

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    ensure_replacement_forecast_live_schema(conn)
    conn.execute("""CREATE TABLE observations (
        id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, source TEXT,
        station_id TEXT, unit TEXT, data_source_version TEXT,
        high_temp REAL, low_temp REAL, high_fetch_utc TEXT, low_fetch_utc TEXT,
        high_provenance_metadata TEXT, low_provenance_metadata TEXT
    )""")
    return conn


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    model: str,
    city: str,
    target_date: str,
    metric: str,
    forecast_value_c: float,
    endpoint: str = "single_runs",
    lead_days: int = 1,
    source_cycle_time: str | None = None,
    source_available_at: str | None = None,
    captured_at: str | None = None,
    recorded_at: str | None = None,
    endpoint_mode: str | None = None,
    request_params_mutation: dict[str, object] | None = None,
    request_url_hash: str | None = None,
    product_id: str | None = None,
    coverage_status: str = "COVERED",
) -> None:
    city_config = _TEST_CITIES[city]
    expected_model = OPENMETEO_MODEL_IDS.get(model, model)
    endpoint_mode = endpoint_mode or endpoint
    target_start = datetime.combine(
        date.fromisoformat(target_date), datetime.min.time(), tzinfo=ZoneInfo(city_config.timezone),
    ).astimezone(UTC)
    default_source_cycle = (target_start - timedelta(hours=3)).isoformat()
    default_source_available = (target_start - timedelta(hours=2)).isoformat()
    default_captured = (target_start - timedelta(hours=1)).isoformat()
    params: dict[str, object] = {
        "latitude": city_config.lat,
        "longitude": city_config.lon,
        "hourly": "temperature_2m" if endpoint != "previous_runs" or lead_days == 0 else f"temperature_2m_previous_day{lead_days}",
        "models": expected_model,
        "temperature_unit": "celsius",
        "timezone": city_config.timezone,
        "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
    }
    if endpoint == "previous_runs":
        params.update(start_date=target_date, end_date=target_date)
    if endpoint_mode == "standard_api_meta_stamped":
        params["forecast_hours"] = 120
    if request_params_mutation:
        params.update(request_params_mutation)
    source_cycle_time = source_cycle_time or default_source_cycle
    if endpoint == "previous_runs":
        source_id = OPENMETEO_PREVIOUS_RUNS_SOURCE_ID.get(model, f"{model}_previous_runs")
        source_family = PREVIOUS_RUNS_SOURCE_FAMILY
        default_product_id = f"{expected_model}::previous_runs"
        base_url = PREVIOUS_RUNS_URL
    elif endpoint_mode == "standard_api_meta_stamped":
        source_id = f"{model}_standard_meta_stamped"
        source_family = STANDARD_META_STAMPED_SOURCE_FAMILY
        default_product_id = f"{expected_model}::standard_api_meta_stamped::run={source_cycle_time}::modified=2026-03-31T00:01:00+00:00"
        base_url = STANDARD_FORECAST_URL
    else:
        source_id = f"{model}_single_runs"
        source_family = SINGLE_RUNS_SOURCE_FAMILY
        default_product_id = f"{expected_model}::single_runs"
        base_url = SINGLE_RUNS_FORECAST_URL
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    default_hash = hashlib.sha256(f"{base_url}?{canonical_params}".encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint, recorded_at,
             source_id, source_family, product_id, provider, model_name,
             request_params_json, request_url_hash, latitude_requested,
             longitude_requested, timezone_requested, endpoint_mode, coverage_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model, city, target_date, metric,
            source_cycle_time,
            source_available_at or default_source_available,
            captured_at or default_captured,
            lead_days, forecast_value_c, endpoint, recorded_at or default_captured,
            source_id, source_family, product_id or default_product_id,
            OPENMETEO_PROVIDER, expected_model, canonical_params,
            request_url_hash or default_hash, city_config.lat, city_config.lon,
            city_config.timezone, endpoint_mode, coverage_status,
        ),
    )


def _insert_settlement(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    settlement_value: float,
    authority: str = "VERIFIED",
    settlement_unit: str = "C",
    era: str = "internal_resolver_post_2026_02_21",
    observation_fetch_at: str | None = None,
) -> None:
    city_config = _TEST_CITIES[city]
    source_type = city_config.settlement_source_type
    if city == "Chicago" and target_date < "2026-08-23":
        source_type = "wu_icao"
    station = city_config.wu_station
    if source_type == "hko":
        source = "hko_daily_api"
        source_url = "https://www.weather.gov.hk/en/cis/climat.htm"
        data_version = "hko_dailyextract_live_v1"
        outcome_version = source
        source_family = "HKO"
        rounding_rule = "oracle_truncate"
    elif source_type == "noaa":
        source = f"noaa_wrh_{station.lower()}"
        source_url = f"https://www.weather.gov/wrh/timeseries?site={station}"
        data_version = "noaa_wrh_timeseries_v1"
        outcome_version = data_version
        source_family = "NOAA"
        rounding_rule = "wmo_half_up"
    else:
        source = "wu_icao_history"
        source_url = f"https://www.wunderground.com/history/daily/test/{station}"
        data_version = "wu_icao_v1_2026"
        outcome_version = source
        source_family = "WU"
        rounding_rule = "wmo_half_up"
    conn.execute(
        """INSERT INTO observations (
            city, target_date, source, station_id, unit, data_source_version,
            high_temp, low_temp, high_fetch_utc, low_fetch_utc,
            high_provenance_metadata, low_provenance_metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}')""",
        (city, target_date, source, station, settlement_unit, data_version,
         settlement_value, settlement_value,
         observation_fetch_at or (target_date + "T20:00:00+00:00"), observation_fetch_at or (target_date + "T20:00:00+00:00")),
    )
    observation_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    provenance = {
        "obs_id": observation_id, "obs_source": source, "data_version": outcome_version,
        "era": era, "era_start_date_utc": "2026-02-21",
        "source_family": source_family, "settlement_source_type": source_family,
        "rounding_rule": rounding_rule,
    }
    conn.execute(
        """INSERT INTO settlement_outcomes (
            city, target_date, temperature_metric, winning_bin, settlement_value,
            settlement_source, settled_at, authority, provenance_json, recorded_at, settlement_unit
        ) VALUES (?, ?, ?, 'fixture-bin', ?, ?, ?, ?, ?, ?, ?)""",
        (city, target_date, metric, settlement_value, source_url,
         target_date + "T19:00:00+00:00", authority, __import__("json").dumps(provenance),
         target_date + "T21:00:00+00:00", settlement_unit),
    )


def _dates(n: int, *, start: date = date(2026, 4, 1)) -> list[str]:
    from datetime import timedelta
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


# =====================================================================================
# (1) NO LEAK — target_date strictly before the decision date only
# =====================================================================================
def test_history_excludes_target_on_or_after_decision_date() -> None:

    conn = _conn()
    # Three settled days BEFORE decision, plus one ON and one AFTER the decision date.
    for d in ("2026-04-01", "2026-04-02", "2026-04-03"):
        _insert_raw(conn, model="gfs_global", city="Paris", target_date=d, metric="high", forecast_value_c=20.0)
        _insert_settlement(conn, city="Paris", target_date=d, metric="high", settlement_value=19.0)
    # decision date == 2026-04-04: the 04-04 (==) and 04-05 (>) rows must NOT appear (no leak).
    for d in ("2026-04-04", "2026-04-05"):
        _insert_raw(conn, model="gfs_global", city="Paris", target_date=d, metric="high", forecast_value_c=20.0)
        _insert_settlement(conn, city="Paris", target_date=d, metric="high", settlement_value=19.0)

    provider = _provider(conn)
    hist = provider(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 4, 4), models=["gfs_global"],
    )
    gfs = hist["gfs_global"]
    assert gfs.n_train == 3, "only target_date < decision_date rows may enter (no same-day/future leak)"
    # residual = forecast - settlement = 20.0 - 19.0 = 1.0 on each kept row
    assert all(abs(r - 1.0) < 1e-9 for r in gfs.residuals)


def test_history_query_uses_filter_index_and_deterministic_tie_order() -> None:
    """The live read filters before sorting and gives duplicate dates an exact order."""

    conn = _conn()
    for cycle, available, value in (
        ("2026-03-31T00:00:00+00:00", "2026-03-31T12:00:00+00:00", 20.0),
        ("2026-03-31T06:00:00+00:00", "2026-03-31T07:00:00+00:00", 21.0),
    ):
        _insert_raw(
            conn,
            model="gfs_global",
            city="Paris",
            target_date="2026-04-01",
            metric="high",
            forecast_value_c=value,
            source_cycle_time=cycle,
            source_available_at=available,
        )
    _insert_settlement(
        conn,
        city="Paris",
        target_date="2026-04-01",
        metric="high",
        settlement_value=19.0,
    )

    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    hist = _provider(conn)(
        city="Paris",
        metric="high",
        lead_days=1,
        target_date=date(2026, 5, 1),
        models=["gfs_global"],
    )["gfs_global"]
    conn.set_trace_callback(None)

    query = next(
        statement
        for statement in statements
        if "FROM raw_model_forecasts INDEXED BY" in statement
    )
    plan = tuple(str(row[3]) for row in conn.execute(f"EXPLAIN QUERY PLAN {query}"))
    assert any("idx_raw_model_forecasts_history_join" in step for step in plan)
    assert not any("SCAN r" in step for step in plan)
    assert hist.forecast_values == (21.0,)


# =====================================================================================
# (2) PROVENANCE GATE — authority must be VERIFIED
# =====================================================================================
def test_history_excludes_non_verified_settlement() -> None:

    conn = _conn()
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-01", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0, authority="VERIFIED")
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-02", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-02", metric="high", settlement_value=19.0, authority="UNVERIFIED")
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-03", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-03", metric="high", settlement_value=19.0, authority="DISPUTED")

    provider = _provider(conn)
    hist = provider(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    assert hist["gfs_global"].n_train == 1, "only authority=VERIFIED settlement rows may contribute"


# =====================================================================================
# (3) ENDPOINT GATE — single_runs (live capture) must NOT train
# =====================================================================================
def test_history_excludes_previous_runs_operator_but_keeps_fixed_run_single() -> None:

    conn = _conn()
    # The archive row may be physically identified, but its daily extrema are assembled from
    # hourly rolling leads and are not a fixed-run daily operator.
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-01", metric="high", forecast_value_c=20.0, endpoint="previous_runs")
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0)
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-02",
        metric="high", forecast_value_c=20.0, endpoint="single_runs",
        source_cycle_time="2026-04-01T12:00:00+00:00",
        source_available_at="2026-04-01T13:00:00+00:00",
        captured_at="2026-04-01T14:00:00+00:00",
    )
    _insert_settlement(conn, city="Paris", target_date="2026-04-02", metric="high", settlement_value=19.0)

    provider = _provider(conn)
    hist = provider(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    assert hist["gfs_global"].target_dates == ("2026-04-02",)


def test_gridded_model_rejects_wrong_current_product_without_fallback() -> None:
    """A single-runs row with a different request cell is not current-equivalent history."""

    conn = _conn()
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=20.0, endpoint="single_runs",
        source_cycle_time="2026-03-31T06:00:00+00:00",
        request_params_mutation={"cell_selection": "sea"},
    )
    _insert_settlement(
        conn, city="Paris", target_date="2026-04-01", metric="high",
        settlement_value=19.0,
    )

    hist = _provider(conn)(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["gfs_global"],
    )
    assert hist == {}


def test_station_positive_lead_uses_latest_single_runs_issue_per_date() -> None:

    conn = _conn()
    for cycle, available, captured, value in (
        ("2026-03-31T00:00:00+00:00", "2026-03-31T01:00:00+00:00", "2026-03-31T02:00:00+00:00", 18.0),
        ("2026-03-31T06:00:00+00:00", "2026-03-31T07:00:00+00:00", "2026-03-31T08:00:00+00:00", 20.0),
    ):
        _insert_raw(
            conn, model="cwa_township_hourly_high", city="Taipei", target_date="2026-04-01",
            metric="high", forecast_value_c=value, endpoint="single_runs",
            source_cycle_time=cycle, source_available_at=available,
            captured_at=captured,
        )
    _insert_settlement(
        conn, city="Taipei", target_date="2026-04-01", metric="high",
        settlement_value=19.0,
    )

    hist = _provider(conn)(
        city="Taipei", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["cwa_township_hourly_high"],
    )["cwa_township_hourly_high"]
    assert hist.n_train == 1
    assert hist.forecast_values == (20.0,)
    assert hist.residuals == pytest.approx((1.0,))


def test_station_day0_single_runs_do_not_train_without_issue_time_alignment() -> None:

    conn = _conn()
    _insert_raw(
        conn, model="hko_fnd", city="Hong Kong", target_date="2026-04-01",
        metric="high", forecast_value_c=30.0, endpoint="single_runs", lead_days=0,
        source_cycle_time="2026-04-01T12:00:00+00:00",
    )
    _insert_settlement(
        conn, city="Hong Kong", target_date="2026-04-01", metric="high",
        settlement_value=29.0,
    )

    hist = _provider(conn)(
        city="Hong Kong", metric="high", lead_days=0,
        target_date=date(2026, 5, 1), models=["hko_fnd"],
    )
    assert hist == {}


def test_station_positive_lead_rejects_after_local_day_start() -> None:

    conn = _conn()
    _insert_raw(
        conn, model="cwa_township_hourly_high", city="Taipei", target_date="2026-04-01",
        metric="high", forecast_value_c=30.0, endpoint="single_runs", lead_days=1,
        source_cycle_time="2026-03-31T12:00:00+00:00",
        # Taipei local 2026-04-01 starts at 2026-03-31T16:00Z. This issue is still
        # March 31 in UTC but is already four hours into the target local day.
        source_available_at="2026-03-31T20:00:00+00:00",
    )
    _insert_settlement(
        conn, city="Taipei", target_date="2026-04-01", metric="high",
        settlement_value=29.0,
    )

    hist = _provider(conn)(
        city="Taipei", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["cwa_township_hourly_high"],
    )
    assert hist == {}


# =====================================================================================
# (4) UNIT COHERENCE — F-settlement converted to degC before the residual
# =====================================================================================
def test_history_converts_fahrenheit_settlement_to_celsius() -> None:

    conn = _conn()
    # forecast_value_c is degC by construction. Settlement stored in degF must be converted.
    # 68F == 20C ; forecast 21C -> residual should be 21 - 20 = +1.0C (NOT 21 - 68).
    _insert_raw(conn, model="gfs_global", city="NewYork", target_date="2026-04-01", metric="high", forecast_value_c=21.0)
    _insert_settlement(conn, city="NewYork", target_date="2026-04-01", metric="high", settlement_value=68.0, settlement_unit="F")

    provider = _provider(conn)
    hist = provider(city="NewYork", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    gfs = hist["gfs_global"]
    assert gfs.n_train == 1
    assert abs(gfs.residuals[0] - 1.0) < 1e-6, "F settlement must convert to C before the residual (no C/F mix)"


# =====================================================================================
# (5) FAIL-SOFT — provider never raises
# =====================================================================================
def test_provider_never_raises_on_bad_input() -> None:

    conn = _conn()
    conn.close()  # a closed connection forces a query error -> must be swallowed to {}
    provider = _provider(conn)
    out = provider(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    assert out == {}, "provider MUST be fail-soft: any error -> empty mapping (anchor fallback)"


# =====================================================================================
# (6) CROSSING MIN_TRAIN — >=25 verified previous-runs rows -> trustable history
# =====================================================================================
def test_history_crosses_min_train_with_25_verified_rows() -> None:

    conn = _conn()
    days = _dates(MIN_TRAIN, start=date(2026, 4, 1))  # 25 days, all strictly < decision
    for i, d in enumerate(days):
        prior_day = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
        _insert_raw(
            conn, model="ecmwf_ifs", city="Paris", target_date=d, metric="high",
            forecast_value_c=20.0 + 0.1 * i, endpoint="single_runs",
            source_cycle_time=prior_day + "T12:00:00+00:00",
            source_available_at=prior_day + "T13:00:00+00:00",
            captured_at=prior_day + "T14:00:00+00:00",
        )
        for m in ("gfs_global", "icon_global"):
            _insert_raw(conn, model=m, city="Paris", target_date=d, metric="high", forecast_value_c=20.0 + 0.1 * i)
        _insert_settlement(conn, city="Paris", target_date=d, metric="high", settlement_value=20.0)

    provider = _provider(conn)
    hist = provider(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 6, 1), models=["ecmwf_ifs", "gfs_global", "icon_global"],
    )
    for m in ("ecmwf_ifs", "gfs_global", "icon_global"):
        assert hist[m].n_train >= MIN_TRAIN, f"{m} should accrue >= MIN_TRAIN={MIN_TRAIN} verified rows"
    # The anchor's n_train >= MIN_TRAIN is the switch that lets capture set anchor_z/anchor_tau0
    # (bayes_precision_fusion_capture.py:312) so fuse_bayes_precision_posterior reaches T2_BAYES (not EQUAL_WEIGHT).
    assert hist["ecmwf_ifs"].n_train >= MIN_TRAIN


def test_provider_excludes_pre_current_source_epoch_even_with_matching_raw() -> None:
    conn = _conn()
    _insert_raw(conn, model="gfs_global", city="Chicago", target_date="2026-08-22", metric="high", forecast_value_c=20.0, lead_days=1)
    _insert_settlement(conn, city="Chicago", target_date="2026-08-22", metric="high", settlement_value=70.0, settlement_unit="F")

    assert _provider(conn)(city="Chicago", metric="high", lead_days=1, target_date=date(2026, 9, 1), models=["gfs_global"]) == {}


def test_provider_excludes_old_resolver_and_future_label_revision() -> None:
    conn = _conn()
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-01", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0, era="uma_oo_v2")
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-02", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-02", metric="high", settlement_value=19.0, observation_fetch_at="2026-07-02T00:00:00+00:00")

    assert _provider(conn)(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"]) == {}


def test_provider_excludes_future_raw_capture_and_recorded_revision() -> None:
    conn = _conn()
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-01", metric="high", forecast_value_c=20.0, captured_at="2026-07-02T00:00:00+00:00")
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0)
    _insert_raw(conn, model="icon_global", city="Paris", target_date="2026-04-02", metric="high", forecast_value_c=20.0, recorded_at="2026-07-02T00:00:00+00:00")
    _insert_settlement(conn, city="Paris", target_date="2026-04-02", metric="high", settlement_value=19.0)

    assert _provider(conn)(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global", "icon_global"]) == {}


def test_history_rejects_ifs025_previous_runs_but_keeps_pre_target_ifs9_single_runs() -> None:
    """The live IFS9 anchor cannot train on Open-Meteo's IFS025 archive."""

    conn = _conn()
    _insert_raw(
        conn, model="ecmwf_ifs", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=17.0, endpoint="previous_runs",
        source_cycle_time="2026-03-31T12:00:00+00:00",
        source_available_at="2026-03-31T13:00:00+00:00",
        captured_at="2026-03-31T14:00:00+00:00",
    )
    _insert_raw(
        conn, model="ecmwf_ifs", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=19.0, endpoint="single_runs",
        source_cycle_time="2026-03-31T12:00:00+00:00",
        source_available_at="2026-03-31T13:00:00+00:00",
        captured_at="2026-03-31T14:00:00+00:00",
    )
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=18.0)

    history = _provider(conn)(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["ecmwf_ifs"],
    )["ecmwf_ifs"]
    assert history.forecast_values == (19.0,)


def test_history_rejects_standard_product_without_modified_stamp_or_valid_request_hash() -> None:
    conn = _conn()
    for target, product_id, request_url_hash in (
        ("2026-04-01", "ecmwf_ifs::standard_api_meta_stamped::run=2026-04-01T00:00:00+00:00::modified=", None),
        ("2026-04-02", None, "tampered"),
        ("2026-04-03", None, None),
    ):
        _insert_raw(
            conn, model="ecmwf_ifs", city="Paris", target_date=target,
            metric="high", forecast_value_c=20.0, endpoint="single_runs",
            endpoint_mode="standard_api_meta_stamped", product_id=product_id,
            request_url_hash=request_url_hash,
            source_cycle_time="2026-03-31T12:00:00+00:00",
            source_available_at="2026-03-31T13:00:00+00:00",
            captured_at="2026-03-31T14:00:00+00:00",
        )
        _insert_settlement(conn, city="Paris", target_date=target, metric="high", settlement_value=19.0)

    history = _provider(conn)(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["ecmwf_ifs"],
    )["ecmwf_ifs"]
    assert history.target_dates == ("2026-04-03",)


def test_day0_previous_runs_is_physically_identified_but_not_fixed_run_history() -> None:
    conn = _conn()
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=20.0, endpoint="previous_runs", lead_days=0,
    )
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0)

    from src.data.bayes_precision_fusion_history_provider import raw_product_matches_live_source

    raw = conn.execute("SELECT * FROM raw_model_forecasts").fetchone()
    assert raw is not None
    assert raw_product_matches_live_source(raw, _TEST_CITIES["Paris"], lead_days=0)
    history = _provider(conn)(
        city="Paris", metric="high", lead_days=0,
        target_date=date(2026, 5, 1), models=["gfs_global"],
    )
    assert history == {}


def test_history_rejects_partial_or_after_target_start_single_runs() -> None:
    conn = _conn()
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=20.0, endpoint="single_runs",
        source_cycle_time="2026-03-31T12:00:00+00:00",
        source_available_at="2026-03-31T13:00:00+00:00",
        captured_at="2026-03-31T14:00:00+00:00", coverage_status="PARTIAL",
    )
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-02",
        metric="high", forecast_value_c=20.0, endpoint="single_runs",
        source_cycle_time="2026-04-01T23:00:00+00:00",
        source_available_at="2026-04-01T23:30:00+00:00",
        captured_at="2026-04-02T00:00:00+00:00",
    )
    for target in ("2026-04-01", "2026-04-02"):
        _insert_settlement(conn, city="Paris", target_date=target, metric="high", settlement_value=19.0)

    assert _provider(conn)(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["gfs_global"],
    ) == {}


def test_history_rejects_post_target_cycle_with_forged_early_availability() -> None:
    conn = _conn()
    # Paris 2026-04-01 begins at 2026-03-31T22:00Z.  Availability/capture cannot
    # make a run initialized inside the target local day causal fixed-lead history.
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=20.0, endpoint="single_runs",
        source_cycle_time="2026-04-01T01:00:00+00:00",
        source_available_at="2026-03-31T13:00:00+00:00",
        captured_at="2026-03-31T14:00:00+00:00",
    )
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0)

    assert _provider(conn)(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["gfs_global"],
    ) == {}


def test_history_rejects_extra_request_physical_option_despite_matching_hash() -> None:
    conn = _conn()
    _insert_raw(
        conn, model="gfs_global", city="Paris", target_date="2026-04-01",
        metric="high", forecast_value_c=20.0, endpoint="previous_runs",
        request_params_mutation={"elevation": 4000},
    )
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0)

    assert _provider(conn)(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 5, 1), models=["gfs_global"],
    ) == {}
