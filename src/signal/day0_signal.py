"""Day0 Signal: observation + residual ENS for settlement-day markets. Spec §2.4.

Key insight: the observed daily high so far sets a HARD FLOOR.
The final settlement value = max(observed_high_so_far, max_remaining_ENS).
This constraint dramatically narrows probability distribution near settlement.
"""

import numpy as np

from src.config import day0_n_mc, day0_obs_dominates_threshold
from src.signal.forecast_uncertainty import day0_post_peak_sigma
from src.types import Bin, SolarDay, DaylightPhase, Day0TemporalContext


class Day0Signal:
    """Combine today's observation with ENS remaining-hours forecast.

    Spec §2.4: final_high = max(obs_high, remaining_ens_max).
    Observation is known (integer °F/°C from WU) — no instrument noise needed.
    ENS provides residual forecast for hours not yet observed.
    """

    def __init__(
        self,
        observed_high_so_far: float,
        current_temp: float,
        hours_remaining: float,
        member_maxes_remaining: np.ndarray,
        unit: str = "F",
        temporal_context: Day0TemporalContext | None = None,
        diurnal_peak_confidence: float = 0.0,
        solar_day: SolarDay | None = None,
        current_local_hour: float | None = None,
        daylight_progress: float | None = None,
        precision: float = 1.0,  # Settlement precision: 1.0=integer, 0.1=one decimal
    ):
        """
        Args:
            observed_high_so_far: highest temp recorded today (settlement unit)
            current_temp: current temperature reading
            hours_remaining: hours until settlement closes
            member_maxes_remaining: ENS member daily max for remaining hours,
                                   shape (n_members,)
            diurnal_peak_confidence: 0.0-1.0, how confident we are that the
                daily peak has already passed (from diurnal_curves data)
        """
        self.obs_high = observed_high_so_far
        self.current_temp = current_temp
        self.hours_remaining = hours_remaining
        self.ens_remaining = member_maxes_remaining
        self.unit = unit
        self._precision = precision
        if temporal_context is not None:
            diurnal_peak_confidence = temporal_context.post_peak_confidence
            solar_day = temporal_context.solar_day
            current_local_hour = temporal_context.current_local_hour
            daylight_progress = temporal_context.daylight_progress
        self._peak_confidence = diurnal_peak_confidence
        self._solar_day = solar_day
        self._current_local_hour = current_local_hour
        if solar_day is not None and current_local_hour is not None:
            self._daylight_progress = solar_day.daylight_progress(current_local_hour)
        else:
            self._daylight_progress = daylight_progress

        # Sigma shrinkage remains tied specifically to post-peak confidence.
        # Broader temporal closure now flows through observation_weight() and
        # remaining-window selection, not through a second implicit sigma policy.
        self._sigma = day0_post_peak_sigma(unit, self._peak_confidence)

    def _settle(self, values) -> np.ndarray:
        """Apply settlement rounding using this market's precision.

        Mirrors EnsembleSignal._simulate_settlement() logic.
        precision=1.0 → integer rounding; precision=0.1 → one decimal place.
        Uses numpy's default round_half_to_even (banker's rounding).
        Result is float, not int — callers use >= / <= comparisons on Bin bounds.
        Accepts both scalar and ndarray inputs.
        """
        arr = np.asarray(values, dtype=float)
        inv = 1.0 / self._precision if self._precision > 0 else 1.0
        return np.round(arr * inv) / inv

    def p_vector(self, bins: list[Bin], n_mc: int | None = None) -> np.ndarray:
        """Compute probability vector incorporating observation floor and diurnal data.

        For each MC iteration:
        1. Sample ENS remaining-hours member (with replacement)
        2. Add instrument noise
        3. Final high = max(obs_high, remaining_member)
        4. Round to integer (WU settlement)
        5. Assign to bin

        Returns: np.ndarray shape (n_bins,), sums to 1.0
        """
        if n_mc is None:
            n_mc = day0_n_mc()

        n_bins = len(bins)
        n_members = len(self.ens_remaining)
        p = np.zeros(n_bins)

        rng = np.random.default_rng()
        obs_settled = self._settle(self.obs_high)
        obs_weight = self.observation_weight()

        for _ in range(n_mc):
            # Sample residual ENS member
            remaining = rng.choice(self.ens_remaining, size=n_members, replace=True)
            noised = remaining + rng.normal(0, self._sigma, n_members)

            # Day0 fusion: the observed high is a hard floor, while residual upside
            # above that floor should shrink continuously as the observation becomes
            # more dominant later in the day.
            residual_excess = np.maximum(0.0, noised - self.obs_high)
            final_highs = self.obs_high + residual_excess * (1.0 - obs_weight)
            final_settled = self._settle(final_highs)

            for i, b in enumerate(bins):
                if b.is_open_low:
                    p[i] += np.sum(final_settled <= b.high)
                elif b.is_open_high:
                    p[i] += np.sum(final_settled >= b.low)
                else:
                    p[i] += np.sum(
                        (final_settled >= b.low) & (final_settled <= b.high)
                    )

        p = p / (float(n_members) * n_mc)
        total = p.sum()
        if total > 0:
            p = p / total
        return p

    def expected_high(self) -> float:
        """Expected final daily high (mean of max(obs, remaining))."""
        final = np.maximum(self.ens_remaining, self.obs_high)
        return float(np.mean(final))

    def observation_weight(self) -> float:
        """Continuous 0→1 weight for how much observation dominates over ENS.

        The fusion is intentionally monotone and multiplicative:
        independent closure signals reduce the remaining forecast freedom rather
        than being added as disconnected heuristics.
        """
        base = self._temporal_closure_weight()

        if self._solar_day is not None and self._current_local_hour is not None:
            phase = self._solar_day.phase(self._current_local_hour)
            if phase == DaylightPhase.PRE_SUNRISE:
                return min(base, 0.05)
            if phase == DaylightPhase.POST_SUNSET:
                return 1.0

        if self._daylight_progress is None:
            return base
        if self._daylight_progress <= 0.0:
            return min(base, 0.05)
        if self._daylight_progress >= 1.0:
            return 1.0
        return max(base, self._daylight_progress * 0.35)

    def _temporal_closure_weight(self) -> float:
        """Monotone fusion of residual-time, diurnal, solar, and ENS-dominance evidence."""
        time_closure = float(np.clip(1.0 - self.hours_remaining / 12.0, 0.0, 1.0))
        peak_signal = float(np.clip(self._peak_confidence, 0.0, 1.0))
        daylight_signal = (
            float(np.clip(self._daylight_progress, 0.0, 1.0))
            if self._daylight_progress is not None
            else time_closure
        )
        ens_dominance = float(np.clip(np.mean(self.ens_remaining <= self.obs_high), 0.0, 1.0))

        residual_freedom = (
            (1.0 - time_closure)
            * (1.0 - 0.75 * peak_signal)
            * (1.0 - 0.50 * daylight_signal)
            * (1.0 - 0.35 * ens_dominance)
        )
        return float(np.clip(1.0 - residual_freedom, 0.0, 1.0))

    def obs_dominates(self) -> bool:
        """True if observation already exceeds most ENS remaining forecasts.

        Legacy boolean interface. Prefer observation_weight() for continuous blending.
        """
        return float(np.mean(self.ens_remaining < self.obs_high)) > day0_obs_dominates_threshold()
