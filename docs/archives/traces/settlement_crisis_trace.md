# Settlement Crisis Trace — 2026-04-07

## Executive Summary

38 trade_decisions marked as "exited" have empty settlement outcome data.
This is NOT 38 missing settlements — it's **1 structural design decision** manifesting as multiple symptoms.

**Root cause**: The `trade_decisions` table was designed as a decision/entry log,
not a full lifecycle table. It has no columns for settlement outcomes (`won`, `exit_price`,
`pnl`, `outcome`). Settlement data flows to 3 other sinks but never back to trade_decisions.

---

## Finding 1: `settlement_semantics_json` is metadata, not outcome data

**File**: `src/contracts/settlement_semantics.py`

The `settlement_semantics_json` column in `trade_decisions` stores the market's
**resolution rules** — written at DECISION time by the evaluator (`src/engine/evaluator.py:880`):

```json
{
  "resolution_source": "WU_KLGA",
  "measurement_unit": "F",
  "precision": 1.0,
  "rounding_rule": "round_half_to_even",
  "finalization_time": "12:00:00Z"
}
```

This describes HOW a market resolves (rounding, units). It does NOT contain:
- `settlement_price` — what the position closed at
- `won` — whether the position's bin matched the winning bin
- `net_pnl` — profit or loss

The column name is misleading. 64 of 66 rows have this metadata populated.
0 rows have outcome data because there is no column for it.

## Finding 2: `trade_decisions` has no settlement outcome columns

**File**: `src/state/db.py` (schema at line ~230)

Full column list shows NO outcome columns:
- No `won` column
- No `exit_price` column  
- No `pnl` column
- No `outcome` column

The only exit-related columns are:
- `exit_reason` TEXT — why the exit was triggered
- `exit_trigger` TEXT
- `settlement_edge_usd` REAL — **mislabeled**: `log_trade_exit()` stores `pos.pnl` here (db.py:1572)

## Finding 3: Settlement outcome flows to 3 sinks, none is trade_decisions

When `_settle_positions()` runs (`harvester.py:566`):

| Sink | Function | What it stores |
|------|----------|----------------|
| chronicle | `log_event(conn, "SETTLEMENT", ...)` | won, pnl, entry_price, outcome, direction |
| position_events_legacy | `log_settlement_event()` → `log_position_event()` | Full settlement payload (won, pnl, exit_price, outcome) |
| settlement_records | `store_settlement_records()` | SettlementRecord with outcome, pnl |
| trade_decisions | **NOTHING** | No UPDATE happens for settlement |

The only `UPDATE trade_decisions` in the codebase is `update_trade_lifecycle()` (db.py:1631)
which updates `status`, `filled_at`, `fill_price`, `chain_state` — no settlement fields.

`log_trade_exit()` (db.py:1560) does an **INSERT** (not UPDATE), creating a new row
with status='exited'. This writes P&L into `settlement_edge_usd` (column mismatch).

## Finding 4: Only 2 of 38 "exited" rows are settlements

| exit_reason category | Count | P&L sum |
|---------------------|-------|---------|
| Model-Market divergence | ~20 | +$1.47 |
| BUY_NO_EDGE/NEAR_EXIT | ~10 | +$1.37 |
| Edge reversed | 1 | +$13.10 |
| MODEL_DIVERGENCE_PANIC | 3 | -$2.57 |
| SETTLEMENT | 2 | -$2.33 |
| **Total** | **38** | **+$11.04** |

The other 36 are early exits. Settlement exits in trade_decisions are rare because
`log_trade_exit()` is called from `_track_exit()` inside `compute_settlement_close()`,
which only runs if the position is found in the portfolio.

## Finding 5: Duplicate chronicle entries

12 trade_ids appear twice in chronicle SETTLEMENT events (harvester ran twice).

| Source | Events | Unique trades | P&L sum |
|--------|--------|--------------|----------|
| chronicle (raw) | 31 | 19 | -$26.72 |
| chronicle (deduped) | 19 | 19 | -$13.03 |
| position_events_legacy | 22 | 19 | -$16.60 |
| trade_decisions (settlement_edge_usd) | 38 | 38 | +$11.04 |

## Finding 6: `outcome_fact` has 0 rows — early return bug

**File**: `src/state/db.py:1524-1527`

```python
def log_settlement_event(conn, pos, *, winning_bin, won, outcome, ...):
    if not _legacy_runtime_position_event_schema_available(conn):
        if _canonical_position_surface_available(conn):
            return  # ← EARLY RETURN before log_outcome_fact()
        _assert_legacy_runtime_position_event_schema(conn)
    # ... log_outcome_fact() is at line 1530, never reached
```

Since the canonical `position_events` table exists and the legacy schema check fails,
the function returns at line 1526 before calling `log_outcome_fact()` at line 1530.
Result: `outcome_fact` table has 0 rows. All outcome data silently discarded.

## Finding 7: The $20 P&L gap

The reported gap ($11.13 vs -$9.67 ≈ $20) comes from comparing incomparable numbers:
- `trade_decisions.settlement_edge_usd` sum = +$11.04 — includes 36 early exits + 2 settlements
- Chronicle SETTLEMENT P&L (deduped) = -$13.03 — only market settlements, no early exits
- These measure different things: one includes early exit gains, the other doesn't

---

## Structural Decisions Count: K=2

### SD-1: trade_decisions is entry-only, not lifecycle
The table was designed to log decisions at entry time. Settlement outcome data
was never given columns here. `log_trade_exit()` does an INSERT with P&L stuffed
into the wrong column (`settlement_edge_usd`). Fix: add proper outcome columns
AND an UPDATE path in the harvester.

### SD-2: `log_settlement_event()` early return skips `outcome_fact`  
The canonical/legacy schema routing causes `outcome_fact` to never be written.
Fix: move `log_outcome_fact()` call before the early-return guard, or into
a separate code path that always executes.

---

## Recommended Fix Priority

1. **P0**: Fix the early return in `log_settlement_event()` so `outcome_fact` gets written
2. **P0**: Add `exit_price`, `pnl`, `outcome`, `won` columns to `trade_decisions`
3. **P0**: Add an UPDATE to `_settle_positions()` that writes settlement outcome to trade_decisions
4. **P1**: Backfill the 38 exited rows from chronicle/position_events data  
5. **P1**: Add idempotency guard to harvester to prevent duplicate chronicle entries
6. **P2**: Rename `settlement_edge_usd` or stop storing P&L in it

---

## Appendix: Cross-Surface Lifecycle Traces

Three trades traced across ALL data surfaces to map gaps per lifecycle phase.

### Trade 1: `f4604ca3-caf` — Full Settlement (NYC, pnl=+$6.58)

| Surface | Data Present? | Content |
|---------|:---:|---|
| trade_decisions | 1 row, STALE | status=`day0_window` (never updated to settled), settlement_edge_usd=0.54 (from prior early exit attempt), NO settlement pnl |
| chronicle | YES | SETTLEMENT event: won=0, pnl=6.58, outcome=1, entry_price=0.635 |
| position_events_legacy | YES (2 rows) | LIFECYCLE_UPDATED + POSITION_SETTLED (won=0, pnl=6.58, exit_price=1.0, outcome=1) |
| position_events (canonical) | EMPTY | 0 rows — canonical dual-write skipped (no prior canonical history) |
| position_current | EMPTY | 0 rows — settled trade removed |
| outcome_fact | EMPTY | 0 rows — early return bug (SD-2) |
| positions-paper.json | In recent_exits | pnl=6.58, exit_reason=SETTLEMENT |
| calibration_pairs | YES | NYC 2026-04-01 pairs exist |

**Key gap**: trade_decisions row frozen at `day0_window` with stale data from a divergence exit.
The settlement outcome (+$6.58) is ONLY in chronicle + position_events_legacy + positions-paper.json.
`won=0` but `outcome=1` and pnl=+$6.58 because this was `buy_no` direction — the bin did NOT win,
so the NO token paid out. The `won` field is ambiguous (bin match vs profitable).

### Trade 2: `db5083ad-78c` — Edge Exit (Chicago, pnl=+$0.32)

| Surface | Data Present? | Content |
|---------|:---:|---|
| trade_decisions | 3 rows, DUPLICATED | Row 273: status=`entered` (entry). Row 281: status=`exited` (1st exit, pnl=0.18). Row 282: status=`exited` (2nd exit, pnl=0.32, 30min later) |
| chronicle | EMPTY | 0 rows — early exits don't write to chronicle |
| position_events_legacy | 4 rows | ORDER_FILLED, ENTRY_RECORDED, EXIT_RECORDED x2 (pnl=0.18, 0.32) |
| position_events (canonical) | EMPTY | 0 rows |
| position_current | EMPTY | 0 rows — exited trade removed |
| outcome_fact | EMPTY | 0 rows |
| positions-paper.json | In recent_exits | pnl=0.32, exit_reason=BUY_NO_EDGE_EXIT |

**Key gap**: `log_trade_exit()` INSERT creates a NEW row each time, so 2 early exits = 2 exited rows.
This means `SUM(settlement_edge_usd)` double-counts this trade ($0.18 + $0.32 = $0.50 instead of $0.32).
Chronicle has nothing because early exits don't log SETTLEMENT events (correct behavior).
But `outcome_fact` is empty (should track all exits, not just settlements).

### Trade 3: `019e683c-7ee` — Still Active (Dallas, day0_window)

| Surface | Data Present? | Content |
|---------|:---:|---|
| trade_decisions | 1 row, STALE | status=`day0_window`, no exit data |
| chronicle | EMPTY | Expected — not yet settled |
| position_events_legacy | 3 rows | ENTRY_RECORDED, ORDER_FILLED, LIFECYCLE_UPDATED (day0_window) |
| position_events (canonical) | 3 rows | POSITION_OPEN_INTENT, ENTRY_ORDER_POSTED, ENTRY_ORDER_FILLED |
| position_current | 1 row | phase=`active` (stale — should be `day0_window`) |
| outcome_fact | EMPTY | Expected — not yet settled |
| positions-paper.json | 1 position | state=`day0_window`, pnl=0.0 |

**Key gap**: `position_current.phase = active` but `positions-paper.json.state = day0_window`.
The canonical surface is behind the legacy surface. `update_trade_lifecycle()` updated
trade_decisions to `day0_window`, but position_current was not updated.

### Cross-Surface Gap Summary

| Gap | Affected Phases | Severity |
|-----|----------------|----------|
| trade_decisions never updated with settlement outcome | Settlement | P0 |
| outcome_fact always empty (early return bug) | Settlement, Exit | P0 |
| position_current.phase stale vs positions-paper.json | Active/Day0 | P1 |
| position_events (canonical) empty for most trades | All | P1 |
| log_trade_exit() INSERT creates duplicate rows per exit | Exit | P1 |
| chronicle has duplicate SETTLEMENT events (harvester re-run) | Settlement | P1 |
| trade_decisions.settlement_edge_usd double-counts multi-exit trades | Exit | P2 |
