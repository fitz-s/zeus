# Created: 2026-04-27
# Last reused/audited: 2026-06-22
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A2.yaml
#                  + docs/evidence/live_order_pathology/2026-06-22_governor_scope_lattice_decision.md
#                    (scope-lattice: SCOPED single-market unknowns isolate per-market;
#                     only SYSTEMIC unknowns trip GLOBAL reduce_only; unscopeable = fail closed)
"""Risk allocation and portfolio-governor gates for R3 A2.

This module is a blocking allocation surface, not a venue client.  It computes
capacity and kill-switch decisions from supplied evidence and never submits,
cancels, redeems, mutates production DB/state artifacts, or authorizes cutover.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Literal, Mapping, Sequence

from src.control.heartbeat_supervisor import HeartbeatHealth, HeartbeatStatus
from src.contracts.execution_intent import ExecutionIntent
from src.contracts.position_truth import FillAuthority
from src.riskguard.risk_level import RiskLevel
from src.state.fill_dedup import canonical_trade_fact_cte, economic_trade_fact_cte
from src.state.canonical_projections import (
    counts_as_active_exposure,
    is_closed_exposure,
    is_optimistic_exposure,
    weighted_lot_exposure_micro,
)

OrderMode = Literal["MAKER", "TAKER", "NO_TRADE"]
ExposureState = Literal[
    "OPTIMISTIC_EXPOSURE",
    "CONFIRMED_EXPOSURE",
    "EXIT_PENDING",
    "ECONOMICALLY_CLOSED_OPTIMISTIC",
    "ECONOMICALLY_CLOSED_CONFIRMED",
    "SETTLED",
    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'QUARANTINED'
    # removed — 0 live position_lots rows, no writer.
]

# A4 exposure classification now lives in src.state.canonical_projections
# (counts_as_active_exposure / is_closed_exposure / is_optimistic_exposure /
# weighted_lot_exposure_micro) — single typed source instead of local frozensets.
_UNRESOLVED_SIDE_EFFECT_STATES = {
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "UNKNOWN",
    "REVIEW_REQUIRED",
}
_PRE_SDK_REVIEW_REQUIRED_REASONS = {
    "pre_submit_collateral_reservation_failed",
    "recovery_no_venue_order_id",
}


@dataclass(frozen=True)
class CapPolicy:
    max_per_market_micro: int = 250_000_000
    max_per_event_micro: int = 500_000_000
    max_per_resolution_window_micro: dict[str, int] = field(default_factory=lambda: {"default": 750_000_000})
    max_correlated_exposure_micro: int = 1_000_000_000
    unknown_side_effect_limit: int = 0
    reconcile_finding_limit: int = 0
    # Scope-lattice systemic escalator: when unknown side effects span this many
    # or more DISTINCT independent markets, the cluster is treated as a systemic
    # (common-mode) failure that trips global reduce_only rather than per-market
    # isolation. A single scoped market's unknown(s) stay below this limit and are
    # isolated only for that market (see governor scope-lattice decision 2026-06-22).
    systemic_market_count_limit: int = 2
    ws_gap_seconds_limit: int = 15
    optimistic_exposure_weight: float = 0.5
    taker_min_depth_micro: int = 50_000_000
    maker_deadline_seconds: int = 30 * 60

    def __post_init__(self) -> None:
        positive_int_fields = (
            "max_per_market_micro",
            "max_per_event_micro",
            "max_correlated_exposure_micro",
            "taker_min_depth_micro",
            "maker_deadline_seconds",
        )
        for name in positive_int_fields:
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0.0 <= float(self.optimistic_exposure_weight) <= 1.0:
            raise ValueError("optimistic_exposure_weight must be in [0, 1]")
        if int(self.unknown_side_effect_limit) < 0 or int(self.reconcile_finding_limit) < 0 or int(self.ws_gap_seconds_limit) < 0:
            raise ValueError("kill-switch thresholds must be non-negative")
        if int(self.systemic_market_count_limit) < 1:
            raise ValueError("systemic_market_count_limit must be >= 1")
        for label, cap in self.max_per_resolution_window_micro.items():
            if not label or int(cap) <= 0:
                raise ValueError("resolution-window caps require non-empty labels and positive caps")


@dataclass(frozen=True)
class GovernorState:
    current_drawdown_pct: float
    heartbeat_health: HeartbeatHealth
    ws_gap_active: bool
    unknown_side_effect_count: int
    reconcile_finding_count: int
    kill_switch_armed: bool = False
    ws_gap_seconds: int = 0
    # Independent WS-recovery latch (src.control.ws_gap_guard): true from the
    # moment a user-channel gap/persistence-failure is recorded until an M5
    # reconcile sweep proves no fills were missed. This is a proof-based state,
    # not a duration — it is NOT part of the graded ws_gap_seconds threshold
    # below and must trip reduce-only unconditionally (see reduce_only_mode_active).
    m5_reconcile_required: bool = False
    risk_level: RiskLevel = RiskLevel.GREEN
    unknown_side_effect_markets: tuple[str, ...] = ()
    # Scope lattice (additive): count of unknown side effects classified SYSTEMIC
    # — either unscopeable (cannot bind to a single market, fail closed) or spanning
    # >= systemic_market_count_limit distinct markets. Only SYSTEMIC unknowns trip
    # the GLOBAL reduce_only latch; SCOPED unknowns isolate via unknown_side_effect_markets.
    systemic_unknown_side_effect_count: int = 0
    manual_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_drawdown_pct": self.current_drawdown_pct,
            "heartbeat_health": self.heartbeat_health.value,
            "ws_gap_active": self.ws_gap_active,
            "ws_gap_seconds": self.ws_gap_seconds,
            "m5_reconcile_required": self.m5_reconcile_required,
            "unknown_side_effect_count": self.unknown_side_effect_count,
            "reconcile_finding_count": self.reconcile_finding_count,
            "systemic_unknown_side_effect_count": self.systemic_unknown_side_effect_count,
            "kill_switch_armed": self.kill_switch_armed,
            "risk_level": self.risk_level.value,
            "unknown_side_effect_markets": list(self.unknown_side_effect_markets),
            "manual_reason": self.manual_reason,
        }


@dataclass(frozen=True)
class ExposureLot:
    market_id: str
    event_id: str
    resolution_window: str
    token_id: str
    exposure_micro: int
    state: ExposureState
    correlation_key: str | None = None
    source: str = "VENUE"


@dataclass(frozen=True)
class AllocationDecision:
    allowed: bool
    reason: str
    requested_micro: int
    available_capacity_micro: int = 0
    remaining_market_capacity_micro: int = 0
    remaining_event_capacity_micro: int = 0
    remaining_resolution_capacity_micro: int = 0
    remaining_correlated_capacity_micro: int = 0
    confirmed_exposure_micro: int = 0
    optimistic_exposure_micro: int = 0
    weighted_existing_exposure_micro: int = 0
    reduce_only: bool = False

    def __bool__(self) -> bool:
        return self.allowed


class AllocationDenied(RuntimeError):
    def __init__(self, decision: AllocationDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class RiskAllocator:
    def __init__(self, cap_policy: CapPolicy | None = None, exposure_lots: Sequence[ExposureLot] | None = None) -> None:
        self.cap_policy = cap_policy or CapPolicy()
        self._lots = tuple(exposure_lots or ())
        market: dict[str, list[int]] = {}
        event: dict[str, int] = {}
        resolution: dict[str, int] = {}
        correlation: dict[str, int] = {}
        for lot in self._lots:
            if is_closed_exposure(lot.state):
                continue
            weighted = self._weighted_lot_exposure(lot)
            totals = market.setdefault(lot.market_id, [0, 0, 0])
            if is_optimistic_exposure(lot.state):
                totals[1] += int(lot.exposure_micro)
                totals[2] += weighted
            elif counts_as_active_exposure(lot.state):
                totals[0] += int(lot.exposure_micro)
                totals[2] += weighted
            event[lot.event_id] = event.get(lot.event_id, 0) + weighted
            resolution[lot.resolution_window] = (
                resolution.get(lot.resolution_window, 0) + weighted
            )
            correlation_key = lot.correlation_key or lot.event_id
            correlation[correlation_key] = correlation.get(correlation_key, 0) + weighted
        self._market_exposure_by_id = {
            key: tuple(values) for key, values in market.items()
        }
        self._weighted_exposure_by_scope = {
            "event": event,
            "resolution": resolution,
            "correlation": correlation,
        }

    @property
    def exposure_lots(self) -> tuple[ExposureLot, ...]:
        return self._lots

    def with_lots(self, exposure_lots: Sequence[ExposureLot]) -> "RiskAllocator":
        return RiskAllocator(self.cap_policy, exposure_lots)

    @classmethod
    def from_position_lots(cls, conn: Any, cap_policy: CapPolicy | None = None) -> "RiskAllocator":
        """Build capacity from current positions and unprojected lot truth."""

        return cls(cap_policy, load_position_lots(conn))

    def auction_capacity(
        self,
        *,
        market_id: str,
        event_id: str,
        resolution_window: str,
        correlation_key: str,
    ) -> AllocationDecision:
        """Return structural capacity for one economic auction epoch."""

        market = str(market_id or "").strip()
        event = str(event_id or market).strip()
        resolution = str(resolution_window or "default").strip() or "default"
        correlation = str(correlation_key or event).strip()
        if not market or not event or not correlation:
            return AllocationDecision(False, "allocation_scope_missing", 0)
        confirmed, optimistic, weighted_market = self._market_exposure(market)
        remaining_market = max(
            int(self.cap_policy.max_per_market_micro) - weighted_market,
            0,
        )
        remaining_event = self._remaining_capacity(
            "event", event, self.cap_policy.max_per_event_micro
        )
        window_cap = self.cap_policy.max_per_resolution_window_micro.get(
            resolution,
            self.cap_policy.max_per_resolution_window_micro.get(
                "default", self.cap_policy.max_per_event_micro
            ),
        )
        remaining_resolution = self._remaining_capacity(
            "resolution", resolution, window_cap
        )
        remaining_correlated = self._remaining_capacity(
            "correlation", correlation, self.cap_policy.max_correlated_exposure_micro
        )
        available = min(
            remaining_market,
            remaining_event,
            remaining_resolution,
            remaining_correlated,
        )
        base = {
            "available_capacity_micro": available,
            "remaining_market_capacity_micro": remaining_market,
            "remaining_event_capacity_micro": remaining_event,
            "remaining_resolution_capacity_micro": remaining_resolution,
            "remaining_correlated_capacity_micro": remaining_correlated,
            "confirmed_exposure_micro": confirmed,
            "optimistic_exposure_micro": optimistic,
            "weighted_existing_exposure_micro": weighted_market,
        }
        for remaining, reason in (
            (remaining_market, "per_market_cap_exceeded"),
            (remaining_event, "per_event_cap_exceeded"),
            (remaining_resolution, "per_resolution_window_cap_exceeded"),
            (remaining_correlated, "correlated_market_cap_exceeded"),
        ):
            if remaining <= 0:
                return AllocationDecision(False, reason, 0, **base)
        return AllocationDecision(True, "allowed", 0, **base)

    def entry_capacity(
        self,
        *,
        market_id: str,
        event_id: str,
        resolution_window: str,
        correlation_key: str,
        governor_state: GovernorState,
        reduce_only: bool = False,
    ) -> AllocationDecision:
        """Return submit-time capacity including current actuation health."""

        capacity = self.auction_capacity(
            market_id=market_id,
            event_id=event_id,
            resolution_window=resolution_window,
            correlation_key=correlation_key,
        )
        if capacity.reason == "allocation_scope_missing":
            return capacity
        market = str(market_id or "").strip()
        kill_reason = self.kill_switch_reason(governor_state)
        if kill_reason:
            return replace(
                capacity,
                allowed=False,
                reason=kill_reason,
                reduce_only=reduce_only,
            )
        if market in set(governor_state.unknown_side_effect_markets):
            return replace(
                capacity,
                allowed=False,
                reason="unknown_side_effect_same_market",
                reduce_only=reduce_only,
            )
        if self.reduce_only_mode_active(governor_state) and not reduce_only:
            return replace(
                capacity,
                allowed=False,
                reason="reduce_only_mode_active",
                reduce_only=False,
            )
        return replace(capacity, reduce_only=reduce_only)

    def can_allocate(self, intent: ExecutionIntent, governor_state: GovernorState) -> AllocationDecision:
        requested = _intent_notional_micro(intent)
        reduce_only = _is_reduce_only_intent(intent)
        resolution_label = str(getattr(intent, "resolution_window", "default") or "default")
        correlation_key = str(getattr(intent, "correlation_key", None) or _intent_event_id(intent))
        capacity = self.entry_capacity(
            market_id=str(intent.market_id),
            event_id=str(_intent_event_id(intent)),
            resolution_window=resolution_label,
            correlation_key=correlation_key,
            governor_state=governor_state,
            reduce_only=reduce_only,
        )
        if not capacity.allowed:
            return replace(
                capacity,
                requested_micro=requested,
                reduce_only=reduce_only,
            )
        for remaining, reason in (
            (capacity.remaining_market_capacity_micro, "per_market_cap_exceeded"),
            (capacity.remaining_event_capacity_micro, "per_event_cap_exceeded"),
            (
                capacity.remaining_resolution_capacity_micro,
                "per_resolution_window_cap_exceeded",
            ),
            (capacity.remaining_correlated_capacity_micro, "correlated_market_cap_exceeded"),
        ):
            if requested > remaining:
                return replace(
                    capacity,
                    allowed=False,
                    reason=reason,
                    requested_micro=requested,
                    reduce_only=reduce_only,
                )
        return replace(
            capacity,
            requested_micro=requested,
            reduce_only=reduce_only,
        )

    def maker_or_taker(self, snapshot: Any, governor_state: GovernorState) -> OrderMode:
        if self.kill_switch_reason(governor_state):
            return "NO_TRADE"
        # Venue heartbeat owns resting-order leases only. Immediate orders do
        # not depend on that lease and remain available for held-position
        # reduction through a missing or degraded keeper snapshot. New-entry
        # blocking is enforced separately by reduce_only_mode_active().
        if governor_state.heartbeat_health is not HeartbeatHealth.HEALTHY:
            return "TAKER"
        if _snapshot_depth_micro(snapshot) < self.cap_policy.taker_min_depth_micro:
            return "TAKER"
        seconds_to_close = _seconds_to_close(snapshot)
        if seconds_to_close is not None and seconds_to_close <= self.cap_policy.maker_deadline_seconds:
            return "TAKER"
        return "MAKER"

    def allowed_order_types(self, governor_state: GovernorState) -> tuple[str, ...]:
        mode = self.maker_or_taker(_EmptySnapshot(), governor_state)
        if mode == "NO_TRADE":
            return ()
        if governor_state.heartbeat_health is HeartbeatHealth.HEALTHY:
            return ("GTC", "GTD", "FOK", "FAK")
        return ("FOK", "FAK")

    def reduce_only_mode_active(self, governor_state: GovernorState) -> bool:
        if governor_state.kill_switch_armed:
            return True
        # Missing current venue-liveness truth blocks new risk.  UNCONFIGURED
        # is bootstrap/configuration absence; STARTING has no successful lease
        # witness yet; LOST includes expired external keeper snapshots.
        if governor_state.heartbeat_health in {
            HeartbeatHealth.UNCONFIGURED,
            HeartbeatHealth.STARTING,
            HeartbeatHealth.LOST,
        }:
            return True
        # M5 reconcile-required (src.control.ws_gap_guard) is an independent
        # WS-recovery latch: proof that no fills were missed during a user-
        # channel gap. It is a binary "has this been swept yet" state, not a
        # duration, so it trips unconditionally -- grading it against
        # ws_gap_seconds would silently disarm it, since ws_gap_seconds is
        # never populated with a real duration by any live caller today (see
        # 2026-07-19 capital-utilization evidence + this commit message).
        if governor_state.m5_reconcile_required:
            return True
        # A sub-threshold transient ws gap alone is not a reduce-only event:
        # grade it with the same threshold the kill-switch already uses
        # (kill_switch_reason, below) instead of tripping unconditionally on
        # any ws_gap_active flag.
        if governor_state.ws_gap_active and governor_state.ws_gap_seconds > self.cap_policy.ws_gap_seconds_limit:
            return True
        # Reconcile findings are always systemic (common-path accounting failure).
        if governor_state.reconcile_finding_count > 0:
            return True
        # Scope lattice: only SYSTEMIC unknown side effects trip the GLOBAL latch.
        # SCOPED single-market unknowns isolate via unknown_side_effect_markets
        # (the per-market reject in can_allocate), and must not freeze the book.
        if _systemic_unknown_present(governor_state):
            return True
        return governor_state.risk_level in {RiskLevel.DATA_DEGRADED, RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED}

    def kill_switch_reason(self, governor_state: GovernorState) -> str | None:
        policy = self.cap_policy
        if governor_state.kill_switch_armed:
            return governor_state.manual_reason or "kill_switch_armed"
        if governor_state.ws_gap_active and governor_state.ws_gap_seconds > policy.ws_gap_seconds_limit:
            return "ws_gap_threshold"
        return None

    def _market_exposure(self, market_id: str) -> tuple[int, int, int]:
        return self._market_exposure_by_id.get(market_id, (0, 0, 0))

    def _remaining_capacity(self, scope: str, key: str, cap: int) -> int:
        exposure = self._weighted_exposure_by_scope.get(scope, {}).get(key, 0)
        return max(int(cap) - exposure, 0)

    def _weighted_lot_exposure(self, lot: ExposureLot) -> int:
        return weighted_lot_exposure_micro(
            lot.state, int(lot.exposure_micro), self.cap_policy.optimistic_exposure_weight
        )


@dataclass(frozen=True)
class AuctionCapitalAuthority:
    """One immutable exposure-and-capacity witness for one auction epoch."""

    allocator: RiskAllocator

    def capacity_usd(
        self,
        *,
        market_id: str,
        event_id: str,
        resolution_window: str = "default",
        correlation_key: str = "",
    ) -> Decimal:
        decision = self.allocator.auction_capacity(
            market_id=market_id,
            event_id=event_id,
            resolution_window=resolution_window,
            correlation_key=correlation_key,
        )
        if not decision.allowed:
            if decision.reason in {
                "per_market_cap_exceeded",
                "per_event_cap_exceeded",
                "per_resolution_window_cap_exceeded",
                "correlated_market_cap_exceeded",
            }:
                return Decimal("0")
            raise AllocationDenied(decision)
        return Decimal(decision.available_capacity_micro) / Decimal("1000000")


class PortfolioGovernor:
    def __init__(self, cap_policy: CapPolicy | None = None) -> None:
        self.cap_policy = cap_policy or CapPolicy()
        self._manual_kill_reason: str | None = None

    def update_state(
        self,
        ledger: Any,
        heartbeat: HeartbeatStatus | Mapping[str, Any] | Any,
        ws_status: Mapping[str, Any] | Any,
        unknown_count: int,
        finding_count: int,
    ) -> GovernorState:
        health = _coerce_heartbeat_health(heartbeat)
        m5_reconcile_required = bool(_mapping_get(ws_status, "m5_reconcile_required", False))
        ws_gap_active = bool(_mapping_get(ws_status, "ws_gap_active", False))
        ws_gap_seconds = int(_mapping_get(ws_status, "gap_seconds", 0) or _mapping_get(ws_status, "ws_gap_seconds", 0) or 0)
        drawdown = float(getattr(ledger, "current_drawdown_pct", _mapping_get(ledger, "current_drawdown_pct", 0.0)) or 0.0)
        risk_level = _coerce_risk_level(getattr(ledger, "risk_level", _mapping_get(ledger, "risk_level", RiskLevel.GREEN)))
        automatic_reason = _automatic_kill_switch_reason(
            self.cap_policy,
            ws_gap_active=ws_gap_active,
            ws_gap_seconds=ws_gap_seconds,
            unknown_side_effect_count=int(unknown_count),
            reconcile_finding_count=int(finding_count),
        )
        kill_reason = self._manual_kill_reason or automatic_reason
        state = GovernorState(
            current_drawdown_pct=drawdown,
            heartbeat_health=health,
            ws_gap_active=ws_gap_active,
            ws_gap_seconds=ws_gap_seconds,
            m5_reconcile_required=m5_reconcile_required,
            unknown_side_effect_count=int(unknown_count),
            reconcile_finding_count=int(finding_count),
            kill_switch_armed=kill_reason is not None,
            risk_level=risk_level,
            manual_reason=kill_reason,
        )
        # Automatic and manual trips are both reflected in kill_switch_armed;
        # manual_reason carries the structured reason for summaries/denials.
        return state

    def kill_switch(self, reason: str) -> None:
        self._manual_kill_reason = str(reason or "manual_kill_switch")

    def clear_kill_switch(self) -> None:
        self._manual_kill_reason = None


_DEFAULT_ALLOCATOR = RiskAllocator()
_GLOBAL_GOVERNOR: PortfolioGovernor | None = None
_GLOBAL_ALLOCATOR: RiskAllocator | None = None
_GLOBAL_GOVERNOR_STATE: GovernorState | None = None
_GLOBAL_ALLOCATION_LOCK = RLock()


def configure_global_allocator(allocator: RiskAllocator | None, governor_state: GovernorState | None = None) -> None:
    global _GLOBAL_ALLOCATOR, _GLOBAL_GOVERNOR_STATE
    with _GLOBAL_ALLOCATION_LOCK:
        _GLOBAL_ALLOCATOR = allocator
        _GLOBAL_GOVERNOR_STATE = governor_state


def configure_global_governor_state(governor_state: GovernorState | None) -> None:
    global _GLOBAL_GOVERNOR_STATE
    with _GLOBAL_ALLOCATION_LOCK:
        _GLOBAL_GOVERNOR_STATE = governor_state


def clear_global_allocator() -> None:
    configure_global_allocator(None, None)


def snapshot_global_auction_capital_authority() -> AuctionCapitalAuthority:
    """Freeze current exposure and caps without freezing actuation readiness."""

    with _GLOBAL_ALLOCATION_LOCK:
        allocator = _GLOBAL_ALLOCATOR
    if allocator is None:
        raise AllocationDenied(AllocationDecision(False, "allocator_not_configured", 0))
    return AuctionCapitalAuthority(allocator)


def _snapshot_global_actuation_authority() -> tuple[RiskAllocator, GovernorState]:
    """Read one coherent current allocator/governor pair for a side effect."""

    with _GLOBAL_ALLOCATION_LOCK:
        allocator = _GLOBAL_ALLOCATOR
        governor_state = _GLOBAL_GOVERNOR_STATE
    if allocator is None or governor_state is None:
        raise AllocationDenied(
            AllocationDecision(False, "allocator_not_configured", 0)
        )
    return allocator, governor_state


def assert_global_allocation_allows(intent: ExecutionIntent) -> AllocationDecision:
    allocator, governor_state = _snapshot_global_actuation_authority()
    decision = allocator.can_allocate(intent, governor_state)
    if not decision.allowed:
        raise AllocationDenied(decision)
    return decision


def current_global_entry_capacity_usd(
    *,
    market_id: str,
    event_id: str,
    resolution_window: str = "default",
    correlation_key: str = "",
) -> Decimal:
    """Read the current candidate-specific entry envelope without reserving it."""

    allocator, governor_state = _snapshot_global_actuation_authority()
    decision = allocator.entry_capacity(
        market_id=market_id,
        event_id=event_id,
        resolution_window=resolution_window,
        correlation_key=correlation_key,
        governor_state=governor_state,
    )
    if not decision.allowed:
        if decision.reason in {
            "per_market_cap_exceeded",
            "per_event_cap_exceeded",
            "per_resolution_window_cap_exceeded",
            "correlated_market_cap_exceeded",
        }:
            return Decimal("0")
        raise AllocationDenied(decision)
    return Decimal(decision.available_capacity_micro) / Decimal("1000000")


def assert_global_submit_allows(*, reduce_only: bool = False) -> AllocationDecision:
    """Guard non-entry submits against global kill-switch state.

    Exits may continue through reduce-only modes, but a true kill-switch reason
    blocks all submit paths before command persistence or SDK contact.
    """

    try:
        allocator, governor_state = _snapshot_global_actuation_authority()
    except AllocationDenied as exc:
        raise AllocationDenied(
            replace(exc.decision, reduce_only=reduce_only)
        ) from exc
    kill_reason = allocator.kill_switch_reason(governor_state)
    if kill_reason:
        decision = AllocationDecision(False, kill_reason, 0, reduce_only=reduce_only)
        raise AllocationDenied(decision)
    if not reduce_only and allocator.reduce_only_mode_active(governor_state):
        decision = AllocationDecision(False, "reduce_only_mode_active", 0, reduce_only=reduce_only)
        raise AllocationDenied(decision)
    return AllocationDecision(True, "allowed", 0, reduce_only=reduce_only)


def select_global_order_type(snapshot: Any) -> str:
    """Return the concrete venue order type allowed by the current governor.

    A2's maker/taker switch is behavior-changing: healthy/deep/far-from-close
    conditions may rest as ``GTC``; unhealthy heartbeat, shallow books, or near
    resolution force immediate-or-cancel semantics (``FOK``).  True no-trade
    states raise ``AllocationDenied`` so callers block before persistence/SDK.
    """

    allocator, governor_state = _snapshot_global_actuation_authority()
    mode = allocator.maker_or_taker(snapshot or _EmptySnapshot(), governor_state)
    if mode == "NO_TRADE":
        reason = allocator.kill_switch_reason(governor_state) or "no_trade_mode"
        raise AllocationDenied(AllocationDecision(False, reason, 0))
    if mode == "TAKER":
        return "FOK"
    return "GTC"


def summary() -> dict[str, Any]:
    with _GLOBAL_ALLOCATION_LOCK:
        allocator = _GLOBAL_ALLOCATOR
        governor_state = _GLOBAL_GOVERNOR_STATE
    if governor_state is None:
        return {"configured": False, "entry": {"allow_submit": False, "reason": "allocator_not_configured"}}
    if allocator is None:
        return {
            "configured": False,
            "state": governor_state.to_dict(),
            "kill_switch_reason": "allocator_not_configured",
            "reduce_only": True,
            "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
        }
    kill_reason = allocator.kill_switch_reason(governor_state)
    reduce_only = allocator.reduce_only_mode_active(governor_state)
    entry_reason = kill_reason or ("reduce_only_mode_active" if reduce_only else "ok")
    return {
        "configured": True,
        "state": governor_state.to_dict(),
        "kill_switch_reason": kill_reason,
        "reduce_only": reduce_only,
        "entry": {"allow_submit": entry_reason == "ok", "reason": entry_reason},
    }


def get_global_governor(cap_policy: CapPolicy | None = None) -> PortfolioGovernor:
    global _GLOBAL_GOVERNOR
    if _GLOBAL_GOVERNOR is None:
        _GLOBAL_GOVERNOR = PortfolioGovernor(cap_policy)
    elif cap_policy is not None:
        # Preserve any manually armed kill switch while allowing operator cap
        # config reloads to affect automatic thresholds on the next cycle.
        _GLOBAL_GOVERNOR.cap_policy = cap_policy
    return _GLOBAL_GOVERNOR


def refresh_global_allocator(
    conn: Any,
    *,
    ledger: Any,
    heartbeat: HeartbeatStatus | Mapping[str, Any] | Any,
    ws_status: Mapping[str, Any] | Any,
    cap_policy: CapPolicy | None = None,
) -> dict[str, Any]:
    """Refresh the process-wide allocator/governor from canonical read models.

    This is the cycle-runner integration seam: read current lot capacity,
    unresolved unknown-submit side effects, unresolved reconcile findings, and
    control-plane health, then publish one blocking allocation state.
    """

    policy = cap_policy or load_cap_policy()
    allocator = RiskAllocator.from_position_lots(conn, policy)
    scope = classify_unknown_side_effect_scope(conn, policy)
    finding_count = count_open_reconcile_findings(conn)
    governor = get_global_governor(policy)
    governor_state = governor.update_state(
        ledger,
        heartbeat,
        ws_status,
        unknown_count=scope.total_count,
        finding_count=finding_count,
    )
    # Scope lattice: list scoped markets for per-market isolation (line-186 path),
    # and publish the systemic count so only SYSTEMIC unknowns trip the GLOBAL latch.
    governor_state = replace(
        governor_state,
        unknown_side_effect_markets=tuple(scope.scoped_markets),
        systemic_unknown_side_effect_count=scope.systemic_count,
    )
    configure_global_allocator(allocator, governor_state)
    return summary()


def load_cap_policy(path: str | Path = "config/risk_caps.yaml") -> CapPolicy:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return CapPolicy()
    raw = cfg_path.read_text()
    data: Mapping[str, Any]
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(raw) or {}
        data = loaded if isinstance(loaded, Mapping) else {}
    except Exception:
        loaded = json.loads(raw)
        data = loaded if isinstance(loaded, Mapping) else {}
    return CapPolicy(
        max_per_market_micro=int(data.get("max_per_market_micro", CapPolicy().max_per_market_micro)),
        max_per_event_micro=int(data.get("max_per_event_micro", CapPolicy().max_per_event_micro)),
        max_per_resolution_window_micro=dict(data.get("max_per_resolution_window_micro", CapPolicy().max_per_resolution_window_micro)),
        max_correlated_exposure_micro=int(data.get("max_correlated_exposure_micro", CapPolicy().max_correlated_exposure_micro)),
        unknown_side_effect_limit=int(data.get("unknown_side_effect_limit", CapPolicy().unknown_side_effect_limit)),
        reconcile_finding_limit=int(data.get("reconcile_finding_limit", CapPolicy().reconcile_finding_limit)),
        systemic_market_count_limit=int(data.get("systemic_market_count_limit", CapPolicy().systemic_market_count_limit)),
        ws_gap_seconds_limit=int(data.get("ws_gap_seconds_limit", CapPolicy().ws_gap_seconds_limit)),
        optimistic_exposure_weight=float(data.get("optimistic_exposure_weight", CapPolicy().optimistic_exposure_weight)),
        taker_min_depth_micro=int(data.get("taker_min_depth_micro", CapPolicy().taker_min_depth_micro)),
        maker_deadline_seconds=int(data.get("maker_deadline_seconds", CapPolicy().maker_deadline_seconds)),
    )


def _weather_family_correlation_key(
    *,
    family_id: object = "",
    city: object = "",
    target_date: object = "",
    metric: object = "",
    fallback: object = "",
) -> str:
    """Normalize current and legacy exposure onto one terminal weather family."""

    family = str(family_id or "").strip()
    if family.startswith("edli_family_"):
        return family
    city_text = str(city or "").strip()
    target_text = str(target_date or "").strip()
    metric_text = str(metric or "").strip().lower()
    if city_text and target_text and metric_text in {"high", "low"}:
        from src.events.candidate_binding import weather_family_id

        return weather_family_id(
            city=city_text,
            target_date=target_text,
            metric=metric_text,
        )
    return family or str(fallback or "").strip()


def load_position_lots(conn: Any) -> tuple[ExposureLot, ...]:
    """Read current confirmed exposure plus still-unprojected active lots."""

    with _named_sqlite_rows(conn) as read_conn:
        current_rows = _load_current_position_exposure_rows(read_conn)
        current_position_ids = {
            str(_row_mapping(row).get("position_id") or "") for row in current_rows
        }
        covered_position_ids = (
            current_position_ids | _load_closed_position_ids(read_conn)
        )
        rows = _load_legacy_position_lot_rows(read_conn)
    lots: list[ExposureLot] = []
    for row in rows:
        row_map = _row_mapping(row)
        if str(row_map.get("runtime_position_id") or "") in covered_position_ids:
            continue
        payload = _coerce_payload(row_map.get("raw_payload_json"))
        submit_payload = _coerce_payload(row_map.get("submit_payload_json"))
        allocation_payload_raw = submit_payload.get("allocation", {}) if isinstance(submit_payload, Mapping) else {}
        allocation_payload = allocation_payload_raw if isinstance(allocation_payload_raw, Mapping) else {}
        market_id = str(payload.get("market_id") or row_map.get("market_id") or row_map.get("position_id"))
        event_id = str(allocation_payload.get("event_id") or payload.get("event_id") or row_map.get("event_id") or market_id)
        resolution_window = str(allocation_payload.get("resolution_window") or payload.get("resolution_window") or payload.get("window_label") or "default")
        correlation_key = _weather_family_correlation_key(
            family_id=(
                allocation_payload.get("family_id")
                or payload.get("family_id")
                or allocation_payload.get("correlation_key")
                or payload.get("correlation_key")
            ),
            city=payload.get("city"),
            target_date=payload.get("target_date"),
            metric=(
                payload.get("metric")
                or payload.get("temperature_metric")
            ),
            fallback=event_id,
        )
        token_id = str(payload.get("token_id") or row_map.get("token_id") or row_map.get("position_id"))
        exposure_micro = _lot_exposure_micro(row_map.get("shares"), row_map.get("entry_price_avg"))
        lots.append(
            ExposureLot(
                market_id=market_id,
                event_id=event_id,
                resolution_window=resolution_window,
                token_id=token_id,
                exposure_micro=exposure_micro,
                state=str(row_map.get("state")),
                correlation_key=correlation_key,
                source=str(row_map.get("source") or "VENUE"),
            )
        )
    for row in current_rows:
        lots.extend(_current_position_exposure_lots(row))
    return tuple(lots)


def _load_closed_position_ids(conn: Any) -> set[str]:
    if not _has_table(conn, "position_current"):
        return set()
    if not _has_column(conn, "position_current", "position_id"):
        return set()
    if not _has_column(conn, "position_current", "phase"):
        return set()
    return {
        str(_row_mapping(row).get("position_id") or "")
        for row in conn.execute(
            """
            SELECT position_id
              FROM position_current
             WHERE phase IN (
                 'economically_closed',
                 'settled',
                 'voided',
                 'admin_closed'
             )
            """
        ).fetchall()
    }


def _load_legacy_position_lot_rows(conn: Any) -> list[Mapping[str, Any]]:
    if not _has_table(conn, "position_lots"):
        return []
    has_commands = _has_table(conn, "venue_commands")
    has_events = _has_table(conn, "venue_command_events")
    runtime_position_expr = (
        "cmd.position_id AS runtime_position_id"
        if has_commands and _has_column(conn, "venue_commands", "position_id")
        else "NULL AS runtime_position_id"
    )
    submit_payload_expr = (
        """
        (
          SELECT event.payload_json
            FROM venue_command_events event
           WHERE event.command_id = cmd.command_id
             AND event.event_type = 'SUBMIT_REQUESTED'
           ORDER BY event.sequence_no DESC
           LIMIT 1
        )
        """
        if has_events and has_commands
        else "NULL"
    )
    command_join = (
        "LEFT JOIN venue_commands cmd ON cmd.command_id = lot.source_command_id"
        if has_commands
        else "LEFT JOIN (SELECT NULL AS command_id, NULL AS market_id, NULL AS token_id, NULL AS decision_id) cmd ON 0"
    )
    command_provenance_predicate = "AND cmd.command_id IS NOT NULL" if has_commands else ""
    return list(
        conn.execute(
            f"""
            SELECT
              lot.position_id,
              lot.state,
              lot.shares,
              lot.entry_price_avg,
              lot.source,
              lot.raw_payload_json,
              {submit_payload_expr} AS submit_payload_json,
              COALESCE(cmd.market_id, CAST(lot.position_id AS TEXT)) AS market_id,
              COALESCE(cmd.token_id, CAST(lot.position_id AS TEXT)) AS token_id,
              COALESCE(cmd.decision_id, cmd.market_id, CAST(lot.position_id AS TEXT)) AS event_id,
              {runtime_position_expr}
            FROM position_lots lot
            JOIN (
              SELECT position_id, MAX(local_sequence) AS max_sequence
              FROM position_lots
              GROUP BY position_id
            ) latest
              ON latest.position_id = lot.position_id
             AND latest.max_sequence = lot.local_sequence
            {command_join}
            WHERE lot.state IN (
              'OPTIMISTIC_EXPOSURE',
              'CONFIRMED_EXPOSURE',
              'EXIT_PENDING'
            )
              {command_provenance_predicate}
            ORDER BY lot.position_id, lot.lot_id
            """
        ).fetchall()
    )


def _load_current_position_exposure_rows(conn: Any) -> list[Mapping[str, Any]]:
    required_current = {
        "position_id",
        "phase",
        "market_id",
        "direction",
        "shares",
        "cost_basis_usd",
        "entry_price",
        "token_id",
        "no_token_id",
        "chain_shares",
        "chain_cost_basis_usd",
    }
    required_command = {
        "command_id",
        "position_id",
        "intent_kind",
        "side",
        "market_id",
        "token_id",
        "decision_id",
        "created_at",
    }
    if not _has_table(conn, "position_current"):
        return []
    if any(not _has_column(conn, "position_current", col) for col in required_current):
        return []
    if any(not _has_column(conn, "venue_commands", col) for col in required_command):
        return []
    if not _has_table(conn, "venue_command_events"):
        return []
    fill_authority_expr = (
        "pc.fill_authority" if _has_column(conn, "position_current", "fill_authority") else "NULL"
    )
    city_expr = (
        "pc.city" if _has_column(conn, "position_current", "city") else "NULL"
    )
    target_date_expr = (
        "pc.target_date"
        if _has_column(conn, "position_current", "target_date")
        else "NULL"
    )
    metric_expr = (
        "pc.temperature_metric"
        if _has_column(conn, "position_current", "temperature_metric")
        else "NULL"
    )
    rows = conn.execute(
            f"""
            SELECT pc.position_id,
                   pc.phase,
                   pc.market_id AS projection_market_id,
                   pc.direction,
                   pc.shares,
                   pc.cost_basis_usd,
                   pc.entry_price,
                   pc.token_id AS yes_token_id,
                   pc.no_token_id,
                   pc.chain_shares,
                   pc.chain_cost_basis_usd,
                   {fill_authority_expr} AS fill_authority,
                   {city_expr} AS city,
                   {target_date_expr} AS target_date,
                   {metric_expr} AS temperature_metric,
                   cmd.command_id,
                   cmd.market_id,
                   cmd.token_id,
                   cmd.decision_id AS event_id,
                   (
                       SELECT event.payload_json
                         FROM venue_command_events event
                        WHERE event.command_id = cmd.command_id
                          AND event.event_type = 'SUBMIT_REQUESTED'
                        ORDER BY event.sequence_no DESC
                        LIMIT 1
                   ) AS submit_payload_json
              FROM position_current pc
              LEFT JOIN venue_commands cmd
                ON cmd.command_id = (
                   SELECT latest.command_id
                     FROM venue_commands latest
                    WHERE latest.position_id = pc.position_id
                      AND latest.intent_kind = 'ENTRY'
                      AND latest.side = 'BUY'
                    ORDER BY latest.created_at DESC, latest.command_id DESC
                    LIMIT 1
                )
             WHERE pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND CASE
                   WHEN COALESCE(pc.chain_shares, 0) > 0 THEN pc.chain_shares
                   ELSE COALESCE(pc.shares, 0)
               END > 0
            ORDER BY pc.position_id
            """
        ).fetchall()
    lot_states = _load_latest_command_lot_states(conn)
    authority_costs = _load_current_position_authority_costs(conn)
    return [
        {
            **dict(_row_mapping(row)),
            "latest_lot_state": lot_states.get(
                str(_row_mapping(row).get("position_id") or "")
            ),
            **authority_costs.get(
                str(_row_mapping(row).get("position_id") or ""),
                {"confirmed_cost": Decimal("0"), "optimistic_cost": Decimal("0")},
            ),
        }
        for row in rows
    ]


def _load_latest_command_lot_states(conn: Any) -> dict[str, str]:
    if not _has_table(conn, "position_lots"):
        return {}
    if not _has_table(conn, "venue_commands"):
        return {}
    lot_order = (
        "COALESCE(lot.captured_at, ''), lot.lot_id"
        if _has_column(conn, "position_lots", "captured_at")
        else "lot.lot_id"
    )
    rows = conn.execute(
        f"""
        SELECT cmd.position_id AS runtime_position_id, lot.state
          FROM position_lots lot
          JOIN (
                SELECT position_id, MAX(local_sequence) AS max_sequence
                  FROM position_lots
                 GROUP BY position_id
          ) latest
            ON latest.position_id = lot.position_id
           AND latest.max_sequence = lot.local_sequence
          JOIN venue_commands cmd
            ON cmd.command_id = lot.source_command_id
         WHERE lot.state IN (
             'OPTIMISTIC_EXPOSURE',
             'CONFIRMED_EXPOSURE',
             'EXIT_PENDING'
         )
         ORDER BY {lot_order}
        """
    ).fetchall()
    return {
        str(_row_mapping(row).get("runtime_position_id") or ""): str(
            _row_mapping(row).get("state") or ""
        )
        for row in rows
    }


def _load_current_position_authority_costs(
    conn: Any,
) -> dict[str, dict[str, Decimal]]:
    if not _has_table(conn, "venue_trade_facts"):
        return {}
    if not _has_table(conn, "venue_commands"):
        return {}
    current_entry_commands = """
        WHERE fact.command_id IN (
            SELECT scoped_cmd.command_id
              FROM venue_commands scoped_cmd
              JOIN position_current scoped_pc
                ON scoped_pc.position_id = scoped_cmd.position_id
             WHERE scoped_cmd.intent_kind = 'ENTRY'
               AND scoped_cmd.side = 'BUY'
               AND scoped_pc.phase IN ('active', 'day0_window', 'pending_exit')
               AND CASE
                   WHEN COALESCE(scoped_pc.chain_shares, 0) > 0
                   THEN scoped_pc.chain_shares
                   ELSE COALESCE(scoped_pc.shares, 0)
               END > 0
        )
    """
    sql = (
        "WITH "
        + canonical_trade_fact_cte(source_clause_sql=current_entry_commands)
        + ",\n"
        + economic_trade_fact_cte()
        + """
        SELECT cmd.position_id AS runtime_position_id,
               fact.state,
               fact.filled_size,
               fact.fill_price
          FROM economic_trade_fact fact
          JOIN venue_commands cmd ON cmd.command_id = fact.command_id
         WHERE cmd.intent_kind = 'ENTRY'
           AND cmd.side = 'BUY'
           AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
           AND CAST(COALESCE(fact.fill_price, '0') AS REAL) > 0
        """
    )
    costs: dict[str, dict[str, Decimal]] = {}
    for row in conn.execute(sql).fetchall():
        row_map = _row_mapping(row)
        try:
            cost = Decimal(str(row_map.get("filled_size"))) * Decimal(
                str(row_map.get("fill_price"))
            )
        except (InvalidOperation, TypeError, ValueError):
            continue
        position_id = str(row_map.get("runtime_position_id") or "")
        totals = costs.setdefault(
            position_id,
            {"confirmed_cost": Decimal("0"), "optimistic_cost": Decimal("0")},
        )
        key = (
            "confirmed_cost"
            if str(row_map.get("state") or "") == "CONFIRMED"
            else "optimistic_cost"
        )
        totals[key] += cost
    return costs


def _current_position_exposure_lots(row: Any) -> tuple[ExposureLot, ...]:
    row_map = _row_mapping(row)
    submit_payload = _coerce_payload(row_map.get("submit_payload_json"))
    allocation_raw = submit_payload.get("allocation", {})
    allocation = allocation_raw if isinstance(allocation_raw, Mapping) else {}
    market_id = str(
        row_map.get("market_id")
        or row_map.get("projection_market_id")
        or row_map.get("position_id")
    )
    event_id = str(allocation.get("event_id") or row_map.get("event_id") or market_id)
    resolution_window = str(allocation.get("resolution_window") or "default")
    correlation_key = _weather_family_correlation_key(
        family_id=(
            allocation.get("family_id")
            or allocation.get("correlation_key")
        ),
        city=row_map.get("city"),
        target_date=row_map.get("target_date"),
        metric=row_map.get("temperature_metric"),
        fallback=event_id,
    )
    direction = str(row_map.get("direction") or "")
    token_id = str(
        row_map.get("token_id")
        or (
            row_map.get("no_token_id")
            if direction == "buy_no"
            else row_map.get("yes_token_id")
        )
        or row_map.get("position_id")
    )
    shares = Decimal(str(row_map.get("shares") or 0))
    chain_shares = Decimal(str(row_map.get("chain_shares") or 0))
    entry_price = Decimal(str(row_map.get("entry_price") or 0))
    projection_cost = Decimal(str(row_map.get("cost_basis_usd") or 0))
    chain_cost = Decimal(str(row_map.get("chain_cost_basis_usd") or 0))
    exposure = max(projection_cost, chain_cost, max(shares, chain_shares) * entry_price)
    phase = str(row_map.get("phase") or "")
    fill_authority = str(row_map.get("fill_authority") or "")
    latest_lot_state = str(row_map.get("latest_lot_state") or "")
    confirmed_authorities = {
        FillAuthority.VENUE_POSITION_OBSERVED.value,
        FillAuthority.VENUE_CONFIRMED_PARTIAL.value,
        FillAuthority.VENUE_CONFIRMED_FULL.value,
        FillAuthority.CANCELLED_REMAINDER.value,
        FillAuthority.SETTLED.value,
    }

    def lot(amount: Decimal, state: ExposureState, source: str) -> ExposureLot:
        return ExposureLot(
            market_id=market_id,
            event_id=event_id,
            resolution_window=resolution_window,
            token_id=token_id,
            exposure_micro=_usd_exposure_micro(amount),
            state=state,
            correlation_key=correlation_key,
            source=source,
        )

    if phase == "pending_exit":
        return (lot(exposure, "EXIT_PENDING", "CHAIN" if chain_shares > 0 else "VENUE"),)
    if chain_shares > 0 or fill_authority in confirmed_authorities:
        return (lot(exposure, "CONFIRMED_EXPOSURE", "CHAIN" if chain_shares > 0 else "VENUE"),)

    confirmed = min(
        max(Decimal(str(row_map.get("confirmed_cost") or 0)), Decimal("0")),
        exposure,
    )
    optimistic = min(
        max(Decimal(str(row_map.get("optimistic_cost") or 0)), Decimal("0")),
        exposure - confirmed,
    )
    residual = exposure - confirmed - optimistic
    if residual > 0:
        if latest_lot_state == "OPTIMISTIC_EXPOSURE":
            optimistic += residual
        else:
            confirmed += residual

    components: list[ExposureLot] = []
    if confirmed > 0:
        components.append(lot(confirmed, "CONFIRMED_EXPOSURE", "VENUE"))
    if optimistic > 0:
        components.append(lot(optimistic, "OPTIMISTIC_EXPOSURE", "VENUE"))
    return tuple(components)


def _has_table(conn: Any, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _has_column(conn: Any, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    for row in rows:
        mapping = _row_mapping(row)
        if str(mapping.get("name") or "") == column:
            return True
    return False


def _latest_review_required_reason(conn: Any, command_id: str) -> str:
    if not _has_table(conn, "venue_command_events"):
        return ""
    try:
        row = conn.execute(
            """
            SELECT payload_json
              FROM venue_command_events
             WHERE command_id = ?
               AND event_type = 'REVIEW_REQUIRED'
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
    except Exception:
        return ""
    if row is None:
        return ""
    payload_raw = _row_mapping(row).get("payload_json")
    if not payload_raw:
        return ""
    try:
        payload = json.loads(str(payload_raw))
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("reason") or "").strip()


def _command_has_fact(conn: Any, table: str, command_id: str) -> bool:
    if not _has_table(conn, table):
        return False
    try:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE command_id = ? LIMIT 1",
            (command_id,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _review_required_has_materialized_entry_exposure(conn: Any, row: Mapping[str, Any]) -> bool:
    """Return true when REVIEW_REQUIRED has become known open exposure.

    A REVIEW_REQUIRED command is unsafe while the venue side-effect is unknown.
    Once the same command has a positive confirmed trade fact, an active exposure
    lot, and an open position_current projection, the risk is no longer unknown:
    it is canonical held exposure that should flow through monitor/redecision.
    """

    command_id = str(row.get("command_id") or "").strip()
    position_id = str(row.get("position_id") or "").strip()
    if not command_id or not position_id:
        return False
    if str(row.get("intent_kind") or "").upper() != "ENTRY":
        return False
    if str(row.get("side") or "").upper() != "BUY":
        return False
    required_tables = ("venue_trade_facts", "position_lots", "position_current")
    if any(not _has_table(conn, table) for table in required_tables):
        return False

    lot_row = conn.execute(
        """
        WITH latest_lot AS (
            SELECT lot.*
              FROM position_lots lot
              JOIN (
                    SELECT source_trade_fact_id, MAX(local_sequence) AS max_sequence
                      FROM position_lots
                     WHERE source_command_id = ?
                       AND source_trade_fact_id IS NOT NULL
                     GROUP BY source_trade_fact_id
              ) latest
                ON latest.source_trade_fact_id = lot.source_trade_fact_id
               AND latest.max_sequence = lot.local_sequence
        )
        SELECT 1
          FROM venue_trade_facts fact
          JOIN latest_lot lot
            ON lot.source_trade_fact_id = fact.trade_fact_id
         WHERE fact.command_id = ?
           AND fact.state = 'CONFIRMED'
           AND CAST(COALESCE(fact.filled_size, '0') AS REAL) > 0
           AND lot.source_command_id = ?
           AND lot.state IN ('CONFIRMED_EXPOSURE', 'EXIT_PENDING')
           AND CAST(COALESCE(lot.shares, '0') AS REAL) > 0
         LIMIT 1
        """,
        (
            command_id,
            command_id,
            command_id,
        ),
    ).fetchone()
    if lot_row is None:
        return False

    current_row = conn.execute(
        """
        SELECT 1
          FROM position_current
         WHERE position_id = ?
           AND phase IN ('active', 'day0_window', 'pending_exit')
           AND CAST(COALESCE(shares, chain_shares, 0) AS REAL) > 0
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    return current_row is not None


def _review_required_position_is_settled(conn: Any, row: Mapping[str, Any]) -> bool:
    """Return whether canonical lifecycle truth has ended all market risk."""

    position_id = str(row.get("position_id") or "").strip()
    if not position_id or not _has_table(conn, "position_current"):
        return False
    current_row = conn.execute(
        """
        SELECT 1
          FROM position_current
         WHERE position_id = ?
           AND phase = 'settled'
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    return current_row is not None


def _review_required_carries_submit_side_effect_risk(conn: Any, row: Mapping[str, Any]) -> bool:
    """Classify REVIEW_REQUIRED rows for the global unknown-side-effect latch.

    REVIEW_REQUIRED is a handoff state, not always proof that the venue side-effect
    boundary was crossed.  Pre-SDK recovery rows with no venue order id and no
    order/trade facts must not freeze reduce-only exits for unrelated held
    positions; actual unknown/ambiguous venue exposure remains counted.
    """

    command_id = str(row.get("command_id") or "")
    if _review_required_position_is_settled(conn, row):
        return False
    if _review_required_has_materialized_entry_exposure(conn, row):
        return False
    latest_reason = _latest_review_required_reason(conn, command_id)
    venue_order_id = str(row.get("venue_order_id") or "").strip()
    if latest_reason not in _PRE_SDK_REVIEW_REQUIRED_REASONS:
        return True
    if venue_order_id:
        return True
    if _command_has_fact(conn, "venue_order_facts", command_id):
        return True
    if _command_has_fact(conn, "venue_trade_facts", command_id):
        return True
    return False


def _gather_risky_unknown_rows(conn: Any) -> list[Mapping[str, Any]]:
    """Return venue_command rows still carrying unresolved submit-side-effect risk.

    Shared by ``count_unknown_side_effects`` (legacy tuple API) and
    ``classify_unknown_side_effect_scope`` (scope lattice) so both views derive
    from one classification pass over the same rows.
    """

    with _named_sqlite_rows(conn) as read_conn:
        has_venue_order_id = _has_column(read_conn, "venue_commands", "venue_order_id")
        venue_order_id_select = ", venue_order_id" if has_venue_order_id else ""
        rows = read_conn.execute(
            f"""
            SELECT command_id, market_id, position_id, intent_kind, side, state{venue_order_id_select}
            FROM venue_commands
            WHERE state IN (?, ?, ?)
            ORDER BY updated_at, command_id
            """,
            tuple(sorted(_UNRESOLVED_SIDE_EFFECT_STATES)),
        ).fetchall()
        risky_rows: list[Mapping[str, Any]] = []
        for raw_row in rows:
            row = _row_mapping(raw_row)
            state = str(row.get("state") or "")
            if state == "REVIEW_REQUIRED" and not _review_required_carries_submit_side_effect_risk(read_conn, row):
                continue
            risky_rows.append(row)
    return risky_rows


def count_unknown_side_effects(conn: Any) -> tuple[int, tuple[str, ...]]:
    """Count venue commands that still carry unresolved submit-side-effect risk.

    The public name is retained for compatibility with the A2 governor, but the
    object being counted is broader than one CommandState. REVIEW_REQUIRED and
    UNKNOWN are operator/recovery handoff states, not allocation clearance.

    Returns ``(total_count, scoped_markets)`` where ``scoped_markets`` is the
    sorted set of non-empty market_ids. Unscopeable rows (blank market_id) are
    counted in ``total_count`` but absent from ``scoped_markets`` — the gap that
    ``classify_unknown_side_effect_scope`` reads to fail closed.
    """

    risky_rows = _gather_risky_unknown_rows(conn)
    markets = tuple(
        sorted({str(row.get("market_id") or "") for row in risky_rows if str(row.get("market_id") or "")})
    )
    return len(risky_rows), markets


@dataclass(frozen=True)
class UnknownSideEffectScope:
    """Scope classification of unresolved unknown submit side effects.

    ``scoped_markets``    — distinct non-empty market_ids that can be isolated
                            per-market (the line-186 ``unknown_side_effect_same_market``
                            path keys on these).
    ``unscopeable_count`` — risky rows whose market_id is blank/ambiguous and so
                            cannot be bound to one market: FAIL CLOSED -> systemic.
    ``systemic_count``    — count of unknowns escalated to GLOBAL: all unscopeable
                            rows, plus the full risky count when distinct scoped
                            markets reach ``systemic_market_count_limit``.
    """

    total_count: int
    scoped_markets: tuple[str, ...]
    unscopeable_count: int
    systemic_count: int

    @property
    def is_systemic(self) -> bool:
        return self.systemic_count > 0


def classify_unknown_side_effect_scope(conn: Any, cap_policy: CapPolicy | None = None) -> UnknownSideEffectScope:
    """Classify unresolved unknown side effects into SCOPED vs SYSTEMIC.

    Fail-closed rule (governor scope-lattice decision 2026-06-22): an unknown that
    cannot be confidently scoped to a single market_id (blank/ambiguous market_id)
    is SYSTEMIC. A cluster spanning ``systemic_market_count_limit`` or more distinct
    independent markets is SYSTEMIC. Otherwise a single scoped market's unknown(s)
    are SCOPED and isolate only that market.
    """

    policy = cap_policy or CapPolicy()
    risky_rows = _gather_risky_unknown_rows(conn)
    total = len(risky_rows)
    scoped_markets = tuple(
        sorted({str(row.get("market_id") or "") for row in risky_rows if str(row.get("market_id") or "")})
    )
    unscopeable_count = sum(1 for row in risky_rows if not str(row.get("market_id") or "").strip())
    cross_market_systemic = len(scoped_markets) >= int(policy.systemic_market_count_limit)
    # Unscopeable rows are always systemic. When the distinct scoped-market count
    # reaches the systemic limit, the entire cluster escalates to global.
    systemic_count = total if cross_market_systemic else unscopeable_count
    return UnknownSideEffectScope(
        total_count=total,
        scoped_markets=scoped_markets,
        unscopeable_count=unscopeable_count,
        systemic_count=systemic_count,
    )


def _systemic_unknown_present(governor_state: GovernorState) -> bool:
    """Return True when GovernorState carries SYSTEMIC unknown side effects.

    SYSTEMIC trips the GLOBAL reduce_only latch. The fail-closed default applies
    when an unknown count is present but no scope classification accompanies it
    (no scoped markets and no explicit systemic count): the unknown is unscopeable
    by absence of evidence and must freeze globally until scoping exists.
    """

    if governor_state.systemic_unknown_side_effect_count > 0:
        return True
    if governor_state.unknown_side_effect_count <= 0:
        return False
    # Unknown(s) present. If at least one market is scoped, treat as SCOPED
    # (isolated per-market). If NO scope evidence accompanies the count, fail closed.
    return not governor_state.unknown_side_effect_markets


def count_open_reconcile_findings(conn: Any) -> int:
    with _named_sqlite_rows(conn) as read_conn:
        row = read_conn.execute(
            "SELECT COUNT(*) AS count FROM exchange_reconcile_findings WHERE resolved_at IS NULL"
        ).fetchone()
    if row is None:
        return 0
    return int(_row_mapping(row).get("count", 0) or 0)


def _intent_notional_micro(intent: ExecutionIntent) -> int:
    raw = getattr(intent, "target_size_usd", 0.0)
    return int((Decimal(str(raw)) * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING))


def _intent_event_id(intent: ExecutionIntent) -> str:
    return str(getattr(intent, "event_id", None) or getattr(intent, "market_id", ""))


def _is_reduce_only_intent(intent: ExecutionIntent) -> bool:
    return bool(getattr(intent, "reduce_only", False) or getattr(intent, "intent_kind", "") in {"EXIT", "SELL", "REDUCE_ONLY"})


def _coerce_heartbeat_health(value: HeartbeatStatus | Mapping[str, Any] | Any) -> HeartbeatHealth:
    raw = getattr(value, "health", None) or _mapping_get(value, "health", HeartbeatHealth.LOST)
    if isinstance(raw, HeartbeatHealth):
        return raw
    return HeartbeatHealth(str(raw).split(".")[-1])


def _coerce_risk_level(value: RiskLevel | str | Any) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    return RiskLevel(str(value).split(".")[-1])


def _mapping_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _snapshot_depth_micro(snapshot: Any) -> int:
    for name in ("orderbook_depth_micro", "depth_micro"):
        raw = getattr(snapshot, name, None)
        if raw is not None:
            return int(raw)
    depth_raw = getattr(snapshot, "orderbook_depth_jsonb", None)
    if isinstance(depth_raw, str) and depth_raw:
        try:
            payload = json.loads(depth_raw)
            for key in ("depth_micro", "bid_depth_micro", "ask_depth_micro"):
                if key in payload and payload[key] is not None:
                    return int(payload[key])
            return _orderbook_json_depth_micro(payload)
        except Exception:
            return 0
    return 0


def _seconds_to_close(snapshot: Any) -> int | None:
    raw = getattr(snapshot, "seconds_to_resolution", None)
    if raw is not None:
        return int(raw)
    for name in ("market_close_at", "market_end_at", "sports_start_at"):
        close_at = getattr(snapshot, name, None)
        if close_at is None:
            continue
        if isinstance(close_at, datetime):
            if close_at.tzinfo is None:
                close_at = close_at.replace(tzinfo=timezone.utc)
            return int((close_at - datetime.now(timezone.utc)).total_seconds())
    return None


def _orderbook_json_depth_micro(payload: Mapping[str, Any]) -> int:
    """Compute approximate pUSD notional depth from serialized CLOB book JSON."""

    total = Decimal("0")
    for side in ("bids", "asks"):
        levels = payload.get(side)
        if not isinstance(levels, Sequence) or isinstance(levels, (str, bytes)):
            continue
        for level in levels:
            price: Any = None
            size: Any = None
            if isinstance(level, Mapping):
                price = level.get("price")
                size = level.get("size")
            elif isinstance(level, Sequence) and not isinstance(level, (str, bytes)) and len(level) >= 2:
                price = level[0]
                size = level[1]
            if price is None or size is None:
                continue
            try:
                total += Decimal(str(price)) * Decimal(str(size))
            except Exception:
                continue
    return int((total * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING))


class _EmptySnapshot:
    orderbook_depth_micro = 0


def _automatic_kill_switch_reason(
    policy: CapPolicy,
    *,
    ws_gap_active: bool,
    ws_gap_seconds: int,
    unknown_side_effect_count: int,
    reconcile_finding_count: int,
) -> str | None:
    if ws_gap_active and ws_gap_seconds > policy.ws_gap_seconds_limit:
        return "ws_gap_threshold"
    return None


def _row_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


@contextmanager
def _named_sqlite_rows(conn: Any) -> Iterator[Any]:
    """Ensure sqlite reads return name-addressable rows, then restore caller state."""

    if not isinstance(conn, sqlite3.Connection):
        yield conn
        return
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.row_factory = previous_factory


def _coerce_payload(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, Mapping) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _lot_exposure_micro(shares: Any, entry_price_avg: Any) -> int:
    return int(
        (
            Decimal(str(shares or 0))
            * Decimal(str(entry_price_avg or 0))
            * Decimal("1000000")
        ).to_integral_value(rounding=ROUND_CEILING)
    )


def _usd_exposure_micro(value: Decimal) -> int:
    return int(
        (value * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING)
    )
