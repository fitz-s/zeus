"""Shared types used across Zeus modules."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bin:
    """A single outcome bin in a Polymarket weather market.

    For open-ended bins: low=None means "X or below", high=None means "X or higher".
    For point bins (°C): low == high (e.g., "4°C" → low=4, high=4).
    For range bins: low < high (e.g., "50-51°F" → low=50, high=51).
    """
    low: float | None
    high: float | None
    label: str = ""

    @property
    def is_open_low(self) -> bool:
        return self.low is None

    @property
    def is_open_high(self) -> bool:
        return self.high is None

    @property
    def is_shoulder(self) -> bool:
        return self.is_open_low or self.is_open_high


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
