# 10 — Source Map

## Uploaded file

- `probability_execution_split_spec.md`
  - Status: critic-approved implementation sequencing spec.
  - It explicitly does not authorize live deploy, production DB mutation, config flips, schema migration, source-routing changes, or strategy promotion.
  - It defines the split between settlement probability, market prior, executable quote/cost, FDR, executor, monitor/exit, persistence, reporting.

## Zeus repo surfaces checked

- `AGENTS.md`
  - Zeus is a live quantitative trading engine.
  - Money path currently lists contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning.
  - Topology doctor is required before source edits.

- `docs/operations/current_state.md`
  - Current phase on `plan-pre5`: G1 engineering hardened; external evidence blocked / live no-go.
  - Live placement blocked by readiness/cutover/heartbeat/collateral/snapshot gates.

- `docs/operations/known_gaps.md`
  - Current audit DBs had zero executable_market_snapshots, venue_commands, venue_order_facts, venue_trade_facts.
  - Calibration tables are not live-alpha complete.
  - Entry intent does not carry executable snapshot facts.
  - No production executable snapshot producer/refresher was found.
  - V2 path uses compatibility envelope not acceptable for certified live-money evidence.
  - Monitoring/exit has partial-fill and semantic issues.

- `src/strategy/market_fusion.py`
  - Current `compute_posterior(p_cal, p_market, alpha)` directly accepts market vector.
  - Current VWMP/comment path treats quote-like values as edge inputs.

- `src/contracts/execution_price.py`
  - Existing contract already warns implied probability is not execution cost, but origin/cost-basis lineage is insufficient.

- `src/engine/cycle_runtime.py`
  - Current `_reprice_decision_from_executable_snapshot` mutates selected decision edge/size after selection.

- `src/execution/executor.py`
  - Current execution docs describe dynamic limit behavior and use of posterior/native side price context.

## Polymarket documentation checked

- Orderbook docs:
  - Token-level orderbook with bids, asks, timestamp, min_order_size, tick_size, neg_risk, hash.
  - Buy price is best ask; sell price is best bid.
  - Midpoint is displayed implied probability, not executable cost.
  - Estimate fill price walks the orderbook to estimate slippage.

- Orders overview:
  - All Polymarket orders are limit orders.
  - Market orders are marketable limit orders.
  - GTC/GTD rest on book; FOK/FAK execute immediately against liquidity.
  - Post-only rests only and rejects if would cross; post-only cannot combine with FOK/FAK.
  - Tick size mismatch causes rejection.
  - Neg-risk markets require `negRisk: true`.

- Fees docs:
  - Fees are match-time protocol facts.
  - `feesEnabled` and `getClobMarketInfo(conditionID)` govern per-market fee parameters.
  - Fee formula: `fee = C × feeRate × p × (1 - p)`.
  - Makers are not charged fees; only takers pay fees.

- Negative-risk docs:
  - A No share in one market can convert into Yes shares in other outcomes through Neg Risk Adapter.
  - Augmented negative-risk markets can include placeholders/Other outcomes and require special handling.
