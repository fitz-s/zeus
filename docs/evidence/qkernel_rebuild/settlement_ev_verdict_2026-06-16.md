# Q-Kernel Spine — Definitive Settlement-Graded After-Cost EV Verdict

Created: 2026-06-16. `scripts/qkernel_settlement_ev_replay.py`.
Read-only on live DBs. No venue calls. No daemon restart.

**Grading window: 2026-06-09 to 2026-06-15** (inclusive; post-spine-enable).

## Join Methodology

1. **Settlement truth**: `zeus-forecasts.db.settlement_outcomes` WHERE `authority='VERIFIED'` AND `target_date` IN [2026-06-09, 2026-06-15].
2. **Bin topology**: `zeus-world.db.no_trade_regret_events` (condition_id → bin_label, city, target_date, metric). **--strict-condition-bin-join**: any condition_id with multiple distinct bin_labels is DROPPED (never guessed); reported as `n_ambiguous_join`. The `market_events` table is empty in the live DBs (confirmed: 0 rows); `no_trade_regret_events` is the only available condition_id→bin join for this window.
3. **Decision-time cost**: `no_trade_regret_events.executable_snapshot_id` → `zeus_trades.db.executable_market_snapshots.snapshot_id`. This is the EXACT snapshot Zeus priced from at decision time, not a retroactive latest-in-window snapshot. The sibling leg (opposite outcome_label) is found by the same condition_id within ±3 seconds of the anchor snapshot's `captured_at`. **--strict-fillability**: any leg with absent or invalid `orderbook_top_ask` is DROPPED. Condition_ids with no `executable_snapshot_id` fall back to latest snapshot in window (counted separately as `n_fallback_latest`).
4. **Spine reconstruction**: VERBATIM from `qkernel_arm_replay.py` (CURRENT_REUSABLE 2026-06-16) — same fresh members at decision cycle = target−1d, same σ-floor, same grid Omega, same joint q + coherent band (300 draws, α=0.05). **--no-day0**: decision cycle is always target−1d (no same-day grading). **--no-synthetic / --no-arb / --no-conversion**: single-leg DIRECT routes only.
5. **Spine gate**: `edge_lcb > 0 AND point_ev > 0` (the live `edge_lower_bound` function over the coherent band). argmax `point_ev` picks the selected leg. A no-trade family contributes ZERO legs (not a 0-EV entry).
6. **Realized payoff**: `buy_yes` wins if the market bin's integer set contains `round_single(settlement_value)` per `SettlementSemantics.for_city` (HK: oracle_truncate; others: wmo_half_up). `buy_no` wins if it does NOT.
7. **After-cost EV**: `realized_payoff − (ask + ask × fee_rate_fraction)` (taker ask + taker fee, all-in cost).

## Coverage

- Settled VERIFIED families in window: **376**
- Strict condition_id→bin_label joins (no ambiguity): **719**
- Dropped for ambiguous join (multiple bin_labels per condition_id): **0**
- With decision-time snapshot (executable_snapshot_id resolved): **719**
- Fallback to latest-in-window (no executable_snapshot_id): **0**
- Settled families with at least one joined condition_id + book: **280**
- Spine-evaluated (usable spine + book condition): **280**
- Spine NO-TRADED (no leg passed edge_lcb>0 ∧ point_ev>0): **67**
- **Spine-SELECTED graded trades (n): 104**
- Drop/skip reasons: grade:no_priced_leg=109, no_book_for_settled_family=96

## Overall Settlement-Realized After-Cost EV

- **n graded trades**: **104**
- **mean after-cost EV per share**: **+0.0297**
- **bootstrap 95% CI (5000 resamples)**: **[-0.0450, +0.1034]**
- median EV: +0.2283; win-rate: 0.510; mean all-in cost: 0.4799
- decision-time snapshot coverage: 104/104 graded trades (100% from decision-time snapshot)
- **sign: INDETERMINATE (CI spans 0)**

## By Side

| side | n | mean EV | 95% CI | win-rate |
|---|---|---|---|---|
| buy_yes | 32 | -0.0004 | [-0.0391, +0.0659] | 0.031 |
| buy_no | 72 | +0.0430 | [-0.0609, +0.1387] | 0.722 |

## By Class (modal / ring / tail)

**modal** = spine's favorite (max-q) bin; **ring** = adjacent bounded bin (not modal); **tail** = shoulder bin (X or below / X or above).

| class | n | mean EV | 95% CI | win-rate | sign |
|---|---|---|---|---|---|
| modal | 32 | +0.0523 | [-0.0981, +0.1858] | 0.688 | 0-span |
| ring | 67 | +0.0098 | [-0.0778, +0.0928] | 0.448 | 0-span |
| tail | 5 | +0.1511 | [-0.0546, +0.5393] | 0.200 | 0-span |

## By Market Class (neg-risk buy_no vs other)

| class | n | mean EV | 95% CI | win-rate | sign |
|---|---|---|---|---|---|
| neg_risk_buy_no | 72 | +0.0430 | [-0.0609, +0.1387] | 0.722 | 0-span |
| neg_risk_buy_yes | 32 | -0.0004 | [-0.0391, +0.0659] | 0.031 | 0-span |
| non_neg_risk | 0 | n/a | n/a | n/a | n/a |

## By Metric

| metric | n | mean EV | 95% CI | sign |
|---|---|---|---|---|
| high | 98 | +0.0458 | [-0.0310, +0.1175] | 0-span |
| low | 6 | -0.2329 | [-0.4445, -0.0249] | NEG |

## By Snapshot Source

| source | n | mean EV | 95% CI | sign |
|---|---|---|---|---|
| decision_time_snapshot | 104 | +0.0297 | [-0.0450, +0.1034] | 0-span |

## Verdict

**VERDICT: INDETERMINATE** — mean after-cost EV +0.0297/share, 95% CI [-0.0450, +0.1034] SPANS 0, n=104: not statistically distinguishable from zero at this sample size.

- Best class: **tail** (n=5, mean EV +0.1511, CI [-0.0546, +0.5393], 0-spanning).
- Worst class: **ring** (n=67, mean EV +0.0098, CI [-0.0778, +0.0928], 0-spanning).

### Operator bar

Settlement-graded positive after-cost EV with 95% CI lower bound > 0 (not merely positive mean). The spine is ARM-approved for live trading only if `VERDICT: SPINE_PROVEN_POSITIVE_AFTER_COST`.

