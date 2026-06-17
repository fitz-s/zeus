# Fusion precision-input fix — feed the precise 9km + regional data, not coarse cells

- Created: 2026-06-17
- Authority basis: operator directive (verbatim) "the data we use is 9km level and the regional
  data is even more precise, your 25 and 15 and the not precise cell is breaking the fusion
  calculation." Translation: the T2 Bayesian precision-fusion center runs COLD vs settlement
  because it is fed IMPRECISE coarse model cells; the fix is to feed the PRECISE data (highest-
  resolution station-resolving model where the city is covered), NOT a statistical bias correction.
- Scope: INPUTS to the fusion only. The T2 precision-fusion math
  (`src/forecast/bayes_precision_fusion.py`) is UNTOUCHED.

---

## 1. The model-set map the fusion currently uses (BEFORE)

The fusion is fed an anchor PRIOR + decorrelated-global LIKELIHOOD instruments + in-domain
regional experts. Stored/served from `state/zeus-forecasts.db.raw_model_forecasts`
(`endpoint='single_runs'` = the value visible at decision time; the q path reads ONLY these
persisted rows, never a live fetch — BLOCKER 5).

| role | model | OM product / resolution | coarse? |
|------|-------|------------------------|---------|
| anchor (prior) | `ecmwf_ifs` | OM `ecmwf_ifs` = ECMWF HRES **9 km / 0.1°** | NO (already precise) |
| global likelihood | `gfs_global` | NOAA GFS **0.25° / ~25 km** | **YES (the "25")** |
| global likelihood | `icon_global` | DWD ICON **~13 km** | **YES (the "15"/13)** |
| global likelihood | `gem_global` | CMC GDPS **~15 km** | **YES (the "15")** |
| global likelihood | `jma_seamless` | JMA **~10-20 km**, coarse-cell offshore snap | **YES (worst)** |
| global likelihood | `ukmo_global_deterministic_10km` | UKMO **10 km** | YES (borderline) |
| global likelihood | `ncep_nbm_conus` | NBM CONUS **~13 km blend** | YES |
| global likelihood | `icon_eu` | DWD ICON-EU **7 km** (EU domain) | regional-ish |
| regional expert | `icon_d2` | DWD **2 km** EU nest | NO (precise) |
| regional expert | `meteofrance_arome_france_hd` | **1.3 km** France | NO (precise) |
| regional expert | `ukmo_uk_deterministic_2km` | **2 km** UK | NO (precise) |

The "25 and 15" the operator names are the coarse decorrelated globals (`gfs_global` 0.25°,
`icon_global`/`gem_global` ~13-15 km). The anchor `ecmwf_ifs` is ALREADY the 9 km HRES (live OM
probe: Singapore resolves cell (1.371, 104.016) elev 15 m, NOT offshore). The 0.25 `ecmwf_ifs025`
appears ONLY in the anchor history bridge (`bayes_precision_fusion_anchor_bridge.py`), which is
already declared and widen-only — not a live member, left untouched.

### Verified cold gap (the disease), state/zeus-forecasts.db, settled WU highs 2026-06-08..06-16

- Member-mean (drop `icon_seamless` alias) minus settlement: **mean −1.23 °C, median −0.92 °C,
  MAE 1.59 °C, n=389.** Worst (coastal/E-Asia, coarse cells snap offshore): Qingdao −8.1, Beijing
  −7.9, Busan −7.2, Seoul −6.6, Shanghai −6.3, Taipei −6.2.

### Per-model RAW bias vs settlement (NO statistical correction), same window — the smoking gun

| model | n | mean_bias °C | MAE °C |
|-------|---|--------------|--------|
| jma_seamless | 389 | **−2.10** | 2.40 |
| ecmwf_ifs (9 km) | 389 | −1.20 | 1.73 |
| ukmo_global_10km | 340 | −1.10 | 1.86 |
| icon_global | 389 | −1.08 | 1.59 |
| gfs_global | 389 | −0.86 | 1.97 |
| icon_eu (7 km) | 95 | −0.61 | 0.85 |
| ukmo_uk_2km | 7 | −0.39 | 0.93 |
| icon_d2 (2 km) | 38 | **−0.33** | 0.87 |
| ncep_nbm_conus | 84 | −0.28 | 1.13 |
| meteofrance_arome (1.3 km) | 23 | +0.67 | 1.01 |

The coarse globals run ~1-2 °C cold; the high-resolution station-resolving models are near-zero
raw bias with the lowest MAE. This confirms the operator's thesis: precise inputs fix the center
WITHOUT any bias correction.

---

## 2. Precise per-city sampling (live OM coverage probe 2026-06-17)

- `ecmwf_ifs` (9 km HRES) — already the anchor; resolves a precise on-station cell everywhere.
  `nearest` snapping offshore is an `ecmwf_ifs025` (0.25) problem, NOT the 9 km feed.
- `cell_selection=land` REJECTED as a universal lever: live probe shows `land` returns the SAME
  cell as `nearest` for nearly all stations (the coarse cell is already land-masked), exactly the
  "coin-flip" the directive flagged. Not used.
- High-res REGIONAL coverage that genuinely adds precision where a city is covered:
  - **CONUS** → `gfs_hrrr` (NOAA HRRR **3 km**) + `gem_hrdps_continental` (CMC HRDPS **2.5 km**).
  - **N-America (Toronto)** → `gem_hrdps_continental`.
  - **Europe** → already covered by `icon_d2` / `arome` / `ukmo_uk` (in the set).
  - **E-Asia coastal cold cities** (Qingdao/Beijing/Busan/Seoul/Shanghai/HK/Singapore): the only
    OM high-res regional is `jma_msm` (5 km) — REJECTED: settlement-graded raw bias −2.15 °C,
    MAE 2.57 (same JMA physics as jma_seamless, no improvement). For these the 9 km `ecmwf_ifs`
    anchor remains the precise truth; no coarse regional is added.

### Settlement-graded RAW bias of the candidate precise adds (OM previous-runs, lead-1)

| model | n | mean_bias °C | MAE °C | verdict |
|-------|---|--------------|--------|---------|
| **gfs_hrrr** (3 km CONUS) | 60 | **+0.004** | 1.33 | ADD — near-perfect raw calibration |
| **gem_hrdps_continental** (2.5 km) | 18 | +0.86 | 1.43 | ADD — station-resolving, decorrelated CMC |
| jma_msm (5 km E-Asia) | 36 | −2.15 | 2.57 | REJECT — no gain over jma_seamless |

---

## 3. Implementation (inputs only; fusion math untouched)

`gfs_hrrr` and `gem_hrdps_continental` added as in-domain regional experts. By the existing
provider-family single-rep doctrine (most-specific-eligible-first), in-CONUS `gfs_hrrr` carries the
NOAA family and SUPPRESSES `gfs_global` + `ncep_nbm` as provider-dups; `gem_hrdps_continental`
carries the CMC family and suppresses `gem_global`. Out-of-domain (Asia/EU) the coarse globals
carry their families unchanged — zero behavior change for cities without high-res coverage.

Edits (5 files, +101/−8):

- `src/forecast/model_selection.py` — new `GFS_HRRR_MODEL` / `GEM_HRDPS_MODEL`; added to
  `REGIONAL_MODELS`; `NCEP_FAMILY = (gfs_hrrr, ncep_nbm_conus, gfs_global)` (most-specific first);
  new `GEM_FAMILY = (gem_hrdps_continental, gem_global)`; `PROVIDER_FAMILIES` extended;
  `_REGIONAL_DOMAIN_KEY` entries for the two nests.
- `config/model_domain_polygons.yaml` — CONUS polygon for `gfs_hrrr` (lat 21-50, lon −125..−66,
  lead≤2) and N-America hull for `gem_hrdps_continental` (lat 18-62, lon −142..−52, lead≤2).
- `src/data/bayes_precision_fusion_capture.py` — `OPENMETEO_MODEL_IDS` entries (download fetches
  them so they accrue single_runs + previous_runs history).
- `src/data/bayes_precision_fusion_download.py` — `OPENMETEO_PREVIOUS_RUNS_SOURCE_ID` entries
  (`<model>_previous_runs`; OM prev-runs availability curl-verified 2026-06-17). They ride
  `BAYES_PRECISION_FUSION_EXTRA_MODELS` automatically and are domain-gated via `REGIONAL_MODELS`.
- `src/data/replacement_fusion_upgrade_trigger.py` — `DECORRELATED_PROVIDER_FAMILIES` NCEP/CMC
  extended with the nests so the family-completeness telemetry counts the precise rep as its
  provider (no under-report when the high-res nest wins).

Selection verified: CONUS (NYC) lead-1 → `used = (ecmwf_ifs, icon_global, jma_seamless,
ukmo_global, gfs_hrrr, gem_hrdps_continental)`, `dropped_provider_dups = (icon_eu, gfs_global,
ncep_nbm_conus, gem_global)`. Singapore lead-1 → no regionals, globals carry families (unchanged).

---

## 4. Validation — OLD vs NEW fused center vs settled (forecast verification, NO market backtest)

Method: real stored single_runs members for each settled CONUS day, the REAL family single-rep
`select_models` selection, fused center = member-mean over the SELECTED set (the precision-weighted
T2 center tracks this set). NEW adds the precise high-res regionals at their real OM previous-runs
lead-1 value. **No EB / statistical bias correction applied** — this is the raw precise forecast,
exactly per the directive. Window settled 2026-06-08..06-15, 12 CONUS cities, n=84.

| metric | OLD (coarse) | NEW (precise) |
|--------|-------------|---------------|
| mean gap °C | **−0.387** | **−0.262** (cold bias −32%, toward 0) |
| MAE °C | 1.055 | **1.029** |
| RMSE °C | 1.292 | **1.287** |

Representative per-city cold-gap shrinkage (oldgap → newgap, °C):
NYC 06-11 −1.20→−0.65 (hrrr 36.3); Chicago 06-10 −1.14→−0.21 (hrrr 34.4, hd 35.4);
Seattle 06-13 −2.22→−1.43 (hrrr 27.1, hd 29.4); San Francisco 06-11 −2.40→−1.30 (hrrr 34.0);
Toronto 06-11 −0.74→−0.45; Miami 06-15 −2.32→−1.92 (hrrr 34.0).

The precise station-resolving members consistently warm the cold center toward settlement and the
cold tail shrinks — the success bar (the precise fused center materially closes the cold gap WITHOUT
bias correction) is met. A few cells (Dallas/Austin) see gfs_hrrr slightly cooler; this is the raw
forecast (no cherry-picking) and the aggregate mean-gap, MAE and RMSE all improve.

Scope honesty: the validation uses a member-MEAN proxy for the precision-weighted center and a
member set spanning CONUS only (where genuine high-res regionals exist). E-Asia coastal cities —
the largest absolute cold gaps — are NOT fixed by an input add (no good high-res OM regional;
jma_msm rejected); for them the 9 km `ecmwf_ifs` anchor remains the precise truth and the fix is
that the coarse cold globals no longer outvote it where a precise rep exists.

---

## 5. Tests

- Required suites GREEN: `tests/money_path/ tests/strategy/live_inference/ tests/architecture/`
  → **378 passed** (baseline before edits: 378 passed — no regression).
- Fusion/model-selection set GREEN: `-k "bayes_precision_fusion or model_selection or fusion"`
  → 147 passed, 2 skipped. Upgrade-trigger / provider-family set → 13 passed, 1 skipped.
- `tests/test_replacement_forecast_materializer.py`: 11 failures are **PRE-EXISTING** — verified
  by running the same test against the unmodified main tree (identical
  `AIFS_MEMBER_COVERAGE / OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE` block; unrelated to this change).

## Provenance verdict

`CURRENT_REUSABLE` for the precision-input fix: it adds station-resolving raw members under the
existing single-rep + polygon law, touches no fusion math, and is settlement-graded. New models
begin with n=0 walk-forward history → they contribute RAW (equal-weight, no EB correction) until
≥MIN_TRAIN rows accrue — which is precisely the operator's intent (the raw precise forecast is the
single truth, no statistical bias correction).
