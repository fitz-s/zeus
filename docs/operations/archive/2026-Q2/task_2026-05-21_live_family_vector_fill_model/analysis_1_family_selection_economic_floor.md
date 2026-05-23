# Analysis 1: Family Selection And Economic Floor Review

Captured from the user's initial review packet for this task. This file is the
durable source reference for the first bug list and repair packet.

## Executive Verdict

`main` had repaired several earlier blockers, including full entry blocker
authority and same-cycle Stage-A family dedup. The remaining live-money fracture
was that Zeus still sized each mutually-exclusive weather bin independently
before family selection existed as an authority object. Stage-A then selected a
survivor after Kelly/risk/min-order/projected-exposure effects had already been
computed.

The observed symptom was live orders at 1-3 cent prices. The review traced that
to this chain:

```text
FDR-selected mutually-exclusive hypotheses
-> independent scalar Kelly per bin
-> per-bin venue minimum uses min_order_shares * price
-> low-price bins have tiny minimum notional
-> passive-maker path lacks fill/adverse-selection authority
-> Stage-A dedup ranks by sized output rather than family utility
-> live submits low-price passive orders
```

## Findings

### A1-P0-1: Family Dedup Runs After Independent Kelly

Files named in review:

- `src/engine/evaluator.py`
- `src/engine/cycle_runtime.py`
- `src/strategy/family_exclusive_dedup.py`

Problem: evaluator sizes all FDR-selected family siblings independently, mutates
projected exposure while doing so, then runtime dedup drops siblings. Dropped
sibling edges can affect heat, throttle, min-order admission, and survivor
selection even though they will never execute.

Required repair: select one family action or explicit family portfolio before
scalar Kelly sizing. Keep runtime dedup as a second-line guard.

Required tests:

- `test_family_preselection_happens_before_projected_exposure_mutation`
- `test_multiple_fdr_edges_only_one_enters_scalar_kelly_in_live`
- `test_dropped_family_edges_do_not_affect_current_heat_or_risk_throttle`

### A1-P0-2: Low-Price Passive-Maker Orders Lack Economic Floor

Files named in review:

- `src/engine/evaluator.py`
- `src/engine/cycle_runtime.py`
- `src/contracts/execution_intent.py`
- `src/contracts/semantic_types.py`

Problem: venue minimum notional was used as the practical live minimum. At low
prices this made penny contracts easier to pass than mid-price contracts.
Passive-maker submit safety did not require expected fill probability, queue
position, or adverse-selection authority.

Required repair: separate venue minimum from strategy/economic floors. Reject
low-quality live entries when submitted notional, expected profit, low-price
tail authorization, or passive fill authority is missing.

Required tests:

- `test_low_price_order_below_strategy_notional_rejected_even_if_venue_min_passes`
- `test_one_cent_passive_order_requires_tail_strategy_and_fill_model`
- `test_venue_min_order_does_not_override_strategy_economic_floor`

### A1-P0-3: Family Survivor Objective Is Sized Output, Not Utility

File named in review:

- `src/strategy/family_exclusive_dedup.py`

Problem: Stage-A ranked family survivors by `size_usd`, which is a downstream
heuristic output, not expected profit, expected log growth, fill-adjusted EV, or
family payoff utility.

Required repair: compute family selection score from executable cost, posterior,
submitted notional, fill probability, fees, and adverse-selection penalty. Stage
B should optimize a payoff-vector portfolio.

Required tests:

- `test_family_dedup_chooses_highest_net_ev_not_largest_size_usd`
- `test_family_dedup_does_not_select_one_cent_tail_when_mid_bin_has_higher_utility`

### A1-P0-4: Passive Maker Kelly Uses Fake Microstructure Context

File named in review:

- `src/engine/cycle_runtime.py`

Problem: passive maker sizing used `EffectiveKellyContext(spread_usd=0,
depth_at_best_ask=100, order_type="GTC")`, treating passive quotes as safe
without modeling non-fill probability, adverse selection, queue depth, quote
age, stale quote risk, or opportunity cost.

Required repair: introduce `PassiveMakerExecutionContext`, size passive orders
on fill-adjusted EV, and block live passive entry when the fill model is missing
unless an explicit maker-experiment/shadow mode is active.

### A1-P1-1: Family Exposure Reads Portfolio Projection Only

File named in review:

- `src/strategy/family_exclusive_dedup.py`

Problem: family exposure was derived from `portfolio.positions`, not from
command/order/trade facts, position lots, unresolved side effects, or open order
truth. Projection lag could allow same-family reentry.

Required repair: read blocking same-family exposure from trade DB command,
order, trade, position-current, position-lot, and unresolved reconcile truth.

### A1-P1-2: Family Dedup Rejections Missing From Canonical no_trade_events

Problem: Stage-A family dedup used string rejection reasons, while
`cycle_runtime` persisted no-trade rows only when `rejection_reason_enum` was
set.

Required repair: add schema-backed
`NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP` or a controlled raw-reason bridge.

### A1-P1-3: MarketAnalysisVNext Is Sidecar Telemetry

File named in review:

- `src/analysis/market_analysis_vnext.py`

Problem: VNext microstructure computed useful fields but did not control live
selection.

Required repair: either wire it into family-level selection or explicitly mark
it telemetry-only until Stage B.

### A1-P1-4: Center-Buy Ultra-Low Guard Is Too Narrow

Problem: ultra-low entry block applied only to center-buy buy-YES orders.

Required repair: move minimum entry price and tail authorization to strategy
policy.

## Release Gate From Analysis 1

Normal live entry remains unsafe until:

1. Family selection happens before Kelly sizing.
2. Strategy economic floor is separate from venue minimum.
3. Low-price passive orders are shadow-only unless tail-authorized.
4. Passive maker orders require fill probability and adverse-selection model.
5. Family dedup is persisted to canonical no-trade telemetry.
6. Family exposure reads command/order/fill truth, not only portfolio projection.

