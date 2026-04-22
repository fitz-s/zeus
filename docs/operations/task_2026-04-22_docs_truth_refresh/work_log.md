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
