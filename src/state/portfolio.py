"""Portfolio state management. Spec §6.4.

Atomic JSON + SQL mirror. Positions are the source of truth.
Provides exposure queries for risk limit enforcement.
"""

import json
import logging
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.config import STATE_DIR, get_mode, settings, state_path
from src.contracts import (
    HeldSideProbability, 
    NativeSidePrice, 
    compute_forward_edge,
    ExpiringAssumption,
)
from src.contracts.semantic_types import ChainState, Direction, DirectionAlias, ExitState, LifecycleState
from src.state.lifecycle_manager import (
    enter_admin_closed_runtime_state,
    enter_economically_closed_runtime_state,
    enter_settled_runtime_state,
    enter_voided_runtime_state,
)
from src.state.portfolio_loader_policy import choose_portfolio_truth_source
from src.state.truth_files import annotate_truth_payload

logger = logging.getLogger(__name__)

CANONICAL_STRATEGY_KEYS = {
    "settlement_capture",
    "shoulder_sell",
    "center_buy",
    "opening_inertia",
}

POSITIONS_PATH = state_path("positions.json")


@dataclass
class ExitDecision:
    """Result of Position.evaluate_exit()."""
    should_exit: bool
    reason: str = ""
    urgency: str = "normal"  # "normal" or "immediate"
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)
    trigger: str = ""


@dataclass(frozen=True)
class ExitContext:
    """Unified runtime authority surface for exit evaluation + execution.

    `evaluate_exit()` consumes this object instead of scattered optional params.
    Some surfaces are required for authority (`fresh_prob`, `current_market_price`,
    `hours_to_settlement`, `position_state`). Others may be explicitly
    unavailable (`best_bid`, `best_ask`, `market_vig`, `whale_toxicity`) and
    must be represented as such instead of silently omitted.
    """

    exit_reason: str = ""
    fresh_prob: Optional[float] = None
    fresh_prob_is_fresh: bool = False
    current_market_price: Optional[float] = None
    current_market_price_is_fresh: bool = False
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    market_vig: Optional[float] = None
    hours_to_settlement: Optional[float] = None
    position_state: str = ""
    day0_active: bool = False
    whale_toxicity: Optional[bool] = None
    chain_is_fresh: Optional[bool] = None
    divergence_score: float = 0.0
    market_velocity_1h: float = 0.0

    @staticmethod
    def _is_finite(value: Optional[float]) -> bool:
        if value is None:
            return False
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def missing_authority_fields(self) -> list[str]:
        missing: list[str] = []
        if not self._is_finite(self.fresh_prob):
            missing.append("fresh_prob")
        elif not self.fresh_prob_is_fresh and not self.day0_active:
            # Day0 positions waive fresh_prob_is_fresh requirement
            missing.append("fresh_prob_is_fresh")
            missing.append("fresh_prob_is_fresh")
        if not self._is_finite(self.current_market_price):
            missing.append("current_market_price")
        elif not self.current_market_price_is_fresh:
            missing.append("current_market_price_is_fresh")
        if not self._is_finite(self.hours_to_settlement):
            missing.append("hours_to_settlement")
        if not self.position_state:
            missing.append("position_state")
        return missing


# Administrative exit reasons — excluded from P&L calculations
ADMIN_EXITS = frozenset({
    "GHOST_DUPLICATE", "PHANTOM_NOT_ON_CHAIN",
    "UNFILLED_ORDER", "SETTLED_NOT_IN_API", "EXIT_FAILED",
    "SETTLED_UNKNOWN_DIRECTION", "EXIT_CHAIN_MISSING_REVIEW_REQUIRED",
})


@dataclass
class Position:
    """A held trading position — stateful entity that owns its exit logic.

    INVARIANT: p_posterior and entry_price are ALWAYS in the native space of the
    direction. For buy_yes: P(YES) and YES market price. For buy_no: P(NO) and NO
    market price. This invariant is established once at entry and never flipped.

    Position knows HOW to exit itself. Monitor just calls evaluate_exit().
    """
    # Identity (immutable after creation)
    trade_id: str
    market_id: str
    city: str
    cluster: str
    target_date: str
    bin_label: str
    direction: DirectionAlias  # Forces use of Direction(Enum)

    unit: str = "F"  # Blueprint v2: carried, never inferred

    # Provenance: which environment created this position (set once, never changed)
    env: str = "live"  # live-only (Phase 1 axiom)

    # Probability (always in held-side space — flipped exactly once at creation)
    size_usd: float = 0.0
    entry_price: float = 0.0  # Native space
    p_posterior: float = 0.0  # Native space (p_held_side in blueprint)
    edge: float = 0.0
    shares: float = 0.0  # size_usd / entry_price
    cost_basis_usd: float = 0.0  # = size_usd
    bankroll_at_entry: float = 150.0
    entered_at: str = ""
    day0_entered_at: str = ""
    entry_ci_width: float = 0.0

    # Entry context (immutable snapshot — Blueprint v2 §2)
    entry_method: str = "ens_member_counting"
    signal_version: str = "v2"
    calibration_version: str = ""
    decision_snapshot_id: str = ""  # FK to ensemble_snapshots at decision time
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)

    # Strategy + attribution
    strategy_key: str = ""
    strategy: str = ""  # "settlement_capture" | "shoulder_sell" | "center_buy" | "opening_inertia"
    edge_source: str = ""
    discovery_mode: str = ""
    market_hours_open: float = 0.0
    fill_quality: float = 0.0  # (exec_price - vwmp) / vwmp

    # Lifecycle state (Blueprint v2 §2)
    state: str = LifecycleState.HOLDING.value
    exit_strategy: str = ""  # "buy_yes_standard" | "buy_no_conservative" (set from direction)
    order_id: str = ""
    order_status: str = ""
    order_posted_at: str = ""
    order_timeout_at: str = ""

    # Chain reconciliation (Blueprint v2 §5)
    chain_state: str = ChainState.UNKNOWN.value
    chain_shares: float = 0.0
    chain_verified_at: str = ""

    # Token IDs for CLOB orderbook queries
    token_id: str = ""
    no_token_id: str = ""
    condition_id: str = ""

    # Quarantine tracking
    quarantined_at: str = ""  # ISO timestamp when quarantined

    # Exit state (persisted across monitor cycles — Blueprint v2 §7)
    neg_edge_count: int = 0
    last_monitor_prob: float = 0.0
    last_monitor_prob_is_fresh: bool = False
    last_monitor_edge: float = 0.0
    last_monitor_market_price: Optional[float] = None
    last_monitor_market_price_is_fresh: bool = False
    last_monitor_best_bid: Optional[float] = None
    last_monitor_best_ask: Optional[float] = None
    last_monitor_market_vig: Optional[float] = None
    last_monitor_whale_toxicity: Optional[bool] = None
    last_monitor_at: str = ""

    # Live exit lifecycle (sell order state machine)
    exit_state: str = ""  # "" | "exit_intent" | "sell_placed" | "sell_pending" |
                          #   "sell_filled" | "retry_pending" | "backoff_exhausted"
    pre_exit_state: str = ""  # authoritative runtime state before pending_exit
    exit_retry_count: int = 0
    next_exit_retry_at: Optional[str] = None  # ISO timestamp for retry cooldown
    last_exit_order_id: Optional[str] = None  # for stale cancel on retry
    last_exit_error: str = ""

    # Entry fill verification (live mode)
    entry_order_id: Optional[str] = None  # CLOB order ID from entry
    entry_fill_verified: bool = False  # True only after CLOB confirms MATCHED/FILLED

    # Anti-churn
    last_exit_at: str = ""
    exit_trigger: str = ""
    exit_reason: str = ""
    admin_exit_reason: str = ""  # Separate from economic exit_reason
    exit_divergence_score: float = 0.0
    exit_market_velocity_1h: float = 0.0
    exit_forward_edge: float = 0.0

    # JSON Object Snapshots (Phase 2 Object Persistence DTO)
    settlement_semantics_json: Optional[str] = None
    epistemic_context_json: Optional[str] = None
    edge_context_json: Optional[str] = None

    # P&L (set on close)
    exit_price: float = 0.0
    pnl: float = 0.0

    def __post_init__(self):
        """CRITICAL: Enforce Enum strictness via coercion."""
        if not isinstance(self.direction, Direction):
            self.direction = Direction(self.direction)
        if not isinstance(self.state, LifecycleState):
            self.state = LifecycleState(self.state)
        if not isinstance(self.chain_state, ChainState):
            self.chain_state = ChainState(self.chain_state)
        if not isinstance(self.exit_state, ExitState):
            self.exit_state = ExitState(self.exit_state)
        if self.pre_exit_state:
            self.pre_exit_state = LifecycleState(self.pre_exit_state).value


    @property
    def effective_shares(self) -> float:
        if self.shares > 0:
            return self.shares
        if self.entry_price > 0:
            return self.size_usd / self.entry_price
        return 0.0

    @property
    def effective_cost_basis_usd(self) -> float:
        return self.cost_basis_usd if self.cost_basis_usd > 0 else self.size_usd

    @property
    def unrealized_pnl(self) -> float:
        """Mark-to-market P&L based on the last known native-space market price."""
        if self.last_monitor_market_price is None:
            return 0.0
        return self.effective_shares * self.last_monitor_market_price - self.effective_cost_basis_usd

    def evaluate_exit(self, exit_context: ExitContext) -> ExitDecision:
        """Position knows how to exit ITSELF. Monitor just calls this.

        All probabilities remain in held/native space. Missing authority fields
        fail closed with an explicit incomplete verdict.
        """
        applied = list(self.applied_validations)
        missing = exit_context.missing_authority_fields()
        if missing:
            applied.append("exit_context_incomplete")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                f"INCOMPLETE_EXIT_CONTEXT (missing={','.join(missing)})",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )

        if exit_context.best_bid is None:
            applied.append("best_bid_unavailable")
        if exit_context.best_ask is None:
            applied.append("best_ask_unavailable")
        if exit_context.market_vig is None:
            applied.append("market_vig_unavailable")
        if exit_context.whale_toxicity is None:
            applied.append("whale_toxicity_unavailable")
        elif exit_context.whale_toxicity:
            applied.append("whale_toxicity_available")
        if exit_context.chain_is_fresh is None:
            applied.append("chain_freshness_unavailable")
        elif exit_context.chain_is_fresh is False:
            applied.append("chain_freshness_stale")

        forward_edge = compute_forward_edge(
            HeldSideProbability(float(exit_context.fresh_prob), self.direction),
            NativeSidePrice(float(exit_context.current_market_price), self.direction),
        )
        applied.append("forward_edge_compute")

        if exit_context.day0_active:
            applied.append("day0_observation_authority")
            # Day0 stale prob waiver: when fresh_prob_is_fresh=False, log the
            # authority waiver and note the substitution for auditability
            if not exit_context.fresh_prob_is_fresh:
                applied.append("day0_stale_prob_authority_waived")
                applied.append("stale_prob_substitution")
            if self.direction == "buy_no":
                day0_decision = self._buy_no_exit(
                    forward_edge,
                    current_p_posterior=float(exit_context.fresh_prob),
                    current_market_price=float(exit_context.current_market_price),
                    hours_to_settlement=exit_context.hours_to_settlement,
                    day0_active=True,
                    applied=applied,
                )
            else:
                day0_decision = self._buy_yes_exit(
                    forward_edge,
                    current_p_posterior=float(exit_context.fresh_prob),
                    best_bid=exit_context.best_bid,
                    day0_active=True,
                    applied=applied,
                )
            if day0_decision.should_exit:
                return day0_decision
            # Don't return False here — fall through to SETTLEMENT_IMMINENT
            # and other force-exit checks (whale toxicity, edge reversal).
            # Without this fallthrough, positions with expired target_dates
            # loop forever in day0_window.
            applied = list(day0_decision.applied_validations or applied)

        # Settlement imminent
        if exit_context.hours_to_settlement is not None and exit_context.hours_to_settlement < 1.0:
            applied.append("near_settlement_gate")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True, "SETTLEMENT_IMMINENT", "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="SETTLEMENT_IMMINENT",
            )

        # Whale toxicity
        if exit_context.whale_toxicity:
            applied.append("whale_toxicity_gate")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True, "WHALE_TOXICITY", "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="WHALE_TOXICITY",
            )

        if exit_context.divergence_score >= divergence_hard_threshold():
            applied.append("divergence_hard_trigger")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                f"MODEL_DIVERGENCE_PANIC (score={exit_context.divergence_score:.2f})",
                "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="MODEL_DIVERGENCE_PANIC",
            )

        if (
            exit_context.divergence_score >= divergence_soft_threshold()
            and exit_context.market_velocity_1h <= divergence_velocity_confirm()
        ):
            applied.append("divergence_soft_trigger")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                (
                    "MODEL_DIVERGENCE_PANIC "
                    f"(score={exit_context.divergence_score:.2f}, velocity={exit_context.market_velocity_1h:.2f}/hr)"
                ),
                "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="MODEL_DIVERGENCE_PANIC",
            )

        if exit_context.market_velocity_1h <= -0.15:
            applied.append("flash_crash_trigger")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                f"FLASH_CRASH_PANIC (velocity={exit_context.market_velocity_1h:.2f}/hr)",
                "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="FLASH_CRASH_PANIC",
            )

        # Micro-position hold (Layer 8: < $1 never sold)
        if self.size_usd < 1.0:
            applied.append("micro_position_hold")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )

        # Vig extreme
        if (
            exit_context.market_vig is not None
            and ExitContext._is_finite(exit_context.market_vig)
            and (exit_context.market_vig > 1.08 or exit_context.market_vig < 0.92)
        ):
            applied.append("vig_extreme_gate")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True, f"VIG_EXTREME (vig={exit_context.market_vig:.3f})",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="VIG_EXTREME",
            )

        # Direction-specific exit logic
        if self.direction == "buy_no":
            return self._buy_no_exit(
                forward_edge,
                current_p_posterior=float(exit_context.fresh_prob),
                current_market_price=float(exit_context.current_market_price),
                hours_to_settlement=exit_context.hours_to_settlement,
                day0_active=bool(exit_context.day0_active),
                applied=applied,
            )
        else:
            return self._buy_yes_exit(
                forward_edge,
                current_p_posterior=float(exit_context.fresh_prob),
                best_bid=exit_context.best_bid,
                day0_active=bool(exit_context.day0_active),
                applied=applied,
            )

    def _buy_yes_exit(
        self,
        forward_edge: float,
        current_p_posterior: float,
        best_bid: Optional[float] = None,
        day0_active: bool = False,
        applied: Optional[list[str]] = None,
    ) -> ExitDecision:
        """Standard 2-consecutive EDGE_REVERSAL with EV gate."""
        applied = list(applied or [])
        if best_bid is None:
            applied.append("exit_context_incomplete")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )
        evidence_edge = conservative_forward_edge(forward_edge, self.entry_ci_width)
        edge_threshold = buy_yes_edge_threshold(self.entry_ci_width)
        applied.append("ci_threshold")
        if day0_active and evidence_edge < edge_threshold:
            applied.append("day0_observation_gate")
            applied.append("ev_gate")
            shares = self.size_usd / self.entry_price if self.entry_price > 0 else 0.0
            if shares * best_bid <= shares * current_p_posterior:
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                )
            self.neg_edge_count = 0
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                f"DAY0_OBSERVATION_REVERSAL (ci_lower={evidence_edge:.4f}, point={forward_edge:.4f})",
                "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="DAY0_OBSERVATION_REVERSAL",
            )
        applied.append("consecutive_cycle_check")
        if evidence_edge >= edge_threshold:
            self.neg_edge_count = 0
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )

        self.neg_edge_count += 1
        if self.neg_edge_count < consecutive_confirmations():
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )

        # Layer 4: EV gate
        if best_bid is not None and self.entry_price > 0:
            applied.append("ev_gate")
            shares = self.size_usd / self.entry_price
            if shares * best_bid <= shares * current_p_posterior:
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                )

        self.neg_edge_count = 0
        self.applied_validations = _dedupe_validations(applied)
        return ExitDecision(
            True, f"EDGE_REVERSAL (ci_lower={evidence_edge:.4f}, point={forward_edge:.4f})",
            selected_method=self.selected_method or self.entry_method,
            applied_validations=list(self.applied_validations),
            trigger="EDGE_REVERSAL",
        )

    def _buy_no_exit(
        self,
        forward_edge: float,
        current_p_posterior: float,
        current_market_price: float,
        hours_to_settlement: Optional[float] = None,
        day0_active: bool = False,
        applied: Optional[list[str]] = None,
    ) -> ExitDecision:
        """Layer 1: Buy-no has ~87.5% base win rate. Different exit math."""
        applied = list(applied or [])
        evidence_edge = conservative_forward_edge(forward_edge, self.entry_ci_width)
        edge_threshold = buy_no_edge_threshold(self.entry_ci_width)
        near_threshold = buy_no_ceiling()
        applied.append("ci_threshold")

        if day0_active and evidence_edge < edge_threshold:
            applied.append("day0_observation_gate")
            if self.entry_price > 0:
                applied.append("ev_gate")
                shares = self.size_usd / self.entry_price
                if shares * current_market_price <= shares * current_p_posterior:
                    self.applied_validations = _dedupe_validations(applied)
                    return ExitDecision(
                        False,
                        selected_method=self.selected_method or self.entry_method,
                        applied_validations=list(self.applied_validations),
                    )
            self.neg_edge_count = 0
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                f"DAY0_OBSERVATION_REVERSAL (ci_lower={evidence_edge:.4f}, point={forward_edge:.4f})",
                "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="DAY0_OBSERVATION_REVERSAL",
            )

        # Near-settlement hold (unless deeply negative)
        if hours_to_settlement is not None and hours_to_settlement < near_settlement_hours():
            applied.append("near_settlement_gate")
            if forward_edge < near_threshold:
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    True, f"BUY_NO_NEAR_EXIT (point={forward_edge:.4f})",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="BUY_NO_NEAR_EXIT",
                )
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )

        applied.append("consecutive_cycle_check")
        if evidence_edge < edge_threshold:
            self.neg_edge_count += 1
        else:
            self.neg_edge_count = 0

        if self.neg_edge_count >= consecutive_confirmations():
            if self.entry_price > 0:
                applied.append("ev_gate")
                shares = self.size_usd / self.entry_price
                if shares * current_market_price <= shares * current_p_posterior:
                    self.applied_validations = _dedupe_validations(applied)
                    return ExitDecision(
                        False,
                        selected_method=self.selected_method or self.entry_method,
                        applied_validations=list(self.applied_validations),
                    )
            self.neg_edge_count = 0
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True, f"BUY_NO_EDGE_EXIT (ci_lower={evidence_edge:.4f}, point={forward_edge:.4f})",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="BUY_NO_EDGE_EXIT",
            )

        self.applied_validations = _dedupe_validations(applied)
        return ExitDecision(
            False,
            selected_method=self.selected_method or self.entry_method,
            applied_validations=list(self.applied_validations),
        )

    @property
    def is_admin_exit(self) -> bool:
        return (self.admin_exit_reason != ""
                or self.exit_reason in ADMIN_EXITS)


@dataclass
class PortfolioState:
    positions: list[Position] = field(default_factory=list)
    bankroll: float = 150.0
    updated_at: str = ""
    audit_logging_enabled: bool = False
    daily_baseline_total: float = 150.0
    weekly_baseline_total: float = 150.0
    # Layer 5+6: recently closed positions for reentry/cooldown checks
    recent_exits: list[dict] = field(default_factory=list)
    # T2-C: Tokens to never resurrect (redeemed, expired, manually closed)
    ignored_tokens: list[str] = field(default_factory=list)
    # P4 (Tier 2.1): when True, DB projection failed and portfolio is empty.
    # Cycle runner must suppress new entries when this flag is set.
    portfolio_loader_degraded: bool = False

    @property
    def initial_bankroll(self) -> float:
        return self.bankroll




class DeprecatedStateFileError(RuntimeError):
    """Raised when a deprecated unsuffixed truth file is accessed."""
    pass


# Bug #7 fix: terminal lifecycle states must never enter the active-positions
# runtime view. save_portfolio and the JSON-fallback load path both filter
# these out. This is defence-in-depth on top of the K1 structural direction
# (derived surfaces should not write at all) — if any code path accidentally
# leaves a settled row in state.positions, the write-side filter strips it;
# if a stale JSON file lingers on disk, the read-side filter strips it.
_TERMINAL_POSITION_STATES = frozenset({
    "settled",
    "voided",
    "admin_closed",
    "quarantined",
})


def _load_portfolio_json_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    if data.get("truth", {}).get("deprecated") is True:
        raise DeprecatedStateFileError(
            f"{path} is a deprecated legacy truth file. "
            "Use the mode-suffixed positions file instead."
        )
    return data


def _guard_deprecated_portfolio_json(path: Path) -> None:
    try:
        _load_portfolio_json_payload(path)
    except DeprecatedStateFileError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Ignoring unreadable derived portfolio JSON sidecar while DB authority is unavailable: %s",
            exc,
        )


def _load_portfolio_from_json_data(data: dict, *, current_mode: str) -> PortfolioState:
    position_fields = {f.name for f in fields(Position)}
    positions = []
    skipped_terminal: list[str] = []
    for p in data.get("positions", []):
        # Bug #7 read-side filter: terminal-state rows must never reach
        # runtime portfolio state from a JSON fallback. If position_current
        # is healthy, the canonical DB-first path is used and this loader
        # is never called. If it IS called (partial_stale, missing_table,
        # DB error), the JSON on disk could still contain settled rows
        # from a prior save_portfolio write that predates the write-side
        # filter. Strip them here and log which were skipped.
        raw_state = str(p.get("state", "") or "").strip().lower()
        if raw_state in _TERMINAL_POSITION_STATES:
            skipped_terminal.append(
                f"{p.get('trade_id', '?')}({raw_state})"
            )
            continue
        filtered = {k: v for k, v in p.items() if k in position_fields}
        if "env" not in p:
            filtered["env"] = current_mode
        pos = Position(**filtered)
        if not pos.strategy_key and pos.strategy in CANONICAL_STRATEGY_KEYS:
            pos.strategy_key = pos.strategy
        if pos.strategy_key and pos.strategy and pos.strategy_key != pos.strategy:
            raise RuntimeError(
                f"strategy_key mismatch for {pos.trade_id}: "
                f"strategy_key={pos.strategy_key}, strategy={pos.strategy}"
            )
        positions.append(pos)

    if skipped_terminal:
        logger.warning(
            "_load_portfolio_from_json_data skipped %d terminal-state rows "
            "from JSON fallback (bug #7 defense): %s",
            len(skipped_terminal),
            ", ".join(skipped_terminal[:10]) + ("..." if len(skipped_terminal) > 10 else ""),
        )

    bankroll = float(settings.capital_base_usd)
    return PortfolioState(
        positions=positions,
        bankroll=bankroll,
        updated_at="",
        audit_logging_enabled=True,
        daily_baseline_total=bankroll,
        weekly_baseline_total=bankroll,
        recent_exits=[],
        ignored_tokens=[],
    )


def _runtime_state_for_portfolio_phase(phase: str) -> str:
    if phase == "pending_entry":
        return "pending_tracked"
    if phase == "day0_window":
        return "day0_window"
    if phase == "pending_exit":
        return "pending_exit"
    if phase == "active":
        return "entered"
    raise ValueError(f"unsupported canonical phase for portfolio loader: {phase!r}")


def _position_from_projection_row(row: dict, *, current_mode: str) -> Position:
    state = str(row.get("state") or "")
    if not state:
        state = _runtime_state_for_portfolio_phase(str(row.get("phase") or ""))
    entered_at = str(row.get("entered_at") or row.get("updated_at") or "")
    order_posted_at = str(row.get("order_posted_at") or entered_at or "")
    day0_entered_at = str(row.get("day0_entered_at") or "") if state == "day0_window" else ""
    payload = dict(
        trade_id=str(row.get("trade_id") or row.get("position_id") or ""),
        market_id=str(row.get("market_id") or ""),
        city=str(row.get("city") or ""),
        cluster=str(row.get("cluster") or ""),
        target_date=str(row.get("target_date") or ""),
        bin_label=str(row.get("bin_label") or ""),
        direction=str(row.get("direction") or "unknown"),
        unit=str(row.get("unit") or "F"),
        env=current_mode,
        size_usd=float(row.get("size_usd") or 0.0),
        shares=float(row.get("shares") or 0.0),
        cost_basis_usd=float(row.get("cost_basis_usd") or 0.0),
        entry_price=float(row.get("entry_price") or 0.0),
        p_posterior=float(row.get("p_posterior") or 0.0),
        entered_at=entered_at if state != "pending_tracked" else "",
        day0_entered_at=day0_entered_at,
        decision_snapshot_id=str(row.get("decision_snapshot_id") or ""),
        entry_method=str(row.get("entry_method") or ""),
        strategy_key=str(row.get("strategy_key") or ""),
        strategy=str(row.get("strategy") or row.get("strategy_key") or ""),
        edge_source=str(row.get("edge_source") or ""),
        discovery_mode=str(row.get("discovery_mode") or ""),
        state=state,
        order_id=str(row.get("order_id") or ""),
        order_status=str(row.get("order_status") or ""),
        order_posted_at=order_posted_at,
        chain_state=str(row.get("chain_state") or ""),
        exit_state=str(row.get("exit_state") or ""),
        last_monitor_prob=row.get("last_monitor_prob"),
        last_monitor_edge=row.get("last_monitor_edge"),
        last_monitor_market_price=row.get("last_monitor_market_price"),
        admin_exit_reason=str(row.get("admin_exit_reason") or ""),
        entry_fill_verified=bool(row.get("entry_fill_verified", False)),
    )
    for field_name in {f.name for f in fields(Position)}:
        if field_name in payload:
            continue
        value = row.get(field_name)
        if value not in (None, "", [], {}, 0, 0.0):
            payload[field_name] = value
    return Position(**payload)


def _canonical_recent_exits_from_settlement_rows(rows: list[dict]) -> list[dict]:
    exits: list[dict] = []
    for row in rows:
        pnl = row.get("pnl")
        if pnl is None:
            continue
        exits.append(
            {
                "city": str(row.get("city") or ""),
                "bin_label": str(row.get("range_label") or row.get("winning_bin") or ""),
                "target_date": str(row.get("target_date") or ""),
                "direction": str(row.get("direction") or ""),
                "token_id": "",
                "no_token_id": "",
                "exit_reason": str(row.get("exit_reason") or "SETTLEMENT"),
                "exited_at": str(row.get("exited_at") or row.get("settled_at") or ""),
                "pnl": float(pnl),
            }
        )
    return exits


def load_portfolio(path: Optional[Path] = None) -> PortfolioState:
    """Load portfolio DB-first, with explicit JSON fallback only when projection is unavailable."""
    path = path or POSITIONS_PATH

    current_mode = get_mode()
    bankroll = float(settings.capital_base_usd)

    from src.state.db import (
        get_connection,
        get_trade_connection_with_world,
        query_authoritative_settlement_rows,
        query_portfolio_loader_view,
        query_token_suppression_tokens,
    )

    mode_override = None
    stem = path.stem
    if stem.startswith("positions-"):
        candidate_mode = stem.split("positions-", 1)[1]
        if candidate_mode in {"live", "test"}:  # paper removed (Phase 1 axiom)
            mode_override = candidate_mode
    elif path == POSITIONS_PATH:
        mode_override = current_mode

    try:
        trade_db = path.parent / "zeus_trades.db"
        if trade_db.exists():
            conn = get_connection(trade_db)
        elif mode_override is not None:
            conn = get_trade_connection_with_world()
        else:
            conn = get_connection(path.parent / "zeus.db")
    except Exception:
        logger.error(
            "load_portfolio DB connection failed; returning empty portfolio (entries suppressed this cycle)",
            exc_info=True,
        )
        _guard_deprecated_portfolio_json(path)
        return PortfolioState(
            positions=[],
            bankroll=bankroll,
            daily_baseline_total=bankroll,
            weekly_baseline_total=bankroll,
            portfolio_loader_degraded=True,
        )

    settlement_rows: list[dict] = []
    ignored_tokens: list[str] = []
    try:
        snapshot = query_portfolio_loader_view(conn)
        ignored_tokens = query_token_suppression_tokens(conn)
        if snapshot.get("status") in ("ok", "partial_stale", "empty"):
            try:
                settlement_rows = query_authoritative_settlement_rows(
                    conn,
                    limit=None,
                    env="live",
                )
            except Exception:
                logger.warning(
                    "load_portfolio could not load canonical recent exits; using empty DB-first recent_exits",
                    exc_info=True,
                )
                settlement_rows = []
    finally:
        conn.close()

    policy = choose_portfolio_truth_source(snapshot.get("status"))
    if policy.source != "canonical_db":
        logger.error(
            "load_portfolio DB projection not authoritative: %s (%s); returning empty portfolio (entries suppressed)",
            snapshot.get("status"),
            policy.reason,
        )
        _guard_deprecated_portfolio_json(path)
        return PortfolioState(
            positions=[],
            bankroll=bankroll,
            daily_baseline_total=bankroll,
            weekly_baseline_total=bankroll,
            ignored_tokens=ignored_tokens,
            portfolio_loader_degraded=True,
        )

    positions = [
        _position_from_projection_row(
            row,
            current_mode=current_mode,
        )
        for row in snapshot.get("positions", [])
    ]
    return PortfolioState(
        positions=positions,
        bankroll=bankroll,
        updated_at="",
        audit_logging_enabled=True,
        daily_baseline_total=bankroll,
        weekly_baseline_total=bankroll,
        recent_exits=_canonical_recent_exits_from_settlement_rows(settlement_rows),
        ignored_tokens=ignored_tokens,
    )


def save_portfolio(state: PortfolioState, path: Optional[Path] = None) -> None:
    """Atomic write: write to tmp, then os.replace(). Spec: atomic write pattern."""
    path = path or POSITIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    state.updated_at = datetime.now(timezone.utc).isoformat()
    # Bug #7 write-side filter: positions-{mode}.json is an "active positions
    # view" file — terminal-state rows must not appear. If a terminal row
    # somehow remains in state.positions (e.g. an event path appended to
    # the list without popping), strip it here. This is defence-in-depth
    # on top of the explicit pop() calls in compute_settlement_close,
    # mark_admin_closed, and void_position. Settled rows belong in
    # position_events / position_current, not in this cache file.
    active_positions = [
        p for p in state.positions
        if str(getattr(p, "state", "") or "").strip().lower() not in _TERMINAL_POSITION_STATES
    ]
    data = {
        "positions": [asdict(p) for p in active_positions],
        "bankroll": state.bankroll,
        "updated_at": state.updated_at,
        "daily_baseline_total": state.daily_baseline_total,
        "weekly_baseline_total": state.weekly_baseline_total,
        "recent_exits": state.recent_exits,
        "ignored_tokens": state.ignored_tokens,
    }
    data = annotate_truth_payload(data, path, mode=get_mode(), generated_at=state.updated_at)

    # Atomic write pattern per OpenClaw conventions
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
    except Exception:
        os.unlink(tmp_path)
        raise


def add_position(state: PortfolioState, pos: Position) -> None:
    """Add a position. Dedup: merge if same token+direction already open."""
    if pos.shares <= 0 and pos.entry_price > 0:
        pos.shares = pos.size_usd / pos.entry_price
    if pos.cost_basis_usd <= 0:
        pos.cost_basis_usd = pos.size_usd

    for existing in state.positions:
        if pos.order_id and existing.order_id and pos.order_id == existing.order_id:
            for field_name, value in asdict(pos).items():
                setattr(existing, field_name, value)
            return

    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    for existing in state.positions:
        if pos.state == "pending_tracked" or existing.state == "pending_tracked":
            continue
        existing_tid = existing.token_id if existing.direction == "buy_yes" else existing.no_token_id
        if tid and existing_tid == tid and existing.direction == pos.direction:
            # Merge: accumulate shares and cost
            logger.warning("DEDUP: merging duplicate %s %s into existing %s",
                           pos.direction, pos.bin_label, existing.trade_id)
            existing.size_usd += pos.size_usd
            existing.shares += pos.effective_shares
            existing.cost_basis_usd += pos.effective_cost_basis_usd
            return
    state.positions.append(pos)


def _dedupe_validations(steps: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for step in steps:
        if step and step not in seen:
            seen.add(step)
            ordered.append(step)
    return ordered


INACTIVE_RUNTIME_STATES = frozenset({"voided", "settled", "economically_closed", "quarantined", "admin_closed"})


def _compute_realized_pnl(position: Position, exit_price: float) -> float:
    if position.entry_price <= 0:
        return 0.0
    return round(position.effective_shares * exit_price - position.effective_cost_basis_usd, 2)


def compute_economic_close(
    state: PortfolioState,
    trade_id: str,
    exit_price: float,
    exit_reason: str,
) -> Optional[Position]:
    """Mark a position economically closed without removing it from runtime truth."""

    now = datetime.now(timezone.utc).isoformat()
    for pos in state.positions:
        if pos.trade_id != trade_id:
            continue
        pos.state = enter_economically_closed_runtime_state(
            pos.state,
            exit_state=getattr(pos, "exit_state", ""),
            chain_state=getattr(pos, "chain_state", ""),
        )
        pos.pre_exit_state = ""
        pos.exit_price = exit_price
        pos.exit_reason = exit_reason
        pos.last_exit_at = now
        pos.pnl = _compute_realized_pnl(pos, exit_price)
        _track_exit(state, pos)
        return pos
    return None


def compute_settlement_close(
    state: PortfolioState,
    trade_id: str,
    settlement_price: float,
    exit_reason: str = "SETTLEMENT",
) -> Optional[Position]:
    """Finalize settlement and remove the position from active runtime truth."""

    now = datetime.now(timezone.utc).isoformat()
    closed = None

    for pos_ref in list(state.positions):
        if pos_ref.trade_id != trade_id:
            continue
        pos = state.positions.pop(state.positions.index(pos_ref))
        was_economically_closed = pos.state == "economically_closed"
        pos.state = enter_settled_runtime_state(
            pos.state,
            exit_state=getattr(pos, "exit_state", ""),
            chain_state=getattr(pos, "chain_state", ""),
        )
        pos.pre_exit_state = ""
        pos.last_exit_at = now
        pos.exit_reason = exit_reason
        if not was_economically_closed:
            pos.exit_price = settlement_price
            pos.pnl = _compute_realized_pnl(pos, settlement_price)
            _track_exit(state, pos)
        closed = pos

    return closed


def close_position(
    state: PortfolioState, trade_id: str,
    exit_price: float, exit_reason: str,
) -> Optional[Position]:
    """Legacy settlement terminalizer. Delegates to compute_settlement_close."""
    return compute_settlement_close(state, trade_id, exit_price, exit_reason)


def mark_admin_closed(
    state: PortfolioState,
    trade_id: str,
    reason: str,
) -> Optional[Position]:
    """Remove a position into an explicit admin_closed terminal state."""

    for i, p in enumerate(state.positions):
        if p.trade_id == trade_id:
            pos = state.positions.pop(i)
            pos.state = enter_admin_closed_runtime_state(
                pos.state,
                exit_state=getattr(pos, "exit_state", ""),
                chain_state=getattr(pos, "chain_state", ""),
            )
            pos.pre_exit_state = ""
            pos.admin_exit_reason = reason
            pos.exit_reason = reason
            pos.last_exit_at = datetime.now(timezone.utc).isoformat()
            _track_exit(state, pos)
            return pos
    return None


def void_position(
    state: PortfolioState, trade_id: str, reason: str,
) -> Optional[Position]:
    """Close with pnl=0 when real exit price is unknown. L3.

    Use for: UNFILLED_ORDER, SETTLED_NOT_IN_API, EXIT_FAILED.
    Does NOT affect loss counters (admin exit).
    """
    for i, p in enumerate(state.positions):
        if p.trade_id == trade_id:
            pos = state.positions.pop(i)
            pos.state = enter_voided_runtime_state(
                pos.state,
                exit_state=getattr(pos, "exit_state", ""),
                chain_state=getattr(pos, "chain_state", ""),
            )
            pos.exit_reason = reason
            pos.exit_price = 0.0
            pos.pnl = 0.0
            pos.last_exit_at = datetime.now(timezone.utc).isoformat()
            _track_exit(state, pos)
            return pos
    return None


def remove_position(
    state: PortfolioState, trade_id: str, exit_reason: str = ""
) -> Optional[Position]:
    """Legacy remove. Delegates to close_position with entry_price as exit."""
    for p in state.positions:
        if p.trade_id == trade_id:
            return close_position(state, trade_id, p.entry_price, exit_reason)
    return None


def _track_exit(state: PortfolioState, pos: Position) -> None:
    """Track exit for reentry/cooldown checks AND replay auditability.

    CRITICAL: All fields required by profit_validation_replay.py must be
    persisted here. If a field is on Position but not in this dict, the
    replay engine will classify the exit as 'fully_skipped'.

    This list is intentionally unbounded. Truncating exit history makes
    realized PnL, weekly/daily loss checks, and future full-fidelity replay
    depend on arbitrary retention rather than actual trading history.
    """
    state.recent_exits.append({
        # Identity
        "trade_id": pos.trade_id,
        "market_id": pos.market_id,
        "city": pos.city,
        "cluster": pos.cluster,
        "bin_label": pos.bin_label,
        "target_date": pos.target_date,
        "direction": pos.direction,
        "token_id": pos.token_id,
        "no_token_id": pos.no_token_id,
        # Entry context (replay-critical)
        "entry_price": pos.entry_price,
        "size_usd": pos.size_usd,
        "p_posterior": pos.p_posterior,
        "edge": pos.edge,
        "entry_ci_width": pos.entry_ci_width,
        "entry_method": pos.entry_method,
        "selected_method": pos.selected_method,
        "applied_validations": list(pos.applied_validations),
        "decision_snapshot_id": pos.decision_snapshot_id,
        "entered_at": pos.entered_at,
        # Strategy attribution
        "strategy_key": pos.strategy_key,
        "strategy": pos.strategy,
        "edge_source": pos.edge_source,
        "discovery_mode": pos.discovery_mode,
        "market_hours_open": pos.market_hours_open,
        "fill_quality": pos.fill_quality,
        "settlement_semantics_json": pos.settlement_semantics_json,
        "epistemic_context_json": pos.epistemic_context_json,
        "edge_context_json": pos.edge_context_json,
        # Exit context
        "exit_trigger": pos.exit_trigger,
        "exit_reason": pos.exit_reason,
        "admin_exit_reason": pos.admin_exit_reason,
        "exit_divergence_score": pos.exit_divergence_score,
        "exit_market_velocity_1h": pos.exit_market_velocity_1h,
        "exit_forward_edge": pos.exit_forward_edge,
        "exit_price": pos.exit_price,
        "exited_at": pos.last_exit_at,
    })

    if state.audit_logging_enabled:
        try:
            from src.state.db import get_connection, log_trade_exit
            conn = get_connection()
            log_trade_exit(conn, pos)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Error logging trade exit to db: %s", e)



def get_open_positions(state: PortfolioState, chain_view=None) -> list[Position]:
    """T2-E: Chain-journal merge for live position queries.

    Paper mode or no chain_view: return local positions only.
    Live mode with chain_view: merge chain truth (shares/price) with
    local metadata (city, range, direction, decision context).
    """
    if chain_view is None or getattr(chain_view, "is_stale", True):
        return [p for p in state.positions if p.state not in INACTIVE_RUNTIME_STATES]

    merged = []
    for pos in state.positions:
        if pos.state in INACTIVE_RUNTIME_STATES:
            continue

        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        chain_pos = chain_view.get_position(tid) if tid else None

        if chain_pos:
            # Chain overrides size/price, local keeps metadata
            pos.shares = chain_pos.size
            if chain_pos.avg_price > 0:
                pos.entry_price = chain_pos.avg_price
            pos.chain_state = "synced"
            merged.append(pos)
        elif pos.state == "pending_tracked":
            merged.append(pos)  # Just placed, chain hasn't indexed yet
        # else: gone from chain — reconciler will handle

    return merged


def total_exposure_usd(state: PortfolioState) -> float:
    """Total open exposure in USD."""
    return sum(p.size_usd for p in state.positions if p.state not in INACTIVE_RUNTIME_STATES)


def portfolio_heat_for_bankroll(state: PortfolioState, bankroll: float) -> float:
    """Portfolio heat against an explicit entry bankroll/cap."""
    if bankroll <= 0:
        return 0.0
    return total_exposure_usd(state) / bankroll


def city_exposure_for_bankroll(state: PortfolioState, city: str, bankroll: float) -> float:
    """City exposure against an explicit entry bankroll/cap."""
    if bankroll <= 0:
        return 0.0
    total = sum(p.size_usd for p in state.positions if p.city == city and p.state not in INACTIVE_RUNTIME_STATES)
    return total / bankroll


def cluster_exposure_for_bankroll(state: PortfolioState, cluster: str, bankroll: float) -> float:
    """Cluster exposure against an explicit entry bankroll/cap."""
    if bankroll <= 0:
        return 0.0
    total = sum(p.size_usd for p in state.positions if p.cluster == cluster and p.state not in INACTIVE_RUNTIME_STATES)
    return total / bankroll




# --- Churn defense: Layers 5, 6, 7 ---

def is_reentry_blocked(
    state: PortfolioState, city: str, bin_label: str,
    target_date: str, minutes: int = 20,
) -> bool:
    """Layer 5: Block re-entry into a range recently exited via reversal."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    reversal_reasons = {
        "EDGE_REVERSAL", "BUY_NO_EDGE_EXIT", "ENSEMBLE_CONFLICT",
        "DAY0_OBSERVATION_REVERSAL",
    }
    for ex in state.recent_exits:
        if (ex["city"] == city and ex["bin_label"] == bin_label
                and ex["target_date"] == target_date
                and ex["exit_reason"] in reversal_reasons
                and ex["exited_at"] >= cutoff):
            return True
    return False


def is_token_on_cooldown(state: PortfolioState, token_id: str, hours: float = 1.0) -> bool:
    """Layer 6: Block rebuy of tokens voided within the last hour."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    voided_reasons = {"UNFILLED_ORDER", "EXIT_FAILED"}
    for ex in state.recent_exits:
        if ((ex["token_id"] == token_id or ex["no_token_id"] == token_id)
                and ex["exit_reason"] in voided_reasons
                and ex["exited_at"] >= cutoff):
            return True
    return False


def has_same_city_range_open(state: PortfolioState, city: str, bin_label: str) -> bool:
    """Layer 7: Block same city+range across different dates."""
    return any(
        p.city == city
        and p.bin_label == bin_label
        and p.state not in INACTIVE_RUNTIME_STATES
        for p in state.positions
    )


_V2_INTRODUCTION_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)

_BUY_NO_SCALING = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_no_scaling_factor"]),
    fallback=1.5,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_BUY_YES_SCALING = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_yes_scaling_factor"]),
    fallback=1.0,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_BUY_NO_FLOOR = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_no_floor"]),
    fallback=-0.03,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_BUY_NO_CEILING = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_no_ceiling"]),
    fallback=-0.15,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_BUY_YES_FLOOR = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_yes_floor"]),
    fallback=-0.02,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_BUY_YES_CEILING = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_yes_ceiling"]),
    fallback=-0.10,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_CONSECUTIVE_CONFIRMS = ExpiringAssumption[int](
    value=int(settings["exit"]["consecutive_confirmations"]),
    fallback=2,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=365,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_NEAR_SETTLEMENT_HOURS = ExpiringAssumption[float](
    value=float(settings["exit"]["near_settlement_hours"]),
    fallback=48.0,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=365,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team"
)

_DIVERGENCE_SOFT_THRESHOLD = ExpiringAssumption[float](
    value=float(settings["exit"]["divergence_soft_threshold"]),
    fallback=0.20,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="divergence_threshold_audit",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team",
)

_DIVERGENCE_HARD_THRESHOLD = ExpiringAssumption[float](
    value=float(settings["exit"]["divergence_hard_threshold"]),
    fallback=0.30,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="divergence_threshold_audit",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team",
)

_DIVERGENCE_VELOCITY_CONFIRM = ExpiringAssumption[float](
    value=float(settings["exit"]["divergence_velocity_confirm"]),
    fallback=-0.05,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="divergence_threshold_audit",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_version="v2",
    owner="risk_team",
)


def buy_no_scaling_factor() -> float:
    return _BUY_NO_SCALING.active_value

def buy_yes_scaling_factor() -> float:
    return _BUY_YES_SCALING.active_value

def buy_no_floor() -> float:
    return _BUY_NO_FLOOR.active_value

def buy_no_ceiling() -> float:
    return _BUY_NO_CEILING.active_value

def buy_yes_floor() -> float:
    return _BUY_YES_FLOOR.active_value

def buy_yes_ceiling() -> float:
    return _BUY_YES_CEILING.active_value

def consecutive_confirmations() -> int:
    return _CONSECUTIVE_CONFIRMS.active_value

def near_settlement_hours() -> float:
    return _NEAR_SETTLEMENT_HOURS.active_value


def divergence_soft_threshold() -> float:
    return _DIVERGENCE_SOFT_THRESHOLD.active_value


def divergence_hard_threshold() -> float:
    return _DIVERGENCE_HARD_THRESHOLD.active_value


def divergence_velocity_confirm() -> float:
    return _DIVERGENCE_VELOCITY_CONFIRM.active_value


def _clamp_negative_threshold(raw: float, floor: float, ceiling: float) -> float:
    """Clamp a negative threshold between a shallow floor and deep ceiling."""
    return max(ceiling, min(floor, raw))


def conservative_forward_edge(forward_edge: float, ci_width: float) -> float:
    """Conservative exit evidence: use the lower confidence bound of edge."""
    return forward_edge - max(0.0, float(ci_width)) / 2.0


def buy_no_edge_threshold(entry_ci_width: float) -> float:
    raw = -abs(entry_ci_width) * buy_no_scaling_factor()
    return _clamp_negative_threshold(raw, buy_no_floor(), buy_no_ceiling())


def buy_yes_edge_threshold(entry_ci_width: float) -> float:
    raw = -abs(entry_ci_width) * buy_yes_scaling_factor()
    return _clamp_negative_threshold(raw, buy_yes_floor(), buy_yes_ceiling())
