# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §13 reactor no direct venue adapter contract.
from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.contracts.execution_price import ExecutionPrice
from src.engine import evaluator
from src.engine import cycle_runtime
from src.engine.discovery_mode import DiscoveryMode
from src.engine.event_reactor_adapter import (
    discovery_mode_for_event,
    edli_source_truth_gate,
    edli_trade_score_gate,
    executable_snapshot_gate_from_trade_conn,
    riskguard_allows_new_entries,
    submit_existing_cycle_for_event,
)
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.riskguard.risk_level import RiskLevel
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family
from src.types import Bin


def test_reactor_never_imports_venue_adapter():
    path = Path("src/events/reactor.py")
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert all("venue_adapter" not in imported for imported in imports)


def test_engine_adapter_builds_reactor_without_side_effect_submit():
    source = Path("src/engine/event_reactor_adapter.py").read_text()
    assert "venue_adapter" not in source
    assert "execute_final_intent" not in source


def _forecast_event(completeness: str = "COMPLETE"):
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        source_id="ecmwf_open_data",
        source_run_id="run-1",
        cycle="2026-05-24T00:00:00+00:00",
        track="operational",
        snapshot_id="snapshot-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-24T08:00:00+00:00",
        available_at="2026-05-24T08:10:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status=completeness,  # type: ignore[arg-type]
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-25|high|run-1",
        source="forecast_snapshot_ready_trigger",
        observed_at=payload.captured_at,
        available_at=payload.available_at,
        received_at="2026-05-24T08:11:00+00:00",
        causal_snapshot_id=payload.snapshot_id,
        payload=payload,
    )


def test_adapter_source_truth_allows_complete_forecast_only():
    complete = _forecast_event("COMPLETE")
    partial_payload = json.loads(complete.payload_json)
    partial_payload["completeness_status"] = "PARTIAL_ALLOWED"
    partial = replace(complete, payload_json=json.dumps(partial_payload, sort_keys=True, separators=(",", ":")))

    assert edli_source_truth_gate(complete) is True
    assert edli_source_truth_gate(partial) is False


def test_adapter_trade_score_gate_uses_robust_trade_score_inputs():
    event = _forecast_event()
    payload = json.loads(event.payload_json)
    payload.update(
        {
            "p_fill_lcb": 0.5,
            "q_5pct": 0.62,
            "q_posterior": 0.64,
            "c_95pct": 0.55,
            "c_stress": 0.56,
            "lambda_edge": 0.01,
            "lambda_stress": 0.01,
        }
    )
    positive = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    payload["c_95pct"] = 0.70
    negative = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))

    assert edli_trade_score_gate(positive) is True
    assert edli_trade_score_gate(negative) is False
    assert edli_trade_score_gate(event) is False


def test_adapter_maps_events_to_existing_discovery_modes():
    forecast = _forecast_event()

    assert discovery_mode_for_event(forecast) == DiscoveryMode.UPDATE_REACTION


def test_submit_existing_cycle_reports_no_submit_when_existing_path_builds_none():
    event = _forecast_event()
    seen = []

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda mode, **kwargs: seen.append((mode, kwargs.get("edli_event_context")))
        or {"final_intents_built": 0, "entry_orders_submitted": 0},
    )

    assert result.submitted is False
    assert result.reason == "EXISTING_CYCLE_NO_SUBMIT"
    assert seen[0][0] == DiscoveryMode.UPDATE_REACTION
    assert seen[0][1]["event_id"] == event.event_id
    assert seen[0][1]["causal_snapshot_id"] == event.causal_snapshot_id
    assert seen[0][1]["taker_fok_fak_live_enabled"] is False


def test_submit_existing_cycle_threads_edli_taker_flag_false_by_default():
    event = _forecast_event()
    seen = []

    submit_existing_cycle_for_event(
        event,
        run_cycle=lambda _mode, **kwargs: seen.append(kwargs["edli_event_context"]) or {},
    )

    assert seen[0]["taker_fok_fak_live_enabled"] is False


def test_cycle_runtime_edli_final_intent_disables_taker_upgrade_from_config():
    source = Path("src/engine/cycle_runtime.py").read_text()
    assert '"allow_taker_upgrade": (' in source
    assert 'bool((edli_event_context or {}).get("taker_fok_fak_live_enabled"))' in source
    assert 'if not allow_taker_upgrade:' in source


def test_submit_existing_cycle_requires_event_bound_submit_receipt():
    event = _forecast_event()

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda _mode, **_kwargs: {"final_intents_built": 1, "entry_orders_submitted": 0},
    )
    assert result.submitted is False
    assert result.reason == "EXISTING_CYCLE_NO_SUBMIT"


def test_submit_existing_cycle_accepts_event_bound_summary_only():
    event = _forecast_event()

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda _mode, **_kwargs: {
            "final_intents_built": 1,
            "entry_orders_submitted": 0,
            "edli_submit_accepted": True,
            "edli_event_id": event.event_id,
            "causal_snapshot_id": event.causal_snapshot_id,
            "edli_trade_score_positive": True,
            "edli_fdr_pass": True,
            "edli_fdr_family_id": "family-1",
            "edli_fdr_hypothesis_count": 4,
            "edli_kelly_pass": True,
            "edli_kelly_execution_price_type": "ExecutionPrice",
            "edli_kelly_price_fee_deducted": True,
            "edli_kelly_size_usd": 1.0,
            "edli_kelly_cost_basis_id": "cost-1",
            "edli_final_intent_id": "intent-1",
        },
    )

    assert result.submitted is True
    assert result.event_id == event.event_id
    assert result.trade_score_positive is True
    assert result.fdr_pass is True
    assert result.fdr_family_id == "family-1"
    assert result.fdr_hypothesis_count == 4
    assert result.kelly_pass is True
    assert result.kelly_execution_price_type == "ExecutionPrice"
    assert result.kelly_price_fee_deducted is True
    assert result.kelly_size_usd == 1.0
    assert result.kelly_cost_basis_id == "cost-1"
    assert result.final_intent_id == "intent-1"


def test_submit_existing_cycle_rejects_non_durable_executor_result():
    event = _forecast_event()

    result = submit_existing_cycle_for_event(
        event,
        run_cycle=lambda _mode, **_kwargs: {
            "final_intents_built": 1,
            "submit_attempts": 1,
            "submit_rejected": 1,
            "edli_submit_reason": "venue rejected",
            "edli_event_id": event.event_id,
            "causal_snapshot_id": event.causal_snapshot_id,
            "edli_trade_score_positive": True,
            "edli_fdr_pass": True,
            "edli_fdr_family_id": "family-1",
            "edli_fdr_hypothesis_count": 4,
            "edli_kelly_pass": True,
            "edli_kelly_execution_price_type": "ExecutionPrice",
            "edli_kelly_price_fee_deducted": True,
            "edli_kelly_size_usd": 1.0,
            "edli_kelly_cost_basis_id": "cost-1",
            "edli_final_intent_id": "intent-1",
        },
    )

    assert result.submitted is False
    assert result.reason == "venue rejected"


def test_cycle_runtime_filters_markets_to_exact_edli_event_context():
    event = _forecast_event()
    context = {
        "event_id": event.event_id,
        "causal_snapshot_id": event.causal_snapshot_id,
        "city": "Chicago",
        "target_date": "2026-05-25",
        "metric": "high",
        "condition_id": "condition-chicago",
        "token_id": "token-chicago-yes",
    }
    markets = [
        {
            "city_name": "Chicago",
            "target_date": "2026-05-25",
            "temperature_metric": "high",
            "condition_id": "condition-chicago",
            "outcomes": [{"token_id": "token-chicago-yes"}],
        },
        {
            "city_name": "New York",
            "target_date": "2026-05-25",
            "temperature_metric": "high",
            "condition_id": "condition-nyc",
            "outcomes": [{"token_id": "token-nyc-yes"}],
        },
    ]
    summary = {}

    filtered = cycle_runtime._filter_markets_for_edli_event(markets, context, summary)

    assert filtered == [markets[0]]
    assert summary["edli_event_id"] == event.event_id
    assert summary["causal_snapshot_id"] == event.causal_snapshot_id
    assert summary["edli_event_market_filter_before"] == 2
    assert summary["edli_event_market_filter_after"] == 1


def test_cycle_runtime_trade_score_uses_final_execution_price_and_fill_lcb():
    decision = SimpleNamespace(
        edge=SimpleNamespace(ci_lower=0.70, p_posterior=0.72),
        applied_validations=["edli_live_bin_inference_applied"],
        edli_live_inference_proof={"p_live_selected": 0.72, "q_5pct": 0.70},
    )
    final_intent = SimpleNamespace(
        fee_adjusted_execution_price=0.50,
        final_limit_price=0.50,
        passive_maker_context=SimpleNamespace(expected_fill_probability=0.40),
    )

    positive, score, inputs = cycle_runtime._edli_trade_score_from_decision(
        decision,
        final_intent,
        {"corrected_candidate_limit_price": 0.52},
    )

    assert positive is True
    assert score > 0.0
    assert inputs["p_fill_lcb"] == 0.40
    assert inputs["c_95pct"] == 0.50


def test_cycle_runtime_trade_score_requires_live_bin_inference_marker():
    decision = SimpleNamespace(edge=SimpleNamespace(ci_lower=0.70, p_posterior=0.72), applied_validations=[])
    final_intent = SimpleNamespace(
        fee_adjusted_execution_price=0.50,
        final_limit_price=0.50,
        passive_maker_context=SimpleNamespace(expected_fill_probability=0.40),
    )

    positive, score, inputs = cycle_runtime._edli_trade_score_from_decision(
        decision,
        final_intent,
        {"corrected_candidate_limit_price": 0.52},
    )

    assert positive is False
    assert score == 0.0
    assert inputs["blocked"] == "EDLI_LIVE_BIN_INFERENCE_MISSING"


def test_cycle_runtime_trade_score_uses_adverse_penalties():
    decision = SimpleNamespace(
        edge=SimpleNamespace(ci_lower=0.70, p_posterior=0.72),
        applied_validations=["edli_live_bin_inference_applied"],
        edli_live_inference_proof={"p_live_selected": 0.72, "q_5pct": 0.70},
    )
    final_intent = SimpleNamespace(
        fee_adjusted_execution_price=0.50,
        final_limit_price=0.50,
        passive_maker_context=SimpleNamespace(expected_fill_probability=0.40, adverse_selection_score=0.03),
    )

    positive, score, inputs = cycle_runtime._edli_trade_score_from_decision(
        decision,
        final_intent,
        {
            "corrected_candidate_limit_price": 0.52,
            "lambda_source": 0.01,
            "lambda_tail": 0.02,
            "lambda_corr": 0.03,
            "lambda_stress": 0.04,
        },
    )

    assert positive is True
    assert score > 0.0
    assert inputs["lambda_adverse"] == pytest.approx(0.03)
    assert inputs["lambda_edge"] == pytest.approx(0.09)
    assert inputs["lambda_stress"] == pytest.approx(0.04)
    assert inputs["c_stress"] == 0.52


def test_cycle_runtime_live_inference_marker_computes_p_live_for_forecast():
    decision = SimpleNamespace(
        decision_snapshot_id="forecast-snapshot-1",
        edge=SimpleNamespace(
            ci_lower=0.60,
            p_posterior=0.70,
            p_market=0.50,
            bin=SimpleNamespace(label="70-71F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )

    cycle_runtime._mark_edli_live_inference_applied(
        decision,
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "causal_snapshot_id": "forecast-snapshot-1",
            "payload": {
                "completeness_status": "COMPLETE",
                "snapshot_id": "forecast-snapshot-1",
                "source_run_id": "source-run-1",
                "snapshot_hash": "hash-1",
            },
        },
    )

    assert "edli_live_bin_inference_applied" in decision.applied_validations
    assert decision.edli_live_inference_proof["factor"] == "forecast_complete_capped_llr"
    assert decision.edli_live_inference_proof["p_live_selected"] > 0.50
    assert decision.edli_live_inference_proof["causal_snapshot_id"] == "forecast-snapshot-1"


def test_edli_forecast_live_inference_requires_causal_snapshot_proof():
    decision = SimpleNamespace(
        decision_snapshot_id="other-snapshot",
        edge=SimpleNamespace(
            ci_lower=0.60,
            p_posterior=0.70,
            p_market=0.50,
            bin=SimpleNamespace(label="70-71F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )

    with pytest.raises(ValueError, match="EDLI_FORECAST_CAUSAL_SNAPSHOT_PROOF_MISSING"):
        cycle_runtime._mark_edli_live_inference_applied(
            decision,
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "causal_snapshot_id": "forecast-snapshot-1",
                "payload": {"completeness_status": "COMPLETE", "snapshot_id": "forecast-snapshot-1"},
            },
        )


def test_cycle_runtime_day0_live_inference_kills_exceeded_high_bin():
    decision = SimpleNamespace(
        edge=SimpleNamespace(
            ci_lower=0.60,
            p_posterior=0.70,
            p_market=0.50,
            bin=SimpleNamespace(label="70-71F", low=70, high=71),
            direction="buy_yes",
        ),
        applied_validations=[],
    )

    cycle_runtime._mark_edli_live_inference_applied(
        decision,
        {
            "event_type": "DAY0_EXTREME_UPDATED",
            "payload": {"metric": "high", "rounded_value": 72},
        },
    )

    assert decision.edli_live_inference_proof["factor"] == "day0_absorbing_boundary"
    assert decision.edli_live_inference_proof["p_live_selected"] == 0.0


def test_edli_day0_family_mask_sets_remaining_realized_bin_probability_to_one():
    lower = SimpleNamespace(
        edge=SimpleNamespace(
            ci_lower=0.10,
            p_posterior=0.50,
            p_market=0.50,
            bin=SimpleNamespace(label="68-69F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )
    selected = SimpleNamespace(
        edge=SimpleNamespace(
            ci_lower=0.10,
            p_posterior=0.50,
            p_market=0.50,
            bin=SimpleNamespace(label="70-71F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )

    cycle_runtime._mark_edli_live_inference_family_applied(
        [lower, selected],
        {
            "event_type": "DAY0_EXTREME_UPDATED",
            "payload": {"metric": "high", "rounded_value": 70},
        },
    )

    assert lower.edli_live_inference_proof["p_live_selected"] == 0.0
    assert selected.edli_live_inference_proof["p_live_selected"] == 1.0
    assert lower.edge.p_posterior == 0.0
    assert selected.edge.p_posterior == 1.0


def test_edli_pre_submit_fdr_proof_occurs_before_execute_final_call():
    source = Path("src/engine/cycle_runtime.py").read_text()
    live_submit_index = source.index("FINAL_EXECUTION_INTENT_ID_MISMATCH")
    fdr_index = source.index("_assert_edli_pre_submit_fdr_proof(", live_submit_index)
    commit_index = source.index("conn.commit()", fdr_index)
    execute_index = source.index("result = execute_final(")
    assert fdr_index < execute_index
    assert fdr_index < commit_index < execute_index


def test_edli_forecast_family_p_live_does_not_double_apply_snapshot_llr():
    selected = SimpleNamespace(
        decision_snapshot_id="forecast-snapshot-1",
        edge=SimpleNamespace(
            ci_lower=0.65,
            p_posterior=0.70,
            p_market=0.50,
            bin=SimpleNamespace(label="70-71F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )
    sibling = SimpleNamespace(
        decision_snapshot_id="forecast-snapshot-1",
        edge=SimpleNamespace(
            ci_lower=0.25,
            p_posterior=0.30,
            p_market=0.50,
            bin=SimpleNamespace(label="72-73F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )

    cycle_runtime._mark_edli_live_inference_family_applied(
        [selected, sibling],
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "causal_snapshot_id": "forecast-snapshot-1",
            "payload": {
                "completeness_status": "COMPLETE",
                "snapshot_id": "forecast-snapshot-1",
                "source_run_id": "source-run-1",
                "snapshot_hash": "hash-1",
            },
        },
    )

    assert selected.edli_live_inference_proof["factor"] == "forecast_complete_causal_snapshot"
    assert selected.edli_live_inference_proof["llr_cap_applied"] is False
    assert selected.edli_live_inference_proof["p_live_selected"] == pytest.approx(0.70)
    assert selected.edge.p_posterior == pytest.approx(0.70)
    assert sibling.edli_live_inference_proof["p_live_selected"] == pytest.approx(0.30)


def test_edli_forecast_buy_no_p_live_uses_complement_market_prior():
    no_selected = SimpleNamespace(
        decision_snapshot_id="forecast-snapshot-1",
        edge=SimpleNamespace(
            ci_lower=0.60,
            p_posterior=0.70,
            p_market=0.30,
            bin=SimpleNamespace(label="70-71F"),
            direction="buy_no",
        ),
        applied_validations=[],
    )
    yes_sibling = SimpleNamespace(
        decision_snapshot_id="forecast-snapshot-1",
        edge=SimpleNamespace(
            ci_lower=0.65,
            p_posterior=0.70,
            p_market=0.70,
            bin=SimpleNamespace(label="72-73F"),
            direction="buy_yes",
        ),
        applied_validations=[],
    )

    cycle_runtime._mark_edli_live_inference_family_applied(
        [no_selected, yes_sibling],
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "causal_snapshot_id": "forecast-snapshot-1",
            "payload": {
                "completeness_status": "COMPLETE",
                "snapshot_id": "forecast-snapshot-1",
                "source_run_id": "source-run-1",
                "snapshot_hash": "hash-1",
            },
        },
    )

    assert no_selected.edli_live_inference_proof["p_live_yes"] == pytest.approx(0.30)
    assert no_selected.edli_live_inference_proof["p_live_selected"] == pytest.approx(0.70)
    assert no_selected.edge.p_posterior == pytest.approx(0.70)


def test_edli_day0_p_live_applies_before_full_family_fdr_scan():
    bins = [
        Bin(low=68, high=69, unit="F", label="68-69F"),
        Bin(low=70, high=71, unit="F", label="70-71F"),
    ]
    analysis = MarketAnalysis(
        p_raw=np.array([0.50, 0.50]),
        p_cal=np.array([0.50, 0.50]),
        p_market=np.array([0.20, 0.20]),
        alpha=0.0,
        bins=bins,
        member_maxes=np.array([70.0, 71.0]),
        unit="F",
    )
    candidate = SimpleNamespace(
        edli_event_context={
            "event_id": "event-1",
            "event_type": "DAY0_EXTREME_UPDATED",
            "causal_snapshot_id": "obs-1",
            "payload": {"metric": "high", "rounded_value": 70},
        }
    )

    proof = evaluator._apply_edli_live_family_before_selection(
        candidate=candidate,
        analysis=analysis,
        decision_snapshot_id="obs-1",
    )
    hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=8)

    assert proof["applied_before_fdr"] is True
    assert proof["family"]["68-69F"] == pytest.approx(0.0)
    assert proof["family"]["70-71F"] == pytest.approx(1.0)
    selected = [h for h in hypotheses if h.range_label == "70-71F" and h.direction == "buy_yes"]
    killed = [h for h in hypotheses if h.range_label == "68-69F" and h.direction == "buy_yes"]
    assert selected[0].p_posterior == pytest.approx(1.0)
    assert selected[0].p_value == 0.0
    assert killed[0].p_posterior == pytest.approx(0.0)
    assert killed[0].passed_prefilter is False


def test_cycle_runtime_filters_forecast_decisions_to_causal_snapshot_id():
    summary = {}
    decisions = [
        SimpleNamespace(decision_snapshot_id="snapshot-target"),
        SimpleNamespace(decision_snapshot_id="snapshot-other"),
    ]

    filtered = cycle_runtime._filter_decisions_for_edli_event(
        decisions,
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "causal_snapshot_id": "snapshot-target",
        },
        summary,
    )

    assert filtered == [decisions[0]]
    assert summary["edli_forecast_decision_filter_before"] == 2
    assert summary["edli_forecast_decision_filter_after"] == 1


def test_cycle_runtime_blocks_forecast_decision_without_matching_causal_snapshot():
    summary = {}
    filtered = cycle_runtime._filter_decisions_for_edli_event(
        [SimpleNamespace(decision_snapshot_id="snapshot-other")],
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "causal_snapshot_id": "snapshot-target",
        },
        summary,
    )

    assert filtered == []
    assert summary["edli_submit_reason"] == "EDLI_FORECAST_CAUSAL_SNAPSHOT_MISMATCH"


def test_cycle_runtime_stamps_fdr_kelly_final_intent_proof_only_with_full_family():
    summary = {}
    context = {
        "event_id": "event-1",
        "causal_snapshot_id": "snapshot-1",
        "condition_id": "condition-1",
        "token_id": "token-yes",
    }
    candidate = SimpleNamespace(city=SimpleNamespace(name="Chicago"), target_date="2026-05-25", temperature_metric="high")
    decision = SimpleNamespace(
        decision_snapshot_id="decision-1",
        decision_id="decision-1",
        size_usd=2.5,
        fdr_family_size=4,
        fdr_fallback_fired=False,
        tokens={"condition_id": "condition-1"},
        edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"),
        kelly_execution_price=ExecutionPrice(0.5, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
        edli_kelly_cost_basis_id="cost-1",
    )
    final_intent = SimpleNamespace(
        hypothesis_id="family-1:hyp-1",
        selected_token_id="token-yes",
        snapshot_id="snapshot-1",
        cost_basis_id="cost-1",
    )

    cycle_runtime._stamp_edli_submit_summary(
        summary,
        context=context,
        candidate=candidate,
        decision=decision,
        snapshot_fields={"condition_id": "condition-1"},
        final_intent=final_intent,
        reprice_payload={},
        trade_score=0.01,
        trade_score_inputs={"p_fill_lcb": 0.4},
    )

    assert summary["edli_event_id"] == "event-1"
    assert summary["edli_trade_score_positive"] is True
    assert summary["edli_fdr_pass"] is False
    assert summary["edli_fdr_family_id"].startswith("hyp|")
    assert summary["edli_fdr_family_id"].endswith("|snap=decision-1")
    assert summary["edli_fdr_hypothesis_count"] == 0
    assert summary["edli_kelly_pass"] is True
    assert summary["edli_kelly_execution_price_type"] == "ExecutionPrice"
    assert summary["edli_kelly_price_fee_deducted"] is True
    assert summary["edli_kelly_size_usd"] == 2.5
    assert summary["edli_kelly_cost_basis_id"] == "cost-1"
    assert summary["edli_final_intent_id"] == "family-1:hyp-1"


def test_cycle_runtime_does_not_stamp_fdr_pass_without_full_family_denominator():
    summary = {}
    decision = SimpleNamespace(
        decision_snapshot_id="decision-1",
        size_usd=2.5,
        fdr_family_size=0,
        fdr_fallback_fired=False,
        tokens={},
        edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"),
        kelly_execution_price=ExecutionPrice(0.5, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
    )

    cycle_runtime._stamp_edli_submit_summary(
        summary,
        context={"event_id": "event-1", "causal_snapshot_id": "snapshot-1"},
        candidate=SimpleNamespace(city=SimpleNamespace(name="Chicago"), target_date="2026-05-25", temperature_metric="high"),
        decision=decision,
        snapshot_fields={},
        final_intent=SimpleNamespace(hypothesis_id="family-1:hyp-1", selected_token_id="", snapshot_id="", cost_basis_id=""),
        reprice_payload={},
        trade_score=0.01,
        trade_score_inputs={},
    )

    assert summary["edli_fdr_pass"] is False
    assert summary["edli_fdr_hypothesis_count"] == 0


def test_cycle_runtime_requires_durable_fdr_rows_when_world_tables_exist():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            created_at TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            decision_time_status TEXT
        );
        CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            decision_id TEXT,
            candidate_id TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            range_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            p_value REAL,
            q_value REAL,
            ci_lower REAL,
            ci_upper REAL,
            edge REAL,
            tested INTEGER NOT NULL DEFAULT 1,
            passed_prefilter INTEGER NOT NULL DEFAULT 0,
            selected_post_fdr INTEGER NOT NULL DEFAULT 0,
            rejection_stage TEXT,
            recorded_at TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    summary = {}
    candidate = SimpleNamespace(
        city=SimpleNamespace(name="Chicago"),
        target_date="2026-05-25",
        temperature_metric="high",
        discovery_mode="update_reaction",
    )
    decision = SimpleNamespace(
        decision_snapshot_id="decision-1",
        size_usd=2.5,
        fdr_family_size=4,
        fdr_fallback_fired=False,
        tokens={},
        edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"),
        kelly_execution_price=ExecutionPrice(0.5, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
    )
    family_id = cycle_runtime._edli_canonical_fdr_family_id(candidate, decision)

    cycle_runtime._stamp_edli_submit_summary(
        summary,
        context={"event_id": "event-1", "causal_snapshot_id": "snapshot-1"},
        conn=conn,
        candidate=candidate,
        decision=decision,
        snapshot_fields={},
        final_intent=SimpleNamespace(hypothesis_id="family-1:hyp-1", selected_token_id="", snapshot_id="", cost_basis_id=""),
        reprice_payload={},
        trade_score=0.01,
        trade_score_inputs={},
    )
    assert summary["edli_fdr_pass"] is False
    assert summary["edli_fdr_hypothesis_count"] == 0

    conn.execute(
        """
        INSERT INTO selection_family_fact (
            family_id, cycle_mode, decision_snapshot_id, city, target_date,
            strategy_key, discovery_mode, created_at, meta_json
        ) VALUES (?, 'update_reaction', 'decision-1', 'Chicago', '2026-05-25', '', 'update_reaction', '2026-05-24T00:00:00Z', '{"tested_hypotheses": 2, "selected_post_fdr": 1}')
        """,
        (family_id,),
    )
    conn.execute(
        """
        INSERT INTO selection_hypothesis_fact (
            hypothesis_id, family_id, decision_id, candidate_id, city, target_date,
            range_label, direction, tested, selected_post_fdr, recorded_at
        ) VALUES ('hyp-1', ?, 'decision-1', 'candidate-1', 'Chicago', '2026-05-25', '75+', 'buy_yes', 1, 1, '2026-05-24T00:00:00Z')
        """,
        (family_id,),
    )
    conn.execute(
        """
        INSERT INTO selection_hypothesis_fact (
            hypothesis_id, family_id, decision_id, candidate_id, city, target_date,
            range_label, direction, tested, selected_post_fdr, recorded_at
        ) VALUES ('hyp-2', ?, 'decision-2', 'candidate-2', 'Chicago', '2026-05-25', '76+', 'buy_no', 1, 0, '2026-05-24T00:00:00Z')
        """,
        (family_id,),
    )
    summary = {}
    cycle_runtime._stamp_edli_submit_summary(
        summary,
        context={"event_id": "event-1", "causal_snapshot_id": "snapshot-1"},
        conn=conn,
        candidate=candidate,
        decision=decision,
        snapshot_fields={},
        final_intent=SimpleNamespace(hypothesis_id="family-1:hyp-1", selected_token_id="", snapshot_id="", cost_basis_id=""),
        reprice_payload={},
        trade_score=0.01,
        trade_score_inputs={},
    )
    assert summary["edli_fdr_pass"] is True
    assert summary["edli_fdr_family_id"] == family_id
    assert summary["edli_fdr_hypothesis_count"] == 2


def test_edli_fdr_rejects_when_hypothesis_rows_less_than_family_meta_tested_hypotheses():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY, cycle_mode TEXT NOT NULL, decision_snapshot_id TEXT,
            city TEXT, target_date TEXT, strategy_key TEXT, discovery_mode TEXT,
            created_at TEXT NOT NULL, meta_json TEXT NOT NULL
        );
        CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY, family_id TEXT NOT NULL, decision_id TEXT,
            candidate_id TEXT, city TEXT NOT NULL, target_date TEXT NOT NULL,
            range_label TEXT NOT NULL, direction TEXT NOT NULL, tested INTEGER NOT NULL DEFAULT 1,
            selected_post_fdr INTEGER NOT NULL DEFAULT 0, recorded_at TEXT NOT NULL
        );
        """
    )
    candidate = SimpleNamespace(city=SimpleNamespace(name="Chicago"), target_date="2026-05-25", temperature_metric="high", discovery_mode="update_reaction")
    decision = SimpleNamespace(decision_snapshot_id="decision-1", edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"))
    family_id = cycle_runtime._edli_canonical_fdr_family_id(candidate, decision)
    conn.execute(
        "INSERT INTO selection_family_fact VALUES (?, 'update_reaction', 'decision-1', 'Chicago', '2026-05-25', '', 'update_reaction', '2026-05-24T00:00:00Z', ?)",
        (family_id, '{"tested_hypotheses": 2, "selected_post_fdr": 1}'),
    )
    conn.execute(
        "INSERT INTO selection_hypothesis_fact VALUES ('hyp-1', ?, 'decision-1', 'candidate-1', 'Chicago', '2026-05-25', '75+', 'buy_yes', 1, 1, '2026-05-24T00:00:00Z')",
        (family_id,),
    )

    passed, count = cycle_runtime._edli_durable_fdr_proof(conn, family_id=family_id, decision=decision)

    assert passed is False
    assert count == 1


def test_edli_fdr_proof_requires_same_p_live_family_hash_when_present():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY, cycle_mode TEXT NOT NULL, decision_snapshot_id TEXT,
            city TEXT, target_date TEXT, strategy_key TEXT, discovery_mode TEXT,
            created_at TEXT NOT NULL, meta_json TEXT NOT NULL
        );
        CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY, family_id TEXT NOT NULL, decision_id TEXT,
            candidate_id TEXT, city TEXT NOT NULL, target_date TEXT NOT NULL,
            range_label TEXT NOT NULL, direction TEXT NOT NULL, tested INTEGER NOT NULL DEFAULT 1,
            passed_prefilter INTEGER NOT NULL DEFAULT 0, selected_post_fdr INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    candidate = SimpleNamespace(city=SimpleNamespace(name="Chicago"), target_date="2026-05-25", temperature_metric="high", discovery_mode="update_reaction")
    decision = SimpleNamespace(
        decision_snapshot_id="decision-1",
        edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"),
        edli_live_inference_proof={"family_hash": "hash-live"},
    )
    family_id = cycle_runtime._edli_canonical_fdr_family_id(candidate, decision)
    conn.execute(
        "INSERT INTO selection_family_fact VALUES (?, 'update_reaction', 'decision-1', 'Chicago', '2026-05-25', '', 'update_reaction', '2026-05-24T00:00:00Z', ?)",
        (family_id, '{"tested_hypotheses": 1, "selected_post_fdr": 1, "edli_live_inference": {"family_hash": "hash-other", "applied_before_fdr": true}}'),
    )
    conn.execute(
        "INSERT INTO selection_hypothesis_fact VALUES ('hyp-1', ?, 'decision-1', 'candidate-1', 'Chicago', '2026-05-25', '75+', 'buy_yes', 1, 1, 1, '2026-05-24T00:00:00Z', ?)",
        (family_id, '{"edli_live_inference_family_hash": "hash-other"}'),
    )

    passed, count = cycle_runtime._edli_durable_fdr_proof(conn, family_id=family_id, decision=decision)
    assert passed is False
    assert count == 0

    conn.execute(
        "UPDATE selection_family_fact SET meta_json = ? WHERE family_id = ?",
        ('{"tested_hypotheses": 1, "selected_post_fdr": 1, "edli_live_inference": {"family_hash": "hash-live", "applied_before_fdr": true}}', family_id),
    )
    conn.execute(
        "UPDATE selection_hypothesis_fact SET meta_json = ? WHERE hypothesis_id = 'hyp-1'",
        ('{"edli_live_inference_family_hash": "hash-live"}',),
    )

    passed, count = cycle_runtime._edli_durable_fdr_proof(conn, family_id=family_id, decision=decision)
    assert passed is True
    assert count == 1


def test_stamp_edli_submit_summary_does_not_copy_context_binding_fields_from_event_context():
    summary = {}
    decision = SimpleNamespace(
        decision_snapshot_id="decision-1",
        size_usd=2.5,
        fdr_family_size=4,
        fdr_fallback_fired=False,
        tokens={},
        edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"),
        kelly_execution_price=ExecutionPrice(0.5, price_type="fee_adjusted", fee_deducted=True, currency="probability_units"),
    )

    cycle_runtime._stamp_edli_submit_summary(
        summary,
        context={
            "event_id": "event-1",
            "causal_snapshot_id": "source-snapshot-1",
            "condition_id": "expected-condition",
            "token_id": "expected-token",
            "executable_snapshot_id": "expected-exec-snapshot",
        },
        candidate=SimpleNamespace(city=SimpleNamespace(name="Chicago"), target_date="2026-05-25", temperature_metric="high"),
        decision=decision,
        snapshot_fields={},
        final_intent=SimpleNamespace(hypothesis_id="family-1:hyp-1", selected_token_id="", snapshot_id="", cost_basis_id=""),
        reprice_payload={},
        trade_score=0.01,
        trade_score_inputs={},
    )

    assert summary["condition_id"] == ""
    assert summary["token_id"] == ""
    assert summary["executable_snapshot_id"] == ""
    assert summary["edli_kelly_pass"] is False


def test_edli_kelly_receipt_derived_from_actual_execution_price_object():
    summary = {}
    decision = SimpleNamespace(
        decision_snapshot_id="decision-1",
        size_usd=2.5,
        fdr_family_size=4,
        fdr_fallback_fired=False,
        tokens={},
        edge=SimpleNamespace(bin=SimpleNamespace(label="75+"), direction="buy_yes"),
        kelly_execution_price=0.5,
    )

    cycle_runtime._stamp_edli_submit_summary(
        summary,
        context={"event_id": "event-1", "causal_snapshot_id": "source-snapshot-1"},
        candidate=SimpleNamespace(city=SimpleNamespace(name="Chicago"), target_date="2026-05-25", temperature_metric="high"),
        decision=decision,
        snapshot_fields={"condition_id": "condition-1", "executable_snapshot_id": "exec-1"},
        final_intent=SimpleNamespace(hypothesis_id="family-1:hyp-1", selected_token_id="token-yes", snapshot_id="exec-1", cost_basis_id=""),
        reprice_payload={},
        trade_score=0.01,
        trade_score_inputs={},
    )

    assert summary["edli_kelly_pass"] is False
    assert summary["edli_kelly_execution_price_type"] == "float"
    assert summary["edli_kelly_price_fee_deducted"] is False


def test_edli_kelly_proof_requires_repriced_size_and_cost_basis_match():
    decision = SimpleNamespace(size_usd=2.5)
    final_intent = SimpleNamespace(
        fee_adjusted_execution_price=0.5,
        cost_basis_id="cost_basis:abc",
    )

    cycle_runtime._stamp_edli_kelly_execution_price(
        decision,
        final_intent,
        {
            "repriced_size_usd": 2.5,
            "corrected_pricing_shadow": {
                "cost_basis_id": "cost_basis:abc",
                "candidate_fee_adjusted_execution_price": "0.5",
            },
        },
    )

    assert decision.kelly_execution_price.__class__.__name__ == "ExecutionPrice"
    assert decision.edli_kelly_cost_basis_id == "cost_basis:abc"

    with pytest.raises(ValueError, match="EDLI_KELLY_SIZE_NOT_REPRICED_FROM_EXECUTABLE_COST"):
        cycle_runtime._stamp_edli_kelly_execution_price(
            SimpleNamespace(size_usd=2.0),
            final_intent,
            {
                "repriced_size_usd": 2.5,
                "corrected_pricing_shadow": {
                    "cost_basis_id": "cost_basis:abc",
                    "candidate_fee_adjusted_execution_price": "0.5",
                },
            },
        )


def test_executable_snapshot_gate_requires_fresh_snapshot():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            freshness_deadline TEXT,
            snapshot_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            active INTEGER,
            closed INTEGER,
            event_slug TEXT
        )
        """
    )
    gate = executable_snapshot_gate_from_trade_conn(
        conn,
        now=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )
    event = _forecast_event()
    assert gate(event) is False
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?)",
        ("2026-05-24T12:05:00+00:00", "other-snapshot", "yes-1", "no-1", 1, 0, "new-york-weather"),
    )
    assert gate(event) is False
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?)",
        ("2026-05-24T12:05:00+00:00", "exec-snapshot-1", "yes-1", "no-1", 1, 0, "chicago-weather"),
    )
    assert gate(event) is False


def test_executable_snapshot_gate_accepts_event_bound_condition_token_only():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            freshness_deadline TEXT,
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            active INTEGER,
            closed INTEGER,
            event_slug TEXT
        )
        """
    )
    gate = executable_snapshot_gate_from_trade_conn(
        conn,
        now=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )
    event = _forecast_event()
    payload = json.loads(event.payload_json)
    payload.update({"condition_id": "condition-1", "token_id": "yes-1"})
    bound = replace(event, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?,?)",
        ("2026-05-24T12:05:00+00:00", "exec-snapshot-1", "condition-1", "yes-1", "no-1", 1, 0, "chicago-weather"),
    )
    assert gate(bound) is True


def test_executable_snapshot_gate_accepts_exact_family_topology_binding():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            freshness_deadline TEXT,
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            active INTEGER,
            closed INTEGER,
            event_slug TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE market_events_v2 (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            outcome TEXT
        )
        """
    )
    gate = executable_snapshot_gate_from_trade_conn(
        conn,
        now=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc),
    )
    event = _forecast_event()
    conn.execute(
        "INSERT INTO market_events_v2 VALUES (?,?,?,?,?,?)",
        ("Chicago", "2026-05-25", "high", "condition-1", "yes-1", None),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?,?)",
        ("2026-05-24T12:05:00+00:00", "exec-snapshot-1", "condition-1", "yes-1", "no-1", 1, 0, "any-weather"),
    )
    assert gate(event) is True


def test_market_filter_rejects_expected_condition_when_market_condition_missing():
    summary = {}
    markets = [{"city": SimpleNamespace(name="Chicago"), "target_date": "2026-05-25", "temperature_metric": "high", "outcomes": []}]
    filtered = cycle_runtime._filter_markets_for_edli_event(
        markets,
        {
            "event_id": "event-1",
            "causal_snapshot_id": "snapshot-1",
            "city": "Chicago",
            "target_date": "2026-05-25",
            "metric": "high",
            "condition_id": "condition-1",
        },
        summary,
    )
    assert filtered == []
    assert summary["edli_submit_reason"] == "EDLI_EVENT_NO_MATCHING_MARKET"


def test_riskguard_adapter_blocks_non_green():
    event = _forecast_event()
    assert riskguard_allows_new_entries(get_current_level=lambda: RiskLevel.GREEN)(event) is True
    assert riskguard_allows_new_entries(get_current_level=lambda: RiskLevel.YELLOW)(event) is False
