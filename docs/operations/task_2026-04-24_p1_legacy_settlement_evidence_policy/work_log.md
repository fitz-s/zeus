# P1.4 Legacy Settlement Evidence Policy - Work Log

Date: 2026-04-24
Branch: `post-audit-remediation-mainline`
Task: P1.4 legacy settlement evidence-only / finalization policy implementation

Changed files:
- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`
- `docs/operations/current_state.md`
- `docs/operations/AGENTS.md`

Summary:
- Renamed the working branch from `p1-unsafe-observation-quarantine` to
  `post-audit-remediation-mainline`, pushed the renamed branch, and deleted
  the old remote branch.
- Reopened context under current `AGENTS.md`, `workspace_map.md`, operations
  router, current-state, current data/source fact companions, and P1.2/P1.3
  packet boundaries.
- Created the P1.4 planning packet for legacy settlement evidence-only /
  finalization policy. This freezes a read-only diagnostic path and explicitly
  excludes production DB mutation, schema/view DDL, `settlements_v2`
  population, market-identity backfill, eligibility views/adapters, and
  calibration/replay/live consumer rewiring.
- Scout mapped settlement/finality anchors across P1.3, forensic package,
  v2 schema expectations, and readiness tests. The key conclusion is that P1.4
  must make legacy `settlements` evidence-only status explicit, not promote or
  rewrite settlement truth.
- Updated docs/topology registry companions required by map-maintenance for
  the new active operations packet.
- Implemented P1.4 read-only training-readiness diagnostics in
  `scripts/verify_truth_surfaces.py`.
- Added legacy `settlements` checks for:
  `settlements.legacy_market_identity_missing`,
  `settlements.legacy_finalization_policy_missing`,
  `settlements.legacy_value_incomplete`, and
  `settlements.legacy_evidence_only`.
- Narrowed the final implementation contract after architect/critic review:
  legacy market identity only accepts `market_slug`; `settled_at` is not
  accepted as source-finalization proof; finalization policy remains missing
  until an explicit source-finalization timestamp, revision/finalization
  policy, and market-rule version exist; value completeness only gates
  `VERIFIED` rows so intentional `QUARANTINED` value/bin gaps are not reported
  as defects.
- Added focused regression tests for absent legacy `settlements`, missing
  legacy market identity, missing finalization policy contract, incomplete
  `VERIFIED` value evidence, ignored `QUARANTINED` value gaps, rejected
  synthetic finalization alias columns, legacy evidence-only status when
  `settlements_v2` is empty, and fail-closed finalization behavior when
  `settlements_v2` identity is otherwise ready.
- Cleaned existing touched-test-file semantic-linter hazards in skipped legacy
  tests by replacing regional cluster literals with city-name clusters and
  moving the skipped freshness probe away from legacy `calibration_pairs`.

Verification:
- Reread `AGENTS.md`.
- Reread `workspace_map.md`.
- Read `docs/AGENTS.md` and `architecture/AGENTS.md` for registry companion
  updates.
- Read `docs/operations/current_state.md`, `docs/operations/AGENTS.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`, and
  `docs/operations/known_gaps.md`.
- Read P1.2 and P1.3 packet boundaries.
- Read forensic settlement/data-readiness surfaces:
  `07_settlement_alignment_audit.md`, `11_data_readiness_ruling.md`,
  `17_apply_order.md`, `03_table_by_table_truth_audit.md`,
  `08_provenance_and_authority_audit.md`, and
  `validation/required_db_queries.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.4 legacy settlement evidence-only finalization policy planning" --files docs/operations/current_state.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md docs/operations/known_gaps.md --json`
  returned known global docs/source/history-lore red issues. Those are derived
  routing debt and do not authorize skipping scoped gates.
- First map-maintenance run reported required companion updates for
  `docs/AGENTS.md`, `docs/README.md`, `architecture/topology.yaml`, and
  `architecture/docs_registry.yaml`; the planning scope was widened only for
  those registry companions.
- After registry companion updates, reran JSON validation, planning-lock,
  work-record, change-receipts, current-state receipt binding,
  map-maintenance precommit, freshness metadata, and `git diff --check`; all
  passed for the expanded changed-file set.
- `python3 scripts/topology_doctor.py impact --files <expanded changed-file set>`
  reported no source zones, write routes, hazards, or required tests for this
  planning-only docs/registry packet.
- `.venv/bin/python scripts/semantic_linter.py --check <expanded changed-file set>`
  passed with zero AST files verified, as expected for docs/registry-only
  changes. System `python3` cannot import this linter due local interpreter
  syntax support, so `.venv/bin/python` is the valid command surface.
- Closeout navigation still reports known global docs/source/history-lore red
  issues outside this packet. The P1.4 scoped gates above remain green.
- Critic review returned PROCEED. Non-blocking watch item: future
  implementation must define an accepted finalization-policy evidence
  column/alias contract in script/tests before adding blockers.
- Fresh implementation phase reread `AGENTS.md`, `workspace_map.md`,
  `docs/operations/current_state.md`, `docs/operations/AGENTS.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`, `docs/operations/known_gaps.md`,
  `scripts/AGENTS.md`, `tests/AGENTS.md`, script/test manifest entries, and
  the P1.4 plan.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.4 legacy settlement evidence policy implementation" --files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py docs/operations/current_state.md docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json --json`
  returned known global docs/source/history-lore red issues, with the scoped
  implementation files allowed and `state/*.db` / `.omx/**` forbidden.
- Read-only live schema probe confirmed `settlements` has 1,561 rows with
  `market_slug` empty on all rows, current source/provenance/rounding evidence
  present, `settlements_v2` present with 0 rows, and VERIFIED value/bin gaps
  at 0 while QUARANTINED rows account for the allowed null value/bin cases.
- First architect/critic review returned BLOCK because the initial alias set
  was too broad and counted QUARANTINED value gaps. The implementation was
  revised to exact current legacy fields and VERIFIED-only value completeness.
- Post-fix critic review returned BLOCK only for a missing negative regression
  proving ad hoc finalization aliases stay rejected. Added that regression and
  reran focused/static checks.
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py` passed.
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py::TestTrainingReadinessP0 -q`
  passed: 42 passed.
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --world-db state/zeus-world.db --json`
  returned expected `NOT_READY` with P1.4 settlement blockers:
  `settlements.legacy_market_identity_missing=1561`,
  `settlements.legacy_finalization_policy_missing=1561`,
  `settlements.legacy_evidence_only=1561`, and
  `settlements.legacy_value_complete` PASS/count 0.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py --plan-evidence docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md --json`
  passed.
- `python3 scripts/topology_doctor.py impact --files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py`
  reported no source zones, write routes, hazards, or required tests; it
  listed the semantic-linter static check.
- `.venv/bin/python scripts/semantic_linter.py --check scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py`
  passed. System `python3` remains invalid for this linter on this machine due
  interpreter syntax support.
- `.venv/bin/python -m pytest tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_tier_resolver.py tests/test_backfill_scripts_match_live_config.py -q`
  passed: 86 passed.
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py -q` still
  reports the pre-existing `TestGhostPositions.test_no_ghost_positions`
  failure because the local live DB lacks `trade_decisions`; this is not a
  P1.4 regression and remains a known full-file health-test gap.

Next:
- Wait for post-fix critic result, then run closeout gates, commit, and push
  the P1.4 implementation packet.
