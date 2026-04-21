# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Workspace Authority Reconstruction (2026-04-20 V2)
- Active package source: `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/README.md`
- Active execution packet: `docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md`
- Status: P3 historical compression and residual hygiene active
- P0 commit: `19e0178`
- P1 commit: `ad73440`
- P2A commit: `d45ec40`
- Supersession: user ruling in this thread makes the reconstruction package the
  current mainline control surface; older wait-for-ruling notes about
  P11/B055/B099 are stale for this packet

## Required evidence

- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/00_executive_ruling.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/01_mental_model.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/02_authority_order_rewrite.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/07_execution_packets.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/16_apply_order.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json`

## Freeze point

- P3 allowlist: visible history docs, history lore, context budget/tests only
  if needed, active packet evidence, plus approved preflight runtime inventory
  LOCAL_ADAPTATION
- No source, test, script, runtime DB, graph-db, or archive-body edits in this
  lane
- Runtime-local details live in `docs/operations/runtime_artifact_inventory.md`
  and `state/**`, not here

## Other registered operations surfaces

- `docs/operations/task_2026-04-13_topology_compiler_program.md`
- `docs/operations/task_2026-04-13_remaining_repair_backlog.md`
- `docs/operations/task_2026-04-14_session_backlog.md`
- `docs/operations/task_2026-04-16_dual_track_metric_spine/plan.md`
- `docs/operations/task_2026-04-16_function_naming_freshness/plan.md`
- `docs/operations/task_2026-04-19_code_review_graph_topology_bridge/plan.md`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`
- `docs/operations/task_2026-04-19_workspace_artifact_sync/plan.md`
- `docs/operations/task_2026-04-20_code_impact_graph_context_pack/plan.md`
- `docs/operations/task_2026-04-20_code_review_graph_online_context/plan.md`
- `docs/operations/data_rebuild_plan.md`

## Next action

- Complete P3 historical compression and residual hygiene
- Do not re-ingest archive bodies wholesale; use package policy and current
  visible surfaces first
- Keep unrelated dirty work and local archive inputs untouched
