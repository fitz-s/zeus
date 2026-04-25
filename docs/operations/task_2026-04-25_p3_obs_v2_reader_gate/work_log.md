# Work Log -- task_2026-04-25_p3_obs_v2_reader_gate

## Machine Work Record

Date: 2026-04-25
Branch: midstream_remediation
Task: P3 4.5.B-lite observation_instants_v2 reader gate
Changed files: architecture/docs_registry.yaml; architecture/script_manifest.yaml; architecture/test_topology.yaml; architecture/topology.yaml; docs/AGENTS.md; docs/README.md; docs/operations/AGENTS.md; docs/operations/current_state.md; docs/operations/task_2026-04-25_p2_daily_observation_revision_history/plan.md; docs/operations/task_2026-04-25_p2_daily_observation_revision_history/receipt.json; docs/operations/task_2026-04-25_p2_daily_observation_revision_history/scope.yaml; docs/operations/task_2026-04-25_p2_daily_observation_revision_history/work_log.md; docs/operations/task_2026-04-25_p3_obs_v2_reader_gate/plan.md; docs/operations/task_2026-04-25_p3_obs_v2_reader_gate/receipt.json; docs/operations/task_2026-04-25_p3_obs_v2_reader_gate/scope.yaml; docs/operations/task_2026-04-25_p3_obs_v2_reader_gate/work_log.md; scripts/etl_diurnal_curves.py; scripts/semantic_linter.py; scripts/verify_truth_surfaces.py; tests/test_obs_v2_reader_gate.py; tests/test_semantic_linter.py; tests/test_truth_surface_health.py
Summary: Add consumer-local non-metric reader gates to canonical diurnal obs_v2 analytics and fail-closed readiness checks for unsafe reader identity.
Verification: py_compile; focused obs_v2 reader/readiness/semantic-linter tests; architecture/truth-surface tests; topology tests/scripts/freshness/planning-lock/map-maintenance/current-state/work-record/change-receipts.
Next: Finish code-review repair loop, close receipt, commit, push, then continue to the next remediation packet.

## 2026-04-25 -- packet started
- Created via `zpkt start`.
- Reread phase-entry law and current-state surfaces after A2 landed.
- Scout confirmed `scripts/etl_hourly_observations.py` and
  `scripts/etl_diurnal_curves.py` are the only active
  `observation_instants_current` consumers.
- Architect review narrowed the route further: do not add a shared safe view
  and do not change `scripts/etl_hourly_observations.py` in the first slice.
- Selected 4.5.B-lite: add consumer-local non-metric reader gates to
  `scripts/etl_diurnal_curves.py` and add readiness/test coverage for unsafe
  reader identity. Do not decide or alter the separate hourly metric-layer
  question.
- Focused semantic-linter verification exposed a pre-existing false positive:
  the K2 `FROM calibration_pairs` regex also matched `calibration_pairs_v2`.
  The packet includes the narrow regex word-boundary fix plus a regression so
  touched P3 tests can be statically checked.
