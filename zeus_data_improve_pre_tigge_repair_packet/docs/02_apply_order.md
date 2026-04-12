# Apply order

This is the exact order I recommend applying the packet.

## Phase 0 — Schema and additive surfaces

Run:

```bash
sqlite3 state/zeus-shared.db < /path/to/migrations/2026_04_11_pre_tigge_cutover.sql
```

Then:

```bash
python /path/to/scripts/repair_shared_db.py --db state/zeus-shared.db
```

What this does:

- creates missing additive tables if they do not exist
- adds `decision_group_id` / `bias_corrected` to `calibration_pairs` if missing
- backfills `calibration_decision_group`
- builds `forecast_error_profile`
- optionally materializes `day0_residual_fact`

## Phase 1 — Runtime writer cutover

Patch these files in this order:

1. `src/strategy/market_analysis.py`
2. `src/engine/evaluator.py`
3. `src/execution/harvester.py`
4. `src/state/portfolio.py`
5. `src/signal/day0_residual.py`

Use the fragments in `patches/`.

Goal:

- every decision writes a probability trace
- every evaluation cycle writes a selection family
- settlement learning writes grouped calibration truth
- partial stale no longer passes silently
- Day0 residual facts stop writing dummy `None` feature placeholders

## Phase 2 — Targeted backfills

Run:

```bash
python /path/to/scripts/backfill_calibration_decision_groups.py --db state/zeus-shared.db
python /path/to/scripts/build_forecast_error_profiles.py --db state/zeus-shared.db
python /path/to/scripts/materialize_day0_residual_features.py --db state/zeus-shared.db --start-date 2026-01-01
python /path/to/scripts/audit_pre_tigge_readiness.py --db state/zeus-shared.db
```

## Phase 3 — Shadow verification

Run paper/shadow only.

What to verify:

- `probability_trace_fact` rows appear for every evaluator decision
- `selection_hypothesis_fact` records all tested hypotheses
- selected rows after BH are a subset of `passed_prefilter=1`
- `calibration_decision_group` row count equals grouped calibration sample count
- `day0_residual_fact` feature completion improves sharply relative to placeholder version
- `partial_stale` no longer silently suppresses open positions

## Phase 4 — Only after TIGGE completes

Go to `docs/04_post_tigge_packet.md`.

That is when you:

- run TIGGE ETL
- widen calibration coverage
- rebuild grouped calibration truth
- run blocked OOS
- move promotion state out of "substrate only"

## Rollback rule

Everything in this packet is either:

- additive
- or a localized runtime cutover with a clear revert surface

If a runtime cutover behaves badly, roll back the runtime import/call site, keep the additive tables and backfills, and debug from the new truth surfaces instead of deleting the substrate.
