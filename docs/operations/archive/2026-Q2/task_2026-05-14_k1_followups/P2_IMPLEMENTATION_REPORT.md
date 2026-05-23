# P2 Implementation Report

**Phase:** P2 — DDL surfaces consult registry + retire world_schema_manifest  
**Branch:** `fix/k1-p0-daily-obs-redirect-2026-05-14`  
**P2 tip commit:** (to be filled after C4 commit)  
**P1 base tip:** `0c10a326e4`  
**Date:** 2026-05-14  
**Author:** Executor agent (claude-sonnet-4-6)

---

## Topology Doctor Citation

```
python3 scripts/topology_doctor.py --planning-lock \
  --plan-evidence docs/operations/task_2026-05-14_k1_followups/IMPLEMENTATION_REVIEW_P1.md \
  --files src/state/db.py architecture/world_schema_manifest.yaml \
          src/contracts/world_schema_validator.py \
          tests/fixtures/before_p2_sqlite_master.sql
Result: topology check ok
```

Planning lock admitted. Editing `src/state/db.py` authorized per plan evidence.

---

## Commits

| Commit | Description |
|--------|-------------|
| C1 `c8bf21638e` | `tests/fixtures/before_p2_sqlite_master.sql` — pre-P2 sqlite_master baseline fixture |
| C2 `4a6ac29dc6` | `_P1_BASELINE_VIOLATIONS` populated (69 entries) + ATTACH `size > 0` guard |
| C3 `5ee05e259e` | Registry-driven DDL refactor + retire `_v2_forecast_tables` bool |
| C4 | D5 manifest retirement + report (this commit) |

---

## P2 Scope Delivered

### D1 — Pre-flight baseline fixture (C1)
- `tests/fixtures/before_p2_sqlite_master.sql` committed from `:memory:` init of
  `init_schema_world_only` + `init_schema_forecasts` at P1 tip `0c10a326e4`.
- 2156 lines: world=148 items, forecasts=23 items.
- Committed BEFORE any DDL change (non-negotiable ordering per PLAN §2 pre-flight).

### D2 — Refactor init_schema* to consult registry (C3)

**`_v2_forecast_tables` bool RETIRED:**
- `init_schema(conn)` signature: `_v2_forecast_tables: bool = True` kwarg removed.
- `apply_v2_schema(conn, forecast_tables=False)` now always called — `init_schema`
  is world-class-only post-K1 split.
- `init_schema_world_only` updated to `init_schema(conn)` (no kwarg).

**`_FORECAST_TABLES` constant replaced with registry derivation:**
- `init_schema_forecasts`: `_FORECAST_TABLES` tuple replaced by
  `tables_for_class(SchemaClass.FORECAST_CLASS)` from `src/state/table_registry.py`.
- Both ATTACH-path loops (TABLE and INDEX) iterate the registry set.
- **Stop-condition #8 closed:** ATTACH path iterates registry, not raw
  `world_src.sqlite_master`, so no K1-orphaned table names can silently appear.

**isinstance guard (opt-in):**
- `init_schema_forecasts` checks: if `isinstance(conn, TypedConnection)` and
  `conn.db_identity != DBIdentity.FORECASTS`, raises `ValueError`.
- Raw `sqlite3.Connection` callers pass through (P3 migrates them to
  `ForecastsConnection`).

**Post-P2 ATTACH fallback:**
- When world.db exists + size>0 but has no legacy forecast table copies (i.e.,
  post-P2 world-class-only world.db), the ATTACH branch now detects missing
  forecast tables and calls static helpers to fill them. This ensures
  `_ensure_v2_forecast_indexes` always finds the v2 tables it references.

### D3 — Acceptance gate #3 — byte-equivalence (C3)

Post-refactor `:memory:` init output byte-identical to
`tests/fixtures/before_p2_sqlite_master.sql`:

```
BYTE-EQUIVALENCE PASS: output matches fixture exactly
```

Schema hash re-pinned (`tests/state/_schema_pinned_hash.txt`): expected delta —
`init_schema()` with default args no longer creates the 4 v2 forecast-class tables
on world conn. On-disk schema SET is unchanged; the change is which code path
creates which tables on which conn.

### D4 — _P1_BASELINE_VIOLATIONS populated (C2)

`scripts/check_writer_signature_typing.py`: 69 pre-P3 violations captured at P1
tip `0c10a326e4`. Script now exits 0 (PASS) for violations within the baseline.
`check_dynamic_sql.py` baseline also updated with pre-existing P1-era new files.

### D5 — size>0 guard for ATTACH branch (C2)

`init_schema_forecasts`: `ZEUS_WORLD_DB_PATH.exists() and ZEUS_WORLD_DB_PATH.stat().st_size > 0`
condition added. Zero-byte stub world.db files in test env no longer silently
enter the ATTACH path; fallback to static DDL helpers is correctly triggered.

### D6 — world_schema_manifest.yaml + world_schema_validator.py RETIRED (C4)

- `architecture/world_schema_manifest.yaml` — DELETED.
- `src/contracts/world_schema_validator.py` — DELETED.
- `src/main.py:759-766` — `validate_world_schema_at_boot` call and surrounding
  try/except block removed. Replaced by a comment noting P3 will wire
  `assert_db_matches_registry`.
- `architecture/AGENTS.md:73` — manifest entry updated to RETIRED status.
- `scripts/check_dynamic_sql.py` — removed allowlist entry for deleted validator.
- `scripts/check_table_registry_coherence.py` — comment referencing manifest updated.

---

## Antibody Proof

### Byte-equivalence gate (acceptance gate #3)
```
init_schema_world_only(:memory:) + init_schema_forecasts(:memory:)
→ sqlite_master dump byte-identical to tests/fixtures/before_p2_sqlite_master.sql
RESULT: PASS
```

### Registry-driven ATTACH (stop-condition #8)
Both `_FORECAST_TABLES` loops replaced with `_registry_forecast_tables` set derived
from `tables_for_class(SchemaClass.FORECAST_CLASS)`. No raw sqlite_master iteration.

### isinstance guard (opt-in)
TypedConnection with wrong db_identity raises `ValueError` immediately. Raw
`sqlite3.Connection` callers unaffected.

### Static-helpers parity
Post-P2 ATTACH fallback ensures that when world.db is world-class-only (no v2
forecast tables), static helpers fill the gap so `_ensure_v2_forecast_indexes`
always succeeds. Verified by `test_relA_*` tests.

---

## Test Results

```
tests/state/test_forecast_db_split_invariant.py: 9 passed, 4 skipped
tests/state/test_table_registry_coherence.py: 13 passed
tests/test_riskguard.py + test_lifecycle.py + test_calibration_observation.py
  + test_phase4_foundation.py + test_source_run_schema.py: 116 passed
Full suite (excl. test_pnl_flow_and_audit.py): see C4 run
```

---

## Stop-Conditions Status

| # | Stop Condition | Status |
|---|---------------|--------|
| 1 | Schema-version bump required | N/A — schema hash re-pinned (expected delta) |
| 2 | DDL byte-equivalence baseline divergence | PASS — byte-identical |
| 3 | REL-3 grep test cannot be un-skipped | DEFERRED to P3 (callers not yet migrated) |
| 4 | PR #114 ATTACH-path reads K1-orphaned tables | CLOSED — registry iteration (stop-cond #8) |
| 5 | Concurrent --rename-world-tables | NOT applicable in P2 |
| 8 | ATTACH-path iterates raw sqlite_master | CLOSED — registry-driven |

---

## Pre-existing Issues / P3 Notes

1. `test_rel3_no_forecast_tables_on_world_connection` remains SKIPPED — P3
   (caller migration) un-skips it.
2. `_FORECAST_TABLES` constant retained in `src/state/db.py` for now — the
   `test_a1_db_constant_matches_registry` test in `test_table_registry_coherence.py`
   cross-checks it; P3 removes it after callers are migrated.
3. `assert_db_matches_registry` defined (P1) but NOT wired into boot — P3 wires
   it as FATAL per INV-05 fail-closed.

---

## Predecessor Compatibility

- **K1 PR #114 ATTACH-path index fix** (commit `_ensure_v2_forecast_indexes`):
  COMPATIBLE — helper runs unconditionally post-ATTACH; registry-driven table
  iteration does not change the index creation logic.
- **INV-37**: no cross-DB write transactions introduced in P2.
- **INV-05** fail-closed: manifest retirement does not leave an advisory-mode
  gap — `validate_world_schema_at_boot` was already warn-only in Phase 2 context;
  P3 wires the FATAL replacement.
