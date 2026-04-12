# Post-TIGGE packet

This packet starts **after** TIGGE backfill finishes.

It assumes the pre-TIGGE repair packet has already been applied.

## Goal

Convert the new larger forecast history into:

- grouped calibration truth
- blocked OOS evidence
- model promotion records
- broader calibrated city coverage

## Order

### Step 1 — Ingest TIGGE
Run your existing TIGGE ETL / ingestion path first.

Expected result:

- `ensemble_snapshots` grows materially
- `calibration_pairs` expands beyond the current 9-city set
- non-MAM coverage becomes real

### Step 2 — Rebuild grouped calibration truth

```bash
python scripts/backfill_calibration_decision_groups.py --db state/zeus-shared.db
```

Verify:

- grouped city count expands
- malformed decision groups are still isolated and visible
- new groups carry `bias_corrected` and group IDs

### Step 3 — Rebuild forecast error profiles

```bash
python scripts/build_forecast_error_profiles.py --db state/zeus-shared.db
```

This gives you city × season × source × lead summaries that can feed uncertainty correction.

### Step 4 — Run blocked OOS evaluation

Use the current mainline evaluator in `src/calibration/blocked_oos.py`.
Do not restore the older packet-local `blocked_oos_eval.py` helper.

Target outputs:

- `model_eval_run`
- `model_eval_point`
- comparable OOS metrics for:
  - current Platt
  - grouped/shrunk Platt candidate
  - Day0 residual candidate
  - any bias/uncertainty correction candidate

### Step 5 — Promotion state
Write promotion decisions to `promotion_registry`.

Suggested states:

- `shadow`
- `candidate`
- `active`
- `rejected`
- `retired`

No live behavior changes without a promotion row.

### Step 6 — Expand calibration routing
Only after grouped truth + OOS evidence exist should you widen active Platt routing to the new TIGGE-unlocked cities.

## What should still wait

Even after TIGGE completes, I would still stage these later:

- hierarchical / James-Stein style shrinkage in production selection
- execution/microstructure learning promotion
- more aggressive α meta-learning
- ergodicity/path-dependent bankroll formalism

Those are useful, but they should sit on top of the repaired truth/eval substrate, not replace it.
