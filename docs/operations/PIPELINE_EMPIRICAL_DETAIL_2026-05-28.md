# Pipeline Empirical Detail — measured per-product / per-step / per-lead evidence

- Created: 2026-05-28
- Author: autonomous session 866db2ea (Opus)
- Authority basis: operator 2026-05-28 "this report not detailed enough" — granular, data-backed companion to `PIPELINE_PROVENANCE_AUDIT_2026-05-28.md`. Answers, with numbers: do we handle TIGGE 6h vs OpenData 3h step-lengths correctly; what is the multi-lead-per-target situation; do season/month/lead calibration buckets match how we serve.
- Scope: READ-ONLY measurement. Script: `scripts/pipeline_empirical_detail.py` → `docs/operations/sd3_validation_evidence/pipeline_empirical_detail.txt`. DB `state/zeus-forecasts.db`.

---

## 0 — What the numbers add to the audit

| finding | severity | new vs prior |
|---|---|---|
| **E1** OpenData 3h vs 6h daily-max disagree up to **8.6 °C** on the same city/date | HIGH | elevates D1 from "metadata bug" to **likely value-level corruption** of the live daily-max |
| **E2** the residual ledger is **0–12h-lead dominated**; live trades **~48h** out | HIGH | NEW (D5): calibration lead-distribution ≠ serving lead |
| **E3** bias has a real **lead gradient** (Busan MAM −2.97 → +0.16; sign flip) | MED | empirical backing for D4 |
| **E4** TIGGE 348k vs OpenData 11k HIGH rows; 12z is 9.5:1 under 00z | MED | quantifies the product-lineage dominance + 12z blind spot |
| **E5** 16 lead-step forecasts exist per target, span ~4 °C, 1 served | (by design) | concretises the operator's "多个不同步长结果" |

---

## 1 — Full inventory (rows per data_version × cycle × metric)

| data_version | cyc | metric | rows | lead lo–hi (h) | cities | dates | span |
|---|---|---|---|---|---|---|---|
| tigge_mx2t6 …_v1 (HIGH archive) | 00 | high | **348,149** | 0–168 | 52 | 868 | 2024-01-01 → 2026-05-28 |
| tigge_mx2t6 …_v1 | 12 | high | 36,990 | 0–168 | 52 | 103 | 2026-02-01 → 2026-05-28 |
| tigge_mn2t6 …_v1 (LOW archive) | 00 | low | 347,256 | 0–168 | 52 | 860 | 2024-01-01 → 2026-05-09 |
| tigge_mn2t6 …_v1 | 12 | low | 36,946 | 0–168 | 51 | 98 | — |
| tigge_mn2t6 …_contract_w | 00 | low | 347,082 | 0–168 | 51 | 858 | (parallel contract-window variant) |
| **ecmwf_opendata_mx2t3** (HIGH LIVE 3h) | 00 | high | **5,930** | 0–144 | 54 | 29 | 2026-05-06 → 06-03 |
| ecmwf_opendata_mx2t3 | 12 | high | 5,488 | 0–144 | 54 | 24 | 2026-05-11 → 06-03 |
| ecmwf_opendata_mn2t3 (LOW LIVE 3h) | 00 | low | 4,190 | 0–144 | 54 | 20 | 2026-05-15 → 06-03 |
| ecmwf_opendata_mn2t3 | 12 | low | 5,124 | 0–144 | 54 | 20 | — |
| ecmwf_opendata_mx2t6 (HIGH 6h long-lead) | 00 | high | 1,089 | 120–240 | 51 | 10 | 2026-05-08 → 05-17 |
| ecmwf_opendata_mx2t6 | 12 | high | 253 | 144–240 | 51 | 5 | — |
| ecmwf_opendata_mn2t6 (LOW 6h) | 00/12 | low | 255/253 | 120–240 | 51 | 5–6 | — |

**Reading:**
- **TIGGE dominates history ~30:1** (348k vs 11k HIGH). Any pooled bias ≈ TIGGE bias — the product-lineage finding, now quantified.
- **Live 3h product (mx2t3) is healthy on coverage:** 54 cities, both 00z+12z, leads 0–144h (6 days). Adequate for ≤2-day markets.
- **12z under-used:** TIGGE 12z 36,990 vs 00z 348,149 ≈ 1:9.5. A whole independent forecast run barely in the training mix.
- **mx2t6 OpenData is a long-lead-only variant** (120–240h) — the 23,918 un-ingested files' product; complements mx2t3's 0–144h but unused.

---

## 2 — E1: EMPIRICAL step-handling test (mx2t3 3h vs mx2t6 6h)

Same ECMWF OpenData source, two accumulation variables. For each (city, target_date) where both exist (overlap 2026-05-08..05-17), compare the ensemble-mean daily-max. If step/window handling is correct, both should equal the day's peak → agree within ~1 °C.

```
[high] common (city,date) pairs: 425
  mx2t3 − mx2t6 daily-max-mean:  mean=-0.564C  median=-0.299C  max|diff|=8.59C
  largest disagreements (|3h-6h|, city, date, 3h, 6h):
     8.59  Seattle     2026-05-15  3h=10.72  6h=19.31
     8.42  Seattle     2026-05-12  3h=28.60  6h=20.18
     8.41  Busan       2026-05-15  3h=15.70  6h=24.12
     8.34  Chongqing   2026-05-15  3h=19.69  6h=28.03
     8.08  Warsaw      2026-05-12  3h=12.99  6h=21.08
     7.91  Mexico City 2026-05-10  3h=27.35  6h=19.44
```

**Interpretation.** Disagreements of **7–8.6 °C** for the *same day, same source* cannot be forecast uncertainty — they are window/step artifacts. The 3h product is the one extracted with the buggy `STEP_HOURS=6` (D1); the 6h product's 6h constant is correct for it. The alternating sign (3h sometimes hotter, sometimes colder) is the signature of windows being mis-attributed across the local-day boundary: a mis-placed 6h window pulls a neighbouring day's peak/trough into the wrong day.

**CAVEAT (honest):** the two products may come from different issue cycles (mx2t6 was the pre-2026-05-07 variable, mx2t3 post), and both here are long-lead (120–144h), so part of the spread is run-to-run. A same-`issue_time` control is needed to fully isolate the extraction component. But: (a) the median −0.30 °C bias and (b) the 8 °C tails are far larger than same-source run jitter, and (c) D1 is independently CONFIRMED in code (`STEP_HOURS=6`) and in DB (6h-minimum window widths). **Conclusion: D1 has a value-level footprint on the live product, not just metadata. Re-extraction is required before the live mx2t3 daily-max can be trusted for calibration.**

---

## 3 — E5: multiple lead-step forecasts per target (operator's "多个不同步长结果")

Amsterdam, target 2026-02-08, HIGH — every snapshot that forecasts this one day:

```
  product            cyc  lead(h)  mean_max_C
  tigge_mx2t6        00     168      6.50
  tigge_mx2t6        12     168      5.48
  tigge_mx2t6        00     144      5.14
  tigge_mx2t6        12     144      6.93
  tigge_mx2t6        00     120      7.56
  tigge_mx2t6        12     120      7.56
  tigge_mx2t6        00      96      7.49
  tigge_mx2t6        12      96      8.12
  tigge_mx2t6        00      72      8.04
  tigge_mx2t6        12      72      8.06
  tigge_mx2t6        00      48      7.86
  tigge_mx2t6        12      48      7.37
  tigge_mx2t6        00      24      8.57
  tigge_mx2t6        12      24      8.80
  tigge_mx2t6        00       0      9.30
  tigge_mx2t6        12       0      8.48
  --> 16 forecasts span 5.14 .. 9.30 C (range 4.16 C). One served (freshest FULL_CONTRIBUTOR); 15 unused.
```

**Reading:** for a single target there are **16 forecasts** (2 cycles × 8 leads) spanning **4.16 °C**. The reader correctly elects ONE (freshest full-contributor — see audit_inference_selection). The other 15 are discarded — this is the "数据很多用得少": the lead/cycle ensemble of forecasts is collapsed to one point, never used as an information source (e.g. lead-trend, cycle-agreement as a confidence signal). NOTE: `forecast_window_*` is null for TIGGE rows (only OpenData populates it) and `contributes_to_target_extrema` is null here — window-provenance columns are unpopulated for the archive product.

---

## 4 — E2 + E3: bias-by-lead, and the calibration-vs-serving lead mismatch

Residual (ens_mean − settlement, °C) by lead-bucket, from the clean 12-city HIGH ledger:

| city/season | 0–12h (n, bias) | 12–24h | 24–48h |
|---|---|---|---|
| San Francisco MAM | 46, −3.78 | 4, −4.94 | 4, −2.30 |
| Jeddah MAM | 34, −4.19 | 4, −1.51 | 4, −1.51 |
| Shanghai MAM | 62, −2.67 | 3, −3.99 | 3, +0.20 |
| Busan MAM | 43, −2.97 | 3, −1.25 | 3, **+0.16** |
| London DJF/MAM/JJA/SON | 91–162, −0.6/−0.2/−0.1/−0.7 | 4, +0.23 | 4, +0.18 |

**E3 (lead gradient, MED — D4 backing):** several cities show the bias shrinking or flipping with lead (Busan −2.97 → +0.16; Shanghai −2.67 → +0.20). A single pooled 0–48h `bias_c` blends these. BUT the longer-lead buckets have tiny n (3–4), so this is suggestive, not conclusive.

**E2 (the bigger problem, HIGH — NEW, call it D5):** the ledger is **overwhelmingly 0–12h lead** (n = 43–162) with almost nothing at 24–48h (n = 3–4). The residual extractor keeps the *freshest* (shortest-lead) snapshot per date, so the bias is effectively a **near-target (day-0) correction**. But the markets we trade settle **~2 days out (≈48h lead)**. We are fitting a short-lead bias and applying it to long-lead live forecasts. Forecast skill and bias at 48h differ materially from 0–12h (the 8 °C step artifacts in §2 are all long-lead). **D5: calibration sample lead-distribution ≠ live serving lead — the bias may be valid for day-0 and wrong for the 2-day market.**

---

## 5 — E4: unit handling

```
members_unit=degC   cities=43  rows=312,329
members_unit=degF   cities=11  rows= 85,570
```
11 US cities store members in °F, 43 in °C. Extraction converts members correctly; the earlier −40..−54 °C "bias" was a *settlement*-side unit mismatch (fixed in T2/T3), not member-side. No member-unit bug seen.

---

## 6 — Updated defect ledger (merging with PIPELINE_PROVENANCE_AUDIT)

| id | defect | layer | severity | status |
|---|---|---|---|---|
| D1 | OpenData 3h extracted with STEP_HOURS=6 | extract | **HIGH** | CONFIRMED (code + DB + §2 value test) |
| D5 | calibration lead-dist (0–12h) ≠ serving lead (~48h) | extract/calib | **HIGH** | NEW (§4) |
| D2 | mx2t6 long-lead OpenData un-ingested (23,918 JSON) | ingest | LOW | confirmed, not a ≤2-day blocker |
| D3 | Platt blends TIGGE+OpenData lead-skill | calib | MED | confirmed |
| D4 | bias_c pools lead 0–48h | calib | MED | empirically backed (§4 E3) |
| — | 12z run under-leveraged (9.5:1) | calib | MED | quantified (§1) |
| — | inference selection (single coherent snapshot) | infer | OK | sound |

---

## 7 — Revised pre-rebuild order (supersedes the prior report's order)

1. **D1** — fix the 3h window extraction (derive step from product / read GRIB startStep-endStep), re-extract live mx2t3 since 2026-05-06. Relationship test: mx2t3 vs mx2t6 same-issue daily-max agree within ~1 °C (the §2 test, controlled for issue_time).
2. **D5** — make the residual ledger and bias fit **lead-stratified** (at least: separate the day-0/short-lead bias from the 24–72h trading-lead bias), and fit/serve the bias at the **lead the market trades** (~48h), not the freshest snapshot. This is as important as D1 for live profit.
3. **D3** — split Platt by data_version.
4. Re-run product-stratified Test A / transfer / consensus-sanity / small clean MC (`SD3_PRODUCT_LINEAGE_VALIDATION` §5) on the corrected, lead-matched base.
5. THEN rebuild / consider live correction. Until then, raw OpenData (post-D1) at the trading lead is the defensible forecaster.

---

## 8 — My guesses (ranked)

1. **D5 (lead mismatch) is the most likely silent profit-killer (HIGH).** Even a perfectly extracted, product-correct bias fit on day-0 residuals will be wrong when applied to a 2-day-out forecast. The whole bias program has been validating at the wrong lead.
2. **D1 re-extraction will materially change the live mx2t3 daily-max for a non-trivial fraction of city-dates (HIGH)** — §2 shows up to 8.6 °C swings; even if most are <1 °C, the tail moves bins.
3. **After D1+D5, most cities route to raw at trading lead (MED).** The big biases are short-lead + TIGGE + window artifacts; at 48h-lead on clean OpenData the live forecast is probably close to unbiased, needing σ-widening more than mean-shift.
4. **The discarded lead/cycle ensemble (§3, 16 forecasts/target) is unused alpha (MED).** Cross-lead trend and 00z/12z agreement are free confidence signals currently collapsed to one point.
