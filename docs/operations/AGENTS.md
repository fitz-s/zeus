# docs/operations AGENTS

Live control surface for Zeus.

This directory contains:

- the thin current control pointer
- active execution packets
- attached package inputs that shape active work

Completed packet material belongs in archive after closeout. Do not let
`current_state.md` turn back into a runtime diary.

## File registry

| File | Purpose |
|------|---------|
| `AGENTS.md` | Registry for live operations surfaces in this directory |
| `current_state.md` | Single live control pointer: current program, active packet, required reads, next action |
| `zeus_workspace_authority_reconstruction_package_2026-04-20_v2/` | Attached reconstruction package; current source input for workspace-authority work |
| `task_2026-04-20_workspace_authority_reconstruction/` | Active execution packet applying the reconstruction package |
| `task_2026-04-21_docs_reclassification_reference_extraction/` | Active docs reclassification/reference extraction packet |
| `task_2026-04-21_gate_f_data_backfill/` | Local Gate F data backfill packet present on disk; registered so docs routing stays explicit |
| `task_2026-04-19_execution_state_truth_upgrade/` | Local packet present on disk but not the active control surface |
| `task_2026-04-13_topology_compiler_program.md` | Active topology compiler program tracker |
| `task_2026-04-13_remaining_repair_backlog.md` | Deferred backlog after non-DB small-package loop |
| `task_2026-04-14_session_backlog.md` | Session backlog snapshot from calibration-refactor work |
| `task_2026-04-16_dual_track_metric_spine/` | Dual-track metric spine refactor packet and evidence |
| `task_2026-04-16_function_naming_freshness/` | Governance package for naming and freshness metadata |
| `task_2026-04-19_code_review_graph_topology_bridge/` | Code Review Graph topology-first integration package |
| `task_2026-04-19_workspace_artifact_sync/` | Workspace artifact synchronization package |
| `task_2026-04-20_code_impact_graph_context_pack/` | Code impact graph context-pack package |
| `task_2026-04-20_code_review_graph_online_context/` | Tracked Code Review Graph online-context package |
| `data_rebuild_plan.md` | Upstream data-rebuild plan; not executable from topology packets |
| `known_gaps.md` | Active operational gap register; moved from docs root |
| `runtime_artifact_inventory.md` | Inventory for runtime-local planning artifacts mirrored into repo control |

## Packet lifecycle

1. Active execution packets live here, are listed in this registry, and are
   referenced from `current_state.md`.
2. Completed packets move to archive by git lineage.
3. Attached source packages may remain here while they are actively driving a
   packet, but they are not substitutes for the packet's own plan/work log.

## Rules

- `current_state.md` stays thin: current program, active packet, required
  evidence, next action, and compact references to other registered surfaces
- non-trivial repo changes update a short work record in the active task folder
- new multi-file execution packets use `task_YYYY-MM-DD_name/`
- do not leave completed packet material in the live surface after closeout
- runtime-local `.omx/.omc` planning artifacts must be inventoried or mirrored
  before they are treated as durable work evidence
