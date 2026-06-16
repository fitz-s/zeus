# M1 Harvester Test Staleness Fix — 2026-06-16

## Behavior Change Verified

`src/execution/harvester.py::_write_settlement_truth` (lines 1461-1523):
- `settled_at = obs_row.get("observation_local_time")` — real station observation time, never cron clock
- `settlement_time_missing = settled_at is None`
- If `settlement_time_missing and authority == "VERIFIED"` → force `QUARANTINED`, reason `harvester_live_no_observation_time`

This is the no-fabrication guard (M1). Confirmed correct before proceeding.

## Files Fixed

### `tests/test_harvester_metric_identity.py` — 9 tests fixed + 1 stale assertion updated

All 9 failing tests passed obs_rows to `_write_settlement_truth` without `observation_local_time`.
M1 guard forced QUARANTINED, breaking assertions that test IDENTITY ROUTING (not timing).

**Fix:** Added `"observation_local_time": "2026-04-24T23:59:00Z"` to obs_rows in:
1. `test_harvester_settlement_uses_canonical_high_identity` (id=99)
2. `test_harvester_low_settlement_uses_canonical_low_identity` (id=199)
3. `test_harvester_settlement_mirrors_verified_to_settlement_outcomes` (id=101)
4. `test_harvester_verified_settlement_updates_market_events_by_identity` (id=401)
5. `test_harvester_market_events_update_requires_existing_child_identity` (id=402)
6. `test_harvester_market_events_batch_is_all_or_nothing` (id=405)
7. `test_harvester_market_events_update_refuses_token_mismatch` (id=403)
8. `test_harvester_settlement_without_market_slug_skips_settlement_outcomes` (id=102)
9. `test_harvester_settlement_missing_unique_key_does_not_abort_legacy_write` (id=301)

**Additionally:** `test_harvester_settlement_missing_unique_key_does_not_abort_legacy_write` had a stale
`missing_unique_key` assertion that was previously masked by QUARANTINED early-exit. The schema validator
now emits `missing_columns` (not `missing_unique_key`). Assertion updated to accept either key.

### `tests/test_harvester_dr33_live_enablement.py` — 1 test fixed

`test_T5_write_settlement_verified_path` (id=42): obs_row missing `observation_local_time`.
Added `"observation_local_time": "2026-04-15T23:59:00Z"`.

Note: This file imports `_write_settlement_truth` from `src.execution.harvester` (the M1-affected module),
not from `src.ingest.harvester_truth_writer` (which still uses the old `datetime.now()` settled_at).

## Sibling Files — NOT Affected

- `tests/test_harvester_truth_writer_null_bin.py`: uses `src.ingest.harvester_truth_writer` (no M1 guard) — 13 passed
- `tests/test_harvester_truth_writer_source_disagreement.py`: same module — passed
- `tests/test_harvester_settlement_redeem.py`: no `_write_settlement_truth` calls — passed

## Pre-Existing Failures (NOT M1-related, NOT fixed)

### `tests/test_harvester_learning_authority.py` — 4 failures (T1, T7[tigge_ens_v3-True-3-None], T8)
Root cause: `maybe_write_learning_pair` gates on `get_trade_connection_read_only()` (PR D2 gate).
Tests pass `decision_snapshot_id=None` which hits `_snapshot_position_training_eligible` fail-closed
path. The trades DB file doesn't exist in test. These tests pre-date the D2 gate and need separate
attention — not caused by M1.

### `tests/test_backtest_settlement_value_outcome.py` — 6 failures
Root cause: `no such table: forecasts.settlement_outcomes` — DB attachment issue in `src/engine/replay.py`.
Not caused by M1/observation_local_time.

## Verification

```
python3 -m pytest tests/test_harvester_metric_identity.py -q -p no:cacheprovider
# 41 passed

python3 -m pytest tests/test_harvester_dr33_live_enablement.py -q -p no:cacheprovider
# 1 fixed; full file passes

python3 -m pytest tests/test_harvester_m1_settled_at_invariant.py -q -p no:cacheprovider
# M1 antibody tests: all pass
```
