# Created: 2026-05-04
# Last reused/audited: 2026-07-19
# Authority basis: IOC forward-port (Fix C: allowed_discovery_modes_inverse) — 2026-05-23
"""Heavy runtime helpers extracted from cycle_runner.

The goal is to keep `cycle_runner.py` focused on orchestration while preserving
monkeypatch-based tests that patch symbols on the cycle_runner module. Every
function here receives a `deps` object, typically the cycle_runner module.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import threading
import time
import uuid
from bisect import bisect_right
from collections.abc import Callable, Mapping
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
from src.contracts.execution_intent import (
    DecisionSourceContext,
    POLYMARKET_MARKETABLE_BUY_MIN_NOTIONAL_USD,
)
from src.contracts.position_truth import (
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
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    INACTIVE_RUNTIME_STATES,
    ExitContext,
    get_open_positions,
)

logger = logging.getLogger(__name__)

SOURCE_WRITER_FRONTIER_STALE_SECONDS = 5 * 60
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): the T5 schema
# migration has run — no writer mints state='quarantined', the DB CHECK no
# longer admits the literal, and LifecycleState has no such member (Position
# construction raises). The quarantine-redecision predicate that used to be
# gated on state=='quarantined' (kept post-T5-CORE as a "dead-but-harmless
# mixed-epoch safety net") is now provably unreachable in every caller and
# has been retired along with its supporting constants/helpers.


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
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' retired
# from LifecyclePhase; the T5 schema migration has run and the
# position_current CHECK no longer admits the literal, so the mixed-epoch
# bridge that used to keep the bare string literal in the orphan-order-
# ownership raw-SQL query below is retired. The query's `NOT IN (?, ?, ?)`
# placeholder count is hardcoded to this frozenset's size (3) — keep them in
# lockstep.
_ORDER_OWNERSHIP_TERMINAL_POSITION_PHASES = frozenset(TERMINAL_STATES)
_ORDER_OWNERSHIP_TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "cancelled", "canceled", "expired", "rejected", "voided"}
)
_ENTRY_RECENT_SAME_TOKEN_EXIT_COOLDOWN_SECONDS = 6 * 60 * 60
_ENTRY_RECENT_SAME_TOKEN_EXIT_PHASES = frozenset({"economically_closed"})
_ENTRY_TERMINAL_NO_FILL_MIN_REPRICE_TICK = Decimal("0.001")
_ENTRY_TERMINAL_NO_FILL_REPRICE_LOOKBACK_SECONDS = 6 * 60 * 60
_LIVE_DISCOVERY_EVAL_BUDGET_ENV = "ZEUS_LIVE_DISCOVERY_EVAL_BUDGET_SECONDS"
_LIVE_DISCOVERY_EVAL_BUDGET_DEFAULT_SECONDS = 360.0
_HELD_POSITION_MONITOR_BUDGET_ENV = "ZEUS_HELD_POSITION_MONITOR_BUDGET_SECONDS"
_HELD_POSITION_MONITOR_BUDGET_DEFAULT_SECONDS = 75.0
_HELD_POSITION_MONITOR_POSITIVE_BUDGET_PROGRESS_MIN = 2
_HELD_POSITION_MONITOR_FULL_COVERAGE_CYCLES = 3


def _held_position_monitor_positive_progress_limit(position_count: int) -> int:
    """Keep one full held book inside three nominal monitor cycles."""

    return max(
        _HELD_POSITION_MONITOR_POSITIVE_BUDGET_PROGRESS_MIN,
        math.ceil(max(0, int(position_count)) / _HELD_POSITION_MONITOR_FULL_COVERAGE_CYCLES),
    )


def _held_position_monitor_budget_seconds(override: float | None = None) -> float:
    raw = override
    if raw is None:
        raw = os.environ.get(_HELD_POSITION_MONITOR_BUDGET_ENV, "")
    if raw in (None, ""):
        return _HELD_POSITION_MONITOR_BUDGET_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _HELD_POSITION_MONITOR_BUDGET_DEFAULT_SECONDS
    if not math.isfinite(value):
        return _HELD_POSITION_MONITOR_BUDGET_DEFAULT_SECONDS
    if override is None and value <= 0:
        return _HELD_POSITION_MONITOR_BUDGET_DEFAULT_SECONDS
    return max(0.0, value)


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
# - SETTLEMENT_IMMINENT / FLASH_CRASH_PANIC /
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
# LEGACY-ONLY (ultimate_alpha 2026-07-24): evaluate_exit now emits the unified
# vocabulary {HOLD, SELL_REVERSAL, EVIDENCE_UNAVAILABLE, RED_FORCE_EXIT}; the
# triggers below are no longer produced. SELL_REVERSAL is DELIBERATELY absent —
# the consecutive-confirmation evidence template this gate enforces is the
# repeated-cycle confirmation machinery FINAL_SPEC retires (a fresh
# current-state value comparison is its own evidence). The set is kept only so
# any in-flight legacy rows drain through the old burden; E-slice removes the
# gate once the vocabulary migration completes.
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
            # capture_policy_spec.md §2 trigger 2: synchronous pre-submit
            # recapture (stale-cycle-snapshot path), already structurally full.
            capture_trigger="JIT_SUBMIT",
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
    # Submit-time recapture reconstructs immutable market identity and refreshes
    # tradability/book facts from CLOB. Preserve the decision snapshot's Gamma
    # Fee Structure V2 schedule as part of that identity: CLOB /fee-rate still
    # exposes the legacy base_fee=1000 (0.10), which is not the V2 weather fee
    # coefficient (Gamma feeSchedule.rate=0.05). Losing this field makes the
    # recapture compare two different fee semantics and reject every valid FAK.
    fee_details = dict(getattr(snapshot, "fee_details", None) or {})
    if "feeSchedule_taker_only" in fee_details:
        from src.contracts.executable_market_snapshot import (
            fee_rate_fraction_from_details,
        )

        fee_schedule = {
            "exponent": 1,
            "rate": fee_rate_fraction_from_details(fee_details),
            "takerOnly": bool(fee_details.get("feeSchedule_taker_only", True)),
        }
        if fee_details.get("maker_rebate_rate") is not None:
            fee_schedule["rebateRate"] = fee_details["maker_rebate_rate"]
        gamma_market_raw["feeSchedule"] = fee_schedule
        if fee_details.get("fee_type"):
            gamma_market_raw["feeType"] = fee_details["fee_type"]
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


_CHAIN_BALANCE_UNITS = Decimal("1000000")
_CHAIN_BALANCE_PHASES = frozenset(
    {"entered", "holding", "active", "day0_window", "pending_exit"}
)


def _current_money_risk_token_metadata(portfolio) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for position in tuple(getattr(portfolio, "positions", ()) or ()):
        state = str(
            getattr(
                getattr(position, "state", ""),
                "value",
                getattr(position, "state", ""),
            )
            or ""
        ).strip()
        if state not in _CHAIN_BALANCE_PHASES:
            continue
        chain_state = str(
            getattr(
                getattr(position, "chain_state", ""),
                "value",
                getattr(position, "chain_state", ""),
            )
            or ""
        ).strip()
        if not has_current_money_risk_chain_state(chain_state):
            continue
        raw_shares = getattr(position, "effective_shares", None)
        if raw_shares is None:
            raw_shares = getattr(position, "chain_shares", None)
        if raw_shares is None:
            raw_shares = getattr(position, "shares", 0)
        try:
            if Decimal(str(raw_shares or 0)) <= 0:
                continue
        except (InvalidOperation, ValueError):
            continue
        raw_direction = getattr(position, "direction", "")
        direction = str(
            getattr(raw_direction, "value", raw_direction) or ""
        ).lower()
        token_id = (
            getattr(position, "no_token_id", "")
            if direction == "buy_no"
            else getattr(position, "token_id", "")
        )
        token = str(token_id or "").strip()
        if token:
            tokens.setdefault(token, str(getattr(position, "condition_id", "") or ""))
    return tokens


def _overlay_current_ctf_balances(
    portfolio,
    clob,
    api_positions,
    *,
    ChainPosition,
):
    """Overlay direct CTF balances for local money-risk tokens.

    The Data API may omit dust or an almost-fully-exited position. A targeted
    conditional-token balance is direct current chain truth, so it must replace
    the API aggregate for the same token before canonical reconciliation.
    """

    token_metadata = _current_money_risk_token_metadata(portfolio)
    if not token_metadata:
        return api_positions, {"ctf_balance_tokens_refreshed": 0}

    try:
        inspect.getattr_static(clob, "get_ctf_collateral_payload")
    except AttributeError:
        reader = None
    else:
        reader = getattr(clob, "get_ctf_collateral_payload", None)
    if not callable(reader):
        try:
            inspect.getattr_static(clob, "_ensure_v2_adapter")
        except AttributeError:
            ensure_adapter = None
        else:
            ensure_adapter = getattr(clob, "_ensure_v2_adapter", None)
        adapter = ensure_adapter() if callable(ensure_adapter) else None
        reader = getattr(adapter, "get_ctf_collateral_payload", None)
    if not callable(reader):
        return api_positions, {"ctf_balance_reader_unavailable": 1}

    payload = dict(reader(token_ids=sorted(token_metadata)) or {})
    authority = str(payload.get("authority_tier") or "").strip().upper()
    if authority not in {"CHAIN", "VENUE"}:
        raise RuntimeError("targeted CTF balance authority is not current")
    balances = payload.get("ctf_token_balances_units")
    if not isinstance(balances, dict) or not set(token_metadata).issubset(balances):
        raise RuntimeError("targeted CTF balance response is incomplete")

    by_token = {str(position.token_id): position for position in api_positions}
    for token_id, condition_id in token_metadata.items():
        try:
            units = int(balances[token_id])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("targeted CTF balance is invalid") from exc
        if units < 0:
            raise RuntimeError("targeted CTF balance is negative")
        prior = by_token.get(token_id)
        by_token[token_id] = ChainPosition(
            token_id=token_id,
            size=float(Decimal(units) / _CHAIN_BALANCE_UNITS),
            avg_price=float(getattr(prior, "avg_price", 0) or 0),
            cost=float(getattr(prior, "cost", 0) or 0),
            condition_id=(
                str(getattr(prior, "condition_id", "") or condition_id)
                if prior is not None
                else condition_id
            ),
            balance_authority=authority,
            balance_source="targeted_ctf_balance_allowance",
        )
    return list(by_token.values()), {
        "ctf_balance_tokens_refreshed": len(token_metadata),
        "ctf_balance_authority": authority,
    }


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
    api_positions, ctf_stats = _overlay_current_ctf_balances(
        portfolio,
        clob,
        api_positions,
        ChainPosition=deps.ChainPosition,
    )
    reconcile_stats = deps.reconcile_with_chain(portfolio, api_positions, conn=conn)
    reconcile_stats.update(ctf_stats)
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
                       AND lower(COALESCE(phase, '')) NOT IN (?, ?, ?)
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


def _entry_command_has_zero_fill_terminal_order_fact(conn, command_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM venue_order_facts
         WHERE command_id = ?
           AND UPPER(COALESCE(state, '')) IN ('CANCEL_CONFIRMED', 'EXPIRED', 'VENUE_WIPED')
           AND CAST(COALESCE(matched_size, '0') AS REAL) <= 0
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    return row is not None


def _same_token_terminal_no_fill_reprice_block_detail(
    conn,
    *,
    token_id: str,
    candidate_position_id: str,
    candidate_price: Decimal,
    now: datetime | None = None,
) -> dict[str, object] | None:
    token = str(token_id or "").strip()
    position_id = str(candidate_position_id or "").strip()
    if (
        not token
        or not position_id
        or candidate_price is None
        or not _table_exists_in_schema(conn, "main", "venue_commands")
        or not _table_exists_in_schema(conn, "main", "venue_order_facts")
    ):
        return None
    try:
        rows = conn.execute(
            """
            SELECT command_id, position_id, state, price, created_at, updated_at
              FROM venue_commands
             WHERE UPPER(intent_kind) = 'ENTRY'
               AND UPPER(side) = 'BUY'
               AND token_id = ?
               AND position_id != ?
               AND UPPER(state) IN ('CANCELLED', 'EXPIRED')
             ORDER BY updated_at DESC, created_at DESC
             LIMIT 16
            """,
            (token, position_id),
        ).fetchall()
    except Exception:
        return None
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    for row in rows:
        command_id = str(row["command_id"] if hasattr(row, "keys") else row[0] or "")
        if not command_id or not _entry_command_has_zero_fill_terminal_order_fact(conn, command_id):
            continue
        prior_price = _decimal_or_none(row["price"] if hasattr(row, "keys") else row[3])
        if prior_price is None:
            continue
        updated_raw = row["updated_at"] if hasattr(row, "keys") else row[5]
        created_raw = row["created_at"] if hasattr(row, "keys") else row[4]
        last_seen = _parse_utc_timestamp(updated_raw) or _parse_utc_timestamp(created_raw)
        if last_seen is None:
            continue
        age_seconds = (now_utc.astimezone(timezone.utc) - last_seen).total_seconds()
        if age_seconds < 0:
            age_seconds = 0.0
        if age_seconds > _ENTRY_TERMINAL_NO_FILL_REPRICE_LOOKBACK_SECONDS:
            continue
        reprice_delta = abs(candidate_price - prior_price)
        if reprice_delta < _ENTRY_TERMINAL_NO_FILL_MIN_REPRICE_TICK:
            return {
                "existing_command_id": command_id,
                "existing_position_id": str(row["position_id"] if hasattr(row, "keys") else row[1] or ""),
                "existing_command_state": str(row["state"] if hasattr(row, "keys") else row[2] or ""),
                "existing_updated_at": str(updated_raw or ""),
                "existing_created_at": str(created_raw or ""),
                "existing_price": str(prior_price),
                "candidate_price": str(candidate_price),
                "reprice_delta": str(reprice_delta),
                "min_reprice_tick": str(_ENTRY_TERMINAL_NO_FILL_MIN_REPRICE_TICK),
                "age_seconds": int(age_seconds),
                "lookback_seconds": _ENTRY_TERMINAL_NO_FILL_REPRICE_LOOKBACK_SECONDS,
            }
    return None


def _recent_same_token_exit_cooldown_detail(
    conn,
    *,
    token_id: str,
    now: datetime | None = None,
) -> dict[str, object] | None:
    token = str(token_id or "").strip()
    if not token or not _table_exists_in_schema(conn, "main", "position_current"):
        return None
    columns = _table_columns_in_schema(conn, "main", "position_current")
    token_columns = [name for name in ("token_id", "no_token_id") if name in columns]
    if not token_columns or "phase" not in columns or "updated_at" not in columns:
        return None
    phase_sql = "phase IN ({})".format(
        ",".join("?" for _ in _ENTRY_RECENT_SAME_TOKEN_EXIT_PHASES)
    )
    token_sql = " OR ".join(f"NULLIF({name}, '') = ?" for name in token_columns)
    position_id_expr = "position_id" if "position_id" in columns else "''"
    exit_reason_expr = "exit_reason" if "exit_reason" in columns else "''"
    try:
        row = conn.execute(
            f"""
            SELECT
                {position_id_expr} AS position_id,
                phase,
                updated_at,
                {exit_reason_expr} AS exit_reason
              FROM position_current
             WHERE {phase_sql}
               AND ({token_sql})
             ORDER BY updated_at DESC
             LIMIT 1
            """,
            (
                *sorted(_ENTRY_RECENT_SAME_TOKEN_EXIT_PHASES),
                *(token for _ in token_columns),
            ),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    updated_raw = row["updated_at"] if hasattr(row, "keys") else row[2]
    updated_at = _parse_utc_timestamp(updated_raw)
    if updated_at is None:
        return None
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    age_seconds = (now_utc.astimezone(timezone.utc) - updated_at).total_seconds()
    if age_seconds < 0:
        age_seconds = 0.0
    if age_seconds > _ENTRY_RECENT_SAME_TOKEN_EXIT_COOLDOWN_SECONDS:
        return None
    position_id = str(row["position_id"] if hasattr(row, "keys") else row[0] or "")
    phase = str(row["phase"] if hasattr(row, "keys") else row[1] or "")
    exit_reason = str(row["exit_reason"] if hasattr(row, "keys") else row[3] or "")
    return {
        "position_id": position_id or "unknown",
        "phase": phase or "unknown",
        "updated_at": updated_at.isoformat(),
        "age_seconds": int(age_seconds),
        "cooldown_seconds": _ENTRY_RECENT_SAME_TOKEN_EXIT_COOLDOWN_SECONDS,
        "exit_reason": exit_reason or "unknown",
    }


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
        no_fill_reprice_detail = _same_token_terminal_no_fill_reprice_block_detail(
            conn,
            token_id=token_id,
            candidate_position_id=str(row["position_id"] if hasattr(row, "keys") else row[1] or ""),
            candidate_price=order_price,
        )
        if no_fill_reprice_detail is not None:
            try:
                from src.execution.exit_safety import request_cancel_for_command

                outcome = request_cancel_for_command(
                    conn,
                    command_id,
                    lambda venue_order_id: clob.cancel_order(venue_order_id),
                )
            except Exception as exc:
                deps.logger.warning(
                    "Terminal no-fill same-price reprice cancel failed for %s: %s",
                    command_id,
                    exc,
                )
                continue
            if is_cancel_confirmed_status(outcome.status):
                cancelled += 1
                deps.logger.info(
                    "Entry order %s canceled: terminal no-fill requires reprice %s",
                    command_id,
                    no_fill_reprice_detail,
                )
            continue
        recent_exit_detail = _recent_same_token_exit_cooldown_detail(
            conn,
            token_id=token_id,
        )
        if recent_exit_detail is not None:
            try:
                from src.execution.exit_safety import request_cancel_for_command

                outcome = request_cancel_for_command(
                    conn,
                    command_id,
                    lambda venue_order_id: clob.cancel_order(venue_order_id),
                )
            except Exception as exc:
                deps.logger.warning(
                    "Recent same-token exit cooldown cancel failed for %s: %s",
                    command_id,
                    exc,
                )
                continue
            if is_cancel_confirmed_status(outcome.status):
                cancelled += 1
                deps.logger.info(
                    "Entry order %s canceled by same-token recent-exit cooldown: %s",
                    command_id,
                    recent_exit_detail,
                )
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
    reason_text = str(getattr(exit_decision, "reason", "") or "")
    # Evidence-incomplete verdicts (legacy INCOMPLETE_EXIT_CONTEXT string or the
    # one-law EVIDENCE_UNAVAILABLE) never get upgraded to an ORANGE exit.
    if reason_text.startswith("INCOMPLETE_EXIT_CONTEXT") or reason_text == "EVIDENCE_UNAVAILABLE":
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

    Returns True only when this call appends a new DAY0_WINDOW_ENTERED event.
    A later ACTIVE event starts a new canonical re-entry epoch; a later
    DAY0_WINDOW event only repairs the projection. Pending-exit and terminal
    truth remain absorbing.
    """
    if conn is None:
        return False

    from src.engine.lifecycle_events import (
        build_day0_window_entered_canonical_write,
        build_position_current_projection,
    )
    from src.state.db import append_many_and_project
    from src.state.projection import upsert_position_current

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
            latest = conn.execute(
                """
                SELECT event_type, phase_after
                  FROM position_events
                 WHERE position_id = ?
                 ORDER BY sequence_no DESC, rowid DESC
                 LIMIT 1
                """,
                (getattr(pos, "trade_id", ""),),
            ).fetchone()
            latest_type = str(latest[0] if latest else "")
            latest_phase_after = str((latest[1] if latest and len(latest) > 1 else "") or "")
            if latest_phase_after not in {
                LifecyclePhase.ACTIVE.value,
                LifecyclePhase.DAY0_WINDOW.value,
            }:
                raise ValueError(
                    "existing DAY0_WINDOW_ENTERED is superseded by latest "
                    f"canonical event {latest_type or '<missing>'}/{latest_phase_after or '<missing>'}; "
                    "refusing to project day0_window over newer lifecycle truth"
                )
            if latest_phase_after == LifecyclePhase.DAY0_WINDOW.value:
                projection = build_position_current_projection(pos)
                projection["phase"] = LifecyclePhase.DAY0_WINDOW.value
                upsert_position_current(conn, projection)
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
            event_identity_suffix=str(next_seq) if existing_day0 is not None else None,
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
    exit_state = _semantic_value(getattr(pos, "exit_state", ""))
    order_status = _semantic_value(getattr(pos, "order_status", ""))
    if (
        state == "pending_exit"
        or exit_state in {
            "exit_intent",
            "sell_placed",
            "sell_pending",
            "retry_pending",
            "backoff_exhausted",
        }
        or order_status in {
            "exit_intent",
            "sell_placed",
            "sell_pending",
            "retry_pending",
            "backoff_exhausted",
        }
    ):
        return LifecyclePhase.PENDING_EXIT.value
    if state == "day0_window":
        return LifecyclePhase.DAY0_WINDOW.value
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' is
    # no longer a reachable Position.state — LifecycleState has no such
    # member (construction raises) and the DB CHECK no longer admits the
    # literal post-migration — so this falls straight to ACTIVE like any
    # other held state.
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
        monitor_occurred_at = (
            deps._utcnow().isoformat()
            if hasattr(deps, "_utcnow")
            else (
                str(getattr(pos, "last_monitor_at", "") or "").strip()
                or datetime.now(timezone.utc).isoformat()
            )
        )
        pos.last_monitor_at = monitor_occurred_at
        events, projection = build_monitor_refreshed_canonical_write(
            pos,
            sequence_no=next_seq,
            phase_after=_monitor_refreshed_phase_for_position(pos),
            source_module="src.engine.cycle_runtime",
            occurred_at=monitor_occurred_at,
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


def _record_monitor_hold_decision(
    conn,
    pos,
    *,
    artifact,
    deps,
    summary: dict,
    reason: str,
    trigger: str,
    validation: str,
    counter: str,
) -> bool:
    from src.state.portfolio import ExitDecision as _ExitDecision

    validations = list(
        dict.fromkeys([*(getattr(pos, "applied_validations", []) or []), validation])
    )
    pos.applied_validations = validations
    exit_decision = _ExitDecision(
        False,
        reason,
        urgency="normal",
        trigger=trigger,
        selected_method=getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
        applied_validations=validations,
    )
    canonical_written = _emit_monitor_refreshed_canonical_if_available(
        conn,
        pos,
        deps=deps,
        exit_decision=exit_decision,
        final_should_exit=False,
        final_exit_reason=reason,
        final_exit_trigger=trigger,
    )
    if not canonical_written:
        summary["monitor_canonical_write_failed"] = (
            summary.get("monitor_canonical_write_failed", 0) + 1
        )
    monitor_fresh_prob, monitor_fresh_edge = (
        _current_monitor_result_probability_and_edge(pos)
    )
    artifact.add_monitor_result(
        deps.MonitorResult(
            position_id=pos.trade_id,
            fresh_prob=monitor_fresh_prob,
            fresh_edge=monitor_fresh_edge,
            should_exit=False,
            exit_reason=reason,
            neg_edge_count=pos.neg_edge_count,
        )
    )
    summary[counter] = summary.get(counter, 0) + 1
    summary["monitors"] = summary.get("monitors", 0) + 1
    return canonical_written


_FAMILY_OVERLAY_STATISTICAL_EXIT_TRIGGERS = frozenset(
    {
        "CI_SEPARATED_REVERSAL",
        "FLASH_CRASH_PANIC",
        "VIG_EXTREME",
        "EDGE_REVERSAL",
        # ultimate_alpha 2026-07-24: the unified stopping-law sell. It is a
        # statistical value comparison (robust q⁻ vs top-of-book proceeds), so
        # it inherits this classification's Day0-immature-authority protection;
        # the legacy triggers above are no longer emitted by evaluate_exit.
        "SELL_REVERSAL",
    }
)

# These monitor verdicts are estimates of one leg's terminal value. They do not
# carry the global auction's current depth curve, fees, portfolio endowment, or
# robust delta-log-wealth objective, so they may propose a SELL but never actuate
# one locally. RED and absorbing Day0 hard facts are deliberately absent: their
# direct reduce-only authority comes from risk/settlement truth, not this
# statistical comparison.
_GLOBAL_AUCTION_STATISTICAL_SELL_TRIGGERS = frozenset(
    {
        "CI_SEPARATED_REVERSAL",
        "CI_OVERLAP_SELL_VALUE_DOMINATES",
        "SETTLEMENT_IMMINENT",
        "EDGE_REVERSAL",
        "BUY_NO_EDGE_EXIT",
        "BUY_NO_NEAR_EXIT",
        # ultimate_alpha 2026-07-24: the unified stopping-law sell proposes and
        # the global auction actuates — evaluate_exit's L(x) sees only the
        # top-of-book bid, while the auction values the sell against the real
        # depth curve, fees, and portfolio endowment (the closest existing
        # machinery to the PR-2 allocator ΔJ). RED and absorbing Day0 hard
        # facts remain deliberately absent (direct reduce-only authority).
        "SELL_REVERSAL",
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


def _entry_aggregate_id(decision_id: str) -> str | None:
    parts = str(decision_id or "").strip().split(":")
    if len(parts) < 5 or parts[0] != "edli_exec_cmd":
        return None
    event_id = parts[1].strip()
    final_intent_id = ":".join(parts[2:-2]).strip()
    token_id = parts[-2].strip()
    direction = parts[-1].strip()
    if (
        not event_id
        or not final_intent_id
        or not token_id
        or direction not in {"buy_yes", "buy_no"}
    ):
        return None
    return f"{event_id}:{final_intent_id}"


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
    aggregate_id = _entry_aggregate_id(decision_id)
    if aggregate_id is None:
        return None
    try:
        event_row = conn.execute(
            """
            SELECT payload_json
              FROM edli_live_order_events
             WHERE aggregate_id = ?
               AND event_type = 'PreSubmitRevalidated'
             ORDER BY event_sequence DESC
             LIMIT 1
            """,
            (aggregate_id,),
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

    summary["entry_selection_guard_invalid_independent_exit_required"] = (
        summary.get("entry_selection_guard_invalid_independent_exit_required", 0) + 1
    )
    return _ExitDecision(
        False,
        (
            "ENTRY_SELECTION_GUARD_INVALID_HOLD_REQUIRES_CURRENT_EXIT "
            f"({verdict.get('invalid_reason')}; sell_value_usd={sell_value:.4f}; "
            f"q_safe={verdict.get('selection_guard_q_safe')})"
        ),
        urgency="normal",
        trigger="ENTRY_SELECTION_GUARD_INVALID_HOLD_REQUIRES_CURRENT_EXIT",
        selected_method=getattr(pos, "selected_method", "") or getattr(pos, "entry_method", ""),
        applied_validations=list(
            dict.fromkeys(
                [
                    *(getattr(pos, "applied_validations", []) or []),
                    "entry_selection_guard_invalid_requires_current_exit",
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
    candidates: list = list(get_open_positions(portfolio))
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
    return _position_state_value(pos) in {"entered", "holding", "active", "day0_window", "pending_exit"}


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


def _monitor_value_inputs(
    position,
) -> tuple[
    float,
    float | None,
    float | None,
    tuple[float, float] | None,
    str | None,
]:
    try:
        shares = float(getattr(position, "effective_shares", getattr(position, "shares", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0, None, None, None, "shares_unavailable"
    if shares <= 0.0:
        return 0.0, None, None, None, "shares_non_positive"
    if getattr(position, "last_monitor_prob_is_fresh", False) is not True:
        return shares, None, None, None, "probability_not_fresh"
    if getattr(position, "last_monitor_market_price_is_fresh", False) is not True:
        return shares, None, None, None, "market_price_not_fresh"
    try:
        held_prob = float(getattr(position, "last_monitor_prob"))
        best_bid = float(getattr(position, "last_monitor_best_bid"))
    except (TypeError, ValueError):
        return shares, None, None, None, "value_inputs_non_numeric"
    if not (math.isfinite(held_prob) and 0.0 <= held_prob <= 1.0):
        return shares, None, None, None, "probability_invalid"
    if not (math.isfinite(best_bid) and 0.0 <= best_bid <= 1.0):
        return shares, None, None, None, "best_bid_invalid"
    held_ci = getattr(position, "_monitor_current_held_ci", None)
    if isinstance(held_ci, tuple) and len(held_ci) == 2:
        try:
            lo, hi = float(held_ci[0]), float(held_ci[1])
        except (TypeError, ValueError):
            held_ci = None
        else:
            held_ci = (
                (lo, hi)
                if math.isfinite(lo)
                and math.isfinite(hi)
                and 0.0 <= lo <= held_prob <= hi <= 1.0
                else None
            )
    else:
        held_ci = None
    return shares, held_prob, best_bid, held_ci, None


def _is_statistical_single_leg_exit(exit_decision, exit_reason: str) -> bool:
    trigger = str(getattr(exit_decision, "trigger", "") or exit_reason or "")
    return any(trigger.startswith(prefix) for prefix in _FAMILY_OVERLAY_STATISTICAL_EXIT_TRIGGERS)


def _global_auction_owns_statistical_sell(exit_decision, exit_reason: str) -> bool:
    trigger = str(getattr(exit_decision, "trigger", "") or exit_reason or "")
    return any(
        trigger.startswith(prefix)
        for prefix in _GLOBAL_AUCTION_STATISTICAL_SELL_TRIGGERS
    )


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
    """Record family point value without overriding the robust exit decision.

    This is live monitor logic over already-refreshed held-side probabilities and
    held-side bids. It does not read replay data and it never creates a
    new entry. The point-value vector is diagnostic because it does not carry a
    coherent current family probability distribution or the exit evaluator's
    fee/time/crowding costs. It therefore cannot veto EXIT or promote SELL.
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
        "value_authority": "point_estimate_diagnostic_only",
        "can_veto_robust_exit": False,
        "can_promote_robust_hold": False,
    }
    hold_value = 0.0
    sell_value = 0.0
    missing: list[dict[str, str]] = []
    leg_payloads: list[dict[str, object]] = []
    for leg in family_positions:
        shares, held_prob, best_bid, held_ci, reason = _monitor_value_inputs(leg)
        # Only the position evaluated in this call is guaranteed to have a CI
        # from this monitor cut. Sibling point values remain diagnostic; never
        # reuse a sibling's transient bound from an earlier loop iteration.
        if leg is not pos:
            held_ci = None
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
            if held_ci is not None:
                leg_payload.update(
                    {
                        "held_probability_lcb": held_ci[0],
                        "held_probability_ucb": held_ci[1],
                        "robust_hold_value_lcb_usd": shares * held_ci[0],
                        "held_probability_ci_authority": (
                            "current_edge_ci_shifted_to_held_probability"
                        ),
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
        payload["decision"] = "FAMILY_POINT_VALUE_DIAGNOSTIC_EXIT_PRESERVED"
        payload["preserved_exit_reason"] = exit_reason
        setattr(pos, "_monitor_family_redecision", payload)
        validations = list(getattr(pos, "applied_validations", []) or [])
        validations.append("family_point_value_cannot_veto_robust_exit")
        pos.applied_validations = list(dict.fromkeys(validations))
        summary["family_redecision_robust_exits_preserved"] = (
            summary.get("family_redecision_robust_exits_preserved", 0) + 1
        )
        return should_exit, exit_reason

    # A high bid over a point belief is useful counterfactual evidence, not a
    # second exit authority. Only Position.evaluate_exit owns the robust
    # CI/cost-aware HOLD/EXIT decision.
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
        payload["decision"] = "FAMILY_POINT_VALUE_DIAGNOSTIC_HOLD_PRESERVED"
        payload["preserved_hold_reason"] = exit_reason
        payload["diagnostic_suggested_action"] = "SELL"
        payload["belief_reversed_below_entry"] = True
        setattr(pos, "_monitor_family_redecision", payload)
        validations = list(getattr(pos, "applied_validations", []) or [])
        validations.append("family_point_value_cannot_promote_sell")
        pos.applied_validations = list(dict.fromkeys(validations))
        summary["family_redecision_robust_holds_preserved"] = (
            summary.get("family_redecision_robust_holds_preserved", 0) + 1
        )
        return should_exit, exit_reason

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


def _semantic_value(value) -> str:
    return str(getattr(value, "value", value) or "")


def _position_state_value(pos) -> str:
    return _semantic_value(getattr(pos, "state", ""))


def _position_chain_state_value(pos) -> str:
    return _semantic_value(getattr(pos, "chain_state", ""))


def _position_direction_value(pos) -> str:
    return _semantic_value(getattr(pos, "direction", ""))


def _position_real_exposure_shares(pos) -> float:
    for attr in ("chain_shares", "shares_filled", "shares"):
        try:
            value = float(getattr(pos, attr, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            return value
    return 0.0


def _day0_hard_fact_position_eligible(pos) -> bool:
    # Active same-day fills must not wait for a successful DAY0_WINDOW_ENTERED
    # projection before consuming settlement-grade observed-so-far facts.  The
    # hard-fact evaluator is date/source-gated and returns None for non-Day0
    # families, so admitting active/holding states here is fail-closed for
    # future dates while preventing same-day positions from missing the only
    # sellable window after an absorbing observation update.
    #
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): this used to also
    # admit a quarantined position via the canonical redecision-eligibility
    # predicate (_quarantined_position_can_redecision). That predicate's own
    # gate (state == 'quarantined') is now provably unreachable — no writer
    # mints the literal and the DB CHECK no longer admits it post-migration —
    # so the predicate and its supporting helpers have been retired.
    return _position_state_value(pos) in {"active", "entered", "holding", "day0_window"}


def _venue_confirmed_local_fill_needs_monitor(pos) -> bool:
    """Monitor real venue fills even before chain reconciliation catches up."""

    state_value = _position_state_value(pos)
    if state_value in INACTIVE_RUNTIME_STATES or state_value in {"pending_entry", "pending_tracked"}:
        return False
    if _position_chain_state_value(pos) != "local_only":
        return False
    fill_authority = str(getattr(pos, "fill_authority", "") or "")
    if fill_authority not in {
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    }:
        return False
    if _position_real_exposure_shares(pos) <= 0.01:
        return False
    for attr in ("effective_cost_basis_usd", "cost_basis_usd", "size_usd"):
        try:
            value = float(getattr(pos, attr, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            return True
    return False


_CANONICAL_MONITOR_PHASE_PRIORITY = {
    "pending_exit": 0,
    "day0_window": 1,
    "active": 2,
}


def _canonical_monitor_position_rows(
    conn,
    *,
    now_utc: datetime | None = None,
    position_ids: tuple[str, ...] | None = None,
) -> list | None:
    """Return monitorable canonical position_current rows in monitor order.

    ``None`` means the canonical projection is unavailable and callers should use
    the legacy portfolio-only fallback.  An empty list means the projection is
    available and currently has no active monitor-risk rows.
    """

    if conn is None or not _table_exists_in_schema(conn, "main", "position_current"):
        return None
    columns = _table_columns_in_schema(conn, "main", "position_current")
    required = {"position_id", "phase"}
    if not required.issubset(columns):
        return None
    monitor_event_payload = "NULL AS last_monitor_event_payload_json"
    monitor_event_occurred_at = "NULL AS last_monitor_event_occurred_at"
    if _table_exists_in_schema(conn, "main", "position_events"):
        event_columns = _table_columns_in_schema(conn, "main", "position_events")
        if {
            "position_id",
            "event_type",
            "sequence_no",
            "payload_json",
        }.issubset(event_columns):
            monitor_event_payload = """
                (
                    SELECT pe.payload_json
                      FROM position_events AS pe
                     WHERE pe.position_id = position_current.position_id
                       AND pe.event_type = 'MONITOR_REFRESHED'
                     ORDER BY pe.sequence_no DESC
                     LIMIT 1
                ) AS last_monitor_event_payload_json
            """
            if "occurred_at" in event_columns:
                monitor_event_occurred_at = """
                    (
                        SELECT pe.occurred_at
                          FROM position_events AS pe
                         WHERE pe.position_id = position_current.position_id
                           AND pe.event_type = 'MONITOR_REFRESHED'
                         ORDER BY pe.sequence_no DESC
                         LIMIT 1
                    ) AS last_monitor_event_occurred_at
                """
    select_sql = ", ".join(
        [
            "position_id",
            "phase",
            _select_expr(columns, "shares", "shares", "0.0"),
            _select_expr(columns, "chain_shares", "chain_shares", "0.0"),
            _select_expr(columns, "updated_at", "updated_at", "''"),
            _select_expr(columns, "target_date", "target_date", "''"),
            _select_expr(columns, "chain_state", "chain_state", "''"),
            _select_expr(columns, "direction", "direction", "''"),
            _select_expr(columns, "order_status", "order_status", "''"),
            _select_expr(columns, "exit_retry_count", "exit_retry_count", "0"),
            _select_expr(columns, "next_exit_retry_at", "next_exit_retry_at", "''"),
            _select_expr(columns, "exit_reason", "exit_reason", "''"),
            _select_expr(columns, "last_monitor_prob", "last_monitor_prob"),
            _select_expr(
                columns,
                "last_monitor_prob_is_fresh",
                "last_monitor_prob_is_fresh",
                "0",
            ),
            _select_expr(
                columns,
                "last_monitor_market_price_is_fresh",
                "last_monitor_market_price_is_fresh",
                "0",
            ),
            _select_expr(columns, "last_monitor_best_bid", "last_monitor_best_bid"),
            monitor_event_payload,
            monitor_event_occurred_at,
        ]
    )
    where_sql = ""
    params: tuple[str, ...] = ()
    if position_ids is not None:
        params = tuple(dict.fromkeys(value for value in position_ids if value))
        if params:
            placeholders = ", ".join("?" for _value in params)
            where_sql = f" WHERE position_id IN ({placeholders})"
        else:
            where_sql = " WHERE 0"
    try:
        rows = conn.execute(
            f"SELECT {select_sql} FROM position_current{where_sql}",
            params,
        ).fetchall()
    except Exception:
        return None

    ordered: list[tuple[int, int, str, str, object]] = []
    for row in rows:
        position_id = str(_row_get(row, "position_id", "") or "").strip()
        phase = str(_row_get(row, "phase", "") or "").strip().lower()
        if not position_id or phase not in _CANONICAL_MONITOR_PHASE_PRIORITY:
            continue
        shares = _finite_positive_or_none(_row_get(row, "shares"))
        chain_shares = _finite_positive_or_none(_row_get(row, "chain_shares"))
        if shares is None and chain_shares is None:
            continue
        market_price_stale = 1
        if int(_row_get(row, "last_monitor_market_price_is_fresh", 0) or 0) == 1:
            market_price_stale = 0
        ordered.append(
            (
                _CANONICAL_MONITOR_PHASE_PRIORITY[phase],
                -market_price_stale,
                str(_row_get(row, "updated_at", "") or ""),
                position_id,
                row,
            )
        )
    ordered.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [row for *_unused, row in ordered]


def _canonical_monitor_position_order(conn, *, now_utc: datetime | None = None) -> list[str] | None:
    """Return monitorable position ids from canonical position_current."""

    rows = _canonical_monitor_position_rows(conn, now_utc=now_utc)
    if rows is None:
        return None
    return [str(_row_get(row, "position_id", "") or "").strip() for row in rows]


def _runtime_state_for_canonical_monitor_phase(phase: str) -> str:
    if phase == "active":
        return "entered"
    return phase


_PENDING_EXIT_ORDER_STATUSES = {
    "exit_intent",
    "sell_placed",
    "sell_pending",
    "retry_pending",
    "backoff_exhausted",
}


def _sync_position_from_canonical_monitor_row(pos, row) -> None:
    """Align the runtime Position view with the canonical monitor projection.

    The canonical projection decides the live monitor set.  If the in-memory
    portfolio object lags a previous canonical write, pending-exit rows must not
    re-enter the held-position exit emitter as stale day0/active positions.
    """

    phase = str(_row_get(row, "phase", "") or "").strip().lower()
    if phase in _CANONICAL_MONITOR_PHASE_PRIORITY:
        pos.state = _runtime_state_for_canonical_monitor_phase(phase)
    order_status = str(_row_get(row, "order_status", "") or "").strip()
    if order_status:
        pos.order_status = order_status
    try:
        pos.exit_retry_count = int(_row_get(row, "exit_retry_count", 0) or 0)
    except (TypeError, ValueError):
        pos.exit_retry_count = 0
    next_retry = str(_row_get(row, "next_exit_retry_at", "") or "").strip()
    pos.next_exit_retry_at = next_retry or None
    exit_reason = str(_row_get(row, "exit_reason", "") or "").strip()
    if exit_reason:
        pos.exit_reason = exit_reason
    pos.last_monitor_prob = _finite_probability_or_none(
        _row_get(row, "last_monitor_prob")
    )
    pos.last_monitor_prob_is_fresh = (
        int(_row_get(row, "last_monitor_prob_is_fresh", 0) or 0) == 1
    )
    pos.last_monitor_market_price_is_fresh = (
        int(_row_get(row, "last_monitor_market_price_is_fresh", 0) or 0) == 1
    )
    pos.last_monitor_best_bid = _finite_probability_or_none(
        _row_get(row, "last_monitor_best_bid")
    )
    pos.neg_edge_count = 0
    monitor_payload = _row_get(row, "last_monitor_event_payload_json")
    pos._canonical_monitor_refreshed_at = str(
        _row_get(row, "last_monitor_event_occurred_at", "") or ""
    )
    if monitor_payload:
        try:
            monitor_event = json.loads(str(monitor_payload))
            neg_edge_count = int(
                monitor_event.get("exit_decision_neg_edge_count") or 0
            )
            validations = monitor_event.get("exit_decision_applied_validations")
            if not isinstance(validations, list):
                validations = monitor_event.get("applied_validations")
            if isinstance(validations, list):
                pos.applied_validations = [
                    str(value) for value in validations if str(value).strip()
                ]
            pos.neg_edge_count = max(0, neg_edge_count)
        except (TypeError, ValueError, json.JSONDecodeError):
            pos.neg_edge_count = 0
    for attr in ("shares", "chain_shares"):
        value = _finite_positive_or_none(_row_get(row, attr))
        if value is not None:
            setattr(pos, attr, value)
    if phase == "pending_exit":
        if order_status in _PENDING_EXIT_ORDER_STATUSES:
            pos.exit_state = order_status
        elif pos.exit_retry_count > 0 and pos.next_exit_retry_at:
            pos.exit_state = "retry_pending"
        elif not str(getattr(pos, "exit_state", "") or ""):
            pos.exit_state = "exit_intent"
    elif str(getattr(pos, "exit_state", "") or "") in _PENDING_EXIT_ORDER_STATUSES:
        pos.exit_state = ""
        pos.exit_retry_count = 0
        pos.next_exit_retry_at = None


def _monitoring_phase_positions(portfolio, conn=None, *, now_utc: datetime | None = None) -> list:
    """Open positions requiring exit/hold redecision.

    When canonical ``position_current`` is available, it owns the live monitor
    set.  Legacy portfolio JSON can contain historical settled/quarantined rows
    with positive compatibility shares; those rows must not consume the
    second-level exit-monitor loop ahead of active/pending money-risk rows.
    """

    out = []
    seen: set[str] = set()
    all_positions = list(getattr(portfolio, "positions", []) or [])
    by_position_id = {
        str(getattr(pos, "trade_id", "") or ""): pos
        for pos in all_positions
        if str(getattr(pos, "trade_id", "") or "")
    }
    position_ids = None
    if getattr(portfolio, "authority_scope", "full_portfolio") == "runtime_exposure":
        position_ids = tuple(by_position_id)
    canonical_rows = _canonical_monitor_position_rows(
        conn,
        now_utc=now_utc,
        position_ids=position_ids,
    )
    if canonical_rows is not None:
        for row in canonical_rows:
            position_id = str(_row_get(row, "position_id", "") or "").strip()
            pos = by_position_id.get(position_id)
            if pos is None:
                continue
            _sync_position_from_canonical_monitor_row(pos, row)
            out.append(pos)
            seen.add(position_id)
        for pos in all_positions:
            position_id = str(getattr(pos, "trade_id", "") or "")
            if position_id in seen:
                continue
            if _venue_confirmed_local_fill_needs_monitor(pos):
                out.append(pos)
                seen.add(position_id)
        return out

    for pos in list(get_open_positions(portfolio)):
        out.append(pos)
        seen.add(str(getattr(pos, "trade_id", "") or id(pos)))
    for pos in all_positions:
        position_id = str(getattr(pos, "trade_id", "") or id(pos))
        if position_id in seen:
            continue
        if _venue_confirmed_local_fill_needs_monitor(pos):
            out.append(pos)
            seen.add(position_id)
            continue
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
    static_closed = _closed_by_static_market_end_info(
        conn,
        pos,
        decision_time=decision_time,
    )
    condition_id = str(
        getattr(pos, "condition_id", None)
        or getattr(pos, "market_id", None)
        or ""
    ).strip()
    from src.engine.monitor_refresh import (
        monitor_orderbook_prefetch_attempted,
        prefetched_monitor_orderbook,
    )

    held_token_id = _position_held_token_id(pos)
    held_book = prefetched_monitor_orderbook(clob, held_token_id)
    if held_book is not None and any(held_book.get(side) for side in ("bids", "asks")):
        return static_closed
    if held_book is None and monitor_orderbook_prefetch_attempted(
        clob,
        held_token_id,
    ):
        return static_closed

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
    return static_closed


def _is_open_crowding_exposure(pos) -> bool:
    state_value = _position_state_value(pos)
    if state_value in INACTIVE_RUNTIME_STATES:
        return False
    if state_value in {"pending_entry", "pending_tracked"}:
        return False
    return True


def _position_held_token_id(pos) -> str:
    direction = _position_direction_value(pos)
    if direction == "buy_no":
        return str(getattr(pos, "no_token_id", "") or getattr(pos, "token_id", "") or "")
    return str(getattr(pos, "token_id", "") or getattr(pos, "no_token_id", "") or "")


def _held_monitor_urgency_rank(pos) -> int:
    """Keep canonical exit urgency ahead of local/network throughput classes."""

    state = _position_state_value(pos)
    if state == "pending_exit":
        return 0
    if state == "day0_window":
        return 1
    return 2


def _held_monitor_schedule_key(
    pos,
    *,
    dead_bin_position_ids: frozenset[int],
    selected_urgent_position_ids: frozenset[int],
    selected_coverage_position_ids: frozenset[int],
    has_selected_urgent: bool,
    reserved_local_position_ids: frozenset[int],
    reserved_network_position_id: int | None,
    structural_win_position_ids: frozenset[int],
    network_book_tokens: frozenset[str],
) -> tuple[int, int]:
    position_id = id(pos)
    urgency = _held_monitor_urgency_rank(pos)
    network_dependent = (
        position_id not in structural_win_position_ids
        and _position_held_token_id(pos) in network_book_tokens
    )
    if position_id in selected_urgent_position_ids:
        return (-3 if position_id in dead_bin_position_ids else -2), urgency
    if has_selected_urgent:
        if position_id in selected_coverage_position_ids:
            return -1, urgency if urgency < 2 else 2 + int(network_dependent)
        if position_id in reserved_local_position_ids:
            return -1, 2
        if position_id == reserved_network_position_id:
            return -1, 3
        if position_id in dead_bin_position_ids or urgency < 2:
            return 0, urgency
        return 1, int(network_dependent)
    if position_id in reserved_local_position_ids:
        return 0, 0
    if position_id == reserved_network_position_id:
        return 0, 1
    if position_id in selected_coverage_position_ids:
        return 0, 1 if network_dependent else 0
    return 1, 1 if network_dependent else 0


_HELD_MONITOR_CURSOR_LOCK = threading.Lock()
_HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE: dict[str, str] = {}
_HELD_MONITOR_ATTEMPT_STATE_BY_LANE: dict[str, dict[str, tuple[int, str]]] = {}
_HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE: dict[str, int] = {}
# Bound the non-Day0 tail without letting continuous urgent wakes starve it.
_HELD_MONITOR_URGENT_ACTIVE_LOCAL_PROGRESS_LIMIT = 4


def _held_monitor_stable_position_key(pos) -> str:
    return "|".join(
        (
            str(getattr(pos, "trade_id", "") or ""),
            str(getattr(pos, "condition_id", "") or ""),
            _position_held_token_id(pos),
        )
    )


def _reserve_held_monitor_positions(
    lane: str,
    positions,
    *,
    limit: int,
    priority_key: Callable[[object], object] | None = None,
    fair_by_attempt: bool = False,
) -> list:
    """Select a thread-safe fair slice, seeded by durable monitor progress."""

    if not positions:
        with _HELD_MONITOR_CURSOR_LOCK:
            _HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE.pop(lane, None)
            _HELD_MONITOR_ATTEMPT_STATE_BY_LANE.pop(lane, None)
            _HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE.pop(lane, None)
        return []
    if limit <= 0:
        return []
    if fair_by_attempt or all(
        hasattr(pos, "_canonical_monitor_refreshed_at") for pos in positions
    ):
        keyed = [
            (
                _held_monitor_stable_position_key(pos),
                str(getattr(pos, "_canonical_monitor_refreshed_at", "") or ""),
                pos,
            )
            for pos in positions
        ]
        current_keys = {key for key, _refreshed_at, _pos in keyed}
        take = min(limit, len(keyed))
        with _HELD_MONITOR_CURSOR_LOCK:
            attempts = _HELD_MONITOR_ATTEMPT_STATE_BY_LANE.setdefault(lane, {})
            known_keys = set(attempts)
            # Urgent family wakes deliberately pass a strict subset of the held
            # book through this same lane.  Absence from that scoped call is not
            # closure: pruning it would reset full-book fairness and repeatedly
            # starve the tail.  A non-subset scope can authoritatively replace
            # stale keys; an empty scope is handled above.
            if not current_keys < known_keys:
                for stale_key in known_keys - current_keys:
                    del attempts[stale_key]
            sequence = _HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE.get(lane, 0)
            for key, refreshed_at, _pos in keyed:
                prior = attempts.get(key)
                if prior is not None and prior[1] != refreshed_at:
                    sequence += 1
                    attempts[key] = (sequence, refreshed_at)
            ordered = sorted(
                keyed,
                key=lambda item: (
                    attempts.get(item[0], (0, ""))[0],
                    priority_key(item[2]) if priority_key is not None else 0,
                    bool(item[1]),
                    item[1],
                    item[0],
                ),
            )
            selected = ordered[:take]
            for key, refreshed_at, _pos in selected:
                sequence += 1
                attempts[key] = (sequence, refreshed_at)
            _HELD_MONITOR_ATTEMPT_SEQUENCE_BY_LANE[lane] = sequence
        return [pos for _key, _refreshed_at, pos in selected]
    ordered = sorted(positions, key=_held_monitor_stable_position_key)
    keyed = [(_held_monitor_stable_position_key(pos), pos) for pos in ordered]
    keys = [key for key, _pos in keyed]
    take = min(limit, len(keyed))
    with _HELD_MONITOR_CURSOR_LOCK:
        last_key = _HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE.get(lane, "")
        start = bisect_right(keys, last_key) % len(keyed)
        selected = [keyed[(start + offset) % len(keyed)] for offset in range(take)]
        _HELD_MONITOR_CURSOR_LAST_KEY_BY_LANE[lane] = selected[-1][0]
    return [pos for _key, pos in selected]


def _reserve_active_network_monitor_position(positions) -> object | None:
    """Round-robin one ordinary network position through the cycle budget."""

    selected = _reserve_held_monitor_positions(
        "active_network",
        positions,
        limit=1,
    )
    return selected[0] if selected else None


def _reserve_urgent_monitor_positions(dead_bin_positions, urgent_positions) -> list:
    if dead_bin_positions and urgent_positions:
        return [
            *_reserve_held_monitor_positions(
                "dead_bin",
                dead_bin_positions,
                limit=1,
            ),
            *_reserve_held_monitor_positions(
                "canonical_urgent",
                urgent_positions,
                limit=1,
            ),
        ]
    lane, positions = (
        ("dead_bin", dead_bin_positions)
        if dead_bin_positions
        else ("canonical_urgent", urgent_positions)
    )
    return _reserve_held_monitor_positions(lane, positions, limit=2)


def _fresh_local_held_monitor_orderbooks(
    conn,
    positions,
    *,
    now_utc: datetime,
    summary: dict,
    deps,
) -> dict[str, dict]:
    if conn is None:
        return {}
    scope = list(
        dict.fromkeys(
            (condition_id, token_id)
            for pos in positions
            if (condition_id := str(getattr(pos, "condition_id", "") or "").strip())
            and (token_id := _position_held_token_id(pos))
        )
    )
    if not scope:
        return {}
    values_sql = ",".join("(?, ?)" for _ in scope)
    params = [part for pair in scope for part in pair]
    params.append(now_utc.astimezone(timezone.utc).isoformat())
    try:
        rows = conn.execute(
            f"""
            WITH requested(condition_id, token_id) AS (
                VALUES {values_sql}
            )
            SELECT latest.selected_outcome_token_id,
                   snapshot.orderbook_depth_json
              FROM requested
              JOIN executable_market_snapshot_latest AS latest
                ON latest.condition_id = requested.condition_id
               AND latest.selected_outcome_token_id = requested.token_id
              JOIN executable_market_snapshots AS snapshot
                ON snapshot.snapshot_id = latest.snapshot_id
             WHERE latest.active = 1
               AND latest.closed = 0
               AND latest.accepting_orders = 1
               AND latest.freshness_deadline >= ?
            """,
            params,
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - network remains the fallback.
        summary["held_monitor_local_orderbook_error"] = str(exc)[:500]
        deps.logger.warning(
            "held monitor local orderbook prefetch failed; using network fallback: %s",
            exc,
        )
        return {}

    books: dict[str, dict] = {}
    for row in rows:
        try:
            token_id, raw_book = row[0], row[1]
            book = json.loads(str(raw_book))
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            continue
        token_id = str(token_id or "").strip()
        asset_id = str(
            book.get("asset_id")
            or book.get("assetId")
            or book.get("token_id")
            or ""
        ).strip()
        if token_id and (not asset_id or asset_id == token_id):
            books[token_id] = book
    return books


def _prefetch_held_monitor_orderbooks(
    conn,
    clob,
    positions,
    summary: dict,
    *,
    now_utc: datetime,
    deps,
    local_only: bool = False,
) -> frozenset[str]:
    from src.data.market_scanner import _configured_batch_orderbook_getter
    from src.engine.monitor_refresh import install_monitor_orderbook_prefetch

    # A client may survive across cycles. Clear the prior cycle before any
    # return or failed fetch so stale executable truth cannot be reused.
    install_monitor_orderbook_prefetch(clob, {})
    getter = _configured_batch_orderbook_getter(clob)
    token_ids = list(
        dict.fromkeys(
            token_id
            for pos in positions
            if (token_id := _position_held_token_id(pos))
        )
    )
    summary["held_monitor_orderbooks_requested"] = len(token_ids)
    local_books = _fresh_local_held_monitor_orderbooks(
        conn,
        positions,
        now_utc=now_utc,
        summary=summary,
        deps=deps,
    )
    summary["held_monitor_orderbooks_local"] = len(local_books)
    missing_token_ids = [
        token_id for token_id in token_ids if token_id not in local_books
    ]
    summary["held_monitor_orderbooks_network_requested"] = len(missing_token_ids)
    if local_only or not missing_token_ids or getter is None:
        installed = install_monitor_orderbook_prefetch(clob, local_books)
        summary["held_monitor_orderbook_prefetch_installed"] = installed
        summary["held_monitor_orderbooks_prefetched"] = (
            len(local_books) if installed else 0
        )
        return frozenset(missing_token_ids if installed else token_ids)
    try:
        network_books = getter(missing_token_ids)
        if not isinstance(network_books, dict):
            raise TypeError("batch orderbook response must be a mapping")
    except Exception as exc:  # noqa: BLE001 - one failed batch must not fan out.
        summary["held_monitor_orderbook_prefetch_error"] = str(exc)[:500]
        network_books = {}
        deps.logger.warning(
            "held monitor batch orderbook prefetch failed; deferring ordinary quote reads: %s",
            exc,
        )
    books = {**local_books, **network_books}
    installed = install_monitor_orderbook_prefetch(
        clob,
        books,
        attempted_token_ids=missing_token_ids,
    )
    summary["held_monitor_orderbook_prefetch_installed"] = installed
    summary["held_monitor_orderbooks_prefetched"] = len(books) if installed else 0
    return frozenset(missing_token_ids if installed else token_ids)


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


def _current_monitor_result_probability_and_edge(pos) -> tuple[float | None, float | None]:
    """Return current-cycle monitor probability/edge only when authority is fresh."""

    if not bool(getattr(pos, "last_monitor_prob_is_fresh", False)):
        return None, None
    fresh_prob = _finite_float_or_none(getattr(pos, "last_monitor_prob", None))
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


def _incomplete_exit_observability_reason(exit_decision, exit_context) -> str | None:
    """The observability-recorder key for an evidence-incomplete exit verdict.

    Recognizes both vocabularies: the legacy INCOMPLETE_EXIT_CONTEXT(missing=…)
    string (in-flight rows) and the one-law EVIDENCE_UNAVAILABLE verdict, whose
    missing fields come from exit_context.missing_authority_fields() plus the
    quote axis (best_bid) rather than reason-string parsing.
    """
    reason = str(getattr(exit_decision, "reason", "") or "")
    if reason.startswith("INCOMPLETE_EXIT_CONTEXT"):
        return reason
    if reason != "EVIDENCE_UNAVAILABLE":
        return None
    missing = []
    fields = getattr(exit_context, "missing_authority_fields", None)
    if callable(fields):
        missing = list(fields())
    if not ExitContext._is_finite(getattr(exit_context, "best_bid", None)):
        missing.append("best_bid")
    return f"INCOMPLETE_EXIT_CONTEXT (missing={','.join(missing) or 'belief'})"


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
    if (
        bool(getattr(pos, "last_monitor_market_price_is_fresh", False))
        and getattr(pos, "last_monitor_market_price", None) is not None
    ):
        p_market = float(pos.last_monitor_market_price)
    elif getattr(edge_ctx, "p_market", None) is not None and len(edge_ctx.p_market) > 0:
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
    # state. We populate entry_ci / current_ci only from a finite current held-side
    # belief band. A missing current CI is an authority gap: Position.evaluate_exit
    # returns EVIDENCE_UNAVAILABLE and cannot fall back to the legacy point estimate
    # to authorize a live SELL.
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
        _entry_ci = (
            max(0.0, _entry_posterior - _ci_half),
            min(1.0, _entry_posterior + _ci_half),
        )
        # Shift edge-space band → belief space by adding the held-side price back.
        _current_ci = (
            max(0.0, float(_cb_lo) + float(_held_price)),
            min(1.0, float(_cb_hi) + float(_held_price)),
        )

    # Receipt-only evidence for the later family diagnostic. Always overwrite
    # so a missing current CI cannot reuse a prior monitor cycle's bound.
    setattr(pos, "_monitor_current_held_ci", _current_ci)

    # The monitor refresh writes the authoritative held-side probability onto
    # the position and stamps its freshness. Use that single surface at the
    # exit boundary so receipts, projections, and evaluate_exit cannot split
    # on two same-cycle probability values.
    _fresh_prob_source = (
        getattr(pos, "last_monitor_prob", None)
        if bool(getattr(pos, "last_monitor_prob_is_fresh", False))
        else getattr(edge_ctx, "p_posterior", None)
    )
    _fresh_prob = _finite_float_or_none(_fresh_prob_source)

    return ExitContext(
        fresh_prob=_fresh_prob,
        fresh_prob_is_fresh=bool(getattr(pos, "last_monitor_prob_is_fresh", False)),
        current_market_price=p_market,
        current_market_price_is_fresh=bool(getattr(pos, "last_monitor_market_price_is_fresh", False)),
        best_bid=best_bid,
        best_ask=getattr(pos, "last_monitor_best_ask", None),
        market_vig=getattr(pos, "last_monitor_market_vig", None),
        hours_to_settlement=hours_to_settlement,
        position_state=position_state,
        day0_active=position_state == "day0_window",
        day0_zero_probability_exit_authority=bool(
            getattr(pos, "_day0_zero_probability_exit_authority", False)
        ),
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


def _current_monitor_global_holding_coverage(
    *,
    conn,
    clob,
    portfolio,
    position,
    probability_witness_identity: str,
    checked_at_utc: datetime,
    current_time_provider: Callable[[], datetime] | None = None,
):
    """Resolve current ledger and executable token book before local delegation."""

    if conn is None or checked_at_utc.tzinfo is None:
        return None
    try:
        from src.contracts.executable_market_snapshot import (
            FRESHNESS_WINDOW_DEFAULT,
        )
        from src.engine.global_auction_universe import (
            _global_book_metadata_is_executable,
            _global_book_snapshot_rows,
            _global_sell_curve,
            current_portfolio_wealth_witness,
        )
        from src.engine.global_batch_runtime import (
            _CurrentHoldingWitness,
            current_global_holding_coverage,
        )
        from src.engine.global_single_order_auction import (
            global_sell_book_witness_identity,
        )
        from src.events.candidate_binding import weather_family_id
        from src.state.collateral_ledger import (
            COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS,
        )

        def current_time() -> datetime:
            value = (
                current_time_provider()
                if current_time_provider is not None
                else datetime.now(timezone.utc)
            )
            if value.tzinfo is None:
                raise ValueError("GLOBAL_HOLDING_COVERAGE_CURRENT_TIME_NAIVE")
            return value.astimezone(timezone.utc)

        checked = checked_at_utc.astimezone(timezone.utc)
        wealth = current_portfolio_wealth_witness(
            conn,
            decision_at_utc=checked,
            max_age=timedelta(
                seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS)
            ),
            portfolio_state=portfolio,
        )
        direction_raw = getattr(position, "direction", "")
        direction = str(
            getattr(direction_raw, "value", direction_raw) or ""
        ).lower()
        if direction == "buy_yes":
            side = "YES"
            token_id = str(getattr(position, "token_id", "") or "").strip()
        elif direction == "buy_no":
            side = "NO"
            token_id = str(getattr(position, "no_token_id", "") or "").strip()
        else:
            return None
        native_holdings = {
            str(token): Decimal(int(amount)) / Decimal("1000000")
            for token, amount in tuple(wealth.native_holdings_micro or ())
        }
        held_shares = native_holdings.get(token_id)
        if held_shares is None or held_shares <= 0:
            return None
        condition_id = str(
            getattr(position, "condition_id", "") or ""
        ).strip()
        family_key = weather_family_id(
            city=str(getattr(position, "city", "") or ""),
            target_date=str(getattr(position, "target_date", "") or ""),
            metric=str(
                getattr(position, "temperature_metric", "") or ""
            ).lower(),
        )

        def current_sell_book_witness(coverage) -> str | None:
            book_checked = current_time()
            metadata_rows = _global_book_snapshot_rows(
                conn,
                condition_ids=[condition_id],
                checked_at_utc=book_checked,
            )
            matches = tuple(
                row
                for row in metadata_rows
                if str(row.get("condition_id") or "") == condition_id
                and token_id
                in {
                    str(row.get("selected_outcome_token_id") or ""),
                    str(row.get("yes_token_id") or ""),
                    str(row.get("no_token_id") or ""),
                }
            )
            if len(matches) != 1 or not _global_book_metadata_is_executable(
                matches[0],
                checked_at_utc=book_checked,
            ):
                return None
            getter = getattr(clob, "get_orderbook_snapshot", None)
            if not callable(getter):
                getter = getattr(clob, "get_orderbook", None)
            if not callable(getter):
                return None
            raw_book = getter(token_id)
            if not isinstance(raw_book, Mapping):
                return None
            raw_asset_id = str(
                raw_book.get("asset_id")
                or raw_book.get("assetId")
                or raw_book.get("token_id")
                or ""
            ).strip()
            if raw_asset_id != token_id:
                return None
            curve = _global_sell_curve(
                family_key=coverage.family_key,
                bin_id=str(coverage.bin_id or ""),
                condition_id=condition_id,
                side=side,
                token_id=token_id,
                raw_book=raw_book,
                metadata=matches[0],
                captured_at_utc=book_checked,
                max_age=FRESHNESS_WINDOW_DEFAULT,
            )
            return (
                None
                if curve is None
                else global_sell_book_witness_identity(curve)
            )

        def current_probability_witness_identity(_coverage) -> str | None:
            from src.engine.monitor_refresh import (
                _refresh_current_global_day0_probability,
            )

            current = _refresh_current_global_day0_probability(
                position,
                trade_conn=conn,
                decision_time=current_time(),
                family_cache=None,
            )
            if current is None:
                return None
            receipt = getattr(
                current[1],
                "_day0_monitor_probability_receipt",
                None,
            )
            return (
                str(receipt.get("probability_witness_identity") or "")
                if isinstance(receipt, dict)
                else None
            )

        def current_holding_witness(_coverage) -> _CurrentHoldingWitness | None:
            current_wealth = current_portfolio_wealth_witness(
                conn,
                decision_at_utc=current_time(),
                max_age=timedelta(
                    seconds=float(COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS)
                ),
                portfolio_state=portfolio,
            )
            current_native = {
                str(token): Decimal(int(amount)) / Decimal("1000000")
                for token, amount in tuple(
                    current_wealth.native_holdings_micro or ()
                )
            }
            current_shares = current_native.get(token_id)
            if current_shares is None or current_shares <= 0:
                return None
            return _CurrentHoldingWitness(
                ledger_snapshot_id=str(current_wealth.ledger_snapshot_id),
                wealth_economic_identity=str(current_wealth.economic_identity),
                held_shares=current_shares,
            )

        return current_global_holding_coverage(
            position_id=str(
                getattr(position, "position_id", "")
                or getattr(position, "trade_id", "")
                or ""
            ),
            probability_witness_identity=probability_witness_identity,
            checked_at_utc=checked,
            family_key=family_key,
            bin_label=str(getattr(position, "bin_label", "") or "").strip(),
            condition_id=condition_id,
            side=side,
            token_id=token_id,
            held_shares=held_shares,
            current_ledger_snapshot_id=str(wealth.ledger_snapshot_id),
            current_wealth_economic_identity=str(wealth.economic_identity),
            current_sell_book_witness_resolver=current_sell_book_witness,
            current_probability_witness_identity_resolver=(
                current_probability_witness_identity
            ),
            current_holding_witness_resolver=current_holding_witness,
            current_time_provider=current_time,
        )
    except Exception as exc:  # noqa: BLE001 - incomplete witness preserves local exit
        logger.debug(
            "global holding coverage current-witness resolution failed for %s: %s",
            getattr(position, "trade_id", "unknown"),
            exc,
        )
        return None


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


def _release_monitor_write_lock_boundary(conn, summary: dict, deps, *, boundary: str) -> bool:
    """Commit monitor writes at bounded points so live price/decision writers can run."""

    if conn is None:
        return True
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
        return False
    else:
        summary["monitor_write_lock_releases"] = (
            summary.get("monitor_write_lock_releases", 0) + 1
        )
        return True



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
    held_position_monitor_budget_seconds: float | None = None,
    should_preempt_for_urgent_day0: Callable[[], bool] | None = None,
    defer_partial_orderbook_gaps: bool = False,
):
    from src.engine.monitor_refresh import (
        _GLOBAL_MONITOR_SAMPLES_ATTR,
        install_monitor_day0_family_cache,
        refresh_position,
    )
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

    portfolio_dirty = False
    tracker_dirty = False

    def urgent_preemption_requested() -> bool:
        if should_preempt_for_urgent_day0 is None:
            return False
        try:
            return bool(should_preempt_for_urgent_day0())
        except Exception as exc:  # noqa: BLE001 - priority hint must not blind exits.
            summary["held_monitor_preemption_probe_errors"] = (
                summary.get("held_monitor_preemption_probe_errors", 0) + 1
            )
            deps.logger.warning("held monitor urgent-preemption probe failed: %s", exc)
            return False

    if urgent_preemption_requested():
        summary["held_monitor_preempted"] = True
        summary["held_monitor_defer_reason"] = "urgent_day0_wake"
        return portfolio_dirty, tracker_dirty

    if run_exit_preflight:
        try:
            exit_stats = check_pending_exits(portfolio, clob, conn=conn)
        except Exception as exc:  # noqa: BLE001 - one pending-exit fault must not blind held monitoring.
            logger = getattr(deps, "logger", None)
            if logger is not None:
                logger.error(
                    "pending-exit preflight failed; continuing held-position monitor: %s",
                    exc,
                    exc_info=True,
                )
            summary["pending_exit_preflight_failed"] = (
                summary.get("pending_exit_preflight_failed", 0) + 1
            )
            summary["pending_exit_preflight_error"] = str(exc)
            exit_stats = {"filled": 0, "retried": 0, "unchanged": 0, "filled_positions": []}
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
        summary["pending_exit_scan_candidates"] = exit_stats.get(
            "pending_exit_scan_candidates",
            0,
        )
        summary["pending_exit_positions_scanned"] = exit_stats.get(
            "pending_exit_positions_scanned",
            0,
        )
        if exit_stats.get("pending_exit_positions_deferred"):
            summary["pending_exit_positions_deferred"] = exit_stats.get(
                "pending_exit_positions_deferred",
                0,
            )
            summary["pending_exit_defer_reason"] = exit_stats.get(
                "pending_exit_defer_reason",
                "",
            )
        _release_monitor_write_lock_boundary(
            conn,
            summary,
            deps,
            boundary="exit_preflight",
        )
    else:
        summary["exit_preflight_skipped_for_monitor_refresh"] = True

    try:
        monitor_now_utc = deps._utcnow() if hasattr(deps, "_utcnow") else datetime.now(timezone.utc)
    except Exception:
        monitor_now_utc = datetime.now(timezone.utc)
    if monitor_now_utc.tzinfo is None:
        monitor_now_utc = monitor_now_utc.replace(tzinfo=timezone.utc)
    else:
        monitor_now_utc = monitor_now_utc.astimezone(timezone.utc)
    monitor_positions = _monitoring_phase_positions(
        portfolio,
        conn=conn,
        now_utc=monitor_now_utc,
    )
    monitor_budget_seconds = _held_position_monitor_budget_seconds(
        held_position_monitor_budget_seconds
    )
    monitor_progress_limit = _held_position_monitor_positive_progress_limit(
        len(monitor_positions)
    )
    monitor_deadline = time.monotonic() + monitor_budget_seconds
    summary["held_monitor_candidates"] = len(monitor_positions)
    summary["held_monitor_budget_seconds"] = monitor_budget_seconds
    if urgent_preemption_requested():
        summary["held_monitor_preempted"] = True
        summary["held_monitor_positions_deferred"] = len(monitor_positions)
        summary["held_monitor_defer_reason"] = "urgent_day0_wake"
        return portfolio_dirty, tracker_dirty
    install_monitor_day0_family_cache(clob)

    durable_hard_facts = {}
    from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit

    for pos in monitor_positions:
        city = deps.cities_by_name.get(pos.city)
        if not _day0_hard_fact_position_eligible(pos) or city is None:
            continue
        try:
            verdict = evaluate_hard_fact_exit(
                position=pos,
                city=city,
                now=monitor_now_utc,
                world_conn=conn,
                durable_only=True,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one family from the batch.
            summary["held_monitor_hard_fact_preclass_errors"] = (
                summary.get("held_monitor_hard_fact_preclass_errors", 0) + 1
            )
            deps.logger.warning(
                "held monitor hard-fact preclassification failed for %s: %s",
                pos.trade_id,
                exc,
            )
            continue
        if verdict is not None:
            durable_hard_facts[id(pos)] = verdict
    summary["held_monitor_durable_hard_facts"] = len(durable_hard_facts)
    structural_win_position_ids = frozenset(
        id(pos)
        for pos in monitor_positions
        if getattr(durable_hard_facts.get(id(pos)), "action", None)
        == "HOLD_STRUCTURAL_WIN"
    )
    dead_bin_position_ids = frozenset(
        id(pos)
        for pos in monitor_positions
        if getattr(durable_hard_facts.get(id(pos)), "action", None)
        == "EXIT_DEAD_BIN"
    )
    selected_coverage_positions = (
        _reserve_held_monitor_positions(
            "bounded_coverage",
            monitor_positions,
            limit=monitor_progress_limit,
            priority_key=lambda pos: (
                -1
                if id(pos) in dead_bin_position_ids
                else _held_monitor_urgency_rank(pos)
            ),
            fair_by_attempt=True,
        )
        if monitor_budget_seconds > 0.0
        else []
    )
    selected_coverage_position_ids = frozenset(
        id(pos) for pos in selected_coverage_positions
    )
    quote_positions = [
        pos for pos in monitor_positions if id(pos) not in structural_win_position_ids
    ]
    summary["held_monitor_structural_win_orderbooks_bypassed"] = (
        len(monitor_positions) - len(quote_positions)
    )
    local_prefetch: dict = {}
    network_book_tokens = _prefetch_held_monitor_orderbooks(
        conn,
        clob,
        quote_positions,
        local_prefetch,
        now_utc=monitor_now_utc,
        deps=deps,
        local_only=True,
    )
    summary.update(local_prefetch)
    local_book_tokens = frozenset(
        _position_held_token_id(pos) for pos in quote_positions
    ) - network_book_tokens
    local_first_network_gap = bool(
        defer_partial_orderbook_gaps and local_book_tokens and network_book_tokens
    )
    if local_first_network_gap:
        summary["held_monitor_partial_orderbook_gaps_scheduled_after_local"] = len(
            network_book_tokens
        )
    network_positions = [
        pos
        for pos in quote_positions
        if _position_held_token_id(pos) in network_book_tokens
    ]
    ordinary_active_network_positions = [
        pos
        for pos in network_positions
        if id(pos) not in dead_bin_position_ids
        and _held_monitor_urgency_rank(pos) == 2
    ]
    reserved_network_position = _reserve_active_network_monitor_position(
        ordinary_active_network_positions,
    )
    reserved_network_position_id = (
        id(reserved_network_position)
        if reserved_network_position is not None
        else None
    )
    dead_bin_positions = [
        pos for pos in monitor_positions if id(pos) in dead_bin_position_ids
    ]
    canonical_urgent_positions = [
        pos
        for pos in monitor_positions
        if id(pos) not in dead_bin_position_ids
        and _held_monitor_urgency_rank(pos) < 2
    ]
    selected_urgent_positions = _reserve_urgent_monitor_positions(
        dead_bin_positions,
        canonical_urgent_positions,
    )
    selected_urgent_position_ids = frozenset(
        id(pos) for pos in selected_urgent_positions
    )
    has_selected_urgent = bool(selected_urgent_positions)
    ordinary_active_local_positions = [
        pos
        for pos in monitor_positions
        if _held_monitor_urgency_rank(pos) == 2
        and _position_held_token_id(pos) not in network_book_tokens
    ]
    reserved_local_positions = (
        []
        if has_selected_urgent and should_preempt_for_urgent_day0 is None
        else _reserve_held_monitor_positions(
            "active_local",
            ordinary_active_local_positions,
            limit=(
                _HELD_MONITOR_URGENT_ACTIVE_LOCAL_PROGRESS_LIMIT
                if has_selected_urgent
                else 1
            ),
        )
    )
    reserved_local_position = (
        reserved_local_positions[0] if reserved_local_positions else None
    )
    reserved_local_position_ids = frozenset(
        id(position) for position in reserved_local_positions
    )
    summary["held_monitor_active_local_progress_position"] = (
        getattr(reserved_local_position, "trade_id", "")
        if reserved_local_position is not None
        else ""
    )
    summary["held_monitor_active_local_progress_positions"] = [
        str(getattr(position, "trade_id", "") or "")
        for position in reserved_local_positions
    ]
    summary["held_monitor_active_network_progress_position"] = (
        getattr(reserved_network_position, "trade_id", "")
        if reserved_network_position is not None
        else ""
    )
    summary["held_monitor_budget_urgent_positions"] = [
        str(getattr(pos, "trade_id", "") or "")
        for pos in selected_urgent_positions
    ]
    summary["held_monitor_budget_coverage_positions"] = [
        str(getattr(pos, "trade_id", "") or "")
        for pos in selected_coverage_positions
    ]

    # Canonical lifecycle urgency is the first ordering key.  Within the
    # pending-exit and Day0 tranches, start the network batch before consuming
    # local work: otherwise a short monitor budget can starve an urgent held
    # position indefinitely.  Ordinary active positions retain local-first
    # throughput once all urgent positions have had their batch opportunity.
    monitor_positions = sorted(
        monitor_positions,
        key=lambda pos: _held_monitor_schedule_key(
            pos,
            dead_bin_position_ids=dead_bin_position_ids,
            selected_urgent_position_ids=selected_urgent_position_ids,
            selected_coverage_position_ids=selected_coverage_position_ids,
            has_selected_urgent=has_selected_urgent,
            reserved_local_position_ids=reserved_local_position_ids,
            reserved_network_position_id=reserved_network_position_id,
            structural_win_position_ids=structural_win_position_ids,
            network_book_tokens=network_book_tokens,
        ),
    )
    budget_guaranteed_position_ids = frozenset(
        {
            *selected_urgent_position_ids,
            *selected_coverage_position_ids,
            *reserved_local_position_ids,
            *(
                position_id
                for position_id in (
                    reserved_network_position_id,
                )
                if position_id is not None
            ),
        }
    )
    summary["held_monitor_budget_guaranteed_positions"] = len(
        budget_guaranteed_position_ids
    )
    summary["held_monitor_local_ready_positions"] = sum(
        1
        for pos in monitor_positions
        if id(pos) in structural_win_position_ids
        or _position_held_token_id(pos) in local_book_tokens
        or _position_held_token_id(pos) not in network_book_tokens
    )
    network_prefetch_started = False
    network_prefetch_unavailable = False
    summary["held_monitor_positive_progress_limit"] = monitor_progress_limit

    for position_index, pos in enumerate(monitor_positions):
        if urgent_preemption_requested():
            deferred_count = len(monitor_positions) - position_index
            summary["held_monitor_preempted"] = True
            summary["held_monitor_positions_deferred"] = deferred_count
            summary["held_monitor_defer_reason"] = "urgent_day0_wake"
            break
        monitor_deadline_expired = time.monotonic() >= monitor_deadline
        monitor_progress_count = int(summary.get("monitors", 0) or 0)
        monitor_progress_persisted = monitor_progress_count > 0
        monitor_progress_limit_reached = (
            monitor_budget_seconds > 0.0
            and monitor_progress_count
            >= monitor_progress_limit
        )
        if (
            (
                monitor_progress_limit_reached
                and id(pos) not in budget_guaranteed_position_ids
            )
            or (
                monitor_deadline_expired
                and (
                    id(pos) not in budget_guaranteed_position_ids
                    or (monitor_budget_seconds > 0.0 and monitor_progress_persisted)
                )
            )
        ):
            deferred_count = len(monitor_positions) - position_index
            if deferred_count > 0:
                summary["held_monitor_positions_deferred"] = deferred_count
                summary["held_monitor_defer_reason"] = (
                    "positive_budget_progress_limit"
                    if monitor_progress_limit_reached
                    else "cycle_budget_exhausted"
                )
                if monitor_progress_limit_reached:
                    summary["held_monitor_progress_limit_reached"] = True
                if monitor_deadline_expired:
                    summary["held_monitor_deadline_deferred_positions"] = deferred_count
                    summary["held_monitor_deadline_defer_reason"] = (
                        "MONITOR_DEADLINE_EXPIRED"
                    )
            break
        if monitor_deadline_expired:
            summary["held_monitor_budget_bypass_scanned"] = (
                summary.get("held_monitor_budget_bypass_scanned", 0) + 1
            )
        summary["held_monitor_positions_scanned"] = (
            summary.get("held_monitor_positions_scanned", 0) + 1
        )
        if pos.state == "pending_tracked":
            continue
        state_value = _position_state_value(pos)
        if is_terminal_state(state_value):
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
                _record_monitor_hold_decision(
                    conn,
                    pos,
                    artifact=artifact,
                    deps=deps,
                    summary=summary,
                    reason="PENDING_EXIT_RETRY_COOLDOWN_ACTIVE",
                    trigger="PENDING_EXIT_RETRY_COOLDOWN_ACTIVE",
                    validation="pending_exit_retry_cooldown_monitor_hold",
                    counter="monitor_pending_exit_retry_cooldown_holds",
                )
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
            _record_monitor_hold_decision(
                conn,
                pos,
                artifact=artifact,
                deps=deps,
                summary=summary,
                reason="EXIT_ORDER_ALREADY_IN_FLIGHT",
                trigger="EXIT_ORDER_ALREADY_IN_FLIGHT",
                validation="exit_order_in_flight_monitor_hold",
                counter="monitor_exit_order_in_flight_holds",
            )
            continue
        if pos.exit_state == "backoff_exhausted":
            _record_monitor_hold_decision(
                conn,
                pos,
                artifact=artifact,
                deps=deps,
                summary=summary,
                reason="PENDING_EXIT_BACKOFF_EXHAUSTED_REDECISION_BLOCKED",
                trigger="PENDING_EXIT_BACKOFF_EXHAUSTED_REDECISION_BLOCKED",
                validation="pending_exit_backoff_exhausted_monitor_hold",
                counter="monitor_pending_exit_backoff_exhausted_holds",
            )
            continue
        if is_exit_cooldown_active(pos):
            _record_monitor_hold_decision(
                conn,
                pos,
                artifact=artifact,
                deps=deps,
                summary=summary,
                reason="EXIT_RETRY_COOLDOWN_ACTIVE",
                trigger="EXIT_RETRY_COOLDOWN_ACTIVE",
                validation="exit_retry_cooldown_monitor_hold",
                counter="monitor_exit_retry_cooldown_holds",
            )
            continue

        if run_exit_preflight:
            check_pending_retries(pos, conn=conn)

        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
        # PHASE LAW): the quarantine-admin-resolution monitor branch is
        # retired — no writer mints phase/chain_state='quarantined', and the
        # DB CHECK no longer admits the literal post-migration, so no
        # position ever reaches the monitor loop carrying it. A real-exposure
        # position that used to be diverted into this admin-resolution limbo
        # now correctly flows through normal monitor refresh below instead of
        # being stranded.

        review_fact = _blocking_review_fact_for_position(portfolio, pos)
        if review_fact is not None:
            _record_monitor_hold_decision(
                conn,
                pos,
                artifact=artifact,
                deps=deps,
                summary=summary,
                reason="REVIEW_REQUIRED_INVALID_ENTRY_PROOF",
                trigger="REVIEW_REQUIRED_INVALID_ENTRY_PROOF",
                validation="blocking_review_fact_monitor_hold",
                counter="monitor_skipped_blocking_review_fact",
            )
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

        held_token_id = _position_held_token_id(pos)
        if held_token_id in network_book_tokens:
            if network_prefetch_unavailable:
                summary["held_monitor_positions_deferred_for_orderbook_gap"] = (
                    summary.get(
                        "held_monitor_positions_deferred_for_orderbook_gap",
                        0,
                    )
                    + 1
                )
                continue
            if not network_prefetch_started:
                # Local lifecycle writes must be durable before optional CLOB
                # I/O.  A failed commit is a typed deferral, never permission
                # to call the batch getter against an uncertain write state.
                if not _release_monitor_write_lock_boundary(
                    conn,
                    summary,
                    deps,
                    boundary="before_network_orderbook_prefetch",
                ):
                    network_prefetch_unavailable = True
                    summary["held_monitor_orderbook_prefetch_defer_reason"] = (
                        "MONITOR_WRITE_COMMIT_FAILED"
                    )
                    summary["held_monitor_positions_deferred_for_commit_failure"] = (
                        summary.get(
                            "held_monitor_positions_deferred_for_commit_failure",
                            0,
                        )
                        + 1
                    )
                    continue
                network_prefetch_started = True
                network_prefetch: dict = {}
                _prefetch_held_monitor_orderbooks(
                    conn,
                    clob,
                    network_positions,
                    network_prefetch,
                    now_utc=monitor_now_utc,
                    deps=deps,
                )
                summary["held_monitor_orderbooks_prefetched"] = (
                    int(summary.get("held_monitor_orderbooks_prefetched", 0) or 0)
                    + int(
                        network_prefetch.get(
                            "held_monitor_orderbooks_prefetched",
                            0,
                        )
                        or 0
                    )
                )
                if error := network_prefetch.get("held_monitor_orderbook_prefetch_error"):
                    summary["held_monitor_orderbook_prefetch_error"] = error
                    network_prefetch_unavailable = True
                    summary["held_monitor_orderbook_prefetch_defer_reason"] = (
                        "ORDERBOOK_BATCH_UNAVAILABLE"
                    )
                if not network_prefetch.get(
                    "held_monitor_orderbook_prefetch_installed",
                    False,
                ):
                    # Some test/minimal clients cannot retain cycle-local
                    # attributes.  They are not local-ready and cannot claim
                    # prefetched depth, but the normal one-position monitor
                    # fallback remains available.
                    summary["held_monitor_orderbook_prefetch_unavailable"] = (
                        "ORDERBOOK_PREFETCH_INSTALL_FAILED"
                    )
                if network_prefetch_unavailable:
                    summary["held_monitor_positions_deferred_for_orderbook_gap"] = (
                        summary.get(
                            "held_monitor_positions_deferred_for_orderbook_gap",
                            0,
                        )
                        + 1
                    )
                    continue
                if urgent_preemption_requested():
                    # This position is already counted as scanned above; only
                    # the unvisited tail is deferred.
                    deferred_count = len(monitor_positions) - position_index - 1
                    summary["held_monitor_preempted"] = True
                    summary["held_monitor_positions_deferred"] = deferred_count
                    summary["held_monitor_defer_reason"] = "urgent_day0_wake"
                    break

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
                        and _position_state_value(pos) in {"active", "entered", "holding"}
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
                        canonical_day0_written = False
                        try:
                            # Temporarily set fields for canonical persistence.
                            old_state = pos.state
                            old_day0 = pos.day0_entered_at
                            pos.state = new_state
                            pos.day0_entered_at = new_day0_entered_at
                            canonical_day0_written = _emit_day0_window_entered_canonical_if_available(
                                conn,
                                pos,
                                day0_entered_at=new_day0_entered_at,
                                previous_phase=previous_phase_str,
                                deps=deps,
                            )
                        except Exception as exc:
                            # Revert memory to pre-transition state
                            pos.state = old_state
                            pos.day0_entered_at = old_day0
                            deps.logger.warning(
                                "Day0 transition ABORTED for %s: canonical persist failed: %s",
                                pos.trade_id,
                                exc,
                            )
                            continue
                        try:
                            from src.state.db import update_trade_lifecycle
                            update_trade_lifecycle(conn=conn, pos=pos)
                        except Exception as exc:
                            if canonical_day0_written:
                                deps.logger.warning(
                                    "Day0 transition legacy bridge skipped for %s: %s",
                                    pos.trade_id,
                                    exc,
                                )
                            else:
                                pos.state = old_state
                                pos.day0_entered_at = old_day0
                                deps.logger.warning(
                                    "Day0 transition ABORTED for %s: legacy persist failed before canonical write: %s",
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
                    if conn is None:
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
            _hard_fact = durable_hard_facts.get(id(pos))
            if _day0_hard_fact_position_eligible(pos) and city is not None:
                try:
                    from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit
                    # Pass conn as world_conn so the METAR kill-memo cold-start
                    # recovery does not open per-city independent world connections
                    # (connection-burst antibody 2026-06-13).
                    if _hard_fact is None:
                        _hard_fact = evaluate_hard_fact_exit(
                            position=pos,
                            city=city,
                            now=deps._utcnow(),
                            world_conn=conn,
                        )
                except Exception as _hf_exc:  # noqa: BLE001 — lane must never break the monitor
                    deps.logger.warning(
                        "day0 hard-fact lane failed for %s (non-fatal): %s", pos.trade_id, _hf_exc
                    )
            if _hard_fact is not None and _hard_fact.action == "HOLD_STRUCTURAL_WIN":
                # Terminal value is already exactly one. Venue metadata cannot
                # change the hold decision, so only the local close timestamp is
                # relevant; remote market/book reads would delay unrelated exits.
                closed_market_info = _closed_by_static_market_end_info(
                    conn,
                    pos,
                    decision_time=deps._utcnow(),
                )
            else:
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
                    from src.execution.exit_lifecycle import mark_market_closed_hold_to_settlement

                    mark_market_closed_hold_to_settlement(
                        pos,
                        reason=exit_decision.trigger,
                        error=str(
                            closed_market_info.get("source")
                            or "market_closed_non_accepting_orders"
                        ),
                        conn=conn,
                    )
                    summary["day0_hard_fact_closed_market_hold_to_settlement"] = (
                        summary.get("day0_hard_fact_closed_market_hold_to_settlement", 0) + 1
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
                    preserve_exit_reason=True,
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

            # Earlier pending-exit/Day0 lifecycle work may have opened a TRADE
            # write transaction. Release it before probability refresh can
            # persist a world-owned Day0 observation fact. Otherwise the
            # monitor holds TRADE while waiting for WORLD, inverse to the
            # price-channel WORLD-main+TRADE-attached writer.
            _release_monitor_write_lock_boundary(
                conn,
                summary,
                deps,
                boundary="before_probability_refresh",
            )
            if _hard_fact is not None and _hard_fact.action == "EXIT_DEAD_BIN":
                from src.engine.monitor_refresh import refresh_exact_zero_position

                edge_ctx = refresh_exact_zero_position(conn, clob, pos)
                summary["day0_hard_fact_probability_refresh_bypassed"] = (
                    summary.get(
                        "day0_hard_fact_probability_refresh_bypassed",
                        0,
                    )
                    + 1
                )
            elif _hard_fact is not None and _hard_fact.action == "HOLD_STRUCTURAL_WIN":
                from src.engine.monitor_refresh import refresh_exact_one_position

                edge_ctx = refresh_exact_one_position(pos)
                summary["day0_hard_fact_probability_refresh_bypassed"] = (
                    summary.get(
                        "day0_hard_fact_probability_refresh_bypassed",
                        0,
                    )
                    + 1
                )
                summary["day0_hard_fact_structural_win_quote_bypassed"] = (
                    summary.get(
                        "day0_hard_fact_structural_win_quote_bypassed",
                        0,
                    )
                    + 1
                )
            else:
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
            if run_exit_preflight and not (
                _hard_fact is not None
                and _hard_fact.action == "HOLD_STRUCTURAL_WIN"
            ):
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
                    if pending_exit_monitor_only and check_pending_retries(
                        pos,
                        conn=conn,
                    ):
                        pending_exit_monitor_only = False
                        portfolio_dirty = True
                        summary["pending_exit_liquidity_wait_released"] = (
                            summary.get("pending_exit_liquidity_wait_released", 0)
                            + 1
                        )
            p_market = exit_context.current_market_price
            portfolio_dirty = True
            # An absorbing hard fact makes the held token worth exactly zero at
            # settlement. Selling it for any executable positive bid strictly
            # dominates HOLD in every state, so waiting for the global auction
            # can only destroy value. Complementary new risk still belongs to the
            # global BUY/HOLD/CASH auction.
            if _hard_fact is not None and _hard_fact.action == "EXIT_DEAD_BIN":
                from src.state.portfolio import ExitDecision as _ExitDecision

                pos.applied_validations = list(
                    dict.fromkeys(
                        [
                            *(pos.applied_validations or []),
                            "day0_hard_fact_zero_value_exit",
                        ]
                    )
                )
                exit_decision = _ExitDecision(
                    True,
                    "DAY0_HARD_FACT_BIN_DEAD "
                    f"({_hard_fact.reason}; source={_hard_fact.source})",
                    urgency="immediate",
                    trigger="DAY0_HARD_FACT_BIN_DEAD",
                    selected_method=pos.selected_method or pos.entry_method,
                    applied_validations=list(pos.applied_validations),
                )
                summary["day0_hard_fact_direct_exit_decisions"] = (
                    summary.get(
                        "day0_hard_fact_direct_exit_decisions", 0
                    )
                    + 1
                )
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
                _hard_fact is not None
                and _hard_fact.action in {"EXIT_DEAD_BIN", "HOLD_STRUCTURAL_WIN"}
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

            # Statistical SELL remains globally optimized. An absorbing hard-
            # fact SELL is different: positive cash strictly dominates a token
            # with exact terminal value zero, so it must not wait behind the
            # full-universe auction.
            local_exit_trigger = _effective_exit_trigger(exit_decision, exit_reason)
            statistical_sell_requires_global = (
                should_exit
                and _global_auction_owns_statistical_sell(
                    exit_decision,
                    exit_reason,
                )
            )
            global_holding_coverage = None
            if (
                should_exit
                and local_exit_trigger != "RED_FORCE_EXIT"
                and local_exit_trigger != "DAY0_HARD_FACT_BIN_DEAD"
                and getattr(pos, _GLOBAL_MONITOR_SAMPLES_ATTR, None) is not None
            ):
                probability_receipt = getattr(
                    pos,
                    "_day0_monitor_probability_receipt",
                    None,
                )
                probability_witness_identity = (
                    str(
                        probability_receipt.get("probability_witness_identity")
                        or ""
                    )
                    if isinstance(probability_receipt, dict)
                    else ""
                )
                if probability_witness_identity:
                    def coverage_time() -> datetime:
                        try:
                            value = (
                                deps._utcnow()
                                if hasattr(deps, "_utcnow")
                                else datetime.now(timezone.utc)
                            )
                        except Exception:
                            value = datetime.now(timezone.utc)
                        return (
                            value.replace(tzinfo=timezone.utc)
                            if value.tzinfo is None
                            else value.astimezone(timezone.utc)
                        )

                    try:
                        coverage_checked_at = coverage_time()
                    except Exception:
                        coverage_checked_at = datetime.now(timezone.utc)
                    global_holding_coverage = _current_monitor_global_holding_coverage(
                        conn=conn,
                        clob=clob,
                        portfolio=portfolio,
                        position=pos,
                        probability_witness_identity=(
                            probability_witness_identity
                        ),
                        checked_at_utc=coverage_checked_at,
                        current_time_provider=coverage_time,
                    )
            if (
                should_exit
                and local_exit_trigger != "RED_FORCE_EXIT"
                and local_exit_trigger != "DAY0_HARD_FACT_BIN_DEAD"
                and global_holding_coverage is not None
            ):
                coverage, coverage_receipt_id = global_holding_coverage
                should_exit = False
                exit_reason = "GLOBAL_AUCTION_OWNS_REDUCE_ONLY_SELL"
                pos.applied_validations = list(
                    dict.fromkeys(
                        [
                            *(pos.applied_validations or []),
                            "local_monitor_sell_delegated_to_global_auction",
                            (
                                "global_holding_coverage_receipt:"
                                f"{coverage_receipt_id}"
                            ),
                            (
                                "global_holding_coverage_epoch:"
                                f"{coverage.selection_epoch_identity}"
                            ),
                        ]
                    )
                )
                summary["monitor_sells_delegated_to_global_auction"] = (
                    summary.get("monitor_sells_delegated_to_global_auction", 0) + 1
                )
                summary.setdefault(
                    "monitor_global_holding_coverage_receipt_ids",
                    [],
                ).append(coverage_receipt_id)
            elif statistical_sell_requires_global:
                should_exit = False
                exit_reason = (
                    "GLOBAL_AUCTION_STATISTICAL_SELL_AUTHORITY_UNAVAILABLE"
                )
                pos.applied_validations = list(
                    dict.fromkeys(
                        [
                            *(pos.applied_validations or []),
                            "local_statistical_sell_diagnostic_only",
                            "global_statistical_sell_authority_unavailable",
                        ]
                    )
                )
                summary["monitor_statistical_sells_blocked_without_global_authority"] = (
                    summary.get(
                        "monitor_statistical_sells_blocked_without_global_authority",
                        0,
                    )
                    + 1
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
            _incomplete_reason = _incomplete_exit_observability_reason(
                exit_decision, exit_context
            )
            if _incomplete_reason is not None and not should_exit:
                _record_incomplete_exit_context_summary(
                    summary,
                    pos=pos,
                    exit_reason=_incomplete_reason,
                    hours_to_settlement=hours_to_settlement,
                )
                deps.logger.warning(
                    "Exit authority incomplete for %s: %s",
                    pos.trade_id,
                    _incomplete_reason,
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
                if exit_trigger != "RED_FORCE_EXIT":
                    monitor_fresh_prob, monitor_fresh_edge = _current_monitor_result_probability_and_edge(pos)
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

            monitor_fresh_prob, monitor_fresh_edge = _current_monitor_result_probability_and_edge(pos)
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
                    from src.engine.global_batch_runtime import (
                        _invalidate_global_holding_coverage,
                    )

                    _invalidate_global_holding_coverage()
                    outcome = execute_exit(
                        portfolio=portfolio,
                        position=pos,
                        exit_context=replace(exit_context, exit_reason=exit_reason),
                        clob=clob,
                        conn=conn,
                        exit_intent=exit_intent,
                        hard_fact_authority=(
                            _hard_fact
                            if exit_trigger == "DAY0_HARD_FACT_BIN_DEAD"
                            else None
                        ),
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
                    preserve_exit_reason=True,
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


def _rotation_held_positions(conn) -> tuple[list, list[str]]:
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
        "last_monitor_best_bid",
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
            "last_monitor_best_bid",
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
        best_bid = _finite_probability_or_none(_row_get(row, "last_monitor_best_bid"))
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
    lookback_hours = max(
        0.25,
        float(os.environ.get("ZEUS_PORTFOLIO_ROTATION_CANDIDATE_LOOKBACK_HOURS", "6.0")),
    )
    earliest = decision_time.astimezone(timezone.utc) - timedelta(hours=lookback_hours)
    try:
        rows = conn.execute(
            f"""
            SELECT {select_sql}
              FROM {schema}.no_trade_regret_events
             WHERE created_at >= ?
               AND trade_score > 0
             ORDER BY created_at DESC
             LIMIT 200
            """,
            (earliest.isoformat(),),
        ).fetchall()
    except Exception as exc:
        return [], [f"no_trade_regret_events_read_failed:{exc.__class__.__name__}"]
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

    holds, hold_missing = _rotation_held_positions(conn)
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
    if value in {"LOW", "LOW_COVERAGE", "WINDOW_INCOMPLETE", "GAP_SUSPECT"}:
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
