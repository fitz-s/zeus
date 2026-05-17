File: scripts/AGENTS.md
Disposition: NEW
Authority basis: architecture/script_manifest.yaml; architecture/zones.yaml; docs/authority/zeus_current_delivery.md.
Why this file exists now: scripts can overreach DB truth or persist one-off probes unless their lifecycle is explicit.

# scripts AGENTS

Scripts are enforcement, audit, runtime support, ETL, repair, or operator tools.
The machine registry is `architecture/script_manifest.yaml`.

Module book: `docs/reference/modules/scripts.md`

## Machine Registry

Use `architecture/script_manifest.yaml` for:

- script lifecycle: `long_lived`, `packet_ephemeral`, `promotion_candidate`, `deprecated_fail_closed`
- authority scope
- read/write targets
- dry-run/apply metadata
- target DB and danger classification
- reuse/disposal policy

Use `PYTHONPATH=. python -m scripts.topology_doctor --scripts --json` to check that top-level
scripts are registered and safe for their declared class.

## Class Quick Guide

- `enforcement`: repeatable topology, policy, lint, or contract gate.
- `diagnostic`: read-only investigation; stdout only; no live authorization.
- `diagnostic_report_writer`: repeatable non-authority report artifact writer.
- `runtime_support`: operator health/watch/resume/post-run support wrapper.
- `etl_writer`: repeatable external/world-data ingestion or backfill writer.
- `repair`: packet-approved repair with explicit dry-run/apply boundary.
- `config_writer`: repeatable config artifact generator.
- `stale_deprecated`: retained only to fail closed; `DO_NOT_RUN`.

## Core Rules

- Check the manifest before adding a top-level script; reuse or extend existing
  long-lived tools when possible.
- Canonical file/function naming and freshness rules live in
  `architecture/naming_conventions.yaml`; do not redefine them here.
- Touched, newly created, or evidence-reused scripts must satisfy the freshness
  header contract in `architecture/naming_conventions.yaml`.
- Old or unknown scripts must be inspected against this file and
  `architecture/script_manifest.yaml` before execution.
- One-off scripts need `task_YYYY-MM-DD_<purpose>.py` naming plus `delete_by`.
- Repair/ETL writers must declare write targets and dry-run/apply behavior.
- Diagnostics and reports must not write canonical DB truth.
- Scripts are not hidden authority centers.

## Local Registry

Only list durable entry points here; use the manifest for the full catalog.

| Script | Purpose |
|--------|---------|
| `topology_doctor.py` | Compiled topology/digest/health checks |
| `check_daemon_heartbeat.py` | Daemon heartbeat staleness check |
| `venus_sensing_report.py` | Venus sensing report, including daemon-independent source-contract watch |
| `backfill_tigge_snapshot_p_raw.py` | Replay-compatible TIGGE `p_raw_json` materialization |
| `watch_source_contract.py` | Polymarket settlement source-contract monitor, city quarantine writer, and conversion-history reporter |
| `source_contract_auto_convert.py` | Cron-safe source-contract transition controller, deterministic date-scope planner, apply/evidence runner, quarantine release gate, receipt writer, and Discord reporter |
| `arm_live_mode.sh` | Deprecated operator tool retained only for non-live execution cleanup checks; it must not be used to introduce runtime modes (created 2026-05-01) |
| `install_hooks.sh` | Installs git pre-commit and related hook symlinks for fail-closed enforcement (ultrareview25 P0-2; created 2026-05-01) |
| `check_dynamic_sql.py` | Enforcement scan for f-string SQL interpolations without whitelist; security review §10 antibody (created 2026-05-01) |
| `check_identity_column_defaults.py` | Enforcement check for identity-column DEFAULT violations per INV-14 and SYNTHESIS K-D (created 2026-05-01) |
| `check_invariant_test_citations.py` | Enforcement scan verifying all test files carry invariant citation headers per SYNTHESIS K-A two-ring rule (created 2026-05-01) |
| `migrate_observations_k1.py` | Repair script: migrates live state/zeus-world.db::observations from legacy single-atom to K1 dual-atom shape (dry-run/apply; created 2026-05-01) |
| `_rebuild_calibration_pairs_v2_parallel.py` | Compute-in-workers + write-in-main parallel orchestrator for rebuild_calibration_pairs_v2; imported lazily when --workers>1 (created 2026-05-11) |
| `archive_may_batch_2026-05-16.py` | One-off archival batch script for May 2026 packet cleanup (created 2026-05-16; delete_by: 2026-06-16) |
| `archive_migration_2026-05-16.py` | One-off archive migration script for 2026-05-16 packet reorganization (created 2026-05-16; delete_by: 2026-06-16) |
| `authority_inventory_v2.py` | Authority inventory v2 per task_2026-05-15_p9_authority_inventory_v2 SCAFFOLD; diagnostic report writer (created 2026-05-15) |
| `backfill_ecmwf_2026_05_04_to_09.py` | Backfill ECMWF observations 2026-05-04 to 2026-05-09; ETL writer (created 2026-05-09) |
| `backfill_ecmwf_v2_2026_05_06_to_09.py` | Backfill ECMWF v2 observations 2026-05-06 to 2026-05-09; ETL writer (created 2026-05-09) |
| `backfill_harvester_settlements.py` | Repair: backfill harvester settlement rows per PLAN.md §10, critic v4 ACCEPT 2026-05-11 (dry-run/apply; created 2026-05-11) |
| `backfill_hko_xml.py` | Backfill HKO XML observations; ETL writer (created 2026-05-02) |
| `backfill_settlements_via_gamma_2026.py` | Backfill settlements via Gamma API for 2026; ETL writer (created 2026-05-07) |
| `check_contract_source_fields.py` | Enforcement scan verifying source-contract fields on all contract entries (created 2026-05-01) |
| `check_pr_identity_collisions.py` | Enforcement scan for PR identity collisions; diagnostic (created 2026-05-04) |
| `check_schema_version.py` | Enforcement check: init_schema boot invariant per task_2026-05-11_init_schema_boot_invariant §5.6 (created 2026-05-11) |
| `check_table_registry_coherence.py` | Enforcement scan verifying table_registry coherence against live DB schema (created 2026-05-14) |
| `check_writer_signature_typing.py` | Enforcement scan for writer signature typing conformance (created 2026-05-14) |
| `cloud_tigge_autochain.sh` | Cloud-side TIGGE download autochain; pairs with local_post_extract_chain.sh (created 2026-05-08) |
| `data_chain_monitor.sh` | Shell monitor for data chain health; runtime support (created 2026-05-11) |
| `ddd_v1_v2_replay.py` | DDD v1→v2 replay diagnostic; replay correctness probe (created 2026-05-03) |
| `drop_world_ghost_tables.py` | Repair: drop ghost tables from zeus-world.db post K1 split per task_2026-05-14_k1_followups PLAN §2 P3 D2 (dry-run/apply; created 2026-05-14) |
| `expire_auto_pause.sh` | Operator tool: expire auto-pause state; sister script of arm_live_mode.sh, runs only step 3 (created 2026-05-01) |
| `force_cycle_with_healthy_gates.py` | Operator tool: force a cycle tick with all gates healthy; runtime support (created 2026-05-16) |
| `live_health_monitor.sh` | Polls live_health_probe.py every 60s; emits one line per state change; runtime support (created 2026-05-11) |
| `local_post_extract_chain.sh` | Local post-extract chain for TIGGE downloads; pairs with cloud_tigge_autochain.sh (created 2026-05-04) |
| `maintenance_worker_install.py` | Installer for maintenance_worker daemon and rules; config_writer (created 2026-05-15) |
| `migrate_ensemble_snapshots_v2_add_ingest_backend.py` | Migration: add ingest_backend column to ensemble_snapshots_v2 per TIGGE_DOWNLOAD_SPEC_v3 §3 Phase 0 #5 (dry-run/apply; created 2026-05-07) |
| `migrate_phase2_cycle_stratification.py` | Migration: Phase 2 cycle stratification per DESIGN_PHASE2_PLATT_CYCLE_STRATIFICATION (dry-run/apply; created 2026-05-14) |
| `migrate_world_observations_to_forecasts.py` | Migration: move world observation rows to zeus-forecasts.db post K1 split per task_2026-05-14_k1_followups PLAN §2 P0 (dry-run/apply; created 2026-05-14) |
| `migrate_world_to_forecasts.py` | Migration: K1 DB split world→forecasts DB transition per task_2026-05-11_forecast_db_split PLAN §5.4 (dry-run/apply; created 2026-05-11) |
| `operator_record_redeem.py` | Operator CLI: advance REDEEM_OPERATOR_REQUIRED rows; runtime support (created 2026-05-14) |
| `pre-commit-capability-gate.sh` | Pre-commit enforcement gate for capability declarations; runs on staged changes (created 2026-05-06) |
| `reevaluate_readiness_2026_05_07.py` | Repair: re-evaluate BLOCKED readiness rows post D1 bridge policy 2026-05-07 (dry-run/apply; created 2026-05-07) |
| `replay_correctness_gate.py` | Enforcement gate for replay correctness per IMPLEMENTATION_PLAN Phase 0.G + ADR-5 (created 2026-05-14) |
| `repro_antibodies.py` | Antibody reproduction diagnostic; verifies antibody tests catch their target defect (created 2026-05-03) |
| `ritual_signal_aggregate.py` | Aggregate ritual signal metrics per ANTI_DRIFT_CHARTER §3 M1; diagnostic_report_writer (created 2026-05-06) |
| `topology_route_shadow.py` | Topology route shadow probe; diagnostic for topology_v_next shadow comparison (created 2026-05-06) |
| `zeus_blocks.py` | Zeus block state diagnostic; reads and reports current block conditions (created 2026-05-04) |
| `doc_citation_lint.py` | Citation-rot detector for Zeus docs; scans .md/.py/.yaml/.json for broken doc references per SCAFFOLD §4 FM-01/FM-04 (created 2026-05-17) |
| `pr_monitor.py` | Canonical PR monitor; single source of filter logic for Monitor tool armed after gh pr create; 7 filter contracts pinned by tests/test_pr_monitor.py (created 2026-05-17) |
