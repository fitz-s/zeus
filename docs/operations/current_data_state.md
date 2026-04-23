# Current Data State

Status: active current-fact surface
Last audited: 2026-04-21
Max staleness: 14 days for data/backfill/schema planning
Evidence packet: `docs/operations/task_2026-04-21_gate_f_data_backfill/step1_schema_audit.md`
Receipt path: `docs/operations/task_2026-04-23_authority_kernel_gamechanger/receipt.json`
Authority status: not authority law; audit-bound planning fact only
If stale, do not use for: live data-readiness, backfill readiness, v2 cutover,
or ingest-health claims
Refresh trigger: new data/schema audit, DB role change, v2 posture change,
ingest-freshness change, or age > max staleness for planning

## Purpose

Use this file only for the compact current answer to data posture. For durable
law, read `architecture/data_rebuild_topology.yaml` and
`docs/authority/zeus_current_architecture.md`.

## Current Conclusions

1. `state/zeus-world.db` is the authoritative data DB for observations,
   forecasts, calibration, snapshots, and settlements.
2. `state/zeus_trades.db` is trades-focused DB truth.
3. `state/zeus.db` is legacy and not the current canonical data store.
4. v2 tables exist but were structurally empty at the audit point:
   observation instants, historical forecasts, calibration pairs, Platt
   models, ensemble snapshots, and settlements were not the populated current
   data path.
5. Legacy tables still carried current data at audit time:
   `observations` was populated for 51 cities and `observation_instants` for
   46 cities.
6. Daily and hourly ingest were lagging by days, not minutes, so "current data
   is fully healthy" was not a truthful claim.
7. Hong Kong source status is a separate source-validity issue; read
   `docs/operations/current_source_validity.md`.

## Invalidation Conditions

Re-audit before relying on this file if:

- any v2 table becomes populated or promoted
- a new writer/cutover lands
- ingest freshness materially changes
- DB role ownership changes
- the file is older than Max staleness and the task needs present-tense data
  truth

## Stale Behavior

If stale, this file may be used only as historical planning context. It must
not justify runtime behavior, backfill execution, data readiness, or source
truth. Record `needs fresh audit` and stop before implementation that depends
on current data posture.
