"""Day0 Signal: observation + residual ENS for settlement-day markets. Spec §2.4.

Key insight: the observed daily high so far sets a HARD FLOOR.
The final settlement value = max(observed_high_so_far, max_remaining_ENS).
This constraint dramatically narrows probability distribution near settlement.
"""

import numpy as np

from src.signal.ensemble_signal import sigma_instrument
from src.types import Bin


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
        diurnal_peak_confidence: float = 0.0,
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
        self._peak_confidence = diurnal_peak_confidence

        # Post-peak: observation floor is more reliable, reduce MC noise.
        # Continuous reduction: sigma scales from base (peak=0) down to 0.5×base (peak=1).
        # Previous binary threshold (>0.7 → halve) caused abrupt signal jump.
        base_sigma = sigma_instrument(unit).value
        self._sigma = base_sigma * (1.0 - diurnal_peak_confidence * 0.5)

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

    def p_vector(self, bins: list[Bin], n_mc: int = 3000) -> np.ndarray:
        """Compute probability vector incorporating observation floor and diurnal data.

        For each MC iteration:
        1. Sample ENS remaining-hours member (with replacement)
        2. Add instrument noise
        3. Final high = max(obs_high, remaining_member)
        4. Round to integer (WU settlement)
        5. Assign to bin

        Returns: np.ndarray shape (n_bins,), sums to 1.0
        """
        n_bins = len(bins)
        n_members = len(self.ens_remaining)
        p = np.zeros(n_bins)

        rng = np.random.default_rng()
        obs_settled = self._settle(self.obs_high)

        for _ in range(n_mc):
            # Sample residual ENS member
            remaining = rng.choice(self.ens_remaining, size=n_members, replace=True)
            noised = remaining + rng.normal(0, self._sigma, n_members)

            # Final high = max(observed, remaining forecast)
            final_highs = np.maximum(noised, self.obs_high)
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

        Increases monotonically as hours_remaining decreases and
        diurnal_peak_confidence increases.

        - At 0h remaining: weight = 1.0 (observation is final fact)
        - At 12h+ remaining, low confidence: weight ≈ 0 (ENS dominant)
        - Diurnal peak already passed: peak_confidence pushes weight up by ≤ 0.5

        Does NOT attempt to model evening-colder-than-morning reversal edge cases
        (those require diurnal_peak_confidence < 0 or explicit flag — caller's job).
        """
        time_weight = max(0.0, 1.0 - self.hours_remaining / 12.0)
        peak_weight = self._peak_confidence * 0.5
        return min(1.0, time_weight + peak_weight)

    def obs_dominates(self) -> bool:
        """True if observation already exceeds most ENS remaining forecasts.

        Legacy boolean interface. Prefer observation_weight() for continuous blending.
        """
        return float(np.mean(self.ens_remaining < self.obs_high)) > 0.8
