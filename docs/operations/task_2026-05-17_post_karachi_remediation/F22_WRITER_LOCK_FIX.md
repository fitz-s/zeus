# F22 — Operator Script Writer-Lock Contract Fix

**Date**: 2026-05-17  
**Branch**: fix/wave2-f22-operator-script-writer-lock-2026-05-17  
**Authority**: OPS_FORENSICS.md §F22

---

## Verbatim Findings (OPS_FORENSICS.md §F22)

> Total raw read-write `sqlite3.connect` (no `?mode=ro`) sites: **43 scripts**.  
> Operator-action subset: **12 scripts**.

### Top-5 most dangerous during live trading

| # | Script | Last touched | Lock? | Targets | Hazard |
|---|---|---|---|---|---|
| 1 | `scripts/migrations/202605_add_redeem_operator_required_state.py` | 2026-05-16 | NO (fcntl only) | all 4 DBs | DDL rebuilds settlement_commands during live; races daemon writes |
| 2 | `scripts/migrate_world_observations_to_forecasts.py` | 2026-05-15 | NO | forecasts.observations + market_events_v2 | Docstring says "stop ingest" but no enforcement |
| 3 | `scripts/bridge_oracle_to_calibration.py` | 2026-05-05 | NO | reads zeus-world.db | (F40 root cause itself + no lock) |
| 4 | `scripts/cleanup_ghost_positions.py` | 2026-05-07 | YES | trade_decisions | Read-classify race outside lock window |
| 5 | `scripts/force_cycle_with_healthy_gates.py` | 2026-05-08 | YES | control_overrides_history | Suppresses legitimate bid-safety check |

---

## CI Antibody Design

**File**: `tests/test_operator_script_lock_contract.py`

**Scope**: `scripts/{operator_*,cleanup_*,force_*,bridge_*,migrate_*}.py` + `scripts/migrations/2*.py`

**Excluded** (runner infrastructure, not operator data scripts):
- `scripts/migrations/__init__.py`
- `scripts/migrations/__main__.py`

**Logic**: For each in-scope script:
1. If no `sqlite3.connect(` → skip (not in scope)
2. If all connects use `?mode=ro` → pass
3. If `db_writer_lock` / `register_known_connection` / `acquire_writer_lock` present → pass
4. If `# WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD` marker present → pass
5. Otherwise → **FAIL** with file:line citation of the raw connect

**Meta-verify** (sed-break/restore on `cleanup_ghost_positions.py`):
- Removed all `db_writer_lock` occurrences → antibody FAILED with:
  `scripts/cleanup_ghost_positions.py: raw read-write sqlite3.connect ... line 115`
- Restored → 22 passed

---

## Per-Script Status

| Script | Status | Rationale |
|---|---|---|
| `scripts/migrations/202605_add_redeem_operator_required_state.py` | DEFER | Standalone `_migrate_one_db()` uses fcntl daemon-stop check (not db_writer_lock). Runner-invoked `up(conn)` is lock-free by design (runner owns lock). Marker added. |
| `scripts/migrate_world_observations_to_forecasts.py` | DEFER | One-shot backfill; docstring says "stop daemon first." db_writer_lock retrofit deferred WAVE-3. Marker added. |
| `scripts/migrate_world_to_forecasts.py` | DEFER | One-shot K1 DB-split migration; docstring says stop daemon first. Marker added. |
| `scripts/migrate_backtest_runs_lane_constraint_2026_05_07.py` | DEFER | Backtest DB only (not a live-trading DB). Marker added. |
| `scripts/bridge_oracle_to_calibration.py` | ALREADY OK | No raw `sqlite3.connect` — uses `get_forecasts_connection_with_world()` which internally acquires `db_writer_lock` at `src/state/db.py:244-245`. |
| `scripts/cleanup_ghost_positions.py` | ALREADY OK | Has `db_writer_lock` (import line 28 + context manager line 114). **Out-of-scope hazard**: OPS_FORENSICS flags a read-classify race outside the lock window (find_ghosts runs while lock is held but the classification logic runs per-row with separate cursor reuse). Lock-scope correctness issue; not addressable by the antibody contract. |
| `scripts/force_cycle_with_healthy_gates.py` | ALREADY OK | Has `db_writer_lock` (import line 48 + context manager line 55). **Out-of-scope hazard**: OPS_FORENSICS flags that this script suppresses a legitimate bid-safety check (hazard is operational, not a missing writer-lock). Not addressable by the antibody contract. |

### Deferred scripts requiring WAVE-3 attention

All 4 deferred scripts carry `# WRITER_LOCK_DEFER_REVIEW=2026-05-17` marker so the CI
antibody passes today. WAVE-3 must either:
- Retrofit `db_writer_lock` wrapper around each raw connect, OR
- Confirm the script is permanently retired and delete it

---

## WAVE-5 carry-forward: marker expiry (2026-05-18)

The sibling antibody `tests/test_operator_script_lock_contract.py` was *accepting* any
`WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD` marker as a contract escape — a defer marker dated
2026-05-17 was functionally identical to a defer marker dated 1970-01-01. Without expiry,
defer markers accumulate forever and the contract escape becomes a permanent waiver.

**Policy** (in force from 2026-05-18):
- Every `WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD` marker is valid for **30 days from its date**.
- After 30 days the marker is **overdue** and the CI antibody
  `tests/test_writer_lock_defer_markers_expiry.py` fails for the bearing script.
- Resolution options (in the failure message):
  - (a) Apply the writer-lock contract (`with db_writer_lock(...)`); delete the marker.
  - (b) Retire the script entirely if no longer in use.
  - (c) Bump the marker date to today **and** add a renewed defer rationale here.

This makes "DEFER" a stage with a deadline, not a parking lot. Re-deferring is a deliberate
operator action that must leave a paper trail in this document, not a silent date bump.

### Current marker inventory (2026-05-18)

| Script | Marker date | Days remaining | Outstanding work |
|---|---|---|---|
| `scripts/migrate_world_observations_to_forecasts.py` | 2026-05-17 | 29 | Retrofit `db_writer_lock` OR delete (one-shot K1 backfill, likely retired). |
| `scripts/migrate_backtest_runs_lane_constraint_2026_05_07.py` | 2026-05-17 | 29 | Backtest DB only (not live-trading). Retrofit lower priority; confirm retire status. |
| `scripts/migrate_world_to_forecasts.py` | 2026-05-17 | 29 | One-shot K1 split migration. Confirm retired; delete preferable to retrofit. |
| `scripts/migrations/202605_add_redeem_operator_required_state.py` | 2026-05-17 | 29 | Standalone `_migrate_one_db()` path needs fcntl→`db_writer_lock` migration. F23-runner path already correct. |
| `scripts/migrations/202605_position_current_bridge_required_trigger.py` | 2026-05-17 | 29 | Confirm runner-only invocation; if standalone CLI path exists, retrofit. |

By 2026-06-16 (30 days from marker date) each row above must resolve to one of (a/b/c)
or the antibody will block CI.

### Meta-verify

The expiry antibody was confirmed via sed-break/restore on 2026-05-18:
- Set `scripts/migrate_world_to_forecasts.py` marker to `2026-01-01`.
- `pytest tests/test_writer_lock_defer_markers_expiry.py::test_defer_marker_within_window`
  failed with: `WRITER_LOCK_DEFER_REVIEW=2026-01-01 is 137 days old (window=30 days, today=2026-05-18)`.
- Restored marker; 9/9 tests pass.

---

## NIT1 Carry-Forward Assessment

The brief requested "refactor to F23 runner convention; drop bootstrap allowlist entry"
for `202605_add_redeem_operator_required_state.py`.

**Finding**: `def up(conn)` already exists at line 323 of that file — the F23 runner
convention was already applied.

The `_BOOTSTRAP_APPLIED` entry in `scripts/migrations/__init__.py` drop is **DEFERRED to
WAVE-3 pending idempotency review of the `up(conn)` no-op path**. The `up(conn)` wrapper
already calls `_is_already_applied(conn)` which checks `sqlite_master.sql LIKE
'%REDEEM_OPERATOR_REQUIRED%'`, making re-application a no-op on existing DBs even without
the `_BOOTSTRAP_APPLIED` guard. The conservative choice is to leave the entry in place and
defer the drop until WAVE-3 confirms the no-op path is exercised under all migration runner
call sites. NIT1 structural work (exposing `def up(conn)`) is complete; ledger entry cleanup
is the only outstanding item.
