# Work Log — Slice K1+K3.B4-physical-bounds

Created: 2026-04-26
Authority basis: `plan.md`, `scope.yaml`.

## 2026-04-26 — slice opened + landed

### Step 0: scaffold + RED — commit `cfa5c0e`
- Wrote plan.md + scope.yaml.
- Antibody `tests/test_obs_v2_physical_bounds.py` with 10 tests:
  - 6 rejection tests (pinned RED — no bounds yet)
  - 4 acceptance tests (passed pre-impl as controls)
- pytest: 6 RED / 4 GREEN as designed.

### Step 1: GREEN — pending commit
- Added `_PHYSICAL_TEMP_BOUNDS_C = (-90.0, 60.0)` + `_PHYSICAL_TEMP_BOUNDS_F = (-130.0, 140.0)` constants at `src/data/observation_instants_v2_writer.py:87-94`.
- Added bounds check in `_validate()` for temp_current / running_max / running_min (skip None per nullable schema).
- Added BETWEEN-style CHECK constraint in `src/state/schema/v2_schema.py` `observation_instants_v2` CREATE TABLE (NEW DBs only — SQLite ALTER cannot retroactively add CHECK).
- pytest: 10/10 GREEN.

### Step 2: regression panel
- Ran `tests/test_obs_v2_writer.py + tests/test_obs_v2_reader_gate.py + tests/test_obs_v2_dst_missing_hour_flag.py + tests/test_architecture_contracts.py + tests/test_live_safety_invariants.py + tests/test_cross_module_invariants.py`.
- 5 failures pre-existing (same 4 day0/chain reds + 1 K4 structural-linter). Adjacent obs_v2 tests all green — no collision with existing writer fixtures.
- delta = 0 NEW failures.

### Step 3: register + close
- `architecture/test_topology.yaml`: registered new test under `tests/`.
- `receipt.json` + this `work_log.md` close.

### Notes for downstream
- Per-city source-binding test (the OTHER half of workbook B4) NOT in this slice. Deferred to followup once `current_source_validity.md` parsing format stabilizes.
- Schema CHECK only applies to NEW DBs. Legacy DBs rely on the writer-level enforcement, which is the load-bearing antibody.
- Bounds are conservative — would reject e.g. a sensor reading 65°C even if physically possible. Operator escalation required to widen; do not silently relax.

### con-nyx pattern lesson #1 applied
Test #8 (`test_insert_rows_integration_path_rejects_out_of_bounds`) exercises the production path end-to-end (build row → call insert_rows on :memory: DB) rather than just literal-arg unit testing. Explicitly addresses the lesson from G6 review.
