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
    ENTERED = "entered"
    HOLDING = "holding"
    DAY0_WINDOW = "day0_window"
    SETTLED = "settled"
    VOIDED = "voided"
    ADMIN_CLOSED = "admin_closed"

class ChainState(str, Enum):
    UNKNOWN = "unknown"
    SYNCED = "synced"
    LOCAL_ONLY = "local_only"
    CHAIN_ONLY = "chain_only"
    QUARANTINED = "quarantined"

class RejectionStage(str, Enum):
    SIGNAL_QUALITY = "SIGNAL_QUALITY"
    MARKET_FILTER = "MARKET_FILTER"
    ANTI_CHURN = "ANTI_CHURN"
    SIZING_TOO_SMALL = "SIZING_TOO_SMALL"
    RISK_REJECTED = "RISK_REJECTED"
    EDGE_INSUFFICIENT = "EDGE_INSUFFICIENT"
    FDR_FILTERED = "FDR_FILTERED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    
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
    return max(0.01, min(0.99, limit_price))


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
