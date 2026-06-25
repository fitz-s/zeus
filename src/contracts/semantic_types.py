"""Typed semantic boundaries and observable helpers for lifecycle invariants."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping


DirectionAlias = str  # Temporary backward-compatibility for untyped downstream inputs

class Direction(str, Enum):
    YES = "buy_yes"
    NO = "buy_no"
    UNKNOWN = "unknown"

class LifecycleState(str, Enum):
    PENDING_TRACKED = "pending_tracked"
    PENDING_EXIT = "pending_exit"
    ENTERED = "entered"
    HOLDING = "holding"
    DAY0_WINDOW = "day0_window"
    QUARANTINED = "quarantined"
    ECONOMICALLY_CLOSED = "economically_closed"
    SETTLED = "settled"
    VOIDED = "voided"
    ADMIN_CLOSED = "admin_closed"

class ChainState(str, Enum):
    """Per-position venue visibility status (NOT per-cycle snapshot completeness).

    Finding 7 (PR B, 2026-05-27): The name `ChainState` is shared with
    `src/state/chain_state.py.ChainState` (per-cycle snapshot completeness).
    These are different real-world objects. New code SHOULD import the
    domain-specific alias below; legacy imports of `ChainState` remain wire-
    compatible.
    """

    UNKNOWN = "unknown"
    SYNCED = "synced"
    LOCAL_ONLY = "local_only"
    CHAIN_ONLY = "chain_only"
    EXIT_PENDING_MISSING = "exit_pending_missing"
    QUARANTINED = "quarantined"
    QUARANTINE_EXPIRED = "quarantine_expired"
    SIZE_MISMATCH_UNRESOLVED = "size_mismatch_unresolved"
    # Terminal closed-class: a position whose CTF tokens left the wallet via an
    # operator-confirmed EXTERNAL close (the operator manually sold Zeus's position on
    # the shared proxy wallet — incident chain 2026-06-10). Written by
    # src.execution.exchange_reconcile._tag_external_operator_closed_position_holdings.
    # Like other terminal/no-on-chain-holding states it carries NO exposure, NO entry
    # eligibility, and is EXCLUDED from drift expected-wallet holdings
    # (_CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES deliberately omits it). Registered
    # here so every consumer that coerces the chain_state column (Position.__post_init__
    # -> VenueVisibilityStatus(value)) constructs a valid member instead of raising
    # "not a valid ChainState" — writer-set MUST be a subset of this enum.
    EXTERNAL_OPERATOR_CLOSED = "external_operator_closed"
    # Terminal closed-class: the exit chain-truth gate proved the on-chain CTF
    # balance is ZERO and voided the position (exit_lifecycle.
    # _void_chain_confirmed_zero, Fix A 2026-05-19). The writer existed for
    # weeks but NEVER fired before 2026-06-12 (its funder-address env vars were
    # absent, so the gate was silently bypassed); the first live firing wrote
    # this value and every load_portfolio() — including the RiskGuard daemon's
    # — crashed on enum coercion, killing risk attestations and fail-closing
    # the entry gate to RED. Writer-set MUST be a subset of this enum.
    CHAIN_CONFIRMED_ZERO = "chain_confirmed_zero"
    # Attribution-quarantine class: chain reconciliation found a CONFIRMED
    # position whose on-chain CTF balance is absent at the attributed location
    # (src.state.chain_reconciliation._quarantine_confirmed_chain_absence writes
    # it via the named constant CONFIRMED_CHAIN_ABSENCE_CHAIN_STATE, alongside
    # state=QUARANTINED — it quarantines for attribution review instead of
    # phantom-voiding; root incident: shared-wallet commingling, the 2026-06-09
    # Hong Kong token). Carries NO clean attributable on-chain holding, so like
    # CHAIN_CONFIRMED_ZERO / EXTERNAL_OPERATOR_CLOSED it is a no-exposure terminal-
    # ish class and is deliberately OMITTED from _CLOSED_POSITION_WALLET_HOLDING_
    # CHAIN_STATES (no expected wallet holding). Registered here because the writer
    # set MUST be a subset of this enum: it escaped via a NAMED CONSTANT (the
    # literal-only vocabulary antibody missed it), poisoning 9 live positions'
    # load_portfolio on 2026-06-22 (Tokyo/Seoul/Houston/Denver/Milan/...). The
    # antibody is now strengthened to resolve *_CHAIN_STATE constants too.
    CHAIN_ABSENT_CONFIRMED_UNATTRIBUTED = "chain_absent_confirmed_position_unattributed"


# Domain-specific alias (Finding 7 / PR B). Prefer this name in new code.
VenueVisibilityStatus = ChainState

class ExitState(str, Enum):
    """Live sell-order state machine for exit lifecycle."""
    NONE = ""
    EXIT_INTENT = "exit_intent"
    SELL_PLACED = "sell_placed"
    SELL_PENDING = "sell_pending"
    SELL_FILLED = "sell_filled"
    RETRY_PENDING = "retry_pending"
    BACKOFF_EXHAUSTED = "backoff_exhausted"

class RejectionStage(str, Enum):
    AUTHORITY_GATE = "AUTHORITY_GATE"
    SIGNAL_QUALITY = "SIGNAL_QUALITY"
    CALIBRATION_IMMATURE = "CALIBRATION_IMMATURE"
    OBSERVATION_SOURCE_UNAUTHORIZED = "OBSERVATION_SOURCE_UNAUTHORIZED"
    OBSERVATION_UNAVAILABLE_LOW = "OBSERVATION_UNAVAILABLE_LOW"
    CAUSAL_SLOT_NOT_OK = "CAUSAL_SLOT_NOT_OK"
    MARKET_FILTER = "MARKET_FILTER"
    ANTI_CHURN = "ANTI_CHURN"
    ORACLE_EVIDENCE_UNAVAILABLE = "ORACLE_EVIDENCE_UNAVAILABLE"
    ORACLE_BLACKLISTED = "ORACLE_BLACKLISTED"
    SIZING_TOO_SMALL = "SIZING_TOO_SMALL"
    SIZING_ERROR = "SIZING_ERROR"
    RISK_REJECTED = "RISK_REJECTED"
    EDGE_INSUFFICIENT = "EDGE_INSUFFICIENT"
    FDR_FILTERED = "FDR_FILTERED"
    FDR_SELECTION_UNEXECUTABLE = "FDR_SELECTION_UNEXECUTABLE"
    FDR_FAMILY_SCAN_UNAVAILABLE = "FDR_FAMILY_SCAN_UNAVAILABLE"
    EXECUTION_PRICE_UNAVAILABLE = "EXECUTION_PRICE_UNAVAILABLE"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    MARKET_LIQUIDITY = "MARKET_LIQUIDITY"
    # DDD v2 live-wiring rejection stages (RERUN_PLAN_v2.md §5 D-E, 2026-05-03):
    DDD_HALT = "DDD_HALT"
    DDD_CITY_UNCONFIGURED = "DDD_CITY_UNCONFIGURED"
    DDD_NO_TRAIN_DATA = "DDD_NO_TRAIN_DATA"
    DDD_EXCLUDED_WORKSTREAM_A = "DDD_EXCLUDED_WORKSTREAM_A"
    DDD_UNKNOWN_STATUS = "DDD_UNKNOWN_STATUS"
    DDD_CONFIG_MISSING = "DDD_CONFIG_MISSING"
    UNSUPPORTED_CALIBRATION_SOURCE_ID = "UNSUPPORTED_CALIBRATION_SOURCE_ID"
    FORECAST_PROVENANCE_INCOMPLETE = "FORECAST_PROVENANCE_INCOMPLETE"
    UNKNOWN_FORECAST_SOURCE_FAMILY = "UNKNOWN_FORECAST_SOURCE_FAMILY"
    FORECAST_PROVENANCE_INCONSISTENT = "FORECAST_PROVENANCE_INCONSISTENT"

class EntryMethod(str, Enum):
    """Known probability refresh methods carried by Position across modules."""

    ENS_MEMBER_COUNTING = "ens_member_counting"
    DAY0_OBSERVATION = "day0_observation"

    @classmethod
    def from_value(cls, value: str | EntryMethod | None) -> "EntryMethod":
        if isinstance(value, cls):
            return value
        if value is None or value == "":
            return cls.ENS_MEMBER_COUNTING
        return cls(value)


@dataclass(frozen=True)
class HeldSideProbability:
    """Probability in the native space of the held side."""

    value: float
    direction: DirectionAlias

    def __post_init__(self) -> None:
        if self.direction not in {Direction.YES, Direction.NO, "buy_yes", "buy_no"}:
            raise ValueError(f"Pricing requires concrete buy_yes/buy_no, got {self.direction}")
        if not 0.0 <= float(self.value) <= 1.0:
            raise ValueError(f"Held-side probability must be in [0, 1], got {self.value}")

    def __float__(self) -> float:
        return float(self.value)

    def __rsub__(self, other: object) -> float:
        raise TypeError(
            "Cross-space conversion from bare float is forbidden for HeldSideProbability. "
            "Construct a new semantic value explicitly."
        )


@dataclass(frozen=True)
class NativeSidePrice:
    """Market price in the native space of the held side."""

    value: float
    direction: DirectionAlias

    def __post_init__(self) -> None:
        if self.direction not in {Direction.YES, Direction.NO, "buy_yes", "buy_no"}:
            raise ValueError(f"Pricing requires concrete buy_yes/buy_no, got {self.direction}")
        if not 0.0 <= float(self.value) <= 1.0:
            raise ValueError(f"Native-side price must be in [0, 1], got {self.value}")

    def __float__(self) -> float:
        return float(self.value)

    def __rsub__(self, other: object) -> float:
        raise TypeError(
            "Cross-space conversion from bare float is forbidden for NativeSidePrice. "
            "Construct a new semantic value explicitly."
        )


@dataclass(frozen=True)
class DecisionSnapshotRef:
    """Decision-time snapshot identity carried across modules."""

    snapshot_id: str
    available_at: str = ""


@dataclass(frozen=True)
class StrategyAttribution:
    """Strategy + edge source that downstream modules must preserve verbatim."""

    strategy: str = ""
    edge_source: str = ""


ProbabilityRegistry = Mapping[str, Callable[..., float | tuple[float, list[str]]]]


def _unwrap_native_value(item: Any, label: str, expected_direction: str | None = None) -> tuple[float, str | None]:
    """Duck-typed unwrap so tests can inject probes without bypassing the helper."""

    value = getattr(item, "value", item)
    direction = getattr(item, "direction", expected_direction)
    if direction not in (None, Direction.YES, Direction.NO, "buy_yes", "buy_no"):
        raise ValueError(f"{label} direction must be pure buy_yes/buy_no, got {direction}")
    if expected_direction is not None and direction is not None and direction != expected_direction:
        raise ValueError(f"{label} direction mismatch: expected {expected_direction}, got {direction}")
    return float(value), direction


def _dedupe_steps(steps: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for step in steps:
        if step and step not in seen:
            seen.add(step)
            ordered.append(step)
    return ordered


def compute_native_limit_price(
    held_prob: Any,
    native_price: Any,
    limit_offset: float,
) -> float:
    """Compute a limit price without leaving held-side/native space."""

    prob_value, direction = _unwrap_native_value(held_prob, "held_prob")
    price_value, _ = _unwrap_native_value(native_price, "native_price", direction)
    limit_price = min(prob_value, price_value) - limit_offset
    # T5.b 2026-04-23: replace bare 0.01/0.99 magic with typed contract
    # reference. POLYMARKET_WEATHER_TICK = TickSize(0.01,
    # "probability_units"); clamp_to_valid_range() is lenient on NaN
    # (propagates), matching pre-T5.b behavior — the T5.a ExecutionPrice
    # boundary at _live_order rejects malformed limit_price before CLOB
    # contact, so NaN does not leak to the venue.
    from src.contracts.tick_size import POLYMARKET_WEATHER_TICK
    return POLYMARKET_WEATHER_TICK.clamp_to_valid_range(limit_price)


def compute_forward_edge(
    held_prob: Any,
    native_price: Any,
) -> float:
    """Compute forward edge when both values are already in held-side/native space."""

    prob_value, direction = _unwrap_native_value(held_prob, "held_prob")
    price_value, _ = _unwrap_native_value(native_price, "native_price", direction)
    return prob_value - price_value


def recompute_native_probability(
    position: Any,
    current_p_market: float,
    registry: ProbabilityRegistry,
    **context: Any,
) -> float:
    """Dispatch refresh by Position.entry_method and persist observable evidence."""

    method = EntryMethod.from_value(getattr(position, "entry_method", None)).value
    fn = registry[method]
    setattr(position, "selected_method", method)

    result = fn(position=position, current_p_market=current_p_market, **context)
    if isinstance(result, tuple):
        probability, applied_validations = result
    else:
        probability, applied_validations = result, []

    existing = list(getattr(position, "applied_validations", []))
    setattr(position, "applied_validations", _dedupe_steps([*existing, *applied_validations]))
    return float(probability)
