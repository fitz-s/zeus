# Bucket Transport (rung 3) — Point Downscaling for API Parity across ALL cities

**Date:** 2026-06-11
**Authority:** operator directive 2026-06-11 — "能30小时拿不到数据，就相当于根本没拿过": every
target city must be servable from the fast 0.1° bucket transport; the 15-city quarantine
(coastal/terrain cities that the raw nearest-gridpoint read could not match) recreated the
30-hour data-starvation class daily. This doc closes that quarantine by replicating
Open-Meteo's point downscaling instead of reading the raw nearest gridpoint.
**Module:** `src/data/openmeteo_ecmwf_ifs9_bucket_transport.py` (downscaling section — the raw
read path is UNCHANGED), `src/data/anchor_cross_check.py`,
`scripts/download_replacement_forecast_current_targets.py`.
**Supersedes the verdict of:** `docs/evidence/anchor_channels/2026-06-11_bucket_vs_api_grid_validation.md`
(which measured the raw read and recommended city-whitelist-pending; the cause it predicted —
"elevation/lapse-rate + land-sea-mask downscaling that open-meteo's point API applies and a raw
grid read does not" — is now measured exactly and replicated).

## Result (headline)

| Class | count | cities |
|-------|-------|--------|
| Served via **raw** nearest-gridpoint read (≤0.1°C) | **38** | flat / inland cities |
| Served via **downscaling** (raw failed, downscaled ≤0.1°C) | **15** | the entire quarantine |
| **Total bucket-servable at ≤0.1°C parity** | **53 / 54** | — |
| Structurally not bucket-servable (any path) | **1** | Lucknow (UTC+5:30) — see below |

The 15 cities newly admitted via downscaling are EXACTLY the coastal/terrain quarantine the
raw read could not serve. Worst raw deltas now match the API: San Francisco 9.75°C→0.05°C,
Tokyo 3.65°C→0.03°C, Seoul 2.85°C→0.05°C, Singapore 2.35°C→0.05°C, Manila 2.15°C→0.04°C,
Auckland 2.05°C→0.04°C.

## Algorithm provenance (open-meteo source, read 2026-06-11)

Open-Meteo's `/v1/forecast` does NOT return the raw nearest O1280 gridpoint for a (lat,lon).
For `temperature_2m` it applies the default **`cell_selection=land`** terrain-optimised cell
selection followed by **statistical-downscaling elevation correction**. Verbatim from
`github.com/open-meteo/open-meteo`:

1. **Grid geometry** — `Sources/App/Domains/GaussianGrid.swift` `getCoordinates` / `findPointXY`
   / `getSurroundingGridpoints`. The O1280 octahedral grid is indexed with an **equidistant
   latitude approximation** `dy = 180/(2·1280 + 0.5)`, row-y latitude `(1280−y−1)·dy + dy/2`,
   longitude `x·360/nx(y)`, `nx(y)=20+4·y` (north cap), flat start `integral(y)=2y²+18y`. This is
   NOT the exact Legendre-root Gaussian latitude; the static `HSURF.om` and `temperature_2m`
   flat arrays are indexed by THIS scheme, so the downscaled path uses it (`om_get_coordinates`,
   `om_get_surrounding_gridpoints`).
2. **Cell selection (`cell_selection=land`)** — `GaussianGrid.swift` `findPointTerrainOptimised`:
   build the 3×3 box; if `|centerElev − target| ≤ 100 m` use the center cell **and treat its
   effective elevation as the target** (so the correction is exactly zero — verbatim
   `return (centerPoint, .elevation(elevation))` where `elevation` is the target parameter, NOT
   `centerElevation`); else over the land neighbors minimise `|elev−target| + distanceKm·30`
   with `distanceKm < 50`, falling back to the center cell if the best is sea or `minDelta > 1500`.
3. **Sea sentinel** — `Gridable.swift` `readElevation`: `elevation ≤ -999` ⇒ sea grid point.
4. **Elevation correction** — `Sources/App/Helper/Reader/GenericReader.swift` `scale()`:
   `data[i] += (modelElevation − targetElevation) * 0.0065`. Lapse rate **0.0065 K/m**; sign:
   target higher than model ⇒ cooler. No-op on sea / NaN cells.
5. **Target elevation** — the requested point's 90 m Copernicus-DEM elevation, echoed by the API
   as the `elevation` field (open-meteo.com/en/docs: "The elevation from a 90 meter digital
   elevation model. This affects which grid-cell is selected … Statistical downscaling is used to
   adapt weather conditions for this elevation."). `cities_by_name` has NO elevation field, so the
   API-reported elevation IS the target-elevation authority (captured once per city with
   provenance, `state/anchor_city_elevation.json`, `authority: openmeteo_90m_dem_api_reported`).

**Acid test (cell-selection match):** the terrain-optimised search reproduces the API's chosen
grid cell EXACTLY for the worst raw-failures — Tokyo `(35.60633,139.74293)`, Singapore
`(1.37083,103.94466)`, San Francisco `(37.57469,−122.40001)`, Cape Town `(−33.91916,18.62843)`,
Chongqing `(29.77153,106.62021)` — all matched the API's reported grid lat/lon to <0.01°.

## Static field provenance

`s3://openmeteo/data/ecmwf_ifs/static/HSURF.om` (model surface elevation; the `data/` prefix is
Open-Meteo's OWN regridded storage the API serves from — NOT `data_spatial/`). Shape `(1, 6599680)`
float32, INDEX-COMPATIBLE with the `data_spatial` `temperature_2m` flat array (same octahedral
O1280 indexing). 2.48 MB compressed; cached one-time under `state/static/` (gitignored). `-999.0`
marks sea points (the land-sea mask is encoded in HSURF itself; no separate mask file exists in
the bucket — the static prefix holds only `HSURF.om`, `meta.json`, `soil_type.om`).

## Measurement: downscaled bucket read vs run-pinned single-runs API

Ground truth = the run-pinned **single-runs** API (`single-runs-api.open-meteo.com`,
`models=ecmwf_ifs`, `run=2026-06-10T06:00`) — the SAME endpoint and run the bucket↔API cross-check
uses. Per city: full 24h local-day window for target local date 2026-06-11, hourly
`temperature_2m`, computed via the SHIPPED production functions
(`select_terrain_optimised_point` + `apply_elevation_correction`). Tolerance bar = **0.1°C**
(`BUCKET_VS_API_TOLERANCE_C`, one API quantum — never weakened). N = 54 cities, 0 errors.

### Cities NEWLY ADMITTED via downscaling (raw read failed; downscaled ≤0.1°C)

| City | target elev (m) | raw max\|Δ\|°C | **downscaled max\|Δ\|°C** | model cell elev (m) | correction °C | cell |
|------|-----------------|----------------|---------------------------|---------------------|---------------|------|
| Amsterdam | 6 | 0.25 | **0.05** | 6 | +0.000 | center |
| Ankara | 948 | 0.90 | **0.03** | 1084 | +0.884 | center |
| Auckland | 5 | 2.05 | **0.04** | 11 | +0.039 | land neighbor |
| Cape Town | 43 | 0.70 | **0.05** | 43 | +0.000 | center |
| Chongqing | 413 | 1.05 | **0.04** | 361 | −0.338 | land neighbor |
| Istanbul | 57 | 1.15 | **0.05** | 57 | +0.000 | center |
| Manila | 7 | 2.15 | **0.04** | −7 | −0.091 | land neighbor |
| Milan | 221 | 1.05 | **0.05** | 221 | +0.000 | center |
| Moscow | 198 | 1.05 | **0.05** | 198 | +0.000 | center |
| San Francisco | 2 | 9.75 | **0.05** | 117 | +0.747 | land neighbor |
| Seoul | 5 | 2.85 | **0.05** | −999 (sea fallback) | +0.000 | center/sea |
| Shenzhen | 2 | 1.90 | **0.04** | 49 | +0.305 | land neighbor |
| Singapore | 15 | 2.35 | **0.05** | 8 | −0.045 | land neighbor |
| Tokyo | 4 | 3.65 | **0.03** | 7 | +0.019 | land neighbor |
| Wellington | 4 | 1.60 | **0.04** | 56 | +0.338 | land neighbor |

Two distinct repair mechanisms are visible: (a) **land-neighbor selection** rescues coastal
cities whose nearest cell is sea (Tokyo, Singapore, SF, Manila, Auckland, Shenzhen, Wellington,
Chongqing); (b) **lapse-rate correction** rescues cities whose nearest land cell is at the wrong
elevation (Ankara +0.88°C over 136 m, SF +0.75°C). Where neither is needed (cell within 100 m of
target), the correction is exactly zero by construction — matching the API's center-cell branch.

### Cities served via RAW read (downscaling unnecessary), all ≤0.1°C

38 flat/inland cities: Atlanta, Austin, Beijing, Buenos Aires, Busan, Chengdu, Chicago, Dallas,
Denver, Guangzhou, Helsinki, Hong Kong, Houston, Jakarta, Jeddah, Jinan, Karachi, Kuala Lumpur,
Lagos, London, Los Angeles, Madrid, Mexico City, Miami, Munich, NYC, Panama City, Paris, Qingdao,
Sao Paulo, Seattle, Shanghai, Taipei, Tel Aviv, Toronto, Warsaw, Wuhan, Zhengzhou — each
raw max\|Δ\| = 0.05°C (the half-quantum API-rounding gap; the bucket carries 0.01°C). These keep
their existing `<cycle>::bucket::<city>` raw whitelist receipts; the resolver prefers raw
(one read, no static field).

### Still quarantined (structural, NOT a downscaling failure): Lucknow

Lucknow is `Asia/Kolkata` (**UTC+5:30** — the only half-hour-offset target). Its local-day hourly
instants fall on the **half-hour in UTC** (18:30Z, 19:30Z, …), but the bucket / model writes only
**on-the-hour** UTC timesteps. `check_partial_run_admission` therefore can NEVER admit it (the
needed `:30` valid_times are absent from the manifest's on-the-hour set) — for the RAW path too.
This is a pre-existing structural limitation of the bucket transport itself, independent of
downscaling. Lucknow is correctly served by rungs 1-2 (single-runs / standard API, which
interpolate to the half-hour). Honest quarantine: the 0.1°C bar is not weakened to admit it.

## Antibody (cross-check + whitelist, downscaled class)

The cross-check is EXTENDED, never weakened:

- `src/data/anchor_cross_check.run_bucket_downscaled_anchor_cross_check_cycle` — once single-runs
  serves the bucket run, compares the stored DOWNSCALED series vs the run-pinned API series per
  city. VERIFIED (≤0.1°C) ⇒ receipt `<cycle>::bucket_downscaled::<city>`; MISMATCH ⇒ ERROR +
  receipt, and the city STAYS quarantined (falls through to rungs 1-2). Mirrors the raw cycle
  (`run_bucket_anchor_cross_check_cycle`) on a separate receipt sub-key so raw and downscaled
  verdicts for one city never collide.
- `resolve_bucket_serve_method(city)` returns `raw` | `downscaled` | `None` from the two receipt
  classes (raw preferred when both verify). `_try_bucket_rung_three` uses the downscaled
  extraction (`fetch_bucket_anchor_payload_downscaled`) for downscaled-verified cities and the
  raw read for raw-verified cities; a city verified by NEITHER class stays quarantined.
- Tolerance stays 0.1°C (`BUCKET_VS_API_TOLERANCE_C`). A city whose downscaled read the API does
  not reproduce within 0.1°C is never served by the bucket — biased anchors remain impossible by
  construction.

## What needs a restart

Code only — no daemon restart performed by this work. To activate the downscaling lane in the
running system the main thread must: (1) restart the replacement-forecast download daemon to pick
up the new `_resolve_anchor_payload` rung-3 path; (2) on the next cycle the cross-check populates
`<cycle>::bucket_downscaled::<city>` receipts as single-runs serves the in-progress run, at which
point the 15 cities flip from quarantined to bucket-servable. The HSURF static field and the
per-city elevation cache are regenerated on first use (`download_hsurf_static_field` /
`capture_city_target_elevation`); both live under gitignored `state/`.

## Reproduction

`/Users/leofitz/zeus/.venv/bin/python /tmp/measure_49city.py` (uses the production
`select_terrain_optimised_point` + `apply_elevation_correction`; ground truth = run-pinned
single-runs API for run 2026-06-10T06:00). Raw output: `/tmp/measure_49city_results.json`.
