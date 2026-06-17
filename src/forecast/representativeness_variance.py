# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator spec zeus_source_access_validation_v3.xlsx GridCorrectionMath
#   rule 4 (elevation/station correction x_station = T_interp + beta_alt*(z_station -
#   z_interp) + b_grid; beta_alt AND b_grid WALK-FORWARD fitted from settlement
#   residuals, NEVER a hardcoded lapse rate as a live shift) + rule 5
#   (sigma_repr^2 = g(d_eff, |z_station - z_interp|, regime), MLE/walk-forward fitted,
#   ADDED to the instrument covariance Sigma DIAGONAL: Sigma_source = Sigma_model_residual
#   + sigma_repr^2 — NEVER a hand down/up-weight). The existing
#   src/forecast/bayes_precision_fusion.py fusion (V*=(tau0^-2+1'Sigma^-1 1)^-1;
#   mu*=V*(tau0^-2 mu0 + 1'Sigma^-1 z)) stays the authority; sigma_repr only enters Sigma.
#   Operator message: build-only (no live-fusion wiring; show the Sigma entry in a test).
"""representativeness_variance — sigma_repr^2 = g(...) + station correction (v3 rule 4/5).

THE VARIANCE THAT ENTERS Sigma — NOT A HAND WEIGHT. The whole v3 correction is honest
only because the representativeness penalty enters the fusion as ADDED OBSERVATION
VARIANCE on the source's Sigma diagonal, and the EXISTING Bayes fusion then DERIVES the
down-weight via Sigma^-1. We never multiply a source's weight by hand. A station that is
far from its native cell (large d_eff) or sits at a very different elevation than the
interpolated grid surface (large |z_station - z_interp|) is a NOISIER instrument for
that city; rule 5 turns that noise into sigma_repr^2 and rule 4 (the elevation/grid
shift) corrects the mean. The fusion's V*/mu* math is untouched.

TWO PIECES (operator rules 4 and 5):

  Rule 4 — station/elevation correction of the MEAN:
      x_station = T_interp + beta_alt(city,season,metric,lead) * (z_station - z_interp)
                  + b_grid(city,season,metric,lead,source_model)
    beta_alt and b_grid are WALK-FORWARD FITTED from settled residuals — NEVER a
    hardcoded lapse rate applied as a live shift. This module provides:
      * ``station_correction(...)`` : the pure transform given (beta_alt, b_grid).
      * ``StationShiftFit`` + ``fit_station_shift(...)`` : the FIT INTERFACE — consumes
        settled residual rows and returns (beta_alt, b_grid) PER STRATUM.
      * ``COLD_START_STATION_SHIFT`` : the clearly-labelled cold-start (beta_alt=0,
        b_grid=0) used ONLY until a fit exists. With it, station_correction == T_interp
        (no live shift) — so an unfitted city is never silently lapse-rate-shifted.

  Rule 5 — representativeness VARIANCE:
      sigma_repr^2 = g(d_eff, |z_station - z_interp|, coastal_regime, orography_regime,
                       urban_regime), fitted by MLE / walk-forward.
    This module provides:
      * ``representativeness_variance(...)`` : g, the cold-start conservative form, with
        a fitted-coefficient override interface.
      * ``ReprVarianceFit`` + ``fit_representativeness_variance(...)`` : the FIT
        INTERFACE — consumes settled residual rows + the d_eff/|dz|/regime features and
        returns the fitted g-coefficients PER STRATUM.
      * ``sigma_with_representativeness(...)`` : Sigma_source = Sigma_model_residual +
        sigma_repr^2 — the exact diagonal add the fusion consumes. It is + on the
        diagonal, never a weight.

COLD-START vs FIT: every fitted quantity has a documented cold-start that is INERT
(beta_alt=0, b_grid=0 → no mean shift; the cold-start g is conservative variance that
only ever WIDENS, never narrows, and is zero when d_eff and |dz| are zero). The live
path must use the FIT once it exists; the cold-start exists only so an unfitted city is
safe-by-widening rather than wrong-by-shifting.

PURITY: pure math. No network, no DB writes. The fit interfaces are pure functions over
already-loaded residual rows (the caller owns the settlement read).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence


# ============================================================================
# Rule 4 — station/elevation correction of the MEAN (fit interface + cold start)
# ============================================================================
@dataclass(frozen=True)
class StationShiftFit:
    """Walk-forward-fitted station/grid shift coefficients for ONE stratum.

    A stratum key is (city, season, metric, lead) for ``beta_alt`` and additionally
    ``source_model`` for ``b_grid`` (rule 4). ``beta_alt`` is the fitted elevation
    sensitivity (degC per metre of z_station - z_interp); ``b_grid`` is the fitted
    residual grid bias (degC). ``n_train`` is the count of settled residual rows the
    fit consumed (provenance + a thin-fit signal for the caller). ``fitted`` is False
    for the cold-start sentinel so a consumer can tell an inert default from a real fit.
    """

    beta_alt: float
    b_grid: float
    n_train: int = 0
    fitted: bool = False


# The clearly-labelled cold-start: NO live shift. Used ONLY until a fit exists.
# beta_alt=0 → no elevation shift; b_grid=0 → no grid-bias shift; so with this default
# station_correction returns T_interp unchanged (rule 4's "never a hardcoded lapse rate
# as a live shift" — the live shift may ONLY come from a real fit).
COLD_START_STATION_SHIFT = StationShiftFit(beta_alt=0.0, b_grid=0.0, n_train=0, fitted=False)


def station_correction(
    T_interp: float,
    z_station: float,
    z_interp: float,
    shift: StationShiftFit = COLD_START_STATION_SHIFT,
) -> float:
    """x_station = T_interp + beta_alt*(z_station - z_interp) + b_grid  (rule 4).

    ``shift`` carries the WALK-FORWARD-fitted beta_alt/b_grid for the stratum. With the
    cold-start (beta_alt=0, b_grid=0) this returns T_interp unchanged — no live lapse
    shift. The live caller MUST pass a fitted ``StationShiftFit`` (from
    ``fit_station_shift``); the cold-start is the only sanctioned no-fit value.
    """
    return T_interp + shift.beta_alt * (z_station - z_interp) + shift.b_grid


@dataclass(frozen=True)
class StationShiftResidualRow:
    """One settled observation for the station-shift fit.

    ``settlement_residual`` = settled_truth - T_interp (the part rule 4 must explain),
    ``dz`` = z_station - z_interp (the elevation regressor). The fit regresses the
    residual on dz: beta_alt is the slope, b_grid the intercept, both per stratum.
    """

    settlement_residual: float
    dz: float


def fit_station_shift(
    rows: Sequence[StationShiftResidualRow],
    *,
    min_train: int = 25,
) -> StationShiftFit:
    """FIT INTERFACE (rule 4): least-squares fit of residual = beta_alt*dz + b_grid.

    Consumes already-loaded, strictly-walk-forward settled residual rows for ONE
    stratum (the caller selects rows with target_date < the date being forecast, so no
    leakage). Returns the fitted (beta_alt, b_grid). Below ``min_train`` rows, or when
    the dz regressor has no spread (every station at the same elevation offset →
    beta_alt unidentifiable), it returns the COLD-START (inert, fitted=False) rather
    than fabricating a slope. This is the interface the live wiring step will call; the
    settlement read is the caller's job.
    """
    n = len(rows)
    if n < min_train:
        return replace(COLD_START_STATION_SHIFT, n_train=n)
    xs = [r.dz for r in rows]
    ys = [r.settlement_residual for r in rows]
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx <= 1e-12:
        # No elevation spread: beta_alt is unidentifiable; fit only the intercept
        # (b_grid = mean residual), keep beta_alt at the inert 0.
        return StationShiftFit(beta_alt=0.0, b_grid=ybar, n_train=n, fitted=True)
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    beta_alt = sxy / sxx
    b_grid = ybar - beta_alt * xbar
    return StationShiftFit(beta_alt=beta_alt, b_grid=b_grid, n_train=n, fitted=True)


# ============================================================================
# Rule 5 — representativeness VARIANCE sigma_repr^2 = g(...) (fit interface + cold start)
# ============================================================================
# Regime multipliers: a coastal / steep-orography / dense-urban station is less
# representative of a grid cell at the SAME d_eff/|dz| than a flat inland rural one.
# These are part of g; the cold-start values are conservative (>= 1, widen-only) and
# the fit interface returns learned replacements.
@dataclass(frozen=True)
class ReprVarianceFit:
    """Walk-forward / MLE-fitted coefficients of g for ONE stratum (rule 5).

    g(d_eff, |dz|, regime) = (a0
                              + a_d * (d_eff/1000)^2        # per-km^2 distance term
                              + a_z * dz^2)                 # per-metre^2 elevation term
                             * regime_multiplier
    All coefficients are >= 0 so g is a VARIANCE that only ever adds. ``a0`` is the
    irreducible representativeness floor (degC^2). The regime multipliers scale the
    whole term. ``fitted`` is False for the cold-start sentinel.
    """

    a0: float
    a_d: float
    a_z: float
    coastal_mult: float = 1.0
    orography_mult: float = 1.0
    urban_mult: float = 1.0
    n_train: int = 0
    fitted: bool = False


# Conservative cold-start g (documented): small positive a0 floor, gentle distance and
# elevation slopes, regime multipliers >= 1. WIDEN-ONLY: every coefficient is >= 0, so
# sigma_repr^2 >= 0 always, and it is exactly 0 only when a0=0; here a0>0 gives a small
# honest representativeness floor. These are NOT a tuned lapse/weight — they are a
# conservative variance used ONLY until the MLE fit exists.
COLD_START_REPR_VARIANCE = ReprVarianceFit(
    a0=0.25,        # 0.25 degC^2  -> 0.5 degC representativeness floor at d_eff=dz=0
    a_d=0.04,       # +0.04 degC^2 per km^2 of effective distance (0.2 degC at 1 km)
    a_z=2.5e-5,     # +2.5e-5 degC^2 per m^2 of |dz| (0.25 degC at 100 m offset)
    coastal_mult=1.5,
    orography_mult=1.5,
    urban_mult=1.25,
    n_train=0,
    fitted=False,
)


def _regime_multiplier(
    fit: ReprVarianceFit,
    coastal: bool,
    orography: bool,
    urban: bool,
) -> float:
    """Combined regime multiplier (each regime that applies scales g)."""
    m = 1.0
    if coastal:
        m *= fit.coastal_mult
    if orography:
        m *= fit.orography_mult
    if urban:
        m *= fit.urban_mult
    return m


def representativeness_variance(
    d_eff_m: float,
    dz_m: float,
    *,
    coastal: bool = False,
    orography: bool = False,
    urban: bool = False,
    fit: ReprVarianceFit = COLD_START_REPR_VARIANCE,
) -> float:
    """sigma_repr^2 = g(d_eff, |z_station - z_interp|, regime)  (rule 5).

    ``d_eff_m`` is the effective station<->grid distance (metres, from
    grid_interpolation); ``dz_m`` is z_station - z_interp (sign-agnostic — only |dz|
    enters as dz^2). Regime flags select the fitted multipliers. ``fit`` is the
    walk-forward / MLE-fitted ``ReprVarianceFit`` (or the conservative cold-start).

    Returns a NON-NEGATIVE variance (degC^2) that is monotone non-decreasing in both
    d_eff and |dz| (every coefficient >= 0). This value is what
    ``sigma_with_representativeness`` ADDS to the source's Sigma diagonal — it is never
    a hand weight.
    """
    if d_eff_m < 0.0:
        raise ValueError("d_eff_m must be >= 0")
    d_km = d_eff_m / 1000.0
    base = fit.a0 + fit.a_d * (d_km * d_km) + fit.a_z * (dz_m * dz_m)
    sigma2 = base * _regime_multiplier(fit, coastal, orography, urban)
    return max(0.0, sigma2)


def sigma_with_representativeness(
    sigma_model_residual_sq: float,
    sigma_repr_sq: float,
) -> float:
    """Sigma_source (diagonal entry) = Sigma_model_residual + sigma_repr^2  (rule 5).

    The model-residual variance is the source's instrument variance the fusion already
    uses on the Sigma diagonal; sigma_repr^2 is ADDED to it. The result is the diagonal
    entry the existing Bayes fusion inverts. Because it is a larger diagonal, the
    fusion's own Sigma^-1 yields a SMALLER weight for that instrument — the down-weight
    is DERIVED by the fusion, not applied by hand here.

    Raises on a negative input (a variance must be >= 0).
    """
    if sigma_model_residual_sq < 0.0 or sigma_repr_sq < 0.0:
        raise ValueError("variances must be >= 0")
    return sigma_model_residual_sq + sigma_repr_sq


@dataclass(frozen=True)
class ReprResidualRow:
    """One settled observation for the representativeness-variance MLE fit.

    ``settlement_residual`` = settled_truth - x_station (the corrected-forecast error
    whose VARIANCE rule 5 must explain), with its features ``d_eff_m``, ``dz_m``, and
    the regime flags. The fit regresses squared residual on the g feature design.
    """

    settlement_residual: float
    d_eff_m: float
    dz_m: float
    coastal: bool = False
    orography: bool = False
    urban: bool = False


def fit_representativeness_variance(
    rows: Sequence[ReprResidualRow],
    *,
    min_train: int = 25,
    base_fit: ReprVarianceFit = COLD_START_REPR_VARIANCE,
) -> ReprVarianceFit:
    """FIT INTERFACE (rule 5): walk-forward fit of g's distance/elevation slopes.

    Consumes already-loaded, strictly-walk-forward settled residual rows for ONE
    stratum. We fit the variance model E[residual^2] = a0 + a_d*(d_eff/1000)^2 +
    a_z*dz^2 by least squares on the per-row squared residual against the design
    [1, (d_eff/1000)^2, dz^2] (regime multipliers carried from ``base_fit`` — they are
    a coarser stratification the live wiring can refine). Coefficients are clamped to
    >= 0 so g stays a widen-only variance. Below ``min_train`` rows it returns the
    cold-start (inert, fitted=False) rather than over-fitting a slope.

    A non-intercept feature column with NO spread in this stratum (e.g. every station at
    the same elevation offset, so dz^2 is constant) is an UNIDENTIFIABLE regressor: it
    is DROPPED from the design and its coefficient set to 0, while the remaining
    identifiable columns are still fit. That is identifiability, not a workaround — a
    constant regressor genuinely carries no slope information, so 0 is its only honest
    estimate. This is why a distance-only stratum still recovers ``a_d`` instead of
    collapsing to a mean-only floor.

    This is the interface the live wiring step calls; the settlement read + the regime
    classification are the caller's job. Returning a ``ReprVarianceFit`` keeps the
    live path on the FIT, never the cold-start, once enough settled rows exist.
    """
    n = len(rows)
    if n < min_train:
        return replace(base_fit, n_train=n, fitted=False)

    # Full design columns: 0 = intercept (a0), 1 = (d_eff/1000)^2 (a_d), 2 = dz^2 (a_z).
    # Target y = residual^2. Pure-Python normal equations (no numpy needed; purity).
    full_feats: list[tuple[float, float, float]] = []
    ys: list[float] = []
    for r in rows:
        d_km = r.d_eff_m / 1000.0
        full_feats.append((1.0, d_km * d_km, r.dz_m * r.dz_m))
        ys.append(r.settlement_residual * r.settlement_residual)

    # Drop unidentifiable (constant) non-intercept columns; the intercept is always kept.
    def _col_varies(col: int) -> bool:
        vals = [f[col] for f in full_feats]
        return (max(vals) - min(vals)) > 1e-12

    active_cols = [0] + [c for c in (1, 2) if _col_varies(c)]
    k = len(active_cols)

    # Reduced normal equations A (k x k), bvec (k,) over the active columns only.
    A = [[0.0] * k for _ in range(k)]
    bvec = [0.0] * k
    for f, y in zip(full_feats, ys):
        for ii, ci in enumerate(active_cols):
            bvec[ii] += f[ci] * y
            for jj, cj in enumerate(active_cols):
                A[ii][jj] += f[ci] * f[cj]

    coeffs = _solve_linear(A, bvec)
    full_coeffs = [0.0, 0.0, 0.0]
    if coeffs is None:
        # Even the reduced design is singular (degenerate inputs): the only honest
        # variance estimate is the mean squared residual carried by the intercept.
        full_coeffs[0] = sum(ys) / n
    else:
        for ii, ci in enumerate(active_cols):
            full_coeffs[ci] = coeffs[ii]

    a0, a_d, a_z = (max(0.0, c) for c in full_coeffs)
    return ReprVarianceFit(
        a0=a0,
        a_d=a_d,
        a_z=a_z,
        coastal_mult=base_fit.coastal_mult,
        orography_mult=base_fit.orography_mult,
        urban_mult=base_fit.urban_mult,
        n_train=n,
        fitted=True,
    )


def _solve_linear(A: list[list[float]], b: list[float]) -> list[float] | None:
    """Solve a square k x k linear system by Gaussian elimination with partial pivoting.

    Returns the solution list (length k), or None if the matrix is singular (so the
    caller can fall back rather than divide by zero). Kept dependency-free to preserve
    module purity; k is 1, 2, or 3 here (the active-column count of the variance fit).
    """
    k = len(A)
    # Augmented copy [A | b].
    M = [list(A[i]) + [b[i]] for i in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pivval = M[col][col]
        M[col] = [v / pivval for v in M[col]]
        for r in range(k):
            if r != col:
                factor = M[r][col]
                M[r] = [M[r][j] - factor * M[col][j] for j in range(k + 1)]
    return [M[i][k] for i in range(k)]
