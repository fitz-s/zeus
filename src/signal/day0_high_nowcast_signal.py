# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5 (Option B pivot)
"""Day0HighNowcastSignal — nowcast lane for HIGH_LOCALDAY_MAX Day0 markets.

Mirrors Day0LowNowcastSignal structure: same constructor shape (hours_remaining,
observation_source, etc.), same output interface (settlement_samples() -> np.ndarray,
p_bin(low, high) -> float, p_vector(bins) -> np.ndarray).

Does NOT subclass Day0HighSignal — deliberate parallel structure per Option B.
Day0Router HIGH branch invokes both Day0HighSignal (ensemble path) and this class
(nowcast path) in parallel. Evaluator owns fusion (element-wise per-bin blend;
p_cal is np.ndarray, not scalar).

Horizon guard: raises NotApplicableHorizon when inputs.hours_remaining > 6.
Fail-closed: live mode must not relabel ensemble output as nowcast.

settlement_samples() implements HIGH floor semantics:
    anchored = max(ens_remaining, current_temp)   # HIGH: floor, not ceiling
    blended  = w * anchored + (1-w) * current_temp
    return max(obs_floor, blended)                # floor applied element-wise

Intentionally does NOT import day0_low_nowcast_signal (parallel-structure invariant).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np

from src.contracts.settlement_semantics import apply_settlement_rounding
from src.signal.forecast_uncertainty import day0_nowcast_context

if TYPE_CHECKING:
    from src.types import Bin, Day0TemporalContext


class NotApplicableHorizon(ValueError):
    """Raised when hours_remaining > 6 — nowcast contract not applicable.

    Callers must catch and fall back to the standard ensemble signal path
    (Day0HighSignal). Never suppress silently.
    """


class Day0HighNowcastSignal:
    """High-temperature Day0 nowcast signal.

    Constructor mirrors Day0LowNowcastSignal for structural symmetry:
    same required fields, same rich optional fields, same output interface.

    Horizon guard fires in __init__ so callers discover inapplicability
    immediately on construction (before any evaluate call).

    Args:
        observed_high_so_far: intraday observed high as of observation_time.
            Forms a floor: the day's high cannot be below it.
        member_maxes_remaining: ensemble member daily-max values for the
            remaining forecast window (same shape/semantics as Day0HighSignal).
        current_temp: current observed temperature (raw float; unit carried
            by `unit` kwarg per day0_router.py L7-21 design note).
        hours_remaining: canonical horizon field from Day0SignalInputs.
            Guard raises NotApplicableHorizon if > 6.
        model: HorizonPlattFit from day0_horizon_calibration. None in SCAFFOLD
            (settlement_samples raises NotImplementedError). Required in production.
    """

    def __init__(
        self,
        *,
        observed_high_so_far: float,
        member_maxes_remaining: np.ndarray,
        current_temp: float,
        hours_remaining: float,
        model=None,
        unit: str = "F",
        observation_source: str = "",
        observation_time: str | None = None,
        current_utc_timestamp: str | None = None,
        temporal_context: "Day0TemporalContext | None" = None,
        round_fn: "Callable | None" = None,
        precision: float = 1.0,
    ) -> None:
        _hr = float(hours_remaining)
        if _hr > 6.0 or _hr < 0.0:
            raise NotApplicableHorizon(
                f"Day0HighNowcastSignal requires 0 <= hours_remaining <= 6; "
                f"got {hours_remaining}. Use Day0HighSignal for longer horizons."
            )
        if observed_high_so_far is None:
            raise ValueError("observed_high_so_far is required for Day0HighNowcastSignal")
        arr = np.asarray(member_maxes_remaining)
        if arr.size == 0:
            raise ValueError(
                "member_maxes_remaining is required and must be non-empty for Day0HighNowcastSignal"
            )
        self.obs_floor = float(observed_high_so_far)
        self.ens_remaining = arr.astype(np.float64)
        self.current_temp = float(current_temp)
        self.hours_remaining = float(hours_remaining)
        self._model = model
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
        """HIGH-equivalent of LOW's _remaining_weight.

        HIGH semantics: high cannot fall below observed_high_so_far (floor, not ceiling).
        Weight structure mirrors LOW: base on hours_remaining proportion, modulated by
        nowcast blend_weight from day0_nowcast_context().
        """
        base = max(0.10, min(0.95, self.hours_remaining / 24.0))
        return base * (1.0 - self._nowcast_context()["blend_weight"])

    def _nowcast_context(self) -> dict:
        """Delegate to preserved day0_nowcast_context() helper from forecast_uncertainty."""
        return day0_nowcast_context(
            hours_remaining=self.hours_remaining,
            observation_source=self._observation_source,
            observation_time=self._observation_time,
            current_utc_timestamp=self._current_utc_timestamp,
        )

    def _settle(self, values: np.ndarray) -> np.ndarray:
        return apply_settlement_rounding(values, self._round_fn, self._precision)

    def settlement_samples(self) -> np.ndarray:
        """HIGH floor semantics — mirrors Day0LowNowcastSignal with max instead of min.

        Day's high cannot be below observed_high_so_far (floor, not ceiling).

        Algorithm:
            anchored = max(ens_remaining[i], current_temp)  # floor: high can only rise
            w        = _remaining_weight()
            blended  = w * anchored + (1-w) * current_temp
            final    = max(obs_floor, blended)              # floor from observation
        """
        anchored = np.maximum(self.ens_remaining, self.current_temp)
        w = self._remaining_weight()
        blended = w * anchored + (1.0 - w) * self.current_temp
        return self._settle(np.maximum(self.obs_floor, blended))

    def p_bin(self, low: float | None, high: float | None) -> float:
        """Per-bin probability under nowcast distribution. Mirrors Day0LowNowcastSignal."""
        samples = self.settlement_samples()
        lo = float("-inf") if low is None else float(low)
        hi = float("inf") if high is None else float(high)
        return float(np.mean((samples >= lo) & (samples <= hi)))

    def p_vector(self, bins: "list[Bin]", n_mc=None, rng=None) -> np.ndarray:
        """Per-bin probability vector. Signature matches Day0HighSignal.p_vector.

        n_mc and rng accepted for signature symmetry but not used (mirrors
        Day0LowNowcastSignal.p_vector signature note).
        """
        samples = self.settlement_samples()
        probs = []
        for b in bins:
            lo = getattr(b, "low", None)
            hi = getattr(b, "high", None)
            if hasattr(b, "__getitem__"):
                if lo is None:
                    lo = b["low"]
                if hi is None:
                    hi = b["high"]
            if getattr(b, "is_open_low", False):
                probs.append(float(np.mean(samples <= float(hi))))
            elif getattr(b, "is_open_high", False):
                probs.append(float(np.mean(samples >= float(lo))))
            else:
                probs.append(self.p_bin(lo, hi))
        return np.asarray(probs, dtype=np.float64)

    def forecast_context(self) -> dict:
        """Diagnostic context dict.

        Key structure matches Day0LowNowcastSignal.forecast_context (lines 143-157):
        {"observation_weight", "temporal_closure_weight", "backbone": {...}}
        Evaluator at evaluator.py:2384 calls day0.forecast_context() and expects this shape.
        """
        nowcast = self._nowcast_context()
        return {
            "observation_weight": 1.0 - self._remaining_weight(),
            "temporal_closure_weight": 1.0 - max(0.0, min(1.0, self.hours_remaining / 24.0)),
            "backbone": {
                "observation_source": self._observation_source,
                "observation_time": self._observation_time,
                "current_utc_timestamp": self._current_utc_timestamp,
                "observed_high": self.obs_floor,
                "current_temp": self.current_temp,
                "backbone_high": float(np.max(self.settlement_samples())),
                "nowcast": nowcast,
            },
        }
