# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Docs Truth Refresh / Stale-Authority Purge (2026-04-22 package)
- Active package source: `zeus_docs_truth_refresh_reconstruction_package_2026-04-22`
- Active execution packet: `docs/operations/task_2026-04-22_docs_truth_refresh/plan.md`
- Status: P1 canonical reference completion and runbook cleanup complete; P1 review pending before P2
- Docs truth refresh P0 commit: `80c0051`
- P0 follow-up review: `proceed_to_p1`
- Prior docs reclassification package closed at `169b014`; post-closeout review
  recorded at `6f51a8c`.

## Required evidence

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/task_2026-04-21_gate_f_data_backfill/step1_schema_audit.md`
- `docs/operations/task_2026-04-21_gate_f_data_backfill/step1b_source_validity.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/plan.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`

## Freeze point

- Do not widen this package into runtime source, DB mutation, graph DB changes,
  archive-body ingestion, or authority-law rewrites.
- P2 owns freshness-aware registry schema and retirement of
  `architecture/reference_replacement.yaml`; P0 only performs path/class cleanup.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`

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

## Next action

- Run P1 follow-up review
- If review says `proceed_to_p2`, plan freshness-aware docs registry enforcement
- Preserve unrelated dirty work and local archive inputs
