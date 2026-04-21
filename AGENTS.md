# Zeus AGENTS

Zeus is two coupled systems:

1. a live weather-probability trading runtime on Polymarket; and
2. an agentic coding workspace that needs thin boot surfaces, machine-routable
   law, derived context engines, and compressed history.

Read this file first. Use it to get the repo's current shape, then route into
the narrower manifest, packet, or code surface that actually governs the task.

## Runtime machine

Zeus turns weather forecasts and settlement observations into calibrated market
probabilities, sized positions, execution decisions, monitoring, exits, and
settlement follow-through.

The live cycle is:

`fetch data -> compute probability -> compare market -> select edge -> size -> execute -> monitor -> exit/settle -> report`

The runtime entry points are:

- `src/main.py` - live daemon entry
- `src/engine/cycle_runner.py` - cycle orchestration
- `src/engine/evaluator.py` - candidate to decision pipeline
- `src/execution/executor.py` - live order placement
- `src/engine/monitor_refresh.py` and `src/execution/exit_triggers.py` -
  monitoring and exits
- `src/execution/harvester.py` - settlement and learning follow-through

The truth path is:

`chain/CLOB facts -> canonical DB/events -> projections/status -> derived reports`

Zeus is dual-track. High and low temperature families share local-calendar-day
geometry and do not share physical quantity, observation field, Day0 causality,
or calibration family. Do not describe Zeus as a single-track daily-high system.

## Workspace machine

The repo is also a change-control system. A cold-start agent should be able to
answer four questions quickly:

1. What is current law?
2. What is active right now?
3. What is derived context, not authority?
4. Where does history live without becoming default context?

The durable workspace kernel is:

- machine manifests under `architecture/**`
- scoped `AGENTS.md` routers
- `docs/operations/current_state.md`, `docs/operations/known_gaps.md`, and the active packet folder
- derived context engines such as `topology_doctor`, source rationale, history
  lore, and Code Review Graph

## Default read order

1. `AGENTS.md`
2. `workspace_map.md`
3. scoped `AGENTS.md` for the directory you will touch
4. relevant machine manifests
5. `docs/operations/current_state.md`
6. active packet docs if the task is live
7. derived context engines as needed
8. historical evidence only by explicit need

If the read set grows beyond the budget in `architecture/context_budget.yaml`,
narrow with `python scripts/topology_doctor.py --navigation --task "<task>" --files <files>`
or `python scripts/topology_doctor.py digest --task "<task>" --files <files>`.

## Authority vs context vs history

### Authority

Authority means the surfaces that grant permission, define truth, or enforce
behavior:

- system / developer / user instructions
- machine-checkable manifests and tests
- active packet control surfaces
- executable source and canonical DB truth for present-tense behavior

### Derived context

Derived context helps routing and review but never outranks authority:

- `topology_doctor` digests, context packs, and core maps
- `architecture/source_rationale.yaml`
- `architecture/history_lore.yaml`
- `.code-review-graph/graph.db`

Code Review Graph is tracked derived context, not authority. It may guide file
discovery, blast-radius analysis, or review order. It never waives planning
lock, manifests, receipts, tests, or canonical truth rules.

### History

History is a visible protocol plus cold storage:

- visible interface: `docs/archive_registry.md`
- dense durable lessons: `architecture/history_lore.yaml`
- raw archive bodies and bundles: local historical cold storage, not
  default-read, not peer authority

Do not read archive bodies by default. When archive material matters, label
archive-derived claims as `[Archive evidence]`.

## Durable rules

- Canonical DB/event truth outranks derived JSON, CSV, reports, notebooks, and
  archives.
- Live may act. Backtest may evaluate. Shadow is observe-only instrumentation.
- Settlement values flow through `SettlementSemantics`.
- `strategy_key` is the sole governance identity.
- Lifecycle transitions belong to lifecycle authority; do not invent phase
  strings.
- High and low rows must not mix in calibration, Platt fitting, replay bin
  lookup, or settlement rebuild identity.
- DB commits must precede derived JSON export writes.
- `RED` risk must cancel pending orders and sweep active positions.
- Authority-loss must degrade monitor / exit lanes to read-only rather than
  kill the cycle.
- Do not widen workspace-authority packets into runtime, DB, source, tests, or
  archive-body rewrites unless the packet explicitly authorizes that scope.

## Planning lock

Stop and plan before touching:

- `architecture/**`
- `docs/authority/**`
- `.github/workflows/**`
- `src/state/**` truth ownership, schema, projection, or lifecycle write paths
- `src/control/**`
- `src/supervisor_api/**`
- cross-zone changes
- more than 4 changed files
- anything described as canonical truth, lifecycle, governance, control, or DB
  authority

Machine check:

`python scripts/topology_doctor.py --planning-lock --changed-files <files...> --plan-evidence <plan file>`

## Change classification

- Math: stays inside existing semantic contracts
- Architecture: changes canonical read/write paths, lifecycle grammar, truth
  ownership, DB/schema, point-in-time semantics, or zone boundaries
- Governance: changes manifests, AGENTS, packets, constitutions, routing, or
  control surfaces

## Mesh maintenance

When adding, renaming, or deleting a file:

1. update the manifest that owns the registry when one exists
2. update the scoped `AGENTS.md` if local routes or file registries change
3. update `workspace_map.md` when directory-level structure or visibility
   classes change

Unregistered files are invisible to future agents.

Registry route reminders:

- `src/**` -> `architecture/source_rationale.yaml`
- `scripts/*` -> `architecture/script_manifest.yaml`
- `tests/test_*.py` -> `architecture/test_topology.yaml`
- `docs/reference/*` -> `docs/reference/AGENTS.md` and
  `architecture/reference_replacement.yaml`

Check:

`python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory|precommit|closeout`

## What to read by task

- Source edits: scoped `src/**/AGENTS.md`, `architecture/source_rationale.yaml`,
  targeted code, targeted tests
- K0/K1 truth or lifecycle work: `docs/authority/zeus_current_architecture.md`,
  `architecture/kernel_manifest.yaml`, targeted rationale entries
- Dual-track work: `docs/authority/zeus_dual_track_architecture.md`
- Delivery/governance work: `docs/authority/zeus_current_delivery.md`,
  `docs/operations/current_state.md`, `docs/operations/known_gaps.md`, active
  packet docs
- Historical failure context: `architecture/history_lore.yaml` first, archive
  bodies only if explicitly needed

## Git safety

Never run destructive git commands or overwrite unrelated dirty work without
explicit human approval. Preserve untracked local inputs, runtime artifacts, and
other packets unless the active packet explicitly governs them.
