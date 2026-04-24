# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `p1-unsafe-observation-quarantine`
- Mainline task: **Post-audit P1.3 unsafe observation quarantine implementation — closeout 2026-04-24**
- Active package source: `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 has implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers. No production DB, schema, runtime, or calibration/training consumer
  mutation is part of this packet.

## Required evidence

- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`
- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`

## Freeze point

- Current freeze: P1.3 implementation may change only the active packet
  closeout files plus `scripts/verify_truth_surfaces.py` and
  `tests/test_truth_surface_health.py` as listed in the P1.3 receipt. No
  production DB, schema, runtime, source-ingest, calibration, replay, or live
  consumer changes are authorized by this packet.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Commit and push the P1.3 implementation branch. Future P1.4 or P1.5 work
  requires a fresh phase entry: reread `AGENTS.md`, rerun topology, and explore
  routed files before planning or editing.
- Preserve unrelated dirty work and concurrent in-flight edits.
