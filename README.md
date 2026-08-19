# Zeus

Weather-derivatives trading engine for Polymarket daily-temperature markets. It ingests
weather forecasts, calibrates them into a settlement probability for every bin of every
market, trades the bins it prices differently from the book, manages the orders through to
settlement, and feeds graded outcomes back into calibration.

A settlement is one integer, published once, after the market has closed — no partial
credit, no second attempt.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture-dark.svg">
  <img alt="Zeus cycle: sources feed forecast, probability, edge and sizing; execution and settlement feed a six-class attribution that filters into calibration" src="docs/architecture-light.svg">
</picture>

This repository is the production system, not a backtest of one: the decision, execution,
reconciliation, and settlement paths here are the paths that trade. Source establishes the
mechanism; `scripts/verify_pipeline_liveness.py` establishes, with dated read-only output,
whether those paths were live at a given deployment.

## Operation

The engine runs a repeating cycle. Each cycle it reconciles its positions against the chain,
refreshes forecasts, observations, and prices, re-evaluates every held position and resting
order against the new data, and scans for new entries. A resting order whose edge has faded
or whose limit the market has moved away from is pulled and decided again; a fresh forecast
cycle on a market already held is itself new information. Held positions are re-evaluated
each cycle and exited when their edge reverses, a profit is takeable, or settlement is near.
The sections below detail one pass of that cycle.

## Markets

A market is a set of yes/no bins over a city's daily high or low (`50–51°F`, `75°F or
higher`, `49°F or below`). One bin resolves YES, on the integer temperature an official
provider publishes for the local date. That integer is a rounded value — a sensor reading
encoded in a METAR report and rounded to a whole degree — so the rounding rule is part of
each market: most cities round half-up (`floor(x + 0.5)`), Hong Kong truncates (`floor(x)`).
Bins are exact, open-ceiling, or open-floor; a city's bins form a complete partition,
Fahrenheit bins span two integers and Celsius one, and a city's high and low markets are
separate objects with separate calibration.

## Data

Forecasts come from ECMWF's global ensemble (the anchor) plus decorrelated regional model
families — ICON (DWD), NOAA, UKMO, and GEM (CMC) — each used where it covers a city, sourced
through ECMWF OpenData and Open-Meteo. For cities that settle on a known station, that
nation's official station forecast is ingested as well (Hong Kong Observatory, Taiwan CWA).
Models refresh two to four times a day on their issue cycles.

Observations come from Weather Underground (daily settlement values), METAR (15-minute), and
the HKO and CWA feeds. Market data — market topology, the order book, and the engine's own
fills — streams from Polymarket over WebSocket. Every record is stamped with when the source
issued it, when Zeus fetched it, and when Zeus wrote it; freshness gates use those stamps to
drop stale forecasts, unsettled observations, and old quotes, and fail closed — stale data
reads as degraded, never as fresh. Ingestion is split across separate daemons per feed.

## Forecast to probability

1. **Capture.** Each provider's raw value enters under a decision-time availability proof:
   both its publication and capture clocks must precede the decision, and missing or
   malformed provenance is exclusion, not permission. Settled residual history sets
   covariance, prior width, and low-sample trust — it never shifts the served center. An
   earlier law did shift centers by a fitted bias; it was retired after a stale artifact
   proposed a −4.85°C shift for Tokyo against a realized residual band of −0.33°C.

2. **Fuse.** The raw instruments `z` are combined into one posterior mean and variance by
   inverse-variance weighting against an ECMWF prior `(μ₀, τ₀²)`:
   `V* = (τ₀⁻² + 1ᵀΣ⁻¹1)⁻¹`, `μ* = V*(τ₀⁻²μ₀ + 1ᵀΣ⁻¹z)`. The residual covariance `Σ` is
   built on the intersection of actual target dates, never equal-length array positions —
   positional stacking pairs one provider's May 1 error with another's May 2 and lets a
   well-conditioned `Σ⁻¹` amplify correlation that never co-occurred. With enough common
   history `Σ` is shrunk toward its diagonal (Ledoit–Wolf); models that are the same
   forecast at two resolutions collapse into one provider family so none votes twice.

3. **Localize.** A grid value is read at the settlement station's exact coordinates by
   interpolation rather than nearest-cell. Elevation mismatch corrects the mean through a
   lapse rate fitted per city and season; the remaining distance and elevation mismatch is
   added to that source's variance, `σ_repr² = a₀ + a_d·d² + a_z·Δz²` — an offset needs
   correcting, a noisy microclimate needs distrust, and one knob cannot do both jobs.

4. **Spread.** The predictive spread is the fused variance plus the walk-forward residual
   error of the fused centre, floored to the cell's realized settlement error.

5. **Integrate.** The distribution is integrated onto each bin over the preimage of its
   city's rounding rule, not the bin's face value: under half-up, bin `X` is
   `Φ((X+0.5−μ)/σ) − Φ((X−0.5−μ)/σ)`; under truncation, `Φ((X+1−μ)/σ) − Φ((X−μ)/σ)`. On its
   face value, a point bin has zero width — the most likely outcome priced at zero. Open
   shoulders integrate as a single tail.

6. **Condition on the day.** Once part of the day's extreme is observed, the settled value
   is `max(observed, remaining)`; the distribution is conditioned on the running extreme,
   placing remaining mass on the hours still to come.

## Probability to edge

1. **Lower bound.** The bin probability is bootstrapped over the parameter posterior and a
   low quantile is taken; each draw is renormalized to a distribution before the quantile,
   so the modal bin's bound is not dragged down by peak-shift draws a coherent row would
   have offset next door.

2. **Selection calibrator.** Each candidate is keyed by `(side, lead, bin class, probability
   bucket)` and its admission probability is replaced by a Wilson lower bound on how often
   that cell has settled in its favour; a cell under 30 settled samples borrows a pooled
   bound from the nearest cells that clear it — borrowed evidence rather than none.

3. **Payoff vectors.** Every executable route maps to a payoff vector over the market's
   complete outcome space, so YES, NO, and basket routes enter one algebra. Point fair value
   is `q · payoff`; the robust edge is a low quantile of `samples · payoff − cost`, with
   cost the all-in executable price including the taker fee `rate·p·(1−p)`. Scalar per-bin
   `q − price` cannot represent a NO route whose payoff spans every sibling outcome — it is
   logged, but nothing selects on it.

4. **False-discovery control.** Benjamini–Hochberg runs within each market across every bin
   tested, not only those that passed earlier filters; a missing p-value is a hard error, so
   it can never silently run on survivors.

## Sizing

Route and stake are chosen together, maximizing lower-tail incremental log wealth — `ΔU(s)`
per probability draw over the full outcome set, against current holdings, pending exposure,
and the route's own depth-walked cost curve. Independent per-bin Kelly fails twice here: it
allocates the same bankroll repeatedly across mutually exclusive siblings, and a stake
priced at top-of-book can destroy, by walking its own depth, the utility that selected it.
The per-strategy and per-city multipliers that once scaled sizing were deleted when the
uncertainty they hedged moved into the robust band itself — each uncertainty counted exactly
once; a strategy key now only grants or denies permission to trade. Admission is still
gated by a two-rail data-density discount: an absolute hard stop on indefensible station
coverage, and a relative rail floored at a low percentile of the city's own coverage history
rather than a rolling mean — a slowly dying station drags a rolling baseline down with it
and never trips the alarm. A NaN or missing input sizes to zero.

Exit decisions never read entry price: under log-utility, positions with identical current
wealth, holdings, posterior, and time-to-resolution take the same optimal action regardless
of what was paid. Cost basis is sunk; a stop-loss keyed to it triggers on luck, not state.
Settled losses are not re-vetoed by a drawdown window either — the loss is already in the
bankroll the next sizing call reads.

## Execution

Orders are limit orders. Entries rest as a maker (good-till-cancel, post-only) and escalate
to a taker cross only if the edge holds past a deadline. Each order carries an idempotency
key and its intent is written before the venue is contacted. A submission that times out is
neither retried nor assumed failed — a blind retry double-submits if the first request
landed; assuming failure silently drops a live position — it holds an explicit unknown state
until the venue is re-queried. Order state reduces across venue read sources by strength of
evidence, not recency, so a confirmed fill is never overwritten by a staler read arriving
later. Exits run a separate state machine, and an exit's fill-or-kill is coerced to
fill-and-kill so a thin book does not reject it whole. An hourly sweep reconciles local
intent against venue and chain facts.

## Worked example

Tokyo daily high, the `50–51°F` bin, two days out, with illustrative numbers:

```
Models (raw, °F)   ECMWF 50.4 · global ICON 51.0 · UKMO 50.1 · …
Fuse               precision-weighted → μ* = 50.3 °F, fused sd 0.9 °F
Localize           station 8 km / +5 m from the grid cell → +representativeness variance
Spread             √(V* + resid²) = 1.3 °F, floored to realized settlement error → σ = 1.4 °F
Integrate          P(50–51) = Φ((51.5−50.3)/1.4) − Φ((49.5−50.3)/1.4) = 0.804 − 0.284 = 0.52
Lower bound        5th-percentile bootstrap → 0.46
Calibrator         cell settled in favour 57% over 60 samples → Wilson lower bound 0.46
Route              YES on 50–51 → payoff vector e_bin; fair value q·payoff = 0.52
Robust edge        5th pct of samples·payoff − cost(0.41 all-in, depth-walked) = 0.05, passes FDR
Stake              argmax of lower-tail ΔU(s) against holdings + pending exposure
Order              rest as maker at 0.40; escalate to a taker cross if the edge holds
```

Scalar per-bin `q − price` would also have admitted the NO route on a neighbouring bin
without noticing its payoff spans every sibling outcome, and independent Kelly would have
sized both against the same bankroll. The family objective admits one route, at a stake its
own depth-walk survives. The same numbers drive the exit: when a later forecast cycle moves
`μ*` and the lower bound falls below price plus cost, the edge has reversed and the position
closes.

## State and learning

What the engine believes it holds is a projection over immutable venue facts and local
intent. Chain reconciliation distinguishes verified-empty from missing-or-stale — only the
former can void a locally-held position — and surfaces on-chain inventory with no matching
intent for review. State is held in three SQLite databases — world facts, forecasts, trades
— under a table-ownership registry asserted at boot: an unqualified table name resolves by
SQLite's attach order, not by intent, so a retired table left on disk would silently absorb
its replacement's writes. Cross-database writes go through one connection, `ATTACH`, and a
single savepoint; WAL has no cross-file atomicity to offer.

When a market resolves, the position is graded against the probability it was sized on —
frozen at decision time, never reconstructed — into one of six outcomes: forecast-earned
win, lucky win, foreseeable loss, miscalibration loss, stale-data decision, unattributable.
Skill claims answer to that taxonomy; probability calibration answers to every causally
eligible settlement, because a distribution is scored on all its outcomes. The grading is
held to its own bar: one staleness check, plausible on its face, was convicted by audit —
every position it flagged as decided-on-stale-data had its "fresher" forecast computed only
*after* the decision, median 27 hours. Superseded grades are archived, never overwritten, so
a fix can be verified against the exact corpus it corrects.

The settled sample is a few hundred positions on a personal account with low four-figure
exposure — enough to ask whether stated probability matches settled frequency, not enough to
support a return figure, and account-level PnL includes unrelated inventory besides.
`scripts/generate_calibration_report.py` regenerates
[`docs/reference/calibration_report.md`](docs/reference/calibration_report.md): a
settled-only reliability diagram with per-bin counts and Wilson intervals, cut by lead time,
side, strategy, and the six-class attribution.

## Strategies

| Strategy | Edge source | Fades |
|----------|-------------|:-----:|
| Settlement Capture | the daily extreme is observed once the peak has passed | slowest |
| Day-0 Nowcast Entry | the running extreme conditions the distribution intraday | slow |
| Forecast Q-Kernel Entry | the full posterior against the book, any bin that clears the edge gate | fast |
| Center Bin Buy | the model prices the most-likely bin against the market | fast |
| Imminent Open Capture | re-opened or next-day markets within hours of settlement | fast |
| Opening Inertia | first-liquidity anchoring on a freshly opened market | fastest |

Each is tracked on its own settled record. Ten further registered strategies (shoulder and
center sells, tail capture, maker provision, cross-market hedges) are blocked from live.

## Development

The codebase is developed with concurrent AI agents whose output is treated as untrusted
proposal material: prompt law, an OS-level sandbox on the autonomous lane, wrapper
revalidation, anchor tests, then human promotion — with the known escape on the interactive
lane published rather than papered over. Every fail-closed gate must declare, adjacent to
its condition, what trips it, what drains it, and what resets it; a test locates each
enrolled gate through a must-be-unique source anchor and fails loud when the gate is
renamed or stripped, because a comparison with no path back to false is a ratchet, not a
gate.

- [`AGENTS.md`](AGENTS.md) — the operating law governing a change to this repository
- [`AI_ASSISTANCE.md`](AI_ASSISTANCE.md) — what the agents did, what they broke, what remains open
- [`REVIEW.md`](REVIEW.md) — what a change is checked against before it lands
- [`docs/reference/theory_map.md`](docs/reference/theory_map.md) — the derivations behind each step above
- [`docs/reference/glossary.md`](docs/reference/glossary.md) — the terms

Three files worth reading closely:

- [`src/decision/selection_calibrator.py`](src/decision/selection_calibrator.py) — the
  admission bound, on a from-scratch regularized incomplete-beta with no SciPy in the hot path
- [`src/execution/command_bus.py`](src/execution/command_bus.py) — `IdempotencyKey`, collision
  probability argued from a birthday bound, not assumed away
- [`src/analysis/settlement_skill_attribution.py`](src/analysis/settlement_skill_attribution.py)
  — the six-class grading off the immutable decision-time certificate

## License

Source-available for reading and review; not licensed for use, modification, or
redistribution — see [LICENSE](LICENSE) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
