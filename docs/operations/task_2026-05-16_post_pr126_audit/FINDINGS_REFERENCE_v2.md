# FINDINGS_REFERENCE v2 — post-PR-126 master index

Baseline: main HEAD `9259df3e9c` (2026-05-17). PR #126 (cascade liveness), PR #130 (ref/authority docs), PR #132/#133 (db_writer_lock Phase 0/0.5/1, Track A.6 daemon retrofit).
Predecessor (frozen, SUPERSEDED for master-index reading): `../task_2026-05-16_deep_alignment_audit/FINDINGS_REFERENCE.md`.

Severity scale: SEV-0 (ship blocker / money loss / silent corruption), SEV-1 (high — likely data integrity, masking risk), SEV-2 (medium — process/discipline), SEV-3 (low — hygiene).

## Numbering reconciliation (added Run #8)

v1 (FROZEN brief, `../task_2026-05-16_deep_alignment_audit/`) and v2 (this doc) DO NOT share F-numbering — same number can refer to entirely different defects. This document remains canonical for the post-PR-126 package; cross-walk to v1 numbers is in `RUN_8_resolution_sweep.md`. Going forward, all new findings receive a globally-unique F-number with no renumbering. See F28 in `RUN_8_findings.md` for the meta-defect.

| F#  | Title                                                                  | Sev   | Status                  | Owner module                 | First seen | Last verified |
|-----|------------------------------------------------------------------------|-------|-------------------------|------------------------------|------------|---------------|
| F1  | ZEUS_FORECASTS_DB legacy path                                          | SEV-1 | FIXED                   | src/state/db.py              | Run #1     | Run #7        |
| F2  | decision_id NULL on selection_hypothesis_fact (superseded by F25)      | SEV-1 | WORSE                   | src/selection/...            | Run #1     | Run #7        |
| F3  | unit-system co-mingling                                                | SEV-1 | OPEN                    | src/signal/, src/calibration | Run #2     | Run #5        |
| F4  | settle_status ghost column                                             | SEV-2 | FIXED                   | src/state/db.py              | Run #2     | Run #7        |
| F5  | collateral_ledger raw sqlite3.connect                                  | SEV-2 | OPEN-acknowledged       | src/state/collateral_ledger.py | Run #2   | Run #7        |
| F6  | candidate_fact orphan rows                                             | SEV-2 | OPEN                    | src/selection/               | Run #2     | Run #5        |
| F7  | order_intent / venue_command lineage                                   | SEV-1 | OPEN                    | src/execution/               | Run #3     | Run #5        |
| F8  | observations_v2 dual-write window                                      | SEV-2 | OPEN                    | src/data/, src/state/db.py   | Run #3     | Run #7        |
| F9  | calibration_pairs_v2 not promoted                                      | SEV-2 | OPEN-progress           | scripts/, src/calibration    | Run #3     | Run #7        |
| F10 | risk_state.db separate-process drift                                   | SEV-2 | OPEN                    | src/riskguard/               | Run #3     | Run #5        |
| F11 | BulkChunker yields LIVE only at chunk boundary                         | SEV-2 | OPEN-progress           | src/state/db_writer_lock.py  | Run #4     | Run #7        |
| F12 | migration script idempotency unverified                                | SEV-2 | OPEN                    | scripts/migrations/          | Run #4     | Run #6        |
| F13 | settlement_commands ux index excludes only 2 terminals (→ F27)         | SEV-1 | NEW-SCOPE               | src/state/db.py              | Run #4     | Run #7        |
| F14 | redeem cascade liveness contract missing                               | SEV-0 | FIXED (PR #126)         | architecture/                | Run #4     | Run #7        |
| F15 | chain reconciliation skip_voiding interplay                            | SEV-1 | OPEN                    | src/state/chain_reconciliation.py | Run #4 | Run #5      |
| F16 | redeem auto-execution gate missing operator path                       | SEV-0 | FIXED (PR #126)         | src/execution/settlement_commands.py | Run #4 | Run #7  |
| F17 | forecasts.db user_version drift risk                                   | SEV-1 | FIXED-held              | src/state/db.py              | Run #5     | Run #7        |
| F18 | INSERT OR IGNORE silent loss                                           | SEV-2 | OPEN                    | src/data/market_scanner.py   | Run #5     | Run #6        |
| F19 | collateral ledger schema not in registry                               | SEV-3 | OPEN                    | architecture/                | Run #5     | Run #5        |
| F20 | position_lots reconciliation                                           | SEV-2 | OPEN                    | src/state/                   | Run #5     | Run #5        |
| F21 | legacy observation_instants writer active                              | SEV-2 | OPEN                    | src/data/hourly_instants_append.py:229 | Run #6 | Run #7  |
| F22 | market_events_v2 dual-write via raw sqlite3.connect                    | SEV-2 | OPEN-acknowledged       | src/data/market_scanner.py:610 | Run #6   | Run #7        |
| F23 | migration runner architecturally bare (no ledger)                      | SEV-1 | OPEN                    | scripts/migrations/          | Run #6     | Run #7        |
| F24 | decision_id NULL accelerated 693→1518 (superseded by F25)              | SEV-1 | EXPANDED → F25          | src/selection/, src/signal/  | Run #6     | Run #7        |
| **F25** | **Triple-NULL systemic snapshot-write failure (3 fact tables)**     | **SEV-0** | **NEW**             | src/selection/, src/opportunity/, src/signal/ | Run #7 | Run #7 |
| **F26** | **Two-truth SQLITE_CONNECT_ALLOWLIST divergence**                   | **SEV-2** | **NEW**             | src/state/db_writer_lock.py + tests/conftest.py | Run #7 | Run #7 |
| **F27** | **REDEEM_OPERATOR_REQUIRED unique-index lockout (PR-126 review gap)** | **SEV-1** | **NEW**             | src/state/db.py + src/execution/settlement_commands.py | Run #7 | Run #7 |
| **F28** | **META: dual-index numbering inconsistency v1↔v2**                  | **SEV-2** | **NEW (Run #8)**    | docs/operations/task_2026-05-16_*                       | Run #8 | Run #8 |
| **F29** | **REDEEM_REVIEW_REQUIRED not excluded from UNIQUE INDEX (sibling of F27)** | **SEV-2** | **NEW (Run #8)** | src/execution/settlement_commands.py                    | Run #8 | Run #8 |
| **F30** | **Migration runner does not enforce last_reviewed header drift**    | **SEV-3** | **NEW (Run #8)**    | scripts/migrations/                                     | Run #8 | Run #8 |
| **F31** | **market_events_v2 reader-side audit gap (deferred from v1.F19)**   | **SEV-2** | **NEW (Run #8)**    | src/, scripts/                                          | Run #8 | Run #8 |

## Carry-forward note
Old `FINDINGS_REFERENCE.md` in the predecessor package now bears a "SUPERSEDED" header pointing here. Run-narrative files (RUN_1…RUN_6_findings.md) remain canonical for their own narrative content.
| **F32** | **Oracle bridge writer not scheduled → runtime permanently MISSING for every city** | **SEV-1** | **NEW (Run #9)** | scripts/bridge_oracle_to_calibration.py + cron | Run #9 | Run #9 |
| **F33** | **Daemon does not escalate on persistent oracle-MISSING-everywhere state**           | **SEV-2** | **NEW (Run #9)** | src/strategy/oracle_penalty.py + RiskGuard notify | Run #9 | Run #9 |
| **F34** | **Passive-only entry pricing in thin books → ~89% non-fill rate**                    | **SEV-3** | **NEW (Run #9)** | src/strategy/market_analysis.py + executor.py    | Run #9 | Run #9 |
| **F35** | **bridge_oracle_to_calibration.py unscheduled (operator-memory archeology confirms F32 is real and NEVER-RAN-IN-PROD)** | **SEV-1** | **NEW (Run #10)** | scripts/bridge_oracle_to_calibration.py + cron | Run #10 | Run #10 |
| **F36** | ~~Settlement live tables empty since 2026-05-07~~ → **DEFECT-INVALID-PROVENANCE (Run #11)**: Run #10 queried wrong DB; harvester writes to zeus-forecasts.db (3634 VERIFIED rows since 5/7, latest 2026-05-17T05:46Z). See RUN_11. | ~~SEV-1~~ | **RETRACTED (Run #11)** | src/ingest/harvester_truth_writer.py:9,395 → zeus-forecasts.db | Run #10 | Run #11 |
| **F37** | **calibration-transfer-eval runs weekly but iterates 0 models (input table empty)** | **SEV-2** | **NEW (Run #10)** | scripts/evaluate_calibration_transfer_oos.py + launchd | Run #10 | Run #10 |
| **F38** | ~~calibration_pairs_v2 = 0 rows~~ → **DEFECT-INVALID-PROVENANCE (Run #11)**: actual count in zeus-forecasts.db is **91,040,450 rows**; zero in zeus-world.db is correct post-K1 archive. See RUN_11 + new F41. | ~~SEV-1~~ | **RETRACTED (Run #11)** | (retracted) | Run #10 | Run #11 |
| **F39** | **com.zeus.calibration-transfer-eval plist comment lies ("DO NOT LOAD") vs reality (loaded + running)** | **SEV-3** | **NEW (Run #10) Cat-N** | ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist | Run #10 | Run #10 |

| **F40** | **scripts/bridge_oracle_to_calibration.py hardcodes `state/zeus-world.db` after PR #114 K1 split moved settlements to zeus-forecasts.db; bridge would emit `{}` even when scheduled** | **SEV-1** | **NEW (Run #11)** Cat-J + Cat-K | scripts/bridge_oracle_to_calibration.py:71 | Run #11 | Run #11 |
| **F41** | **scripts/evaluate_calibration_transfer_oos.py uses `get_world_connection`; reads dead calibration_pairs_v2 (0 rows in world DB; 91M rows in forecasts DB). Live log regression 2026-05-10→2026-05-17 confirms** | **SEV-1** | **NEW (Run #11)** Cat-J | scripts/evaluate_calibration_transfer_oos.py:684 | Run #11 | Run #11 |
| **F42** | **META: PR #114 K1 split migrated writers but did NOT sweep ~30 reader callers using `get_world_connection` against forecast-class tables. F40 + F41 are confirmed; ~10 more files suspect.** | **SEV-1** | **NEW (Run #11)** Cat-K + Cat-J META | src/, scripts/ (~30 callers) | Run #11 | Run #11 |

## Run #11 retraction note (2026-05-17)
F36 + F38 retracted as DEFECT-INVALID-PROVENANCE. Root cause of Run #10's error: the K1 forecast DB split (PR #114, commit `eba80d2b9d`, merged 2026-05-14) MOVED 7 forecast-class tables off `zeus-world.db` and renamed source tables to `_archived_2026_05_11`. Run #10 queried `zeus-world.db` (post-archive empty shell) and `zeus_trades.db` (settlements never lived there) but not `zeus-forecasts.db` (the actual K1 target). Two real regressions surfaced: F40, F41 — reader-side callers that PR #114 missed. F42 frames the meta — likely ~10 more silent reader regressions exist.

## Run #12 additions (F43–F80) — F42 reader caller sweep
| **F43** | **src/main.py:1372 boot smoke probes settlements on world.db → 0 rows; passes vacuously** | **SEV-2** | **NEW (Run #12)** Cat-J + Cat-K | src/main.py:1372 | Run #12 | Run #12 |
| **F44** | **src/execution/harvester.py:495 reads observations via conn-param; caller-trace required** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/execution/harvester.py:495 | Run #12 | Run #12 |
| **F45** | **src/ingest/harvester_truth_writer.py:208 reads observations via conn-param** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/ingest/harvester_truth_writer.py:208 | Run #12 | Run #12 |
| **F46** | **src/state/db.py:3620,4509 read market_events_v2 via conn-param** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/state/db.py:3620,4509 | Run #12 | Run #12 |
| **F47** | **src/data/calibration_transfer_policy.py:560 reads calibration_pairs_v2 via conn-param** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/data/calibration_transfer_policy.py:560 | Run #12 | Run #12 |
| **F48** | **src/engine/monitor_refresh.py:1041 reads settlements via conn-param** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/engine/monitor_refresh.py:1041 | Run #12 | Run #12 |
| **F49** | **src/state/source_run_repo.py:163,169 (get_source_run, get_latest_source_run) read source_run via conn-param** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/state/source_run_repo.py:163,169 | Run #12 | Run #12 |
| **F50** | **src/data/daily_observation_writer.py:82 reads observations via conn-param** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | src/data/daily_observation_writer.py:82 | Run #12 | Run #12 |
| **F51** | **scripts/baseline_experiment.py:149,262 reads observations/settlements on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/baseline_experiment.py | Run #12 | Run #12 |
| **F52** | **scripts/rebuild_calibration_pairs_canonical.py:191 reads observations on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/rebuild_calibration_pairs_canonical.py:191 | Run #12 | Run #12 |
| **F53** | **scripts/rebuild_settlements.py:158,178 reads observations on world (and writes settlements there)** | **SEV-2** | **NEW (Run #12)** DEAD-READ + DEAD-WRITE | scripts/rebuild_settlements.py | Run #12 | Run #12 |
| **F54** | **scripts/backfill_observations_from_settlements.py:37,43 reads migrated tables on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/backfill_observations_from_settlements.py | Run #12 | Run #12 |
| **F55** | **scripts/etl_asos_wu_offset.py:43 reads observations on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ (write-side correct) | scripts/etl_asos_wu_offset.py:43 | Run #12 | Run #12 |
| **F56** | **scripts/audit_city_data_readiness.py:44,147,154 reads settlements/observations on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/audit_city_data_readiness.py | Run #12 | Run #12 |
| **F57** | **scripts/antibody_scan.py:99,123,181 reads migrated tables on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/antibody_scan.py | Run #12 | Run #12 |
| **F58** | **scripts/etl_temp_persistence.py:65 reads observations on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/etl_temp_persistence.py:65 | Run #12 | Run #12 |
| **F59** | **scripts/automation_analysis.py uses get_world_connection alias for migrated-table queries** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/automation_analysis.py:28 | Run #12 | Run #12 |
| **F60** | **scripts/backfill_wu_daily_all.py:334,349,363,397,410,516,739 reads/writes migrated tables on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ + DEAD-WRITE | scripts/backfill_wu_daily_all.py | Run #12 | Run #12 |
| **F61** | **scripts/migrate_phase2_cycle_stratification.py:127–381 mutates migrated tables on world; ledger-bypassing** | **SEV-2** | **NEW (Run #12)** DEAD-READ + DEAD-WRITE | scripts/migrate_phase2_cycle_stratification.py | Run #12 | Run #12 |
| **F62** | **scripts/backfill_tigge_snapshot_p_raw_v2.py:133,211 reads calibration_pairs_v2/ensemble_snapshots_v2 on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ + DEAD-WRITE | scripts/backfill_tigge_snapshot_p_raw_v2.py | Run #12 | Run #12 |
| **F63** | **scripts/data_chain_monitor.sh:26–29 raw sqlite3.connect('state/zeus-world.db') on source_run → permanently 0; observability blindness** | **SEV-1** | **NEW (Run #12)** DEAD-READ HOT | scripts/data_chain_monitor.sh:26 | Run #12 | Run #12 |
| **F64** | **scripts/diagnose_low_high_alignment.py:40,188,224,384 reads ensemble_snapshots_v2 on world (default DB)** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/diagnose_low_high_alignment.py | Run #12 | Run #12 |
| **F65** | **scripts/refit_platt_v2.py:323,438,486 reads calibration_pairs_v2 on world (default --db)** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/refit_platt_v2.py | Run #12 | Run #12 |
| **F66** | **scripts/promote_calibration_v2_stage_to_prod.py:665,680 reads/writes calibration_pairs_v2 on world (default --prod-db)** | **SEV-2** | **NEW (Run #12)** DEAD-READ + DEAD-WRITE | scripts/promote_calibration_v2_stage_to_prod.py | Run #12 | Run #12 |
| **F67** | **scripts/backfill_ens.py:46 reads settlements on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/backfill_ens.py:46 | Run #12 | Run #12 |
| **F68** | **scripts/backfill_settlements_via_gamma_2026.py:145,276,350 reads settlements_v2 on world (one-shot)** | **SEV-3** | **NEW (Run #12)** DEAD-READ | scripts/backfill_settlements_via_gamma_2026.py | Run #12 | Run #12 |
| **F69** | **scripts/audit_divergence_exit_counterfactual.py:59 reads settlements on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/audit_divergence_exit_counterfactual.py:59 | Run #12 | Run #12 |
| **F70** | **scripts/cleanup_ghost_positions.py:85 reads settlements on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/cleanup_ghost_positions.py:85 | Run #12 | Run #12 |
| **F71** | **scripts/check_forecast_live_ready.py:169 source_run readiness gate likely reads world (caller-conn verify); silent vacuous-pass risk** | **SEV-1** | **NEW (Run #12)** DEAD-READ HOT | scripts/check_forecast_live_ready.py:169 | Run #12 | Run #12 |
| **F72** | **scripts/venus_sensing_report.py:192–194 imports BOTH world+forecasts helpers; settlements read path needs verify** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | scripts/venus_sensing_report.py:192 | Run #12 | Run #12 |
| **F73** | **scripts/backfill_low_contract_window_evidence.py:203,228 reads ensemble_snapshots_v2 on world (default --db-path)** | **SEV-2** | **NEW (Run #12)** DEAD-READ | scripts/backfill_low_contract_window_evidence.py | Run #12 | Run #12 |
| **F74** | **scripts/migrate_observations_k1.py raw world-open mutates observations (post-K1 should retire or repoint to forecasts.db)** | **SEV-3** | **NEW (Run #12)** DEAD-WRITE on re-invoke | scripts/migrate_observations_k1.py | Run #12 | Run #12 |
| **F75** | **scripts/promote_calibration_pairs_v2.py reads/writes calibration_pairs_v2 on world** | **SEV-2** | **NEW (Run #12)** DEAD-READ + DEAD-WRITE | scripts/promote_calibration_pairs_v2.py | Run #12 | Run #12 |
| **F76** | **scripts/ingest_grib_to_snapshots.py:575 writes ensemble_snapshots_v2 (likely already CORRECT; verify helper)** | **SEV-3** | **NEW (Run #12)** AMBIGUOUS | scripts/ingest_grib_to_snapshots.py:575 | Run #12 | Run #12 |
| **F77** | **scripts/backfill_london_f_to_c_2026_05_08.py reads settlements_v2/settlements on world (one-shot, dated)** | **SEV-3** | **NEW (Run #12)** DEAD-READ if re-invoked | scripts/backfill_london_f_to_c_2026_05_08.py | Run #12 | Run #12 |
| **F78** | **scripts/healthcheck.py:653 settlements_v2 read path needs verify (file uses get_forecasts_connection at L1064)** | **SEV-3** | **NEW (Run #12)** AMBIGUOUS | scripts/healthcheck.py:653 | Run #12 | Run #12 |
| **F79** | **scripts/live_health_probe.py:197 settlements_v2 read; raw sqlite3.connect path needs verify** | **SEV-2** | **NEW (Run #12)** AMBIGUOUS | scripts/live_health_probe.py:197 | Run #12 | Run #12 |
| **F80** | **scripts/backfill_uma_resolution_2026.py:376 reads settlements_v2 (one-shot; verify it's already executed)** | **SEV-3** | **NEW (Run #12)** AMBIGUOUS | scripts/backfill_uma_resolution_2026.py:376 | Run #12 | Run #12 |

## Run #12 META

F42 (the meta finding from Run #11) is now **CONFIRMED + DECOMPOSED** into F43 (src/) + F44–F50 (src/ AMBIGUOUS) + F51–F80 (scripts/). Total surfaced: 1 src/ confirmed DEAD-READ, 7 src/ AMBIGUOUS, ~21 scripts/ DEAD-READs, ~9 scripts/ AMBIGUOUS, plus retirement candidates. F43+ sibling-migration scan: NONE. Karachi 5/17 blast-radius re-affirm: NONE (only F40 was hot, already-unscheduled).


## Run #13 status delta (PR #137 merge + caller-trace close-out)

PR #137 merged `2026-05-17T23:15:14Z`. Status updates:

| F#  | Previous status | Run #13 status | Reason |
|-----|-----------------|----------------|--------|
| F2  | WORSE → was OPEN | FIXED (PR #137) | `_record_selection_family_facts` decision_id forwarding. |
| F7  | OPEN | FIXED (PR #137) | execution_fact.command_id + COALESCE preservation. |
| F8  | OPEN | FIXED (PR #137) | position_events.occurred_at CHECK + 3-row backfill. |
| F15 | OPEN | FIXED (PR #137) | settlements_v2 1583-row backfill. |
| F18 | OPEN | FIXED (PR #137) | INSERT OR IGNORE zero-insert WARNING. |
| F23 | OPEN | FIXED (PR #137) | scripts/migrations runner + ledger. |
| F25 | NEW | FIXED (PR #137 Strategy R) | _make_rejection_decision sentinel. |
| F26 | NEW | DEFERRED → #22 | naive dedup blocked by 65 antibody failures. |
| F27 | NEW | FIXED (PR #137) | UNIQUE INDEX REDEEM_OPERATOR_REQUIRED exclusion. |
| F29 | NEW | FIXED (PR #137) | UNIQUE INDEX REDEEM_REVIEW_REQUIRED exclusion. |
| F30 | NEW | FIXED (PR #137) | header drift gate in runner. |
| F40 | NEW | FIXED (PR #137) | bridge_oracle_to_calibration K1 repoint. |
| F41 | NEW | FIXED (PR #137) | cal-transfer-eval K1 repoint. |
| F42-batch-2 | OPEN | PARTIALLY-CLOSED (PR #137) | 4 scripts batch-2 repointed; 11 src/ verified clean; 17 scripts residue. |
| F44 | AMBIGUOUS | **CLEAN** (Run #13 trace) | harvester.py forecasts-routed via L682. |
| F45 | AMBIGUOUS | **CLEAN** (Run #13 trace) | harvester_truth_writer doc L9 confirms forecasts. |
| F46 | AMBIGUOUS | **CONFIRMED-OPEN** (Run #13 trace) | cycle_runtime upstream dual-write — escalated to F81. |
| F47 | AMBIGUOUS | **CLEAN** (Run #13 trace) | evaluator path threads forecasts conn (matches PR #137 "11 src/ clean"). |
| F48 | AMBIGUOUS | **CONFIRMED-DEAD-READ** (Run #13) | monitor_refresh settlements read on world conn → silent-zero deltas. |
| F49 | AMBIGUOUS | **DEAD** (no callers) | retire `get_source_run` / `get_latest_source_run`. |
| F50 | AMBIGUOUS | **CLEAN** (Run #13) | ingest_main forecasts-routed; 3 backfill callers → F84. |

## Run #13 additions (F81–F84)

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| **F81** | **K1 dual-write LEAK to zeus-world.db post-PR-114 (market_events_v2 2112 rows + observations 145 rows POST-K1)** | **SEV-1** | **NEW (Run #13)** HOT | `src/data/market_scanner.py:610` (F22) + cycle_runtime upstream | Run #13 | Run #13 |
| **F82** | **K1 triple-write FAN-OUT to zeus_trades.db (market_events_v2 7964 rows, latest 2026-05-17T14:59 LIVE)** | **SEV-1** | **NEW (Run #13)** HOT | unknown — provenance audit needed | Run #13 | Run #13 |
| **F83** | **Schema drift: zeus-world.db observations has NO recorded_at column; forecasts.db has it (sibling of F81)** | **SEV-2** | **NEW (Run #13)** | `state/zeus-world.db` vs `state/zeus-forecasts.db` | Run #13 | Run #13 |
| **F84** | **3 HKO/WU backfill scripts call daily_observation_writer; caller conn-source not verified (sibling of F60)** | **SEV-3** | **NEW (Run #13)** AMBIGUOUS | scripts/backfill_hko_xml.py, scripts/backfill_hko_daily.py, scripts/backfill_wu_daily_all.py | Run #13 | Run #13 |


## Run #14 status delta (audit sweep, branch fix/wave-2-lineage-and-k1-cleanup-2026-05-17 @ b973ece)

Track A (market_events): re-classified F22 + F81 + F82 from triple-write to **dual active writer** (forecasts via market_scanner.py:610, trades via cycle_runner-injected conn). World.db copy DEAD. Verdict **A2 (two-zone, forecasts-authoritative)**. 1-line fix path: `log_forward_market_substrate` opens own forecasts conn. Karachi 5/17 + 5/19 benign in current window.

Track B (F46 / F48): root causes pinned + 1-line fixes specified (see RUN_14_track_B). F48 flagged **Karachi-HOT** — `_persistence_discount` silently returns 1.0 on 3-day-NULL fallback.

Track C (alias lint): 17 sites confirmed (1 FP, 16 rewrite targets). Patch = `tools/lint/zeus_db_alias.py` + 16 mechanical seds.

Track D (daemon supervision): **+8 new findings F85–F92** below. F87 flagged **Karachi-CRITICAL** (forecast-live daemon DOWN).

## Run #14 additions (F85–F92)

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| **F85** | Daemon stdout/stderr inversion: all noise on .err, .log files dead (7 daemons) | SEV-2 | NEW (Run #14) | launchd plists + each daemon entry-point | Run #14 | Run #14 |
| **F86** | SIGTERM `exit -15` on live-trading/riskguard/venue-heartbeat without forensic trail | SEV-2 | NEW (Run #14) | `src/control/heartbeat_supervisor.py`, riskguard, live_trading | Run #14 | Run #14 |
| **F87** | **`com.zeus.forecast-live` launchctl exit code = 1 (FAILED), .err = 3 MB; daemon NOT running** | **SEV-1** | **NEW (Run #14)** **HOT** | `~/Library/LaunchAgents/com.zeus.forecast-live.plist` | Run #14 | Run #14 |
| **F88** | `calibration-transfer-eval` plist name suggests live, actually once-daily 04:00 | SEV-3 | NEW (Run #14) | `~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist` | Run #14 | Run #14 |
| **F89** | `heartbeat-sensor` lives in cron `*/30`, not launchd → split supervision topology | SEV-3 | NEW (Run #14) | `crontab -l` + `scripts/heartbeat_sensor.sh` | Run #14 | Run #14 |
| **F90** | **`cron/jobs.json` 82 KB vs `crontab -l` only 2 lines — 40-job catalog NOT scheduled** | **SEV-1** | **NEW (Run #14)** | `cron/jobs.json` | Run #14 | Run #14 |
| **F91** | Heartbeat JSONs written every minute; consumer + alert path unverified | SEV-2 | NEW (Run #14) AMBIGUOUS | `state/heartbeats/zeus-*.json` + `src/control/heartbeat_supervisor.py` | Run #14 | Run #14 |
| **F92** | Riskguard auth/api-key 400 → derive-api-key fallback succeeds silently (no metric) | SEV-2 | NEW (Run #14) | `src/riskguard/*` | Run #14 | Run #14 |

## Run #15 Track 1 additions (F90 reframe + F90a/b/c + F93–F95)

Track 1 (F90 deep dive): Run #14's F90 premise ("82KB jobs.json vs 2-line crontab; 40 jobs un-scheduled") was wrong on both numbers and conclusion. `crontab -l` is 71 lines / 24 active commands; `jobs.json` IS executed by `ai.openclaw.node` daemon. 31 disabled-flag jobs are intentionally dormant, not silently un-scheduled. F90 reframed; sub-findings extracted; 3 new findings F93–F95.

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| **F90** | **REFRAMED**: jobs.json↔crontab↔launchd source-of-truth ambiguity (was: "40 jobs un-scheduled" — DISPROVEN) | SEV-3 | REFRAMED (Run #15) | `cron/jobs.json` + crontab + launchd | Run #14 | **Run #15 Track 1** |
| **F90a** | **3 enabled jobs failing every tick**: `memory-observer` + `finance-subagent-scanner` + `finance-subagent-scanner-offhours` reject `payload.model 'openai-codex/gpt-5.4-mini'` | **SEV-1** | **NEW (Run #15 T1)** | `cron/jobs.json` payload.model + `openclaw.json` agents.defaults.model | Run #15 T1 | Run #15 T1 |
| **F90b** | `memory-reflector` + `memory-dream-cycle` timing out on most ticks (`cron: job execution timed out`) | SEV-2 | NEW (Run #15 T1) | `cron/jobs.json` payload.timeoutMs + memory pipeline | Run #15 T1 | Run #15 T1 |
| **F90c** | No `cron_reconcile` tool across 3 scheduler layers (jobs.json / crontab / launchd) | SEV-3 | NEW (Run #15 T1) | `tools/ops/cron_reconcile.py` (to be created) | Run #15 T1 | Run #15 T1 |
| **F93** | **Karachi-direct**: no job in any layer refreshes Karachi WU/HKO data more than once/day; single `oracle_snapshot_listener.py` @ 10:00 UTC = SPOF | SEV-3 | NEW (Run #15 T1) | crontab oracle entry | Run #15 T1 | Run #15 T1 |
| **F94** | `cron/jobs-state.json` is structurally empty (all 42 entries `{}`); real state lives in `cron/runs/<jobid>.jsonl` — likely cause of F90 misread | SEV-3 | NEW (Run #15 T1) | `cron/jobs-state.json` writer | Run #15 T1 | Run #15 T1 |
| **F95** | **Karachi-defensive**: `zeus-antibody-scan` + `zeus-daily-audit` DISABLED in jobs.json since 2026-04-14/15 with NO crontab/launchd replacement → Karachi regression coverage gap | SEV-2 | NEW (Run #15 T1) | `cron/jobs.json` enabled flags | Run #15 T1 | Run #15 T1 |

## Run #15 Track 3 additions (F91/F86 confirmations + F99–F101)

Track 3 (heartbeat consumer trace + SIGTERM forensic): F91 resolved AMBIGUOUS → CONFIRMED-NO-WIRE (4 of 5 heartbeat surfaces unread by any autonomous loop; only HB-4 venue-heartbeat-keeper.json is consumed, and as a functional lease-token seed for live-trading startup, not as an alerter). F86 confirmed: the 3 daemons that exit `-15` (live-trading, riskguard, venue-heartbeat) are exactly the 3 daemons without SIGTERM handlers; .err files contain zero shutdown trace. Lowest-cost antibody: extend `scripts/healthcheck.py` to read `last_exit_status` from `launchctl print` output (lifts forensic surface into existing every-30-min cron path without touching live-money daemon code). Numbering note: F96/F97/F98 reserved by Track 2 (different bug class — monitor_refresh persistence); Track 3 uses F99/F100/F101.

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| **F86** | SIGTERM `exit -15` on live-trading/riskguard/venue-heartbeat without forensic trail | SEV-2 | **CONFIRMED (Run #15 T3)** — 3/3 SIGTERM'd daemons lack handlers; .err logs contain zero shutdown trace; macOS launchctl `last_exit_status` is sole forensic surface and is NOT read by `healthcheck.py` | `src/main.py`, `src/riskguard/riskguard.py`, `src/control/heartbeat_supervisor.py` + `scripts/healthcheck.py` | Run #14 | Run #15 T3 |
| **F91** | Heartbeat JSONs written every 30–60 s; consumer + alert path unverified | SEV-2 | **CONFIRMED-NO-WIRE (Run #15 T3)** — 5 writers, 1 functional consumer (HB-4 venue → live-trading startup), 0 autonomous alerting consumers for HB-1/HB-2/HB-3/HB-5 | `scripts/healthcheck.py` (missing reads) + 5 writer sites | Run #14 | Run #15 T3 |
| **F99** | Heartbeat write/read asymmetry: `check_daemon_heartbeat.py` + `check_forecast_live_ready.py` exist but are unscheduled; `healthcheck.py` does not grep any heartbeat JSON | SEV-2 | NEW (Run #15 T3) | `scripts/healthcheck.py` + `scripts/heartbeat_dispatcher.py` | Run #15 T3 | Run #15 T3 |
| **F100** | `daemon-heartbeat-ingest.json` + `oracle_error_rates.heartbeat.json` have ZERO readers anywhere in src/ or scripts/ — pure disk churn | SEV-2 | NEW (Run #15 T3) | `src/ingest_main.py:170` + `src/state/paths.py:183` writers (orphan) | Run #15 T3 | Run #15 T3 |
| **F101** | Schema drift across 5 heartbeat payloads (3/3/7/13/N fields, different key names) blocks generic staleness checker | SEV-3 | NEW (Run #15 T3) | needs `src/state/heartbeat_envelope.py` | Run #15 T3 | Run #15 T3 |

## Run #15 Track 2 additions (F102–F104) + F48 status update

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| F48 | monitor_refresh.py:1041 settlements DEAD-READ — 2nd pass | **SEV-1** | **HOT-FIX-SPEC** (Run #15 T2) — Run #14 1-liner insufficient (see F103); requires `forecasts.`-schema-qualifier + counter | `src/engine/monitor_refresh.py:1040-1086` | Run #11/13 | Run #15 T2 |
| **F102** | `temp_persistence` table empty in trades.db (0), world.db (0); missing in forecasts.db — secondary DEAD-READ at `monitor_refresh.py:1064` blocks discount even after F48 §5 fix | **SEV-2** | **NEW (Run #15 T2)** HOT | `src/engine/monitor_refresh.py:1064` + repopulation pipeline | Run #15 T2 | Run #15 T2 |
| **F103** | Run #14 Track B F48 fix (bare-name `settlements_v2` rename) is no-op — SQLite ATTACH name-resolution requires schema-qualifier in mixed-DB conns | **SEV-1** | **NEW (Run #15 T2)** META | `tools/lint/zeus_db_alias.py` (extend) | Run #15 T2 | Run #15 T2 |
| **F104** | `PERSISTENCE_CHECK_DISABLED` warning never observed in logs despite permanent DEAD-READ — observability gap | SEV-3 | NEW (Run #15 T2) | `src/engine/monitor_refresh.py:1067` + log config | Run #15 T2 | Run #15 T2 |

> See `RUN_15_track2_f48_hot_fix.md` for §5 hot-fix spec (Edits A–C), §6 antibody test, §7 Karachi 5/17 blast-radius re-assessment, and §8 full F102–F104 detail.


## Run #16 Track D additions (F90a expansion + F105)

Track D (F90a deep-dive + cron model-allowlist sweep): F90a precise root cause is two-layer — provider `openai-codex` does not register model id `gpt-5.4-mini` (only `gpt-5.4`), AND the `agents.defaults.models` allowlist contains only `openai/gpt-5.4` + `openai/gpt-5.5` (neither `openai-codex/...` nor `openai/gpt-5.4-mini` is allowlisted). Recommended substitute: `openai-codex/gpt-5.4` (registered + closest semantic match). 9 disabled jobs share the same bad string and are swept by the same JSON patch.

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| F90a | 3 enabled jobs reject `payload.model = "openai-codex/gpt-5.4-mini"` | **SEV-1** | **HOT-FIX-SPEC (Run #16 T D)** — recommended substitute `openai-codex/gpt-5.4`; jq one-liner + kickstart in RUN_16_track_D §5–6 | `cron/jobs.json` payload.model (3 enabled + 9 disabled siblings) | Run #15 T1 | Run #16 T D |
| **F105** | **Allowlist drift META**: `agents.defaults.models` is informational only — per-agent `model.primary = "openai/gpt-5.4-mini"` (4 agents) is unregistered yet works; `minimax-portal/MiniMax-M2.7` works in 24 jobs but is not allowlisted; `openai/gpt-5.5` is allowlisted but registered nowhere | SEV-3 | **NEW (Run #16 T D)** META | `openclaw.json` agents.defaults.models + per-agent primaries + models.providers registry | Run #16 T D | Run #16 T D |

> See `RUN_16_track_D_f90a_model_allowlist_fix.md` §3 (root cause), §4 (per-job substitute), §5 (JSON patch + verification), §6 (kickstart), §7 (D1–D5 latent drifts), §8 (Karachi blast radius).


## Run #16 Track A — F87 close + F85 root cause (2026-05-17)

Track A: F87 false-alarm formal close + F85 root cause + fix spec. READ-ONLY production; no code or plist mutated. See `RUN_16_track_A_f85_log_routing_f87_close.md` and `LEARNINGS.md` (new antibody file).

| F#  | Title | Sev | Status | Owner | First seen | Last verified |
|-----|-------|-----|--------|-------|------------|----------------|
| **F87** | `com.zeus.forecast-live` flagged "DOWN" in Run #14 | ~~SEV-1 HOT~~ | **CLOSED-FALSE-ALARM (Run #16 A)** — PID 10397 healthy; `.err` mtime within 1 min; Run #14 misread `launchctl list` column 2 (LAST exit) as current state. Cross-check rule logged in LEARNINGS §1. | `~/Library/LaunchAgents/com.zeus.forecast-live.plist` | Run #14 | Run #16 A |
| **F85** | Daemon stdout/stderr inversion: all 7 `.err` huge + fresh, all 7 `.log` 0 B / stale | SEV-2 | **ROOT-CAUSE-PINNED + FIX-SPECIFIED (Run #16 A)** — plist layer ruled out (7/7 distinct `.log`/`.err` paths); root cause = `logging.basicConfig()` default `StreamHandler(sys.stderr)` at 4 daemon entry points (`src/main.py:1332`, `src/ingest_main.py:1035`, `src/ingest/forecast_live_daemon.py:664`, `src/riskguard/riskguard.py:1446`). Dual-handler patch spec'd; verification probe defined. No code mutated. | 4 daemon `main()` entry points | Run #14 | Run #16 A |

> See `RUN_16_track_A_f85_log_routing_f87_close.md` §1 (F87 evidence + close), §2 (F85 root cause), §3 (text-block fix spec), §5 (verification probe). New cross-run antibody catalog: `LEARNINGS.md`.
