# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Authority basis: Operator 2026-06-07 live cutover directive: replacement 0.1
#   posterior is the live forecast authority; NO probabilities must not be
#   inferred from YES complements.

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.calibration.qlcb_provenance import _qlcb_float
from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as adapter
from src.types.market import Bin


def _family() -> SimpleNamespace:
    return SimpleNamespace(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
        candidates=(
            SimpleNamespace(
                condition_id="cond-27",
                yes_token_id="yes-27",
                no_token_id="no-27",
                bin=Bin(low=27.0, high=27.0, unit="C", label="27°C"),
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
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={
            "bin-27": 0.20,
            "bin-28": 0.80,
        },
        q_lcb={
            "bin-27": 0.10,
            "bin-28": 0.70,
        },
        q_ucb={
            "bin-27": 1.0,
            "bin-28": 1.0,
        },
        provenance_json={
            # FIX 1 (2026-06-09): the live q-mode gate runs before this proof's logic and admits
            # only the fused-Normal modes. This fixture exercises the downstream YES-posterior /
            # native-NO direction relationship, so it carries a live-eligible mode to reach it.
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "q_bootstrap_samples_by_bin": {
                "bin-27": [0.20] * 200,
                "bin-28": [0.80] * 200,
            },
            "bin_topology": [
                {"bin_id": "bin-27", "lower_c": 27.0, "upper_c": 27.0},
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


def test_replacement_0_1_authority_uses_yes_posterior_and_blocks_no_without_native_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    feature_flags = dict(settings._data.get("feature_flags", {}))
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)
    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=_replacement_bundle(), reason_code="READY"),
    )

    # REAUDIT_0_1.md §1.6 (item 3): this success-path test originally called the 0.1
    # builder with NO evidence, which encoded the flag-alone bug as expected behavior.
    # FIX-1 moved the success path BEHIND the shared evidence gate, so both passing
    # evidence objects are now supplied; absent/failing evidence is covered by
    # tests/engine/test_replacement_0_1_authority_evidence_gate.py (returns None).
    q_by_condition, lcb_by_direction, p_values, prefilter, evidence = (
        adapter._replacement_authority_probability_and_fdr_proof(
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={},
            family=_family(),
            conn=object(),
            native_costs={
                ("cond-27", "buy_yes"): (None, ExecutionPrice(0.30, "ask", fee_deducted=True, currency="probability_units"), 0.30, None, None),
                ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
                ("cond-27", "buy_no"): (None, ExecutionPrice(0.70, "ask", fee_deducted=True, currency="probability_units"), 0.70, None, None),
                ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
            },
            decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
            promotion_evidence=_passing_evidence(),
            capital_objective_evidence=_capital_objective_evidence(),
        )
    )

    assert evidence["probability_authority"] == "replacement_0_1"
    assert q_by_condition == {"cond-27": pytest.approx(0.20), "cond-28": pytest.approx(0.80)}
    assert _qlcb_float(lcb_by_direction[("cond-28", "buy_yes")]) > 0.55
    assert _qlcb_float(lcb_by_direction[("cond-27", "buy_no")]) == 0.0
    assert _qlcb_float(lcb_by_direction[("cond-28", "buy_no")]) == 0.0
    assert p_values[("cond-28", "buy_no")] == 1.0
    assert prefilter[("cond-28", "buy_no")] is False


def test_replacement_yes_lcb_ignores_aifs_provenance_fallback() -> None:
    bundle = SimpleNamespace(
        q_lcb=None,
        provenance_json={
            "aifs_member_count": 51,
            "aifs_probabilities": {"bin-28": 1.0},
        },
    )

    assert adapter._replacement_yes_lcb_for_bin(
        bundle,
        bin_id="bin-28",
        q_yes=0.80,
        settlement_floor_lcb=None,
    ) == 0.0


def test_replacement_intermediate_cycles_keep_live_horizon_profile() -> None:
    assert adapter._posterior_horizon_profile("2026-06-18T00:00:00+00:00") == "full"
    assert adapter._posterior_horizon_profile("2026-06-18T06:00:00+00:00") == "full"
    assert adapter._posterior_horizon_profile("2026-06-18T12:00:00+00:00") == "full"
    assert adapter._posterior_horizon_profile("2026-06-18T18:00:00+00:00") == "full"


def test_replacement_reactor_hook_success_status_is_plain_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data.replacement_forecast_runtime_policy import ReplacementForecastRuntimePolicy
    from src.data.replacement_forecast_switch_decision import ReplacementForecastSwitchDecision
    from src.engine import replacement_forecast_reactor_hook as hook

    monkeypatch.setattr(
        hook,
        "build_replacement_forecast_receipt_provenance",
        lambda **_kwargs: SimpleNamespace(as_dict=lambda: {"runtime_layer": "live"}),
    )
    policy = ReplacementForecastRuntimePolicy(
        status="live",
        reason_codes=("REPLACEMENT_LIVE_ENABLED",),
        live_enabled=True,
        kelly_increase_enabled=False,
        direction_flip_enabled=False,
    )
    switch = ReplacementForecastSwitchDecision(
        status="live",
        reason_codes=("REPLACEMENT_SWITCH_LIVE_ADMITTED",),
        can_read_live_posterior=True,
        can_apply_reactor_hook=True,
        can_initiate_trade=True,
        can_increase_kelly=False,
        can_flip_direction=False,
        readiness_id="ready-1",
    )

    result = hook.apply_replacement_forecast_reactor_hook(
        policy=policy,
        switch_decision=switch,
        candidate={
            "baseline_direction": "buy_yes:bin-28",
            "baseline_q_posterior": 0.75,
            "baseline_q_lcb": 0.65,
            "baseline_kelly_fraction": 0.01,
            "candidate_direction": "buy_yes:bin-28",
            "candidate_q_posterior": 0.80,
            "candidate_q_lcb": 0.70,
            "candidate_kelly_fraction": 0.01,
            "market_snapshot_id": "snap-1",
            "condition_id": "cond-28",
            "token_id": "yes-28",
            "decision_time": "2026-06-18T06:05:00+00:00",
        },
        replacement_bundle=SimpleNamespace(
            posterior_id=123,
            product_id="openmeteo_ecmwf_ifs9_bayes_fusion_v1",
            q={"bin-28": 0.80},
        ),
        readiness=object(),
    )

    assert result.status == "live"
    assert result.as_receipt_tag() == {"runtime_layer": "live"}


def test_replacement_0_1_primary_authority_skips_legacy_replacement_hook() -> None:
    replacement_proof = SimpleNamespace(q_source="replacement_0_1")
    baseline_proof = SimpleNamespace(q_source="emos")
    qkernel_selected_proof = SimpleNamespace(
        q_source="emos",
        selection_authority_applied="qkernel_spine",
    )

    assert adapter._replacement_primary_authority_already_applied(replacement_proof) is True
    assert adapter._replacement_primary_authority_already_applied(baseline_proof) is False
    assert adapter._replacement_primary_authority_already_applied(qkernel_selected_proof) is True
