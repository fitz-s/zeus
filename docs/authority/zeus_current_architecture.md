# Zeus Current Architecture Law

Status: active durable architecture authority  
Scope: semantic law for the live-money Zeus weather prediction-market trading system  
Freshness model: durable law. This file names implementation anchors, but it is not a runtime snapshot. Branch posture, loaded SHA, bankroll, open orders, positions, process PIDs, and packet status live in code/config/runtime receipts/current-fact surfaces and expire there.

---

## 0. Authority Rank

When two surfaces disagree, apply this order:

1. executable code, migrations, launchd artifacts actually deployed by the operator, DB schemas, DB ownership manifests, tests/invariants, and runtime receipts;
2. machine-checkable manifests under `architecture/**`;
3. this file and `docs/authority/zeus_current_delivery.md`;
4. canonical durable references under `docs/reference/**`;
5. current-fact pointers under `docs/operations/current_state.md`, `current_data_state.md`, and `current_source_validity.md` while fresh;
6. reports, evidence, packets, consults, rebuild notes, PR reviews, dated audits, transcripts, and archive material.

No packet, consult, PR review, evidence folder, rebuild diary, current-state note, graph output, chat memory, or generated plan can create durable law. If code and prose disagree, believe code, then repair prose. If code behavior is unclear, document the ambiguity; do not invent a stable doctrine.

---

## 1. System Identity

Zeus is a live-money weather prediction-market trading engine for Polymarket-style settlement contracts.

It trades discrete settlement contracts, not continuous weather values and not generic stock-like assets. A Zeus family is the mutually-exclusive market set for one city, one local settlement date, one metric (`high` or `low`), and one venue condition/market topology. Each family contains bins over the settlement value. Bins may be point bins, finite ranges, or open shoulders. The economic asset is a native YES or NO outcome token at a venue price and depth.

The money path is:

`contract/source/settlement truth -> forecast posterior -> q over Ω -> conservative q band -> family book -> route/payoff vector -> direction/coherence/edge/utility gates -> sizing/risk -> execution intent -> venue command -> fill/position lifecycle -> monitor/exit -> settlement/redeem -> learning`

Any shortcut that skips contract identity, settlement source, native side, executable cost, or lifecycle truth is architecture-invalid even if tests around a local helper pass.

Implementation anchors:

- Runtime entrypoints: `src/main.py`, `src/engine/cycle_runner.py`, `src/engine/evaluator.py`, `src/engine/event_reactor_adapter.py`, `src/engine/qkernel_spine_bridge.py`.
- Probability/materialization: `src/data/replacement_forecast_materializer.py`, `src/forecast/bayes_precision_fusion.py`, `src/calibration/emos.py`, `src/probability/**`, `src/forecast/**`.
- Decision: `src/decision/family_decision_engine.py`, `src/decision/payoff_vector.py`, `src/decision/market_coherence.py`, `src/decision/qlcb_reliability_guard.py`, `src/strategy/live_inference/direction_law.py`, `src/strategy/utility_ranker.py`.
- Execution/venue: `src/execution/executor.py`, `src/venue/polymarket_v2_adapter.py`, `src/state/venue_command_repo.py`, `src/engine/event_reactor_adapter.py` pre-submit gates.
- Truth/lifecycle: `src/state/lifecycle_manager.py`, `src/state/ledger.py`, `src/state/projection.py`, `src/state/chain_reconciliation.py`, `src/state/portfolio.py`, `src/execution/exit_lifecycle.py`, `src/engine/monitor_refresh.py`, `src/execution/harvester.py`.
- Deploy topology artifacts: `deploy/launchd/com.zeus.substrate-observer.plist`, `deploy/launchd/com.zeus.price-channel-ingest.plist`, `deploy/launchd/com.zeus.post-trade-capital.plist`, plus the live-trading daemon entry in `src/main.py`.

---

## 2. Contract, Family, Bin, And Native-Side Law

Contract truth precedes probability. A valid decision must know the venue family, local target date, metric, settlement unit, rounding rule, bin topology, and Polymarket condition/token identity before interpreting q, edge, or price.

A family is one mutually-exclusive Ω. Family identity must not be rebuilt ad hoc from strings when typed family or topology identity is available. The durable identity spine is:

- city / canonical city alias;
- local target date, not UTC-only date;
- metric (`high` or `low`);
- settlement unit (`C` or `F`);
- rounding rule (`wmo_half_up`, `oracle_truncate`, `floor`, `ceil`, as allowed by event resolution code);
- condition/event/market ids and token ids;
- bin topology hash and family id.

YES and NO are native venue sides. YES_i pays on bin i. NO_i pays on all settlement outcomes except bin i. NO is not an execution shortcut for `1 - YES price`; it has its own quote, depth, route, token, fill, collateral, and risk exposure. The only allowed probability-space conservative NO lower bound is a certified complement of the YES upper bound, `q_lcb_no = 1 - q_ucb_yes`, produced inside the q-construction seam. `1 - q_lcb_yes` is forbidden because it overstates the NO lower bound.

The exception registered in `architecture/negative_constraints.yaml` is narrow: a complement may bound a maker quote against Polymarket complete-set mint matching when it lowers the resting limit. It may not price edge, fill probability, q, q_lcb, or size.

High and low tracks are distinct physical quantities. They may share local-day geometry and city identity, but they do not share observation field, calibration family, Day0 causality, settlement rebuild identity, replay bin lookup, or metric-specific data version. Any table, model, or reference that implicitly defaults to high is incomplete for low-track work.

---

## 3. Forecast And Probability Authority

### 3.1 Current executable probability path

The current implemented path is not legacy ENS/Platt/market-fusion doctrine. The live replacement/q-kernel path is:

1. Forecast capture and materialization write to `state/zeus-forecasts.db` through `src/data/replacement_forecast_materializer.py` and related replacement forecast readers.
2. Multi-model forecast rows are stored in `raw_model_forecasts`; forecast values are Celsius and carry source/product/request/cell identity.
3. `src/forecast/bayes_precision_fusion.py` owns Bayesian precision fusion: empirical-Bayes residual bias, date-aligned residual covariance, Ledoit-Wolf shrink-to-diagonal Σ when enough common dates exist, diagonal fallback otherwise, and T2 posterior `mu*`/`sd` with fail-soft anchor/equal-weight fallback.
4. `src/calibration/emos.py::bin_probability_settlement` is the settlement-preimage integrator. It maps a predictive distribution and contract rounding/bin topology into q over Ω.
5. `replacement_forecast_materializer` persists point q plus `q_lcb_json` and `q_ucb_json` only when the row satisfies the live q carrier contract: `replacement_q_mode in {FUSED_NORMAL_FULL, FUSED_NORMAL_PARTIAL}`, q_lcb and q_ucb maps present, live q-lcb basis present, and required feature flags true.
6. `event_reactor_adapter` consumes only execution-authority posterior rows and builds candidate proofs with side-aware q/q_lcb.
7. When `settings["feature_flags"]["qkernel_spine_enabled"]` is true, `src/engine/qkernel_spine_bridge.py` routes family decision to `src/decision/family_decision_engine.py`.
8. `FamilyDecisionEngine.decide()` builds the predictive distribution, Ω, joint q, joint q band, family book, market coherence report, route set, payoff-vector economics, and selection receipt.

The bridge states the current single-truth law: the q-kernel center is the raw precise multi-model fused center from persisted model members; the legacy settlement-residual de-bias maze is identity/no-op. If a future commit changes that, code and evidence must update this law.

### 3.2 LCB/certification law

`q` is the point settlement probability over Ω. `q_lcb` is a conservative lower bound for settlement probability or candidate payoff under a defined uncertainty law. `q_ucb` is the matching upper bound. A live candidate may use a lower bound only if it is tied to the same Ω, same contract rounding, same family topology, same source cycle, same model/provenance identity, and same side/payoff semantics as the candidate being priced.

A lower bound that exceeds the point estimate is invalid unless it is explicitly a lower bound for a different random variable with a stated transformation and proof. The historical category `q_lcb_5pct` is not durable authority by name; the executable fact is the current side-aware q-band/certification carried through the decision and proof objects. References may mention `q_lcb_5pct` only as a legacy field name on `_CandidateProof`, not as an independent live law.

Live decisions must be settlement-graded. A replay, calibration claim, q-lcb reliability artifact, or model-superiority license is not live authority unless it respects settlement-market truth, discrete bins, local date, high/low identity, executable orderbook costs, and information availability at decision time.

### 3.3 Diagnostic-only probability surfaces

Legacy ENS, Platt baseline, old `market_fusion`, market-anchor caps, arbitrary haircuts, and dated replacement-final-form papers are not current default-read authority. They may exist as tests, diagnostics, rollback reference, calibration baselines, or archive evidence only when registered that way. They cannot be cited as current probability law without a current code anchor and live gating path.

---

## 4. Decision, Direction, Edge, Selection, And Sizing Law

The executable current decision object is a family-level candidate over `(family, bin, native side, route, executable cost, payoff vector, size)`. A high-probability YES or low-probability NO is not enough. No edge exists until the executable cost, fees, tick, depth, fill mode, and side-specific quote are inside a conservative belief/payoff bound.

The current q-kernel selection contract is:

1. Build the one live-eligible predictive distribution. If not live-eligible, return typed no-trade before integrating q.
2. Build the complete Ω for the family and a normalized joint q over Ω.
3. Build a coherent q band from the predictive distribution and Ω.
4. Build the executable family book from the per-bin book snapshots/proofs.
5. Build direct/native and negative-risk route candidates where executable.
6. Convert each route into a payoff vector and candidate economics.
7. Filter in order: direction law, market coherence, `edge_lcb > 0`, and `optimal_delta_u > 0`.
8. Select the survivor with maximum robust utility density, with total robust ΔU as secondary order. Scalar `trade_score`, q-price telemetry, or market disagreement may be recorded but must not select.

Current direction law:

- `buy_yes`/YES is legal only on the forecast settlement bin: the bin where the served center settles under the family rounding rule. It is modal/forecast-bin only. Non-modal YES is illegal even if a tail q appears positive.
- `buy_no`/NO is legal only when the bin is not the forecast settlement bin, with the boundary-zone law in `src/strategy/live_inference/direction_law.py` preventing NO on bins that the rounded center materially straddles.
- The q-kernel has an empirical OOF reliability license for certain NO harvest cases, but that license is NO-only. It cannot admit a non-modal YES.

Sizing law:

- Sizing is robust expected log-utility / marginal utility over the family payoff matrix, exposure vector, side-specific executable cost curve, and conservative q band.
- Kelly-style sizing is at executable price, not display/mid/implied probability. `ExecutionPrice.assert_kelly_safe()` and `architecture/negative_constraints.yaml` prohibit bare entry-price/implied-probability Kelly seams.
- Fractional multipliers and risk caps constrain stake. They are not second probability models and must not rewrite q.
- `config/settings.json` removes fixed config-bankroll authority and per-trade Kelly hard caps; live capital truth comes from the bankroll provider / venue/collateral/accounting path. A config `max_single_position_pct: 0.0` is disabled, not a zero-dollar cap.

---

## 5. Risk And Degraded-Data Law

Risk must change behavior. Advisory-only risk is forbidden.

Risk levels are behavioral:

- `GREEN`: normal operation.
- `YELLOW`: no new entries; continue monitoring held positions.
- `ORANGE`: no new entries; exit only when favorable or policy-authorized.
- `RED`: cancel/sweep/exit according to the protective lane; computation error fails closed.

`DATA_DEGRADED` is a no-new-entry posture for missing/stale/partial authority. It must not blind monitor, exit, reconciliation, or settlement paths where those can operate safely from already-known truth. Missing forecast, source, q, book, heartbeat, balance/allowance, chain, readiness, or current-fact evidence must become typed no-trade/no-submit/reduce-only behavior, not fabricated inputs.

`src/riskguard/**` owns fast protective risk. `src/risk_allocator/**` may block, reduce-only, or summarize allocation risk; it must not submit, cancel, redeem, or mutate production DB/state. `strategy_key` is the only governance key for strategy-aware risk policy.

---

## 6. Execution And Venue Boundary Law

Execution is the live-money external side-effect boundary.

The execution path must preserve this ordering:

`selected candidate/proof -> risk and freshness re-proof -> pre-submit JIT book/balance/heartbeat/allowance witness -> durable venue command/intent persistence -> adapter/SDK side effect -> venue ack/fill facts -> order truth reducer -> lifecycle event/projection`

Laws:

- Limit orders only. Do not introduce market orders.
- A final execution intent may not carry posterior, p_market, VWMP, edge, market prior, or entry-price recompute inputs. It carries the selected executable limit/order intent and provenance, not enough data to silently re-decide.
- Final-stage maker/taker mode may validate the chosen proof but must not reselect or chase a different mode without a typed abort/re-rank path.
- Pre-submit JIT book, heartbeat, user-channel, venue connectivity, and balance/allowance witnesses are authority checks. If required evidence is missing/stale, fail closed.
- Command persistence must precede side effect. `src/state/venue_command_repo.py` owns `venue_commands` and `venue_command_events`; direct updates outside the repo seam are forbidden.
- Dedupe/idempotency is part of live-money safety. Unknown side-effect states cannot be retried as if empty.
- Price non-repricing is law: a selected maker order rests at its admitted limit unless a typed recapture/redecision path aborts and re-ranks. A taker/crossing order may have a bounded slippage ceiling only where code implements it.

Venue adapter code is the only place Zeus may adapt to Polymarket SDK/API volatility. Other modules must not bypass the adapter, venue command repo, or provenance envelope.

---

## 7. Lifecycle, Monitor, Exit, Settlement, And Learning Law

Canonical truth is append-first:

1. append event to `position_events` / command event log;
2. fold deterministic projection to `position_current` / read model;
3. keep event append and projection update in one transaction boundary where that write path is used;
4. write derived JSON/status only after DB commit.

Legal lifecycle phases are:

`pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled`

Terminal phases: `voided`, `quarantined`, `admin_closed`. Runtime sentinel: `unknown` where the code declares it. No code or doc may invent `holding`, `closed`, `sold`, `redeemed`, or other phase strings.

Exit intent is not economic close. Economic close is not settlement. Settlement/redeem is not an exit order. Held-position monitor refresh may update belief and produce exit intent, but it cannot declare settlement or locally close a position. Chain/CLOB truth outranks local cache:

`Chain / Polymarket CLOB > canonical DB and event log > projection/cache/export`

Void requires known absence, not unknown/stale chain status. Unknown chain-only or local/chain mismatch must quarantine/isolate/block new entries according to reconciliation and risk code; it must not be normalized away for convenience.

Settlement truth writes belong to the harvester / settlement outcome path and must be source/rounding/bin-topology aware. Learning and attribution consume settlement/fill truth only when provenance and training eligibility allow it; backtest/replay results are diagnostic until they prove live parity.

---

## 8. Data, DB, Replay, And Current-Fact Law

Current canonical DB topology is declared by `architecture/db_table_ownership.yaml`:

- world DB: `state/zeus-world.db` for world/runtime classes that remain world-owned;
- forecast DB: `state/zeus-forecasts.db` for observations, source runs, readiness, raw forecast artifacts, raw model forecasts, forecast posteriors, settlement outcomes, and other forecast-class tables;
- trade DB: `state/zeus_trades.db` for trade decisions, execution facts, position events/current/lots, venue commands/events, settlement commands, and trade-class lifecycle truth.

A table name is not enough. Table ownership is `(table, db)`. Legacy shells may remain for compatibility and must stay registered as legacy/archive until removed.

Replay/backtest is not live authority. A valid replay for strategy promotion must model:

- settlement-market contract identity;
- discrete bins and shoulders;
- local date and high/low metric;
- source availability and proof-of-possession timing;
- q and q-band construction as known at the decision time;
- executable orderbook, tick, fees, depth, maker/taker mode, and fill assumptions;
- family-level selection and exposure constraints;
- no hindsight leakage.

Current facts must carry evidence, freshness, and expiry. `docs/operations/current_state.md`, `current_data_state.md`, and `current_source_validity.md` are active pointers only while fresh. They cannot authorize architecture. If stale or unverifiable, write `unknown` or fail closed.

---

## 9. Deploy And Runtime Topology Law

`src/main.py` is the trading daemon entrypoint. It owns trading orchestration only after the system-decomposition split: ingest/data-daemon work is separated from trading. Launchd artifacts under `deploy/launchd/**` are installable operator artifacts, not proof that a service is loaded unless runtime receipts say so.

The committed launchd artifacts define these durable process roles:

- substrate observer: `python -m src.ingest.substrate_observer_daemon`, producer for market substrate / executable snapshots;
- price-channel ingest: `python -m src.ingest.price_channel_daemon`, producer for price/user-channel/fill feasibility facts;
- post-trade capital: `python -m src.ingest.post_trade_capital_daemon`, post-trade chain-sync/harvester/redeem/wrap command follow-up lane.

Do not document a PID, loaded SHA, launchctl status, live bankroll, open order count, active position set, or temporary rejection count here. Those are current operational facts and expire.

Runtime modes and posture are manifest/code facts:

- discovery modes are parameters to a shared CycleRunner path (`architecture/runtime_modes.yaml`);
- branch posture is read-only manifest authority (`architecture/runtime_posture.yaml`);
- EDLI live runtime mode and submission gates are code/config facts, not prose law.

---

## 10. Default-Read And Documentation Isolation Law

Default boot for a zero-context agent may read only:

- root `AGENTS.md`;
- `workspace_map.md`;
- scoped `AGENTS.md` for touched directories;
- `docs/README.md` and `docs/AGENTS.md` for docs work;
- active authority law: this file and `docs/authority/zeus_current_delivery.md`;
- canonical durable references named by `docs/reference/AGENTS.md` and `architecture/docs_registry.yaml`;
- current-fact pointer files only when the task requires current operational state and their freshness/expiry is acceptable.

Default boot must not read by default:

- `docs/evidence/**`;
- `docs/reports/**`;
- `docs/archive/**`;
- `docs/rebuild/**`;
- closed `docs/operations/task_*` folders;
- consult/review/raw/packet/dated authority-history files;
- any file whose registry class is `evidence`, `report`, `archive`, `authority_history`, `packet`, `task`, `transitional`, or `obsolete`.

If a historical file contains a surviving rule, promote the rule here or into a canonical reference, then keep the historical file as evidence only.

---

## 11. Catastrophic Failure Classes

These failures are architecture-relevant because they can move live money:

1. treating a weather market as a continuous weather prediction rather than a settlement contract;
2. UTC/local-day mismatch;
3. high/low track leakage;
4. bin topology or shoulder misread;
5. NO complement shortcut in quote, q, or q_lcb;
6. stale q, stale forecast cycle, stale book, stale heartbeat, stale balance/allowance, or stale current facts;
7. lower-bound inversion (`q_lcb > q`) without a distinct-random-variable proof;
8. partial market substrate outage treated as a complete family;
9. duplicate submit or idempotency break after unknown side effect;
10. lifecycle phase hallucination;
11. exit intent treated as close or settlement;
12. chain/local mismatch normalized away;
13. risk level recorded but not acted on;
14. backtest/shadow result promoted to live authority without parity and operator approval;
15. packet/consult/rebuild/evidence material treated as present-tense law.

---

## 12. Relationship To Other Files

- `docs/authority/zeus_current_delivery.md` defines how changes land and how authority/reference/current-fact layers remain isolated.
- `docs/authority/zeus_change_control_constitution.md` is durable anti-entropy rationale, not the fast default architecture spec.
- `docs/reference/zeus_prediction_market_quant_reference.md` is the canonical durable system reference. It explains the current deploy system in more detail but does not outrank code or this law.
- `docs/reference/zeus_domain_model.md`, `zeus_math_spec.md`, `zeus_strategy_spec.md`, `zeus_market_settlement_reference.md`, `zeus_execution_lifecycle_reference.md`, `zeus_risk_strategy_reference.md`, `zeus_data_and_replay_reference.md`, and `zeus_failure_modes_reference.md` are durable reference books.
- `docs/operations/current_state.md`, `current_data_state.md`, and `current_source_validity.md` are expiry-bound current facts.
- `architecture/docs_registry.yaml`, `reference_replacement.yaml`, `module_manifest.yaml`, `db_table_ownership.yaml`, `invariants.yaml`, `negative_constraints.yaml`, `fatal_misreads.yaml`, `runtime_modes.yaml`, `runtime_posture.yaml`, `money_path_objects.yaml`, `test_topology.yaml`, and `task_boot_profiles.yaml` are machine-checkable routing and invariant surfaces.
