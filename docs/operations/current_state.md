# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `data-improve`
- Mainline task: **Post-audit P1.2 writer provenance gates implementation — active 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`
- Status: implementation active inside plan freeze `e498b0d`; P1.1 is closed
  and P1.2 planning received post-close critic/verifier PASS.

## Required evidence

- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`

## Freeze point

- Current freeze: P1.2 implementation may change only the writer-local code,
  scoped writer tests, and active-packet closeout bookkeeping listed in the
  receipt. Schema, DB rows, source registry, caller constructors/scripts,
  calibration/replay consumers, authority docs, and architecture remain frozen.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Commit and push scoped P1.2 implementation files only.
- After push, run the required third-party post-close critic/verifier before
  closing P1.2 or freezing the next packet.
- Preserve unrelated dirty work and concurrent in-flight edits.
