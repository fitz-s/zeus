# Zeus

Weather-derivatives trading engine for Polymarket daily-temperature markets, across 54
cities. It ingests weather forecasts, calibrates them into a settlement probability for
every bin of every market, trades the bins it prices differently from the book, manages the
orders through to settlement, and feeds graded outcomes back into calibration.

**Evidence:** [three decisions that change the answer](#three-things-worth-a-minute) ·
[settled calibration, adverse verdicts included](docs/reference/calibration_report.md) ·
[scope and honest limits](#scope-and-honest-limits)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture-dark.svg">
  <img alt="Zeus cycle: sources feed forecast, probability, edge and sizing; execution and settlement feed a six-class attribution and walk-forward calibration" src="docs/architecture-light.svg">
</picture>

## What this repository establishes — and what it does not

This is a running system, not a backtest: the code in this repository is the code the
engine trades with, live, and every claim above is checkable against the databases it
writes. What the history here establishes is that the full loop — forecast, price,
execute, settle, attribute, recalibrate — runs unattended end to end; that changes to it,
including AI-assisted ones, go through a reviewable, auditable process rather than being
pasted in; and that the calibration discipline (frozen decision-time probabilities,
walk-forward-only learning, selection-aware bounds) is real and enforced in code, not
aspirational. It
does not establish durable net alpha — the settled sample is small, and the honest reading
of it is calibration evidence, not a return claim. It also does not establish operation at
institutional scale or as a team practice; this is one person's system, sized accordingly.

---

## Three things worth a minute

If you read nothing else, read these. Each is a decision that changes the answer, not a
detail of the implementation.

**Bins are priced over the preimage of the rounding rule, not the bin's face value.**
A market settles on an integer temperature that an official provider publishes — a station
observation, represented through METAR where applicable, rounded to a whole degree. So the
rounding rule is part of the market. Most cities round half-up; Hong Kong truncates. Pricing bin `X` as
written, rather than as `Φ((X+0.5−μ)/σ) − Φ((X−0.5−μ)/σ)`, is a systematic error that every
participant who skips this step carries on every trade.
[→ Integrate](#forecast-to-probability)

**Multiple-comparisons control is applied across every bin tested, not the survivors.**
A cycle prices every bin of every market in 54 cities. Applying Benjamini–Hochberg only to
candidates that already passed earlier filters would be selecting on the outcome. It runs
across the full set tested that cycle. Above it sits a selection calibrator: each candidate
is keyed by `(side, lead, bin class, probability bucket)` and its admission probability is
replaced by a conservative 95% lower bound on how often that cell has historically settled
in its favour — an empirical-Bayes beta-binomial bound, cascade-pooled when the cell is
thin, fail-closed when its evidence is absent or stale.
[→ Probability to edge](#probability-to-edge)

**The probability a position was sized on is frozen, and settled outcomes are graded
against that record.**
When a market resolves, the position is graded into one of six classes — forecast-earned
win, lucky win, foreseeable loss, miscalibration loss, stale-data decision, unattributable —
against the probability frozen at decision time where that certificate exists, never one
reconstructed afterwards. A missing certificate is counted as unattributable, published as a
coverage failure, and never imputed. Attribution classifies the outcome relative to the
frozen evidence; it does not filter the reliability sample, which pools every scoreable
decision, adverse ones included. Learning is strictly walk-forward — an outcome may change
the next decision, never the record of the last one.
[→ State and learning](#state-and-learning)

---

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
provider publishes for the local date. That integer is a rounded station observation —
represented through METAR where applicable — so the rounding rule is part of each market:
most cities round half-up (the integer is `floor(x + 0.5)`), Hong Kong truncates
(`floor(x)`). Bins are exact (a value or closed range), open-ceiling, or
open-floor; a city's bins form a complete partition, Fahrenheit bins span two integers and
Celsius one, and a city's high and low markets are separate objects with separate
calibration.

## Data

Forecasts come from ECMWF's global ensemble (the anchor) plus decorrelated regional model
families — ICON (DWD), NOAA, UKMO, and GEM (CMC) — each used where it covers a city, sourced
through ECMWF OpenData and Open-Meteo. For cities that settle on a known station, that
nation's official station forecast is ingested as well (Hong Kong Observatory, Taiwan CWA).
Models refresh two to four times a day on their issue cycles.

Observations come from Weather Underground (daily settlement values), METAR (15-minute), and
the HKO and CWA feeds. Market data — market topology, the order book, and the engine's own
fills — streams from Polymarket over WebSocket. Every record is stamped with when the source
issued it, when Zeus fetched it, and when Zeus wrote it. Freshness is graded, not binary: an
aging forecast first widens its stated uncertainty, then loses entry authority while held
positions stay monitored, then expires outright; stale quotes and unsettled observations are
dropped. Ingestion is split across separate daemons per feed.

## Forecast to probability

1. **De-bias.** Each model is corrected against its own settled residuals with an
   empirical-Bayes shrinkage, `b̂ = λ·r̄ + (1 − λ)·prior` with `λ = n/(n + 8)`: thin history
   stays near a structural prior, long history trusts the model's own mean. The fit uses only
   residuals that had settled before the forecast date.

2. **Fuse.** The de-biased model values `z` are combined into one posterior mean and variance
   by inverse-variance (precision) weighting against an ECMWF prior `(μ₀, τ₀²)`:
   `V* = (τ₀⁻² + 1ᵀΣ⁻¹1)⁻¹`, `μ* = V*(τ₀⁻²μ₀ + 1ᵀΣ⁻¹z)`. The residual covariance `Σ` is
   shrunk toward its diagonal (Ledoit–Wolf) so noisy cross-correlations do not dominate at
   small sample sizes, and models that are the same forecast at two resolutions are collapsed
   into one provider family so none is counted twice.

3. **Localize.** A grid value is read at the settlement station's exact coordinates by
   interpolation rather than nearest-cell. What the grid cannot represent about the station —
   distance, elevation, exposure — widens that source's variance instead of shifting its
   value; representation error is priced as uncertainty, never hidden as a correction.

4. **Spread.** The predictive spread is current evidence, not history: the member spread of
   the same-cycle causal ECMWF ensemble for that exact target, the disagreement between
   providers at the decision instant, and the displacement between ensemble and fused
   centres, added in quadrature. A missing or stale ensemble shape fails closed — the engine
   does not substitute a historical residual, a constant width, or a fitted floor for
   evidence it does not have.

5. **Integrate.** The distribution is integrated onto each bin over the preimage of the
   rounding rule, not the bin's face value — under half-up, bin `X` is
   `Φ((X+0.5−μ)/σ) − Φ((X−0.5−μ)/σ)`; under truncation, `Φ((X+1−μ)/σ) − Φ((X−μ)/σ)`. Open
   shoulders integrate as a single tail.

6. **Condition on the day.** Once part of the day's extreme is already observed, the settled
   high is `max(observed so far, remaining hours)` and the settled low its mirror under `min`;
   the distribution is conditioned on the running extreme, placing remaining mass on the hours
   still to come.

## Probability to edge

Four distinct objects, and none is allowed to impersonate another: the **posterior mean** is
the action probability — the number a trade is valued and sized at; the **confidence band**
says what current evidence can witness around it; the **admission bound** is a separate,
historical statistic of the engine's own settled record; the **error-control test** governs
the family of hypotheses. Bounds constrain what a candidate may claim — they never replace
the action probability.

1. **Confidence band.** Around the posterior mean sits what current evidence can actually
   witness: an exact finite-sample bound from how many ensemble members land in the bin's
   preimage, and a distribution-robust bound from the centre and variance alone. However
   clean the Normal tail looks, certainty the members cannot support is not available to
   claim.

2. **Admission bound.** Each candidate is keyed by `(side, lead, bin class, probability
   bucket)` and admitted at a conservative 95% lower bound on how often that cell has
   historically settled in its favour — an empirical-Bayes beta-binomial bound over the
   engine's own settled record, cascade-pooled when the cell is thin, fail-closed when its
   evidence is absent or stale.

3. **Edge.** A candidate must clear its executable all-in cost — price and the Polymarket
   taker fee `rate·p·(1−p)` — at its conservative bound, not at its point estimate.

4. **False-discovery control.** Benjamini–Hochberg is applied across every bin tested in the
   cycle, not only those that passed earlier filters.

## Sizing

Surviving candidates are not sized in isolation. Each executable route — bin, side, maker or
taker — is valued as wealth in every joint settlement outcome of its market family, against
the positions already held; route and stake are chosen together by maximizing the lower-tail
CVaR of expected log-growth across coherent probability draws — the mean of the worst
fraction of draws, chosen because it stays concave and solvable to a global optimum where a
raw quantile does not — with the confidence band capping what any candidate may claim. Bins
of one market compete for the same capital, so the admission threshold is endogenous rather
than a per-bin constant. A NaN, a missing input, or a missing authority sizes to zero.

## Execution

Orders are limit orders. Entries rest as a maker (good-till-cancel, post-only) and escalate
to a taker cross (fill-or-kill or fill-and-kill) only if the edge holds past a deadline. Each
order carries an idempotency key and its intent is written before the venue is contacted.
Fills are verified against the venue each cycle; an order is entered only on a confirmed
trade fact, and partial fills track their remainder. Exits run a separate state machine, and
an exit's fill-or-kill is coerced to fill-and-kill so a thin book does not reject it whole.
Each cycle, local intent is reconciled against venue and chain facts. Settlement is read
from the market feed; redemption of winning tokens is recorded for accounting.

## State and learning

What the engine believes it holds is a projection over immutable venue facts (orders,
trades, balances) and local intent. Chain reconciliation distinguishes a complete-empty
snapshot from a missing or stale one; on-chain inventory with no matching intent is
quarantined behind a scoped entry block and a review item — unexplained holdings stop new
risk in their scope until they are explained. State is held in three SQLite databases —
world facts, forecasts, trades — with machine-checked ownership; cross-database writes are
confined to two sanctioned helpers that group them in a single connection's transaction.

When a market resolves, the position is graded into one of six outcomes — forecast-earned
win, lucky win, foreseeable loss, miscalibration loss, stale-data decision, unattributable —
against the probability it was sized on, frozen at decision time where that certificate
exists; a missing certificate is counted and published as a coverage failure, never imputed.
Attribution classifies the outcome relative to the frozen evidence; it does not filter it:
reliability is measured over every scoreable frozen decision, and learning is strictly
walk-forward — only residuals settled before a decision may inform it.

The sample this produces is a few hundred settled positions — enough to ask whether the stated
probability matches the settled frequency, not enough to support a return figure. `python3
scripts/generate_calibration_report.py` regenerates
[`docs/reference/calibration_report.md`](docs/reference/calibration_report.md), a settled-only
reliability diagram (with per-bin counts and Wilson intervals) decomposed into calibration vs
informativeness and cut by lead time, side, strategy, and the six-class attribution above.

## Strategies

| Strategy | Decision premise | Fades |
|----------|-------------|:-----:|
| Settlement Capture | the daily extreme is observed once the peak has passed | slowest |
| Center Bin Buy | the model prices the most-likely bin against the market | fast |
| Imminent Open Capture | re-opened or next-day markets within hours of settlement | fast |
| Opening Inertia | first-liquidity anchoring on a freshly opened market | fastest |

Each is tracked on its own settled record. Further strategies (shoulder-bin sell, center-bin
sell, tail-capture) are registered but not live.

## Scope and honest limits

Read this before drawing conclusions from anything above.

**Capital is small and deliberately so.** This runs on a personal account with low
four-figure exposure. The strategy is unproven at any size and the sizing cascade is tuned
for survival rather than growth, so absolute returns are not the thing to look at. What the
sample is large enough to say something about is *calibration* — whether the stated
probabilities match settled frequencies — and that is what the attribution taxonomy exists
to measure.

**Per-strategy attribution is not clean.** The account holds unrelated positions, so
account-level PnL is not a measurement of this system. Chain-truth reconciliation separates
Zeus decisions from foreign inventory, but the separation is a reconstruction, not an
instrument. Any single number quoted from it should be read as indicative.

**Two modules have accreted past the point of easy reasoning.**
`src/engine/event_reactor_adapter.py` and `src/execution/command_recovery.py` are the
largest files in the repository, and neither should be one module. This is the same failure
in structural form that the system's own history keeps producing: the signal layer gets
over-engineered and the position-lifecycle layer accretes around it. Decomposition is in
progress against explicit invariant seams rather than by line count.

**What is not claimed.** That the strategies generalize beyond daily temperature markets;
that the microstructure edges persist as the venue matures; that the forecast fusion beats a
well-tuned single-model baseline by a margin this sample can resolve. Each of those is a
falsifiable claim and none of them has enough settled data behind it yet.

## Reading this repository

Depending on how much time you have:

| Time | Read |
|---|---|
| 2 min | this page, [Three things worth a minute](#three-things-worth-a-minute) |
| 10 min | [`AGENTS.md`](AGENTS.md) — root operating law: what governs a change to this repository |
| 10 min | [`REVIEW.md`](REVIEW.md) — review doctrine: what a change is checked against before it lands |
| 15 min | [`docs/reference/theory_map.md`](docs/reference/theory_map.md) — index of the derivations behind each step above |
| 15 min | [`loop/README.md`](loop/README.md) — the unattended improvement loop that proposes and reviews changes against this system |
| 30 min | [`src/forecast/`](src/forecast) and [`src/calibration/`](src/calibration) — the statistics, end to end |
| 30 min | [`src/execution/exit_lifecycle.py`](src/execution/exit_lifecycle.py) — the exit state machine, which is where the real difficulty lives |
| 45 min | [`architecture/`](architecture) — machine-readable invariants and the AST rules that enforce them in CI |
| — | [`docs/reference/glossary.md`](docs/reference/glossary.md) for terms |

## Engineering

Solo-built. Roughly 590 source modules against 1,400 test modules. Architecture invariants
are expressed as machine-readable manifests and enforced by AST rules and an import-linter
contract in CI, alongside a money-path release gate that blocks changes to order-submitting
code without the corresponding safety tests. Runtime state is SQLite; deployment is a set of
launchd daemons designed for continuous operation.

Three paths worth reading closely, each for a different reason:

- [`src/decision/selection_calibrator.py`](src/decision/selection_calibrator.py) — the
  admission lower bound, backed by a from-scratch, pure-Python regularized incomplete-beta
  function and an empirical-Bayes bound over settled cells, with no SciPy in the hot path.
- [`src/execution/command_bus.py`](src/execution/command_bus.py) — `IdempotencyKey`, a
  frozen value object with a deterministic factory whose collision probability is argued
  from first principles (birthday bound over a 128-bit key), not assumed away.
- [`src/analysis/settlement_skill_attribution.py`](src/analysis/settlement_skill_attribution.py)
  — grades every settled position into one of six skill/luck classes off an immutable,
  decision-time certificate, so a lucky win can never be counted as evidence of skill.

## License

Source-available for reading and review. Not licensed for use, modification, or
redistribution — see [LICENSE](LICENSE). This is published so the work can be examined, not
as an open-source project; [`CONTRIBUTING.md`](CONTRIBUTING.md) explains why it is closed to
contributions.
