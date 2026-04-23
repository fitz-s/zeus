# Guidance Kernel Semantic Boot Work Log

Date: 2026-04-23
Branch: `data-improve`
Task: Phase -1 packet activation and current-state alignment.

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/AGENTS.md`
- `docs/operations/runtime_artifact_inventory.md`
- `architecture/topology.yaml`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json`

Summary:

- activated the guidance-kernel semantic boot work as a durable operations
  packet
- pointed `current_state.md` at the new packet
- aligned the topology active operations anchor with the new packet
- indexed guidance-kernel `.omx` plan/context artifacts in
  `runtime_artifact_inventory.md`

Verification:

- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <Phase -1 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <Phase -1 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <Phase -1 files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <Phase -1 files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <Phase -1 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `git diff --check -- <Phase -1 files>` -> ok
- `git diff -- docs/authority` -> empty

Pre-close review:

- Critic: pass. Phase -1 stayed limited to packet activation/current-state
  routing. The only architecture change is the existing topology active
  operations anchor needed by `topology_doctor --docs`.
- Verifier: pass. Current-state active source now points at tracked packet
  evidence, while `.omx` artifacts remain evidence only via current-state
  required evidence and runtime artifact inventory.

Next:

- commit Phase -1, then run post-close review before opening Phase 0

## Phase 0 Semantic Boot Kernel Skeleton

Changed files:

- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`
- `architecture/self_check/zero_context_entry.md`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_core_map.py`
- `scripts/topology_doctor_policy_checks.py`
- `scripts/topology_doctor_registry_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json`

Summary:

- added `task_boot_profiles.yaml` for question-first task classes:
  source routing, settlement, hourly ingest, Day0, calibration, docs
  authority, and graph review
- added `fatal_misreads.yaml` for the high-risk semantic shortcuts that must be
  disproven before code/graph context is trusted
- added topology_doctor validation modes for the new manifests
- routed root `AGENTS.md`, `workspace_map.md`, and zero-context overlay through
  semantic boot before code or graph review

Verification:

- `python scripts/topology_doctor.py --task-boot-profiles --json` -> ok
- `python scripts/topology_doctor.py --fatal-misreads --json` -> ok
- `python scripts/topology_doctor.py --self-check-coherence --json` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <Phase 0 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <Phase 0 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <Phase 0 files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <Phase 0 files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <Phase 0 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python -m pytest -q tests/test_topology_doctor.py -k 'task_boot_profiles or fatal_misreads or compiled_topology or self_check_coherence or context_budget'` -> 15 passed
- `git diff --check -- <Phase 0 files>` -> ok
- `python scripts/topology_doctor.py --strict --json` -> blocked by pre-existing
  unregistered tracked scripts/tests and unrelated untracked state artifacts,
  not by Phase 0 semantic boot changes

Pre-close review:

- Critic: pass. Phase 0 installs boot manifests and validators without
  implementing city truth contracts or semantic-bootstrap output.
- Verifier: pass. Root and zero-context routing now force task-class semantic
  boot before graph/code confidence, and graph remains `derived_not_authority`.

Next:

- commit Phase 0, then run post-close review before Phase 1

## Phase 1 City Truth Contract Schema

Changed files:

- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/city_truth_contract.yaml`
- `architecture/core_claims.yaml`
- `architecture/task_boot_profiles.yaml`
- `architecture/fatal_misreads.yaml`
- `architecture/self_check/zero_context_entry.md`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_core_map.py`
- `scripts/topology_doctor_policy_checks.py`
- `scripts/topology_doctor_registry_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json`

Summary:

- added `city_truth_contract.yaml` as a stable source-role schema and evidence
  taxonomy, explicitly not a current city/source truth table
- linked city truth schema into semantic boot profiles and fatal misread proof
  surfaces
- added core claims for source-role separation, Hong Kong caution, hourly
  extrema preservation, and graph-derived-only status
- added topology_doctor validation for the city truth contract and unbacked
  architecture-side current assertions

Verification:

- `python scripts/topology_doctor.py --city-truth-contract --json` -> ok
- `python scripts/topology_doctor.py --core-claims --json` -> ok
- `python scripts/topology_doctor.py --task-boot-profiles --json` -> ok
- `python scripts/topology_doctor.py --fatal-misreads --json` -> ok
- `python scripts/topology_doctor.py --self-check-coherence --json` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <Phase 1 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <Phase 1 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <Phase 1 files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <Phase 1 files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <Phase 1 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python -m pytest -q tests/test_topology_doctor.py -k 'city_truth_contract or task_boot_profiles or fatal_misreads or core_claims'` -> 14 passed
- `git diff --check -- <Phase 1 files>` -> ok

Pre-close review:

- Critic: pass. `city_truth_contract.yaml` defines schema, evidence classes,
  caution flags, and examples only; it does not encode current per-city truth.
- Verifier: pass. Core claims are proof-targeted, topology_doctor rejects
  unbacked architecture-side current assertions, and current truth ownership
  remains with operations current-fact surfaces.

Next:

- commit Phase 1, then run post-close review before Phase 2
