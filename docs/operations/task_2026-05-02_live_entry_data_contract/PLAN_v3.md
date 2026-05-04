# Plan V3: Live Entry Data Contract
> Created: 2026-05-02 | Status: FULL IMPLEMENTATION CANDIDATE / PENDING CRITIC

## Goal
Make live opening-hunt entries possible only when ECMWF forecast data has executable source authority, fresh row coverage, source-run provenance, readiness, calibration provenance, and decision persistence that all agree.

## Architecture Decision
ECMWF Open Data ENS and TIGGE MARS ECMWF ENS are the same forecast authority family and physical signal, delivered through different SLAs:

- Same model family: ECMWF IFS ENS.
- Same ensemble shape: 51 members.
- Same live-relevant parameters: `mx2t6` and `mn2t6` local-calendar-day extrema.
- Same canonical metric identity after extraction.
- Different delivery: Open Data is same-day rolling feed; TIGGE MARS is T-2 archive/backfill.

Therefore Open Data can be the live inference source, while TIGGE remains the durable calibration/training archive. They must keep separate `source_id` and `data_version` values, but they may share a named calibration transfer policy after evidence.

## Concrete Decisions
1. Use explicit columns on `ensemble_snapshots_v2`, not a separate link table.
   - Add `source_id TEXT`, `source_transport TEXT`, `source_run_id TEXT`, and `release_calendar_key TEXT`.
   - Add indexes for `(source_id, source_run_id)` and `(city, target_date, temperature_metric, source_id, data_version)`.
   - Existing rows without these fields are shadow/legacy only for live entry until explicitly backfilled or linked.
2. Add a transport-aware executable source gate.
   - `ecmwf_open_data` is eligible for `entry_primary` only with `source_transport = ensemble_snapshots_v2_db_reader`.
   - `openmeteo_ensemble_ecmwf_ifs025` remains monitor/diagnostic only.
   - direct `fetch_ensemble(model="ecmwf_open_data", role="entry_primary")` remains blocked unless it is impossible to bypass the DB reader.
3. Add explicit entry source config, separate from `ensemble.primary`.
   - `entry_forecast.source_id = ecmwf_open_data`.
   - `entry_forecast.transport = ensemble_snapshots_v2_db_reader`.
   - `entry_forecast.rollout_mode = blocked | shadow | canary | live`.
4. Use named calibration transfer, not silent reuse.
   - Policy id: `ecmwf_open_data_uses_tigge_localday_cal_v1`.
   - Default mode: `SHADOW_ONLY` until equivalence evidence, tests, and operator live-money approval promote it.
   - Decision evidence must carry `forecast_source_id`, `forecast_data_version`, `calibration_source_id`, `calibration_data_version`, `calibration_input_space` or `calibrator_model_key`, `calibration_policy_id`, and `calibration_mode`.
5. Readiness is two-layered.
   - Producer readiness: city/date/metric/source/run row coverage.
   - Entry readiness: evaluator-composed market/strategy/condition readiness that depends on producer readiness, market topology, calibration policy, and rollout gate.
6. Decision persistence must preserve input provenance.
   - Open Data decisions must store Open Data source/data-version/input snapshot/source-run/readiness/calibration fields.
   - Open Data evidence must never be written as TIGGE `data_version`.
7. Live canary requires explicit operator live-money-deploy-go evidence plus G1/live-readiness evidence. Passing tests or healthcheck is not authorization.
8. Implement in a fresh worktree/branch from explicit base, not the PR46 healthcheck branch.

## Phase 0: Fresh Worktree And Contract Tests
- Create a fresh worktree from `main` or operator-selected base.
- Add failing relationship tests before production code.
- Add config contract tests for explicit `entry_forecast` keys.
- Add tests that direct Open-Meteo/Open Data API entry paths remain blocked.
- Add tests that rows without explicit source/source_run linkage are not live-executable.
- Add tests that calibration transfer fields are required before sizing.
- Add tests that all new live modes default to blocked/shadow.

Go gate: tests fail on current code for expected reasons; no live behavior changed.

## Phase 1: Schema And Source Contract
Files likely involved: `src/state/schema/v2_schema.py`, `src/data/forecast_source_registry.py`, new source contract module, config/tests.

Work:
- Add explicit columns and indexes to `ensemble_snapshots_v2`.
- Add a source/transport/role gate such as `gate_executable_forecast_source(source_id, transport, role)`.
- Add source contract constants for `ecmwf_open_data`, `ensemble_snapshots_v2_db_reader`, and policy modes.
- Add migration/backfill policy: old rows can remain for archive/training, but live entry requires explicit linkage fields.

Go gate: schema and gate tests pass; old direct fetch path remains blocked for entry.

## Phase 2: Open Data Producer Truth
Files likely involved: `src/data/ecmwf_open_data.py`, `src/ingest_main.py`, `src/data/release_calendar.py`, `src/state/source_run_repo.py`, `src/state/readiness_repo.py`, Open Data tests.

Work:
- Replace `_default_cycle()` with release-calendar safe-run selection.
- Collapse Open Data scheduler timing to one authority, using release calendar/config instead of duplicate hardcoding.
- Make extraction durable for large `mx2t6/mn2t6` GRIBs: resumable/chunked or long-worker status with locks and truthful progress.
- Make startup catch-up aggregate high/low child failures and return FAILED when either fails.
- Write source-run rows for attempted, failed, partial, skipped-not-released, and successful runs.
- Insert Open Data snapshot rows with `source_id=ecmwf_open_data`, `source_transport=ensemble_snapshots_v2_db_reader`, `source_run_id`, and `release_calendar_key`.
- Write producer readiness only after matching v2 high/low rows exist for required city/date/metric scope.
- Use existing 2026-05-02 12Z Open Data GRIBs as a shadow extraction/ingest probe.

Go gate: current forward target dates either have complete Open Data high/low rows with successful source-run provenance or visible blocked readiness with exact reason.

## Phase 3: Executable Snapshot Reader
Files likely involved: new `src/data/executable_forecast_reader.py`, `src/data/tigge_client.py` adapter extraction, reader tests.

Work:
- Read by city, target local date, metric, source, data version, source transport, and source run.
- Require successful source-run and non-expired producer readiness.
- Validate authority, causality, member count, unit, timing order, freshness, release calendar, and local-day scope.
- Return evaluator-compatible data and evidence: members/times or extrema, `forecast_source_id`, `forecast_data_version`, `source_transport`, `input_snapshot_ids`, `source_run_id`, `readiness_id`, `raw_payload_hash`, `captured_at`.
- If compatibility needs synthetic hourly columns, build them from the city local target day converted to UTC; do not use UTC-midnight fake rows.
- Keep TIGGE archive fallback shadow-only for same-day entry unless separate live-causal proof is added later.

Go gate: shadow reader produces p_raw-compatible evidence and refuses unlinked/stale/partial rows.

## Phase 4: Calibration Transfer Policy
Files likely involved: calibration manager/store, evidence docs, evaluator evidence contracts, tests.

Work:
- Write evidence artifact for `ecmwf_open_data_uses_tigge_localday_cal_v1`: same IFS ENS family, 51 members, `mx2t6/mn2t6`, extraction algorithm, grid, and metric identity; different delivery SLA/source identity.
- Encode policy as `SHADOW_ONLY` by default.
- Allow `LIVE_ELIGIBLE` only with evidence and operator approval.
- Persist calibration provenance in decision/evidence records.
- Fail closed or shadow-only when forecast/calibration source/data-version/policy fields are missing or mismatched.

Go gate: Open Data can run through calibration in shadow; live sizing remains blocked until policy is live-eligible and operator-approved.

## Phase 5: Status, Readiness, And Health
Files likely involved: `src/data/ingest_status_writer.py`, `scripts/healthcheck.py`, `src/observability/status_summary.py`, live-readiness scripts if present.

Work:
- Report v2 coverage by source/data-version/metric/target date.
- Report source-run and producer readiness separately from upstream reachability.
- Compose entry readiness from producer readiness, market topology, calibration policy, rollout mode, and source gate.
- Add closed blocker enums/output fields before healthcheck emits them.
- Flag all-candidate source-policy/coverage rejection as live-alpha blocked while daemon/RiskGuard remain green.
- Legacy `ensemble_snapshots` counts are never executable coverage.

Go gate: health/status correctly blocks zero-row, failed source-run, expired readiness, calibration shadow-only, and all-candidate source-policy cases.

## Phase 6: Evaluator And Monitor Shadow Wiring
Files likely involved: `src/engine/evaluator.py`, `src/engine/monitor_refresh.py`, `src/config.py`, persistence/evidence tests.

Work:
- Replace entry-primary forecast fetch with executable snapshot reader behind rollout gate.
- Preserve diagnostic/crosscheck fallback separately.
- Attach forecast, readiness, source-run, and calibration evidence before sizing.
- Persist Open Data provenance without TIGGE data-version substitution.
- Align monitor refresh to the same reader for authoritative refresh, or label fallback as monitor-only degraded.

Go gate: evaluator reaches edge computation in shadow with Open Data evidence, places no orders, and has no live-entry direct-fetch path.

## Phase 7: Limited Live Canary
Only after all prior gates pass:

- Require operator live-money-deploy-go evidence and G1/live-readiness evidence.
- Enable tiny allowlist or tiny cap.
- Keep health/status readiness blockers active.
- Roll back to blocked mode on provenance mismatch, source-run failure, missing readiness, calibration mismatch, or unexpected direct fetch.

## Tests / Antibodies
- Open-Meteo ECMWF cannot be entry-primary.
- `fetch_ensemble(model="ecmwf_open_data", role="entry_primary")` cannot bypass DB reader.
- Open Data v2 row without `source_run_id` blocks entry.
- Open Data v2 row with failed/partial source-run blocks entry.
- Open Data v2 row with expired/missing readiness blocks entry.
- Reader refuses source/data-version/source-run mismatches.
- Local-day adapter passes DST and non-DST cities.
- Calibration transfer fields persist end to end.
- Open Data decision never writes TIGGE `data_version`.
- source health green plus zero executable v2 rows blocks health/readiness.
- TIGGE archive rows cannot authorize same-day live entry.
- Startup catch-up child failure writes failed source-run/job status.

## Do Not Do
- Do not authorize Open-Meteo ECMWF for `entry_primary`.
- Do not use TIGGE MARS archive to unblock same-day live orders.
- Do not add static `entry_primary` to `ecmwf_open_data` without transport gating.
- Do not treat source health, daemon health, or RiskGuard green as entry readiness.
- Do not implement in the dirty PR46 worktree.
