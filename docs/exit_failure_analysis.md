# Exit Failure Analysis — center_buy / shoulder_sell Tail Losses

**Investigator:** exit-investigator  
**Date:** 2026-04-07  
**Question:** If the signal was correct but the outcome drifted against us, why didn't EDGE_REVERSAL or Day0 exit triggers fire and sell the position before settlement?

---

## 1. Data Summary

All queries run against `state/zeus.db.trade_decisions`.

### center_buy (16 losses, -$18.00)

| trade_id | price | size_usd | status | exit_trigger | exit_forward_edge |
|----------|-------|----------|--------|--------------|-------------------|
| 255 | 0.020 | $1.20 | exited | EDGE_REVERSAL | -0.0643 |
| 69  | 0.018 | $1.02 | day0_window | NULL | 0.0 |
| 70  | 0.0065 | $1.23 | day0_window | NULL | 0.0 |
| 75  | 0.015 | $1.11 | day0_window | NULL | 0.0 |
| 76  | 0.007 | $1.21 | day0_window | NULL | 0.0 |
| 77  | 0.0035 | $1.05 | day0_window | NULL | 0.0 |
| 78  | 0.0035 | $1.05 | day0_window | NULL | 0.0 |
| 79  | 0.003 | $1.05 | day0_window | NULL | 0.0 |
| 256 | 0.002 | $1.28 | day0_window | NULL | 0.0 |
| 266/267 | 0.002/0.0035 | ~$1.28 | exited | SETTLEMENT | 0.0 |

**Result:** EDGE_REVERSAL fired exactly ONCE (trade 255, pre-day0). All other center_buy losses reached settlement inside day0_window with zero exit trigger.

### shoulder_sell tail losses (buy_yes direction, 4 losses)

| trade_id | price | size_usd | status | exit_trigger |
|----------|-------|----------|--------|--------------|
| 68  | 0.010 | $1.10 | day0_window | NULL |
| 71  | 0.0015 | $1.29 | day0_window | NULL |
| 74  | 0.0035 | $1.25 | day0_window | NULL |
| 80  | 0.003 | $1.05 | day0_window | NULL |

**Note:** 5 other shoulder_sell trades (buy_no direction, pre-day0) DID exit via MODEL_DIVERGENCE_PANIC. The tail buy_yes failures are a separate population.

---

## 2. Why EDGE_REVERSAL Never Fired in day0_window

### The day0 EV gate (`portfolio.py:464-484`)

When `day0_active=True` and `evidence_edge < edge_threshold`, the code takes a special single-cycle path:

```python
if day0_active and evidence_edge < edge_threshold:
    # EV gate: only exit if market bid > model posterior
    if shares * best_bid <= shares * current_p_posterior:
        return ExitDecision(False, ...)   # <-- HOLD
    return ExitDecision(True, "DAY0_OBSERVATION_REVERSAL", ...)  # EXIT
```

The gate reduces to: **exit only if `best_bid > current_p_posterior`**.

### The failure mechanism (stale p_posterior)

The day0 signal refresh (`monitor_refresh.py:148-205`) has five early-return fallback paths that all return `position.p_posterior` unchanged:

1. `obs is None` → return stale p_posterior
2. `obs.observation_time` missing → return stale p_posterior
3. Ensemble fetch fails / invalid → return stale p_posterior
4. `temporal_context is None` → return stale p_posterior
5. `remaining_member_maxes.size == 0` → return stale p_posterior

When ANY of these hit, `current_p_posterior = entry_price ≈ 0.002–0.02`.

### The arithmetic at extreme tails

For center_buy at p_entry=0.02:
- Position enters day0_window (≤6h to settlement)
- Temperature outcome is clearly NOT in the extreme bin → market reprices toward 0
- `best_bid` drops to ~0.005–0.01
- Day0 signal falls back → `current_p_posterior = 0.02` (stale entry value)
- EV gate: `best_bid (0.01) <= current_p_posterior (0.02)` → **HOLD**

The model permanently "believes" the position is worth 2× the market bid. No exit fires. The position rides to settlement. At settlement, NO wins (temperature wasn't in that extreme bin) and the full stake is lost.

### Why Layer 8 (micro-position guard) is NOT the cause

All positions have `size_usd > $1.00`. Layer 8 (`if position.size_usd < 1.0: return None`) does not apply here.

---

## 3. Why the One EDGE_REVERSAL Fired (Trade 255)

Trade 255 exited pre-day0 (before entering day0_window), so it took the standard 2-cycle EDGE_REVERSAL path (not the day0 path):

- `forward_edge = -0.0643`, implied `ci_width ≈ 0.091`
- `evidence_edge = -0.1098` (forward_edge - ci_width/2)
- `edge_threshold = max(-0.1, min(-0.01, -0.091 × 0.3)) = -0.027`
- `-0.1098 < -0.027` → negative for 2 consecutive cycles → EDGE_REVERSAL fires

This worked because:
1. The position was still in `entered`/`holding` state (not yet day0_window)
2. Standard 2-cycle path with no EV gate suppression
3. The signal had correctly updated (non-stale posterior)

---

## 4. Structural Question: Can EDGE_REVERSAL Ever Protect Extreme-Tail YES Positions?

### Config values (from settings)

```
buy_yes_floor: -0.01
buy_yes_ceiling: -0.10
buy_yes_scaling_factor: 0.30
consecutive_confirmations: 2
near_settlement_hours: 4.0
```

### Threshold analysis at p_entry=0.02

`edge_threshold = max(-0.10, min(-0.01, -ci_width × 0.30))`

For a typical entry ci_width ≈ 0.09:
`edge_threshold = -0.027`

For EDGE_REVERSAL to fire (pre-day0): `evidence_edge < -0.027` for 2 consecutive cycles.
- If p_market drops from 0.02 to 0.01: forward_edge ≈ 0.02 - 0.01 = +0.01 (POSITIVE — no exit)
- If p_model has NOT updated, forward_edge is still positive. The signal must go negative.
- EDGE_REVERSAL requires the MODEL to declare the edge negative — not just the market price to drop.

**The entry precondition for EDGE_REVERSAL is that the model itself turns negative.** At extreme tails where the market leads the model (market goes to 0.01 before model updates), EDGE_REVERSAL fires only after model lag.

### In day0_window: the EV gate further suppresses it

Even when the edge IS negative:
- Day0 path fires on single cycle (more aggressive)
- But EV gate `best_bid <= current_p_posterior` blocks it if model is stale
- Stale model = entry posterior = entry price
- At extreme tails, `best_bid` is always below `entry_price` when things go wrong
- **DAY0_OBSERVATION_REVERSAL is structurally impossible when p_posterior is stale**

---

## 5. Day0 Protection — What Actually Happens

The day0 lifecycle (from `cycle_runtime.py:489-508`):

1. When `hours_to_settlement <= 6.0` AND position is in `entered`/`holding` state → transition to `day0_window`
2. In `day0_window`, the monitoring loop STILL runs — exits are NOT suppressed
3. `day0_active=True` is passed to `evaluate_exit()` → activates the single-cycle DAY0_OBSERVATION_REVERSAL path
4. DAY0_OBSERVATION_REVERSAL requires `best_bid > current_p_posterior` (EV gate)
5. SETTLEMENT_IMMINENT fires at `hours_to_settlement < 1.0` regardless

**Conclusion: Day0 does NOT suppress exits — it creates a MORE aggressive single-cycle exit path. But the EV gate inside that path systematically blocks it when the day0 signal falls back to stale p_posterior.**

---

## 6. Root Cause Summary

| Failure Layer | Mechanism | Effect |
|--------------|-----------|--------|
| **Primary: stale p_posterior** | Day0 signal fallbacks (obs unavailable, ENS stale, temporal context missing) return entry p_posterior unchanged | `current_p_posterior = entry_price ≈ 0.02` |
| **Secondary: EV gate design** | DAY0_OBSERVATION_REVERSAL blocked when `best_bid <= current_p_posterior` | Hold decision even when market price has halved |
| **Compound: market leads model** | At extreme tails, market reprices toward 0 faster than model updates | `best_bid < entry_price` always when losing |
| **Result** | Position held to settlement, full stake lost | 100% loss on all day0_window extreme-tail YES positions |

**One-sentence root cause:**  
The DAY0_OBSERVATION_REVERSAL EV gate (`best_bid <= current_p_posterior`) structurally cannot fire when the day0 signal falls back to the stale entry posterior — the market will always be below the entry price on losing positions, so the gate always says HOLD.

---

## 7. What Would Fix It

Three independent interventions, any one of which breaks the failure mode:

1. **EV gate: replace stale posterior with market price as fallback**  
   When `fresh_prob_is_fresh=False`, use `current_market_price` instead of `p_posterior` in the EV gate comparison. Stale model should not veto an exit.

2. **Day0 signal reliability**  
   Track why the 5 fallback paths are hitting. If observation data is unavailable for >1 cycle in day0_window, treat the probability as unknown rather than as the entry value.

3. **Pre-day0 exit window for extreme-tail YES**  
   For buy_yes positions at p_entry < 0.05 (extreme tail), apply the EDGE_REVERSAL check earlier (e.g. 12h before settlement) before entering day0_window — when the standard 2-cycle path is still active and the signal has the best chance of being fresh.

---

## 8. Confidence Assessment

- **Finding 1 (EDGE_REVERSAL fired once, pre-day0):** Confirmed by DB query — trade 255.
- **Finding 2 (day0_window trades held to settlement):** Confirmed — 8/8 center_buy day0_window trades have NULL exit_trigger.
- **Finding 3 (EV gate mechanism):** Confirmed by code at `portfolio.py:464-484`.
- **Finding 4 (stale p_posterior fallback):** Confirmed by `monitor_refresh.py:163-205` — 5 fallback paths return `position.p_posterior` unchanged.
- **Finding 5 (size_usd > $1, Layer 8 not the cause):** Confirmed by DB query — all trades > $1.00.
- **Uncertainty:** Cannot confirm which specific fallback path was triggering without runtime logs. The stale-posterior mechanism is the structurally sound explanation but the specific observation/ENS failure reason is unknown.
