# F22 Writer-Lock Fix — Operator-Script Contract
# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: tests/test_operator_script_lock_contract.py (F22 antibody)
#                  tests/test_writer_lock_defer_markers_expiry.py (30-day expiry)
#                  src/state/db_writer_lock.py (WriteClass, db_writer_lock)

## Background

F22 finding (OPS_FORENSICS.md §F22): 43 scripts had raw read-write
`sqlite3.connect()` without the writer-lock contract. 12 were in the
"operator-action" subset. The contract antibody enforces one of three resolutions
per script:

- **(a)** Wrap writes with `db_writer_lock(db_path, WriteClass.BULK)` — for scripts
  whose writes could race the live daemon.
- **(b)** Use `?mode=ro` URI — for genuinely read-only scripts.
- **(c)** Add `# WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD` marker — for one-shot DDL
  migrations intended to run with the daemon DOWN. Marker expires after 30 days;
  renewal requires a new entry here.

This file records the justification for every `(c)` defer marker applied to the
8 pre-existing reds resolved on 2026-05-29.

---

## Resolution log — 2026-05-29

### 1. migrate_no_trade_events_rebuild_phase3_t2.py — Resolution (a)

**Write class:** BULK wrap added.
**Writes:** INSERT (row copy), DROP TABLE, RENAME TABLE, PRAGMA user_version = 18
inside a SAVEPOINT on `zeus-world.db`.
**Daemon-state assumption:** daemon may be running; db_writer_lock(BULK) is the
correct retrofit because these are real DML writes to a DB the live daemon also
writes.
**Change:** top-level import of `db_writer_lock` / `WriteClass` added; the
`sqlite3.connect(...)` block in `run()` is wrapped in
`with db_writer_lock(db_path, WriteClass.BULK):`.

---

### 2. migrate_settlement_commands_in_flight_at_era_flip.py — Resolution (c)

**Write class:** DML (INSERT/UPDATE) via helper — already contract-compliant.
**Why deferred, not required:** The script executes real DML (INSERT INTO
settlement_commands_era_quarantine, UPDATE settlement_commands SET
status='ERA_QUARANTINED') but routes all DB access through
`get_forecasts_connection_with_world()`, which is already writer-lock
contract-compliant. The false-positive is on the bare-`sqlite3.connect` scan
only: the only literal `sqlite3.connect(` in the file is inside a docstring
comment (`"DISK SAFETY: ... No bare sqlite3.connect()."`) — not a call site.
The regex `sqlite3\.connect\(` matches that comment text. The defer marker
suppresses the false positive; no db_writer_lock retrofit is needed because the
writes are already routed through the compliant helper.
**Daemon-state assumption:** n/a — the raw-connect scan false-positive is the
sole trigger; actual writes are helper-routed.

---

### 3. migrate_model_bias_ens_canonical_fields.py — Resolution (c)

**Write class:** DDL-only (ALTER TABLE ADD COLUMN).
**Why deferred:** Script adds nullable columns to `model_bias_ens` in
`zeus-forecasts.db`. It is `--commit` gated (dry-run by default). The docstring
states it targets a staging/copy DB only — "NEVER prod without explicit operator
approval". Each ALTER is guarded by `PRAGMA table_info` so re-runs are no-ops.
No INSERT, UPDATE, or DELETE. A db_writer_lock(BULK) wrap would be the correct
Phase 1+ retrofit when the `--commit --db` path is used against a shared DB, but
is deferred because the script is design-gated to staging copies.
**Daemon-state assumption:** daemon DOWN; staging copy only.

---

### 4. migrate_ensemble_snapshots_alpha_proxy.py — Resolution (c)

**Write class:** DDL-only (ALTER TABLE ADD COLUMN).
**Why deferred:** Adds 3 nullable columns to `ensemble_snapshots` in
`zeus-forecasts.db`. Each ALTER is guarded by `PRAGMA table_info`. The bare
`sqlite3.connect(db_path)` is only reached via the `--db` CLI override in
`main()`; the default code path calls `get_forecasts_connection()` (already
contract-compliant). No INSERT, UPDATE, or DELETE. Phase 1+ retrofit: add
db_writer_lock(BULK) around the `--db` override path in `main()`.
**Daemon-state assumption:** daemon DOWN when `--db` override path is used.

---

### 5. migrate_settlement_commands_polymarket_anchor.py — Resolution (c)

**Write class:** DDL-only (ALTER TABLE ADD COLUMN).
**Why deferred:** Adds columns to `settlement_commands` and `wrap_unwrap_commands`
in `zeus-world.db` and `zeus_trades.db`. Each ALTER is guarded by
`PRAGMA table_info`. The bare `sqlite3.connect(db_path)` is only reached via the
`--db` CLI override in `main()`; the default code path calls
`get_trade_connection_with_world()` (already contract-compliant). No INSERT,
UPDATE, or DELETE. Phase 1+ retrofit: add db_writer_lock(BULK) around the `--db`
override path in `main()`.
**Daemon-state assumption:** daemon DOWN when `--db` override path is used.

---

### 6. migrate_no_trade_events_create_2026_05_21.py — Resolution (c)

**Write class:** DDL-only (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS).
**Why deferred:** Creates `no_trade_events` table and two indexes in
`zeus-world.db`. All statements use IF NOT EXISTS — idempotent. No INSERT,
UPDATE, or DELETE. The daemon writes zeus-world.db, but CREATE IF NOT EXISTS is
atomic in SQLite and safe if the table already exists. Classified as daemon-DOWN
one-shot per ops policy; db_writer_lock(BULK) retrofit is lowest priority for
pure CREATE-only scripts.
**Daemon-state assumption:** daemon DOWN; one-shot operator run.

---

### 7. migrate_decision_events_create_2026_05_19.py — Resolution (c)

**Write class:** DDL-only (CREATE TABLE IF NOT EXISTS, CREATE TRIGGER IF NOT
EXISTS, CREATE INDEX IF NOT EXISTS x3).
**Why deferred:** Creates `decision_events` table, a backstop AFTER INSERT
TRIGGER, and 3 indexes in `zeus-world.db`. All DDL uses IF NOT EXISTS guards —
idempotent. No INSERT, UPDATE, or DELETE in the migration itself (the trigger
fires on future inserts by compliant writers, not during migration). Daemon-DOWN
one-shot per ops policy; db_writer_lock(BULK) retrofit is lowest priority for
pure CREATE-only scripts.
**Daemon-state assumption:** daemon DOWN; one-shot operator run.

---

### 8. migrate_book_hash_transitions_create_2026_05_21.py — Resolution (c)

**Write class:** DDL-only (CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS x2).
**Why deferred:** Creates `book_hash_transitions` table and two indexes in
`zeus_trades.db`. All statements use IF NOT EXISTS — idempotent. No INSERT,
UPDATE, or DELETE. The daemon writes zeus_trades.db, but CREATE IF NOT EXISTS is
safe with concurrent access. Daemon-DOWN one-shot per ops policy; db_writer_lock
retrofit is lowest priority for pure CREATE-only scripts.
**Daemon-state assumption:** daemon DOWN; one-shot operator run.

---

## Phase 1+ retrofit queue (from defer entries above)

Scripts deferred via (c) that should receive db_writer_lock(BULK) wraps if ever
run against a shared production DB while the daemon is live:

1. `migrate_model_bias_ens_canonical_fields.py` — ALTER on zeus-forecasts.db
2. `migrate_ensemble_snapshots_alpha_proxy.py` — ALTER on zeus-forecasts.db (--db path)
3. `migrate_settlement_commands_polymarket_anchor.py` — ALTER on zeus-world.db (--db path)
4. `migrate_no_trade_events_create_2026_05_21.py` — CREATE on zeus-world.db
5. `migrate_decision_events_create_2026_05_19.py` — CREATE on zeus-world.db
6. `migrate_book_hash_transitions_create_2026_05_21.py` — CREATE on zeus_trades.db

`migrate_settlement_commands_in_flight_at_era_flip.py` requires no retrofit
(defer marker suppresses a comment-only regex false-positive; no raw connects).
