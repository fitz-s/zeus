# zeus_trades.db-wal grew to 810 MB → snapshot-capture `database is locked` → spine starved

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: GOAL #83 (continuous settlement-graded alpha) + RULE 1 (a "no fresh
  candidates" symptom is OUR defect). Twin of the 2026-06-04 zeus-world.db WAL backstop.
- Capability touched: `src/state/db.py` (read-only checkpoint helper) + `src/main.py`
  (scheduler job). Reversibility = a checkpoint is not a write txn; places no order.

## Defect (live, evidenced 2026-06-16)

`state/zeus_trades.db-wal` was **810 MB**. A long-lived READER connection in the live daemon
held a WAL snapshot (read-mark) across cycles, pinning the WAL floor so
`PRAGMA wal_checkpoint(TRUNCATE)` returned BUSY (1) and never truncated → unbounded WAL growth.
Every `executable_market_snapshots` write then triggered a blocked auto-checkpoint that
contended for the write lock → `database is locked` on EVERY capture
(`fresh_executable_city_count=0`, `inserted=0` continuously since ~07:00) → the q-kernel spine
had no fresh executable snapshots → could not price fresh families → no crosses. The spine had
crossed 6 buy_no orders 01:14–04:41 (while capture worked); it degraded when the WAL bloated.

Evidence: `PRAGMA wal_checkpoint(PASSIVE)` checkpointed 14246/20964 frames; `TRUNCATE` returned
busy=1 (a reader pinned the floor); WAL stayed 810 MB. zeus-world.db has had a periodic
checkpoint backstop since 2026-06-04 (`checkpoint_world_wal` + `_world_wal_checkpoint_cycle`);
**the trade DB had none** — the gap.

## Change

- **Immediate:** restarted `com.zeus.live-trading` → the floor-pinning connection closed → the
  WAL truncated 810 MB → 0 (capture began recovering).
- **Durable (this commit):** `checkpoint_trades_wal()` (db.py) — the zeus_trades.db twin of
  `checkpoint_world_wal`: a dedicated short-lived connection runs `wal_checkpoint(TRUNCATE)`, no
  write mutex (a checkpoint is not a write txn; SQLite serializes checkpoints internally), closed
  immediately so it never itself pins the floor. `_trades_wal_checkpoint_cycle` (main.py) — a 90s
  scheduler job (offset from the world job) that runs it and ALWAYS logs the
  (busy, log_frames, checkpointed) triple; a chronic `busy == 1` is a LOUD warning that names the
  floor-pinning-reader regression (it is not silenced).

## Reversibility / safety

Read-only checkpoint; no writes, no order. Mirrors the proven world-DB backstop exactly.
`git revert` removes it. Worst case it logs a busy warning each cycle.

## Verification / status

- `checkpoint_trades_wal()` runs against the live DB; py_compile OK on both files.
- The job deploys on the daemon's next restart; it bounds WAL growth (reclaims freed frames each
  cycle) and makes the floor-pinner observable.

## Follow-up (Part 1 — the specific floor-pinner)

The world fix had TWO parts: Part 1 = each long-lived reader `conn.rollback()`s per cycle so the
floor advances; Part 2 = this periodic backstop. The reactor's per-cycle trade_conn already
commits per-event (2026-06-08 antibody) and reopens each cycle; the harvester's read conn is
short-lived (closed in finally). The remaining floor-pinner is a longer-lived trade-DB reader
not yet pinned down — the backstop's `busy=1` warning will surface it in the logs. Adding the
per-cycle release to that reader is the Part-1 completion (tracked, not blocking — the backstop
bounds the damage meanwhile).
