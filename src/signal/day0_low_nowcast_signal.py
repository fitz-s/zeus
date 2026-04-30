# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Authority basis: phase6_contract.md R-BB, R-BC, R-BD, day0_signal_router.py reference skeleton
"""Day0LowNowcastSignal — ceiling semantics for LOW_LOCALDAY_MIN Day0 markets.

Intentionally does NOT import day0_high_signal (R-BE invariant).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np

from src.contracts.settlement_semantics import apply_settlement_rounding
from src.signal.forecast_uncertainty import day0_nowcast_context

if TYPE_CHECKING:
    from src.types import Bin, Day0TemporalContext


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
        observation_source: str = "",
        observation_time: str | None = None,
        current_utc_timestamp: str | None = None,
        temporal_context: "Day0TemporalContext | None" = None,
        round_fn: "Callable | None" = None,
        precision: float = 1.0,
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
        self._observation_source = observation_source
        self._observation_time = observation_time
        self._current_utc_timestamp = current_utc_timestamp
        self._temporal_context = temporal_context
        self._round_fn = round_fn
        self._precision = precision
        if temporal_context is not None:
            self._current_utc_timestamp = temporal_context.current_utc_timestamp.isoformat()

    def _remaining_weight(self) -> float:
        base = max(0.10, min(0.95, self.hours_remaining / 24.0))
        # A fresh trusted Day0 observation should reduce residual forecast freedom;
        # stale or untrusted sources keep the legacy remaining-hours weighting.
        return base * (1.0 - self._nowcast_context()["blend_weight"])

    def _nowcast_context(self) -> dict:
        return day0_nowcast_context(
            hours_remaining=self.hours_remaining,
            observation_source=self._observation_source,
            observation_time=self._observation_time,
            current_utc_timestamp=self._current_utc_timestamp,
        )

    def _settle(self, values) -> np.ndarray:
        return apply_settlement_rounding(values, self._round_fn, self._precision)

    def settlement_samples(self) -> np.ndarray:
        anchored = np.minimum(self.ens_remaining, self.current_temp)
        w = self._remaining_weight()
        blended = w * anchored + (1.0 - w) * self.current_temp
        return self._settle(np.minimum(self.obs_ceiling, blended))

    @staticmethod
    def _bound(value, *, open_default: float) -> float:
        return open_default if value is None else float(value)

    @staticmethod
    def _bin_bounds(bin_like) -> tuple[float | None, float | None]:
        lo = getattr(bin_like, "low", None)
        hi = getattr(bin_like, "high", None)
        if hasattr(bin_like, "__getitem__"):
            if lo is None:
                lo = bin_like["low"]
            if hi is None:
                hi = bin_like["high"]
        return lo, hi

    def p_bin(self, low: float | None, high: float | None) -> float:
        samples = self.settlement_samples()
        lo = self._bound(low, open_default=float("-inf"))
        hi = self._bound(high, open_default=float("inf"))
        return float(np.mean((samples >= lo) & (samples <= hi)))

    def p_vector(self, bins: "list[Bin]", n_mc=None, rng=None) -> np.ndarray:
        """Phase 9C A3: per-bin probability vector for LOW Day0 nowcast.

        Returns np.ndarray of shape (len(bins),) — probability mass in each
        bin under the LOW ceiling-semantics sample distribution. Caller
        interface matches Day0HighSignal.p_vector signature (bins, n_mc, rng)
        so Day0Router dispatch is type-compatible.

        Notes:
          - `n_mc` and `rng` are ACCEPTED for signature symmetry with
            Day0HighSignal but NOT USED — LOW nowcast samples are
            deterministic (anchored blend of ens_remaining + current_temp,
            clipped by obs_ceiling). No Monte Carlo needed; the sample set
            IS the distribution.
          - DOES NOT delegate to day0_high_signal (R-BE invariant — no
            HIGH→LOW cross-import). Pre-P9C handoff flagged "lazy-
            construction delegating to HIGH" concern; the current impl is
            the proper LOW-specific path derived from settlement_samples().

        Caller (evaluator/monitor_refresh Day0 paths) passes a Bin-like
        sequence with `.low` and `.high` numeric attributes (or fallback
        keys). See src/contracts/calibration_bins.py for Bin shape.
        """
        samples = self.settlement_samples()
        probs = []
        for b in bins:
            lo, hi = self._bin_bounds(b)
            if getattr(b, "is_open_low", False):
                if hi is None:
                    raise ValueError("open-low LOW Day0 bin must carry a finite high bound")
                probs.append(float(np.mean(samples <= float(hi))))
            elif getattr(b, "is_open_high", False):
                if lo is None:
                    raise ValueError("open-high LOW Day0 bin must carry a finite low bound")
                probs.append(float(np.mean(samples >= float(lo))))
            else:
                probs.append(self.p_bin(lo, hi))
        return np.asarray(probs, dtype=np.float64)

    def forecast_context(self) -> dict:
        nowcast = self._nowcast_context()
        return {
            "observation_weight": 1.0 - self._remaining_weight(),
            "temporal_closure_weight": 1.0 - max(0.0, min(1.0, self.hours_remaining / 24.0)),
            "backbone": {
                "observation_source": self._observation_source,
                "observation_time": self._observation_time,
                "current_utc_timestamp": self._current_utc_timestamp,
                "observed_low": self.obs_ceiling,
                "current_temp": self.current_temp,
                "backbone_low": float(np.min(self.settlement_samples())),
                "nowcast": nowcast,
            },
        }
