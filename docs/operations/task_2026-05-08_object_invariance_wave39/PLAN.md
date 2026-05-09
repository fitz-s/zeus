# Wave39 Plan — `solar_daily` Malformed-Rootpage Day0 Degrade Antibody

Status: APPROVED

## Goal

Resolve the stale `solar_daily` malformed-rootpage known gap without mutating
DB files. The expected behavior is degrade/fail-closed at the Day0 evidence
boundary: a corrupted `solar_daily` table must not crash the cycle and must not
produce executable Day0 temporal context.

## Invariant

Day0 temporal context is only tradable when solar/DST context is available from
a readable, semantically valid `solar_daily` source. If SQLite reports
`malformed database schema (solar_daily) - invalid rootpage`, the real object is
not a valid SolarDay; downstream evaluator and monitor consumers must treat it
as missing authority and degrade to non-trading / stale-monitor state.

## Evidence

Read-only mapping found the runtime shape already degrades:

- `src/signal/diurnal.py::get_solar_day()` catches DB read failures and returns `None`.
- `build_day0_temporal_context()` refuses to build context when solar data is unavailable.
- evaluator Day0 candidate path turns missing context into `DATA_STALE` with
  rejection reason `Solar/DST context unavailable for Day0`.
- monitor Day0 refresh preserves prior posterior and tags `missing_solar_context`.

The missing piece is an exact regression for the historical SQLite rootpage
failure string, plus boundary tests tying the missing context to evaluator and
monitor non-trading semantics.

## Non-Goals / Stop Conditions

- Do not run `PRAGMA integrity_check` or any repair against canonical DB files.
- Do not rebuild `solar_daily`.
- Do not add migration/drop/recreate logic.
- If the code path requires a real DB repair to behave safely, record a
  detailed data-layer known gap instead of patching around it.

## Planned Repair

1. Add a `tests/test_diurnal.py` regression: a `sqlite3.DatabaseError` containing
   the historical malformed-rootpage message makes `build_day0_temporal_context()`
   return `None`.
2. Add/update evaluator boundary test evidence that missing Day0 context remains
   `DATA_STALE`.
3. Add monitor boundary test evidence that the same missing authority preserves
   prior posterior and emits `missing_solar_context`.
4. If tests pass, move the stale `solar_daily` known-gap item to archive as
   code-behavior verified, not physically repaired.

## Verification Plan

- Focused pytest for `tests/test_diurnal.py` rootpage regression.
- Focused pytest for evaluator and monitor boundary tests in `tests/test_runtime_guards.py`.
- Topology planning-lock and map-maintenance closeout for touched files.
- No live/prod DB commands.

## Verification Evidence

Passing:

- `python -m pytest tests/test_diurnal.py tests/test_runtime_guards.py::test_day0_observation_path_rejects_missing_solar_context tests/test_runtime_guards.py::test_day0_monitor_refresh_degrades_on_malformed_solar_daily_rootpage -q --tb=short` → 9 passed.
- `python -m py_compile tests/test_diurnal.py tests/test_runtime_guards.py`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_diurnal.py tests/test_runtime_guards.py tests/AGENTS.md architecture/test_topology.yaml docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave39/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave39/PLAN.md` → pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files tests/test_diurnal.py tests/test_runtime_guards.py tests/AGENTS.md architecture/test_topology.yaml docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave39/PLAN.md` → pass.
- `git diff --check` on Wave39 test/registry/packet files → pass.

Known-gap disposition:

- The active `solar_daily` malformed-rootpage stale item was moved from
  `docs/to-do-list/known_gaps.md` to `docs/to-do-list/known_gaps_archive.md` as
  behavior-verified closed.
- Residual physical DB rootpage repair remains outside this wave. Any actual
  DB repair/migration still requires operator-approved data-layer inventory,
  backup, dry-run, and rollback.

Topology note:

- The tests/packet route admitted the intended test and registry files.
- The known-gaps workbook route remained advisory-only even for closing a
  verified stale item; this is recorded as topology friction. The edit was a
  minimal evidence-ledger move under the user's standing instruction to archive
  closed known gaps, not runtime/source permission.

## Critic Verdict

APPROVE. Critic confirmed the closure does not overclaim physical DB repair,
the active register no longer carries the stale blocker, and the tests prove
malformed `solar_daily` rootpage evidence degrades before executable Day0
probability/trade decisions while monitor refresh preserves prior posterior.
