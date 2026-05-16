# Current Data State

Status: active current-fact surface — partially stale; K1 split row (point 1) superseded
Last audited: 2026-04-28 (HIGH `physical_quantity` migration + LOW settlements backfill); K1 split addendum 2026-05-16
Max staleness: 14 days for data/backfill/schema planning
Evidence packets:
  - `docs/operations/task_2026-04-23_data_readiness_remediation/` (HIGH baseline, P-E reconstruction)
  - `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/` (code-only follow-up, flag OFF)
  - `docs/operations/task_2026-04-28_settlements_physical_quantity_migration/` (HIGH `physical_quantity` canonical-string migration; APPLIED)
  - `docs/operations/task_2026-04-28_settlements_low_backfill/` (LOW settlements bootstrap; APPLIED)
Authority status: not authority law; audit-bound planning fact only. The 2026-04-28 merge preserves the branch's APPLIED evidence claims as packet evidence; it does not itself authorize a new live/prod DB mutation.
If stale, do not use for: live data-readiness, backfill readiness, v2 cutover,
or ingest-health claims
Refresh trigger: new data/schema audit, DB role change, v2 posture change,
ingest-freshness change, or age > max staleness for planning

## Purpose

Use this file only for the compact current answer to data posture. For durable
law, read `architecture/data_rebuild_topology.yaml`,
`architecture/invariants.yaml`, and
`docs/authority/zeus_current_architecture.md`.

## Current Conclusions (post-2026-04-23 workstream; K1 split addendum 2026-05-16)

**K1 DB split addendum (2026-05-11 / PR #114):** Zeus now operates two canonical
SQLite databases. `WORLD_CLASS` tables (markets, positions, lifecycle) live in
`state/zeus-world.db`; `FORECAST_CLASS` tables (observations, settlements,
calibration_pairs_v2, ensemble_snapshots_v2, source_run, market_events_v2) live in
`state/zeus-forecasts.db`. Canonical ownership is machine-checked by
`architecture/db_table_ownership.yaml` (loader: `src/state/table_registry.py`).
The sanctioned cross-DB write path is `get_forecasts_connection_with_world()`
(ATTACH+SAVEPOINT, enforced by INV-37). Points 1-3 below reflect pre-K1 state
and are superseded by this addendum for routing and schema queries.

1. `state/zeus-world.db` is the authoritative data DB for observations,
   forecasts, calibration, snapshots, and settlements.
   **SUPERSEDED by K1 split**: FORECAST_CLASS tables now live in `zeus-forecasts.db`.
   `zeus-world.db` retains WORLD_CLASS (markets/positions/lifecycle).
2. `state/zeus_trades.db` is trades-focused DB truth.
3. `state/zeus.db` is legacy and not the current canonical data store.
4. **`settlements` is canonical-authority-grade as of 2026-04-28**: 1,609 rows total. INV-14 identity spine intact on every row (`temperature_metric`, `physical_quantity`, `observation_field`, `data_version`) + full `provenance_json`. Schema carries `settlements_authority_monotonic` + `settlements_non_null_metric` + `settlements_verified_insert/update_integrity` triggers.

   **HIGH track** (1,561 rows, writer `p_e_reconstruction_2026-04-23`, post-2026-04-28 migration):
   - 1,469 VERIFIED + 92 QUARANTINED
   - All rows now carry canonical `physical_quantity = "mx2t6_local_calendar_day_max"` (was legacy literal `"daily_maximum_air_temperature"` pre-2026-04-28; migrated by `task_2026-04-28_settlements_physical_quantity_migration` with snapshot at `state/zeus-world.db.pre-physqty-migration-2026-04-28`)
   - Closure summary: `docs/operations/task_2026-04-23_data_readiness_remediation/CLOSURE_SUMMARY.md`

   **LOW track** (48 rows, writer `p_e_reconstruction_low_2026-04-28`):
   - 4 VERIFIED + 44 QUARANTINED
   - Coverage: 8 cities (London/Seoul/NYC/Tokyo/Shanghai/Paris/Miami/Hong Kong), 2026-04-15..2026-04-27
   - All rows carry canonical `physical_quantity = "mn2t6_local_calendar_day_min"`
   - **STRUCTURAL LIMIT**: Polymarket did NOT offer LOW markets before 2026-04-15 (verified gamma-api 2026-04-28). LOW row count is upstream-limited, not a backfill miss. See `architecture/fatal_misreads.yaml::polymarket_low_market_history_starts_2026_04_15` and `docs/operations/task_2026-04-28_settlements_low_backfill/plan.md`.
   - Snapshot: `state/zeus-world.db.pre-low-backfill-2026-04-28`
5. **`observations` still carries the settlement-driving data**: 51 cities of
   `wu_icao_history` + `hko_daily_api` + `ogimet_metar_*` rows are the source
   of truth that P-E used to re-derive `settlements.settlement_value` via
   `SettlementSemantics.assert_settlement_value()` gate.
6. **Source-family routing per P-C is live in settlements provenance**:
   - WU cities use `wu_icao_history` obs + `wmo_half_up` rounding
   - NOAA cities (Istanbul / Moscow / Tel Aviv NOAA-bound rows) use
     `ogimet_metar_*` obs + `wmo_half_up` rounding
   - Hong Kong HKO rows use `hko_daily_api` obs + `oracle_truncate` rounding
   - Taipei CWA 7 rows have no accepted proxy collector — QUARANTINED with
     `pc_audit_station_remap_needed_no_cwa_collector` reason
7. **8 enumerable QUARANTINE reasons** cover the 92 non-VERIFIED rows:
   source-role-collapse (27, ex-AP-4), Shenzhen drift (26, whole-bucket),
   HKO no-obs for specific dates (15), DST-spring-forward (7, 2026-03-08
   cluster), CWA-no-collector (7), Seoul drift (5), pe_obs_outside_bin (3),
   1-unit drift (2: KL + Cape Town). Enumerated in
   `docs/operations/task_2026-04-23_data_readiness_remediation/first_principles.md`.
8. **v2 tables** (observations_v2, forecasts_v2, calibration_pairs_v2, etc.)
   remain structurally present; still not the canonical path for the data
   that settled through P-E (which wrote to the canonical `settlements` table
   in `zeus-world.db`).
9. **Harvester live-write path** is still DORMANT by default: DR-33-A landed
   the canonical-authority code behind `ZEUS_HARVESTER_LIVE_ENABLED=1`
   feature flag (default OFF). Current runtime produces 0 harvester writes
   per cycle. Flipping the flag requires separate DR-33-C review.
10. Daily and hourly ingest lag may still be non-zero; consult
   `docs/operations/current_source_validity.md` for per-source freshness
   claims and `docs/to-do-list/known_gaps.md` for known ingest issues.
11. Hong Kong source status remains an explicit caution path; read
    `docs/operations/current_source_validity.md` and
    `architecture/fatal_misreads.yaml::hong_kong_hko_explicit_caution_path`.

## Invalidation Conditions

Re-audit before relying on this file if:

- any v2 table becomes populated or promoted to canonical
- a new writer/cutover lands on `settlements` beyond the three currently-registered writers (`p_e_reconstruction_2026-04-23`, `p_e_reconstruction_low_2026-04-28`, `harvester_live_dr33`)
- `ZEUS_HARVESTER_LIVE_ENABLED` is flipped to `1`
- ingest freshness materially changes
- DB role ownership changes
- any subsequent mutation changes the 1,609-row baseline (1,561 HIGH + 48 LOW; 1,473 VERIFIED total)
- a fresh gamma-api.polymarket.com probe shows LOW markets predating 2026-04-15 OR coverage beyond the 8-city set (which would invalidate the structural-limit caution)
- the file is older than Max staleness and the task needs present-tense data truth

## Stale Behavior

If stale, this file may be used only as historical planning context. It must
not justify runtime behavior, backfill execution, data readiness, or source
truth. Record `needs fresh audit` and stop before implementation that depends
on current data posture.
