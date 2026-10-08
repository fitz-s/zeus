# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Lifecycle: created=2026-10-08; last_reviewed=2026-10-08; last_reused=2026-10-08
# Authority basis: consult round 3 (route kinds NO-GO) blockers "obsolete conditioning detection" and
#   "migration ordering"; coordinator Track A2/A6.
# Purpose: A posterior or request conditioned on a source the Day0 fact law retired (instrument proxy
#   JMA/SWOB, physical-only FMI/IMGW/DWD/KNMI/WU-current) is migrated deterministically: the lag reader
#   names it, the queue boundary lets a canonical METAR reseed replace it through a typed retirement
#   witness, and a queued request carrying it is retired.  Cycle and same-source/fact ordering stand.
# Reuse: Private SQLite fixtures over the real city/registry config; calls the unmodified adapter.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_day0_route_kinds import (
    _awc,
    _bind,
    _conditioning,
    _route,
    _route_row,
    _seed,
    _world,
    TARGET,
)

UTC = timezone.utc
JMA = "jma_amedas_temperature"
T_AWC = datetime(2026, 10, 7, 6, 30, tzinfo=UTC)
T_JMA = datetime(2026, 10, 7, 6, 44, tzinfo=UTC)
DECISION = datetime(2026, 10, 7, 6, 50, tzinfo=UTC)
JMA_CONDITIONING = {"active": True, "metric": "high", "observation_time": T_JMA.isoformat(),
                    "observed_extreme_c": 20.3, "sample_count": 15, "source": JMA, "unit": "C"}


def _tokyo_no_page():
    conn = _world()
    _awc(conn, "Tokyo", "RJTT", T_AWC, 20)
    _route_row(conn, "Tokyo", _route(JMA, "RJTT"), T_JMA, 20.3)
    return conn


def _forecasts(tmp_path, conditioning, *, cycle="2026-10-06T18:00:00+00:00", key="day0_provisional_observation"):
    import src.data.replacement_forecast_live_materialization_queue as queue

    path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY, runtime_layer TEXT, source_id TEXT, city TEXT,
            target_date TEXT, temperature_metric TEXT, source_cycle_time TEXT, computed_at TEXT,
            provenance_json TEXT);
        CREATE INDEX idx_forecast_posteriors_runtime_layer_target
            ON forecast_posteriors(runtime_layer, city, target_date, temperature_metric, computed_at);
        """
    )
    conn.execute("INSERT INTO forecast_posteriors VALUES (1, 'live', ?, 'Tokyo', ?, 'high', ?, ?, ?)",
                 (queue.SOURCE_ID, TARGET, cycle, (T_JMA + timedelta(minutes=3)).isoformat(),
                  json.dumps({key: conditioning})))
    conn.commit()
    conn.close()
    return path


def _boundary(tmp_path, monkeypatch, conditioning, seed_source, seed_c, *, seed_cycle="2026-10-06T18:00:00+00:00",
              posterior_cycle="2026-10-06T18:00:00+00:00", key="day0_provisional_observation"):
    import src.data.replacement_forecast_live_materialization_queue as queue
    import src.data.replacement_input_hwm as input_hwm

    db = _forecasts(tmp_path, conditioning, cycle=posterior_cycle, key=key)
    monkeypatch.setattr(input_hwm, "latest_eligible_ensemble_input_cycle", lambda *_a, **_k: None)
    return queue._seed_source_cycle_boundary(forecast_db=db, seed={
        "city": "Tokyo", "target_date": TARGET, "temperature_metric": "high",
        "source_cycle_time": seed_cycle, "computed_at": DECISION.isoformat(), "baseline_source_run_id": "",
        "day0_observed_extreme_source": seed_source, "day0_observed_extreme_c": seed_c,
        "day0_observed_extreme_observation_time": T_AWC.isoformat(), "day0_observed_extreme_unit": "C",
    })


# ---------------------------------------------------------------- the consult's probe, end to end

def test_no_page_tokyo_jma_conditioning_recovers_at_entry_and_held(monkeypatch, tmp_path):
    """JMA conditioning 20.3 @06:44 and no page row: ENTRY and HELD refuse it, the lag reader now names the
    retired source (it returned None: no page fact), the queue admits the METAR reseed observed 06:30, and the
    reseed's conditioning binds for ENTRY and HELD."""
    from src.data.replacement_forecast_current_target_plan import _day0_observation_lag_reason

    conn = _tokyo_no_page()
    for held in (False, True):
        with pytest.raises(ValueError, match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_MISMATCH"):
            _bind(conn, "Tokyo", "RJTT", JMA_CONDITIONING, held=held)
    reason = _day0_observation_lag_reason(
        conn, city="Tokyo", target_date=TARGET, temperature_metric="high", decision_time=DECISION,
        posterior_provenance_json=json.dumps({"day0_provisional_observation": JMA_CONDITIONING}))
    assert reason == f"basis=day0_retired_fact_source:posterior_source={JMA}:posterior_observation_time={T_JMA.isoformat()}"

    reseed = _seed(monkeypatch, conn, "Tokyo")
    assert (reseed["day0_observed_extreme_source"], reseed["day0_observed_extreme_c"]) == ("aviationweather_metar", 20.0)
    assert _boundary(tmp_path, monkeypatch, JMA_CONDITIONING, reseed["day0_observed_extreme_source"],
                     reseed["day0_observed_extreme_c"]) is None
    for held in (False, True):
        binding = _bind(conn, "Tokyo", "RJTT", _conditioning(reseed), held=held)["_edli_global_day0_binding"]
        assert binding["probability_conditioning_identity"]["source"] == "aviationweather_metar"


# ---------------------------------------------------------------- the witness is narrow

def test_without_the_witness_the_retired_clock_blocks_the_reseed(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    monkeypatch.setattr(queue, "_fact_seed_retires_retired_source", lambda *_a: False)
    assert _boundary(tmp_path, monkeypatch, JMA_CONDITIONING, "aviationweather_metar", 20.0) == (
        "current_day0_observation", T_JMA.isoformat())


@pytest.mark.parametrize("seed_source", [JMA, "fmi_airport_temperature"])
def test_a_retired_source_seed_never_carries_the_witness(tmp_path, monkeypatch, seed_source):
    assert _boundary(tmp_path, monkeypatch, JMA_CONDITIONING, seed_source, 20.0) == (
        "current_day0_observation", T_JMA.isoformat())


@pytest.mark.parametrize("incumbent_source", ["aviationweather_metar", "noaa_wrh_rjtt", "mgm_metar_temperature"])
def test_fact_to_fact_observation_ordering_still_rejects(tmp_path, monkeypatch, incumbent_source):
    """An incumbent on a Day0 fact (AWC, page, native report) keeps its observation high-water mark."""
    incumbent = {**JMA_CONDITIONING, "source": incumbent_source, "observed_extreme_c": 20.0}
    assert _boundary(tmp_path, monkeypatch, incumbent, "aviationweather_metar", 20.0) == (
        "current_day0_observation", T_JMA.isoformat())


def test_the_witness_never_crosses_a_cycle_regression(tmp_path, monkeypatch):
    assert _boundary(tmp_path, monkeypatch, JMA_CONDITIONING, "aviationweather_metar", 20.0,
                     seed_cycle="2026-10-06T12:00:00+00:00") == ("current_posterior", "2026-10-06T18:00:00+00:00")


def test_the_witness_is_metric_scoped():
    import src.data.replacement_forecast_live_materialization_queue as queue

    seed = {"temperature_metric": "high", "day0_observed_extreme_source": "aviationweather_metar"}
    assert queue._fact_seed_retires_retired_source(seed, JMA_CONDITIONING) is True
    assert queue._fact_seed_retires_retired_source(seed, {**JMA_CONDITIONING, "metric": "low"}) is False


# ---------------------------------------------------------------- lag reader: only the retired source changes

@pytest.mark.parametrize("source,expected_none", [("aviationweather_metar", True), (JMA, False),
                                                  ("eccc_swob_temperature", False), ("fmi_airport_temperature", False),
                                                  ("mgm_metar_temperature", True)])
def test_lag_reader_flags_exactly_the_retired_sources(source, expected_none):
    from src.data.replacement_forecast_current_target_plan import _day0_observation_lag_reason

    conn = _world()
    _awc(conn, "Tokyo", "RJTT", T_AWC, 20)
    conditioning = {**JMA_CONDITIONING, "source": source, "observation_time": (T_AWC + timedelta(minutes=5)).isoformat(),
                    "observed_extreme_c": 20.0}
    reason = _day0_observation_lag_reason(
        conn, city="Tokyo", target_date=TARGET, temperature_metric="high", decision_time=DECISION,
        posterior_provenance_json=json.dumps({"day0_provisional_observation": conditioning}))
    assert (reason is None) is expected_none


# ---------------------------------------------------------------- A6: a queued retired-source request drains

@pytest.mark.parametrize("source,retired", [(JMA, True), ("eccc_swob_temperature", True),
                                            ("fmi_airport_temperature", True), ("knmi_station_temperature", True),
                                            ("aviationweather_metar", False), ("mgm_metar_temperature", False),
                                            ("noaa_wrh_rjtt", False), (None, False)])
def test_queued_request_on_a_retired_source_is_retired_whatever_the_exposure(source, retired):
    """The real queued Tokyo request (jma 20.3, expires 2026-10-09T12:00Z) is retired deterministically,
    held or not, before any child; a request on a Day0 fact is untouched."""
    from src.data.replacement_forecast_live_materialization_queue import (
        _REQUEST_DAY0_SOURCE_RETIRED_REASON,
        _request_contract_lapse_reason,
    )

    payload = {"city": "Tokyo", "target_date": "2026-10-09", "temperature_metric": "high",
               "expires_at": "2026-10-09T12:00:00+00:00", "day0_observed_extreme_source": source}
    for held in (False, True):
        reason = _request_contract_lapse_reason(payload, now_utc=datetime(2026, 10, 8, 17, 30, tzinfo=UTC),
                                                held=lambda _scope, held=held: held)
        assert reason == (_REQUEST_DAY0_SOURCE_RETIRED_REASON if retired else None)
