# Methodology critique: per-city RAW best-subset model selection vs one-pot ensemble

- Created: 2026-06-17
- Last audited: 2026-06-17
- Authority basis: independent adversarial methodological review (operator request). Read-only re the repo; no code changed.
- Data: `/tmp/unbiased_test_forecasts.json` (51 cities, 33 dates 2026-05-15..2026-06-16, 14 models, lead-1 RAW day-ahead highs); settlements from `state/zeus-forecasts.db` (`settlements`, `temperature_metric='high'`, `authority='VERIFIED'`, F→C normalized), opened `immutable=1`.
- Reproduction scripts: `/tmp/repro.py`, `/tmp/null_test.py`, `/tmp/baselines.py`, `/tmp/cv_and_metric.py`, `/tmp/bin_metric.py`, `/tmp/confound_dup.py`, `/tmp/freq_normalized.py`, `/tmp/sanity.py`, `/tmp/debias_test.py`.

## Bottom-line verdict

**PARTIALLY REAL, but the headline and the operational conclusion are an OVERFITTING / mis-specified-objective ARTIFACT.**

Three separate things are true at once, and the prior analysis conflated them:

1. **There IS a small, statistically real point-MAE signal** that per-city RAW subset selection beats the raw one-pot mean. It survives a permutation null and honest rolling-origin CV. So it is *not pure selection noise*.
2. **The signal is a noisy proxy for per-city de-biasing.** A single scalar bias correction of the *full* ensemble equals or beats the entire 2^14 subset search (0.971 vs 0.990, p=0.617). The selection machinery spends 16,384-way overfitting risk to approximate one fitted parameter.
3. **The signal does NOT transfer to the trading objective.** On a settled-*bin* log-loss proxy (what the spine actually prices), the MAE-selected subset is a coin flip vs one-pot (1.696 vs 1.655, p=0.44, 26/49). Optimizing point-MAE of a raw mean is a **category error** relative to bin/q_lcb pricing.

And the specific operational claim the operator is skeptical of — *"gfs_global matters; it's in 28/49 best combos"* — is **a confounded artifact**. Normalized for eligibility and noise, gfs_global's excess selection is only +0.34, *below* several fine-resolution regionals. The raw count inverts the real ranking.

## Reproduction (faithful)

Re-ran the exact pipeline: per city, enumerate all non-empty subsets of the 13 center models (icon_seamless excluded), pick min-train-MAE subset on dates ≤2026-05-31, score test MAE on 2026-06-01..16, compare to raw mean of all present models.

| metric | value |
|---|---|
| pooled SELECTED test MAE | 0.990 |
| pooled ONE-POT test MAE | 1.121 |
| selection wins | 32/49 |
| gfs_global freq | 28 |

Matches the prior numbers exactly. Everything below attacks this reproduced result.

---

## Attack 1 — Overfitting / selection bias (permutation null)

Shuffled settlement labels within each city (breaks the date→settlement link), re-ran the *entire* select-then-test pipeline, 40 permutations.

| quantity | REAL | NULL (shuffled) |
|---|---|---|
| delta (sel − onepot) | **−0.130** | +0.085 ± 0.039 (range −0.000..+0.170) |
| selection wins | 32/49 | 19.5/49 |
| gfs_global freq | 28 | 10.7 ± 2.0 |

Two findings:
- **The point-MAE win is real, not pure noise.** Under the null, selection *hurts* test MAE (+0.085) because train-fit subsets generalize worse on random labels. The real −0.130 sits at the 0th percentile of the null (0% of nulls are more negative). Honest signal present.
- **The frequency counts are heavily noise-inflated.** Pure selection noise alone puts gfs_global in ~11 cities. So of the "28", roughly **11 are noise**; only the excess (~17) is real, and even that is confounded (Attack 7).

Optimism gap (selected-subset train vs test MAE): train 0.853 → test 0.990, **+0.138**. Train MAE understates true error by ~0.14°C for the selected subset — the standard "you selected on it" inflation.

## Attack 2 — Significance, outliers, multiple comparisons

Paired tests across 49 cities, plus outlier sensitivity.

- sel vs one-pot: paired-t p=0.028, Wilcoxon p=0.007. Significant — but barely, and on a metric that doesn't match the objective (Attack 3).
- **Outlier-carried.** Top-3 improvement cities = Seoul (−1.76), Lucknow (−1.02), Kuala Lumpur (−0.98). Removing just those three drops the pooled improvement from **−0.130 to −0.057** — ~56% of the total "win" lives in 3/49 cities. Robust **median delta is −0.098** (the mean is inflated by the tail). The operator's "3-4 outlier cities" instinct is correct.

## Attack 3 — Is the metric even right? (the deepest flaw)

The live spine prices **bins** → q_lcb → edge, not a point. Built a settled-bin log-loss proxy: calibrate a normal on the subset-mean's train residual (mean+sd), score probability mass on the settled 1°C bin in test.

| objective | MAE-selected subset | one-pot ALL | paired-t p | sel wins |
|---|---|---|---|---|
| point-MAE (the prior metric) | 0.990 | 1.121 | 0.028 | 32/49 |
| **settled-bin log-loss (trading proxy)** | **1.696** | **1.655** | **0.44** | **26/49** |

Within-city, the Spearman correlation between train-point-MAE and test-bin-log-loss across candidate subsets is only **+0.23** (31% of cities NEGATIVE). 

**Conclusion: optimizing point-MAE of a raw mean is a category error vs the bin objective.** Subset selection that nudges the *center* also collapses the member *spread*; for bins both matter, and the selection trades spread calibration for center accuracy. The point-MAE win is real and **does not survive** the move to the objective the system actually trades on. This alone disqualifies the method as a basis for choosing the spine's fusion set.

## Attack 4 — Baseline fairness (vs saner baselines, not the strawman)

| baseline | mean MAE | sel − base | sel wins | paired-t p |
|---|---|---|---|---|
| one-pot ALL (the prior strawman) | 1.121 | −0.130 | 32/49 | 0.028 |
| globals-only | 1.183 | −0.193 | 34/49 | 0.002 |
| anchor-only (ecmwf_ifs) | 1.569 | −0.579 | 42/49 | 0.000 |
| **median of all (robust)** | **1.087** | **−0.097** | 32/49 | **0.071** |
| ecmwf+gfs+icon (fixed sensible) | 1.203 | −0.213 | 31/49 | 0.006 |

The win is *not solely* a strawman artifact — it beats globals-only and a fixed sensible set. But against the **robust median** of all models (a one-line, zero-fit baseline) it is **not significant (p=0.071)**. Most of the apparent advantage over "one-pot mean" is just the mean being non-robust to a bad cold member (Seoul: gfs_global −5.7°C drags the mean), which the median already fixes for free.

## Attack 5 — Sample adequacy & leakage

- **Sample too small for per-city subset skill.** ~17 train days to choose among 8,191 subsets per city = ~2 effective points per bit of subset identity. This is below any reasonable threshold to *estimate* per-city model skill; it can only *fit* it.
- **No look-ahead leakage found.** F→C is applied to settlements only; selection uses train-window dates strictly ≤2026-05-31; `nanmean`/missing-data handling is per-date but uses only that date's members (no future info). Rolling-origin CV (select on all prior dates, predict next; 748 daily preds) reproduces the win (sel 0.972 vs onepot 1.128, median 1.094, both p<0.001) — so the single-split result is not a split artifact.
- **Forecasts are trustworthy.** Spot-check vs settlement: ecmwf_ifs corr 0.96 (Amsterdam), 0.80 (Seoul/Austin), 0.89 (Lucknow), 0.53 (KL — the weak one); plausible °C ranges; a **systematic −1 to −2°C cold bias** in most cities (Amsterdam −1.4, Seoul −1.3, KL −2.0). The cold bias is the real lever (Attack 6), consistent with the standing "coarse cell snaps away from the airport → cold for hot cities" finding.

## Attack 6 — Mechanism plausibility: it's de-biasing in disguise

Seoul deep-dive (test window, member bias vs settlement): gfs_global −5.70, jma −4.88, ukmo_global −4.36, ecmwf −1.07, gem −0.79, icon_global +0.46. The selection isn't picking gfs_global because it's *good*; it's assembling whatever members **cancel** on the ~17 train days. That is small-sample **error-cancellation**, not learned skill.

Direct test — replace the 2^14 search with one fitted scalar:

| method | test MAE |
|---|---|
| subset-selected (the trick) | 0.990 |
| one-pot ALL, raw | 1.121 |
| **one-pot ALL, per-city train de-bias (1 scalar)** | **0.971** |
| ecmwf anchor, per-city de-bias | 1.178 |

De-biased one-pot (0.971) **beats the selection trick** (0.990); head-to-head the selection wins only 20/49, p=0.617 (no real difference). **The subset selection is a noisy proxy for a single de-bias parameter.** It achieves the center-shift by dropping members instead of subtracting a bias — burning enormous selection variance to do badly what one regression coefficient does cleanly. Since de-bias dominates it and the spine reads RAW values, the honest takeaway is "the data wants a per-city bias correction," not "the data wants a bespoke RAW subset."

## Attack 7 — The gfs_global=28 claim is a confound

Two structural artifacts inflate it:
- **Eligibility confound.** The 6 global models are eligible in all 51 cities; regionals in 4–12 (gfs_hrrr 12, meteofrance 6, icon_d2 5, ukmo_uk_2km 4, gem_hrdps 4). "gfs_global 28 vs gfs_hrrr 6" compares 51 chances to 12. Not a like-for-like ranking.
- **icon_seamless is an alias dup.** It equals icon_global in 1327/1683 (79%) and icon_d2 in 165/165 (100%) — real model redundancy among the ICON family.

Eligibility- and null-normalized excess selection (real/elig − null/elig):

| model | real/elig | excess over noise |
|---|---|---|
| meteofrance_arome_france_hd | 0.67 | **+0.57** |
| ukmo_uk_deterministic_2km | 0.50 | **+0.49** |
| icon_d2 | 0.40 | +0.35 |
| gem_hrdps_continental | 0.75 | +0.34 |
| **gfs_global** | **0.58** | **+0.34** |
| gem_global | 0.38 | +0.24 |
| ecmwf_ifs | 0.42 | +0.17 |
| ukmo_global_10km | 0.42 | +0.14 |
| icon_global | 0.32 | +0.04 |
| jma_seamless | 0.26 | −0.06 |

Once you correct for how often each model *could* be picked and for noise, **gfs_global is mid-pack, and the fine-resolution regionals show the highest excess** — the exact opposite of the headline. The "coarse global is in 28 cities" story is an artifact of (a) eligibility, (b) noise-floor selection, and (c) error-cancellation. There is **no honest evidence that the 25km gfs_global is a uniquely valuable fusion member**; the cleanest reading (regionals + the anchor, plus de-bias) is consistent with the standing precision/cell-distance hypothesis.

---

## The CORRECT methodology

Given the data constraints (33 days, lead-1, one metric), here is how to answer the real question — *which models should the spine fuse per city* — without overfitting and tied to the actual objective.

1. **Optimize the trading objective, not point-MAE.** Score every candidate with the settlement-graded probabilistic loss the spine uses (settled-bin log-loss / Brier on q_lcb, or directly after-cost EV by bin class). Point-MAE of a mean is a category error and gave a non-result on the bin metric here.

2. **Kill the de-bias confound first.** Apply a per-city scalar (or per-model) de-bias fit on train *before* comparing structures, OR explicitly frame the question as "raw subset vs de-biased full ensemble." As shown, de-biased one-pot already matches the best raw subset — so the live answer is likely "fuse all (de-dup'd) globals + any eligible regional, then de-bias per city," not "search for a bespoke RAW subset."

3. **Constrain the hypothesis space.** Never enumerate 2^14 on 17 points. Restrict to ≤3-member subsets, or to a small menu of physically-motivated sets (anchor; anchor+regional; all-globals-de-dup'd; globals+regional). With ~5 candidates instead of 16,384, selection variance and the optimism gap collapse.

4. **De-duplicate the model list.** Drop icon_seamless (alias of icon_global/icon_d2). Treat the ICON family as correlated; do not let near-duplicates inflate "ensemble size."

5. **Honest validation.** Use rolling-origin (leave-future-out) or nested CV — never a single train/test split with selection inside train scored on test as if clean. Report the optimism gap.

6. **Proper significance.** Test per-prediction (daily, ~750 paired points) not per-city-mean (49 points, dominated by 3 outliers). Use a permutation null as the reference distribution for *every* headline (delta AND each model's selection frequency), and normalize frequency by eligibility.

7. **More data before per-city claims.** 16–17 train days cannot estimate per-city model skill — only fit it. Per-city selection needs either (a) far more history, or (b) pooling across cities (hierarchical/partial-pooling: a global model menu with per-city de-bias and shrinkage), which is the statistically defensible version of "which models per city."

**If forced to ship from this data:** do NOT deploy per-city RAW subsets. Ship **de-dup'd full-ensemble fusion + per-city scalar de-bias**, optionally adding eligible high-resolution regionals (which show the real excess signal). That captures the entire honest improvement, ties to the settlement, and carries one fitted parameter per city instead of a 13-bit overfit.
