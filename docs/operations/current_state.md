# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `post-audit-remediation-mainline`
- Mainline task: **Post-audit P1.4 legacy settlement evidence policy implementation closeout — active 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers at `7a3524e`. P1.4 planning was pushed at `da1662f`. P1.4
  implementation now adds read-only legacy `settlements` evidence-only
  readiness blockers and focused regression tests; commit/push closeout is in
  progress on this branch.

## Required evidence

- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`

## Freeze point

- Current freeze: P1.4 implementation may change only
  `scripts/verify_truth_surfaces.py`, `tests/test_truth_surface_health.py`,
  `docs/operations/AGENTS.md`, this file, and the active P1.4 work log/receipt.
  No schema, production DB, runtime state, current-fact surface, calibration,
  replay, live consumer, `settlements_v2` population, or market-identity
  backfill changes are authorized by this packet.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Complete post-fix critic/verifier closeout, commit, and push the P1.4
  implementation packet on `post-audit-remediation-mainline`.
- Preserve unrelated dirty work and concurrent in-flight edits.
