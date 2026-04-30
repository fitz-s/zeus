# 07 — Verification Matrix

## Layer isolation tests

| Test | Expected result |
|---|---|
| Epistemic import fence | `src/epistemic`/posterior code cannot import Polymarket/CLOB/fee/orderbook/token modules |
| Microstructure import fence | `src/microstructure` cannot import weather/calibration/posterior/Kelly |
| Execution boundary | Corrected executor accepts only final intent, not raw `BinEdge` authority |

## Posterior tests

| Test | Expected result |
|---|---|
| Ask/depth changes | posterior unchanged |
| Named MarketPriorDistribution changes | posterior changes with trace id |
| Raw quote passed to posterior | hard reject |
| Sparse monitor vector passed as prior | hard reject |
| Legacy prior in live without flag | hard reject |

## Microstructure tests

| Test | Expected result |
|---|---|
| BUY sweep asks ascending | all-in price matches depth/fee math |
| SELL sweep bids descending | all-in sell value matches depth/fee math |
| Insufficient depth | reject or depth_status=DEPTH_INSUFFICIENT |
| Stale snapshot | reject |
| Missing fee metadata | reject in corrected live path |
| Tick misalignment | reject |
| Min-order violation | reject |
| Neg-risk metadata mismatch | reject |

## Edge/Kelly tests

| Test | Expected result |
|---|---|
| `live_economic_edge <= 0` | reject before Kelly |
| Cost increases with size | Kelly fixed-point shrinks/rejects |
| Raw `entry_price` used at Kelly | static/runtime violation |
| Fee-adjusted implied probability with no cost basis | violation |

## FDR tests

| Test | Expected result |
|---|---|
| Hypothesis id excludes snapshot/cost | fail |
| Snapshot changes after FDR | selected row invalidated |
| Positive edge prefilter only | fail; full family required |
| Research FDR used as live economic FDR | fail |

## Executor tests

| Test | Expected result |
|---|---|
| Missing final limit | reject |
| Missing cost basis hash | reject |
| Token mismatch | reject |
| Snapshot hash mismatch | reject |
| Corrected executor recomputes price | fail |
| Submitted limit differs from final intent | fail |
| Legacy compatibility envelope in certified path | fail |

## Monitor/exit tests

| Test | Expected result |
|---|---|
| Held-token quote used as posterior vector | fail |
| Exit uses held token sell quote | pass |
| Partial sell fill | remaining exposure reduced |
| Corrected entry + legacy exit | promotion blocked |

## Reporting/backtest tests

| Test | Expected result |
|---|---|
| Mixed legacy/corrected economics in promotion report | hard fail |
| Diagnostic report side-by-side cohorts | allowed |
| Historical row without snapshot/depth/hash marked corrected | fail |
| Model-only run claims corrected executable economics | fail |

## Live-readiness gates still separate

Even after all pricing tests pass, live remains blocked unless these are separately green:

```text
source truth validity
calibration readiness
risk/RED sweep behavior
collateral/allowance
venue command/fill facts
monitor/exit symmetry
settlement/learning traceability
operator live-money go
```
