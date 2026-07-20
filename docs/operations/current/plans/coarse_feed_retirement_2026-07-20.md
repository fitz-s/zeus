# Coarse (0.25° ecmwf_open_data) feed retirement → fusion — investigation + staged plan

Status: INVESTIGATION COMPLETE, plan pending operator scope decision. 2026-07-20.
Owner: this session. Money-path (live forecast belief + sizing). Not authority law; audit-bound current fact.

## Decisive ground truth (from state/zeus-forecasts.db, read-only, 2026-07-20; supersedes the
## stale current_source_validity.md / current_data_state.md which are 43d old and still cite AIFS)

Two parallel LIVE forecast feeds:
- **Fusion (strategy of record, 9km):** `raw_model_forecasts` (multi-model: ecmwf_ifs 9km, icon_*,
  ukmo_10km, arome_hd, cwa_township, ncep_nbm...) → `forecast_posteriors` /
  `deterministic_forecast_anchors`. Product `openmeteo_ecmwf_ifs9_bayes_fusion_{high,low}_v1`.
- **Coarse legacy (0.25°):** `ecmwf_open_data` GRIB (mx2t3/mn2t3) → `ensemble_snapshots`. Still
  written daily (last 2026-07-19T20:28).

The MAIN live decision path ALREADY reads fusion, not the coarse feed:
- `src/events/triggers/forecast_snapshot_ready.py:517-549` — posterior-backed COMPLETE short-circuit;
  the replacement lane row is sourced from `forecast_posteriors` (NOT ensemble_snapshots),
  `POSTERIOR_BACKED_DATA_VERSION = "forecast_posteriors.replacement_0_1_neutral_carrier"`.
- `src/decision_kernel/verifier.py:48-55` — `POSTERIOR_MEMBERS_JSON_SOURCE = raw_model_forecasts.multimodel`
  vs cold `ENSEMBLE_MEMBERS_JSON_SOURCE = ensemble_snapshots.daily_extrema`.

Residual coarse consumers (the cold lane, still live):
- `forecast_snapshot_ready.py:1175/1225` cold source_run_coverage→source_run→ensemble_snapshots JOIN.
- `src/execution/harvester.py` settlement/learning skill-attribution (joins decision_snapshot_id).
- `src/state/fact_revocation.py` selection_hypothesis_fact→…→ensemble_snapshots.
- `src/state/portfolio.py:627` `decision_snapshot_id` FK to ensemble_snapshots at decision time.

## The blocker: fusion coverage is INCOMPLETE — coarse feed is still LOAD-BEARING

Coverage (target_date >= 2026-07-18):
| track | fusion cities | coarse (ensemble) cities | gap |
|-------|---------------|--------------------------|-----|
| HIGH  | 49            | 54                       | 5   |
| LOW   | 8             | 54                       | 46  |

- **LOW fusion = 8 cities only:** Hong Kong, London, Miami, NYC, Paris, Seoul, Shanghai, Tokyo
  (highest-liquidity). The other **46 cities' LOW track is served ONLY by the 0.25° coarse feed.**
- **5 HIGH-missing cities have ZERO fusion raw data:** Auckland, Jakarta, Jinan, Lagos, Zhengzhou
  (`raw_model_forecasts` empty for them) — genuinely DATA-gated.
- **41 LOW-missing cities HAVE fresh raw multi-model data** (`raw_model_forecasts` present) — so LOW
  is materialization/calibration-gated, NOT data-gated.

Why LOW is capped at 8: `config/settings.json _low_purity_doctrine_2026_05_07` (operator PR#93):
LOW requires a validated per-city contract-bin-preserving Platt calibration bucket; if missing /
UNVERIFIED / QUARANTINED / below n_eff floor the live read returns RAW_UNCALIBRATED (no cross-cluster
Platt borrow, no legacy-metric LOW data_version). HIGH keeps a season-pool fallback (stable
construction law); LOW does not. The 8 LOW-fusion cities are the ones with validated LOW calibration.

Anchor resolution NON-issue: live center is 9km `ecmwf_ifs`. The `ecmwf_ifs025` (0.25°) reference is
ONLY the anchor's HISTORICAL residual-std prior (OpenMeteo previous-runs serves only ifs025 for ECMWF);
`bayes_precision_fusion_capture.py:498-507` explicitly reconciles it to the 9km frame via the declared
`ifs025→ifs9` bridge that WIDENS (never narrows) uncertainty. No hidden 0.25° regression in live belief.

## Staged plan (each phase is a money-path change; do not batch)

- **Phase A — extend fusion LOW to the 41 raw-covered cities.** Materializer scope change. Uncalibrated
  cities trade RAW_UNCALIBRATED on finer 9km data (existing PR#93 law — NOT a new "wait N days" gate;
  strict upgrade over coarse RAW). Verify sample cities' fusion LOW posteriors are sane vs coarse
  before wide flip. OPEN DECISION: RAW-degrade now vs gate each city on validated LOW calibration.
- **Phase B — add fusion source coverage for the 5 data-gated cities** (Auckland/Jakarta/Jinan/Lagos/
  Zhengzhou), both metrics: OpenMeteo multi-model fetch-plan + source-registry addition.
- **Phase C — retire the coarse feed.** Once fusion covers 54×{high,low}: stop `ingest_opendata_daily_*`
  + drop ecmwf_open_data from `ingest_replacement_availability_poll`; migrate the residual cold-lane
  consumers to the posterior lane. KEEP historical `ensemble_snapshots` rows (settlement attribution of
  already-decided positions FKs into them) — retire the INGEST, not the table.

## Per-city best-fusion-combination computation (operator directive 2026-07-20:
## "计算并更新城市最佳fusion组合 按需获取和分配")

Engine: `scratchpad/compute_city_fusion_combination.py` — joins `raw_model_forecasts` (lead<=1) ×
`settlements` over 2026-05-20.. to get per (city, metric, model) de-biased residual std / MAE / bias / n,
runs the live `model_selection.select_models` per city, and reports divergences. De-biased std (not raw
MAE) is the fusion's real member-quality signal because EB walk-forward de-bias corrects bias at member
level (RAW no-de-bias law applies to the CENTER, not to member-instrument selection).

Findings (decision-grade, de-biased std, n>=8):
- **Coverage gaps confirmed at the FETCH layer:** HIGH fused=49 (5 starved: Auckland, Jakarta, Jinan,
  Lagos, Zhengzhou — ZERO raw); LOW fused=8, 46 starved — **LOW raw is not fetched for 46 cities and has
  NO settled history to even measure** (not merely un-materialized). Closing LOW = a FETCH change first.
- **Official city sources beat the fused set (strongest "每城不同源" signal):**
  Taipei/high `cwa_township` std=0.969 MAE=0.820 n=523 CRUSHES fused ukmo_global(1.426)/ecmwf(1.892).
  (Check `hko_fnd` for Hong Kong similarly.) These official meso forecasts are fetched but NOT admitted
  to the F4 selector (REGIONAL_MODELS excludes them).
- **Fetched-but-unfused regionals that beat the current rep (de-biased std):** Amsterdam icon_eu<icon_d2
  (resolution-first doctrine loses to skill); London dmi_harmonie<icon_d2; Paris/low dmi_harmonie<ecmwf;
  US: Chicago/Dallas/Denver/Seattle ncep_nbm/nam_conus < gfs_hrrr/icon_global; Seoul/Busan jma_msm<rep.
- **jma_msm bias caveat:** de-biased std good (Seoul 1.191) BUT raw bias −4.24 (n=90) — admitting on
  std alone imports a large systematic bias the EB de-bias must fully absorb; admit with a bias-magnitude
  guard, not blindly.

CORRECTED MECHANISM (the "计算/更新/分配" is an EXISTING production engine, not a new build):
- **计算 + 分配 (per-city basket weights)** = `scripts/fit_source_clock_city_weights.py` — walk-forward
  per-(city, metric) basket selection (paired greedy, eps=max(0.05C,3%) + 2·SE gate, cap 4, tier
  fallback CONUS/EU/ASIA/OTHER→global-core), weight math = `src/forecast/center.py::raw_second_moment_weights`.
- **更新 (serve it)** = writes `state/source_clock_weights/city_weights_<YYYYMMDD>.json` + `ACTIVE.json`
  pointer; consumed by `source_clock_city_weights.py::scheme_for_city` → `fixed_weight_center_from_values`
  (method SOURCE_CLOCK_FIXED_WEIGHT), fail-closed under PRESENT_WEIGHT_FLOOR=0.25.
- My scratch `compute_city_fusion_combination.py` is a DIAGNOSTIC that partially re-derives this fitter;
  use the CANONICAL fitter for production weights.

THE GAP that blocks the operator's intent (fitter line 112): `LIVE_SERVABLE_MODELS = ANCHOR +
GLOBAL_LIKELIHOOD_MODELS + REGIONAL_MODELS` (from model_selection.py) — it **EXCLUDES the ingested
official station sources cwa_township/hko_fnd**, so the fitter can never select them despite their being
the best-skill source (cwa Taipei n=523 std 0.969). This is why they sit "research-disabled".

Update/fetch slices (each money-path — stages the served center; fitter + model universe are STOP-AND-PLAN):
1. Add official station sources to the fitter's candidate universe (city-pinned: cwa_township→Taipei high,
   hko_fnd→HK high) so basket selection can pick them; keep MIN_ENTRY_PROVIDER_FAMILIES=2 (cwa + a gridded
   2nd family). Re-run fitter → new artifact → promote ACTIVE.json. Serving already reads their ingested
   values. Highest value, decisively proven, bounded to 2 cities' HIGH.
2. Fetch LOW raw for the 41 raw-covered cities + globals for the 5 starved cities ("按需获取"): extend the
   download city×metric plan. Then re-fit (LOW baskets become selectable) and materialize.
3. Re-run the fitter across all 54×{high,low} on the widened candidate universe + fetched data (captures
   the ncep_nbm/nam CONUS, jma_msm E-Asia [bias-guarded], dmi_harmonie EU-edge, icon_eu-vs-icon_d2
   divergences automatically — the greedy+significance basket governance already handles them).
4. Then Phase C (retire coarse) once fusion covers 54×{high,low}.

NOTE the config references `scripts/grade_cwa_township_forward.py` which DOES NOT EXIST (doc-code gap);
the fitter's walk-forward paired-MAE IS the grading — no separate grader needed.

## EXECUTION 2026-07-20 (first-principles pass)

- **LANDED + DEPLOYED + VERIFIED — station ingest re-home** (08425d858 fix, 8c71d03db legacy-cleanup).
  Root cause: the 2026-06-11 download-lane migration orphaned `_ingest_station_forecasts_live` (it
  lived only in the descheduled forecast-live `_replacement_forecast_download_cycle`), so
  cwa_township/hko_fnd went dark 2026-07-17 16:03. Re-homed onto `ingest_main._replacement_availability_poll_tick`
  via due-gated `_ingest_station_forecasts_if_due` (~3h monotonic gate, fail-soft). The data-ingest
  daemon picked it up 1m41s after commit; cwa/hko ingesting again (verified fresh to 2026-07-20 04:00).
  Removed the orphaned duplicate call from the diagnostic cycle (single live owner).

- **FITTER WIDENING — investigated, NOT done (correctly).** Adding cwa/hko (and the extended regionals
  jma_msm/nam_conus/dmi_harmonie/knmi) to `fit_source_clock_city_weights.py::LIVE_SERVABLE_MODELS` is a
  NO-OP right now and was reverted: (a) station sources are `single_runs` endpoint ONLY (no `previous_runs`
  archive — they are official forecasts, not re-forecast gridded models), and the fitter's `_FIT_QUERY`
  reads `endpoint='previous_runs'`; (b) every recently-added source has only ~20-25 settled days
  (cwa ~20, jma_msm/nam/dmi ~23-24), FAR below the fitter's TIER1_MIN_N=60. The fitter's thin-sample
  governance (MIN_SETTLED_N + 2·SE) correctly refuses to fit them — forcing them in would over-fit a
  20-day sample, the exact failure the guard prevents (and NOT a "wait N days" gate: they are used NOW).
- **Current correct serving**: cwa/hko reach Taipei/HK centres via the materializer ADD-DATA path
  (`_station_live_omitted`, operator "加数据不禁数据") at EQUAL_WEIGHT — equal (not precision) because the
  raw_m2/residual history also reads `previous_runs`, which station sources lack, so precision falls back
  to equal. Verified live: Taipei/high `used_models=[icon_global, ukmo, cwa_township, ecmwf_ifs]`.
- **The real granularity lever (future, careful, money-path)**: make the residual/precision history (and
  optionally the fitter) read station `single_runs` walk-forward, with EB-shrinkage moderating the thin
  sample — so cwa/hko graduate off equal-weight onto a precision weight reflecting their settlement-station
  skill. Do NOT force it before the mechanism is built + staged; the thin-sample shrinkage is the crux.

## Dead residue (separate, low-risk cleanup — verify then delete)
- `ecmwf_ifs_ens_0p1` 0.1° registry stub in `forecast_source_registry.py` — 0 rows ever, 0 live consumers.
- `_legacy_coarse_unique_20260607T131448Z` archive tables (posteriors/anchors/shadow) — 0 code refs
  (DROP is destructive on 42GB live DB → operator sign-off + backup required, do NOT auto-drop).
- dead `..._aifs_sampled_2t_soft_anchor_*` posterior products (last 2026-06-20).
- `forecast_posteriors.aifs_source_run_id` residual column (post-AIFS-deletion).
