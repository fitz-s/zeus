# docs/operations AGENTS

Live control and packet-evidence surface for Zeus.

This directory is not a second authority plane. It routes current work,
attached package inputs, active packets, and operational evidence. Current law
still lives in `docs/authority/**`, `architecture/**`, tests, and executable
source.

## Surface Classes

### Live Pointer

| File | Purpose |
|------|---------|
| `current_state.md` | Single live control pointer: current program, active packet, required evidence, freeze point, next action |

Keep this file thin. It should not become a runtime diary, historical packet
index, or archive catalog.

### Active Supporting Surfaces

| File | Purpose |
|------|---------|
| `known_gaps.md` | Compatibility pointer; active known gaps now live at `docs/to-do-list/known_gaps.md` |
| `current_data_state.md` | Active current-fact surface for audited data posture |
| `current_source_validity.md` | Active current-fact surface for audited source-validity posture |
| `packet_scope_protocol.md` | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |

Current-fact files must stay summary-only, receipt/evidence-backed,
expiry-bound, and fail-closed when stale. They are planning truth, not durable
law or implementation permission.

### Active Execution Packet

No active execution packet is frozen. Freeze a new packet through
`current_state.md` before starting any implementation slice.

### Packet Evidence

`task_*/**` folders and `task_*.md` files are packet evidence unless
`current_state.md` names one as the active execution packet. Read them only when
the active task routes you there.

Packet-local file names are not global workflow requirements. Files such as
`evidence.md`, `findings.md`, `work_log.md`, and `receipt.json` are required
only when the active packet, closeout gate, audit/review task, or future handoff
consumes them. Direct T0/T1 work should not create packet evidence just to make
the workflow look complete.

Discrete `task_*` packet folders are agent-closeable by default once their
work log, report, or committed code proves the task is complete or superseded.
The closing agent must move the packet body to `docs/archives/packets/`, add or
update the `docs/archive_registry.md` closeout entry, remove active pointers,
and promote any residual OPEN work into `docs/to-do-list/known_gaps.md` or a
new admitted packet. Operator-only closeout is required only when the packet
itself says `awaiting operator`, `operator-deferred`, `STAGED, NOT COMMITTED`,
or carries an active runtime-gating artifact.

The operation-end feedback capsule is a closeout habit, not a packet filename.
For direct work, keep it in the final response. For packet closeout, append it
to an already-required work log or receipt. It should briefly capture context
recovery, Zeus improvement insights, and topology helped/blocked notes without
creating standalone evidence/findings files or widening the active packet. The
topology note should name the route/admission/risk outcome, whether it matched
the semantic task, one help, one friction, and one next topology delta or
`none_observed`. If an agent needs a route card for this habit, use intent
`direct operation feedback capsule`; do not persist capsules under
`.omx/context/` or invent `handoff` files in packet folders.

Tracked packet evidence in this live router is limited to the current rows
below. Closed packet evidence is archived under `docs/archives/packets/` and
indexed in `docs/archive_registry.md`; do not use archived packet folders as
active workflow defaults.

### Attached Package Inputs

Package-input directories are source material for a packet, not universal law.
Archived package inputs are historical lookup surfaces only. If an archived
packet still has unfinished content, summarize the live residue in
`active_unfinished_backlog.md` and point to the archived body there.

Use the active packet plan/work log to determine which package inputs matter.

## File Registry

This explicit registry keeps docs checks machine-routable. Entries here do not
make a surface default-read unless `current_state.md` routes it.

| Path | Class | Purpose |
|------|-------|---------|
| `AGENTS.md` | operations router | Registry for live operations surfaces in this directory |
| `current_state.md` | live pointer | Single current program, active packet, required evidence, freeze point, next action |
| `known_gaps.md` | compatibility pointer | Redirects to `docs/to-do-list/known_gaps.md`; no active gap content belongs here |
| `docs/to-do-list/known_gaps.md` | active checklist | Active known-gap worklist outside operations |
| `docs/to-do-list/known_gaps_archive.md` | archive interface | Closed gap antibody archive; moved to to-do-list 2026-05-01 |
| `current_data_state.md` | current fact | Current audited data posture; not authority law |
| `current_source_validity.md` | current fact | Current audited source-validity posture; not authority law |
| `packet_scope_protocol.md` | active support | Protocol reference for the Packet Runtime (`zpkt`) and `scope.yaml` sidecar contract |
| `tigge_daemon_integration.md` | packet evidence | Design doc for TIGGE retrieval inside the data-ingest daemon — operator directive 2026-05-01 |
| `edge_observation/` | active monitoring | Operator-managed edge trajectory observation surface |
| `attribution_drift/` | active monitoring | Operator-managed strategy attribution drift observation surface |
| `ws_poll_reaction/` | active monitoring | Operator-managed WebSocket/poll reaction observation surface |
| `calibration_observation/` | active monitoring | Operator-managed calibration stability observation surface |
| `learning_loop_observation/` | active monitoring | Operator-managed learning-loop health observation surface |
| `task_2026-04-26_ultimate_plan/` | active packet container | Contains live-alpha runtime-gating TIGGE authorization evidence |
| `task_2026-04-26_ultimate_plan/2026-05-01_live_alpha/evidence/tigge_ingest_decision_2026-05-01.md` | active runtime-gating evidence | TIGGE entry-primary operator authorization evidence; do not archive without replacement |
| `task_2026-05-02_live_entry_data_contract/CURRENT_ROLLOUT_MODE.md` | single-source-of-truth | Declared rollout_mode; canary `test_settings_json_rollout_mode_matches_plan_declaration` asserts agreement with `config/settings.json:entry_forecast.rollout_mode` |
| `task_2026-05-02_live_entry_data_contract/PREMISE_ERRATUM_2026-05-03.md` | erratum record | DB-probe resolution of LIVE_ELIGIBLE/HORIZON_OUT_OF_RANGE per-track vs aggregate figure mismatch |
| `task_2026-05-04_zeus_may3_review_remediation/` | lock-candidate planning packet container | Round-5 corrected-live remediation plan packet; not implementation authority until locked by `LOCK_DECISION.md` |
| `task_2026-05-04_zeus_may3_review_remediation/PLAN.md` | topology planning entrypoint | Routeable entrypoint for the Round-5 plan; points to `MASTER_PLAN_v2.md` as detailed payload |
| `task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md` | lock-candidate remediation plan | Serialized T-1/T0/T1/T2/T3/T4 plan for corrected-live safety and orchestrated-delivery execution |
| `task_2026-05-04_zeus_may3_review_remediation/ORCHESTRATOR_RUNBOOK.md` | lock-candidate orchestration runbook | Skill-derived coordinator prompt, role split, idle boot, critic-gate, verifier, and co-tenant staging protocol for the Round-5 plan |
| `task_2026-05-04_zeus_may3_review_remediation/scope.yaml` | packet scope | Plan-finalization scope sidecar; source/script/test implementation requires narrower phase scopes |
| `task_2026-05-05_topology_noise_repair/` | plan packet container | Scoped plan packet for topology boot-profile and script-route noise repair |
| `task_2026-05-05_topology_noise_repair/PLAN.md` | topology planning packet | Minimal route evidence for the 2026-05-05 topology noise repair |
| `task_2026-05-05_object_invariance_mainline/` | closeout packet container | PR67 object-meaning invariance closeout ledger and remaining-mainline alignment |
| `task_2026-05-05_object_invariance_mainline/PLAN.md` | closeout ledger | PR67 baseline, wave-evidence ledger, review claims, verification debt, and remaining mainline plan |
| `task_2026-05-05_object_invariance_wave5/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 5 settlement authority cutover |
| `task_2026-05-05_object_invariance_wave5/PLAN.md` | topology planning packet | Route evidence for settlement source/result to position settlement authority repair |
| `task_2026-05-05_object_invariance_wave6/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 6 command recovery and allocation authority repair |
| `task_2026-05-05_object_invariance_wave6/PLAN.md` | topology planning packet | Route evidence for unknown submit side-effect recovery to fill-finality and allocation-authority repair |
| `task_2026-05-05_object_invariance_wave7/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 7 forecast source identity to calibration bucket identity repair |
| `task_2026-05-05_object_invariance_wave7/PLAN.md` | topology planning packet | Route evidence for forecast source role, calibration bucket, and live/shadow evidence separation repair |
| `task_2026-05-05_object_invariance_wave8/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 8 OrderResult/fill authority economics repair |
| `task_2026-05-05_object_invariance_wave8/PLAN.md` | topology planning packet | Route evidence for venue fill observation to entry economics and report/replay cohort gates |
| `task_2026-05-05_object_invariance_wave11/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 11 current-open fill-authority DB read-model repair |
| `task_2026-05-05_object_invariance_wave11/PLAN.md` | topology planning packet | Route evidence for fill-authority current-open economics through position read models and strategy health |
| `task_2026-05-05_object_invariance_wave12/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 12 operator status bankroll semantics repair |
| `task_2026-05-05_object_invariance_wave12/PLAN.md` | topology planning packet | Route evidence for wallet-equity bankroll preservation in derived status_summary output |
| `task_2026-05-05_object_invariance_wave13/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 13 RiskGuard loader provenance repair |
| `task_2026-05-05_object_invariance_wave13/PLAN.md` | topology planning packet | Route evidence for fill-authority current-open economics provenance preservation in RiskGuard portfolio loading |
| `task_2026-05-05_object_invariance_wave14/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 14 strategy-health settlement authority repair |
| `task_2026-05-05_object_invariance_wave14/PLAN.md` | topology planning packet | Route evidence for verified settlement authority feeding strategy_health realized PnL metrics |
| `task_2026-05-05_object_invariance_wave15/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 15 replay diagnostic outcome_fact eligibility repair |
| `task_2026-05-05_object_invariance_wave15/PLAN.md` | topology planning packet | Route evidence for legacy outcome_fact to trade-history diagnostic replay/report semantics |
| `task_2026-05-05_object_invariance_wave16/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 16 diagnostic fact-table authority repair |
| `task_2026-05-05_object_invariance_wave16/PLAN.md` | topology planning packet | Route evidence for legacy outcome_fact row counts to operator diagnostic/readiness semantics |
| `task_2026-05-05_object_invariance_wave17/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 17 legacy outcome_fact producer guard repair |
| `task_2026-05-05_object_invariance_wave17/PLAN.md` | topology planning packet | Route evidence for fail-closed backfill_outcome_fact producer semantics and manifest truth |
| `task_2026-05-05_object_invariance_wave18/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 18 calibration-transfer OOS evidence time-basis repair |
| `task_2026-05-05_object_invariance_wave18/PLAN.md` | topology planning packet | Route evidence for time-blocked OOS transfer evidence and pseudo-OOS rejection |
| `task_2026-05-05_object_invariance_wave19/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 19 FinalExecutionIntent identity preservation repair |
| `task_2026-05-05_object_invariance_wave19/PLAN.md` | topology planning packet | Route evidence for corrected execution intent no-recompute, hypothesis/cost-basis direction, and snapshot-hash submit identity repair |
| `task_2026-05-05_object_invariance_wave20/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 20 exit snapshot identity preservation repair |
| `task_2026-05-05_object_invariance_wave20/PLAN.md` | topology planning packet | Route evidence for exit snapshot hash preservation through execute_exit_order retry/recovery paths |
| `task_2026-05-05_object_invariance_wave21/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 21 exchange-reconcile freshness authority repair |
| `task_2026-05-05_object_invariance_wave21/PLAN.md` | topology planning packet | Route evidence for venue-read freshness authority before M5 absence findings |
| `task_2026-05-08_object_invariance_wave27/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 27 trade-fact to position-lot exposure authority repair |
| `task_2026-05-08_object_invariance_wave27/PLAN.md` | topology planning packet | Route evidence for state-compatible venue trade facts before active `position_lots` exposure authority |
| `task_2026-05-08_object_invariance_wave28/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 28 monitor posterior to exit EV gate repair |
| `task_2026-05-08_object_invariance_wave28/PLAN.md` | topology planning packet | Route evidence for monitor-current native posterior preservation through exit trigger hold-value EV gates |
| `task_2026-05-08_object_invariance_wave29/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 29 monitor result reporting probability authority repair |
| `task_2026-05-08_object_invariance_wave29/PLAN.md` | topology planning packet | Route evidence for preventing skipped/error monitor results from reporting stale probabilities as fresh |
| `task_2026-05-08_object_invariance_wave30/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 30 position_current monitor probability read-model authority repair |
| `task_2026-05-08_object_invariance_wave30/PLAN.md` | topology planning packet | Route evidence for preserving missing monitor probability and edge through canonical portfolio loader view |
| `task_2026-05-08_object_invariance_wave31/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 31 D4 exit evidence hard gate repair |
| `task_2026-05-08_object_invariance_wave31/PLAN.md` | topology planning packet | Route evidence for preventing weak statistical exit evidence from becoming executable sell intent |
| `task_2026-05-08_object_invariance_wave32/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 32 venue fill cost-basis continuity repair |
| `task_2026-05-08_object_invariance_wave32/PLAN.md` | topology planning packet | Route evidence for preserving venue-confirmed fill cost through portfolio loader and Position effective exposure |
| `task_2026-05-08_object_invariance_wave33/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 33 passive maker fee authority repair |
| `task_2026-05-08_object_invariance_wave33/PLAN.md` | topology planning packet | Route evidence for preventing post-only passive maker-only cost basis from carrying market taker fee |
| `task_2026-05-08_object_invariance_wave34/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 34 replay execution-cost continuity repair |
| `task_2026-05-08_object_invariance_wave34/PLAN.md` | topology planning packet | Route evidence for preserving fee-adjusted replay execution cost through shares and PnL |
| `task_2026-05-08_object_invariance_wave35/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 35 calibration bulk writer isolation repair |
| `task_2026-05-08_object_invariance_wave35/PLAN.md` | topology planning packet | Route evidence for preventing rebuild/refit bulk calibration writes from defaulting to canonical live world DB |
| `task_2026-05-08_object_invariance_wave36/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 36 pending entry economics authority repair |
| `task_2026-05-08_object_invariance_wave36/PLAN.md` | topology planning packet | Route evidence for preventing pending submitted/model economics from masquerading as fill-authoritative position cost basis |
| `task_2026-05-08_object_invariance_wave37/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 37 calibration weighting LAW antibody triage |
| `task_2026-05-08_object_invariance_wave37/PLAN.md` | topology planning packet | Route evidence separating safe calibration weighting LAW antibodies from schema/data-layer blocked LAW implementation work |
| `task_2026-05-08_object_invariance_wave38/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 38 dead hourly-observations compatibility-surface deletion |
| `task_2026-05-08_object_invariance_wave38/PLAN.md` | topology planning packet | Route evidence for deleting stale `hourly_observations` table/view/script constructibility without mutating canonical DB data |
| `task_2026-05-08_object_invariance_wave39/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 39 `solar_daily` malformed-rootpage Day0 degrade antibody |
| `task_2026-05-08_object_invariance_wave39/PLAN.md` | topology planning packet | Route evidence for proving malformed `solar_daily` rootpage degrades Day0 evaluator/monitor paths without DB mutation |
| `task_2026-05-08_object_invariance_wave41/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 41 explicit fill-price authority repair |
| `task_2026-05-08_object_invariance_wave41/PLAN.md` | topology planning packet | Route evidence for preventing generic exchange `price` from becoming venue-confirmed fill economics |
| `task_2026-05-08_object_invariance_wave42/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 42 harvester corrected-economics fail-closed repair |
| `task_2026-05-08_object_invariance_wave42/PLAN.md` | topology planning packet | Route evidence for preventing corrected-marked positions without fill authority from using legacy settlement P&L fallback |
| `task_2026-05-08_object_invariance_remaining_mainline/` | closeout packet container | Remaining object-meaning invariance mainline closeout packet for Wave22/23 evidence, failed-trade rollback repair, downstream sweep, and read-only contamination audit |
| `task_2026-05-08_object_invariance_remaining_mainline/PLAN.md` | closeout ledger | Evidence and verification for remaining mainline closure; not live unlock, data mutation authority, backfill, relabel, or report-publication authority |
| `task_2026-05-06_calibration_quality_blockers/` | active packet container | 2026-05-06 launch-blocker remediation packet for calibration quality (12 inverted-slope Platts quarantined + fit-time guard) |
| `task_2026-05-06_calibration_quality_blockers/PLAN.md` | active packet plan | LOW/HIGH calibration alignment recovery plan covering contract-window evidence persistence, recovery snapshot backfill, LOW pair rebuild gates, and calibration authority shadow gating |
| `task_2026-05-06_calibration_quality_blockers/QUARANTINE_LEDGER.md` | active packet evidence | Ledger of the 12 QUARANTINED platt_models_v2 buckets with reversibility queries and fallback-chain analysis |
| `task_2026-05-07_recalibration_after_low_high_alignment/` | active packet container | Post-merge LOW/HIGH recalibration packet for contract-window recovery materialization and runtime authority checks |
| `task_2026-05-07_recalibration_after_low_high_alignment/PLAN.md` | active packet plan | Controlled LOW/HIGH recovery, pair rebuild, Platt refit, authority, and promotion-readiness plan |
| `task_2026-05-07_recalibration_after_low_high_alignment/REPORT.md` | active packet evidence | Before/after report for TIGGE LOW contract-window recovery snapshots, pairs, Platts, runtime samples, and verification |
| `task_2026-05-09_copilot_agent_sync/` | plan packet container | Planning packet for synchronizing Claude Code/OMC subagent, hook, and workflow discipline into Copilot/VS Code agent workflows |
| `task_2026-05-09_copilot_agent_sync/PLAN.md` | topology planning packet | Route evidence and phased design for Copilot/VS Code agent sync, MCP/tool bridge, hook adapter, and OpenClaw delegation |
Archived packet evidence (physically moved to `docs/archives/packets/`) is
listed in `docs/archive_registry.md`; do not re-list those packets here. When
a packet closes and is archived, remove its row from this registry and the
archive_registry entry becomes its single source of historical truth.

## Rules

- `current_state.md` stays thin: current program, active packet, required
  evidence, freeze point, next action, and compact references to other
  registered surfaces.
- Non-trivial repo changes update a short work record in the active package or
  phase folder.
- New independent multi-file packages use `task_YYYY-MM-DD_name/`.
- New phases of an existing package live under that package, usually
  `task_YYYY-MM-DD_package/phases/task_YYYY-MM-DD_phase/`; do not create
  sibling top-level folders for phases of the same package.
- Do not leave completed packet material in the live pointer after closeout.
- Do not leave completed or superseded packet folders in `docs/operations/`;
  archive them and leave only active packets, active monitoring surfaces,
  current-fact surfaces, and compatibility pointers.
- Runtime-local `.omx/.omc` planning artifacts must be inventoried or mirrored
  before they are treated as durable work evidence.
- `state/daemon-heartbeat.json` and `state/status_summary.json` are live
  runtime projections. Treat them as interference for ordinary docs/source/test
  packets; exclude them from non-runtime-governance receipts and closeout diffs
  unless the packet explicitly owns runtime state policy.
- Current-fact surfaces require fresh packet/operator evidence. Do not update
  them from memory, and re-audit if they are older than their refresh protocol
  allows for the task at hand.
- Current-fact surfaces must state Status, Last audited, Max staleness,
  Evidence packet, Receipt path, stale do-not-use policy, and refresh trigger.
- Dense module rehydration packets may change routing, manifests, module books,
  and scoped routers, but they must not silently widen into runtime/source
  semantics without an explicit packet scope change.
- When closeout reports unrelated global script/docs/test drift, phrase the
  result as changed-surface status plus repo-wide drift summary. Do not repair
  unrelated weekly diagnostics just to make a narrow packet look globally clean.
