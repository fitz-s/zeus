# Settlements physical_quantity Migration Plan
# task_2026-04-28_settlements_physical_quantity_migration

Created: 2026-04-28
Status: NEEDS_OPERATOR_APPROVAL — do NOT execute --apply without explicit operator sign-off

---

## Authority Basis

- **INV-14 identity spine**: `src/types/metric_identity.py` — defines `HIGH_LOCALDAY_MAX.physical_quantity = "mx2t6_local_calendar_day_max"` as the canonical string.
- **Antibody citation (test_harvester_metric_identity.py:7-9)**:
  ```
  # Purpose: INV-14 identity spine antibody for harvester settlement writes —
  #          pins temperature_metric / physical_quantity / observation_field to
  #          canonical HIGH_LOCALDAY_MAX.* so regression to the legacy literal
  #          "daily_maximum_air_temperature" fails the test.
  ```
- **Residual call-out (test_harvester_metric_identity.py:27-30)**:
  ```
  # Residual: 1,561 pre-fix settlement rows on the live DB still carry
  # physical_quantity="daily_maximum_air_temperature"; historical-data migration
  # is owed but out of scope for this packet (NEEDS_OPERATOR_DECISION).
  ```
- **MetricIdentity class**: `src/types/metric_identity.py` lines 14-90. `HIGH_LOCALDAY_MAX` is the single canonical source of truth for the physical_quantity string.

---

## Reuse Audit Verdicts

| Artifact | Verdict | Rationale |
|----------|---------|-----------|
| `src/types/metric_identity.py` | CURRENT_REUSABLE | Defines canonical string used as migration target. Last audited 2026-04-24. INV-14 law regime. |
| `tests/test_harvester_metric_identity.py` | CURRENT_REUSABLE | Written 2026-04-24 under INV-14 law. Explicitly documents the 1561-row residual. DO NOT MODIFY. |
| `tests/test_settlements_authority_trigger.py` | CURRENT_REUSABLE — keep as-is | Tests trigger behavior, not canonical physical_quantity correctness. Uses `'daily_maximum_air_temperature'` to exercise trigger paths that are independent of this migration. DO NOT MODIFY (see decision below). |
| `tests/test_settlements_verified_row_integrity.py` | CURRENT_REUSABLE — keep as-is | Tests INSERT/UPDATE trigger integrity. Uses `'daily_maximum_air_temperature'` as a structural placeholder. Trigger behavior being tested is independent of the physical_quantity string value. DO NOT MODIFY. |
| `tests/test_settlements_unique_migration.py` | CURRENT_REUSABLE — keep as-is | Tests UNIQUE constraint migration (REOPEN-2). Physical_quantity string is incidental to the dual-track UNIQUE semantics being tested. DO NOT MODIFY. |
| `src/state/db.py` | CURRENT_REUSABLE | Schema definitions; migration operates via standalone sqlite3 calls, not via db module. |
| `src/execution/harvester.py` | CURRENT_REUSABLE | Fixed 2026-04-24 (C6 antibody). Future writes use canonical string. Not touched by this migration. |

**Decision on existing tests using legacy string**: The three tests (`test_settlements_authority_trigger.py`, `test_settlements_verified_row_integrity.py`, `test_settlements_unique_migration.py`) use `'daily_maximum_air_temperature'` as a fixture/seed value to exercise DB behaviors (trigger firing, UNIQUE constraint enforcement) that are orthogonal to canonical identity. The physical_quantity field is not the subject of any assertion in those tests. Modifying them would be scope creep and could break their structural-antibody intent. They are left as-is. A new test file (`tests/test_settlements_physical_quantity_invariant.py`) provides the post-migration invariant assertion against the live DB.

---

## Drift Evidence

### Live DB group-by (2026-04-28 snapshot)

```sql
SELECT physical_quantity, observation_field, data_version, COUNT(*)
FROM settlements GROUP BY 1, 2, 3;
```

Output:
```
daily_maximum_air_temperature|high_temp|cwa_no_collector_v0|7
daily_maximum_air_temperature|high_temp|hko_daily_api_v1|29
daily_maximum_air_temperature|high_temp|ogimet_metar_v1|67
daily_maximum_air_temperature|high_temp|wu_icao_history_v1|1458
```

Total rows: **1561** — all carry the legacy string `"daily_maximum_air_temperature"`.

### Diff vs canonical

| Field | Legacy value (current DB) | Canonical value (HIGH_LOCALDAY_MAX) | Delta |
|-------|--------------------------|-------------------------------------|-------|
| `physical_quantity` | `daily_maximum_air_temperature` | `mx2t6_local_calendar_day_max` | DRIFT |
| `observation_field` | `high_temp` | `high_temp` | MATCH |
| `temperature_metric` | `high` | `high` | MATCH |

Only `physical_quantity` drifts. The other two identity fields are already canonical.

### Root cause

Before the C6 harvester fix (2026-04-24), `src/execution/harvester.py::_write_settlement_truth` hardcoded the physical_quantity literal `"daily_maximum_air_temperature"` instead of reading it from `HIGH_LOCALDAY_MAX.physical_quantity`. The C6 fix corrected future writes. Historical rows were explicitly deferred as `NEEDS_OPERATOR_DECISION`.

---

## Migration Semantics

### Which rows change

- All rows where `temperature_metric = 'high' AND physical_quantity = 'daily_maximum_air_temperature'`
- Expected count: **1561**

### What changes

| Column | Before | After |
|--------|--------|-------|
| `physical_quantity` | `daily_maximum_air_temperature` | `mx2t6_local_calendar_day_max` |

### What stays the same

All other columns are untouched:
- `id`, `city`, `target_date`, `market_slug`
- `winning_bin`, `settlement_value`
- `settlement_source`, `settled_at`
- `authority` (VERIFIED / QUARANTINED / UNVERIFIED — not touched)
- `pm_bin_lo`, `pm_bin_hi`, `unit`, `settlement_source_type`
- `temperature_metric` (already `'high'` — correct, not changed)
- `observation_field` (already `'high_temp'` — correct, not changed)
- `data_version` (source-specific, not changed — these document provenance of the observation, not the metric identity version)
- `provenance_json` (chain of custody, must NOT change)

---

## Idempotency

The UPDATE uses:
```sql
UPDATE settlements
SET physical_quantity = 'mx2t6_local_calendar_day_max'
WHERE temperature_metric = 'high'
  AND physical_quantity = 'daily_maximum_air_temperature'
```

After a successful first run, the WHERE clause matches zero rows. A second `--apply` run:
- Takes a new snapshot (`.pre-physqty-migration-2026-04-28` will be overwritten — acceptable)
- Pre-count shows 0 rows to migrate
- UPDATE affects 0 rows
- Post-count assertion passes (0 rows still have legacy string)
- Exits 0 with `migrated=0`

**Result: fully idempotent.**

---

## Atomicity

The apply path uses:

```
BEGIN IMMEDIATE
  <pre-count SELECT>
  UPDATE settlements SET physical_quantity = 'mx2t6_local_calendar_day_max'
         WHERE temperature_metric = 'high'
           AND physical_quantity = 'daily_maximum_air_temperature'
  <post-count assertion: no legacy rows remain for temperature_metric='high'>
COMMIT   (or ROLLBACK if assertion fails)
```

`BEGIN IMMEDIATE` acquires a write lock immediately, preventing any concurrent writer from inserting new legacy rows between the pre-count and the UPDATE.

The single UPDATE statement is atomic at the SQLite level.

---

## Pre-Flight Gates

ALL of the following must pass before `--apply` is executed:

1. **DB backup taken**: `state/zeus-world.db.pre-physqty-migration-2026-04-28` must exist and its size must match `state/zeus-world.db`. The script creates this automatically via `shutil.copy2` before opening any connection.

2. **Dry-run passes**: Run `python3 scripts/migrate_settlements_physical_quantity.py --db-path state/zeus-world.db --dry-run` and confirm output shows `would_change=1561`.

3. **Preventive antibody intact**: `pytest tests/test_harvester_metric_identity.py` must PASS. This confirms the harvester future-write protection is still active.

4. **New invariant test detects drift** (pre-migration): `pytest tests/test_settlements_physical_quantity_invariant.py -v` must FAIL on `test_settlements_high_uses_canonical_physical_quantity`. This confirms the test is live and will detect drift. (It will pass post-migration.)

5. **Existing tests still pass**: `pytest tests/test_settlements_authority_trigger.py tests/test_settlements_verified_row_integrity.py tests/test_settlements_unique_migration.py` must PASS. These are unchanged structural antibodies and must not regress.

6. **Zeus daemon is stopped**: Confirm `ZEUS_MODE=live python -m src.main` is NOT running against the live DB during migration. Concurrent writes during `BEGIN IMMEDIATE` will be serialized but unexpected INSERTs between dry-run and apply could change the row count.

---

## Antibody Extension (Post-Migration Invariant)

New file: `tests/test_settlements_physical_quantity_invariant.py`

Three test cases:
- `test_settlements_high_uses_canonical_physical_quantity`: asserts no live row has `temperature_metric='high' AND physical_quantity != HIGH_LOCALDAY_MAX.physical_quantity`. This test FAILS before migration and PASSES after. It is the persistent post-migration invariant.
- `test_settlements_low_uses_canonical_physical_quantity_or_absent`: asserts no live row has `temperature_metric='low' AND physical_quantity != LOW_LOCALDAY_MIN.physical_quantity`. Currently passes vacuously (no LOW rows in DB). When LOW rows are written by the harvester, this becomes a live gate.
- `test_canonical_strings_match_registry`: pure import check — verifies `HIGH_LOCALDAY_MAX.physical_quantity` and `LOW_LOCALDAY_MIN.physical_quantity` are non-empty strings. Fails if the constants are deleted or corrupted.

All DB-touching tests use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` (read-only, no WAL interference). They SKIP gracefully when `state/zeus-world.db` does not exist (CI safety).

---

## Reversibility

The script takes a filesystem snapshot via `shutil.copy2` BEFORE opening any DB connection:
```
state/zeus-world.db.pre-physqty-migration-2026-04-28
```

Roll-back procedure (if post-migration integrity check fails):
1. The script automatically rolls back the transaction and restores the snapshot if the post-count assertion fails.
2. Manual rollback if needed: `cp state/zeus-world.db.pre-physqty-migration-2026-04-28 state/zeus-world.db`
3. Verify: `sqlite3 state/zeus-world.db "SELECT COUNT(*) FROM settlements WHERE physical_quantity = 'daily_maximum_air_temperature';"` must return 1561.

The snapshot is NOT deleted after a successful migration. It is retained as a point-in-time backup until the operator explicitly removes it.

---

## Risk Classification

**Category**: Silent semantic corruption — data-provenance failure (Fitz Constraint #4).

**What failure this prevents**: Any downstream JOIN, filter, or aggregation that uses `physical_quantity = 'mx2t6_local_calendar_day_max'` to select settlement rows silently returns zero rows because 100% of the live rows carry the legacy string. This is identical to the failure mode documented in `test_harvester_metric_identity.py` docstring: "any downstream JOIN filtering on canonical physical_quantity silently dropped 100% of harvester-written settlement rows."

**Category in Fitz methodology**: This is a data-provenance failure at the Module A → Module B boundary. The harvester (Module A) wrote correct data under old law; the type system (Module B, MetricIdentity) now defines a different canonical string. The semantic mismatch is invisible to code correctness checks — code is correct, data semantics are broken (per Constraint #4: "Correct code + wrong data semantics = silent disaster").

**Structural fix**: This migration makes the category impossible by aligning 100% of historical rows with the canonical MetricIdentity registry. The post-migration invariant test (`test_settlements_high_uses_canonical_physical_quantity`) becomes the persistent immune-system antibody — any future harvester regression or manual backfill using the legacy string will fail CI.

**Risk level**: LOW. The migration changes one column on rows with a pre-existing legacy string. It does not alter authority, provenance_json, settlement_value, or any column that drives settlement logic. It is fully reversible from the pre-migration snapshot.
