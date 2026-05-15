# IMPLEMENTATION_REVIEW_P0_FINAL — K1 daily-obs DB redirect (commit 2ebd8965ef)

- **Reviewer**: critic agent (Opus 4.7), 2026-05-14
- **Target**: worktree `zeus-k1-p0-2026-05-14`, branch `fix/k1-p0-daily-obs-redirect-2026-05-14`, tip `2ebd8965ef` (was `341a3ab32f` at prior review)
- **Fix delta**: 6 files / +675 LOC vs prior tip
- **Mode**: THOROUGH (no triggering HIGH defects; no ADVERSARIAL escalation needed)
- **Anchor**: prior `IMPLEMENTATION_REVIEW_P0.md` flagged B-1 CRITICAL (data_coverage SAVEPOINT crash) and prescribed Option (a) — new `get_forecasts_connection_with_world()` helper + 2 callsite swaps + cross-DB atomicity smoke test.

---

## Check 1 — Helper existence and shape

`src/state/db.py:180-239` adds `@contextlib.contextmanager def get_forecasts_connection_with_world(*, write_class="bulk")`. Cross-comparison against `trade_connection_with_world_flocked` (`src/state/db.py:237-292` on origin/main, the canonical reference pattern):

| Property | `trade_connection_with_world_flocked` (reference) | `get_forecasts_connection_with_world` (new) | Status |
|---|---|---|---|
| Canonical flock order | `canonical_lock_order([_zeus_trade_db_path(), ZEUS_WORLD_DB_PATH])` | `canonical_lock_order([ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH])` at L211-213 | SOUND — alphabetical (`zeus-forecasts.db` < `zeus-world.db`) |
| Nested `db_writer_lock` acquisition | sequential `with db_writer_lock(...)` per path | L214-215 nested `with db_writer_lock(ordered_paths[0], resolved): with db_writer_lock(ordered_paths[1], resolved):` | SOUND |
| ATTACH guard against double-ATTACH | yes (checks `PRAGMA database_list`) | L217-225 same idiom: skip ATTACH if `"world" in attached` | SOUND |
| ATTACH statement | `conn.execute("ATTACH DATABASE ? AS world", (...))` parameterized | L222-225 same shape, parameterized via `(str(ZEUS_WORLD_DB_PATH),)` | SOUND |
| Cleanup / close in `finally` | yes | L227-231 `try: ... finally: try: conn.close() except Exception: pass` | SOUND |
| Context-manager contract | yes (generator-based) | yes via `@contextlib.contextmanager` decorator | SOUND |
| Write-class default | resolves via `_resolve_write_class` | L206-209 same: defaults to BULK when `None` returned | SOUND |
| DETACH on exit | NO (relies on conn.close() reclaiming the ATTACH) | NO (same pattern) | SOUND (matches reference) |
| Idempotency | re-callable; re-ATTACH guarded | re-callable; re-ATTACH guarded | SOUND |

**Helper correctness verdict: APPROVE.** The new helper mirrors `trade_connection_with_world_flocked` faithfully in every load-bearing respect (canonical lock order, ATTACH idempotency, finally-close). One small structural difference: the reference uses a generator with explicit yield; this new helper uses `@contextlib.contextmanager` decorator — semantically equivalent. No flock-order risk: alphabetical sort places `zeus-forecasts.db` BEFORE `zeus-world.db`, consistent with `CROSS_DB_CANONICAL_ORDER` per `src/state/db_writer_lock.py:609-614`.

---

## Check 2 — Callsite swap

`git diff 341a3ab32f..2ebd8965ef -- src/ingest_main.py` shows:

- **L222-226 `_k2_daily_obs_tick`**: `get_forecasts_connection` import removed; replaced with `get_forecasts_connection_with_world` import. Body changed from `conn = get_forecasts_connection(write_class="bulk"); try: ... finally: conn.close()` to `with get_forecasts_connection_with_world(write_class="bulk") as conn: result = daily_tick(conn)`. The `finally: conn.close()` is removed (helper handles it). **SOUND** — context-manager idiom.
- **L380-385 `_k2_startup_catch_up`**: `obs_conn` separate-conn pattern replaced. Old: `obs_conn = get_forecasts_connection(write_class="bulk"); ...; catch_up_obs(obs_conn, days_back=30); ... obs_conn.close()`. New: `with get_forecasts_connection_with_world(write_class="bulk") as obs_conn: catch_up_obs(obs_conn, days_back=30)`. Scoped narrowly to the catch_up_obs call only — the surrounding Phase 2 staleness probes still use the world `conn` (which is correct because they query world-class tables). **SOUND**.

**Plain-`get_forecasts_connection` callsite sweep**: 
- `grep -n "get_forecasts_connection(" src/` (independent of `_with_world`): residual callers exist in other parts of the codebase, but the ONLY callsites that flow into a cross-DB SAVEPOINT path (`_write_atom_with_coverage` via `daily_tick` or `catch_up_missing`) are the two in `src/ingest_main.py` and both are now swapped.
- The migration script (`scripts/migrate_world_observations_to_forecasts.py:259-260`) opens its own `sqlite3.connect(world_path)` and ATTACHes forecasts; it never touches `_write_atom_with_coverage`. Safe.

**Callsite swap verdict: APPROVE.** Both B-1 originating callsites now use the helper. Zero remaining plain-conn callsites in the daily-obs SAVEPOINT path.

---

## Check 3 — Cross-DB atomicity smoke test

`tests/state/test_daily_obs_cross_db_atomicity.py` (NEW, 268 LOC) defines three tests in `TestCrossDbSavepointAtomicity`:

### Test fixture `dual_db` (L37-74)
Builds REAL on-disk DBs at `tmp_path / "zeus-forecasts.db"` and `tmp_path / "zeus-world.db"` via `init_schema(wc)` + `init_schema_forecasts(fc)`. Path constants `ZEUS_WORLD_DB_PATH` / `ZEUS_FORECASTS_DB_PATH` are temporarily redirected during fixture setup so the ATTACH-from-world replication path in `init_schema_forecasts` works against the tmp DBs. No stubbed `data_coverage` — the real schema is built. **SOUND.**

### Test 1 — `test_negative_bare_forecasts_conn_crashes_on_data_coverage` (L143-163)
Opens a bare `sqlite3.connect(forecasts_path)` (NOT the new helper), calls `_write_atom_with_coverage(conn, high, low, data_source="WU")`, asserts `pytest.raises(sqlite3.OperationalError, match="no such table")`. This is **the antibody-proof test** — it pins the B-1 crash empirically. If a future regression accidentally removes the helper, this test will fail (the bare conn will no longer crash if a future engineer adds data_coverage to forecasts.db, or it WILL crash if the helper is bypassed — both correctly fail-loud).

**Critical antibody-proof check**: would this test have FAILED on the prior `341a3ab32f` code path? **No — and that is the correct semantics.** On `341a3ab32f`, this test would have PASSED (because the bare conn WOULD crash on data_coverage exactly as the test asserts). The antibody-proof here is INVERTED: the test asserts the crash STILL HAPPENS on a bare conn. The positive-path test (Test 2 below) is the one that distinguishes pre/post-fix.

### Test 2 — `test_positive_attach_conn_writes_both_dbs` (L165-209)
Calls `get_forecasts_connection_with_world(write_class="bulk")` as a context manager, invokes `_write_atom_with_coverage(conn, high, low, data_source="WU")`, commits. Then opens fresh independent connections to forecasts.db and world.db and verifies:
- L193-199: `forecasts.observations` has 1 row for `'TestCity'` 
- L202-209: `world.data_coverage` has 1 row for `'TestCity'`

**Critical antibody-proof check**: would this test FAIL on the prior `341a3ab32f` code path? **YES** — `get_forecasts_connection_with_world` did not exist at `341a3ab32f` (the helper was added in this very commit `2ebd8965ef`). The import at L183 (`from src.state.db import get_forecasts_connection_with_world`) would `ImportError`. The test correctly catches the fix.

Even stronger: if a future engineer reverted the helper but kept the test, the test would fail at import time — the antibody is structural.

### Test 3 — `test_savepoint_rollback_undoes_both_dbs` (L211-268)
Patches `src.data.daily_obs_append.record_written` with `side_effect=RuntimeError("forced rollback")`, expects `pytest.raises(RuntimeError)` inside `_write_atom_with_coverage`, then verifies BOTH forecasts.observations AND world.data_coverage have ZERO rows for the test city. This validates the SAVEPOINT atomicity contract that `_write_atom_with_coverage`'s docstring (L562-565) advertises ("savepoint guarantees that either both land or neither does, per row"). 

**Critical antibody-proof check**: SQLite SAVEPOINT semantics across ATTACHed DBs are explicitly tested. If a future engineer accidentally split the SAVEPOINT (Option c in the prior review, which I rejected), this test would fail because one of the two DBs would have a row even after rollback.

**Use of real init_schema**: the `dual_db` fixture L60-69 runs `init_schema(wc)` then `init_schema_forecasts(fc)` — the REAL functions. Not stubbed. This is what the prior review (Pass C gap) explicitly demanded.

**Atomicity test correctness verdict: APPROVE.** The three tests cover negative (bare-conn crash empirically pins B-1), positive (helper makes both writes atomic across DBs), and rollback (SAVEPOINT undoes both). All use real on-disk DBs with real `init_schema_*` calls. The positive-path test (Test 2) would fail on `341a3ab32f` because the helper did not exist — this is the proper antibody-proof: the test catches the bug class it is designed to catch.

---

## Check 4 — Regression sweep

Executed `python -m pytest tests/state/ tests/test_no_raw_world_attach.py` against the worktree (rootdir `zeus-k1-p0-2026-05-14`):

```
30 tests collected
20 passed, 4 skipped, 6 failed in 1.32s
```

Failure breakdown:

| Failure | File:test | Pre-existing on origin/main? | P0-introduced? |
|---|---|---|---|
| 1 | `test_forecast_db_split_invariant.py::test_rel1_init_schema_forecasts_tables_and_version` | YES — confirmed in prior `IMPLEMENTATION_REVIEW_P0.md` Pass C | NO |
| 2 | `test_forecast_db_split_invariant.py::test_rel1_init_schema_forecasts_critical_indexes` | YES — confirmed in prior review | NO |
| 3 | `test_forecast_db_split_invariant.py::test_rel6_trio_atomicity_rollback` | YES — confirmed in prior review | NO |
| 4 | `test_forecast_db_split_invariant.py::test_rel6_trio_atomicity_commit` | YES — confirmed in prior review | NO |
| 5 | `test_schema_current_invariant.py::test_rel1_no_hot_path_init_schema` | **VERIFIED YES**: `git show origin/main:src/ingest/forecast_live_daemon.py` lines 265-270 show `init_schema(conn)` callsite intact on origin/main. P0 did not modify this file (verified via `git diff origin/main..HEAD --stat`). | NO |
| 6 | `test_no_raw_world_attach.py::TestNoRawWorldAttach::test_no_get_trade_connection_with_world_in_trading_lane` | **VERIFIED YES**: `git show origin/main:src/engine/replay_selection_coverage.py` returns `get_trade_connection_with_world` at L55, L487 on origin/main. P0 did not modify this file. | NO |

All 6 failures are pre-existing on origin/main. The 4 `test_forecast_db_split_invariant.py` failures were called out by the executor's P0 report L103-108 and the prior critic review Pass C. Failures #5 and #6 surface from the broader `tests/state/` + `tests/test_no_raw_world_attach.py` sweep — these are governance/antibody tests that fire on existing K1-debt code paths in `src/ingest/forecast_live_daemon.py` and `src/engine/replay_selection_coverage.py`. Neither file was touched by P0; neither failure is P0-introduced.

Executor's report L5 claims "7/7 routing tests green" — the 7 are: 4 atomicity smoke tests (3 in `test_daily_obs_cross_db_atomicity.py` + 4 in `test_daily_obs_routing.py`). Wait, that's actually 3 + 4 = 7. Confirmed via direct run:

```
test_daily_obs_cross_db_atomicity.py::test_negative_bare_forecasts_conn_crashes_on_data_coverage PASSED
test_daily_obs_cross_db_atomicity.py::test_positive_attach_conn_writes_both_dbs PASSED
test_daily_obs_cross_db_atomicity.py::test_savepoint_rollback_undoes_both_dbs PASSED
test_daily_obs_routing.py::TestDailyObsTickRouting::test_daily_obs_tick_uses_forecasts_connection PASSED
test_daily_obs_routing.py::TestStartupCatchUpRouting::test_catch_up_obs_uses_forecasts_connection PASSED
test_daily_obs_routing.py::TestDBPathSanity::test_forecasts_db_path_distinct_from_world_db_path PASSED
test_daily_obs_routing.py::TestDBPathSanity::test_get_forecasts_connection_returns_forecasts_path PASSED
7 passed in 0.55s
```

These are the right 7. The 3 atomicity tests are the new ones; the 4 routing tests are updated for the helper context-manager idiom (mock now wraps the context manager via `__enter__`/`__exit__` per L161-165 diff).

**Regression sweep verdict: APPROVE.** 20 K1-related tests pass, 4 skipped, 6 fail — all 6 failures pre-existing and unrelated to P0.

---

## B-1 closure check

The prior review's B-1 finding was:

> Post-P0 flow at `src/ingest_main.py:226`: `conn = get_forecasts_connection(write_class="bulk")` → `daily_tick(conn)` → propagates to `_write_atom_with_coverage` → `record_written(conn, ...)` → `INSERT INTO data_coverage` → `sqlite3.OperationalError: no such table: data_coverage`.

REV-tip `2ebd8965ef` resolution:
- `src/ingest_main.py:225` now uses `with get_forecasts_connection_with_world(write_class="bulk") as conn:` — the helper ATTACHes world.db so `data_coverage` resolves via the `world` schema name in ATTACH.
- `src/data/daily_obs_append.py:550-594 _write_atom_with_coverage` is UNCHANGED, but `record_written(conn, ...)` now has a conn whose ATTACH list includes `world`. Bare `data_coverage` in the INSERT (`src/state/data_coverage.py:148`) will resolve to `world.data_coverage` via SQLite's schema-search-order (MAIN first, then ATTACHed in order). Since `data_coverage` does not exist on MAIN (forecasts.db), it resolves to the ATTACHed `world.data_coverage`. **SAVEPOINT spans both DBs.**
- The new `test_positive_attach_conn_writes_both_dbs` test EMPIRICALLY confirms the SAVEPOINT lands rows on both DBs (one observation row on forecasts.db, one data_coverage row on world.db) in a single transaction.

**B-1 closure verdict: CLOSED.** The crash is empirically prevented; the SAVEPOINT atomicity contract is preserved across DBs.

---

## Final verdict

**APPROVE_FINAL.**

P0 phase complete. Branch `fix/k1-p0-daily-obs-redirect-2026-05-14` at tip `2ebd8965ef` is ready for PR open. The B-1 CRITICAL flagged in the prior review is empirically closed via:

1. New `get_forecasts_connection_with_world()` helper mirroring the canonical `trade_connection_with_world_flocked` pattern (canonical flock order, ATTACH idempotency, finally-close).
2. Both daily-obs SAVEPOINT callsites swapped to the helper (`_k2_daily_obs_tick` and `_k2_startup_catch_up`).
3. Three new atomicity tests using REAL on-disk DBs with real `init_schema_*` calls — covering negative crash, positive cross-DB write, and rollback.
4. Routing tests updated for the helper's context-manager idiom.
5. Regression sweep: 20 K1-related tests pass; 6 pre-existing failures verified unrelated to P0.

**Push-readiness criteria met:**
- B-1 crash structurally prevented (not just patched)
- Antibody test pins the fix (Test 2 fails on `341a3ab32f` via ImportError; Test 1 pins the bare-conn crash semantic for future regression detection; Test 3 verifies SAVEPOINT atomicity across ATTACHed DBs)
- No new test failures introduced
- Helper shape matches the established cross-DB pattern

**Open follow-ups (do NOT block P0; address in P1 or later):**
- N1 (LOW): the 4 pre-existing `test_forecast_db_split_invariant.py` failures around `_ensure_v2_forecast_indexes` running against `:memory:` without `settlements_v2` are PR #114 byproducts; P2 byte-equivalence fixture work will likely surface them as part of the executescript decomposition baseline.
- N2 (LOW): `test_schema_current_invariant.py::test_rel1_no_hot_path_init_schema` and `test_no_raw_world_attach.py::test_no_get_trade_connection_with_world_in_trading_lane` are pre-existing governance-test failures pointing to `src/ingest/forecast_live_daemon.py:267` and `src/engine/replay_selection_coverage.py:[55, 487]`. These are K1 debt — properly fall into P3 caller migration scope.
- N3 (LOW): The `--conflict-policy` flag on `scripts/migrate_world_observations_to_forecasts.py:242-251` defaults to `stop`. Worth documenting in the deploy runbook that the migration script is operator-mediated and not invoked by any daemon, so the default `stop` will block deploy if VALUE-diff conflicts arise. (Executor already covered this in `P0_IMPLEMENTATION_REPORT.md` deploy checklist.)
- N4 (MINOR): The new helper at `src/state/db.py:180-239` does not have a corresponding `architecture/source_rationale.yaml` companion-admit entry. Strictly P0-followup; AGENTS.md §4 planning lock is satisfied because P0 is a hot-patch (active-data-loss fix per `feedback_live_alpha_overrides_legacy_design`). P1 should add the registry companion-admit for the helper.

