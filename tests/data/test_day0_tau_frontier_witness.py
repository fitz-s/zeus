# Created: 2026-10-02
# Last reused/audited: 2026-10-03
# Lifecycle: created=2026-10-02; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Preserve Day0 tau parity and exact original missing-input verdicts.
# Reuse: Run for materialization block-evidence or prewrite emission changes.
# Authority basis: 7e9dec5ea (Day0 remaining window proven at the last observation tau).
#   Live 2026-10-02 Chongqing 10-02 high after its deploy: prepare served ecmwf06/ukmo06/icon12
#   under tau, the final writer's frontier witness re-read without tau, saw a different
#   frontier and retried to REPLACEMENT_FORECAST_SNAPSHOT_RETRY_EXHAUSTED.
"""Every reader that compares against the prepare's served set reads the same tau."""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import scripts.materialize_replacement_forecast_live as cli
from src.data import materialization_block_evidence as evidence
from src.data import replacement_current_value_serving as serving
from src.data import replacement_forecast_live_materialization_queue as queue
from src.data.forecast_target_contract import day0_remaining_from_iso_of

TAU = "2026-10-02T15:05:56+00:00"
DECISION = "2026-10-02T16:30:00+00:00"


def _family(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE raw_model_forecasts (
        raw_model_forecast_id INTEGER PRIMARY KEY, model TEXT, city TEXT, target_date TEXT, metric TEXT,
        source_cycle_time TEXT, source_available_at TEXT, captured_at TEXT, recorded_at TEXT,
        lead_days INTEGER, forecast_value_c REAL, endpoint TEXT)""")
    conn.executemany(
        "INSERT INTO raw_model_forecasts VALUES (?, ?, 'Chongqing', '2026-10-02', 'high', ?, ?, ?, ?, 0, ?, 'single_runs')",
        [
            (1, "ecmwf_ifs", "2026-10-01T12:00:00+00:00", "2026-10-01T19:00:00+00:00",
             "2026-10-02T00:00:00+00:00", "2026-10-02T00:00:00+00:00", 23.7),
            (2, "ecmwf_ifs", "2026-10-02T06:00:00+00:00", "2026-10-02T12:00:00+00:00",
             "2026-10-02T12:32:00+00:00", "2026-10-02T12:32:00+00:00", 22.5),
        ],
    )


@pytest.fixture
def tau_proof(monkeypatch):
    """The newer run is proven only over the remaining window tau names (as on live)."""

    def authority(raw, *, lead_days):
        row = json.loads(str(raw))
        if row["raw_model_forecast_id"] == 2:
            return row.get("day0_remaining_from") == TAU
        return True

    monkeypatch.setattr(serving, "_source_clock_product_has_authority", authority)
    monkeypatch.setattr(serving, "_read_product_identity_at_cutoff", lambda conn, raw, **_k: raw)
    monkeypatch.setattr(serving, "_physical_response_provenance", lambda row: None)


def test_frontier_with_tau_matches_the_prepared_served_set(tau_proof):
    conn = sqlite3.connect(":memory:")
    _family(conn)
    schema = serving.current_value_serving_schema(conn)
    served = serving.read_current_instrument_values(
        conn, city="Chongqing", metric="high", target_date="2026-10-02",
        source_cycle_time_iso="2026-10-01T12:00:00+00:00", decision_time_iso=DECISION,
        day0_remaining_from_iso=TAU)
    frontier = dict(serving.read_current_instrument_frontier_identity(
        conn, city="Chongqing", metric="high", target_date="2026-10-02", decision_time_iso=DECISION,
        models=None, schema=schema, day0_remaining_from_iso=TAU))
    blind = dict(serving.read_current_instrument_frontier_identity(
        conn, city="Chongqing", metric="high", target_date="2026-10-02", decision_time_iso=DECISION,
        models=None, schema=schema))
    assert served["ecmwf_ifs"].raw_model_forecast_id == 2
    assert frontier == {"ecmwf_ifs": 2}   # the writer sees what prepare served
    assert blind == {"ecmwf_ifs": 1}      # a tau-blind witness is the live mismatch


def test_writer_witness_reads_the_frontier_with_the_request_tau(monkeypatch):
    """The real witness builder hands the request's own tau to the frontier reader."""
    seen = {}

    def frontier(conn, **kwargs):
        seen["tau"] = kwargs.get("day0_remaining_from_iso")
        raise cli._TargetDependencyWitnessUnavailable("stop after the frontier read")

    monkeypatch.setattr(cli, "read_current_instrument_frontier_identity", frontier)
    monkeypatch.setattr(cli, "read_current_instrument_family_latest_id", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_exact_rows_witness", lambda *a, **k: cli._ExactRowsWitness((), ()))
    request = SimpleNamespace(
        city="Chongqing", target_date="2026-10-02", computed_at=datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc),
        baseline_source_run_id=None, baseline_source_available_at=None,
        openmeteo_source_run_id=None, openmeteo_source_available_at=None,
        day0_observed_extreme_observation_time="2026-10-02T23:05:56+08:00")
    prepared = SimpleNamespace(request=request, metric="high",
                               posterior=SimpleNamespace(provenance_payload={"bayes_precision_fusion": {}}))
    columns = {table: ("x",) for table in ("source_run", "raw_forecast_artifacts", "raw_model_forecasts", "ensemble_snapshots")}
    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._build_target_dependency_witness(sqlite3.connect(":memory:"), prepared, columns=columns,
                                             provider_schema=object())
    assert seen["tau"] == TAU


def test_every_served_set_comparison_names_the_same_tau():
    """No reader that is compared against prepare's served set is tau-blind."""
    queue_src = inspect.getsource(queue)
    assert queue_src.count("day0_remaining_from_iso=day0_tau") == 3
    assert 'day0_remaining_from_iso_of(payload.get("day0_observed_extreme_observation_time"))' in queue_src
    assert "day0_remaining_from_iso_of(\n                payload.get(\"day0_observed_extreme_observation_time\"))" in queue_src
    ev = inspect.getsource(evidence)
    assert 'item.get("day0_remaining_from_iso")' in ev
    assert 'day0_remaining_from_iso_of(payload.get("day0_observed_extreme_observation_time"))' in ev


def test_tau_text_is_one_normalization():
    assert day0_remaining_from_iso_of("2026-10-02T23:05:56+08:00") == TAU
    assert day0_remaining_from_iso_of(None) is None
    assert day0_remaining_from_iso_of("2026-10-02T15:05:56") is None
    assert day0_remaining_from_iso_of("garbage") is None


def test_no_cohort_evidence_without_tau_is_unchanged():
    assert evidence.no_cohort_item(window_hours=6.0, decision_time_iso=DECISION) == {
        "kind": evidence.NO_COHERENT_COHORT, "window_hours": 6.0, "decision_time_iso": DECISION}
    assert evidence.no_cohort_item(window_hours=6.0, decision_time_iso=DECISION,
                                   day0_remaining_from_iso=TAU)["day0_remaining_from_iso"] == TAU


def _missing_day0_request(metric="high"):
    from dataclasses import replace
    from tests.test_replacement_forecast_materializer import _request, _dt, _current_baseline_data_version

    return replace(_request(computed_at=_dt(18), expires_at=_dt(2).replace(day=7)),
                   temperature_metric=metric, baseline_data_version=_current_baseline_data_version(metric))


def _missing_day0_payload(request):
    return {
        "city": request.city, "city_timezone": request.city_timezone,
        "target_date": str(request.target_date), "temperature_metric": request.temperature_metric,
        "computed_at": request.computed_at.isoformat(),
        "day0_observed_extreme_c": request.day0_observed_extreme_c,
        "day0_observation_state": request.day0_observation_state,
    }


@pytest.mark.parametrize("metric", ("high", "low"))
def test_original_missing_day0_emits_exact_immutable_proof(metric, monkeypatch):
    from src.data import replacement_forecast_materializer as materializer
    from tests.test_replacement_forecast_materializer import _conn

    request = _missing_day0_request(metric)
    conn = _conn()
    monkeypatch.setattr(materializer, "_request_with_materialization_clock",
                        lambda *_a: pytest.fail("original refusal precedes clock lift"))
    monkeypatch.setattr(materializer, "_request_with_day0_physical_frontier",
                        lambda *_a, **_k: pytest.fail("original refusal precedes dynamic observation lookup"))
    result = materializer._validated_replacement_forecast_request(conn, request)
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_" + evidence.DAY0_REQUIRED,)
    assert result.evidence is not None
    assert evidence.evidence_holds(conn, result.evidence, exact_request=_missing_day0_payload(request))
    # A later real source observation does not repair this immutable request.
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    ensure_table(conn)
    append_print(conn, city=request.city, station_id="ZSPD", source_channel="aviationweather_metar",
                 publish_ts_utc=request.computed_at.isoformat(), value_native=20.0, unit="C",
                 fetched_at_utc=request.computed_at.isoformat(), raw_report="METAR ZSPD 062000Z 20/18")
    assert evidence.evidence_holds(conn, result.evidence, exact_request=_missing_day0_payload(request))
    assert materializer._validated_replacement_forecast_request(conn, request).evidence == result.evidence
    assert not evidence.evidence_holds(conn, result.evidence, _missing_day0_payload(request))


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("twin", ("observed", "typed_zero", "unknown", "future_day", "after_day_end"))
def test_missing_day0_proof_preserves_gate_twins(metric, twin):
    from dataclasses import replace
    from datetime import timedelta
    from src.data import replacement_forecast_materializer as materializer
    from src.contracts.replacement_pipeline_files import DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS as zero
    from tests.test_replacement_forecast_materializer import _dt

    request = _missing_day0_request(metric)
    changes = {
        "observed": {"day0_observed_extreme_c": 20.0},
        "typed_zero": {"day0_observation_state": zero},
        "unknown": {"day0_observation_state": "UNKNOWN"},
        "future_day": {"computed_at": _dt(4)},
        "after_day_end": {"computed_at": request.computed_at + timedelta(days=2)},
    }
    request = replace(request, **changes[twin])
    blocks = "REPLACEMENT_MATERIALIZATION_" + evidence.DAY0_REQUIRED in materializer._prewrite_block_reasons(request)
    assert blocks == (twin in ("unknown", "after_day_end"))
    assert (evidence.day0_missing_input_item(request) is not None) == (twin == "after_day_end")


@pytest.mark.parametrize("fault", ("revision", "predicate", "timezone", "computed_at", "metric", "target_date", "city", "items", "naive_clock", "unknown", "observed", "typed_zero", "missing_request"))
def test_missing_day0_proof_rejects_tamper_and_new_inputs(fault):
    from copy import deepcopy
    from src.data import replacement_forecast_materializer as materializer
    from src.contracts.replacement_pipeline_files import DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS as zero

    request = _missing_day0_request()
    conn = sqlite3.connect(":memory:")
    proof = deepcopy(materializer._prewrite_blocked(
        conn, request, ("REPLACEMENT_MATERIALIZATION_" + evidence.DAY0_REQUIRED,), original_request=True).evidence)
    payload = _missing_day0_payload(request)
    if fault == "revision": proof["revision"] = "foreign"
    elif fault == "predicate": proof["items"][0]["predicate_revision"] = "foreign"
    elif fault == "timezone": proof["items"][0]["city_timezone"] = "UTC"
    elif fault == "computed_at": proof["items"][0]["computed_at"] = "2026-06-06T19:00:00+00:00"
    elif fault in ("metric", "target_date", "city"):
        proof["scope"][{"metric": "temperature_metric"}.get(fault, fault)] = "foreign"
    elif fault == "items": proof["items"] = []
    elif fault == "naive_clock": payload["computed_at"] = "2026-06-06T18:00:00"
    elif fault == "unknown": payload["day0_observation_state"] = "UNKNOWN"
    elif fault == "observed": payload["day0_observed_extreme_c"] = 20.0
    elif fault == "typed_zero": payload["day0_observation_state"] = zero
    elif fault == "missing_request": payload = None
    assert not evidence.evidence_holds(conn, proof, exact_request=payload)


def test_dynamic_second_prewrite_cannot_emit_missing_input_proof(monkeypatch):
    from dataclasses import replace
    from src.data import replacement_forecast_materializer as materializer
    from tests.test_replacement_forecast_materializer import _conn, _dt

    original = replace(_missing_day0_request(), computed_at=_dt(4))
    monkeypatch.setattr(materializer, "_artifact_identity_block_reasons", lambda *_a: ())
    monkeypatch.setattr(materializer, "_cycle_monotone_block_reasons", lambda *_a, **_k: ())
    monkeypatch.setattr(materializer, "_precision_guard_block_reason", lambda *_a: ())
    monkeypatch.setattr(materializer, "_request_with_materialization_clock", lambda *_a: _missing_day0_request())
    monkeypatch.setattr(materializer, "_request_with_day0_physical_frontier", lambda _c, r, **_k: r)
    result = materializer._validated_replacement_forecast_request(_conn(), original)
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_" + evidence.DAY0_REQUIRED,)
    assert result.evidence is None
