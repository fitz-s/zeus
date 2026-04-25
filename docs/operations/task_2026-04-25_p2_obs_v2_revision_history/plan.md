# P2 4.4.A1 Obs V2 Revision History Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: closed

## Background

POST_AUDIT_HANDOFF 4.4.A requires observation/backfill writers to stop using
silent `INSERT OR REPLACE` overwrite semantics. The target behavior is
hash-checked idempotence: an incoming row with the same payload hash may be
treated as a rerun, but an incoming row with a different payload hash must be
captured as revision history instead of replacing canonical observation truth.

The first packet is deliberately `obs_v2`-only. `observation_instants_v2` has
one central typed writer and `backfill_obs_v2.py` already writes through that
writer. Daily WU/HKO/Ogimet backfills write a different legacy `observations`
shape and will be handled by a later A2 packet.

## Phase Entry Evidence

- Reread root `AGENTS.md`, `workspace_map.md`, `docs/authority/zeus_current_architecture.md`,
  `docs/authority/zeus_current_delivery.md`, scoped `src/data/AGENTS.md`,
  `src/state/AGENTS.md`, `scripts/AGENTS.md`, `tests/AGENTS.md`, and
  `docs/operations/AGENTS.md`.
- Ran semantic boot and fatal-misread gates. `--task-boot-profiles` and
  `--fatal-misreads --json` passed.
- Read current fact surfaces: `docs/operations/current_data_state.md` last
  audited 2026-04-23 and `docs/operations/current_source_validity.md` last
  audited 2026-04-21; both are within the 14-day planning window for this
  packet.
- Ran topology navigation for the broader 4.4.A candidate set. It correctly
  returned scope-expansion-required for multi-script + architecture/doc
  changes, so this packet uses explicit planning lock and a narrowed A1 scope.
- Scout inventory found `observation_instants_v2_writer.py` and
  `backfill_obs_v2.py` share one overwrite seam. Architect review selected
  schema-backed `obs_v2` history first, with daily backfill history deferred.

## Semantic Proofs

- Truth surface: `observation_instants_v2` remains canonical only for the v2
  hourly evidence lane; this packet changes how duplicate natural keys are
  handled, not which source family is valid.
- Zone: `src/state/schema/v2_schema.py` is schema/truth-contract work under
  planning lock; `src/data/observation_instants_v2_writer.py` is a K2 data
  writer seam.
- Invariants: INV-06 point-in-time truth, INV-14 metric identity spine, and
  the hourly-observation ingest boot profile apply.
- Source role proof: this packet does not change city/source routing, station
  selection, fallback order, or Hong Kong HKO caution status.
- Fatal misreads avoided: daily settlement, Day0 monitoring, historical hourly,
  and forecast-skill sources remain non-interchangeable; hash mismatch is
  evidence of a disputed incoming payload, not permission to overwrite.

## Scope

_The machine-readable list lives in `scope.yaml`; this section is a
human-readable mirror._

### In scope

- `src/state/schema/v2_schema.py`
- `src/data/observation_instants_v2_writer.py`
- `scripts/backfill_obs_v2.py` only for documentation/comment alignment if the
  central writer API contract needs it
- `tests/test_obs_v2_writer.py`
- `tests/test_backfill_scripts_match_live_config.py` for caller-level
  `rows_written` regression on obs_v2 reruns
- P3 residual packet closeout artifacts, only to align already-pushed P3
  closeout evidence before this packet advances the live pointer
- route/control manifests and this packet folder

### Out of scope

- production DB mutation
- daily `observations` backfill writers: WU, HKO, Ogimet
- legacy `observation_instants` / `backfill_hourly_openmeteo.py`
- live daily ingest `src/data/daily_obs_append.py`
- row-level quarantine or existing DB backfill
- P3 reader-gate design and P4 v2 population

## Deliverables

- Add an idempotent `observation_revisions` schema surface suitable for
  observation writer collision evidence.
- Replace the `observation_instants_v2` writer's `INSERT OR REPLACE` path with
  hash-checked write logic:
  - no existing row: insert the row
  - same natural key + same payload hash: treat as idempotent rerun without
    mutating the existing row
  - same natural key + different payload hash: append a revision row and leave
    the existing observation row unchanged
- Update obs_v2 writer regressions so the old "second write wins" behavior is
  forbidden.
- Keep `backfill_obs_v2.py` counters honest by returning zero for duplicate
  no-op and revision-only rows, with a caller-level regression.
- Mark the prior P3 residual packet artifacts closed, matching its pushed
  implementation commit before this packet becomes active.

## Verification

- `python3 -m py_compile src/state/schema/v2_schema.py src/data/observation_instants_v2_writer.py scripts/backfill_obs_v2.py tests/test_obs_v2_writer.py`
- `pytest -q tests/test_obs_v2_writer.py`
- Targeted state/data tests selected after implementation, including schema
  smoke coverage for `apply_v2_schema`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --fatal-misreads --json`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p2_obs_v2_revision_history/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_obs_v2_writer.py`
- `git diff --check -- <packet files>`

## Stop Conditions

- Stop if the writer change requires choosing or changing a source family,
  station, or fallback order.
- Stop if revision history requires mutating production DB rows or retroactive
  quarantine.
- Stop if daily backfill writers must be changed to complete the obs_v2 writer
  seam.
- Stop if existing schema assumptions require a broad migration/cutover instead
  of additive idempotent DDL.

## Closeout

- Implementation commit: `0837afc` (`Preserve obs v2 evidence when payloads drift`)
- Pushed to `origin/midstream_remediation`.
- Follow-up packet: `docs/operations/task_2026-04-25_p2_daily_observation_revision_history/plan.md`
