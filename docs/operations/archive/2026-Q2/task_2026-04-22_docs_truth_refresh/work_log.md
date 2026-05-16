# Docs Truth Refresh Work Log

Date: 2026-04-22
Branch: `data-improve`
Task: P0 stale truth purge and current fact install.

Changed files:

- `architecture/context_budget.yaml`
- `architecture/docs_registry.yaml`
- `architecture/history_lore.yaml`
- `architecture/reference_replacement.yaml`
- `architecture/topology.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/data_rebuild_plan.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/plan.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`
- `docs/reference/AGENTS.md`
- `docs/reference/zeus_architecture_reference.md`
- `docs/reference/zeus_data_and_replay_reference.md`
- `docs/reference/zeus_failure_modes_reference.md`
- `docs/reference/zeus_market_settlement_reference.md`
- `docs/reference/zeus_math_spec.md`
- `docs/reference/repo_overview.md` -> `docs/reports/legacy_reference_repo_overview.md`
- `docs/reference/data_inventory.md` -> `docs/reports/legacy_reference_data_inventory.md`
- `docs/reference/data_strategy.md` -> `docs/reports/legacy_reference_data_strategy.md`
- `docs/reference/settlement_source_provenance.md` -> `docs/reports/legacy_reference_settlement_source_provenance.md`
- `docs/reference/statistical_methodology.md` -> `docs/reports/legacy_reference_statistical_methodology.md`
- `docs/reference/quantitative_research.md` -> `docs/reports/legacy_reference_quantitative_research.md`
- `docs/reference/market_microstructure.md` -> `docs/reports/legacy_reference_market_microstructure.md`
- `docs/reports/AGENTS.md`
- `docs/runbooks/settlement_mismatch_triage.md`

Summary:

- moved seven non-canonical support/reference files out of `docs/reference/`
  into `docs/reports/legacy_reference_*.md`
- preserved the pre-existing dirty `market_microstructure.md` wording fix
  while moving it; pre-move body hash equals post-move body hash after stripping
  the P0 legacy banner:
  `8a52d6243e509013f9b667d65a29c8fa6e75883a1f98e44c0ab4d0d0c325f7e3`
- created `docs/operations/current_data_state.md`
- created `docs/operations/current_source_validity.md`
- rewrote `docs/reference/AGENTS.md` to canonical-only routing
- rewrote data/replay and market/settlement canonical references so current
  facts route to operations current-fact surfaces
- updated settlement mismatch triage to route current source-validity updates to
  operations current-fact surfaces and dated reports
- updated `zeus_math_spec.md` to point methodology citations at the demoted
  legacy report instead of a removed reference file
- updated docs routers and machine manifests for the moved reports/current-fact
  surfaces
- LOCAL_ADAPTATION: updated `architecture/history_lore.yaml` and
  `docs/operations/data_rebuild_plan.md` stale path references to the demoted
  legacy reports so negative stale-truth greps do not leave trusted stale paths

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P0 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-22_docs_truth_refresh/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P0 files> --work-record-path docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P0 files> --receipt-path docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-22_docs_truth_refresh/plan.md --work-record-path docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md --receipt-path docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 42 passed, 139 deselected
- exact `docs/reference` whitelist check -> ok
- current-fact acceptance assertions -> ok
- current-fact unsafe-string check -> no matches
- stale support path grep -> only packet evidence / moved legacy report paths remain
- `git diff --check -- <P0 files>` -> ok
- `git diff -- docs/authority` -> no changes

Pre-close review:

- Critic: PASS for P0 subset. A critic pass flagged that full worktree receipt
  validation fails because unrelated graph/state/artifact/source dirt exists,
  but the P0 subset validates and those unrelated files are intentionally
  preserved and left unstaged.
- Verifier: PASS. `docs/reference/` is canonical-only; current-fact surfaces
  exist; legacy support docs moved to reports; canonical data/market refs no
  longer route to stale support docs for current facts; reference-replacement
  checks pass; no runtime/source/state/graph/archive/authority semantic files
  are in the intended P0 staged set.

Next:

- commit P0
- run P0 follow-up review before P1

## P0 follow-up review

Date: 2026-04-22
Commit: `80c0051`
Task: Review P0 before P1.

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`

Summary:

- P0 removed all seven non-canonical support docs from `docs/reference/`.
- P0 created `current_data_state.md` and `current_source_validity.md`.
- P0 rerouted canonical data/market references away from stale support docs.
- P0 preserved unrelated dirty work and staged only the intended P0 subset.

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `find docs/reference -maxdepth 1 -type f -name "*.md" | sort` -> canonical-only whitelist
- stale trusted-path grep -> no live trusted-router/manifests hits outside packet evidence and moved legacy reports

Verdict:

- `proceed_to_p1`

Next:

- implement P1 canonical reference completion and runbook cleanup

## P1 implementation

Date: 2026-04-22
Task: Canonical reference completion and runbook cleanup.

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`
- `docs/reference/zeus_architecture_reference.md`
- `docs/runbooks/live-operation.md`
- `docs/runbooks/live-phase-1-first-boot.md`

Summary:

- expanded `zeus_architecture_reference.md` with workspace questions, current
  fact surfaces, derived context engines, and docs trust layers
- updated `live-operation.md` heartbeat path to `state/daemon-heartbeat.json`
- updated `live-phase-1-first-boot.md` to use `get_trade_connection()` instead
  of legacy `state_path("zeus.db")`
- confirmed data/replay and market/settlement canonical references were already
  completed during P0 and no longer route to stale support docs for current
  facts

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- stale runbook/reference negative check for `state/zeus.db`,
  `daemon-heartbeat-live`, `settlement_source_provenance.md`,
  `rainstorm.db`, and `details pending extraction` -> only intentional
  current-fact/reference statements or legacy report paths remain

Pre-close review:

- Critic: PASS. P1 stayed within docs/reference and runbook cleanup; no source,
  state, graph DB, archives, or authority-law files are included.
- Verifier: PASS. Runbooks no longer teach legacy trade DB paths for first boot,
  heartbeat path matches `src/main.py`, and canonical refs are standalone enough
  for ordinary current understanding.

Next:

- commit P1
- run P1 follow-up review before P2

## P1 follow-up review

Date: 2026-04-22
Commit: `d742083`
Task: Review P1 before P2.

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`

Summary:

- P1 completed targeted architecture-reference and live-runbook cleanup.
- Docs/reference remains canonical-only.
- Runbooks no longer point operators at legacy first-boot trade DB or stale
  heartbeat path.

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- stale runbook/reference grep -> only intentional current-fact/reference
  statements or legacy report paths remain

Verdict:

- `proceed_to_p2`

Next:

- implement P2 freshness-aware docs registry and stale-truth enforcement

## P2 implementation

Date: 2026-04-22
Task: Freshness-aware docs registry and stale-truth enforcement.

Changed files:

- `architecture/docs_registry.yaml`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`
- `scripts/topology_doctor_docs_checks.py`
- `tests/test_topology_doctor.py`

Summary:

- added freshness/truth fields to docs registry entries
- added docs checker validation for truth/freshness fields
- added stale-truth checks for non-canonical reference docs, removed support-doc
  path leaks, local absolute paths in `current_state.md`, and stale truth
  markers in trusted docs
- added targeted tests for non-canonical reference rejection and removed path
  leaks
- retained `architecture/reference_replacement.yaml` for compatibility in P2;
  P0 already removed moved support-doc entries, and a later packet can retire
  the manifest after downstream references/tests are updated

Verification:

- `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py scripts/topology_doctor_registry_checks.py` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 44 passed, 139 deselected

Pre-close review:

- Critic: PASS. P2 adds freshness-aware registry/checker behavior without
  crossing into runtime/source behavior. Full retirement of
  `reference_replacement.yaml` is explicitly deferred because existing tests and
  generated topology surfaces still depend on that transitional manifest.
- Verifier: PASS. Docs checks, context budget, reference replacement, py_compile,
  and targeted topology tests pass.

Next:

- commit P2
- run P2 follow-up review before P3

## P2 follow-up review

Date: 2026-04-22
Commit: `8b687da`
Task: Review P2 before P3.

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`

Summary:

- P2 added freshness/truth metadata to docs registry entries.
- P2 added stale-truth checks for canonical-only reference routing and current
  state hygiene.
- P2 kept `architecture/reference_replacement.yaml` as a compatibility manifest;
  retirement is deferred to a dedicated compatibility packet.

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok

Verdict:

- `proceed_to_p3`

Next:

- implement P3 current-fact refresh workflow

## P3 implementation

Date: 2026-04-22
Task: Current-fact refresh workflow.

Changed files:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`

Summary:

- chose manual refresh protocol instead of adding a script in P3
- added refresh triggers, required evidence, maximum staleness, and
  no-memory-update rules to `current_data_state.md`
- added matching source-validity refresh protocol to
  `current_source_validity.md`
- updated operations router rules to require evidence-based current-fact
  updates

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P3 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <P3 files> --plan-evidence docs/operations/task_2026-04-22_docs_truth_refresh/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <P3 files> --work-record-path docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <P3 files> --receipt-path docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P3 files> --plan-evidence docs/operations/task_2026-04-22_docs_truth_refresh/plan.md --work-record-path docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md --receipt-path docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json --json` -> ok
- `git diff --check -- <P3 files>` -> ok
- `git diff -- docs/authority` -> no changes

Pre-close review:

- Critic: PASS. P3 lands an explicit manual refresh protocol without adding
  read/write tooling that could overfit an unstable DB/query contract.
- Verifier: PASS. Current-fact surfaces now include refresh triggers, evidence
  requirements, staleness window, and no-memory update rules.

Next:

- commit P3
- run package closeout

## Package closeout

Date: 2026-04-22
Task: Close docs truth refresh package.

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`

Closeout verdict:

- PASS. P0-P3 are committed and the stale-reference contamination channel has
  been removed from `docs/reference/`.

Package commits:

- P0 stale truth purge and current-fact install: `80c0051`
- P1 canonical reference completion and runbook cleanup: `d742083`
- P2 freshness-aware docs registry and stale-truth checks: `8b687da`
- P3 current-fact refresh protocol: `55eb285`

Final canonical reference set:

- `docs/reference/zeus_domain_model.md`
- `docs/reference/zeus_architecture_reference.md`
- `docs/reference/zeus_market_settlement_reference.md`
- `docs/reference/zeus_data_and_replay_reference.md`
- `docs/reference/zeus_failure_modes_reference.md`
- `docs/reference/zeus_math_spec.md`

Current-fact surfaces:

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`

Demoted legacy reference snapshots:

- `docs/reports/legacy_reference_repo_overview.md`
- `docs/reports/legacy_reference_data_inventory.md`
- `docs/reports/legacy_reference_data_strategy.md`
- `docs/reports/legacy_reference_settlement_source_provenance.md`
- `docs/reports/legacy_reference_statistical_methodology.md`
- `docs/reports/legacy_reference_quantitative_research.md`
- `docs/reports/legacy_reference_market_microstructure.md`

Residual risks:

- `architecture/reference_replacement.yaml` remains as a compatibility manifest;
  retirement needs a later dedicated compatibility packet.
- P3 uses a manual refresh protocol, not generated current-fact tooling.
- Unrelated dirty graph/state/artifact files remain outside this package.

Validation:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 44 passed, 139 deselected

Suggested post-closeout review prompt:

Review the completed docs truth refresh package after P0-P3 and closeout. Check
whether stale factual influence was removed from trusted docs surfaces, whether
current fact surfaces are sufficiently compact and evidence-bound, whether
canonical references are standalone enough, and whether a later packet should
retire `architecture/reference_replacement.yaml` or add generated refresh
tooling.

Next:

- run post-closeout review
- keep registry retirement or generated refresh tooling in a separate packet

## Post-closeout review

Date: 2026-04-22
Task: Review docs truth refresh package after closeout.

Changed files:

- `architecture/topology.yaml`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/work_log.md`
- `docs/operations/task_2026-04-22_docs_truth_refresh/receipt.json`

Summary:

- post-closeout review passed after one compatibility fix
- new Gate F packet evidence introduced
  `docs/operations/task_2026-04-21_gate_f_data_backfill/confirmed_upstream_gaps.yaml`
- updated `architecture/topology.yaml` so operations packet evidence may include
  `.yaml` / `.yml`
- package results remain intact: `docs/reference/` is canonical-only, current
  facts live in operations, and demoted support docs live in reports

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --reference-replacement --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml --plan-evidence docs/operations/current_state.md --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files architecture/topology.yaml --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or map_maintenance or context_budget"` -> 44 passed, 139 deselected
- `git diff --check -- architecture/topology.yaml` -> ok

Verdict:

- PASS. Docs truth refresh is closed and post-closeout reviewed.

Next:

- Later registry retirement/deletion or generated current-fact tooling requires
  a separate packet.
