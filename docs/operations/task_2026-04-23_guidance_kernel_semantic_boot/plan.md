# Guidance Kernel Semantic Boot Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: governance/tooling
Phase: -1 packet activation and current-state alignment

## Objective

Activate the guidance-kernel semantic boot work as a durable operations packet
before implementing the phase plan from the approved ralplan.

## Source Plan

- `.omx/plans/guidance-kernel-semantic-boot-ralplan-2026-04-23.md`
- `.omx/context/guidance-kernel-semantic-boot-20260423T005005Z.md`

## Phase Scope

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
