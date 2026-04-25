# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `midstream_remediation`
- Mainline task: **Post-audit remediation mainline — P0 4.2.A readiness guard normalization active**
- Active package source: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Active execution packet: `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`
- Prior closeout evidence packet: `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers at `7a3524e`. P1.4 planning was pushed at `da1662f`; P1.4
  implementation was pushed at `df9ece5`, adding read-only legacy
  `settlements` evidence-only readiness blockers and focused regression tests;
  P1.4 control surfaces were closed at `50cd713`. P1.5 planning was pushed
  at `07c86d8`. P1.5a implemented script-side read-only calibration-pair
  rebuild and Platt-refit preflight modes plus live-write CLI guards; critic
  and verifier review passed after excluding forbidden runtime artifacts from
  the submit diff. Implementation commit `99c4ac3` was pushed to
  `origin/midstream_remediation`. Post-close review found no code regression
  and required control-surface alignment before freezing the next packet.
  Commit `c274230` removed the stale 4.1.A-C next-packet pointer. The existing
  P0 data-audit containment packet is now active only for a small 4.2.A
  readiness-guard normalization follow-up.

## Required evidence

- `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`

## Freeze point

- Current freeze: P1.5a and POST_AUDIT_HANDOFF Immediate 4.1.A-C are closed.
  Current branch facts show Immediate 4.1.A-C already landed and closed in
  `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
  (`P-1 Pre-P0 Post-Audit Cleanup -- 2026-04-24`), so it must not be
  re-opened as the next execution packet without new evidence. The only active
  code scope is POST_AUDIT_HANDOFF 4.2.A readiness-query / fail-closed guard
  normalization in `scripts/verify_truth_surfaces.py` and
  `tests/test_truth_surface_health.py`, plus the P0 packet docs and live
  operations pointer. No new script, production DB mutation,
  `settlements_v2` population, market-identity backfill, replay/live/runtime
  consumer rewiring, or legacy-settlement promotion is authorized by this
  pointer.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Complete review/closeout for the active 4.2.A follow-up: address
  critic/verifier findings, then commit and push only the scoped files.
- Preserve unrelated dirty work and concurrent in-flight edits.
