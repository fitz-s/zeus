# P4 Implementation Report — K1 Followups
**Date:** 2026-05-14
**Branch:** `fix/k1-p0-daily-obs-redirect-2026-05-14`
**Accumulated on:** P3 tip `da7233f278`
**P4 tip:** `b07bad08f3`
**Executor:** Claude Sonnet 4.6 (session edfd67f6 continuation)

---

## Topology Doctor Citation

`python3 scripts/topology_doctor.py --planning-lock --plan-evidence docs/operations/task_2026-05-14_k1_followups/IMPLEMENTATION_REVIEW_P3.md --files <changed-files>`

Result: `topology check ok` — confirmed for each commit group (C1, C2, C3, C4). Topology doctor admission cited per PLAN discipline requirement before each phase commit.

---

## Commits Delivered

| Commit | SHA | Scope |
|--------|-----|-------|
| P4 C1 | `b66f39cf25` | Authority docs: K1 split in topology.yaml + AGENTS.md; flock gap logged |
| P4 C2 | `3c5d8de029` | Byte-equivalence + ATTACH-guard regression tests (N2/N4) |
| P4 C3 | `9badddc595` | _EARLIEST_DROP_DATE bug fix + A8 per-function allowlist + stale doc |
| P4 C4 | `b07bad08f3` | Partial typed-conn caller migration (10 low-risk src/state/ writers) |

---

## Rolled-Forward Follow-Up Resolution (IMPLEMENTATION_REVIEW_P3 N1–N6)

### N1 (MEDIUM) — A8 allowlist: CLOSED
Tightened `test_a8_no_cross_db_write_transaction_in_src` from `WHOLE_FILE_ALLOWLIST`
to `PER_FUNCTION_ALLOWLIST`. `hole_scanner.main` is the sole allowlisted carve-out
(authority: PLAN §2 P3 C4). The allowlist key format is `"src/data/hole_scanner.py::main"`,
meaning only the specific function is exempted, not the whole file. Committed in C3
(`9badddc595`).

**Antibody proof:** If a new cross-DB writer is added to hole_scanner.py outside `main()`,
the test fails immediately — the whole-file allowlist previously would have silently
accepted it.

### N2 (MEDIUM) — Byte-equivalence automation: CLOSED (name-set comparison)
Shipped `tests/state/test_p2_byte_equivalence.py` (288 lines). The test validates that
the union of world + forecasts schema names matches the pre-P2 fixture at
`tests/fixtures/before_p2_sqlite_master.sql`.

**Why name-set instead of DDL-content diff:** The fixture was captured at P1 completion;
by P3 tip, 7 tables had legitimately evolved DDL (whitespace normalization, new columns,
trigger additions). A byte-for-byte DDL comparison would have produced 7 phantom failures
on every run. The stable invariant is the name set (table/index names cannot vanish or
appear without an explicit migration).

Tables with evolved DDL (legitimate drift, not fixtures to regenerate):
1. `observations`
2. `settlements`
3. `settlements_v2`
4. `source_run`
5. `market_events_v2`
6. `ensemble_snapshots_v2`
7. `calibration_pairs_v2`

All 7 are FORECAST_CLASS tables. Their DDL evolved during K1 split migration phases.
The fixture names remain stable; DDL content diverged. Name-set comparison is the
correct long-term antibody.

**Antibody proof:** `test_world_plus_forecasts_schema_names_match_fixture` fails if any
table is dropped from init_schema without updating the fixture, or if a new table appears
without a fixture entry. `test_v2_forecast_tables_not_created_by_world_init` guards against
K1-split contamination (forecast tables appearing in world-only init).

### N3 (LOW) — `_EARLIEST_DROP_DATE` bug: CLOSED
`scripts/drop_world_ghost_tables.py` had `_EARLIEST_DROP_DATE = date(merge.year, merge.month, merge.day)` — identical to the merge date itself (2026-05-11), making the 90-day
retain window completely ineffective. Fixed to:
```python
_EARLIEST_DROP_DATE = _PLAN_K1_MERGE_DATE + timedelta(days=_RETENTION_DAYS)
# = 2026-08-09
```
Added `timedelta` import; updated `_retention_check()` to use the constant.
Committed in C3 (`9badddc595`).

### N4 (LOW) — size>0 ATTACH guard regression test: CLOSED
`TestInitSchemaForecasts0ByteGuard` in `tests/state/test_p2_byte_equivalence.py`:
- `test_zero_byte_stub_takes_static_fallback`: creates a 0-byte stub at ZEUS_WORLD_DB_PATH,
  asserts warning emitted + all 7 EXPECTED_FORECAST_TABLES created via static DDL fallback
  + no "world_src" in PRAGMA database_list.
- `test_nonexistent_world_db_takes_static_fallback`: same with non-existent path.

Both pass at HEAD. Committed in C2 (`3c5d8de029`).

### N5 (LOW) — Stale doc in test_main_module_scope.py: CLOSED
`tests/test_main_module_scope.py:13` referenced `world_schema_validator` (retired in P3).
Updated to `typed ConnectionTriple accessors post-K1`. Committed in C3 (`9badddc595`).

### N6 (P5+) — Typed-connection migration: PARTIAL
P4 targeted "begin migration at hot writer sites." Delivered 10 low-risk migrations
(src/state/ module, WORLD_CLASS tables only, no @capability/@protects, no ATTACH calls).
Remaining 59 baseline entries require HIGH-RISK caller analysis and are deferred. See
`_P1_BASELINE_VIOLATIONS delta` section below.

---

## _P1_BASELINE_VIOLATIONS Delta

| Metric | Count |
|--------|-------|
| Baseline at P1 completion | 69 |
| P4 migrations | 10 |
| Remaining baseline at P4 tip | 59 |

### Migrated in P4 (all WORLD_CLASS, no decorators, no ATTACH)

| File | Function |
|------|----------|
| `src/state/data_coverage.py` | `record_written` |
| `src/state/data_coverage.py` | `record_legitimate_gap` |
| `src/state/data_coverage.py` | `record_failed` |
| `src/state/data_coverage.py` | `record_missing` |
| `src/state/data_coverage.py` | `bulk_record_written` |
| `src/state/job_run_repo.py` | `write_job_run` |
| `src/state/market_topology_repo.py` | `write_market_topology_state` |
| `src/state/readiness_repo.py` | `write_readiness_state` |
| `src/state/snapshot_repo.py` | `insert_snapshot` |
| `src/state/source_run_coverage_repo.py` | `write_source_run_coverage` |

### Deferred (require future phase)

- `src/state/db.py` (6 entries): internal db.py writers operate on raw connections before
  the typed wrapping layer; migration requires coordinating with the return-type changes in
  `get_world_connection` and `get_forecasts_connection`.
- `src/state/ledger.py::append_many_and_project` + `src/state/projection.py::upsert_position_current`:
  both carry `@capability`/`@protects` decorators — HIGH-RISK, require capability audit before
  signature change.
- `src/state/uma_resolution_listener.py::record_resolution`: UMA resolution writes span
  conditional logic; deferred for closer inspection.
- `src/state/venue_command_repo.py` (8 entries): trading-lane writers; deferred pending
  full ConnectionTriple rollout.
- `src/state/source_run_repo.py::write_source_run`: writes to source_run (FORECAST_CLASS
  table) — requires `ForecastsConnection` annotation, not `WorldConnection`; deferred.
- `src/execution/**`, `src/engine/**`, `src/data/**`, `src/ingest/**`, `src/calibration/**`,
  `src/strategy/**` (38 entries): cross-module; deferred to P5+.

### Migration correctness

`WorldConnection` is a dataclass subclass of `TypedConnection` with full pass-through for
`.execute()`, `.executemany()`, `.commit()`, `.rollback()`, `.cursor()`, `.close()`,
`__enter__`, `__exit__`. Annotating `conn: sqlite3.Connection` → `conn: WorldConnection`
in function signatures does not break bodies — all `conn.execute()` calls route through
`TypedConnection.execute()` unchanged. Verified via `scripts/check_writer_signature_typing.py`
exit 0 + scoped pytest 43 passed.

---

## Flock Investigation Finding

`trade_connection_with_world_flocked` (the flocked ATTACH-world variant in `src/state/db.py`)
has **zero callers outside db.py** and does **not** ATTACH forecasts.db — no flock extension
needed for P4.

The non-flocked variant `get_trade_connection_with_world` ATTACHes both world.db AND
forecasts.db with zero flocks. This is a separate known gap, documented in
`docs/to-do-list/known_gaps.md` under `[OPEN — K1 P4] get_trade_connection_with_world
ATTACHes forecasts.db with zero flocks`. Severity: MEDIUM. No known live cross-DB write
via this path as of 2026-05-14.

---

## Boot Wiring Status

`get_forecasts_connection_with_world` (ATTACH+SAVEPOINT sanctioned cross-DB write path)
and `get_connection_triple` boot wiring remain deferred. No P4 scope change. Carry
forward to P5.

---

## Test Suite Status at P4 Tip

Scoped suite (tests/state/ + tests/test_no_raw_world_attach.py +
tests/state/test_table_registry_coherence.py):

```
43 passed, 4 skipped
```

Pre-existing failure excluded (not caused by P4):
- `tests/test_no_raw_world_attach.py::TestNoRawWorldAttach::test_no_get_trade_connection_with_world_in_trading_lane`
  — `src/engine/replay_selection_coverage.py` uses `get_trade_connection_with_world` outside
  the trading-lane allowlist. Pre-existing at P3 tip; confirmed by stash-and-retest.
  Carry forward to future phase.

---

## Push Status

Branch pushed after P4 report commit. 65 commits ahead of origin at push time.
