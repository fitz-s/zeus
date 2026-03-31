# Zeus Workspace Map

## Root

| Item | Purpose |
|------|---------|
| `.claude/CLAUDE.md` | Zeus coding rules, design principles, Rainstorm reuse policy |
| `WORKSPACE_MAP.md` | This file — directory contract |
| `ZEUS_PROGRESS.md` | Session-level progress tracking and status |
| `PLAN.md` | Current active implementation plan |
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
| `docs/architecture/zeus_design_philosophy.md` | **HIGHEST.** Why recognition ≠ prevention. Cross-module invariants. | Active |
| `docs/architecture/zeus_blueprint_v2.md` | **Architectural.** Position-centric lifecycle, CycleRunner, Decision Chain. | Active |
| `docs/ZEUS_DATA_UTILIZATION_PLAN.md` | Data utilization. 4.7% usage gap. | Active |
| `docs/reference/quantitative_research.md` | Calibration math, Kelly, sample sizes | Domain reference |
| `docs/reference/market_microstructure.md` | Edge thesis, participant types, entry timing | Domain reference |
| `docs/reference/statistical_methodology.md` | Three σ, instrument noise, FDR, data versioning | Domain reference |
| `docs/reference/architecture_blueprint.md` | **SUPERSEDED by blueprint_v2.** Historical only. | Historical |

## `config/` — Runtime Parameters

| File | Purpose |
|------|---------|
| `settings.json` | All runtime parameters (single source of truth) |
| `cities.json` | 16 cities with coordinates, stations, peak hours, units |

## `state/` — Persistent State

| File/Dir | Purpose |
|----------|---------|
| `zeus.db` | Main database (chronicle, calibration, decisions, ensemble snapshots) |
| `risk_state.db` | RiskGuard state (separate process, separate DB) |
| `positions.json` | Open positions (atomic writes) |
| `status_summary.json` | Health snapshot (Venus reads this) |
| `control_plane.json` | Runtime commands (Venus writes this) |
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

---

## What Does NOT Belong in Zeus Workspace

- Rainstorm source code (lives in `workspace-venus/rainstorm/`)
- TIGGE raw data (lives in `workspace-venus/51 source data/`)
- Session prompts, code review docs (live in `project level docs/archive/`)
- OpenClaw central config (`~/.openclaw/openclaw.json`)
- Venus agent identity files (live in `workspace-venus/` root)
