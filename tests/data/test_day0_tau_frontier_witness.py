# Created: 2026-10-02
# Last reused/audited: 2026-10-02
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
from src.data.replacement_forecast_materializer import day0_remaining_from_iso_of

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
    assert queue_src.count("day0_remaining_from_iso=day0_tau") == 2
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
