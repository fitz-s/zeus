# Multi-Source Observation Precision Fusion — Implementation

Created: 2026-06-17
Authority basis: operator directive "处理多源数据整合" (multi-source data integration).
do_not_rebuild manifest honored: the live METAR/WU fetch lane
(`src/data/day0_fast_obs.py`, `observation_client.py`, `daily_obs_append.py`) and the
T2 FORECAST fusion (`src/forecast/bayes_precision_fusion.py`) are READ-ONLY references —
this module imports neither's fusion math and modifies none of them.

## Files

- `src/forecast/observation_precision_fusion.py` (new) — the fusion layer.
- `tests/test_observation_precision_fusion.py` (new) — 18 RED-on-revert tests.

Verified: `import src.forecast.observation_precision_fusion` OK; `pytest -q` → **18 passed**.

## What this is

The OBSERVATION analogue of the forecast fusion, a DISTINCT layer. The forecast fusion
combines model PREDICTIONS against a prior; this combines realized STATION OBSERVATIONS
of a quantity that has already happened (the day0 running extreme), so there is no prior —
it is a pure precision-weighted GLS combine with a correlation-aware effective-information
correction. Metric-agnostic: identical path for `high_so_far` and `low_so_far`.

## API

```python
fuse_day0_observations(
    sources: Sequence[ObsSourceReading],
    *, decision_time, city_name="", city_unit="F",
    plausible_move_rate=None, budget_minutes=None,
    rounding_rule="wmo_half_up", half_step=0.5,
    fitted_cov=None,                     # learned-covariance upgrade hook
) -> FusedObservation(value, precision, sigma_eff, n_eff, per_source_sigma, provenance)
```

`ObsSourceReading(value, source_family, station_id, observation_available_at,
sample_count=0, is_settlement_faithful=True, sensor_sigma=None,
provider_transform_sigma=None)`. `value` is the running extreme in the city's native
settlement unit. `station_id` drives correlation: two readings sharing a non-empty
`station_id` are treated as correlated (the same physical draw). Per-city source routing
(which families to fuse) is taken as the `sources` list — the overlay
`CityRouting.day0_primary_families` supplies it upstream; the fusion takes it as a param
and never re-derives settlement authority.

## σ-term provenance (every term from a persisted fact, not an invented constant)

```
σ_eff,j² = σ_sensor² + σ_rounding² + σ_lag²(Δt) + σ_station_mismatch² + σ_provider_transform²
τ_j = 1/σ_eff,j²
```

- **σ_lag²(Δt)** — `Δt = decision_time − observation_available_at`. Scale = the SAME plausible
  move rate the day0 stale-extreme guard uses (`day0_obs_latency._MAX_MOVE_PER_HOUR`:
  2.5 °C/h, 4.5 °F/h). Only EXCESS age beyond the per-city staleness budget
  (`config/wu_obs_latency.json` via `staleness_budget_minutes()`) contributes; within budget
  σ_lag = 0. `σ_lag = rate · min(excess_hours, 6h)` — mirrors
  `stale_extreme_uncertainty_margin`, reused as a 1σ scale.
- **σ_station_mismatch²** — `config/wu_metar_divergence.json` `max_abs_raw_delta` (measured
  same-station WU-vs-METAR divergence) for a non-faithful secondary provider; 0 for the
  settlement-faithful primary; unit default when the city is absent.
- **σ_rounding²** — settlement quantum via
  `settlement_semantics.settlement_preimage_offsets(rule, half_step)`. The preimage span IS
  the quantum width q; quantization variance of a uniform over q is q²/12 → σ = q/√12.
- **σ_sensor², σ_provider_transform²** — the only DOCUMENTED defaults (0.25, 0.10 native unit),
  the two terms with no direct persisted artifact yet; both carry the fitted-cov / per-source
  override upgrade hook so a walk-forward residual fit REPLACES them — not hand-tuned policy.

## Covariance-shrink approach (anti-double-count core)

Per-source σ_eff build a structured cross-source covariance Σ: diagonal = σ_eff,j²;
off-diagonal (j,k) = ρ·σ_j·σ_k (ρ = 0.95) when j,k share a physical station, else 0. Σ is
then SHRUNK toward its diagonal by an independent Ledoit-Wolf-style intensity
δ = ‖offdiag‖²_F / ‖Σ‖²_F (own implementation; does NOT import the forecast `shrink_cov`).
The fuse is GLS: weights w ∝ Σ⁻¹·1, fused value = wᵀx / (1ᵀΣ⁻¹1), fused precision = 1ᵀΣ⁻¹1.
The Σ⁻¹ on the same-station covariance is what cancels the correlated rows — two readings of
one station do NOT add their precisions independently. PD-repaired diagonal; `pinv` on
singular Σ (mirrors the proven `bayes_fuse`).

## n_eff anti-double-count result

`n_eff = fused_precision / mean(1/σ_eff,j²)` — correlation-aware information ÷ average
single-source information. Measured on identical inputs (NYC, F-unit, 2 sources, fresh):

- same station (KLGA + KLGA): **n_eff = 1.38** (shrink_delta 0.42, fused σ_eff 0.389)
- independent (KLGA + KORD):  **n_eff = 2.00** (shrink_delta 0.00, fused σ_eff 0.279)

Independent carries strictly more information (gap 0.62) and tightens the estimate more —
exactly the double-count Σ⁻¹ prevents. Single source → n_eff = 1.0, returns itself.

## Learned-covariance upgrade hook

`fitted_cov` accepts an externally-fitted (p×p) native-unit² source covariance (walk-forward
residual covariance fitted offline). When supplied it REPLACES the constructed σ-component Σ
entirely; the GLS fuse runs on it directly and per-source σ is recomputed from its diagonal
(`method="FITTED_COV_GLS"`). Per-source `sensor_sigma` / `provider_transform_sigma` overrides
give the same upgrade at single-source granularity. Until a fit exists, the config-derived
σ-component path is the correct, fully-functional first cut.

## Test coverage (18 tests, all real assertions)

same-station n_eff<2 / independent n_eff≈2 / independent>same; staler source → lower τ →
less weight + larger σ_eff; σ_lag 0-within-budget then linear; convexity (fused ∈ [min,max]);
single-source identity; F (4.5/h) vs C (2.5/h) move-rate scale + default selection by unit;
σ_rounding = q/√12; faithful=0 vs measured-divergence secondary; unknown-city unit default;
shrink δ=0 diagonal / δ>0 same-station; fitted_cov replaces build + shape-mismatch raises;
empty-sources + non-finite-value guards.
