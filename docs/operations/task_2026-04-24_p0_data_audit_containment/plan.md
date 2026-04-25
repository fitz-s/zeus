# P0 Data Audit Containment Packet

Date: 2026-04-24
Original branch: `data-improve`
Active follow-up branch: `midstream_remediation`
Status: reopened for POST_AUDIT_HANDOFF 4.2.A readiness guard normalization
follow-up; the original P0 Ralph slice below is historical packet context.

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

## 4.2.A Follow-up Plan

Status: active follow-up on `midstream_remediation`.

This follow-up reuses the existing P0 packet instead of creating
`scripts/zeus_readiness_check.py`. `scripts/verify_truth_surfaces.py` is already
the long-lived truth-surface readiness command, and P1.5a split phase-specific
`calibration-pair-rebuild-preflight` and `platt-refit-preflight` modes from the
full `training-readiness` verdict.

Plan:

1. Keep the active scope to `scripts/verify_truth_surfaces.py`,
   `tests/test_truth_surface_health.py`, this P0 packet, and the operations
   pointer/router docs.
2. In full `training-readiness`, reuse the existing per-metric
   `ensemble_snapshots_v2` rebuild-eligibility predicates so table presence
   cannot certify snapshots that are not training-allowed, verified,
   causality-safe, metric-scoped, and time-safe.
3. In full `training-readiness`, reuse the existing `calibration_pairs_v2`
   Platt-refit predicates so table presence cannot certify calibration pairs
   without per-metric mature decision-group buckets.
4. Add focused `TestTrainingReadinessP0` antibodies for snapshot rows that
   exist but are not per-metric eligible, and calibration pairs that exist but
   are below the Platt mature-bucket threshold.
5. Do not add a new script, touch `src/**`, mutate production DB/runtime state,
   populate canonical v2 truth, or rewire replay/live consumers.

## Acceptance

- Current local world DB readiness command exits non-zero and emits structured
  blockers.
- P0 targeted tests pass.
- Default truth-surface diagnostic remains callable through the default mode.
- Static linter catches bare legacy hourly table reads in targeted paths.
- 4.2.A follow-up targeted tests prove `training-readiness` fails closed on
  per-metric ineligible snapshots and immature Platt calibration buckets.
- No production DB, graph DB, runtime state, or source behavior is mutated.
