# Zeus

Weather-derivatives trading engine for Polymarket daily-temperature markets.

Zeus ingests weather forecasts, calibrates them into a probability for each market, and
trades the markets where that probability differs from the price by enough to cover cost and
uncertainty. It runs the whole path — forecast ingestion, calibration, edge detection,
sizing, order placement, settlement, and feeding results back into the models — across 54
cities.

It is a private, single-operator system trading real capital, published here for transparency
and review. It is not open source and not deployable as-is. See [LICENSE](LICENSE).

## How it works

Forecasts from around two dozen models and official station feeds are de-biased against their
own settled history and combined by reliability into one probability per market, then read
onto the market's bins under that city's settlement rounding rule. A bin is traded only if a
conservative lower bound on its probability clears the price plus cost, similar bins have
actually settled in its favour, and the edge survives false-discovery control. Surviving bins
are sized with fractional Kelly and placed as limit orders. Settled outcomes are graded for
skill versus luck, and only skill feeds back into calibration.

The methods are documented in full under [`docs/reference/`](docs/reference/) — start with
[`theory_map.md`](docs/reference/theory_map.md).

## Repository

```text
src/             Engine: forecasting, calibration, decision, execution, state, risk
tests/           Test suite
scripts/         Tooling and integrity checks (topology_doctor.py)
architecture/    Manifests and invariants
config/          Configuration and source registries
docs/            Reference and operational documentation
state/           Runtime databases (local, not committed)
```

## Documentation

- [`docs/reference/theory_map.md`](docs/reference/theory_map.md) — forecasting, calibration,
  and sizing methods
- [`docs/reference/glossary.md`](docs/reference/glossary.md) — terminology
- [`AGENTS.md`](AGENTS.md) — conventions for the agents that maintain this repository

## License

Proprietary, all rights reserved. See [LICENSE](LICENSE).
