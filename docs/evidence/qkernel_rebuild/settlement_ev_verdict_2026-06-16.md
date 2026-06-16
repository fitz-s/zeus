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

- Settled VERIFIED families in window: **370**
- Strict condition_id→bin_label joins (no ambiguity): **719**
- Dropped for ambiguous join (multiple bin_labels per condition_id): **0**
- With decision-time snapshot (executable_snapshot_id resolved): **719**
- Fallback to latest-in-window (no executable_snapshot_id): **0**
- Settled families with at least one joined condition_id + book: **275**
- Spine-evaluated (usable spine + book condition): **275**
- Spine NO-TRADED (no leg passed edge_lcb>0 ∧ point_ev>0): **61**
- **Spine-SELECTED graded trades (n): 108**
- Drop/skip reasons: grade:no_priced_leg=106, no_book_for_settled_family=95

## Overall Settlement-Realized After-Cost EV

- **n graded trades**: **108**
- **mean after-cost EV per share**: **+0.0180**
- **bootstrap 95% CI (5000 resamples)**: **[-0.0530, +0.0854]**
- median EV: -0.0010; win-rate: 0.472; mean all-in cost: 0.4543
- decision-time snapshot coverage: 108/108 graded trades (100% from decision-time snapshot)
- **sign: INDETERMINATE (CI spans 0)**

## By Side

| side | n | mean EV | 95% CI | win-rate |
|---|---|---|---|---|
| buy_yes | 38 | -0.0107 | [-0.0439, +0.0446] | 0.026 |
| buy_no | 70 | +0.0335 | [-0.0717, +0.1317] | 0.714 |

## By Class (modal / ring / tail)

**modal** = spine's favorite (max-q) bin; **ring** = adjacent bounded bin (not modal); **tail** = shoulder bin (X or below / X or above).

| class | n | mean EV | 95% CI | win-rate | sign |
|---|---|---|---|---|---|
| modal | 22 | -0.0462 | [-0.2392, +0.1300] | 0.545 | 0-span |
| ring | 83 | +0.0364 | [-0.0414, +0.1103] | 0.470 | 0-span |
| tail | 3 | -0.0224 | [-0.0410, -0.0105] | 0.000 | NEG |

## By Market Class (neg-risk buy_no vs other)

| class | n | mean EV | 95% CI | win-rate | sign |
|---|---|---|---|---|---|
| neg_risk_buy_no | 70 | +0.0335 | [-0.0717, +0.1317] | 0.714 | 0-span |
| neg_risk_buy_yes | 38 | -0.0107 | [-0.0439, +0.0446] | 0.026 | 0-span |
| non_neg_risk | 0 | n/a | n/a | n/a | n/a |

## By Metric

| metric | n | mean EV | 95% CI | sign |
|---|---|---|---|---|
| high | 102 | +0.0299 | [-0.0437, +0.0999] | 0-span |
| low | 6 | -0.1853 | [-0.4445, +0.0670] | 0-span |

## By Snapshot Source

| source | n | mean EV | 95% CI | sign |
|---|---|---|---|---|
| decision_time_snapshot | 108 | +0.0180 | [-0.0530, +0.0854] | 0-span |

## Verdict

**VERDICT: INDETERMINATE** — mean after-cost EV +0.0180/share, 95% CI [-0.0530, +0.0854] SPANS 0, n=108: not statistically distinguishable from zero at this sample size.

- Best class: **ring** (n=83, mean EV +0.0364, CI [-0.0414, +0.1103], 0-spanning).
- Worst class: **modal** (n=22, mean EV -0.0462, CI [-0.2392, +0.1300], 0-spanning).

### Operator bar

Settlement-graded positive after-cost EV with 95% CI lower bound > 0 (not merely positive mean). The spine is ARM-approved for live trading only if `VERDICT: SPINE_PROVEN_POSITIVE_AFTER_COST`.

