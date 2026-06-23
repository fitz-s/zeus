# Zeus

Zeus is a live-money quantitative trading engine for Polymarket weather-settlement prediction markets.

It trades discrete settlement contracts, not continuous weather values. The basic economic object is a city/local-date/metric family with mutually-exclusive settlement bins and native YES/NO venue tokens. Forecast probability becomes tradable only after Zeus pins contract semantics, source/settlement truth, family/bin topology, executable orderbook cost, native side, risk, and lifecycle state.

For complete current law and reference, read:

- `AGENTS.md` and `workspace_map.md` for boot routing;
- `docs/authority/zeus_current_architecture.md` for durable architecture law;
- `docs/authority/zeus_current_delivery.md` for docs/change-control law;
- `docs/reference/zeus_prediction_market_quant_reference.md` for the canonical money-path reference.

This README is intentionally not a strategy-of-record snapshot. Runtime behavior must be proven from code, config, manifests, tests, DB/runtime receipts, and current-fact pointers.

---

## Money Path

```text
contract/source/settlement truth
  -> forecast posterior
  -> q over Ω
  -> conservative q band
  -> family book
  -> native-side route/payoff vector
  -> direction/coherence/edge/utility gates
  -> sizing/risk
  -> execution intent
  -> venue command
  -> fill/position lifecycle
  -> monitor/exit
  -> settlement/redeem
  -> learning
```

Current implementation anchors:

| Surface | Code / manifest |
|---|---|
| trading daemon | `src/main.py` |
| discovery/cycle orchestration | `src/engine/cycle_runner.py`, `src/engine/discovery_mode.py`, `architecture/runtime_modes.yaml` |
| event reactor | `src/engine/event_reactor_adapter.py` |
| q-kernel bridge | `src/engine/qkernel_spine_bridge.py` |
| terminal family decision | `src/decision/family_decision_engine.py` |
| replacement forecast materialization | `src/data/replacement_forecast_materializer.py` |
| Bayesian precision fusion | `src/forecast/bayes_precision_fusion.py` |
| settlement-preimage bin integration | `src/calibration/emos.py` |
| direction law | `src/strategy/live_inference/direction_law.py` |
| execution boundary | `src/execution/executor.py`, `src/venue/**`, `src/state/venue_command_repo.py` |
| lifecycle/position truth | `src/state/lifecycle_manager.py`, `src/state/portfolio.py`, `src/state/chain_reconciliation.py` |
| monitor/exit/settlement | `src/engine/monitor_refresh.py`, `src/execution/exit_lifecycle.py`, `src/execution/harvester.py` |
| DB ownership | `architecture/db_table_ownership.yaml` |

---

## Current Architecture In One Page

A family is one complete Ω: one city, local settlement date, metric (`high` or `low`), settlement unit, rounding rule, and venue market/condition topology. Bins may be point, finite range, or open shoulder. High and low tracks share calendar geometry but not physical quantity, observation field, calibration family, replay identity, or settlement rebuild identity.

YES and NO are native venue sides. NO is not an execution shortcut for `1 - YES`; it has its own token, quote, depth, payoff, fill, and risk. The allowed conservative probability complement is the certified q-band identity `q_lcb_no = 1 - q_ucb_yes`, produced inside the q construction seam. `1 - q_lcb_yes` is forbidden.

The current q-kernel path, when enabled by config, routes each family through `qkernel_spine_bridge -> FamilyDecisionEngine.decide()`: predictive distribution, Ω, joint q, joint q band, family book, market coherence, route/payoff candidates, direction/coherence/edge/ΔU filters, then robust utility-density selection. Legacy scalar trade-score and old market-fusion/baseline paths may exist as diagnostics, rollback, or receipt provenance; they are not the default strategy authority.

Direction law is structural: YES is legal only on the forecast settlement bin; NO is legal only off the forecast settlement bin, subject to the boundary-zone rule. A non-modal YES is not admitted just because a tail probability looks positive.

Execution is a side-effect boundary. Zeus must persist command/intent truth, prove a fresh pre-submit book/heartbeat/user-channel/connectivity/balance witness, and submit through the venue adapter. Unknown side-effect states are not retried as empty.

---

## DB And Deploy Topology

Canonical DB topology is declared by `architecture/db_table_ownership.yaml`:

| DB | Canonical role |
|---|---|
| `state/zeus-world.db` | world/runtime facts that remain world-owned |
| `state/zeus-forecasts.db` | forecast, observation, source-run, readiness, posterior, settlement-outcome truth |
| `state/zeus_trades.db` | trade decisions, execution facts, venue commands/events, positions, lifecycle truth, settlement commands |

Committed launchd artifacts under `deploy/launchd/**` are installable operator artifacts, not proof that a process is loaded. They define split process roles for substrate observation, price/user-channel ingest, and post-trade capital follow-up. Live process/PID/loaded-SHA status must come from fresh operator/runtime receipts, not README prose.

---

## Risk And Lifecycle

Risk levels change behavior. Advisory-only risk is forbidden.

| Level | Runtime behavior |
|---|---|
| GREEN | normal operation |
| YELLOW | no new entries; continue monitoring |
| ORANGE | no new entries; exit only under favorable/policy-authorized conditions |
| RED | protective cancel/sweep/exit behavior according to code |

Canonical lifecycle phases are:

```text
pending_entry -> active -> day0_window -> pending_exit -> economically_closed -> settled
```

Terminal/recovery phases are `voided`, `quarantined`, `admin_closed`, and the code-declared `unknown` sentinel. Exit intent is not economic close. Economic close is not settlement. Chain/CLOB truth outranks local cache.

---

## Documentation Map

| Layer | Purpose |
|---|---|
| `docs/authority/**` | durable law only |
| `docs/reference/**` | durable reference and module books |
| `docs/operations/current*.md` | current-fact pointers with freshness/expiry semantics |
| `docs/runbooks/**` | procedures |
| `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**` | history/evidence only; not default boot |
| `architecture/**` | machine-checkable manifests, registries, topology, invariants |

Dated consults, PR reviews, packet closeouts, raw evidence, and historical strategy snapshots must not be read as current law.

---

## Validation Commands

Run the strongest applicable subset for a change:

```bash
python3 scripts/topology_doctor.py --strict
python3 scripts/topology_doctor.py --source
python3 scripts/topology_doctor.py --tests
python3 scripts/topology_doctor.py --fatal-misreads
```

Use targeted pytest for the touched code paths. Separate changed-surface failures from pre-existing repo drift.
