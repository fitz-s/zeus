# Wave34: Replay Execution-Cost Continuity

Created: 2026-05-08
Authority basis: object-meaning invariance mainline; topology route `edit replay fidelity`; Polymarket fee formula and replay non-promotion rules.

## Invariant

Replay must not size a hypothetical trade with one execution-cost object and then compute shares/PnL with a different price object. If replay applies a fee-adjusted execution cost at the Kelly boundary, downstream replay shares and hypothetical PnL must use that same fee-adjusted cost basis. Bare decision-time market probability may remain a reported market price, but it must not silently become the all-in entry cost for PnL.

## Scope

Patch only replay computation and relationship tests:

- `src/engine/replay.py`
- `tests/test_backtest_settlement_value_outcome.py`

No schema change, live/prod DB mutation, replay promotion, backfill, relabeling, or report publication.

## Failure

`_replay_one_settlement()` sizes with `_size_at_execution_price_boundary(..., fee_rate=_default_weather_fee_rate())`, but later computes `shares = size_usd / edge.entry_price` and `replay_pnl = shares * exit_price - size_usd`. That converts a fee-adjusted Kelly cost object back into bare market probability for share/PnL math.

## Repair Plan

1. Compute and carry the replay fee-adjusted execution price next to `size_usd`.
2. Add a validation marker so replay decisions expose the cost-basis transform.
3. Compute replay shares/PnL from fee-adjusted execution cost, not bare `edge.entry_price`.
4. Add a relationship test proving the old bare-price PnL differs and the repaired path uses the fee-adjusted object.

## Verification

- Focused replay settlement-value relationship test
  `tests/test_backtest_settlement_value_outcome.py::test_replay_pnl_uses_fee_adjusted_execution_cost_for_share_count`: PASS.
- Adjacent replay tests:
  `test_trade_history_audit_uses_position_metric_for_settlement_match`,
  `test_closed_bin_scores_from_settlement_value`,
  `test_open_ended_bins_score_from_settlement_value`,
  `test_celsius_and_fahrenheit_units_preserved`: PASS.
- Replay CLI market-price-linkage tests:
  `test_replay_without_market_price_linkage_cannot_generate_pnl`,
  `test_replay_market_price_linkage_limitations_distinguish_full_partial_none`: PASS.
- `tests/test_execution_price.py`: PASS, 25 passed / 1 xfailed.
- `py_compile` for replay source/test: PASS.
- Planning-lock, map-maintenance, and diff check: PASS.
- Whole `tests/test_backtest_settlement_value_outcome.py`: pre-existing noise,
  3 WU sweep fixture failures outside the Wave34 path; focused admitted slice
  passes.
- Critic review after repair: APPROVE. No Critical or Important findings for
  the Wave34 slice; critic noted default-system Python lacks `sklearn`, while
  the project venv verification passed.

## Residual

Replay still uses `_default_weather_fee_rate()` as diagnostic replay fee authority instead of a historical per-market token fee snapshot. This wave prevents intra-replay object drift; it does not claim historical execution economics are venue-confirmed.
