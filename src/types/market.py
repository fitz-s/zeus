"""Market types: Bin and BinEdge."""

from dataclasses import dataclass, field


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
    label: str = ""
    unit: str = "F"  # "F" or "C" — carried, never inferred

    @property
    def is_open_low(self) -> bool:
        return self.low is None

    @property
    def is_open_high(self) -> bool:
        return self.high is None

    @property
    def is_shoulder(self) -> bool:
        return self.is_open_low or self.is_open_high

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
    ev_per_dollar: float = 0.0
