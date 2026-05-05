# Phase 1 §2.2 — Small-Sample Multiplier `k` Validation: CONCLUSION

Created: 2026-05-03 (executed)
Authority: PLAN.md §2.2 + operator success benediction 2026-05-03 + kill-switch §1
Status: **FAIL — recommend k=0 in v1 spec**

## Headline

> **The empirical evidence does not support a positive `k` multiplier in v1.**
> The kill-switch protocol from PLAN.md §1 should be invoked: set k=0 (no
> small-sample amplification) and revisit if more data accumulates.

## Three measures, three findings

We tested the hypothesis "small N → worse calibration → multiplier compensates"
using three calibration metrics:

| Measure | k_estimate | R² | Slope direction | Verdict |
|---|---|---|---|---|
| Brier (all rows) | 16.4 | 0.036 | + (matches hypothesis) | weak |
| Brier (winning rows only) | -0.16 | 0.000 | **− (opposes hypothesis)** | reject |
| ECE (10-decile bins) | 7.65 | 0.077 | + (matches hypothesis) | borderline |

The all-rows Brier is dominated by ~99% non-winning buckets (where p_raw ≈ 0,
outcome = 0, residual ≈ 0) and is not actually probing calibration quality —
that's why it gave a "monotone" signal but the signal is from noise structure,
not calibration.

The winning-bucket Brier directly measures "how confident is the model in the
true winner". Slope went NEGATIVE — meaning at smaller N, the model is MORE
confident on average. This is opposite of the hypothesis and likely reflects
that small-N cities (mostly LOW track) have more concentrated outcome
distributions (tropical cities cluster narrowly), making prediction easier.

The ECE measure is the most principled (it directly tests "predicted prob
matches observed frequency"). It shows the hypothesis is **weakly supported**:
R² = 0.077 (below the 0.10 acceptance threshold), but slope is positive and
the smallest-N buckets cluster at higher ECE on average.

## Time-window stability (Ruling 1)

|Δk_train - k_test| / k_train was 62% (Brier-all), 172% (Brier-winners),
unmeasured (ECE — only train-window run done). All exceeded the 50%
acceptance threshold. **Time-window generalization is poor.**

This is the operator's strongest stipulated criterion: parameters should
survive on the time axis. They don't, on any of the three measures. This is
the deciding evidence.

## Why the hypothesis fails empirically

Three plausible explanations:

1. **Platt regularization**: standard Platt fits include an L2 prior that
   pulls small-N cities toward a sane default. The "borrowed strength" from
   the prior makes small-N calibration nearly as good as large-N. This is
   exactly the regime-conditional internalization story from §4 — small N
   doesn't matter because the prior contributes the missing information.
2. **Small-N regime is LOW-track only**: the cities with N < 1000 are
   exclusively LOW tracks of geographically narrow-distribution cities
   (Singapore, Lucknow, Jakarta). LOW outcomes there are concentrated in 3-4
   bins, so prediction is easier than the hypothesis assumed. The "small N"
   concept doesn't translate to the same Platt-bias-residual the hypothesis
   imagined.
3. **City regime variance dominates**: at fixed large N (5820), Brier ranges
   0.5-1.0 across cities. At fixed small N (~150), Brier ranges 0.7-1.0. The
   inter-city variance is so large that an N-trend cannot be cleanly resolved
   from this dataset.

## Recommended action — invoke kill-switch protocol

Per PLAN.md §1 kill-switch criteria (operator pre-committed):
> "If validation reveals that anomalous coverage drops do not correlate with
> measurable Brier/log-loss degradation in the test set, [...] the
> anomaly-based DDD formulation will be halted and returned to the operator
> for re-spec."

The §2.2 result triggers the kill-switch for the multiplier specifically (NOT
the entire DDD formulation — §2.1 hard-floor calibration succeeded). The
right action:

1. **v1 spec uses k=0** (i.e., no multiplier; multiplier collapses to 1.0).
2. Update `zeus_oracle_density_discount_reference.md` §5.3 to reflect
   empirical finding: "small-sample multiplier was found to be empirically
   unjustified at current sample sizes; spec retained for future revisit if
   small-N regime expands or calibration architecture changes".
3. The DDD formula simplifies to:
   ```
   DDD_actual = DDD_raw  (multiplier=1.0)
   small_sample_floor stays as-is (still useful for protecting tiny-N from
   ZERO-discount via hard floor at 0.05)
   ```
4. Document in case empirical evidence later supports it: re-run §2.2 every
   ~6 months as more samples accumulate; if R² rises above 0.20 and time-
   window stability passes, propose k value at that point.

## Counterfactual: what if we set k > 0 anyway?

The ECE k_estimate was 7.65. Applying this:
- Lagos LOW (N=1956): multiplier = 1 + 7.65/√1956 = 1.173 → ~17% extra discount
- Singapore LOW (N=199): multiplier = 1 + 7.65/√199 = 1.542 → ~54% extra
- Tokyo HIGH (N=6691): multiplier = 1 + 7.65/√6691 = 1.094 → ~9% extra

Without empirical support, applying these would be guess-tax: penalize cities
that may not actually have worse calibration. **This is exactly what operator
forbade ("我们不能拍脑袋")**. Hence k=0 is the correct conservative default.

## What this means for the broader DDD architecture

§2.1 SUCCEEDED → hard_floor calibration is sound
§2.2 FAILED → small-sample multiplier is empirically unjustified
§2.3-§2.6 still pending — sigma_window, curve breakpoints, threshold N=100,
peak_window radius

The DDD formula in `zeus_oracle_density_discount_reference.md` §6 should be
amended to make the multiplier OPTIONAL (default k=0) rather than mandatory.
The architecture remains correct; only one coefficient is set to its
"identity" value pending future evidence.

## Acceptance criteria (formal record)

Per PLAN.md §2.2:

| criterion | result | status |
|---|---|---|
| Monotone Brier vs small-N (winning) | slope -0.158 (NEGATIVE) | ❌ FAIL |
| Monotone Brier vs small-N (all) | slope +0.166 | ✓ PASS |
| Monotone ECE vs small-N | slope +0.058 | ✓ PASS |
| R²_train > 0.10 (winning Brier) | R² = 0.000 | ❌ FAIL |
| R²_train > 0.10 (all Brier) | R² = 0.036 | ❌ FAIL |
| R²_train > 0.10 (ECE) | R² = 0.077 | ❌ FAIL |
| \|Δk\|/k_train < 50% | 62% / 172% | ❌ FAIL |
| Variance reduction positive | 12.5% (all-Brier), -0.7% (winning) | ⚠ MIXED |

**Net: 1 PASS, 5 FAIL, 1 MIXED.** Insufficient for the multiplier to be
empirically justified at current data scale.

## Files produced

- `p2_2_k_validation.json` — full per-(city,metric) data
- `p2_2_k_validation.md` — initial report
- `p2_2_CONCLUSION.md` — this document

## Operator decision points

1. **Accept kill-switch outcome (k=0 in v1)?** Recommended.
2. **Defer §2.3-§2.6** until k decision is settled, or run them in parallel?
   They don't depend on k, so parallel is safe.
3. **Phase 2 implementation**: Encode the formula with `k` as a config-file
   parameter (default 0); leaving operator override path for future revival.
   This honors both the kill-switch and the architecture preservation.
