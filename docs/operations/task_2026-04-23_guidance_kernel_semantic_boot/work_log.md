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

## Phase 2 Semantic-Bootstrap Topology Output

Changed files:

- `architecture/context_pack_profiles.yaml`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_context_pack.py`
- `tests/test_topology_doctor.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json`

Summary:

- added `topology_doctor.py semantic-bootstrap --task-class ...` output for
  required reads, proof questions, fatal misreads, current fact freshness,
  semantic core claims, graph usage, forbidden shortcuts, and verification gates
- added context-pack `semantic_bootstrap` injection for inferred or explicit
  task classes
- added warning paths for missing/stale current fact surfaces and unavailable
  or mismatched graph context
- recorded `semantic_bootstrap` in context-pack profile output contracts

Verification:

- `python scripts/topology_doctor.py semantic-bootstrap --task-class source_routing --task "audit Hong Kong source routing" --files src/data/tier_resolver.py --json` -> ok, with derived graph metadata mismatch warning
- `python scripts/topology_doctor.py context-pack --pack-type debug --task "debug settlement rounding mismatch" --files src/contracts/settlement_semantics.py --json` -> ok, includes `semantic_bootstrap.task_class=settlement_semantics`
- `python scripts/topology_doctor.py --context-packs --json` -> ok
- `python scripts/topology_doctor.py --task-boot-profiles --json` -> ok
- `python scripts/topology_doctor.py --fatal-misreads --json` -> ok
- `python scripts/topology_doctor.py --city-truth-contract --json` -> ok
- `python scripts/topology_doctor.py --core-claims --json` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <Phase 2 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <Phase 2 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <Phase 2 files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <Phase 2 files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <Phase 2 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python -m pytest -q tests/test_topology_doctor.py -k 'semantic_bootstrap or context_pack or task_boot_profiles or fatal_misreads or city_truth_contract or core_claims'` -> 30 passed
- `git diff --check -- <Phase 2 files>` -> ok

Pre-close review:

- Critic: pass. Semantic bootstrap is generated context, not authority, and
  graph remains Stage 2 derived context after semantic proof questions.
- Verifier: pass. Output covers required reads, current facts, proof questions,
  fatal misreads, semantic claims, graph use, forbidden shortcuts, verification
  gates, and capability-present/absent test cases.

Next:

- commit Phase 2, then run post-close review before Phase 3

## Phase 3 Receipt-Bound Current State

Changed files:

- `docs/operations/current_state.md`
- `architecture/docs_registry.yaml`
- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_docs_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json`

Summary:

- added `Receipt-bound source` to `current_state.md` and topology required
  labels
- changed docs registry freshness for `current_state.md` to packet-bound
- added topology_doctor receipt-bound checks for active packet/receipt mismatch,
  missing receipt, runtime-local active source, and receipt coverage
- added `current-state --from-receipt` generated candidate output

Verification:

- `python scripts/topology_doctor.py --current-state-receipt-bound --json` -> ok
- `python scripts/topology_doctor.py current-state --from-receipt docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <Phase 3 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <Phase 3 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <Phase 3 files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <Phase 3 files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <Phase 3 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python -m pytest -q tests/test_topology_doctor.py -k 'current_state_receipt or current_state_candidate or docs_mode_rejects_current_state_missing_required_anchor or docs_registry'` -> 12 passed
- `git diff --check -- <Phase 3 files>` -> ok

Pre-close review:

- Critic: pass. Phase 3 adds receipt-bound validation and candidate generation
  without changing runtime behavior or semantic boot/source truth contracts.
- Verifier: pass. `current_state.md` active packet, receipt source, and receipt
  packet field now have a machine-checkable consistency path.

Next:

- commit Phase 3, then run post-close review before Phase 4

## Phase 4 Graph Protocol Hardening

Changed files:

- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/code_review_graph_protocol.yaml`
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

- added `code_review_graph_protocol.yaml` as a two-stage graph use contract
- rooted graph usage in semantic boot first, graph context second
- added topology_doctor validation that graph stays derived-only and root
  AGENTS carries Stage 1/Stage 2 wording
- registered the graph protocol in root/architecture/workspace routing

Verification:

- `python scripts/topology_doctor.py --code-review-graph-protocol --json` -> ok
- `python scripts/topology_doctor.py semantic-bootstrap --task-class graph_review --task "graph review" --files scripts/topology_doctor.py --json` -> ok
- `python scripts/topology_doctor.py --current-state-receipt-bound --json` -> ok
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <Phase 4 files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <Phase 4 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <Phase 4 files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <Phase 4 files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <Phase 4 files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python -m pytest -q tests/test_topology_doctor.py -k 'code_review_graph_protocol or semantic_bootstrap or graph_review'` -> 11 passed
- `git diff --check -- <Phase 4 files>` -> ok

Pre-close review:

- Critic: pass. Graph protocol is machine-readable and does not promote graph
  output above semantic boot, receipts, manifests, current facts, or tests.
- Verifier: pass. Root AGENTS, workspace map, architecture registry, protocol
  manifest, and topology_doctor all agree on Stage 1 semantic boot then Stage 2
  graph context.

Next:

- commit Phase 4, then run post-close review before package closeout

## Phase 5 Closeout

Changed files:

- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md`
- `docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json`

Summary:

- marked the guidance-kernel semantic boot package closed in `current_state.md`
- recorded Phase 4 commit evidence
- kept runtime/source/state/graph/authority docs outside closeout scope

Verification:

- `python scripts/topology_doctor.py --task-boot-profiles --json` -> ok
- `python scripts/topology_doctor.py --fatal-misreads --json` -> ok
- `python scripts/topology_doctor.py --city-truth-contract --json` -> ok
- `python scripts/topology_doctor.py --code-review-graph-protocol --json` -> ok
- `python scripts/topology_doctor.py --current-state-receipt-bound --json` -> ok
- `python scripts/topology_doctor.py semantic-bootstrap --task-class source_routing --task "audit Hong Kong source routing" --files src/data/tier_resolver.py --json` -> ok, with expected derived graph metadata mismatch warning
- `python scripts/topology_doctor.py context-pack --pack-type debug --task "debug settlement rounding mismatch" --files src/contracts/settlement_semantics.py --json` -> ok, includes `semantic_bootstrap.task_class=settlement_semantics`
- `python scripts/topology_doctor.py --docs --json` -> ok
- `python scripts/topology_doctor.py --context-budget --json` -> ok
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <closeout files> --json` -> ok
- `python scripts/topology_doctor.py --planning-lock --changed-files <closeout files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --json` -> ok
- `python scripts/topology_doctor.py --work-record --changed-files <closeout files> --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --json` -> ok
- `python scripts/topology_doctor.py --change-receipts --changed-files <closeout files> --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python scripts/topology_doctor.py closeout --changed-files <closeout files> --plan-evidence docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/plan.md --work-record-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/work_log.md --receipt-path docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json --json` -> ok
- `python -m pytest -q tests/test_topology_doctor.py -k "semantic_bootstrap or context_pack or task_boot_profiles or fatal_misreads or city_truth_contract or code_review_graph_protocol or current_state_receipt or current_state_candidate or core_claims"` -> 37 passed
- `git diff --check -- <closeout files>` -> ok
- `git diff -- docs/authority src architecture scripts tests AGENTS.md workspace_map.md` -> empty

Pre-close review:

- Critic: pass. Closeout only updates current-state and packet evidence; all
  implementation phases are already committed and verified.
- Verifier: pass. Core boot manifests, semantic-bootstrap output,
  city-truth schema, graph protocol, and receipt-bound current-state checks all
  pass their topology_doctor gates.

Post-close review:

- Critic: pass. Package objectives are complete: question-first semantic boot,
  fatal misread antibodies, city/source schema boundary, semantic-bootstrap
  output, receipt-bound current_state, and graph Stage 2 protocol.
- Verifier: pass. No closeout diff exists in `src/**`, `docs/authority/**`,
  `architecture/**`, `scripts/**`, `tests/**`, root `AGENTS.md`, or
  `workspace_map.md`; runtime state and graph DB remain unrelated dirty work.

Next:

- package closed
