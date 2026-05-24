"""Live-bin state for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveBinState:
    probabilities: tuple[float, ...]

    def normalized(self) -> "LiveBinState":
        total = sum(self.probabilities)
        if total <= 0.0:
            raise ValueError("zero probability mass after live inference")
        return LiveBinState(tuple(value / total for value in self.probabilities))
