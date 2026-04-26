# P1.3 Unsafe Observation Quarantine - Work Log

Date: 2026-04-24
Branch: `data-improve`
Task: P1.3 unsafe observation quarantine planning packet

Changed files:
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`
- `docs/AGENTS.md`
- `docs/README.md`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`

Summary:
- Reopened the phase entry using current root `AGENTS.md` and topology after
  the restored law/topology commit `4933b80`.
- Closed P1.2 as a writer-local packet at implementation commit `16292e2`.
- Created this P1.3 planning packet as the next active execution packet.
- Updated the required docs/topology registry companions for the new active
  packet because restored map-maintenance now treats added operations packet
  files as requiring docs root and topology registry touchpoints.
- Tightened `docs/operations/AGENTS.md` registry wording so the new active
  packet is explicitly planning-only and fail-closed.
- Corrected the future phase boundary: P1.3 is unsafe-observation quarantine /
  non-training policy; P1.4 is legacy-settlement evidence-only/finalization
  policy for existing rows, not `settlements_v2` population or market-identity
  backfill; P1.5 is eligibility views/adapters plus calibration/training-
  preflight cutover; broad replay/live rewiring remains P3.
- Applied architect ITERATE findings by tightening empty-provenance semantics
  to include structurally empty JSON (`{}` / `[]`), requiring live read-only
  training-readiness verification for future implementation closeout, and
  correcting the `tests/test_truth_surface_health.py` trust-status wording.

Verification:
- Reread `AGENTS.md`.
- Reread `workspace_map.md`.
- Read P1 handoff sections for WU empty-provenance triage and resumption.
- Read forensic P1 prompt, `11_data_readiness_ruling.md`, and
  `17_apply_order.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.3 unsafe observation quarantine planning packet" --files <candidate files> --json`
  returned known global docs/source/history-lore red issues and test
  gate-trust context.
- Verified `tests/test_truth_surface_health.py` current status directly:
  lifecycle header exists and the file is categorized as a core law antibody,
  but it is not registered under `test_trust_policy.trusted_tests` and carries
  high-sensitivity skip debt. Future implementation may audit and record its
  assumptions, but must not treat a full-file pass as automatically trusted
  closeout evidence.
- `.venv/bin/python scripts/verify_truth_surfaces.py --help` passed and
  confirmed the required future live read-only command shape:
  `--mode training-readiness --world-db state/zeus-world.db --json`.
- `python3 -m json.tool` passed for this P1.3 receipt and the P1.2 receipt.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed
  after the architect fix pass.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed after
  the architect fix pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md --json`
  passed.
- `python3 scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md --json`
  passed.
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <files> --receipt-path docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json --json`
  passed.
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --json`
  passed.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <files> --json`
  passed.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <files> --json`
  passed.
- `git diff --check -- <files>` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.3 unsafe observation quarantine planning packet" --files <candidate files> --json`
  still reports pre-existing global docs/source/history-lore red issues and
  digest-profile gate-trust warnings outside this planning packet's changed
  files. Scoped closeout remains bound to the planning-lock, receipt,
  current-state, map-maintenance, and freshness gates above.
- `python3 scripts/topology_doctor.py --code-review-graph-status --json`
  failed on derived-context graph hygiene (`.code-review-graph` ignore guard
  missing and graph metadata stale vs current HEAD). This packet does not
  modify graph artifacts; graph output remains advisory only.
- Critic review returned ITERATE for two evidence-accuracy issues: align
  `current_state.md` with the applied architect pass, and make
  `docs/operations/AGENTS.md` changed-file evidence accurate. The companion
  registry now has a real planning-only wording update, satisfying the
  map-maintenance requirement for added operations packet files.
- Verifier review returned ITERATE for final closeout hygiene: align
  `current_state.md` and this work log on the final next action, and rerun
  whitespace evidence so untracked new packet files are covered. The pointer
  was updated to commit/push-only after reviews.
- `git add -N docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/{plan.md,work_log.md,receipt.json}`
  followed by `git diff --check --cached -- <new P1.3 packet files>` passed,
  making whitespace evidence real for the new untracked files.
- After verifier fixes, reran JSON validation, planning-lock, work-record,
  change-receipts, current-state receipt binding, map-maintenance,
  freshness-metadata, tracked-file `git diff --check`, and cached
  new-file `git diff --check`; all passed.
- Verifier follow-up returned PASS with no blocking gaps. Remaining risks are
  unrelated dirty `state/*` files, which must stay unstaged, and stale Code
  Review Graph advisory context, which is not authority for this packet.

Next:
- Commit and push the planning packet only.
- Future P1.3 implementation starts only after post-close review and a fresh
  phase entry rereads `AGENTS.md`, runs topology, and explores routed files.

## Implementation closeout addendum

Date: 2026-04-24
Branch: `p1-unsafe-observation-quarantine`
Task: P1.3 unsafe observation quarantine implementation

Changed files:
- `scripts/verify_truth_surfaces.py`
- `tests/test_truth_surface_health.py`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md`
- `docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json`

Summary:
- Confirmed branch cleanup before implementation: local branches are only
  `main` and `p1-unsafe-observation-quarantine`; remote branches are only
  `origin/main`.
- Reopened phase entry under the current restored `AGENTS.md`, `workspace_map`,
  current-state, fact companions, active packet, task boot profiles, fatal
  misreads, and topology navigation.
- Extended `scripts/verify_truth_surfaces.py` training-readiness diagnostics
  to fail closed for structurally empty observation provenance (`{}` / `[]`),
  WU verified rows without provenance, unsafe training source roles,
  missing/non-OK causality status, and missing payload identity when a complete
  payload-identity column contract exists.
- Tightened `observation_instants_v2` training role eligibility to
  `historical_hourly` only. `settlement_truth` is not accepted as a training
  source role for observation instants.
- Added focused regressions for empty JSON provenance, WU provenance blockers,
  `settlement_truth`/unknown/fallback/null roles, causality blockers, and
  optional payload identity checks.
- Kept the packet read-only against production DB/state. Runtime JSON changes
  under `state/**` remain unrelated dirty work and must stay unstaged.

Verification:
- Reread `AGENTS.md`.
- Reread `workspace_map.md`.
- Read `docs/operations/current_state.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`,
  `docs/operations/known_gaps.md`, and the active P1.3 packet.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.3 unsafe observation quarantine implementation" --files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py docs/operations/current_state.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/work_log.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/receipt.json --json`
  reported known global docs/source/history-lore red issues and generic source
  modification warnings. These are derived routing debt; this implementation
  stayed inside the active packet's read-only diagnostics/test scope.
- `python3 scripts/topology_doctor.py --code-review-graph-status --json`
  failed on derived graph staleness/ignore-guard hygiene. Graph output remains
  advisory only for this packet.
- `.venv/bin/python -m py_compile scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py` passed.
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py::TestTrainingReadinessP0 -q`
  passed: 34 tests.
- `.venv/bin/python -m pytest tests/test_truth_surface_health.py -q`
  was run for the full file and failed on the pre-existing live-health gap
  `TestGhostPositions.test_no_ghost_positions`: the current live test DB has
  no `trade_decisions` table. This same gap is already recorded in the P0 data
  audit containment work log; it is outside this P1.3 diagnostic quarantine
  slice and was not changed.
- `.venv/bin/python -m pytest tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_tier_resolver.py tests/test_backfill_scripts_match_live_config.py -q`
  passed: 86 tests.
- `.venv/bin/python scripts/verify_truth_surfaces.py --mode training-readiness --world-db state/zeus-world.db --json`
  exited 1 as expected with `status=NOT_READY`. Current live blockers include
  `observation_instants_v2.training_role_unsafe=1813662`,
  `observations.verified_without_provenance=39431`, and
  `observations.wu_empty_provenance=39431`; causality unsafe count is 0 and
  payload identity is not checkable because the complete contract columns are
  not present.
- Critic review returned ITERATE for payload identity alternatives: the first
  pass checked only the first present source/station-registry alternative. The
  implementation now treats source URL/file and station registry version/hash
  as alternatives, and a regression covers the case where both alternative
  columns exist but only the second column is populated.
- Closeout gates passed after the critic fix: JSON validation, planning-lock,
  work-record, change-receipts, current-state receipt binding,
  map-maintenance precommit, freshness metadata, and staged whitespace check.
- Verifier follow-up returned PASS. Remaining risks are unrelated dirty
  `state/**` runtime artifacts and derived topology/Code Review Graph debt
  outside this packet.

Next:
- Commit and push this branch.
- Future P1.4 or P1.5 work requires a fresh phase entry before planning or
  editing.
