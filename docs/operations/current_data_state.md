# Current Data State

Status: active current-fact surface
Last audited: 2026-04-23
Authority basis: Gate F Step 1 schema audit + step 3 fleet closeout + step 4 Phase 2 plan; architecture/data-rebuild law
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

As of 2026-04-23 the `observation_instants_v2` surface is populated; other v2
tables remain structurally present but unpopulated pending their own Phase
plans:

- `observation_instants_v2`: **1,812,495 rows** across 50 cities
  (`data_version='v1.wu-native'`, 2024-01-01 → 2026-04-21)
  - Source breakdown: `wu_icao_history` 931,677 (47 cities primary) +
    `meteostat_bulk_*` 815,585 (46 cities extremum supplement) +
    `ogimet_metar_*` 65,233 (3 primary + DST/gap supplement) +
    `hko_hourly_accumulator` 0 (accepted accumulator-forward gap)
  - Audit posture (scripts/audit_observation_instants_v2.py): 0 tier
    violations, 0 source-tier mismatches, 0 UNVERIFIED rows, 0 openmeteo
    rows, 233 confirmed upstream gaps allowlisted
- `historical_forecasts_v2`: 0 rows
- `calibration_pairs_v2`: 0 rows
- `platt_models_v2`: 0 rows
- `ensemble_snapshots_v2`: 0 rows
- `settlements_v2`: 0 rows

Atomic cutover state (as of this refresh, post-flip):

- `zeus_meta.observation_data_version`: `'v1.wu-native'` — Phase 2 atomic flip
  landed 2026-04-23 under Gate F step 5 evidence.
- `observation_instants_current` VIEW: returns the 1,812,495-row v1.wu-native
  corpus (50 cities). Rollback command: `UPDATE zeus_meta SET value='v0'` —
  single statement, <1s.
- ETL scripts `scripts/etl_diurnal_curves.py` and
  `scripts/etl_hourly_observations.py` now read from `observation_instants_current`
  (the VIEW), making the atomic flip propagate to derived tables on every
  ETL run.
- Derived tables post-rebuild from v2:
  - `diurnal_curves`: 4,800 rows × 50 cities (HK has 0 rows — plan v3 Option A
    accepted gap; signal layer AC11 fallback active)
  - `hourly_observations`: 1,812,401 rows × 50 cities (94 of 1.8M dropped:
    91 DST-ambiguous + 3 temperature-range rejected)
  - `diurnal_peak_prob`: 14,400 rows (50 × 12 months × 24 hours)
- Legacy `observation_instants` table (867,489 `openmeteo_archive_hourly` rows)
  remains present in read-only compat mode; plan v3 Phase 4 DROP follows +30d
  post-flip.

Interpretation:

- v2 observation-instants is the current runtime data path via the VIEW.
- Signal layer (`src/signal/diurnal.py`) reads `diurnal_curves` and
  `diurnal_peak_prob` which are now both v2-sourced.
- Other v2 tables (`historical_forecasts_v2`, `calibration_pairs_v2`,
  `platt_models_v2`, `ensemble_snapshots_v2`, `settlements_v2`) remain
  structurally prepared but unpopulated — separate migration packets needed.

### Legacy tables still carrying data

- `observations`: populated for 51 cities
- `observation_instants`: populated for 46 cities with 867,489 rows (all
  `source='openmeteo_archive_hourly'` — legacy Tier 4, superseded by v2 when
  the Phase 2 flip lands); five cities remain missing from this legacy hourly
  surface, which is one of the structural drivers of the v2 migration
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

## Refresh Protocol

Refresh trigger:

- a new data/backfill/schema audit lands
- any packet changes DB roles, v2 table posture, ingest freshness, or major
  coverage gaps
- the file is older than 14 days and is being used for planning

Required evidence: fresh packet audit evidence, current machine manifests or
DB/schema audit output, and explicit source packet path in the work log or
receipt.

Manual refresh rule:

- update from evidence only
- keep the file compact
- preserve `Last audited`
- do not paste raw query dumps or large tables here
- do not edit this file from memory

Maximum staleness: 14 days for planning live data/backfill work; otherwise
treat as historical current-fact evidence and re-audit first.

## What This File Is Not

- not a rebuild approval
- not a canonical DB manifest
- not a data inventory dump
- not permission to ignore current packet evidence
