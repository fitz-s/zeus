# Lifecycle: created=2026-06-11; last_reviewed=2026-07-10; last_reused=2026-07-10
# Authority basis: production defect (15:03Z+ burst, 2026-06-11): 12 of 21 families'
#   best candidates (ev/$ +0.04..+0.30; Atlanta q_lcb=0.7842 price=0.6021, HK
#   0.7613/0.6318, Warsaw, Moscow, Busan, Helsinki, Shanghai, Mexico City, Cape Town,
#   Karachi, London, San Francisco) killed ONLY by
#   ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING: yes_posterior=0.205..0.267
#   max=0.200 source=FORECAST_BOOTSTRAP — while the settlement-backward coverage
#   verdict for the SAME families said the settled record BACKED the claim.
#   The material-bin buy-NO waiver requires realized settlement evidence.
#   INSUFFICIENT_DATA remains a typed verdict for the certificate bridge, but it does not
#   waive the special buy-NO native-evidence requirement.
"""Relationship antibodies: settlement-coverage verdict licenses material-YES buy_no.

CROSS-MODULE INVARIANTS pinned here:
  1. The material buy-NO licensing set has ONE home
     (live_admission.SETTLEMENT_COVERAGE_LICENSING_STATUSES). The adapter cert credential
     deliberately uses a separate typed-verdict set because cert authority and buy-NO
     waiver are different semantics.
  2. LICENSED and UNLICENSED-after-shrink admit a material-YES buy_no whose q_lcb source is
     FORECAST_BOOTSTRAP. INSUFFICIENT_DATA / None / UNEVALUATED reject at this buy-NO
     waiver because they lack realized backing.
  3. CATEGORY-INVERSION KILL: before reconciliation a record-BACKED bootstrap
     q_lcb was rejected while a record-REFUTED one (re-branded SETTLEMENT_ISOTONIC
     by the shrink) was accepted. The verdict, not the brand, is the evidence.
  4. TWIN-SITE LOCKSTEP (21a4c14ee2 lesson): the proof-generation gate and the
     receipt-level gate (_receipt_money_path_blocker) produce the SAME verdict-aware
     outcome for the same inputs — the status travels on the receipt exactly as
     same_bin_yes_posterior does, omit-when-None for hash stability.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.decision_kernel.canonicalization import stable_hash
from src.events.no_submit_receipts import _receipt_json
from src.events.reactor import (
    EventSubmissionReceipt,
    ReactorConfig,
    _receipt_money_path_blocker,
)
from src.strategy.live_inference.live_admission import (
    SETTLEMENT_COVERAGE_LICENSING_STATUSES,
    live_buy_no_conservative_evidence_rejection_reason,
    replacement_no_bound_expected_from_parents,
    replacement_probability_bundle_hash,
)

# Real-shaped values from the 15:03Z burst (Atlanta high 2026-06-12 buy_no):
# q_lcb=0.7842, price=0.6021 (ev/$ = +0.302), yes_posterior=0.22 (material, >= 0.20),
# q_lcb source FORECAST_BOOTSTRAP (fused-bootstrap bounds, settled-record cohort n>=30).
_ATLANTA = dict(
    direction="buy_no",
    q_direction=0.80,
    q_lcb=0.7842,
    execution_price=0.6021,
    q_lcb_calibration_source="FORECAST_BOOTSTRAP",
    same_bin_yes_posterior=0.22,
)

_CERTIFIED_REPLACEMENT_Q = {"bin-atlanta-high": 0.22, "other": 0.78}
_CERTIFIED_REPLACEMENT_Q_LCB = {"bin-atlanta-high": 0.18, "other": 0.70}
_CERTIFIED_REPLACEMENT_Q_UCB = {"bin-atlanta-high": 0.25, "other": 0.82}
_CERTIFIED_REPLACEMENT_CANONICAL_BOUND_HASH = replacement_probability_bundle_hash(
    posterior_id=314159,
    posterior_identity_hash="4" * 64,
    family_id="Atlanta|2026-06-12|high",
    bin_topology_hash="5" * 64,
    q_mode="FUSED_NORMAL_FULL",
    q_lcb_basis="fused_center_bootstrap_p05",
    q_ucb_role="fused_center_bootstrap_ucb",
    bootstrap_draws=200,
    joint_samples_hash="6" * 64,
    q=_CERTIFIED_REPLACEMENT_Q,
    q_lcb=_CERTIFIED_REPLACEMENT_Q_LCB,
    q_ucb=_CERTIFIED_REPLACEMENT_Q_UCB,
)
_CERTIFIED_REPLACEMENT_CERT_BODY = {
    "schema": "replacement_native_no_bound_v1",
    "probability_authority": "replacement_0_1",
    "posterior_id": 314159,
    "posterior_identity_hash": "4" * 64,
    "family_id": "Atlanta|2026-06-12|high",
    "bin_topology_hash": "5" * 64,
    "condition_id": "cond-atlanta-high",
    "bin_id": "bin-atlanta-high",
    "q_mode": "FUSED_NORMAL_FULL",
    "q_lcb_basis": "fused_center_bootstrap_p05",
    "q_ucb_role": "fused_center_bootstrap_ucb",
    "bootstrap_draws": 200,
    "joint_samples_hash": "6" * 64,
    "canonical_bound_hash": _CERTIFIED_REPLACEMENT_CANONICAL_BOUND_HASH,
    "side": "buy_no",
    "yes_q": 0.22,
    "yes_q_ucb": 0.25,
    "side_q_point": 0.78,
    "side_q_lcb_raw": 0.75,
    "side_q_lcb_served": 0.75,
    "coverage_shrink_applied": False,
}
_CERTIFIED_REPLACEMENT_CERT = {
    **_CERTIFIED_REPLACEMENT_CERT_BODY,
    "certificate_hash": stable_hash(_CERTIFIED_REPLACEMENT_CERT_BODY),
}
_CERTIFIED_REPLACEMENT_FORECAST_PARENT = {
    "replacement_posterior_id": 314159,
    "posterior_identity_hash": "4" * 64,
    "replacement_family_id": "Atlanta|2026-06-12|high",
    "replacement_bin_topology_hash": "5" * 64,
    "replacement_q_mode": "FUSED_NORMAL_FULL",
    "replacement_q_lcb_basis": "fused_center_bootstrap_p05",
    "replacement_q_ucb_role": "fused_center_bootstrap_ucb",
    "replacement_bootstrap_draws": 200,
    "replacement_joint_samples_hash": "6" * 64,
    "replacement_canonical_bound_hash": _CERTIFIED_REPLACEMENT_CANONICAL_BOUND_HASH,
    "replacement_q": _CERTIFIED_REPLACEMENT_Q,
    "replacement_q_lcb": _CERTIFIED_REPLACEMENT_Q_LCB,
    "replacement_q_ucb": _CERTIFIED_REPLACEMENT_Q_UCB,
}
_CERTIFIED_REPLACEMENT_CANDIDATE_PARENT = {
    "condition_id": "cond-atlanta-high",
    "replacement_no_bound_bin_id": "bin-atlanta-high",
    "replacement_no_bound_served_lcb": 0.75,
}
_CERTIFIED_REPLACEMENT_EXPECTED = replacement_no_bound_expected_from_parents(
    _CERTIFIED_REPLACEMENT_FORECAST_PARENT,
    _CERTIFIED_REPLACEMENT_CANDIDATE_PARENT,
)
_CERTIFIED_REPLACEMENT = dict(
    direction="buy_no",
    q_direction=0.78,
    q_lcb=0.75,
    execution_price=0.6021,
    q_lcb_calibration_source="FORECAST_BOOTSTRAP",
    same_bin_yes_posterior=0.22,
    settlement_coverage_status="INSUFFICIENT_DATA",
    probability_authority="replacement_0_1",
    posterior_id=314159,
    condition_id="cond-atlanta-high",
    replacement_no_bound_certificate=_CERTIFIED_REPLACEMENT_CERT,
    replacement_no_bound_expected=_CERTIFIED_REPLACEMENT_EXPECTED,
)


def _replacement_proof_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        forecast_authority=SimpleNamespace(
            payload=_CERTIFIED_REPLACEMENT_FORECAST_PARENT
        ),
        candidate_evidence=SimpleNamespace(
            payload=_CERTIFIED_REPLACEMENT_CANDIDATE_PARENT
        ),
    )


# --- (a) record-BACKED verdict admits the bootstrap q_lcb --------------------------------------


def test_material_yes_bootstrap_with_licensed_verdict_is_admitted() -> None:
    """THE BURST, as a pin: material-YES buy_no + FORECAST_BOOTSTRAP + verdict
    LICENSED (settled record backs the claim) => ADMITTED."""
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="LICENSED"
    )
    assert reason is None, f"record-backed bootstrap q_lcb must be admitted: {reason}"


# --- (c) UNLICENSED admits (the shrunk q_lcb is what's being admitted) -------------------------


def test_material_yes_bootstrap_with_unlicensed_verdict_is_admitted() -> None:
    """UNLICENSED = the record refuted the RAW claim and the K3 shrink to
    realized-minus-1pp was the verdict's output — the (shrunk) q_lcb the decision
    actually carries is settled-record-backed. Same statuses the cert credential
    licenses in the buy-NO material-bin waiver; kills the category
    inversion where only the REFUTED-then-rebranded claim was admitted."""
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="UNLICENSED"
    )
    assert reason is None


# --- (b) INSUFFICIENT_DATA rejects for the material buy-NO waiver -----------------


def test_material_yes_bootstrap_with_insufficient_data_rejects() -> None:
    """Thin settlement history is not a material-bin buy-NO evidence waiver."""
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="INSUFFICIENT_DATA"
    )
    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")
    assert ":coverage_status=INSUFFICIENT_DATA" in reason


def test_exact_replacement_complement_bound_supersedes_the_no_only_waiver() -> None:
    """The one replacement authority already provides native NO uncertainty."""

    assert live_buy_no_conservative_evidence_rejection_reason(
        **_CERTIFIED_REPLACEMENT
    ) is None


def test_material_yes_bootstrap_with_no_verdict_rejected_with_missing_status() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status=None
    )
    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")
    assert ":coverage_status=missing" in reason


def test_unknown_status_never_licenses() -> None:
    """Fail-closed: only a REAL settled-record verdict admits. An unknown / future /
    corrupted / UNEVALUATED status string rejects with the status recorded — it is not
    a verdict the settled record produced."""
    assert SETTLEMENT_COVERAGE_LICENSING_STATUSES == frozenset(
        {"LICENSED", "UNLICENSED"}
    )
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="UNEVALUATED"
    )
    assert reason is not None
    assert ":coverage_status=UNEVALUATED" in reason


# --- gate NOT weakened: allowed-source path and immaterial path are byte-identical --------------


def test_allowed_source_path_unchanged_regardless_of_verdict() -> None:
    """An allowed native NO source admits exactly as before — the verdict leg is
    only consulted when the source is NOT in the allow-list."""
    for status in ("LICENSED", "INSUFFICIENT_DATA", None):
        reason = live_buy_no_conservative_evidence_rejection_reason(
            **{**_ATLANTA, "q_lcb_calibration_source": "EMOS_ANALYTIC"},
            settlement_coverage_status=status,
        )
        assert reason is None


# --- (d) immaterial-YES path byte-identical -----------------------------------------------------


def test_immaterial_yes_posterior_path_byte_identical() -> None:
    """yes_posterior < 0.20: the gate never reaches the source/verdict branch —
    outcome identical for every status value (no new admission, no new rejection)."""
    for status in ("LICENSED", "UNLICENSED", "INSUFFICIENT_DATA", None):
        reason = live_buy_no_conservative_evidence_rejection_reason(
            **{**_ATLANTA, "same_bin_yes_posterior": 0.10},
            settlement_coverage_status=status,
        )
        assert reason is None
    # missing-posterior rejection is also untouched by any verdict
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **{**_ATLANTA, "same_bin_yes_posterior": None},
        settlement_coverage_status="LICENSED",
    )
    assert reason == "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING", (
        "a verdict must never substitute for the independently-materialized YES posterior"
    )


# --- (e) TWIN-SITE LOCKSTEP: receipt-level gate sees the SAME verdict ---------------------------


def _money_path_clean_buy_no_receipt(**over: object) -> EventSubmissionReceipt:
    """A buy_no receipt passing every pre-buy_no money-path check so that
    `_receipt_money_path_blocker` reaches the conservative-evidence gate (mirrors
    tests/engine/test_same_bin_yes_posterior_receipt_wiring.py)."""
    base: dict[str, object] = dict(
        submitted=False,
        event_id="evt-atlanta-burst",
        direction="buy_no",
        q_live=0.80,
        q_lcb_5pct=0.7842,
        c_fee_adjusted=0.6021,
        trade_score=0.182,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
        same_bin_yes_posterior=0.22,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fam",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=5.0,
        kelly_cost_basis_id="cb",
        final_intent_id="fi",
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
    )
    base.update(over)
    return EventSubmissionReceipt(**base)  # type: ignore[arg-type]


def test_twin_sites_lockstep_for_one_fixture() -> None:
    """RELATIONSHIP (the 21a4c14ee2 twin-gate lesson): for the SAME fixture inputs,
    the proof-generation gate and the receipt-level money-path gate produce the SAME
    verdict-aware outcome — admitted under LICENSED, rejected (same reason string)
    when the verdict is absent. A starved receipt-level twin would re-reject every
    coverage-licensed buy_no the proof gate had just admitted."""
    # LICENSED: both sites admit.
    direct = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="LICENSED"
    )
    receipt = _money_path_clean_buy_no_receipt(settlement_coverage_status="LICENSED")
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert direct is None and stage is None, (
        f"twin sites diverged on LICENSED: direct={direct!r} receipt=({stage!r}, {reason!r})"
    )
    # No verdict: both sites reject with the SAME reason string.
    direct = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status=None
    )
    receipt = _money_path_clean_buy_no_receipt()  # settlement_coverage_status defaults None
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert stage == "TRADE_SCORE"
    assert reason == direct, (
        f"twin sites must emit the IDENTICAL rejection: direct={direct!r} receipt={reason!r}"
    )


def test_twin_sites_admit_only_the_same_exact_replacement_bound_certificate() -> None:
    direct = live_buy_no_conservative_evidence_rejection_reason(
        **_CERTIFIED_REPLACEMENT
    )
    receipt = _money_path_clean_buy_no_receipt(
        q_live=0.78,
        q_lcb_5pct=0.75,
        trade_score=0.1479,
        same_bin_yes_posterior=0.22,
        settlement_coverage_status="INSUFFICIENT_DATA",
        posterior_id=314159,
        probability_authority="replacement_0_1",
        condition_id="cond-atlanta-high",
        replacement_no_bound_certificate=_CERTIFIED_REPLACEMENT[
            "replacement_no_bound_certificate"
        ],
        decision_proof_bundle=_replacement_proof_bundle(),
    )
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert direct is None and stage is None, (direct, stage, reason)

    broken = {
        **_CERTIFIED_REPLACEMENT["replacement_no_bound_certificate"],
        "yes_q_ucb": 0.26,
    }
    direct = live_buy_no_conservative_evidence_rejection_reason(
        **{**_CERTIFIED_REPLACEMENT, "replacement_no_bound_certificate": broken}
    )
    receipt = _money_path_clean_buy_no_receipt(
        q_live=0.78,
        q_lcb_5pct=0.75,
        trade_score=0.1479,
        same_bin_yes_posterior=0.22,
        settlement_coverage_status="INSUFFICIENT_DATA",
        posterior_id=314159,
        probability_authority="replacement_0_1",
        condition_id="cond-atlanta-high",
        replacement_no_bound_certificate=broken,
        decision_proof_bundle=_replacement_proof_bundle(),
    )
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert stage == "TRADE_SCORE"
    assert reason == direct


def test_receipt_accepts_current_qkernel_bound_on_either_side_of_served_diagnostic() -> None:
    for current_lcb in (0.70, 0.76):
        economics = {
            "q_lcb_authority": "qkernel_payoff_bound",
            "probability_authority": "qkernel_payoff_direct_route",
            "pre_qkernel_q_posterior": 0.78,
            "pre_qkernel_q_lcb_5pct": 0.75,
            "payoff_q_point": 0.78,
            "payoff_q_lcb": current_lcb,
        }
        direct = live_buy_no_conservative_evidence_rejection_reason(
            **{
                **_CERTIFIED_REPLACEMENT,
                "q_lcb": current_lcb,
                "qkernel_execution_economics": economics,
            }
        )
        receipt = _money_path_clean_buy_no_receipt(
            q_live=0.78,
            q_lcb_5pct=current_lcb,
            trade_score=current_lcb - 0.6021,
            same_bin_yes_posterior=0.22,
            settlement_coverage_status="INSUFFICIENT_DATA",
            posterior_id=314159,
            probability_authority="replacement_0_1",
            condition_id="cond-atlanta-high",
            replacement_no_bound_certificate=_CERTIFIED_REPLACEMENT_CERT,
            qkernel_execution_economics=economics,
            decision_proof_bundle=_replacement_proof_bundle(),
        )
        stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
        assert direct is None and stage is None, (current_lcb, direct, stage, reason)


# --- receipt-hash stability: status omitted-when-None, present when set ------------------------


def test_receipt_json_omits_status_when_none_for_hash_stability() -> None:
    receipt = _money_path_clean_buy_no_receipt()
    blob = _receipt_json(receipt)
    assert '"settlement_coverage_status"' not in blob, (
        "None status must be OMITTED so legacy/canonical receipt hashes stay byte-stable"
    )
    receipt_with = _money_path_clean_buy_no_receipt(settlement_coverage_status="LICENSED")
    blob_with = _receipt_json(receipt_with)
    assert '"settlement_coverage_status":"LICENSED"' in blob_with, (
        "an evaluated verdict must be recoverable from the receipt blob (provenance law)"
    )


def test_receipt_json_round_trips_replacement_no_bound_certificate() -> None:
    receipt = _money_path_clean_buy_no_receipt(
        replacement_no_bound_certificate=_CERTIFIED_REPLACEMENT[
            "replacement_no_bound_certificate"
        ]
    )
    blob = _receipt_json(receipt)
    assert '"replacement_no_bound_certificate"' in blob
    assert '"yes_q_ucb":0.25' in blob

    assert '"replacement_no_bound_certificate"' not in _receipt_json(
        _money_path_clean_buy_no_receipt()
    )
