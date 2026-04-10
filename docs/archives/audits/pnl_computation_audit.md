# P&L Computation Audit — 2026-04-07

Auditor: pnl-auditor agent  
Scope: positions-paper.json (15 exits), P&L formula, duplicate exits, Atlanta cluster

---

## Issue 1: shares=None, cost_basis_usd=None in exits

### Root Cause: Benign — fallback properties handle it correctly

The `Position` dataclass defaults `shares=0.0` and `cost_basis_usd=0.0`. Older positions entered before the `register_position` fixup (lines 939-942 of `portfolio.py`) never had these populated. In `recent_exits`, they serialize as `None`.

However, **P&L is computed via `effective_shares` and `effective_cost_basis_usd` properties**, not the raw fields:

```python
# portfolio.py:249-258
@property
def effective_shares(self) -> float:
    if self.shares > 0:
        return self.shares
    if self.entry_price > 0:
        return self.size_usd / self.entry_price  # <-- fallback
    return 0.0

@property
def effective_cost_basis_usd(self) -> float:
    return self.cost_basis_usd if self.cost_basis_usd > 0 else self.size_usd  # <-- fallback
```

The P&L formula (`portfolio.py:979-982`):
```python
def _compute_realized_pnl(position, exit_price):
    return round(position.effective_shares * exit_price - position.effective_cost_basis_usd, 2)
```

This is mathematically equivalent to:
```
pnl = (size_usd / entry_price) * exit_price - size_usd
```

For Polymarket YES shares: buy at `entry_price` per share, sell at `exit_price` per share. `shares = size_usd / entry_price`. Revenue = `shares * exit_price`. P&L = revenue - cost. **Formula is correct.**

### Verified with concrete example (Atlanta 82-83F):
- entry=0.02581, exit=0.305, size_usd=3.824
- effective_shares = 3.824 / 0.02581 = 148.17
- pnl = 148.17 * 0.305 - 3.824 = $41.37 (matches stored value)

### Verdict: P&L formula is **correct** despite shares=None.

The `recent_exits` dict stores raw `pos.pnl` which was already computed via the effective properties at exit time. The None in serialized exits is cosmetic — it does not affect the computed P&L.

---

## Issue 2: Duplicate trade_ids in recent_exits

### Root Cause: Race condition — position re-exited before state persisted

**4 trade_ids appear twice:**

| trade_id (prefix) | Exit 1 timestamp | Exit 2 timestamp | Delta |
|---|---|---|---|
| 019e683c-7ee | 09:54:40 | 10:08:19 | ~13 min |
| 2acca60d-5a3 | 09:55:06 | 10:08:10 | ~13 min |
| 96b81a89-a2b | 09:55:33 | 10:07:57 | ~12 min |
| b6ac1c14-c1f | 09:55:47 | 10:07:52 | ~12 min |

All 4 have exit_reason=DAY0_OBSERVATION_REVERSAL with slightly different CI values between the two exits.

**Mechanism:**

1. `compute_economic_close()` (`portfolio.py:985-1009`) sets `pos.state = "economically_closed"` in memory and calls `_track_exit()` which appends to `recent_exits`.
2. The monitor loop (`cycle_runtime.py:420`) checks `pos.state == "economically_closed"` and skips such positions.
3. BUT: `compute_economic_close` does NOT remove the position from `state.positions`. The position stays in the list, marked `economically_closed`.
4. State is persisted to disk only at end of cycle (`cycle_runner.py:266-267`): `save_portfolio(portfolio)`.
5. **If a second cycle loads state from disk before the first cycle's save completes**, the position is still in its pre-exit state. The second cycle triggers a new exit → second `_track_exit` append → duplicate.

**The lifecycle guard (`enter_economically_closed_runtime_state`) requires `pending_exit` phase**, which should prevent double-close. But in paper mode, `_mark_pending_exit(position)` is called immediately before `compute_economic_close` in the same call (`exit_lifecycle.py:211-213`), so the guard passes each time a fresh cycle starts from disk state.

**No dedup check exists in `_track_exit()`** (`portfolio.py:1123`) — it unconditionally appends.

### P&L Impact:

| Metric | Value |
|---|---|
| Total P&L (all 15 exits) | $124.64 |
| Deduplicated P&L (11 unique) | $122.16 |
| Double-counted amount | $2.48 |
| Error rate | 2.0% |

The double-counted amount is small ($2.48) because these were buy_no positions with small edge — but the structural bug could produce larger errors with bigger positions.

### Verdict: **P&L is over-reported by $2.48 due to duplicate exits.**

### Recommended fix:
Add a dedup guard in `_track_exit()`:
```python
def _track_exit(state, pos):
    # Prevent duplicate exit records
    if any(ex["trade_id"] == pos.trade_id for ex in state.recent_exits):
        return
    state.recent_exits.append({...})
```

Alternatively, add a guard at the top of `compute_economic_close`:
```python
if pos.state == "economically_closed":
    return pos  # Already closed, skip re-tracking
```

---

## Issue 3: Atlanta Apr 11 cluster — 7 buy_yes at entry=0.026

### Root Cause: Legitimate — 7 different temperature bins

All 7 trades have **unique market_ids and unique bin_labels**:

| Bin | Entry | Exit | P&L |
|---|---|---|---|
| 76-77F | 0.026 | 0.090 | $3.89 |
| 80-81F | 0.026 | 0.180 | $16.14 |
| 82-83F | 0.026 | 0.305 | $41.37 |
| 84-85F | 0.026 | 0.300 | $36.31 |
| 86-87F | 0.026 | 0.180 | $9.34 |
| 88-89F | 0.026 | 0.175 | $9.03 |
| 90-91F | 0.026 | 0.085 | $3.58 |

These are 7 different Polymarket temperature bins for Atlanta on April 11. Zeus bought YES on multiple bins at the tail price (~2.6 cents each), and the forecast shifted such that several bins rose significantly. This is consistent with a forecast change scenario (e.g., warm front arriving).

The identical entry price (0.025806...) across all 7 bins is suspicious — it suggests they were all at the minimum price or floor. This warrants checking whether 0.0258 is the Polymarket minimum tick or if Zeus has a hardcoded floor.

All 7 exited via EDGE_REVERSAL, meaning the model's posterior moved against the position. The exit prices (8.5-30.5 cents) are plausible for forecast-driven moves 4 days out.

### Verdict: **Legitimate trades, not double-counting.** 7 distinct markets/bins.

The 1082% ROI on the best trade (82-83F) is real but reflects buying at extreme tail prices. Total outlay was ~$26.77 across 7 positions; total return ~$119.66. This is high-risk tail betting that happened to work out due to forecast shift.

---

## Summary

| Issue | Severity | P&L Impact | Trustworthy? |
|---|---|---|---|
| shares=None | Low (cosmetic) | None — formula is correct | Yes |
| Duplicate trade_ids | **Medium (bug)** | -$2.48 over-reported | **No — inflated by 2%** |
| Atlanta cluster | Low (expected) | None — legitimate trades | Yes |

**Overall P&L trustworthiness: $122.16 (not $124.64).** The duplicate exit bug needs a structural fix (dedup guard) to prevent escalation with larger positions.
