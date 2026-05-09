"""Heavy runtime helpers extracted from cycle_runner.

The goal is to keep `cycle_runner.py` focused on orchestration while preserving
monkeypatch-based tests that patch symbols on the cycle_runner module. Every
function here receives a `deps` object, typically the cycle_runner module.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace

from src.config import get_mode
from src.contracts.decision_evidence import DecisionEvidence, EvidenceAsymmetryError
from src.contracts.execution_intent import DecisionSourceContext
from src.engine.time_context import lead_hours_to_date_start, lead_hours_to_settlement_close
from src.state.lifecycle_manager import (
    enter_day0_window_runtime_state,
    initial_entry_runtime_state_for_order_status,
)
MAX_SNAPSHOT_AGE_SECONDS = 5

from src.state.portfolio import (
    CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_MODEL_EDGE_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    INACTIVE_RUNTIME_STATES,
)

logger = logging.getLogger(__name__)


# H2 critic R6 (2026-05-04, rebuild fixes branch): the previously-hardcoded
# CANONICAL_STRATEGY_KEYS frozenset and STRATEGY_KEYS_BY_DISCOVERY_MODE
# inverse map were the 6th and 7th unmigrated sites flagged in the rebuild
# review — exactly the anti-pattern Bug review §D called out
# ("strategy identity remains a function of DiscoveryMode + edge shape").
# Both now derive from the strategy registry's live_status field and the
# new per-strategy cycle_axis_dispatch_mode field. The registry owns the
# truth; cycle_runtime only filters at use-sites.
def _canonical_strategy_keys() -> frozenset[str]:
    """Strategies cycle_runtime treats as canonical for telemetry/attribution.

    Equals the registry's ``live_safe_keys()`` — every boot-allowed strategy
    (live + shadow) is canonical because shadow strategies still emit
    decisions that need attribution. Recomputed on every call so registry
    swaps in tests propagate without import-order surprises.
    """
    from src.strategy.strategy_profile import live_safe_keys
    return live_safe_keys()


def _strategy_keys_by_discovery_mode() -> dict[str, frozenset[str]]:
    """Inverse of cycle_axis_dispatch_mode: discovery_mode → set of strategies
    routed under that mode by legacy clauses 1-4 short-circuit. Recomputed on
    every call (cheap; registry has 6 entries)."""
    from src.strategy.strategy_profile import cycle_axis_dispatch_inverse
    return cycle_axis_dispatch_inverse()


# Module-level read for backward-compat. Tests / external callers that
# imported these names get a snapshot equivalent to the pre-H2 behavior.
# Prefer the helpers above for fresh reads (e.g., post-_reload_for_test).
CANONICAL_STRATEGY_KEYS = _canonical_strategy_keys()
STRATEGY_KEYS_BY_DISCOVERY_MODE = _strategy_keys_by_discovery_mode()
NATIVE_BUY_NO_LIVE_APPROVED_CONTEXTS: frozenset[tuple[str, str, str]] = frozenset()
NATIVE_BUY_NO_LIVE_PROMOTION_VALIDATION = "native_buy_no_live_promotion_approved"
_FORWARD_PRICE_LINKAGE_OK_STATUSES = frozenset({"inserted", "unchanged"})


# D4: exit triggers whose statistical burden (2 consecutive negative cycles,
# no FDR correction) is weaker than the entry-side burden (bootstrap CI +
# BH-FDR). These are statistical hypotheses; force-majeure exits are excluded
# because their evidence class is market/risk/settlement authority, not
# entry-vs-exit statistical symmetry.
#
# Excluded triggers and their rationale:
# - SETTLEMENT_IMMINENT / WHALE_TOXICITY / MODEL_DIVERGENCE_PANIC /
#   FLASH_CRASH_PANIC / RED_FORCE_EXIT / VIG_EXTREME — force-majeure exits
#   driven by market-mechanics or risk-layer mandates, not statistical
#   inference. Symmetry with a statistical entry burden is not a coherent
#   question.
# - DAY0_OBSERVATION_REVERSAL — single-cycle observation-authority exit
#   fired when Day0 forward-edge drops below threshold while
#   day0_active=True. It does NOT use a consecutive_confirmations gate,
#   so the statistical weak-exit evidence template (sample_size=2,
#   consecutive_confirmations=2) would misrepresent its actual burden.
#   A future wave may introduce an observation-grade evidence variant.
_D4_ASYMMETRIC_EXIT_TRIGGERS = frozenset({
    "EDGE_REVERSAL",
    "BUY_NO_EDGE_EXIT",
    "BUY_NO_NEAR_EXIT",
})


def _deps_utcnow_iso(deps) -> str:
    utcnow = getattr(deps, "_utcnow", None)
    if utcnow is not None:
        try:
            return utcnow().isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _record_exit_evidence_gate_block(
    summary: dict,
    deps,
    *,
    trade_id: str,
    trigger: str,
    reason: str,
    entry_evidence: DecisionEvidence | None = None,
    exit_evidence: DecisionEvidence | None = None,
    error: str | None = None,
) -> tuple[bool, str]:
    summary["exit_evidence_gate_blocked"] = summary.get("exit_evidence_gate_blocked", 0) + 1
    if reason.startswith("EXIT_EVIDENCE_ASYMMETRY"):
        summary["exit_evidence_asymmetry_blocked"] = (
            summary.get("exit_evidence_asymmetry_blocked", 0) + 1
        )
    else:
        summary["exit_evidence_missing_blocked"] = (
            summary.get("exit_evidence_missing_blocked", 0) + 1
        )
    summary.setdefault("exit_evidence_gate_blocked_positions", []).append(
        {
            "position_id": trade_id,
            "trigger": trigger,
            "reason": reason,
        }
    )
    payload = {
        "trigger": trigger,
        "trade_id": trade_id,
        "reason": reason,
        "timestamp": _deps_utcnow_iso(deps),
    }
    if entry_evidence is not None:
        payload["entry_evidence_envelope"] = entry_evidence.to_json()
    if exit_evidence is not None:
        payload["exit_evidence_envelope"] = exit_evidence.to_json()
    if error:
        payload["error"] = error
    deps.logger.warning("exit_evidence_gate_blocked " + json.dumps(payload, sort_keys=True))
    return False, reason


def _exit_evidence_gate_allows_statistical_exit(
    *,
    conn,
    pos,
    exit_trigger: str,
    summary: dict,
    deps,
) -> tuple[bool, str | None]:
    if exit_trigger not in _D4_ASYMMETRIC_EXIT_TRIGGERS:
        return True, None
    if conn is None:
        return _record_exit_evidence_gate_block(
            summary,
            deps,
            trade_id=pos.trade_id,
            trigger=exit_trigger,
            reason="INCOMPLETE_EXIT_EVIDENCE:ENTRY_DECISION_EVIDENCE_DB_MISSING",
        )

    exit_evidence = DecisionEvidence(
        evidence_type="exit",
        statistical_method="consecutive_confirmation",
        sample_size=2,
        # No exit-side alpha/FDR exists for these triggers today; the semantic
        # absence is represented by fdr_corrected=False.
        confidence_level=1.0,
        fdr_corrected=False,
        consecutive_confirmations=2,
    )
    try:
        from src.state.decision_chain import load_entry_evidence

        entry_evidence = load_entry_evidence(conn, pos.trade_id)
    except Exception as exc:
        return _record_exit_evidence_gate_block(
            summary,
            deps,
            trade_id=pos.trade_id,
            trigger=exit_trigger,
            reason="INCOMPLETE_EXIT_EVIDENCE:ENTRY_DECISION_EVIDENCE_LOAD_FAILED",
            exit_evidence=exit_evidence,
            error=str(exc),
        )
    if entry_evidence is None:
        return _record_exit_evidence_gate_block(
            summary,
            deps,
            trade_id=pos.trade_id,
            trigger=exit_trigger,
            reason="INCOMPLETE_EXIT_EVIDENCE:ENTRY_DECISION_EVIDENCE_MISSING",
            exit_evidence=exit_evidence,
        )
    try:
        exit_evidence.assert_symmetric_with(entry_evidence)
    except EvidenceAsymmetryError as asym:
        return _record_exit_evidence_gate_block(
            summary,
            deps,
            trade_id=pos.trade_id,
            trigger=exit_trigger,
            reason="EXIT_EVIDENCE_ASYMMETRY_BLOCKED",
            entry_evidence=entry_evidence,
            exit_evidence=exit_evidence,
            error=str(asym),
        )

    summary["exit_evidence_gate_passed"] = summary.get("exit_evidence_gate_passed", 0) + 1
    return True, None


def _resolve_strategy_key(decision) -> str:
    strategy_key = str(getattr(decision, "strategy_key", "") or "").strip()
    return strategy_key if strategy_key in _canonical_strategy_keys() else ""


def _discovery_mode_value(mode) -> str:
    return str(getattr(mode, "value", mode) or "").strip()


def _strategy_phase_rejection_reason(strategy_key: str, mode) -> str | None:
    mode_value = _discovery_mode_value(mode)
    allowed = _strategy_keys_by_discovery_mode().get(mode_value)
    if allowed is None:
        return f"strategy_phase_unknown_mode:{mode_value or 'unknown'}"
    if strategy_key not in allowed:
        return f"strategy_phase_mismatch:{strategy_key}:{mode_value}"
    return None


def _native_buy_no_live_authorization_rejection_reason(decision, strategy_key: str, mode) -> str | None:
    from src.engine.evaluator import NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION

    applied_validations = {
        str(value).strip()
        for value in (getattr(decision, "applied_validations", None) or [])
        if str(value).strip()
    }
    if NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION not in applied_validations:
        return "NATIVE_BUY_NO_QUOTE_EVIDENCE_MISSING"
    mode_value = _discovery_mode_value(mode)
    approval_context = (strategy_key, mode_value, "buy_no")
    if approval_context not in NATIVE_BUY_NO_LIVE_APPROVED_CONTEXTS:
        return f"NATIVE_BUY_NO_LIVE_PROMOTION_MISSING:{strategy_key}:{mode_value}:buy_no"
    if NATIVE_BUY_NO_LIVE_PROMOTION_VALIDATION not in applied_validations:
        return "NATIVE_BUY_NO_LIVE_PROMOTION_EVIDENCE_MISSING"
    return None


def _forward_price_linkage_status_degraded(status: str) -> bool:
    return str(status or "").strip() not in _FORWARD_PRICE_LINKAGE_OK_STATUSES


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decision_source_context_from_epistemic_json(value: str | None) -> DecisionSourceContext | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    forecast_context = payload.get("forecast_context")
    return DecisionSourceContext.from_forecast_context(forecast_context)


def _decimal_payload(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    digits_text = "".join(str(digit) for digit in digits) or "0"
    while digits_text.endswith("0"):
        digits_text = digits_text[:-1]
        exponent += 1
    if exponent >= 0:
        text = digits_text + ("0" * exponent)
    else:
        decimal_index = len(digits_text) + exponent
        if decimal_index > 0:
            text = digits_text[:decimal_index] + "." + digits_text[decimal_index:]
        else:
            text = "0." + ("0" * -decimal_index) + digits_text
    return f"-{text}" if sign else text


def _floor_to_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    if tick_size <= Decimal("0"):
        raise ValueError("tick_size must be positive")
    return (value // tick_size) * tick_size


def _mode_timeout_seconds(mode_value: str) -> int:
    from src.execution.executor import MODE_TIMEOUTS

    normalized = str(mode_value or "").strip()
    if normalized not in MODE_TIMEOUTS:
        raise ValueError(
            f"Unknown execution mode '{normalized}' cannot default to timeout. "
            "Explicit runtime mode required."
        )
    return MODE_TIMEOUTS[normalized]


def _select_final_submit_order_type(conn, snapshot_id: str, deps) -> str:
    selector = getattr(deps, "select_final_order_type", None)
    if callable(selector):
        return str(selector(conn, snapshot_id))
    from src.execution.executor import _select_risk_allocator_order_type

    return str(_select_risk_allocator_order_type(conn, snapshot_id))


def _quantize_submit_shares(direction: str, shares: Decimal) -> Decimal:
    if shares <= Decimal("0"):
        raise ValueError("submitted_shares must be positive")
    quantum = Decimal("0.01")
    rounding = ROUND_CEILING if direction.startswith("buy_") else ROUND_FLOOR
    quantized = (shares / quantum).to_integral_value(rounding=rounding) * quantum
    if quantized <= Decimal("0"):
        raise ValueError("submitted_shares rounded to zero")
    return quantized


def _expected_gross_notional(cost_basis) -> Decimal:
    if cost_basis.requested_size_kind == "shares":
        return cost_basis.requested_size_value * cost_basis.expected_fill_price_before_fee
    return cost_basis.requested_size_value


def _attach_corrected_pricing_authority(
    *,
    decision,
    snapshot,
    candidate_limit_price: float,
    candidate_expected_fill_price_before_fee: float,
    candidate_size_usd: float,
    order_type: str = "GTC",
    cancel_after: datetime | None = None,
    resolution_window: str = "default",
    correlation_key: str = "",
) -> dict:
    """Attach corrected pricing evidence and the frozen final submit intent."""

    from src.contracts.execution_intent import (
        ExecutableCostBasis,
        ExecutableTradeHypothesis,
        FinalExecutionIntent,
        simulate_clob_sweep,
    )

    tokens = dict(getattr(decision, "tokens", {}) or {})
    edge = getattr(decision, "edge", None)
    if edge is None:
        raise ValueError("corrected pricing shadow requires edge")
    setattr(decision, "final_execution_intent", None)
    decision_snapshot_id = str(getattr(decision, "decision_snapshot_id", "") or "").strip()
    if not decision_snapshot_id:
        decision_snapshot_id = str(
            getattr(getattr(decision, "edge_context", None), "decision_snapshot_id", "")
            or ""
        ).strip()
    if not decision_snapshot_id:
        raise ValueError("corrected pricing shadow requires decision_snapshot_id")

    candidate_limit = Decimal(str(candidate_limit_price))
    candidate_expected_fill = Decimal(str(candidate_expected_fill_price_before_fee))
    candidate_size = Decimal(str(candidate_size_usd))
    direction = str(edge.direction)
    if direction.startswith("buy_"):
        is_marketable = candidate_limit >= snapshot.orderbook_top_ask
    elif direction.startswith("sell_"):
        is_marketable = candidate_limit <= snapshot.orderbook_top_bid
    else:
        is_marketable = False
    depth_status = "NOT_MARKETABLE_PASSIVE_LIMIT"
    sweep_payload = {
        "sweep_attempted": False,
        "sweep_depth_status": "NOT_MARKETABLE_PASSIVE_LIMIT",
    }
    if is_marketable:
        sweep = simulate_clob_sweep(
            snapshot=snapshot,
            direction=direction,
            requested_size_kind="notional_usd",
            requested_size_value=candidate_size,
            limit_price=candidate_limit,
        )
        if sweep.average_price is None:
            raise ValueError("corrected pricing shadow sweep produced no executable fill")
        candidate_expected_fill = sweep.average_price
        depth_status = sweep.depth_status
        sweep_payload = {
            "sweep_attempted": True,
            "sweep_depth_status": sweep.depth_status,
            "sweep_book_side": sweep.book_side,
            "sweep_levels_consumed": sweep.levels_consumed,
            "sweep_filled_shares": _decimal_payload(sweep.filled_shares),
            "sweep_gross_notional": _decimal_payload(sweep.gross_notional),
            "sweep_average_price": _decimal_payload(sweep.average_price),
            "sweep_worst_price": (
                None if sweep.worst_price is None else _decimal_payload(sweep.worst_price)
            ),
            "sweep_unfilled_size_value": _decimal_payload(sweep.unfilled_size_value),
        }

    immediate_order_type = str(order_type or "").strip().upper()
    final_unsupported_reason = ""
    if is_marketable and immediate_order_type in {"FOK", "FAK"}:
        submitted_shares = _quantize_submit_shares(direction, sweep.filled_shares)
        sweep = simulate_clob_sweep(
            snapshot=snapshot,
            direction=direction,
            requested_size_kind="shares",
            requested_size_value=submitted_shares,
            limit_price=candidate_limit,
        )
        if sweep.average_price is None:
            raise ValueError("corrected pricing final sweep produced no executable fill")
        candidate_expected_fill = sweep.average_price
        depth_status = sweep.depth_status
        sweep_payload.update(
            {
                "sweep_depth_status": sweep.depth_status,
                "sweep_book_side": sweep.book_side,
                "sweep_levels_consumed": sweep.levels_consumed,
                "sweep_filled_shares": _decimal_payload(sweep.filled_shares),
                "sweep_gross_notional": _decimal_payload(sweep.gross_notional),
                "sweep_average_price": _decimal_payload(sweep.average_price),
                "sweep_worst_price": (
                    None
                    if sweep.worst_price is None
                    else _decimal_payload(sweep.worst_price)
                ),
                "sweep_unfilled_size_value": _decimal_payload(
                    sweep.unfilled_size_value
                ),
            }
        )
        cost_basis = ExecutableCostBasis.from_snapshot_sweep(
            snapshot=snapshot,
            direction=direction,
            order_policy="marketable_limit_depth_bound",
            requested_size_kind="shares",
            requested_size_value=submitted_shares,
            final_limit_price=candidate_limit,
            fee_adjusted_execution_price=None,
        )
        sweep_payload["sweep_submitted_shares"] = _decimal_payload(submitted_shares)
    elif is_marketable:
        final_unsupported_reason = (
            "MARKETABLE_FINAL_INTENT_REQUIRES_IMMEDIATE_ORDER_TYPE"
        )
        cost_basis = ExecutableCostBasis.from_snapshot_sweep(
            snapshot=snapshot,
            direction=direction,
            order_policy="marketable_limit_depth_bound",
            requested_size_kind="notional_usd",
            requested_size_value=candidate_size,
            final_limit_price=candidate_limit,
            fee_adjusted_execution_price=None,
        )
    else:
        cost_basis = ExecutableCostBasis.from_snapshot(
            snapshot=snapshot,
            direction=direction,
            order_policy="post_only_passive_limit",
            requested_size_kind="notional_usd",
            requested_size_value=candidate_size,
            final_limit_price=candidate_limit,
            expected_fill_price_before_fee=candidate_expected_fill,
            fee_adjusted_execution_price=None,
            depth_status=depth_status,
        )
    snapshot_event_id = str(snapshot.event_id or "")
    hypothesis = ExecutableTradeHypothesis.from_cost_basis(
        event_id=snapshot_event_id,
        bin_id=str(getattr(getattr(edge, "bin", None), "label", "") or ""),
        payoff_probability=Decimal(str(edge.p_posterior)),
        posterior_distribution_id=f"decision_snapshot:{decision_snapshot_id}",
        market_prior_id=None,
        fdr_family_id=f"legacy_selection_family:{decision_snapshot_id}",
        cost_basis=cost_basis,
    )
    final_intent = None
    if is_marketable and not final_unsupported_reason:
        final_intent = FinalExecutionIntent.from_hypothesis_and_cost_basis(
            hypothesis=hypothesis,
            cost_basis=cost_basis,
            order_type=immediate_order_type,
            post_only=False,
            cancel_after=cancel_after,
            max_slippage_bps=Decimal("200"),
            event_id=snapshot_event_id,
            resolution_window=resolution_window,
            correlation_key=correlation_key,
            decision_source_context=_decision_source_context_from_epistemic_json(
                getattr(decision, "epistemic_context_json", None)
            ),
        )
        setattr(decision, "final_execution_intent", final_intent)
    payload = {
        "pricing_semantics_version": cost_basis.pricing_semantics_version,
        "shadow_only": final_intent is None,
        "live_submit_authority": final_intent is not None,
        "field_semantics": (
            "final_execution_intent_submit_authority"
            if final_intent is not None
            else (
                "marketable_limit_requires_immediate_order_type"
                if final_unsupported_reason
                else "passive_limit_requires_maker_only_support"
            )
        ),
        "selected_token_id": cost_basis.selected_token_id,
        "direction": cost_basis.direction,
        "snapshot_id": cost_basis.quote_snapshot_id,
        "snapshot_hash": cost_basis.quote_snapshot_hash,
        "cost_basis_id": cost_basis.cost_basis_id,
        "cost_basis_hash": cost_basis.cost_basis_hash,
        "hypothesis_id": hypothesis.fdr_hypothesis_id,
        "final_execution_intent_id": None if final_intent is None else final_intent.hypothesis_id,
        "order_policy": cost_basis.order_policy,
        "order_type": immediate_order_type if final_intent is None else final_intent.order_type,
        "cancel_after": (
            None
            if final_intent is None or final_intent.cancel_after is None
            else final_intent.cancel_after.isoformat()
        ),
        "event_id": snapshot_event_id,
        "resolution_window": (
            resolution_window if final_intent is None else final_intent.resolution_window
        ),
        "correlation_key": (
            correlation_key if final_intent is None else final_intent.correlation_key
        ),
        "candidate_final_limit_price": _decimal_payload(cost_basis.final_limit_price),
        "candidate_expected_fill_price_before_fee": _decimal_payload(
            cost_basis.expected_fill_price_before_fee
        ),
        "candidate_fee_adjusted_execution_price": _decimal_payload(
            cost_basis.fee_adjusted_execution_price
        ),
        "candidate_size_kind": cost_basis.requested_size_kind,
        "candidate_size_value": _decimal_payload(cost_basis.requested_size_value),
        "candidate_size_usd": _decimal_payload(_expected_gross_notional(cost_basis)),
        "candidate_submitted_shares": (
            None
            if final_intent is None
            else _decimal_payload(final_intent.submitted_shares)
        ),
        "fee_rate": _decimal_payload(cost_basis.worst_case_fee_rate),
        "fee_source": cost_basis.fee_source,
        "neg_risk": cost_basis.neg_risk,
        "posterior_distribution_id": hypothesis.posterior_distribution_id,
        "market_prior_id": hypothesis.market_prior_id,
        "payoff_probability": _decimal_payload(hypothesis.payoff_probability),
        "submitted_limit_price": None,
        "submit_path": None,
    }
    if final_intent is None:
        payload["unsupported_reason"] = (
            final_unsupported_reason
            or "PASSIVE_LIMIT_REQUIRES_POST_ONLY_OR_MAKER_ONLY_SUBMIT"
        )
    payload.update(sweep_payload)
    tokens["corrected_pricing_shadow"] = payload
    decision.tokens = tokens
    validation = (
        "final_execution_intent_built"
        if final_intent is not None
        else "corrected_pricing_shadow_built"
    )
    if validation not in decision.applied_validations:
        decision.applied_validations.append(validation)
    return payload


def _reprice_decision_from_executable_snapshot(
    conn,
    decision,
    snapshot_fields: dict,
    final_intent_context: dict | None = None,
) -> float | None:
    """Reprice a selected entry decision from the executable snapshot book.

    The evaluator quote selects a candidate. The post-decision executable
    snapshot is the pre-submit pricing authority.
    """

    snapshot_id = str(snapshot_fields.get("executable_snapshot_id") or "")
    if not snapshot_id:
        raise ValueError("EXECUTABLE_SNAPSHOT_UNAVAILABLE: missing snapshot_id")
    if decision.edge is None:
        raise ValueError("EXECUTABLE_REPRICE_REJECTED: missing edge")
    from src.data.market_scanner import _top_book_level_decimal
    from src.engine.evaluator import _size_at_execution_price_boundary
    from src.state.snapshot_repo import get_snapshot
    from src.strategy.market_fusion import vwmp

    snapshot = get_snapshot(conn, snapshot_id)
    if snapshot is None:
        raise ValueError(f"EXECUTABLE_SNAPSHOT_UNAVAILABLE: {snapshot_id}")
    captured_at = snapshot.captured_at
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    snapshot_age_seconds = (datetime.now(timezone.utc) - captured_at).total_seconds()
    if snapshot_age_seconds > MAX_SNAPSHOT_AGE_SECONDS:
        raise ValueError("executable_snapshot_stale")
    from src.config import settings
    from src.contracts import (
        Direction,
        HeldSideProbability,
        NativeSidePrice,
        compute_native_limit_price,
        simulate_clob_sweep,
    )
    from src.contracts.slippage_bps import SlippageBps
    tokens = dict(getattr(decision, "tokens", {}) or {})
    selected_method = str(decision.selected_method or "").strip()
    edge_context_entry_method = str(
        getattr(getattr(decision, "edge_context", None), "entry_provenance", "") or ""
    ).strip()
    entry_method = selected_method or edge_context_entry_method or "unknown"
    expected_token = tokens.get("no_token_id") if decision.edge.direction == "buy_no" else tokens.get("token_id")
    expected_label = "NO" if decision.edge.direction == "buy_no" else "YES"
    if str(snapshot.selected_outcome_token_id or "") != str(expected_token or ""):
        raise ValueError("EXECUTABLE_SNAPSHOT_TOKEN_MISMATCH")
    if str(snapshot.outcome_label or "").upper() != expected_label:
        raise ValueError("EXECUTABLE_SNAPSHOT_OUTCOME_MISMATCH")

    try:
        orderbook = json.loads(snapshot.orderbook_depth_jsonb)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"EXECUTABLE_SNAPSHOT_UNAVAILABLE: invalid orderbook JSON: {exc}") from exc
    best_bid, bid_size = _top_book_level_decimal(orderbook, "bids")
    best_ask, ask_size = _top_book_level_decimal(orderbook, "asks")
    best_bid_float = float(best_bid)
    best_ask_float = float(best_ask)
    bid_size_float = float(bid_size)
    ask_size_float = float(ask_size)
    original_edge = decision.edge
    original_size = float(getattr(decision, "size_usd", 0.0) or 0.0)
    snapshot_vwmp = vwmp(best_bid_float, best_ask_float, bid_size_float, ask_size_float)
    repriced_edge = float(decision.edge.p_posterior) - float(snapshot_vwmp)
    if repriced_edge <= 0.0:
        raise ValueError(f"EXECUTABLE_REPRICE_REJECTED: edge={repriced_edge:.6f}")
    direction = Direction(decision.edge.direction)
    snapshot_limit_price = compute_native_limit_price(
        HeldSideProbability(float(decision.edge.p_posterior), direction),
        NativeSidePrice(float(snapshot_vwmp), direction),
        limit_offset=float(settings["execution"]["limit_offset_pct"]),
    )
    tick_size_decimal = Decimal(str(snapshot.min_tick_size))
    snapshot_limit_decimal = _floor_to_tick(
        Decimal(str(round(float(snapshot_limit_price), 12))),
        tick_size_decimal,
    )
    snapshot_limit_price = float(snapshot_limit_decimal)
    slippage_reference_price = min(float(decision.edge.p_posterior), float(snapshot_vwmp))
    max_slippage = SlippageBps(value_bps=200.0, direction="adverse")
    best_ask_slippage_bps = 0.0
    if slippage_reference_price > 0.0 and best_ask_float > slippage_reference_price:
        best_ask_slippage_bps = (
            (best_ask_float - slippage_reference_price) / slippage_reference_price * 10_000.0
        )

    sizing_bankroll = float(getattr(decision, "sizing_bankroll", 0.0) or 0.0)
    kelly_multiplier = float(getattr(decision, "kelly_multiplier_used", 0.0) or 0.0)
    taker_fee_rate = float(getattr(decision, "execution_fee_rate", 0.0) or 0.0)
    if sizing_bankroll <= 0.0 or kelly_multiplier <= 0.0:
        raise ValueError("EXECUTABLE_REPRICE_REJECTED: missing sizing context")
    # The first candidate is a passive maker limit. If the book supports an
    # immediate fill, the marketable branch below resizes with taker fees.
    repriced_size_at_snapshot_vwmp = _size_at_execution_price_boundary(
        p_posterior=float(decision.edge.p_posterior),
        entry_price=float(snapshot_vwmp),
        fee_rate=0.0,
        sizing_bankroll=sizing_bankroll,
        kelly_multiplier=kelly_multiplier,
    )
    if repriced_size_at_snapshot_vwmp <= 0.0:
        raise ValueError("EXECUTABLE_REPRICE_REJECTED: repriced size is zero")
    final_best_ask: float | None = None
    final_price = float(snapshot_limit_price)
    repriced_size = repriced_size_at_snapshot_vwmp
    corrected_candidate_price = float(snapshot_limit_price)
    corrected_candidate_expected_fill = float(snapshot_limit_price)
    corrected_candidate_size = repriced_size_at_snapshot_vwmp
    best_ask_edge = float(decision.edge.p_posterior) - best_ask_float
    p_posterior_decimal = Decimal(str(decision.edge.p_posterior))
    slippage_reference_decimal = Decimal(str(slippage_reference_price))
    slippage_cap_decimal = slippage_reference_decimal * (
        Decimal("1") + Decimal(str(max_slippage.fraction))
    )
    positive_edge_cap_decimal = p_posterior_decimal - tick_size_decimal
    depth_sweep_limit_decimal = Decimal("0")
    if positive_edge_cap_decimal > Decimal("0") and slippage_cap_decimal > Decimal("0"):
        depth_sweep_limit_decimal = _floor_to_tick(
            min(slippage_cap_decimal, positive_edge_cap_decimal),
            tick_size_decimal,
        )
    depth_sweep_limit_float = float(depth_sweep_limit_decimal)
    best_ask_inside_slippage_budget = (
        depth_sweep_limit_float > 0.0
        and best_ask_float <= depth_sweep_limit_float
    )
    if best_ask_edge > 0.0 and best_ask_inside_slippage_budget:
        size_at_depth_limit = _size_at_execution_price_boundary(
            p_posterior=float(decision.edge.p_posterior),
            entry_price=depth_sweep_limit_float,
            fee_rate=taker_fee_rate,
            sizing_bankroll=sizing_bankroll,
            kelly_multiplier=kelly_multiplier,
        )
        if size_at_depth_limit <= 0.0:
            final_best_ask = None
        else:
            best_ask_sweep = simulate_clob_sweep(
                snapshot=snapshot,
                direction=str(decision.edge.direction),
                requested_size_kind="notional_usd",
                requested_size_value=Decimal(str(size_at_depth_limit)),
                limit_price=depth_sweep_limit_decimal,
            )
            if best_ask_sweep.depth_status != "PASS":
                raise ValueError(
                    "EXECUTABLE_TAKER_DEPTH_CONSTRAINED: "
                    f"visible_best_ask_usd={float(best_ask_sweep.gross_notional):.6f} "
                    f"required_usd={size_at_depth_limit:.6f}"
                )
            final_best_ask = depth_sweep_limit_float
            final_price = depth_sweep_limit_float
            repriced_size = size_at_depth_limit
            corrected_candidate_price = depth_sweep_limit_float
            corrected_candidate_expected_fill = float(best_ask_sweep.average_price or best_ask_float)
            corrected_candidate_size = size_at_depth_limit

    final_intent_context = final_intent_context or {}
    corrected_pricing_shadow = _attach_corrected_pricing_authority(
        decision=decision,
        snapshot=snapshot,
        candidate_limit_price=float(corrected_candidate_price),
        candidate_expected_fill_price_before_fee=float(corrected_candidate_expected_fill),
        candidate_size_usd=float(corrected_candidate_size),
        order_type=str(final_intent_context.get("order_type") or "GTC"),
        cancel_after=final_intent_context.get("cancel_after"),
        resolution_window=str(final_intent_context.get("resolution_window") or "default"),
        correlation_key=str(final_intent_context.get("correlation_key") or ""),
    )
    if (
        isinstance(corrected_pricing_shadow, dict)
        and corrected_pricing_shadow.get("live_submit_authority") is True
    ):
        corrected_candidate_size = float(
            Decimal(str(corrected_pricing_shadow["candidate_size_usd"]))
        )
        repriced_size = corrected_candidate_size
    tokens = dict(getattr(decision, "tokens", {}) or {})

    decision.edge = replace(
        decision.edge,
        edge=repriced_edge,
        p_market=float(snapshot_vwmp),
        entry_price=float(snapshot_vwmp),
        vwmp=float(snapshot_vwmp),
        forward_edge=repriced_edge,
    )
    if getattr(decision, "edge_context", None) is not None:
        if is_dataclass(decision.edge_context):
            decision.edge_context = replace(decision.edge_context, forward_edge=repriced_edge)
        else:
            setattr(decision.edge_context, "forward_edge", repriced_edge)
    try:
        edge_context_payload = json.loads(getattr(decision, "edge_context_json", "") or "{}")
        if isinstance(edge_context_payload, dict):
            edge_context_payload["forward_edge"] = repriced_edge
            decision.edge_context_json = json.dumps(edge_context_payload, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        pass
    decision.size_usd = float(repriced_size)
    if "executable_snapshot_repriced" not in decision.applied_validations:
        decision.applied_validations.append("executable_snapshot_repriced")
    tokens["executable_snapshot_reprice"] = {
        "snapshot_id": snapshot_id,
        "entry_method": entry_method,
        "selected_method": selected_method,
        "raw_orderbook_hash": str(snapshot.raw_orderbook_hash or ""),
        "executable_snapshot_hash": str(snapshot.executable_snapshot_hash or ""),
        "selected_outcome_token_id": str(snapshot.selected_outcome_token_id or ""),
        "outcome_label": str(snapshot.outcome_label or ""),
        "old_entry_price": float(getattr(original_edge, "entry_price", 0.0) or 0.0),
        "old_vwmp": float(getattr(original_edge, "vwmp", 0.0) or 0.0),
        "old_size_usd": original_size,
        "snapshot_vwmp": float(snapshot_vwmp),
        "snapshot_best_bid": best_bid_float,
        "snapshot_best_bid_size": bid_size_float,
        "snapshot_best_ask": best_ask_float,
        "snapshot_best_ask_size": ask_size_float,
        "snapshot_limit_price": float(snapshot_limit_price),
        "slippage_reference_price": float(slippage_reference_price),
        "max_slippage_bps": float(max_slippage.value_bps),
        "depth_sweep_limit_price": depth_sweep_limit_float,
        "best_ask_slippage_bps": float(best_ask_slippage_bps),
        "best_ask_blocked_by_slippage": bool(
            best_ask_edge > 0.0
            and not best_ask_inside_slippage_budget
            and best_ask_slippage_bps > max_slippage.value_bps
        ),
        "best_ask_blocked_by_edge_boundary": bool(
            best_ask_edge > 0.0
            and not best_ask_inside_slippage_budget
            and best_ask_slippage_bps <= max_slippage.value_bps
        ),
        "final_limit_price": final_price,
        "final_best_ask": final_best_ask,
        "corrected_candidate_limit_price": float(corrected_candidate_price),
        "corrected_candidate_expected_fill_price": float(corrected_candidate_expected_fill),
        "corrected_candidate_size_usd": float(corrected_candidate_size),
        "repriced_edge": repriced_edge,
        "repriced_size_usd": float(repriced_size),
        "live_submit_authority": bool(corrected_pricing_shadow.get("live_submit_authority"))
        if isinstance(corrected_pricing_shadow, dict)
        else False,
        "final_execution_intent_id": corrected_pricing_shadow.get("final_execution_intent_id")
        if isinstance(corrected_pricing_shadow, dict)
        else None,
    }
    if corrected_pricing_shadow is not None:
        tokens["executable_snapshot_reprice"]["corrected_pricing_shadow"] = corrected_pricing_shadow
    decision.tokens = tokens
    return final_best_ask


def normalize_order_status(payload) -> str:
    if isinstance(payload, str):
        return payload.upper()
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
        if status is not None:
            return str(status).upper()
    return ""


def extract_float(payload: dict | None, *keys: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None


def pending_order_timed_out(pos, now: datetime) -> bool:
    deadline = parse_iso(pos.order_timeout_at)
    return deadline is not None and now >= deadline


def extract_order_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "orderID", "orderId"):
        if payload.get(key):
            return str(payload[key])
    return ""


def chain_positions_from_api(payload, *, ChainPosition):
    if payload is None:
        return None

    chain_positions = []
    for row in payload:
        token_id = row.get("token_id", "")
        if not token_id:
            continue
        try:
            chain_positions.append(
                ChainPosition(
                    token_id=token_id,
                    size=float(row.get("size", 0) or 0),
                    avg_price=float(row.get("avg_price", 0) or row.get("cur_price", 0) or 0),
                    cost=float(row.get("cost", 0) or 0),
                    condition_id=row.get("condition_id", ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return chain_positions


def run_chain_sync(portfolio, clob, conn=None, *, deps):
    api_positions = chain_positions_from_api(clob.get_positions_from_api(), ChainPosition=deps.ChainPosition)
    if api_positions is None:
        raise RuntimeError("chain sync returned None — API call succeeded but returned no data")
    return deps.reconcile_with_chain(portfolio, api_positions, conn=conn), True


def cleanup_orphan_open_orders(portfolio, clob, *, deps, conn=None) -> int:
    """Cancel exchange orders that are not tracked locally.

    Durable-command guard (#63 + R3):
      1. Order is NOT in local portfolio tracking (order_id / last_exit_order_id)
      2. Order is NOT in execution_fact (recent command log) within 2 hours
      3. Order has durable venue_commands truth; cancel through request_cancel_for_command
    """
    if not hasattr(clob, "get_open_orders"):
        return 0

    tracked_order_ids = set()
    for pos in portfolio.positions:
        if pos.order_id:
            tracked_order_ids.add(pos.order_id)
        if pos.last_exit_order_id:
            tracked_order_ids.add(pos.last_exit_order_id)

    # Build set of recently-commanded order IDs from trade_decisions
    recent_order_ids: set[str] = set()
    if conn is not None:
        try:
            from src.state.db import _table_exists
            if _table_exists(conn, "trade_decisions"):
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
                rows = conn.execute(
                    "SELECT order_id FROM trade_decisions WHERE order_posted_at >= ? AND order_id IS NOT NULL AND order_id != ''",
                    (cutoff,),
                ).fetchall()
                recent_order_ids = {str(r[0]) for r in rows}
        except Exception as exc:
            deps.logger.warning("Could not query trade_decisions for orphan guard: %s", exc)

    cancelled = 0
    for order in clob.get_open_orders():
        order_id = extract_order_id(order)
        if not order_id or order_id in tracked_order_ids:
            continue
        # Quarantine guard: if order appears in recent trade_decisions, do NOT cancel
        if order_id in recent_order_ids:
            deps.logger.warning(
                "Orphan order %s found in recent execution_fact — quarantining instead of cancelling",
                order_id,
            )
            continue
        if conn is None:
            deps.logger.warning(
                "Orphan open-order cleanup blocked for %s: missing durable venue command connection",
                order_id,
            )
            continue
        try:
            row = conn.execute(
                """
                SELECT command_id
                  FROM venue_commands
                 WHERE venue_order_id = ?
                 ORDER BY updated_at DESC, created_at DESC
                 LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        except Exception as exc:
            if "no such table" in str(exc).lower() and "venue_commands" in str(exc):
                deps.logger.warning(
                    "Orphan open-order cleanup blocked for %s: venue command journal unavailable",
                    order_id,
                )
                continue
            deps.logger.warning(
                "Orphan open-order cleanup blocked for %s: venue command lookup failed: %s",
                order_id,
                exc,
            )
            continue
        if row is None:
            deps.logger.warning(
                "Orphan open-order cleanup blocked for %s: missing durable venue command truth",
                order_id,
            )
            continue
        try:
            from src.execution.exit_safety import request_cancel_for_command

            command_id = row["command_id"] if hasattr(row, "keys") else row[0]
            outcome = request_cancel_for_command(
                conn,
                str(command_id),
                lambda venue_order_id: clob.cancel_order(venue_order_id),
            )
            if outcome.status == "CANCELED":
                cancelled += 1
        except Exception as exc:
            deps.logger.warning("Orphan open-order durable cancel failed for %s: %s", order_id, exc)
    return cancelled


def _summary_risk_level(summary: dict) -> str:
    return str(summary.get("risk_level") or "").strip().upper()


def _dedupe_steps(steps: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for step in steps:
        if step and step not in seen:
            seen.add(step)
            ordered.append(step)
    return ordered


def _orange_favorable_exit_decision(pos, exit_context, exit_decision):
    """Return an ORANGE exit only when authority and net execution are favorable."""

    if exit_decision.should_exit:
        return exit_decision
    if str(getattr(exit_decision, "reason", "") or "").startswith("INCOMPLETE_EXIT_CONTEXT"):
        return exit_decision
    missing_authority = getattr(exit_context, "missing_authority_fields", None)
    if callable(missing_authority) and missing_authority():
        return exit_decision
    try:
        best_bid = float(exit_context.best_bid)
        current_market_price = float(exit_context.current_market_price)
        shares = float(getattr(pos, "effective_shares", 0.0) or 0.0)
        cost_basis = float(getattr(pos, "effective_cost_basis_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        return exit_decision
    if not (
        math.isfinite(best_bid)
        and math.isfinite(current_market_price)
        and math.isfinite(shares)
        and math.isfinite(cost_basis)
        and shares > 0.0
        and cost_basis > 0.0
    ):
        return exit_decision

    from src.config import exit_fee_rate
    from src.contracts.execution_price import polymarket_fee
    from src.contracts.tick_size import TickSize
    from src.contracts.slippage_bps import SlippageBps

    token_id = getattr(pos, "token_id", "") if getattr(pos, "direction", "") == "buy_yes" else getattr(pos, "no_token_id", "")
    tick = TickSize.for_market(token_id=token_id).value
    base_exit_limit = current_market_price - tick
    planned_exit_price = base_exit_limit
    if best_bid < base_exit_limit:
        if current_market_price <= 0.0:
            return exit_decision
        slippage = SlippageBps(
            value_bps=abs(current_market_price - best_bid) / current_market_price * 10_000.0,
            direction="adverse",
        )
        if slippage.fraction > 0.03:
            return exit_decision
        planned_exit_price = best_bid

    if not (math.isfinite(planned_exit_price) and 0.0 < planned_exit_price < 1.0):
        return exit_decision
    clamped_fee_price = min(max(planned_exit_price, 1e-6), 1.0 - 1e-6)
    fee_per_share = polymarket_fee(clamped_fee_price, exit_fee_rate())
    net_exit_value = shares * (planned_exit_price - fee_per_share)
    if net_exit_value <= cost_basis:
        return exit_decision

    applied = list(getattr(exit_decision, "applied_validations", None) or getattr(pos, "applied_validations", []) or [])
    applied.extend(["risk_orange", "orange_favorable_bid_gate", "orange_favorable_net_exit_gate"])
    applied = _dedupe_steps(applied)
    return replace(
        exit_decision,
        should_exit=True,
        reason="ORANGE_FAVORABLE_EXIT",
        urgency="normal",
        trigger="ORANGE_FAVORABLE_EXIT",
        applied_validations=applied,
    )


def entry_bankroll_for_cycle(portfolio, clob, *, deps):
    # On-chain wallet balance is the SOLE bankroll truth source for live entry
    # sizing. Removed 2026-05-04: the prior config-literal cap truncation
    # hard-clipped the real wallet even when the venue returned a higher value,
    # producing the structural failure
    # documented in docs/operations/task_2026-05-01_bankroll_truth_chain/.
    # Bankroll fallback semantics now live entirely in
    # src.runtime.bankroll_provider (5-min stale-cache window); when the
    # provider/clob returns no usable value the cycle blocks new entries.
    try:
        balance = float(clob.get_balance())
    except Exception as exc:
        deps.logger.warning("Wallet balance fetch failed: %s", exc)
        return None, {
            "wallet_balance_usd": None,
            "dynamic_cap_usd": None,
            "entry_block_reason": "wallet_query_failed",
            "entry_bankroll_contract": "live_wallet_only",
            "bankroll_truth_source": "wallet_balance",
            "wallet_balance_used": True,
        }

    if balance <= 0.0:
        deps.logger.warning("Wallet balance $%.2f — blocking new entries.", balance)
        return None, {
            "wallet_balance_usd": balance,
            "dynamic_cap_usd": None,
            "entry_block_reason": "entry_bankroll_non_positive",
            "entry_bankroll_contract": "live_wallet_only",
            "bankroll_truth_source": "wallet_balance",
            "wallet_balance_used": True,
        }

    effective_bankroll = balance
    return effective_bankroll, {
        "wallet_balance_usd": balance,
        "dynamic_cap_usd": effective_bankroll,
        "entry_bankroll_contract": "live_wallet_only",
        "bankroll_truth_source": "wallet_balance",
        "wallet_balance_used": True,
    }


def materialize_position(candidate, decision, result, portfolio, city, mode, *, state: str, env: str, bankroll_at_entry=None, deps):
    # B097 [YELLOW / flag for §7c architect sign-off]: bankroll_at_entry
    # must be captured authoritatively at the point of entry. Falling back
    # to None (which previously propagated through to Position) corrupts
    # subsequent per-position P&L and size-reconstruction analytics. Reject
    # the materialization outright rather than synthesize a fake value.
    if bankroll_at_entry is None:
        raise ValueError(
            f"materialize_position: bankroll_at_entry is None for trade_id={getattr(result, 'trade_id', '?')!r} "
            f"state={state!r} env={env!r}; entry materialization requires an authoritative bankroll snapshot"
        )
    now = deps._utcnow()
    reported_fill_price = float(result.fill_price or 0.0)
    submitted_limit_price = float(result.submitted_price or 0.0)
    fallback_edge_price = float(decision.edge.entry_price or 0.0)
    corrected_shadow = {}
    try:
        corrected_shadow = (
            decision.tokens.get("executable_snapshot_reprice", {})
            .get("corrected_pricing_shadow", {})
        )
    except AttributeError:
        corrected_shadow = {}
    pricing_semantics_version = str(
        corrected_shadow.get("pricing_semantics_version")
        or "legacy_unclassified"
    )
    command_state = str(getattr(result, "command_state", "") or "")
    fill_has_finality = command_state == "FILLED"
    fill_price = reported_fill_price
    if reported_fill_price > 0 and not fill_has_finality:
        logger.warning(
            "materialize_position: ignoring non-final fill_price for trade_id=%s "
            "command_state=%s status=%s",
            getattr(result, "trade_id", ""),
            command_state or "missing",
            getattr(result, "status", ""),
        )
        fill_price = 0.0
    submitted_price_basis = submitted_limit_price or fallback_edge_price
    submitted_shares = float(
        result.shares
        or (
            decision.size_usd / submitted_price_basis
            if submitted_price_basis > 0
            else 0.0
        )
    )
    target_notional_usd = float(decision.size_usd or 0.0)
    submitted_notional_usd = (
        submitted_shares * submitted_limit_price
        if submitted_limit_price > 0 and submitted_shares > 0
        else 0.0
    )
    if fill_price > 0 and submitted_shares > 0 and fill_has_finality:
        entry_price = fill_price
        shares = submitted_shares
        cost_basis_usd = shares * fill_price
        entry_price_avg_fill = fill_price
        shares_filled = shares
        filled_cost_basis_usd = cost_basis_usd
        shares_remaining = 0.0
        entry_economics_authority = ENTRY_ECONOMICS_AVG_FILL_PRICE
        fill_authority = FILL_AUTHORITY_VENUE_CONFIRMED_FULL
        corrected_executable_economics_eligible = (
            pricing_semantics_version == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION
        )
    else:
        entry_price = 0.0
        shares = 0.0
        cost_basis_usd = 0.0
        entry_price_avg_fill = 0.0
        shares_filled = 0.0
        filled_cost_basis_usd = 0.0
        shares_remaining = submitted_shares
        entry_economics_authority = (
            ENTRY_ECONOMICS_SUBMITTED_LIMIT
            if submitted_limit_price > 0
            else ENTRY_ECONOMICS_MODEL_EDGE_PRICE
        )
        fill_authority = FILL_AUTHORITY_NONE
        corrected_executable_economics_eligible = False
    timeout_at = ""
    if result.timeout_seconds:
        timeout_at = (now + timedelta(seconds=result.timeout_seconds)).isoformat()
    edge_source = decision.edge_source or deps._classify_edge_source(mode, decision.edge)
    strategy_key = _resolve_strategy_key(decision)
    if not strategy_key:
        raise ValueError("missing or invalid strategy_key on decision")

    return deps.Position(
        trade_id=result.trade_id,
        market_id=decision.tokens["market_id"],
        city=city.name,
        cluster=city.cluster,
        target_date=candidate.target_date,
        bin_label=decision.edge.bin.label,
        direction=decision.edge.direction,
        size_usd=decision.size_usd,
        entry_price=entry_price,
        p_posterior=decision.edge.p_posterior,
        edge=decision.edge.edge,
        shares=shares,
        cost_basis_usd=cost_basis_usd,
        target_notional_usd=target_notional_usd,
        submitted_notional_usd=submitted_notional_usd,
        filled_cost_basis_usd=filled_cost_basis_usd,
        entry_price_submitted=submitted_limit_price,
        entry_price_avg_fill=entry_price_avg_fill,
        shares_submitted=submitted_shares,
        shares_filled=shares_filled,
        shares_remaining=shares_remaining,
        entry_cost_basis_id=str(corrected_shadow.get("cost_basis_id") or ""),
        entry_cost_basis_hash=str(corrected_shadow.get("cost_basis_hash") or ""),
        entry_economics_authority=entry_economics_authority,
        fill_authority=fill_authority,
        pricing_semantics_version=pricing_semantics_version,
        execution_cost_basis_version=str(
            corrected_shadow.get("execution_cost_basis_version")
            or corrected_shadow.get("cost_basis_id")
            or ""
        ),
        corrected_executable_economics_eligible=corrected_executable_economics_eligible,
        bankroll_at_entry=bankroll_at_entry,
        entered_at=now.isoformat() if state == "entered" else "",
        entry_ci_width=max(0.0, decision.edge.ci_upper - decision.edge.ci_lower),
        unit=city.settlement_unit,
        token_id=decision.tokens["token_id"],
        no_token_id=decision.tokens["no_token_id"],
        strategy_key=strategy_key,
        strategy=strategy_key,
        edge_source=edge_source,
        discovery_mode=mode.value,
        market_hours_open=candidate.hours_since_open,
        decision_snapshot_id=decision.decision_snapshot_id,
        entry_method=decision.selected_method,
        selected_method=decision.selected_method,
        applied_validations=list(decision.applied_validations),
        settlement_semantics_json=decision.settlement_semantics_json,
        epistemic_context_json=decision.epistemic_context_json,
        edge_context_json=decision.edge_context_json,
        state=state,
        order_id=result.order_id or "",
        entry_order_id=result.order_id or "",
        order_status=result.status,
        order_posted_at=now.isoformat() if state == "pending_tracked" else "",
        order_timeout_at=timeout_at,
        chain_state="local_only" if state == "pending_tracked" else "unknown",
        env=env,
        # Slice P2-fix3 (post-review C1 from critic, 2026-04-26): drop the
        # redundant `getattr(..., "high")` fallback. MarketCandidate is a
        # dataclass with `temperature_metric: str = "high"` default at
        # evaluator.py:91, so candidate.temperature_metric is always set
        # for valid MarketCandidate instances. Pre-fix the getattr fallback
        # silently HIGH-stamped non-MarketCandidate-shaped inputs (e.g.,
        # custom test fixtures, dynamic dicts) onto Position, after which
        # resolve_position_metric would falsely classify them VERIFIED.
        # Now: AttributeError raises if a non-MarketCandidate flows here.
        temperature_metric=candidate.temperature_metric,
        entry_model_agreement=getattr(decision, "agreement", "NOT_CHECKED"),
    )


def _emit_day0_window_entered_canonical_if_available(
    conn,
    pos,
    *,
    day0_entered_at: str,
    previous_phase: str,
    deps,
) -> bool:
    """Day0-canonical-event feature slice (2026-04-24): emit canonical
    DAY0_WINDOW_ENTERED event after a successful day0 transition
    (post-memory-mutation, post-update_trade_lifecycle persist).

    Pre-this-slice: cycle_runtime set pos.state='day0_window' + persisted
    via update_trade_lifecycle but never wrote a canonical position_events
    record. This helper lands one via build_day0_window_entered_canonical
    _write + append_many_and_project. Clears T1.c-followup L875 OBSOLETE_
    PENDING_FEATURE (test_day0_transition_emits_durable_lifecycle_event).

    Returns True on successful write, False on non-fatal skip (conn None
    or RuntimeError from canonical transaction schema absence — matches
    the pattern from _dual_write_canonical_entry_if_available).
    """
    if conn is None:
        return False

    from src.engine.lifecycle_events import build_day0_window_entered_canonical_write
    from src.state.db import append_many_and_project

    try:
        # Query next sequence_no for this position (same pattern as
        # fill_tracker._mark_entry_filled at src/execution/fill_tracker.py:156).
        # Position may already have POSITION_OPEN_INTENT / ENTRY_ORDER_POSTED /
        # ENTRY_ORDER_FILLED events (sequence_no 1-3); day0 event takes 4+.
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (getattr(pos, "trade_id", ""),),
        ).fetchone()
        next_seq = int((row[0] if row else 0) or 0) + 1
        events, projection = build_day0_window_entered_canonical_write(
            pos,
            day0_entered_at=day0_entered_at,
            sequence_no=next_seq,
            previous_phase=previous_phase,
            source_module="src.engine.cycle_runtime",
        )
        append_many_and_project(conn, events, projection)
    except RuntimeError as exc:
        deps.logger.warning(
            "CANONICAL_DAY0_EMIT_SKIPPED trade_id=%s reason=%s",
            pos.trade_id,
            exc,
        )
        return False

    return True


def _dual_write_canonical_entry_if_available(
    conn,
    pos,
    *,
    decision_id: str | None,
    deps,
    decision_evidence: DecisionEvidence | None = None,
) -> bool:
    # T4.1b 2026-04-23 (D4 Option E): `decision_evidence` threads through
    # to `build_entry_canonical_write` so the ENTRY_ORDER_POSTED payload
    # carries the `decision_evidence_envelope` sidecar for T4.2/Wave31
    # exit-side read-back via `json_extract(payload_json,
    # '$.decision_evidence_envelope')`. Remains None on paths that do not
    # originate from an accept-path `EdgeDecision` (e.g. test harnesses);
    # the payload simply omits the key, preserving pre-slice wire format.
    if conn is None:
        return False

    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    try:
        events, projection = build_entry_canonical_write(
            pos,
            decision_id=decision_id,
            source_module="src.engine.cycle_runtime",
            decision_evidence=decision_evidence,
        )
        append_many_and_project(conn, events, projection)
    except RuntimeError as exc:
        deps.logger.warning("CANONICAL_DUAL_WRITE_SKIPPED trade_id=%s reason=%s", pos.trade_id, exc)
        return False

    return True


def reconcile_pending_positions(portfolio, clob, tracker, *, deps):
    summary = {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False}
    from src.execution.fill_tracker import check_pending_entries

    stats = check_pending_entries(
        portfolio,
        clob,
        tracker,
        deps=deps,
    )
    summary["entered"] = int(stats.get("entered", 0) or 0)
    summary["voided"] = int(stats.get("voided", 0) or 0)
    summary["dirty"] = bool(stats.get("dirty", False) or summary["entered"] or summary["voided"])
    summary["tracker_dirty"] = bool(stats.get("tracker_dirty", False) or summary["entered"])
    return summary


def _apply_acknowledged_quarantine_clears(portfolio, summary: dict, *, deps, conn=None) -> bool:
    portfolio_dirty = False
    for pos in list(portfolio.positions):
        if _position_chain_state_value(pos) not in {"quarantined", "quarantine_expired"}:
            continue
        token_id = pos.token_id if _position_direction_value(pos) != "buy_no" else pos.no_token_id
        if not token_id:
            continue
        if token_id in getattr(portfolio, "ignored_tokens", []):
            continue
        if not deps.has_acknowledged_quarantine_clear(token_id):
            continue
        result = deps.record_token_suppression(
            conn,
            token_id=token_id,
            condition_id=getattr(pos, "condition_id", ""),
            suppression_reason="operator_quarantine_clear",
            source_module="src.engine.cycle_runtime",
            evidence={"trade_id": getattr(pos, "trade_id", "")},
        )
        if result.get("status") != "written":
            summary["operator_clears_suppression_failed"] = (
                summary.get("operator_clears_suppression_failed", 0) + 1
            )
            deps.logger.warning(
                "Quarantine clear for %s was acknowledged but token suppression was not persisted: %s",
                token_id,
                result,
            )
            continue
        portfolio.ignored_tokens.append(token_id)
        summary["operator_clears_applied"] = summary.get("operator_clears_applied", 0) + 1
        portfolio_dirty = True
    return portfolio_dirty


def _semantic_value(value) -> str:
    return str(getattr(value, "value", value) or "")


def _position_state_value(pos) -> str:
    return _semantic_value(getattr(pos, "state", ""))


def _position_chain_state_value(pos) -> str:
    return _semantic_value(getattr(pos, "chain_state", ""))


def _position_direction_value(pos) -> str:
    return _semantic_value(getattr(pos, "direction", ""))


def _is_open_crowding_exposure(pos) -> bool:
    state_value = _position_state_value(pos)
    chain_state = _position_chain_state_value(pos)
    if state_value in INACTIVE_RUNTIME_STATES:
        return False
    if state_value in {"pending_entry", "pending_tracked"}:
        return False
    if chain_state in {"quarantined", "quarantine_expired"}:
        return False
    return True


def _finite_float_or_none(value):
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    return value_f if math.isfinite(value_f) else None


def _current_monitor_result_probability_and_edge(pos, edge_ctx=None) -> tuple[float | None, float | None]:
    """Return current-cycle monitor probability/edge only when authority is fresh."""

    if edge_ctx is None or not bool(getattr(pos, "last_monitor_prob_is_fresh", False)):
        return None, None
    fresh_prob = _finite_float_or_none(getattr(edge_ctx, "p_posterior", None))
    fresh_edge = _finite_float_or_none(getattr(edge_ctx, "forward_edge", None))
    if fresh_edge is None:
        fresh_edge = _finite_float_or_none(getattr(pos, "last_monitor_edge", None))
    if fresh_prob is None:
        return None, None
    return fresh_prob, fresh_edge


def _build_exit_context(
    pos,
    edge_ctx,
    *,
    hours_to_settlement,
    ExitContext,
    portfolio=None,
):
    if False:
        _ = pos.entry_method
        _ = pos.selected_method
    p_market = None
    if getattr(edge_ctx, "p_market", None) is not None and len(edge_ctx.p_market) > 0:
        # Bug #64: edge_ctx.p_market from monitor_refresh is single-element
        # [held_bin_price], so index 0 is correct here. The held_bin_index
        # routing happens in monitor_refresh._build_all_bins.
        p_market = float(edge_ctx.p_market[0])
    elif getattr(pos, "last_monitor_market_price", None) is not None:
        p_market = float(pos.last_monitor_market_price)

    best_bid = getattr(pos, "last_monitor_best_bid", None)

    position_state = _position_state_value(pos)

    # T6.4-phase2 (2026-04-24): thread portfolio context so
    # HoldValue.compute_with_exit_costs can compute correlation-crowding
    # cost over other held positions. Exclude self from the tuple; each
    # element is (cluster, effective_cost_basis_usd, trade_id). When portfolio
    # is None, falls back to empty tuple / None bankroll; downstream treats
    # that as "no co-held positions, correlation_crowding=0".
    portfolio_positions: tuple = ()
    bankroll = None
    if portfolio is not None:
        try:
            bankroll = float(getattr(portfolio, "bankroll", None) or 0.0) or None
        except (TypeError, ValueError):
            bankroll = None
        others = getattr(portfolio, "positions", None) or ()
        portfolio_positions = tuple(
            (str(p.cluster), float(getattr(p, "effective_cost_basis_usd", 0.0) or 0.0), str(p.trade_id))
            for p in others
            if getattr(p, "trade_id", None) != getattr(pos, "trade_id", None)
            and _is_open_crowding_exposure(p)
        )

    return ExitContext(
        fresh_prob=float(edge_ctx.p_posterior) if getattr(edge_ctx, "p_posterior", None) is not None else None,
        fresh_prob_is_fresh=bool(getattr(pos, "last_monitor_prob_is_fresh", False)),
        current_market_price=p_market,
        current_market_price_is_fresh=bool(getattr(pos, "last_monitor_market_price_is_fresh", False)),
        best_bid=best_bid,
        best_ask=getattr(pos, "last_monitor_best_ask", None),
        market_vig=getattr(pos, "last_monitor_market_vig", None),
        hours_to_settlement=hours_to_settlement,
        position_state=position_state,
        day0_active=position_state == "day0_window",
        whale_toxicity=getattr(pos, "last_monitor_whale_toxicity", None),
        chain_is_fresh=pos.chain_state == "synced",
        divergence_score=float(getattr(edge_ctx, "divergence_score", 0.0) or 0.0),
        market_velocity_1h=float(getattr(edge_ctx, "market_velocity_1h", 0.0) or 0.0),
        portfolio_positions=portfolio_positions,
        bankroll=bankroll,
    )


def _execution_stub(candidate, decision, result, city, mode, *, deps):
    edge_source = decision.edge_source or deps._classify_edge_source(mode, decision.edge)
    strategy_key = _resolve_strategy_key(decision)
    return SimpleNamespace(
        trade_id=result.trade_id,
        market_id=decision.tokens["market_id"],
        city=city.name,
        target_date=candidate.target_date,
        bin_label=decision.edge.bin.label,
        direction=decision.edge.direction,
        strategy_key=strategy_key,
        strategy=strategy_key,
        edge_source=edge_source,
        decision_snapshot_id=decision.decision_snapshot_id,
        order_id=result.order_id or "",
        order_status=result.status,
        order_posted_at="",
        entered_at="",
        chain_state="",
        fill_quality=None,
    )



def execute_monitoring_phase(conn, clob, portfolio, artifact, tracker, summary: dict, *, deps):
    from src.engine.monitor_refresh import refresh_position
    from src.execution.exit_lifecycle import (
        ExitContext,
        build_exit_intent,
        check_pending_exits,
        check_pending_retries,
        execute_exit,
        handle_exit_pending_missing,
        is_exit_cooldown_active,
    )
    from src.state.chain_reconciliation import quarantine_resolution_reason

    portfolio_dirty = _apply_acknowledged_quarantine_clears(
        portfolio,
        summary,
        deps=deps,
        conn=conn,
    )
    tracker_dirty = False

    exit_stats = check_pending_exits(portfolio, clob, conn=conn)
    if exit_stats["filled"] or exit_stats["retried"]:
        portfolio_dirty = True

    for filled_pos in exit_stats.get("filled_positions", []):
        artifact.add_exit(
            filled_pos.trade_id,
            filled_pos.exit_reason or "DEFERRED_SELL_FILL",
            filled_pos.exit_price or 0.0,
            "sell_filled",
        )
        tracker.record_exit(filled_pos)
        tracker_dirty = True

    summary["pending_exits_filled"] = exit_stats["filled"]
    summary["pending_exits_retried"] = exit_stats["retried"]

    for pos in list(portfolio.positions):
        if pos.state == "pending_tracked":
            continue
        if False:
            _ = pos.entry_method
            _ = pos.selected_method
        pending_exit_resolution = handle_exit_pending_missing(portfolio, pos)
        if pending_exit_resolution["action"] == "closed":
            closed = pending_exit_resolution["position"]
            if closed is not None:
                tracker.record_exit(closed)
                tracker_dirty = True
                portfolio_dirty = True
                summary["exit_chain_missing_closed"] = summary.get("exit_chain_missing_closed", 0) + 1
            continue
        if pending_exit_resolution["action"] == "skip":
            summary["monitor_skipped_exit_pending_missing"] = summary.get("monitor_skipped_exit_pending_missing", 0) + 1
            continue
        if pos.state == "economically_closed":
            summary["monitor_skipped_economic_close"] = summary.get("monitor_skipped_economic_close", 0) + 1
            continue
        if pos.state == "admin_closed":
            summary["monitor_skipped_admin_close"] = summary.get("monitor_skipped_admin_close", 0) + 1
            continue
        if pos.state == "pending_exit":
            if pos.exit_state == "backoff_exhausted":
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                continue
            if is_exit_cooldown_active(pos):
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                continue
            check_pending_retries(pos, conn=conn)
            if pos.state == "pending_exit":
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                continue
        if pos.exit_state in ("sell_placed", "sell_pending"):
            continue
        if pos.exit_state == "backoff_exhausted":
            continue
        if is_exit_cooldown_active(pos):
            continue

        check_pending_retries(pos, conn=conn)

        if (
            _position_state_value(pos) == "quarantined"
            or _position_chain_state_value(pos) in {"quarantined", "quarantine_expired"}
        ):
            if not pos.admin_exit_reason:
                pos.admin_exit_reason = quarantine_resolution_reason(_position_chain_state_value(pos))
                pos.exit_reason = pos.admin_exit_reason
                pos.last_exit_at = deps._utcnow().isoformat() if hasattr(deps, "_utcnow") else datetime.now(timezone.utc).isoformat()
                portfolio_dirty = True
                summary["quarantine_resolution_marked"] = summary.get("quarantine_resolution_marked", 0) + 1
            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=None,
                    fresh_edge=None,
                    should_exit=False,
                    exit_reason=pos.admin_exit_reason,
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            summary["monitor_skipped_quarantine_resolution"] = summary.get("monitor_skipped_quarantine_resolution", 0) + 1
            continue

        if _position_direction_value(pos) not in {"buy_yes", "buy_no"}:
            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=None,
                    fresh_edge=None,
                    should_exit=False,
                    exit_reason="UNKNOWN_DIRECTION",
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            summary["monitor_skipped_unknown_direction"] = summary.get("monitor_skipped_unknown_direction", 0) + 1
            continue

        # K1/#49: belt-and-suspenders guard — quarantine placeholders must not
        # reach monitor_refresh where cities_by_name lookup would fail.
        if getattr(pos, 'is_quarantine_placeholder', False):
            logger.warning("Quarantine placeholder %s reached monitor loop — skipping", pos.trade_id)
            summary["monitor_skipped_quarantine_placeholder"] = summary.get("monitor_skipped_quarantine_placeholder", 0) + 1
            continue

        hours_to_settlement = None
        monitor_result_written = False
        try:
            city = deps.cities_by_name.get(pos.city)
            if city is not None:
                _now_utc = deps._utcnow()
                hours_to_settlement = lead_hours_to_settlement_close(
                    pos.target_date,
                    city.timezone,
                    _now_utc,
                )
                # P4 site 1 of 2 (PLAN_v3 §6.P4 D-A two-clock unification).
                # Flag ON (default post-A6 2026-05-04): respect the
                # position's market-phase axis A — DAY0_WINDOW transition
                # fires when the market is in MarketPhase.SETTLEMENT_DAY
                # (city-local 00:00 of target_date through Polymarket
                # endDate 12:00 UTC). The wider window matches operator
                # framing "all 24 hours before midnight of the local
                # market" and closes the legacy bug where west-of-UTC
                # cities fired DAY0_WINDOW AFTER Polymarket trading
                # already closed.
                # Flag OFF (ZEUS_MARKET_PHASE_DISPATCH=0): byte-equal
                # legacy ``hours_to_settlement <= 6.0`` anchored on
                # city-local end-of-target_date — kept as escape hatch
                # for legacy fixtures and rollback.
                from src.engine.dispatch import should_enter_day0_window
                _enter_day0 = should_enter_day0_window(
                    target_date_str=pos.target_date,
                    city_timezone=city.timezone,
                    decision_time_utc=_now_utc,
                    legacy_hours_to_settlement=hours_to_settlement,
                    legacy_threshold_hours=6.0,
                )
                if (_enter_day0
                        and _position_state_value(pos) in {"entered", "holding"}
                        and not getattr(pos, "exit_state", "")):
                    new_state = enter_day0_window_runtime_state(
                        pos.state,
                        exit_state=getattr(pos, "exit_state", ""),
                        chain_state=getattr(pos, "chain_state", ""),
                    )
                    new_day0_entered_at = pos.day0_entered_at or deps._utcnow().isoformat()
                    # Day0-canonical-event slice 2026-04-24: capture
                    # pre-transition phase so the canonical event records
                    # the actual lifecycle transition (not just "from
                    # active" default).
                    previous_phase_str = "active" if pos.state == "holding" else "active"
                    # Persist FIRST, then update memory (avoid split-brain)
                    if conn is not None:
                        try:
                            from src.state.db import update_trade_lifecycle
                            # Temporarily set fields for persistence
                            old_state = pos.state
                            old_day0 = pos.day0_entered_at
                            pos.state = new_state
                            pos.day0_entered_at = new_day0_entered_at
                            update_trade_lifecycle(conn=conn, pos=pos)
                        except Exception as exc:
                            # Revert memory to pre-transition state
                            pos.state = old_state
                            pos.day0_entered_at = old_day0
                            deps.logger.warning(
                                "Day0 transition ABORTED for %s: persist failed: %s",
                                pos.trade_id,
                                exc,
                            )
                            continue
                    else:
                        pos.state = new_state
                        pos.day0_entered_at = new_day0_entered_at
                    portfolio_dirty = True
                    # Day0-canonical-event slice 2026-04-24: emit typed
                    # DAY0_WINDOW_ENTERED event post-transition. Clears
                    # T1.c-followup L875 OBSOLETE_PENDING_FEATURE.
                    # Non-fatal: if canonical schema absent or write fails,
                    # logs warning but does not abort the cycle.
                    _emit_day0_window_entered_canonical_if_available(
                        conn,
                        pos,
                        day0_entered_at=new_day0_entered_at,
                        previous_phase=previous_phase_str,
                        deps=deps,
                    )

            edge_ctx = refresh_position(conn, clob, pos)
            exit_context = _build_exit_context(
                pos,
                edge_ctx,
                hours_to_settlement=hours_to_settlement,
                ExitContext=ExitContext,
                portfolio=portfolio,
            )
            p_market = exit_context.current_market_price
            portfolio_dirty = True
            exit_decision = pos.evaluate_exit(exit_context)
            if _summary_risk_level(summary) == "ORANGE":
                orange_decision = _orange_favorable_exit_decision(
                    pos,
                    exit_context,
                    exit_decision,
                )
                if orange_decision.should_exit and not exit_decision.should_exit:
                    pos.applied_validations = list(orange_decision.applied_validations)
                    summary["risk_orange_favorable_exits"] = (
                        summary.get("risk_orange_favorable_exits", 0) + 1
                    )
                elif not exit_decision.should_exit:
                    summary["risk_orange_holds"] = summary.get("risk_orange_holds", 0) + 1
                exit_decision = orange_decision
            should_exit = exit_decision.should_exit
            exit_reason = exit_decision.reason
            if should_exit:
                exit_trigger = exit_decision.trigger or exit_reason
                gate_allowed, gate_reason = _exit_evidence_gate_allows_statistical_exit(
                    conn=conn,
                    pos=pos,
                    exit_trigger=exit_trigger,
                    summary=summary,
                    deps=deps,
                )
                if not gate_allowed:
                    should_exit = False
                    exit_reason = gate_reason or "INCOMPLETE_EXIT_EVIDENCE"
            if exit_reason.startswith("INCOMPLETE_EXIT_CONTEXT"):
                summary["monitor_incomplete_exit_context"] = summary.get("monitor_incomplete_exit_context", 0) + 1
                if hours_to_settlement is not None and hours_to_settlement <= 6.0:
                    summary["monitor_chain_missing"] = summary.get("monitor_chain_missing", 0) + 1
                    summary.setdefault("monitor_chain_missing_positions", []).append(pos.trade_id)
                    summary.setdefault("monitor_chain_missing_reasons", []).append(
                        {
                            "position_id": pos.trade_id,
                            "reason": f"incomplete_exit_context:{exit_reason}",
                        }
                    )
                deps.logger.warning(
                    "Exit authority incomplete for %s: %s",
                    pos.trade_id,
                    exit_reason,
                )

            monitor_fresh_prob, monitor_fresh_edge = _current_monitor_result_probability_and_edge(pos, edge_ctx)
            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=monitor_fresh_prob,
                    fresh_edge=monitor_fresh_edge,
                    should_exit=should_exit,
                    exit_reason=exit_reason,
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            monitor_result_written = True
            summary["monitors"] += 1

            if should_exit:
                pos.exit_trigger = exit_decision.trigger or exit_reason
                pos.exit_reason = exit_reason
                pos.exit_divergence_score = edge_ctx.divergence_score
                pos.exit_market_velocity_1h = edge_ctx.market_velocity_1h
                pos.exit_forward_edge = edge_ctx.forward_edge
                exit_intent = build_exit_intent(
                    pos,
                    replace(exit_context, exit_reason=exit_reason),
                )
                outcome = execute_exit(
                    portfolio=portfolio,
                    position=pos,
                    exit_context=replace(exit_context, exit_reason=exit_reason),
                    clob=clob,
                    conn=conn,
                    exit_intent=exit_intent,
                )
                if outcome.startswith("exit_filled:"):
                    tracker.record_exit(pos)
                    tracker_dirty = True
                summary["exits"] += 1
                portfolio_dirty = True
        except Exception as e:
            deps.logger.error("Monitor failed for %s: %s", pos.trade_id, e, exc_info=True)
            summary["monitor_failed"] = summary.get("monitor_failed", 0) + 1
            reason_prefix = "time_context_failed" if hours_to_settlement is None else f"refresh_failed:{e.__class__.__name__}"
            if hours_to_settlement is None:
                try:
                    city = deps.cities_by_name.get(pos.city)
                    if city is not None:
                        lead_hours_to_settlement_close(pos.target_date, city.timezone, deps._utcnow())
                except Exception:
                    reason_prefix = f"time_context_failed:{e.__class__.__name__}"
            near_settlement = (
                hours_to_settlement is None
                or hours_to_settlement <= 6.0
                or _position_state_value(pos) in {"day0_window", "pending_exit"}
            )
            if near_settlement and not monitor_result_written and "execution failed" not in str(e).lower():
                summary["monitor_chain_missing"] = summary.get("monitor_chain_missing", 0) + 1
                summary.setdefault("monitor_chain_missing_positions", []).append(pos.trade_id)
                summary.setdefault("monitor_chain_missing_reasons", []).append(
                    {"position_id": pos.trade_id, "reason": reason_prefix}
                )
                artifact.add_monitor_result(
                    deps.MonitorResult(
                        position_id=pos.trade_id,
                        fresh_prob=None,
                        fresh_edge=None,
                        should_exit=False,
                        exit_reason=f"MONITOR_CHAIN_MISSING:{reason_prefix}",
                        neg_edge_count=pos.neg_edge_count,
                    )
                )

    return portfolio_dirty, tracker_dirty


def fetch_day0_observation(city, target_date: str, decision_time, *, deps):
    getter = deps.get_current_observation
    try:
        return getter(city, target_date=target_date, reference_time=decision_time)
    except TypeError:
        return getter(city)


def _availability_status_for_exception(exc: Exception) -> str:
    name = exc.__class__.__name__
    text = str(exc).lower()
    if "429" in text or "rate" in text or "limit" in text:
        return "RATE_LIMITED"
    if name == "MissingCalibrationError":
        return "DATA_STALE"
    if name == "ObservationUnavailableError":
        return "DATA_UNAVAILABLE"
    if "chain" in text:
        return "CHAIN_UNAVAILABLE"
    return "DATA_UNAVAILABLE"


def execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time, *, env: str, deps):
    portfolio_dirty = False
    tracker_dirty = False
    market_candidate_ctor = getattr(deps, "MarketCandidate", None)
    if market_candidate_ctor is None:
        from src.engine.evaluator import MarketCandidate as market_candidate_ctor
    # Slice P3-fix1b (post-review side-fix, 2026-04-26): _normalize_
    # temperature_metric must be imported unconditionally — pre-fix
    # the import sat inside `if market_candidate_ctor is None:` so the
    # external-deps path (deps.MarketCandidate provided) left
    # _normalize_temperature_metric undefined when L1133 referenced it,
    # raising UnboundLocalError that the entry path caught and
    # auto-paused entries. P2-fix3 latent bug surfaced post-merge.
    from src.engine.evaluator import _normalize_temperature_metric

    # Oracle is a sizing modifier, not a truth gate. Refresh once per discovery
    # cycle so bridge writes are visible without per-candidate disk I/O.
    oracle_penalty_reload = getattr(deps, "oracle_penalty_reload", None)
    if callable(oracle_penalty_reload):
        oracle_penalty_reload()

    def _record_opportunity_fact(candidate, decision, *, should_trade: bool, rejection_stage: str, rejection_reasons: list[str]):
        try:
            from src.state.db import log_opportunity_fact

            log_opportunity_fact(
                conn,
                candidate=candidate,
                decision=decision,
                should_trade=should_trade,
                rejection_stage=rejection_stage,
                rejection_reasons=rejection_reasons,
                recorded_at=decision_time.isoformat(),
            )
        except Exception as exc:
            deps.logger.warning(
                "Opportunity fact write failed for %s: %s",
                getattr(decision, "decision_id", ""),
                exc,
            )

    def _record_probability_trace(candidate, decision):
        try:
            from src.state.db import log_probability_trace_fact

            result = log_probability_trace_fact(
                conn,
                candidate=candidate,
                decision=decision,
                recorded_at=decision_time.isoformat(),
                mode=mode.value,
            )
            if result.get("status") != "written":
                deps.logger.warning(
                    "Probability trace not written for %s: %s",
                    getattr(decision, "decision_id", ""),
                    result.get("status"),
                )
        except Exception as exc:
            deps.logger.warning(
                "Probability trace write failed for %s: %s",
                getattr(decision, "decision_id", ""),
                exc,
            )

    def _availability_scope_key(*, candidate=None, city_name: str = "", target_date: str = "") -> str:
        if candidate is not None:
            event_id = str(getattr(candidate, "event_id", "") or "").strip()
            if event_id:
                return event_id
            slug = str(getattr(candidate, "slug", "") or "").strip()
            if slug:
                return slug
            city_name = city_name or str(getattr(getattr(candidate, "city", None), "name", "") or "")
            target_date = target_date or str(getattr(candidate, "target_date", "") or "")
        if city_name and target_date:
            return f"{city_name}:{target_date}"
        return city_name or target_date or "unknown"

    def _availability_failure_type(status: str, reasons: list[str]) -> str:
        normalized = str(status or "").strip().upper()
        reason_text = " ".join(reasons).lower()
        if normalized == "RATE_LIMITED":
            return "rate_limited"
        if normalized == "CHAIN_UNAVAILABLE":
            return "chain_unavailable"
        if normalized == "DATA_STALE":
            return "data_stale"
        if "observation" in reason_text or "obs " in reason_text or reason_text.startswith("obs"):
            return "observation_missing"
        if "ens" in reason_text:
            return "ens_missing"
        return "data_unavailable"

    def _record_availability_fact(
        *,
        status: str,
        reasons: list[str],
        scope_type: str,
        scope_key: str,
        details: dict,
    ):
        normalized = str(status or "").strip().upper()
        if not normalized or normalized == "OK":
            return
        try:
            from src.state.db import log_availability_fact

            failure_type = _availability_failure_type(normalized, reasons)
            availability_id = ":".join(
                part
                for part in (
                    "availability",
                    scope_type,
                    scope_key,
                    decision_time.isoformat(),
                    failure_type,
                )
                if part
            )
            log_availability_fact(
                conn,
                availability_id=availability_id,
                scope_type=scope_type,
                scope_key=scope_key,
                failure_type=failure_type,
                started_at=decision_time.isoformat(),
                ended_at=decision_time.isoformat(),
                impact="skip",
                details=details,
            )
        except Exception as exc:
            deps.logger.warning("Availability fact write failed for %s: %s", scope_key, exc)

    def _market_scan_authority() -> str:
        getter = getattr(deps, "get_last_scan_authority", None)
        if not callable(getter):
            return "NEVER_FETCHED"
        try:
            return str(getter() or "NEVER_FETCHED").strip().upper()
        except Exception as exc:
            deps.logger.warning("Market scan authority read failed: %s", exc)
            return "EMPTY_FALLBACK"

    def _market_scan_availability_status(authority: str) -> str:
        if authority == "STALE":
            return "DATA_STALE"
        if authority == "EMPTY_FALLBACK":
            return "DATA_UNAVAILABLE"
        if authority == "NEVER_FETCHED":
            return "DATA_UNAVAILABLE"
        if authority != "VERIFIED":
            return "DATA_UNAVAILABLE"
        return ""

    def _record_forward_market_substrate(markets_to_record, authority: str) -> None:
        try:
            from src.state.db import log_forward_market_substrate

            result = log_forward_market_substrate(
                conn,
                markets=markets_to_record,
                recorded_at=decision_time.isoformat(),
                scan_authority=authority,
            )
        except Exception as exc:
            deps.logger.warning("Forward market substrate write failed: %s", exc)
            summary["forward_market_substrate_status"] = "error"
            summary["forward_market_substrate_error"] = str(exc)
            summary["degraded"] = True
            return

        status = str(result.get("status", "") or "")
        summary["forward_market_substrate_status"] = status
        for key in (
            "market_events_inserted",
            "market_events_unchanged",
            "market_events_conflicted",
            "price_rows_inserted",
            "price_rows_unchanged",
            "price_rows_conflicted",
            "markets_skipped_missing_facts",
            "outcomes_skipped_missing_facts",
            "prices_skipped_missing_facts",
            "outcomes_skipped_with_outcome_fact",
        ):
            if key in result:
                summary[f"forward_market_substrate_{key}"] = int(result.get(key) or 0)
        if status in {"written_with_conflicts", "skipped_invalid_schema"}:
            deps.logger.warning("Forward market substrate degraded: %s", result)
            summary["degraded"] = True

        try:
            from src.state.db import log_market_source_contract_topology_facts

            source_contract_result = log_market_source_contract_topology_facts(
                conn,
                markets=markets_to_record,
                recorded_at=decision_time.isoformat(),
                scan_authority=authority,
            )
        except Exception as exc:
            deps.logger.warning("Market source-contract topology write failed: %s", exc)
            summary["market_source_contract_topology_status"] = "error"
            summary["market_source_contract_topology_error"] = str(exc)
            summary["degraded"] = True
            return

        source_contract_status = str(source_contract_result.get("status", "") or "")
        summary["market_source_contract_topology_status"] = source_contract_status
        for key in (
            "topology_rows_written",
            "markets_skipped_missing_facts",
            "markets_skipped_source_contract_status",
            "outcomes_skipped_missing_facts",
        ):
            if key in source_contract_result:
                summary[f"market_source_contract_topology_{key}"] = int(
                    source_contract_result.get(key) or 0
                )
        if source_contract_status in {"skipped_missing_tables", "skipped_invalid_schema"}:
            deps.logger.warning(
                "Market source-contract topology degraded: %s", source_contract_result
            )
            summary["degraded"] = True

    def _execution_snapshot_fields(tokens: dict) -> dict:
        tokens = tokens or {}
        return {
            "executable_snapshot_id": str(
                tokens.get("executable_snapshot_id") or tokens.get("snapshot_id") or ""
            ).strip(),
            "executable_snapshot_min_tick_size": tokens.get(
                "executable_snapshot_min_tick_size",
                tokens.get("min_tick_size"),
            ),
            "executable_snapshot_min_order_size": tokens.get(
                "executable_snapshot_min_order_size",
                tokens.get("min_order_size"),
            ),
            "executable_snapshot_neg_risk": (
                tokens["executable_snapshot_neg_risk"]
                if "executable_snapshot_neg_risk" in tokens
                else tokens.get("neg_risk")
            ),
        }

    def _missing_execution_snapshot_fields(fields: dict) -> list[str]:
        missing = []
        if not fields["executable_snapshot_id"]:
            missing.append("executable_snapshot_id")
        if fields["executable_snapshot_min_tick_size"] is None:
            missing.append("executable_snapshot_min_tick_size")
        if fields["executable_snapshot_min_order_size"] is None:
            missing.append("executable_snapshot_min_order_size")
        if fields["executable_snapshot_neg_risk"] is None:
            missing.append("executable_snapshot_neg_risk")
        return missing

    params = deps.MODE_PARAMS[mode]
    min_hours_to_resolution = params.get("min_hours_to_resolution")
    if min_hours_to_resolution is None:
        min_hours_to_resolution = 0 if "max_hours_to_resolution" in params else 6
    markets = deps.find_weather_markets(min_hours_to_resolution=min_hours_to_resolution)
    if "max_hours_since_open" in params:
        markets = [m for m in markets if m["hours_since_open"] < params["max_hours_since_open"]]
    if "min_hours_since_open" in params:
        markets = [m for m in markets if m["hours_since_open"] >= params["min_hours_since_open"]]
    if "max_hours_to_resolution" in params:
        # P4 site 2 of 2 (PLAN_v3 §6.P4 D-A two-clock unification).
        # Flag ON (default post-A6 2026-05-04): replace legacy filter
        # with phase-axis SETTLEMENT_DAY membership (anchored at
        # city-local 00:00 of target_date for entry, 12:00 UTC of
        # target_date for exit per F1). Closes the D-A drift where
        # west-of-UTC cities had their DAY0_CAPTURE candidate window
        # open 18+h AFTER Polymarket trading already closed.
        # Flag OFF (ZEUS_MARKET_PHASE_DISPATCH=0): byte-equal legacy
        # filter on hours_to_resolution (anchored at UTC endDate − now
        # via market_scanner._parse_event) — escape hatch for rollback.
        from src.engine.dispatch import (
            filter_market_to_settlement_day,
            market_phase_dispatch_enabled,
        )
        # Critic R5 code-reviewer M1: stamp the flag state on the cycle
        # summary so downstream substrate / cohort attribution can
        # explain step-changes in candidate count when the operator
        # flips ZEUS_MARKET_PHASE_DISPATCH. Without this, the substrate
        # log shows only the post-filter count with no audit trail.
        flag_on = market_phase_dispatch_enabled()
        summary["market_phase_dispatch_flag"] = flag_on
        if flag_on:
            markets = [
                m for m in markets
                if filter_market_to_settlement_day(
                    market=m, decision_time_utc=decision_time
                )
            ]
        else:
            markets = [m for m in markets if m.get("hours_to_resolution") is not None and m["hours_to_resolution"] < params["max_hours_to_resolution"]]
    scan_authority = _market_scan_authority()
    summary["market_scan_authority"] = scan_authority
    _record_forward_market_substrate(markets, scan_authority)
    scan_availability_status = _market_scan_availability_status(scan_authority)
    if scan_availability_status:
        reasons = [f"market_scan_authority={scan_authority}"]
        if not markets:
            _record_availability_fact(
                status=scan_availability_status,
                reasons=reasons,
                scope_type="cycle",
                scope_key="gamma_active_events",
                details={
                    "mode": mode.value,
                    "market_scan_authority": scan_authority,
                    "availability_status": scan_availability_status,
                },
            )
        for market in markets:
            city = market.get("city")
            city_name = str(getattr(city, "name", "") or "")
            target_date = str(market.get("target_date", "") or "")
            scope_key = _availability_scope_key(
                city_name=city_name,
                target_date=target_date,
            )
            _record_availability_fact(
                status=scan_availability_status,
                reasons=reasons,
                scope_type="city_target",
                scope_key=scope_key,
                details={
                    "city": city_name,
                    "target_date": target_date,
                    "mode": mode.value,
                    "event_id": market.get("event_id", ""),
                    "slug": market.get("slug", ""),
                    "market_scan_authority": scan_authority,
                    "availability_status": scan_availability_status,
                },
            )
            artifact.add_no_trade(
                deps.NoTradeCase(
                    decision_id="",
                    city=city_name,
                    target_date=target_date,
                    range_label="",
                    direction="unknown",
                    rejection_stage="MARKET_FILTER",
                    strategy_key="",
                    strategy="",
                    edge_source="",
                    availability_status=scan_availability_status,
                    rejection_reasons=reasons,
                    bin_labels=[
                        outcome["title"]
                        for outcome in market.get("outcomes", [])
                        if not (outcome.get("range_low") is None and outcome.get("range_high") is None)
                    ],
                    market_hours_open=market.get("hours_since_open"),
                    timestamp=decision_time.isoformat(),
                )
            )
            summary["no_trades"] += 1
        return portfolio_dirty, tracker_dirty

    for market in markets:
        city = market.get("city")
        if city is None:
            continue
        parseable_labels = [
            outcome["title"]
            for outcome in market.get("outcomes", [])
            if not (outcome.get("range_low") is None and outcome.get("range_high") is None)
        ]

        # P2 stage 2 (PLAN_v3 §6.P2) — derive MarketPhase axis A from the
        # cycle's frozen decision_time BEFORE the obs gate so P3's
        # phase-based dispatch can read it. Errors are fail-soft: log +
        # leave market_phase=None so dispatch falls back to legacy mode.
        #
        # A5 (PLAN.md §A5): build the full MarketPhaseEvidence record
        # alongside the bare phase. The evidence carries phase_source
        # (verified_gamma / fallback_f1 / unknown) + the timestamps used,
        # so attribution writers can persist the determination provenance
        # and the A6 Kelly resolver can apply a 0.7× haircut on
        # fallback_f1. ``market_phase`` (bare enum) is kept for backward
        # compat with existing dispatch callsites; the evidence object is
        # the canonical post-A5 source.
        from src.strategy.market_phase_evidence import (
            from_market_dict as _build_market_phase_evidence,
        )
        from src.state.uma_resolution_listener import lookup_resolution as _lookup_uma

        # If a UMA resolution has been observed for this market, propagate
        # its tx hash so the evidence reports phase_source=onchain_resolved
        # (strictly stronger than heuristic POST_TRADING).
        uma_tx_hash = None
        try:
            condition_id = market.get("condition_id") or market.get("conditionId")
            if condition_id and conn is not None:
                resolved = _lookup_uma(conn, str(condition_id))
                if resolved is not None:
                    uma_tx_hash = resolved.tx_hash
        except Exception as exc:  # noqa: BLE001 - UMA lookup is observability-only
            deps.logger.warning(
                "UMA resolution lookup failed for %s: %s",
                market.get("condition_id") or market.get("conditionId"),
                exc,
            )

        market_phase_evidence = _build_market_phase_evidence(
            market=market,
            city_timezone=city.timezone,
            target_date_str=market.get("target_date", ""),
            decision_time_utc=decision_time,
            uma_resolved_source=uma_tx_hash,
        )
        market_phase = market_phase_evidence.phase
        if market_phase_evidence.phase_source == "unknown":
            deps.logger.warning(
                "MarketPhase tag failed for %s/%s: %s",
                city.name,
                market.get("target_date"),
                market_phase_evidence.failure_reason,
            )

        # P3 site 4 of 4 — observation-fetch gate (PLAN_v3 §6.P3).
        # Routed through the testable helper per critic R4 A7-M2 so the
        # contract has independent unit tests instead of being inlined.
        from src.engine.dispatch import should_fetch_settlement_day_observation
        should_fetch_observation = should_fetch_settlement_day_observation(
            mode=mode, market_phase=market_phase
        )

        try:
            obs = (
                fetch_day0_observation(city, market["target_date"], decision_time, deps=deps)
                if should_fetch_observation
                else None
            )
        except Exception as e:
            from src.contracts.exceptions import MissingCalibrationError, ObservationUnavailableError

            if isinstance(e, (ObservationUnavailableError, MissingCalibrationError)):
                deps.logger.warning("Skipping candidate for %s: %s", city.name, e)
                availability_status = _availability_status_for_exception(e)
                _record_availability_fact(
                    status=availability_status,
                    reasons=[str(e)],
                    scope_type="city_target",
                    scope_key=_availability_scope_key(city_name=city.name, target_date=market["target_date"]),
                    details={
                        "city": city.name,
                        "target_date": market["target_date"],
                        "mode": mode.value,
                        "availability_status": availability_status,
                        "failure_reason": str(e),
                        "event_id": market.get("event_id", ""),
                        "slug": market.get("slug", ""),
                    },
                )
                artifact.add_no_trade(
                    deps.NoTradeCase(
                        decision_id="",
                        city=city.name,
                        target_date=market["target_date"],
                        range_label="",
                        direction="unknown",
                        rejection_stage="SIGNAL_QUALITY",
                        strategy_key="",
                        strategy="",
                        edge_source="",
                        availability_status=availability_status,
                        rejection_reasons=[str(e)],
                        market_hours_open=market.get("hours_since_open"),
                        timestamp=decision_time.isoformat(),
                    )
                )
                summary["no_trades"] += 1
                continue
            raise

        # market_phase already computed above (used by both the obs-fetch
        # gate at P3 site 4 and the candidate tag below).
        candidate = market_candidate_ctor(
            city=city,
            target_date=market["target_date"],
            outcomes=market["outcomes"],
            hours_since_open=market["hours_since_open"],
            hours_to_resolution=market["hours_to_resolution"],
            # Slice P2-fix3 (post-review C1 from critic, 2026-04-26): route
            # through canonical normalizer (post-A3 raises on missing/invalid)
            # instead of double-defensive `... or "high"` silent default.
            # If market dict lacks temperature_metric, the scanner upstream
            # has a bug worth surfacing — fail loud rather than silently
            # stamping HIGH onto every LOW market.
            temperature_metric=_normalize_temperature_metric(
                market.get("temperature_metric")
            ).temperature_metric,
            event_id=market.get("event_id", ""),
            slug=market.get("slug", ""),
            observation=obs,
            discovery_mode=mode.value,
            market_phase=market_phase,
            # PR #56 review (Copilot + Codex P1, 2026-05-04): forward the
            # MarketPhaseEvidence provenance so evaluator's A6 phase-aware
            # Kelly resolver applies the right haircut (fallback_f1=0.7×).
            # Pre-fix evaluator hardcoded "verified_gamma" → systematic
            # over-sizing on Gamma payloads missing endDate.
            market_phase_source=market_phase_evidence.phase_source,
            phase_evidence=market_phase_evidence,
        )
        summary["candidates"] += 1

        try:
            # B091: forward the cycle's authoritative decision_time to the
            # evaluator so per-cycle `recorded_at` timestamps derive from
            # the cycle boundary rather than being silently re-fabricated
            # as `datetime.now()` inside the evaluator per-candidate.
            decisions = deps.evaluate_candidate(
                candidate, conn, portfolio, clob, limits,
                entry_bankroll=entry_bankroll,
                decision_time=decision_time,
            )
            # P2 (PLAN_v3 §6.P2 stage 2): stamp MarketPhase axis A onto
            # every returned EdgeDecision. evaluate_candidate has 30+
            # ``return [EdgeDecision(...)]`` sites; stamping at the call
            # site keeps the contract single-locus instead of threading
            # ``market_phase=`` into every return. The serialized
            # ``.value`` form is used so SQL/JSON downstream paths see a
            # uniform string regardless of caller.
            if decisions and candidate.market_phase is not None:
                _phase_value = candidate.market_phase.value
                for _d in decisions:
                    _d.market_phase = _phase_value
            if decisions:
                # Accumulate FDR health metrics into cycle summary
                if any(getattr(d, "fdr_fallback_fired", False) for d in decisions):
                    summary["fdr_fallback_fired"] = True
                family_sizes = [getattr(d, "fdr_family_size", 0) for d in decisions if getattr(d, "fdr_family_size", 0) > 0]
                if family_sizes:
                    summary["fdr_family_size"] = summary.get("fdr_family_size", 0) + family_sizes[0]
                for trace_decision in decisions:
                    _record_probability_trace(candidate, trace_decision)
                try:
                    from src.engine.time_context import lead_hours_to_date_start, lead_hours_to_settlement_close
                    from src.state.db import log_shadow_signal
                    first = decisions[0]
                    edges_payload = [
                        {
                            "decision_id": d.decision_id,
                            "should_trade": d.should_trade,
                            "direction": d.edge.direction if d.edge else "",
                            "bin_label": d.edge.bin.label if d.edge else "",
                            "edge": d.edge.edge if d.edge else 0.0,
                            "rejection_stage": d.rejection_stage,
                            "decision_snapshot_id": d.decision_snapshot_id,
                            "selected_method": d.selected_method,
                        }
                        for d in decisions
                    ]
                    log_shadow_signal(
                        conn,
                        city=city.name,
                        target_date=candidate.target_date,
                        timestamp=decision_time.isoformat(),
                        decision_snapshot_id=first.decision_snapshot_id,
                        p_raw_json=json.dumps(first.p_raw.tolist() if getattr(first, "p_raw", None) is not None else []),
                        p_cal_json=json.dumps(first.p_cal.tolist() if getattr(first, "p_cal", None) is not None else []),
                        edges_json=json.dumps(edges_payload),
                        lead_hours=float(lead_hours_to_date_start(date.fromisoformat(candidate.target_date), city.timezone, decision_time)),
                    )
                except Exception as exc:
                    deps.logger.error("telemetry write failed, cycle flagged degraded: %s", exc, exc_info=True)
                    summary["degraded"] = True
            for d in decisions:
                if False:
                    _ = d.calibration
                strategy_key = _resolve_strategy_key(d) if d.edge else ""
                if d.should_trade and d.edge and d.tokens:
                    if not strategy_key:
                        summary["no_trades"] += 1
                        rejection_stage = "SIGNAL_QUALITY"
                        rejection_reasons = ["strategy_key_unclassified"]
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy="",
                                strategy_key="",
                                edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=d.edge.entry_price if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=list(d.applied_validations),
                                bin_labels=parseable_labels,
                                p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                alpha=getattr(d, "alpha", 0.0),
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    strategy_name = strategy_key
                    phase_rejection_reason = _strategy_phase_rejection_reason(strategy_name, mode)
                    if phase_rejection_reason:
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        summary["no_trades"] += 1
                        summary["strategy_phase_rejections"] = summary.get("strategy_phase_rejections", 0) + 1
                        rejection_stage = "SIGNAL_QUALITY"
                        rejection_reasons = [phase_rejection_reason]
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy=strategy_name,
                                strategy_key=strategy_name,
                                edge_source=edge_source,
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=d.edge.entry_price if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=list(d.applied_validations),
                                bin_labels=parseable_labels,
                                p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                alpha=getattr(d, "alpha", 0.0),
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    if not deps.is_strategy_enabled(strategy_name):
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        summary["no_trades"] += 1
                        summary["strategy_gate_rejections"] = summary.get("strategy_gate_rejections", 0) + 1
                        rejection_stage = "RISK_REJECTED"
                        rejection_reasons = [f"strategy_gate_disabled:{strategy_name}"]
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy=strategy_name,
                                strategy_key=strategy_name,
                                edge_source=edge_source,
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=d.edge.entry_price if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=list(d.applied_validations),
                                bin_labels=parseable_labels,
                                p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                alpha=getattr(d, "alpha", 0.0),
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    if (
                        str(env or "").strip().lower() == "live"
                        and d.edge is not None
                        and d.edge.direction == "buy_no"
                    ):
                        from src.engine.evaluator import native_multibin_buy_no_live_enabled

                        try:
                            native_buy_no_live_enabled = native_multibin_buy_no_live_enabled()
                            live_flag_error = ""
                        except ValueError as exc:
                            native_buy_no_live_enabled = False
                            live_flag_error = str(exc)
                        if not native_buy_no_live_enabled:
                            buy_no_live_rejection_reason = (
                                live_flag_error
                                or "NATIVE_MULTIBIN_BUY_NO_LIVE_DISABLED"
                            )
                        else:
                            buy_no_live_rejection_reason = _native_buy_no_live_authorization_rejection_reason(
                                d,
                                strategy_name,
                                mode,
                            )
                        if buy_no_live_rejection_reason:
                            summary["no_trades"] += 1
                            rejection_stage = "RISK_REJECTED"
                            rejection_reasons = [buy_no_live_rejection_reason]
                            _record_opportunity_fact(
                                candidate,
                                d,
                                should_trade=False,
                                rejection_stage=rejection_stage,
                                rejection_reasons=rejection_reasons,
                            )
                            artifact.add_no_trade(
                                deps.NoTradeCase(
                                    decision_id=d.decision_id,
                                    city=city.name,
                                    target_date=candidate.target_date,
                                    range_label=d.edge.bin.label if d.edge else "",
                                    direction=d.edge.direction if d.edge else "",
                                    rejection_stage=rejection_stage,
                                    strategy=strategy_name,
                                    strategy_key=strategy_name,
                                    edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                    availability_status=getattr(d, "availability_status", ""),
                                    rejection_reasons=rejection_reasons,
                                    best_edge=d.edge.edge if d.edge else 0.0,
                                    model_prob=d.edge.p_posterior if d.edge else 0.0,
                                    market_price=d.edge.entry_price if d.edge else 0.0,
                                    decision_snapshot_id=d.decision_snapshot_id,
                                    selected_method=d.selected_method,
                                    settlement_semantics_json=d.settlement_semantics_json,
                                    epistemic_context_json=d.epistemic_context_json,
                                    edge_context_json=d.edge_context_json,
                                    applied_validations=list(d.applied_validations),
                                    bin_labels=parseable_labels,
                                    p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                    p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                    p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                    alpha=getattr(d, "alpha", 0.0),
                                    market_hours_open=candidate.hours_since_open,
                                    agreement=getattr(d, "agreement", ""),
                                    timestamp=decision_time.isoformat(),
                                )
                            )
                            continue
                    snapshot_fields = _execution_snapshot_fields(d.tokens)
                    missing_snapshot_fields = _missing_execution_snapshot_fields(snapshot_fields)
                    snapshot_capture_error = ""
                    if str(env or "").strip().lower() == "live" and missing_snapshot_fields:
                        capture_snapshot = getattr(deps, "capture_executable_market_snapshot", None)
                        if callable(capture_snapshot):
                            try:
                                captured_snapshot_fields = capture_snapshot(
                                    conn,
                                    market=market,
                                    decision=d,
                                    clob=clob,
                                    captured_at=datetime.now(timezone.utc),
                                    scan_authority=scan_authority,
                                )
                            except Exception as exc:
                                deps.logger.warning(
                                    "Executable market snapshot capture failed for %s %s %s: %s",
                                    city.name,
                                    candidate.target_date,
                                    d.edge.bin.label if d.edge else "",
                                    exc,
                                )
                                if not isinstance(d.tokens, dict):
                                    d.tokens = {}
                                snapshot_capture_error = str(exc)
                                d.tokens["executable_snapshot_capture_error"] = snapshot_capture_error
                            else:
                                if not isinstance(d.tokens, dict):
                                    d.tokens = {}
                                d.tokens.update(captured_snapshot_fields)
                                try:
                                    from src.state.db import log_executable_snapshot_market_price_linkage

                                    linkage_result = log_executable_snapshot_market_price_linkage(
                                        conn,
                                        snapshot_id=str(
                                            captured_snapshot_fields.get("executable_snapshot_id", "")
                                        ),
                                    )
                                    summary["forward_market_price_linkage_status"] = str(
                                        linkage_result.get("status", "")
                                    )
                                    if _forward_price_linkage_status_degraded(
                                        summary["forward_market_price_linkage_status"]
                                    ):
                                        summary["degraded"] = True
                                except Exception as exc:
                                    deps.logger.warning(
                                        "Executable snapshot price linkage write failed for %s %s %s: %s",
                                        city.name,
                                        candidate.target_date,
                                        d.edge.bin.label if d.edge else "",
                                        exc,
                                    )
                                    summary["forward_market_price_linkage_status"] = "error"
                                    summary["degraded"] = True
                                snapshot_fields = _execution_snapshot_fields(d.tokens)
                                missing_snapshot_fields = _missing_execution_snapshot_fields(snapshot_fields)
                                try:
                                    conn.commit()
                                except Exception as exc:
                                    deps.logger.warning(
                                        "Executable market snapshot commit failed for %s %s %s: %s",
                                        city.name,
                                        candidate.target_date,
                                        d.edge.bin.label if d.edge else "",
                                        exc,
                                    )
                                    snapshot_capture_error = f"executable_snapshot_commit_failed:{exc}"
                                    d.tokens["executable_snapshot_capture_error"] = snapshot_capture_error

                    if str(env or "").strip().lower() == "live" and (missing_snapshot_fields or snapshot_capture_error):
                        summary["no_trades"] += 1
                        rejection_stage = "EXECUTION_FAILED"
                        capture_error = snapshot_capture_error
                        if isinstance(getattr(d, "tokens", None), dict):
                            capture_error = capture_error or str(d.tokens.get("executable_snapshot_capture_error") or "")
                        rejection_reasons = []
                        if missing_snapshot_fields:
                            rejection_reasons.append(
                                "missing_executable_market_identity:" + ",".join(missing_snapshot_fields)
                            )
                        if capture_error:
                            rejection_reasons.append(f"executable_snapshot_capture_failed:{capture_error}")
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy=strategy_name,
                                strategy_key=strategy_name,
                                edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=d.edge.entry_price if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=list(d.applied_validations),
                                bin_labels=parseable_labels,
                                p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                alpha=getattr(d, "alpha", 0.0),
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    snapshot_best_ask = None
                    if str(env or "").strip().lower() == "live":
                        try:
                            mode_value = str(getattr(mode, "value", mode))
                            timeout_seconds = _mode_timeout_seconds(mode_value)
                            final_intent_context = {
                                "order_type": _select_final_submit_order_type(
                                    conn,
                                    snapshot_fields["executable_snapshot_id"],
                                    deps,
                                ),
                                "cancel_after": decision_time + timedelta(seconds=timeout_seconds),
                                "resolution_window": candidate.target_date,
                                "correlation_key": (
                                    f"{getattr(city, 'cluster', '') or city.name}:{candidate.target_date}"
                                ),
                            }
                            _reprice_fn = getattr(deps, "reprice_from_snapshot", None)
                            if callable(_reprice_fn):
                                snapshot_best_ask = _reprice_fn(conn, d, snapshot_fields, final_intent_context)
                            else:
                                snapshot_best_ask = _reprice_decision_from_executable_snapshot(
                                    conn,
                                    d,
                                    snapshot_fields,
                                    final_intent_context,
                                )
                        except Exception as exc:
                            summary["no_trades"] += 1
                            rejection_stage = "EXECUTION_FAILED"
                            rejection_reasons = [f"executable_snapshot_reprice_failed:{exc}"]
                            _record_opportunity_fact(
                                candidate,
                                d,
                                should_trade=False,
                                rejection_stage=rejection_stage,
                                rejection_reasons=rejection_reasons,
                            )
                            artifact.add_no_trade(
                                deps.NoTradeCase(
                                    decision_id=d.decision_id,
                                    city=city.name,
                                    target_date=candidate.target_date,
                                    range_label=d.edge.bin.label if d.edge else "",
                                    direction=d.edge.direction if d.edge else "",
                                    rejection_stage=rejection_stage,
                                    strategy=strategy_name,
                                    strategy_key=strategy_name,
                                    edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                    availability_status=getattr(d, "availability_status", ""),
                                    rejection_reasons=rejection_reasons,
                                    best_edge=d.edge.edge if d.edge else 0.0,
                                    model_prob=d.edge.p_posterior if d.edge else 0.0,
                                    market_price=d.edge.entry_price if d.edge else 0.0,
                                    decision_snapshot_id=d.decision_snapshot_id,
                                    selected_method=d.selected_method,
                                    settlement_semantics_json=d.settlement_semantics_json,
                                    epistemic_context_json=d.epistemic_context_json,
                                    edge_context_json=d.edge_context_json,
                                    applied_validations=list(d.applied_validations),
                                    bin_labels=parseable_labels,
                                    p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                    p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                    p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                    alpha=getattr(d, "alpha", 0.0),
                                    market_hours_open=candidate.hours_since_open,
                                    agreement=getattr(d, "agreement", ""),
                                    timestamp=decision_time.isoformat(),
                                )
                            )
                            continue
                    _record_opportunity_fact(
                        candidate,
                        d,
                        should_trade=True,
                        rejection_stage="",
                        rejection_reasons=[],
                    )
                    result = None
                    try:
                        is_live_env = str(env or "").strip().lower() == "live"
                        if is_live_env:
                            if not isinstance(getattr(d, "tokens", None), dict):
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING: decision tokens unavailable")
                            reprice_payload = d.tokens.get("executable_snapshot_reprice")
                            if not isinstance(reprice_payload, dict):
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING: reprice payload unavailable")
                            final_intent = getattr(d, "final_execution_intent", None)
                            shadow_payload = reprice_payload.get("corrected_pricing_shadow")
                            if reprice_payload.get("live_submit_authority") is not True:
                                unsupported_reason = None
                                if isinstance(shadow_payload, dict):
                                    unsupported_reason = shadow_payload.get("unsupported_reason")
                                raise ValueError(
                                    "FINAL_EXECUTION_INTENT_UNAVAILABLE:"
                                    f"{unsupported_reason or 'live_submit_authority_false'}"
                                )
                            if final_intent is None:
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING")
                            sweep_payload = reprice_payload.get("corrected_pricing_shadow")
                            if not isinstance(sweep_payload, dict):
                                sweep_payload = reprice_payload
                            try:
                                submitted_shares = Decimal(str(sweep_payload["sweep_submitted_shares"]))
                                filled_shares = Decimal(str(sweep_payload["sweep_filled_shares"]))
                            except (KeyError, TypeError, ValueError) as exc:
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING: sweep depth unavailable") from exc
                            if filled_shares < submitted_shares:
                                raise ValueError("depth_below_submitted_shares")
                            if (
                                reprice_payload.get("final_execution_intent_id")
                                != final_intent.hypothesis_id
                            ):
                                raise ValueError("FINAL_EXECUTION_INTENT_ID_MISMATCH")
                            try:
                                corrected_candidate_limit = Decimal(
                                    str(reprice_payload["corrected_candidate_limit_price"])
                                )
                            except (KeyError, TypeError, ValueError) as exc:
                                raise ValueError(
                                    "FINAL_EXECUTION_LIMIT_MISMATCH: missing corrected candidate limit"
                                ) from exc
                            final_limit_decimal = Decimal(str(final_intent.final_limit_price))
                            tick_tolerance = Decimal(
                                str(snapshot_fields["executable_snapshot_min_tick_size"])
                            ) / Decimal("1000000")
                            if abs(final_limit_decimal - corrected_candidate_limit) > tick_tolerance:
                                raise ValueError(
                                    "FINAL_EXECUTION_LIMIT_MISMATCH: "
                                    f"corrected_candidate={float(corrected_candidate_limit):.6f} "
                                    f"final_intent_limit={float(final_limit_decimal):.6f}"
                                )
                            execute_final = getattr(deps, "execute_final_intent", None)
                            if not callable(execute_final):
                                from src.execution.executor import execute_final_intent as execute_final
                            result = execute_final(
                                final_intent,
                                conn=conn,
                                decision_id=str(d.decision_id) if d.decision_id else "",
                                snapshot_conn=conn,
                            )
                            submitted_limit = float(final_limit_decimal)
                            submit_rejected = str(getattr(result, "status", "") or "") == "rejected"
                            reprice_payload["execution_path"] = "final_execution_intent"
                            reprice_payload["submitted_limit_price"] = (
                                None if submit_rejected else submitted_limit
                            )
                            reprice_payload["final_limit_price"] = submitted_limit
                            reprice_payload["repriced_limit_forced"] = snapshot_best_ask is not None
                            reprice_payload["submit_path"] = (
                                None if submit_rejected else "final_execution_intent"
                            )
                            reprice_payload["final_execution_intent_id"] = final_intent.hypothesis_id
                            if submit_rejected:
                                reprice_payload["submit_rejected_reason"] = getattr(
                                    result,
                                    "reason",
                                    None,
                                )
                            if isinstance(shadow_payload, dict):
                                shadow_payload["execution_path"] = "final_execution_intent"
                                shadow_payload["submitted_limit_price"] = (
                                    None
                                    if submit_rejected
                                    else _decimal_payload(final_limit_decimal)
                                )
                                shadow_payload["submit_path"] = (
                                    None if submit_rejected else "final_execution_intent"
                                )
                                shadow_payload["submitted_matches_corrected_candidate"] = (
                                    False
                                    if submit_rejected
                                    else abs(final_limit_decimal - corrected_candidate_limit) <= tick_tolerance
                                )
                                if submit_rejected:
                                    shadow_payload["submit_rejected_reason"] = getattr(
                                        result,
                                        "reason",
                                        None,
                                    )
                        else:
                            intent = deps.create_execution_intent(
                                edge_context=d.edge_context,
                                edge=d.edge,
                                size_usd=d.size_usd,
                                mode=mode.value,
                                market_id=d.tokens["market_id"],
                                token_id=d.tokens["token_id"],
                                no_token_id=d.tokens["no_token_id"],
                                best_ask=snapshot_best_ask,
                                repriced_limit_price=snapshot_best_ask,
                                event_id=(
                                    candidate.event_id
                                    or candidate.slug
                                    or f"{city.name}:{candidate.target_date}"
                                ),
                                resolution_window=candidate.target_date,
                                correlation_key=(
                                    f"{getattr(city, 'cluster', '') or city.name}:{candidate.target_date}"
                                ),
                                executable_snapshot_id=snapshot_fields["executable_snapshot_id"],
                                executable_snapshot_min_tick_size=snapshot_fields["executable_snapshot_min_tick_size"],
                                executable_snapshot_min_order_size=snapshot_fields["executable_snapshot_min_order_size"],
                                executable_snapshot_neg_risk=snapshot_fields["executable_snapshot_neg_risk"],
                                decision_source_context=_decision_source_context_from_epistemic_json(
                                    d.epistemic_context_json
                                ),
                            )
                            result = deps.execute_intent(
                                intent,
                                d.edge.vwmp,
                                d.edge.bin.label,
                                decision_id=str(d.decision_id) if d.decision_id else "",
                            )
                    except Exception as exc:
                        summary["no_trades"] += 1
                        rejection_stage = "EXECUTION_FAILED"
                        reason = str(exc)
                        if reason in {"strategy_key_unclassified", "depth_below_submitted_shares", "executable_snapshot_stale"}:
                            rejection_reasons = [reason]
                        else:
                            rejection_reasons = [f"execution_intent_rejected:{exc}"]
                        _record_opportunity_fact(
                            candidate,
                            d,
                            should_trade=False,
                            rejection_stage=rejection_stage,
                            rejection_reasons=rejection_reasons,
                        )
                        artifact.add_no_trade(
                            deps.NoTradeCase(
                                decision_id=d.decision_id,
                                city=city.name,
                                target_date=candidate.target_date,
                                range_label=d.edge.bin.label if d.edge else "",
                                direction=d.edge.direction if d.edge else "",
                                rejection_stage=rejection_stage,
                                strategy=strategy_name,
                                strategy_key=strategy_name,
                                edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=d.edge.entry_price if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=list(d.applied_validations),
                                bin_labels=parseable_labels,
                                p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                                p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                                p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                                alpha=getattr(d, "alpha", 0.0),
                                market_hours_open=candidate.hours_since_open,
                                agreement=getattr(d, "agreement", ""),
                                timestamp=decision_time.isoformat(),
                            )
                        )
                        continue
                    artifact.add_trade(
                        {
                            "decision_id": d.decision_id,
                            "trade_id": result.trade_id,
                            "status": result.status,
                            "timestamp": decision_time.isoformat(),
                            "city": city.name,
                            "target_date": candidate.target_date,
                            "range_label": d.edge.bin.label,
                            "direction": d.edge.direction,
                            "market_id": d.tokens["market_id"],
                            "token_id": d.tokens["token_id"],
                            "no_token_id": d.tokens["no_token_id"],
                            "size_usd": d.size_usd,
                            "entry_price": d.edge.entry_price,
                            "p_posterior": d.edge.p_posterior,
                            "edge": d.edge.edge,
                            "strategy_key": strategy_name,
                            "strategy": strategy_name,
                            "edge_source": d.edge_source or deps._classify_edge_source(mode, d.edge),
                            "market_hours_open": candidate.hours_since_open,
                            "decision_snapshot_id": d.decision_snapshot_id,
                            "selected_method": d.selected_method,
                            "applied_validations": d.applied_validations,
                            "settlement_semantics_json": d.settlement_semantics_json,
                            "epistemic_context_json": d.epistemic_context_json,
                            "edge_context_json": d.edge_context_json,
                            "bin_labels": parseable_labels,
                            "p_raw_vector": d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                            "p_cal_vector": d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                            "p_market_vector": d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                            "alpha": getattr(d, "alpha", 0.0),
                            "agreement": getattr(d, "agreement", ""),
                            "executable_snapshot_reprice": (
                                d.tokens.get("executable_snapshot_reprice", {})
                                if isinstance(getattr(d, "tokens", None), dict)
                                else {}
                            ),
                        }
                    )
                    # P1.S5 INV-32: materialize_position advances position
                    # authority ONLY after the venue command reached a durable
                    # ack state (ACKED, PARTIAL, FILLED). Commands in
                    # SUBMITTING / UNKNOWN do not yield position rows;
                    # the recovery loop resolves them out-of-band.
                    _cmd_state = result.command_state  # str | None
                    _cmd_durable = _cmd_state in ("ACKED", "PARTIAL", "FILLED")
                    _cmd_in_flight = _cmd_state in ("SUBMITTING", "UNKNOWN")
                    runtime_order_status = result.status
                    if result.status == "filled" and _cmd_state != "FILLED":
                        logger.warning(
                            "run_cycle: downgrading non-final filled order result to pending "
                            "for trade_id=%s command_state=%s",
                            getattr(result, "trade_id", ""),
                            _cmd_state or "missing",
                        )
                        runtime_order_status = "pending"
                    if runtime_order_status in ("filled", "pending") and _cmd_durable:
                        pos = materialize_position(
                            candidate,
                            d,
                            result,
                            portfolio,
                            city,
                            mode,
                            state=initial_entry_runtime_state_for_order_status(runtime_order_status),
                            env=env,
                            bankroll_at_entry=entry_bankroll,
                            deps=deps,
                        )
                        deps.add_position(portfolio, pos)
                        from src.state.db import log_execution_report, log_trade_entry

                        sp_name = f"sp_candidate_{str(d.decision_id).replace('-', '_')}"
                        conn.execute(f"SAVEPOINT {sp_name}")
                        try:
                            log_trade_entry(conn, pos)
                            log_execution_report(conn, pos, result, decision_id=d.decision_id)
                            # Post-audit fix #2 (2026-04-24): dual-write moved
                            # INSIDE sp_candidate_* — DR-33-B (commit 2a62623)
                            # replaced with-conn inside append_many_and_project
                            # with explicit nested SAVEPOINT, so placing the
                            # dual-write here no longer releases sp_candidate_*
                            # on commit. Closes torn-state window per T4.0 F3.
                            _dual_write_canonical_entry_if_available(
                                conn,
                                pos,
                                decision_id=d.decision_id,
                                deps=deps,
                                decision_evidence=getattr(d, "decision_evidence", None),
                            )
                            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                        except Exception:
                            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                            raise
                        portfolio_dirty = True
                        if result.status == "filled":
                            tracker.record_entry(pos)
                            tracker_dirty = True
                            summary["trades"] += 1
                    elif result.status in ("filled", "pending") and not _cmd_durable:
                        # INV-32: command in SUBMITTING/UNKNOWN or command_state=None
                        # (pre-P1.S5 path or executor rejected before persist).
                        # Do not materialize; recovery loop will resolve.
                        if _cmd_in_flight:
                            logger.warning(
                                "INV-32: skipping materialize_position for trade_id=%s "
                                "command_state=%s (in-flight; recovery will resolve)",
                                result.trade_id,
                                _cmd_state,
                            )
                        else:
                            logger.warning(
                                "INV-32: skipping materialize_position for trade_id=%s "
                                "command_state=%s (no durable ack)",
                                result.trade_id,
                                _cmd_state,
                            )
                    else:
                        from src.state.db import log_execution_report

                        log_execution_report(
                            conn,
                            _execution_stub(candidate, d, result, city, mode, deps=deps),
                            result,
                            decision_id=d.decision_id,
                        )
                else:
                    edge_source = ""
                    strategy_name = strategy_key
                    rejection_stage = d.rejection_stage
                    rejection_reasons = list(d.rejection_reasons)
                    if d.edge:
                        edge_source = d.edge_source or deps._classify_edge_source(mode, d.edge)
                        if not strategy_name:
                            rejection_stage = "SIGNAL_QUALITY"
                            rejection_reasons = [*rejection_reasons, "strategy_key_unclassified"]
                    availability_status = str(getattr(d, "availability_status", "") or "")
                    if availability_status:
                        _record_availability_fact(
                            status=availability_status,
                            reasons=rejection_reasons,
                            scope_type="candidate" if d.decision_id else "city_target",
                            scope_key=(
                                d.decision_id
                                if d.decision_id
                                else _availability_scope_key(candidate=candidate)
                            ),
                            details={
                                "decision_id": d.decision_id,
                                "candidate_id": _availability_scope_key(candidate=candidate),
                                "city": city.name,
                                "target_date": candidate.target_date,
                                "range_label": d.edge.bin.label if d.edge else "",
                                "direction": d.edge.direction if d.edge else "unknown",
                                "rejection_stage": rejection_stage,
                                "rejection_reasons": rejection_reasons,
                                "availability_status": availability_status,
                                "strategy_key": strategy_name,
                            },
                        )
                    _record_opportunity_fact(
                        candidate,
                        d,
                        should_trade=False,
                        rejection_stage=rejection_stage,
                        rejection_reasons=rejection_reasons,
                    )
                    summary["no_trades"] += 1
                    artifact.add_no_trade(
                        deps.NoTradeCase(
                            decision_id=d.decision_id,
                            city=city.name,
                            target_date=candidate.target_date,
                            range_label=d.edge.bin.label if d.edge else "",
                            direction=d.edge.direction if d.edge else "",
                            rejection_stage=rejection_stage,
                            strategy=strategy_name,
                            strategy_key=strategy_name,
                            edge_source=edge_source,
                            availability_status=getattr(d, "availability_status", ""),
                            rejection_reasons=rejection_reasons,
                            best_edge=d.edge.edge if d.edge else 0.0,
                            model_prob=d.edge.p_posterior if d.edge else 0.0,
                            market_price=d.edge.entry_price if d.edge else 0.0,
                            decision_snapshot_id=d.decision_snapshot_id,
                            selected_method=d.selected_method,
                            settlement_semantics_json=d.settlement_semantics_json,
                            epistemic_context_json=d.epistemic_context_json,
                            edge_context_json=d.edge_context_json,
                            applied_validations=list(d.applied_validations),
                            bin_labels=parseable_labels,
                            p_raw_vector=d.p_raw.tolist() if getattr(d, "p_raw", None) is not None else [],
                            p_cal_vector=d.p_cal.tolist() if getattr(d, "p_cal", None) is not None else [],
                            p_market_vector=d.p_market.tolist() if getattr(d, "p_market", None) is not None else [],
                            alpha=getattr(d, "alpha", 0.0),
                            market_hours_open=candidate.hours_since_open,
                            agreement=getattr(d, "agreement", ""),
                            timestamp=decision_time.isoformat(),
                        )
                    )
        except Exception as e:
            deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e, exc_info=True)

    return portfolio_dirty, tracker_dirty
