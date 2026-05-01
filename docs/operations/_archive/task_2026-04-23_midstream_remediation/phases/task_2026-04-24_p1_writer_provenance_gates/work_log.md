# P1.2 Writer Provenance Gates Ralplan - Work Log

Date: 2026-04-24
Branch: `data-improve`
Task: P1.2 observation_instants_v2 writer provenance gates ralplan
Changed files:
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_writer_provenance_gates/receipt.json`
- `src/data/observation_instants_v2_writer.py`
- `tests/test_obs_v2_writer.py`

Summary:
- Closed the live pointer over P1.1 and rotated active operations routing to
  this P1.2 planning-only packet.
- Scoped P1.2 to writer/call-site provenance gates for future
  `observation_instants_v2` rows.
- Deferred schema, DB mutation, settlement identity, calibration readers,
  runtime state, and authority/architecture changes.
- Architect review found P1.2 can stay narrow only if the writer derives
  `source_role`, `training_allowed`, and `causality_status` from existing
  `ObsV2Row` fields and validated provenance. The plan was narrowed to keep
  the `ObsV2Row` constructor stable and leave caller scripts as compatibility
  gates rather than implementation files.
- Metric identity fields (`temperature_metric`, `physical_quantity`,
  `observation_field`) are deferred because the current writer row carries both
  `running_max` and `running_min` and lacks a single-track metric identity
  input. Any need to write those now widens the packet.

Verification:
- P1.1 implementation commit `af7dd52` received post-close critic/verifier
  PASS before this packet opened.
- `python scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python scripts/topology_doctor.py digest --task "P1.2 observation_instants_v2 writer provenance gates" --files src/data/observation_instants_v2_writer.py tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py scripts/backfill_obs_v2.py scripts/fill_obs_v2_dst_gaps.py scripts/fill_obs_v2_meteostat.py --json`
  identified writer/caller files and `observation_instants_v2_typed_writer`
  source rationale.
- `python scripts/topology_doctor.py --code-review-graph-status --json`
  remains known-red on derived graph state from unrelated workspace changes;
  graph output is not authority for this packet.
- `python scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_writer_provenance_gates/plan.md --json`
  passed for the planning-only changed-file set.
- `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_writer_provenance_gates/work_log.md --json`
  passed.
- `python scripts/topology_doctor.py --change-receipts --changed-files ... --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_writer_provenance_gates/receipt.json --json`
  passed.
- `python scripts/topology_doctor.py --current-state-receipt-bound --json`
  passed.
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files ... --json`
  passed.
- `git diff --check -- <P1.2 planning files>` passed.
- Architect review completed: recommended writer-only derivation from existing
  `city`, `source`, and validated `provenance_json`; warned that constructor
  expansion would force all four production writer callers into scope.
- After architect-driven scope narrowing, reran `planning-lock`,
  `work-record`, `change-receipts`, `current-state-receipt-bound`,
  `map-maintenance`, and `git diff --check`; all passed for the planning-only
  changed-file set.
- Critic review rejected three plan gaps. Fixes applied: recorded semantic
  boot/fatal-misread evidence, clarified unknown/model-only rows remain A2 hard
  rejects before INSERT, added stop conditions for source-allowlist relaxation
  and stronger provenance-key requirements, and updated `current_state.md`
  next action to match the already-completed planning gates.
- Follow-up critic review rejected one stale work-log `Next` item. Fixed the
  remaining steps to match the live pointer: final review, scoped commit/push,
  and post-close review before implementation.
- After interruption/long-running fact reset, reread root `AGENTS.md`,
  `docs/operations/AGENTS.md`, and `docs/operations/current_state.md`; reran
  `.venv/bin/python scripts/topology_doctor.py digest --task "P1.2 writer
  provenance gates planning packet fact reset before scoped commit" --files
  <P1.2 planning files> --json`. The digest required those AGENTS reads and
  kept the allowed file set to this packet's five planning files.
- After the fact-reset work-log update, reran `planning-lock`, `work-record`,
  `change-receipts`, `current-state-receipt-bound`, `map-maintenance`, and
  `git diff --check`; all passed for the same five-file planning set.
- P1.2 planning commit `e498b0d` pushed to `origin/data-improve`. Post-close
  third-party critic and verifier both passed, freezing the plan for
  implementation.
- Implementation ralplan checks completed: architect approved writer-local
  derivation with stable `ObsV2Row`; test-engineer required additional positive
  persistence coverage for Tier-2 primary Ogimet and WU Meteostat fallback.
- Test-first red run: `.venv/bin/python -m pytest tests/test_obs_v2_writer.py
  tests/test_hk_rejects_vhhh_source.py -q` failed 5 new persistence assertions,
  proving the writer still relied on DB defaults (`source_role=NULL`,
  fallback/HKO `training_allowed=1`, `causality_status='OK'`).
- Implemented writer-local derivation in
  `src/data/observation_instants_v2_writer.py`: INSERT now explicitly writes
  `training_allowed`, `causality_status`, and `source_role` from the P1.1
  registry without changing `ObsV2Row` constructor or caller scripts.
- Green verification:
  `.venv/bin/python -m py_compile src/data/observation_instants_v2_writer.py
  scripts/backfill_obs_v2.py scripts/fill_obs_v2_dst_gaps.py
  scripts/fill_obs_v2_meteostat.py scripts/hko_ingest_tick.py` passed;
  `.venv/bin/python -m pytest tests/test_obs_v2_writer.py
  tests/test_hk_rejects_vhhh_source.py tests/test_tier_resolver.py -q`
  passed (78 tests);
  `.venv/bin/python -m pytest tests/test_backfill_scripts_match_live_config.py
  -q` passed (8 tests).
- Implementation topology gates passed for the seven-file implementation set:
  `planning-lock`, `work-record`, `change-receipts`,
  `current-state-receipt-bound`, `map-maintenance`, `freshness-metadata`, and
  `git diff --check`.
- Code Review Graph status remains derived-red: missing ignore guard, stale
  graph head, and partial coverage for the touched writer/test files. Per
  root AGENTS, graph output is derived context only and does not override the
  scoped gates, packet receipt, source rationale, or tests.
- Pre-close critic and verifier both passed. Critic found the writer-local
  derivation narrow and consistent with P1.1; verifier confirmed receipt/diff
  consistency, green tests, topology gates, and derived-red graph recording.

Next:
- P1.2 is closed at implementation commit `16292e2`.
- Before every future phase, reread root `AGENTS.md`, run topology navigation
  for that phase's candidate files, and explore the important files routed by
  topology before implementation.
- Open the next P1 packet with a fresh ralplan. The corrected future sequence
  is P1.3 quarantine/mark unsafe observation families, P1.4 settlement
  market-identity/evidence-only policy, and P1.5 eligibility views/adapters
  plus calibration/training-preflight cutover.

Post-close reassessment:
- Reread current root `AGENTS.md`, `workspace_map.md`,
  `docs/operations/AGENTS.md`, `docs/operations/current_state.md`, and the
  restored topology/source/test-trust surfaces after commit `4933b80`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.2 writer provenance gates post-close control surface repair" --files <P1.2 closeout files> --json`
  returned known global docs/source/history-lore red issues and gate-trust
  context; these are recorded as restored-topology routing debt, not P1.2
  writer-code blockers.
- Architect review of the restored law returned ITERATE for future phase
  boundaries: P1 cannot end at producer-side labels/views only. Calibration /
  training-preflight cutover must move into a later P1 slice, while broad
  replay/live rewiring remains P3.
