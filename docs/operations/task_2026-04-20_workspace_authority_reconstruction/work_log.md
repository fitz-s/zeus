# Workspace Authority Reconstruction Work Log

Date: 2026-04-20
Branch: data-improve
Task: Register the 2026-04-20 V2 reconstruction package as the live mainline
task and execute P0 boot-surface realignment.

Changed files:

- `AGENTS.md`
- `workspace_map.md`
- `docs/README.md`
- `docs/AGENTS.md`
- `docs/archive_registry.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/runtime_artifact_inventory.md`
- `architecture/topology.yaml`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/**`

Summary:

- registered the reconstruction package as the active control source
- tracked the reconstruction package body as a local adaptation so online
  reviewers can inspect the package referenced by the boot surface
- created the active execution packet for P0
- rewrote the tracked boot surfaces around authority, derived context, and
  historical cold storage
- introduced `docs/archive_registry.md` as the visible archive interface
- slimmed `docs/operations/current_state.md` into a live control pointer
- updated topology to recognize the new archive interface and the thinner
  current-state contract
- indexed new `.omx/plans/**` ralplan artifacts in `runtime_artifact_inventory.md`

Verification:

- `python scripts/topology_doctor.py --planning-lock --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --plan-evidence docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --work-record-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --receipt-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --json` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml --json` -> ok
- `git diff --check -- AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json` -> clean
- `git diff --cached --check` -> clean
- P0 follow-up review result: `proceed_to_p1` with follow-up that P1 must
  machine-protect the P0 claims before package-defined P2.

Next:

- hold at the staged P0 packet until explicit commit/next-packet direction
- keep unrelated dirty work unstaged

## P1 update

Date: 2026-04-21
Branch: data-improve
Task: P1 machine visibility and registry alignment.

Changed files:

- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `architecture/map_maintenance.yaml`
- `architecture/context_budget.yaml`
- `architecture/artifact_lifecycle.yaml`
- `docs/AGENTS.md`
- `docs/operations/current_state.md`
- `scripts/topology_doctor_registry_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json`

Summary:

- marked `docs/archives` as historical cold storage with a visible interface
  at `docs/archive_registry.md`
- added archive-interface checks that reject archive-as-live-peer language and
  missing/unregistered archive registry surfaces
- added context budgets for archive registry and current state
- classified archive registry as a liminal archive interface in artifact
  lifecycle
- added targeted tests for archive-interface policy
- updated current state from P0 to P1

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --artifact-lifecycle --json` -> ok
- `pytest -q tests/test_topology_doctor.py -k "docs or registry or current_state or map_maintenance"` -> 35 passed, 139 deselected
- `git diff --check -- architecture/topology.yaml architecture/topology_schema.yaml architecture/map_maintenance.yaml architecture/context_budget.yaml architecture/artifact_lifecycle.yaml scripts/topology_doctor_registry_checks.py tests/test_topology_doctor.py` -> clean
- `python -m py_compile scripts/topology_doctor_registry_checks.py` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <P1 files> --plan-evidence docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md --work-record-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md --receipt-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --json` -> ok

Known verification gap:

- `python scripts/topology_doctor.py --strict --json` remains blocked by
  pre-existing script/test registry debt and the local root
  `zeus_data_inventory.xlsx`; these are outside the P1 allowlist.
- P1 closeout reports Code Review Graph stale/partial coverage warnings for
  changed checker/test files. These are warning-grade and are the subject of
  later P2A graph portability/status work.

Next:

- run P1 planning-lock/work-record/receipt/map-maintenance/closeout with the
  final changed-file list
- do not start P2 until P1 closeout is verified

## P2A update

Date: 2026-04-21
Branch: data-improve
Task: Graph portability/status disclosure, sidecar deferred.

Changed files:

- `scripts/code_review_graph_mcp_readonly.py`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_code_review_graph.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json`

Summary:

- removed hardcoded workstation repo root from the Zeus CRG MCP facade
- preserved upstream auto-detection and `CRG_REPO_ROOT` override behavior
- added graph status details for storage `path_mode`, counts, metadata, and
  sidecar absence/presence
- threaded the same graph disclosure into code-impact metadata
- added targeted tests for wrapper repo-root resolution and absent sidecar
  status
- deferred `graph_meta.json` creation and sidecar lifecycle classification to
  P2B

Verification:

- `python scripts/topology_doctor.py --code-review-graph-status --json` -> ok;
  reports `path_mode=absolute` and `graph_meta.present=false`
- `python scripts/topology_doctor.py --context-packs --json` -> ok
- `python -m py_compile scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py scripts/topology_doctor_registry_checks.py` -> ok
- `pytest -q tests/test_topology_doctor.py -k "code_review_graph or context_pack"` -> 13 passed, 163 deselected, 1 warning
- `git diff --check -- scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py tests/test_topology_doctor.py` -> clean
- `python scripts/topology_doctor.py closeout --changed-files <P2A files> --plan-evidence docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md --work-record-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md --receipt-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --json` -> ok

Known verification note:

- `.code-review-graph/graph.db` is dirty in the local worktree and remains
  unstaged. P2A does not hand-edit, regenerate, or commit graph DB artifacts.
- P2A closeout reports graph dirty-file-stale warnings for the changed P2A
  files. These warnings are expected until graph DB refresh policy is handled;
  they do not block the P2A portability/status change.

P2B gate:

- P2A committed as `d45ec40`.
- `graph_meta.json` was not created.
- Sidecar lifecycle/classification work is deferred until provenance and DB
  parity can be proved and sidecar tracking is explicitly approved.
