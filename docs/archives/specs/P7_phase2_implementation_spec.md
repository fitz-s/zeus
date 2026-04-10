# P7 Phase 2 Implementation Spec: Exit/Settlement Canonical Writes

**Author:** boundary-auditor-2  
**Date:** 2026-04-07  
**Based on:** docs/archives/audits/boundary_audit_2.md canonical gap analysis

## Problem

72 exit/settlement/lifecycle events exist only in `position_events_legacy`. The canonical `position_events` table has zero such events. This causes:
- 11 of 24 `position_current` rows to be stale (legacy has newer timestamps)
- `query_portfolio_loader_view()` returns `CANONICAL_AUTHORITY_UNAVAILABLE` for all 24
- Both `cycle_runner` and `riskguard` fall back to empty `positions.json`
- `cycle_runner` sees `realized_pnl=0` (does not backfill); `riskguard` sees `-13.03` (backfills from chronicle)
- The trading engine cannot monitor/exit any open position

## Infrastructure Already Exists

The canonical write infrastructure is **complete**. These components exist and work:

| Component | File | Purpose |
|-----------|------|--------|
| `append_event_and_project()` | `src/state/ledger.py:69` | Atomically inserts event + upserts projection |
| `append_many_and_project()` | `src/state/ledger.py:86` | Batch insert events + single projection upsert |
| `upsert_position_current()` | `src/state/projection.py:84` | Updates `position_current` row |
| `build_entry_canonical_write()` | `src/engine/lifecycle_events.py:156` | Entry event builder (reference pattern) |
| `build_settlement_canonical_write()` | `src/engine/lifecycle_events.py:223` | Settlement event builder (**ALREADY EXISTS, not called**) |
| `build_position_current_projection()` | `src/engine/lifecycle_events.py:67` | Builds `position_current` row from position |
| `_canonical_position_surface_available()` | `src/state/db.py:638` | Guard: canonical tables available? |

**`build_settlement_canonical_write()` is fully implemented and battle-tested but never invoked from `log_settlement_event()`. This is the lowest-risk fix.**

---

## Change 1: POSITION_SETTLED u2014 Wire `build_settlement_canonical_write()`

### Root write site
`src/state/db.py:1514` u2014 `log_settlement_event()`  
`src/execution/harvester.py:671` u2014 caller

### Current code (db.py:1544u20131558)
```python
# Legacy position_event write u2014 guarded by schema availability
if _legacy_runtime_position_event_schema_available(conn):
    log_position_event(
        conn,
        "POSITION_SETTLED",
        pos,
        details=_canonical_position_settled_payload(...),
        timestamp=settled_at,
        source="settlement",
    )
```

### Required change (db.py:1544+)
Insert before the legacy write block:
```python
# Canonical write u2014 build_settlement_canonical_write() already exists
if _canonical_position_surface_available(conn):
    try:
        from src.engine.lifecycle_events import build_settlement_canonical_write
        from src.state.ledger import append_many_and_project
        from src.state.db import _query_next_sequence_no  # or compute inline
        seq = _query_next_sequence_no(conn, getattr(pos, 'trade_id', ''))
        phase_before = _canonical_phase_before(conn, getattr(pos, 'trade_id', ''))
        events, projection = build_settlement_canonical_write(
            pos,
            winning_bin=winning_bin,
            won=won,
            outcome=outcome,
            sequence_no=seq,
            phase_before=phase_before,
        )
        append_many_and_project(conn, events, projection)
    except Exception as _e:
        logger.warning("Canonical settlement write failed: %s", _e)
```

### position_current UPDATE result
- `phase`: `'settled'` (via `build_position_current_projection()` u2192 `canonical_phase_for_position()` u2192 `LifecyclePhase.SETTLED`)
- `updated_at`: set to `pos.last_exit_at`
- All position fields refreshed

### Estimated changes
- db.py: ~15 lines added (import block + try/except wrapper + 6 call lines)
- No new functions needed (builder already exists)

---

## Change 2: POSITION_EXIT_RECORDED u2014 New `build_exit_canonical_write()`

### Root write site
`src/state/db.py:1561` u2014 `log_trade_exit()`  
`src/state/portfolio.py:1177u20131179` u2014 caller (inside `close_position()`)

### Current code (db.py:1612u20131626)
```python
log_position_event(
    conn,
    "POSITION_EXIT_RECORDED",
    pos,
    details={"status": status, "exit_price": ..., "pnl": ..., ...},
    ...
)
```

### New function to add in `src/engine/lifecycle_events.py` (after `build_settlement_canonical_write` at ~line 280)
```python
def build_exit_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    phase_before: str,
    source_module: str = "src.state.db",
) -> tuple[list[dict], dict]:
    """Canonical write for economically closed positions (exited without settlement)."""
    projection = build_position_current_projection(position)
    # phase should be economically_closed for exited positions
    occurred_at = _non_empty(
        getattr(position, "last_exit_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "status": "voided" if getattr(position, "state", "") == "voided" else "exited",
            "exit_price": getattr(position, "exit_price", None),
            "pnl": getattr(position, "pnl", None),
            "exit_trigger": getattr(position, "exit_trigger", ""),
            "exit_reason": getattr(position, "exit_reason", ""),
            "admin_exit_reason": getattr(position, "admin_exit_reason", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:exit_recorded:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "EXIT_RECORDED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": projection["phase"],  # economically_closed or settled
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "trade_exit",
        "idempotency_key": f"{getattr(position, 'trade_id')}:exit_recorded:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "payload_json": payload,
    }
    return [event], projection
```

### Required change in db.py `log_trade_exit()` (~line 1561)
Insert before the legacy `log_position_event()` call:
```python
if _canonical_position_surface_available(conn):
    try:
        from src.engine.lifecycle_events import build_exit_canonical_write
        from src.state.ledger import append_many_and_project
        seq = _query_next_sequence_no(conn, getattr(pos, 'trade_id', ''))
        phase_before = _canonical_phase_before(conn, getattr(pos, 'trade_id', ''))
        events, projection = build_exit_canonical_write(
            pos, sequence_no=seq, phase_before=phase_before
        )
        append_many_and_project(conn, events, projection)
    except Exception as _e:
        logger.warning("Canonical exit write failed: %s", _e)
```

### position_current UPDATE result
- `phase`: `'economically_closed'` (for exited positions) or `'settled'` (for voided positions)
- `updated_at`: set to `pos.last_exit_at`

### Estimated changes
- lifecycle_events.py: ~35 lines (new `build_exit_canonical_write()` function)
- db.py: ~12 lines (canonical write block in `log_trade_exit()`)

---

## Change 3: POSITION_LIFECYCLE_UPDATED u2014 Lightweight `updated_at` refresh

### Root write site
`src/state/db.py:1632` u2014 `update_trade_lifecycle()`  
Callers:
1. `src/state/chain_reconciliation.py:291` u2014 chain sync
2. `src/execution/fill_tracker.py:128` u2014 fill verification
3. `src/engine/cycle_runtime.py:502` u2014 monitoring phase

### What this event type represents
Phase may or may not change. Common uses:
- Position verified as filled (pending_entry u2192 active)
- Chain state updated (active u2192 active, just chain_state field changed)
- Monitoring probe recorded

**The critical fix for the CANONICAL_AUTHORITY_UNAVAILABLE cascade is simply refreshing `position_current.updated_at`.** The staleness guard in `query_portfolio_loader_view()` only compares timestamps. If `position_current.updated_at` is kept in sync, the fallback never fires.

### Option A: Minimal fix u2014 `upsert_position_current()` only (no new event)
In `update_trade_lifecycle()` (db.py:1632), after the UPDATE to `trade_decisions`, add:
```python
if _canonical_position_surface_available(conn):
    try:
        from src.engine.lifecycle_events import build_position_current_projection
        from src.state.projection import upsert_position_current
        projection = build_position_current_projection(pos)
        upsert_position_current(conn, projection)
    except Exception as _e:
        logger.warning("Canonical lifecycle projection refresh failed: %s", _e)
```
This keeps `position_current.updated_at` in sync **without** adding a canonical event. Sufficient to fix the CANONICAL_AUTHORITY_UNAVAILABLE issue.

### Option B: Full canonical event (longer-term)
Create `build_lifecycle_canonical_write()` in `lifecycle_events.py` with `event_type="LIFECYCLE_UPDATED"`, `phase_before=current_phase`, `phase_after=new_phase`. Requires determining phase_before from current `position_current` row.

**Recommendation: Start with Option A.** It directly fixes the staleness root cause (updated_at out of sync). Full event log can follow as a separate commit.

### Estimated changes (Option A)
- db.py: ~10 lines added to `update_trade_lifecycle()`
- No new functions needed (`build_position_current_projection` and `upsert_position_current` already exist)

---

## Helper Functions Needed

Two small helpers needed in `db.py` (or can be inlined):

### `_query_next_sequence_no(conn, trade_id) -> int`
```python
def _query_next_sequence_no(conn: sqlite3.Connection, trade_id: str) -> int:
    """Get next sequence number for canonical position events."""
    row = conn.execute(
        "SELECT MAX(sequence_no) FROM position_events WHERE position_id = ?",
        (trade_id,),
    ).fetchone()
    return (row[0] or 0) + 1
```
~5 lines.

### `_canonical_phase_before(conn, trade_id) -> str`
```python
def _canonical_phase_before(conn: sqlite3.Connection, trade_id: str) -> str:
    """Read current phase from position_current as phase_before for next event."""
    row = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ? OR trade_id = ?",
        (trade_id, trade_id),
    ).fetchone()
    return str(row[0]) if row else "active"  # default to active if not found
```
~6 lines.

---

## Dependency Order

```
1. lifecycle_events.py: add build_exit_canonical_write()
   (no dependencies on db changes)

2. db.py: add _query_next_sequence_no(), _canonical_phase_before()
   (no dependencies)

3. db.py: wire canonical write in log_settlement_event() [Change 1]
   (depends on: build_settlement_canonical_write already exists)

4. db.py: wire canonical write in log_trade_exit() [Change 2]
   (depends on: step 1 u2014 build_exit_canonical_write)

5. db.py: add upsert refresh in update_trade_lifecycle() [Change 3, Option A]
   (no dependencies u2014 can be done in any order)
```

Change 5 (Option A) is the highest-priority single fix: it unblocks `CANONICAL_AUTHORITY_UNAVAILABLE` immediately without requiring new event builders.

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Change 1 (POSITION_SETTLED) | Low u2014 builder already written and tested | Wrap in try/except; legacy write still runs |
| Change 2 (POSITION_EXIT_RECORDED) | Medium u2014 new builder | Wrap in try/except; legacy write still runs |
| Change 3 Option A (lifecycle refresh) | Very Low u2014 upsert only, no event | Wrap in try/except; idempotent |

All three changes wrap canonical writes in `try/except` with a WARNING log. Legacy writes continue unchanged. If canonical writes fail, system degrades to current broken state (not worse).

---

## Total Estimated Lines of Code

| File | Change | +Lines |
|------|--------|-------|
| `src/engine/lifecycle_events.py` | `build_exit_canonical_write()` | ~35 |
| `src/state/db.py` | `_query_next_sequence_no()`, `_canonical_phase_before()` | ~12 |
| `src/state/db.py` | Wire Change 1 in `log_settlement_event()` | ~15 |
| `src/state/db.py` | Wire Change 2 in `log_trade_exit()` | ~12 |
| `src/state/db.py` | Wire Change 3 Option A in `update_trade_lifecycle()` | ~10 |
| **Total** | | **~84 lines** |

MVP (Change 3 Option A only): **~10 lines** u2014 fixes CANONICAL_AUTHORITY_UNAVAILABLE immediately.  
Full fix (all 3 changes): **~84 lines** u2014 closes the entire 72-event gap.
