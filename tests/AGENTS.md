# tests AGENTS

Tests defend Zeus kernel law, runtime safety, and delivery guarantees. This
file is the human route into the test suite; the machine-readable test map is
`architecture/test_topology.yaml`.

Module book: `docs/reference/modules/tests.md`

## Machine Registry

Use `architecture/test_topology.yaml` for:

- law-gate membership
- test categories
- high-sensitivity skip accounting
- reverse-antibody status
- test-to-law routing

Use `python3 scripts/topology_doctor.py --tests --json` to check that active
`tests/test_*.py` files are classified.

## Local Registry

| Path | Purpose |
|------|---------|
| `__init__.py` | Package marker for pytest/import tooling |
| `contracts/` | Spec-owned validation manifests; see `tests/contracts/AGENTS.md` |
| `fakes/` | Test-only fake venue/runtime doubles; must not import credentials or perform live I/O |
| `integration/` | Cross-module integration antibodies such as R3 T1 fake/live adapter parity scenarios |

Top-level `test_*.py` files are registered below for topology compliance. The
authoritative machine registry is `architecture/test_topology.yaml`.

| Path | Purpose |
|------|---------|
| `conftest.py` | Shared pytest fixtures for R3 T1 fake venue parity tests (created 2026-04-27) |
| `conftest_connection_pair.py` | Test helper fake_connection_pair() for riskguard/fill_tracker monkeypatching (two-system independence; created 2026-04-30) |
| `test_attribution_drift.py` | Cross-module antibody: silent attribution drift detector per R3 §1 #2 ATTRIBUTION_DRIFT packet (created 2026-04-28) |
| `test_attribution_drift_weekly.py` | End-to-end runner antibody for attribution drift batch-3 weekly dispatch (created 2026-04-28) |
| `test_calibration_observation.py` | Cross-module antibody: Platt parameter drift monitoring per R3 §1 #2 CALIBRATION_HARDENING packet (created 2026-04-29) |
| `test_calibration_observation_weekly.py` | End-to-end runner antibody for calibration hardening batch-3 weekly dispatch (created 2026-04-29) |
| `test_calibration_weighting_laws.py` | Calibration weighting LAW safe-subset antibodies for per-city LOW opt-out, batch rebuild n_mc default, forbidden temp-delta weighting, per-bucket rebuild savepoints, and no per-city alpha tuning (created 2026-05-08) |
| `test_control_plane_dual_consumer.py` | Antibody #14: control_plane.json dual consumer — ingest_main.py must contain control_plane read pattern (two-system independence; created 2026-04-30) |
| `test_data_freshness_gate.py` | Antibody #6: freshness gate three-branch behavior — FRESH/STALE/ABSENT (two-system independence; created 2026-04-30) |
| `test_data_version_priority.py` | Antibody: opendata data_version preferred over TIGGE when both rows exist for same (city, target_date, metric) (created 2026-05-01) |
| `test_diurnal.py` | Day0 solar/DST context relationship tests, including malformed `solar_daily` rootpage degrade behavior (audited 2026-05-08) |
| `test_drift_detector_threshold.py` | Antibody #SC-6: drift detector threshold tests (two-system independence; created 2026-04-30) |
| `test_dual_run_lock_obeyed.py` | Antibody #11: dual-run file lock race test (two-system independence; created 2026-04-30) |
| `test_dynamic_sql_baseline.py` | Pytest wrapper for check_dynamic_sql.py; security antibody for f-string SQL interpolations (created 2026-05-01) |
| `test_edge_observation.py` | Cross-module antibody: alpha-decay tracker per strategy_key per R3 §1 #2 EDGE_OBSERVATION packet (created 2026-04-28) |
| `test_edge_observation_weekly.py` | End-to-end runner antibody for edge observation batch-3 weekly dispatch (created 2026-04-28) |
| `test_forecast_live_daemon.py` | Relationship antibody: forecast-live daemon owns only OpenData forecast jobs and shares the OpenData lock with legacy ingest during rollout (created 2026-05-14) |
| `test_harvester_split_independence.py` | Antibody #12: structural boundary between ingest-side settlement harvester and trading lane (created 2026-04-30) |
| `test_heartbeat_dual_coverage.py` | Antibody #15: heartbeat sensor must monitor BOTH daemon heartbeat files (two-system independence; created 2026-04-30) |
| `test_identity_column_defaults.py` | Pytest wrapper for check_identity_column_defaults.py; INV-14 identity-column DEFAULT antibody (created 2026-05-01) |
| `test_ingest_boot_time_semantics.py` | PR45a relationship contract: city-local time, DST, scope identity, source-health, and data-coverage substrate facts cannot collapse into live readiness (created 2026-05-02) |
| `test_ingest_provenance_contract.py` | Antibody #9: IngestionGuard provenance contract tests (two-system independence; created 2026-04-30) |
| `test_inv_prototype.py` | Prototype evidence tests for @enforced_by INV decorator experiment; advisory only (created 2026-04-28) |
| `test_invariant_citations.py` | Pytest wrapper for check_invariant_test_citations.py; two-ring invariant citation enforcement (created 2026-05-01) |
| `test_learning_loop_observation.py` | Cross-module antibody: settlement-corpus->calibration update pipeline per R3 §1 #2 LEARNING_LOOP packet (created 2026-04-29) |
| `test_learning_loop_observation_weekly.py` | End-to-end runner antibody for learning loop batch-3 weekly dispatch (created 2026-04-29) |
| `test_main_module_scope.py` | Antibody #8: Phase 3 module-scope enforcement for src.main (two-system independence; created 2026-04-30) |
| `test_no_raw_world_attach.py` | Antibody #13: no raw ATTACH DATABASE or get_trade_connection_with_world in trading-lane source modules (created 2026-04-30) |
| `test_no_synthetic_provenance_marker.py` | Relapse antibody: blocks re-introduction of synthetic provenance markers per critic findings #6A/E (created 2026-04-28) |
| `test_observations_k1_migration.py` | Antibody for Invariant C: observations schema migrated to K1 dual-atom shape (created 2026-05-01) |
| `test_opendata_mx2t6_not_2t.py` | Antibody for Invariant A: download_ecmwf_open_ens.py default param must be mx2t6+mn2t6, not 2t (created 2026-05-01) |
| `test_opendata_writes_v2_table.py` | Antibody for Invariant A: Open Data ENS rows land in ensemble_snapshots_v2 with canonical data_versions (created 2026-05-01) |
| `test_riskguard_cold_start.py` | Antibody: riskguard cold-start deadlock fix — empty outcome fact must not block startup (created 2026-05-01) |
| `test_readiness_state.py` | PR45a relationship contract: readiness invalidation, backfill shadow-only, topology blocking, quote exclusion, and settlement-capture shadow boundary (created 2026-05-02) |
| `test_scheduler_health_truthfulness.py` | Antibody for Invariant D: structural-failure job must produce status=FAILED in scheduler_jobs_health.json (created 2026-05-01) |
| `test_settlements_physical_quantity_invariant.py` | Antibody for INV-14 identity spine: settlement metric identity correctness and migration residuals (created 2026-04-28) |
| `test_source_health_probe.py` | Antibody #5: source health probe contract tests (two-system independence; created 2026-04-30) |
| `test_station_migration_probe.py` | Antibody for Invariant F: station-migration drift detection when Polymarket gamma URL differs from cities.json station (created 2026-05-01) |
| `test_tigge_daily_ingest.py` | Antibody: TIGGE retrieval inside ingest daemon — MARS-credential-missing auto-pause, idempotent re-run, control_plane pause (created 2026-05-01) |
| `test_tigge_schema_contract.py` | Antibody #16: TIGGE extractor<->ingester schema drift structural tests (created 2026-04-29) |
| `test_trading_isolation.py` | Antibody #3: trading-lane isolation — src.engine/execution/strategy/signal must not import ingest modules (created 2026-04-30) |
| `test_truth_authority_enum.py` | Antibody for INV-23: DEGRADED_PROJECTION must be a distinct TruthAuthority enum value; ultrareview P1-3 (created 2026-05-01) |
| `test_world_schema_ready_check.py` | Antibody for A-2: _startup_world_schema_ready_check() must exist in src/main.py (two-system independence; created 2026-05-01) |
| `test_world_writer_boundary.py` | Antibody #2: only allowlisted modules may contain world-DB write SQL (INSERT/UPDATE/DELETE) (created 2026-04-30) |
| `test_ws_poll_reaction.py` | Cross-module antibody: WS/poll reaction timing invariant per R3 §1 #2 WS_OR_POLL_TIGHTENING packet (created 2026-04-28) |
| `test_ws_poll_reaction_weekly.py` | End-to-end runner antibody for WS/poll tightening batch-3 weekly dispatch (created 2026-04-29) |

## Test Trust Policy

Tests are **untrusted by default**. Only 36/162 tests have lifecycle headers
and are trusted to run without prior audit. The machine-readable registry is:

`architecture/test_topology.yaml` → `test_trust_policy.trusted_tests`

### Trust classification

| Class | Criteria | Agent action |
|-------|----------|-------------|
| **trusted** | Has `# Created: YYYY-MM-DD` + `# Last reused/audited: YYYY-MM-DD` | May run directly |
| **reviewed_only** | Has Created + last_reviewed but `last_reused=never` | Audit required before running |
| **audit_required** | No lifecycle header | Audit required before running |

### Before running an untrusted test

1. Read the test source — verify it tests current code contracts, not deleted APIs
2. Check `architecture/test_topology.yaml` for category and skip status
3. If the test is valid, add lifecycle headers and register in `trusted_tests`
4. Only then run it

### When creating or reusing a test

Every test file must have these headers in the first 15 lines:
```python
# Created: YYYY-MM-DD
# Last reused/audited: YYYY-MM-DD
# Authority basis: <packet or task that created/validated this test>
```

## Core Rules

- Breaking an architecture/law test means the code or plan is wrong, not that
  the test is inconvenient.
- Canonical file/function naming and test freshness rules live in
  `architecture/naming_conventions.yaml`; do not redefine them here.
- Touched, newly created, or evidence-reused top-level `tests/test_*.py` files
  must satisfy the freshness header contract in `architecture/naming_conventions.yaml`.
- Old tests are not proof by age. Before relying on an old/unknown test file as
  evidence, inspect its current code, `architecture/test_topology.yaml`, skip
  status, and update `last_reviewed` / `last_reused` as appropriate.
- Do not delete or xfail high-sensitivity tests without a written sunset plan
  and packet evidence.
- Prefer relationship tests for cross-module work: prove what must remain true
  when one module's output flows into the next.
- Mark transitional/advisory tests explicitly; do not let them masquerade as
  active law.
- Historical doc claims are not active law unless backed by code, manifest, or
  a current authority surface.

## Common Routes

| Task | Start With |
|------|------------|
| Find tests for a law/invariant | `python3 scripts/topology_doctor.py --tests --json` |
| Find cross-module validation manifests | `tests/contracts/spec_validation_manifest.py` |
| Edit source behavior | digest task + `architecture/source_rationale.yaml` + targeted tests |
| Edit test topology | `architecture/test_topology.yaml` + `tests/test_topology_doctor.py` |
| Review old/stale tests | `architecture/test_topology.yaml` categories before deleting anything |
