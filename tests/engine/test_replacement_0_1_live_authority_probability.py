# Created: 2026-06-07
# Last reused/audited: 2026-07-10
# Authority basis: Operator 2026-06-07 live cutover directive: replacement 0.1
#   posterior is the live forecast authority; NO probabilities must not be
#   inferred from YES complements.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.calibration.qlcb_provenance import _qlcb_float
from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as adapter
from src.events.candidate_binding import weather_family_id
from src.solve.solver import (
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    joint_probability_witness_identity,
)
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
        source_cycle_time="2026-06-07T00:00:00+00:00",
        computed_at="2026-06-07T00:05:00+00:00",
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
    feature_flags["w3_solve_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)
    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(adapter, "_replacement_live_input_lag_reason", lambda *a, **k: None)
    bundle = _replacement_bundle()
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=bundle, reason_code="READY"),
    )
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE forecast_posteriors "
        "(posterior_id INTEGER PRIMARY KEY, posterior_identity_hash TEXT)"
    )
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?, ?)",
        (bundle.posterior_id, "fixture-posterior-identity"),
    )

    # REAUDIT_0_1.md §1.6 (item 3): this success-path test originally called the 0.1
    # builder with NO evidence, which encoded the flag-alone bug as expected behavior.
    # FIX-1 moved the success path BEHIND the shared evidence gate, so both passing
    # evidence objects are now supplied; absent/failing evidence is covered by
    # tests/engine/test_replacement_0_1_authority_evidence_gate.py (returns None).
    payload = {}
    native_costs = {
        ("cond-27", "buy_yes"): (None, ExecutionPrice(0.30, "ask", fee_deducted=True, currency="probability_units"), 0.30, None, None),
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
        ("cond-27", "buy_no"): (None, ExecutionPrice(0.70, "ask", fee_deducted=True, currency="probability_units"), 0.70, None, None),
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }
    q_by_condition, lcb_by_direction, p_values, prefilter, evidence = (
        adapter._replacement_authority_probability_and_fdr_proof(
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload=payload,
            family=_family(),
            conn=conn,
            native_costs=native_costs,
            decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
            promotion_evidence=_passing_evidence(),
            capital_objective_evidence=_capital_objective_evidence(),
        )
    )
    assert payload["_edli_spine_served_joint_q_samples_by_condition"] == {
        "cond-27": [0.20] * 200,
        "cond-28": [0.80] * 200,
    }
    assert "_edli_spine_joint_q_samples_unavailable_reason" not in payload

    assert evidence["probability_authority"] == "replacement_0_1"
    assert q_by_condition == {"cond-27": pytest.approx(0.20), "cond-28": pytest.approx(0.80)}
    assert _qlcb_float(lcb_by_direction[("cond-28", "buy_yes")]) > 0.55
    assert _qlcb_float(lcb_by_direction[("cond-27", "buy_no")]) == 0.0
    assert _qlcb_float(lcb_by_direction[("cond-28", "buy_no")]) == 0.0
    assert p_values[("cond-28", "buy_no")] == 1.0
    assert prefilter[("cond-28", "buy_no")] is False

    bundle.provenance_json["city_calibration_layer_applied"] = True
    bundle.provenance_json["city_calibration_rho"] = 0.25
    bundle.provenance_json["q_bootstrap_samples_basis"] = "global_simplex_v1"
    city_payload = {
        "_edli_spine_served_joint_q_samples_by_condition": {"stale": [1.0, 1.0]}
    }
    adapter._replacement_authority_probability_and_fdr_proof(
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        payload=city_payload,
        family=_family(),
        conn=conn,
        native_costs=native_costs,
        decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )
    assert "_edli_spine_served_joint_q_samples_by_condition" not in city_payload
    assert (
        city_payload["_edli_spine_joint_q_samples_unavailable_reason"]
        == "CITY_MIX_SAMPLE_AUTHORITY_SUPERSEDED"
    )

    bundle.provenance_json["q_bootstrap_samples_basis"] = (
        "served_rho_mixed_simplex_v2"
    )
    mixed_payload = {}
    adapter._replacement_authority_probability_and_fdr_proof(
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        payload=mixed_payload,
        family=_family(),
        conn=conn,
        native_costs=native_costs,
        decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )
    assert mixed_payload["_edli_spine_served_joint_q_samples_by_condition"] == {
        "cond-27": [0.20] * 200,
        "cond-28": [0.80] * 200,
    }
    assert "_edli_spine_joint_q_samples_unavailable_reason" not in mixed_payload
    conn.close()


def test_current_global_probability_authority_rebuilds_canonical_matrix_and_refutes_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE forecast_posteriors "
        "(posterior_id INTEGER PRIMARY KEY, posterior_identity_hash TEXT)"
    )
    conn.execute(
        "CREATE TABLE market_events ("
        "city TEXT, target_date TEXT, temperature_metric TEXT, condition_id TEXT, "
        "market_slug TEXT, range_label TEXT, range_low REAL, range_high REAL, "
        "outcome TEXT, token_id TEXT)"
    )
    conn.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "Testopolis",
                "2026-06-09",
                "high",
                "cond-27",
                "market-27",
                "27C",
                27.0,
                27.0,
                "bin-27",
                "yes-27",
            ),
            (
                "Testopolis",
                "2026-06-09",
                "high",
                "cond-28",
                "market-28",
                "28C",
                28.0,
                28.0,
                "bin-28",
                "yes-28",
            ),
        ),
    )
    posterior_identity = "canonical-posterior-current"
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?, ?)",
        (123, posterior_identity),
    )
    bundle = _replacement_bundle()
    bundle.provenance_json["q_bootstrap_samples_basis"] = "global_simplex_v1"
    bundle.provenance_json["q_bootstrap_samples_by_bin"] = {
        "bin-27": [0.20] * 400,
        "bin-28": [0.80] * 400,
    }
    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(
            ok=True,
            bundle=bundle,
            reason_code="READY",
        ),
    )
    event = SimpleNamespace(
        payload_json=json.dumps(
            {
                "city": "Testopolis",
                "target_date": "2026-06-09",
                "metric": "high",
                "unit": "C",
            }
        )
    )
    bindings = (
        OutcomeTokenBinding("internal-27", "cond-27", "yes-27", "no-27"),
        OutcomeTokenBinding("internal-28", "cond-28", "yes-28", "no-28"),
    )
    samples = np.column_stack(
        (np.full(400, 0.20), np.full(400, 0.80))
    )
    decision_time = datetime(2026, 6, 7, tzinfo=timezone.utc)
    family_key = weather_family_id(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
    )
    witness_identity = joint_probability_witness_identity(
        family_key=family_key,
        bindings=bindings,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash=posterior_identity,
        source_truth_identity="source-current",
        authority_certificate_hash="certificate-current",
        band_alpha=0.05,
        band_basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        yes_q_samples=samples,
        captured_at_utc=decision_time,
    )
    witness = JointOutcomeProbabilityWitness(
        family_key=family_key,
        bindings=bindings,
        yes_q_samples=samples,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash=posterior_identity,
        source_truth_identity="source-current",
        authority_certificate_hash="certificate-current",
        band_alpha=0.05,
        band_basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        captured_at_utc=decision_time,
        max_age=timedelta(seconds=30),
        witness_identity=witness_identity,
    )

    current = adapter.current_global_probability_authority(
        conn,
        event,
        witness,
        decision_time=decision_time,
    )
    assert current is not None
    assert current.posterior_identity_hash == posterior_identity

    conn.execute(
        "UPDATE forecast_posteriors SET posterior_identity_hash = ? WHERE posterior_id = ?",
        ("posterior-superseded", 123),
    )
    assert adapter.current_global_probability_authority(
        conn,
        event,
        witness,
        decision_time=decision_time,
    ) is None
    conn.close()


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
    replacement_probability_authority_proof = SimpleNamespace(
        q_source=None,
        probability_authority="replacement_0_1",
    )
    baseline_proof = SimpleNamespace(q_source="emos")
    qkernel_selected_proof = SimpleNamespace(
        q_source="emos",
        selection_authority_applied="qkernel_spine",
    )

    assert adapter._replacement_primary_authority_already_applied(replacement_proof) is True
    assert (
        adapter._replacement_primary_authority_already_applied(
            replacement_probability_authority_proof
        )
        is True
    )
    assert adapter._replacement_primary_authority_already_applied(baseline_proof) is False
    assert adapter._replacement_primary_authority_already_applied(qkernel_selected_proof) is True
