# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `midstream_remediation`
- Mainline task: **Post-audit remediation mainline — P1.5a implementation active**
- Active package source: `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- Active execution packet: `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers at `7a3524e`. P1.4 planning was pushed at `da1662f`; P1.4
  implementation was pushed at `df9ece5`, adding read-only legacy
  `settlements` evidence-only readiness blockers and focused regression tests;
  P1.4 control surfaces were closed at `50cd713`. P1.5 planning is now active
  at `07c86d8`. P1.5a implements script-side read-only calibration-pair
  rebuild and Platt-refit preflight modes plus live-write CLI guards; critic
  and verifier review passed after excluding forbidden runtime artifacts from
  the submit diff.

## Required evidence

- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/work_log.md`
- `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/receipt.json`

## Freeze point

- Current freeze: this P1.5a implementation slice may change only
  `scripts/verify_truth_surfaces.py`,
  `scripts/rebuild_calibration_pairs_v2.py`, `scripts/refit_platt_v2.py`,
  `tests/test_truth_surface_health.py`, active packet control surfaces, and
  the small topology hygiene files required to keep restored gates current:
  `architecture/naming_conventions.yaml`, `architecture/test_topology.yaml`,
  and `scripts/topology_doctor_test_checks.py`. No production DB mutation,
  `src/state/**` schema/view DDL,
  replay/live/runtime consumer rewiring, `settlements_v2` population,
  market-identity backfill, or legacy-settlement promotion is authorized.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Next action

- Commit and push this P1.5a implementation slice before starting the next
  packet.
- Preserve unrelated dirty work and concurrent in-flight edits.
