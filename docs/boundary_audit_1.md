# Boundary Audit 1: Cross-Module Data Flow Verification

Auditor: boundary-auditor-1 (team zeus-root-cause)
Date: 2026-04-07

## Methodology

For each boundary: read both sides, trace what data is built on the producer side, what fields the consumer reads, and identify gaps where the producer outputs something the consumer never verifies or vice versa.

---

## Boundary 1: evaluator → executor

### Data expected to cross
- `EdgeDecision.edge.direction` — buy_yes or buy_no
- `EdgeDecision.tokens` — {token_id, no_token_id, market_id} for the winning bin
- `EdgeDecision.size_usd` — Kelly-sized target, computed with fee-adjusted price
- `EdgeDecision.edge_context` — EdgeContext carrying p_posterior, p_market, etc.
- `EdgeDecision.edge.vwmp` — market VWMP for paper fill simulation
- `ExecutionPrice` (fee-adjusted) — computed in evaluator for Kelly sizing

### Data that actually crosses

**Direction:** Correct. `edge.direction` flows:
- evaluator → EdgeDecision.edge.direction
- executor.create_execution_intent() → ExecutionIntent.direction = Direction(edge.direction)
- executor: order_token = token_id if edge.direction == "buy_yes" else no_token_id

**Token resolution:** Correct. Both yes and no token IDs passed to executor as named args; executor resolves by direction. No double-inversion.

**Size:** Correct. `decision.size_usd` → `ExecutionIntent.target_size_usd`.

**Execution price / fee:** GAP.

In evaluator.py (lines 793–804):
```python
raw_price = edge.entry_price  # p_market[i]
fee = polymarket_fee(raw_price)
fee_adjusted_price = raw_price + fee
exec_price = ExecutionPrice(value=fee_adjusted_price, ...)
exec_price.assert_kelly_safe()
size = kelly_size(edge.p_posterior, exec_price.value, ...)  # uses fee-adjusted
```

`exec_price` is used to size the trade but is NOT stored on EdgeDecision and NOT passed to executor. In executor `_paper_fill`:
```python
fill_price = edge_vwmp   # raw VWMP, no fee
shares = intent.target_size_usd / fill_price
```

Result: paper fill_price = raw VWMP (no fee). cost_basis = size_usd (fee-adjusted Kelly). Recorded position has fill_price underestimating true cost, which slightly overstates paper P&L.

### Gap summary
- **exec_price (fee) does not cross the boundary.** Kelly sizing uses fee-adjusted price; paper fill_price records raw VWMP. P&L math is internally inconsistent: cost basis is fee-inclusive but fill price is not.
- No direction/token gap.

---

## Boundary 2: executor → portfolio

### Data expected to cross
From OrderResult → Position:
- fill_price → entry_price
- shares
- direction
- token_id, no_token_id, market_id
- size_usd (Kelly target)

### Data that actually crosses

`materialize_position()` in cycle_runtime.py (lines 191–243):

```python
entry_price = result.fill_price or result.submitted_price or decision.edge.entry_price
shares = result.shares or (decision.size_usd / entry_price if entry_price > 0 else 0.0)
```

Priority chain: fill_price (paper) → submitted_price (live limit, pre-fill) → entry_price (fallback).

**direction:** `decision.edge.direction` → `Position.direction`. Correct.

**token_id / no_token_id:** `decision.tokens["token_id"]` and `decision.tokens["no_token_id"]` both written to Position. Correct.

**size_usd:** `decision.size_usd` (Kelly target) → `Position.size_usd` AND `Position.cost_basis_usd`. For paper mode these are equal (fill at VWMP ≈ target). For live mode, actual fill cost can differ but is not reconciled until fill confirmation.

**p_posterior:** `decision.edge.p_posterior` → `Position.p_posterior`. Correct.

### Gap summary
- **Live pending order gap (minor/expected):** status="pending" orders have fill_price=None, so entry_price falls back to submitted_price (limit price). Actual fill price reconciled later by fill_tracker. No structural corruption.
- **cost_basis ≠ actual fill cost for live orders.** cost_basis_usd is set to Kelly size_usd at entry, not actual fill cost. If the limit order fills at a different price than the limit (slippage), cost_basis diverges from reality. There is no post-fill cost_basis correction in materialize_position.
- No direction mismatch gap.

---

## Boundary 3: monitor_refresh → exit_triggers

### Data expected to cross
- Fresh p_posterior (recomputed with current ENS/Day0 signal)
- `is_fresh` flag so exit decision knows whether to trust the probability

### Data that actually crosses

**Flow:** `refresh_position()` → returns `EdgeContext` → `_build_exit_context()` → `ExitContext` → `pos.evaluate_exit(exit_context)` → exit triggers

**Fresh p_posterior:**
`refresh_position()` returns `EdgeContext(p_posterior=current_p_posterior, ...)` where `current_p_posterior` is either the freshly computed value (if ENS fetch succeeded) or the stale `pos.p_posterior` (if any exception caught at line 455).

`_build_exit_context()` reads: `fresh_prob = float(edge_ctx.p_posterior)` — the return value, correct.

**is_fresh flag path:**

Inside `refresh_position()` (lines 368–372):
```python
pos.last_monitor_prob_is_fresh = False  # initialized
```

Then after successful recompute:
```python
pos.last_monitor_prob_is_fresh = True if prob_refresh_is_fresh is None else bool(prob_refresh_is_fresh)
```

where `prob_refresh_is_fresh = getattr(refresh_pos, "_monitor_probability_is_fresh", None)` and `refresh_pos` had that attribute pre-cleared to None via setattr.

`_build_exit_context()` reads: `fresh_prob_is_fresh = bool(getattr(pos, "last_monitor_prob_is_fresh", False))`

This is a **split-read**: `fresh_prob` comes from the EdgeContext return value; `fresh_prob_is_fresh` comes from a side-effect on `pos`. Both are set within the same try block in `refresh_position`, so they're consistent when refresh succeeds.

**When fresh_prob_is_fresh defaults to False:**

1. ENS fetch fails (`_refresh_ens_member_counting` returns early with `_set_monitor_probability_fresh(position, False)`)
2. Day0 observation unavailable (same pattern)
3. Any exception caught at line 455 of monitor_refresh.py — entire try block skipped, `pos.last_monitor_prob_is_fresh` stays False from initialization
4. Unknown city: function raises ValueError before setting anything

**What exit_triggers does with stale fresh_prob:**

In `pos.evaluate_exit()` → `ExitContext.missing_authority_fields()` (portfolio.py line 96):
```python
elif not self.fresh_prob_is_fresh:
    missing.append("fresh_prob_is_fresh")
```

Day0 exception (portfolio.py lines 279–281):
```python
if exit_context.day0_active and missing == ["fresh_prob_is_fresh"]:
    missing = []  # waive authority check
    applied.append("day0_stale_prob_authority_waived")
```

For day0 stale signal, execution continues into `_buy_yes_exit` with `fresh_prob_is_fresh=False`, which triggers stale prob substitution:
```python
if fresh_prob_is_fresh:
    effective_prob = current_p_posterior
else:
    effective_prob = min(current_p_posterior, best_bid * 1.1)
```

**For non-day0 stale signal:** `missing = ["fresh_prob_is_fresh"]` → exit returns INCOMPLETE → position is silently held, no exit decision made.

### Gap summary

1. **day0 stale-signal path IS implemented** but uses probability substitution (`min(stale_p, best_bid * 1.1)`) that depends on `best_bid` being available. If `best_bid` is also None in paper mode, the EV gate skips and the exit can fire on a stale signal.

2. **False-fresh signal possible:** `setattr(refresh_pos, attr, None)` pre-clears the freshness flag. If `recompute_native_probability` succeeds but the internal registry function does NOT call `_set_monitor_probability_fresh` (e.g., entry_method not in registry), `getattr` returns `None` and the code evaluates `True if None else bool(None)` = `True` — marking probability fresh when it may not be.

3. **Non-day0 stale silent hold:** When ENS fetch fails for a non-day0 position, exit evaluation returns INCOMPLETE and the position is held without any forced-exit or alert. The position waits until the next cycle for a fresh signal. There is no maximum-staleness timeout.

---

## Boundary 4: harvester → portfolio

### Data expected to cross
- Settlement outcome removes position from portfolio.positions (positions.json)
- Settlement P&L recorded in trade_decisions DB

### Data that actually crosses

**positions.json:** `compute_settlement_close()` calls `state.positions.pop(index)` — position removed from in-memory list. `pos.state = enter_settled_runtime_state(...)`, `pos.exit_price`, `pos.pnl` set. Then `save_portfolio(portfolio)` commits to disk. **This path works correctly.**

**trade_decisions DB (SD-1 fix, lines 691–703 of harvester.py):**
```python
conn.execute(
    """UPDATE trade_decisions
       SET settlement_edge_usd = ?,
           exit_reason = COALESCE(exit_reason, 'SETTLEMENT'),
           status = CASE WHEN status = 'entered' THEN 'day0_window' ELSE status END
       WHERE runtime_trade_id = ?""",
    (round(pnl, 4), rtid),
)
```

### Gap summary

1. **SD-1 status CASE bug:** The status update sets `'entered'` → `'day0_window'` instead of `'entered'` → `'settled'`. Positions in state="entered" at settlement time will have trade_decisions.status = 'day0_window' instead of 'settled'. This is a logic error in the fix.

2. **Missing exit_price and settled_at in trade_decisions:** The UPDATE sets `settlement_edge_usd` and `exit_reason` but does NOT write `exit_price` or a settlement timestamp to trade_decisions. If a downstream query tries to find when/what price a trade settled at from trade_decisions, it won't find it.

3. **settlement_records path (SettlementRecord → store_settlement_records):** Records are built from `closed` (the settled Position object) and written to the decision_chain. This path correctly carries `pnl`, `p_posterior`, `outcome`, `edge_source`, `direction`. No gap here.

4. **paper_mode parameter defaults to True** in `_settle_positions()` signature but the call site in `run_harvester()` does not pass `paper_mode`, so live mode never redeems winning USDC on-chain (the `if exit_price > 0 and not paper_mode and pos.condition_id:` block is never entered in a live run called from `run_harvester`). The `paper_mode` default needs to come from config.

---

## Cross-cutting observations

1. **Fee-adjusted price never survives past evaluator.** The `ExecutionPrice` wrapper (with polymarket_fee baked in) is constructed and asserted in evaluator but dropped before EdgeDecision is returned. Executor, portfolio, and P&L math all use raw VWMP. Net result: paper positions overestimate fill quality.

2. **Freshness flags are side-effects on mutable Position, not part of the data contract.** `last_monitor_prob_is_fresh`, `last_monitor_market_price_is_fresh` are `setattr`-based attributes set during refresh. If refresh_position is ever refactored to not mutate the Position, these flags would silently disappear and all exit decisions would fail INCOMPLETE.

3. **Day0 stale-prob authority waiver is the intended fix for the day0 stale-signal bug.** The mechanism exists and is wired correctly. The residual risk is the false-fresh signal case (#2 in Boundary 3 gaps) where `prob_refresh_is_fresh` is `None` after an interrupted refresh, causing the position to believe its probability is fresh when it isn't.
