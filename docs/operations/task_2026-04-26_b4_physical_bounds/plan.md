# Slice K1+K3.B4-physical-bounds — obs_v2 temp value bounds

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: parent packet `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` Wave 1 + workbook B4 row (archived) + con-nyx G6 review pattern lesson #1 (production-path integration test required).
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

Add physical-bounds validation to `observation_instants_v2` temperature columns (`temp_current`, `running_max`, `running_min`) at both writer level (load-bearing — covers all writes including legacy DBs) and schema CREATE TABLE level (new DBs only — SQLite ALTER cannot add CHECK retroactively).

Catches the Warsaw 88°C class of poison-data failure mentioned in workbook N1.8.

## 1. Sub-deliverable split (B4 workbook entry has two halves)

| Workbook deliverable | This slice | Reason |
|---|---|---|
| Physical-bounds CHECK on obs_v2 | ✅ THIS SLICE | Universal bounds (-90/60°C, -130/140°F); minimal coupling; immediate antibody value |
| Per-city source-binding test | ❌ DEFERRED to followup slice | Requires reading `docs/operations/current_source_validity.md` truth surface and parsing per-(city, target_date) source mapping; non-trivial; will fold into a separate slice once current_source_validity.md format stabilizes |

## 2. Bounds rationale

| Unit | Lower | Upper | Justification |
|---|---|---|---|
| C | -90 | 60 | Lower covers Vostok station all-time low (-89.2°C, 1983) with 0.8° margin; upper covers Death Valley extreme (54.4°C verified, 56.7°C disputed) with 3.3° margin |
| F | -130 | 140 | Equivalents (-90°C ≈ -130°F, 60°C = 140°F) |

Kelvin is rejected at writer (`_ALLOWED_TEMP_UNITS = {"F", "C"}` at L85) so K-bounds not needed.

The Warsaw 88°C case (workbook N1.8) sits well above the 60°C upper bound — would be rejected at write.

## 3. Files touched

| File | Change | Hunk size |
|---|---|---|
| `src/data/observation_instants_v2_writer.py` | Add `_PHYSICAL_TEMP_BOUNDS_C: tuple[float, float] = (-90.0, 60.0)` + `_PHYSICAL_TEMP_BOUNDS_F: tuple[float, float] = (-130.0, 140.0)` constants near L85; add bounds check in `_validate()` for the 3 temp fields (skip if None) | ~30 lines added |
| `src/state/schema/v2_schema.py` | Add `CHECK ((temp_unit IN ('C') AND (temp_current IS NULL OR (temp_current BETWEEN -90 AND 60))) OR ...)` to CREATE TABLE — restrict to new DBs only | ~5 lines added |
| `tests/test_obs_v2_physical_bounds.py` (NEW) | 8 antibody tests including production-path integration via `insert_rows()` | ~150 lines |
| `architecture/test_topology.yaml` | Register | 1 line |

## 4. Worktree-collision check (re-verified 2026-04-26)

- `src/data/observation_instants_v2_writer.py`: NOT touched by either active worktree. SAFE.
- `src/state/schema/v2_schema.py`: NOT touched by either active worktree. SAFE.
- `tests/test_obs_v2_physical_bounds.py`: NEW. SAFE.

## 5. Antibody design (con-nyx pattern lesson #1: include production-path integration)

| # | Test | Type |
|---|---|---|
| 1 | `test_physical_bounds_constants_typed_and_in_canonical_range` | Pin constants — type + value (defends against silent widening to allow 88°C) |
| 2 | `test_obs_v2_row_rejects_out_of_bounds_temp_current_celsius` | Constructor — Warsaw 88°C scenario (`temp_current=88.0`, `temp_unit="C"`) raises |
| 3 | `test_obs_v2_row_rejects_out_of_bounds_temp_current_fahrenheit` | Same in °F (`temp_current=200.0`, `temp_unit="F"`) raises |
| 4 | `test_obs_v2_row_accepts_in_bounds_values` | Control — 25°C, 77°F in-bounds accepted |
| 5 | `test_obs_v2_row_accepts_boundary_values` | Edge — exactly -90 and 60 °C accepted (BETWEEN-style inclusive) |
| 6 | `test_obs_v2_row_rejects_just_outside_bounds` | Edge — -90.01 and 60.01 °C rejected |
| 7 | `test_obs_v2_row_accepts_null_temp_fields` | Nullable contract — None for all 3 temp fields passes |
| 8 | `test_insert_rows_rejects_out_of_bounds_via_production_path` | **Production-path integration**: construct row with bad value (must raise at constructor) OR if row constructed first, verify `insert_rows()` either rejects or DB CHECK constraint catches it (depending on whether CHECK was applied in fresh schema). Tests the full path, not just the constructor. |

## 6. Sequence (RED→GREEN)

1. Write `tests/test_obs_v2_physical_bounds.py` with all 8 tests.
2. Run pytest — expect 8 RED (constructor doesn't reject; constants don't exist).
3. Commit RED.
4. Add constants + bounds check to writer.
5. Add CHECK to v2_schema CREATE TABLE.
6. Re-run pytest — expect 8 GREEN.
7. Commit GREEN.
8. Run regression panel — confirm delta=0.
9. Register test in `architecture/test_topology.yaml`.
10. Write receipt.json + work_log.md.

## 7. Out-of-scope

- **Per-city source-binding test** — deferred to separate slice (B4-source-binding) once truth surface format stabilizes.
- **Retroactive CHECK on existing live DBs** — SQLite ALTER limitation. Writer-level enforcement is the load-bearing antibody; schema CHECK covers fresh DBs.
- **Bounds on legacy `observations` table** — separate scope (workbook B4 explicitly cites obs_v2 writer).
- **Modifying `_ALLOWED_TEMP_UNITS`** — Kelvin rejection already in place.

## 8. Provenance

Recon performed live 2026-04-26 in this worktree:
- `src/data/observation_instants_v2_writer.py:85` — `_ALLOWED_TEMP_UNITS = frozenset({"F", "C"})`
- `src/data/observation_instants_v2_writer.py:243-246` — temp_unit validation (no bounds yet)
- `src/state/schema/v2_schema.py:275-277` — `temp_current REAL`, `running_max REAL`, `running_min REAL` (no CHECK)
- Workbook N1.8 cite: Warsaw 88°C escaped `availability_fact` validator coverage gap.
