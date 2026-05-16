# Plan V2: Live Entry Data Contract
> Created: 2026-05-02 | Status: ARCHITECT-DERIVED / PENDING CRITIC

## Goal
Enable live opening-hunt entries only when the forecast source, transport, row coverage, source-run provenance, readiness, calibration policy, and decision evidence all agree.

## Supersedes
This supersedes `PLAN.md`, which was rejected because it revised a good diagnosis without closing the decisive authority contracts.

## Architecture Diagnosis
The current live system has five different truths:

- `config/settings.json` says `ensemble.primary = ecmwf_ifs025`.
- `forecast_source_registry.py` maps `ecmwf_ifs025` to Open-Meteo ensemble, correctly blocked for `entry_primary`.
- `ingest_main.py` treats ECMWF Open Data as same-day live source and TIGGE MARS as T-2 archive/backfill.
- `ensemble_snapshots_v2` currently has zero `ecmwf_opendata_%` rows, so the intended live source is not producing executable coverage.
- healthcheck/source health can stay green while executable entry coverage is zero.

The fix is not to toggle a registry role. The fix is to introduce an explicit executable forecast contract that links source identity, transport, row provenance, readiness, calibration, and evaluator evidence.

## Non-Negotiable Decisions
1. Open-Meteo ensemble remains non-entry fallback.
2. TIGGE MARS T-2 archive remains non-live for same-day entry unless separate live-causal proof exists.
3. ECMWF Open Data is entry-capable only through verified `ensemble_snapshots_v2` rows and source-run/readiness proof.
4. `ensemble.primary` must not choose money-lane authority. Add an explicit entry forecast source/config seam.
5. Static registry role checks are insufficient. Entry authorization must be transport-aware: `source_id + transport + role`.
6. Calibration policy and calibration provenance must be decided before any Open Data order sizing.
7. Decision persistence must preserve Open Data source/data-version/input snapshot provenance and never write it as TIGGE.
8. Implementation must happen in a fresh worktree/branch from an explicit base, not the PR46 healthcheck branch.

## Phase 0: Isolation And Authority Contracts
- Create a fresh worktree/branch from `main` or another operator-approved base.
- Add a default-blocked rollout gate for the new entry source.
- Define config keys for `entry_forecast.source_id`, `entry_forecast.transport`, and rollout mode.
- Define the source contract fields: `source_id`, `transport`, `role`, `origin_mode`, `data_version`, `release_calendar_key`, `source_run_id`, `readiness_id`, `calibration_policy_id`.
- Choose and document the snapshot linkage design before producer/reader implementation:
  - preferred: add indexed `source_id`, `source_run_id`, and optional `readiness_id` columns to `ensemble_snapshots_v2`; or
  - alternative: create an indexed snapshot-source-run/readiness link table.
- The reader must not authorize executable rows from data-version prefix or `provenance_json` alone.
- Decide calibration mode:
  - `SHADOW_ONLY` until Open Data-specific calibration is promoted; or
  - named transition policy, e.g. `ecmwf_open_data_uses_tigge_localday_cal_v1`, with evidence.
- Decision evidence and persistence must include `forecast_source_id`, `forecast_data_version`, `calibration_source_id`, `calibration_data_version`, `calibration_input_space` or `calibrator_model_key`, `calibration_policy_id`, and `calibration_mode`.
- Missing or mismatched calibration provenance fails closed or marks the decision `SHADOW_ONLY`; it must not size live orders.
- Define readiness scope before implementation: producer city/date/metric readiness plus evaluator-composed market readiness, or fully market-scoped readiness with a named writer.

Go gate: tests prove no Open Data path can submit live orders yet.

## Phase 1: Relationship Contracts And Antibodies
Write failing relationship tests before implementation.

Required tests:
- Open-Meteo ECMWF remains blocked for `entry_primary`.
- `fetch_ensemble(model="ecmwf_open_data", role="entry_primary")` is blocked unless routed through the verified DB-reader transport.
- `source_health` green plus zero `ecmwf_opendata_%` v2 rows blocks entry readiness.
- TIGGE archive rows do not authorize same-day live entry.
- Missing/expired readiness blocks entry.
- Calibration source/data-version mismatch blocks live sizing or marks decision shadow-only.
- Open Data decision evidence is never persisted with TIGGE source/data-version.
- Open Data row with complete v2 data but missing/failed `source_run_id` blocks entry.
- Open Data row with missing/expired `readiness_id` blocks entry.
- Reader refuses source/source-run/data-version mismatches.
- Decision persistence preserves calibration provenance fields end to end.
- Startup catch-up with one failed Open Data child track writes failed source-run/job status.
- Local-day adapter preserves target local date across New York DST, London DST, Tokyo, Sydney, and UTC cities.

Go gate: relationship tests fail for the current code for the expected reasons.

## Phase 2: Repair Open Data Producer Truth
Files likely involved: `src/data/ecmwf_open_data.py`, `src/ingest_main.py`, `src/state/source_run_repo.py`, `src/state/readiness_repo.py`, `src/data/release_calendar.py`, `config/source_release_calendar.yaml`, Open Data tests.

Work:
- Replace `_default_cycle()` with release-calendar-based safe run selection. No impossible `18 + 7` logic.
- Collapse scheduler timing to one authority instead of hardcoded 07:30/07:35 plus `settings.discovery.ecmwf_open_data_times_utc`.
- Make extraction durable for ~500MB mx2t6/mn2t6 GRIBs: resumable/chunked, or long-worker status with locks and truthful progress.
- Make startup catch-up return aggregate failure if either high or low track fails.
- Write `source_run` rows for attempted, failed, partial, skipped-not-released, and successful runs.
- Write readiness only after matching high/low v2 snapshot rows exist for the relevant city/date/metric scope.
- Populate the chosen snapshot linkage columns/table as part of ingest; unlinked rows are shadow-only and cannot authorize entry.
- Use existing 2026-05-02 12Z raw GRIBs as a shadow backfill probe before evaluator work.

Go gate: current forward target dates have complete Open Data high/low v2 rows with source-run provenance, or readiness is visibly blocked with exact reason.

## Phase 3: Build Executable Snapshot Reader In Shadow
Files likely involved: new `src/data/executable_forecast_reader.py`, `src/data/tigge_client.py`, `src/data/tigge_db_fetcher.py`, snapshot/schema contracts.

Work:
- Read `ensemble_snapshots_v2` by city, target local date, metric, data version, source, and source run.
- Validate 51 members, unit, authority, causality, source-run status, readiness status, freshness, issue/available/fetch ordering, and local-day scope.
- Return evaluator-compatible evidence: `source_id`, `transport`, `forecast_source_role`, `degradation_level`, `authority_tier`, `forecast_data_version`, `input_snapshot_ids`, `source_run_id`, `readiness_id`, `captured_at`, `raw_payload_hash`.
- Verify the explicit snapshot linkage design. A matching data version or JSON provenance field is evidence only when linked to a successful source run/readiness record by contract.
- If using a synthetic hourly compatibility adapter, generate timestamps from the city local target day and convert to UTC. Do not reuse UTC-midnight fake rows.
- Keep an extrema-native evaluator refactor as later cleanup, not first live unblock.

Go gate: reader runs in shadow, produces p_raw-compatible evidence, and cannot authorize rows without source-run/readiness.

## Phase 4: Status And Readiness Surfaces
Files likely involved: `src/data/ingest_status_writer.py`, `scripts/healthcheck.py`, `src/observability/status_summary.py`, `scripts/live_readiness_check.py` if present.

Work:
- Report `ensemble_snapshots_v2` coverage by source/data-version/metric/target date.
- Report `source_run` status and entry-ready coverage separately from upstream reachability.
- Add closed blocker enums/contracts before adding new health fields.
- Healthcheck must flag all-candidate source-policy/coverage rejection as live-alpha blocked while daemon/RiskGuard remain green.
- Do not use legacy `ensemble_snapshots` counts as executable coverage.

Go gate: zero Open Data rows, failed/partial source runs, expired readiness, or all-candidate source-policy rejection are visible blockers.

## Phase 5: Evaluator And Monitor Shadow Wiring
Files likely involved: `src/engine/evaluator.py`, `src/engine/monitor_refresh.py`, `src/config.py`, evidence/persistence tests.

Work:
- Replace entry-primary forecast fetch with the executable snapshot reader behind a disabled-by-default rollout gate.
- Preserve diagnostic/crosscheck fallback separately.
- Attach calibration policy evidence before sizing.
- Update decision persistence so Open Data keeps `forecast_source_id`, `forecast_data_version`, `input_snapshot_ids`, `source_run_id`, `readiness_id`, `calibration_source_id`, `calibration_data_version`, `calibration_input_space` or `calibrator_model_key`, `calibration_policy_id`, and `calibration_mode`.
- Align monitor refresh: same executable reader for authoritative refresh, or explicitly monitor-only degraded fallback.

Go gate: evaluator reaches edge computation in shadow with Open Data evidence, places no orders, and no direct `fetch_ensemble(... role="entry_primary")` path remains for live entry.

## Phase 6: Limited Live Canary
Only after all prior gates pass:

- Enable rollout for a tiny allowlist or tiny cap.
- Keep health/status readiness blockers active.
- Require explicit operator live-money-deploy-go evidence plus G1/live-readiness evidence. Passing Phase 0-5 tests or health checks is not authorization by itself.
- Log each decision with forecast/calibration/readiness provenance.
- Roll back to blocked mode on any provenance mismatch, source-run failure, missing readiness, or calibration mismatch.

## What Not To Do
- Do not authorize Open-Meteo ECMWF for `entry_primary`.
- Do not use TIGGE MARS archive to unblock same-day live orders.
- Do not add static `entry_primary` to `ecmwf_open_data` without a DB-reader transport gate.
- Do not treat source health, daemon health, or RiskGuard green as entry readiness.
- Do not implement this in the dirty PR46 worktree.
