# Zeus Architecture Reference

Purpose: durable descriptive map of the Zeus trading engine. This file sits
below `docs/authority/**` and `architecture/**`; it does not create law.

Authority relationship: executable source, tests, machine manifests, and
authority docs win on disagreement. Use this file to orient reading, not to
override current architecture law.

## What Zeus Is (Runtime Systems Map)

Zeus is a **live weather settlement-contract trading runtime** for Polymarket.

**The Money Path:**
`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`

**The Probability Chain:**
`51 ENS members -> ENS bias correction (empirical-Bayes, pre-MC; flag-gated, default off) -> per-member daily max -> Monte Carlo (sensor noise + ASOS rounding) -> P_raw -> Extended Platt (A·logit + B·lead_days + C) -> P_cal -> α-weighted Market Fusion -> P_posterior -> Edge & Double-Bootstrap CI -> Fractional Kelly (× DDD oracle-coverage discount) -> Position Size`

ENS bias correction is empirical-Bayes shrinkage of the TIGGE structural prior toward live OpenData residuals, SNR-gated and applied to member extrema before Monte Carlo (`src/calibration/ens_bias_model.py`, `src/calibration/ens_error_model.py`; PRs #334/#336). This step is flag-gated (`settings.bias_correction_enabled`, default `false`) and not yet active in production — activation pending. The final Kelly size is scaled by the Data Density Discount (DDD) when observation coverage is thin (see Subsystem Map).

### Platform Configuration & Change Control

Zeus employs standard operational and configuration systems (manifests, packets, context engines). These routines enforce the boundaries defining the trading machine. They codify the answers to:
1. what is law
2. what is current
3. what is durable reference
4. what is derived context
5. where history lives without becoming default context

## Runtime Boundary

Main runtime flow:

`fetch data -> compute probability -> compare market -> select edge -> size -> execute -> monitor -> exit/settle -> report`

Primary code path:

- `src/main.py` starts the live daemon and scheduler.
- `src/engine/cycle_runner.py` owns the shared cycle across discovery modes.
- `src/engine/evaluator.py` converts market candidates into trade/no-trade decisions.
- `src/execution/executor.py` places live limit orders.
- `src/engine/monitor_refresh.py` and `src/execution/exit_triggers.py` refresh
  monitored positions and emit exit intent.
- `src/execution/harvester.py` handles settlement and learning follow-through.

Discovery modes are parameters of one shared cycle, not separate runtimes:
`opening_hunt`, `update_reaction`, and `day0_capture`.

## Truth And Control Surfaces

Runtime truth flows from chain/CLOB facts into canonical DB/event truth and only
then into projections, JSON, reports, or operator status. JSON/status/report
surfaces are derived; they do not become canonical truth by being convenient.

Important surfaces:

- `state/zeus_trades.db`: live trade/event/projection truth.
- `state/zeus-world.db`: weather, calibration, forecast, and settlement-world data.
- `state/zeus-forecasts.db`: ensemble snapshots, settlements, calibration pairs, Platt + ENS-bias models (3rd DB of the K1 split; cross-DB writes use ATTACH+SAVEPOINT per INV-37, never independent connections).
- `data/oracle_error_rates.json`: per-city oracle mismatch rate (beta-binomial 95% bound), written daily by an `ingest_main.py` cron job; feeds the strategy oracle penalty and DDD.
- `position_events` and `position_current`: append-first event/projection model.
- `docs/operations/current_state.md`: repo-facing active work pointer, not runtime truth.
- `docs/operations/current_data_state.md`: current audited data posture, not law.
- `docs/operations/current_source_validity.md`: current audited source-validity posture, not law.
- `.code-review-graph/graph.db`: tracked derived context, not authority.

Risk/control outputs must change behavior. Advisory-only RED/YELLOW/ORANGE
states are not safety mechanisms.

## Subsystem Map

- Data ingestion: `src/data/**`, ingestion guards, observations, forecasts,
  market scanner, and backfill scripts.
- Probability/signal: `src/signal/**`, ensemble signals, Day0 high/low paths,
  and settlement semantics.
- Calibration/math: `src/calibration/**`, Platt models, effective sample size,
  market fusion, FDR, Kelly sizing, and the hierarchical ENS bias / predictive-error
  layer (`ens_bias_model.py`, `ens_bias_repo.py`, `ens_error_model.py`).
- Oracle/DDD: `src/oracle/**`, Data Density Discount v2 (two-rail coverage trigger
  + continuous Kelly discount) and oracle-error-rate consumption; spec
  `docs/reference/zeus_oracle_density_discount_reference.md`.
- Execution: `src/execution/**`, limit-order placement, fill tracking, exits,
  collateral, and settlement harvest.
- State/control: `src/state/**`, lifecycle manager, chronicler/ledger,
  projections, chain reconciliation, and control overrides.
- Observability/supervisor boundary: `src/observability/**`,
  `src/supervisor_api/**`, and Venus/OpenClaw contracts.

Use `architecture/zones.yaml` and `architecture/source_rationale.yaml` for
file-level ownership. This reference is descriptive only.

First-wave dense module books now exist for the three highest-risk runtime
surfaces:

- `docs/reference/modules/state.md`
- `docs/reference/modules/engine.md`
- `docs/reference/modules/data.md`

## Pipeline Data Flow

How the trading pipeline stages connect through the codebase:

```
data ingestion (src/data/**)         → observations, forecasts, market book
        ↓
signal generation (src/signal/**)    → P_raw per bin (Monte Carlo over ENS)
        ↓
calibration (src/calibration/**)     → P_cal (Extended Platt with lead_days)
        ↓
strategy (src/strategy/**)           → P_posterior (α-weighted fusion), edge,
                                       bootstrap CI, FDR filter, Kelly sizing
        ↓
engine (src/engine/**)               → evaluator decisions, cycle orchestration,
                                       monitor refresh
        ↓
execution (src/execution/**)         → limit orders on CLOB, fill tracking,
                                       exit triggers, settlement harvest
        ↓
state (src/state/**)                 → lifecycle transitions, event log,
                                       projections, chain reconciliation
        ↓
riskguard (src/riskguard/**)         → policy emission that changes evaluator/
                                       sizing/execution behavior
        ↓
observability (src/observability/**) → derived operator summaries (read-only)
supervisor_api (src/supervisor_api/**) → typed boundary for Venus/OpenClaw
```

Two cross-cutting facts the stage list hides:
- **Grid resolution asymmetry:** TIGGE (training prior) is O640 (≈0.5°) while ECMWF
  OpenData (live) is 0.25°; the live path reconciles them and the ENS-bias layer
  transports the 0.5°→0.25° variance. Binding law:
  `architecture/zeus_grid_resolution_authority_2026_05_07.yaml`;
  ingest detail `src/data/ecmwf_open_data.py`.
- **ENS bias correction** runs inside signal generation, before the Monte Carlo,
  on member extrema (see Probability Chain above).

Risk policy flows laterally: RiskGuard emits policy consumed by engine,
evaluator, and executor. Control plane (`src/control/**`) bridges operator
intent into typed runtime behavior changes. DDD (`src/oracle/**`) likewise
applies laterally as a Kelly-size discount when oracle coverage is thin.

## Dual-Track Architecture

Zeus is dual-track. The dual-track spine separates:

- high track: `temperature_metric=high`,
  `physical_quantity=mx2t6_local_calendar_day_max`,
  `observation_field=high_temp`
- low track: `temperature_metric=low`,
  `physical_quantity=mn2t6_local_calendar_day_min`,
  `observation_field=low_temp`

The tracks share local-calendar-day geometry but not calibration family,
observation field, physical quantity, or Day0 causality law. Current binding
law is in `docs/authority/zeus_current_architecture.md`.

## Code And Topology Hotspots

The historically high-blast-radius files are not automatically wrong, but they
should be approached with packet discipline:

- `src/engine/evaluator.py`: signal, calibration, FDR, sizing, policy gates.
- `src/engine/cycle_runner.py`: full live-cycle orchestration.
- `src/state/db.py`: DB schema and canonical query/write surfaces.
- `src/state/portfolio.py`: runtime position projection and compatibility.
- `src/execution/executor.py`: live-money order boundary.
- `scripts/topology_doctor*.py`: workspace-law enforcement and routing.

Before editing high-sensitivity areas, load the scoped `AGENTS.md`, machine
manifests, and active packet plan.

## What This File Is Not

- not current architecture law
- not a packet plan
- not a source-rationale replacement
- not Code Review Graph output
- not archive evidence

Where to go next:

- Current law: `docs/authority/zeus_current_architecture.md`
- Dual-track law: `docs/authority/zeus_current_architecture.md`
- State / engine / data deep dive: `docs/reference/modules/state.md`,
  `docs/reference/modules/engine.md`, `docs/reference/modules/data.md`
- File ownership: `architecture/source_rationale.yaml`
- Workspace routing: `architecture/docs_registry.yaml`, `workspace_map.md`
