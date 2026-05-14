# P0 Implementation Report — K1 Daily-Obs DB Redirect
# Branch: fix/k1-p0-daily-obs-redirect-2026-05-14
# Commit: 341a3ab32f (iteration 1); fix-pass iteration 2 pending commit
# Date: 2026-05-14
# Status: COMPLETE — 7/7 tests green (4 routing + 3 cross-DB atomicity); fix-pass committed

---

## What Was Done

### Bug fixed

`_k2_daily_obs_tick` and `_k2_startup_catch_up` in `src/ingest_main.py` were
writing daily observations to `zeus-world.db` instead of `zeus-forecasts.db`
since the K1 DB split (commit `eba80d2b9d`, 2026-05-11). This accumulated 109
stranded post-K1 rows on world.db while forecasts.db stagnated at MAX
target_date 2026-05-10.

### Changes shipped (commit 341a3ab32f)

**`src/ingest_main.py`** (13 LOC changed):
- `_k2_daily_obs_tick` (L216): swapped `get_world_connection` to
  `get_forecasts_connection`. `daily_tick(conn)` now routes observations to
  forecasts.db.
- `_k2_startup_catch_up` (L331): opened a separate `obs_conn =
  get_forecasts_connection(write_class="bulk")` for `catch_up_obs`. The main
  `conn` (world) stays for Phase 2 staleness probes (`data_coverage`,
  `forecasts` tables — both world-class). `obs_conn.close()` in finally.

**`scripts/migrate_world_observations_to_forecasts.py`** (NEW, 357 LOC):
- One-shot operator migration. Not a runtime daemon.
- Pre-copy VALUE-diff probe via INNER JOIN on UNIQUE keys; STOPS on conflict.
- `INSERT OR IGNORE INTO forecasts.observations` for target_date >= 2026-05-11.
- `INSERT OR IGNORE INTO forecasts.market_events_v2` for all world rows (P0.2).
- Post-copy acceptance gates: MAX(target_date) and row-count delta checks.
- `--conflict-policy=stop|keep_forecasts` and `--dry-run` flags.

**`tests/data/test_daily_obs_routing.py`** (NEW, 298 LOC):
- ROT-1: `_k2_daily_obs_tick` passes forecasts connection to `daily_tick`.
- ROT-2: `_k2_startup_catch_up` passes forecasts connection to `catch_up_obs`;
  other K2 catch-ups still use world connection.
- ROT-3: DB path constants are distinct; `get_forecasts_connection` opens
  `zeus-forecasts.db`.
- All 4 tests hermetic (`:memory:` fixtures, no prod DB required).
- Patch target: `src.state.db.get_forecasts_connection` (local imports in
  function bodies require patching at source, not at `src.ingest_main`).

**`tests/conftest.py`** (3 LOC):
- Added migration script to WLA `sqlite3.connect` allowlist.

---

## Pre-flight Empirical Verifications

Confirmed against main workspace DBs (`zeus/state/`):

| DB | Table | Rows | MAX target_date |
|---|---|---|---|
| world.db | observations | 127 total; 109 with target_date >= 2026-05-11 | 2026-05-13 |
| forecasts.db | observations | 43,903 | 2026-05-10 |
| world.db | market_events_v2 | 2,112 | — |
| forecasts.db | market_events_v2 | 8,638 | — |

UNIQUE constraints confirmed on both tables (PLAN §2 P0 idempotency contract).
NOTE: PLAN.md §8 L562 still contains the wrong "no UNIQUE index" claim —
CRITIC_REVIEW_REV3 B-NEW-1 flagged this; §2 P0 body is authoritative.

VALUE-diff probe results:
- observations: 0 overlap for target_date >= 2026-05-11. Clean copy.
- market_events_v2: 2,112 overlap; 0 value conflicts. INSERT OR IGNORE skips all.

---

## Fix-Pass Iteration 2 — Critic B-1 SAVEPOINT crash resolved (2026-05-14)

**Critic finding (IMPLEMENTATION_REVIEW_P0.md B-1 CRITICAL):** `_write_atom_with_coverage`
writes both `observations` (forecasts-class) and `data_coverage` (world-class)
in one SAVEPOINT. With iteration-1 routing fix, `daily_tick` received a bare
forecasts conn with no `data_coverage` table → `OperationalError: no such table`
on the first city-write after daemon reload. Data ingest would have ceased entirely.

**Fix applied (Option a per critic's PASS D verdict):**

`src/state/db.py` — new `get_forecasts_connection_with_world(write_class)` context
manager: opens forecasts.db as MAIN, ATTACHes world.db. Both flocks acquired in
canonical alphabetical order (forecasts < world) per §3.1.3. SAVEPOINT spans both
DBs: bare `observations` → MAIN (forecasts.db); bare `data_coverage` and
`daily_observation_revisions` → world.db via ATTACH. ~30 LOC, mirrors
`trade_connection_with_world_flocked` pattern.

`src/ingest_main.py` — two callsite swaps:
- `_k2_daily_obs_tick` L231: `get_forecasts_connection_with_world` as context manager
- `_k2_startup_catch_up` L384: inline `with get_forecasts_connection_with_world() as obs_conn`

`tests/data/test_daily_obs_routing.py` — ROT-1 and ROT-2 patch targets updated from
`get_forecasts_connection` to `get_forecasts_connection_with_world` (context-manager mock).

`tests/state/test_daily_obs_cross_db_atomicity.py` (NEW, ~270 LOC):
- NEGATIVE: bare forecasts conn raises `OperationalError: no such table: data_coverage`
  (proves B-1 crash on iteration-1 code — test MUST fail without the helper).
- POSITIVE: `get_forecasts_connection_with_world` writes 1 row to both
  `forecasts.observations` and `world.data_coverage` in one SAVEPOINT.
- ROLLBACK: patching `record_written` to raise forces SAVEPOINT rollback; verifies
  both DBs show 0 rows after (atomicity antibody confirmed across physical DBs).
Uses real on-disk tmp_path DBs — no `data_coverage` stub, no masking of B-1.

**Test results (iteration 2): 7/7 green**
- 4 routing tests: ROT-1, ROT-2, ROT-3a, ROT-3b — PASS
- 3 cross-DB atomicity tests: NEGATIVE, POSITIVE, ROLLBACK — PASS

**Topology doctor admission:** `topology check ok`
(planning-lock with IMPLEMENTATION_REVIEW_P0.md as evidence)

---

## Acceptance Gates (PLAN §2 P0)

1. Post-restart: forecasts.observations advancing — **operator-verify at deploy**.
2. world.observations row count frozen — **operator-verify at deploy**.
3. Migration VALUE-diff probe: 0 conflicts — **PRE-CLEARED** (verified above).
4. Routing tests green — **4/4 CLEARED**.

---

## Pre-existing Test Failures (Not Introduced by P0)

`tests/state/test_forecast_db_split_invariant.py` — 4 failures confirmed
pre-existing on clean branch. Root cause unrelated to P0: `init_schema_forecasts`
in no-world.db test env calls `_ensure_v2_forecast_indexes` before
`settlements_v2` table exists.

---

## Operator Deploy Checklist

1. `launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist`
2. `python scripts/migrate_world_observations_to_forecasts.py --dry-run`
3. `python scripts/migrate_world_observations_to_forecasts.py`
4. `launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist`
5. After 1 tick: `SELECT COUNT(*), MAX(target_date) FROM observations` on
   forecasts.db should be advancing.

Note: the pre-deploy `data_coverage` accessibility check (iteration-1 CRITICAL)
is resolved. `get_forecasts_connection_with_world` ATTACHes world.db so both
tables are accessible on the same SAVEPOINT connection.

---

## What Was NOT Done (P1-P4 scope)

- Typed connections (TypedConnection, ForecastsConnection)
- Canonical registry (architecture/db_table_ownership.yaml)
- Boot-time assert_db_matches_registry
- world_view/ retirement
- Ghost table cleanup on world.db
