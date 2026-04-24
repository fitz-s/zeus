# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: **Post-audit P1.1 source-role registry implementation — active 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_source_role_registry/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_source_role_registry/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_source_role_registry/receipt.json`
- Status: implementation closeout in progress; source/test verification is
  passing and critic/verifier review is next.

## Required evidence

- `docs/operations/task_2026-04-24_p1_source_role_registry/plan.md`
- `docs/operations/task_2026-04-24_p1_source_role_registry/work_log.md`
- `docs/operations/task_2026-04-24_p1_source_role_registry/receipt.json`

## Freeze point

- Current freeze: P1.1 implementation may change only
  `src/data/tier_resolver.py`, `tests/test_tier_resolver.py`, and
  verification-closeout bookkeeping in the active packet. Writer, schema,
  DB, settlement, calibration, authority, and architecture changes remain
  blocked for later P1 slices.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Finish P1.1 implementation closeout: complete critic/verifier review,
  apply fixes if any, then commit and push scoped implementation files only.
- After push, run the required third-party critic/verifier pass before
  treating P1.1 as closed and freezing the next P1.2 ralplan.
- Preserve unrelated dirty work and concurrent in-flight edits.
