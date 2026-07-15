# Created: 2026-05-31
# Last reused or audited: 2026-07-14
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
from decimal import Decimal
import math

import pytest

from src.decision_kernel.certificates.execution import (
    _declared_max_slippage_bps,
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
from src.contracts.execution_intent import (
    _adverse_slippage_bps,
    quantize_submit_shares_for_venue_at_most,
    venue_submit_amount_precision_error,
)

# Reuse the certificate-graph fixtures already proven in the sibling suite.
from tests.decision_kernel.test_execution_command_certificate import (
    NOW,
    _cert,
    _live_cap_payload,
)
from src.decision_kernel import claims


_UNSET = object()


def test_declared_taker_slippage_is_a_decimal_upper_bound_after_float_round_trip():
    expected = 0.3808146118721461
    limit = 0.5778658536585366

    declared = Decimal(
        str(
            _declared_max_slippage_bps(
                direction="buy_yes",
                order_mode="TAKER",
                limit_price=limit,
                expected_fill_price=expected,
            )
        )
    )
    exact = _adverse_slippage_bps(
        direction="buy_yes",
        reference_price=Decimal(str(expected)),
        final_limit_price=Decimal(str(limit)),
    )

    assert declared >= exact


def _taker_chain(*, order_mode: str = "TAKER", actionable_overrides: dict | None = None,
                 quote_overrides: dict | None = None, return_parents: bool = False,
                 passive_maker_context=_UNSET,
                 available_crossable_shares: float | None = None,
                 sweep_expected_fill_price: str | None = None,
                 exact_taker_shares: str | None = None,
                 exact_taker_limit_price: str | None = None,
                 order_type: str | None = None,
                 time_in_force: str | None = None,
                 fee_rate: float = 0.0,
                 taker_quality_proof: dict | None = None):
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
        "min_entry_price": 0.0,
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "strategy_key": "center_buy",
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

    if taker_quality_proof is None and str(order_mode).strip().upper() == "TAKER":
        taker_quality_proof = {
            "schema_version": 1,
            "passed": True,
            "reason": "allowed",
            "passed_basis": "test_fixture_taker_quality_floor",
            "taker_fee_adjusted_edge": "0.03",
            "taker_expected_profit_usd": "0.30",
            "maker_expected_profit_usd": "0.00",
            "incremental_expected_profit_usd": "0.30",
            "model_confidence": "0.90",
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
        order_type=order_type,
        time_in_force=time_in_force,
        tick_size=0.01,
        min_order_size=1.0,
        fee_rate=fee_rate,
        best_bid=float(quote_payload["best_bid"]),
        best_ask=float(quote_payload["best_ask"]),
        available_crossable_shares=available_crossable_shares,
        sweep_expected_fill_price=sweep_expected_fill_price,
        exact_taker_shares=exact_taker_shares,
        exact_taker_limit_price=exact_taker_limit_price,
        taker_quality_proof=taker_quality_proof,
    )
    if return_parents:
        # The builder attaches all five parents; verify_final_intent requires them.
        final_intent_parents = (actionable, executable, quote, cost, forecast)
        return actionable, executable, final_intent, final_intent_parents
    return actionable, executable, final_intent


def _buy_fak_prefix_economics(*, shares: float = 5.0, limit: float = 0.45) -> dict:
    fee_rate = 0.05
    win_q = 0.60
    loss_q = 0.40
    floor = 100.0
    ceiling = 100.0
    max_fee_shape = limit * (1.0 - limit)
    worst_fee_per_share = 2.0 * fee_rate * max_fee_shape
    unit_cost = limit + worst_fee_per_share
    full_cost = unit_cost * shares
    robust_du = loss_q * math.log((floor - full_cost) / floor) + win_q * math.log(
        (ceiling - full_cost + shares) / ceiling
    )
    curve = "curve-current"
    return {
        "side": "YES",
        "global_jit_execution_curve_identity": curve,
        "global_target_shares": str(shares),
        "global_limit_price": str(limit),
        "global_terminal_win_probability_lcb": win_q,
        "global_terminal_loss_probability_ucb": loss_q,
        "global_terminal_loss_payoff_usd": "-2.25",
        "global_terminal_win_payoff_usd": "2.75",
        "global_terminal_wealth_after_loss_usd": "97.75",
        "global_terminal_wealth_after_win_usd": "102.75",
        "global_buy_fak_prefix_semantics": (
            "CONCAVE_WORST_LIMIT_ALL_NONZERO_PREFIXES_POSITIVE"
        ),
        "global_buy_fak_fee_rate_source": "CURRENT_EXECUTABLE_CURVE",
        "global_buy_fak_execution_curve_identity": curve,
        "global_buy_fak_fee_rate": str(fee_rate),
        "global_buy_fak_fee_rounding_bound": (
            "ROUNDED_FEE_AT_MOST_TWO_X_UNROUNDED"
        ),
        "global_buy_fak_worst_fee_shape": str(max_fee_shape),
        "global_buy_fak_worst_fee_per_share": str(worst_fee_per_share),
        "global_buy_fak_worst_unit_cost": str(unit_cost),
        "global_buy_fak_full_worst_cost_usd": str(full_cost),
        "global_buy_fak_full_robust_delta_log_wealth": robust_du,
        "global_buy_fak_full_robust_ev_usd": win_q * shares - full_cost,
    }


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


def test_global_buy_fak_requires_positive_prefix_certificate_and_exact_fee_binding():
    economics = _buy_fak_prefix_economics()
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        order_type="FAK_LIMIT",
        time_in_force="FAK",
        fee_rate=0.05,
        actionable_overrides={"qkernel_execution_economics": economics},
        available_crossable_shares=5.0,
        sweep_expected_fill_price="0.45",
        exact_taker_shares="5.00",
        exact_taker_limit_price="0.45",
        return_parents=True,
    )

    verify_final_intent(final_intent, parents)
    assert final_intent.payload["time_in_force"] == "FAK"
    assert final_intent.payload["fee_rate"] == pytest.approx(0.05)

    from src.engine.event_bound_final_intent import (
        _final_execution_intent_from_payload,
    )

    native = _final_execution_intent_from_payload(final_intent.payload)
    assert native.fee_rate == Decimal("0.05")
    expected_fill = native.expected_fill_price_before_fee
    assert native.fee_adjusted_execution_price == (
        expected_fill + Decimal("0.05") * expected_fill * (1 - expected_fill)
    )


def test_absorbing_day0_certainty_survives_actionable_to_pre_submit_projection():
    from src.engine.event_reactor_adapter import (
        PreSubmitAuthorityWitness,
        _pre_submit_revalidation_payload_from_final_intent,
    )

    finite_no = ["condition-1"]
    _, executable, final_intent = _taker_chain(
        actionable_overrides={
            "_edli_day0_finite_evidence_absorbing_no_conditions": finite_no,
        }
    )
    assert final_intent.payload[
        "_edli_day0_finite_evidence_absorbing_no_conditions"
    ] == finite_no

    witness = PreSubmitAuthorityWitness(
        quote_seen_at=NOW.isoformat(),
        book_hash="book-current",
        current_best_bid=0.40,
        current_best_ask=0.45,
        tick_size=0.01,
        min_order_size=1.0,
        neg_risk=False,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="clob_jit_book",
        book_captured_at=NOW.isoformat(),
        heartbeat_authority_id="heartbeat_supervisor",
        heartbeat_checked_at=NOW.isoformat(),
        user_ws_authority_id="ws_gap_guard",
        user_ws_checked_at=NOW.isoformat(),
        venue_connectivity_authority_id="polymarket_public_orderbook",
        venue_connectivity_checked_at=NOW.isoformat(),
        balance_allowance_authority_id="polymarket_wallet_readonly",
        balance_allowance_checked_at=NOW.isoformat(),
        checked_at=NOW.isoformat(),
    )
    pre_submit = _pre_submit_revalidation_payload_from_final_intent(
        final_intent=final_intent,
        executable_snapshot=executable,
        decision_time=NOW,
        authority_witness=witness,
    )

    assert pre_submit[
        "_edli_day0_finite_evidence_absorbing_no_conditions"
    ] == finite_no


def test_global_buy_fak_rejects_missing_prefix_certificate():
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        order_type="FAK_LIMIT",
        time_in_force="FAK",
        fee_rate=0.05,
        available_crossable_shares=5.0,
        exact_taker_shares="5.00",
        exact_taker_limit_price="0.45",
        return_parents=True,
    )

    with pytest.raises(CertificateVerificationError, match="prefix certificate invalid"):
        verify_final_intent(final_intent, parents)


def test_global_buy_fak_rejects_final_fee_rate_drift():
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        order_type="FAK_LIMIT",
        time_in_force="FAK",
        fee_rate=0.10,
        actionable_overrides={
            "qkernel_execution_economics": _buy_fak_prefix_economics()
        },
        available_crossable_shares=5.0,
        exact_taker_shares="5.00",
        exact_taker_limit_price="0.45",
        return_parents=True,
    )

    with pytest.raises(CertificateVerificationError, match="fee-rate binding mismatch"):
        verify_final_intent(final_intent, parents)


def test_global_exact_taker_preserves_deep_limit_and_exact_share_count():
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        actionable_overrides={"live_cap_reserved_notional_usd": 6.0},
        available_crossable_shares=12.0,
        sweep_expected_fill_price="0.1666666666666666666666666667",
        exact_taker_shares="12.00",
        exact_taker_limit_price="0.50",
        return_parents=True,
    )

    verify_final_intent(final_intent, parents)
    assert final_intent.payload["limit_price"] == 0.5
    assert final_intent.payload["size"] == 12.0
    assert final_intent.payload["notional_usd"] == 6.0
    assert final_intent.payload["expected_fill_price_before_fee"] == (
        "0.1666666666666666666666666667"
    )
    assert final_intent.payload["global_exact_order"] is True


def test_global_exact_taker_slippage_uses_persisted_high_precision_vwap():
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        actionable_overrides={
            "c_fee_adjusted": 0.86,
            "live_cap_reserved_notional_usd": 4.3,
        },
        quote_overrides={"best_ask": 0.86, "visible_depth": 5.0},
        available_crossable_shares=5.0,
        sweep_expected_fill_price="0.673158683960709666",
        exact_taker_shares="5.00",
        exact_taker_limit_price="0.86",
        return_parents=True,
    )

    verify_final_intent(final_intent, parents)
    native_hash = validate_final_intent_cert_for_existing_executor(final_intent)
    assert native_hash
    adverse = _adverse_slippage_bps(
        direction="buy_yes",
        reference_price=Decimal(final_intent.payload["expected_fill_price_before_fee"]),
        final_limit_price=Decimal(str(final_intent.payload["limit_price"])),
    )
    assert Decimal(str(final_intent.payload["max_slippage_bps"])) >= adverse


def test_global_exact_taker_certificate_rejects_share_binding_drift():
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        actionable_overrides={"live_cap_reserved_notional_usd": 6.0},
        available_crossable_shares=12.0,
        sweep_expected_fill_price="0.1666666666666666666666666667",
        exact_taker_shares="12.00",
        exact_taker_limit_price="0.50",
        return_parents=True,
    )
    drifted = dict(final_intent.payload)
    drifted["global_target_shares"] = "11.99"
    object.__setattr__(final_intent, "payload", drifted)

    with pytest.raises(
        CertificateVerificationError,
        match="global exact order share binding mismatch",
    ):
        verify_final_intent(final_intent, parents)


def test_taker_price_is_marketable_when_touch_inside_reservation():
    """Taker BUY limit crosses best_ask when best_ask <= c_fee_adjusted."""
    _, _, final_intent = _taker_chain(order_mode="TAKER")
    # best_ask=0.45 < reservation c_fee_adjusted=0.50 -> price at best_ask
    assert final_intent.payload["limit_price"] == pytest.approx(0.45)


def test_taker_buy_final_intent_uses_venue_legal_size_within_reserved_notional():
    """Immediate BUY final intents must not emit long-decimal venue-rejected sizes."""
    _, _, final_intent, parents = _taker_chain(
        order_mode="TAKER",
        actionable_overrides={
            "c_fee_adjusted": 0.51,
            "kelly_size_usd": 18.5152684,
            "live_cap_reserved_notional_usd": 18.5152684,
            "live_cap_notional_cap_enabled": True,
        },
        quote_overrides={
            "best_bid": 0.49,
            "best_ask": 0.51,
            "native_execution_price": 0.51,
        },
        available_crossable_shares=100.0,
        sweep_expected_fill_price="0.51",
        return_parents=True,
    )

    size = Decimal(str(final_intent.payload["size"]))
    limit_price = Decimal(str(final_intent.payload["limit_price"]))
    notional = Decimal(str(final_intent.payload["notional_usd"]))

    assert size == Decimal("36.0")
    assert size == size.quantize(Decimal("0.0001"))
    assert (size * limit_price) == (size * limit_price).quantize(Decimal("0.01"))
    assert notional <= Decimal("18.5152684")
    verify_final_intent(final_intent, parents)
    assert validate_final_intent_cert_for_existing_executor(final_intent)


@pytest.mark.parametrize(
    ("raw_size", "limit_price", "expected_size"),
    (
        ("10.338092370915971", "0.77", "10.00"),
        ("36.304447843137254", "0.51", "36.00"),
    ),
)
def test_reported_live_buy_sizes_quantize_to_valid_amounts_without_widening(
    raw_size: str,
    limit_price: str,
    expected_size: str,
):
    quantized = quantize_submit_shares_for_venue_at_most(
        "buy_no",
        Decimal(raw_size),
        final_limit_price=Decimal(limit_price),
        order_type="FOK",
    )

    assert quantized == Decimal(expected_size)
    assert quantized <= Decimal(raw_size)
    assert (
        venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal(limit_price),
            submitted_shares=quantized,
            order_type="FOK",
        )
        is None
    )


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
# Wave-1 2026-06-12: the CANARY force-taker branch of _select_edli_order_mode is
# DELETED (twin-authority disease: a knob that force-flips the proof's mode to
# TAKER competes with the single rest-then-cross policy authority). The two former
# canary-force tests (force taker at >=5c floor; no force below floor) are removed.
# Mode is now driven by the spread guard, the governor, and the proof's
# rest_then_cross_policy ONLY.
# --------------------------------------------------------------------------
def test_proof_policy_drives_taker_on_healthy_book():
    """A TAKER_* proof policy on a healthy (tight-spread) book routes TAKER; the
    canary force-taker knob is gone, so the proof policy is the sole mode authority."""
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.56,
        "c_fee_adjusted": 0.50,
        "trade_score": 0.06,  # e
        "p_fill_lcb": 0.15,   # thin
        "fee_rate": 0.0,
        "rest_then_cross_policy": "TAKER_FLEETING_EDGE",
    }
    quote_payload = {"best_bid": 0.48, "best_ask": 0.50, "visible_depth": 6.0}

    class _Snap:
        payload = {}

    mode = _select_edli_order_mode(
        actionable_payload=actionable_payload,
        quote_payload=quote_payload,
        best_bid=0.48,
        best_ask=0.50,
        executable_snapshot=_Snap(),
        fresh_best_bid=0.48,
        fresh_best_ask=0.50,
    )
    assert mode == "TAKER"


def test_rest_policy_rests_maker_on_thin_book_large_edge():
    """§2: with NO TAKER_* proof policy, the fresh-mode witness rests MAKER even on a
    thin book with a large edge — the escalation lane owns any later cross, never an
    inline one. (Replaces the deleted §2 EV-override / canary force-taker behaviour.)"""
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    actionable_payload = {
        "direction": "buy_yes",
        "q_live": 0.56,
        "c_fee_adjusted": 0.50,
        "trade_score": 0.06,  # e
        "p_fill_lcb": 0.15,   # thin
        "fee_rate": 0.0,
        # No rest_then_cross_policy -> REST/MAKER witness.
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
    )
    # No TAKER_* policy on the payload -> the rest-then-cross witness is MAKER, regardless
    # of the thin book / large edge. The escalation lane owns any later cross.
    assert mode == "MAKER"


# --------------------------------------------------------------------------
# Wave-2 item 8 (2026-06-12): taker FOK/FAK legality is UNCONDITIONAL. The
# former taker_fok_fak_live_enabled kill-lever (config flag + cert payload field
# + assert_taker_live_allowed OFF branch) is DELETED. The governor-decided taker
# tuple (post_only False, maker_intent False, FOK/FAK) is the single authority.
# --------------------------------------------------------------------------
def test_taker_legality_is_unconditional_no_kill_lever():
    """A governor-decided TAKER cert is submittable with NO config flag.

    Relationship test for the fold: the taker tuple alone authorizes the taker
    path at the expressibility boundary; there is no longer any payload flag or
    OFF branch that can deny it. The flag field must be ABSENT from the payload.
    """
    _, _, taker_intent = _taker_chain(order_mode="TAKER")
    # The deleted flag must not reappear in the cert payload.
    assert "taker_fok_fak_live_enabled" not in taker_intent.payload
    # Taker is authorized purely by the tuple: post_only False, maker_intent False.
    assert taker_intent.payload["post_only"] is False
    assert taker_intent.payload["maker_intent"] is False
    native_hash = validate_final_intent_cert_for_existing_executor(taker_intent)
    assert native_hash


def test_assert_taker_live_allowed_is_deleted():
    """The OFF-branch gate function must no longer exist on the trade_score module."""
    import src.strategy.live_inference.trade_score as _ts

    assert not hasattr(_ts, "assert_taker_live_allowed")


def test_maker_path_unaffected_by_taker_fold():
    """MAKER cert still passes the expressibility boundary (maker law untouched)."""
    _, _, maker_intent = _taker_chain(order_mode="MAKER")
    assert "taker_fok_fak_live_enabled" not in maker_intent.payload
    maker_hash = validate_final_intent_cert_for_existing_executor(maker_intent)
    assert maker_hash


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
