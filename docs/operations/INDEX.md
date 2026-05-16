# `docs/operations/` — Authoritative Index

**Purpose:** every directory and top-level file under `docs/operations/`
must be registered here.  If it isn't on this page, it's a candidate
for archival — see `POLICY.md` for the closeout rule.

**Last reviewed:** 2026-05-16.

---

## Permanent observation surfaces (long-lived, no merge tied)

These directories collect rolling operational data and are not
expected to close out.  Keep them under their existing names.

| Surface | Owner | Purpose |
|---|---|---|
| `activation/` | runtime | Activation evidence captures (rollout gating). |
| `attribution_drift/` | runtime | Drift attribution snapshots per cycle. |
| `calibration_observation/` | runtime | Live calibration sample log. |
| `edge_observation/` | runtime | Edge-decision rejection counters per stage. |
| `learning_loop_observation/` | runtime | Feedback loop instrumentation outputs. |
| `ws_poll_reaction/` | runtime | Websocket-vs-poll reaction-time evidence. |

## Permanent reference docs (top-level .md, semi-stable)

| File | Status | Purpose |
|---|---|---|
| `AGENTS.md` | active | Scoped agent rules for `docs/operations/`. |
| `current_state.md` | active | Live operational state checkpoint. |
| `current_data_state.md` | active | Data-side state checkpoint. |
| `current_source_validity.md` | active | Source-validity status (what's hot/cold). |
| `packet_scope_protocol.md` | active | Packet scope conventions. |
| `tigge_daemon_integration.md` | active | TIGGE daemon wiring reference. |
| `live_rescue_ledger_2026-05-04.md` | **review** | Per-day live-rescue log; needs rotation policy if this grows. |

## Task directories — closeout status

A task directory captures a finite operation with a known closeout
trigger (PR merge or operator declaration).  After closeout, the
directory should EITHER be moved under `archive/` OR keep a
`STATUS.md` with the closeout receipt — see `POLICY.md`.

| Directory | Anchor PR / commit | Status | Last touched |
|---|---|---|---|
| `task_2026-04-26_ultimate_plan/` | (multi-commit) `2cb1c421` | **closed** — pre-PR-37 ingest plan, completed | 2026-04 |
| `task_2026-05-02_live_entry_data_contract/` | (planning artifacts) `2cb1c421` | **review** — multiple PLAN_v[1,2,3] suggest abandoned drafts | 2026-05-02 |
| `task_2026-05-03_ddd_implementation_plan/` | DDD v2 commits `650136bd` etc. | **closed** — DDD v2 redesign live-wired, 38 docs is excessive — needs INDEX of its own or pruning | 2026-05-04 |
| `task_2026-05-05_topology_noise_repair/` | PR #67 | **closed** — topology boot-profile and script-route noise repair, completed | 2026-05-05 |
| `task_2026-05-05_object_invariance_mainline/` | PR #67 | **closed** — object-meaning invariance mainline closeout ledger | 2026-05-05 |
| `task_2026-05-05_object_invariance_wave5/` through `wave8/`, `wave11/`–`wave21/` | PR #67 | **closed** — object-meaning invariance remediation waves 5-21 | 2026-05-05 |
| `task_2026-05-06_calibration_quality_blockers/` | PR #80 | **closed** — calibration quality launch-blocker (12 quarantined Platts + fit-time guard) | 2026-05-06 |
| `task_2026-05-06_hook_redesign/` | superseded | **closed** — hook redesign v1; superseded by v2 | 2026-05-06 |
| `task_2026-05-06_topology_redesign/` | superseded | **closed** — topology redesign v1; superseded by v2 | 2026-05-06 |
| `task_2026-05-07_hook_redesign_v2/` | PR #77 | **active** — hook ecosystem v2; `.claude/settings.json` BLOCKING tier is authoritative | 2026-05-07 |
| `task_2026-05-07_navigation_topology_v2/` | PR #79 | **closed** — topology v2 navigation upgrade, merged | 2026-05-07 |
| `task_2026-05-07_recalibration_after_low_high_alignment/` | PR #82 | **closed** — LOW/HIGH recalibration recovery, merged | 2026-05-07 |
| `task_2026-05-07_object_invariance_wave24/`–`wave26/` | PR #67 | **closed** — object-meaning invariance waves 24-26 | 2026-05-07 |
| `task_2026-05-08_deep_alignment_audit/` | pre-PR #88 | **closed** — deep alignment audit, findings drove repair | 2026-05-08 |
| `task_2026-05-08_alignment_safe_implementation/` | PR #88 | **closed** — alignment repair implementation, merged | 2026-05-08 |
| `task_2026-05-08_alignment_repair_workflow/` | workflow record | **closed** — alignment repair session record | 2026-05-08 |
| `task_2026-05-08_100_blocked_horizon_audit/` | diagnostic | **closed** — blocked-horizon audit evidence | 2026-05-08 |
| `task_2026-05-08_262_london_f_to_c/` | PR #89 | **closed** — London °F→°C settlement semantics fix, merged | 2026-05-08 |
| `task_2026-05-08_ecmwf_publication_strategy/` | evidence packet | **closed** — ECMWF publication timing strategy | 2026-05-08 |
| `task_2026-05-08_ecmwf_step_grid_scientist_eval/` | evidence packet | **closed** — ECMWF step-grid evaluation | 2026-05-08 |
| `task_2026-05-08_f1_subprocess_hardening/` | PR #90 | **closed** — F1 subprocess hardening, merged | 2026-05-08 |
| `task_2026-05-08_low_recalibration_residue_pr/` | PR #91 | **closed** — LOW recalibration residue, merged | 2026-05-08 |
| `task_2026-05-08_obs_outside_bin_audit/` | evidence packet | **closed** — observations-outside-bin audit | 2026-05-08 |
| `task_2026-05-08_phase_b_download_root_cause/` | evidence packet | **closed** — Phase B download root-cause dossier | 2026-05-08 |
| `task_2026-05-08_post_merge_full_chain/` | post-merge checks | **closed** — full-chain verification post-merge | 2026-05-08 |
| `task_2026-05-08_topology_redesign_completion/` | PR #92 | **closed** — topology redesign completion, merged | 2026-05-08 |
| `task_2026-05-08_track_a6_run/` | evidence packet | **closed** — Track A.6 run evidence | 2026-05-08 |
| `task_2026-05-08_object_invariance_wave27/`–`wave42/` | PR #67 | **closed** — object-meaning invariance waves 27-42 | 2026-05-08 |
| `task_2026-05-08_object_invariance_remaining_mainline/` | PR #67 | **closed** — remaining mainline invariance closeout | 2026-05-08 |
| `task_2026-05-09_copilot_agent_sync/` | planning packet | **active** — Copilot/VS Code agent sync planning; open for implementation | 2026-05-09 |
| `task_2026-05-09_daemon_restart_and_backfill/` | operations run | **closed** — daemon restart and ECMWF backfill, completed | 2026-05-09 |
| `task_2026-05-09_post_s4_residuals_topology/` | evidence packet | **closed** — post-S4 topology residuals audit | 2026-05-09 |
| `task_2026-05-09_pr_workflow_failure/` | incident record | **closed** — PR workflow failure, resolved | 2026-05-09 |
| `task_2026-05-09_workflow_redesign_plan/` | PR #94 | **closed** — PR discipline and workflow redesign, merged | 2026-05-09 |
| `task_2026-05-11_ecmwf_download_replacement/` | PR #105 | **closed** — ECMWF download replacement, merged | 2026-05-11 |
| `task_2026-05-11_tigge_vm_to_zeus_db/` | PR #106 | **closed** — TIGGE VM-to-Zeus DB wiring, merged | 2026-05-11 |
| `task_2026-05-14_attach_path_index_fix/` | PR #114 | **closed** — K1 attach-path index fix, merged | 2026-05-14 |
| `task_2026-05-14_data_daemon_live_efficiency/` | PR #115 | **closed** — data-daemon live-efficiency refactor, merged | 2026-05-14 |
| `task_2026-05-14_k1_followups/` | PR #116 | **closed** — K1 followup seam repairs, merged | 2026-05-14 |
| `task_2026-05-15_data_pipeline_live_rootfix/` | PR #117 | **closed** — live data pipeline root-cause fix, merged | 2026-05-15 |
| `task_2026-05-15_live_order_e2e_goal/` | branch: feat/live-order-e2e | **active** — live order E2E proof: data→evaluator→executor→venue record | 2026-05-15 |
| `task_2026-05-15_live_order_e2e_verification/` | evidence packet | **active** — E2E verification plan and critic approval | 2026-05-15 |
| `task_2026-05-15_p_drift_remediation/` | PR #119 | **closed** — probability drift remediation, merged | 2026-05-15 |
| `task_2026-05-15_p1_topology_v_next_additive/` | PR #119 | **closed** — topology v-next additive phase 1, merged | 2026-05-15 |
| `task_2026-05-15_p2_companion_required_mechanism/` | PR #119 | **closed** — companion-required mechanism, merged | 2026-05-15 |
| `task_2026-05-15_p3_topology_v_next_phase2_shadow/` | PR #119 | **closed** — topology v-next phase 2 shadow mode, merged | 2026-05-15 |
| `task_2026-05-15_p5_maintenance_worker_core/` | PR #119 | **closed** — maintenance worker core scaffold, merged | 2026-05-15 |
| `task_2026-05-15_p8_authority_drift_3_blocking/` | PR #119 | **closed** — authority drift blocking fixes, merged | 2026-05-15 |
| `task_2026-05-15_p9_authority_inventory_v2/` | PR #119 | **closed** — authority inventory v2, merged | 2026-05-15 |
| `task_2026-05-15_p10_module_consolidation_planning/` | planning packet | **active** — module consolidation planning; open for implementation | 2026-05-15 |
| `task_2026-05-15_runtime_improvement_engineering_package/` | PR #119 | **active** — runtime improvement engineering package; ACTIVE_LAW | 2026-05-15 |
| `task_2026-05-15_autonomous_agent_runtime_audit/` | audit packet | **active** — autonomous agent runtime audit; IN_PROGRESS | 2026-05-15 |
| `task_2026-05-15_claude_md_drift_audit/` | audit packet | **closed** — CLAUDE.md drift audit, findings applied | 2026-05-15 |
| `task_2026-05-16_doc_alignment_plan/` | this PR (feat/doc-alignment-2026-05-16) | **active** — post-PR-#119 authority doc + semantic drift refresh (大扫除) | 2026-05-16 |

## Active operation (only one allowed unless explicit branching)

If you are starting a new operation, add a row here BEFORE creating the
directory (see POLICY.md §3).  When the operation closes, move the row
to the table above and either archive or annotate its dir with
`STATUS.md`.

| Operation | Started | Trigger | Anchor PR / branch |
|---|---|---|---|
| `task_2026-05-16_doc_alignment_plan/` | 2026-05-16 | post-PR-#119 authority refresh + 大扫除 | feat/doc-alignment-2026-05-16 |

## Archived

These items have been moved to `docs/operations/archive/` per `POLICY.md`.

| Item | Status | Purpose |
|---|---|---|
| `observations_k1_migration.md` | archived | K1 schema migration was one-shot. |
| `pr39_source_contract_artifact_audit.md` | archived | PR #39 audit; PR is merged. |
| `PROPOSALS_2026-05-04.md` | archived | Operational improvement proposals. |
| `task_2026-04-28_contamination_remediation/` | archived | contamination pass, completed |
| `task_2026-04-29_design_simplification_audit/` | archived | design audit, completed |
| `task_2026-05-01_tigge_5_01_backfill/` | archived | TIGGE 5.01 backfill, completed |
| `task_2026-05-01_ultrareview25_remediation/` | archived | ultrareview-25 remediation, completed |
| `task_2026-05-02_data_daemon_readiness/` | archived | data daemon readiness substrate, completed |
| `task_2026-05-02_full_launch_audit/` | archived | full launch audit, multi-PR completed |
| `task_2026-05-02_oracle_lifecycle/` | archived | oracle resilience review fixes, completed |
| `task_2026-05-02_review_crash_remediation/` | archived | live entry guards + ENS snapshot, merged |
| `task_2026-05-02_settlement_pipeline_audit/` | archived | settlement audit, completed |
| `task_2026-05-02_strategy_update_execution_plan/` | archived | promotion-evidence guard, completed |
| `task_2026-05-04_live_block_root_cause/` | archived | live-block antibodies + EntriesBlockRegistry, merged |
| `task_2026-05-04_oracle_kelly_evidence_rebuild/` | archived | oracle/kelly evidence rebuild, merged 2026-05-04 |
| `task_2026-05-04_strategy_redesign_day0_endgame/` | archived | Day0-as-endgame strategy redesign, merged |
| `task_2026-05-04_tigge_ingest_resilience/` | archived | TIGGE 12z resilience, merged 2026-05-04 |
| `task_2026-05-04_zeus_may3_review_remediation/` | archived | empty, archived for naming-collision avoidance |

## Cross-references (artifacts that live outside `docs/operations/`)

| File | Purpose |
|---|---|
| `architecture/improvement_backlog.yaml` | Typed registry for capsule-emitted improvement insights (P3 V1).  Capsule writes here instead of leaving lessons in chat history. |
| `scripts/check_pr_identity_collisions.py` + `.github/workflows/pr_identity_collision_check.yml` | Pre-merge identity-collision detection (P1).  Posts an advisory comment when two open PRs both add a class with the same name in identity-bearing files. |
| `src/state/schema_introspection.py` | `has_columns()` helper (P2) — call this from any code path that depends on a column added by a staged migration. |

## Triage backlog (one-time cleanup, 2026-05-04)

Items above marked **review** or **archive candidate** need an
operator decision in the next housekeeping pass:

1. `task_2026-05-02_live_entry_data_contract/` — multiple PLAN_v[1,2,3]
   files suggest abandoned drafts; either anchor to a merged PR or
   delete.
2. `task_2026-05-03_ddd_implementation_plan/` — 38 docs in one task
   dir is a smell; needs an internal INDEX or thinning.
3. `live_rescue_ledger_2026-05-04.md` — needs a per-day rotation
   policy or it'll keep growing.
