# Market-cost executable seam

This file records only the current executable price contract. Earlier staged
market-cost experiments were removed because they were not active on the live
route and created a second, dormant decision regime.

## Current law

1. `MarketAnalysis` computes YES and NO edge against the corresponding current
   executable market-price vector. There is one construction path.
2. `BinEdge.entry_price` is an `ExecutionPrice` created at the edge-scan seam;
   live code must not invent price provenance at the Kelly boundary.
3. `_size_at_execution_price_boundary` applies the configured taker fee when
   the typed price is not already fee-adjusted, calls `assert_kelly_safe()`,
   applies the current `EffectiveKellyContext` haircut, and then calls Kelly.
4. A caller with authoritative executable depth may pass
   `max_executable_shares`; the boundary caps notional to that proven depth.
5. Missing effective context on a live path fails closed. Replay-only callers
   may explicitly use `allow_missing_context=True`.
6. Quote freshness, tradeability, fee, depth, robust edge, and venue limits are
   cumulative submit requirements. None is a dormant alternate probability or
   sizing regime.

## Forbidden shape

- No default-off alternate edge builder inside live modules.
- No optional cost-evidence object that changes edge or bootstrap semantics.
- No environment switch that removes Kelly haircuts for selected edges.
- No simulated/read-only-live phase that can later acquire submit authority.
- No price-provenance laundering from bare probability to an apparently
  executable price.

## Executable anchors

- `src/strategy/market_analysis.py::MarketAnalysis.find_edges`
- `src/types/market.py::BinEdge`
- `src/contracts/execution_price.py::ExecutionPrice`
- `src/engine/evaluator.py::_size_at_execution_price_boundary`
- `src/engine/cycle_runtime.py`

## Verification

- `tests/test_R1_edge_kelly_entry_price_identity.py`
- `tests/test_R2_bin_edge_executable_provenance.py`
- current evaluator, replay, and cycle-runtime sizing tests
