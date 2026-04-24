# P0 Data Audit Containment Packet

Date: 2026-04-24
Branch: `data-improve`
Status: implementation packet for the first post-audit P0 Ralph slice

## Task

Create fail-closed, read-only training-readiness evidence and static legacy
hourly containment after the forensic audit ruled the current data spine unsafe
for training/replay promotion.

## Route

- Mainline ralplan: `.omx/plans/post-p1-forensic-mainline-ralplan-2026-04-24.md`
- Ralph PRD: `.omx/plans/prd-p0-data-audit-containment.md`
- Ralph test spec: `.omx/plans/test-spec-p0-data-audit-containment.md`
- Context snapshot: `.omx/context/post-p1-forensic-mainline-20260424T025628Z.md`
- Forensic package input:
  `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/`

## Scope

Allowed files:

- `scripts/verify_truth_surfaces.py`
- `scripts/semantic_linter.py`
- `tests/test_truth_surface_health.py`
- `tests/test_semantic_linter.py`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`

Forbidden files:

- `state/**`
- `.code-review-graph/graph.db`
- `src/**`
- `docs/authority/**`
- `architecture/**`
- production DBs and generated runtime JSON

## Implementation Plan

1. Add a `training-readiness` mode to `scripts/verify_truth_surfaces.py`.
   It opens `state/zeus-world.db` read-only and reports `NOT_READY` with
   blockers for empty or unsafe training row families.
2. Add negative fixture coverage for empty v2 training tables, missing market
   identity, fallback source roles, missing forecast lineage times,
   reconstructed availability, and JSON contract shape.
3. Extend `scripts/semantic_linter.py` with a P0 unsafe-table rule for bare
   reads from legacy `hourly_observations` outside documented compatibility
   surfaces.
4. Add linter tests for unsafe `FROM`/`JOIN`, comment-only references, and
   evidence-view references.
5. Register this packet in `docs/operations/AGENTS.md` and validate receipt /
   work-record closeout against this packet, not the closed midstream packet.
6. Point `docs/operations/current_state.md` at this active P0 packet so
   topology routing does not rely on stale midstream packet state.
7. Inventory the `.omx` Ralph/mainline planning artifacts that this packet
   uses as local planning evidence.

## Acceptance

- Current local world DB readiness command exits non-zero and emits structured
  blockers.
- P0 targeted tests pass.
- Default truth-surface diagnostic remains callable through the default mode.
- Static linter catches bare legacy hourly table reads in targeted paths.
- No production DB, graph DB, runtime state, or source behavior is mutated.
