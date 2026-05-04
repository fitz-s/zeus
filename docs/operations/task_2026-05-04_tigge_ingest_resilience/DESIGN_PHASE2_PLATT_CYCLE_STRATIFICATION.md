# Phase 2 Design: Platt v2 Cycle Stratification

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Author:** Claude Opus 4.7
**Authority basis:** Operator directive 2026-05-04 — option C (cycle as stratifier, not lead-bin) chosen for math-architectural reasons (cycles are discrete, leads are continuous and would explode bucket count).

---

## Problem

Current `platt_models_v2` schema buckets by `(temperature_metric, city, season, data_version, input_space)`. **No `cycle` dimension.** Pairs from 00z and 12z forecasts collapse into the same bucket, forcing a single (A, B, C) Platt parameter set across both cycles.

This is mathematically incorrect because:
- 00z and 12z perturbation seeds are independent (per ECMWF docs)
- For matched target_date, 12z has shorter lead → different forecast variance → different reliability curve
- A single Platt fit averages over the lead-time distribution; the distribution shifts by cycle

When live evaluator applies the bucket-averaged Platt to a 12z forecast:
- Variance underestimated (12z is shorter-lead, smaller spread, but Platt assumes mixed-lead spread)
- Bias may shift (00z systematically longer-lead than mean, 12z shorter)
- Calibration error magnitude: empirically TBD; depends on data, but documented in numerical-weather-prediction literature (cite forthcoming).

## Decision

Add `cycle TEXT NOT NULL DEFAULT '00'` to `platt_models_v2`. Bucket key becomes `(metric, city, cycle, season, data_version, input_space)`. Existing rows backfilled with `'00'` (legacy is 00z-only).

Equivalent change to `calibration_pairs_v2`: add `cycle TEXT NOT NULL` derived from `forecast_available_at` (or `issue_time` of the snapshot), backfilled to `'00'` for existing rows.

The fitter (`scripts/refit_platt_v2.py`) groups by the new bucket key. Each (metric, city, cycle, season, data_version, input_space) tuple becomes its own Platt fit.

## Why option C (cycle stratifier) rather than D (lead-bin stratifier)

| Option | Stratifier dim | New buckets per old bucket | Sample density |
|---|---|---|---|
| C (chosen) | cycle ∈ {00z, 12z} | 2× | halved |
| D | lead_bin (e.g., 0-1d, 1-2d, ..., 7d+) | ~7× | divided by 7 |
| C+D | cycle × lead_bin | ~14× | divided by 14 |

- D would push most buckets to immature (level=4) again, requiring many more months of data
- C is a coarser-grained but mathematically meaningful split: the cycle is a discrete operational choice, the lead is a continuous regression
- The Platt logistic regression already accounts for monotone reliability curves; lead variation within a cycle is partially absorbed by the (A, B, C) parameters
- C is the minimum sufficient stratifier given current data density

Option D becomes viable AFTER 17-month full backfill (much later). It's deferred but documented for future improvement.

## Schema changes

### `platt_models_v2` ALTER

```sql
-- Phase 2 migration (forward-only, no downgrade path expected):
ALTER TABLE platt_models_v2 ADD COLUMN cycle TEXT NOT NULL DEFAULT '00';
-- Existing rows automatically get '00' via DEFAULT
-- Bucket key unique constraint must include cycle:
CREATE UNIQUE INDEX IF NOT EXISTS ix_platt_models_v2_bucket_cycle
ON platt_models_v2 (temperature_metric, city, cycle, season, data_version, input_space, is_active)
WHERE is_active = 1;
-- Old bucket_key column is recomputed by the fitter to include cycle prefix
```

### `calibration_pairs_v2` ALTER

```sql
ALTER TABLE calibration_pairs_v2 ADD COLUMN cycle TEXT NOT NULL DEFAULT '00';
-- Backfill: derive cycle from snapshot_id → ensemble_snapshots_v2.issue_time
UPDATE calibration_pairs_v2 SET cycle = (
  SELECT substr(es.issue_time, 12, 2)
  FROM ensemble_snapshots_v2 es
  WHERE es.snapshot_id = calibration_pairs_v2.snapshot_id
)
WHERE EXISTS (
  SELECT 1 FROM ensemble_snapshots_v2 es WHERE es.snapshot_id = calibration_pairs_v2.snapshot_id
)
AND cycle = '00';  -- only update rows still at default
```

For pre-existing pairs without snapshot_id linkage (legacy `bin_source='legacy'`), accept `'00'` since legacy data is all 00z anyway.

## Fitter changes

`scripts/refit_platt_v2.py`:
1. Group by `(metric, city, cycle, season, data_version, input_space)` instead of `(metric, city, season, data_version, input_space)`
2. The bucket-key string includes cycle: `"<metric>:<city>:<cycle>:<season>:<data_version>:<input_space>"`
3. The maturity threshold (`required_threshold=3x` per error message → likely 15 samples × 3 = 45 samples for level<3) applies per-cycle now. With 90 days of 12z added, expect:
   - 12z buckets at level=3 or 4 (15-45 samples)
   - 00z buckets retain their existing maturity (often level 1 or 2 with 17-month data)
4. Output: separate `platt_models_v2` rows for `cycle='00'` and `cycle='12'` per (metric, city, season, data_version, input_space)

## Live evaluator changes

`src/engine/evaluator.py` and any code that calls `get_calibrator(...)`:

1. Determine forecast cycle at evaluation time: `cycle = '00' if issue_time.hour == 0 else '12'` (only 00 or 12 expected for entry_primary; reject 06/18 which aren't TIGGE-aligned)
2. Pass `cycle` to `get_calibrator(metric, city, cycle, season, data_version, input_space)`
3. If no Platt model exists for the cycle (e.g., 12z bucket still immature):
   - Reject with `CALIBRATION_IMMATURE_CYCLE_BUCKET` (new code)
   - Do NOT silently fall back to the other cycle's Platt — that would re-introduce miscalibration

## Test plan

### Schema tests
- `test_platt_v2_schema_has_cycle_column`
- `test_calibration_pairs_v2_schema_has_cycle_column`
- `test_legacy_rows_default_to_cycle_00z`

### Fitter tests
- `test_refit_groups_by_cycle_with_dual_cycle_pairs`
- `test_refit_produces_distinct_models_per_cycle`
- `test_refit_immature_threshold_applies_per_cycle`

### Evaluator integration tests
- `test_evaluator_routes_00z_forecast_to_00z_bucket`
- `test_evaluator_routes_12z_forecast_to_12z_bucket`
- `test_evaluator_rejects_when_cycle_bucket_immature`
- `test_evaluator_does_not_fall_back_across_cycles`

### Mathematical validation
- After Platt fit, verify (A, B, C) parameters differ between 00z and 12z buckets for the same (city, metric, season). If they're identical, either:
  - Stratification didn't take effect (bug), or
  - The pairs are statistically identical (unlikely — different perturbation seeds)
- Compute Brier score in-sample for each cycle bucket and compare to combined-cycle Brier from old fit. Cycle-stratified should be ≤ combined-cycle Brier (or equal within noise) — strict inequality is expected for reasonable data.

## Rollout sequence

After Phase 1 (12z code + 90-day backfill) lands:

1. **Migration 1**: ALTER calibration_pairs_v2 + ALTER platt_models_v2 with cycle column (default '00')
2. **Migration 2**: Backfill cycle column on calibration_pairs_v2 from snapshot_id linkage
3. **Code change**: Update refit_platt_v2.py grouping and bucket_key
4. **Code change**: Update get_calibrator + evaluator to thread cycle param
5. **Refit**: Run `python scripts/refit_platt_v2.py --no-dry-run --force` against the 90-day-augmented dataset
6. **Verification**: SQL counts, parameter divergence test, Brier comparison
7. **Smoke test**: Eval one candidate forecast for each cycle and verify it routes to the correct Platt bucket

Only after step 7 passes can the live trading lock be lifted (per `LIVE_TRADING_LOCKED_2026-05-04.md` Step 2).

## Open questions

1. **What does `cycle='06'` or `cycle='18'` mean if such a row ever lands?**
   - These cycles are not in TIGGE archive and should not feed entry_primary
   - Recommend: `gate_source_role` rejects forecasts with cycle ∉ {'00','12'} for entry_primary
   - Document in registry

2. **Can existing trained 00z models be reused directly, or do they need refit?**
   - Existing `platt_models_v2` rows trained on 17 months of (00z + bias) need refit because the previous fit averaged over an asymmetric lead distribution; the new 00z-only fit will produce slightly different (A, B, C)
   - **Recommendation:** refit ALL buckets after migration, even the 00z ones; document this explicitly so we know not to compare against legacy parameters

3. **What if a city has 12z observations but no 00z (or vice versa)?**
   - Possible due to data availability gaps
   - The bucket simply remains immature; live evaluator rejects forecasts targeting that cycle for that city
   - This is correct conservative behavior

## Out of scope (future)

- Lead-bin sub-stratification (option D) — needs full 17-month backfill first
- Cross-cycle pooled fits with cycle-as-feature (instead of stratifier) — requires regularization design and is more complex
- Continuous-time models (e.g., GAM with smooth lead-time response) — significant pipeline change
