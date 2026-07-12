# Created: 2026-07-03
# Last reused/audited: 2026-07-11
# Authority basis: W3 SOLVE design packet and 2026-07-11 global fractional-Kelly repair
"""G3 harness for the W3 SOLVE promotion seam (qkernel_spine_bridge.py w3_solve_enabled flag).

Proves the promotion flag is a SAFE, reversible, single-point cutover before any live enablement:
  (a) absent-vs-OFF byte-identity — the flag key absent vs explicitly False produce identical
      SpineDecisionResults over a fixture corpus (the OFF path is a no-op);
  (b) single-divergence-point — `w3_solve_enabled` is consumed at EXACTLY one code site (the guard);
  (c) ON-mode integration — with the flag ON the shim runs and every decision passes
      validate_family_decision_contract (no getattr-default consumer field fired);
  (d) OFF-path import-isolation — a decide call with the flag OFF does not import src.solve.

Fixtures are reused from tests/integration/test_qkernel_spine_routing.py (the realistic family +
proofs the legacy spine path is tested against).
"""

from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import inspect
import json
import sqlite3
import subprocess
import sys
import textwrap
from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.qkernel_spine_bridge as bridge
import src.engine.event_reactor_adapter as era
import src.engine.global_batch_runtime as global_batch_runtime
import src.engine.global_auction_universe as universe
from src.decision_kernel import claims
from src.decision_kernel.certificate import build_certificate
from src.engine.global_single_order_auction import (
    global_single_order_actuation_identity,
    global_single_order_economic_identity,
    select_prepared_global_auction,
)
from src.engine.global_auction_universe import (
    _current_day0_events,
    _day0_event_is_current_for_entry,
    capture_current_global_book_epoch,
    current_global_scope_events_with_day0,
    current_portfolio_wealth_witness,
    current_global_auction_scope_from_events,
)
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    make_day0_extreme_updated_event,
    make_opportunity_event,
)
from src.events.reactor import EventSubmissionReceipt
from src.solve.solver import (
    CurrentExecutionAuthority,
    CurrentFamilyProbabilityAuthority,
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    PortfolioWealthWitness,
    global_candidate_from_native,
    joint_probability_witness_identity,
    portfolio_wealth_identity,
    validate_family_decision_contract,
)
from src.strategy import utility_ranker
from src.state.collateral_ledger import init_collateral_schema
from src.state.portfolio import PortfolioState
from src.state.schema.opportunity_events_schema import (
    ensure_table as ensure_opportunity_events_table,
)
from tests.integration import test_qkernel_spine_routing as R

_BRIDGE_PATH = bridge.__file__


def test_global_actuation_does_not_blanket_block_existing_family_exposure():
    """A first fill must not structurally disable every later global order."""

    actuation_source = inspect.getsource(
        era._build_event_bound_no_submit_receipt_core
    )
    metrics_source = inspect.getsource(
        __import__("src.solve.solver", fromlist=["_single_order_metrics"])
        ._single_order_metrics
    )

    assert "GLOBAL_EXISTING_FAMILY_EXPOSURE_UNMODELED" not in actuation_source
    assert "_family_existing_exposure_for_selection_by_bin_id" in actuation_source
    assert "Coupling-robust endowment bound" in metrics_source


def _drive(family, proofs, payload):
    """Drive decide_family_via_spine with a FIXED positive baseline so the fixture's wealth is
    deterministic (the module bankroll provider is not warm in-test); identical for OFF and ON."""
    return bridge.decide_family_via_spine(
        family=family, payload=payload, proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )


def _payload_with_joint_samples(proofs, payload, *, draws=64):
    """Attach a coherent current-posterior draw matrix to a synthetic fixture payload."""
    out = dict(payload)
    out["_edli_spine_served_joint_q_samples_by_condition"] = {
        str(proof.candidate.condition_id): [float(proof.q_posterior)] * draws
        for proof in proofs
        if proof.direction == "buy_yes"
    }
    out["_edli_spine_posterior_identity_hash"] = "fixture-current-posterior"
    return out


def test_global_prepare_empty_scope_names_admission_classes_without_changing_scope():
    _family, proofs, _payload = _corpus()[0]
    ordinary_diagnostic: dict[str, object] = {}
    ordinary = era._selection_scoped_proofs(
        proofs=proofs,
        honor_admission_rejections=False,
        enforce_win_rate_floor=False,
        diagnostic_out=ordinary_diagnostic,
    )
    assert ordinary == tuple(proofs)
    assert ordinary_diagnostic == {}

    blocked = tuple(
        replace(proof, missing_reason="BUY_NO_CONSERVATIVE_EVIDENCE_MISSING")
        for proof in proofs
    )
    blocked_diagnostic: dict[str, object] = {}
    assert era._selection_scoped_proofs(
        proofs=blocked,
        honor_admission_rejections=False,
        enforce_win_rate_floor=False,
        diagnostic_out=blocked_diagnostic,
    ) == ()
    assert blocked_diagnostic == {
        "empty_reason": (
            "SELECTION_SCOPE_EMPTY:admission:"
            f"input={len(blocked)}:classes=BUY_NO_CONSERVATIVE_EVIDENCE_MISSING="
            f"{len(blocked)}"
        )
    }


def test_global_prepare_failure_preserves_early_spine_no_trade_reason():
    assert era._global_prepare_failure_reason(
        SimpleNamespace(
            global_family=None,
            global_prepare_reason=None,
            no_trade_reason="SPINE_INPUTS_UNAVAILABLE:DAY0_OBSERVATION_STALE",
        )
    ) == "SPINE_INPUTS_UNAVAILABLE:DAY0_OBSERVATION_STALE"
    assert era._global_prepare_failure_reason(
        SimpleNamespace(
            global_family=None,
            global_prepare_reason="GLOBAL_FAMILY_PREPARE_FAILED:ValueError:bad",
            no_trade_reason="SPINE_NO_SELECTION",
        )
    ) == "GLOBAL_FAMILY_PREPARE_FAILED:ValueError:bad"
    assert era._global_prepare_failure_reason(
        SimpleNamespace(
            global_family=object(),
            global_prepare_reason=None,
            no_trade_reason="SPINE_NO_SELECTION",
        )
    ) is None


def test_global_actuation_revalidates_content_then_preserves_selected_witness(monkeypatch):
    content = {
        field: f"current-{field}"
        for field in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    }
    selected = SimpleNamespace(**content, authority_certificate_hash="selected-cert")
    refreshed = SimpleNamespace(**content, authority_certificate_hash="fresh-cert")
    current_family = bridge.PreparedGlobalFamily(
        decision_id="fresh-decision",
        probability_witness=refreshed,
        candidate_seeds=(),
    )
    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        lambda *_args, **_kwargs: current_family,
    )
    conn = sqlite3.connect(":memory:")
    rebound, current_day0_payload = era._current_global_actuation_prepared_family(
        SimpleNamespace(),
        global_actuation=SimpleNamespace(probability_witness=selected),
        forecast_conn=conn,
        topology_conn=conn,
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    )
    assert rebound.probability_witness is selected
    assert rebound.decision_id == "fresh-decision"
    assert current_day0_payload == {}

    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        lambda *_args, **_kwargs: replace(
            current_family,
            probability_witness=SimpleNamespace(**{**content, "q_version": "moved"}),
        ),
    )
    with pytest.raises(ValueError, match="GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED"):
        era._current_global_actuation_prepared_family(
            SimpleNamespace(),
            global_actuation=SimpleNamespace(probability_witness=selected),
            forecast_conn=conn,
            topology_conn=conn,
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        )
    conn.close()


def _stale_day0_carrier_and_current_observations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT,
            target_date TEXT,
            source TEXT,
            station_id TEXT,
            local_timestamp TEXT,
            utc_timestamp TEXT,
            imported_at TEXT,
            temp_unit TEXT,
            running_max REAL,
            running_min REAL,
            authority TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            source_role TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                "Moscow", "2026-07-10", "ogimet_metar_uuww", "UUWW",
                "2026-07-10T16:00:00+03:00", "2026-07-10T13:00:00+00:00",
                "2026-07-10T13:05:00+00:00", "C", 27.0, 27.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
            (
                "Moscow", "2026-07-10", "ogimet_metar_uuww", "UUWW",
                "2026-07-10T22:00:00+03:00", "2026-07-10T19:00:00+00:00",
                "2026-07-10T19:05:00+00:00", "C", 19.0, 19.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
            (
                "Moscow", "2026-07-10", "ogimet_metar_uuww", "UUWW",
                "2026-07-10T23:00:00+03:00", "2026-07-10T20:00:00+00:00",
                "2026-07-10T20:30:00+00:00", "C", 18.0, 18.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
        ),
    )
    carrier_payload = {
        "city": "Moscow",
        "target_date": "2026-07-10",
        "metric": "high",
        "station_id": "UUWW",
        "settlement_source": "ogimet_metar_uuww",
        "settlement_unit": "C",
        "observation_time": "2026-07-10T13:00:00+00:00",
        "observation_available_at": "2026-07-10T13:05:00+00:00",
        "raw_value": 27.0,
        "rounded_value": 27,
        "high_so_far": 27.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    carrier = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Moscow|2026-07-10|high|UUWW",
        source="global_auction_winner_target:old-carrier",
        observed_at="2026-07-10T13:00:00+00:00",
        available_at="2026-07-10T13:05:00+00:00",
        received_at="2026-07-10T13:05:00+00:00",
        payload=carrier_payload,
        causal_snapshot_id="old-day0-carrier",
    )
    return conn, carrier


def test_global_day0_actuation_rebinds_stale_carrier_to_current_conditioning():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    conditioning = {
        "active": True,
        "metric": "high",
        "observation_time": "2026-07-10T19:00:00+00:00",
        "observed_extreme_c": 27.0,
        "sample_count": 2,
        "source": "durable_observation_instants",
        "unit": "C",
    }
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
        resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
        conditioning=conditioning,
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        posterior_id=29914,
    )
    conn.close()

    assert json.loads(carrier.payload_json)["observation_time"] == "2026-07-10T13:00:00+00:00"
    assert rebound["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert rebound["high_so_far"] == 27.0
    assert rebound["sample_count"] == 2
    assert rebound["station_id"] == "UUWW"
    assert rebound["settlement_source"] == "ogimet_metar_uuww"
    assert rebound["_edli_global_day0_binding"]["posterior_id"] == 29914


def test_global_day0_authority_uses_current_possession_clock_not_stale_carrier_clock():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    decision_time = _dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc)
    payload = json.loads(carrier.payload_json)
    payload.update(
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
            resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T19:00:00+00:00",
                "observed_extreme_c": 27.0,
                "sample_count": 2,
                "source": "durable_observation_instants",
                "unit": "C",
            },
            observation_conn=conn,
            decision_time=decision_time,
            posterior_id=29914,
        )
    )
    conn.close()
    old_source_time = _dt.datetime(2026, 7, 10, 13, 0, tzinfo=_dt.timezone.utc)
    old_received_time = _dt.datetime(2026, 7, 10, 13, 5, tzinfo=_dt.timezone.utc)

    def base_cert(certificate_type, cert_payload=None):
        return build_certificate(
            certificate_type=certificate_type,
            semantic_key=f"fixture:{certificate_type}",
            claim_type=certificate_type,
            mode="LIVE",
            decision_time=decision_time,
            source_available_at=old_source_time,
            agent_received_at=old_received_time,
            persisted_at=old_received_time,
            payload=dict(cert_payload or {}),
            authority_id="fixture",
            authority_version="v1",
            algorithm_id="fixture",
            algorithm_version="v1",
        )

    parents = (
        base_cert(claims.CLOCK_MODE),
        base_cert(claims.CAUSAL_EVENT),
        base_cert(claims.SOURCE_TRUTH),
        base_cert(claims.FAMILY_CLOSURE, {"family_id": "Moscow|2026-07-10|high"}),
        base_cert(claims.BELIEF),
    )
    certs = era._day0_live_source_parent_certificates(
        event=carrier,
        payload=payload,
        base_certs=parents,
        decision_time=decision_time,
    )
    authority = next(
        cert for cert in certs if cert.certificate_type == claims.DAY0_AUTHORITY
    )

    assert authority.payload["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert authority.header.source_available_at == _dt.datetime(
        2026, 7, 10, 19, 5, tzinfo=_dt.timezone.utc
    )
    assert authority.header.agent_received_at == decision_time
    assert authority.header.persisted_at == decision_time
    assert (
        authority.header.source_available_at
        <= authority.header.agent_received_at
        <= authority.header.persisted_at
        <= authority.header.decision_time
    )


def test_global_day0_actuation_binds_native_fahrenheit_to_conditioned_celsius():
    conn, old_carrier = _stale_day0_carrier_and_current_observations()
    conn.execute(
        """
        UPDATE observation_instants
           SET city='NYC', source='wu_icao_history', station_id='KLGA', temp_unit='F',
               running_max=CASE utc_timestamp
                   WHEN '2026-07-10T13:00:00+00:00' THEN 80.6
                   WHEN '2026-07-10T19:00:00+00:00' THEN 66.0
                   ELSE 64.0
               END
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "NYC", "2026-07-10", "wu_icao_history_kjfk", "KJFK",
            "2026-07-10T15:30:00-04:00", "2026-07-10T19:30:00+00:00",
            "2026-07-10T19:35:00+00:00", "F", 75.0, 70.0,
            "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "NYC", "2026-07-10", "wu_icao_history", "KLGA",
            "2026-07-10T15:45:00-04:00", "2026-07-10T19:45:00+00:00",
            "2026-07-10T19:50:00+00:00", "C", 25.0, 20.0,
            "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )
    carrier_payload = {
        **json.loads(old_carrier.payload_json),
        "city": "NYC",
        "station_id": "KLGA",
        "settlement_source": "aviationweather_metar",
        "settlement_unit": "F",
        "raw_value": 80.6,
        "rounded_value": 81,
        "high_so_far": 80.6,
    }
    carrier = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="NYC|2026-07-10|high|KLGA",
        source="global_auction_winner_target:old-nyc-carrier",
        observed_at="2026-07-10T13:00:00+00:00",
        available_at="2026-07-10T13:05:00+00:00",
        received_at="2026-07-10T13:05:00+00:00",
        payload=carrier_payload,
        causal_snapshot_id="old-nyc-day0-carrier",
    )
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(city="NYC", target_date="2026-07-10", metric="high"),
        resolution=SimpleNamespace(measurement_unit="F", station_id="KLGA"),
        conditioning={
            "active": True,
            "metric": "high",
            "observation_time": "2026-07-10T19:00:00+00:00",
            "observed_extreme_c": 27.0,
            "sample_count": 2,
            "source": "durable_observation_instants",
            "unit": "F",
        },
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        posterior_id=29915,
    )
    assert rebound["high_so_far"] == pytest.approx(80.6)
    assert rebound["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert rebound["sample_count"] == 2
    assert rebound["settlement_unit"] == "F"
    assert rebound["rounded_value"] == 81
    assert rebound["station_id"] == "KLGA"
    assert rebound["settlement_source"] == "wu_icao_history"
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH",
    ):
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(city="NYC", target_date="2026-07-10", metric="high"),
            resolution=SimpleNamespace(measurement_unit="F", station_id="KLGA"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T19:30:00+00:00",
                "observed_extreme_c": 27.0,
                "sample_count": 3,
                "source": "durable_observation_instants",
                "unit": "F",
            },
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
            posterior_id=29915,
        )
    conn.close()


def test_global_day0_actuation_rejects_conditioning_not_equal_to_current_state():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH",
    ):
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
            resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T13:00:00+00:00",
                "observed_extreme_c": 27.0,
                "sample_count": 2,
                "source": "durable_observation_instants",
                "unit": "C",
            },
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
            posterior_id=29914,
        )
    conn.close()


def test_global_day0_observation_unknown_source_type_fails_closed(monkeypatch):
    from src.data.replacement_forecast_current_target_plan import (
        _latest_authorized_day0_fact,
    )

    conn, _carrier = _stale_day0_carrier_and_current_observations()
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "Moscow": SimpleNamespace(
                settlement_source_type="unknown",
                settlement_unit="C",
                wu_station="UUWW",
            )
        },
    )
    assert _latest_authorized_day0_fact(
        conn,
        city="Moscow",
        target_date="2026-07-10",
        temperature_metric="high",
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    ) is None
    conn.close()


def test_global_day0_uses_replacement_joint_probability_builder(monkeypatch):
    expected = ({"condition": 0.5}, {}, {}, {}, {"probability_authority": "replacement_0_1"})
    monkeypatch.setattr(
        "src.data.day0_oracle_anomaly.is_day0_family_paused",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        era,
        "_replacement_authority_probability_and_fdr_proof",
        lambda **_kwargs: expected,
    )
    monkeypatch.setattr(
        era,
        "_canonical_probability_and_fdr_proof",
        lambda **_kwargs: pytest.fail("global Day0 must not use canonical probability"),
    )
    conn = sqlite3.connect(":memory:")
    result = era._live_yes_probabilities(
        event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        payload={"_edli_global_auction_prepare": True},
        family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
        conn=conn,
        calibration_conn=conn,
        native_costs={},
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    )
    conn.close()
    assert result is expected


def _global_scope_event(*, city: str, source_run_id: str):
    captured_at = "2026-07-10T08:00:00+00:00"
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date="2026-07-11",
        metric="high",
        source_id="replacement_0_1",
        source_run_id=source_run_id,
        cycle="2026-07-10T00:00:00+00:00",
        track="replacement_0_1_openmeteo_bayes_fusion",
        snapshot_id=f"rmf-{city}|2026-07-11|high|2026-07-10",
        snapshot_hash=source_run_id,
        captured_at=captured_at,
        available_at=captured_at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=3,
        min_members_floor=3,
        completeness_status="COMPLETE",
        required_steps=[],
        observed_steps=[],
        expected_members=3,
        source_run_status="COMPLETE",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|2026-07-11|high",
        source="global-auction-current-scope",
        observed_at=captured_at,
        available_at=captured_at,
        received_at=captured_at,
        payload=payload,
        causal_snapshot_id=payload.snapshot_id,
    )


@pytest.mark.parametrize(
    "bootstrap_basis",
    (
        "global_simplex_v1",
        "global_simplex_current_finite_moment_evidence_v3",
    ),
)
def test_current_global_probability_prepare_does_not_require_price_snapshot(
    monkeypatch,
    bootstrap_basis,
):
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.replacement_forecast_hook_factory as hook_factory

    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            posterior_identity_hash TEXT NOT NULL,
            dependency_hash TEXT NOT NULL,
            posterior_config_hash TEXT NOT NULL
        )
        """
    )
    forecast.execute(
        "INSERT INTO forecast_posteriors VALUES "
        "(1, 'db-posterior', 'db-dependency', 'db-config')"
    )
    forecast.execute(
        """
        CREATE TABLE market_events (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ("Dallas", "2026-07-11", "high", "c0", "yes0", "dallas-69-or-below", "69F or below", None, 69.0),
            ("Dallas", "2026-07-11", "high", "c1", "yes1", "dallas-70-71", "70-71F", 70.0, 71.0),
            ("Dallas", "2026-07-11", "high", "c2", "yes2", "dallas-72-or-above", "72F or above", 72.0, None),
        ),
    )
    posterior_bins = (
        ("p0", None, (69.0 - 32.0) * 5.0 / 9.0),
        ("p1", (70.0 - 32.0) * 5.0 / 9.0, (71.0 - 32.0) * 5.0 / 9.0),
        ("p2", (72.0 - 32.0) * 5.0 / 9.0, None),
    )
    probabilities = (0.2, 0.3, 0.5)
    bundle = SimpleNamespace(
        posterior_id=1,
        posterior_identity_hash="posterior-1",
        dependency_hash="dependency-1",
        posterior_config_hash="config-1",
        q={key: probability for (key, _lo, _hi), probability in zip(posterior_bins, probabilities)},
        provenance_json={
            "q_bootstrap_samples_basis": bootstrap_basis,
            "q_bootstrap_samples_by_bin": {
                key: [probability] * 400
                for (key, _lo, _hi), probability in zip(posterior_bins, probabilities)
            },
            "bin_topology": [
                {"bin_id": key, "lower_c": lower, "upper_c": upper}
                for key, lower, upper in posterior_bins
            ],
        },
        source_cycle_time="2026-07-10T00:00:00+00:00",
        source_available_at="2026-07-10T06:00:00+00:00",
    )
    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
        lambda *args, **kwargs: object(),
    )
    bundle_read: dict[str, object] = {}

    def read_bundle(*args, **kwargs):
        bundle_read.update(kwargs)
        return SimpleNamespace(
            ok=True,
            bundle=bundle,
            reason_code="READY",
        )

    monkeypatch.setattr(bundle_reader, "read_replacement_forecast_bundle", read_bundle)

    traced: list[str] = []
    forecast.set_trace_callback(traced.append)
    prepared = era._prepare_current_global_probability_family(
        _global_scope_event(city="Dallas", source_run_id="run-dallas"),
        forecast_conn=forecast,
        topology_conn=forecast,
        decision_time=_dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
        max_age=_dt.timedelta(seconds=30),
    )
    forecast.set_trace_callback(None)

    witness = prepared.probability_witness
    assert prepared.candidate_seeds == ()
    assert witness.yes_q_samples.shape == (400, 3)
    assert witness.band_basis == "replacement_served_current_simplex_v1"
    assert bundle.provenance_json["q_bootstrap_samples_basis"] == bootstrap_basis
    assert [binding.yes_token_id for binding in witness.bindings] == ["yes0", "yes1", "yes2"]
    assert all(binding.no_token_id is None for binding in witness.bindings)
    assert witness.yes_q_samples[0].tolist() == pytest.approx(list(probabilities))
    assert (1.0 - witness.yes_q_samples[:, 1]).tolist() == pytest.approx([0.7] * 400)
    assert witness.posterior_identity_hash == "posterior-1"
    assert len(str(bundle_read["current_bin_topology_hash"])) == 64
    assert sum("FROM MARKET_EVENTS" in statement.upper() for statement in traced) == 1
    assert not any(
        "SELECT POSTERIOR_IDENTITY_HASH, DEPENDENCY_HASH, POSTERIOR_CONFIG_HASH"
        in statement.upper()
        for statement in traced
    )


def test_live_adapter_routes_global_scope_through_world_connection(monkeypatch):
    import src.data.polymarket_client as polymarket_client
    import src.engine.global_auction_universe as universe

    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured = {}

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
        portfolio_state_provider=lambda: pytest.fail(
            "cycle-start portfolio must not back global selection wealth"
        ),
    )
    event = _global_scope_event(city="Dallas", source_run_id="run-dallas")

    result = adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    assert result.events == (event,)
    assert captured["world_conn"] is world
    assert captured["forecast_conn"] is forecast
    assert captured["world_conn"] is not topology
    assert captured["portfolio_state_provider"] is None
    metadata_calls = []
    bind_calls = []
    metadata_key = ("condition", "yes-token")
    metadata = {"condition_id": "condition", "active": True}

    def fake_bind(_forecast_conn, *, probability_witnesses, metadata_sink, **_):
        bind_calls.append(1)
        if len(bind_calls) == 1:
            metadata_sink[metadata_key] = metadata
        return probability_witnesses

    def fake_capture(_trade_conn, *, metadata_overrides, **_):
        metadata_calls.append(dict(metadata_overrides))
        return SimpleNamespace(witness_identity=f"book-{len(metadata_calls)}")

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakeClient)
    provider = captured["current_book_epoch_provider"]
    probabilities = {"family": object()}
    provider(probabilities, _dt.datetime.now(_dt.timezone.utc))
    provider(probabilities, _dt.datetime.now(_dt.timezone.utc))

    assert metadata_calls == [
        {metadata_key: metadata},
        {metadata_key: metadata},
    ]


def test_global_curve_supersession_keeps_typed_current_candidate():
    candidate = object()
    reason = (
        "GLOBAL_ACTUATION_EXECUTION_BINDING_SUPERSEDED:curve_economics:"
        "detail=prefix_price"
    )
    exc = era._GlobalCurveSuperseded(reason, candidate)
    receipt = era.EventSubmissionReceipt(
        False,
        "event-1",
        "snapshot-1",
        reason=str(exc),
        global_jit_candidate=exc.replacement_candidate,
    )

    assert era._global_curve_supersession_from_receipt(receipt) == (
        "CURVE_SUPERSEDED",
        candidate,
        reason,
    )
    missing = replace(receipt, global_jit_candidate=None)
    assert era._global_curve_supersession_from_receipt(missing) == (
        "BLOCKED",
        None,
        f"{reason}:replacement_candidate_missing",
    )


def test_current_global_scope_uses_latest_day0_carrier_per_family():
    forecast_alpha = _global_scope_event(city="Alpha", source_run_id="run-a")
    forecast_beta = _global_scope_event(city="Beta", source_run_id="run-b")
    day0_payload = Day0ExtremeUpdatedPayload(
        city="Alpha",
        target_date="2026-07-11",
        metric="high",
        settlement_source="WU",
        station_id="ALPHA-WU",
        observation_time="2026-07-10T08:09:00+00:00",
        observation_available_at="2026-07-10T08:10:00+00:00",
        raw_value=21.2,
        rounded_value=21,
        high_so_far=21.2,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    day0_alpha = make_day0_extreme_updated_event(
        entity_key="Alpha|2026-07-11|high|ALPHA-WU",
        source="day0_observation",
        observed_at=day0_payload.observation_time,
        received_at="2026-07-10T08:10:01+00:00",
        payload=day0_payload,
        causal_snapshot_id="day0-alpha-0810",
    )

    forecast_only = current_global_auction_scope_from_events(
        (forecast_alpha, forecast_beta),
        captured_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
    )
    merged_events = current_global_scope_events_with_day0(
        (forecast_alpha, forecast_beta),
        (day0_alpha,),
    )
    merged = current_global_auction_scope_from_events(
        merged_events,
        captured_at_utc=_dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    assert len(merged.events) == 2
    assert merged.events_by_family[0][1].event_id == day0_alpha.event_id
    assert merged.events_by_family[0][1].event_type == "DAY0_EXTREME_UPDATED"
    assert merged.events_by_family[1][1].event_id == forecast_beta.event_id
    assert merged.scope_identity != forecast_only.scope_identity


def test_day0_entry_scope_requires_target_city_current_local_day():
    current = _dt.datetime(2026, 7, 10, 12, 0, tzinfo=_dt.timezone.utc)

    assert _day0_event_is_current_for_entry(
        {"city": "London", "target_date": "2026-07-10"},
        decision_at_utc=current,
    )
    assert not _day0_event_is_current_for_entry(
        {"city": "London", "target_date": "2026-07-09"},
        decision_at_utc=current,
    )
    assert not _day0_event_is_current_for_entry(
        {"city": "London", "target_date": "2026-07-11"},
        decision_at_utc=current,
    )


def _insert_event(conn, event):
    fields = tuple(event.__dataclass_fields__)
    conn.execute(
        f"INSERT INTO opportunity_events ({','.join(fields)}) "
        f"VALUES ({','.join('?' for _ in fields)})",
        tuple(getattr(event, field) for field in fields),
    )


def _current_day0_scope_event(*, city, target_date, available_at):
    payload = {
        "city": city,
        "target_date": target_date,
        "metric": "high",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"{city}|{target_date}|high|{available_at}",
        source="day0_observation",
        observed_at=available_at,
        available_at=available_at,
        received_at=available_at,
        payload=payload,
        causal_snapshot_id=f"day0-{city}-{target_date}",
    )


def test_current_day0_query_uses_utc_window_and_target_date_index(monkeypatch):
    import src.config as config

    decision_at = _dt.datetime(2026, 7, 10, 11, 30, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(
        config,
        "runtime_cities_by_name",
        lambda: {
            "West": SimpleNamespace(timezone="Etc/GMT+12"),
            "Center": SimpleNamespace(timezone="UTC"),
            "East": SimpleNamespace(timezone="Pacific/Kiritimati"),
            "Old": SimpleNamespace(timezone="UTC"),
            "Future": SimpleNamespace(timezone="UTC"),
        },
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_opportunity_events_table(conn)
    for city, target_date in (
        ("West", "2026-07-09"),
        ("Center", "2026-07-10"),
        ("East", "2026-07-11"),
        ("Old", "2026-07-08"),
        ("Future", "2026-07-12"),
    ):
        _insert_event(
            conn,
            _current_day0_scope_event(
                city=city,
                target_date=target_date,
                available_at="2026-07-10T11:00:00+00:00",
            ),
        )
    _insert_event(
        conn,
        _current_day0_scope_event(
            city="Center",
            target_date="2026-07-10",
            available_at="2026-07-10T10:00:00+00:00",
        ),
    )

    executed_sql = []
    conn.set_trace_callback(executed_sql.append)
    events = _current_day0_events(conn, decision_at_utc=decision_at)

    events_by_city = {
        json.loads(event.payload_json)["city"]: event for event in events
    }
    assert set(events_by_city) == {"West", "Center", "East"}
    assert events_by_city["Center"].available_at == "2026-07-10T11:00:00+00:00"
    sql = next(
        sql
        for sql in executed_sql
        if "INDEXED BY idx_opportunity_events_fsr_target_date" in sql
    )
    assert "BETWEEN '2026-07-09' AND '2026-07-11'" in sql
    plan = " ".join(
        row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    ).upper()
    assert (
        "SEARCH OPPORTUNITY_EVENTS USING INDEX IDX_OPPORTUNITY_EVENTS_FSR_TARGET_DATE"
        in plan
    )
    assert "SCAN OPPORTUNITY_EVENTS" not in plan
    assert "USE TEMP B-TREE" not in plan


@pytest.fixture(autouse=True)
def _fast_band_draws(monkeypatch):
    monkeypatch.setattr(bridge, "SPINE_BAND_DRAWS", 400, raising=False)


def _corpus():
    """A small (family, proofs, payload) corpus: a +edge trade and an overpriced no-trade."""
    fam_a, _ = R._three_bin_family()
    trade_proofs = R._proofs_for(
        fam_a, yes_asks=[0.05, 0.20, 0.20, 0.05], no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    trade = (
        fam_a,
        trade_proofs,
        _payload_with_joint_samples(
            trade_proofs,
            R._payload_with_spine_inputs(
                mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7]
            ),
        ),
    )
    fam_b, _ = R._three_bin_family()
    no_trade_proofs = R._proofs_for(
        fam_b, yes_asks=[0.60, 0.60, 0.60, 0.60], no_asks=[0.60, 0.60, 0.60, 0.60],
        q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    no_trade = (
        fam_b,
        no_trade_proofs,
        _payload_with_joint_samples(
            no_trade_proofs,
            R._payload_with_spine_inputs(
                mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7]
            ),
        ),
    )
    return [trade, no_trade]


def _serialize(result) -> str:
    """Canonical serialization of a SpineDecisionResult for byte-identity comparison."""
    d = result.decision
    sel = getattr(d, "selected", None) if d is not None else None
    parts = [
        f"decided_by_spine={getattr(result, 'decided_by_spine', None)}",
        f"no_trade_reason={result.no_trade_reason!r}",
        f"selected_proof={getattr(getattr(result, 'selected_proof', None), 'token_id', None)!r}",
    ]
    if d is not None:
        parts += [
            f"decision_id={d.decision_id!r}", f"receipt_hash={d.receipt_hash!r}",
            f"no_trade={d.no_trade_reason!r}", f"n_candidates={len(d.candidates)}",
            f"n_candidate_decisions={len(d.candidate_decisions)}",
        ]
    if sel is not None:
        parts += [
            f"sel_route={sel.route_id!r}", f"sel_stake={sel.optimal_stake_usd}",
            f"sel_du={sel.optimal_delta_u!r}",
        ]
    return "|".join(parts)


def _set_flag(value):
    """Set the flag dict entry (None => absent). Returns a restore callable."""
    from src.config import settings

    ff = settings["feature_flags"]
    had = "w3_solve_enabled" in ff
    prev = ff.get("w3_solve_enabled")
    if value is None:
        ff.pop("w3_solve_enabled", None)
    else:
        ff["w3_solve_enabled"] = value

    def _restore():
        if had:
            ff["w3_solve_enabled"] = prev
        else:
            ff.pop("w3_solve_enabled", None)

    return _restore


# --- (a) absent-vs-OFF byte-identity ----------------------------------------

def test_g3_absent_vs_off_byte_identical():
    corpus = _corpus()
    restore = _set_flag(None)  # absent
    try:
        assert bridge.w3_solve_enabled() is False
        absent = [_serialize(_drive(f, p, pl)) for f, p, pl in corpus]
    finally:
        restore()
    restore = _set_flag(False)  # explicit OFF
    try:
        assert bridge.w3_solve_enabled() is False
        off = [_serialize(_drive(f, p, pl)) for f, p, pl in corpus]
    finally:
        restore()
    assert absent == off, f"absent vs OFF diverged:\n absent={absent}\n off={off}"
    # the corpus must run the real pipeline (a FamilyDecision produced), not a trivial input-fault
    assert any("decision_id=" in s for s in off), "corpus did not exercise the engine pipeline"


def test_g3_off_ignores_joint_samples_and_keeps_v1_band_identity():
    restore = _set_flag(None)
    try:
        result = _drive(*_corpus()[0])
    finally:
        restore()

    assert result.decision is not None
    band = result.decision.band
    assert band is not None
    assert band.samples.shape[0] == 1
    expected = hashlib.sha256()
    expected.update(b"REACTOR_SERVED_POSTERIOR_DETERMINISTIC_BAND_V1")
    expected.update(result.decision.joint_q.identity_hash.encode("utf-8"))
    expected.update(f"alpha={float(band.alpha):.12f}".encode("utf-8"))
    assert band.sample_hash == expected.hexdigest()


# --- (b) single divergence point --------------------------------------------

def test_g3_flag_consumed_at_exactly_one_site():
    tree = ast.parse(open(_BRIDGE_PATH).read())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "w3_solve_enabled"
    ]
    wraps = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_wrap_engine_with_solve_shim"
    ]
    assert len(calls) == 1, f"w3_solve_enabled() must be consumed at EXACTLY one site, found {len(calls)}"
    assert len(wraps) == 1, f"_wrap_engine_with_solve_shim must be called exactly once, found {len(wraps)}"


# --- (c) ON-mode integration ------------------------------------------------

_SOLVER_ORIGIN_REASONS = (
    "NO_IMPROVING_DISCRETE_PLAN", "NO_EXECUTABLE_MENU_ITEMS", "UNSAFE_PREFIX_DECOMPOSITION",
    "BUDGET_EXCEEDED", "PHASE1_PRIMARY_LEG",
)


def test_g3_on_mode_shim_runs_and_is_contract_valid():
    corpus = _corpus()
    restore = _set_flag(True)
    try:
        assert bridge.w3_solve_enabled() is True
        ran_solver = False
        for f, p, pl in corpus:
            result = _drive(f, p, pl)
            if result.decision is None:
                continue
            # every emitted FamilyDecision satisfies the frozen consumer contract (no getattr
            # default would fire in the facts writer / overlay)
            validate_family_decision_contract(result.decision)
            if result.decision.selected is not None:
                ran_solver = True
                # projection stamped: selected carries the standalone ΔU value
                assert result.decision.selected.optimal_delta_u is not None
            elif result.no_trade_reason and any(k in result.no_trade_reason for k in _SOLVER_ORIGIN_REASONS):
                ran_solver = True  # a solver-origin no-trade proves the solver selection path ran
        # the ON branch physically imported + executed the solver
        assert "src.solve.solver" in sys.modules
        assert ran_solver, "ON-mode did not exercise the solver selection path"
    finally:
        restore()


def test_g3_on_mode_selection_diverges_from_off():
    # The whole point of the seam: ON runs the current-state solver while OFF retains the
    # legacy empirical-guard selector.  A route becoming honestly executable may make both
    # paths trade, so divergence is proven by decision authority rather than by requiring
    # one path to manufacture a no-trade reason.
    trade = _corpus()[0]
    restore = _set_flag(None)
    try:
        off = _drive(*trade)
    finally:
        restore()
    restore = _set_flag(True)
    try:
        on = _drive(*trade)
    finally:
        restore()
    assert off.decision is not None and on.decision is not None
    assert all(
        candidate.q_lcb_guard_basis != "CURRENT_POSTERIOR_BAND"
        for candidate in off.decision.candidate_decisions
    )
    assert on.decision.candidate_decisions
    assert all(
        candidate.q_lcb_guard_basis == "CURRENT_POSTERIOR_BAND"
        for candidate in on.decision.candidate_decisions
    )
    assert any(k in (on.no_trade_reason or "") for k in _SOLVER_ORIGIN_REASONS) or on.decision.selected is not None


def test_g3_on_mode_never_reads_historical_decision_guards(monkeypatch):
    from src.decision.family_decision_engine import FamilyDecisionEngine

    def _history_read_forbidden(*args, **kwargs):
        raise AssertionError("W3_CURRENT_STATE_SOLVE_MUST_NOT_READ_HISTORICAL_GUARDS")

    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_apply_qlcb_reliability_guard",
        _history_read_forbidden,
    )
    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_apply_selection_calibrator_guard",
        _history_read_forbidden,
    )
    restore = _set_flag(True)
    try:
        result = _drive(*_corpus()[0])
    finally:
        restore()

    assert result.decision is not None
    validate_family_decision_contract(result.decision)


def test_g3_on_mode_fails_closed_without_joint_posterior_samples():
    family, proofs, payload = _corpus()[0]
    payload = dict(payload)
    payload.pop("_edli_spine_served_joint_q_samples_by_condition", None)
    restore = _set_flag(True)
    try:
        result = _drive(family, proofs, payload)
    finally:
        restore()

    assert result.decision is None
    assert result.no_trade_reason == "SPINE_INPUTS_UNAVAILABLE:SERVED_JOINT_SAMPLES_MISSING"


def test_global_family_prepare_binds_full_simplex_to_condition_token_pairs():
    family, proofs, payload = _corpus()[0]
    captured_at = "2026-06-13T11:59:59.900000+00:00"
    proofs = tuple(
        replace(proof, row={**proof.row, "captured_at": captured_at})
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    restore = _set_flag(False)
    try:
        result = bridge.decide_family_via_spine(
            family=family,
            payload=payload,
            proofs=proofs,
            decision_time=_dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc),
            native_side_candidate_from_proof=era._native_side_candidate_from_proof,
            global_native_side_candidate_from_proof=(
                era._full_depth_native_side_candidate_from_proof
            ),
            require_global_probability_witness=True,
            global_probability_max_age=_dt.timedelta(seconds=1),
            candidate_bin_id=era._candidate_bin_id,
            payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
            exposure_builder=era._robust_marginal_utility_exposure,
            baseline_usd_provider=lambda: Decimal("1000"),
            per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
            extra_exposure_by_bin_id=None,
        )
    finally:
        restore()

    assert result.global_prepare_reason is None
    prepared = result.global_family
    assert prepared is not None
    probability = prepared.probability_witness
    assert probability.yes_q_samples.shape[0] == 400
    assert all(
        abs(float(row.sum()) - 1.0) < 1e-12
        for row in probability.yes_q_samples
    )
    binding_by_key = {
        (binding.bin_id, "YES"): binding.yes_token_id
        for binding in probability.bindings
    } | {
        (binding.bin_id, "NO"): binding.no_token_id
        for binding in probability.bindings
    }
    assert prepared.candidate_seeds
    for seed in prepared.candidate_seeds:
        candidate = seed.native_candidate
        assert candidate.token_id == binding_by_key[(candidate.bin_id, candidate.side)]
        assert candidate.executable_cost_curve.token_id == candidate.token_id
        materialized = global_candidate_from_native(
            candidate,
            probability_witness=probability,
            ledger_snapshot_id="ledger-current",
            book_captured_at_utc=seed.book_captured_at_utc,
        )
        assert materialized.token_id == candidate.token_id
        assert materialized.probability_witness_identity == probability.witness_identity


def _global_book_metadata_conn(probability):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER NOT NULL,
            fee_details_json TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        )
        """
    )
    for binding in probability.bindings:
        for side, token in (
            ("YES", binding.yes_token_id),
            ("NO", binding.no_token_id),
        ):
            snapshot_id = f"metadata-{binding.condition_id}-{side}"
            conn.execute(
                "INSERT INTO executable_market_snapshots VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    f"market-event-{probability.family_key}",
                    binding.condition_id,
                    token,
                    binding.yes_token_id,
                    binding.no_token_id,
                    1,
                    1,
                    0,
                    1,
                    '{"fee_rate_fraction":0}',
                    "0.01",
                    "5",
                    "2026-07-10T07:59:00+00:00",
                    "2026-07-10T08:00:30+00:00",
                    '{"executable_allowed":true}',
                    '{"unused_append_payload":"must_not_be_read"}',
                ),
            )
            conn.execute(
                "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?)",
                (binding.condition_id, token, snapshot_id),
            )
    return conn


def test_global_book_curve_uses_same_realized_fee_authority_as_jit(monkeypatch):
    observed = []

    def realized_fee(schedule):
        observed.append(schedule)
        return 0.0, "realized_test"

    monkeypatch.setattr(universe, "resolve_taker_fee_fraction", realized_fee)
    curve = universe._global_book_curve(
        family_key="City|2026-07-11|high",
        bin_id="bin-1",
        condition_id="condition-1",
        side="NO",
        token_id="no-1",
        raw_book={
            "hash": "book-1",
            "tick_size": "0.01",
            "min_order_size": "5",
            "asks": [{"price": "0.30", "size": "100"}],
        },
        metadata={"fee_details_json": '{"fee_rate_fraction":0.05}'},
        captured_at_utc=_dt.datetime(
            2026, 7, 11, 3, 0, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    )

    assert observed == pytest.approx([0.05])
    assert curve is not None
    assert curve.fee_model.fee_rate == Decimal("0.0")


def test_current_global_book_epoch_reads_yes_and_no_symmetrically():
    family, proofs, payload = _corpus()[0]
    proofs = tuple(
        replace(
            proof,
            row={**proof.row, "captured_at": "2026-06-13T07:59:59+00:00"},
        )
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    result = bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        global_native_side_candidate_from_proof=era._full_depth_native_side_candidate_from_proof,
        require_global_probability_witness=True,
        global_probability_max_age=_dt.timedelta(seconds=30),
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )
    assert result.global_family is not None
    probability = result.global_family.probability_witness
    conn = _global_book_metadata_conn(probability)
    denied_columns = {"orderbook_depth_json"}

    def metadata_authorizer(action, table, column, _db, _trigger):
        if (
            action == sqlite3.SQLITE_READ
            and table == "executable_market_snapshots"
            and column in denied_columns
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(metadata_authorizer)
    requested = []
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))

    def books(tokens):
        requested.extend(tokens)
        return {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens
        }

    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=books,
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
        batch_size=500,
    )

    expected = 2 * len(probability.bindings)
    assert len(requested) == expected
    assert len(epoch.asset_states) == expected
    assert len(epoch.assets) == expected
    assert {asset.side for asset in epoch.assets} == {"YES", "NO"}
    assert all(asset.curve.token_id == asset.token_id for asset in epoch.assets)

    required_conn = _global_book_metadata_conn(probability)
    denied_columns.clear()
    denied_columns.add("fee_details_json")
    required_conn.set_authorizer(metadata_authorizer)
    required_times = iter((at, at + _dt.timedelta(seconds=1)))
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        capture_current_global_book_epoch(
            required_conn,
            probability_witnesses={probability.family_key: probability},
            get_books=books,
            clock=lambda: next(required_times),
            max_age=_dt.timedelta(seconds=30),
            batch_size=500,
        )


def test_current_global_book_epoch_rejects_one_missing_native_side():
    family, proofs, payload = _corpus()[0]
    proofs = tuple(
        replace(
            proof,
            row={**proof.row, "captured_at": "2026-06-13T07:59:59+00:00"},
        )
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    result = bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        global_native_side_candidate_from_proof=era._full_depth_native_side_candidate_from_proof,
        require_global_probability_witness=True,
        global_probability_max_age=_dt.timedelta(seconds=30),
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )
    assert result.global_family is not None
    probability = result.global_family.probability_witness
    conn = _global_book_metadata_conn(probability)
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))

    def incomplete_books(tokens):
        return {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens[:-1]
        }

    with pytest.raises(ValueError, match="GLOBAL_BOOK_RESPONSE_INCOMPLETE:1"):
        capture_current_global_book_epoch(
            conn,
            probability_witnesses={probability.family_key: probability},
            get_books=incomplete_books,
            clock=lambda: next(times),
            max_age=_dt.timedelta(seconds=30),
            batch_size=500,
        )


def test_current_gamma_identity_fills_missing_no_without_changing_q():
    family, proofs, payload = _corpus()[0]
    proofs = tuple(
        replace(
            proof,
            row={**proof.row, "captured_at": "2026-06-13T07:59:59+00:00"},
        )
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    result = bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        global_native_side_candidate_from_proof=era._full_depth_native_side_candidate_from_proof,
        require_global_probability_witness=True,
        global_probability_max_age=_dt.timedelta(seconds=30),
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )
    assert result.global_family is not None
    original = result.global_family.probability_witness
    missing_bindings = tuple(
        OutcomeTokenBinding(
            bin_id=binding.bin_id,
            condition_id=binding.condition_id,
            yes_token_id=binding.yes_token_id,
            no_token_id=(None if index == 0 else binding.no_token_id),
        )
        for index, binding in enumerate(original.bindings)
    )
    missing_identity = joint_probability_witness_identity(
        family_key=original.family_key,
        bindings=missing_bindings,
        q_version=original.q_version,
        resolution_identity=original.resolution_identity,
        topology_identity=original.topology_identity,
        posterior_identity_hash=original.posterior_identity_hash,
        source_truth_identity=original.source_truth_identity,
        authority_certificate_hash=original.authority_certificate_hash,
        band_alpha=original.band_alpha,
        band_basis=original.band_basis,
        yes_q_samples=original.yes_q_samples,
        captured_at_utc=original.captured_at_utc,
    )
    missing = JointOutcomeProbabilityWitness(
        family_key=original.family_key,
        bindings=missing_bindings,
        yes_q_samples=original.yes_q_samples,
        q_version=original.q_version,
        resolution_identity=original.resolution_identity,
        topology_identity=original.topology_identity,
        posterior_identity_hash=original.posterior_identity_hash,
        source_truth_identity=original.source_truth_identity,
        authority_certificate_hash=original.authority_certificate_hash,
        band_alpha=original.band_alpha,
        band_basis=original.band_basis,
        captured_at_utc=original.captured_at_utc,
        max_age=original.max_age,
        witness_identity=missing_identity,
    )
    forecast = sqlite3.connect(":memory:")
    forecast.execute(
        "CREATE TABLE market_events (condition_id TEXT, market_slug TEXT, created_at TEXT)"
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?,?,?)",
        [
            (binding.condition_id, "current-family-slug", "2026-07-10T08:00:00+00:00")
            for binding in missing.bindings
        ],
    )
    gamma_event = {
        "id": "gamma-event-current",
        "markets": [
            {
                "conditionId": binding.condition_id,
                "questionID": f"question-{index}",
                "id": f"market-{index}",
                "question": f"Will the temperature be {index}C?",
                "clobTokenIds": [binding.yes_token_id, original.bindings[index].no_token_id],
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.5", "0.5"],
                "acceptingOrders": True,
                "enableOrderBook": True,
                "active": True,
                "closed": False,
                "feeSchedule": {
                    "exponent": 1,
                    "rate": 0.05,
                    "takerOnly": True,
                    "rebateRate": 0.25,
                },
                "feeType": "weather",
                "orderPriceMinTickSize": "0.01",
                "orderMinSize": "5",
            }
            for index, binding in enumerate(missing.bindings)
        ]
    }

    from src.engine.global_auction_universe import bind_current_global_probability_tokens

    gamma_metadata = {}
    rebound = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: gamma_event if slug == "current-family-slug" else None,
        metadata_sink=gamma_metadata,
    )[missing.family_key]

    assert rebound.bindings[0].no_token_id == original.bindings[0].no_token_id
    assert rebound.sample_matrix_identity == missing.sample_matrix_identity
    assert rebound.q_version == missing.q_version
    assert rebound.witness_identity != missing.witness_identity
    assert rebound.family_binding_identity != missing.family_binding_identity
    assert all(
        getattr(rebound, field) == getattr(missing, field)
        for field in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    )
    assert "family_binding_identity" not in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    assert "authority_certificate_hash" not in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    assert gamma_metadata[
        (rebound.bindings[0].condition_id, rebound.bindings[0].no_token_id)
    ]["fee_details_json"]

    gamma_calls = []
    local = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: gamma_calls.append(slug),
        trade_conn=_global_book_metadata_conn(original),
        checked_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
    )[missing.family_key]
    assert gamma_calls == []
    assert local.bindings == original.bindings
    assert local.sample_matrix_identity == missing.sample_matrix_identity

    stale_calls = []
    stale_fallback = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: stale_calls.append(slug) or gamma_event,
        trade_conn=_global_book_metadata_conn(original),
        checked_at_utc=_dt.datetime(2026, 7, 10, 8, 1, tzinfo=_dt.timezone.utc),
    )[missing.family_key]
    assert stale_calls == ["current-family-slug"]
    assert stale_fallback.bindings == original.bindings

    partial = _global_book_metadata_conn(original)
    missing_condition = missing.bindings[0].condition_id
    partial.execute(
        "DELETE FROM executable_market_snapshot_latest WHERE condition_id = ?",
        (missing_condition,),
    )
    partial_calls = []
    fallback = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: (
            partial_calls.append(slug) or gamma_event
        ),
        trade_conn=partial,
        checked_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
    )[missing.family_key]
    assert partial_calls == ["current-family-slug"]
    assert fallback.bindings == original.bindings

    ambiguous = _global_book_metadata_conn(original)
    ambiguous.execute(
        """
        INSERT INTO executable_market_snapshots
            SELECT 'conflicting-topology', event_id, condition_id,
                   'conflicting-selected', 'conflicting-yes', 'conflicting-no',
                   enable_orderbook, active,
               closed, accepting_orders, fee_details_json, min_tick_size,
               min_order_size, captured_at, freshness_deadline,
               tradeability_status_json, orderbook_depth_json
          FROM executable_market_snapshots
         WHERE condition_id = ?
         LIMIT 1
        """,
        (missing_condition,),
    )
    ambiguous.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?)",
        (missing_condition, "conflicting-selected", "conflicting-topology"),
    )
    with pytest.raises(
        ValueError,
        match=f"GLOBAL_LOCAL_TOKEN_IDENTITY_AMBIGUOUS:{missing_condition}",
    ):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={missing.family_key: missing},
            get_gamma_event=lambda _slug: gamma_event,
            trade_conn=ambiguous,
            checked_at_utc=_dt.datetime(
                2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc
            ),
        )


def test_global_scope_is_independent_of_the_reactor_page_and_current_q_identity():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    first = _global_scope_event(city="Chicago", source_run_id="posterior-chicago-a")
    second = _global_scope_event(city="London", source_run_id="posterior-london-a")

    scope = current_global_auction_scope_from_events(
        (first, second),
        captured_at_utc=decision_at,
    )
    reactor_page = current_global_auction_scope_from_events(
        (first,),
        captured_at_utc=decision_at,
    )
    updated = current_global_auction_scope_from_events(
        (
            _global_scope_event(
                city="Chicago", source_run_id="posterior-chicago-new"
            ),
            second,
        ),
        captured_at_utc=decision_at,
    )

    assert len(scope.family_keys) == 2
    assert set(reactor_page.family_keys) < set(scope.family_keys)
    assert reactor_page.scope_identity != scope.scope_identity
    assert updated.family_keys == scope.family_keys
    assert updated.scope_identity != scope.scope_identity


def test_two_prepared_families_choose_one_globally_unique_order():
    family, proofs, payload = _corpus()[0]
    decision_at = _dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc)
    captured_at = "2026-06-13T11:59:59.900000+00:00"
    proofs = tuple(
        replace(proof, row={**proof.row, "captured_at": captured_at})
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    current_scope = current_global_auction_scope_from_events(
        (
            _global_scope_event(
                city="Chicago", source_run_id="posterior-chicago-current"
            ),
            _global_scope_event(
                city="London", source_run_id="posterior-london-current"
            ),
        ),
        captured_at_utc=decision_at,
    )

    prepared_by_event = {}
    restore = _set_flag(False)
    try:
        for suffix, family_key in zip(("a", "b"), current_scope.family_keys):
            scoped_family = replace(
                family,
                family_id=family_key,
                event_id=f"event-{suffix}",
            )
            result = bridge.decide_family_via_spine(
                family=scoped_family,
                payload=payload,
                proofs=proofs,
                decision_time=decision_at,
                native_side_candidate_from_proof=era._native_side_candidate_from_proof,
                global_native_side_candidate_from_proof=(
                    era._full_depth_native_side_candidate_from_proof
                ),
                require_global_probability_witness=True,
                global_probability_max_age=_dt.timedelta(seconds=1),
                candidate_bin_id=era._candidate_bin_id,
                payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
                exposure_builder=era._robust_marginal_utility_exposure,
                baseline_usd_provider=lambda: Decimal("1000"),
                per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
                extra_exposure_by_bin_id=None,
            )
            assert result.global_family is not None
            prepared_by_event[f"event-{suffix}"] = result.global_family
    finally:
        restore()

    venue_identity = "current-venue-universe"
    wealth_identity = portfolio_wealth_identity(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions-current",
        wealth_floor_usd=Decimal("1000"),
        wealth_ceiling_usd=Decimal("1000"),
        spendable_cash_usd=Decimal("1000"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=decision_at,
    )
    wealth = PortfolioWealthWitness(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions-current",
        wealth_floor_usd=Decimal("1000"),
        wealth_ceiling_usd=Decimal("1000"),
        spendable_cash_usd=Decimal("1000"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=1),
        witness_identity=wealth_identity,
    )
    probabilities = {
        prepared.probability_witness.family_key: prepared.probability_witness
        for prepared in prepared_by_event.values()
    }

    auction_kwargs = dict(
        selection_epoch_identity="selection-epoch-current",
        selection_cut_at_utc=decision_at,
        current_scope=current_scope,
        current_scope_identity_resolver=lambda: current_scope.scope_identity,
        venue_universe_identity=venue_identity,
        current_venue_universe_identity_resolver=lambda: venue_identity,
        universe_max_age=_dt.timedelta(seconds=1),
        current_probability_resolver=lambda key: (
            CurrentFamilyProbabilityAuthority.from_witness(probabilities[key])
        ),
        current_execution_resolver=lambda candidate: CurrentExecutionAuthority(
            token_id=candidate.token_id,
            side=candidate.side,
            book_snapshot_id=candidate.book_snapshot_id,
            execution_curve_identity=candidate.execution_curve_identity,
        ),
        current_wealth_identity_resolver=lambda: wealth.economic_identity,
        wealth_witness=wealth,
        capital_limit_usd=Decimal("100"),
        decision_at_utc=decision_at,
    )
    selected = select_prepared_global_auction(
        prepared_by_event,
        **auction_kwargs,
    )
    fallthrough = select_prepared_global_auction(
        prepared_by_event,
        preflight_excluded_by_family={
            selected.decision.candidate.family_key: "candidate-local-block"
        },
        **auction_kwargs,
    )
    partial = select_prepared_global_auction(
        {"event-a": prepared_by_event["event-a"]},
        **auction_kwargs,
    )

    assert selected.decision.candidate is not None
    assert selected.winner_event_id in prepared_by_event
    assert selected.actuation is not None
    assert selected.actuation.decision == selected.decision
    assert selected.actuation.winner_event_id == selected.winner_event_id
    assert selected.actuation.universe_witness_identity
    assert selected.actuation.wealth_witness_identity == wealth.witness_identity
    assert selected.actuation.selection_epoch_identity == "selection-epoch-current"
    assert selected.actuation.selection_cut_at_utc == decision_at
    later_actuation_identity = global_single_order_actuation_identity(
        decision=selected.decision,
        winner_event_id=selected.winner_event_id,
        universe_witness_identity=selected.actuation.universe_witness_identity,
        wealth_witness_identity=selected.actuation.wealth_witness_identity,
        selection_epoch_identity=selected.actuation.selection_epoch_identity,
        selection_cut_at_utc=selected.actuation.selection_cut_at_utc,
        decision_at_utc=decision_at + _dt.timedelta(seconds=30),
    )
    assert later_actuation_identity != selected.actuation.actuation_identity
    assert selected.actuation.economic_identity == global_single_order_economic_identity(
        decision=selected.decision,
        probability_witness=selected.actuation.probability_witness,
        wealth_economic_identity=wealth.economic_identity,
    )
    assert fallthrough.decision.candidate is not None
    assert (
        fallthrough.decision.candidate.family_key
        != selected.decision.candidate.family_key
    )
    assert fallthrough.winner_event_id != selected.winner_event_id
    assert partial.decision.candidate is None
    assert partial.actuation is None
    assert partial.decision.no_trade_reason == "GLOBAL_FEASIBLE_SET_INCOMPLETE"
    all_ids = {
        global_candidate_from_native(
            seed.native_candidate,
            probability_witness=prepared.probability_witness,
            ledger_snapshot_id=wealth.ledger_snapshot_id,
            book_captured_at_utc=seed.book_captured_at_utc,
        ).candidate_id
        for prepared in prepared_by_event.values()
        for seed in prepared.candidate_seeds
    }
    assert len(all_ids) == sum(
        len(prepared.candidate_seeds) for prepared in prepared_by_event.values()
    )


def _wealth_test_conn(*, captured_at: _dt.datetime, ctf: dict[str, int] | None = None):
    conn = sqlite3.connect(":memory:")
    init_collateral_schema(conn)
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            25_000_000,
            20_000_000,
            2_000_000,
            json.dumps(ctf or {}),
            "{}",
            0,
            "{}",
            captured_at.isoformat(),
            "CHAIN",
            "wallet-hash",
        ),
    )
    return conn


def test_current_portfolio_wealth_witness_uses_one_chain_generation():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )
    repeated = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.spendable_cash_usd == Decimal("20")
    assert witness.wealth_floor_usd == Decimal("22")
    assert witness.wealth_ceiling_usd == Decimal("22")
    assert repeated.witness_identity == witness.witness_identity


def test_current_portfolio_wealth_economic_identity_ignores_heartbeat_time_only():
    first_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    second_at = first_at + _dt.timedelta(seconds=30)
    conn = _wealth_test_conn(captured_at=first_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at=first_at.isoformat(),
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    first = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=first_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") SELECT pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,?,?,"
        "raw_balance_payload_hash FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1",
        (second_at.isoformat(), "CHAIN"),
    )
    portfolio.positions[0].chain_verified_at = second_at.isoformat()
    second = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=second_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )

    assert second.witness_identity != first.witness_identity
    assert second.economic_identity == first.economic_identity


def test_current_portfolio_wealth_economic_identity_changes_with_cash():
    first_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    second_at = first_at + _dt.timedelta(seconds=1)
    conn = _wealth_test_conn(captured_at=first_at)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    first = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=first_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (19_000_000, 20_000_000, 2_000_000, "{}", "{}", 0, "{}", second_at.isoformat(), "CHAIN", "changed"),
    )
    second = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=second_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )

    assert second.economic_identity != first.economic_identity


def test_current_portfolio_wealth_uses_fresh_synced_positions_when_ctf_mirror_empty():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at=decision_at.isoformat(),
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_floor_usd == Decimal("22")
    assert witness.wealth_ceiling_usd == Decimal("25.25")


def test_current_portfolio_wealth_uses_fresh_ctf_mirror_over_stale_projection_time():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(
        captured_at=decision_at,
        ctf={"yes-token": 3_250_000},
    )
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at="2026-07-10T07:00:00+00:00",
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_ceiling_usd == Decimal("25.25")


@pytest.mark.parametrize(
    ("chain_state", "chain_verified_at", "reason"),
    [
        ("unknown", "2026-07-10T08:00:00+00:00", "CHAIN_STATE_UNVERIFIED"),
        ("synced", "2026-07-10T07:29:00+00:00", "CHAIN_EXPIRED"),
    ],
)
def test_current_portfolio_wealth_refuses_unverified_position_inventory(
    chain_state, chain_verified_at, reason
):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state=chain_state,
                chain_shares=1.0,
                chain_verified_at=chain_verified_at,
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    with pytest.raises(ValueError, match=reason):
        current_portfolio_wealth_witness(
            conn,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
        )


def test_current_portfolio_wealth_witness_refuses_inflight_or_unknown_inventory():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    reserved = _wealth_test_conn(captured_at=decision_at)
    reserved.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        ("cmd", "PUSD_BUY", None, 1_000_000, decision_at.isoformat()),
    )
    with pytest.raises(ValueError, match="CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS"):
        current_portfolio_wealth_witness(
            reserved,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
        )

    unknown = _wealth_test_conn(captured_at=decision_at, ctf={"unknown-token": 1_000_000})
    with pytest.raises(ValueError, match="CURRENT_WEALTH_CHAIN_POSITION_SET_MISMATCH"):
        current_portfolio_wealth_witness(
            unknown,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
        )


def test_global_batch_waits_until_global_winner_family_is_claimed(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b),
        captured_at_utc=decision_at,
    )
    prepared = {
        event_a.event_id: SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=scope.family_keys[0],
                captured_at_utc=decision_at,
                posterior_identity_hash="run-a",
            )
        ),
        event_b.event_id: SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=scope.family_keys[1],
                captured_at_utc=decision_at,
                posterior_identity_hash="run-b",
            )
        ),
    }
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event_b.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-b",
            economic_identity="economic-b",
        ),
    )
    monkeypatch.setattr(global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope)
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(global_batch_runtime, "current_venue_auction_identity", lambda *_, **__: "venue")
    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected)

    result = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: __import__("json").loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=lambda *_: pytest.fail("unclaimed winner must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.next_claim_event is not None
    assert result.next_claim_event.event_id != event_b.event_id
    assert result.next_claim_event.event_type == event_b.event_type
    assert result.next_claim_event.causal_snapshot_id == event_b.causal_snapshot_id
    assert result.next_claim_event.payload_json == event_b.payload_json
    assert result.next_claim_event.source.endswith(":economic-b")
    repeated = global_batch_runtime._next_claim_carrier(
        event_b,
        targeted_at=decision_at + _dt.timedelta(seconds=30),
        economic_identity="economic-b",
        payload=__import__("json").loads(event_b.payload_json),
    )
    assert repeated.event_id == result.next_claim_event.event_id
    assert result.receipts[event_a.event_id].reason == "GLOBAL_WINNER_AWAITS_CLAIM"


def test_global_batch_claims_unpaged_cut_time_winner_and_continues_actuation(
    monkeypatch,
):
    from src.engine.global_single_order_auction import (
        GlobalSingleOrderActuation,
        PreparedGlobalAuctionResult,
    )

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    family_a, family_b = scope.family_keys

    def _witness(family_key, suffix):
        return SimpleNamespace(
            family_key=family_key,
            witness_identity=f"probability-{suffix}",
            posterior_identity_hash=f"run-{suffix}",
            q_version=f"q-{suffix}",
            family_binding_identity=f"family-binding-{suffix}",
            sample_matrix_identity=f"sample-matrix-{suffix}",
            band_alpha=0.05,
            band_basis="lower-tail",
            captured_at_utc=decision_at,
        )

    witness_a = _witness(family_a, "a")
    witness_b = _witness(family_b, "b")
    curve = SimpleNamespace(
        book_hash="book-b",
        levels=(SimpleNamespace(price=Decimal("0.40"), size=Decimal("10")),),
        fee_model=SimpleNamespace(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    candidate = SimpleNamespace(
        candidate_id="candidate-b",
        family_key=family_b,
        bin_id="20C",
        condition_id="condition-b",
        side="YES",
        token_id="token-b",
        probability_witness_identity=witness_b.witness_identity,
        book_snapshot_id="book-snapshot-b",
        execution_curve_identity="curve-b",
        executable_cost_curve=curve,
        resolution_identity="resolution-b",
    )
    decision = SimpleNamespace(
        candidate=candidate,
        shares=Decimal("10"),
        cost_usd=Decimal("4"),
        limit_price=Decimal("0.40"),
        expected_fill_price_before_fee=Decimal("0.40"),
        max_spend_usd=Decimal("4"),
        robust_delta_log_wealth=0.01,
        robust_ev_usd=1.0,
        capital_efficiency=0.25,
        no_trade_reason=None,
    )
    wealth_economic_identity = "wealth-economic"
    economic_identity = global_single_order_economic_identity(
        decision=decision,
        probability_witness=witness_b,
        wealth_economic_identity=wealth_economic_identity,
    )
    actuation_identity = global_single_order_actuation_identity(
        decision=decision,
        winner_event_id=event_b.event_id,
        universe_witness_identity="universe",
        wealth_witness_identity="wealth-witness",
        selection_epoch_identity="selection-epoch",
        selection_cut_at_utc=decision_at,
        decision_at_utc=decision_at,
    )
    selected = PreparedGlobalAuctionResult(
        decision=decision,
        winner_event_id=event_b.event_id,
        actuation=GlobalSingleOrderActuation(
            decision=decision,
            winner_event_id=event_b.event_id,
            universe_witness_identity="universe",
            wealth_witness_identity="wealth-witness",
            selection_epoch_identity="selection-epoch",
            probability_witness=witness_b,
            selection_cut_at_utc=decision_at,
            decision_at_utc=decision_at,
            actuation_identity=actuation_identity,
            wealth_economic_identity=wealth_economic_identity,
            economic_identity=economic_identity,
        ),
    )

    @dataclass(frozen=True)
    class _Prepared:
        probability_witness: object

    prepared = {
        event_a.event_id: _Prepared(probability_witness=witness_a),
        event_b.event_id: _Prepared(probability_witness=witness_b),
    }
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-witness",
            economic_identity=wealth_economic_identity,
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )
    selection_calls = [0]

    def _select(*_args, **_kwargs):
        selection_calls[0] += 1
        return selected

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        _select,
    )
    claimed_targets = []
    actuated = []
    venue_calls = [0]

    def _claim(target):
        claimed_targets.append(target)
        return True

    def _actuate(event, actuation, _at):
        actuated.append((event, actuation))
        venue_calls[0] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            reason="SUBMITTED:test",
            proof_accepted=True,
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=_actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        claim_unpaged_winner=_claim,
    )

    assert len(claimed_targets) == 1
    target = claimed_targets[0]
    assert result.next_claim_event is None
    assert result.winner_event_id == target.event_id
    assert result.venue_submit_count == 1
    assert selection_calls[0] == 1
    assert set(result.receipts) == {event_a.event_id, target.event_id}
    assert actuated[0][0] == target
    rebound = actuated[0][1]
    assert rebound.winner_event_id == target.event_id
    assert rebound.actuation_identity != actuation_identity
    assert rebound.economic_identity == economic_identity

    actuated.clear()
    venue_calls[0] = 0
    selection_calls[0] = 0
    resumed = global_batch_runtime.process_current_global_batch(
        (target,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=_actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        claim_unpaged_winner=lambda _target: pytest.fail(
            "an already-claimed deterministic target must not be claimed again"
        ),
    )

    assert resumed.next_claim_event is None
    assert resumed.winner_event_id == target.event_id
    assert resumed.venue_submit_count == 1
    assert selection_calls[0] == 1
    assert set(resumed.receipts) == {target.event_id}
    assert actuated[0][0] == target

    fence_wealth_economic_identity = "wealth-economic-fence"
    fence_economic_identity = global_single_order_economic_identity(
        decision=decision,
        probability_witness=witness_b,
        wealth_economic_identity=fence_wealth_economic_identity,
    )
    fence_selected = replace(
        selected,
        actuation=replace(
            selected.actuation,
            wealth_economic_identity=fence_wealth_economic_identity,
            economic_identity=fence_economic_identity,
        ),
    )
    selections = iter((selected, fence_selected))
    fence_selection_calls = [0]

    def _select_fence(*_args, **_kwargs):
        fence_selection_calls[0] += 1
        return next(selections)

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        _select_fence,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_book_economics_manifest",
        lambda _epoch: (("book",),),
    )
    fake_epoch = SimpleNamespace(
        max_age=_dt.timedelta(seconds=30),
        witness_identity="book-epoch",
    )
    claimed_targets.clear()
    actuated.clear()
    venue_calls[0] = 0
    fenced = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=_actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        claim_unpaged_winner=_claim,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            fake_epoch,
        ),
        preflight_winner=lambda *_: global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token=object(),
        ),
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda event, actuation, at, _token, _authority: _actuate(
                event, actuation, at
            )
        ),
    )

    assert len(claimed_targets) == 2
    assert claimed_targets[0].event_id != claimed_targets[1].event_id
    assert fenced.winner_event_id == claimed_targets[1].event_id
    assert fenced.venue_submit_count == 1
    assert fence_selection_calls[0] == 2
    assert set(fenced.receipts) == {
        event_a.event_id,
        claimed_targets[0].event_id,
        claimed_targets[1].event_id,
    }


def test_global_batch_excludes_typed_current_q_ineligible_family(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    family_a, family_b = scope.family_keys
    prepared_b = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=family_b,
            captured_at_utc=decision_at,
            posterior_identity_hash="run-b",
        )
    )
    current_probability = object()
    actuation = SimpleNamespace(actuation_identity="actuation-b")
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event_b.event_id,
        actuation=actuation,
    )
    calls = {"venue": 0, "ineligible_prepare": 0}
    ineligible_reason = (
        "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
        "GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED:REPLACEMENT_RAW_INPUT_HWM"
    )

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )

    def select(prepared_by_event, *, current_scope, **_kwargs):
        assert current_scope.family_keys == (family_b,)
        assert tuple(prepared_by_event) == (event_b.event_id,)
        return selected

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)
    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(lambda cls, witness: current_probability),
    )

    def prepare(event, _at):
        if event.event_id == event_a.event_id:
            calls["ineligible_prepare"] += 1
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason=ineligible_reason,
            )
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared_b,
        )

    def actuate(winner, chosen, _at):
        assert winner.event_id == event_b.event_id
        assert chosen is actuation
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert calls["ineligible_prepare"] == 1
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event_b.event_id
    assert result.receipts[event_b.event_id].submitted is True
    assert result.receipts[event_a.event_id].reason == (
        f"GLOBAL_FAMILY_INELIGIBLE:{ineligible_reason}"
    )


def test_global_batch_rejects_unexpected_probability_prepare_failure(monkeypatch):
    import src.data.replacement_input_hwm as input_hwm

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    reason = "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:RuntimeError:boom"
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    selection = sqlite3.connect(":memory:")
    prime_seen = []

    def prepare(current, _at):
        prime_seen.append(input_hwm._FROZEN_INPUT_HWM.get() is not None)
        return EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            reason=reason,
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=selection,
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("unexpected failure must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        selection_snapshot_connections=(selection,),
    )

    assert prime_seen == [True]
    assert input_hwm._FROZEN_INPUT_HWM.get() is None
    assert selection.in_transaction is False
    assert result.venue_submit_count == 0
    assert result.receipts[event.event_id].reason == (
        f"GLOBAL_PREPARED_FAMILY_INCOMPLETE:{scope.family_keys[0]}:{reason}"
    )
    selection.close()


def test_global_batch_actuates_exactly_one_claimed_global_winner(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    duplicate = _global_scope_event(city="Alpha", source_run_id="run-duplicate")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=scope.family_keys[0],
            captured_at_utc=decision_at,
            posterior_identity_hash="run-a",
        )
    )
    actuation = SimpleNamespace(actuation_identity="actuation-a")
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=actuation,
    )
    current_probability = object()
    calls = {"venue": 0, "fractional_kelly_multiplier": None}
    monkeypatch.setattr(global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope)
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(global_batch_runtime, "current_venue_auction_identity", lambda *_, **__: "venue")
    def select(*_, **kwargs):
        calls["fractional_kelly_multiplier"] = kwargs[
            "fractional_kelly_multiplier"
        ]
        return selected

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)
    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(lambda cls, witness: current_probability),
    )

    def actuate(winner, chosen, _at):
        assert winner.event_id == event.event_id
        assert chosen is actuation
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event, duplicate),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: __import__("json").loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        fractional_kelly_multiplier=Decimal("0.03125"),
    )

    assert calls["venue"] == 1
    assert calls["fractional_kelly_multiplier"] == Decimal("0.03125")
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True
    assert result.receipts[duplicate.event_id].reason == (
        f"GLOBAL_DUPLICATE_FAMILY_CARRIER:{event.event_id}"
    )


def test_global_one_shot_actuator_refuses_second_consumption():
    calls = []
    receipt = EventSubmissionReceipt(False, "event")
    actuator = global_batch_runtime.GlobalOneShotActuator(
        lambda value: calls.append(value) or receipt
    )

    assert actuator.consume("first") is receipt
    with pytest.raises(RuntimeError, match="GLOBAL_ACTUATION_CAPABILITY_CONSUMED"):
        actuator.consume("second")
    assert calls == ["first"]


def _global_test_book(identity: str, *, price: str):
    return SimpleNamespace(
        witness_identity=identity,
        max_age=_dt.timedelta(seconds=30),
        assets=(
            SimpleNamespace(
                family_key="family",
                bin_id="bin",
                condition_id="condition",
                market_event_id="market-event",
                side="YES",
                token_id="token",
                curve=SimpleNamespace(
                    fee_model=SimpleNamespace(fee_rate=Decimal("0")),
                    min_tick=Decimal("0.001"),
                    min_order_size=Decimal("1"),
                    levels=(
                        SimpleNamespace(
                            price=Decimal(price),
                            size=Decimal("100"),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_global_jit_overlay_replaces_only_selected_native_curve():
    captured = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    old_curve = era.ExecutableCostCurve(
        token_id="token",
        side="YES",
        snapshot_id="snapshot-old",
        book_hash="book-old",
        levels=(era.BookLevel(price=Decimal("0.40"), size=Decimal("10")),),
        fee_model=era.FeeModel(fee_rate=Decimal("0.01")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    new_curve = replace(
        old_curve,
        snapshot_id="snapshot-new",
        book_hash="book-new",
        levels=(era.BookLevel(price=Decimal("0.41"), size=Decimal("9")),),
    )
    states = (
        (
            "family",
            "bin",
            "condition",
            "YES",
            "token",
            "EXECUTABLE",
            "book-old",
            "market-event",
        ),
    )
    epoch = universe.CurrentGlobalBookEpoch(
        assets=(
            universe.CurrentGlobalBookAsset(
                family_key="family",
                bin_id="bin",
                condition_id="condition",
                market_event_id="market-event",
                side="YES",
                token_id="token",
                curve=old_curve,
                captured_at_utc=captured,
            ),
        ),
        asset_states=states,
        captured_at_utc=captured,
        max_age=_dt.timedelta(seconds=30),
        witness_identity=universe.current_global_book_epoch_identity(
            asset_states=states,
            captured_at_utc=captured,
        ),
    )
    selected = SimpleNamespace(
        family_key="family",
        bin_id="bin",
        condition_id="condition",
        side="YES",
        token_id="token",
        probability_witness_identity="probability",
        resolution_identity="resolution",
        ledger_snapshot_id="ledger",
        book_captured_at_utc=captured,
        execution_curve_identity=global_batch_runtime.executable_curve_identity(old_curve),
    )
    replacement = SimpleNamespace(
        family_key="family",
        bin_id="bin",
        condition_id="condition",
        side="YES",
        token_id="token",
        probability_witness_identity="probability",
        resolution_identity="resolution",
        ledger_snapshot_id="ledger",
        executable_cost_curve=new_curve,
        book_captured_at_utc=captured + _dt.timedelta(seconds=1),
        execution_curve_identity=global_batch_runtime.executable_curve_identity(new_curve),
    )

    overlaid = global_batch_runtime._overlay_current_global_book_epoch(
        epoch,
        selected,
        replacement,
    )

    assert epoch.assets[0].curve is old_curve
    assert overlaid.assets[0].curve is new_curve
    assert overlaid.assets[0].captured_at_utc == replacement.book_captured_at_utc
    assert overlaid.asset_states == (
        (
            "family",
            "bin",
            "condition",
            "YES",
            "token",
            "EXECUTABLE",
            "book-new",
            "market-event",
        ),
    )
    assert overlaid.witness_identity != epoch.witness_identity
    with pytest.raises(ValueError, match="GLOBAL_JIT_OVERLAY_IDENTITY_MISMATCH"):
        global_batch_runtime._overlay_current_global_book_epoch(
            epoch,
            selected,
            SimpleNamespace(**(vars(replacement) | {"resolution_identity": "other"})),
        )


def test_global_batch_reauctions_once_on_full_universe_curve_drift(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    witnesses = {
        family_key: SimpleNamespace(
            family_key=family_key,
            captured_at_utc=decision_at,
            posterior_identity_hash=run_id,
            witness_identity=f"q-{run_id}",
        )
        for family_key, run_id in zip(scope.family_keys, ("run-a", "run-b"))
    }
    prepared = {
        event.event_id: SimpleNamespace(
            probability_witness=witnesses[family_key]
        )
        for event, family_key in zip((event_a, event_b), scope.family_keys)
    }
    actuation_a = SimpleNamespace(
        actuation_identity="actuation-a", wealth_witness_identity="wealth-1"
    )
    actuation_b_fence = SimpleNamespace(
        actuation_identity="actuation-b-fence", wealth_witness_identity="wealth-2"
    )
    actuation_b_final = SimpleNamespace(
        actuation_identity="actuation-b-final", wealth_witness_identity="wealth-3"
    )
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
            winner_event_id=event.event_id,
            actuation=actuation,
        )
        for event, actuation in (
            (event_a, actuation_a),
            (event_b, actuation_b_fence),
            (event_b, actuation_b_final),
        )
    )
    books = iter(
        (
            _global_test_book("book-0", price="0.40"),
            _global_test_book("book-1", price="0.41"),
        )
    )
    replacement_candidate = object()
    calls = {"prepare": 0, "books": 0, "wealth": 0, "preflight": [], "venue": 0}

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )

    def wealth(*_, **__):
        calls["wealth"] += 1
        return SimpleNamespace(
            spendable_cash_usd=Decimal(str(10 + calls["wealth"])),
            witness_identity=f"wealth-{calls['wealth']}",
            economic_identity=f"wealth-economics-{calls['wealth']}",
        )

    monkeypatch.setattr(
        global_batch_runtime, "current_portfolio_wealth_witness", wealth
    )

    def select(_prepared, **kwargs):
        expected_cash = Decimal(str(10 + calls["wealth"]))
        assert kwargs["capital_limit_usd"] == expected_cash
        return next(selections)

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)

    def prepare(event, _at):
        calls["prepare"] += 1
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        )

    def book_provider(probabilities, _at):
        calls["books"] += 1
        return probabilities, next(books)

    def preflight(event, _actuation, _at, _authority):
        calls["preflight"].append(event.event_id)
        if len(calls["preflight"]) == 1:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="CURVE_SUPERSEDED",
                replacement_candidate=replacement_candidate,
                reason="curve moved",
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE", binding_token="binding-b"
        )

    def overlay(book, selected_candidate, replacement):
        assert book.witness_identity == "book-1"
        assert selected_candidate is not None
        assert replacement is replacement_candidate
        return _global_test_book("book-2", price="0.42")

    monkeypatch.setattr(
        global_batch_runtime,
        "_overlay_current_global_book_epoch",
        overlay,
    )

    def actuate_preflighted(event, actuation, _at, token, authority):
        assert event.event_id == event_b.event_id
        assert actuation is actuation_b_final
        assert token == "binding-b"
        assert authority.book_epoch_identity == "book-2"
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate_preflighted
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=book_provider,
    )

    assert calls == {
        "prepare": 2,
        "books": 2,
        "wealth": 3,
        "preflight": [event_b.event_id, event_b.event_id],
        "venue": 1,
    }
    assert result.winner_event_id == event_b.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event_b.event_id].submitted is True


def test_global_batch_falls_through_candidate_local_preflight_block(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    witnesses = {
        family_key: SimpleNamespace(
            family_key=family_key,
            captured_at_utc=decision_at,
            posterior_identity_hash=run_id,
            witness_identity=f"q-{run_id}",
        )
        for family_key, run_id in zip(scope.family_keys, ("run-a", "run-b"))
    }
    prepared = {
        event.event_id: SimpleNamespace(
            probability_witness=witnesses[family_key]
        )
        for event, family_key in zip((event_a, event_b), scope.family_keys)
    }
    candidates = {
        event_a.event_id: SimpleNamespace(family_key=scope.family_keys[0]),
        event_b.event_id: SimpleNamespace(family_key=scope.family_keys[1]),
    }
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(
                candidate=candidates[event.event_id], no_trade_reason=None
            ),
            winner_event_id=event.event_id,
            actuation=SimpleNamespace(
                actuation_identity=actuation_id,
                wealth_witness_identity=wealth_id,
            ),
        )
        for event, actuation_id, wealth_id in (
            (event_a, "actuation-a-initial", "wealth-1"),
            (event_a, "actuation-a-fence", "wealth-2"),
            (event_b, "actuation-b-fallthrough", "wealth-3"),
        )
    )
    books = iter(
        (
            _global_test_book("book-0", price="0.40"),
            _global_test_book("book-1", price="0.41"),
        )
    )
    blocked_reason = "SHIFT_BIN_NO_SUBMIT:SHIFT_OLD_LEG_BELIEF_NOT_WEAKENED"
    calls = {
        "prepare": 0,
        "books": 0,
        "wealth": 0,
        "preflight": [],
        "excluded": [],
        "epoch": [],
        "venue": 0,
    }

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )

    def wealth(*_, **__):
        calls["wealth"] += 1
        return SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity=f"wealth-{calls['wealth']}",
            economic_identity=f"wealth-economics-{calls['wealth']}",
        )

    monkeypatch.setattr(
        global_batch_runtime, "current_portfolio_wealth_witness", wealth
    )

    def select(_prepared, **kwargs):
        calls["excluded"].append(kwargs["preflight_excluded_by_family"])
        calls["epoch"].append(kwargs["selection_epoch_identity"])
        return next(selections)

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)

    def prepare(event, _at):
        calls["prepare"] += 1
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        )

    def book_provider(probabilities, _at):
        calls["books"] += 1
        return probabilities, next(books)

    def preflight(event, _actuation, _at, _authority):
        calls["preflight"].append(event.event_id)
        if event.event_id == event_a.event_id:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="BLOCKED", reason=blocked_reason
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE", binding_token="binding-b"
        )

    def actuate_preflighted(event, actuation, _at, token, _authority):
        assert event.event_id == event_b.event_id
        assert actuation.actuation_identity == "actuation-b-fallthrough"
        assert token == "binding-b"
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate_preflighted
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=book_provider,
    )

    assert calls["prepare"] == 2
    assert calls["books"] == 2
    assert calls["wealth"] == 3
    assert calls["preflight"] == [event_a.event_id, event_b.event_id]
    assert calls["excluded"] == [
        None,
        None,
        {scope.family_keys[0]: blocked_reason},
    ]
    assert calls["epoch"][2] != calls["epoch"][1]
    assert calls["venue"] == 1
    assert result.winner_event_id == event_b.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event_b.event_id].submitted is True
    assert result.receipts[event_a.event_id].reason == (
        f"GLOBAL_PREFLIGHT_FAMILY_INELIGIBLE:{blocked_reason}"
    )


def test_global_batch_second_curve_supersession_exhausts_without_venue(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    calls = {"preflight": 0, "venue": 0}
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected
    )

    def preflight(*_):
        calls["preflight"] += 1
        return global_batch_runtime.GlobalWinnerPreflight(
            status="CURVE_SUPERSEDED",
            replacement_candidate=object(),
            reason=f"curve moved {calls['preflight']}",
        )

    monkeypatch.setattr(
        global_batch_runtime,
        "_overlay_current_global_book_epoch",
        lambda book, _selected, _replacement: book,
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda *_: pytest.fail("must not actuate")
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            _global_test_book("book", price="0.40"),
        ),
    )

    assert calls == {"preflight": 2, "venue": 0}
    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_REAUCTION_EXHAUSTED:curve moved 2"
    )


def test_global_batch_reauction_rejects_probability_cut_drift(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    initial_witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-cut-a",
    )
    drifted_witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-cut-b",
    )
    prepared = SimpleNamespace(probability_witness=initial_witness)
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    calls = {"books": 0, "preflight": 0, "venue": 0}
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected
    )

    def book_provider(probabilities, _at):
        calls["books"] += 1
        rebound = (
            probabilities
            if calls["books"] == 1
            else {scope.family_keys[0]: drifted_witness}
        )
        return rebound, _global_test_book(
            f"book-{calls['books']}", price="0.40"
        )

    def preflight(*_):
        calls["preflight"] += 1
        return global_batch_runtime.GlobalWinnerPreflight(
            status="CURVE_SUPERSEDED",
            replacement_candidate=object(),
            reason="curve moved",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda *_: pytest.fail("must not actuate")
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=book_provider,
    )

    assert calls == {"books": 2, "preflight": 0, "venue": 0}
    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_PREFLIGHT_PROBABILITY_CUT_DRIFT"
    )


def test_global_batch_freezes_cut_then_releases_before_winner_jit(
    monkeypatch, tmp_path
):
    import src.data.replacement_input_hwm as input_hwm

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=scope.family_keys[0],
            captured_at_utc=decision_at,
            posterior_identity_hash="run-a",
        )
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(actuation_identity="actuation-a"),
    )
    current_probability = object()
    path = tmp_path / "batch-cut.db"
    seed = sqlite3.connect(path)
    assert seed.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    seed.execute("CREATE TABLE readiness_state (value TEXT NOT NULL)")
    seed.execute("INSERT INTO readiness_state VALUES ('cut')")
    seed.commit()
    seed.close()
    selection = sqlite3.connect(path)
    writer = sqlite3.connect(path)
    scope_reads = []

    def scan(**_kwargs):
        scope_reads.append(1)
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
        writer.execute("UPDATE readiness_state SET value='after-cut'")
        writer.commit()
        return scope

    def prepare(current, _at):
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
        assert input_hwm._FROZEN_INPUT_HWM.get() is not None
        return EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        )

    def actuate(winner, _chosen, _at):
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "after-cut"
        assert input_hwm._FROZEN_INPUT_HWM.get() is None
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    monkeypatch.setattr(global_batch_runtime, "scan_current_global_auction_scope", scan)
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue-before",
    )
    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected)
    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(lambda cls, witness: current_probability),
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=selection,
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=iter((0, 1)).__next__,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        selection_snapshot_connections=(selection,),
    )

    assert scope_reads == [1]
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True
    assert input_hwm._FROZEN_INPUT_HWM.get() is None
    selection.close()
    writer.close()


def test_global_batch_rejects_mixed_probability_manifest(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    cases = (
        (
            SimpleNamespace(
                family_key=scope.family_keys[0],
                captured_at_utc=decision_at + _dt.timedelta(microseconds=1),
                posterior_identity_hash="run-a",
            ),
            "GLOBAL_PROBABILITY_EPOCH_MIXED_CUT",
        ),
        (
            SimpleNamespace(
                family_key=scope.family_keys[0],
                captured_at_utc=decision_at,
                posterior_identity_hash="run-after-cut",
            ),
            f"GLOBAL_PROBABILITY_EPOCH_CARRIER_MISMATCH:{scope.family_keys[0]}",
        ),
    )
    for witness, expected_reason in cases:
        prepared = SimpleNamespace(probability_witness=witness)
        result = global_batch_runtime.process_current_global_batch(
            (event,),
            decision_time=decision_at,
            world_conn=object(),
            forecast_conn=object(),
            trade_conn=object(),
            payload_reader=lambda current: json.loads(current.payload_json),
            prepare_event=lambda current, _at: EventSubmissionReceipt(
                False,
                current.event_id,
                current.causal_snapshot_id,
                prepared_global_family=prepared,
            ),
            actuate_winner=lambda *_: pytest.fail(
                "a mixed probability manifest must never actuate"
            ),
            stamp_receipt=lambda receipt: receipt,
            venue_submit_count=lambda: 0,
            current_execution=lambda *_: object(),
            current_time_provider=lambda: decision_at,
        )

        assert result.venue_submit_count == 0
        assert result.receipts[event.event_id].reason == expected_reason


def test_global_selection_read_snapshot_holds_one_readiness_cut(tmp_path):
    path = tmp_path / "selection-cut.db"
    seed = sqlite3.connect(path)
    assert seed.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    seed.execute("CREATE TABLE readiness_state (value TEXT NOT NULL)")
    seed.execute("INSERT INTO readiness_state VALUES ('cut')")
    seed.commit()
    seed.close()

    selection = sqlite3.connect(path)
    writer = sqlite3.connect(path)
    release = global_batch_runtime._begin_selection_read_snapshot(
        (selection, selection)
    )
    try:
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
        writer.execute("UPDATE readiness_state SET value='after-cut'")
        writer.commit()
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
    finally:
        release()
    assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "after-cut"
    selection.execute("BEGIN")
    with pytest.raises(
        RuntimeError, match="GLOBAL_SELECTION_SNAPSHOT_CALLER_TXN_OPEN"
    ):
        global_batch_runtime._begin_selection_read_snapshot((selection,))
    selection.rollback()
    selection.close()
    writer.close()


def test_global_selection_schema_reads_are_cached_only_inside_owned_snapshot():
    import src.data.market_topology_rows as topology_rows

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    release_snapshot = global_batch_runtime._begin_selection_read_snapshot((conn,))
    release_schema = topology_rows.prime_frozen_schema_reads((conn,))
    try:
        for _ in range(2):
            assert "main" in topology_rows._database_names(conn)
            assert topology_rows._table_ref_exists(conn, "sample") is True
            assert topology_rows._table_ref_columns(conn, "sample") == {"value"}
    finally:
        release_schema()
        release_snapshot()

    assert "main" in topology_rows._database_names(conn)
    assert topology_rows._table_ref_exists(conn, "sample") is True
    assert topology_rows._table_ref_columns(conn, "sample") == {"value"}
    conn.set_trace_callback(None)

    normalized = [" ".join(statement.upper().split()) for statement in traced]
    assert sum(statement == "PRAGMA DATABASE_LIST" for statement in normalized) == 2
    assert sum(
        "FROM SQLITE_MASTER" in statement and "NAME = 'SAMPLE'" in statement
        for statement in normalized
    ) == 2
    assert sum(statement == "PRAGMA TABLE_INFO(SAMPLE)" for statement in normalized) == 2


# --- (d) OFF-path import-isolation (subprocess) -----------------------------

def test_g3_off_path_does_not_import_src_solve():
    script = textwrap.dedent(
        """
        import sys, datetime
        from decimal import Decimal
        from src.config import settings
        settings["feature_flags"].pop("w3_solve_enabled", None)  # OFF/absent
        import src.engine.qkernel_spine_bridge as bridge
        import src.engine.event_reactor_adapter as era
        from src.strategy import utility_ranker
        bridge.SPINE_BAND_DRAWS = 400
        from tests.integration import test_qkernel_spine_routing as R
        fam, _ = R._three_bin_family()
        proofs = R._proofs_for(fam, yes_asks=[0.05,0.20,0.20,0.05], no_asks=[0.92,0.75,0.75,0.92],
                               q_by_bin=[0.05,0.45,0.40,0.10], q_lcb_by_bin=[0.02,0.32,0.28,0.05])
        payload = R._payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8,20.1,20.5,21.0,20.7])
        assert bridge.w3_solve_enabled() is False
        _ = bridge.decide_family_via_spine(  # a full decide with the flag OFF
            family=fam, payload=payload, proofs=proofs,
            decision_time=datetime.datetime(2026,6,13,12,0,tzinfo=datetime.timezone.utc),
            native_side_candidate_from_proof=era._native_side_candidate_from_proof,
            candidate_bin_id=era._candidate_bin_id,
            payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
            exposure_builder=era._robust_marginal_utility_exposure,
            baseline_usd_provider=lambda: Decimal("1000"),
            per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs), extra_exposure_by_bin_id=None,
        )
        leaked = [m for m in sys.modules if m.startswith('src.solve')]
        assert not leaked, f'OFF path imported src.solve: {leaked}'
        print('ISOLATION_OK')
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=".")
    assert "ISOLATION_OK" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
