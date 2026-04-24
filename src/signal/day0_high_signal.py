# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Authority basis: phase6_contract.md R-BA, day0_signal_router.py reference skeleton
"""Day0HighSignal — hard-floor semantics for HIGH_LOCALDAY_MAX Day0 markets.

Delegates rich p_vector / forecast_context to Day0Signal internally so the
evaluator callsite gets identical behaviour with the new typed API.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np

if TYPE_CHECKING:
    from src.types import Day0TemporalContext, Bin


class Day0HighSignal:
    """High-temperature Day0 signal.

    final_high = max(observed_high_so_far, remaining_high)

    Simple consumers (tests, new code) use settlement_samples() / p_bin().
    Rich consumers (evaluator, monitor_refresh) use p_vector() / forecast_context(),
    which delegate to Day0Signal for full backward-compatible behaviour.
    """

    def __init__(
        self,
        *,
        observed_high_so_far: float,
        member_maxes_remaining: np.ndarray,
        current_temp: float,
        hours_remaining: float,
        unit: str = "F",
        # Rich fields forwarded from evaluator/monitor callsites
        observation_source: str = "",
        observation_time: str | None = None,
        current_utc_timestamp: str | None = None,
        temporal_context: "Day0TemporalContext | None" = None,
        round_fn: "Callable | None" = None,
        precision: float = 1.0,
    ) -> None:
        if observed_high_so_far is None:
            raise ValueError("observed_high_so_far is required for Day0HighSignal")
        arr = np.asarray(member_maxes_remaining)
        if arr.size == 0:
            raise ValueError("member_maxes_remaining is required and must be non-empty for Day0HighSignal")
        self.obs_floor = float(observed_high_so_far)
        self.ens_remaining = arr.astype(np.float64)
        self.current_temp = float(current_temp)
        self.hours_remaining = float(hours_remaining)
        self.unit = unit
        # Stash rich fields for _day0_signal() lazy construction
        self._observation_source = observation_source
        self._observation_time = observation_time
        self._current_utc_timestamp = current_utc_timestamp
        self._temporal_context = temporal_context
        self._round_fn = round_fn
        self._precision = precision
        self.__day0_signal = None

    def _day0_signal(self):
        """Lazy-construct the underlying Day0Signal for rich p_vector delegation."""
        if self.__day0_signal is None:
            from src.signal.day0_signal import Day0Signal
            from src.types.metric_identity import HIGH_LOCALDAY_MAX
            self.__day0_signal = Day0Signal(
                observed_high_so_far=self.obs_floor,
                current_temp=self.current_temp,
                hours_remaining=self.hours_remaining,
                member_maxes_remaining=self.ens_remaining,
                unit=self.unit,
                observation_source=self._observation_source,
                observation_time=self._observation_time,
                current_utc_timestamp=self._current_utc_timestamp,
                temporal_context=self._temporal_context,
                round_fn=self._round_fn,
                precision=self._precision,
                temperature_metric=HIGH_LOCALDAY_MAX,
            )
        return self.__day0_signal

    def settlement_samples(self) -> np.ndarray:
        return np.maximum(self.obs_floor, self.ens_remaining)

    def p_bin(self, low: float, high: float) -> float:
        samples = self.settlement_samples()
        return float(np.mean((samples >= low) & (samples <= high)))

    def p_vector(self, bins: "list[Bin]", n_mc: int | None = None, rng=None) -> np.ndarray:
        return self._day0_signal().p_vector(bins, n_mc=n_mc, rng=rng)

    def forecast_context(self) -> dict:
        return self._day0_signal().forecast_context()
