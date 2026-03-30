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
    ):
        """
        Args:
            observed_high_so_far: highest temp recorded today (settlement unit)
            current_temp: current temperature reading
            hours_remaining: hours until settlement closes
            member_maxes_remaining: ENS member daily max for remaining hours,
                                   shape (n_members,)
        """
        self.obs_high = observed_high_so_far
        self.current_temp = current_temp
        self.hours_remaining = hours_remaining
        self.ens_remaining = member_maxes_remaining
        self.unit = unit
        self._sigma = sigma_instrument(unit).value  # P0-9: city-appropriate noise

    def p_vector(self, bins: list[Bin], n_mc: int = 3000) -> np.ndarray:
        """Compute probability vector incorporating observation floor.

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
        obs_int = round(self.obs_high)

        for _ in range(n_mc):
            # Sample residual ENS member
            remaining = rng.choice(self.ens_remaining, size=n_members, replace=True)
            noised = remaining + rng.normal(0, self._sigma, n_members)

            # Final high = max(observed, remaining forecast)
            final_highs = np.maximum(noised, self.obs_high)
            final_ints = np.round(final_highs).astype(int)

            for i, b in enumerate(bins):
                if b.is_open_low:
                    p[i] += np.sum(final_ints <= b.high)
                elif b.is_open_high:
                    p[i] += np.sum(final_ints >= b.low)
                else:
                    p[i] += np.sum(
                        (final_ints >= b.low) & (final_ints <= b.high)
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

    def obs_dominates(self) -> bool:
        """True if observation already exceeds most ENS remaining forecasts.

        When this is True, the settlement is largely determined — the
        remaining hours are unlikely to produce a higher temperature.
        """
        return float(np.mean(self.ens_remaining < self.obs_high)) > 0.8
