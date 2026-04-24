# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: **Post-audit P1.2 writer provenance gates ralplan — active 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`
- Status: planning-only; P1.1 is closed and post-close reviewed.

## Required evidence

- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`

## Freeze point

- Current freeze: P1.2 is planning-only until architect/critic/verifier review
  closes, the plan packet is committed and pushed, and post-close review
  passes. Runtime/source implementation starts only after that freeze.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Finish P1.2 ralplan: complete critic/verifier review, apply fixes if any,
  then commit and push scoped planning files only.
- After push, run the required third-party critic/verifier pass before
  treating P1.2 as frozen for implementation.
- Preserve unrelated dirty work and concurrent in-flight edits.
