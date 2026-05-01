# Pre-merge scope: claude/mystifying-varahamihira-3d3733

Generated: 2026-04-28
Base branch: plan-pre5 @ 8a433f6
Merge base: 874e00cc0244
Candidate head: e3b2844

## Unique commits
7b2d73e Open packet — backtest first-principles review (planning only)
99e0b96 Slice S1 GREEN — backtest purpose contracts + decision-time provenance
5ab1468 Slice S2+S4 GREEN — skill orchestrator + economics tombstone
6a93d18 Open F11 packet — forecasts issue_time hindsight antibody (planning only)
14d87ae Slice F11.1 GREEN — per-source forecast dissemination schedule registry
3ece859 Update 04_corrections — U1-U7 verification status (2026-04-28)
cdb19bb U4 RESOLVED — Polymarket subgraph stores events only, no snapshots
5b1b05d F11.2-F11.5 GREEN — schema migration + writer + backfill + eligibility (code only)
5bd9be8 F11 apply runbook + Q5 WU obs triage packet plan (planning only)
fef0c8a F11 evidence — forecasts table consumer audit for F11.5-migrate scope
1e0c197 U1 RESOLVED — crosscheck_members: 31 = NOAA GEFS via Open-Meteo ensemble API
7b46003 F11 review-fix slice — BLOCKERs + MAJORs from critic + code-reviewer
57fdc81 F11.6 GREEN — wire SKILL_ELIGIBLE_SQL into replay forecasts read
8dbe7c2 Merge origin/plan-pre5 into F11 backtest packet
9088e60 F11 canonical apply complete — evidence + runbook update
e3b2844 F11.5-migrate GREEN — wire SKILL_ELIGIBLE_SQL into ETL training paths

## Diff stat (triple-dot)
 .claude/agents/critic-opus.md                      |   68 +
 .claude/agents/safety-gate.md                      |  104 +
 .claude/agents/verifier.md                         |   65 +
 .claude/hooks/pre-commit-invariant-test.sh         |  106 +
 .claude/hooks/pre-edit-architecture.sh             |   60 +
 .claude/settings.json                              |   27 +
 .claude/skills/zeus-phase-discipline/SKILL.md      |   47 +
 AGENTS.md                                          |   16 +-
 architecture/AGENTS.md                             |    4 +-
 architecture/code_review_graph_protocol.yaml       |   18 +-
 architecture/docs_registry.yaml                    |   64 +-
 architecture/fatal_misreads.yaml                   |   17 +-
 architecture/invariants.yaml                       |  100 +-
 architecture/map_maintenance.yaml                  |    4 +-
 architecture/module_manifest.yaml                  |  170 +-
 architecture/naming_conventions.yaml               |    2 +
 architecture/script_manifest.yaml                  |   65 +-
 architecture/source_rationale.yaml                 |  393 ++-
 architecture/test_topology.yaml                    |  153 +-
 architecture/topology.yaml                         | 2493 +++++++++++++++++++-
 config/AGENTS.md                                   |    2 +
 config/risk_caps.yaml                              |   17 +
 docs/AGENTS.md                                     |    2 +
 docs/README.md                                     |    2 +
 .../adversarial_debate_for_project_evaluation.md   |  687 ++++++
 docs/operations/AGENTS.md                          |    4 +-
 docs/operations/current_state.md                   |   14 +
 .../AGENTS.md                                      |    3 +-
 .../down/r1l1_external_grounding_2026-04-26.md     |   53 +
 .../open_questions.md                              |   35 +-
 .../plan.md                                        |   42 +-
 .../polymarket_live_money_contract.md              |   25 +
 .../receipt.json                                   |  264 +++
 .../v2_system_impact_report.md                     |  402 +---
 .../work_log.md                                    |  114 +
 .../RETROSPECTIVE_2026-04-26.md                    |   86 +
 .../task_2026-04-26_ultimate_plan/ULTIMATE_PLAN.md |  356 +++
 .../cross_region_questions.md                      |   36 +
 .../dependency_graph.mmd                           |   86 +
 .../evidence/apr26_findings_routing.yaml           |  366 +++
 .../evidence/down/_context_boot_opponent.md        |  265 +++
 .../evidence/down/_context_boot_proponent.md       |  211 ++
 .../evidence/down/converged_R1L1.md                |   89 +
 .../evidence/down/converged_R2L2.md                |  215 ++
 .../evidence/down/converged_R3L3.md                |  452 ++++
 ...q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md |   72 +
 .../evidence/mid/_context_boot_opponent.md         |  169 ++
 .../evidence/mid/_context_boot_proponent.md        |  195 ++
 .../evidence/mid/converged_R1L1.md                 |  116 +
 .../evidence/mid/converged_R2L2.md                 |  149 ++
 .../evidence/mid/converged_R3L3.md                 |  453 ++++
 .../multi_review/MULTI_REVIEW_SYNTHESIS.md         |  123 +
 .../evidence/multi_review/V2_1_STRUCTURAL_DIFF.md  |  449 ++++
 .../evidence/multi_review/architect_report.md      |   40 +
 .../multi_review/citation_verification_report.md   |  248 ++
 .../evidence/multi_review/critic_report.md         |  107 +
 .../evidence/multi_review/feasibility_report.md    |  124 +
 .../multi_review/trading_correctness_report.md     |   61 +
 .../evidence/up/_context_boot_opponent.md          |   98 +
 .../evidence/up/_context_boot_proponent.md         |  145 ++
 .../evidence/up/_layer1_resolved.md                |  101 +
 .../evidence/up/converged_R2L2.md                  |   80 +
 .../evidence/up/converged_R3L3.md                  |  252 ++
 .../evidence/xcut/MASTER_VERDICT.md                |   88 +
 .../evidence/xcut/X1.md                            |   55 +
 .../evidence/xcut/X2.md                            |   83 +
 .../evidence/xcut/X3.md                            |   92 +
 .../evidence/xcut/X4.md                            |  118 +
 .../task_2026-04-26_ultimate_plan/judge_ledger.md  |  103 +
 .../r3/CONFUSION_CHECKPOINTS.md                    |  349 +++
 .../r3/IMPLEMENTATION_PROTOCOL.md                  |  465 ++++
 .../r3/INVARIANTS_LEDGER.md                        |  101 +
 .../task_2026-04-26_ultimate_plan/r3/R3_README.md  |  271 +++
 .../r3/SELF_LEARNING_PROTOCOL.md                   |  273 +++
 .../r3/SKILLS_MATRIX.md                            |  211 ++
 .../r3/ULTIMATE_PLAN_R3.md                         |  302 +++
 .../M3_gap_threshold_and_auth_2026-04-27.md        |   30 +
 ..._schedule_exit_and_cancel_grammar_2026-04-27.md |   30 +
 ...dings_vs_trade_fact_write_surface_2026-04-27.md |   25 +
 .../r3/_confusion/README.md                        |   65 +
 .../U2_schema_ddl_vs_acceptance_2026-04-27.md      |   27 +
 .../r3/_phase_at_gate_edge_M1.md                   |   41 +
 .../r3/_phase_status.yaml                          |  291 +++
 .../r3/_protocol_evolution/README.md               |   62 +
 .../z0_docs_architecture_path.md                   |   25 +
 .../r3/boot/A1_codex_2026-04-27.md                 |   31 +
 .../r3/boot/A2_codex_2026-04-27.md                 |   28 +
 .../r3/boot/F1_codex_2026-04-27.md                 |   38 +
 .../r3/boot/F2_codex_2026-04-27.md                 |   29 +
 .../r3/boot/F3_codex_2026-04-27.md                 |   34 +
 .../r3/boot/G1_codex_2026-04-27.md                 |   49 +
 .../r3/boot/M1_INV29_codex_2026-04-27.md           |   23 +
 .../r3/boot/M1_codex_2026-04-27.md                 |   16 +
 .../r3/boot/M2_codex_2026-04-27.md                 |   29 +
 .../r3/boot/M3_codex_2026-04-27.md                 |   48 +
 .../r3/boot/M4_codex_2026-04-27.md                 |   32 +
 .../r3/boot/M5_codex_2026-04-27.md                 |   33 +
 .../r3/boot/R1_codex_2026-04-27.md                 |   33 +
 .../r3/boot/T1_codex_2026-04-27.md                 |   31 +
 .../r3/boot/U1_codex_2026-04-27.md                 |   38 +
 .../r3/boot/U2_codex_2026-04-27.md                 |   11 +
 .../r3/boot/Z0_codex_2026-04-27.md                 |   40 +
 .../r3/boot/Z1_codex_2026-04-27.md                 |   86 +
 .../r3/boot/Z2_codex_2026-04-27.md                 |   61 +
 .../r3/boot/Z3_codex_2026-04-27.md                 |   21 +
 .../r3/boot/Z4_codex_2026-04-27.md                 |   15 +
 .../r3/dependency_graph_r3.mmd                     |  151 ++
 .../r3/drift_reports/2026-04-26.md                 |   12 +
 .../r3/drift_reports/2026-04-27.md                 |   31 +
 .../r3/drift_reports/2026-04-28.md                 |   31 +
 .../r3/evidence/A1_work_record_2026-04-27.md       |   98 +
 .../r3/evidence/A2_work_record_2026-04-27.md       |  132 ++
 .../r3/evidence/F1_work_record_2026-04-27.md       |   48 +
 .../r3/evidence/F2_work_record_2026-04-27.md       |   50 +
 .../r3/evidence/F3_work_record_2026-04-27.md       |   52 +
 .../r3/evidence/G1_work_record_2026-04-27.md       |  244 ++
 .../r3/evidence/M1_INV29_work_record_2026-04-27.md |   13 +
 .../r3/evidence/M1_work_record_2026-04-27.md       |   38 +
 .../r3/evidence/M2_work_record_2026-04-27.md       |   55 +
 .../r3/evidence/M3_work_record_2026-04-27.md       |  102 +
 .../r3/evidence/M4_work_record_2026-04-27.md       |   95 +
 .../r3/evidence/M5_work_record_2026-04-27.md       |   94 +
 .../r3/evidence/R1_work_record_2026-04-27.md       |   78 +
 .../r3/evidence/T1_work_record_2026-04-27.md       |   97 +
 .../r3/evidence/U2_work_record_2026-04-27.md       |   42 +
 .../evidence/full_suite_blocker_plan_2026-04-27.md |  105 +
 .../r3/frozen_interfaces/A1.md                     |   50 +
 .../r3/frozen_interfaces/A2.md                     |   58 +
 .../r3/frozen_interfaces/G1.md                     |   26 +
 .../r3/frozen_interfaces/M4.md                     |   43 +
 .../r3/frozen_interfaces/M5.md                     |   44 +
 .../r3/frozen_interfaces/R1.md                     |   56 +
 .../r3/frozen_interfaces/T1.md                     |   43 +
 .../r3/frozen_interfaces/U1.md                     |   57 +
 .../r3/frozen_interfaces/U2.md                     |   62 +
 .../r3/frozen_interfaces/Z2.md                     |   38 +
 .../r3/frozen_interfaces/Z3.md                     |   21 +
 .../r3/frozen_interfaces/Z4.md                     |   47 +
 .../r3/learnings/M4_codex_2026-04-27_retro.md      |   19 +
 .../r3/learnings/M5_codex_2026-04-27_retro.md      |   19 +
 .../r3/learnings/R1_codex_2026-04-27_retro.md      |   15 +
 .../r3/learnings/README.md                         |   31 +
 .../U2_codex_2026-04-27_topology_profile.md        |   21 +
 .../r3/learnings/Z0_codex_2026-04-27_retro.md      |   54 +
 .../Z0_docs_architecture_route_2026-04-27.md       |   21 +
 .../r3/learnings/Z1_codex_2026-04-27_retro.md      |   75 +
 .../r3/learnings/Z2_codex_2026-04-27_retro.md      |   87 +
 .../r3/operator_decisions/INDEX.md                 |   58 +
 .../inv_29_amendment_2026-04-27.md                 |   33 +
 .../polymarket_user_ws_2026-04-27.md               |   18 +
 .../py_clob_client_v2_surface_2026-04-27.md        |   57 +
 .../r3/reviews/A1_post_close_2026-04-27.md         |   63 +
 .../r3/reviews/A1_pre_close_2026-04-27.md          |   49 +
 .../r3/reviews/A2_post_close_2026-04-27.md         |   55 +
 .../r3/reviews/A2_pre_close_2026-04-27.md          |   49 +
 .../r3/reviews/F1_post_close_2026-04-27.md         |   42 +
 .../r3/reviews/F1_pre_close_2026-04-27.md          |   39 +
 .../r3/reviews/F2_post_close_2026-04-27.md         |   35 +
 .../r3/reviews/F2_pre_close_2026-04-27.md          |   31 +
 .../r3/reviews/F3_post_close_2026-04-27.md         |   39 +
 .../r3/reviews/F3_pre_close_2026-04-27.md          |   41 +
 .../r3/reviews/G1_pre_close_2026-04-27.md          |   57 +
 .../r3/reviews/M1_inv29_post_close_2026-04-27.md   |   28 +
 .../r3/reviews/M1_inv29_pre_close_2026-04-27.md    |   28 +
 .../r3/reviews/M1_pre_close_2026-04-27.md          |   39 +
 .../r3/reviews/M2_post_close_2026-04-27.md         |   42 +
 .../r3/reviews/M2_pre_close_2026-04-27.md          |   34 +
 .../r3/reviews/M3_post_close_2026-04-27.md         |   58 +
 .../r3/reviews/M3_pre_close_2026-04-27.md          |   48 +
 .../r3/reviews/M4_post_close_2026-04-27.md         |   71 +
 .../r3/reviews/M4_pre_close_2026-04-27.md          |   67 +
 .../r3/reviews/M5_post_close_2026-04-27.md         |   76 +
 .../r3/reviews/M5_pre_close_2026-04-27.md          |   75 +
 .../r3/reviews/R1_post_close_2026-04-27.md         |   70 +
 .../r3/reviews/R1_pre_close_2026-04-27.md          |   72 +
 .../r3/reviews/T1_post_close_2026-04-27.md         |   72 +
 .../r3/reviews/T1_pre_close_2026-04-27.md          |   70 +
 .../r3/reviews/U1_post_close_2026-04-27.md         |   49 +
 .../r3/reviews/U2_post_close_2026-04-27.md         |   40 +
 .../r3/reviews/U2_pre_close_2026-04-27.md          |   35 +
 .../r3/scripts/aggregate_r3_cards.py               |  248 ++
 .../r3/scripts/r3_drift_check.py                   |  303 +++
 .../r3/slice_cards/A1.yaml                         |   90 +
 .../r3/slice_cards/A2.yaml                         |   80 +
 .../r3/slice_cards/F1.yaml                         |  123 +
 .../r3/slice_cards/F2.yaml                         |  104 +
 .../r3/slice_cards/F3.yaml                         |  120 +
 .../r3/slice_cards/G1.yaml                         |   88 +
 .../r3/slice_cards/M1.yaml                         |   85 +
 .../r3/slice_cards/M2.yaml                         |   57 +
 .../r3/slice_cards/M3.yaml                         |   71 +
 .../r3/slice_cards/M4.yaml                         |   77 +
 .../r3/slice_cards/M5.yaml                         |   87 +
 .../r3/slice_cards/R1.yaml                         |   90 +
 .../r3/slice_cards/T1.yaml                         |   74 +
 .../r3/slice_cards/U1.yaml                         |  145 ++
 .../r3/slice_cards/U2.yaml                         |  173 ++
 .../r3/slice_cards/Z0.yaml                         |   81 +
 .../r3/slice_cards/Z1.yaml                         |   88 +
 .../r3/slice_cards/Z2.yaml                         |  141 ++
 .../r3/slice_cards/Z3.yaml                         |   92 +
 .../r3/slice_cards/Z4.yaml                         |  148 ++
 .../r3/slice_summary_r3.md                         |   45 +
 .../r3/templates/fresh_start_prompt.md             |  300 +++
 .../r3/templates/phase_prompt_template.md          |  257 ++
 .../task_2026-04-26_ultimate_plan/receipt.json     |  323 +++
 .../scripts/aggregate_slice_cards.py               |  232 ++
 .../side_channel_fixes.md                          |   14 +
 .../slice_cards/down-01.yaml                       |   61 +
 .../slice_cards/down-02.yaml                       |   42 +
 .../slice_cards/down-03.yaml                       |   53 +
 .../slice_cards/down-04.yaml                       |   28 +
 .../slice_cards/down-05.yaml                       |   43 +
 .../slice_cards/down-06.yaml                       |   64 +
 .../slice_cards/down-07.yaml                       |  100 +
 .../slice_cards/mid-01.yaml                        |   49 +
 .../slice_cards/mid-02.yaml                        |   72 +
 .../slice_cards/mid-03.yaml                        |   64 +
 .../slice_cards/mid-04.yaml                        |   52 +
 .../slice_cards/mid-05.yaml                        |   95 +
 .../slice_cards/mid-06.yaml                        |   73 +
 .../slice_cards/mid-07.yaml                        |   42 +
 .../slice_cards/mid-08.yaml                        |   52 +
 .../slice_cards/up-01.yaml                         |   33 +
 .../slice_cards/up-02.yaml                         |   32 +
 .../slice_cards/up-03.yaml                         |   28 +
 .../slice_cards/up-04.yaml                         |   60 +
 .../slice_cards/up-05.yaml                         |   32 +
 .../slice_cards/up-06.yaml                         |   33 +
 .../slice_cards/up-07.yaml                         |   30 +
 .../slice_cards/up-08.yaml                         |   47 +
 .../task_2026-04-26_ultimate_plan/slice_summary.md |   43 +
 .../01_backtest_upgrade_design.md                  |  348 +++
 .../02_blocker_handling_plan.md                    |  330 +++
 .../03_data_layer_issues.md                        |  317 +++
 .../04_corrections_2026-04-27.md                   |  258 ++
 .../evidence/reality_calibration.md                |  198 ++
 .../evidence/vm_probe_2026-04-27.md                |  157 ++
 .../plan.md                                        |  102 +
 .../task_2026-04-27_harness_debate/DEEP_PLAN.md    |  325 +++
 .../task_2026-04-27_harness_debate/TOPIC.md        |   92 +
 .../evidence/critic-harness/_boot_critic.md        |  159 ++
 .../critic-harness/batch_A_review_2026-04-28.md    |  150 ++
 .../critic-harness/batch_B_review_2026-04-28.md    |  226 ++
 .../critic-harness/batch_C_review_2026-04-28.md    |  249 ++
 .../critic-harness/batch_D_review_2026-04-28.md    |  299 +++
 .../evidence/executor/_boot_executor.md            |  135 ++
 .../evidence/opponent/R1_opening.md                |  214 ++
 .../evidence/opponent/R2_rebuttal.md               |  264 +++
 .../evidence/opponent/_boot_opponent.md            |   92 +
 .../evidence/opponent/round2_critique.md           |  314 +++
 .../evidence/opponent/round2_proposal.md           |  575 +++++
 .../evidence/opponent/round3_critique.md           |  226 ++
 .../evidence/opponent/round3_proposal.md           |  223 ++
 .../evidence/proponent/R1_opening.md               |  134 ++
 .../evidence/proponent/R2_rebuttal.md              |  210 ++
 .../evidence/proponent/_boot_proponent.md          |  130 +
 .../evidence/proponent/round2_critique.md          |  311 +++
 .../evidence/proponent/round2_proposal.md          |  278 +++
 .../evidence/proponent/round3_critique.md          |  247 ++
 .../evidence/proponent/round3_proposal.md          |  211 ++
 .../task_2026-04-27_harness_debate/judge_ledger.md |  280 +++
 .../round2_verdict.md                              |  272 +++
 .../round3_verdict.md                              |  236 ++
 .../task_2026-04-27_harness_debate/verdict.md      |  253 ++
 .../apply_runbook.md                               |  245 ++
 .../evidence/canonical_apply_2026-04-28.md         |  148 ++
 .../forecasts_consumer_audit_2026-04-28.md         |   85 +
 .../plan.md                                        |  217 ++
 .../plan.md                                        |  231 ++
 docs/reference/AGENTS.md                           |   12 +-
 docs/reference/modules/AGENTS.md                   |    2 +
 docs/reference/modules/calibration.md              |    2 +
 docs/reference/modules/contracts.md                |   11 +-
 docs/reference/modules/control.md                  |   13 +
 docs/reference/modules/data.md                     |   18 +-
 docs/reference/modules/engine.md                   |   14 +-
 docs/reference/modules/execution.md                |   36 +-
 docs/reference/modules/ingest.md                   |   36 +
 docs/reference/modules/riskguard.md                |   37 +
 docs/reference/modules/signal.md                   |    4 +
 docs/reference/modules/state.md                    |   20 +-
 docs/reference/modules/strategy.md                 |    7 +
 docs/reference/modules/venue.md                    |   88 +
 requirements.txt                                   |    2 +-
 scripts/backfill_forecast_issue_time.py            |  239 ++
 scripts/backfill_openmeteo_previous_runs.py        |  125 +-
 scripts/etl_forecast_skill_from_forecasts.py       |   13 +-
 scripts/etl_historical_forecasts.py                |   10 +-
 scripts/live_readiness_check.py                    |  446 ++++
 .../migrate_forecasts_availability_provenance.py   |  132 ++
 scripts/post_sequential_fillback.sh                |   16 +-
 scripts/r3_drift_check.py                          |  136 ++
 scripts/rebuild_settlements.py                     |  168 ++
 scripts/resume_backfills_sequential.sh             |   12 +-
 scripts/semantic_linter.py                         |   12 +-
 scripts/validate_assumptions.py                    |    6 +-
 src/AGENTS.md                                      |    2 +-
 src/backtest/__init__.py                           |    0
 src/backtest/decision_time_truth.py                |   93 +
 src/backtest/economics.py                          |   25 +
 src/backtest/purpose.py                            |  122 +
 src/backtest/skill.py                              |   96 +
 src/backtest/training_eligibility.py               |   81 +
 src/calibration/AGENTS.md                          |    3 +
 src/calibration/retrain_trigger.py                 |  507 ++++
 src/contracts/AGENTS.md                            |    3 +
 src/contracts/__init__.py                          |   11 +
 src/contracts/executable_market_snapshot_v2.py     |  251 ++
 src/contracts/execution_intent.py                  |   20 +
 src/contracts/fx_classification.py                 |   60 +
 src/contracts/settlement_semantics.py              |  107 +-
 src/contracts/venue_submission_envelope.py         |  153 ++
 src/control/AGENTS.md                              |    3 +
 src/control/control_plane.py                       |   15 +
 src/control/cutover_guard.py                       |  382 +++
 src/control/heartbeat_supervisor.py                |  262 ++
 src/control/ws_gap_guard.py                        |  223 ++
 src/data/AGENTS.md                                 |    5 +-
 src/data/daily_obs_append.py                       |    5 +-
 src/data/dissemination_schedules.py                |  145 ++
 src/data/ensemble_client.py                        |  145 +-
 src/data/forecast_ingest_protocol.py               |   82 +
 src/data/forecast_source_registry.py               |  251 ++
 src/data/forecasts_append.py                       |   64 +-
 src/data/hole_scanner.py                           |    9 +-
 src/data/polymarket_client.py                      |  282 ++-
 src/data/tigge_client.py                           |  229 ++
 src/engine/cycle_runner.py                         |  262 +-
 src/engine/cycle_runtime.py                        |    9 +
 src/engine/evaluator.py                            |    8 +-
 src/engine/replay.py                               |   10 +
 src/execution/AGENTS.md                            |    6 +-
 src/execution/collateral.py                        |   16 +-
 src/execution/command_bus.py                       |   21 +-
 src/execution/command_recovery.py                  |  186 +-
 src/execution/exchange_reconcile.py                |  696 ++++++
 src/execution/executor.py                          |  775 +++++-
 src/execution/exit_lifecycle.py                    |  147 +-
 src/execution/exit_safety.py                       |  489 ++++
 src/execution/harvester.py                         |   39 +-
 src/execution/settlement_commands.py               |  601 +++++
 src/execution/wrap_unwrap_commands.py              |  232 ++
 src/ingest/AGENTS.md                               |   32 +
 src/ingest/polymarket_user_channel.py              |  528 +++++
 src/main.py                                        |  116 +-
 src/risk_allocator/AGENTS.md                       |   25 +
 src/risk_allocator/__init__.py                     |   50 +
 src/risk_allocator/governor.py                     |  739 ++++++
 src/signal/day0_signal.py                          |    6 +-
 src/signal/ensemble_signal.py                      |    5 +-
 src/state/AGENTS.md                                |    4 +
 src/state/collateral_ledger.py                     |  640 +++++
 src/state/db.py                                    |  360 ++-
 src/state/portfolio.py                             |   13 +-
 src/state/snapshot_repo.py                         |  259 ++
 src/state/venue_command_repo.py                    |  950 +++++++-
 src/strategy/__init__.py                           |   26 +
 src/strategy/benchmark_suite.py                    |  459 ++++
 src/strategy/candidates/__init__.py                |   59 +
 .../candidates/cross_market_correlation_hedge.py   |   11 +
 .../liquidity_provision_with_heartbeat.py          |   11 +
 src/strategy/candidates/neg_risk_basket.py         |   11 +
 src/strategy/candidates/resolution_window_maker.py |   11 +
 src/strategy/candidates/stale_quote_detector.py    |   11 +
 src/strategy/candidates/weather_event_arbitrage.py |   11 +
 src/strategy/data_lake.py                          |   72 +
 src/strategy/market_analysis.py                    |    6 +-
 src/venue/AGENTS.md                                |   37 +
 src/venue/__init__.py                              |    0
 src/venue/polymarket_v2_adapter.py                 |  874 +++++++
 state/assumptions.json                             |   17 +-
 tests/AGENTS.md                                    |    2 +
 tests/conftest.py                                  |   78 +
 tests/fakes/__init__.py                            |    1 +
 tests/fakes/polymarket_v2.py                       |  401 ++++
 tests/integration/test_p0_live_money_safety.py     |  347 +++
 tests/test_architecture_contracts.py               |    1 +
 tests/test_auto_pause_entries.py                   |   20 +-
 tests/test_backfill_openmeteo_previous_runs.py     |   14 +
 tests/test_backtest_outcome_comparison.py          |   21 +-
 tests/test_backtest_purpose_contract.py            |  158 ++
 tests/test_backtest_skill_economics.py             |   94 +
 tests/test_backtest_training_eligibility.py        |  138 ++
 tests/test_calibration_retrain.py                  |  413 ++++
 tests/test_center_buy_repair.py                    |    8 +-
 tests/test_collateral_ledger.py                    |  768 ++++++
 tests/test_command_bus_types.py                    |  186 +-
 tests/test_command_grammar_amendment.py            |  224 ++
 tests/test_command_recovery.py                     |  124 +-
 tests/test_config.py                               |    3 +
 tests/test_cutover_guard.py                        |  427 ++++
 tests/test_day0_runtime_observation_context.py     |    2 +
 tests/test_day0_window.py                          |   41 +-
 tests/test_digest_profile_matching.py              |  341 ++-
 tests/test_discovery_idempotency.py                |   27 +-
 tests/test_dissemination_schedules.py              |  202 ++
 tests/test_divergence_exit_counterfactual.py       |    9 +-
 tests/test_dual_track_law_stubs.py                 |   59 +-
 tests/test_etl_skill_eligibility_filter.py         |  139 ++
 tests/test_exchange_reconcile.py                   |  665 ++++++
 tests/test_executable_market_snapshot_v2.py        |  389 +++
 tests/test_executor.py                             |   88 +-
 tests/test_executor_command_split.py               |  346 ++-
 tests/test_executor_db_target.py                   |   95 +-
 tests/test_executor_typed_boundary.py              |   11 +-
 tests/test_exit_safety.py                          |  645 +++++
 tests/test_fake_polymarket_venue.py                |  128 +
 tests/test_forecast_source_registry.py             |  346 +++
 tests/test_forecasts_schema_alignment.py           |   28 +-
 tests/test_forecasts_writer_provenance_required.py |  182 ++
 tests/test_harvester_dr33_live_enablement.py       |    2 +-
 tests/test_healthcheck.py                          |   34 +-
 tests/test_heartbeat_supervisor.py                 |  217 ++
 tests/test_instrument_invariants.py                |    4 +
 tests/test_k2_slice_e.py                           |   46 +-
 tests/test_k2_slice_f.py                           |    4 +-
 tests/test_k2_slice_g.py                           |   11 +-
 tests/test_k3_5_fix_pack.py                        |   11 +-
 tests/test_k7_slice_o.py                           |    7 +-
 tests/test_k8_slice_r.py                           |    4 +
 tests/test_live_execution.py                       |   80 +-
 tests/test_live_readiness_gates.py                 |  191 ++
 tests/test_live_safety_invariants.py               |   25 +-
 tests/test_market_analysis.py                      |    6 +-
 tests/test_neg_risk_passthrough.py                 |  168 +-
 tests/test_no_bare_float_seams.py                  |    2 +-
 tests/test_observation_atom.py                     |   41 +-
 tests/test_p0_hardening.py                         |   93 +-
 tests/test_pe_reconstruction_relationships.py      |    2 +
 tests/test_phase10c_dt_seam_followup.py            |    3 +-
 tests/test_phase4_ingest.py                        |    2 +
 tests/test_phase5_fixpack.py                       |    5 +-
 tests/test_pnl_flow_and_audit.py                   |   93 +-
 tests/test_polymarket_error_matrix.py              |  155 +-
 tests/test_pre_live_integration.py                 |    3 +-
 tests/test_provenance_5_projections.py             |  743 ++++++
 tests/test_replay_skill_eligibility_filter.py      |  126 +
 tests/test_risk_allocator.py                       |  635 +++++
 tests/test_riskguard.py                            |    2 +-
 tests/test_riskguard_red_durable_cmd.py            |  190 ++
 tests/test_runtime_guards.py                       |    5 +-
 tests/test_settlement_commands.py                  |  231 ++
 tests/test_settlement_semantics.py                 |  100 +
 tests/test_strategy_benchmark.py                   |  213 ++
 tests/test_tigge_ingest.py                         |  202 ++
 tests/test_unknown_side_effect.py                  |  538 +++++
 tests/test_user_channel_ingest.py                  |  442 ++++
 tests/test_v2_adapter.py                           |  563 +++++
 tests/test_venue_command_repo.py                   |  145 +-
 tests/test_z0_plan_lock.py                         |   98 +
 workspace_map.md                                   |    8 +-
 452 files changed, 58004 insertions(+), 1129 deletions(-)

## Name status (triple-dot)
A	.claude/agents/critic-opus.md
A	.claude/agents/safety-gate.md
A	.claude/agents/verifier.md
A	.claude/hooks/pre-commit-invariant-test.sh
A	.claude/hooks/pre-edit-architecture.sh
A	.claude/settings.json
A	.claude/skills/zeus-phase-discipline/SKILL.md
M	AGENTS.md
M	architecture/AGENTS.md
M	architecture/code_review_graph_protocol.yaml
M	architecture/docs_registry.yaml
M	architecture/fatal_misreads.yaml
M	architecture/invariants.yaml
M	architecture/map_maintenance.yaml
M	architecture/module_manifest.yaml
M	architecture/naming_conventions.yaml
M	architecture/script_manifest.yaml
M	architecture/source_rationale.yaml
M	architecture/test_topology.yaml
M	architecture/topology.yaml
M	config/AGENTS.md
A	config/risk_caps.yaml
M	docs/AGENTS.md
M	docs/README.md
A	docs/methodology/adversarial_debate_for_project_evaluation.md
M	docs/operations/AGENTS.md
M	docs/operations/current_state.md
M	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/AGENTS.md
A	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/down/r1l1_external_grounding_2026-04-26.md
M	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/open_questions.md
M	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/plan.md
A	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
A	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/receipt.json
M	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/v2_system_impact_report.md
M	docs/operations/task_2026-04-26_polymarket_clob_v2_migration/work_log.md
A	docs/operations/task_2026-04-26_ultimate_plan/RETROSPECTIVE_2026-04-26.md
A	docs/operations/task_2026-04-26_ultimate_plan/ULTIMATE_PLAN.md
A	docs/operations/task_2026-04-26_ultimate_plan/cross_region_questions.md
A	docs/operations/task_2026-04-26_ultimate_plan/dependency_graph.mmd
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/apr26_findings_routing.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/down/_context_boot_opponent.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/down/_context_boot_proponent.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/down/converged_R1L1.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/down/converged_R2L2.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/down/converged_R3L3.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/down/q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/mid/_context_boot_opponent.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/mid/_context_boot_proponent.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/mid/converged_R1L1.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/mid/converged_R2L2.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/mid/converged_R3L3.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/MULTI_REVIEW_SYNTHESIS.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/V2_1_STRUCTURAL_DIFF.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/architect_report.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/citation_verification_report.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/critic_report.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/feasibility_report.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/multi_review/trading_correctness_report.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/up/_context_boot_opponent.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/up/_context_boot_proponent.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/up/_layer1_resolved.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/up/converged_R2L2.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/up/converged_R3L3.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/xcut/MASTER_VERDICT.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/xcut/X1.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/xcut/X2.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/xcut/X3.md
A	docs/operations/task_2026-04-26_ultimate_plan/evidence/xcut/X4.md
A	docs/operations/task_2026-04-26_ultimate_plan/judge_ledger.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/CONFUSION_CHECKPOINTS.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/IMPLEMENTATION_PROTOCOL.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/INVARIANTS_LEDGER.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/R3_README.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/SELF_LEARNING_PROTOCOL.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/SKILLS_MATRIX.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/M3_gap_threshold_and_auth_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/M4_schedule_exit_and_cancel_grammar_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/M5_findings_vs_trade_fact_write_surface_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/README.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/U2_schema_ddl_vs_acceptance_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_at_gate_edge_M1.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_protocol_evolution/README.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/_protocol_evolution/z0_docs_architecture_path.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/A1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/A2_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/F1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/F2_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/F3_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/G1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M1_INV29_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M2_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M3_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M4_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M5_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/R1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/T1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/U1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/U2_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/Z0_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/Z1_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/Z2_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/Z3_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/boot/Z4_codex_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/dependency_graph_r3.mmd
A	docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-26.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-28.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/A1_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/A2_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/F1_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/F2_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/F3_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/G1_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M1_INV29_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M1_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M2_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M3_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M4_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M5_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/R1_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/T1_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/U2_work_record_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/full_suite_blocker_plan_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/A1.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/A2.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/G1.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/M4.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/M5.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/R1.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/T1.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/U1.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/U2.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/Z2.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/Z3.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/Z4.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/M4_codex_2026-04-27_retro.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/M5_codex_2026-04-27_retro.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/R1_codex_2026-04-27_retro.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/README.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/U2_codex_2026-04-27_topology_profile.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/Z0_codex_2026-04-27_retro.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/Z0_docs_architecture_route_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/Z1_codex_2026-04-27_retro.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/Z2_codex_2026-04-27_retro.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/INDEX.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/inv_29_amendment_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reference_excerpts/polymarket_user_ws_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reference_excerpts/py_clob_client_v2_surface_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/A1_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/A1_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/A2_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/A2_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F1_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F1_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F2_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F2_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F3_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F3_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/G1_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M1_inv29_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M1_inv29_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M1_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M2_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M2_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M3_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M3_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M4_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M4_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M5_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M5_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/R1_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/R1_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/T1_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/T1_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/U1_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/U2_post_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/U2_pre_close_2026-04-27.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/aggregate_r3_cards.py
A	docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A2.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F2.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F3.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M2.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M3.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M4.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M5.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/R1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U2.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z0.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z1.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z3.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z4.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/r3/slice_summary_r3.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/templates/fresh_start_prompt.md
A	docs/operations/task_2026-04-26_ultimate_plan/r3/templates/phase_prompt_template.md
A	docs/operations/task_2026-04-26_ultimate_plan/receipt.json
A	docs/operations/task_2026-04-26_ultimate_plan/scripts/aggregate_slice_cards.py
A	docs/operations/task_2026-04-26_ultimate_plan/side_channel_fixes.md
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-01.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-02.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-03.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-04.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-05.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-06.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/down-07.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-01.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-02.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-03.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-04.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-05.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-06.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-07.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/mid-08.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-01.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-02.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-03.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-04.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-05.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-06.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-07.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-08.yaml
A	docs/operations/task_2026-04-26_ultimate_plan/slice_summary.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/02_blocker_handling_plan.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/03_data_layer_issues.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/04_corrections_2026-04-27.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/evidence/reality_calibration.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/evidence/vm_probe_2026-04-27.md
A	docs/operations/task_2026-04-27_backtest_first_principles_review/plan.md
A	docs/operations/task_2026-04-27_harness_debate/DEEP_PLAN.md
A	docs/operations/task_2026-04-27_harness_debate/TOPIC.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/critic-harness/_boot_critic.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/critic-harness/batch_A_review_2026-04-28.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/critic-harness/batch_B_review_2026-04-28.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/critic-harness/batch_C_review_2026-04-28.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/critic-harness/batch_D_review_2026-04-28.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/executor/_boot_executor.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/R1_opening.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/R2_rebuttal.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/_boot_opponent.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/round2_critique.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/round2_proposal.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/round3_critique.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/opponent/round3_proposal.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/R1_opening.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/R2_rebuttal.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/_boot_proponent.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/round2_critique.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/round2_proposal.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/round3_critique.md
A	docs/operations/task_2026-04-27_harness_debate/evidence/proponent/round3_proposal.md
A	docs/operations/task_2026-04-27_harness_debate/judge_ledger.md
A	docs/operations/task_2026-04-27_harness_debate/round2_verdict.md
A	docs/operations/task_2026-04-27_harness_debate/round3_verdict.md
A	docs/operations/task_2026-04-27_harness_debate/verdict.md
A	docs/operations/task_2026-04-28_f11_forecast_issue_time/apply_runbook.md
A	docs/operations/task_2026-04-28_f11_forecast_issue_time/evidence/canonical_apply_2026-04-28.md
A	docs/operations/task_2026-04-28_f11_forecast_issue_time/evidence/forecasts_consumer_audit_2026-04-28.md
A	docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md
A	docs/operations/task_2026-04-28_wu_observations_empty_provenance_triage/plan.md
M	docs/reference/AGENTS.md
M	docs/reference/modules/AGENTS.md
M	docs/reference/modules/calibration.md
M	docs/reference/modules/contracts.md
M	docs/reference/modules/control.md
M	docs/reference/modules/data.md
M	docs/reference/modules/engine.md
M	docs/reference/modules/execution.md
A	docs/reference/modules/ingest.md
M	docs/reference/modules/riskguard.md
M	docs/reference/modules/signal.md
M	docs/reference/modules/state.md
M	docs/reference/modules/strategy.md
A	docs/reference/modules/venue.md
M	requirements.txt
A	scripts/backfill_forecast_issue_time.py
M	scripts/backfill_openmeteo_previous_runs.py
M	scripts/etl_forecast_skill_from_forecasts.py
M	scripts/etl_historical_forecasts.py
A	scripts/live_readiness_check.py
A	scripts/migrate_forecasts_availability_provenance.py
M	scripts/post_sequential_fillback.sh
A	scripts/r3_drift_check.py
A	scripts/rebuild_settlements.py
M	scripts/resume_backfills_sequential.sh
M	scripts/semantic_linter.py
M	scripts/validate_assumptions.py
M	src/AGENTS.md
A	src/backtest/__init__.py
A	src/backtest/decision_time_truth.py
A	src/backtest/economics.py
A	src/backtest/purpose.py
A	src/backtest/skill.py
A	src/backtest/training_eligibility.py
M	src/calibration/AGENTS.md
A	src/calibration/retrain_trigger.py
M	src/contracts/AGENTS.md
M	src/contracts/__init__.py
A	src/contracts/executable_market_snapshot_v2.py
M	src/contracts/execution_intent.py
A	src/contracts/fx_classification.py
M	src/contracts/settlement_semantics.py
A	src/contracts/venue_submission_envelope.py
M	src/control/AGENTS.md
M	src/control/control_plane.py
A	src/control/cutover_guard.py
A	src/control/heartbeat_supervisor.py
A	src/control/ws_gap_guard.py
M	src/data/AGENTS.md
M	src/data/daily_obs_append.py
A	src/data/dissemination_schedules.py
M	src/data/ensemble_client.py
A	src/data/forecast_ingest_protocol.py
A	src/data/forecast_source_registry.py
M	src/data/forecasts_append.py
M	src/data/hole_scanner.py
M	src/data/polymarket_client.py
A	src/data/tigge_client.py
M	src/engine/cycle_runner.py
M	src/engine/cycle_runtime.py
M	src/engine/evaluator.py
M	src/engine/replay.py
M	src/execution/AGENTS.md
M	src/execution/collateral.py
M	src/execution/command_bus.py
M	src/execution/command_recovery.py
A	src/execution/exchange_reconcile.py
M	src/execution/executor.py
M	src/execution/exit_lifecycle.py
A	src/execution/exit_safety.py
M	src/execution/harvester.py
A	src/execution/settlement_commands.py
A	src/execution/wrap_unwrap_commands.py
A	src/ingest/AGENTS.md
A	src/ingest/polymarket_user_channel.py
M	src/main.py
A	src/risk_allocator/AGENTS.md
A	src/risk_allocator/__init__.py
A	src/risk_allocator/governor.py
M	src/signal/day0_signal.py
M	src/signal/ensemble_signal.py
M	src/state/AGENTS.md
A	src/state/collateral_ledger.py
M	src/state/db.py
M	src/state/portfolio.py
A	src/state/snapshot_repo.py
M	src/state/venue_command_repo.py
M	src/strategy/__init__.py
A	src/strategy/benchmark_suite.py
A	src/strategy/candidates/__init__.py
A	src/strategy/candidates/cross_market_correlation_hedge.py
A	src/strategy/candidates/liquidity_provision_with_heartbeat.py
A	src/strategy/candidates/neg_risk_basket.py
A	src/strategy/candidates/resolution_window_maker.py
A	src/strategy/candidates/stale_quote_detector.py
A	src/strategy/candidates/weather_event_arbitrage.py
A	src/strategy/data_lake.py
M	src/strategy/market_analysis.py
A	src/venue/AGENTS.md
A	src/venue/__init__.py
A	src/venue/polymarket_v2_adapter.py
M	state/assumptions.json
M	tests/AGENTS.md
A	tests/conftest.py
A	tests/fakes/__init__.py
A	tests/fakes/polymarket_v2.py
A	tests/integration/test_p0_live_money_safety.py
M	tests/test_architecture_contracts.py
M	tests/test_auto_pause_entries.py
M	tests/test_backfill_openmeteo_previous_runs.py
M	tests/test_backtest_outcome_comparison.py
A	tests/test_backtest_purpose_contract.py
A	tests/test_backtest_skill_economics.py
A	tests/test_backtest_training_eligibility.py
A	tests/test_calibration_retrain.py
M	tests/test_center_buy_repair.py
A	tests/test_collateral_ledger.py
M	tests/test_command_bus_types.py
A	tests/test_command_grammar_amendment.py
M	tests/test_command_recovery.py
M	tests/test_config.py
A	tests/test_cutover_guard.py
M	tests/test_day0_runtime_observation_context.py
M	tests/test_day0_window.py
M	tests/test_digest_profile_matching.py
M	tests/test_discovery_idempotency.py
A	tests/test_dissemination_schedules.py
M	tests/test_divergence_exit_counterfactual.py
M	tests/test_dual_track_law_stubs.py
A	tests/test_etl_skill_eligibility_filter.py
A	tests/test_exchange_reconcile.py
A	tests/test_executable_market_snapshot_v2.py
M	tests/test_executor.py
M	tests/test_executor_command_split.py
M	tests/test_executor_db_target.py
M	tests/test_executor_typed_boundary.py
A	tests/test_exit_safety.py
A	tests/test_fake_polymarket_venue.py
A	tests/test_forecast_source_registry.py
M	tests/test_forecasts_schema_alignment.py
A	tests/test_forecasts_writer_provenance_required.py
M	tests/test_harvester_dr33_live_enablement.py
M	tests/test_healthcheck.py
A	tests/test_heartbeat_supervisor.py
M	tests/test_instrument_invariants.py
M	tests/test_k2_slice_e.py
M	tests/test_k2_slice_f.py
M	tests/test_k2_slice_g.py
M	tests/test_k3_5_fix_pack.py
M	tests/test_k7_slice_o.py
M	tests/test_k8_slice_r.py
M	tests/test_live_execution.py
A	tests/test_live_readiness_gates.py
M	tests/test_live_safety_invariants.py
M	tests/test_market_analysis.py
M	tests/test_neg_risk_passthrough.py
M	tests/test_no_bare_float_seams.py
M	tests/test_observation_atom.py
M	tests/test_p0_hardening.py
M	tests/test_pe_reconstruction_relationships.py
M	tests/test_phase10c_dt_seam_followup.py
M	tests/test_phase4_ingest.py
M	tests/test_phase5_fixpack.py
M	tests/test_pnl_flow_and_audit.py
M	tests/test_polymarket_error_matrix.py
M	tests/test_pre_live_integration.py
A	tests/test_provenance_5_projections.py
A	tests/test_replay_skill_eligibility_filter.py
A	tests/test_risk_allocator.py
M	tests/test_riskguard.py
A	tests/test_riskguard_red_durable_cmd.py
M	tests/test_runtime_guards.py
A	tests/test_settlement_commands.py
A	tests/test_settlement_semantics.py
A	tests/test_strategy_benchmark.py
A	tests/test_tigge_ingest.py
A	tests/test_unknown_side_effect.py
A	tests/test_user_channel_ingest.py
A	tests/test_v2_adapter.py
M	tests/test_venue_command_repo.py
A	tests/test_z0_plan_lock.py
M	workspace_map.md

## Drift keyword grep in diff
9:+description: Adversarial code/spec/plan reviewer for Zeus. Runs 10 explicit attack patterns to surface drift, omissions, and rubber-stamp risks. Invoke for: PR pre-merge gate, spec/plan adversarial check, post-implementation regression hunt, post-debate verdict critique. Never self-validates "narrow scope" or "pattern proven" without test citation.
26:+1. **Citation rot**: every cited file:line — does it still resolve at HEAD? Run `git rev-parse HEAD` then grep each citation. Symbol-anchor where possible. Dispatch report: GREEN/YELLOW/RED count.
29:+4. **Authority direction**: does the change respect the canonical truth direction? DB > derived JSON > reports. Live > backtest > shadow. Chain > Chronicler > Portfolio. Any reversal is an INV-17 / INV-18 violation regardless of how the local change reads.
32:+7. **Mode mismatch**: live vs paper vs shadow vs backtest — does any new code path leak between modes? `ZEUS_MODE` honored? Live-only code wrapped? Per INV-29 (no live-bypass via paper-mode shortcut).
33:+8. **Type-encodable category errors**: any new "if station == X" / "if unit == 'C'" / "if city == 'Hong Kong'" branch — could this be a TypeError instead? Per Fitz "make the category impossible" (settlement_semantics.py SettlementRoundingPolicy is the canonical pattern).
35:+10. **Rollback path**: if this lands and breaks production tomorrow, what is the revert? Single commit revert clean? Or has it touched 5 modules and inverted 3 schema columns?
89:+You are safety-gate. You run BEFORE risky work, not after. Your job is procedural: refuse the work if the plan-evidence and registry-update preconditions are not met.
109:+- anything described as canonical truth / lifecycle / governance / control / DB authority
210:+1. **Acceptance criteria reproduction**: take each acceptance criterion from the spec/plan/dispatch. Re-run it. If it's a pytest, run pytest. If it's a CLI, run the CLI. If it's a manual gate, walk the gate. UNVERIFIED if you cannot reproduce; FAILED if it does not pass.
248:+The team-lead or executor passes you: (a) what was claimed done, (b) where the evidence lives (commit hash, work_log, evidence dir). You read the evidence, run the 5 checks, and write the verdict to disk at the path specified (typically `evidence/<role>/verify_<topic>_<date>.md`). SendMessage the team-lead the verdict + path.
313:+# TEST_FILES widened in BATCH C to include the 3 new settlement-semantics
314:+# relationship tests (HKO+WMO type-encoded antibody). Per dispatch OP-FOLLOWUP-1
316:+# test_settlement_semantics). SIDECAR-3 added 3 more negative-half regression
318:+TEST_FILES="tests/test_architecture_contracts.py tests/test_settlement_semantics.py"
465:+- DB-canonical-truth direction (INV-17 spirit) is one-way: DB > derived JSON > reports. Never write the reverse direction.
512: ### High-risk zero-context work
519: | `history_lore.yaml` | Dense historical lore registry: failure modes, wrong moves, antibodies, residual risks, and task routing |
523:+| `module_manifest.yaml` | Machine registry for dense module books, module routers, and module-level law/current-fact/test links; now includes R3 CutoverGuard, venue adapter, user-channel ingest, strategy benchmark, and risk allocator/governor routes |
525: | `task_boot_profiles.yaml` | Question-first semantic boot profiles for source/settlement/hourly/Day0/calibration/docs/graph tasks |
530: | `change_receipt_schema.yaml` | Machine-readable route/change receipt contract for high-risk closeout |
573:+- path: docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
577:+  current_role: R3 Z0 packet-local CLOB V2 live-money invariant summary; evidence, not a new authority plane
587:+  may_live_in_reference: false
617:-  current_role: dense execution module reference for live order placement, exits, and settlement harvest
618:+  current_role: dense execution module reference for live order placement, exits, settlement harvest, and A2 allocation pre-submit gates
628:+  may_live_in_reference: true
636:+  current_role: dense venue module reference for Polymarket V2 adapter boundaries and submission provenance
646:+  may_live_in_reference: true
662:-  current_role: dense riskguard module reference for protective enforcement and risk levels
663:+  current_role: dense riskguard/risk-allocator module reference for protective enforcement, risk levels, allocation caps, and kill switches
673:       Hong Kong is an explicit current caution path. Current truth must route
674:       through fresh audit evidence and HKO-specific proof before changing
675:-      source, settlement, hourly, or monitoring behavior.
676:+      source, settlement, hourly, or monitoring behavior. Per round2_verdict.md
686:+    # rounding-policy mismatch sub-case (HKO_Truncation vs WMO_HalfUp on the
693:+    type_encoded_at: src/contracts/settlement_semantics.py:HKO_Truncation
695:       Only a fresh Hong Kong audit receipt may change this caution status or
696:       route around HKO-specific evidence.
699:+      - tests/test_settlement_semantics.py::test_hko_policy_required_for_hong_kong
700:+      - tests/test_settlement_semantics.py::test_hko_policy_invalid_for_non_hong_kong
701:     task_classes: [source_routing, settlement_semantics, hourly_observation_ingest]
720:     why: Exit execution and final market settlement are distinct lifecycle facts.
775:+    # layers) + tests/test_dual_track_law_stubs.py (settlements_metric_identity
781:     why: These four fields are row identity; without them, high and low truths share the same key space and conflate two distinct settlement facts.
792:+        - tests/test_dual_track_law_stubs.py::test_settlements_metric_identity_requires_non_null_and_unique_per_metric
795:     statement: Forecast rows lacking canonical cycle identity may serve runtime degrade paths but must not enter canonical training.
815:+    # CITATION_REPAIR 2026-04-28: PRUNE_CANDIDATE marker REVERTED per critic-harness BATCH C cross-batch audit. The R1+R2+R3 verdicts based the prose-as-law claim on opponent's grep-of-`tests:`-field-only audit, which missed that 6 relationship tests for this invariant ALREADY EXIST in tests/test_dt1_commit_ordering.py (file docstring: "Relationship tests for DT#1 / INV-17: DB authority writes commit BEFORE derived JSON exports fire"). The schema-citation gap was real; the enforcement gap was imaginary.
817:     statement: DB authority writes (event append + projection fold) must COMMIT before any derived JSON export is updated.
818:     why: On a mid-step crash the DB wins and JSON is rebuilt from projection; a JSON write that races the commit can leave exports ahead of authority, making recovery ambiguous.
852:+        collapse, RESTING/MATCHED/MINED/CONFIRMED in CommandState, live venue
868:+        - EXPIRED
871:+        - REVIEW_REQUIRED
890:+        - EXPIRED
891:+        - REVIEW_REQUIRED
931:+  # Marking source_plan as ARCHIVED to preserve provenance; downstream tooling
956:       - docs/authority/zeus_current_delivery.md
965:+      - tests/test_riskguard_red_durable_cmd.py
968:+      - tests/test_provenance_5_projections.py
969:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
974:     high_risk_files:
984:+      - tests/test_riskguard_red_durable_cmd.py
985:+      - tests/test_risk_allocator.py
986:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1018:       - tests/test_backfill_scripts_match_live_config.py
1024:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1028:     authority_role: live_execution
1029:     high_risk_files:
1032:+      - src/execution/settlement_commands.py
1044:+      - src/execution/settlement_commands.py
1057:       - tests/test_live_safety_invariants.py
1063:+      - tests/test_settlement_commands.py
1064:+      - tests/test_risk_allocator.py
1065:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1074:+    authority_role: live_venue_adapter
1075:+    high_risk_files:
1093:+      - tests/integration/test_p0_live_money_safety.py
1095:+      - tests/test_neg_risk_passthrough.py
1096:+    graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1106:+    high_risk_files:
1121:+    graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1127:       - src/calibration/platt.py
1128:       - src/calibration/manager.py
1129:       - src/calibration/store.py
1130:+      - src/calibration/retrain_trigger.py
1131:       - src/calibration/effective_sample_size.py
1132:+      - src/calibration/retrain_trigger.py
1134:       - src/calibration/platt.py
1135:       - src/calibration/manager.py
1137:       - tests/test_calibration_manager.py
1138:       - tests/test_calibration_bins_canonical.py
1139:       - tests/test_calibration_unification.py
1140:+      - tests/test_calibration_retrain.py
1141:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1160:       - tests/test_live_safety_invariants.py
1161:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1163:+  risk_allocator:
1164:+    path: src/risk_allocator
1165:+    scoped_agents: src/risk_allocator/AGENTS.md
1166:+    module_book: docs/reference/modules/riskguard.md
1171:+    high_risk_files:
1172:+      - src/risk_allocator/governor.py
1176:+      - src/risk_allocator/governor.py
1177:+      - src/risk_allocator/__init__.py
1184:+      - tests/test_risk_allocator.py
1187:+    graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1193:       - src/contracts/settlement_semantics.py
1194:       - src/contracts/calibration_bins.py
1200:       - src/contracts/provenance_registry.py
1203:       - src/contracts/settlement_semantics.py
1204:       - src/contracts/calibration_bins.py
1218:       - tests/test_calibration_bins_canonical.py
1222:+      - tests/test_risk_allocator.py
1223:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1228:     high_risk_files:
1242:       - docs/authority/zeus_current_delivery.md
1250:     graph_appendix_status: LOCAL_GRAPH_QUERY_REQUIRED
1259:+        live_readiness_check.py: R3 G1 readiness gate name fixed by operator-facing live-readiness packet contract; script remains read-only and operator-gated
1260:         live_smoke_test.py: established runtime smoke entrypoint
1279:+  backfill_forecast_issue_time.py: {class: repair, dangerous_if_run: true, apply_flag: "--apply", target_db: state/zeus-world.db, dry_run_default: true, read_targets: [state/zeus-world.db], write_targets: [state/zeus-world.db], authority_scope: repair_write, lifecycle: long_lived, status: active, delete_policy: retain_until_superseded, reuse_when: "Backfill forecast_issue_time + availability_provenance for pre-F11 NULL rows. Idempotent — skips already-populated rows. Requires F11.2 schema migration applied first.", do_not_use_when: "Schema migration not applied yet (script raises). All rows already populated.", canonical_command: "python3 scripts/backfill_forecast_issue_time.py [--db PATH] (--dry-run | --apply --confirm-backup | --verify)", external_inputs: [], promotion_barrier: "--apply requires --confirm-backup affirming verified DB backup exists.", required_helpers: [src.data.dissemination_schedules.derive_availability], required_tests: [tests/test_dissemination_schedules.py, tests/test_forecasts_writer_provenance_required.py], unguarded_write_rationale: "Backfills 23,466 rows in single transaction; --apply + --confirm-backup required; uses F11.1 dissemination registry to derive provenance per source."}
1282:     required_helpers: [src.contracts.settlement_semantics.SettlementSemantics, src.data.daily_observation_writer, scripts.backfill_completeness]
1284:     required_tests: [tests/test_backfill_completeness_guardrails.py, tests/test_backfill_scripts_match_live_config.py]
1285:   backfill_observations_from_settlements.py: {class: etl_writer}
1301:+  live_readiness_check.py:
1305:+    reason: "R3 G1 live-readiness orchestrator; runs 17 explicit PASS/FAIL gates and fails closed when staged-smoke/operator evidence is absent."
1306:+    canonical_command: "python3 scripts/live_readiness_check.py"
1310:+    required_tests: [tests/test_live_readiness_gates.py]
1312:+    promotion_barrier: "This script can report readiness only; it cannot authorize live deployment without the live-money-deploy-go operator gate."
1313:   live_smoke_test.py: {class: runtime_support}
1315:   migrate_cluster_to_city.py: {class: repair, apply_flag: implicit, target_db: state/zeus-world.db, unguarded_write_rationale: "Existing migration mutates calibration tables without an apply flag; manifest marks it dangerous pending hardening."}
1318:   migrate_b071_token_suppression_to_history.py: {class: repair, dangerous_if_run: true, apply_flag: "--apply", target_db: state/zeus.db, dry_run_default: true, read_targets: [state/zeus.db], write_targets: [state/zeus.db], authority_scope: repair_write, lifecycle: long_lived, status: active, delete_policy: retain_until_superseded, reuse_when: "One-time migration from mutable token_suppression upsert to append-only token_suppression_history + view (Phase 10A B071). Run once per DB; idempotent on re-run.", do_not_use_when: "Migration has already been applied to this DB instance (idempotency guard checks table existence).", canonical_command: "python3 scripts/migrate_b071_token_suppression_to_history.py [--db PATH] [--apply]", external_inputs: [], promotion_barrier: "Requires explicit --apply flag; dry-run default shows DDL + row-count changes without writing.", required_helpers: [src.state.db.get_connection], required_tests: [tests/test_phase10a_hygiene.py], unguarded_write_rationale: "Migration applies DDL (CREATE TABLE token_suppression_history + CREATE VIEW token_suppression_current) and copies existing rows; --apply required; dangerous if run on wrong DB path."}
1320:+  migrate_forecasts_availability_provenance.py: {class: repair, dangerous_if_run: true, apply_flag: "--apply", target_db: state/zeus-world.db, dry_run_default: true, read_targets: [state/zeus-world.db], write_targets: [state/zeus-world.db], authority_scope: repair_write, lifecycle: long_lived, status: active, delete_policy: retain_until_superseded, reuse_when: "F11.2: ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT with CHECK constraint enforcing the AvailabilityProvenance enum. Idempotent — skips if column exists.", do_not_use_when: "Column already present (idempotency guard).", canonical_command: "python3 scripts/migrate_forecasts_availability_provenance.py [--db PATH] (--dry-run | --apply | --verify)", external_inputs: [], promotion_barrier: "Requires explicit --apply flag; dry-run default shows the ALTER statement only.", required_helpers: [src.backtest.decision_time_truth.AvailabilityProvenance], required_tests: [tests/test_dissemination_schedules.py, tests/test_forecasts_writer_provenance_required.py], unguarded_write_rationale: "DDL only (ADD COLUMN); reversible because new column defaults NULL and old readers continue to work; --apply required."}
1326:     required_tests: [tests/test_calibration_bins_canonical.py, tests/test_replay_time_provenance.py]
1327:   rebuild_calibration_pairs_v2.py: {class: repair, dangerous_if_run: true, apply_flag: "--force", target_db: state/zeus-world.db, unguarded_write_rationale: "Phase 7A metric-aware v2 rebuild; safety gates enforced via --dry-run default + --no-dry-run + --force requirement."}
1328:+  rebuild_settlements.py:
1334:+    write_targets: [settlements]
1337:+    required_helpers: [src.contracts.settlement_semantics.SettlementSemantics]
1339:+    reason: "Narrow high-track repair helper: rebuild settlements only from observations with authority='VERIFIED'; dry-run by default."
1344:+    lifecycle: long_lived
1348:+    do_not_use_when: "Never use as proof of live-money authorization; it validates repository drift only."
1362:   rebuild_strategy_tracker_current_regime.py: {class: repair, apply_flag: implicit, target_db: state/strategy_tracker-live.json, unguarded_write_rationale: "Existing tracker rebuild writes tracker/history files without an apply flag; manifest marks it dangerous pending hardening."}
1373:-  ingest/daily_obs_tick.py: {class: runtime_support, canonical_command: "python3 scripts/ingest/daily_obs_tick.py", reason: "Standalone WU+HKO+Ogimet daily-obs tick — mirrors src/main.py::_k2_daily_obs_tick.", required_tests: [tests/test_ingest_isolation.py]}
1387:+  SOURCE_TRUTH_PLANE_COLLAPSE: Source roles must not collapse settlement, Day0, hourly, and forecast truth planes.
1389:   ORACLE_TRUNCATION_BIAS: HKO/Oracle truncation differences must stay explicit and may not be silently mixed with WMO half-up.
1408:     authority_role: risk_policy_runtime
1410:     - pytest -q tests/test_riskguard.py tests/test_live_safety_invariants.py
1411:+  src/risk_allocator:
1415:+    - pytest -q -p no:cacheprovider tests/test_risk_allocator.py
1418:     authority_role: live_execution_boundary
1445:+      decision-time truth provenance. Does NOT carry promotion authority.
1453:     - src/calibration/effective_sample_size.py
1454:     - src/calibration/manager.py
1455:     - src/calibration/platt.py
1456:+    - src/calibration/retrain_trigger.py
1457:     - src/calibration/store.py
1458:     - src/contracts/calibration_bins.py
1473:+    why: Separates intent semantics from order mechanics; R3 A2 adds typed allocation metadata so per-event, per-resolution-window, and correlated-exposure caps are live-path data, not test-only dynamic attributes.
1476:+    - src/risk_allocator/governor.py
1502:+    why: Frozen provenance envelope for Polymarket V2 submissions; pins canonical pre-sign/request/response evidence instead of one SDK call shape.
1513:     - src/riskguard/policy.py
1517:+    why: Runtime state machine that blocks live venue side effects unless the CLOB V2 cutover is explicitly LIVE_ENABLED.
1528:+    why: R3 Z3 venue heartbeat supervisor that blocks resting live orders when heartbeat health is not HEALTHY and reuses the fail-closed auto-pause tombstone.
1557:+  src/risk_allocator/AGENTS.md:
1560:+    why: R3 A2 risk allocator package rules and file registry.
1561:+  src/risk_allocator/__init__.py:
1568:+  src/risk_allocator/governor.py:
1577:+    - tests/test_risk_allocator.py
1588:+    why: R3 F1 typed protocol for source-stamped forecast bundles; K2 data seam, not K0 settlement/source authority.
1591:+    - future F2 calibration retrain loop
1592:+    - future F3 TIGGE ingest stub
1603:+    - future F2 calibration retrain loop
1604:+    - future F3 TIGGE ingest stub
1608:+    why: R3 F3 TIGGE ingest stub implementing ForecastIngestProtocol while keeping external TIGGE pulls dormant behind operator artifact + env flag.
1613:+    - future F2 calibration retrain loop
1616:     authority_role: live_daily_observation_append
1620:     authority_role: live_forecast_append
1621:-    why: Appends live forecast-history rows and coverage for post-backfill freshness.
1622:+    why: Appends live forecast-history rows and coverage for post-backfill freshness; R3 F1 stamps registry source_id/raw_payload_hash/captured_at/authority_tier on new rows.
1628:+    why: Per-source (ECMWF/GFS/ICON/UKMO/OpenMeteo) deterministic base→available_at function and provenance tier; F11 antibody for forecast_issue_time NULL rows.
1640:+    why: CLOB market-data compatibility wrapper; live placement/cancel/order query delegates to the R3 Z2 PolymarketV2Adapter boundary. R3 A2 threads RiskAllocator-selected order_type through this shim so maker/taker policy reaches the venue adapter.
1659:+    why: Sole Polymarket V2 live placement/cancel/query adapter and shared PolymarketV2AdapterProtocol; converts execution intent plus market snapshot into VenueSubmissionEnvelope before SDK contact and anchors T1 paper/live parity fakes.
1665:+    - tests/integration/test_p0_live_money_safety.py
1692:+    why: Extracted heavy helpers from cycle_runner; supports orchestration without becoming authority. R3 A2 threads candidate event/date/cluster allocation metadata into ExecutionIntent for live cap enforcement.
1703:+    why: R3 Z4 durable command/state model for USDC.e ↔ pUSD wrap/unwrap lifecycle; no live chain submission authority.
1724:     authority_role: live_order_executor
1725:-    why: Places live CLOB limit orders; real-money boundary.
1726:+    why: Places live CLOB limit orders; real-money boundary. R3 U2 persists pre-submit VenueSubmissionEnvelope provenance before SDK contact; R3 A2 consults RiskAllocator/PortfolioGovernor before command persistence or SDK contact and persists/submits the selected maker/taker order type.
1735:+    why: R3 M5 read-only venue-vs-journal reconciliation sweep; writes exchange_reconcile_findings and linkable missing trade facts without inserting venue_commands or performing live venue side effects.
1746:+  src/execution/settlement_commands.py:
1748:+    authority_role: settlement_redeem_command_model
1749:+    why: R3 R1 durable settlement/redeem command ledger; models redemption lifecycle and tx-hash crash recovery without authorizing live redeem side effects.
1753:+    - settlement_write
1768:+    why: R3 M4 mutex, typed cancel-outcome parsing, and replacement-sell gating for live exit safety without widening command grammar or M5 reconciliation.
1783:     why: Defines per-metric calibration identities so high/low families cannot silently share the wrong calibration surface.
1784:+  src/calibration/retrain_trigger.py:
1786:+    authority_role: operator_gated_calibration_retrain_trigger
1787:+    why: R3 F2 operator-gated calibration retrain/promotion wiring; consumes CONFIRMED trade facts only and requires frozen-replay PASS before promotion.
1791:+    - src/calibration/store.py
1792:+    - src/calibration/manager.py
1794:   src/calibration/platt.py:
1814:+    authority_role: decision_time_provenance_contract
1829:+    why: Purpose-typed wrapper around src.engine.replay.run_wu_settlement_sweep enforcing purpose=SKILL contract.
1835:+  src/backtest/training_eligibility.py:
1837:+    authority_role: training_eligibility_filter
1838:+    why: Per-purpose (SKILL/ECONOMICS) eligibility predicates + parameterless SQL fragments for filtering forecasts by availability_provenance (F11.5 antibody).
1845:+    why: R3 A1 replay/paper/live-shadow benchmark metrics and promotion gate; computes evidence-only StrategyMetrics and blocks live promotion unless replay+paper+shadow pass.
1860:+    why: R3 A1 strategy candidate stub registry; advertises identities for benchmarking without executable alpha or live promotion authority.
1881:+  src/strategy/candidates/neg_risk_basket.py:
1924:+    why: Durable venue command/event journal; R3 Z4 terminal command transitions release collateral reservations atomically; R3 U1 requires fresh executable snapshot citation before insert; R3 U2 owns append-only venue_submission_envelopes, order facts, trade facts, position lots, and provenance envelope events.
1937:-    why: SQLite connections, schema setup, and canonical DB queries.
1938:+    why: SQLite connections, schema setup, canonical DB queries, and R3 U2 raw provenance projection schema initialization.
1941:     - settlement_write
1952:+    tests/test_backtest_training_eligibility.py: {created: "2026-04-28", last_used: "2026-04-28"}
1954:+    tests/test_forecasts_writer_provenance_required.py: {created: "2026-04-28", last_used: "2026-04-28"}
1957:     tests/test_calibration_manager_low_fallback_regression.py: {created: "2026-04-26", last_used: "2026-04-26"}
1958:     tests/test_calibration_pairs_v2_metric_linter.py: {created: "2026-04-26", last_used: "2026-04-26"}
1959:     tests/test_calibration_store_metric_required.py: {created: "2026-04-26", last_used: "2026-04-26"}
1960:     tests/test_calibration_v2_fallback_alerting.py: {created: "2026-04-26", last_used: "2026-04-26"}
1961:+    tests/test_calibration_retrain.py: {created: "2026-04-27", last_used: "2026-04-27"}
1976:     tests/test_hk_settlement_floor_rounding.py: {created: "2026-04-26", last_used: "2026-04-26"}
1979:     tests/test_k2_live_ingestion_relationships.py: {created: "2026-04-13", last_used: "2026-04-25"}
1981:     tests/test_kelly_live_safety_cap.py: {created: "2026-04-11", last_used: "2026-04-23"}
1982:+    tests/test_live_execution.py: {created: "2026-04-27", last_used: "2026-04-27"}
1983:     tests/test_live_safe_strategies.py: {created: "2026-04-26", last_used: "2026-04-26"}
1984:     tests/test_live_safety_invariants.py: {created: "2026-03-31", last_used: "2026-04-23"}
1985:     tests/test_market_scanner_provenance.py: {created: "2026-04-17", last_used: "2026-04-17"}
1996:     tests/test_harvester_dr33_live_enablement.py: {created: "2026-04-23", last_used: "2026-04-23"}
1998:-    tests/test_neg_risk_passthrough.py: {created: "2026-04-23", last_used: "2026-04-23"}
1999:+    tests/test_neg_risk_passthrough.py: {created: "2026-04-23", last_used: "2026-04-27"}
2002:     tests/test_replay_time_provenance.py: {created: "2026-04-25", last_used: "2026-04-25"}
2003:     tests/test_settlements_authority_trigger.py: {created: "2026-04-23", last_used: "2026-04-23"}
2004:     tests/test_settlements_verified_row_integrity.py: {created: "2026-04-23", last_used: "2026-04-23"}
2005:+    tests/test_settlement_semantics.py: {created: "2026-04-27", last_used: "2026-04-27"}
2007:     tests/test_vig_treatment_provenance.py: {created: "2026-04-24", last_used: "2026-04-24"}
2015:+    tests/test_provenance_5_projections.py: {created: "2026-04-27", last_used: "2026-04-27"}
2017:+    tests/test_riskguard_red_durable_cmd.py: {created: "2026-04-27", last_used: "2026-04-27"}
2025:+    tests/test_settlement_commands.py: {created: "2026-04-27", last_used: "2026-04-27"}
2028:+    tests/test_risk_allocator.py: {created: "2026-04-27", last_used: "2026-04-27"}
2029:+    tests/test_live_readiness_gates.py: {created: "2026-04-27", last_used: "2026-04-27"}
2040:     - tests/test_backtest_settlement_value_outcome.py
2043:+    - tests/test_backtest_training_eligibility.py
2045:+    - tests/test_forecasts_writer_provenance_required.py
2048:     - tests/test_calibration_bins_canonical.py
2060:+    - tests/test_provenance_5_projections.py
2062:+    - tests/test_riskguard_red_durable_cmd.py
2072:+    - tests/test_settlement_commands.py
2073:+    - tests/test_settlement_semantics.py
2076:+    - tests/test_risk_allocator.py
2077:+    - tests/test_live_readiness_gates.py
2079:+    - tests/test_hk_settlement_floor_rounding.py
2081:+    - tests/test_live_safe_strategies.py
2095:     - tests/test_harvester_dr33_live_enablement.py
2099:     - tests/test_calibration_manager.py
2100:     - tests/test_calibration_unification.py
2101:+    - tests/test_calibration_retrain.py
2143:+      - tests/test_risk_allocator.py
2145:+      - src/risk_allocator/governor.py
2146:+      - src/risk_allocator/__init__.py
2151:+      - tests/test_live_readiness_gates.py
2153:+      - scripts/live_readiness_check.py
2161:+      - tests/test_settlement_semantics.py
2172:+    reason: P0 hardening remains a core relationship antibody; its only skip is an explicitly deferred P2 RED command-emission slice, while P0 keeps the existing local-marking regression guard elsewhere.
2175:+      - "R-5 (RED x command-emission) is a P2 slice; P0 keeps the existing local-marking regression guard elsewhere."
2191:+    reason: R3 CutoverGuard law tests intentionally skip the later exchange wipe classification and full live-money cutover simulation because those are owned by M5/T1 follow-up phases, not the Z1 pre-submit gate.
2195:+      - "T1/G1 live-money integration harness owns full cutover-wipe simulation."
2203:     sunset: Remove once provenance registry dependency is always present in test env.
2223:+    - docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
2240:+  - id: "r3 live readiness gates implementation"
2242:+      - "G1 live readiness gates"
2244:+      - "live readiness"
2245:+      - "live_readiness_check"
2247:+      - "staged-live-smoke"
2249:+      - "live-money-deploy-go"
2252:+        - "G1 live readiness gates"
2254:+        - "live_readiness_check"
2256:+        - "staged-live-smoke"
2258:+        - "live-money-deploy-go"
2262:+        - "live readiness"
2266:+        - "tests/test_live_readiness_gates.py"
2294:+      - "scripts/live_readiness_check.py"
2296:+      - "tests/test_live_readiness_gates.py"
2299:+      - "INV-NEW-S: LIVE deployment requires 17/17 G1 gate PASS plus at least one staged-live-smoke environment passing the same readiness suite."
2300:+      - "G1 may implement and run readiness checks, but it must not place, cancel, redeem, deploy, transition CutoverGuard to LIVE_ENABLED, mutate production DB/state artifacts, activate credentials, or promote live strategies."
2302:+      - "Missing Q1/staged-smoke/operator evidence must fail closed; CI/engineering green must not masquerade as live deploy authorization."
2303:+      - "The live-money-deploy-go operator gate remains blocking even if the readiness script and tests are green."
2324:+      - "scripts/live_readiness_check.py"
2326:+      - "tests/test_live_readiness_gates.py"
2334:+      - "live venue submission"
2335:+      - "live venue cancel/redeem side effects"
2336:+      - "production DB mutation"
2339:+      - "live strategy promotion"
2340:+      - "credentialed live shadow activation"
2342:+      - "python3 -m py_compile scripts/live_readiness_check.py tests/test_live_readiness_gates.py"
2343:+      - "pytest -q -p no:cacheprovider tests/test_live_readiness_gates.py"
2344:+      - "python3 scripts/live_readiness_check.py --help"
2345:+      - "pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat"
2352:+      - "operator live-money-deploy-go gate"
2356:+      - "Stop and plan if G1 requires live submit/cancel/redeem, credential use, production DB/state mutation, or CLOB cutover."
2358:+      - "Stop and plan if a G1 script attempts to execute scripts/live_smoke_test.py or other live side-effect smoke commands automatically."
2379:+      - "tests/test_live_execution.py"
2381:+      - "Heartbeat is mandatory for GTC/GTD live resting orders; missing or unhealthy heartbeat must fail closed before venue submit."
2383:+      - "FOK/FAK immediate-only order types may remain allowed when heartbeat is lost, but all current executor live submits default to resting GTC unless explicitly changed."
2406:+      - "tests/test_live_execution.py"
2415:+      - "pytest -q -p no:cacheprovider tests/test_heartbeat_supervisor.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py"
2467:+      - "tests/test_live_safety_invariants.py"
2469:+      - "tests/test_live_execution.py"
2477:+      - "Q-FX-1 gates pUSD redemption/accounting only; Z4 must not choose the FX classification or implement R1 settlement redemption side effects."
2478:+      - "No live wrap/unwrap chain side effects are authorized by Z4; wrap/unwrap work is durable command/state modeling only unless a later operator gate authorizes submission."
2508:+      - "tests/test_live_safety_invariants.py"
2510:+      - "tests/test_live_execution.py"
2520:+      - "production DB mutation"
2521:+      - "live wrap/unwrap submission"
2522:+      - "R1 settlement redemption side effects"
2524:+      - "pytest -q -p no:cacheprovider tests/test_collateral_ledger.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py"
2537:+      - "Do not implement R1 settlement redemption submission or choose Q-FX-1 classification in Z4."
2538:+      - "Do not mutate production DB/state artifacts; schema changes must stay in source migration/init code and tests."
2570:+      - "tests/test_live_execution.py"
2578:+      - "tests/test_neg_risk_passthrough.py"
2583:+      - "Snapshot gates must fail closed on stale, inactive, closed, orderbook-disabled, token mismatch, tick mismatch, min-order mismatch, or neg-risk mismatch inputs."
2585:+      - "U1 must not execute live cutover, mutate production DB artifacts, or implement U2 raw-provenance projections."
2608:+      - "tests/test_live_execution.py"
2616:+      - "tests/test_neg_risk_passthrough.py"
2624:+      - "production DB mutation"
2628:+      - "pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py"
2640:+      - "Do not implement U2 raw provenance schema or envelope persistence in U1."
2644:+  - id: "r3 raw provenance schema implementation"
2646:+      - "U2 raw provenance schema"
2648:+      - "5-projection raw provenance"
2649:+      - "five projection raw provenance"
2653:+      - "provenance_envelope_events"
2656:+        - "U2 raw provenance schema"
2658:+        - "5-projection raw provenance"
2659:+        - "five projection raw provenance"
2666:+        - "provenance_envelope_events"
2687:+      - "tests/test_provenance_5_projections.py"
2691:+      - "tests/test_live_execution.py"
2699:+      - "tests/test_neg_risk_passthrough.py"
2702:+      - "U2 splits command intent, order facts, trade facts, position lots, and settlement/envelope provenance; do not collapse venue-side states back into CommandState."
2704:+      - "Calibration/retraining consumers must use only venue_trade_facts rows with state='CONFIRMED'; MATCHED/MINED rows are not training truth."
2706:+      - "No production DB/state artifact mutation or live venue side effect is authorized by U2; schema changes stay in source migration/init code and tests."
2726:+      - "tests/test_provenance_5_projections.py"
2730:+      - "tests/test_live_execution.py"
2738:+      - "tests/test_neg_risk_passthrough.py"
2746:+      - "production DB mutation"
2747:+      - "live venue submission"
2751:+      - "R1 settlement redemption side effects"
2753:+      - "pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py"
2756:+      - "pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py"
2766:+      - "future F2 calibration retrain consumers"
2767:+      - "future A2 risk allocator consumers"
2770:+      - "Do not implement websocket ingest, exchange reconciliation sweeps, cancel/replace policy, settlement redemption submission, or risk allocator sizing in U2."
2771:+      - "Stop and plan if settlement provenance requires a first-class settlement_commands table instead of U2's provenance_envelope_events placeholder."
2772:+      - "Stop and plan if existing executor placement cannot supply envelope_id without widening into M2/M3 live-side-effect semantics."
2799:+        - "U2 raw provenance"
2822:+      - "tests/test_riskguard_red_durable_cmd.py"
2827:+      - "tests/test_neg_risk_passthrough.py"
2829:+      - "M1 owns command-side grammar only; order/trade/finality grammars live in U2 facts and must not be collapsed into CommandState."
2830:+      - "RESTING is not a CommandState member; RESTING lives in venue_order_facts.state (NC-NEW-E)."
2853:+      - "tests/test_riskguard_red_durable_cmd.py"
2858:+      - "tests/test_neg_risk_passthrough.py"
2869:+      - "live venue submission"
2872:+      - "pytest -q -p no:cacheprovider tests/test_riskguard_red_durable_cmd.py"
2885:+      - "Stop and plan if RED durable command emission cannot be scoped to cycle_runner._execute_force_exit_sweep."
2910:+        - "live venue submission"
2931:+      - "Closing INV-29 only unblocks M1/M2 sequencing; it does not authorize M2 unknown-side-effect runtime semantics, live venue submission, or any live-money cutover."
2958:+      - "live venue submission"
2970:+      - "Stop and plan if closing INV-29 is used to authorize live submission, M2 runtime semantics, or operator go-live gates."
2998:+        - "U2 raw provenance"
3000:+        - "TIGGE ingest stub"
3001:+        - "calibration retrain"
3030:+      - "tests/test_live_execution.py"
3043:+      - "M2 may add a SAFE_REPLAY_PERMITTED event only with an INV-29 planning-lock amendment; it must not add RESTING/MATCHED/MINED/CONFIRMED to CommandState or perform live venue submission."
3077:+      - "tests/test_live_execution.py"
3096:+      - "production DB mutation"
3097:+      - "live venue submission"
3100:+      - "pytest -q -p no:cacheprovider tests/test_executor_command_split.py tests/test_live_execution.py"
3113:+      - "future A2 risk kill-switch unknown_side_effect_count"
3115:+      - "Stop and plan if M2 requires broad exchange reconciliation sweeps, websocket gap handling, cancel/replace policy, or operator live cutover."
3149:+        - "calibration retrain"
3150:+        - "TIGGE ingest stub"
3193:+      - "MATCHED is optimistic execution truth only; CONFIRMED is final trade fact eligibility for calibration/learning."
3195:+      - "M3 may record an M5 sweep-required marker/status only; it must not implement broad exchange reconciliation, cancel/replace policy, or live cutover."
3244:+      - "production DB mutation"
3245:+      - "live venue submission"
3262:+      - "Stop and plan if implementation starts live WebSocket side effects by default or logs L2 API credentials."
3295:+        - "calibration retrain"
3296:+        - "TIGGE ingest stub"
3331:+      - "Replacement sell is blocked until the prior sell reaches CANCEL_CONFIRMED/CANCELLED, FILLED, EXPIRED, or future M5 proves absence."
3336:+      - "M4 must not implement broad exchange reconciliation, live cutover, or new command-state/event grammar."
3377:+      - "production DB mutation"
3378:+      - "live venue submission"
3383:+      - "pytest -q -p no:cacheprovider tests/test_executor.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py"
3397:+      - "Stop and plan if M4 requires new CommandState/EventType values instead of using existing CANCEL_REQUESTED/CANCEL_ACKED/CANCEL_FAILED/CANCEL_REPLACE_BLOCKED/REVIEW_REQUIRED grammar."
3399:+      - "Stop and plan if implementation requires live venue cancel/submit side effects in tests or default startup."
3441:+        - "TIGGE ingest stub"
3442:+        - "calibration retrain"
3488:+      - "Heartbeat/cutover findings classify evidence only; they do not silently close positions or mutate live venue state."
3489:+      - "M5 must not authorize live venue submission, CLOB cutover, production DB mutation, or redeem settlement."
3534:+      - "live venue submission"
3535:+      - "live venue cancel/replace side effects"
3536:+      - "production DB mutation"
3538:+      - "redeem settlement"
3556:+      - "future G1 live readiness gates"
3561:+      - "Stop and plan if implementation starts live venue submit/cancel/redeem side effects or production DB mutation."
3562:+  - id: "r3 settlement redeem command ledger implementation"
3566:+      - "settlement redeem command ledger"
3567:+      - "settlement_commands"
3568:+      - "REDEEM_TX_HASHED"
3576:+        - "settlement redeem command ledger"
3577:+        - "settlement_commands"
3578:+        - "REDEEM_TX_HASHED"
3584:+        - "settlement command"
3589:+        - "settlement_command_events"
3593:+        - "change settlement rounding"
3594:+        - "calibration retrain"
3595:+        - "TIGGE ingest stub"
3618:+      - "src/execution/settlement_commands.py"
3627:+      - "tests/test_settlement_commands.py"
3631:+      - "REDEEM_TX_HASHED is the crash-recovery anchor; chain receipt reconciliation follows tx_hash to terminal state."
3632:+      - "Q-FX-1 remains operator-gated; when ZEUS_PUSD_FX_CLASSIFIED is unset, settlement redemption submission must fail closed with FXClassificationPending."
3633:+      - "R1 may model durable commands and tests only; it must not perform live redeem submission, production DB mutation, or CLOB cutover."
3657:+      - "src/execution/settlement_commands.py"
3666:+      - "tests/test_settlement_commands.py"
3674:+      - "live redeem submission"
3675:+      - "live venue submission"
3676:+      - "production DB mutation"
3681:+      - "pytest -q -p no:cacheprovider tests/test_settlement_commands.py"
3682:+      - "pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat"
3684:+      - "python3 -m py_compile src/execution/settlement_commands.py src/execution/harvester.py src/state/db.py tests/test_settlement_commands.py"
3693:+      - "future G1 live readiness gates"
3695:+      - "Stop and plan if R1 needs live redeem/chain submission in default runtime instead of a durable command model."
3697:+      - "Stop and plan if settlement command failure attempts to mark positions settled or alter LifecyclePhase grammar."
3698:+      - "Stop and plan if R1 needs production DB/state artifact mutation rather than schema/init code and tests."
3704:+      - "paper live parity"
3705:+      - "paper/live parity"
3715:+        - "paper live parity"
3716:+        - "paper/live parity"
3727:+        - "test_p0_live_money_safety"
3731:+        - "R1 settlement redeem command ledger"
3732:+        - "TIGGE ingest stub"
3733:+        - "calibration retrain"
3772:+      - "tests/integration/test_p0_live_money_safety.py"
3778:+      - "Paper-mode T1 runs through the same PolymarketV2Adapter protocol surface as live; fake-specific behavior is confined to tests/fakes unless an explicit protocol seam is needed."
3779:+      - "FakePolymarketVenue and live/mock-live adapter scenarios must produce schema-identical VenueSubmissionEnvelope / venue-command event shapes for the same scenario."
3780:+      - "Failure injection must model live-money failure modes without live venue submission, live cancel/redeem, production DB mutation, credentialed activation, or CLOB cutover."
3781:+      - "T1 must preserve pre-side-effect command journal discipline and may not bypass venue_command_repo or VenueSubmissionEnvelope provenance."
3782:+      - "T1 must preserve Q-FX-1 / no-live-redeem boundaries inherited from R1 and may not settle positions via fake redeem shortcuts."
3783:+      - "T1 may create integration/fake test infrastructure; it must not move paper/live behavior into a divergent production execution path."
3820:+      - "tests/integration/test_p0_live_money_safety.py"
3831:+      - "live venue submission"
3832:+      - "live venue cancel/redeem side effects"
3833:+      - "production DB mutation"
3835:+      - "paper/live divergent execution path"
3837:+      - "automatic settlement/redeem side effects"
3840:+      - "pytest -q -p no:cacheprovider tests/integration/test_p0_live_money_safety.py"
3843:+      - "python3 -m py_compile tests/fakes/polymarket_v2.py tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py src/venue/polymarket_v2_adapter.py"
3848:+      - "future A1 strategy benchmark paper/live replay"
3849:+      - "future G1 live readiness gates"
3853:+      - "R1 no-live-redeem boundary checks"
3855:+      - "Stop and plan if T1 needs credentialed Polymarket calls or production DB/state artifact mutation."
3856:+      - "Stop and plan if fake venue requires a production paper/live split path instead of the shared adapter protocol seam."
3858:+      - "Stop and plan if failure injection mutates lifecycle/settlement truth directly instead of producing venue-shaped facts/events."
3866:+      - "replay paper live shadow"
3876:+        - "replay paper live shadow"
3884:+        - "live shadow"
3886:+        - "calibration error"
3890:+        - "FakePolymarketVenue paper/live parity"
3891:+        - "settlement redeem command ledger"
3892:+        - "calibration retrain"
3925:+      - "No strategy may be promoted to live without StrategyBenchmarkSuite.promotion_decision() returning PROMOTE from replay + paper + shadow evidence."
3926:+      - "A1 benchmark metrics must include alpha, execution drag, fees/slippage, fill probability, adverse selection, capital-lock/time-to-resolution risk, drawdown, and calibration error vs market implied probability."
3927:+      - "Live-shadow evaluation is read-only/shadow evidence; A1 must not place live orders, mutate production DB/state artifacts, activate credentials, or authorize CLOB cutover."
3928:+      - "Paper evaluation must use the shared T1 fake venue/protocol seam without creating a divergent production execution path."
3963:+      - "live venue submission"
3964:+      - "live venue cancel/redeem side effects"
3965:+      - "production DB mutation"
3967:+      - "live strategy promotion"
3968:+      - "credentialed live shadow activation"
3969:+      - "calibration retrain go-live"
3981:+      - "G1 live readiness gates"
3982:+      - "F2 calibration retrain drift signals"
3985:+      - "Stop and plan if A1 needs live venue orders, credentialed live shadow activation, production DB/state artifact mutation, or CLOB cutover."
3987:+      - "Stop and plan if benchmark metrics redefine core probability, settlement, FDR, Kelly, or lifecycle semantics instead of consuming existing evidence."
4014:+        - "calibration retrain"
4015:+        - "TIGGE ingest stub"
4043:+      - "tests/test_k2_live_ingestion_relationships.py"
4047:+      - "F1 wires forecast source selection and provenance only; it must not activate TIGGE ingest, retrain calibration, or change live-money behavior."
4050:+      - "Forecast source registry is forecast/training/signal plumbing, not settlement source routing authority."
4051:+      - "Do not make Open-Meteo/grid forecast sources settlement-adjacent truth or collapse forecast-skill, Day0, hourly, and settlement source roles."
4078:+      - "tests/test_k2_live_ingestion_relationships.py"
4087:+      - "calibration retrain"
4089:+      - "active TIGGE fetch implementation"
4090:+      - "settlement source routing change"
4091:+      - "live venue submission"
4094:+      - "pytest -q -p no:cacheprovider tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry"
4106:+      - "future F2 calibration retrain loop"
4107:+      - "future F3 TIGGE ingest stub"
4110:+      - "Stop and plan if F1 requires activating TIGGE or performing any new external data pull path."
4111:+      - "Stop and plan if forecast provenance requires a non-additive forecasts table rebuild instead of additive columns and legacy ALTER hooks."
4112:+      - "Do not change calibration parameters, Platt training corpus, or alpha weights in F1."
4113:+      - "Do not alter settlement, Day0, or hourly source routing while wiring forecast-source registry."
4114:+  - id: "r3 calibration retrain loop implementation"
4118:+      - "calibration retrain loop"
4122:+      - "calibration_params_versions"
4127:+        - "calibration retrain loop"
4131:+        - "calibration_params_versions"
4140:+        - "TIGGE ingest stub"
4142:+        - "settlement source routing"
4143:+        - "live venue submission"
4152:+      - "docs/reference/modules/calibration.md"
4161:+      - "src/calibration/retrain_trigger.py"
4162:+      - "src/calibration/AGENTS.md"
4163:+      - "src/calibration/store.py"
4165:+      - "tests/test_calibration_retrain.py"
4166:+      - "tests/test_provenance_5_projections.py"
4169:+      - "F2 wires an operator-gated retrain/promotion trigger only; it must not auto-fire retraining or silently change live calibration without operator arm and frozen-replay PASS."
4170:+      - "Calibration retrain corpus may consume only venue_trade_facts WHERE state='CONFIRMED'. MATCHED and MINED are forbidden for training."
4172:+      - "Metric identity, data_version, cluster, and season remain explicit; do not mix HIGH/LOW calibration families."
4173:+      - "F2 must not change Platt math, settlement/source routing, TIGGE active ingest, or live venue behavior."
4181:+      - "docs/reference/modules/calibration.md"
4190:+      - "src/calibration/retrain_trigger.py"
4191:+      - "src/calibration/AGENTS.md"
4192:+      - "src/calibration/store.py"
4194:+      - "tests/test_calibration_retrain.py"
4195:+      - "tests/test_provenance_5_projections.py"
4203:+      - "automatic calibration retrain go-live"
4205:+      - "active TIGGE external data pull"
4206:+      - "settlement source routing change"
4207:+      - "live venue submission"
4209:+      - "pytest -q -p no:cacheprovider tests/test_calibration_retrain.py"
4210:+      - "pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only"
4211:+      - "pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_f2_calibration_retrain_loop_routes_to_f2_profile_not_heartbeat"
4216:+      - "src/calibration/store.py"
4217:+      - "src/calibration/manager.py"
4221:+      - "Stop and plan if F2 requires changing Platt formulas, calibration manager runtime lookup semantics, or live evaluator behavior beyond gated promotion through existing model stores."
4223:+      - "Do not activate TIGGE ingest, mutate production DBs, or perform live calibration retrain in F2."
4226:+      - "F3 TIGGE ingest stub"
4228:+      - "TIGGE ingest stub"
4229:+      - "TIGGEIngest"
4230:+      - "TIGGEIngestNotEnabled"
4231:+      - "ZEUS_TIGGE_INGEST_ENABLED"
4232:+      - "ZEUS_TIGGE_PAYLOAD_PATH"
4235:+        - "F3 TIGGE ingest stub"
4237:+        - "TIGGE ingest stub"
4238:+        - "TIGGEIngest"
4239:+        - "TIGGEIngestNotEnabled"
4240:+        - "ZEUS_TIGGE_INGEST_ENABLED"
4241:+        - "ZEUS_TIGGE_PAYLOAD_PATH"
4250:+        - "calibration retrain"
4252:+        - "settlement source routing"
4253:+        - "active TIGGE backfill"
4274:+      - "F3 lands a TIGGE ingest stub only; it must not perform live TIGGE archive pulls, GRIB parsing, calibration retrain, or live-money behavior."
4275:+      - "TIGGE remains dormant behind both an operator-decision artifact and ZEUS_TIGGE_INGEST_ENABLED."
4276:+      - "When TIGGE is operator-enabled, ensemble fetch routing must use the registered ForecastIngestProtocol adapter rather than sending model=tigge to Open-Meteo."
4277:+      - "Open-gate TIGGE may read only an operator-approved local JSON payload via constructor, ZEUS_TIGGE_PAYLOAD_PATH, or payload_path in the decision artifact; missing payload configuration fails closed."
4278:+      - "TIGGE construction is allowed when the gate is closed, but fetch must fail closed with TIGGEIngestNotEnabled before any external I/O."
4279:+      - "TIGGE is a forecast source only, not settlement, Day0, hourly, or calibration source authority."
4303:+      - "calibration retrain"
4305:+      - "active TIGGE external data pull"
4307:+      - "settlement source routing change"
4308:+      - "live venue submission"
4318:+      - "future F2 calibration retrain loop"
4321:+      - "Stop and plan if F3 requires real TIGGE archive HTTP, API-key handling beyond constructor storage, GRIB parsing, or data persistence."
4322:+      - "Stop and plan if switch-only wiring needs more than local operator-approved payload loading behind the existing TIGGE dual gate."
4323:+      - "Stop and plan if TIGGE activation can occur without both operator artifact and ZEUS_TIGGE_INGEST_ENABLED."
4324:+      - "Do not change calibration, settlement, Day0, hourly, or live venue routing in F3."
4325:+  - id: "r3 risk allocator governor implementation"
4352:+        - "risk caps"
4357:+        - "tests/test_risk_allocator.py"
4361:+        - "FakePolymarketVenue paper/live parity"
4362:+        - "settlement redeem command ledger"
4373:+      - "docs/reference/modules/riskguard.md"
4386:+      - "config/risk_caps.yaml"
4390:+      - "src/riskguard/AGENTS.md"
4391:+      - "src/risk_allocator/**"
4400:+      - "tests/test_risk_allocator.py"
4406:+      - "A2 may block or reduce-only new risk but must not place live orders, mutate production DB/state artifacts, activate credentials, promote strategies, or authorize CLOB cutover."
4408:+      - "Runtime ExecutionIntent allocation metadata must be populated by production entry construction, not only test-only dynamic attributes."
4410:+      - "A2 config defaults must be sane if config/risk_caps.yaml is absent; operator tuning is not a prerequisite for engineering closeout."
4419:+      - "docs/reference/modules/riskguard.md"
4432:+      - "config/risk_caps.yaml"
4436:+      - "src/riskguard/AGENTS.md"
4437:+      - "src/risk_allocator/**"
4446:+      - "tests/test_risk_allocator.py"
4455:+      - "live venue submission"
4456:+      - "live venue cancel/redeem side effects"
4457:+      - "production DB mutation"
4459:+      - "live strategy promotion"
4460:+      - "credentialed live shadow activation"
4463:+      - "pytest -q -p no:cacheprovider tests/test_risk_allocator.py"
4464:+      - "pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py"
4465:+      - "pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat"
4466:+      - "python3 -m py_compile src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/data/polymarket_client.py tests/test_risk_allocator.py"
4471:+      - "G1 live readiness gates"
4476:+      - "Stop and plan if A2 needs live venue orders, credentialed activation, production DB/state artifact mutation, or CLOB cutover."
4501:+      - "No production V2 cutover, DB mutation, or live placement is authorized by package closeout checks."
4540:+      - "tests/test_neg_risk_passthrough.py"
4546:+      - "tests/test_live_execution.py"
4556:+      - "pytest -q -p no:cacheprovider tests/test_v2_adapter.py tests/test_neg_risk_passthrough.py tests/test_cutover_guard.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py tests/test_k2_slice_e.py"
4582:+      - "tests/test_neg_risk_passthrough.py"
4589:+      - "Only src/venue/polymarket_v2_adapter.py may touch py-clob-client-v2 for live placement."
4590:+      - "Every live submit must still persist a venue command before the venue side effect."
4592:+      - "Z2 must not mutate venue command schema; U1/U2 own snapshot and raw-provenance DB columns."
4605:+      - "tests/test_neg_risk_passthrough.py"
4640:+      - "pytest -q tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py"
4641:+      - "pytest -q tests/test_neg_risk_passthrough.py"
4650:+      - "Do not choose or execute the production V2 cutover."
4664:+      - "live-money runtime gate"
4674:+      - "tests/test_live_execution.py"
4677:+      - "No live venue side effect may proceed unless CutoverGuard is LIVE_ENABLED."
4691:+      - "tests/test_live_execution.py"
4714:+      - "pytest -q tests/test_executor.py tests/test_live_execution.py"
4741:+| `risk_caps.yaml` | R3 A2 engineering defaults for RiskAllocator/PortfolioGovernor capacity, drawdown, heartbeat/WS-gap, reconciliation, unknown-side-effect, and maker/taker thresholds |
4745: - `settings.json` is the source for tunable runtime parameters. Other config files have scoped authority for cities, generated data bounds/correlation, provenance, and reality contracts.
4747: - Changes to `provenance_registry.yaml` require tracing to source literature/data
4748:+- `risk_caps.yaml` defaults must remain sane when absent; operator tuning is separate from engineering closeout and must not itself authorize live deployment.
4752:diff --git a/config/risk_caps.yaml b/config/risk_caps.yaml
4756:+++ b/config/risk_caps.yaml
4762:+# file still loads sane defaults in src/risk_allocator/governor.py.
4781: | `authority/` | Current architecture and delivery law -> `authority/AGENTS.md` |
4782: | `reference/` | Canonical domain, math, architecture, market/settlement, data/replay, failure-mode, and module references -> `reference/AGENTS.md` |
4785:+| `operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` | Packet-local R3 Z0 CLOB V2 live-money invariant summary; evidence, not authority |
4786: | `operations/task_2026-04-23_midstream_remediation/` | Midstream remediation package; phase evidence lives under `phases/` and routes through `operations/current_state.md` |
4793:@@ -35,6 +35,7 @@ Historical governance files demoted from authority live under
4794: - `authority/zeus_current_delivery.md` - current delivery law
4799: - `reference/zeus_market_settlement_reference.md` - canonical market/settlement reference anchor
4801:@@ -45,6 +46,7 @@ Historical governance files demoted from authority live under
4802: - `runbooks/live_operation.md` - day-to-day live daemon runbook
4804: - `operations/task_2026-04-23_midstream_remediation/` - midstream remediation package; phase evidence lives under `phases/`
4805:+- `operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` - R3 Z0 packet-local live-money invariant summary for CLOB V2
4806: - `artifacts/tigge_data_training_handoff_2026-04-23.md` - dated TIGGE asset + Zeus training handoff snapshot
4998:+Every artifact lands on disk BEFORE SendMessage notification. SendMessage is convenience; disk is canonical record. **SendMessage delivery is asymmetric and can drop silently**; disk is the durable source.
5000:+Recovery pattern: if a teammate goes idle without sending notification, disk-poll for their output. If found, treat as delivered.
5086:+- `tests/test_dt1_commit_ordering.py` exists with 6 PASSING tests; file docstring: "Relationship tests for DT#1 / INV-17: DB authority writes commit BEFORE..."
5153:+**Recovery**: read the disk file; treat as delivered; proceed normally. Note in judge_ledger as a process observation.
5332:+- External evidence sources (Anthropic + Cognition for harness; AWS / GCP / Postgres docs for database; etc.)
5456:+Track: how many phases / how much elapsed before mutual cross-over occurs. Different topic types may have different convergence speeds. Build a calibration table.
5458:+### E2: Source database
5476:+## §13 Lineage + provenance
5507: | `task_2026-04-23_midstream_remediation/` | packet evidence | Midstream remediation package; phase evidence lives under `phases/` and includes POST_AUDIT_HANDOFF_2026-04-24.md for post-compaction resumption |
5509: | `task_2026-04-26_live_readiness_completion/` | packet evidence | Live-readiness completion planning packet (K=4 antibodies for 11 open B/G/U/N items); implementation lands in `claude/live-readiness-completion-2026-04-26` worktree |
5512:+| `task_2026-04-26_ultimate_plan/` | packet evidence | R3 ultimate implementation packet for Zeus CLOB V2 live-money execution and dominance infrastructure; phase cards, boot notes, work records, reviews, and M3 user-channel ingest evidence live under `r3/` |
5513:+| `task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` | packet evidence | R3 Z0 packet-local live-money invariant summary for CLOB V2; not a durable authority doc |
5521:@@ -4,6 +4,20 @@ Role: single live control pointer for the repo.
5528:+- Mainline task: **Zeus R3 CLOB V2 / live-money upgrade — G1 live-readiness gates phase entry**
5533:+- Live-money contract: `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`
5535:+- Current phase: `G1 ENGINEERING HARDENED; EXTERNAL EVIDENCE BLOCKED / LIVE NO-GO` — post-interruption verification confirms the safe no-operator seams keep improving, but this is **not** only waiting for a human "yes". The current local evidence is: targeted residual repair group `15 passed, 15 skipped`; broad R3 aggregate `128 passed, 2 skipped`; topology `--scripts` and `--tests` both `ok true`; R3 drift `GREEN=241 YELLOW=0 RED=0` with `r3/drift_reports/2026-04-28.md`; `scripts/live_readiness_check.py --json` still fails closed with `16/17` gates because Q1 Zeus-egress and staged-live-smoke evidence are absent and `live_deploy_authorized=false`; full-repo pytest sample is still red (`--maxfail=30`: 30 failed, 2566 passed, 91 skipped, 16 deselected, 1 xfailed, 1 xpassed). Additional hardening since the second-round review includes CutoverGuard LIVE_ENABLED evidence binding to a 17/17 readiness report, WU transition scripts requiring operator-provided `WU_API_KEY`, settlement rebuild helper registration, and stale fixture compatibility fixes. Remaining no-go blockers: real Q1/staged evidence, G1 close review, explicit `live-money-deploy-go`, full-suite riskguard/harvester/runtime-guard triage, and current-fact data/training evidence for any TIGGE/calibration/live-alpha claim.
5536:+- Freeze note: A2 pre-close completion does not authorize live venue submission/cancel/redeem, CLOB cutover, automatic cancel-unknown unblock in production, live R1 redeem side effects, calibration retrain go-live, external TIGGE archive HTTP/GRIB fetch, production DB mutation outside explicit test/local schema seams, credentialed WS activation, live strategy promotion, or live deployment. Q1-zeus-egress and CLOB v2 cutover go/no-go remain OPEN.
5537:+- Freeze point: live placement remains blocked by Q1/cutover plus heartbeat/collateral/snapshot gates. G1 may implement/readiness-check gate surfaces only; it cannot authorize live deployment, run live smoke, or execute live venue side effects.
5559:+| `polymarket_live_money_contract.md` | contract evidence | Packet-local R3 Z0 live-money invariant summary; not a new docs authority plane |
5597:+- No heartbeat / keepalive / session method in README (we did NOT find evidence of mandatory heartbeat in V2 README — flagged for Phase 0.B deeper source check)
5607:+- No clientOrderId / idempotency / heartbeat / WS keepalive wording surfaced
5612:+1. V2 is a **separate Python package**, not a config flag inside V1 — confirms transport-paradigm shift (Plan §2 Paradigm A) is real, not synthetic.
5676:+- Affects: CollateralLedger, settlement/redeem command ledger, reports, and G1 readiness gate
5696:+Status: **V2_ACTIVE_P0 under R3** (2026-04-27 Z0). Migrate Zeus's Polymarket CLOB integration from V1 (`clob.polymarket.com`, `py-clob-client`, USDC.e collateral) to V2 (`clob-v2.polymarket.com`, `py-clob-client-v2`, pUSD collateral) without losing typed-contract, fail-closure, provenance, reconciliation, and cycle-architecture investments. The exact heartbeat cadence is evidence-gated by Q-HB; R3 still treats heartbeat supervision as mandatory for live resting-order risk correctness.
5702: - Changes to settlement schema, INV-14 spine, observation_instants_v2, calibration tables. The CLOB V2 boundary is upstream of all of those — this packet does not alter them.
5712:+- Packet-local live-money contract: `polymarket_live_money_contract.md`
5724:+No source-code change is authorized by this original packet body. Z0 is doc/test-only. R3 implementation phases authorize later source changes one phase at a time after drift check, topology navigation, operator-gate review, acceptance tests, and critic/verifier review. Q1-zeus-egress, Q-HB, Q-FX-1, CLOB V2 cutover, calibration retrain, TIGGE ingest, and G1 live deploy remain fail-closed operator gates.
5742:-| A. Transport | request/response, no liveness contract | persistent session + 10s mandatory heartbeat | Phase 1 (clob_protocol) + Phase 2 (heartbeat coroutine) |
5743:+| A. Transport | request/response, no liveness contract | supervised resting-order health; exact heartbeat cadence is Q-HB evidence-gated | Superseded by R3 Z1/Z2/Z3 |
5745:-| C. State machine | live → matched/cancelled | live → delayed → matched/cancelled + server-side cancel | Phase 2 (fill_tracker delayed branch) |
5746:+| C. State machine | live → matched/cancelled | expanded/unknown venue states must be typed and fail-closed; exact transitional spellings require current SDK/API citation | Superseded by R3 U2/M1-M5 |
5750:@@ -240,14 +256,14 @@ Medium-risk, sequential, requires Phase 1 + Phase 0 Q5/Q6 answers. **This is whe
5770:@@ -354,7 +370,7 @@ High-risk. Real V2 traffic. Requires Phase 2 closed AND real fundable account.
5779:@@ -378,7 +394,7 @@ High-risk. Real V2 traffic. Requires Phase 2 closed AND real fundable account.
5806:diff --git a/docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md b/docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
5810:+++ b/docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
5819:+This packet-local contract lists the Polymarket live-money invariants Zeus must uphold before any CLOB V2 live-money cutover. It is operational evidence and phase guidance, not a new architecture authority plane.
5823:+- V2 SDK (`py-clob-client-v2`) is the only live placement path after cutover.
5824:+- Heartbeat is mandatory for GTC/GTD live resting orders; FOK/FAK may run without resting-heartbeat supervision only when the adapter and CutoverGuard explicitly allow it.
5826:+- No live placement may proceed when `CutoverGuard.current_state()` is not `LIVE_ENABLED`.
5828:+- `MATCHED` is not `CONFIRMED`; trade-fact finality must preserve MATCHED/MINED/CONFIRMED distinctions and calibration consumes CONFIRMED only.
5836:+- This file does not claim Zeus has alpha dominance; F/A/G phases must prove benchmark, allocator, and live-readiness gates.
5853:+This report supersedes the earlier 2026-04-26 impact report for active implementation planning. The earlier report mixed real V2 risk with several falsified or under-evidenced premises. R3 Z0 keeps the live-money risk framing but corrects the factual substrate before any CLOB V2 execution work proceeds.
5860:+**CLOB V2 is a live-money P0 venue-adapter migration for Zeus.** It is not a strategy rewrite and not a trivial drop-in. It changes the venue boundary that can place, cancel, reconcile, and account for real-money orders.
5921:-V2 statuses: `live`, `matched`, `delayed`, `unmatched`. The `delayed` / `ORDER_DELAYED` / `DELAYING_ORDER_ERROR` states are new transitional values between `live` and `matched|cancelled`. Server-side enforced: `maxOrderSize = balance − Σ(openOrderSize − filledAmount)` validated at submit.
5955:-### 2.10 Neg-risk markets
5959:-`get_neg_risk(token_id)` SDK helper retained — Zeus's antibody test at `tests/test_neg_risk_passthrough.py:66-83` will need a V2-equivalent.
5977:-| **A. Transport** | request/response, no liveness contract | persistent session + 10s mandatory heartbeat | host change, `getHeartbeat()`, mass-cancel-on-miss |
5979:-| **C. Order state machine** | live → matched/cancelled (2 terminal) | live → delayed → matched/cancelled (3 terminal + server-side cancel) | `delayed` status, OrderArgs schema diff, fee removed from order, server-side max-order-size |
5984:+3. **Mandatory 10s heartbeat unsourced** — the exact "10s mandatory heartbeat cancels all orders" claim is not accepted as active law without fresh official/source evidence. R3 still makes HeartbeatSupervisor mandatory for Zeus risk correctness, but the cadence remains Q-HB/operator-evidence governed.
5986:+5. **`fee_rate_bps` removed is partial-truth** — fee fields may disappear from the submitted order shape, but Zeus's local EV/fee formula remains load-bearing. Fee discovery moves behind venue market-info/provenance, not into deletion of internal EV math.
5989:+8. **heartbeat existed in V1 v0.34.2** — heartbeat-related SDK surface is not by itself proof of a V2-only mandatory cadence. Zeus's runtime requirement is risk-driven: live resting orders need a supervised health mechanism; the venue cadence remains evidence-gated.
6001:-| Daemon liveness | `state/daemon-heartbeat.json`, `scripts/check_daemon_heartbeat.py:30`, `scripts/deep_heartbeat.py:14` (Layer 1 diagnostics) | process-level | operator alarm, no automatic action |
6013:+- **Venue adapter boundary**: live placement must move behind a strict V2 adapter and `VenueSubmissionEnvelope` so every side effect is reconstructable from provenance.
6014:+- **Cutover state**: live submit must be blocked unless `CutoverGuard` allows it.
6018:+- **Paper/live parity**: paper mode must exercise the same adapter protocol via a fake venue, not a parallel state machine.
6047:-- `:24-25` — `FILL_STATUSES = {"FILLED", "MATCHED"}`, `CANCEL_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}`
6054:-- Current logic does not crash, but **semantically misclassifies**: a `delayed → cancelled` transition that the client misses leaves Zeus believing the order is still live → `_mark_entry_voided` never fires → capital perpetually locked
6056:-This is a **latent capital-leak risk**, not a visible bug. Mitigation: add a third class `TRANSITIONAL_STATUSES = {"DELAYED", "ORDER_DELAYED"}` plus a wall-clock timeout that escalates to tombstone on prolonged delay.
6123:-| `tests/test_neg_risk_passthrough.py` SDK contract antibody | Repurpose for V2 SDK; this antibody design is the early-warning signal for SDK-renames and **must** be replicated, not removed |
6134:+| Runtime cutover block | Z1 | `CutoverGuard` prevents live submits before operator/go-no-go. |
6135:+| V2 SDK seam and provenance | Z2 | `PolymarketV2Adapter` + `VenueSubmissionEnvelope`; provenance over SDK call shape. |
6136:+| Heartbeat health | Z3 | Mandatory health supervisor for live resting orders; cadence evidence remains Q-HB. |
6138:+| Snapshot and payload lineage | U1/U2 | Fresh executable snapshots and five raw provenance projections. |
6140:+| Settlement/redeem | R1 | Durable settlement command ledger. |
6141:+| Fake venue parity | T1 | Paper and live share one adapter protocol. |
6142:+| Dominance infrastructure | F1-F3/A1-A2 | Source/retrain/TIGGE wiring plus benchmark and risk allocator. |
6143:+| Final live gate | G1 | 17/17 gates plus staged live smoke and operator deploy command. |
6166:-| R2 | `getClobMarketInfo` cache (fee_rate + tick_size + neg_risk in one call) | Reduces N×REST calls to 1 |
6189:+| Q1-zeus-egress | Z2 production preflight/cutover confidence | live placement blocked / preflight failure |
6192:+| CLOB v2 cutover | Z1 `LIVE_ENABLED` transition | `PRE_CUTOVER_FREEZE` / no live submit |
6193:+| TIGGE ingest | F3 active ingest | dormant, fetch raises gate-not-enabled |
6194:+| Calibration retrain | F2 live parameter promotion | existing Platt parameters stay frozen |
6195:+| G1 live-money deploy | final production flip | no live-money deployment |
6239:+The packet-local live-money contract is `polymarket_live_money_contract.md`. Its constraints are deliberately infrastructure-level and do not claim alpha dominance by themselves. R3 must make every venue side effect reconstructable, every live submit gateable, every fill finality state explicit, and every capital allocation bounded before live-money cutover.
6276:-- `tests/test_neg_risk_passthrough.py:66-83`, `test_polymarket_error_matrix.py:39`
6291:+| 2026-04-27 | R3.Z0 | pending | APPROVE | R3 Z0 source-of-truth correction closed locally: impact report rewrite, open-question correction, packet-local live-money contract, plan-lock tests; post-close third-party audit approved and Z1 is ready to start. |
6293:+| 2026-04-27 | R3.Z2 | pending | APPROVE | R3 Z2 V2 adapter closed locally after blocker-fix critic+verifier approval: strict `PolymarketV2Adapter`, frozen `VenueSubmissionEnvelope`, V2-only live dependency, preflight-before-submit compatibility wrapper, missing-order-id rejection, stale snapshot antibodies, and full R3 YAML/topology cleanup. Post-close third-party critic Confucius and verifier Wegener approved the closeout; Z3/Z4 are now unfrozen. |
6295:+| 2026-04-27 | R3.Z4 | pending | APPROVE | R3 Z4 CollateralLedger re-closed after fresh pre-close critic+verifier approval and terminal-release atomicity repair: pUSD/CTF collateral ledger, allowance-aware reservations, quantized BUY notional reservation, repo-owned terminal release, DB-backed snapshot refresh, no-live-side-effect wrap/unwrap commands, and R1-deferred redemption. Second post-close critic Leibniz + verifier Herschel passed; U1 is unfrozen. |
6306:+packet-local live-money contract route, impact-report path normalization,
6311:+its live cutover remains operator-gated.
6335:+The first pre-close critic and verifier blocked Z2 on live-money safety and
6340:+full-diff routing. The revised Z2 closeout removes the legacy live bypass,
6356:+parse OK, R3 drift check `GREEN=18 YELLOW=0 RED=0`, map-maintenance/reference-replacement/
6358:+diff, no V1 `py_clob_client` live imports, and `py_clob_client_v2` confined to
6360:+fail-closed and no live cutover is authorized. Post-close third-party critic
6361:+Confucius approved the adapter/live-money boundary, verifier Wegener reran the
6371:+Summary: Added Z0 plan-lock evidence, Z1 CutoverGuard, and Z2 Polymarket V2 adapter fail-closed boundary with compatibility routing through `PolymarketClient`, strict submission envelope provenance, stale snapshot rejection, V2-only live SDK dependency, and package topology/mesh maintenance.
6379:+Task: R3 Z3 HeartbeatSupervisor mandatory live-resting-order gate.
6391:+- 2026-04-27 — R3 Z0 detected that the phase card requested `docs/architecture/polymarket_live_money_contract.md`, but `docs/architecture/` is not an active docs subroot. The contract is emitted packet-locally instead to avoid creating a parallel authority surface.
6393:+- 2026-04-27 — R3 Z1 keeps exchange cutover-wipe classification deferred to M5/T1. Z1 blocks venue side effects before command persistence; it does not invent reconciliation findings or choose the live cutover date.
6394:+- 2026-04-27 — R3 Z2 first pre-close review proved that compatibility seams can silently preserve unsafe live paths. Future live-boundary phases should test the compatibility wrapper itself, not only the new adapter class.
6395:+- 2026-04-27 — R3 Z3 post-close critic caught a live scheduler observability gap not covered by the initial focused suite. Future daemon-scheduler slices must include `tests/test_bug100_k1_k2_structural.py` or an equivalent scheduler-health antibody before local close.
6409:+Summary: Added a DB-backed CollateralLedger for pUSD buy collateral, CTF sell inventory, reservations, legacy USDC.e separation, and degraded-authority fail-closed snapshots; added durable wrap/unwrap command states without live chain side effects; gated executor entry/exit before command persistence/SDK contact; enforced pUSD and CTF allowances; converted CTF inventory to micro-share units to avoid fractional overcommit; persisted runtime snapshots/reservations through the trade DB; released reservations from terminal venue-command transitions atomically; kept V2 SDK imports confined to the venue adapter; and left Q-FX-1/R1 redemption side effects blocked.
6413:+Z4 critic blocker repair: allowance checks, micro-share CTF accounting, DB-backed global ledger configuration, and command-repo terminal reservation release were added after the first pre-close critic BLOCK. A second critic then found two remaining blockers: runtime snapshots/reservations were not durably tied to the same command DB connection, and direct redeem compatibility paths could still imply live settlement side effects. That repair commits DB-backed balance snapshots, reserves buy/sell collateral with the same venue-command connection, releases terminal reservations in the same savepoint, and makes both `PolymarketClient.redeem()` and `PolymarketV2Adapter.redeem()` defer fail-closed to R1 without SDK contact. A fresh critic then found a BUY share-quantization blocker: submitted shares could round up above target notional while preflight/reservation only checked `target_size_usd`; the repair computes pUSD preflight/reservation from the actual submitted BUY notional (`ceil(shares * limit_price * 1e6)`) and adds antibodies for the `$10 @ 0.333 -> $10.003320` case. The next critic found an aggregate-allowance blocker: existing pUSD/CTF reservations were subtracted from balances but not allowances. That repair nets open reservations from pUSD and CTF allowance availability, removes the private BUY reservation fallback to target-size notional, and adds aggregate allowance over-reservation antibodies. Post-close third-party critic James then found a terminal-release atomicity blocker: executor fallback code could release reservations after a failed terminal `append_event()`. The latest repair removes those fallback releases and adds entry/exit antibodies proving reservations remain active when terminal append fails, so release occurs only through successful command-repo terminal transitions in the same savepoint. Evidence was refreshed before re-review.
6421:+Summary: Added immutable executable CLOB market snapshots with raw payload hashes, append-only SQLite triggers, repo round-trips, latest-fresh lookup, and a Python freshness/tradability gate at `venue_command_repo.insert_command()`. New venue commands must cite a fresh snapshot before insertion; stale/missing snapshots and disabled/inactive/closed/orderbook-disabled/token/tick/min-size/neg-risk mismatches fail closed before executor SDK contact. Entry and exit intent contracts now carry executable snapshot citation and comparison facts. Fresh DBs create `venue_commands.snapshot_id NOT NULL`; legacy DBs receive a nullable column but new writes are Python-enforced.
6422:+Verification: `tests/test_executable_market_snapshot_v2.py` reached `15 passed`; command journal suite reached `104 passed` then `118 passed` with U1 tests included; executor/collateral focused suite reached `79 passed, 2 skipped, 4 warnings`; combined U1/Z4 focused suite reached `131 passed, 8 skipped, 4 warnings`; `py_compile` OK; U1 drift check `GREEN=14 YELLOW=0 RED=0`; topology navigation OK; map-maintenance OK; planning-lock OK; `git diff --check` OK. A broader `tests/test_architecture_contracts.py tests/test_db.py` run surfaced two unrelated discovery-harness failures from `temperature_metric=None`; topology/verifier classified them outside U1 scope.
6424:+Next: `ready_to_start` is advanced to `[U2]`. Start U2 only through topology navigation and its own boot/pre-close/post-close gates; live cutover remains blocked by Q1/cutover and downstream M/R/T gates.
6438:+- opponent-mid grep-verified §8.3 17 transitions over-count by 4 already-existing (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION) → real K=4-6, not 17.
6441:+- Slice card mints landing on disk with proper schema (id/authority/file_line/antibody_test/depends_on/h/risk/critic_gate).
6531:+(Up = boundary + provenance; Mid = execution truth + state machine;
6534:+honest dependency-ordered execution sequence. Per-section detail lives in
6540:+**Honest scope label** (per multi-review scientist): this plan is a **live-readiness
6542:+S0 losses. It does NOT improve edge. Edge / forecast / calibration / learning
6558:+  exist (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED,
6560:+  → K1..K5 in Mid + K6 settlement in Down.
6565:+  extension). Apr26 §9 weather-vs-trading provenance asymmetry resolved.
6583:+    added as `up-08` FROZEN_REPLAY_HARNESS to guard against silent calibration
6601:+### §2.1 Region-Up — boundary + provenance + raw payloads (8 cards, 56h)
6620:+against silent calibration-schema drift from up-04 ALTER.
6628:+K4 CANCEL-FAIL, K5 EXCH-RECON; K6 settlement-redeem is Down). F-001 ROW-state
6635:+  (riskguard observability-only). NC-NEW-D allowlist is **function-scope**, not
6655:+  CLOB + 5 P0 behavioral tests (duplicate-submit, rapid partial-fill, RED
6705:+  COVERED by Up cards. up-03 EXTENDS with `raw_orderbook_jsonb`. Replay test
6744:+  RED-emission ships) → mid-03 → mid-04 → mid-05 → mid-06 → mid-07 → mid-08.
6774:+The plan cannot fully execute until operator delivers:
6810:+Multi-review scientist verdict: this plan is a **live-readiness gate**, not a
6813:+P_posterior → Edge & DBS-CI → Kelly → Size`) is preserved by omission, not
6814:+proven; up-08 FROZEN_REPLAY_HARNESS asserts the plan does not BREAK calibration
6817:+The following work is NECESSARY for live-market dominance and is explicitly
6821:+   (settlement-capture / shoulder-bin / center-bin / opening-inertia) with
6825:+   C) parameter monitoring; Monte Carlo noise calibration vs realized; α-fusion
6831:+4. **LEARNING_LOOP_PACKET** — settlement-corpus → calibration update →
6840:+live-market dominance.
6851:+| scientist (opus) | CONDITIONAL → CONDITIONAL APPROVE post-R2 | Plan honestly relabeled as live-readiness gate (§4 Dominance Roadmap added); up-08 frozen-replay closes calibration-drift antibody |
6901:+- proposed disposition: new cross-cut X-UD-1 (Up↔Down sequencing dependency on D-phase collapse). Or fold into X3 if X3 is already "transport↔provenance sequencing".
6905:+- summary: up-04 EXTEND venue_commands schema adds `condition_id` column (per Apr26 F-008 fix design + opponent-up Attack-5 EXTEND framing). Region-Mid F-008 fix (YES/NO outcome-token identity at command level) ALSO needs `condition_id` on the same table. To avoid two competing ALTER TABLE migrations, up-04 must be the SINGLE coordinated migration that adds condition_id once with semantics that satisfy both Up's payload-residual provenance need AND Mid's outcome-identity-freezing need. Mid's mid-NN cards should depend on up-04, not redefine the column.
6908:+## 2026-04-26 — proponent-mid — A1 K4-RED→durable-cmd parallelizability with D2.A given V2 unified SDK
6910:+- summary: Original X1 framing (judge spawn brief) had A1 K4-RED→durable-cmd vs CLOB-v2 D2.A delayed-status sequencing as a sequencing risk: if D2.A renames submit() or changes error semantics, A1's typed-error table breaks. Region-Down WebFetch finding (V2 SDK is unified V1+V2 client with per-token version resolution) reduces sequencing risk. PROPOSAL: A1 + D2.A are PARALLELIZABLE if A1 codes against py-clob-client's unified surface, NOT against V1-only or V2-only branches. Same applies to mid-02 (signed_order_hash + SIGNED_ORDER_PERSISTED): the ORDER signing surface in unified SDK must expose hash before post for both V1+V2 markets, OR mid-02 fails to close F-001 payload-binding for V2 markets. Cross-region question: does Region-Down's collapsed D-phase shape preserve a stable signing-surface seam where mid-02 can intercept signed_order_hash? If yes, A1 + mid-02 + D2.A all parallelizable. If no, mid-02 sequences AFTER Region-Down's surface stabilizes.
6919:+- **X-UM-1 (Up↔Mid `condition_id` coordinated schema migration)** → FOLDED INTO X3. X3's verdict will state: "up-04 owns the SINGLE coordinated ALTER TABLE on venue_commands (adds `condition_id` column) satisfying BOTH Up's payload-residual provenance need AND Mid's F-008 outcome-identity-freezing need. mid-NN cards depend on up-04 — they must NOT redefine the column. State-machine extension proposed by Apr26 §8.4 is implemented as additive enum members on existing CommandState (3 new: PARTIALLY_FILLED already exists per opponent-mid audit; 11 transitions are genuinely new — to be enumerated in mid-NN cards). Closed-enum amendment is governance, not architecture: gate via planning-lock + INV-29 amendment commit."
7058:+      Core pre-live blocker: order_command table + COMMAND_PERSISTED event before signing/posting.
7105:+      CANCEL_REQUESTED / CANCELLED / CANCEL_FAILED / REVIEW_REQUIRED are missing. Related to
7119:+      distinct from D2 (transport) and A1 (RED/command).
7153:+      D1 = Phase 1 protocol+antibody: V2 SDK contract antibody (1.D) + neg_risk passthrough
7160:+    title: RED risk behavior authority-drifted — docs claim cancel+sweep, runtime does not
7165:+      A1 = K4-RED→durable-cmd: the durable-command journal and RED immediate cancel-all are
7166:+      the same deferred item from PR18. RED executor (cancel open orders, block entries,
7184:+    title: Tests prove happy paths only — live-money failure modes uncovered
7259:+      Cancel failure means order may still be live; CANCEL_FAILED must trigger reconciliation.
7327:+  - id: transition_REVIEW_REQUIRED
7328:+    title: State transition REVIEW_REQUIRED missing
7333:+      A1 = K4-RED→durable-cmd: REVIEW_REQUIRED is the terminal state when reconciliation
7334:+      cannot resolve an unknown submit. RED executor must produce this state.
7337:+  - id: transition_REDEEM_REQUESTED
7338:+    title: State transition REDEEM_REQUESTED missing
7344:+      REDEEM_REQUESTED is a state within that redemption flow.
7347:+  - id: transition_REDEEMED
7348:+    title: State transition REDEEMED missing
7354:+      REDEEMED is the success terminal for that path.
7360:+    title: Proposed production order event schema (raw_request/response/orderbook/trade/position + 8 timestamps + sdk_version + signature_type)
7371:+  # §9 — Data provenance audit (one row)
7373:+  - id: provenance_audit
7374:+    title: Trading data provenance audit — CLOB order/trade raw storage parity with weather subsystem
7381:+      settlement_source_snapshot. All map to the proposed raw-payload-storage slice.
7382:+      Weather provenance subsystem is PASS; trading provenance is FAIL.
7396:+Authority basis: live external fetch (curl + WebFetch) of Polymarket V2 SDK source on 2026-04-26 + grep over Zeus `data-improve` HEAD on same date.
7423:+### 2.2 V2 host live probe
7429:+These three endpoints alive against Anthropic egress is NOT proof Zeus's egress (Polygon / Gnosis Safe / IP-pinned account) is reachable. Q1 is partially answered for "is the host generally reachable from any internet?" but NOT answered for "is Zeus's specific egress allowed?" Operator must still execute 0.A from Zeus's daemon machine to discharge Q1.
7451:+These are claims in `plan.md` or `v2_system_impact_report.md` that the live V2 source FALSIFIES.
7484:+**Reality**: V2 SDK `client.py:245-251` defines `post_heartbeat(heartbeat_id: str = "") -> dict` — single POST endpoint at `/v1/heartbeats` (note: `/v1/`, not `/v2/`!). It is NOT enforced session-keepalive in the SDK code path: `create_and_post_order` does NOT call `post_heartbeat`. There is no internal coroutine, no async timer, no `asyncio.sleep(10)`. **The heartbeat is an explicit operator-side opt-in, not a transport-layer must-have.** Examples directory `examples/account/` has 13 files; NONE is a heartbeat example. The SDK has zero docs about timing requirements.
7494:+**Plan claim**: `v2_system_impact_report.md:67` "V2 statuses: live, matched, delayed, unmatched. The delayed / ORDER_DELAYED / DELAYING_ORDER_ERROR states are new transitional values..."
7497:+**Implication**: M2 (`delayed` status branch), Phase 2.A (fill_tracker delayed branch), the wall-clock timeout escalation, and the §4.3 "latent capital-leak risk" narrative are all built on a server-side state name that the SDK does not surface. Either (a) the docs site mentions it somewhere I haven't reached, in which case proponent must produce the URL, OR (b) the impact-report invented the state name. Demand the citation.
7509:+**Reality**: V2 SDK has `get_clob_market_info(condition_id: str)` (snake_case, not camelCase as plan writes). `client.py:290` returns a dict; the SDK ALSO has dedicated methods `get_fee_rate_bps(token_id)`, `get_fee_exponent(token_id)`, `get_tick_size(token_id)`, `get_neg_risk(token_id)`. **The plan implicitly proposes a less-granular method as the replacement; the right replacement is the existing dedicated `get_fee_rate_bps`.** Also note: V2 SDK already wraps `__ensure_market_info_cached` (`client.py:1061`) internally — Zeus's plan to add its own cache layer (slice 2.F) is REDUNDANT WITH SDK CACHING.
7539:+This is a hidden architectural decision. The plan has only "thread-safe design in 2.B; A2 antibody injection test" as the mitigation row in the risk register (`plan.md:487`). That is risk-handwaving, not a design.
7543:+`plan.md:281-286` slice 2.D edits `polymarket_client.py` import + host + OrderArgs. Slice 3.D in Phase 3 says "Flip ZEUS_CLOB_PROTOCOL=v2." But: between 2.D landing and 3.D flipping, the code already imports BOTH SDKs at module load. **What happens if `py-clob-client-v2` v1.0.x has a transitive-dependency conflict with V1's `py-clob-client>=0.25`?** The plan's risk register row "V2 SDK package conflicts with existing deps" (`plan.md:493`) has mitigation "resolve in 1.G with operator". But 1.G is a cosmetic dual-pin; the actual conflict surface is in 2.D where both are imported simultaneously. **Real test**: `pip install py-clob-client>=0.25 py-clob-client-v2>=1.0.0` and run `python -c "from py_clob_client.client import ClobClient as C1; from py_clob_client_v2.client import ClobClient as C2"`. Until that runs clean, slice 2.D's branching design is theoretical.
7689:+| `src/engine/cycle_runner.py` | 1-100 | `run_cycle(mode)` synchronous; `KNOWN_STRATEGIES` enum (line 50); `_TERMINAL_POSITION_STATES_FOR_SWEEP` references INV-19 RED-related code; `_execute_force_exit_sweep` sets `pos.exit_reason="red_force_exit"` but does NOT emit durable cancel commands (cross-cut X1 / F-010 evidence) |
7691:+| `src/execution/executor.py` | 1-150 + grep results | `idempotency_key` is a load-bearing field on `OrderResult` (line 66), `ExitOrderIntent` (line 84); pre-submit lookup `find_command_by_idempotency_key` (lines 456, 795); `_orderresult_from_existing` ack-state gate (lines 87-168); `v2_preflight` is invoked at line 873 inside `_live_order` (Phase 4 / INV-25 / K5 evidence) |
7693:+| `src/execution/fill_tracker.py` | 1-120 + grep | `FILL_STATUSES = frozenset({"FILLED", "MATCHED"})` (line 24); `CANCEL_STATUSES = frozenset({"CANCELLED","CANCELED","EXPIRED","REJECTED"})` (line 25); `_normalize_status` defined at line 390; status reads in `check_pending_entries` at lines 330, 352; **NO** `DELAYED` handling today — confirms plan slice 2.A is real gap |
7694:+| `src/execution/harvester.py` | 1-100 head + grep | `run_harvester()` at line 293; `harvester_live` flag-OFF gate via `ZEUS_HARVESTER_LIVE_ENABLED` (line 305 area); pUSD redemption boundary lives here (lines 1244-1264 per inventory) |
7704:+| `.../evidence/mid/_context_boot_proponent.md` | 100-160 | Region-Mid's reading of A1.5/A4.5 amendments + RED durable-cmd routing |
7705:+| `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` | 1-80 grep slice | Wave 2 mapping: B2/B4/B5 are DATA work; G7/G10-cutover are the live-readiness gates |
7706:+| `docs/operations/task_2026-04-26_live_readiness_completion/evidence/audit_2026-04-26.md` | 22-58 grep slice | confirms G7 (LIVE_SAFE_CITIES) and G10-cutover live-readiness items |
7721:+| `https://github.com/Polymarket/py-clob-client-v2/blob/main/README.md` | 200 | full README — confirms structure above; reaffirms NO heartbeat/keepalive method documented in README |
7723:+**Authority note**: `v2_system_impact_report.md` cites richer evidence (V2 `getHeartbeat()` added in `py-clob-client-v2 v0.0.4` on 2026-04-16; pUSD contract `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`; mandatory ~10s heartbeat; EIP-712 v2; `clob-v2.polymarket.com` host) that my own WebFetch did not reproduce because GitHub's main README reflects the LATEST tag (v1.0.0), and the heartbeat detail lives in earlier release notes / source files / docs subpages. **The impact_report's pre-WebFetched evidence (2026-04-26 fetch) is the authority for these specific facts**; my WebFetches add the v1.0.0 README + USDC labelling + Polymarket docs landing-page negative.
7737:+| V2 `getClobMarketInfo(conditionID)` returns fee_rate + tick_size + neg_risk in one call | **ASSUMED** (impact_report §2.4 / Q3 OPEN) | gates Phase 0.C |
7741:+| Builder code REQUIRED for V2 fee-share | **ASSUMED-NO-DEFAULT** (Q7 OPEN); only docs wording is "Builder Program" | gates Phase 2.H |
7774:+| `getClobMarketInfo` is a 1-call replacement for fee/tick/neg_risk | **PARTIALLY REDUNDANT** | V2 SDK provides dedicated `get_fee_rate_bps`, `get_fee_exponent`, `get_tick_size`, `get_neg_risk` AND internally caches `MarketDetails` via `__ensure_market_info_cached` (client.py:1061). Slice 2.F adds a Zeus cache layer parallel to SDK cache — redundant. |
7785:+| Q4 (V1 fee snapshot for ≥3 weather tokens) | n/a (live data) | n/a (live data) | OPEN | Phase 0.E live `polymarket_client.get_fee_rate` runs |
7832:+| F-010 (RED authority drift) | NO | Region-Mid PR18-P2 A1 |
7840:+| B2 (settlement backfill scripts) | NO | DATA work, scripts/backfill_*; pre-existing |
7849:+- **X1 (PR18-P2 A1 ↔ D2.A delayed-status branch)**: SEQUENCE A1 BEFORE D2.A. Reasoning: A1 is the durable cancel-command journal (RED→cancel). When V2 D2.A introduces a `delayed` state with wall-clock timeout that voids positions, the void path needs A1's durable-cancel-command emission to avoid silently broken cancels. If D2.A lands first, you get capital-leak symmetry (delayed→void without durable cancel) that A1 has to fix afterward anyway.
7850:+- **X4 (F-011 raw-payload persistence — D2.D add-on vs independent NET_NEW packet)**: D2.D ADD-ON. Reasoning: SDK swap is when Zeus has full control of `OrderArgs` construction + post_order response (`polymarket_client.py:155+196`). Inserting raw-payload capture lines at the same boundary is ~30 LOC and a single column on `venue_commands`. Splitting into a separate packet creates two diff-windows on the same chokepoint module — collision risk per memory `feedback_no_git_add_all_with_cotenant`. Region-Up may push back wanting a broader `clob_market_snapshot` table; I will defend **D2.D add-on for the order-side raw payload** while conceding `clob_market_snapshot` (read-side / discovery-time snapshot) is a separate Region-Up F-007 concern.
7858:+- `down-06` G10-cutover sequencing precedes V2 Phase 2.B (cross-cut with live-readiness packet)
7925:+- `down-03` unified-client antibody (asserts `OrderArgsV1`/`OrderArgsV2`/`_resolve_version`/`_is_v2_order`/`post_heartbeat`/`get_fee_rate_bps`/`get_tick_size`/`get_neg_risk` exist; skip-on-import-error; pattern from `tests/test_neg_risk_passthrough.py:66-83`)
7941:+- **Wave 2 B2 / B4 / B5** (settlement backfill scripts / obs_v2 physical-bounds / DST flag writer) → Up. All touch DATA authority files (`source_rationale.yaml`, `test_topology.yaml`, `observation_instants_v2_writer.py`); not Down's region. Flagged here only for completeness.
7967:+- Sequence down-04 (Q1 amendment) and down-05 (packet status amendment) as text-edit slices that can ship pre-Q probe-results as low-risk closure work.
7979:+Layer: 2 (cross-module data-provenance / relationship invariants)
7992:+6 L2 OPEN questions all closed at HEAD-grep-verified. Q-NEW-1 RE-VERIFIED FRESH on-chain in this run (proponent-down dispatched live polygon.drpc.org eth_call). Disk-state lies fixed in-flight (down-01 yaml seam citation rot + down-07 yaml grep-evidenced absence proofs).
7998:+| A1 | Q-FX-1 ship-block gate (no ship-on-promise) | DUAL-GATE LOCKED | down-07 critic_gate adds: (i) file-existence check `evidence/q-fx-1_classification_decision_2026-04-26.md` MUST exist with operator-signoff specifying `{fx_line_item, trading_pnl_inflow, carry_cost}`; (ii) runtime env-flag `ZEUS_PUSD_REDEMPTION_ENABLED=false` default; fail-closed FXClassificationPending raise. Antibody: `tests/test_pusd_collateral_boundary.py::test_pusd_redemption_blocked_until_fx_classification` |
8000:+| A3 | pUSD propagation 7-row matrix | LOCKED w/ GREP-EVIDENCED ABSENCE | 4 ACTIVE modify-sites + 3 ABSENCE-PROVEN negative-tests (riskguard.py + chain_reconciliation.py + calibration/ all grep-empty for USDC/pUSD/currency) |
8003:+| A6 | Q-NEW-1 re-verification | FRESH RE-VERIFIED IN R2L2 RUN | proponent-down dispatched live curl to https://polygon.drpc.org during turn-2: symbol() → "pUSD"; name() → "Polymarket USD". Q-NEW-1 STABLE. No proxy upgrade between 2026-04-26 11:12 (original probe) and R2L2 lock time |
8029:+| 6 | `src/riskguard/riskguard.py` (sizing) | `grep -in "USDC\|currency\|pUSD\|collateral\|balance" src/riskguard/riskguard.py` = 0 hits | riskguard MUST stay collateral-agnostic ($-units only); antibody asserts grep returns empty |
8031:+| 8 | `src/calibration/` (training authority filter) | `grep -irn "USDC\|currency" src/calibration/` = 0 hits | calibration MUST NOT reference USDC currency unit; antibody asserts grep returns empty |
8038:+for module_path in ['src/riskguard/', 'src/calibration/', 'src/state/db.py', 'src/state/chain_reconciliation.py', 'src/observability/']:
8059:+    if os.environ.get("ZEUS_PUSD_REDEMPTION_ENABLED", "false").lower() != "true":
8061:+            "ZEUS_PUSD_REDEMPTION_ENABLED env-flag is false; operator MUST flip explicitly "
8071:+    monkeypatch.setenv("ZEUS_PUSD_REDEMPTION_ENABLED", "true")  # env says yes
8077:+    monkeypatch.setenv("ZEUS_PUSD_REDEMPTION_ENABLED", "false")
8118:+Proponent-down dispatched live curl to https://polygon.drpc.org during turn-2:
8137:+`grep "B2|B4|B5|settlement backfill|obs_v2|DST flag|observation_instants_v2_writer|source_rationale" docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-*.yaml` = 0 hits.
8142:+- B2: settlement backfill scripts (touches `scripts/onboard_cities.py` + settlements table)
8155:+| down-03 | `tests/test_neg_risk_passthrough.py:66-83` (V1 antibody pattern model) | ✓ |
8223:+| A5 | down-03 antibody full attribute list | 10 ClobClient attrs + 4 imports (14 total) | Final list dropped OrderArgsV1/V2 (V2 SDK unified to single OrderArgs per Mid R2L2 verdict). Skip-on-import-error from test_neg_risk_passthrough.py:62-75 |
8261:+    """Redeem winning shares for pUSD after settlement."""
8320:+# tests/test_v2_sdk_contract.py — NEW file; pattern from tests/test_neg_risk_passthrough.py:62-75
8331:+    "get_neg_risk",         # F-009 closure
8383:+| 6 | `src/riskguard/riskguard.py` | `grep -in "USDC\|currency\|pUSD\|collateral\|balance" src/riskguard/riskguard.py` = 0 hits | riskguard sizing MUST stay $-units only (collateral-agnostic) |
8385:+| 8 | `src/calibration/` (training authority filter) | `grep -irn "USDC\|currency" src/calibration/` = 0 hits | calibration MUST NOT reference USDC currency unit |
8413:+| down-03 | `tests/test_neg_risk_passthrough.py:62-75` (V1 antibody pattern model) | ✓ skip-on-import-error |
8618:+    for module_path in ['src/state/db.py', 'src/riskguard/', 'src/state/chain_reconciliation.py', 'src/calibration/']:
8742:+3. `src/execution/command_bus.py:92-103` — IN_FLIGHT_STATES = {SUBMITTING, UNKNOWN, REVIEW_REQUIRED, CANCEL_PENDING}; TERMINAL_STATES = {FILLED, CANCELLED, EXPIRED, REJECTED}.
8746:+7. `src/engine/cycle_runner.py:60-102, 352-372` — `_execute_force_exit_sweep` only sets `pos.exit_reason="red_force_exit"`; comment line 67 explicitly: "Does NOT post sell orders in-cycle". **No SDK cancel_order call**; no durable cancel command persisted on RED.
8750:+11. `src/riskguard/risk_level.py:11,20,29` — RED is a level enum; doc string says "Cancel all pending orders, exit all positions immediately"; runtime does NOT execute that.
8767:+### AV-3 (F-010 RESIDUAL — RED is decorative-capability)
8768:+**Claim**: NC-17 forbids decorative-capability. RED documentation promises "cancel all pending orders, exit all positions" but runtime only marks `pos.exit_reason="red_force_exit"`.
8769:+**Evidence**: `cycle_runner.py:67` explicit comment "Does NOT post sell orders in-cycle". `cycle_runner.py:60-102` `_execute_force_exit_sweep` mutates Python objects in memory only. No `client.cancel(order_id)` call in RED path. No `submit_command(intent_kind=CANCEL)` either. RED is currently a flag-mutation, not an order action.
8770:+**Burden on proponent**: cite where RED durably submits CANCEL or DERISK commands at HEAD 874e00c. Not in cycle_runner.py, not in riskguard.py, not in executor.py.
8797:+| REVIEW_REQUIRED | reachable from 7 source states (lines 45,52,59,68,75,78,83) | **EXISTS — yaml over-count** |
8798:+| REDEEM_REQUESTED | absent | NET_NEW |
8799:+| REDEEMED | absent | NET_NEW |
8802:+- ALREADY EXISTS (full): REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION = **4 over-counted by routing yaml**.
8804:+- GENUINELY NET_NEW: COMMAND_PERSISTED, SIGNED_ORDER_PERSISTED, ACCEPTED-vs-RESTING, CANCEL_FAILED, CANCEL_REPLACE_BLOCKED, CLOSED_MARKET_UNKNOWN, POSITION_CONFIRMED_FROM_EXCHANGE, RECONCILED_BY_OPEN_ORDERS, RECONCILED_BY_TRADES, REDEEM_REQUESTED, REDEEMED = **11 genuinely new**.
8812:+- (K6) Settlement post-trade (REDEEM_REQUESTED + REDEEMED) — 2 transitions, ~F-011 adjacent
8816:+**Burden on proponent**: enumerate their K=2 against this audit. Routing yaml flagged transition_REVIEW_REQUIRED `bucket: A1 / target_slice: A1` despite REVIEW_REQUIRED already being reachable from 7 states — yaml-heuristic mismatch.
8823:+### AV-7 (X1 sequencing risk — A1 ↔ D2.A)
8824:+**Claim**: A1 K4-RED→durable-cmd assumes venue_commands schema is stable. D2.A is CLOB V2 transport migration — likely renames `place_limit_order` / `post_order` and changes error-shape. A1's typed-error matrix may be invalidated by D2.A landing afterward.
8826:+**Burden on proponent**: produce dependency graph asserting independence, or concede sequencing risk.
8836:+**Proponent claim 3**: "PR18 P2 A1 K4 RED→durable-cmd preserves authority direction."
8837:+**Counter**: At HEAD, RED does not submit any durable cancel/derisk command (AV-3); it only mutates in-memory Python objects. A1 must EITHER add a new authority surface (risk module emits commands directly) OR push RED through cycle-runtime (latency cost). Either choice is an authority-direction decision, not preservation.
8853:+   - Signing-bind hook in `src/execution/executor.py` _live_order/execute_exit_order between persist phase and submit phase.
8868:+- **A1 (F-010 RED→durable cancel)**: authority-direction proof needed. RED currently only mutates `pos.exit_reason` in memory (cycle_runner.py:60-102). Slice must emit durable CANCEL command via existing INV-30 path.
8872:+### Settlement K6 (REDEEM_REQUESTED + REDEEMED)
8874:+Out of mid-scope; lives in harvester / settlement zone (Down region likely owns).
8882:+Judge ledger says "§8.3 reduces to 3-4 K-decisions." My audit says K=6 (or K=5 if settlement K6 is excluded as out-of-scope). I will defend K=5-6 in L1; if Up region's snapshot/recon work absorbs K5, the residual mid-K is 4 (K1, K2+K3, K4, settlement TBD).
8918:+| `src/execution/executor.py` | 1-1029 (full) | `_live_order` + `execute_exit_order` persist→submit→ack chain |
8922:+| `src/engine/cycle_runner.py` | 100-150, 340-400 | RED-force-exit-sweep + execution-truth warnings |
8923:+| `src/state/chain_reconciliation.py` | 1-120 | LEARNING_AUTHORITY_REQUIRED + position-vs-chain reconciliation |
8924:+| `src/riskguard/risk_level.py` | 1-32 (full) | RiskLevel enum + LEVEL_ACTIONS |
8936:+`_live_order` calls `insert_command` THEN `append_event(SUBMIT_REQUESTED)` THEN
8948:+`IN_FLIGHT_STATES = {SUBMITTING, UNKNOWN, REVIEW_REQUIRED, CANCEL_PENDING}`
8970:+`_live_order` calls `find_command_by_idempotency_key` BEFORE `insert_command`
8990:+| REVIEW_REQUIRED | REVIEW_REQUIRED event | `command_bus.py:76`, `venue_command_repo.py:44,51,58,67,74,82` |
8998:+| SIGNED_ORDER_PERSISTED | venue_commands schema has no `signed_order_hash` column; idempotency key is local-canonical, not the signed EIP-712 hash. Apr26 §8 wants signed-order provenance pre-post. **One column + one repo write**. |
9006:+| REDEEM_REQUESTED | Settlement-side (post-fill). **Outside current state machine**. |
9015:+The remaining 13 transitions are payload metadata, recovery branches, or out-of-region (discovery / settlement). The routing yaml's NET_NEW EXECUTION_STATE_MACHINE umbrella over-counts.
9024:+   (exchange-proven idempotency provenance). **Not a new umbrella.**
9037:+**RED authority direction (F-010)**: cycle_runner L352-373 RED→force_exit_sweep
9039:+durable cancel commands through venue_commands. PR18 P2 A1 (K4 RED→durable-cmd)
9040:+plugs this gap by routing RED-triggered cancellations through
9041:+insert_command/append_event(CANCEL_REQUESTED). Authority direction (risk →
9050:+  CANCELLED, EXPIRED, REJECTED, REVIEW_REQUIRED}. Closed grammar over
9054:+  LifecycleState (PENDING_TRACKED, ENTERED, HOLDING, DAY0_WINDOW, …) +
9116:+> Mid R1L1: K=4 (PAYLOAD_BIND, CANCEL_FAIL, PARTIAL/RESTING, EXCH_RECON) + A1 new-auth-surface + §8/C1.5. F-001 row CLOSED, payload OPEN. F-006 split; F-010 RED-decorative. Cards mid-01..06.
9128:+K6 settlement (REDEEM_REQUESTED + REDEEMED) = OUT-OF-SCOPE for Mid → Down region (D2 pUSD redemption flow per routing yaml lines 319-337).
9134:+| mid-01 | A1 K4 RED → durable-cmd (new authority surface) | proponent-mid | 3822 B |
9143:+**mid-02 BEFORE mid-01** — A1 emits CANCEL_REQUESTED via insert_command/append_event. Without mid-02 signed-payload binding landing first, CANCEL events have no signed-payload provenance, leaving F-010 closure incomplete on payload side.
9152:+mid-01 (A1 RED → durable-cmd)
9175:+| F-006 reconciliation | SPLIT — INV-31 reconciles local rows; F-006 needs exchange-side enum (command_recovery.py:71-77 punts orphans to REVIEW_REQUIRED) | mid-05 |
9177:+| F-010 RED authority | RESIDUAL — RED is decorative-capability at HEAD (riskguard 0 hits for command_bus emission; cycle_runner.py:67 "Does NOT post sell orders in-cycle"; riskguard.py:1080-1082 "forced exit sweep is a Phase 2 item") | mid-01 (NEW authority surface, NC-17 grammar-bounded) |
9184:+- **4 ALREADY EXIST**: REVIEW_REQUIRED (reachable from 7 source states), PARTIALLY_FILLED ((ACKED|UNKNOWN|PARTIAL, PARTIAL_FILL_OBSERVED) → PARTIAL), REMAINING_CANCEL_REQUESTED ((PARTIAL, CANCEL_REQUESTED) → CANCEL_PENDING), RECONCILED_BY_POSITION (chain_reconciliation.py).
9186:+- **11 genuinely NET_NEW** reduce to K1 + K2 + K3 + K4 + K5 + K6 (K6 settlement = Down region).
9194:+3. **mid-01 ownership**: riskguard direct vs cycle_runner-as-proxy emits CANCEL on RED. Authority-direction proof needed.
9214:+- **opponent-mid AV-6 wins** (proponent C-B): RED → durable-cmd is NEW authority surface, not preserved direction. NC-17 holds because grammar-bounded (CANCEL/DERISK only).
9222:+- **Constraint #4 (data provenance > code correctness)**: K3 RESTING locked R2-OPEN pending downstream-consumer audit; refused to lock payload-discrimination without auditing chain_reconciliation/lifecycle_manager interpretation.
9233:+Region: Mid (state grammar + journal payload + reconciliation + RED-cancel emission)
9234:+Layer: 2 (cross-module data-provenance / relationship invariants)
9255:+| A3 | mid-01 ownership | cycle_runner-PROXY (preserves existing pattern) | riskguard.py:826 unchanged (writes force_exit_review flag); cycle_runner.py:359-373 extension reads flag EARLIER (pre-execution-loop reorder) and emits CANCEL/DERISK commands inline. Authority direction PRESERVED (riskguard signals, cycle_runner emits). cycle_runner gains FIRST `insert_command` import. NC-17 grammar-bound to {CANCEL, DERISK}. |
9258:+| MINT | NC-NEW-D `zeus-insert-command-emitter-only` | NEW negative constraint | Allowed `insert_command` Python callers = {executor.py:476 (place_limit_order_with_command_journal), executor.py:815 (execute_exit_order), cycle_runner.py:<TBD line in 359-373 region> (mid-01 RED-cancel emission)}. Mirror NC-16 semgrep shape. |
9266:+| mid-01 ownership | cycle_runner-PROXY. riskguard signals via existing flag (unchanged); cycle_runner emits commands. Same-cycle latency via flag-read reorder. |
9286:+          - src/engine/cycle_runner.py       # allowed: mid-01 RED-cancel emission
9314:+| mid-01 | `src/riskguard/riskguard.py:826` (UNCHANGED — writes force_exit_review flag) | ✓ |
9315:+| mid-01 | `src/riskguard/riskguard.py:1077-1094` (`get_force_exit_review` reader, doc says "Phase 2 item") | ✓ |
9350:+- mid-01: emission seam at cycle_runner.py:359-373 extension; riskguard.py:826 unchanged; allowed-emitter allowlist
9388:+Region: Mid (state grammar + journal payload + reconciliation + RED-cancel emission)
9494:+| mid-01 | `src/riskguard/riskguard.py:826` (force_exit_review write — UNCHANGED) | ✓ `force_exit_review = 1 if daily_loss_level == RiskLevel.RED else 0` |
9515:+### mid-01 (RED-cancel emission via cycle_runner-proxy)
9518:+# tests/test_red_emit.py::test_red_emit_grammar_bound_to_cancel_or_derisk_only
9519:+def test_red_emit_grammar_bound_to_cancel_or_derisk_only(conn, portfolio):
9780:+    assert already_exist == {'REVIEW_REQUIRED','PARTIALLY_FILLED','REMAINING_CANCEL_REQUESTED','RECONCILED_BY_POSITION'}
9784:+    # Cross-module test: riskguard.tick() sets force_exit_review; cycle_runner reads + emits
9785:+    riskguard.tick()  # writes force_exit_review=1
9859:+| scientist | **CONDITIONAL** | Plan is live-readiness gate, not dominance plan; 0/20 cards improve edge; forecast/calibration/learning untouched; ≥5 Apr26 P0 tests unowned |
9865:+2. **Scope holes**: WebSocket coverage, deterministic-fake-CLOB, ≥5 Apr26 P0 behavioral tests, forecast/calibration/learning legs of the money path.
9884:+4. **mid-01 ownership ambiguity unresolved in YAML.** Prose says cycle_runner-as-proxy locked; YAML lists riskguard-direct AND cycle_runner-proxy.
9886:+### S1 — must fix before Wave B / production cutover
9888:+5. **Apr26 §F-012 fix design (deterministic-fake-CLOB + failure injection + restart simulation + resolved-market fixtures) NOT minted as a slice.** mid-06 has it as "recommended investment in conftest" only. Apr26 axis-31 (paper/live parity, FAIL/S1) and axis-49 ("failure-mode test suite") have **zero slice ownership**.
9890:+6. **0/20 cards improve edge.** Forecast / calibration / learning legs of the money path entirely absent. Apr26 Phase 4 (settlement corpus, high/low split, DST resolved fixtures) silently dropped. Mislabeling: plan is "live-readiness gate", not "dominate live market" as prompt promised.
9905:+- RED cancel-all behavioral (mid-01 covers RED-as-authority, not RED-as-action)
9925:+- **up-08 `FROZEN_REPLAY_HARNESS`** — bit-identical replay (P_raw → Size) before/after Wave A+B against fixture portfolio. Antibody against silent calibration-schema drift from up-04 ALTER. Estimated 8-12h. Per scientist's PROBABILITY_CHAIN_FROZEN_REPLAY recommendation.
9931:+3. **mid-01 yaml ownership lock** — pick cycle_runner-as-proxy in `depends_on` field; remove riskguard-direct option from yaml.
9936:+- Rename plan in §3 from "Zeus dominates live market" to "**Zeus live-readiness gate (Wave A+B+C+D+E) → readiness for dominance experiments**". Honest scope. Add §4 "**Post-readiness dominance roadmap**" listing the 4 missing money-path legs (forecast / calibration / edge-monitoring / learning) as deferred packets, not silently dropped.
9957:+6. Re-label plan §3 as live-readiness gate + add §4 Dominance Roadmap section.
9989:+| Ours | **Code-locality regions** | Up (boundary/provenance) · Mid (execution) · Down (transport) + cross-cuts X1-X4 |
9990:+| V2.1 | **Data-lifecycle phases** | Z (foundation) → U (snapshot+provenance) → M (execution lifecycle) → R (settlement) → T (parity) → A (strategy) → G (gates) |
9993:+(a function lives in one module). Data lifecycle is a *dynamic* property
10001:+- In V2.1: it's U2 (raw provenance schema). One phase, one commit. Other
10016:+Replace with: Preserve provenance, not a specific SDK call shape. … wrap it
10025:+exposes — one-step or two-step — and persists provenance fields above the
10046:+calibration on `PARTIAL_FILL_OBSERVED` events that turn out to be
10047:+`FAILED`-on-chain, the calibration corpus is poisoned. Our up-08
10064:+CONFIRMED. The risk allocator (when we get there) cannot distinguish
10065:+"capital at risk pending chain confirmation" from "capital actually
10086:+V2.1: *"Heartbeat is mandatory for any live resting-order strategy.
10095:+risk-correctness needs heartbeat.
10109:+propagation matrix asserts pUSD doesn't leak into riskguard / chain_recon /
10110:+calibration (negative tests). But it doesn't address the **buy/sell
10119:+NC-NEW-F + ZEUS_PUSD_REDEMPTION_ENABLED gates only cover the redemption
10131:+3. `venue_trade_facts` (trade/settlement)
10133:+5. `settlement_commands` (resolution/redeem)
10148:+V2.1: A1 alpha/execution benchmark + A2 risk allocator are part of the
10152:+this — calling our plan a "live-readiness gate" not "dominance plan" was
10155:+the operator's actual intent is "Zeus dominates live market" (per
10162:+### 3.1 V2.1's "V2 is P0 not low-risk"
10164:+V2.1: *"Delete: 'CLOB V2 plan structurally collapsed / low-risk drop-in.'
10167:+V2.1 OVERSTATES this. Our Down R1L1 didn't say V2 is low-risk; it said
10225:+debate** (each region has distinct attack axes: boundary/provenance vs
10232:+**Scenario A: Add `clob_token_ids_raw` column for Gamma↔CLOB provenance**
10236:+- V2.1: U2 raw provenance schema. One phase. Gamma payload, CLOB
10237:+  market-info hash, raw orderbook hash all live in one place.
10314:+   settlement_commands. Stop compressing into one CommandState.
10330:+9. **Settlement_commands ledger** (R1) — REDEEM_INTENT_CREATED →
10331:+   REDEEM_TX_HASHED → REDEEM_CONFIRMED → REDEEM_FAILED. Currently our
10332:+   K6 settlement is "out-of-scope deferred to Down".
10387:+dominate live market" than our R2. R2 is a more concrete plan for
10388:+"close the live-readiness safety bar". The merger (R3) is what the
10411:+4. **"Preserve provenance not seam" is a generalizable principle.**
10438:+4. **Provenance chain audited (Constraint #4).** Q-NEW-1 disputed → resolved by direct on-chain `eth_call` with raw hex + block height + ABI decode on disk. Earlier "marketing label" ruling overturned by ground truth. Correct provenance-interrogation template.
10453:+None. Two near-fatal risks flagged honestly with mitigations:
10502:+| down-01 | `tests/test_neg_risk_passthrough.py:66-83` | NOT VERIFIED | File exists; line range not spot-checked in this pass (pattern claimed as V1 antibody) | NONE (low risk) |
10503:+| down-03 | `tests/test_neg_risk_passthrough.py:66-83` | NOT VERIFIED | Same as above | NONE (low risk) |
10504:+| down-03 | `tests/test_polymarket_error_matrix.py:39` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10512:+| down-07 | `src/execution/harvester.py:1244-1264` | NOT VERIFIED | Not spot-checked in this pass | NONE (low risk) |
10513:+| mid-01 | `src/riskguard/riskguard.py:826` | YES | Line 826: `force_exit_review = 1 if daily_loss_level == RiskLevel.RED else 0` — confirmed writes force_exit_review | NONE |
10514:+| mid-01 | `src/riskguard/riskguard.py:1077-1094` | YES | Line 1077: `def get_force_exit_review() -> bool:` with docstring and body at 1078-1094. Content: reads force_exit_review from risk_state DB. CONFIRMED | NONE |
10516:+| mid-01 | `src/execution/executor.py:325-690` | YES | Lines 325-690 span `execute_exit_order` through the ack phase. `execute_exit_order` starts at 325; `_live_order` starts at 693. Range is accurate for the durable terminal path. | NONE |
10527:+| mid-03 | `src/execution/command_recovery.py:140-280` | NOT VERIFIED (boundary only) | Line 140 exists (inside `_resolve_one`). Range plausible for resolution table. Not full-content checked. | NONE (low risk) |
10533:+| mid-05 | `src/execution/command_recovery.py:80-92` | YES | Lines 80-92: `if state == CommandState.SUBMITTING and not cmd.venue_order_id:` + REVIEW_REQUIRED emission. F-006 case confirmed. | NONE |
10539:+| mid-06 | `src/engine/cycle_runner.py:60-102` | LINE_DRIFT | Lines 60-102 are the `_execute_force_exit_sweep` function body (Phase 9B sweep). NOT the RED-cancel boundary in the cycle loop. The RED-cancel boundary (get_force_exit_review + sweep call) is at lines 359-373. Off by ~300 lines. | LINE_DRIFT |
10540:+| up-02 | `src/contracts/settlement_semantics.py:50-183` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
10541:+| up-02 | `src/contracts/tick_size.py:91-92` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
10542:+| up-02 | `src/contracts/execution_intent.py:32-33` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
10544:+| up-03 | `src/state/db.py:813` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
10546:+| up-04 | `src/state/db.py:813` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10548:+| up-05 | `src/types/observation_atom.py:44-130` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10549:+| up-05 | `src/state/db.py:813` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10550:+| up-06 | `src/state/chain_reconciliation.py:46` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10551:+| up-06 | `src/state/chain_reconciliation.py:181` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10553:+| up-07 | `src/state/db.py:813` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
10591:+### 4. `command_recovery.py:71-77` — REVIEW_REQUIRED handoff (F-006 split anchor)
10595:+Line 71: `if state == CommandState.REVIEW_REQUIRED: return "stayed"`
10599:+Note: mid-05 cites lines 80-92 for the SUBMITTING path — those lines contain the actual REVIEW_REQUIRED emission. Lines 71-77 capture the REVIEW_REQUIRED guard + block header, consistent with card claim.
10603:+### 5. `riskguard.py:1080-1082` — "forced exit sweep is Phase 2 item"
10620:+Does NOT post sell orders in-cycle — keeps the sweep low-risk + testable.
10630:+Lines 359-373 contain `get_force_exit_review()` call and force-exit sweep. This is the RED sweep site, not an "NC-NEW-D allowlist" site. mid-06 cites lines 60-102 as the RED-cancel boundary, but lines 60-102 are the `_execute_force_exit_sweep` function definition. The actual call site is at lines 359-373.
10686:+- **NOT VERIFIED (low-risk unspot-checked)**: 6
10718:+4. **mid-06 cycle_runner.py:60-102**: This range is the force_exit_sweep function definition, not the RED-cancel entry point. The RED-cancel cycle boundary is at lines 359-373. Fix before writing relationship tests.
10745:+CLOB only as "recommended investment in conftest". Apr26 axis-31 (paper/live
10757:+mid-01 risks state "OWNERSHIP-AMBIGUITY (open R2): riskguard direct vs
10773:+| `cycle_runner.py:359-373` (mid-01) | "2-cycle latency RED→cancel via pos.exit_reason" | 359-373 = force_exit_review SWEEP — Phase-2 same-cycle force-exit ALREADY landed | STALE PREMISE |
10781:+Operator never delivers Q1 / Q-HB / Q-FX-1 / INV-29 amendment / impact_report:
10790:+Estimate: **~30-35h shippable / 120.5h = 25-29%.** "Zeus dominates live market"
10801:+4. axis-31 paper/live parity — no slice. CI green ≠ live-money safe.
10888:+**ANOMALY**: mid-03 has `depends_on: []` (no prerequisites) but mid-01.yaml lists mid-03 in its own `depends_on`. The `blocks:` field in mid-03 correctly lists mid-01/mid-04/mid-06 as downstream. The graph encodes `mid-03 --> mid-01`. However, mid-03 also carries an X3 operator gate: INV-29 amendment commit + planning-lock receipt required in mid-03's PR. This gate is NOT encoded as a `depends_on` in mid-03.yaml — it is a `critic_gate` precondition only. Mid-03 can be authored in parallel with Wave A but cannot be merged until INV-29 amendment lands. This distinction (author vs. merge gate) is not surfaced in the YAML `depends_on`, creating a latent merge-ordering risk for the executor.
10922:+- Critical risk (from mid-02.yaml risk section): if py-clob-client V2's `create_and_post_order` monolith replaces the two-step seam, mid-02's interception point disappears and this test cannot enforce the invariant. The X1 verdict is conditional, not absolute.
10930:+- **RUNNABLE as a negative-grammar test. SDK dependency on get_trades is a scope risk, not a runnability blocker for this specific test.**
10940:+| Q-FX-1 | USDC.e ↔ pUSD PnL classification operator decision | down-07 (ship_block_gate: ZEUS_PUSD_REDEMPTION_ENABLED env-flag + evidence file dual gate; runtime raises FXClassificationPending if missing) |
10945:+**Gate semantics note**: Q1-zeus-egress and Q-HB are OPERATOR-WAIT gates (engineering can proceed with authoring but cannot merge/deploy). Q-FX-1 is a RUNTIME gate (code enforces fail-closed via env-flag even after merge). INV-29 amendment is a PLANNING-LOCK gate (planning system enforces, not a YAML depends_on). The plan correctly distinguishes these semantics in prose but does NOT encode them uniformly in `depends_on` YAML fields — a latent risk if an executor reads only the YAML.
10959:+3. **mid-05 has a real scope risk on get_trades.** If py-clob-client-v2 does not expose `get_trades()`, mid-05 scopes down to a weaker sweep (open_orders + positions only). The slice card documents this risk correctly but the 14h estimate assumes the full three-SDK-call sweep. A scope reduction would change the F-006 closure claim.
10973:+Reviewer: scientist agent. Anchor: ULTIMATE_PLAN.md HEAD `874e00c`; Apr26 forensic tribunal (1544 lines). Frame: does the 20-card / 120.5h plan let Zeus "dominate live market"?
10983:+Ratio is defensible — Apr26 verdict was S0-execution-blocker, not edge-decay; closing leaks first is correct. **But** the plan's claim "Zeus is unblocked to dominate live market after 4-6 weeks" overclaims: 17 leak-closes give Zeus the **right to trade**, not measurable edge. Zero new alpha, zero alpha-monitoring. Re-label as "live-readiness gate," not "dominance."
10992:+| calibration | NOT TOUCHED |
10993:+| edge | NOT TOUCHED (no DBS-CI hardening, no edge-decay tracker) |
10995:+| monitoring | mid-06 — PARTIAL (no live-edge KPI) |
10996:+| settlement | down-07 only — PARTIAL (Apr26 §11 corpus deferred) |
10999:+Forecast/calibration/edge/learning are entirely absent. Apr26 §16 defers them; plan inherits that gap without flagging it as residual. Apr26 Phase 4 (settlement corpus, high/low split, DST resolved fixtures) silently dropped. §1 dedupe collapses Apr26 §11.1-11.4 into "data-readiness reroute" without confirming that packet exists or has owners.
11003:+Chain `51 ENS → daily max → MC → P_raw → Platt → P_cal → α-fusion → P_posterior → Edge & DBS-CI → Kelly → Size`. Plan touches **only the tail** (Size → Order). Chain is structurally intact (no module modified). Risk vectors:
11005:+1. **Schema risk.** up-04 15-col ALTER + INV-29 amendment touch `venue_commands`. If cascades into `position_events`/`execution_fact` semantics, replay-based calibration backtests become version-fragile. "Grammar-additive" claim has no antibody asserting calibration tooling reads only schema-versioned columns.
11006:+2. **Authority direction risk.** mid-01 reframes `cycle_runner` as RED→durable-cmd proxy. If RED propagation latency increases by one cycle, fractional-Kelly during DATA_DEGRADED becomes too aggressive. NC-NEW-D is shape-only, no latency assertion.
11016:+2. **Network jitter during heartbeat windows.** down-06 is Q-HB-gated. Independent of mandate, WS-reconnect jitter is the live failure mode — no WS-resubscribe correctness test, no missed-trade recovery via `get_trades` since-cursor.
11017:+3. **Oracle conflicts during settlement.** down-07 covers collateral-token PnL classification only. Apr26 §11 #3 (exchange resolution snapshot preservation, UMA dispute-window) rerouted to data-readiness — **no slice card owns it**.
11018:+4. **Cancel-failure during RED sweep.** Apr26 F-010 says runtime RED does not implement immediate cancel-sweep. mid-01 covers RED-as-authority but not RED-as-action; no behavioral test.
11025:+Plan is correct on what it covers; **under-claims its scope**. It is a live-readiness gate, not a live-market-dominance plan. Three additions needed to honestly hit the prompt:
11027:+1. **EDGE_OBSERVATION_SLICE** — alpha-decay tracker per `strategy_key` (settlement-capture / shoulder-bin / center-bin / opening-inertia) with weekly drift assertion. Apr26 §1.5 strategy-family table is the contract; no card enforces it.
11028:+2. **PROBABILITY_CHAIN_FROZEN_REPLAY** — bit-identical replay (P_raw → Size) before/after Wave A+B. Antibody against silent calibration-schema drift from up-04 ALTER.
11029:+3. **P0_TEST_OWNERSHIP_LEDGER** — cross-walk Apr26 §13 P0 list to slice cards; 5 unmapped tests (duplicate-submit, rapid partial-fill, RED cancel-all behavioral, market-close-while-resting, WS-resubscribe-recovery) need owners or explicit defer-with-risk.
11064:+5. **No `gamma_market_snapshot`, `clob_market_snapshot`, `order_command` (separate from venue_commands), `order_event`, `trade_fill`, `exchange_position_snapshot`, `reconciliation_run`, `settlement_source_snapshot`, `raw_payload`** table exists in `src/state/db.py`. F-011 / §9 raw-payload-storage IS genuinely NET_NEW.
11065:+6. **`chain_reconciliation.py` mixes three identifiers** (`token_id`, `condition_id`, `market_id`) at lines 130, 134, 670, 679, 688, 697 — verifies F-007 leakage but ALSO shows that `market_id` is used as a degraded/quarantine sentinel (line 670 comment: "market_id can collide with degraded-but-live positions"). That semantics is not captured by a "value-object translation" framing.
11067:+8. **`signature_type=2` is hardcoded** at `polymarket_client.py:76` — Apr26 §10 calls this "unacceptable for general live-money readiness". This is in scope for Region Up F-007/F-009 territory and proponent did not mention it.
11097:+- §9 provenance audit: **NET_NEW packet with the full table set** (`gamma_market_snapshot`, `clob_market_snapshot`, `order_event`, `trade_fill`, `exchange_position_snapshot`, `reconciliation_run`, `settlement_source_snapshot`) — but NOT mirror-ObservationAtom semantically; the trading atoms have lifecycle + chain-anchor, not multi-source-observation, semantics.
11105:+Files added: `src/contracts/settlement_semantics.py:1-180` (esp. lines 51, 71-78, 147-180), `architecture/city_truth_contract.yaml:1-142`, `docs/operations/current_source_validity.md:1-50`, `docs/operations/task_2026-04-26_u1_hk_floor_antibody/plan.md`. Wave-1 ingest files (wu_hourly_client 354 lines, observation_client 481, observation_instants_v2_writer 638, hourly_instants_append 519) line-counted but not deep-read; if Layer 2 needs ingest detail I'll spot-read.
11109:+1. **Weather has per-entity dispatcher pattern**: `SettlementSemantics.for_city()` at `src/contracts/settlement_semantics.py:147-180` returns `SettlementSemantics(rounding_rule, measurement_unit, resolution_source)` per city. HK alone gets `oracle_truncate` (line 171), all others `wmo_half_up` (lines 127, 142, 180). This is the canonical per-entity semantic carve-out antibody pattern — cross-module relationship enforced by dispatch shape, not by ad-hoc if-checks.
11111:+2. **Trading has NO equivalent `OrderSemantics.for_market()` dispatcher.** Polymarket per-market values that should dispatch the same way: `tick_size`, `min_order_size`, `neg_risk`, `signature_type`. Currently delegated to SDK at `polymarket_client.py:73-77` (signature_type=2 hardcoded — applies blanket to all wallets) and `:184-191` (`OrderArgs` constructed without per-market preflight). F-009 routing to "D1 SDK contract antibody" understates this — D1 catches ONE call site; the missing structure is a `for_market()` dispatcher mirror of `for_city()`.
11113:+3. **Weather has caution_flags catalog** at `architecture/city_truth_contract.yaml:101-111` (`hong_kong_explicit_caution`, `source_changed_by_date`, `website_api_product_divergence`, `airport_station_not_settlement_station`, `freshness_audit_required`). Each caution flag is REIFIED — a discrete enum-tagged carve-out documented per evidence class. **Trading has NO `polymarket_truth_contract.yaml`.** Equivalent flags that SHOULD exist: `signature_type_per_wallet_required`, `neg_risk_market_only_specific_pricing`, `tick_size_per_market_varies`, `clob_endpoint_drift_after_v3`, `gamma_active_clob_closed_divergence`. None captured.
11121:+Proponent-up framed F-007 as identifier translation. The actual transferable antibody from weather is a **per-market dispatcher** (`OrderSemantics.for_market()`) that returns `(tick_size, min_order_size, neg_risk, signature_type, freshness_window, clob_endpoint_id)` — same shape as `SettlementSemantics.for_city()`. This subsumes F-009 (precision/tick), F-007 (boundary leak via forced gate-on-dispatch), and Apr26 §10's signature_type concern. Value-object framing is the WRONG abstraction; dispatcher framing is the antibody that already exists in weather and can be replicated.
11125:+Trading needs an `architecture/polymarket_truth_contract.yaml` analogue with `caution_flags` + `forbidden_inferences` + `evidence_classes` mirroring `city_truth_contract.yaml`. Without this, every per-market exception (negRisk, GTD, FOK, signature_type) lives in human memory. Proponent's plan does not mention this layer. This is the structural decision that makes F-007 + F-008 + F-009 + F-010 all fall out as enforced consequences (Fitz Constraint #1: K=1 dominates K=4).
11146:+Region: Up (boundary + provenance + raw payloads)
11156:+| `src/types/observation_atom.py` | 1-130 | Reference shape: weather first-class provenance atom. |
11158:+| `src/contracts/settlement_semantics.py` | 1-183 | Per-market resolution rules (rounding/precision); NOT a boundary id seam. |
11161:+| `src/state/schema/v2_schema.py` | 1-540 | v2 schema; NO trading-side raw-payload tables (gamma/clob/order_command/order_event/trade_fill/exchange_position_snapshot/reconciliation_run/settlement_source_snapshot). |
11167:+| `src/state/db.py` | 160-790 | DB-level `authority TEXT CHECK(...)` constraints on observations/decisions/calibration_pairs/rescue_events (lines 166,225,303,330,368); `source TEXT NOT NULL` columns (189,235,587,604,618,649,713); `data_source_version TEXT` (223,724); `raw_response TEXT` on observation_instants (634-635). |
11168:+| `src/data/daily_obs_append.py` | 471-985 | Generic ingest path serving HKO + WU + Ogimet — explicit `source=`, `data_source_version=`, `authority="VERIFIED"` per atom write. Confirms HKO is generic-path, NOT a standalone module. Each city's source-tag is computed via `target.source_tag` (~line 1013-1023). |
11169:+| `architecture/city_truth_contract.yaml` | 1-80 | FORMALIZED source-role contract schema: 4 source roles (`settlement_daily_source` / `day0_live_monitor_source` / `historical_hourly_source` / `forecast_skill_source`) each with required `evidence_refs` + `freshness_class`. Schema-level provenance for the WEATHER side. No trading-side equivalent. |
11177:+1. **Weather provenance IS first-class.** `ObservationAtom` (`src/types/observation_atom.py:44-96`) is a `@dataclass(frozen=True)` with mandatory `source`, `api_endpoint`, `station_id`, `fetch_utc`, `rebuild_run_id`, `data_source_version`, `authority: Literal["VERIFIED","UNVERIFIED","QUARANTINED"]`. `__post_init__` (lines 98-116) raises `IngestionRejected` if `validation_pass=False` or authority/validation mismatched — invalid atoms are UNCONSTRUCTABLE. This is the canonical antibody shape.
11187:+6. **Settlement semantics is per-market resolution rules, not a boundary id seam.** `SettlementSemantics` (`src/contracts/settlement_semantics.py:50-183`) owns `precision`, `rounding_rule`, `measurement_unit`, `finalization_time`. It is NOT a candidate for housing the polymarket-id translation — that needs a separate `MarketIdentity` value-object.
11191:+8. **§9 missing-tables claim is REAL even though its citation is fabricated.** Grep of `v2_schema.py:48-540` shows NO `gamma_market_snapshot`, NO `clob_market_snapshot`, NO `order_command`, NO `order_event`, NO `trade_fill`, NO `exchange_position_snapshot`, NO `reconciliation_run`, NO `settlement_source_snapshot`. Existing tables are weather/calibration/lifecycle only.
11193:+9. **`ExecutionIntent` is the boundary type missing both ID-seam discipline and provenance.** `src/contracts/execution_intent.py:32-33` has `market_id: str` + `token_id: str` as bare strings — no validation that they refer to the same market, no source/fetch_utc, no provenance. This is the trading equivalent of "passing temperature_value as a bare float without unit". Same class of failure as Fitz Constraint #4.
11206:+| `provenance_audit file_line_in_review: 737` | Same — "§9" not in review; review has no §9. |
11224:+**Q2 (F-011 raw-payload first-class):** NET_NEW upstream packet, NOT a D2.D add-on. Fitz Constraint #4: provenance is its own dimension. D2.D is a transport swap (`py-clob-client` v0.34 → newer SDK). Bolting provenance into a transport-replacement PR conflates concerns and forfeits the antibody. The structural decision is: introduce a `TradingPayloadAtom` (mirror of `ObservationAtom`) BEFORE D2.D so the SDK swap lands on a typed boundary. Schema add: `raw_signed_order_jsonb`, `raw_post_order_response_jsonb`, `raw_orderbook_snapshot_jsonb`, `raw_gamma_snapshot_jsonb`, `fetch_utc`, `sdk_version`, `signature_type`, `authority`. Bolted onto venue_commands + new orderbook/gamma snapshot tables. Note: F-001/F-003 STRUCTURE is already shipped (INV-28/INV-30); only PAYLOAD is residual — collapses cleanly into F-011.
11226:+**Q3 (§9 weather/trading provenance asymmetry):** GREP-VERIFIED. Weather IS first-class. Trading is NOT. The same shape IS achievable architecturally because we have a reference implementation (`ObservationAtom` + `IngestionGuard.validate()` + `raw_response TEXT` column pattern) to mirror. This makes F-011 STRUCTURAL upstream, achievable without major surgery — small new module + new schema rows + opt-in writes — not a multi-month architecture change.
11230:+- **U-DEC-2:** Trading payload first-class (`TradingPayloadAtom` + raw-payload schema add) — discharges F-011 + provenance_audit + the residual payload columns from F-001/F-003 + the `sdk_version`/`signature_type` from §8.3 transitions + Apr26 §9 as a whole.
11232:+Wave-2 B7 (B4-legacy-quarantine, ~1h) and B8 (B4-source-binding, gated) sit DOWNSTREAM of U-DEC-2: B7 = quarantine inherited rows whose provenance fails authority check; B8 = bind weather-side source tags to trading-side `TradingPayloadAtom`. Both depend on U-DEC-2 landing first.
11239:+- (b) "TradingPayloadAtom is YAGNI — D2.D will introduce SDK v3 which natively returns provenance objects."
11240:+- (c) "Weather had multi-source ambiguity (WU/HKO/IEM/ECMWF); trading is single-source (Polymarket only) — the same antibody isn't needed."
11250:+11. **DB-level `authority` is enforced by CHECK constraints, not just by Python.** `src/state/db.py:166,225,303,330,368` show `authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED'))` on observations / decisions / calibration_pairs / rescue_events. SQLite refuses to write a non-enum authority value. This is a SECOND-LAYER antibody beyond the Python frozen-dataclass — provenance is enforced at the storage seam, not just at the construction seam. Trading-side `venue_commands` schema (`state/db.py:init_schema`) has NO authority column at all.
11254:+13. **HKO is generic-path, not a standalone module.** `src/data/daily_obs_append.py:471-499,929-985` confirms HKO ingestion uses the same `_write_atom_with_coverage()` helper as WU + Ogimet, with `source=HKO_REALTIME_SOURCE` / `HKO_SOURCE` / `HKO_OPENDATA_SOURCE`, `data_source_version="hko_rhrread_accumulated_v1"` / `"hko_opendata_v1_2026"`, `authority="VERIFIED"`. The pattern is: source-tag + version + authority, on EVERY atom write — codified by `_write_atom_with_coverage` (`daily_obs_append.py:543-580`). Trading-side has no equivalent funnel.
11256:+14. **`architecture/city_truth_contract.yaml` formalizes source-roles for the weather side at the SCHEMA level.** Four roles: `settlement_daily_source`, `day0_live_monitor_source`, `historical_hourly_source`, `forecast_skill_source`. Each requires `source_family`, `station_or_product`, `evidence_refs`, `freshness_class`. The contract is enforced by the `freshness_class` + `evidence_refs` fields and the `volatile_assertion_policy` block (lines 30-40). Trading-side has no manifest of upstream roles. Per Fitz Constraint #2, the manifest is the place where intent survives translation loss — its absence on the trading side is exactly the architectural asymmetry F-011 calls out.
11260:+The supplementary evidence STRENGTHENS U-DEC-2. The weather provenance kernel is THREE LAYERS:
11269:+- `architecture/trading_provenance_contract.yaml` declaring upstream roles (Gamma, CLOB, Data-API, Chain) with `evidence_refs`.
11279:+- `up-05` U-DEC-2.C: `architecture/trading_provenance_contract.yaml` — manifest of trading upstream roles. depends_on: up-03.
11282:+- `up-08` Wave-2 B8: weather→trading source-binding (link condition_id ↔ city for settlement). depends_on: up-04, up-05.
11315:+| up-02 | `src/contracts/order_semantics.py::OrderSemantics.for_market()` dispatcher (mirror of `src/contracts/settlement_semantics.py:147-180`) | F-007 + F-009 + signature_type=2 hardcoding at `polymarket_client.py:76` |
11359:+- **Wave-2 B8** (weather→trading source-binding: condition_id ↔ city for settlement) → deferred Layer 2/3.
11368:+- 1 of 3 weather-kernel layers transfers cleanly: **Layer 2 SQLite CHECK constraint pattern** — `state TEXT CHECK(state IN ('DRAFT','SIGNED','SUBMITTED','MINED','CONFIRMED','REORGED'))` is same antibody shape, constraining lifecycle enum instead of authority enum.
11369:+- 1 of 3 transfers with refactor: **Layer 3 manifest** — `architecture/trading_provenance_contract.yaml` (= up-01) lists 4 upstream roles (Gamma, Data-API, CLOB, Chain) with authority TIERS + freshness/finality classes. Encodes opponent-up's authority-tier insight as a contract.
11380:+- R2 market→city resolution: one-many (one settlement city) or many-many (synthetic markets)?
11404:+Region: Up (boundary + provenance + raw payloads)
11405:+Layer: 2 (cross-module data-provenance / relationship invariants)
11425:+| up-06 | UNVERIFIED rejection matrix (7 consumers) | [] **LANDS FIRST** | per-consumer guard (chain_recon, fill_tracker, harvester, cycle_summary, riskguard, executor.preflight, calibration training) |
11434:+| L2-3 | "UNVERIFIED matrix incomplete (4-row)" | 7-row matrix in up-06 (added: risk_guard, executor.preflight, calibration training). |
11457:+- **NC-NEW-C**: No `ClobClient.create_order()` outside allowlist (`src/contracts/order_semantics.py` + `src/data/polymarket_client.py` + `scripts/live_smoke_test.py`). (semgrep `zeus-create-order-via-order-semantics-only`)
11490:+Region: Up (boundary + provenance + raw payloads)
11511:+| A2 | NC-NEW-C allowlist false positive (live_smoke_test) | CONCEDE | Final allowlist: `src/contracts/order_semantics.py + src/data/polymarket_client.py + tests/**/*.py` only. live_smoke_test.py never calls create_order (verified grep) |
11513:+| A4 | Append-only gate bypassable (semgrep-only) | CONCEDE | Defense-in-depth: SQLite triggers (BEFORE DELETE + BEFORE UPDATE w/ RAISE(ABORT)) + semgrep + Python-side guard. Template: db.py:1064 settlements_authority_monotonic |
11516:+| A7 | PrecisionAuthorityConflictError fallback | CONCEDE | Per-market quarantine, NOT cycle abort. cycle_runner per-market try/except (existing pattern at cycle_runner.py:237 run_cycle, 11 existing try-blocks). riskguard NOT escalated (60s separate cycle). cycle_summary surfaces conflict count via up-06 row 4 |
11524:+| PrecisionAuthorityConflictError operator-fallback | Single-market quarantine via existing per-market try/except in cycle_runner. snapshot row authority_tier='UNVERIFIED' for forensic. cycle_summary surfaces UNVERIFIED count + conflict count (up-06 row 4). NOT cycle abort. NOT riskguard escalation. ops alert via existing observability seam. |
11552:+- (Note: live_smoke_test.py NOT in exclude — calls place_limit_order, not create_order; covered by NC-16.)
11560:+| up-02 | `src/contracts/settlement_semantics.py:120-182` (narrowed from 50-183) | ✓ (classmethod cluster: default_wu_fahrenheit @120, default_wu_celsius @133, for_city @147) |
11568:+| up-06 | `src/state/chain_reconciliation.py:46` | ✓ (`LEARNING_AUTHORITY_REQUIRED = "VERIFIED"`) |
11570:+| up-06 | `src/riskguard/riskguard.py:51` (UNVERIFIED row 5) | ✓ (`_get_runtime_trade_connection`) |
11599:+    "market_id","tick_size","min_size","signature_type","neg_risk"
11609:+    OrderSemantics.for_market("0xDEADBEEF")
11666:+# test_calibration_excludes_unverified_position_events
11667:+events = load_position_events_for_training(conn)
11726:+- finality_window_blocks=256 calibration (Polygon-specific; may tune against historical reorg depth).
11763:+| X2 | F-012 RED-test audit (20 cards) | 17 Class A + 3 Class B + 0 violators | Class B mints lighter text-artifact antibodies for down-02/04/05; F-012 fully closed |
11765:+| X4 | F-011 raw-payload coverage (X-UD-1 fold) | FULLY COVERED | Up cards via up-03 + up-04 + up-05; up-03 EXTENDS with `raw_orderbook_jsonb` for orderbook depth at decision time. Replay test as F-011 acceptance |
11850:+Are A1 (cycle_runner mid-01 RED-cancel emission) + mid-02 (signed_order_hash interception) + Down's D2.A (V2 SDK swap) parallelizable, given the X-MD-1 cross-region risk that V2 SDK might collapse the create_order/post_order seam?
11899:+# X2 — F-012 RED-Test Audit Across All 20 Slice Cards
11911:+Apr26 review F-012 demands every slice has a RED-test (failure-mode antibody, not happy-path-only). Audit 20 slice cards (up-01..07 + mid-01..06 + down-01..07); identify violators; mint RED-tests for any uncovered.
11915:+**3-CLASS CLASSIFICATION** — 17 Class A code-slices with RED-test (PASS); 3 Class B text/process-slices (need lighter antibody mint); 0 Class C F-012 violators after mint.
11917:+## Class A — Code-slice with RED-test (17 cards, all PASS)
11919:+| Card | RED-test (sample) |
11926:+| up-06 | test_unverified_rejected_at_every_consumer (7-row matrix all RED) |
11928:+| mid-01 | test_red_emit_grammar_bound_to_cancel_or_derisk_only (raises ValueError on ENTRY/EXIT) |
11957:+- 20/20 slice cards have RED-test antibodies (17 code-tests + 3 text-tests)
11959:+- Future implementation packets cannot ship a slice without its RED-test attached
11963:+- Slice ships without RED-test (immediate F-012 reopener)
11969:+- Apr26 review F-012 (Zeus_Apr26_review.md:F-012 — "Tests prove happy paths, not live-money safety")
11970:+- `feedback_critic_prompt_adversarial_template` — RED-tests are the adversarial closure
12110:+**FULLY COVERED** by Up cards after R3L3 raw_orderbook_jsonb extension to up-03.
12118:+| raw CLOB market snapshot | up-03 ExecutableMarketSnapshot | `raw_clob_snapshot_jsonb` (market info: authority_tier, fee_rate_bps, tick_size, min_order_size, neg_risk) |
12184:+- Splitting F-011 across Up and Down regions (would create cross-module integrity risk)
12193:+- Fitz Constraint #4: data provenance > code correctness — column existence ≠ content semantics
12226:+| Up    | 2 R2L2 | dispatched | data-provenance | active (sequential lead) | 5 minted; may grow |
12236:+- Total findings: 31 (F-001..F-012 + 17 §8.3 transitions + schema_event_v1 + provenance_audit)
12247:+- Wave 1 closed: G6 `task_2026-04-26_g6_live_safe_strategies/`, G10-scaffold `task_2026-04-26_g10_ingest_scaffold/`, G10-helper-extraction `task_2026-04-26_g10_helper_extraction/`, B4-physical-bounds `task_2026-04-26_b4_physical_bounds/`, B5-DST `task_2026-04-26_b5_dst_antibody/`, U1 `task_2026-04-26_u1_hk_floor_antibody/`.
12256:+- mid-01 A1 RED→durable-cmd
12282:+- §8.3 17 transitions reduce: 4 already exist (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION) → 11 missing → opponent-mid K=6 (K1 payload-bind, K2 error-typing, K3 book-resting grammar, K4 cancel-failure terminal, K5 exchange-recon, K6 settlement-redeem).
12345:+- "TIGGE archive returns GRIB2 with parameter table 4"
12373:+  to CANCELED/FILLED/EXPIRED" — what about REVIEW_REQUIRED? Released or
12405:+3. If symbol is GONE (renamed / deleted / refactored): RED drift. STOP.
12470:+**Trigger**: Your phase's `deliverables.extended_modules` lists files A,
12694:+| 4 | **Multi-agent disagreement on ambiguous spec** — Z4 says "pUSD vs CTF" but doesn't define wrapped-CTF | Ambiguity gate before phase entry | `deep-interview` skill mandatory for HIGH-risk phases |
12701:+| 11 | **Decimal precision drift** — Decimal at write, float at read, calibration corrupts silently | Type-checked I/O via dataclass + mypy strict on src/state/ | `mypy --strict` CI gate |
12727:+11. **Optional but recommended**: dispatch `deep-interview` skill if the phase has any genuine ambiguity (HIGH-risk phases MUST do this).
12843:+6. **Boundaries**: "DO NOT modify files outside the phase's `deliverables.extended_modules` list. If you need to, write `_cross_phase_question.md` and ask the user."
12846:+9. **Failure modes specific to this phase** (lifted from card's risk: section).
12861:+| Boot — ambiguity gate (HIGH-risk phases) | `deep-interview` | force precision before code |
12869:+| External SDK fact-check | `document-specialist` + WebFetch | V2 SDK source / TIGGE docs / Polymarket docs |
12871:+| Pre-merge — review | `code-reviewer` + `critic` (HIGH-risk only) | severity-rated review + adversarial |
12878:+For HIGH-risk phases (Z1-Z4, U2, M1-M5, R1, T1, A1, A2, G1), critic-opus
12889:+2. **Antibody liveness**: for every NC-NEW + INV-NEW with `Status: LIVE`
12903:+- RED: SEMANTIC_MISMATCH or antibody fail (blocking).
12967:+- No phase merges if drift_check is RED.
13033:+- Q-FX-1 (Z4) parallelizable with TIGGE-ingest go-live (F3) — different
13051:+### F-2: Antibody fails after merge (drift_check RED)
13082:+- Is NC-NEW-G provenance-not-seam ACTUALLY captured (versus a thin wrapper
13098:+- It is NOT a guarantee against drift. It REDUCES drift; it doesn't eliminate.
13130:+- `INVARIANTS_LEDGER.md` — cross-phase invariant tracker (lives, updated by CI)
13162:+- `RETIRED` — invariant amended out by a later planning-lock event
13186:+| NC-NEW-D | M1 | cycle_runner._execute_force_exit_sweep is SOLE caller of insert_command(IntentKind.CANCEL,...) within cycle_runner.py | tests/test_riskguard_red_durable_cmd.py::test_red_emit_sole_caller_is_cycle_runner_force_exit_block | — | — | PENDING |
13189:+| NC-NEW-G | Z2 | Provenance pinned at VenueSubmissionEnvelope, NOT specific SDK call shape | tests/test_v2_adapter.py::test_one_step_sdk_path_still_produces_envelope_with_provenance + test_two_step_sdk_path_produces_envelope_with_signed_order_hash + semgrep `zeus-v2-placement-via-adapter-only` | — | — | PENDING |
13190:+| NC-NEW-H | U2 | Calibration training filters venue_trade_facts WHERE state='CONFIRMED' | tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only | — | — | PENDING |
13191:+| NC-NEW-I | U2 | Risk allocator separates OPTIMISTIC_EXPOSURE from CONFIRMED_EXPOSURE | tests/test_risk_allocator.py::test_optimistic_vs_confirmed_split_in_capacity_check | — | — | PENDING |
13192:+| NC-NEW-J | F3 | TIGGEIngest.fetch() raises TIGGEIngestNotEnabled when gate closed; open-gate fetch reads only operator-approved local JSON payloads | tests/test_tigge_ingest.py::test_tigge_fetch_raises_when_gate_closed + tests/test_tigge_ingest.py::test_tigge_open_gate_without_payload_configuration_fails_closed + tests/test_forecast_source_registry.py::test_tigge_gate_open_routes_through_ingest_not_openmeteo | — | — | PENDING |
13194:+| INV-NEW-A | Z1 | No live submit when CutoverGuard.current_state() != LIVE_ENABLED | tests/test_cutover_guard.py::test_executor_raises_cutover_pending_when_freeze | — | — | PENDING |
13195:+| INV-NEW-B | Z2 | Every submit() produces a VenueSubmissionEnvelope persisted via venue_command_repo BEFORE side effect | tests/test_v2_adapter.py::test_create_submission_envelope_captures_all_provenance_fields | — | — | PENDING |
13197:+| INV-NEW-D | Z4 | Reserved tokens released atomically when sell command transitions to CANCELED/FILLED/EXPIRED | tests/test_collateral_ledger.py::test_release_reservation_on_cancel_or_fill | — | — | PENDING |
13199:+| INV-NEW-F | U2 | Every fact has source + observed_at + local_sequence | tests/test_provenance_5_projections.py::test_local_sequence_monotonic_per_subject + test_source_field_required_on_every_event | — | — | PENDING |
13205:+| INV-NEW-L | R1 | Settlement transitions are durable + crash-recoverable; REDEEM_TX_HASHED is recovery anchor | tests/test_settlement_commands.py::test_redeem_crash_after_tx_hash_recovers_by_chain_receipt | — | — | PENDING |
13206:+| INV-NEW-M | T1 | Paper-mode runs go through SAME PolymarketV2Adapter Protocol; FakePolymarketVenue and live adapter produce schema-identical events | tests/integration/test_p0_live_money_safety.py::test_paper_and_live_produce_identical_journal_event_shapes | — | — | PENDING |
13208:+| INV-NEW-O | F2 | Calibration retrain consumes ONLY venue_trade_facts WHERE state='CONFIRMED' | tests/test_calibration_retrain.py::test_arm_then_trigger_consumes_confirmed_trades_only | — | — | PENDING |
13209:+| INV-NEW-P | F2 | Calibration param promotion to live REQUIRES frozen-replay PASS | tests/test_calibration_retrain.py::test_frozen_replay_failure_blocks_promotion | — | — | PENDING |
13210:+| INV-NEW-Q | A1 | No strategy promoted to live without StrategyBenchmarkSuite.promotion_decision() = PROMOTE | tests/test_strategy_benchmark.py::test_promotion_blocked_unless_replay_paper_shadow_all_pass | — | — | PENDING |
13211:+| INV-NEW-R | A2 | Kill switch trips on threshold breach (configurable) | tests/test_risk_allocator.py::test_kill_switch_blocks_all_submits | — | — | PENDING |
13212:+| INV-NEW-S | G1 | LIVE deploy requires 17/17 G1 gate PASS + ≥1 staged-live-smoke environment | scripts/live_readiness_check.py exit code 0 | — | — | PENDING |
13242:+- This ledger is the SINGLE SOURCE OF TRUTH for invariant liveness across all 20 R3 phases.
13265:+1. **Trade live, real money on Polymarket V2** with no silent S0 losses.
13266:+2. **Dominate the live market** — alpha + execution + risk allocation
13269:+3. **Ingest TIGGE / additional forecast sources + retrain calibration**
13289:+                                                                              U2 raw-provenance-schema (5 projections)
13299:+                                                                              R1 settlement-ledger  →  T1 fake-venue
13301:+                                                            F1 forecast-pipeline-plumbing  ──→  F2 calibration-loop-wiring  ──→  F3 tigge-ingest-stub
13303:+                                                                              A1 benchmark-harness  →  A2 risk-allocator
13305:+                                                                              G1 live-readiness-gates
13346:+15. After implementation: dispatch `code-reviewer` + (HIGH-risk only)
13358:+| Q1-zeus-egress | Z2 (production cutover) | `evidence/q1_zeus_egress_<date>.txt` host probe from Zeus daemon machine | engineering proceeds, cutover BLOCKED |
13362:+| TIGGE-ingest go-live | F3 (TIGGE active ingest) | `evidence/tigge_ingest_decision_<date>.md` + ZEUS_TIGGE_INGEST_ENABLED env flag + local JSON payload path (`payload_path:` or ZEUS_TIGGE_PAYLOAD_PATH) | code path wired, ingest disabled |
13363:+| Calibration retrain go-live | F2 (calibration update) | `evidence/calibration_retrain_decision_<date>.md` + operator dispatch | engine reads existing Platt params; no retrain |
13365:+| impact_report rewrite | Z0 (Phase 0.F critic gate) | `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/v2_system_impact_report.md` rewritten with marketing-label disclaimer | engineering proceeds, but live placement BLOCKED |
13392:+  RED-emission within `cycle_runner.py`.
13401:+- **NC-NEW-G** `provenance-not-seam` — pin `VenueSubmissionEnvelope`
13403:+- **NC-NEW-H** `matched-not-confirmed` — calibration training paths
13405:+  rows for training raises ValueError.
13406:+- **NC-NEW-I** `optimistic-vs-confirmed-exposure` — risk allocator
13409:+- **NC-NEW-J** `tigge-ingest-flag-gate` — `TIGGEClient.fetch()` raises
13410:+  unless `ZEUS_TIGGE_INGEST_ENABLED=1` AND operator decision file
13451:+- `reference_excerpts/<topic>_<date>.md` — frozen excerpts of external docs (V2 SDK source, TIGGE access, etc.)
13473:+- R2 up-06 → R3 G1 (UNVERIFIED rejection matrix becomes a live-readiness gate)
13476:+- R2 mid-01 → R3 M1 (RED→durable-cmd, NC-NEW-D function-scope antibody preserved)
13497:+- F1/F2/F3 forecast plumbing added (TIGGE wired + calibration loop)
13505:+When G1 live-readiness gates all pass:
13506:+- Every order Zeus places is reconstructable from raw payload provenance.
13509:+- Every fill is split into MATCHED → MINED → CONFIRMED with calibration
13513:+- Paper and live use the SAME state machine via T1 fake venue.
13515:+- A2 risk allocator caps capital deployment per market / event /
13517:+- F1/F2/F3 forecast pipeline is wired so operator can flip local TIGGE ingest
13518:+  + calibration retrain switches without code changes; external TIGGE archive
13521:+That is the minimum infrastructure for "Zeus dominates live market"
13830:+| Implementation — write code | `executor` | opus for HIGH risk, sonnet otherwise | focused implementation |
13835:+| External SDK / docs | `document-specialist` + WebFetch | sonnet | sole source for V2 SDK, TIGGE, Polymarket docs |
13847:+### Z0 plan-lock (low risk, doc-only)
13853:+### Z1 CutoverGuard (HIGH risk, state machine + ops)
13859:+### Z2 V2 adapter + VenueSubmissionEnvelope (HIGH risk, external SDK)
13863:+- Pre-merge: `critic` (NC-NEW-G spirit check) + `code-reviewer` + `security-reviewer` (live placement surface).
13865:+### Z3 HeartbeatSupervisor (HIGH risk, async)
13871:+### Z4 CollateralLedger (HIGH risk, multi-asset semantics)
13877:+### U1 ExecutableMarketSnapshotV2 (medium risk, append-only DB)
13883:+### U2 5-projection schema (HIGH risk, schema split)
13886:+- Tests: `test-engineer` + provenance chain reconstructability test.
13889:+### M1 Lifecycle grammar (HIGH risk, planning-lock)
13895:+### M2 SUBMIT_UNKNOWN_SIDE_EFFECT (HIGH risk, exception classification)
13901:+### M3 User-channel WS (HIGH risk, async + external)
13907:+### M4 Cancel/replace + exit safety (HIGH risk, mutex)
13913:+### M5 Exchange reconciliation sweep (HIGH risk, large module)
13919:+### R1 Settlement command ledger (medium risk, chain ops)
13925:+### T1 Fake venue (HIGH risk, large test infra)
13928:+- Tests: `test-engineer` — fake venue and live adapter MUST produce schema-identical events.
13929:+- Pre-merge: `critic` (paper/live parity spirit check) + `code-reviewer`.
13931:+### F1 Forecast source registry (medium risk, pluggable system)
13937:+### F2 Calibration retrain loop (HIGH risk, math + corpus)
13943:+### F3 TIGGE ingest stub (medium risk, dormant code path)
13944:+- Boot: `document-specialist` — capture TIGGE archive access docs to `reference_excerpts/tigge_archive_access.md`.
13949:+### A1 StrategyBenchmarkSuite (HIGH risk, large module)
13955:+### A2 RiskAllocator + PortfolioGovernor (HIGH risk, sizing math)
13961:+### G1 Live readiness gates (medium risk, orchestration)
13963:+- Impl: `executor`. New `scripts/live_readiness_check.py` runs all 17 gates.
13999:+- DO NOT use `general-purpose` for HIGH-risk phases unless ambiguity has been resolved.
14012:+- TIGGE ingest go-live decision.
14013:+- Calibration retrain go-live decision.
14027:+Created: 2026-04-26 (post multi-review + V2.1 merger + user directive on TIGGE/training operator gates)
14029:+Decomposition: lifecycle phases (Z foundation → U snapshot/provenance → M execution → R settlement → T parity → F forecast → A strategy/risk → G gates)
14040:+## §0 What "live + dominate" means
14042:+Per original prompt: **after R3 implementation, Zeus must be able to (i) trade live real money on Polymarket V2 with no silent S0 losses and (ii) dominate the live market — alpha + execution + risk wired so edge can compound.** Per user directive 2026-04-26: **data ingest + training (e.g., TIGGE) is operator-decision; wiring must be ready.**
14046:+- A1 + A2 close the dominance bar (no strategy goes live without StrategyBenchmarkSuite PROMOTE; capital deployment bounded by RiskAllocator).
14047:+- F1 + F2 + F3 wire the data + training plumbing — TIGGE ingest path exists but is gated by operator decision artifact + env flag (NC-NEW-J).
14069:+                                                                                                R1 settlement      T1 fake-venue
14071:+                                                                F1 forecast-source-registry ────→ F2 calibration-retrain ←─ A1 benchmark-suite
14073:+                                                                F3 TIGGE-ingest-stub
14075:+                                                                                                                       A2 risk-allocator
14077:+                                                                                                            G1 live-readiness-gates
14091:+- **Z0** plan-lock + source-of-truth rewrite (4h, low risk) — Doc-only slice. Replace stale inactive-tracker language with `V2_ACTIVE_P0`, rewrite `impact_report` v2 with falsified-premise disclaimers (8 R2 multi-review premises), add `polymarket_live_money_contract.md` listing the 8 V2 invariants. CI grep enforces no stale language.
14092:+- **Z1** CutoverGuard (12h, high risk, critic-opus) — Replace operator-runbook with code: state machine NORMAL → PRE_CUTOVER_FREEZE → CUTOVER_DOWNTIME → POST_CUTOVER_RECONCILE → LIVE_ENABLED. Operator-token-signed transitions only; runtime gate prevents live submit when state ≠ LIVE_ENABLED.
14093:+- **Z2** V2 strict adapter + VenueSubmissionEnvelope (18h, high risk, critic-opus) — `src/venue/polymarket_v2_adapter.py` is the ONLY live placement surface. Pin envelope (provenance), not seam (NC-NEW-G). Replaces R2 X1 seam-pinning. Removes `py-clob-client` from live deps.
14094:+- **Z3** HeartbeatSupervisor MANDATORY (12h, high risk, critic-opus) — Async coroutine + placement gate. GTC/GTD blocked when `health != HEALTHY`. Reuses existing apscheduler tombstone (NC-NEW-F single-tombstone preserved). Promoted from R2 down-06 D2-gated to required.
14095:+- **Z4** CollateralLedger (22h, high risk, critic-opus) — Replaces R2 down-07 `balanceOf` rewire with multi-asset ledger: pUSD balance + allowance + CTF token balance per outcome + reserved buy/sell sizes + wrap/unwrap durable commands + legacy USDC.e classification. NC-NEW-K: `sell_preflight` cannot substitute pUSD for token inventory.
14097:+### U phase — Snapshot + provenance (2 cards, 42h)
14099:+- **U1** ExecutableMarketSnapshotV2 (14h, medium risk, critic-opus) — Append-only table (NC-NEW-B preserved via SQLite triggers). Every venue_command MUST cite a fresh snapshot whose token id / tick / min size / fee / neg_risk match intent. Freshness gate at `venue_command_repo.insert_command` (Python single-insertion-point). Replaces R2 up-03 + up-07.
14100:+- **U2** Raw provenance schema — 5 distinct projections (28h, high risk, critic-opus) — Splits R2's compressed CommandState grammar into 5 tables: `venue_commands` (intent+submit) + `venue_order_facts` (RESTING/MATCHED/PARTIALLY_MATCHED/CANCEL_*) + `venue_trade_facts` (MATCHED/MINED/CONFIRMED/RETRYING/FAILED) + `position_lots` (OPTIMISTIC vs CONFIRMED exposure) + `venue_submission_envelopes`. NC-NEW-H: calibration training filters `WHERE state='CONFIRMED'`. NC-NEW-I: risk allocator separates OPTIMISTIC vs CONFIRMED.
14104:+- **M1** Lifecycle grammar (14h, high risk, critic-opus) — INV-29 amendment commit + planning-lock receipt required to merge. Extends CommandState with INTENT_CREATED / SNAPSHOT_BOUND / SIGNED_PERSISTED / POSTING / POST_ACKED / SUBMIT_UNKNOWN_SIDE_EFFECT etc. cycle_runner-as-proxy lock for RED→durable-cmd (NC-NEW-D function-scope antibody). RESTING NOT in CommandState (NC-NEW-E).
14105:+- **M2** Unknown-side-effect semantics (10h, high risk, critic-opus) — Replace `status='rejected'` for post-POST exceptions with `unknown_side_effect`. NC-19 idempotency_key dedup + economic-intent fingerprint. Reconciliation converts unknown → ACKED/FILLED/SAFE_REPLAY_PERMITTED.
14106:+- **M3** User WebSocket ingest + REST fallback (16h, high risk, critic-opus) — `src/ingest/polymarket_user_channel.py`. WS gap detection → forces M5 sweep before new submit. Closes Apr26 axis-45 + axis-24. Replaces R2 mid-07 decision-slice with WS-first explicit.
14107:+- **M4** Cancel/replace + exit safety (12h, high risk, critic-opus) — Mutex per (position, token) + typed CancelOutcome parser. Exit preflight uses Z4 token reservations, not pUSD. CANCEL_UNKNOWN blocks replacement.
14108:+- **M5** Exchange reconciliation sweep (18h, high risk, critic-opus) — Bulk diff exchange truth vs journal. `exchange_reconcile_findings` table records ghost-orders / orphans / unrecorded-trades / position-drift / heartbeat-suspected-cancel / cutover-wipe. Findings → operator review queue (closes R2 multi-review architect's "antibody-without-actuator" liability).
14112:+- **R1** Settlement / redeem command ledger (12h, medium risk, critic-opus) — `settlement_commands` table with REDEEM_INTENT_CREATED → REDEEM_SUBMITTED → REDEEM_TX_HASHED → REDEEM_CONFIRMED. Crash-recoverable via tx_hash recovery. K6 deferral from R2 lands here.
14114:+### T phase — Paper/live parity (1 card, 22h)
14116:+- **T1** FakePolymarketVenue (22h, high risk, critic-opus) — Implements PolymarketV2Adapter Protocol exactly. Failure-injection knobs: TIMEOUT_AFTER_POST / NETWORK_JITTER / ORACLE_CONFLICT / RESTART_MID_CYCLE / HEARTBEAT_MISS / OPEN_ORDER_WIPE / CANCEL_NOT_CANCELED / etc. Closes Apr26 axis-31 + axis-49 + 5 unowned P0 tests. Paper and live use SAME adapter Protocol.
14122:+- **F1** Forecast source registry (12h, medium risk, critic-opus) — Typed source registry: existing primary sources (ECMWF open data, openmeteo, etc.) ungated; new sources (TIGGE, GFS) gated by operator-decision artifact + env flag. NC-NEW-J: gated source raises `SourceNotEnabled`. Extends `src/data/forecasts_append.py` to persist `source_id` + `raw_payload_hash` + `authority_tier` per row.
14123:+- **F2** Calibration retrain loop (14h, high risk, critic-opus) — Operator-armed retrain trigger (operator token + evidence file required). Frozen-replay harness asserts P_raw → Size is bit-identical pre/post on 3 fixture portfolios (R2 up-08 carries forward). Drift detection blocks promotion. New `calibration_params_versions` table holds versioned params.
14124:+- **F3** TIGGE ingest stub (6h, medium risk, critic-opus) — Registered in F1 registry with `tier='experimental'` + dual-gate (artifact + ZEUS_TIGGE_INGEST_ENABLED env flag). `TIGGEIngest.fetch()` raises `TIGGEIngestNotEnabled` until operator flips both gates, then reads only an operator-approved local JSON payload path; external TIGGE archive HTTP/GRIB remains a later packet. Code path lands; ingest dormant by default.
14126:+### A phase — Strategy + risk (2 cards, 52h)
14128:+Pulled in from R2 §4 deferred Dominance Roadmap per user mandate "live trade and dominate".
14130:+- **A1** StrategyBenchmarkSuite (30h, high risk, critic-opus) — Standardized metrics (EV after fees+slippage, realized spread capture, fill probability, adverse selection, time-to-resolution risk, drawdown, calibration error vs market-implied). Strategy promotion gate: replay → paper → live-shadow tests must all PASS. New `strategy_benchmark_runs` table.
14131:+- **A2** RiskAllocator + PortfolioGovernor (22h, high risk, critic-opus) — Caps per-market / per-event / per-resolution-window. Drawdown governor + kill switch. Maker/taker mode based on book depth + heartbeat health + resolution deadline. Reduce-only mode when degraded. NC-NEW-I: sizing distinguishes OPTIMISTIC vs CONFIRMED exposure.
14135:+- **G1** 17 CI gates + staged-live-smoke (14h, medium risk, critic-opus + operator) — Single command (`scripts/live_readiness_check.py`) runs all 17 gates: V2 SDK / Host / Heartbeat / pUSD / Sell-token / Snapshot / Provenance / Order-type / Unknown / Matched-not-final / User-channel / Cancel-replace / Cutover-wipe / Crash / Paper-live-parity / Strategy-benchmark / Agent-docs. Each gate maps to a specific R3 antibody. INV-NEW-S: LIVE deploy requires 17/17 PASS + ≥1 staged-live-smoke environment passing the same.
14147:+| NC-NEW-E | M1 | RESTING is NOT a CommandState member; lives in `venue_order_facts.state` |
14150:+| NC-NEW-H | U2 | Calibration training paths filter `venue_trade_facts WHERE state='CONFIRMED'`; SELECTing MATCHED for training raises ValueError |
14152:+| NC-NEW-J | F3 | TIGGEIngest.fetch() raises TIGGEIngestNotEnabled when operator gate closed |
14154:+| INV-NEW-A | Z1 | No live submit when CutoverGuard.current_state() != LIVE_ENABLED |
14157:+| INV-NEW-D | Z4 | Reserved tokens for an open sell command MUST be released atomically when command transitions to CANCELED/FILLED/EXPIRED |
14162:+| INV-NEW-I | M4 | Replacement sell BLOCKED until prior sell reaches CANCEL_CONFIRMED, FILLED+CONFIRMED, EXPIRED, or proven absent |
14165:+| INV-NEW-L | R1 | Settlement transitions are durable + crash-recoverable; REDEEM_TX_HASHED is recovery anchor |
14166:+| INV-NEW-M | T1 | Paper-mode runs go through SAME PolymarketV2Adapter Protocol; FakePolymarketVenue and live adapter produce schema-identical events |
14169:+| INV-NEW-P | F2 | Calibration param promotion to live REQUIRES frozen-replay PASS |
14170:+| INV-NEW-Q | A1 | No strategy promoted to live without StrategyBenchmarkSuite.promotion_decision() returning PROMOTE |
14172:+| INV-NEW-S | G1 | LIVE deployment requires 17/17 G1 gate PASS + ≥1 staged-live-smoke environment passing same |
14186:+| TIGGE-ingest go-live | F3 fetch | TIGGEIngestNotEnabled raised; open gate without local payload raises TIGGEIngestFetchNotConfigured |
14202:+| up-06 | G1 | UNVERIFIED rejection matrix → live-readiness gate |
14205:+| mid-01 | M1 | RED→durable-cmd; cycle_runner-as-proxy ownership lock |
14231:+**Wave B** — Snapshot + provenance + lifecycle (~126h, U + M phase):
14236:+Outcomes: settlement command ledger + paper/live parity fake venue.
14239:+Outcomes: forecast source registry + calibration retrain wiring + local-payload TIGGE stub. Operator can flip local TIGGE / retrain switches without code change; external TIGGE archive HTTP/GRIB is a later data-source packet.
14244:+**Wave F** — Live readiness + cutover (~14h + operator time, G phase): G1 → operator runs `scripts/live_readiness_check.py` → if 17/17 PASS, operator dispatches CutoverGuard transition `PRE_CUTOVER_FREEZE → CUTOVER_DOWNTIME → POST_CUTOVER_RECONCILE → LIVE_ENABLED`.
14245:+Outcomes: Zeus is live.
14247:+**Total wall-clock estimate**: 312h engineering + operator gate time. With 2-3 engineers in parallel and operator-decision turnaround ~1-3 days per gate, **realistic delivery: 5-8 weeks**.
14251:+## §7 What "dominate live market" looks like post-G1
14253:+- Every order Zeus places is reconstructable from raw payload provenance (U2 + Z2 envelope).
14256:+- Every fill is split MATCHED → MINED → CONFIRMED with calibration consuming CONFIRMED only (NC-NEW-H).
14259:+- Paper and live use SAME state machine via T1 fake venue.
14261:+- A2 risk allocator caps deployment per market / event / resolution-time / drawdown.
14262:+- F1+F2+F3 forecast plumbing is wired so operator can flip local TIGGE ingest + calibration retrain switches without code changes; external TIGGE archive HTTP/GRIB is not yet authorized.
14265:+That is the minimum infrastructure for "Zeus dominates live market" per the original prompt. Once Wave F closes, edge can compound rather than being erased by execution-state pollution.
14299:+   - `deliverables` — exactly what to write.
14321:+- `reference_excerpts/` — frozen excerpts of external docs (Polymarket V2 docs, py-clob-client-v2 SDK, TIGGE archive access) — operator captures so cold-start agent doesn't need network
14361:+- Bounded: no command enum/state expansion and no live cutover authority.
14382:+- `src/execution/exit_triggers.py` is signal-only; it evaluates exit triggers and does not schedule live submits.
14383:+- `src/execution/exit_lifecycle.py` owns live exit actuation and previously had an untyped stale-cancel retry path.
14389:+- Retargeted M4 integration to the actual live-money seams: `exit_lifecycle`, `executor`, and `venue_command_repo`.
14392:+  - `CancelOutcome(status="NOT_CANCELED")` maps to existing `CANCEL_FAILED` event and command state `REVIEW_REQUIRED`.
14398:+This resolution does not implement exchange reconciliation, live cutover, production DB mutation, or new command grammar.
14429:+Absence must be based on a successful venue enumeration. Stale/error/unauthorized venue reads are not proof of absence. M5 tests use fakes only and do not authorize live venue side effects or production DB mutation.
14518:+- `position_lots` sketch omits provenance columns (`source`, `observed_at`, `local_sequence`, `raw_payload_hash`) even though INV-NEW-F says every fact in the five projections carries those fields.
14520:+- `venue_submission_envelopes` sketch omits some fields present on the frozen `VenueSubmissionEnvelope` contract (`side`, `price`, `size`, `trade_ids`, `transaction_hashes`). Omitting them would prevent DB-only reconstruction of the envelope.
14527:+- adds common provenance fields to `position_lots`;
14554:+- Added RED force-exit durable CANCEL proxy emission inside `cycle_runner._execute_force_exit_sweep` only; RiskGuard does not write venue commands.
14555:+- Preserved no-live-side-effect behavior: RED proxy writes durable command journal + provenance envelope and appends `CANCEL_REQUESTED`; it does not call SDK `cancel_order()` or `place_limit_order()`.
14570:+- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py` -> `14 passed`.
14572:+- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py tests/test_command_bus_types.py tests/test_venue_command_repo.py tests/test_command_recovery.py tests/test_executor_command_split.py tests/test_digest_profile_matching.py::test_r3_m1_lifecycle_grammar_routes_to_m1_profile_not_heartbeat tests/test_neg_risk_passthrough.py tests/test_dual_track_law_stubs.py::test_red_triggers_active_position_sweep` -> `142 passed`.
14573:+- `pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py tests/test_executable_market_snapshot_v2.py tests/test_v2_adapter.py` -> `80 passed, 2 skipped`.
14575:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M1` -> GREEN (`GREEN=15 YELLOW=0 RED=0`).
14679:+    title: 5-projection raw provenance schema
14774:+    title: FakePolymarketVenue (paper/live parity)
14809:+    blocking_operator_gate: Calibration-retrain go-live
14814:+    title: TIGGE ingest stub (gated dormant)
14823:+    blocking_operator_gate: TIGGE-ingest go-live
14854:+    title: 17 live readiness gates + staged smoke
14862:+    blocking_operator_gate: live-money-deploy-go (17/17 PASS + smoke)
14863:+    critic_review: "ENGINEERING APPROVE/PASS but LIVE NO-GO: pre-close critic Mill the 2nd APPROVE + verifier Tesla the 2nd PASS; post-verification critic/security/verifier found and hardened non-operator seams; phase close/live-ready still BLOCKED by missing Q1 Zeus-egress and staged-live-smoke evidence plus full-suite triage; keep IN_PROGRESS until real evidence passes."
14868:+ready_to_start: []  # G1 is in progress; live deployment remains operator-gated
14960:+The original `slice_cards/Z0.yaml` named `docs/architecture/polymarket_live_money_contract.md` as a new file before Z0 adapted it to the packet-local path.
14964:+`python3 scripts/topology_doctor.py --navigation ...` reports that `docs/architecture/polymarket_live_money_contract.md` is outside known workspace routes and cannot be classified. `docs/AGENTS.md` does not declare `docs/architecture` as an active docs subroot.
14972:+Z0 implementation used `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` and registered it in that packet router.
14976:+Z0 has already adapted its card and tests to use `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`. Operator review can later decide whether to incorporate this as a permanent protocol rule.
14993:+python3 scripts/topology_doctor.py --navigation --task "R3 A1 StrategyBenchmarkSuite alpha execution metrics replay paper live shadow promotion gate strategy_benchmark_runs INV-NEW-Q" --files ...
15002:+1. Current law: no live strategy promotion without replay + paper + shadow benchmark evidence (`INV-NEW-Q`).
15004:+3. Derived context: R3 plan/slice card/topology outputs guide scope but do not authorize live side effects.
15009:+- No live venue submit/cancel/redeem.
15010:+- No credentialed live-shadow activation.
15011:+- No production DB/state artifact mutation.
15012:+- No CLOB cutover or live strategy promotion.
15031:+- Existing runtime seams: `src/execution/executor.py`, `src/engine/cycle_runner.py`, `src/control/heartbeat_supervisor.py`, `src/riskguard/risk_level.py`, `src/state/db.py` position_lots schema.
15034:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2`: GREEN=12 YELLOW=0 RED=0.
15035:+- Dedicated topology profile route: `r3 risk allocator governor implementation`.
15039:+A2 should be a K1 blocking allocation/governor seam, not a venue actor. The allocator reads supplied/canonical exposure lots, preserves OPTIMISTIC vs CONFIRMED capacity accounting, and returns structured denial reasons. The executor should consult the global allocator before command persistence/SDK contact; cycle runner should refresh governor state from canonical read models and expose `portfolio_governor` in summaries. Config defaults ship in `config/risk_caps.yaml`, but absence still loads safe engineering defaults.
15043:+- Live venue submit/cancel/redeem, CLOB cutover, credential activation, live strategy promotion, and G1 deploy remain blocked.
15047:+- Frozen interfaces produced: `src/risk_allocator/governor.py` allocator/governor API, executor pre-submit allocation denial seam, cycle summary `portfolio_governor`, `config/risk_caps.yaml` default cap policy.
15063:+Wire forecast-source plumbing so Zeus can identify, gate, and provenance-stamp forecast sources before later F2/F3 work adds calibration retrain controls or TIGGE fetch behavior.
15069:+- Make experimental TIGGE source dormant behind both operator artifact and env flag.
15071:+- Update live forecast appender and live ensemble client to stamp registry provenance.
15076:+- No calibration retrain trigger or Platt refit (F2).
15077:+- No TIGGE network/GRIB fetch implementation (F3).
15078:+- No settlement-source routing change.
15079:+- No live venue/order side effects.
15085:+- F1 registry is forecast-source plumbing only; it does not prove settlement, Day0, or hourly truth.
15086:+- Existing Open-Meteo forecast-history sources remain forecast/training rows, not settlement-adjacent truth.
15087:+- Experimental TIGGE remains inert until operator evidence + `ZEUS_TIGGE_INGEST_ENABLED=1` are both present.
15107:+- `python3 scripts/topology_doctor.py --navigation --task "R3 F2 Calibration retrain loop operator-gated retrain frozen-replay antibody ZEUS_CALIBRATION_RETRAIN_ENABLED calibration_params_versions" ...` -> profile `r3 calibration retrain loop implementation`, navigation ok after registering the new calibration/test files and hazard badge.
15108:+- Scoped routers read: root AGENTS, `docs/AGENTS.md`, `docs/operations/AGENTS.md`, `architecture/AGENTS.md`, `src/AGENTS.md`, `src/calibration/AGENTS.md`, `tests/AGENTS.md`.
15109:+- Target module book read: `docs/reference/modules/calibration.md`.
15113:+1. Which truth is allowed into retraining?
15114:+   - Only `venue_trade_facts` rows with `state='CONFIRMED'`, through the U2 repository seam `load_calibration_trade_facts`; MATCHED/MINED execution observations remain excluded.
15116:+   - Both `ZEUS_CALIBRATION_RETRAIN_ENABLED` and an operator evidence artifact matching `docs/operations/task_2026-04-26_ultimate_plan/**/evidence/calibration_retrain_decision_*.md`, plus a non-empty operator token. F2 does not create such a live operator decision artifact.
15120:+   - No Platt formula change, no ambient auto-fit, no TIGGE activation, no production DB mutation, no settlement/Day0/hourly source-role changes, and no live-money venue behavior.
15126:+No repo-local `calibration_retrain_decision_*.md` live-go artifact was created. The code path remains dormant unless a future operator creates the packet-approved evidence artifact and sets `ZEUS_CALIBRATION_RETRAIN_ENABLED=1`.
15135:+Phase: F3 `TIGGE ingest stub`
15141:+- `python3 scripts/topology_doctor.py --navigation --task "R3 F3 TIGGE ingest stub TIGGEIngest TIGGEIngestNotEnabled ZEUS_TIGGE_INGEST_ENABLED" ...` -> profile `r3 tigge ingest stub implementation`, navigation ok after registering the new source/test files.
15143:+  `python3 scripts/topology_doctor.py --navigation --task "R3 F3 TIGGE switch-only local operator payload wiring TIGGEIngest ZEUS_TIGGE_PAYLOAD_PATH registered ingest ensemble_client" ...`
15150:+   - Forecast-source plumbing only. TIGGE is not settlement daily truth, Day0/current truth, or hourly historical truth.
15151:+2. Does endpoint/data availability prove settlement correctness?
15152:+   - No. F3 performs no real TIGGE HTTP/GRIB fetch and does not alter settlement routing.
15154:+   - `current_source_validity.md` and `current_data_state.md` were checked only to preserve source-role separation and live data posture. They do not authorize TIGGE live ingest.
15156:+   - TIGGE fetch requires both `docs/operations/task_2026-04-26_ultimate_plan/**/evidence/tigge_ingest_decision_*.md` and `ZEUS_TIGGE_INGEST_ENABLED=1`. Construction is safe with gate closed; fetch raises before payload loading.
15157:+5. What makes TIGGE switch-only without fabricating source truth?
15158:+   - Open-gate TIGGE reads only an operator-approved local JSON payload via constructor,
15159:+     `ZEUS_TIGGE_PAYLOAD_PATH`, or `payload_path:` in the decision artifact. If no
15160:+     payload path exists, it fails closed with `TIGGEIngestFetchNotConfigured`.
15166:+No calibration retrain, Platt refit, settlement routing, Day0/hourly routing, production DB mutation, live venue behavior, or real external TIGGE archive HTTP/GRIB I/O is authorized by F3.
15173:+# G1 boot — live-readiness gates
15182:+for operator-controlled live deployment only when:
15185:+2. staged-live-smoke evidence proves the same 17/17 suite passed in a staged
15189:+remains `live-money-deploy-go`.
15193:+- `scripts/live_readiness_check.py`
15194:+- `tests/test_live_readiness_gates.py`
15203:+- No live submit/cancel/redeem side effects.
15204:+- No production DB or runtime state mutation.
15205:+- No credential activation or live strategy promotion.
15207:+- No automatic execution of live smoke scripts; G1 reads staged-smoke evidence
15217:+3. Can the script authorize live deployment? — No. `live_deploy_authorized` is
15218:+   always false and the output states operator `live-money-deploy-go` is still
15220:+4. Does G1 run live side-effect smoke automatically? — No. It reads evidence
15221:+   files; it never invokes `scripts/live_smoke_test.py`.
15237:+- High-risk reads completed: root AGENTS, `architecture/AGENTS.md`, `architecture/self_check/zero_context_entry.md`, `architecture/self_check/authority_index.md`, `architecture/kernel_manifest.yaml`, `architecture/invariants.yaml`, `architecture/zones.yaml`, `architecture/negative_constraints.yaml`, `docs/authority/zeus_current_architecture.md`, `docs/authority/zeus_current_delivery.md`, `docs/authority/zeus_change_control_constitution.md`, M1 gate-edge artifact, M1 slice card, operator decision index.
15245:+- Forbidden: source-code grammar expansion, RESTING CommandState, M2 unknown-side-effect runtime semantics, live venue submission, cutover/go-live.
15250:+This amendment incorporates already-reviewed M1 grammar-additive values only. It does not change source code, mutate DBs, call live SDKs, or authorize any operator runtime gate.
15259:+Read set: root `AGENTS.md`, scoped `src/engine/AGENTS.md`, `src/execution/AGENTS.md`, `src/state/AGENTS.md`, `architecture/self_check/zero_context_entry.md`, `architecture/self_check/authority_index.md`, `architecture/kernel_manifest.yaml`, `architecture/invariants.yaml`, `architecture/zones.yaml`, `architecture/negative_constraints.yaml`, `docs/authority/zeus_current_architecture.md`, `docs/authority/zeus_current_delivery.md`, `docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M1.yaml`, `SKILLS_MATRIX.md`, `CONFUSION_CHECKPOINTS.md`, `IMPLEMENTATION_PROTOCOL.md`, `operator_decisions/INDEX.md`, and targeted module books for engine/execution/state.
15266:+- Authoritative truth surface: `CommandState`/`CommandEventType` in `src/execution/command_bus.py`, transition enforcement in `src/state/venue_command_repo.py`, RED actuation sequencing in `src/engine/cycle_runner.py`, and current law in `architecture/invariants.yaml` + `docs/authority/zeus_current_architecture.md`.
15269:+- Forbidden: no `RESTING` CommandState; no order/trade/finality collapse from U2 into command grammar; RiskGuard must not call `insert_command`; no M2 unknown-side-effect runtime resolution; no M3 websocket; no M5 exchange sweep; no live venue side effects.
15270:+- Evidence required: M1 grammar tests, RED durable command antibodies, existing command bus/repo/recovery regressions, executor split regressions, digest/profile route test, neg-risk passthrough, drift check, map-maintenance, planning-lock, closeout receipt, critic+verifier. If operator gate remains OPEN, close as `COMPLETE_AT_GATE` with gate-edge artifact rather than COMPLETE.
15289:+- Scoped routers read: root AGENTS, `src/execution/AGENTS.md`, `src/state/AGENTS.md`, `src/contracts/AGENTS.md`, `src/data/AGENTS.md`, `src/venue/AGENTS.md`, high-risk overlay `architecture/self_check/zero_context_entry.md`, and `architecture/self_check/authority_index.md`.
15290:+- Authority spine read: `architecture/kernel_manifest.yaml`, `architecture/invariants.yaml`, `architecture/zones.yaml`, `architecture/negative_constraints.yaml`, `docs/authority/zeus_current_architecture.md`, `docs/authority/zeus_current_delivery.md` excerpts for command/lifecycle/runtime truth.
15297:+   - The canonical trades DB `venue_commands` / `venue_command_events` journal written through `src/state/venue_command_repo.py`, plus `CommandState`/`CommandEventType` grammar from `src/execution/command_bus.py`. Derived docs/artifacts are evidence only.
15299:+   - K2 live execution (`src/execution/executor.py`, `src/execution/command_recovery.py`) and K1 state journal seam (`src/state/venue_command_repo.py`). No lifecycle phase grammar or production DB artifact is touched.
15303:+   - Production DB artifacts, authority docs, graph caches, parallel worktrees, OMX runtime state, RESTING/MATCHED/MINED/CONFIRMED `CommandState`, M3/M4/M5 runtime surfaces, production DB mutation, and live venue submission/cutover.
15307:+   - M2 adds economic-intent duplicate blocking and venue_order_id persistence because they are necessary to make the unknown-side-effect invariant effective, but it does not invent exchange sweeps, websocket ingest, live cutover, or new command/order/trade finality states.
15348:+- No live WS side effect by default; startup remains environment-gated.
15349:+- No live venue submission or cutover approval.
15353:+- No production DB mutation.
15385:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M4` → GREEN=1 YELLOW=0 RED=0, skipped=10.
15390:+M4 should not add command grammar. The safe implementation is a DB-only `exit_safety` surface that owns per-position/token mutexes, typed cancel outcome parsing, and replacement-sell gating, then integrates at the existing real actuation seams: `execute_exit_order`, `exit_lifecycle` stale-cancel retry, and `venue_command_repo` terminal release. `CANCEL_UNKNOWN` is represented as a typed semantic outcome plus `CANCEL_REPLACE_BLOCKED` payload requiring M5, not as a new `CommandState` or `CommandEventType`.
15422:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M5` → GREEN=10 YELLOW=0 RED=0.
15427:+M5 is not a live actuator. The safe implementation is a read-only venue enumeration sweep (`get_open_orders`, optional `get_trades`, optional `get_positions`) that writes durable `exchange_reconcile_findings` and only appends linkable missing trade facts where a local command foreign key already exists. Exchange-only orders/trades become findings, never `venue_commands` rows.
15432:+- Live venue submission/cancel/redeem, CLOB cutover, production DB mutation, and R1 settlement/redeem side effects remain closed.
15445:+# R3 R1 boot — settlement / redeem command ledger
15454:+- `python3 scripts/topology_doctor.py --navigation --task "R3 R1 Settlement / redeem command ledger settlement_commands REDEEM_TX_HASHED crash-recoverable redemption Q-FX-1 FXClassificationPending" --files ...` -> navigation ok, profile `r3 settlement redeem command ledger implementation`.
15455:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase R1` -> `GREEN=7 YELLOW=0 RED=0`.
15459:+- Settlement value/source semantics are not changed by R1. R1 models redemption command durability after settlement and does not alter `SettlementSemantics`, winning-bin calculation, or HIGH/LOW identity.
15461:+- Legacy USDC.e payout is not silently treated as pUSD; R1 records it as `REDEEM_REVIEW_REQUIRED` for operator classification.
15462:+- `REDEEM_TX_HASHED` is the crash-recovery anchor; receipt reconciliation follows tx hash to confirmed/failed without relying on in-memory submit state.
15463:+- Redeem failure/review states do not mark positions settled. Settlement terminalization remains owned by canonical settlement paths, not the redeem command ledger.
15468:+- Add `src/execution/settlement_commands.py` durable ledger.
15469:+- Add `settlement_commands` / `settlement_command_events` schema to `src/state/db.py`.
15473:+- No live redeem submission in tests or default startup.
15474:+- No production DB mutation.
15484:+# R3 T1 boot — FakePolymarketVenue paper/live parity
15492:+Task: R3 T1 FakePolymarketVenue paper/live parity, same `PolymarketV2Adapter` protocol, schema-identical events, INV-NEW-M.
15498:+- Paper mode must use the same adapter protocol surface as live.
15499:+- Fake/live parity tests must compare typed adapter result/envelope schemas, not green SDK mocks.
15500:+- Failure injection must not perform live venue submit/cancel/redeem, production DB mutation, credentialed activation, or CLOB cutover.
15501:+- T1 must preserve R1 no-live-redeem and Q-FX-1 boundaries.
15514:+Allowed local implementation is test fake/integration infrastructure and the minimal production protocol seam in `src/venue/polymarket_v2_adapter.py`. No live I/O or production DB mutation is authorized.
15531:+returned `GREEN=14 YELLOW=0 RED=0` and refreshed the 2026-04-27 drift report.
15533:+Working hypothesis: U1's job is not live cutover and not U2 raw provenance.
15544:+  `neg_risk`).
15548:+- Legacy DBs get a nullable `snapshot_id` ALTER path, while fresh U1 DBs make
15551:+Operator gates: Q1/cutover and Q-FX-1 remain open. U1 does not authorize live
15571:+Working hypothesis: U2 is the schema/provenance backbone only. It may create and protect the five projection surfaces, require `envelope_id` + U1 `snapshot_id` at the venue-command insert seam, mirror command events into provenance, and add repo APIs for order/trade/lot facts plus CONFIRMED-only calibration reads. It must not amend `CommandState`, implement websocket ingest, exchange reconciliation, settlement redemption side effects, risk allocation, or calibration retraining.
15573:+Implementation boundaries: source edits stay in `src/state/db.py`, `src/state/venue_command_repo.py`, and narrow executor wiring only if required to provide pre-side-effect envelope IDs. Tests concentrate in `tests/test_provenance_5_projections.py` with existing U1/Z2/command regressions rerun after.
15604:+- navigation warning: `docs/architecture/polymarket_live_money_contract.md` is unclassified; implementation adapts to packet-local path.
15607:+Z0 is a doc/test-only source-of-truth correction. The old CLOB V2 packet remains supporting evidence, but R3 is the active implementation authority. The corrected impact report must retain the busted-premise learnings while upgrading framing to V2_ACTIVE_P0. The live-money contract should not create a new `docs/architecture` authority surface.
15615:+- Produces: corrected CLOB V2 impact report, packet-local live-money contract, Z0 plan-lock tests.
15649:+- High-risk overlay: `architecture/self_check/zero_context_entry.md`,
15661:+- **Authoritative truth surface here:** live venue side effects must be gated
15669:+  NC direct-venue-command constraints, and the live-money contract claim that
15670:+  no live placement proceeds unless CutoverGuard is `LIVE_ENABLED`.
15678:+- **Forbidden / deliberately deferred:** no live cutover, no real SDK calls in
15682:+- **Change class:** architecture/control feature with live-money guard rails.
15683:+- **Semantic boot profile:** none of the source/settlement/Day0/calibration
15748:+  envelope provenance. It does not own venue command schema, lifecycle grammar,
15752:+- **Invariant scope:** NC-NEW-G (envelope provenance, not SDK shape),
15754:+  INV-24/25/28/30 live venue ordering.
15760:+  and records the discrepancy in the reference excerpt; no live cutover is
15772:+- envelope captures host/chain/funder/token/order/snapshot/fee/provenance fields
15778:+- V2 neg-risk/tick/fee fields pass through from snapshot/SDK surface
15780:+  a DB schema mutation
15805:+- `pytest -q -p no:cacheprovider tests/test_heartbeat_supervisor.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py`
15818:+Drift check: `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase Z4` returned `GREEN=15 YELLOW=0 RED=0` and wrote the 2026-04-27 drift report.
15820:+Working hypothesis: implement Z4 as a typed, DB-backed collateral runtime truth surface. pUSD is the only BUY collateral and CTF outcome tokens are the only SELL inventory; no preflight path may satisfy a sell by looking at pUSD. `CollateralLedger` owns snapshots and reservations; wrap/unwrap commands are durable state records only, with no live chain submission in Z4.
15822:+Ambiguity/deep-interview gate: the plan is sufficiently specific to proceed without asking the operator because the user explicitly authorized autonomous implementation and Q-FX-1 has a defined fail-closed default. The main resolved edge cases are: legacy USDC.e remains separately reported and not spendable as pUSD; Q-FX-1 gates redemption/accounting only; missing collateral snapshot/chain failure is `DEGRADED` and blocks live submit; existing legacy sell-collateral checks must be routed to CTF-token inventory or fail closed.
15826:+Operator gates: Q-FX-1 remains OPEN. Z4 may ship to the gate edge by implementing `FXClassificationPending` and enum validation, but it must not choose `TRADING_PNL_INFLOW`, `FX_LINE_ITEM`, or `CARRY_COST`, and it must not perform pUSD redemption side effects. Q1/Cutover remains open, so live placement is still blocked elsewhere.
15828:+Pre-close target evidence: `tests/test_collateral_ledger.py` plus focused executor/live-safety tests, `py_compile` on touched Python, Z4 drift GREEN, map-maintenance/reference/planning-lock/closeout, pre-close critic+verifier, then post-close third-party critic+verifier before U1 unfreezes.
15999:+| Phase | GREEN | YELLOW | RED | SKIPPED |
16003:+**Totals**: 5 GREEN · 0 YELLOW · 0 RED
16017:+| Phase | GREEN | YELLOW | RED | SKIPPED |
16040:+**Totals**: 241 GREEN · 0 YELLOW · 0 RED
16054:+| Phase | GREEN | YELLOW | RED | SKIPPED |
16077:+**Totals**: 241 GREEN · 0 YELLOW · 0 RED
16100:+- `src/strategy/candidates/neg_risk_basket.py`
16124:+- Added `StrategyBenchmarkSuite` with replay, fake-paper, and read-only live-shadow metric evaluation.
16127:+- Added local supplied-connection `strategy_benchmark_runs` DDL/persistence helper; this does not mutate production DB/state artifacts.
16137:+pytest -q -p no:cacheprovider tests/test_strategy_benchmark.py tests/test_fake_polymarket_venue.py tests/test_fdr.py tests/test_kelly.py tests/test_kelly_cascade_bounds.py tests/test_kelly_live_safety_cap.py: 82 passed
16138:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A1: GREEN=7 YELLOW=0 RED=0
16142:+Known non-goals / risks:
16144:+- No strategy is promoted to live in A1.
16147:+- A2 RiskAllocator/PortfolioGovernor and G1 live readiness remain dependency-gated.
16161:+- A2 unfrozen after post-close critic Carson the 2nd APPROVE and verifier Maxwell the 2nd PASS; G1 remains dependency/live-gate blocked.
16181:+- A2 may enter phase; G1 remains blocked by A2 and live-readiness gates. No live venue/prod DB/cutover authorization is implied.
16198:+- `src/risk_allocator/AGENTS.md`
16199:+- `src/risk_allocator/__init__.py`
16200:+- `src/risk_allocator/governor.py`
16202:+- `config/risk_caps.yaml`
16209:+- `tests/test_risk_allocator.py`
16221:+- `docs/reference/modules/riskguard.md`
16239:+- Added `RiskAllocator`, `PortfolioGovernor`, `CapPolicy`, `GovernorState`, `ExposureLot`, `AllocationDecision`, and `AllocationDenied` in a new `src/risk_allocator` package.
16243:+- Added `config/risk_caps.yaml` engineering defaults while preserving in-code defaults when the file is absent.
16252:+python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
16253:+python3 -m py_compile src/risk_allocator/governor.py src/risk_allocator/__init__.py src/engine/cycle_runner.py src/execution/executor.py tests/test_risk_allocator.py: PASS
16254:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 17 passed
16255:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 17 passed
16256:+pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
16257:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py: 33 passed, 5 skipped
16258:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
16263:+Known non-goals / risks:
16265:+- A2 does not tune operator caps for production; `risk_caps.yaml` is an engineering default and later operator tuning remains separate.
16266:+- A2 does not authorize live venue submit/cancel/redeem, CLOB cutover, credentialed activation, live strategy promotion, or G1 deployment.
16268:+- The process-wide allocator defaults to allow when not configured so isolated tests and utility seams remain inert; cycle startup refresh is the intended live-runtime configuration seam.
16272:+- Critic Epicurus the 2nd BLOCKED the initial A2 implementation on three live-path gaps: allocation metadata was test-only/dynamic, kill switch did not guard exit submits, and PortfolioGovernor refresh occurred after monitoring/force-exit work rather than at cycle start.
16273:+- Remediation added typed `ExecutionIntent.event_id`, `resolution_window`, and `correlation_key`; `cycle_runtime` now passes candidate event/date/cluster allocation identity through the production entry constructor.
16277:+- Added regressions for typed production intent metadata, exit kill-switch pre-persistence denial, command-event allocation metadata reconstruction, and cycle-start refresh ordering.
16282:+python3 -m py_compile src/contracts/execution_intent.py src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/engine/cycle_runner.py src/engine/cycle_runtime.py tests/test_risk_allocator.py: PASS
16283:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 20 passed
16284:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py: 36 passed, 5 skipped
16285:+pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
16286:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
16287:+python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
16302:+python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
16303:+python3 -m py_compile src/contracts/execution_intent.py src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/data/polymarket_client.py src/engine/cycle_runner.py src/engine/cycle_runtime.py tests/test_risk_allocator.py tests/test_executor.py: PASS
16304:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 24 passed
16305:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py tests/test_k2_slice_e.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py: 82 passed, 6 skipped
16306:+pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
16307:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
16319:+- G1 is unfrozen for phase entry only; live deploy remains blocked by the G1 17/17 readiness gate and explicit operator authorization.
16330:+Task: R3 F1 forecast source registry — source gating and forecast provenance wiring
16350:+Implemented F1 forecast-source plumbing. Added a typed K2 forecast ingest protocol, a registry with active-source gating, and a dormant TIGGE source that requires both operator evidence and `ZEUS_TIGGE_INGEST_ENABLED=1`. Existing forecast-history and live ensemble sources stay enabled by default. New `forecasts` writes now persist `source_id`, `raw_payload_hash`, `captured_at`, and `authority_tier`, with additive legacy-safe schema hooks. `ensemble_client.fetch_ensemble()` now returns registry provenance and fails closed before network for disabled/gated sources. `hole_scanner` forecast sources now derive from the registry. The previous-runs backfill script now writes the same F1 provenance fields as live append. `EnsembleSignal` math was intentionally unchanged.
16355:+- `pytest -q -p no:cacheprovider tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill tests/test_ensemble_client.py tests/test_digest_profile_matching.py::test_r3_f1_forecast_source_registry_routes_to_f1_profile_not_heartbeat` -> 11 passed.
16356:+- Focused F1/data/signal regression suite: `tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py tests/test_ensemble_client.py tests/test_backfill_openmeteo_previous_runs.py tests/test_etl_forecasts_v2_from_legacy.py tests/test_ensemble_signal.py tests/test_digest_profile_matching.py` -> 103 passed.
16366:+- Post-close remediation F1 drift -> GREEN=20 YELLOW=0 RED=0; `git diff --check` clean; `git diff -- src/signal/ensemble_signal.py` empty.
16380:+# F2 work record — calibration retrain loop wiring
16384:+Task: R3 F2 calibration retrain loop wiring — operator-gated trigger + frozen-replay antibody
16389:+- docs/reference/modules/calibration.md / src/calibration/AGENTS.md
16390:+- src/calibration/retrain_trigger.py
16391:+- tests/test_calibration_retrain.py / tests/test_digest_profile_matching.py
16401:+Implemented the F2 retrain trigger seam without enabling live retraining. `src/calibration/retrain_trigger.py` now exposes `status()`, `arm()`, `load_confirmed_corpus()`, and `trigger_retrain()` around a dormant operator-gated calibration promotion path. The gate requires `ZEUS_CALIBRATION_RETRAIN_ENABLED`, an operator evidence artifact on the approved packet route, and a signed operator token (`v1.<operator_id>.<nonce>.<hmac_sha256>` using `ZEUS_CALIBRATION_RETRAIN_OPERATOR_TOKEN_SECRET`). The corpus loader delegates to the U2 `load_calibration_trade_facts` seam and rejects any non-CONFIRMED request before reading. Promotion is atomic with the version-history insert/retire operation and calls `save_platt_model_v2` only after frozen replay PASS; frozen replay FAIL records an audit row and blocks promotion.
16404:+- No `calibration_retrain_decision_*.md` live-go artifact was created in the repo, so the default project status remains DISABLED even if code is present.
16405:+- No Platt formula, calibration manager, ensemble signal read path, TIGGE activation, settlement/source routing, production DB, or live venue behavior changed.
16409:+- `python3 scripts/topology_doctor.py --navigation --task "R3 F2 Calibration retrain loop ..." ...` -> navigation ok, profile `r3 calibration retrain loop implementation`.
16410:+- `python3 -m py_compile src/calibration/retrain_trigger.py` -> ok.
16411:+- `pytest -q -p no:cacheprovider tests/test_calibration_retrain.py tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only tests/test_digest_profile_matching.py::test_r3_f2_calibration_retrain_loop_routes_to_f2_profile_not_heartbeat` -> 10 passed.
16412:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F2` -> GREEN=10 YELLOW=0 RED=0.
16420:+- Critic Noether found that frozen-replay PASS promotion could hit the existing `platt_models_v2` active-row uniqueness constraint because F2 inserted the new audit row and then called `save_platt_model_v2` without first deleting/replacing the exact live model key.
16422:+- Regression: `tests/test_calibration_retrain.py::test_frozen_replay_pass_replaces_existing_live_platt_row` seeds an existing active row and proves PASS promotion replaces it while preserving a promoted F2 audit row.
16436:+# F3 work record — TIGGE ingest stub
16440:+Task: R3 F3 TIGGE ingest stub — registered, operator-gated, dormant by default
16457:+Implemented the F3 dormant TIGGE ingest stub. `TIGGEIngest` implements the forecast ingest protocol shape, can be constructed with the gate closed, reports gate health without external I/O, and makes `fetch()` fail closed with `TIGGEIngestNotEnabled` before payload loading unless both the operator artifact and `ZEUS_TIGGE_INGEST_ENABLED=1` are present. The F1 registry records `ingest_class=TIGGEIngest` while preserving `enabled_by_default=False`, `requires_api_key=True`, and dual operator gates.
16460:+To satisfy the "authorization should only be a switch" requirement without fabricating TIGGE source truth, open-gate TIGGE now reads an operator-approved local JSON payload from one of three reversible seams: constructor `payload_path`, `ZEUS_TIGGE_PAYLOAD_PATH`, or `payload_path:` / `tigge_payload_path:` in the latest `tigge_ingest_decision_*.md` artifact. Missing payload configuration fails closed with `TIGGEIngestFetchNotConfigured`. `ensemble_client.fetch_ensemble(..., model="tigge")` now routes through the registered `ForecastIngestProtocol` adapter and proves it does not call Open-Meteo HTTP. No real TIGGE archive HTTP/GRIB implementation was added; external archive access remains a later operator/data-source packet.
16466:+- Follow-up: `python3 scripts/topology_doctor.py --navigation --task "R3 F3 TIGGE switch-only local operator payload wiring TIGGEIngest ZEUS_TIGGE_PAYLOAD_PATH registered ingest ensemble_client" ...` -> navigation ok, profile `r3 tigge ingest stub implementation`.
16470:+- Follow-up: `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F3` -> GREEN=12 YELLOW=0 RED=0, STATUS GREEN.
16471:+- Original closeout: `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F3` -> GREEN=7 YELLOW=0 RED=0.
16473:+- `git diff -- src/signal/ensemble_signal.py src/calibration/platt.py src/calibration/manager.py src/calibration/store.py` -> empty.
16480:+F3 remains COMPLETE with post-close critic+verifier PASS for the original close. The follow-up local-payload switch wiring is ready for independent diff review and closeout evidence. Operator go-live still requires a real decision artifact, env flag, local JSON payload, and later calibration retrain authorization; external TIGGE archive HTTP/GRIB remains out of scope.
16486:+- Critic Confucius the 2nd: APPROVE. Confirmed TIGGE remains experimental, disabled by default, dual-gated by artifact + `ZEUS_TIGGE_INGEST_ENABLED`, open-gate payload loading reads only constructor/env/artifact local JSON, `ensemble_client` routes model=`tigge` through the registered ingest adapter, and docs preserve no external HTTP/GRIB/no retrain/no live-deploy claims.
16494:+# G1 work record — live-readiness gates
16498:+Task: R3 G1 live-readiness gate implementation — 17 CI gates, Q1 Zeus-egress evidence check, staged-live-smoke evidence check, and operator-gated deployment boundary
16504:+- `scripts/live_readiness_check.py`
16507:+- `tests/test_live_readiness_gates.py`
16531:+- Added `scripts/live_readiness_check.py`, a read-only enforcement script with a 17-gate registry, JSON/plain output, explicit Q1/staged-smoke evidence checks, and fail-closed behavior for missing evidence.
16532:+- Added `tests/test_live_readiness_gates.py` to lock the 17-gate registry, fail-closed evidence behavior, safe CLI help, and invariant that the script cannot authorize deployment.
16533:+- Registered the script in `architecture/script_manifest.yaml` and documented the `live_readiness_check.py` long-lived naming exception because the operator-facing G1 contract fixes that script name.
16534:+- Registered the test in `architecture/test_topology.yaml` and moved it to top-level `tests/test_live_readiness_gates.py` because the topology test checker only classifies top-level `tests/test_*.py` files.
16535:+- Added a dedicated topology profile and digest regression so G1 routes to the live-readiness profile rather than heartbeat/risk profiles.
16541:+python3 scripts/topology_doctor.py --navigation --task "R3 G1 live readiness gates live_readiness_check 17 CI gates staged-live-smoke INV-NEW-S live-money-deploy-go" --files ...: navigation ok True, profile r3 live readiness gates implementation
16542:+python3 -m py_compile scripts/live_readiness_check.py tests/test_live_readiness_gates.py: PASS
16543:+python3 scripts/live_readiness_check.py --help: PASS
16544:+python3 -m pytest -q -p no:cacheprovider tests/test_live_readiness_gates.py: 5 passed
16545:+python3 -m pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat: 1 passed
16546:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase G1: GREEN=0 YELLOW=0 RED=0, STATUS GREEN
16547:+python3 scripts/live_readiness_check.py --json: exit 1 expected in this environment; 16/17 engineering gates PASS, G1-02 Q1 Zeus-egress evidence FAIL, staged-live-smoke evidence FAIL, live_deploy_authorized=false
16553:+pre-close critic Mill the 2nd: ENGINEERING APPROVE; phase close/live-ready BLOCKED by missing external evidence
16555:+python3 scripts/topology_doctor.py --navigation ... after review artifact: navigation ok True, profile r3 live readiness gates implementation
16559:+Known non-goals / risks:
16561:+- G1 did not place, cancel, redeem, deploy, mutate production DB/state, activate credentials, promote strategies, or run a live smoke command.
16562:+- `scripts/live_readiness_check.py` reads staged-smoke artifacts only; it intentionally fails closed when Q1/staged-smoke evidence is absent.
16563:+- The readiness script reports `live_deploy_authorized=false` even if all checks pass; operator `live-money-deploy-go` remains the final live-money gate.
16569:+- Obtain real Q1 Zeus-egress and staged-live-smoke evidence from the authorized operator/staging path; do not fabricate evidence and do not auto-run live smoke.
16574:+A broad critic/security/verifier pass after F3 found that G1 was not live-ready and that several non-operator hardening seams were still too permissive or under-tested.  These were remediated without creating operator evidence, activating credentials, mutating production DB/state, or executing live venue side effects.
16583:+- `src/calibration/retrain_trigger.py`
16584:+- `tests/test_calibration_retrain.py`
16587:+- `src/execution/settlement_commands.py`
16588:+- `tests/test_settlement_commands.py`
16589:+- `src/risk_allocator/governor.py`
16590:+- `tests/test_risk_allocator.py`
16593:+- `scripts/live_readiness_check.py`
16594:+- `tests/test_live_readiness_gates.py`
16600:+- User-channel CONFIRMED/MATCHED projections now fall back from executor runtime `position_id` to numeric `decision_id`, so live executor-shaped commands do not silently skip `position_lots`.
16601:+- Registered TIGGE ingest configuration errors now propagate instead of being hidden as `None`.
16604:+- R1 `submit_redeem()` checks `CutoverGuard.redemption_decision()` before `REDEEM_SUBMITTED` and adapter redeem side effects.
16605:+- Global risk allocator defaults now fail closed until the cycle runner/configured test harness publishes allocator state.
16614:+pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_forecast_source_registry.py tests/test_calibration_retrain.py tests/test_v2_adapter.py tests/test_settlement_commands.py tests/test_exit_safety.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py: 109 passed, 20 warnings
16615:+pytest -q -p no:cacheprovider tests/test_cutover_guard.py tests/test_v2_adapter.py tests/test_executable_market_snapshot_v2.py tests/test_heartbeat_supervisor.py tests/test_collateral_ledger.py tests/test_provenance_5_projections.py tests/test_command_grammar_amendment.py tests/test_unknown_side_effect.py tests/test_user_channel_ingest.py tests/test_exit_safety.py tests/test_exchange_reconcile.py tests/test_settlement_commands.py tests/test_fake_polymarket_venue.py tests/test_strategy_benchmark.py tests/test_calibration_retrain.py tests/test_tigge_ingest.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py: 295 passed, 8 skipped, 25 warnings
16616:+python3 -m py_compile src/control/ws_gap_guard.py src/ingest/polymarket_user_channel.py src/data/ensemble_client.py src/data/tigge_client.py src/calibration/retrain_trigger.py src/data/polymarket_client.py src/execution/settlement_commands.py src/risk_allocator/governor.py src/execution/exit_lifecycle.py scripts/live_readiness_check.py tests/conftest.py: PASS
16617:+python3 scripts/live_readiness_check.py --json: exit 1 expected; 16/17 gates PASS; G1-02 Q1 Zeus-egress evidence FAIL; staged-live-smoke evidence FAIL; live_deploy_authorized=false
16618:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py: GREEN=241 YELLOW=0 RED=0, STATUS GREEN
16625:+- G1 remains `IN_PROGRESS`; do not close it until real Q1 Zeus-egress and staged-live-smoke evidence exist and pre-close critic+verifier review passes.
16626:+- `scripts/live_readiness_check.py --json` correctly remains FAIL in this environment because those external evidence artifacts are absent.
16627:+- Operator decisions remain open for Q1 egress, CLOB v2 cutover, Q-FX-1, TIGGE ingest go-live, calibration retrain go-live, staged smoke, and final `live-money-deploy-go`.
16632:+Independent security and code-review passes found additional non-operator bypasses.  These were remediated without fabricating Q1/staged-smoke evidence, changing CutoverGuard live state, activating credentials, mutating production DB/state artifacts, or placing/canceling/redeeming on the live venue.
16637:+payload_sha256`; production CLI no longer accepts arbitrary `--evidence-root` overrides.
16647:+pytest -q -p no:cacheprovider tests/test_live_readiness_gates.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_calibration_retrain.py: 48 passed, 20 warnings
16649:+pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_forecast_source_registry.py tests/test_calibration_retrain.py tests/test_v2_adapter.py tests/test_settlement_commands.py tests/test_exit_safety.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py: 114 passed, 22 warnings
16650:+pytest -q -p no:cacheprovider tests/test_cutover_guard.py tests/test_v2_adapter.py tests/test_executable_market_snapshot_v2.py tests/test_heartbeat_supervisor.py tests/test_collateral_ledger.py tests/test_provenance_5_projections.py tests/test_command_grammar_amendment.py tests/test_unknown_side_effect.py tests/test_user_channel_ingest.py tests/test_exit_safety.py tests/test_exchange_reconcile.py tests/test_settlement_commands.py tests/test_fake_polymarket_venue.py tests/test_strategy_benchmark.py tests/test_calibration_retrain.py tests/test_tigge_ingest.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py: 300 passed, 8 skipped, 27 warnings
16652:+python3 scripts/live_readiness_check.py --json: exit 1 expected; 16/17 gates PASS; G1-02 Q1 Zeus-egress evidence FAIL; staged-live-smoke evidence FAIL; live_deploy_authorized=false
16653:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py: GREEN=241 YELLOW=0 RED=0, STATUS GREEN
16662:+- G1 remains `IN_PROGRESS`; missing Q1 Zeus-egress and staged-live-smoke signed evidence are intentional external/operator blockers.
16663:+- Broad R3 unit-harness evidence is not production fail-closed evidence by itself; explicit fail-closed antibodies cover WS default blocking and risk allocator default blocking, and live-readiness still requires staged evidence.
16664:+- Full-repo pytest is not green and must not be represented as live readiness.  The current sampled failures include stale fixtures against newer metric/slippage laws and possible follow-up live plan omissions (Day0 runtime/window, auto-pause, strategy-key surfaces) requiring separate triage before any claim of global suite health.
16669:+The network interruption did not change the authority state: G1 remains `IN_PROGRESS`, external-evidence blocked, and live deploy remains **NO-GO**. The resumed pass continued only safe, non-operator remediation and verification. It did not transition `CutoverGuard` to `LIVE_ENABLED`, create Q1/staged smoke evidence, activate credentials, place/cancel/redeem/wrap/unwrap live orders, or mutate production DB truth.
16673:+- `src/control/cutover_guard.py` now binds `LIVE_ENABLED` operator evidence to a JSON readiness report with `status=PASS`, `gate_count=17`, `passed_gates=17`, `staged_smoke_status=PASS`, and `live_deploy_authorized=false`; arbitrary note files or failing readiness reports are rejected.
16674:+- Active transition shell scripts `scripts/resume_backfills_sequential.sh` and `scripts/post_sequential_fillback.sh` no longer export a plaintext WU key fallback; they require operator-provided `WU_API_KEY` before WU-dependent steps.
16675:+- `scripts/rebuild_settlements.py` was added/registered as a dry-run-by-default, verified-observation-only high-track settlement repair helper for authority tests; no production DB mutation was performed.
16676:+- Legacy test compatibility was repaired around ENS member extrema, settlement helper objects created via `__new__`, injected Polymarket client adapters, P0 preflight harnesses, complete bin topologies, and explicit metric/unit identity fixtures.
16683:+pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_forecast_source_registry.py tests/test_calibration_retrain.py tests/test_v2_adapter.py tests/test_settlement_commands.py tests/test_exit_safety.py tests/test_risk_allocator.py tests/test_live_readiness_gates.py tests/test_cutover_guard.py: 128 passed, 2 skipped, 22 warnings in 5.53s
16684:+python3 scripts/live_readiness_check.py --json: exit 1 expected; status FAIL; gate_count=17; passed_gates=16; failing gate G1-02 Q1 Zeus-egress evidence missing; staged_smoke_status=FAIL; live_deploy_authorized=false
16688:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py: GREEN=241 YELLOW=0 RED=0, STATUS GREEN; wrote r3/drift_reports/2026-04-28.md
16689:+python3 -m py_compile scripts/live_readiness_check.py scripts/rebuild_settlements.py scripts/r3_drift_check.py src/control/cutover_guard.py src/data/daily_obs_append.py src/data/polymarket_client.py src/engine/evaluator.py src/execution/executor.py src/signal/ensemble_signal.py src/strategy/market_analysis.py src/signal/day0_signal.py: PASS
16695:+- Missing signed Q1 Zeus-egress evidence and missing signed staged-live-smoke evidence still keep `scripts/live_readiness_check.py` at `16/17` and `staged_smoke_status=FAIL`.
16697:+- Full-suite failures remain real blockers or explicit-waiver candidates. The current major clusters are `riskguard` canonical/fallback contract drift, `harvester` return/settlement-contract drift, runtime guard harness signature/telemetry drift, two rebuild-pipeline settlement tests, and strategy-tracker/audit fixture drift.
16698:+- TIGGE/data training is not live-training-ready from this workspace alone: local switch/stub tests are green, but real payload/data availability, signed retrain evidence, staged smoke, and Q1 evidence are absent.
16699:+- A1 strategy benchmark evidence is not proof of live-market alpha; live strategy promotion still needs current data, calibration, benchmark, risk, and staged/live readiness evidence.
16710:+| `zeus-live-readiness-2026-04-26` | `claude/live-readiness-completion-2026-04-26` | `6c42aa7` | already ancestor of `plan-pre5` (`branch_only=0`) | `.code-review-graph/graph.db` only | no merge needed; do not import derived graph DB |
16720:+mystifying targeted pytest at updated HEAD 5bd9be8: 66 passed in 0.17s for tests/test_backtest_purpose_contract.py tests/test_backtest_skill_economics.py tests/test_backtest_training_eligibility.py tests/test_dissemination_schedules.py tests/test_forecasts_writer_provenance_required.py
16726:+- `mystifying` is valuable but not a blind merge into the current dirty R3 workspace. It adds F11/backtest decision-time truth work (`src/backtest/*`, `src/data/dissemination_schedules.py`, forecast issue-time/availability-provenance scripts/tests) plus planning-only F11 apply runbook and WU empty-provenance triage docs; it overlaps current dirty files: `architecture/script_manifest.yaml`, `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`, and `src/data/forecasts_append.py`.
16727:+- The `src/data/forecasts_append.py` overlap is semantic, not just textual: current R3 work stamps forecast-source registry identity (`source_id`, `raw_payload_hash`, `captured_at`, `authority_tier`), while F11 adds `forecast_issue_time` + `availability_provenance`. The correct integration is to preserve both families of provenance in one writer/schema contract.
16735:+4. Resolve the four overlapping files deliberately, especially `src/data/forecasts_append.py`, by combining R3 source identity and F11 availability provenance rather than choosing one side.
16736:+5. Run topology navigation/planning-lock after the F11 files are present, then targeted tests (`66 passed` group), R3 targeted gates, `scripts/live_readiness_check.py --json`, and a full-suite sample.
16737:+6. Re-evaluate live plan only after the F11 merge: F11 can improve training/backtest readiness by removing forecast hindsight leakage, but it does **not** satisfy Q1 Zeus-egress, staged-live-smoke, TIGGE payload, calibration retrain, or `live-money-deploy-go` evidence.
16750:+Summary: Incorporated the already-reviewed M1 command-side grammar expansion into `architecture/invariants.yaml` under amendment `R3-M1-INV-29-2026-04-27`, updated the operator decision register to close INV-29 for M1 grammar values only, and added tests/topology routing so future agents cannot treat the amendment as runtime semantics or live authorization.
16756:+Post-close: critic Planck PASS; verifier Pasteur PASS. M2 may now freeze next, but M2 implementation requires its own boot/review and live/cutover gates remain closed.
16767:+Task: R3 M1 lifecycle grammar — command-side grammar amendment and RED durable cancel proxy
16780:+- tests/test_command_grammar_amendment.py / tests/test_riskguard_red_durable_cmd.py / tests/test_command_bus_types.py / tests/test_venue_command_repo.py
16783:+Implemented M1 to the gate edge: added command-side grammar states/events, preserved U2 order/trade fact separation and NC-NEW-E (`RESTING` not a `CommandState`), made unresolved lookup derive from `IN_FLIGHT_STATES`, and added RED force-exit durable CANCEL proxy command emission only inside `cycle_runner._execute_force_exit_sweep`. RiskGuard remains non-writing; no live SDK side effects were added. M1 remains blocked from full completion by the open `INV-29 amendment` operator/governance gate.
16786:+- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py` -> 14 passed.
16788:+- Combined M1 focused suite + digest/neg-risk/dual-track antibody -> 142 passed.
16819:+- tests/test_unknown_side_effect.py / tests/test_v2_adapter.py / tests/test_executor_command_split.py / tests/test_live_execution.py / tests/test_digest_profile_matching.py
16838:+- No live venue submission/cutover is enabled; executor remains behind cutover, heartbeat, collateral, and executable-snapshot gates.
16839:+- No production DB/state artifact was mutated.
16841:+- M3 websocket ingest, M4 cancel/replace policy, M5 exchange reconciliation sweep, calibration retrain go-live, and TIGGE activation remain out of scope.
16846:+- `pytest -q -p no:cacheprovider tests/test_unknown_side_effect.py tests/test_v2_adapter.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_command_recovery.py tests/test_command_bus_types.py tests/test_command_grammar_amendment.py tests/test_venue_command_repo.py tests/test_digest_profile_matching.py::test_r3_m2_unknown_side_effect_routes_to_m2_profile_not_heartbeat` -> 169 passed, 1 skipped, 1 warning (deprecation warning from compatibility wrapper).
16847:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M2` -> GREEN=10 YELLOW=0 RED=0.
16915:+- Added `PolymarketUserChannelIngestor` with official user-channel subscription payload (`auth`, condition-ID `markets`, `type=user`) and optional live start via lazy `websockets` import.
16923:+- Wired executor submit paths to call the WS gap guard before DB/venue side effects.
16937:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M3: GREEN=12 YELLOW=0 RED=0
16957:+Known deferrals / risks:
17024:+- Added `exit_mutex_holdings` schema to `init_schema` without importing execution modules during DB initialization.
17033:+pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_digest_profile_matching.py::test_r3_m4_cancel_replace_routes_to_m4_profile_not_heartbeat tests/test_executor.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py: 58 passed, 2 skipped
17035:+pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_digest_profile_matching.py tests/test_executor.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py tests/test_venue_command_repo.py tests/test_collateral_ledger.py: 158 passed, 2 skipped, 4 known deprecation warnings
17036:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M4: GREEN=10 YELLOW=0 RED=0
17042:+Known non-goals / risks:
17050:+- M4 closed after post-close third-party critic+verifier PASS. M5/R1 are ready for phase-entry planning/topology boot; R1 still carries Q-FX-1 operator gate. No live venue submission, CLOB cutover, or production DB mutation is authorized.
17113:+- Added `exchange_reconcile_findings` schema and unresolved partial unique index to `init_schema` without importing execution modules from state DB boot.
17124:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M5: GREEN=10 YELLOW=0 RED=0
17128:+Known non-goals / risks:
17161:+- Post-remediation reruns: `python3 -m py_compile src/execution/exchange_reconcile.py tests/test_exchange_reconcile.py` PASS; focused M5/V2/digest gate `38 passed`; broader M5 dependency gate `133 passed, 6 skipped, 18 known deprecation warnings`; M5 drift remains `GREEN=10 YELLOW=0 RED=0`.
17168:+- Verifier Pasteur the 2nd: PASS after `M5_post_close_2026-04-27.md` artifact creation; local verification reran py_compile, broader M5 gate (`133 passed, 6 skipped`), and M5 drift (`GREEN=10 YELLOW=0 RED=0`).
17181:+Task: R3 R1 settlement / redeem command ledger — durable command states, Q-FX-1 gate, tx-hash recovery
17187:+- `src/execution/settlement_commands.py`
17192:+- `tests/test_settlement_commands.py`
17212:+- Added `settlement_commands.py` with R1 `SettlementState`, `SettlementResult`, request/submit/reconcile APIs, savepoint-based transitions, hashed event payloads, and tx-hash receipt recovery.
17213:+- Added `settlement_commands` and `settlement_command_events` schema to `src/state/db.py` without importing execution modules from schema boot.
17216:+- Classified legacy `USDC_E` payout separately as `REDEEM_REVIEW_REQUIRED`.
17222:+python3 -m py_compile src/execution/settlement_commands.py src/execution/harvester.py src/state/db.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py: PASS
17223:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat: 7 passed
17224:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_collateral_ledger.py tests/test_v2_adapter.py tests/test_exchange_reconcile.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat: 76 passed, 4 known deprecation warnings
17225:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat tests/test_collateral_ledger.py tests/test_v2_adapter.py tests/test_exchange_reconcile.py tests/test_venue_command_repo.py tests/test_exit_safety.py tests/test_user_channel_ingest.py: 151 passed, 22 known deprecation warnings
17226:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase R1: GREEN=7 YELLOW=0 RED=0
17227:+python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 settlement redeem command ledger implementation
17242:+- T1 is unfrozen for phase entry only; live venue/prod DB/CLOB cutover remain unauthorized.
17244:+Known non-goals / risks:
17248:+- `tests/test_harvester_dr33_live_enablement.py` has a pre-existing stale expectation for `physical_quantity` (`daily_maximum_air_temperature` vs current `mx2t6_local_calendar_day_max`) and was not used as R1 closeout evidence.
17249:+- Current real adapter still returns `REDEEM_DEFERRED_TO_R1`; R1 tests use fake adapters to prove ledger semantics without SDK/chain side effects.
17254:+- Preserve R1 live-side-effect boundaries: no live redeem, production DB mutation, or CLOB cutover authorization.
17261:+# R3 T1 work record — FakePolymarketVenue paper/live parity
17265:+Task: R3 T1 FakePolymarketVenue — same adapter protocol, deterministic failure injection, schema-identical paper/live event shapes
17278:+- `tests/integration/test_p0_live_money_safety.py`
17299:+- Added `PolymarketV2AdapterProtocol` as the shared live/paper adapter contract.
17301:+- Added T1 P0 integration tests with the phase-card acceptance names, including duplicate submit idempotency, partial fills, heartbeat miss handling, cutover wipe simulation, pUSD/token insufficiency blocks, MATCHED→FAILED rollback, and paper/live schema parity against a mock live adapter.
17309:+python3 -m py_compile tests/fakes/polymarket_v2.py tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/conftest.py src/venue/polymarket_v2_adapter.py: PASS
17310:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py: 15 passed
17311:+pytest -q -p no:cacheprovider tests/integration/test_p0_live_money_safety.py::test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary tests/integration/test_p0_live_money_safety.py::test_paper_and_live_produce_identical_journal_event_shapes: 2 passed
17312:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 88 passed
17313:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_exchange_reconcile.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 155 passed, 6 skipped, 18 known deprecation warnings
17314:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase T1: GREEN=11 YELLOW=0 RED=0
17321:+- Added `tests/integration/test_p0_live_money_safety.py::test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary`.
17332:+Known non-goals / risks:
17334:+- Fake venue is test-only and does not authorize live venue submit/cancel/redeem, production DB mutation, credentialed activation, or CLOB cutover.
17335:+- T1 proves adapter protocol/envelope/result parity and deterministic P0 scenario simulation; later G1 still owns 17/17 live readiness and staged smoke gates.
17336:+- The fake uses test-only imports and in-memory state; production paper/live mode wiring remains outside T1 unless separately authorized.
17357:+- A1 may enter phase; A2/G1 remain dependency-gated and no live venue/prod DB/cutover authorization is implied.
17364:+# U2 work record — raw provenance schema
17368:+Task: R3 U2 raw provenance schema — five projection backbone and command envelope gate
17384:+- tests/test_provenance_5_projections.py / tests/test_executable_market_snapshot_v2.py / tests/test_command_bus_types.py / tests/test_command_recovery.py / tests/test_venue_command_repo.py / tests/test_executor_command_split.py / tests/test_neg_risk_passthrough.py / tests/test_digest_profile_matching.py
17387:+Implemented U2's raw provenance backbone: append-only venue submission envelopes, order facts, trade facts, position lots, and provenance envelope events; `venue_commands.envelope_id` is required with the U1 `snapshot_id` gate; executor entry/exit flows now persist a pre-submit `VenueSubmissionEnvelope` before SDK contact; the command insert envelope gate validates token+side+price+size so a command cannot cite a different order shape. Added U2 topology routing and documented DDL-vs-acceptance schema decisions.
17391:+- `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py` -> 13 passed.
17394:+- `pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py` -> 47 passed, 2 skipped.
17395:+- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_u2_raw_provenance_routes_to_u2_profile_not_heartbeat tests/test_neg_risk_passthrough.py tests/test_collateral_ledger.py` -> 36 passed, 4 deprecation warnings.
17402:+- Leader post-close recheck: `r3_drift_check.py --phase U2` -> GREEN; `tests/test_provenance_5_projections.py` -> 13 passed.
17417:+- Update stale test fixtures to satisfy current explicit metric/slippage/bin-topology laws without weakening production fail-closed behavior.
17420:+- Refresh assumption/test metadata for the Hong Kong HKO floor/truncate exception while preserving `SettlementSemantics.for_city()` as authority.
17424:+- Do not fabricate Q1 Zeus-egress or staged-live-smoke evidence.
17425:+- Do not transition CutoverGuard to live, activate credentials, place/cancel/redeem live orders, or mutate production DB truth.
17426:+- Do not weaken metric identity, SlippageBps, settlement semantics, risk fail-closed, or WS gap laws.
17431:+- `live_readiness_check.py --json` remains fail-closed until external evidence exists.
17436:+After targeted R3 gates passed, a repo sample `pytest -q -p no:cacheprovider --maxfail=30` still failed at 30 failures. These are not operator authorization actions, so the allowed follow-up is to remediate safe local correctness/test-contract blockers without live venue side effects and without fabricating Q1/staged evidence.
17439:+- Add/repair missing non-live settlement rebuild helper expected by authority tests (`scripts/rebuild_settlements.py`) and register it.
17440:+- Refresh stale fixtures that insert legacy `settlements` rows without `temperature_metric` while preserving current dual-track law.
17442:+- Repair test/runtime seams that should fail closed but also remain unit-testable without installed live SDKs.
17446:+- no live submit/cancel/redeem/wrap side effects;
17448:+- no production DB mutation;
17450:+- no weakening of settlement semantics, dual-track metric identity, RED fail-closed risk behavior, or source truth boundaries.
17453:+- a failure requires live credentials, network egress to Polymarket/TIGGE, or production host evidence;
17462:+- strengthen `src/control/cutover_guard.py` so `LIVE_ENABLED` evidence must be a JSON readiness report proving `status=PASS`, `gate_count=17`, `passed_gates=17`, `staged_smoke_status=PASS`, and `live_deploy_authorized=false`;
17467:+- no creation of fake production Q1/staged evidence;
17468:+- no live venue side effects.
17470:+## Expansion — active script hardcoded WU key removal (2026-04-27)
17472:+Security review found plaintext WU key fallbacks in active transition shell scripts. Although the key is documented elsewhere as a public web key for Weather Underground browser traffic, active scripts exporting a key value create avoidable security-review noise and stale-credential risk.
17476:+- fail closed with an operator-supplied `WU_API_KEY` requirement before running WU backfill steps.
17480:+- no external WU calls;
17481:+- no production DB writes.
17485:+After the first blocker batch, `pytest -q -p no:cacheprovider --maxfail=30` improved to 16 failed / 14 evidence-fixture errors before maxfail. Remaining safe no-operator work is limited to stale unit fixture compatibility and fail-closed contract assertions. This batch may update tests or narrow compatibility shims, but must not weaken live-money safety.
17489:+- add defensive compatibility fallback where it does not affect production semantics (e.g. RNG forwarding helpers using legacy `member_maxes` test doubles);
17494:+- no production evidence fabrication;
17496:+- no live SDK calls or external data fetches;
17497:+- no DB truth mutation outside temp/in-memory tests.
17510:+- `riskguard`: several tests expect legacy fallback/projection behavior, while current runtime code requires canonical `position_current` truth and now reports different fail-closed details/levels. This is a contract decision, not a fixture-only edit; do not weaken runtime risk law casually.
17511:+- `harvester`: tests expect return keys such as `pairs_created` / `settlements_found`, but the current preflight/fail-closed path returns a different shape. Requires a narrow harvester contract review before source edits.
17513:+- Rebuild pipeline: remaining settlement unit/unknown-unit tests need review against the new dry-run high-track helper and current settlement semantics.
17516:+Allowed next action remains: narrow, evidence-backed triage of these clusters with topology navigation and planning-lock evidence before touching high-risk riskguard/harvester/runtime source. Full-suite green is still not claimed, and G1/live deploy remain NO-GO until this is either fixed or explicitly waived by packet authority plus operator evidence.
17555:+- `evaluate_live_shadow()` is read-only and consumes preloaded shadow corpora; it does not contact a venue or activate credentials.
17557:+- Candidate stubs do not implement alpha and do not authorize live trading.
17561:+- No live strategy promotion.
17562:+- No live venue submit/cancel/redeem.
17563:+- No production DB/state artifact mutation.
17564:+- No CLOB cutover or credentialed live-shadow activation.
17572:+- Post-close critic Carson the 2nd APPROVE and verifier Maxwell the 2nd PASS; A2 unfrozen for phase entry while G1 remains blocked by A2/live-readiness gates.
17587:+`src/risk_allocator/governor.py` exports:
17604:+`config/risk_caps.yaml` defines engineering defaults for cap policy. Operators may tune later; absence of the file still loads in-code defaults.
17616:+- `load_position_lots()` is read-only and consumes latest append-only `position_lots` state per position; it does not repair, insert, update, delete, or mutate production truth.
17627:+- No live venue submit/cancel/redeem authorization.
17628:+- No production DB/state artifact mutation.
17629:+- No CLOB cutover, credentialed WS activation, live strategy promotion, or G1 live deployment.
17636:+- Post-close third-party critic Parfit the 2nd APPROVE and verifier Godel the 2nd PASS recorded in `../reviews/A2_post_close_2026-04-27.md`; G1 may enter phase work, but live deployment remains separately gated.
17643:+# G1 interface draft — live-readiness gates
17654:+- Entry point: `python3 scripts/live_readiness_check.py [--json] [--evidence-root PATH]`
17660:+  - `0` only when all 17 gates pass and staged-live-smoke evidence passes
17661:+  - `1` when any gate fails or staged-live-smoke evidence is missing/invalid
17665:+- `live_deploy_authorized` remains `false`.
17666:+- Operator gate `live-money-deploy-go` remains required.
17667:+- The script does not run live smoke, submit, cancel, redeem, cut over, activate
17668:+  credentials, promote strategies, or mutate production DB/state.
17701:+| `NOT_CANCELED` | `CANCEL_FAILED` | `REVIEW_REQUIRED` | replacement blocked until operator/reconcile action |
17702:+| `UNKNOWN` | `CANCEL_REPLACE_BLOCKED` | `REVIEW_REQUIRED` | replacement blocked until M5 proves absence |
17715:+- No CLOB v2 cutover or live venue activation.
17716:+- No production DB mutation.
17750:+- Forbidden: live venue submit/cancel/redeem side effects.
17764:+- No live CLOB cutover.
17765:+- No production DB mutation.
17766:+- No R1 settlement/redeem command ledger.
17782:+`src/execution/settlement_commands.py` exports:
17785:+  - `REDEEM_INTENT_CREATED`
17786:+  - `REDEEM_SUBMITTED`
17787:+  - `REDEEM_TX_HASHED`
17788:+  - `REDEEM_CONFIRMED`
17789:+  - `REDEEM_FAILED`
17790:+  - `REDEEM_RETRYING`
17791:+  - `REDEEM_REVIEW_REQUIRED`
17793:+- `init_settlement_command_schema(conn) -> None`
17802:+- `settlement_commands`
17803:+- `settlement_command_events`
17805:+`REDEEM_TX_HASHED` is the recovery anchor. Chain receipt reconciliation moves tx-hashed commands to `REDEEM_CONFIRMED` or `REDEEM_FAILED`.
17811:+- Legacy `USDC_E` payout creates `REDEEM_REVIEW_REQUIRED`, not pUSD accounting.
17816:+- No live redeem side effects in tests/default runtime.
17817:+- No production DB mutation.
17819:+- No automatic position settlement from redeem success/failure; settlement terminalization remains separate.
17829:+- T1 is unfrozen for phase entry only; live venue/prod DB/CLOB cutover remain unauthorized.
17836:+# Frozen Interface — T1 FakePolymarketVenue paper/live parity
17838:+Phase: T1 — FakePolymarketVenue (paper/live parity)
17846:+- `PolymarketV2AdapterProtocol` — runtime-checkable shared live/paper adapter protocol.
17857:+- Fake submit returns the same `SubmitResult` / `VenueSubmissionEnvelope` shape as the live adapter.
17860:+- Heartbeat miss, open-order wipe, cancel-not-canceled, partial fill, timeout-after-post, restart-mid-cycle, and MATCHED→FAILED-chain scenarios are injectable without live I/O. Restart-mid-cycle records a recovery boundary while preserving venue-side order/idempotency state.
17861:+- Fake redeem returns `REDEEM_DEFERRED_TO_R1` to preserve R1 command-ledger ownership.
17865:+- No live venue submit/cancel/redeem side effects.
17866:+- No production DB mutation.
17869:+- No production paper/live split path; the fake is test-scoped.
17916:+  least `min_order_size`, and provided `neg_risk` must match the snapshot.
17918:+  command insertion gate before V2 preflight, signing, SDK submit, or live
17920:+- Legacy live compatibility paths without a snapshot reject before SDK contact;
17922:+- Fresh DBs create `venue_commands.snapshot_id NOT NULL`; legacy DBs are
17931:+- Do not implement U2 projection/raw provenance tables in U1.
17932:+- Do not treat U1 closure as live cutover approval; Q1/cutover and later
17933:+  M/R/T phases still own deployment/reconciliation/settlement safety.
17937:+- U2 owns raw provenance projection tables and envelope/order/trade fact
17956:+- `src/state/db.py::init_provenance_projection_schema`
17962:+- `src/state/venue_command_repo.py::append_provenance_event`
17963:+- `src/state/venue_command_repo.py::load_calibration_trade_facts`
17973:+- `provenance_envelope_events`
17978:+- U2 facts are append-only raw provenance, not mutable current-state rows.
17979:+- Fresh DBs create `venue_commands.envelope_id NOT NULL`; legacy DBs may have a nullable column from migration, but new writes must pass Python enforcement.
17984:+- Command events remain in `venue_command_events`, but every command event is atomically mirrored into `provenance_envelope_events` with `source`, `observed_at`, `local_sequence`, and `payload_hash`.
17985:+- Order-side state grammar lives in `venue_order_facts`; trade-side state grammar lives in `venue_trade_facts`; neither should be collapsed into `CommandState`.
17987:+- Calibration/retraining reads through `load_calibration_trade_facts()` may consume only `CONFIRMED` trade facts; asking for `MATCHED` or `MINED` fails closed.
17989:+- Settlement/redeem trace in U2 is provenance-only via `provenance_envelope_events`; live redemption side effects remain deferred.
17995:+- Do not treat `MATCHED` or `MINED` as calibration truth.
17997:+- Do not bypass `venue_command_repo` for writes to U2 provenance tables.
17998:+- Do not treat U2 provenance closure as live cutover approval; Q1/cutover and downstream M/R/T/G gates still block live deployment.
17999:+- Do not implement websocket ingest, exchange reconciliation sweeps, live settlement redemption, risk allocation, or calibration retraining in U2.
18006:+- R1 owns settlement/redeem command ledger and Q-FX-1 resolution.
18007:+- F2 owns calibration retrain loop wiring.
18008:+- A2 owns risk allocator / portfolio governor semantics for optimistic vs confirmed capacity.
18051:+- Do not treat this interface as proof that live cutover is approved.
18072:+- Executor live entry/exit paths call the heartbeat gate before venue-command persistence or SDK contact.
18116:+- `PolymarketClient.get_balance()` is a compatibility wrapper that refreshes and commits a DB-backed CollateralLedger snapshot.
18118:+- Z4 wrap/unwrap APIs record durable command state only; they do not submit live chain transactions.
18125:+- Do not add direct redeem, wrap, or unwrap live side effects before R1/M4/T1 ownership lands.
18126:+- Do not treat Z4 closure as live cutover approval; Q1-zeus-egress and operator go/no-go remain open.
18130:+- R1 owns settlement redeem command ledger and Q-FX-1 final classification.
18133:+- T1 owns fake-venue parity for collateral, wrap/unwrap, and settlement behavior.
18147:+4. `src/state/db.py` should not import execution modules during schema initialization; duplicate idempotent DDL is safer than a state→execution import at DB boot.
18196:+- Generic `settlement`/R3 packet language can route to older heartbeat or settlement-rounding profiles unless R1 has strong `settlement_commands` / `REDEEM_TX_HASHED` / `Q-FX-1` phrases.
18197:+- Redemption command durability is distinct from settlement terminalization. A redeem command may fail or require review while the settlement/lifecycle truth remains governed by harvester/canonical settlement paths.
18203:+- Keep R1 command events independent from `venue_commands`; they model settlement/redeem chain state, not CLOB order state.
18248:+# U2 learning — raw provenance needed its own topology profile
18256:+A first U2 navigation request for `venue_order_facts`, `venue_trade_facts`, `position_lots`, and provenance envelope events was incorrectly routed to the R3 heartbeat-supervisor profile because no U2-specific digest profile existed. That false routing marked `src/state/db.py` and `src/state/venue_command_repo.py` as outside the allowed set even though U2 explicitly owns the raw provenance schema backbone.
18260:+Without a U2 profile, agents either stop unnecessarily or bypass topology guidance by hand. Both outcomes are bad for a high-risk state/schema slice: the first wastes context and the second weakens the planning-lock discipline around durable truth tables.
18264:+Add a narrow `r3 raw provenance schema implementation` profile to `architecture/topology.yaml`, plus a digest regression test, before editing U2 source files. The profile owns only the U2 schema/repo/executor/test surfaces and explicitly forbids M1 grammar, M3 websocket, M5 sweeping, R1 redemption side effects, and live venue submission.
18279:+Maximize R3 upgrade progress while preserving live-money safety, evidence
18281:+the source-of-truth packet without touching live execution code.
18285:+- Reframed the old CLOB V2 packet from "generic low-risk SDK swap" into
18289:+- Added a packet-local live-money contract instead of creating
18319:+- `r3_drift_check.py --phase Z0`: `GREEN=20 YELLOW=0 RED=0`.
18324:+## Open risk
18343:+Z0 requested `docs/architecture/polymarket_live_money_contract.md`, but topology navigation classified `docs/architecture/` as outside known workspace routes. The docs root rules also say new active docs belong in declared tracked subroots; there is no active `docs/architecture` subroot.
18351:+Emit the live-money contract as packet-local evidence at `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`, register it in the packet AGENTS, and adapt Z0 tests to that path.
18366:+Maximize R3 live-money upgrade progress while keeping every new venue-side
18368:+that meant adding a narrow CutoverGuard control surface without choosing a live
18379:+- Gated `execute_intent`, `execute_exit_order`, and `_live_order` before command
18390:+  control module and targeted K2 call-site gates, but not DB schema, lifecycle
18416:+  `tests/test_cutover_guard.py tests/test_executor.py tests/test_live_execution.py
18423:+- `r3_drift_check.py --phase Z1`: `GREEN=15 YELLOW=0 RED=0`.
18431:+## Open risk
18452:+- Added `src/contracts/venue_submission_envelope.py` as the provenance contract.
18454:+  live placement/cancel/query paths, with V2 adapter preflight before submit.
18457:+- Reworked neg-risk antibodies so `neg_risk` is allowed only as V2 venue
18458:+  provenance, not strategy/settlement logic.
18464:+1. Compatibility code is live code.
18466:+   call `create_order/post_order` directly. That preserved a V1-shaped live
18502:+- Treat compatibility shims as live-money surfaces; test them directly.
18503:+- Never let a mock-only convenience path bypass the new live boundary.
18511:+## Remaining non-goals / risks
18546:+| INV-29 amendment | M1 (state grammar) | governance | Approve closed-law amendment for grammar-additive CommandState changes | `architecture/invariants.yaml` + `docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/inv_29_amendment_2026-04-27.md` | CLOSED: incorporated 2026-04-27; no live venue/cutover authorization |
18547:+| TIGGE-ingest go-live | F3 (TIGGE active) | data-source | Approve activating TIGGE ECMWF data ingest into ensemble pipeline | `evidence/tigge_ingest_decision_*.md` + `ZEUS_TIGGE_INGEST_ENABLED=1` + operator-approved local JSON payload path (`payload_path:` in artifact or `ZEUS_TIGGE_PAYLOAD_PATH`) | OPEN |
18548:+| Calibration retrain | F2 (Platt re-fit) | training-trigger | Approve recalibrating Extended Platt against new corpus | `evidence/calibration_retrain_decision_2026-04-26.md` | OPEN |
18570:+- TIGGE-ingest: not on critical path. F1/F2 can be wired without TIGGE.
18571:+- Calibration retrain: can wait until live data accumulates a fresh
18577:+- Q1-zeus-egress: Z2 adapter `preflight()` returns failure; live
18584:+  does not authorize M2 runtime semantics, live venue submission, or cutover.
18585:+- TIGGE: closed gate raises `TIGGEIngestNotEnabled`; open gate without a local
18586:+  operator payload raises `TIGGEIngestFetchNotConfigured`; open gate with a
18588:+  through `TIGGEIngest` without Open-Meteo HTTP. External TIGGE archive HTTP/GRIB
18606:+Summary: Incorporates the already-implemented and reviewed M1 command-side grammar expansion into the authoritative `INV-29` invariant. This closes the M1 governance gate narrowly so M1 can move from `COMPLETE_AT_GATE` to `COMPLETE` and M2 can be frozen next after M1 closeout/review. It does not authorize M2 unknown-side-effect runtime semantics, live venue submission, CLOB cutover, or any operator go-live gate.
18615:+- `docs/authority/zeus_current_delivery.md` treats `architecture/**` and lifecycle grammar as planning-lock/governance work.
18628:+- Does not authorize live venue submission, CLOB v2 cutover, TIGGE activation, or calibration retrain go-live.
18671:+  Polymarket; PyPI provenance links to `Polymarket/py-clob-client-v2@v1.0.0`.
18692:+  - `get_neg_risk(token_id)`
18715:+Z2 should pin provenance around `VenueSubmissionEnvelope`, support both one-step
18717:+unit tests, and keep live preflight fail-closed when Q1-zeus-egress evidence is
18734:+Per the R3 loop directive, A1 cannot unfreeze A2/G1 until the additional post-close third-party critic and verifier pass. A1 was marked complete only after pre-close critic Ohm the 2nd APPROVE and verifier Harvey the 2nd PASS. This artifact records the post-close gate. The paired post-close critic and verifier have passed; A2 may be unfrozen for phase entry while G1 remains blocked by A2 and live-readiness gates.
18744:+pytest -q -p no:cacheprovider tests/test_strategy_benchmark.py tests/test_fake_polymarket_venue.py tests/test_fdr.py tests/test_kelly.py tests/test_kelly_cascade_bounds.py tests/test_kelly_live_safety_cap.py: 82 passed
18745:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A1: GREEN=7 YELLOW=0 RED=0
18759:+- Metrics are complete: EV after fees/slippage, spread, fill probability, adverse selection, capital-lock/time-to-resolution, liquidity decay, opportunity cost, drawdown duration, calibration error, and PnL split are represented/tested.
18760:+- No live activation found in A1 source: live-shadow uses preloaded corpora only; no credentials, CLOB cutover, production DB, live submit/cancel/redeem path.
18783:+- Fresh checks were green: py_compile, focused A1 tests (`11 passed`), adjacent strategy/fake suite (`82 passed`), A1 drift (`GREEN=7 YELLOW=0 RED=0`), and closeout (`ok=true`, `blocking_issues=[]`).
18784:+- No live venue, credentialed, production DB, or CLOB cutover authorization was exercised.
18788:+Decision: A2 may be marked ready for phase entry after this A1 post-close PASS. G1 remains blocked by A2 and the live deploy gate. No live venue submit/cancel/redeem, production DB mutation, credentialed live-shadow activation, live strategy promotion, or CLOB cutover is authorized.
18807:+pytest -q -p no:cacheprovider tests/test_strategy_benchmark.py tests/test_fake_polymarket_venue.py tests/test_fdr.py tests/test_kelly.py tests/test_kelly_cascade_bounds.py tests/test_kelly_live_safety_cap.py: 82 passed
18808:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A1: GREEN=7 YELLOW=0 RED=0
18822:+- Metrics cover EV after fees/slippage, spread, fill probability, adverse selection, capital-lock/time-to-resolution, liquidity decay, opportunity cost, drawdown/duration, calibration error, and PnL split.
18823:+- Paper path uses the T1 fake/protocol seam; live-shadow consumes preloaded corpora only. No live submit/cancel/redeem, credentials, or production DB mutation path found.
18838:+- Verification commands passed: py_compile, focused A1 tests (`11 passed`), adjacent strategy/fake suite (`82 passed`), drift (`GREEN=7 YELLOW=0 RED=0`), closeout (`ok=true`, `blocking_issues=[]`).
18839:+- No live venue, credentialed, production DB, or CLOB cutover authorization was exercised.
18858:+Per the R3 loop directive, A2 cannot unfreeze G1 until the additional post-close third-party critic and verifier pass. A2 was marked complete only after pre-close critic Euclid the 2nd APPROVE and verifier Ampere the 2nd PASS. This artifact records the post-close gate. The paired post-close critic and verifier have passed; G1 may be unfrozen for phase entry while live deployment remains blocked by the G1 readiness gate and explicit operator authorization.
18866:+python3 -m py_compile src/contracts/execution_intent.py src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/data/polymarket_client.py src/engine/cycle_runner.py src/engine/cycle_runtime.py tests/test_risk_allocator.py tests/test_executor.py: PASS
18867:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 24 passed
18868:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py tests/test_k2_slice_e.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py: 82 passed, 6 skipped
18869:+pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
18870:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
18883:+- No premature G1/live authorization: `_phase_status.yaml` marks A2 COMPLETE while recording post-close review pending at review time, keeps G1 PENDING and `ready_to_start: []`, and `current_state.md` says live placement still needs Q1/cutover plus gates.
18886:+- Artifacts/status/receipt are coherent after close: work record says pre-close pass / closeout complete / post-close pending at review time, receipt records A2 was marked complete only after pre-close critic+verifier, and receipt forbids live venue submission, cancel/redeem side effects, production DB mutation, CLOB cutover, live strategy promotion, and credentialed activation.
18887:+- Fresh critic verification: `pytest -q -p no:cacheprovider tests/test_risk_allocator.py` → `24 passed`.
18898:+- Current state and frozen interface were consistent with the post-close-pending freeze at review time: A2 COMPLETE, G1 frozen, no live submit/cancel/redeem, no production DB/state mutation, no CLOB cutover or credentialed live activation.
18900:+- Reproducibility checks passed: py_compile + combined executor/heartbeat/client suite (`82 passed, 6 skipped`), digest profile (`1 passed`), and drift (`GREEN=12 YELLOW=0 RED=0`).
18904:+Decision: G1 may be unfrozen for phase entry only. This does **not** authorize live venue submit/cancel/redeem, production DB mutation, credentialed live-shadow activation, live strategy promotion, CLOB cutover, or live deployment. G1 itself remains governed by `blocking_operator_gate: live-money-deploy-go (17/17 PASS + smoke)` and explicit operator authorization.
18920:+python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
18921:+python3 -m py_compile src/contracts/execution_intent.py src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/data/polymarket_client.py src/engine/cycle_runner.py src/engine/cycle_runtime.py tests/test_risk_allocator.py tests/test_executor.py: PASS
18922:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 24 passed
18923:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py tests/test_k2_slice_e.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py: 82 passed, 6 skipped
18924:+pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
18925:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
18942:+- Original A2 guarantees remain: typed `ExecutionIntent` metadata, production construction/runtime candidate metadata population, cycle-start governor refresh, and read-only/governance-only allocator behavior.
18943:+- No CLOB cutover, live activation, production DB mutation, or live venue submit/cancel/redeem authorization was found in A2 surfaces.
18952:+- Fresh verification passed: py_compile, focused A2 tests (`24 passed`), combined executor/heartbeat/client suites (`82 passed, 6 skipped`), digest profile (`1 passed`), A2 drift (`GREEN=12 YELLOW=0 RED=0`), and scoped closeout (`closeout ok`, changed_files=36).
18959:+Decision: A2 may be marked complete after this pre-close review. Per the standing R3 loop directive, G1 remains frozen until the required A2 post-close third-party critic + verifier pass completes. No live venue submit/cancel/redeem, production DB mutation, credentialed live-shadow activation, live strategy promotion, CLOB cutover, or live deployment is authorized.
18975:+  - F1 code slice passed scope review: forecast-source registry/provenance wiring only, no calibration retrain, no active TIGGE ingest, no settlement routing, and no live venue behavior.
18983:+- `docs/operations/current_state.md` now names F1 as `COMPLETE / POST-CLOSE REVIEW BLOCKER REMEDIATION`, states F2/F3 remain frozen until post-close critic+verifier PASS, and preserves the M2/INV-29 and live-money freeze points.
18990:+- `pytest -q -p no:cacheprovider tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_backfill_openmeteo_previous_runs.py tests/test_ensemble_client.py tests/test_ensemble_signal.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill` -> `46 passed`.
18991:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F1` -> `GREEN=20 YELLOW=0 RED=0`.
18994:+- Full F1/data/signal regression suite (`tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py tests/test_ensemble_client.py tests/test_backfill_openmeteo_previous_runs.py tests/test_etl_forecasts_v2_from_legacy.py tests/test_ensemble_signal.py tests/test_digest_profile_matching.py`) -> `103 passed`.
18999:+  - Confirmed `current_state.md` no longer points at stale U2/F1 state, F1 card acceptance tests are reconciled, post-close artifact exists, TIGGE remains dormant/gated, no calibration retrain or Platt refit, no settlement routing, no live venue behavior, and no `EnsembleSignal` diff.
19000:+  - Fresh critic evidence: py_compile ok, full F1/data/signal suite `103 passed`, F1 drift `GREEN=20 YELLOW=0 RED=0`, `git diff --check` clean.
19002:+  - Independently reran py_compile, full F1/data/signal suite (`103 passed`), focused verifier subset (`46 passed`), F1 drift (`GREEN=20 YELLOW=0 RED=0`), `git diff --check`, `git diff -- src/signal/ensemble_signal.py`, and planning-lock.
19023:+  - Confirmed F1 stays in forecast-source registry/provenance wiring.
19024:+  - Confirmed TIGGE is registered but dormant behind operator artifact + `ZEUS_TIGGE_INGEST_ENABLED`.
19025:+  - Confirmed no calibration retrain / Platt refit / F2 behavior, no active TIGGE fetch / F3 behavior, no settlement source routing change, no live venue side effects.
19027:+  - Confirmed live append and previous-runs backfill both stamp F1 provenance columns.
19030:+  - Fresh verifier run: `pytest -q -p no:cacheprovider tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_backfill_openmeteo_previous_runs.py tests/test_ensemble_client.py tests/test_ensemble_signal.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill` -> 46 passed.
19036:+- `pytest -q -p no:cacheprovider tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill tests/test_ensemble_client.py tests/test_digest_profile_matching.py::test_r3_f1_forecast_source_registry_routes_to_f1_profile_not_heartbeat` -> 11 passed before script/backfill alignment.
19037:+- Focused F1/data/signal regression suite (`tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py tests/test_ensemble_client.py tests/test_backfill_openmeteo_previous_runs.py tests/test_etl_forecasts_v2_from_legacy.py tests/test_ensemble_signal.py tests/test_digest_profile_matching.py`) -> 103 passed after script/backfill alignment.
19039:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F1` -> GREEN (`GREEN=17 YELLOW=0 RED=0`).
19044:+## Non-blocking gaps / risks
19046:+- No live DB migration/run was performed; accepted because the schema change is additive/nullable and fresh + legacy ALTER paths are tested.
19047:+- No live TIGGE fetch with gate open was run; accepted because F1 intentionally does not implement active TIGGE fetch.
19063:+Task: R3 F2 calibration retrain loop wiring post-close third-party review
19074:+- R3 drift: `GREEN=10 YELLOW=0 RED=0`.
19076:+- Live retrain go artifact: intentionally absent; no `calibration_retrain_decision_*.md` exists in the repo package.
19085:+Meitner PASS reviewed receipt, F2 artifacts, receipt-wide topology navigation, map-maintenance/planning-lock, targeted tests, drift check, and absence of a live `calibration_retrain_decision_*.md` artifact. Key laws passed: dormant/operator-gated only, CONFIRMED-only corpus, FAIL blocks promotion, PASS replaces exact active `platt_models_v2` key inside the same transaction, and no Platt formula/manager/ensemble/TIGGE/settlement/Day0/hourly source-role changes in scope.
19093:+PASS. F2 is post-close verified. Next packet freeze may proceed only through the current ready/blocked phase rules: M2 remains held until M1 `INV-29 amendment`; no calibration retrain go-live is authorized.
19104:+Task: R3 F2 calibration retrain loop wiring
19112:+- Topology navigation: PASS, profile `r3 calibration retrain loop implementation`.
19114:+- Drift check: `GREEN=10 YELLOW=0 RED=0` for F2.
19116:+- Live retrain go artifact: intentionally absent; no `calibration_retrain_decision_*.md` exists in the repo package.
19139:+Phase: F3 `TIGGE ingest stub`
19146:+  - Confirmed F3 is a dormant TIGGE stub only: `fetch()` checks the dual gate before payload loading and raises `TIGGEIngestNotEnabled` while closed.
19147:+  - Confirmed TIGGE is `experimental`, `enabled_by_default=False`, `requires_operator_decision=True`, and gated by `docs/operations/task_2026-04-26_ultimate_plan/**/evidence/tigge_ingest_decision_*.md` plus `ZEUS_TIGGE_INGEST_ENABLED`.
19148:+  - Confirmed no real TIGGE HTTP/GRIB implementation, no calibration/Platt changes, no settlement/Day0/hourly routing change, and no live venue behavior.
19149:+- Verifier: Averroes — BLOCK (procedural/artifact/command-reproduction only).
19150:+  - Technical checks passed: py_compile, targeted pytest `17 passed`, F3 drift GREEN, `git diff --check` clean, and signal/calibration diff empty.
19170:+  - Independently reran py_compile, targeted pytest (`17 passed`), F3 drift (`GREEN=7 YELLOW=0 RED=0`), `git diff --check`, signal/calibration diff, current-state receipt binding, map-maintenance, planning-lock, and closeout with both `ULTIMATE_PLAN_R3.md` and the F3 boot evidence.
19184:+Phase: F3 `TIGGE ingest stub`
19191:+  - Code laws passed: construction is safe with gate closed; `fetch()` checks the gate before payload loading; no real TIGGE HTTP/GRIB implementation; registry marks TIGGE experimental, gated, and disabled by default; closed-gate import/probe succeeded.
19207:+- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F3` -> `GREEN=7 YELLOW=0 RED=0`.
19209:+- `git diff -- src/signal/ensemble_signal.py src/calibration/platt.py src/calibration/manager.py src/calibration/store.py` -> empty.
19216:+  - Confirmed construction is safe with gate closed, `fetch()` raises before payload/external I/O, TIGGE remains dormant behind the R3 ultimate-plan artifact glob + `ZEUS_TIGGE_INGEST_ENABLED`, and no real TIGGE HTTP/GRIB, calibration/Platt, settlement/Day0/hourly routing, or live venue behavior was added.
19218:+  - Confirmed the F3 pointer and pre-close artifact are present; py_compile passed; targeted pytest `17 passed`; F3 drift `GREEN=7 YELLOW=0 RED=0`; `git diff --check` clean; signal/calibration diff empty; current-state receipt, planning-lock, map-maintenance, and closeout checks passed with no selected-lane blockers.
19232:+Phase: G1 — 17 live-readiness gates + staged smoke
19237:+- `scripts/live_readiness_check.py`
19238:+- `tests/test_live_readiness_gates.py`
19256:+- Q1 Zeus-egress evidence and staged-live-smoke evidence both fail closed when absent or invalid.
19257:+- `live_deploy_authorized` remains false and the operator `live-money-deploy-go` gate remains required.
19258:+- The script performs no live submit/cancel/redeem/deploy, credential activation, production DB/state mutation, or live-smoke execution.
19260:+- G1 is not improperly closed: `_phase_status.yaml` remains `IN_PROGRESS` and current state says live deployment remains blocked.
19262:+Phase-close blocker called out by critic: real Q1 Zeus-egress and staged-live-smoke evidence is still missing. G1 must not be marked `COMPLETE` or live-ready until that evidence exists and the gate suite passes.
19268:+Verdict: PASS for the engineering implementation and PASS for correct refusal of live-readiness in the missing-evidence environment.
19273:+python3 -m py_compile scripts/live_readiness_check.py tests/test_live_readiness_gates.py: PASS
19274:+pytest -q -p no:cacheprovider tests/test_live_readiness_gates.py: 5 passed
19275:+pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat: 1 passed
19276:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase G1: GREEN=0 YELLOW=0 RED=0, STATUS GREEN
19277:+python3 scripts/live_readiness_check.py --json: exit 1 as expected; gate_count=17, passed_gates=16, G1-02 FAIL missing Q1 Zeus-egress evidence, staged_smoke_status=FAIL, live_deploy_authorized=false
19284:+- G1 remains `IN_PROGRESS` and not live-ready.
19308:+- R3 drift: M1 `GREEN=15 YELLOW=0 RED=0`.
19310:+- Scope guard: no `src/**` runtime files in this amendment receipt; no M2 runtime semantics/live/cutover authorization.
19314:+- Post-close critic: PASS — Planck found no blockers and confirmed no M2/live/cutover widening.
19319:+PASS. M1 INV-29 is post-close verified. M2 may now be frozen as the next packet; this does not authorize M2 implementation until its own topology/semantic boot is complete, and it does not authorize live venue submission or cutover.
19340:+- R3 drift: M1 `GREEN=15 YELLOW=0 RED=0`.
19371:+  - Confirmed transitions are grammar-checked, unresolved lookup derives from `IN_FLIGHT_STATES`, and RED proxy emission is scoped to `_execute_force_exit_sweep` without SDK place/cancel side effects.
19375:+  - Fresh verifier run: `python3 -m pytest -q tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py tests/test_command_bus_types.py tests/test_venue_command_repo.py` -> `106 passed`.
19376:+  - Confirmed gate-edge artifact, receipt, command grammar, RED proxy tests, and M2 freeze state.
19380:+Critic noted a non-blocking stale docstring in `src/execution/command_recovery.py` saying SUBMITTING without `venue_order_id` gets `EXPIRED`. The implementation and tests use `REVIEW_REQUIRED`; the docstring now matches the code. Literal escaped unicode markers in nearby recovery comments/docstrings were also normalized without changing runtime behavior.
19384:+- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py` -> `14 passed`.
19386:+- Combined M1 focused suite + digest/neg-risk/dual-track antibody -> `142 passed`.
19392:+- Post-review rerun: `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py tests/test_command_bus_types.py tests/test_venue_command_repo.py` -> `106 passed`.
19435:+- R3 drift report: `M2 | 10 GREEN | 0 YELLOW | 0 RED`.
19439:+Nonblocking risks:
19471:+- R3 drift: `GREEN=10 YELLOW=0 RED=0`.
19515:+- M3 current-state freeze note does not authorize live venue submission, CLOB cutover, M4 cancel/replace, or M5 reconciliation/unblock.
19521:+- R3 drift check: GREEN=12 YELLOW=0 RED=0
19543:+- R3 M3 drift check observed `GREEN=12 YELLOW=0 RED=0`.
19545:+- Procedural fix touched only docs/receipt status surfaces; no M4/M5 implementation or live cutover was introduced.
19550:+- Live venue submission, CLOB cutover, M5 reconciliation/unblock, credentialed WS activation, and production DB mutation remain unauthorized.
19596:+- `pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_digest_profile_matching.py tests/test_executor.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py` -> 80 passed, 2 skipped, warnings only.
19604:+- No live endpoint smoke was run; live WS activation remains operator-gated and outside M3 closeout.
19635:+- Docs/status were consistent at critic time: `current_state.md` said `M4 COMPLETE / POST-CLOSE REVIEW PENDING`, M5/R1 frozen pending critic+verifier, and no live venue/cutover/prod DB authorization.
19636:+- Fresh verification run by critic: py_compile passed; targeted suite passed `140 passed, 2 skipped, 4 warnings`; R3 drift check for M4 returned `GREEN=10 YELLOW=0 RED=0`; topology navigation matched M4 profile.
19639:+Nonblocking risks from critic:
19640:+- `exit_lifecycle` may raise rather than gracefully return if a retry tries to re-cancel a command already in `REVIEW_REQUIRED`; this remains fail-closed, not fail-open.
19641:+- Live venue cancel/submit remains unexercised by design; tests use fakes/monkeypatches and live activation stays operator-gated.
19673:+- R3 drift check evidence remains `GREEN=10 YELLOW=0 RED=0`.
19681:+- Live venue submission, CLOB cutover, M5 reconciliation unblock in production, R1 redeem settlement, credentialed WS activation, and production DB mutation remain unauthorized.
19716:+- Closed grammar remains in `src/execution/command_bus.py:44-90`; transitions map `CANCEL_FAILED`/`CANCEL_REPLACE_BLOCKED` to `REVIEW_REQUIRED` in `src/state/venue_command_repo.py:120-123`.
19719:+- State/execution layering: DB schema owns DDL without importing execution at `src/state/db.py:1080-1090`; command repo releases mutex only after terminal command transition at `src/state/venue_command_repo.py:759-765`.
19741:+  - drift check: `GREEN=10 YELLOW=0 RED=0`
19743:+- `drift_reports/2026-04-27.md` confirms M4 GREEN: `10 GREEN · 0 YELLOW · 0 RED`.
19750:+- Live venue cancel/submit was not exercised; live activation remains operator-gated and outside M4 closeout.
19795:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M5: GREEN=10 YELLOW=0 RED=0
19822:+because this `M5_post_close_2026-04-27.md` artifact did not yet exist and live
19832:+Pasteur verified the M5 post-close artifact, code/evidence consistency, broader M5 gate (`133 passed, 6 skipped`), and M5 drift (`GREEN=10 YELLOW=0 RED=0`). No technical blockers remained for the verifier gate.
19905:+  - M5 drift: `GREEN=10 YELLOW=0 RED=0`
19910:+- Live venue enumeration was not exercised against production SDK/API; tests use fakes and live activation remains operator-gated.
19917:+- Live venue submission/cancel/redeem, CLOB cutover, R1 redeem settlement, credentialed WS activation, and production DB mutation remain unauthorized.
19944:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase R1: GREEN=7 YELLOW=0 RED=0
19961:+- `settlement_commands.py` defines the durable command ledger states, schema, event journal, payload hashes, active-command de-dupe, and `REDEEM_TX_HASHED` receipt reconciliation path.
19963:+- `USDC_E` is distinct from pUSD and routes to `REDEEM_REVIEW_REQUIRED`; payout assets are constrained to `pUSD`, `USDC`, and `USDC_E`.
19966:+- `src/state/db.py` mirrors the durable settlement command/event schema.
19970:+Nonblocking cautions: live redeem submission remains unauthorized, and future controlled callers of `submit_redeem()` must preserve explicit Q-FX/operator gates.
19983:+- Fresh read-only checks matched recorded evidence: py_compile OK; focused R1 tests 7 passed; broader R1 gate 151 passed with known warnings; R1 drift GREEN=7 YELLOW=0 RED=0.
19984:+- Code evidence aligns with R1 behavior: durable redeem states including `REDEEM_TX_HASHED`, Q-FX-1 fail-closed gating, legacy `USDC_E` review classification, and harvester intent recording without direct live redeem side effects.
19985:+- No live venue submission/cancel/redeem authorization, production DB mutation authorization, or CLOB cutover authorization was granted.
19992:+inside its packet boundaries. It does not authorize live venue submit/cancel/redeem,
19993:+production DB mutation, credentialed live activation, or CLOB cutover.
20011:+- Durable command state machine exists in `src/execution/settlement_commands.py`; schema constrains settlement states and payout assets, mirrored in `src/state/db.py`.
20014:+- `REDEEM_TX_HASHED` is implemented as the crash-recovery anchor; chain receipt reconciliation moves tx-hashed commands to confirmed or failed.
20015:+- No runtime live redeem path was introduced: runtime harvester code creates command intents via `request_redeem()`, and `submit_redeem()` is not invoked by runtime code.
20018:+- Legacy `USDC_E` remains distinct and routes to `REDEEM_REVIEW_REQUIRED`, not pUSD accounting.
20019:+- R1 routing/profile evidence points to `r3 settlement redeem command ledger implementation`, not heartbeat.
20031:+- `settlement_commands` and `settlement_command_events` durable tables exist, with R1 settlement states documented in code.
20037:+- Digest/profile routing is covered by `tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat`.
20043:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat tests/test_collateral_ledger.py::test_polymarket_client_redeem_fails_closed_before_adapter_when_q_fx_open tests/test_collateral_ledger.py::test_polymarket_client_redeem_defers_to_r1_without_sdk_side_effect tests/test_collateral_ledger.py::test_v2_adapter_redeem_deferred_without_sdk_contact: 10 passed, 2 warnings
20050:+python3 -m py_compile src/execution/settlement_commands.py src/execution/harvester.py src/state/db.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py: PASS
20051:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat: 7 passed
20052:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_collateral_ledger.py tests/test_v2_adapter.py tests/test_exchange_reconcile.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat: 76 passed, 4 known deprecation warnings
20053:+pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat tests/test_collateral_ledger.py tests/test_v2_adapter.py tests/test_exchange_reconcile.py tests/test_venue_command_repo.py tests/test_exit_safety.py tests/test_user_channel_ingest.py: 151 passed, 22 known deprecation warnings
20054:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase R1: GREEN=7 YELLOW=0 RED=0
20055:+python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 settlement redeem command ledger implementation
20060:+- Live redeem side effects were not exercised; live venue submission/cancel/redeem remains unauthorized.
20061:+- Production DB mutation and CLOB cutover remain unauthorized.
20065:+- `tests/test_harvester_dr33_live_enablement.py` still carries a pre-existing stale `physical_quantity` expectation unrelated to R1 and is not R1 evidence.
20071:+- Live venue submit/cancel/redeem, production DB mutation, credentialed live activation, and CLOB cutover remain outside authorization.
20080:+Phase: T1 — FakePolymarketVenue (paper/live parity)
20100:+python3 -m py_compile tests/fakes/polymarket_v2.py tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/conftest.py src/venue/polymarket_v2_adapter.py: PASS
20101:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 88 passed
20102:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_exchange_reconcile.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 155 passed, 6 skipped, 18 known deprecation warnings
20103:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase T1: GREEN=11 YELLOW=0 RED=0
20121:+- `FakePolymarketVenue` implements the shared `PolymarketV2AdapterProtocol`, reuses the live envelope creation path, and produces the same `SubmitResult` / `VenueSubmissionEnvelope` path as the adapter surface.
20123:+- Paper/live journal shape parity is asserted through `venue_command_repo` insert/append/list paths for fake + mock-live scenarios.
20124:+- Boundaries hold: fake redeem defers to R1, tests use in-memory SQLite/tmp evidence/mock clients only, and no live submit/cancel/redeem, production DB mutation, CLOB cutover, or lifecycle grammar change was observed.
20132:+Peirce verified py_compile, focused T1 tests (`88 passed`), and T1 drift (`GREEN=11 YELLOW=0 RED=0`), but failed the gate because a closeout run in that verifier pass reported blocking `map_maintenance_companion_missing` issues. The leader reran map-maintenance and closeout with the receipt/full T1 changed-file set including this post-close artifact; both returned ok with `blocking_issues=[]`. A verifier re-run is required before any downstream unfreeze.
20145:+- No live venue, credentialed, production DB, or CLOB cutover authorization was exercised.
20149:+Decision: A1 may be marked ready for phase entry after this T1 post-close PASS. A2 and G1 remain blocked by their own dependencies (A1/A2 and live deploy gates) and no live venue submit/cancel/redeem, production DB mutation, credentialed activation, or CLOB cutover is authorized.
20158:+Phase: T1 — FakePolymarketVenue paper/live parity
20174:+- Added regression `tests/integration/test_p0_live_money_safety.py::test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary`.
20184:+- Focused remediation tests passed: restart + paper/live schema parity `2 passed`.
20187:+- Drift check: `GREEN=11 YELLOW=0 RED=0`.
20189:+- Protocol parity, no-live-I/O/prod-DB/cutover boundaries, schema parity, acceptance-name existence, and topology/receipt/status consistency were reviewed and satisfied.
20197:+- `python3 -m py_compile tests/fakes/polymarket_v2.py tests/integration/test_p0_live_money_safety.py`: PASS.
20198:+- Restart + paper/live schema parity focused tests: `2 passed`.
20199:+- T1 drift: `GREEN=11 YELLOW=0 RED=0 STATUS: GREEN`.
20203:+- No live venue/prod DB/CLOB cutover authorization was exercised or granted.
20208:+python3 -m py_compile tests/fakes/polymarket_v2.py tests/integration/test_p0_live_money_safety.py: PASS
20209:+pytest -q -p no:cacheprovider tests/integration/test_p0_live_money_safety.py::test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary tests/integration/test_p0_live_money_safety.py::test_paper_and_live_produce_identical_journal_event_shapes: 2 passed
20210:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 88 passed
20211:+pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_exchange_reconcile.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 155 passed, 6 skipped, 18 known deprecation warnings
20212:+python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase T1: GREEN=11 YELLOW=0 RED=0
20219:+- T1 fake venue is test-only and does not authorize live venue submit/cancel/redeem, production DB mutation, credentialed activation, or CLOB cutover.
20247:+    wiring, fail-closed compatibility paths, no alternate live submit bypass in
20248:+    `src/`, and no U2 raw-provenance/live-cutover implementation.
20267:+- `pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py tests/test_collateral_ledger.py`
20272:+  -> `GREEN=14 YELLOW=0 RED=0`.
20279:+by the leader, but live cutover remains blocked by Q1/cutover and downstream
20289:+Phase: U2 `Raw provenance schema`
20301:+- Non-blocking risks retained for downstream owners:
20302:+  - Legacy DBs can retain nullable `venue_commands.envelope_id`; new writes are Python-enforced.
20303:+  - Idempotency races may leave unreferenced pre-submit envelopes, but they remain append-only provenance and do not create confirmed exposure.
20310:+  - `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py tests/test_executable_market_snapshot_v2.py tests/test_command_bus_types.py tests/test_command_recovery.py tests/test_venue_command_repo.py tests/test_executor_command_split.py tests/test_neg_risk_passthrough.py` -> `155 passed`.
20312:+  - `src/state/db.py` U2 append-only provenance tables/triggers and `venue_commands.envelope_id`.
20313:+  - `src/state/venue_command_repo.py` snapshot+envelope gating, token/side/price/size mismatch rejection, command-to-provenance mirroring, append-only facts/lots, and CONFIRMED-only calibration reads.
20315:+  - `tests/test_provenance_5_projections.py` and `tests/test_executor_command_split.py` for schema, mismatch rejection, confirmed-only training, provenance mirroring, and entry/exit ordering.
20322:+- `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py` -> `13 passed`.
20335:+Phase: U2 `Raw provenance schema`
20341:+  - Confirmed U2 acceptance evidence, snapshot+envelope gates, executor pre-submit envelope persistence, append-only/provenance fields, CONFIRMED-only calibration reads, topology closeout, and planning-lock evidence.
20346:+  - `tests/test_provenance_5_projections.py::test_command_insert_rejects_envelope_shape_mismatch` locks side/price/size mismatch rejection.
20356:+- `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py` -> `13 passed`.
20359:+- `pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py` -> `47 passed, 2 skipped`.
20360:+- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_u2_raw_provenance_routes_to_u2_profile_not_heartbeat tests/test_neg_risk_passthrough.py tests/test_collateral_ledger.py` -> `36 passed, 4 warnings`.
20392:+    "U": "#a3d9ff",  # snapshot/provenance
20394:+    "R": "#d9a3ff",  # settlement
20397:+    "A": "#ffa3a3",  # strategy/risk
20404:+RISK_RE = re.compile(r"^risk:\s*(\S+)")
20417:+        "risk": None,
20468:+            card["risk"] = m.group(1)
20506:+        risk = c.get("risk") or "?"
20510:+        label = f"{cid}<br/>{risk}/{h_label}/{gate}"
20581:+    out.append("## Per-card risk + gate + dependencies")
20588:+        risk = c.get("risk") or "?"
20594:+        out.append(f"| {cid} | {phase} | {risk} | {h_str} | {gate} | {deps} | {title} |")
20641:+    1 — RED (SEMANTIC_MISMATCH or antibody fail; blocks merges)
20663:+RELEVANT_KEYS = ("file_line", "deliverables", "acceptance_tests", "extended_modules")
20771:+        if "operator_decisions" in path or "/evidence/" in path or "/q_fx_" in path or "/tigge_" in path or "/cutover_runbook" in path or "/q1_zeus_egress" in path or "/q_hb_" in path or "/q_new_1_" in path or "/calibration_retrain_decision" in path:
20848:+        "| Phase | GREEN | YELLOW | RED | SKIPPED |",
20865:+        f"**Totals**: {total_green} GREEN · {total_yellow} YELLOW · {total_red} RED"
20870:+        lines.append("## RED findings (blocking)")
20918:+        f"RED={total_red}"
20922:+        print("STATUS: RED — blocks new phase merges. Fix RED findings first.")
20939:+title: StrategyBenchmarkSuite — alpha + execution metrics + replay/paper/live promotion gate
20942:+  goes live without passing replay → paper → small-cap-live-shadow tests.
20947:+  - AGENTS.md "Strategy families" — settlement-capture / shoulder-bin / center-bin / opening-inertia
20951:+deliverables:
20964:+            time_to_resolution_risk: float  # capital-lock duration weighted
20969:+            calibration_error_vs_market_implied: float
20974:+            def evaluate_live_shadow(self, strategy_key: str, capital_cap_micro: int, duration_hours: int) -> StrategyMetrics: ...
20990:+        from .neg_risk_basket import NegRiskBasket  # NEW
20998:+          environment TEXT NOT NULL CHECK (environment IN ('replay','paper','shadow','live')),
21008:+  - tests/test_strategy_benchmark.py::test_benchmark_metrics_computed_for_live_shadow
21010:+  - tests/test_strategy_benchmark.py::test_pnl_split_into_alpha_spread_fees_slippage_failed_settlement_capital_lock
21011:+  - tests/test_strategy_benchmark.py::test_backtest_to_paper_to_live_semantic_drift_report_empty_or_explicitly_waived
21012:+  - tests/test_strategy_benchmark.py::test_calibration_error_vs_market_implied_p_computed
21016:+risk: high
21022:+  - INV-NEW-Q: "No strategy promoted to live without StrategyBenchmarkSuite.promotion_decision() returning PROMOTE based on replay + paper + shadow runs."
21026:+  - Coordinate with F2: calibration retrains feed metrics; metric drift signals when retrain is needed.
21037:+  Convert signals into bounded, capital-efficient live positions. Caps
21044:+  - AGENTS.md risk-level grammar (GREEN/YELLOW/ORANGE/RED + DATA_DEGRADED)
21045:+  - INV-NEW-I (R3) — risk allocator separates OPTIMISTIC vs CONFIRMED exposure
21048:+deliverables:
21050:+    - path: src/risk_allocator/governor.py
21084:+  - tests/test_risk_allocator.py::test_per_market_cap_enforced
21085:+  - tests/test_risk_allocator.py::test_correlated_market_cap_via_multiple_outcome_tokens_enforced
21086:+  - tests/test_risk_allocator.py::test_unknown_side_effect_blocks_new_risk_in_same_market
21087:+  - tests/test_risk_allocator.py::test_heartbeat_degraded_switches_to_FOK_FAK_only
21088:+  - tests/test_risk_allocator.py::test_heartbeat_lost_switches_to_no_trade
21089:+  - tests/test_risk_allocator.py::test_drawdown_governor_blocks_new_risk_at_threshold
21090:+  - tests/test_risk_allocator.py::test_reduce_only_mode_when_risk_state_degraded
21091:+  - tests/test_risk_allocator.py::test_manual_operator_trade_appears_as_external_position_drift_reduces_capacity
21092:+  - tests/test_risk_allocator.py::test_kill_switch_blocks_all_submits
21093:+  - tests/test_risk_allocator.py::test_optimistic_vs_confirmed_split_in_capacity_check
21098:+    artifact: config/risk_caps.yaml (NEW)
21102:+risk: high
21111:+  - This card converts strategy benchmarks (A1) into bounded live deployment.
21123:+  Wire the ingestion architecture so any new forecast source (TIGGE, GFS,
21125:+  changes in calibration / signal / strategy. Existing scaffolding in
21129:+  - 'User directive 2026-04-26: "数据回输和训练（例如tigge数据）的时候需要我决定，但是搭线需要搭好" (data ingest + training is operator-decision; wiring must be ready)'
21131:+  - evidence/multi_review/trading_correctness_report.md MONEY_PATH_GAPS (forecast/calibration/learning untouched in R2)
21138:+    - src/calibration/platt.py (existing)
21139:+    - src/contracts/ensemble_snapshot_provenance.py (existing)
21140:+deliverables:
21146:+        source to the live ensemble.
21156:+            env_flag_name: Optional[str]  # e.g., "ZEUS_TIGGE_INGEST_ENABLED"
21170:+                ingest_class=TIGGEIngest,  # NEW — F3 mints stub
21171:+                requires_api_key=True,  # ECMWF TIGGE archive requires registration
21174:+                env_flag_name="ZEUS_TIGGE_INGEST_ENABLED",
21208:+        Resolve live ensemble model identity through forecast_source_registry
21214:+        P_raw engine; registry/source gating lives at data/evaluator fetch
21224:+  - tests/test_forecast_source_registry.py::test_ensemble_fetch_result_carries_registry_provenance
21229:+risk: medium
21236:+  - INV-NEW-N: "Every forecast row in `forecasts` table carries `source_id` + `raw_payload_hash` + `authority_tier` — same provenance discipline as observation_atoms."
21238:+  - This card builds the WIRING. F2 adds calibration loop control. F3 adds the TIGGE-specific ingest stub.
21240:+  - TIGGE is tier='experimental' AND operator-decision-gated. Code path lands; ingest is dormant until operator flips the flag.
21252:+  Plumb the calibration retrain trigger so operator can flip a switch to
21258:+  - 'User directive 2026-04-26: "训练（例如tigge数据）的时候需要我决定" (training requires operator decision)'
21260:+  - evidence/multi_review/trading_correctness_report.md PROBABILITY_CHAIN_RISKS (calibration drift antibody)
21262:+  - src/calibration/platt.py (existing module)
21266:+deliverables:
21268:+    - path: src/calibration/retrain_trigger.py
21272:+        calibration_params table. Frozen-replay test asserts no silent
21289:+        def test_calibration_retrain_does_not_break_p_posterior_bit_identicality() -> None: ...
21290:+        def test_calibration_retrain_disabled_by_default() -> None: ...
21291:+        def test_calibration_retrain_armed_requires_operator_token() -> None: ...
21292:+        def test_drift_detection_blocks_promotion_to_live() -> None: ...
21295:+      content: 3 captured snapshots (e.g., 2026-04-15 weather-Day0, 2026-04-20 settlement-day, 2026-04-25 mid-cycle-degraded)
21298:+        CREATE TABLE IF NOT EXISTS calibration_params_versions (
21307:+          promoted_at TEXT,  -- when this version became "live"
21312:+    - file: src/calibration/platt.py
21316:+        new params via calibration_params_versions table.
21319:+        Read latest "live" calibration_params_versions row at startup;
21322:+  - tests/test_calibration_retrain.py::test_retrain_disabled_by_default
21323:+  - tests/test_calibration_retrain.py::test_arm_requires_operator_token_AND_evidence_path
21325:+    tests/test_calibration_retrain.py::test_arm_then_trigger_consumes_confirmed_trades_only
21327:+  - tests/test_calibration_retrain.py::test_frozen_replay_failure_blocks_promotion
21328:+  - tests/test_calibration_retrain.py::test_frozen_replay_pass_promotes_new_version
21329:+  - tests/test_calibration_retrain.py::test_promotion_atomic_no_mid_swap_visible
21330:+  - tests/test_calibration_retrain.py::test_retired_versions_retained_for_audit
21331:+  - tests/integration/test_frozen_replay_harness.py::test_p_posterior_bit_identical_pre_post_calibration_retrain
21334:+  - gate_id: calibration-retrain
21335:+    blocking: yes (new params CANNOT promote to live without operator arm + frozen-replay PASS)
21336:+    artifact: docs/operations/.../evidence/calibration_retrain_decision_<date>.md
21340:+risk: high
21347:+  - INV-NEW-P: "Calibration param promotion to live REQUIRES frozen-replay PASS. Drift = operator review required, not auto-promote."
21350:+  - 'Coordinate with F1: when operator activates TIGGE source, the new corpus includes TIGGE forecasts; calibration retrain may produce different α-weights.'
21351:+  - The 3 frozen-portfolio fixtures are CAPTURED NOW (pre-Wave-A) so they survive any subsequent schema migrations.
21360:+title: TIGGE ingest stub — registered, gated, dormant by default
21362:+  Land the TIGGE-specific ingest class as a stub registered in F1's
21363:+  forecast_source_registry. Code path is built; external TIGGE HTTP/GRIB fetch
21365:+  route a pre-approved local JSON TIGGE payload through the ensemble pipeline.
21367:+  - User directive 2026-04-26: TIGGE-class data ingestion is operator-decision; wiring must be ready
21369:+  - Existing ECMWF open data ingest at src/data/ecmwf_open_data.py (template for TIGGE)
21370:+  - TIGGE archive specs (operator should capture in reference_excerpts/tigge_archive_access_<date>.md)
21373:+deliverables:
21376:+      summary: TIGGE ingest adapter. Implements ForecastIngestProtocol without external HTTP/GRIB I/O.
21378:+        class TIGGEIngest:
21396:+                    raise TIGGEIngestNotEnabled(
21397:+                        "TIGGE ingest is operator-gated. "
21400:+                        "AND env var ZEUS_TIGGE_INGEST_ENABLED=1"
21404:+                #   2. read local JSON from payload_path / ZEUS_TIGGE_PAYLOAD_PATH /
21406:+                # Missing local payload raises TIGGEIngestFetchNotConfigured.
21415:+            env_flag = os.environ.get("ZEUS_TIGGE_INGEST_ENABLED") == "1"
21421:+        Register TIGGEIngest in the SOURCES dict with tier='experimental',
21422:+        requires_operator_decision=True, env_flag_name='ZEUS_TIGGE_INGEST_ENABLED'.
21426:+        adapter instead of sending TIGGE to Open-Meteo; preserve existing cache
21427:+        shape and provenance fields.
21430:+        Document TIGGE archive registration steps + API key location +
21436:+    | NC-NEW-J — TIGGEIngest.fetch() raises TIGGEIngestNotEnabled with exact error message including operator artifact path + env flag name
21440:+    | open gate without an injected/local payload raises TIGGEIngestFetchNotConfigured
21444:+    | ZEUS_TIGGE_PAYLOAD_PATH can provide the local JSON payload path
21446:+    | model='tigge' routes through TIGGEIngest and does not call Open-Meteo HTTP
21449:+  - tests/test_tigge_ingest.py::test_ensemble_signal_does_not_consume_TIGGE_when_gated
21450:+    | F1 active_sources() excludes TIGGE when gate closed; ensemble_signal output is bit-identical with/without TIGGE class loaded
21452:+  - gate_id: TIGGE-ingest-go-live
21453:+    blocking: yes (TIGGE fetch path; rest of F1+F2 unblocked without)
21456:+      env var ZEUS_TIGGE_INGEST_ENABLED=1 plus operator-approved local JSON
21457:+      payload path via artifact `payload_path:` or ZEUS_TIGGE_PAYLOAD_PATH
21460:+risk: medium
21466:+  - NC-NEW-J: TIGGEIngest.fetch() raises TIGGEIngestNotEnabled when operator gate is closed.
21470:+    When operator decides to enable TIGGE local ingest: (1) write an evidence
21473:+    ZEUS_TIGGE_PAYLOAD_PATH, (2) export ZEUS_TIGGE_INGEST_ENABLED=1 in daemon
21474:+    environment, (3) restart daemon. F1 active_sources() picks up TIGGE on
21475:+    restart and ensemble_client routes model='tigge' through TIGGEIngest.
21476:+  - External TIGGE archive HTTP/GRIB fetch remains intentionally unimplemented; adding it requires a later operator/data-source packet with archive access, product tags, lead grid, and parser evidence.
21477:+  - F2 calibration retrain may consume TIGGE-augmented corpus once operator supplies payloads and arms retraining; frozen-replay harness asserts no silent drift.
21486:+title: Live readiness gates — 17 CI gates + staged live-smoke verification
21488:+  Zeus is not live-money-ready until ALL 17 gates pass in CI AND at least
21489:+  one staged live-smoke environment. Each gate is a runnable test or
21496:+deliverables:
21498:+    - path: scripts/live_readiness_check.py
21501:+    - tests/test_live_readiness_gates.py — orchestrates the 17 gate tests
21505:+    | tests/test_live_readiness_gates.py::test_only_v2_sdk_in_live_code
21506:+    | grep `from py_clob_client ` in src/ live-mode paths returns 0
21508:+    | tests/test_live_readiness_gates.py::test_correct_pre_cutover_or_production_host_verified_from_zeus_daemon
21511:+    | tests/test_live_readiness_gates.py::test_heartbeat_supervised_failure_quarantines_orders
21514:+    | tests/test_live_readiness_gates.py::test_pusd_balance_allowance_wrap_unwrap_tested
21517:+    | tests/test_live_readiness_gates.py::test_exits_use_token_balances_reservations
21520:+    | tests/test_live_readiness_gates.py::test_every_order_has_fresh_executable_snapshot
21523:+    | tests/test_live_readiness_gates.py::test_every_side_effect_has_raw_payload_envelope
21524:+    | U2 antibody — full provenance chain reconstructable
21526:+    | tests/test_live_readiness_gates.py::test_GTC_GTD_FOK_FAK_explicit
21529:+    | tests/test_live_readiness_gates.py::test_unknown_blocks_duplicate_submit
21532:+    | tests/test_live_readiness_gates.py::test_MATCHED_cannot_final_close_or_final_train
21535:+    | tests/test_live_readiness_gates.py::test_user_trade_statuses_persisted_and_reconciled
21538:+    | tests/test_live_readiness_gates.py::test_cancel_unknown_failed_blocks_replacement
21541:+    | tests/test_live_readiness_gates.py::test_open_order_wipe_simulation_reconciles
21544:+    | tests/test_live_readiness_gates.py::test_process_crash_at_every_order_lifecycle_point_recovers_deterministically
21546:+  - 15. Paper/live parity gate
21547:+    | tests/test_live_readiness_gates.py::test_fake_venue_produces_same_event_schema_as_live_adapter
21550:+    | tests/test_live_readiness_gates.py::test_alpha_candidates_pass_replay_paper_shadow
21553:+    | tests/test_live_readiness_gates.py::test_no_agent_facing_docs_expose_direct_SDK_calls_or_ambiguous_status
21556:+  - gate_id: live-money-deploy-go
21558:+    artifact: scripts/live_readiness_check.py output showing 17/17 PASS + staged-live-smoke evidence
21562:+risk: medium
21568:+  - INV-NEW-S: "LIVE deployment requires 17/17 G1 gate PASS + ≥1 staged-live-smoke environment passing the same test set."
21570:+  - G1 is the last gate before live-money cutover. All R3 phases contribute their antibodies; G1 is the orchestration surface.
21571:+  - Operator gate is the FINAL live-money flip; engineering can ship the suite green but operator pulls the trigger.
21583:+  cycle_runner as the SOLE RED→durable-cmd authority surface (NC-NEW-D
21584:+  function-scope). Trade and order grammars live in U2; this card owns
21588:+  - R2 mid-01.yaml (RED→durable-cmd; cycle_runner-as-proxy LOCKED in R2 multi-review)
21597:+deliverables:
21604:+          REVIEW_REQUIRED
21610:+        a CommandState; lives in venue_order_facts per U2).
21614:+        / fill states live in venue_order_facts + venue_trade_facts (U2).
21625:+    - file: src/riskguard/riskguard.py
21627:+        UNCHANGED — riskguard remains observability-only (sets
21628:+        force_exit_review flag in risk_state). cycle_runner-as-proxy reads
21629:+        the flag; riskguard does NOT directly call insert_command.
21636:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emits_cancel_command_within_same_cycle
21637:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emit_grammar_bound_to_cancel_or_derisk_only
21638:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emit_satisfies_inv_30_persist_before_sdk
21639:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emit_satisfies_nc_19_idempotency_lookup
21640:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emit_passes_through_command_recovery
21641:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emit_sole_caller_is_cycle_runner_force_exit_block
21643:+  - tests/test_riskguard_red_durable_cmd.py::test_riskguard_does_NOT_call_insert_command_directly
21651:+risk: high
21658:+  - NC-NEW-E preserved: RESTING is NOT a CommandState member; lives in venue_order_facts.state.
21662:+  - Trade/order/lot/settlement grammars live in U2 / R1 / their own slices.
21682:+deliverables:
21716:+risk: high
21746:+deliverables:
21783:+    | when WS active, append_order_fact / append_trade_fact are populated from WS messages with source='WS_USER'; identical schema to REST path (paper/live parity by construction)
21784:+  - tests/test_p0_live_money_safety.py::test_resubscribe_recovery
21793:+risk: high
21802:+  - axis-31 paper/live parity closure depends on M3 + T1 producing identical event schema.
21821:+deliverables:
21863:+  - tests/test_exit_safety.py::test_cancel_not_canceled_dict_creates_CANCEL_FAILED_or_REVIEW_REQUIRED
21875:+risk: high
21881:+  - INV-NEW-I: "Replacement sell BLOCKED until prior sell reaches CANCEL_CONFIRMED, FILLED+CONFIRMED, EXPIRED, or proven absent by reconciliation."
21905:+deliverables:
21969:+risk: high
21989:+  Make settlement side effects (winning-share redemption) as auditable as
21993:+  - Apr26 review F-011 (raw payload provenance — extends to settlement)
21997:+deliverables:
21999:+    - path: src/execution/settlement_commands.py
22003:+            REDEEM_INTENT_CREATED = "REDEEM_INTENT_CREATED"
22004:+            REDEEM_SUBMITTED = "REDEEM_SUBMITTED"
22005:+            REDEEM_TX_HASHED = "REDEEM_TX_HASHED"
22006:+            REDEEM_CONFIRMED = "REDEEM_CONFIRMED"
22007:+            REDEEM_FAILED = "REDEEM_FAILED"
22008:+            REDEEM_RETRYING = "REDEEM_RETRYING"
22009:+            REDEEM_REVIEW_REQUIRED = "REDEEM_REVIEW_REQUIRED"
22016:+        CREATE TABLE IF NOT EXISTS settlement_commands (
22019:+            'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
22020:+            'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED'
22035:+        CREATE TABLE IF NOT EXISTS settlement_command_events (
22037:+          command_id TEXT NOT NULL REFERENCES settlement_commands(command_id),
22046:+        Replace direct adapter.redeem() calls with settlement_commands.request_redeem()
22047:+        + settlement_commands.submit_redeem(). Harvester reconciles pending
22050:+  - tests/test_settlement_commands.py::test_redeem_lifecycle_atomic_states
22052:+  - tests/test_settlement_commands.py::test_redeem_crash_after_tx_hash_recovers_by_chain_receipt
22053:+  - tests/test_settlement_commands.py::test_redeem_failure_does_not_mark_position_settled
22054:+  - tests/test_settlement_commands.py::test_v1_legacy_unresolved_classified_separately_from_v2_pusd_payout
22055:+  - tests/test_settlement_commands.py::test_redeem_blocked_until_q_fx_1_classified
22057:+  - tests/test_settlement_commands.py::test_payout_asset_constraint_enforced
22065:+risk: medium
22071:+  - INV-NEW-L: "Settlement transitions are durable + crash-recoverable — REDEEM_TX_HASHED state is the recovery anchor (chain truth-chain reconcile reads tx_hash and follows receipt)."
22073:+  - K6 settlement work that was R2-deferred lands here.
22074:+  - Coordinate with Z4: redemption goes through R1 settlement_commands, NOT directly through Z4 CollateralLedger.
22083:+title: Paper/live parity FakePolymarketVenue — same adapter contract, simulated failure modes
22085:+  Force paper to fail the same way live fails. FakePolymarketVenue
22087:+  produces identical event schema as live adapter. Replaces R2 mid-08.
22090:+  - Apr26 review F-012 (tests prove happy paths, not live-money safety) + axis-31 (paper/live parity) + axis-49 (failure-mode test suite)
22094:+deliverables:
22099:+        Internal state: orderbook engine, trade generator, settlement clock,
22130:+  - tests/integration/test_p0_live_money_safety.py::test_duplicate_submit_idempotency
22131:+  - tests/integration/test_p0_live_money_safety.py::test_rapid_sequential_partial_fills
22132:+  - tests/integration/test_p0_live_money_safety.py::test_red_cancel_all_behavioral
22133:+  - tests/integration/test_p0_live_money_safety.py::test_market_close_while_resting
22134:+  - tests/integration/test_p0_live_money_safety.py::test_resubscribe_recovery
22135:+  - tests/integration/test_p0_live_money_safety.py::test_heartbeat_miss_auto_cancel_and_reconcile
22136:+  - tests/integration/test_p0_live_money_safety.py::test_cutover_wipe_simulation_reconciles
22137:+  - tests/integration/test_p0_live_money_safety.py::test_pusd_insufficient_blocks_buy
22138:+  - tests/integration/test_p0_live_money_safety.py::test_token_insufficient_blocks_sell
22139:+  - tests/integration/test_p0_live_money_safety.py::test_MATCHED_then_FAILED_chain_rolls_back_optimistic_exposure
22140:+  - tests/integration/test_p0_live_money_safety.py::test_paper_and_live_produce_identical_journal_event_shapes
22141:+    | run identical scenario against fake venue + (mock-injected) live adapter; assert event row schemas match
22145:+risk: high
22151:+  - INV-NEW-M: "Paper-mode runs go through the SAME PolymarketV2Adapter Protocol; FakePolymarketVenue and live adapter produce schema-identical events. Backtests can replay fake venue facts to regenerate the same portfolio projections."
22153:+  - This card is the immune-system slice for live-money safety. Without it, all the V2/Z3/Z4/M-series antibodies are isolated assertions; T1 lets us replay full P0 scenarios.
22154:+  - axis-31 paper/live parity closure depends on FakePolymarketVenue mirroring the LIVE adapter EXACTLY.
22167:+  tick, min size, fee details, neg risk, and tradability flags match the
22177:+deliverables:
22207:+            neg_risk: bool
22259:+          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
22276:+      change: REQUIRE snapshot_id in command row; raise StaleMarketSnapshotError if snapshot is missing OR captured_at < now() - FRESHNESS_WINDOW OR token_id/tick/min_size/neg_risk mismatch intent
22294:+risk: medium
22314:+title: Raw provenance schema — 5 distinct projections (commands / order-facts / trade-facts / position-lots / settlement-commands)
22319:+  monotonic local sequence. Calibration training consumes CONFIRMED only
22322:+  - V2.1 §M1 (5 projections) + V2.1 §U2 (raw payload provenance)
22328:+deliverables:
22354:+          neg_risk INTEGER NOT NULL,
22374:+            'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED'
22430:+        CREATE TABLE IF NOT EXISTS provenance_envelope_events (
22432:+          subject_type TEXT NOT NULL CHECK (subject_type IN ('command','order','trade','lot','settlement','wrap_unwrap','heartbeat')),
22443:+        CREATE INDEX IF NOT EXISTS idx_envelope_events_subject ON provenance_envelope_events (subject_type, subject_id, observed_at);
22451:+  - tests/test_provenance_5_projections.py::test_command_insert_requires_envelope_and_snapshot
22452:+  - tests/test_provenance_5_projections.py::test_order_facts_state_grammar_includes_RESTING_HEARTBEAT_CANCEL_SUSPECTED
22453:+  - tests/test_provenance_5_projections.py::test_trade_facts_split_MATCHED_MINED_CONFIRMED_RETRYING_FAILED
22454:+  - tests/test_provenance_5_projections.py::test_position_lots_optimistic_vs_confirmed_split
22456:+    tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only
22457:+    NC-NEW-H — SELECT * FROM venue_trade_facts WHERE state IN ('MATCHED','MINED') for calibration raises ValueError
22459:+    tests/test_provenance_5_projections.py::test_optimistic_exposure_rolled_back_on_FAILED_trade
22462:+    tests/test_provenance_5_projections.py::test_full_provenance_chain_reconstructable
22464:+  - tests/test_provenance_5_projections.py::test_local_sequence_monotonic_per_subject
22465:+  - tests/test_provenance_5_projections.py::test_source_field_required_on_every_event
22466:+  - tests/test_provenance_5_projections.py::test_redeem_can_be_traced_to_tx_hash_and_chain_receipt
22470:+risk: high
22476:+  - NC-NEW-G: provenance pinned at VenueSubmissionEnvelope (already minted by Z2)
22477:+  - NC-NEW-H: matched-not-confirmed — calibration training paths filter venue_trade_facts WHERE state = 'CONFIRMED' only.
22478:+  - NC-NEW-I: optimistic-vs-confirmed-exposure — risk allocator separates capital deployed against OPTIMISTIC_EXPOSURE from CONFIRMED_EXPOSURE.
22495:+  Lock all R3 phase docs against stale R2 / dormant-tracker / V1-low-risk
22510:+deliverables:
22521:+    - path: docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
22523:+        Lists the Polymarket live-money invariants Zeus must uphold:
22524:+        - V2 SDK (py-clob-client-v2) is the only live placement path.
22527:+        - No live placement may proceed when CutoverGuard != LIVE_ENABLED.
22529:+        - MATCHED ≠ CONFIRMED at trade-fact level; calibration consumes CONFIRMED only.
22535:+  - tests/test_z0_plan_lock.py::test_no_v2_low_risk_drop_in_in_active_docs
22536:+    | grep -rE "V2 low.risk|low.risk drop.in" docs/operations/task_2026-04-26_polymarket_clob_v2_migration/ returns 0 hits
22537:+  - tests/test_z0_plan_lock.py::test_polymarket_live_money_contract_doc_exists
22538:+    | docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md exists with all 8 invariant bullets
22541:+  - tests/test_z0_plan_lock.py::test_no_live_path_imports_v1_sdk
22550:+risk: low
22557:+  - step2: write docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md from the deliverables.new_files content above
22571:+  decision: emit live-money contract packet-locally under the registered CLOB V2 migration packet and record protocol evolution proposal `r3/_protocol_evolution/z0_docs_architecture_path.md`.
22583:+  states block live placement during the transition. Z1 exposes a deferred
22593:+deliverables:
22622:+      function: execute_intent, execute_exit_order, _live_order
22632:+        A DB-backed cutover/exchange findings table is deferred to M5.
22636:+  - tests/test_cutover_guard.py::test_live_enabled_rejects_unsigned_operator_token
22640:+  - "tests/test_cutover_guard.py::test_live_enabled_allows_normal_v2_operation (gate-decision-only in Z1; full V2 adapter operation waits for Z2)"
22654:+risk: high
22660:+  - INV-NEW-A: "No live submit may proceed when CutoverGuard.current_state() != LIVE_ENABLED. CutoverPending exception raised in executor pre-flight."
22676:+  Isolate SDK volatility behind a single adapter module. All live
22693:+    - tests/test_neg_risk_passthrough.py (V1 antibody, mirror for V2)
22694:+deliverables:
22699:+      summary: The ONLY live Polymarket placement / cancel / query surface.
22720:+      summary: Frozen dataclass capturing all provenance for a single submission.
22739:+            neg_risk: bool
22755:+      change: add `py-clob-client-v2>=1.0.0`; remove `py-clob-client` from live-mode deps (keep test-only)
22764:+  - tests/test_v2_adapter.py::test_create_submission_envelope_captures_all_provenance_fields
22767:+    tests/test_v2_adapter.py::test_one_step_sdk_path_still_produces_envelope_with_provenance
22776:+    tests/test_v2_adapter.py::test_old_v1_sdk_import_fails_in_live_mode
22777:+    `from py_clob_client.client import ClobClient` in live-mode test path raises ImportError or DeprecationWarning
22788:+    tests/test_v2_adapter.py::test_neg_risk_passthrough_v2
22789:+    mirror of tests/test_neg_risk_passthrough.py (V1 baseline) for V2 surface
22800:+risk: high
22806:+  - NC-NEW-G: "Provenance pinned at VenueSubmissionEnvelope contract layer, NOT specific SDK call shape. Antibody: tests/test_v2_adapter.py::test_one_step_sdk_path_still_produces_envelope_with_provenance + test_two_step_sdk_path_produces_envelope_with_signed_order_hash. semgrep rule `zeus-v2-placement-via-adapter-only` bans direct ClobClient import outside src/venue/."
22810:+  - VenueSubmissionEnvelope is the CONTRACT for U2 raw provenance schema; U2 adds the matching DB columns.
22821:+title: HeartbeatSupervisor — MANDATORY for live resting orders
22823:+  Heartbeat is mandatory for any live resting-order strategy (per V2.1 §Z3).
22835:+deliverables:
22891:+  - tests/test_p0_live_money_safety.py::test_forced_16s_heartbeat_miss_in_fake_venue_auto_cancels_orders_and_reconciles
22899:+risk: high
22932:+deliverables:
23025:+      change: consult CollateralLedger; emit redemption commands via R1 settlement_commands ledger (NOT direct adapter call)
23052:+risk: high
23059:+  - INV-NEW-D: "Reserved tokens for an open sell command MUST be released atomically when the command transitions to CANCELED, FILLED, or EXPIRED — never abandoned, never double-counted."
23064:+  - Coordinate with R1 (settlement_commands): redemption goes through R1, not directly through Z4.
23087:+## Per-card risk + gate + dependencies
23091:+| A1 | A | high | 30 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1, T1, F1 | StrategyBenchmarkSuite — alpha + execution metrics + replay/paper/live promotion gate |
23095:+| F3 | F | medium | 6 | critic-opus | F1 | TIGGE ingest stub — registered, gated, dormant by default |
23096:+| G1 | G | medium | 14 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1, T1, F1, F2, F3, A1, A2 | Live readiness gates — 17 CI gates + staged live-smoke verification |
23103:+| T1 | T | high | 22 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1 | Paper/live parity FakePolymarketVenue — same adapter contract, simulated failure modes |
23105:+| U2 | U | high | 28 | critic-opus | Z0, Z1, Z2, Z3, Z4, U1 | Raw provenance schema — 5 distinct projections (commands / order-facts / trade-facts / pos |
23109:+| Z3 | Z | high | 12 | critic-opus | Z0, Z1, Z2 | HeartbeatSupervisor — MANDATORY for live resting orders |
23171:+You are NOT starting code yet. Your first deliverable is a brief
23183:+2. **Drift status** — drift_check GREEN/YELLOW/RED; if RED, what's blocking.
23323:+- IMPLEMENTATION → `executor` (default), opus for HIGH-risk
23328:+  HIGH-risk per `feedback_default_dispatch_reviewers_per_phase`)
23500:+If drift report has RED status on a load-bearing citation: STOP. Write
23524:+- antibody status: <count>/<total> live
23541:+  `deliverables.extended_modules` + `deliverables.new_modules`. Modifying
23549:+- For HIGH-risk phases: dispatch `deep-interview` skill BEFORE writing
23605:+5. Critic-opus dispatched (HIGH-risk phases) — APPROVED verdict.
23638:+5. ☐ Critic-opus review APPROVED (HIGH-risk phases)
23644:+11. ☐ No drift_check RED status
23648:+(Read the phase yaml's `risk:` and `notes:` sections. Each lists
23718:+RISK_RE = re.compile(r"^risk:\s*(\S+)")
23730:+        "risk": None,
23767:+            card["risk"] = m.group(1)
23811:+        risk = c.get("risk") or "?"
23815:+        label = f"{cid}<br/>{risk}/{h_label}/{gate}"
23850:+    out.append("## Per-card risk + gate + dependencies")
23856:+        risk = c.get("risk") or "?"
23862:+        out.append(f"| {cid} | {risk} | {h_str} | {gate} | {deps} | {title} |")
23993:+risk:
24000:+  - tests/test_neg_risk_passthrough.py:66-83 MUST stay green OR be migrated alongside down-03 antibody addition (V2-SDK still exposes get_neg_risk; V1 antibody pattern survives)
24045:+risk:
24054:+  - This slice produces NO code change; only evidence files + doc text amendments. Low-risk, low-effort, but BLOCKS down-01 (D1 swap can't ship without Q1-zeus-egress evidence).
24069:+  - docs/operations/task_2026-04-26_ultimate_plan/evidence/down/_context_boot_opponent.md §3.8 (get_fee_rate_bps + get_fee_exponent + get_tick_size + get_neg_risk dedicated methods)
24070:+  - tests/test_neg_risk_passthrough.py:66-83 (V1 antibody pattern model: skip-on-import-error + assert SDK method existence)
24076:+    - tests/test_neg_risk_passthrough.py:66-83  # V1 antibody pattern: skip-if-not-importable + assert get_neg_risk callable
24082:+    - "no test asserts presence of OrderArgsV1, OrderArgsV2, _resolve_version, _is_v2_order, post_heartbeat, get_fee_rate_bps, get_fee_exponent, get_tick_size, or get_neg_risk on V2 SDK"
24084:+    - tests/test_v2_sdk_contract.py (NEW)  # asserts unified-client shape; pattern from tests/test_neg_risk_passthrough.py:66-83
24089:+    - test_v2_sdk_imports_or_skip  # `from py_clob_client_v2.client import ClobClient`; pytest.skip if ImportError (matches V1 antibody pattern at test_neg_risk_passthrough.py:66-83)
24094:+    - test_v2_sdk_exposes_get_tick_size_and_get_neg_risk  # both callable with token_id; preserves Apr26 F-009 closure
24102:+risk:
24104:+  - "SDK SURFACE DRIFT: this antibody is the cheapest tripwire for ANY SDK rename in v1.0.x → v1.1+. False-positive risk LOW; false-negative risk HIGH if assertions are too narrow (e.g. assert exact signature). Use `hasattr` + `callable` pattern, NOT signature inspection."
24110:+  - existing V1 antibody at tests/test_neg_risk_passthrough.py:66-83 STAYS GREEN (V2 SDK exposes get_neg_risk per opponent-down §3.10 — Closing as VERIFIED-OK)
24115:+  - Pattern source: tests/test_neg_risk_passthrough.py:66-83 (existing V1 antibody). New file mirrors structure exactly.
24141:+    - 'manual review: Phase 0 critic verdict (Phase 0.F) confirms Q1 evidence file (evidence/q1_v2_host_probe_2026-04-26.txt) shows curl provenance from Zeus daemon machine'
24145:+risk: low
24150:+  - 'Why it matters: curl-from-anywhere returning 200 (which I confirmed in boot §2.2 via Anthropic egress) does NOT prove Zeus daemon''s KYC-tier-2 funder_address-bound account is permitted to interact. This was a real false-positive risk in the original plan.'
24193:+risk: low
24198:+  - 'Why this matters as a slice (not a casual edit): the impact_report is REGISTERED as the authority basis for plan.md (line 5 of plan.md). Six teammates and one critic-opus need a single coherent reference. Spot-edits across 6+ premises would diverge under L20 rot. This slice owns the full reconciliation.'
24261:+risk: medium
24315:+risk: medium
24323:+  runtime_gate: "ZEUS_PUSD_REDEMPTION_ENABLED env-flag (defaults False)"
24349:+      site: "src/riskguard/riskguard.py"
24350:+      grep: "grep -in 'USDC|pUSD|collateral|balance' src/riskguard/riskguard.py = 0 hits at HEAD 874e00c"
24351:+      verdict: "VERIFIED-CLEAN — riskguard uses $-unit/collateral-agnostic sizing; INVARIANT: riskguard MUST stay collateral-agnostic; antibody asserts `grep -l 'USDC|pUSD' src/riskguard/` empty"
24357:+      site: "src/calibration/*"
24358:+      grep: "grep -irn 'USDC|pUSD|currency' src/calibration/ = 0 hits at HEAD 874e00c"
24359:+      verdict: "VERIFIED-CLEAN — calibration training is currency-agnostic; antibody asserts grep-empty"
24383:+title: A1 K4 RED → durable-cmd (new authority surface)
24387:+  - Apr26 review §3 F-010 (RED authority drift)
24390:+    - src/riskguard/riskguard.py:826  # writes force_exit_review=1 to risk_state on RED
24391:+    - src/riskguard/riskguard.py:1077-1094  # get_force_exit_review() reader
24395:+    - "grep insert_command|venue_command_repo|append_event|command_bus across src/riskguard/*.py = 0 hits"
24400:+    rationale: "co-locating with the force_exit sweep keeps RED-handling semantics in ONE block; cycle_runner is canonical RED→action authority surface (not riskguard.py)"
24403:+  new_file_or_extend: tests/test_riskguard_red_durable_cmd.py (NEW)
24405:+    - test_red_emits_cancel_command_within_same_cycle  # RED detection → venue_commands row state=SUBMITTING in same process; no 2-cycle latency
24406:+    - test_red_emit_grammar_bound_to_cancel_or_derisk_only  # IntentKind ∈ {CANCEL, DERISK}; ENTRY/EXIT raise ValueError
24413:+  - mid-02  # A1.5 PAYLOAD_BIND must land first so CANCEL events carry signed_order_hash provenance; without mid-02, A1 emits CANCELs with no signed-payload binding (F-010 closure incomplete on payload side)
24414:+  - mid-03  # mid-03 STATE_GRAMMAR_AMEND adds CLOSED_MARKET_UNKNOWN event-type used by RED→cancel when discovery has invalidated the market mid-cycle
24416:+risk:
24418:+  - "OWNERSHIP LOCKED 2026-04-26 Mid R2L2 A3 (post multi-review correction): cycle_runner-as-proxy. riskguard.py REMAINS observability-only (sets force_exit_review flag in risk_state). cycle_runner reads flag + emits CommandBus CANCEL events. NC-NEW-D allowlist scope: cycle_runner._execute_force_exit_sweep ONLY (function-scope, not file-scope per multi-review architect §GAPS:1)."
24419:+  - "LATENCY: today RED→cancel is 2-cycle through pos.exit_reason. New direct path is same-cycle. Verify same-cycle in antibody."
24424:+  - test_red_emit_grammar_bound_to_cancel_or_derisk_only must FAIL when CANCEL_REQUESTED is replaced with SUBMIT_REQUESTED (negative-grammar test)
24426:+  - tests/test_riskguard_red_durable_cmd.py::test_red_emit_sole_caller_is_cycle_runner_force_exit_block MUST be runnable + green
24429:+  - K6 settlement (REDEEM_REQUESTED + REDEEMED) is OUT-OF-SCOPE — Down region (K6 → D2 pUSD redemption flow per routing yaml lines 319-337).
24481:+    - test_clob_token_ids_raw_provenance  # raw Gamma response stored verbatim; reconstructable forensically
24493:+risk:
24498:+  - "PROVENANCE-BY-CODE: clob_token_ids_raw stores raw Gamma response. Per Memory L20+L21 antibody discipline, this column gives forensic reconstruction; antibody test_clob_token_ids_raw_provenance LOCKS the contract."
24507:+  - mid-01 (A1) depends_on mid-02 because RED→cancel commands need signed_order_hash to be a no-op-extension on the SAME schema once mid-02 lands. CANCEL events emitted by mid-01 do NOT themselves need signed_order_hash (cancel is order-id-keyed, not order-payload-keyed), but the ROW they cancel MUST have signed_order_hash for forensic traceability of what was cancelled.
24508:+  - K6 settlement (REDEEM_*) explicitly OUT-OF-SCOPE per opponent-mid Strike-3 + judge §74 K=4 mid-active.
24538:+    - "re-audit IN_FLIGHT_STATES (currently SUBMITTING/UNKNOWN/REVIEW_REQUIRED/CANCEL_PENDING) — does CANCEL_FAILED join? Likely no (terminal); confirm in implementation"
24539:+    - "re-audit TERMINAL_STATES (FILLED/CANCELLED/EXPIRED/REJECTED) — CANCEL_FAILED is new terminal; add"
24541:+    - "re-audit command_recovery resolution: CANCEL_FAILED is non-recoverable; route to terminal vs REVIEW_REQUIRED based on venue_status payload"
24558:+  - mid-01  # A1 needs CLOSED_MARKET_UNKNOWN event-type to handle market-closed-mid-cycle case during RED→cancel
24562:+risk:
24563:+  - "CLOSED-LAW AMENDMENT scope risk: every site pattern-matching CommandState/CommandEventType (IN_FLIGHT_STATES, TERMINAL_STATES, _TRANSITIONS, recovery resolution, repo enum-grammar at insert_command:181-186, the test_command_state_strings_match_repo round-trip antibody) MUST be re-audited. Slice MUST own this re-audit explicitly."
24565:+  - "BACKWARD COMPAT: existing venue_commands rows in production DBs have current grammar. Migration must NOT mutate historical rows; new state members apply to NEW commands only."
24566:+  - "CANCEL_FAILED vs REVIEW_REQUIRED authority: when SDK rejects cancel, is the row CANCEL_FAILED (terminal — give up) or REVIEW_REQUIRED (operator-resolves)? Decide based on whether venue exposes a retry-allowed flag in error response."
24596:+    - src/execution/command_recovery.py:140-280  # resolution table — handles SUBMIT_ACKED/SUBMIT_REJECTED/EXPIRED but NO PARTIAL_FILL_OBSERVED emission path
24621:+risk:
24634:+  - "Out-of-scope for this slice: trade_fill table (separate persistence; lives in C1.5 relationship test, mid-06)"
24635:+  - "INV-32 materialize gate already handles PARTIAL state (line 70 _TRANSITIONS): pos.shares = filled_size; remaining_size open commands stay in PARTIAL until FILL_CONFIRMED or EXPIRED"
24655:+    - src/execution/command_recovery.py:80-92  # SUBMITTING + no_venue_order_id → REVIEW_REQUIRED operator-handoff (the exact F-006 case)
24684:+    - mid-05 MAY emit FILL_CONFIRMED + PARTIAL_FILL_OBSERVED + CANCEL_ACKED + EXPIRED for rows already in venue_commands
24685:+    - mid-05 MUST NOT call insert_command (NC-19 idempotency would be violated; only the executor or A1 RED-emitter inserts new rows)
24721:+risk:
24737:+  - K6 settlement (REDEEM_*) explicitly OUT-OF-SCOPE per opponent-mid Strike-3 + judge §74 K=4 mid-active.
24749:+  - Apr26 review §3 F-012 (tests prove happy paths only; live-money failure modes uncovered)
24761:+    - src/engine/cycle_runner.py:60-102  # RED→cancel boundary (relationship test target)
24763:+    - "opponent-mid §8.3 audit summary: 4 ALREADY EXIST (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION); 2 typing-only refinements; 11 genuinely NET_NEW reduce to K2+K3+K4 in mid-03 + K5 in mid-05"
24764:+    - "F-012 RESIDUAL: existing executor tests prove happy paths only with deterministic fakes; no cross-module relationship test for materialize_position↔trade_fill, RED↔cancel-emission, or command_recovery↔chain_reconciliation"
24765:+deliverables:
24784:+    - test_red_cancel_emission_creates_durable_command_row  # riskguard → command_bus: RED detection in cycle N → venue_commands row state=SUBMITTING (intent_kind=CANCEL) in same process; not 2-cycle latency (validates mid-01)
24793:+  - mid-01  # RED→cancel emission boundary tested
24799:+risk:
24816:+  - "Out-of-scope: settlement K6 relationship tests (REDEEM_REQUESTED → harvester → calibration_pairs) — Down region owns"
24829:+  - evidence/mid/converged_R1L1.md (mid-05 EXCH_RECON acknowledged "GET_TRADES SDK GAP" + "POSITION-DRIFT FALSE POSITIVES" risks)
24843:+    asserts: poll_interval_seconds × 2 < settlement_market_close_warning_window AND
24847:+    asserts: when ZEUS_WS_FILL_INGEST=1, ws_listener emits SUBMIT_ACKED / PARTIAL_FILL_OBSERVED / FILL_CONFIRMED / CANCEL_FAILED via venue_command_repo with EXACTLY THE SAME schema as polling path (paper/live parity by construction)
24857:+risk: medium
24874:+  - Apr26 forensic review §3 F-012 (Tests prove happy paths, not live-money safety)
24875:+  - Apr26 forensic review §6 axis-31 (paper/live parity — FAIL, S1)
24885:+    - tests/integration/test_p0_live_money_safety.py (NEW — 5+ P0 behavioral tests)
24894:+    tests/integration/test_p0_live_money_safety.py::test_duplicate_submit_idempotency
24897:+    tests/integration/test_p0_live_money_safety.py::test_rapid_sequential_partial_fills
24900:+    tests/integration/test_p0_live_money_safety.py::test_red_cancel_all_behavioral
24901:+    asserts: when riskguard sets RED, mid-01 emits CANCEL command via venue_command_repo for EACH open order (not just exit_reason); fake CLOB observes cancel_order calls; no live order remains open after sweep
24903:+    tests/integration/test_p0_live_money_safety.py::test_market_close_while_resting
24906:+    tests/integration/test_p0_live_money_safety.py::test_resubscribe_recovery
24915:+risk: high  # 5 distinct integration-grade tests + 2 new fixture modules; surface area large
24918:+  - 'Deterministic fake CLOB requires: state machine (place_order → resting → partial → filled), settlement clock (markets close at fixed time), failure-injection knobs (timeout, jitter, partial_response, oracle_conflict).'
24919:+  - Apr26 axis-31 (paper/live parity) closure depends on fake CLOB matching real V2 SDK shape exactly — antibody from down-03 (V2 SDK contract antibody) is the upstream contract.
24920:+  - This is the immune-system slice for live-money safety. Without it, X2's "0 F-012 violators" is scoped only to slice cards in scope, not to Apr26 §13 P0 list. Multi-review scientist + critic both flagged this gap.
24948:+risk: low
24951:+  - Pure manifest — no schema mutation, no live code change.
24954:+  - 'Authority status: stable_schema_not_current_truth_table; current per-market truth lives in NEW docs/operations/current_polymarket_validity.md (separate concern).'
24971:+  - src/contracts/settlement_semantics.py:50-183 (canonical Zeus dispatcher pattern — frozen dataclass + classmethod for_city())
24975:+  - 'src/contracts/settlement_semantics.py:50-183 (mirror shape: @dataclass(frozen=True) + classmethod for_city → for_market)'
24984:+  - tests/test_order_semantics.py::test_neg_risk_passthrough_per_market (negRisk markets vs not)
24986:+  - tests/test_relationship_settlement_to_order.py::test_settlement_and_order_share_market_id_resolution (cross-module relationship test — Fitz #2)
24991:+risk: low
24994:+  - Mirror SettlementSemantics.for_city() — established Zeus pattern, low risk.
24998:+  - '**R2L2 Attack-L2-7 GATE**: NC-NEW-C `zeus-create-order-via-order-semantics-only` semgrep mirrors NC-16. Direct `ClobClient.create_order()` outside allowlist (order_semantics + polymarket_client + live_smoke_test) is precommit-rejected. Without this, "subsumes F-009" claim is wishful — Python class can be bypassed.'
25022:+  - tests/test_executable_market_snapshot.py::test_gamma_open_neq_clob_tradable_caught (synthetic Gamma=open + CLOB=closed → command rejected)
25026:+risk: medium
25030:+  - 'Schema add: NEW TABLE executable_market_snapshots (snapshot_id PK, market_id, condition_id, yes_token_id, no_token_id, authority_tier, chain_resolved_at, raw_clob_snapshot_jsonb, fee_rate_bps, tick_size, min_order_size, neg_risk, captured_at, freshness_window_seconds, **collateral_token TEXT CHECK (''pUSD'',''USDC'')**).'
25076:+risk: medium
25097:+  - critic-opus gate because schema migration on production-shipped table + 15-col scope (post Mid R2L2 A1 ALTER MERGE absorbing mid-02's 3 signing cols).
25130:+risk: medium
25148:+  - src/state/chain_reconciliation.py:46 (LEARNING_AUTHORITY_REQUIRED='VERIFIED' — calibration filter pattern to mirror)
25154:+  - src/execution/harvester.py (harvest — exclude UNVERIFIED from settlement evidence)
25156:+  - src/riskguard/riskguard.py (RED executor — refuse to cancel UNVERIFIED rows)
25158:+  - src/calibration/store.py + src/calibration/manager.py (training — exclude UNVERIFIED-derived position_events; mirror chain_reconciliation.py:46 LEARNING_AUTHORITY_REQUIRED)
25162:+  - tests/test_unverified_rejection.py::test_harvester_excludes_unverified_from_settlement
25164:+  - tests/test_unverified_rejection.py::test_riskguard_refuses_to_cancel_unverified
25166:+  - tests/test_unverified_rejection.py::test_calibration_excludes_unverified_position_events
25170:+risk: medium
25174:+  - 7-row matrix per opponent-up Attack-L2-3 (was 4 in turn-1, expanded to 7 in turn-2: +risk_guard, +executor.preflight, +calibration training).
25204:+risk: medium
25220:+title: FROZEN_REPLAY_HARNESS — bit-identical replay of probability chain (P_raw → Size) pre/post Wave A+B (calibration-schema-drift antibody)
25222:+  - AGENTS.md "THE PROBABILITY CHAIN" — full chain `51 ENS members → daily max → MC → P_raw → Platt → P_cal → α-fusion → P_posterior → Edge & DBS-CI → Kelly → Size`
25223:+  - 'evidence/multi_review/trading_correctness_report.md PROBABILITY_CHAIN_RISKS §1 (schema risk: up-04 15-col ALTER + INV-29 amendment touch venue_commands; "no antibody asserts calibration tooling reads only schema-versioned columns")'
25232:+    - tests/fixtures/ — must contain at least 3 frozen portfolio states (e.g., 2026-04-15 weather-Day0, 2026-04-20 settlement-day, 2026-04-25 mid-cycle-degraded)
25243:+    asserts: replay of fixture portfolio through Edge & DBS-CI → Kelly → Size yields IDENTICAL position_size float after mid-01..06 land; isolates calibration-output drift from execution-output drift
25246:+    asserts: calibration tooling reads only columns present at pre-Wave-A schema version (validates `pragma user_version` gating); fails fast if calibration accidentally consumes a new up-04 column
25249:+    asserts: mid-01's new RED→durable-cmd authority surface does NOT mutate calibration inputs (pure execution-side change); P_posterior is identical with/without mid-01 wired
25252:+  - up-04  # 15-col ALTER must land — replay validates ALTER doesn't leak into calibration
25253:+  - mid-02  # signed_order_hash binding must land — replay validates payload-binding is execution-only, not calibration-side
25255:+  - mid-01  # RED→durable-cmd must land — replay validates RED path doesn't alter calibration
25257:+risk: medium
25260:+  - 'SLICE PURPOSE: prove the probability chain (forecast → Size) is structurally INTACT post-plan. Plan touches the tail of the chain (Size → Order). This slice antibodies any silent leak from execution-side changes back into calibration-side outputs.'
25262:+  - 'Snapshots captured BEFORE Wave A starts via scripts/capture_frozen_portfolio.py. Critical: snapshot tool must capture ALL upstream inputs (ENS members, daily max bins, source role assignments, settlement targets) AT pre-Wave-A schema version.'
25283:+## Per-card risk + gate + dependencies
25294:+| mid-01 | ? | 6 | standard | mid-02, mid-03 | A1 K4 RED → durable-cmd (new authority surface) |
25325:+Authority basis: `zeus/AGENTS.md` §1 (probability chain), `docs/reports/authority_history/zeus_live_backtest_shadow_boundary.md`, `architecture/invariants.yaml` (INV-06 point-in-time, INV-13 multiplier provenance, INV-15 forecast cycle identity)
25336:+1. **D1**. What backtest is *for* is not typed; it lives in docstrings (Constraint #2 violation).
25339:+4. **D4**. Decision-time-truth provenance is enforced via comments (`DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({...})` at [replay.py:42-46](../../../src/engine/replay.py:42)) but not via type — `INV-06` is doc-level, not structure-level.
25347:+**Question answered.** Given `ensemble_snapshots` of P_raw at decision time and `settlements.winning_bin` as ground truth, how good are the probabilities? Output: Brier, log-loss, accuracy, climatology skill score, calibration buckets.
25353:+| Decision-time P_raw vector | `ensemble_snapshots`: 0 rows ; `forecasts`: 23,466 rows (synthetic decision-time fallback) | The probability under test |
25354:+| `settlements.winning_bin` | 1,469 VERIFIED rows | The ground truth |
25364:+**Verdict.** This lane is **runnable today** and is the only honest output of the current replay stack. It maps to `WU_SWEEP_LANE` ([replay.py:1721](../../../src/engine/replay.py:1721)) but is currently mixed into the same `ReplaySummary` struct as the others.
25368:+**Question answered.** If the current code had been live during \[start, end\], what would the realized PnL trajectory have been, with statistical significance against a control? Output: $ PnL curve, Sharpe, drawdown, FDR-adjusted alpha vs control.
25376:+| Polymarket fee + tick + neg_risk | not captured at decision time historically | Need historical capture |
25385:+**Question answered.** Given a candidate code change (calibration tweak, alpha rule, exit threshold), at what fraction of historical decisions would the new code have made a different decision (trade vs no-trade, different bin, different size class) than what was historically logged? Output: divergence matrix per cohort, surfacing of unintended regressions.
25395:+**Verdict.** This lane is **runnable when `decision_log` or `shadow_signals` has historical records**. Today both substrates are largely empty in the canonical DB but oracle/instrumentation captures may exist. Worth one explicit probe before declaring impossible.
25397:+**Critical distinction.** DIAGNOSTIC is *not* economics. It does not compute PnL. It surfaces "would the new code have made a different decision than the old code on this snapshot?" This is the **antibody** Zeus actually needs to prevent silent calibration regressions — not a PnL fairy tale.
25418:+SKILL_FIELDS = frozenset({"brier", "log_loss", "accuracy", "calibration_buckets",
25445:+├── skill.py                # SKILL lane (formerly run_wu_settlement_sweep + skill summarizers)
25503:+    BH_FDR = "bh_fdr"       # ECONOMICS: matches live entry contract
25524:+2. "Calling `run_replay(purpose=ECONOMICS)` with `market_price_linkage='none'` raises before opening any DB."
25532:+## 5. D4 antibody — decision-time provenance is typed
25536:+`DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({"shadow_signals", "ensemble_snapshots.available_at", "forecasts_table_synthetic"})` at [replay.py:42-46](../../../src/engine/replay.py:42) is a comment-level convention. `_replay_provenance_limitations()` ([replay.py:167-196](../../../src/engine/replay.py:167)) computes a count of "diagnostic_replay_subjects" and emits the rate as a metric — but no path actually rejects a subject because of it. The fallback chain at [replay.py:502-579](../../../src/engine/replay.py:502) descends through three increasingly speculative layers:
25541:+4. `forecasts_table_synthetic` (reconstructed midday)
25547:+`forecasts.forecast_issue_time = NULL` on every one of 23,466 rows. That means even the legacy table cannot prove decision-time truth without inferring `available_at = forecast_basis_date + 12h` or similar — which IS hindsight reconstruction. F11 from the forensic audit. **The risk is not theoretical; it is on disk now.**
25560:+    RECONSTRUCTED = "reconstructed"     # heuristic; HISTORY-ONLY, never enters training/economics
25566:+    provenance: AvailabilityProvenance
25571:+        return self.provenance in (AvailabilityProvenance.FETCH_TIME, AvailabilityProvenance.RECORDED)
25574:+        return self.provenance in (AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
25598:+1. `test_load_decision_time_truth_economics_rejects_reconstructed` — fixture row with provenance="reconstructed" + purpose=ECONOMICS raises.
25600:+3. `test_no_consumer_silently_calls_with_reconstructed_provenance` — semgrep / ast-grep antibody equivalent to INV-06's existing rule.
25615:+- Cut `run_wu_settlement_sweep()` + `_summarize_binary_samples()` + `_summarize_forecast_skill()` out of `replay.py` into `skill.py`.
25641:+- LOW-track settlement writer.
25642:+- TIGGE local rsync.
25643:+- WU empty-provenance backfill.
25668:+- **Fitz Constraint #4** (data provenance): D4 makes provenance a typed field, not metadata.
25679:+Authority basis: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md` §3 (39-finding registry), `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/17_apply_order.md` (P0→P4 sequencing), live disk probes 2026-04-27
25686:+The 4-decision design in `01_backtest_upgrade_design.md` proves that **D1+D3+D4 (purpose-split, sentinel sizing, decision-time provenance typing) are pure code work that can ship today, while D2 (PnL gating) and the entire ECONOMICS purpose are blocked by data-layer issues**.
25700:+| **Reversibility** | `reversible` / `one-way` | Drives risk tolerance |
25706:+### 3.A — Empty WU observation provenance (39,431 of 39,437 rows = 99%)
25710:+**Unblocks:** `SKILL` (partially — provenance gates training data eligibility), `ECONOMICS` (fully)
25715:+| Source | Total rows | Empty provenance | Rate |
25721:+**The pattern is unambiguous.** WU writer historically did not stamp `provenance_metadata`; ogimet + HKO writers did. This is a **mono-source writer defect**, not a systemic data problem.
25727:+| **A. Quarantine all 39,431** | `UPDATE observations SET authority='QUARANTINED', quarantine_reason='empty_provenance_wu_daily'` | Low | Moves 99% of WU obs out of training-eligible — large eligibility hit; HK/Taipei impacts disputed (separate issue) |
25728:+| **B. Partial backfill from oracle_shadow** | Use `raw/oracle_shadow_snapshots/{city}/{date}.json` to populate `provenance_metadata` for the 480 overlapping rows; quarantine the rest | Medium | Coverage: 480 / 39,431 = **1.2%** — most rows still need quarantine. Oracle shadow only covers 2026-04-15 to 2026-04-26 |
25729:+| **C. Log-replay reconstruction** | Walk historical fetcher logs (if any exist) to re-derive provenance deterministically | High; requires log persistence the operator hasn't confirmed | Highest authority; lowest probability of execution |
25734:+2. **Same packet**: option A for everything else — explicit quarantine with `quarantine_reason='empty_provenance_wu_daily_pre_2026-04-15'`. This is reversible if option C ever lands.
25737:+**Antibody test:** `tests/test_observations_provenance_required.py::test_writer_rejects_empty_provenance` — applies to the writer going forward.
25758:+- Polymarket has many live temperature markets (count from search summary, low-criticality)
25765:+- **Resolution source for verified US markets** (NYC / Chicago / Miami / LA): **Wunderground / KLGA, KORD, KMIA, KLAX** — Zeus's `settlements.settlement_source` (1400+ wunderground.com URLs) **matches reality**. The earlier "NOAA" claim was retracted in 04 §C3.
25773:+| **B. Forward-only WebSocket capture** | Subscribe to Polymarket Market Channel; persist book/price_change/last_trade events into `market_price_history` going forward | Real-time orderbook truth from go-live; perfect for forward-grade ECONOMICS | Medium — daemon, sequence-gap handling |
25778:+**Real per-period exceptions to the WU resolution rule** (these are narrow and already in `known_gaps.md`, NOT structural):
25779:+- **Taipei** switched 03-16~03-22 (CWA) → 03-23~04-04 (NOAA Taoyuan) → 04-05+ (WU/RCSS Songshan) — the only city with multi-period source switching
25780:+- **HK 03-13/14**: WU/VHHH (HK Airport) used by Polymarket; Zeus has HKO Observatory
25781:+- **WU API vs WU website daily summary** divergence: ~19 mismatches on SZ/Seoul/SP/KL/Chengdu — Zeus reads WU API `max(hourly)`, Polymarket reads WU website daily summary. Both WU, different code paths.
25814:+- `RECONSTRUCTED` (heuristic; not training-grade)
25819:+2. **Backfill slice**: for the 23,466 existing rows, derive `forecast_issue_time` deterministically from `forecast_basis_date` if + only if a clear ECMWF run schedule applies. Stamp `provenance.availability_provenance = "DERIVED_FROM_DISSEMINATION"` so downstream training filters can choose to include or exclude.
25821:+**Acceptance gate:** training-eligibility view rejects rows with `availability_provenance IN ('reconstructed', NULL)`.
25823:+**Antibody test:** `tests/test_forecasts_writer_availability_provenance_required.py`.
25827:+### 3.D — All settlements are HIGH-track; LOW-track structurally absent
25837:+SELECT temperature_metric, COUNT(*) FROM settlements GROUP BY temperature_metric;
25844:+**Operator question Q4:** does Zeus onboard LOW-track in production?
25846:+- If **yes**: a separate data-engineering packet is required (writer + reconstruction of historical LOW settlements from existing observations). Out of scope here. Backtest design accommodates either way (purpose-split is metric-agnostic).
25856:+**Owner:** `operator` (decision: how to repopulate / when live restarts)
25858:+**Reversibility:** one-way (live trades create canonical events)
25872:+This is **post-canonical-DB-cutover state**. The auto_pause tombstone has been in place since 2026-04-16, so no trades have happened in 11+ days. Earlier history may exist in legacy `state/zeus.db` but per `current_data_state.md` that's "legacy and not the current canonical data store".
25876:+- The current `run_trade_history_audit()` lane has nothing to audit until live trading resumes.
25878:+- A useful interim DIAGNOSTIC tool is **forward-only**: instrument the live engine (when it un-pauses) to record decisions, then run DIAGNOSTIC against the rolling forward window.
25883:+2. Once live resumes, instrument decision capture (already in design via `shadow_signals` + `decision_log`).
25884:+3. DIAGNOSTIC purpose has a meaningful corpus after ~2 weeks of live operation.
25897:+**Boundary doc rule (verified 2026-04-27):** "No replay-derived promotion authority. Until replay achieves full market-price linkage across all subjects, active sizing parity, and selection-family parity, its output may inform but NOT authorize live math changes."
25912:+                 [ S2: skill.py (live data sufficient) ]                          [ S3.C: forecasts writer fix + backfill ]
25944:+| 3 | F11 writer fix: `scripts/forecasts_append.py` requires `availability_provenance` | nothing | medium |
25951:+| 5 | Empty-provenance triage: option B+A combined (3.A) | Q5 answer | data-eng |
25963:+| 12 | Q4: LOW-track production scope |
25964:+| 13 | Q5: empty-provenance handling option (A / B / C / mixed) |
25970:+- DIAGNOSTIC matures **only** when: live resumes (separate decision) + 2 weeks of trade decisions accumulated.
25971:+- SKILL unblocks **today** with empty-provenance gate via the `authority_tier='VERIFIED'` filter.
25977:+- Mutate any of the 39,431 empty-provenance rows. The triage above describes the work; the work belongs in `task_2026-04-XX_observations_empty_provenance_triage`.
25981:+- Build the LOW-track settlement writer.
26015:+Authority basis: live SQL probes against `state/zeus-world.db` and `state/zeus_trades.db` 2026-04-27, `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/12_major_findings.md`, `docs/operations/current_data_state.md`
26016:+Status: planning evidence; not authority. All row counts probed live within the 30-min audit window of this writing.
26024:+Every claim in §2 was probed live on 2026-04-27. Memory L20 grep-gate applied throughout.
26043:+**External reality calibration (verified 2026-04-27 against polymarket.com + docs.polymarket.com):**
26045:+- 361 live temperature markets exist right now → historical backlog is large
26049:+- ⚠ **CORRECTED (see [04 §C3](04_corrections_2026-04-27.md#c3-polymarket-us-weather-market-resolution-source))**: US weather markets verified verbatim use **Wunderground** (KLGA/KORD/KMIA/KLAX) — Zeus's settlement_source matches reality. The earlier "NOAA stations" framing here was a WebSearch hallucination.
26051:+**Implication.** Even ingesting Polymarket data won't automatically align with Zeus's existing `settlements` table because:
26053:+- `settlements.settlement_source` is dominated by `wunderground.com` URLs (1400/1469 rows verified 2026-04-27)
26061:+3. Mismatch markets enter a quarantine bucket — NOT used for training/economics until Zeus also ingests the matching observation source (NOAA / WU/VHHH for HK / etc.).
26067:+### 2.2 — Empty WU observation provenance (CRITICAL — training/SKILL blocker)
26071:+source=wu_icao_history: total=39,437, empty_provenance=39,431 (99%)
26072:+source=ogimet_metar_*: total=2,491, empty_provenance=0 (0%)
26073:+source=hko_daily_api: total=821, empty_provenance=0 (0%)
26080:+- **SKILL** lane: training-eligibility filter rejects empty-provenance rows; if all 39k rows are filtered, only 6 WU rows + 3,312 non-WU rows = 3,318 of 42,749 = **7.8%** of observations remain. Significant scale loss.
26081:+- **DIAGNOSTIC** lane: same impact, since calibration depends on the same observation substrate.
26092:+**Conclusion:** oracle_shadow_snapshots covers ~96 of 39,431 empty-provenance rows = **0.24%**. It cannot meaningfully backfill historical empty provenance. It IS authoritative going-forward, however — the format already includes `wu_raw_payload`, `station_id`, `captured_at_utc`, `source`, `data_version`. So the going-forward writer should be hardened against ever producing empty provenance again, while history is handled by quarantine.
26096:+1. Quarantine: `UPDATE observations SET authority='QUARANTINED', quarantine_reason='empty_provenance_wu_daily_pre_2026-04-15'` for the ~39,335 rows pre-2026-04-15.
26097:+2. Backfill from oracle_shadow_snapshots: for the ~96 overlap rows (2026-04-15, 2026-04-16) populate `provenance_metadata` from the corresponding shadow file. Hash matches; explicit provenance_method = `oracle_shadow_backfill_v1`.
26098:+3. Forward-going writer hardening: `wu_history_writer.py` (or wherever the WU daily writer lives) MUST require `provenance_metadata` payload-hash + source URL + parser version (per forensic Fix 4.3.B).
26099:+4. Antibody test: relationship test that asserts NO `wu_icao_history` row can be inserted with empty provenance going forward.
26105:+### 2.3 — `forecasts.forecast_issue_time` NULL on every row (HIGH — F11 hindsight risk)
26114:+**Forensic ref:** F11 ("Forecast `available_at` may be reconstructed → hindsight leakage risk")
26116:+**Backtest impact:** the entire `decision_time_truth.AvailabilityProvenance` axis is sitting at NULL or `RECONSTRUCTED`. Every backtest using `forecasts` rows currently violates INV-06 (point-in-time truth) silently. The current `_forecast_reference_for()` ([replay.py:326-362](../../../src/engine/replay.py:326)) emits `decision_reference_source: "forecasts_table_synthetic"` and `decision_time_status: "SYNTHETIC_MIDDAY"` — i.e., it knows the source is non-canonical. But downstream consumers don't enforce.
26118:+**External calibration (verified 2026-04-27 at ecmwf.int):**
26126:+1. Extend `forecasts` schema or use `provenance_json` to carry `availability_provenance` ∈ {`FETCH_TIME`, `RECORDED`, `DERIVED_FROM_DISSEMINATION`, `RECONSTRUCTED`}.
26128:+3. Backfill the 23,466 existing rows: `availability_provenance = "DERIVED_FROM_DISSEMINATION"` since `forecast_issue_time` is NULL but `forecast_basis_date` exists. Going-forward writer must require `RECORDED` when source headers permit.
26129:+4. Training-eligibility view rejects `availability_provenance IN ('reconstructed', NULL)`.
26131:+**Antibody test:** `tests/test_forecasts_availability_provenance_required.py`.
26150:+- 1.81 million rows have NULL on every INV-14 column added by C7's ALTER (`temperature_metric`, `physical_quantity`, `observation_field`, `data_version`, `training_allowed`, `causality_status`, `source_role`)
26151:+- `training_allowed DEFAULT 1` means **all 1.81M rows are silently training-eligible** despite the writer never populating these fields
26153:+- Backtest using these rows produces metric-confused calibration data
26162:+**Backtest design accommodation:** the new `decision_time_truth` loader does NOT read from `observation_instants_v2` (it reads from `ensemble_snapshots` for forecasts and `settlements` for ground truth). So this issue does not directly block backtest, but does block training and Day0 signal computation.
26166:+### 2.5 — `settlements` is 100% HIGH-track; LOW-track structurally absent
26170:+SELECT temperature_metric, COUNT(*) FROM settlements GROUP BY temperature_metric;
26179:+**External reality:** Polymarket has both HIGH and LOW temperature markets (visible at polymarket.com/weather/temperature). If Zeus intends LOW-track production trading, the writer gap is a market-coverage gap, not just a backtest gap.
26187:+### 2.6 — `settlements` settlement_source ≠ Polymarket resolution source (HIGH — semantic gap)
26191:+-- 1400/1469 (95%) settlement_source rows are wunderground.com URLs
26199:+- Even with `market_events_v2` populated (issue 2.1), the join `settlements ⨝ market_events` may resolve **wrong** for the US market cohort (NYC, Chicago, Atlanta, Dallas, Miami, Seattle, Houston, San Francisco, Los Angeles, Austin, Denver — major US cities).
26200:+- Zeus's `settlement_value` from WU may not match Polymarket's actual resolved value from NOAA.
26201:+- Backtest using these mismatched settlements produces silently-wrong PnL (economics) and silently-wrong outcomes (skill).
26206:+2. For DIVERGED markets: either ingest the matching observation source (NOAA for US), OR exclude from training.
26207:+3. The HK 2026-03-13/14 case (per `known_gaps.md`) is the canonical instance: Polymarket used WU/VHHH airport, Zeus has HKO observatory.
26225:+- Without `ensemble_snapshots`, the SKILL lane falls back to `forecasts_table_synthetic` ([replay.py:326](../../../src/engine/replay.py:326)) with `decision_time_status: "SYNTHETIC_MIDDAY"`. This is a **real degradation** — synthetic decision-time, not an actual Zeus snapshot.
26227:+- TIGGE data is downloaded on cloud but not yet ingested locally per current_data_state.md §9 + handoff §C1/C2.
26231:+1. Operator: TIGGE rsync cloud → local (3.B in handoff Fix 4.7).
26232:+2. Data-engineering: `scripts/ingest_grib_to_snapshots.py` runs against local TIGGE; populates `ensemble_snapshots_v2`.
26233:+3. Source-time verification: `available_at` from TIGGE source headers, never reconstructed (F11 antibody applies here too).
26235:+**Backtest design accommodation:** SKILL lane works today against `forecasts_table_synthetic` with explicit downgrade label; full-fidelity SKILL via `ensemble_snapshots_v2` becomes available after TIGGE ingest.
26254:+1. Probe `state/zeus.db` (legacy) for residual `trade_decisions` history. If usable as DIAGNOSTIC-only (with explicit legacy provenance flag), it can seed a corpus.
26256:+3. Forward-only DIAGNOSTIC: instrument the live engine to capture decisions; build corpus over weeks.
26260:+### 2.9 — `data_coverage` 350,088 rows; lacks v2 forecast/settlement family tracking
26266:+**Backtest impact:** the data-immune-system memory exists but doesn't track `ensemble_snapshots_v2`, `settlements_v2`, `calibration_pairs_v2`, `market_events_v2`. Any "is the substrate ready?" question against v2 silently returns "no row in coverage = no expectation = OK", which is wrong.
26276:+99% empty provenance is a single-writer defect. Fixing the WU writer + going-forward enforcement is a small code change. Backfill is the expensive part.
26280:+23,466 NULL `forecast_issue_time` rows make F11 a present-tense risk, not a future-tense risk. The new `availability_provenance` typed enum is the antibody.
26298:+| **SKILL** (with `forecasts_table_synthetic` fallback) | `settlements > 0` AND any decision-time vector source available | ✓ runnable |
26299:+| **SKILL** (full-fidelity via ensemble_snapshots_v2) | + `ensemble_snapshots_v2 > 0` (after TIGGE ingest) | ✗ blocked by 2.7 |
26300:+| **SKILL** (training-eligible filter) | + provenance gate: `availability_provenance ∈ {FETCH_TIME, RECORDED, DERIVED_FROM_DISSEMINATION}` AND `authority='VERIFIED'` AND non-empty observation provenance | ✗ blocked by 2.2 + 2.3 (until backfill + writer hardening) |
26310:+| 1 | 2.3 — `forecasts.forecast_issue_time` typed | SKILL training-eligibility | Code (small) + backfill (medium) | **P0 — code-only, ship now** |
26311:+| 2 | 2.2 — empty WU provenance triage | SKILL training-eligibility | Operator decision + backfill | **P0 — Q5 needed** |
26313:+| 4 | 2.7 — TIGGE rsync + ingest | full-fidelity SKILL | Operator + data-eng | **P1** |
26314:+| 5 | 2.4 — `observation_instants_v2` INV-14 backfill | training/Day0, NOT direct backtest | Architecture decision (forensic §14 Q7) | **P1 — separate packet** |
26324:+- **L20 grep-gate**: every row count in this doc was probed live within 30 minutes of writing.
26327:+- **Fitz Constraint #4 (data provenance)**: this entire doc IS the application of the principle. Every row count carries a `source` (live SQL probe) and `authority` (verified at 2026-04-27).
26337:+Status: audit-trail evidence; corrects errors in plan.md, 01, 02, 03, and `evidence/reality_calibration.md`.
26369:+**Correct formula for `DERIVED_FROM_DISSEMINATION` provenance:**
26381:+- F11 hindsight risk: a forecast row with `forecast_basis_date=2026-04-20` and `lead_days=3` has `available_at = 2026-04-20T00:00 + 6h40m + 12min = 2026-04-20T06:52:00 UTC`. Any backtest using this row for "decision-time truth" earlier than 06:52 UTC on 2026-04-20 commits hindsight leakage.
26410:+**False claim (in plan.md §1 + 02 §3.B + 03 §2.1):** "Polymarket US markets resolution source is NOAA stations, not Wunderground" — implying Zeus's `settlements.settlement_source = wunderground.com URLs` is structurally mismatched.
26412:+**Source of error:** First WebSearch result summary on Polymarket weather markets stated "NOAA official station records are the most common resolution source for US temperature markets" — this was a hallucination by the search summarizer. The summarizer probably conflated "WU.com displays NOAA-managed station data" (true: KLGA, KORD, KMIA, KLAX are all NOAA-managed ICAO stations) with "Polymarket reads NOAA directly" (false).
26423:+**Zeus's existing model was correct.** Per [zeus_market_settlement_reference.md:155-162](../../../docs/reference/zeus_market_settlement_reference.md:155):
26424:+> "Real temperature → NWP → ASOS sensor → METAR → **Weather Underground display → Polymarket settlement integer**"
26428:+1. **WU API vs WU website daily summary divergence** (forensic F9, narrow): Zeus reads WU API `max(hourly)`, Polymarket reads WU website daily summary. Both are "WU" but different code paths. ~19 mismatches on SZ/Seoul/SP/KL/Chengdu per [known_gaps.md](../known_gaps.md). NARROW issue.
26429:+2. **Taipei period switching** (known_gaps): 03-16~03-22 used CWA → 03-23~04-04 used NOAA Taoyuan → 04-05+ used WU/RCSS Songshan. This IS the only period where Polymarket genuinely used NOAA. Specific to Taipei.
26430:+3. **HK 03-13/14**: Polymarket used WU/VHHH (HK Airport), Zeus has HKO Observatory data — still WU, different station.
26431:+4. **HKO floor rounding** (known_gaps HK): HK alone uses `oracle_truncate`, not `wmo_half_up`.
26433:+**Impact on backtest design:** my claim "Zeus has structural settlement-source mismatch with Polymarket" is RETRACTED. The actual state is "Zeus model matches reality; narrow per-city / per-period exceptions are tracked in known_gaps.md". The blocker_handling_plan §3.B and data_layer_issues §2.6 must be rewritten to reflect this.
26439:+**False claim (in 02 §3.B + 03 §2.1):** "No public archive API for orderbook snapshots — bid/ask price history must be captured live via WebSocket or sourced from a third-party archive"
26447:+1. **Gamma API** (`gamma-api.polymarket.com`) — Zeus already uses this for market discovery per `zeus_market_settlement_reference.md:33`. Provides market metadata, lifecycle events.
26462:+**What's still unclear (legitimate uncertainty):** whether the `orderbook-subgraph` indexes orderbook **snapshots at arbitrary timestamps** vs only events. The github README describes it as "order data" without specifying snapshot retention. Verifying this requires reading the actual subgraph schema (`schema.graphql`) — DEFERRED to a separate verification slice.
26499:+| `wu_icao_history` 39,431/39,437 = 99% empty provenance | Direct SQL probe 2026-04-27 ✓ |
26525:+| U2 | Polymarket has 361 live temperature markets right now | **VERIFIED-WITH-CONTEXT** | polymarket.com/weather/temperature 2026-04-28 shows ~60-70 daily-temperature events. Each event has multiple bin markets (typically 6-10), so 60-70 × 6-10 = 360-700 bin markets ≈ "361 markets" reported earlier. Unit confusion (events vs markets), not a factual error. |
26528:+| U5 | All 5 forecast sources have known dissemination schedules | **PARTIAL — RESOLVED** | F11.1 slice (commit 14d87ae 2026-04-28) registers all 5 sources with verified ECMWF (confluence wiki) + verified GFS (NCEP production status); ICON/UKMO/OpenMeteo carry RECONSTRUCTED tier until primary-source schedule captured. See [src/data/dissemination_schedules.py](../../../src/data/dissemination_schedules.py). |
26529:+| U6 | Polymarket Data API REST `/trades` is publicly queryable without auth | **RETRACTED — AUTH REQUIRED** | `curl -s -o /dev/null -w "%{http_code}" "https://clob.polymarket.com/data/trades?limit=1"` returns **HTTP 401** on 2026-04-28. Data API REST `/trades` requires authenticated access; not anonymously queryable. Subgraph (via The Graph) remains the unauthenticated path. |
26530:+| U7 | TIGGE archive on the cloud VM matches the standard ENS 51-member shape | **VERIFIED** | gcloud SSH probe to tigge-runner 2026-04-27 — actual JSON files have `member_count: 51` and `members: list len=51` (member 0 = control + members 1-50 perturbed). See [evidence/vm_probe_2026-04-27.md](evidence/vm_probe_2026-04-27.md) §5. |
26565:+| [plan.md](plan.md) | §1 reality calibration table (3 rows: Polymarket source, ENS members, dissemination lag) |
26569:+| [evidence/reality_calibration.md](evidence/reality_calibration.md) | replace incorrect rows; add correction pointer |
26581:+- **Decision-time provenance typing (D4)** — UNCHANGED in shape; the lag formula moves from `+40min` to `+6h40min × leadDay-scaled`. Cite confluence wiki, not the 2017 news article.
26583:+- **Settlement-source mismatch fear (03 §2.6)** — RETRACTED for general US case. Narrow exceptions remain (Taipei periods, HK 03-13/14, WU API vs website divergence). These are already in `known_gaps.md` and need no special backtest treatment beyond what's already logged.
26592:diff --git a/docs/operations/task_2026-04-27_backtest_first_principles_review/evidence/reality_calibration.md b/docs/operations/task_2026-04-27_backtest_first_principles_review/evidence/reality_calibration.md
26596:+++ b/docs/operations/task_2026-04-27_backtest_first_principles_review/evidence/reality_calibration.md
26601:+Authority basis: live SQL probes + external WebFetch (polymarket.com, ecmwf.int) on 2026-04-27
26614:+| settlements | 1,561 | 1469 VERIFIED + 92 QUARANTINED, 100% temperature_metric=high |
26615:+| observations | 42,749 | 42,743 VERIFIED, 39,431 with empty provenance_metadata (99% of WU rows) |
26619:+| ensemble_snapshots_v2 | 0 | TIGGE not ingested |
26620:+| calibration_pairs | 0 | legacy |
26621:+| calibration_pairs_v2 | 0 | gated on P4 |
26624:+| settlements_v2 | 0 | |
26664:+### `settlements.settlement_source` distribution (top 20)
26689:+→ **1400+ rows are WU URLs**, including all major US cities.
26691:+### `observations` empty-provenance distribution by source
26703:+→ **The pattern is: WU writer never stamped provenance, others always did.** Mono-source defect.
26710:+- `wu_raw_payload` contains full WU API response with `observations[]` array (40 obs/day typical), full station metadata
26711:+- This IS a viable provenance source going forward (matches forensic Fix 4.3.B requirements: payload hash + source URL + parser version derivable)
26716:+settlements:    2025-12-30 to 2026-04-16  (61 distinct dates)
26721:+→ Overlap with empty-provenance WU rows: **~96 rows (2 dates × 48 cities)** = 0.24% of 39,431.
26731:+- 361 live temperature prediction markets
26733:+- ⚠ ~~US markets: NOAA stations are most common resolution source (NOT WU)~~ — **RETRACTED 2026-04-27**: 4 US markets verified verbatim (NYC/Chicago/Miami/LA) all use **Wunderground** (KLGA/KORD/KMIA/KLAX). See [04 §C3](04_corrections_2026-04-27.md#c3-polymarket-us-weather-market-resolution-source) for verbatim quotes.
26737:+- `neg_risk` field exists on multi-outcome markets; uses separate "Neg Risk CTF Exchange" contract
26769:+  L167  def _replay_provenance_limitations
26775:+  L1721 def run_wu_settlement_sweep   # WU_SWEEP_LANE
26788:+src/engine/replay.py:1767  WU_SWEEP_LANE limitations.authority_scope
26802:+# VM Direct Probe — TIGGE Ground Truth (2026-04-27)
26816:+## 2. TIGGE workspace layout
26874:+  'training_allowed',      # True
26887:+- ECMWF.int's documentation of "51 forecasts comprising one control plus 50 perturbed" matches the actual TIGGE archive.
26894:+### 6.A. F11 antibody on TIGGE-sourced data → `RECORDED` tier, not `DERIVED_FROM_DISSEMINATION`
26896:+TIGGE JSON files already carry `issue_time_utc` extracted from the GRIB header — that IS the authoritative issue time, not a reconstruction. Therefore when Zeus ingests these files:
26899:+# When the TIGGE writer populates ensemble_snapshots_v2 from these JSON files:
26900:+availability_provenance = AvailabilityProvenance.RECORDED  # NOT derived/reconstructed
26903:+training_allowed = json_doc["training_allowed"]            # already pre-stamped
26906:+The 6h40m dissemination-derivation formula in 01 §5 only applies to **other** forecast sources where Zeus's `forecasts` writer doesn't carry source-header issue_time. TIGGE is the ideal authoritative path.
26912:+- `training_allowed` → already pre-labeled, NOT silently defaulting to true
26916:+**Implication.** The forensic P1 provenance hardening work for the TIGGE ingest path is dramatically simpler than the WU writer remediation — TIGGE already meets P1 contracts at source. The writer just needs to STAMP these fields onto `ensemble_snapshots_v2` rows on insert.
26920:+Both `tigge_ecmwf_ens_mx2t6_localday_max/` and `tigge_ecmwf_ens_mn2t6_localday_min/` exist at scale on the VM. C4/F3 (no LOW track) is purely a Zeus *writer-side* gap; the upstream data is ready. This makes Q4 (operator decision on LOW production scope) cheaper to answer YES — no upstream data engineering needed.
26924:+The latest TIGGE files are 2026-04-18; today is 2026-04-27. **9-day lag** suggests:
26953:+| 01 §5 D4 | AvailabilityProvenance | Note: TIGGE-ingest path uses `RECORDED` tier (issue_time from GRIB header); 6h40m formula applies only to non-TIGGE sources |
26955:+| 02 §3 — TIGGE rsync blocker | Status | Add: data range stops at 2026-04-18 (9 days behind today); operator should check daemon |
26958:+These are non-load-bearing refinements; the core design (purpose-split, sentinel sizing, provenance typing) is unaffected.
26969:+Authority basis: `zeus/AGENTS.md` §1 (money path, probability chain), `docs/operations/current_state.md` (mainline = midstream remediation, P4 BLOCKED), `docs/reports/authority_history/zeus_live_backtest_shadow_boundary.md`, `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
26970:+Status: planning evidence; not authority. Does not mutate code/DB/manifests.
26983:+1. **`01_backtest_upgrade_design.md`** — the typed structural redesign (purpose-split, sentinel sizing, decision-time provenance enforcement, file split).
26989:+## 1. Reality calibration (verified against disk + external 2026-04-27)
26995:+| `forecasts` table empty | live SQL probe | **23,466 rows** (handoff doc was already stale) |
26996:+| `forecast_issue_time` recorded | live SQL probe | **NULL on every row** — F11 hindsight risk realised |
26997:+| `raw_payload_hash` recorded | live SQL probe | **NULL on every row** — F16 / F18 confirmed |
26998:+| Empty-provenance distribution | live SQL probe | **39,431/39,437 = 99% of `wu_icao_history` rows** ; ogimet+hko sources have 0% empty |
26999:+| `market_events` populated | live SQL probe | **0 rows** (all 3 tables) — F13 confirmed |
27000:+| `zeus_trades.db` has trade history | live SQL probe | **0 rows in every table** — `trade_history_audit` lane has nothing to audit |
27001:+| Polymarket weather market count | external (polymarket.com) | **361 live temperature markets 2026-04-27** |
27006:+| Oracle shadow snapshot coverage | disk count | **48 cities × 10 dates (2026-04-15 to 2026-04-26)** — does NOT overlap with most settlements |
27007:+| Settlement temperature_metric | live SQL probe | **100% high (0 low rows)** — C4 confirmed |
27009:+External evidence summary lives in `evidence/reality_calibration.md`.
27027:+- Authorizing live promotion of any replay-derived metric.
27029:+- LOW-track settlement writer (forensic C4) — separate packet.
27030:+- TIGGE local rsync or P4 readiness re-run — operator/data-engineering.
27036:+- **Planning lock**: this packet plus the three sub-docs touch only `docs/operations/task_2026-04-27_backtest_first_principles_review/**`. No code, no DB, no manifest, no `architecture/**`. Per `zeus/AGENTS.md` §3 the planning-lock check is informational; this packet does not require lock evidence because it does not modify governed surfaces.
27051:+| Q2 | For decision-time-truth typing: hard reject `RECONSTRUCTED` provenance, or annotate-and-allow? | F11 antibody | 01 §5 |
27052:+| Q3 | Polymarket data ingestion source: live websocket capture going forward, or third-party historical archive (paid), or both? | Economics-grade backtest | 02 §3.B |
27053:+| Q4 | LOW-track settlements: build a parallel writer now (data-engineering effort) or defer until v2 cutover? | LOW-track backtest | 02 §3.D |
27054:+| Q5 | Empty-provenance WU rows (39,431): quarantine all, or scope a partial backfill from oracle_shadow + WU log replay? | Training readiness | 02 §3.A, 03 §2 |
27062:+- Any of the v2 tables (`market_events_v2`, `ensemble_snapshots_v2`, `calibration_pairs_v2`, `settlements_v2`) becomes non-empty.
27065:+- Polymarket changes its CTF / neg-risk / settlement model materially.
27066:+- Zeus onboards LOW-track markets in production.
27085:+**Goal**: Reduce Zeus harness from current ~25K LOC to ~5K-6K LOC short-term and ~1.5-2K LOC at 24-month asymptote, **WITHOUT** losing the empirical catch-rate validated in round-1 evidence (Z2 retro 6/6, V2 BUSTED-HIGH 5+8, HK HKO antibody, all 5 verified semgrep rules, 12 schema-backed INVs).
27112:+| `evidence/opponent/round2_proposal.md` + `round2_critique.md` | Whole-replace spec (now retracted to ~3,500 LOC); §3.1 HKO type code; §3.2 47-line SKILL.md template; §3.4 drift-checker diff |
27129:+| 5 | Encode HK HKO as type subclasses | C | HIGH (K0_frozen) | src/contracts/settlement_semantics.py (+~60 LOC); fatal_misreads.yaml (HK row reference); tests/test_settlement_semantics.py (+1 relationship test) | TypeError on HKO+WMO mixing; existing tests preserved | git revert; YAML antibody still in place pending delete |
27174:+| P8 | Type-encoded antibody migration: HK HKO done in Tier 1 #5; consider 1-2 more candidates (per round-2 verdict W3 T1: only where type discipline is uniform) | 6-12h | Tier 1 #5 + Tier 2 #17 (mypy-strict policy) | per-antibody revert; YAML antibody preserved in defense-in-depth pattern |
27176:+| P10 | Validation: simulated regression replay + Z2-class pattern reproduction + 71-pass baseline + planning-lock receipts ledger | 10-15h | All P1-P9 done | NO ROLLBACK — this is verification only; failures roll back specific phases |
27253:+- [ ] HK HKO mixing raises TypeError (relationship test)
27265:+| Tier 1 #5 HK HKO type encoding has gap (round-2 W3 T1 critique) | MED | HIGH (live-money) | Defense-in-depth: keep YAML antibody marked TYPE_ENCODED; relationship test asserts mixing raises; mypy-strict policy decision (Tier 2 #17) |
27272:+| Forward-asymptote bet wrong (capability grows slower than expected) | LOW | LOW | Plan delivers ~5K-6K short-term; asymptote convergence is forward, not blocking |
27289:+| 7-10 `.claude/skills/` files (phase-discipline, task-boot×N, fatal-misreads, calibration-domain, settlement-domain) | ~700-1,000 |
27317:+**Why debate-valuable**: directly answers the "now that safety is sized, what's next?" question; both sides will produce concrete sequencing proposals; the answer materially affects Zeus's live-trading P&L.
27321:+**Question**: Among the 5 packets deferred in round-1 verdict §4, which is highest-leverage for Polymarket weather trading specifically? Sequencing options: edge-observation-first (measurement before optimization) vs calibration-hardening-first (the Platt model is the bottleneck) vs ws-or-poll-tightening-first (operational latency wins).
27367:+- **Daemon / runtime changes**: live trading daemon `src/main.py` and cycle_runner are not touched; only the documentation/manifest layer governing them.
27413:+Given Opus 4.7 (1M context window, released 2026-Q1) and GPT 5.5 capabilities as of 2026-04-27, is Zeus's current harness — defined below — **net-positive ROI for live-trading correctness**, or has the constraint surface itself become the dominant attention-drift cost?
27514:+- evidence/executor/_boot_executor.md (135 L) — 4 path corrections + 4 clarifications + per-batch risk grid
27519:+- src/contracts/settlement_semantics.py (182 L, full) — `RoundingRule` Literal pattern at L11; `for_city` HK branch at L161-173 already implements oracle_truncate via string dispatch
27529:+Implication: the 2 evaluator.py failures appear to have been resolved between baseline doc-time (~2026-04-23 midstream verdict v2) and boot-time (2026-04-28 evening). I will use **73-pass, 22-skip, 0-fail** as my live regression baseline. Any new failures = BLOCK regardless of the executor's "no new failures vs documented baseline" claim.
27564:+- **B1.3**: `pre-commit-invariant-test.sh` — must run pytest against the DOCUMENTED 71-pass baseline OR adapted live 73-pass. Any drift in reference number = silent escape.
27577:+C.1 SettlementRoundingPolicy ABC + HKO_Truncation + WMO_HalfUp subclasses (append-only per judge):
27578:+- **C1.1**: Does new ABC + subclasses live in `src/contracts/settlement_semantics.py` AS APPEND? Existing `SettlementSemantics` dataclass + `RoundingRule` Literal must be UNCHANGED. If executor refactored existing `for_city` dispatch → SCOPE BREACH (replace, not append).
27579:+- **C1.2**: Does the new ABC actually make the wrong code UNCONSTRUCTABLE per Fitz "make category impossible"? An ABC alone doesn't enforce — there must be a `settle_market(market, raw, policy)` (or equivalent) function that validates `isinstance(policy, HKO_Truncation)` for HK and rejects WMO_HalfUp via TypeError at call time.
27580:+- **C1.3**: Test file `tests/test_settlement_semantics.py` is NEW (per executor §1; did NOT exist on HEAD). Three relationship assertions per executor §C: `test_hko_policy_required_for_hk_city_raises_typeerror`, `test_wmo_policy_for_hk_city_raises_typeerror`, `test_hko_policy_for_non_hk_raises_typeerror`. Verify all 3 actually call the new `settle_market` function (or equivalent) and verify TypeError is raised — not just `policy is HKO_Truncation` introspection (would be tautological).
27581:+- **C1.4**: HK city detection — what's the predicate? `city.name == "HK"`? `city.settlement_source_type == "hko"`? Hardcoded list? **The predicate IS the antibody surface.** If executor used `if "HK" in city.name`, it will mis-route any city with HK in the name (Hong Kong AND others). Existing `for_city` uses `source_type == "hko"` which is the right anchor.
27582:+- **C1.5**: `fatal_misreads.yaml` row update — must APPEND `TYPE_ENCODED:src/contracts/settlement_semantics.py:HKO_Truncation` token; must NOT delete the antibody row (defense-in-depth per round-2 verdict §1.3 #4).
27585:+- **C1.8**: Does the test file have `# Created: 2026-04-27` + `# Authority basis:` per project convention (CLAUDE.md provenance rule)?
27628:+.venv/bin/python -m pytest tests/test_settlement_semantics.py -v
27629:+.venv/bin/python -c "from src.contracts.settlement_semantics import HKO_Truncation, WMO_HalfUp, SettlementRoundingPolicy; print('imports OK')"
27652:+- BATCH C type encoding may CONFLICT with BATCH D INV-16/17 delete if INV-17 (commit-ordering) interacts with `SettlementSemantics.assert_settlement_value` DB-write gate.
27673:+Pre-batch baseline: 73 passed / 22 skipped / 0 failed (live verified at boot)
27680:+The 3 doc-only deliverables are functionally complete, regression-clean, and survive the A1-A3 attack vectors I anticipated in `_boot_critic.md §2`. Two non-blocking concerns that should be tracked into BATCH B and Tier 2:
27685:+I articulate WHY this APPROVE: the executor's path-(a/b) deprecate-with-stub recommendation (per their boot §2 BATCH A.1) was the CORRECT call vs full-delete, validated by my independent `--code-review-graph-protocol --json` returning `{"ok": true, "issues": []}`. Path-c (full delete + 5-script patch) would have been ~150 LOC of changes outside doc-only batch boundary and would have risked regressions. The work is honest, the work is contained, the work doesn't exceed BATCH A scope.
27687:+## Pre-review independent reproduction
27706:+ZERO drift from live baseline (73/22/0). My boot-time live baseline was already 73-pass, NOT the 71-pass documented in judge_ledger §54 + executor _boot §3 — meaning the 2 evaluator.py:377 failures were resolved sometime between baseline doc-time and now. **Executor's "73 pass / 22 skip / 0 fail matches your live baseline" claim is correct; this is the new ground truth.**
27720:+- All 6 `forbidden_uses` items intact (settlement truth / source validity / current fact freshness / authority rank / planning lock waiver / receipt or manifest waiver).
27736:+**Bonus A1 finding**: `metadata.deprecated="2026-04-28"` and `metadata.superseded_by="AGENTS.md root §Code Review Graph (inline 6-line summary)"` were added — these are GOOD provenance markers per CLAUDE.md "Code Provenance: Legacy Is Untrusted Until Audited" rule. A future cold-start agent can immediately tell this file is on retirement track.
27796:+## Independent regression baseline reproduction
27836:+5 deliverables all verified by independent reproduction. Both CAVEATs from BATCH A are resolved (SKILL `model: inherit` + workspace settings.json registration). All 4 attack vectors from boot §2 BATCH B pass. All 6 supplementary checks from team-lead's review prompt pass.
27838:+I articulate WHY this APPROVE: the executor demonstrated REAL discipline this batch. (a) Hooks have working escape hatches that I exercised live (`COMMIT_INVARIANT_TEST_SKIP=1` → SKIPPED message + exit 0; `ARCH_PLAN_EVIDENCE` → allow). (b) Hook command-pattern matching distinguishes `git commit` from plumbing `git commit-tree` — verified live. (c) The shim correctly delegates to the r3-located module via subprocess pass-through, NOT a brittle import or sys.path hack. (d) The 34 RED in drift-checker output were spot-audited 5/5 to be pre-existing — executor honestly surfaced ALL drift instead of filtering down to a curated subset. (e) settings.json schema matches the canonical pattern in `~/.claude/settings.json` exactly (PreToolUse → matcher → hooks array of {type, command, description}).
27840:+The only non-blocking observation is that the commit hook's BASELINE_PASSED is HARDCODED at 73; if the live baseline grows (e.g. BATCH C adds a new test → 76 pass), the hook will need a manual baseline update. **Tracked as CAVEAT-B1 below; not blocking.**
27842:+## Pre-review independent reproduction
27867:+## ATTACK B1 (hooks behavior — independent live smoke-test) [VERDICT: PASS]
27904:+PASS. Both have +x. No silent no-op risk.
27936:+GREEN=241 YELLOW=0 RED=0
27940:+PASS. The shim correctly invokes the r3-located module via subprocess (L100-108), not a fragile `sys.path.insert` import. Pass-through args excluding the new `--architecture-yaml`+`--json` flags (L107) preserves back-compat. The r3 script ran clean (20 phases, 241 GREEN, 0 RED) — no breakage to the original Tier 0 use case.
27946:+architecture/*.yaml drift check: 4035 GREEN, 34 RED
27947:+[34 RED entries, each with yaml + cite + missing_path]
27950:+PASS. New flag invokes `check_architecture_yaml()` (L61-97) instead of `_delegate_to_r3()`. JSON mode supported via `--json`. Exit code 1 when RED present (per L132 `0 if not result.get('red') else 1`).
27960:+  (b) The checker DOES catch every other class of architecture/*.yaml citation drift — proven by 34 real RED finds.
27965:+The drift checker uses `yaml.safe_load(yaml_path.read_text())` (L76). YAML comments are inert. Verified: invariants.yaml has 2 PRUNE_CANDIDATE comment lines (INV-16, INV-17) and the drift checker processed it successfully (no YAML_PARSE_ERROR in the RED list).
27995:+## 34 RED audit (per team-lead's request: spot-check 5+ to confirm pre-existing)
27997:+Audited 5 of 34 RED entries against `git show HEAD:<file>`:
28026:+I have NOT written "looks good" or "narrow scope self-validating." I have engaged the strongest claim (hooks actually fire correctly + drift-checker actually catches real drift) at face value before pivoting to the CAVEATs. I have independently exercised every advertised behavior live (12 smoke-test cases + bash -x trace + 5 RED audits + planning_lock + pytest re-run + drift checker default + new mode). Zero rubber-stamp.
28032:+| CAVEAT-B1 | `BASELINE_PASSED=73` is hardcoded in pre-commit-invariant-test.sh L53; BATCH C will add ~3 new tests in `tests/test_settlement_semantics.py`, bumping baseline to 76 | Executor MUST update L53 to `BASELINE_PASSED=76` (or whatever the post-BATCH-C count is) as part of BATCH C | executor |
28038:+1. **Executor MUST update `BASELINE_PASSED=73` → `76` in pre-commit-invariant-test.sh L53 as part of BATCH C** when adding the 3 new relationship tests in `tests/test_settlement_semantics.py`. Otherwise BATCH C's own commit gate will block the BATCH C commit (test count regressed downward only because the variable wasn't updated upward — false-positive block).
28040:+2. **For BATCH C architecture/** edits** (the new `tests/test_settlement_semantics.py` is OK because it's `tests/` not `architecture/`; but `fatal_misreads.yaml` HK row update IS architecture/**), executor MUST set `ARCH_PLAN_EVIDENCE=docs/operations/task_2026-04-27_harness_debate/round2_verdict.md` BEFORE editing — otherwise the new pre-edit-architecture.sh hook will block the edit.
28042:+These follow-ups are MECHANICAL (set env var, update one int) and the executor knows about both per their boot §3 risk grid.
28060:+Scope: BATCH C per round2_verdict.md §4.1 #4 (HK HKO type-encoded antibody) — SettlementRoundingPolicy ABC + WMO_HalfUp + HKO_Truncation + settle_market dispatch + relationship tests + fatal_misreads.yaml row update + BATCH-B-CAVEAT operational follow-ups
28071:+- Existing surface (`assert_settlement_value`, `round_single`, `round_values`, `default_wu_fahrenheit`, `for_city`) verified intact.
28073:+- Self-test antibody (executor's own pre-edit-architecture.sh blocked their fatal_misreads.yaml edit) is empirical proof that BATCH B's hooks work in production.
28077:+1. **CAVEAT-C1 (predicate brittleness)**: `city_name == "Hong Kong"` is case- and whitespace-sensitive. Verified consistent with rest of codebase (`HKO_CITY_NAME = "Hong Kong"` in `daily_obs_append.py:268`; matching pattern in `observation_instants_v2_writer.py:230` and 5+ test files), so NOT a regression — but a future caller passing `"hong kong"` or `"HK"` will silently get the WRONG branch.
28080:+4. **CAVEAT-C4 (HIGH — arithmetic divergence on negative half-values)**: new `WMO_HalfUp.round_to_settlement()` uses `Decimal ROUND_HALF_UP` which differs from existing `np.floor(x + 0.5)` for negative half-values: `-3.5` → old=-3 / new=-4; `-0.5` → old=0 / new=-1. **For positive values (the Zeus universe today): arithmetic equivalent.** For cold-weather markets (negative °F): silent 1-degree divergence. New path is NOT called by any existing code today — so no live impact — but Tier 3 P8 unification MUST resolve this before swapping callers.
28084:+## Pre-review independent reproduction
28087:+$ git diff --stat HEAD -- src/contracts/settlement_semantics.py architecture/fatal_misreads.yaml \
28092:+ src/contracts/settlement_semantics.py      | 90 +++++++++++++++++++++++++++++-
28095:+$ ls -la tests/test_settlement_semantics.py
28098:+$ .venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py -q --no-header
28107:+$ .venv/bin/python -c "from src.contracts.settlement_semantics import SettlementRoundingPolicy; SettlementRoundingPolicy()"
28108:+TypeError: Can't instantiate abstract class SettlementRoundingPolicy without an implementation for abstract methods 'round_to_settlement', 'source_authority'
28111:+PASS. Both abstract methods (`round_to_settlement`, `source_authority`) flagged on instantiation attempt. `abc.ABC` + `@abstractmethod` decorators work as documented (settlement_semantics.py L199, L210, L214). Cannot construct a half-baked policy.
28116:+>>> w = WMO_HalfUp(); h = HKO_Truncation()
28120:+>>> w.source_authority(), h.source_authority()  # → ('WMO', 'HKO')
28131:+| `settle_market("Hong Kong", Decimal("28.7"), HKO_Truncation())` | 28 (truncation) | 28 | PASS (positive case) |
28134:+| `settle_market("Hong Kong", Decimal("28.7"), WMO_HalfUp())` | TypeError "Hong Kong.*require.*HKO_Truncation" | TypeError correct | PASS |
28135:+| `settle_market("New York", Decimal("74.5"), HKO_Truncation())` | TypeError "HKO_Truncation.*Hong Kong only" | TypeError correct | PASS |
28137:+| `settle_market("hong kong" lowercase, Decimal, HKO_Truncation())` | TypeError "HKO_Truncation valid for Hong Kong only" | TypeError raised; treated as NON-HK | **CAVEAT-C1** |
28138:+| `settle_market("HK" short form, Decimal, HKO_Truncation())` | TypeError | TypeError raised; only exact "Hong Kong" matches | **CAVEAT-C1** |
28139:+| `settle_market("Hong Kong " trailing space, Decimal, HKO_Truncation())` | TypeError | TypeError raised; whitespace-sensitive | **CAVEAT-C1** |
28141:+**CAVEAT-C1 (predicate brittleness)**: The HK predicate `city_name == "Hong Kong"` is exact-match string equality. Survey of existing codebase confirms this is the project-canonical form: `HKO_CITY_NAME = "Hong Kong"` at `src/data/daily_obs_append.py:268`; `if self.city == "Hong Kong":` at `src/data/observation_instants_v2_writer.py:230`; `city="Hong Kong"` in 5+ test files; `tier_for_city("Hong Kong")` test fixture. **The predicate matches existing convention; not a regression.** But future callers must canonicalize HK city names BEFORE passing to `settle_market`. Worth a defensive normalization (`city_name.strip()` + `city_name == "Hong Kong"`) for safety, but not blocking.
28148:+- `type_encoded_at: src/contracts/settlement_semantics.py:HKO_Truncation` added (L141) — defense-in-depth marker.
28152:+**CAVEAT-C2 (test-block coverage)**: 3 new relationship tests exist in `tests/test_settlement_semantics.py` but only 2 are registered in fatal_misreads.yaml `tests:` block. The 3rd test `test_invalid_policy_type_rejected` (which protects against duck-typed policy substitutes — a different attack surface than HK city/policy mismatch) is NOT cited. **Should be added** for completeness — it's the same load-bearing antibody type. Minor; tracked.
28158:+- L52: `TEST_FILES="tests/test_architecture_contracts.py tests/test_settlement_semantics.py"` — 2-file space-separated list.
28179:+**CAVEAT-C3 (env block weakens default state)**: The hook becomes session-default-permissive for the harness-debate scope. ANY architecture/** edit in this workspace (not just BATCH C+D) silently passes the gate as long as round2_verdict.md exists on disk. **This is a known trade-off of the OP-FOLLOWUP-2 design**: BATCH C+D don't self-block (good); but a future unrelated session in this workspace also won't be gated (bad if the work is unrelated to the harness-debate plan).
28185:+Per round-2 verdict §4.2 governance norm: harness mechanisms should not silently outlive their explicit authorization scope.
28193:+with pytest.raises(TypeError, match=r"Hong Kong.*require.*HKO_Truncation"):
28198:+# Test 2: NY + HKO → TypeError
28199:+with pytest.raises(TypeError, match=r"HKO_Truncation.*Hong Kong only"):
28209:+**Positive cases NOT explicitly tested in test file** (HK + HKO valid; NY + WMO valid). Independently verified by me via direct dispatch: HK+HKO+28.7 → 28; NY+WMO+74.5 → 75. The relationship tests focus on the negative case (Fitz "make category impossible") which is the load-bearing assertion. **Acceptable** — positive cases are implicit (would fail the existing test if rounding broke). Worth adding 1-2 explicit positive tests in Tier 2 for full coverage.
28216:+- L187-271: new code block (provenance comment + ABC + 2 subclasses + dispatch function) — pure addition.
28217:+- ZERO modifications to existing `SettlementSemantics` dataclass, `round_values`, `round_single`, `assert_settlement_value`, `default_wu_fahrenheit`, `default_wu_celsius`, `for_city`, or any helper.
28224:+sem.assert_settlement_value(74.5)        # → 75.0  CORRECT
28227:+K0 zone unperturbed. All existing callers (`src/calibration/store.py:98,171`; `src/execution/harvester.py:706`; `src/engine/replay.py:1215`+5; `src/engine/evaluator.py:1003,1219,1336`; `src/signal/ensemble_signal.py:217,316`) continue to invoke the OLD path unchanged.
28231:+Old path: `np.floor(x + 0.5)` (settlement_semantics.py:24, used throughout).
28232:+New path: `Decimal(str(x)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)` (settlement_semantics.py:224).
28253:+**Today's impact**: ZERO. The new `settle_market` is NOT called by any existing code path (verified via grep — only the 3 new tests invoke it). No live arithmetic divergence.
28256:+(a) Change `WMO_HalfUp.round_to_settlement` to use `np.floor(x + 0.5)` semantics (match legacy; "WMO half-up half-towards-positive-infinity" — actually IS the WMO definition per the existing `round_wmo_half_up_values` docstring at settlement_semantics.py:L17 "floor(x + 0.5)").
28257:+(b) Verify all settlement values in Zeus are POSITIVE (eg, all weather markets are in cities that don't go below 0°F or 0°C respectively) — provide proof in Tier 3 P8 boot evidence.
28259:+**Recommend (a)** — the existing `round_wmo_half_up_values` docstring explicitly defines WMO as `floor(x + 0.5)`, and the new path's "ROUND_HALF_UP" is a NumPy/Python semantic that doesn't match. Tier 3 P8 should fix `WMO_HalfUp.round_to_settlement` to use `np.floor(float(raw_temp_c) + 0.5)` or equivalent.
28261:+This is the strongest finding of BATCH C. Not blocking BATCH D (the new path isn't wired in production). MUST be on the Tier 3 P8 boot checklist.
28266:+- **BATCH B drift-checker → BATCH C edits**: drift-checker did not flag any new RED introduced by BATCH C. Independently re-ran `scripts/r3_drift_check.py --architecture-yaml`: still 4035 GREEN / 34 RED (same pre-existing entries). BATCH C did not introduce architecture/*.yaml citation drift.
28274:+I have written APPROVE-WITH-CAVEATS, not APPROVE. The 4 CAVEATs are real: C1 is benign (verified consistent with codebase) but worth tracking; C2 is minor coverage gap; C3 is a known trade-off in the OP-FOLLOWUP design with an actionable Tier 2 follow-up; C4 is the strongest finding — a 1-degree silent arithmetic divergence on negative half-values that MUST be reconciled before Tier 3 P8 swap.
28276:+I have NOT written "narrow scope self-validating" or "pattern proven" without test citation. I have engaged the strongest claim (BATCH C delivers a working type-encoded antibody per Fitz "make the category impossible") at face value before pivoting to the caveats. I have independently exercised every advertised behavior (3 positive + 3 negative + 3 case-sensitivity + ABC abstract instantiation + ClassVar binding + existing surface preservation + arithmetic equivalence trace).
28284:+| CAVEAT-C1 | LOW | `city_name == "Hong Kong"` is case/whitespace-sensitive; future callers must canonicalize | Add defensive `city_name.strip()` normalization OR document the canonical form in settle_market docstring; project-consistent behavior so non-blocking | Tier 2 |
28287:+| CAVEAT-C4 | **HIGH** (for Tier 3 P8) | `WMO_HalfUp.round_to_settlement` uses `Decimal ROUND_HALF_UP` which differs from existing `np.floor(x + 0.5)` for negative half-values (1° divergence at -3.5, -0.5, etc) | Reconcile semantics BEFORE Tier 3 P8 swap; recommend changing new path to `np.floor(float(raw_temp_c) + 0.5)` to match legacy WMO definition | Tier 3 P8 prerequisite |
28333:+## Pre-review independent reproduction
28340:+$ .venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py -q --no-header
28358:+**Honesty signal**: the executor's CITATION_REPAIR comments cite my batch_C_review file by name and cite the underlying source defect (verdict-grep-too-narrow) — not just "fixed it" but "this is why the prior call was wrong." Strong provenance discipline.
28389:+Live verification: 6 passed (in batched live run with all 15 BATCH D tests = 15/15 PASS).
28425:+  statement: No mixing of high and low rows in any Platt model, calibration pair set, bin lookup, or settlement identity.
28431:+  statement: No JSON export write before the corresponding DB commit returns.
28442:+- L4-12: comment block explaining the SIDECAR-1 change with provenance reference to BATCH B drift checker.
28444:+- L14: NEW `source_plan_descendent: docs/operations/zeus_topology_system_deep_evaluation_package_2026-04-24/repair_blueprints/p2_module_book_rehydration.md` (inheritor pointer per provenance discipline).
28448:+$ .venv/bin/python scripts/r3_drift_check.py --architecture-yaml --json | python -c "extract module_manifest RED + auth_rehydration RED"
28449:+Total RED count: 33  (was 34 in BATCH B)
28450:+module_manifest.yaml RED count: 0  (was 1 in BATCH B)
28451:+task_2026-04-23_authority_rehydration RED count: 0  (was 1 in BATCH B)
28454:+PASS. SIDECAR-1 successfully eliminated the 1 RED entry that BATCH B's drift checker surfaced. Net drift count drop: 34 → 33. The fix preserved provenance (executor used `source_plan: ARCHIVED` literal sentinel + descendent pointer rather than blanking the field, so future audits know WHY the original path is gone).
28457:+Executor noted "the original plan was archived/sunset without leaving a forwarding stub" — empirical observation from grep that the descendent path inherits the rehydration design but isn't an exact replica. This is a Fitz Constraint #4 signal: when data provenance is broken upstream, document the gap rather than paper over it.
28482:+This test loads the actual production `fold_lifecycle_phase` from `src/state/lifecycle_manager.py` and verifies a specific transition table. Note line "(active, settled)" — this is exactly INV-02's "Settlement is not exit" claim manifested as a state-table assertion: `settled` is reachable from multiple lifecycle states (active, day0_window, pending_exit), NOT exclusively from `pending_exit`. **Real relationship test**, not registration. PASS.
28490:+  - tests/test_dual_track_law_stubs.py::test_settlements_metric_identity_requires_non_null_and_unique_per_metric
28505:+This test loads `CANONICAL_POSITION_CURRENT_COLUMNS` from production code (`src/state/projection`) and asserts `temperature_metric` is present. INV-14 statement is: "Every temperature-market family row in canonical tables must carry temperature_metric, physical_quantity, observation_field, and data_version." Test asserts the first of these 4 fields. The other 3 fields are presumably tested elsewhere or by the SQL-declaration test (test 2 in the cited list).
28532:+| **HEAD post-BATCH-D** | **0/30 LARP-suspect (all have tests/schema/spec/script enforcement)** | **Verified by extended grep + spot-check** | Grep filename + class + docstring + production code reference |
28562:+- **BATCH B drift-checker → BATCH D + SIDECAR-1**: drift checker that I tested in BATCH B was the mechanism that surfaced the SIDECAR-1 RED. Tool-from-batch-B caught the data-defect-fixed-in-batch-D. Cross-batch tool-product coherence.
28563:+- **BATCH C type-encoded antibody → BATCH D YAML antibody**: BATCH C added a TypeError-based antibody (HKO+WMO mixing); BATCH D added test-citation-based antibody (INV-16/17 enforcement). Different antibody mechanisms (type vs test) — matches verdict §1.3 #4 "defense-in-depth (type + YAML) where type discipline is mixed."
28566:+- **Self-test antibody from BATCH C** (executor's own pre-edit-architecture.sh blocked their fatal_misreads.yaml edit) shows the BATCH B hooks worked in production. Combined with the BATCH C arithmetic divergence finding (CAVEAT-C4) and the BATCH D citation correction win, the executor's run includes 3 distinct moments where the harness CAUGHT something real, not just shipped clean.
28574:+- 33→33 RED count change verified via `--json` output filtering.
28589:+| B | APPROVE | C-B1 hardcoded baseline; C-B2 drift PATH_RE prefix; C-B3 SKILL forward-reference | All 4 vectors PASS; 5/5 RED audited pre-existing |
28630:+- `evidence/opponent/round2_proposal.md` §3.1-§3.4 (lines 121-296) — code templates for HK HKO ABC + 47-line SKILL.md + r3_drift_check.py extension diff
28638:+- `src/contracts/settlement_semantics.py` (182 lines, full)
28650:+| `scripts/r3_drift_check.py` | `docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py` | Script lives inside task r3/ subdir |
28651:+| `tests/test_settlement_semantics.py` | DOES NOT EXIST | Will need CREATE in BATCH C |
28654:+`tests/test_architecture_contracts.py` exists (3759 lines). `src/contracts/settlement_semantics.py` exists (182 lines).
28658:+### BATCH A — doc-only (lowest risk)
28694:+**C.1 SettlementRoundingPolicy ABC** (§4.1 #4) — Add to `src/contracts/settlement_semantics.py`. **Critical integration concern**: existing module already implements rounding via `RoundingRule = Literal["wmo_half_up", "floor", "ceil", "oracle_truncate"]` + `SettlementSemantics.round_values()` dispatch on string. Adding ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses creates a parallel structure. Two paths:
28695:+- **Append-only** (lower risk, opponent §3.1 verbatim): add ABC + 2 subclasses + `settle_market(market, raw_temp, policy)` function that asserts `isinstance(policy, HKO_Truncation)` for HK / `not HK` for non-HK. New code path; existing `assert_settlement_value` unchanged.
28696:+- **Replace** (higher risk, opponent's "make category impossible" full vision): refactor `SettlementSemantics.round_values()` to delegate to a `policy: SettlementRoundingPolicy` field, deprecate `rounding_rule` Literal. Touches every caller of `round_single`/`assert_settlement_value`.
28698:+Dispatch language ("add SettlementRoundingPolicy ABC + HKO_Truncation + WMO_HalfUp subclasses (~30-60 LOC)") matches **append-only**. **Will execute append-only**; document path-2 as future work in BATCH C closeout.
28700:+`fatal_misreads.yaml` HK row update: append `TYPE_ENCODED:src/contracts/settlement_semantics.py:HKO_Truncation` token to existing row's `proof_files` or `correction` block. Do NOT delete row (per dispatch).
28702:+Test: CREATE `tests/test_settlement_semantics.py` with `test_hko_policy_required_for_hk_city_raises_typeerror` + `test_wmo_policy_for_hk_city_raises_typeerror` + `test_hko_policy_for_non_hk_raises_typeerror`. Three relationship assertions.
28745:+4. **C.1 Append-only vs replace**: confirm append-only (parallel structure, lower risk) is OK, or do you want full integration with existing `RoundingRule` Literal?
28766:+Per anti-rubber-stamp rule (TOPIC.md L72), I must engage proponent's strongest argument before pivoting. Proponent's strongest is **Argument A** (`evidence/proponent/_boot_proponent.md:30-40`): the Z2 retro empirical case — 6 critic-caught defects in a single phase that "would have shipped silent-but-broken to live trading without the harness gate."
28772:+1. **The 6 catches are real.** Z2 retro (`docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/Z2_codex_2026-04-27_retro.md:21-67`) does document 6 specific defects caught before merge: compatibility-code-as-live, preflight-not-centralized, ACK-without-order-id, provenance-hash-over-mutated-fields, snapshot-freshness-without-time-semantics, 19 malformed slice-card YAML.
28774:+2. **At least one is a live-money loss vector at unbounded scale.** "Compatibility code is live code" leaving a V1-shaped bypass on a V2 cutover is a real catastrophic risk class.
28776:+3. **Some of these are NOT catchable by `pytest -q` alone.** Cross-module relationship bugs (Fitz Constraint #4: data provenance) require either (a) a critic-with-domain-knowledge, or (b) a relationship test that already exists. Both require some form of structured discipline beyond raw source-reading.
28827:+| Notification-summary-only messages | 56-60 | SendMessage delivery is asymmetric; disk-poll required |
28832:+1. The **rate of memory-entry production is itself a failure metric**. 7 entries in 1 day means the harness was operating at ~57% process-correctness. If the harness's own operator cannot run the harness without 7 process faults in 12 hours, the harness is the cognitive load.
28932:+1. **Some harness IS load-bearing.** Critic-opus dispatch, antibody contracts (NC-NEW-A..J), and per-phase boot evidence files are doing real work catching real bugs (Z2 retro). My position is "net-negative on the **whole surface**", not "net-negative on every individual mechanism." If the harness were 1/5 its current size — the critic gates + the 5-7 most-cited antibodies + a single AGENTS.md root + `SettlementSemantics.assert_settlement_value()` — it might be net-positive.
28934:+2. **The trading domain has irreducible complexity.** Zeus is real money in a real venue with real settlement mechanics. Some encoding of "settlement station ≠ airport station" must exist somewhere. The question is whether it must exist as 153 lines of YAML across multiple manifests, or whether it could be a single comment block in `src/contracts/settlement_semantics.py` plus a single relationship test.
29021:+- 6.7% pure-LARP rate is much smaller than my R1 "33% LARP" claim, but it is non-zero. The harness DOES contain prose-as-law in production.
29023:+This is itself an antibody-production moment: the audit reveals exactly which INVs to either back with a test/schema/semgrep, or delete.
29066:+Anthropic's Dec 2024 "Building effective agents" applies to chat agents and general-purpose tooling. Zeus is live-money trading — a different mode. Proponent is correct that "few lines of code" doesn't apply. **CONCEDED.**
29080:+Proponent's R1 §2 lays out three cases where the harness allegedly catches things 1M-context Opus 4.7 source-read cannot: HK HKO caution, V2 BUSTED-HIGH plan premises, Z2 6-catch. Engaging at face value, then pivoting.
29082:+### Case 1 — HK HKO caution (`fatal_misreads.yaml:118-134`)
29084:+**Conceded at face value**: Hong Kong HKO truncation differs from WMO half-up. This is a domain fact NOT derivable from `src/contracts/settlement_semantics.py`. An Opus 4.7 agent reading the source alone would silently mix.
29088:+A modern alternative: encode HK as a TYPE in `src/contracts/settlement_semantics.py`:
29090:+class HKO_Truncation(SettlementRoundingPolicy): ...
29093:+Mixing them becomes a TypeError. Per Fitz's own methodology (`/Users/leofitz/.claude/CLAUDE.md` "Make the category impossible, not just the instance" — "type system that makes the wrong code unwritable"), the type-encoded version is **strictly better** than the YAML antibody. The YAML lives at a distance from the code; the type lives where the bug would be written. No agent — Opus 4.7, Sonnet, or human — can write the bug.
29095:+This is not theoretical: `src/contracts/settlement_semantics.py` already exists (proponent boot row 17). Adding two subclasses costs 30 LOC and replaces the YAML antibody with an unconstructable error. **The harness's own structure preempts the harness's own justification for this case.**
29171:+1. **Z2 retro 6 catches are real and at least one is a live-money loss vector.** Compatibility-as-live-bypass is unbounded-cost.
29176:+6. **Domain facts (HK HKO truncation, settlement station ≠ airport station, Day0 ≠ historical hourly) cannot be derived from source-read alone.** They must be encoded somewhere; encoding cost is unavoidable.
29177:+7. **Anthropic's Dec 2024 "few lines of code" guidance does NOT apply directly to live-money trading mode.** Mode mismatch on that specific quote.
29183:+2. **At least 2 INVs (INV-16, INV-17) are pure prose-as-law in production.** Empirically grounded; not a framing issue. These should be deleted or backed with concrete enforcement.
29184:+3. **HK HKO case is better solved as a TYPE in `settlement_semantics.py` than as YAML antibody.** Per Fitz's own "make the category impossible" methodology. The harness's existing structure preempts this case's harness justification.
29187:+6. **The retrospective's 7 process failures + 12-hour debate are evidence the harness operator's bandwidth is the bottleneck.** Even if the harness produced these as immune-system response, the production rate (7/12h) shows operator-as-harness load is at saturation. Adding more harness surface continues a positive feedback loop.
29214:+Per TOPIC.md L80-82 if the judge finds net-negative, the winner must propose a subtraction list. Round-2 (alt-system proposal cycle) is where the specific subtraction list goes. For R2 close I commit only to: **pure-prose INV pruning (INV-16, INV-17 first); HK HKO encoded as type subclass of `SettlementRoundingPolicy` rather than YAML antibody; consolidate the 14-anti-drift-mechanism catalog and 7-class boot profile into a single ~100-line operating heuristic per Anthropic Jun 2025 "good heuristics rather than rigid rules"; retain critic + verifier + retro discipline + disk-first artifacts + memory + the load-bearing schema-backed INVs.**
29259:+| `workspace_map.md` (107 lines) | Routes to 17 manifest files, 5 visibility classes, ~10 directory routers. The map's own complexity exceeds many production systems' source code. |
29262:+| `architecture/fatal_misreads.yaml` (153 lines) | 3 antibodies. Conceptually sound, but enforcement is `topology_doctor.py --fatal-misreads --json` — same suspect topology_doctor that has 1,630 LOC. The "antibody" is itself a script call that requires the script to be alive and correct. |
29366:+Combined with §5.2's table that maps every catch (Z2 6-catch, V2 BUSTED-HIGH, HK HKO, all 5 semgrep INVs, all 12 schema INVs) to a specific retained mechanism.
29376:+Proponent's position aligns with 25 years of industry consensus that rewrite-from-scratch carries asymmetric risk and discards legacy bug-knowledge. Proponent's §5.2 catch-preservation map is the concrete instantiation of "the legacy bug-fixes" Spolsky names: every empirical catch is a "bug found in real-world usage" that the current harness encodes.
29382:+2. **Proponent's 85-90h with deterministic preservation is empirically more conservative than my 216h with stated risk.** Proponent's P3 (path-drift fixes) is already DONE per judge ledger; their P4 (HK HKO type encoding) is identical to my §3.1; their P5+P6 (mergers + generated registries) is a strict subset of my P5+P6+P7 (which includes total deletion not just merger). On every overlapping phase, in-place is cheaper.
29384:+3. **The catch-preservation map (§5.2) is honest accounting.** Each of the 6 Z2 catches, 5+8 V2 BUSTED-HIGH catches, HK HKO, 5 semgrep INVs, 12 schema INVs is mapped to a specific retained K1-K15 mechanism. Proponent did the work I challenged them to do.
29386:+4. **The "trading bias toward conservatism" argument has weight per verdict §1.7 LOCKED**: Anthropic's "few lines of code" guidance does NOT apply to live-money mode. By the same logic, **rapid surface-pruning bias does not apply to live-money mode either** — the asymmetry cuts BOTH ways. A missed catch is unbounded-cost; an over-large surface is bounded. Proponent is honest about which asymmetry dominates.
29449:+The strongest version of proponent's defense: each scoped router contains domain knowledge specific to the directory (e.g., `src/state/AGENTS.md` documents the 9-state lifecycle grammar; `src/contracts/AGENTS.md` documents settlement gates). Concession: **YES, those 5 of the 17 are load-bearing because they encode irreducible domain truth.** The OTHER 12 (`src/data/AGENTS.md`, `src/engine/AGENTS.md`, `tests/AGENTS.md`, `scripts/AGENTS.md`, `docs/authority/AGENTS.md`, `docs/operations/AGENTS.md`, `docs/reference/AGENTS.md`, `config/AGENTS.md`, `raw/AGENTS.md`, `architecture/AGENTS.md`, `src/calibration/AGENTS.md`, `src/risk_allocator/AGENTS.md`) are MIXED — some encode unique trading-domain knowledge; others duplicate root AGENTS.md content + module_manifest content.
29461:+The threat: my §3.1 HK HKO type encoding is CORRECT but UNTESTED in production. If the type encoding has a subtle gap (e.g., the `isinstance(policy, HKO_Truncation)` check is bypassed by a code path that constructs HKO objects from a string-typed configuration without going through the type registry), the HK HKO catch is LOST. Proponent's 17-line YAML antibody catches it via grep / lint / code review even when the type system is bypassed; my type system catches it ONLY at type-check time, which can be skipped if the construction path uses `Any` or `cast()`.
29481:+Per verdict §1.7 LOCKED: *"Anthropic's Dec 2024 'few lines of code' guidance does NOT apply directly to live-money trading mode."*
29483:+Live-money trading explicitly favors defense-in-depth: multiple independent mechanisms catching the same bug class. Zeus's current harness has, e.g., compatibility-as-live-bypass caught by:
29492:+The threat: in live-money trading, 5 layers > 3 layers IF the marginal layers are catching distinct bug categories. My §2 argues (d) and (e) are duplicating (a)+(b)+(c); proponent argues they're catching distinct categories.
29508:+My §2 proposes "5 per-package routers (src, tests, scripts, docs, architecture)". Per §3 W3 analysis above, **9-11 scoped routers is the empirically defensible floor**, not 5. The additional 4-6 routers (e.g., `src/state/AGENTS.md` for lifecycle grammar, `src/contracts/AGENTS.md` for settlement gates, `src/risk_allocator/AGENTS.md` for capital allocation, `src/strategy/AGENTS.md` for strategy_key governance) encode trading-domain knowledge that does NOT belong in module docstrings (would be too noisy in `__init__.py`).
29574:+1. **For proponent**: Spolsky's essay is the strongest single external citation in favor of in-place reform. The 30 INVs + 5 semgrep rules + 12 schema INVs + Z2 retro lessons are "weeks of real-world usage" encoded into the harness. Whole-replace risks "throwing away that knowledge."
29578:+3. **Honest middle**: Spolsky DOES caution that even re-encodings risk subtle bug-fix loss. The HK HKO type-encoding migration must be accompanied by a relationship test that explicitly reproduces the YAML antibody's enforcement. This is in my §8 acceptance criteria (AC-1: "All Z2-class regressions still detected by post-replace harness in simulated re-run") — Spolsky's principle is honored.
29597:+2. **Joel Spolsky 2000 + verdict §1.7 (live-money trading bias toward conservatism) supports gradualism over whole-replace as the OPERATIONAL strategy** even if the END-STATE is the same ~1,500-2,000 LOC asymptote per both proposals' §6.
29598:+3. **Defense-in-depth is a real principle in live-money trading.** Type-encoding alone is insufficient when type-discipline is mixed; ~140 LOC of `fatal_misreads.yaml` should be retained alongside type subclasses (per W3 T1 analysis).
29599:+4. **9-11 scoped AGENTS.md routers is the defensible floor**, not 5. Trading-domain knowledge (lifecycle, settlement gates, risk allocator, strategy_key governance, contracts) is irreducible.
29601:+6. **Migration sequencing (proponent's 8-phase 85h) is operationally safer than my 11-phase 216h** for live-money trading. The 6-month break-even is real; the live-money risk asymmetry is real.
29697:+1. The §1 LOCKED concessions show domain-encoded artifacts are necessary (HK HKO + V2 BUSTED-HIGH + Z2 6-catch).
29699:+3. Switching to a different shape costs migration overhead + retraining the operator + breaking working CI.
29705:+1. **Migration cost is real and load-bearing**. Whole-system replace means rewriting `topology_doctor.py`, recompiling `architecture/*.yaml` knowledge into types/code, retraining the operator's mental model, and re-validating CI. This is non-trivial and has a "kitchen sink session" failure mode (per the Anthropic Claude Code best practices URL §6 below).
29707:+2. **Working CI is precious**. 71 passing tests in `test_architecture_contracts.py` (per judge ledger §54) prove parts of the current harness ARE wired. Replacing them risks breaking known-good gates.
29760:+| `.claude/skills/settlement-rounding/SKILL.md` | ~50 LOC | replaces HK HKO YAML antibody (delegates to type system) |
29765:+| `src/contracts/settlement_semantics.py` (EXTEND existing) | +60 LOC (HKO_Truncation + WMO_HalfUp subclasses) | replaces HK HKO antibody YAML — type-checker enforces |
29778:+- **Preserved**: critic dispatch + verifier dispatch + antibody contracts + per-phase boot + disk-first + memory + all 5 verified semgrep rules + HK HKO domain knowledge + settlement gate + RED-cancel-sweep + lifecycle grammar + all 30 invariants (re-encoded as Python with same enforcement)
29786:+### §3.1 HK HKO as `SettlementRoundingPolicy` subclass code
29788:+Replaces `architecture/fatal_misreads.yaml` antibody for HK HKO + Hong Kong-specific routing prose.
29791:+# src/contracts/settlement_semantics.py (EXTEND)
29799:+    Replaces fatal_misreads.yaml HK HKO caution prose with unconstructable error.
29802:+    def round_to_settlement(self, raw_temp_c: Decimal) -> int: ...
29808:+    """WMO half-up: 74.45 → 74; 74.50 → 75. Used for WU/NOAA settlement chain."""
29809:+    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
29813:+class HKO_Truncation(SettlementRoundingPolicy):
29814:+    """HKO truncation: 74.99 → 74. Used for Hong Kong settlement chain ONLY."""
29815:+    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
29817:+    def source_authority(self) -> str: return "HKO"
29822:+    if market.city == "Hong Kong" and not isinstance(policy, HKO_Truncation):
29823:+        raise TypeError(f"HK markets require HKO_Truncation policy, got {type(policy).__name__}")
29824:+    if market.city != "Hong Kong" and isinstance(policy, HKO_Truncation):
29825:+        raise TypeError(f"HKO_Truncation only valid for HK; got city={market.city}")
29826:+    return policy.round_to_settlement(raw_temp)
29829:+**Verification**: Adding `assert isinstance(policy, SettlementRoundingPolicy)` at the existing `assert_settlement_value()` gate site means every settlement-touching code path is type-checked. The category of "wrong rounding for wrong city" is unconstructable. Replaces 17 lines of YAML antibody with 30 LOC of self-enforcing types per Fitz Constraint #1: "make the category impossible, not just the instance."
29831:+Time-to-implement: **~2 engineer-hours** (write code, add 3 pytest cases, delete YAML antibody, update `src/contracts/settlement_semantics.py` AGENTS.md if exists). Net LOC delta: **-(17 YAML antibody + ~50 prose around it) + 60 = effectively neutral, but BUG-CATEGORY-IMPOSSIBLE.**
29976:+| `assert_settlement_value()` gate at `src/contracts/settlement_semantics.py` | INV-02 / settlement law |
29977:+| RED → cancel + sweep behavior | INV-05 risk law |
30023:+| **P1 — type-encode HK HKO + 2-3 other YAML antibodies** | `SettlementRoundingPolicy` subclasses + 2 other obvious cases | **6 hr** |
30044:+- 100% of HK HKO + V2 BUSTED-HIGH + Z2 6-catch CAUSAL CHAIN (the mechanisms that produced these catches survive)
30075:+I do NOT think it is asymptotic in the sense of "harness goes to zero." Some encoding-as-artifact will always survive thermodynamic translation loss (per Fitz Constraint #2 + Anthropic Sept 2025 "context windows become insufficient"). The load-bearing core in §4 — critic + verifier + antibody contracts + per-phase boot + disk-first + memory — is a property of the WORK (live-money trading with cross-session multi-agent operation), not a property of the model. No future model frees you from "the agent should ask another agent to verify before the user is exposed to the answer." That's a process invariant, not a capability gap.
30081:+1. **Hooks become richer** (Claude Code auto-classifier-mode-equivalents for trading-specific risks): planning-lock hook becomes just `claude --permission-mode auto` with custom classifier. Removes another ~30 LOC.
30089:+- Domain knowledge (HK HKO, settlement station identity) MUST be encoded somewhere. Type system in source code is the floor — cannot go below "type that the wrong action requires."
30092:+- One operator-decision register for live-money cutover gates is a regulatory + accountability invariant, not a model capability gap.
30097:+- Type encoding of domain (HK, settlement gate, lifecycle): ~500 LOC. Cannot compress below this; it IS the code.
30104:+**Asymptote estimate at GPT 6 / Opus 5 generation: ~1,800-2,000 LOC.** That is the bottom for live-money trading. Below this, the harness stops being a harness and becomes "code-with-comments" — which is fine, that is the natural endpoint.
30138:+The proponent will counter that Zeus is not "many tasks", it is live-money trading. Conceded. The proposed minimal harness in §2 is ~2,800 LOC — STILL 3-15× larger than Aider's dynamic budget, calibrated for the trading-domain encoding overhead. The principle holds: dynamic + minimal beats static + maximal at this model generation.
30169:+- **Skills for on-demand domain knowledge** → §2 `.claude/skills/zeus-domain/`, `settlement-rounding/`, `zeus-phase-discipline/`
30180:+If judge accepts this proposal as round-2 winner, these are the binding deliverables:
30187:+| AC-4 | HK HKO mixing produces TypeError, not silent settlement error | category-impossible per Fitz #1 |
30202:+2. **Zone reorganization (P5 — 36 scoped AGENTS.md cull) carries risk of losing mid-tier domain knowledge currently encoded in routers.** Mitigation: walk each router and either fold into module docstring or merge to root; do not bulk-delete.
30204:+4. **`topology_doctor.py` → `topology_navigator.py` rewrite (P8, 40h) is the highest-risk phase.** Existing planning-lock and map-maintenance facades have CI-test consumers. Migration must preserve the public CLI signature for one cycle.
30209:+1. **Type-encoded antibodies are strictly better than YAML antibodies** for cases where they apply. HK HKO, Day0 vs hourly, settlement station ≠ airport station are all type-encodable. The harness has the mechanism (`src/contracts/`); the proposal extends it.
30219:+- [x] Honored verdict §6.2 commits in CONCRETE form (§3.1 HK HKO Python, §3.2 47-line skill, §3.3 audit principle script, §3.4 drift-checker extension diff)
30274:+3. **Money Path causality (proponent Reason C) is correct.** Edge work sits MID-pipeline (calibration → execution → monitoring); upstream corruption (e.g., INV-15 "forecast rows lacking canonical cycle identity" pure-prose) propagates downstream into CALIBRATION_HARDENING. Optimization against bad signal IS worse than no optimization.
30282:+**Pivot-A — Knight Capital is the WRONG analogy.** Per my NEW WebFetch (Wikipedia Knight Capital Group, §5 below): the 2012 incident was caused by *"a technician forgot to copy the new Retail Liquidity Program (RLP) code to one of the eight SMARS computer servers"* + *"old Power Peg code still present on that server"*. **Root cause: deployment omission + dormant production code that should have been deleted**, NOT missing observability/harness infrastructure. This is a **production deployment hygiene failure**, not a development-time harness failure. Zeus's analog of Knight Capital risk is `src/main.py` daemon deployment + `state/` runtime — NOT `architecture/*.yaml` manifests. **Proponent's Counter-1 conflates two distinct failure classes.** The right Knight Capital antibody is deployment validation + dead-code-removal CI gate, both of which DEEP_PLAN already covers in Tier 1 (drift-checker + planning-lock hooks). It is not 110-150h of YAML pruning.
30286:+**Pivot-C — Proponent's Reason B (Z2-class on un-pruned harness) double-counts the Tier 1 substrate.** Per round-2 verdict §1.1 LOCKED #1-12, Tier 1 LANDS the load-bearing core (critic + verifier + antibody contracts + per-phase boot + disk-first + memory + HK HKO type + hooks). Z2's 6-catch was attributed to **critic + verifier + tests + YAML closeout parser** (4 mechanisms — round-2 §0.3 smoking gun). All 4 of those are IN Tier 1. The Z2-class catch capability is at full strength immediately after Tier 1; Tier 2/3 work removes routers and merges YAML without adding NEW catch capability. Proponent's "EDGE on un-pruned harness is more dangerous" claim is true ONLY if Tier 2/3 work is necessary FOR catches — but it isn't. It's necessary for operator cognitive load only.
30298:+The honest delta: **proponent delays CALIBRATION_HARDENING by ~12-16 weeks vs my plan**. CALIBRATION_HARDENING is the Platt-model bottleneck (DEEP_PLAN §7.2 framing). 12-16 weeks of running on un-hardened calibration is 12-16 weeks of mis-sized positions in live trading. **The opportunity cost of that delay is unbounded; it is not balanced against the bounded operator-attention savings.**
30302:+Proponent §2 Reason A claims 5 EDGE packets add 0.5h/month operator-tax × 12-24 months = 30-60h. **No empirical basis cited.** The Renaissance Wikipedia source I cited in round-3 phase-1 (150 staff, half PhDs) shows research-leaning hiring at scale — but at single-operator scale, the additional 0.5h/month claim is fabricated. Counter-data point: Zeus has been running with the current ~25K LOC harness for months; the 7 process-faults / 12h were specifically attributable to the R3 multi-region parallel debate apparatus (verdict §1.9 LOCKED — operator already retired this), NOT to general harness usage. Proponent's per-EDGE-packet tax claim has no calibration data.
30306:+### Weakness W3 — Knight/Flash Crash analogy conflates production trading bug with development-time harness debt
30308:+Per my NEW WebFetch §5: Knight Capital's failure was a deployment + dead-code-removal failure. Zeus's analogous risk surface is `src/main.py` + `state/` + deployment scripts + cron jobs — NOT `architecture/*.yaml` manifests. Tier 2/3 pruning of YAML manifests does NOT address Knight-class risks; those risks are addressed by:
30313:+**ALL of these are in Tier 1, which both sides agree finishes first regardless of allocation.** Tier 2/3 work — which is the contested allocation — does NOT address Knight Capital's failure class. Proponent uses the Knight citation rhetorically to motivate work that doesn't actually defend against the cited risk.
30315:+Same critique on Flash Crash: the 2010 event required CIRCUIT BREAKERS + CME Stop Logic — runtime safety mechanisms in the trading engine, NOT YAML manifests in `architecture/`. Zeus's analogous defenses are RED-cancel-sweep (INV-05) + risk_level enum (already in source code) + RiskGuard daemon. Tier 2/3 pruning doesn't add or harden any of these.
30325:+**Concession**: there exists a SUBSET of Tier 2/3 work that is **substrate-relevant for specific EDGE packets**. Specifically: any Tier 2/3 work that tightens calibration-input-validity (INV-09, INV-15) IS a precondition for CALIBRATION_HARDENING. Equivalent work for ATTRIBUTION_DRIFT (strategy_key audit, INV-04 enforcement) IS a precondition for that packet.
30399:+**Application**: Knight Capital's failure was **deployment omission + dormant production code that was not deleted** — NOT missing harness/observability infrastructure. The right antibody class is:
30406:+Stronger version: the actual Zeus-side analog of Knight risk lives in `src/state/chain_reconciliation.py` (verifying chain truth vs local cache) and `src/execution/*` deployment paths. Auditing those is ~5-10h of work and IS already covered by Tier 1 hook setup. Proponent's 40/60 puts 90-130h into work that does not address the cited risk.
30430:+| Weeks 13-20 | CALIBRATION_HARDENING (HIGH risk) + INV-15/INV-09 precondition | 45% harness / 55% edge | 55% | Higher harness % during HIGH-risk packet |
30440:+- **My 70/30 was too aggressive on edge-leaning** (failed to account for per-EDGE substrate work + INV-15/INV-09 calibration precondition + Headlands operations co-equal framing).
30490:+Proponent will argue: complete the gradualist harness migration BEFORE starting edge work because (a) live-money operational safety is precondition; (b) Spolsky-2000 conservatism + verdict §1.7 trading-bias-toward-conservatism LOCKED both apply; (c) building edge on an unstable substrate compounds risk; (d) the DEEP_PLAN.md plan is already in flight (executor running Tier 1 batches A-D per task list); pivoting mid-flight is costly. They will cite §6.4 verdict-deferred items and the §1 LOCKED catch-asymmetry (missed catch = unbounded; over-large surface = bounded).
30494:+1. **Live-money safety is precondition.** No edge packet should ship until Tier 1 (executor batches A-D, ~14-20h) is COMPLETE and the 71-pass `tests/test_architecture_contracts.py` baseline holds. Tier 1 catches the worst LARP (INV-16/17) + lands the type-encoded HK HKO + adds the deterministic hooks. **Tier 1 is non-negotiable; it must finish before any edge work.**
30496:+2. **Verdict §1.7 LOCKED stands**: Anthropic Dec 2024 "few lines of code" doesn't apply to live-money mode. The conservative bias is correct in spirit. **I am NOT arguing "ship edge with no harness"; I am arguing "Tier 1 done → pivot heavy to edge → Tier 2+3 continue at lower-priority background cadence."**
30500:+4. **Substrate stability matters.** Building edge on a broken settlement gate is unbounded-cost. **But Tier 1 lands the load-bearing core**: critic-opus + verifier dispatches + antibody contracts + per-phase boot + disk-first + memory + HK HKO type + hooks (per round-2 verdict §1.1 12 LOCKED items). The substrate AFTER Tier 1 is sufficient for edge work; Tier 2+3 are polish, not foundation.
30504:+**Pivot-A — diminishing returns curve**: round-2 verdict §1.2 LOCKED that **the load-bearing core is ~20-30% of current surface** and both sides converged at **~5,000-6,000 LOC short-term + ~1,500-2,000 LOC at 24-month asymptote**. After Tier 1 (executor), the harness lands at ~10-12K LOC — already past the steepest part of the diminishing-returns curve. The Tier 2+3 work delivers an additional ~5,000 LOC reduction over 80-110h. **That's ~50 LOC reduced per engineer-hour**. The marginal hour spent on Tier 2+3 buys a 50-LOC reduction; the marginal hour spent on EDGE_OBSERVATION buys live-trading P&L visibility. The asymmetry is unambiguous.
30521:+| CALIBRATION_HARDENING | ~1 calibration improvement / ~1 Platt regression test | DIRECT (calibrated probabilities are the conversion from forecast to bet sizing) | Bottleneck per DEEP_PLAN.md §7.2 |
30544:+Per round-2 verdict §1.2 LOCKED defense-in-depth principle in live-money trading. By symmetry: **edge packets have asymmetric UPSIDE** (one good catch in CALIBRATION_HARDENING can recover months of mis-sized positions; one good ATTRIBUTION_DRIFT alert can prevent a strategy from running on dead alpha for weeks). Harness pruning has **bounded UPSIDE** (50 LOC reduced is 50 LOC, period). The defense-in-depth principle that justifies retaining `fatal_misreads.yaml` (round-2 W3 T1) ALSO justifies prioritizing the edge packets that produce defense-in-depth on the trading P&L side. **Symmetric application of the locked principle.**
30554:+2. **HK HKO type-encoding + hook setup + `fatal_misreads.yaml` retention** are the substrate-stability minimum. If executor batch C (HK HKO) fails or batch B (hooks) introduces regression, edge work waits.
30564:+1. **Harness post-Tier-1 IS sufficient substrate for edge work.** The load-bearing core is in place; Tier 2+3 is polish. Defense-in-depth is satisfied with critic + verifier + antibodies + hooks + memory + HK HKO type + remaining 28 INVs + 5 semgrep rules. Polish does not unlock new substrate; it reduces operator cognitive load by another increment.
30616:+**Application**: Graham's framework directly applies to Zeus's allocation problem. The "users" of Zeus are the trading market itself + the operator's P&L. EDGE_OBSERVATION puts something "in front of users" (= in front of the live market for measurement); harness pruning is the "automate the bottlenecks" step that comes AFTER the bottlenecks are identified. Graham's contrapositive: **automating something before it is the bottleneck IS the procrastination he warns against.** Zeus's bottleneck post-Tier-1 is NOT yaml LOC; it is unmeasured strategy P&L + uncalibrated probability + undetected alpha decay. Edge packets address the actual bottleneck; harness Tier 2+3 polish does not.
30628:+| Week 0-1 | Tier 1 finishing | **100%** harness | **0%** edge | Tier 1 batches A-D non-negotiable; HK HKO + hooks + INV-16/17 deletion + critic/verifier/safety-gate subagents must complete |
30758:+### Case 1 — `fatal_misreads.yaml:118-134` (Hong Kong HKO caution)
30760:+> "Hong Kong is an explicit current caution path. Current truth must route through fresh audit evidence and HKO-specific proof before changing source, settlement, hourly, or monitoring behavior."
30762:+This is NOT in any source file, function signature, or git commit message. It is a domain fact: Hong Kong's HKO truncation differs from WMO half-up rounding for settlement. An Opus 4.7 agent reading `src/contracts/settlement_semantics.py` cannot derive this — the file says how to round, not that HKO is a special case. Without the manifest, the agent silently mixes truncation with half-up. Live-trading consequence: systematic settlement mispricing for HK markets.
30772:+Six implementation defects caught by critic+verifier in ONE phase: (1) compatibility code as live bypass; (2) preflight not centralized; (3) ACK without order id; (4) provenance hash over post-mutation fields; (5) snapshot freshness without time semantics; (6) 19 malformed slice-card YAML. All six would have shipped to live without the gate. The "no-harness baseline" Opus 4.7 agent has no critic-opus gate in its loop unless the harness invokes one.
30790:+- "**As production agents handle more complex tasks and generate more tool results, they often exhaust their effective context windows**" — Sept 2025 statement; this post-dates Opus 4 launch and is about the same model generation as Opus 4.7.
30801:+**My reading**: Zeus is exactly the case where simpler solutions fall short. Live financial trading + lifecycle state machine + multi-source settlement + cross-session multi-agent operation is NOT the "few lines of code" regime. The Dec 2024 post explicitly carves out: "**Agentic systems often trade latency and cost for better task performance.**" Zeus accepts that trade-off because settlement errors are unbounded-cost; one INV-21 violation (Kelly without distribution) silently undersizes a position by 50%+ for weeks. The Dec 2024 advice is calibrated to "many applications" — not to "a live-money quantitative trading system with 30 invariants and 4 strategy families."
30815:+**What I do NOT concede**: that the catch-rate benefit (Z2 retro 6/6, V2 plan 5+8 BUSTED-HIGH, fatal_misreads HK Caution etc.) is replaced by 1M-context Opus 4.7 reading source directly. Anthropic's own Sept 2025 + Jun 2025 publications explicitly say context windows are insufficient at production scale and that disk-first multi-agent + curated context IS the answer. Zeus is a particular instance of that general pattern; the harness can be PRUNED but not REPLACED by long context.
30835:+  - `architecture/fatal_misreads.yaml` lines 118-134 verified (Hong Kong HKO caution).
30876:+**Counter-A: Cognition's full caveat list, NOT the headline quote.** Verbatim from same Cognition post (R2 WebFetch §3 below): *"In 2024, many models were really bad at editing code"* and *"these systems would still be very faulty"* and *"today, the edit decision-making and applying are more often done by a single model in one action."* Cognition's argument is **about the file-editing inner loop**. Zeus's harness is largely about **safety gates + provenance + cross-session memory + invariant law** — Cognition's post explicitly does NOT cover these. Cognition also concedes: *"not always possible due to limited context windows and practical tradeoffs"* and *"the subtask agent is usually only tasked with answering a question"* — exactly the pattern Zeus uses for its critic + verifier dispatches (single-question subagents, not parallel collaborators).
30901:+| INV-15 | Forecast rows lacking canonical cycle identity may serve runtime degrade paths but must not enter canonical training. | `schema:` (drifted path) | **WEAKEST** — schema path drifted; no other backstop. |
30903:+| INV-17 | DB authority writes (event append + projection fold) must COMMIT before any derived JSON export is updated. | `negative_constraints: [NC-13]` | **PARTIAL** — NC-13 exists; relies on transitive enforcement. |
30936:+- **Frozen-interface docs**: agreed, they drift vs source. The `IMPLEMENTATION_PROTOCOL.md` §5 prescribes that downstream phases READ THE DOC NOT THE SOURCE — which means even when source moves, downstream consumers are stable. This is exactly the API-versioning pattern that every production library uses. Calling it "drift" is the wrong frame; it's **deliberate slow-changing interface**.
30951:+- The 5+8 BUSTED-HIGH plan premises in V2 plan would have shipped (RETROSPECTIVE lines 7-12). Failure cost: live-money loss vector.
30992:+The opponent's R1 §3 Source 1 invoked Anthropic's "minimal scaffolding" guidance to argue Zeus is overbuilt. Cursor is Anthropic's largest-volume customer for Claude API (Cursor is built on Claude). Cursor's PRODUCTION pattern is heavily structured-rules-based — exactly the opposite direction from "minimal scaffolding." When the largest production deployer of Claude diverges from the model vendor's general-purpose advice, the divergence is the data. Zeus is in the same regime as Cursor (production-grade, real-stake, multi-step), not in the regime of "many applications" Anthropic was advising in their Dec 2024 post.
31017:+5. **Cursor's Dec 2025 production architecture uses root + directory-scoped rules + structured workflow + approval gates** — exactly Zeus's pattern. Opponent's "no industry deployer uses this" thesis is empirically falsified.
31018:+6. **The HK HKO truncation rule + V2 plan BUSTED-HIGH catches + Z2 6-catch are not derivable** from 1M-context source-read. They require domain-encoded artifacts that survive cross-session compaction.
31064:+Role: proponent-harness (defends harness as net-positive ROI for live-trading correctness on Opus 4.7 / GPT 5.5)
31076:+| 3 | `AGENTS.md` (335 lines) | Three-layer authority: money path → topology digest → scoped routers. Hard rules: settlement gated by `SettlementSemantics.assert_settlement_value()`, RED→cancel+sweep, advisory-only risk forbidden (INV-05). The harness IS the encoding of these rules into machine-checkable form. |
31079:+| 6 | `architecture/fatal_misreads.yaml` (153 lines) | 7 documented "false equivalences" (e.g. `api_returns_data == settlement_correct_source`, `airport_station == city_settlement_station`). These are exactly the cross-module relationship bugs that Fitz Constraint #4 (data provenance) names — they are NOT discoverable by grep or by reading function signatures, only by knowing the domain. |
31081:+| 8 | `architecture/task_boot_profiles.yaml` (360 lines, sampled) | 7 task classes (source_routing, settlement_semantics, hourly_observation_ingest, day0_monitoring, calibration, docs_authority, graph_review) each with `required_proofs` (question + evidence). Question-first boot — agent must ANSWER before code. |
31085:+| 12 | `r3/learnings/Z2_codex_2026-04-27_retro.md` (88 lines) | Concrete win: critic-opus caught (a) compatibility code is live code, (b) preflight must be centralized not assumed, (c) ACK requires order id, (d) provenance hash over final fields, (e) snapshot freshness needs time semantics, (f) YAML is code (19 malformed slice-cards). All 6 caught BEFORE merge by harness gates. Without harness, all 6 would have shipped. |
31094:+Z2 retro (`r3/learnings/Z2_codex_2026-04-27_retro.md:21-67`) lists 6 critic-caught implementation defects in a SINGLE phase, all of which would have shipped silent-but-broken to live trading without the harness gate:
31095:+- Compatibility code is live code (would have left a V1-shaped live bypass)
31102:+Each of these is a Fitz #4 (data provenance) failure mode — the code is "correct" but the relationship between modules is broken. None are catchable by `pytest -q` alone; they require the critic-opus + verifier + topology gate stack. **Anchor**: Z2_retro lines 21-67. Net dollars saved: at least one (compat-bypass) is a live-money loss vector at unbounded scale.
31108:+- `architecture/code_review_graph_protocol.yaml:51-58` — explicit `forbidden_uses:` list (settlement truth, source validity, current fact freshness, authority rank, planning lock waiver, receipt or manifest waiver). Grep proves these are wired into `topology_doctor.py` (1630 lines per TOPIC.md row).
31111:+The opponent will likely cite "769 .md files" and "29 yaml manifests" as bloat. The pro response: the manifests carry the 30+ invariants that NO long-context model can synthesize, because they encode TRADING-DOMAIN truths (settlement station ≠ airport station, Day0 source ≠ historical hourly source) that exist NOWHERE in the source code or git history.
31118:+- `architecture/fatal_misreads.yaml:31-153` — 7 misreads each with `proof_files`, `invalidation_condition`, `tests`, `task_classes`. Without this manifest, every fresh agent would re-discover (via failure) that `api_returns_data != settlement_correct_source`. The manifest converts a thermodynamic loss into a one-time encoding.
31120:+- `feedback_on_chain_eth_call_for_token_identity` (memory) was DERIVED from this debate process and is now permanent cross-session knowledge. The harness is the antibody-production line.
31144:+3. The harness specifically encodes things that DON'T exist in the source code: data provenance (which station settles which city/date), invariants over relationships (Day0 source ≠ historical hourly source), and forbidden semantic equivalences. No amount of source-code reading produces these.
31171:+**Why load-bearing**: Industry leaders running long-context agents in production will document their OWN harness layers. If Devin/Cursor/Replit Agent run task-routing + invariant-checking + per-task scoped context curation, that is direct industry parallel to Zeus harness. The opponent will struggle to argue Zeus is unique in needing this.
31206:+**Convergence statement**: Opponent and I are closer than the round-1 verdict suggested. Both target ~1,800-2,000 LOC asymptote at GPT-6/Opus-5 generation (their §6 numerical estimate; my §6.3). The real disagreements are: **end-state size today (their 2,800 LOC vs my 5,500 LOC) + migration cost (their 216h vs my 85-90h) + risk profile (their 11-phase whole-replace vs my 8-phase incremental)**.
31240:+1. **"Ruthlessly prune" is a HEURISTIC, not a fixed-LOC ceiling.** The page does not say "≤500 LOC" or "≤5 routers" or "22% of current". It says "would removing this cause Claude to make mistakes?" Applied honestly, the answer for many of the 36 routers opponent wants to delete is YES — `src/state/AGENTS.md` (canonical write path) + `src/contracts/AGENTS.md` (settlement law) + `src/risk_allocator/AGENTS.md` (R3 A2 cap policy) carry trading-domain rules whose absence WOULD cause mistakes. Opponent's blanket "5 routers" is ruthless to the point of imprudence.
31242:+2. **The page is calibrated to chat agent + general coding, NOT live-money trading mode.** Same caveat as Anthropic Dec 2024 "few lines of code" guidance — verdict §1.7 LOCKED that mode-mismatch applies. Anthropic's "ruthlessly prune" is correct in spirit; Zeus's threshold for "would removing cause mistakes" is lower than a typical project because every silent miss is unbounded-cost.
31246:+**Net of §0**: opponent's Anthropic citation is genuinely strong. I update my position MOSTLY toward theirs on hooks + skills + native subagents (~3 of 5 conceded substantively). I hold on the bare router count + the "ruthlessly prune" specific threshold for a live-money codebase.
31258:+- Z2-class catches DURING the migration window are at HIGHER risk of being missed because the migration touches the very mechanisms (topology_doctor, invariants.yaml, source_rationale.yaml) that catch them.
31262:+**Concrete hit**: opponent's break-even claim ("after ~10-15 future agent sessions ... within 2-4 weeks of completion") assumes 30-min savings per session. That assumes the new harness is FUNCTIONALLY EQUIVALENT to the old on the catch axis. If the new harness misses ONE Z2-class catch during P0-P11 or in the first month after, the migration is net-negative immediately — because verdict §1.2 #1 LOCKED that "compatibility-as-live-bypass on V2 cutover" is unbounded-cost. 216h is a LOT to bet on the migration being bug-free.
31264:+My in-place 85-90h proposal has the same risk per-phase but distributed over 8 small-batch phases each with rollback (planning-lock receipt + verifier dispatch). Opponent's whole-replace concentrates the risk into one window.
31270:+The proposal does NOT include a working code sample for the @enforced_by decorator. Opponent §3.1 shows `SettlementRoundingPolicy` Python — that is type-encoding for ONE invariant (HK HKO). The OTHER 29 invariants involve heterogeneous enforcement: tests, semgrep rules, schema migrations, scripts, negative_constraints. The decorator pattern would need to handle:
31279:+**Concrete hit**: my round-2 §K8+K9 retains `architecture/invariants.yaml` (28 INVs after pruning) and `architecture/ast_rules/semgrep_zeus.yml` because they ALREADY work — `tests/test_architecture_contracts.py` 71-pass per judge ledger §54, all 5 semgrep rules verified present. Opponent's P2 is rebuilding a working subsystem to gain ... what? The same enforcement, expressed in Python instead of YAML. The catch-rate doesn't change. The migration risk does.
31281:+### Weakness 3 — P5 (36 scoped AGENTS.md cull) admits irreversibility and risk in §9
31283:+Opponent's own §9 NEW concession 2: *"Zone reorganization (P5 — 36 scoped AGENTS.md cull) carries risk of losing mid-tier domain knowledge currently encoded in routers. Mitigation: walk each router and either fold into module docstring or merge to root; do not bulk-delete."*
31287:+**These are the opponent's own admissions** that two of the largest line-items (P5 20h + P6 16h = 36h, ~17% of total cost) carry KNOWLEDGE-LOSS RISK and IRREVERSIBILITY risk. The "mitigation" is "walk each router and decide" — which is what my round-2 §M9 already does, just with a less aggressive cull (24 routers, not 36). The mitigation collapses the difference between our proposals on this dimension.
31338:+| 41 scoped AGENTS.md routers | Cull to 17 active-touched (M9) | Cull to 5 per-package (src, tests, scripts, docs, architecture) | **Hold partial**: 5 is too aggressive for live-money codebase per §0 weakness 1; opponent's own §9 mitigation walks each. **Updated target: 8-12 routers** (root + per-active-package + critical-domain like src/state, src/contracts, src/risk_allocator, src/strategy, src/execution). |
31341:+| HK HKO + 2-3 other antibodies | Type-encode HKO_Truncation/WMO_HalfUp (P4) | Same — `SettlementRoundingPolicy` ABC + subclasses | **Concede full**: opponent's §3.1 code is exactly right. |
31356:+**Net**: my updated position lands at ~4,000 LOC YAML / 8-12 routers / 400 LOC topology / native subagents+skills+hooks. Opponent's lands at 2,800 LOC / 5 routers / 300 LOC topology. **Real remaining gap: ~1,200 LOC YAML + 3-7 routers + 100 LOC script.** The architectural philosophy is converged; the remaining gap is risk-tolerance (mine more conservative on router count + invariants format).
31364:+My weakness 2 above: opponent's @enforced_by decorator is unproven and the migration cost is underestimated. The current YAML + tests/test_architecture_contracts.py 71-pass setup WORKS. Migration risk > marginal benefit. **Hold YAML for INVs until @enforced_by has a working prototype with measurable enforcement strength.**
31370:+Per §0 hold 1 + Anthropic's own pattern (child CLAUDE.md "pulled in on demand"). Live-money trading codebase needs scoped routers for: `src/state/`, `src/contracts/`, `src/risk_allocator/`, `src/strategy/`, `src/execution/`, `src/engine/`, `tests/`, `scripts/`, `architecture/`, `docs/operations/`, plus root + 1-2 others. The "would removing this cause Claude to make mistakes?" test, applied honestly, returns YES for canonical-write-path / settlement-law / risk-cap-policy routers. Opponent's 5 deletes domain rules whose absence WOULD cause mistakes.
31374:+My 8-phase 85-90h vs opponent's 11-phase 216h. **The right answer depends on operator risk tolerance, not on debate-side correctness.**
31376:+If operator can dedicate 4-6 weeks to a planned migration with parallel testing on 3 live phases (opponent's P11), the 216h whole-replace yields a cleaner end state.
31378:+If operator wants migration distributed across normal feature work (each phase as its own packet under planning-lock + critic gate), my 85-90h yields lower per-window risk + retains rollback at each phase.
31380:+**This is genuinely operator-decision territory, not debate-resolvable.** I HOLD my proposal as the lower-risk option without claiming it dominates.
31408:+> "**Try something risky. If it doesn't work, rewind and try a different approach. Checkpoints persist across sessions, so you can close your terminal and still rewind later.**"
31416:+2. *"Try something risky. If it doesn't work, rewind"* — argues for REVERSIBLE migration steps. My §1+§2+§3 are individually reversible (delete an INV, restore from git; merge two YAMLs, split back). Opponent's P6 (`source_rationale.yaml` → inline docstrings) is per their own §9 admission "irreversible". The Anthropic-recommended pattern is reversible-with-checkpoint, not irreversible-and-validate-after.
31440:+A4. **`SettlementRoundingPolicy` ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses** for HK HKO antibody (matches my §P4 but using opponent's exact code from their §3.1). Net: +60 LOC code / -17 LOC YAML antibody / category-impossible.
31468:+| HK HKO encoding | YAML antibody | type subclass | **type subclass (opponent §3.1 code)** | type subclass | ✓ |
31473:+**End-state delta from opponent**: ~1,200 LOC YAML + 3-7 routers + 100 LOC topology_doctor. Real architectural philosophy: CONVERGED. Real disagreement: risk-tolerance + INV format + history-archive policy.
31481:+**FINAL POSITION: PARTIAL ACCEPT** of A1-A6 (hooks, native agents, native skills, type-encoded HK HKO, drift-checker extension, deeper-prune topology/protocol/boot-profiles). HOLD on H1-H4 (INV format, router count, migration phasing, history archive policy).
31491:+**The judge's job at round-2 close**: weigh ~1,200 LOC YAML conservatism vs whole-replace one-shot cleanliness; weigh 95h vs 216h migration cost; weigh 8-12 routers vs 5 routers risk profile. The architectural philosophy is shared; the disagreement is bounded.
31504:+- 3 concrete weaknesses in opponent's plan documented (§1: 216h cost vs simulated-regression risk, P2 unproven decorator prototype, P5/P6 self-admitted irreversibility/knowledge-loss).
31533:+2. **Type-encoding > YAML antibody where possible.** HK HKO as `HKO_Truncation` subclass of `SettlementRoundingPolicy` IS strictly better than 17 lines of `fatal_misreads.yaml`. Per Fitz's own "make the category impossible" methodology. No agent — Opus 4.7, Sonnet, human — can write the bug.
31557:+| K6 | cross-session memory (`MEMORY.md` + 42 `feedback_*` entries) | ~200-line index + ~42 short files | verdict §1.8 (cross-session memory necessary per Fitz #2) | HK HKO + on-chain eth_call patterns survive across sessions only via these |
31561:+| K10 | settlement gates (`SettlementSemantics.assert_settlement_value()` + lifecycle enum) | Concentrated in `src/contracts/settlement_semantics.py` + `src/state/lifecycle_manager.py` | verdict §1.8 (domain encoding necessary) | Trading-domain truth that source-read alone cannot derive |
31564:+| K13 | scoped `AGENTS.md` for actively-touched directories: `src/state`, `src/execution`, `src/risk_allocator`, `src/strategy`, `src/contracts`, `src/venue`, `src/calibration`, `src/engine`, `src/data`, `tests`, `scripts`, `architecture`, `docs/authority`, `docs/operations`, `docs/reference`, `config`, `raw` | ~17 routers (down from 41) | verdict §1.4 + Cursor docs ("additional files applied depending on the affected directories") | Pruning rationale below |
31565:+| K14 | small `fatal_misreads.yaml` (after HK HKO migrates to type) | ~140 lines (down from 153) | verdict §6.2 item 1 | Domain antibodies for cases NOT yet type-encodable |
31584:+| M7 | `architecture/task_boot_profiles.yaml` (360 LOC, 7 task-class profiles) | Reduce to 3 boot profiles (settlement, source, calibration); inline trigger words in scoped AGENTS.md | -~200 LOC | Opponent verdict §2 (smoking gun): Z2 retro does NOT name task_boot_profiles. Keyword-trigger boot profiles for 7 classes is over-engineered for the actual catches we observed. |
31640:+| P4: HK HKO type encoding | Add `HKO_Truncation` + `WMO_HalfUp` subclasses to `src/contracts/settlement_semantics.py`; relationship test `test_no_hko_wmo_mixing`; remove HK rows from `fatal_misreads.yaml` | 12h | relationship test passes; mypy strict; critic-opus PASS |
31655:+| Z2 retro 6-catch (compatibility-as-live-bypass, preflight-not-centralized, ACK-without-order-id, provenance-hash-over-mutated-fields, snapshot-freshness-without-time-semantics, 19 malformed slice-card YAML) | K1 critic + K2 verifier + K4 per-phase boot evidence + K7 retro discipline. **All 4 named mechanisms in Z2 retro retained.** |
31657:+| HK HKO truncation case | P4 type encoding (HKO_Truncation subclass) — STRICTLY BETTER than current YAML antibody per Fitz "make the category impossible" |
31701:+- **Long term (24+ months)**: K3 (semgrep antibodies) + K8 (schema migrations) + K10 (settlement gates) likely permanent — they encode TRADING-DOMAIN truths that survive any model improvement. K6 (memory) collapses to model-native API. K1+K2 (critic+verifier) likely remain because adversarial-quality independent review benefits from dispatching to a DIFFERENT model instance with DIFFERENT context (per LangGraph state-machine pattern, verbatim from langchain.com/blog/langgraph-multi-agent-workflows Jan 2024: "Each agent can have its own prompt, LLM, tools, and other custom code to best collaborate with the other agents").
31703:+**Asymptote floor**: ~1,500-2,000 LOC of trading-domain antibodies + the per-PR critic+verifier dispatch + the schema-backed INVs + settlement type encoding. Below this, marginal value of further pruning becomes negative because the trading-domain truths still need encoding somewhere.
31738:+**Application**: LangGraph — a production multi-agent framework on the same model generation — explicitly endorses STATE-MACHINE-AS-GRAPH for multi-agent orchestration. This is the CORRECT framing for Zeus's per-phase critic+verifier+executor dispatch (K1+K2 retained). It is NOT a framing that endorses prophylactic-YAML-cathedral. The LangGraph article does not recommend a particular harness size — but it explicitly recommends **distinct agent identity + state-as-shared-artifact** — which is exactly what K4 (per-phase boot evidence) + K5 (disk-first) + K7 (retro discipline) deliver in the reformed harness.
31756:+| Type-encoded antibodies (NEW) | 0 | 1 (HKO_Truncation/WMO_HalfUp) | ~12h (P4) |
31770:+4. **Cost asymmetry of failure**: a missed catch in trading is unbounded-cost; an over-large harness is bounded-cost. Verdict §1.7 LOCKED that "Anthropic Dec 2024 'few lines of code' guidance does NOT apply directly to live-money trading mode" — this asymmetry argues for IN-PLACE pruning over whole-replace because in-place pruning preserves the conservative trading bias.
31831:+1. **Renaissance comparison conflates established firm with bootstrap stage** — and opponent's own §2 reason 2 caveat acknowledges this. Renaissance's 150-person staffing pattern was built AFTER they had measurement infrastructure + risk controls + execution validation. Zeus is NOT at the "alpha is the bottleneck" stage yet — it is at the "do we have edge at all?" stage, where the answer comes from EDGE_OBSERVATION (which both sides agree starts week 1) AND from the harness substrate that ensures the measurement isn't corrupted by upstream signal pollution. Citing Renaissance's mature-firm staffing pattern for a bootstrap-stage system is mode-mismatched, the same way Anthropic Dec 2024 "few lines of code" was mode-mismatched per verdict §1.7 LOCKED.
31833:+2. **Paul Graham's "automate AFTER bottlenecks" applies to PRODUCT FEATURES, not safety infrastructure.** Graham's essay is about chat support, manual onboarding, hand-built early-stage features — things where premature automation reduces flexibility and learning. SAFETY INFRASTRUCTURE in financial systems is the OPPOSITE asymmetry: failure tail is unbounded, recovery from failure is impossible (lost money is lost). Graham's framework explicitly does not address regulated/financial/safety-critical contexts. Citing it for a live-money trading system trades the framework's strength (flexibility for product iteration) for its weakness (no defense against catastrophic operational failures). The Knight Capital + 2010 Flash Crash citations from my §2.A apply here directly: in trading, "perfectionism is procrastination" is exactly inverted — "skimping on infrastructure to chase edge" has a $440M precedent.
31925:+- Tier 3 P1 (hooks + native agents + native skills) — these are deterministic-gate substrate for ALL subsequent edge work; without hooks, edge packets re-introduce the verdict-§1.5 LARP risks.
31965:+- **Knight Capital + 2010 Flash Crash precedents stand.** Skimping infrastructure to chase edge has a $440M precedent. The substrate-stability minimum (Tier 1 + hooks + decorator settlement) is non-negotiable.
31993:+- **High-interest sections** (rarely-touched but high-blast-radius if wrong, like INV-09 INV-15 prose-as-law that could mis-fire on calibration touch): pay down NOW. Matches my front-loaded position.
32097:+**Counter-1 (Knight Capital, Headlands Tech 2017-08-03)**: verbatim quote: *"(like Knight Capital's +$400m loss)"* attributed to operations/monitoring failure. Knight Capital lost $440M in 45 minutes when an unaudited deployment activated dormant code in production. The dormant code's sin: operational infrastructure not pruned to current state. **Zeus's untested INVs + drifted citations + 14-mechanism catalog are dormant-code analogues** — they are not load-bearing today, but they create attentional debt that increases the probability of an "I-thought-this-was-handled" failure during edge work. Every harness section that exists but doesn't catch (DEEP_PLAN §6.2 trigger 2: "90-day catch log shows ZERO catches attributed to a specific manifest section → trigger sunset") IS a Knight Capital risk surface. Pruning IS edge protection.
32101:+**Counter-3 (DEEP_PLAN's own §6.2 trigger 5)**: *"Cumulative agent process-fault rate > 1 per 12h sustained — review whether harness is producing failures it then catches."* The round-1 retrospective documented 7 process faults in 12h — already over the trigger threshold. EDGE work amplifies this because EDGE packets cross multiple zones (calibration + execution + monitoring) where the harness is most relevant. Without Tier 2/3 pruning, EDGE work runs at 7+ process faults per 12h, eating into the 30-60h-per-packet budget.
32122:+Verdict §1.2 #1 LOCKED: *"Z2 retro 6 catches are real and at least one is a live-money loss vector (compatibility-as-live-bypass on V2 cutover)."* EDGE packets touch:
32123:+- CALIBRATION_HARDENING → calibration store + Platt fitting + decision_group write paths (HIGH zone risk)
32133:+Per Zeus's Money Path (root AGENTS.md:7-13): *"contract semantics → source truth → forecast signal → calibration → edge → execution → monitoring → settlement → learning."* EDGE packets are mid-pipeline (calibration through monitoring). Each downstream stage assumes upstream correctness. If forecast signal is corrupted (because INV-15 "forecast rows lacking canonical cycle identity" is pure prose-as-law and gets violated silently), CALIBRATION_HARDENING optimizes against bad signal — making the model worse at being right about wrong data.
32137:+EDGE work that runs AFTER Tier 2/3 pruning has cleaner ground truth to optimize against. EDGE work that runs DURING Tier 2/3 pruning races against the safety tightening — and the race is itself a Z2-class regression risk.
32145:+| Months 1-2 | weeks 1-8 | **60%** | **40%** | Finish Tier 1 (in flight) + Tier 2 #11/#12/#17 (auto-gen registries + skill migration + decorator prototype) ~30-50h. Start ONE EDGE packet in parallel: **EDGE_OBSERVATION** (it's pure measurement, lowest write-path risk). |
32147:+| Months 5-6 | weeks 17-24 | **20%** | **80%** | Tier 3 P4-P10 trickle ~30-55h on safe weeks; CALIBRATION_HARDENING (the highest-stakes EDGE packet — touches K0_frozen_kernel calibration store) gets the dedicated focus. Defer LEARNING_LOOP until 6-month re-audit. |
32162:+| **EDGE_OBSERVATION** | Month 1 (parallel with Tier 2) | Pure measurement; lowest write-path risk; informs whether OTHER edge work is even justified (if no edge, no point optimizing it). Per Headlands Tech: "Programs to optimize and analyze the trading strategy" includes monitoring as foundational. |
32163:+| **WS_OR_POLL_TIGHTENING** | Month 3 (after Tier 3 P1) | Operational; touches execution but not calibration. Hooks (Tier 3 P1) make it safer. |
32165:+| **CALIBRATION_HARDENING** | Month 5-6 (after Tier 3 P3 module_manifest reorg) | HIGHEST risk packet — touches K0_frozen calibration store + Platt fitting + decision_group write paths. Needs maximum harness hardening before starting. |
32179:+- *"Operations/monitoring: Monitor strategies and risk intraday and overnight to ensure there are no problems (like Knight Capital's +$400m loss)"*
32180:+- *"If the algorithm performs differently in production than it did on historical data, then it may lose money when it was supposed to be profitable."*
32182:+**Application**: A practitioner-grade quant trading firm explicitly identifies operations/monitoring as a co-equal product layer with strategy research, anchored to a SPECIFIC catastrophic loss ($440M, Knight Capital, 2012). This is direct industry evidence that **edge work without proportionate infrastructure investment produces tail-risk losses that exceed the optimized edge's value**. Zeus's harness IS its operations/monitoring layer; pruning it to the load-bearing core is an INVESTMENT in the operations side that mirrors industry practice. Headlands does not specify a % allocation, but they list both layers as PERSISTENT — neither finishes; both run continuously.
32193:+**Application**: The 2010 Flash Crash demonstrates the **asymmetric cost of inadequate observability infrastructure**: a 5-minute crash took 5 MONTHS to analyze because the underlying data + monitoring infrastructure wasn't built for it. The regulatory response (circuit breakers, CME Stop Logic, CAT initiative July 2012) was a forcing function for infrastructure investment AFTER the loss event. Zeus is in a position to do this BEFORE the loss event — Tier 2/3 harness pruning + drift-checker extension + native hooks + type-encoded antibodies are exactly the "circuit breaker" / observability layer that the post-mortem of every catastrophic trading event has identified as missing. EDGE work compounds risk; safety infrastructure compounds the ability to RECOVER from edge failures.
32224:+2. **Z2-class regression escapes** to live trading — immediate audit of the catching infrastructure.
32249:+**Distance from likely opponent position**: opponent likely wants 80-90% edge starting now; mine is 40-60% edge ramping. Real disagreement: **how much risk to absorb during the front-loaded window**. Counter-evidence: Knight Capital + 2010 Flash Crash demonstrate the asymmetric cost of skimping on infrastructure to chase edge.
32328:+- HK HKO as type subclass — round-2 alt-system territory
32348:+- Asymmetric counterfactual: HK HKO caution + Z2 6-catch + V2 BUSTED-HIGH cannot be replaced by 1M-ctx source-read
32368:+- **Disagreement**: whether Anthropic's "minimal scaffolding" applies to live-money trading mode (proponent: NO; opponent: YES)
32518:+| C (architecture/K0_frozen) | #4 SettlementRoundingPolicy ABC + HKO/WMO subclasses + relationship test | HIGH (K0_frozen_kernel zone) | pending Batch B |
32535:+| `tests/test_settlement_semantics.py` | does NOT exist (will CREATE in BATCH C) |
32581:+| Type-encoded antibodies | 0 | **1+** (`SettlementRoundingPolicy` ABC + HKO/WMO subclasses) | NEW |
32602:+| 4 | Type-encoded HK HKO antibody (`SettlementRoundingPolicy` ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses, ~30-60 LOC) replaces 17 lines of YAML in `fatal_misreads.yaml` | YES | proponent A4 / opponent §3.1 / Fitz "make category impossible" |
32622:+| Defense-in-depth principle | RESPECTED in live-money trading mode (verdict §1.7 LOCKED reaffirmed) |
32623:+| Migration philosophy | Gradualism > whole-replace for live-money operational safety |
32712:+| 4 | Encode HK HKO as `SettlementRoundingPolicy` ABC + subclasses | 2-4h | READY (specs in opponent's §3.1) |
32743:+| P8 | Type-encoded antibody migration: HK HKO + (optional) 1-2 more | ~6-12h | Per §4.1 #4 |
32874:+| 12 | Operator-attention compounding tax IS real but unmeasured (proponent's 0.5h/month/packet × 5 = 30-60h was asserted, not calibrated) | Opponent §2 W2 + proponent did not produce calibration data |
32899:+- **Weeks 13-24**: proponent wants edge-dominant; opponent wants to add harness substrate during HIGH-risk CALIBRATION_HARDENING packet
32932:+                                              CALIBRATION_HARDENING is HIGH-risk; deserves harness substrate
33100:+1. **Z2 retro 6 catches are real and at least one is a live-money loss vector** (compatibility-as-live-bypass on V2 cutover). Some discipline is load-bearing.
33112:+7. **Anthropic's Dec 2024 "few lines of code" guidance does NOT apply directly to live-money trading mode.** Mode-mismatch on that specific quote. Proponent's framing wins on this point.
33114:+8. **Cross-session memory + domain encoding ARE necessary** per Fitz Constraint #2 (translation loss thermodynamic). HK HKO truncation, settlement station ≠ airport station, Day0 ≠ historical hourly are not derivable from source-read alone — they must be encoded somewhere.
33127:+- Z2 retro 6-catch case is empirical; HK HKO + V2 BUSTED-HIGH + Z2 6-catch are domain knowledge that 1M-context source-read does not produce.
33137:+- HK HKO case is structurally better as a TYPE in `src/contracts/settlement_semantics.py` (per Fitz "make the category impossible") than as YAML antibody. The harness's own structure preempts this case's harness justification.
33212:+1. **HK HKO encoded as TYPE in `src/contracts/settlement_semantics.py`** (HKO_Truncation + WMO_HalfUp subclasses) RATHER THAN as YAML antibody in `fatal_misreads.yaml`. Per Fitz "make the category impossible". Estimated: 30 LOC; replaces 17 lines of YAML antibody with TypeError.
33259:+Teammates `proponent-harness@zeus-harness-debate-2026-04-27` and `opponent-harness@zeus-harness-debate-2026-04-27` remain alive in idle pending round-2 dispatch.
33285:+- INV-17 has 6 relationship tests in `tests/test_dt1_commit_ordering.py` (file docstring: "Relationship tests for DT#1 / INV-17: DB authority writes commit BEFORE...") — all PASSING
33339:+Status: **PHASES 1+2 APPLIED 2026-04-28 — see [evidence/canonical_apply_2026-04-28.md](evidence/canonical_apply_2026-04-28.md) for verbatim apply outputs + SHA chain.** Phases 3-5 deferred to operator (live resume + backup retirement).
33345:+Sequences the canonical-DB writes for F11 in 5 phases. Each phase is gated on the prior phase's verification passing. Each phase has a defined rollback path.
33349:+**Result on completion**: `forecasts.forecast_issue_time` non-NULL on every row; `forecasts.availability_provenance` typed per row with verified provenance for ECMWF/GFS and RECONSTRUCTED tier for ICON/UKMO/OpenMeteo; live forecasts cron resumes writing typed rows.
33368:+# C. Backup DB exists and matches canonical SHA pre-mutation
33371:+#         differs, someone wrote to the DB after backup was made — STOP and
33374:+# D. Schema does NOT yet have availability_provenance column
33375:+sqlite3 -readonly state/zeus-world.db "PRAGMA table_info(forecasts);" | grep -c availability_provenance
33383:+.venv/bin/python -m pytest tests/test_dissemination_schedules.py tests/test_forecasts_writer_provenance_required.py tests/test_backtest_training_eligibility.py tests/test_forecasts_schema_alignment.py tests/test_backtest_purpose_contract.py tests/test_backtest_skill_economics.py
33388:+- (B) LIVE NOT paused → resolve before continuing (live daemon could write while we migrate, causing column-not-found errors)
33400:+.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/migrate_forecasts_availability_provenance.py \
33404:+.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/migrate_forecasts_availability_provenance.py \
33408:+.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/migrate_forecasts_availability_provenance.py \
33419:+# Stop any live writes first
33473:+Before resuming the cron, run one writer cycle in a controlled context to confirm the new code path works against the migrated DB.
33481:+    .claude/worktrees/mystifying-varahamihira-3d3733/tests/test_forecasts_writer_provenance_required.py \
33501:+## 5. Phase 4 — Resume scheduler / live
33509:+  "SELECT COUNT(*) FROM forecasts WHERE availability_provenance IS NOT NULL AND retrieved_at > datetime('now', '-1 hour');"
33519:+Once Phase 4 has been live for 7 days without incident:
33536:+| Phase 1 ✓ → resume cron without Phase 2 | New cron-tick rows get typed provenance (writer correctly populates). 23,466 old rows stay NULL on `forecast_issue_time` and `availability_provenance`. SKILL training-eligibility filter rejects them (correct). DIAGNOSTIC purpose can still see them. Backfill can be applied later without issue. |
33578:+Total apply time: under 5 minutes of canonical-DB-locked work.
33599:+| 1 | F11.2 schema migration | ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT (with CHECK constraint) — applied on canonical; row count 23,466 → 23,466 (no row mutation) |
33600:+| 2 | F11.4 backfill | UPDATE 23,466 forecasts rows with derived (forecast_issue_time, availability_provenance) in single transaction; 0 NULL rows remaining post-apply |
33602:+| 4 | Resume cron | DEFERRED to operator (LIVE remains PAUSED) |
33603:+| 5 | Backup retirement | DEFERRED to 7-day soak per runbook |
33614:+[apply] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
33617:+[apply] Column added; all 23,466 rows have NULL provenance (expected; backfill via F11.4).
33619:+[verify] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
33620:+[verify] availability_provenance column present.
33629:+[apply] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
33634:+[verify] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
33637:+[verify] Per-source × provenance distribution:
33666:+tests/test_forecasts_writer_provenance_required.py ........      [ 33%]
33682:+| `gfs_previous_runs` | 4,998 | `derived_dissemination` | NOAA GFS via Open-Meteo previous_runs; lag = `base + 4h14m` (NCEP production status MOS-completion verified) |
33690:+## 6. What's now live on canonical
33693:+- `forecasts` table has `availability_provenance` column with CHECK constraint enforcing the 4-tier enum
33694:+- All 23,466 historical rows carry typed provenance and non-NULL `forecast_issue_time`
33695:+- Live writer (`forecasts_append.py:_insert_rows`) will fail-fast on any new row with NULL provenance (F11.3 antibody)
33697:+- Training-eligibility helpers (`src/backtest/training_eligibility.py`) ready for downstream consumer migration (F11.5-migrate slice — `etl_historical_forecasts.py` + `etl_forecast_skill_from_forecasts.py`)
33707:+# Stop any live writes first (LIVE PAUSED, but verify daemon)
33719:+- **F11.5-migrate**: wire `SKILL_ELIGIBLE_SQL` into `etl_historical_forecasts.py:71` and `etl_forecast_skill_from_forecasts.py:120` so training-only ETLs filter at the SQL layer (per `forecasts_consumer_audit_2026-04-28.md`).
33722:+- **Q5 WU obs triage**: separate packet, separate operator decisions Q5-A/B/C.
33742:+Authority basis: live grep against `src/`, `scripts/`, `tests/` at HEAD `5bd9be8`
33754:+This locates every read site on the `forecasts` table outside of test fixtures. The F11.5 `training_eligibility` SQL filter (`SKILL_ELIGIBLE_SQL` + `ECONOMICS_ELIGIBLE_SQL`) should be applied at the read sites that participate in **training/learning** flows; diagnostic / coverage / count readers do NOT need the filter.
33762:+| `src/data/hole_scanner.py:363` | Coverage scanner — counts (city, source, target_date) tuples; flags missing rows | ✗ No | Coverage is purpose-agnostic; ALL rows count for completeness, not just training-eligible. |
33763:+| `scripts/etl_historical_forecasts.py:71` | Legacy `forecasts` → `historical_forecasts` ETL | ✓ **Yes (SKILL filter)** | Output feeds calibration / training; RECONSTRUCTED rows would corrupt training. Highest-priority migration. |
33764:+| `src/engine/replay.py:314` | Replay synthetic forecast fallback (`forecasts_table_synthetic`) | ⚠ Partial | Already labeled `decision_reference_source: "forecasts_table_synthetic"` and rejected by SKILL purpose via S1's `gate_for_purpose`. The read at L314 is purpose-agnostic; the gate happens downstream. The pure SQL reader does NOT need a WHERE filter, but a defense-in-depth filter could be added. |
33765:+| `scripts/etl_forecast_skill_from_forecasts.py:120` | Forecast skill ETL — produces forecast skill aggregates | ✓ **Yes (SKILL filter)** | Output feeds skill scoring; semantically requires only training-eligible rows. |
33766:+| `scripts/etl_forecasts_v2_from_legacy.py:104, 141` | Legacy `forecasts` → `forecasts_v2` ETL | ⚠ Partial — preserve provenance | When migrating legacy → v2, the per-row `availability_provenance` should propagate to v2 (not be dropped). v2 schema may need extension. |
33771:+| `scripts/migrate_forecasts_availability_provenance.py:44, 53` | F11.2 schema migration; counts + groupby | ✗ No (own slice) |
33779:+**Scope = 2 scripts** (highest-priority training-eligibility leaks):
33783:+   - After: `FROM forecasts WHERE ... AND availability_provenance IN ('fetch_time', 'recorded', 'derived_dissemination')` (or equivalent via `SKILL_ELIGIBLE_SQL`)
33789:+3. `src/engine/replay.py:314` — add `availability_provenance` predicate redundantly with the existing `gate_for_purpose` downstream check.
33790:+4. `scripts/etl_forecasts_v2_from_legacy.py:141` — propagate `availability_provenance` from legacy row to v2 row.
33793:+- All counting / hole-scanner / migration / backfill readers — they need every row, not just training-eligible.
33799:+This migration depends on F11.4 backfill having populated `availability_provenance` on existing rows. Apply order:
33801:+1. F11.2 schema migration (operator approves) — `availability_provenance` column added.
33812:+- `tests/test_etl_historical_forecasts.py` (NEW) — assert that running the ETL with mixed-provenance fixture produces only DERIVED+RECORDED+FETCH_TIME rows.
33815:+Both can use the in-memory fixture style from `tests/test_backtest_training_eligibility.py`.
33822:+- The F11.5 module (`src/backtest/training_eligibility.py`) was committed at HEAD `5b1b05d`. The 2 scripts above import from it for the migration.
33834:+Authority basis: `architecture/invariants.yaml` (INV-06 point-in-time truth, INV-15 forecast cycle identity), `docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md` §5 (D4 typed provenance), forensic finding F11 ("forecast available_at may be reconstructed → hindsight leakage risk"), live SQL probe 2026-04-27/28
33835:+Status: planning evidence; not authority. No DB / schema mutation in this plan packet.
33842:+F11 is the realized, on-disk form of the hindsight-leakage risk:
33843:+- 23,466 rows in `state/zeus-world.db::forecasts` have `forecast_issue_time = NULL` (verified live SQL 2026-04-28).
33880:+This is **not 4 fixes** (one per NULL column). It is **one structural decision**: what level of decision-time-truth provenance do these rows carry? Once that's typed, all four columns either get values or are explicitly stamped UNKNOWN.
33882:+**The decision (Q1 below) is**: where does `availability_provenance` live in the schema?
33888:+### Q1. Schema treatment for `availability_provenance`
33893:+| **B. Add `availability_provenance` TEXT column** | small migration, planning lock | Cleanest; matches D4 type contract | DB schema change; requires a schema slice |
33894:+| **C. Pack into existing `raw_payload_hash` JSON-extended field** | none | No new column | Conceptually wrong — provenance ≠ hash |
33896:+**Recommendation: B** — minimal column add (`ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT`), explicit semantic, gates training-eligibility queries.
33914:+| **A. Single migration script** | `scripts/backfill_forecast_issue_time.py` populates all 23,466 in one pass; per-source dissemination derivation; mark `availability_provenance = "DERIVED_FROM_DISSEMINATION"` (or RECONSTRUCTED for openmeteo `best_match`) | medium |
33922:+D4 antibody requires the writer to STAMP an `availability_provenance` value. Should the writer's `_insert_rows` raise if the provenance is NULL?
33944:+### Slice F11.2 — Schema migration + `availability_provenance` column
33947:+- One ALTER TABLE migration adding `availability_provenance TEXT`.
33948:+- Migration script under `scripts/migrate_forecasts_availability_provenance.py`.
33960:+- `src/data/forecasts_append.py:_rows_from_payload` derives `forecast_issue_time = base_datetime + dissemination_lag(source)` and `availability_provenance` accordingly.
33963:+- Writer assertion: row constructed without `availability_provenance` raises (Q4).
33966:+- `tests/test_forecasts_writer_provenance_required.py` — antibody that NULL-provenance row inserts fail; OpenMeteo best_match rows get RECONSTRUCTED; ECMWF rows get DERIVED_FROM_DISSEMINATION.
33968:+Blast radius: medium (touches live ingest writer that runs every cron tick). Must confirm cron job continues to succeed.
33973:+- `scripts/backfill_forecast_issue_time.py` reads existing rows, applies `dissemination_schedules.py` per source, writes `forecast_issue_time` + `availability_provenance` in a single transaction.
33975:+- Rows where derivation fails (e.g., openmeteo best_match) get `availability_provenance="RECONSTRUCTED"` + `authority_tier="QUARANTINED_PRE_F11"`.
33984:+- `src/backtest/skill.py` and any future `decision_time_truth` consumer of `forecasts` reads `availability_provenance` and routes through the per-purpose `gate_for_purpose`.
33985:+- Training-eligibility view: only rows with `availability_provenance IN ('FETCH_TIME', 'RECORDED', 'DERIVED_FROM_DISSEMINATION')` are training-eligible.
33999:+3. F11.2 + F11.4 require operator approval before applying to canonical DB; F11.4 requires verified backup.
34008:+- Touching `ensemble_snapshots` (TIGGE-sourced, separate path with already-correct provenance — see [task_2026-04-27_backtest_first_principles_review/evidence/vm_probe_2026-04-27.md](../task_2026-04-27_backtest_first_principles_review/evidence/vm_probe_2026-04-27.md) §6.A).
34010:+- LOW-track settlements writer.
34022:+| F11.4 backfill 23,466 rows | medium | 4-6h + DB backup verify | 1-2 days |
34032:+| Q1 | Schema treatment for availability_provenance (A/B/C) | F11.2 |
34035:+| Q4 | Writer raises on NULL provenance? | F11.3 |
34044:+- L22 commit boundary: F11.3 + F11.4 implementation MUST NOT autocommit before critic dispatch (these are runtime + canonical-DB-mutating slices).
34047:diff --git a/docs/operations/task_2026-04-28_wu_observations_empty_provenance_triage/plan.md b/docs/operations/task_2026-04-28_wu_observations_empty_provenance_triage/plan.md
34051:+++ b/docs/operations/task_2026-04-28_wu_observations_empty_provenance_triage/plan.md
34053:+# WU Observations — 39,431 Empty-Provenance Rows Triage (Q5)
34057:+Authority basis: forensic finding F5 (CRITICAL), `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md` §H4 + §C3.A, packet 2026-04-27 §02 §3.A (Q5 deferred), live SQL probe 2026-04-27/28
34058:+Status: planning evidence; not authority. No DB / schema mutation in this plan packet.
34064:+F11 fixed `forecasts` table provenance (forecast issue_time NULL → typed). This packet fixes `observations` table provenance (provenance_metadata empty on 99% of WU rows). They are **different tables, different writers, different consumers** — combining would explode blast radius.
34073:+  Per-source distribution + empty-provenance rate:
34083:+**Pattern**: WU writer historically did NOT stamp `provenance_metadata`; ogimet + HKO writers always did. Single-writer defect, not systemic data-quality problem.
34093:+| **A. Going-forward writer hardening** | Ensure `wu_icao_history` writer NEVER produces empty-provenance rows again. Code change. |
34094:+| **B. Historical row treatment** | Decide what to do with the 39,431 existing empty-provenance rows. Operator decision (Q5 from packet 02). |
34102:+**Oracle shadow snapshots** at `raw/oracle_shadow_snapshots/{city}/{date}.json` are recent (post-2026-04-15) authoritative WU API captures with full payload + sha256 derivable.
34104:+| Dimension | settlements | observations (WU) | oracle_shadow | Overlap with empty-provenance rows |
34107:+| % of empty-provenance rows that overlap | n/a | n/a | n/a | **0.24%** |
34116:+- `UPDATE observations SET authority='QUARANTINED', quarantine_reason='empty_provenance_wu_daily_pre_2026-04-15' WHERE source='wu_icao_history' AND (provenance_metadata IS NULL OR provenance_metadata = '' OR provenance_metadata = '{}')`
34117:+- Effect: training-eligible WU obs drops from 39,437 → 6 rows (the 6 that already had provenance).
34119:+- Cons: scale loss in training data (2.3 years of WU obs effectively excluded).
34125:+  - Compute provenance_metadata from shadow's `wu_raw_payload`:
34135:+  - UPDATE `provenance_metadata` on the matching observation rows
34137:+- Pros: salvages the 96 audit-grade rows; explicit provenance for going-forward training; the 39,335 quarantine is reversible if log-replay (option C.1) ever lands.
34156:+- Identify the WU writer (likely `src/data/observation_client.py` or `src/data/daily_observation_writer.py`).
34157:+- Extend writer to require non-empty `provenance_metadata` on every insert.
34158:+- Add assertion `if not provenance_metadata: raise ValueError(...)` at writer site (matches F11.3 pattern).
34160:+- Antibody test: `tests/test_wu_observations_writer_provenance_required.py`.
34164:+Blast radius: low-medium (touches live ingest writer; not currently running due to LIVE PAUSED).
34166:+### Slice Q5.2 — Schema migration (provenance_metadata NOT NULL CHECK)
34169:+- Add CHECK constraint: `provenance_metadata IS NOT NULL AND provenance_metadata != ''`.
34181:+- New script `scripts/backfill_wu_obs_provenance_from_oracle_shadow.py`.
34184:+- UPDATE `provenance_metadata` with derived JSON bundle.
34194:+- New script `scripts/quarantine_wu_obs_empty_provenance.py`.
34197:+- DB backup verified before apply.
34201:+Blast radius: medium (mutates 39,335 existing canonical rows). Requires DB backup + operator confirmation.
34206:+- Training-eligibility view / filter rejects QUARANTINED rows with this reason (Zeus already filters on `authority='VERIFIED'` for calibration training; verify the filter holds).
34207:+- Antibody test: assert calibration manager refuses to read QUARANTINED rows.
34225:+Apply order on canonical DB:
34239:+| Q5-B | Do historical WU fetcher logs exist? If yes, Q5.6 (option C.1 log-replay) becomes a future opportunity. | Q5.6 future |
34240:+| Q5-C | Authorize canonical DB writes for Q5.3 / Q5.4? | Apply step |
34251:+| Q5.4 quarantine 39,335 | medium | 4-6h + DB backup verify |
34261:+| oracle_shadow JSON SHA256 mismatch (operator captured vs original WU response) | low | Verify shadow capture has integrity checks; if not, mark backfilled rows as `oracle_shadow_backfill_v1` provenance with explicit caveat |
34262:+| Quarantine breaks downstream calibration that already trained on these rows | medium | Audit calibration pairs / Platt models for downstream provenance — they should not have been trained on empty-provenance rows already (forensic ruling: training BLOCKED). If they were, the Platt models also need re-training. |
34263:+| Writer hardening (Q5.1) breaks live ingest if a code path exists that doesn't have provenance_metadata available | medium | Run regression with the writer change; verify live cron job continues to succeed in next tick after Q5.1 commit (live still paused; safe window) |
34264:+| Operator confirms historical WU logs exist (option C.1) — sunk cost on Q5.4 quarantine | low | Quarantine is reversible: UPDATE authority back to VERIFIED. The work is not wasted. |
34271:+- LOW-track settlements writer — separate packet
34274:+- Touching observations table for non-WU sources — they're already clean
34280:+- L20 grep-gate: every row count probed live within 30 minutes of writing.
34282:+- L24 git scope: stage only `task_2026-04-28_wu_observations_empty_provenance_triage/**` files for this plan packet.
34293:-  landed high-risk module books
34296:+  `modules/riskguard.md` when routed by the active phase/module manifest
34298: Current data/source facts live under operations current-fact surfaces, not in
34299: this directory. Dated analytical/support snapshots live under the reports
34304:+| `modules/venue.md` | Dense Polymarket V2 adapter / submission provenance module book |
34306:+| `modules/execution.md` | Dense live execution / command / exit / settlement and pre-submit gate module book |
34307:+| `modules/riskguard.md` | Dense riskguard and R3 A2 risk-allocator/governor module book |
34326: | `execution.md` | Dense module book for live-money order placement, exit mechanics, and settlement harvest |
34327:+| `venue.md` | Dense module book for Polymarket V2 adapter boundaries and submission provenance |
34329: | `riskguard.md` | Dense module book for protective enforcement and behavior-changing risk levels |
34330: | `control.md` | Dense module book for the external control plane and gate provenance |
34332:diff --git a/docs/reference/modules/calibration.md b/docs/reference/modules/calibration.md
34334:--- a/docs/reference/modules/calibration.md
34335:+++ b/docs/reference/modules/calibration.md
34343: - tests/test_calibration_manager.py
34344: - tests/test_calibration_quality.py
34345: - tests/test_calibration_unification.py
34346:+- tests/test_calibration_retrain.py
34358:-- Execution intent/evidence: typed objects that separate decision, price, provenance, and actuation.
34359:+- Execution intent/evidence: typed objects that separate decision, allocation metadata, price, provenance, and actuation.
34364: | `calibration_bins.py` | Discrete-support geometry for training and market bins. |
34368:+| `execution_intent.py` | Typed expression of what execution may do; critical boundary surface. R3 A2 adds `event_id`, `resolution_window`, and `correlation_key` so allocation caps are carried by production intents rather than dynamic test-only attributes. |
34376: - tests/test_calibration_bins_canonical.py
34379: - tests/test_backtest_settlement_value_outcome.py
34382:+- tests/test_risk_allocator.py
34385: - Settlement rounding must match WMO half-up / HKO-special semantics exactly; Python banker's rounding is forbidden.
34387: - Metadata fields must not be mistaken for settlement outcomes.
34395: - Any rounding helper used by settlement or Monte Carlo simulation
34398:+- `ExecutionIntent` allocation metadata consumed by `src/risk_allocator/governor.py`
34402: pytest -q tests/test_calibration_bins_canonical.py tests/test_execution_price.py tests/test_architecture_contracts.py
34403:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py
34404: pytest -q tests/test_backtest_settlement_value_outcome.py
34418: - `docs/authority/zeus_current_delivery.md` planning-lock and always-human-gated sections for control semantics
34441:+- GTC/GTD live resting orders must not submit unless the venue heartbeat is HEALTHY; FOK/FAK immediate-only orders are the only heartbeat-exempt order types.
34445: - No hidden in-memory override dicts as long-lived truth.
34462: - Gate semantic change: review riskguard, engine, and state together.
34486:+  wrapper for callers that still patch/use `PolymarketClient`; live
34489:+  maker/taker policy reaches live adapter submit calls.
34498: | `wu_hourly_client.py / ogimet_hourly_client.py / observation_instants_v2_writer.py / tier_resolver.py` | New same-source-as-settlement hourly migration stack. |
34502:+| `tigge_client.py` | R3 F3 dormant TIGGE ingest adapter; construction is safe with gate closed, and open-gate fetch reads only an operator-approved local JSON payload configured by constructor, `ZEUS_TIGGE_PAYLOAD_PATH`, or `payload_path:` in the decision artifact. |
34504:+| `market_scanner.py / polymarket_client.py` | Venue/executable-context inputs; live order side effects route through the V2 venue adapter; balance compatibility configures CollateralLedger with pUSD. `polymarket_client.py` preserves A2-selected `order_type` on the adapter boundary. |
34509: - Daily settlement writer source routing
34525:+- `src/control/heartbeat_supervisor.py` status, surfaced by cycle summary for R3 live-money readiness
34526:+- `src/risk_allocator/governor.py` refreshed at cycle start for R3 A2 allocation/kill-switch state
34527: - `architecture/invariants.yaml` lifecycle and risk-actuation rules
34536: | `evaluator.py` | Turns signal/calibration/market context into action candidates. |
34538: | `replay.py` | Historical/replay path that must not silently diverge from live semantics. |
34548:+- tests/test_risk_allocator.py
34551: - Engine sequencing must not collapse settlement, monitoring, and execution into one truth plane.
34552: - Exit intent is not economic closure; settlement is not exit.
34556:+- RED force-exit uses `cycle_runner._execute_force_exit_sweep` as the sole proxy for durable `red_force_exit_proxy` CANCEL command emission; RiskGuard remains policy/observability and does not write venue commands.
34560: - Do not make the engine infer settlement/source law from whichever endpoint currently answers.
34561: - Do not let monitor-refresh data silently become settlement truth.
34562: - Do not patch around state/risk/execution boundaries by adding orchestration shortcuts.
34563:+- Do not add additional RED durable-command writers outside `_execute_force_exit_sweep`.
34564: - Do not let replay-only assumptions leak into live runtime.
34568: - Day0 monitoring uses the wrong source or stale current fact and misses risk events.
34569: - Replay diverges from live semantics and produces false confidence.
34571:+- Heartbeat health is logged but not wired into entry-block reasons, creating advisory-only live-money protection.
34578: - Cycle orchestration change: review state, riskguard, execution, observability together.
34580:+- PortfolioGovernor/allocation sequencing change: review `src/risk_allocator/governor.py`, executor submit gates, and cycle summary together.
34582: - Replay change: prove semantic parity with live path.
34593:+- `src/execution/command_bus.py`, `executor.py`, `exchange_reconcile.py`, `settlement_commands.py`, `exit_triggers.py`, `exit_lifecycle.py`, `exit_safety.py`, `fill_tracker.py`, `collateral.py`, `wrap_unwrap_commands.py`, `harvester.py`
34594:+- `src/control/cutover_guard.py` and `src/control/heartbeat_supervisor.py` as pre-submit live-money gates consumed by executor
34595:+- `src/risk_allocator/governor.py` as the R3 A2 pre-submit capital allocation and kill-switch gate consumed by executor
34596:+- `src/execution/executor.py` must persist a U2 pre-submit `VenueSubmissionEnvelope` before SDK contact; no live order may rely on an uncited command row.
34598: - `architecture/invariants.yaml` on exit-vs-settlement and risk actuation
34603: - `executor.py` live actuation path
34608:+- `fill_tracker.py`, `collateral.py`, `wrap_unwrap_commands.py`, and `settlement_commands.py` helper APIs
34609: - `harvester.py` for post-trade/settlement collection flows
34616:-| `executor.py` | Primary live-money actuation entrypoint. |
34618:+| `executor.py` | Primary live-money actuation entrypoint. M2 maps exceptions after possible venue submit side effects to `OrderResult.status="unknown_side_effect"` and `SUBMIT_UNKNOWN_SIDE_EFFECT`, never semantic rejection. R3 A2 consults the global RiskAllocator before command persistence/SDK contact, persists/submits the selected maker/taker order type, and raises structured `AllocationDenied` when capacity/governor gates deny new risk. |
34620:+| `settlement_commands.py` | R3 R1 durable settlement/redeem command ledger. `REDEEM_TX_HASHED` is the crash-recovery anchor; Q-FX-1 gates pUSD redemption/accounting. |
34627:+| `wrap_unwrap_commands.py` | Durable USDC.e↔pUSD command states; Z4 has no live chain-submission authority. |
34628: | `harvester.py` | Collects external result/harvest information without redefining settlement law. |
34643:+- tests/test_settlement_commands.py
34644:+- tests/test_risk_allocator.py
34647: - Exit intent is not closure; economic close is not settlement.
34648: - Execution must obey risk/control actuation; advisory-only risk is theater.
34649:+- Resting GTC/GTD live orders must pass CutoverGuard, HeartbeatSupervisor, RiskAllocator/PortfolioGovernor, and CollateralLedger before venue-command persistence or SDK contact; missing heartbeat/collateral/allocation health is a hard pre-submit failure.
34656: - Do not infer settlement success from local order/fill state.
34659:+- Do not bypass CutoverGuard, HeartbeatSupervisor, RiskAllocator/PortfolioGovernor, or CollateralLedger for live placement convenience tests; tests that exercise executor mechanics must explicitly opt out with monkeypatches.
34668:+- `settlement_commands.py` Q-FX-1 gate, payout-asset classification, and `REDEEM_TX_HASHED` recovery semantics
34669:+- Heartbeat/order-type submit gates before `_live_order` and `execute_exit_order`
34675: - `harvester.py` settlement-result handling
34680:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py
34705:+- Not live cutover approval.
34715:+- `MATCHED` trade facts may create `OPTIMISTIC_EXPOSURE`; only `CONFIRMED` creates `CONFIRMED_EXPOSURE` / canonical training eligibility.
34726:diff --git a/docs/reference/modules/riskguard.md b/docs/reference/modules/riskguard.md
34728:--- a/docs/reference/modules/riskguard.md
34729:+++ b/docs/reference/modules/riskguard.md
34734:+- R3 A2 allocation governor APIs in `src/risk_allocator/governor.py`:
34741: | `risk_level.py` | Level taxonomy and ordering. |
34744:+| `../risk_allocator/governor.py` | R3 A2 blocking capital allocator/governor: reads current `position_lots`, unresolved submit-unknown side effects, open exchange reconcile findings, heartbeat/WS health, and drawdown evidence to deny new risk or force reduce-only/no-trade modes before executor submission. |
34746:+### R3 A2 risk allocator / portfolio governor
34748:+`src/risk_allocator/` is a K1 governance adjunct to RiskGuard. It is intentionally
34767:+  the seam defaults to allow so isolated tests and non-live utility seams remain
34777:+- tests/test_risk_allocator.py
34781: - `strategy_key` remains the only governance key; risk metadata must not become a competing key.
34782: - Alerting is not proof of risk actuation.
34789: - Do not add a risk level that only changes UI or logs.
34792: - Any change to risk levels, policy grammar, or how risk affects runtime behavior.
34803:+pytest -q -p no:cacheprovider tests/test_risk_allocator.py
34804: python -m py_compile src/riskguard/*.py
34814: - Historical hourly features vs live same-day monitor context
34815:+- Forecast-source activation/provenance lives upstream in
34826:@@ -51,7 +51,7 @@ This is the strongest code truth surface after executable tests. Delivery law al
34831:+| `db.py` | Canonical write API and high-blast-radius state anchor, including R3 M5 `exchange_reconcile_findings` and R3 R1 `settlement_commands` schema. |
34835:@@ -59,6 +59,8 @@ This is the strongest code truth surface after executable tests. Delivery law al
34837: | `portfolio.py / portfolio_loader_policy.py` | Read model and load policy for live holdings. |
34840:+| `venue_command_repo.py` | Durable venue command/event journal plus R3 U2 raw provenance projections (`venue_submission_envelopes`, order facts, trade facts, position lots, provenance envelope events). R3 M1 keeps command-side transitions grammar-additive and leaves order/trade facts in U2. R3 M2 adds economic-intent duplicate lookup for unresolved `SUBMIT_UNKNOWN_SIDE_EFFECT` commands and persists acked `venue_order_id` from append-event payloads. |
34844:@@ -68,12 +70,25 @@ This is the strongest code truth surface after executable tests. Delivery law al
34850:+- tests/test_provenance_5_projections.py
34852:+- tests/test_riskguard_red_durable_cmd.py
34855:+- tests/test_settlement_commands.py
34858: - Append event before projection; never let derived JSON outrank canonical DB/event truth.
34865:+- RED force-exit durable CANCEL proxy commands are emitted only through `cycle_runner._execute_force_exit_sweep`; RiskGuard does not write venue commands directly.
34866:+- Settlement/redeem command failure is not lifecycle settlement; only canonical settlement paths may terminalize positions.
34870:@@ -99,6 +114,9 @@ This is the strongest code truth surface after executable tests. Delivery law al
34874:+- R3 F1 forecast provenance columns (`source_id`, `raw_payload_hash`,
34888:+- StrategyBenchmarkSuite replay/paper/live-shadow benchmark and promotion gate (R3 A1).
34895: | `risk_limits.py / oracle_penalty.py / selection_family.py` | Strategy-side constraints and grouping. |
34905:+- INV-NEW-Q: no strategy may be promoted to live unless StrategyBenchmarkSuite.promotion_decision() returns PROMOTE from replay + paper + shadow evidence.
34908: - Sizing must remain coupled to calibrated uncertainty and risk limits.
34911:+- A1 live-shadow evaluation is read-only evidence; it must not place orders, activate credentials, mutate production DB/state artifacts, or authorize CLOB cutover.
34942:+and market snapshot context into `VenueSubmissionEnvelope` provenance before
34948:+- Not a place to choose live cutover timing.
34954:+- `PolymarketV2AdapterProtocol`: the shared live/paper contract that T1 fake
34955:+  venues implement for parity tests without credentials, network I/O, or live
34957:+- `VenueSubmissionEnvelope`: immutable submission provenance contract.
34959:+  delegates live placement/cancel/order queries into the adapter while
34967:+persisted. The adapter creates a provenance envelope, signs/posts through the
34969:+DB tables directly.
34973:+The venue adapter is a live-money boundary, but it does not define settlement,
34974:+lifecycle, or risk law. It implements R3 Z2 and inherits INV-24, INV-25,
34975:+INV-28, INV-30, NC-NEW-G, INV-NEW-B, and T1's INV-NEW-M paper/live parity
34999:+  same protocol and compare envelope / event schemas against a mock live
35000:+  adapter. This does not authorize live submit/cancel/redeem or cutover.
35013:+pytest -q tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py
35015:+pytest -q tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py
35016:+pytest -q tests/test_neg_risk_passthrough.py
35037:+"""F11.4 backfill: populate forecast_issue_time + availability_provenance for NULL rows.
35040:+(base_time, lead_day) -> available_at function plus a provenance tier.
35085:+           OR availability_provenance IS NULL
35092:+    """Returns (issue_time_iso, provenance_value).
35109:+    issue_time, provenance = derive_availability(str(row["source"]), base_time, lead_day)
35110:+    return issue_time.isoformat(), provenance.value
35114:+    """Return {(source, derived_provenance): count}."""
35138:+    print(f"[dry-run] Target DB: {db_path}")
35141:+    if not _column_exists(conn, "availability_provenance"):
35142:+        print("[dry-run] FAIL: availability_provenance column missing. Run migrate_forecasts_availability_provenance.py --apply first.")
35159:+        print("[apply] FAIL: --apply requires --confirm-backup affirming a verified DB backup exists.", file=sys.stderr)
35161:+    print(f"[apply] Target DB: {db_path}")
35165:+        if not _column_exists(conn, "availability_provenance"):
35193:+            "UPDATE forecasts SET forecast_issue_time = ?, availability_provenance = ? WHERE id = ?",
35198:+            "SELECT COUNT(*) FROM forecasts WHERE forecast_issue_time IS NULL OR availability_provenance IS NULL"
35209:+    print(f"[verify] Target DB: {db_path}")
35212:+    if not _column_exists(conn, "availability_provenance"):
35213:+        print("[verify] FAIL: availability_provenance column missing.", file=sys.stderr)
35217:+            "SELECT availability_provenance, COUNT(*) FROM forecasts GROUP BY availability_provenance"
35222:+        "SELECT COUNT(*) FROM forecasts WHERE forecast_issue_time IS NULL OR availability_provenance IS NULL"
35226:+        "SELECT source, availability_provenance, COUNT(*) "
35227:+        "FROM forecasts GROUP BY source, availability_provenance ORDER BY source"
35229:+    print("[verify] Per-source × provenance distribution:")
35250:+        help="Required with --apply; affirms operator has verified a DB backup",
35260:+        print(f"DB not found: {db_path}", file=sys.stderr)
35283:+# Reuse: Run only through packet-approved ETL/backfill workflows; dry-run first for live DB work.
35284:+# Authority basis: R3 F1 forecast provenance wiring + historical forecast backfill packet.
35321:+# the F11 antibody (NULL availability_provenance / forecast_issue_time
35324:+# rows carry typed F11 provenance + R3 source_id/payload_hash/captured_at/
35325:+# authority_tier identically to the live ingest path. Path A duplication
35371:                 city.name, target_date, lead, source, high, low, city.settlement_unit, reason,
35374:+        # F11 antibody (2026-04-28): derive issue_time + provenance from the
35379:+            issue_time, provenance = derive_availability(source, base_time, lead)
35400:                 temp_unit=city.settlement_unit,
35409:+                availability_provenance=provenance.value,
35460:+    (NULL availability_provenance / forecast_issue_time would have been
35463:+    live cron path.
35474:     # path selects forecast_high only, so the JOIN must restrict settlements
35475:     # to HIGH rows or a future LOW settlement would spuriously match and
35478:+    # forecasts side of the JOIN. availability_provenance IS NULL clause
35479:+    # tolerates pre-F11 legacy DBs; post-F11 backfilled rows are filtered
35482:+    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
35485:+        "availability_provenance", "f.availability_provenance"
35496:           AND s.settlement_value IS NOT NULL
35497:+          AND (f.availability_provenance IS NULL OR {skill_filter_qualified})
35511:+    # availability_provenance IS NULL clause tolerates pre-F11 legacy DBs;
35515:+    # are excluded from training-grade ETL output.
35516:+    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
35522:+          AND (availability_provenance IS NULL OR {SKILL_ELIGIBLE_SQL})
35526:diff --git a/scripts/live_readiness_check.py b/scripts/live_readiness_check.py
35530:+++ b/scripts/live_readiness_check.py
35534:+# Purpose: Enforce R3 G1 live-readiness gate aggregation without live side effects.
35535:+# Reuse: Run before live-readiness, staged-smoke, cutover, or operator-deploy gate decisions.
35539:+"""R3 G1 live-readiness gate orchestrator.
35543:+mutates canonical DB/state artifacts.  Exit code 0 means all 17 readiness gates
35544:+passed *and* staged-live-smoke evidence is present; it is still not live-deploy
35545:+authority without the operator's live-money-deploy-go decision.
35602:+    operator_gate: str = "live-money-deploy-go"
35603:+    live_deploy_authorized: bool = False
35616:+        "NC-NEW-G / no legacy V1 SDK imports in live source",
35670:+        "Full side-effect provenance chain reconstructable",
35672:+        ("tests/test_provenance_5_projections.py::test_full_provenance_chain_reconstructable",),
35731:+        "Paper/live parity gate",
35733:+        "Fake venue emits live-adapter-compatible envelope/result schema",
35749:+        "Agent-facing docs do not expose legacy direct SDK live paths",
35868:+    if "_select_risk_allocator_order_type" not in executor or "order_type=order_type" not in executor:
35890:+    files = _glob_evidence(evidence_roots, ("staged_live_smoke_*.json", "live_readiness_smoke_*.json"))
35892:+        return FAIL, "missing staged-live-smoke evidence"
35894:+        payload, error = _load_signed_evidence(path, evidence_type="staged_live_smoke")
35902:+    return FAIL, "staged-live-smoke evidence exists but does not prove signed PASS + 17/17 + staged environment"
35955:+    parser = argparse.ArgumentParser(description="Run R3 G1 live-readiness gates.")
35962:+        parser.error("--evidence-root is test-only; production readiness uses canonical evidence roots")
35968:+        print(f"G1 live readiness: {report.status} ({report.passed_gates}/{report.gate_count} gates); staged_smoke={report.staged_smoke_status}")
35972:+        print("live_deploy_authorized: false (operator live-money-deploy-go still required)")
35978:diff --git a/scripts/migrate_forecasts_availability_provenance.py b/scripts/migrate_forecasts_availability_provenance.py
35982:+++ b/scripts/migrate_forecasts_availability_provenance.py
35987:+"""F11.2 schema migration: add forecasts.availability_provenance.
35989:+Adds a typed availability_provenance TEXT column to the forecasts table
35996:+  .venv/bin/python scripts/migrate_forecasts_availability_provenance.py --dry-run
35997:+  .venv/bin/python scripts/migrate_forecasts_availability_provenance.py --apply
35998:+  .venv/bin/python scripts/migrate_forecasts_availability_provenance.py --verify
36016:+    f"CHECK (availability_provenance IS NULL "
36017:+    f"OR availability_provenance IN ({', '.join(repr(v) for v in PROVENANCE_VALUES)}))"
36023:+    return "availability_provenance" in cols
36030:+def _provenance_distribution(conn: sqlite3.Connection) -> dict[str | None, int]:
36035:+            "SELECT availability_provenance, COUNT(*) "
36036:+            "FROM forecasts GROUP BY availability_provenance"
36042:+    print(f"[dry-run] Target DB: {db_path}")
36045:+    print(f"[dry-run] availability_provenance column exists: {_column_exists(conn)}")
36047:+    print(f"[dry-run]   ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT {CHECK_CLAUSE};")
36054:+    print(f"[apply] Target DB: {db_path}")
36058:+            print(f"[apply] availability_provenance already exists; nothing to do.")
36063:+            f"ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT {CHECK_CLAUSE}"
36072:+        print(f"[apply] Column added; all {after_count:,} rows have NULL provenance (expected; backfill via F11.4).")
36078:+    print(f"[verify] Target DB: {db_path}")
36081:+        print(f"[verify] FAIL: availability_provenance column NOT present.")
36083:+    print(f"[verify] availability_provenance column present.")
36084:+    distribution = _provenance_distribution(conn)
36103:+        print(f"DB not found: {db_path}", file=sys.stderr)
36124:-# Transition WU_API_KEY: the K2 security fix removed the hardcoded default
36126:-# WU_API_KEY as an environment variable. The value below is the one
36132:-if [ -z "${WU_API_KEY:-}" ]; then
36133:-    export WU_API_KEY="e1f10a1e78da46f5b10a1e78da96f525"
36134:-    log "WU_API_KEY not in env — exporting legacy value for fillback transition (see task #62)"
36136:+# WU calls require an operator-provided key. Do not embed transition keys in
36138:+: "${WU_API_KEY:?WU_API_KEY must be set in the operator environment before running WU fillback}"
36139:+export WU_API_KEY
36141: log "--- Step A: WU --all --missing-only ---"
36168:+    path that lives at `architecture/`).
36178:+    1 — RED (SEMANTIC_MISMATCH / FILE_MISSING / cited path absent at HEAD)
36276:+        print(f"architecture/*.yaml drift check: {green_n} GREEN, {red_n} RED")
36278:+            print(f"  RED: {r.get('yaml')}: {r.get('cite')} → {r.get('kind')} ({r.get('missing_path', r.get('detail',''))})")
36285:diff --git a/scripts/rebuild_settlements.py b/scripts/rebuild_settlements.py
36289:+++ b/scripts/rebuild_settlements.py
36294:+"""Rebuild high-temperature settlement rows from VERIFIED daily observations.
36296:+This repair helper is intentionally narrow: it writes only high-track settlement
36298:+not fetch external data, infer provider validity, or authorize live deployment.
36310:+from src.contracts.settlement_semantics import SettlementSemantics
36324:+    return sem.assert_settlement_value(raw_value, context="rebuild_settlements")
36327:+def rebuild_settlements(
36333:+    """Rebuild settlement rows from VERIFIED observation highs.
36336:+        conn: Open world DB connection. The caller owns commit/rollback.
36379:+            settlement_value = _round_high_value(
36392:+            INSERT INTO settlements
36393:+            (city, target_date, winning_bin, settlement_value, settlement_source, settled_at,
36395:+             data_version, provenance_json)
36399:+                settlement_value = excluded.settlement_value,
36400:+                settlement_source = excluded.settlement_source,
36406:+                provenance_json = excluded.provenance_json
36411:+                f"{int(settlement_value)}°{str(row['unit']).upper()}",
36412:+                settlement_value,
36418:+                '{"source":"scripts/rebuild_settlements.py","authority":"VERIFIED"}',
36435:+    parser.add_argument("--db", type=Path, default=None, help="World DB path; defaults to configured world DB")
36442:+        summary = rebuild_settlements(
36465: DB="state/zeus-world.db"
36467:-# Transition WU_API_KEY export for the fillback restart after the old
36472:-if [ -z "${WU_API_KEY:-}" ]; then
36473:-    export WU_API_KEY="e1f10a1e78da46f5b10a1e78da96f525"
36475:+# WU calls require an operator-provided key. Do not embed transition keys in
36477:+: "${WU_API_KEY:?WU_API_KEY must be set in the operator environment before running WU backfill}"
36478:+export WU_API_KEY
36504: # K2_struct: forbid bare FROM calibration_pairs outside the allowlist.
36510:             mismatches.append(f"{city.name} settlement unit mismatch: {semantics.measurement_unit} vs {city.settlement_unit}")
36512:             mismatches.append(f"{city.name} settlement precision mismatch: {semantics.precision} vs {expected_precision}")
36513:-        if semantics.rounding_rule != assumptions["settlement"]["rounding_rule"]:
36515:+            assumptions["settlement"].get("rounding_rule_overrides", {}).get(city.name)
36516:+            or assumptions["settlement"]["rounding_rule"]
36530:+Zeus source code root. 16 packages organized by zone (K0-K4), plus cross-cutting types and standalone config. `src/venue/` is the R3 live-venue adapter boundary; `src/ingest/` is the R3 user-channel event ingest boundary.
36543:+"""Decision-time truth with typed availability provenance.
36547:+a typed enum that callers must declare. Backtest purposes refuse provenance
36581:+    provenance: AvailabilityProvenance
36584:+        return self.provenance in _PROMOTION_GRADE
36587:+        return self.provenance not in _PROMOTION_GRADE
36591:+    """Raised when a snapshot's provenance is too soft for the requested purpose."""
36609:+            f"ECONOMICS purpose requires FETCH_TIME or RECORDED provenance; "
36610:+            f"got {truth.provenance.value} on snapshot {truth.snapshot_id}"
36614:+        and truth.provenance is AvailabilityProvenance.RECONSTRUCTED
36617:+            f"SKILL purpose refuses RECONSTRUCTED provenance "
36712:+    "calibration_buckets",
36804:+(src.engine.replay.run_wu_settlement_sweep) with the typed
36847:+    from src.engine.replay import run_wu_settlement_sweep
36849:+    summary = run_wu_settlement_sweep(
36897:diff --git a/src/backtest/training_eligibility.py b/src/backtest/training_eligibility.py
36901:+++ b/src/backtest/training_eligibility.py
36908:+and a SQL fragment so consumers (training rebuilds, backtest queries)
36946:+    f"availability_provenance IN ({_quote_in_clause(SKILL_ELIGIBLE_PROVENANCE)})"
36950:+    f"availability_provenance IN ({_quote_in_clause(ECONOMICS_ELIGIBLE_PROVENANCE)})"
36958:+    provenance: str | AvailabilityProvenance | None,
36961:+    if provenance is None:
36963:+    if isinstance(provenance, str):
36965:+            provenance = AvailabilityProvenance(provenance)
36968:+    truth = DecisionTimeTruth(snapshot_id="eligibility_check", available_at=_DUMMY_TIME, provenance=provenance)
36976:+def is_skill_eligible(provenance: str | AvailabilityProvenance | None) -> bool:
36978:+    return _eligible_via_gate(provenance, BacktestPurpose.SKILL)
36981:+def is_economics_eligible(provenance: str | AvailabilityProvenance | None) -> bool:
36983:+    return _eligible_via_gate(provenance, BacktestPurpose.ECONOMICS)
36984:diff --git a/src/calibration/AGENTS.md b/src/calibration/AGENTS.md
36986:--- a/src/calibration/AGENTS.md
36987:+++ b/src/calibration/AGENTS.md
36989: | `platt.py` | Extended Platt calibrator + bootstrap | HIGH — core calibration engine |
36990: | `manager.py` | Calibration lifecycle, maturity gates | HIGH — controls when calibration applies |
36991: | `store.py` | Persistence of calibration parameters | MEDIUM |
36992:+| `retrain_trigger.py` | Operator-gated retrain/promotion wiring + frozen-replay gate | HIGH — live calibration promotion seam |
36993: | `effective_sample_size.py` | Decision-group calibration sample accounting | MEDIUM |
36994: | `blocked_oos.py` | Blocked out-of-sample calibration evaluation facts | MEDIUM |
37000:+- Retrain corpus reads must be CONFIRMED-only from `venue_trade_facts`; MATCHED/MINED are execution observations, not training truth.
37004:diff --git a/src/calibration/retrain_trigger.py b/src/calibration/retrain_trigger.py
37008:+++ b/src/calibration/retrain_trigger.py
37013:+"""Operator-gated calibration retrain/promotion wiring for R3 F2.
37035:+from src.calibration.store import deactivate_model_v2, save_platt_model_v2
37036:+from src.state.venue_command_repo import load_calibration_trade_facts
37044:+    "evidence/calibration_retrain_decision_*.md"
37092:+                "calibration retrain corpus may consume only CONFIRMED venue_trade_facts"
37212:+        raise CalibrationRetrainGateError("operator_token is required to arm calibration retrain")
37215:+        raise CalibrationRetrainGateError(f"{ENV_FLAG_NAME}=1 is required to arm calibration retrain")
37219:+            f"{OPERATOR_TOKEN_SECRET_ENV} is required to validate calibration retrain operator token"
37254:+        CREATE TABLE IF NOT EXISTS calibration_params_versions (
37276:+        CREATE INDEX IF NOT EXISTS idx_calibration_params_versions_live
37277:+        ON calibration_params_versions(temperature_metric, cluster, season, data_version, input_space, promoted_at, retired_at)
37295:+            "calibration retrain corpus may consume only CONFIRMED venue_trade_facts"
37297:+    rows = load_calibration_trade_facts(conn, states=corpus_filter.states)
37309:+    that the fact belongs to this calibration family.  F2 therefore requires
37310:+    each retrain fact to carry explicit calibration identity in
37311:+    ``raw_payload_json`` (either top-level or under ``calibration_identity``).
37334:+            "confirmed retrain facts missing calibration identity: "
37352:+    nested = payload.get("calibration_identity")
37377:+        INSERT INTO calibration_params_versions (
37417:+    """Gate, replay-check, and promote a candidate calibration version.
37455:+            UPDATE calibration_params_versions
37522: | `edge_context.py` | Edge provenance (source, confidence, costs) | HIGH — INV-12 enforcement |
37525:+| `venue_submission_envelope.py` | Polymarket V2 submission provenance envelope | HIGH — live venue provenance contract |
37529: | `provenance_registry.py` | INV-13 constant registration | HIGH — cascade safety |
37532: - `assert_settlement_value()` MUST gate every DB write of a settlement value — no exceptions
37547: from src.contracts.settlement_semantics import SettlementSemantics
37641:+    neg_risk: bool
37745:+    expected_neg_risk: Optional[bool] = None,
37791:+    if expected_neg_risk is not None and bool(expected_neg_risk) != snapshot.neg_risk:
37793:+            f"intent neg_risk {bool(expected_neg_risk)} != snapshot neg_risk {snapshot.neg_risk}"
37839:     decision_edge: float = 0.0  # T5.a 2026-04-23: field was read at src/execution/executor.py:136,428 but missing from dataclass, latent TypeError on live entry; paired default maintains backward compatibility.
37846:+    executable_snapshot_neg_risk: bool | None = None
37848:+    # production intent boundary so per-event / per-resolution-window /
37932:diff --git a/src/contracts/settlement_semantics.py b/src/contracts/settlement_semantics.py
37934:--- a/src/contracts/settlement_semantics.py
37935:+++ b/src/contracts/settlement_semantics.py
37954:+#   §1.1 #4 + §4.1 #4 (both proponent + opponent endorsed type-encoded HK HKO
37958:+# This block APPENDS a parallel type-encoded settlement-rounding policy. It does
37964:+    """Type-encoded settlement-rounding policy. Replaces YAML antibody for
37965:+    HK HKO truncation vs WMO half-up cross-city mixing with a TypeError at
37969:+    implement `round_to_settlement` + `source_authority`. Mixing policies
37975:+    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
37976:+        """Round a raw temperature to the integer settlement value."""
37980:+        """Return the authority string for this policy (e.g., 'WMO', 'HKO')."""
37984:+    """WMO asymmetric half-up: 74.45 → 74; 74.50 → 75. WU/NOAA/CWA chains.
37994:+    yields systematic settlement drift"). Critic batch_C_review §C4 caught a
38000:+    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
38003:+        # (NOT -4). See settlement_semantics.py:16-27 docstring + docs/reference/
38011:+class HKO_Truncation(SettlementRoundingPolicy):
38012:+    """HKO truncation: 74.99 → 74. Hong Kong settlement chain ONLY.
38015:+    28'). Empirically verified: floor() achieves 14/14 (100%) match on HKO
38016:+    same-source settlement days vs 5/14 (36%) with WMO half-up.
38020:+    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
38024:+        return "HKO"
38031:+    HK markets REQUIRE HKO_Truncation; non-HK markets REQUIRE non-HKO policy.
38041:+    if city_name == "Hong Kong" and not isinstance(policy, HKO_Truncation):
38043:+            f"Hong Kong markets require HKO_Truncation policy; "
38046:+    if city_name != "Hong Kong" and isinstance(policy, HKO_Truncation):
38048:+            f"HKO_Truncation policy is valid for Hong Kong only; "
38051:+    return policy.round_to_settlement(raw_temp_c)
38061:+"""Polymarket V2 submission provenance envelope.
38093:+    """Immutable provenance contract for one Polymarket V2 submission."""
38115:+    neg_risk: bool
38170:+            "neg_risk": self.neg_risk,
38219:+| `cutover_guard.py` | CLOB V2 cutover runtime state machine and live-side-effect gate | Must fail closed; live enablement remains operator-gated |
38222: | `gate_decision.py` | `GateDecision` frozen dataclass + `ReasonCode` enum + `reason_refuted()` — machine-readable gate provenance | `reason_refuted()` returns False for all codes in Phase 1 (conservative); do not add per-code refutation logic without a Phase 2 plan |
38266:+# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z1.yaml; docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
38279:+classification, and live-money integration simulations. Cancel/redemption
38413:+        # Unknown/corrupt state must not accidentally permit live side effects.
38494:+    _validate_live_readiness_evidence(evidence_path)
38498:+def _validate_live_readiness_evidence(evidence_path: Path) -> None:
38501:+    The live-readiness script is intentionally unable to authorize live deploy
38502:+    (`live_deploy_authorized=false`). CutoverGuard therefore still requires the
38505:+    sufficient to flip the live-money switch.
38512:+            f"LIVE_ENABLED evidence must be a JSON live-readiness report: {evidence_path}"
38522:+        "live_deploy_authorized": payload.get("live_deploy_authorized") is False,
38652:+"""Heartbeat supervision for live resting Polymarket orders.
38695:+    """Raised before live resting-order submit when heartbeat is not healthy."""
39148: | `daily_obs_append.py` | Settlement-adjacent daily observation writer | CRITICAL — settlement truth enters here |
39151:+| `forecast_source_registry.py` | Forecast-source registry + operator gates | HIGH — source activation/provenance |
39153:+| `tigge_client.py` | TIGGE ingest stub; construction allowed, fetch operator-gated/dormant | HIGH — source activation seam |
39156: | `forecasts_append.py` | Forecast write family (TIGGE ENS) | HIGH — signal source |
39159:+| `polymarket_client.py` | Market data client and legacy compatibility wrapper; live V2 placement delegates to `src/venue/polymarket_v2_adapter.py` | HIGH — compatibility seam during R3 |
39173: # G10 calibration-fence (2026-04-26, con-nyx NICE-TO-HAVE #4): import from
39174: # canonical location to avoid transitively pulling src.calibration into the
39203:+the provenance tier the derivation carries.
39208:+verified at primary source carry `RECONSTRUCTED` provenance and are
39215:+- NOAA GFS: nco.ncep.noaa.gov production status — completion ~base+4h14m
39240:+    provenance: AvailabilityProvenance
39256:+    provenance (not FETCH_TIME / RECORDED), so consumers that need
39275:+        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
39281:+        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
39287:+        provenance=AvailabilityProvenance.RECONSTRUCTED,
39293:+        provenance=AvailabilityProvenance.RECONSTRUCTED,
39299:+        provenance=AvailabilityProvenance.RECONSTRUCTED,
39322:+    return entry.derive(base_time, lead_day), entry.provenance
39342:+        if entry.provenance is AvailabilityProvenance.DERIVED_FROM_DISSEMINATION
39382:     temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"
39415:+    This keeps TIGGE switch-only wiring dormant behind the forecast-source
39416:+    registry. Gate-closed TIGGE fails before this function is reached; gate-open
39417:+    TIGGE reads only the operator-approved local payload configured on the
39548:+F1 wires forecast-source provenance and dormant source gates without changing
39549:+settlement/source authority, calibration training, or signal mathematics.
39635:+The registry is forecast-source plumbing. It is not settlement-source
39637:+calibration. Experimental sources stay dormant until both operator evidence
39651:+from src.data.tigge_client import TIGGEIngest
39655:+ForecastSourceKind = Literal["forecast_table", "live_ensemble", "experimental_ingest"]
39708:+_TIGGE_OPERATOR_ARTIFACT = (
39747:+        kind="live_ensemble",
39753:+        kind="live_ensemble",
39760:+        ingest_class=TIGGEIngest,
39763:+        operator_decision_artifact=_TIGGE_OPERATOR_ARTIFACT,
39764:+        env_flag_name="ZEUS_TIGGE_INGEST_ENABLED",
39901: #: any drift here silently fragments per-model calibration buckets.
39923:+    availability_provenance: Optional[str] = None  # F11 antibody: must be set, writer raises on None
39954:         if _validate_forecast_temps(high, low, city.settlement_unit):
39956:+        # F11 antibody: derive forecast_issue_time + availability_provenance from
39961:+        issue_time, provenance = derive_availability(source, base_time, lead)
39973:             temp_unit=city.settlement_unit,
39982:+            availability_provenance=provenance.value,
39994:+    availability_provenance
40002:+    # F11 antibody (Q4): writer rejects rows missing availability_provenance.
40005:+        if r.availability_provenance is None or r.forecast_issue_time is None:
40007:+                f"ForecastRow rejected: must carry availability_provenance + "
40021:+            r.availability_provenance,
40106:+            "PolymarketClient._ensure_client() is deprecated; live venue I/O routes "
40114:+        """Lazy init: connect live CLOB I/O through the strict V2 adapter."""
40138:-        logger.info("Polymarket CLOB client initialized (live mode)")
40140:+        logger.info("Polymarket CLOB V2 adapter initialized (live mode)")
40146:         INV-25: When this method raises, _live_order must return a rejected
40155:+                "only for compatibility tests; live preflight uses PolymarketV2Adapter.",
40217:+            "live placement routes through PolymarketV2Adapter.",
40326:         """Fetch a live order's latest exchange status."""
40366:+                "only for compatibility tests; live order queries use PolymarketV2Adapter.",
40381:         """Fetch authoritative live positions from Polymarket's data API."""
40394:+            "live balance queries route through CollateralLedger.",
40410:         """Redeem winning shares for USDC after settlement.
40433:+            "Redeem deferred for condition %s: R1 settlement command ledger is not implemented",
40438:+            "errorCode": "REDEEM_DEFERRED_TO_R1",
40439:+            "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
40471:+"""Dormant TIGGE ingest stub for R3 F3.
40473:+This module wires the TIGGE forecast-source class without performing external
40474:+TIGGE archive I/O. Construction is intentionally safe with the operator gate
40498:+ENV_FLAG_NAME = "ZEUS_TIGGE_INGEST_ENABLED"
40499:+PAYLOAD_PATH_ENV = "ZEUS_TIGGE_PAYLOAD_PATH"
40502:+class TIGGEIngestNotEnabled(RuntimeError):
40503:+    """Raised when TIGGE fetch is attempted while the operator gate is closed."""
40506:+class TIGGEIngestFetchNotConfigured(RuntimeError):
40513:+class TIGGEIngest:
40514:+    """ForecastIngestProtocol-compatible TIGGE adapter stub.
40546:+        """Return a source-stamped TIGGE bundle, or fail closed before I/O."""
40549:+            raise TIGGEIngestNotEnabled(_gate_closed_message())
40555:+                    f"TIGGE payload returned source_id={payload.source_id!r}, "
40580:+        """Report gate health without touching the external TIGGE archive."""
40587:+            message="TIGGE operator gate open" if ok else _gate_closed_message(),
40595:+            raise TIGGEIngestFetchNotConfigured(
40596:+                "TIGGE gate is open but no operator-approved payload is configured. "
40599:+                "local JSON only; it does not perform live TIGGE archive HTTP/GRIB I/O."
40629:+    """Return True only when the registry's TIGGE dual gate is open."""
40645:+        "TIGGE ingest is operator-gated. Required: operator decision artifact at "
40687:+        raise TIGGEIngestFetchNotConfigured(
40688:+            f"TIGGE operator payload is not valid JSON: {path}"
40692:+            f"TIGGE operator payload source_id={payload.get('source_id')!r}, expected {SOURCE_ID!r}"
40720: from src.riskguard.risk_level import RiskLevel
40721: from src.riskguard.riskguard import get_current_level, get_force_exit_review, tick_with_portfolio
40723:@@ -57,7 +63,12 @@ KNOWN_STRATEGIES = {"settlement_capture", "shoulder_sell", "center_buy", "openin
40734:     """DT#2 / INV-19 RED force-exit sweep (Phase 9B).
40853:+                    "M1 RED cancel proxy emission failed for trade_id=%s: %s",
40904: def _risk_allows_new_entries(risk_level: RiskLevel) -> bool:
40905:     return risk_level == RiskLevel.GREEN
40910:         summary["risk_level"] = risk_level.value
40914:+        from src.risk_allocator import refresh_global_allocator
40923:+            ledger={"current_drawdown_pct": _drawdown_pct, "risk_level": risk_level.value},
40996:+        from src.risk_allocator import refresh_global_allocator
41003:+            ledger={"current_drawdown_pct": _drawdown_pct, "risk_level": risk_level.value},
41064:             # Downstream (rebuild_calibration_pairs*) filters by temperature_metric column
41065:             # to route to the correct settlement semantics. Do NOT use members_json without
41087:+        # read so the typed gate from src.backtest.training_eligibility actually
41089:+        # are excluded; backfilled rows whose availability_provenance is
41091:+        # legacy DBs (no availability_provenance column) are tolerated via the
41093:+        # behavior on un-migrated DBs continues unchanged.
41094:+        from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
41102:+                  AND (availability_provenance IS NULL OR {SKILL_ELIGIBLE_SQL})
41115:+| `collateral.py` | Compatibility sell-collateral facade over CollateralLedger | HIGH — fail-closed live exits |
41116:+| `wrap_unwrap_commands.py` | Durable USDC.e↔pUSD command state | HIGH — no live chain side effects in Z4 |
41117:+| `settlement_commands.py` | Durable settlement/redeem command ledger | HIGH — Q-FX-1 gated, crash-recoverable tx hash anchor |
41128: - Live/backtest/shadow separation is explicit; execution code must not reintroduce paper/live split paths
41130:+- Settlement redemption side effects flow through `settlement_commands.py`; do not call adapter redeem paths directly from harvester or collateral code.
41176:@@ -7,8 +7,8 @@ Pure type contract. No I/O, no DB, no side effects. P1.S3 wires the executor
41203:     EXPIRED = "EXPIRED"
41206:     REVIEW_REQUIRED = "REVIEW_REQUIRED"
41228:     EXPIRED = "EXPIRED"
41229:     REVIEW_REQUIRED = "REVIEW_REQUIRED"
41238:     CommandState.REVIEW_REQUIRED,
41243: # REVIEW_REQUIRED can re-enter the system from these.
41248:     CommandState.EXPIRED,
41261:-UNKNOWN, REVIEW_REQUIRED, CANCEL_PENDING) and reconciles each against venue
41272: Chain reconciliation (FILL_CONFIRMED via on-chain settlement evidence) is OUT
41277: Cross-DB note (per INV-30 caveat): venue_commands lives in zeus_trades.db.
41280:     "CANCELLED", "CANCELED", "EXPIRED", "REJECTED",
41470:-                    "recovery: command %s UNKNOWN without venue_order_id u2192 REVIEW_REQUIRED",
41471:+                    "recovery: command %s UNKNOWN without venue_order_id -> REVIEW_REQUIRED",
41490:     REVIEW_REQUIRED are skipped (operator-handoff). Rows without a
41491:-    venue_order_id and in SUBMITTING get an EXPIRED event since recovery
41493:+    venue_order_id and in SUBMITTING get a REVIEW_REQUIRED event since recovery
41496:     DB connection: if conn is None, opens get_trade_connection_with_world()
41509:+``venue_commands`` row, and no live venue submit/cancel/redeem side effects are
41551:+        "REVIEW_REQUIRED",
41905:+    if state in {"FILLED", "CANCELLED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}:
42226:+    """Fail before command persistence or SDK contact when cutover is not live."""
42246:+def _assert_risk_allocator_allows_submit(intent: ExecutionIntent) -> None:
42247:+    """Fail before command persistence or SDK contact when A2 allocator denies risk."""
42248:+    from src.risk_allocator import assert_global_allocation_allows
42253:+def _assert_risk_allocator_allows_exit_submit() -> None:
42255:+    from src.risk_allocator import assert_global_submit_allows
42260:+def _select_risk_allocator_order_type(conn: sqlite3.Connection, snapshot_id: str) -> str:
42268:+    from src.risk_allocator import select_global_order_type
42430:+        neg_risk=snapshot.neg_risk,
42467:+    executable_snapshot_neg_risk: bool | None = None
42533:+    executable_snapshot_neg_risk: bool | None = None,
42581:+        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
42595:     return _live_order(
42605:+    executable_snapshot_neg_risk: bool | None = None,
42607:     """Build the explicit executor contract for a live sell/exit order."""
42616:+        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
42634:+    _assert_risk_allocator_allows_exit_submit()
42643:+        order_type = _select_risk_allocator_order_type(conn, intent.executable_snapshot_id)
42745:+                expected_neg_risk=intent.executable_snapshot_neg_risk,
42751:+                    event_type="REVIEW_REQUIRED",
42766:+                    command_state="REVIEW_REQUIRED",
42952:@@ -710,6 +1208,9 @@ def _live_order(
42962:@@ -754,6 +1255,9 @@ def _live_order(
42966:+    _assert_risk_allocator_allows_submit(intent)
42971:     # Derive a synthetic decision_id when caller hasn't supplied a real one.
42972:@@ -787,12 +1291,20 @@ def _live_order(
42976:+        order_type = _select_risk_allocator_order_type(conn, intent.executable_snapshot_id)
42994:@@ -810,11 +1322,49 @@ def _live_order(
43009:+                "_live_order: same economic intent is already unresolved as "
43044:@@ -825,15 +1375,38 @@ def _live_order(
43051:+                expected_neg_risk=intent.executable_snapshot_neg_risk,
43083:@@ -868,7 +1441,43 @@ def _live_order(
43111:+                    "_live_order: SUBMIT_REJECTED append_event failed after client init "
43128:@@ -902,6 +1511,43 @@ def _live_order(
43155:+                    "_live_order: SUBMIT_REJECTED append_event failed after generic "
43172:@@ -919,34 +1565,44 @@ def _live_order(
43203:-                    "_live_order: SUBMIT_UNKNOWN append_event failed after SDK exception "
43204:+                    "_live_order: SUBMIT_TIMEOUT_UNKNOWN append_event failed after SDK exception "
43223:@@ -980,6 +1636,69 @@ def _live_order(
43249:+                    "_live_order: SUBMIT_REJECTED (success_false) append_event failed "
43276:+                    "_live_order: SUBMIT_REJECTED (missing_order_id) append_event failed "
43293:@@ -987,7 +1706,11 @@ def _live_order(
43317:+    executable_snapshot_neg_risk: bool | None = None,
43328:+            executable_snapshot_neg_risk=executable_snapshot_neg_risk,
43332:@@ -388,7 +396,10 @@ def _execute_live_exit(
43344:@@ -411,13 +422,82 @@ def _execute_live_exit(
43433:@@ -435,6 +515,7 @@ def _execute_live_exit(
43441:@@ -445,6 +526,7 @@ def _execute_live_exit(
43449:@@ -584,6 +666,57 @@ def _execute_live_exit(
43461:+    M4 exit lifecycle is upstream of executor's U1 snapshot gate.  When a DB
43477:+            SELECT snapshot_id, min_tick_size, min_order_size, neg_risk
43500:+        "executable_snapshot_neg_risk": bool(row["neg_risk"]),
43513:+"""R3 M4 cancel/replace safety for live exits.
43520:+- NOT_CANCELED -> ``CANCEL_FAILED`` -> command state ``REVIEW_REQUIRED``
43521:+- UNKNOWN -> ``CANCEL_REPLACE_BLOCKED`` -> command state ``REVIEW_REQUIRED``
43541:+  command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
43549:+    {"CANCELLED", "FILLED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}
43567:+        "REVIEW_REQUIRED",
43827:+    if state == "REVIEW_REQUIRED" and latest and latest[0] == "CANCEL_REPLACE_BLOCKED":
43887:+    The callable is injected so tests never contact a live venue.  Exceptions
44008:                 strategy_tracker.record_settlement(closed)
44012:+        # settlement commands. Z4 may wire the fail-closed edge but must not
44013:+        # perform direct live redemption side effects.
44032:+                from src.execution.settlement_commands import request_redeem
44044:+                    "pUSD redemption for %s (condition=%s) recorded in R1 settlement command ledger: %s",
44055:diff --git a/src/execution/settlement_commands.py b/src/execution/settlement_commands.py
44059:+++ b/src/execution/settlement_commands.py
44061:+"""Durable settlement/redeem command ledger for R3 R1.
44064:+live chain submission.  The ledger records intent, submission, tx-hash, terminal
44066:+``REDEEM_TX_HASHED`` anchor during reconciliation.
44088:+CREATE TABLE IF NOT EXISTS settlement_commands (
44091:+    'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
44092:+    'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED'
44108:+CREATE INDEX IF NOT EXISTS idx_settlement_commands_state
44109:+  ON settlement_commands (state, requested_at);
44110:+CREATE INDEX IF NOT EXISTS idx_settlement_commands_condition
44111:+  ON settlement_commands (condition_id, market_id);
44112:+CREATE UNIQUE INDEX IF NOT EXISTS ux_settlement_commands_active_condition_asset
44113:+  ON settlement_commands (condition_id, market_id, payout_asset)
44114:+  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');
44116:+CREATE TABLE IF NOT EXISTS settlement_command_events (
44118:+  command_id TEXT NOT NULL REFERENCES settlement_commands(command_id),
44125:+CREATE INDEX IF NOT EXISTS idx_settlement_command_events_command
44126:+  ON settlement_command_events (command_id, recorded_at);
44131:+    REDEEM_INTENT_CREATED = "REDEEM_INTENT_CREATED"
44132:+    REDEEM_SUBMITTED = "REDEEM_SUBMITTED"
44133:+    REDEEM_TX_HASHED = "REDEEM_TX_HASHED"
44134:+    REDEEM_CONFIRMED = "REDEEM_CONFIRMED"
44135:+    REDEEM_FAILED = "REDEEM_FAILED"
44136:+    REDEEM_RETRYING = "REDEEM_RETRYING"
44137:+    REDEEM_REVIEW_REQUIRED = "REDEEM_REVIEW_REQUIRED"
44141:+    SettlementState.REDEEM_CONFIRMED,
44142:+    SettlementState.REDEEM_FAILED,
44143:+    SettlementState.REDEEM_REVIEW_REQUIRED,
44147:+    SettlementState.REDEEM_INTENT_CREATED,
44148:+    SettlementState.REDEEM_RETRYING,
44164:+    """Base error for invalid settlement command operations."""
44168:+    """Raised for illegal settlement command transitions."""
44171:+def init_settlement_command_schema(conn: sqlite3.Connection) -> None:
44190:+    ``REDEEM_REVIEW_REQUIRED`` for operator classification.
44207:+    init_settlement_command_schema(conn)
44211:+        SELECT command_id FROM settlement_commands
44215:+           AND state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED')
44229:+        SettlementState.REDEEM_REVIEW_REQUIRED
44231:+        else SettlementState.REDEEM_INTENT_CREATED
44243:+                INSERT INTO settlement_commands (
44295:+    The durable ``REDEEM_SUBMITTED`` event is committed before adapter contact.
44296:+    If the adapter returns a tx hash, ``REDEEM_TX_HASHED`` becomes the recovery
44308:+    init_settlement_command_schema(conn)
44325:+                SettlementState.REDEEM_SUBMITTED,
44341:+                    SettlementState.REDEEM_RETRYING,
44348:+            return SettlementResult(command_id, SettlementState.REDEEM_RETRYING, error_payload=error_payload)
44353:+                SettlementState.REDEEM_REVIEW_REQUIRED
44354:+                if raw_payload.get("errorCode") == "REDEEM_DEFERRED_TO_R1"
44355:+                else SettlementState.REDEEM_FAILED
44374:+        state_after = SettlementState.REDEEM_TX_HASHED if tx_hash else SettlementState.REDEEM_REVIEW_REQUIRED
44406:+    init_settlement_command_schema(conn)
44409:+        SELECT * FROM settlement_commands
44413:+        (SettlementState.REDEEM_TX_HASHED.value,),
44426:+            state_after = SettlementState.REDEEM_CONFIRMED
44429:+            state_after = SettlementState.REDEEM_FAILED
44460:+    init_settlement_command_schema(conn)
44465:+    init_settlement_command_schema(conn)
44467:+        rows = conn.execute("SELECT * FROM settlement_commands ORDER BY requested_at, command_id").fetchall()
44471:+            "SELECT * FROM settlement_commands WHERE state = ? ORDER BY requested_at, command_id",
44478:+    row = conn.execute("SELECT * FROM settlement_commands WHERE command_id = ?", (command_id,)).fetchone()
44501:+        UPDATE settlement_commands
44536:+        INSERT INTO settlement_command_events (
44546:+    name = f"settlement_cmd_{uuid.uuid4().hex}"
44670:+Z4 models request/tx/confirmation/failure state only. It does not submit live
44787:+    Z4 intentionally does not perform live chain reads/writes here. R1/G1 can
44922:+| `polymarket_user_channel.py` | R3 M3 Polymarket authenticated user WebSocket ingestor, gap status, and U2 fact append bridge | HIGH — live venue truth ingest |
44937:+- Writing directly to provenance tables instead of using `venue_command_repo` append APIs.
45123:+    Executor-created live commands store a short runtime trade id in
45124:+    ``position_id`` for operator correlation and thread the durable DB
45470:+        raise WSDependencyMissing("websockets package is required for live M3 user-channel ingest") from exc
45489:+    Disabled by default so M3 adds no live WebSocket side effect until an
45497:+    if _user_channel_thread is not None and _user_channel_thread.is_alive():
45537:+    """Post the Polymarket venue heartbeat required for live resting orders."""
45595:     # visible. See _assert_live_safe_strategies_or_exit() docstring above.
45596:     _assert_live_safe_strategies_or_exit()
45621:diff --git a/src/risk_allocator/AGENTS.md b/src/risk_allocator/AGENTS.md
45625:+++ b/src/risk_allocator/AGENTS.md
45627:+# src/risk_allocator AGENTS
45643:+- This package may block, reduce-only, or summarize risk; it must never submit,
45644:+  cancel, redeem, or mutate production DB/state artifacts.
45652:diff --git a/src/risk_allocator/__init__.py b/src/risk_allocator/__init__.py
45656:+++ b/src/risk_allocator/__init__.py
45663:+from src.risk_allocator.governor import (
45708:diff --git a/src/risk_allocator/governor.py b/src/risk_allocator/governor.py
45712:+++ b/src/risk_allocator/governor.py
45721:+cancels, redeems, mutates production DB/state artifacts, or authorizes cutover.
45735:+from src.riskguard.risk_level import RiskLevel
45797:+    risk_level: RiskLevel = RiskLevel.GREEN
45810:+            "risk_level": self.risk_level.value,
45940:+        return governor_state.risk_level in {RiskLevel.DATA_DEGRADED, RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED}
46012:+        risk_level = _coerce_risk_level(getattr(ledger, "risk_level", _mapping_get(ledger, "risk_level", RiskLevel.GREEN)))
46031:+            risk_level=risk_level,
46186:+def load_cap_policy(path: str | Path = "config/risk_caps.yaml") -> CapPolicy:
46317:+    return bool(getattr(intent, "reduce_only", False) or getattr(intent, "intent_kind", "") in {"EXIT", "SELL", "REDUCE_ONLY"})
46327:+def _coerce_risk_level(value: RiskLevel | str | Any) -> RiskLevel:
46458:         shared helper `apply_settlement_rounding` in settlement_semantics to
46461:-        return apply_settlement_rounding(values, self._round_fn, self._precision)
46462:+        return apply_settlement_rounding(
46485:             self.settlement_semantics,
46495:+| `collateral_ledger.py` | pUSD/CTF collateral snapshot + reservations | HIGH — live pre-submit fail-closed truth |
46498: | `portfolio_loader_policy.py` | DB-vs-fallback load discipline | HIGH — truth source selection |
46501: - **DB commits must precede JSON export writes.** The canonical truth is the
46502:   DB. JSON/status files are derived exports that must trail, never lead.
46507:   risk policy resolution, and performance slicing flows through `strategy_key`.
46546:+    {"CANCELED", "CANCELLED", "FILLED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}
46581:+    """Raised when live submit preflight lacks spendable collateral/inventory."""
47167: ZEUS_DB_PATH = STATE_DIR / "zeus.db"  # LEGACY — remove after Phase 4
47172:+def init_provenance_projection_schema(conn: sqlite3.Connection) -> None:
47173:+    """Create U2 raw-provenance projection tables and legacy migrations.
47177:+    these tables, but they must not mutate historical provenance.
47203:+          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
47237:+            'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED'
47340:+        CREATE TABLE IF NOT EXISTS provenance_envelope_events (
47342:+          subject_type TEXT NOT NULL CHECK (subject_type IN ('command','order','trade','lot','settlement','wrap_unwrap','heartbeat')),
47354:+        CREATE INDEX IF NOT EXISTS idx_envelope_events_subject ON provenance_envelope_events (subject_type, subject_id, observed_at);
47356:+        CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_update
47357:+        BEFORE UPDATE ON provenance_envelope_events
47359:+          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
47362:+        CREATE TRIGGER IF NOT EXISTS provenance_envelope_events_no_delete
47363:+        BEFORE DELETE ON provenance_envelope_events
47365:+          SELECT RAISE(ABORT, 'provenance_envelope_events is append-only');
47390:+            availability_provenance TEXT
47391:+                CHECK (availability_provenance IS NULL
47392:+                       OR availability_provenance IN ('derived_dissemination', 'fetch_time', 'reconstructed', 'recorded')),
47405:+            -- submission provenance envelope.
47416:+    # R3 M4 exit mutex DDL lives here to keep DB initialization independent of
47422:+          command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
47429:+    # DB initialization does not import the execution sweep module.
47454:+    init_provenance_projection_schema(conn)
47456:+    # import src.execution during DB initialization. The execution module owns
47480:+    # R3 R1 settlement/redeem command ledger.  Keep DDL in the schema owner so
47481:+    # DB initialization does not import src.execution during startup.
47483:+        CREATE TABLE IF NOT EXISTS settlement_commands (
47486:+            'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
47487:+            'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED'
47503:+        CREATE INDEX IF NOT EXISTS idx_settlement_commands_state
47504:+          ON settlement_commands (state, requested_at);
47505:+        CREATE INDEX IF NOT EXISTS idx_settlement_commands_condition
47506:+          ON settlement_commands (condition_id, market_id);
47507:+        CREATE UNIQUE INDEX IF NOT EXISTS ux_settlement_commands_active_condition_asset
47508:+          ON settlement_commands (condition_id, market_id, payout_asset)
47509:+          WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');
47511:+        CREATE TABLE IF NOT EXISTS settlement_command_events (
47513:+          command_id TEXT NOT NULL REFERENCES settlement_commands(command_id),
47520:+        CREATE INDEX IF NOT EXISTS idx_settlement_command_events_command
47521:+          ON settlement_command_events (command_id, recorded_at);
47525:     for col in ["entry_alpha_usd", "execution_slippage_usd", "exit_timing_usd", "risk_throttling_usd", "settlement_edge_usd"]:
47528:     # state/scheduler_jobs_health.json). ALTER path catches legacy DBs without
47529:     # disturbing fresh DBs (OperationalError on duplicate-column is swallowed).
47545:+    # inserts availability_provenance (D4 antibody). Same pattern as REOPEN-1 above:
47546:+    # CREATE TABLE adds the column for fresh DBs, this ALTER catches legacy DBs.
47547:+    # The CHECK constraint can't be added via ALTER in SQLite, so legacy DBs run
47548:+    # without the DB-level enum enforcement; the writer-level assertion at
47549:+    # forecasts_append.py:283-288 still rejects bad values. Fresh DBs get both.
47551:+        conn.execute("ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT;")
47555:+    # U1: legacy trade DBs predate the executable snapshot citation. SQLite
47556:+    # cannot add a NOT NULL column without a table rebuild, so old DBs get the
47651:+          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
47686:+          rfqe, neg_risk, orderbook_top_bid, orderbook_top_ask,
47696:+          :rfqe, :neg_risk, :orderbook_top_bid, :orderbook_top_ask,
47774:+        "neg_risk": int(snapshot.neg_risk),
47812:+        neg_risk=bool(row["neg_risk"]),
47890:     ("INTENT_CREATED", "REVIEW_REQUIRED"):    "REVIEW_REQUIRED",
47897:+    ("SNAPSHOT_BOUND", "REVIEW_REQUIRED"):     "REVIEW_REQUIRED",
47899:+    ("SIGNED_PERSISTED", "REVIEW_REQUIRED"):   "REVIEW_REQUIRED",
47907:+    ("POSTING", "REVIEW_REQUIRED"):            "REVIEW_REQUIRED",
47912:+    ("POST_ACKED", "EXPIRED"):                 "EXPIRED",
47913:+    ("POST_ACKED", "REVIEW_REQUIRED"):         "REVIEW_REQUIRED",
47922:     ("SUBMITTING", "REVIEW_REQUIRED"):        "REVIEW_REQUIRED",
47933:     ("UNKNOWN", "EXPIRED"):                   "EXPIRED",
47934:     ("UNKNOWN", "REVIEW_REQUIRED"):           "REVIEW_REQUIRED",
47942:+    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "EXPIRED"):               "EXPIRED",
47943:+    ("SUBMIT_UNKNOWN_SIDE_EFFECT", "REVIEW_REQUIRED"):       "REVIEW_REQUIRED",
47952:+    ("CANCEL_PENDING", "CANCEL_FAILED"):      "REVIEW_REQUIRED",
47953:+    ("CANCEL_PENDING", "CANCEL_REPLACE_BLOCKED"): "REVIEW_REQUIRED",
47954:     ("CANCEL_PENDING", "EXPIRED"):            "EXPIRED",
47955:     ("CANCEL_PENDING", "REVIEW_REQUIRED"):    "REVIEW_REQUIRED",
47971:+        "EXPIRED",
47989:+    {"command", "order", "trade", "lot", "settlement", "wrap_unwrap", "heartbeat"}
48134:+              tick_size, min_order_size, neg_risk, fee_details_json,
48144:+              :tick_size, :min_order_size, :neg_risk, :fee_details_json,
48172:+                "neg_risk": int(envelope.neg_risk),
48206:+    expected_neg_risk: bool | None = None,
48226:+        expected_neg_risk=expected_neg_risk,
48277:+        _append_command_provenance_event(
48309:+        raise ValueError("venue command requires provenance envelope_id")
48328:+            "venue command token_id does not match provenance envelope selected_outcome_token_id"
48331:+        raise ValueError("venue command side does not match provenance envelope side")
48333:+        raise ValueError("venue command price does not match provenance envelope price")
48335:+        raise ValueError("venue command size does not match provenance envelope size")
48350:+                        f"provenance envelope {field} does not match executable snapshot"
48372:+    expected_neg_risk: bool | None,
48399:+        expected_neg_risk=expected_neg_risk,
48428:+        _append_command_provenance_event(
48456:+def _append_command_provenance_event(
48464:+    return append_provenance_event(
48476:+def append_provenance_event(
48489:+    """Append an immutable U2 provenance-envelope event."""
48509:+            table="provenance_envelope_events",
48516:+            INSERT INTO provenance_envelope_events (
48595:+        append_provenance_event(
48686:+        append_provenance_event(
48787:+        append_provenance_event(
48801:+def load_calibration_trade_facts(
48806:+    """Return only CONFIRMED trade facts for calibration/retraining.
48809:+    training truth. Explicitly asking for any state except CONFIRMED fails
48810:+    closed instead of returning polluted calibration inputs.
48815:+        raise ValueError("calibration training may consume only CONFIRMED venue_trade_facts")
48886:-            "WHERE state IN ('SUBMITTING', 'UNKNOWN', 'REVIEW_REQUIRED', 'CANCEL_PENDING')"
48998:+The suite is deliberately evidence-only: it computes replay/paper/live-shadow
49000:+production DB, or authorizes live strategy promotion by itself.
49018:+    LIVE = "live"
49039:+    replay, fake-paper, or live-shadow evidence providers. This object is not a
49049:+    failed_settlement_cost: float = 0.0
49059:+    capital_at_risk: float = 1.0
49068:+            - self.failed_settlement_cost
49093:+    time_to_resolution_risk: float
49098:+    calibration_error_vs_market_implied: float
49105:+    failed_settlement_cost: float = 0.0
49116:+            "failed_settlement": self.failed_settlement_cost,
49131:+    max_calibration_error_vs_market_implied: float = 0.2
49153:+  environment TEXT NOT NULL CHECK (environment IN ('replay','paper','shadow','live')),
49164:+    """Compute replay/paper/shadow metrics and gate live promotion.
49166:+    `evaluate_live_shadow` consumes preloaded shadow corpora only. It is not a
49167:+    live adapter and intentionally cannot submit/cancel/redeem venue orders.
49204:+    def evaluate_live_shadow(self, strategy_key: str, capital_cap_micro: int, duration_hours: int) -> StrategyMetrics:
49216:+                        message="live-shadow benchmark requires preloaded read-only shadow evidence",
49293:+                time_to_resolution_risk=0.0,
49298:+                calibration_error_vs_market_implied=0.0,
49308:+        failed = sum(item.failed_settlement_cost for item in observations)
49310:+        calibration_errors = [
49323:+            time_to_resolution_risk=_capital_weighted_resolution_risk(observations),
49328:+            calibration_error_vs_market_implied=fmean(calibration_errors) if calibration_errors else 0.0,
49335:+            failed_settlement_cost=failed,
49354:+        if metrics.calibration_error_vs_market_implied > threshold.max_calibration_error_vs_market_implied:
49355:+            reasons.append(f"{prefix}: calibration error above threshold")
49392:+            failed_settlement_cost=size * 0.01 if is_failed else 0.0,
49401:+            capital_at_risk=max(size, 1.0),
49405:+def _capital_weighted_resolution_risk(observations: Sequence[BenchmarkObservation]) -> float:
49406:+    total_capital = sum(max(item.capital_at_risk, 0.0) for item in observations)
49409:+    return sum(item.time_to_resolution_hours * max(item.capital_at_risk, 0.0) for item in observations) / total_capital
49464:+compute alpha, place orders, or authorize live promotion.
49502:+from .neg_risk_basket import NegRiskBasket
49551:diff --git a/src/strategy/candidates/neg_risk_basket.py b/src/strategy/candidates/neg_risk_basket.py
49555:+++ b/src/strategy/candidates/neg_risk_basket.py
49567:+        super().__init__(CandidateMetadata("neg_risk_basket", "candidate_stub", "Stub for future negative-risk basket benchmarking."))
49632:+is absent instead of silently fetching live data.
49702:         shared helper `apply_settlement_rounding` in settlement_semantics to
49705:-        return apply_settlement_rounding(values, self._round_fn, self._precision)
49706:+        return apply_settlement_rounding(
49729:+journal → `VenueSubmissionEnvelope` provenance → SDK/API side effect.
49735:+| `polymarket_v2_adapter.py` | Polymarket CLOB V2 adapter, shared adapter protocol, and SDK boundary | CRITICAL — live-money external side effects |
49740:+- Adapter code may import `py_clob_client_v2`; other live source modules must
49742:+- Paper/live parity fakes must implement `PolymarketV2AdapterProtocol`; fake
49743:+  behavior belongs in test-only fakes, not production paper/live split paths.
49744:+- Pin provenance at `VenueSubmissionEnvelope`, not a specific SDK call shape.
49754:+- Treating a green SDK mock as proof of production V2 readiness.
49755:+- Calling SDK `post_order` without a complete provenance envelope.
49772:+pins provenance in VenueSubmissionEnvelope while tolerating one-step and
49858:+    """Shared live/paper venue adapter contract.
49861:+    call surface as the live V2 adapter without credentials or network I/O.
50042:+            neg_risk=bool(_snapshot_attr(snapshot, "neg_risk")),
50081:+            options = SimpleNamespace(tick_size=str(envelope.tick_size), neg_risk=envelope.neg_risk)
50250:+            "errorCode": "REDEEM_DEFERRED_TO_R1",
50251:+            "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
50342:+                neg_risk=bool(None),
50402:+            neg_risk=sdk_snapshot.neg_risk,
50419:+        neg_risk_fn = getattr(client, "get_neg_risk", None)
50421:+        if not callable(neg_risk_fn):
50422:+            raise V2AdapterError("SDK client does not expose get_neg_risk for legacy submit compatibility")
50430:+            neg_risk=bool(neg_risk_fn(token_id)),
50648:+| `fakes/` | Test-only fake venue/runtime doubles; must not import credentials or perform live I/O |
50649:+| `integration/` | Cross-module integration antibodies such as R3 T1 paper/live parity scenarios |
50686:+def r3_default_risk_allocator_for_unit_tests():
50687:+    """Keep legacy live-executor unit tests focused on their targeted guard.
50693:+    individual risk tests to call ``clear_global_allocator()`` and assert the
50699:+    from src.risk_allocator import (
50743:+"""Test fakes for venue/live-money parity suites."""
50753:+"""Fake Polymarket V2 venue used by T1 paper/live parity tests.
50756:+surface consumed by Zeus while avoiding credentials, network, production DB
50757:+mutation, and live venue side effects.
50832:+    The fake mirrors live adapter result dataclasses and envelope shapes. Failure
51055:+            "errorCode": "REDEEM_DEFERRED_TO_R1",
51084:+                neg_risk=False,
51151:diff --git a/tests/integration/test_p0_live_money_safety.py b/tests/integration/test_p0_live_money_safety.py
51155:+++ b/tests/integration/test_p0_live_money_safety.py
51158:+# Purpose: R3 T1 paper/live parity P0 safety scenario antibodies.
51159:+# Reuse: Run before fake/live parity, adapter protocol, or live-readiness gate changes.
51163:+"""R3 T1 P0 live-money safety scenarios against FakePolymarketVenue."""
51185:+    neg_risk: bool = False
51253:+            neg_risk=envelope.neg_risk,
51285:+        expected_neg_risk=envelope.neg_risk,
51467:+def test_paper_and_live_produce_identical_journal_event_shapes(tmp_path):
51476:+            return {"success": True, "orderID": "live-ord-1", "status": "LIVE"}
51480:+    live = PolymarketV2Adapter(
51487:+        sdk_version="fake-live-sdk",
51489:+    paper = FakePolymarketVenue(funder_address="0xfake-funder", sdk_version="fake-live-sdk")
51491:+    live_envelope = live.create_submission_envelope(_intent(), ScenarioSnapshot(), "GTC")
51493:+    live_result = live.submit(live_envelope)
51495:+    live_journal_shape = _persist_submit_journal_shape(live_result, prefix="live")
51499:+    assert set(live_result.envelope.to_dict()) == set(paper_result.envelope.to_dict())
51500:+    assert set(json.loads(live_result.envelope.raw_response_json or "{}")) == set(
51503:+    assert live_journal_shape == paper_journal_shape
51528:+import src.risk_allocator as risk_allocator
51531: from src.riskguard.risk_level import RiskLevel
51546:+        risk_allocator,
51580:+# Authority basis: R3 F1 forecast provenance wiring + historical backfill packet.
51614:-        INSERT INTO settlements (city, target_date, settlement_value)
51616:+        INSERT INTO settlements
51617:+        (city, target_date, settlement_value, temperature_metric,
51629:-    replay_module.run_replay("2026-04-03", "2026-04-03", mode="wu_settlement_sweep")
51633:+        mode="wu_settlement_sweep",
51741:+        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
51752:+        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
51762:+        provenance=AvailabilityProvenance.RECONSTRUCTED,
51773:+        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
51782:+        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
51791:+        provenance=AvailabilityProvenance.RECONSTRUCTED,
51802:+        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
51807:+def test_diagnostic_purpose_accepts_all_provenance_tiers():
51810:+        truth = DecisionTimeTruth(snapshot_id="s1", available_at=base, provenance=prov)
51912:diff --git a/tests/test_backtest_training_eligibility.py b/tests/test_backtest_training_eligibility.py
51916:+++ b/tests/test_backtest_training_eligibility.py
51921:+"""F11.5 antibody: training-eligibility filter rejects RECONSTRUCTED + NULL."""
51928:+from src.backtest.training_eligibility import (
51978:+# SQL fragment antibodies (executed against an in-memory DB)
51983:+def db_with_mixed_provenance():
51989:+            availability_provenance TEXT
52001:+        "INSERT INTO forecasts (id, availability_provenance) VALUES (?, ?)",
52009:+def test_skill_sql_filter_includes_fetch_time_recorded_derived(db_with_mixed_provenance):
52010:+    rows = db_with_mixed_provenance.execute(
52016:+def test_skill_sql_filter_excludes_reconstructed_and_null(db_with_mixed_provenance):
52017:+    rows = db_with_mixed_provenance.execute(
52018:+        f"SELECT id FROM forecasts WHERE NOT ({SKILL_ELIGIBLE_SQL}) OR availability_provenance IS NULL"
52025:+def test_economics_sql_filter_includes_only_fetch_time_recorded(db_with_mixed_provenance):
52026:+    rows = db_with_mixed_provenance.execute(
52032:+def test_economics_sql_filter_strictly_subset_of_skill(db_with_mixed_provenance):
52035:+        for r in db_with_mixed_provenance.execute(
52041:+        for r in db_with_mixed_provenance.execute(
52056:diff --git a/tests/test_calibration_retrain.py b/tests/test_calibration_retrain.py
52060:+++ b/tests/test_calibration_retrain.py
52066:+# Purpose: Lock R3 F2 operator-gated calibration retrain/promotion wiring.
52067:+# Reuse: Run when changing calibration retrain gates, corpus filters, frozen-replay promotion, or CONFIRMED trade-fact training seams.
52068:+"""R3 F2 tests for operator-gated calibration retrain wiring."""
52079:+from src.calibration.retrain_trigger import (
52174:+        '{"calibration_identity":'
52211:+    path = root / "docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/calibration_retrain_decision_2026-04-27.md"
52213:+    path.write_text("operator approved calibration retrain test\n")
52225:+def _token(secret: str = "unit-calibration-secret", operator_id: str = "operator", nonce: str = "nonce-123456") -> str:
52231:+def _armed_env(secret: str = "unit-calibration-secret") -> dict[str, str]:
52256:+    wrong_path = tmp_path / "operator_decisions" / "calibration_retrain_decision_2026-04-27.md"
52265:+    assert armed.evidence_path.endswith("calibration_retrain_decision_2026-04-27.md")
52299:+def test_confirmed_corpus_is_filtered_by_calibration_identity(tmp_path):
52324:+    with pytest.raises(UnsafeCorpusFilter, match="missing calibration identity"):
52349:+    row = conn.execute("SELECT frozen_replay_status, promoted_at FROM calibration_params_versions").fetchone()
52375:+    row = conn.execute("SELECT frozen_replay_status, promoted_at, confirmed_trade_count FROM calibration_params_versions").fetchone()
52381:+def test_frozen_replay_pass_replaces_existing_live_platt_row(tmp_path):
52400:+    live_rows = conn.execute(
52407:+    assert len(live_rows) == 1
52408:+    assert live_rows[0]["param_A"] == pytest.approx(1.1)
52409:+    assert live_rows[0]["param_B"] == pytest.approx(-0.02)
52410:+    assert live_rows[0]["param_C"] == pytest.approx(0.3)
52411:+    assert live_rows[0]["n_samples"] == 15
52412:+    assert live_rows[0]["authority"] == "VERIFIED"
52414:+        "SELECT frozen_replay_status, promoted_at FROM calibration_params_versions"
52442:+        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'calibration_params_versions'"
52446:+        count = conn.execute("SELECT COUNT(*) FROM calibration_params_versions").fetchone()[0]
52469:+        "SELECT retired_at, promoted_at FROM calibration_params_versions ORDER BY version_id"
52481:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
52593:+    executable_snapshot_neg_risk: bool | None = None,
52609:+        executable_snapshot_neg_risk=executable_snapshot_neg_risk,
52652:+                neg_risk=False,
52668:+        "executable_snapshot_neg_risk": False,
52781:+@pytest.mark.parametrize("terminal_state", ["CANCELLED", "CANCELED", "FILLED", "EXPIRED"])
52886:+    from src.execution.executor import _live_order
52904:+            _live_order("z4-buy-block", _buy_intent(size_usd=10.0), 20.0, conn=conn, decision_id="z4-buy")
52977:+    from src.execution.executor import _live_order
52997:+        result = _live_order(
53057:+    from src.execution.executor import _live_order
53091:+        result = _live_order(
53264:+    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"
53282:+    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"
53343:+            neg_risk=False,
53399:+            neg_risk=False,
53618:+        neg_risk=False,
53651:+        neg_risk=False,
53748:+    assert get_command(conn, "cmd-m1")["state"] == "REVIEW_REQUIRED"
53789:@@ -9,6 +11,8 @@ Uses in-memory DB; mocks PolymarketClient.get_order.
53862:+            neg_risk=False,
53918:+            neg_risk=False,
53944:@@ -373,6 +373,9 @@ def test_settlement_semantics_matches_city_metadata():
53946:         if city.settlement_source_type == "wu_icao":
53947:             assert sem.resolution_source == f"WU_{city.wu_station}"
53948:+        elif city.settlement_source_type == "hko":
53949:+            assert sem.resolution_source == "HKO_HQ"
53952:             # Non-WU sources use source_type prefix
53953:             assert sem.resolution_source == f"{city.settlement_source_type}_{city.wu_station}"
53961:+# Purpose: Lock R3 Z1 CutoverGuard fail-closed live-money gate behavior.
53965:+# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z1.yaml; docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
53969:+advance from the corrected CLOB V2 plan into live-money runtime changes.
53992:+    '"staged_smoke_status":"PASS","live_deploy_authorized":false}'
54067:+def test_live_enabled_rejects_unsigned_operator_token(tmp_path):
54071:+    evidence = tmp_path / "live_readiness_report_2026-04-27.json"
54092:+def test_live_enabled_rejects_generic_operator_note_evidence(tmp_path):
54117:+def test_live_enabled_rejects_failing_readiness_report(tmp_path):
54121:+    evidence = tmp_path / "live_readiness_report_2026-04-27.json"
54124:+        '"staged_smoke_status":"FAIL","live_deploy_authorized":false}'
54213:+def test_live_enabled_allows_normal_v2_operation(tmp_path):
54217:+    evidence = tmp_path / "live_readiness_report_2026-04-27.json"
54239:+def test_live_enabled_transition_requires_operator_evidence(tmp_path):
54322:+        executor._live_order(
54337:+    from src.riskguard.risk_level import RiskLevel
54384:+@pytest.mark.skip(reason="T1/G1 live-money integration harness owns full cutover-wipe simulation.")
54507:+def test_r3_u2_raw_provenance_routes_to_u2_profile_not_heartbeat():
54510:+    files are admitted for the provenance slice."""
54512:+        "R3 U2 raw provenance schema venue_order_facts venue_trade_facts position_lots",
54516:+            "tests/test_provenance_5_projections.py",
54520:+    assert digest["profile"] == "r3 raw provenance schema implementation"
54523:+    assert "tests/test_provenance_5_projections.py" in digest["admission"]["admitted_files"]
54528:+    phrases must win so command grammar and RED proxy files are admitted."""
54536:+            "tests/test_riskguard_red_durable_cmd.py",
54669:+def test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat():
54670:+    """R1 mentions settlement/redeem and shares R3 packet docs with Z3; strong
54671:+    settlement-command phrases must admit the durable command ledger files
54672:+    instead of falling through to heartbeat or generic settlement-rounding."""
54674:+        "R3 R1 Settlement / redeem command ledger settlement_commands "
54675:+        "REDEEM_TX_HASHED crash-recoverable redemption Q-FX-1 FXClassificationPending",
54677:+            "src/execution/settlement_commands.py",
54681:+            "tests/test_settlement_commands.py",
54686:+    assert digest["profile"] == "r3 settlement redeem command ledger implementation"
54688:+    assert "src/execution/settlement_commands.py" in digest["admission"]["admitted_files"]
54691:+    assert "tests/test_settlement_commands.py" in digest["admission"]["admitted_files"]
54699:+        "R3 T1 FakePolymarketVenue paper/live parity same PolymarketV2Adapter "
54703:+            "tests/integration/test_p0_live_money_safety.py",
54714:+    assert "tests/integration/test_p0_live_money_safety.py" in digest["admission"]["admitted_files"]
54719:+    """A1 shares broad strategy/live-shadow/replay terms with R3 runtime work;
54723:+        "R3 A1 StrategyBenchmarkSuite alpha execution metrics replay paper live shadow "
54764:+    """F3 shares broad R3 docs and forecast terms with F1/Z3; strong TIGGE
54768:+        "R3 F3 TIGGE ingest stub TIGGEIngest TIGGEIngestNotEnabled ZEUS_TIGGE_INGEST_ENABLED",
54782:+def test_r3_f2_calibration_retrain_loop_routes_to_f2_profile_not_heartbeat():
54783:+    """F2 shares broad R3 docs plus calibration/source terms with other profiles;
54786:+        "R3 F2 Calibration retrain loop operator-gated retrain frozen-replay antibody ZEUS_CALIBRATION_RETRAIN_ENABLED calibration_params_versions",
54790:+            "src/calibration/retrain_trigger.py",
54791:+            "tests/test_calibration_retrain.py",
54796:+    assert digest["profile"] == "r3 calibration retrain loop implementation"
54799:+    assert "src/calibration/retrain_trigger.py" in digest["admission"]["admitted_files"]
54800:+    assert "tests/test_calibration_retrain.py" in digest["admission"]["admitted_files"]
54803:+def test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat():
54806:+    the risk allocator profile rather than heartbeat/M2/M5."""
54811:+            "src/risk_allocator/governor.py",
54812:+            "src/risk_allocator/__init__.py",
54813:+            "config/risk_caps.yaml",
54814:+            "tests/test_risk_allocator.py",
54819:+    assert digest["profile"] == "r3 risk allocator governor implementation"
54821:+    assert "src/risk_allocator/governor.py" in digest["admission"]["admitted_files"]
54822:+    assert "src/risk_allocator/__init__.py" in digest["admission"]["admitted_files"]
54823:+    assert "config/risk_caps.yaml" in digest["admission"]["admitted_files"]
54824:+    assert "tests/test_risk_allocator.py" in digest["admission"]["admitted_files"]
54827:+def test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat():
54828:+    """G1 readiness mentions heartbeat/cutover/risk artifacts, but owns the
54831:+        "R3 G1 live readiness gates live_readiness_check 17 CI gates "
54832:+        "staged-live-smoke INV-NEW-S live-money-deploy-go",
54834:+            "scripts/live_readiness_check.py",
54835:+            "tests/test_live_readiness_gates.py",
54840:+    assert digest["profile"] == "r3 live readiness gates implementation"
54842:+    assert "scripts/live_readiness_check.py" in digest["admission"]["admitted_files"]
54843:+    assert "tests/test_live_readiness_gates.py" in digest["admission"]["admitted_files"]
54861:+    monkeypatch.setattr("src.execution.executor._assert_risk_allocator_allows_submit", lambda *a, **kw: None)
54866:+    monkeypatch.setattr("src.execution.executor._select_risk_allocator_order_type", lambda *a, **kw: "GTC")
55078:+def test_schedule_url_for_gfs_cites_ncep_production_status():
55097:+    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
55105:+    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
55112:+    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
55121:+    truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
55132:+        truth = DecisionTimeTruth(snapshot_id="t1", available_at=avail, provenance=prov)
55138:@@ -41,7 +41,14 @@ def test_counterfactual_reports_delayed_and_settlement_pnl(tmp_path, monkeypatch
55142:-        "INSERT INTO settlements (city, target_date, winning_bin, settlement_value) VALUES ('Paris', '2026-04-03', '12°C', 12.0)"
55144:+        INSERT INTO settlements
55145:+        (city, target_date, winning_bin, settlement_value, temperature_metric,
55164:+def test_settlements_metric_identity_requires_non_null_and_unique_per_metric():
55168:     1. apply_v2_schema creates the v2 tables in a fresh :memory: DB.
55169:-    2. The legacy settlements table still has UNIQUE(city, target_date) —
55182:-    # Legacy settlements UNIQUE must still be (city, target_date) — single-metric
55186:-        "INSERT INTO settlements (city, target_date, authority) VALUES ('NYC', '2026-04-16', 'UNVERIFIED')"
55191:-            "INSERT INTO settlements (city, target_date, authority) VALUES ('NYC', '2026-04-16', 'UNVERIFIED')"
55192:+            "INSERT INTO settlements (city, target_date, authority) "
55197:-            "Legacy settlements table accepted a duplicate (city, target_date) row; "
55202:+        INSERT INTO settlements
55213:+            INSERT INTO settlements
55226:+        INSERT INTO settlements
55253:+flow into training/skill ETL output. DERIVED_FROM_DISSEMINATION + RECORDED
55262:+def _seed_forecasts_with_mixed_provenance(conn: sqlite3.Connection) -> None:
55274:+            availability_provenance TEXT
55294:+def _seed_settlements(conn: sqlite3.Connection) -> None:
55295:+    """Single settlement row matching the forecasts above."""
55297:+        CREATE TABLE settlements (
55299:+            settlement_value REAL, settlement_source TEXT, settled_at TEXT,
55304:+        "INSERT INTO settlements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
55305:+        ("NYC", "2026-04-30", "test", "70-71F", 71.0, "WU", "2026-04-30", "VERIFIED", "high"),
55311:+def db_with_mixed_provenance():
55314:+    _seed_forecasts_with_mixed_provenance(conn)
55315:+    _seed_settlements(conn)
55320:+def test_etl_historical_forecasts_filter_excludes_reconstructed(db_with_mixed_provenance):
55323:+    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
55325:+    rows = db_with_mixed_provenance.execute(f"""
55326:+        SELECT id, source, availability_provenance
55329:+          AND (availability_provenance IS NULL OR {SKILL_ELIGIBLE_SQL})
55336:+    # Sanity: no row in result has provenance = 'reconstructed'
55337:+    assert all(r["availability_provenance"] != "reconstructed" for r in rows)
55340:+def test_etl_forecast_skill_join_filter_excludes_reconstructed(db_with_mixed_provenance):
55343:+    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
55346:+        "availability_provenance", "f.availability_provenance"
55348:+    rows = db_with_mixed_provenance.execute(f"""
55349:+        SELECT f.id, f.source, f.availability_provenance, s.settlement_value
55351:+        JOIN settlements s
55357:+          AND s.settlement_value IS NOT NULL
55358:+          AND (f.availability_provenance IS NULL OR {skill_filter_qualified})
55364:+    assert all(r["availability_provenance"] != "reconstructed" for r in rows)
55377:+    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
55523:+            neg_risk=False,
55581:+            neg_risk=False,
56126:+        neg_risk=False,
56180:+            neg_risk=False,
56208:+    expected_neg_risk: bool | None = False,
56229:+        expected_neg_risk=expected_neg_risk,
56386:+              token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
56501:+        "executable_snapshot_neg_risk": False,
56539:+            neg_risk=False,
56620:+# Reuse: Run when venue command persistence, live order submission, or ACK handling changes.
56648:+def _cutover_guard_live_enabled(monkeypatch):
56691:+            neg_risk=False,
56747:+            neg_risk=False,
56790:+        executable_snapshot_neg_risk=False,
56814:+        executable_snapshot_neg_risk=False,
56841:         from src.execution.executor import _live_order
56890:         from src.execution.executor import _live_order
56904:+        from src.execution.executor import _live_order
56925:+            result = _live_order(
56944:+        from src.execution.executor import _live_order
56970:+            result = _live_order(
56989:         from src.execution.executor import _live_order
57004:         # Pre-insert a command with the key that _live_order will derive
57198:@@ -833,7 +1101,7 @@ def test_synthetic_decision_id_emits_warning(mem_conn, caplog):
57199:     from src.execution.executor import _live_order
57222:+# Purpose: Ensure executor command writes target zeus_trades DB, not legacy zeus DB.
57223:+# Reuse: Run when executor DB connection targeting or venue command schema changes.
57227: # Authority basis: P1.S3 critic CRITICAL finding — DB target regression
57245:+def _cutover_guard_live_enabled(monkeypatch):
57246:+    """This file tests DB targeting, not cutover gating."""
57288:+            neg_risk=False,
57328:+        executable_snapshot_neg_risk=False,
57348:+        executable_snapshot_neg_risk=False,
57354:         from src.execution.executor import _live_order
57378:+# Purpose: Lock typed ExecutionPrice validation at the executor live-send boundary.
57379:+# Reuse: Run when executor live order construction or typed boundary contracts change.
57469:+def _allow_risk_allocator_for_exit_tests() -> None:
57471:+    from src.risk_allocator import GovernorState, RiskAllocator, configure_global_allocator
57519:+            neg_risk=False,
57577:+            neg_risk=False,
57691:+def test_cancel_not_canceled_dict_creates_CANCEL_FAILED_or_REVIEW_REQUIRED(conn):
57705:+    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"
57722:+    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"
57738:+    _allow_risk_allocator_for_exit_tests()
57772:+        from src.risk_allocator import clear_global_allocator
57818:+    _allow_risk_allocator_for_exit_tests()
57842:+                executable_snapshot_neg_risk=False,
57857:+                executable_snapshot_neg_risk=False,
57868:+        from src.risk_allocator import clear_global_allocator
57885:+            neg_risk=intent.executable_snapshot_neg_risk,
57905:+        "neg_risk": False,
57916:+    _allow_risk_allocator_for_exit_tests()
57943:+        from src.risk_allocator import clear_global_allocator
58058:+# Reuse: Run when fake/live venue adapter protocol, failure modes, or paper/live parity changes.
58082:+    neg_risk: bool = False
58144:+        "neg_risk",
58183:+    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"
58193:+# Purpose: Protect R3 F1 forecast-source registry gates and provenance stamping.
58194:+# Reuse: Run before forecast source, schema, ensemble fetch, or TIGGE gate changes.
58196:+"""R3 F1 forecast source registry and provenance antibodies."""
58207:+from src.contracts.settlement_semantics import SettlementSemantics
58227:+        settlement_unit="F",
58262:+            environ={"ZEUS_TIGGE_INGEST_ENABLED": "1"},
58291:+        environ={"ZEUS_TIGGE_INGEST_ENABLED": "1"},
58303:+        raise AssertionError("network should not be called for gate-closed TIGGE")
58320:+        raise AssertionError("TIGGE must use its registered ingest adapter, not Open-Meteo")
58344:+    monkeypatch.setenv("ZEUS_TIGGE_INGEST_ENABLED", "1")
58360:+    from src.data.tigge_client import TIGGEIngestFetchNotConfigured
58370:+    monkeypatch.setenv("ZEUS_TIGGE_INGEST_ENABLED", "1")
58373:+    with pytest.raises(TIGGEIngestFetchNotConfigured, match="payload"):
58470:+def test_ensemble_fetch_result_carries_registry_provenance(monkeypatch) -> None:
58544:+# Purpose: Protect forecasts writer/schema alignment across fresh and legacy DBs.
58556: 1. **Fresh-DB path**: `init_schema()` on a blank connection yields a
58558:+   `forecasts` table that contains writer provenance columns.
58559: 2. **Legacy-DB path**: a pre-existing `forecasts` table that predates the
58564:    runs. Catches future writer drift without requiring a live DB.
58565:+4. **R3 F1 provenance**: source_id/raw_payload_hash/captured_at/authority_tier
58585:             f"fresh DB forecasts table missing data_source_version (columns: {sorted(cols)})"
58601:             "ALTER TABLE path did NOT add data_source_version on legacy-schema DB"
58604:+            "ALTER TABLE path did NOT add R3 F1 forecast provenance columns: "
58614:+def test_writer_insert_columns_include_f1_provenance_sanity_check():
58615:+    """R3 F1: new forecast writes must carry source registry provenance."""
58623:diff --git a/tests/test_forecasts_writer_provenance_required.py b/tests/test_forecasts_writer_provenance_required.py
58627:+++ b/tests/test_forecasts_writer_provenance_required.py
58632:+"""F11.3 antibody: writer rejects ForecastRow missing availability_provenance / issue_time.
58635:+construct a ForecastRow without the typed provenance fields and have
58650:+    UNIQUE matches production exactly so relationship antibodies catch
58661:+            availability_provenance TEXT,
58677:+    """Factory for ForecastRow with both F11 (availability_provenance) and
58679:+    populated. Values are realistic-shaped but not pulled from the live
58700:+        availability_provenance="derived_dissemination",
58706:+def test_writer_accepts_row_with_provenance(db):
58712:+def test_writer_rejects_null_provenance(db):
58713:+    bad = _good_row(availability_provenance=None)
58714:+    with pytest.raises(ValueError, match="availability_provenance"):
58724:+def test_writer_accepts_each_valid_provenance_tier(db):
58726:+        rows = [_good_row(target_date=f"2026-04-30-{tier}", availability_provenance=tier)]
58738:+    rows = [_good_row(target_date="2026-04-30"), _good_row(target_date="2026-05-01", availability_provenance=None)]
58746:+def test_rows_from_payload_stamps_provenance_for_canonical_sources():
58747:+    """End-to-end: _rows_from_payload constructs rows with non-NULL provenance
58776:+    assert all(r.availability_provenance is not None for r in rows)
58781:+        assert r.availability_provenance == "derived_dissemination"
58811:diff --git a/tests/test_harvester_dr33_live_enablement.py b/tests/test_harvester_dr33_live_enablement.py
58813:--- a/tests/test_harvester_dr33_live_enablement.py
58814:+++ b/tests/test_harvester_dr33_live_enablement.py
58815:@@ -215,7 +215,7 @@ def test_T5_write_settlement_verified_path(scratch_db):
58816:     assert r["settlement_value"] == 17.0  # wmo_half_up(17.3) = 17
58823:     prov = json.loads(r["provenance_json"])
58833:-    risk_path = tmp_path / "risk_state-paper.db"
58834:+    status_path = tmp_path / "status_summary-live.json"
58835:+    risk_path = tmp_path / "risk_state-live.db"
58838:         risk={"level": "GREEN", "details": {
58840:     _write_risk_state(risk_path)
58844:+    monkeypatch.setenv("ZEUS_MODE", "live")
58846:     monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
58852:+        stdout = "123\t0\tcom.zeus.live-trading\n"
58858:     _write_risk_state(risk_path)
58861:+    monkeypatch.setenv("ZEUS_MODE", "live")
58863:     monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
58868:+        stdout = '{\n\t"Label" = "com.zeus.live-trading";\n\t"PID" = 59087;\n\t"LastExitStatus" = 15;\n};\n'
58874:     _write_risk_state(risk_path)
58877:+    monkeypatch.setenv("ZEUS_MODE", "live")
58879:     monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
58886:+            return _Result(0, "gui/501/com.zeus.live-trading = {\n\tstate = running\n\tpid = 59087\n}\n")
58893: def test_healthcheck_is_not_healthy_when_riskguard_is_missing(monkeypatch, tmp_path):
58895:-    risk_path = tmp_path / "risk_state-paper.db"
58896:+    status_path = tmp_path / "status_summary-live.json"
58897:+    risk_path = tmp_path / "risk_state-live.db"
58901:     _write_risk_state(risk_path, checked_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
58904:+    monkeypatch.setenv("ZEUS_MODE", "live")
58906:     monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
58912:+            returncode = 0 if cmd[-1] == "com.zeus.live-trading" else 1
58913:+            stdout = "123\t0\tcom.zeus.live-trading\n" if cmd[-1] == "com.zeus.live-trading" else ""
58918:     _write_risk_state(risk_path)
58922:+    monkeypatch.setenv("ZEUS_MODE", "live")
58924:     monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
58930:+        stdout = "123\t0\tcom.zeus.live-trading\n"
58934:@@ -554,13 +554,13 @@ def test_healthcheck_flags_stale_status_and_risk_contracts(monkeypatch, tmp_path
58939:+    monkeypatch.setenv("ZEUS_MODE", "live")
58941:     monkeypatch.setattr(healthcheck, "_risk_state_path", lambda: risk_path)
58946:+        stdout = "123\t0\tcom.zeus.live-trading\n"
58958:+# Reuse: Run when heartbeat supervision, executor submit gating, or R3 live-money readiness changes.
59112:+    from src.execution.executor import _live_order
59126:+            _live_order("heartbeat-trade", _intent(), shares=10.0, conn=conn, decision_id="decision-heartbeat")
59284:-    """Degraded trailing loss must be RED, not YELLOW."""
59288:         from src.riskguard.riskguard import _trailing_loss_snapshot, RiskLevel
59293:-            assert result["level"] == RiskLevel.RED
59307:+    """_apply_bias_correction returns status or fails closed on DB faults."""
59310:+    def test_correction_database_fault_raises(self):
59316:             # Force exception by patching the DB import
59376:+        "cancel_order must call the V2 adapter boundary for lazy live I/O"
59414:diff --git a/tests/test_live_execution.py b/tests/test_live_execution.py
59416:--- a/tests/test_live_execution.py
59417:+++ b/tests/test_live_execution.py
59420:+# Purpose: Mock live execution happy/error path coverage with R3 cutover guard opt-outs.
59421:+# Reuse: Run when _live_order side effects, ACK semantics, or mock CLOB behavior changes.
59424:+# Authority basis: R3 Z1 cutover guard audit; pre-existing live execution mock tests updated to opt out of CutoverGuard so they keep testing executor mechanics.
59427: Mock Polymarket CLOB, run _live_order through happy path + error modes.
59428:@@ -8,11 +14,16 @@ submitting. Tests that call _live_order without an explicit conn use the
59429: `_mem_conn` autouse fixture to supply an in-memory DB with schema.
59437: from src.execution.executor import _live_order, OrderResult
59477:+        "executable_snapshot_neg_risk": False,
59517:+            neg_risk=False,
59537:         result = _live_order("trade-4", _make_intent(), shares=10.0)
59545:diff --git a/tests/test_live_readiness_gates.py b/tests/test_live_readiness_gates.py
59549:+++ b/tests/test_live_readiness_gates.py
59555:+# Purpose: R3 G1 live-readiness gate orchestrator regressions.
59556:+# Reuse: Run before any live-readiness, cutover, deployment, or operator-gate changes.
59557:+"""R3 G1 live-readiness gate tests."""
59569:+from scripts import live_readiness_check as lrc
59610:+    (root / "staged_live_smoke_2026-04-27.json").write_text(
59612:+            "staged_live_smoke",
59616:+                "environment": "staged-live-smoke",
59638:+    assert report.live_deploy_authorized is False
59643:+    assert "missing staged-live-smoke" in report.staged_smoke_evidence
59670:+    assert report.live_deploy_authorized is False
59677:+        "HTTP/2 200 OK\nZeus daemon machine\nfunder_address=0xREDACTED\n"
59679:+    (evidence / "staged_live_smoke_2026-04-27.json").write_text(
59680:+        '{"status":"PASS","gates_passed":17,"environment":"staged-live-smoke"}'
59707:+def test_cli_help_is_safe_and_does_not_require_live_evidence():
59709:+        [sys.executable, "scripts/live_readiness_check.py", "--help"],
59717:+    assert "Run R3 G1 live-readiness gates" in proc.stdout
59718:+    assert "live_deploy_authorized" not in proc.stdout
59728:+            "scripts/live_readiness_check.py",
59742:diff --git a/tests/test_live_safety_invariants.py b/tests/test_live_safety_invariants.py
59744:--- a/tests/test_live_safety_invariants.py
59745:+++ b/tests/test_live_safety_invariants.py
59746:@@ -140,7 +140,7 @@ def test_live_exit_never_closes_without_fill():
59823:diff --git a/tests/test_neg_risk_passthrough.py b/tests/test_neg_risk_passthrough.py
59825:--- a/tests/test_neg_risk_passthrough.py
59826:+++ b/tests/test_neg_risk_passthrough.py
59829:+# Purpose: Constrain neg-risk provenance to the venue adapter/envelope boundary.
59838:-`src/contracts/neg_risk.py`) BUT qualified: "verify py-clob-client
59842:-1. py-clob-client auto-detects neg_risk per token via
59843:-   `ClobClient.get_neg_risk(token_id)` (client.py L441-448) with
59846:-   L572-575): if caller supplies `PartialCreateOrderOptions.neg_risk`
59848:-3. Zeus src/ has **ZERO** `neg_risk` / `negRisk` override paths —
59849:-   production trading relies entirely on the SDK auto-detection.
59851:-   resolution) are conceptually neg-risk (exactly one bin resolves
59852:-   YES), but neg-risk semantics are already handled at the SDK /
59864:-neg_risk contract OR if Zeus ever starts overriding explicitly, these
59869:-- SDK contract: ClobClient.get_neg_risk(token_id) exists and is
59871:-- SDK contract: PartialCreateOrderOptions carries `neg_risk: Optional[bool]`.
59872:-- Zeus assumption: no `neg_risk` / `negRisk` string-literal references
59874:-- Zeus assumption: no `options.neg_risk=True` / `options.neg_risk=False`
59879:+"""Neg-risk provenance boundary antibodies for the R3 V2 adapter.
59881:+The 2026-04-23 V1 antibody enforced "no Zeus neg_risk references" because the
59883:+Zeus may now carry `neg_risk` only as venue/snapshot provenance inside
59885:+coded override or leak the concept into pricing/settlement logic.
59899:-    """py-clob-client must continue to expose auto-detected neg_risk."""
59901:-    def test_clob_client_exposes_get_neg_risk_method(self):
59902:-        """ClobClient.get_neg_risk(token_id) is the SDK's auto-detection
59907:-        assert hasattr(ClobClient, "get_neg_risk"), (
59908:-            "py-clob-client must expose ClobClient.get_neg_risk(token_id); "
59911:-        sig = inspect.signature(ClobClient.get_neg_risk)
59914:-            "ClobClient.get_neg_risk must accept a token_id parameter"
59917:-    def test_partial_create_order_options_exposes_neg_risk_field(self):
59918:-        """PartialCreateOrderOptions.neg_risk is the caller-supplied
59925:-        assert "neg_risk" in fields, (
59926:-            "PartialCreateOrderOptions must declare a neg_risk field"
59931:-    """Zeus must not introduce neg_risk overrides — the T5.c skip
59941:+    "src/strategy/candidates/neg_risk_basket.py",
59950:+    """Zeus may carry neg-risk as V2 provenance, not as strategy logic."""
59963:-    def test_no_neg_risk_string_literals_in_zeus_src(self):
59964:-        """A grep-style scan of src/ for neg_risk / negRisk / NegRisk.
59971:-        here because any form of `neg_risk` reference indicates the
59973:+    def test_neg_risk_references_are_confined_to_v2_provenance_boundary(self):
59974:         needles = ("neg_risk", "negRisk", "NegRisk")
59989:-            "Zeus src/ must not reference neg_risk — T5.c resolution "
59995:+            "R3 Z2 allows neg_risk only in VenueSubmissionEnvelope and the "
59996:+            "PolymarketV2Adapter provenance boundary. Other references are "
59997:+            f"potential strategy/settlement leakage. Offenders: {offenders[:10]}"
60000:+    def test_adapter_passes_envelope_neg_risk_without_boolean_override(self):
60003:+        assert "neg_risk=envelope.neg_risk" in text
60004:+        forbidden = ("neg_risk=True", "neg_risk = True", "neg_risk=False", "neg_risk = False")
60006:+        assert hits == [], f"Adapter must not hard-code neg_risk overrides: {hits}"
60010:-        PartialCreateOrderOptions(neg_risk=...) at order-create time.
60011:-        Even without the literal `neg_risk` substring, the
60025:-            "for overriding neg_risk and breaks the T5.c passthrough "
60029:+            "the V2 adapter passes the snapshot/envelope neg_risk value rather "
60062:+        # K1 additions: observations table stores per-field high/low provenance.
60084:-        provenance_metadata=json.dumps(atom.provenance_metadata),
60085:+        high_provenance_metadata=json.dumps(atom.provenance_metadata) if atom.value_type == "high" else None,
60086:+        low_provenance_metadata=json.dumps(atom.provenance_metadata) if atom.value_type == "low" else None,
60109:-    assert json.loads(result["provenance_metadata"]) == atom.provenance_metadata
60110:+    assert json.loads(result[f"{prefix}_provenance_metadata"]) == atom.provenance_metadata
60159:+                neg_risk=False,
60175:+            "executable_snapshot_neg_risk": False,
60203:+        """Patch post-P0 live-submit guards that are outside the INV-25 seam.
60214:+        stack.enter_context(patch("src.execution.executor._assert_risk_allocator_allows_submit", return_value=None))
60215:+        stack.enter_context(patch("src.execution.executor._select_risk_allocator_order_type", return_value="GTC"))
60223:         """Mocked v2_preflight raises V2PreflightError; _live_order returns rejected
60227:         from src.execution.executor import _live_order
60239:         from src.execution.executor import _live_order
60283:             "training_allowed": True,
60297:-        """R-AT-2 (GREEN): _require_wu_api_key() raises SystemExit when WU_API_KEY is empty.
60298:+        """R-AT-2 (GREEN): _require_wu_api_key() fails closed when WU_API_KEY is empty.
60304:         monkeypatch.setattr(obs_mod, "WU_API_KEY", "")
60306:-        with pytest.raises(SystemExit, match="WU_API_KEY"):
60307:+        with pytest.raises((SystemExit, AssertionError), match="WU_API_KEY"):
60389:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60409:@@ -1858,6 +1870,7 @@ def test_inv_tighten_risk_reduces_kelly_multiplier(monkeypatch):
60417:@@ -1869,9 +1882,11 @@ def test_inv_tighten_risk_reduces_kelly_multiplier(monkeypatch):
60419:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60430:@@ -1889,7 +1904,7 @@ def test_inv_tighten_risk_reduces_kelly_multiplier(monkeypatch):
60439:@@ -1978,6 +1993,7 @@ def test_inv_strategy_policy_gate_yields_risk_rejected(monkeypatch):
60447:@@ -1989,9 +2005,11 @@ def test_inv_strategy_policy_gate_yields_risk_rejected(monkeypatch):
60449:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60460:@@ -2009,7 +2027,7 @@ def test_inv_strategy_policy_gate_yields_risk_rejected(monkeypatch):
60479:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60509:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60529:@@ -2357,6 +2381,7 @@ def test_inv_manual_override_beats_automatic_risk_action_on_active_evaluator_pat
60537:@@ -2368,9 +2393,11 @@ def test_inv_manual_override_beats_automatic_risk_action_on_active_evaluator_pat
60539:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60550:@@ -2388,7 +2415,7 @@ def test_inv_manual_override_beats_automatic_risk_action_on_active_evaluator_pat
60559:@@ -2435,6 +2462,8 @@ def test_inv_manual_override_beats_automatic_risk_action_on_active_evaluator_pat
60563:+    monkeypatch.setattr("src.riskguard.policy.is_entries_paused", lambda: False)
60564:+    monkeypatch.setattr("src.riskguard.policy.get_edge_threshold_multiplier", lambda: 1.0)
60568:@@ -2484,6 +2513,7 @@ def test_inv_expired_manual_override_restores_automatic_risk_action_on_active_ev
60576:@@ -2495,9 +2525,11 @@ def test_inv_expired_manual_override_restores_automatic_risk_action_on_active_ev
60578:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60589:@@ -2515,7 +2547,7 @@ def test_inv_expired_manual_override_restores_automatic_risk_action_on_active_ev
60598:@@ -2562,6 +2594,8 @@ def test_inv_expired_manual_override_restores_automatic_risk_action_on_active_ev
60602:+    monkeypatch.setattr("src.riskguard.policy.is_entries_paused", lambda: False)
60603:+    monkeypatch.setattr("src.riskguard.policy.get_edge_threshold_multiplier", lambda: 1.0)
60617:         def __init__(self, members_hourly, times, city, target_d, settlement_semantics=None, decision_time=None, **kwargs):
60642:+    monkeypatch.setattr("src.riskguard.policy.is_entries_paused", lambda: False)
60643:+    monkeypatch.setattr("src.riskguard.policy.get_edge_threshold_multiplier", lambda: 1.0)
60647:@@ -2922,11 +2961,11 @@ def test_inv_riskguard_prefers_canonical_position_events_settlement_source(monke
60695:+    monkeypatch.setattr("src.execution.executor._assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
60696:+    monkeypatch.setattr("src.execution.executor._select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
60742:+                neg_risk=False,
60758:+        "executable_snapshot_neg_risk": False,
60901:diff --git a/tests/test_pre_live_integration.py b/tests/test_pre_live_integration.py
60903:--- a/tests/test_pre_live_integration.py
60904:+++ b/tests/test_pre_live_integration.py
60922:diff --git a/tests/test_provenance_5_projections.py b/tests/test_provenance_5_projections.py
60926:+++ b/tests/test_provenance_5_projections.py
60930:+# Purpose: U2 antibodies for 5 raw provenance projections and CONFIRMED-only training.
60931:+# Reuse: Run when venue command, order fact, trade fact, position lot, settlement provenance, or calibration ingestion changes.
60934:+"""U2 raw provenance schema tests for five distinct projections."""
60956:+    append_provenance_event,
60960:+    load_calibration_trade_facts,
61008:+        neg_risk=False,
61046:+        neg_risk=False,
61052:+        raw_response_json=json.dumps({"orderID": "ord-u2", "status": "live"}, sort_keys=True),
61101:+        expected_neg_risk=False,
61115:+    assert "provenance_envelope_events" in tables
61316:+def test_calibration_training_filters_for_CONFIRMED_only(conn):
61333:+    rows = load_calibration_trade_facts(conn)
61338:+        load_calibration_trade_facts(conn, states=["MATCHED", "MINED"])
61397:+def test_full_provenance_chain_reconstructable(conn):
61570:+        append_provenance_event(
61582:+def test_command_events_are_mirrored_with_u2_provenance(conn):
61595:+        FROM provenance_envelope_events
61613:+        trade_id="trade-settlement",
61639:+    append_provenance_event(
61641:+        subject_type="settlement",
61643:+        event_type="REDEEM_CONFIRMED",
61660:+        FROM provenance_envelope_events
61661:+        WHERE subject_type = 'settlement' AND subject_id = ?
61679:+# Authority basis: critic adversarial review 2026-04-28 MAJOR #2 (typed gate not wired into live consumers); F11.6 slice
61686:+Pre-F11 legacy DBs (no availability_provenance column) MAY also pass through
61687:+via the IS NULL clause — this is a deliberate tolerance for un-migrated DBs.
61696:+def db_with_mixed_provenance():
61714:+            availability_provenance TEXT
61725:+        # Pre-F11 legacy row (NULL provenance — tolerance case)
61747:+def test_replay_excludes_reconstructed_rows(db_with_mixed_provenance):
61754:+        db_with_mixed_provenance,
61766:+    # Note: openmeteo appears once for the legacy NULL-provenance row, not
61770:+    assert openmeteo_rows[0]["forecast_basis_date"] == "2026-04-27"  # the NULL-provenance legacy row
61773:+def test_replay_includes_legacy_null_provenance_rows(db_with_mixed_provenance):
61774:+    """Pre-F11 legacy rows (availability_provenance IS NULL) are tolerated.
61776:+    This is a deliberate compatibility allowance — un-migrated DBs continue
61778:+    every row has populated provenance and the IS NULL clause becomes inert.
61783:+        db_with_mixed_provenance,
61792:+def test_replay_eligibility_count_matches_design(db_with_mixed_provenance):
61797:+        db_with_mixed_provenance,
61803:diff --git a/tests/test_risk_allocator.py b/tests/test_risk_allocator.py
61807:+++ b/tests/test_risk_allocator.py
61812:+# Reuse: Run for A2 allocator/governor, executor pre-submit, and live-readiness gate changes.
61831:+from src.risk_allocator import (
61847:+    summary as risk_allocator_summary,
61849:+from src.riskguard.risk_level import RiskLevel
61867:+        executable_snapshot_neg_risk=False,
61929:+            neg_risk=False,
61985:+def test_unknown_side_effect_blocks_new_risk_in_same_market():
62019:+def test_drawdown_governor_blocks_new_risk_at_threshold():
62028:+def test_reduce_only_mode_when_risk_state_degraded():
62031:+    decision = allocator.can_allocate(_intent(size=1), _state(risk_level=RiskLevel.DATA_DEGRADED))
62074:+        snapshot = risk_allocator_summary()
62088:+    from src.execution.executor import _assert_risk_allocator_allows_submit
62094:+            _assert_risk_allocator_allows_submit(_intent(size=1))
62107:+        lambda: (_ for _ in ()).throw(AssertionError("DB persistence must not start")),
62127:+def test_live_entry_submit_uses_allocator_selected_FOK_for_shallow_book(monkeypatch):
62170:+def test_live_exit_submit_uses_allocator_selected_FOK_when_heartbeat_is_degraded(monkeypatch):
62198:+                executable_snapshot_neg_risk=False,
62277:+        {"current_drawdown_pct": 5.0, "risk_level": "GREEN"},
62353:+          '{"allocation":{"event_id":"event-live","resolution_window":"2026-04-27","correlation_key":"city-nyc"}}',
62376:+    assert lots[1].event_id == "event-live"
62394:+        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
62440:+    policy = load_cap_policy("config/risk_caps.yaml")
62444:diff --git a/tests/test_riskguard.py b/tests/test_riskguard.py
62446:--- a/tests/test_riskguard.py
62447:+++ b/tests/test_riskguard.py
62457:diff --git a/tests/test_riskguard_red_durable_cmd.py b/tests/test_riskguard_red_durable_cmd.py
62461:+++ b/tests/test_riskguard_red_durable_cmd.py
62465:+# Purpose: M1 antibodies for RED force-exit durable command proxy and NC-NEW-D function-scope ownership.
62466:+# Reuse: Run when cycle_runner RED sweep, venue command persistence, or riskguard actuation changes.
62468:+"""RED force-exit durable command proxy tests."""
62524:+        neg_risk=False,
62588:+def test_red_emit_grammar_bound_to_cancel_or_derisk_only():
62650:+def test_riskguard_does_NOT_call_insert_command_directly():
62651:+    riskguard_source = (ROOT / "src/riskguard/riskguard.py").read_text()
62652:+    assert "insert_command" not in riskguard_source
62685:diff --git a/tests/test_settlement_commands.py b/tests/test_settlement_commands.py
62689:+++ b/tests/test_settlement_commands.py
62693:+# Authority basis: R3 R1 settlement/redeem command ledger packet
62696:+# Reuse: Run for settlement/redeem, harvester redemption, collateral FX gate, or payout-asset changes.
62697:+"""Regression tests for R3 R1 durable settlement/redeem commands."""
62755:+            "SELECT event_type FROM settlement_command_events WHERE command_id = ? ORDER BY id",
62762:+    return conn.execute("SELECT * FROM settlement_commands WHERE command_id = ?", (command_id,)).fetchone()
62767:+        "src.execution.settlement_commands.redemption_decision",
62773:+    from src.execution.settlement_commands import (
62792:+    assert command(conn, command_id)["state"] == SettlementState.REDEEM_INTENT_CREATED.value
62795:+    assert result.state is SettlementState.REDEEM_TX_HASHED
62803:+    assert confirmed.state is SettlementState.REDEEM_CONFIRMED
62804:+    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value
62809:+        "REDEEM_INTENT_CREATED",
62810:+        "REDEEM_SUBMITTED",
62811:+        "REDEEM_TX_HASHED",
62812:+        "REDEEM_CONFIRMED",
62815:+        "SELECT payload_hash FROM settlement_command_events WHERE command_id = ?",
62822:+    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem, submit_redeem
62830:+    assert command(conn, command_id)["state"] == SettlementState.REDEEM_TX_HASHED.value
62833:+    assert [result.state for result in results] == [SettlementState.REDEEM_CONFIRMED]
62838:+    from src.execution.settlement_commands import SettlementState, request_redeem, submit_redeem
62858:+    assert result.state is SettlementState.REDEEM_FAILED
62859:+    assert command(conn, command_id)["state"] == SettlementState.REDEEM_FAILED.value
62864:+    from src.execution.settlement_commands import SettlementState, request_redeem
62871:+    assert row["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value
62876:+    from src.execution.settlement_commands import request_redeem, submit_redeem
62881:+    assert conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0] == 0
62892:+    assert command(conn, command_id)["state"] == "REDEEM_INTENT_CREATED"
62893:+    assert states(conn, command_id) == ["REDEEM_INTENT_CREATED"]
62898:+    from src.execution.settlement_commands import request_redeem, submit_redeem
62902:+        "src.execution.settlement_commands.redemption_decision",
62903:+        lambda: SimpleNamespace(allow_redemption=False, block_reason="BLOCKED:REDEEM", state="BLOCKED"),
62908:+    with pytest.raises(CutoverPending, match="BLOCKED:REDEEM"):
62912:+    assert command(conn, command_id)["state"] == "REDEEM_INTENT_CREATED"
62913:+    assert states(conn, command_id) == ["REDEEM_INTENT_CREATED"]
62917:+    from src.execution.settlement_commands import request_redeem
62922:diff --git a/tests/test_settlement_semantics.py b/tests/test_settlement_semantics.py
62926:+++ b/tests/test_settlement_semantics.py
62932:+#   HKO antibody). Per Fitz "test relationships, not just functions" — these
62940:+arithmetic correctness of WMO_HalfUp / HKO_Truncation themselves is incidental;
62951:+from src.contracts.settlement_semantics import (
62952:+    HKO_Truncation,
62965:+    with pytest.raises(TypeError, match=r"Hong Kong.*require.*HKO_Truncation"):
62966:+        settle_market("Hong Kong", Decimal("28.7"), WMO_HalfUp())
62970:+    """RELATIONSHIP: non-HK city + HKO policy → TypeError.
62972:+    HKO truncation is the wrong rounding semantics for any non-HK market;
62974:+    settlement values vs the WU integer °F oracle.
62976:+    with pytest.raises(TypeError, match=r"HKO_Truncation.*Hong Kong only"):
62977:+        settle_market("New York", Decimal("74.5"), HKO_Truncation())
62984:+    inheriting from SettlementRoundingPolicy may decide a settlement value;
62988:+        def round_to_settlement(self, x: Decimal) -> int:
62999:+# settlement_semantics.py:19 + docs/reference/modules/contracts.md:89). DB has
63001:+# in NYC/Chicago winter — silent drift would have shifted settlement by 1°C on
63007:+    assert policy.round_to_settlement(Decimal("-3.5")) == -3
63008:+    assert policy.round_to_settlement(Decimal("-0.5")) == 0
63009:+    assert policy.round_to_settlement(Decimal("-100.5")) == -100
63015:+    assert policy.round_to_settlement(Decimal("3.5")) == 4
63016:+    assert policy.round_to_settlement(Decimal("100.5")) == 101
63021:+    from src.contracts.settlement_semantics import round_wmo_half_up_value
63026:+        new = policy.round_to_settlement(Decimal(str(x)))
63080:+            failed_settlement_cost=0.0,
63089:+            capital_at_risk=100.0,
63098:+            failed_settlement_cost=0.0,
63107:+            capital_at_risk=50.0,
63122:+    assert metrics.time_to_resolution_risk == 10 / 3
63138:+def test_benchmark_metrics_computed_for_live_shadow():
63141:+    metrics = suite.evaluate_live_shadow(STRATEGY, capital_cap_micro=100_000, duration_hours=2)
63152:+    shadow = suite.evaluate_live_shadow(STRATEGY, capital_cap_micro=100_000, duration_hours=2)
63156:+    blocked_shadow = StrategyBenchmarkSuite().evaluate_live_shadow(STRATEGY, capital_cap_micro=100_000, duration_hours=2)
63162:+def test_pnl_split_into_alpha_spread_fees_slippage_failed_settlement_capital_lock():
63170:+        failed_settlement_cost=1.5,
63184:+        "failed_settlement": 1.5,
63190:+def test_backtest_to_paper_to_live_semantic_drift_report_empty_or_explicitly_waived():
63202:+def test_calibration_error_vs_market_implied_p_computed():
63205:+    assert metrics.calibration_error_vs_market_implied == pytest.approx(0.05)
63242:+        "neg_risk_basket",
63257:+# Purpose: Lock R3 F3 TIGGE ingest stub gates and registry integration.
63258:+# Reuse: Run when changing TIGGE source gating, forecast-source registry entries, or ForecastIngestProtocol adapters.
63259:+"""R3 F3 tests for the dormant TIGGE ingest stub."""
63271:+from src.contracts.settlement_semantics import SettlementSemantics
63276:+    TIGGEIngest,
63277:+    TIGGEIngestFetchNotConfigured,
63278:+    TIGGEIngestNotEnabled,
63289:+    settlement_unit="F",
63303:+    body = "operator-approved TIGGE ingest test artifact\n"
63320:+    ingest = TIGGEIngest(root=tmp_path, environ={})
63337:+    ingest = TIGGEIngest(root=tmp_path, environ={}, payload_fetcher=fail_if_called)
63339:+    with pytest.raises(TIGGEIngestNotEnabled) as excinfo:
63362:+    ingest = TIGGEIngest(
63381:+    ingest = TIGGEIngest(root=tmp_path, environ={ENV_FLAG_NAME: "1"})
63383:+    with pytest.raises(TIGGEIngestFetchNotConfigured, match=PAYLOAD_PATH_ENV):
63404:+    ingest = TIGGEIngest(root=tmp_path, environ={ENV_FLAG_NAME: "1"})
63420:+    ingest = TIGGEIngest(
63433:+    assert spec.ingest_class is TIGGEIngest
63441:+def test_ensemble_signal_does_not_consume_TIGGE_when_gated(tmp_path):
63448:+    # Loading the TIGGE class/registry must not alter signal math or source
63450:+    assert TIGGEIngest.source_id == "tigge"
63484:+    """In-memory trades DB with live-money gates neutralized for unit tests."""
63534:+            neg_risk=False,
63569:+        executable_snapshot_neg_risk=False,
63610:+            neg_risk=False,
63663:+    from src.execution.executor import _live_order
63671:+        result = _live_order("trade-m2-timeout", intent, shares=18.19, conn=conn, decision_id="dec-m2-timeout")
63683:+    from src.execution.executor import _live_order
63695:+        result = _live_order("trade-m2-reject", intent, shares=18.19, conn=conn, decision_id="dec-m2-reject")
63705:+    from src.execution.executor import _live_order
63712:+        result = _live_order("trade-m2-prepost", intent, shares=18.19, conn=conn, decision_id="dec-m2-prepost")
63723:+    from src.execution.executor import _live_order
63730:+        result = _live_order("trade-m2-generic-prepost", intent, shares=18.19, conn=conn, decision_id="dec-m2-generic-prepost")
63761:+                executable_snapshot_neg_risk=False,
63799:+            executable_snapshot_neg_risk=False,
63855:+                executable_snapshot_neg_risk=False,
63871:+    from src.execution.executor import _live_order
63878:+        first = _live_order("trade-m2-dupe", intent, shares=18.19, conn=conn, decision_id="dec-m2-dupe")
63884:+        second = _live_order("trade-m2-dupe", intent, shares=18.19, conn=conn, decision_id="dec-m2-dupe")
63892:+    from src.execution.executor import _live_order
63899:+        first = _live_order("trade-m2-economic", intent, shares=18.19, conn=conn, decision_id="dec-m2-a")
63905:+        second = _live_order("trade-m2-economic-replacement", intent, shares=18.19, conn=conn, decision_id="dec-m2-b")
63914:+    from src.execution.executor import _live_order
63924:+        first = _live_order("trade-m2-float-a", first_intent, shares=18.19, conn=conn, decision_id="dec-m2-float-a")
63930:+        second = _live_order("trade-m2-float-b", second_intent, shares=18.19, conn=conn, decision_id="dec-m2-float-b")
64032:+    load_calibration_trade_facts,
64081:+        neg_risk=False,
64119:+        neg_risk=False,
64125:+        raw_response_json=json.dumps({"orderID": "ord-ws", "status": "live"}, sort_keys=True),
64156:+        expected_neg_risk=False,
64243:+    assert load_calibration_trade_facts(conn) == []
64253:+    confirmed = load_calibration_trade_facts(conn)
64300:+    """M3 is live-truth-gated; absent WS configuration is not an implicit PASS."""
64455:+# Reuse: Run when V2 SDK adapter, envelope provenance, or Q1 preflight behavior changes.
64485:+    neg_risk: bool = True
64504:+    def get_neg_risk(self, token_id):
64505:+        self.calls.append(("get_neg_risk", token_id))
64531:+    def get_neg_risk(self, token_id):
64532:+        self.calls.append(("get_neg_risk", token_id))
64697:+    assert "get_neg_risk" in (result.error_message or "")
64726:+def test_create_submission_envelope_captures_all_provenance_fields(tmp_path):
64753:+    assert envelope.neg_risk is True
64762:+def test_one_step_sdk_path_still_produces_envelope_with_provenance(tmp_path):
64781:+    fake = FakeTwoStepClient(post_response={"orderID": "ord-two", "status": "live"}, signed_order=signed)
64862:+def test_neg_risk_passthrough_v2_preserves_snapshot_value(tmp_path):
64865:+    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(neg_risk=True), order_type="GTC")
64871:+    assert envelope.neg_risk is True
64872:+    assert getattr(options, "neg_risk") is True
64873:+    assert result.envelope.neg_risk is True
64906:+def test_polymarket_client_live_submit_delegates_to_v2_adapter(tmp_path):
65008:+def test_old_v1_sdk_import_is_removed_from_live_client_paths():
65009:+    live_paths = [
65014:+    offenders = [path.as_posix() for path in live_paths if "py_clob_client" in path.read_text()]
65100:+            neg_risk=False,
65156:+            neg_risk=False,
65195:-        # From INTENT_CREATED: only SUBMIT_REQUESTED and REVIEW_REQUIRED are legal
65196:+        # From INTENT_CREATED: submit/cancel/provenance-boundary/review events are legal
65203:         ("INTENT_CREATED", "EXPIRED", []),
65280:+    MIGRATION_PACKET / "polymarket_live_money_contract.md",
65298:+def test_no_v2_low_risk_drop_in_in_active_docs() -> None:
65299:+    """CLOB V2 must be framed as P0 live-money work, not low-risk drop-in."""
65300:+    pattern = re.compile(r"V2 low[ -]?risk|low[ -]?risk drop[ -]?in", re.IGNORECASE)
65305:+def test_polymarket_live_money_contract_doc_exists() -> None:
65306:+    """The live-money contract is packet-local to avoid a new docs/architecture authority plane."""
65307:+    contract = MIGRATION_PACKET / "polymarket_live_money_contract.md"
65310:+        "V2 SDK (`py-clob-client-v2`) is the only live placement path",
65311:+        "Heartbeat is mandatory for GTC/GTD live resting orders",
65313:+        "No live placement may proceed when `CutoverGuard.current_state()` is not `LIVE_ENABLED`",
65340:+def test_no_live_path_imports_v1_sdk() -> None:
65341:+    """Z2 owns live-code SDK replacement; Z0 only installs the conditional antibody."""
65345:+        pytest.skip("Z2 has not shipped; V1 live import gate activates post-Z2")
65347:+    live_paths = [
65352:+    offenders = [str(path.relative_to(ROOT)) for path in live_paths if "from py_clob_client " in _read(path)]
65365:+| `src/risk_allocator/` | R3 A2 capital allocation, cap policy, governor state, and kill-switch enforcement | `src/risk_allocator/AGENTS.md`, `docs/reference/modules/riskguard.md` |
65369: | `docs/authority/` | Durable architecture and delivery law only | `docs/authority/AGENTS.md` |
65370: | `docs/reference/` | Domain, architecture, market/settlement, data/replay, failure-mode, and module references | `docs/reference/AGENTS.md` |
65376:+| `architecture/module_manifest.yaml` | Machine registry for module books, module routers, module-level dependencies, high-risk control routes such as CutoverGuard, and the R3 venue adapter boundary |
