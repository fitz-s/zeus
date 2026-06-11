# Bucket Transport (rung 3) — O1280 Grid Mapping + Bucket↔API Delta Measurement

**Date:** 2026-06-11
**Authority:** operator directive 2026-06-11 (~07:10Z) — third anchor transport (direct S3
om-file read from open-meteo's data_spatial bucket), CITY-WHITELIST gate requirement.
**Module:** `src/data/openmeteo_ecmwf_ifs9_bucket_transport.py`

## Bucket layout (verified live)

- `https://openmeteo.s3.amazonaws.com/data_spatial/ecmwf_ifs/in-progress.json` →
  `{reference_time: 2026-06-11T00:00:00Z, completed: false, valid_times: [90 hourly steps
  through 2026-06-14T17Z], variables: [...], last_modified_time, crs_wkt: Reduced Gaussian
  Grid O1280}`.
- `latest.json` → completed run `2026-06-10T06:00:00Z` (109 valid_times).
- Per-run per-step files:
  `data_spatial/ecmwf_ifs/<YYYY>/<MM>/<DD>/<HHHH>Z/<YYYY-MM-DDTHHMM>.om`
  (one ~110–132 MB om file PER timestep; the 00Z run dir held 90 `.om` files).

## om-file format + grid

- Reader: `omfiles==1.2.0` (PyPI, official open-meteo bindings; arm64 wheel) + `fsspec` +
  `s3fs` for cloud-native partial reads (`blockcache::s3://...`, `s3={"anon": True}`).
- Each `.om` file is a hierarchical group of 41 children. `temperature_2m` is a
  `float32` array of shape **`(1, 6599680)`** — the flat ECMWF **octahedral reduced
  Gaussian O1280** grid (NOT regridded to a regular lat/lon grid). Scalar metadata:
  `crs_wkt` (= "Reduced Gaussian Grid O1280"), `coordinates="lat lon"`,
  `forecast_reference_time`, `valid_time`. There is **no in-file lat/lon array** — the flat
  index maps to the grid via the published O1280 definition.
- Grid mapping (`map_lat_lon_to_o1280_index`): Gaussian latitudes = `arcsin(roots of
  Legendre P_2560)` (`numpy.polynomial.legendre.leggauss(2560)`), scanned **north→south**;
  per-row longitude count `nlon(j) = 20 + 4*j` (j = 0-based distance from nearest pole),
  each row west→east from 0°E. **Σ nlon = 6,599,680 exactly = the file's array length**
  (verified). Nearest-neighbour: nearest Gaussian latitude row, then nearest longitude
  bucket.

## Measurement: bucket read vs single-runs API (completed run 2026-06-10T06Z)

Local-day high/low for target local date 2026-06-11, full 24h window per city, bucket
nearest-grid-point read vs API single-runs (`run=2026-06-10T06:00`, same city coordinate,
same timezone). Tolerance bar = **0.05C**.

| City         | metric | bucket | api  | delta  | verdict |
|--------------|--------|--------|------|--------|---------|
| Los Angeles  | high   | 24.50  | 24.5 | +0.00  | CLEAN |
| Los Angeles  | low    | 18.40  | 18.4 | +0.00  | CLEAN |
| Madrid       | high   | 32.50  | 32.5 | +0.00  | CLEAN |
| Madrid       | low    | 15.95  | 15.9 | +0.05  | CLEAN |
| Mexico City  | high   | 23.70  | 23.7 | +0.00  | CLEAN |
| Mexico City  | low    | 14.45  | 14.4 | +0.05  | CLEAN |
| Denver       | high   | 24.20  | 24.2 | +0.00  | CLEAN |
| Denver       | low    | 12.20  | 12.2 | +0.00  | CLEAN |
| London       | high   | 15.65  | 15.6 | +0.05  | CLEAN |
| London       | low    |  9.60  |  9.6 | +0.00  | CLEAN |
| Atlanta      | high   | 31.85  | 31.9 | −0.05  | CLEAN |
| Atlanta      | low    | 21.40  | 21.4 | +0.00  | CLEAN |
| Sao Paulo    | high   | 21.45  | 21.5 | −0.05  | CLEAN |
| Sao Paulo    | low    | 15.25  | 15.2 | +0.05  | CLEAN |
| **Chongqing**| high   | 25.45  | 24.5 | **+0.95** | FAIL |
| **Chongqing**| low    | 20.35  | 19.5 | **+0.85** | FAIL |
| **Tokyo**    | high   | 20.60  | 22.8 | **−2.20** | FAIL |
| **Tokyo**    | low    | 17.75  | 16.3 | **+1.45** | FAIL |
| **Singapore**| high   | 29.70  | 30.5 | **−0.80** | FAIL |
| **Singapore**| low    | 27.95  | 25.6 | **+2.35** | FAIL |
| **Cape Town**| high   | 17.15  | 16.6 | **+0.55** | FAIL |
| **Cape Town**| low    |  8.60  |  8.9 | **−0.30** | FAIL |

**N=22 deltas, max|d|=2.35C, mean|d|=0.44C. 7/11 cities CLEAN (≤0.05C), 4/11 FAIL (>0.5C).**

## Verdict: CITY-WHITELIST-PENDING (not a clean drop-in)

The flat-inland cities (Los Angeles, Madrid, Mexico City, Denver, London, Atlanta, Sao
Paulo) match the API to ≤0.05C — the raw 9km grid read IS the API value there (the residual
is just API rounding to 0.1C; bucket has 2-decimal precision). But **coastal /
complex-terrain cities diverge badly** (Tokyo −2.2C, Singapore +2.35C, Chongqing +0.95C,
Cape Town +0.55C). Note elevation alone is NOT the cause — Denver (1600m) and Mexico City
(2200m) are clean; the failures are coastal (Tokyo/Singapore/Cape Town: nearest O1280 point
lands on/near sea, SST-influenced or wrong land-sea-mask) and basin-terrain (Chongqing).
This is exactly the elevation/lapse-rate + land-sea-mask downscaling that open-meteo's point
API applies and a raw grid read does not — the directive predicted it.

**Structural decision (K-decision, antibody-first):** the bucket transport is
CITY-WHITELISTED, not abandoned. It serves a city ONLY after that city's bucket↔API
cross-check is VERIFIED (≤0.05C) — sourced live from `state/anchor_cross_check.json` at call
time, EMPTY until the first VERIFIED receipt lands. Coastal/terrain cities fall through to
rungs 1-2 unchanged. Biased anchors are impossible by construction, not patched per-city.

## Cross-check antibody

`src/data/anchor_cross_check.run_bucket_anchor_cross_check_cycle` — once single-runs serves
the bucket run, compares the stored bucket series vs the run-pinned API series per city
(reusing `compare_hourly_series`). VERIFIED ⇒ city joins the whitelist; MISMATCH ⇒ ERROR +
receipt (`<cycle>::bucket` key). This both AUDITS already-written bucket artifacts and
POPULATES the whitelist that gates future bucket serves.
