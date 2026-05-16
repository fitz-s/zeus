# Cron and Daemon Inventory

## LaunchAgents: ~/Library/LaunchAgents/com.zeus.*.plist
| File Name | Status |
|---|---|
| com.zeus.calibration-transfer-eval.plist | Active |
| com.zeus.data-ingest.plist | Active |
| com.zeus.forecast-live.plist | Active |
| com.zeus.heartbeat-sensor.plist | Active |
| com.zeus.live-trading.plist | Active |
| com.zeus.riskguard-live.plist | Active |
| com.zeus.data-ingest.plist.bak-2026-05-15-forecast-live-split | Artifact |
| com.zeus.forecast-live.plist.bak-20260515014318 | Artifact |
| com.zeus.heartbeat-sensor.plist.replaced-2026-05-01.bak | Artifact |
| com.zeus.live-trading.plist.bak-2026-04-28-pre-wu-api-key | Artifact |
| com.zeus.live-trading.plist.before_proxy_cleanup_20260515T1838Z | Artifact |
| com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak | Artifact |

## Cron Jobs: ~/.openclaw/cron/jobs.json (First 30)
| Name | Cron Expression | Command / Purpose (Truncated) |
|---|---|---|
| finance-premarket-brief | 10 8 * * 1-5 | /opt/homebrew/bin/python3 ... finance_discord_report_job.py --mode marketday-review |
| google-workspace-chief-of-staff-morning-brief | 30 9 * * 1-5 | Google Workspace Chief-of-Staff v1 morning brief |
| finance-subagent-scanner-offhours | 0 0,4,7,17,20 * * * | /opt/homebrew/bin/python3 ... finance_scanner_job.py --mode offhours-scan |
| evolution-weekly-learning | 0 21 * * 1 | review_runner.py --mode weekly-learning |
| evolution-skills-audit | 0 21 1,15 * 1 | review_runner.py --mode skills-audit |
| (Note: truncated to top examples due to length) | | |

## Maintenance Scripts: scripts/*.py (Matched: schedule, cron, daily, maintenance)
- scripts/attribution_drift_weekly.py
- scripts/antibody_scan.py
- scripts/audit_time_semantics.py
- scripts/automation_analysis.py
- scripts/backfill_forecast_issue_time.py
- scripts/backfill_hko_daily.py
- scripts/backfill_hourly_openmeteo.py
- scripts/backfill_observations_from_settlements.py
- scripts/backfill_hko_xml.py
- scripts/backfill_openmeteo_previous_runs.py
- scripts/backfill_obs_v2.py
- scripts/backfill_solar_openmeteo.py
- scripts/baseline_experiment.py
- scripts/backfill_ogimet_metar.py
- scripts/build_correlation_matrix.py
- scripts/backfill_wu_daily_all.py
- scripts/bridge_oracle_to_calibration.py
- scripts/check_writer_signature_typing.py
- scripts/calibration_observation_weekly.py
- scripts/check_dynamic_sql.py
- scripts/etl_asos_wu_offset.py
- scripts/edge_observation_weekly.py
- scripts/etl_temp_persistence.py
- scripts/evaluate_calibration_transfer_oos.py
- scripts/heartbeat_dispatcher.py
- scripts/learning_loop_observation_weekly.py
- scripts/etl_diurnal_curves.py
- scripts/hko_ingest_tick.py
- scripts/etl_solar_times.py
- scripts/migrate_world_observations_to_forecasts.py
- scripts/migrate_observations_k1.py
- scripts/onboard_cities.py
- scripts/rebuild_calibration_pairs_canonical.py
- scripts/rebuild_settlements.py
- scripts/oracle_snapshot_listener.py
- scripts/topology_doctor_artifact_checks.py
- scripts/source_contract_auto_convert.py
- scripts/topology_doctor_map_maintenance.py
- scripts/topology_doctor_closeout.py
- scripts/topology_doctor_cli.py
- scripts/topology_doctor_digest.py
- scripts/topology_doctor_policy_checks.py
- scripts/topology_doctor_ownership_checks.py
- scripts/venus_autonomy_gate.py
- scripts/topology_doctor_code_review_graph.py
- scripts/verify_truth_surfaces.py
- scripts/topology_doctor_freshness_checks.py
- scripts/topology_doctor_receipt_checks.py
- scripts/topology_doctor.py
- scripts/watch_source_contract.py
- scripts/venus_sensing_report.py
- scripts/zpkt.py
- scripts/ws_poll_reaction_weekly.py

## Scheduled Task State
- .claude/scheduled_tasks.lock: Not found
- .claude/scheduled_tasks.json: Not found

## Operational Daemons Note
- **Docs Hygiene Daemon**: Not found.
- **Operations Archival Daemon**: Not found.
- No existing daemons identified for automated documentation maintenance or operations cleanup.
