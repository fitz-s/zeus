# Zeus Workspace Map

> Master topology node for the Zeus mesh network.
> Every directory has an `AGENTS.md` with a file registry. This file links them all.
> For operating rules, see root `AGENTS.md`. For domain model, see `docs/reference/zeus_domain_model.md`.

## How to navigate

```
workspace_map.md (YOU ARE HERE)
  ├── AGENTS.md              ← operating rules, invariants, zones
  ├── src/*/AGENTS.md        ← zone-specific rules + file registry per package
  ├── tests/AGENTS.md        ← test catalog with invariant mappings
  ├── docs/README.md         ← docs index
  │   ├── docs/authority/AGENTS.md   ← architecture + governance file registry
  │   ├── docs/reference/AGENTS.md   ← domain model + research file registry
  │   └── docs/operations/AGENTS.md  ← active work packets file registry
  ├── architecture/AGENTS.md ← machine-checkable authority files
  ├── config/AGENTS.md       ← runtime parameters + reality contracts
  ├── scripts/AGENTS.md      ← script catalog
  └── .github/workflows/AGENTS.md ← CI gate rules
```

**Navigation rule**: Read this file for orientation. Then read the `AGENTS.md` in the directory you're editing. That file has the complete file registry and domain rules for its zone.

**Maintenance rule**: When you add, rename, or delete a file, update the `AGENTS.md` in that directory AND this file if it changes directory-level structure. See root `AGENTS.md` §7 "Mesh topology maintenance."

---

## Root

| Item | Purpose |
|------|---------|
| `AGENTS.md` | Root operating brief — read first, always |
| `workspace_map.md` | This file — master topology node |
| `src/main.py` | Daemon entry point (paper/live mode) |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Git exclusions |
| `.importlinter` | Zone boundary enforcement (import rules) |

---

## `src/` — Source Code (14 packages, organized by zone)

Each package has its own `AGENTS.md` with zone rules, domain context, and a complete file registry. Read the package `AGENTS.md` before editing any file in that package.

### Zone K0 — Kernel (truth, lifecycle, contracts)

| Package | Files | Purpose | Key entry point |
|---------|-------|---------|-----------------|
| `src/contracts/` | 17 | Typed semantic boundaries — settlement semantics, execution prices, edge context, provenance registry, reality contracts | `settlement_semantics.py`, `edge_context.py` |
| `src/state/` | 10 | Truth surface — DB, portfolio, lifecycle FSM, chronicler, chain reconciliation, strategy tracker | `db.py`, `lifecycle_manager.py` |

### Zone K1 — Protective (risk, control)

| Package | Files | Purpose | Key entry point |
|---------|-------|---------|-----------------|
| `src/riskguard/` | 5 | Independent risk process — risk levels, metrics, policy, Discord alerts | `riskguard.py` |
| `src/control/` | 1 | Control plane — 6 commands from Venus/OpenClaw, runtime behavior changes | `control_plane.py` |

### Zone K2 — Execution (orders, supervisor)

| Package | Files | Purpose | Key entry point |
|---------|-------|---------|-----------------|
| `src/execution/` | 6 | Order execution — limit orders on CLOB, exit triggers, fill tracking, settlement harvesting | `executor.py` |
| `src/supervisor_api/` | 1 | Typed contracts between Zeus and Venus (O/P/C/O pattern) | `contracts.py` |

### Zone K3 — Math/Data (signals, calibration, strategy, data, engine)

| Package | Files | Purpose | Key entry point |
|---------|-------|---------|-----------------|
| `src/engine/` | 9 | Cycle orchestration — trading cycle runner, evaluator pipeline, replay, discovery modes | `cycle_runner.py`, `evaluator.py` |
| `src/signal/` | 6 | Probability generation — 51-member Monte Carlo, day-0 signal, diurnal, model agreement | `ensemble_signal.py` |
| `src/calibration/` | 4 | Extended Platt calibration — 3-param logistic with temporal decay, maturity gates, drift detection | `platt.py`, `manager.py` |
| `src/strategy/` | 6 | Trading decisions — edge computation, α-weighted fusion, FDR filter, Kelly sizing, correlation | `market_analysis.py`, `kelly.py` |
| `src/data/` | 7 | External data — ECMWF ENS fetch, Polymarket CLOB, WU observations, Open-Meteo | `ecmwf_open_data.py`, `polymarket_client.py` |

### Zone K4 — Extension (observability, analysis)

| Package | Files | Purpose | Key entry point |
|---------|-------|---------|-----------------|
| `src/observability/` | 1 | Status summary — cycle health snapshot for Venus (derived, never canonical) | `status_summary.py` |
| `src/analysis/` | 0 | Placeholder for analysis utilities (empty) | — |

### Cross-cutting

| Package | Files | Purpose | Key entry point |
|---------|-------|---------|-----------------|
| `src/types/` | 3 | Unit-safe types — Temperature, TemperatureDelta, market types, solar types | `temperature.py` |

### Standalone

| File | Purpose |
|------|---------|
| `src/config.py` | Runtime configuration — settings loader, state paths, mode qualification |

---

## `tests/` — Test Suite (68 test files)

See `tests/AGENTS.md` for the complete test catalog with invariant mappings.

### Architecture-critical tests (break these = code is wrong)

| File | Tests | Invariant |
|------|-------|-----------|
| `test_cross_module_invariants.py` | Cross-module boundary contracts | INV-12 |
| `test_cross_module_relationships.py` | Module dependency rules | Zone boundaries |
| `test_architecture_contracts.py` | Cross-module invariant enforcement | Multiple |
| `test_lifecycle.py` | 9-state FSM transitions | INV-01, INV-08 |
| `test_provenance_enforcement.py` | Constant registration in cascades | INV-13 |
| `test_reality_contracts.py` | External assumption contracts | INV-11 |
| `test_no_bare_float_seams.py` | No bare floats at cross-layer seams | INV-12 |
| `test_live_safety_invariants.py` | Pre-live safety gates | Safety |
| `test_truth_surface_health.py` | Truth surface consistency | INV-03 |

### Math/signal tests

| File | Tests |
|------|-------|
| `test_ensemble_signal.py` | Monte Carlo signal generation |
| `test_platt.py` | Calibration correctness |
| `test_fdr.py` | FDR filter |
| `test_kelly.py` + `test_kelly_cascade_bounds.py` | Kelly sizing + cascade bounds |
| `test_market_analysis.py` | Edge computation |
| `test_calibration_*.py` | Calibration manager, quality, unification |
| `test_day0_*.py` | Day-0 signal, window, exit gate, runtime observation |
| `test_diurnal.py` | Diurnal adjustments |
| `test_correlation.py` | Cross-city/bin correlation |
| `test_temperature.py` | Unit-safe temperature types |

### Execution/lifecycle tests

| File | Tests |
|------|-------|
| `test_executor.py` | Order execution (paper + live) |
| `test_riskguard.py` | Risk level behavior changes |
| `test_entry_exit_symmetry.py` | Entry/exit statistical burden fairness |
| `test_exit_authority.py` | Exit decision authority |
| `test_churn_defense.py` | 8-layer churn defense |
| `test_pnl_flow_and_audit.py` | P&L data flow chain |

### Data/ETL tests

| File | Tests |
|------|-------|
| `test_ensemble_client.py` | Ensemble data retrieval |
| `test_etl_recalibrate_chain.py` | ETL recalibration chain |
| `test_observation_*.py` | Observation contracts and instants |
| `test_solar_etl.py` | Solar time ETL |

### Infrastructure tests

| File | Tests |
|------|-------|
| `test_config.py` | Configuration loading |
| `test_db.py` | Database operations |
| `test_healthcheck.py` | System health checks |
| `test_structural_linter.py` | Code structure quality |

### Subdirectory

| Path | Purpose |
|------|---------|
| `tests/contracts/` | Spec-owned validation manifests (YAML) |

---

## `docs/` — Documentation (flat mesh architecture)

See `docs/README.md` for the docs index.

| Directory | Files | Purpose | AGENTS.md |
|-----------|-------|---------|-----------|
| `docs/authority/` | 12 | Architecture specs + governance — constitutions, boundary law, decision register | `docs/authority/AGENTS.md` |
| `docs/reference/` | 9 | Domain model, data inventory, quantitative research, methodology | `docs/reference/AGENTS.md` |
| `docs/operations/` | 3 | Live control pointer + active work packets | `docs/operations/AGENTS.md` |
| `docs/archives/` | many | Historical — never active authority | — |

Root docs: `docs/README.md` (index), `docs/known_gaps.md` (operational gap register).

---

## `architecture/` — Machine-Checkable Authority

See `architecture/AGENTS.md` for zone rules. Changes here are ALWAYS governance changes.

| File | Purpose |
|------|---------|
| `kernel_manifest.yaml` | Kernel file ownership and protection rules |
| `invariants.yaml` | 10 invariant definitions (INV-01 through INV-10) |
| `zones.yaml` | Zone definitions with import rules (K0-K4) |
| `negative_constraints.yaml` | 10 negative constraint definitions |
| `maturity_model.yaml` | Maturity model definitions |
| `lifecycle_grammar.md` | Lifecycle grammar specification |
| `2026_04_02_architecture_kernel.sql` | Canonical event/projection schema — position_events, position_current, strategy_health, risk_actions, control_overrides, fact tables |
| `self_check/zero_context_entry.md` | Zero-context agent entry checklist |
| `ast_rules/semgrep_zeus.yml` | Semgrep rules for code enforcement |
| `ast_rules/forbidden_patterns.md` | Forbidden code patterns |
| `packet_templates/*.md` | Work packet templates (bugfix, feature, refactor, schema) |

---

## `config/` — Runtime Parameters

See `config/AGENTS.md` for file registry and rules.

| File | Purpose |
|------|---------|
| `settings.json` | All runtime parameters (single source of truth) |
| `cities.json` | 16 cities with coordinates, stations, peak hours, units |
| `provenance_registry.yaml` | INV-13 constant registration for Kelly cascade |
| `reality_contracts/*.yaml` | External assumption contracts (INV-11) |

---

## `scripts/` — Operations & ETL (75 scripts)

See `scripts/AGENTS.md` for rules. Scripts are one-time operations, NOT part of the runtime.

| Category | Examples |
|----------|---------|
| **ETL** | `etl_diurnal_curves.py`, `etl_historical_forecasts.py`, `etl_hourly_observations.py`, `etl_solar_times.py`, `etl_tigge_*.py`, `etl_observation_instants.py`, `etl_market_price_history.py` |
| **Backfill** | `backfill_ens.py`, `backfill_wu_daily_all.py`, `backfill_hourly_openmeteo.py`, `backfill_semantic_snapshots.py`, `backfill_cluster_taxonomy.py`, `backfill_exit_telemetry.py` |
| **Audit** | `audit_paper_explainability.py`, `audit_realtime_pnl.py`, `audit_replay_*.py`, `audit_divergence_*.py`, `audit_time_semantics.py` |
| **Architecture checks** | `check_kernel_manifests.py`, `check_module_boundaries.py`, `check_work_packets.py`, `check_advisory_gates.py` |
| **Analysis** | `analyze_paper_trading.py`, `equity_curve.py`, `baseline_experiment.py`, `automation_analysis.py` |
| **Replay** | `run_replay.py`, `replay_parity.py`, `capture_replay_artifact.py`, `profit_validation_replay.py` |
| **Operations** | `healthcheck.py`, `heartbeat_dispatcher.py`, `live_smoke_test.py`, `auto_pause_live.sh`, `force_lifecycle.py`, `cleanup_ghost_positions.py` |
| **Venus integration** | `venus_autonomy_gate.py`, `venus_sensing_report.py` |
| **Calibration** | `refit_platt.py`, `generate_calibration_pairs.py`, `validate_dynamic_alpha.py` |
| **Migration** | `migrate_rainstorm_full.py`, `migrate_to_isolated_dbs.py`, `onboard_cities.py` |
| **Validation** | `validate_assumptions.py`, `verify_truth_surfaces.py`, `diagnose_*.py`, `semantic_linter.py` |

---

## `state/` — Persistent State (gitignored)

| File/Dir | Purpose |
|----------|---------|
| `zeus-paper.db` / `zeus-live.db` | Mode-specific trade databases |
| `zeus-shared.db` | Shared world-data database |
| `risk_state-*.db` | Mode-specific RiskGuard state |
| `positions-*.json` | Mode-qualified position files |
| `ensemble-log/` | ENS snapshots (append-only) |
| `status_summary-*.json` | Cycle health snapshots (derived, for Venus) |

---

## `.github/workflows/` — CI/CD

See `.github/workflows/AGENTS.md` for gate rules.

| File | Purpose |
|------|---------|
| `architecture_advisory_gates.yml` | Advisory architecture gates (non-blocking) |

---

## File Placement Rules (MANDATORY)

| Type | Location | Naming pattern |
|------|----------|---------------|
| Authority docs (specs, constitutions, boundary law) | `docs/authority/` | `zeus_<topic>.md` |
| Reference material (domain model, data inventory) | `docs/reference/` | `<topic>.md` |
| Operations (control, plans, work packets) | `docs/operations/` | varies |
| Completed work packets | `docs/archives/work_packets/` | same name |
| Archives | `docs/archives/<type>/` | original name |
| Agent micro-logs | `.omx/context/` | `<packet>-worklog.md` |

## Naming Rules (MANDATORY)

- All `.md` files: `lower_snake_case.md`
- Exceptions: `AGENTS.md`, `README.md`
- **New files**: Use `task_YYYY-MM-DD_name.md` format — task prefix identifies the program/packet, date is creation date. Example: `datafix_2026-04-10_tigge_backfill_status.md`
- No single-word prefixes: ❌ `data_plan.md` → ✅ `datafix_2026-04-10_improvement_plan.md`
- No generic names: ❌ `plan.md`, `progress.md` → ✅ `<task>_<date>_<topic>.md`
- No spaces in filenames or directory names
- Existing files keep current names (no retroactive renames)
- Date prefixes only for time-bound reports

## What Does NOT Belong Here

- old Rainstorm
- TIGGE raw data → `workspace-venus/51 source data/`
- Session prompts, code review docs → `project level docs/archive/`
- OpenClaw central config → `~/.openclaw/openclaw.json`
- Venus agent identity files → `workspace-venus/` root
