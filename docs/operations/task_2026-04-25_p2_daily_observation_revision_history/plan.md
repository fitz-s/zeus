# P2 4.4.A2 Daily Observation Revision History Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: in progress

## Background

POST_AUDIT_HANDOFF 4.4.A requires daily observation backfills to stop silently
replacing `observations` rows when a rerun sees a different upstream payload.
A1 closed the `observation_instants_v2` writer seam and added the obs_v2
`observation_revisions` evidence table. A2 applies the same history-first
principle to the WU/HKO daily scripts that write the canonical daily
`observations` table, but the daily shape is different enough to need a
dedicated daily revision surface.

The daily natural key is `UNIQUE(city, target_date, source)`. One daily row
carries both high and low observations with split high/low provenance JSON.
A same-key rerun with the same combined payload hash is idempotent. A same-key
rerun with a different payload hash is revision evidence and must not overwrite
the current row.

## Phase Entry Evidence

- Reread root `AGENTS.md`, `docs/operations/current_state.md`, scoped docs
  routers, `src/data/AGENTS.md`, `src/state/AGENTS.md`, `scripts/AGENTS.md`,
  `tests/AGENTS.md`, and `architecture/AGENTS.md`.
- Ran `python3 scripts/topology_doctor.py --task-boot-profiles --json` and
  `python3 scripts/topology_doctor.py --fatal-misreads --json`; both passed.
- Ran topology navigation/digest/impact for the candidate A2 files. Navigation
  correctly treated the schema/helper/docs expansion as packet-scoped rather
  than ordinary data-ingest work, so this packet uses explicit planning-lock
  closeout.
- Scout inventory confirmed the WU/HKO daily overwrite seams. Architect review
  rejected a generic `observation_revisions(table_name='observations')` helper:
  daily observations have a distinct natural key and split high/low row shape.
- Ogimet daily history is deferred. The current Ogimet script does not expose a
  stable daily payload hash comparable to WU/HKO, so forcing it into this packet
  would change source semantics rather than only write-conflict behavior.

## Semantic Proofs

- Truth surface: `observations` remains the daily observation truth table.
  This packet changes duplicate-key write behavior only; it does not choose or
  reclassify source truth.
- Natural key: `(city, target_date, source)` from `src/state/db.py`.
- Revision sink: new `daily_observation_revisions`, keyed to the daily row
  shape and carrying existing/incoming high, low, and combined payload hashes.
- Fatal misreads avoided: WU daily settlement observations, HKO daily
  observations, Day0 monitoring, historical hourly observations, Ogimet
  METAR/SYNOP mirror evidence, and forecast-skill rows are not interchangeable.
- Existing rows with missing or different provenance are not silently fixed or
  quarantined here; incoming rows are recorded as revision evidence.

## Scope

### In scope

- `scripts/backfill_wu_daily_all.py`
- `scripts/backfill_hko_daily.py`
- `src/state/db.py`
- `src/data/daily_observation_writer.py`
- a no-behavior-change extraction in `src/data/daily_obs_append.py` so live and
  backfill paths share the canonical daily row mapping
- focused tests for same-hash no-op, different-hash revision capture, no
  current-row overwrite, and no `INSERT OR REPLACE` in the WU/HKO daily scripts
- packet/control surfaces and companion registries

### Out of scope

- production DB mutation or data population
- live daily ingest revision-history behavior or coverage semantics
- `scripts/backfill_ogimet_metar.py` until a stable daily payload-identity
  contract is planned
- `observation_instants_v2` / `scripts/backfill_obs_v2.py`
- legacy `observation_instants` / `backfill_hourly_openmeteo.py`
- row-level quarantine of historical empty-provenance WU rows
- source-routing changes, station changes, fallback changes, P3 reader-gate
  design, or P4 population

## Deliverables

- Add schema-backed daily revision history and replace WU/HKO daily backfill
  `INSERT OR REPLACE` writes with hash-checked insert logic:
  - no existing natural key: insert current row
  - same natural key + same combined high/low payload hash: no-op
  - same natural key + different or missing existing payload hash: write
    `daily_observation_revisions` and leave current `observations` row
    unchanged
- Preserve rerun counters so no-op/revision-only collisions do not inflate
  inserted/current-row counts.
- Add tests proving the WU/HKO backfill surfaces preserve current rows and write
  revision rows on payload drift.
- Mark A1 packet artifacts closed and move the live pointer to A2.

## Verification

- `python3 -m py_compile src/data/daily_observation_writer.py src/data/daily_obs_append.py src/state/db.py scripts/backfill_wu_daily_all.py scripts/backfill_hko_daily.py tests/test_backfill_completeness_guardrails.py tests/test_k2_live_ingestion_relationships.py tests/test_db.py`
- `pytest -q tests/test_backfill_completeness_guardrails.py tests/test_k2_live_ingestion_relationships.py tests/test_backfill_scripts_match_live_config.py`
- `pytest -q tests/test_db.py tests/test_architecture_contracts.py tests/test_truth_surface_health.py`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --scripts --json`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <changed scripts/tests>`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p2_daily_observation_revision_history/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 scripts/topology_doctor.py --change-receipts --receipt-path docs/operations/task_2026-04-25_p2_daily_observation_revision_history/receipt.json`
- `git diff --check -- <packet files>`

## Stop Conditions

- Stop if the fix requires changing source/station/fallback routing.
- Stop if existing empty-provenance rows must be quarantined or backfilled to
  complete this packet.
- Stop if live daily ingest coverage semantics or revision semantics need to
  change.
- Stop if script counters require production data mutation or replay reruns to
  prove correctness.
