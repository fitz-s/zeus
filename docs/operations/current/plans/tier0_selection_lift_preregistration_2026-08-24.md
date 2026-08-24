# PREREGISTRATION — Tier-0 ordinal selection-lift test (frozen 2026-08-24)

Status: FROZEN before any prospective Tier-0 settlement is observed. Amendments
after the first Tier-0 entry require a new version of this file with the old
kept intact; results computed under an amended protocol must disclose the
amendment. Authority: reversal_plan_tier0_2026-08-24.md Item 7 (consult section
A.5, adopted).

## Question

Does the engine's ordinal selector add settlement value over generic cheapness —
i.e., does the selected claim outperform price-matched, contemporaneously
available, NOT-selected claims from the same opportunity set?

## Design (within-city-date opportunity-set contrast)

For every actual Tier-0 auction decision (live auctions only — NO hypothetical
or shadow order streams):

- Treatment: the selected candidate's settlement residual y − p0, where p0 is
  the immutable decision-time executable side price from the Item-3 certificate
  and y ∈ {0,1} is side settlement.
- Control: the weighted mean settlement residual of candidates in the SAME
  city-date opportunity set that were eligible under the same policy at the
  same instant, price-matched to the selected candidate within ±0.05 on side
  price, same lead bucket, logical duplicates and yes/no complements collapsed
  to one economic claim each.
- One aggregate lift observation per city-date:
  L = (y_sel − p0_sel) − weighted_mean(y_ctrl − p0_ctrl).
- City-dates with an empty matched control set contribute NOTHING (counted in
  coverage; never padded).

## Frozen analysis choices

1. Inference: permutation test — within each opportunity set, permute the
   "selected" label across the eligible price-matched candidates; 10,000
   permutations; two-sided p on mean(L).
2. Dependence: primary inference clusters by city-date (each set is one
   observation). Sensitivity: date-block resampling (all city-dates sharing a
   calendar date form one block). The LARGER uncertainty governs.
3. Stopping rule: evaluate ONCE when 100 qualifying city-date observations
   have accrued; earlier peeks are forbidden; if a second evaluation is ever
   run, alpha is split 0.03/0.02 (first/second), no third.
4. Policy epoch: only decisions made under the Tier-0 policy (flag on, this
   plan's admission rules) count. Pre-Tier-0 history is NOT pooled (admission
   rules differ; the pooled result would be a regime artifact).
5. Decision rule (consult Item 7, verbatim adoption):
   - mean(L) positive lower one-sided 95% confidence bound → ordinal selection
     is ELIGIBLE for the Gate-B capital-use evaluation (not sufficient alone).
   - point estimate positive, interval crosses zero → remain Tier 0, keep
     accruing.
   - point estimate ≤ 0 at the stopping count → the q-based ordinal selector
     is retired from Tier-0 admission; replace with the simplest market-only
     comparator (cheapest eligible claim per cluster) and re-preregister.
6. Historical selection-lift is UNKNOWN and stays unknown: pre-Tier-0 auction
   receipts persist only the winner + a candidate-set hash (verified
   2026-08-24, decision_log id 499036); reconstruction is forbidden by the
   walk-forward law.

## Data requirements (satisfied by plan Items 3 + 6)

Each Tier-0 decision certificate must persist: the full considered candidate
set (id, side, executable side price p0, lead bucket, eligibility verdict),
the selected candidate id, snapshot/generation id, and decision timestamp —
all written BEFORE order submission. Absence of any field excludes the
city-date from the test and increments a named coverage counter.

## What this test cannot show (declared limits)

- It measures selection lift at matched prices, not execution quality (P3
  panel owns that) and not calibration (Gate A owns that).
- With ~1.5 cheap city-date clusters/day accruing, 100 observations ≈ 8-10
  weeks; the stopping count is an accrual unit, not a power claim. Cluster
  variance from the first 30 observations will be published as a power check
  (report-only; never a stopping trigger).
