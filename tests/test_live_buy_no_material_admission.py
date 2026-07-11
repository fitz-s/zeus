# Lifecycle: created=2026-06-07; last_reviewed=2026-07-10; last_reused=2026-07-10
# Purpose: Prove material-bin BUY_NO admission uses native side uncertainty without weakening live gates.
# Reuse: Re-audit replacement bound identity, receipt plumbing, and legacy-source behavior before relying on it.
# Authority basis: PR_SPEC.md §2 FIX-4 (close the buy_no escape hatch; allow-list ⊆ carrier
#   vocab) plus operator directive 2026-06-17: near-settled entry prices are not
#   exploitable Day0 opportunities and must not enter live entry evaluation.
"""Live admission antibodies for material-YES buy_no and near-settled prices.

The escape hatch: a material-YES-bin buy_no was ADMITTED without an allowed native
NO LCB source whenever ``conservative_edge > confidence_gap`` — a self-referential
test on the SAME un-provenanced q_lcb. FIX-4 deletes that waiver: material-YES buy_no
requires an allowed native NO source unconditionally. The allow-list must also be a
subset of the q_lcb carrier vocabulary (CALIBRATION_SOURCES); YES_UCB_DERIVED is
removed because it is not a CalibrationSource.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.calibration.qlcb_provenance import CALIBRATION_SOURCES
from src.decision_kernel.canonicalization import stable_hash
from src.engine.event_reactor_adapter import (
    _build_replacement_no_bound_certificate,
    _replacement_no_bound_authority,
)
from src.strategy.live_inference.live_admission import (
    LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES,
    live_buy_no_conservative_evidence_rejection_reason,
    live_near_settled_entry_price_rejection_reason,
    replacement_no_bound_expected_from_parents,
    replacement_probability_bundle_hash,
)
from src.types.market import Bin


_REPLACEMENT_Q = {"bin-24c": 0.35, "other": 0.65}
_REPLACEMENT_Q_LCB = {"bin-24c": 0.20, "other": 0.50}
_REPLACEMENT_Q_UCB = {"bin-24c": 0.38, "other": 0.80}
_REPLACEMENT_TOPOLOGY = [
    {"bin_id": "bin-24c", "lower_c": 24.0, "upper_c": 24.0}
]
_REPLACEMENT_TOPOLOGY_HASH = stable_hash(_REPLACEMENT_TOPOLOGY)
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


def test_near_settled_entry_price_rejects_999() -> None:
    reason = live_near_settled_entry_price_rejection_reason(execution_price=0.999)

    assert reason is not None
    assert reason.startswith("ADMISSION_NEAR_SETTLED_PRICE:")
    assert "price=0.999000" in reason


def test_near_settled_entry_price_boundary_is_rejected() -> None:
    reason = live_near_settled_entry_price_rejection_reason(execution_price=0.99)

    assert reason is not None
    assert reason.startswith("ADMISSION_NEAR_SETTLED_PRICE:")


def test_entry_price_below_near_settled_ceiling_stays_admissible_to_other_gates() -> None:
    assert live_near_settled_entry_price_rejection_reason(execution_price=0.989) is None


def test_missing_entry_price_is_not_near_settled() -> None:
    assert live_near_settled_entry_price_rejection_reason(execution_price=None) is None
