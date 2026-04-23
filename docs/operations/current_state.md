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

**Data-readiness remediation CLOSED 2026-04-23** (8/8 packets APPROVED by
critic-opus). Closure banner + App-C R3-## traceability:
`docs/operations/task_2026-04-23_data_readiness_remediation/first_principles.md`.
Full audit trail: `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md`.
Outcome: `settlements` table is canonical-authority-grade (1,561 rows,
1,469 VERIFIED + 92 QUARANTINED; INV-14 identity + provenance_json +
`settlements_authority_monotonic` trigger). Rollback chain preserved on
disk (4 snapshot md5 sidecars committed).

**DR-33-A** (live-harvester enablement, code-only scaffold): landed
2026-04-23 at `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/`.
Feature-flagged `ZEUS_HARVESTER_LIVE_ENABLED` default OFF — no runtime
behavior change until explicit operator flip under DR-33-C review.

Scope boundary with midstream retained: upstream-data-readiness owned
`src/data/*`, `src/execution/harvester.py`, `src/state/db.py` settlements
schema (the P-B migration added 5 columns + trigger), plus the new DR-33-A
additions to `architecture/source_rationale.yaml::write_routes::settlement_write`
and `architecture/test_topology.yaml`. Midstream owns `tests/*`,
`src/strategy/*`, `src/engine/evaluator.py`, `src/engine/cycle_runtime.py`,
`src/execution/{executor,exit_triggers}.py`, `src/contracts/*`. Shared
files (`current_state.md`, `known_gaps.md`,
`architecture/source_rationale.yaml`, `architecture/script_manifest.yaml`,
`architecture/test_topology.yaml`) were touched by upstream only at slice
boundaries with surgical diffs to avoid midstream work loss.

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
