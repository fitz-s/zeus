# SD3 Statistics Validation — Full Methodology, Results, Failure-Mode Diagnosis

- Created: 2026-05-28
- Author: autonomous session 866db2ea (Opus)
- Authority basis: operator directive 2026-05-28 — "继续完成 sd3 测试 + 完整文档报告；正确拿到统计数据是开启 live 最重要的一步；写出怎么做/做了什么/结果是什么；让我能判断是数据没处理对、数学漏了一步、还是统计方法错了；写下你的猜测。"
- Scope: READ-ONLY analysis. No DB writes, no flag changes, no promotion. `full_transport_live_enabled=false` (shadow). Live trading remains HOLD.
- Evidence artifacts (this session, all committed + reproducible):
  - `scripts/build_ens_residual_evidence.py` — clean residual-evidence ledger builder (T2/T3).
  - `scripts/oos_bias_crossfit.py` — Test A, the OOS bias cross-fit (decisive). Output: `docs/operations/sd3_validation_evidence/phase2_oos_bias_high.csv`.
  - `scripts/score_raw_vs_sd3_bins.py` — Test B, bin-level proper-score raw vs contaminated-sd3. Output: `docs/operations/sd3_validation_evidence/score_12city_high.csv`.
  - `scripts/score_error_model_candidates.py` + `tests/test_t4_selection_rule_invariants.py` (10/10 green) — the accept-gate.
  - `docs/operations/sd3_validation_evidence/ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv` — the clean per-event ledger (12 cities, HIGH).
  - NOTE: `oos_bias_crossfit.py` / `score_raw_vs_sd3_bins.py` carry hardcoded absolute I/O paths from the session run; treat as evidence-of-method. Productionizing (CLI args) is part of the unbuilt T4 scoring path.

---

## 0 — TL;DR (decision-grade)

1. **The clean bias correction is REAL and generalizes out-of-sample — but only for some buckets.** Blocked-by-date 5-fold OOS test on 12 cities (HIGH): **11 / 20 (city×season) buckets** show the correction beating raw with a bootstrap lower-bound > 0 (LCB>0). Examples: San Francisco MAM RMSE 4.06→1.55 °C, Jakarta SON 3.45→1.51, NYC DJF 2.66→1.73, Shanghai MAM 3.44→2.27, Busan MAM 3.64→2.50.
2. **For near-zero-bias buckets the correction LOSES OOS** — London MAM (bias −0.20) and JJA (−0.12), Austin MAM (+0.26): RMSE unchanged or slightly worse. The accept-gate correctly keeps these on **raw**.
3. **Against the SETTLEMENT (the correct arbiter), the correction direction is right — this REVERSES the earlier open-meteo finding.** Bin-level proper scores (Test B) show the cold-bias correction beats raw on LogLoss/Brier for every city. The earlier "sd3 is worse" verdict was measured vs open-meteo (another forecast); vs the payout truth, warming the cold-biased ensemble helps. BUT Test B's magnitudes are untrustworthy (not date-blocked; the `none` baseline is pathologically over-peaked, LogLoss 11–25; contaminated sd3 over-corrects on RPS) — so Test A (clean, OOS) is the decisive evidence and Test B only confirms direction. See §5.3.
4. **The OLD shipped sd3 was wrong for three independent reasons** (one per layer the operator asked about): a DATA-processing bug, a missing MATH (gating) step, and a missing statistical METHOD (no date-blocked OOS vs settlement). All three are now fixed or gated. See §6.
5. **NEW red flag surfaced: the production MC spread (σ) looks mis-calibrated.** The stored `none` distributions assign near-zero probability to the settled bin (LogLoss up to 25) — over-confident/under-dispersed. This must be checked before live independently of the bias question (§7, hyp. 3).
6. **VERDICT: HOLD remains correct; the redesign is validated in direction and at the temperature layer.** The per-bucket, OOS-gated, clean-extraction approach produces a defensible model where the old unconditional `full_transport` did not. The remaining blockers before live: (a) **clean-candidate bin-level proper-score** re-MC (§7, hyp. 2), (b) **σ/PIT calibration** (§7, hyp. 3), (c) **Jeddah station-identity** (§7, hyp. 1).

---

## 1 — Background: what is being tested and why

**sd3** = the `full_transport_v1` error-model family at gate `deabf8f64bde27b7`. It assigns each (city × metric × season) bucket a `bias_c` (a temperature offset) and `residual_sd_c` (a spread), fit from historical (forecast, settlement) residuals. At inference the Monte-Carlo sampler shifts the ensemble member maxes by `−bias_c` before binning into market probabilities.

The operator's hard question (verbatim): *"bias_c 的训练目标没有被证明等于 live 要修正的目标"* — has it been **proven**, on held-out data, that subtracting `bias_c` makes the forecast a better predictor of the **settlement** (the thing the market pays out on)? Until this session, no. The earlier "evidence" (before/after vs open-meteo) compared the forecast to *another forecast*, not to the settlement the market actually uses.

This report builds that proof properly, end to end, and diagnoses exactly where the old pipeline broke.

---

## 2 — The pipeline (where each statistic lives)

```
 ensemble members (per city, per cycle, per target_date)         [ensemble_snapshots_v2.members_json]
        │  daily-extreme extraction (HIGH=local afternoon max)
        ▼
 ensemble_mean_c  ── minus ──  settlement_value_c                 [settlements_v2.settlement_value]
        │                              (the market truth)
        ▼
 residual_c = ensemble_mean_c − settlement_c                      ← the training signal for bias_c
        │  aggregate per (city, season)
        ▼
 bias_c = mean(residual_c) ,  residual_sd_c = std(residual_c)     [model_bias_ens_v2]
        │  MC: shift members by −bias_c, widen by residual_sd_c
        ▼
 p_raw vector over market temperature bins                        [calibration_pairs_v2.p_raw]
        │  score against which bin the settlement fell in
        ▼
 proper score (LogLoss / RPS / Brier) vs settlement              ← the ONLY valid arbiter
```

Two distinct statistics matter, at two layers:
- **Temperature layer**: does `(ensemble_mean − bias_c)` predict `settlement_c` better than `ensemble_mean` alone? (RMSE / MAE.) — **Test A**, §5.2.
- **Bin / market layer**: does the bias-corrected `p_raw` distribution score better against the settled bin? (LogLoss / RPS / Brier.) — **Test B**, §5.3.

Test A is the necessary first gate: if the correction doesn't even reduce temperature error OOS, the bin-level score cannot legitimately improve. Test B is the sufficient gate for live (a temperature win can still fail to move enough probability mass across wide market bins).

---

## 3 — Two DATA-processing bugs the old sd3 baked in (already found, recapped)

These corrupted the training signal `residual_c` itself, so every downstream statistic inherited the error.

### Bug 1 — 12Z nighttime-window contamination (HIGH)
The TIGGE `mx2t6` 12Z snapshot covers 12Z→12Z (local night) and misses the afternoon HIGH peak → a fabricated −3 to −4 °C cold bias. The old loader *preferred* 0Z but **fell back** to 12Z, and trusted a `contributes_to_target_extrema` flag that is demonstrably unreliable (Jeddah/SF carry `contributes=1` on BOTH cycles). Per-cycle residuals confirmed: Seoul 0z −0.94 vs 12z −4.97; Jakarta 0z −3.10 vs 12z −7.83.
**Fix (T2/T3):** cycle-strict extraction — HIGH accepts **0Z only**, LOW **12Z only**. "Proof or no sample", uniform, not metadata-trusted.

### Bug 2 — settlement unit (°F vs °C)
US-city settlements are stored in °F (`members_unit='degF'`). A path converted ensemble members °F→°C but left `settlement_value` in °F → fabricated −40 to −54 °C "biases" for Austin/NYC/SF.
**Fix (T2/T3):** normalize the settlement with the same `members_unit` before differencing.

### Clean bias after both fixes (12 cities, HIGH, 0Z-strict, unit-normalized)

| bucket | clean bias_c | n | old sd3 bias_c |
|---|---|---|---|
| San Francisco MAM | −3.76 | 54 | −3.38 (and −51 raw pre-unit-fix) |
| Jeddah MAM | −3.68 | 42 | −6.84 |
| Jakarta SON | −3.10 | 45 | −3.80 |
| Busan MAM | −2.67 | 49 | −3.92 |
| Shanghai MAM | −2.60 | 68 | −3.15 |
| NYC DJF | −2.04 | 76 | (−45 raw pre-fix) |
| Istanbul MAM | −1.97 | 47 | −2.37 |
| Paris DJF / MAM | −1.43 / −1.05 | 9 / 61 | — |
| Seoul MAM / DJF | −1.01 / −0.88 | 72 / 74 | −1.66 |
| London SON/DJF/MAM/JJA | −0.69/−0.60/−0.20/−0.12 | 91/115/170/92 | — |
| Hong Kong MAM | +0.69 | 41 | +0.63 |
| Austin MAM | +0.26 | 55 | (−54 raw pre-fix) |
| NYC JJA / SON / MAM | +0.48 / −0.51 / −0.23 | 92/90/169 | — |

The clean biases are physically plausible (≤ ~3.8 °C) where the old ones were not (−6.84, −45, −54). The data-processing layer is now sound. The question becomes: are even the clean biases worth applying? → Test A.

---

## 4 — Method

### 4.1 Test A — blocked-by-date OOS bias cross-fit (the decisive gate)
For each (city, season) bucket on the clean ledger:
1. K-fold split **by `target_date`** (5-fold if ≥20 dates, else 3-fold). All events on a date share a fold → no same-day leakage.
2. For each test fold: `bias_hat = mean(residual_c)` over the **other** folds (never the test fold).
3. OOS error per test event: raw `|residual_c|` vs corrected `|residual_c − bias_hat|`.
4. Aggregate `RMSE_raw` vs `RMSE_corr`, `MAE_raw` vs `MAE_corr`.
5. Paired bootstrap (3000 resamples) of `(|raw| − |corr|)` → `LCB` = 5th percentile.
6. **Verdict:** `CORRECTION_WINS_OOS` iff `RMSE_corr < RMSE_raw` AND `LCB > 0`; else `improves_but_LCB≤0` or `RAW_WINS`.

This isolates a *real, stable offset* from *noise*: a true offset survives the held-out folds and the bootstrap; zero-mean noise does not (the estimated `bias_hat` is ~0 OOS and can only add estimation variance).

### 4.2 Test B — bin-level proper score, raw vs (contaminated) sd3
One distribution per `decision_group_id`; multinomial Brier / LogLoss / RPS / P(actual) vs the settled bin; per-city aggregate. Reuses the production primitives in `scripts/audit_refit_proper_scores.py`. NOTE: the stored `full_transport_v1` pairs were generated under the **contaminated** extraction, so Test B measures *the model that was almost shipped*, not the clean candidate.

### 4.3 The accept-gate (the antibody)
`scripts/score_error_model_candidates.py::choose_candidate` (10/10 relationship tests green) encodes the operator's rule: a correction enters the model ONLY if it beats raw on ≥2/3 proper scores AND bootstrap LCB(improvement)>0 AND no catastrophic cohort regression; otherwise raw identity. This makes "promote a correction that did not beat raw OOS" structurally unwritable.

---

## 5 — Results

### 5.1 Clean bias ledger — see §3 table.

### 5.2 Test A — OOS bias cross-fit (12 cities, HIGH) — **the headline**

| city | season | n | dates | k | bias_c | RMSE_raw | RMSE_corr_OOS | MAE_raw | MAE_corr | LCB(|Δ|) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| San Francisco | MAM | 54 | 54 | 5 | −3.76 | 4.06 | **1.55** | 3.76 | 1.29 | +2.10 | **CORRECTION_WINS_OOS** |
| Jakarta | SON | 45 | 45 | 5 | −3.10 | 3.45 | **1.51** | 3.15 | 1.21 | +1.47 | **CORRECTION_WINS_OOS** |
| NYC | DJF | 76 | 76 | 5 | −2.04 | 2.66 | **1.73** | 2.31 | 1.35 | +0.69 | **CORRECTION_WINS_OOS** |
| Shanghai | MAM | 68 | 68 | 5 | −2.60 | 3.44 | **2.27** | 2.80 | 1.83 | +0.60 | **CORRECTION_WINS_OOS** |
| Busan | MAM | 49 | 49 | 5 | −2.67 | 3.64 | **2.50** | 3.05 | 2.07 | +0.50 | **CORRECTION_WINS_OOS** |
| Paris | DJF | 9 | 9 | 3 | −1.43 | 1.58 | **0.69** | 1.43 | 0.48 | +0.37 | **CORRECTION_WINS_OOS** (small n) |
| Paris | MAM | 61 | 61 | 5 | −1.05 | 1.46 | **1.01** | 1.25 | 0.77 | +0.31 | **CORRECTION_WINS_OOS** |
| Istanbul | MAM | 47 | 47 | 5 | −1.97 | 2.69 | **1.86** | 2.14 | 1.51 | +0.25 | **CORRECTION_WINS_OOS** |
| Seoul | MAM | 72 | 72 | 5 | −1.01 | 2.10 | **1.85** | 1.77 | 1.39 | +0.20 | **CORRECTION_WINS_OOS** |
| London | SON | 91 | 91 | 5 | −0.69 | 1.09 | **0.84** | 0.89 | 0.66 | +0.14 | **CORRECTION_WINS_OOS** |
| London | DJF | 115 | 115 | 5 | −0.60 | 0.97 | **0.76** | 0.81 | 0.59 | +0.14 | **CORRECTION_WINS_OOS** |
| Jeddah | MAM | 42 | 42 | 5 | −3.68 | 6.10 | 4.90 | 5.20 | 4.38 | −0.07 | improves_but_LCB≤0 |
| Hong Kong | MAM | 41 | 41 | 5 | +0.69 | 1.48 | 1.33 | 1.16 | 1.00 | −0.01 | improves_but_LCB≤0 |
| Seoul | DJF | 74 | 74 | 5 | −0.88 | 1.69 | 1.45 | 1.21 | 1.10 | −0.04 | improves_but_LCB≤0 |
| NYC | JJA | 92 | 92 | 5 | +0.48 | 1.68 | 1.61 | 1.41 | 1.36 | −0.04 | improves_but_LCB≤0 |
| NYC | SON | 90 | 90 | 5 | −0.51 | 1.52 | 1.48 | 1.23 | 1.18 | −0.04 | improves_but_LCB≤0 |
| NYC | MAM | 169 | 169 | 5 | −0.23 | 1.96 | 1.95 | 1.53 | 1.52 | −0.01 | improves_but_LCB≤0 |
| Austin | MAM | 55 | 55 | 5 | +0.26 | 1.88 | 1.93 | 1.39 | 1.44 | −0.12 | RAW_WINS |
| London | JJA | 92 | 92 | 5 | −0.12 | 1.15 | 1.16 | 0.90 | 0.91 | −0.03 | RAW_WINS |
| London | MAM | 170 | 170 | 5 | −0.20 | 1.36 | 1.36 | 1.02 | 1.02 | −0.02 | RAW_WINS |

**Totals: CORRECTION_WINS_OOS = 11, improves_but_LCB≤0 = 6, RAW_WINS = 3.**

**The structural pattern (most important reasoning):** the verdict tracks `|bias_c|` almost monotonically.
- `|bias_c| ≳ 1.0` with a genuine offset → correction wins OOS, RMSE drops 15–62 %.
- `|bias_c| ≲ 0.7` → raw wins or the gain is within bootstrap noise → keep raw.
- The one large-bias exception is **Jeddah** (−3.68): correction improves RMSE (6.10→4.90) but LCB≤0 AND the *corrected* RMSE is still 4.9 °C — far larger than any other city. This is not a bias-correction failure; it is a sign of a deeper data-provenance problem (see §7).

### 5.3 Test B — bin-level proper score, raw(none) vs CONTAMINATED sd3 (12 cities, HIGH)

Lower = better for LogLoss/RPS/Brier. P(actual) higher = better. `sd3_wins_of_3` = LogLoss/RPS/Brier where sd3 beats raw.

| city | n_raw | n_sd3 | LogLoss_raw | LogLoss_sd3 | RPS_raw | RPS_sd3 | Brier_raw | Brier_sd3 | P(act)_raw | P(act)_sd3 | sd3_wins/3 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Jeddah | 5016 | 2647 | **25.48** | 3.39 | 5.44 | 4.06 | 1.50 | 0.96 | 0.006 | 0.034 | 3 |
| Shanghai | 5016 | 2664 | 13.46 | 3.22 | 2.37 | **2.63** | 1.20 | 0.95 | 0.120 | 0.075 | 2 (RPS worse) |
| Busan | 5016 | 2664 | 10.53 | 2.72 | 1.90 | **2.09** | 1.10 | 0.93 | 0.162 | 0.072 | 2 (RPS worse) |
| Jakarta | 5016 | 2664 | 13.89 | 2.88 | 2.41 | 2.41 | 1.20 | 0.96 | 0.062 | 0.066 | 3 |
| San Francisco | 5016 | 2625 | 12.42 | 2.08 | 2.25 | 1.07 | 1.23 | 0.85 | 0.088 | 0.149 | 3 |
| NYC | — | — | insufficient (cluster label mismatch) | | | | | | | | |
| Seoul | 2928 | 4737 | 6.31 | 2.30 | 1.35 | 1.35 | 1.07 | 0.87 | 0.149 | 0.121 | 2 |
| Hong Kong | 5016 | 2555 | 6.02 | 2.06 | 1.10 | 1.02 | 0.98 | 0.84 | 0.190 | 0.170 | 3 |
| Istanbul | 4408 | 2585 | 9.58 | 2.89 | 1.71 | **2.01** | 1.14 | 0.92 | 0.152 | 0.104 | 2 (RPS worse) |
| Paris | 2928 | 4727 | 3.52 | 2.08 | 1.16 | 1.00 | 0.93 | 0.82 | 0.175 | 0.194 | 3 |
| Austin | 5016 | 2630 | 3.51 | 2.42 | 1.22 | **1.39** | 0.88 | 0.86 | 0.190 | 0.138 | 2 (RPS worse) |
| London | — | — | insufficient (cluster label mismatch) | | | | | | | | |
| **GLOBAL** | 45376 | 45762 | **11.00** | **2.38** | 2.17 | 1.57 | 1.13 | 0.87 | 0.126 | 0.134 | 3 |

**This result REVERSES the earlier open-meteo conclusion — and that is the whole point.** Against the **settlement** (the market truth), the cold-bias correction's DIRECTION is right: contaminated sd3 beats raw on LogLoss and Brier for every city, and on P(actual) for the cold-biased ones. The earlier report said "sd3 is worse" — but it measured vs open-meteo (another forecast). Measured vs the payout truth, the warming helps. This independently corroborates Test A's direction.

**THREE critical caveats forbid using these magnitudes (and they are themselves findings):**
1. **Not cleanly OOS.** The stored `full_transport_v1` p_raw was generated by the MC using a `bias_c` fit on overlapping data; §4.1 raw-vs-ft is NOT blocked (only the §4.2 Platt layer is). So sd3 is favored in-sample. Test A (properly date-blocked) is the trustworthy direction evidence; Test B magnitudes are upper bounds on the benefit.
2. **The `none` baseline is pathological.** LogLoss_raw = 11–25 (Jeddah 25!) means the raw stored distributions assign ~1e-5…1e-11 probability to the true bin — near-delta, over-peaked distributions that miss the settled bin almost entirely. A sane forecast LogLoss is ~1–3. So a large part of "sd3's win" is just raw being absurdly over-confident in the stored pairs. **This is a NEW red flag: the production MC's spread (σ) for the `none` family appears badly under-dispersed** — directly feeding hypothesis 3 (§7).
3. **Contaminated sd3 OVER-corrects (RPS).** On Shanghai, Busan, Istanbul, Austin the RPS gets WORSE under sd3 even as LogLoss improves — the inflated (12z+unit) cold correction overshoots the settled bin's neighborhood. RPS penalizes distance; LogLoss/Brier reward hitting the exact bin. So contaminated sd3 sometimes lands the exact bin by luck-of-overshoot while being further away on average. This is precisely why the **clean, gated** correction (Test A) — not the contaminated unconditional one — is the right model.

**Net of Test B:** the correction direction is real vs settlement (confirms Test A), but Test B's own baseline is broken (over-peaked raw) and its sd3 is contaminated and not-OOS, so it cannot size the benefit. The clean-candidate bin-level re-MC (§7, hyp. 2) remains the required pre-live step.

---

## 6 — Diagnosis: data / math / method (the operator's three buckets)

The old `full_transport` sd3 was wrong for **three independent reasons**, one in each layer the operator named. This is the core of the report.

### (A) DATA was not processed correctly — CONFIRMED, FIXED
- 12Z night-window contamination + °F/°C settlement mix corrupted `residual_c` at the source.
- Symptom: Jeddah −6.84, US cities −45..−54.
- Fix: cycle-strict + unit-normalized extraction (T2/T3, committed). Clean biases now ≤3.8 °C.
- *This alone invalidates the shipped sd3 numbers — they were computed on corrupted residuals.*

### (B) MATH missed a step — CONFIRMED, FIXED
- The arithmetic of the correction (subtract the mean residual) is correct. What was missing is the **gating** step: the old pipeline applied `full_transport` **unconditionally to every bucket**. It never asked "should this bucket be corrected at all?" So it "corrected" London MAM (bias −0.20, pure noise) and Austin (+0.26) — adding estimation variance and *hurting* OOS accuracy (Test A: RAW_WINS for exactly these).
- Fix: `choose_candidate` accept-gate — correction is a candidate, not an entitlement; raw is the default.

### (C) Statistical METHOD was wrong — CONFIRMED, FIXED
- The old "validation" never held out data by date and never tested the correction against the **settlement**. It trusted the in-sample prior, and the only "before/after" was vs open-meteo (another forecast, not the payout truth).
- Correct method = blocked-by-`target_date` K-fold OOS + paired bootstrap LCB vs settlement (Test A). This is what separates SF MAM (real, LCB +2.10) from London MAM (noise, LCB −0.02).
- Fix: the Test A design + the LCB>0 requirement inside the accept-gate.

**Net:** all three failed simultaneously in the shipped sd3, which is why its biases were both *too large* (data bug) and *applied everywhere* (math/method gap). The redesign fixes each at the layer it lives in.

---

## 7 — My guesses (ranked) on what could still be wrong

1. **Jeddah is a station-identity / provenance problem, not a bias problem (HIGH confidence).** Clean bias −3.68 but corrected OOS RMSE still **4.90 °C** and LCB≤0 — i.e. even after removing the mean offset, the forecast misses the settlement by ~5 °C with high variance. My guess: the WU settlement station for Jeddah is a different microclimate (airport vs city / coastal vs inland desert) than the forecast grid point, OR desert diurnal extremes the ensemble cannot resolve. **This is a data-provenance question (Constraint #4), not a statistics question.** Action: verify the Jeddah settlement station identity vs forecast coordinates before trusting *any* Jeddah model; keep Jeddah on raw / quarantined meanwhile.

2. **A temperature-level OOS win does NOT guarantee a bin-level proper-score win (MEDIUM-HIGH).** Test A proves the corrected mean is closer to settlement. But the market scores over *bins*. If a market's bins are wide relative to the RMSE gain, the corrected probability mass may not cross a bin edge → no proper-score improvement, or even a loss near edges. My guess: the 11 winning buckets will mostly carry over to proper scores, but a few (small RMSE gain, e.g. London DJF 0.97→0.76) may not. **This is the not-yet-built step: re-MC the clean candidate through `p_raw_vector_from_maxes` over the real market bins and re-run Test B on the clean (not contaminated) distributions.** It is the last gate before live.

3. **Spread (σ) is mis-calibrated — now with evidence (MEDIUM-HIGH).** Test A validates the *location* (mean) correction only. But Test B exposed that the stored `none` distributions carry LogLoss 11–25 (Jeddah 25, Shanghai 13, Jakarta 14) — i.e. they assign ~1e-5…1e-11 probability to the settled bin. A correctly-dispersed forecast cannot have LogLoss 25; this is a **near-delta, under-dispersed distribution**. My guess: the production MC is producing over-confident probability vectors (σ too small relative to true forecast error), so the system is systematically over-betting. This is independent of the bias question and could hurt live PnL on its own (over-confident sizing). Action: PIT/ECE on the clean candidate AND on the raw baseline; if PIT is U-shaped / ECE high, widen σ before live. The conservative σ floor (3.0 °C) may be simultaneously too wide for London (bias ~0, RMSE ~1) and too narrow for Jeddah (RMSE ~5).

4. **Small-n buckets are fragile (MEDIUM).** Paris DJF (n=9, 3-fold) "wins" but 9 dates cannot support a trustworthy held-out estimate; one outlier flips it. My guess: enforce a minimum effective n (the existing MIN_PAIRED_N=5 is too low for a 5-fold OOS claim); treat n<~30 buckets as "insufficient → raw or wide-σ identity" regardless of the win.

5. **Seasonal non-stationarity inside a bucket (LOW-MEDIUM).** A (city, season) bucket pools all years; if the bias drifted (model upgrades, station moves), the pooled mean is a blur. Blocked-by-date K-fold does not block by *year*. My guess: minor for now, but a year-blocked fold would be a stricter test for the borderline buckets.

---

## 8 — What is proven, what is not, live gate

**Proven this session:**
- The two data bugs are real and fixed; clean biases are physically plausible.
- The clean bias correction generalizes OOS at the temperature layer for 11/20 buckets (LCB>0), and correctly should NOT be applied to ~9 near-zero/noisy buckets.
- The accept-gate enforces exactly this (10/10 tests).

**Not yet proven (blocks live):**
- Bin-level proper-score (vs settlement) win for the **clean** candidate (Test B currently only covers the contaminated sd3). → build the clean re-MC + re-run Test B.
- σ / PIT calibration of the clean candidate.
- Jeddah (and any city with corrected RMSE ≫ peers) station-identity provenance.
- LOW metric (this report is HIGH only).

**Live gate (unchanged): HOLD.** Do not promote, do not unshadow. Promote only the buckets that pass BOTH Test A (done) AND a clean-candidate Test B (to build), with σ/PIT checked, Jeddah excluded pending provenance. Everything else stays raw — which Test A shows is the better forecaster for those buckets anyway.

---

## 9 — Reproduction

```bash
WT=/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
DB=/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-forecasts.db
# clean evidence ledger (12 cities, HIGH, 0z-strict, unit-normalized)
.venv/bin/python "$WT/scripts/build_ens_residual_evidence.py" --source-db "$DB" --metric high \
  --cities "Jeddah,Shanghai,Busan,Jakarta,San Francisco,NYC,Seoul,Hong Kong,Istanbul,Paris,Austin,London" \
  --out ENS_RESIDUAL_EVIDENCE_12CITY_HIGH.csv
# Test A — OOS bias cross-fit
.venv/bin/python phase2_oos_bias.py        # reads the ledger CSV
# Test B — bin-level proper score raw vs contaminated sd3
.venv/bin/python score_12city.py
# accept-gate unit tests
.venv/bin/python -m pytest "$WT/tests/test_t4_selection_rule_invariants.py" -q
```
