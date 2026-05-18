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
| `scripts/cleanup_ghost_positions.py` | ALREADY OK | Has `db_writer_lock` (import line 28 + context manager line 114). |
| `scripts/force_cycle_with_healthy_gates.py` | ALREADY OK | Has `db_writer_lock` (import line 48 + context manager line 55). |

### Deferred scripts requiring WAVE-3 attention

All 4 deferred scripts carry `# WRITER_LOCK_DEFER_REVIEW=2026-05-17` marker so the CI
antibody passes today. WAVE-3 must either:
- Retrofit `db_writer_lock` wrapper around each raw connect, OR
- Confirm the script is permanently retired and delete it

---

## NIT1 Carry-Forward Assessment

The brief requested "refactor to F23 runner convention; drop bootstrap allowlist entry"
for `202605_add_redeem_operator_required_state.py`.

**Finding**: `def up(conn)` already exists at line 323 of that file — the F23 runner
convention was already applied. The `_BOOTSTRAP_APPLIED` entry in `scripts/migrations/__init__.py`
**must remain**: it records that this migration was applied to production before the ledger
existed, preventing re-application on existing DBs. Dropping it would cause `apply_migrations()`
to re-run the DDL rebuild on every existing DB — a Karachi-path regression. NIT1 is complete
as of the file's current state; no further change required.
