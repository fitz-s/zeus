# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `midstream_remediation`
- Mainline task: **Post-audit remediation mainline — P1.5a closed; next packet pending phase-entry**
- Active package source: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Active execution packet: none frozen; the next packet must be opened by
  fresh phase-entry planning before any code edit.
- Closeout evidence packet: `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`
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
  and required this control-surface alignment before freezing the next packet.

## Required evidence

- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/work_log.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`

## Freeze point

- Current freeze: P1.5a is closed. No code edit is authorized from the closed
  packet. Current branch facts show POST_AUDIT_HANDOFF Immediate 4.1.A-C
  already landed and closed in
  `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
  (`P-1 Pre-P0 Post-Audit Cleanup -- 2026-04-24`), so it must not be
  re-opened as the next execution packet without new evidence. The next packet
  must be selected from unresolved POST_AUDIT_HANDOFF 4.2+ work by fresh
  phase-entry planning with root/scoped `AGENTS.md` rereads, topology
  navigation, important-file exploration, and reviewer challenge before
  implementation. No production DB mutation, `settlements_v2` population,
  market-identity backfill, replay/live/runtime consumer rewiring, or
  legacy-settlement promotion is authorized by this pointer.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Open and freeze the next unresolved small packet from
  `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
  4.2+ after fresh phase-entry planning. Likely starting point: Forensic P0
  readiness/containment, but no execution packet is frozen yet.
- Preserve unrelated dirty work and concurrent in-flight edits.
