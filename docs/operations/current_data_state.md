# Current Data State

Status: active current-fact surface
Last audited: 2026-04-21
Authority basis: Gate F Step 1 schema audit plus current architecture/data-rebuild law
Authority status: not authority law; this is present-tense routing for current data facts

## Purpose

Read this file when you need the current audited answer to:

- which DB is authoritative for data
- what is legacy versus v2 right now
- what current ingest freshness looks like
- which structural gaps still block a truthful "data is ready" narrative

Do not use this file to authorize rebuild execution, mutate DBs, or declare
live math ready.

## Canonical DB Roles

- `state/zeus-world.db`: authoritative data DB for observations, forecasts,
  calibration, snapshots, and settlements.
- `state/zeus_trades.db`: trades-focused DB.
- `state/zeus.db`: legacy DB, not the current canonical data store.

## Current Posture

### v2 tables

The v2 schema exists, but the audited posture is still structurally empty:

- `observation_instants_v2`: 0 rows
- `historical_forecasts_v2`: 0 rows
- `calibration_pairs_v2`: 0 rows
- `platt_models_v2`: 0 rows
- `ensemble_snapshots_v2`: 0 rows
- `settlements_v2`: 0 rows

Interpretation:

- v2 is structurally prepared
- v2 is not yet the populated current data path

### Legacy tables still carrying data

- `observations`: populated for 51 cities
- `observation_instants`: populated for 46 cities
- five cities are missing from the legacy hourly surface
- detailed counts and city lists come from Gate F Step 1; update this file only
  from fresh packet evidence

## Current Freshness / Ingest Posture

As of the Gate F audit:

- daily observation ingest is lagging by days, not minutes
- hourly/instant ingest is also lagging by days
- K2 appenders are not behaving like a healthy always-fresh current pipeline

This means present-tense "ingestion is active/current" claims must be made
carefully. Historical backfill must not silently replace missing live-boundary
freshness.

## Current Structural Blockers

1. v2 observation-instants posture is structurally present but not populated.
2. v2 historical forecast posture is structurally present but lacks the active
   writer path.
3. legacy hourly coverage is incomplete for five cities.
4. ingest freshness is stale enough that "current data fully healthy" is not a
   truthful claim.
5. Hong Kong source status remains a separate source-validity issue; see
   `docs/operations/current_source_validity.md`.

## How To Use This File

Use it as the compact current answer for data posture.

For durable law, read `docs/authority/zeus_data_rebuild_adr.md` and
`architecture/data_rebuild_topology.yaml`.

For present-tense blockers, read `docs/operations/known_gaps.md`.

For detailed audit evidence, read the Gate F Step 1 packet docs.

For current source-provider status, read
`docs/operations/current_source_validity.md`.

## What This File Is Not

- not a rebuild approval
- not a canonical DB manifest
- not a data inventory dump
- not permission to ignore current packet evidence
