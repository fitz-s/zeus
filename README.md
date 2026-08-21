# Zeus

Zeus trades daily-temperature prediction markets. Each contract turns a city's weather, a settlement station, an observation convention, and an integer rounding rule into a family of mutually exclusive claims. A forecast can be meteorologically accurate and still lose because it prices the wrong station, the wrong preimage of a rounded bin, or a book that cannot fill at the assumed cost.

The engine therefore treats understanding as a chain of proof. It records what evidence existed before the decision, turns the forecast into one probability distribution over the complete settlement space, maps each executable route to its actual payoff, and moves capital only when the lower tail of that decision remains useful after depth, fees, existing exposure, and a fresh submit-time replay. Settlement judges the exact belief the system committed to where its frozen certificate exists—not a more favorable story reconstructed afterwards; a missing certificate is a published coverage failure, never a reconstruction license.

**Evidence:** [decision-time provenance](#evidence-has-a-clock) · [settled calibration, adverse verdicts included](docs/reference/calibration_report.md) · [scope and limits](#state-and-learning)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/architecture-dark.svg">
  <img alt="Zeus cycle from source evidence through forecast, probability, capital allocation, execution, settlement, and learning" src="docs/architecture-light.svg">
</picture>

The repository contains the production decision, execution, reconciliation, and learning paths. Offline analyses read the records those paths produce; they do not replace live authority.

## Markets are contracts

A market is a complete family of YES/NO bins over one city's daily high or low: an exact value, a closed range, an open floor, or an open ceiling. Exactly one bin resolves YES on the integer published for the contract's local date. High and low markets remain separate objects because they have different physical processes, observation histories, and calibration errors.

The city name is not the settlement definition. The definition also binds the station, source, timezone, metric, unit, precision, finalization rule, and bin topology. Most cities use half-up rounding; Hong Kong uses truncation. Treating the displayed bin label as the event itself ignores the continuous sensor values that map into that label and introduces the same systematic pricing error on every affected trade.

Those semantics are represented as typed resolution and outcome-space objects. The bins must form a complete, non-overlapping partition before a probability vector can become executable; normalizing an incomplete subset would quietly redistribute omitted tail mass into the contracts that happened to be present.

## Evidence has a clock

Forecast inputs come from an ECMWF anchor and decorrelated global or regional model families obtained through ECMWF Open Data and Open-Meteo, with official national feeds used where they carry relevant station evidence. Observations and settlement evidence come from Weather Underground, METAR, the Hong Kong Observatory, and Taiwan's Central Weather Administration. Market topology, books, orders, and fills come from Polymarket's market and CLOB interfaces.

Every usable record carries more than one time:
- when the source cycle was issued;
- when the source made it available;
- when Zeus captured it;
- when the derived artifact was computed;
- when the decision was made.

A provider value can enter a decision only when its publication and capture evidence are no later than the decision boundary. Missing, empty, malformed, or future availability is exclusion, not permission. Comparing only database write times would allow an upserted row to look causal even when the underlying forecast did not yet exist.

The reader selects a coherent source-run, coverage, readiness, and snapshot bundle rather than taking the latest row from each table independently. A newer forecast cycle can begin after the part of the local day capable of producing the settlement extreme; selecting it merely because it is newer can displace the older cycle that still contains the physically relevant window.

Current provider values remain unshifted. Settled residual history estimates provider trust and covariance; it neither subtracts a historical mean from the served center nor sets the served predictive width. A center that moved with history would create separate entry and monitoring beliefs whose probabilities no longer describe the same forecast.

Heavy forecast acquisition runs in the data plane, outside the trading daemon. Materialization consumes already downloaded artifacts on a lighter path. Keeping large downloads in the money process once allowed disk and database pressure from data acquisition to starve market evaluation and risk dependencies.

## Forecast to probability

### Fuse evidence that co-occurred

Let $`x`$ contain the current provider values, let $`(\mu_0,\tau_0^2)`$ be the ECMWF anchor, and let $`\Sigma`$ describe historical provider error:

```math
V^* =
\left(
\tau_0^{-2} + \mathbf{1}^{\mathsf T}\Sigma^{-1}\mathbf{1}
\right)^{-1}
```


```math
\mu^* =
V^*
\left(
\tau_0^{-2}\mu_0 +
\mathbf{1}^{\mathsf T}\Sigma^{-1}x
\right)
```


Provider aliases or resolutions known to represent the same underlying forecast family are collapsed before fusion. Counting two versions of the same signal as independent evidence narrows the posterior without adding information.

Covariance rows are aligned on the intersection of actual target dates shared by the participating providers. When enough common-date history exists, the sample covariance is shrunk toward its diagonal with a Ledoit–Wolf estimator; otherwise the system falls back toward diagonal uncertainty. Equal-length positional arrays are not sufficient: they can pair one model's error from Monday with another model's error from Tuesday, manufacture correlation, and let a numerically stable $`\Sigma^{-1}`$ amplify a relationship that never occurred.

A grid forecast is localized to the settlement station. Distance, elevation mismatch, and source geometry contribute representativeness variance to that provider's uncertainty before fusion. Hand-adjusting the provider weight separately would count the same location defect twice.

### Construct the predictive distribution

The fused center is not enough. The served width is current evidence, not history: the member spread of the same-cycle causal ECMWF ensemble for the exact target, the disagreement between providers at the decision instant, and the displacement between the ensemble and fused centers, combined in quadrature. A missing or stale ensemble shape blocks the live posterior rather than falling back to a historical residual, a constant width, or a fitted floor — walk-forward residual history informs trust and covariance upstream, never the served width itself.

Open or thin-history sources enter at conservative prior precision and sharpen only as settled evidence accrues. Excluding a new source until an arbitrary sample count is reached throws away information; trusting its first few residuals at full strength lets chance dominate the center.

The resulting predictive distribution carries its provider identities, member envelope, center method, uncertainty components, timing evidence, and a deterministic identity hash. Ineligible paths still return the same receipt shape with a typed refusal reason, rather than switching to an undocumented fallback probability regime.

### Integrate the settlement rule

For a half-up integer bin $`k`$, the probability is integrated over the values that actually settle to $`k`$:

```math
P(X_{\text{settled}}=k)
=
\Phi\!\left(\frac{k+0.5-\mu}{\sigma}\right)
-
\Phi\!\left(\frac{k-0.5-\mu}{\sigma}\right)
```


For Hong Kong's truncation, the corresponding preimage is:

```math
P(X_{\text{settled}}=k)
=
\Phi\!\left(\frac{k+1-\mu}{\sigma}\right)
-
\Phi\!\left(\frac{k-\mu}{\sigma}\right)
```


Closed ranges sum the integer preimages they contain. Open shoulders integrate one continuous tail. Evaluating density at a label or treating a two-degree Fahrenheit range as a point discards probability mass that the contract will pay on.

Once the target day is underway, the distribution is conditioned on the running observed extreme:

```math
Y_{\text{high}}=\max(Y_{\text{observed}},Y_{\text{remaining}})
```


```math
Y_{\text{low}}=\min(Y_{\text{observed}},Y_{\text{remaining}})
```


For a high, probability below the observed maximum collapses onto that maximum; for a low, probability above the observed minimum collapses onto that minimum. Merely moving the mean would leave mass on outcomes that have become physically impossible.

## Probability to action

### Admission is empirical and family-wide

The integrated bin masses form one normalized joint distribution $`q`$ over the complete outcome space $`\Omega`$. Parameter draws are each normalized before forming a coherent uncertainty band, so the lower tail remains a distribution rather than a collection of independently pessimistic bin estimates whose mass no longer sums to one.

A selection calibrator then asks a different question from parameter uncertainty: among prior settled candidates with this side, lead bucket, bin class, and raw probability bucket, how often did the executable claim pay? The admission probability is bounded by a one-sided Wilson lower interval. Thin cells cascade to the narrowest predefined pool clearing the sample floor; absent or stale calibration evidence fails closed. Using an unconstrained cell would promote noise, while banning every thin cell would prevent the system from learning where new evidence belongs.

Benjamini–Hochberg control runs separately within each market over every hypothesis tested in that family. A missing p-value is a hard error. Running the procedure only on candidates that survived earlier filters would select on the same evidence the correction is meant to control and understate the false-discovery burden.

### Price the payoff, not the label

For an executable route $`r`$, let $`a_r`$ be its payoff vector over every outcome in $`\Omega`$, and let $`c_r(s)`$ be its all-in executable cost at stake $`s`$. The point fair value is:

```math
v_r = q^{\mathsf T}a_r
```


The conservative edge is computed from the payoff under coherent probability draws $`Q`$:

```math
\operatorname{edge}^{-}_r(s)
=
\operatorname{Quantile}_{\alpha}
\left(Qa_r-c_r(s)\right)
```


A YES claim has a one-hot payoff. A NO claim pays on every sibling outcome. A bundle has its own vector. Reducing all three to a scalar bin probability minus a displayed price misprices the claim whenever its payoff extends beyond one outcome.

Capital is chosen against outcome-contingent wealth. If $`A_y`$ is portfolio wealth before the candidate under outcome $`y`$, and $`R_{yr}(s)`$ is the route's return at stake $`s`$, then:

```math
\Delta U_r(s;q)
=
\sum_{y\in\Omega}
q_y
\left[
\log\!\left(A_y+R_{yr}(s)\right)-\log(A_y)
\right]
```


```math
s_r^*
=
\arg\max_s
\operatorname{CVaR}_{\alpha}
\left(
\Delta U_r(s;Q)
\right)
```


The stake objective is the lower-tail CVaR — the mean of the worst $`\alpha`$-fraction of draws — rather than the raw quantile: each draw's utility is concave in the stake, and the lower-tail CVaR of concave functions stays concave, so the optimizer can certify a global optimum where a quantile objective cannot. The selected route must have positive lower-tail edge, positive utility at the venue minimum, and positive optimized lower-tail utility. Its cost is read from the same depth curve at the chosen stake. Independent binary Kelly sizing can allocate the same bankroll repeatedly across mutually exclusive siblings, and pricing the final size from top-of-book can erase the edge that selected it.

The optimizer may evaluate broader payoff geometry, but the current live actuator accepts only implemented direct native routes. A synthetic or multi-leg optimum becomes a typed no-trade; silently mapping it to one order would execute a different payoff from the one that won selection.

A two-rail data-density discount remains outside the statistical posterior. The absolute rail stops entry when station coverage is indefensible. The relative rail compares current density with a low percentile of the city's own history. A rolling mean is not a safe baseline here: as a station degrades gradually, the mean degrades with it and converts a sustained failure into the new normal. Missing or non-finite density evidence sizes to zero.

## Positions and exits

Entry and exit are evaluations of the same current state at different sides of a position. A held claim is compared with the value of continuing to hold it, the proceeds available from exiting it, current uncertainty, time remaining, fees, liquidity, and the portfolio that would remain afterwards.

Entry price is not part of the exit decision. Two positions with identical present wealth, holdings, posterior, executable proceeds, and time-to-settlement have the same optimal action even if they were acquired at different prices. A stop-loss keyed to cost basis reacts to past luck rather than current opportunity and can preserve a bad hold merely because it was bought cheaply.

Observed day-of extremes can make a claim's payoff impossible before the market formally resolves. Those hard facts can authorize an exit from a dead claim, but a finite bin containing the current extreme is not treated as settled while later observations can still move the final value out of it.

## Execution and reconciliation

Entries begin as post-only limit orders and may cross as FOK or FAK only after a deadline and a full fresh redecision. The recapture rebuilds native depth, reprices the selected stake, and reranks the family. Checking only whether the latest quote remains below an old ceiling can submit a candidate whose depth-adjusted utility or sibling rank has already reversed.

Every order has a deterministic idempotency key, and its local command intent is durable before the venue is contacted. A submission timeout enters an explicit unknown state until venue evidence resolves it. Blind retry can place the same order twice when the first request landed; assuming failure can erase an order that is already live.

Order state is reduced by strength of evidence rather than arrival order. A confirmed trade or fill outranks an open-order poll, which outranks a local submission acknowledgement. Last-write-wins reduction would allow a delayed, weaker read to overwrite a stronger fact and reopen filled quantity.

Partial fills preserve their executed quantity and unresolved remainder. Exit orders use partial-fill-capable behavior where an all-or-none instruction would reject the entire risk reduction against a thin book.

Venue reconciliation follows three phases:
1. read a bounded local snapshot and close the database connection;
2. perform venue or chain I/O with no writer connection held;
3. open a fresh connection and apply one bounded transaction.

Holding a SQLite writer across network I/O turns a slow external dependency into database-wide write starvation. It can block position, collateral, and risk-state updates even when the reconciliation logic itself is correct.

## State and learning

World facts, forecast facts, and trading facts live in separate SQLite databases. A machine-readable table-ownership registry assigns every canonical table to one database and names sanctioned readers and writers. A table merely existing in SQLite does not make it valid authority; an undeclared copy in another database can become a ghost surface whose readers and migrations silently diverge.

Cross-database writes are confined to the sanctioned helpers that group them within one connection's bounded transaction. Independent commits would expose intermediate states in which, for example, a command exists without its position attribution or a settlement grade exists without the certificate it claims to judge.

Local holdings are projections over immutable command, order, trade, balance, and chain facts. Reconciliation distinguishes a complete empty snapshot from a missing or stale snapshot, and it surfaces venue or chain inventory with no matching Zeus intent instead of adopting it silently. Unexplained inventory is quarantined behind a scoped entry block until it is reconciled.

Each decision receipt binds the forecast identity, probability vector, uncertainty basis, route, payoff-vector hash, executable cost, stake, and decision result. Derived fields such as probability mass and member envelopes are computed from the same arrays used by the decision; accepting them as free metadata would allow a receipt to describe a different calculation from the one that moved capital.

After settlement, a position is attributed to one of six outcomes:
- `SKILL_WIN`
- `LUCKY_WIN`
- `SKILL_LOSS`
- `MISCALIBRATED_LOSS`
- `STALE_DECISION`
- `UNATTRIBUTABLE_Q_MISSING`

This taxonomy governs performance attribution and claims about strategy skill. Probability calibration uses every causally eligible decision with a resolvable frozen probability joined to verified settlement, not only rows later labeled as skill; missing-probability settlements remain counted coverage failures, never reconstructed. Treating a lucky win as evidence of forecasting skill corrupts attribution; filtering the reliability sample by an outcome-dependent skill label would corrupt calibration in the opposite direction.

For a scoreable entry, the decision probability comes from the immutable certificate attached to it. A time-reconstructed posterior is diagnostic only. One earlier staleness predicate was rejected after its own audit showed that the supposedly fresher forecasts were generally computed after the decisions they were used to condemn. Superseded grades are archived rather than overwritten so the old predicate and the repaired one remain distinguishable.

[`docs/reference/calibration_report.md`](docs/reference/calibration_report.md) is regenerated from settled attribution records. It includes reliability bins, Wilson intervals, Brier decomposition, and cuts by lead time, side, strategy, and attribution class. The current corpus contains several hundred scoreable settled positions, with missing-probability settlements counted as coverage failures: enough to examine calibration with explicit uncertainty, not enough to establish durable net alpha. Capital exposure is in the low four figures, and the account contains unrelated inventory, so aggregate account PnL is not presented as a clean return series for this engine.

Drift may arm a retraining candidate, but it cannot promote one. Promotion requires an operator-controlled gate, identity-complete settled evidence, and a passing frozen replay; failed candidates remain versioned evidence rather than replacing the active model. Allowing the same process that detects drift to fit and deploy its own remedy would turn a noisy diagnostic into an automatic money-path change.

## Trading modes

The live modes are timing and information variants of one decision law: acquire the claim with the best current lower-tail utility, then sell before that utility reverses or hold through settlement.
| Mode | Information entering the same decision |
|---|---|
| Forecast Q-Kernel Entry | the current full predictive distribution against the executable family |
| Center Bin Buy | the modal claim when its executable price remains below supported value |
| Day-0 Nowcast Entry | the running observed extreme and the remaining-day distribution |
| Settlement Capture | a late-day extreme that has become difficult or impossible to displace |
| Imminent Open Capture | newly available near-settlement contracts |
| Opening Inertia | early liquidity before the book has fully incorporated current evidence |

Additional registered modes do not receive live permission merely by existing in configuration. Registration names a strategy; it does not prove an edge or authorize capital.

## Engineering contracts

Fail-closed gates enrolled in the architecture registry must declare three facts beside the source condition: their scope, what drains the blocked state, and what resets it. Tests locate each gate through a must-be-unique source anchor and fail when the anchor is renamed, moved, duplicated, or stripped of its declaration. A comparison that can turn true but has no proven route back to false is a ratchet, not a gate.

Money-path objects and tests are registered by semantic role. CI derives the required safety surface from the changed objects and fails closed when a new money-path object is not classified. Running a large generic suite without knowing which execution relationship changed can produce a green result while missing the one boundary that matters.

Relationship tests cross module seams rather than stopping at helper functions. Forecast-bundle tests use distinct runs and coverage objects; submit tests drive proof, executable curve, family rerank, and typed abort state; covariance tests use equal-length but date-misaligned histories. A mock that reproduces the implementation's own assumption cannot expose the assumption as the defect.

## Development

Coding agents participate in implementation, testing, investigation, documentation, and review, but generated output is proposal material rather than authority.

The unattended improvement lane has three capability tiers:
- `AUTO` may change the loop, documentation, and tests within a bounded allowlist.
- `PREPARE` may read source and produce a patch plus a red test, but cannot write production source.
- `NEVER` covers deployment, live databases, risk posture, capital controls, and operator-owned configuration.

The operating-system sandbox denies writes to production source, architecture authority, `.git`, live state databases, and paths outside the declared workspace; network access is also denied. After the agent exits, a separate wrapper reloads the allowlist from the immutable base commit, compares against the pre-run co-tenant baseline, rejects symlink and rename escapes, applies a 20-file and 600-line circuit breaker, and commits only explicit permitted paths. Letting the model report its own compliance would make the actor being constrained the authority on whether the constraint held.

Interactive coding agents use linked worktrees and the same review law, but a worktree is workflow isolation rather than a security boundary. Linked worktrees share the repository's ref store; an agent once used a ref-mutation command from an isolated worktree and briefly moved the branch checked out by the live tree. The exact prior ref was restored and the incident is documented in [`AI_ASSISTANCE.md`](AI_ASSISTANCE.md). The remaining shared-ref escape is published rather than being hidden behind a claim that file isolation also isolates repository authority.

Architecture, research assumptions, merge acceptance, deployment, and capital-risk decisions remain human-owned. Deployment is an explicit operator action, not an agent capability.

Further documentation and source paths:
- [`AGENTS.md`](AGENTS.md) — repository operating law
- [`REVIEW.md`](REVIEW.md) — review doctrine and evidence standards
- [`AI_ASSISTANCE.md`](AI_ASSISTANCE.md) — agent roles, control failures, and open limits
- [`docs/reference/theory_map.md`](docs/reference/theory_map.md) — mathematical derivations and their implementations
- [`docs/reference/calibration_report.md`](docs/reference/calibration_report.md) — settled reliability evidence
- [`loop/README.md`](loop/README.md) — unattended improvement-loop design
- [`src/forecast/`](src/forecast) — forecast construction and uncertainty
- [`src/calibration/`](src/calibration) — calibration, drift, and promotion controls
- [`src/decision/payoff_vector.py`](src/decision/payoff_vector.py) — family payoff economics and robust stake selection
- [`src/execution/exit_lifecycle.py`](src/execution/exit_lifecycle.py) — exit lifecycle
- [`architecture/`](architecture) — invariants, negative constraints, ownership, and test topology

### License

The source is available for reading and review. It is not licensed for use, modification, or redistribution; see [`LICENSE`](LICENSE). The repository is not an open-source contribution project; see [`CONTRIBUTING.md`](CONTRIBUTING.md).
