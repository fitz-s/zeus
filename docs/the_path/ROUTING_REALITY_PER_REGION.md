# ROUTING_REALITY_PER_REGION — What the live replacement_0_1 path actually routes, and per-region skill before/after

> Created: 2026-06-08
> Authority basis: REALIGN_0_1_AUTHORITY.md (live authority = replacement_0_1 = AIFS + Open-Meteo IFS 0.1), forecast_source_registry.py, event_reactor_adapter.py, replacement_forecast_bundle_reader.py; settlement truth = zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'.
> Method: READ-ONLY (sqlite mode=ro) on LIVE DBs in /Users/leofitz/zeus/state. Forecast SKILL only (bin-hit, PI-coverage) — PATH-INDEPENDENT, history-validatable, walk-forward (per-city bias/residual learned from PRIOR settled cells; no lookahead). ECMWF-ENS history used as the same-family proxy for the live AIFS/0.1 center (AIFS has 0 settled cells). Measurement script: /tmp/per_region_skill.py + /tmp/per_region_cov.py (not repo).

## IRON RULE #3 VERDICT — the operator's "different regions use different tighter calibration, e.g. EU ICON 2km" is FALSE in the live path

There is **NO regional high-res model wired into the live replacement_0_1 authority.** ICON-D2 (~2km EU) and AROME (France) do not exist anywhere in live `src/` — zero hits for `2km|icon_d2|arome|0.02deg` across the entire source tree. The "tighter calibration that varies regionally" is the **per-CITY EB bias** (`model_bias_ens`, no `region` column, keyed `(city,season,month,metric,live_data_version,lead_bucket)`), applied on top of ONE global model stack.

## ROUTING REALITY (file:line)

1. **Live probability authority** = `_replacement_authority_probability_and_fdr_proof` (event_reactor_adapter.py:5430), reached from `_live_yes_probabilities` (~:5292) on FORECAST_SNAPSHOT_READY. Gated ONLY by `_replacement_authority_enabled()` (event_reactor_adapter.py:5341) → feature flag `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled` = **true** (config/settings.json:296). Flag-alone grants authority; no settlement-evidence gate (REALIGN finding).
2. **What it pulls**: `read_replacement_forecast_bundle` (replacement_forecast_bundle_reader.py:375) reads directly `FROM forecast_posteriors` (:426), one fixed source: `SOURCE_ID = openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor`. Dependencies (bundle_reader:161): `baseline_b0`, `aifs_sampled_2t`, `openmeteo_ifs9_anchor`. The two ingested model families in `raw_forecast_artifacts` are exactly and only `ecmwf_aifs_ens` + `openmeteo_ecmwf_ifs_9km` (287 rows) — both GLOBAL.
3. **Registry tier gate is BYPASSED for the live decision.** In forecast_source_registry.py, `ecmwf_aifs_ens` (:282), `openmeteo_ecmwf_ifs_9km` (:291), and `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` (:300) are all `tier="disabled"`, `allowed_roles=("diagnostic",)`. The live path never calls `gate_source`/`is_source_enabled` (only `calibration_source_id_for_lookup` is imported, :4298/:7646). So the registry's "disabled/diagnostic" verdict governs the legacy OpenData serving path, NOT the flag-gated replacement_0_1 authority that is live.
4. **entry_primary vs diagnostic in the registry** (allowed_roles): `entry_primary` = `tigge` (experimental, operator-gated OFF) and `ecmwf_open_data` (secondary). `monitor_fallback` = `openmeteo_ensemble_ecmwf_ifs025`, `openmeteo_ensemble_gfs025`. Everything else (`*_previous_runs`, the 0.1/AIFS/soft-anchor specs) = `diagnostic` only. `icon_previous_runs`/`icon_global` is a diagnostic forecast_table — the only "ICON" in the system, and it is GLOBAL, diagnostic, not high-res, not routed.
5. **No region-conditioned model selection anywhere.** Zero hits for `if.*region.*==|region_model|regional_model|model_for_region` in `src/`; the materializer/request-builder/AIFS-probabilities have no region/icon/arome/high-res branch.

## LIVE vs AVAILABLE-BUT-NOT-WIRED

| thing | status |
|---|---|
| AIFS ENS sampled-2t (global) + Open-Meteo ECMWF IFS 9km/0.1 anchor → soft-anchor posterior | **LIVE** (flag-on, forecast_posteriors authority) |
| per-city EB bias (`model_bias_ens`, 153 rows / 51 cities, no region key) | available; the "regional variation" the operator means — but NOT yet applied to the live AIFS center (open-Q: AIFS bias re-fit pending, 0 settled cells) |
| ICON-D2 ~2km EU / AROME France | **DOES NOT EXIST** in code or data — not wired, not ingested, not registered |
| ECMWF Open Data ENS (B0 baseline) | available, entry_primary in registry, but live authority is the replacement posterior, not B0 |
| TIGGE | operator-gated OFF (env flag + artifact required) |

## LIVE replacement_0_1 SKILL IS NOT DIRECTLY MEASURABLE TODAY

`forecast_posteriors` method `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` = 192 rows (167 high + 25 low), ALL target_date 2026-06-08..06-10. Latest VERIFIED settlement = 2026-06-06. **0 of 192 join to VERIFIED settlement** → live posterior skill is unmeasurable on history. First live EDLI fills 2026-06-06; **0 settled traded positions** → after-cost win-rate / q_lcb coverage of the live path are unmeasurable (OBSERVE_BASELINE.md). Skill below is on the same-family ECMWF-ENS proxy (the legitimate transfer for an ECMWF AIFS/0.1 center).

## PER-REGION BEFORE/AFTER (ECMWF-ENS proxy, walk-forward, lead≈24h, 1°C floor-bins, raw degC-numeric per provenance hazard)

The "AFTER" applies the validated candidate (a) per-city walk-forward EB bias-correction (SUPERIOR_BLEND.md) for bin-hit, and adds candidate (c) dispersion-widening to realized residual sd for the 90% PI-coverage column. Raw-numeric comparison (settlement_value and members are degC-numeric; settlement_unit/members_unit labels are mislabeled and ignored; |center-obs|>20 dropped as corruption).

| region | n | bin-hit B | bin-hit A | Δ | PI90-cov B | PI90-cov A |
|---|---:|---:|---:|---:|---:|---:|
| EU  | 2630 | 0.168 | 0.268 | +0.100 | 0.423 | 0.867 |
| NA  | 1594 | 0.102 | 0.127 | +0.024 | 0.395 | 0.845 |
| AS  | 1147 | 0.137 | 0.219 | +0.082 | 0.302 | 0.767 |
| SA  |  379 | 0.135 | 0.282 | +0.148 | 0.359 | 0.805 |
| SAS |  129 | 0.202 | 0.225 | +0.023 | 0.372 | 0.775 |
| ME  |  121 | 0.066 | 0.099 | +0.033 | 0.182 | 0.603 |
| AF  |   76 | 0.026 | 0.224 | +0.197 | 0.224 | 0.737 |
| OC  |  124 | 0.073 | 0.266 | +0.194 | 0.056 | 0.855 |
| **POOL** | **6200** | **0.138** | **0.219** | **+0.080** | **0.374** | **0.830** |

Pooled +0.080 bin-hit reproduces SUPERIOR_BLEND.md's pooled +0.068 (0.191→0.259) directionally (small offset from including low metric + all dates + 1°C floor-bins vs the audit's high-only lead=1 topology bins). PI90 coverage BEFORE ≈ 0.37 vs nominal 0.90 is the 3.24× underdispersion (QLCB_HONESTY.md); widening lifts it to ≈0.83. EU is the LARGEST cohort (n=2630) and shows the largest single-region absolute bin-hit gain among the high-n regions (+0.100). ME stays worst on coverage even after widening (0.603) — consistent with QLCB's "Jeddah 11.5× underdispersion" worst-city finding.

## ICON-2km-vs-AIFS+0.1 ON EU — NOT MEASURABLE

The comparison cannot be quantified because **ICON-D2 2km does not exist** in Zeus code or data — no source spec, no ingest, no rows in any forecast table (`raw_forecast_artifacts`/`forecast_posteriors`/`deterministic_forecast_anchors` carry only `ecmwf_aifs_ens` + `openmeteo_ecmwf_ifs_9km`). There is nothing to evaluate against AIFS+0.1 on EU cells. The measurable EU result is the in-family skill above: AIFS/0.1-proxy EU bin-hit 0.168 → 0.268 via per-city bias-correction. A regional-model improvement cannot be claimed (iron rule #2) — it is not wired.

## CAVEATS
- ECMWF-ENS proxy ≠ live AIFS; bias DIRECTION transfers (same family), magnitude must be re-fit once June+ AIFS posteriors settle.
- Small-n regions (AF n=76, ME n=121, SAS n=129, OC n=124) are DIRECTIONAL only.
- Bin-hit uses 1°C floor-bins, not the exact market topology; absolute level differs slightly from SUPERIOR_BLEND's topology-bin numbers (direction identical).
- After-cost win-rate / live q_lcb coverage are cohort-dependent and have 0 live settled cells today; not claimed.
