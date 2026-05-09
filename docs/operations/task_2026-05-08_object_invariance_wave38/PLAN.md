# Wave38 Plan — Remove Dead `hourly_observations` Compatibility Surface

Status: APPROVED

## Goal

Delete the dead `hourly_observations` compatibility table/view/script surface without mutating canonical live/world databases or promoting legacy data. This wave is a housekeeping object-meaning repair: a lossy local-hour compatibility object currently still looks like a valid evidence surface even though canonical hourly truth is `observation_instants_v2` via `observation_instants_current`.

## Invariant

Hourly observation evidence must preserve its real-world time basis and source authority. A lossy local-hour compatibility row must not remain constructible, scheduled, queried, or exposed as an evidence view after all runtime consumers have moved to DST-aware observation instants.

## Non-Goals / Stop Conditions

- Do not run migrations, `DROP TABLE`, or data rewrites against canonical `state/zeus-world.db`.
- Do not rebuild historical observation or diurnal data in this wave.
- Do not change `observation_instants_v2`, `observation_instants_current`, or `diurnal_curves` semantics.
- If any active live/calibration/monitor/exit/settlement consumer still requires `hourly_observations`, stop and record a detailed known gap instead of deleting.

## Repo Evidence

| Surface | Current Meaning | Finding |
| --- | --- | --- |
| `src/state/db.py` | schema init creates legacy table and evidence view | Constructibility root for stale compatibility rows |
| `scripts/etl_hourly_observations.py` | compatibility writer from `observation_instants_current` to lossy local-hour rows | Recreates stale object; dangerous if run because it writes world DB |
| `src/ingest_main.py` | ingest recalibration subprocess list | Still schedules compatibility writer |
| `scripts/backfill_hourly_openmeteo.py` | historical backfill follow-up | Still imports/runs compatibility writer after writes |
| `scripts/semantic_linter.py` | forbids bare table reads but allows writer/view DDL | Needs to become a no-reintroduction guard, not a compatibility exception |
| Tests/manifests | encode compatibility table/view/writer | Must flip from preservation tests to absence/no-reintroduction tests |

Explorer read-only pass found no live `src/**` consumer reading `hourly_observations`; live adjacent paths use `observation_instants`, `observation_instants_v2`, or `diurnal_curves`.

## Planned Repair

1. Remove schema creation for `hourly_observations` and `v_evidence_hourly_observations` from `src/state/db.py`.
2. Delete `scripts/etl_hourly_observations.py`.
3. Remove launch/follow-up hooks from `src/ingest_main.py`, `scripts/backfill_hourly_openmeteo.py`, and `scripts/validate_assumptions.py`.
4. Convert `scripts/semantic_linter.py` from compatibility-allowlist mode to fail-closed detection for both legacy table and evidence-view reads/DDL in non-test/non-migration code.
5. Update tests/manifests to assert the dead surface is not created, scheduled, allowlisted, or reintroduced.
6. Update `known_gaps.md` to close/archive this low-priority item or record any remaining data-layer blocker discovered during verification.

## Verification Plan

- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave38/PLAN.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...`
- Focused tests:
  - `tests/test_semantic_linter.py`
  - `tests/test_architecture_contracts.py`
  - `tests/test_world_writer_boundary.py`
  - `tests/test_structural_linter.py`
  - any existing db/schema tests touched by `src/state/db.py`
- Static sweep for `hourly_observations`, `v_evidence_hourly_observations`, and `etl_hourly_observations.py`.
- Critic review before closing wave.

## Verification Evidence

Passing:

- `python3 scripts/topology_doctor.py --task-boot-profiles`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave38/PLAN.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...`
- `git diff --check -- <Wave38 files>`
- `python -m py_compile` over touched source/scripts/tests
- `python -m pytest tests/test_semantic_linter.py -q --tb=short` → 54 passed
- `python -m pytest tests/test_architecture_contracts.py::test_init_schema_does_not_create_legacy_hourly_compatibility_surface tests/test_world_writer_boundary.py::test_deleted_hourly_observations_writer_not_allowlisted tests/test_structural_linter.py::test_time_sensitive_etl_no_longer_reads_legacy_observations_local_hour -q --tb=short` → 3 passed

Noise / not used as Wave38 proof:

- Full `tests/test_world_writer_boundary.py tests/test_structural_linter.py` run still has pre-existing unrelated repo-wide failures in world-writer allowlist, global semantic-linter scan, and Bin unit scan. Wave38 adds targeted tests proving the deleted compatibility writer stays absent and not allowlisted; the unrelated failures were not fixed in this housekeeping wave.

Static sweep:

- `rg hourly_observations|v_evidence_hourly_observations|etl_hourly_observations src scripts` now finds only the no-reintroduction linter. No runtime `src/` or ordinary `scripts/` path creates, schedules, imports, or reads the deleted surface.

## Data-Layer Residual

Physical legacy tables/views may still exist inside already initialized DB files. This wave intentionally does not run destructive SQL. Active follow-up is now `docs/to-do-list/known_gaps.md` → `[OPEN — DATA-LAYER APPROVAL REQUIRED] Physical hourly_observations residue may remain in existing DB files`.

## Critic Verdict

APPROVE. Critic verified the dead compatibility surface is no longer created by
`init_schema()`, no longer scheduled by ingest, no longer rebuilt by Open-Meteo
backfill, no longer registered in the script manifest, and guarded by
`semantic_linter.py` against table/view reads, writes, and DDL. Critic also
confirmed the physical DB cleanup residual is correctly left as an explicit
data-layer approval item in `known_gaps.md`.
