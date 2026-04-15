# Work Log

## 2026-04-15 — Context Pack And Artifact Routing

Date: 2026-04-15
Branch: data-improve
Task: Reduce agent context burden and make topology outputs task-shaped.
Changed files: `scripts/topology_doctor.py`, `architecture/context_pack_profiles.yaml`, `architecture/core_claims.yaml`, `tests/test_topology_doctor.py`, `workspace_map.md`, `docs/operations/task_2026-04-14_topology_context_efficiency/plan.md`
Summary: Added package-review and debug context packs with provisional context warnings, proof-backed claim surfacing, route/repo health separation, and tiered lore summaries.
Verification: `pytest -q tests/test_topology_doctor.py -k 'context_pack or package_review or debug_context or core_claims or core_map or impact'`; `python scripts/topology_doctor.py --context-packs --summary-only`; `python scripts/topology_doctor.py --context-budget --summary-only`.
Next: Add a low-friction work-record and artifact classification gate so future agents leave a small factual record and place files by lifecycle.

## 2026-04-15 — Work Packet Archive Reorganization

Date: 2026-04-15
Branch: data-improve
Task: Move the data-improvement large package out of active docs and organize archived work packets by git lineage.
Changed files: `docs/operations/AGENTS.md`, `docs/operations/data_rebuild_plan.md`, `workspace_map.md`, ignored `docs/archives/work_packets/**`
Summary: Moved the large data-improvement package to `docs/archives/work_packets/branches/data-improve/data_rebuild/2026-04-13_zeus_data_improve_large_pack/` and grouped archived work packets under branch/domain/date paths.
Verification: `python scripts/topology_doctor.py --docs --summary-only`; `python scripts/topology_doctor.py --context-budget --summary-only`; `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...`; `rg "docs/data improve large pack"`.
Next: Keep future completed packages under `docs/archives/work_packets/branches/<branch>/<program_domain>/YYYY-MM-DD_slug/` or `trees/<tree-name>/...` when worktree-bound.

## 2026-04-15 — Artifact Lifecycle And Work Record Gate

Date: 2026-04-15
Branch: data-improve
Task: Require agents to leave a lightweight factual record and classify generated files by lifecycle.
Changed files: `architecture/artifact_lifecycle.yaml`, `scripts/topology_doctor.py`, `tests/test_topology_doctor.py`, `AGENTS.md`, `workspace_map.md`, `docs/operations/AGENTS.md`, `docs/operations/task_2026-04-14_topology_context_efficiency/AGENTS.md`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Added artifact lifecycle classes, approved work-record paths, archive lineage rules, and `--work-record` / `--artifact-lifecycle` topology checks.
Verification: `pytest -q tests/test_topology_doctor.py -k 'artifact_lifecycle or work_record'`; `python scripts/topology_doctor.py --artifact-lifecycle --summary-only`; `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md --summary-only`; `python scripts/topology_doctor.py --context-budget --summary-only`.
Next: Use the work-record check during packet closeout alongside planning-lock and map-maintenance.

## 2026-04-15 — Docs Boundary Health Checks

Date: 2026-04-15
Branch: data-improve
Task: Remove false clarity from docs mesh checks by making hidden subtrees, binary artifacts, and broken internal paths machine-visible.
Changed files: `architecture/topology.yaml`, `architecture/kernel_manifest.yaml`, `architecture/map_maintenance.yaml`, `architecture/artifact_lifecycle.yaml`, `docs/AGENTS.md`, `docs/README.md`, `docs/artifacts/AGENTS.md`, `docs/artifacts/zeus_data_inventory.xlsx`, `docs/to-do-list/AGENTS.md`, `docs/to-do-list/zeus_data_improve_bug_audit_75.xlsx`, `docs/operations/current_state.md`, `scripts/topology_doctor.py`, `tests/test_topology_doctor.py`, `workspace_map.md`
Summary: Classified `docs/to-do-list/` as active checklist evidence, kept the bug-audit workbook at the path other agents use, classified `docs/artifacts/` as evidence-only, added docs-subroot/non-md/broken-path checks, and listed the topology context sidecar in current state.
Verification: `pytest -q tests/test_topology_doctor.py -k 'docs_mode or hidden_docs or broken_internal'`; `python scripts/topology_doctor.py --docs --summary-only`; `python scripts/topology_doctor.py --context-budget --summary-only`; `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md --summary-only`.
Next: Plan larger phase-level improvements with RALPLAN after this closeout.

## 2026-04-15 — Active Operations Registry Check

Date: 2026-04-15
Branch: data-improve
Task: Make active operations sidecars and backlogs machine-visible from `current_state.md`.
Changed files: `architecture/topology.yaml`, `architecture/topology_schema.yaml`, `docs/operations/current_state.md`, `scripts/topology_doctor.py`, `tests/test_topology_doctor.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Added `active_operations_registry` metadata and a docs-mode check that requires current state to list primary packet, active sidecars, active backlog, and next packet with real, registered operations surfaces.
Verification: `pytest -q tests/test_topology_doctor.py -k 'docs_mode or current_state or hidden_docs or broken_internal'`; `python scripts/topology_doctor.py --docs --summary-only`.
Next: Continue later phases with liminal artifact role control and config fact demotion.

## 2026-04-15 — Liminal Artifact Classification

Date: 2026-04-15
Branch: data-improve
Task: Classify high-risk semi-authority surfaces without adding broad role-crossing enforcement.
Changed files: `architecture/artifact_lifecycle.yaml`, `architecture/topology_schema.yaml`, `scripts/topology_doctor.py`, `tests/test_topology_doctor.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Added liminal artifact roles for `zeus_math_spec`, `history_lore`, core claims, reference replacement claims, and this packet's work log; validation only checks classification fields, allowed roles, allowed route classes, and path existence.
Verification: `python scripts/topology_doctor.py --artifact-lifecycle --summary-only`; `pytest -q tests/test_topology_doctor.py -k 'artifact_lifecycle or work_record'`.
Next: Phase 3B role-crossing enforcement remains deferred unless classification reveals drift.

## 2026-04-15 — Config Volatile Fact Demotion

Date: 2026-04-15
Branch: data-improve
Task: Keep `config/AGENTS.md` as rule/cadence/router and move dated market-station facts into evidence.
Changed files: `config/AGENTS.md`, `docs/artifacts/AGENTS.md`, `docs/artifacts/polymarket_city_settlement_audit_2026-04-14.md`, `scripts/topology_doctor.py`, `tests/test_topology_doctor.py`, `architecture/topology_schema.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved the 2026-04-14 Polymarket city settlement-source/station snapshot into an evidence artifact and added a docs-mode lint that rejects dated external market fact snapshots in `config/AGENTS.md`.
Verification: `python scripts/topology_doctor.py --docs --summary-only`; `pytest -q tests/test_topology_doctor.py -k 'docs_mode or config_agents'`.
Next: Continue toward compiled topology read-model planning after closing remaining small review fixes.

## 2026-04-15 — Derived Compiled Topology Read Model

Date: 2026-04-15
Branch: data-improve
Task: Add a generated read model that summarizes topology health without becoming authority.
Changed files: `scripts/topology_doctor.py`, `tests/test_topology_doctor.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Added `topology_doctor.py compiled-topology`, a `derived_not_authority` output with source manifests, docs subroots, reviewer-visible routes, local-only archive route, active operations surfaces, liminal artifact roles, broken visible routes, and unclassified docs artifacts.
Verification: `python scripts/topology_doctor.py compiled-topology --json`; `pytest -q tests/test_topology_doctor.py -k 'compiled_topology or core_map'`.
Next: Use the compiled read model as a query surface; do not promote it into authority precedence.

## 2026-04-15 — Docs Map-Maintenance Expansion

Date: 2026-04-15
Branch: data-improve
Task: Make docs-side hidden branches harder to introduce by accident.
Changed files: `architecture/map_maintenance.yaml`, `tests/test_topology_doctor.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Added map-maintenance companion rules for top-level docs files/artifacts and new docs subtrees, requiring docs registry and topology updates before closeout.
Verification: `pytest -q tests/test_topology_doctor.py -k 'map_maintenance and docs'`; `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ... --summary-only`.
Next: Phase 7 modularization should wait until golden-output parity fixtures are defined.

## 2026-04-15 — Topology Doctor CLI Facade Split

Date: 2026-04-15
Branch: data-improve
Task: Create the first safe topology_doctor modularization seam without moving checker logic.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_cli.py`, `architecture/script_manifest.yaml`, `tests/test_topology_doctor.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved CLI parser/dispatch/rendering into `topology_doctor_cli.py`; `topology_doctor.py` remains the checker/build kernel and delegates `main()` to the CLI facade. Added/kept CLI JSON parity tests before deeper splits.
Verification: `pytest -q tests/test_topology_doctor.py -k 'cli_json_parity or compiled_topology or docs_mode'`; `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_cli.py`.
Next: Future modularization should split one checker family at a time behind the same CLI and parity tests.

## 2026-04-15 — Docs Checker Family Split

Date: 2026-04-15
Branch: data-improve
Task: Extract the first checker family from `topology_doctor.py` behind parity tests.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_docs_checks.py`, `architecture/script_manifest.yaml`, `tests/test_topology_doctor.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved docs-specific checker implementations into `topology_doctor_docs_checks.py` while preserving wrapper functions in `topology_doctor.py` for existing tests and callers.
Verification: `pytest -q tests/test_topology_doctor.py -k 'docs_mode or current_state or hidden_docs or broken_internal or compiled_topology or cli_json_parity'`; `python scripts/topology_doctor.py --docs --summary-only`; `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py scripts/topology_doctor_cli.py`.
Next: Future splits should move one checker family at a time with the same wrapper/parity pattern.

## 2026-04-15 — Artifact Checker Family Split

Date: 2026-04-15
Branch: data-improve
Task: Extract the artifact lifecycle/work-record checker family behind existing wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_artifact_checks.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved artifact lifecycle and work-record checker implementations into `topology_doctor_artifact_checks.py`; `topology_doctor.py` keeps wrapper functions for existing callers and CLI behavior.
Verification: `pytest -q tests/test_topology_doctor.py -k 'artifact_lifecycle or work_record'`; `python scripts/topology_doctor.py --artifact-lifecycle --summary-only`; `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md --summary-only`; `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_artifact_checks.py`.
Next: Avoid splitting git-status-dependent map-maintenance until unrelated script dirty state is isolated.

## 2026-04-15 — Reference/Core Claim Checker Split

Date: 2026-04-15
Branch: data-improve
Task: Extract the reference replacement and core-claim checker family behind existing wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_reference_checks.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved reference replacement and core-claim checker implementations into `topology_doctor_reference_checks.py`; `topology_doctor.py` keeps wrapper functions for existing callers, core-map claim lookup, and CLI behavior.
Verification: `pytest -q tests/test_topology_doctor.py -k 'reference_replacement or core_claims or core_map or cli_json_parity'`; `python scripts/topology_doctor.py --reference-replacement --summary-only`; `python scripts/topology_doctor.py --core-claims --summary-only`; `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_reference_checks.py`.
Next: Defer map-maintenance splitting until dirty script changes are isolated.

## 2026-04-15 — Context Pack Builder Split

Date: 2026-04-15
Branch: data-improve
Task: Extract the context-pack and impact builder family behind existing wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_context_pack.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved `build_impact`, context-pack profile validation, debug/package-review packet builders, lore layering, route/repo health, and related helper builders into `topology_doctor_context_pack.py`; `topology_doctor.py` keeps wrappers and the existing CLI/API surface.
Verification: `pytest -q tests/test_topology_doctor.py -k 'context_pack or package_review or debug_context or impact or cli_json_parity'`; `python scripts/topology_doctor.py --context-packs --summary-only`; `python scripts/topology_doctor.py context-pack --pack-type debug ... --json`.
Next: Defer `build_digest` and map-maintenance splits until separate golden parity coverage is added.

## 2026-04-15 — Digest Builder Split

Date: 2026-04-15
Branch: data-improve
Task: Extract the digest builder family behind existing wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_digest.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved data-rebuild digest, script-lifecycle digest, compact lore cards, matched history lore, and `build_digest` into `topology_doctor_digest.py`; `topology_doctor.py` keeps wrapper functions and navigation behavior.
Verification: `pytest -q tests/test_topology_doctor.py -k 'digest or navigation or context_budget or cli_json_parity'`; `python scripts/topology_doctor.py digest --task ... --json`; `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_digest.py`.
Next: Avoid splitting map-maintenance until dirty script state is isolated; remaining safe split candidate is compiled-topology/core-map only if additional parity is needed.

## 2026-04-15 — Core Map And Compiled Topology Split

Date: 2026-04-15
Branch: data-improve
Task: Extract the core-map and compiled-topology builder family behind existing wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_core_map.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved core-map profile lookup, proof validation, core-map building, core-map validation, locator lookup, and compiled topology read-model generation into `topology_doctor_core_map.py`; `topology_doctor.py` keeps wrappers and existing CLI/API behavior.
Verification: `pytest -q tests/test_topology_doctor.py -k 'core_map or compiled_topology or cli_json_parity'`; `python scripts/topology_doctor.py core-map --profile probability-chain --json`; `python scripts/topology_doctor.py compiled-topology --json`.
Next: Map-maintenance remains excluded from Phase 7 until dirty script state is isolated.

## 2026-04-15 — Governance/Policy Checker Split

Date: 2026-04-15
Branch: data-improve
Task: Extract policy-oriented topology checks while preserving the existing CLI and compatibility wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_policy_checks.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved history-lore validation, context-budget enforcement, planning-lock checks, idiom validation, self-check coherence, and runtime-mode checks into `topology_doctor_policy_checks.py`; `topology_doctor.py` keeps wrapper functions for tests, callers, and CLI parity.
Verification: `pytest -q tests/test_topology_doctor.py -k 'history_lore or context_budget or planning_lock or idioms or self_check or runtime_modes or cli_json_parity'`; `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_policy_checks.py scripts/topology_doctor_cli.py`; `python scripts/topology_doctor.py --history-lore --summary-only`; `python scripts/topology_doctor.py --context-budget --summary-only`; `python scripts/topology_doctor.py --planning-lock ... --summary-only`; `python scripts/topology_doctor.py --idioms --summary-only`; `python scripts/topology_doctor.py --self-check-coherence --summary-only`; `python scripts/topology_doctor.py --runtime-modes --summary-only`; Spark read-only review PASS.
Next: Continue Phase 7 with remaining non-map-maintenance checker families only.

## 2026-04-15 — Remaining Non-Map Checker Splits

Date: 2026-04-15
Branch: data-improve
Task: Complete Phase 7 modularization except map-maintenance, which remains in `topology_doctor.py`.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_registry_checks.py`, `scripts/topology_doctor_source_checks.py`, `scripts/topology_doctor_test_checks.py`, `scripts/topology_doctor_script_checks.py`, `scripts/topology_doctor_data_rebuild_checks.py`, `scripts/topology_doctor_packet_prefill.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved registry/root/docs-strict checks, source-rationale and scoped-AGENTS checks, test-topology checks, script-manifest checks, data-rebuild checks, and packet-prefill/invariant-slice builders into import-only helper modules. `topology_doctor.py` keeps compatibility wrappers and retains map-maintenance logic for a later isolated package.
Verification: `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_registry_checks.py scripts/topology_doctor_source_checks.py scripts/topology_doctor_test_checks.py scripts/topology_doctor_script_checks.py scripts/topology_doctor_data_rebuild_checks.py scripts/topology_doctor_packet_prefill.py`; `pytest -q tests/test_topology_doctor.py -k 'scripts_mode_rejects_long_lived_one_off_script_name or scripts_mode_rejects_ephemeral_without_delete_trigger or scripts_mode_rejects_malformed_ephemeral_name or scripts_mode_rejects_expired_ephemeral_script'`; `pytest -q tests/test_topology_doctor.py -k 'packet_prefill or invariants_slice or context_budget or planning_lock or idioms or self_check or runtime_modes or cli_json_parity'`; `python scripts/topology_doctor.py --docs --summary-only`; `python scripts/topology_doctor.py --data-rebuild --summary-only`; `python scripts/topology_doctor.py --context-packs --summary-only`; `python scripts/topology_doctor.py digest ... --json`; `python scripts/topology_doctor.py context-pack ... --json`; `python scripts/topology_doctor.py core-map --profile probability-chain --json`; `python scripts/topology_doctor.py compiled-topology --json`; `python scripts/topology_doctor.py --work-record ... --summary-only`; `python scripts/topology_doctor.py --planning-lock ... --summary-only`; `git diff --check -- <changed files>`. Full `strict`/`source`/`tests`/`scripts` checks are currently blocked by unrelated active workspace changes (unregistered K-package tests, temporary scripts, state logs, and `src/contracts/ensemble_snapshot_provenance.py` rationale gap).
Next: Map-maintenance remains intentionally excluded and should be split only after the unrelated dirty script/state/test surfaces are isolated.

## 2026-04-15 — Prior-Agent Map Debt Cleanup

Date: 2026-04-15
Branch: data-improve
Task: Close topology debt left by earlier K-package/debug iterations so Phase 7 final map-maintenance work can run from a cleaner surface.
Changed files: `.gitignore`, `architecture/test_topology.yaml`, `architecture/source_rationale.yaml`, `architecture/script_manifest.yaml`, `scripts/auto_pause_live.sh`, `scripts/audit_polymarket_city_settlement.py`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Classified K1/K2/K4/K5/supervisor regression tests, removed stale `test_config.py` high-sensitivity skip metadata, registered `ensemble_snapshot_provenance.py`, registered durable Ogimet/audit scripts, removed the stale `auto_pause_live.sh` manifest surface after the file had already been deleted, ignored generated city-audit/state-log outputs, and deleted obvious `_debug_*`/one-off fix scripts from the workspace.
Verification: `python scripts/topology_doctor.py --source --summary-only`; `python scripts/topology_doctor.py --tests --summary-only`; `python scripts/topology_doctor.py --scripts --summary-only`; `python scripts/topology_doctor.py --strict --summary-only`; `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ... --summary-only`; `python scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-14_topology_context_efficiency/plan.md --summary-only`; `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md --summary-only`; `python -m py_compile scripts/audit_polymarket_city_settlement.py`; `pytest -q tests/test_topology_doctor.py -k 'source_rationale or tests_mode or scripts_mode or strict_passes or map_maintenance'`; `git diff --cached --check`.
Next: Phase 7 final map-maintenance split can proceed from scoped staged state, while the remaining unstaged data-rebuild/refit work stays out of this cleanup commit.

## 2026-04-15 — Map-Maintenance Checker Split

Date: 2026-04-15
Branch: data-improve
Task: Complete Phase 7 by extracting the final map-maintenance checker family behind existing wrappers.
Changed files: `scripts/topology_doctor.py`, `scripts/topology_doctor_map_maintenance.py`, `architecture/script_manifest.yaml`, `docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md`
Summary: Moved git-status parsing, explicit changed-file kind inference, and map-maintenance rule evaluation into `topology_doctor_map_maintenance.py`; `topology_doctor.py` keeps compatibility wrappers and remains the only runnable CLI surface.
Verification: `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_map_maintenance.py scripts/topology_doctor_cli.py`; `pytest -q tests/test_topology_doctor.py -k 'map_maintenance or git_status_parser'`; `pytest -q tests/test_topology_doctor.py`; `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --summary-only`; `python scripts/topology_doctor.py --strict --summary-only`; `python scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-14_topology_context_efficiency/plan.md --summary-only`; `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md --summary-only`; `git diff --check -- <changed files>`; Spark read-only review PASS.
Next: Phase 7 topology-doctor modularization is complete; remaining unstaged data-rebuild/refit work is outside this package.
