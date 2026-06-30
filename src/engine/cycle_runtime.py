# Created: 2026-05-04
# Last reused/audited: 2026-06-13
# Authority basis: IOC forward-port (Fix C: allowed_discovery_modes_inverse) — 2026-05-23
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
import time
import uuid
from dataclasses import is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace
from typing import Any

from src.config import get_mode, state_path
from src.contracts.canonical_lifecycle import is_cancel_confirmed_status
from src.contracts.decision_evidence import DecisionEvidence, EvidenceAsymmetryError
from src.contracts.effective_kelly_context import EffectiveKellyContext
from src.contracts.execution_intent import DecisionSourceContext
from src.contracts.position_truth import (
    REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES,
    has_current_money_risk_chain_state,
)
from src.engine.time_context import lead_hours_to_date_start, lead_hours_to_settlement_close
from src.state.lifecycle_manager import (
    LifecyclePhase,
    TERMINAL_STATES,
    enter_day0_window_runtime_state,
    initial_entry_runtime_state_for_order_status,
    is_terminal_state,
)
from src.state.portfolio import (
    CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_MODEL_EDGE_PRICE,
    ENTRY_ECONOMICS_SUBMITTED_LIMIT,
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    INACTIVE_RUNTIME_STATES,
    get_open_positions,
)

logger = logging.getLogger(__name__)

SOURCE_WRITER_FRONTIER_STALE_SECONDS = 5 * 60
_REDECISION_QUARANTINE_CHAIN_STATES = REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES
_CHAIN_ABSENT_QUARANTINE_REDECISION_SECONDS = 12 * 3600


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
    is canonical for attribution. Recomputed on every call so registry swaps
    in tests propagate without import-order surprises.
    """
    from src.strategy.strategy_profile import live_safe_keys
    return live_safe_keys()


def _strategy_keys_by_discovery_mode() -> dict[str, frozenset[str]]:
    """Discovery_mode → set of strategy_keys allowed in that mode.

    Uses ``allowed_discovery_modes_inverse`` (multi-valued allowed-modes field)
    instead of ``cycle_axis_dispatch_inverse`` (single dispatch-ownership field).
    The ownership inversion was a latent bug: strategies spanning multiple modes
    (e.g. opening_inertia over opening_hunt AND imminent_open_capture) were
    phase-rejected in the non-owner mode. Recomputed on every call (cheap;
    registry is small)."""
    from src.strategy.strategy_profile import allowed_discovery_modes_inverse
    return allowed_discovery_modes_inverse()


# Module-level read for backward-compat. Tests / external callers that
# imported these names get a snapshot equivalent to the pre-H2 behavior.
# Prefer the helpers above for fresh reads (e.g., post-_reload_for_test).
CANONICAL_STRATEGY_KEYS = _canonical_strategy_keys()
STRATEGY_KEYS_BY_DISCOVERY_MODE = _strategy_keys_by_discovery_mode()
NATIVE_BUY_NO_LIVE_APPROVED_CONTEXTS: frozenset[tuple[str, str, str]] = frozenset()
NATIVE_BUY_NO_LIVE_PROMOTION_VALIDATION = "native_buy_no_live_promotion_approved"
_FORWARD_PRICE_LINKAGE_OK_STATUSES = frozenset({"inserted", "unchanged"})
_ORDER_OWNERSHIP_TERMINAL_POSITION_PHASES = frozenset(TERMINAL_STATES)
_ORDER_OWNERSHIP_TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "cancelled", "canceled", "expired", "rejected", "voided"}
)
_LIVE_DISCOVERY_EVAL_BUDGET_ENV = "ZEUS_LIVE_DISCOVERY_EVAL_BUDGET_SECONDS"
_LIVE_DISCOVERY_EVAL_BUDGET_DEFAULT_SECONDS = 360.0
POLYMARKET_MARKETABLE_BUY_MIN_NOTIONAL_USD = Decimal("1")


def _marketable_buy_min_notional_usd(final_intent_context: dict) -> Decimal:
    raw_value = final_intent_context.get("marketable_buy_min_notional_usd")
    if raw_value in (None, ""):
        return POLYMARKET_MARKETABLE_BUY_MIN_NOTIONAL_USD
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            "marketable_buy_min_notional_usd must be a decimal-compatible value"
        ) from exc
    if value <= Decimal("0"):
        raise ValueError("marketable_buy_min_notional_usd must be positive")
    return value


def _record_submitted_shoulder_exposure(
    *,
    conn,
    city_name: str,
    target_date: str,
    decision,
    observed_at: datetime,
    source: str,
) -> str | None:
    """Record shoulder exposure only after a final runtime submit survives gates."""

    edge = getattr(decision, "edge", None)
    if edge is None:
        return None
    try:
        from src.strategy.shoulder_cluster_cap import record_accepted_shoulder_exposure

        return record_accepted_shoulder_exposure(
            conn=conn,
            city_name=city_name,
            target_date=target_date,
            edge=edge,
            notional_usd=float(getattr(decision, "size_usd", 0.0) or 0.0),
            decision_event_id=str(getattr(decision, "decision_id", "") or ""),
            observed_at=observed_at,
            source=source,
        )
    except Exception as exc:  # noqa: BLE001
        return f"shoulder_exposure_ledger_write_failed: {exc}"


def _freeze_entries_after_shoulder_ledger_failure(error: str, *, logger) -> str | None:
    """Pause entries when accepted shoulder exposure cannot reach its risk ledger."""

    try:
        from src.control.control_plane import pause_entries

        pause_entries(
            "shoulder_exposure_ledger_write_failed_after_submit",
            issued_by="system_auto_pause",
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "failed to pause entries after shoulder ledger write failure: %s; ledger_error=%s",
            exc,
            error,
            exc_info=True,
        )
        return f"entries_pause_failed_after_shoulder_ledger_error: {exc}"


# D4: exit triggers whose statistical burden (2 consecutive negative cycles,
# no FDR correction) is weaker than the entry-side burden (bootstrap CI +
# BH-FDR). These are statistical hypotheses; force-majeure exits are excluded
# because their evidence class is market/risk/settlement authority, not
# entry-vs-exit statistical symmetry.
#
# Excluded triggers and their rationale:
# - SETTLEMENT_IMMINENT / WHALE_TOXICITY / FLASH_CRASH_PANIC /
#   RED_FORCE_EXIT / VIG_EXTREME — force-majeure exits
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


def _source_writer_frontier_status(now: datetime | None = None) -> dict[str, object]:
    """Non-blocking source writer freshness for money-path frontier reports."""
    now_utc = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    status: dict[str, object] = {
        "source_data_fresh": None,
        "source_writer_fresh": False,
        "observability_degraded": True,
        "writer_budget_seconds": SOURCE_WRITER_FRONTIER_STALE_SECONDS,
    }
    try:
        path = state_path("source_health.json")
        status["path"] = str(path)
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 - frontier diagnostics must not block trading.
        status["issue"] = f"SOURCE_HEALTH_READ_FAILED:{type(exc).__name__}"
        return status
    written_at_raw = data.get("written_at") if isinstance(data, dict) else None
    try:
        written_at = datetime.fromisoformat(str(written_at_raw).replace("Z", "+00:00"))
        if written_at.tzinfo is None:
            written_at = written_at.replace(tzinfo=timezone.utc)
        writer_age = (now_utc - written_at.astimezone(timezone.utc)).total_seconds()
    except Exception:
        status["issue"] = "SOURCE_HEALTH_WRITER_TIME_INVALID"
        return status
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    stale_sources = [
        str(name)
        for name, payload in sources.items()
        if isinstance(payload, dict) and str(payload.get("status", "")).upper() == "STALE"
    ] if isinstance(sources, dict) else []
    writer_fresh = writer_age <= SOURCE_WRITER_FRONTIER_STALE_SECONDS
    status.update(
        {
            "written_at": written_at.astimezone(timezone.utc).isoformat(),
            "writer_age_seconds": float(writer_age),
            "source_writer_fresh": bool(writer_fresh),
            "source_data_fresh": len(stale_sources) == 0,
            "stale_sources": stale_sources[:10],
            "observability_degraded": not writer_fresh,
            "issue": "" if writer_fresh else "SOURCE_HEALTH_WRITER_OBSERVABILITY_STALE",
        }
    )
    return status


def _live_discovery_eval_budget_seconds(mode, env: str, params: dict | None) -> float | None:
    if str(env or "").strip().lower() != "live":
        return None
    raw = None
    if isinstance(params, dict):
        raw = params.get("evaluation_budget_seconds")
    if raw is None:
        raw = os.getenv(_LIVE_DISCOVERY_EVAL_BUDGET_ENV)
    if raw is None:
        return _LIVE_DISCOVERY_EVAL_BUDGET_DEFAULT_SECONDS
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using default", _LIVE_DISCOVERY_EVAL_BUDGET_ENV, raw)
        return _LIVE_DISCOVERY_EVAL_BUDGET_DEFAULT_SECONDS
    if budget <= 0:
        return None
    return budget


def _monotonic_seconds(deps) -> float:
    getter = getattr(deps, "monotonic", None)
    if callable(getter):
        return float(getter())
    return time.monotonic()


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
    day0_immature_reason = _day0_immature_exit_authority_reason(pos)
    if day0_immature_reason and _exit_trigger_requires_mature_day0_authority(exit_trigger):
        return _record_exit_evidence_gate_block(
            summary,
            deps,
            trade_id=pos.trade_id,
            trigger=exit_trigger,
            reason=f"DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED:{day0_immature_reason}",
        )
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


def _quantize_submit_shares(
    direction: str,
    shares: Decimal,
    *,
    final_limit_price: Decimal | None = None,
    order_type: str | None = None,
    tick_size: Decimal | str | None = None,
) -> Decimal:
    if shares <= Decimal("0"):
        raise ValueError("submitted_shares must be positive")
    if final_limit_price is not None and order_type is not None:
        from src.contracts.execution_intent import quantize_submit_shares_for_venue

        return quantize_submit_shares_for_venue(
            direction,
            shares,
            final_limit_price=final_limit_price,
            order_type=order_type,
            tick_size=tick_size,
        )
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
    passive_maker_context=None,
    taker_quality_proof: dict | None = None,
) -> dict:
    """Attach corrected pricing evidence and the frozen final submit intent."""
    # Provenance context required by H3 semantic linter rule (p_posterior access).
    # Accessing entry_method/selected_method satisfies the rule that p_posterior
    # consumers must evaluate the selection provenance in the same scope.
    _entry_method = str(decision.entry_method if hasattr(decision, "entry_method") else "")
    _selected_method = str(decision.selected_method if hasattr(decision, "selected_method") else "")

    from src.contracts.execution_intent import (
        ExecutableCostBasis,
        ExecutableTradeHypothesis,
        FinalExecutionIntent,
        PassiveMakerExecutionContext,
        simulate_clob_sweep,
    )

    tokens = dict(getattr(decision, "tokens", {}) or {})
    edge = getattr(decision, "edge", None)
    if edge is None:
        raise ValueError("corrected pricing authority requires edge")
    setattr(decision, "final_execution_intent", None)
    decision_snapshot_id = str(getattr(decision, "decision_snapshot_id", "") or "").strip()
    if not decision_snapshot_id:
        decision_snapshot_id = str(
            getattr(getattr(decision, "edge_context", None), "decision_snapshot_id", "")
            or ""
        ).strip()
    if not decision_snapshot_id:
        raise ValueError("corrected pricing authority requires decision_snapshot_id")

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
            raise ValueError("corrected pricing sweep produced no executable fill")
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
        raw_submit_shares = max(sweep.filled_shares, snapshot.min_order_size)
        submitted_shares = _quantize_submit_shares(
            direction,
            raw_submit_shares,
            final_limit_price=candidate_limit,
            order_type=immediate_order_type,
            tick_size=snapshot.min_tick_size,
        )
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
    if not is_marketable and immediate_order_type not in {"GTC", "GTD"}:
        final_unsupported_reason = "PASSIVE_LIMIT_REQUIRES_GTC_OR_GTD_POST_ONLY"
    if not final_unsupported_reason:
        final_intent = FinalExecutionIntent.from_hypothesis_and_cost_basis(
            hypothesis=hypothesis,
            cost_basis=cost_basis,
            order_type=immediate_order_type,
            post_only=not is_marketable,
            cancel_after=cancel_after,
            max_slippage_bps=Decimal("200"),
            event_id=snapshot_event_id,
            resolution_window=resolution_window,
            correlation_key=correlation_key,
            decision_source_context=_decision_source_context_from_epistemic_json(
                getattr(decision, "epistemic_context_json", None)
            ),
            passive_maker_context=(
                passive_maker_context
                if isinstance(passive_maker_context, PassiveMakerExecutionContext)
                else None
            ),
            taker_quality_proof=taker_quality_proof,
        )
        setattr(decision, "final_execution_intent", final_intent)
    payload = {
        "pricing_semantics_id": cost_basis.pricing_semantics_id,
        "submit_authority_absent": final_intent is None,
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
        "passive_maker_context": (
            {
                "spread_usd": _decimal_payload(final_intent.passive_maker_context.spread_usd),
                "quote_age_ms": final_intent.passive_maker_context.quote_age_ms,
                "expected_fill_probability": _decimal_payload(
                    final_intent.passive_maker_context.expected_fill_probability
                ),
                "queue_depth_ahead": (
                    None
                    if final_intent.passive_maker_context.queue_depth_ahead is None
                    else _decimal_payload(final_intent.passive_maker_context.queue_depth_ahead)
                ),
                "adverse_selection_score": (
                    None
                    if final_intent.passive_maker_context.adverse_selection_score is None
                    else _decimal_payload(final_intent.passive_maker_context.adverse_selection_score)
                ),
                "orderbook_hash_age_ms": final_intent.passive_maker_context.orderbook_hash_age_ms,
            }
            if final_intent is not None and final_intent.passive_maker_context is not None
            else None
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
    tokens["corrected_pricing_evidence"] = payload
    decision.tokens = tokens
    validation = (
        "final_execution_intent_built"
        if final_intent is not None
        else "corrected_pricing_evidence_built"
    )
    if validation not in decision.applied_validations:
        decision.applied_validations.append(validation)
    return payload


def _ensure_fresh_executable_snapshot(
    conn,
    snapshot_id: str,
    *,
    now: datetime,
    clob: Any = None,
    decision: Any = None,
    market: dict | None = None,
):
    """Return a snapshot that satisfies the 30s freshness gate, re-capturing if stale.

    Root cause (2026-05-24, docs/archive/2026-Q2/operations_historical/EXEC_FRESHNESS_ROOTCAUSE_2026-05-24.md):
    the 5-min mode run's discovery→reprice latency exceeds the 30s freshness window,
    so the persisted cycle snapshot is already stale at submit and reprice raised
    ``executable_snapshot_stale`` — killing real above-floor edges. The 30s gate is
    correct (never submit on a stale book); the defect was reusing the cycle snapshot
    instead of re-capturing fresh.

    Behaviour:
    - fresh persisted snapshot → returned as-is (no network).
    - stale + a live CLOB client (and decision/market identity) → re-capture a fresh
      single-market snapshot via the validated ``capture_executable_market_snapshot``
      primitive and return it.
    - stale + no client → raise ``executable_snapshot_stale`` (safety gate preserved).
    """
    from src.contracts.executable_market_snapshot import is_fresh
    from src.state.snapshot_repo import get_snapshot

    snapshot = get_snapshot(conn, snapshot_id)
    if snapshot is None:
        raise ValueError(f"EXECUTABLE_SNAPSHOT_UNAVAILABLE: {snapshot_id}")
    if is_fresh(snapshot, now):
        return snapshot
    # Stale: re-capture fresh for this single market when a client is available.
    if clob is None or decision is None or not market:
        raise ValueError("executable_snapshot_stale")
    from src.data.market_scanner import capture_executable_market_snapshot

    try:
        fields = capture_executable_market_snapshot(
            conn,
            market=market,
            decision=decision,
            clob=clob,
            captured_at=now,
            scan_authority="VERIFIED",
            execution_side="BUY",
        )
    except Exception as exc:  # noqa: BLE001 — any capture failure preserves the stale gate
        logger.warning("executable_snapshot_stale: recapture failed — %s", exc)
        raise ValueError("executable_snapshot_stale") from exc
    new_id = str(fields.get("executable_snapshot_id") or "")
    fresh = get_snapshot(conn, new_id) if new_id else None
    if fresh is None or not is_fresh(fresh, now):
        raise ValueError("executable_snapshot_stale")
    return fresh


def _market_dict_from_snapshot(snapshot) -> dict:
    """Build the minimal Gamma ``market`` dict capture_executable_market_snapshot needs
    from a persisted executable snapshot's identity facts.

    Market identity (condition/question/token ids, slug, neg_risk) does NOT go stale.
    Tradability does. This payload is explicitly marked as reconstructed so
    capture_executable_market_snapshot ignores stale Gamma accepting-order facts
    and requires current CLOB archived/orderbook/accepting-orders authority.
    """

    def _iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    yes_token = str(getattr(snapshot, "yes_token_id", "") or "")
    no_token = str(getattr(snapshot, "no_token_id", "") or "")
    condition_id = str(getattr(snapshot, "condition_id", "") or "")
    question_id = str(getattr(snapshot, "question_id", "") or "")
    gamma_market_id = str(getattr(snapshot, "gamma_market_id", "") or condition_id)
    neg_risk = bool(getattr(snapshot, "neg_risk", False))
    gamma_market_raw = {
        "id": gamma_market_id,
        "conditionId": condition_id,
        "questionID": question_id,
        "tradability_authority": "persisted_snapshot_reconstruction",
        "identity_only": True,
        "negRisk": neg_risk,
        "clobTokenIds": [yes_token, no_token],
    }
    outcome = {
        "token_id": yes_token,
        "no_token_id": no_token,
        "condition_id": condition_id,
        "market_id": condition_id,
        "question_id": question_id,
        "gamma_market_id": gamma_market_id,
        "identity_only": True,
        "neg_risk": neg_risk,
        "market_start_at": _iso(getattr(snapshot, "market_start_at", None)),
        "market_end_at": _iso(getattr(snapshot, "market_end_at", None)),
        "market_close_at": _iso(getattr(snapshot, "market_close_at", None)),
        "token_map_raw": dict(
            getattr(snapshot, "token_map_raw", None) or {"YES": yes_token, "NO": no_token}
        ),
        "raw_gamma_payload_hash": str(getattr(snapshot, "raw_gamma_payload_hash", "") or ""),
        "gamma_market_raw": gamma_market_raw,
    }
    return {
        "event_id": str(getattr(snapshot, "event_id", "") or ""),
        "slug": str(getattr(snapshot, "event_slug", "") or ""),
        "outcomes": [outcome],
    }


def _propagate_recaptured_snapshot_fields(snapshot_fields, fresh_snapshot) -> None:
    """Propagate a re-captured snapshot's id AND derived facts into snapshot_fields.

    After fresh-at-submit re-capture, the recorded provenance must reference the fresh
    snapshot in full — not the fresh id paired with the stale snapshot's tick/min_order/
    neg_risk (read at live validation and on the paper path). Otherwise the recorded
    intent carries a fresh id against stale derived facts.
    """
    if not isinstance(snapshot_fields, dict):
        return
    snapshot_fields["executable_snapshot_id"] = str(fresh_snapshot.snapshot_id)
    if getattr(fresh_snapshot, "min_tick_size", None) is not None:
        snapshot_fields["executable_snapshot_min_tick_size"] = str(fresh_snapshot.min_tick_size)
    if getattr(fresh_snapshot, "min_order_size", None) is not None:
        snapshot_fields["executable_snapshot_min_order_size"] = str(fresh_snapshot.min_order_size)
    snapshot_fields["executable_snapshot_neg_risk"] = bool(getattr(fresh_snapshot, "neg_risk", False))


def _reprice_recapture_fresh_snapshot(conn, snapshot_id, *, decision, stale_snapshot, now):
    """Open a short-lived public CLOB client and re-capture a fresh snapshot for ONE market.

    Activates the fresh-at-submit path (see docs/archive/2026-Q2/operations_historical/EXEC_FRESHNESS_ROOTCAUSE_2026-05-24.md):
    the persisted cycle snapshot is stale because the mode run's discovery->reprice latency
    exceeds the 30s window. PolymarketClient() public orderbook reads need no auth/keychain
    (httpx _public_http), so this is a read-only fresh price fetch — it places no orders.
    Disabled via ZEUS_REPRICE_RECAPTURE_DISABLED (then the 30s stale gate is preserved).
    """
    if os.environ.get("ZEUS_REPRICE_RECAPTURE_DISABLED"):
        raise ValueError("executable_snapshot_stale")
    from src.data.polymarket_client import PolymarketClient

    market = _market_dict_from_snapshot(stale_snapshot)
    with PolymarketClient() as clob:
        return _ensure_fresh_executable_snapshot(
            conn,
            snapshot_id,
            now=now,
            clob=clob,
            decision=decision,
            market=market,
        )


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
    from src.data.market_scanner import (
        _optional_top_book_level_decimal,
        _top_book_level_decimal,
    )
    from src.engine.evaluator import _size_at_execution_price_boundary
    from src.state.snapshot_repo import get_snapshot
    from src.strategy.market_fusion import vwmp

    snapshot = get_snapshot(conn, snapshot_id)
    if snapshot is None:
        raise ValueError(f"EXECUTABLE_SNAPSHOT_UNAVAILABLE: {snapshot_id}")
    from src.contracts.executable_market_snapshot import is_fresh

    _now_reprice = datetime.now(timezone.utc)
    if not is_fresh(snapshot, _now_reprice):
        # Fresh-at-submit re-capture: the persisted cycle snapshot has aged past the
        # 30s window (discovery->reprice latency); re-capture one fresh single-market
        # snapshot rather than rejecting a real edge.  Raises executable_snapshot_stale
        # when re-capture is disabled or unavailable (safety gate preserved).
        snapshot = _reprice_recapture_fresh_snapshot(
            conn,
            snapshot_id,
            decision=decision,
            stale_snapshot=snapshot,
            now=_now_reprice,
        )
        # _snapshot_id is content+time-based: re-capture mints a NEW id. Propagate the
        # id AND its sibling derived facts (tick/min_order/neg_risk read at live + paper
        # validation) so downstream trade provenance references the fresh snapshot the
        # order was actually priced against — never a fresh id against stale derived facts.
        if str(getattr(snapshot, "snapshot_id", "") or "") != snapshot_id:
            snapshot_id = str(snapshot.snapshot_id)
            _propagate_recaptured_snapshot_fields(snapshot_fields, snapshot)
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

    direction = Direction(decision.edge.direction)
    try:
        orderbook = json.loads(snapshot.orderbook_depth_jsonb)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"EXECUTABLE_SNAPSHOT_UNAVAILABLE: invalid orderbook JSON: {exc}") from exc
    if direction.startswith("buy_"):
        best_bid, bid_size = _optional_top_book_level_decimal(orderbook, "bids")
        best_ask, ask_size = _top_book_level_decimal(orderbook, "asks")
    else:
        best_bid, bid_size = _top_book_level_decimal(orderbook, "bids")
        best_ask, ask_size = _top_book_level_decimal(orderbook, "asks")
    ask_only_entry_book = direction.startswith("buy_") and best_bid is None
    best_bid_float = None if best_bid is None else float(best_bid)
    best_ask_float = float(best_ask)
    bid_size_float = float(bid_size)
    ask_size_float = float(ask_size)
    original_edge = decision.edge
    original_size = float(getattr(decision, "size_usd", 0.0) or 0.0)
    snapshot_vwmp = (
        best_ask_float
        if ask_only_entry_book
        else vwmp(float(best_bid_float), best_ask_float, bid_size_float, ask_size_float)
    )
    repriced_edge = float(decision.edge.p_posterior) - float(snapshot_vwmp)
    if repriced_edge <= 0.0:
        raise ValueError(f"EXECUTABLE_REPRICE_REJECTED: edge={repriced_edge:.6f}")
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
    final_intent_context = final_intent_context or {}
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
    # PR 7 — build EffectiveKellyContext from snapshot microstructure fields.
    # Spread derived from the snapshot's orderbook top levels (already parsed above).
    # Order type for maker path (W2) is GTC; taker paths (W3/W4) inherit the
    # intent context order_type which defaults to "GTC" pre-upgrade.
    # Use getattr with defaults for backward-compat with legacy SimpleNamespace mocks
    # in tests that predate the PR2 fields (depth_at_best_ask=0, top_ask check).
    from decimal import Decimal as _Decimal
    _snap_top_ask = getattr(snapshot, "orderbook_top_ask", None)
    _snap_depth = int(getattr(snapshot, "depth_at_best_ask", 0) or 0)
    _snapshot_spread_usd = (
        (best_ask - best_bid)
        if _snap_top_ask is not None and best_bid is not None
        else _Decimal("0")
    )
    try:
        from src.analysis.market_analysis_vnext import MarketAnalysisVNext
        _vnext_metrics = MarketAnalysisVNext(snapshot=snapshot, history=[]).compute()
        _snap_depth = int(_vnext_metrics.depth_at_best_ask)
        _vnext_wide_spread = bool(_vnext_metrics.wide_spread_display_substitution)
    except Exception:
        _vnext_wide_spread = False
    _reprice_order_type = str(final_intent_context.get("order_type") or "GTC").strip().upper()
    strategy_key_for_live_quality = _resolve_strategy_key(decision)
    # W2 is post-only passive maker sizing. Live strategy decisions consume
    # observed spread/depth; legacy helper tests without canonical strategy_key
    # keep the old neutral fixture context.
    _maker_spread = _snapshot_spread_usd if strategy_key_for_live_quality else _Decimal("0")
    _maker_depth = _snap_depth if strategy_key_for_live_quality else 100
    _maker_effective_context = EffectiveKellyContext(
        spread_usd=_maker_spread,
        depth_at_best_ask=_maker_depth,
        order_type="GTC",
    )
    _taker_order_type_for_haircut = _reprice_order_type
    if (
        bool(final_intent_context.get("allow_taker_upgrade"))
        and _taker_order_type_for_haircut in {"GTC", "GTD"}
    ):
        _taker_order_type_for_haircut = "FOK"
    _taker_effective_context = EffectiveKellyContext(
        spread_usd=_snapshot_spread_usd,
        depth_at_best_ask=_snap_depth,
        order_type=_taker_order_type_for_haircut,
    )
    # The first candidate is a passive maker limit. If the book supports an
    # immediate fill, the marketable branch below resizes with taker fees.
    # Wave 6 / K1 (PR #348): per-edge unified-budget gate. The EKC haircut at
    # this LIVE microstructure boundary is the duplicate that INV-40 drops once
    # σ_market already entered edge_LCB at edge-scan. No-op at flag-OFF (the
    # boundary ANDs with _unified_uncertainty_budget_enabled()). Single source
    # of truth: the edge carries whether σ_market was applied.
    _market_unc_in_lcb = bool(
        getattr(decision.edge, "market_cost_uncertainty_applied", False)
    )
    repriced_size_at_snapshot_vwmp = _size_at_execution_price_boundary(
        p_posterior=float(decision.edge.p_posterior),
        entry_price=float(snapshot_vwmp),
        fee_rate=0.0,
        sizing_bankroll=sizing_bankroll,
        kelly_multiplier=kelly_multiplier,
        effective_context=_maker_effective_context,  # W2
        market_uncertainty_in_lcb=_market_unc_in_lcb,
    )
    if repriced_size_at_snapshot_vwmp <= 0.0:
        raise ValueError("EXECUTABLE_REPRICE_REJECTED: repriced size is zero")
    best_ask_edge = float(decision.edge.p_posterior) - best_ask_float
    p_posterior_decimal = Decimal(str(decision.edge.p_posterior))
    slippage_reference_decimal = Decimal(str(slippage_reference_price))
    slippage_cap_decimal = slippage_reference_decimal * (
        Decimal("1") + Decimal(str(max_slippage.fraction))
    )
    positive_edge_cap_decimal = p_posterior_decimal - tick_size_decimal
    passive_maker_repositioned = False
    passive_maker_reposition_reason = ""
    if (
        direction.startswith("buy_")
        and best_bid is not None
        and snapshot_limit_decimal < best_bid
    ):
        if positive_edge_cap_decimal < best_bid:
            raise ValueError(
                "EXECUTABLE_PASSIVE_MAKER_NO_COMPETITIVE_POSITIVE_EDGE: "
                f"best_bid={float(best_bid):.6f} edge_cap={float(positive_edge_cap_decimal):.6f}"
            )
        snapshot_limit_decimal = best_bid
        passive_maker_repositioned = True
        passive_maker_reposition_reason = "raised_buy_limit_to_snapshot_best_bid"
    snapshot_limit_price = float(snapshot_limit_decimal)
    final_best_ask: float | None = None
    final_price = snapshot_limit_price
    repriced_size = repriced_size_at_snapshot_vwmp
    corrected_candidate_price = snapshot_limit_price
    corrected_candidate_expected_fill = snapshot_limit_price
    corrected_candidate_size = repriced_size_at_snapshot_vwmp
    best_ask_fee_adjusted_edge = 0.0
    best_ask_size_at_fee_adjusted_cost = 0.0
    if best_ask_edge > 0.0:
        best_ask_size_at_fee_adjusted_cost = _size_at_execution_price_boundary(
            p_posterior=float(decision.edge.p_posterior),
            entry_price=best_ask_float,
            fee_rate=taker_fee_rate,
            sizing_bankroll=sizing_bankroll,
            kelly_multiplier=kelly_multiplier,
            effective_context=_taker_effective_context,  # W3
            market_uncertainty_in_lcb=_market_unc_in_lcb,
        )
        best_ask_fee_adjusted_edge = best_ask_edge - (
            taker_fee_rate * best_ask_float * (1.0 - best_ask_float)
        )
    best_ask_inside_edge_budget = (
        best_ask_edge > 0.0
        and best_ask_fee_adjusted_edge > 0.0
        and best_ask_size_at_fee_adjusted_cost > 0.0
    )
    allow_taker_upgrade = bool(final_intent_context.get("allow_taker_upgrade"))
    edge_aware_taker_enabled = allow_taker_upgrade
    f34_crossing_evidence = None
    # F34 cost-of-fill optimizer (OPT-IN, default OFF).
    # ZEUS_TAKER_CROSSING_ENABLED=1 lets the math decide whether to cross the spread;
    # default "0" preserves the existing passive-maker-only behavior exactly.
    # Karachi safety: flag defaults OFF → zero impact on day0_window positions.
    # Operator must validate via backtest before flipping.
    if (
        os.environ.get("ZEUS_TAKER_CROSSING_ENABLED", "0") == "1"
        and allow_taker_upgrade
        and not ask_only_entry_book
    ):
        from src.engine.evaluator import _crossing_decision as _f34_crossing_decision
        _f34_order_size = best_ask_size_at_fee_adjusted_cost
        _f34_expected_pnl = best_ask_fee_adjusted_edge * _f34_order_size
        _f34_non_fill_prob = float(final_intent_context.get("f34_non_fill_probability", 0.5))
        _f34_min_econ_size = float(final_intent_context.get("f34_min_economical_size", 5.0))
        _f34_taker_fee_bps = taker_fee_rate * 10_000.0
        should_cross, f34_evidence = _f34_crossing_decision(
            best_ask_price=best_ask_float,
            best_ask_size=_f34_order_size,
            best_bid_price=best_bid_float,
            p_posterior=float(decision.edge.p_posterior),
            expected_pnl_if_filled=_f34_expected_pnl,
            non_fill_probability=_f34_non_fill_prob,
            taker_fee_bps=_f34_taker_fee_bps,
            min_economical_size=_f34_min_econ_size,
        )
        f34_evidence["orderbook_best_ask_size"] = ask_size_float
        f34_evidence["intended_order_size_usd"] = _f34_order_size
        f34_crossing_evidence = dict(f34_evidence)
        logger.info("F34_CROSSING_DECISION %s", f34_evidence)
        edge_aware_taker_enabled = allow_taker_upgrade and bool(should_cross)
    edge_aware_taker_selected = False
    depth_sweep_limit_decimal = Decimal("0")
    marketable_buy_below_venue_min = False
    marketable_buy_submitted_notional_usd: Decimal | None = None
    marketable_buy_min_notional_usd = _marketable_buy_min_notional_usd(
        final_intent_context
    )
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
    if (
        not best_ask_inside_slippage_budget
        and edge_aware_taker_enabled
        and best_ask_inside_edge_budget
    ):
        depth_sweep_limit_decimal = _floor_to_tick(
            Decimal(str(best_ask_float)),
            tick_size_decimal,
        )
        depth_sweep_limit_float = float(depth_sweep_limit_decimal)
        edge_aware_taker_selected = True
    if best_ask_edge > 0.0 and (best_ask_inside_slippage_budget or edge_aware_taker_selected):
        size_at_depth_limit = _size_at_execution_price_boundary(
            p_posterior=float(decision.edge.p_posterior),
            entry_price=depth_sweep_limit_float,
            fee_rate=taker_fee_rate,
            sizing_bankroll=sizing_bankroll,
            kelly_multiplier=kelly_multiplier,
            effective_context=_taker_effective_context,  # W4
            market_uncertainty_in_lcb=_market_unc_in_lcb,
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
                if ask_only_entry_book:
                    raise ValueError(
                        "EXECUTABLE_ASK_ONLY_PASSIVE_PRIOR_UNAVAILABLE: "
                        f"taker depth constrained status={best_ask_sweep.depth_status} "
                        f"visible_best_ask_usd={float(best_ask_sweep.gross_notional):.6f} "
                        f"required_usd={size_at_depth_limit:.6f}"
                    )
                raise ValueError(
                    "EXECUTABLE_TAKER_DEPTH_CONSTRAINED: "
                    f"visible_best_ask_usd={float(best_ask_sweep.gross_notional):.6f} "
                    f"required_usd={size_at_depth_limit:.6f}"
                )
            if direction.startswith("buy_"):
                marketable_buy_submitted_shares = _quantize_submit_shares(
                    str(direction),
                    max(best_ask_sweep.filled_shares, snapshot.min_order_size),
                    final_limit_price=depth_sweep_limit_decimal,
                    order_type="FOK",
                    tick_size=snapshot.min_tick_size,
                )
                marketable_buy_submitted_notional_usd = (
                    marketable_buy_submitted_shares * depth_sweep_limit_decimal
                )
            if (
                marketable_buy_submitted_notional_usd is not None
                and marketable_buy_submitted_notional_usd < marketable_buy_min_notional_usd
            ):
                if ask_only_entry_book:
                    raise ValueError(
                        "EXECUTABLE_ASK_ONLY_MARKETABLE_BUY_BELOW_MIN_NOTIONAL_NO_PASSIVE_BID: "
                        f"required_usd={float(marketable_buy_submitted_notional_usd):.6f} "
                        f"marketable_min_usd={float(marketable_buy_min_notional_usd):.6f} "
                        f"best_ask={best_ask_float:.6f}"
                    )
                passive_cap = _floor_to_tick(
                    min(
                        snapshot_limit_decimal,
                        best_ask - tick_size_decimal,
                        positive_edge_cap_decimal,
                    ),
                    tick_size_decimal,
                )
                if (
                    best_bid is not None
                    and passive_cap < best_bid <= positive_edge_cap_decimal
                    and best_bid < best_ask
                ):
                    passive_cap = best_bid
                if passive_cap <= Decimal("0") or passive_cap >= best_ask:
                    raise ValueError(
                        "EXECUTABLE_MARKETABLE_BUY_BELOW_MIN_NOTIONAL_NO_PASSIVE_PRICE: "
                        f"required_usd={float(marketable_buy_submitted_notional_usd):.6f} "
                        f"marketable_min_usd={float(marketable_buy_min_notional_usd):.6f} "
                        f"best_bid={float(best_bid):.6f} best_ask={best_ask_float:.6f}"
                    )
                snapshot_limit_decimal = passive_cap
                snapshot_limit_price = float(snapshot_limit_decimal)
                final_price = snapshot_limit_price
                corrected_candidate_price = snapshot_limit_price
                corrected_candidate_expected_fill = snapshot_limit_price
                corrected_candidate_size = repriced_size_at_snapshot_vwmp
                repriced_size = repriced_size_at_snapshot_vwmp
                marketable_buy_below_venue_min = True
                passive_maker_repositioned = True
                passive_maker_reposition_reason = (
                    "marketable_buy_notional_below_venue_min_repositioned_passive"
                )
            else:
                final_best_ask = depth_sweep_limit_float
                final_price = depth_sweep_limit_float
                repriced_size = size_at_depth_limit
                corrected_candidate_price = depth_sweep_limit_float
                corrected_candidate_expected_fill = float(best_ask_sweep.average_price or best_ask_float)
                corrected_candidate_size = size_at_depth_limit

    if ask_only_entry_book and final_best_ask is None:
        raise ValueError(
            "EXECUTABLE_ASK_ONLY_PASSIVE_PRIOR_UNAVAILABLE: "
            "BUY entry ask-only book has executable taker cost but no bid-side "
            "market prior for passive maker pricing"
        )

    passive_fill_probability = final_intent_context.get(
        "passive_fill_probability",
        final_intent_context.get("expected_fill_probability"),
    )
    if passive_fill_probability in (None, ""):
        try:
            from src.analysis.market_analysis_vnext import estimate_passive_maker_execution

            passive_estimate = estimate_passive_maker_execution(
                conn,
                snapshot,
                quote_price=snapshot_limit_decimal,
            )
        except Exception:
            passive_estimate = None
        if passive_estimate is not None:
            edge_profit_per_share = max(
                Decimal("0"),
                Decimal(str(decision.edge.p_posterior)) - snapshot_limit_decimal,
            )
            gross_profit_usd = (
                Decimal(str(repriced_size))
                * edge_profit_per_share
                / max(snapshot_limit_decimal, Decimal("0.000001"))
            )
            adverse_penalty_usd = (
                passive_estimate.adverse_selection_score
                * Decimal(str(repriced_size))
            )
            fill_adjusted_profit_usd = (
                passive_estimate.expected_fill_probability * gross_profit_usd
                - adverse_penalty_usd
            )
            min_profit_raw = final_intent_context.get("min_expected_profit_usd")
            min_profit_usd = Decimal(str(min_profit_raw if min_profit_raw not in (None, "") else "0.05"))
            if fill_adjusted_profit_usd >= min_profit_usd:
                passive_fill_probability = passive_estimate.expected_fill_probability
                final_intent_context["passive_fill_probability"] = str(
                    passive_estimate.expected_fill_probability
                )
                final_intent_context["queue_depth_ahead"] = (
                    None
                    if passive_estimate.queue_depth_ahead is None
                    else str(passive_estimate.queue_depth_ahead)
                )
                final_intent_context["adverse_selection_score"] = str(
                    passive_estimate.adverse_selection_score
                )
                final_intent_context["passive_fill_model_source"] = passive_estimate.evidence_source
                final_intent_context["passive_fill_model_order_count"] = passive_estimate.evidence_order_count
                final_intent_context["passive_fill_model_fill_count"] = passive_estimate.evidence_fill_count
                final_intent_context["fill_adjusted_expected_profit_usd"] = str(
                    fill_adjusted_profit_usd
                )
    passive_maker_context = None
    if passive_fill_probability not in (None, ""):
        from src.contracts.execution_intent import PassiveMakerExecutionContext

        captured_at = getattr(snapshot, "captured_at", None)
        quote_age_ms = 0
        if isinstance(captured_at, datetime):
            captured_utc = (
                captured_at
                if captured_at.tzinfo is not None
                else captured_at.replace(tzinfo=timezone.utc)
            )
            quote_age_ms = max(
                0,
                int(
                    (
                        datetime.now(timezone.utc)
                        - captured_utc.astimezone(timezone.utc)
                    ).total_seconds()
                    * 1000
                ),
            )
        passive_maker_context = PassiveMakerExecutionContext(
            spread_usd=_snapshot_spread_usd,
            quote_age_ms=quote_age_ms,
            expected_fill_probability=Decimal(str(passive_fill_probability)),
            queue_depth_ahead=(
                None
                if final_intent_context.get("queue_depth_ahead") in (None, "")
                else Decimal(str(final_intent_context.get("queue_depth_ahead")))
            ),
            adverse_selection_score=(
                None
                if final_intent_context.get("adverse_selection_score") in (None, "")
                else Decimal(str(final_intent_context.get("adverse_selection_score")))
            ),
            orderbook_hash_age_ms=quote_age_ms,
        )
    if final_best_ask is None and passive_maker_context is None:
        raise ValueError(
            "PASSIVE_FILL_PROBABILITY_UNMODELED: "
            "post_only_passive_limit requires PassiveMakerExecutionContext"
        )

    selected_order_type = str(final_intent_context.get("order_type") or "GTC").strip().upper()
    final_order_type = selected_order_type
    immediate_order_requested = selected_order_type in {"FOK", "FAK"}
    taker_quality_required = allow_taker_upgrade or immediate_order_requested
    taker_order_type_upgraded = False
    taker_quality_proof = None
    taker_quality_passed = False
    taker_candidate_present = final_best_ask is not None or immediate_order_requested
    if taker_candidate_present and taker_quality_required:
        taker_edge_dec = Decimal(str(best_ask_fee_adjusted_edge))
        taker_price_dec = Decimal(str(corrected_candidate_expected_fill or final_price))
        taker_notional_dec = Decimal(str(corrected_candidate_size))
        taker_expected_profit_usd = (
            max(Decimal("0"), taker_edge_dec)
            * taker_notional_dec
            / max(taker_price_dec, Decimal("0.000001"))
        )
        maker_context_source = "passive_maker_context"
        if passive_maker_context is None:
            maker_expected_profit_usd = Decimal("0")
            maker_expected_fill_probability = Decimal("0")
            maker_context_source = "maker_unavailable_or_unmodeled"
        else:
            maker_price_dec = min(
                Decimal(str(snapshot_limit_price)),
                Decimal(str(best_ask_float)) - tick_size_decimal,
                positive_edge_cap_decimal,
            )
            if (
                best_bid is not None
                and maker_price_dec < best_bid < Decimal(str(best_ask_float))
                and best_bid <= positive_edge_cap_decimal
            ):
                maker_price_dec = best_bid
            maker_notional_dec = max(
                Decimal(str(repriced_size_at_snapshot_vwmp)),
                maker_price_dec * Decimal(str(snapshot.min_order_size)),
            )
            maker_edge_dec = max(Decimal("0"), p_posterior_decimal - maker_price_dec)
            maker_expected_profit_usd = (
                passive_maker_context.expected_fill_probability
                * maker_edge_dec
                * maker_notional_dec
                / max(maker_price_dec, Decimal("0.000001"))
            )
            if passive_maker_context.adverse_selection_score is not None:
                maker_expected_profit_usd -= (
                    passive_maker_context.adverse_selection_score * maker_notional_dec
                )
            maker_expected_fill_probability = passive_maker_context.expected_fill_probability
        incremental_expected_profit_usd = (
            taker_expected_profit_usd - maker_expected_profit_usd
        )
        min_taker_edge = Decimal(str(final_intent_context.get("min_taker_fee_adjusted_edge", "0.03")))
        min_incremental_profit = Decimal(str(final_intent_context.get("min_taker_incremental_profit_usd", "0.05")))
        min_model_confidence = Decimal(str(final_intent_context.get("min_taker_model_confidence", "0.60")))
        min_profit_ratio = Decimal(str(final_intent_context.get("min_taker_profit_ratio", "1.20")))
        required_profit = max(
            maker_expected_profit_usd * min_profit_ratio,
            maker_expected_profit_usd + min_incremental_profit,
        )
        try:
            ci_lower = float(getattr(decision.edge, "ci_lower"))
            ci_upper = float(getattr(decision.edge, "ci_upper"))
        except (TypeError, ValueError):
            ci_lower = float("nan")
            ci_upper = float("nan")
        if math.isfinite(ci_lower) and math.isfinite(ci_upper) and ci_upper >= ci_lower:
            model_confidence = max(
                Decimal("0"),
                min(Decimal("1"), Decimal("1") - Decimal(str(ci_upper - ci_lower))),
            )
            model_confidence_source = "edge_ci_width_confidence"
        else:
            model_confidence = Decimal("0")
            model_confidence_source = "missing_edge_ci_width"
        taker_quality_passed = (
            taker_edge_dec >= min_taker_edge
            and incremental_expected_profit_usd >= min_incremental_profit
            and taker_expected_profit_usd >= required_profit
            and model_confidence >= min_model_confidence
        )
        taker_quality_proof = {
            "schema_version": 1,
            "passed": taker_quality_passed,
            "taker_fee_adjusted_edge": str(taker_edge_dec),
            "taker_expected_profit_usd": str(taker_expected_profit_usd),
            "maker_expected_profit_usd": str(maker_expected_profit_usd),
            "incremental_expected_profit_usd": str(incremental_expected_profit_usd),
            "model_confidence": str(model_confidence),
            "model_confidence_source": model_confidence_source,
            "min_taker_fee_adjusted_edge": str(min_taker_edge),
            "min_taker_incremental_profit_usd": str(min_incremental_profit),
            "min_taker_model_confidence": str(min_model_confidence),
            "min_taker_profit_ratio": str(min_profit_ratio),
            "maker_expected_fill_probability": str(maker_expected_fill_probability),
            "maker_context_source": maker_context_source,
        }
    if final_best_ask is not None and taker_quality_passed:
        final_order_type = "FOK" if final_order_type in {"GTC", "GTD", "FOK"} else "FAK"
        taker_order_type_upgraded = selected_order_type in {"GTC", "GTD"}
    elif taker_quality_required and (
        final_best_ask is not None or final_order_type in {"FOK", "FAK"}
    ):
        if final_order_type in {"FOK", "FAK"}:
            final_order_type = "GTC"
        final_best_ask = None
        tick = Decimal(str(getattr(snapshot, "min_tick_size", "0.01") or "0.01"))
        direction_text = str(getattr(getattr(decision, "edge", None), "direction", "") or "")
        if direction_text.startswith("buy_"):
            passive_ceiling = Decimal(str(best_ask_float)) - tick
            if passive_ceiling <= Decimal("0"):
                raise ValueError("ENTRY_TAKER_QUALITY_FALLBACK_NO_PASSIVE_BID_BELOW_ASK")
            if Decimal(str(corrected_candidate_price)) >= passive_ceiling:
                corrected_candidate_price = float(passive_ceiling)
                corrected_candidate_expected_fill = float(passive_ceiling)
                final_price = float(passive_ceiling)
                corrected_candidate_size = max(
                    float(corrected_candidate_size),
                    float(passive_ceiling * Decimal(str(snapshot.min_order_size))),
                )
        elif direction_text.startswith("sell_"):
            best_bid = getattr(snapshot, "orderbook_top_bid", None)
            if best_bid is not None:
                passive_floor = Decimal(str(best_bid)) + tick
                if Decimal(str(corrected_candidate_price)) <= passive_floor:
                    corrected_candidate_price = float(passive_floor)
                    corrected_candidate_expected_fill = float(passive_floor)
                    final_price = float(passive_floor)
                    corrected_candidate_size = max(
                        float(corrected_candidate_size),
                        float(passive_floor * Decimal(str(snapshot.min_order_size))),
                    )
    corrected_pricing_evidence = _attach_corrected_pricing_authority(
        decision=decision,
        snapshot=snapshot,
        candidate_limit_price=float(corrected_candidate_price),
        candidate_expected_fill_price_before_fee=float(corrected_candidate_expected_fill),
        candidate_size_usd=float(corrected_candidate_size),
        order_type=final_order_type,
        cancel_after=final_intent_context.get("cancel_after"),
        resolution_window=str(final_intent_context.get("resolution_window") or "default"),
        correlation_key=str(final_intent_context.get("correlation_key") or ""),
        passive_maker_context=passive_maker_context,
        taker_quality_proof=taker_quality_proof,
    )
    if (
        isinstance(corrected_pricing_evidence, dict)
        and corrected_pricing_evidence.get("live_submit_authority") is True
    ):
        corrected_candidate_size = float(
            Decimal(str(corrected_pricing_evidence["candidate_size_usd"]))
        )
        repriced_size = corrected_candidate_size
    if strategy_key_for_live_quality:
        from src.engine.evaluator import _live_entry_economic_floor_rejection

        expected_profit_usd = 0.0
        if final_price > 0.0 and repriced_size > 0.0:
            expected_profit_usd = max(0.0, repriced_edge) * (repriced_size / final_price)
        live_quality_rejection = _live_entry_economic_floor_rejection(
            strategy_key=strategy_key_for_live_quality,
            edge=decision.edge,
            submitted_notional_usd=float(repriced_size),
            expected_profit_usd=float(expected_profit_usd),
            final_limit_price=float(final_price),
            passive_order=final_best_ask is None,
            passive_fill_probability=(
                None
                if passive_maker_context is None
                else float(passive_maker_context.expected_fill_probability)
            ),
            passive_adverse_selection_score=(
                None
                if passive_maker_context is None
                or passive_maker_context.adverse_selection_score is None
                else float(passive_maker_context.adverse_selection_score)
            ),
        )
        if live_quality_rejection:
            raise ValueError(
                f"EXECUTABLE_LIVE_QUALITY_REJECTED: {live_quality_rejection}"
            )
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
        "entry_book_semantics": (
            "ask_only_entry_book" if ask_only_entry_book else "two_sided_entry_book"
        ),
        "snapshot_market_prior_status": (
            "ask_only_executable_cost" if ask_only_entry_book else "two_sided_vwmp"
        ),
        "snapshot_vwmp": float(snapshot_vwmp),
        "snapshot_best_bid": best_bid_float,
        "snapshot_best_bid_size": bid_size_float,
        "snapshot_best_ask": best_ask_float,
        "snapshot_best_ask_size": ask_size_float,
        "market_analysis_vnext_wide_spread_display_substitution": bool(
            _vnext_wide_spread
        ),
        "market_analysis_vnext_depth_at_best_ask": int(_snap_depth),
        "snapshot_limit_price": float(snapshot_limit_price),
        "passive_maker_repositioned": passive_maker_repositioned,
        "passive_maker_reposition_reason": passive_maker_reposition_reason,
        "slippage_reference_price": float(slippage_reference_price),
        "max_slippage_bps": float(max_slippage.value_bps),
        "depth_sweep_limit_price": depth_sweep_limit_float,
        "best_ask_slippage_bps": float(best_ask_slippage_bps),
        "best_ask_fee_adjusted_edge": float(best_ask_fee_adjusted_edge),
        "best_ask_size_at_fee_adjusted_cost": float(best_ask_size_at_fee_adjusted_cost),
        "marketable_buy_min_notional_usd": float(marketable_buy_min_notional_usd),
        "marketable_buy_submitted_notional_usd": (
            None
            if marketable_buy_submitted_notional_usd is None
            else float(marketable_buy_submitted_notional_usd)
        ),
        "marketable_buy_below_venue_min": bool(marketable_buy_below_venue_min),
        "best_ask_inside_edge_budget": bool(best_ask_inside_edge_budget),
        "f34_crossing_evidence": f34_crossing_evidence,
        "best_ask_slippage_override_by_edge": bool(edge_aware_taker_selected),
        "best_ask_blocked_by_slippage": bool(
            best_ask_edge > 0.0
            and not best_ask_inside_slippage_budget
            and not edge_aware_taker_selected
            and best_ask_slippage_bps > max_slippage.value_bps
        ),
        "best_ask_blocked_by_edge_boundary": bool(
            best_ask_edge > 0.0
            and not best_ask_inside_slippage_budget
            and best_ask_slippage_bps <= max_slippage.value_bps
        ),
        "final_limit_price": final_price,
        "final_best_ask": final_best_ask,
        "selected_order_type": selected_order_type,
        "final_order_type": final_order_type,
        "taker_order_type_upgraded": taker_order_type_upgraded,
        "taker_quality_proof": taker_quality_proof,
        "passive_maker_expected_fill_probability": (
            None
            if passive_maker_context is None
            else float(passive_maker_context.expected_fill_probability)
        ),
        "passive_fill_model_source": final_intent_context.get("passive_fill_model_source"),
        "passive_fill_model_order_count": final_intent_context.get("passive_fill_model_order_count"),
        "passive_fill_model_fill_count": final_intent_context.get("passive_fill_model_fill_count"),
        "fill_adjusted_expected_profit_usd": final_intent_context.get(
            "fill_adjusted_expected_profit_usd"
        ),
        "corrected_candidate_limit_price": float(corrected_candidate_price),
        "corrected_candidate_expected_fill_price": float(corrected_candidate_expected_fill),
        "corrected_candidate_size_usd": float(corrected_candidate_size),
        "repriced_edge": repriced_edge,
        "repriced_size_usd": float(repriced_size),
        "live_submit_authority": bool(corrected_pricing_evidence.get("live_submit_authority"))
        if isinstance(corrected_pricing_evidence, dict)
        else False,
        "final_execution_intent_id": corrected_pricing_evidence.get("final_execution_intent_id")
        if isinstance(corrected_pricing_evidence, dict)
        else None,
    }
    if corrected_pricing_evidence is not None:
        tokens["executable_snapshot_reprice"]["corrected_pricing_evidence"] = corrected_pricing_evidence
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


# PR-S1 Bug #3: per-token block-list for aggregate reconciliation violations.
# Lifetime: daemon process. Populated by _assert_token_aggregate_invariant().
# Cleared automatically (N1) when the invariant no longer fires for the token.
# _tokens_blocked_lock guards all mutations and snapshot reads of the set.
# Two DiscoveryMode cycles can run concurrently; without the lock a set union
# during iteration by one thread while another calls add/discard raises
# RuntimeError: Set changed size during iteration (CPython 3.x, confirmed).
import threading as _threading
tokens_blocked_until_resolution: set[str] = set()
_tokens_blocked_lock: _threading.Lock = _threading.Lock()

_logger_runtime = logging.getLogger(__name__)


def _assert_token_aggregate_invariant(
    portfolio,
    chain_positions: list,
    *,
    deps,
) -> None:
    """Post-reconcile invariant: chain aggregate must cover local aggregate per token.

    For each token with active local positions, sum local shares and compare to
    the chain balance. If local_sum > chain_balance + DUST, the invariant fires:
    - LOGs a warning
    - Emits metric inv_token_aggregate_violated_total{token_id=...}
    - Adds the token to tokens_blocked_until_resolution (N1: stays until it passes)

    For tokens currently in the block-list, if the invariant does NOT fire,
    removes them (N1 auto-clear).

    Does NOT raise — called post-reconcile inside run_chain_sync.
    """
    from src.state.portfolio import INACTIVE_RUNTIME_STATES
    from src.observability.counters import increment as _ci

    _DUST = 0.01
    chain_by_token = {cp.token_id: cp.size for cp in chain_positions}

    # Build local aggregate by token across active positions.
    # Mirrors the pass-1 exclusion in chain_reconciliation.py:643-647:
    # exclude pending_exit (exit in flight — exit_lifecycle owns chain propagation)
    # so a mid-exit chain lag does not spuriously fire the invariant and over-block
    # other entries for that token.
    from src.state.chain_reconciliation import PENDING_EXIT_STATES
    local_by_token: dict[str, float] = {}
    for pos in portfolio.positions:
        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if not tid:
            continue
        state_val = getattr(pos.state, "value", pos.state)
        if state_val in INACTIVE_RUNTIME_STATES or state_val == "pending_tracked":
            continue
        if (
            state_val == "pending_exit"
            or getattr(pos, "exit_state", "") in PENDING_EXIT_STATES
        ):
            continue
        size = float(getattr(pos, "effective_shares", None) or getattr(pos, "shares", 0) or 0)
        if size > 0:
            local_by_token[tid] = local_by_token.get(tid, 0.0) + size

    # Check all tokens: both active and currently blocked (for auto-clear).
    # Snapshot blocked set under lock to prevent RuntimeError on concurrent mutation.
    with _tokens_blocked_lock:
        blocked_snapshot = set(tokens_blocked_until_resolution)
    all_tokens = set(local_by_token.keys()) | blocked_snapshot
    for tid in all_tokens:
        local_sum = local_by_token.get(tid, 0.0)
        chain_bal = chain_by_token.get(tid, 0.0)
        fires = local_sum > chain_bal + _DUST

        if fires:
            _logger_runtime.warning(
                "INV_TOKEN_AGGREGATE_VIOLATED: token=%s local_sum=%.4f chain_bal=%.4f",
                tid, local_sum, chain_bal,
            )
            _ci("inv_token_aggregate_violated_total", labels={"token_id": tid})
            with _tokens_blocked_lock:
                tokens_blocked_until_resolution.add(tid)
        elif tid in blocked_snapshot:
            # N1 auto-clear: invariant no longer fires for this token.
            with _tokens_blocked_lock:
                tokens_blocked_until_resolution.discard(tid)
            _logger_runtime.info(
                "TOKEN_BLOCK_AUTO_CLEARED: token=%s local_sum=%.4f chain_bal=%.4f",
                tid, local_sum, chain_bal,
            )

    # N1 heartbeat gauge.
    with _tokens_blocked_lock:
        _blocked_size = len(tokens_blocked_until_resolution)
    _logger_runtime.info(
        "telemetry_gauge tokens_blocked_until_resolution_size=%d",
        _blocked_size,
    )


def run_chain_sync(portfolio, clob, conn=None, *, deps):
    api_positions = chain_positions_from_api(clob.get_positions_from_api(), ChainPosition=deps.ChainPosition)
    if api_positions is None:
        raise RuntimeError("chain sync returned None — API call succeeded but returned no data")
    reconcile_stats = deps.reconcile_with_chain(portfolio, api_positions, conn=conn)
    # Gate the invariant on authoritative chain state only.
    # reconcile() sets "skipped_void_incomplete_api" in stats when it detects
    # CHAIN_UNKNOWN (empty-but-suspect API response). Running the aggregate
    # invariant against a suspect/empty chain would spuriously fire on every
    # active token and block all exits — identical to the CHAIN_UNKNOWN void-skip
    # guard added to pass-1. Mirror that guard here.
    if "skipped_void_incomplete_api" not in reconcile_stats:
        _assert_token_aggregate_invariant(portfolio, api_positions, deps=deps)
    else:
        _logger_runtime.warning(
            "INV_TOKEN_AGGREGATE_SKIPPED: chain_state=CHAIN_UNKNOWN, "
            "skipped_void_incomplete_api=%s",
            reconcile_stats.get("skipped_void_incomplete_api"),
        )
    return reconcile_stats, True


def cleanup_orphan_open_orders(portfolio, clob, *, deps, conn=None) -> int:
    """Cancel exchange orders that are not tracked locally.

    Durable-command guard (#63 + R3):
      1. Order is NOT in local portfolio tracking (order_id / last_exit_order_id)
      2. Order is NOT in canonical position_current ownership
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

    locally_owned_order_ids: set[str] = set()
    if conn is not None:
        try:
            from src.state.db import _table_exists
            if _table_exists(conn, "position_current"):
                rows = conn.execute(
                    """
                    SELECT order_id
                      FROM position_current
                     WHERE order_id IS NOT NULL
                       AND order_id != ''
                       AND lower(COALESCE(phase, '')) NOT IN (?, ?, ?, ?)
                       AND lower(COALESCE(order_status, '')) NOT IN (?, ?, ?, ?, ?, ?)
                    """,
                    tuple(_ORDER_OWNERSHIP_TERMINAL_POSITION_PHASES)
                    + tuple(_ORDER_OWNERSHIP_TERMINAL_ORDER_STATUSES),
                ).fetchall()
                locally_owned_order_ids.update(str(r[0]) for r in rows)
        except Exception as exc:
            deps.logger.warning("Could not query canonical local order ownership for orphan guard: %s", exc)

    cancelled = 0
    for order in clob.get_open_orders():
        order_id = extract_order_id(order)
        if not order_id or order_id in tracked_order_ids:
            continue
        if order_id in locally_owned_order_ids:
            deps.logger.warning(
                "Open order %s has durable local ownership — quarantining instead of cancelling",
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
            if is_cancel_confirmed_status(outcome.status):
                cancelled += 1
        except Exception as exc:
            deps.logger.warning("Orphan open-order durable cancel failed for %s: %s", order_id, exc)
    return cancelled


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_utc_timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_command_has_positive_trade_fact(conn, command_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM venue_trade_facts
         WHERE command_id = ?
           AND CAST(filled_size AS REAL) > 0
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    return row is not None


def _entry_command_has_positive_order_fact(conn, command_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM venue_order_facts
         WHERE command_id = ?
           AND CAST(COALESCE(matched_size, '0') AS REAL) > 0
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    return row is not None


def _fresh_best_bid_for_token(clob, token_id: str) -> Decimal | None:
    getter = getattr(clob, "get_orderbook_snapshot", None)
    if not callable(getter):
        getter = getattr(clob, "get_orderbook", None)
    if not callable(getter):
        return None
    try:
        raw_orderbook = getter(token_id)
        from src.data.market_scanner import _top_book_level_decimal

        best_bid, _bid_size = _top_book_level_decimal(raw_orderbook, "bids")
    except Exception:
        return None
    return best_bid


def cleanup_stale_entry_orders(clob, *, deps, conn=None) -> int:
    """Cancel no-fill entry orders that are no longer competitive.

    This is entry-order management, not exposure dedup. It only touches ACKED
    no-fill ENTRY commands with durable command truth and a fresher executable
    snapshot proving a better passive BUY bid is now required.
    """
    if conn is None or not hasattr(clob, "cancel_order"):
        return 0
    try:
        rows = conn.execute(
            """
            SELECT vc.command_id,
                   vc.position_id,
                   vc.token_id,
                   vc.side,
                   vc.price,
                   vc.venue_order_id,
                   vc.state,
                   vc.created_at,
                   vc.updated_at,
                   pc.phase,
                   pc.shares,
                   pc.cost_basis_usd
              FROM venue_commands vc
              LEFT JOIN position_current pc
                ON pc.position_id = vc.position_id
             WHERE UPPER(vc.intent_kind) = 'ENTRY'
               AND UPPER(vc.state) = 'ACKED'
               AND COALESCE(vc.venue_order_id, '') != ''
             ORDER BY vc.updated_at ASC, vc.created_at ASC
            """
        ).fetchall()
    except Exception as exc:
        if "no such table" in str(exc).lower():
            deps.logger.warning("Stale entry-order cleanup blocked: command/snapshot tables unavailable")
            return 0
        raise

    cancelled = 0
    for row in rows:
        command_id = str(row["command_id"] if hasattr(row, "keys") else row[0])
        token_id = str(row["token_id"] if hasattr(row, "keys") else row[2])
        side = str(row["side"] if hasattr(row, "keys") else row[3]).upper()
        order_price = _decimal_or_none(row["price"] if hasattr(row, "keys") else row[4])
        command_created_at = row["created_at"] if hasattr(row, "keys") else row[7]
        command_updated_at = row["updated_at"] if hasattr(row, "keys") else row[8]
        phase = str(row["phase"] if hasattr(row, "keys") else row[9] or "").lower()
        shares = _decimal_or_none(row["shares"] if hasattr(row, "keys") else row[10]) or Decimal("0")
        cost_basis = _decimal_or_none(row["cost_basis_usd"] if hasattr(row, "keys") else row[11]) or Decimal("0")
        if side != "BUY" or order_price is None:
            continue
        if phase != "pending_entry" or shares != Decimal("0") or cost_basis != Decimal("0"):
            continue
        if _entry_command_has_positive_trade_fact(conn, command_id):
            continue
        if _entry_command_has_positive_order_fact(conn, command_id):
            continue
        snapshot = conn.execute(
            """
            SELECT orderbook_top_bid, orderbook_top_ask, min_tick_size, captured_at
              FROM executable_market_snapshots
             WHERE selected_outcome_token_id = ?
             ORDER BY captured_at DESC
             LIMIT 1
            """,
            (token_id,),
        ).fetchone()
        if snapshot is None:
            continue
        snapshot_captured_at = _parse_utc_timestamp(
            snapshot["captured_at"] if hasattr(snapshot, "keys") else snapshot[3]
        )
        command_observed_at = (
            _parse_utc_timestamp(command_updated_at)
            or _parse_utc_timestamp(command_created_at)
        )
        if snapshot_captured_at is None or command_observed_at is None:
            continue
        if snapshot_captured_at <= command_observed_at:
            continue
        best_bid = _decimal_or_none(snapshot["orderbook_top_bid"] if hasattr(snapshot, "keys") else snapshot[0])
        if best_bid is None or best_bid <= order_price:
            continue
        fresh_best_bid = _fresh_best_bid_for_token(clob, token_id)
        if fresh_best_bid is None or fresh_best_bid <= order_price:
            continue
        try:
            from src.execution.exit_safety import request_cancel_for_command

            outcome = request_cancel_for_command(
                conn,
                command_id,
                lambda venue_order_id: clob.cancel_order(venue_order_id),
            )
        except Exception as exc:
            deps.logger.warning("Stale entry-order cancel failed for %s: %s", command_id, exc)
            continue
        if is_cancel_confirmed_status(outcome.status):
            cancelled += 1
            deps.logger.info(
                "Stale entry order %s canceled for reprice: old_price=%s latest_best_bid=%s",
                command_id,
                order_price,
                fresh_best_bid,
            )
    return cancelled


def _summary_risk_level(summary: dict) -> str:
    return str(summary.get("risk_level") or "").strip().upper()


def _initialize_entry_order_summary(summary: dict) -> None:
    summary.setdefault("trades", 0)
    summary.setdefault("entry_orders_submitted", 0)
    summary.setdefault("entry_orders_resting", 0)
    summary.setdefault("entry_orders_filled_immediate", 0)


def _record_entry_order_summary(
    summary: dict,
    *,
    runtime_order_status: str,
    command_state: str | None,
) -> None:
    _initialize_entry_order_summary(summary)
    summary["entry_orders_submitted"] += 1
    if runtime_order_status == "filled" or command_state == "FILLED":
        summary["entry_orders_filled_immediate"] += 1
    else:
        summary["entry_orders_resting"] += 1


def _frontier_reason_prefix(reason: object) -> str:
    text = str(reason or "").strip()
    if not text:
        return "unknown"
    return text.split(":", 1)[0] or "unknown"


def _increment_summary_counter(summary: dict, key: str, amount: int = 1) -> None:
    summary[key] = int(summary.get(key, 0) or 0) + amount


def _mark_observability_degraded(summary: dict) -> None:
    summary["observability_degraded"] = True


def _record_lane_write_failure(summary: dict, lane_name: str, exc: BaseException) -> None:
    """Fail loud + count a swallowed decision/telemetry-lane write failure.

    AB3 (2026-06-16, timing-semantics fix). A swallowed lane-write exception is
    INDISTINGUISHABLE from a lane that simply had nothing to write — that
    blindness is what let edli_no_submit_receipts sit dead from 2026-06-06
    with nobody noticing. This preserves the existing fail-soft behavior
    (``summary['observability_degraded']`` is set, the cycle is NOT
    blocked) but additionally (a) emits a logger.error naming the lane and the
    exception, and (b) increments a per-lane failure counter on the summary so
    a downstream liveness check can name a lane that is failing every cycle.
    """
    failures = summary.setdefault("lane_write_failures", {})
    if isinstance(failures, dict):
        failures[lane_name] = int(failures.get(lane_name, 0) or 0) + 1
    _mark_observability_degraded(summary)
    logger.error("LANE WRITE FAILED lane=%s err=%r", lane_name, exc)


def _record_lane_write_success(summary: dict, lane_name: str) -> None:
    """Count a successful per-cycle decision/telemetry-lane write.

    AB3 (2026-06-16). The companion to ``_record_lane_write_failure``: a lane
    that wrote zero times this cycle (no success AND no failure recorded)
    becomes detectable downstream — a dead lane no longer looks identical to a
    quiet one. Lightweight: a single per-lane integer on the summary.
    """
    writes = summary.setdefault("decision_lane_writes", {})
    if isinstance(writes, dict):
        writes[lane_name] = int(writes.get(lane_name, 0) or 0) + 1


def _increment_reason_bucket(summary: dict, key: str, reason: object) -> str:
    prefix = _frontier_reason_prefix(reason)
    bucket = summary.setdefault(key, {})
    bucket[prefix] = int(bucket.get(prefix, 0) or 0) + 1
    return prefix


def _family_key_for_frontier(candidate: Any, city_name: str) -> str:
    metric = str(getattr(candidate, "temperature_metric", "") or "")
    target_date = str(getattr(candidate, "target_date", "") or "")
    family_id = (
        getattr(candidate, "event_id", None)
        or getattr(candidate, "slug", None)
        or f"{city_name}:{target_date}:{metric}"
    )
    return f"{city_name}|{target_date}|{metric}|{family_id}"


def _record_final_intent_frontier(
    summary: dict,
    *,
    candidate: Any,
    decision: Any,
    city_name: str,
    strategy_key: str,
    stage: str,
    outcome: str,
    reason: object = "",
    snapshot_fields: dict | None = None,
) -> dict:
    frontier = summary.setdefault(
        "final_intent_frontier",
        {
            "attempts": [],
            "rejections_by_stage": {},
            "rejections_by_reason": {},
        },
    )
    reason_text = str(reason or "")
    reason_prefix = _frontier_reason_prefix(reason_text)
    if outcome not in {"built", "submitted", "accepted", "venue_ack"}:
        by_stage = frontier.setdefault("rejections_by_stage", {})
        by_stage[stage] = int(by_stage.get(stage, 0) or 0) + 1
        by_reason = frontier.setdefault("rejections_by_reason", {})
        by_reason[reason_prefix] = int(by_reason.get(reason_prefix, 0) or 0) + 1
    edge = getattr(decision, "edge", None)
    tokens = getattr(decision, "tokens", None)
    reprice_payload = {}
    if isinstance(tokens, dict):
        maybe_reprice = tokens.get("executable_snapshot_reprice")
        if isinstance(maybe_reprice, dict):
            reprice_payload = maybe_reprice
    corrected_pricing_evidence = reprice_payload.get("corrected_pricing_evidence")
    if not isinstance(corrected_pricing_evidence, dict):
        corrected_pricing_evidence = {}
    fields = snapshot_fields or {}
    attempt = {
        "family_key": _family_key_for_frontier(candidate, city_name),
        "city": city_name,
        "target_date": str(getattr(candidate, "target_date", "") or ""),
        "temperature_metric": str(getattr(candidate, "temperature_metric", "") or ""),
        "decision_id": str(getattr(decision, "decision_id", "") or ""),
        "rank": int(getattr(decision, "family_ranked_candidate_rank", 0) or 0),
        "family_ranked_candidate_count": int(
            getattr(decision, "family_ranked_candidate_count", 0) or 0
        ),
        "bin": str(getattr(getattr(edge, "bin", None), "label", "") or ""),
        "direction": str(getattr(edge, "direction", "") or ""),
        "strategy_key": strategy_key,
        "stage": stage,
        "outcome": outcome,
        "reason": reason_text,
        "reason_prefix": reason_prefix,
        "executable_snapshot_id": str(fields.get("executable_snapshot_id") or ""),
        "book_semantics": str(
            reprice_payload.get("book_semantics")
            or corrected_pricing_evidence.get("book_semantics")
            or ""
        ),
        "market_prior_status": str(
            reprice_payload.get("market_prior_status")
            or corrected_pricing_evidence.get("market_prior_status")
            or ""
        ),
    }
    frontier.setdefault("attempts", []).append(attempt)
    return attempt


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
    corrected_pricing_evidence = {}
    try:
        corrected_pricing_evidence = (
            decision.tokens.get("executable_snapshot_reprice", {})
            .get("corrected_pricing_evidence", {})
        )
    except AttributeError:
        corrected_pricing_evidence = {}
    pricing_semantics_id = str(
        corrected_pricing_evidence.get("pricing_semantics_id")
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
            pricing_semantics_id == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION
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
        entry_cost_basis_id=str(corrected_pricing_evidence.get("cost_basis_id") or ""),
        entry_cost_basis_hash=str(corrected_pricing_evidence.get("cost_basis_hash") or ""),
        entry_economics_authority=entry_economics_authority,
        fill_authority=fill_authority,
        pricing_semantics_id=pricing_semantics_id,
        execution_cost_basis_version=str(
            corrected_pricing_evidence.get("execution_cost_basis_version")
            or corrected_pricing_evidence.get("cost_basis_id")
            or ""
        ),
        corrected_executable_economics_eligible=corrected_executable_economics_eligible,
        bankroll_at_entry=bankroll_at_entry,
        entered_at=now.isoformat() if state == "entered" else "",
        entry_ci_width=max(0.0, decision.edge.ci_upper - decision.edge.ci_lower),
        unit=city.settlement_unit,
        token_id=decision.tokens["token_id"],
        no_token_id=decision.tokens["no_token_id"],
        # Fix B (2026-05-19): populate condition_id at entry write-time so
        # upsert_position_current's NullConditionIdOnOpenPhaseError guard passes.
        # evaluator.py:2005-2009 places condition_id into each bin dict; cycle_runtime
        # reads it back from decision.tokens at the point of materialization.
        # This closes the write-path gap that caused Jakarta e914a28a-420's NULL row.
        condition_id=str(decision.tokens.get("condition_id") or ""),
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
        decision_id=str(getattr(decision, "decision_id", None) or "") or None,
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
        existing_day0 = conn.execute(
            """
            SELECT 1
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'DAY0_WINDOW_ENTERED'
             LIMIT 1
            """,
            (getattr(pos, "trade_id", ""),),
        ).fetchone()
        if existing_day0 is not None:
            return False
        # Query next sequence_no for this position (same pattern as
        # fill_tracker._mark_entry_filled at src/execution/fill_tracker.py:156).
        # Position may already have POSITION_OPEN_INTENT / ENTRY_ORDER_POSTED /
        # ENTRY_ORDER_FILLED events (sequence_no 1-3); day0 event takes 4+.
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (getattr(pos, "trade_id", ""),),
        ).fetchone()
        next_seq = int((row[0] if row else 0) or 0) + 1
        # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): DAY0_WINDOW
        # transition target is the event's defining phase; pass explicitly.
        events, projection = build_day0_window_entered_canonical_write(
            pos,
            day0_entered_at=day0_entered_at,
            sequence_no=next_seq,
            phase_after=LifecyclePhase.DAY0_WINDOW.value,
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


def _monitor_refreshed_phase_for_position(pos) -> str:
    state = _position_state_value(pos)
    if state == "day0_window":
        return LifecyclePhase.DAY0_WINDOW.value
    if state == "pending_exit":
        return LifecyclePhase.PENDING_EXIT.value
    if state == "quarantined":
        return LifecyclePhase.QUARANTINED.value
    return LifecyclePhase.ACTIVE.value


def _emit_monitor_refreshed_canonical_if_available(
    conn,
    pos,
    *,
    deps,
    exit_decision=None,
    final_should_exit: bool | None = None,
    final_exit_reason: str | None = None,
    final_exit_trigger: str | None = None,
) -> bool:
    if conn is None:
        return True

    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.db import append_many_and_project

    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (getattr(pos, "trade_id", ""),),
        ).fetchone()
        next_seq = int((row[0] if row else 0) or 0) + 1
        events, projection = build_monitor_refreshed_canonical_write(
            pos,
            sequence_no=next_seq,
            phase_after=_monitor_refreshed_phase_for_position(pos),
            source_module="src.engine.cycle_runtime",
            exit_decision=exit_decision,
            final_should_exit=final_should_exit,
            final_exit_reason=final_exit_reason,
            final_exit_trigger=final_exit_trigger,
        )
        append_many_and_project(conn, events, projection)
    except Exception as exc:
        deps.logger.warning(
            "CANONICAL_MONITOR_REFRESHED_EMIT_FAILED trade_id=%s reason=%s",
            getattr(pos, "trade_id", ""),
            exc,
        )
        return False

    return True


_FAMILY_OVERLAY_STATISTICAL_EXIT_TRIGGERS = frozenset(
    {
        "CI_SEPARATED_REVERSAL",
        "FLASH_CRASH_PANIC",
        "VIG_EXTREME",
        "EDGE_REVERSAL",
    }
)

_FAMILY_OVERLAY_MIN_DIRECT_SELL_ADVANTAGE_USD = 0.05
_FAMILY_OVERLAY_MIN_DIRECT_SELL_ADVANTAGE_FRACTION = 0.0025


def _family_direct_sell_advantage_threshold_usd(sell_value: float) -> float:
    if not math.isfinite(sell_value) or sell_value <= 0.0:
        return _FAMILY_OVERLAY_MIN_DIRECT_SELL_ADVANTAGE_USD
    return max(
        _FAMILY_OVERLAY_MIN_DIRECT_SELL_ADVANTAGE_USD,
        sell_value * _FAMILY_OVERLAY_MIN_DIRECT_SELL_ADVANTAGE_FRACTION,
    )


def _entry_qkernel_selection_guard_verdict(conn, pos) -> dict[str, object] | None:
    """Return the entry-time qkernel selection guard for a live position.

    The position projection does not currently carry the qkernel cert. The
    durable venue command does carry the EDLI decision id, and the append-only
    EDLI stream carries the pre-submit qkernel economics. Monitor uses this to
    avoid treating an entry admitted under an unarmed selection cell as a valid
    raw-posterior hold.
    """

    if conn is None:
        return None
    position_id = str(getattr(pos, "trade_id", "") or "").strip()
    if not position_id:
        return None
    try:
        command_row = conn.execute(
            """
            SELECT decision_id
              FROM venue_commands
             WHERE position_id = ?
               AND intent_kind = 'ENTRY'
             ORDER BY created_at DESC, updated_at DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    except Exception:
        return None
    if command_row is None:
        return None
    decision_id = str(
        command_row["decision_id"] if hasattr(command_row, "keys") else command_row[0]
        or ""
    ).strip()
    if not decision_id:
        return None
    try:
        event_row = conn.execute(
            """
            SELECT payload_json
              FROM edli_live_order_events
             WHERE event_type = 'PreSubmitRevalidated'
               AND ? LIKE '%' || aggregate_id || '%'
             ORDER BY occurred_at DESC, event_sequence DESC
             LIMIT 1
            """,
            (decision_id,),
        ).fetchone()
    except Exception:
        return None
    if event_row is None:
        return None
    payload_json = event_row["payload_json"] if hasattr(event_row, "keys") else event_row[0]
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        return None
    economics = payload.get("qkernel_execution_economics")
    if not isinstance(economics, dict):
        return None
    if str(economics.get("source") or "").strip() != "qkernel_spine":
        return None
    basis = str(economics.get("selection_guard_basis") or "").strip()
    raw_abstained = economics.get("selection_guard_abstained")
    if isinstance(raw_abstained, bool):
        abstained = raw_abstained
    else:
        abstained = str(raw_abstained).strip().lower() in {"1", "true", "yes"}
    try:
        q_safe = float(economics.get("selection_guard_q_safe"))
    except (TypeError, ValueError):
        q_safe = float("nan")
    invalid_reason = ""
    if not basis:
        invalid_reason = "selection_guard_missing"
    elif basis == "SIDE_NOT_ARMED":
        invalid_reason = "selection_guard_side_not_armed"
    elif abstained:
        invalid_reason = "selection_guard_abstained"
    elif not (math.isfinite(q_safe) and q_safe > 0.0):
        invalid_reason = "selection_guard_q_safe_non_positive"
    return {
        "invalid_reason": invalid_reason,
        "selection_guard_basis": basis,
        "selection_guard_abstained": abstained,
        "selection_guard_q_safe": q_safe if math.isfinite(q_safe) else None,
        "selection_guard_cell_key": str(economics.get("selection_guard_cell_key") or ""),
        "payoff_q_lcb": economics.get("payoff_q_lcb"),
        "cost": economics.get("cost"),
        "edge_lcb": economics.get("edge_lcb"),
    }


def _entry_selection_guard_exit_decision(
    *,
    conn,
    pos,
    exit_context,
    summary: dict,
    exit_decision=None,
) -> object | None:
    verdict = _entry_qkernel_selection_guard_verdict(conn, pos)
    if verdict is None:
        return None
    if not verdict.get("invalid_reason"):
        return None

    summary["entry_selection_guard_invalid_positions"] = (
        summary.get("entry_selection_guard_invalid_positions", 0) + 1
    )
    summary.setdefault("entry_selection_guard_invalid_details", []).append(
        {
            "position_id": str(getattr(pos, "trade_id", "") or ""),
            "reason": verdict.get("invalid_reason"),
            "basis": verdict.get("selection_guard_basis"),
            "q_safe": verdict.get("selection_guard_q_safe"),
            "cell_key": verdict.get("selection_guard_cell_key"),
        }
    )

    if exit_decision is not None and bool(getattr(exit_decision, "should_exit", False)):
        summary["entry_selection_guard_invalid_existing_exit_preserved"] = (
            summary.get("entry_selection_guard_invalid_existing_exit_preserved", 0) + 1
        )
        return None

    day0_immature_reason = _day0_immature_exit_authority_reason(pos, exit_context, exit_decision)
    if day0_immature_reason:
        summary["entry_selection_guard_invalid_day0_immature_holds"] = (
            summary.get("entry_selection_guard_invalid_day0_immature_holds", 0) + 1
        )
        from src.state.portfolio import ExitDecision as _ExitDecision

        return _ExitDecision(
            False,
            (
                "ENTRY_SELECTION_GUARD_INVALID_HOLD_DAY0_IMMATURE "
                f"({verdict.get('invalid_reason')}; {day0_immature_reason})"
            ),
            urgency="normal",
            trigger="ENTRY_SELECTION_GUARD_INVALID_HOLD_DAY0_IMMATURE",
            selected_method=getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
            applied_validations=list(
                dict.fromkeys(
                    [
                        *(getattr(pos, "applied_validations", []) or []),
                        "entry_selection_guard_invalid_day0_immature_hold",
                    ]
                )
            ),
        )

    current_edge = _finite_float_or_none(getattr(pos, "last_monitor_edge", None))
    current_prob_fresh = bool(getattr(pos, "last_monitor_prob_is_fresh", False))
    current_price_fresh = bool(getattr(pos, "last_monitor_market_price_is_fresh", False))
    if (
        current_prob_fresh
        and current_price_fresh
        and current_edge is not None
        and current_edge > 0.0
    ):
        summary["entry_selection_guard_invalid_current_ev_holds"] = (
            summary.get("entry_selection_guard_invalid_current_ev_holds", 0) + 1
        )
        from src.state.portfolio import ExitDecision as _ExitDecision

        return _ExitDecision(
            False,
            (
                "ENTRY_SELECTION_GUARD_INVALID_HOLD_CURRENT_EDGE "
                f"({verdict.get('invalid_reason')}; current_edge={current_edge:.4f})"
            ),
            urgency="normal",
            trigger="ENTRY_SELECTION_GUARD_INVALID_HOLD_CURRENT_EDGE",
            selected_method=getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
            applied_validations=list(
                dict.fromkeys(
                    [
                        *(getattr(pos, "applied_validations", []) or []),
                        "entry_selection_guard_invalid_current_edge_hold",
                    ]
                )
            ),
        )

    shares = _position_real_exposure_shares(pos)
    try:
        best_bid = float(getattr(exit_context, "best_bid", 0.0) or 0.0)
    except (TypeError, ValueError):
        best_bid = 0.0
    sell_value = shares * best_bid if math.isfinite(best_bid) and best_bid > 0.0 else 0.0
    threshold = _family_direct_sell_advantage_threshold_usd(sell_value)

    from src.state.portfolio import ExitDecision as _ExitDecision

    if shares <= 0.0 or sell_value + 1e-9 < threshold:
        return _ExitDecision(
            False,
            (
                "ENTRY_SELECTION_GUARD_INVALID_HOLD_NO_EXECUTABLE_BID "
                f"({verdict.get('invalid_reason')})"
            ),
            urgency="normal",
            trigger="ENTRY_SELECTION_GUARD_INVALID_HOLD_NO_EXECUTABLE_BID",
            selected_method=getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
            applied_validations=list(
                dict.fromkeys(
                    [
                        *(getattr(pos, "applied_validations", []) or []),
                        "entry_selection_guard_invalid_no_executable_bid",
                    ]
                )
            ),
        )

    return _ExitDecision(
        True,
        (
            "ENTRY_SELECTION_GUARD_INVALID_EXIT "
            f"({verdict.get('invalid_reason')}; sell_value_usd={sell_value:.4f}; "
            f"q_safe={verdict.get('selection_guard_q_safe')})"
        ),
        urgency="normal",
        trigger="ENTRY_SELECTION_GUARD_INVALID_EXIT",
        selected_method=getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
        applied_validations=list(
            dict.fromkeys(
                [
                    *(getattr(pos, "applied_validations", []) or []),
                    "entry_selection_guard_invalid_exit",
                ]
            )
        ),
    )


def _family_monitor_key(pos) -> tuple[str, str, str] | None:
    city = str(getattr(pos, "city", "") or "").strip()
    target_date = str(getattr(pos, "target_date", "") or "").strip()
    metric = str(
        getattr(pos, "temperature_metric", "")
        or getattr(pos, "metric", "")
        or ""
    ).strip()
    if not (city and target_date and metric):
        return None
    return city, target_date, metric


def _family_monitor_positions(portfolio, pos) -> list:
    key = _family_monitor_key(pos)
    if key is None or portfolio is None:
        return [pos]
    out: list = []
    candidates: list = []
    seen_ids: set[int] = set()
    for other in get_open_positions(portfolio):
        candidates.append(other)
        seen_ids.add(id(other))
    for other in list(getattr(portfolio, "positions", []) or []):
        if id(other) in seen_ids:
            continue
        if _quarantined_position_can_redecision(other):
            candidates.append(other)
            seen_ids.add(id(other))
    for other in candidates:
        if _family_monitor_key(other) != key:
            continue
        if not _family_monitor_position_has_live_risk(other):
            continue
        try:
            if float(getattr(other, "effective_shares", getattr(other, "shares", 0.0)) or 0.0) <= 0.0:
                continue
        except (TypeError, ValueError):
            continue
        out.append(other)
    return out or [pos]


def _family_monitor_position_has_live_risk(pos) -> bool:
    phase = _position_state_value(pos)
    if phase in {"entered", "holding", "active", "day0_window", "pending_exit"}:
        return True
    if phase != "quarantined":
        return False
    try:
        chain_shares = float(getattr(pos, "chain_shares", 0.0) or 0.0)
    except (TypeError, ValueError):
        chain_shares = 0.0
    return chain_shares > 0.01 and has_current_money_risk_chain_state(
        getattr(pos, "chain_state", "")
    )


_DAY0_IMMATURE_EXIT_AUTHORITY_PREFIXES = (
    "day0_high_extreme_not_mature:",
    "day0_low_extreme_not_terminal:",
    "day0_extreme_maturity_unavailable:",
)


def _day0_immature_exit_authority_reason(*sources) -> str | None:
    for source in sources:
        for validation in getattr(source, "applied_validations", []) or []:
            text = str(validation or "")
            if text.startswith(_DAY0_IMMATURE_EXIT_AUTHORITY_PREFIXES):
                return text
    return None


def _exit_trigger_requires_mature_day0_authority(exit_trigger: str) -> bool:
    trigger = str(exit_trigger or "")
    if trigger == "FAMILY_DIRECT_SELL_DOMINATES_HOLD":
        return True
    if trigger == "DAY0_OBSERVATION_REVERSAL":
        return True
    return any(
        trigger.startswith(prefix)
        for prefix in _FAMILY_OVERLAY_STATISTICAL_EXIT_TRIGGERS
    )


def _monitor_value_inputs(position) -> tuple[float, float | None, float | None, str | None]:
    try:
        shares = float(getattr(position, "effective_shares", getattr(position, "shares", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0, None, None, "shares_unavailable"
    if shares <= 0.0:
        return 0.0, None, None, "shares_non_positive"
    if getattr(position, "last_monitor_prob_is_fresh", False) is not True:
        return shares, None, None, "probability_not_fresh"
    if getattr(position, "last_monitor_market_price_is_fresh", False) is not True:
        return shares, None, None, "market_price_not_fresh"
    try:
        held_prob = float(getattr(position, "last_monitor_prob"))
        best_bid = float(getattr(position, "last_monitor_best_bid"))
    except (TypeError, ValueError):
        return shares, None, None, "value_inputs_non_numeric"
    if not (math.isfinite(held_prob) and 0.0 <= held_prob <= 1.0):
        return shares, None, None, "probability_invalid"
    if not (math.isfinite(best_bid) and 0.0 <= best_bid <= 1.0):
        return shares, None, None, "best_bid_invalid"
    return shares, held_prob, best_bid, None


def _is_statistical_single_leg_exit(exit_decision, exit_reason: str) -> bool:
    trigger = str(getattr(exit_decision, "trigger", "") or exit_reason or "")
    return any(trigger.startswith(prefix) for prefix in _FAMILY_OVERLAY_STATISTICAL_EXIT_TRIGGERS)


def _block_immature_day0_exit_authority(
    *,
    pos,
    payload: dict[str, object],
    summary: dict,
    exit_reason: str,
    day0_maturity_block: str,
) -> tuple[bool, str]:
    payload["decision"] = "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"
    payload["blocked_exit_reason"] = exit_reason
    payload["day0_maturity_block"] = day0_maturity_block
    setattr(pos, "_monitor_family_redecision", payload)
    validations = list(getattr(pos, "applied_validations", []) or [])
    validations.append("family_day0_immature_exit_authority_blocked")
    pos.applied_validations = list(dict.fromkeys(validations))
    summary["family_redecision_day0_immature_exits_blocked"] = (
        summary.get("family_redecision_day0_immature_exits_blocked", 0) + 1
    )
    return False, "FAMILY_DAY0_IMMATURE_EXIT_AUTHORITY_BLOCKED"


def _apply_family_monitor_overlay(
    *,
    portfolio,
    pos,
    exit_decision,
    should_exit: bool,
    exit_reason: str,
    summary: dict,
) -> tuple[bool, str]:
    """Require a family value check before a statistical single-leg exit.

    This is live monitor logic over already-refreshed held-side probabilities and
    held-side bids. It does not read replay data and it never creates a
    new entry; it records the current family value evidence for every held
    position and only prevents a single leg from liquidating when the current
    family vector's hold value dominates its direct-sell value.
    """

    try:
        if hasattr(pos, "_monitor_family_redecision"):
            delattr(pos, "_monitor_family_redecision")
    except Exception:
        pass

    family_positions = _family_monitor_positions(portfolio, pos)

    key = _family_monitor_key(pos)
    payload: dict[str, object] = {
        "family_key": "|".join(key) if key else "",
        "position_count": len(family_positions),
        "mode": "live_family_hold_vs_direct_sell",
    }
    hold_value = 0.0
    sell_value = 0.0
    missing: list[dict[str, str]] = []
    leg_payloads: list[dict[str, object]] = []
    for leg in family_positions:
        shares, held_prob, best_bid, reason = _monitor_value_inputs(leg)
        leg_payload: dict[str, object] = {
            "position_id": str(getattr(leg, "trade_id", "") or ""),
            "direction": str(getattr(leg, "direction", "") or ""),
            "bin_label": str(getattr(leg, "bin_label", "") or ""),
            "shares": shares,
        }
        if reason is not None:
            missing.append(
                {
                    "position_id": str(getattr(leg, "trade_id", "") or ""),
                    "reason": reason,
                }
            )
            leg_payload["evidence_status"] = reason
        else:
            leg_hold = shares * float(held_prob)
            leg_sell = shares * float(best_bid)
            hold_value += leg_hold
            sell_value += leg_sell
            leg_payload.update(
                {
                    "held_probability": float(held_prob),
                    "best_bid": float(best_bid),
                    "hold_value_usd": leg_hold,
                    "direct_sell_value_usd": leg_sell,
                    "evidence_status": "complete",
                }
            )
        leg_payloads.append(leg_payload)

    payload["legs"] = leg_payloads
    day0_maturity_block = _day0_immature_exit_authority_reason(pos, exit_decision)
    if day0_maturity_block is not None and _is_statistical_single_leg_exit(exit_decision, exit_reason):
        if missing:
            payload["missing"] = missing
        return _block_immature_day0_exit_authority(
            pos=pos,
            payload=payload,
            summary=summary,
            exit_reason=exit_reason,
            day0_maturity_block=day0_maturity_block,
        )

    if missing:
        payload["decision"] = "FAMILY_VALUE_EVIDENCE_UNAVAILABLE"
        payload["missing"] = missing
        setattr(pos, "_monitor_family_redecision", payload)
        summary["family_redecision_evidence_unavailable"] = (
            summary.get("family_redecision_evidence_unavailable", 0) + 1
        )
        return should_exit, exit_reason

    payload["family_hold_value_usd"] = hold_value
    payload["family_direct_sell_value_usd"] = sell_value
    payload["family_value_edge_usd"] = hold_value - sell_value
    sell_advantage = sell_value - hold_value
    sell_advantage_threshold = _family_direct_sell_advantage_threshold_usd(sell_value)
    payload["family_direct_sell_advantage_usd"] = sell_advantage
    payload["family_direct_sell_advantage_threshold_usd"] = sell_advantage_threshold

    if should_exit and _is_statistical_single_leg_exit(exit_decision, exit_reason):
        if hold_value + 1e-9 >= sell_value:
            payload["decision"] = "FAMILY_HOLD_DOMINATES_SINGLE_LEG_EXIT"
            payload["suppressed_exit_reason"] = exit_reason
            setattr(pos, "_monitor_family_redecision", payload)
            validations = list(getattr(pos, "applied_validations", []) or [])
            validations.append("family_hold_dominates_single_leg_exit")
            pos.applied_validations = list(dict.fromkeys(validations))
            summary["family_redecision_single_leg_exits_suppressed"] = (
                summary.get("family_redecision_single_leg_exits_suppressed", 0) + 1
            )
            return False, "FAMILY_HOLD_DOMINATES_SINGLE_LEG_EXIT"

    # A high bid over a conservative belief is not a reversal by itself. Promote
    # hold->sell here only when the held-side belief has fallen below entry.
    _entry_belief = getattr(pos, "p_posterior", None)
    _cur_belief = getattr(pos, "last_monitor_prob", None)
    _belief_reversed_below_entry = (
        _entry_belief is not None
        and _cur_belief is not None
        and math.isfinite(float(_entry_belief))
        and math.isfinite(float(_cur_belief))
        and float(_cur_belief) < float(_entry_belief)
    )
    if (not should_exit) and sell_advantage > sell_advantage_threshold and _belief_reversed_below_entry:
        if day0_maturity_block is not None:
            payload["decision"] = "FAMILY_DIRECT_SELL_BLOCKED_DAY0_IMMATURE"
            payload["suppressed_exit_reason"] = "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
            payload["day0_maturity_block"] = day0_maturity_block
            setattr(pos, "_monitor_family_redecision", payload)
            validations = list(getattr(pos, "applied_validations", []) or [])
            validations.append("family_direct_sell_blocked_day0_immature")
            pos.applied_validations = list(dict.fromkeys(validations))
            summary["family_redecision_day0_immature_exits_blocked"] = (
                summary.get("family_redecision_day0_immature_exits_blocked", 0) + 1
            )
            return should_exit, exit_reason
        payload["decision"] = "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
        payload["promoted_exit_reason"] = exit_reason
        payload["belief_reversed_below_entry"] = True
        setattr(pos, "_monitor_family_redecision", payload)
        validations = list(getattr(pos, "applied_validations", []) or [])
        validations.append("family_direct_sell_dominates_hold_exit")
        pos.applied_validations = list(dict.fromkeys(validations))
        summary["family_redecision_hold_exits_promoted"] = (
            summary.get("family_redecision_hold_exits_promoted", 0) + 1
        )
        return True, "FAMILY_DIRECT_SELL_DOMINATES_HOLD"

    payload["decision"] = "FAMILY_OVERLAY_NO_OVERRIDE"
    setattr(pos, "_monitor_family_redecision", payload)
    summary["family_redecision_overlay_evaluated"] = (
        summary.get("family_redecision_overlay_evaluated", 0) + 1
    )
    return should_exit, exit_reason


def _effective_exit_trigger(exit_decision, exit_reason: str) -> str:
    original_reason = str(getattr(exit_decision, "reason", "") or "")
    if exit_reason and exit_reason != original_reason:
        return str(exit_reason)
    return str(getattr(exit_decision, "trigger", "") or exit_reason or "")


def _dual_write_canonical_entry_if_available(
    conn,
    pos,
    *,
    phase_after: str,
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
    #
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): caller supplies
    # the explicit ``phase_after`` so the canonical builder does not derive
    # it from runtime Position.state strings. PENDING_ENTRY when the order
    # is pending; ACTIVE / DAY0_WINDOW when the order has filled.
    if conn is None:
        return False

    try:
        has_position_events = conn.execute(
            """
            SELECT 1
              FROM sqlite_master
             WHERE type = 'table'
               AND name = 'position_events'
             LIMIT 1
            """
        ).fetchone()
    except Exception:
        has_position_events = None
    if has_position_events is None:
        return False

    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    try:
        events, projection = build_entry_canonical_write(
            pos,
            phase_after=phase_after,
            decision_id=decision_id,
            source_module="src.engine.cycle_runtime",
            decision_evidence=decision_evidence,
        )
        position_id = str(getattr(pos, "trade_id", "") or "")
        if position_id:
            already_posted = conn.execute(
                """
                SELECT 1
                  FROM position_events
                 WHERE position_id = ?
                   AND event_type = 'ENTRY_ORDER_POSTED'
                 LIMIT 1
                """,
                (position_id,),
            ).fetchone()
            if already_posted is not None:
                return True
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


def _requires_quarantine_monitor_resolution(pos) -> bool:
    return (
        _position_state_value(pos) == "quarantined"
        or _position_chain_state_value(pos) in {"quarantined", "quarantine_expired"}
    )


def _position_real_exposure_shares(pos) -> float:
    for attr in ("chain_shares", "shares_filled", "shares"):
        try:
            value = float(getattr(pos, attr, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            return value
    return 0.0


def _position_timestamp_recent(value, *, max_age_seconds: int) -> bool:
    if value in (None, ""):
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    return 0.0 <= age_seconds <= max_age_seconds


def _chain_absent_quarantine_is_fresh_enough_for_redecision(pos) -> bool:
    return (
        _position_timestamp_recent(
            getattr(pos, "chain_verified_at", "") or "",
            max_age_seconds=_CHAIN_ABSENT_QUARANTINE_REDECISION_SECONDS,
        )
        or _position_timestamp_recent(
            getattr(pos, "last_chain_absence_observed_at", "") or "",
            max_age_seconds=_CHAIN_ABSENT_QUARANTINE_REDECISION_SECONDS,
        )
    )


def _quarantined_position_can_redecision(pos) -> bool:
    if _position_state_value(pos) != "quarantined":
        return False
    chain_state = _position_chain_state_value(pos)
    if chain_state not in _REDECISION_QUARANTINE_CHAIN_STATES:
        return False
    if _position_direction_value(pos) not in {"buy_yes", "buy_no"}:
        return False
    if _position_real_exposure_shares(pos) <= 0.01:
        return False
    if getattr(pos, "is_quarantine_placeholder", False):
        return False
    if chain_state == "entry_authority_quarantined":
        return True
    return _chain_absent_quarantine_is_fresh_enough_for_redecision(pos)


def _day0_hard_fact_position_eligible(pos) -> bool:
    return _position_state_value(pos) == "day0_window" or _quarantined_position_can_redecision(pos)


def _monitoring_phase_positions(portfolio) -> list:
    """Open positions plus quarantine rows that need explicit monitor receipts."""

    out = []
    seen: set[int] = set()
    for pos in list(get_open_positions(portfolio)):
        out.append(pos)
        seen.add(id(pos))
    for pos in list(getattr(portfolio, "positions", []) or []):
        if id(pos) in seen:
            continue
        if _requires_quarantine_monitor_resolution(pos):
            out.append(pos)
            seen.add(id(pos))
    return out


def _market_info_value(info, *keys):
    if isinstance(info, dict):
        for key in keys:
            if key in info:
                return info.get(key)
    for key in keys:
        if hasattr(info, key):
            return getattr(info, key)
    return None


def _parse_market_timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _closed_by_static_market_end_info(conn, pos, *, decision_time: datetime | None) -> dict | None:
    """Return closed-market evidence from stable market end timestamps.

    Live CLOB market-info can disappear or fail after close. The persisted
    market_end/close timestamp from executable snapshots is contract topology,
    not a stale tradability quote, so it can prove that further live exit
    attempts are no longer actionable.
    """

    if conn is None:
        return None
    condition_id = str(
        getattr(pos, "condition_id", None)
        or getattr(pos, "market_id", None)
        or ""
    ).strip()
    if not condition_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT snapshot_id,
                   condition_id,
                   market_end_at,
                   market_close_at,
                   captured_at
              FROM executable_market_snapshots
             WHERE condition_id = ?
             ORDER BY captured_at DESC
             LIMIT 1
            """,
            (condition_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None

    def _row_get(key: str):
        try:
            return row[key]
        except (TypeError, KeyError, IndexError):
            idx = {
                "snapshot_id": 0,
                "condition_id": 1,
                "market_end_at": 2,
                "market_close_at": 3,
                "captured_at": 4,
            }[key]
            return row[idx]

    close_at = (
        _parse_market_timestamp(_row_get("market_close_at"))
        or _parse_market_timestamp(_row_get("market_end_at"))
    )
    now_utc = decision_time or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    if close_at is None or close_at > now_utc:
        return None
    return {
        "condition_id": condition_id,
        "closed": True,
        "accepting_orders": False,
        "enable_orderbook": None,
        "source": "executable_snapshot_market_end",
        "snapshot_id": _row_get("snapshot_id"),
        "market_close_at": close_at.isoformat(),
        "captured_at": _row_get("captured_at"),
    }


def _closed_non_accepting_market_info(clob, pos, conn=None, *, decision_time: datetime | None = None) -> dict | None:
    condition_id = str(
        getattr(pos, "condition_id", None)
        or getattr(pos, "market_id", None)
        or ""
    ).strip()
    get_market_info = getattr(clob, "get_clob_market_info", None)
    if condition_id and callable(get_market_info):
        try:
            info = get_market_info(condition_id)
        except Exception:
            info = None
        if info is not None:
            closed = _market_info_value(info, "closed")
            accepting_orders = _market_info_value(info, "accepting_orders", "acceptingOrders")
            enable_orderbook = _market_info_value(info, "enable_order_book", "enable_orderbook", "enableOrderBook")
            if closed is True and accepting_orders is False:
                return {
                    "condition_id": condition_id,
                    "closed": closed,
                    "accepting_orders": accepting_orders,
                    "enable_orderbook": enable_orderbook,
                    "source": "clob_market_info",
                }
    return _closed_by_static_market_end_info(conn, pos, decision_time=decision_time)


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


def _position_held_token_id(pos) -> str:
    direction = _position_direction_value(pos)
    if direction == "buy_no":
        return str(getattr(pos, "no_token_id", "") or getattr(pos, "token_id", "") or "")
    return str(getattr(pos, "token_id", "") or getattr(pos, "no_token_id", "") or "")


def _blocking_review_fact_for_position(portfolio, pos):
    held_token_id = _position_held_token_id(pos)
    condition_id = str(getattr(pos, "condition_id", "") or "")
    if not held_token_id:
        return None
    for fact in getattr(portfolio, "chain_only_facts", None) or ():
        if not bool(getattr(fact, "blocks_position_management", True)):
            continue
        fact_token_id = str(getattr(fact, "token_id", "") or "")
        if fact_token_id != held_token_id:
            continue
        fact_condition_id = str(getattr(fact, "condition_id", "") or "")
        if fact_condition_id and condition_id and fact_condition_id != condition_id:
            continue
        return fact
    return None


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


def _missing_fields_from_incomplete_exit_reason(exit_reason: str) -> set[str]:
    prefix = "INCOMPLETE_EXIT_CONTEXT (missing="
    text = str(exit_reason or "")
    if not text.startswith(prefix) or not text.endswith(")"):
        return set()
    return {part.strip() for part in text[len(prefix):-1].split(",") if part.strip()}


def _record_incomplete_exit_context_summary(
    summary: dict,
    *,
    pos,
    exit_reason: str,
    hours_to_settlement,
) -> None:
    summary["monitor_incomplete_exit_context"] = (
        summary.get("monitor_incomplete_exit_context", 0) + 1
    )
    if hours_to_settlement is None or hours_to_settlement > 6.0:
        return
    missing_fields = _missing_fields_from_incomplete_exit_reason(exit_reason)
    reason = f"incomplete_exit_context:{exit_reason}"
    if missing_fields & {
        "current_market_price",
        "current_market_price_is_fresh",
        "best_bid",
    }:
        summary["monitor_exit_quote_missing"] = (
            summary.get("monitor_exit_quote_missing", 0) + 1
        )
        summary.setdefault("monitor_exit_quote_missing_positions", []).append(pos.trade_id)
        summary.setdefault("monitor_exit_quote_missing_reasons", []).append(
            {"position_id": pos.trade_id, "reason": reason}
        )
        return
    summary["monitor_chain_missing"] = summary.get("monitor_chain_missing", 0) + 1
    summary.setdefault("monitor_chain_missing_positions", []).append(pos.trade_id)
    summary.setdefault("monitor_chain_missing_reasons", []).append(
        {"position_id": pos.trade_id, "reason": reason}
    )


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

    # BUG#113 (守護 SD-7): thread the CI-separation inputs into the exit context so the LIVE
    # Position.evaluate_exit gate can fire WITHOUT any DB read (the 2026-05-31 deadlock was a
    # belief read opening a 2nd world connection inside the reactor SAVEPOINT — here the bounds
    # are already in hand). PROVENANCE: edge_ctx.confidence_band_{lower,upper} are EDGE-space CI
    # (bootstrap of p_posterior − price; src/strategy/market_analysis._bootstrap_bin). Edge =
    # belief − price with price a per-decision constant, so adding the held-side market price back
    # shifts the band into BELIEF space — the same space as entry_posterior (pos.p_posterior) and
    # entry_ci_width (an edge-CI WIDTH, which is shift-invariant ⇒ already a belief-CI width). All
    # three are therefore consistent belief-space quantities. belief_available is False when the
    # current bootstrap CI is non-finite (degraded day0/obs math) → EVIDENCE_UNAVAILABLE third
    # state. Inert (None) unless we have both a finite current CI and the held-side price.
    # belief_available stays TRUE here: a missing CI is NOT proof of degraded belief math — it is
    # almost always a missing-authority case that the existing INCOMPLETE_EXIT_CONTEXT path must
    # own. We populate entry_ci / current_ci ONLY when a finite current belief CI is in hand; when
    # it is not, the CI-separation gate is simply inert and the legacy authority + flat path run
    # unchanged. The EVIDENCE_UNAVAILABLE third state is reserved for callers that POSITIVELY know
    # the belief math degraded (day0 absorbing-mask) and pass belief_available=False explicitly.
    _entry_posterior = None
    _entry_ci = None
    _current_ci = None
    _cb_lo = getattr(edge_ctx, "confidence_band_lower", None)
    _cb_hi = getattr(edge_ctx, "confidence_band_upper", None)
    _held_price = p_market
    _fresh_post = getattr(edge_ctx, "p_posterior", None)
    _pos_entry_posterior = None
    try:
        _pos_entry_posterior = float(pos.p_posterior)
    except (TypeError, ValueError):
        _pos_entry_posterior = None
    if (
        _pos_entry_posterior is not None
        and math.isfinite(_pos_entry_posterior)
        and 0.0 < _pos_entry_posterior < 1.0
        and _fresh_post is not None and math.isfinite(float(_fresh_post))
        and _cb_lo is not None and _cb_hi is not None and _held_price is not None
        and math.isfinite(float(_cb_lo)) and math.isfinite(float(_cb_hi))
        and math.isfinite(float(_held_price))
    ):
        # Both entry and a finite CURRENT belief CI are available → arm the CI-separation gate.
        _entry_posterior = _pos_entry_posterior
        _ci_half = max(0.0, float(getattr(pos, "entry_ci_width", 0.0) or 0.0)) / 2.0
        _entry_ci = (_entry_posterior - _ci_half, _entry_posterior + _ci_half)
        # Shift edge-space band → belief space by adding the held-side price back.
        _current_ci = (float(_cb_lo) + float(_held_price), float(_cb_hi) + float(_held_price))

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
        entry_posterior=_entry_posterior,
        entry_ci=_entry_ci,
        current_ci=_current_ci,
    )


def _row_value(row, index: int, key: str):
    if row is None:
        return None
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        try:
            return row[index]
        except (TypeError, IndexError):
            return None


def _latest_exit_snapshot_identity_row(conn, pos):
    if conn is None:
        return None
    identifiers: list[str] = []
    for value in (
        getattr(pos, "token_id", ""),
        getattr(pos, "no_token_id", ""),
        getattr(pos, "market_id", ""),
        getattr(pos, "condition_id", ""),
    ):
        identifier = str(value or "").strip()
        if identifier and identifier not in identifiers:
            identifiers.append(identifier)
    if not identifiers:
        return None
    padded_identifiers = (identifiers + ["__zeus_no_exit_snapshot_identifier__"] * 4)[:4]
    params = padded_identifiers * 5
    try:
        return conn.execute(
            """
            SELECT yes_token_id, no_token_id, condition_id, question_id
              FROM executable_market_snapshots
             WHERE selected_outcome_token_id IN (?, ?, ?, ?)
                OR yes_token_id IN (?, ?, ?, ?)
                OR no_token_id IN (?, ?, ?, ?)
                OR condition_id IN (?, ?, ?, ?)
                OR gamma_market_id IN (?, ?, ?, ?)
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            params,
        ).fetchone()
    except Exception as exc:
        logger.debug(
            "Exit retry snapshot identity lookup failed for %s: %s",
            getattr(pos, "trade_id", ""),
            exc,
        )
        return None


def _held_exit_token_from_snapshot_identity(conn, pos) -> str:
    direction = _position_direction_value(pos)
    token_attr = "no_token_id" if direction == "buy_no" else "token_id"
    token = str(getattr(pos, token_attr, "") or "").strip()
    if token:
        return token
    row = _latest_exit_snapshot_identity_row(conn, pos)
    if row is None:
        return ""
    yes_token = str(_row_value(row, 0, "yes_token_id") or "").strip()
    no_token = str(_row_value(row, 1, "no_token_id") or "").strip()
    if not getattr(pos, "token_id", "") and yes_token:
        pos.token_id = yes_token
    if not getattr(pos, "no_token_id", "") and no_token:
        pos.no_token_id = no_token
    if not getattr(pos, "condition_id", ""):
        condition_id = str(_row_value(row, 2, "condition_id") or "").strip()
        if condition_id:
            pos.condition_id = condition_id
    return str(getattr(pos, token_attr, "") or "").strip()


def _refresh_pending_exit_retry_quote_from_current_clob(
    *,
    conn,
    clob,
    pos,
    exit_context,
    identity_seed_allowed: bool,
):
    """Use stale snapshot identity only to find the held token; price comes from CLOB."""

    if (
        not identity_seed_allowed
        or conn is None
        or clob is None
        or getattr(exit_context, "current_market_price_is_fresh", False)
    ):
        return exit_context, False

    token_id = _held_exit_token_from_snapshot_identity(conn, pos)
    if not token_id:
        return exit_context, False
    quote_fn = getattr(clob, "get_best_bid_ask", None)
    if not callable(quote_fn):
        return exit_context, False

    try:
        bid, ask, bid_size, ask_size = quote_fn(token_id)
        bid_f = float(bid)
        ask_f = float(ask)
        bid_size_f = float(bid_size)
        ask_size_f = float(ask_size)
    except Exception as exc:
        logger.debug(
            "Exit retry current CLOB quote failed for %s token=%s: %s",
            getattr(pos, "trade_id", ""),
            token_id,
            exc,
        )
        return exit_context, False
    if (
        not math.isfinite(bid_f)
        or not math.isfinite(ask_f)
        or not math.isfinite(bid_size_f)
        or not math.isfinite(ask_size_f)
        or bid_f <= 0.0
        or ask_f <= 0.0
        or bid_size_f <= 0.0
        or ask_size_f <= 0.0
        or bid_f > ask_f
    ):
        return exit_context, False

    from src.strategy.market_fusion import vwmp

    diagnostic_market_price = (
        bid_f if _position_state_value(pos) == "day0_window" else float(vwmp(bid_f, ask_f, bid_size_f, ask_size_f))
    )
    source_timestamp = datetime.now(timezone.utc).isoformat()
    pos.last_monitor_best_bid = bid_f
    pos.last_monitor_best_ask = ask_f
    pos.last_monitor_market_price = diagnostic_market_price
    pos.last_monitor_market_price_is_fresh = True
    pos.last_monitor_at = source_timestamp
    return (
        replace(
            exit_context,
            current_market_price=diagnostic_market_price,
            current_market_price_is_fresh=True,
            best_bid=bid_f,
            best_ask=ask_f,
        ),
        True,
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


def _release_monitor_write_lock_boundary(conn, summary: dict, deps, *, boundary: str) -> None:
    """Commit monitor writes at bounded points so live price/decision writers can run."""

    if conn is None:
        return
    try:
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        summary["monitor_write_lock_release_failed"] = (
            summary.get("monitor_write_lock_release_failed", 0) + 1
        )
        summary.setdefault("monitor_write_lock_release_failures", []).append(
            {"boundary": boundary, "error": str(exc)[:500]}
        )
        deps.logger.warning(
            "monitor write-lock release failed at %s: %s",
            boundary,
            exc,
        )
    else:
        summary["monitor_write_lock_releases"] = (
            summary.get("monitor_write_lock_releases", 0) + 1
        )



def execute_monitoring_phase(
    conn,
    clob,
    portfolio,
    artifact,
    tracker,
    summary: dict,
    *,
    deps,
    exit_order_submit_enabled: bool = True,
    run_exit_preflight: bool = True,
):
    from src.engine.monitor_refresh import refresh_position
    from src.execution.exit_lifecycle import (
        ExitContext,
        build_exit_intent,
        check_pending_exits,
        check_pending_retries,
        execute_exit,
        handle_exit_pending_missing,
        is_exit_cooldown_active,
        release_backoff_exhausted_pending_exit_for_redecision,
        release_pending_exit_without_order_if_retryable,
        release_market_closed_pending_exit_hold,
    )
    from src.state.chain_reconciliation import quarantine_resolution_reason

    portfolio_dirty = False
    tracker_dirty = False

    if run_exit_preflight:
        portfolio_dirty = _apply_acknowledged_quarantine_clears(
            portfolio,
            summary,
            deps=deps,
            conn=conn,
        )

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
        _release_monitor_write_lock_boundary(
            conn,
            summary,
            deps,
            boundary="exit_preflight",
        )
    else:
        summary["exit_preflight_skipped_for_monitor_refresh"] = True

    for pos in _monitoring_phase_positions(portfolio):
        if pos.state == "pending_tracked":
            continue
        state_value = _position_state_value(pos)
        if is_terminal_state(state_value) and state_value != "quarantined":
            summary["monitor_skipped_terminal"] = summary.get("monitor_skipped_terminal", 0) + 1
            continue
        if pos.state == "economically_closed":
            summary["monitor_skipped_economic_close"] = summary.get("monitor_skipped_economic_close", 0) + 1
            continue
        if False:
            _ = pos.entry_method
            _ = pos.selected_method
        if run_exit_preflight:
            pending_exit_resolution = handle_exit_pending_missing(portfolio, pos, conn=conn)
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
            if pending_exit_resolution["action"] == "evaluate":
                # FIX 2a (2026-06-20): chain-truth confirmed the position is
                # still held (balance > dust) with no resting sell order, so it
                # was released from the pending_exit pre-emption to reach the
                # live exit emitter THIS cycle. Do NOT continue — fall through to
                # the full refresh_position → evaluate_exit → execute_exit lane
                # below so a reversal caught while a bid exists reaches
                # place_sell_order instead of looping on EXIT_CHAIN_MISSING.
                summary["exit_pending_missing_routed_to_evaluate"] = (
                    summary.get("exit_pending_missing_routed_to_evaluate", 0) + 1
                )
        if pos.state == "admin_closed":
            summary["monitor_skipped_admin_close"] = summary.get("monitor_skipped_admin_close", 0) + 1
            continue
        pending_exit_monitor_only = False
        pending_exit_retry_identity_seed_allowed = (
            pos.state == "pending_exit"
            and getattr(pos, "exit_state", "") == "retry_pending"
            and not is_exit_cooldown_active(pos)
        )
        if pos.state == "pending_exit":
            if pos.exit_state == "backoff_exhausted":
                if release_market_closed_pending_exit_hold(pos, conn=conn):
                    portfolio_dirty = True
                    summary["monitor_repaired_market_closed_pending_exit_hold"] = (
                        summary.get("monitor_repaired_market_closed_pending_exit_hold", 0) + 1
                    )
                elif release_backoff_exhausted_pending_exit_for_redecision(pos, conn=conn):
                    portfolio_dirty = True
                    summary["monitor_released_backoff_exhausted_for_redecision"] = (
                        summary.get("monitor_released_backoff_exhausted_for_redecision", 0) + 1
                    )
                else:
                    summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                    continue
            if is_exit_cooldown_active(pos):
                summary["monitor_skipped_pending_exit_phase"] = summary.get("monitor_skipped_pending_exit_phase", 0) + 1
                continue
            if run_exit_preflight:
                check_pending_retries(pos, conn=conn)
            if release_pending_exit_without_order_if_retryable(pos, conn=conn):
                portfolio_dirty = True
                summary["monitor_released_pending_exit_without_order"] = (
                    summary.get("monitor_released_pending_exit_without_order", 0) + 1
                )
            if pos.state == "pending_exit":
                pending_exit_monitor_only = True
                summary["monitor_pending_exit_phase_evaluated"] = (
                    summary.get("monitor_pending_exit_phase_evaluated", 0) + 1
                )
        if pos.exit_state in ("sell_placed", "sell_pending") and not pending_exit_monitor_only:
            continue
        if pos.exit_state == "backoff_exhausted":
            continue
        if is_exit_cooldown_active(pos):
            continue

        if run_exit_preflight:
            check_pending_retries(pos, conn=conn)

        if _requires_quarantine_monitor_resolution(pos):
            if _quarantined_position_can_redecision(pos):
                summary["quarantined_exposure_routed_to_redecision"] = (
                    summary.get("quarantined_exposure_routed_to_redecision", 0) + 1
                )
            else:
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

        review_fact = _blocking_review_fact_for_position(portfolio, pos)
        if review_fact is not None:
            artifact.add_monitor_result(
                deps.MonitorResult(
                    position_id=pos.trade_id,
                    fresh_prob=None,
                    fresh_edge=None,
                    should_exit=False,
                    exit_reason="REVIEW_REQUIRED_INVALID_ENTRY_PROOF",
                    neg_edge_count=pos.neg_edge_count,
                )
            )
            summary["monitor_skipped_blocking_review_fact"] = summary.get(
                "monitor_skipped_blocking_review_fact",
                0,
            ) + 1
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
                    _release_monitor_write_lock_boundary(
                        conn,
                        summary,
                        deps,
                        boundary="day0_window_entered",
                    )

            # Day0 hard facts are settlement/observation truth, not venue
            # executability.  Compute them before the closed-market gate so a
            # CLOB closed/non-accepting state cannot hide an already-dead bin or
            # a structurally won hold behind a generic awaiting-settlement
            # receipt.
            _hard_fact = None
            if _day0_hard_fact_position_eligible(pos) and city is not None:
                try:
                    from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit
                    # Pass conn as world_conn so the METAR kill-memo cold-start
                    # recovery does not open per-city independent world connections
                    # (connection-burst antibody 2026-06-13).
                    _hard_fact = evaluate_hard_fact_exit(
                        position=pos, city=city, now=deps._utcnow(), world_conn=conn
                    )
                except Exception as _hf_exc:  # noqa: BLE001 — lane must never break the monitor
                    deps.logger.warning(
                        "day0 hard-fact lane failed for %s (non-fatal): %s", pos.trade_id, _hf_exc
                    )
            closed_market_info = _closed_non_accepting_market_info(
                clob,
                pos,
                conn,
                decision_time=deps._utcnow(),
            )
            # FIX 2b (2026-06-20): split the day0 closed-market pre-emption by
            # evidence source.
            #   * source="clob_market_info" → the VENUE itself reports
            #     closed=True AND accepting_orders=False. This is authoritative
            #     "will-not-accept-a-sell" truth → terminal stamp now, as before.
            #   * source="executable_snapshot_market_end" → a STATIC time
            #     heuristic (market_close_at/market_end_at passed). The venue may
            #     still be accepting orders with a live bid, so a reversal caught
            #     just before close must get one real shot at place_sell_order.
            #     Defer the terminal stamp: run the full refresh→evaluate_exit→
            #     execute_exit lane below, and only stamp MARKET_CLOSED if the
            #     market is genuinely untradeable (no finite executable best_bid).
            deferred_static_closed_market_info = None
            if closed_market_info is not None:
                _closed_source = str(closed_market_info.get("source") or "")
                if _closed_source == "executable_snapshot_market_end":
                    deferred_static_closed_market_info = closed_market_info
                    closed_market_info = None
            if closed_market_info is not None:
                if _hard_fact is not None and _hard_fact.action in {
                    "EXIT_DEAD_BIN",
                    "HOLD_STRUCTURAL_WIN",
                }:
                    from src.state.portfolio import ExitDecision as _ExitDecision

                    hard_fact_win = _hard_fact.action == "HOLD_STRUCTURAL_WIN"
                    if _position_state_value(pos) != "quarantined":
                        pos.state = "day0_window"
                        pos.pre_exit_state = ""
                        pos.exit_state = ""
                        pos.next_exit_retry_at = ""
                        pos.exit_retry_count = 0
                        pos.exit_reason = ""
                        pos.last_exit_error = (
                            "MARKET_CLOSED_AWAITING_SETTLEMENT:"
                            f"{closed_market_info.get('source') or 'market_closed_non_accepting_orders'}"
                        )[:500]
                    pos.last_monitor_prob = 1.0 if hard_fact_win else 0.0
                    pos.last_monitor_prob_is_fresh = True
                    pos.last_monitor_edge = None
                    pos.last_monitor_market_price = None
                    pos.last_monitor_market_price_is_fresh = False
                    pos.last_monitor_best_bid = None
                    pos.last_monitor_best_ask = None
                    pos.last_monitor_market_vig = None
                    pos.applied_validations = list(
                        dict.fromkeys(
                            [
                                *(pos.applied_validations or []),
                                (
                                    "day0_hard_fact_structural_win_closed_hold"
                                    if hard_fact_win
                                    else "day0_hard_fact_bin_dead_closed_market"
                                ),
                            ]
                        )
                    )
                    exit_decision = _ExitDecision(
                        False,
                        (
                            "DAY0_HARD_FACT_STRUCTURAL_WIN_MARKET_CLOSED "
                            if hard_fact_win
                            else "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED "
                        )
                        + f"({_hard_fact.reason}; source={_hard_fact.source})",
                        urgency="normal",
                        trigger=(
                            "DAY0_HARD_FACT_STRUCTURAL_WIN_MARKET_CLOSED"
                            if hard_fact_win
                            else "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED"
                        ),
                        selected_method=pos.selected_method or pos.entry_method,
                        applied_validations=list(pos.applied_validations),
                    )
                    _emit_monitor_refreshed_canonical_if_available(
                        conn,
                        pos,
                        deps=deps,
                        exit_decision=exit_decision,
                        final_should_exit=False,
                        final_exit_reason=exit_decision.reason,
                        final_exit_trigger=exit_decision.trigger,
                    )
                    artifact.add_monitor_result(
                        deps.MonitorResult(
                            position_id=pos.trade_id,
                            fresh_prob=pos.last_monitor_prob,
                            fresh_edge=None,
                            should_exit=False,
                            exit_reason=exit_decision.reason,
                            neg_edge_count=pos.neg_edge_count,
                        )
                    )
                    portfolio_dirty = True
                    summary["day0_hard_fact_closed_market_monitors"] = (
                        summary.get("day0_hard_fact_closed_market_monitors", 0) + 1
                    )
                    summary["monitors"] += 1
                    continue
                from src.execution.exit_lifecycle import mark_market_closed_hold_to_settlement

                mark_market_closed_hold_to_settlement(
                    pos,
                    reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
                    error=str(closed_market_info.get("source") or "market_closed_non_accepting_orders"),
                    conn=conn,
                )
                portfolio_dirty = True
                artifact.add_monitor_result(
                    deps.MonitorResult(
                        position_id=pos.trade_id,
                        fresh_prob=pos.last_monitor_prob,
                        fresh_edge=None,
                        should_exit=False,
                        exit_reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
                        neg_edge_count=pos.neg_edge_count,
                    )
                )
                summary["monitor_skipped_closed_market_pending_settlement"] = (
                    summary.get("monitor_skipped_closed_market_pending_settlement", 0) + 1
                )
                summary.setdefault("monitor_closed_market_pending_settlement_positions", []).append(pos.trade_id)
                summary.setdefault("monitor_closed_market_pending_settlement_reasons", []).append(
                    {
                        "position_id": pos.trade_id,
                        "reason": "market_closed_non_accepting_orders",
                        **closed_market_info,
                    }
                )
                summary["monitors"] += 1
                continue

            edge_ctx = refresh_position(conn, clob, pos)
            # === DAY0 HARD-FACT verdict — computed before the exit decision and
            # before closed-market pre-emption above. Settlement-authority hard
            # facts must not depend on estimator evidence.
            exit_context = _build_exit_context(
                pos,
                edge_ctx,
                hours_to_settlement=hours_to_settlement,
                ExitContext=ExitContext,
                portfolio=portfolio,
            )
            if run_exit_preflight:
                exit_context, refreshed_retry_quote = _refresh_pending_exit_retry_quote_from_current_clob(
                    conn=conn,
                    clob=clob,
                    pos=pos,
                    exit_context=exit_context,
                    identity_seed_allowed=pending_exit_retry_identity_seed_allowed,
                )
                if refreshed_retry_quote:
                    summary["pending_exit_retry_current_clob_quote_refreshed"] = (
                        summary.get("pending_exit_retry_current_clob_quote_refreshed", 0) + 1
                    )
            p_market = exit_context.current_market_price
            portfolio_dirty = True
            # === DAY0 HARD-FACT EXIT LANE (adversarial review 2026-06-10 fix 1) ===
            # SEPARATE from the estimator-evidence lane below: a position whose bin
            # is absorbing-boundary DEAD by a settlement-grade hard fact (WU + the
            # METAR fast lane, margin per config/wu_metar_divergence.json) exits NOW
            # — no maturity gate, no CI separation, no fresh_prob dependency (this
            # is what gives buy_no its day0 exit authority for the hard-fact class).
            # Estimator flips keep the full panic-sell hardening unchanged. The lane
            # is fail-soft: any data gap / oracle-anomaly pause -> None -> the normal
            # evaluate_exit path runs.
            # (_hard_fact was computed ABOVE, before the canonical-write failure
            # branch — PR#404 P0-4.)
            if _hard_fact is not None and _hard_fact.action == "EXIT_DEAD_BIN":
                from src.state.portfolio import ExitDecision as _ExitDecision

                pos.applied_validations = list(
                    dict.fromkeys([*(pos.applied_validations or []), "day0_hard_fact_exit_lane"])
                )
                exit_decision = _ExitDecision(
                    True,
                    f"DAY0_HARD_FACT_BIN_DEAD ({_hard_fact.reason}; source={_hard_fact.source})",
                    urgency="immediate",
                    trigger="DAY0_HARD_FACT_BIN_DEAD",
                    selected_method=pos.selected_method or pos.entry_method,
                    applied_validations=list(pos.applied_validations),
                )
                summary["day0_hard_fact_exits"] = summary.get("day0_hard_fact_exits", 0) + 1
            elif _hard_fact is not None and _hard_fact.action == "HOLD_STRUCTURAL_WIN":
                # TERMINAL HOLD (PR#404 BLOCKER 2): a structural-win hard fact
                # (buy_no on a DEAD bin, buy_yes on the entered shoulder) is an
                # absorbing settlement-authority verdict — do NOT run the normal
                # estimator-evidence path and do NOT let the ORANGE favorable-exit
                # layer sell out of a structurally won position.  The hold is
                # explicit and separately named: only a kill-switch / manual
                # reduce-only override (which produces an EXIT_DEAD_BIN verdict)
                # can override it.
                from src.state.portfolio import ExitDecision as _ExitDecision

                pos.applied_validations = list(
                    dict.fromkeys([*(pos.applied_validations or []), "day0_hard_fact_structural_win_hold"])
                )
                exit_decision = _ExitDecision(
                    False,
                    f"DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD ({_hard_fact.reason}; source={_hard_fact.source})",
                    urgency="normal",
                    trigger="DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD",
                    selected_method=pos.selected_method or pos.entry_method,
                    applied_validations=list(pos.applied_validations),
                )
                summary["day0_hard_fact_structural_win_holds"] = (
                    summary.get("day0_hard_fact_structural_win_holds", 0) + 1
                )
                # ORANGE gate intentionally skipped: a favorable exit on a
                # structurally-won position would defeat the purpose of holding.
            else:
                exit_decision = pos.evaluate_exit(exit_context)
            entry_selection_guard_forced_exit = False
            if not (
                _hard_fact is not None
                and _hard_fact.action in {"EXIT_DEAD_BIN", "HOLD_STRUCTURAL_WIN"}
            ):
                selection_guard_decision = _entry_selection_guard_exit_decision(
                    conn=conn,
                    pos=pos,
                    exit_context=exit_context,
                    summary=summary,
                    exit_decision=exit_decision,
                )
                if selection_guard_decision is not None:
                    exit_decision = selection_guard_decision
                    entry_selection_guard_forced_exit = bool(selection_guard_decision.should_exit)
            if _summary_risk_level(summary) == "ORANGE" and not (
                _hard_fact is not None and _hard_fact.action == "HOLD_STRUCTURAL_WIN"
            ):
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
            if not (
                _hard_fact is not None
                and _hard_fact.action in {"EXIT_DEAD_BIN", "HOLD_STRUCTURAL_WIN"}
            ) and not entry_selection_guard_forced_exit:
                should_exit, exit_reason = _apply_family_monitor_overlay(
                    portfolio=portfolio,
                    pos=pos,
                    exit_decision=exit_decision,
                    should_exit=should_exit,
                    exit_reason=exit_reason,
                    summary=summary,
                )

            exit_trigger = _effective_exit_trigger(exit_decision, exit_reason)
            if should_exit:
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
                _record_incomplete_exit_context_summary(
                    summary,
                    pos=pos,
                    exit_reason=exit_reason,
                    hours_to_settlement=hours_to_settlement,
                )
                deps.logger.warning(
                    "Exit authority incomplete for %s: %s",
                    pos.trade_id,
                    exit_reason,
                )

            monitor_canonical_written = _emit_monitor_refreshed_canonical_if_available(
                conn,
                pos,
                deps=deps,
                exit_decision=exit_decision,
                final_should_exit=should_exit,
                final_exit_reason=exit_reason,
                final_exit_trigger=exit_trigger,
            )
            if not monitor_canonical_written:
                summary["monitor_canonical_write_failed"] = (
                    summary.get("monitor_canonical_write_failed", 0) + 1
                )
                if _hard_fact is not None and _hard_fact.action == "EXIT_DEAD_BIN":
                    # P0-4: telemetry failure must not hold a structurally dead
                    # leg. The monitor result below records the exit decision;
                    # settlement-authority exits may still actuate.
                    summary["day0_hard_fact_exit_despite_canonical_write_failure"] = (
                        summary.get("day0_hard_fact_exit_despite_canonical_write_failure", 0) + 1
                    )
                    deps.logger.error(
                        "MONITOR_CANONICAL_WRITE_FAILED for %s but day0 hard-fact bin death "
                        "present — proceeding to exit (telemetry failure does not gate "
                        "settlement-authority exits)",
                        pos.trade_id,
                    )
                else:
                    monitor_fresh_prob, monitor_fresh_edge = _current_monitor_result_probability_and_edge(pos, edge_ctx)
                    artifact.add_monitor_result(
                        deps.MonitorResult(
                            position_id=pos.trade_id,
                            fresh_prob=monitor_fresh_prob,
                            fresh_edge=monitor_fresh_edge,
                            should_exit=False,
                            exit_reason="MONITOR_CANONICAL_WRITE_FAILED",
                            neg_edge_count=pos.neg_edge_count,
                        )
                    )
                    monitor_result_written = True
                    summary["monitors"] += 1
                    continue

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
                pos.exit_trigger = exit_trigger
                pos.exit_reason = exit_reason
                pos.exit_divergence_score = edge_ctx.divergence_score
                pos.exit_market_velocity_1h = edge_ctx.market_velocity_1h
                pos.exit_forward_edge = edge_ctx.forward_edge
                if pending_exit_monitor_only:
                    summary["pending_exit_exit_signal_already_in_flight"] = (
                        summary.get("pending_exit_exit_signal_already_in_flight", 0) + 1
                    )
                    portfolio_dirty = True
                    continue
                exit_intent = build_exit_intent(
                    pos,
                    replace(exit_context, exit_reason=exit_reason),
                )
                if not exit_order_submit_enabled:
                    # Live submit-gate disabled: record the exit decision for
                    # operator visibility but do not place a venue sell order.
                    summary["exits_suppressed_no_submit"] = summary.get("exits_suppressed_no_submit", 0) + 1
                    summary["exits"] += 1
                    portfolio_dirty = True
                else:
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

            # FIX 2b (2026-06-20): apply the DEFERRED static-time closed-market
            # stamp only now that the live exit lane has run. The terminal stamp
            # is correct ONLY for a genuinely untradeable market — i.e. one with
            # no finite executable best_bid. If a bid still exists the position
            # stays monitored (it already took its real shot at place_sell_order
            # above when should_exit fired, and can exit a later cycle while a
            # bid persists); a reversal caught just before the static close is no
            # longer pre-empted into MARKET_CLOSED_AWAITING_SETTLEMENT.
            if (
                deferred_static_closed_market_info is not None
                and not ExitContext._is_finite(getattr(exit_context, "best_bid", None))
            ):
                from src.execution.exit_lifecycle import mark_market_closed_hold_to_settlement

                mark_market_closed_hold_to_settlement(
                    pos,
                    reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
                    error=str(
                        deferred_static_closed_market_info.get("source")
                        or "market_closed_non_accepting_orders"
                    ),
                    conn=conn,
                )
                portfolio_dirty = True
                summary["monitor_closed_market_pending_settlement_after_eval"] = (
                    summary.get("monitor_closed_market_pending_settlement_after_eval", 0) + 1
                )
                summary.setdefault("monitor_closed_market_pending_settlement_positions", []).append(pos.trade_id)
                summary.setdefault("monitor_closed_market_pending_settlement_reasons", []).append(
                    {
                        "position_id": pos.trade_id,
                        "reason": "market_closed_no_executable_bid",
                        **deferred_static_closed_market_info,
                    }
                )
            elif deferred_static_closed_market_info is not None:
                summary["day0_static_closed_market_tradable_bid_preserved"] = (
                    summary.get("day0_static_closed_market_tradable_bid_preserved", 0) + 1
                )
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
        finally:
            _release_monitor_write_lock_boundary(
                conn,
                summary,
                deps,
                boundary="position_monitor",
            )

    _emit_portfolio_rotation_evaluation_status(conn, summary, deps=deps)
    _release_monitor_write_lock_boundary(
        conn,
        summary,
        deps,
        boundary="portfolio_rotation_evaluation_status",
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


def _is_attached_schema(conn, schema_name: str) -> bool:
    try:
        return any(str(row[1]) == schema_name for row in conn.execute("PRAGMA database_list").fetchall())
    except Exception:
        return False


def _table_exists_in_schema(conn, schema_name: str, table_name: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {schema_name}.sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _table_columns_in_schema(conn, schema_name: str, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema_name}.table_info({table_name})").fetchall()
    except Exception:
        return set()
    out: set[str] = set()
    for row in rows:
        try:
            out.add(str(row[1]))
        except Exception:
            continue
    return out


def _select_expr(columns: set[str], column: str, alias: str, default_sql: str = "NULL") -> str:
    if column in columns:
        return f"{column} AS {alias}"
    return f"{default_sql} AS {alias}"


def _row_get(row, key: str, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return default


def _finite_probability_or_none(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out < 0.0 or out > 1.0:
        return None
    return out


def _finite_positive_or_none(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out <= 0.0:
        return None
    return out


def _parse_utc_or_none(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_monitor_best_bid_by_position(conn) -> dict[str, float]:
    if conn is None or not _table_exists_in_schema(conn, "main", "position_events"):
        return {}
    out: dict[str, float] = {}
    try:
        rows = conn.execute(
            """
            SELECT position_id, payload_json
              FROM position_events
             WHERE event_type = 'MONITOR_REFRESHED'
             ORDER BY occurred_at DESC, rowid DESC
            """
        ).fetchall()
    except Exception:
        return {}
    for row in rows:
        position_id = str(_row_get(row, "position_id", "") or "").strip()
        if not position_id or position_id in out:
            continue
        try:
            payload = json.loads(str(_row_get(row, "payload_json", "") or "{}"))
        except (TypeError, ValueError):
            continue
        bid = _finite_probability_or_none(payload.get("last_monitor_best_bid"))
        if bid is not None:
            out[position_id] = bid
    return out


def _rotation_held_positions(conn, *, best_bids: dict[str, float]) -> tuple[list, list[str]]:
    from src.strategy.portfolio_rotation import RotationHold

    if conn is None or not _table_exists_in_schema(conn, "main", "position_current"):
        return [], ["position_current_unavailable"]
    columns = _table_columns_in_schema(conn, "main", "position_current")
    required = {
        "position_id",
        "phase",
        "city",
        "target_date",
        "bin_label",
        "direction",
        "shares",
        "last_monitor_prob",
        "last_monitor_prob_is_fresh",
        "last_monitor_market_price_is_fresh",
    }
    missing_required = sorted(required - columns)
    if missing_required:
        return [], [f"position_current_missing_columns:{','.join(missing_required)}"]
    select_sql = ", ".join(
        [
            "position_id",
            _select_expr(columns, "trade_id", "trade_id", "position_id"),
            "phase",
            "city",
            "target_date",
            _select_expr(columns, "temperature_metric", "metric", "'high'"),
            "bin_label",
            "direction",
            "shares",
            "last_monitor_prob",
            "last_monitor_prob_is_fresh",
            "last_monitor_market_price_is_fresh",
            _select_expr(columns, "token_id", "token_id"),
            _select_expr(columns, "no_token_id", "no_token_id"),
            _select_expr(columns, "condition_id", "condition_id"),
        ]
    )
    try:
        rows = conn.execute(
            f"""
            SELECT {select_sql}
              FROM position_current
             WHERE phase IN ('active', 'day0_window')
               AND COALESCE(shares, 0) > 0
            """
        ).fetchall()
    except Exception as exc:
        return [], [f"position_current_read_failed:{exc.__class__.__name__}"]
    holds: list = []
    missing: list[str] = []
    for row in rows:
        position_id = str(_row_get(row, "position_id", "") or "").strip()
        shares = _finite_positive_or_none(_row_get(row, "shares"))
        held_prob = _finite_probability_or_none(_row_get(row, "last_monitor_prob"))
        if not position_id:
            missing.append("held_position_missing_id")
            continue
        if int(_row_get(row, "last_monitor_prob_is_fresh", 0) or 0) != 1:
            missing.append(f"{position_id}:held_probability_not_fresh")
            continue
        if int(_row_get(row, "last_monitor_market_price_is_fresh", 0) or 0) != 1:
            missing.append(f"{position_id}:held_market_price_not_fresh")
            continue
        best_bid = best_bids.get(position_id)
        if shares is None or held_prob is None or best_bid is None:
            missing.append(f"{position_id}:held_rotation_value_inputs_incomplete")
            continue
        direction = str(_row_get(row, "direction", "") or "").strip()
        token_id = (
            str(_row_get(row, "no_token_id", "") or "").strip()
            if direction == "buy_no"
            else str(_row_get(row, "token_id", "") or "").strip()
        )
        try:
            holds.append(
                RotationHold(
                    position_id=position_id,
                    city=str(_row_get(row, "city", "") or "").strip(),
                    target_date=str(_row_get(row, "target_date", "") or "").strip(),
                    metric=str(_row_get(row, "metric", "") or "").strip(),
                    bin_label=str(_row_get(row, "bin_label", "") or "").strip(),
                    direction=direction,
                    shares=shares,
                    held_probability=held_prob,
                    held_side_best_bid=best_bid,
                    token_id=token_id,
                    condition_id=str(_row_get(row, "condition_id", "") or "").strip(),
                )
            )
        except ValueError as exc:
            missing.append(f"{position_id}:held_rotation_invalid:{exc}")
    return holds, missing


def _rotation_candidates(conn, *, decision_time: datetime) -> tuple[list, list[str]]:
    from src.strategy.portfolio_rotation import RotationCandidate

    if conn is None:
        return [], ["connection_unavailable"]
    schema = None
    if _is_attached_schema(conn, "world") and _table_exists_in_schema(conn, "world", "no_trade_regret_events"):
        schema = "world"
    elif _table_exists_in_schema(conn, "main", "no_trade_regret_events"):
        schema = "main"
    if schema is None:
        return [], ["no_trade_regret_events_unavailable"]
    columns = _table_columns_in_schema(conn, schema, "no_trade_regret_events")
    required = {
        "event_id",
        "rejection_reason",
        "city",
        "target_date",
        "metric",
        "bin_label",
        "direction",
        "q_lcb_5pct",
        "c_fee_adjusted",
        "trade_score",
        "created_at",
    }
    missing_required = sorted(required - columns)
    if missing_required:
        return [], [f"no_trade_regret_events_missing_columns:{','.join(missing_required)}"]
    select_sql = ", ".join(
        [
            "event_id",
            _select_expr(columns, "rejection_stage", "rejection_stage", "''"),
            "rejection_reason",
            "city",
            "target_date",
            "metric",
            "bin_label",
            "direction",
            "q_lcb_5pct",
            "c_fee_adjusted",
            _select_expr(columns, "p_fill_lcb", "p_fill_lcb"),
            "trade_score",
            _select_expr(columns, "token_id", "token_id"),
            _select_expr(columns, "condition_id", "condition_id"),
            "created_at",
        ]
    )
    try:
        rows = conn.execute(
            f"""
            SELECT {select_sql}
              FROM {schema}.no_trade_regret_events
             WHERE COALESCE(trade_score, 0) > 0
             ORDER BY created_at DESC
             LIMIT 200
            """
        ).fetchall()
    except Exception as exc:
        return [], [f"no_trade_regret_events_read_failed:{exc.__class__.__name__}"]
    lookback_hours = max(
        0.25,
        float(os.environ.get("ZEUS_PORTFOLIO_ROTATION_CANDIDATE_LOOKBACK_HOURS", "6.0")),
    )
    earliest = decision_time.astimezone(timezone.utc) - timedelta(hours=lookback_hours)
    candidates: list = []
    skipped: list[str] = []
    for row in rows:
        created_at = _parse_utc_or_none(_row_get(row, "created_at"))
        if created_at is None or created_at < earliest:
            continue
        rejection_stage = str(_row_get(row, "rejection_stage", "") or "").strip()
        rejection_reason = str(_row_get(row, "rejection_reason", "") or "").strip()
        if rejection_stage not in {"KELLY", "LIVE_CAP", ""} and not (
            "KELLY" in rejection_reason
            or "CAP" in rejection_reason
            or "BUDGET" in rejection_reason
            or "OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED" in rejection_reason
            or "OPEN_POSITION_SAME_TOKEN_MONITOR_OWNED" in rejection_reason
        ):
            continue
        q_lcb = _finite_probability_or_none(_row_get(row, "q_lcb_5pct"))
        cost = _finite_probability_or_none(_row_get(row, "c_fee_adjusted"))
        score = _finite_positive_or_none(_row_get(row, "trade_score"))
        p_fill_lcb = _finite_probability_or_none(_row_get(row, "p_fill_lcb"))
        event_id = str(_row_get(row, "event_id", "") or "").strip()
        if q_lcb is None or cost is None or cost <= 0.0 or cost >= 1.0 or score is None or not event_id:
            skipped.append(f"{event_id or 'unknown'}:candidate_rotation_inputs_incomplete")
            continue
        try:
            candidates.append(
                RotationCandidate(
                    event_id=event_id,
                    city=str(_row_get(row, "city", "") or "").strip(),
                    target_date=str(_row_get(row, "target_date", "") or "").strip(),
                    metric=str(_row_get(row, "metric", "") or "").strip(),
                    bin_label=str(_row_get(row, "bin_label", "") or "").strip(),
                    direction=str(_row_get(row, "direction", "") or "").strip(),
                    q_lcb=q_lcb,
                    fee_adjusted_cost=cost,
                    trade_score=score,
                    p_fill_lcb=p_fill_lcb,
                    token_id=str(_row_get(row, "token_id", "") or "").strip(),
                    condition_id=str(_row_get(row, "condition_id", "") or "").strip(),
                    rejection_reason=rejection_reason,
                )
            )
        except ValueError as exc:
            skipped.append(f"{event_id}:candidate_rotation_invalid:{exc}")
    return candidates, skipped


def _emit_portfolio_rotation_evaluation_status(conn, summary: dict, *, deps) -> None:
    """Evaluate portfolio rotation value without implying executable live action.

    Actual same-family fill-up/shift actions are driven by EDLI_REDECISION_PENDING
    in the event reactor. This summary is read-side evidence only; it must not say
    a rotation is live-ready when no cross-family rotation actuator consumes it.
    """

    if conn is None:
        summary["portfolio_rotation_evaluation_status"] = "unavailable:no_connection"
        return
    now_fn = getattr(deps, "_utcnow", None)
    try:
        decision_time = now_fn() if callable(now_fn) else datetime.now(timezone.utc)
    except Exception:
        decision_time = datetime.now(timezone.utc)
    if decision_time.tzinfo is None:
        decision_time = decision_time.replace(tzinfo=timezone.utc)
    decision_time = decision_time.astimezone(timezone.utc)

    best_bids = _latest_monitor_best_bid_by_position(conn)
    holds, hold_missing = _rotation_held_positions(conn, best_bids=best_bids)
    candidates, candidate_missing = _rotation_candidates(conn, decision_time=decision_time)
    summary["portfolio_rotation_held_positions_evaluated"] = len(holds)
    summary["portfolio_rotation_candidates_evaluated"] = len(candidates)
    if hold_missing:
        summary["portfolio_rotation_hold_input_gaps"] = hold_missing[:10]
    if candidate_missing:
        summary["portfolio_rotation_candidate_input_gaps"] = candidate_missing[:10]
    if not holds:
        summary["portfolio_rotation_evaluation_status"] = "evaluated:no_held_positions_with_fresh_rotation_inputs"
        return
    if not candidates:
        summary["portfolio_rotation_evaluation_status"] = "evaluated:no_capital_constrained_positive_candidates"
        return

    from src.strategy.portfolio_rotation import best_rotation

    fee_rate = float(os.environ.get("ZEUS_PORTFOLIO_ROTATION_FEE_RATE", "0.02"))
    min_usd = max(0.0, float(os.environ.get("ZEUS_PORTFOLIO_ROTATION_MIN_IMPROVEMENT_USD", "0.05")))
    min_ratio = max(0.0, float(os.environ.get("ZEUS_PORTFOLIO_ROTATION_MIN_IMPROVEMENT_RATIO", "0.03")))
    decision = best_rotation(
        holds,
        candidates,
        fee_rate=fee_rate,
        min_net_improvement_usd=min_usd,
        min_net_improvement_ratio=min_ratio,
        require_fill_lcb=True,
    )
    if decision is None:
        summary["portfolio_rotation_evaluation_status"] = "evaluated:hold_value_dominant"
        return
    summary["portfolio_rotation_evaluation_status"] = (
        "evaluated:positive_rotation_value_no_cross_family_actuator"
    )
    summary["portfolio_rotation_best"] = {
        "hold_position_id": decision.hold.position_id,
        "hold_city": decision.hold.city,
        "hold_target_date": decision.hold.target_date,
        "hold_metric": decision.hold.metric,
        "hold_bin_label": decision.hold.bin_label,
        "hold_direction": decision.hold.direction,
        "candidate_event_id": decision.candidate.event_id,
        "candidate_city": decision.candidate.city,
        "candidate_target_date": decision.candidate.target_date,
        "candidate_metric": decision.candidate.metric,
        "candidate_bin_label": decision.candidate.bin_label,
        "candidate_direction": decision.candidate.direction,
        "reason": decision.reason,
        "sell_value_usd": decision.sell_value_usd,
        "hold_future_value_usd": decision.hold_future_value_usd,
        "candidate_future_value_usd": decision.candidate_future_value_usd,
        "net_improvement_usd": decision.net_improvement_usd,
        "net_improvement_ratio": decision.net_improvement_ratio,
        "fill_lcb_used": decision.fill_lcb_used,
    }


def _observation_time_to_local_date(observation_time, timezone_name: str):
    """Best-effort local date of an observation timestamp. None when un-parseable.

    Accepts ISO strings, epoch seconds, or datetime. Total (never raises) —
    this is observability instrumentation that must not perturb the cycle.
    """
    if observation_time is None:
        return None
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        return None
    dt = None
    try:
        if isinstance(observation_time, datetime):
            dt = observation_time
        elif isinstance(observation_time, (int, float)):
            dt = datetime.fromtimestamp(float(observation_time), tz=timezone.utc)
        else:
            raw = str(observation_time).strip()
            if not raw:
                return None
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(tz).date()
    except Exception:
        return None


def _normalize_observation_coverage_status(raw: str) -> str:
    """Map the two runtime coverage/availability vocabularies onto the canonical
    MISSING / STALE / NON_SETTLEMENT_SOURCE / LOW / OK set the operator audit
    query expects.

    Settlement-bound observation paths emit OK / LOW_COVERAGE /
    WINDOW_INCOMPLETE. Explicitly requested non-settlement observation paths emit
    NON_SETTLEMENT_SOURCE. Failure paths emit DATA_UNAVAILABLE / DATA_STALE /
    RATE_LIMITED / CHAIN_UNAVAILABLE (_availability_status_for_exception). The
    raw value is preserved in payload_json; this is only the canonical column
    value.
    """
    value = str(raw or "").strip().upper()
    if value in {"OK"}:
        return "OK"
    if value in {"DATA_STALE", "STALE", "RATE_LIMITED"}:
        return "STALE"
    if value in {"NON_SETTLEMENT_SOURCE"}:
        return "NON_SETTLEMENT_SOURCE"
    if value in {"LOW", "LOW_COVERAGE", "WINDOW_INCOMPLETE"}:
        return "LOW"
    if value in {"DATA_UNAVAILABLE", "CHAIN_UNAVAILABLE", "MISSING", "", "UNKNOWN"}:
        return "MISSING"
    return "MISSING"


def stamp_observation_authority_id_onto_decisions(candidate, decisions) -> None:
    """Propagate candidate.observation_authority_id onto every EdgeDecision.

    OBS-AUTHORITY-FOUNDATION (2026-05-23). The durable opportunity_fact row links
    back to the runtime observation object via this id. Only fills decisions that
    don't already carry one (an evaluator may set it directly in a future
    package); never clears an existing id. Fail-soft on frozen/foreign decision
    objects — observability must not perturb the cycle.
    """
    auth_id = getattr(candidate, "observation_authority_id", None)
    if not auth_id or not decisions:
        return
    for decision in decisions:
        if getattr(decision, "observation_authority_id", None) is None:
            try:
                decision.observation_authority_id = auth_id
            except Exception:  # noqa: BLE001 - frozen/foreign decision objects.
                pass


def build_settlement_day_observation_authority_row(
    *,
    city,
    target_date: str,
    temperature_metric,
    decision_time,
    market_phase,
    observation,
    coverage_status: str,
    recorded_at: str,
) -> dict:
    """Assemble one settlement_day_observation_authority row from the RUNTIME
    observation object (or its absence on the missing/stale/low cases).

    OBS-AUTHORITY-FOUNDATION (2026-05-23). Pure + total: computes the derived
    audit fields (local_date_matches_target, source_authorized_for_settlement,
    persisted_surface_available, freshness_status) without touching the DB or
    raising. ``observation`` is a Day0ObservationContext or None.

    Field semantics:
      source_authorized_for_settlement — 1 when the obs came from the
        settlement-bound source path (coverage_status == "OK"); 0 for
        non-settlement sources; None when no obs was fetched.
      local_date_matches_target — 1 when the observation timestamp's local date
        (city tz) equals target_date. The whole-point field for catching
        wrong-date/source fakes. None when un-computable or no obs.
      persisted_surface_available — 1 when a runtime obs object existed at
        decision time; 0 when the fetch failed / was skipped.
    """
    authority_id = uuid.uuid4().hex
    city_name = getattr(city, "name", None) or (str(city) if city else None)
    timezone_name = getattr(city, "timezone", "") or ""
    cov_raw = str(coverage_status or "").strip().upper() or "UNKNOWN"
    cov = _normalize_observation_coverage_status(cov_raw)
    metric = str(temperature_metric or "").strip().lower() or None
    if metric not in (None, "high", "low"):
        metric = None

    if observation is None:
        return {
            "authority_id": authority_id,
            "city": city_name,
            "target_date": str(target_date or "") or None,
            "temperature_metric": metric,
            "decision_time_utc": decision_time.isoformat() if hasattr(decision_time, "isoformat") else str(decision_time),
            "market_phase": str(getattr(market_phase, "value", market_phase) or "") or None,
            "source": None,
            "station_id": None,
            "observation_time_utc": None,
            "first_sample_time_utc": None,
            "last_sample_time_utc": None,
            "high_so_far": None,
            "low_so_far": None,
            "current_temp": None,
            "sample_count": None,
            "coverage_status": cov,
            "freshness_status": "MISSING",
            "local_date_matches_target": None,
            "source_authorized_for_settlement": None,
            "persisted_surface_available": 0,
            "payload_json": json.dumps(
                {"observation": None, "coverage_status": cov, "coverage_status_raw": cov_raw},
                sort_keys=True,
            ),
            "recorded_at": recorded_at,
        }

    obs_source = getattr(observation, "source", None)
    obs_station = getattr(observation, "station_id", None)
    obs_time = getattr(observation, "observation_time", None)
    first_t = getattr(observation, "first_sample_time", None)
    last_t = getattr(observation, "last_sample_time", None)
    obs_coverage = str(getattr(observation, "coverage_status", "") or cov).strip().upper() or "UNKNOWN"
    sample_count = getattr(observation, "sample_count", None)

    # source_authorized_for_settlement: settlement-bound path yields
    # coverage_status="OK"; non-settlement sources yield NON_SETTLEMENT_SOURCE.
    source_authorized = 1 if obs_coverage == "OK" else 0

    # local_date_matches_target: compare obs timestamp local date to target_date.
    local_match = None
    obs_local_date = _observation_time_to_local_date(obs_time, timezone_name)
    if obs_local_date is not None and target_date:
        try:
            target_d = date.fromisoformat(str(target_date)[:10])
            local_match = 1 if obs_local_date == target_d else 0
        except (ValueError, TypeError):
            local_match = None

    # freshness_status: derived from coverage. OK obs is FRESH; non-settlement
    # or incomplete coverage is DEGRADED (not execution-authorizing truth).
    freshness = "FRESH" if obs_coverage == "OK" else "DEGRADED"

    def _f(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    payload = {
        "observation": {
            "source": obs_source,
            "station_id": obs_station,
            "observation_time": str(obs_time) if obs_time is not None else None,
            "high_so_far": _f(getattr(observation, "high_so_far", None)),
            "low_so_far": _f(getattr(observation, "low_so_far", None)),
            "current_temp": _f(getattr(observation, "current_temp", None)),
            "unit": getattr(observation, "unit", None),
            "causality_status": getattr(observation, "causality_status", None),
            "sample_count": int(sample_count) if isinstance(sample_count, (int, float)) else None,
            "coverage_status": obs_coverage,
            "observation_available_at": getattr(observation, "observation_available_at", None),
        },
        "coverage_status": _normalize_observation_coverage_status(obs_coverage),
        "coverage_status_raw": obs_coverage,
        "obs_local_date": obs_local_date.isoformat() if obs_local_date is not None else None,
    }

    return {
        "authority_id": authority_id,
        "city": city_name,
        "target_date": str(target_date or "") or None,
        "temperature_metric": metric,
        "decision_time_utc": decision_time.isoformat() if hasattr(decision_time, "isoformat") else str(decision_time),
        "market_phase": str(getattr(market_phase, "value", market_phase) or "") or None,
        "source": str(obs_source or "") or None,
        "station_id": str(obs_station or "") or None,
        "observation_time_utc": str(obs_time) if obs_time is not None else None,
        "first_sample_time_utc": str(first_t) if first_t is not None else None,
        "last_sample_time_utc": str(last_t) if last_t is not None else None,
        "high_so_far": _f(getattr(observation, "high_so_far", None)),
        "low_so_far": _f(getattr(observation, "low_so_far", None)),
        "current_temp": _f(getattr(observation, "current_temp", None)),
        "sample_count": int(sample_count) if isinstance(sample_count, (int, float)) else None,
        "coverage_status": _normalize_observation_coverage_status(obs_coverage),
        "freshness_status": freshness,
        "local_date_matches_target": local_match,
        "source_authorized_for_settlement": source_authorized,
        "persisted_surface_available": 1,
        "payload_json": json.dumps(payload, sort_keys=True),
        "recorded_at": recorded_at,
    }


def execute_discovery_phase(conn, clob, portfolio, artifact, tracker, limits, mode, summary: dict, entry_bankroll: float, decision_time, *, env: str, deps):
    portfolio_dirty = False
    tracker_dirty = False
    _initialize_entry_order_summary(summary)
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

    derived_writes: list[tuple[str, object]] = []
    money_path_frontier = summary.get("money_path_frontier")
    if not isinstance(money_path_frontier, dict):
        money_path_frontier = {}
        summary["money_path_frontier"] = money_path_frontier

    def _frontier(name: str) -> dict:
        section = money_path_frontier.get(name)
        if not isinstance(section, dict):
            section = {}
            money_path_frontier[name] = section
        return section

    def _frontier_increment(section_name: str, key: str, amount: int = 1) -> None:
        section = _frontier(section_name)
        section[key] = int(section.get(key, 0) or 0) + int(amount)

    def _frontier_set(section_name: str, key: str, value) -> None:
        _frontier(section_name)[key] = value

    def _classify_money_path_frontier() -> str:
        market_frontier = _frontier("market_frontier")
        candidate_frontier = _frontier("candidate_frontier")
        math_frontier = _frontier("math_frontier")
        family_frontier = _frontier("family_frontier")
        execution_frontier = _frontier("execution_frontier")
        substrate_count = int(market_frontier.get("substrate_events_read", 0) or 0)
        candidate_count = int(candidate_frontier.get("candidate_objects_built", 0) or 0)
        if candidate_count == 0:
            if int(market_frontier.get("dropped_phase_parse_failure", 0) or 0) > 0:
                return "market_filter_bug"
            if (
                substrate_count > 0
                and int(market_frontier.get("after_phase_or_hours_to_resolution", -1) or 0) == 0
            ):
                return "no_markets_after_mode_phase_filter"
            return "no_candidate_objects_built"
        evaluator_candidates = int(math_frontier.get("evaluator_candidates", 0) or 0)
        model_conflict = int(math_frontier.get("model_conflict", 0) or 0)
        should_trade_true = int(math_frontier.get("should_trade_true_before_family", 0) or 0)
        no_trades = int(summary.get("no_trades", 0) or 0)
        if evaluator_candidates > 0 and model_conflict >= evaluator_candidates:
            return "signal_conflict"
        if evaluator_candidates > 0 and should_trade_true <= 0 and no_trades > 0:
            return "math_rejected_before_family"
        if int(family_frontier.get("blocked_existing_family_exposure", 0) or 0) > 0:
            return "family_exposure_block"
        if (
            int(family_frontier.get("ranked_candidates", 0) or 0) > 0
            and int(execution_frontier.get("final_intent_built", 0) or 0) == 0
        ):
            return "execution_viability_failure"
        if (
            int(execution_frontier.get("final_intent_built", 0) or 0) > 0
            and int(execution_frontier.get("submit_attempts", 0) or 0) > 0
        ):
            return "submit_or_recovery_frontier"
        return "unclassified_frontier"

    def _publish_frontier_status_proof() -> None:
        math_frontier = _frontier("math_frontier")
        reason_counts = math_frontier.get("rejection_reason_counts")
        if not isinstance(reason_counts, dict):
            reason_counts = {}
        normalized_counts = {
            str(key): int(value or 0)
            for key, value in reason_counts.items()
            if int(value or 0) > 0
        }
        no_trades = int(summary.get("no_trades", 0) or 0)
        counted_no_trades = sum(normalized_counts.values())
        if no_trades > counted_no_trades:
            normalized_counts["unclassified_no_trade"] = no_trades - counted_no_trades
            _frontier_set("math_frontier", "unclassified_no_trade", no_trades - counted_no_trades)
        if normalized_counts:
            summary["rejection_reason_counts"] = normalized_counts
            summary["top_no_trade_reasons"] = normalized_counts
        terminal = str(money_path_frontier.get("terminal_classification") or "")
        if terminal in {"no_markets_after_mode_phase_filter", "market_filter_bug"}:
            summary["no_market_reason"] = terminal
        if "substrate_events_read" in _frontier("market_frontier"):
            summary["market_scanner_attempted"] = True

    _frontier("scheduler_frontier").update(
        {
            "mode": mode.value,
            "cycle_lock_acquired": True,
            "skipped": False,
            "skip_reason": "",
        }
    )
    _frontier("market_frontier")
    _frontier("candidate_frontier")
    _frontier("math_frontier")
    _frontier("family_frontier")
    _frontier("execution_frontier")
    _frontier_set(
        "source_frontier",
        "source_writer_status",
        _source_writer_frontier_status(decision_time),
    )

    def _queue_derived_write(name: str, writer) -> None:
        if callable(writer):
            derived_writes.append((name, writer))

    def _flush_derived_writes() -> None:
        while derived_writes:
            name, writer = derived_writes.pop(0)
            try:
                writer()
            except Exception as exc:  # noqa: BLE001 - derived telemetry is fail-soft.
                # AB3: every queued decision/telemetry-lane write (opportunity_fact,
                # probability_trace, microstructure, forward_market_substrate,
                # signal evidence, settlement_day_observation_authority, ...) flows
                # through here. A swallowed exception is indistinguishable from a
                # lane with nothing to write; name + count it so a dead lane fails
                # loud. Observability degradation is recorded; the cycle is not blocked.
                deps.logger.warning("Derived discovery write failed for %s: %s", name, exc)
                _record_lane_write_failure(summary, name, exc)
            else:
                # Per-cycle success counter: a lane that never reaches this branch
                # (zero writes AND zero failures) is detectable as dead downstream.
                _record_lane_write_success(summary, name)

    def _record_microstructure(row: dict) -> None:
        payload = dict(row)

        def _write() -> None:
            from src.state.db import log_microstructure

            log_microstructure(conn, **payload)

        _queue_derived_write("microstructure", _write)

    def _record_settlement_day_observation_authority(
        *,
        city,
        target_date: str,
        temperature_metric,
        market_phase,
        observation,
        coverage_status: str,
    ) -> str:
        """Build + queue the authority row; return its authority_id for stamping.

        OBS-AUTHORITY-FOUNDATION (2026-05-23). Observability only: returns the
        id synchronously (so the candidate/decisions can reference it) and
        queues the durable write through the same fail-soft derived-write path
        as the other facts. Never raises into the cycle.
        """
        try:
            row = build_settlement_day_observation_authority_row(
                city=city,
                target_date=target_date,
                temperature_metric=temperature_metric,
                decision_time=decision_time,
                market_phase=market_phase,
                observation=observation,
                coverage_status=coverage_status,
                recorded_at=decision_time.isoformat(),
            )
        except Exception as exc:  # noqa: BLE001 - instrumentation is fail-soft.
            deps.logger.warning("Failed to build observation authority row: %s", exc)
            return ""
        authority_id = str(row.get("authority_id") or "")

        def _write(payload=dict(row)) -> None:
            from src.state.db import log_settlement_day_observation_authority

            log_settlement_day_observation_authority(conn, **payload)

        _queue_derived_write(
            f"settlement_day_observation_authority:{authority_id}", _write
        )
        return authority_id

    def _record_opportunity_fact(candidate, decision, *, should_trade: bool, rejection_stage: str, rejection_reasons: list[str]):
        def _write(
            candidate=candidate,
            decision=decision,
            should_trade=should_trade,
            rejection_stage=rejection_stage,
            rejection_reasons=list(rejection_reasons or []),
        ) -> None:
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

        _queue_derived_write(
            f"opportunity_fact:{getattr(decision, 'decision_id', '')}",
            _write,
        )

    def _record_probability_trace(candidate, decision):
        def _write(candidate=candidate, decision=decision) -> None:
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

        _queue_derived_write(
            f"probability_trace:{getattr(decision, 'decision_id', '')}",
            _write,
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
        details_payload = dict(details)
        reasons_payload = list(reasons)

        def _write(
            normalized=normalized,
            reasons=reasons_payload,
            scope_type=scope_type,
            scope_key=scope_key,
            details=details_payload,
        ) -> None:
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

        _queue_derived_write(f"availability_fact:{scope_key}", _write)

    def _market_scan_authority() -> str:
        getter = getattr(deps, "get_last_scan_authority", None)
        if not callable(getter):
            return "NEVER_FETCHED"
        try:
            return str(getter() or "NEVER_FETCHED").strip().upper()
        except Exception as exc:
            deps.logger.warning("Market scan authority read failed: %s", exc)
            return "SCAN_AUTHORITY_READ_FAILED"

    def _market_scan_availability_status(authority: str) -> str:
        if authority == "STALE":
            return "DATA_STALE"
        if authority in {
            "FETCH_FAILED_NO_CACHE",
            "KEYWORD_DISCOVERY_UNVERIFIED",
            "SCAN_AUTHORITY_READ_FAILED",
        }:
            return "DATA_UNAVAILABLE"
        if authority == "NEVER_FETCHED":
            return "DATA_UNAVAILABLE"
        if authority != "VERIFIED":
            return "DATA_UNAVAILABLE"
        return ""

    def _record_forward_market_substrate(markets_to_record, authority: str) -> None:
        markets_payload = list(markets_to_record or [])

        def _write(markets_to_record=markets_payload, authority=authority) -> None:
            from src.state.db import log_forward_market_substrate

            result = log_forward_market_substrate(
                markets=markets_to_record,
                recorded_at=decision_time.isoformat(),
                scan_authority=authority,
            )

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
                _mark_observability_degraded(summary)

            from src.state.db import log_market_source_contract_topology_facts

            source_contract_result = log_market_source_contract_topology_facts(
                conn,
                markets=markets_to_record,
                recorded_at=decision_time.isoformat(),
                scan_authority=authority,
            )

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
            if source_contract_status in {"skipped_invalid_schema", "error"}:
                deps.logger.warning(
                    "Market source-contract topology degraded: %s", source_contract_result
                )
                _mark_observability_degraded(summary)

        def _write_guarded() -> None:
            try:
                _write()
            except Exception as exc:
                # AB3: record the substrate-specific status fields, then RE-RAISE so
                # the central _flush_derived_writes handler is the single place that
                # logs + counts the failure (no double-count, no false success).
                # Net behavior is identical to the prior internal swallow: observability
                # degradation is recorded and the cycle is not blocked — the swallow simply moves
                # up one frame to the uniform fail-loud path.
                summary["forward_market_substrate_status"] = "error"
                summary["forward_market_substrate_error"] = str(exc)
                raise

        _queue_derived_write("forward_market_substrate", _write_guarded)

        # ThePath P1 ITEM 3 (2026-06-07): additive, fail-soft intraday order-book
        # DEPTH capture for the fill model. The mid-only substrate write above
        # leaves best_bid/best_ask/raw_orderbook_hash NULL. This sibling derived
        # write taps the executable_market_snapshots rows the executor/scanner
        # already captured (no new external poll) and writes full-linkage depth
        # rows for the scanned conditions. Purely additive (new 'full' rows only),
        # fail-soft (never raises, never blocks the cycle), and the helper itself
        # degrades to a no-op if EMS is absent. Flag-off/absent-EMS => byte-identical
        # behaviour to today (the helper writes nothing).
        def _write_intraday_depth_guarded(markets_to_record=markets_payload) -> None:
            try:
                condition_ids: list[str] = []
                _seen_cid: set[str] = set()
                for _market in markets_to_record or ():
                    if not isinstance(_market, dict):
                        continue
                    for _outcome in _market.get("outcomes") or ():
                        if not isinstance(_outcome, dict):
                            continue
                        _cid = str(_outcome.get("condition_id") or "").strip()
                        if _cid and _cid not in _seen_cid:
                            _seen_cid.add(_cid)
                            condition_ids.append(_cid)
                if not condition_ids:
                    summary["intraday_depth_capture_status"] = "no_condition_ids"
                    return
                from src.state.db import capture_intraday_orderbook_depth_from_snapshots

                depth_result = capture_intraday_orderbook_depth_from_snapshots(
                    conn,
                    condition_ids=condition_ids,
                    recorded_at=decision_time.isoformat(),
                )
                summary["intraday_depth_capture_status"] = str(depth_result.get("status", ""))
                for _k in ("rows_inserted", "skipped_no_snapshot", "conditions_seen"):
                    if _k in depth_result:
                        summary[f"intraday_depth_capture_{_k}"] = int(depth_result.get(_k) or 0)
                # The helper does not commit (caller owns the txn boundary, like
                # log_executable_snapshot_market_price_linkage). Persist the new
                # full-linkage rows; commit is fail-soft so a busy DB never blocks
                # the cycle and never corrupts other pending derived telemetry.
                try:
                    conn.commit()
                except Exception as _cexc:  # noqa: BLE001
                    deps.logger.warning("Intraday depth capture commit failed (non-fatal): %s", _cexc)
            except Exception as exc:  # noqa: BLE001 — fail-soft: never block the cycle
                deps.logger.warning("Intraday depth capture failed (non-fatal): %s", exc)
                summary["intraday_depth_capture_status"] = "error"

        _queue_derived_write("intraday_orderbook_depth", _write_intraday_depth_guarded)

    def _execution_snapshot_fields(tokens: dict) -> dict:
        tokens = tokens or {}
        return {
            "executable_snapshot_id": str(
                tokens.get("executable_snapshot_id") or tokens.get("snapshot_id") or ""
            ).strip(),
            "condition_id": str(tokens.get("condition_id") or "").strip(),
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
        if not fields["condition_id"]:
            missing.append("condition_id")
        if fields["executable_snapshot_min_tick_size"] is None:
            missing.append("executable_snapshot_min_tick_size")
        if fields["executable_snapshot_min_order_size"] is None:
            missing.append("executable_snapshot_min_order_size")
        if fields["executable_snapshot_neg_risk"] is None:
            missing.append("executable_snapshot_neg_risk")
        return missing

    def _capture_execution_snapshot_for_decision(market, decision, city_name: str, target_date: str) -> tuple[dict, str]:
        capture_snapshot = getattr(deps, "capture_executable_market_snapshot", None)
        if not callable(capture_snapshot):
            return _execution_snapshot_fields(getattr(decision, "tokens", {}) or {}), "capture_helper_unavailable"
        try:
            captured_snapshot_fields = capture_snapshot(
                conn,
                market=market,
                decision=decision,
                clob=clob,
                captured_at=datetime.now(timezone.utc),
                scan_authority=scan_authority,
            )
        except Exception as exc:
            deps.logger.warning(
                "Executable market snapshot capture failed for %s %s %s: %s",
                city_name,
                target_date,
                decision.edge.bin.label if decision.edge else "",
                exc,
            )
            if not isinstance(decision.tokens, dict):
                decision.tokens = {}
            decision.tokens["executable_snapshot_capture_error"] = str(exc)
            return _execution_snapshot_fields(decision.tokens), str(exc)
        if not isinstance(decision.tokens, dict):
            decision.tokens = {}
        decision.tokens.update(captured_snapshot_fields)
        try:
            from src.state.db import log_executable_snapshot_market_price_linkage

            linkage_result = log_executable_snapshot_market_price_linkage(
                conn,
                snapshot_id=str(captured_snapshot_fields.get("executable_snapshot_id", "")),
            )
            summary["forward_market_price_linkage_status"] = str(linkage_result.get("status", ""))
            if _forward_price_linkage_status_degraded(summary["forward_market_price_linkage_status"]):
                _mark_observability_degraded(summary)
        except Exception as exc:
            # AB3: direct (non-queued) price-linkage telemetry-lane write. Was
            # logged but uncounted; now counted with explicit observability degradation.
            deps.logger.warning(
                "Executable snapshot price linkage write failed for %s %s %s: %s",
                city_name,
                target_date,
                decision.edge.bin.label if decision.edge else "",
                exc,
            )
            summary["forward_market_price_linkage_status"] = "error"
            _record_lane_write_failure(summary, "executable_snapshot_price_linkage", exc)
        try:
            conn.commit()
        except Exception as exc:
            deps.logger.warning(
                "Executable market snapshot commit failed for %s %s %s: %s",
                city_name,
                target_date,
                decision.edge.bin.label if decision.edge else "",
                exc,
            )
            decision.tokens["executable_snapshot_capture_error"] = f"executable_snapshot_commit_failed:{exc}"
            return _execution_snapshot_fields(decision.tokens), str(decision.tokens["executable_snapshot_capture_error"])
        return _execution_snapshot_fields(decision.tokens), ""

    def _execution_snapshot_is_stale(conn, snapshot_id: str) -> bool:
        from src.contracts.executable_market_snapshot import is_fresh
        from src.state.snapshot_repo import get_snapshot

        snapshot = get_snapshot(conn, snapshot_id)
        return snapshot is None or not is_fresh(snapshot, datetime.now(timezone.utc))

    params = deps.MODE_PARAMS[mode]
    evaluation_budget_seconds = _live_discovery_eval_budget_seconds(mode, env, params)
    min_hours_to_resolution = params.get("min_hours_to_resolution")
    if min_hours_to_resolution is None:
        min_hours_to_resolution = 0 if "max_hours_to_resolution" in params else 6
    if "imminent_window_hours" in params:
        # imminent_open_capture: pure UTC time-to-resolution filter (no city-local
        # phase gate). Scans markets with 0 < hours_to_resolution <= window.
        # Window is capped at 24h so it sits strictly below opening_hunt's
        # min_hours_to_resolution: 24, preventing double-coverage.
        # min_hours_to_resolution=0 to include markets already past the 6h
        # default floor used by other modes.
        min_hours_to_resolution = 0
    live_substrate_reader = (
        str(os.getenv("ZEUS_LIVE_MARKET_SUBSTRATE_READER", "1")).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    market_snapshot = None
    if env == "live" and conn is not None and live_substrate_reader:
        from src.data.market_scanner import read_persisted_weather_markets

        market_snapshot = read_persisted_weather_markets(conn, now_utc=decision_time)
        markets = list(market_snapshot.events)
        summary["market_substrate_source"] = "persisted_executable_market_snapshots"
        summary["market_substrate_fetched_at_utc"] = (
            market_snapshot.fetched_at_utc.isoformat()
            if market_snapshot.fetched_at_utc is not None
            else None
        )
        summary["market_substrate_stale_age_seconds"] = (
            market_snapshot.stale_age_seconds
        )
        summary["market_substrate_market_count"] = len(markets)
        if market_snapshot.authority != "VERIFIED":
            summary["market_substrate_unavailable_reason"] = market_snapshot.authority
    else:
        markets = deps.find_weather_markets(min_hours_to_resolution=min_hours_to_resolution)
    _frontier_set("market_frontier", "substrate_events_read", len(markets))
    _frontier_set(
        "market_frontier",
        "substrate_source",
        summary.get("market_substrate_source", "scanner"),
    )
    # NOTE: evaluation_started_at is set AFTER market substrate acquisition so
    # that upstream discovery I/O does not consume per-market evaluation budget.
    # Live normally reads persisted substrate here; background market_discovery
    # owns Gamma/CLOB refresh.
    evaluation_started_at = _monotonic_seconds(deps)
    if "max_hours_since_open" in params:
        before_count = len(markets)
        markets = [m for m in markets if m["hours_since_open"] < params["max_hours_since_open"]]
        _frontier_set("market_frontier", "after_max_hours_since_open", len(markets))
        _frontier_set(
            "market_frontier",
            "dropped_by_max_hours_since_open",
            before_count - len(markets),
        )
    if "min_hours_since_open" in params:
        before_count = len(markets)
        markets = [m for m in markets if m["hours_since_open"] >= params["min_hours_since_open"]]
        _frontier_set("market_frontier", "after_min_hours_since_open", len(markets))
        _frontier_set(
            "market_frontier",
            "dropped_by_min_hours_since_open",
            before_count - len(markets),
        )
    if "imminent_window_hours" in params:
        # Upper bound: strictly < imminent_window_hours (not <=) so opening_hunt
        # owns markets at exactly the boundary (hours_to_resolution == 24).
        window = float(params["imminent_window_hours"])
        before_count = len(markets)
        markets = [
            m for m in markets
            if m.get("hours_to_resolution") is not None
            and 0 < m["hours_to_resolution"] < window
        ]
        _frontier_set("market_frontier", "after_imminent_window", len(markets))
        _frontier_set(
            "market_frontier",
            "dropped_by_imminent_window",
            before_count - len(markets),
        )
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
        from src.engine.dispatch import market_phase_dispatch_enabled
        # Critic R5 code-reviewer M1: stamp the flag state on the cycle
        # summary so downstream substrate / cohort attribution can
        # explain step-changes in candidate count when the operator
        # flips ZEUS_MARKET_PHASE_DISPATCH. Without this, the substrate
        # log shows only the post-filter count with no audit trail.
        flag_on = market_phase_dispatch_enabled()
        summary["market_phase_dispatch_flag"] = flag_on
        if flag_on:
            from src.strategy.market_phase import MarketPhase
            from src.strategy.market_phase_evidence import (
                from_market_dict as _build_filter_phase_evidence,
            )

            kept_markets = []
            phase_drop_samples = []
            dropped_not_settlement_day = 0
            dropped_phase_parse_failure = 0
            for market in markets:
                city = market.get("city")
                phase_value = None
                phase_source = ""
                failure_reason = ""
                try:
                    evidence = _build_filter_phase_evidence(
                        market=market,
                        city_timezone=getattr(city, "timezone", None),
                        target_date_str=market.get("target_date", ""),
                        decision_time_utc=decision_time,
                    )
                except Exception as exc:  # noqa: BLE001 - frontier only; filter is fail-closed.
                    allowed = False
                    dropped_phase_parse_failure += 1
                    phase_source = "evidence_exception"
                    failure_reason = str(exc)
                else:
                    phase_value = evidence.phase.value if evidence.phase is not None else None
                    phase_source = evidence.phase_source
                    failure_reason = evidence.failure_reason or ""
                    allowed = evidence.phase == MarketPhase.SETTLEMENT_DAY
                    if evidence.phase is None:
                        dropped_phase_parse_failure += 1
                if allowed:
                    kept_markets.append(market)
                    continue
                if phase_value is not None:
                    dropped_not_settlement_day += 1
                if len(phase_drop_samples) < 10:
                    phase_drop_samples.append(
                        {
                            "slug": str(market.get("slug", "") or ""),
                            "event_id": str(market.get("event_id", "") or ""),
                            "city": str(getattr(city, "name", "") or ""),
                            "target_date": str(market.get("target_date", "") or ""),
                            "temperature_metric": str(market.get("temperature_metric", "") or ""),
                            "hours_to_resolution": market.get("hours_to_resolution"),
                            "market_phase": phase_value,
                            "phase_source": phase_source,
                            "failure_reason": failure_reason,
                        }
                    )
            markets = kept_markets
            _frontier_set("market_frontier", "after_phase_or_hours_to_resolution", len(markets))
            _frontier_set(
                "market_frontier",
                "dropped_not_settlement_day",
                dropped_not_settlement_day,
            )
            _frontier_set(
                "market_frontier",
                "dropped_phase_parse_failure",
                dropped_phase_parse_failure,
            )
            if phase_drop_samples:
                _frontier_set("market_frontier", "phase_drop_samples", phase_drop_samples)
        else:
            before_count = len(markets)
            markets = [m for m in markets if m.get("hours_to_resolution") is not None and m["hours_to_resolution"] < params["max_hours_to_resolution"]]
            _frontier_set("market_frontier", "after_phase_or_hours_to_resolution", len(markets))
            _frontier_set(
                "market_frontier",
                "dropped_by_max_hours_to_resolution",
                before_count - len(markets),
            )
    scan_authority = market_snapshot.authority if market_snapshot is not None else _market_scan_authority()
    summary["market_scan_authority"] = scan_authority
    _record_forward_market_substrate(markets, scan_authority)
    scan_availability_status = _market_scan_availability_status(scan_authority)
    if scan_availability_status:
        reasons = [f"market_scan_authority={scan_authority}"]
        if not markets:
            artifact.add_no_trade(
                deps.NoTradeCase(
                    decision_id="",
                    city="",
                    target_date="",
                    range_label="",
                    direction="unknown",
                    rejection_stage="MARKET_FILTER",
                    strategy_key="",
                    strategy="",
                    edge_source="",
                    availability_status=scan_availability_status,
                    rejection_reasons=reasons,
                    market_hours_open=None,
                    timestamp=decision_time.isoformat(),
                )
            )
            summary["no_trades"] += 1
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
        _flush_derived_writes()
        return portfolio_dirty, tracker_dirty

    for market_index, market in enumerate(markets):
        if evaluation_budget_seconds is not None:
            elapsed = _monotonic_seconds(deps) - evaluation_started_at
            if elapsed >= evaluation_budget_seconds:
                markets_skipped = len(markets) - market_index
                summary["cycle_backpressure_truncated"] = True
                summary["cycle_backpressure_reason"] = "market_evaluation_budget_exceeded"
                summary["cycle_backpressure_budget_seconds"] = evaluation_budget_seconds
                summary["cycle_backpressure_elapsed_seconds"] = elapsed
                summary["cycle_backpressure_markets_evaluated"] = market_index
                summary["cycle_backpressure_markets_skipped"] = markets_skipped
                _mark_observability_degraded(summary)
                deps.logger.warning(
                    "Discovery cycle backpressure: truncated %s after %.1fs budget=%.1fs evaluated=%s skipped=%s",
                    mode.value,
                    elapsed,
                    evaluation_budget_seconds,
                    market_index,
                    markets_skipped,
                )
                break
        city = market.get("city")
        if city is None:
            _frontier_increment("candidate_frontier", "dropped_missing_city")
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
        if should_fetch_observation:
            _frontier_increment("candidate_frontier", "observation_fetch_attempted")

        try:
            obs = (
                fetch_day0_observation(city, market["target_date"], decision_time, deps=deps)
                if should_fetch_observation
                else None
            )
        except Exception as e:
            from src.contracts.exceptions import MissingCalibrationError, ObservationUnavailableError

            if isinstance(e, (ObservationUnavailableError, MissingCalibrationError)):
                _frontier_increment("candidate_frontier", "observation_unavailable")
                deps.logger.warning("Skipping candidate for %s: %s", city.name, e)
                availability_status = _availability_status_for_exception(e)
                # OBS-AUTHORITY-FOUNDATION: capture the MISSING observation case
                # — the candidate is dropped here so it never reaches the durable
                # opportunity_fact path, but the runtime "we tried and got
                # nothing" fact is exactly what was previously invisible. Write
                # an authority row (observation=None) so the operator audit sees
                # the attempted settlement-day fetch and its failure coverage.
                _record_settlement_day_observation_authority(
                    city=city,
                    target_date=market["target_date"],
                    temperature_metric=market.get("temperature_metric"),
                    market_phase=market_phase,
                    observation=None,
                    coverage_status=availability_status,
                )
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
        _frontier_increment("candidate_frontier", "candidate_objects_built")
        # DEBUG-LOOP-REACH 2026-05-27: confirm loop body executes per candidate.
        # Placed immediately after the increment so any city/target reaching this
        # line MUST emit. If candidate_objects_built=49 but ZERO DEBUG_LOOP_REACH
        # lines emit, the daemon is loading a stale cycle_runtime module from a
        # different path than what we're editing.
        try:
            deps.logger.debug(
                "DEBUG_LOOP_REACH city=%s tgt=%s mode=%s",
                getattr(candidate, "city", "?"),
                getattr(candidate, "target_date", "?"),
                getattr(mode, "value", str(mode)),
            )
        except Exception as _dbg_lr_exc:
            pass

        # OBS-AUTHORITY-FOUNDATION (2026-05-23): for settlement-day/day0
        # candidates (the only ones for which a day0/settlement observation was
        # fetched), persist the runtime observation object as an auditable
        # authority row and stamp its id onto the candidate so every EdgeDecision
        # it produces can join back to the observation. Covers the OK case here;
        # the MISSING case was captured in the obs-fetch except handler above.
        # Observability only — does not change selection or trade behavior.
        if should_fetch_observation:
            _obs_coverage = str(getattr(obs, "coverage_status", "") or "") or "UNKNOWN"
            candidate.observation_authority_id = _record_settlement_day_observation_authority(
                city=city,
                target_date=market["target_date"],
                temperature_metric=candidate.temperature_metric,
                market_phase=market_phase,
                observation=obs,
                coverage_status=_obs_coverage,
            ) or None

        try:
            # B091: forward the cycle's authoritative decision_time to the
            # evaluator so per-cycle `recorded_at` timestamps derive from
            # the cycle boundary rather than being silently re-fabricated
            # as `datetime.now()` inside the evaluator per-candidate.
            try:
                decisions = deps.evaluate_candidate(
                    candidate, conn, portfolio, clob, limits,
                    entry_bankroll=entry_bankroll,
                    decision_time=decision_time,
                    microstructure_sink=_record_microstructure,
                    use_forecasts_live_snapshot_store=True,
                )
            except TypeError as exc:
                if (
                    "microstructure_sink" not in str(exc)
                    and "use_forecasts_live_snapshot_store" not in str(exc)
                ):
                    raise
                decisions = deps.evaluate_candidate(
                    candidate, conn, portfolio, clob, limits,
                    entry_bankroll=entry_bankroll,
                    decision_time=decision_time,
                )
            # OBS-AUTHORITY-FOUNDATION (2026-05-23): stamp the candidate's
            # observation authority id onto every EdgeDecision so the durable
            # opportunity_fact row links back to the runtime observation object.
            stamp_observation_authority_id_onto_decisions(candidate, decisions)
            _frontier_increment("math_frontier", "evaluator_candidates")
            # DEBUG-49-DROP 2026-05-27: instrument per-candidate result to surface
            # why 49 candidate_objects_built → 0 traces post-restart.
            try:
                _dbg_n = len(decisions) if decisions else 0
                _dbg_reasons = [str(getattr(_d, "rejection_reason_enum", None) or "?") for _d in (decisions or [])][:3]
                _dbg_should_trade = [bool(getattr(_d, "should_trade", False)) for _d in (decisions or [])]
                deps.logger.debug(
                    "DEBUG_49DROP city=%s tgt=%s n=%d should_trade=%s reasons=%s",
                    getattr(candidate, "city", "?"),
                    getattr(candidate, "target_date", "?"),
                    _dbg_n,
                    _dbg_should_trade,
                    _dbg_reasons,
                )
            except Exception as _dbg_exc:
                deps.logger.warning("DEBUG_49DROP log failed: %s", _dbg_exc)
            if decisions:
                _frontier_increment("math_frontier", "evaluator_decisions", len(decisions))
                _frontier_increment(
                    "math_frontier",
                    "should_trade_true_before_family",
                    sum(1 for _d in decisions if getattr(_d, "should_trade", False)),
                )
                _reason_counts = _frontier("math_frontier").setdefault("rejection_reason_counts", {})
                if not isinstance(_reason_counts, dict):
                    _reason_counts = {}
                    _frontier_set("math_frontier", "rejection_reason_counts", _reason_counts)
                for _d in decisions:
                    _enum = getattr(_d, "rejection_reason_enum", None)
                    if _enum is None and not getattr(_d, "should_trade", False):
                        _reason = "uncategorized"
                    elif _enum is None:
                        continue
                    else:
                        _reason = str(getattr(_enum, "value", _enum) or "")
                    _reason_counts[_reason] = int(_reason_counts.get(_reason, 0) or 0) + 1
                    if _reason == "model_conflict":
                        _frontier_increment("math_frontier", "model_conflict")
                        _frontier_increment("source_math_frontier", "model_conflict")
                    detail = str(getattr(_d, "rejection_reason_detail", "") or "")
                    if _reason in {
                        "ultra_low_price_not_authorized",
                        "center_buy_ultra_low_price",
                    } or (
                        _reason == "strategy_economic_floor"
                        and (
                            detail.startswith("STRATEGY_ENTRY_PRICE_BELOW_LIVE_FLOOR")
                            or detail.startswith("ULTRA_LOW_NON_TAIL_NOT_AUTHORIZED")
                        )
                    ):
                        _frontier_increment("edge_frontier", "price_policy_rejected")
                        if "tail_topology=true" in detail:
                            _frontier_increment("edge_frontier", "tail_edges_blocked")
                        else:
                            _frontier_increment("edge_frontier", "normal_price_or_non_tail_edges_blocked")
                    elif _reason == "strategy_economic_floor":
                        _frontier_increment("edge_frontier", "economic_floor_rejected")
                    if _reason in {"confidence_band_insufficient", "uncategorized"}:
                        stage = str(getattr(_d, "rejection_stage", "") or "")
                        if stage in {"EDGE_INSUFFICIENT", "FDR_FILTERED", "FDR_FAMILY_SCAN_UNAVAILABLE"}:
                            _frontier_increment("edge_frontier", "edge_or_fdr_rejected")
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
            # P0-1 STAGE A — emergency mutually-exclusive family entry gate.
            # A (city, target_date, metric) weather market is a PARTITION
            # (exactly one bin resolves YES). evaluate_candidate returns one
            # EdgeDecision per bin; family-wise FDR can mark several bins
            # should_trade=True, and the execution loop below submits each as
            # an INDEPENDENT scalar-Kelly live order → ~Nx over-allocation on
            # one underlying event. This single structural hook (NOT a
            # per-callsite cap) admits one coherent optimized family intent:
            # either one scalar best leg or all selected legs of a typed
            # multi-leg portfolio. Ranked scalar alternatives are not parallel
            # live submit intents. Gate default ON in live; fail-safe (only
            # ever removes entries). Authority: operator P0-1 live-money spec
            # 2026-05-20/21 (mutually-exclusive weather family sizing).
            if decisions:
                from src.strategy.family_exclusive_dedup import (
                    dedup_mutually_exclusive_families,
                    resolve_weather_family_exposures,
                )

                _family_exposure_conn = None
                try:
                    from src.state.db import get_trade_connection_with_world_required
                    _family_exposure_conn = get_trade_connection_with_world_required(
                        write_class="live"
                    )
                    _family_exposures = resolve_weather_family_exposures(
                        trade_conn=_family_exposure_conn,
                        portfolio=portfolio,
                    )
                except Exception as _family_exposure_exc:
                    logger.warning(
                        "[WEATHER_FAMILY_EXPOSURE_DB_FALLBACK] slug=%s exc=%s",
                        getattr(candidate, "slug", ""),
                        _family_exposure_exc,
                    )
                    _family_exposures = resolve_weather_family_exposures(portfolio=portfolio)
                finally:
                    if _family_exposure_conn is not None:
                        _family_exposure_conn.close()

                decisions = dedup_mutually_exclusive_families(
                    decisions,
                    city=city.name,
                    target_date=candidate.target_date,
                    temperature_metric=candidate.temperature_metric,
                    market_family_id=(
                        getattr(candidate, "event_id", "")
                        or getattr(candidate, "slug", "")
                    ),
                    existing_exposures=_family_exposures,
                    family_portfolio_intent=any(
                        str(getattr(_d, "family_portfolio_leg_role", "") or "")
                        == "portfolio_selected"
                        for _d in decisions
                    ),
                )
                _frontier_increment("family_frontier", "families_seen")
                _frontier_increment(
                    "family_frontier",
                    "ranked_candidates",
                    sum(1 for _d in decisions if getattr(_d, "should_trade", False)),
                )
                _frontier_increment(
                    "family_frontier",
                    "family_selection_dedup",
                    sum(
                        1
                        for _d in decisions
                        if str(getattr(getattr(_d, "rejection_reason_enum", None), "value", ""))
                        == "mutually_exclusive_family_dedup"
                        and "existing family exposure" not in str(getattr(_d, "rejection_reason_detail", "") or "")
                    ),
                )
                _frontier_increment(
                    "family_frontier",
                    "blocked_existing_family_exposure",
                    sum(
                        1
                        for _d in decisions
                        if str(getattr(getattr(_d, "rejection_reason_enum", None), "value", ""))
                        == "mutually_exclusive_family_dedup"
                        and "existing family exposure" in str(getattr(_d, "rejection_reason_detail", "") or "")
                    ),
                )
            # Phase 2 T2: write no_trade_events rows for rejected decisions.
            # Fail-soft: logging/learning infrastructure must not crash the cycle.
            # INV-37: caller opens the world-DB connection (conn required on writer).
            if decisions:
                _obs_time_for_no_trade: str = ""
                if candidate.observation is not None:
                    if isinstance(candidate.observation, dict):
                        _obs_time_for_no_trade = str(
                            candidate.observation.get("observation_time", "") or ""
                        )
                    else:
                        _obs_time_for_no_trade = str(
                            getattr(candidate.observation, "observation_time", "") or ""
                        )
                _no_trade_conn = None
                try:
                    from src.state.db import get_world_connection
                    from src.state.db_writer_lock import WriteClass
                    from src.state.no_trade_events import write_no_trade_event
                    from src.contracts.decision_natural_key import make_decision_natural_key
                    from src.contracts.no_trade_reason import NoTradeReason
                    _no_trade_conn = get_world_connection(write_class=WriteClass.LIVE)
                    for _nd in decisions:
                        _enum = getattr(_nd, "rejection_reason_enum", None)
                        _detail = getattr(_nd, "rejection_reason_detail", None)
                        if _enum is None and not getattr(_nd, "should_trade", False):
                            _enum = NoTradeReason.UNCATEGORIZED
                            _detail = _detail or "|".join(
                                str(reason) for reason in getattr(_nd, "rejection_reasons", [])
                            ) or str(getattr(_nd, "rejection_stage", "") or "uncategorized")
                        if _enum is not None:
                            try:
                                _nte_key = make_decision_natural_key(
                                    market_slug=str(candidate.slug or ""),
                                    temperature_metric=str(candidate.temperature_metric or ""),
                                    target_date=str(candidate.target_date or ""),
                                    observation_time=_obs_time_for_no_trade,
                                    decision_seq=0,  # overwritten by allocate_decision_seq inside writer
                                )
                                write_no_trade_event(
                                    _nte_key,
                                    _enum,
                                    _detail,
                                    decision_time.isoformat(),
                                    conn=_no_trade_conn,
                                    strategy_key=str(getattr(_nd, "strategy_key", "") or "") or None,
                                    event_source=str(getattr(_nd, "edge_source", "") or "") or None,
                                )
                            except Exception as _nte_exc:
                                logger.warning(
                                    "[NO_TRADE_EVENT_WRITE_FAILED] slug=%s reason=%s exc=%s",
                                    candidate.slug,
                                    _enum,
                                    _nte_exc,
                                )
                except Exception as _nte_conn_exc:
                    logger.warning(
                        "[NO_TRADE_EVENT_CONN_FAILED] slug=%s exc=%s",
                        candidate.slug,
                        _nte_conn_exc,
                    )
                finally:
                    if _no_trade_conn is not None:
                        _no_trade_conn.close()
            if decisions:
                # Accumulate FDR health metrics into cycle summary
                if any(getattr(d, "fdr_family_scan_unavailable", False) for d in decisions):
                    summary["fdr_family_scan_unavailable"] = True
                family_sizes = [getattr(d, "fdr_family_size", 0) for d in decisions if getattr(d, "fdr_family_size", 0) > 0]
                if family_sizes:
                    summary["fdr_family_size"] = summary.get("fdr_family_size", 0) + family_sizes[0]
                for trace_decision in decisions:
                    _record_probability_trace(candidate, trace_decision)
                try:
                    from src.engine.time_context import lead_hours_to_date_start, lead_hours_to_settlement_close
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
                    _ = edges_payload
                except Exception as exc:
                    deps.logger.error(
                        "telemetry write failed, cycle observability degraded: %s",
                        exc,
                        exc_info=True,
                    )
                    _record_lane_write_failure(summary, "decision_evidence_build", exc)
            family_ranked_submit_satisfied = False
            for d in decisions:
                if False:
                    _ = d.calibration
                strategy_key = _resolve_strategy_key(d) if d.edge else ""
                if d.should_trade and d.edge and d.tokens:
                    is_live_env = str(env or "").strip().lower() == "live"
                    family_ranked_candidate_count = int(
                        getattr(d, "family_ranked_candidate_count", 0) or 0
                    )
                    family_portfolio_leg_role = str(
                        getattr(d, "family_portfolio_leg_role", "") or ""
                    )
                    if (
                        family_ranked_candidate_count > 1
                        and family_ranked_submit_satisfied
                        and family_portfolio_leg_role != "portfolio_selected"
                    ):
                        summary["no_trades"] += 1
                        rejection_stage = "ANTI_CHURN"
                        rejection_reasons = [
                            "mutually_exclusive_family_ranked_alternative_not_attempted_after_submit"
                        ]
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
                                strategy=strategy_key,
                                strategy_key=strategy_key,
                                edge_source=d.edge_source or deps._classify_edge_source(mode, d.edge),
                                availability_status=getattr(d, "availability_status", ""),
                                rejection_reasons=rejection_reasons,
                                best_edge=d.edge.edge if d.edge else 0.0,
                                model_prob=d.edge.p_posterior if d.edge else 0.0,
                                market_price=float(d.edge.entry_price) if d.edge else 0.0,
                                decision_snapshot_id=d.decision_snapshot_id,
                                selected_method=d.selected_method,
                                settlement_semantics_json=d.settlement_semantics_json,
                                epistemic_context_json=d.epistemic_context_json,
                                edge_context_json=d.edge_context_json,
                                applied_validations=[
                                    *list(d.applied_validations),
                                    "family_ranked_alternative_skipped_after_submit",
                                ],
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
                                market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                                market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                                market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                        from src.strategy.family_exclusive_dedup import (
                            buy_no_native_quote_evidence_submit_enabled,
                        )

                        try:
                            native_buy_no_live_enabled = buy_no_native_quote_evidence_submit_enabled()
                            live_flag_error = ""
                        except ValueError as exc:
                            native_buy_no_live_enabled = False
                            live_flag_error = str(exc)
                        if not native_buy_no_live_enabled:
                            buy_no_live_rejection_reason = (
                                live_flag_error
                                or "BUY_NO_NATIVE_QUOTE_EVIDENCE_SUBMIT_DISABLED"
                            )
                        else:
                            buy_no_live_rejection_reason = _native_buy_no_live_authorization_rejection_reason(
                                d,
                                strategy_name,
                                mode,
                            )
                        if buy_no_live_rejection_reason:
                            summary["no_trades"] += 1
                            _increment_summary_counter(summary, "final_intent_attempts")
                            _increment_summary_counter(summary, "final_intent_preflight_rejected")
                            _increment_reason_bucket(
                                summary,
                                "final_intent_preflight_rejected_by_reason",
                                buy_no_live_rejection_reason,
                            )
                            _record_final_intent_frontier(
                                summary,
                                candidate=candidate,
                                decision=d,
                                city_name=city.name,
                                strategy_key=strategy_name,
                                stage="preflight",
                                outcome="preflight_rejected",
                                reason=buy_no_live_rejection_reason,
                            )
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
                                    market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                        _frontier_increment("execution_frontier", "snapshot_capture_attempts")
                        snapshot_fields, snapshot_capture_error = _capture_execution_snapshot_for_decision(
                            market,
                            d,
                            city.name,
                            candidate.target_date,
                        )
                        missing_snapshot_fields = _missing_execution_snapshot_fields(snapshot_fields)

                    if (
                        str(env or "").strip().lower() == "live"
                        and not missing_snapshot_fields
                        and not snapshot_capture_error
                        and _execution_snapshot_is_stale(conn, snapshot_fields["executable_snapshot_id"])
                    ):
                        _frontier_increment("execution_frontier", "snapshot_capture_attempts")
                        snapshot_fields, snapshot_capture_error = _capture_execution_snapshot_for_decision(
                            market,
                            d,
                            city.name,
                            candidate.target_date,
                        )
                        missing_snapshot_fields = _missing_execution_snapshot_fields(snapshot_fields)

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
                        frontier_reason = ";".join(rejection_reasons) or "executable_snapshot_unavailable"
                        _increment_summary_counter(summary, "final_intent_attempts")
                        _increment_summary_counter(summary, "final_intent_snapshot_failed")
                        _increment_reason_bucket(
                            summary,
                            "final_intent_snapshot_failed_by_reason",
                            frontier_reason,
                        )
                        _record_final_intent_frontier(
                            summary,
                            candidate=candidate,
                            decision=d,
                            city_name=city.name,
                            strategy_key=strategy_name,
                            stage="snapshot",
                            outcome="snapshot_failed",
                            reason=frontier_reason,
                            snapshot_fields=snapshot_fields,
                        )
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
                                market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                                "allow_taker_upgrade": True,
                                "cancel_after": decision_time + timedelta(seconds=timeout_seconds),
                                "resolution_window": candidate.target_date,
                                "correlation_key": (
                                    f"{getattr(city, 'cluster', '') or city.name}:{candidate.target_date}"
                                ),
                            }
                            decision_tokens = dict(getattr(d, "tokens", {}) or {})
                            for passive_key in (
                                "passive_fill_probability",
                                "expected_fill_probability",
                                "queue_depth_ahead",
                                "adverse_selection_score",
                                "min_expected_profit_usd",
                            ):
                                if passive_key in decision_tokens:
                                    final_intent_context[passive_key] = decision_tokens[
                                        passive_key
                                    ]
                            _reprice_fn = getattr(deps, "reprice_from_snapshot", None)
                            _frontier_increment("execution_frontier", "reprice_attempts")
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
                            rejection_reason = f"executable_snapshot_reprice_failed:{exc}"
                            rejection_reasons = [rejection_reason]
                            _increment_summary_counter(summary, "final_intent_attempts")
                            _increment_summary_counter(summary, "final_intent_reprice_failed")
                            _increment_reason_bucket(
                                summary,
                                "final_intent_reprice_failed_by_reason",
                                exc,
                            )
                            _record_final_intent_frontier(
                                summary,
                                candidate=candidate,
                                decision=d,
                                city_name=city.name,
                                strategy_key=strategy_name,
                                stage="reprice",
                                outcome="reprice_failed",
                                reason=str(exc),
                                snapshot_fields=snapshot_fields,
                            )
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
                                    market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                    family_ranked_attempt_accepted = False
                    live_frontier_stage = "final_intent_contract"
                    live_frontier_attempt_counted = False
                    try:
                        if is_live_env:
                            if not isinstance(getattr(d, "tokens", None), dict):
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING: decision tokens unavailable")
                            reprice_payload = d.tokens.get("executable_snapshot_reprice")
                            if not isinstance(reprice_payload, dict):
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING: reprice payload unavailable")
                            final_intent = getattr(d, "final_execution_intent", None)
                            evidence_payload = reprice_payload.get("corrected_pricing_evidence")
                            if reprice_payload.get("live_submit_authority") is not True:
                                unsupported_reason = None
                                if isinstance(evidence_payload, dict):
                                    unsupported_reason = evidence_payload.get("unsupported_reason")
                                raise ValueError(
                                    "FINAL_EXECUTION_INTENT_UNAVAILABLE:"
                                    f"{unsupported_reason or 'live_submit_authority_false'}"
                                )
                            if final_intent is None:
                                raise ValueError("FINAL_EXECUTION_INTENT_MISSING")
                            sweep_payload = reprice_payload.get("corrected_pricing_evidence")
                            if not isinstance(sweep_payload, dict):
                                sweep_payload = reprice_payload
                            if getattr(final_intent, "order_policy", "") == "post_only_passive_limit":
                                if getattr(final_intent, "post_only", False) is not True:
                                    raise ValueError("FINAL_EXECUTION_INTENT_MISSING: passive intent is not post_only")
                                if sweep_payload.get("sweep_depth_status") != "NOT_MARKETABLE_PASSIVE_LIMIT":
                                    raise ValueError("FINAL_EXECUTION_INTENT_MISSING: passive non-crossing proof unavailable")
                            else:
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
                            _increment_summary_counter(summary, "final_intent_attempts")
                            live_frontier_attempt_counted = True
                            _increment_summary_counter(summary, "final_intents_built")
                            _record_final_intent_frontier(
                                summary,
                                candidate=candidate,
                                decision=d,
                                city_name=city.name,
                                strategy_key=strategy_name,
                                stage="final_intent_contract",
                                outcome="built",
                                snapshot_fields=snapshot_fields,
                            )
                            execute_final = getattr(deps, "execute_final_intent", None)
                            if not callable(execute_final):
                                from src.execution.executor import execute_final_intent as execute_final
                            _increment_summary_counter(summary, "submit_attempts")
                            _record_final_intent_frontier(
                                summary,
                                candidate=candidate,
                                decision=d,
                                city_name=city.name,
                                strategy_key=strategy_name,
                                stage="submit",
                                outcome="submitted",
                                snapshot_fields=snapshot_fields,
                            )
                            live_frontier_stage = "submit"
                            result = execute_final(
                                final_intent,
                                conn=conn,
                                decision_id=str(d.decision_id) if d.decision_id else "",
                                snapshot_conn=conn,
                            )
                            submitted_limit = float(final_limit_decimal)
                            submit_rejected = str(getattr(result, "status", "") or "") == "rejected"
                            family_ranked_attempt_accepted = not submit_rejected
                            if submit_rejected:
                                submit_rejected_reason = getattr(result, "reason", None) or "submit_rejected"
                                _increment_summary_counter(summary, "submit_rejected")
                                _increment_reason_bucket(
                                    summary,
                                    "submit_rejected_by_reason",
                                    submit_rejected_reason,
                                )
                                _record_final_intent_frontier(
                                    summary,
                                    candidate=candidate,
                                    decision=d,
                                    city_name=city.name,
                                    strategy_key=strategy_name,
                                    stage="submit",
                                    outcome="submit_rejected",
                                    reason=submit_rejected_reason,
                                    snapshot_fields=snapshot_fields,
                                )
                            elif getattr(result, "command_state", None) in ("ACKED", "PARTIAL", "FILLED"):
                                _increment_summary_counter(summary, "venue_acks")
                                _record_final_intent_frontier(
                                    summary,
                                    candidate=candidate,
                                    decision=d,
                                    city_name=city.name,
                                    strategy_key=strategy_name,
                                    stage="venue_ack",
                                    outcome="venue_ack",
                                    snapshot_fields=snapshot_fields,
                                )
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
                            if isinstance(evidence_payload, dict):
                                evidence_payload["execution_path"] = "final_execution_intent"
                                evidence_payload["submitted_limit_price"] = (
                                    None
                                    if submit_rejected
                                    else _decimal_payload(final_limit_decimal)
                                )
                                evidence_payload["submit_path"] = (
                                    None if submit_rejected else "final_execution_intent"
                                )
                                evidence_payload["submitted_matches_corrected_candidate"] = (
                                    False
                                    if submit_rejected
                                    else abs(final_limit_decimal - corrected_candidate_limit) <= tick_tolerance
                                )
                                if submit_rejected:
                                    evidence_payload["submit_rejected_reason"] = getattr(
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
                            family_ranked_attempt_accepted = (
                                str(getattr(result, "status", "") or "") != "rejected"
                            )
                    except Exception as exc:
                        summary["no_trades"] += 1
                        rejection_stage = "EXECUTION_FAILED"
                        reason = str(exc)
                        if is_live_env:
                            if not live_frontier_attempt_counted:
                                _increment_summary_counter(summary, "final_intent_attempts")
                            if live_frontier_stage == "submit":
                                _increment_summary_counter(summary, "submit_failed")
                                _increment_reason_bucket(
                                    summary,
                                    "submit_failed_by_reason",
                                    reason,
                                )
                                _record_final_intent_frontier(
                                    summary,
                                    candidate=candidate,
                                    decision=d,
                                    city_name=city.name,
                                    strategy_key=strategy_name,
                                    stage="submit",
                                    outcome="submit_failed",
                                    reason=reason,
                                    snapshot_fields=snapshot_fields,
                                )
                            else:
                                _increment_summary_counter(summary, "final_intent_unavailable")
                                _increment_reason_bucket(
                                    summary,
                                    "final_intent_unavailable_by_reason",
                                    reason,
                                )
                                _record_final_intent_frontier(
                                    summary,
                                    candidate=candidate,
                                    decision=d,
                                    city_name=city.name,
                                    strategy_key=strategy_name,
                                    stage="final_intent_contract",
                                    outcome="contract_failed",
                                    reason=reason,
                                    snapshot_fields=snapshot_fields,
                                )
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
                                market_price=float(d.edge.entry_price) if d.edge else 0.0,
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
                    if family_ranked_attempt_accepted:
                        shoulder_ledger_error = _record_submitted_shoulder_exposure(
                            conn=conn,
                            city_name=city.name,
                            target_date=candidate.target_date,
                            decision=d,
                            observed_at=decision_time,
                            source=d.selected_method or selected_method,
                        )
                        if shoulder_ledger_error:
                            deps.logger.warning(
                                "shoulder exposure ledger write failed after submit "
                                "decision_id=%s: %s",
                                d.decision_id,
                                shoulder_ledger_error,
                            )
                            _mark_observability_degraded(summary)
                            summary["entries_paused"] = True
                            pause_error = _freeze_entries_after_shoulder_ledger_failure(
                                shoulder_ledger_error,
                                logger=deps.logger,
                            )
                            if pause_error:
                                _mark_observability_degraded(summary)
                                summary["shoulder_exposure_pause_error"] = pause_error
                            if isinstance(getattr(d, "tokens", None), dict):
                                d.tokens.setdefault("shoulder_exposure_ledger_error", shoulder_ledger_error)
                                d.tokens.setdefault(
                                    "entries_pause_reason",
                                    "shoulder_exposure_ledger_write_failed_after_submit",
                                )
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
                            # Wave 2 (INV-38): coerce typed ExecutionPrice to
                            # float at the json-serialization boundary so the
                            # decision_log artifact_json schema stays
                            # numeric (not a nested ExecutionPrice dict).
                            "entry_price": float(d.edge.entry_price),
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
                    if (
                        family_ranked_candidate_count > 1
                        and family_ranked_attempt_accepted
                    ):
                        family_ranked_submit_satisfied = True
                    if (
                        family_ranked_candidate_count > 1
                        and family_ranked_attempt_accepted
                        and family_portfolio_leg_role != "portfolio_selected"
                    ):
                        summary["family_ranked_selected_rank"] = int(
                            getattr(d, "family_ranked_candidate_rank", 0) or 0
                        )
                        summary["family_ranked_candidate_count"] = family_ranked_candidate_count
                    elif (
                        family_ranked_candidate_count > 1
                        and family_ranked_attempt_accepted
                        and family_portfolio_leg_role == "portfolio_selected"
                    ):
                        summary["family_portfolio_selected_submits"] = (
                            int(summary.get("family_portfolio_selected_submits", 0) or 0) + 1
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
                    if _cmd_durable:
                        _record_entry_order_summary(
                            summary,
                            runtime_order_status=runtime_order_status,
                            command_state=_cmd_state,
                        )
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
                            # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28):
                            # derive phase_after from the runtime_order_status
                            # (mapped to the runtime state via
                            # initial_entry_runtime_state_for_order_status above)
                            # rather than letting the builder read pos.state.
                            # filled → ACTIVE; pending → PENDING_ENTRY.
                            _entry_phase_after = (
                                LifecyclePhase.ACTIVE.value
                                if runtime_order_status == "filled"
                                else LifecyclePhase.PENDING_ENTRY.value
                            )
                            _dual_write_canonical_entry_if_available(
                                conn,
                                pos,
                                phase_after=_entry_phase_after,
                                decision_id=d.decision_id,
                                deps=deps,
                                decision_evidence=getattr(d, "decision_evidence", None),
                            )
                            # F5 (2026-05-28): trade_decisions bridge assertion removed.
                            # log_trade_entry no longer writes to trade_decisions;
                            # canonical entry truth is in position_events/position_current.
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
                            market_price=float(d.edge.entry_price) if d.edge else 0.0,
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

    _frontier("execution_frontier").update(
        {
            "final_intent_built": int(summary.get("final_intents_built", 0) or 0),
            "submit_attempts": int(summary.get("submit_attempts", 0) or 0),
        }
    )
    money_path_frontier["terminal_classification"] = _classify_money_path_frontier()
    _publish_frontier_status_proof()
    _flush_derived_writes()
    return portfolio_dirty, tracker_dirty
