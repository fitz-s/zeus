# Current Data State

Status: active current-fact surface
Last audited: 2026-04-23 (post-data-readiness-workstream closure)
Max staleness: 14 days for data/backfill/schema planning
Evidence packet: `docs/operations/task_2026-04-23_data_readiness_remediation/first_principles.md`
  + `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md` (full trail)
  + `docs/operations/task_2026-04-23_live_harvester_enablement_dr33/` (code-only follow-up, flag OFF)
Authority status: not authority law; audit-bound planning fact only
If stale, do not use for: live data-readiness, backfill readiness, v2 cutover,
or ingest-health claims
Refresh trigger: new data/schema audit, DB role change, v2 posture change,
ingest-freshness change, or age > max staleness for planning

## Purpose

Use this file only for the compact current answer to data posture. For durable
law, read `architecture/data_rebuild_topology.yaml`,
`architecture/invariants.yaml`, and
`docs/authority/zeus_current_architecture.md`.

## Current Conclusions (post-2026-04-23 workstream)

1. `state/zeus-world.db` is the authoritative data DB for observations,
   forecasts, calibration, snapshots, and settlements.
2. `state/zeus_trades.db` is trades-focused DB truth.
3. `state/zeus.db` is legacy and not the current canonical data store.
4. **`settlements` is canonical-authority-grade as of 2026-04-23**: 1,561 rows
   (1,469 VERIFIED + 92 QUARANTINED), every row carrying INV-14 identity
   spine (`temperature_metric`, `physical_quantity`, `observation_field`,
   `data_version`) + full `provenance_json` with `decision_time_snapshot_id`
   referencing `observations.fetched_at`. Schema carries the
   `settlements_authority_monotonic` trigger (P-B). Writer signature on
   every row: `p_e_reconstruction_2026-04-23`. See the closure summary at
   `docs/operations/task_2026-04-23_data_readiness_remediation/CLOSURE_SUMMARY.md`.
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
    claims and `docs/operations/known_gaps.md` for known ingest issues.
11. Hong Kong source status remains an explicit caution path; read
    `docs/operations/current_source_validity.md` and
    `architecture/fatal_misreads.yaml::hong_kong_hko_explicit_caution_path`.

## Invalidation Conditions

Re-audit before relying on this file if:

- any v2 table becomes populated or promoted to canonical
- a new writer/cutover lands on `settlements` beyond the two currently-registered
  writers (`p_e_reconstruction_2026-04-23`, `harvester_live_dr33`)
- `ZEUS_HARVESTER_LIVE_ENABLED` is flipped to `1`
- ingest freshness materially changes
- DB role ownership changes
- any subsequent mutation changes the 1,561-row / 1,469-VERIFIED baseline
- the file is older than Max staleness and the task needs present-tense data
  truth

## Stale Behavior

If stale, this file may be used only as historical planning context. It must
not justify runtime behavior, backfill execution, data readiness, or source
truth. Record `needs fresh audit` and stop before implementation that depends
on current data posture.
