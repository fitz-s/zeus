# Current State

Role: single live control pointer for the repo.

## Active program

### Active override — Zeus R3 ultimate plan (2026-04-27)

- Branch: `plan-pre5`
- Mainline task: **Zeus R3 CLOB V2 / live-money upgrade — G1 live-readiness gates phase entry**
- Active package source: `docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md`
- Active package plan: `docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md`
- Current R3 phase card: `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml`
- Supporting CLOB V2 packet: `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/`
- Live-money contract: `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`
- Phase status tracker: `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- Current phase: `G1 ENGINEERING HARDENED; EXTERNAL EVIDENCE BLOCKED / LIVE NO-GO` — post-interruption verification confirms the safe no-operator seams keep improving, but this is **not** only waiting for a human "yes". The current local evidence is: targeted residual repair group `15 passed, 15 skipped`; broad R3 aggregate `128 passed, 2 skipped`; topology `--scripts` and `--tests` both `ok true`; R3 drift `GREEN=241 YELLOW=0 RED=0` with `r3/drift_reports/2026-04-28.md`; `scripts/live_readiness_check.py --json` still fails closed with `16/17` gates because Q1 Zeus-egress and staged-live-smoke evidence are absent and `live_deploy_authorized=false`; full-repo pytest sample is still red (`--maxfail=30`: 30 failed, 2566 passed, 91 skipped, 16 deselected, 1 xfailed, 1 xpassed). Additional hardening since the second-round review includes CutoverGuard LIVE_ENABLED evidence binding to a 17/17 readiness report, WU transition scripts requiring operator-provided `WU_API_KEY`, settlement rebuild helper registration, and stale fixture compatibility fixes. Remaining no-go blockers: real Q1/staged evidence, G1 close review, explicit `live-money-deploy-go`, full-suite riskguard/harvester/runtime-guard triage, and current-fact data/training evidence for any TIGGE/calibration/live-alpha claim.
- Freeze note: A2 pre-close completion does not authorize live venue submission/cancel/redeem, CLOB cutover, automatic cancel-unknown unblock in production, live R1 redeem side effects, calibration retrain go-live, external TIGGE archive HTTP/GRIB fetch, production DB mutation outside explicit test/local schema seams, credentialed WS activation, live strategy promotion, or live deployment. Q1-zeus-egress and CLOB v2 cutover go/no-go remain OPEN.
- Freeze point: live placement remains blocked by Q1/cutover plus heartbeat/collateral/snapshot gates. G1 may implement/readiness-check gate surfaces only; it cannot authorize live deployment, run live smoke, or execute live venue side effects.
- Temporary remediation packet: `docs/operations/task_2026-04-28_contamination_remediation/` is active evidence for Codex drift cleanup only. It does not authorize live deployment, production DB mutation, CLOB cutover, TIGGE training/data-readiness work, or history_lore remediation.

- Branch: `main`
- Mainline task: **Post-audit remediation mainline — operations package cleanup closed; P4 mutation blocked**
- Active package source: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Active package plan: `docs/operations/task_2026-04-23_midstream_remediation/plan.md`
- Active execution packet: none frozen; next packet pending phase-entry
- Phase evidence root: `docs/operations/task_2026-04-23_midstream_remediation/phases/`
- Phase evidence index: `docs/operations/task_2026-04-23_midstream_remediation/phases/README.md`
- Closeout evidence packet: `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/plan.md`
- Prior PR #17 review-fix evidence packet: `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_pr17_review_fixes/plan.md`
- Receipt-bound source: `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/receipt.json`
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

  PR #17 review fixes are closed on `main`: replay strict preflight now matches
  market-event bins semantically instead of by raw label text, packet
  `scope.yaml` schema accepts the emitted `closed` status, the Code Review
  Graph context-pack path no longer swallows internal `TypeError`s, the bare
  `test` digest regression is strict, and `pytest.ini` states that full
  automated coverage must override the default `live_topology` marker filter.

  Operations package cleanup is closed: midstream remediation phase evidence
  now lives under the single
  `docs/operations/task_2026-04-23_midstream_remediation/phases/` root, and
  guidance/tooling now says phases of one package must stay inside that package.
  The follow-up runtime projection cleanup removed
  `state/daemon-heartbeat.json` and `state/status_summary.json` from Git
  tracking while leaving local daemon output intact.

## Required evidence

- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/scope.yaml`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-26_operations_package_cleanup/receipt.json`

## Freeze point

- Current freeze: P1.5a, POST_AUDIT_HANDOFF Immediate 4.1.A-C, P0 4.2.A,
  P0 4.2.B, P0 4.2.C planning, and P0 4.2.C implementation
  are closed. Current branch facts show Immediate 4.1.A-C already landed and
  closed in `docs/operations/task_2026-04-23_midstream_remediation/work_log.md`
  (`P-1 Pre-P0 Post-Audit Cleanup -- 2026-04-24`), and 4.2.A closed at
  `0b61261`, while 4.2.B closed at `3e1bda7`, 4.2.C planning closed at
  `8e94f4a`, and 4.2.C implementation closed at `1411017`. The P1.5/P1.5a packet
  `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_eligibility_views_training_preflight/plan.md`
  remains a historical topology anchor only; it is not the active slice.
  WU/HKO daily observation writer provenance identity, obs_v2 provenance
  identity, P2 4.4.B-lite backfill completeness guardrails, P2 4.4.A1/A2
  revision history, P3 4.5.A settlement metric-read enforcement, the P3
  residual replay usage-path guard, P3 4.5.B-lite obs_v2 reader-gate consumer
  hardening, post-P3/P4 preflight evidence, the read-only P4 readiness
  checker, PR #17 review fixes, and operations package cleanup are closed. No
  active implementation packet is frozen. This pointer does not authorize
  production DB mutation,
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
- Independent top-level operation packages retained outside the midstream
  phase tree: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`,
  `docs/operations/task_2026-04-23_graph_rendering_integration/`, and
  `docs/operations/task_2026-04-25_p2_packet_runtime/`.
- Merge-preserved packet evidence retained outside the active execution lane:
  `docs/operations/task_2026-04-27_harness_debate/`. This is debate/review
  evidence only; it is not live-deploy, production DB mutation, source-routing,
  or calibration authority.
- Design simplification audit and phased repair evidence:
  `docs/operations/task_2026-04-29_design_simplification_audit/`. This is
  packet-local evidence only; it is not live-deploy, production DB mutation,
  source-routing, or calibration authority.

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


## Active worktrees (2026-04-26)

Three claude worktrees are concurrently active. Per-worktree scope, file-touch
inventory, and cross-worktree collision matrix live in
`docs/operations/task_2026-04-26_live_readiness_completion/evidence/audit_2026-04-26.md`.
Do not edit a file inventoried by another worktree without coordinating there first.

| Worktree path | Branch | Packet root |
|---|---|---|
| `zeus-pr18-fix-plan-20260426` | `claude/pr18-execution-state-truth-fix-plan-2026-04-26` | `task_2026-04-26_execution_state_truth_p0_hardening/` + `..._p1_command_bus/` |
| `zeus-fix-plan-20260426` | `claude/zeus-full-data-midstream-fix-plan-2026-04-26` | `task_2026-04-26_full_data_midstream_fix_plan/` |
| `zeus-live-readiness-2026-04-26` | `claude/live-readiness-completion-2026-04-26` | `task_2026-04-26_live_readiness_completion/` (planning-only) |

## Next action

- Use the closed P4 readiness checker output to freeze the next narrow packet.
  Do not start P4 mutation until the relevant operator/source/data evidence
  appears and semantic boot/topology gates are rerun.
- Preserve unrelated dirty work and concurrent in-flight edits.
- 2026-04-25 packet `task_2026-04-25_p2_packet_runtime` landed (head 7bf8da2). <!-- zpkt landed: task_2026-04-25_p2_packet_runtime -->
