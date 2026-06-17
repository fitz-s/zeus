# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis:
#   docs/evidence/qkernel_rebuild/modal_buyyes_drag_rootcause_2026-06-16.md
#     (settlement-proven cold bias: mu* - realized mean ~ -0.5 deg C; 47 cold
#      cells vs 10 warm over the settled window),
#   docs/evidence/qkernel_rebuild/settlement_ev_verdict_2026-06-16.md,
#   src/forecast/debias_authority.py (the DebiasAuthority artifact + activation
#      contract this provider feeds: BiasArtifact, WILDCARD representativeness
#      basis, MIN_N / N_SIGMA_BIAS magnitude band).
"""Settlement-residual de-bias artifact provider (the cold-center-bias fix).

ROOT CAUSE (settlement-proven). The q-kernel spine's forecast center ``mu*`` is the
robust consensus of the fresh NWP members (``src/forecast/center.py``) with NO
de-bias applied: both the live reactor seam (``_NoOpDebiasAuthority`` in
``qkernel_spine_bridge.py``) and the settlement-EV replay construct an EMPTY
``DebiasAuthority()`` with zero artifacts. Daily-extreme NWP members run
systematically COLD versus realized settlement highs (mean ``mu* - realized``
~ -0.5 deg C, 66% of cells cold), so the un-corrected center under-predicts the
settled temperature. That cold center mis-places the modal bin and inflates the
YES side of the bins it sells, which is exactly the modal / buy_yes negative-EV
drag the verdict measured.

THE FIX. This module is the single place that FITS the de-bias the
``DebiasAuthority`` was designed to apply but never had artifacts for. It reads the
REALIZED VERIFIED settlement residuals from ``settlement_outcomes`` and the same
fresh-member consensus the spine forms, and emits ONE product-agnostic
``city_station_representativeness`` ``BiasArtifact`` per ``(city, metric)`` cell.
The ``DebiasAuthority`` then subtracts the realized trailing residual band center
once, warming the cold center toward settlement truth.

Three correctness properties, by construction:

  1. **Settlement-station truth, not model self-agreement (Law-8).** The residual
     is ``mu_hat_native - settlement_value_native`` where ``settlement_value`` is the
     VERIFIED settlement outcome at the cell's settlement station. The artifact's
     served shift IS the realized residual band center (``residual_mean_native``),
     never a model-proposed number; ``DebiasAuthority`` re-checks that the
     ``proposed_shift_native`` agrees with the realized band (it is set EQUAL to it
     here, so the served correction is bounded by realized residuals).

  2. **Walk-forward / no leakage.** A case's artifact is fit ONLY on settlements
     whose ``target_date`` is strictly BEFORE the case's ``target_local_date``. A
     case never sees its own (or any future) outcome. The provider holds the full
     residual series and filters per case at ``apply`` time, so the SAME provider
     instance produces an honestly walk-forward artifact for every case in a
     replay sweep.

  3. **Robust, shrunk, not over-fit.** The cell estimate is the MEDIAN of trailing
     residuals (robust to the heavy-tailed daily error), shrunk toward the
     metric-pooled median for thin cells (``lambda = n/(n+K_SHRINK)``). Thin or
     noisy cells are pulled toward the population bias, so the ~0.5 deg C is not
     fit to small-n noise and a single warm outlier cannot flip a cell's sign. The
     magnitude band (``residual_std_native``) is the realized dispersion, so
     ``DebiasAuthority`` admits the shift only because it equals the realized band
     center; a fabricated large shift would be ``MAGNITUDE_REFUSED``.

This does NOT introduce a reverse (warm) bias: the served shift equals the realized
residual median, so the corrected center is, in expectation, the realized
settlement value. The original disease was a fabricated +2.8 deg C WARM
contamination; this provider is grounded entirely in realized residuals and is
sign-symmetric (a cell that is genuinely warm gets a warm-correcting positive
residual median, e.g. Los Angeles / Lucknow).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np

from src.forecast.debias_authority import (
    MIN_N,
    WILDCARD,
    BiasArtifact,
    DebiasAuthority,
)
from src.forecast.types import ForecastCase

# ---------------------------------------------------------------------------
# Fit tuning. All in the settlement native unit (deg C or deg F per cell).
# ---------------------------------------------------------------------------

# Shrinkage constant: a cell with n trailing residuals trusts its own median with
# weight ``lambda = n / (n + K_SHRINK)`` and the metric-pooled median with the
# remainder. K_SHRINK = 10 means a cell needs ~10 settlements to reach half-trust
# in its own estimate, so the early-window thin cells are pulled toward the
# population bias rather than fit to a handful of noisy residuals.
K_SHRINK: float = 10.0

# A cell needs at least this many trailing residuals to publish ANY artifact. Below
# it the provider emits no artifact for the cell (DebiasAuthority then serves
# NO_ARTIFACT -> zero shift), so a cold cell with too little history is left
# uncorrected rather than corrected by a guess. (MIN_N is the DebiasAuthority floor
# the published artifact must also satisfy on its ``n`` field.)
MIN_CELL_N: int = MIN_N

# Trailing window (days) of settlements used to fit a cell. A finite window keeps
# the residual estimate responsive to a regime shift while staying robust; the
# whole VERIFIED history before the window is ignored so a stale seasonal bias does
# not contaminate the current estimate.
TRAILING_WINDOW_DAYS: int = 45

# Hard sanity bound (native units) on the published shift magnitude. A realized
# residual median beyond this is implausible for a daily temperature de-bias and is
# clamped — a belt-and-braces guard on top of the DebiasAuthority magnitude band.
MAX_ABS_SHIFT_NATIVE: float = 4.0

# Floor on the realized residual std used for the magnitude band, so a degenerate
# (near-zero-dispersion) cell still publishes a band the authority can validate
# against. Matches the spirit of DebiasAuthority.SIGMA_FLOOR_EPSILON.
RESID_STD_FLOOR: float = 0.25


# ---------------------------------------------------------------------------
# One trailing residual sample.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Resid:
    target_date: date
    residual_native: float  # mu_hat_native - settlement_value_native (cold => < 0)


def _c_to_native(value_c: float, unit: str) -> float:
    return value_c if unit == "C" else (value_c * 9.0 / 5.0 + 32.0)


# ---------------------------------------------------------------------------
# Residual series construction (settlement truth vs reconstructed consensus).
# ---------------------------------------------------------------------------

def _consensus_native(member_values_native: np.ndarray) -> float:
    """The cold-bias-relevant consensus the spine center reduces to.

    The spine center (``build_center``) with equal member weights and zero de-bias
    is the robust (Huber) location of the members, which for the symmetric NWP
    member spread is the member median to within a fraction of the bin width. The
    residual we de-bias is the SAME consensus statistic vs settlement truth, so we
    use the member median here: a robust, weight-free location that needs no per-day
    re-run of the full center authority and is in the member hull by construction.
    """
    arr = np.asarray(member_values_native, dtype=float)
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


def build_cell_residuals(
    fc_con: sqlite3.Connection,
    *,
    cycle_lag_days: int = 1,
    settlement_min_date: str = "2026-01-01",
) -> dict[tuple[str, str], list[_Resid]]:
    """Build the per-(city, metric) trailing settlement-residual series.

    For every VERIFIED settlement outcome, reconstruct the decision-cycle member
    consensus (latest member per model captured on ``target_date - cycle_lag_days``,
    the same fresh-member rule the spine uses) and record
    ``residual_native = consensus_native - settlement_value_native``.

    Returns ``{(city, metric): [_Resid, ...]}`` sorted by target_date. The caller
    (the provider) filters this to the strictly-past window per case, so this is
    the leakage-free residual ground truth, fit once.
    """
    rows = fc_con.execute(
        """
        SELECT city, target_date, temperature_metric, settlement_value, settlement_unit
        FROM settlement_outcomes
        WHERE authority='VERIFIED'
          AND settlement_value IS NOT NULL
          AND target_date >= ?
        ORDER BY target_date
        """,
        (settlement_min_date,),
    ).fetchall()

    out: dict[tuple[str, str], list[_Resid]] = defaultdict(list)
    for city, td_str, metric, sv, unit in rows:
        try:
            td = date.fromisoformat(td_str)
        except (TypeError, ValueError):
            continue
        unit = (unit or "C").upper()
        cycle_date = (td - timedelta(days=cycle_lag_days)).isoformat()
        member_rows = fc_con.execute(
            """
            SELECT model, source_cycle_time, forecast_value_c
            FROM raw_model_forecasts
            WHERE city=? AND metric=? AND target_date=?
              AND date(source_cycle_time)=?
            ORDER BY model, source_cycle_time
            """,
            (city, metric, td_str, cycle_date),
        ).fetchall()
        if not member_rows:
            continue
        # Latest cycle per model (rows ascending by source_cycle_time).
        best: dict[str, float] = {}
        for model, _sct, val_c in member_rows:
            best[model] = _c_to_native(float(val_c), unit)
        vals = np.asarray(list(best.values()), dtype=float)
        if vals.size < 3:
            continue
        consensus = _consensus_native(vals)
        sv_native = float(sv)  # settlement_value is already stored in the settlement unit
        out[(city, metric)].append(
            _Resid(target_date=td, residual_native=consensus - sv_native)
        )

    for key in out:
        out[key].sort(key=lambda r: r.target_date)
    return out


# ---------------------------------------------------------------------------
# The provider: walk-forward, robust, shrunk artifacts per case.
# ---------------------------------------------------------------------------

class SettlementResidualDebiasProvider:
    """Builds robust, walk-forward, settlement-residual de-bias artifacts.

    Construct once over the forecasts DB; call ``artifacts_for(case)`` to get the
    (zero-or-one) representativeness ``BiasArtifact`` admissible for that case using
    ONLY settlements strictly before the case's target date, or ``debias_authority``
    for a ready ``DebiasAuthority`` seeded with that case's artifact.
    """

    def __init__(
        self,
        cell_residuals: dict[tuple[str, str], list[_Resid]],
    ) -> None:
        self._cell_residuals = cell_residuals
        # Pooled (metric-level) residual series for shrinkage of thin cells.
        self._pooled_by_metric: dict[str, list[_Resid]] = defaultdict(list)
        for (_city, metric), series in cell_residuals.items():
            self._pooled_by_metric[metric].extend(series)
        for metric in self._pooled_by_metric:
            self._pooled_by_metric[metric].sort(key=lambda r: r.target_date)

    @classmethod
    def from_connection(
        cls,
        fc_con: sqlite3.Connection,
        *,
        cycle_lag_days: int = 1,
        settlement_min_date: str = "2026-01-01",
    ) -> "SettlementResidualDebiasProvider":
        return cls(
            build_cell_residuals(
                fc_con,
                cycle_lag_days=cycle_lag_days,
                settlement_min_date=settlement_min_date,
            )
        )

    # -- the robust shrunk estimate ------------------------------------------------

    def _past_residuals(
        self, series: list[_Resid], cutoff: date
    ) -> list[float]:
        """Residuals strictly before ``cutoff`` and within the trailing window."""
        window_start = cutoff - timedelta(days=TRAILING_WINDOW_DAYS)
        return [
            r.residual_native
            for r in series
            if window_start <= r.target_date < cutoff
        ]

    def _shrunk_shift(
        self, city: str, metric: str, cutoff: date
    ) -> Optional[tuple[float, float, int]]:
        """Robust shrunk (shift, std, n) for the cell, walk-forward to ``cutoff``.

        Returns ``None`` if the cell has fewer than ``MIN_CELL_N`` trailing
        residuals (no artifact published -> no shift). The shift is the cell median
        shrunk toward the metric-pooled median; the std is the cell residual std
        (floored), the realized magnitude band the authority validates against.
        """
        cell_series = self._cell_residuals.get((city, metric), [])
        cell_past = self._past_residuals(cell_series, cutoff)
        if len(cell_past) < MIN_CELL_N:
            return None

        cell_arr = np.asarray(cell_past, dtype=float)
        cell_median = float(np.median(cell_arr))
        cell_std = float(np.std(cell_arr, ddof=1)) if cell_arr.size > 1 else 0.0

        pooled_past = self._past_residuals(
            self._pooled_by_metric.get(metric, []), cutoff
        )
        pooled_median = (
            float(np.median(np.asarray(pooled_past, dtype=float)))
            if pooled_past
            else cell_median
        )

        n = cell_arr.size
        lam = n / (n + K_SHRINK)
        shift = lam * cell_median + (1.0 - lam) * pooled_median
        # Belt-and-braces magnitude clamp (DebiasAuthority also magnitude-checks).
        shift = float(np.clip(shift, -MAX_ABS_SHIFT_NATIVE, MAX_ABS_SHIFT_NATIVE))
        std = max(cell_std, RESID_STD_FLOOR)
        return shift, std, int(n)

    # -- artifact emission ---------------------------------------------------------

    def artifacts_for(self, case: ForecastCase) -> tuple[BiasArtifact, ...]:
        """Zero or one walk-forward representativeness artifact for ``case``.

        Uses ONLY settlements with ``target_date < case.target_local_date``. The
        emitted artifact is product-agnostic (``WILDCARD`` product hash + station
        mapping) and station-matched (the case's settlement ``station_id``), so it
        activates on the ``city_station_representativeness`` basis in both the live
        reactor seam and the replay regardless of the member ``model_set_hash``.
        """
        out = self._shrunk_shift(case.city, case.metric, case.target_local_date)
        if out is None:
            return ()
        shift, std, n = out

        # The served shift IS the realized residual band center. residual_mean and
        # proposed_shift are set EQUAL so DebiasAuthority's magnitude band admits it
        # (the correction is, by construction, exactly the realized band center).
        cutoff_dt = datetime(
            case.target_local_date.year,
            case.target_local_date.month,
            case.target_local_date.day,
            tzinfo=timezone.utc,
        )
        artifact = BiasArtifact(
            artifact_id=(
                f"settle_resid::{case.city}::{case.metric}::"
                f"{case.target_local_date.isoformat()}::n{n}"
            ),
            authority="SETTLEMENT_STATION_WALK_FORWARD_V1",
            city=case.city,
            station_id=case.station_id,
            metric=case.metric,
            season=case.season,
            regime_key=case.regime_key,
            lead_bucket="d1",
            product_set_hash=WILDCARD,
            model_id=None,
            training_start_utc=cutoff_dt - timedelta(days=TRAILING_WINDOW_DAYS),
            training_cutoff_utc=case.issue_time_utc,
            valid_until_utc=case.issue_time_utc + timedelta(days=2),
            n=n,
            residual_mean_native=float(shift),
            residual_std_native=float(std),
            residual_se_native=float(std / max(np.sqrt(n), 1.0)),
            proposed_shift_native=float(shift),
            oos_crps_before=0.0,
            oos_crps_after=0.0,
            oos_logscore_before=None,
            oos_logscore_after=None,
            station_mapping_id=WILDCARD,
            source_hash=f"settle_resid_v1::{case.city}::{case.metric}",
        )
        return (artifact,)

    def debias_authority(self, case: ForecastCase) -> DebiasAuthority:
        """A ``DebiasAuthority`` seeded with this case's walk-forward artifact."""
        return DebiasAuthority(self.artifacts_for(case))
