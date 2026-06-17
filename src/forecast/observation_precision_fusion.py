# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator directive "处理多源数据整合" (multi-source data
#   integration). Distinct OBSERVATION-side analogue of the T2 FORECAST fusion
#   (src/forecast/bayes_precision_fusion.py) — NOT an extension of it. σ-term
#   provenance is sourced from existing persisted facts (see module docstring):
#     - config/wu_obs_latency.json            via src/signal/day0_obs_latency.py
#     - config/wu_metar_divergence.json       (measured WU-vs-METAR divergence)
#     - src/contracts/settlement_semantics.settlement_preimage_offsets (rounding quantum)
#   do_not_rebuild manifest: the live METAR/WU fetch lane (day0_fast_obs.py,
#   observation_client.py, daily_obs_append.py) and the forecast fusion are
#   READ-ONLY references; this module touches none of them.
"""Multi-source day0 OBSERVATION precision fusion.

THE PROBLEM. For one (city, target_date, metric, decision_time) several
observation sources may report the SAME daily extreme — e.g. for US cities both
MADIS HF-METAR and AviationWeather METAR resolve the SAME physical ICAO station;
for HK the HKO realtime API. Naively averaging (or worse, summing precisions)
treats two readings of the same station as two independent draws and
DOUBLE-COUNTS the information. The fused estimate must:

  1. weight each source by PRECISION (inverse effective variance), not equally;
  2. fuse CORRELATED sources (same station) through a covariance estimator that
     SHRINKS toward its diagonal (Ledoit-Wolf style), so same-station readings
     collapse toward ONE effective observation rather than N;
  3. report an effective sample count n_eff that is < N exactly to the degree the
     sources are correlated (n_eff → 1 for perfectly-correlated same-station
     readings; n_eff → N for fully-independent stations).

This is the OBSERVATION analogue of the T2 forecast fusion. It is a DISTINCT
layer: the forecast fusion fuses model PREDICTIONS against a prior; this fuses
realized STATION OBSERVATIONS of a quantity that has already happened (the
running daily extreme), so there is no prior — it is a pure precision-weighted
GLS combine with a correlation-aware effective-information correction.

EFFECTIVE VARIANCE per source j (all in the city's NATIVE settlement unit):

    σ_eff,j²  =  σ_sensor,j²
              +  σ_rounding,j²
              +  σ_lag,j²(Δt)
              +  σ_station_mismatch,j²
              +  σ_provider_transform,j²

    precision  τ_j = 1 / σ_eff,j²

σ-TERM PROVENANCE (every term sourced from a persisted fact, NOT an
operator-picked constant invented here):

  σ_lag,j²(Δt) — staleness variance. Δt = decision_time − observation_available_at.
      A stale running extreme may have been overtaken by an unseen later report.
      The plausible move scale is the SAME rate the day0 stale-extreme guard uses
      (src/signal/day0_obs_latency._MAX_MOVE_PER_HOUR: 2.5°C/h for C-cities,
      4.5°F/h for F-cities), passed in as ``plausible_move_rate``. The staleness
      BUDGET (one report interval + publication delay) is per-city from
      config/wu_obs_latency.json via staleness_budget_minutes(); only the EXCESS
      age beyond the budget contributes variance (within-budget the snapshot is
      as fresh as the station cadence allows). σ_lag = rate · excess_hours; this
      mirrors stale_extreme_uncertainty_margin's deterministic margin, reused
      here as a 1σ scale.

  σ_station_mismatch,j² — provider-vs-settlement-station divergence. From
      config/wu_metar_divergence.json (measured same-station WU-vs-METAR integer
      divergence): we use ``max_abs_raw_delta`` as a conservative 1σ for a source
      that is the SAME physical station but a different provider transform path,
      and 0 for the settlement-faithful primary. Defaults block used when the
      city is absent.

  σ_rounding,j² — settlement rounding quantum. The reported extreme is rounded to
      the settlement grid; the quantization error of a value uniform over a
      quantum of width q has variance q²/12. q is derived from
      settlement_semantics.settlement_preimage_offsets(rounding_rule, half_step):
      the preimage span (high_off − low_off) IS the quantum width. half_step =
      settlement_step / 2 (0.5 for the 1°/grid of all current Zeus markets).

  σ_sensor,j², σ_provider_transform,j² — small DOCUMENTED defaults
      (DEFAULT_SENSOR_SIGMA, DEFAULT_PROVIDER_TRANSFORM_SIGMA). These are the two
      terms with no direct persisted artifact yet; they carry a clear upgrade
      hook (``fitted_cov`` / per-source overrides) so a walk-forward
      residual-covariance fit REPLACES them when available — they are NOT policy
      knobs to be tuned by hand.

CORRELATION HANDLING (the anti-double-count core). Sources are grouped by the
physical station they observe (station_id). Within the SAME (city, metric) the
cross-source covariance is built as a diagonal of σ_eff,j² with an off-diagonal
correlation ρ between any two sources sharing a station, then SHRUNK toward its
diagonal (Ledoit-Wolf-style intensity). The fused precision is the GLS precision
1' Σ⁻¹ 1 (which correctly DISCOUNTS correlated rows), and n_eff =
fused_precision · mean(σ_eff²) — the ratio of the correlation-aware information to
the average single-source information. Same-station sources ⇒ Σ near rank-1 ⇒
n_eff < N; independent stations ⇒ Σ near-diagonal ⇒ n_eff ≈ N.

LEARNED-COVARIANCE UPGRADE HOOK. ``fitted_cov`` accepts an externally-fitted
(p×p) source covariance in native-unit². When supplied it REPLACES the
constructed Σ entirely (the σ-component build is bypassed), so a walk-forward
residual covariance learned offline drives the weights instead of the
config-derived first cut. The σ-component path is the correct, fully-functional
first cut used until that fit exists.

PURE: no network, no DB, no imports of the live fetch lane or the forecast
fusion. Operates only over already-persisted facts handed in as arguments /
read from config via the read-only latency + divergence loaders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional, Sequence

import json
import logging

import numpy as np

from src.contracts.settlement_semantics import settlement_preimage_offsets
from src.signal.day0_obs_latency import staleness_budget_minutes

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIVERGENCE_PATH = _REPO_ROOT / "config" / "wu_metar_divergence.json"

# --- DOCUMENTED defaults for the two terms without a direct persisted artifact ---
# These are the ONLY hand-set scales; both carry the fitted_cov upgrade hook.
# Provenance: conservative small-sensor noise for a quality-controlled ICAO METAR
# sensor (sub-grid sensor + decode noise), well below one settlement quantum.
DEFAULT_SENSOR_SIGMA = 0.25            # native unit; per-source sensor/decode noise 1σ
DEFAULT_PROVIDER_TRANSFORM_SIGMA = 0.10  # native unit; provider unit/round transform 1σ

# Correlation assumed between two sources that resolve the SAME physical station.
# Two providers reading the identical ICAO METAR are very nearly the same draw;
# 0.95 (not 1.0) leaves a sliver of independent provider-transform noise so the
# covariance stays positive-definite and n_eff is slightly above 1, not exactly 1.
SAME_STATION_RHO = 0.95

# Variance floor so a degenerate σ_eff (all terms ~0) cannot blow up precision.
_SIGMA_EFF_FLOOR = 0.05  # native unit; 1σ floor


# ---------------------------------------------------------------------------
# divergence config loader (read-only; fail-soft to defaults)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_divergence(path_str: str = str(_DIVERGENCE_PATH)) -> dict:
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            model = json.load(fh)
        if not isinstance(model, dict) or not isinstance(model.get("cities"), dict):
            raise ValueError("wu_metar_divergence.json malformed: missing 'cities' dict")
        return model
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "WU_METAR_DIVERGENCE_UNAVAILABLE path=%s exc=%s — conservative defaults",
            path_str, exc,
        )
        return {"cities": {}, "defaults": {"F": 1.5, "C": 1.0}}


def station_mismatch_sigma(city: str, unit: str, *, is_settlement_faithful: bool) -> float:
    """1σ provider-vs-settlement-station divergence for a city (native unit).

    A source that IS the settlement-faithful primary contributes 0 mismatch
    (it defines truth). A secondary same-station provider whose transform path
    differs carries the measured ``max_abs_raw_delta`` as a conservative 1σ.
    Unknown city / missing model → unit default. Read-only, fail-soft.
    """
    if is_settlement_faithful:
        return 0.0
    model = _load_divergence()
    entry = model.get("cities", {}).get(str(city)) or {}
    delta = entry.get("max_abs_raw_delta")
    if delta is None:
        defaults = model.get("defaults") or {}
        delta = defaults.get(str(unit).upper(), 1.0)
    try:
        return abs(float(delta))
    except (TypeError, ValueError):
        return 1.0


def rounding_sigma(rounding_rule: str = "wmo_half_up", *, half_step: float = 0.5) -> float:
    """1σ settlement-rounding quantization noise (native unit).

    The reported extreme is rounded to the settlement grid. The preimage span of
    the rounding rule (settlement_preimage_offsets) IS the quantum width q; the
    variance of a value uniform over a quantum of width q is q²/12, so σ = q/√12.
    Provenance: src/contracts/settlement_semantics.settlement_preimage_offsets.
    """
    low_off, high_off = settlement_preimage_offsets(rounding_rule, half_step=half_step)
    quantum = float(high_off) - float(low_off)
    return quantum / math.sqrt(12.0)


# ---------------------------------------------------------------------------
# staleness → σ_lag
# ---------------------------------------------------------------------------
def _coerce_utc(value: object) -> Optional[datetime]:
    """Parse an ISO timestamp / datetime to aware-UTC. None on failure."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        raw = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def lag_sigma(
    *,
    decision_time: object,
    observation_available_at: object,
    plausible_move_rate: float,
    budget_minutes: float,
) -> float:
    """1σ staleness uncertainty for a running extreme (native unit).

    Δt = decision_time − observation_available_at. Only the EXCESS age beyond the
    city's staleness budget (one report interval + publication delay) contributes:
    within budget the snapshot is as fresh as the station cadence allows, so
    σ_lag = 0. Beyond budget the unseen later reports could have moved the true
    extreme by up to rate · excess_hours — used as a 1σ scale. Mirrors
    src/signal/day0_obs_latency.stale_extreme_uncertainty_margin (same rate, same
    excess-age form), reused here as a variance scale.

    Unparseable timestamps → maximally stale within the widening cap (fail-closed:
    a larger σ only DOWN-weights the source, never up-weights it).
    """
    dt_dec = _coerce_utc(decision_time)
    dt_obs = _coerce_utc(observation_available_at)
    if dt_dec is None or dt_obs is None:
        # fail-closed: treat as a full widening window of staleness.
        return float(plausible_move_rate) * _MAX_WIDENING_HOURS
    age_min = (dt_dec - dt_obs).total_seconds() / 60.0
    if age_min < 0.0:
        age_min = 0.0  # obs published after the decision clock; not stale.
    excess_hours = max(0.0, (age_min - float(budget_minutes)) / 60.0)
    return float(plausible_move_rate) * min(excess_hours, _MAX_WIDENING_HOURS)


# Cap mirrors day0_obs_latency._MAX_WIDENING_HOURS: beyond this the source is
# maximally distrusted (σ saturates) rather than growing without bound.
_MAX_WIDENING_HOURS = 6.0


# ---------------------------------------------------------------------------
# typed records
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ObsSourceReading:
    """One source's reading of the day0 running extreme for a (city, metric).

    Metric-agnostic: ``value`` is high_so_far OR low_so_far in the city's native
    settlement unit — the fusion never interprets which. ``source_family`` is the
    canonical family key (e.g. madis_hfmetar, aviationweather_metar, hko_realtime_api).
    ``station_id`` is the physical station the reading resolves; two readings with
    the SAME station_id are treated as correlated (same draw). ``observation_available_at``
    is the UTC publication clock used for staleness. ``sample_count`` is provenance
    only (carried through; not yet used to scale variance).
    """

    value: float
    source_family: str
    station_id: str
    observation_available_at: object  # UTC ISO str | datetime | None
    sample_count: int = 0
    is_settlement_faithful: bool = True
    # Per-source σ-component overrides (the learned-covariance upgrade hook at the
    # single-source granularity). None → module default.
    sensor_sigma: Optional[float] = None
    provider_transform_sigma: Optional[float] = None

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.value)):
            raise ValueError("ObsSourceReading.value must be finite")


@dataclass(frozen=True, slots=True)
class FusedObservation:
    """Precision-weighted, correlation-discounted fused observation.

    ``value``    — fused running extreme (native unit), Σ τ_j x_j / Σ τ_j under the
                   correlation-aware GLS weights (lies within [min, max] source value).
    ``precision``— fused effective precision 1' Σ⁻¹ 1 (native unit⁻²); the inverse is
                   the fused variance.
    ``sigma_eff``— fused 1σ = 1/√precision.
    ``n_eff``    — effective independent-observation count (< N for correlated
                   same-station sources; → N for independent stations). The
                   anti-double-count witness.
    ``per_source_sigma`` — each source's σ_eff,j (native unit), source_family-keyed.
    ``provenance``— method + per-term breakdown + shrink intensity for receipts.
    """

    value: float
    precision: float
    sigma_eff: float
    n_eff: float
    per_source_sigma: Mapping[str, float]
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError("FusedObservation.value must be finite")
        if not (math.isfinite(self.precision) and self.precision > 0.0):
            raise ValueError("FusedObservation.precision must be positive finite")


# ---------------------------------------------------------------------------
# covariance build + Ledoit-Wolf-style shrink-to-diagonal
# ---------------------------------------------------------------------------
def _build_source_covariance(
    sigmas: np.ndarray,
    station_ids: Sequence[str],
    *,
    rho: float = SAME_STATION_RHO,
) -> np.ndarray:
    """Construct the (p×p) cross-source covariance from per-source σ_eff and the
    same-station correlation structure.

    Diagonal = σ_eff,j². Off-diagonal (j,k) = ρ·σ_j·σ_k when sources j and k share
    a non-empty station_id (correlated same-station readings), else 0. Empty
    station_id (""), as for grid-only diagnostic sources, is treated as NOT shared
    (independent) — only an explicit matching physical station induces correlation.
    """
    p = len(sigmas)
    cov = np.diag(sigmas ** 2).astype(float)
    for j in range(p):
        sj = str(station_ids[j] or "")
        for k in range(j + 1, p):
            sk = str(station_ids[k] or "")
            if sj and sk and sj == sk:
                c = rho * sigmas[j] * sigmas[k]
                cov[j, k] = c
                cov[k, j] = c
    return cov


def shrink_to_diagonal(cov: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf-style shrink of a covariance toward its DIAGONAL.

    Independent OBSERVATION-side implementation (does NOT import the forecast
    fusion's shrink_cov — that is the do_not_rebuild T2 layer). Here the input is
    already a STRUCTURED covariance (diagonal σ_eff² + same-station off-diagonal),
    not a sample covariance from a residual matrix, so the shrink intensity is the
    structural off-diagonal mass relative to total mass:

        δ = ||offdiag||²_F / ||cov||²_F  ∈ [0, 1]

    Σ_shrunk = (1−δ)·cov + δ·diag(cov). δ→0 when sources are uncorrelated (cov is
    already diagonal → no shrink needed); δ grows with same-station correlation,
    pulling the off-diagonal DOWN so two same-station readings are not counted as
    two independent draws. Returns (Σ_shrunk, δ). PD-repaired on the diagonal.

    NOTE the inversion of the usual LW idiom: a LARGER off-diagonal mass means the
    correlation is real and STRONG, so we shrink the off-diagonal toward zero MORE
    — i.e. we trust the diagonal (independent) target less, but the FUSED-precision
    consequence is that correlated rows still cancel via Σ⁻¹. The δ here is a
    reported diagnostic of how much same-station structure was present; the
    anti-double-count itself comes from the GLS Σ⁻¹ on the (lightly shrunk)
    full covariance.
    """
    cov = np.asarray(cov, dtype=float)
    p = cov.shape[0]
    if p <= 1:
        return cov, 0.0
    diag = np.diag(np.diag(cov))
    off = cov - diag
    off_mass = float(np.sum(off * off))
    total_mass = float(np.sum(cov * cov))
    delta = 0.0 if total_mass <= 1e-12 else max(0.0, min(1.0, off_mass / total_mass))
    shrunk = (1.0 - delta) * cov + delta * diag
    # PD repair: floor eigenvalues so Σ⁻¹ is well-conditioned.
    d = np.diag(shrunk).copy()
    d = np.maximum(d, _SIGMA_EFF_FLOOR ** 2)
    np.fill_diagonal(shrunk, d)
    w, V = np.linalg.eigh(shrunk)
    w = np.maximum(w, (_SIGMA_EFF_FLOOR ** 2) * 0.25)
    return (V * w) @ V.T, delta


# ---------------------------------------------------------------------------
# per-source σ_eff
# ---------------------------------------------------------------------------
def _source_sigma_eff(
    reading: ObsSourceReading,
    *,
    decision_time: object,
    city_name: str,
    city_unit: str,
    plausible_move_rate: float,
    budget_minutes: float,
    rounding_rule: str,
    half_step: float,
) -> tuple[float, dict[str, float]]:
    """σ_eff,j = sqrt(Σ component variances). Returns (σ_eff, per-term σ breakdown)."""
    s_sensor = (
        float(reading.sensor_sigma)
        if reading.sensor_sigma is not None
        else DEFAULT_SENSOR_SIGMA
    )
    s_provider = (
        float(reading.provider_transform_sigma)
        if reading.provider_transform_sigma is not None
        else DEFAULT_PROVIDER_TRANSFORM_SIGMA
    )
    s_round = rounding_sigma(rounding_rule, half_step=half_step)
    s_lag = lag_sigma(
        decision_time=decision_time,
        observation_available_at=reading.observation_available_at,
        plausible_move_rate=plausible_move_rate,
        budget_minutes=budget_minutes,
    )
    s_mismatch = station_mismatch_sigma(
        city_name, city_unit, is_settlement_faithful=reading.is_settlement_faithful
    )
    var = s_sensor ** 2 + s_round ** 2 + s_lag ** 2 + s_mismatch ** 2 + s_provider ** 2
    sigma_eff = max(math.sqrt(var), _SIGMA_EFF_FLOOR)
    breakdown = {
        "sigma_sensor": s_sensor,
        "sigma_rounding": s_round,
        "sigma_lag": s_lag,
        "sigma_station_mismatch": s_mismatch,
        "sigma_provider_transform": s_provider,
        "sigma_eff": sigma_eff,
    }
    return sigma_eff, breakdown


# ---------------------------------------------------------------------------
# the public fusion
# ---------------------------------------------------------------------------
def fuse_day0_observations(
    sources: Sequence[ObsSourceReading],
    *,
    decision_time: object,
    city_name: str = "",
    city_unit: str = "F",
    plausible_move_rate: Optional[float] = None,
    budget_minutes: Optional[float] = None,
    rounding_rule: str = "wmo_half_up",
    half_step: float = 0.5,
    fitted_cov: Optional[np.ndarray] = None,
) -> FusedObservation:
    """Precision-fuse multiple day0 observation sources of one running extreme.

    Metric-agnostic (works for high_so_far and low_so_far identically). Each
    source is weighted by precision 1/σ_eff,j²; correlated same-station sources are
    fused through a shrink-to-diagonal covariance so they are NOT double-counted.

    Parameters
    ----------
    sources : the per-source readings (≥1). All must be in ``city_unit``.
    decision_time : the UTC decision clock; staleness Δt is measured against each
        source's observation_available_at.
    city_name : used to look up the per-city staleness budget + station-mismatch
        divergence. Empty → defaults.
    city_unit : 'C' or 'F'; selects the default plausible-move-rate scale and the
        divergence default.
    plausible_move_rate : °/h move scale for σ_lag. None → 2.5 (C) / 4.5 (F),
        the day0_obs_latency rates.
    budget_minutes : per-city staleness budget for σ_lag. None → looked up via
        staleness_budget_minutes(city_name).
    rounding_rule, half_step : settlement rounding geometry for σ_rounding.
    fitted_cov : LEARNED-COVARIANCE UPGRADE HOOK. A (p×p) native-unit² source
        covariance fitted offline (walk-forward residual covariance). When given it
        REPLACES the constructed σ-component covariance entirely; the GLS fuse runs
        on it directly. Must match len(sources). The per_source_sigma reported then
        comes from its diagonal.

    Returns
    -------
    FusedObservation : value, precision, sigma_eff, n_eff, per_source_sigma, provenance.

    Single source → returns itself with its own σ_eff (n_eff == 1).
    """
    readings = list(sources)
    if not readings:
        raise ValueError("fuse_day0_observations requires at least one source")

    unit = str(city_unit or "F").upper()
    if plausible_move_rate is None:
        plausible_move_rate = 2.5 if unit == "C" else 4.5
    if budget_minutes is None:
        budget_minutes = staleness_budget_minutes(city_name) if city_name else 100.0

    values = np.array([float(r.value) for r in readings], dtype=float)
    station_ids = [str(r.station_id or "") for r in readings]
    p = len(readings)

    # --- per-source σ_eff (component build) ---
    per_source_sigma: dict[str, float] = {}
    breakdowns: list[dict[str, float]] = []
    sigmas = np.empty(p, dtype=float)
    for j, r in enumerate(readings):
        s_eff, bd = _source_sigma_eff(
            r,
            decision_time=decision_time,
            city_name=city_name,
            city_unit=unit,
            plausible_move_rate=float(plausible_move_rate),
            budget_minutes=float(budget_minutes),
            rounding_rule=rounding_rule,
            half_step=half_step,
        )
        sigmas[j] = s_eff
        breakdowns.append(bd)
        # source_family-keyed; disambiguate same-family duplicates with an index.
        key = r.source_family if r.source_family not in per_source_sigma else f"{r.source_family}#{j}"
        per_source_sigma[key] = s_eff

    # --- covariance: fitted (upgrade hook) OR constructed σ-component + shrink ---
    if fitted_cov is not None:
        cov = np.asarray(fitted_cov, dtype=float)
        if cov.shape != (p, p):
            raise ValueError(
                f"fitted_cov shape {cov.shape} != ({p}, {p}) (one row/col per source)"
            )
        # PD-repair the supplied matrix; recompute per-source σ from its diagonal.
        d = np.maximum(np.diag(cov).copy(), _SIGMA_EFF_FLOOR ** 2)
        np.fill_diagonal(cov, d)
        sigmas = np.sqrt(np.diag(cov))
        for j, r in enumerate(readings):
            key = r.source_family if list(per_source_sigma).count(r.source_family) == 1 else f"{r.source_family}#{j}"
            per_source_sigma[key] = float(sigmas[j])
        delta = float("nan")  # not a structural shrink; fitted cov used as-is.
        method = "FITTED_COV_GLS"
        Sigma = cov
    else:
        raw_cov = _build_source_covariance(sigmas, station_ids)
        Sigma, delta = shrink_to_diagonal(raw_cov)
        method = "SIGMA_COMPONENT_SHRINK_GLS"

    # --- GLS precision-weighted fuse: w ∝ Σ⁻¹ 1 ; value = wᵀx / 1ᵀΣ⁻¹1 ---
    try:
        Sinv = np.linalg.inv(Sigma)
    except np.linalg.LinAlgError:
        Sinv = np.linalg.pinv(Sigma)
    ones = np.ones(p)
    fused_precision = float(ones @ Sinv @ ones)
    if not (math.isfinite(fused_precision) and fused_precision > 0.0):
        # degenerate Σ — fall back to the most-precise single source.
        best = int(np.argmin(sigmas))
        fused_precision = 1.0 / (sigmas[best] ** 2)
        fused_value = float(values[best])
        method += "_DEGENERATE_FALLBACK"
    else:
        w = Sinv @ ones
        fused_value = float((w @ values) / fused_precision)

    fused_sigma = 1.0 / math.sqrt(fused_precision)

    # --- n_eff: correlation-aware information ÷ average single-source information ---
    # mean single-source precision = mean(1/σ_eff²). n_eff = fused_precision / mean_single_prec.
    # Independent stations: fused_precision = Σ τ_j → n_eff = N. Perfectly correlated
    # same-station: fused_precision ≈ τ → n_eff ≈ 1. The anti-double-count witness.
    mean_single_precision = float(np.mean(1.0 / (sigmas ** 2)))
    n_eff = fused_precision / mean_single_precision if mean_single_precision > 0 else float(p)
    # clamp to the physically-meaningful [≈1, p] band (numerical guard only).
    n_eff = float(min(max(n_eff, 0.0), float(p)))

    # convexity guarantee: a precision-weighted mean of correlated/independent
    # readings must lie within the source value envelope.
    vmin, vmax = float(values.min()), float(values.max())
    fused_value = float(min(max(fused_value, vmin), vmax))

    provenance = {
        "method": method,
        "n_sources": p,
        "shrink_delta": delta,
        "plausible_move_rate": float(plausible_move_rate),
        "budget_minutes": float(budget_minutes),
        "rounding_rule": rounding_rule,
        "half_step": half_step,
        "city_unit": unit,
        "station_ids": tuple(station_ids),
        "source_families": tuple(r.source_family for r in readings),
        "per_source_breakdown": tuple(breakdowns),
        "value_envelope": (vmin, vmax),
        "fitted_cov_used": fitted_cov is not None,
    }

    return FusedObservation(
        value=fused_value,
        precision=fused_precision,
        sigma_eff=fused_sigma,
        n_eff=n_eff,
        per_source_sigma=per_source_sigma,
        provenance=provenance,
    )
