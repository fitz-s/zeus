# Authority Kernel Gamechanger Plan

Date: 2026-04-23
Branch: `data-improve`
Classification: governance/authority
Phase: P1 core authority rewrite

## Objective

Rebuild Zeus's authority kernel around a smaller durable authority surface,
stronger runtime semantic center, evidence-only demotion for historical
governance files, and receipt/expiry-bound current-fact surfaces.

## Source Package

- `/Users/leofitz/Downloads/zeus_authority_kernel_gamechanger_package_2026-04-23`
- `.omx/context/kernel-gamechanger-20260423T035846Z.md`
- `.omx/plans/kernel-gamechanger-ralplan-2026-04-23.md`

## Phase Order

- P0: authority decontamination and packet activation
- P1: core authority rewrite
- P2: side authority demotion/merge and active reference retarget
- P3: current-fact hardening and package closeout

## P0 Scope

Allowed:

- `docs/operations/current_state.md`
- `docs/operations/AGENTS.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/operations/task_2026-04-23_authority_kernel_gamechanger/**`
- `docs/authority/AGENTS.md`
- `docs/AGENTS.md`
- `docs/README.md`
- `workspace_map.md`
- `architecture/docs_registry.yaml`
- `architecture/topology.yaml`
- `docs/reports/AGENTS.md`
- `docs/reports/authority_history/**`
- `docs/runbooks/task_2026-04-15_data_math_operator_runbook.md`
- moved `docs/authority/task_2026-04-15_*`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- runtime/data/source implementation
- current city/source re-audit
- side authority demotion beyond the three `task_2026-04-15_*` files in P0

## P1 Scope

Allowed:

- `docs/authority/zeus_current_architecture.md`
- `docs/authority/zeus_current_delivery.md`
- `docs/authority/zeus_change_control_constitution.md`
- `architecture/docs_registry.yaml` side-authority next_action markers only
- `docs/operations/current_state.md`
- this packet's `plan.md`, `work_log.md`, `receipt.json`

Forbidden:

- `src/**`
- `state/**`
- `.code-review-graph/graph.db`
- runtime/data/source implementation
- current city/source re-audit
- side authority file moves

## P1 Acceptance

- `zeus_current_architecture.md` is the runtime semantic kernel.
- `zeus_current_delivery.md` is the single delivery/change-control entrypoint.
- `zeus_change_control_constitution.md` is clearly deep, non-default
  governance.
- Core authority includes load-bearing dual-track, boundary, packet, autonomy,
  demotion/promotion, and current-fact rules needed before P2 demotion.
- `docs_registry.yaml` marks side authority files as sunset-pending via
  `next_action: demote_after_extraction` without moving them in P1.

## P0 Acceptance

- `current_state.md` points at this packet and receipt.
- `.omx` context/plan artifacts are inventoried as evidence only.
- `docs/authority/` contains no `task_2026-04-15_*` files.
- Moved task docs are visible under `docs/reports/authority_history/` and
  classified as evidence/history, not authority.
- Non-archive active references to moved task docs are retargeted or explicitly
  historical.
- No unrelated dirty work is staged or modified.

## Verification

- `python scripts/topology_doctor.py --docs --json`
- `python scripts/topology_doctor.py --current-state-receipt-bound --json`
- `python scripts/topology_doctor.py --context-budget --json`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <P0 files> --json`
- `python scripts/topology_doctor.py --planning-lock --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-23_authority_kernel_gamechanger/plan.md --json`
- `python scripts/topology_doctor.py --work-record --changed-files <P0 files> --work-record-path docs/operations/task_2026-04-23_authority_kernel_gamechanger/work_log.md --json`
- `python scripts/topology_doctor.py --change-receipts --changed-files <P0 files> --receipt-path docs/operations/task_2026-04-23_authority_kernel_gamechanger/receipt.json --json`
- `python scripts/topology_doctor.py closeout --changed-files <P0 files> --plan-evidence docs/operations/task_2026-04-23_authority_kernel_gamechanger/plan.md --work-record-path docs/operations/task_2026-04-23_authority_kernel_gamechanger/work_log.md --receipt-path docs/operations/task_2026-04-23_authority_kernel_gamechanger/receipt.json --json`
- `find docs/authority -maxdepth 1 -name 'task_2026-04-15*' -print`
- `git diff --check -- <P0 files>`
