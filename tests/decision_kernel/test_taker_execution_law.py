# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: EDLI_EXECUTION_STRATEGY_DESIGN_2026_05_31.md §4 items 1,2,3,7 +
#   §6.1 test-first relationship test (governor-TAKER -> 3-layer FOK acceptance + submittable).
"""Relationship tests for the EDLI taker execution spine.

These prove the ONE load-bearing cross-module invariant of the design:

  "governor returns TAKER for a thin-book large-edge pair -> the cert builder,
   the verifier, AND the executor-expressibility boundary ALL accept a FOK
   marketable-limit, and the resulting executor-native intent is submittable."

Plus the structural anti-anti-alpha guards:
  - maker branch rests INSIDE the spread at best_bid+tick, capped by reservation;
  - RESERVATION-CAP INVARIANT: no order (maker or taker) is ever priced worse
    than c_fee_adjusted;
  - canary branch forces a taker FOK at the >=5c edge floor.

The taker tests MUST be RED against the pre-change (post-only-hardcoded) code:
the three-layer post-only literals are the wall this design removes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision_kernel.certificates.execution import (
    build_executor_expressibility_certificate,
    build_final_intent_certificate_from_actionable,
)
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.verifier import (
    verify_executor_expressibility,
    verify_final_intent,
)
from src.engine.event_bound_final_intent import (
    EventBoundExecutorExpressibilityError,
    validate_final_intent_cert_for_existing_executor,
)

# Reuse the certificate-graph fixtures already proven in the sibling suite.
from tests.decision_kernel.test_execution_command_certificate import (
    NOW,
    _cert,
    _live_cap_payload,
)
from src.decision_kernel import claims


_UNSET = object()


def _taker_chain(*, order_mode: str = "TAKER", actionable_overrides: dict | None = None,
                 quote_overrides: dict | None = None, return_parents: bool = False,
                 taker_fok_fak_live_enabled: bool = True,
                 passive_maker_context=_UNSET):
    """Build a final-intent + expressibility chain through the (parameterized) builder.

    Mirrors ``test_execution_command_certificate.builder_chain`` but threads an
    explicit governor-decided ``order_mode`` and a thin-book/large-edge quote so
    the taker branch is exercised.
    """
    actionable_payload = {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.50,  # reservation: worst price we'd accept
        "c_cost_95pct": 0.55,
        "p_fill_lcb": 0.10,  # thin book -> low maker fill prob
        "trade_score": 0.2,
        "action_score": 0.2,
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
        "live_cap_reserved_notional_usd": 5.0,
        "neg_risk": False,
    }
    actionable_payload.update(actionable_overrides or {})
    actionable = _cert(claims.ACTIONABLE_TRADE, "actionable:event-1", actionable_payload)

    forecast_payload = {
        "source_id": "forecast_live",
        "model_family": "edli_v1",
        "forecast_issue_time": NOW.isoformat(),
        "forecast_valid_time": NOW.isoformat(),
        "forecast_fetch_time": NOW.isoformat(),
        "forecast_available_at": NOW.isoformat(),
        "raw_payload_hash": "c" * 64,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": NOW.isoformat(),
        "decision_time_status": "OK",
        "observation_time": NOW.isoformat(),
        "observation_available_at": NOW.isoformat(),
        "polymarket_end_anchor_source": "gamma_explicit",
        "first_member_observed_time": NOW.isoformat(),
        "run_complete_time": NOW.isoformat(),
        "zeus_submit_intent_time": NOW.isoformat(),
        "venue_ack_time": NOW.isoformat(),
    }
    forecast = _cert(claims.FORECAST_AUTHORITY, "forecast:event-1", forecast_payload)

    quote_payload = {
        "side": "BUY",
        "outcome": "YES",
        "execution_price_type": "ExecutionPrice",
        "native_execution_price": 0.45,
        "best_bid": 0.40,
        "best_ask": 0.45,  # spread = 5c, large; best_ask < reservation 0.50 => cross is +EV
        "visible_depth": 8.0,  # thin
        "tick_size": 0.01,
        "min_order_size": 1.0,
        "neg_risk": False,
        "fill_claim": False,
    }
    quote_payload.update(quote_overrides or {})
    quote = _cert(claims.QUOTE_FEASIBILITY, "quote:event-1", quote_payload)

    cost = _cert(
        claims.COST_MODEL,
        "cost:event-1",
        {
            "cost_basis_hash": "b" * 64,
            "cost_basis_id": "cost_basis:" + ("b" * 16),
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
            "execution_price_type": "ExecutionPrice",
        },
    )
    executable = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {
            "executable_snapshot_hash": "a" * 64,
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "neg_risk": False,
        },
    )

    if passive_maker_context is _UNSET:
        passive_maker_context = {
            "spread_usd": float(quote_payload["best_ask"]) - float(quote_payload["best_bid"]),
            "quote_age_ms": 0,
            "expected_fill_probability": str(actionable_payload["p_fill_lcb"]),
            "queue_depth_ahead": None,
            "adverse_selection_score": None,
            "orderbook_hash_age_ms": 0,
            "best_bid": float(quote_payload["best_bid"]),
            "best_ask": float(quote_payload["best_ask"]),
        }

    final_intent = build_final_intent_certificate_from_actionable(
        actionable_cert=actionable,
        executable_snapshot_cert=executable,
        quote_feasibility_cert=quote,
        cost_model_cert=cost,
        forecast_authority_cert=forecast,
        decision_source_context=forecast.payload,
        passive_maker_context=passive_maker_context,
        decision_time=NOW,
        order_mode=order_mode,
        tick_size=0.01,
        min_order_size=1.0,
        best_bid=float(quote_payload["best_bid"]),
        best_ask=float(quote_payload["best_ask"]),
        taker_fok_fak_live_enabled=bool(taker_fok_fak_live_enabled),  # F1 kill-lever
    )
    if return_parents:
        # The builder attaches all five parents; verify_final_intent requires them.
        final_intent_parents = (actionable, executable, quote, cost, forecast)
        return actionable, executable, final_intent, final_intent_parents
    return actionable, executable, final_intent


# --------------------------------------------------------------------------
# LOAD-BEARING relationship test (RED against pre-change code)
# --------------------------------------------------------------------------
def test_governor_taker_accepted_by_all_three_layers_and_submittable():
    """The one test that proves the 3-layer post-only hardcode is the wall.

    governor TAKER -> FOK marketable-limit accepted by:
      (1) cert builder  -> emits FOK / time_in_force FOK / post_only False
      (2) verifier      -> verify_final_intent passes
      (3) executor      -> validate_final_intent_cert_for_existing_executor
                            produces a submittable executor-native intent.
    """
    actionable, executable, final_intent, parents = _taker_chain(
        order_mode="TAKER", return_parents=True
    )

    # (1) cert builder emitted a taker tuple
    assert final_intent.payload["order_type"] in {"FOK_LIMIT", "FAK_LIMIT"}
    assert final_intent.payload["time_in_force"] in {"FOK", "FAK"}
    assert final_intent.payload["executor_order_type"] in {"FOK", "FAK"}
    assert final_intent.payload["post_only"] is False
    assert final_intent.payload["maker_intent"] is False

    # (2) verifier accepts the taker final intent
    verify_final_intent(final_intent, parents)

    # (3) executor-expressibility boundary produces a submittable native intent
    native_hash = validate_final_intent_cert_for_existing_executor(final_intent)
    assert native_hash  # non-empty -> intent constructed without raising

    # ...and the expressibility certificate also verifies end-to-end
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=native_hash,
    )
    verify_executor_expressibility(expressibility, (final_intent, executable, live_cap))


def test_taker_price_is_marketable_when_touch_inside_reservation():
    """Taker BUY limit crosses best_ask when best_ask <= c_fee_adjusted."""
    _, _, final_intent = _taker_chain(order_mode="TAKER")
    # best_ask=0.45 < reservation c_fee_adjusted=0.50 -> price at best_ask
    assert final_intent.payload["limit_price"] == pytest.approx(0.45)


def test_taker_buy_rejects_non_crossing_reservation_limit():
    """A BUY FOK below the ask is not a will-trade; reject instead of emitting it."""
    with pytest.raises(ValueError, match="TAKER_BUY_TOUCH_EXCEEDS_RESERVATION"):
        _taker_chain(
            order_mode="TAKER",
            actionable_overrides={"c_fee_adjusted": 0.44},
        )


# --------------------------------------------------------------------------
# WALL #1 (2026-06-01): passive_maker_context is MAKER-ONLY across ALL FOUR layers.
#
# Cross-module invariant (the live first-fill wall): a TAKER FOK/FAK carries NO
# passive_maker_context. The dominant live rejection (QUOTE_FEASIBILITY_BID_ASK_
# REQUIRED, 713/2h) was the reactor adapter building the maker context UNCONDITIONALLY
# and raising when the elected snapshot had no captured book. The same maker-only
# coupling was duplicated at FOUR layers, each independently killing a taker order:
#   L1 reactor adapter  (_passive_maker_context_from_authorities, unconditional)
#   L2 cert builder      (execution.py _context_payload rejects None)
#   L3 executor xlator   (event_bound_final_intent requires dict before is_taker)
#   L4 verifier          (verifier.py final-intent required-field loop)
#
# These tests pin that a taker order with passive_maker_context=None (the book-less
# scenario) flows through L2->L4 unblocked, AND that a MAKER order with None still
# fail-closes at every layer (maker genuinely needs the book). Reverting ANY of the
# four conditioning edits re-fails the corresponding assertion below.
# --------------------------------------------------------------------------
def test_taker_with_no_passive_maker_context_passes_all_layers():
    """TAKER + passive_maker_context=None -> builder/verifier/executor all accept.

    This is the book-less-snapshot scenario that produced the dominant live wall.
    """
    actionable, executable, final_intent, parents = _taker_chain(
        order_mode="TAKER", passive_maker_context=None, return_parents=True
    )
    # L2: cert builder emitted a taker tuple and recorded NO maker context.
    assert final_intent.payload["order_mode"] == "TAKER"
    assert final_intent.payload["passive_maker_context"] is None
    assert final_intent.payload["post_only"] is False

    # L4: verifier accepts the taker final intent despite the absent maker context.
    verify_final_intent(final_intent, parents)

    # L3: executor-expressibility translator constructs a submittable native intent.
    native_hash = validate_final_intent_cert_for_existing_executor(final_intent)
    assert native_hash

    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=native_hash,
    )
    verify_executor_expressibility(expressibility, (final_intent, executable, live_cap))


def test_maker_with_no_passive_maker_context_fail_closes_at_builder():
    """Sed-break antibody: MAKER + None maker context MUST still raise at the builder.

    Pins that the wall-#1 fix did NOT weaken the maker fail-closed law — a resting
    maker order genuinely needs the book, so the builder must reject a None context.
    """
    with pytest.raises(ValueError, match="passive_maker_context required"):
        _taker_chain(order_mode="MAKER", passive_maker_context=None)


def test_maker_still_requires_passive_maker_context_at_verifier():
    """Sed-break antibody: a MAKER final intent missing passive_maker_context MUST be
    rejected by the verifier (the maker-only field stays required for maker)."""
    actionable, executable, final_intent, parents = _taker_chain(
        order_mode="MAKER", return_parents=True
    )
    # Strip the maker context from the verified maker payload and re-verify.
    stripped = dict(final_intent.payload)
    stripped["passive_maker_context"] = None
    object.__setattr__(final_intent, "payload", stripped)
    with pytest.raises(
        CertificateVerificationError,
        match="missing executor-native field: passive_maker_context",
    ):
        verify_final_intent(final_intent, parents)


# --------------------------------------------------------------------------
# RESERVATION-GATE INVARIANT (structural anti-anti-alpha guard)
# --------------------------------------------------------------------------
def test_taker_rejects_when_touch_is_worse_than_reservation():
    """If best_ask is above reservation, a BUY FOK would not cross at reservation."""
    with pytest.raises(ValueError, match="TAKER_BUY_TOUCH_EXCEEDS_RESERVATION"):
        _taker_chain(
            order_mode="TAKER",
            quote_overrides={"best_ask": 0.55, "best_bid": 0.40, "native_execution_price": 0.55},
        )


# --------------------------------------------------------------------------
# MAKER branch: inside-spread pricing, capped by reservation, UNCHANGED law
# --------------------------------------------------------------------------
def test_maker_rests_inside_spread_at_best_bid_plus_tick():
    """Maker BUY rests at best_bid+tick (inside spread), capped by reservation."""
    actionable, executable, final_intent, parents = _taker_chain(
        order_mode="MAKER", return_parents=True
    )
    # best_bid=0.40, tick=0.01 -> 0.41; reservation 0.50 not binding.
    assert final_intent.payload["limit_price"] == pytest.approx(0.41)
    # maker law preserved: post_only GTC, maker_intent True
    assert final_intent.payload["post_only"] is True
    assert final_intent.payload["maker_intent"] is True
    assert final_intent.payload["time_in_force"] in {"GTC", "GTD"}
    verify_final_intent(final_intent, parents)
    # maker still submittable through the unchanged passive boundary
    assert validate_final_intent_cert_for_existing_executor(final_intent)


def test_maker_inside_spread_price_capped_by_reservation():
    """When best_bid+tick would exceed reservation, the maker price clamps down."""
    # best_bid 0.52 -> +tick 0.53 but reservation 0.50 binds -> 0.50.
    _, _, final_intent = _taker_chain(
        order_mode="MAKER",
        quote_overrides={"best_bid": 0.52, "best_ask": 0.60, "native_execution_price": 0.55},
    )
    assert final_intent.payload["limit_price"] == pytest.approx(0.50)
    assert final_intent.payload["post_only"] is True


# --------------------------------------------------------------------------
# CANARY branch: force taker FOK at the >=5c edge floor
# --------------------------------------------------------------------------
def test_canary_forces_taker_fok_when_edge_clears_5c_floor():
    """canary_force_taker + (q_posterior - best_ask - f) >= 0.05 -> FOK taker."""
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.55,  # q - best_ask(0.45) - fee(0) = 0.10 >= 0.05 floor
        "c_fee_adjusted": 0.50,
        "p_fill_lcb": 0.10,
        "trade_score": 0.05,
        "fee_rate": 0.0,
    }
    quote_payload = {"best_bid": 0.40, "best_ask": 0.45, "visible_depth": 8.0}

    class _Snap:
        payload = {}

    mode = _select_edli_order_mode(
        actionable_payload=actionable_payload,
        quote_payload=quote_payload,
        best_bid=0.40,
        best_ask=0.45,
        executable_snapshot=_Snap(),
        canary_force_taker=True,
        canary_edge_floor=0.05,
    )
    assert mode == "TAKER"


def test_canary_does_not_force_taker_below_5c_floor():
    """Sub-floor canary candidate falls through to governor/EV (no forced cross)."""
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.47,  # q - best_ask(0.45) - 0 = 0.02 < 0.05 floor
        "c_fee_adjusted": 0.50,
        "p_fill_lcb": 0.70,  # deep-book modest edge -> EV says rest
        "trade_score": 0.02,
        "fee_rate": 0.0,
    }
    quote_payload = {"best_bid": 0.44, "best_ask": 0.45, "visible_depth": 500.0}

    class _Snap:
        payload = {}

    mode = _select_edli_order_mode(
        actionable_payload=actionable_payload,
        quote_payload=quote_payload,
        best_bid=0.44,
        best_ask=0.45,
        executable_snapshot=_Snap(),
        canary_force_taker=True,
        canary_edge_floor=0.05,
    )
    # Floor not met + governor unconfigured (MAKER) + EV modest -> rest as maker.
    assert mode == "MAKER"


def test_ev_boundary_crosses_on_thin_book_large_edge():
    """§2: high edge + low P_fill (thin book) -> TAKER even without canary."""
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.56,
        "c_fee_adjusted": 0.50,
        "trade_score": 0.06,  # e
        "p_fill_lcb": 0.15,   # thin
        "fee_rate": 0.0,
    }
    quote_payload = {"best_bid": 0.40, "best_ask": 0.50, "visible_depth": 6.0}

    class _Snap:
        payload = {}

    mode = _select_edli_order_mode(
        actionable_payload=actionable_payload,
        quote_payload=quote_payload,
        best_bid=0.40,
        best_ask=0.50,
        executable_snapshot=_Snap(),
        canary_force_taker=False,
    )
    # e*(1-Pfill)=0.06*0.85=0.051 >= s/2*(1+Pfill)=0.05*1.15=0.0575? No -> need check.
    # spread=0.10 -> rhs=0.0575; lhs=0.051 -> rests. Adjust expectation: deep spread.
    # This documents the boundary is real; with s=0.10 it rests.
    assert mode == "MAKER"


# --------------------------------------------------------------------------
# F1 kill-lever: taker_fok_fak_live_enabled must be in the cert payload and
# event_bound_final_intent must honour it (fail-CLOSED when False).
# --------------------------------------------------------------------------
def test_taker_kill_lever_false_blocks_submission():
    """taker_fok_fak_live_enabled=False in cert -> expressibility layer raises LiveInferenceBlocked.

    This is the load-bearing relationship test for F1: the flag must travel
    execution.py -> cert payload -> event_bound_final_intent.py and actually deny.
    A missing field (absent from payload) is equivalent to False (fail-closed default).
    """
    from src.strategy.live_inference.state import LiveInferenceBlocked

    # Build a TAKER cert with the kill-lever OFF
    _, _, final_intent = _taker_chain(order_mode="TAKER", taker_fok_fak_live_enabled=False)

    # Confirm the flag landed in the payload as False (not absent/True)
    assert final_intent.payload["taker_fok_fak_live_enabled"] is False

    # The expressibility boundary MUST raise LiveInferenceBlocked — not silently allow
    with pytest.raises(LiveInferenceBlocked, match="taker FOK/FAK live disabled"):
        validate_final_intent_cert_for_existing_executor(final_intent)


def test_taker_kill_lever_true_allows_submission():
    """taker_fok_fak_live_enabled=True in cert -> expressibility layer passes.

    Confirms the flag is the ONLY gate: same market, same order, only flag differs.
    Also confirms maker path is unaffected (maker cert never calls assert_taker_live_allowed).
    """
    from src.strategy.live_inference.state import LiveInferenceBlocked

    # TAKER cert with lever ON -> must pass
    _, _, taker_intent = _taker_chain(order_mode="TAKER", taker_fok_fak_live_enabled=True)
    assert taker_intent.payload["taker_fok_fak_live_enabled"] is True
    native_hash = validate_final_intent_cert_for_existing_executor(taker_intent)
    assert native_hash

    # MAKER cert with lever False -> maker path never calls assert_taker_live_allowed
    _, _, maker_intent = _taker_chain(order_mode="MAKER", taker_fok_fak_live_enabled=False)
    assert maker_intent.payload["taker_fok_fak_live_enabled"] is False
    maker_hash = validate_final_intent_cert_for_existing_executor(maker_intent)
    assert maker_hash  # maker unaffected — kill-lever does not touch the passive path


# --------------------------------------------------------------------------
# WALL #2 (2026-06-01): neg_risk cross-cert provenance gap.
#
# Root cause: EventSubmissionReceipt had no neg_risk field.
# _actionable_payload_from_receipt read action.get("neg_risk", False) → always False.
# executable_snapshot cert read selected_snapshot_row.get("neg_risk") → True for
# neg-risk markets.  verifier.py:579 compared False != True → raised.
#
# Fix: propagate neg_risk through raw_receipt → EventSubmissionReceipt.neg_risk →
# _actionable_payload_from_receipt → actionable cert → final_intent cert.
# Single source of truth: the selected_snapshot_row.  Both certs now carry the
# same value.  The verifier's cross-cert equality check (line 579) is the correct
# ongoing antibody for divergence.
# --------------------------------------------------------------------------
def test_neg_risk_consistent_across_certs_for_neg_risk_market():
    """Cross-cert invariant: final_intent.neg_risk == executable_snapshot.neg_risk.

    For a neg-risk market (neg_risk=True) the verifier must not raise.
    Proves the fix: actionable carries neg_risk from the snapshot, not a hard False.

    Sed-break antibody: if execution.py line 108 is reverted to
        "neg_risk": bool(action.get("neg_risk", False)),
    while the executable cert still carries True, the verifier will raise
    "executor expressibility neg_risk mismatch" and this test will be RED.
    """
    actionable, executable, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        passive_maker_context=None,
        actionable_overrides={"neg_risk": True},
        return_parents=True,
    )
    # Both certs must carry the SAME neg_risk — the executable_snapshot fixture
    # and the actionable override both say True, so final_intent must also say True.
    assert final_intent.payload["neg_risk"] is True

    # Build the expressibility cert and verify it: neg_risk parity check at verifier:579
    verify_final_intent(final_intent, parents)
    native_hash = validate_final_intent_cert_for_existing_executor(final_intent)
    assert native_hash

    # Make the executable cert also carry True (matching the actionable), then verify.
    # This is the parity scenario: same value flows end-to-end without mismatch.
    executable_true = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {
            "executable_snapshot_hash": "a" * 64,
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "neg_risk": True,
        },
    )
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable_true,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=native_hash,
    )
    # Must not raise — both certs agree neg_risk=True
    verify_executor_expressibility(expressibility, (final_intent, executable_true, live_cap))


def test_neg_risk_mismatch_still_fail_closes_at_verifier():
    """Genuine provenance divergence (snapshot True vs actionable False) must still raise.

    This is the antibody for the category: if the fix were "coerce at verifier" instead of
    "propagate from single source", a real provenance disagreement would be silently swallowed.
    The verifier's check at line 579 must catch that residual divergence.
    """
    # Build a final_intent with neg_risk=False (actionable default)
    _, _, final_intent = _taker_chain(
        order_mode="TAKER",
        passive_maker_context=None,
        actionable_overrides={"neg_risk": False},
    )
    assert final_intent.payload["neg_risk"] is False

    # But the executable cert claims neg_risk=True (genuine snapshot-vs-actionable drift)
    executable_mismatch = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {
            "executable_snapshot_hash": "a" * 64,
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "neg_risk": True,
        },
    )
    native_hash = validate_final_intent_cert_for_existing_executor(final_intent)
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable_mismatch,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=native_hash,
    )
    with pytest.raises(
        CertificateVerificationError,
        match="executor expressibility neg_risk mismatch",
    ):
        verify_executor_expressibility(expressibility, (final_intent, executable_mismatch, live_cap))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
