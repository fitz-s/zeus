# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `post-audit-remediation-mainline`
- Mainline task: **Post-audit P1.4 legacy settlement evidence policy planning — active 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 has implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers at `7a3524e`. P1.4 is a planning-only packet for legacy settlement
  evidence-only / finalization policy; implementation must not start until
  this plan is reviewed, pushed, and post-close reviewed.

## Required evidence

- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`

## Freeze point

- Current freeze: P1.4 planning may change only the active packet files,
  operations routing, and `current_state.md` as listed in the P1.4 receipt. No
  source, schema, production DB, runtime, current-fact, calibration, replay,
  live consumer, `settlements_v2` population, or market-identity backfill
  changes are authorized by this planning packet.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Architect/scout review completed with a planning-only recommendation. Run
  critic/verifier closeout, commit, and push the P1.4 planning packet only.
  Future P1.4 implementation requires a fresh phase entry before editing.
- Preserve unrelated dirty work and concurrent in-flight edits.
