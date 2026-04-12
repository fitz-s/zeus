# Patch 05 — Day0 residual feature completion

## Problem

`day0_residual_fact` already exists, but several columns are still placeholders.

## What to wire now

Use the helper functions in `src/signal/day0_residual_features.py` to populate:

- `daylight_progress`
- `obs_age_minutes`
- `post_peak_confidence`
- `ens_q50_remaining`
- `ens_q90_remaining`
- `ens_spread`

## Recommended runtime strategy

Do not try to fully compute these inside the hot decision path.

Use a scheduled/materialized writer:

```bash
python scripts/materialize_day0_residual_features.py --db state/zeus-shared.db --start-date 2026-01-01
```

Then let Day0 learning and audit code read from `day0_residual_fact` rather than recomputing the same joins inside every trading cycle.
