# M1 Invariant Test — Implementation Evidence

Date: 2026-06-16
Authority basis: timing-semantics fix M1 (settled_at = observation event time); src/execution/harvester.py:1453-1523

## What was written

`tests/test_harvester_m1_settled_at_invariant.py` — 3 unit tests calling
`_write_settlement_truth` directly from `src.execution.harvester`, using an
in-memory SQLite DB with the `settlements` DDL.

## Coverage

### M1-case1 (harvester_live_no_obs)
`obs_row=None` → returned dict `authority=="QUARANTINED"`, persisted `settled_at IS NULL`,
`reason=="harvester_live_no_obs"`. Covers the null-obs entry path (line ~1476).

### M1-case2 (VERIFIED, settled_at = obs time)
`obs_row` with `high_temp=77.0` (WMO half-up → 77, contained in [75,79]) AND
`observation_local_time="2026-06-16T15:00:00"` → returned dict `authority=="VERIFIED"`,
persisted `settled_at == "2026-06-16T15:00:00"` (asserts it equals the obs time and
is NOT the now()/recorded_at clock). Covers line 1462.

### M1-case3 (CRITICAL GUARD — line 1520)
Same bin-contained `high_temp=77.0` BUT `observation_local_time=None` → returned dict
`authority=="QUARANTINED"`, `reason=="harvester_live_no_observation_time"`, persisted
`settled_at IS NULL`. This test will FAIL if the guard at harvester.py:1520
(`if settlement_time_missing and authority == "VERIFIED": authority = "QUARANTINED"`)
is removed or regressed.

## Verification

```
python3 -m pytest tests/test_harvester_m1_settled_at_invariant.py -x -q
3 passed in 1.56s
```

## Test infrastructure notes

- In-memory conn with manual settlements DDL (subset of `init_schema`).
- `settlement_outcomes` table NOT created; `log_settlement` returns
  `{"status": "skipped_missing_table"}` harmlessly — the direct INSERT into
  `settlements` still fires and is verifiable.
- `dispatch_era_basis` is pure Python and runs without mocking (case2 date
  2026-06-16 is post-cutover, returns ERA_RESOLVED).
- No production DB connections opened.
