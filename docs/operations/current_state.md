# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `midstream_remediation`
- Mainline task: **Post-audit remediation mainline — P0 4.2.B legacy hourly evidence-view active**
- Active package source: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Active execution packet: `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md`
- Prior closeout evidence packet: `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json`
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
  Commit `c274230` removed the stale 4.1.A-C next-packet pointer. Commit
  `0b61261` closed and pushed the P0 4.2.A readiness-guard normalization
  follow-up, making full training-readiness inherit per-metric snapshot
  eligibility and Platt mature-bucket preflights. Commit `b8c6307` closed the
  4.2.A control pointer. The active slice is now 4.2.B evidence-only legacy
  hourly observations view plus canonical-path bare-table lint proof. Critic
  review found two closeout blockers: over-broad linter DDL exemption and a
  live health test reading the wrong DB authority surface. Both were closed
  inside the 4.2.B packet with narrow tests and gate reruns; final critic and
  verifier review passed.

## Required evidence

- `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md`
- `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/work_log.md`
- `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json`

## Freeze point

- Current freeze: P1.5a, POST_AUDIT_HANDOFF Immediate 4.1.A-C, and P0 4.2.A
  are closed. Current branch facts show Immediate 4.1.A-C already landed and
  closed in `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
  (`P-1 Pre-P0 Post-Audit Cleanup -- 2026-04-24`), and 4.2.A closed at
  `0b61261`, so neither must be re-opened as the next execution packet without
  new evidence. The active 4.2.B code scope is `src/state/db.py`
  schema-view DDL for `v_evidence_hourly_observations`,
  `tests/test_architecture_contracts.py` coverage, a narrow
  `scripts/semantic_linter.py` evidence-view DDL exemption with
  `tests/test_semantic_linter.py` antibodies, and the
  `tests/test_truth_surface_health.py` DB-authority connection correction
  needed to run the required state gate. This pointer does not authorize
  production DB mutation, `settlements_v2` population,
  market-identity backfill, replay/live/runtime consumer rewiring, or
  legacy-settlement promotion.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Commit and push only 4.2.B receipt-bound files.
- Preserve unrelated dirty work and concurrent in-flight edits.
