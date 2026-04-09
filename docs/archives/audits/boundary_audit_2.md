# Boundary Audit 2: Boundaries 5–8

**Auditor:** boundary-auditor-2  
**Date:** 2026-04-07  
**Evidence:** live DB queries against zeus.db, risk_state-paper.db, logs/zeus-paper.err, positions.json

---

## Boundary 5: riskguard → status_summary

### Expected data flow
`riskguard.tick()` writes `risk_state` row to `risk_state-paper.db` with realized_pnl, total_pnl, risk_level, strategy_settlement_summary.  
`status_summary.write_status()` reads the latest row from that DB, populates `portfolio.*` and `risk.details.*` sections.

### Actual data flow (evidence)

```
risk_state-paper.db (latest row, checked_at=2026-04-07T09:23:50Z):
  realized_pnl:            -13.03
  total_pnl:               -13.03
  effective_bankroll:       136.97
  portfolio_position_count: 0      ← riskguard fallback
  portfolio_truth_source:  working_state_fallback
  level:                   RED

status_summary-paper.json (generated from same DB read):
  portfolio.realized_pnl:      -13.03  ✓ MATCH
  portfolio.total_pnl:         -13.03  ✓ MATCH
  portfolio.effective_bankroll: 136.97 ✓ MATCH
  portfolio.open_positions:     24     ← from position_current (different source)
  risk.level:                  RED    ✓ MATCH
  risk_details.portfolio_position_count: 0  (riskguard view)

strategy_settlement_summary (both sources):
  opening_inertia: count=7, pnl=+0.66, accuracy=0.7143  ✓ MATCH
  shoulder_sell:   count=5, pnl=-5.94, accuracy=0.0     ✓ MATCH
  center_buy:      count=10, pnl=-11.32, accuracy=0.0    ✓ MATCH
```

### Gap
**SPLIT POSITION COUNT.** `portfolio.open_positions = 24` (from `query_position_current_status_view()`) while `risk_details.portfolio_position_count = 0` (from riskguard's fallback portfolio view). status_summary reads position count from position_current directly, but reads realized_pnl and effective_bankroll from risk_state. These two sources present contradictory portfolio views to the same consumer.

All other fields (P&L, level, strategy breakdown) match correctly. The status_summary passes through riskguard values faithfully — the discrepancy is structural: status_summary assembles two different portfolio views into one document without flagging the inconsistency.

---

## Boundary 6: portfolio → riskguard

### Expected data flow
Both cycle_runner and riskguard should read the same portfolio truth. riskguard uses `_load_riskguard_portfolio_truth()` which prefers `query_portfolio_loader_view()` (reads `position_current`). cycle_runner uses `load_portfolio()` (reads positions.json).

### Actual data flow (evidence)

```
position_current (zeus.db):
  total rows:    24
  phase=active:  24
  
position_events_legacy staleness check:
  stale (legacy.max_timestamp > position_current.updated_at):  11 of 24
  fresh (position_current is authoritative):                   13 of 24

  Stale example:
    trade=e6f0d01d-2a3
    position_current.updated_at = 2026-04-06T18:39:26
    legacy.max_timestamp        = 2026-04-06T18:50:29  (+11 min)
    latest legacy event_type    = POSITION_LIFECYCLE_UPDATED

  Legacy events on stale positions:
    ORDER_FILLED:               11
    POSITION_ENTRY_RECORDED:    11
    POSITION_EXIT_RECORDED:      5  ← EXITS not propagated to position_current
    POSITION_LIFECYCLE_UPDATED: 12

query_portfolio_loader_view() result:
  status: CANONICAL_AUTHORITY_UNAVAILABLE
  stale_trade_ids: 11 positions
  → falls through to load_portfolio()

positions.json:
  positions:    0  ← EMPTY
  recent_exits: 0  ← EMPTY

Riskguard portfolio (after fallback + chronicle backfill):
  position_count:  0  (from empty positions.json)
  realized_pnl:    -13.03  (backfilled from chronicle)

Cycle_runner portfolio (from load_portfolio(), no backfill):
  position_count:  0  (from empty positions.json)
  realized_pnl:    0  (no backfill, recent_exits empty)
```

### Gap
**CANONICAL AUTHORITY BROKEN — 11 of 24 positions stale.**  
The staleness guard in `query_portfolio_loader_view()` detects that 11 positions have `position_events_legacy` events (including 5 POSITION_EXIT_RECORDED) that are newer than `position_current.updated_at`. This correctly flags those positions as untrustworthy — but the consequence is that the ENTIRE position_current table is rejected, not just the 11 stale rows.

Both cycle_runner and riskguard fall back to `positions.json`, which is empty (0 positions, 0 exits). The trading engine is operating blind:
- cycle_runner sees 0 positions → does not monitor/exit any of the 24 open positions
- riskguard sees 0 positions but backfills from chronicle → reports realized P&L correctly, but daily/weekly loss calculations use 0 open exposure

**Root cause of staleness:** 5 positions have `POSITION_EXIT_RECORDED` in legacy but position_current was NOT updated. The new canonical write system handles entry events (position_events has POSITION_OPEN_INTENT, ENTRY_ORDER_POSTED, ENTRY_ORDER_FILLED), but settlement/exit events are still written only to position_events_legacy and are not propagating to position_current.

---

## Boundary 7: all P&L sources → single truth

### Expected data flow
All 5 P&L sources should agree on -13.03 for 19 settled paper trades.

### Actual data flow (evidence)

```
Source 1: chronicle.SETTLEMENT (details_json.pnl)
  Settled trades: 19
  Total P&L:      -13.03  ← AUTHORITATIVE
  Trade ID format: UUID position IDs ('454db425-76d', ...)

Source 2: trade_decisions.settlement_edge_usd
  Rows with settlement_edge_usd set: 122  (ALL paper trades)
  Total:           -3.98
  Trade ID format: INTEGER sequential (67, 68, 69, ...)
  ← DOES NOT MATCH (different ID namespace, different semantic)
  NOTE: settlement_edge_usd is populated at ENTRY time for all trades,
        not at settlement. This column represents expected edge,
        not realized P&L. JOIN to chronicle returns 0 rows because
        the ID formats are incompatible.

Source 3: outcome_fact.pnl
  Rows: 19
  Total P&L: -13.03  ✓ MATCH
  Uses UUID position_ids — same namespace as chronicle

Source 4: positions.json recent_exits.pnl
  Exits: 0  ✗ EMPTY
  Total: 0.00  ← INCORRECT (positions.json never populated)

Source 5: risk_state.details_json.realized_pnl
  Value: -13.03  ✓ MATCH (backfilled from chronicle in fallback path)
```

### Gap
**TWO GAPS:**

1. **trade_decisions.settlement_edge_usd is NOT a P&L source.** It uses integer IDs incompatible with chronicle/outcome_fact UUID position IDs, and it is set for all 122 rows (all entries), not just 19 settled trades. It represents pre-entry expected edge, not post-settlement realized P&L. Anyone reading this column expecting settled P&L will get wrong data.

2. **positions.json recent_exits is empty.** Source 4 is completely broken as a P&L record. Settlement/exit events are not being written back to positions.json. cycle_runner reads `load_portfolio()` from positions.json and sees `realized_pnl = 0`, diverging from all other sources that show `-13.03`.

Sources 1, 3, 5 agree: -13.03 for 19 settled trades.

---

## Boundary 8: cycle_runner → canonical write path

### Expected data flow
cycle_runner writes position entries AND exits to `position_events` (canonical) AND `position_current` (projection). No `CRITICAL: Canonical entry dual-write FAILED` messages expected.

### Actual data flow (evidence)

```
position_events (zeus.db) totals:
  POSITION_OPEN_INTENT: 24
  ENTRY_ORDER_POSTED:   24
  ENTRY_ORDER_FILLED:   24
  Total rows:           72
  Latest event: 2026-04-07T08:22:51

  Settlement/exit events in position_events: 0  ← NONE

position_current:
  24 rows, all phase=active
  Updated at: 2026-04-07T08:22:51 (matches latest position_events)

logs/zeus-paper.err:
  'CRITICAL: Canonical entry dual-write FAILED': 0 occurrences
  'CANONICAL_AUTHORITY_UNAVAILABLE': 32 occurrences
    First: 2026-04-06T14:44:52
    Last:  2026-04-07T04:24:53

position_events_legacy for stale positions:
  POSITION_EXIT_RECORDED:   5
  POSITION_LIFECYCLE_UPDATED: 12
  These are NOT present in position_events (new canonical table)
```

### Gap
**ENTRY WRITES WORK. EXIT/SETTLEMENT WRITES ARE MISSING FROM CANONICAL PATH.**

Entry writes are functioning correctly: 24 positions each have POSITION_OPEN_INTENT → ENTRY_ORDER_POSTED → ENTRY_ORDER_FILLED in position_events, and position_current is up-to-date for all 24. No CRITICAL dual-write failures.

However, the canonical write path does NOT handle exit/settlement events. When positions are settled or lifecycle-updated (monitoring, chain sync), those events go to `position_events_legacy` only. `position_current` is not updated for these events, creating the staleness that triggers CANONICAL_AUTHORITY_UNAVAILABLE.

Result: the canonical system is half-complete. Entries are canonical; exits/lifecycle are still legacy-only. This is the structural root of the CANONICAL_AUTHORITY_UNAVAILABLE cascade across boundaries 5, 6, and 7.

---

## Summary Table

| Boundary | Expected | Actual | Status |
|----------|----------|--------|--------|
| 5: riskguard→status_summary | P&L fields match | P&L matches; open_positions split (24 vs 0) | **PARTIAL GAP** |
| 6: portfolio→riskguard | Same portfolio truth | Both see empty positions.json; cycle_runner realized_pnl=0, riskguard=-13.03 | **BROKEN** |
| 7: P&L sources agree | All 5 sources agree | Sources 1/3/5 agree (-13.03); S2 incompatible (wrong ID/concept); S4 empty | **2 GAPS** |
| 8: canonical write path | Entries + exits → position_events+position_current | Entries canonical ✓; exits/lifecycle legacy-only ✗ → 11 stale | **PARTIAL** |

## Root Cause Chain

```
B8: exit events not written to canonical position_events
    ↓
B6: position_current becomes stale (legacy newer) → CANONICAL_AUTHORITY_UNAVAILABLE
    ↓ (both cycle_runner + riskguard fall back to positions.json)
B6: positions.json is empty → both see 0 positions
    ↓
B5: status_summary shows split: 24 open (from position_current direct read) vs 0 (from riskguard)
B7: positions.json recent_exits = 0 → cycle_runner sees realized_pnl=0 (wrong)
```

All four boundaries trace back to one gap: **exit/settlement/lifecycle events are not written to the new canonical `position_events` table or used to update `position_current`**.
