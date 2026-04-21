# Docs Reclassification / Reference Extraction Work Log

Date: 2026-04-21
Branch: data-improve
Task: P0 docs registry and canonical reference anchors.

Changed files:

- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/context_budget.yaml`
- `architecture/data_rebuild_topology.yaml`
- `architecture/docs_registry.yaml`
- `architecture/map_maintenance.yaml`
- `architecture/reference_replacement.yaml`
- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/authority/zeus_current_architecture.md`
- `docs/authority/zeus_current_delivery.md`
- `docs/authority/zeus_data_rebuild_adr.md`
- `docs/known_gaps.md` -> `docs/operations/known_gaps.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/reference/AGENTS.md`
- `docs/reference/zeus_architecture_reference.md`
- `docs/reference/zeus_market_settlement_reference.md`
- `docs/reference/zeus_data_and_replay_reference.md`
- `docs/reference/zeus_failure_modes_reference.md`
- `docs/runbooks/task_2026-04-15_data_math_operator_runbook.md`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_docs_checks.py`
- `scripts/topology_doctor_registry_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/plan.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json`

Summary:

- added `architecture/docs_registry.yaml` with seeded docs classification and
  parent-entry coverage semantics
- moved `docs/known_gaps.md` to `docs/operations/known_gaps.md`
- created four canonical reference anchor files
- updated routing docs and reference/operations registries
- added docs-registry checks to `topology_doctor --docs`
- added targeted tests for docs-registry schema, parent coverage, visible docs
  classification, and direct-reference leaks
- registered Gate F and ralplan runtime artifacts as preflight
  `LOCAL_ADAPTATION`

Verification:

- `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py scripts/topology_doctor_map_maintenance.py` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `python scripts/topology_doctor.py --history-lore --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 42 passed, 139 deselected
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P0 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P0 files> --plan-evidence .omx/plans/docs-reclassification-p0-ralplan-revised.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P0 files> --work-record-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P0 files> --receipt-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P0 files> --plan-evidence .omx/plans/docs-reclassification-p0-ralplan-revised.md --work-record-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md --receipt-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json --json` -> ok
- `git diff -- docs/authority/zeus_current_architecture.md docs/authority/zeus_current_delivery.md docs/authority/zeus_data_rebuild_adr.md` -> path/routing updates only

Pre-close review:

- Critic: PASS. P0 stayed within docs reclassification scaffolding: registry,
  known-gaps path move, reference anchors, routing/manifests, and targeted
  checker/tests. It did not perform P1 extraction/demotion or archive ingestion.
- Verifier: PASS. Authority-doc changes are path/routing-only; no `src/**`,
  `state/**`, `raw/**`, `docs/archives/**`, or `.code-review-graph/graph.db`
  paths are included in the P0 staged set.

Next:

- commit P0, then run post-close status checks before opening P1

## P0 post-close

Date: 2026-04-21
Commit: `b1a9761`

Post-close review:

- Critic: PASS. P0 installed docs classification scaffolding and anchors
  without P1 extraction/demotion, source/runtime changes, graph DB changes, or
  archive-body work.
- Verifier: PASS. `python scripts/topology_doctor.py --docs --json`,
  `python scripts/topology_doctor.py --context-budget --json`, and
  `python scripts/topology_doctor.py --reference-replacement --json` pass after
  commit.

Next:

- run package P0 follow-up review
- do not start P1 until review says `proceed_to_p1`

## P1 implementation

Date: 2026-04-21
Task: Extract durable reference content and demote legacy root snapshots.

Changed files:

- `architecture/context_budget.yaml`
- `architecture/artifact_lifecycle.yaml`
- `architecture/docs_registry.yaml`
- `architecture/reference_replacement.yaml`
- `architecture/topology.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/artifacts/AGENTS.md`
- `docs/zeus-architecture-deep-map.md` -> `docs/artifacts/zeus_architecture_deep_map_2026-04-16.md`
- `docs/operations/current_state.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/plan.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json`
- `docs/reference/AGENTS.md`
- `docs/settlement-source-provenance.md` -> `docs/reference/settlement_source_provenance.md`
- `docs/reference/zeus_architecture_reference.md`
- `docs/reference/zeus_data_and_replay_reference.md`
- `docs/reference/zeus_failure_modes_reference.md`
- `docs/reference/zeus_market_settlement_reference.md`
- `docs/reports/AGENTS.md`
- `docs/zeus-pathology-registry.md` -> `docs/reports/zeus_pathology_registry_2026-04-16.md`
- `docs/zeus-refactor-plan.md` -> `docs/reports/zeus_refactor_plan_2026-04-16.md`
- `docs/zeus-system-constitution.md` -> `docs/reports/zeus_system_constitution_2026-04-16.md`
- `docs/runbooks/AGENTS.md`
- `docs/settlement-validation-workflow.md` -> `docs/runbooks/settlement_mismatch_triage.md`

Summary:

- populated the four compact canonical references for architecture,
  market/settlement, data/replay, and failure modes
- moved legacy docs-root snapshots out of `docs/` into their typed subroots
- kept detailed settlement source evidence as a conditional reference and
  settlement mismatch procedure as a runbook
- added not-default-read/evidence banners to demoted legacy sources
- updated docs routers, docs registry, reference-replacement matrix, topology
  root-allowed docs list, artifact lifecycle, and context-budget roles
- indexed P1 ralplan runtime artifacts in `runtime_artifact_inventory.md`

Verification:

- `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py scripts/topology_doctor_map_maintenance.py` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `python scripts/topology_doctor.py --history-lore --json` -> ok
- `python scripts/topology_doctor.py --artifact-lifecycle --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P1 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P1 files> --plan-evidence .omx/plans/docs-reclassification-p1-ralplan-revised.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P1 files> --work-record-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P1 files> --receipt-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P1 files> --plan-evidence .omx/plans/docs-reclassification-p1-ralplan-revised.md --work-record-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md --receipt-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 42 passed, 139 deselected
- `git diff --check -- <P1 files>` -> ok
- `git diff -- docs/authority` -> no changes

Pre-close review:

- Critic: PASS. P1 extracted durable summaries into compact canonical
  references, moved legacy root docs to typed subroots, and preserved detailed
  provenance/runbook evidence without deleting conditional sources or touching
  authority/source/runtime paths.
- Verifier: PASS. Docs registry, reference replacement, context budget,
  artifact lifecycle, planning-lock, work-record, receipt, map-maintenance,
  closeout, and targeted topology tests all pass. `.code-review-graph/graph.db`,
  `state/**`, archive bundles, and unrelated runbook dirty work remain
  outside the P1 staged set.

Next:

- commit P1 implementation
- run P1 follow-up review before any P2 deletion/archive decisions

## P1 follow-up review

Date: 2026-04-21
Commit: `84d6d25`
Task: Review P1 before P2.

Changed files: none for review.

Summary:

- P1 moved legacy root snapshots into typed subroots and populated canonical
  reference summaries.
- Post-commit docs checks pass.
- Default routers no longer point at old root snapshot paths.
- P1 preserved evidence while removing mixed-class files from `docs/` root.

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- negative router check for old root snapshot paths across default routers and
  manifests -> no matches

Verdict:

- `proceed_to_p2`

Next:

- implement P2 runbook and operations routing normalization

## P2 implementation

Date: 2026-04-21
Task: Normalize runbooks and operations routing.

Changed files:

- `architecture/artifact_lifecycle.yaml`
- `architecture/context_budget.yaml`
- `architecture/docs_registry.yaml`
- `docs/artifacts/AGENTS.md`
- `docs/runbooks/task_2026-04-19_tigge_cloud_download_zeus_wiring.md` -> `docs/artifacts/tigge_cloud_wiring_snapshot_2026-04-19.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/plan.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md`
- `docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json`
- `docs/runbooks/AGENTS.md`
- `docs/runbooks/tigge_cloud_download.md`

Summary:

- demoted the dated TIGGE local/cloud wiring note into artifacts evidence
- preserved the current TIGGE note body, including the 2026-04-21 rebalance
  evidence, under `docs/artifacts/tigge_cloud_wiring_snapshot_2026-04-19.md`
- added a compact durable `docs/runbooks/tigge_cloud_download.md` without VM
  project, IP, account, credential, or local absolute-path details
- rewrote runbook routing into durable operator, packet-scoped,
  contributor/workflow, and sensitive/local snapshot classes
- rewrote operations routing into live pointer, active support, active packet,
  packet evidence, and attached package input classes while preserving the
  explicit registry needed by docs checks
- recorded P1 follow-up review as `proceed_to_p2`
- indexed P2 ralplan/context runtime artifacts
- updated docs registry, artifact lifecycle, context budget, packet plan, and
  receipt

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --artifact-lifecycle --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P2 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P2 files> --plan-evidence .omx/plans/docs-reclassification-p2-ralplan-2026-04-21.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P2 files> --work-record-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P2 files> --receipt-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P2 files> --plan-evidence .omx/plans/docs-reclassification-p2-ralplan-2026-04-21.md --work-record-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/work_log.md --receipt-path docs/operations/task_2026-04-21_docs_reclassification_reference_extraction/receipt.json --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 42 passed, 139 deselected
- `git diff --check -- <P2 files>` -> ok
- `git diff -- docs/authority` -> no changes
- TIGGE snapshot SHA-256 after demotion:
  `699001cb3a31019c940f7e746bac61820a5e22d16a63edd7c7f15342a74a7cda`
  (matches pre-move worktree body)
- stale current-state freeze-text negative check -> no matches

Pre-close review:

- Critic: PASS. P2 uses demotion instead of risky sanitization for the
  environment-specific TIGGE note, preserves the current dirty evidence as an
  artifact, and keeps the durable runbook generic. The main tradeoff is that
  `current_state.md` still lists visible non-default packet paths because the
  docs checker requires them; it avoids restoring the old per-packet narrative
  diary.
- Verifier: PASS. Docs, context-budget, artifact-lifecycle, map-maintenance,
  planning-lock, work-record, change-receipts, closeout, targeted tests,
  diff-check, authority diff, stale freeze-text check, durable-runbook
  sensitive-string check, old-TIGGE-path router check, and TIGGE body hash all
  pass. Unrelated graph/state/archive/local workbook changes remain outside the
  P2 staged set.

Next:

- commit P2 implementation
- run P2 follow-up review before P3
