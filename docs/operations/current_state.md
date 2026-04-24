# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: **Post-audit P1.3 unsafe observation quarantine planning — active 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 is a planning-only packet; implementation must not start
  until this plan is reviewed, pushed, and post-close critic/verifier pass.

## Required evidence

- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`

## Freeze point

- Current freeze: P1.3 planning may change only the active packet files,
  operations routing, required docs/topology registry companions, and P1.2
  closeout bookkeeping listed in the P1.3 receipt. No source, schema, DB,
  current-fact, calibration, replay, or live consumer changes are authorized by
  this planning packet.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Architect, critic, and verifier reviews completed with required fixes
  applied. Commit and push the planning packet only. Future P1.3
  implementation starts only after post-close review.
- Preserve unrelated dirty work and concurrent in-flight edits.
