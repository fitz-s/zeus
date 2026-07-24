# Zeus live strategy specification

This reference describes only strategies that currently exist in the live
registry. It is not an incubator, candidate catalog, research backlog, or route
for later activation. A strategy absent from
`architecture/strategy_profile_registry.yaml` is unknown and fail-closed.

## Live registry

| Strategy | Economic source | Runtime boundary |
|---|---|---|
| `settlement_capture` | observation-confirmed settlement fact | settlement-day capture only |
| `day0_nowcast_entry` | settlement-day forecast upside before observation lock | settlement-day, finite support |
| `center_buy` | calibrated forecast probability above executable YES cost | configured finite-support phases |
| `forecast_qkernel_entry` | replacement-chain q/LCB | source-clock entry route |
| `opening_inertia` | opening-price adjustment lag | opening-hunt route |
| `imminent_open_capture` | imminent market-open adjustment lag | imminent-open route |

The exact phase, direction, topology, metric, Kelly, source-run, and economic
limits live in the registry. Runtime code must consume those fields rather than
maintain a second strategy list.

## Admission law

- Registry rows must have `live_status: live`; no blocked, deprecated,
  candidate, proposed, simulated, or observation-only strategy row is valid.
- Unknown strategy keys fail closed and receive no Kelly or submit authority.
- Metric-level `blocked` is a present capability boundary inside a live
  strategy, not a dormant strategy implementation.
- Strategy identity never overrides contract, source, settlement, executable
  price, risk, lifecycle, or submit-time freshness law.
- Historical strategy labels may remain decodable in read-only attribution
  code and immutable records; they are not registry entries or buildable
  runtime entities.

## Re-decision

Every live strategy is re-evaluated against current source truth, probability,
book, risk, and portfolio state. A resting candidate or held position does not
retain stale authority. A fallback remains WATCH-only until a complete fresh
re-rank makes it primary.

## Verification anchors

- `src/strategy/strategy_profile.py`
- `src/engine/cycle_runner.py`
- `src/engine/evaluator.py`
- `src/strategy/kelly.py`
- `tests/test_strategy_profile_registry.py`
- `tests/test_evaluator_strategy_key_failclosed.py`
