# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Docs Reclassification / Reference Extraction (2026-04-21 package)
- Active package source: `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/README.md`
- Active execution packet: `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/plan.md`
- Status: Docs reclassification package post-closeout review complete
- Docs reclassification P0 commit: `b1a9761`
- Docs reclassification P1 commit: `84d6d25`
- P1 follow-up review: `proceed_to_p2`
- Docs reclassification P2 commit: `1e1b9a7`
- P2 follow-up review: `proceed_to_p3`
- Docs reclassification P3 commit: `995c313`
- P3 follow-up review: `proceed_to_closeout`
- Prior workspace authority reconstruction is closed at `152f210`.

## Required evidence

- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/12_codex_prompts/codex_closeout.md`
- `/Users/leofitz/Downloads/zeus_docs_reclassification_reference_extraction_package_2026-04-21/11_validation_matrix.md`
- `.omx/context/docs-reclassification-closeout-20260421T233946Z.md`
- `.omx/plans/docs-reclassification-closeout-plan-2026-04-21.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json`

## Freeze point

- Do not open a later reference-fragment deletion packet until post-closeout
  review confirms it is safe and `architecture/reference_replacement.yaml`
  marks the target file deletion-ready.
- Runtime-local details live in `docs/operations/runtime_artifact_inventory.md`
  and `state/**`, not here.

## Other operations surfaces

Use `docs/operations/AGENTS.md` for the registered operations-surface classes
and non-default packet/package routing.

Visible non-default packet evidence:

- `docs/operations/task_2026-04-16_dual_track_metric_spine/`
- `docs/operations/task_2026-04-16_function_naming_freshness/`
- `docs/operations/task_2026-04-19_code_review_graph_topology_bridge/`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`
- `docs/operations/task_2026-04-19_workspace_artifact_sync/`
- `docs/operations/task_2026-04-20_code_impact_graph_context_pack/`
- `docs/operations/task_2026-04-20_code_review_graph_online_context/`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/`
- `docs/operations/task_2026-04-21_gate_f_data_backfill/`

## Next action

- Later reference deletion/archive work requires a separate packet.
- Keep later deletion/archive work in a separate packet.
- Keep unrelated dirty work and local archive inputs untouched
