# Scripts Index

Map of scripts/ (328 `.py` + 13 `.sh` in root; 8 subdirectories). The load-bearing
operational tools are surfaced first. For per-script safety metadata (class, lifecycle,
authority scope, DB targets, dry-run defaults) see `architecture/script_manifest.yaml`.

---

## Start Here

Daily-driver commands and the scripts you reach for when something is wrong.

| Script | What it does |
|--------|-------------|
| `topology_doctor.py` | Main governance gate: compiled agent-navigation graph, planning-lock, map-maintenance, closeout checks. Facade over the `topology_doctor_*.py` family |
| `zpkt.py` | Packet runtime CLI (`zpkt status`): replaces 5 doctor commands; pre-task discovery, scope tracking, packet closeout |
| `healthcheck.py` | Operator health summary: daemon liveness, launchd contracts, source truth, entry capability, settlement freshness. Exit 0/1/2 |
| `ops/health_probe.py` | Liveness-first health probe: daemon ps-alive, world-DB WAL size, APScheduler stalls, artifact freshness, city coverage. Run as first step on any resume |
| `deploy_live.py` | Safe live restart: refuses `launchctl kickstart` while the live checkout is uncommitted/unpushed; requires preflight gates |
| `preflight_restart_check.py` | Read-only flag posture check (JSON only, no DB/network): answers "which flag to flip next" |
| `zeus_status.py` | Full money-path heartbeat: daemons → events → blocks → surface → positions in one invocation |
| `zeus_blocks.py` | Enumerate current runtime entry blockers |
| `antibody_scan.py` | Macro system health: pipeline health, data integrity, settlement integrity, position sanity — four immune-system questions |
| `verify_e2e_money_path.py` | End-to-end money-path walker: download → order, every stage, real state |

---

## Topology Doctor Family

`topology_doctor.py` is the entry point; each module below is a focused checker.

`topology_doctor_artifact_checks.py`, `topology_doctor_cli.py`, `topology_doctor_closeout.py`,
`topology_doctor_code_review_graph.py`, `topology_doctor_context_pack.py`,
`topology_doctor_data_rebuild_checks.py`, `topology_doctor_digest.py`,
`topology_doctor_docs_checks.py`, `topology_doctor_freshness_checks.py`,
`topology_doctor_map_maintenance.py`, `topology_doctor_ownership_checks.py`,
`topology_doctor_policy_checks.py`, `topology_doctor_receipt_checks.py`,
`topology_doctor_reference_checks.py`, `topology_doctor_registry_checks.py`,
`topology_doctor_script_checks.py`, `topology_doctor_source_checks.py`,
`topology_doctor_test_checks.py`

---

## Subdirectories

### `ci/` — CI enforcement (17 scripts)

Repeatable gates that run in CI or as pre-commit hooks. All read-only (stdout only).

Key scripts: `check_topology_structural_blockers.py`, `assert_invariant_coverage.py`,
`check_db_table_delta.py`, `check_source_rationale_delta.py`, `semantic_diff_classifier.py`,
`check_context_pack_integrity.py`, `pr_monitor.py`

### `ingest/` — Data ingestion ticks (7 scripts)

Standalone ticks decoupled from the main scheduler:
`daily_obs_tick.py` (WU + HKO + Ogimet), `forecasts_daily_tick.py` (NWP 5 models × 7 leads),
`hourly_instants_tick.py`, `hole_scanner_tick.py`, `solar_daily_tick.py`

### `migrations/` — Schema migrations (19 scripts)

Named `202605_*` / `202606_*`. Applied via `__main__.py`. One-time schema operations with
dry-run protection. Do not re-run applied migrations.

### `ops/` — Runtime operations (2 scripts)

`health_probe.py` (see Start Here), `orderable_bias_pass_candidates.py`

### `calibration/` — Calibration diagnostics (1 script)

`q_lcb_coverage_audit.py` — q_lcb coverage audit

### `fusion/` — Fusion diagnostics (1 script)

`lead_profile_walkforward.py` — walk-forward lead profile analysis

### `replay/` — Replay tools (2 scripts)

`executable_q_lcb_replay.py`, `source_update_alpha_decay.py`

---

## Root Scripts by Class

### Live operations & deploy

`deploy_live.py`, `preflight_restart_check.py`, `arm_live_mode.sh`,
`check_live_restart_preflight.py`, `check_live_release_gate.py`,
`check_full_transport_ship_readiness.py`, `live_readiness_check.py`,
`live_smoke_test.py`, `verify_fill_e2e.py`, `verify_e2e_money_path.py`,
`restore_live_trading_launchagent.py`

Shell monitors: `live_health_monitor.sh`, `data_chain_monitor.sh`,
`cloud_tigge_autochain.sh`, `local_post_extract_chain.sh`

### Enforcement / contract gates

`check_*` (22 scripts): contract, invariant, module boundary, schema, identity, dynamic-SQL,
heartbeat, e2e canary, work-packet, and writer-signature checks.
`assert_invariant_coverage.py`, `assert_test_quality.py` (in `ci/`)
`semantic_linter.py`, `source_contract_lint.py`, `doc_citation_lint.py`, `antibody_scan.py`

### Diagnostics (read-only investigations)

`audit_*` (15 scripts): architecture alignment, city data readiness, error model,
market price semantics, observation instants, replay fidelity, settlement provenance,
time semantics, PnL, divergence, etc.

`verify_*` (8 scripts): pipeline liveness, truth surfaces, reality contracts, forecast bundle,
fill e2e, analytic CI coverage.

`measure_*` (5 scripts): fusion AIFS drop, WU/METAR divergence, WU obs latency, member correlation, arm gate.

`validate_*` (7 scripts): dynamic alpha, ENS refit OOS, grid representativeness fusion, assumptions, etc.

`zeus_status.py`, `zeus_blocks.py`, `state_census.py`, `equity_curve.py`, `attribution_drift_weekly.py`,
`edge_observation_weekly.py`, `obs_coverage_report.py`, `data_completeness_audit.py`

### ETL & backfill writers

`etl_*` (7 scripts): solar times, TIGGE calibration, historical forecasts, diurnal curves, etc.
`backfill_*` (30+ scripts): observations, ENS, HKO, OpenMeteo, WU, settlement outcomes, cluster taxonomy, etc.
`ingest/` subdirectory (see above)

Shell launchers: `resume_backfills_sequential.sh`, `post_sequential_fillback.sh`

### Fit / build (calibration artifacts)

`fit_*` (12 scripts): EMOS calibration, sigma scale/shape, grid representativeness, selection calibrator, city skill gate, etc.
`build_*` (9 scripts): ENS residual evidence, correlation matrix, replacement forecast artifacts, OOF q_lcb reliability table, etc.
`run_offline_calibration_rebuild.py`, `run_offline_platt_refit.py`, `run_platt_oos_scoring.py`

### Repair & migration (root-level)

`migrate_*` (17 scripts): schema-level one-time migrations (complement to `migrations/` subdir).
`repair_*` (2 scripts): dust exit projection, HKO runtime monitoring.
`reconcile_*` (2 scripts): realized fees, Wellington zombie.
`absorb_filled_unknown_edli_submit.py`, `resolve_edli_unknown_by_authenticated_absence.py`,
`resolve_unresolved_edli_submit.py`, `cleanup_ghost_positions.py`

### Operator record / runtime support

`zpkt.py`, `operator_record_redeem.py`, `operator_record_wrap.py`,
`apply_recommended_controls.py`, `force_lifecycle.py`, `force_cycle_with_healthy_gates.py`,
`nuke_rebuild_projections.py`, `restamp_readiness_to_cycle_bound.py`,
`maintenance_worker_install.py`, `install_hooks.sh`, `install_codegraph_hooks.sh`,
`expire_auto_pause.sh`

### Internal helpers (not for direct invocation)

`_build_pm_truth.py`, `_tigge_common.py`, `_yaml_bootstrap.py`, `_zpkt_scope.py`

---

## One-off / Historical

Scripts below are point-in-time investigations or dated tasks. They are retained for
reference but are **not repeatable tools**. Do not re-run without reading the header.

**`probe_*`** (5 scripts): `probe_emos_mu_correction_D4.py`, `probe_favorite_capture.py`,
`probe_full_live_path_to_submit.py`, `probe_lib.py`, `probe_model_cell_distance.py`

**Dated task scripts**: `task_2026-06-09_drop_dead_tables.py`,
`task_2026-06-09_restart_zeus_daemons.sh`, `run_platt_oos_49.sh`

**Single-use switches**: `apply_replacement_forecast_live_switch.py`,
`rollback_phase3_t3.py`, `init_replacement_forecast_live_schema.py`,
`backfill_london_f_to_c_2026_05_08.py`,
`backfill_settlement_outcomes_canonical_2026_06_02.py`,
`backfill_settlement_unit_2026_06_03.py`

**Experiment / bakeoff**: `experiment_route5_spread_scale.py`,
`experiment_route6_transport_beta.py`, `calibration_bakeoff.py`,
`baseline_experiment.py`, `cycle_phase_offline_study.py`,
`sigma_kernel_holdout_replay.py`, `oos_bias_crossfit.py`,
`oos_validation_harness.py`, `m4_bin_bias_before_after.py`,
`sigma_scale_before_after.py`, `center_warming_before_after.py`,
`score_platt_candidates.py`, `score_error_model_candidates.py`
