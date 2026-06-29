# Zeus

> Quantitative trading engine for weather-settlement prediction markets on Polymarket.

Zeus trades daily high and low temperature markets. It reads the same weather everyone
reads, turns it into a probability for each market that is honest about its own
uncertainty, and commits capital only where that probability beats the price by a margin
it can defend against its own settled record. What follows is exactly how a forecast
becomes a position, and what keeps each step honest.

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

Each market asks a yes/no question — *"will Tokyo's high land in 50–51°F?"* — and settles
on the integer temperature an official provider reports for the day. That integer is the
end of a rounding chain: a real `74.45°F` is measured by a sensor, rounded in the weather
report, and posted as `74°F`. Zeus follows that chain exactly instead of treating
temperature as a smooth number, because a fraction of a degree decides which side of a bin
boundary the day lands on — and the cities don't all round the same way.

A market is one of three shapes — an exact value or range (`50–51°F`), an open ceiling
(`75°F or higher`), or an open floor (`30°C or below`). High and low markets for the same
city share nothing: different measurement, different history, different calibration.

## How a position is made

### 1 · From many forecasts to one probability

The inputs are several independent global weather models — ECMWF, GFS, ICON, Arpège — and,
for a city that settles on a known weather station, that nation's own official forecast for
that exact station.

Every model runs warm or cold in its own way, so each is first corrected against its own
settled history — and that correction is trusted only in proportion to how much history
exists. A model with a long track record is believed; a barely-tested one is held close to a
neutral prior until it earns more.

The corrected models are then combined by **reliability, not reputation**: each is weighted
by how little it has actually erred, and two models that are really the same forecast at
different resolutions are counted once, never twice. The result is a single best estimate
with a single spread.

That spread is where most systems quietly lie to themselves. A grid model describes a square
of the map, but a market settles at one airport thermometer — often kilometres away and at a
different altitude. Zeus reads the forecast at the station's exact coordinates and treats the
leftover distance and elevation as *added uncertainty*, so a far-off or poorly-matched source
counts for less on its own. The final spread is never allowed to be tighter than the errors
the system has actually made at settlement, because overconfidence is the one mistake that
empties a book.

Only then is the forecast laid onto the market's bins — through the precise rounding rule the
city settles by — so the probability of `74°F` is the chance the true temperature *rounds* to
74, not merely that it falls between 74 and 75.

### 2 · From a probability to an edge worth taking

A number that beats the price is not yet a reason to trade. Each bin must pass four checks in
order, and most fall at one of them:

- **Confidence, not hope.** Zeus acts on a conservative lower bound of the probability, not
  the optimistic midpoint.
- **The honesty check.** This is the one that matters most. The simple rule "my probability
  beats the price" quietly selects exactly the bins where the model is *most overconfident* —
  it wins the bets it shouldn't. Zeus counters this by asking how often bins of this exact
  kind have actually settled in its favour, and stands down where that record is thin or poor.
- **A real margin.** The edge must clear the market price *and* the cost of trading.
- **No flukes.** Across all the bins weighed in a cycle, a false-discovery control keeps the
  rate of edges that are really just noise in check.

### 3 · From an edge to a sized position

Among the bins that survive, Zeus takes the best **return per dollar at risk** — not the
largest gross swing — so the book funds several independent bets rather than one oversized
one. It will buy either side of any bin; the forecast's favoured outcome never vetoes the
other.

The chosen bet is sized by fractional Kelly, then scaled back through a chain of independent
brakes — how wide the uncertainty is, how far off settlement, how much risk the book already
carries, how deep any recent drawdown runs. And it fails safe: a missing or broken input
produces no trade, never a careless one.

### 4 · Keeping the position honest until it settles

A resting order is not forgotten. Every cycle it is re-examined against fresh prices and a
fresh forecast, and pulled and decided again the moment the edge fades or the price drifts —
a new model run on a market already held is itself new information.

Held positions are monitored toward an exit and continually reconciled against the
blockchain, where a *missing* reading is never mistaken for a closed position — only a clear,
complete settlement counts. When a market resolves, the outcome flows back into the
calibration the next forecast depends on, with strict care that knowing the result never
leaks backward into what the model is judged to have known beforehand.

## Strategies

Five strategies trade live, each capturing a different inefficiency and fading at its own
pace as the market competes it away:

| Strategy | Where the edge comes from | Fades |
|----------|---------------------------|:-----:|
| **Settlement Capture** | observed fact, once the day's peak has passed | very slowly |
| **Center Bin Buy** | the model beating the market on the most-likely bin | quickly |
| **Imminent Open Capture** | re-opened or next-day markets close to settlement | quickly |
| **Opening Inertia** | mispricing in a freshly opened market | fastest |

Each is graded on its own settled record; several more are registered but held back until
the evidence earns them in.

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

For the methods behind each step — the forecast fusion, the settlement calibration, the
sizing — see [`docs/reference/theory_map.md`](docs/reference/theory_map.md), with terms
defined in [`glossary.md`](docs/reference/glossary.md).

## License

Proprietary — all rights reserved. See [LICENSE](LICENSE).
