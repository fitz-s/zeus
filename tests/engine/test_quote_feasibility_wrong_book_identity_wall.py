# Created: 2026-06-12
# Last reused/audited: 2026-07-22
# Authority basis: WRONG-BOOK / IDENTITY WALL #5 live incident 2026-06-12
#   (Busan 27C 06-14 BUY NO POST_ONLY 386sh @0.02 vs real NO book 0.63/0.65).
#   Root cause: src/engine/event_reactor_adapter.py — selected_snapshot_row passed
#   to the proof-evidence bundle was keyed by the EVENT trigger token
#   (payload.token_id), not the DELTA-U-selected proof's token, so the
#   QuoteFeasibilityCertificate quoted a SIBLING bin's book (30C-YES 0.018/0.035).
#   The maker price then bid-improved that ghost 0.018 bid to a 0.02 limit that can
#   never fill.
"""Relationship tests (cross-module invariants) — WALL #5 wrong-book/identity.

Modules at the seam:
  A = src.engine.event_reactor_adapter._build_no_submit_proof_bundle_from_adapter_evidence
      (PRODUCES the QUOTE_FEASIBILITY AuthorityEvidence; best_bid/best_ask + the new
       quote_book_condition_id / quote_book_token_id come from selected_snapshot_row).
  B = src.engine.event_reactor_adapter._passive_maker_context_from_authorities
      (CONSUMES the cert; now ASSERTS the quoted-book identity == the candidate identity
       before returning a maker context).
  C = src.engine.event_reactor_adapter._selected_proof_snapshot_row_or_raise
      (ROOT FIX: the bundle's selected_snapshot_row is the SELECTED proof's native row).
  D = src.engine.event_reactor_adapter._assert_maker_book_agrees_with_fresh_witness
      (PRICE-SEAM DEFENSE: the maker-priced book must agree with the fresh submit-time
       JIT witness book within a small tolerance, else fail closed).

Cross-module property under test:
  The book a maker order is priced against MUST belong to the candidate the order is
  for (same condition_id + native token). A sibling bin's book can never enter the
  maker price — it raises a typed reason instead of resting at ~0.02 on a 0.64 book.
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import build_certificate
from src.engine.event_reactor_adapter import (
    _CandidateProof,
    PreSubmitAuthorityWitness,
    _assert_maker_book_agrees_with_fresh_witness,
    _assert_quote_book_identity_matches_candidate,
    _native_token_id_for_snapshot_row,
    _passive_maker_context_and_book,
    _passive_maker_context_from_authorities,
    _selected_proof_snapshot_row_or_raise,
    _would_cross_post_only_book,
)

NOW = datetime(2026, 6, 12, 13, 4, 46, tzinfo=timezone.utc)

# Busan family identities (the incident shape).
NO_27C_CONDITION = "0x6c1c66c96a7e1c6471d5df2469efab7380093ff2d423c1292edd873bd42ae309"
NO_27C_TOKEN = "no-27c"
YES_27C_TOKEN = "yes-27c"
YES_30C_TOKEN = "yes-30c"  # the WRONG sibling-bin book the cert carried in the incident.
YES_30C_CONDITION = "0x30c-condition"

# The CORRECT executable book for the selected NO-27C candidate.
REAL_NO_BID = 0.63
REAL_NO_ASK = 0.65
# The WRONG 30C-YES book the live cert carried.
GHOST_BID = 0.018
GHOST_ASK = 0.035


def _fresh_witness(*, best_bid: float = 0.10, best_ask: float = 0.93):
    return PreSubmitAuthorityWitness(
        quote_seen_at=NOW.isoformat(),
        book_hash="fresh-book-hash",
        current_best_bid=best_bid,
        current_best_ask=best_ask,
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="clob_jit_book",
        book_captured_at=NOW.isoformat(),
        heartbeat_authority_id="heartbeat-1",
        heartbeat_checked_at=NOW.isoformat(),
        user_ws_authority_id="user-ws-1",
        user_ws_checked_at=NOW.isoformat(),
        venue_connectivity_authority_id="venue-1",
        venue_connectivity_checked_at=NOW.isoformat(),
        balance_allowance_authority_id="balance-1",
        balance_allowance_checked_at=NOW.isoformat(),
        checked_at=NOW.isoformat(),
    )


def _quote_cert(*, payload: dict):
    return build_certificate(
        certificate_type=claims.QUOTE_FEASIBILITY,
        semantic_key="quote_feasibility:busan:identity",
        claim_type=claims.QUOTE_FEASIBILITY,
        mode="NO_SUBMIT",
        decision_time=NOW,
        source_available_at=NOW - timedelta(milliseconds=200),
        agent_received_at=NOW - timedelta(milliseconds=200),
        persisted_at=NOW - timedelta(milliseconds=200),
        payload=payload,
        authority_id="zeus.strategy.live_inference.executable_cost",
        authority_version="v1",
        algorithm_id="decision_kernel.quote_feasibility.event_bound_adapter",
        algorithm_version="v1",
    )


def _executable_snapshot_cert():
    return build_certificate(
        certificate_type=claims.EXECUTABLE_SNAPSHOT,
        semantic_key="executable_snapshot:busan:identity",
        claim_type=claims.EXECUTABLE_SNAPSHOT,
        mode="NO_SUBMIT",
        decision_time=NOW,
        source_available_at=NOW - timedelta(milliseconds=200),
        agent_received_at=NOW - timedelta(milliseconds=200),
        persisted_at=NOW - timedelta(milliseconds=200),
        payload={"identity": "ems2-busan-no-27c"},
        authority_id="zeus.trades.executable_market_snapshots",
        authority_version="v1",
        algorithm_id="decision_kernel.executable_snapshot.event_bound_adapter",
        algorithm_version="v1",
    )


def _actionable_cert(*, condition_id: str = NO_27C_CONDITION, token_id: str = NO_27C_TOKEN):
    """The candidate the maker order is FOR (the selected NO-27C leg)."""
    return build_certificate(
        certificate_type=claims.ACTIONABLE_TRADE,
        semantic_key="actionable:busan:identity",
        claim_type=claims.ACTIONABLE_TRADE,
        mode="NO_SUBMIT",
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload={
            "p_fill_lcb": 0.988,
            "condition_id": condition_id,
            "token_id": token_id,
            "direction": "buy_no",
        },
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def _native_book_payload(*, condition_id, token_id, best_bid, best_ask) -> dict:
    """A post-fix QUOTE_FEASIBILITY payload (carries the book-source identity binding)."""
    return {
        "identity": f"busan:{token_id}",
        "condition_id": NO_27C_CONDITION,
        "token_id": NO_27C_TOKEN,
        "direction": "buy_no",
        "selected_token_id": NO_27C_TOKEN,
        "quote_book_condition_id": condition_id,
        "quote_book_token_id": token_id,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "p_fill_lcb": 0.988,
    }


# ---------------------------------------------------------------------------
# (a) Regression fixture reproducing the incident shape: cert book = WRONG bin's
#     book (30C-YES 0.018/0.035) while the candidate is NO-27C. Typed rejection,
#     never a maker context that would price a ~0.02 intent.
# ---------------------------------------------------------------------------
def test_incident_shape_wrong_bin_book_is_rejected_not_priced():
    payload = _native_book_payload(
        condition_id=YES_30C_CONDITION,
        token_id=YES_30C_TOKEN,
        best_bid=GHOST_BID,
        best_ask=GHOST_ASK,
    )
    with pytest.raises(ValueError, match="QUOTE_FEASIBILITY_BOOK_IDENTITY_MISMATCH"):
        _passive_maker_context_from_authorities(
            actionable=_actionable_cert(),
            quote_feasibility_cert=_quote_cert(payload=payload),
            executable_snapshot_cert=_executable_snapshot_cert(),
            decision_time=NOW,
        )


# ---------------------------------------------------------------------------
# (b) Identity-binding test: a cert whose book token differs from the candidate's
#     selected token raises, independent of price level.
# ---------------------------------------------------------------------------
def test_book_token_mismatch_raises():
    payload = _native_book_payload(
        condition_id=NO_27C_CONDITION,  # same condition...
        token_id=YES_27C_TOKEN,  # ...but the YES token's book (wrong native side)
        best_bid=REAL_NO_BID,
        best_ask=REAL_NO_ASK,
    )
    with pytest.raises(ValueError, match="QUOTE_FEASIBILITY_BOOK_IDENTITY_MISMATCH:book_token_id"):
        _assert_quote_book_identity_matches_candidate(
            quote_payload=payload,
            actionable_payload=_actionable_cert().payload,
        )


def test_book_condition_mismatch_raises():
    payload = _native_book_payload(
        condition_id=YES_30C_CONDITION,
        token_id=NO_27C_TOKEN,
        best_bid=REAL_NO_BID,
        best_ask=REAL_NO_ASK,
    )
    with pytest.raises(ValueError, match="QUOTE_FEASIBILITY_BOOK_IDENTITY_MISMATCH:book_condition_id"):
        _assert_quote_book_identity_matches_candidate(
            quote_payload=payload,
            actionable_payload=_actionable_cert().payload,
        )


def test_legacy_cert_without_identity_fields_is_noop():
    """A pre-fix cert (no quote_book_* keys) cannot assert — must not spuriously raise."""
    payload = {
        "condition_id": NO_27C_CONDITION,
        "token_id": NO_27C_TOKEN,
        "best_bid": REAL_NO_BID,
        "best_ask": REAL_NO_ASK,
    }
    # No exception.
    _assert_quote_book_identity_matches_candidate(
        quote_payload=payload,
        actionable_payload=_actionable_cert().payload,
    )


# ---------------------------------------------------------------------------
# (c) Happy path: matching books → consumer returns a maker context whose spread
#     comes from the REAL NO book (0.63/0.65), never the ghost book.
# ---------------------------------------------------------------------------
def test_matching_books_yield_real_book_maker_context():
    payload = _native_book_payload(
        condition_id=NO_27C_CONDITION,
        token_id=NO_27C_TOKEN,
        best_bid=REAL_NO_BID,
        best_ask=REAL_NO_ASK,
    )
    context = _passive_maker_context_from_authorities(
        actionable=_actionable_cert(),
        quote_feasibility_cert=_quote_cert(payload=payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        decision_time=NOW,
    )
    assert context["best_bid"] == pytest.approx(REAL_NO_BID)
    assert context["best_ask"] == pytest.approx(REAL_NO_ASK)
    assert context["spread_usd"] == pytest.approx(REAL_NO_ASK - REAL_NO_BID)


def test_maker_limit_on_matching_book_is_bid_plus_tick_not_002():
    """Bind the consumer book to the maker_limit_price formula: the limit must be
    a tick improvement on the REAL 0.63 bid (~0.64), never the ghost 0.02."""
    from src.strategy.live_inference.mode_consistent_ev import maker_limit_price

    payload = _native_book_payload(
        condition_id=NO_27C_CONDITION,
        token_id=NO_27C_TOKEN,
        best_bid=REAL_NO_BID,
        best_ask=REAL_NO_ASK,
    )
    context = _passive_maker_context_from_authorities(
        actionable=_actionable_cert(),
        quote_feasibility_cert=_quote_cert(payload=payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        decision_time=NOW,
    )
    limit = maker_limit_price(
        best_bid=context["best_bid"],
        best_ask=context["best_ask"],
        tick_size=0.01,
        reservation=0.6614,  # c_fee_adjusted from the incident certificate
    )
    assert limit is not None
    assert limit == pytest.approx(0.64)
    assert limit > 0.5  # categorically not the 0.02 ghost-book intent


def test_taker_to_maker_redecision_prices_from_fresh_touch_not_same_midpoint_quote():
    """The final passive price and context share the fresh JIT book authority."""

    from src.decision_kernel.certificates.execution import _branch_limit_price

    payload = _native_book_payload(
        condition_id=NO_27C_CONDITION,
        token_id=NO_27C_TOKEN,
        best_bid=0.51,
        best_ask=0.52,
    )
    witness = _fresh_witness()

    context, maker_bid, maker_ask = _passive_maker_context_and_book(
        actionable=_actionable_cert(),
        quote_feasibility_cert=_quote_cert(payload=payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        authority_witness=witness,
        proof_order_mode="TAKER",
        order_mode="MAKER",
        decision_time=NOW,
    )
    assert context is not None
    limit = _branch_limit_price(
        side="BUY",
        order_mode="MAKER",
        reservation=0.80,
        best_bid=maker_bid,
        best_ask=maker_ask,
        tick_size=0.01,
        passive_maker_context=context,
    )

    assert context["best_bid"] == pytest.approx(0.10)
    assert context["best_ask"] == pytest.approx(0.93)
    assert limit == pytest.approx(0.11)
    assert limit != pytest.approx(0.52)
    assert not _would_cross_post_only_book(
        side="BUY",
        limit_price=limit,
        current_best_bid=witness.current_best_bid,
        current_best_ask=witness.current_best_ask,
    )


def test_taker_to_maker_fresh_price_survives_command_verifier_and_executor_recapture(
    monkeypatch,
):
    """One relationship proof spans builder, PRE_SUBMIT verifier, and executor."""

    import src.contracts.executable_market_snapshot as snap_contract
    import src.data.market_scanner as scanner
    import src.data.polymarket_client as pmc
    import src.engine.cycle_runtime as cycle_runtime
    import src.state.snapshot_repo as snapshot_repo
    from src.decision_kernel.certificate import build_certificate
    from src.decision_kernel.certificates.execution import (
        build_execution_command_certificate_from_final_intent,
        build_executor_expressibility_certificate,
        build_final_intent_certificate_from_actionable,
    )
    from src.decision_kernel.verifier import (
        verify_execution_command,
        verify_executor_expressibility,
        verify_final_intent,
    )
    from src.engine.event_bound_final_intent import (
        _final_execution_intent_from_payload,
        validate_final_intent_cert_for_existing_executor,
    )
    from src.engine.event_reactor_adapter import (
        _pre_submit_revalidation_payload_from_final_intent,
    )
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed
    from tests.decision_kernel.test_execution_command_certificate import (
        _live_cap_payload,
    )
    from tests.decision_kernel.test_taker_execution_law import _taker_chain
    from tests.execution.test_recapture_maker_economics import _LegacyIntent

    old_payload = _native_book_payload(
        condition_id=NO_27C_CONDITION,
        token_id=NO_27C_TOKEN,
        best_bid=0.51,
        best_ask=0.52,
    )
    witness = _fresh_witness()
    context, maker_bid, maker_ask = _passive_maker_context_and_book(
        actionable=_actionable_cert(),
        quote_feasibility_cert=_quote_cert(payload=old_payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        authority_witness=witness,
        proof_order_mode="TAKER",
        order_mode="MAKER",
        decision_time=NOW,
    )

    actionable, executable, _, parents = _taker_chain(
        order_mode="TAKER",
        actionable_overrides={
            "c_fee_adjusted": 0.80,
            "min_expected_profit_usd": 0.0,
            "min_submit_edge_density": 0.0,
            "selection_authority_applied": "qkernel_spine",
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.7,
                "payoff_q_lcb": 0.6,
                "cost": 0.11,
                "edge_lcb": 0.49,
                "optimal_delta_u": 0.01,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 0.88,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.6,
            },
        },
        quote_overrides={"best_bid": 0.51, "best_ask": 0.52},
        return_parents=True,
    )
    actionable, executable, quote, cost, forecast = parents
    final_intent = build_final_intent_certificate_from_actionable(
        actionable_cert=actionable,
        executable_snapshot_cert=executable,
        quote_feasibility_cert=quote,
        cost_model_cert=cost,
        forecast_authority_cert=forecast,
        decision_source_context=forecast.payload,
        passive_maker_context=context,
        decision_time=NOW,
        order_mode="MAKER",
        tick_size=0.01,
        min_order_size=5.0,
        fee_rate=0.0,
        best_bid=maker_bid,
        best_ask=maker_ask,
        exact_maker_shares="8.00",
    )
    assert final_intent.payload["post_only"] is True
    assert final_intent.payload["limit_price"] == pytest.approx(0.11)
    assert final_intent.payload["size"] == pytest.approx(8.0)
    verify_final_intent(final_intent, parents)

    live_cap = build_certificate(
        certificate_type=claims.LIVE_CAP,
        semantic_key="live-cap:cap-1",
        claim_type=claims.LIVE_CAP,
        mode="LIVE",
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload=_live_cap_payload(),
        authority_id="test.live_cap",
        authority_version="v1",
        algorithm_id="test.live_cap",
        algorithm_version="v1",
    )
    native_hash = validate_final_intent_cert_for_existing_executor(final_intent)
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=native_hash,
    )
    verify_executor_expressibility(
        expressibility, (final_intent, executable, live_cap)
    )

    pre_payload = _pre_submit_revalidation_payload_from_final_intent(
        final_intent=final_intent,
        executable_snapshot=executable,
        decision_time=NOW,
        authority_witness=witness,
    )
    pre_payload.update(
        {
            "aggregate_id": "event-1:intent-1",
            "aggregate_event_id": "pre-submit-event-1",
            "aggregate_event_hash": "pre-submit-hash",
            "aggregate_event_sequence": 1,
            "aggregate_execution_command_event_hash": "command-hash",
            "final_intent_certificate_hash": final_intent.certificate_hash,
            "live_cap_usage_id": live_cap.payload["usage_id"],
        }
    )
    pre_submit = build_certificate(
        certificate_type=claims.PRE_SUBMIT_REVALIDATION,
        semantic_key="pre-submit:event-1:intent-1",
        claim_type=claims.PRE_SUBMIT_REVALIDATION,
        mode="LIVE",
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload=pre_payload,
        authority_id="test.pre_submit",
        authority_version="v1",
        algorithm_id="test.pre_submit",
        algorithm_version="v1",
        parent_certificates=(final_intent, live_cap),
    )
    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )
    verify_execution_command(
        command, (actionable, final_intent, expressibility, live_cap, pre_submit)
    )

    old_snapshot = SimpleNamespace(
        snapshot_id="snap-stale",
        condition_id="condition-1",
        yes_token_id="yes-1",
        no_token_id="no-1",
    )
    fresh_snapshot = SimpleNamespace(
        snapshot_id="snap-fresh",
        executable_snapshot_hash="hash-fresh",
        selected_outcome_token_id="yes-1",
        min_tick_size=0.01,
        min_order_size=5.0,
        fee_details={"fee_rate_fraction": "0"},
        neg_risk=False,
        orderbook_top_ask=Decimal("0.93"),
        yes_token_id="yes-1",
        no_token_id="no-1",
        condition_id="condition-1",
        orderbook_depth_jsonb="{}",
        tradeability_status=SimpleNamespace(provenance_source=None),
    )

    def get_snapshot(_conn, snapshot_id):
        return old_snapshot if snapshot_id == "snap-stale" else fresh_snapshot

    monkeypatch.delenv("ZEUS_REPRICE_RECAPTURE_DISABLED", raising=False)
    monkeypatch.setattr(snapshot_repo, "get_snapshot", get_snapshot)
    monkeypatch.setattr(
        snapshot_repo, "latest_snapshot_for_market", lambda *_args: None
    )
    monkeypatch.setattr(
        snap_contract,
        "is_fresh",
        lambda snapshot, _now: snapshot.snapshot_id == "snap-fresh",
    )
    monkeypatch.setattr(
        scanner,
        "capture_executable_market_snapshot",
        lambda *_args, **_kwargs: {"executable_snapshot_id": "snap-fresh"},
    )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pmc, "PolymarketClient", FakeClient)
    monkeypatch.setattr(cycle_runtime, "_market_dict_from_snapshot", lambda _s: {})

    native_final = _final_execution_intent_from_payload(final_intent.payload)
    refreshed = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(
            executable_snapshot_id="snap-stale",
            executable_snapshot_hash="hash-stale",
            executable_snapshot_min_tick_size=0.01,
            executable_snapshot_min_order_size=5.0,
            limit_price=final_intent.payload["limit_price"],
        ),
        native_final,
        conn=object(),
        submitted_shares=8.0,
    )
    assert refreshed.executable_snapshot_id == "snap-fresh"
    assert refreshed.limit_price == pytest.approx(0.11)


# ---------------------------------------------------------------------------
# (c2) ROOT FIX helper: the bundle's selected_snapshot_row is the SELECTED proof's
#      native row; a proof with no native row fails closed (never the trigger book).
# ---------------------------------------------------------------------------
def _proof(*, row) -> _CandidateProof:
    return _CandidateProof(
        candidate=type("C", (), {"condition_id": NO_27C_CONDITION})(),
        token_id=NO_27C_TOKEN,
        direction="buy_no",
        row=row,
        executable_snapshot_id="ems2-busan-no-27c",
        execution_price=None,
        q_posterior=0.8169,
        q_lcb_5pct=0.7236,
        c_cost_95pct=0.6614,
        p_fill_lcb=0.988,
        trade_score=0.1,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="h",
        p_live_vector_hash="h",
    )


def test_selected_proof_row_is_the_proofs_native_row():
    native_row = {
        "condition_id": NO_27C_CONDITION,
        "no_token_id": NO_27C_TOKEN,
        "yes_token_id": YES_27C_TOKEN,
        "orderbook_top_bid": REAL_NO_BID,
        "orderbook_top_ask": REAL_NO_ASK,
    }
    out = _selected_proof_snapshot_row_or_raise(_proof(row=native_row))
    assert out is native_row
    # The book-identity stamping then names the NO token (native side for buy_no).
    assert _native_token_id_for_snapshot_row(out, "buy_no") == NO_27C_TOKEN


def test_selected_proof_without_native_row_fails_closed():
    with pytest.raises(ValueError, match="SELECTED_PROOF_SNAPSHOT_ROW_MISSING"):
        _selected_proof_snapshot_row_or_raise(_proof(row=None))


# ---------------------------------------------------------------------------
# (d) Price-seam defense: the maker-priced book must agree with the fresh JIT
#     witness book within tolerance. The incident's quote mid (~0.0265) vs the
#     fresh mid (~0.64) is ~37 ticks apart → fail closed.
# ---------------------------------------------------------------------------
def test_price_seam_rejects_ghost_book_vs_fresh_witness():
    with pytest.raises(ValueError, match="MAKER_BOOK_FRESH_WITNESS_DISAGREEMENT"):
        _assert_maker_book_agrees_with_fresh_witness(
            quote_best_bid=GHOST_BID,
            quote_best_ask=GHOST_ASK,
            fresh_best_bid=REAL_NO_BID,
            fresh_best_ask=REAL_NO_ASK,
            tick_size=0.01,
        )


def test_price_seam_accepts_agreeing_books():
    # Quote book == fresh book (within a couple ticks) → no raise.
    _assert_maker_book_agrees_with_fresh_witness(
        quote_best_bid=0.63,
        quote_best_ask=0.65,
        fresh_best_bid=0.64,
        fresh_best_ask=0.66,
        tick_size=0.01,
    )


def test_price_seam_single_sided_book_is_left_to_other_guards():
    # Missing fresh ask → mid undefined → this seam abstains (other guards own it).
    _assert_maker_book_agrees_with_fresh_witness(
        quote_best_bid=0.63,
        quote_best_ask=0.65,
        fresh_best_bid=0.63,
        fresh_best_ask=None,
        tick_size=0.01,
    )
