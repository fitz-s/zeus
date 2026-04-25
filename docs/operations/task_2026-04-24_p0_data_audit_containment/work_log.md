# P0 Data Audit Containment — Work Log

Date: 2026-04-24
Branch: `data-improve`
Task: P0 post-audit data containment readiness gates
Changed files:
- `scripts/verify_truth_surfaces.py`
- `scripts/semantic_linter.py`
- `tests/test_truth_surface_health.py`
- `tests/test_semantic_linter.py`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/runtime_artifact_inventory.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`

Summary:
- Added read-only `training-readiness` mode to `verify_truth_surfaces.py`.
- Added fail-closed blockers for empty/unsafe training data families, market
  identity, source roles, forecast times, reconstructed availability, and
  observation provenance.
- Added P0 static linter containment for bare legacy `hourly_observations`
  reads outside compatibility/evidence surfaces.
- Added targeted negative tests, registered this tracked P0 packet, and
  rotated `current_state.md` to the active P0 packet/receipt.
- Inventoried the local `.omx` mainline/PRD/test-spec/context artifacts that
  P0 uses as Ralph planning evidence.
- Tightened readiness to fail closed when `observation_instants_v2` or
  `observations` are present but empty, and routed the forensic package
  reference to its archived path.
- Tightened observation-provenance checks to fail closed when provenance
  columns are absent, and hardened legacy-hourly lint against multiline and
  quoted SQL table references.
- Added VERIFIED-observation presence and split-provenance completeness
  blockers so a passable DB cannot become `READY` with only UNVERIFIED
  observations or one-sided high/low provenance.
- Added positive `observation_instants_v2` eligibility blockers so
  `training_allowed=0` rows and unknown `source_role` values cannot certify
  readiness; narrowed the legacy-hourly linter allowlist to the canonical
  `scripts/etl_hourly_observations.py` path.
- Hardened legacy-hourly lint against block-comment and schema-qualified table
  references.
- Hardened legacy-hourly lint against Python-adjacent, concatenated, and
  keyword-argument literal SQL strings that reference `hourly_observations`.
- Completed scoped deslop pass by narrowing AST keyword scanning to explicit
  SQL-bearing keyword names.
- Post-close critic found two remaining false-confidence paths. Follow-up
  fix makes readiness fail closed when market identity columns/values are
  missing from `market_events_v2`, `market_price_history`, or `settlements_v2`,
  and hardens legacy-hourly lint against `.format`, `.format_map`, `%`,
  mapping-backed `%`, and constant-backed f-string SQL assembly.

Verification:
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py scripts/semantic_linter.py`
  passed.
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py -k training_readiness -q`
  passed: 20 passed, 7 deselected.
- `.venv/bin/python -m pytest tests/test_semantic_linter.py -q`
  passed: 27 passed.
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --json`
  exited 1 as expected with `status=NOT_READY`; blockers include empty
  `historical_forecasts_v2`, `ensemble_snapshots_v2`,
  `calibration_pairs_v2`, `platt_models_v2`, `market_events_v2`,
  `market_price_history`, `settlements_v2`, and 1,813,662
  `observation_instants_v2` fallback-source-role rows, with zero
  positive training-eligible observation instants.
- `.venv/bin/python scripts/semantic_linter.py --check scripts/etl_hourly_observations.py scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py src/engine/replay.py`
  passed.
- `python scripts/topology_doctor.py --current-state-receipt-bound --json`
  passed.
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit ...`
  passed for the P0 changed-file set.
- `git diff --check -- <P0 changed files>` passed.
- `python scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
  passed.
- `python scripts/topology_doctor.py --work-record ... --work-record-path docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md`
  passed.
- `python scripts/topology_doctor.py --change-receipts ... --receipt-path docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`
  passed.
- `python scripts/topology_doctor.py --freshness-metadata ...` passed on
  touched script/test files.
- Final verifier pass confirmed py_compile, targeted pytest, readiness
  `NOT_READY`, targeted semantic linter, topology gates, and unstaged index.
- Final critic pass found no blocker in the receipt-scoped diff after the
  deslop edit.
- Post-close P0 follow-up targeted tests passed after market-identity and
  literal-SQL bypass fixes.
- Full `tests/test_truth_surface_health.py tests/test_semantic_linter.py`
  run is still red on existing live-DB assumption:
  `TestGhostPositions.test_no_ghost_positions` cannot find
  `trade_decisions`; non-failing count is 48 passed, 5 skipped.
- Broad `--navigation`, `--scripts`, and `--tests` remain red on pre-existing
  registry/archive/script debt outside P0 scope; targeted receipt, planning,
  freshness, map-maintenance, and test gates passed. This statement records
  the original P0 snapshot; the 4.2.A follow-up below reran `--scripts` and
  `--tests` after intervening branch repairs and both returned green for the
  current branch state.

Next:
- Run topology gates, critic/verifier review, then commit and push the scoped
  P0 follow-up files only.

## 4.2.A Readiness Guard Normalization Follow-up — 2026-04-24

Date: 2026-04-24
Branch: `midstream_remediation`
Task: POST_AUDIT_HANDOFF 4.2.A P0 readiness-query / fail-closed guard normalization.
Changed files:
- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md`
- `docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json`

Plan:
- Reuse this P0 packet and the existing `verify_truth_surfaces.py`
  `training-readiness` command; do not create `scripts/zeus_readiness_check.py`.
- Keep P1.5a's phase-specific rebuild/refit preflight modes separate, but
  make the full `training-readiness` verdict inherit their strict per-metric
  snapshot eligibility and Platt mature-bucket predicates.
- Add focused `TestTrainingReadinessP0` antibodies for table-present but
  semantically ineligible `ensemble_snapshots_v2` rows and table-present but
  immature `calibration_pairs_v2` rows.
- Keep `src/**`, production DBs, runtime state, canonical v2 population, market
  backfills, and replay/live consumer rewiring out of scope.

Verification:
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py` -> passed.
- `.venv/bin/python -m pytest -q tests/test_truth_surface_health.py -k training_readiness`
  -> 44 passed, 15 deselected.
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --json`
  -> exited 1 as expected with `status=NOT_READY`; blockers now include
  per-metric `empty_rebuild_eligible_snapshots` for high/low
  `ensemble_snapshots_v2` and per-metric `empty_platt_refit_bucket` for
  high/low `calibration_pairs_v2`, in addition to existing P0 unsafe-data
  blockers.
- `python3 scripts/topology_doctor.py --navigation --task "POST_AUDIT_HANDOFF 4.2.A P0 readiness guard normalization in existing P0 data-audit containment packet" --files <4.2.A files> --json`
  -> `ok: true`, no direct blockers; repo-wide docs/source/lore debt remains
  outside this packet.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <4.2.A files> --plan-evidence docs/operations/task_2026-04-24_p0_data_audit_containment/plan.md --json`
  -> `{ok: true, issues: []}`.
- `python3 scripts/topology_doctor.py --work-record --changed-files <4.2.A files> --work-record-path docs/operations/task_2026-04-24_p0_data_audit_containment/work_log.md --json`
  -> `{ok: true, issues: []}`.
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <4.2.A files> --receipt-path docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json --json`
  -> `{ok: true, issues: []}`.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <4.2.A files> --json`
  -> `{ok: true, issues: []}`.
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --receipt-path docs/operations/task_2026-04-24_p0_data_audit_containment/receipt.json --json`
  -> `{ok: true, issues: []}`.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py --json`
  -> `{ok: true, issues: []}`.
- `python3 scripts/topology_doctor.py --scripts --json` and
  `python3 scripts/topology_doctor.py --tests --json` -> both
  `{ok: true, issues: []}`.
- `git diff --check -- <4.2.A files>` -> clean.

Next:
- Run critic/verifier review, address any findings, then commit and push only
  the scoped files.
