# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Authority basis: phase6_contract.md R-BB, R-BC, R-BD, day0_signal_router.py reference skeleton
"""Day0LowNowcastSignal — ceiling semantics for LOW_LOCALDAY_MIN Day0 markets.

Intentionally does NOT import day0_high_signal (R-BE invariant).
"""
from __future__ import annotations

import numpy as np


class Day0LowNowcastSignal:
    """Low-temperature Day0 signal.

    final_low = min(observed_low_so_far, blended_remaining)
    observed_low_so_far forms a ceiling: the day's minimum cannot be above it.
    """

    def __init__(
        self,
        *,
        observed_low_so_far: float,
        member_mins_remaining: np.ndarray,
        current_temp: float,
        hours_remaining: float,
        unit: str = "F",
    ) -> None:
        if observed_low_so_far is None:
            raise ValueError("observed_low_so_far is required for Day0LowNowcastSignal")
        if member_mins_remaining is None or np.asarray(member_mins_remaining).size == 0:
            raise ValueError("member_mins_remaining is required and must be non-empty for Day0LowNowcastSignal")
        self.obs_ceiling = float(observed_low_so_far)
        self.ens_remaining = np.asarray(member_mins_remaining, dtype=np.float64)
        self.current_temp = float(current_temp)
        self.hours_remaining = float(hours_remaining)
        self.unit = unit

    def _remaining_weight(self) -> float:
        return max(0.10, min(0.95, self.hours_remaining / 24.0))

    def settlement_samples(self) -> np.ndarray:
        anchored = np.minimum(self.ens_remaining, self.current_temp)
        w = self._remaining_weight()
        blended = w * anchored + (1.0 - w) * self.current_temp
        return np.minimum(self.obs_ceiling, blended)

    def p_bin(self, low: float, high: float) -> float:
        samples = self.settlement_samples()
        return float(np.mean((samples >= low) & (samples <= high)))
