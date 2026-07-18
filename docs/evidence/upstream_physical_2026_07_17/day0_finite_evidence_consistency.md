# Day0 vs. CP finite-evidence UCB floor — consistency check

Date: 2026-07-18
Scope: `src/data/replacement_forecast_materializer.py` fused-q core.
Verdict: **the reported live leak does NOT exist in the current code.** Below-observed-extreme
("impossible") Day0 bins already serve `q_ucb == 0`. No production code change was made.

## The claim under test

Reported defect: on the Day0 path, the CP finite-evidence UCB floor
(`_current_evidence_tail_ucb_floors`, :2865) is not Day0-aware, so a bin the Day0 CDF
conditioning marks impossible (HIGH: preimage entirely `<= observed max`) would still receive a
positive served `q_ucb` via `q_ucb = max(mixed_ucb, q_point, finite_evidence_floor)` at :3959-3963,
asserting positive probability for an outcome observation has already excluded.

## Finding

The premise about the *function* is true; the premise about *live reachability* is false. The
finite-evidence machinery is structurally disabled on the Day0 path by commit `d8053c27e9`
(`fix(probability): bound current tail certainty`, 2026-07-11), so the floor never reaches a Day0
served bound.

### Two independent gates prevent the leak

1. Payload assembly (:3700). `_finite_evidence_member_count` — the sole trigger for computing
   `_finite_evidence_ucb_floor_by_bin` (:3780) — is assigned in exactly ONE place (:3707), inside
   `if _current_shape is not None and _day0_obs_extreme_c is None:`. On Day0 (`_day0_obs_extreme_c`
   not None) it is never set, so the floor dict stays `None`, and the max at :3962
   reads `(_finite_evidence_ucb_floor_by_bin or {}).get(_bid, 0.0) == 0.0`.
2. Bootstrap core (:3458). Even if members were passed to `_build_fused_q_bounds` directly, the
   internal finite-evidence stress is gated `if evidence_members_c is not None and day0_obs is None:`.

The existing test `tests/test_finite_evidence_probability_symmetry.py::test_day0_absorbing_fact_dominates_forecast_ambiguity`
(added in the same commit) already asserts the below-obs bin is not floored on Day0.

## Evidence

### A. The function is Day0-unaware (as reported)

Called directly with a HIGH scenario, observed max 30.0 C, members near the obs
(`mu*=30.5, sigma=1.2`, `wmo_half_up`, `half_step=0.5`):

| bin           | preimage vs obs | floor    |
|---------------|-----------------|----------|
| `< 28`        | impossible      | 0.450720 |
| `[28, 29)`    | impossible      | 0.590164 |
| `[30, 31)`    | possible        | 1.000000 |
| `>= 31`       | possible        | 0.657408 |

So IF invoked on Day0, the two below-obs bins would carry ~0.45 / ~0.59 leaked UCB mass. They are
never invoked on Day0.

### B. Live served data — leak rate = 0

Read-only over `state/zeus-forecasts.db :: forecast_posteriors` (43,348 rows w/ provenance):

| population                                             | rows   |
|--------------------------------------------------------|--------|
| Day0 rows (`q_shape = fused_day0_conditioned_normal`)  | 15,583 |
| … with non-null `finite_evidence_tail_ucb_floor_by_bin`| **0**  |
| … with any positive finite-evidence floor              | **0**  |
| non-Day0 rows carrying a finite-evidence floor          | 6,833  |

The finite-evidence floor is active and populated on non-Day0 rows (6,833) and completely absent on
every Day0 row (0 / 15,583). **Live leak rate on impossible Day0 bins = 0 / 15,583 = 0.000.**

Spot check of 2,000 Day0 rows: 1,476 rows carry ≥1 bin with `q_ucb == 0.0` exactly (7,070
zero-`q_ucb` bins of 22,000) — the impossible bins are served at exactly zero, as required.

## Method

- Function demo: direct call to `_current_evidence_tail_ucb_floors` with a synthetic bin family.
- Control-flow proof: single guarded assignment of `_finite_evidence_member_count` (grep-verified),
  downstream gate at :3780, caller max at :3962.
- Live measurement: read-only `file:...?mode=ro` scan of `forecast_posteriors.provenance_json` /
  `q_ucb_json`, classifying rows by `q_shape` and inspecting `finite_evidence_tail_ucb_floor_by_bin`.
- Walk-forward / strictly served rows only; no fitters run; no market backtest.

## Recommendation

No leak fix is warranted — the served bound is already correct. Two forward options, both requiring
a separate decision (out of scope for a "fix the leak" change):

- Leave as-is. Day0 relies on the observed absorbing fact; the finite-evidence tail certainty band
  is intentionally off (commit d8053c27e9).
- If the CP finite-evidence tail floor is wanted ACTIVE on Day0 possible bins (a modeling change,
  not a leak fix): remove the `_day0_obs_extreme_c is None` gate at :3700 (and :3458) AND make
  `_current_evidence_tail_ucb_floors` zero the Day0-impossible bins via a predicate shared with the
  :3406-3421 conditioning block. That changes served `q_ucb` on possible Day0 bins and must be
  validated on its own (settlement-graded), not merged as a no-op.
