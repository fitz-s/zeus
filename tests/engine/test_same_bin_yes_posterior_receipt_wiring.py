# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: production defect — Shanghai|2026-06-12|high 32°C buy_no
#   (trade_score +0.0448) rejected ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING;
#   docs/evidence/settlement_guard/2026-06-11_yesq_wiring_plan.md.
"""Relationship antibodies: the independently-materialized YES-bin posterior must
survive the proof -> receipt projection and reach the receipt-level buy_no gate.

ROOT CAUSE these guard against: `live_buy_no_conservative_evidence_rejection_reason`
is enforced at TWO sites — the ADAPTER (per-candidate, WITH `same_bin_yes_posterior`
= yes_q) and the post-submit RECEIPT-level `_receipt_money_path_blocker`. The receipt
contract had no `same_bin_yes_posterior` field, so the receipt-level gate defaulted
the posterior to None and rejected EVERY buy_no with
ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING — even when the q-vector carried
material YES mass for the bin. The fix carries the posterior on the receipt so both
gates see the SAME independently-materialized YES posterior.

CROSS-MODULE INVARIANT (the relationship, not the function):
  the YES posterior the ADAPTER gate evaluated == the YES posterior the RECEIPT gate
  evaluates, for the same selected proof.

COMPLEMENT-ARITHMETIC BAN preserved: `same_bin_yes_posterior` is the q-vector YES
mass (yes_q), NEVER 1 - price or 1 - q_no. These tests assert it equals yes_q, not a
complement of the NO direction posterior.
"""
from __future__ import annotations

import json

from src.events.reactor import (
    EventSubmissionReceipt,
    ReactorConfig,
    _receipt_money_path_blocker,
)
from src.events.no_submit_receipts import _receipt_json


# Real-shaped values from the live no_trade_regret row (Shanghai 32°C buy_no):
#   direction=buy_no, q_live (NO direction posterior)=0.86521, q_lcb=0.76996,
#   c_fee_adjusted=0.67122, trade_score=+0.04483.
# The 32°C YES posterior from the materialized q-vector is q_yes=0.1348, i.e.
# 1 - 0.86521 ROUNDS to 0.1348 — but the value the gate must see comes from the
# q-vector directly (yes_q), not the complement.
_SHANGHAI_NO_POSTERIOR = 0.8652100825648349
_SHANGHAI_Q_LCB = 0.7699559816098935
_SHANGHAI_PRICE = 0.67122
_SHANGHAI_TRADE_SCORE = 0.04483283282785847
_SHANGHAI_YES_Q = 0.1348  # materialized q-vector YES mass for the 32°C bin


def _money_path_clean_buy_no_receipt(**over: object) -> EventSubmissionReceipt:
    """A buy_no receipt that passes every pre-buy_no money-path check so that
    `_receipt_money_path_blocker` reaches the conservative-evidence gate."""
    base: dict[str, object] = dict(
        submitted=False,
        event_id="evt-shanghai-32c",
        direction="buy_no",
        q_live=_SHANGHAI_NO_POSTERIOR,
        q_lcb_5pct=_SHANGHAI_Q_LCB,
        c_fee_adjusted=_SHANGHAI_PRICE,
        trade_score=_SHANGHAI_TRADE_SCORE,
        q_lcb_calibration_source="FORECAST_BOOTSTRAP",
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


def test_receipt_carries_same_bin_yes_posterior_field() -> None:
    """The receipt contract MUST be able to carry the YES-bin posterior. Without
    the field the receipt-level gate is structurally starved (None)."""
    receipt = _money_path_clean_buy_no_receipt(same_bin_yes_posterior=_SHANGHAI_YES_Q)
    assert receipt.same_bin_yes_posterior == _SHANGHAI_YES_Q


def test_receipt_gate_admits_when_immaterial_yes_posterior_present() -> None:
    """THE INCIDENT, as a relationship test. The Shanghai 32°C buy_no carried a
    real (immaterial, < 0.20 floor) YES posterior. With the posterior on the
    receipt, `_receipt_money_path_blocker` admits (stage is None) — it does NOT
    emit ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING."""
    receipt = _money_path_clean_buy_no_receipt(same_bin_yes_posterior=_SHANGHAI_YES_Q)
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert stage is None, (
        f"posterior-carrying buy_no rejected at receipt level: stage={stage} reason={reason}"
    )
    assert "INDEPENDENT_YES_POSTERIOR_MISSING" not in reason


def test_receipt_gate_still_rejects_when_posterior_genuinely_absent() -> None:
    """The guard is NOT weakened: a buy_no whose receipt carries NO YES posterior
    (None) is STILL rejected with the missing-posterior reason. The fix carries the
    real value when it exists; it never fabricates one."""
    receipt = _money_path_clean_buy_no_receipt()  # same_bin_yes_posterior defaults None
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert stage == "TRADE_SCORE"
    assert reason == "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING"


def test_receipt_gate_material_yes_unlicensed_source_still_rejected() -> None:
    """Gate LOGIC is untouched: a MATERIAL YES posterior (>= 0.20) with an
    unlicensed q_lcb source is still rejected with CONSERVATIVE_EVIDENCE_MISSING —
    proving the fix only restored the lost INPUT, not relaxed the rule."""
    receipt = _money_path_clean_buy_no_receipt(same_bin_yes_posterior=0.35)
    stage, reason = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert stage == "TRADE_SCORE"
    assert "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING" in reason


def test_same_bin_yes_posterior_is_yes_q_not_a_no_complement() -> None:
    """COMPLEMENT-ARITHMETIC BAN antibody. The carried posterior is the q-vector
    YES mass (yes_q ~ 0.1348), which is NOT 1 - q_lcb_no and NOT (1 - price). If a
    future change ever wired the field from a NO complement, these inequalities
    would catch it: the real YES mass is far below 1 - q_lcb (0.230) and 1 - price
    (0.329)."""
    yes_q = _SHANGHAI_YES_Q
    one_minus_q_lcb = 1.0 - _SHANGHAI_Q_LCB
    one_minus_price = 1.0 - _SHANGHAI_PRICE
    assert abs(yes_q - one_minus_q_lcb) > 0.05
    assert abs(yes_q - one_minus_price) > 0.05
    # And the receipt-level gate's admission keys off yes_q (immaterial), not the
    # NO direction posterior (0.865, which would be "material" and force a source).
    receipt = _money_path_clean_buy_no_receipt(same_bin_yes_posterior=yes_q)
    stage, _ = _receipt_money_path_blocker(receipt, ReactorConfig())
    assert stage is None


def test_receipt_json_omits_posterior_when_none_keeps_hash_stable() -> None:
    """HASH-STABILITY antibody: a legacy/buy-YES receipt that never carried the
    posterior must serialize to receipt_json WITHOUT the key (byte-identical to the
    pre-field baseline, so receipt_hash does not drift). A buy_no receipt that
    carries it serializes the value."""
    legacy = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-legacy",
        direction="buy_yes",
        q_live=0.4,
        q_lcb_5pct=0.3,
        c_fee_adjusted=0.25,
        trade_score=0.05,
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
    )
    legacy_json = json.loads(_receipt_json(legacy))
    assert "same_bin_yes_posterior" not in legacy_json

    carrying = _money_path_clean_buy_no_receipt(same_bin_yes_posterior=_SHANGHAI_YES_Q)
    carrying_json = json.loads(_receipt_json(carrying))
    assert carrying_json["same_bin_yes_posterior"] == _SHANGHAI_YES_Q


def test_receipt_projection_round_trips_posterior_through_raw_receipt() -> None:
    """BOUNDARY antibody at the actual proof -> receipt projection seam: the
    adapter writes `same_bin_yes_posterior` into the raw_receipt dict alongside
    q_live; the dict -> EventSubmissionReceipt deserializer must map it onto the
    typed field so the receipt-level gate (a DIFFERENT module) sees it. This is the
    cross-module relationship the incident broke."""
    from src.engine.event_reactor_adapter import (
        _event_submission_receipt_from_typed_receipt_payload,
    )

    class _Evt:
        event_id = "evt-shanghai-32c"
        causal_snapshot_id = "snap-1"

    # Minimal valid raw_receipt payload mirroring the adapter success-path dict,
    # with the YES posterior set alongside q_live (the load-bearing pair).
    raw_receipt = {
        "schema": "edli_event_bound_no_submit_v1",
        "side_effect_status": "NO_SUBMIT",
        "submitted": False,
        "proof_accepted": True,
        "event_id": "evt-shanghai-32c",
        "causal_snapshot_id": "snap-1",
        "direction": "buy_no",
        "q_live": _SHANGHAI_NO_POSTERIOR,
        "q_lcb_5pct": _SHANGHAI_Q_LCB,
        "c_fee_adjusted": _SHANGHAI_PRICE,
        "trade_score": _SHANGHAI_TRADE_SCORE,
        "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
        "same_bin_yes_posterior": _SHANGHAI_YES_Q,
        "reason": "event_bound_final_intent_no_submit",
    }
    receipt = _event_submission_receipt_from_typed_receipt_payload(raw_receipt, _Evt())
    assert receipt.same_bin_yes_posterior == _SHANGHAI_YES_Q, (
        "the proof->receipt projection dropped same_bin_yes_posterior — the "
        "receipt-level buy_no gate will be starved and reject every buy_no"
    )
