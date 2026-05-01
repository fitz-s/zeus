# observations K1 dual-atom migration

**Created**: 2026-05-01
**Authority basis**: Operator directive 2026-05-01 — daemon-correctness fix.
The live `state/zeus-world.db::observations` table fell out of sync with the
K1 schema declared in `src/state/db.py::init_schema` somewhere between
2026-04-19 (the last successful WU daily insert) and 2026-04-21. Every WU
daily insert since then was failing with `table observations has no column
named high_raw_value`, freezing Zeus's daily observation pool for ~12 days
and starving the harvester truth writer.

## What the migration does

Adds the 16 K1 dual-atom columns to `observations` if they are missing, then
back-fills `high_*` / `low_*` values from any rows that still carry the
legacy single-atom shape (`raw_value`, `value_type`, `provenance_metadata`):

| Column added | Type | Purpose |
|---|---|---|
| `high_raw_value` | REAL | Raw value of the daily HIGH atom |
| `high_raw_unit` | TEXT | Unit reported by the source for HIGH |
| `high_target_unit` | TEXT | Unit Zeus normalises HIGH to |
| `low_raw_value` | REAL | Raw value of the daily LOW atom |
| `low_raw_unit` | TEXT | Unit reported by the source for LOW |
| `low_target_unit` | TEXT | Unit Zeus normalises LOW to |
| `high_fetch_utc` | TEXT | UTC timestamp the HIGH atom was fetched |
| `high_local_time` | TEXT | Local-time of the HIGH peak |
| `high_collection_window_start_utc` | TEXT | Start of HIGH collection window |
| `high_collection_window_end_utc` | TEXT | End of HIGH collection window |
| `low_fetch_utc` | TEXT | UTC timestamp the LOW atom was fetched |
| `low_local_time` | TEXT | Local-time of the LOW trough |
| `low_collection_window_start_utc` | TEXT | Start of LOW collection window |
| `low_collection_window_end_utc` | TEXT | End of LOW collection window |
| `high_provenance_metadata` | TEXT | JSON blob of HIGH provenance |
| `low_provenance_metadata` | TEXT | JSON blob of LOW provenance |

Legacy columns (`raw_value`, `value_type`, `provenance_metadata`,
`fetch_utc`, `local_time`, `collection_window_*`) are **kept** so existing
queries keep working during the dual-write transition.

## Concurrency safety

The migration uses `BEGIN IMMEDIATE` to take an exclusive write lock; SQLite
serialises ALTER TABLE against concurrent writes from the live ingest
daemon, so the daemon's K2 writers block briefly (~ms) and resume once the
migration commits. WAL mode is preserved.

## Idempotency

`PRAGMA table_info` gates each ALTER TABLE statement. A second run is a
strict no-op:

```text
INFO __main__: observations already migrated; no ALTER TABLE needed.
{'status': 'noop_already_migrated', 'altered': [], 'backfill': {...}}
```

## Usage

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
.venv/bin/python scripts/migrate_observations_k1.py [--dry-run]
```

`--dry-run` prints the planned ALTERs and rolls back without touching the DB.

## Antibody

`tests/test_observations_k1_migration.py` covers:

- Migration adds every required column to a legacy-shaped fixture DB.
- Re-running is a strict no-op (idempotency).
- Backfill pivots legacy `value_type='high'` rows into `high_*` columns,
  `value_type='low'` rows into `low_*` columns.
- Dry-run does not mutate the schema.
- `plan_migration()` reports exactly the missing columns.

## When to re-run

Only when `init_schema` adds new K1 columns. The migration is forward-only:
it never drops columns, never modifies data outside the legacy → K1 pivot,
and never reorders rows. If a future K1.x adds new columns, append them to
`REQUIRED_K1_COLUMNS` in the script and re-run.
