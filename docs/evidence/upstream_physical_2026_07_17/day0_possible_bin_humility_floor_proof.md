# Day0 possible-bin humility floor — settlement-graded proof

**Date:** 2026-07-18
**Scope:** read-only SQL against `state/zeus-forecasts.db`; no source edited.
**Question:** on the Day0 (same-day) route, do POSSIBLE bins (q>0, at/above the observed
floor) need a member-count humility floor, given the finite-evidence UCB-floor gate at
`src/data/replacement_forecast_materializer.py:3700` is mutually exclusive with Day0
conditioning (`if _current_shape is not None and _day0_obs_extreme_c is None:`)?

## Verdict

**FLOOR NEEDED — but not in the "low member count" form the question proposed.**
Raw ensemble member count has ~zero variance in this dataset (it's a fixed-size product,
99.7%+ of populated values are exactly 51); the count-gradient hypothesis is **not
testable** and is **refuted as stated**. But the exact evidence-richness flag the floor
gate keys on (`current_evidence_shape` present vs absent, i.e. decision-time
within-day-ensemble sigma vs fallback residual-std sigma) **is** testable, and splits Day0
possible bins into a badly overconfident group and a well-calibrated group:

- **`with_current_evidence` bucket** (current-ensemble sigma basis
  `decision_time_current_ensemble_within_plus_provider_between`, n=51 members, this is
  the SAME bucket the finite-evidence floor would fire on if Day0 weren't excluding it):
  lowest quintile of possible-bin q (n=279, deduped-per-market) served **mean q≈0.0000**
  with `q_ucb≈0.0009`, but **realized frequency = 0.72%** — realized rate exceeds the
  model's own upper bound by ~8x. Pooled across all cycles (n=5,085 bin-instances,
  lowest decile) the gap is worse: `q_ucb≈2.7e-5` vs **realized frequency 0.73%**, a
  ~270x violation of the model's own upper confidence bound. Coverage fails
  (`covered=False`) in the bottom 1-2 deciles.
- **`no_current_evidence` bucket** (fallback sigma basis `fused_center_residual_std`,
  no decision-time ensemble narrowing at all): lowest quintile/decile is **well
  calibrated** — mean_q and freq track closely (e.g. mean_q=0.0013 vs freq=0.0059
  deduped; mean_q≈0.00001 vs freq≈0.0000 pooled), and `[q_lcb,q_ucb]` covers the
  realized frequency in every bucket checked.

So the overconfidence is real, large, and settlement-graded — but it lives on the
**current-ensemble sigma basis**, not on a member-count gradient (which doesn't exist in
this data). Proposed floor form below.

## Method

1. Identify Day0-served posteriors via `provenance_json.$.day0_conditioning` (confirmed
   against `_posterior_day0_observed_extreme_c is not None` at
   `replacement_forecast_materializer.py:4402-4415`, which stamps this exact key).
   15,623 of 43,389 posteriors are Day0-conditioned. 13,810 join to a `VERIFIED`
   settlement outcome; deduped to one posterior per (city, target_date, metric) market
   (latest `computed_at`), that's **891 settled Day0 markets**.
2. Per posterior, parse `q_json`; POSSIBLE bins are `q > 1e-9` (below-floor bins are
   hard-zeroed by `np.zeros_like` init in `_replacement_fused_q_shape_bounds`, confirmed:
   only 16 of 57,165 below-floor bin-instances realized anyway — 0.028%, consistent with
   observation revision / station mismatch noise, not a systematic floor breach).
3. Match `winning_bin` (e.g. `"80-81°F"`, `"40°C or below"`) to the `q_json` key by
   substring containment (0 unmatched across 13,810 rows) to get the realized 0/1 label
   per possible bin.
4. **Member-count bucketing attempt (as literally asked):** looked up
   `provenance_json.$.bayes_precision_fusion.current_evidence_shape.member_count`, the
   SAME field the finite-evidence floor reads
   (`_finite_evidence_member_count = int(_current_shape["member_count"])`,
   materializer.py:3707). Distribution across all 15,623 Day0 rows: `NULL`=6,646 (no
   current-evidence shape at all), `51`=8,948, `40`=16, `49`=13. **No usable low/mid/high
   gradient exists** — it's a near-binary present/absent flag, not a count.
5. Reframed to the natural binary split the data actually supports: `with_current_evidence`
   (member_count populated, essentially always 51) vs `no_current_evidence` (field null,
   `replacement_sigma_basis = fused_center_residual_std` fallback, confirmed 1:1 with the
   member_count-null rows). Computed quantile-binned reliability (mean served q vs
   realized frequency) and coverage (`mean_qlcb ≤ freq ≤ mean_qucb`) per bucket, both on
   the deduped 891-market set and the full 13,810-row pooled set (same-market cycles are
   correlated, so pooled numbers are a power check, not the primary estimate — deduped is
   primary).

## SQL used

```sql
-- Day0 marker + settlement join
select fp.posterior_id, fp.city, fp.target_date, fp.temperature_metric, fp.computed_at,
       fp.q_json, fp.q_lcb_json, fp.q_ucb_json,
       so.winning_bin, so.settlement_value,
       json_extract(fp.provenance_json,
         '$.bayes_precision_fusion.current_evidence_shape.member_count') as member_count
from forecast_posteriors fp
join settlement_outcomes so
  on so.city=fp.city and so.target_date=fp.target_date
 and so.temperature_metric=fp.temperature_metric
where so.authority='VERIFIED'
  and json_extract(fp.provenance_json,'$.day0_conditioning') is not null;
```

Bin matching, decile/quintile reliability, and coverage were computed in Python
(json parse + substring match; script not persisted, logic is the loop above run
against the query result set).

## Load-bearing numbers (deduped, one posterior per settled Day0 market, n=891)

| bucket | quintile | n bins | mean served q | realized freq | q_lcb | q_ucb | covered |
|---|---|---|---|---|---|---|---|
| no_current_evidence | 1 (lowest) | 678 | 0.0013 | 0.0059 | 0.0002 | 0.0462 | yes |
| no_current_evidence | 2 | 679 | 0.0251 | 0.0118 | 0.0004 | 0.0751 | yes |
| with_current_evidence | 1 (lowest) | 279 | 0.0000 | **0.0072** | 0.0000 | **0.0009** | **NO** |
| with_current_evidence | 2 | 279 | 0.0043 | 0.0251 | 0.0003 | 0.0419 | yes |

Pooled across all Day0-served cycles (n=94,745 possible-bin instances, larger power
check, same direction and much starker):

| bucket | decile | n bins | mean served q | realized freq | q_ucb | covered |
|---|---|---|---|---|---|---|
| no_current_evidence | 1 (lowest) | 4,389 | 0.00001 | 0.00000 | 0.068 | yes |
| with_current_evidence | 1 (lowest) | 5,085 | 0.00000 | **0.0073** | **0.000027** | **NO** |
| with_current_evidence | 2 | 5,085 | 0.00000 | **0.0189** | 0.0011 | **NO** |
| with_current_evidence | 3 | 5,085 | 0.0007 | 0.0295 | 0.0102 | **NO** |

ECE (10-decile, pooled): `no_current_evidence` = 0.0163; `with_current_evidence` = 0.0433
— 2.7x worse, concentrated entirely in the bottom deciles (upper deciles are fine or
mildly *under*-confident).

No city-mix or bin-count confound found: `no_current_evidence` averages 5.6 possible
bins/market across a similar city spread (Miami, Shanghai, NYC, Paris, London, Tokyo,
Seoul...) vs `with_current_evidence` at 4.8 possible bins/market (Seoul, Tokyo, Shanghai,
London, Paris...) — comparable granularity, overlapping cities, not a like-for-unlike
comparison.

## Interpretation

`current_evidence_shape`'s sigma basis folds raw same-day ensemble spread ("within") plus
provider disagreement ("between") additively in variance — but the raw ensemble spread of
a single NWP ensemble is well known to be **underdispersed** (textbook ensemble
under-spread; the entire reason EMOS/NGR calibration exists elsewhere in this codebase).
Feeding raw spread straight into the Day0 conditional-normal tail, with no calibration
inflation and no finite-evidence floor (excluded by the Day0 branch), produces exactly
the observed failure mode: a tail collapsed to ~0 that still realizes ~1% of the time.
The fallback path (`fused_center_residual_std`) is apparently already calibrated against
historical residuals and shows no such defect.

This means the fix target is **not** "add a floor keyed on member count" (there's no
member-count gradient to key on) — it's "the Day0 branch must not bypass humility for
rows using the current-ensemble sigma basis." A floor keyed on
`replacement_sigma_basis == "decision_time_current_ensemble_within_plus_provider_between"`
(equivalently: `current_evidence_shape is not None`) reproduces exactly the condition
`_day0_obs_extreme_c is None` currently excludes — i.e., re-admit the SAME
`_finite_evidence_zero_hit_ucb_floor(member_count, metric)` floor (member_count=51 is
already computed and sitting unused on these rows) for Day0-possible bins when
`_current_shape is not None`, regardless of whether `_day0_obs_extreme_c` fired. member
count on these rows is uniformly 51 so the floor value itself is a constant per metric —
cheap to apply, and directly targets the bucket proven overconfident above.

## Caveats

- Sample sizes at the deduped/primary granularity are moderate (279 bin-instances in the
  worst decile) — real but not huge; the pooled cross-cycle numbers (5,085/decile) confirm
  direction and magnitude with much tighter noise, at the cost of within-market
  correlation across cycles.
- The originally-asked "member count" variable cannot be graded on a gradient (40/47/49
  member rows total 29 across the whole table) — this proof answers the *evidence-richness*
  version of the question, not a literal count-based dose-response, because that gradient
  does not exist in production data.
- 16 below-floor bins realized anyway (0.028% of below-floor instances) — a separate,
  much smaller anomaly (likely settlement-source revision after the Day0 obs was locked),
  noted but not the subject of this proof.
