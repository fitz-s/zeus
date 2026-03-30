"""EnsembleSignal: 51 ENS members → probability vector over market bins.

Core signal generation for Zeus. Takes raw ensemble hourly data and produces
P_raw — the uncalibrated probability vector that feeds into Platt calibration.

Spec §2.1: The critical insight most bots miss is WU integer rounding.
Settlement = round(member_max + instrument_noise) → integer.
Simple member counting ignores measurement uncertainty at bin boundaries.
"""

from datetime import date
from zoneinfo import ZoneInfo

import numpy as np
from scipy.signal import argrelextrema
from scipy.stats import gaussian_kde

from src.config import City
from src.types import Bin
from src.types.temperature import TemperatureDelta, Unit


def sigma_instrument(unit: Unit) -> TemperatureDelta:
    """ASOS sensor precision. °C value independently calibrated, not °F/1.8."""
    if unit == "C":
        return TemperatureDelta(0.28, "C")
    return TemperatureDelta(0.5, "F")


# Legacy constant for tests that don't pass city. Will be removed after full migration.
SIGMA_INSTRUMENT = 0.5

# Default MC iterations for p_raw_vector
DEFAULT_N_MC = 5000

# KDE bimodality detection: argrelextrema order parameter
BIMODAL_KDE_ORDER = 10

# Fallback bimodality: gap/range threshold
BIMODAL_GAP_RATIO = 0.3

# Boundary sensitivity window: members within ±0.5° of boundary
BOUNDARY_WINDOW = 0.5


class EnsembleSignal:
    """51 ensemble members → probability vector over all bins.

    Spec §2.1: Monte Carlo simulation of the full settlement chain:
    atmosphere → NWP member → sensor noise → METAR rounding → WU integer display
    """

    def __init__(
        self,
        members_hourly: np.ndarray,
        city: City,
        target_date: date,
    ):
        """
        Args:
            members_hourly: shape (n_members, hours), city's settlement unit
            city: City config with timezone
            target_date: the settlement date
        """
        if members_hourly.shape[0] < 51:
            raise ValueError(
                f"Expected ≥51 ensemble members, got {members_hourly.shape[0]}. "
                f"Per CLAUDE.md: reject entirely, do not pad."
            )

        tz = ZoneInfo(city.timezone)
        tz_hours = self._select_hours_for_date(
            target_date, tz, members_hourly.shape[1]
        )

        if len(tz_hours) == 0:
            raise ValueError(
                f"No hours found for {target_date} in {city.timezone}. "
                f"Check forecast_days parameter."
            )

        # Daily max per member, respecting city timezone for day boundary
        self.member_maxes: np.ndarray = members_hourly[:, tz_hours].max(axis=1)
        # WU settlement simulation: round to integer
        self.member_maxes_int: np.ndarray = np.round(self.member_maxes).astype(int)
        self.city = city
        self.target_date = target_date

    @staticmethod
    def _select_hours_for_date(
        target_date: date, tz: ZoneInfo, n_hours: int
    ) -> np.ndarray:
        """Select hourly indices belonging to target_date in the city's timezone.

        P0-1 FIX: Open-Meteo returns hourly data from midnight UTC on the
        first forecast day. For T+3 targets, hours 0-23 are day 0 — WRONG.
        We must select the 24-hour window that corresponds to target_date
        in the city's local timezone.
        """
        from datetime import datetime, timedelta

        # Get UTC offset for the target date (midday avoids DST edge)
        midday = datetime(target_date.year, target_date.month, target_date.day,
                          12, 0, 0, tzinfo=tz)
        offset_hours = midday.utcoffset().total_seconds() / 3600.0

        # Open-Meteo starts at midnight UTC of the issue date (today or close to it)
        # The target_date in local time spans from (midnight_local - offset) in UTC hours
        # We approximate: local midnight = UTC hour (-offset)
        # Local 23:59 = UTC hour (23 - offset)

        # Estimate which forecast hour index corresponds to target_date midnight local
        # If issue date = today, target_date = today + lead_days
        today = date.today()
        lead_days = (target_date - today).days
        if lead_days < 0:
            lead_days = 0

        # Start hour in the forecast array for target_date local midnight
        start_h = int(lead_days * 24 - offset_hours)
        end_h = start_h + 24

        # Clamp to valid range
        start_h = max(0, start_h)
        end_h = min(n_hours, end_h)

        if end_h <= start_h:
            # Fallback: if calculation fails, use last 24 hours available
            return np.arange(max(0, n_hours - 24), n_hours)

        return np.arange(start_h, end_h)

    def p_raw_vector(
        self, bins: list[Bin], n_mc: int = DEFAULT_N_MC
    ) -> np.ndarray:
        """Probability vector over all bins with instrument noise.

        Spec §2.1: Monte Carlo with ε ~ N(0, σ_instrument²) per member.
        Simulates full settlement chain including WU integer rounding.

        Returns: np.ndarray shape (n_bins,), sums to 1.0
        """
        n_bins = len(bins)
        n_members = len(self.member_maxes)
        p = np.zeros(n_bins)

        rng = np.random.default_rng(seed=None)  # Tests should seed externally
        # Use city-appropriate instrument noise (°C independently calibrated)
        sig = sigma_instrument(self.city.settlement_unit)

        for _ in range(n_mc):
            # Add instrument noise to each member's daily max
            noised = self.member_maxes + rng.normal(0, sig.value, n_members)
            measured_int = np.round(noised).astype(int)

            for i, b in enumerate(bins):
                if b.is_open_low:
                    # Shoulder low: "X or below" — compare int measurement to float boundary
                    p[i] += np.sum(measured_int <= b.high)
                elif b.is_open_high:
                    # Shoulder high: "X or higher"
                    p[i] += np.sum(measured_int >= b.low)
                else:
                    p[i] += np.sum(
                        (measured_int >= b.low) & (measured_int <= b.high)
                    )

        p = p / (float(n_members) * n_mc)

        # Normalize to sum=1.0. If total=0 (impossible but defensive), return as-is.
        total = p.sum()
        if total > 0:
            p = p / total
        return p

    def spread(self) -> TemperatureDelta:
        """Ensemble spread (σ of member daily maxes) as typed TemperatureDelta."""
        return TemperatureDelta(float(np.std(self.member_maxes)), self.city.settlement_unit)

    def spread_float(self) -> float:
        """Spread as bare float (legacy compatibility, used by DB storage)."""
        return float(np.std(self.member_maxes))

    def is_bimodal(self) -> bool:
        """Detect regime split (e.g., cold front timing uncertainty).

        Spec §2.1: Uses KDE peak counting with argrelextrema.
        Fallback: gap heuristic if KDE fails (e.g., all members identical).
        """
        maxes = self.member_maxes
        rng = float(maxes.max() - maxes.min())

        if rng < 0.5:
            return False  # All members agree — definitely unimodal

        try:
            kde = gaussian_kde(maxes)
            x = np.linspace(maxes.min() - 1, maxes.max() + 1, 200)
            density = kde(x)
            peaks = argrelextrema(density, np.greater, order=BIMODAL_KDE_ORDER)[0]
            return len(peaks) >= 2
        except Exception:
            # Fallback: gap heuristic
            sorted_maxes = np.sort(maxes)
            gaps = np.diff(sorted_maxes)
            return rng > 0 and float(gaps.max()) / rng > BIMODAL_GAP_RATIO

    def boundary_sensitivity(self, boundary: float) -> float:
        """Fraction of 51 members within ±0.5° of a bin boundary.

        High sensitivity → probability estimate is fragile at this boundary.
        Used to flag trades that depend on borderline bin assignment.
        """
        return float(
            np.sum(np.abs(self.member_maxes - boundary) < BOUNDARY_WINDOW)
        ) / len(self.member_maxes)
