"""Shared types used across Zeus modules."""

from dataclasses import dataclass


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
