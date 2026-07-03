# Zeus

Weather-derivatives trading engine for Polymarket daily-temperature markets, across 54
cities. It ingests weather forecasts, calibrates them into a settlement probability for every
bin of every market, trades the bins it prices differently from the book, manages the orders
through to settlement, and feeds graded outcomes back into calibration.

## Operation

The engine runs a repeating cycle. Each cycle it reconciles its positions against the chain,
refreshes forecasts, observations, and prices, re-evaluates every held position and resting
order against the new data, and scans for new entries. A resting order whose edge has faded or
whose limit the market has moved away from is pulled and decided again; a fresh forecast cycle
on a market already held is itself new information. Held positions are re-evaluated each cycle
and exited when their edge reverses, a profit is takeable, or settlement is near. The sections
below detail one pass of that cycle.

## Markets

A market is a set of yes/no bins over a city's daily high or low (`50–51°F`, `75°F or higher`,
`49°F or below`). One bin resolves YES, on the integer temperature an official provider
publishes for the local date. That integer is a rounded value — a sensor reading encoded in a
METAR report and rounded to a whole degree — so the rounding rule is part of each market:
most cities round half-up (the integer is `floor(x + 0.5)`), Hong Kong truncates (`floor(x)`).
Bins are exact (a value or closed range), open-ceiling, or open-floor; a city's bins form a
complete partition, Fahrenheit bins span two integers and Celsius one, and a city's high and
low markets are separate objects with separate calibration.

## Data

Forecasts come from ECMWF's global ensemble (the anchor) plus decorrelated regional model
families — ICON (DWD), NOAA, UKMO, and GEM (CMC) — each used where it covers a city, sourced
through ECMWF OpenData and Open-Meteo. For cities that settle on a known station, that nation's
official station forecast is ingested as well (Hong Kong Observatory, Taiwan CWA). Models
refresh two to four times a day on their issue cycles.
Observations come from Weather Underground (daily settlement values), METAR (15-minute), and
the HKO and CWA feeds. Market data — market topology, the order book, and the engine's own
fills — streams from Polymarket over WebSocket. Every record is stamped with when the source
issued it, when Zeus fetched it, and when Zeus wrote it; freshness gates use those stamps to
drop stale forecasts, unsettled observations, and old quotes. Ingestion is split across
separate daemons per feed.

## Forecast to probability

1. **De-bias.** Each model is corrected against its own settled residuals with an
   empirical-Bayes shrinkage, `b̂ = λ·r̄ + (1 − λ)·prior` with `λ = n/(n + 8)`: thin history
   stays near a structural prior, long history trusts the model's own mean. The fit uses only
   residuals that had settled before the forecast date.

2. **Fuse.** The de-biased model values `z` are combined into one posterior mean and variance by
   inverse-variance (precision) weighting against an ECMWF prior `(μ₀, τ₀²)`:
   `V* = (τ₀⁻² + 1ᵀΣ⁻¹1)⁻¹`, `μ* = V*(τ₀⁻²μ₀ + 1ᵀΣ⁻¹z)`. The residual covariance `Σ` is shrunk
   toward its diagonal (Ledoit–Wolf) so noisy cross-correlations do not dominate at small sample
   sizes, and models that are the same forecast at two resolutions are collapsed into one
   provider family so none is counted twice.

3. **Localize.** A grid value is read at the settlement station's exact coordinates by
   interpolation rather than nearest-cell. The altitude difference between grid and station is
   corrected by a lapse rate fitted per city and season; the remaining distance and elevation
   mismatch is added to that source's variance, `σ_repr² = a₀ + a_d·d² + a_z·Δz²`.

4. **Spread.** The predictive spread is the fused variance plus the walk-forward residual error
   of the fused centre, floored to the cell's realized settlement error.

5. **Integrate.** The distribution is integrated onto each bin over the preimage of the
   rounding rule, not the bin's face value — under half-up, bin `X` is
   `Φ((X+0.5−μ)/σ) − Φ((X−0.5−μ)/σ)`; under truncation, `Φ((X+1−μ)/σ) − Φ((X−μ)/σ)`. Open
   shoulders integrate as a single tail.

6. **Condition on the day.** Once part of the day's extreme is already observed, the settled
   value is `max(observed, remaining)`; the distribution is conditioned on the running extreme,
   placing remaining mass on the hours still to come.

## Probability to edge

1. **Lower bound.** The bin probability is bootstrapped over the parameter posterior and a low
   quantile is taken; each draw is renormalized to a distribution before the quantile.

2. **Selection calibrator.** Each candidate is keyed by `(side, lead, bin class, probability
   bucket)` and its admission probability is replaced by a Wilson lower bound on how often that
   cell has settled in its favour, over at least 30 settled samples.

3. **Edge.** `edge = q − price − cost`, where cost is the all-in entry cost including the
   Polymarket taker fee `rate·p·(1−p)`.

4. **False-discovery control.** Benjamini–Hochberg is applied across every bin tested in the
   cycle, not only those that passed earlier filters.

## Sizing

Surviving bins are ranked by return per dollar at risk, ties broken on lower-quantile
log-growth. The selected bin is sized by fractional Kelly, `f* = (q − price)/(1 − price)`,
reduced by a multiplicative cascade — strategy multiplier, observation coverage, confidence
width, lead time, portfolio heat, and a two-rail data-density discount (a hard stop below 0.35
coverage past the window mid-point, a continuous discount otherwise). A NaN or missing input
sizes to zero.

## Execution

Orders are limit orders. Entries rest as a maker (good-till-cancel, post-only) and escalate to
a taker cross (fill-or-kill or fill-and-kill) only if the edge holds past a deadline. Each
order carries an idempotency key and its intent is written before the venue is contacted. Fills
are verified against the venue each cycle; an order is entered only on a confirmed trade fact,
and partial fills track their remainder. Exits run a separate state machine, and an exit's
fill-or-kill is coerced to fill-and-kill so a thin book does not reject it whole. An hourly
sweep reconciles local intent against venue and chain facts. Settlement is read from the market
feed; redemption of winning tokens is recorded for accounting.

## Worked example

One market through the loop, with illustrative numbers — Tokyo daily high, the `50–51°F` bin,
two days out (Tokyo rounds half-up):

```
Models (de-biased, °F)   ECMWF 50.4 · global ICON 51.0 · UKMO 50.1 · …
Fuse                     precision-weighted → μ* = 50.3 °F, fused sd 0.9 °F
Localize                 station 8 km / +5 m from the grid cell → +representativeness variance
Spread                   √(V* + resid²) = 1.3 °F, floored to realized settlement error → σ = 1.4 °F
Integrate (half-up)      P(50–51) = Φ((51.5−50.3)/1.4) − Φ((49.5−50.3)/1.4) = 0.804 − 0.284 = 0.52
Lower bound              5th-percentile bootstrap → 0.46
Calibrator               cell settled in favour 57% over 60 samples → Wilson lower bound 0.46
Edge                     market YES at 0.40, cost 0.01 → 0.46 − 0.40 − 0.01 = 0.05  (> 0, passes FDR)
Size                     f* = (0.46 − 0.40)/(1 − 0.40) = 0.10, reduced by the cascade
Order                    rest as maker buying YES at 0.40; escalate to a taker cross if the edge holds
```

The same numbers drive an exit: if a later forecast cycle moves `μ*` away and the lower-bound
probability falls below the price plus cost, the position's edge has reversed and it is closed.

## State and learning

What the engine believes it holds is a projection over immutable venue facts (orders, trades,
balances) and local intent. Chain reconciliation distinguishes a complete-empty snapshot from
a missing or stale one, and surfaces on-chain inventory with no matching intent as a reviewable
item. State is held in three SQLite databases — world facts, forecasts, trades — with
cross-database writes done in one transaction via `ATTACH` and a savepoint.

When a market resolves, the position is graded into one of six outcomes — forecast-earned win,
lucky win, foreseeable loss, miscalibration loss, stale-data decision, unattributable — and
only the skill outcomes feed calibration. The probability a position was sized on is frozen at
decision time, and calibration consumes only outcomes that have already settled.

## Strategies

| Strategy | Edge source | Fades |
|----------|-------------|:-----:|
| Settlement Capture | the daily extreme is observed once the peak has passed | slowest |
| Center Bin Buy | the model prices the most-likely bin against the market | fast |
| Imminent Open Capture | re-opened or next-day markets within hours of settlement | fast |
| Opening Inertia | first-liquidity anchoring on a freshly opened market | fastest |

Each is tracked on its own settled record. Further strategies (shoulder-bin sell, center-bin
sell, tail-capture) are registered but not live.

## Repository

```text
src/             Engine: forecasting, calibration, decision, execution, state, risk
tests/           Test suite
scripts/         Tooling and integrity checks
architecture/    Machine-readable manifests and invariants
config/          Configuration and source registries
docs/            Reference and operational documentation
state/           Runtime databases (local, not committed)
```

Deeper detail is under [`docs/reference/`](docs/reference/) — [`theory_map.md`](docs/reference/theory_map.md)
indexes the derivations and [`glossary.md`](docs/reference/glossary.md) defines the terms.

## License

Proprietary, all rights reserved. See [LICENSE](LICENSE).
