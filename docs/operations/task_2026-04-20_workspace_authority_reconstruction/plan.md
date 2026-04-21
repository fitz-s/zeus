# Workspace Authority Reconstruction Plan

Date: 2026-04-20
Branch: data-improve
Classification: governance

## Objective

Apply the 2026-04-20 V2 workspace authority reconstruction package as the
current mainline task.

P0 is the first active lane: realign the online-visible boot surfaces to repo
reality without changing runtime behavior, source code, tests, scripts, graph
artifacts, runtime DBs, or archive bodies.

## Route source

- Attached package:
  `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/README.md`
- Execution order:
  `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/16_apply_order.md`

## Scope

Allowed files for P0:

- `AGENTS.md`
- `workspace_map.md`
- `docs/README.md`
- `docs/AGENTS.md`
- `docs/archive_registry.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `architecture/topology.yaml`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md`
- `docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json`

LOCAL_ADAPTATION registration files:

- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/**`
- `docs/operations/runtime_artifact_inventory.md`

Forbidden files:

- `src/**`
- `tests/**`
- `scripts/**`
- `docs/authority/**`
- `docs/archives/**`
- `state/**`
- `raw/**`
- `.omx/**`
- `.omc/**`
- `.code-review-graph/graph.db`
- `architecture/**` except `architecture/topology.yaml`

The local package directory excludes `.DS_Store` and other platform junk.

## Decision

Treat the reconstruction package as the active control source for this lane and
register it explicitly in the live operations surface.

The active packet will start with P0 only. P1/P2/P3 remain downstream phases
and are not implicitly authorized by reading the package.

## P1 decision

P0 was committed as `19e0178` after local follow-up review and closeout
verification. RALPLAN consensus then confirmed the next defensible sequence:

1. finish P0 closeout/review
2. land P1 machine visibility and registry alignment
3. split future P2 into P2A portability/status and optional P2B sidecar work

P1 therefore implements only the machine-protection layer for P0:

- archive cold-storage semantics in `architecture/topology.yaml`
- archive-interface checks in topology_doctor
- context budgets for `docs/archive_registry.md` and `current_state.md`
- artifact lifecycle classification for `docs/archive_registry.md`
- targeted regression tests

P1 does not touch graph wrapper code, graph DB artifacts, runtime state, source
runtime behavior, archive bodies, or package-defined P2 work.

## P2A decision

After P1, RALPLAN consensus split package-defined P2 into:

- P2A: graph wrapper portability and status disclosure
- P2B: optional `graph_meta.json` sidecar and lifecycle/classification work

P2A is authorized now. P2B remains gated on real provenance/parity evidence.

P2A implements only:

- removal of workstation-specific `DEFAULT_REPO_ROOT` from
  `scripts/code_review_graph_mcp_readonly.py`
- status JSON disclosure for graph storage `path_mode`
- explicit sidecar absence/presence disclosure
- targeted tests for wrapper repo-root resolution and graph status details

P2A explicitly does not create `.code-review-graph/graph_meta.json`, regenerate
or stage `.code-review-graph/graph.db`, add sidecar lifecycle classification, or
promote graph output into authority.

## Required reads

- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/00_executive_ruling.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/01_mental_model.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/02_authority_order_rewrite.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/07_execution_packets.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/09_validation_matrix.md`
- `docs/operations/zeus_workspace_authority_reconstruction_package_2026-04-20_v2/16_apply_order.md`

## Verification gates

- `python scripts/topology_doctor.py --planning-lock --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --plan-evidence docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md --json`
- `python scripts/topology_doctor.py --work-record --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --work-record-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md --json`
- `python scripts/topology_doctor.py --change-receipts --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --receipt-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --json`
- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --context-budget --json`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml --json`

## Notes

- LOCAL_ADAPTATION: the local worktree contains `docs/archives.zip` and
  `docs/operations/task_2026-04-19_execution_state_truth_upgrade/` as visible
  untracked inputs. P0 must register or explicitly route around them without
  deleting or overwriting them.
- LOCAL_ADAPTATION: the reconstruction package itself is tracked as package
  input because the user explicitly designated it as the current mainline task
  and online review needs the referenced package body. Runtime-local ralplan
  plan files created during P2 sequencing exploration are indexed in
  `docs/operations/runtime_artifact_inventory.md` so docs checks stay honest.
