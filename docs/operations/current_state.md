# Current State

Role: single live control pointer for the repo.

## Active program

- Branch: `midstream_remediation`
- Mainline task: **Post-audit remediation mainline — P4 read-only readiness checker closed; P4 mutation blocked**
- Active package source: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/project_brief.md`
- Active execution packet: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md` (planning package only; first coding packet is P0 hardening)
- Closeout evidence packet: `docs/operations/task_2026-04-25_p4_readiness_checker/plan.md`
- Prior post-P3/P4 preflight closeout evidence packet: `docs/operations/task_2026-04-25_post_p3_p4_preflight/plan.md`
- Prior P3 B-lite closeout evidence packet: `docs/operations/task_2026-04-25_p3_obs_v2_reader_gate/plan.md`
- Prior P2 A2 closeout evidence packet: `docs/operations/task_2026-04-25_p2_daily_observation_revision_history/plan.md`
- Prior P2 A1 closeout evidence packet: `docs/operations/task_2026-04-25_p2_obs_v2_revision_history/plan.md`
- Prior P3 residual closeout evidence packet: `docs/operations/task_2026-04-25_p3_usage_path_residual_guards/plan.md`
- Prior P3 closeout evidence packet: `docs/operations/task_2026-04-25_p3_settlement_metric_linter_closeout/plan.md`
- Prior P2 closeout evidence packet: `docs/operations/task_2026-04-25_p2_backfill_completeness_guardrails/plan.md`
- Prior P1 closeout evidence packet: `docs/operations/task_2026-04-25_p1_obs_v2_provenance_identity/plan.md`
- Prior closeout evidence packet: `docs/operations/task_2026-04-25_p1_daily_observation_writer_provenance/plan.md`
- Earlier closeout evidence packet: `docs/operations/task_2026-04-25_p0_market_events_preflight/plan.md`
- Legacy hourly evidence-view closeout anchor: `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/receipt.json`
- Status: P1.2 writer provenance gates are closed at implementation commit
  `16292e2`. P1.3 implemented read-only training-readiness quarantine
  diagnostics and tests for unsafe observation role/provenance/causality
  blockers at `7a3524e`. P1.4 planning was pushed at `da1662f`; P1.4
  implementation was pushed at `df9ece5`, adding read-only legacy
  `settlements` evidence-only readiness blockers and focused regression tests;
  P1.4 control surfaces were closed at `50cd713`. P1.5 planning was pushed
  at `07c86d8`. P1.5a implemented script-side read-only calibration-pair
  rebuild and Platt-refit preflight modes plus live-write CLI guards; critic
  and verifier review passed after excluding forbidden runtime artifacts from
  the submit diff. Implementation commit `99c4ac3` was pushed to
  `origin/midstream_remediation`. Commit `1411017` closed and pushed P0 4.2.C
  implementation after critic found and verified a wrong-label market-event
  false-pass fix. Commit `4abf8f0` closed the P0 replay preflight control
  pointer. Phase-entry reassessment selected a narrow P1 4.3.B-lite slice:
  harden WU/HKO daily observation backfill writers so new `VERIFIED` rows carry
  non-empty provenance identity. This packet added WU/HKO payload/source/parser
  provenance identity, SQLite writer-seam regressions, and test-trust metadata.
  Critic re-review passed after the first review required writer-seam coverage.
  This packet did not mutate production DB rows, create schemas/views, overhaul
  `INSERT OR REPLACE`, quarantine existing legacy observations, or jump to
  P2/P3/P4. This packet then closed the remaining P1 `4.3.B`
  `observation_instants_v2` provenance-identity seam: writer-accepted rows now
  require payload/source/parser/station provenance identity, active obs_v2
  producers stamp that identity without exposing secrets, and training-readiness
  checks fail closed when training-allowed obs_v2 rows lack JSON provenance
  identity. This packet did not mutate production DB rows, create schemas/views,
  overhaul `INSERT OR REPLACE`, quarantine existing legacy observations, or
  jump to P2/P3/P4. The implementation landed in commit `11c6315`, which also
  carried a concurrent `pytest.ini` test-isolation change already pushed to
  origin before the packet-specific commit command could create a separate
  Lore commit. Phase-entry reassessment selected a narrow P2 4.4.B-lite
  script-guardrail slice for backfill completeness manifests and fail
  thresholds; that packet closed at `0a4bae3`, with packet-runtime naming
  follow-up `cfd92f9` and runtime snapshot evidence `c023c9c` also pushed.
  Phase-entry reassessment found P3 4.5.A's named consumer joins already carry
  explicit `temperature_metric` predicates, but the H3 semantic-linter
  enforcement boundary still exempted scripts broadly enough for script reads of
  `settlements` without metric predicates to pass. Commit `381952e` closed that
  P3 4.5.A linter closeout packet, and commit `b8e0986` recorded the following
  live runtime state snapshot. Post-close verification then exposed a residual
  replay settlement read without a `temperature_metric` predicate. The active
  packet then closed at `3e8056b`, pinning replay settlement reads to metric
  identity and preserving the canonical `hourly_observations` ban proof. The
  P2 4.4.A1 then closed at `0837afc`, adding a schema-backed revision sink and
  replacing the central obs_v2 writer's silent `INSERT OR REPLACE` overwrite
  path with hash-checked idempotence. P2 4.4.A2 closed at `91d2a35`, adding a
  daily-specific revision sink for WU/HKO `observations` backfill payload drift;
  Ogimet daily revision history remains deferred until a stable payload-identity
  contract is planned. P3 4.5.B-lite closed at implementation commit
  `cdec77d`, adding non-metric reader gates to the canonical
  `observation_instants_current` analytics consumer and training-readiness
  diagnostics without changing shared view semantics or deciding hourly
  high/low metric placement. Runtime heartbeat follow-up `c653d03` was pushed
  separately. Legacy `observation_instants`, live daily ingest revision
  behavior, production DB mutation, row-level quarantine, full P3 4.5.B
  metric-layer design, and P4 data population remain out of scope. The
  post-P3/P4 preflight evidence packet closed after confirming
  `market_events_v2`, `settlements_v2`, `ensemble_snapshots_v2`, and
  `calibration_pairs_v2` are still empty, local TIGGE target artifacts are not
  present under `state/`, local market-rule artifacts were not found in the
  expected `state/raw/data` scan, `WU_API_KEY` is missing in the current shell,
  `k2_daily_obs` is failing with the same env reason, and the auto-pause
  tombstone remains an operator decision.

  The read-only P4 readiness checker is closed as a machine-readable status
  surface. `python3 scripts/verify_truth_surfaces.py --mode p4-readiness
  --json` reports `NOT_READY`, preserving P4 blocker status without DB or
  runtime mutation.

## Required evidence

- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/project_brief.md`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/task_packet.md`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/verification_plan.md`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/work_log.md`
- `docs/operations/task_2026-04-19_execution_state_truth_upgrade/receipt.json`
- Prior closeout remains available at `docs/operations/task_2026-04-25_p4_readiness_checker/plan.md`

## Freeze point

- Current freeze: P1.5a, POST_AUDIT_HANDOFF Immediate 4.1.A-C, P0 4.2.A,
  P0 4.2.B, P0 4.2.C planning, and P0 4.2.C implementation
  are closed. Current branch facts show Immediate 4.1.A-C already landed and
  closed in `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
  (`P-1 Pre-P0 Post-Audit Cleanup -- 2026-04-24`), and 4.2.A closed at
  `0b61261`, while 4.2.B closed at `3e1bda7`, 4.2.C planning closed at
  `8e94f4a`, and 4.2.C implementation closed at `1411017`. The P1.5/P1.5a packet
  `docs/operations/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
  remains a historical topology anchor only; it is not the active slice.
  WU/HKO daily observation writer provenance identity, obs_v2 provenance
  identity, P2 4.4.B-lite backfill completeness guardrails, P2 4.4.A1/A2
  revision history, P3 4.5.A settlement metric-read enforcement, the P3
  residual replay usage-path guard, P3 4.5.B-lite obs_v2 reader-gate consumer
  hardening, post-P3/P4 preflight evidence, and the read-only P4 readiness
  checker are closed. No active implementation packet is frozen. This pointer
  does not authorize production DB mutation,
  `settlements_v2` population, market-identity backfill, live executor DB
  authority, legacy-settlement promotion, broad P1 source-role/view work,
  row-level quarantine, live daily ingest changes, shared obs_v2 view redesign,
  hourly high/low metric placement, or P4 data population.

## Current fact companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/operations/known_gaps.md`

## Other operations surfaces

- Use `docs/operations/AGENTS.md` for non-default packet/package routing.
- Use `docs/archive_registry.md` for archived packet lookup.

## Parallel infrastructure packet

- Branch: `p2-packet-runtime` (landed into `midstream_remediation`)
- Scope: Packet Runtime (`zpkt`) — `scripts/zpkt.py`, `scripts/_zpkt_scope.py`,
  `.zeus-githooks/pre-commit`, `architecture/scope_schema.json`, soft-warn
  pre-commit hook, `tooling_runtime` test category, and
  `docs/operations/packet_scope_protocol.md`.
- Status: Landed; packet runtime is available for scope tracking and closeout
  receipts. Follow-up bypass telemetry review remains future tooling/process
  work, not a blocker for the active P3 slice.
- Authority footprint: Tooling/process only; does not touch pricing, settlement,
  observation, calibration, or DB authority. Mainline P1/P2/P3 sequencing is
  unaffected.


## Next action

- Use `docs/operations/task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md` to freeze the next narrow implementation packet: P0 hardening only.
  Do not start P1/P2 schema or lifecycle work until P0 closes and topology/planning-lock gates are rerun.
  Do not start P4 data mutation until the relevant operator/source/data evidence appears and semantic boot/topology gates are rerun.
- Preserve unrelated dirty work and concurrent in-flight edits.
- 2026-04-25 packet `task_2026-04-25_p2_packet_runtime` landed (head 7bf8da2). <!-- zpkt landed: task_2026-04-25_p2_packet_runtime -->
