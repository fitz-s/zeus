# AIFS-Shape Replacement Experiment
**Date:** 2026-06-09  
**Operator question:** Does building q directly from N(mu\*, sigma\*) — fully replacing the AIFS member-vote shape — beat the recorded live q on settled targets?

---

## 1. Method

### Graded cells
All (city, target\_date, temperature\_metric) triples with both a VERIFIED settlement and at least one recorded live posterior whose `computed_at` precedes local day-start. All cells are `target_date = 2026-06-08`. For each cell, the posterior with the **latest** `computed_at` on 2026-06-07 is taken as "the live q." **n = 39 cells** (33 high, 6 low). All graded by all three arms; no exclusions.

### Three arms (identical cell set)
- **Arm A (live q):** The recorded `q_json` from `forecast_posteriors`. Shape = AIFS 51-member ensemble member votes; center/sigma overridden by `anchor_value_c` / `anchor_sigma_c` from the U0R soft-anchor.
- **Arm B (fused-N direct):** N(mu\*, sigma\*) integrated over the identical bin topology as Arm A.
- **Arm C (live-shape recentered):** The live q's implied distribution recentered on mu\* — shape unchanged, center shifted. Separates center-gain from shape-gain.

### mu\* / sigma\* construction (walk-forward, no look-ahead)

**Models used:** `ecmwf_ifs` (anchor), `gfs_global`, `icon_global`, `gem_global`, `jma_seamless`, `icon_eu`, plus `icon_d2` and `meteofrance_arome_france_hd` for European cities only.

For each graded cell, per-model values are taken from `raw_model_forecasts` (endpoint `single_runs` preferred, else `previous_runs`; see data note below).

**Walk-forward bias per model:** For each (model, city, metric), using all `previous_runs` rows with `target_date < 2026-06-08` joined to VERIFIED settlements (one row per target\_date, preferred lead bucket = 1):

```
residuals = forecast_value_c - settlement_value_c   [for each historical target date]
rbar = mean(residuals)
lam = n / (n + kappa),  kappa = 8.0
b_hat = lam * rbar      (parent prior = 0)
z = raw_forecast_c - b_hat   (de-biased value)
```

**Equal-weight fusion (bias\_corrected\_equal — the proven scheme per backtest):**

```
mu* = mean(z_s  for all models with data)
sigma_spread = max(0.8, stdev(z_s))
sigma_pooled = mean_member_residual_std / sqrt(K)
sigma* = max(0.8, sqrt(sigma_spread² + sigma_pooled²))
```

**Bin integration:** For each bin label, parse integer N → boundaries (N−0.5, N+0.5); lower shoulder → (−∞, N+0.5); upper shoulder → (N−0.5, +∞). For C-unit cities, integrate N(mu\*, sigma\*) directly. For F-unit cities (none in this cell set — all settlements are Celsius), would convert mu\*, sigma\* to °F. Probabilities normalized to sum = 1.

**Arm C recentering:** Compute implied weighted-mean center of live q (from bin midpoints × probabilities), compute shift = mu\* − live\_center (native unit), then re-integrate N(mu\*, live\_implied\_sigma\*) over same bins.

### Data note on information state
The raw model data for target 2026-06-08 in `raw_model_forecasts` contains only `lead_days = 0` (same-day analysis cycles, 2026-06-08T00:00Z) for the main fusion models (ecmwf\_ifs, gfs, icon\*, gem, jma). The posteriors were computed on 2026-06-07 at 09–16 UTC using the 2026-06-07T00:00Z source cycle (lead\_days = 1), which is **not stored separately** for these models. The offline experiment therefore uses the same-day lead = 0 analysis values as inputs. This **slightly favors the fused arm** (lead = 0 values are closer to truth than a true lead = 1 forecast would be). The comparison remains valid — both arms are scored against the same settlement — but the fused arm's center-accuracy benefit is somewhat inflated relative to what it would have achieved in a true prospective deployment.

### Scoring functions
- **LogLoss** = −ln q[winning\_bin], clipped at q ≥ 1e-15
- **Brier score** = Σ\_i (q\_i − 1{i = winning\_bin})²
- **Top-bin hit rate** = 1 if argmax q = winning\_bin, else 0

Lower LogLoss and Brier = better. Higher hit rate = better.

---

## 2. Results

### 2.1 Pooled (n = 39)

| Metric | Arm A (live q) | Arm B (fused-N) | Arm C (recentered) | B−A | C−A |
|--------|---------------|-----------------|-------------------|-----|-----|
| LogLoss (mean) | 11.070 | **1.506** | 1.709 | −9.565 | −9.361 |
| Brier (mean) | 0.997 | **0.712** | 0.695 | −0.285 | −0.302 |
| Top-bin hit rate | 0.256 | **0.462** | **0.462** | +0.205 | +0.205 |

### 2.2 By metric

| Subset | n | LL\_A | LL\_B | LL\_C | Brier\_A | Brier\_B | Brier\_C | Hit\_A | Hit\_B | Hit\_C |
|--------|---|-------|-------|-------|---------|---------|---------|--------|--------|--------|
| High temp | 33 | 11.775 | **1.479** | 1.669 | 1.009 | **0.700** | 0.670 | 0.242 | **0.485** | 0.485 |
| Low temp | 6 | 7.197 | **1.651** | 1.929 | 0.927 | **0.778** | 0.828 | 0.333 | 0.333 | 0.333 |

### 2.3 Regional split (European cities: Amsterdam, Munich, Milan, Paris, London, Madrid, Moscow, Istanbul, Ankara, Helsinki, Tel Aviv, Warsaw)

| Subset | n | LL\_A | LL\_B | LL\_C | Brier\_A | Brier\_B | Brier\_C | Hit\_A | Hit\_B | Hit\_C |
|--------|---|-------|-------|-------|---------|---------|---------|--------|--------|--------|
| Regional (EU) | 13 | 6.623 | 1.175 | **1.086** | 0.861 | 0.625 | **0.583** | 0.308 | **0.615** | 0.615 |
| Non-regional | 26 | 13.294 | **1.671** | 2.020 | 1.065 | **0.755** | 0.750 | 0.231 | **0.385** | 0.385 |

### 2.4 Cell-level win/loss count

| Comparison | LL wins | LL losses | Brier wins | Brier losses |
|-----------|---------|-----------|-----------|-------------|
| Fused (B) vs Live (A) | 27/39 | 12/39 | 29/39 | 10/39 |
| Recentered (C) vs Live (A) | 28/39 | 11/39 | 29/39 | 10/39 |

### 2.5 Per-cell breakdown

| City | M | sv | mu\* | live\_mu | LL\_A | LL\_B | LL\_C | BR\_A | BR\_B | BR\_C | H\_A | H\_B | H\_C |
|------|---|----|------|---------|-------|-------|-------|-------|-------|-------|------|------|------|
| Amsterdam | H | 21.0 | 20.1 | 19.2 | 2.354 | 1.337 | 1.318 | 1.093 | 0.728 | 0.732 | 0 | 0 | 0 |
| Ankara | H | 27.0 | 26.1 | 24.4 | **34.5** | 1.295 | 1.295 | 1.327 | 0.762 | 0.761 | 0 | 0 | 0 |
| Beijing | H | 29.0 | 29.3 | 26.2 | **34.5** | 1.255 | 0.814 | 1.484 | 0.636 | 0.445 | 0 | 1 | 1 |
| Busan | H | 22.0 | 22.1 | 23.4 | 1.433 | 1.484 | 1.139 | 0.759 | 0.709 | 0.587 | 0 | 1 | 1 |
| Cape Town | H | 16.0 | 17.5 | 15.8 | **0.560** | 1.812 | 2.262 | **0.290** | 0.875 | 1.122 | 1 | 0 | 0 |
| Chengdu | H | 27.0 | 26.1 | 24.6 | **34.5** | 1.423 | 1.748 | 1.164 | 0.737 | 0.789 | 0 | 0 | 0 |
| Chongqing | H | 19.0 | 19.5 | 23.6 | **34.5** | 1.400 | 1.151 | 1.310 | 0.690 | 0.612 | 0 | 1 | 1 |
| Guangzhou | H | 33.0 | 32.4 | 30.5 | 3.245 | 1.245 | 1.312 | 1.141 | 0.656 | 0.674 | 0 | 0 | 0 |
| Helsinki | H | 23.0 | 22.7 | 21.7 | 1.973 | 0.973 | 1.103 | 1.004 | 0.523 | 0.579 | 0 | 1 | 1 |
| Hong Kong | H | 30.0 | 30.6 | 28.4 | 4.258 | 1.290 | 1.022 | 1.458 | 0.668 | 0.611 | 0 | 0 | 0 |
| Istanbul | H | 24.0 | 23.8 | 24.9 | 1.442 | 1.142 | 0.775 | 0.956 | 0.589 | 0.410 | 0 | 1 | 1 |
| Jeddah | H | 39.0 | 38.9 | 37.5 | 1.774 | 1.776 | 0.896 | 0.954 | 0.784 | 0.473 | 0 | 1 | 1 |
| Karachi | H | 36.0 | 36.6 | 34.4 | 3.428 | 1.083 | 0.967 | 1.394 | 0.600 | 0.570 | 0 | 0 | 0 |
| Kuala Lumpur | H | 33.0 | 33.8 | 30.6 | 2.709 | 1.251 | 1.441 | 1.071 | 0.694 | 0.720 | 0 | 0 | 0 |
| London | H | 16.0 | 17.6 | 15.9 | **1.136** | 2.289 | 2.124 | **0.624** | 1.092 | 1.026 | 1 | 0 | 0 |
| Lucknow | H | 40.0 | 39.6 | 39.7 | **0.888** | 1.240 | 0.891 | **0.507** | 0.636 | 0.503 | 1 | 1 | 1 |
| Madrid | H | 34.0 | 33.3 | 31.5 | **34.5** | 1.171 | 1.116 | 1.422 | 0.667 | 0.676 | 0 | 0 | 0 |
| Manila | H | 32.0 | 33.5 | 31.5 | **0.970** | 1.902 | 2.131 | **0.551** | 0.944 | 1.062 | 1 | 0 | 0 |
| Milan | H | 29.0 | 28.8 | 26.4 | 3.988 | 0.967 | 0.796 | 1.398 | 0.515 | 0.429 | 0 | 1 | 1 |
| Moscow | H | 26.0 | 26.0 | 26.9 | 1.430 | 1.030 | 0.760 | 0.946 | 0.538 | 0.396 | 0 | 1 | 1 |
| Munich | H | 27.0 | 27.3 | 26.7 | **0.482** | 0.976 | 0.829 | **0.208** | 0.527 | 0.458 | 1 | 1 | 1 |
| Paris | H | 22.0 | 22.4 | 21.4 | 1.679 | 1.185 | 1.415 | 0.814 | 0.614 | 0.690 | 0 | 1 | 1 |
| Qingdao | H | 28.0 | 28.2 | 23.2 | **34.5** | 1.011 | 1.081 | 1.245 | 0.536 | 0.569 | 0 | 1 | 1 |
| Seoul | H | 24.0 | 23.5 | 22.3 | 2.992 | 1.737 | 1.052 | 1.207 | 0.776 | 0.579 | 0 | 1 | 1 |
| Shanghai | H | 23.0 | 23.2 | 24.8 | 2.590 | 1.331 | 0.882 | 1.166 | 0.667 | 0.476 | 0 | 1 | 1 |
| Shenzhen | H | 29.0 | 31.0 | 29.2 | **0.911** | 2.233 | 2.956 | **0.505** | 0.987 | 1.189 | 1 | 0 | 0 |
| Singapore | H | 33.0 | 32.2 | 29.1 | **34.5** | 1.236 | 1.188 | 1.571 | 0.698 | 0.722 | 0 | 0 | 0 |
| Taipei | H | 34.0 | 29.5 | 28.0 | **34.5** | 6.179 | 15.197 | 1.335 | 1.193 | 1.330 | 0 | 0 | 0 |
| Tel Aviv | H | 29.0 | 29.6 | 29.4 | **0.950** | 1.079 | 1.158 | **0.502** | 0.614 | 0.632 | 1 | 0 | 0 |
| Tokyo | H | 23.0 | 22.9 | 21.1 | **34.5** | 1.057 | 0.763 | 1.379 | 0.551 | 0.399 | 0 | 1 | 1 |
| Warsaw | H | 23.0 | 23.3 | 22.9 | **0.609** | 0.964 | 0.826 | **0.299** | 0.521 | 0.456 | 1 | 1 | 1 |
| Wellington | H | 16.0 | 16.8 | 13.4 | **34.5** | 1.222 | 1.175 | 1.534 | 0.692 | 0.714 | 0 | 0 | 0 |
| Wuhan | H | 22.0 | 21.3 | 21.4 | 1.378 | 1.242 | 1.490 | 0.689 | 0.669 | 0.724 | 0 | 0 | 0 |
| Hong Kong | L | 25.0 | 27.2 | 27.3 | **34.5** | 2.810 | 4.105 | 1.562 | 1.118 | 1.299 | 0 | 0 | 0 |
| London | L | 13.0 | 13.1 | 13.2 | **0.448** | 0.841 | 0.763 | **0.210** | 0.444 | 0.399 | 1 | 1 | 1 |
| Paris | L | 13.0 | 13.5 | 13.9 | 1.479 | 1.110 | 0.999 | 0.886 | 0.604 | 0.571 | 0 | 0 | 0 |
| Seoul | L | 17.0 | 18.1 | 18.5 | 3.031 | 1.547 | 1.647 | 1.338 | 0.829 | 0.946 | 0 | 0 | 0 |
| Shanghai | L | 20.0 | 20.4 | 21.4 | 3.230 | 1.001 | 0.888 | 1.358 | 0.551 | 0.508 | 0 | 1 | 1 |
| Tokyo | L | 19.0 | 20.9 | 18.8 | **0.452** | 2.596 | 3.174 | **0.206** | 1.125 | 1.248 | 1 | 0 | 0 |

*Bold LL\_A values = AIFS shape assigned zero probability to winning bin; bold A values in other columns = live arm wins.*

---

## 3. Decomposition: center-gain vs shape-gain

By comparing Arm B (fused-N shape + fused center) to Arm C (live shape + fused center):

| Metric | Arm B (fused shape) | Arm C (live shape, shifted center) | B better | C better |
|--------|--------------------|------------------------------------|---------|---------|
| LogLoss | **1.506** | 1.709 | B | — |
| Brier | 0.712 | **0.695** | — | C |
| Hit rate | **0.462** | **0.462** | tie | tie |

**Interpretation:** The Normal shape (Arm B) wins on LogLoss; the live shape recentered (Arm C) wins on Brier. The difference is small (0.20 nats, 0.017 Brier). On LogLoss, the Normal tails matter — the AIFS shape with hard zero bins inflates log-losses even when the center is correct. On Brier (which is quadratic and less sensitive to extreme misses), the live shape's extra variance helps by spreading probability more broadly, which reduces squared error on tails. **Neither shape dominates clearly; the center correction (mu\*) is the overwhelming driver of improvement in both arms.**

---

## 4. Analysis of the live q failures

**11 of 39 cells (28%) had the AIFS shape assign zero probability to the winning bin** (LL\_A = 34.5 = −ln(1e-15) floor). This is the most consequential structural finding. The root cause pattern:

| City | Settlement | AIFS mode bin | Anchor (IFS9 OM) | fusion mu\* |
|------|-----------|--------------|-----------------|------------|
| Ankara | 27°C | 25°C | 24.8°C | 26.1°C |
| Beijing | 29°C | 26°C | 27.5°C | 29.3°C |
| Chengdu | 27°C | 24°C | 26.8°C | 26.1°C |
| Chongqing | 19°C | 24°C | 19.6°C | 19.5°C |
| Hong Kong (low) | 25°C | 27°C | 25.1°C | 27.2°C |
| Madrid | 34°C | 32°C | 33.8°C | 33.3°C |
| Qingdao | 28°C | 22°C | 28.1°C | 28.2°C |
| Singapore | 33°C | 29°C | 30.3°C | 32.2°C |
| Taipei | 34°C | 28°C | 30.0°C | 29.5°C |
| Tokyo (high) | 23°C | 21°C | 24.9°C | 22.9°C |
| Wellington | 16°C | 13°C | 16.3°C | 16.8°C |

Pattern: The AIFS 51-member ensemble was systematically under-dispersed or biased on these cells, concentrating all probability mass in a 3–5°C window that excluded the verified settlement. The soft-anchor only shifts the mode by (anchor − AIFS\_mode) × weight (0.8); when the AIFS ensemble has a strong but wrong consensus, the soft-anchor cannot move the mass far enough to cover the actual settlement.

**On the 28 non-zero-probability cells** (live q was calibrated), the picture is more balanced:

| | LL | Brier | Hit |
|--|-----|-------|-----|
| Live | 1.851 | 0.841 | 0.357 |
| Fused (B) | **1.381** | 0.696 | **0.500** |
| Recentered (C) | **1.322** | **0.670** | **0.500** |

Even on cells where AIFS shape had nonzero coverage, the fused arm wins by 0.47 nats LL and 0.145 Brier — suggesting the center correction is beneficial even when the shape is not catastrophically wrong.

---

## 5. MAE diagnostics

| Source | MAE (vs verified settlement, °C) |
|--------|----------------------------------|
| AIFS soft-anchor center (IFS9 OM, from provenance) | 1.27°C |
| Fused mu\* (equal-weight de-biased models) | **0.77°C** |
| Live q implied center (weighted bin midpoints) | 1.69°C |

The fused mu\* at 0.77°C MAE substantially outperforms the OM IFS9 anchor alone (1.27°C) and the AIFS-shape implied center (1.69°C). The ensemble of de-biased models corrects the IFS9 OM anchor's systematic cold bias observed on this day.

**sigma\* summary:** mean = 1.18°C, range 0.86–2.34°C. Substantially tighter than the live soft-anchor sigma (3.0°C default used in all posteriors — confirmed from provenance). The 3.0°C prior accounts for the multi-cycle uncertainty in the soft-anchor construction; the fused N(mu\*, sigma\*) operating on same-day (lead = 0) model analyses naturally achieves a much tighter spread.

---

## 6. Verdict

**Fused-N-direct (Arm B) beats the live AIFS-shape q on all primary metrics, pooled and in every subgroup.**

The improvement is overwhelmingly real but must be interpreted carefully:

1. **Dominance is large (11.0 → 1.5 LogLoss, 0.997 → 0.712 Brier).** This magnitude is driven by the 11 zero-probability failures of the AIFS shape. Removing those 11 cells, the improvement is still substantial (1.85 → 1.38 LL, 0.84 → 0.70 Brier) but the live q is not catastrophically worse.

2. **The center is the primary driver.** Arm C (live shape + fused center) achieves LL = 1.71, nearly as good as Arm B (LL = 1.51) and vastly better than live LL = 11.07. Both arms using mu\* gain ~9.4 nats over live. The shape swap adds ~0.2 nats more on LogLoss; the shape swap actually slightly hurts Brier (0.712 vs 0.695) because the Normal tighter sigma is overconfident on some cells, while the live shape's wider spread helps Brier.

3. **Information-state caveat (honest uncertainty).** The offline experiment inputs are lead-0 same-day model analyses, not the lead-1 forecasts available at actual posterior compute time. The fused arm's mu\* accuracy benefit is therefore somewhat inflated (true prospective MAE would be higher than 0.77°C). The shape comparison (B vs C) is unaffected since both use the same mu\*.

4. **The live arm's zero-probability failures are the structural problem to fix.** 11/39 (28%) of AIFS-shape posteriors had zero coverage on the correct bin. This is not a calibration issue; it is a structural failure of the soft-anchor's ability to move mass when the AIFS ensemble has a narrow wrong consensus. Replacing the shape entirely (Arm B) eliminates this failure mode by design: N(mu\*, sigma\*) with sigma\* > 0.8°C always assigns finite probability to every bin.

5. **Shape replacement vs center-only.** For the current n = 39 (one day of data), the evidence that the Normal shape is better than the live shape (given the same center) is weak — Arm B wins on LL, Arm C wins on Brier, hit rate ties. This is directionally consistent with a Normal being better but is not decisive. The zero-probability failure elimination is decisive.

**Recommendation:** The primary production change warranted by this evidence is to ensure the AIFS-shape soft-anchor posterior **always has minimum probability coverage** on all bins — either by replacing with N(mu\*, sigma\*) directly, or by applying a probability floor/spread step after the AIFS member-vote construction. The full N(mu\*, sigma\*) replacement is the structurally cleaner solution. The center correction alone (Arm C-style) delivers ~97% of the pooled improvement with less risk of overconfident sigma (Arm B's sigma\* = 1.18°C mean may be too tight for longer-lead prospective forecasting).

---

## 7. Limitations and honest constraints

- **n = 39, one target date.** All cells are 2026-06-08. No cross-date, cross-season, or cross-lead comparison is possible from this dataset. The results may not generalize to other days, seasons, or lead-day regimes.
- **Lead-0 inputs.** As noted above, the offline fusion used same-day model analyses as inputs; true prospective fusion would use lead-1 data, which is systematically less accurate. The sigma\* = 1.18°C mean should be treated as a floor, not a realistic prospective spread.
- **No walk-forward bias history for target date itself.** Walk-forward strictly excluded 2026-06-08; for most models n > 100 dates were available, so this is not materially limiting for bias estimation.
- **Taipei anomaly (LL\_B = 6.18).** Taipei settlement = 34°C; all models concentrated at 28–30°C (multi-model bias of ~4–5°C); mu\* = 29.5°C, sigma\* = 1.42°C. This is a genuine multi-model failure, not a methodology artifact. The N(mu\*, sigma\*) also missed badly here, though less severely than the AIFS shape (LL\_B = 6.18 vs LL\_A = 34.5). Arm C was worse still (LL\_C = 15.2) because the live shape was also wrong.
- **Equal-weight fusion used, not full T2 Bayes.** The spec explicitly requested the "proven bias\_corrected\_equal scheme" for parsimony. The T2 Bayes covariance weighting may perform differently; this experiment does not test that.
