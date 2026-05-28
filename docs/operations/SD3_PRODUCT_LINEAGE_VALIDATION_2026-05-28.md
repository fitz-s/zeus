# SD3 Product-Lineage Validation — TIGGE vs OpenData, the Transfer Test

- Created: 2026-05-28
- Author: autonomous session 866db2ea (Opus)
- Authority basis: operator critique 2026-05-28 — "Test A 把 TIGGE 与 OpenData 混在同一 residual population 里验证；它证明的是混合历史 forecast product 的 correction，不是 live OpenData 产品会被改善；下一次 MC 前必须修正这个测试语义。"
- Supersedes the live-readiness claim of `SD3_STATISTICS_VALIDATION_2026-05-28.md` §5.2/§0: Test A is **not** decisive for live.
- Scope: READ-ONLY. No writes, no flag, no promotion. Live = HOLD.
- Evidence: `docs/operations/sd3_validation_evidence/ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv` (1512 rows) + `scripts/product_lineage_transfer.py` → `docs/operations/sd3_validation_evidence/product_stratified_high.csv`.

---

## 0 — TL;DR (decision-grade)

1. **The operator is right: Test A is product-mixed.** The clean ledger is **1343 TIGGE + 169 OpenData** rows, all labelled `source_kind=prior`, all 00Z. Because TIGGE is 89 % of the data, Test A's per-bucket bias ≈ the **TIGGE** bias. It validated correcting a *historical product mixture*, not the *live OpenData* product.
2. **The TIGGE-derived correction HURTS live OpenData on balance.** Applying each bucket's TIGGE bias to its OpenData rows (a clean out-of-product test — the products have disjoint dates): **3 buckets improve, 7 worsen, 1 neutral; mean −1.00 °C, median −0.94 °C.** This reproduces the operator's number exactly.
3. **Root cause: the two products carry DIFFERENT (sometimes opposite-sign) biases.** Jeddah MAM TIGGE −7.35 vs OpenData **+1.72**; Seoul MAM TIGGE −1.62 vs OpenData **+1.31**. The live OpenData ensemble is **already near-unbiased** (OPD raw MAE mostly 1–2 °C). The big biases Test A "found" are largely TIGGE reanalysis artifacts that do not exist in the live operational product.
4. **Only ONE bucket has a real, product-consistent live bias: San Francisco MAM** (TIGGE −3.48, OpenData −4.55; OPD raw MAE 4.55 → 1.36 corrected). Possibly weak candidates: Paris MAM, NYC MAM, Jakarta SON (small n, modest gain).
5. **VERDICT: the current evidence cannot authorize ANY live OpenData correction except (provisionally) SF MAM. For live OpenData, RAW dominates almost everywhere.** This is a 4th, deeper failure mode than the three in the prior report: **product-lineage mixing — a data-provenance violation (Constraint #4).** HOLD stands.

---

## 1 — The product mix (the hidden defect)

```
product split (ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv, 1512 rows):
  tigge_mx2t6_local_calendar_day_max_v1        : 1343 rows   dates 2025-01-22 .. 2026-05-05
  ecmwf_opendata_mx2t3_local_calendar_day_max_v1:  169 rows   dates 2026-05-06 .. 2026-05-26
  source_kind = 'prior' for ALL rows            (mislabelled — OpenData rows are LIVE product, not prior)
  cycle = '00' for ALL rows                      (00Z-strict OK)
```

Two consequences:
- **Semantic bug in the ledger:** `source_kind='prior'` is wrong for the 169 OpenData rows. The schema cannot distinguish the historical prior (TIGGE) from the live product (OpenData). Must become `prior_tigge` / `live_opendata` / `paired_delta`.
- **Test A is product-mixed OOS.** Date-blocking does not separate products. With TIGGE = 89 % of rows, every per-bucket `bias_c` is essentially the TIGGE bias. The disjoint date ranges (TIGGE ends 2026-05-05, OpenData starts 2026-05-06) make this a clean natural experiment — which is what lets the transfer test below be trusted.

---

## 2 — The transfer test (the decisive result)

For each (city, season) bucket with OpenData rows: fit `bias_tigge` on TIGGE rows only, apply to the OpenData rows only (disjoint dates ⇒ out-of-product), measure OpenData MAE before/after. Also OpenData-only leave-one-date-out (does the LIVE product have the same bias?).

| city | season | n_T | n_O | bias_TIGGE | bias_OPD | OPD raw MAE | OPD +TIGGEbias MAE | OPD LOO MAE | transfer Δ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| San Francisco | MAM | 40 | 14 | −3.48 | −4.55 | 4.55 | **1.62** | 1.36 | **+2.92** | TIGGE_HELPS |
| Paris | MAM | 44 | 17 | −0.95 | −1.33 | 1.36 | 0.93 | 0.96 | +0.43 | TIGGE_HELPS |
| NYC | MAM | 151 | 18 | −0.17 | −0.76 | 1.21 | 1.13 | 1.10 | +0.07 | TIGGE_HELPS (marginal) |
| London | MAM | 154 | 16 | −0.22 | +0.03 | 0.97 | 0.94 | 1.04 | +0.03 | NEUTRAL |
| Austin | MAM | 40 | 15 | +0.45 | −0.27 | 1.38 | 1.49 | 1.50 | −0.11 | TIGGE_HURTS |
| Shanghai | MAM | 52 | 16 | −3.15 | −0.84 | 1.37 | 2.32 | 1.15 | −0.94 | TIGGE_HURTS |
| Istanbul | MAM | 34 | 13 | −2.54 | −0.48 | 0.97 | 2.06 | 0.91 | −1.09 | TIGGE_HURTS |
| Seoul | MAM | 57 | 15 | −1.62 | **+1.31** | 1.96 | 3.20 | 1.59 | −1.23 | TIGGE_HURTS |
| Jakarta | SON | 33 | 12 | −3.84 | −1.08 | 1.24 | 2.77 | 0.85 | −1.53 | TIGGE_HURTS |
| Busan | MAM | 33 | 16 | −3.90 | −0.12 | 1.30 | 3.78 | 1.37 | −2.49 | TIGGE_HURTS |
| Jeddah | MAM | 25 | 17 | −7.35 | **+1.72** | 2.05 | 9.06 | 1.57 | −7.02 | TIGGE_HURTS |

```
TRANSFER (TIGGE bias -> OpenData rows): buckets=11  wins=3  losses=7  neutral=1
  mean improvement = -1.00 C   median = -0.94 C      (>0 = TIGGE bias helps live OpenData)
```

(Buckets with 0 OpenData rows — London DJF/JJA/SON, NYC DJF/JJA/SON, Seoul DJF, Paris DJF, Hong Kong MAM — cannot be transfer-tested at all; their Test A "wins" rest entirely on TIGGE and are unproven for live.)

### What this proves
- **The OpenData live product is already good.** OPD raw MAE is 1.0–2.0 °C for 10 of 11 buckets. The single exception is San Francisco (4.55). The market's payout truth is tracked well by the *uncorrected* live ensemble for almost every city tested.
- **TIGGE biases do not transfer.** For 7 buckets the TIGGE correction pushes the (already-good) OpenData forecast *away* from settlement. Jeddah is the extreme: TIGGE says −7.35 (cold), OpenData is actually +1.72 (slightly warm) — opposite sign — so the TIGGE correction quadruples the error (2.05 → 9.06).
- **Only SF MAM is a genuine, product-consistent live bias.** Both products agree (TIGGE −3.48, OpenData −4.55) and OpenData-only LOO confirms it (4.55 → 1.36). SF is the one place a live correction is currently defensible.

---

## 3 — Re-diagnosis: this is failure mode #4 (data provenance)

The prior report decomposed the old sd3 into DATA (12z/unit), MATH (no gate), METHOD (no OOS-vs-settlement). The product mix is a distinct, deeper one:

> **#4 — PROVENANCE: the training residuals come from a DIFFERENT forecast product than the one served live.** Code correct, math correct, method correct — and still wrong, because the data's *source* does not match the inference-time source. This is the canonical Constraint #4 failure (the London-DST class): every agent "knew" the math; none questioned which product the inherited residuals belonged to.

It also re-frames earlier conclusions:
- "Test A is decisive" → decisive only for the TIGGE/mixed historical bias. **Not** for live.
- "Jeddah is a station-identity problem" → partly, but more fundamentally a **product-transfer failure**: OpenData Jeddah is fine (MAE 2.05); only the TIGGE-derived correction breaks it.
- "clean biases are physically plausible" → the *mixed* biases are plausible numbers but belong to the wrong product for live use.

---

## 4 — What is now proven / not proven

**Proven:**
- Old sd3 (unconditional, contaminated) must be discarded (prior report, unchanged).
- The ledger silently mixes TIGGE (89 %) + OpenData (11 %); `source_kind` mislabels OpenData as prior.
- TIGGE-derived corrections do not transfer to live OpenData (mean −1.00 °C; 7/11 worse).
- The live OpenData ensemble is near-unbiased for 10/11 tested buckets (MAE 1–2 °C).
- SF MAM has a real, product-consistent live bias (~−4 °C).

**Not proven (blocks live):**
- Any live OpenData correction other than (provisionally) SF MAM.
- OpenData-only biases for the other buckets are small and rest on n = 12–18 in-sample LOO — not productionizable.
- Bin-level proper score, σ/PIT, and forecast-consensus sanity — all still open from the prior report.

---

## 5 — Corrected path before any next MC (operator's plan, adopted)

No full MC. Do **not** build a live correction from the current mixed Test A.

1. **Fix the ledger schema.** `source_kind ∈ {prior_tigge, live_opendata, paired_delta}`; add `forecast_window_start_local`, `forecast_window_end_local`, `target local-day interval`, `horizon_profile` so each row proves it forecasts the same local-day HIGH the settlement measures, and which product it is.
2. **Re-run Test A stratified by product:** (A1) TIGGE-only OOS, (A2) OpenData-only OOS, (A3) TIGGE→OpenData transfer. A correction may touch live **only** if it wins A2 or A3.
3. **Per-product candidate set** per city×season×cycle/horizon: `raw_opendata`, `scale_only_opendata`, `opendata_live_bias`, `tigge_prior_bias`, `tigge→opd_shrunk_bias`, cluster/global fallback, `no-route`. Default = raw_opendata.
4. **Cycle/horizon match:** the correction key must include cycle/horizon, or apply only to live snapshots of the same cycle the evidence was built on (00Z here). Never apply a 00Z-trained bias to a 12Z live forecast.
5. **Forecast-consensus sanity gate** (cheap, pre-live): for upcoming targets, require `|corrected_mean − consensus| ≤ |raw_mean − consensus| + 0.5 °C` vs Open-Meteo / ECMWF / GFS. This would have blocked old sd3.
6. **Small clean-candidate MC** (12 cities, n_mc=1000): raw vs selected candidate, LogLoss/RPS/Brier/P(actual)/PIT/ECE + consensus gap. Only on pass → full MC.

Acceptance to apply a correction live: wins A2 or A3 OOS **AND** bootstrap LCB>0 **AND** passes consensus sanity **AND** wins ≥2/3 bin proper scores in the small MC **AND** retains residual evidence. Else raw / scale-only.

---

## 6 — My guesses (ranked)

1. **For live OpenData, raw is the right model for ~all buckets except SF MAM (HIGH confidence).** The live product is well-calibrated in the mean; the apparent need for correction was a TIGGE illusion. Expect the stratified re-run to route almost everything to raw.
2. **SF MAM is real and worth correcting (~−4 °C), but verify the station** (SF microclimate / which WU station) before sizing — the magnitude is large and coastal SF is notoriously station-sensitive.
3. **OpenData has too little history to fit per-bucket live biases yet (HIGH).** 12–18 rows/bucket since 2026-05-06. The honest near-term policy is raw + a wide σ; revisit live-bias correction after more OpenData accumulates, or use a shrinkage estimator (TIGGE prior shrunk toward OpenData) only where the two agree (SF).
4. **σ is still likely mis-calibrated (from prior report: none-family LogLoss 11–25).** Independent of bias; check PIT/ECE before live regardless of the bias decision.
5. **The mix may also contaminate LOW** (not tested here) — LOW had 8 cities; re-run the product split there too.

---

## 7 — Reproduction
```bash
WT=/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
.venv/bin/python "$WT/scripts/product_lineage_transfer.py"   # reads the committed ledger CSV
# -> docs/operations/sd3_validation_evidence/product_stratified_high.csv
```
