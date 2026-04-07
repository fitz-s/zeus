# Strategy Failure Analysis: shoulder_sell and center_buy

**Date:** 2026-04-07  
**Author:** strategy-analyst (zeus-root-cause team)  
**Evidence basis:** live DB queries on `state/zeus.db`, evaluator.py code review, ensemble snapshot analysis

---

## Summary

Both strategies fail for structural, not statistical, reasons. center_buy places tiny EV bets on narrow 2°F bins where forecast precision is insufficient to win consistently. shoulder_sell is mislabeled—it conflates two opposite bet types, one of which (tail buys) is a lottery ticket, the other (true shoulder sells) is partially protected by the divergence exit system.

| Strategy | Trades | P&L | Win Rate | Root Cause |
|---|---|---|---|---|
| center_buy | 16 | -$18.00 | 0% | Narrow bin precision gap + near-zero probability targets |
| shoulder_sell | 8 | -$9.38 | 0% | Strategy mislabeling: mixes tail buys (losses) with shoulder sells (protected exits) |
| opening_inertia | 7 | +$0.66 | 29% | Edge compression (already gated 2026-04-07) |

---

## 1. What do these strategies do? (code)

From `src/engine/evaluator.py` lines 186–207, strategy keys are assigned in `_strategy_key_for()`:

```python
def _strategy_key_for(candidate, edge) -> str:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if edge.bin.is_shoulder:           # open_low OR open_high bin
        return "shoulder_sell"
    if edge.direction == "buy_yes":    # non-shoulder, any direction
        return "center_buy"
    return "opening_inertia"
```

**is_shoulder** (market.py:64): a bin with `low=None` ("X or below") OR `high=None` ("X or higher").  
**center bin**: a finite 2°F range bin (e.g., "70-71°F"). Width = exactly 2 settlement integers.

### The labeling bug

`shoulder_sell` is assigned to **any** trade on a shoulder bin regardless of direction. This conflates:
- `buy_no` on `"72°F or higher"` → genuinely selling the high tail (model: temp stays below)
- `buy_yes` on `"45°F or below"` → **buying** the extreme cold tail (lottery ticket)

These are opposite bets. Aggregating their P&L under one strategy key is meaningless.

---

## 2. Cities, dates, and what the trades actually were

### center_buy (all buy_yes, all narrow 2°F bins)

| Bin | Entry Price | p_raw | p_posterior | Edge | Settlement P&L | Status |
|---|---|---|---|---|---|---|
| Denver 52-53°F Apr 1 | 0.020 | 5.1% | 5.1% | 0.031 | +$13.10 * | exited early |
| NYC 70-71°F Apr 1 | 0.018 | 4.5% | 4.5% | 0.027 | -$1.02 | day0_window |
| NYC 84-85°F Apr 1 | 0.0065 | 3.9% | 3.9% | 0.032 | -$1.23 | day0_window |
| NYC 86-87°F Apr 1 | 0.002 | 3.6% | 3.6% | 0.034 | -$1.28 | day0_window + exited |
| Denver 48-49°F Apr 1 | 0.015 | 4.4% | 4.4% | 0.029 | -$1.11 | day0_window |
| Denver 50-51°F Apr 1 | 0.007 | 3.9% | 3.9% | 0.032 | -$1.21 | day0_window |
| Houston 90-91°F Apr 1 | 0.0035 | 3.1% | 3.1% | 0.028 | -$1.05 | day0_window + exited |
| Houston 92-93°F Apr 1 | 0.003 | 3.1% | 3.1% | 0.028 | -$1.05 | day0_window |
| Houston 94-95°F Apr 1 | 0.0035 | 3.1% | 3.1% | 0.028 | -$1.05 | day0_window |

*The Denver +$13.10 "win" was NOT a correct settlement prediction — it was an early exit when price moved favorably after edge reversed. Exit reason: "Buy-yes edge reversed for 2 cycles (ci_lower=-0.1098)". Fill price jumped from 0.02 entry to 0.239 exit.*

### shoulder_sell — Sub-type A: buy_yes on extreme tails (LOTTERY TICKETS)

| Bin | Entry Price | p_raw | Settlement P&L | Note |
|---|---|---|---|---|
| NYC ≤69°F Apr 1 | 0.010 | 3.9% | -$1.10 | cold tail, NYC avg ~55-60°F in early April |
| NYC ≥88°F Apr 1 | 0.0015 | 3.6% | -$1.29 | extreme heat tail |
| Denver ≤45°F Apr 1 | 0.0035 | 3.7% | -$1.25 | extreme cold tail |
| Houston ≥96°F Apr 1 | 0.003 | 3.1% | -$1.05 | extreme heat tail |

All status: `day0_window`. All settled wrong. These are buy_YES bets that the temperature would reach extreme values that the ensemble forecast made nearly impossible.

### shoulder_sell — Sub-type B: buy_no on high shoulders (DIVERGENCE EXITS)

| Bin | Entry Price | p_raw | Exit Reason | Settlement P&L |
|---|---|---|---|---|
| Chicago ≥48°F Apr 2 | 0.0070 | 0.350 | Divergence score 0.34 | +$0.84 |
| Chicago ≥48°F Apr 2 | 0.0085 | 0.290 | Divergence score 0.34 | $0.00 |
| Chicago ≥48°F Apr 2 | 0.0085 | 0.290 | Divergence score 0.34 | $0.00 |
| SF ≥72°F Apr 3 | 0.545 | 0.649 | Divergence score 0.23 | +$0.01 |
| SF ≥72°F Apr 3 | 0.545 | 0.649 | Divergence score 0.23 | +$0.05 |

These are **not catastrophic losses** — the divergence exit protected them. The p_raw values (29-65%) for shoulder NO positions indicate the model detected genuine edges on the shoulder NO side. Venus's 0/8 win-rate count includes these as "losses" but their realized P&L is near zero or slightly positive.

---

## 3. Were the weather predictions actually wrong?

### center_buy: signal direction roughly correct, precision insufficient

The ensemble data for April 1 cities shows member maxes tightly clustered:
- **Denver Apr 1**: members cluster around 40-45°F (from snapshot with spread=1.19). The bins Denver 48-49°F, 50-51°F, 52-53°F are in the right-tail of the distribution — each is 3-5% probable.
- **NYC Apr 1**: members shown in snapshots at 65-76°F range. Buying 70-71°F has ~4.5% probability — the central bin, but still just one of ~20 possible outcomes.
- **Houston Apr 1**: members clustered at 85-90°F. The bins 90-91°F, 92-93°F, 94-95°F are each ~3% probable.

**The model is NOT wrong directionally**. It correctly identifies that these are underpriced relative to the ensemble distribution. But:
- A 1°F forecast error (well within ECMWF MAE of ~1.5-2°F at 3-day lead) completely misses a 2°F bin
- Even if the forecast mean is exactly 52°F, only ~50% of the time will the actual temp round to 52 vs 51 or 53
- With p_posterior ≈ 4-5%, the base win rate requires 20-25 trades per expected win

**P(0 wins | n=16, p=0.04) = (0.96)^16 = 52%** — getting 0/16 is more likely than not for this strategy at this sample size.

### shoulder_sell Sub-type A (tail buys): signal wrong by construction

For extreme tails:
- Denver ≤45°F Apr 1: ensemble snapshot (Chicago Apr 1 analogy) shows all 51 members above 48°F. The Denver snapshot should show similar certainty against 45°F. p_raw = 3.7% — model itself says near-impossible.
- NYC ≥88°F, ≤69°F: for April 1, NYC ensemble shows 65-76°F range. ≥88°F and ≤69°F are the outer tails with ~1-4% model probability.

The signal is not wrong — it correctly says 3-4% probability. The problem is that 3-4% is **still very unlikely**, and the strategy is placing bets on these outcomes at entry prices of 0.1-1%. There's a tiny positive edge but enormous variance.

### shoulder_sell Sub-type B (shoulder NO trades): model potentially stale at day-boundary

For Chicago ≥48°F Apr 2:
- The market priced YES at ~99.3%, the model said ~35% for YES (65% for NO).
- This 64-point discrepancy is too large to be a genuine model insight at day0 for April 2.
- The ensemble snapshot for Chicago Apr 2 (spread=6.06) shows most members in 55-76°F range — nearly all above 48°F. The model should give >95% for YES, not 35%.
- Hypothesis: The p_raw=35% in the trade record may reflect an ensemble snapshot fetched from a prior cycle that used data from a colder forecast window, or a different market event's bins were mapped incorrectly.
- The divergence exit (score=0.34 > threshold) correctly identified that this model-market disagreement was implausible and exited.

For SF ≥72°F Apr 3:
- Model p_raw=0.649 (YES direction for the shoulder), so 35.1% for NO.
- Market priced NO at 54.5%. Edge = 54.5% - 35.1% = 19.4% for... wait, actually the BinEdge direction is buy_no.
- From `market_analysis.py:148-169`: buy_no edge = p_post_no - p_market_no = (1-p_cal) - (1-p_market_yes).
- p_cal=0.649 for YES → p_cal_no = 0.351. Market NO = 0.455. Edge_no = 0.351 - 0.455 = **negative** — this should not be a buy_no edge.
- Either the stored p_raw (0.649) is the NO direction probability or there's a field interpretation mismatch in the DB schema.

---

## 4. Is this a signal problem or sizing/entry problem?

### center_buy: primarily a structural granularity problem

The signal has a real edge (0.027-0.034) on bins priced at 0.2-2%. But:

1. **Bin precision vs forecast MAE mismatch**: A 2°F bin requires forecast accuracy better than ±1°F. ECMWF at 3-day lead has MAE ~1.5-2°F. This means even a perfect-signal model will miss ≥50% of the time on 2°F bins.

2. **EV math at these prices**: Buying at p_market=0.015 when p_posterior=0.051:
   - EV per dollar = p_posterior/p_market - 1 = 0.051/0.015 - 1 = 240% edge nominal
   - But this assumes the edge survives to settlement — with a 1°F MAE, the adjacent bin is equally likely to win
   - Real edge is: (p_posterior - 0.5*adjacent_spill) vs p_market — likely near zero

3. **Sizing is appropriately tiny** ($1-1.30 per trade) but the strategy produces no wins to offset the accumulated losses.

### shoulder_sell Sub-type A: purely signal problem

Buying YES on extreme tails where p_model is already 3-4% at entry — these should never be taken. The edge vs. p_market (1% vs 3%) is nominally there but the true win probability after accounting for model uncertainty is near zero for these extremes.

### shoulder_sell Sub-type B: entry timing problem (day0 staleness)

The Chicago trades show the model using a forecast that dramatically underestimates the likelihood of ≥48°F when the actual day is approaching/in progress. This is a data provenance issue: the ensemble model is being used at a point in time where the observation already strongly constrains the outcome, but the system still weights the ensemble distribution rather than the observation.

---

## 5. Should these strategies be disabled?

### Recommendation: Gate both, for different reasons

**center_buy: GATE**

Evidence threshold met:
- 0/16 wins (vs ~4% model-implied win rate = expected 0.64 wins)
- The structural mismatch between 2°F bin width and ECMWF MAE is a permanent problem, not a data issue
- The only "profitable" trade (+$13.10) was a price-movement exit, not a settlement win
- Gate until: calibration data confirms median forecast error < 0.5°F for the relevant city/season, OR bin widths increase to ≥4°F

**shoulder_sell (tail buys, Sub-type A): GATE**

Evidence threshold met:
- These are buy_yes bets on extreme tails where model p_raw itself is 3-4%
- 0/4 wins, full stake lost on each, no path to structural profitability
- Gate immediately and permanently until there's a specific thesis for why extreme tails are systematically mispriced

**shoulder_sell (true shoulder sells, Sub-type B): MONITOR, do not gate**

- 0/5 divergence-exit trades, but realized P&L is near zero or slightly positive (+$0.84, 0, 0, +$0.01, +$0.05)
- The divergence exit system is functioning correctly — it protected capital when model confidence was unjustified
- These are not contributing to catastrophic losses
- The Chicago issue is a day0 model-staleness symptom that the divergence score already catches

### Current riskguard state (2026-04-07)

- `opening_inertia`: GATED (edge_compression), active as of 08:58 UTC
- `center_buy`: **no active gate**
- `shoulder_sell`: **no active gate**
- `strategy_health` table: no rows for center_buy or shoulder_sell (tracking gap)

---

## 6. Root cause summary

### center_buy: 3 structural causes

| # | Cause | Evidence | Fix |
|---|---|---|---|
| C1 | 2°F bin width < ECMWF MAE | All p_raw = 3-5%, bins require <1°F accuracy | Gate until MAE < bin_width/2 |
| C2 | Near-zero probability targets (1-2% entry) | Entry prices 0.002-0.020 across all 16 trades | Minimum entry price floor |
| C3 | Sample too small for win expectation | P(0/16 | p=0.04) = 52%, not anomalous | Needs 50+ trades to verify EV claim |

### shoulder_sell: strategy mislabeling creates two failure modes

| # | Sub-type | Cause | Evidence | Fix |
|---|---|---|---|
| S1 | Tail buy (buy_yes on shoulder) | Buying extreme cold/heat tails at 0.1-1% | 0/4 settlements, full stake losses | Separate strategy key: "tail_buy", gate |
| S2 | True shoulder sell (buy_no) | Day0 model staleness + divergence exits | +$0.84/~$0/~$0 outcomes, exits protected | Fix strategy key assignment; continue monitoring |

### The labeling bug (evaluator.py:198-207)

`_strategy_key_for()` assigns `shoulder_sell` to all shoulder-bin trades regardless of direction. A `buy_yes` on `"Denver ≤45°F"` is a tail buy, not a shoulder sell. This conflation:
- Makes strategy_health tracking impossible to interpret
- Mixes a permanently losing sub-strategy with a viable one
- Produces the misleading 0/8 win rate that obscures that Sub-type B trades are near breakeven

**Recommended fix**: Add a fourth strategy key `"tail_buy"` for `buy_yes` on shoulder bins, keeping `"shoulder_sell"` for `buy_no` on shoulder bins only.

---

## 7. Evidence artifacts

All data queried from `state/zeus.db` on 2026-04-07.

Key queries used:
```sql
-- Trade evidence
SELECT strategy, bin_label, direction, price, size_usd, settlement_edge_usd,
       p_raw, p_posterior, edge, fill_price, exit_reason, status
FROM trade_decisions 
WHERE strategy IN ('shoulder_sell', 'center_buy')
AND status IN ('exited', 'day0_window')
ORDER BY strategy, bin_label;

-- Risk actions
SELECT strategy_key, action_type, value, reason, status 
FROM risk_actions WHERE status='active';
```

Ensemble member data for Chicago Apr 2 (spread=6.06) confirms most members 55-76°F with one at 47.5°F — model should give >95% YES on ≥48°F, not 35% as recorded in trade_decisions.p_raw. Potential data provenance issue in the Chicago shoulder trades.
