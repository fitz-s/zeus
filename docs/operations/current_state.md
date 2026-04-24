# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `post-audit-remediation-mainline`
- Mainline task: **Post-audit remediation mainline — P1.4 closed, P1.5 planning next**
- Active package source: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md` (closed receipt-bound reference; no further implementation authority)
- Active execution packet: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md` (closed receipt-bound reference; no further implementation authority)
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers at `7a3524e`. P1.4 planning was pushed at `da1662f`; P1.4
  implementation was pushed at `df9ece5`, adding read-only legacy
  `settlements` evidence-only readiness blockers and focused regression tests.
  No active implementation packet is open until P1.5 is planned.

## Required evidence

- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`

## Freeze point

- Current freeze: no further implementation is authorized until a P1.5 packet
  is created, reviewed, and made active. The next packet is expected to plan
  eligibility views/adapters and training-preflight cutover without mutating
  production DBs or widening into P3 consumer rewiring unless explicitly
  authorized.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Open a new P1.5 planning packet for eligibility views/adapters plus
  calibration/training-preflight cutover. Before any implementation, reread
  `AGENTS.md`, run topology, and lock the exact view/adapter contract.
- Preserve unrelated dirty work and concurrent in-flight edits.
