# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: funnel autopsy 2026-06-10 — buy_no FDR p-value reconciled with the
#   certified fused NO posterior. Root cause: the live replacement_0_1 path
#   formerly hardcoded p_value[buy_no]=1.0, then later used {0,1} LCB pass/fail
#   flags as p-values. Both are misleading: the p-value must be the empirical
#   false-edge rate over the posterior bootstrap draws, while the robust LCB
#   pass/fail decision remains a separate prefilter.
"""Relationship test: certified fused NO posterior <-> empirical edge p-value.

This is a RELATIONSHIP test (Fitz methodology): it does not check a single function's
output, it checks the property that holds ACROSS the boundary where the certified
probability authority (the fused posterior's q_ucb -> native NO q_lcb) flows into the
selection layer (p_value). The invariant:

    a buy_no whose native robust NO lower bound (1 - q_ucb_yes) exceeds the native NO
    cost MUST pass the binary robust-LCB prefilter, while its p_value records the
    empirical share of bootstrap draws where q_no <= cost.

A buy_no with NO robust edge must fail the prefilter even if its empirical p_value is
graded rather than 1.0. The gate is reconciled, not removed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as adapter
from src.types.market import Bin


def _family() -> SimpleNamespace:
    # cond-22 is a forecast-DISTANT tail: high YES mass on the family center (cond-28)
    # means a buy_no on cond-22 has a high native NO lower bound (1 - q_ucb_yes_22).
    return SimpleNamespace(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
        candidates=(
            SimpleNamespace(
                condition_id="cond-22",
                yes_token_id="yes-22",
                no_token_id="no-22",
                bin=Bin(low=22.0, high=22.0, unit="C", label="22°C"),
            ),
            SimpleNamespace(
                condition_id="cond-28",
                yes_token_id="yes-28",
                no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28°C"),
            ),
        ),
    )


def _replacement_bundle() -> SimpleNamespace:
    # Native NO authority PRESENT: q_ucb map carried by the bundle. For cond-22 the YES
    # upper bound is small (0.12) so the native NO lower bound 1 - q_ucb_yes = 0.88 — a
    # strong favorite-longshot NO. For cond-28 (the center) the YES upper bound is large
    # (0.92) so the native NO lower bound is only 0.08.
    return SimpleNamespace(
        posterior_id=456,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        source_cycle_time="2026-06-09T00:00:00+00:00",
        computed_at="2026-06-09T00:05:00+00:00",
        q={"bin-22": 0.08, "bin-28": 0.80},
        q_lcb={"bin-22": 0.03, "bin-28": 0.70},
        q_ucb={"bin-22": 0.12, "bin-28": 0.92},
        provenance_json={
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "q_bootstrap_samples_by_bin": {
                # cond-22 NO side: 10/200 draws are <= 0.87, so p_value is
                # empirical 0.05 while the robust lower bound still clears cost.
                "bin-22": ([0.14] * 10) + ([0.10] * 190),
                # cond-28 NO side: 120/200 draws are <= 0.45, so p_value is
                # graded 0.60 while the robust lower bound fails cost.
                "bin-28": ([0.60] * 120) + ([0.40] * 80),
            },
            "bin_topology": [
                {"bin_id": "bin-22", "lower_c": 22.0, "upper_c": 22.0},
                {"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0},
            ],
        },
    )


def _passing_evidence():
    from src.data.replacement_forecast_runtime_policy import ReplacementForecastPromotionEvidence

    return ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=1.0,
        q_lcb_coverage=0.95,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def _capital_objective_evidence():
    from src.data.replacement_forecast_runtime_policy import (
        EXPECTED_CAPITAL_OBJECTIVE_LABEL,
        ReplacementForecastCapitalObjectiveEvidence,
    )

    return ReplacementForecastCapitalObjectiveEvidence(
        selected_label=EXPECTED_CAPITAL_OBJECTIVE_LABEL,
        replay_status="EMPIRICAL_WINNER",
        after_cost_pnl=1.0,
        source_availability_observed=True,
        source_availability_violations=0,
        anti_lookahead_violations=0,
        same_clob_replay_passed=True,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        product_specific_refit_passed=True,
    )


def _run(native_costs):
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    try:
        feature_flags = dict(settings._data.get("feature_flags", {}))
        feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
        mp.setitem(settings._data, "feature_flags", feature_flags)
        mp.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
        mp.setattr(adapter, "_replacement_live_input_lag_reason", lambda *a, **k: None)
        mp.setattr(
            reader,
            "read_replacement_forecast_bundle",
            lambda *a, **k: SimpleNamespace(
                ok=True, bundle=_replacement_bundle(), reason_code="READY"
            ),
        )
        return adapter._replacement_authority_probability_and_fdr_proof(
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={},
            family=_family(),
            conn=object(),
            native_costs=native_costs,
            decision_time=datetime(2026, 6, 9, tzinfo=timezone.utc),
            promotion_evidence=_passing_evidence(),
            capital_objective_evidence=_capital_objective_evidence(),
        )
    finally:
        mp.undo()


def test_buy_no_with_certified_edge_gets_empirical_edge_p_value() -> None:
    """cond-22 NO: native NO q_lcb 0.88 > NO cost -> prefilter admits; p is empirical."""
    native_costs = {
        ("cond-22", "buy_yes"): (None, ExecutionPrice(0.08, "ask", fee_deducted=True, currency="probability_units"), 0.08, None, None),
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.80, "ask", fee_deducted=True, currency="probability_units"), 0.80, None, None),
        # Favorite-longshot NO on the distant tail: cheap-ish NO cost well under the
        # certified native NO lower bound (0.88).
        ("cond-22", "buy_no"): (None, ExecutionPrice(0.87, "ask", fee_deducted=True, currency="probability_units"), 0.87, None, None),
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }
    _q, lcb, p_values, prefilter, _ev = _run(native_costs)

    # The certified native NO lower bound for the distant tail clears its cost.
    from src.calibration.qlcb_provenance import _qlcb_float

    assert _qlcb_float(lcb[("cond-22", "buy_no")]) == pytest.approx(0.88, abs=1e-9)
    # INVARIANT: p_value is the finite-sample-corrected empirical false-edge
    # rate, not a binary gate.
    assert p_values[("cond-22", "buy_no")] == pytest.approx(11 / 201)
    assert prefilter[("cond-22", "buy_no")] is True


def test_buy_no_without_edge_still_rejected_by_fdr() -> None:
    """cond-28 NO: native NO q_lcb 0.08 < NO cost 0.45 -> prefilter rejects.

    Reconciliation is one-directional: p_value may be graded from samples, but a NO
    with no certified robust edge is NOT admitted. The gate is preserved, not weakened.
    """
    native_costs = {
        ("cond-22", "buy_yes"): (None, ExecutionPrice(0.08, "ask", fee_deducted=True, currency="probability_units"), 0.08, None, None),
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.80, "ask", fee_deducted=True, currency="probability_units"), 0.80, None, None),
        ("cond-22", "buy_no"): (None, ExecutionPrice(0.72, "ask", fee_deducted=True, currency="probability_units"), 0.72, None, None),
        # Center-bin NO: native NO lower bound is only 0.08, far below this 0.45 cost.
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }
    _q, _lcb, p_values, prefilter, _ev = _run(native_costs)

    assert p_values[("cond-28", "buy_no")] == pytest.approx(121 / 201)
    assert prefilter[("cond-28", "buy_no")] is False


def test_buy_no_missing_native_cost_is_non_actionable() -> None:
    """No native NO price -> no edge can be certified -> p=1.0 (fail-closed, unchanged)."""
    native_costs = {
        ("cond-22", "buy_yes"): (None, ExecutionPrice(0.08, "ask", fee_deducted=True, currency="probability_units"), 0.08, None, None),
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.80, "ask", fee_deducted=True, currency="probability_units"), 0.80, None, None),
        # No buy_no entries at all -> native_costs.get returns the default (price None).
    }
    _q, _lcb, p_values, prefilter, _ev = _run(native_costs)

    assert p_values[("cond-22", "buy_no")] == 1.0
    assert prefilter[("cond-22", "buy_no")] is False
    assert p_values[("cond-28", "buy_no")] == 1.0
    assert prefilter[("cond-28", "buy_no")] is False
