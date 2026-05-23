# MASS TRIAGE: Audit Findings Consolidation (2026-05-17)

## Executive Summary
This triage consolidates findings from three major audit corpuses covering the Karachi 5/17 remediation window. Findings already merged to main via PR #137 (F2, F7, F8, F15, F18, F23, F25, F26, F29, F30, F40, F41, F42) have been excluded.

- **K-Axes Identified**: K1-Split-Reader-Regressions (F46-F84), Live-Trading-Observability (F85-F87), and Heartbeat-Asymmetry (F91, F99-F101).
- **Critical Path**: F87 (daemon failure) and F108 (stuck positions) are prioritized for immediate resolution.

## Findings Triage

| ID | Subject | Source File | Status | Classification | Confidence |
|:---|:---|:---|:---|:---|:---|
| F14 | `submit_redeem` has no production caller | deep_alignment_audit/REPORT.md | pending | DEFER (Future scope) | HIGH |
| F16 | `wrap_unwrap_commands.py` dead state machine | deep_alignment_audit/REPORT.md | pending | DEFER (Future scope) | HIGH |
| F17 | `validated_calibration_transfers` unscheduled | deep_alignment_audit/REPORT.md | pending | TRIVIAL_BATCH (Registry/Scheduler) | HIGH |
| F19 | Cross-DB `market_events_v2` asymmetry | deep_alignment_audit/REPORT.md | pending | STRUCTURAL (K1 fallout) | HIGH |
| F20 | `ensemble_snapshots` 116 dead legacy rows | deep_alignment_audit/REPORT.md | in-flight | IN_FLIGHT (Purge cycle) | HIGH |
| F27 | REDEEM_OPERATOR_REQUIRED unique-index lockout | deep_alignment_audit/REPORT.md | pending | TRIVIAL_BATCH (Schema/Review) | HIGH |
| F32 | Oracle bridge writer not scheduled | post_pr126_audit/RUN_14_track_B | pending | TRIVIAL_BATCH (Scheduler) | HIGH |
| F33 | No escalation on persistent MISSING | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (Alerting logic) | MED |
| F35 | bridge_oracle_to_calibration unscheduled | post_pr126_audit/RUN_14_track_B | pending | TRIVIAL_BATCH (Scheduler) | HIGH |
| F39 | cal-transfer-eval plist intent vs reality | deep_alignment_audit/RUN_3 | pending | DEFER (Operator Action) | HIGH |
| F43 | K1-helper world-table qualification regression | post_karachi_remediation/F43_F44 | in-flight | IN_FLIGHT (Karachi-tracer) | HIGH |
| F44 | observation_instants_v2 writer dead | post_karachi_remediation/F43_F44 | pending | STRUCTURAL (Data provenance) | HIGH |
| F46 | `cycle_runtime` upstream of K1 dual-write | post_pr126_audit/RUN_14_track_A | pending | STRUCTURAL (K1 fallout) | MED |
| F48 | `monitor_refresh.py` reads legacy settlements | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | HIGH |
| F63 | `data_chain_monitor.sh` raw connect to world | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | HIGH |
| F71 | `check_forecast_live_ready` vacuous pass risk | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | HIGH |
| F81 | K1 dual-write LEAK to zeus-world.db | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | HIGH |
| F82 | K1 triple-write FAN-OUT to zeus_trades.db | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | HIGH |
| F83 | Schema drift between world and forecasts | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | MED |
| F84 | Backfill scripts call daily_observation_writer | post_pr126_audit/RUN_14_track_B | pending | STRUCTURAL (K1 fallout) | MED |
| F85 | Daemon stdout/stderr inversion | post_pr126_audit/RUN_16_track_A | pending | STRUCTURAL (Observability) | HIGH |
| F86 | SIGTERM exit -15 without audit trail | post_pr126_audit/RUN_16_track_A | pending | TRIVIAL_BATCH (Logging) | HIGH |
| F87 | `forecast-live` exit code 1 (FAILED) | post_pr126_audit/RUN_16_track_A | pending | STRUCTURAL (Observability) | HIGH |
| F88 | `calibration-transfer-eval` daemon last run OK | post_pr126_audit/RUN_16_track_A | completed | RETRACT (Steady state) | HIGH |
| F89 | `heartbeat-sensor` not in launchctl | post_pr126_audit/RUN_16_track_A | pending | TRIVIAL_BATCH (Convention) | HIGH |
| F90 | `cron/jobs.json` vs crontab drift | post_pr126_audit/RUN_15_track_1 | pending | STRUCTURAL (Scheduler) | HIGH |
| F91 | Heartbeat JSONs alert path unverified | post_pr126_audit/RUN_15_track_3 | pending | STRUCTURAL (Alerting) | MED |
| F92 | riskguard `auth/api-key` 400 | post_pr126_audit/RUN_15_track_3 | pending | TRIVIAL_BATCH (Metrics) | HIGH |
| F99 | Heartbeat write/read asymmetry | post_pr126_audit/RUN_15_track_3 | pending | STRUCTURAL (Observability) | HIGH |
| F100 | `daemon-heartbeat-ingest.json` has zero readers | post_pr126_audit/RUN_15_track_3 | pending | STRUCTURAL (Observability) | HIGH |
| F101 | Schema drift across heartbeat payloads | post_pr126_audit/RUN_15_track_3 | pending | TRIVIAL_BATCH (Schema) | HIGH |
| F102 | `temp_persistence` table empty/missing | post_pr126_audit/RUN_16_track_B | pending | STRUCTURAL (Data provenance) | HIGH |
| F103 | Run #14 Track B F48 fix insufficient | post_pr126_audit/RUN_16_track_B | pending | STRUCTURAL (K1 fallout) | HIGH |
| F104 | `PERSISTENCE_CHECK_DISABLED` never observed | post_pr126_audit/RUN_16_track_B | pending | TRIVIAL_BATCH (Logging) | MED |
| F105 | `EXIT_ORDER_REJECTED` false phase log | post_pr126_audit/RUN_16_track_F | pending | TRIVIAL_BATCH (Logging) | HIGH |
| F106 | Silent `active → pending_exit` transition | post_pr126_audit/RUN_16_track_F | pending | STRUCTURAL (Lifecycle) | HIGH |
| F107 | `occurred_at` carries `"unknown_entered_at"` | post_pr126_audit/RUN_16_track_F | pending | TRIVIAL_BATCH (Data hygiene) | HIGH |
| F108 | London positions STUCK in `pending_exit` | post_pr126_audit/RUN_16_track_F | pending | STRUCTURAL (Lifecycle) | HIGH |
| F109 | `event_type` lacks transition record | post_pr126_audit/RUN_16_track_F | pending | TRIVIAL_BATCH (Lifecycle) | HIGH |
| F110 | No mapping between current.phase and lots.state | post_pr126_audit/RUN_16_track_F | pending | STRUCTURAL (Lifecycle) | HIGH |

## Triage Summary
- **RETRACT**: 1 (F88)
- **DEFER**: 3 (F14, F16, F39)
- **TRIVIAL_BATCH**: 10 (Registry, Scheduler, Logging, Metrics, Hygiene clusters)
- **STRUCTURAL**: 22 (K1 fallout: 11, Observability/Heartbeat: 6, Lifecycle: 5)
- **IN_FLIGHT**: 2 (F20, F43)

## K-Axes
1. **K1 Split Reader Regressions**: A significant number of pending findings (F46-F84) stem from reader-side code still pointing to `zeus-world.db` or legacy tables after the K1 split.
2. **Observability & Heartbeat Asymmetry**: A critical cluster (F85, F87, F99-F101) where the system's "immune system" is reporting on dead/silent channels or not reporting at all.
3. **Position Lifecycle Correctness**: New SEV-1/2 findings (F106-F110) regarding stuck states and missing transitions in the trade lifecycle.
