# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A2.yaml
"""Risk allocation and portfolio-governor gates for R3 A2.

This module is a blocking allocation surface, not a venue client.  It computes
capacity and kill-switch decisions from supplied evidence and never submits,
cancels, redeems, mutates production DB/state artifacts, or authorizes cutover.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from src.control.heartbeat_supervisor import HeartbeatHealth, HeartbeatStatus
from src.contracts.execution_intent import ExecutionIntent
from src.riskguard.risk_level import RiskLevel

OrderMode = Literal["MAKER", "TAKER", "NO_TRADE"]
ExposureState = Literal[
    "OPTIMISTIC_EXPOSURE",
    "CONFIRMED_EXPOSURE",
    "EXIT_PENDING",
    "ECONOMICALLY_CLOSED_OPTIMISTIC",
    "ECONOMICALLY_CLOSED_CONFIRMED",
    "SETTLED",
    "QUARANTINED",
]

_ACTIVE_EXPOSURE_STATES = {"OPTIMISTIC_EXPOSURE", "CONFIRMED_EXPOSURE", "EXIT_PENDING"}
_CLOSED_EXPOSURE_STATES = {"ECONOMICALLY_CLOSED_OPTIMISTIC", "ECONOMICALLY_CLOSED_CONFIRMED", "SETTLED"}
_UNRESOLVED_SIDE_EFFECT_STATES = {
    "SUBMIT_UNKNOWN_SIDE_EFFECT",
    "UNKNOWN",
    "REVIEW_REQUIRED",
}


@dataclass(frozen=True)
class CapPolicy:
    max_per_market_micro: int = 250_000_000
    max_per_event_micro: int = 500_000_000
    max_per_resolution_window_micro: dict[str, int] = field(default_factory=lambda: {"default": 750_000_000})
    max_drawdown_pct: float = 10.0
    max_correlated_exposure_micro: int = 1_000_000_000
    unknown_side_effect_limit: int = 0
    reconcile_finding_limit: int = 0
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
        if float(self.max_drawdown_pct) < 0:
            raise ValueError("max_drawdown_pct must be non-negative")
        if int(self.unknown_side_effect_limit) < 0 or int(self.reconcile_finding_limit) < 0 or int(self.ws_gap_seconds_limit) < 0:
            raise ValueError("kill-switch thresholds must be non-negative")
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
    risk_level: RiskLevel = RiskLevel.GREEN
    unknown_side_effect_markets: tuple[str, ...] = ()
    manual_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_drawdown_pct": self.current_drawdown_pct,
            "heartbeat_health": self.heartbeat_health.value,
            "ws_gap_active": self.ws_gap_active,
            "ws_gap_seconds": self.ws_gap_seconds,
            "unknown_side_effect_count": self.unknown_side_effect_count,
            "reconcile_finding_count": self.reconcile_finding_count,
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
    remaining_market_capacity_micro: int = 0
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

    @property
    def exposure_lots(self) -> tuple[ExposureLot, ...]:
        return self._lots

    def with_lots(self, exposure_lots: Sequence[ExposureLot]) -> "RiskAllocator":
        return RiskAllocator(self.cap_policy, exposure_lots)

    @classmethod
    def from_position_lots(cls, conn: Any, cap_policy: CapPolicy | None = None) -> "RiskAllocator":
        """Build allocator capacity from canonical ``position_lots`` truth.

        The table is append-only, so only the latest lot row per position_id is
        active for capacity.  This is read-only: the allocator never repairs,
        inserts, or mutates the lot ledger.
        """

        return cls(cap_policy, load_position_lots(conn))

    def can_allocate(self, intent: ExecutionIntent, governor_state: GovernorState) -> AllocationDecision:
        requested = _intent_notional_micro(intent)
        reduce_only = _is_reduce_only_intent(intent)
        confirmed, optimistic, weighted_market = self._market_exposure(str(intent.market_id))
        remaining_market = max(int(self.cap_policy.max_per_market_micro) - weighted_market, 0)
        base = {
            "requested_micro": requested,
            "remaining_market_capacity_micro": remaining_market,
            "confirmed_exposure_micro": confirmed,
            "optimistic_exposure_micro": optimistic,
            "weighted_existing_exposure_micro": weighted_market,
            "reduce_only": reduce_only,
        }
        kill_reason = self.kill_switch_reason(governor_state)
        if kill_reason:
            return AllocationDecision(False, kill_reason, **base)
        if str(intent.market_id) in set(governor_state.unknown_side_effect_markets):
            return AllocationDecision(False, "unknown_side_effect_same_market", **base)
        if self.reduce_only_mode_active(governor_state) and not reduce_only:
            return AllocationDecision(False, "reduce_only_mode_active", **base)
        if requested > remaining_market:
            return AllocationDecision(False, "per_market_cap_exceeded", **base)
        event_remaining = self._remaining_capacity("event", str(_intent_event_id(intent)), self.cap_policy.max_per_event_micro)
        if requested > event_remaining:
            return AllocationDecision(False, "per_event_cap_exceeded", **base)
        resolution_label = str(getattr(intent, "resolution_window", "default") or "default")
        window_cap = self.cap_policy.max_per_resolution_window_micro.get(
            resolution_label,
            self.cap_policy.max_per_resolution_window_micro.get("default", self.cap_policy.max_per_event_micro),
        )
        if requested > self._remaining_capacity("resolution", resolution_label, window_cap):
            return AllocationDecision(False, "per_resolution_window_cap_exceeded", **base)
        correlation_key = str(getattr(intent, "correlation_key", None) or _intent_event_id(intent))
        if requested > self._remaining_capacity("correlation", correlation_key, self.cap_policy.max_correlated_exposure_micro):
            return AllocationDecision(False, "correlated_market_cap_exceeded", **base)
        return AllocationDecision(True, "allowed", **base)

    def maker_or_taker(self, snapshot: Any, governor_state: GovernorState) -> OrderMode:
        if self.kill_switch_reason(governor_state):
            return "NO_TRADE"
        if governor_state.heartbeat_health is HeartbeatHealth.LOST:
            return "NO_TRADE"
        if governor_state.heartbeat_health in {HeartbeatHealth.DEGRADED, HeartbeatHealth.DISABLED_FOR_NON_RESTING_ONLY, HeartbeatHealth.STARTING}:
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
        if governor_state.heartbeat_health in {HeartbeatHealth.STARTING, HeartbeatHealth.DEGRADED, HeartbeatHealth.LOST, HeartbeatHealth.DISABLED_FOR_NON_RESTING_ONLY}:
            return True
        if governor_state.ws_gap_active:
            return True
        if governor_state.unknown_side_effect_count > 0 or governor_state.reconcile_finding_count > 0:
            return True
        return governor_state.risk_level in {RiskLevel.DATA_DEGRADED, RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED}

    def kill_switch_reason(self, governor_state: GovernorState) -> str | None:
        policy = self.cap_policy
        if governor_state.kill_switch_armed:
            return governor_state.manual_reason or "kill_switch_armed"
        if governor_state.heartbeat_health is HeartbeatHealth.LOST:
            return "heartbeat_lost"
        if governor_state.ws_gap_active and governor_state.ws_gap_seconds > policy.ws_gap_seconds_limit:
            return "ws_gap_threshold"
        if governor_state.unknown_side_effect_count > policy.unknown_side_effect_limit:
            return "unknown_side_effect_threshold"
        if governor_state.reconcile_finding_count > policy.reconcile_finding_limit:
            return "reconcile_finding_threshold"
        if governor_state.current_drawdown_pct >= policy.max_drawdown_pct:
            return "drawdown_threshold"
        return None

    def _market_exposure(self, market_id: str) -> tuple[int, int, int]:
        confirmed = 0
        optimistic = 0
        weighted = 0
        for lot in self._lots:
            if lot.market_id != market_id or lot.state in _CLOSED_EXPOSURE_STATES:
                continue
            if lot.state == "OPTIMISTIC_EXPOSURE":
                optimistic += int(lot.exposure_micro)
                weighted += int(round(lot.exposure_micro * self.cap_policy.optimistic_exposure_weight))
            elif lot.state in _ACTIVE_EXPOSURE_STATES:
                confirmed += int(lot.exposure_micro)
                weighted += int(lot.exposure_micro)
        return confirmed, optimistic, weighted

    def _remaining_capacity(self, scope: str, key: str, cap: int) -> int:
        exposure = 0
        for lot in self._lots:
            if lot.state in _CLOSED_EXPOSURE_STATES:
                continue
            if scope == "event" and lot.event_id != key:
                continue
            if scope == "resolution" and lot.resolution_window != key:
                continue
            if scope == "correlation" and (lot.correlation_key or lot.event_id) != key:
                continue
            exposure += self._weighted_lot_exposure(lot)
        return max(int(cap) - exposure, 0)

    def _weighted_lot_exposure(self, lot: ExposureLot) -> int:
        if lot.state == "OPTIMISTIC_EXPOSURE":
            return int(round(lot.exposure_micro * self.cap_policy.optimistic_exposure_weight))
        if lot.state in _ACTIVE_EXPOSURE_STATES:
            return int(lot.exposure_micro)
        return 0


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
        ws_gap_active = bool(_mapping_get(ws_status, "m5_reconcile_required", False) or _mapping_get(ws_status, "ws_gap_active", False))
        ws_gap_seconds = int(_mapping_get(ws_status, "gap_seconds", 0) or _mapping_get(ws_status, "ws_gap_seconds", 0) or 0)
        drawdown = float(getattr(ledger, "current_drawdown_pct", _mapping_get(ledger, "current_drawdown_pct", 0.0)) or 0.0)
        risk_level = _coerce_risk_level(getattr(ledger, "risk_level", _mapping_get(ledger, "risk_level", RiskLevel.GREEN)))
        automatic_reason = _automatic_kill_switch_reason(
            self.cap_policy,
            current_drawdown_pct=drawdown,
            heartbeat_health=health,
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


def configure_global_allocator(allocator: RiskAllocator | None, governor_state: GovernorState | None = None) -> None:
    global _GLOBAL_ALLOCATOR, _GLOBAL_GOVERNOR_STATE
    _GLOBAL_ALLOCATOR = allocator
    _GLOBAL_GOVERNOR_STATE = governor_state


def configure_global_governor_state(governor_state: GovernorState | None) -> None:
    global _GLOBAL_GOVERNOR_STATE
    _GLOBAL_GOVERNOR_STATE = governor_state


def clear_global_allocator() -> None:
    configure_global_allocator(None, None)


def assert_global_allocation_allows(intent: ExecutionIntent) -> AllocationDecision:
    if _GLOBAL_ALLOCATOR is None or _GLOBAL_GOVERNOR_STATE is None:
        decision = AllocationDecision(False, "allocator_not_configured", 0)
        raise AllocationDenied(decision)
    decision = _GLOBAL_ALLOCATOR.can_allocate(intent, _GLOBAL_GOVERNOR_STATE)
    if not decision.allowed:
        raise AllocationDenied(decision)
    return decision


def assert_global_submit_allows(*, reduce_only: bool = False) -> AllocationDecision:
    """Guard non-entry submits against global kill-switch state.

    Exits may continue through reduce-only modes, but a true kill-switch reason
    blocks all submit paths before command persistence or SDK contact.
    """

    if _GLOBAL_ALLOCATOR is None or _GLOBAL_GOVERNOR_STATE is None:
        decision = AllocationDecision(False, "allocator_not_configured", 0, reduce_only=reduce_only)
        raise AllocationDenied(decision)
    allocator = _GLOBAL_ALLOCATOR
    kill_reason = allocator.kill_switch_reason(_GLOBAL_GOVERNOR_STATE)
    if kill_reason:
        decision = AllocationDecision(False, kill_reason, 0, reduce_only=reduce_only)
        raise AllocationDenied(decision)
    if not reduce_only and allocator.reduce_only_mode_active(_GLOBAL_GOVERNOR_STATE):
        decision = AllocationDecision(False, "reduce_only_mode_active", 0, reduce_only=reduce_only)
        raise AllocationDenied(decision)
    return AllocationDecision(True, "allowed", 0, reduce_only=reduce_only)


def select_global_order_type(snapshot: Any) -> str:
    """Return the concrete venue order type allowed by the current governor.

    A2's maker/taker switch is behavior-changing: healthy/deep/far-from-close
    conditions may rest as ``GTC``; degraded heartbeat, shallow books, or near
    resolution force immediate-or-cancel semantics (``FOK``).  True no-trade
    states raise ``AllocationDenied`` so callers block before persistence/SDK.
    """

    if _GLOBAL_GOVERNOR_STATE is None:
        raise AllocationDenied(AllocationDecision(False, "allocator_not_configured", 0))
    if _GLOBAL_ALLOCATOR is None:
        raise AllocationDenied(AllocationDecision(False, "allocator_not_configured", 0))
    allocator = _GLOBAL_ALLOCATOR
    mode = allocator.maker_or_taker(snapshot or _EmptySnapshot(), _GLOBAL_GOVERNOR_STATE)
    if mode == "NO_TRADE":
        reason = allocator.kill_switch_reason(_GLOBAL_GOVERNOR_STATE) or "no_trade_mode"
        raise AllocationDenied(AllocationDecision(False, reason, 0))
    if mode == "TAKER":
        return "FOK"
    return "GTC"


def summary() -> dict[str, Any]:
    if _GLOBAL_GOVERNOR_STATE is None:
        return {"configured": False, "entry": {"allow_submit": False, "reason": "allocator_not_configured"}}
    if _GLOBAL_ALLOCATOR is None:
        return {
            "configured": False,
            "state": _GLOBAL_GOVERNOR_STATE.to_dict(),
            "kill_switch_reason": "allocator_not_configured",
            "reduce_only": True,
            "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
        }
    allocator = _GLOBAL_ALLOCATOR
    kill_reason = allocator.kill_switch_reason(_GLOBAL_GOVERNOR_STATE)
    return {
        "configured": _GLOBAL_ALLOCATOR is not None,
        "state": _GLOBAL_GOVERNOR_STATE.to_dict(),
        "kill_switch_reason": kill_reason,
        "reduce_only": allocator.reduce_only_mode_active(_GLOBAL_GOVERNOR_STATE),
        "entry": {"allow_submit": kill_reason is None, "reason": kill_reason or "ok"},
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
    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    finding_count = count_open_reconcile_findings(conn)
    governor = get_global_governor(policy)
    governor_state = governor.update_state(
        ledger,
        heartbeat,
        ws_status,
        unknown_count=unknown_count,
        finding_count=finding_count,
    )
    if unknown_markets:
        governor_state = replace(governor_state, unknown_side_effect_markets=tuple(unknown_markets))
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
        max_drawdown_pct=float(data.get("max_drawdown_pct", CapPolicy().max_drawdown_pct)),
        max_correlated_exposure_micro=int(data.get("max_correlated_exposure_micro", CapPolicy().max_correlated_exposure_micro)),
        unknown_side_effect_limit=int(data.get("unknown_side_effect_limit", CapPolicy().unknown_side_effect_limit)),
        reconcile_finding_limit=int(data.get("reconcile_finding_limit", CapPolicy().reconcile_finding_limit)),
        ws_gap_seconds_limit=int(data.get("ws_gap_seconds_limit", CapPolicy().ws_gap_seconds_limit)),
        optimistic_exposure_weight=float(data.get("optimistic_exposure_weight", CapPolicy().optimistic_exposure_weight)),
        taker_min_depth_micro=int(data.get("taker_min_depth_micro", CapPolicy().taker_min_depth_micro)),
        maker_deadline_seconds=int(data.get("maker_deadline_seconds", CapPolicy().maker_deadline_seconds)),
    )


def load_position_lots(conn: Any) -> tuple[ExposureLot, ...]:
    """Read active exposure lots from ``position_lots`` without mutation."""

    rows = conn.execute(
        """
        SELECT
          lot.position_id,
          lot.state,
          lot.shares,
          lot.entry_price_avg,
          lot.source,
          lot.raw_payload_json,
          (
            SELECT event.payload_json
            FROM venue_command_events event
            WHERE event.command_id = cmd.command_id
              AND event.event_type = 'SUBMIT_REQUESTED'
            ORDER BY event.sequence_no DESC
            LIMIT 1
          ) AS submit_payload_json,
          COALESCE(cmd.market_id, CAST(lot.position_id AS TEXT)) AS market_id,
          COALESCE(cmd.token_id, CAST(lot.position_id AS TEXT)) AS token_id,
          COALESCE(cmd.decision_id, cmd.market_id, CAST(lot.position_id AS TEXT)) AS event_id
        FROM position_lots lot
        JOIN (
          SELECT position_id, MAX(local_sequence) AS max_sequence
          FROM position_lots
          GROUP BY position_id
        ) latest
          ON latest.position_id = lot.position_id
         AND latest.max_sequence = lot.local_sequence
        LEFT JOIN venue_commands cmd ON cmd.command_id = lot.source_command_id
        WHERE lot.state IN (
          'OPTIMISTIC_EXPOSURE',
          'CONFIRMED_EXPOSURE',
          'EXIT_PENDING'
        )
        ORDER BY lot.position_id, lot.lot_id
        """
    ).fetchall()
    lots: list[ExposureLot] = []
    for row in rows:
        row_map = _row_mapping(row)
        payload = _coerce_payload(row_map.get("raw_payload_json"))
        submit_payload = _coerce_payload(row_map.get("submit_payload_json"))
        allocation_payload_raw = submit_payload.get("allocation", {}) if isinstance(submit_payload, Mapping) else {}
        allocation_payload = allocation_payload_raw if isinstance(allocation_payload_raw, Mapping) else {}
        market_id = str(payload.get("market_id") or row_map.get("market_id") or row_map.get("position_id"))
        event_id = str(allocation_payload.get("event_id") or payload.get("event_id") or row_map.get("event_id") or market_id)
        resolution_window = str(allocation_payload.get("resolution_window") or payload.get("resolution_window") or payload.get("window_label") or "default")
        correlation_key = str(allocation_payload.get("correlation_key") or payload.get("correlation_key") or event_id)
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
    return tuple(lots)


def count_unknown_side_effects(conn: Any) -> tuple[int, tuple[str, ...]]:
    """Count venue commands that still carry unresolved submit-side-effect risk.

    The public name is retained for compatibility with the A2 governor, but the
    object being counted is broader than one CommandState. REVIEW_REQUIRED and
    UNKNOWN are operator/recovery handoff states, not allocation clearance.
    """

    rows = conn.execute(
        """
        SELECT market_id
        FROM venue_commands
        WHERE state IN (?, ?, ?)
        ORDER BY updated_at, command_id
        """,
        tuple(sorted(_UNRESOLVED_SIDE_EFFECT_STATES)),
    ).fetchall()
    markets = tuple(
        sorted({str(_row_mapping(row).get("market_id") or "") for row in rows if str(_row_mapping(row).get("market_id") or "")})
    )
    return len(rows), markets


def count_open_reconcile_findings(conn: Any) -> int:
    row = conn.execute(
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
    current_drawdown_pct: float,
    heartbeat_health: HeartbeatHealth,
    ws_gap_active: bool,
    ws_gap_seconds: int,
    unknown_side_effect_count: int,
    reconcile_finding_count: int,
) -> str | None:
    if heartbeat_health is HeartbeatHealth.LOST:
        return "heartbeat_lost"
    if ws_gap_active and ws_gap_seconds > policy.ws_gap_seconds_limit:
        return "ws_gap_threshold"
    if unknown_side_effect_count > policy.unknown_side_effect_limit:
        return "unknown_side_effect_threshold"
    if reconcile_finding_count > policy.reconcile_finding_limit:
        return "reconcile_finding_threshold"
    if current_drawdown_pct >= policy.max_drawdown_pct:
        return "drawdown_threshold"
    return None


def _row_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


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
