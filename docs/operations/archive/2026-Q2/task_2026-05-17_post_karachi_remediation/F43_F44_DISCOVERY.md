# F43 + F44 — Post-#137-merge discoveries (2026-05-17)

Discovered during WAVE-A.F40 dry-run verification. Two findings collapsed into one root-cause investigation.

## F43 — K1-helper world-table qualification regression (FIX APPLIED, awaiting data)

**Symptom**: PR #137 changed `scripts/bridge_oracle_to_calibration.py` + `scripts/evaluate_calibration_transfer_oos.py` to use `get_forecasts_connection_with_world()` which opens forecasts.db as MAIN with world.db ATTACHed. Three SQL queries reference world-class tables without `world.` schema prefix → bare refs resolve to empty MAIN shells.

**Probe** (verbatim):
```
EVAL probe (under K1-helper):
  bare FROM calibration_pairs_v2: 91040450      ← forecast_class, MAIN=forecasts, correct
  world.calibration_pairs_v2:     0              ← world ghost shell, expected 0

BRIDGE probe (get_world_connection):
  bare FROM observation_instants_v2: 1835645    ← world_class, lives in world.db
```

**Registry truth** (`architecture/db_table_ownership.yaml`): two entries per K1-split forecast-class table — canonical `db: forecasts` + `db: world schema_class: legacy_archived` (the ghost). My earlier python script picked the wrong one in coarse scan, generating false 55-site noise. Actual broken count: **3 lines**.

**Fix applied** (this branch, commit pending):
- `scripts/bridge_oracle_to_calibration.py:183` `FROM observation_instants_v2` → `FROM world.observation_instants_v2`
- `scripts/bridge_oracle_to_calibration.py:195` same
- `scripts/evaluate_calibration_transfer_oos.py:222` `FROM platt_models_v2` → `FROM world.platt_models_v2`

**Bridge `FROM settlements` at L90 is CORRECT**: settlements moved to forecasts.db post-K1, MAIN=forecasts, bare ref resolves correctly. Eval `FROM calibration_pairs_v2` at L284, L316 similarly CORRECT.

**Antibody upgrade required**: existing `tests/test_k1_reader_isolation.py` asserts "helper used", not "queries return non-zero rows". Extend with smoke-read: invoke representative read for each K1-helper-using script; assert row_count > 0 for tables registered as world-class.

## F44 — observation_instants_v2 writer dead since 2026-05-10 (NEW finding, blocks F40/F35)

**Smoking gun**: `SELECT MAX(target_date) FROM zeus-world.db.observation_instants_v2` = **2026-05-10**. Today is 2026-05-17. Writer stopped exactly at K1-split day (2026-05-11).

**Live impact**:
- Bridge filters `WHERE city=? AND target_date=? AND source=?` → 0 matches for any target_date ≥ 2026-05-11
- Oracle penalty calibration permanently stale since 2026-05-10
- All cities fall back to 0.5× Kelly conservative (safe, but degraded)
- Persisted across Karachi window — Karachi position priced with stale-since-7-days oracle data

**Auth distribution** (sanity-check, not the cause):
- VERIFIED: 1,835,641 rows (99.99%)
- ICAO_STATION_NATIVE: 4 rows

**Writer file**: `src/data/observation_instants_v2_writer.py` (single match in repo).

**Hypothesis (unverified)**: K1 split (2026-05-11) re-routed observation_instants_v2 writes to forecasts.db instead of world.db, OR broke the writer invocation chain in the daemon. Need to investigate:
1. What was changed in K1 PR #114 regarding `observation_instants_v2_writer.py` callers
2. Is the writer being invoked? `grep "observation_instants_v2_writer" src/` for invocation sites
3. If invoked, where does it actually write (forecasts vs world)?
4. Cron / launchd schedule

**Karachi safety**: oracle fallback is conservative; no immediate adversarial risk. But this is the upstream cause of F33 (persistent oracle MISSING) findings.

**Severity**: SEV-1 (live-impacting structural defect post-K1 that survived 7 days undetected).

## Structural finding (K-decision)

K1 split was incomplete. The split moved 7 forecast-class tables but did NOT verify:
(a) Every WRITER of every table is repointed correctly
(b) Every READER of every table uses the new connection helper correctly with proper schema qualification

The audit caught (b) partially (K1_READER_SWEEP.md); (a) was never systematically audited. F44 is the symptom.

**Recommended antibody**: a CI test that, for every table in `db_table_ownership.yaml`, runs `SELECT MAX(updated_at) FROM <table>` and asserts the timestamp is fresh within an expected window (per-table SLA from registry). Catches dead-writer category permanently.

## Action plan

1. **F43 (this branch)**: 3-line fix applied; antibody upgrade pending.
2. **F44 (this branch + dispatch)**: deep root-cause investigation dispatched to sonnet executor. Likely fix: repoint writer to correct DB OR re-enable invocation.
3. **F35 (was blocked on F40 verify)**: REMAINS BLOCKED until F44 resolves and bridge produces non-empty cities.
4. **Bundle F43+F44 into WAVE-2 PR** along with WAVE-B (F7-followup Position dataclass) and WAVE-C (F21 + others).
