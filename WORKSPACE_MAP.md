# Zeus Workspace Map

> Status: Orientation-only and archive-aware map, not principal authority.
> Current authority order is defined in `architecture/self_check/authority_index.md`.
> Current repo operating rules are defined in `AGENTS.md`.
> Historical material moved under `docs/archives/**` is not active authority.

## Root

| Item | Purpose |
|------|---------|
| `AGENTS.md` | Repo-native execution brief and current operating contract |
| `architects_state_index.md` | Fastest current Architects entrypoint |
| `architects_task.md` | Current live packet control surface |
| `architects_progress.md` | Durable Architects ledger |
| `root_progress.md` | Program-level narrative / backlog context |
| `root_task.md` | Program-level backlog queue |
| `WORKSPACE_MAP.md` | This file — directory contract |
| `zeus_mature_project_foundation/` | Imported foundation source package for provenance/reference, not active authority |
| `pytest.ini` | Test configuration |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Git exclusions |

## `src/` — Source Code

| Package | Purpose |
|---------|---------|
| `contracts/` | Semantic types (HeldSideProbability, DecisionSnapshotRef, ValidationManifest) |
| `control/` | Control plane (runtime commands from Venus/OpenClaw) |
| `data/` | External data clients (ensemble, polymarket, observation, market scanner) |
| `engine/` | CycleRunner (pure orchestrator), evaluator, monitor_refresh |
| `execution/` | Executor, exit triggers, harvester |
| `calibration/` | Platt, calibration store, manager, drift detection |
| `observability/` | Status summary (written every cycle for Venus to read) |
| `riskguard/` | Independent risk process (separate from main daemon) |
| `signal/` | EnsembleSignal, Day0Signal, model agreement |
| `state/` | Portfolio (Position objects), chronicler, decision chain, strategy tracker |
| `strategy/` | Market analysis, market fusion, FDR filter, Kelly sizing, risk limits, correlation |
| `types/` | Temperature, TemperatureDelta (unit safety) |

## `docs/` — Documentation

| Path | Purpose | Authority |
|------|---------|-----------|
| `docs/architecture/zeus_durable_architecture_spec.md` | Principal architecture authority for current phase. | Active |
| `docs/zeus_FINAL_spec.md` | Terminal target-state and endgame authority. | Active |
| `docs/governance/zeus_change_control_constitution.md` | Change-control authority. | Active |
| `docs/governance/zeus_autonomous_delivery_constitution.md` | Delivery and runtime-governance authority. | Active |
| `docs/known_gaps.md` | Active operational gap / antibody register. | Active |
| `docs/archives/**` | Historical handoffs, audits, findings, traces, research, and reports. | Historical |
| `docs/architecture/zeus_design_philosophy.md` | Historical rationale about system center and translation-loss failure mode. | Historical |
| `docs/architecture/zeus_blueprint_v2.md` | Historical architectural rationale for position-centric design. | Historical |
| `docs/KEY_REFERENCE/quantitative_research.md` | Calibration math, Kelly, sample sizes | Domain reference |
| `docs/KEY_REFERENCE/market_microstructure.md` | Edge thesis, participant types, entry timing | Domain reference |
| `docs/KEY_REFERENCE/statistical_methodology.md` | Three σ, instrument noise, FDR, data versioning | Domain reference |
| `docs/KEY_REFERENCE/architecture_blueprint.md` | **SUPERSEDED by blueprint_v2.** Historical only. | Historical |

## `config/` — Runtime Parameters

| File | Purpose |
|------|---------|
| `settings.json` | All runtime parameters (single source of truth) |
| `cities.json` | 16 cities with coordinates, stations, peak hours, units |

## `state/` — Persistent State

| File/Dir | Purpose |
|----------|---------|
| `zeus-paper.db` / `zeus-live.db` | Mode-specific trade databases |
| `zeus-shared.db` | Shared world-data database |
| `risk_state-paper.db` / `risk_state-live.db` | Mode-specific RiskGuard state |
| `positions-paper.json` / `positions-live.json` | Mode-qualified position files |
| `status_summary-paper.json` / `status_summary-live.json` | Mode-qualified derived operator snapshots |
| `control_plane-paper.json` / `control_plane-live.json` | Mode-qualified runtime command surfaces |
| `ensemble-log/` | ENS snapshots (append-only) |

## `tests/` — Test Suite

| Path | Purpose |
|------|---------|
| `tests/contracts/` | Spec-owned validation manifests |
| `tests/test_cross_module_invariants.py` | **Cross-module relationship tests** (the mechanism that breaks the failure cycle) |
| `tests/test_pnl_flow_and_audit.py` | P&L data flow chain invariants |
| `tests/test_*.py` | Function and lifecycle tests |

## `scripts/` — One-Time Operations

| Script | Purpose |
|--------|---------|
| `migrate_rainstorm_data.py` | Import data from rainstorm.db |
| `backfill_ens.py` | 93-day ENS P_raw backfill |
| `baseline_experiment.py` | Phase 0 GO/NO-GO gate |
| `healthcheck.py` | Daemon alive/dead check (Venus cron target) |
| `etl_*.py` | Data ETL from rainstorm.db / TIGGE / other sources |

## `logs/` — Daemon Logs

`zeus-paper.log`, `zeus-paper.err` — rotated by launchd.

## Archive boundary

- Historical handoffs, sessions, findings, traces, research, and reports now live under `docs/archives/`.
- Do not treat archived files as live control surfaces unless a new packet explicitly promotes them.

---

## What Does NOT Belong in Zeus Workspace

- Rainstorm source code (lives in `workspace-venus/rainstorm/`)
- TIGGE raw data (lives in `workspace-venus/51 source data/`)
- Session prompts, code review docs (live in `project level docs/archive/`)
- OpenClaw central config (`~/.openclaw/openclaw.json`)
- Venus agent identity files (live in `workspace-venus/` root)
