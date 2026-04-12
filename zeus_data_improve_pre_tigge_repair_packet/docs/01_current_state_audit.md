# Current-state audit

This audit combines:

- the current `data-improve` branch structure and commit intent,
- the uploaded system/data status note,
- and the uploaded `zeus-shared.db` snapshot.

## 1. What is already true

The branch has already done the hard foundation work:

- authority + docs are much cleaner,
- additive learning/truth substrates were introduced,
- the project is clearly trying to converge on DB-backed truth instead of old export surfaces,
- and the branch explicitly says those new tables are **behavior-neutral until a separate cutover packet**.

That last sentence matters. It means the next work is **not more substrate creation**. The next work is **cutover + materialization + proof**.

## 2. What is still not finished

### 2.1 Probability truth is still split

From the uploaded DB snapshot:

- `shadow_signals`: **136** rows, with complete `p_raw_json` and `p_cal_json`
- `opportunity_fact`: **43** rows, but **43/43** have `p_raw IS NULL`
- `trade_decisions`: **145** rows, and **145/145** have `p_calibrated IS NULL`

That is the exact shape of a system that has vectors in runtime/shadow surfaces but has **not finished canonical per-decision probability persistence**.

### 2.2 Selection-family truth is still substrate-only

The branch now contains `selection_family_fact` and `selection_hypothesis_fact`, but the runtime path still needs to be switched so BH is applied over **all tested hypotheses**, not only the already-prefiltered subset.

### 2.3 Calibration effective sample size is still overstated

The uploaded DB snapshot shows:

- `calibration_pairs`: **22,781** rows
- but only **2,141** distinct `(city, target_date, forecast_available_at)` decision groups
- average rows per group: **10.64**
- malformed groups where `n_rows != 11` or `positives != 1`: **77**

So the system still needs grouped-sample truth and grouped invariants before maturity / OOS / promotion can be trusted.

### 2.4 Day0 residual fact exists in the branch, but the feature surface is not filled

The branch added `day0_residual_fact`, but the current implementation still writes several key fields as `None` placeholders. That means the table exists, but the learning surface is not actually useful yet.

### 2.5 Portfolio loader still needs a stricter stale-truth policy

The branch moved toward `partial_stale`, which is better than all-or-nothing staleness, but it still needs an explicit policy layer. The current risk is: stale open positions can disappear from the loaded runtime portfolio if `partial_stale` is treated as safe authority without merge/fallback discipline.

## 3. What the uploaded DB snapshot says

### Core audit metrics

See `artifacts/current_db_audit.csv`.

Highlights:

- Missing new tables in this snapshot:
  - `probability_trace_fact`
  - `selection_family_fact`
  - `selection_hypothesis_fact`
  - `model_eval_run`
  - `model_eval_point`
  - `promotion_registry`
  - `forecast_error_profile`
  - `day0_residual_fact`
  - `calibration_decision_group`

This is not a contradiction with the branch. It simply means the uploaded DB snapshot predates local materialization of the new branch schema.

### Probability truth gap

See `artifacts/probability_truth_gap.csv`.

Important facts:

- `shadow_signals` is the richest current vector surface
- `opportunity_fact` is the weakest current truth surface for probability lineage
- `trade_decisions` still lacks calibrated-probability write-through

### Calibration grouping audit

See:

- `artifacts/calibration_group_audit.csv`
- `artifacts/calibration_group_anomalies.csv`

This is the immediate justification for grouped calibration truth.

### Day0 seed audit

See `artifacts/day0_feature_seed_audit.csv`.

Important facts from the uploaded DB snapshot:

- `observation_instants`: **1,107,567** rows
- missing `running_max`: **888,084**
- missing `delta_rate_per_h`: **897,241**
- `solar_daily`: **34,718** rows
- observation rows lacking solar match on `(city, target_date)`: **55,296**

This means Day0 materialization is very doable, but it must be written as a robust feature builder with missingness accounting, not as a naive backfill.

## 4. Why this packet is ordered the way it is

Because TIGGE is still downloading, the correct order is:

1. **repair the truth surfaces now**
2. **repair grouped-sample learning now**
3. **repair Day0 feature materialization now**
4. **repair stale-truth policy now**
5. **only then** expand calibration coverage when TIGGE completes

If you reverse this order, you only get a larger system with the same truth and evaluation seams.
