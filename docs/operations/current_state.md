# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `midstream_remediation`
- Mainline task: **Post-audit remediation mainline — P1 daily observation writer provenance closed; next phase reassessment**
- Active package source: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Active execution packet: none after this packet closeout
- Closeout evidence packet: `docs/operations/task_2026-04-25_p1_daily_observation_writer_provenance/plan.md`
- Prior closeout evidence packet: `docs/operations/task_2026-04-25_p0_market_events_preflight/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-25_p1_daily_observation_writer_provenance/receipt.json`
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
  `origin/midstream_remediation`. Commit `1411017` closed and pushed P0 4.2.C
  implementation after critic found and verified a wrong-label market-event
  false-pass fix. Commit `4abf8f0` closed the P0 replay preflight control
  pointer. Phase-entry reassessment selected a narrow P1 4.3.B-lite slice:
  harden WU/HKO daily observation backfill writers so new `VERIFIED` rows carry
  non-empty provenance identity. This packet added WU/HKO payload/source/parser
  provenance identity, SQLite writer-seam regressions, and test-trust metadata.
  Critic re-review passed after the first review required writer-seam coverage.
  This packet did not mutate production DB rows, create schemas/views, overhaul
  `INSERT OR REPLACE`, quarantine existing legacy observations, or jump to
  P2/P3/P4.

## Required evidence

- `docs/operations/task_2026-04-25_p1_daily_observation_writer_provenance/plan.md`
- `docs/operations/task_2026-04-25_p1_daily_observation_writer_provenance/work_log.md`
- `docs/operations/task_2026-04-25_p1_daily_observation_writer_provenance/receipt.json`

## Freeze point

- Current freeze: P1.5a, POST_AUDIT_HANDOFF Immediate 4.1.A-C, P0 4.2.A,
  P0 4.2.B, P0 4.2.C planning, and P0 4.2.C implementation
  are closed. Current branch facts show Immediate 4.1.A-C already landed and
  closed in `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
  (`P-1 Pre-P0 Post-Audit Cleanup -- 2026-04-24`), and 4.2.A closed at
  `0b61261`, while 4.2.B closed at `3e1bda7`, 4.2.C planning closed at
  `8e94f4a`, and 4.2.C implementation closed at `1411017`. The P1.5/P1.5a packet
  `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
  remains a historical topology anchor only; it is not the active slice.
  WU/HKO daily observation writer provenance identity is closed in this packet.
  The active implementation scope is now empty until the next packet is
  selected. This pointer does not authorize production DB mutation,
  `settlements_v2` population, market-identity backfill, live executor DB
  authority, legacy-settlement promotion, broad P1 source-role/view work, P2
  upsert/revision work, P3 safe-view migration, or P4 data population.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Rebuild phase-entry context before the next packet: reread `AGENTS.md`, run
  topology for the next candidate, and reassess the remaining post-audit
  P1/P2/P3 sequence against the current branch facts before any new code
  implementation.
- Preserve unrelated dirty work and concurrent in-flight edits.
