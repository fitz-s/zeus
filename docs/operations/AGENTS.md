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

### Standalone Reports / Evidence (top-level files, not packet-scoped)

| File | Purpose |
|------|---------|
| `zeus_system_review_2026-05-16.md` | 2026-05-16 system review evidence (7-agent investigation; parent for compounding plan) |
| `zeus_agent_runtime_compounding_plan_2026-05-16.md` | Wave 1 agent runtime compounding plan v2.4 (4-critic + A2 + pre-PR + bot revisions, traced in §9) |

Top-level standalone reports must register in `architecture/docs_registry.yaml`
under `doc_class: report` with explicit `freshness_class` and `next_action`.

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
The closing agent must move the packet body to `docs/operations/archive/<YYYY>-Q<N>/`,
update `docs/operations/archive/<YYYY>-Q<N>/INDEX.md`, remove active pointers,
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
below. Closed packet evidence is archived under `docs/operations/archive/<YYYY>-Q<N>/` and
indexed in `docs/operations/archive/<YYYY>-Q<N>/INDEX.md`; do not use archived packet folders as
active workflow defaults. Active archival rules: `docs/authority/ARCHIVAL_RULES.md`.
Note: ARCHIVAL_RULES.md now lives at `docs/authority/ARCHIVAL_RULES.md` (relocated from
`docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/`
by W3 2026-05-17). A `.relocated` stub exists at the old path for reference.

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
| `live_egress/` | active runtime-gating evidence | Current Q1 Zeus daemon egress evidence for Polymarket CLOB V2 preflight; public probe results only, no secrets or signed payloads |
| `edge_observation/` | active monitoring | Operator-managed edge trajectory observation surface |
| `attribution_drift/` | active monitoring | Operator-managed strategy attribution drift observation surface |
| `ws_poll_reaction/` | active monitoring | Operator-managed WebSocket/poll reaction observation surface |
| `calibration_observation/` | active monitoring | Operator-managed calibration stability observation surface |
| `learning_loop_observation/` | active monitoring | Operator-managed learning-loop health observation surface |
| `task_2026-04-26_ultimate_plan/` | active packet container | Contains live-alpha runtime-gating TIGGE authorization evidence |
| `task_2026-04-26_ultimate_plan/2026-05-01_live_alpha/evidence/tigge_ingest_decision_2026-05-01.md` | active runtime-gating evidence | TIGGE entry-primary operator authorization evidence; do not archive without replacement |
| `task_2026-05-04_zeus_may3_review_remediation/` | lock-candidate planning packet container | Round-5 corrected-live remediation plan packet; not implementation authority until locked by `LOCK_DECISION.md` |
| `task_2026-05-05_topology_noise_repair/` | plan packet container | Scoped plan packet for topology boot-profile and script-route noise repair |
| `task_2026-05-05_object_invariance_mainline/` | closeout packet container | PR67 object-meaning invariance closeout ledger and remaining-mainline alignment |
| `task_2026-05-05_object_invariance_wave5/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 5 settlement authority cutover |
| `task_2026-05-05_object_invariance_wave6/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 6 command recovery and allocation authority repair |
| `task_2026-05-05_object_invariance_wave7/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 7 forecast source identity to calibration bucket identity repair |
| `task_2026-05-05_object_invariance_wave8/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 8 OrderResult/fill authority economics repair |
| `task_2026-05-05_object_invariance_wave11/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 11 current-open fill-authority DB read-model repair |
| `task_2026-05-05_object_invariance_wave12/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 12 operator status bankroll semantics repair |
| `task_2026-05-05_object_invariance_wave13/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 13 RiskGuard loader provenance repair |
| `task_2026-05-05_object_invariance_wave14/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 14 strategy-health settlement authority repair |
| `task_2026-05-05_object_invariance_wave15/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 15 replay diagnostic outcome_fact eligibility repair |
| `task_2026-05-05_object_invariance_wave16/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 16 diagnostic fact-table authority repair |
| `task_2026-05-05_object_invariance_wave17/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 17 legacy outcome_fact producer guard repair |
| `task_2026-05-05_object_invariance_wave18/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 18 calibration-transfer OOS evidence time-basis repair |
| `task_2026-05-05_object_invariance_wave19/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 19 FinalExecutionIntent identity preservation repair |
| `task_2026-05-05_object_invariance_wave20/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 20 exit snapshot identity preservation repair |
| `task_2026-05-05_object_invariance_wave21/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 21 exchange-reconcile freshness authority repair |
| `task_2026-05-08_object_invariance_wave27/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 27 trade-fact to position-lot exposure authority repair |
| `task_2026-05-08_object_invariance_wave28/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 28 monitor posterior to exit EV gate repair |
| `task_2026-05-08_object_invariance_wave29/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 29 monitor result reporting probability authority repair |
| `task_2026-05-08_object_invariance_wave30/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 30 position_current monitor probability read-model authority repair |
| `task_2026-05-08_object_invariance_wave31/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 31 D4 exit evidence hard gate repair |
| `task_2026-05-08_object_invariance_wave32/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 32 venue fill cost-basis continuity repair |
| `task_2026-05-08_object_invariance_wave33/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 33 passive maker fee authority repair |
| `task_2026-05-08_object_invariance_wave34/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 34 replay execution-cost continuity repair |
| `task_2026-05-08_object_invariance_wave35/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 35 calibration bulk writer isolation repair |
| `task_2026-05-08_object_invariance_wave36/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 36 pending entry economics authority repair |
| `task_2026-05-08_object_invariance_wave37/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 37 calibration weighting LAW antibody triage |
| `task_2026-05-08_object_invariance_wave38/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 38 dead hourly-observations compatibility-surface deletion |
| `task_2026-05-08_object_invariance_wave39/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 39 `solar_daily` malformed-rootpage Day0 degrade antibody |
| `task_2026-05-08_object_invariance_wave41/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 41 explicit fill-price authority repair |
| `task_2026-05-08_object_invariance_wave42/` | plan packet container | Planning-lock evidence for object-meaning invariance Wave 42 harvester corrected-economics fail-closed repair |
| `task_2026-05-08_object_invariance_remaining_mainline/` | closeout packet container | Remaining object-meaning invariance mainline closeout packet for Wave22/23 evidence, failed-trade rollback repair, downstream sweep, and read-only contamination audit |
| `task_2026-05-06_calibration_quality_blockers/` | active packet container | 2026-05-06 launch-blocker remediation packet for calibration quality (12 inverted-slope Platts quarantined + fit-time guard) |
| `task_2026-05-07_recalibration_after_low_high_alignment/` | active packet container | Post-merge LOW/HIGH recalibration packet for contract-window recovery materialization and runtime authority checks |
| `task_2026-05-08_100_blocked_horizon_audit/` | packet evidence container | Evidence packet for ECMWF blocked-horizon audit around missing 2026-05-08 forecast horizons |
| `task_2026-05-08_100_blocked_horizon_audit/RUN.md` | packet evidence | Run record for blocked-horizon audit and source-run horizon findings |
| `task_2026-05-08_alignment_safe_implementation/` | plan packet container | Safe implementation plan for alignment repair work following deep alignment audit |
| `task_2026-05-08_alignment_safe_implementation/PLAN.md` | topology planning packet | Scoped implementation plan for alignment repair without widening authority surfaces |
| `task_2026-05-08_deep_alignment_audit/` | audit packet container | Deep audit packet for alignment/data-source integrity risks |
| `task_2026-05-08_deep_alignment_audit/PLAN.md` | audit plan | Multi-phase audit plan for alignment failure modes and repair route selection |
| `task_2026-05-08_ecmwf_publication_strategy/` | packet evidence container | ECMWF publication strategy packet for safe-fetch timing and source-publication constraints |
| `task_2026-05-08_ecmwf_publication_strategy/TASK.md` | packet task | Task definition for ECMWF publication strategy review |
| `task_2026-05-08_ecmwf_publication_strategy/REPORT.md` | packet report | Findings report for ECMWF publication timing and operational strategy |
| `task_2026-05-08_ecmwf_step_grid_scientist_eval/` | packet evidence container | Scientist evaluation packet for ECMWF step-grid behavior |
| `task_2026-05-08_ecmwf_step_grid_scientist_eval/REPORT.md` | packet report | Evaluation report for ECMWF step-grid source behavior and implications |
| `task_2026-05-08_low_recalibration_residue_pr/` | packet evidence container | LOW recalibration residue packet for PR residue and closeout evidence |
| `task_2026-05-08_low_recalibration_residue_pr/RUN.md` | packet evidence | Run record for LOW recalibration residue verification |
| `task_2026-05-08_phase_b_download_root_cause/` | packet evidence container | Phase B download root-cause investigation packet |
| `task_2026-05-08_phase_b_download_root_cause/DOSSIER.md` | investigation dossier | Root-cause dossier for Phase B download failure modes |
| `task_2026-05-08_post_merge_full_chain/` | packet evidence container | Post-merge full-chain operation packet for source/backfill validation |
| `task_2026-05-08_post_merge_full_chain/TASK.md` | packet task | Task definition for post-merge full-chain checks |
| `task_2026-05-08_post_merge_full_chain/RUN.md` | packet evidence | Run record for post-merge full-chain checks and outcomes |
| `task_2026-05-08_track_a6_run/` | packet evidence container | Track A.6 execution evidence packet |
| `task_2026-05-08_track_a6_run/RUN.md` | packet evidence | Run record for Track A.6 execution and findings |
| `task_2026-05-09_daemon_restart_and_backfill/` | packet evidence container | Daemon restart and backfill operation packet |
| `task_2026-05-09_daemon_restart_and_backfill/TASK.md` | packet task | Task definition for daemon restart and ECMWF backfill |
| `task_2026-05-09_daemon_restart_and_backfill/RUN.md` | packet evidence | Run record for daemon restart/backfill execution |
| `task_2026-05-09_workflow_redesign_plan/` | plan packet container | Workflow redesign planning packet for PR discipline and topology friction reduction |
| `task_2026-05-09_workflow_redesign_plan/PLAN.md` | topology planning packet | Plan for workflow redesign, PR discipline, and topology route improvements |
| `task_2026-05-09_copilot_agent_sync/` | plan packet container | Planning packet for synchronizing Claude Code/OMC subagent, hook, and workflow discipline into Copilot/VS Code agent workflows |
| `task_2026-05-09_copilot_agent_sync/PLAN.md` | topology planning packet | Route evidence and phased design for Copilot/VS Code agent sync, MCP/tool bridge, hook adapter, and OpenClaw delegation |
| `task_2026-05-14_data_daemon_live_efficiency/` | plan packet container | Planning packet for data-daemon live-efficiency refactor: HTTP 429 handling, forecast producer split, readiness wiring, launch maintenance, and end-to-end verification |
| `task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md` | topology planning packet | Cross-module plan for isolating live OpenData forecast production and proving live readiness consumption without runtime deployment authority |
| `task_2026-05-14_data_daemon_live_efficiency/FORECAST_LIVE_OPERATOR_HANDOFF.md` | packet operator handoff | Repo-only launch/verification shape for future `com.zeus.forecast-live`; not plist installation, launchctl authorization, or production deployment evidence |
| `task_2026-05-15_data_pipeline_live_rootfix/` | plan packet container | Root-cause plan packet for live data pipeline failure: forecasts DB authority reads, source-run attribution, coverage/readiness ownership, live daemon wiring, and end-to-end proof |
| `task_2026-05-15_data_pipeline_live_rootfix/DATA_PIPELINE_ROOTFIX_PLAN.md` | topology planning packet | Empirical live-only rootfix plan superseding the 2026-05-14 plan-only approval until live end-to-end proof exists |
| `task_2026-05-15_live_order_e2e_verification/` | plan packet container | Live order end-to-end verification packet for forecast-live data, live reader, evaluator, executor, venue command journal, order state, and reconciliation proof |
| `task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md` | topology planning packet | Execution plan requiring deployed-code proof, real live limit-order submission, durable command/event evidence, and post-order guard before claiming live readiness |
| `task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_CRITIC_APPROVAL.md` | packet review evidence | Critic REVISE-to-APPROVE record for the live order E2E verification plan; approval is plan-only, not empirical live completion evidence |
| `task_2026-05-15_live_order_e2e_verification/receipt.json` | packet receipt | Current-state freeze receipt naming the live order E2E plan as the active execution packet; not implementation closeout evidence |
| `task_2026-05-15_live_order_e2e_goal/` | plan packet container | Main-based plan packet for real live order end-to-end proof: live data, evaluator intent, executor submit, venue order identity, command/order/fill/position records, and guard closeout |
| `task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md` | topology planning packet | Detailed execution plan for completing the user `/goal` on a new branch from main; explicitly excludes shadow-only proof and rejected/unknown order outcomes as completion |
| `task_2026-05-15_live_order_e2e_goal/CRITIC_APPROVAL.md` | packet review evidence | Critic REVISE-to-APPROVE record for the live-order E2E goal plan; approval is plan/phase-gate only, not completion evidence |
| `task_2026-05-15_live_order_e2e_goal/receipt.json` | packet receipt | Active packet freeze receipt; explicitly records that live-order E2E is not complete yet |
| `task_2026-05-16_live_continuous_run_package/` | plan packet container | Follow-up package for proving stable continuous live operation after the first real live order milestone |
| `task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md` | topology planning packet | Plan and completion definition for `LIVE_CONTINUOUS_READY` vs `CONTROLLED_DEGRADED`, including code-plane, launchd, source-health, DB-lock, and live acceptance gates |
| `task_2026-05-16_live_continuous_run_package/CRITIC_REVIEW.md` | packet review evidence | Critic attack and approval record for the live continuous-run package plan; not empirical live-ready evidence |
| `task_2026-05-17_live_order_survival/` | plan packet container | Live order survival packet for heartbeat lease ownership, submit ownership projection, REVIEW_REQUIRED clearance, and sustained order progress proof |
| `task_2026-05-17_live_order_survival/LIVE_ORDER_SURVIVAL_PLAN.md` | topology planning packet | Plan and completion definition for proving submitted orders survive heartbeat/recovery boundaries through fill, venue terminality, or explicit no-exposure clearance |
| `LIVE_LAUNCH_HANDOFF.md` | Live launch handoff document for daemon deployment |
| `UNMATCHED_GAMMA_CITIES_2026_05_07.md` | Unmatched gamma cities investigation evidence (2026-05-07) |
| `CLOUD_EXTRACT_PATCH_2026_05_07.md` | Cloud extract patch evidence (2026-05-07) |
| `PLIST_UPDATE_FOR_RELOCK.md` | Plist update evidence for re-lock operation |
| `LIVE_RESTART_2026_05_07.md` | Live daemon restart evidence (2026-05-07) |
| `INDEX.md` | Directory index for docs/operations live surfaces |
| `live_rescue_ledger_2026-05-04.md` | Live rescue ledger evidence (2026-05-04) |
| `POLICY.md` | Operations policy document |
Archived packet evidence (physically moved to `docs/operations/archive/<YYYY>-Q<N>/`) is
listed in `docs/operations/archive/<YYYY>-Q<N>/INDEX.md`; do not re-list those packets here.
When a packet closes and is archived, create a stub at `docs/operations/<name>.archived`
and add a row to the quarter INDEX. Active archival rules: see `docs/authority/ARCHIVAL_RULES.md`.

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
