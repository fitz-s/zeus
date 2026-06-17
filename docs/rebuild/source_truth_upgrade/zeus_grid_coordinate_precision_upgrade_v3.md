# Zeus Grid/Station Precision Upgrade v3

> Operator authority spec, 2026-06-17. Source of truth for the v3 grid-coordinate precision engine
> (CoordinatePrecisionGuard + native-grid interpolation + station representativeness variance into Σ).
> Paired with zeus_source_access_validation_v3.xlsx (GridCorrectionMath / CityBestSources sheets).

## Problem

The uploaded overlay stores every city `lat`/`lon` with exactly two decimals. This is not acceptable for station-settlement trading.

- 0.01° latitude ≈ 1.1132 km.
- Longitude error ≈ 111.32 km × cos(latitude) × 0.01.
- For 2 km regional grids or sharp coastal/orographic gradients, a two-decimal coordinate can select the wrong model cell or distort interpolation weights.

## Non-negotiable storage rule

1. Station identity coordinates are strings/Decimal in config and provenance.
2. Never format lat/lon with numeric display formats that truncate precision.
3. Numeric float conversion happens only inside interpolation, never as persisted identity.
4. Any station coordinate with fewer than 4 decimal places fails `CoordinatePrecisionGuard`.
5. For exact airport settlement, station lat/lon must come from `config/cities.json` or the active market rule/source, not from a rounded overlay CSV.

## Native grid interpolation

### Rectilinear lat/lon grid

Let station coordinate be `(φ, λ)`. Four surrounding grid points: SW `(φ0, λ0)`, SE `(φ0, λ1)`, NW `(φ1, λ0)`, NE `(φ1, λ1)`.

```text
u = (λ - λ0) / (λ1 - λ0)
v = (φ - φ0) / (φ1 - φ0)
w_SW = (1-u)(1-v);  w_SE = u(1-v);  w_NW = (1-u)v;  w_NE = uv
T_interp = Σ_i w_i T_i
z_interp = Σ_i w_i z_i
d_eff = sqrt(Σ_i w_i d_i^2)     # d_i = haversine(station, grid_i)
```

### Irregular/rotated/native model grid

Use the model's native projection, locate the containing triangle, barycentric interpolate:
`T_interp = w1*T1 + w2*T2 + w3*T3`, `w1+w2+w3=1`, `w_i >= 0`. Persist the three grid ids + weights.

## Elevation and station representativeness correction

```text
x_station = T_interp
          + β_alt(city,season,metric,lead) * (z_station - z_interp)
          + b_grid(city,season,metric,lead,source_model)
```

`β_alt` and `b_grid` fitted WALK-FORWARD only. No hardcoded lapse-rate shift in live q.

```text
σ_repr² = g(d_eff, |z_station-z_interp|, coastal_regime, orography_regime, urban_regime)
Σ_source = Σ_model_residual + σ_repr²       # covariance diagonal, NOT a hand-tuned weight
```

## Bayes integration (do NOT replace the existing T2 fusion)

```text
z_s = x_station_s - b_hat_s
b_hat_s = λ * rbar_s + (1-λ) * parent_bias_s
V* = (τ0^-2 + 1'Σ^-1 1)^-1
μ* = V* (τ0^-2 μ0 + 1'Σ^-1 z)
```

The grid/station improvement enters as: (1) better `x_station_s`; (2) extra `σ_repr²` in `Σ`; (3) provenance fields proving coordinate precision + interpolation weights.

## Day0 observation conditioning

Observed extremes dominate the grid prior as a physical boundary:

```text
HIGH sample = settle(max(H_obs_so_far, H_future_member))
LOW sample  = settle(min(L_obs_so_far, L_future_member))
```

Grid forecasts only describe the remaining local-day distribution after the observation timestamp.
[ZEUS STATUS 2026-06-17: this Day0 boundary is already LIVE — HIGH was correct; the LOW preimage was
fixed from a current_temp convex-blend to settle(min(L_obs, future_member_min)) in
src/signal/day0_low_distribution.py (committed a72d98e11c, worktree claude/day0-physical-law).]
