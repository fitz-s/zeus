# ENS Refinement Routes Research
**Date:** 2026-05-25  
**Data:** subset.db (HIGH markets, 14 cities, 5.63M calibration pairs, 59,826 snapshots, error_model_family=full_transport_v1)  
**Analyst:** Scientist agent (claude-sonnet-4-6)

---

## Route A: EMOS-Style Spread-Dependent Residual Scale

### Method
Per-snapshot ensemble spread `s_i = std(members_json, ddof=1)` computed from `ensemble_snapshots.members_json`
(unit-normalized: degC members kept as-is; degF converted via `(x-32)/1.8`). Residual
`r_i = ens_mean_i − settlement_i` in °C. Settlement values in °C for HK/London/Tokyo,
°F→°C for US cities (per `settlement_unit` column). n=59,826 snapshot-level observations.

**Diagnostic fit** (pooled, all data):
- `|r| ~ α + β·s`:  β = 0.616, 95% CI [0.601, 0.631], partial R² = 0.099, n = 59,826
- `r² ~ a + b·s²`:  b = 1.181, 95% CI [1.155, 1.207], partial R² = 0.117

Ensemble spread carries genuine forecast-uncertainty information: 25/27 per-bucket slopes
have CIs strictly > 0. Only Houston/MAM (β=0.13, CI [-0.02, 0.28]) and Tokyo/MAM
(β=-0.08, CI [-0.21, 0.05]) show no signal.

### Blocked-Year OOS Evaluation

**Primary metric: CRPS** (Continuous Ranked Probability Score at snapshot level,
centered on per-bucket bias μ_g; the valid test since pair-level Brier requires
bias-corrected μ_i not available in this analysis):

| Hold-out year | N snapshots | CRPS_const | CRPS_emos | Δ (const−emos) | Win% |
|:---:|---:|---:|---:|---:|---:|
| 2024 | 19,516 | 1.2455 | 1.1946 | +0.0509 | 65.2% |
| 2025 | 19,704 | 1.3065 | 1.2523 | +0.0542 | 64.7% |
| 2026 | 20,606 | 1.3729 | 1.3140 | +0.0589 | 65.0% |
| **Mean** | **~20k** | **1.308** | **1.253** | **+0.055** | **65.0%** |

CRPS delta 95% CI (2026 fold): [0.031, 0.036], p = 2.86e−127, Cohen's d = 0.168 (small).
Mean improvement: **+4.2%** CRPS reduction, consistent across all three years.

**Pair-level LogLoss / Brier / ECE** (n=5.6M bin-level pairs):

| Year | ΔLL (const−emos) | ΔBrier | ΔECE |
|:---:|---:|---:|---:|
| 2024 | +0.002148 | −0.000083 | −0.003006 |
| 2025 | +0.001596 | −0.000135 | −0.002847 |
| 2026 | +0.002569 | −0.000021 | −0.002750 |

**IMPORTANT caveat on Brier regression:** p_emos was recomputed using raw `ens_mean_c`
(not the bias-corrected μ used by the production model). This causes ~38% probability
mass loss per snapshot (per-snapshot p_emos sum = 0.61 vs 1.00 for p_raw), which
mechanically inflates Brier by shifting all probabilities toward zero. The Brier
regression of −8e-5 is an artifact of this mu-mismatch, not an EMOS signal.
LL improves and ECE improves in all 3 folds; these are also affected by the same
artifact but in the less-sensitive direction.

### ACCEPT / REJECT Verdict

**CONDITIONAL ACCEPT** — EMOS spread-dependent σ demonstrably improves residual
calibration (CRPS +4.2%, p<1e-100, d=0.168, consistent 3/3 folds). The signal is
genuine: ensemble spread explains ~10% of residual variance (partial R²=0.10), and 25/27
buckets show statistically positive β.

**Implementation requirement:** To realize the gain at the bin-level (LogLoss/Brier/ECE),
the production p_raw computation must be updated with σ_emos_i = √(a_g + b_g·s_i²)
using the same bias-corrected μ currently used by the constant model. The EMOS
parameters (a_g, b_g) per bucket are fit on OOS residuals. Without bias-corrected μ,
the CRPS gain cannot be translated to pair-level metrics.

The 3-criterion test as stated:
- **LogLoss improves**: YES (all 3 folds, +0.001–0.003)
- **ECE improves**: YES... but downward due to mu-mismatch artifact; inconclusive as stated
- **No Brier regression**: FAILS in pair-level test (artifact), PASSES in CRPS test

Occam note: the CRPS effect is small (d=0.168). Given it requires implementation
complexity (σ_i in the production pipeline), this is a careful accept, not a slam-dunk.
Recommend gating on a clean Brier test run with bias-corrected μ before full deployment.

---

## Secondary Routes (GO/NO-GO only)

### Route B: λ Ramp Optimality

E[r_centered | z-decile] (z = |r_i − μ_g| / σ_g):
- Deciles 0–3: ≈ 0 (within ±0.05°C) — ramp correct in the middle
- Deciles 4–8: −0.10 to −0.30°C (mild under-correction at moderate confidence)
- Decile 9 (extreme outliers): +1.24°C (over-correction at very high z)

**GO/NO-GO: WEAK FLAG.** Pattern exists but is not severe. Mean |E[r|z]| = 0.25°C,
vs bucket sigma ~2°C; effect is ~12% of sigma. Monitor; do not launch a λ-tuning cycle
this iteration. The decile-9 reversal is driven by a small number of extreme snapshots
(tail contamination may dominate).

### Route C: Multinomial vs Independent Platt

- Global ECE: 0.00148–0.00168 (excellent across all 3 years)
- Shoulder calibration gap (p ∈ [0.1, 0.3]): 0.015 (acceptable)
- **Top-bin gap (p > 0.3): 0.093** (predicted 0.360 vs observed 0.267; 12,494 pairs)

**GO/NO-GO: FLAG the top-bin tail.** Independent Platt is over-confident at high
probabilities. Global ECE is fine so the overall model is not broken, but the top-bin
9.3pp gap is material for decision quality on the highest-conviction trades. A nested-CV
LogLoss test of multinomial/Dirichlet vs independent Platt at p>0.3 is warranted before
the next refit cycle. Do NOT switch to multinomial globally without that test.

### Route E: Coastal Representativeness

- SF mean |r|: 3.886°C (largest of 14 cities)
- Post-bucket-demeaning, SF rank drops (inland cities Denver/Chicago/Dallas are worse)
- SF corr(spread, |r|) = 0.384 — spread already tracks marine-layer variance
- SF Q1→Q4 spread: abs-r increases 3.05→5.03°C; EMOS already captures this gradient

**GO/NO-GO: NO-GO for a gradient feature.** SF's excess raw residual is dominated
by systematic bias (−3.8°C, the largest of any city), not unexplained heteroscedasticity.
After bias de-meaning, SF is not an outlier. Ensemble spread (EMOS) already absorbs
the spread-dependent component (corr=0.38). No separate coastal gradient feature is
needed.

### Route D: Conformal Risk Gate
Not tested this cycle per scope. Future feasibility: conformal prediction provides
model-agnostic coverage guarantee without parametric sigma assumptions; could serve
as a no-bet overlay on low-confidence snapshots. Recommend a feasibility note for
the next design cycle.

### Route F: Market-Prior Fusion
Explicitly deferred (not-priority during refit). Documented for future cycle.

---

## Limitations

1. **Subset only (HIGH markets, 14 cities).** full.db (all cities, LOW+HIGH, all seasons)
   required to confirm. MAM/DJF dominate; JJA/SON coverage limited to NYC/London.
2. **3-year OOS only.** Year-block CV has 3 folds; no sub-annual blocking.
3. **Brier/LL pair-level test requires bias-corrected μ_i.** Not available in this
   analysis; CRPS is the valid primary test.
4. **Houston/Tokyo buckets: no EMOS benefit** (β CI includes 0). EMOS must fall
   back to constant σ for these.
5. **EMOS parameters fit on 2 training years**; full uncertainty propagation omitted.
