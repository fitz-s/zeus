# Zeus Prediction-Market Quant Reference

Status: canonical durable reference  
Authority rank: reference explanation. Executable source, tests, manifests, DB/runtime receipts, and `docs/authority/**` outrank this file.  
Freshness model: durable concepts and code anchors. Runtime facts such as loaded SHA, PID, bankroll, open positions, current rejection counts, and active packet state expire and do not belong here.

---

## 0. Reading Contract

This file is the canonical one-stop reference for Zeus as currently implemented: a live-money Polymarket weather prediction-market trading engine.

Every section labels claims as one of:

- **Executable current behavior** — implemented in current source/config/manifests.
- **Durable architecture law** — required by authority/manifests even if multiple modules participate.
- **Reference explanation** — explanatory math/domain model for agents.
- **Current operational fact** — belongs elsewhere; this file only names the pointer.
- **Historical evidence** — useful provenance but not current law.
- **Ideal target / not asserted implemented** — future shape; not live authority.

If this file and code disagree, believe code and patch this file.

---

## 1. System Identity

**Durable architecture law.** Zeus trades discrete weather settlement contracts on prediction markets. It does not trade continuous weather values and it does not trade generic stock-like assets.

A Zeus trade is defined by:

```text
city + local settlement date + metric(high|low) + settlement unit/rule
+ venue condition/market topology + bin + native side(YES|NO)
+ executable route + limit/cost/depth + size + lifecycle identity
```

A family is one complete mutually-exclusive Ω. A family contains all sibling bins for one city/local-date/metric/market-set. The family, not an isolated bin, is the selection and risk object.

A candidate is not just `(q - price)`. A live candidate is:

```text
ForecastCase
+ OutcomeSpace Ω
+ Instrument(YES_i or NO_i)
+ CandidateRoute(native/direct or permitted route)
+ ExecutableCostCurve / RouteCost
+ CandidateEconomics(point_ev, edge_lcb, robust ΔU, stake)
+ direction-law proof
+ market-coherence proof
+ risk/capital/freshness proof
```

Primary implementation anchors:

- `src/main.py` — trading daemon.
- `src/engine/event_reactor_adapter.py` — event-driven live family decision and submit-seam reproofs.
- `src/engine/qkernel_spine_bridge.py` — q-kernel cutover bridge from reactor to family decision engine.
- `src/decision/family_decision_engine.py` — terminal q-kernel family decision orchestrator.
- `src/data/replacement_forecast_materializer.py` — live posterior/q materialization.
- `src/forecast/bayes_precision_fusion.py` — multi-model Bayesian precision fusion.
- `src/calibration/emos.py` — settlement-preimage bin probability integration.
- `src/strategy/live_inference/direction_law.py` — live direction-law helper.
- `src/decision/payoff_vector.py` — instrument/route/payoff economics.
- `src/strategy/utility_ranker.py` — robust family payoff/exposure utility geometry.
- `src/execution/executor.py`, `src/venue/**`, `src/state/venue_command_repo.py` — execution side-effect boundary.
- `src/state/lifecycle_manager.py`, `src/state/portfolio.py`, `src/state/chain_reconciliation.py` — lifecycle and truth projection.

---

## 2. Truth Hierarchy

**Durable architecture law.** Prediction quality is downstream of contract truth. The ordering is:

1. settlement contract/source/rounding/bin topology;
2. source availability and forecast provenance;
3. predictive distribution and q over Ω;
4. conservative q band/q_lcb/q_ucb;
5. executable venue book, side, depth, fees, tick, fill mode;
6. candidate economics, robust utility, size, risk;
7. command persistence and venue side effect;
8. fill/lifecycle/settlement truth;
9. learning and attribution.

A downstream component may not infer upstream truth. Examples:

- A market title does not prove settlement station/source without current source evidence.
- A successful API read proves endpoint availability, not settlement correctness.
- A market quote is not a probability authority.
- A NO quote is not `1 - YES` unless a narrow code-authorized maker reservation bound uses it only to lower a resting limit.
- A backtest is not live permission.
- A current-state note is not durable architecture.
- A packet closeout is evidence, not law.

Machine anchors:

- `architecture/fatal_misreads.yaml` for shortcut antibodies.
- `architecture/negative_constraints.yaml` for forbidden seams and carve-outs.
- `architecture/db_table_ownership.yaml` for table/DB truth.
- `architecture/runtime_modes.yaml` and `runtime_posture.yaml` for runtime grammar/posture.
- `architecture/money_path_objects.yaml` for economic objects and state-machine vocabulary.

---

## 3. Domain Model

### 3.1 Family And Ω

**Reference explanation, enforced by code/manifests.** Ω is the settlement outcome space for one family. Each bin is a member of Ω. The family must be complete enough that Zeus can compute probability mass, payoff vectors, exposure, and mutually-exclusive risk.

Minimum family identity:

```text
canonical city name / aliases
local target date
metric: high or low
unit: C or F
settlement rounding rule
condition_id / market ids / token ids
bin topology
family_id / topology hash / dependency hash
```

### 3.2 Bin Types

**Reference explanation.** Zeus supports:

- `point`: resolves on one integer/settled value.
- `finite_range`: resolves on a finite set of integer/settled values.
- `open_shoulder`: resolves on an unbounded tail.

Open shoulders are not finite ranges with missing endpoints. They accumulate probability over unbounded tails and can dominate mass even when they are not the forecast settlement bin. Direction law therefore uses the bin containing the served center, not necessarily the largest-mass bin.

### 3.3 High/Low Dual Track

**Durable architecture law.** HIGH and LOW are separate metric tracks. They may share city/date geometry, but they do not share:

- settlement physical quantity;
- observation field;
- Day0 running-extreme interpretation;
- calibration family;
- replay identity;
- market topology history;
- source validity evidence;
- attribution slice.

Any code/doc that silently defaults to high for low-track work is wrong.

### 3.4 Native YES/NO

**Executable current behavior and durable law.** YES_i and NO_i are native venue instruments.

Payoffs over Ω:

```text
YES_i(ω) = 1 if ω = i else 0
NO_i(ω)  = 0 if ω = i else 1
```

NO_i is a basket payoff over all other outcomes. It has its own token, quote, depth, route, fill, and exposure. It must not be priced, sized, or admitted by casually computing `1 - YES_price` or `1 - q_lcb_yes`.

Allowed conservative q-band identity:

```text
q_lcb_no_i = 1 - q_ucb_yes_i
```

Forbidden:

```text
q_lcb_no_i = 1 - q_lcb_yes_i
```

Reason: the latter turns a lower bound on YES into an overconfident lower bound on NO.

---

## 4. Forecast And Probability Pipeline

### 4.1 Current Executable Path

**Executable current behavior.** The current live replacement/q-kernel path is:

```text
raw_model_forecasts
  -> Bayesian precision fusion (mu*, sd)
  -> predictive distribution / sigma authority
  -> settlement-preimage integration over Ω
  -> point q + q_lcb/q_ucb maps
  -> q-kernel family decision
```

Implementation anchors:

- `src/data/replacement_forecast_materializer.py` creates/persists live posterior rows and q carriers.
- `src/forecast/bayes_precision_fusion.py` fuses model members into posterior center/dispersion.
- `src/calibration/emos.py::bin_probability_settlement` integrates predictive distributions into settlement-bin probabilities.
- `src/engine/qkernel_spine_bridge.py` adapts reactor-native proofs into `FamilyDecisionEngine` inputs.
- `src/decision/family_decision_engine.py` consumes predictive distribution, Ω, q, q band, book, route/payoff, and selection.

### 4.2 Raw Model Forecasts

**Executable current behavior.** `raw_model_forecasts` lives in the forecast DB and records model/product/request identity. Rows carry physical product identity: model, provider, product id, request URL hash, source cycle, endpoint, cell selection, requested coordinates/timezone/elevation, and forecast value in Celsius.

This prevents a stored numeric forecast from being treated as interchangeable across Open-Meteo endpoint/product/cell/timezone changes.

DB ownership anchor: `architecture/db_table_ownership.yaml`.

### 4.3 Bayesian Precision Fusion

**Executable current behavior.** `src/forecast/bayes_precision_fusion.py` owns the multi-model fusion math. The current durable interpretation is:

- model members are treated as decorrelated/in-domain forecast instruments where available;
- empirical-Bayes residual bias and date-aligned residual history inform precision;
- covariance uses shrinkage where enough common dates exist and diagonal fallback otherwise;
- output is a fused posterior center and dispersion, not a market prior;
- fallback modes must be explicit and fail-soft/fail-closed at the next admission layer when live eligibility is absent.

Reference equation:

```text
Given member vector x and residual covariance Σ,
precision-weighted posterior center mu* combines members by inverse covariance.
Posterior variance V* is the fused uncertainty after residual/covariance treatment.
```

This equation is explanatory. The executable implementation and its guards live in `src/forecast/bayes_precision_fusion.py`.

### 4.4 Settlement-Preimage q

**Executable current behavior.** q is computed over settlement outcomes, not continuous weather values. The integrator must apply the family settlement rounding/transform and bin topology.

Reference form:

```text
q_i = P(settle(Y) in bin_i | predictive distribution, city/metric/date/rule)
```

`src/calibration/emos.py::bin_probability_settlement` is the settlement-preimage integrator used by the live q path.

### 4.5 q, q_lcb, q_ucb, q_exec_lcb

**Reference explanation and executable law where implemented.**

- `q`: point settlement probability over Ω.
- `q_lcb`: conservative lower bound for the same probability or payoff random variable.
- `q_ucb`: matching upper bound.
- `q_exec_lcb`: when present, a candidate/payoff/execution-space conservative value after side/payoff transformation and reliability guard. Do not assume this field exists globally; inspect the code path.

Lower-bound coherence rule:

```text
For the same random variable X, LCB(X) <= E[X] <= UCB(X)
```

A lower bound greater than the point estimate is invalid unless the code explicitly proves it is a lower bound on a different random variable. Example: a candidate payoff lower bound may be compared to candidate payoff point EV, not blindly to a same-bin YES probability if the side/payoff changed.

### 4.6 Live Eligibility

**Executable current behavior.** A predictive distribution that lacks required sigma/source/q authority is not integrated into q for live decisions. `FamilyDecisionEngine.decide()` returns `PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE` before q when `predictive.live_eligible` is false.

Materializer live q carrier rows are similarly gated: q mode, q_lcb/q_ucb maps, source cycle, dependency/provenance, and configured flags must support execution authority.

### 4.7 Legacy Probability Surfaces

**Historical evidence / diagnostics unless code proves otherwise.** Legacy ENS/Platt/market_fusion, market-anchor caps, old q_lcb_5pct bootstrap doctrine, and dated replacement papers are not default current law. They can remain as:

- receipt provenance;
- diagnostics;
- tests for rollback parity;
- historical reports;
- non-default references for a task that explicitly names them.

They cannot gate, cap, blend, or override the live q path unless current source/config/manifests show an active executable seam.

---

## 5. q-Kernel Decision Pipeline

### 5.1 Terminal Orchestrator

**Executable current behavior.** `src/decision/family_decision_engine.py::FamilyDecisionEngine.decide()` is the terminal q-kernel family decision orchestrator. It assembles, not re-implements, the spine modules.

Current order:

```text
case / Ω / snapshots / portfolio / sizing inputs
  -> fresh model read
  -> Day0 observation read
  -> predictive_builder.build()
  -> live eligibility gate
  -> build_joint_q(predictive, Ω)
  -> build_joint_q_band(predictive, Ω)
  -> family_book_from_snapshots(Ω, snapshots)
  -> market_implied_q / market_coherence
  -> route set
  -> enumerate direct YES and dominant NO routes
  -> build payoff vector / candidate economics
  -> q_lcb reliability guard
  -> direction law
  -> coherence gate
  -> edge_lcb > 0 and robust ΔU > 0
  -> select by robust utility density
  -> FamilyDecision receipt hash
```

No-trade outcomes are typed and auditable. A no-trade is a valid `FamilyDecision`, not an exception path.

### 5.2 Bridge From Reactor

**Executable current behavior.** `src/engine/qkernel_spine_bridge.py` is the single bridge from `event_reactor_adapter` into the q-kernel spine when `settings["feature_flags"]["qkernel_spine_enabled"]` is true. When false or config read fails, the legacy path is rollback/compatibility behavior.

The bridge replaces decision computation, not submit machinery. Reactor downstream gates still own risk, freshness, MECE fail-closed checks, pre-submit witness, command persistence, venue submission, and receipts.

### 5.3 Direction Law

**Executable current behavior and durable law.** Direction law is based on the forecast settlement bin: the bin where the served center settles under the family's rounding rule.

Rules:

```text
YES_i legal iff i == forecast_settlement_bin
NO_i legal iff i != forecast_settlement_bin
```

Boundary-zone logic prevents buying NO on a bin the rounded center materially straddles. Non-modal YES is illegal even when tail q exceeds a heuristic floor.

The q-kernel has a narrow empirical OOF reliability license for certain NO harvest cases. That license is NO-only and cannot admit non-modal YES.

### 5.4 Market Coherence

**Executable current behavior.** Market coherence is a typed incident report, not a q mutation. When the report blocks live for an offending bin and no empirical model-superiority license applies, candidates for that bin are removed before edge/ΔU selection. The q itself is not clamped to market price.

### 5.5 Edge And Payoff Economics

**Executable current behavior.** Candidate economics are payoff-vector economics over Ω.

Reference form:

```text
point_ev   = q · payoff - cost
edge_lcb   = conservative_q_payoff_lcb - cost
ΔU(stake)  = E_conservative[log(bankroll + payoff(stake) - cost(stake) + current_exposure)]
```

The exact executable formula, cost representation, and stake search live in `src/decision/payoff_vector.py` and `src/strategy/utility_ranker.py`.

A candidate passes selection only after:

```text
route executable
AND direction-law admitted
AND market-coherence allowed
AND edge_lcb > 0
AND optimal_delta_u > 0
AND live_candidate_passes(...)
```

### 5.6 Selection Objective

**Executable current behavior.** Default live selection chooses the survivor with maximum robust utility density:

```text
argmax optimal_delta_u / optimal_stake_usd
```

Tie/secondary order uses total `optimal_delta_u`, `edge_lcb`, and lower cost. Scalar `trade_score`, `q - price` telemetry, and old opportunity-book ranks are not selection authorities.

---

## 6. Sizing, Risk, And DATA_DEGRADED

### 6.1 Executable Price Kelly

**Durable architecture law and executable contracts.** Sizing must use executable price/cost curves. Bare display price, market midpoint, implied probability, or old `entry_price` values cannot satisfy corrected Kelly authority.

`architecture/negative_constraints.yaml` forbids:

- bare static entry price at Kelly boundaries;
- implied-probability `ExecutionPrice` as executable-cost authority;
- final execution intent carrying posterior/edge/recompute inputs.

### 6.2 Robust Utility Sizing

**Executable current behavior.** q-kernel candidate sizing is robust expected log utility under family payoff/exposure geometry. It is not independent scalar Kelly per bin.

Inputs include:

- payoff vector over Ω;
- candidate side and route cost;
- q band / conservative payoff bound;
- current family/portfolio exposure;
- max stake / capital/risk bound;
- executable depth/cost curve.

### 6.3 Risk Behavior

**Durable law.** Risk is behavioral, not advisory.

| Level | Behavior |
|---|---|
| GREEN | normal admission if all other gates pass |
| YELLOW | block new entries; continue monitoring |
| ORANGE | block new entries; exit only under favorable/policy-authorized conditions |
| RED | protective cancel/sweep/exit behavior per code |
| DATA_DEGRADED | no new entries; preserve held-position monitor/exit/reconciliation lanes where safe |

Missing/stale source, q, book, heartbeat, balance/allowance, user-channel, chain, readiness, or current-fact evidence must become typed no-trade/no-submit/reduce-only behavior, not fabricated inputs.

`src/riskguard/**` owns protective risk. `src/risk_allocator/**` can block, reduce-only, force FOK/FAK, or summarize allocation; it must not bypass execution/venue command boundaries.

---

## 7. Execution Boundary

### 7.1 Executor Law

**Executable current behavior.** `src/execution/executor.py` is limit-order-only. It routes live execution through venue adapters when `ZEUS_MODE=live` and through shadow/paper/replay executors otherwise.

Key current laws:

- no market orders;
- command/idempotency checks before side effects;
- cutover, heartbeat, WS gap, risk allocator, and collateral preflight checks before SDK contact;
- share quantization: buy rounds up, sell rounds down where executor code applies it;
- deterministic Polymarket 400/403 classes are submit rejections, not unknown side effects;
- unknown side effects cannot be retried as empty.

### 7.2 Pre-Submit Witness

**Executable current behavior.** The event reactor constructs a pre-submit authority witness containing quote time, book hash, current bid/ask, tick, min order size, neg-risk flag, heartbeat/user-channel/venue/balance status, and checked-at/freshness metadata. The JIT pre-submit book can be persisted as `JIT_PRESUBMIT` provenance before the submit path consumes it.

A final submit must not silently recompute probability, edge, or size. It may validate mode/price/freshness/collateral and abort to typed no-submit/re-rank states.

### 7.3 Command Persistence And Venue Truth

**Durable architecture law.** Durable command truth must precede venue side effects. `src/state/venue_command_repo.py` owns `venue_commands` and `venue_command_events`; direct mutation outside that seam is forbidden.

Venue truth hierarchy:

```text
Polymarket CLOB / chain / user-channel facts
  -> venue command/events and trade facts
  -> position event/projection
  -> status/export/report
```

---

## 8. Lifecycle, Monitor, Exit, Settlement, Learning

### 8.1 Lifecycle Phases

**Durable law and manifest-backed grammar.** Canonical lifecycle phases:

```text
pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled
```

Terminal/recovery phases:

```text
voided, quarantined, admin_closed, unknown
```

No code or doc may invent alternative phase strings as truth. `closed` may appear in compatibility/exclusion lists, but active lifecycle law is the enum/manifest grammar.

### 8.2 Exit Is Not Settlement

**Executable current behavior and durable law.** `src/execution/exit_lifecycle.py` states the golden rule: confirmed sell fill creates economic close, not settlement. Settlement remains a later harvester-owned transition.

Exit lifecycle internal states include `exit_intent`, `sell_placed`, `sell_pending`, `sell_filled`, `retry_pending`, and `backoff_exhausted`. These are exit module runtime states, not replacements for canonical lifecycle phases.

### 8.3 Monitor And Held Positions

**Executable current behavior, exact trigger thresholds must be inspected in code/current config.** Monitor refresh must use fresh belief and executable exit economics without letting held-token quotes become posterior-prior evidence. Held-token quote observations belong to executable mark/exit economics only.

Same-family exposure handling, fill-up, shift-bin, and family rebalance surfaces exist in strategy/engine modules on this branch. This reference does not assert their full current behavior without the specific code path in scope; future work must inspect `src/strategy/fill_up_wiring.py`, `src/strategy/shift_bin_wiring.py`, `src/strategy/family_rebalance.py`, and the reactor call sites before changing them.

### 8.4 Chain Reconciliation

**Durable law.** Chain/CLOB truth outranks local cache. Unknown chain-only or chain/local mismatch must quarantine/isolate/block new entries according to current reconciliation/risk code; it must not be normalized away.

Do not void a local position from an unknown/stale chain snapshot. Known-empty and unknown are different facts.

### 8.5 Settlement And Learning

**Durable law.** Settlement writes belong to harvester/settlement outcome paths and must use settlement source/rounding/bin topology. Learning consumes settlement/fill truth only when provenance and training eligibility permit.

Replay/backtest results cannot become live authority without settlement-market parity and no-hindsight proof.

---

## 9. Data And DB Topology

**Machine manifest authority.** Canonical table ownership lives in `architecture/db_table_ownership.yaml`.

Current DB classes:

| DB | Role |
|---|---|
| `state/zeus-world.db` | world/runtime records that remain world-owned |
| `state/zeus-forecasts.db` | observations, settlement outcomes, source runs, readiness, raw forecast artifacts, raw model forecasts, forecast posteriors |
| `state/zeus_trades.db` | trade decisions, execution facts, position events/current/lots, venue commands/events, settlement commands |

A table name alone is ambiguous. Ownership is `(table, db)`. Legacy shells may exist and must be registered as `legacy_archived` or equivalent until removed.

Forecast/source facts and trade facts must not be joined through unsanctioned cross-DB write transactions. Use sanctioned connection helpers and manifest-declared ownership.

---

## 10. Runtime/Deploy Topology

**Executable/deploy artifact reference.** `src/main.py` is the trading daemon entrypoint. Current source comments state that K2 ingest jobs have been removed from the trading daemon; ingest lanes are owned by split daemons.

Committed launchd artifacts:

| Artifact | Program | Role |
|---|---|---|
| `deploy/launchd/com.zeus.substrate-observer.plist` | `python -m src.ingest.substrate_observer_daemon` | substrate/market topology/executable snapshot producer |
| `deploy/launchd/com.zeus.price-channel-ingest.plist` | `python -m src.ingest.price_channel_daemon` | price/user-channel/fill-feasibility fact producer |
| `deploy/launchd/com.zeus.post-trade-capital.plist` | `python -m src.ingest.post_trade_capital_daemon` | post-trade chain/harvester/redeem/wrap follow-up lane |

These files are installable artifacts. They do not prove the service is loaded. Loaded state, PID, current SHA, and liveness must come from fresh operator/runtime receipts.

---

## 11. Replay And Backtest Boundary

**Durable law.** A valid replay/backtest for strategy work must model:

- settlement contract identity;
- local date and high/low metric;
- discrete bins and open shoulders;
- source availability at decision time;
- forecast cycle and raw model provenance;
- q/q-band construction as available then;
- executable orderbook, tick, fees, depth, maker/taker/FOK behavior, and fill assumptions;
- family-level selection/exposure;
- command/lifecycle truth;
- settlement-only validation without hindsight leakage.

Backtest may evaluate. Shadow may observe. Neither authorizes live behavior by itself.

---

## 12. Current-Fact Pointers

Use these only when current operational state is necessary:

- `docs/operations/current_state.md` — active packet/current operational pointer.
- `docs/operations/current_data_state.md` — data/DB/source state pointer.
- `docs/operations/current_source_validity.md` — settlement/source validity pointer.

A valid current fact must name evidence, observed_at/checked_at, freshness or expiry, owner path, and stale behavior. If freshness cannot be proven, treat it as unknown.

---

## 13. Failure Modes That Matter To Live Money

1. Continuous-weather thinking for settlement-bin contracts.
2. UTC/local-day mismatch.
3. HIGH/LOW identity leak.
4. Bin topology or shoulder misread.
5. NO complement shortcut in price/q/q_lcb/fill.
6. Stale q, stale source cycle, stale book, stale heartbeat, stale balance/allowance.
7. q_lcb lower-bound inversion.
8. Partial substrate outage treated as complete family.
9. Duplicate submit after unknown side effect.
10. Lifecycle phase hallucination.
11. Exit intent treated as close or settlement.
12. Chain/local mismatch normalized away.
13. Advisory-only risk.
14. Backtest/shadow result promoted to live authority.
15. Packet/consult/report/evidence treated as present-tense law.
16. Current fact copied into durable authority.

Use `docs/reference/zeus_failure_modes_reference.md` and `architecture/fatal_misreads.yaml` for failure-mode review.

---

## 14. Minimal Rebuild Blueprint

A clean-room Zeus-like system must implement these layers in order:

1. **Contract kernel**: typed family/bin/native-side/settlement source/rounding model.
2. **Source kernel**: forecast, observation, settlement, Day0, and historical source roles separated by type and provenance.
3. **DB truth kernel**: table ownership, append-only events, projections, command/event logs.
4. **Forecast kernel**: raw model capture with physical product identity; fusion with residual/covariance authority; explicit live eligibility.
5. **q kernel**: settlement-preimage integration over Ω; coherent q_lcb/q_ucb; side-aware NO bounds.
6. **Decision kernel**: family-level payoff-vector candidate economics; direction law; market coherence; robust utility selection.
7. **Sizing/risk kernel**: executable-cost Kelly/log-utility sizing, exposure vectors, risk levels that change behavior.
8. **Execution kernel**: command persistence before side effect, pre-submit witness, venue adapter, idempotency, unknown-side-effect handling.
9. **Lifecycle kernel**: entry/fill/hold/monitor/exit/economic-close/settlement/redeem/learning with chain truth precedence.
10. **Replay kernel**: settlement-market parity and no-hindsight proof.
11. **Docs/control kernel**: authority/reference/current/evidence isolation, manifest routing, validation commands.

Do not build strategy before contract truth. Do not build execution before command truth. Do not build learning before settlement/fill provenance. Do not let docs or packets become hidden authorities.
