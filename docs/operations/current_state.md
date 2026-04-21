# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Docs Reclassification / Reference Extraction (2026-04-21 package)
- Active package source: `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/README.md`
- Active execution packet: `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/plan.md`
- Status: P1 reference extraction and root-snapshot demotion implemented; P1 review/closeout required before P2
- Docs reclassification P0 commit: `b1a9761`
- P0 commit: `19e0178`
- P1 commit: `ad73440`
- P2A commit: `d45ec40`
- P2 closeout-state commit: `c39ed5a`
- P3 commit: `0510357`
- Prior workspace authority reconstruction is closed at `152f210`.

## Required evidence

- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/00_executive_ruling.md`
- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/04_target_docs_topology.md`
- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/05_reference_extraction_matrix.md`
- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/08_execution_packets.md`
- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/17_apply_order.md`
- `.omx/plans/docs-reclassification-p0-ralplan-revised.md`
- `.omx/plans/docs-reclassification-p1-ralplan-revised.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json`

## Freeze point

- P1 implementation is complete locally. Do not begin P2 deletion/archive
  decisions until P1 review says `proceed_to_p2`.
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
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md`
- `docs/operations/data_rebuild_plan.md`
- `docs/operations/task_2026-04-21_gate_f_data_backfill/`

## Next action

- Run P1 follow-up review against moved docs, canonical references, manifests,
  and validation evidence
- If review says `proceed_to_p2`, open P2 deletion/archive planning
- Keep unrelated dirty work and local archive inputs untouched
