# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: Midstream Remediation (test-currency + active-failure + D3/D4/D6 antibody wave)
- Active package source: `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- Active execution packet: `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-23_midstream_remediation/receipt.json`
- Status: W0 packet opened 2026-04-23; W1 executing (T1.a + T1.b + T3.1 + T3.3 + T7.b + T4.0)
- Authority source for the 36-slice plan: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`

## Concurrent parallel packet

A parallel agent is executing upstream data-readiness repair
(`docs/operations/task_2026-04-23_data_readiness_remediation/`).
Scope-disjoint from midstream by design: upstream owns `src/data/*`,
`scripts/ingest/*`, `src/state/db.py` forecasts schema, launchd plists.
Midstream owns `tests/*`, `src/strategy/*`, `src/engine/evaluator.py`,
`src/engine/cycle_runtime.py`, `src/execution/{executor,exit_triggers}.py`,
`src/contracts/*`. Shared files (`current_state.md`, `known_gaps.md`,
`architecture/source_rationale.yaml`, `architecture/script_manifest.yaml`)
are touched only at slice boundaries with `git pull --rebase` immediately
before the edit.

## Required evidence

- `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/receipt.json`

## Freeze point

- Midstream Remediation packet may edit the files listed in its plan's
  "Wave N scope" allowed_files sections. It must not mutate runtime DBs
  (`state/**`), `.code-review-graph/graph.db`, or `docs/authority/**`
  broad rewrites. It must not touch upstream `src/data/*` (reserved for
  the concurrent data-readiness packet).

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
- `docs/operations/task_2026-04-23_graph_rendering_integration/`
- `docs/operations/task_2026-04-23_data_readiness_remediation/`

## Next action

- Execute W1 of the Midstream Remediation plan: T1.a 15-file header wave,
  T1.b provenance_registry.yaml content audit, T3.1 7-caller signature
  drift fix, T3.3 canonical position_current schema bootstrap alignment,
  T7.b AST guard test, T4.0 persistence design doc.
- Each slice: critic-reviewed by con-nyx before commit; clean pull-rebase
  on every commit to sync with concurrent upstream-data-readiness agent.
- Preserve unrelated dirty work and the concurrent upstream agent's
  in-flight edits.
