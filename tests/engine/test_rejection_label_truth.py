# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: OPERATOR LAW 2026-06-11 (每一个被拒绝的具体原因都要写出来,
#   每一个做的决策为什么都需要被查阅) + the measured REJECTION-LABEL LIE: Beijing
#   high 2026-06-12 @14:28 had 7/8 bins with LIVE two-sided NO books yet the family
#   receipt claimed EXECUTABLE_NATIVE_ASK_MISSING; Ankara high 2026-06-12 @14:28 had
#   11/11 live two-sided books yet emitted the bare EVENT_BOUND_SELECTED_CANDIDATE_
#   MISSING. Both are the SAME truth: every priced candidate was gate-rejected
#   (capital-efficiency EV<=0, the efficient-market normal state).
"""Relationship antibodies for the honest family-level rejection label + candidate book.

Cross-module invariants pinned here (book evaluations -> family reason string ->
receipt field -> envelope candidate_book -> regret row):

  A. A family where every PRICED proof failed an admission gate yields
     EVENT_BOUND_ALL_CANDIDATES_REJECTED carrying the per-class counts + the
     closest-to-tradeable leg — NOT EXECUTABLE_NATIVE_ASK_MISSING and NOT the bare
     SELECTED_CANDIDATE_MISSING.
  B. A family with genuinely ZERO books still yields EXECUTABLE_NATIVE_ASK_MISSING
     (the fallback path); a zero-PROOF family yields SELECTED_CANDIDATE_MISSING
     annotated with the structural precondition that emptied it.
  C. The receipt's envelope carries candidate_book with one entry per candidate and
     the surfaced fallback flagged is_selected_fallback.
  D. The honest relabel is OBSERVABILITY ONLY: no gate's accept/reject behavior
     changes (a genuinely-no-book family is still NATIVE_ASK_MISSING).
"""
from __future__ import annotations

import json

from src.engine import event_reactor_adapter as era
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.opportunity_book import build_family_opportunity_book


# --- pure-aggregator relationship pins (book evaluations -> honest reason) -----------------------


def _ev(
    candidate_id: str,
    direction: str,
    bin_label: str,
    price: float | None,
    q_lcb: float,
    missing_reason: str | None,
    *,
    q_post: float = 0.5,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_id=candidate_id,
        family_id="fam",
        condition_id=candidate_id,
        token_id="tok-" + candidate_id,
        direction=direction,
        bin_label=bin_label,
        execution_price=price,
        q_posterior=q_post,
        q_lcb_5pct=q_lcb,
        c_cost_95pct=None,
        p_fill_lcb=0.5,
        trade_score=0.0,
        p_value=0.5,
        passed_prefilter=False,
        native_quote_available=price is not None,
        missing_reason=missing_reason,
    )


def _book(evaluations):
    return build_family_opportunity_book(
        family_id="fam", evaluations=tuple(evaluations), event_id="evt", decided_candidate_id=None
    )


def test_all_priced_rejected_yields_all_candidates_rejected_with_class_counts():
    """A (the Beijing/Ankara lie): every priced proof failed capital-efficiency ->
    ALL_CANDIDATES_REJECTED carrying the per-class counts, NOT NATIVE_ASK_MISSING."""
    book = _book(
        [
            _ev("c1", "buy_no", "30C", 0.66, 0.40, "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-0.39"),
            _ev("c2", "buy_no", "31C", 0.76, 0.50, "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-0.34"),
            _ev("c3", "buy_no", "32C", 0.89, 0.60, "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-0.32"),
            _ev("c4", "buy_yes", "29C", 0.30, 0.20, "DIRECTION_LAW_BIN_FORECAST_MISMATCH:far"),
        ]
    )
    reason = era._family_all_candidates_rejected_reason(book)
    assert reason is not None
    assert reason.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    assert "EXECUTABLE_NATIVE_ASK_MISSING" not in reason
    # per-class counts present and correct
    assert "n=4" in reason
    assert "capital_efficiency_lcb_ev=3" in reason
    assert "direction_law=1" in reason
    # best leg = highest conservative EV/$ priced loser, with its numbers
    assert "best=" in reason and "q_lcb=" in reason and "price=" in reason and "ev_per_dollar=" in reason
    # the base reason must be in the typed registry (no K2.1 unregistered warning)
    from src.contracts.rejection_reasons import is_registered_rejection_reason

    assert is_registered_rejection_reason(reason)


def test_genuinely_no_books_keeps_native_ask_missing_label():
    """B/D: a family with NO priced candidate at all returns None from the aggregator,
    so the caller keeps the honest EXECUTABLE_NATIVE_ASK_MISSING — no relabel."""
    book = _book(
        [
            _ev("c1", "buy_no", "30C", None, 0.40, "clob_no_ask_illiquid"),
            _ev("c2", "buy_no", "31C", None, 0.50, "missing executable snapshot row"),
        ]
    )
    assert era._family_all_candidates_rejected_reason(book) is None


def test_empty_book_returns_none():
    """B: a zero-evaluation book is not an all-candidates-rejected family."""
    assert era._family_all_candidates_rejected_reason(_book([])) is None


def test_mixed_priced_and_bookless_counts_both_classes_but_relabels():
    """A: a family with SOME priced-rejected and SOME bookless bins is still
    ALL_CANDIDATES_REJECTED (>=1 priced loser), and the class histogram counts BOTH
    the gate class and native_ask_missing — the receipt is honest about the mix."""
    book = _book(
        [
            _ev("c1", "buy_no", "30C", 0.66, 0.40, "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev=-0.4"),
            _ev("c2", "buy_no", "31C", None, 0.50, "clob_no_ask_illiquid"),
            _ev("c3", "buy_no", "32C", None, 0.55, "clob_no_ask_illiquid"),
        ]
    )
    reason = era._family_all_candidates_rejected_reason(book)
    assert reason is not None
    assert "capital_efficiency_lcb_ev=1" in reason
    assert "native_ask_missing=2" in reason
    assert "n=3" in reason


def test_classifier_buckets_every_known_gate_and_falls_to_other():
    """The class taxonomy is total: every known gate prefix maps, unknown -> other
    (never silently collapsed into a misleading class)."""
    cases = {
        "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:x": "capital_efficiency_lcb_ev",
        "ADMISSION_CAPITAL_EFFICIENCY:price=missing": "capital_efficiency",
        "COVERAGE_UNLICENSED_TAIL:x": "coverage_unlicensed_tail",
        "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:x": "buy_no_evidence",
        "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING": "buy_no_evidence",
        "DIRECTION_LAW_BIN_FORECAST_MISMATCH:x": "direction_law",
        "clob_no_ask_illiquid": "native_ask_missing",
        "missing executable snapshot row": "native_ask_missing",
        "missing token id": "native_ask_missing",
        None: "other",
        "SOME_BRAND_NEW_GATE:x": "other",
    }
    for reason, expected in cases.items():
        assert era._classify_rejection_missing_reason(reason) == expected, reason


# --- candidate_book serialization pins (C) -------------------------------------------------------


def test_candidate_book_projection_one_entry_per_candidate_flags_fallback():
    """C: the envelope projection has one entry per candidate, carries the fate fields,
    truncates long strings to the cap, and flags the surfaced fallback."""
    book = _book(
        [
            _ev("c1", "buy_no", "30C", 0.66, 0.40, "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:" + "Z" * 500),
            _ev("c2", "buy_no", "31C", None, 0.50, "clob_no_ask_illiquid"),
        ]
    )
    book_dict = book.to_receipt_dict()
    # Simulate the receipt surfacing c2 (the bookless fallback) as the decision.
    book_dict["actual_receipt_selected_candidate_id"] = "c2"
    projection = era._candidate_book_for_envelope(book_dict)
    assert projection is not None
    assert len(projection) == 2
    by_id = {e["candidate_id"]: e for e in projection}
    assert set(by_id) == {"c1", "c2"}
    # fate fields present
    for entry in projection:
        for field in ("bin_label", "direction", "q_lcb_5pct", "execution_price", "missing_reason", "trade_score"):
            assert field in entry
    # string cap applied
    assert len(by_id["c1"]["missing_reason"]) == era._CANDIDATE_BOOK_STR_CAP
    # the surfaced fallback is flagged, the loser is not
    assert by_id["c2"]["is_selected_fallback"] is True
    assert by_id["c1"]["is_selected_fallback"] is False


def test_candidate_book_projection_is_none_when_no_candidates():
    assert era._candidate_book_for_envelope({"candidates": []}) is None
    assert era._candidate_book_for_envelope({}) is None
    assert era._candidate_book_for_envelope(None) is None


# --- real-path relationship: proof -> selection -> book -> honest reason (A, the lie) -----------


def _native_row(condition_id: str, no_ask: str, *, snapshot_id: str = "snap") -> dict:
    """Live two-sided NO book on a single bin (Beijing 2026-06-12 shape)."""
    depth = {
        "YES": {"asks": [{"price": "0.40", "size": "100000"}], "bids": [{"price": "0.39", "size": "100"}]},
        "NO": {"asks": [{"price": no_ask, "size": "100000"}], "bids": [{"price": "0.60", "size": "100"}]},
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": "yes-" + condition_id,
        "no_token_id": "no-" + condition_id,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "bh",
    }


def _priced_no_proof(condition_id: str, no_ask: str, bin_c: float, q_lcb: float, missing_reason: str):
    from src.events.candidate_binding import MarketTopologyCandidate
    from src.types.market import Bin

    row = _native_row(condition_id, no_ask, snapshot_id="snap-" + condition_id)
    ep, _pf, _c = era._execution_price_from_snapshot(row, selected_token_id=row["no_token_id"], direction="buy_no")
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city="beijing", target_date="2026-06-12", metric="high",
            condition_id=condition_id, yes_token_id=row["yes_token_id"], no_token_id=row["no_token_id"],
            bin=Bin(low=bin_c, high=bin_c, unit="C", label=f"{int(bin_c)}C"),
        ),
        token_id=row["no_token_id"], direction="buy_no", row=row,
        executable_snapshot_id=str(row["snapshot_id"]), execution_price=ep,
        q_posterior=q_lcb, q_lcb_5pct=q_lcb, c_cost_95pct=None, p_fill_lcb=1.0,
        trade_score=0.0, p_value=0.01, passed_prefilter=False, native_quote_available=True,
        p_cal_vector_hash="ch", p_live_vector_hash="lh", missing_reason=missing_reason,
    )


def test_beijing_lie_real_path_selects_none_and_relabels_to_all_rejected():
    """A (the measured lie, real path): a family of bins with LIVE two-sided NO books
    whose every priced NO is capital-efficiency-rejected (q_lcb < price) drives the
    REAL selector to None — the trigger for the bare SELECTED_CANDIDATE_MISSING —
    and the honest aggregator over the REAL book emits ALL_CANDIDATES_REJECTED."""
    proofs = (
        _priced_no_proof("cond-30", "0.66", 30.0, 0.30,
                         "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-0.55:q_lcb=0.30:price=0.66"),
        _priced_no_proof("cond-31", "0.76", 31.0, 0.40,
                         "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:ev_per_dollar=-0.47:q_lcb=0.40:price=0.76"),
    )
    # Every proof is PRICED (live book) but missing_reason set -> scoped out ->
    # nothing executable AND nothing bookless -> the real selector returns None.
    selected = era._selected_candidate_proof({"family_id": "fam", "event_id": "evt"}, proofs)
    assert selected is None, "all priced+gate-rejected, no bookless fallback -> selector None"
    book = era._opportunity_book_from_proofs(
        event_id="evt", family_id="fam", proofs=proofs, selected_proof=selected
    )
    reason = era._family_all_candidates_rejected_reason(book)
    assert reason is not None and reason.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    assert "capital_efficiency_lcb_ev=2" in reason
    assert "EXECUTABLE_NATIVE_ASK_MISSING" not in reason
    # the candidate_book projection from this real book has both bins, neither flagged
    # (selector None -> no fallback surfaced) and carries the gate reasons verbatim.
    projection = era._candidate_book_for_envelope(book.to_receipt_dict())
    assert projection is not None and len(projection) == 2
    assert all(e["is_selected_fallback"] is False for e in projection)
    assert all("CAPITAL_EFFICIENCY" in (e["missing_reason"] or "") for e in projection)


# --- hash-stability pin (D): the candidate_book lives in envelope_json, never the hash ----------


def test_receipt_hash_byte_identical_with_and_without_candidate_book():
    """D: envelope_json (carrying candidate_book) is EXCLUDED from receipt_json/
    receipt_hash, so persisting the per-candidate book can never drift the money-path
    receipt hash for the (event_id, final_intent_id) idempotency key."""
    import hashlib

    from src.events.no_submit_receipts import _receipt_json
    from src.events.reactor import EventSubmissionReceipt

    base = dict(
        submitted=False, event_id="e1", causal_snapshot_id="s1",
        side_effect_status="NO_SUBMIT", proof_accepted=True, reason="x",
        final_intent_id="fi1", q_live=0.5, c_fee_adjusted=0.4,
    )
    r_no = EventSubmissionReceipt(**base, envelope_json=None)
    r_yes = EventSubmissionReceipt(
        **base,
        envelope_json=json.dumps(
            {"candidate_book": [{"bin_label": "30C", "is_selected_fallback": True}], "rejection": None}
        ),
    )
    j_no, j_yes = _receipt_json(r_no), _receipt_json(r_yes)
    assert j_no == j_yes, "receipt_json must be byte-identical regardless of envelope candidate_book"
    assert (
        hashlib.sha256(j_no.encode()).hexdigest() == hashlib.sha256(j_yes.encode()).hexdigest()
    )
    assert "candidate_book" not in j_yes, "candidate_book must never enter the hashed receipt_json"
