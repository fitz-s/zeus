# Work Log -- task_2026-04-25_p2_obs_v2_revision_history

## Machine Work Record

Date: 2026-04-25
Branch: midstream_remediation
Task: P2 4.4.A1 obs_v2 hash-checked revision history
Changed files: src/state/schema/v2_schema.py; src/data/observation_instants_v2_writer.py; scripts/backfill_obs_v2.py; tests/test_obs_v2_writer.py; tests/test_backfill_scripts_match_live_config.py; architecture/docs_registry.yaml; architecture/topology.yaml; docs/AGENTS.md; docs/README.md; docs/operations/AGENTS.md; docs/operations/current_state.md; docs/operations/task_2026-04-25_p3_usage_path_residual_guards/plan.md; docs/operations/task_2026-04-25_p3_usage_path_residual_guards/work_log.md; docs/operations/task_2026-04-25_p3_usage_path_residual_guards/scope.yaml; docs/operations/task_2026-04-25_p3_usage_path_residual_guards/receipt.json; docs/operations/task_2026-04-25_p2_obs_v2_revision_history/plan.md; docs/operations/task_2026-04-25_p2_obs_v2_revision_history/work_log.md; docs/operations/task_2026-04-25_p2_obs_v2_revision_history/scope.yaml; docs/operations/task_2026-04-25_p2_obs_v2_revision_history/receipt.json
Summary: Add schema-backed obs_v2 revision capture and replace silent replace-on-conflict writer behavior with hash-checked idempotence.
Verification: py_compile passed; obs_v2 writer tests passed; trusted data writer tests passed; architecture/truth surface tests passed; topology tests/scripts/planning-lock/map/freshness/current-state gates passed.
Next: Commit and push this packet before opening daily-observation A2 history work.

## 2026-04-25 -- packet started
- Created via `zpkt start`.
- Corrected packet branch metadata back to the mainline branch
  `midstream_remediation` after the helper generated a packet-local branch
  name.
- Opened the A1 scope as `observation_instants_v2` revision-history hardening:
  schema-backed revision sink plus central writer hash checks.
- Deferred daily WU/HKO/Ogimet `observations` backfills, legacy
  `observation_instants`, live daily ingest, production DB mutation, and P4
  data population.
- Added `observation_revisions` DDL in `src/state/schema/v2_schema.py` with
  obs_v2 lookup and payload-dedup indexes.
- Replaced the obs_v2 writer's replace-on-conflict path with explicit
  hash-checked handling:
  - new natural key inserts into `observation_instants_v2`
  - duplicate natural key with same payload hash is a no-op if material fields
    match, ignoring only `imported_at`
  - duplicate natural key with reused payload hash but changed material fields
    raises `InvalidObsV2RowError`
  - duplicate natural key with a different payload hash records the incoming
    row in `observation_revisions` and leaves the current row unchanged
- Updated `scripts/backfill_obs_v2.py` prose to describe writer-level
  hash-checked idempotence instead of replace semantics.
- Tightened `insert_rows()` return semantics so duplicate no-ops and
  revision-only collisions do not inflate `rows_written`; added a caller-level
  `backfill_obs_v2` regression for rerun counters.
- Closed the prior P3 residual packet artifacts to align plan/scope/receipt
  status with the already-pushed implementation commit `3e8056b`.
- Verification so far:
  - `python3 -m py_compile src/state/schema/v2_schema.py src/data/observation_instants_v2_writer.py scripts/backfill_obs_v2.py tests/test_obs_v2_writer.py tests/test_backfill_scripts_match_live_config.py` passed.
  - `pytest -q tests/test_obs_v2_writer.py` passed: 49 passed.
  - `pytest -q tests/test_obs_v2_writer.py tests/test_backfill_scripts_match_live_config.py tests/test_hk_rejects_vhhh_source.py tests/test_tier_resolver.py` passed: 110 passed.
  - `pytest -q tests/test_architecture_contracts.py tests/test_truth_surface_health.py` passed: 128 passed, 27 skipped.
  - `python3 scripts/topology_doctor.py --tests --json` passed.
  - `python3 scripts/topology_doctor.py --scripts --json` passed.
- Final code-reviewer pass found no blocking correctness, contract, topology,
  or test-gap issues.
- `semantic_linter.py --check` remains a non-blocking pre-existing static-check
  mismatch for the `observation_instants_v2` `local_hour` contract field
  transport; this packet did not add new raw time-semantics logic and did not
  expand scope to linter-rule maintenance.
