# C3 Adjacent-Ring Calibration Surface
**Generated:** 2026-06-12  
**Status:** READ-ONLY analysis — no code changes made  
**DB:** `state/zeus-forecasts.db` (mode=ro)

---

## Scope and Data Inventory

**Settled cells with matched posteriors:** `high` temperature metric only (the primary market). `low` metric has posteriors for exactly 8 cities: Hong Kong, London, Miami, NYC, Paris, Seoul, Shanghai, Tokyo — sample too thin (≤20 cells total) for calibration; excluded.

**Date range of study:** 2026-06-08 to 2026-06-11 (4 target_date days — the only window where `settlements` and `forecast_posteriors` overlap).

**Lead convention:**  
- **Bucket A (≈24h):** `computed_at` is 12–36h before `target_date 00:00 UTC`  
- **Bucket B (≈48h):** `computed_at` is 36–60h before `target_date 00:00 UTC`  
- For each (city, target_date, bucket), the **freshest** posterior within the bucket is used.

| Bucket | Unit | Cells | Cities | Interior bin-records |
|--------|------|-------|--------|----------------------|
| A_24h  | C    | 127   | 38     | 928 (d=0..4)         |
| A_24h  | F    | 25    | 11     | 194 (d=0..4)         |
| B_48h  | C    | 67    | 33     | 480 (d=0..4)         |
| B_48h  | F    | —     | —      | 0 (no B-bucket posteriors exist for F cities) |

**Winning-bin matching:** 3-pass: (1) substring match winning_bin vs label, (2) center ± 0.6 step, (3) tail direction check. Match rate = 219/219 cells (100%).

**Step sizes:** C cities = 1°C/bin; F cities = 2°F/bin (cities use 2°F-wide interior bins: 82-83°F, 84-85°F etc.).

---

## TABLE 1: Distance × Lead Calibration

`dist` = bins from mode; `ratio` = realized_freq / mean_q; n < 30 flagged. Tail bins excluded throughout.

```
bucket     dist  unit  n_bins  sum_q    actual_wins  realiz_freq  ratio_r/e  CI_lo   CI_hi
------------------------------------------------------------------------------------------
A_24h      0     C     118     49.679   21           0.1780       0.423      0.1194  0.2568
A_24h      1     C     238     48.852   42           0.1765       0.860      0.1333  0.2299
A_24h      2     C     227     14.192   36           0.1586       2.537      0.1168  0.2117
A_24h      3     C     196      4.014   16           0.0816       3.986      0.0509  0.1285
A_24h     >=4    C     364      2.250    9           0.0247       4.001      0.0131  0.0463

A_24h      0     F      25     11.195    8           0.3200       0.715      0.1720  0.5159  ***<30
A_24h      1     F      49     10.011    7           0.1429       0.699      0.0710  0.2667
A_24h      2     F      48      2.545    5           0.1042       1.964      0.0453  0.2217
A_24h      3     F      43      0.479    4           0.0930       8.353      0.0368  0.2160
A_24h     >=4    F      60      0.165    0           0.0000       0.000      0.0000  0.0602

B_48h      0     C      59     24.883   10           0.1695       0.402      0.0948  0.2846
B_48h      1     C     126     25.675   26           0.2063       1.013      0.1449  0.2852
B_48h      2     C     119      7.706   15           0.1261       1.947      0.0779  0.1976
B_48h      3     C     107      1.784    9           0.0841       5.046      0.0449  0.1522
B_48h     >=4    C     192      0.636    5           0.0260       7.856      0.0112  0.0595
```

**Key pattern:**
- **d=0 (mode bin): massive overconfidence.** Mean q ≈ 0.43–0.46, realized win rate ≈ 0.16–0.19 for C cities. Ratio ≈ 0.40 (model assigns 2.5× too much probability to the mode bin).
- **d=1 ring:** A_24h C ratio = 0.86 (roughly calibrated). B_48h C ratio = 1.01 (well calibrated). A_24h F ratio = 0.70.
- **d=2 ring:** Ratios 1.9–2.5 — model assigns too little probability. This is the C3 finding.
- **d=3:** Ratios 4–8 — severely underweighted. CIs wide; n=43–196 per cell.
- **d≥4:** Underweighted by 4–8×, but very small absolute probability; CIs include zero at the low end.

**C3 finding confirmed:** d=2 realized/expected ≈ 2×–2.5× for both lead buckets (C), and ≈ 2× for A_24h F. The mode overconfidence (ratio ≈ 0.4 at d=0) transfers mass away from all ring bins.

---

## TABLE 2: q-Decile Breakdown (distance ≤ 2, interior bins only)

Is the d≤2 miss concentrated in low-q bins?

### A_24h / C (n=583 bins at d≤2)
```
decile   n     mean_q   wins  realiz  ratio   CI_lo  CI_hi   q_range
D0       59    0.0011    9    0.1525  142.6   0.0824 0.2652  [0.000,0.012]
D1       58    0.0237   10    0.1724    7.3   0.0964 0.2891  [0.013,0.039]
D2       58    0.0551    9    0.1552    2.8   0.0838 0.2693  [0.040,0.076]
D3       59    0.0988    6    0.1017    1.0   0.0474 0.2046  [0.076,0.122]
D4       58    0.1402   10    0.1724    1.2   0.0964 0.2891  [0.122,0.155]
D5       58    0.1845   14    0.2414    1.3   0.1496 0.3653  [0.159,0.208]
D6       59    0.2352    7    0.1186    0.5   0.0587 0.2252  [0.210,0.266]
D7       58    0.2971   14    0.2414    0.8   0.1496 0.3653  [0.266,0.331]
D8       58    0.3766   12    0.2069    0.5   0.1225 0.3277  [0.332,0.428]
D9       58    0.5254    8    0.1379    0.3   0.0716 0.2493  [0.428,0.805]
```

### A_24h / F (n=122 bins at d≤2, n<30 per decile — treat as indicative only)
```
D0       13    0.0002    2    0.1538  793.5   [0.000,0.001]
D1       12    0.0109    0    0.0000    0.0   [0.001,0.017]
D2       12    0.0445    3    0.2500    5.6   [0.019,0.062]
D3       12    0.0906    1    0.0833    0.9   [0.069,0.116]
D4       12    0.1449    1    0.0833    0.6   [0.122,0.171]
D5       13    0.2008    2    0.1538    0.8   [0.172,0.219]
D6       12    0.2410    1    0.0833    0.3   [0.222,0.269]
D7       12    0.2927    4    0.3333    1.1   [0.271,0.317]
D8       12    0.3873    3    0.2500    0.6   [0.330,0.454]
D9       12    0.5497    3    0.2500    0.5   [0.462,0.792]
```

### B_48h / C (n=304 bins at d≤2)
```
D0       31    0.0002    5    0.1613  1060.2  0.0709 0.3263  [0.000,0.001]
D1       30    0.0249    3    0.1000    4.0   0.0346 0.2562  [0.001,0.043]
D2       31    0.0557    4    0.1290    2.3   0.0513 0.2885  [0.045,0.069]
D3       30    0.0921    4    0.1333    1.4   0.0531 0.2968  [0.070,0.119]
D4       30    0.1465    4    0.1333    0.9   0.0531 0.2968  [0.122,0.170]
D5       31    0.1946    7    0.2258    1.2   0.1139 0.3981  [0.172,0.219]
D6       30    0.2402    7    0.2333    1.0   0.1179 0.4093  [0.219,0.261]
D7       31    0.2937   10    0.3226    1.1   0.1857 0.4986  [0.261,0.325]
D8       30    0.3652    4    0.1333    0.4   0.0531 0.2968  [0.325,0.396]
D9       30    0.5109    3    0.1000    0.2   0.0346 0.2562  [0.396,0.831]
```

**Decile findings:**
1. **Extreme low-q bins (D0: q ≈ 0.000–0.012) massively underestimated** — these near-zero bins win at 13–16% frequency. This is partially a model floor effect (posterior clips near-zero), but also genuine fat-tail miss.
2. **Mid-q (D3–D7, q ≈ 0.08–0.33) is approximately calibrated** — ratios 0.8–1.4 for A_24h C, ~1.0–1.2 for B_48h C.
3. **High-q (D8–D9, q > 0.33) is overconfident** — these are the mode-adjacent bins where the model is too peaked. Ratio 0.2–0.5.
4. **The C3 miss is NOT concentrated in only low-q ring bins** — it spans D0–D2 (near-zero to mid-q within the ring). The miss is structural: the posterior is too peaked, not just too conservative on a specific q slice.

---

## Market Price Comparison

**Not joinable.** `market_price_history` (the only populated table) has a hard ceiling of `2026-05-28T06:10Z`. Our settlement study window starts `2026-06-08`. Gap = 11 days with no overlap. `executable_market_snapshots`, `market_microstructure_snapshots`, and `token_price_log` are all empty in both `zeus-forecasts.db` and `zeus-world.db`. No market-implied q can be reconstructed for the settled cells.

The C3 finding (market priced adjacent-ring bins at 2–2.5× our q) comes from the three 2026-06-12 trade observations cited in the brief — not from this dataset, which cannot reconstruct pre-settlement market prices for those events.

---

## Implied σ Multiplier Fitting

**Method:** For each settled cell, the implied σ is back-solved from the mode bin probability using:  
`q_mode = Φ(0.5/σ) − Φ(−0.5/σ)` → `σ_implied = 0.5 / Φ⁻¹((q_mode+1)/2)`

This treats the posterior as locally normal in temperature-step units (1°C or 2°F per step). k is then the global multiplier on σ_implied.

**σ_implied distribution (in units of 1 bin-step):**

| Bucket | Unit | n  | min   | p25   | median | p75   | max   |
|--------|------|----|-------|-------|--------|-------|-------|
| A_24h  | C    | 127 | 0.150 | 0.726 | 0.888  | 1.191 | 2.819 |
| A_24h  | F    |  25 | 0.397 | 0.744 | 0.828  | 1.099 | 1.490 |
| B_48h  | C    |  67 | 0.364 | 0.741 | 0.963  | 1.094 | 1.962 |

Median σ_implied ≈ 0.89–0.96 for C (very peaked distributions — mode concentrates ~40% probability in a single 1°C bin).

**Fit results — MSE-minimizing k (over d=0,1,2,3,4):**

| Bucket | Unit | n_cells | best_k | Note |
|--------|------|---------|--------|------|
| A_24h  | C    | 127     | **2.6** | d=0 adjusted: 49.7→20.3 (actual=21) ✓ |
| A_24h  | F    |  25     | **2.3** | n<30 cells — treat as directional |
| B_48h  | C    |  67     | **2.5** | d=0 adjusted: 24.9→10.5 (actual=10) ✓ |

**What k achieves per distance for A_24h C (k=2.6, best case):**

```
dist  n_bins  actual_wins  q@k=1.0  q@k=2.6  q@k=2.0
d=0   118     21           49.7     20.3      26.2     ← near-perfect fix at d=0
d=1   238     42           51.6     36.0      42.7     ← k=2.0 is closer for d=1
d=2   227     36           12.1     24.1      24.0     ← both overcorrect d=2
d=3   196     16            2.5     12.9      10.3     ← both overcorrect d=3
d=4   149      6            0.6      5.5       3.6     ← both overcorrect d=4
```

**Critical limitation:** The normal approximation has a probability ceiling — widening σ moves mass out of d=0 into d=1,2 but saturates. At k=1.5 the ring wins are maximized for ring-only (d=1,2) fit; k=2.6 is needed to fix d=0 but then overcorrects d=2,3. No single k-multiplier on a normal posterior simultaneously fixes all distances.

**k for mode-only fix (σ_median-based):**
- A_24h C: k_fix ≈ **2.36** (target realized = 0.189)
- A_24h F: k_fix ≈ **1.47** (target realized = 0.320; n<30)
- B_48h C: k_fix ≈ **2.51** (target realized = 0.164)

---

## What Correction to Implement

### Finding 1 (strongest evidence): The posterior is too peaked by ~2.5×

The mode bin is assigned mean q ≈ 0.43–0.46 but wins at 0.16–0.19 for C cities. This is the structural root: σ_pred is approximately half what it should be. The posterior integration is producing distributions that are too narrow by a factor of ~2.5 in σ (or equivalently, the integration temperature/ensemble spread is insufficient).

**Option A — σ floor raise (simplest, highest confidence):**  
Impose a minimum σ_pred such that the implied mode-bin probability is ≤ 0.22 (≈ 1 step / 4.5-step effective width). Current median mode-bin q = 0.43 for C; floor at q_mode ≤ 0.22 would imply σ_floor ≈ 2.2 steps ≈ **2.2°C** for C cities, **4.4°F** for F cities.  
Evidence strength: **HIGH** (n=127 C cells, effect is 2.5× with tight CI; consistent across both lead buckets).  
Risk: a floor is blunt — it cannot distinguish genuinely peaked (correct) from artificially peaked (bad) cases.

**Option B — lead-dependent σ scale (more precise):**  
Multiply σ_pred by k before integration:  
- A_24h (12–36h lead): k ≈ **2.4**  
- B_48h (36–60h lead): k ≈ **2.5**  
Both buckets give similar k for C cities, suggesting a **single factor k ≈ 2.4–2.5** is appropriate regardless of lead within this 12–60h range.  
Evidence strength: **HIGH** for C (n=127+67 cells). **LOW** for F (n=25 cells, k≈2.3 directional only).

**Option C — distance-dependent shape mix:**  
A fixed-σ normal cannot simultaneously calibrate d=0 and d≥2 — the normal's shape is too thin in the tails even after scaling. A heavier-tailed shape (e.g., Student-t or mixture with uniform component) would better match: d=0 near 0.18, d=1 near 0.20, d=2 near 0.16, d=3 near 0.08. The flat realized frequency across d=0,1,2 (all ≈ 0.16–0.21 for C) is inconsistent with any unimodal normal regardless of σ.  
Evidence strength: **MEDIUM** — directionally clear but requires shape parameterization with n=4 days of data.

### Recommended correction

**Implement Option B (σ scale k ≈ 2.4–2.5 for C, applied before posterior integration) as the immediate structural fix.** This brings d=0 into calibration (ratio ≈ 1.0), which is the largest single source of error by probability mass. It simultaneously raises d=2 mass (though likely to overcorrect — see table above), narrowing the C3 undercount.

**Combine with Option C signal:** Monitor d=2 realized/expected after k is applied. If d=2 overcorrects (ratio drops below 0.5), add a 5–10% uniform floor across all bins (a mixture weight on uniform = probability of "anything can happen") — this fixes the flat realized curve without another free parameter.

**Do not apply to F cities yet:** n=25 cells is insufficient to separate k from sampling noise. The existing market-anchor cap provides interim protection.

### σ multiplier table

| Unit | Lead bucket | Recommended k | Confidence | n_cells |
|------|-------------|--------------|------------|---------|
| C    | A_24h (12–36h) | 2.4       | HIGH       | 127     |
| C    | B_48h (36–60h) | 2.5       | HIGH       | 67      |
| F    | A_24h          | ~2.3 (indicative) | LOW | 25     |
| F    | B_48h          | — (no data)       | —   | 0      |

### Caveats

- **4-day window:** All 219 settled cells fall in 2026-06-08 to 2026-06-11. This is one weather regime. k may vary seasonally or with synoptic pattern.
- **Single posterior_method:** All posteriors in study are `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor`. No legacy method comparison possible.
- **Normal approximation:** σ back-solve from mode bin assumes unimodal symmetric distribution. Multimodal or skewed posteriors will have incorrect σ_implied.
- **Tail bins excluded:** Tail behavior (d beyond bin grid) not characterized here; tail undercount also likely given d≥4 ratios of 4–8×.

---

## Implementation — FITTED artifact (operator law 2026-06-12, supersedes the hand-set k)

OPERATOR LAW 2026-06-12 "没有一个人可以在没有数学支持下决定一个 hard coded value": the σ-scale factor
must be FITTED by math, never operator-picked. The settings key `replacement_sigma_scale_k_c` was
DELETED. The correction is now a fitted artifact:

- **Estimator:** `scripts/fit_sigma_scale.py` — MLE of (k, w) for the mixture
  `q_adj(bin) = (1−w)·Normal(σ_impl·k) + w·(1/n_bins)` over the Bernoulli win/loss of every settled
  (cell, bin) pair. C and F families fit separately; profile-likelihood 95% CIs (or `--bootstrap N`
  over cells). REFUSES a family with < 60 settled cells (writes `fitted:false`, k=1, w=0 → inert).
- **Artifact:** `state/sigma_scale_fit.json` — `{families: {C|F: {fitted, k, w, ci, n_cells, ...}}}` +
  provenance/source-query hashes + `data_window`. The fit script is its ONLY writer.
- **Materializer:** `src/data/replacement_forecast_materializer.py` reads the artifact via
  `_replacement_sigma_scale_lookup(unit)` (fail-soft → (1.0, 0.0) when missing / family unfitted).
  σ-scale k applies before the settlement sigma floor (floor stays a lower bound); the uniform
  mixture w applies to the final normalized q, with the catch-all coherence cap (an open-ended
  bin is re-capped at its honest predictive-σ mass so neither the floor nor the mixture can inflate
  it). Provenance: `sigma_scale_k_applied` + `uniform_mixture_w_applied`.
- **Enabling:** there is no flag — *the artifact existing with the family fitted IS the enable.* F
  refuses today (n=47 < 60) → inert automatically.
- **Refit cadence (auditable):** re-run weekly (or as settlements accrue) so k tracks data growth —
  `python scripts/fit_sigma_scale.py` (operator/scheduler-invoked; read-only over zeus-forecasts.db,
  writes only the artifact). Every refit stamps `data_window` + `provenance_hash`, so any change in k
  is traceable to the settled-cell window it was fit on. No daemon restart is required to pick up a
  new artifact (the materializer reads it per-materialization).

### First fit on real data (2026-06-12, window 2026-06-08..2026-06-11)

| Family | fitted | k | w | n_cells | CI_k (95%) | CI_w (95%) |
|--------|--------|------|------|---------|------------|------------|
| C | yes | **1.58** | **0.28** | 215 | [1.32, 1.88] | [0.17, 0.41] |
| F | **no (refused)** | 1.0 | 0.0 | 47 | — | — (n<60) |

Per-distance calibration for C, before vs at the fitted (k, w) — the proof the fit works:

```
dist  n_bins  q@k1w0  realized  ratio_before   |   q@fit  ratio_at_fit
0     200     0.428   0.220     0.514          |   0.229   0.961   <- mode overconfidence removed
1     408     0.216   0.191     0.884          |   0.172   1.115
2     390     0.052   0.133     2.572 (C3)     |   0.093   1.435   <- C3 undercount narrowed
3     342     0.012   0.070     5.769          |   0.052   1.361
>=4   595     0.002   0.019     9.162          |   0.031   0.593
```

The jointly-fitted k=1.58 is lower than the surface's hand-picked k=2.4 because the uniform mixture
w=0.28 does part of the flattening — the ML joint optimum (proper scoring rule) over (k, w), not k
alone under MSE. The math decides the correction entirely; there is no operator number to flip.

---

*Original report (above the Implementation section) covered zeus-forecasts.db settlements +
forecast_posteriors only and modified no code. The Implementation section was added when the fitted
artifact replaced the hand-set key (operator law 2026-06-12).*
