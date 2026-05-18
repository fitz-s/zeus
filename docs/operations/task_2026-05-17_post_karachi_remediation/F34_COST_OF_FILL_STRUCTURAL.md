# F34 Cost-of-Fill Optimizer

**Created:** 2026-05-17  
**Authority basis:** Investigator verdict 2026-05-17 — F34 STRUCTURAL_POSSIBLE  
**Status:** OPT-IN, default OFF (`ZEUS_TAKER_CROSSING_ENABLED=0`)

---

## The Math

Zeus historically placed passive maker limit orders to avoid taker fees.
The 89% non-fill rate observed in the Karachi audit means that avoiding the
fee incurs an opportunity cost most of the time.

The optimizer computes:

```
taker_fee_per_share  = best_ask_price × (taker_fee_bps / 10_000)
spread_cost_per_share = best_ask_price − best_bid_price
cost_of_crossing      = (taker_fee_per_share + spread_cost_per_share) × best_ask_size

opportunity_cost      = expected_pnl_if_filled × non_fill_probability
```

**Decision rule:** cross the spread when `opportunity_cost > cost_of_crossing`.

Two safety guards apply before the crossing decision:

1. **Thin-book guard** — if `best_ask_size < min_economical_size`, always stay
   passive regardless of opportunity cost (result: `PASSIVE_THIN_BOOK`).
2. **Flag gate** — the entire optimizer is dead-code unless
   `ZEUS_TAKER_CROSSING_ENABLED=1` is set.

## Integration point

`src/engine/cycle_runtime.py` — immediately after `edge_aware_taker_enabled`
is assigned from `allow_taker_upgrade` intent context (~line 803).

When the flag is ON, `_crossing_decision()` (defined in `evaluator.py`)
overrides `edge_aware_taker_enabled`. The existing downstream logic that
converts `edge_aware_taker_enabled=True` into a FOK/depth-sweep is unchanged.

## Why opt-in via flag

The optimizer changes trading alpha (crossing vs not-crossing affects fill
rate, realized PnL, and fee exposure). Shipping it default-OFF means:

- Existing passive behavior is byte-for-byte identical when flag is absent.
- Operator can validate economic impact via backtest replay before enabling.
- No risk to live positions (including current day0_window positions).

## Karachi safety

The Karachi position is in `day0_window` state — Zeus is not entering new
positions on that market. `ZEUS_TAKER_CROSSING_ENABLED` defaults to `"0"`,
so the optimizer code path is never reached during Karachi day0 monitoring.

## Backtest validation protocol (required before flipping flag)

Before setting `ZEUS_TAKER_CROSSING_ENABLED=1` in the live plist:

1. Run full backtest replay over a representative 30-day window covering
   markets where the 89% non-fill rate was observed.
2. Compare: realized PnL, fill rate, taker fee paid, net alpha.
3. Tune `f34_non_fill_probability` (default 0.5) and
   `f34_min_economical_size` (default 5.0 USD) per intent context if needed.
4. Confirm that `F34_CROSSING_DECISION CROSS` log lines correlate with
   subsequent fills in replay output.
5. Only flip the flag after a net-positive result on ≥2 independent
   backtest windows.

## Parameters (passed via `final_intent_context`)

| Key | Default | Meaning |
|-----|---------|---------|
| `f34_non_fill_probability` | `0.5` | Estimated probability a passive order does NOT fill. Tune per market regime. |
| `f34_min_economical_size` | `5.0` | Minimum ask-side liquidity (USD) to consider crossing. |

The conservative defaults (`non_fill_probability=0.5`) ensure that
flag-ON without explicit tuning is still more conservative than the 89%
empirical rate — erring toward fewer crosses until operator validates.
