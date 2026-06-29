# Zeus

> Quantitative trading engine for weather-settlement prediction markets on Polymarket.

Zeus trades daily high and low temperature markets. Predicting the weather is the easy part —
everyone has the same forecasts. The work is turning those forecasts into a probability honest
enough to bet real money against a market price: calibrated against what has actually settled,
conservative where it is uncertain, and never confident for the wrong reasons. What follows is
how it works, and what makes each step more than a weighted average.

```mermaid
flowchart LR
    A["Forecasts<br/>models + stations"]:::a --> B["One calibrated<br/>probability"]:::b
    B --> C["Edge<br/>vs market price"]:::c
    C --> D["Sized<br/>position"]:::c
    D --> E["Order on<br/>Polymarket"]:::d
    E --> F["Monitor ·<br/>settle · learn"]:::d
    F -. "settled record" .-> B
    E -. "price moves → re-decide" .-> C
    classDef a fill:#0b3d57,stroke:#7fd1ff,color:#eaf6ff;
    classDef b fill:#123d2e,stroke:#7fe0b0,color:#eafff4;
    classDef c fill:#4a3410,stroke:#ffd479,color:#fff7e6;
    classDef d fill:#3d1230,stroke:#ff9ad1,color:#ffeaf7;
```

> **Status** — Private, operator-run engine trading real capital. Published for
> transparency and audit; not open source, not built for redeployment. See [LICENSE](LICENSE).

---

## What it trades

Each market asks a yes/no question — *"will Tokyo's high land in 50–51°F?"* — and settles on
the integer temperature an official provider reports for the day. That integer is the end of
a rounding chain: a real `74.45°F` is measured by a sensor, rounded in the weather report,
and posted as `74°F`. A fraction of a degree decides which side of a bin boundary the day
lands on, and not every city rounds the same way — so the rounding itself has to be modelled,
not assumed away. Markets come as an exact value or range (`50–51°F`), an open ceiling
(`75°F or higher`), or an open floor (`30°C or below`); high and low markets for a city share
nothing — separate measurement, separate history, separate calibration.

---

## The probability

Everything downstream is only as good as this number, so it is built with some care.

**Forecasts, weighted by reliability rather than reputation.** The inputs are several
independent global models — ECMWF, GFS, ICON, Arpège — and, for a city that settles on a
known station, that nation's own official forecast for that exact station. Each model is
first de-biased against its own settled history with an **empirical-Bayes shrinkage**: the
correction is trusted in proportion to how much history supports it, so a long-tested model
moves freely while a barely-seen one is held near a neutral prior. The de-biased models are
then combined by **precision-weighted fusion** — each weighted by the inverse of how much it
actually errs. The covariance that drives those weights is **shrunk toward its diagonal**
(Ledoit–Wolf), because with only a handful of models the off-diagonal correlations are mostly
noise; and models that are really the same forecast at two resolutions are collapsed into one
**provider family**, so a popular model can't vote twice. On settled cells this fusion beats
an equal-weighted average by more than ten times its own standard error — and beats betting on
the single best model outright. *It wins because it fuses, not because it trusts any one
source.*

**A physical correction the grid can't make for you.** A gridded model describes a square
kilometre of map; a market settles at one airport thermometer, often kilometres away and at a
different altitude. Zeus reads each model at the station's exact coordinates by interpolation,
corrects the altitude gap with a **lapse rate fitted per city** from settled outcomes — not
the textbook −6.5 °C/km, which sea-breezes and thin mountain air routinely break — and then
treats the *remaining* distance-and-elevation mismatch as **added variance, not subtracted
bias**. A far-off or badly-matched station isn't reliably cold; it is reliably *uncertain*,
so the fusion simply leans on it less. No hand-written "distrust the coastal cities" rule
exists anywhere.

**Width that can't lie.** The spread is then floored to the error the system has *actually*
made at settlement for cells like this one. Overconfidence — a spread too narrow, piling
probability onto one bin — is the single mistake that empties a book, so the served
uncertainty is never allowed below the measured truth.

**Onto the bins, through the rounding.** Finally the distribution is integrated onto each
market's bins across the **preimage of the rounding rule** — the set of real temperatures
that *round* to the bin — rather than the bin's face value. The difference is not academic:
get it wrong and a single-degree bin can read as zero probability when it is in fact the most
likely outcome. The rule is part of the contract, and it matters — on Hong Kong's truncating
oracle, integrating the correct asymmetric rounding matched every settled day in a test where
ordinary rounding matched barely a third.

---

## The edge

A number that beats the price is not yet a reason to trade. Each bin runs a gauntlet, and most
are turned away:

- **Confidence, not hope** — Zeus acts on a conservative lower bound of the probability, not
  the optimistic midpoint.
- **The winner's curse, answered.** This is the sharpest idea in the engine. The plain rule
  "trade when my probability beats the price" quietly selects exactly the bins where the model
  is *most overconfident* — it wins the bets it has no business winning. Zeus measures this
  directly: it asks how often bins of this exact kind have actually settled in its favour, and
  serves a conservative lower bound on that realised rate, standing down where the record is
  thin or poor. On one live stretch the model believed a side at thirteen percent that settled
  at thirty-three — the kind of gap no model-internal check can see, and exactly what this one
  catches.
- **A real margin** — the edge must clear the price *and* the cost of trading.
- **No flukes** — across all the bins weighed in a cycle, a false-discovery control keeps the
  rate of edges that are really just noise in check.

---

## The position

Among the bins that survive, Zeus takes the best **return per dollar at risk** — not the
biggest gross swing — so the book funds several independent bets instead of one oversized one.
It will buy either side of any bin; the forecast's favoured outcome never vetoes the other. The
chosen bet is sized by **fractional Kelly**, scaled back through a chain of independent brakes
— how wide the uncertainty, how far off settlement, how much risk the book already carries, how
deep any recent drawdown — and it **fails safe**: a missing or broken input yields no trade,
never a careless one.

A resting order is not forgotten. Every cycle it is re-examined against fresh prices and a fresh
forecast, and pulled and decided again the moment the edge fades or the price drifts — a new
model run on a market already held is itself new information. Held positions are reconciled
against the blockchain, where a *missing* reading is never mistaken for a closed position; and
when a market resolves, the outcome flows back into the calibration the next forecast leans on —
with strict care that knowing the result never leaks backward into what the model is judged to
have known beforehand.

---

## Strategies

Five strategies trade live, each capturing a different inefficiency and fading at its own pace
as the market competes it away:

| Strategy | Where the edge comes from | Fades |
|----------|---------------------------|:-----:|
| **Settlement Capture** | observed fact, once the day's peak has passed | very slowly |
| **Center Bin Buy** | the model beating the market on the most-likely bin | quickly |
| **Imminent Open Capture** | re-opened or next-day markets close to settlement | quickly |
| **Opening Inertia** | mispricing in a freshly opened market | fastest |

Each is graded on its own settled record; several more are registered but held back until the
evidence earns them in.

---

## Project structure

```text
src/             Engine — forecasting, calibration, decision, execution, state, risk
tests/           Correctness and regression guards
scripts/         Maintenance tools and integrity checks
architecture/    Machine-readable manifests and invariants
config/          Runtime configuration and source registries
docs/            Reference, domain, and operational documentation
state/           Runtime databases (local, not committed)
```

The full derivations — the precision-fusion mathematics, the representativeness model, the
settlement calibration, and the sizing — are indexed in
[`docs/reference/theory_map.md`](docs/reference/theory_map.md), with terms defined in
[`glossary.md`](docs/reference/glossary.md).

## License

Proprietary — all rights reserved. See [LICENSE](LICENSE).
