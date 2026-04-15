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
