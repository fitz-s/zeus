# Zeus Architecture Reference

Purpose: durable descriptive map of Zeus as a runtime machine and an agentic
workspace. This file sits below `docs/authority/**` and `architecture/**`; it
does not create law.

Authority relationship: executable source, tests, machine manifests, and
authority docs win on disagreement. Use this file to orient reading, not to
override current architecture law.

Extracted from: `docs/artifacts/zeus_architecture_deep_map_2026-04-16.md`,
`docs/reports/zeus_system_constitution_2026-04-16.md`,
`docs/reports/zeus_refactor_plan_2026-04-16.md`, and
`docs/reports/legacy_reference_repo_overview.md`.

## What Zeus Is

Zeus is a live-only weather-probability trading runtime for Polymarket. It
collects forecast and observation data, computes calibrated probabilities,
selects edges, sizes and executes limit orders, monitors open positions, records
exits and settlement, and exposes typed state outward to Venus/OpenClaw.

Zeus is also a workspace change-control system. The repo has a boot surface,
machine-checkable manifests, active operations packets, derived context engines,
and historical cold storage. The current workspace law is in `architecture/**`,
`AGENTS.md`, `workspace_map.md`, scoped `AGENTS.md` files, and
`docs/operations/current_state.md`.

The workspace exists to answer five questions quickly:

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
  market fusion, FDR, and Kelly sizing.
- Execution: `src/execution/**`, limit-order placement, fill tracking, exits,
  collateral, and settlement harvest.
- State/control: `src/state/**`, lifecycle manager, chronicler/ledger,
  projections, chain reconciliation, and control overrides.
- Observability/supervisor boundary: `src/observability/**`,
  `src/supervisor_api/**`, and Venus/OpenClaw contracts.

Use `architecture/zones.yaml` and `architecture/source_rationale.yaml` for
file-level ownership. This reference is descriptive only.

## Derived Context Engines

`topology_doctor` is the repo-native routing and governance checker. Use it for
navigation, docs checks, registry drift, map maintenance, planning lock, and
closeout discipline.

Code Review Graph is a first-class derived structural context engine. Use it
for blast radius, review order, structural discovery, and minimal context. Do
not use it as authority or as a substitute for current factual docs, manifests,
tests, or source.

## Docs Trust Layers

The tracked docs mesh is:

- `docs/authority/`: law
- `docs/reference/`: canonical durable understanding
- `docs/operations/`: live current facts and packets
- `docs/runbooks/`: procedures
- `docs/reports/`: dated analytical evidence
- `docs/artifacts/`: snapshot/workbook evidence
- `docs/to-do-list/`: checklist evidence
- `docs/archive_registry.md`: visible historical interface

Current facts should route through operations current-fact surfaces. Dated
evidence should route through reports or artifacts. Neither belongs in
canonical reference.

## Dual-Track Architecture

Zeus is not a single-track daily-high system. The dual-track spine separates:

- high track: `temperature_metric=high`,
  `physical_quantity=mx2t6_local_calendar_day_max`,
  `observation_field=high_temp`
- low track: `temperature_metric=low`,
  `physical_quantity=mn2t6_local_calendar_day_min`,
  `observation_field=low_temp`

The tracks share local-calendar-day geometry but not calibration family,
observation field, physical quantity, or Day0 causality law. Current binding
law is in `docs/authority/zeus_dual_track_architecture.md`.

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
- Dual-track law: `docs/authority/zeus_dual_track_architecture.md`
- File ownership: `architecture/source_rationale.yaml`
- Workspace routing: `architecture/docs_registry.yaml`, `workspace_map.md`
