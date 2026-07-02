# Per-city capital-gated ρ-mix — MULTI-ANGLE BEFORE/AFTER evaluation (前后对比 / 多角度评估 / 数学论证)

**Date:** 2026-06-30
**Money-path stage:** forecast signal → **calibration** (the served settlement-bin q).
**Authority basis:** operator — "目的是前后对比 多角度评估和数学论证使得一个功能被improve还是开启 没有别的选项" (the purpose is before/after comparison + multi-angle evaluation + mathematical proof, so a feature is improved or turned on — no other option). The evaluation is the decision; the gates are the arbiter ([[ship-decision-is-math-stats-not-approval]]).
**Reproduce:** `PYTHONPATH=. python3 scripts/percity_multiangle_before_after.py` (read-only on live DBs; reuses the after-cost EV gate's FAITHFUL per-cell reconstruction). Paired gate: `scripts/percity_after_cost_ev_gate.py`.

## The change under evaluation
BEFORE = `q_global` (today's served family pair, k/w MLE). AFTER = `q_serve = (1−ρ)·q_global + ρ·q_cityEB`, `ρ = 1−exp(−C/W)`, served only for cities with positive earned out-of-sample capital `C`. 18 C + 9 F cities served; ρ ≤ 0.23 (≥77% proven-global even for the strongest city).

## 前后对比 + 多角度评估 — six angles, settled window 2026-06-10..06-28

| angle | what it scores | BEFORE | AFTER | Δ (positive = AFTER improves) |
|---|---|--:|--:|--:|
| log-loss (winner) | multiclass −log q(winner) | 771.4365 | 770.1549 | **+1.2816** |
| Brier (all bins) | Σ_b (q_b − y_b)² | 356.6350 | 356.3780 | **+0.2571** |
| RPS (ordinal) | Σ_k (CDF_q − CDF_y)² | 325.9335 | 325.1532 | **+0.7804** |
| ECE (reliability) | \|mean q − realized freq\| by q-decile, 5137 bin-obs | 0.02091 | 0.02068 | **+0.00023** (better calibrated) |
| **OOS prequential capital ΣC** | leak-free Bernoulli log-score, rolling splits | — | — | **+27.7127** |
| after-cost EV (decisions) | realized (won−cost) on the live q_lcb carrier | −12.6931 | −12.6836 | **+0.0094** (27/27 cities ≥0) |

Graded cells (faithful reconstruction + mapped winner): ~450 across 27 cities. Drops: no_predictive_or_center_sigma 78, no_posterior 23, unfaithful_point 13 (day0/non-fused carriers — dropped, never graded on an unfaithful rebuild).

In-window log-loss: **20/27 cities better, 7 worse**. The 7 worse (winner-only lens): Seoul −0.0669, Jeddah −0.0331, Atlanta −0.0282, Austin −0.0085, Kuala Lumpur −0.0011, Seattle −0.0005, Dallas −0.0000.

## The 7 "worse" cities — interpreted (why multi-angle matters)
6 of the 7 are exactly the cities whose cityEB **widens** the forecast (k_eb > k_global; e.g. Jeddah k_eb 0.819 vs global 0.697); the 7th, Dallas, is a ρ=0 no-op (C=0.000, Δ=−0.0000 numerical noise). Widening **improves the Bernoulli proper score** (the fit objective — lower mass on the many losing tail bins, where the term is −log(1−q)) and **improves the buy_NO / after-cost-EV path** (weather books are buy_NO-dominated), but it lowers q on the *single center winner bin*, so the winner-only multiclass log-loss dips for those cities in-window. This is not a contradiction: different proper scores weight bins differently. The two **money-relevant** angles — OOS Bernoulli capital `C` and realized after-cost EV — are non-inferior for **all 27** served cities; the winner-only lens is mixed and money-secondary. Tokyo (+0.328), Chongqing (+0.223), Tel Aviv (+0.187), Wellington (+0.169), Milan (+0.138) lead the in-window winner-log-loss gains.

## 数学论证 — the guarantee behind the comparison
- **Serving law:** `q_serve = (1−ρ)·q_global + ρ·q_cityEB`, `ρ = 1−exp(−C/W)`, `ρ=0` if `C≤0`.
- **Pathwise non-inferiority:** every Bernoulli bin term loses at worst `log(1−ρ)` vs global, so a batch of `W` eligible bins loses at worst `W·log(1−ρ)`. With `ρ = 1−exp(−C/W)` ⇒ `W·log(1−ρ) = −C`: **a city can never spend more proper-score than the capital `C` it earned out-of-sample.** `C≤0 ⇒ ρ=0 ⇒ q_serve == q_global` (byte-identical) ⇒ structurally cannot harm. Hence AFTER ≥ BEFORE out-of-sample **per city by construction**; the in-window deltas show realized magnitude, the after-cost EV gate shows it survives thresholded trade decisions.
- **Byte-identical-global:** ρ=0 cities (and a no-cities artifact) reproduce today's q exactly (builder goldens, full k→floor→catch-all-cap→uniform ladder).

## Verdict
The money-relevant angles are uniformly non-inferior (OOS capital +27.71 across 27 cities, after-cost EV +0.0094 with 0/27 cities negative), the descriptive in-window proper scores and the calibration reliability (ECE) are net-positive on all six aggregates, and the per-location non-inferiority is guaranteed by construction. The honest mixed signal (7 wideners worse on the winner-only lens) is interpretable and money-secondary. The evaluation supports turning the feature ON.
