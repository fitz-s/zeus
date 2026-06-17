<!--
Created: 2026-06-17
Last reused or audited: 2026-06-17
Authority basis: operator spec zeus_source_access_validation_v3.xlsx GridCorrectionMath
  (rules 1-5) + the operator's build message. Build-only; NO live-fusion wiring.
-->
# v3 grid-coordinate precision engine — implementation report

Replaces nearest-rounded-coordinate grid lookup with native-grid **interpolation** +
**station-representativeness variance** that feeds the existing Bayes precision fusion's
`Σ`. Build-only: new modules + RED-on-revert tests, no wiring into the live fusion, no
auto-deploy. `src/forecast/bayes_precision_fusion.py` is **unmodified** (verified clean
in git status).

## Modules + APIs (all under `src/forecast/`)

### `coordinate_precision_guard.py` (rule 1)
- `CoordinatePrecisionGuard(coord_text) -> CoordPrecisionVerdict` — PASS iff the
  **written** form has ≥ 4 decimal places. `count_decimals` counts digits after `.` in
  the STRING (rejects exponent form / junk), never a float round-trip.
- `guard_pair(lat_text, lon_text)` — a pair PASSES iff BOTH coords pass.
- `load_city_coordinates(config/cities.json) -> list[CityCoordRecord]` — reads cities.json
  (the source of truth) as **text**, extracts each city's `lat`/`lon` by their written form
  (a `json.load` would coerce to float and destroy precision), and flags sub-4-decimal
  cities `REQUIRES_PRECISE_RESTORE` with the exact operator action
  `RESTORE_FROM_CONFIG_CITIES_JSON_OR_SOURCE_OF_TRUTH_AS_TEXT_DECIMAL; DO_NOT_ROUND`.
  It **never fabricates** a more-precise coordinate; it only diagnoses.

### `grid_interpolation.py` (rules 2, 3)
- `haversine_m(lat1,lon1,lat2,lon2)` — great-circle metres, R=6371000 (operator formula).
- `bilinear_interpolate(station, SW,SE,NW,NE) -> GridInterpolation` — rectilinear:
  `u,v` from cell bounds; `w_SW=(1-u)(1-v) … w_NE=uv`; `T_interp/z_interp = Σ wᵢ·{value,elev}ᵢ`;
  `d_eff = sqrt(Σ wᵢ dᵢ²)`.
- `barycentric_interpolate(station_proj, n1,n2,n3) -> GridInterpolation` — irregular/rotated
  (ICON): solves barycentric weights in the **native projection plane** (no lat/lon snap),
  `T_interp = w1T1+w2T2+w3T3`, `Σwᵢ=1`; **refuses extrapolation** outside the containing
  triangle (negative weight → `ValueError`); `d_eff` still haversine-RSS over the 3 nodes.
- Each call returns `(T_interp, z_interp, d_eff_m, grid_ids, weights, geometry, provenance)`;
  `provenance` is a fully persistable dict (geometry, node ids/coords/weights, per-node dᵢ, d_eff).

### `representativeness_variance.py` (rules 4, 5)
- `station_correction(T_interp, z_station, z_interp, shift) -> x_station` (rule 4):
  `x_station = T_interp + β_alt·(z_station - z_interp) + b_grid`.
- `representativeness_variance(d_eff_m, dz_m, regime…, fit) -> σ_repr²` (rule 5):
  `g = (a0 + a_d·(d_eff/1000)² + a_z·dz²)·regime_mult`, all coefficients ≥ 0 → widen-only.
- `sigma_with_representativeness(σ_model_resid², σ_repr²) -> Σ_source` — the additive
  diagonal entry `Σ_source = Σ_model_residual + σ_repr²` the fusion consumes.

## Text-decimal precision enforcement
Precision is the count of decimal digits in the coordinate's **written string**, not the
precision of a parsed float (`39.12` → 2; `39.1234` → 4; `39.12000` → 5). A float-based
check would silently PASS a truncated coordinate after a round-trip — the exact defect the
guard exists to catch. Coordinates are stored/compared as text-decimal throughout, and
exponent/junk literals are rejected (`ValueError`) so a malformed coord can never be
mis-scored as zero-decimal.

## Bilinear + barycentric + d_eff (verified by tests)
- Bilinear weights sum to 1 at every tested `(u,v)`; recover a node's value exactly at that
  node; interpolate linearly along an edge and to the 4-corner mean at the cell centre.
- Barycentric weights sum to 1; recover a vertex value at the vertex; give the 3-node mean
  at the centroid; INSIDE/OUTSIDE classification correct; extrapolation refused.
- `d_eff` is 0 when the station sits on a node (weight 1, dᵢ=0) and strictly increases as the
  station moves into the cell interior — monotone in station offset.

## σ_repr → Σ mechanism (proof it is variance, not a hand weight)
`σ_repr²` enters ONLY as an **additive term on the source's Σ diagonal**
(`Σ_source = Σ_model_residual + σ_repr²`). The end-to-end test feeds that inflated diagonal
into the **live, unmodified** `bayes_fuse` / `fuse_bayes_precision_posterior`
(`V*=(τ0⁻²+1'Σ⁻¹1)⁻¹; μ*=V*(τ0⁻²μ0+1'Σ⁻¹z)`): adding a large `σ_repr²` (15 km d_eff, 400 m
Δz, orography regime) to instrument 0's diagonal pulls `μ*` back toward the anchor prior and
strictly reduces that instrument's influence — purely through the fusion's own `Σ⁻¹`. No
weight is multiplied anywhere; only the diagonal changes. The same directional result holds
through the production `ModelInstrument` path (the wider source's `train_residuals` widen its
`diag_cov` entry). This is the operator's "NEVER a hand down/up-weight" guarantee, demonstrated.

## β_alt / b_grid + σ_repr walk-forward fit interface + cold-start
- `fit_station_shift(rows, min_train=25) -> StationShiftFit` — least-squares fit of
  `settlement_residual = β_alt·dz + b_grid` per stratum (city,season,metric,lead[,source_model]),
  strictly walk-forward (caller selects rows before the target date). Recovers a planted
  slope to 1e-6. Below `min_train`, or with no elevation spread, returns the **inert cold-start**
  `β_alt=0, b_grid=0` (`COLD_START_STATION_SHIFT`) → `station_correction` returns `T_interp`
  with **no live lapse shift**. The live shift may come ONLY from a real fit; no lapse rate is
  hardcoded as a live shift anywhere.
- `fit_representativeness_variance(rows, min_train=25) -> ReprVarianceFit` — least-squares fit
  of `E[residual²] = a0 + a_d·(d_eff/1000)² + a_z·dz²`, coefficients clamped ≥ 0 (widen-only);
  unidentifiable (constant) feature columns are dropped (identifiability, not a workaround) so a
  distance-only stratum still recovers `a_d`. Below `min_train` returns the conservative
  cold-start `COLD_START_REPR_VARIANCE` (`fitted=False`).

## Cities needing precise-coord restore
**38 of 54** cities in `config/cities.json` are < 4 decimal places on lat OR lon and are flagged
`REQUIRES_PRECISE_RESTORE` (matches the operator CityBestSources audit). 16 pass (≥ 4 decimals
on both). Restoration is an operator / source-of-truth step; this engine diagnoses and never
invents digits.

## What the live-fusion wiring step would require next (NOT done here)
1. **Precise-coord restore first** — restore the 38 flagged cities to ≥ 4-decimal text coords in
   the source of truth before any live interpolation (a truncated station coord poisons the 4
   surrounding-node selection and d_eff). The guard should gate the live path.
2. **Native-grid node provider** — a reader that, given a precise station coord + a source model,
   returns the 4 surrounding rectilinear nodes (or the containing triangle + native projection for
   ICON), with grid ids, values, elevations. This is the only new I/O; keep it out of these pure
   modules.
3. **Walk-forward fit jobs** — run `fit_station_shift` / `fit_representativeness_variance` over
   settled residuals per stratum, persist the fitted `StationShiftFit` / `ReprVarianceFit`, and
   serve them to the live path (cold-start only until a stratum has ≥ `min_train` settled rows).
4. **Σ-diagonal wiring in the materializer** — at the call site that builds each source's Σ entry,
   replace the model-residual variance with `sigma_with_representativeness(σ_model_resid², σ_repr²)`
   and replace the nearest-cell `T` with `station_correction(T_interp, …)`. `bayes_precision_fusion.py`
   itself stays untouched — the change is in the Σ assembly upstream of `fuse_bayes_precision_posterior`.
5. **Provenance persistence** — persist the returned `provenance` dicts (grid ids + weights + d_eff)
   per forecast so a live receipt can reconstruct the interpolation.

## Tests (all green: `36 passed`)
`tests/test_coordinate_precision_guard.py`, `tests/test_grid_interpolation.py`,
`tests/test_representativeness_variance.py`. Existing fusion port-fidelity + thin-anchor tests
re-run clean (no regression).
