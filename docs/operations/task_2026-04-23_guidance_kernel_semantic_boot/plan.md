# Guidance Kernel Semantic Boot Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: governance/tooling
Phase: 4 graph protocol hardening

## Objective

Activate the guidance-kernel semantic boot work as a durable operations packet
before implementing the phase plan from the approved ralplan.

## Source Plan

- `.omx/plans/guidance-kernel-semantic-boot-ralplan-2026-04-23.md`
- `.omx/context/guidance-kernel-semantic-boot-20260423T005005Z.md`

## Phase -1 Scope

Allowed:

- `docs/operations/current_state.md`
- `docs/operations/AGENTS.md`
- `docs/operations/runtime_artifact_inventory.md`
- `architecture/topology.yaml` active operations anchor only
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**`
- `docs/archives/**`
- semantic boot manifests or topology-doctor implementation files

## Phase 0 Scope

Allowed:

- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`
- `architecture/topology_schema.yaml`
- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/self_check/zero_context_entry.md`
- existing `scripts/topology_doctor*.py` checker/CLI files needed to validate
  the manifests
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**`
- `docs/archives/**`
- context-pack semantic-bootstrap output implementation

## Phase 1 Scope

Allowed:

- `architecture/city_truth_contract.yaml`
- `architecture/core_claims.yaml`
- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`
- `architecture/topology_schema.yaml`
- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/self_check/zero_context_entry.md`
- existing `scripts/topology_doctor*.py` checker/CLI files needed to validate
  the schema and claims
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- current per-city/source truth table in `architecture/**`
- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**`
- `docs/archives/**`
- context-pack semantic-bootstrap output implementation

## Phase 2 Scope

Allowed:

- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_context_pack.py`
- existing topology_doctor checker files only if required
- `architecture/context_pack_profiles.yaml`
- `architecture/topology_schema.yaml`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**`
- `docs/archives/**`
- current per-city/source truth table changes

## Phase 3 Scope

Allowed:

- `docs/operations/current_state.md`
- `architecture/docs_registry.yaml`
- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_docs_checks.py`
- `tests/test_topology_doctor.py`
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**`
- `docs/archives/**`
- semantic boot profile/source truth changes

## Phase 4 Scope

Allowed:

- `architecture/code_review_graph_protocol.yaml`
- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_core_map.py`
- `scripts/topology_doctor_policy_checks.py`
- `scripts/topology_doctor_registry_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- `docs/authority/**`
- `docs/archives/**`
- current per-city/source truth table changes

## Future Phase Summary

- Phase 0: `task_boot_profiles.yaml` + `fatal_misreads.yaml`
- Phase 1: `city_truth_contract.yaml` schema + core semantic claims
- Phase 2: `topology_doctor semantic-bootstrap` and context-pack integration
- Phase 3: receipt-bound current state
- Phase 4: Code Review Graph protocol hardening
- Phase 5: closeout and post-closeout review

## Verification

- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --context-budget --json`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <phase files> --json`
- `python scripts/topology_doctor.py --planning-lock --changed-files <phase files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json`
- `python scripts/topology_doctor.py --work-record --changed-files <phase files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json`
- `python scripts/topology_doctor.py --change-receipts --changed-files <phase files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json`
- `python scripts/topology_doctor.py closeout --changed-files <phase files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json`
- `git diff --check`
