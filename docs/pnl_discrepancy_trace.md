# P&L Discrepancy Trace

**Investigated:** 2026-04-07  
**Agent:** pnl-tracer (zeus-root-cause team)  
**Question:** chronicle SETTLEMENT total = -$26.72 vs status_summary realized_pnl = reported -$9.67 (now 0.0). Why the gap?

---

## Ground Truth from DB

```
Chronicle SETTLEMENT rows:  31 total
Unique trade_ids:            19
Total PnL (raw, all rows):  -$26.72   ← includes 12 duplicate events
Total PnL (deduped, last):  -$13.03   ← true unique-trade total

outcome_fact rows:           0         ← SD-2 fix only affects future
strategy_health.realized_pnl_30d: 0.0 (reads from outcome_fact)
risk_state.db realized_pnl: 0.0
status_summary.realized_pnl: 0.0
```

### Chronicle duplicate breakdown
12 of 19 trades have exactly 2 SETTLEMENT events — each settlement logged twice.  
Single-occurrence trades (7): f904a495 (-$13.07), b21e81ef (-$1.25), adad4dea (+$1.54),  
  845fbd22 (+$1.68), 9ad96ae9 (+$2.36), 59d3ec84 (+$2.82), f4604ca3 (+$6.58)

---

## Task 1: status_summary.realized_pnl Computation Chain

```
status_summary.portfolio.realized_pnl
  ← risk_details.get("realized_pnl")                    # status_summary.py:222
  ← risk_state.db details_json["realized_pnl"]           # written by riskguard.tick()
  ← round(portfolio.realized_pnl, 2)                     # riskguard.py:465
  ← PortfolioState.realized_pnl property                 # portfolio.py:639-640
  ← _realized_pnl_value(state, exclude_admin=True)       # portfolio.py:1162
  ← sum(ex["pnl"] for ex in state.recent_exits)          # portfolio.py:1165
```

### The structural bug: riskguard always passes recent_exits=[]

In `riskguard.py:_load_riskguard_portfolio_truth()`, when the DB loader succeeds:

```python
portfolio = PortfolioState(
    positions=positions,
    ...  
    recent_exits=[],    # ← HARDCODED EMPTY — riskguard.py:88
    ...
)
```

Result: `portfolio.realized_pnl` is always 0.0 when DB loader path is taken.

When DB loader fails (current state: `CANONICAL_AUTHORITY_UNAVAILABLE`):
- Falls back to `load_portfolio()` which reads `positions.json`
- `positions.json.recent_exits = []` (confirmed: 0 entries)
- Still yields realized_pnl = 0.0

**current risk_state.db (as of 2026-04-07T08:55:53Z):**
```
realized_pnl:          0.0
portfolio_truth_source: CANONICAL_AUTHORITY_UNAVAILABLE  
portfolio_fallback_active: True
portfolio_position_count: 0
```

The -$9.67 the team previously observed likely came from an earlier period when  
`positions.json` still had recent_exits populated. Currently 0.0.

### Fallback path: strategy_summary
If `risk_details.get("realized_pnl")` returns **None** (not 0.0), status_summary falls back to:
```python
sum(bucket.get("realized_pnl", 0.0) for bucket in strategy_summary.values())
```
But `strategy_summary.realized_pnl` reads from `strategy_health.realized_pnl_30d`  
which reads from `outcome_fact` — also 0.0.

**Both paths return 0.0 because outcome_fact is empty.**

---

## Task 2: Why positions.json != chronicle

`positions.json.recent_exits` holds settlements recorded by the main loop at time of exit.  
chronicle holds events appended by `log_settlement_event()` in db.py.

These are **two separate stores**. They can diverge when:
1. Settlements are processed but not written to positions.json (or positions.json is reset)
2. Chronicle events are written but positions.json `recent_exits` is pruned/overwritten
3. Riskguard uses portfolio from positions.json but reads chronicle separately for metrics

**Per-trade PnL match (chronicle deduped vs positions.json recent_exits):**  
- positions.json has 0 SETTLEMENT recent_exits → no match possible  
- The 19 chronicle settlements were never reflected in current positions.json

**Deduped total = -$13.03** is the closest to true realized P&L from chronicle.  
**-$26.72** double-counts 12 trades (2 SETTLEMENT events each).

---

## Task 3: outcome_fact = 0 rows

### SD-2 fix verification
The fix IS in the running code at `db.py:1524-1529`:

```python
# SD-2 fix: log_outcome_fact() MUST run regardless of schema routing.
# Previously, early return at line 1526 skipped outcome_fact entirely,
# resulting in 0 rows in outcome_fact table. (settlement_crisis_trace.md Finding 6)
log_outcome_fact(
    conn,
    position_id=getattr(pos, "trade_id", ""),
    ...
)
```

### Why still 0 rows despite fix being present

**The fix only affects future settlements.**  
All 19 historical settlements were logged to chronicle BEFORE the fix was applied.  
They were never written to outcome_fact. They need a backfill.

Backfill query needed:
```sql
INSERT OR IGNORE INTO outcome_fact (position_id, strategy_key, pnl, outcome, settled_at, ...)
SELECT 
    json_extract(details_json, '$.trade_id') as position_id,
    json_extract(details_json, '$.strategy') as strategy_key,
    json_extract(details_json, '$.pnl') as pnl,
    json_extract(details_json, '$.outcome') as outcome,
    timestamp as settled_at
FROM chronicle 
WHERE event_type = 'SETTLEMENT'
  AND json_extract(details_json, '$.trade_id') IS NOT NULL
GROUP BY json_extract(details_json, '$.trade_id');  -- dedup
```

---

## Root Cause Summary

| Source | Realized P&L | Why |
|--------|-------------|-----|
| chronicle (raw) | -$26.72 | 12 duplicate SETTLEMENT events inflate total |
| chronicle (deduped) | -$13.03 | True unique-trade total from chronicle |
| outcome_fact | $0.00 | 0 rows — SD-2 fix not retroactive |
| strategy_health | $0.00 | Reads outcome_fact |
| risk_state.db | $0.00 | portfolio.recent_exits=[] in riskguard |
| status_summary | $0.00 | Reads risk_state.db |

### Three independent accounting failures

1. **Chronicle duplicate events** — 12 trades each have 2 SETTLEMENT rows → raw total inflated by 2×  
2. **outcome_fact empty** — SD-2 fix not backfilled; strategy_health/status_summary show 0 P&L  
3. **riskguard hardcodes recent_exits=[]** — `riskguard.py:88` never feeds settled trade P&L into risk metrics  

### What -$9.67 was
Likely from an earlier riskguard tick when `positions.json` still had recent_exits entries.  
As positions.json was cleared/reset, realized_pnl went to 0.0. The figure was never authoritative.  

---

## Recommended Fixes

1. **Backfill outcome_fact** from chronicle (deduped by trade_id) — unblocks strategy_health P&L
2. **Fix chronicle duplicate writes** — find call sites that write SETTLEMENT twice per trade
3. **Fix riskguard.py:88** — load recent_exits from DB (settlement records) instead of hardcoding []
4. **Verify positions.json recent_exits lifecycle** — understand when/why it gets cleared
