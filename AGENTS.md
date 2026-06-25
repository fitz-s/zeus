# Zeus AGENTS

Root operating contract for `/Users/leofitz/zeus`. Keep this file short, durable, and safe for a zero-context coding agent. Do not store branch names, loaded SHAs, live PIDs, bankrolls, active position inventory, temporary rejection counts, packet diaries, generated dumps, model catalogs, or current operational snapshots here. Nested `AGENTS.md` files govern their subtrees; direct system/developer/user instructions outrank every repo file.

---

## 0. Mission And Money Path

Zeus is a live-money weather prediction-market trading engine. It trades Polymarket settlement contracts: city/local-date/metric families with mutually-exclusive settlement bins and native YES/NO tokens.

Primary money path:

`contract/source/settlement truth -> forecast posterior -> q over Ω -> conservative q band -> family book -> native-side route/payoff vector -> direction/coherence/edge/utility gates -> sizing/risk -> execution intent -> venue command -> fill/position lifecycle -> monitor/exit -> settlement/redeem -> learning`

Every non-trivial change must state which part of this path it touches and which upstream truth it consumes. A downstream optimization that guesses contract, source, settlement, q, executable cost, native side, DB truth, or lifecycle truth is a money-path bug.

---

## 1. Authority And Proof Hierarchy

Use the narrowest surface that can prove the claim.

| Rank | Surface | Role | Forbidden misread |
|---|---|---|---|
| 1 | executable code, tests, migrations, DB/event/projection truth, launchd/operator receipts | actual behavior and ownership | prose cannot create behavior |
| 2 | `architecture/**` machine manifests | machine-checkable law, registries, topology, invariants | manifests are not historical lore |
| 3 | active `docs/authority/**` | durable architecture, delivery, DB, fusion, docs-plane law | dated authority-history docs are not current law |
| 4 | `docs/reference/**` canonical references | durable explanation for agents | reference does not authorize runtime state |
| 5 | `docs/operations/current_state.md`, `current_data_state.md`, `current_source_validity.md` | expiry-bound current fact pointers | current facts expire and fail closed |
| 6 | `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**`, closed packets | history/evidence only | never default-read as present-tense law |

If code/manifests/tests conflict with prose, believe code/manifests/tests and update prose. If behavior cannot be proven, mark it as unknown or unresolved implementation ambiguity.

---

## 2. Required Default Boot

For every non-trivial task:

1. Read this file.
2. Read `workspace_map.md`.
3. Read scoped `AGENTS.md` for every subtree you will touch.
4. Read `docs/authority/zeus_current_architecture.md` for runtime/strategy/settlement/execution/lifecycle/data work.
5. Read `docs/authority/zeus_current_delivery.md` for docs, governance, router, registry, demotion, packet, or architecture-boundary work.
6. Read `docs/authority/zeus_database_runtime_authority.md` for DB/WAL/lock/schema runtime work.
7. Read `docs/authority/zeus_forecast_fusion_authority.md` for forecast source/model/fusion work.
8. Read `docs/authority/zeus_docs_classification_authority.md` and `docs/authority/zeus_runtime_artifact_authority.md` for docs plane or runtime artifact placement work.
9. Read `docs/reference/AGENTS.md`, then the canonical reference named for the task.
10. Read relevant machine manifests in `architecture/**` before editing code or docs that they route.
11. Read current-fact pointers only when the task needs present operational state and the pointer is fresh/evidence-backed.

Default boot must not recursively read `docs/operations/current/**`, `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**`, or closed `docs/operations/task_*` packages. Read those only for explicit evidence/history work and never as current law.

Use CodeGraph for symbol/caller/callee tracing when available. If unavailable, fall back to `rg`, AST/callsite inspection, and targeted tests. CodeGraph/topology can tell where to inspect; it cannot decide settlement truth, source validity, current runtime state, or authority rank.

---

## 3. Canonical References

Task-specific supplemental reads after the default boot:

| Task | Canonical reads |
|---|---|
| full money-path orientation | `docs/reference/zeus_prediction_market_quant_reference.md` |
| domain/family/bin/native-side basics | `docs/reference/zeus_domain_model.md` |
| probability/q/q_lcb/math | `docs/reference/zeus_math_spec.md` |
| forecast source/product/regional model work | `docs/reference/zeus_forecast_source_and_regional_model_reference.md` |
| strategy/admission/selection | `docs/reference/zeus_strategy_spec.md` |
| settlement/source/bin topology | `docs/reference/zeus_market_settlement_reference.md` |
| execution/lifecycle/exit/settlement | `docs/reference/zeus_execution_lifecycle_reference.md` |
| sizing/risk/degraded data | `docs/reference/zeus_risk_strategy_reference.md` |
| DB/replay/backtest/current facts | `docs/reference/zeus_data_and_replay_reference.md` |
| failure-mode review | `docs/reference/zeus_failure_modes_reference.md`, `architecture/fatal_misreads.yaml` |

Do not route new work through dated replacement papers, raw consults, PR reviews, evidence packets, or rebuild diaries. If such a file contains surviving truth, promote that truth into active authority/reference first.

---

## 4. Runtime And Truth Invariants

Runtime entry surfaces:

| Surface | File |
|---|---|
| trading daemon | `src/main.py` |
| shared cycle/discovery path | `src/engine/cycle_runner.py`, `src/engine/discovery_mode.py`, `architecture/runtime_modes.yaml` |
| event reactor / live family decision | `src/engine/event_reactor_adapter.py`, `src/engine/qkernel_spine_bridge.py`, `src/decision/family_decision_engine.py` |
| probability materialization | `src/data/replacement_forecast_materializer.py`, `src/forecast/bayes_precision_fusion.py`, `src/calibration/emos.py` |
| execution boundary | `src/execution/executor.py`, `src/venue/**`, `src/state/venue_command_repo.py` |
| lifecycle/monitor/exit/settlement | `src/state/lifecycle_manager.py`, `src/engine/monitor_refresh.py`, `src/execution/exit_lifecycle.py`, `src/execution/harvester.py` |

Canonical DB topology is declared by `architecture/db_table_ownership.yaml` and `architecture/db_runtime_manifest.yaml`: `state/zeus-world.db`, `state/zeus-forecasts.db`, and `state/zeus_trades.db`. Table ownership is `(table, db)`, not table name alone.

Durable invariants:

- Zeus trades discrete settlement contracts, not continuous weather values.
- Family identity is city/local-date/metric/market-set Ω with mutually-exclusive bins.
- High and low tracks are distinct physical quantities and calibration/settlement identities.
- YES/NO are native venue sides. NO is not a casual `1 - YES` execution shortcut.
- `q_lcb` must be a coherent lower bound; `q_lcb > q` is invalid absent distinct-random-variable proof.
- Entry requires executable side-specific cost, fee/tick/depth, fresh book, conservative edge, positive robust ΔU, and risk admission.
- Risk levels must change behavior. Advisory-only risk is forbidden.
- Lifecycle phases are enum-backed: `pending_entry`, `active`, `day0_window`, `pending_exit`, `economically_closed`, `settled`, `voided`, `quarantined`, `admin_closed`, with `unknown` only where code declares recovery/sentinel semantics.
- Exit intent is not closure. Settlement is not exit. Chain/CLOB truth outranks local cache.
- Backtest/shadow evidence cannot promote live behavior without parity evidence, operator approval, and rollback.
- Runtime artifacts belong in operations, not authority/reference.

---

## 5. Planning Lock

Stop and plan before touching:

- `docs/authority/**`, `architecture/**`, AGENTS files, workspace routers, docs registry, reference replacement, or module manifest;
- `.github/workflows/**`;
- schema/migrations/DB ownership;
- `src/state/**`, `src/execution/**`, `src/engine/event_reactor_adapter.py`, `src/decision/**`, `src/probability/**`, `src/riskguard/**`, `src/risk_allocator/**`, `src/venue/**`;
- settlement/source/contract semantics;
- any live-money side effect, lifecycle truth, control-plane, risk, q authority, DB runtime, or forecast fusion authority;
- cross-zone edits or more than four files.

Do not create root-level coordination, scratch, plan, or handoff files unless explicitly requested.
