# End-to-End Pipeline & Data-Provenance Audit (locate-once, pre-rebuild)

- Created: 2026-05-28
- Author: autonomous session 866db2ea (Opus) + 5 read-only subagents
- Authority basis: operator directive 2026-05-28 — "从最开始的数据开始……tigge 不同步长校准、opendata 不同步长校准、季节月份……从头到尾把整个 pipeline 定位一次，确保不会再有下一次 rebuild。先让 subagent 找到所有不同类型数据集，以及是否 empirically 正确处理。我们数据很多，利用很少。"
- Scope: READ-ONLY. No writes, no flag, no rebuild. Live = HOLD.
- Sub-audit evidence files (job dir): `audit_datasets.md`, `audit_extraction.md`, `audit_calibration_keying.md`, `audit_inference_selection.md`, `audit_blindspots.md`.
- Confidence tags: **[CONFIRMED]** = verified by me at file:line; **[AGENT]** = sub-agent reported, spot-check before acting.

---

## 0 — TL;DR (decision-grade)

The pipeline has **two foundational data defects in the LIVE (OpenData) product** that sit UPSTREAM of the entire bias/MC debate. Until both are fixed, no rebuild and no live correction can be trusted — they would re-bake the same corruption.

1. **[CONFIRMED] OpenData 3h extraction still uses a 6h step.** Since the 2026-05-07 `mx2t6→mx2t3` cutover, `extract_open_ens_localday.py` still imports `STEP_HOURS=6` (from `tigge_local_calendar_day_common.py:20`) and computes every window as `window_end − 6h`. For a 3-hourly product this mis-attributes the forecast window by 3h, emits negative/garbage step labels, writes `aggregation_window_hours=6` (wrong), and can flip `contributes_to_target_extrema` near local midnight. **The live product's window metadata has been wrong for ~3 weeks.**
2. **[AGENT] ~23,918 OpenData (`open_ens`) forecast JSON files are on disk with ZERO DB rows** — a downloader runs but its ingest backend was never wired. The live product's own forecasts (incl. leads 8–10d that TIGGE lacks) are largely NOT in the DB. This is the literal "数据很多，利用很少."
3. **[AGENT] Calibration mixes products and pools leads.** Platt blends TIGGE + OpenData into one lead-skill slope (`param_B`); `bias_c` pools all lead 0–48h with no lead dimension. Both are mis-keyed relative to how the live product is served.
4. **[AGENT] Inference SELECTION is sound** — single coherent snapshot, freshest contributor wins, no product mixing at read, window-gated. The only inference gap is the known bias `gate_set_hash` wiring (#138).
5. **VERDICT:** the next rebuild must be preceded by fixing #1 and #2 (live data foundation), then re-keying calibration by product (#3). The bias question (sd3 etc.) is downstream and cannot be settled on a corrupted/under-populated OpenData base. HOLD stands.

---

## 1 — Dataset inventory (the map) [AGENT, audit_datasets.md]

| source | product/vars | native step | role | where | rows in ensemble_snapshots_v2 |
|---|---|---|---|---|---|
| TIGGE (ECMWF MARS archive) | `mx2t6` / `mn2t6` | **6-hourly** | historical prior | `51 source data/scripts/tigge_*_download.py` → GRIB `51 source data/raw/` → `extract_tigge_*` → `ingest_grib_to_snapshots.py` | ~769K (`tigge_mx2t6_…`, `tigge_mn2t6_…`) |
| ECMWF OpenData | `mx2t3` / `mn2t3` | **3-hourly** | LIVE product | in-process fetch (ThreadPool) → per-step GRIB2 → `extract_open_ens_localday.py` → ingest | ~22.5K (`ecmwf_opendata_mx2t3_…`) |
| OpenData `open_ens` JSON | ditto | 3-hourly, leads 0–10d | **un-ingested** | `51 source data/` JSON (~23,918 files) | **0** |
| Weather Underground | daily high/low | daily | settlement / obs truth | WU daily API, 52 cities | settlements_v2 |
| GFS / others | — | — | NOT in production | — | 0 |

Two products, two step-lengths (6h vs 3h), disjoint eras (TIGGE ≤2026-05-05, OpenData ≥2026-05-06). This is the root of every downstream "which product / which step" question.

---

## 2 — Stage-by-stage empirical-correctness verdict

| stage | TIGGE (6h) | OpenData (3h) | evidence |
|---|---|---|---|
| download | ✅ ok | ✅ fetch ok BUT `open_ens` JSON not ingested (#2) | audit_datasets |
| **extract daily extreme** | ✅ **[CONFIRMED] correct** — reads GRIB `startStep`/`endStep`, DST-aware local-day, max over windows | ❌ **[CONFIRMED] BROKEN** — `STEP_HOURS=6` applied to 3h product (#1) | audit_extraction; `extract_open_ens_localday.py:399,408,438,503` |
| ingest → snapshots | ✅ ok | ⚠️ ingests mis-windowed metadata; most files never ingested | audit_datasets/blindspots |
| calibrate: bias_c | ⚠️ per (city,season,metric), pools lead 0–48h, no lead dim | ⚠️ same; small n (live era short) | `ens_bias_repo.py:254,281`; `fit_full_transport_error_models.py:433` |
| calibrate: Platt | ❌ **[AGENT] blends TIGGE+OpenData** lead-skill into one `param_B` | ❌ same blend → mis-cal for both | audit_calibration_keying; `audit_refit_proper_scores.py:101` `_load_rows` ignores data_version |
| inference: snapshot selection | ✅ **[AGENT] sound** (freshest FULL_CONTRIBUTOR, one product/call, window-gated) | ✅ sound | `executable_forecast_reader.py:1042–1231`; `forecast_extrema_authority.py:125` |
| inference: bias apply | ⚠️ (city,season,metric,data_version) key; **no `gate_set_hash` gate (#138)** | ⚠️ same | `evaluator.py:3329–3356`; `ens_bias_repo.py:542` |

**Net:** the *selection* logic is good; the *data foundation and calibration keying* are where correctness breaks, and worst on the live product.

---

## 3 — Confirmed/likely defects, ranked

### D1 — [CONFIRMED] OpenData 3h extraction uses 6h step (LIVE product, since 2026-05-07)
`scripts/extract_open_ens_localday.py` imports `STEP_HOURS` from `tigge_local_calendar_day_common.py:20` (`=6`). Uses:
- `:399` `window_start = window_end − timedelta(hours=STEP_HOURS)` → 6h window for a 3h product → start mis-set 3h early.
- `:408` `step_label = f"{step_hours − STEP_HOURS}-{step_hours}"` → e.g. step=3 → label `"-3-3"`.
- `:438,:503` `aggregation_window_hours = STEP_HOURS = 6` → wrong window stamped into the snapshot.
A `.bak_pre_mx2t3_2026_05_07` sibling confirms the param/short_name were switched at cutover but `STEP_HOURS` was not. Consequence: `forecast_window_start_utc` and `contributes_to_target_extrema` are wrong for OpenData; a spurious 6h window crossing local midnight can flag the wrong day; LOW `boundary_ambiguous` split is wrong. **This corrupts the live product's window provenance — the exact class of bug (window/step semantics) that caused prior cold-bias episodes.**

### D2 — [AGENT] OpenData `open_ens` forecasts not ingested (~23,918 JSON, 0 DB rows)
The download pipeline produces ~23,918 JSON files (55 cities, leads 0–10d, HIGH+LOW) but the DB ingest backend was "never wired." If true, the live product is operating on a tiny ingested fraction (~22.5K rows via a different in-process path), and leads 8–10d are entirely absent. **Reconcile against D1's path** — there appear to be TWO OpenData routes (in-process fetch→ingest vs `open_ens` JSON). Spot-check before acting: confirm the file count and that none map to existing snapshot rows.

### D3 — [AGENT] Platt blends TIGGE + OpenData
`audit_refit_proper_scores.py:_load_rows` (and the production Platt fit) read all `data_version` together; `param_B` (lead coefficient) becomes a blended TIGGE+OpenData slope → mis-calibrated for both. Products have different resolution and assimilation recency; their lead-skill curves differ.

### D4 — [AGENT] bias_c pools lead 0–48h (no lead dimension)
`model_bias_ens_v2` PK = (city, season, month, metric, live_data_version); `load_bucket_residuals` pools all `lead_hours≤48`. A 6h-lead and a 48h-lead forecast get the same correction. Defensible only if the within-window lead gradient is empirically small — untested.

---

## 4 — Blind spots (data we have, do not use) [AGENT, audit_blindspots.md]

1. **~23,918 `open_ens` JSON, 0 DB rows** (D2) — biggest; includes leads 8–10d absent from TIGGE.
2. **12z pairs under-leveraged** — 4.7M 12z TIGGE pairs exist, 267 active 12z Platt models, but training is ~9:1 toward 00z. 12z is an independent forecast run barely exploited.
3. **No lead-stratified Platt** — 932 Platt models pool leads 0–7 into one sigmoid; 48M+ pairs span 8 discrete leads. Per-lead would sharpen day0 (obs-informed) and day6–7 (high-σ).
4. **8 schema-ready analytic tables permanently empty** — `forecast_error_profile`, `day0_residual_fact`, `model_skill`, `asos_wu_offsets`, `replay_results`, `regime_correlation_cache`, `tail_stress_scenarios`, `opportunity_fact` (DDL + writers designed, never run).
5. **Auckland** — 22k snapshots + active Platt, **0 settlements** → calibrated but untradeable (settlement-authority config missing).

---

## 5 — Why this prevents the next rebuild

A rebuild fits `residual = forecast − settlement`. If the OpenData `forecast` carries a 3h-vs-6h window error (D1) and most OpenData rows are missing (D2), then:
- the residual population is a TIGGE-dominated, OpenData-mis-windowed mixture (this is exactly the product-lineage finding in `SD3_PRODUCT_LINEAGE_VALIDATION_2026-05-28.md`);
- any bias_c fit on it is fitting the wrong window of the wrong-proportion product;
- a rebuild now would re-bake D1+D2 into new pairs → the (N+1)-th rebuild.

**The fix order must be data-foundation → keying → fit, not fit-first.**

---

## 6 — Ordered remediation (before ANY next rebuild)

1. **Fix D1 (extraction step).** `extract_open_ens_localday.py`: make `STEP_HOURS` product-derived (3 for mx2t3, 6 for mx2t6) — ideally read `startStep`/`endStep` from the GRIB message exactly as the TIGGE extractor does, rather than a constant. Re-extract all OpenData since 2026-05-07. Relationship test: for an mx2t3 message, `window_end − window_start == 3h` and `contributes_to_target_extrema` matches the true local-day overlap.
2. **Resolve D2 (un-ingested OpenData).** Confirm the `open_ens` JSON count, wire/replay the ingest, verify row counts jump and leads 8–10d appear. This also fixes the OpenData small-n that made the product-lineage transfer test thin.
3. **Re-key calibration by product (D3).** Split Platt fit by `data_version` (add `AND data_version=?` to `_load_rows` and the production refit); never blend TIGGE+OpenData lead-skill.
4. **Decide lead stratification (D4).** Empirically measure the within-0–48h lead gradient per bucket; add a `lead_bucket` dimension only where it's material. Per-lead Platt for day0 and day6–7.
5. **Then** re-run the product-stratified Test A / transfer / consensus-sanity / small clean MC from `SD3_PRODUCT_LINEAGE_VALIDATION_2026-05-28.md` §5 on the corrected OpenData base.
6. **Only then** consider a full MC / live correction. For live trading in the meantime, raw OpenData (post-D1/D2 fix) is the defensible forecaster.

---

## 7 — My guesses (ranked)

1. **D1 is the hidden driver of the OpenData "noise" (HIGH).** The product-lineage report found OpenData near-unbiased in the mean but with residual MAE ~1–2 °C and odd per-city signs (Jeddah OpenData +1.72 vs TIGGE −7.35). A 3h-window mis-attribution would inject exactly this kind of city-dependent scatter. Fixing D1 may tighten OpenData materially and change which buckets (if any) need correction.
2. **D2 explains the thin OpenData sample (HIGH).** 169 OpenData rows across 12 cities is implausibly small for ~3 weeks × 55 cities × multiple leads. Most OpenData is un-ingested; once wired, the OpenData-only OOS test becomes viable (currently too small to trust).
3. **After D1+D2, most cities will route to raw with a small, product-correct, lead-aware correction for a few (e.g. SF) (MEDIUM).** The big TIGGE biases are partly window artifacts of the archive product and will not be the live story.
4. **σ under-dispersion (LogLoss 11–25 from the prior report) may be partly D1-induced (MEDIUM)** — wrong windows widen apparent forecast error inconsistently, which a single σ cannot capture. Re-check PIT/ECE after D1.
5. **The 8 empty analytic tables (esp. `day0_residual_fact`, `asos_wu_offsets`, `model_skill`) were designed to answer exactly these questions (LOW-MEDIUM)** — wiring `asos_wu_offsets` would directly address the Jeddah/SF station-identity question from the prior reports.

---

## 8b — Operator-grade verification (I checked the two load-bearing claims myself)

Per provenance discipline (trust but verify agent claims before action):

**D1 — CONFIRMED on the live product.** `ensemble_snapshots_v2` for `ecmwf_opendata_mx2t3_…` stores forecast windows with a **6 h minimum width** (min 6 h, max 24 h over 10,795 rows) and a uniform anomalous `step_horizon_hours=144`. A genuine 3-hourly product should produce 3 h single-step windows. The 6 h floor is the direct footprint of `STEP_HOURS=6` (D1). NOTE: the daily-max VALUE is probably ~preserved (max-of-3h-maxes ≈ daily max), so the corruption is concentrated in **window-attribution metadata** (`forecast_window_*`, `contributes_to_target_extrema`, `boundary_ambiguous`) — which is exactly what calibration cycle/window gating consumes. Severity: high for calibration provenance, lower for the served point forecast.

**D2 — OVERSTATED by the agent; corrected here.** The live 3-hourly product IS ingested and is adequate for current markets:

| data_version | rows | target span | max lead |
|---|---|---|---|
| ecmwf_opendata_mx2t3 (HIGH, 3h, LIVE) | 11,418 | 2026-05-06 → 06-03 | 144 h (6 d) |
| ecmwf_opendata_mn2t3 (LOW, 3h, LIVE) | 9,314 | 2026-05-15 → 06-03 | 144 h |
| ecmwf_opendata_mx2t6 (HIGH, 6h) | 1,342 | 2026-05-08 → 05-17 | 240 h |
| ecmwf_opendata_mn2t6 (LOW, 6h) | 508 | — | 240 h |

The **23,918 un-ingested JSON are the `open_ens_mx2t6` (6 h) variant** (leads to 10 d), a *separate, redundant* extraction — NOT the live 3 h product. So "live OpenData largely un-ingested" is **false**: the live mx2t3 product is present (≈11.4k HIGH rows, leads to 6 d, covering the ≤2-day markets we trade). Corrected D2: the un-ingested 6 h files are genuinely unused but **not** a live blocker; the real D2-class issue is that the **mx2t3 live rows are mis-windowed by D1**, and OpenData history is simply short (live since 2026-05-06), which is why the product-lineage transfer test had small OpenData n — not because of a missing ingest.

**Revised priority:** D1 (fix the 3 h window extraction + re-extract live mx2t3 since 2026-05-06) is the single highest-value fix. D2 downgrades to "wire the 6 h long-lead variant only if we later trade leads >6 d." D3 (Platt product split) and D4 (lead pooling) unchanged.

## 8 — Reproduction / evidence
- Sub-audits: `/Users/leofitz/.claude/jobs/866db2ea/audit_{datasets,extraction,calibration_keying,inference_selection,blindspots}.md`.
- D1 confirm: `grep -n STEP_HOURS "51 source data/scripts/extract_open_ens_localday.py" "51 source data/scripts/tigge_local_calendar_day_common.py"`.
- Product split + transfer: `SD3_PRODUCT_LINEAGE_VALIDATION_2026-05-28.md` + `scripts/product_lineage_transfer.py`.
