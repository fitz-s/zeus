# Legacy Audit: Position Write Path Analysis

**Date:** 2026-04-06
**Auditor:** legacy-tracer (zeus-legacy-audit team)

## 1. Complete Write Path Map

### Table: `trade_decisions` (LEGACY)

| Writer Function | Source File | Line | Trigger |
|---|---|---|---|
| `log_trade_entry()` | `src/state/db.py` | 1326 | New entry (filled or pending_tracked) |
| `log_trade_exit()` | `src/state/db.py` | 1553 | Exit fill confirmed |
| `record_shadow_attribution_trade()` | `src/state/db.py` | 758+ | Shadow attribution |
| Direct INSERT (signal log) | `src/state/db.py` | 802 | Decision recording |

### Table: `position_events` (CANONICAL — event-sourced)

| Writer Function | Source File | Line | Trigger |
|---|---|---|---|
| `append_event_and_project()` | `src/state/ledger.py` | 69 | Single event write |
| `append_many_and_project()` | `src/state/ledger.py` | 86 | Batch event write |

**Callers of `append_many_and_project`:**
1. `_dual_write_canonical_entry_if_available()` — `cycle_runtime.py:259` — entry dual-write
2. `_dual_write_canonical_settlement_if_available()` — `harvester.py:104` — settlement dual-write
3. `_append_canonical_rescue_if_available()` — `chain_reconciliation.py:190` — rescue dual-write
4. `_append_canonical_size_correction_if_available()` — `chain_reconciliation.py:222` — size correction
5. `seed_canonical_position_from_open_portfolio()` — `db.py:741` — backfill seeder

### Table: `position_current` (CANONICAL — projected state)

Written ONLY by `upsert_position_current()` (`projection.py:83`), called exclusively from within `append_event_and_project` / `append_many_and_project`. **No other writer exists.**

## 2. Entry Path Trace

When a new trade enters via `cycle_runtime.py`:

```
cycle_runtime.py:949  → result.status in ("filled", "pending")
cycle_runtime.py:950  → pos = materialize_position(...)     # creates Position object
cycle_runtime.py:961  → deps.add_position(portfolio, pos)   # adds to in-memory portfolio
cycle_runtime.py:964  → log_trade_entry(conn, pos)          # ★ WRITES trade_decisions (ALWAYS)
cycle_runtime.py:965  → _dual_write_canonical_entry_if_available(conn, pos, ...)  # ★ ATTEMPTS canonical write
cycle_runtime.py:971  → log_execution_report(conn, pos, result, ...)
```

### Critical: `_dual_write_canonical_entry_if_available` (cycle_runtime.py:246)

```python
def _dual_write_canonical_entry_if_available(conn, pos, *, decision_id, deps) -> bool:
    if conn is None:
        return False                          # ← Bypass #1: no DB connection
    try:
        events, projection = build_entry_canonical_write(pos, ...)
        append_many_and_project(conn, events, projection)
    except RuntimeError as exc:
        deps.logger.debug(...)                # ← Bypass #2: SILENTLY catches RuntimeError
        return False
    return True
```

**The `RuntimeError` catch at line 260 is the smoking gun.** It catches and SILENTLY logs (at DEBUG level!) any `RuntimeError` from:
- `assert_canonical_transaction_schema()` — fails if tables missing/wrong columns
- `require_payload_fields()` — fails if event/projection missing fields
- `validate_event_projection_pair()` / `validate_event_projection_batch()` — fails on mismatches

## 3. Exit Path Trace

```
cycle_runtime.py:554  → execute_exit(portfolio, position, exit_context, ...)
exit_lifecycle.py:183 → execute_exit()
exit_lifecycle.py:233 → _execute_live_exit()    # places sell order
exit_lifecycle.py:432 → check_pending_exits()   # on next cycle, checks fill
exit_lifecycle.py:507 → compute_economic_close() # closes position in portfolio
```

**EXIT HAS ZERO CANONICAL WRITES.** The exit lifecycle (`exit_lifecycle.py`) does not import or call `append_many_and_project`, `append_event_and_project`, or any canonical write function. Exit events go ONLY to:
- `log_exit_fill_event()` → legacy event log
- `log_pending_exit_recovery_event()` → legacy event log  
- `log_exit_retry_event()` → legacy event log
- `log_trade_exit()` → INSERT into `trade_decisions`

**Settlement** (harvester) DOES have a canonical dual-write (`_dual_write_canonical_settlement_if_available` at `harvester.py:75`), but it **guards** on `_has_canonical_position_history()` — which checks if `position_events` already has rows for that trade_id. If the entry canonical write was silently skipped, the settlement canonical write will also be skipped.

## 4. Root Cause Analysis

### Why canonical writes stopped on April 3 ~20:13 UTC

**Hypothesis A (MOST LIKELY): `RuntimeError` silently swallowed**

The `_dual_write_canonical_entry_if_available` function catches `RuntimeError` at line 260 and logs at DEBUG level only. The `assert_canonical_transaction_schema` function raises `RuntimeError` in three cases:

1. `position_events` or `position_current` tables don't exist
2. `position_events` missing required columns from `CANONICAL_POSITION_EVENT_COLUMNS`
3. `position_current` missing required columns from `CANONICAL_POSITION_CURRENT_COLUMNS`

The migration (`2026_04_02_architecture_kernel.sql`) is applied during `init_schema()` → `_ensure_runtime_bootstrap_support_tables()`. If this function encounters the legacy `position_events` table (with different columns), it tries to:
1. Rename it to `position_events_legacy` (line 585)
2. Then apply the canonical schema (line 597)

**If this rename fails or the migration partially applies**, the canonical schema assertion will fail on every subsequent cycle, and the `except RuntimeError` will silently skip the canonical write.

**Hypothesis B: `idempotency_key UNIQUE` constraint violation**

The `position_events` table has `idempotency_key TEXT UNIQUE`. The key is constructed as `{trade_id}:{event_type_slug}` (lifecycle_events.py:149). If a position was partially written (e.g., first event inserted but second failed), re-running would hit a UNIQUE violation on the first event's idempotency_key. However, this would raise `sqlite3.IntegrityError`, not `RuntimeError`, so it would **not** be caught by the existing handler — it would propagate up and likely crash the cycle. This makes it less likely as the cause of silent failure.

**Hypothesis C: `build_entry_canonical_write` raises on phase**

`build_entry_canonical_write` (lifecycle_events.py:164) raises `ValueError` if phase is not in `{PENDING_ENTRY, ACTIVE, DAY0_WINDOW}`. A `ValueError` is NOT a `RuntimeError`, so it would propagate. However, `_entry_event` and `build_position_current_projection` could raise `RuntimeError` from `_non_empty()` if all timestamp fields are empty/None.

### Key Evidence: The cascade effect

Once canonical entry writes are silently skipped:
1. `position_events` gets no rows for new trades
2. `position_current` gets no UPSERT for new trades
3. Harvester's `_has_canonical_position_history()` returns False → settlement dual-write skipped
4. Chain reconciliation's rescue/size-correction dual-writes check for baseline → skip
5. **Result: Complete canonical path death for any trade entered after the failure starts**

## 5. What Needs to Change

### Immediate Fix (restore canonical writes)
1. **Change `except RuntimeError` to log at WARNING or ERROR level** in `_dual_write_canonical_entry_if_available` (cycle_runtime.py:260-261). Silent DEBUG logging masks a critical data path failure.
2. **Add a health check** that counts `trade_decisions` rows with status='entered' that have no matching `position_current` row. Alert if count > 0.
3. **Run `seed_canonical_position_from_open_portfolio()`** (db.py:730+) to backfill the 37 missing trades into `position_events`/`position_current`.

### Structural Fix (make the category impossible)
4. **Add canonical dual-write to exit lifecycle.** `exit_lifecycle.py` has ZERO canonical writes — exits never update `position_events` or `position_current`. This means even when canonical entry writes work, the position phase in `position_current` never progresses past `active`/`day0_window` to `pending_exit`/`economically_closed`.
5. **Replace `except RuntimeError` with explicit schema pre-check.** Instead of try/except, call `assert_canonical_transaction_schema` once at cycle start and set a flag. If schema is missing, log a WARNING once and skip all canonical writes for the cycle. Don't catch errors per-trade.
6. **Add a monotonic staleness counter** to the canonical write path. If N consecutive canonical writes return `False`, escalate to WARNING/ERROR.

### The Fundamental Design Gap
The "dual-write" architecture was implemented for ENTRY (cycle_runtime), SETTLEMENT (harvester), and RECONCILIATION (chain_reconciliation) — but **never for EXIT** (exit_lifecycle). This means `position_current.phase` can never reach `pending_exit` or `economically_closed` through the canonical path. The canonical surface was born incomplete.
