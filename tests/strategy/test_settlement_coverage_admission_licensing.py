# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: production defect (15:03Z+ burst, 2026-06-11): 12 of 21 families'
#   best candidates (ev/$ +0.04..+0.30; Atlanta q_lcb=0.7842 price=0.6021, HK
#   0.7613/0.6318, Warsaw, Moscow, Busan, Helsinki, Shanghai, Mexico City, Cape Town,
#   Karachi, London, San Francisco) killed ONLY by
#   ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING: yes_posterior=0.205..0.267
#   max=0.200 source=FORECAST_BOOTSTRAP — while the settlement-backward coverage
#   verdict for the SAME families said the settled record BACKED the claim. Twin-
#   authority instance #7: the cert layer licenses on the verdict
#   (_FUSED_BOOTSTRAP_COVERAGE_LICENSING_STATUSES = {LICENSED, UNLICENSED}); the
#   admission gate one step earlier still used the OLD source-brand vocabulary
#   {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC} and never saw the verdict.
"""Relationship antibodies: settlement-coverage verdict licenses material-YES buy_no.

CROSS-MODULE INVARIANTS pinned here:
  1. The licensing set has ONE home (live_admission.SETTLEMENT_COVERAGE_LICENSING_
     STATUSES); the adapter cert credential aliases it (registry entry #11).
  2. A verdict the settled record evaluated (LICENSED, or UNLICENSED where the
     shrink was the verdict's output) admits a material-YES buy_no whose q_lcb
     source is FORECAST_BOOTSTRAP; INSUFFICIENT_DATA / None reject EXACTLY as
     before, with the status in the reason (provenance law).
  3. CATEGORY-INVERSION KILL: before reconciliation a record-BACKED bootstrap
     q_lcb was rejected while a record-REFUTED one (re-branded SETTLEMENT_ISOTONIC
     by the shrink) was accepted. The verdict, not the brand, is the evidence.
  4. TWIN-SITE LOCKSTEP (21a4c14ee2 lesson): the proof-generation gate and the
     receipt-level gate (_receipt_money_path_blocker) produce the SAME verdict-aware
     outcome for the same inputs — the status travels on the receipt exactly as
     same_bin_yes_posterior does, omit-when-None for hash stability.
"""
from __future__ import annotations

from src.events.no_submit_receipts import _receipt_json
from src.events.reactor import (
    EventSubmissionReceipt,
    ReactorConfig,
    _receipt_money_path_blocker,
)
from src.strategy.live_inference.live_admission import (
    SETTLEMENT_COVERAGE_LICENSING_STATUSES,
    live_buy_no_conservative_evidence_rejection_reason,
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
    licenses (_FUSED_BOOTSTRAP_COVERAGE_LICENSING_STATUSES); kills the category
    inversion where only the REFUTED-then-rebranded claim was admitted."""
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="UNLICENSED"
    )
    assert reason is None


# --- (b) INSUFFICIENT_DATA / None reject exactly as today, status in reason --------------------


def test_material_yes_bootstrap_with_insufficient_data_rejected_with_status() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status="INSUFFICIENT_DATA"
    )
    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")
    assert ":coverage_status=INSUFFICIENT_DATA" in reason, (
        "the rejection must record WHICH verdict failed to license (provenance law)"
    )


def test_material_yes_bootstrap_with_no_verdict_rejected_with_missing_status() -> None:
    reason = live_buy_no_conservative_evidence_rejection_reason(
        **_ATLANTA, settlement_coverage_status=None
    )
    assert reason is not None
    assert reason.startswith("ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:")
    assert ":coverage_status=missing" in reason


def test_unknown_status_never_licenses() -> None:
    """Fail-closed: only the two members of the ONE-home licensing set admit. An
    unknown / future / corrupted status string rejects with the status recorded."""
    assert SETTLEMENT_COVERAGE_LICENSING_STATUSES == frozenset({"LICENSED", "UNLICENSED"})
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
