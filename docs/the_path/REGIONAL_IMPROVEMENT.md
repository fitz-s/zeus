# REGIONAL_IMPROVEMENT — Per-Region Calibration Reality & Quantified Skill

<!--
Created: 2026-06-08
Last reused or audited: 2026-06-08
Authority basis: READ-ONLY live-DB + live-src audit. Truth = zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'.
  Routing verified against src/engine/event_reactor_adapter.py, config/settings.json, forecast_source_registry.py,
  and live DBs (zeus-forecasts.db, zeus-world.db). Cross-refs: REALIGN_0_1_AUTHORITY.md, SUPERIOR_BLEND.md,
  QLCB_HONESTY.md, OBSERVE_BASELINE.md, ROUTING_REALITY_PER_REGION.md. Iron rules #2/#3/#6 applied.
-->

## TL;DR (lead with the correction)

The operator's premise — **"different regions use different tighter calibration, e.g. EU ICON 2km"** — is
**FALSE in the live path.** No regional high-res model is wired anywhere in Zeus. ICON-D2 (2km) and AROME do
not exist in the code, are not ingested, are not registered as a requestable model, and have **zero rows** in
any forecast table. EVERY region routes through ONE uniform live authority:
`replacement_0_1` = AIFS sampled-2t (global) + Open-Meteo ECMWF-IFS 0.1deg/9km soft-anchor. The ONLY thing
that varies "regionally" is the per-**CITY** EB bias (`model_bias_ens`), which has **no region column** and is
not a per-region or per-model routing decision.

---

## (1) ROUTING REALITY — what the live path actually does per region

### One uniform global authority, no per-region branch

| Aspect | Live reality | Evidence (file:line) |
|---|---|---|
| Live YES-prob authority | `_replacement_authority_probability_and_fdr_proof`, stamps `probability_authority='replacement_0_1'` | `src/engine/event_reactor_adapter.py:5430`, call site `:5301` |
| Gate | `_replacement_authority_enabled()` returns **ONLY** the feature-flag bool — **no settlement-evidence gate** | `event_reactor_adapter.py:5348-5353` (verified: `return bool(flags.get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled", False))`) |
| Flag value | `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled = true` | `config/settings.json:296` |
| Region branch in authority | **NONE.** grep for `region.*==|region_model|regional_model|model_for_region|continent` over the authority path returned **zero** | `event_reactor_adapter.py`, `src/events/triggers/forecast_snapshot_ready.py` |
| Regional high-res model | **NONE.** grep for `icon_d2|icon-d2|arome|0.02deg|2km|icon_eu` over `src/` returned **zero hits** | entire `src/` tree |

The flag-gated live path reads `forecast_posteriors` directly for ONE fixed
`source_id=openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` and **never calls** `gate_source` /
`is_source_enabled`. The registry's `tier='disabled'`/`role='diagnostic'` tags on the AIFS/0.1/soft-anchor
specs are therefore **bypassed** for the live authority decision (the registry governs the legacy OpenData
serving path, not the live posterior authority).

### Confirmed against live DBs (mode=ro)

- `forecast_posteriors`: **ONE** `source_id` only (`openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor`), 193 rows,
  all `target_date` 2026-06-08..06-10. **Zero** ICON/AROME/2km source_ids.
- `raw_forecast_artifacts`: model families = **ONLY** `ecmwf_aifs_ens` (14) + `openmeteo_ecmwf_ifs_9km` (275),
  both **GLOBAL**. Zero ICON/AROME.
- `ensemble_snapshots` (member store of record): **ONLY** `ecmwf_ens` (1,154,218) + `ecmwf_ifs025` (69) +
  `tigge` (132). **Zero** ICON / AIFS / AROME members ever ingested.
- `model_bias_ens` (zeus-world.db): 153 rows / 51 cities. PK = (city, season, month, metric,
  live_data_version, lead_bucket). **NO region column** — confirmed via `PRAGMA table_info`. This is the only
  "regional flavor", and it is **per-CITY**.

### LIVE vs AVAILABLE-BUT-NOT-WIRED

- **LIVE (flag-on):** AIFS sampled-2t + Open-Meteo ECMWF-IFS 0.1deg/9km soft-anchor posterior, uniform across
  all regions.
- **AVAILABLE-BUT-NOT-WIRED-as-routing:** per-city EB bias (`model_bias_ens`, 51 cities, no region key). This
  IS the "regional variation" the operator means, but it is per-CITY, not per-region/per-model, and per
  OBSERVE_BASELINE / SUPERIOR_BLEND it is **not yet applied to the live AIFS center** (0 settled AIFS cells;
  re-fit pending). The two EB-related serving flags
  (`replacement_0_1_eb_bias_correction_enabled`, `settings.json:90`) are **default-FALSE**, so the live serving
  path today applies **no** bias-correction in any region.
- **DOES NOT EXIST AT ALL:** ICON-D2 2km, AROME — not in code, not ingested, not registered, not requestable
  via `openmeteo_client.py`. The only "ICON" in the system is `icon_global` (~13km) /`icon_previous_runs`,
  `allowed_roles=('diagnostic',)` only (`forecast_source_registry.py:205`) — a GLOBAL diagnostic, never trade
  authority.

---

## (2) Quantified PER-REGION before/after table

**Method.** AIFS has **0 settled cells** (live posteriors all future-dated; 0 of 193 join VERIFIED
settlement — verified by JOIN). So skill is measured on the **same-family ECMWF-ENS proxy** (identical
member→bin→soft-anchor math), which is path-independent and history-validatable. Walk-forward: per-city EB
bias = mean error from PRIOR settled cells (no lookahead). Lead≈24h, 1°C floor-bins, raw-degC-numeric frame
(`settlement_unit`/`members_unit` labels are MISLABELED for US cities → ignored; `|center-obs|>20` dropped as
corruption guard). Truth = `settlement_outcomes` WHERE `authority='VERIFIED'`.

`q_lcb` coverage is on the OLD-path settled population (PI90 widen-to-realized-residual-sd candidate); AFTER
coverage with floor/gate flags enabled is **unmeasurable** (flags OFF + live `q_lcb_json` NULL on 192/192).

### Bin-hit before/after (independently reproduced — see Validation note)

| region | n | bin-hit B | bin-hit A | Δ | PI90 cov B | PI90 cov A (nominal 0.90) | flag |
|---|---:|---:|---:|---:|---:|---:|---|
| EU | 2630 | 0.168 | 0.268 | +0.100 | 0.423 | 0.867 | high-n |
| NA | 1594 | 0.102 | 0.127 | +0.024 | 0.395 | 0.845 | F-unit distortion |
| AS | 1147 | 0.137 | 0.219 | +0.082 | 0.302 | 0.767 | high-n |
| SA | 379 | 0.135 | 0.282 | +0.148 | 0.359 | 0.805 | mid-n |
| SAS | 129 | 0.202 | 0.225 | +0.023 | 0.372 | 0.775 | **small-n / directional** |
| ME | 121 | 0.066 | 0.099 | +0.033 | 0.182 | 0.603 | **small-n / directional** |
| AF | 76 | 0.026 | 0.224 | +0.197 | 0.224 | 0.737 | **small-n / directional** |
| OC | 124 | 0.073 | 0.266 | +0.194 | 0.056 | 0.855 | **small-n / directional** |
| **POOL** | **6200** | **0.138** | **0.219** | **+0.080** | **0.374** | **0.830** | |

(Numbers above from the `routing` lens with full PI90 coverage pair. My own independent reproduction at
lead<36h with one snapshot per (city,date) returned the SAME direction and ranking: EU +0.082 n=2267,
NA +0.022 n=1533, AS +0.075 n=1063, SA +0.176, SAS +0.008, ME +0.092, AF +0.181, OC +0.179, POOL +0.072
n=5672. The pooled +0.072..+0.086 band reproduces SUPERIOR_BLEND's +0.068 directionally.)

**after-cost win-rate b/a = NOT MEASURABLE per region.** The live `replacement_0_1` book has 0 settled traded
positions (first fills 2026-06-06); only 24 OLD-path positions ever settled (all buy_yes, 20.8% win, -$2.54)
— too few to split 8 regions. After-cost win-rate is cohort-dependent and not licensed by historical skill.

---

## (3) Which regions the calibration helps / hurts, and why

- **Helps most:** OC (+0.19), AF (+0.20), SA (+0.15) — strongly cold-biased cities where the per-city shift is
  large; and **EU (+0.10, n=2630)**, the largest high-n cohort, which shows the largest *high-n single-region*
  bin-hit gain. AS (+0.08) also benefits.
- **Helps least / structurally distorted:** **NA (+0.02).** NA is dominated by F-unit cities (11/12 settle in
  F; only Toronto settles in C). On a 1°C bin grid, F-domain members read ~5-10°F cold, collapsing base
  bin-hit; the apparent NA weakness is the **F/C bin hazard** (QLCB_HONESTY q8), not a genuine skill
  regression. On the correct native 1°F grid NA improves (~0.016→0.063 per the per-region lens). **NA numbers
  least trustworthy.**
- **Coverage:** PI90 BEFORE ≈0.37 vs 0.90 nominal = the **3.24× underdispersion** (QLCB_HONESTY); widening to
  the realized residual sd lifts it to ≈0.83 pooled. **ME stays worst on coverage even after widening (0.603)**
  — matches QLCB's Jeddah-worst-city finding.

The per-city EB shift removes the ~−1°C cold center *regionally* (every region's mean-PIT moves toward 0.5 in
the per-region lens), so the gain is a genuine bias-removal effect — but it is an **EB-bias gain, not a
regional-model gain.**

---

## (4) ICON-D2 2km EU verdict

**NOT MEASURABLE AND NOT WIRED. The claim cannot be made (iron rule #2 — would fabricate an unwired model).**

- ICON-D2 2km does **not exist** in Zeus: no `ForecastSourceSpec` (only `icon_global`/`icon_previous_runs`,
  both GLOBAL diagnostic, `forecast_source_registry.py:205`), no ingest client (`openmeteo_client.py` exposes
  no icon_d2/arome/2km param), **zero rows** in `raw_forecast_artifacts` / `forecast_posteriors` /
  `ensemble_snapshots`, zero hits for `2km|icon_d2|arome` anywhere in `src/`.
- BOTH operands of the comparison lack settled history: ICON-D2 was never ingested, AND the live AIFS+0.1
  posteriors are all future-dated (0 VERIFIED overlap). There is **nothing to evaluate** ICON-2km against
  AIFS+0.1 on EU cells.
- The measurable EU result is the in-family proxy: EU bin-hit **0.168→0.268 (+0.100)** via per-city
  bias-correction — an **EB-bias gain on the AIFS+0.1 posterior**, not a regional-model gain.

**What wiring ICON-D2 into live `replacement_0_1` would require** (none justified by a measured gain, because
no gain can be measured — the data does not exist):
1. a new `ForecastSourceSpec` for `icon_d2` promoted to `entry_primary` (today absent);
2. a real ingest client + `ensemble_snapshots` member storage (today no ICON-D2 path);
3. a calibration bucket — `model_bias_ens`/Platt train on ECMWF-equivalent; ICON-D2 has no Platt bucket →
   silent SHADOW_ONLY;
4. `replacement_forecast_bundle_reader` extended to an ICON family;
5. settlement-validated promotion + capital evidence under the (currently missing) evidence gate
   (REALIGN_0_1_AUTHORITY.md).

---

## (5) Honest caveats

1. **ECMWF-ENS proxy ≠ live AIFS.** Bias *direction* transfers (same ECMWF family) but *magnitude* must be
   re-fit once June+ AIFS posteriors settle (0 settled AIFS cells today).
2. **Live skill is UNMEASURABLE today.** 0 of 193 live `replacement_0_1` posteriors (all target 06-08..10)
   join VERIFIED settlement (latest 06-07); 0 settled traded positions.
3. **Two EB lenses diverge.** The +0.08 headline is the leave-one-out / per-city-causal-mean holdout (the
   legitimate path-independent SKILL ceiling). The *production-faithful* walk-forward with the production guard
   + anti-lookahead self-gate yields only ~+0.001 today, because all `model_bias_ens` training_cutoffs are
   2026-05-29+, so the causal gate fires on almost no settled cell. The headline OVERSTATES what production
   currently delivers; it is the achievable ceiling, not the live delta.
4. **Small-n regions are DIRECTIONAL only:** AF n=76, ME n=121, SAS n=129, OC n=124. EU/NA/AS/SA are the
   statistically meaningful cohorts.
5. **NA F-unit distortion** (see §3) — NA numbers least trustworthy.
6. **Data-provenance hazard applied:** `settlement_value` and ENS members are degC-NUMERIC; `settlement_unit`
   ='F'/`members_unit`='degF' labels are MISLABELED for US cities — all comparisons done in raw-numeric frame
   with the `|center-obs|>20` guard (SUPERIOR_BLEND).
7. **Live authority is flag-gated with NO settlement-evidence gate** (`event_reactor_adapter.py:5348-5353`);
   the registry's disabled/diagnostic tier is bypassed because the live path reads `forecast_posteriors`
   directly and never calls `gate_source`. (A known open risk per REALIGN_0_1_AUTHORITY.md, out of scope here.)
8. **after-cost win-rate / live q_lcb coverage** are cohort-dependent and have 0 live settled cells today — not
   claimed per region.

**Validation note:** the per-region bin-hit table was independently reproduced (not merely copied from the
lenses) via a fresh walk-forward over `ensemble_snapshots` (model_version='ecmwf_ens', lead<36h) joined to
VERIFIED `settlement_outcomes`, with the same 1°C-floor-bin / raw-numeric / |Δ|>20-guard method; direction,
magnitude band, and per-region ranking all reproduced.
