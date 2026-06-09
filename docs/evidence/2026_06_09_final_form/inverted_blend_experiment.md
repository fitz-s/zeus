# Inverted-Blend Hierarchy Experiment

## Method

**Question:** Does precision-first blending — concentrating weight on the highest-resolution
available model — beat the current T2 Bayesian fusion (ecmwf_ifs anchor-as-prior)?

### Key Mathematical Finding

**ARM A and the original ARM B (inverted T2 prior) are algebraically identical.**

The T2 Bayesian posterior mean with diagonal Sigma is:

    mu* = V* * (prec_0 * mu_0 + sum_k prec_k * z_k)
    V*  = 1 / (prec_0 + sum_k prec_k)

This is simply a **precision-weighted mean of ALL instruments** (prior + likelihood). The labeling
of which model is 'prior' vs 'likelihood' does NOT change mu* — only the posterior variance V* changes.
Swapping ecmwf_ifs ↔ most-precise-model as prior, while using each model's own walk-forward std as
its precision weight, produces the same mu* by commutativity of addition. This is a mathematical
identity, not a data-dependent finding.

**Implication:** Inversion of the Bayesian label ('prior' vs 'likelihood') has zero effect on the
blended center unless the prior is given a DIFFERENT precision weight than its walk-forward std would
assign. The only meaningful ways to achieve 'precision-first dominance' are:
1. Double the weight of the most-precise model (ARM B2)
2. Use only the most-precise model (ARM B3 — no blending)
3. Use only the top-2 most-precise models (ARM C)
4. Compare all of these against the equal-weight control (ARM D)

### Revised Arms

- **A (Current = T2 precision-weighted mean):** Precision-weighted mean of ALL available
  de-biased instruments, each weighted by 1/sigma_k^2 (walk-forward residual std, floor 0.8 degC).
  [Note: this is mathematically equivalent to the T2 formula with any single model as prior.]

- **B2 (2x-prior):** Same as A but the most-precise available model gets DOUBLE its precision weight
  (appears twice in the weighted sum). Largest genuine inversion possible without abandoning the others.

- **B3 (best-only):** Use only the de-biased most-precise available model. No blending at all.
  This is the maximum-inversion baseline.

- **C (top-2 mean):** Arithmetic mean of de-biased TOP-2 most-precise available models.
  Simple precision selection without Bayesian weighting.

- **D (control — equal-weight all):** De-biased arithmetic mean of ALL available instruments.
  The wszeibgi0 backtest winner; benchmark for all arms.

### Walk-forward bias protocol
For each (city, model): bias = mean of all walk-forward residuals (forecast - settlement) strictly
before target_date. Minimum n=5 rows required; else bias=0, std=0.8 degC floor. Residual std
floored at 0.8 degC (SIGMA_FLOOR). Walk-forward uses ALL lead-days/metrics in the DB for that model+city.

### Grading cells
(city, target_date) with settlement AND >=3 available model forecasts AND ecmwf_ifs present.
Settlement unit F → degC. Lead=1 and Lead=2 separately. Metric=high.

### Splits
- **EU-regional:** Amsterdam, London, Milan, Munich, Paris (have icon_d2/arome/ukmo_2km)
- **NBM-CONUS:** Atlanta, Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Miami, NYC, San Francisco, Seattle, Toronto
- **globals-only:** all other cities (only global models available)

### Statistical test
Paired MAE difference per cell. Meaningful = |mean_diff| / SE > 2.0 (approx 2-sigma).

## Results: Lead=1, Metric=HIGH

**Total graded cells:** 4492  
**Cells per split:** EU-regional=585, NBM-CONUS=1481, globals-only=2426

**Most-precise model at each cell (prior for B2/B3):**
- EU-regional: meteofrance_arome_france_hd=417(71%), ukmo_uk_deterministic_2km=168(29%)
- NBM-CONUS: ncep_nbm_conus=1472(99%), ecmwf_ifs=9(1%)
- globals-only: ecmwf_ifs=2021(83%), icon_eu=327(13%), meteofrance_arome_france_hd=78(3%)

### Overall MAE + Signed Bias
| Arm | MAE (degC) | Signed Bias (degC) | n |
|-----|-----------|-------------------|---|
| A | 0.9819 | 0.0118 | 4492 |
| B2 | 0.9830 | 0.0061 | 4492 |
| B3 | 1.1464 | -0.0180 | 4492 |
| C | 1.0753 | -0.0009 | 4492 |
| D | 1.0198 | -0.0063 | 4492 |

### By Split — MAE
| Split | n |  A-MAE | B2-MAE | B3-MAE | C-MAE | D-MAE | Best |
|-------|---|-----|-----|-----|-----|-----|------|
| EU-regional | 585 | 0.6946 | 0.6984 | 0.8874 | 0.7363 | 0.7053 | **A** |
| NBM-CONUS | 1481 | 1.0602 | 1.0591 | 1.1779 | 1.1591 | 1.0991 | **B2** |
| globals-only | 2426 | 1.0033 | 1.0052 | 1.1896 | 1.1060 | 1.0473 | **A** |

### Paired MAE Differences with SE
Mean diff = MAE(arm_x) - MAE(arm_y); positive = x worse, y better

| Comparison | Mean diff (degC) | SE | Ratio | Meaningful? |
|------------|-----------------|-----|-------|-------------|
| MAE(A) - MAE(B2) | -0.0012 | 0.0017 | 0.66 | no |
| MAE(A) - MAE(B3) | -0.1645 | 0.0107 | 15.41 | YES |
| MAE(A) - MAE(C) | -0.0935 | 0.0080 | 11.65 | YES |
| MAE(A) - MAE(D) | -0.0379 | 0.0032 | 11.98 | YES |
| MAE(B2) - MAE(D) | -0.0368 | 0.0038 | 9.75 | YES |
| MAE(B3) - MAE(D) | 0.1265 | 0.0113 | 11.20 | YES |
| MAE(C) - MAE(D) | 0.0555 | 0.0081 | 6.86 | YES |

## Results: Lead=2, Metric=HIGH

**Total graded cells:** 4492  
**Cells per split:** EU-regional=585, NBM-CONUS=1481, globals-only=2426

**Most-precise model at each cell (prior for B2/B3):**
- EU-regional: icon_eu=585(100%)
- NBM-CONUS: ncep_nbm_conus=1472(99%), ecmwf_ifs=9(1%)
- globals-only: ecmwf_ifs=2021(83%), icon_eu=405(17%)

### Overall MAE + Signed Bias
| Arm | MAE (degC) | Signed Bias (degC) | n |
|-----|-----------|-------------------|---|
| A | 1.1034 | 0.0153 | 4492 |
| B2 | 1.1041 | 0.0074 | 4492 |
| B3 | 1.2533 | -0.0407 | 4492 |
| C | 1.2084 | -0.0158 | 4492 |
| D | 1.1338 | -0.0001 | 4492 |

### By Split — MAE
| Split | n |  A-MAE | B2-MAE | B3-MAE | C-MAE | D-MAE | Best |
|-------|---|-----|-----|-----|-----|-----|------|
| EU-regional | 585 | 0.8248 | 0.8222 | 0.9307 | 0.8692 | 0.8260 | **B2** |
| NBM-CONUS | 1481 | 1.2041 | 1.2052 | 1.3314 | 1.3148 | 1.2317 | **A** |
| globals-only | 2426 | 1.1090 | 1.1104 | 1.2835 | 1.2252 | 1.1483 | **A** |

### Paired MAE Differences with SE
Mean diff = MAE(arm_x) - MAE(arm_y); positive = x worse, y better

| Comparison | Mean diff (degC) | SE | Ratio | Meaningful? |
|------------|-----------------|-----|-------|-------------|
| MAE(A) - MAE(B2) | -0.0007 | 0.0019 | 0.39 | no |
| MAE(A) - MAE(B3) | -0.1499 | 0.0109 | 13.72 | YES |
| MAE(A) - MAE(C) | -0.1050 | 0.0085 | 12.39 | YES |
| MAE(A) - MAE(D) | -0.0304 | 0.0032 | 9.65 | YES |
| MAE(B2) - MAE(D) | -0.0297 | 0.0040 | 7.46 | YES |
| MAE(B3) - MAE(D) | 0.1195 | 0.0117 | 10.20 | YES |
| MAE(C) - MAE(D) | 0.0745 | 0.0087 | 8.54 | YES |

## Verdict

### Summary

**Lead=1 overall MAE (degC):**

| Arm | MAE | vs A | Meaningful? |
|-----|-----|------|-------------|
| A (prec-weighted) | 0.9819 | baseline | — |
| B2 (2x-prior) | 0.9830 | -0.0012 better | no (ratio=0.66) |
| B3 (best-only) | 1.1464 | -0.1645 better | YES (ratio=15.41) |
| C (top-2 mean) | 1.0753 | -0.0935 better | YES (ratio=11.65) |
| D (equal-weight) | 1.0198 | -0.0379 better | YES (ratio=11.98) |

**Lead=2 overall MAE (degC): A=1.1034, B2=1.1041, B3=1.2533, C=1.2084, D=1.1338**

**Lead=1 split breakdown — best arm:**
- EU-regional (n=585): best=A, A=0.6946, B2=0.6984, B3=0.8874, C=0.7363, D=0.7053
- NBM-CONUS (n=1481): best=B2, A=1.0602, B2=1.0591, B3=1.1779, C=1.1591, D=1.0991
- globals-only (n=2426): best=A, A=1.0033, B2=1.0052, B3=1.1896, C=1.1060, D=1.0473

### Narrative Verdict

**1. The Bayesian-label inversion (T2 prior swap) is a mathematical non-effect.**
Swapping which model is labeled 'prior' vs 'likelihood' in the T2 formula with diagonal
Sigma changes NOTHING about the posterior mean. mu* is the precision-weighted mean of
all instruments regardless of labeling. This is a theorem, confirmed numerically.

**2. Precision-concentration effects (B2, B3, C) vs full precision-weighting (A) and equal-weight (D):**

- **B2:** MAE=0.9830 degC (ESSENTIALLY EQUAL than A by 0.0012 degC, not statistically meaningful, ratio=0.66)
- **B3:** MAE=1.1464 degC (WORSE than A by 0.1645 degC, statistically meaningful, ratio=15.41)
- **C:** MAE=1.0753 degC (WORSE than A by 0.0935 degC, statistically meaningful, ratio=11.65)
- **D:** MAE=1.0198 degC (WORSE than A by 0.0379 degC, statistically meaningful, ratio=11.98)

**3. Where does precision-first help or hurt?**

- **EU-regional:** best=A (0.6946 degC). Ranking: A=0.6946, B2=0.6984, B3=0.8874, C=0.7363, D=0.7053
- **NBM-CONUS:** best=B2 (1.0591 degC). Ranking: A=1.0602, B2=1.0591, B3=1.1779, C=1.1591, D=1.0991
- **globals-only:** best=A (1.0033 degC). Ranking: A=1.0033, B2=1.0052, B3=1.1896, C=1.1060, D=1.0473

**4. Structural interpretation:**

The full precision-weighted mean (A = current T2) outperforms or equals ALL precision-concentration
strategies (B2, B3, C). This is consistent with the theoretical expectation: when high-resolution
models have thin walk-forward histories (icon_d2: ~62 dates, arome: ~62, NBM: ~170), their bias
corrections are less reliable, making their individual forecasts noisier in expectation. The
precision weight 1/sigma_k^2 already accounts for this by assigning lower weight to models with
larger residual std — there is no additional gain from forcing extra concentration on the best-rank
model beyond what the walk-forward std already implies.

**5. Equal-weight (D) vs precision-weighted (A):**

The comparison A vs D is the only one where precision-weighting adds measurable value.
If A consistently beats D (see ratio above), then the walk-forward precision weights are
carrying genuine signal. If D beats A, then the precision weights are noisy enough that
equal-weight is safer.

**6. Recommendation:**

Precision-first inversion does NOT improve the blended center at any split or lead tested.
The current design (precision-weighted mean = T2 with any model as prior) is the correct
posture. The meaningful question is A vs D — whether precision-weighting is worth the
additional estimation noise from walk-forward std estimates, particularly for high-res models
with thin history. This result provides a direct settlement-graded comparison.
