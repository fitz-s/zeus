# Lifecycle: created=2026-06-07; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: Prove material-bin BUY_NO admission uses native side uncertainty without weakening live gates.
# Reuse: Re-audit replacement bound identity, receipt plumbing, and legacy-source behavior before relying on it.
# Authority basis: PR_SPEC.md §2 FIX-4 (close the buy_no escape hatch; allow-list ⊆ carrier
#   vocab).
"""Live admission antibodies for material-YES buy_no.

The escape hatch: a material-YES-bin buy_no was ADMITTED without an allowed native
NO LCB source whenever ``conservative_edge > confidence_gap`` — a self-referential
test on the SAME un-provenanced q_lcb. FIX-4 deletes that waiver: material-YES buy_no
requires an allowed native NO source unconditionally. The allow-list must also be a
subset of the q_lcb carrier vocabulary (CALIBRATION_SOURCES); YES_UCB_DERIVED is
removed because it is not a CalibrationSource.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.calibration.qlcb_provenance import CALIBRATION_SOURCES
from src.decision_kernel.canonicalization import (
    qkernel_current_state_identity_hash,
    stable_hash,
)
from src.engine.event_reactor_adapter import (
    _build_replacement_no_bound_certificate,
    _replacement_no_bound_authority,
)
from src.strategy.live_inference.live_admission import (
    LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES,
    live_buy_no_conservative_evidence_rejection_reason,
    replacement_no_bound_certificate_mismatch_reason,
    replacement_no_bound_expected_from_parents,
    replacement_probability_bundle_hash,
)
from src.events.reactor import (
    EventSubmissionReceipt,
    ReactorConfig,
    _receipt_money_path_blocker,
)
from src.types.market import Bin


_REPLACEMENT_Q = {"bin-24c": 0.35, "other": 0.65}
_REPLACEMENT_Q_LCB = {"bin-24c": 0.20, "other": 0.50}
_REPLACEMENT_Q_UCB = {"bin-24c": 0.38, "other": 0.80}
_REPLACEMENT_TOPOLOGY = [
    {"bin_id": "bin-24c", "lower_c": 24.0, "upper_c": 24.0}
]
_REPLACEMENT_TOPOLOGY_HASH = stable_hash(_REPLACEMENT_TOPOLOGY)


def _sealed_global_current_buy_no_economics() -> dict[str, object]:
    economics: dict[str, object] = {
        "source": "qkernel_spine",
        "decision_id": "decision-current",
        "receipt_hash": "receipt-current",
        "q_version": "q-current",
        "sample_hash": "samples-current",
        "side": "NO",
        "payoff_q_point": 0.65,
        "payoff_q_lcb": 0.61,
        "cost": 0.32,
        "edge_lcb": 0.29,
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "samples-current",
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "samples-current",
        "selection_guard_n": 64,
        "global_actuation_identity": "actuation-current",
        "global_economic_identity": "economic-current",
        "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
        "global_candidate_id": "candidate-current",
        "global_condition_id": "condition-current",
        "global_token_id": "token-no-current",
        "global_family_key": "family-current",
        "global_probability_witness_identity": "probability-current",
        "global_probability_authority": "global_current_probability_witness",
        "global_posterior_id": None,
        "global_bin_id": "bin-current",
        "global_universe_witness_identity": "universe-current",
        "global_wealth_witness_identity": "wealth-current",
        "global_wealth_economic_identity": "wealth-economic-current",
        "global_selection_epoch_identity": "epoch-current",
        "global_selection_cut_at": "2026-07-20T02:00:00+00:00",
        "global_selection_decision_at": "2026-07-20T02:00:01+00:00",
        "global_jit_book_hash": "book-current",
        "global_jit_venue_book_hash": "venue-book-current",
        "global_jit_book_snapshot_id": "snapshot-current",
        "global_jit_execution_curve_identity": "curve-current",
        "global_target_shares": "10",
        "global_expected_cost_usd": "3.2",
        "global_max_spend_usd": "3.2",
        "global_robust_delta_log_wealth": 0.01,
        "global_robust_ev_usd": 2.9,
        "global_cut_time_win_probability_lcb": 0.61,
        "global_cut_time_loss_probability_ucb": 0.39,
        "global_terminal_win_probability_lcb": 0.61,
        "global_terminal_loss_probability_ucb": 0.39,
        "global_terminal_loss_payoff_usd": "-3.2",
        "global_terminal_win_payoff_usd": "6.8",
        "global_terminal_median_payoff_usd": "6.8",
        "global_terminal_wealth_after_loss_usd": "96.8",
        "global_terminal_wealth_after_win_usd": "106.8",
        "global_cut_time_expected_value_diagnostic_usd": 2.9,
        "global_expected_value_diagnostic_usd": 2.9,
        "global_expected_value_semantics": (
            "DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN"
        ),
        "global_terminal_payoff_semantics": "BINARY_0_1",
    }
    economics["current_state_identity_hash"] = (
        qkernel_current_state_identity_hash(economics)
    )
    return economics


def _global_current_parent_kwargs() -> dict[str, object]:
    return {
        "global_actuation_identity": "actuation-current",
        "global_economic_identity": "economic-current",
        "global_probability_witness_identity": "probability-current",
        "global_universe_witness_identity": "universe-current",
        "global_wealth_witness_identity": "wealth-current",
        "global_wealth_economic_identity": "wealth-economic-current",
        "global_selection_epoch_identity": "epoch-current",
        "global_selection_cut_at": "2026-07-20T02:00:00+00:00",
        "global_selection_decision_at": "2026-07-20T02:00:01+00:00",
    }


def _global_current_actuation() -> SimpleNamespace:
    return SimpleNamespace(
        actuation_identity="actuation-current",
        economic_identity="economic-current",
        universe_witness_identity="universe-current",
        wealth_witness_identity="wealth-current",
        wealth_economic_identity="wealth-economic-current",
        selection_epoch_identity="epoch-current",
        selection_cut_at_utc="2026-07-20T02:00:00+00:00",
        decision_at_utc="2026-07-20T02:00:01+00:00",
        decision=SimpleNamespace(
            candidate=SimpleNamespace(
                probability_witness_identity="probability-current",
            )
        ),
    )
_REPLACEMENT_CANONICAL_BOUND_HASH = replacement_probability_bundle_hash(
    posterior_id=271828,
    posterior_identity_hash="1" * 64,
    family_id="Wellington|2026-07-12|high",
    bin_topology_hash=_REPLACEMENT_TOPOLOGY_HASH,
    q_mode="FUSED_NORMAL_FULL",
    q_lcb_basis="fused_center_bootstrap_p05",
    q_ucb_role="fused_center_bootstrap_ucb",
    bootstrap_draws=200,
    joint_samples_hash="3" * 64,
    q=_REPLACEMENT_Q,
    q_lcb=_REPLACEMENT_Q_LCB,
    q_ucb=_REPLACEMENT_Q_UCB,
)
_REPLACEMENT_NO_CERT_BODY = {
    "schema": "replacement_native_no_bound_v1",
    "probability_authority": "replacement_0_1",
    "posterior_id": 271828,
    "posterior_identity_hash": "1" * 64,
    "family_id": "Wellington|2026-07-12|high",
    "bin_topology_hash": _REPLACEMENT_TOPOLOGY_HASH,
    "condition_id": "cond-wellington-high-24c",
    "bin_id": "bin-24c",
    "q_mode": "FUSED_NORMAL_FULL",
    "q_lcb_basis": "fused_center_bootstrap_p05",
    "q_ucb_role": "fused_center_bootstrap_ucb",
    "bootstrap_draws": 200,
    "joint_samples_hash": "3" * 64,
    "canonical_bound_hash": _REPLACEMENT_CANONICAL_BOUND_HASH,
    "side": "buy_no",
    "yes_q": 0.35,
    "yes_q_ucb": 0.38,
    "side_q_point": 0.65,
    "side_q_lcb_raw": 0.62,
    "side_q_lcb_served": 0.62,
    "coverage_shrink_applied": False,
}
_REPLACEMENT_NO_CERT = {
    **_REPLACEMENT_NO_CERT_BODY,
    "certificate_hash": stable_hash(_REPLACEMENT_NO_CERT_BODY),
}
_REPLACEMENT_NO_EXPECTED = replacement_no_bound_expected_from_parents(
    {
        "replacement_posterior_id": 271828,
        "posterior_identity_hash": "1" * 64,
        "replacement_family_id": "Wellington|2026-07-12|high",
        "replacement_bin_topology_hash": _REPLACEMENT_TOPOLOGY_HASH,
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "replacement_q_lcb_basis": "fused_center_bootstrap_p05",
        "replacement_q_ucb_role": "fused_center_bootstrap_ucb",
        "replacement_bootstrap_draws": 200,
        "replacement_joint_samples_hash": "3" * 64,
        "replacement_canonical_bound_hash": _REPLACEMENT_CANONICAL_BOUND_HASH,
        "replacement_q": _REPLACEMENT_Q,
        "replacement_q_lcb": _REPLACEMENT_Q_LCB,
        "replacement_q_ucb": _REPLACEMENT_Q_UCB,
    },
    {
        "condition_id": "cond-wellington-high-24c",
        "replacement_no_bound_bin_id": "bin-24c",
        "replacement_no_bound_served_lcb": 0.62,
    },
)


def test_replacement_no_bound_mismatch_names_exact_parent_field() -> None:
    assert _REPLACEMENT_NO_EXPECTED is not None
    expected = {**_REPLACEMENT_NO_EXPECTED, "posterior_identity_hash": "9" * 64}

    reason = replacement_no_bound_certificate_mismatch_reason(
        _REPLACEMENT_NO_CERT,
        expected=expected,
        q_direction=0.65,
        q_lcb=0.62,
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics=None,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason == "parent_field:posterior_identity_hash"


def test_current_qkernel_bound_supersedes_legacy_served_lcb() -> None:
    economics = {
        "q_lcb_authority": "qkernel_payoff_bound",
        "probability_authority": "qkernel_payoff_direct_route",
        "pre_qkernel_q_posterior": 0.65,
        "pre_qkernel_q_lcb_5pct": 0.62,
        "payoff_q_point": 0.65,
        "payoff_q_lcb": 0.64,
    }

    reason = replacement_no_bound_certificate_mismatch_reason(
        _REPLACEMENT_NO_CERT,
        expected=_REPLACEMENT_NO_EXPECTED,
        q_direction=0.65,
        q_lcb=0.64,
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics=economics,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason is None
    assert replacement_no_bound_certificate_mismatch_reason(
        _REPLACEMENT_NO_CERT,
        expected=_REPLACEMENT_NO_EXPECTED,
        q_direction=0.65,
        q_lcb=0.64,
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics={**economics, "payoff_q_lcb": 0.63},
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    ) == "qkernel_payoff_lcb"


def test_material_yes_buy_no_without_allowed_source_is_rejected_even_with_positive_edge() -> None:
    """The deleted waiver would have admitted this (conservative_edge > confidence_gap);
    FIX-4 requires an allowed native NO source unconditionally, so it is rejected."""

    # conservative_edge = q_lcb - price = 0.90 - 0.10 = 0.80
    # confidence_gap   = q_direction - q_lcb = 0.92 - 0.90 = 0.02
    # 0.80 > 0.02 -> the old waiver returned None (ADMIT). FIX-4 must reject.
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.92,
        q_lcb=0.90,
        execution_price=0.10,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",  # NOT in the allow-list
        same_bin_yes_posterior=0.40,  # material YES mass (>= 0.20 floor)
    )

    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")


def test_replacement_source_or_coverage_strings_cannot_spoof_bound_authority() -> None:
    for source, status in (
        ("EMOS_ANALYTIC", "INSUFFICIENT_DATA"),
        ("FORECAST_BOOTSTRAP", "LICENSED"),
    ):
        reason = live_buy_no_conservative_evidence_rejection_reason(
            direction="buy_no",
            q_direction=0.65,
            q_lcb=0.62,
            execution_price=0.32,
            q_lcb_calibration_source=source,
            same_bin_yes_posterior=0.35,
            settlement_coverage_status=status,
            probability_authority="replacement_0_1",
            posterior_id=271828,
            condition_id="cond-wellington-high-24c",
        )
        assert reason is not None
        assert reason.startswith(
            "ADMISSION_BUY_NO_REPLACEMENT_BOUND_CERTIFICATE_MISSING:"
        )


def test_material_yes_buy_no_with_allowed_native_no_source_is_admitted() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.92,
        q_lcb=0.90,
        execution_price=0.10,
        q_lcb_calibration_source="EMOS_ANALYTIC",  # allowed native NO source
        same_bin_yes_posterior=0.40,
    )

    assert reason is None


def test_global_current_economics_alone_cannot_admit_material_buy_no() -> None:
    economics = _sealed_global_current_buy_no_economics()
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics=economics,
    )

    assert reason is not None


def test_exact_global_current_parents_admit_material_buy_no() -> None:
    economics = _sealed_global_current_buy_no_economics()

    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics=economics,
        probability_authority="global_current_probability_witness",
        condition_id="condition-current",
        token_id="token-no-current",
        family_id="family-current",
        candidate_id="candidate-current",
        **_global_current_parent_kwargs(),
    )

    assert reason is None


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("candidate_id", "candidate-sibling"),
        ("condition_id", "condition-sibling"),
        ("token_id", "token-no-sibling"),
        ("family_id", "family-sibling"),
        ("probability_authority", "replacement_0_1"),
        ("posterior_id", 271828),
    ),
)
def test_global_current_parent_transplant_is_rejected(field, value) -> None:
    kwargs = {
        "direction": "buy_no",
        "q_direction": 0.65,
        "q_lcb": 0.61,
        "execution_price": 0.32,
        "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
        "same_bin_yes_posterior": 0.35,
        "qkernel_execution_economics": _sealed_global_current_buy_no_economics(),
        "probability_authority": "global_current_probability_witness",
        "condition_id": "condition-current",
        "token_id": "token-no-current",
        "family_id": "family-current",
        "candidate_id": "candidate-current",
        **_global_current_parent_kwargs(),
    }
    kwargs[field] = value

    assert live_buy_no_conservative_evidence_rejection_reason(**kwargs) is not None


@pytest.mark.parametrize(
    ("source", "yes_posterior"),
    (
        ("EMOS_ANALYTIC", 0.35),
        ("FORECAST_BOOTSTRAP", 0.05),
    ),
)
def test_global_current_identity_mismatch_never_falls_back_to_legacy_admission(
    source, yes_posterior
) -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source=source,
        same_bin_yes_posterior=yes_posterior,
        settlement_coverage_status="LICENSED",
        qkernel_execution_economics=_sealed_global_current_buy_no_economics(),
        probability_authority="global_current_probability_witness",
        condition_id="condition-sibling",
        token_id="token-no-current",
        family_id="family-current",
        candidate_id="candidate-current",
    )

    assert reason == (
        "ADMISSION_BUY_NO_GLOBAL_CURRENT_STATE_INVALID:"
        "global_condition_id_mismatch"
    )


def test_global_current_parent_mutation_breaks_sealed_identity() -> None:
    economics = _sealed_global_current_buy_no_economics()
    economics["global_probability_witness_identity"] = "probability-mutated"

    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics=economics,
        probability_authority="global_current_probability_witness",
        condition_id="condition-current",
        token_id="token-no-current",
        family_id="family-current",
        candidate_id="candidate-current",
    )

    assert reason is not None


@pytest.mark.parametrize(
    "field",
    (
        "global_actuation_identity",
        "global_economic_identity",
        "global_probability_witness_identity",
        "global_universe_witness_identity",
        "global_wealth_witness_identity",
        "global_wealth_economic_identity",
        "global_selection_epoch_identity",
        "global_selection_cut_at",
        "global_selection_decision_at",
    ),
)
def test_resealed_global_parent_transplant_is_rejected(field: str) -> None:
    economics = _sealed_global_current_buy_no_economics()
    economics[field] = f"{field}-transplanted"
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )

    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        qkernel_execution_economics=economics,
        probability_authority="global_current_probability_witness",
        condition_id="condition-current",
        token_id="token-no-current",
        family_id="family-current",
        candidate_id="candidate-current",
        **_global_current_parent_kwargs(),
    )

    assert reason is not None
    assert reason.endswith(f"{field}_mismatch")


def test_partial_global_current_payload_cannot_fall_back_to_legacy_admission() -> None:
    economics = {
        key: value
        for key, value in _sealed_global_current_buy_no_economics().items()
        if not key.startswith("global_")
    }
    economics["global_economic_identity"] = "economic-partial"
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )

    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source="EMOS_ANALYTIC",
        same_bin_yes_posterior=0.05,
        settlement_coverage_status="LICENSED",
        qkernel_execution_economics=economics,
        probability_authority="global_current_probability_witness",
        condition_id="condition-current",
        token_id="token-no-current",
        family_id="family-current",
        candidate_id="candidate-current",
    )

    assert reason == (
        "ADMISSION_BUY_NO_GLOBAL_CURRENT_STATE_INVALID:global_actuation_identity"
    )


def test_receipt_gate_binds_global_current_certificate_to_exact_receipt() -> None:
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="event-current",
        family_id="family-current",
        candidate_id="candidate-current",
        condition_id="condition-current",
        token_id="token-no-current",
        direction="buy_no",
        q_live=0.65,
        q_lcb_5pct=0.61,
        c_fee_adjusted=0.32,
        trade_score=0.29,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        probability_authority="global_current_probability_witness",
        qkernel_execution_economics=_sealed_global_current_buy_no_economics(),
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-current",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=3.2,
        kelly_cost_basis_id="cost-current",
        final_intent_id="intent-current",
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
        global_actuation=_global_current_actuation(),
    )

    assert _receipt_money_path_blocker(receipt, ReactorConfig()) == (None, "")
    transplanted = replace(receipt, condition_id="condition-sibling")
    stage, reason = _receipt_money_path_blocker(transplanted, ReactorConfig())
    assert stage == "TRADE_SCORE"
    assert reason == (
        "ADMISSION_BUY_NO_GLOBAL_CURRENT_STATE_INVALID:"
        "global_condition_id_mismatch"
    )


def test_immaterial_yes_buy_no_is_not_gated_by_source() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.92,
        q_lcb=0.90,
        execution_price=0.10,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.05,  # below the 0.20 material floor
    )

    assert reason is None


def test_certified_replacement_native_no_bound_is_admitted_symmetrically() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.62,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        settlement_coverage_status="INSUFFICIENT_DATA",
        replacement_no_bound_certificate=_REPLACEMENT_NO_CERT,
        replacement_no_bound_expected=_REPLACEMENT_NO_EXPECTED,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason is None


def test_replacement_builder_binds_raw_complement_and_served_shrink() -> None:
    bundle = SimpleNamespace(
        posterior_id=271828,
        family_id="Wellington|2026-07-12|high",
        bin_topology_hash=_REPLACEMENT_TOPOLOGY_HASH,
        q_ucb=_REPLACEMENT_Q_UCB,
        q=_REPLACEMENT_Q,
        q_lcb=_REPLACEMENT_Q_LCB,
        provenance_json={
            "bin_topology": _REPLACEMENT_TOPOLOGY,
            "q_ucb_json_role": "fused_center_bootstrap_ucb",
            "q_bootstrap_samples_hash": "3" * 64,
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "q_lcb_bootstrap_draws": 200,
        },
    )
    credential = {
        "posterior_id": 271828,
        "q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": "fused_center_bootstrap_p05",
        "bootstrap_draws": 200,
    }
    authority = _replacement_no_bound_authority(
        bundle,
        posterior_identity_hash="1" * 64,
    )
    certificate = _build_replacement_no_bound_certificate(
        replacement_bundle=bundle,
        calibration_credential=credential,
        authority=authority,
        candidate=SimpleNamespace(bin=Bin(24.0, 24.0, "C", "24°C")),
        condition_id="cond-wellington-high-24c",
        yes_q=0.35,
        no_q=0.65,
        no_q_lcb=0.60,
    )
    assert certificate is not None
    assert certificate["side_q_lcb_raw"] == 0.62
    assert certificate["side_q_lcb_served"] == 0.60
    assert certificate["coverage_shrink_applied"] is True
    assert certificate["certificate_hash"] == stable_hash(
        {key: value for key, value in certificate.items() if key != "certificate_hash"}
    )
    expected = replacement_no_bound_expected_from_parents(
        {
            "replacement_posterior_id": certificate["posterior_id"],
            "posterior_identity_hash": certificate["posterior_identity_hash"],
            "replacement_family_id": certificate["family_id"],
            "replacement_bin_topology_hash": certificate["bin_topology_hash"],
            "replacement_q_mode": certificate["q_mode"],
            "replacement_q_lcb_basis": certificate["q_lcb_basis"],
            "replacement_q_ucb_role": certificate["q_ucb_role"],
            "replacement_bootstrap_draws": certificate["bootstrap_draws"],
            "replacement_joint_samples_hash": certificate["joint_samples_hash"],
            "replacement_canonical_bound_hash": certificate["canonical_bound_hash"],
            "replacement_q": bundle.q,
            "replacement_q_lcb": bundle.q_lcb,
            "replacement_q_ucb": bundle.q_ucb,
        },
        {
            "condition_id": certificate["condition_id"],
            "replacement_no_bound_bin_id": certificate["bin_id"],
            "replacement_no_bound_served_lcb": certificate[
                "side_q_lcb_served"
            ],
        },
    )
    assert live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.60,
        execution_price=0.32,
        q_lcb_calibration_source="anything-telemetry-only",
        same_bin_yes_posterior=0.35,
        settlement_coverage_status="INSUFFICIENT_DATA",
        replacement_no_bound_certificate=certificate,
        replacement_no_bound_expected=expected,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    ) is None


def test_replacement_authority_name_without_exact_bound_certificate_still_rejects() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.62,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        settlement_coverage_status="INSUFFICIENT_DATA",
        replacement_no_bound_certificate={
            "probability_authority": "replacement_0_1",
        },
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_REPLACEMENT_BOUND_CERTIFICATE_MISSING:")


def test_replacement_native_no_bound_certificate_fails_closed_on_every_identity_leg() -> None:
    mutations = (
        ("schema", "replacement_native_no_bound_v0"),
        ("probability_authority", "canonical"),
        ("posterior_id", 0),
        ("posterior_identity_hash", "0" * 64),
        ("family_id", ""),
        ("bin_topology_hash", "0" * 64),
        ("condition_id", ""),
        ("bin_id", ""),
        ("q_mode", "ANCHOR_ONLY"),
        ("q_lcb_basis", "wilson"),
        ("q_ucb_role", "soft_anchor_ucb"),
        ("bootstrap_draws", 1),
        ("joint_samples_hash", "0" * 64),
        ("side", "buy_yes"),
        ("yes_q", 0.36),
        ("yes_q_ucb", 0.39),
        ("side_q_point", 0.64),
        ("side_q_lcb_raw", 0.61),
        ("side_q_lcb_served", 0.61),
        ("coverage_shrink_applied", True),
        ("certificate_hash", "0" * 64),
    )
    for field, value in mutations:
        certificate = {**_REPLACEMENT_NO_CERT, field: value}
        reason = live_buy_no_conservative_evidence_rejection_reason(
            direction="buy_no",
            q_direction=0.65,
            q_lcb=0.62,
            execution_price=0.32,
            q_lcb_calibration_source="FORECAST_BOOTSTRAP",
            same_bin_yes_posterior=0.35,
            settlement_coverage_status="INSUFFICIENT_DATA",
            replacement_no_bound_certificate=certificate,
            replacement_no_bound_expected=_REPLACEMENT_NO_EXPECTED,
            probability_authority="replacement_0_1",
            posterior_id=271828,
            condition_id="cond-wellington-high-24c",
        )
        assert reason is not None, field

    outer_mutations = (
        ("probability_authority", "canonical"),
        ("posterior_id", 271829),
        ("condition_id", "other-condition"),
    )
    for field, value in outer_mutations:
        kwargs = {
            "direction": "buy_no",
            "q_direction": 0.65,
            "q_lcb": 0.62,
            "execution_price": 0.32,
            "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
            "same_bin_yes_posterior": 0.35,
            "settlement_coverage_status": "INSUFFICIENT_DATA",
            "replacement_no_bound_certificate": _REPLACEMENT_NO_CERT,
            "replacement_no_bound_expected": _REPLACEMENT_NO_EXPECTED,
            "probability_authority": "replacement_0_1",
            "posterior_id": 271828,
            "condition_id": "cond-wellington-high-24c",
        }
        kwargs[field] = value
        assert live_buy_no_conservative_evidence_rejection_reason(**kwargs) is not None


def test_coherently_rehashed_fake_identity_cannot_override_parent_authority() -> None:
    forged_body = {
        **_REPLACEMENT_NO_CERT_BODY,
        "posterior_identity_hash": "9" * 64,
        "bin_topology_hash": "8" * 64,
        "joint_samples_hash": "7" * 64,
    }
    forged = {**forged_body, "certificate_hash": stable_hash(forged_body)}
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.62,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        settlement_coverage_status="INSUFFICIENT_DATA",
        replacement_no_bound_certificate=forged,
        replacement_no_bound_expected=_REPLACEMENT_NO_EXPECTED,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )
    assert reason is not None


def test_coherently_rehashed_fake_selected_scalars_cannot_override_forecast_maps() -> None:
    forged_body = {
        **_REPLACEMENT_NO_CERT_BODY,
        "yes_q": 0.36,
        "yes_q_ucb": 0.39,
        "side_q_point": 0.64,
        "side_q_lcb_raw": 0.61,
        "side_q_lcb_served": 0.61,
    }
    forged = {**forged_body, "certificate_hash": stable_hash(forged_body)}
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.64,
        q_lcb=0.61,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.36,
        settlement_coverage_status="INSUFFICIENT_DATA",
        replacement_no_bound_certificate=forged,
        replacement_no_bound_expected=_REPLACEMENT_NO_EXPECTED,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason is not None


def test_forecast_parent_map_change_with_old_digest_fails_closed() -> None:
    expected = replacement_no_bound_expected_from_parents(
        {
            "replacement_posterior_id": 271828,
            "posterior_identity_hash": "1" * 64,
            "replacement_family_id": "Wellington|2026-07-12|high",
            "replacement_bin_topology_hash": _REPLACEMENT_TOPOLOGY_HASH,
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "replacement_q_lcb_basis": "fused_center_bootstrap_p05",
            "replacement_q_ucb_role": "fused_center_bootstrap_ucb",
            "replacement_bootstrap_draws": 200,
            "replacement_joint_samples_hash": "3" * 64,
            "replacement_canonical_bound_hash": _REPLACEMENT_CANONICAL_BOUND_HASH,
            "replacement_q": {**_REPLACEMENT_Q, "bin-24c": 0.36},
            "replacement_q_lcb": _REPLACEMENT_Q_LCB,
            "replacement_q_ucb": _REPLACEMENT_Q_UCB,
        },
        {
            "condition_id": "cond-wellington-high-24c",
            "replacement_no_bound_bin_id": "bin-24c",
            "replacement_no_bound_served_lcb": 0.62,
        },
    )

    assert expected is None


def test_self_signed_sibling_bin_cannot_replace_canonical_condition_bin() -> None:
    forged_body = {
        **_REPLACEMENT_NO_CERT_BODY,
        "bin_id": "other",
        "yes_q": 0.65,
        "yes_q_ucb": 0.80,
        "side_q_point": 0.35,
        "side_q_lcb_raw": 0.20,
        "side_q_lcb_served": 0.20,
    }
    forged = {**forged_body, "certificate_hash": stable_hash(forged_body)}

    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.35,
        q_lcb=0.20,
        execution_price=0.10,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.65,
        settlement_coverage_status="INSUFFICIENT_DATA",
        replacement_no_bound_certificate=forged,
        replacement_no_bound_expected=_REPLACEMENT_NO_EXPECTED,
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason is not None


def test_self_signed_unshrunk_bound_cannot_override_pre_qkernel_served_parent() -> None:
    expected = {
        **_REPLACEMENT_NO_EXPECTED,
        "side_q_lcb_served": 0.60,
    }
    reason = live_buy_no_conservative_evidence_rejection_reason(
        direction="buy_no",
        q_direction=0.65,
        q_lcb=0.60,
        execution_price=0.32,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.35,
        settlement_coverage_status="UNLICENSED",
        replacement_no_bound_certificate=_REPLACEMENT_NO_CERT,
        replacement_no_bound_expected=expected,
        qkernel_execution_economics={
            "q_lcb_authority": "qkernel_payoff_bound",
            "probability_authority": "qkernel_payoff_direct_route",
            "pre_qkernel_q_posterior": 0.65,
            "pre_qkernel_q_lcb_5pct": 0.60,
            "payoff_q_point": 0.65,
            "payoff_q_lcb": 0.60,
        },
        probability_authority="replacement_0_1",
        posterior_id=271828,
        condition_id="cond-wellington-high-24c",
    )

    assert reason is not None


def test_allow_list_is_subset_of_calibration_sources() -> None:
    """Invariant: every allowed buy_no LCB source must be a member of the q_lcb
    carrier vocabulary. A source the carrier cannot even express (e.g. the removed
    YES_UCB_DERIVED) can never be honestly provenanced through QlcbByDirection."""

    assert LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES <= CALIBRATION_SOURCES
    assert "YES_UCB_DERIVED" not in LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES
