# docs/operations AGENTS

Live control and packet-evidence surface for Zeus.

This directory is not a second authority plane. It routes current work,
attached package inputs, active packets, and operational evidence. Current law
still lives in `docs/authority/**`, `architecture/**`, tests, and executable
source.

## Surface Classes

### Live Pointer

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control pointer: current program, active packet, required evidence, freeze point, next action |

Keep this file thin. It should not become a runtime diary, historical packet
index, or archive catalog.

### Active Supporting Surfaces

| File | Purpose |
|------|---------|
| `known_gaps.md` | Active operational gap register; moved from docs root |
| `runtime_artifact_inventory.md` | Inventory for runtime-local planning artifacts mirrored into repo control |
| `data_rebuild_plan.md` | Upstream data-rebuild plan; not executable from topology packets |

### Active Execution Packet

| Path | Purpose |
|------|---------|
| `task_2026-04-21_docs_reclassification_reference_extraction/` | Active docs reclassification/reference extraction packet |

### Packet Evidence

`task_*/**` folders and `task_*.md` files are packet evidence unless
`current_state.md` names one as the active execution packet. Read them only when
the active task routes you there.

Tracked packet evidence currently includes topology, dual-track, graph,
workspace-artifact, code-impact, execution-state, and Gate F data-backfill
packets.

### Attached Package Inputs

Package-input directories are source material for a packet, not universal law.
For example:

- `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/`

Use the active packet plan/work log to determine which package inputs matter.

## File Registry

This explicit registry keeps docs checks machine-routable. Entries here do not
make a surface default-read unless `current_state.md` routes it.

| Path | Class | Purpose |
|------|-------|---------|
| `AGENTS.md` | operations router | Registry for live operations surfaces in this directory |
| `current_state.md` | live pointer | Single current program, active packet, required evidence, freeze point, next action |
| `known_gaps.md` | active support | Active operational gap register |
| `runtime_artifact_inventory.md` | active support | Inventory for runtime-local planning artifacts mirrored into repo control |
| `data_rebuild_plan.md` | active support | Upstream data-rebuild plan; not executable from topology packets |
| `task_2026-04-21_docs_reclassification_reference_extraction/` | active packet | Active docs reclassification/reference extraction packet |
| `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/` | package input | Attached reconstruction package input; not universal authority |
| `task_2026-04-13_topology_compiler_program.md` | packet evidence | Historical/active topology compiler program tracker |
| `task_2026-04-13_remaining_repair_backlog.md` | packet evidence | Deferred backlog after non-DB small-package loop |
| `task_2026-04-14_session_backlog.md` | packet evidence | Session backlog snapshot from calibration-refactor work |
| `task_2026-04-16_dual_track_metric_spine/` | packet evidence | Dual-track metric spine refactor packet and evidence |
| `task_2026-04-16_function_naming_freshness/` | packet evidence | Governance package for naming and freshness metadata |
| `task_2026-04-19_code_review_graph_topology_bridge/` | packet evidence | Code Review Graph topology-first integration package |
| `task_2026-04-19_execution_state_truth_upgrade/` | packet evidence | Local execution-state truth upgrade packet present on disk |
| `task_2026-04-19_workspace_artifact_sync/` | packet evidence | Workspace artifact synchronization package |
| `task_2026-04-20_code_impact_graph_context_pack/` | packet evidence | Code impact graph context-pack package |
| `task_2026-04-20_code_review_graph_online_context/` | packet evidence | Tracked Code Review Graph online-context package |
| `task_2026-04-20_workspace_authority_reconstruction/` | packet evidence | Workspace-authority reconstruction execution packet |
| `task_2026-04-21_gate_f_data_backfill/` | packet evidence | Local Gate F data backfill packet present on disk |

## Rules

- `current_state.md` stays thin: current program, active packet, required
  evidence, freeze point, next action, and compact references to other
  registered surfaces.
- Non-trivial repo changes update a short work record in the active task folder.
- New multi-file execution packets use `task_YYYY-MM-DD_name/`.
- Do not leave completed packet material in the live pointer after closeout.
- Runtime-local `.omx/.omc` planning artifacts must be inventoried or mirrored
  before they are treated as durable work evidence.
