"""EnsembleSignal: 51 ENS members → probability vector over market bins.

Core signal generation for Zeus. Takes raw ensemble hourly data and produces
P_raw — the uncalibrated probability vector that feeds into Platt calibration.

Spec §2.1: The critical insight most bots miss is WU integer rounding.
Settlement = round(member_max + instrument_noise) → integer.
Simple member counting ignores measurement uncertainty at bin boundaries.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy.signal import argrelextrema
from scipy.stats import gaussian_kde

from src.contracts.settlement_semantics import SettlementSemantics
from src.config import (
    City,
    ensemble_bimodal_gap_ratio,
    ensemble_bimodal_kde_order,
    ensemble_boundary_window,
    ensemble_instrument_noise,
    ensemble_member_count,
    ensemble_n_mc,
    ensemble_unimodal_range_epsilon,
)
from src.types import Bin
from src.types.temperature import TemperatureDelta, Unit


def sigma_instrument(unit: Unit) -> TemperatureDelta:
    """ASOS sensor precision. °C value independently calibrated, not °F/1.8."""
    return TemperatureDelta(ensemble_instrument_noise(unit), unit)


# Compatibility aliases for tests and assumption audits.
SIGMA_INSTRUMENT = ensemble_instrument_noise("F")
DEFAULT_N_MC = ensemble_n_mc()
BIMODAL_KDE_ORDER = ensemble_bimodal_kde_order()
BIMODAL_GAP_RATIO = ensemble_bimodal_gap_ratio()
BOUNDARY_WINDOW = ensemble_boundary_window()
UNIMODAL_RANGE_EPSILON = ensemble_unimodal_range_epsilon()


def _coerce_timezone(timezone_name: str | ZoneInfo) -> ZoneInfo:
    if isinstance(timezone_name, ZoneInfo):
        return timezone_name
    return ZoneInfo(str(timezone_name))


def select_hours_for_target_date(
    target_date: date,
    timezone_name: str | ZoneInfo,
    *,
    times: list[str],
) -> np.ndarray:
    """Return hourly indices that belong to the local target date."""
    tz = _coerce_timezone(timezone_name)
    idxs = [
        idx
        for idx, ts in enumerate(times)
        if EnsembleSignal._parse_forecast_timestamp(ts).astimezone(tz).date() == target_date
    ]
    if idxs:
        return np.array(idxs, dtype=int)
    raise ValueError(
        f"No forecast hours map to local target date {target_date} in {tz.key}."
    )


def member_maxes_for_target_date(
    members_hourly: np.ndarray,
    times: list[str],
    timezone_name: str | ZoneInfo,
    target_date: date,
) -> np.ndarray:
    """Compute per-member daily maxes using the local target-date slice."""
    tz_hours = select_hours_for_target_date(
        target_date,
        timezone_name,
        times=times,
    )
    return members_hourly[:, tz_hours].max(axis=1)


def p_raw_vector_from_maxes(
    member_maxes: np.ndarray,
    city: City,
    settlement_semantics: SettlementSemantics,
    bins: list[Bin],
    *,
    n_mc: int | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Monte Carlo P_raw vector from per-member daily maxes.

    Spec §2.1: Simulates the full settlement chain:
    atmosphere -> NWP member -> sensor noise -> METAR rounding -> WU integer display.

    Extracted from ``EnsembleSignal.p_raw_vector`` so offline calibration
    rebuilds (which load ``member_maxes`` from ``ensemble_snapshots.members_json``
    without hourly context) and live inference (which slices maxes from hourly
    data) share **exactly the same MC + noise + rounding code path**. Training
    and inference MUST use this function — naive member counting (iterating
    members and testing bin membership directly without Monte Carlo) is
    forbidden because it produces a distribution shape that diverges from the
    live P_raw space, and Platt learned on that shape would not generalize.

    Args:
        member_maxes: per-member daily max temperatures, shape (n_members,),
            already in ``city.settlement_unit``.
        city: city config — used for instrument sigma lookup.
        settlement_semantics: per-market rounding rules.
        bins: bin partition to compute probabilities over. Must be a complete
            partition (cover the real line including shoulders) for the result
            to sum to 1.0; the caller is responsible for grid completeness.
        n_mc: Monte Carlo iterations. Defaults to ``ensemble_n_mc()`` (10,000).
        rng: numpy Generator. Callers should seed externally for reproducible
            tests; defaults to ``np.random.default_rng()``.

    Returns:
        np.ndarray shape (n_bins,). If the grid is complete the vector sums to
        1.0; if not, it is normalized so whatever mass landed sums to 1.0.
    """
    if n_mc is None:
        n_mc = ensemble_n_mc()
    if rng is None:
        rng = np.random.default_rng()

    n_bins = len(bins)
    n_members = len(member_maxes)
    p = np.zeros(n_bins)
    sig = sigma_instrument(city.settlement_unit)

    for _ in range(n_mc):
        noised = member_maxes + rng.normal(0, sig.value, n_members)
        measured = settlement_semantics.round_values(noised)

        for i, b in enumerate(bins):
            if b.is_open_low:
                p[i] += np.sum(measured <= b.high)
            elif b.is_open_high:
                p[i] += np.sum(measured >= b.low)
            else:
                p[i] += np.sum((measured >= b.low) & (measured <= b.high))

    p = p / (float(n_members) * n_mc)

    total = p.sum()
    if total > 0:
        p = p / total
    return p


class EnsembleSignal:
    """51 ensemble members → probability vector over all bins.

    Spec §2.1: Monte Carlo simulation of the full settlement chain:
    atmosphere → NWP member → sensor noise → METAR rounding → WU integer display
    """

    def __init__(
        self,
        members_hourly: np.ndarray,
        times: list[str],
        city: City,
        target_date: date,
        settlement_semantics: SettlementSemantics,
        decision_time: datetime | None = None,
    ):
        """
        Args:
            members_hourly: shape (n_members, hours), city's settlement unit
            times: UTC timestamps corresponding to hourly columns
            city: City config with timezone
            target_date: the settlement date
            settlement_semantics: Exact resolution constraints for this target market
            decision_time: Exact time the orchestrator began the evaluation cycle
        """
        if members_hourly.shape[0] < ensemble_member_count():
            raise ValueError(
                f"Expected ≥{ensemble_member_count()} ensemble members, got {members_hourly.shape[0]}. "
                f"Per CLAUDE.md: reject entirely, do not pad."
            )
        if len(times) != members_hourly.shape[1]:
            raise ValueError(
                f"Forecast times length {len(times)} does not match members_hourly hours "
                f"{members_hourly.shape[1]}."
            )

        # Daily max per member, respecting city timezone for day boundary
        self.member_maxes = member_maxes_for_target_date(
            members_hourly,
            times,
            city.timezone,
            target_date,
        )
        
        # Bias correction: subtract per-city×season systematic ECMWF bias
        # GATED by config flag. Activation requires simultaneous Platt recompute
        # to avoid out-of-domain inference (see cross-module invariant test).
        self.bias_corrected = False
        try:
            from src.config import settings
            if settings._data.get("bias_correction_enabled", False):
                self.member_maxes = self._apply_bias_correction(
                    self.member_maxes, city, target_date
                )
                self.bias_corrected = True
        except Exception:
            pass  # Config access failure → no correction, safe fallback
        
        self.city = city
        self.target_date = target_date
        self.settlement_semantics = settlement_semantics
        
        # Simulated settlement values (may have floating decimals if precision < 1)
        self.member_maxes_settled: np.ndarray = self._simulate_settlement(self.member_maxes)

    def _simulate_settlement(self, values: np.ndarray) -> np.ndarray:
        return self.settlement_semantics.round_values(values)

    @staticmethod
    def _parse_forecast_timestamp(value: str) -> datetime:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _apply_bias_correction(
        maxes: np.ndarray, city: City, target_date: date
    ) -> np.ndarray:
        """Apply per-city×season ECMWF bias correction to member maxes.

        model_bias.bias = mean(forecast - actual). Positive = model too warm.
        Subtract bias × discount_factor from member maxes.

        INVARIANT: If this runs for live signals, ALL calibration_pairs must
        also have been computed with bias correction. The cross-module test
        test_calibration_pairs_use_same_bias_correction_as_live enforces this.
        """
        try:
            from src.state.db import get_world_connection

            from src.calibration.manager import season_from_date

            season = season_from_date(target_date.isoformat(), lat=city.lat)

            conn = get_world_connection()
            row = conn.execute(
                "SELECT bias, discount_factor, n_samples FROM model_bias "
                "WHERE city = ? AND season = ? AND source = 'ecmwf'",
                (city.name, season),
            ).fetchone()
            conn.close()

            if row and row["n_samples"] >= 20:
                discount = row["discount_factor"] if row["discount_factor"] else 0.7
                correction = row["bias"] * discount
                import logging
                logging.getLogger(__name__).info(
                    "Bias correction %s/%s: %.2f° × %.1f = %.2f° (n=%d)",
                    city.name, season, row["bias"], discount,
                    correction, row["n_samples"],
                )
                return maxes - correction

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Bias correction failed for %s: %s", city.name, e
            )

        return maxes

    @staticmethod
    def _select_hours_for_date(
        target_date: date,
        tz: ZoneInfo,
        *,
        times: list[str],
    ) -> np.ndarray:
        """Select hourly indices belonging to target_date in the city's timezone.

        This is a hard time-semantics contract: hourly forecast timestamps must be
        present and the local-day window is selected from those actual timestamps.
        Approximate lead-day slicing is forbidden here because it can drift from
        the decision-reference semantics used elsewhere in the pipeline.
        """
        return select_hours_for_target_date(target_date, tz, times=times)

    def p_raw_vector(
        self, bins: list[Bin], n_mc: int | None = None
    ) -> np.ndarray:
        """Probability vector over all bins with instrument noise.

        Spec §2.1: Monte Carlo with ε ~ N(0, σ_instrument²) per member.
        Simulates full settlement chain according to SettlementSemantics rules.

        Delegates to ``p_raw_vector_from_maxes`` (module-level) so offline
        calibration rebuilds that do not have hourly context share the exact
        same code path. The seed=None default preserves legacy behavior;
        tests that require determinism should patch ``np.random.default_rng``
        or pass ``rng`` through the module-level function directly.

        Returns: np.ndarray shape (n_bins,), sums to 1.0
        """
        return p_raw_vector_from_maxes(
            self.member_maxes,
            self.city,
            self.settlement_semantics,
            bins,
            n_mc=n_mc,
        )

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

        # Unit-aware: if spread < 1 instrument noise, members are in consensus
        if rng < sigma_instrument(self.city.settlement_unit).value:
            return False  # All members agree — definitely unimodal

        try:
            kde = gaussian_kde(maxes)
            x = np.linspace(maxes.min() - 1, maxes.max() + 1, 200)
            density = kde(x)
            peaks = argrelextrema(density, np.greater, order=ensemble_bimodal_kde_order())[0]
            return len(peaks) >= 2
        except Exception:
            # Fallback: gap heuristic
            sorted_maxes = np.sort(maxes)
            gaps = np.diff(sorted_maxes)
            return rng > 0 and float(gaps.max()) / rng > ensemble_bimodal_gap_ratio()

    def boundary_sensitivity(self, boundary: float) -> float:
        """Fraction of 51 members within ±σ_instrument of a bin boundary.

        Window is unit-aware: 0.5°F for US cities, 0.28°C for metric cities.
        High sensitivity → probability estimate is fragile at this boundary.
        """
        window = sigma_instrument(self.city.settlement_unit).value
        return float(
            np.sum(np.abs(self.member_maxes - boundary) < window)
        ) / len(self.member_maxes)
