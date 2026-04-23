# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Authority Kernel Gamechanger
- Active package source: `docs/operations/task_2026-04-23_authority_kernel_gamechanger/plan.md`
- Active execution packet: `docs/operations/task_2026-04-23_authority_kernel_gamechanger/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-23_authority_kernel_gamechanger/receipt.json`
- Status: P0 authority decontamination and packet activation in progress
- Docs truth refresh P0 commit: `80c0051`
- P0 follow-up review: `proceed_to_p1`
- Docs truth refresh P1 commit: `d742083`
- P1 follow-up review: `proceed_to_p2`
- Docs truth refresh P2 commit: `8b687da`
- P2 follow-up review: `proceed_to_p3`
- Docs truth refresh P3 commit: `55eb285`
- P3 follow-up review: `proceed_to_closeout`
- Docs truth refresh closeout commit: `36d2f64`
- Docs truth refresh post-closeout review: complete
- Guidance kernel Phase -1 commit: `b90c345`
- Guidance kernel Phase 0 commit: `1d5b724`
- Guidance kernel Phase 1 commit: `e3e8178`
- Guidance kernel Phase 2 commit: `ec22a02`
- Guidance kernel Phase 3 commit: `24b501a`
- Guidance kernel Phase 4 commit: `f887e9b`
- Guidance kernel closeout commit: `65bd122`
- Prior docs reclassification package closed at `169b014`; post-closeout review
  recorded at `6f51a8c`.

## Required evidence

- `.omx/context/kernel-gamechanger-20260423T035846Z.md`
- `.omx/plans/kernel-gamechanger-ralplan-2026-04-23.md`
- `docs/operations/task_2026-04-23_authority_kernel_gamechanger/plan.md`
- `docs/operations/task_2026-04-23_authority_kernel_gamechanger/work_log.md`
- `docs/operations/task_2026-04-23_authority_kernel_gamechanger/receipt.json`

## Freeze point

- P0 activates the packet and demotes packet-scoped `task_2026-04-15_*`
  authority files into reports evidence.
- Do not modify `src/**`, `state/**`, graph DB, runtime/source/data behavior,
  or current city/source truth in P0.

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
- `docs/operations/task_2026-04-22_docs_truth_refresh/`
- `docs/operations/task_2026-04-22_orphan_artifact_cleanup/`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/`

## Next action

- Finish P0 validation and review.
- If P0 review passes, open P1 core authority rewrite.
- Preserve unrelated dirty work and local archive inputs.
- Preserve unrelated dirty work and local archive inputs
