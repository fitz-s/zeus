"""Market types: Bin and BinEdge."""

from dataclasses import dataclass, field
import math

import numpy as np


_INF_LOW_SENTINEL = -32768
_INF_HIGH_SENTINEL = 32767


class BinTopologyError(ValueError):
    """Raised when a set of bins is not a complete integer partition."""


def _is_neg_inf(value: float | None) -> bool:
    return isinstance(value, float) and math.isinf(value) and value < 0


def _is_pos_inf(value: float | None) -> bool:
    return isinstance(value, float) and math.isinf(value) and value > 0


def _norm_low(value: float | None) -> float:
    return float("-inf") if value is None else float(value)


def _norm_high(value: float | None) -> float:
    return float("inf") if value is None else float(value)


@dataclass(frozen=True)
class Bin:
    """A single outcome bin in a Polymarket weather market.

    The instrument Zeus trades. Must carry its unit and settlement contract
    so downstream math (p_raw, Platt, edge, exit) can operate correctly.

    For open-ended bins: low=None means "X or below", high=None means "X or higher".
    For point bins (°C): low == high (e.g., "4°C" → low=4, high=4).
    For range bins (°F): low < high (e.g., "60-65°F" → low=60, high=65).

    Width semantics:
    - Range bin 60-65°F: 6 integer settlement values (60,61,62,63,64,65)
    - Point bin 4°C: 1 integer settlement value (4)
    - Shoulder bins: width is unbounded (None)
    """
    low: float | None
    high: float | None
    unit: str  # "F" or "C" — carried, never inferred
    label: str = ""

    def __post_init__(self) -> None:
        if self.unit not in {"F", "C"}:
            raise ValueError(f"Bin.unit must be 'F' or 'C', got {self.unit!r}")

        label = self.label or ""
        if "°F" in label and self.unit != "F":
            raise ValueError(f"Bin label {label!r} is Fahrenheit but unit={self.unit!r}")
        if "°C" in label and self.unit != "C":
            raise ValueError(f"Bin label {label!r} is Celsius but unit={self.unit!r}")

        if self.low is None and self.high is None:
            raise ValueError("Bin cannot have both low and high unset")

        for name, value in (("low", self.low), ("high", self.high)):
            if value is not None and math.isnan(float(value)):
                raise ValueError(f"Bin.{name} cannot be NaN")
        if _is_pos_inf(self.low):
            raise ValueError("Bin.low cannot be +inf")
        if _is_neg_inf(self.high):
            raise ValueError("Bin.high cannot be -inf")
        if not self.is_shoulder and self.low is not None and self.high is not None:
            if float(self.low) > float(self.high):
                raise ValueError(
                    f"Bin.low={self.low!r} must be <= high={self.high!r}"
                )

        if self.is_shoulder:
            return

        width = self.width
        if self.unit == "F" and width != 2:
            raise ValueError(
                f"Fahrenheit non-shoulder bins must cover exactly 2 settled degrees; "
                f"{label!r} has width={width}"
            )
        if self.unit == "C" and width != 1:
            raise ValueError(
                f"Celsius non-shoulder bins must cover exactly 1 settled degree; "
                f"{label!r} has width={width}"
            )

    @property
    def is_open_low(self) -> bool:
        return self.low is None or _is_neg_inf(self.low)

    @property
    def is_open_high(self) -> bool:
        return self.high is None or _is_pos_inf(self.high)

    @property
    def is_shoulder(self) -> bool:
        return self.is_open_low or self.is_open_high

    def contains(self, value: float) -> bool:
        """Return whether this integer-settlement bin contains value."""
        v = float(value)
        return _norm_low(self.low) <= v <= _norm_high(self.high)

    @property
    def is_point(self) -> bool:
        """°C point bin: low == high, covers exactly one integer degree."""
        return self.low is not None and self.high is not None and self.low == self.high

    @property
    def width(self) -> float | None:
        """Number of integer settlement values this bin covers.

        Polymarket temperature bin structure (from actual market data):
        - °F range bin "50-51°F": covers {50, 51} → width = 2
        - °C point bin "10°C": covers {10} → width = 1
        - Shoulder bin "X°F or below": unbounded → width = None
        """
        if self.is_shoulder:
            return None
        if self.low is not None and self.high is not None:
            if self.is_point:
                return 1
            # Range bin: inclusive on both ends
            # "50-51°F" → high(51) - low(50) + 1 = 2 integer values
            return self.high - self.low + 1
        return None

    @property
    def settlement_values(self) -> list[int] | None:
        """The exact integer settlement values that resolve this bin to YES.

        "50-51°F" → [50, 51]
        "10°C" → [10]
        Shoulder → None (unbounded)
        """
        if self.is_shoulder:
            return None
        if self.low is not None and self.high is not None:
            return list(range(int(self.low), int(self.high) + 1))
        return None


def to_json_safe(b: Bin) -> dict:
    """Encode a Bin without JSON Infinity/NaN values.

    Both legacy None shoulders and staged +/-inf shoulders are encoded to the
    same integer sentinels so JSON boundaries move toward the explicit form.
    """
    if b.is_open_low:
        low: int | float = _INF_LOW_SENTINEL
    else:
        low = float(b.low)  # type: ignore[arg-type]

    if b.is_open_high:
        high: int | float = _INF_HIGH_SENTINEL
    else:
        high = float(b.high)  # type: ignore[arg-type]

    return {
        "unit": b.unit,
        "low": low,
        "high": high,
        "label": b.label,
        "is_open_low": b.is_open_low,
        "is_open_high": b.is_open_high,
    }


def from_json_safe(payload: dict) -> Bin:
    """Decode a Bin encoded by to_json_safe."""
    low_raw = payload["low"]
    high_raw = payload["high"]
    low = float("-inf") if low_raw == _INF_LOW_SENTINEL else float(low_raw)
    high = float("inf") if high_raw == _INF_HIGH_SENTINEL else float(high_raw)
    return Bin(
        low=low,
        high=high,
        unit=payload["unit"],
        label=payload.get("label", ""),
    )


def validate_bin_topology(bins: list[Bin]) -> None:
    """Validate that bins form a complete, non-overlapping integer partition."""
    if not bins:
        raise BinTopologyError("empty bin set")
    if len(bins) == 1 and bins[0].is_open_low and bins[0].is_open_high:
        raise BinTopologyError("single universal open-open bin is not a market topology")
    units = {b.unit for b in bins}
    if len(units) != 1:
        raise BinTopologyError(f"mixed bin units are invalid: {sorted(units)!r}")

    ordered = sorted(bins, key=lambda b: (_norm_low(b.low), _norm_high(b.high)))
    if _norm_low(ordered[0].low) != float("-inf"):
        raise BinTopologyError(
            f"leftmost bin low={ordered[0].low!r}; expected -inf"
        )
    if _norm_high(ordered[-1].high) != float("inf"):
        raise BinTopologyError(
            f"rightmost bin high={ordered[-1].high!r}; expected +inf"
        )

    for prev, nxt in zip(ordered, ordered[1:]):
        prev_high = _norm_high(prev.high)
        next_low = _norm_low(nxt.low)
        if math.isinf(prev_high) or math.isinf(next_low):
            raise BinTopologyError(
                f"invalid interior infinite edge between {prev!r} and {nxt!r}"
            )
        if next_low <= prev_high:
            raise BinTopologyError(f"overlapping bins: {prev!r} and {nxt!r}")
        if not math.isclose(prev_high + 1.0, next_low, abs_tol=1e-9):
            raise BinTopologyError(f"gap between bins: {prev!r} and {nxt!r}")


def bin_count_from_values(values, b: Bin) -> int:
    """Count measured settlement values that fall inside a bin."""
    try:
        return bin_count_from_array(np.asarray(values, dtype=float), b)
    except Exception:
        return sum(1 for value in values if b.contains(float(value)))


def bin_count_from_array(values: np.ndarray, b: Bin) -> int:
    """Count values already materialized as a NumPy array."""
    return int(np.count_nonzero((values >= _norm_low(b.low)) & (values <= _norm_high(b.high))))


def bin_counts_from_array(values: np.ndarray, bins: list[Bin]) -> np.ndarray:
    """Count values across many bins in one vectorized pass."""
    lows = np.array([_norm_low(b.low) for b in bins], dtype=float)
    highs = np.array([_norm_high(b.high) for b in bins], dtype=float)
    arr = np.asarray(values, dtype=float)[:, None]
    return np.count_nonzero((arr >= lows) & (arr <= highs), axis=0)


def bin_probability_from_values(values, b: Bin) -> float:
    """Probability mass of measured settlement values inside a bin."""
    n = len(values)
    if n == 0:
        return 0.0
    return float(bin_count_from_values(values, b)) / float(n)


@dataclass
class BinEdge:
    """A detected trading edge on a specific bin. Spec §4.1.

    Not frozen — ev_per_dollar is set by rank_edges() after construction.
    """
    bin: Bin
    direction: str  # "buy_yes" or "buy_no"
    edge: float
    ci_lower: float
    ci_upper: float
    p_model: float
    p_market: float
    p_posterior: float
    entry_price: float
    p_value: float
    vwmp: float
    forward_edge: float = 0.0
    ev_per_dollar: float = 0.0
