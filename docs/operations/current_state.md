# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Graph Rendering Integration
- Active package source: `docs/operations/task_2026-04-23_graph_rendering_integration/plan.md`
- Active execution packet: `docs/operations/task_2026-04-23_graph_rendering_integration/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-23_graph_rendering_integration/receipt.json`
- Status: graph deep-rendering packet opened; implementation plan in preparation

## Required evidence

- `docs/operations/task_2026-04-23_graph_rendering_integration/plan.md`
- `docs/operations/task_2026-04-23_graph_rendering_integration/work_log.md`
- `docs/operations/task_2026-04-23_graph_rendering_integration/receipt.json`

## Freeze point

- Authority Rehydration and official graph refresh verification are closed.
- This packet may prepare and later implement graph deep-rendering integration,
  but it must not change runtime/source behavior or stage `.code-review-graph/graph.db`
  without an explicit later phase.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

Use `docs/operations/AGENTS.md` for registered operations-surface classes and
non-default packet/package routing.

Visible non-default packet evidence:

- `docs/operations/task_2026-04-16_dual_track_metric_spine/`
- `docs/operations/task_2026-04-16_function_naming_freshness/`
- `docs/operations/task_2026-04-19_code_review_graph_topology_bridge/`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`
- `docs/operations/task_2026-04-19_workspace_artifact_sync/`
- `docs/operations/task_2026-04-20_code_impact_graph_context_pack/`
- `docs/operations/task_2026-04-20_code_review_graph_online_context/`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/`
- `docs/operations/task_2026-04-21_gate_f_data_backfill/`
- `docs/operations/task_2026-04-22_docs_truth_refresh/`
- `docs/operations/task_2026-04-22_orphan_artifact_cleanup/`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/`
- `docs/operations/task_2026-04-23_authority_kernel_gamechanger/`
- `docs/operations/task_2026-04-23_authority_rehydration/`
- `docs/operations/task_2026-04-23_graph_refresh_official_integration/`
- `docs/operations/task_2026-04-23_data_readiness_remediation/`

## Next action

- Prepare the implementation packet that turns graph deep-rendering results
  into repo-relative text summaries and module/context-pack integrations.
- Preserve unrelated dirty work and local archive inputs.
