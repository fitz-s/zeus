"""Portfolio state management. Spec §6.4.

Atomic JSON + SQL mirror. Positions are projection-cache adapters; canonical
truth is `position_events` + `position_current` (see PR #352, F1 in
docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md). The `Position` dataclass is a runtime view that
combines submitted-intent economics, verified fill economics, and chain-
observed economics into a single object — but each economics object has its
own authority field on `position_current` and event payloads. The legacy
``entry_price`` / ``cost_basis_usd`` / ``size_usd`` / ``shares`` attributes
remain as derived/compatibility views and MUST NOT be mutated by chain-
balance rescue after F1: balance-only rescue writes the chain aggregate into
``chain_avg_price`` / ``chain_cost_basis_usd`` / ``chain_shares`` only.

Provides exposure queries for risk limit enforcement.
"""

import json
import logging
import math
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from src.config import (
    STATE_DIR,
    exit_correlation_crowding_rate,
    exit_daily_hurdle_rate,
    exit_fee_rate,
    get_mode,
    hold_value_exit_costs_enabled,
    settings,
    state_path,
)
from src.contracts import (
    HeldSideProbability, 
    NativeSidePrice, 
    compute_forward_edge,
    ExpiringAssumption,
)
from src.contracts.position_truth import (
    CHAIN_ONLY_REVIEW_WINDOW_HOURS,
    ChainOnlyFact,
    ChainOnlyReviewState,
    CURRENT_MONEY_RISK_CHAIN_STATES,
    NO_CURRENT_MONEY_RISK_CHAIN_STATES,
    REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES,
    has_current_money_risk_chain_state,
)
from src.contracts.semantic_types import VenueVisibilityStatus, Direction, DirectionAlias, ExitState, LifecycleState
from src.contracts.settlement_outcome import SettlementOutcome
from src.contracts.hold_value import HoldValue
from src.strategy.correlation import get_correlation
from src.strategy.live_inference.live_admission import LIVE_DIRECTION_WIN_RATE_FLOOR
from src.state.lifecycle_manager import (
    TERMINAL_STATES as _TERMINAL_POSITION_STATES,
    enter_admin_closed_runtime_state,
    enter_chain_quarantined_runtime_state,
    enter_economically_closed_runtime_state,
    enter_settled_runtime_state,
    enter_voided_runtime_state,
)
from src.state.portfolio_loader_policy import choose_portfolio_truth_source
from src.state.truth_files import annotate_truth_payload
from src.types.truth_authority import TruthAuthority
from src.observability.counters import increment as _cnt_inc

logger = logging.getLogger(__name__)

CANONICAL_STRATEGY_KEYS = {
    "settlement_capture",
    "shoulder_sell",
    "center_buy",
    "opening_inertia",
}

POSITION_ENV_UNKNOWN = "unknown_env"

POSITIONS_PATH = state_path("positions.json")

# Portfolio authority labels are a separate grammar from observation authority.
# ObservationAtom uses Literal["VERIFIED", "UNVERIFIED", "QUARANTINED"]
# (src/types/observation_atom.py). DEGRADED_PROJECTION is a portfolio-only
# label emitted via annotate_truth_payload in save_portfolio; it MUST NOT
# flow into ObservationAtom or MarketScanner typed boundaries.
# Verified isolated: grep confirms no DEGRADED_PROJECTION consumer in
# src/types/ or src/data/market_scanner.py (2026-04-26 audit).
#
# 2026-05-01 P1-3: values switched from bare strings to TruthAuthority
# StrEnum members. Wire-compatible (StrEnum extends str), but a future
# producer that bypasses the enum is caught by
# tests/test_truth_authority_enum.py at pre-commit time.
_TRUTH_AUTHORITY_MAP: dict[str, TruthAuthority] = {
    "canonical_db": TruthAuthority.VERIFIED,
    "degraded":     TruthAuthority.DEGRADED_PROJECTION,
    "unverified":   TruthAuthority.UNVERIFIED,
}


@dataclass
class ExitDecision:
    """Result of Position.evaluate_exit()."""
    should_exit: bool
    reason: str = ""
    urgency: str = "normal"  # "normal" or "immediate"
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)
    trigger: str = ""


_CI_SEP_EPS: float = 1e-9

def _ci_intervals_separated(
    a: Optional[tuple], b: Optional[tuple]
) -> Optional[bool]:
    """True iff two confidence intervals are DISJOINT (no overlap) — the SD-7 CI-separation test.

    Mirrors src.events.continuous_redecision._ci_separated (the severed screen_exit logic). The
    CI bounds are the system's existing robust LCB/percentile (q_5pct-style) machinery, supplied by
    the caller — NOT a new statistic. Returns None when either CI is unavailable or non-finite, so
    the caller falls back to the flat reversal floor.
    """
    if a is None or b is None:
        return None
    try:
        lo_a, hi_a = float(min(a)), float(max(a))
        lo_b, hi_b = float(min(b)), float(max(b))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (lo_a, hi_a, lo_b, hi_b)):
        return None
    return (hi_a < lo_b - _CI_SEP_EPS) or (hi_b < lo_a - _CI_SEP_EPS)


def _held_side_lcb_or_point(
    ci: Optional[tuple],
    point: Optional[float],
) -> Optional[float]:
    if ci is not None:
        try:
            lower = float(min(ci))
        except (TypeError, ValueError):
            lower = float("nan")
        if math.isfinite(lower):
            return lower
    if point is None:
        return None
    try:
        point_float = float(point)
    except (TypeError, ValueError):
        return None
    if math.isfinite(point_float):
        return point_float
    return None


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
    day0_zero_probability_exit_authority: bool = False
    whale_toxicity: Optional[bool] = None
    chain_is_fresh: Optional[bool] = None
    divergence_score: float = 0.0
    market_velocity_1h: float = 0.0

    # T6.4-phase2 (2026-04-24): portfolio context for correlation-crowding
    # cost computation in HoldValue.compute_with_exit_costs. Threaded by
    # cycle_runtime._build_exit_context from PortfolioState at monitor tick.
    # Each element is (cluster, effective_cost_basis_usd, trade_id) for OTHER held positions
    # (self excluded). Empty tuple = no co-held positions / not threaded.
    # bankroll: current bankroll used as the denominator for exposure %.
    portfolio_positions: tuple = ()
    bankroll: Optional[float] = None

    # BUG#113 (守護 SD-7): CI-separation exit inputs, threaded from the cycle's ALREADY
    # computed bootstrap CI machinery (monitor_refresh EdgeContext.confidence_band_*) and the
    # entry-time held-side belief snapshot — NOT a new statistic and NOT a fresh DB read. The
    # 2026-05-31 severance deadlocked because the belief read opened a SECOND world connection
    # inside the reactor SAVEPOINT; threading the bounds through this frozen context means the
    # live CI-separation gate performs ZERO DB I/O, so that deadlock category is impossible.
    #   entry_posterior : held-side belief point at entry (Position.p_posterior, frozen).
    #   entry_ci        : (lo, hi) entry belief CI (entry_posterior ± entry_ci_width/2).
    #   current_ci      : (lo, hi) CURRENT belief CI (fresh bootstrap from this cycle).
    #   belief_available: False when current belief math is degraded (day0 absorbing-mask /
    #                     obs gap) — the EVIDENCE_UNAVAILABLE third state (distinct from
    #                     belief-reversed). When any of these is None, the gate is inert and
    #                     the legacy flat 2-confirm path runs unchanged (full back-compat).
    entry_posterior: Optional[float] = None
    entry_ci: Optional[tuple] = None
    current_ci: Optional[tuple] = None
    belief_available: bool = True

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
        elif not self.fresh_prob_is_fresh:
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


def _compute_exit_correlation_crowding(
    *,
    this_cluster: str,
    portfolio_positions: tuple,
    bankroll: Optional[float],
    shares: float,
    best_bid: float,
    crowding_rate: float,
) -> float:
    """T6.4-phase2: compute the dollar-denominated correlation-crowding cost
    for an exit decision.

    Formula:
        exposure_ratio = Σ over OTHER held positions of
            (other.effective_cost_basis_usd / bankroll) × get_correlation(this_cluster, other.cluster)
        cost_usd = crowding_rate × exposure_ratio × shares × best_bid

    Returns 0.0 safely when:
        - portfolio_positions is empty (no co-held positions)
        - bankroll is None or <= 0 (authority gap)
        - crowding_rate is 0.0 (feature off by default)

    Self-exclusion is already applied at the _build_exit_context layer
    (trade_id filter). This function sums correlation × exposure across
    whatever tuple it receives.
    """
    if crowding_rate <= 0.0:
        return 0.0
    if not portfolio_positions:
        return 0.0
    if bankroll is None or bankroll <= 0.0:
        return 0.0

    exposure_ratio = 0.0
    for entry in portfolio_positions:
        # entry is (cluster, effective_cost_basis_usd, trade_id) tuple per _build_exit_context
        try:
            other_cluster, other_size_usd, _trade_id = entry
        except (TypeError, ValueError):
            continue
        try:
            size_pct = float(other_size_usd) / float(bankroll)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        corr = get_correlation(str(this_cluster), str(other_cluster))
        exposure_ratio += size_pct * corr

    return float(crowding_rate) * exposure_ratio * float(shares) * float(best_bid)


# Administrative exit reasons — excluded from P&L calculations
ADMIN_EXITS = frozenset({
    "PHANTOM_NOT_ON_CHAIN",
    "UNFILLED_ORDER", "SETTLED_NOT_IN_API", "EXIT_FAILED",
    "SETTLED_UNKNOWN_DIRECTION", "EXIT_CHAIN_MISSING_REVIEW_REQUIRED",
})  # GHOST_DUPLICATE removed 2026-06-29: 0 live rows, no writer (dead value)

# K1/#49: Sentinel for quarantine placeholder fields — downstream code must
# check `pos.is_quarantine_placeholder` instead of comparing city == "UNKNOWN".
QUARANTINE_SENTINEL = "QUARANTINE_UNRESOLVED"

ENTRY_ECONOMICS_LEGACY_UNKNOWN = "legacy_unknown"
ENTRY_ECONOMICS_MODEL_EDGE_PRICE = "model_edge_price"
ENTRY_ECONOMICS_SUBMITTED_LIMIT = "submitted_limit"
ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE = "optimistic_match_price"
ENTRY_ECONOMICS_AVG_FILL_PRICE = "avg_fill_price"
ENTRY_ECONOMICS_CORRECTED_COST_BASIS = "corrected_executable_cost_basis"

FILL_AUTHORITY_NONE = "none"
# PR C3 (Finding 5, 2026-05-27): degraded recovery authority. Set when chain
# reconciliation rescues a pending entry against an aggregate venue balance
# WITHOUT a linked venue trade fact. Tradable as active exposure, but
# downstream training gates (PR D Finding 9) MUST treat it as not training-
# eligible. Distinct from venue_confirmed_full which requires a trade fact.
FILL_AUTHORITY_VENUE_POSITION_OBSERVED = "venue_position_observed"
FILL_AUTHORITY_OPTIMISTIC_SUBMITTED = "optimistic_submitted"
FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL = "venue_confirmed_partial"
FILL_AUTHORITY_VENUE_CONFIRMED_FULL = "venue_confirmed_full"
FILL_AUTHORITY_CANCELLED_REMAINDER = "cancelled_remainder"
FILL_AUTHORITY_SETTLED = "settled"
CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION = "corrected_executable_cost_v1"

FILL_GRADE_ENTRY_AUTHORITIES = frozenset({
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
})
FILL_GRADE_FILL_AUTHORITIES = frozenset({
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_CANCELLED_REMAINDER,
    FILL_AUTHORITY_SETTLED,
})

FILL_AUTHORITY_RANK = {
    FILL_AUTHORITY_NONE: 0,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED: 0,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL: 1,
    FILL_AUTHORITY_CANCELLED_REMAINDER: 2,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL: 3,
    FILL_AUTHORITY_SETTLED: 4,
}

# Real-submit decision_audit was introduced with the live aggregate hotfix on
# 2026-06-07. Earlier fills are real exposure but cannot be retroactively given
# a q/selector proof without fabrication. They stay unscorable for settlement
# attribution, but must remain manageable by the exit/risk plane.
EDLI_DECISION_AUDIT_REQUIRED_FROM = datetime(2026, 6, 7, 3, 0, tzinfo=timezone.utc)

# PR D2 (Finding 9, 2026-05-27): authorities that MAY produce training rows.
# `is_training_eligible_position(pos)` is the type-boundary that downstream
# learning/calibration writers must consult before writing a row to
# `calibration_pairs`. Authorities outside this set carry insufficient
# causality / fill provenance to support model fitting.
#
# References the FILL_AUTHORITY_* constants by name (not bare string literals)
# so a future rename produces a NameError at import time, not silent
# mis-categorization — per Copilot review on PR #347 (2026-05-27).
TRAINING_ELIGIBLE_FILL_AUTHORITIES = frozenset({
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_CANCELLED_REMAINDER,
    FILL_AUTHORITY_SETTLED,
})


def is_training_eligible_position(pos: object) -> bool:
    """Return True iff this position's fill economics are strong enough to
    feed model training (Finding 9, PR D2).

    Rejects:
      - FILL_AUTHORITY_VENUE_POSITION_OBSERVED  (PR C3 degraded recovery from aggregate balance)
      - FILL_AUTHORITY_OPTIMISTIC_SUBMITTED     (no venue confirmation)
      - FILL_AUTHORITY_NONE                     (no fill at all)
      - "legacy_unknown"                        (pre-typed-authority rows)
      - any unknown value                       (fail-closed)
    """
    authority = str(getattr(pos, "fill_authority", "") or "").strip()
    return authority in TRAINING_ELIGIBLE_FILL_AUTHORITIES


# PR D0 (Finding D0, Part-2 audit, 2026-05-27): split entry_fill_verified as rescue
# authority into two orthogonal derivations from fill_authority.
#
#   has_verified_trade_fill(pos) — True iff a real venue trade fact confirmed the
#     fill. Use this for fill-economics accuracy gates (learning rows, cost-basis
#     trust, unverified-entry counts). False for balance-only recovery.
#
#   has_tradable_exposure(pos) — True iff the position carries real capital at risk
#     that riskguard/exit EXPOSURE gates must manage. True for both trade-verified
#     AND balance-only (venue_position_observed) fills. False for unsubmitted
#     or pending entries with no venue confirmation at all.
#
# Both are pure derivations from fill_authority — NO new schema columns.
# entry_fill_verified remains on the Position for normal fill path (fill_tracker.py)
# but must NOT be relied on to distinguish the two categories above.


def has_verified_trade_fill(pos: object) -> bool:
    """Return True iff a venue trade fact confirmed this position's fill.

    Used by fill-economics gates: unverified-entry counts, learning-row writers,
    cost-basis trust logic. Returns False for balance-only recovery
    (FILL_AUTHORITY_VENUE_POSITION_OBSERVED) and for any unconfirmed authority.

    Accepts both Position objects (attribute access) and plain dicts
    (key access) so callers in db.py can use it directly on fill_economics dicts.
    """
    if isinstance(pos, dict):
        authority = str(pos.get("fill_authority", "") or "").strip()
    else:
        authority = str(getattr(pos, "fill_authority", "") or "").strip()
    return authority in FILL_GRADE_FILL_AUTHORITIES


def has_tradable_exposure(pos: object) -> bool:
    """Return True iff this position carries real capital at risk.

    EXPOSURE gates (riskguard, exit coordinator) must use this, not
    entry_fill_verified, to decide whether to manage a position. True for:
      - All FILL_GRADE_FILL_AUTHORITIES (trade-verified fills)
      - FILL_AUTHORITY_VENUE_POSITION_OBSERVED (balance-only recovery — real
        capital is held on-chain even though no trade fact was linked)

    False for unconfirmed/pending entries (FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED, legacy_unknown).

    Accepts both Position objects (attribute access) and plain dicts
    (key access) so callers in db.py can use it directly on fill_economics dicts.
    """
    if isinstance(pos, dict):
        authority = str(pos.get("fill_authority", "") or "").strip()
    else:
        authority = str(getattr(pos, "fill_authority", "") or "").strip()
    return authority in FILL_GRADE_FILL_AUTHORITIES or authority == FILL_AUTHORITY_VENUE_POSITION_OBSERVED


def _finite_float_or_zero(value: object) -> float:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


# F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): effective-exposure typed
# view consumed by exit_triggers / monitor_refresh / risk gates. Routes by
# fill_authority so balance-only (venue_position_observed) positions expose
# chain economics, and trade-verified positions expose fill economics — both
# without any consumer needing to read raw Position.shares / entry_price /
# cost_basis_usd.
EFFECTIVE_EXPOSURE_SOURCE_VENUE_TRADE_FILL = "venue_trade_fill"
EFFECTIVE_EXPOSURE_SOURCE_VENUE_POSITION_OBSERVED = "venue_position_observed"
EFFECTIVE_EXPOSURE_SOURCE_SUBMITTED_INTENT = "submitted_intent"
EFFECTIVE_EXPOSURE_SOURCE_NONE = "none"


@dataclass(frozen=True)
class EffectiveExposure:
    """Derived view: a Position's currently-effective exposure for exit/risk.

    Authority routing (F1):
      - venue_confirmed_* fill_authority → fill economics
        (filled_cost_basis_usd, shares_filled, avg_fill_price)
      - venue_position_observed         → chain economics
        (chain_cost_basis_usd, chain_shares, chain_avg_price)
      - pending / unverified            → 0.0 / submitted intent (per state)

    Consumers MUST read this rather than raw Position.shares /
    Position.entry_price / Position.cost_basis_usd for exit-sizing,
    risk-exposure, and EV gating.
    """
    shares: float
    cost_basis_usd: float
    avg_price: float
    source_authority: str


def fill_authority_effective_open_cost_basis(
    *,
    current_open_cost: object,
    current_open_shares: object,
    entry_fill_cost: object,
    entry_fill_shares: object,
) -> float:
    """Derive current open cost from fill authority without capping real fills.

    A lower current/projection cost is authoritative only when the current open
    share count proves that part of the filled entry slice has been sold.
    Otherwise the venue-confirmed fill cost remains the open cost basis.
    """

    fill_cost = _finite_float_or_zero(entry_fill_cost)
    fill_shares = _finite_float_or_zero(entry_fill_shares)
    open_shares = _finite_float_or_zero(current_open_shares)
    open_cost = _finite_float_or_zero(current_open_cost)

    if fill_cost <= 0.0:
        return open_cost if open_cost > 0.0 else 0.0
    if fill_shares <= 0.0 or open_shares <= 0.0:
        return fill_cost
    if open_shares < fill_shares:
        return fill_cost * (open_shares / fill_shares)
    return fill_cost


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
    temperature_metric: Literal["high", "low"] = "high"  # carried from market at entry

    # Provenance: which environment created this position (set once, never changed).
    # Missing provenance is non-authoritative; live writers must pass env explicitly.
    env: str = POSITION_ENV_UNKNOWN

    # Probability (always in held-side space — flipped exactly once at creation)
    size_usd: float = 0.0
    entry_price: float = 0.0  # Native space
    p_posterior: float = 0.0  # Native space (p_held_side in blueprint)
    edge: float = 0.0
    shares: float = 0.0  # size_usd / entry_price
    cost_basis_usd: float = 0.0  # = size_usd
    target_notional_usd: float = 0.0
    submitted_notional_usd: float = 0.0
    filled_cost_basis_usd: float = 0.0
    entry_price_submitted: float = 0.0
    entry_price_avg_fill: float = 0.0
    shares_submitted: float = 0.0
    shares_filled: float = 0.0
    shares_remaining: float = 0.0
    entry_cost_basis_id: str = ""
    entry_cost_basis_hash: str = ""
    entry_economics_authority: str = ENTRY_ECONOMICS_LEGACY_UNKNOWN
    fill_authority: str = FILL_AUTHORITY_NONE
    pricing_semantics_id: str = "legacy_unclassified"
    execution_cost_basis_version: str = ""
    corrected_executable_economics_eligible: bool = False
    bankroll_at_entry: Optional[float] = None
    entered_at: str = ""
    # F2 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F2, 2026-05-28): provenance tag for
    # entered_at. "verified_entry_fill" when the timestamp came from a real
    # venue fill fact; "reconstructed_from_chain" when it was fabricated from
    # the reconcile-time clock (no fill fact available); "" for legacy rows
    # that predate this field.
    entered_at_authority: str = ""
    day0_entered_at: str = ""
    # Slice P3-fix3 (post-review critic Major #2, 2026-04-26): ENTRY-TIME
    # SNAPSHOT — frozen at construction (cycle_runtime.py:273) and NOT
    # refreshed post-entry. Consumers reading this for stale-but-
    # defensive CI fallback (e.g. monitor_refresh.py:730 P3.2) must
    # accept that the width reflects entry-time bin geometry, not
    # current. Steady-state uses fresh bootstrap CI; this fallback is
    # bounded to post-restart first-cycle window.
    entry_ci_width: float = 0.0

    # Entry context (immutable snapshot — Blueprint v2 §2)
    entry_method: str = "ens_member_counting"
    signal_version: str = "v2"
    calibration_version: str = ""
    decision_snapshot_id: str = ""  # FK to ensemble_snapshots at decision time
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)
    entry_model_agreement: str = "NOT_CHECKED"  # P9 fix: GFS crosscheck result at entry time

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
    nested_fills: list = field(default_factory=list)

    # Chain reconciliation (Blueprint v2 §5)
    chain_state: str = VenueVisibilityStatus.UNKNOWN.value
    chain_shares: float = 0.0
    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md, 2026-05-28): chain-observed economics
    # carry their own typed slots so balance-only rescue does not overwrite
    # decision/fill economics on `entry_price` / `cost_basis_usd` / `size_usd`.
    # Set by chain_reconciliation balance-only rescue branch; consumed by
    # `effective_exposure()` when fill_authority == venue_position_observed.
    chain_avg_price: float = 0.0
    chain_cost_basis_usd: float = 0.0
    # `chain_verified_at` is a POSITIVE observation timestamp ONLY: it records
    # when the venue/chain confirmed this position is held (rescue, size
    # correction, sync). It MUST NOT be advanced when the chain snapshot does
    # not see this position — that case uses `last_chain_absence_observed_at`.
    # Finding 1 (PR C0, 2026-05-27): conflating positive and absence
    # observations into one timestamp inverted CHAIN_EMPTY vs CHAIN_UNKNOWN
    # semantics downstream. See docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-position-model-refactor.md.
    chain_verified_at: str = ""
    last_chain_absence_observed_at: str = ""

    # Token IDs for CLOB orderbook queries
    token_id: str = ""
    no_token_id: str = ""
    condition_id: str = ""

    # Quarantine tracking
    quarantined_at: str = ""  # ISO timestamp when quarantined

    # Exit state (persisted across monitor cycles — Blueprint v2 §7)
    neg_edge_count: int = 0
    # BUG#127: consecutive monitor cycles an adverse flash-crash-magnitude
    # market velocity has persisted. Drives the persistence path of
    # flash_crash_should_fire(); reset to 0 the moment velocity recovers above
    # the arming threshold. Carried across cycles like neg_edge_count.
    flash_crash_count: int = 0
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
    entry_fill_verified: bool = False  # True only after final fill confirmation.

    # Anti-churn
    last_exit_at: str = ""
    exit_trigger: str = ""
    exit_reason: str = ""
    admin_exit_reason: str = ""  # Separate from economic exit_reason
    exit_divergence_score: float = 0.0
    exit_market_velocity_1h: float = 0.0
    exit_forward_edge: float = 0.0

    # Lineage audit-trail: FK to EdgeDecision that originated entry order.
    # Set at Position creation from EdgeDecision.decision_id; forwarded to
    # exit-side execution_fact so the full entry→exit lineage is joinable.
    # Default None for in-memory compatibility with pre-fix positions.
    decision_id: Optional[str] = None

    # JSON Object Snapshots (Phase 2 Object Persistence DTO)
    settlement_semantics_json: Optional[str] = None
    epistemic_context_json: Optional[str] = None
    edge_context_json: Optional[str] = None

    # P&L (set on close)
    exit_price: float = 0.0
    pnl: float = 0.0

    # Market slug (JSON-only — Phase 2 T5; NO SQL ALTER, NO SCHEMA_VERSION bump).
    # Populated from market_events at decision time or via one-shot backfill.
    # Used by monitor_refresh nowcast wiring to gate write_nowcast_run calls.
    # Default None preserves backward-compat with v1-vintage positions.json.
    market_slug: Optional[str] = None

    # Settlement lifecycle state (JSON-only — Phase 7 T1; NO SQL ALTER, NO SCHEMA_VERSION bump).
    # Typed enum tracks the settlement lifecycle from UNRESOLVED through REDEEMED.
    # Default UNRESOLVED preserves backward-compat with pre-Phase-7 positions.json.
    lifecycle_state: SettlementOutcome = SettlementOutcome.UNRESOLVED

    def __post_init__(self):
        """CRITICAL: Enforce Enum strictness via coercion."""
        # S3 R5 P10B: runtime enforcement of Literal["high", "low"] at entry point
        assert self.temperature_metric in ("high", "low"), (
            f"Invalid temperature_metric: {self.temperature_metric!r}"
        )
        if not isinstance(self.direction, Direction):
            self.direction = Direction(self.direction)
        if not isinstance(self.state, LifecycleState):
            self.state = LifecycleState(self.state)
        if not isinstance(self.chain_state, VenueVisibilityStatus):
            self.chain_state = VenueVisibilityStatus(self.chain_state)
        if not isinstance(self.exit_state, ExitState):
            self.exit_state = ExitState(self.exit_state)
        if self.pre_exit_state:
            self.pre_exit_state = LifecycleState(self.pre_exit_state).value
        if not isinstance(self.lifecycle_state, SettlementOutcome):
            self.lifecycle_state = SettlementOutcome(int(self.lifecycle_state))


    @property
    def is_pending_entry_without_fill_authority(self) -> bool:
        state_value = self.state.value if isinstance(self.state, LifecycleState) else str(self.state)
        return (
            state_value == LifecycleState.PENDING_TRACKED.value
            and not self.has_fill_economics_authority
        )

    @property
    def effective_shares(self) -> float:
        if self.is_pending_entry_without_fill_authority:
            return 0.0
        if self.has_fill_economics_authority:
            current_open_shares = float(self.shares or 0.0)
            entry_fill_shares = float(self.shares_filled or 0.0)
            if current_open_shares > 0:
                return min(current_open_shares, entry_fill_shares)
            return entry_fill_shares
        # F1: balance-only rescue (venue_position_observed). chain_shares is
        # the ONLY truth — the legacy `shares` field is no longer mutated by
        # the rescue branch, so falling through to it would return the
        # pre-rescue submitted shares (or zero).
        if self.has_chain_observed_authority and self.chain_shares > 0:
            return float(self.chain_shares)
        if self.shares > 0:
            return self.shares
        if self.entry_price > 0:
            return self.size_usd / self.entry_price
        return 0.0

    @property
    def effective_cost_basis_usd(self) -> float:
        if self.is_pending_entry_without_fill_authority:
            return 0.0
        if self.has_fill_economics_authority:
            return fill_authority_effective_open_cost_basis(
                current_open_cost=self.cost_basis_usd,
                current_open_shares=self.shares,
                entry_fill_cost=self.filled_cost_basis_usd,
                entry_fill_shares=self.shares_filled,
            )
        # F1: balance-only rescue routes through chain_cost_basis_usd.
        if self.has_chain_observed_authority and self.chain_cost_basis_usd > 0:
            return float(self.chain_cost_basis_usd)
        return self.cost_basis_usd if self.cost_basis_usd > 0 else self.size_usd

    @property
    def has_fill_economics_authority(self) -> bool:
        return (
            self.entry_economics_authority in FILL_GRADE_ENTRY_AUTHORITIES
            and self.fill_authority in FILL_GRADE_FILL_AUTHORITIES
            and self.shares_filled > 0
            and self.filled_cost_basis_usd > 0
        )

    @property
    def has_chain_observed_authority(self) -> bool:
        """F1: True iff this position's economics are chain-observed only
        (balance-only rescue with no linked venue trade fact)."""
        return self.fill_authority == FILL_AUTHORITY_VENUE_POSITION_OBSERVED

    def effective_exposure(self) -> EffectiveExposure:
        """Typed effective-exposure view for exit/risk consumers (F1).

        Authority routing:
          - venue_confirmed_* fill_authority → fill economics
          - venue_position_observed         → chain economics
          - pending / unverified            → zero exposure or submitted-intent
            depending on lifecycle state

        Returns:
            EffectiveExposure with `source_authority` identifying the
            economics object the exposure was derived from.
        """
        # Pending-entry without authority: no real exposure yet.
        if self.is_pending_entry_without_fill_authority:
            return EffectiveExposure(
                shares=0.0,
                cost_basis_usd=0.0,
                avg_price=0.0,
                source_authority=EFFECTIVE_EXPOSURE_SOURCE_NONE,
            )
        # Trade-verified fill: route to fill economics.
        if self.has_fill_economics_authority:
            shares = float(self.effective_shares or 0.0)
            cost = float(
                fill_authority_effective_open_cost_basis(
                    current_open_cost=self.cost_basis_usd,
                    current_open_shares=self.shares,
                    entry_fill_cost=self.filled_cost_basis_usd,
                    entry_fill_shares=self.shares_filled,
                )
                or 0.0
            )
            avg_price = (
                cost / shares if shares > 0 else float(self.entry_price_avg_fill or self.entry_price or 0.0)
            )
            return EffectiveExposure(
                shares=shares,
                cost_basis_usd=cost,
                avg_price=float(avg_price),
                source_authority=EFFECTIVE_EXPOSURE_SOURCE_VENUE_TRADE_FILL,
            )
        # Balance-only rescue: chain economics are the only truth.
        if self.has_chain_observed_authority:
            shares = float(self.chain_shares or 0.0)
            cost = float(self.chain_cost_basis_usd or 0.0)
            avg_price = float(self.chain_avg_price or 0.0)
            if avg_price <= 0.0 and shares > 0.0 and cost > 0.0:
                avg_price = cost / shares
            return EffectiveExposure(
                shares=shares,
                cost_basis_usd=cost,
                avg_price=avg_price,
                source_authority=EFFECTIVE_EXPOSURE_SOURCE_VENUE_POSITION_OBSERVED,
            )
        # Submitted intent fallback (legacy / pre-F1 positions). entry_price /
        # size_usd / cost_basis_usd here reflect the local intent and are not
        # authoritative; record the source so downstream gates can degrade.
        shares = float(self.shares or 0.0)
        cost = float(self.cost_basis_usd or self.size_usd or 0.0)
        avg_price = float(self.entry_price or 0.0)
        if shares <= 0 and avg_price > 0 and cost > 0:
            shares = cost / avg_price
        return EffectiveExposure(
            shares=shares,
            cost_basis_usd=cost,
            avg_price=avg_price,
            source_authority=EFFECTIVE_EXPOSURE_SOURCE_SUBMITTED_INTENT,
        )

    @property
    def unrealized_pnl(self) -> float:
        """Mark-to-market P&L based on the last known native-space market price."""
        if self.last_monitor_market_price is None:
            return 0.0
        return self.effective_shares * self.last_monitor_market_price - self.effective_cost_basis_usd

    @property
    def is_quarantine_placeholder(self) -> bool:
        """K1/#49: True when this position is a chain-only quarantine stub with
        unresolved market metadata.  Downstream code must NOT participate these
        in lifecycle, risk, or monitor logic."""
        return self.city == QUARANTINE_SENTINEL

    def _sell_value_exceeds_hold_value(
        self,
        *,
        current_p_posterior: float,
        best_bid: Optional[float],
        hours_to_settlement: Optional[float],
        applied: list[str],
        portfolio_positions: tuple = (),
        bankroll: Optional[float] = None,
    ) -> Optional[bool]:
        """Return whether immediate sale beats held EV; None means no proof."""

        if not ExitContext._is_finite(best_bid):
            applied.append("best_bid_unavailable")
            return None
        shares = self.effective_shares
        if shares <= 0:
            applied.append("effective_shares_unavailable")
            return None
        applied.append("ev_gate")
        executable_bid = float(best_bid)
        if hold_value_exit_costs_enabled():
            applied.append("hold_value_exit_costs_enabled")
            if hours_to_settlement is None or hours_to_settlement < 0.0:
                applied.append("hold_value_hours_unknown_time_cost_zero")
            _crowding = _compute_exit_correlation_crowding(
                this_cluster=self.cluster,
                portfolio_positions=portfolio_positions,
                bankroll=bankroll,
                shares=shares,
                best_bid=executable_bid,
                crowding_rate=exit_correlation_crowding_rate(),
            )
            if _crowding > 0.0:
                applied.append("hold_value_correlation_crowding_applied")
            hold_value = HoldValue.compute_with_exit_costs(
                shares=shares,
                current_p_posterior=current_p_posterior,
                best_bid=executable_bid,
                hours_to_settlement=hours_to_settlement,
                fee_rate=exit_fee_rate(),
                daily_hurdle_rate=exit_daily_hurdle_rate(),
                correlation_crowding=_crowding,
            )
        else:
            hold_value = HoldValue.compute(
                gross_value=shares * current_p_posterior,
                fee_cost=0.0,
                time_cost=0.0,
            )
        return shares * executable_bid > hold_value.net_value

    def _near_settlement_hold_is_confirmed_win(
        self,
        *,
        current_p_posterior: float,
        best_bid: Optional[float],
    ) -> bool:
        """Return whether near-settle hold is a confirmed-win posture, not generic EV drift."""

        if not ExitContext._is_finite(best_bid):
            return False
        return float(best_bid) >= 0.95 and float(current_p_posterior) >= 0.95

    def evaluate_exit(self, exit_context: ExitContext) -> ExitDecision:
        """Position knows how to exit ITSELF. Monitor just calls this.

        All probabilities remain in held/native space. Missing authority fields
        fail closed with an explicit incomplete verdict.

        Phase 9B ITERATE resolution (DT#2 R-BY): when `self.exit_reason` is
        set to "red_force_exit" (by cycle_runner's `_execute_force_exit_sweep`
        during a RED daily-loss cycle), short-circuit normal edge evaluation
        and return `ExitDecision(should_exit=True, trigger="RED_FORCE_EXIT")`.
        This wires the Phase 9B sweep marker to the existing exit actuator
        path (monitor_refresh → evaluate_exit → execute_exit), closing the
        critic-carol cycle-3 CRITICAL-1 "inert marker" gap. Day0 positions
        skip this path — they have their own risk-containment via
        nowcast/causality; DT#2 RED is orthogonal to Day0 evaluator logic.
        """
        applied = list(self.applied_validations)

        # DT#2 RED force-exit sweep short-circuit (Phase 9B ITERATE, R-BY).
        # Must run BEFORE the missing-authority fail-closed check: when the
        # risk layer declares RED, we exit regardless of whether we have full
        # ExitContext authority. The sell order posts at whatever the
        # orderbook offers; RED containment takes precedence over normal
        # price-quality gating.
        if (
            self.exit_reason == "red_force_exit"
            and not exit_context.day0_active
        ):
            applied.append("dt2_red_force_exit_sweep_actuated")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                "RED_FORCE_EXIT",
                urgency="immediate",
                trigger="RED_FORCE_EXIT",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
            )

        # BUG#113 (守護 SD-7) — EVIDENCE_UNAVAILABLE third state. When the current belief math is
        # degraded (day0 absorbing-mask / obs gap), belief CANNOT be computed — distinct from
        # belief-reversed. Do NOT exit on a price move and do NOT collapse into a blind hold; return
        # a first-class hold flagged EVIDENCE_UNAVAILABLE for the 守護 heartbeat. Runs BEFORE the
        # missing-authority check so a NaN fresh_prob caused by degraded belief is named correctly
        # (not the generic INCOMPLETE_EXIT_CONTEXT). RED force-exit above still preempts this.
        if (
            not exit_context.belief_available
            and exit_context.entry_posterior is not None
        ):
            applied.append("ci_separation_gate")
            applied.append("evidence_unavailable_third_state")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                False,
                "EVIDENCE_UNAVAILABLE",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="EVIDENCE_UNAVAILABLE",
            )

        missing = exit_context.missing_authority_fields()
        model_probability_missing_only = (
            exit_context.day0_active
            and bool(missing)
            and set(missing) <= {"fresh_prob", "fresh_prob_is_fresh"}
        )
        if model_probability_missing_only:
            if not ExitContext._is_finite(exit_context.best_bid):
                applied.append("best_bid_unavailable")
                applied.append("exit_context_incomplete")
                applied.append("day0_probability_authority_blocked")
                missing_with_bid = list(missing) + ["best_bid"]
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    f"INCOMPLETE_EXIT_CONTEXT (missing={','.join(missing_with_bid)})",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                )
            if exit_context.hours_to_settlement is not None and exit_context.hours_to_settlement < 1.0:
                applied.append("near_settlement_gate")
                applied.append("model_probability_authority_not_required:settlement_imminent")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    True, "SETTLEMENT_IMMINENT", "immediate",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="SETTLEMENT_IMMINENT",
                )
            if exit_context.whale_toxicity:
                applied.append("whale_toxicity_gate")
                applied.append("model_probability_authority_not_required:whale_toxicity")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    True, "WHALE_TOXICITY", "immediate",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="WHALE_TOXICITY",
                )
        if missing:
            applied.append("exit_context_incomplete")
            if exit_context.day0_active and any(field in missing for field in ("fresh_prob", "fresh_prob_is_fresh")):
                applied.append("day0_probability_authority_blocked")
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

        def _live_floor_revoked_decision() -> ExitDecision | None:
            entry_held_prob = (
                float(exit_context.entry_posterior)
                if ExitContext._is_finite(exit_context.entry_posterior)
                else float(self.p_posterior or 0.0)
            )
            current_held_prob = float(exit_context.fresh_prob)
            entry_held_lcb = _held_side_lcb_or_point(exit_context.entry_ci, entry_held_prob)
            current_held_lcb = _held_side_lcb_or_point(exit_context.current_ci, current_held_prob)
            if not (
                entry_held_lcb is not None
                and current_held_lcb is not None
                and current_held_lcb < LIVE_DIRECTION_WIN_RATE_FLOOR
                and entry_held_lcb < LIVE_DIRECTION_WIN_RATE_FLOOR
                and not (exit_context.day0_active and current_held_prob <= 1e-9)
            ):
                return None
            applied.append("live_win_rate_floor_revoked")
            if not ExitContext._is_finite(exit_context.best_bid):
                applied.append("best_bid_unavailable")
                applied.append("exit_context_incomplete")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="LIVE_WIN_RATE_FLOOR_REVOKED_CONTEXT_INCOMPLETE",
                )
            self.neg_edge_count = 0
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                (
                    "LIVE_WIN_RATE_FLOOR_REVOKED "
                    f"(entry_lcb={entry_held_lcb:.4f}, current_lcb={current_held_lcb:.4f}, "
                    f"entry_point={entry_held_prob:.4f}, current_point={current_held_prob:.4f}, "
                    f"floor={LIVE_DIRECTION_WIN_RATE_FLOOR:.4f})"
                ),
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="LIVE_WIN_RATE_FLOOR_REVOKED",
            )

        if exit_context.day0_active:
            applied.append("day0_observation_authority")
            applied.append("day0_standard_exit_optimizer")
            if not ExitContext._is_finite(exit_context.best_bid):
                applied.append("best_bid_unavailable")
                applied.append("exit_context_incomplete")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                )

        # Settlement imminent (<1h). The blanket force-sell here is a FALSE EXIT for a position
        # whose hold-to-settlement EV still dominates selling now (operator-reported 2026-06-23: a
        # confirmed-win NO at 99.9c was force-sold). Route the decision through the SAME EV(hold)
        # vs EV(sell) authority the rest of the exit path uses — HoldValue net of exit costs vs
        # shares×bid — rather than any hardcoded "confirmed" price/belief threshold. HOLD only when
        # holding genuinely beats selling now; a physics REVERSAL the market has not priced (fresh
        # belief low) OR a market overpaying our fresh belief both SELL ("sell before the market
        # notices"); an unprovable EV (no executable bid / no shares -> None) keeps the conservative
        # force-sell. Freshness of fresh_prob is already gated by the missing-authority check above,
        # so a stale belief cannot drive this branch.
        if exit_context.hours_to_settlement is not None and exit_context.hours_to_settlement < 1.0:
            if self._near_settlement_hold_is_confirmed_win(
                current_p_posterior=float(exit_context.fresh_prob),
                best_bid=exit_context.best_bid,
            ):
                # Confirmed-win hold: do NOT blanket force-sell at 99c+ and do NOT let
                # model-divergence telemetry rename the decision into a panic exit.
                applied.append("near_settlement_confirmed_win_hold")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                )
            sell_beats_hold = self._sell_value_exceeds_hold_value(
                current_p_posterior=float(exit_context.fresh_prob),
                best_bid=exit_context.best_bid,
                hours_to_settlement=exit_context.hours_to_settlement,
                applied=applied,
                portfolio_positions=exit_context.portfolio_positions,
                bankroll=exit_context.bankroll,
            )
            if sell_beats_hold is not False:
                # sell-EV dominant (physics REVERSAL, or the market overpaying our fresh belief)
                # OR unprovable (no executable bid / no shares -> None): conservative force-sell.
                applied.append("near_settlement_gate")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    True, "SETTLEMENT_IMMINENT", "immediate",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="SETTLEMENT_IMMINENT",
                )
            # Hold-EV is mathematically positive but not a confirmed win. Near settlement this is not
            # enough to hand control to panic/divergence gates; exit under the deterministic time gate.
            applied.append("near_settlement_hold_ev_unconfirmed")
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

        # MODEL_DIVERGENCE_PANIC removed 2026-06-24 (Shanghai 25C "wrong exit"; frontier consult
        # REQ-20260624-105149 HIGH-confidence verdict). divergence_score = max(0, p_market - p_belief)
        # is positive ONLY when the market values the HELD side ABOVE the model — which for a held binary
        # is harmless overpayment or a cold model, NEVER adverse. The two panic branches turned that gap
        # into an immediate market-order liquidation here, PREEMPTING the purpose-built CI-separation
        # reversal gate + HoldValue economics below (the machinery that exits only on a CONFIRMED reversal,
        # never a bare price move) — and dumped near-certain winners (Shanghai NO @0.96, belief 0.655).
        # Removed outright (gate collapse, not a conditional gate). Real deterioration still exits via
        # day0 absorbing hard-fact, SETTLEMENT_IMMINENT, WHALE_TOXICITY, the velocity-evidenced
        # FLASH_CRASH_PANIC below, CI_SEPARATED_REVERSAL, and the direction-specific HoldValue economics.

        # BUG#127 (守護 SEV1): FLASH_CRASH_PANIC is evidence-gated, not a bare
        # price-delta trigger. Maintain the consecutive-cycle persistence counter
        # first, then consult the shared gate. Probability authority is guaranteed
        # present here (missing_authority_fields() already passed above, requiring
        # fresh_prob + fresh_prob_is_fresh).
        if exit_context.market_velocity_1h <= flash_crash_velocity():
            self.flash_crash_count = int(self.flash_crash_count or 0) + 1
        else:
            self.flash_crash_count = 0
        if flash_crash_should_fire(
            market_velocity_1h=exit_context.market_velocity_1h,
            divergence_score=exit_context.divergence_score,
            has_probability_authority=True,
            flash_crash_count=self.flash_crash_count,
        ):
            applied.append("flash_crash_trigger")
            self.applied_validations = _dedupe_validations(applied)
            return ExitDecision(
                True,
                (
                    f"FLASH_CRASH_PANIC (velocity={exit_context.market_velocity_1h:.2f}/hr, "
                    f"divergence={exit_context.divergence_score:.2f}, cycles={self.flash_crash_count})"
                ),
                "immediate",
                selected_method=self.selected_method or self.entry_method,
                applied_validations=list(self.applied_validations),
                trigger="FLASH_CRASH_PANIC",
            )

        # Micro-position marker: small fills still need the same live redecision
        # math as every other held position. Returning here hid incident-created
        # negative-edge dust from CI/hold-value exits; keep the breadcrumb but let
        # the downstream economic gates decide hold vs exit.
        if self.effective_cost_basis_usd < 1.0:
            applied.append("micro_position_hold")

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

        # BUG#113 (守護 SD-7) — CI-SEPARATION belief-reversal gate (the 120-min 守護 guarantee:
        # "exit only when the belief CI has SEPARATED below entry, NEVER on a bare/large price
        # move whose CI still overlaps entry"). This lifts the severed screen_exit logic onto the
        # LIVE path. It performs ZERO DB I/O — the CI bounds arrive pre-computed via ExitContext
        # (current_ci = this cycle's fresh bootstrap CI; entry_ci = entry-time snapshot) — so the
        # 2026-05-31 "second world connection inside the SAVEPOINT" deadlock is structurally
        # impossible here. When CI inputs are absent the gate is inert and the legacy flat
        # 2-confirm path below runs unchanged.
        separated = _ci_intervals_separated(exit_context.entry_ci, exit_context.current_ci)
        if separated is not None and exit_context.entry_posterior is not None:
            applied.append("ci_separation_gate")
            current_held = float(exit_context.fresh_prob)
            below = current_held < float(exit_context.entry_posterior) - _CI_SEP_EPS
            if separated and below:
                evidence_edge = conservative_forward_edge(
                    forward_edge,
                    self.entry_ci_width,
                )
                edge_threshold = (
                    buy_no_edge_threshold(self.entry_ci_width)
                    if self.direction == "buy_no"
                    else buy_yes_edge_threshold(self.entry_ci_width)
                )
                applied.append("ci_threshold")
                if exit_context.day0_active and current_held <= 1e-9:
                    if not exit_context.day0_zero_probability_exit_authority:
                        applied.append("day0_zero_probability_exit_authority_blocked")
                    else:
                        sell_value_dominates = self._sell_value_exceeds_hold_value(
                            current_p_posterior=current_held,
                            best_bid=exit_context.best_bid,
                            hours_to_settlement=exit_context.hours_to_settlement,
                            applied=applied,
                            portfolio_positions=exit_context.portfolio_positions,
                            bankroll=exit_context.bankroll,
                        )
                        if sell_value_dominates is True:
                            self.neg_edge_count = 0
                            applied.append("day0_zero_probability_sell_value_dominates")
                            self.applied_validations = _dedupe_validations(applied)
                            return ExitDecision(
                                True,
                                (
                                    "DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES "
                                    f"(entry={float(exit_context.entry_posterior):.4f}, "
                                    f"current={current_held:.4f})"
                                ),
                                selected_method=self.selected_method or self.entry_method,
                                applied_validations=list(self.applied_validations),
                                trigger="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES",
                            )
                if evidence_edge >= edge_threshold:
                    self.neg_edge_count = 0
                    if forward_edge > 0.0:
                        hold_reason = "CI_SEPARATED_POSITIVE_EDGE_HOLD"
                        applied.append("ci_separated_positive_edge_hold")
                    else:
                        hold_reason = "CI_SEPARATED_EDGE_WITHIN_THRESHOLD_HOLD"
                        applied.append("ci_separated_edge_within_threshold_hold")
                    self.applied_validations = _dedupe_validations(applied)
                    return ExitDecision(
                        False,
                        hold_reason,
                        selected_method=self.selected_method or self.entry_method,
                        applied_validations=list(self.applied_validations),
                        trigger=hold_reason,
                    )
                sell_value_dominates = self._sell_value_exceeds_hold_value(
                    current_p_posterior=current_held,
                    best_bid=exit_context.best_bid,
                    hours_to_settlement=exit_context.hours_to_settlement,
                    applied=applied,
                    portfolio_positions=exit_context.portfolio_positions,
                    bankroll=exit_context.bankroll,
                )
                if sell_value_dominates is False:
                    self.neg_edge_count = 0
                    applied.append("ci_separated_hold_value_dominates")
                    self.applied_validations = _dedupe_validations(applied)
                    return ExitDecision(
                        False,
                        "CI_SEPARATED_HOLD_VALUE_DOMINATES",
                        selected_method=self.selected_method or self.entry_method,
                        applied_validations=list(self.applied_validations),
                        trigger="CI_SEPARATED_HOLD_VALUE_DOMINATES",
                    )
                if sell_value_dominates is None:
                    self.neg_edge_count = 0
                    applied.append("ci_separated_exit_context_incomplete_hold")
                    self.applied_validations = _dedupe_validations(applied)
                    return ExitDecision(
                        False,
                        "CI_SEPARATED_EXIT_CONTEXT_INCOMPLETE_HOLD",
                        selected_method=self.selected_method or self.entry_method,
                        applied_validations=list(self.applied_validations),
                        trigger="CI_SEPARATED_EXIT_CONTEXT_INCOMPLETE_HOLD",
                    )
                # Disjoint AND moved against the held side → genuine evidence reversal → EXIT.
                self.neg_edge_count = 0
                applied.append("ci_separated_reversal")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    True,
                    f"CI_SEPARATED_REVERSAL (entry={float(exit_context.entry_posterior):.4f}, current={current_held:.4f})",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="CI_SEPARATED_REVERSAL",
                )
            # CI present but NOT a separated-below reversal: for ordinary positions,
            # a noisy overlapping interval suppresses flat exits. For Day0 the
            # observed-boundary remaining-window CI is often deliberately wide; a
            # terminal hold here prevents the standard EV/consecutive optimizer from
            # ever reacting to the same fresh belief. Keep the overlap as evidence,
            # but let Day0 continue into the normal exit optimizer.
            floor_revoked_decision = _live_floor_revoked_decision()
            if floor_revoked_decision is not None:
                return floor_revoked_decision
            if exit_context.day0_active:
                applied.append("ci_overlap_nonterminal_day0")
            else:
                self.neg_edge_count = 0
                applied.append("ci_overlap_hold")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    "CI_OVERLAP_HOLD",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                    trigger="CI_OVERLAP_HOLD",
                )

        floor_revoked_decision = _live_floor_revoked_decision()
        if floor_revoked_decision is not None:
            return floor_revoked_decision

        # Direction-specific exit logic
        if self.direction == "buy_no":
            return self._buy_no_exit(
                forward_edge,
                current_p_posterior=float(exit_context.fresh_prob),
                current_market_price=float(exit_context.current_market_price),
                best_bid=exit_context.best_bid,
                hours_to_settlement=exit_context.hours_to_settlement,
                day0_active=exit_context.day0_active,
                applied=applied,
                portfolio_positions=exit_context.portfolio_positions,
                bankroll=exit_context.bankroll,
            )
        else:
            best_bid = exit_context.best_bid
            return self._buy_yes_exit(
                forward_edge,
                current_p_posterior=float(exit_context.fresh_prob),
                best_bid=best_bid,
                day0_active=exit_context.day0_active,
                hours_to_settlement=exit_context.hours_to_settlement,
                applied=applied,
                portfolio_positions=exit_context.portfolio_positions,
                bankroll=exit_context.bankroll,
            )

    def _buy_yes_exit(
        self,
        forward_edge: float,
        current_p_posterior: float,
        best_bid: Optional[float] = None,
        day0_active: bool = False,
        hours_to_settlement: Optional[float] = None,
        applied: Optional[list[str]] = None,
        portfolio_positions: tuple = (),
        bankroll: Optional[float] = None,
    ) -> ExitDecision:
        """Standard 2-consecutive EDGE_REVERSAL with EV gate.

        T6.4: when feature_flags.HOLD_VALUE_EXIT_COSTS is enabled, the EV
        gate uses HoldValue.compute_with_exit_costs (fee + time opportunity
        cost) instead of the legacy zero-cost HoldValue.compute. hours_to_
        settlement feeds the time_cost component; when None, time_cost
        collapses to 0.0 as a soft conservative default.

        T6.4-phase2: portfolio_positions + bankroll thread the correlation-
        crowding substrate through to HoldValue.compute_with_exit_costs.
        Each element of portfolio_positions is
        (cluster, effective_cost_basis_usd, trade_id) for OTHER co-held
        positions (self-excluded at _build_exit_context
        layer). Crowding cost defaults to 0.0 via the helper when
        exit_correlation_crowding_rate() is 0.0 (current default).
        """
        applied = list(applied or [])
        if not ExitContext._is_finite(best_bid):
            applied.append("best_bid_unavailable")
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
            applied.append("day0_observation_reversal_nonterminal")
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
        shares = self.effective_shares
        if best_bid is not None and shares > 0:
            applied.append("ev_gate")
            if hold_value_exit_costs_enabled():
                applied.append("hold_value_exit_costs_enabled")
                if hours_to_settlement is None or hours_to_settlement < 0.0:
                    applied.append("hold_value_hours_unknown_time_cost_zero")
                _crowding = _compute_exit_correlation_crowding(
                    this_cluster=self.cluster,
                    portfolio_positions=portfolio_positions,
                    bankroll=bankroll,
                    shares=shares,
                    best_bid=best_bid,
                    crowding_rate=exit_correlation_crowding_rate(),
                )
                if _crowding > 0.0:
                    applied.append("hold_value_correlation_crowding_applied")
                hold_value = HoldValue.compute_with_exit_costs(
                    shares=shares,
                    current_p_posterior=current_p_posterior,
                    best_bid=best_bid,
                    hours_to_settlement=hours_to_settlement,
                    fee_rate=exit_fee_rate(),
                    daily_hurdle_rate=exit_daily_hurdle_rate(),
                    correlation_crowding=_crowding,
                )
            else:
                hold_value = HoldValue.compute(
                    gross_value=shares * current_p_posterior,
                    fee_cost=0.0,
                    time_cost=0.0,
                )
            if shares * best_bid <= hold_value.net_value:
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
        best_bid: Optional[float] = None,
        hours_to_settlement: Optional[float] = None,
        day0_active: bool = False,
        applied: Optional[list[str]] = None,
        portfolio_positions: tuple = (),
        bankroll: Optional[float] = None,
    ) -> ExitDecision:
        """Layer 1: Buy-no has ~87.5% base win rate. Different exit math.

        T6.4: routes the EV gate through HoldValue contract (previously
        bypassed). When feature_flags.HOLD_VALUE_EXIT_COSTS is enabled,
        exit decisions include fee + time opportunity cost. Buy-no sell value
        uses held-token best_bid; current_market_price remains the probability
        / forward-edge input and must not masquerade as executable proceeds.

        T6.4-phase2: portfolio_positions + bankroll thread correlation-
        crowding substrate; defaults preserve pre-phase2 behavior (cost 0.0)
        until exit_correlation_crowding_rate() > 0.0.
        """
        applied = list(applied or [])
        evidence_edge = conservative_forward_edge(forward_edge, self.entry_ci_width)
        edge_threshold = buy_no_edge_threshold(self.entry_ci_width)
        near_threshold = buy_no_ceiling()
        applied.append("ci_threshold")

        if day0_active and evidence_edge < edge_threshold:
            applied.append("day0_observation_gate")
            applied.append("day0_observation_reversal_nonterminal")

        # Near-settlement hold (unless deeply negative). Day0 already passed
        # through the shared settlement-imminent gate in evaluate_exit; do not
        # let this wider buy-NO shortcut bypass the standard Day0 optimizer.
        if (
            not day0_active
            and hours_to_settlement is not None
            and hours_to_settlement < near_settlement_hours()
        ):
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
            if not ExitContext._is_finite(best_bid):
                applied.append("best_bid_unavailable")
                self.applied_validations = _dedupe_validations(applied)
                return ExitDecision(
                    False,
                    "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)",
                    selected_method=self.selected_method or self.entry_method,
                    applied_validations=list(self.applied_validations),
                )
            shares = self.effective_shares
            if shares > 0:
                applied.append("ev_gate")
                if hold_value_exit_costs_enabled():
                    applied.append("hold_value_exit_costs_enabled")
                    if hours_to_settlement is None or hours_to_settlement < 0.0:
                        applied.append("hold_value_hours_unknown_time_cost_zero")
                    _crowding = _compute_exit_correlation_crowding(
                        this_cluster=self.cluster,
                        portfolio_positions=portfolio_positions,
                        bankroll=bankroll,
                        shares=shares,
                        best_bid=best_bid,
                        crowding_rate=exit_correlation_crowding_rate(),
                    )
                    if _crowding > 0.0:
                        applied.append("hold_value_correlation_crowding_applied")
                    hold_value = HoldValue.compute_with_exit_costs(
                        shares=shares,
                        current_p_posterior=current_p_posterior,
                        best_bid=best_bid,
                        hours_to_settlement=hours_to_settlement,
                        fee_rate=exit_fee_rate(),
                        daily_hurdle_rate=exit_daily_hurdle_rate(),
                        correlation_crowding=_crowding,
                    )
                else:
                    hold_value = HoldValue.compute(
                        gross_value=shares * current_p_posterior,
                        fee_cost=0.0,
                        time_cost=0.0,
                    )
                sell_value = shares * best_bid
                if sell_value <= hold_value.net_value:
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
    # bankroll/daily_baseline_total/weekly_baseline_total default to 0.0
    # ("uninitialized — ask bankroll_provider"). The retired config-literal
    # default was the structural-failure source: any caller that constructed a
    # bare PortfolioState() inherited synthetic capital. Live truth must come from
    # src.runtime.bankroll_provider.current(); when that returns None the
    # cycle fails-CLOSED rather than falling back to a config literal.
    positions: list[Position] = field(default_factory=list)
    bankroll: float = 0.0
    updated_at: str = ""
    audit_logging_enabled: bool = False
    daily_baseline_total: float = 0.0
    weekly_baseline_total: float = 0.0
    # Layer 5+6: recently closed positions for reentry/cooldown checks
    recent_exits: list[dict] = field(default_factory=list)
    # T2-C: Tokens to never resurrect (redeemed, expired, manually closed)
    ignored_tokens: list[str] = field(default_factory=list)
    # PR C2 (Finding 3, 2026-05-27): typed review-queue entries for chain-only
    # venue inventory (tokens visible on chain with NO matching local intent).
    # Replaces the synthetic `Position(direction="unknown", ...)` construction
    # in chain_reconciliation. Consumers that gate on chain-only inventory
    # (e.g. cycle_runner._has_quarantined_positions) MUST check both
    # `positions` (legacy synthetic placeholders, still emitted by loader) AND
    # `chain_only_facts` (new typed signal from reconcile). Loader synthesis
    # is removed in PR E once all consumers migrate.
    chain_only_facts: list = field(default_factory=list)
    # P4 (Tier 2.1): when True, DB projection failed and portfolio is empty.
    # Cycle runner must suppress new entries when this flag is set.
    portfolio_loader_degraded: bool = False
    # Phase 5A (B069/B073): truth authority of this state snapshot.
    # "canonical_db"=loaded from authoritative DB projection.
    # "degraded"=DB reachable but projection non-canonical.
    # "unverified"=DB connection failed; callers must not trust this as authority.
    authority: str = "unverified"

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
#
# Slice B1 (PR #19 finding 9, 2026-04-26): canonical set lives in
# src/state/lifecycle_manager.TERMINAL_STATES, derived from
# LEGAL_LIFECYCLE_FOLDS. Imported at module top under the local alias
# `_TERMINAL_POSITION_STATES` for zero call-site churn at L1008/L1343.


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
            filtered["env"] = POSITION_ENV_UNKNOWN
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

    # Bankroll is uninitialized at this load step; live truth is supplied by
    # src.runtime.bankroll_provider.current() in cycle_runner / riskguard.
    # Removed 2026-05-04: previously defaulted to retired config-literal
    # capital. PortfolioState is now position-and-snapshot data only;
    # the bankroll field is overridden by upstream callers when on-chain
    # truth is available, and remains 0.0 (fail-CLOSED in sizing) otherwise.
    bankroll = 0.0
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


_D6_LOCKED_FIELDS = frozenset({"entry_price", "cost_basis_usd", "size_usd", "shares"})


def _load_d6_field(row: dict, field_name: str, default: float = 0.0) -> float:
    """T1BD: Load a D6 locked field from a projection row.

    Emits position_loader_field_defaulted_total{field} when the row does not
    carry the field (None or missing). The row is not silently zero-defaulted
    without telemetry.
    """
    value = row.get(field_name)
    if value is None or value == "":
        _cnt_inc("position_loader_field_defaulted_total", labels={"field": field_name})
        logger.warning(
            "telemetry_counter event=position_loader_field_defaulted_total field=%s",
            field_name,
        )
        return default
    return float(value)


def _runtime_strategy_key_from_projection_row(row: dict) -> str:
    """Repair only the legacy EDLI forecast bridge label that predates strategy certs."""

    strategy_key = str(row.get("strategy_key") or "")
    if strategy_key != "settlement_capture":
        return strategy_key
    if str(row.get("entry_method") or "") != "ens_member_counting":
        return strategy_key
    if str(row.get("direction") or "").strip().lower() != "buy_no":
        return strategy_key
    metric = str(row.get("temperature_metric") or "").strip().lower()

    from src.strategy.strategy_profile import try_get

    profile = try_get("opening_inertia")
    if (
        profile is not None
        and profile.is_runtime_live()
        and profile.is_direction_allowed("buy_no")
        and (not metric or profile.metric_is_live(metric))
    ):
        logger.warning(
            "runtime repaired legacy EDLI forecast strategy label: position_id=%s "
            "settlement_capture -> opening_inertia metric=%s",
            row.get("position_id") or row.get("trade_id") or "",
            metric,
        )
        return "opening_inertia"
    return strategy_key


def _strategy_profile_rejection_for_position(pos: "Position") -> str | None:
    strategy_key = str(getattr(pos, "strategy_key", "") or "").strip()
    if not strategy_key:
        return "STRATEGY_KEY_MISSING"

    from src.strategy.strategy_profile import try_get

    profile = try_get(strategy_key)
    if profile is None:
        return f"STRATEGY_UNKNOWN:{strategy_key}"
    if not profile.is_runtime_live():
        return f"STRATEGY_NOT_RUNTIME_LIVE:{strategy_key}"

    direction = _semantic_value(getattr(pos, "direction", "")).strip().lower()
    if direction and not profile.is_direction_allowed(direction):
        return f"STRATEGY_DIRECTION_BLOCKED:{strategy_key}:direction={direction}"

    metric = str(getattr(pos, "temperature_metric", "") or "").strip().lower()
    if metric and not profile.metric_is_live(metric):
        return f"STRATEGY_METRIC_BLOCKED:{strategy_key}:metric={metric}"

    return None


def _invalid_strategy_review_fact_from_position(pos: "Position") -> ChainOnlyFact | None:
    if not _is_runtime_open_position(pos):
        return None
    reason = _strategy_profile_rejection_for_position(pos)
    if reason is None:
        return None
    token_id = str(getattr(pos, "no_token_id", "") or getattr(pos, "token_id", "") or "")
    if not token_id:
        token_id = f"invalid-strategy:{getattr(pos, 'trade_id', '')}"
    observed_at = str(getattr(pos, "entered_at", "") or getattr(pos, "updated_at", "") or "")
    logger.error(
        "active position blocks new entries due to invalid runtime strategy: "
        "position_id=%s city=%s target_date=%s metric=%s direction=%s strategy_key=%s reason=%s",
        getattr(pos, "trade_id", ""),
        getattr(pos, "city", ""),
        getattr(pos, "target_date", ""),
        getattr(pos, "temperature_metric", ""),
        getattr(pos, "direction", ""),
        getattr(pos, "strategy_key", ""),
        reason,
    )
    return ChainOnlyFact(
        token_id=token_id,
        condition_id=str(getattr(pos, "condition_id", "") or ""),
        size=float(getattr(pos, "shares", 0.0) or 0.0),
        avg_price=float(getattr(pos, "entry_price", 0.0) or 0.0),
        cost_basis=float(getattr(pos, "cost_basis_usd", 0.0) or 0.0),
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        review_state=ChainOnlyReviewState.UNRESOLVED,
        entry_block_scope="position_only",
    )


def _is_open_edli_entry_position_row(row: dict) -> bool:
    phase = str(row.get("phase") or row.get("state") or "").strip()
    if phase not in {
        "pending_entry",
        "pending_tracked",
        "active",
        "entered",
        "holding",
        "day0_window",
        "pending_exit",
    }:
        return False
    trade_id = str(row.get("trade_id") or row.get("position_id") or "")
    if trade_id.startswith("edli"):
        return True
    decision_snapshot_id = str(row.get("decision_snapshot_id") or "")
    if decision_snapshot_id.startswith("ems2-"):
        return True
    return str(row.get("entry_method") or "") == "ens_member_counting"


def _held_token_id_from_position_row(row: dict) -> str:
    direction = str(row.get("direction") or "").strip().lower()
    if direction == "buy_no":
        return str(row.get("no_token_id") or row.get("token_id") or "")
    return str(row.get("token_id") or row.get("no_token_id") or "")


def _edli_event_id_from_execution_decision_id(decision_id: str) -> str:
    parts = str(decision_id or "").split(":")
    if len(parts) >= 2 and parts[0] == "edli_exec_cmd":
        return parts[1]
    return ""


def _attached_schema_names(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    except sqlite3.Error:
        return set()


def _attached_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _entry_receipt_authority_rejection(receipt_json: str | None) -> str | None:
    if not receipt_json:
        return "EDLI_ENTRY_RECEIPT_MISSING"
    try:
        receipt = json.loads(receipt_json)
    except (TypeError, json.JSONDecodeError):
        return "EDLI_ENTRY_RECEIPT_JSON_INVALID"
    if str(receipt.get("q_source") or "").strip() == "":
        return "EDLI_ENTRY_Q_SOURCE_MISSING"
    book = receipt.get("opportunity_book")
    if not isinstance(book, dict):
        return "EDLI_ENTRY_OPPORTUNITY_BOOK_MISSING"
    selected = str(book.get("selected_candidate_id") or "").strip()
    actual = str(book.get("actual_receipt_selected_candidate_id") or "").strip()
    if not selected:
        return "EDLI_ENTRY_OPPORTUNITY_BOOK_SELECTED_MISSING"
    if actual and actual != selected:
        return "EDLI_ENTRY_OPPORTUNITY_BOOK_SELECTION_MISMATCH"
    return None


def _entry_decision_audit_authority_rejection(audit_json: str | None) -> str | None:
    if not audit_json:
        return "EDLI_ENTRY_DECISION_AUDIT_MISSING"
    try:
        audit = json.loads(audit_json)
    except (TypeError, json.JSONDecodeError):
        return "EDLI_ENTRY_DECISION_AUDIT_JSON_INVALID"
    if str(audit.get("strategy_key") or "").strip() == "":
        return "EDLI_ENTRY_DECISION_AUDIT_STRATEGY_KEY_MISSING"
    if str(audit.get("q_source") or "").strip() == "":
        return "EDLI_ENTRY_DECISION_AUDIT_Q_SOURCE_MISSING"
    book = audit.get("opportunity_book")
    if not isinstance(book, dict):
        return "EDLI_ENTRY_DECISION_AUDIT_OPPORTUNITY_BOOK_MISSING"
    selected = str(book.get("selected_candidate_id") or "").strip()
    actual = str(book.get("actual_receipt_selected_candidate_id") or "").strip()
    if not selected:
        return "EDLI_ENTRY_DECISION_AUDIT_OPPORTUNITY_BOOK_SELECTED_MISSING"
    if actual and actual != selected:
        return "EDLI_ENTRY_DECISION_AUDIT_OPPORTUNITY_BOOK_SELECTION_MISMATCH"
    return None


def _actionable_entry_authority_rejection(payload_json: str | None) -> str | None:
    if not payload_json:
        return "EDLI_ENTRY_ACTIONABLE_CERT_MISSING"
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return "EDLI_ENTRY_ACTIONABLE_CERT_JSON_INVALID"
    if str(payload.get("strategy_key") or "").strip() == "":
        return "EDLI_ENTRY_ACTIONABLE_STRATEGY_KEY_MISSING"
    if str(payload.get("q_source") or "").strip() == "":
        return "EDLI_ENTRY_ACTIONABLE_Q_SOURCE_MISSING"
    if not isinstance(payload.get("opportunity_book"), dict):
        return "EDLI_ENTRY_ACTIONABLE_OPPORTUNITY_BOOK_MISSING"
    return None


def _calibration_entry_authority_rejection(payload_json: str | None) -> str | None:
    if not payload_json:
        return "EDLI_ENTRY_CALIBRATION_CERT_MISSING"
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return "EDLI_ENTRY_CALIBRATION_CERT_JSON_INVALID"
    authority = str(payload.get("authority") or "").strip().upper()
    coverage_status = str(payload.get("coverage_status") or "").strip()
    if authority == "IDENTITY_FALLBACK_NO_PLATT_BUCKET":
        return "EDLI_ENTRY_CALIBRATION_IDENTITY_FALLBACK"
    if authority == "DAY0_LIVE_OBSERVATION_HARD_FACT":
        return None
    n_samples_raw = payload.get("n_samples")
    try:
        n_samples = int(n_samples_raw) if n_samples_raw is not None else None
    except (TypeError, ValueError):
        n_samples = None
    if (
        n_samples is not None
        and n_samples <= 0
        and coverage_status != "INSUFFICIENT_DATA"
    ):
        return "EDLI_ENTRY_CALIBRATION_EMPTY_SAMPLE"
    return None


def _entry_proof_rejection_from_evidence(
    *,
    decision_audit_json: str | None = None,
    receipt_json: str | None,
    actionable_payload_json: str | None,
    calibration_payload_json: str | None,
) -> str | None:
    if decision_audit_json:
        audit_rejection = _entry_decision_audit_authority_rejection(decision_audit_json)
        if audit_rejection is not None:
            return audit_rejection
        return None
    actionable_rejection = _actionable_entry_authority_rejection(actionable_payload_json)
    calibration_rejection = _calibration_entry_authority_rejection(calibration_payload_json)
    if receipt_json:
        receipt_rejection = _entry_receipt_authority_rejection(receipt_json)
        if receipt_rejection is not None:
            return receipt_rejection
    if actionable_rejection is not None:
        return actionable_rejection
    if calibration_rejection is not None:
        return calibration_rejection
    return None


def _legacy_entry_audit_gap_is_manageable(decision_proof_occurred_at: str | None) -> bool:
    parsed = _parse_iso_datetime(decision_proof_occurred_at)
    return parsed is not None and parsed < EDLI_DECISION_AUDIT_REQUIRED_FROM


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _query_edli_entry_proof_review_reasons(
    conn: sqlite3.Connection,
    rows: list[dict],
) -> dict[str, str]:
    if not rows:
        return {}
    if "world" not in _attached_schema_names(conn):
        return {
            str(row.get("trade_id") or row.get("position_id") or ""): "EDLI_ENTRY_PROOF_WORLD_DB_UNAVAILABLE"
            for row in rows
            if _is_open_edli_entry_position_row(row)
        }
    required_world_tables = ("edli_no_submit_receipts", "decision_certificates", "edli_live_order_events")
    if any(not _attached_table_exists(conn, "world", table) for table in required_world_tables):
        return {
            str(row.get("trade_id") or row.get("position_id") or ""): "EDLI_ENTRY_PROOF_WORLD_TABLE_MISSING"
            for row in rows
            if _is_open_edli_entry_position_row(row)
        }
    if not _attached_table_exists(conn, "main", "venue_commands"):
        return {
            str(row.get("trade_id") or row.get("position_id") or ""): "EDLI_ENTRY_PROOF_COMMAND_TABLE_MISSING"
            for row in rows
            if _is_open_edli_entry_position_row(row)
        }

    reasons: dict[str, str] = {}
    for row in rows:
        if not _is_open_edli_entry_position_row(row):
            continue
        trade_id = str(row.get("trade_id") or row.get("position_id") or "")
        token_id = _held_token_id_from_position_row(row)
        order_id = str(row.get("order_id") or "")
        command_row = None
        if order_id:
            command_row = conn.execute(
                """
                SELECT decision_id
                  FROM venue_commands
                 WHERE venue_order_id = ?
                   AND intent_kind = 'ENTRY'
                 ORDER BY updated_at DESC, created_at DESC
                 LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        if command_row is None and token_id:
            command_row = conn.execute(
                """
                SELECT decision_id
                  FROM venue_commands
                 WHERE token_id = ?
                   AND intent_kind = 'ENTRY'
                 ORDER BY updated_at DESC, created_at DESC
                 LIMIT 1
                """,
                (token_id,),
            ).fetchone()
        if command_row is None:
            reasons[trade_id] = "EDLI_ENTRY_PROOF_COMMAND_MISSING"
            continue
        event_id = _edli_event_id_from_execution_decision_id(str(command_row["decision_id"] or ""))
        if not event_id:
            reasons[trade_id] = "EDLI_ENTRY_PROOF_EVENT_ID_MISSING"
            continue
        decision_audit_row = conn.execute(
            """
            SELECT
                json_extract(payload_json, '$.decision_audit') AS decision_audit_json,
                occurred_at
              FROM world.edli_live_order_events
             WHERE event_type = 'DecisionProofAccepted'
               AND aggregate_id >= ?
               AND aggregate_id < ?
             ORDER BY event_sequence ASC
             LIMIT 1
            """,
            (f"{event_id}:", f"{event_id}:~"),
        ).fetchone()
        decision_audit_json = (
            str(decision_audit_row["decision_audit_json"])
            if decision_audit_row is not None and decision_audit_row["decision_audit_json"] is not None
            else None
        )
        if decision_audit_row is not None and not decision_audit_json:
            if _legacy_entry_audit_gap_is_manageable(str(decision_audit_row["occurred_at"] or "")):
                logger.warning(
                    "legacy EDLI entry lacks decision_audit but predates audit requirement; "
                    "position remains manageable but settlement attribution is unscorable: "
                    "position_id=%s event_id=%s",
                    trade_id,
                    event_id,
                )
                continue
            reasons[trade_id] = "EDLI_ENTRY_DECISION_AUDIT_MISSING"
            continue
        receipt_row = conn.execute(
            """
            SELECT receipt_json
              FROM world.edli_no_submit_receipts
             WHERE event_id = ?
               AND token_id = ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (event_id, token_id),
        ).fetchone()
        actionable_row = conn.execute(
            """
            SELECT payload_json
              FROM world.decision_certificates
             WHERE certificate_type = 'ActionableTradeCertificate'
               AND semantic_key LIKE ?
               AND json_extract(payload_json, '$.token_id') = ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (f"actionable:{event_id}:%", token_id),
        ).fetchone()
        calibration_row = conn.execute(
            """
            SELECT payload_json
              FROM world.decision_certificates
             WHERE certificate_type = 'CalibrationCertificate'
               AND semantic_key LIKE ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (f"calibration:{event_id}:%",),
        ).fetchone()
        rejection = _entry_proof_rejection_from_evidence(
            decision_audit_json=decision_audit_json,
            receipt_json=str(receipt_row["receipt_json"]) if receipt_row is not None else None,
            actionable_payload_json=str(actionable_row["payload_json"]) if actionable_row is not None else None,
            calibration_payload_json=str(calibration_row["payload_json"]) if calibration_row is not None else None,
        )
        if rejection is not None:
            reasons[trade_id] = rejection
    return reasons


def _invalid_entry_proof_review_fact_from_position(
    pos: "Position",
    *,
    reason: str | None,
) -> ChainOnlyFact | None:
    if reason is None or not _is_runtime_open_position(pos):
        return None
    token_id = str(getattr(pos, "no_token_id", "") or getattr(pos, "token_id", "") or "")
    if not token_id:
        token_id = f"invalid-entry-proof:{getattr(pos, 'trade_id', '')}"
    observed_at = str(getattr(pos, "entered_at", "") or getattr(pos, "updated_at", "") or "")
    logger.error(
        "active EDLI position requires position-only review due to invalid entry proof: "
        "position_id=%s city=%s target_date=%s metric=%s direction=%s strategy_key=%s reason=%s",
        getattr(pos, "trade_id", ""),
        getattr(pos, "city", ""),
        getattr(pos, "target_date", ""),
        getattr(pos, "temperature_metric", ""),
        getattr(pos, "direction", ""),
        getattr(pos, "strategy_key", ""),
        reason,
    )
    return ChainOnlyFact(
        token_id=token_id,
        condition_id=str(getattr(pos, "condition_id", "") or ""),
        size=float(getattr(pos, "shares", 0.0) or 0.0),
        avg_price=float(getattr(pos, "entry_price", 0.0) or 0.0),
        cost_basis=float(getattr(pos, "cost_basis_usd", 0.0) or 0.0),
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        review_state=ChainOnlyReviewState.UNRESOLVED,
        entry_block_scope="position_only",
    )


def _position_from_projection_row(row: dict, *, current_mode: str) -> Position:
    state = str(row.get("state") or "")
    if not state:
        state = _runtime_state_for_portfolio_phase(str(row.get("phase") or ""))
    entered_at = str(row.get("entered_at") or row.get("updated_at") or "")
    order_posted_at = str(row.get("order_posted_at") or entered_at or "")
    day0_entered_at = str(row.get("day0_entered_at") or "") if state == "day0_window" else ""
    runtime_strategy_key = _runtime_strategy_key_from_projection_row(row)
    exit_retry_count = int(row.get("exit_retry_count") or 0)
    next_exit_retry_at = str(row.get("next_exit_retry_at")) if row.get("next_exit_retry_at") else None
    runtime_exit_state = str(row.get("exit_state") or "")
    if (
        not runtime_exit_state
        and state == "pending_exit"
        and str(row.get("order_status") or "") == "backoff_exhausted"
    ):
        runtime_exit_state = "backoff_exhausted"
    if (
        not runtime_exit_state
        and state == "pending_exit"
        and str(row.get("order_status") or "")
        in {"exit_intent", "sell_placed", "sell_pending", "retry_pending"}
    ):
        runtime_exit_state = str(row.get("order_status") or "")
    if (
        not runtime_exit_state
        and state == "pending_exit"
        and exit_retry_count > 0
        and next_exit_retry_at
    ):
        runtime_exit_state = "retry_pending"
    payload = dict(
        trade_id=str(row.get("trade_id") or row.get("position_id") or ""),
        market_id=str(row.get("market_id") or ""),
        city=str(row.get("city") or ""),
        cluster=str(row.get("cluster") or ""),
        target_date=str(row.get("target_date") or ""),
        bin_label=str(row.get("bin_label") or ""),
        direction=str(row.get("direction") or "unknown"),
        unit=str(row.get("unit") or "F"),
        # B074 [YELLOW / flag for §7c architect sign-off]: the canonical
        # projection row may not carry env. Previously we stamped the
        # current runtime mode, which destroyed the provenance of which
        # env originally created the position. Preserve the row's env when
        # present; fall back to the 'unknown_env' sentinel otherwise and
        # let downstream authority consumers mark the row UNVERIFIED.
        env=str(row.get("env") or POSITION_ENV_UNKNOWN),
        size_usd=_load_d6_field(row, "size_usd"),
        shares=_load_d6_field(row, "shares"),
        cost_basis_usd=_load_d6_field(row, "cost_basis_usd"),
        entry_price=_load_d6_field(row, "entry_price"),
        p_posterior=float(row.get("p_posterior") or 0.0),
        entry_ci_width=float(row.get("entry_ci_width") or 0.0),
        # Exit-retry persistence (2026-06-12): reload the bounded-backoff state
        # so MAX_EXIT_RETRIES -> backoff_exhausted is reachable across cycles.
        exit_retry_count=exit_retry_count,
        next_exit_retry_at=next_exit_retry_at,
        entered_at=entered_at if state != "pending_tracked" else "",
        day0_entered_at=day0_entered_at,
        decision_snapshot_id=str(row.get("decision_snapshot_id") or ""),
        entry_method=str(row.get("entry_method") or ""),
        strategy_key=runtime_strategy_key,
        strategy=runtime_strategy_key,
        edge_source=str(row.get("edge_source") or ""),
        discovery_mode=str(row.get("discovery_mode") or ""),
        state=state,
        order_id=str(row.get("order_id") or ""),
        order_status=str(row.get("order_status") or ""),
        order_posted_at=order_posted_at,
        chain_state=str(row.get("chain_state") or ""),
        exit_state=runtime_exit_state,
        exit_reason=str(row.get("exit_reason") or ""),
        last_monitor_prob=row.get("last_monitor_prob"),
        last_monitor_prob_is_fresh=bool(row.get("last_monitor_prob_is_fresh") or False),
        last_monitor_edge=row.get("last_monitor_edge"),
        last_monitor_market_price=row.get("last_monitor_market_price"),
        last_monitor_market_price_is_fresh=bool(
            row.get("last_monitor_market_price_is_fresh") or False
        ),
        admin_exit_reason=str(row.get("admin_exit_reason") or ""),
        entry_fill_verified=bool(row.get("entry_fill_verified", False)),
        # PR #352 (Part-5 audit Finding 1): the durable projection stores chain
        # observation timestamps under chain_seen_at / chain_absence_at; the
        # runtime Position carries them as chain_verified_at /
        # last_chain_absence_observed_at. Without this translation a chain-synced
        # position reloads with empty chain_verified_at and classify_chain_state()
        # mis-reads it as CHAIN_UNKNOWN — blocking a legitimate void after
        # restart. Prefer the legacy runtime name if present, else the durable
        # projection column.
        chain_verified_at=str(row.get("chain_verified_at") or row.get("chain_seen_at") or ""),
        last_chain_absence_observed_at=str(
            row.get("last_chain_absence_observed_at") or row.get("chain_absence_at") or ""
        ),
        chain_shares=float(row.get("chain_shares") or 0.0),
        # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
        # economics round-trip via position_current so a balance-only
        # rescued position survives daemon restart with the correct
        # exposure on `effective_exposure()`.
        chain_avg_price=float(row.get("chain_avg_price") or 0.0),
        chain_cost_basis_usd=float(row.get("chain_cost_basis_usd") or 0.0),
        fill_authority=str(row.get("fill_authority") or FILL_AUTHORITY_NONE),
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
        if not row.get("metric_ready", False):
            continue
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


def _chain_only_fact_from_row(row: dict) -> ChainOnlyFact:
    """Build a ChainOnlyFact from a token_suppression row of reason
    `chain_only_quarantined` (PR E2 replacement for the legacy
    `_chain_only_quarantine_position_from_row` synthetic-Position path).

    The fact carries the same identity + economics the synthetic Position
    used to carry; consumers read it from `portfolio.chain_only_facts`
    instead of `portfolio.positions`.
    """
    token_id = str(row.get("token_id") or "")
    evidence: dict = {}
    try:
        evidence = json.loads(str(row.get("evidence_json") or "{}"))
    except (TypeError, json.JSONDecodeError):
        evidence = {}
    shares = float(evidence.get("size") or evidence.get("chain_shares") or 0.0)
    avg_price = float(evidence.get("avg_price") or 0.0)
    cost = float(evidence.get("cost") or (shares * avg_price))
    first_seen = str(
        evidence.get("first_seen_at")
        or row.get("created_at")
        or row.get("updated_at")
        or ""
    )
    last_seen = str(row.get("updated_at") or first_seen)
    condition_id = str(row.get("condition_id") or evidence.get("condition_id") or "")
    review_state = _derive_chain_only_review_state(
        suppression_reason=str(row.get("suppression_reason") or "chain_only_quarantined"),
        first_seen_at=first_seen,
    )
    return ChainOnlyFact(
        token_id=token_id,
        condition_id=condition_id,
        size=shares,
        avg_price=avg_price,
        cost_basis=cost,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        review_state=review_state,
        entry_block_scope=str(row.get("entry_block_scope") or "global"),
    )


def _derive_chain_only_review_state(
    *,
    suppression_reason: str,
    first_seen_at: str,
    now: Optional[datetime] = None,
) -> ChainOnlyReviewState:
    """PR D1 (Finding D1, Part-2 audit, 2026-05-27): derive review lifecycle
    state for a chain-only token from its underlying suppression row +
    elapsed time since first detection.

    Existing token_suppression schema already encodes the operator-side
    transitions via `suppression_reason`:
      `chain_only_quarantined`     → unresolved (or expired after 48h)
      `operator_quarantine_clear`  → resolved (operator cleared)
      `settled_position`           → resolved (token settled, no longer chain-only)

    The 48h review window is a SOFT escalation marker — it does NOT clear
    the fact, it flips UNRESOLVED → EXPIRED so ops dashboards can surface
    chain-only inventory that has lingered past triage SLA. Expired review
    debt does not freeze unrelated new entries; only operator action
    (suppression_reason flip) actually resolves the fact.
    """
    if suppression_reason in ("operator_quarantine_clear", "settled_position"):
        return ChainOnlyReviewState.RESOLVED
    if suppression_reason != "chain_only_quarantined":
        # Defensive: any future / unknown reason is treated as unresolved so the
        # gate fail-safe holds.
        return ChainOnlyReviewState.UNRESOLVED
    if not first_seen_at:
        return ChainOnlyReviewState.UNRESOLVED
    try:
        first_seen_dt = datetime.fromisoformat(first_seen_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ChainOnlyReviewState.UNRESOLVED
    _now = now if now is not None else datetime.now(timezone.utc)
    hours_elapsed = (_now - first_seen_dt).total_seconds() / 3600.0
    if hours_elapsed > CHAIN_ONLY_REVIEW_WINDOW_HOURS:
        return ChainOnlyReviewState.EXPIRED
    return ChainOnlyReviewState.UNRESOLVED


# PR E2 (Finding 3, 2026-05-27): the legacy `_chain_only_quarantine_position_from_row`
# constructor was DELETED. Its callers in `load_portfolio` now use
# `_chain_only_fact_from_row` (defined above) to emit typed
# `ChainOnlyFact` review-queue entries on `PortfolioState.chain_only_facts`
# instead of synthetic `Position(direction="unknown")` rows on
# `PortfolioState.positions`. The cycle entry gate
# `_has_quarantined_positions` consults both signals (see PR C2).


def load_portfolio(path: Optional[Path] = None) -> PortfolioState:
    """Load portfolio DB-first, with explicit JSON fallback only when projection is unavailable."""
    path = path or POSITIONS_PATH

    current_mode = get_mode()
    # Bankroll uninitialized at load — live truth is supplied by
    # src.runtime.bankroll_provider.current() in cycle_runner / riskguard.
    # Removed 2026-05-04: previously defaulted to retired config-literal
    # capital. PortfolioState carries 0.0 when canonical metadata is
    # absent; sizing math fails-CLOSED, riskguard returns DATA_DEGRADED.
    bankroll = 0.0

    from src.state.db import (
        ZEUS_WORLD_DB_PATH,
        get_connection,
        get_trade_connection_with_world,
        query_chain_only_quarantine_rows,
        query_authoritative_settlement_rows,
        query_portfolio_loader_view,
        query_token_suppression_tokens,
    )

    mode_override = None
    stem = path.stem
    if stem.startswith("positions-"):
        candidate_mode = stem.split("positions-", 1)[1]
        if candidate_mode in {"live", "test"}:  # only live and test are valid modes
            mode_override = candidate_mode
    elif path == POSITIONS_PATH:
        mode_override = current_mode

    try:
        # v4 plan §AX3: portfolio loader runs in the live cycle path.
        trade_db = path.parent / "zeus_trades.db"
        if trade_db.exists():
            conn = get_connection(trade_db, write_class="live")
        elif mode_override is not None:
            conn = get_trade_connection_with_world(write_class="live")
        else:
            conn = get_connection(path.parent / "zeus.db", write_class="live")
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
            authority="unverified",
        )

    settlement_rows: list[dict] = []
    ignored_tokens: list[str] = []
    chain_only_quarantines: list[dict] = []
    entry_proof_review_reasons: dict[str, str] = {}
    try:
        attached = _attached_schema_names(conn)
        if "world" not in attached and ZEUS_WORLD_DB_PATH.exists():
            try:
                conn.execute("ATTACH DATABASE ? AS world", (str(ZEUS_WORLD_DB_PATH),))
            except sqlite3.OperationalError:
                logger.warning(
                    "load_portfolio could not attach world DB for EDLI entry-proof audit",
                    exc_info=True,
                )
        snapshot = query_portfolio_loader_view(conn)
        entry_proof_review_reasons = _query_edli_entry_proof_review_reasons(
            conn,
            list(snapshot.get("positions", [])),
        )
        ignored_tokens = query_token_suppression_tokens(conn)
        chain_only_quarantines = query_chain_only_quarantine_rows(conn)
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
        # PR E2 (Finding 3, 2026-05-27): chain-only quarantine rows now
        # populate `chain_only_facts` as typed ChainOnlyFact entries instead
        # of synthesizing fake Position objects. `_has_quarantined_positions`
        # in cycle_runner already consults this list (since PR C2), so the
        # entry gate continues to fire on these rows.
        degraded_facts = [
            _chain_only_fact_from_row(row)
            for row in chain_only_quarantines
        ]
        return PortfolioState(
            positions=[],
            bankroll=bankroll,
            daily_baseline_total=bankroll,
            weekly_baseline_total=bankroll,
            ignored_tokens=ignored_tokens,
            chain_only_facts=degraded_facts,
            portfolio_loader_degraded=True,
            authority="degraded",
        )

    # POISON-ROW CONTAINMENT (2026-06-12): one row whose enum coercion fails
    # must not kill the WHOLE portfolio load — the first live firing of the
    # chain-truth void wrote a chain_state outside the then-current enum and
    # every RiskGuard tick crashed here, stopping risk attestations entirely
    # (stale -> RED -> 1100+ false RISK_GUARD_BLOCKED). A poison row is
    # quarantined LOUDLY (ERROR log) and skipped; the healthy rest of the
    # portfolio keeps risk management alive. This contains coercion defects,
    # it never hides them: the log line carries the row identity and error.
    positions = []
    for row in snapshot.get("positions", []):
        try:
            positions.append(
                _position_from_projection_row(row, current_mode=current_mode)
            )
        except Exception as exc:  # noqa: BLE001 — poison row, contained loudly
            logger.error(
                "load_portfolio: POISON projection row quarantined (position_id=%s "
                "city=%s phase=%s chain_state=%s): %s",
                row.get("position_id") or row.get("trade_id"),
                row.get("city"),
                row.get("phase"),
                row.get("chain_state"),
                exc,
            )
    represented_tokens = {
        token
        for pos in positions
        for token in (getattr(pos, "token_id", ""), getattr(pos, "no_token_id", ""))
        if token
    }
    # PR E2 (Finding 3, 2026-05-27): canonical-path chain-only quarantine
    # rows that are NOT already represented by a real local position become
    # typed ChainOnlyFact review-queue entries instead of synthetic
    # Position objects.
    chain_only_facts = [
        _chain_only_fact_from_row(row)
        for row in chain_only_quarantines
        if str(row.get("token_id") or "") not in represented_tokens
    ]
    chain_only_facts.extend(
        fact
        for fact in (
            _invalid_strategy_review_fact_from_position(pos)
            for pos in positions
        )
        if fact is not None
    )
    chain_only_facts.extend(
        fact
        for fact in (
            _invalid_entry_proof_review_fact_from_position(
                pos,
                reason=entry_proof_review_reasons.get(str(getattr(pos, "trade_id", "") or "")),
            )
            for pos in positions
        )
        if fact is not None
    )
    deduped_chain_only_facts: list[ChainOnlyFact] = []
    seen_chain_only_keys: set[tuple[str, str]] = set()
    for fact in chain_only_facts:
        key = (str(fact.token_id or ""), str(fact.condition_id or ""))
        if key in seen_chain_only_keys:
            continue
        seen_chain_only_keys.add(key)
        deduped_chain_only_facts.append(fact)
    chain_only_facts = deduped_chain_only_facts
    return PortfolioState(
        positions=positions,
        bankroll=bankroll,
        updated_at="",
        audit_logging_enabled=True,
        daily_baseline_total=bankroll,
        weekly_baseline_total=bankroll,
        recent_exits=_canonical_recent_exits_from_settlement_rows(settlement_rows),
        ignored_tokens=ignored_tokens,
        chain_only_facts=chain_only_facts,
        authority="canonical_db",
    )


def save_portfolio(
    state: PortfolioState,
    path: Optional[Path] = None,
    *,
    last_committed_artifact_id: Optional[int] = None,
    source: str = "internal",
) -> None:
    """Atomic write: write to tmp, then os.replace(). Spec: atomic write pattern.

    last_committed_artifact_id: when provided, written into the JSON payload
    as "last_committed_artifact_id" for DT#1 / INV-17 stale-detection (D5).

    source: Phase 9C B3 observability hook (DT#6 §B Interpretation B). Tags
    the persistence event with the caller's origin so the JSON audit
    trail shows what drove the write. Convention:
      - "internal" (default): normal cycle housekeeping
      - "reconciliation": chain/CLOB reconciliation write
      - "fill_event": order-fill / exit-fill event
      - "settlement": settlement terminalization
      - "admin": operator-manual intervention
    NO runtime enforcement of the DT#6 §B "external-authority origin"
    rule — this is caller-side discipline logged to the JSON for audit.
    Future phase may add a `source: Literal[...]` contract + runtime check.
    """
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
        if _semantic_value(getattr(p, "state", "")).strip().lower() not in _TERMINAL_POSITION_STATES
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
    if last_committed_artifact_id is not None:
        data["last_committed_artifact_id"] = last_committed_artifact_id
    # Phase 9C B3: record save source for audit trail (observability only;
    # no runtime enforcement of DT#6 §B "external-authority origin" rule).
    data["save_source"] = str(source)
    data = annotate_truth_payload(
        data,
        path,
        generated_at=state.updated_at,
        authority=_TRUTH_AUTHORITY_MAP.get(state.authority, TruthAuthority.UNVERIFIED),
    )

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
    if pos.cost_basis_usd <= 0 and not pos.is_pending_entry_without_fill_authority:
        pos.cost_basis_usd = pos.size_usd

    for existing in state.positions:
        if pos.order_id and existing.order_id and pos.order_id == existing.order_id:
            authority_regression = (
                existing.has_fill_economics_authority
                and (
                    not pos.has_fill_economics_authority
                    or FILL_AUTHORITY_RANK.get(pos.fill_authority, 0)
                    < FILL_AUTHORITY_RANK.get(existing.fill_authority, 0)
                    or float(pos.shares_filled or 0.0)
                    < float(existing.shares_filled or 0.0)
                    or float(pos.filled_cost_basis_usd or 0.0)
                    < float(existing.filled_cost_basis_usd or 0.0)
                )
            )
            protected_fields = frozenset()
            if authority_regression:
                protected_fields = _D6_LOCKED_FIELDS | frozenset(
                    {
                        "entry_economics_authority",
                        "fill_authority",
                        "shares_filled",
                        "filled_cost_basis_usd",
                        "entry_price_avg_fill",
                        "entry_fill_verified",
                        "order_status",
                        "state",
                        "entered_at",
                    }
                )
            for field_name, value in asdict(pos).items():
                if field_name in protected_fields:
                    continue
                if value not in (None, "", 0, 0.0) or getattr(existing, field_name) in (None, "", 0, 0.0):
                    setattr(existing, field_name, value)
            if authority_regression:
                existing.applied_validations = _dedupe_validations(
                    list(existing.applied_validations)
                    + ["same_order_fill_authority_regression_blocked"]
                )
                existing.shares = existing.effective_shares
                existing.cost_basis_usd = existing.effective_cost_basis_usd
                existing.size_usd = existing.effective_cost_basis_usd
            return

    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    for existing in state.positions:
        if pos.state == "pending_tracked" or existing.state == "pending_tracked":
            continue
        existing_tid = existing.token_id if existing.direction == "buy_yes" else existing.no_token_id
        if tid and existing_tid == tid and existing.direction == pos.direction:
            existing_fill_grade = existing.has_fill_economics_authority
            pos_fill_grade = pos.has_fill_economics_authority
            if existing_fill_grade != pos_fill_grade:
                logger.warning(
                    "DEDUP/LEDGER: preserving separate %s %s slices for token=%s "
                    "because fill authority differs (existing=%s incoming=%s)",
                    pos.direction,
                    pos.bin_label,
                    tid,
                    getattr(existing, "fill_authority", ""),
                    getattr(pos, "fill_authority", ""),
                )
                state.positions.append(pos)
                return
            pos_open_shares = pos.effective_shares
            pos_open_cost = pos.effective_cost_basis_usd
            # Append-only virtual ledger projection
            logger.warning(
                "DEDUP/LEDGER: appending duplicate %s %s fill into existing %s %s; "
                "entry context from new position is preserved only inside nested_fills "
                "(entered_at=%s entry_price=%.4f)",
                pos.direction,
                pos.bin_label,
                existing.trade_id,
                existing.state,
                pos.entered_at,
                pos.entry_price,
            )
            existing.nested_fills.append(pos)
            existing.size_usd += pos_open_cost
            existing.shares += pos_open_shares
            existing.cost_basis_usd += pos_open_cost
            if existing_fill_grade and pos_fill_grade:
                existing.shares_filled += pos_open_shares
                existing.filled_cost_basis_usd += pos_open_cost
                if existing.shares_filled > 0:
                    existing.entry_price_avg_fill = (
                        existing.filled_cost_basis_usd / existing.shares_filled
                    )
            if existing.has_fill_economics_authority:
                existing.shares = existing.effective_shares
                existing.cost_basis_usd = existing.effective_cost_basis_usd
                existing.size_usd = existing.effective_cost_basis_usd
            if existing.effective_shares > 0:
                existing.entry_price = existing.effective_cost_basis_usd / existing.effective_shares
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


INACTIVE_RUNTIME_STATES = frozenset(
    set(_TERMINAL_POSITION_STATES) | {"economically_closed"}
)
NO_EXPOSURE_CHAIN_STATES = NO_CURRENT_MONEY_RISK_CHAIN_STATES
_POSITIVE_CHAIN_EXPOSURE_EPS = 1e-6


def _positive_chain_exposure_shares(pos: "Position") -> float:
    try:
        value = float(getattr(pos, "chain_shares", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value) or value <= _POSITIVE_CHAIN_EXPOSURE_EPS:
        return 0.0
    return value


def _semantic_value(value: object) -> str:
    if hasattr(value, "value"):
        value = getattr(value, "value")
    return str(value or "")


def _is_runtime_open_position(pos: Position) -> bool:
    state = _semantic_value(getattr(pos, "state", ""))
    chain_state = _semantic_value(getattr(pos, "chain_state", ""))
    chain_shares = _positive_chain_exposure_shares(pos)
    if (
        chain_shares > 0.0
        and (
            state == "quarantined"
            and has_current_money_risk_chain_state(chain_state)
            or (not state and chain_state in REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES)
        )
    ):
        return True
    no_exposure_chain_state = chain_state in NO_EXPOSURE_CHAIN_STATES
    if state == "pending_exit" and chain_shares > 0.0:
        no_exposure_chain_state = False
    local_projection_without_chain_exposure = (
        chain_state == VenueVisibilityStatus.LOCAL_ONLY.value
        and chain_shares <= 0.0
    )
    return (
        state not in INACTIVE_RUNTIME_STATES
        and not no_exposure_chain_state
        and not local_projection_without_chain_exposure
    )


def _runtime_open_exposure_usd(pos: Position) -> float:
    return float(getattr(pos, "effective_cost_basis_usd", 0.0) or 0.0)


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
        pos.exit_state = "sell_filled"
        pos.next_exit_retry_at = ""
        pos.exit_retry_count = 0
        pos.last_exit_error = ""
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


def _project_d6_field(pos: "Position", field_name: str, chain_value: float, fill_authority_value: float) -> float:
    """T1BD: For corrected-eligible positions, use FillAuthority value in projection row.

    If the position is corrected_executable_economics_eligible and the chain-context
    value differs from the FillAuthority-derived value, emit a telemetry counter and
    return the FillAuthority value. Legacy positions pass through unchanged.
    """
    if not getattr(pos, "corrected_executable_economics_eligible", False):
        return chain_value
    if fill_authority_value is not None and fill_authority_value != chain_value:
        _cnt_inc("position_projection_field_dropped_total", labels={"field": field_name})
        logger.warning(
            "telemetry_counter event=position_projection_field_dropped_total field=%s",
            field_name,
        )
        return fill_authority_value
    return chain_value


def _track_exit(state: PortfolioState, pos: Position) -> None:
    """Track exit for reentry/cooldown checks AND replay auditability.

    CRITICAL: All fields required by equity/report replay consumers must be
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
        # T1BD: for corrected-eligible positions, project FillAuthority-derived
        # D6 values and emit telemetry counters.
        "entry_price": _project_d6_field(pos, "entry_price", pos.entry_price, pos.entry_price_avg_fill),
        "size_usd": _project_d6_field(pos, "size_usd", pos.size_usd, pos.filled_cost_basis_usd),
        "target_notional_usd": pos.target_notional_usd,
        "submitted_notional_usd": pos.submitted_notional_usd,
        "filled_cost_basis_usd": pos.filled_cost_basis_usd,
        "entry_price_submitted": pos.entry_price_submitted,
        "entry_price_avg_fill": pos.entry_price_avg_fill,
        "shares_submitted": pos.shares_submitted,
        "shares_filled": pos.shares_filled,
        "shares_remaining": pos.shares_remaining,
        "entry_cost_basis_id": pos.entry_cost_basis_id,
        "entry_cost_basis_hash": pos.entry_cost_basis_hash,
        "entry_economics_authority": pos.entry_economics_authority,
        "fill_authority": pos.fill_authority,
        "pricing_semantics_id": pos.pricing_semantics_id,
        "execution_cost_basis_version": pos.execution_cost_basis_version,
        "corrected_executable_economics_eligible": pos.corrected_executable_economics_eligible,
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
        conn = None
        try:
            from src.state.db import get_trade_connection_with_world, log_trade_exit
            # v4 plan §AX3: trade exit audit = LIVE (runtime exit path).
            conn = get_trade_connection_with_world(write_class="live")
            log_trade_exit(conn, pos)
            # INFO(DT#1): This commit is exempt from the commit_then_export
            # choke point. The exit audit row is itself the authoritative
            # record of the exit event, not a derived export. Durability
            # must survive a subsequent cycle crash or JSON write failure.
            conn.commit()
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.warning("Error logging trade exit to db: %s", e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass



def get_open_positions(state: PortfolioState, chain_view=None) -> list[Position]:
    """Return the runtime-open positions for a PortfolioState.

    PR D1 (Finding 4, 2026-05-27): this helper is now PURE. The previous
    `chain_view` merge branch silently mutated `pos.shares`, `pos.entry_price`,
    and `pos.chain_state = "synced"` without appending a canonical
    `position_events` row, which violated the "append before projection
    mutation" law and let intra-process state diverge from durable
    projection.

    Any chain↔local size/price correction must now flow through
    `src/state/chain_reconciliation.py` so a canonical
    `CHAIN_SIZE_CORRECTED` / `VENUE_POSITION_OBSERVED` event accompanies
    the projection change. The `chain_view` parameter is preserved for
    API backwards compatibility but no longer triggers any mutation —
    callers wishing to overlay chain-derived economics MUST construct
    that overlay themselves (e.g. an explicit
    `PositionProjectionWithVenueOverlay` value object) without writing
    back into `state.positions`.

    Returns a filtered list of positions whose runtime state is "open"
    per `_is_runtime_open_position`.
    """
    return [p for p in state.positions if _is_runtime_open_position(p)]


def total_exposure_usd(state: PortfolioState) -> float:
    """Total open exposure in USD."""
    return sum(
        _runtime_open_exposure_usd(p)
        for p in state.positions
        if _is_runtime_open_position(p)
    )


def portfolio_heat_for_bankroll(state: PortfolioState, bankroll: float) -> float:
    """Portfolio heat against an explicit entry bankroll/cap."""
    if bankroll <= 0:
        return 0.0
    return total_exposure_usd(state) / bankroll


def city_exposure_for_bankroll(state: PortfolioState, city: str, bankroll: float) -> float:
    """City exposure against an explicit entry bankroll/cap."""
    if bankroll <= 0:
        return 0.0
    total = sum(
        _runtime_open_exposure_usd(p)
        for p in state.positions
        if p.city == city and _is_runtime_open_position(p)
    )
    return total / bankroll


def correlated_committed_usd(
    state: PortfolioState,
    *,
    new_city: str,
    extra_reserved: list[tuple[str, float]] | None = None,
) -> float:
    """Correlation-weighted committed capital (USD) for a new bet in ``new_city``.

    Task #107 (portfolio/multi Kelly), design §3(a) — the SINGLE structural
    decision: "committed capital = correlation-weighted open + in-flight
    exposure". The live EDLI reactor then sizes the new bet against
    ``effective_bankroll(B, this)`` so simultaneous fractional-Kelly stakes
    can never sum past ``B·f_cap``.

    Computes ``Σ_i  effective_cost_basis_usd(p_i) · get_correlation(new_city,
    p_i.city)`` over all runtime-open positions, PLUS the same-cycle
    ``extra_reserved`` ``(city, usd)`` tuples weighted identically. Reuses the
    existing primitives verbatim (no parallel system — iron rule 4):

      - ``get_open_positions`` / ``_is_runtime_open_position`` — the open set.
      - ``_runtime_open_exposure_usd`` — per-position committed USD
        (``effective_cost_basis_usd``; 0.0 for pending-without-fill).
      - ``get_correlation`` (correlation.py) — pairwise city correlation in
        [0,1]; self-correlation (same city, e.g. a sibling MECE bin) = 1.0, so
        same-family bins are summed at FULL weight (MECE-safe — competing
        partitions of one event are never sized as independent bets).
        Distant cities decay to the 0.10 haversine floor.

    ``extra_reserved`` carries the same-cycle in-flight reservations (INV-K7):
    a just-emitted EDLI entry is ``PENDING_TRACKED`` without fill authority, so
    its ``effective_cost_basis_usd`` is 0.0 and it is invisible to
    ``load_portfolio()`` until reconciled. The reactor accumulates each
    accepted Kelly stake as a ``(city, usd)`` reservation and passes it here so
    the next same-cycle bet nets it — otherwise the intra-cycle budget is
    breached.

    Returns a non-negative USD figure. Never amplifies anything downstream:
    ``effective_bankroll`` only ever subtracts this from the bankroll.
    """
    total = 0.0
    for pos in get_open_positions(state):
        committed = _runtime_open_exposure_usd(pos)
        if committed <= 0.0:
            continue
        corr = get_correlation(new_city, pos.city)
        total += committed * corr
    if extra_reserved:
        for reserved_city, reserved_usd in extra_reserved:
            usd = float(reserved_usd)
            if usd <= 0.0:
                continue
            corr = get_correlation(new_city, str(reserved_city))
            total += usd * corr
    return total


@dataclass(frozen=True)
class ClusterExposureResult:
    gross_heat: float
    variance_heat: float | None
    method: str
    fallback_reason: str | None = None

    @property
    def policy_heat(self) -> float:
        if self.variance_heat is None:
            return self.gross_heat
        return max(self.gross_heat, self.variance_heat)


def cluster_exposure_result_for_bankroll(
    state: PortfolioState,
    cluster: str,
    bankroll: float,
    *,
    regime_correlation_store: "Optional[Any]" = None,
    regime: "Optional[Any]" = None,
    cities: "Optional[list[str]]" = None,
) -> ClusterExposureResult:
    """Structured cluster exposure with separate gross and variance heat."""

    if bankroll <= 0:
        return ClusterExposureResult(
            gross_heat=0.0,
            variance_heat=None,
            method="gross_notional",
            fallback_reason="bankroll_non_positive",
        )

    open_positions = [
        p for p in state.positions
        if p.cluster == cluster and _is_runtime_open_position(p)
    ]
    if not open_positions:
        return ClusterExposureResult(
            gross_heat=0.0,
            variance_heat=None,
            method="gross_notional",
        )

    total_notional = sum(_runtime_open_exposure_usd(p) for p in open_positions)
    gross_heat = total_notional / bankroll

    if regime_correlation_store is None or regime is None or cities is None:
        return ClusterExposureResult(
            gross_heat=gross_heat,
            variance_heat=None,
            method="gross_notional",
            fallback_reason="missing_regime_context",
        )

    try:
        from src.contracts.weather_regime_tag import WeatherRegimeTag as _WRT

        if regime is _WRT.UNKNOWN:
            return ClusterExposureResult(
                gross_heat=gross_heat,
                variance_heat=None,
                method="gross_notional",
                fallback_reason="unknown_regime",
            )
        sigma = regime_correlation_store.get(regime, cities)
        _city_notional: dict[str, float] = {}
        for _p in open_positions:
            if hasattr(_p, "city"):
                _city_notional[_p.city] = (
                    _city_notional.get(_p.city, 0.0)
                    + _runtime_open_exposure_usd(_p)
                )
        import numpy as _np

        wv = _np.array([_city_notional.get(c, 0.0) / bankroll for c in cities], dtype=float)
        variance = float(wv @ sigma @ wv)
        variance_heat = math.sqrt(max(variance, 0.0))
        return ClusterExposureResult(
            gross_heat=gross_heat,
            variance_heat=variance_heat,
            method="max_gross_variance",
        )
    except Exception as exc:  # noqa: BLE001
        return ClusterExposureResult(
            gross_heat=gross_heat,
            variance_heat=None,
            method="gross_notional",
            fallback_reason=f"variance_context_unavailable:{type(exc).__name__}",
        )


def cluster_exposure_for_bankroll(
    state: PortfolioState,
    cluster: str,
    bankroll: float,
    *,
    regime_correlation_store: "Optional[Any]" = None,
    regime: "Optional[Any]" = None,
    cities: "Optional[list[str]]" = None,
) -> float:
    """Cluster exposure policy heat against an explicit entry bankroll/cap.

    Backward-compatible float wrapper. The policy heat is conservative:
    ``max(gross_heat, variance_heat)`` when variance context exists, otherwise
    gross notional heat.
    """

    return cluster_exposure_result_for_bankroll(
        state,
        cluster,
        bankroll,
        regime_correlation_store=regime_correlation_store,
        regime=regime,
        cities=cities,
    ).policy_heat




# --- Churn defense: Layer 7 (honest dedup) ---
# Layers 5 (is_reentry_blocked, 20-min reversal time-ban) and 6 (is_token_on_cooldown,
# 1-hr post-fail time-ban) DELETED 2026-06-14 (operator no-caps law: time-bans are not
# derived from belief/quote/edge/Kelly/arm). Layer 7 below (same-token / same-range
# inflight dedup) is honest and STAYS. recent_exits + _track_exit are retained (replay/
# audit). NoTradeReason.REENTRY_BLOCKED / TOKEN_COOLDOWN enum members are left in place
# (schema-fingerprint / no_trade_events CHECK-pin coupling) — they simply have no emitter.


def has_same_city_range_open(state: PortfolioState, city: str, bin_label: str) -> bool:
    """Layer 7: Block same city+range across different dates."""
    return any(
        p.city == city
        and p.bin_label == bin_label
        and _is_runtime_open_position(p)
        for p in state.positions
    )


def has_same_token_open(state: PortfolioState, token_id: str) -> bool:
    """Layer 7 (v2) snapshot fallback: block re-entry on a token already held in any
    non-terminal state. Keys by token_id (outcome-specific, direction-specific) not
    city+bin_label text. Used when conn is None (paper mode, test fixtures).

    Checks BOTH token_id (YES side) and no_token_id (NO side) so buy_no positions
    are not invisible to the dedup gate.
    """
    return any(
        (p.token_id == token_id or p.no_token_id == token_id)
        and _is_runtime_open_position(p)
        for p in state.positions
    )


# Non-open runtime phases used by the direct DB dedup query. This is intentionally
# broader than terminal lifecycle states because economically_closed has no live exposure.
# tuple(sorted(...)) for stable SQL placeholder order.
_NON_OPEN_PHASES = tuple(sorted(INACTIVE_RUNTIME_STATES))


def has_same_token_open_db(conn, token_id: str) -> bool:
    """Decision-time dedup gate: queries position_current directly at call time.
    Non-terminal phases: active, day0_window, pending_exit, pending_entry,
      phantom_not_on_chain, and any future open state.
    Non-open (excluded): voided, economically_closed, settled, quarantined,
      admin_closed.

    Checks BOTH token_id (YES side) and no_token_id (NO side) columns so buy_no
    positions are not invisible to the dedup gate.

    Uses a parameterized NOT IN — placeholders built from the fixed-length
    _NON_OPEN_PHASES tuple (internal constant, not user input). This f-string
    SQL site is registered in scripts/check_dynamic_sql.py baseline.
    """
    placeholders = ",".join("?" * len(_NON_OPEN_PHASES))
    columns = {
        str(row[1] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in conn.execute("PRAGMA table_info(position_current)").fetchall()
    }
    if "chain_shares" in columns:
        chain_state_values = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))
        if "phase" in columns and "chain_state" in columns:
            chain_truth_sql = "phase = 'quarantined' AND chain_state IN ({})".format(
                ",".join("?" for _ in chain_state_values)
            )
        elif "chain_state" in columns:
            chain_truth_sql = "chain_state IN ({})".format(
                ",".join("?" for _ in chain_state_values)
            )
        else:
            chain_truth_sql = "phase IN ('voided', 'quarantined')"
            chain_state_values = ()
        positive_chain_clause = (
            " OR (COALESCE(chain_shares, 0) > ? "
            f"AND ({chain_truth_sql}))"
        )
    else:
        positive_chain_clause = ""
        chain_state_values = ()
    params: list[object] = [token_id, token_id, *_NON_OPEN_PHASES]
    if positive_chain_clause:
        params.extend([_POSITIVE_CHAIN_EXPOSURE_EPS, *chain_state_values])
    row = conn.execute(
        f"""SELECT 1 FROM position_current
            WHERE (token_id = ? OR no_token_id = ?)
            AND (phase NOT IN ({placeholders}){positive_chain_clause})
            LIMIT 1""",
        tuple(params),
    ).fetchone()
    return row is not None


def has_inflight_exit_for_token(conn, token_id: str) -> bool:
    """Belt-and-suspenders: block re-entry if any EXIT order for this token is
    in-flight (MATCHED or MINED in venue_trade_facts but not yet CONFIRMED/promoted).

    PR-S3 critic R1 (2026-05-17): original JOIN via position_current.trade_id was DEAD —
    venue_trade_facts.trade_id stores full UUIDs; position_current.trade_id stores 11-char
    short IDs (different namespaces, 0/76 rows matched in live DB).

    Fixed join path: venue_trade_facts → venue_commands (both use full command_id UUID),
    venue_commands.token_id is the correct bridge key.

    PR #143 bot review fixes (2026-05-18):
    - Restricted to intent_kind = 'EXIT' so BUY confirmations (MATCHED/MINED)
      do not falsely trigger the gate.
    - Added NOT EXISTS subquery to exclude historical MATCHED rows that are
      superseded by a CONFIRMED row for the same trade_id + command_id (venue_trade_facts
      is append-only; older state rows are never deleted). trade_id correlation is
      required: a CONFIRMED row for trade T1 must not suppress the MATCHED/MINED
      gate for a sibling trade T2 under the same command_id (bot finding PR #143).

    Alternative future path (if needed): execution_fact.position_id → venue_commands.position_id
    (execution_fact table exists with position_id column, confirmed 2026-05-17).
    """
    row = conn.execute(
        """SELECT 1
           FROM venue_trade_facts vtf
           JOIN venue_commands vc ON vc.command_id = vtf.command_id
           WHERE vc.token_id = ?
             AND vc.intent_kind = 'EXIT'
             AND vtf.state IN ('MATCHED', 'MINED')
             AND NOT EXISTS (
                 SELECT 1 FROM venue_trade_facts vtf2
                 WHERE vtf2.command_id = vtf.command_id
                   AND vtf2.trade_id = vtf.trade_id
                   AND vtf2.state = 'CONFIRMED'
             )
           LIMIT 1""",
        (token_id,),
    ).fetchone()
    return row is not None


_V2_INTRODUCTION_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)

_BUY_NO_SCALING = ExpiringAssumption[float](
    value=float(settings["exit"]["buy_no_scaling_factor"]),
    fallback=1.5,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="pr_b_validation_replay",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
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
    semantic_id="v2",
    owner="risk_team",
)

# BUG#127 (守護 SEV1): flash-crash evidence-gate tunables. The bare velocity
# threshold only ARMS consideration; firing requires belief confirmation or a
# persistent deep catastrophe. See flash_crash_should_fire().
_FLASH_CRASH_VELOCITY = ExpiringAssumption[float](
    value=float(settings["exit"].get("flash_crash_velocity", -0.15)),
    fallback=-0.15,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="bug127_flash_crash_evidence_gate",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_id="v2",
    owner="risk_team",
)

_FLASH_CRASH_CONFIRMATIONS = ExpiringAssumption[int](
    value=int(settings["exit"].get("flash_crash_confirmations", 2)),
    fallback=2,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="bug127_flash_crash_evidence_gate",
    max_lifespan_days=365,
    kill_switch_action="revert_to_fallback",
    semantic_id="v2",
    owner="risk_team",
)

_FLASH_CRASH_CATASTROPHE_VELOCITY = ExpiringAssumption[float](
    value=float(settings["exit"].get("flash_crash_catastrophe_velocity", -0.40)),
    fallback=-0.40,
    last_verified_at=_V2_INTRODUCTION_DATE,
    verified_by="system",
    verification_source="bug127_flash_crash_evidence_gate",
    max_lifespan_days=180,
    kill_switch_action="revert_to_fallback",
    semantic_id="v2",
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


def flash_crash_velocity() -> float:
    return _FLASH_CRASH_VELOCITY.active_value


def flash_crash_confirmations() -> int:
    return _FLASH_CRASH_CONFIRMATIONS.active_value


def flash_crash_catastrophe_velocity() -> float:
    return _FLASH_CRASH_CATASTROPHE_VELOCITY.active_value


def flash_crash_should_fire(
    *,
    market_velocity_1h: float,
    divergence_score: float,
    has_probability_authority: bool,
    flash_crash_count: int,
) -> bool:
    """BUG#127 (守護 SEV1, GOAL#36): evidence gate for FLASH_CRASH_PANIC.

    A bare adverse market-price move is NOT edge reversal. Crossing
    ``flash_crash_velocity()`` only ARMS consideration; the exit may fire only
    when the move is *confirmed*:

      (a) BELIEF-confirmed — probability authority is present AND the model/market
          divergence has reached the soft-divergence threshold (the belief moved in
          the SAME adverse direction as the price), OR
      (b) PERSISTENT CATASTROPHE — the adverse velocity has persisted for at least
          ``flash_crash_confirmations()`` consecutive monitor cycles AND its
          magnitude exceeds the deep catastrophe bound
          ``flash_crash_catastrophe_velocity()`` (a genuine sustained crash that we
          must escape even when the probability refresh is degraded).

    A single-cycle quote wiggle on a thin book / single seller / data gap, with the
    belief unchanged, satisfies neither path and therefore does NOT exit.

    This is the single source of truth shared by both legacy trigger and
    ``Position.evaluate_exit`` call sites so they cannot diverge.
    """
    arming = flash_crash_velocity()
    try:
        velocity = float(market_velocity_1h)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(velocity):
        return False
    if velocity > arming:
        # Not even an adverse move past the arming threshold.
        return False

    # Path (a): belief confirms the adverse move.
    if has_probability_authority:
        try:
            div = float(divergence_score)
        except (TypeError, ValueError):
            div = 0.0
        if math.isfinite(div) and div >= divergence_soft_threshold():
            return True

    # Path (b): sustained DEEP catastrophe, belief not required.
    try:
        count = int(flash_crash_count)
    except (TypeError, ValueError):
        count = 0
    if (
        velocity <= flash_crash_catastrophe_velocity()
        and count >= flash_crash_confirmations()
    ):
        return True

    return False


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
