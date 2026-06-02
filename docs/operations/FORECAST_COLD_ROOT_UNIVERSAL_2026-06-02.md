# Forecast "Cold Bias" — Universal Root, Verified (SUPERSEDES the SF single-city note)

Created: 2026-06-02
Last reused/audited: 2026-06-02
Authority basis: GOAL #36; 54-city deep analysis (workflow wf_faeda208); operator-verified spot checks (grid columns, 144h cap, SF lead-0 2.5yr series). This doc SUPERSEDES SF_BIAS_ROOT_CONFIRMED_AND_PROPOSED_FIX_2026-06-02.md, whose §4b (enable +4.682 correction) is RETRACTED.

## Retraction
The earlier SF conclusion — "genuine cold bias, re-enable the +4.682 correction" — was a single-city overreach on wrong data. It is WRONG. There is no proven genuine model bias. Applying +4.682 would mask a grid-cell extraction defect with a magic constant.

## The "cold" is NOT one thing and NOT a model bias. Three disjoint structural defects:

### 1. Marine grid-cell contamination (coastal cities) — VERIFIED
- ensemble_snapshots stores NO nearest_grid_lat/lon (only bin_grid_id) — we never record which cell we sampled.
- Extraction = pure regular-grid nearest-neighbor, NO land mask. For a coastal/bay settlement station the nearest 0.25° node lands over water.
- **Lead-0 proof (decisive):** SF lead=0 (model analysis, zero forecast skill, zero truncation) summer daily-max = 58–63°F across 2024→2026, while KSFO settles ~69°F (≥66°F 80% of days). A 6–10°F cold offset at zero lead can only be the grid cell. CALC recovered SF node 14.32°C vs one-cell-inland 19.19°C (+4.87°C) — and +4.87°C ≈ the −4.682 "bias". The "bias" IS the marine displacement.
- Confirmed: SF, LA. Flagged-unaudited (cannot confirm without grid provenance): Tokyo, Hong Kong, Seoul, Busan, Cape Town, Manila.

### 2. D+6 horizon truncation (UTC-negative cities) — structural
- The afternoon-peak GRIB step (~150–162h from 00Z) exceeds OPENDATA_MAX_STEP_HOURS=144 → only a pre-dawn window survives, mislabeled FULLY_INSIDE_TARGET_LOCAL_DAY/contributes=1 → cold daily-max.
- Deterministic across all 17 UTC-negative cities (Americas + SF/LA/Seattle/Denver/Chicago/Houston/Austin/NYC/Miami/Atlanta/Dallas/Toronto/Mexico City/Panama/Buenos Aires/Sao Paulo + Lucknow); 0 rows for the 36 Eastern/European cities. Longitude/horizon geometry, not bias. (= task #28.)

### 3. Genuine model bias — ZERO survive refutation
- Every "climatology" comparison used a different station/window than the forecast cell → measures reference-mismatch + weather variability, not bias. Every "independent" arm was a forecast at fetch time (future target dates), not a verifying observation. No model bias is proven; none is currently measurable until target dates settle.
- model_bias_ens: OFF in live (weight_live=0). MUST stay OFF — it is not a remedy for a grid-cell or truncation defect.

## Per-datum: should-be vs is
- ENS member daily-max: IS = max over a marine cell and/or a truncated window. SHOULD = max over the correct LAND cell across the full local day, or fail-closed when peak steps are unfetched.
- Grid cell: IS = nearest node, no land mask → marine for coastal stations. SHOULD = nearest LAND node (land-mask/coastal correction).
- Local-day window: math correct (DST-aware ZoneInfo); the FULLY_INSIDE label must be unconstructable when required steps > fetched horizon.
- Field/unit: correct (mx2t3/mn2t3 stepType max/min, K→native once, 0 K rows). Latent: LOW extractor hard-codes payload members_unit='K' (inert; reject non-{degC,degF}).
- model_bias_ens / q: q is a faithful downstream of the members; it needs no patch once the upstream extraction defects are fixed. Do not correct q to compensate for a wrong cell.

## The right fix (structural; make each error category unconstructable)
1. **Horizon-coverage gate** + raise the 144h cap to cover Western D+5/6 peaks (~162h): contributes=1/FULLY_INSIDE unconstructable when max(required_steps) > fetched horizon. Kills the truncation category for all 17 cities at once. (task #28.)
2. **Persist grid provenance + land-mask node selection**: add real nearest_grid_lat/lon/distance to ensemble_snapshots, populate on live ingest (currently None/absent), then select the nearest LAND node for coastal settlement stations. Provenance persistence is the antibody — until the cell is stored, the full marine-contaminated set is unknowable in production.
3. **Contract antibody**: reject members_unit ∉ {degC,degF}.

## What NOT to do
- Do NOT apply a flat +bias to cold cities (masks marine + truncation; wrong on every other row).
- Do NOT enable edli_bias_correction / set weight_live>0 (0 genuine bias; double-counts a structural defect as model error, corrupts calibration).
- Do NOT fix truncation alone (marine cell is independent, persists at lead-0).
- Do NOT trust independent_minus_climatology as a bias signal (station/window mismatch).
- Do NOT change any live config now.

## Remaining uncertainty
- 6 coastal cities unaudited — need the grid-provenance fix (re-run GRIB nearest-neighbor per city) to confirm marine vs land.
- No verifying observation truth yet (future target dates) — genuine bias is unproven, not proven-absent; revisit once dates settle and observation running_max exists.
- 47/54 per-city structured lanes failed to emit (schema); universality rests on the deterministic truncation geometry (17 cities) + lead-0 marine proof + 3 calc dives + ~7 surviving city audits + refutation, not 54 independent measurements.
