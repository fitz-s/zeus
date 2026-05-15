# IMPLEMENTATION_REVIEW_P0 — K1 daily-obs DB redirect (commit 341a3ab32f)

- **Reviewer**: critic agent (Opus 4.7), 2026-05-14
- **Target**: worktree `zeus-k1-p0-2026-05-14`, branch `fix/k1-p0-daily-obs-redirect-2026-05-14`, tip `341a3ab32f`, not yet pushed
- **Diff**: 6 files / +806 LOC vs `origin/main` (`eba80d2b9d`)
- **Mode**: started THOROUGH; **escalated to ADVERSARIAL** after PASS B #1 confirmed the executor-flagged CRITICAL is a real daemon-crash hazard.

---

## PASS A — PLAN conformance

### Mechanical fix vs PLAN §2 P0

- **PLAN spec.** `src/ingest_main.py` `_k2_daily_obs_tick` (L216) AND `_k2_startup_catch_up` (L331, calling `catch_up_obs(conn, days_back=30)` at L380) switch from `get_world_connection` to `get_forecasts_connection`. NEW `scripts/migrate_world_observations_to_forecasts.py` one-shot. NEW `tests/data/test_daily_obs_routing.py`.
- **Implementation.** `src/ingest_main.py` diff (L220-226 + L349-381 + L444): `_k2_daily_obs_tick` switched cleanly; `_k2_startup_catch_up` retains world `conn` (correct — Phase 2 staleness probes touch world-class `forecasts` + `data_coverage`) and opens a separate `obs_conn = get_forecasts_connection(write_class="bulk")` for `catch_up_obs` only. Migration script (357 LOC) implements pre-copy VALUE-diff probe + `INSERT OR IGNORE`. Routing tests (298 LOC) match the PLAN's "pre-P1 sqlite3.connect-path string match endswith('zeus-forecasts.db')" guidance via mock-and-patch.
- **Conformance: PASS.**

### Acceptance criteria

| PLAN §2 P0 Acceptance | Verified |
|---|---|
| 1. Post-restart forecasts.observations advancing, MAX(target_date) >= today | NOT YET verifiable (operator step post-deploy) — gated by daemon reload |
| 2. world.observations row count frozen at post-P0 baseline | NOT YET verifiable (operator step post-deploy) |
| 3. Migration pre-copy probe found ZERO row-VALUE conflicts | PRE-CLEARED per report L70 (`observations: 0 overlap`; `market_events_v2: 2,112 overlap with 0 value conflicts`) |
| 4. New routing test green | **CLEARED** — `pytest tests/data/test_daily_obs_routing.py -q` → `4 passed in 0.25s` |

### Stop-conditions respected
- §7 #6 (P0 row-VALUE conflict on overlap): WIRED in migration script L268-275 with `--conflict-policy stop` default. Pre-cleared per report.
- No stop conditions fired during implementation.

### topology_doctor admission
- Executor brief required admission for this packet; the report does not cite the admission output. **GAP** — not blocking for P0 (this is a hotpatch) but should be confirmed before push.

### PASS A verdict: **PASS with one gap** (topology_doctor admission citation missing from report).

---

## PASS B — Code correctness adversarial

### B-1 CRITICAL — `_write_atom_with_coverage` SAVEPOINT crash hazard CONFIRMED

The executor flagged this as a "CRITICAL latent issue (verify at deploy)" in their report. **Independent verification: the crash is real, will fire on the first daily-obs tick after P0 deploy.**

**Evidence chain (read-only, verified during this review):**

1. `src/data/daily_obs_append.py:550-594` `_write_atom_with_coverage(conn, ...)`:
   - L573 `conn.execute(f"SAVEPOINT {sp}")`
   - L579-582 `write_daily_observation_with_revision(conn, atom_high, atom_low, writer=...)` → writes to `observations`
   - L583-589 `record_written(conn, data_table=DataTable.OBSERVATIONS, ...)` → writes to `data_coverage`
   - L594 `conn.execute(f"RELEASE SAVEPOINT {sp}")`

2. `src/state/data_coverage.py:148-175` `record_written` body is verbatim `INSERT INTO data_coverage (...)` against the same `conn` — no schema-qualified prefix, no ATTACH lookup.

3. `daily_tick(conn)` calls (L1282-1378) propagate `conn` to `append_wu_city`, `_accumulate_hko_reading`, `append_hko_months`, `_finalize_hko_yesterday`, `append_ogimet_city`, every one of which calls `_write_atom_with_coverage(conn, ...)` (callsites at L511, L887, L1021, L1250).

4. Empirical (read-only probe):
   - `state/zeus-world.db sqlite_master WHERE name='data_coverage'` returns 1 row
   - `state/zeus-forecasts.db sqlite_master WHERE name='data_coverage'` returns 0 rows
   - `data_coverage` lives on world.db ONLY

5. `src/state/db.py:166-180` `get_forecasts_connection()` returns a BARE forecasts.db connection. NO ATTACH to world.db.

6. Post-P0 flow at `src/ingest_main.py:226`: `conn = get_forecasts_connection(write_class="bulk")` → `daily_tick(conn)` → propagates to `_write_atom_with_coverage` → `record_written(conn, ...)` → `INSERT INTO data_coverage` → **`sqlite3.OperationalError: no such table: data_coverage`**.

**Severity: CRITICAL.** The first city-write on the first scheduled tick after `launchctl load` will raise. The SAVEPOINT will roll back the `observations` insert; the exception will propagate up; `_k2_daily_obs_tick` has no exception handler beyond `conn.commit()`/`conn.close()` in the `finally`. The ingest daemon will log the exception and the cron will fire again on the next tick. **The result is worse than the pre-P0 state**: pre-P0 wrote to wrong DB (data accumulated on world.db); post-P0 writes to a DB that can't accept the transaction (data accumulates NOWHERE). Net effect: complete cessation of daily observation ingest from the moment of deploy.

The executor identified this correctly in the report (L74-91) but **shipped the code anyway**, deferring resolution to "verify at deploy." This is the wrong default: the verification IS this review's job, and the verification confirms the crash.

Note: The routing tests `tests/data/test_daily_obs_routing.py` STUB `data_coverage` table on the in-memory forecasts conn (L73-84 `_make_forecasts_mem_conn` creates `data_coverage` schema on the in-memory conn) — which is why the tests pass while prod will crash. The tests verify routing (which conn is passed to which writer) but not data_coverage co-location. The stub MASKS the crash.

### B-2 — Migration script uses INSERT OR IGNORE correctly

Verified: `scripts/migrate_world_observations_to_forecasts.py:166-170` uses native `INSERT OR IGNORE INTO forecasts.observations(...) SELECT ... FROM main.observations WHERE target_date >= '2026-05-11'`. UNIQUE(city, target_date, source) is the deduplication key (confirmed: empirical CREATE TABLE shows the constraint; CRITIC_REVIEW_REV2 B1 / REV3 B-NEW-1 demanded native OR IGNORE; executor implemented it).

For `market_events_v2`: L206-209 uses same idiom against UNIQUE(market_slug, condition_id). **SOUND.**

### B-3 — VALUE-diff probe IS implemented

`scripts/migrate_world_observations_to_forecasts.py:87-117` `_obs_value_diff_probe` and L120-146 `_mev2_value_diff_probe` execute INNER JOIN over UNIQUE key and SELECT mismatching-payload rows. Cap at 20 displayed rows. STOP-condition wired via `--conflict-policy stop` default at L268-275. **SOUND.**

### B-4 — catch_up_missing wiring

`src/ingest_main.py:384` `logger.info("  %s", catch_up_obs(obs_conn, days_back=30))` — `obs_conn` is the forecasts conn (L355 `obs_conn = get_forecasts_connection(write_class="bulk")`).

**But:** the executor's `catch_up_obs` also passes through to `_write_atom_with_coverage` per `src/data/daily_obs_append.py:1380+` (catch_up_missing body uses `find_pending_fills(conn, ...)` at L1400 which SELECTs from `data_coverage` — same table absence problem). The boot-time catch-up will fire FIRST after daemon reload (per L356 `@_scheduler_job("ingest_k2_startup_catch_up")` runs on `date` trigger at boot) and crash before the cron-driven `_k2_daily_obs_tick` ever runs.

PLAN §2 P0 "Catch-up window (CRITIC §2.2)" L180 specifies `catch_up_missing(forecasts_conn, days_back=2)` but the executor used `days_back=30` (preserving the pre-existing kwarg). Functionally equivalent for the unload-gap scenario (30 is conservatively larger than 2), but worth noting the PLAN's `days_back=2` recommendation was silently overridden in favor of `days_back=30`. Acceptable — wider coverage is safer.

### B-5 — Sibling rerouted-function callers

Grep for `daily_tick` callers (`grep -rn "daily_tick" src/`):
- `src/ingest_main.py:226, 384` (covered by P0)
- `src/data/daily_obs_append.py:1282` (definition)
- No other production callers found.

Sibling functions `_k2_solar_daily_tick` (L257), `_k2_hourly_instants_tick` (L236), `_k2_forecasts_daily_tick` (L278) — per INVESTIGATION §2.2 these write to world-class tables (`solar_daily`, `observation_instants`, `forecasts`) and correctly remain on world conn. Not in P0 scope. **SOUND.**

### PASS B verdict: **1 CRITICAL defect** (B-1 daemon crash on first post-P0 tick).

---

## PASS C — Test coverage

### Pre-existing failures, P0-introduced?

`tests/state/test_forecast_db_split_invariant.py` — 4 failures confirmed pre-existing on this branch:
- `test_rel1_init_schema_forecasts_tables_and_version`
- `test_rel1_init_schema_forecasts_critical_indexes`
- `test_rel6_trio_atomicity_rollback`
- `test_rel6_trio_atomicity_commit`

Root cause: `src/state/db.py:2527 _ensure_v2_forecast_indexes` runs `CREATE INDEX IF NOT EXISTS idx_settlements_v2_city_date_metric ON settlements_v2(...)` against a `:memory:` conn that has not yet had `settlements_v2` table created (the ATTACH-from-world fallback path failed to copy the table because no on-disk `world.db` exists in the test env). This is the unintended consequence of PR #114 `_ensure_v2_forecast_indexes` running unconditionally after the table-creation branch. **Pre-existing**, NOT introduced by this P0 commit (verified by checkout-out main and reproducing the same failures).

### P0-added test coverage (relationship tests per Fitz #3)

3 test classes / 4 test functions in `tests/data/test_daily_obs_routing.py`:
- ROT-1 `test_daily_obs_tick_uses_forecasts_connection` — routing test (relationship: which DB conn flows into daily_tick)
- ROT-2 `test_catch_up_obs_uses_forecasts_connection` — routing test (relationship: which DB conn flows into catch_up_obs, while sibling probes keep world conn)
- ROT-3a `test_forecasts_db_path_distinct_from_world_db_path` — sanity (DB path constants)
- ROT-3b `test_get_forecasts_connection_returns_forecasts_path` — sanity (`PRAGMA database_list` ends in zeus-forecasts.db)

**Coverage check (do ROT-1 / ROT-2 fail on the OLD code path?):**
- ROT-1 asserts `mock_daily_tick.call_args[0][0] is forecasts_conn`. On pre-P0 code (`conn = get_world_connection(...)`), `daily_tick` would receive `world_conn`. The assertion would fail. **Test correctly distinguishes pre-/post-P0.**
- ROT-2 asserts `mock_catch_up_obs.call_args[0][0] is forecasts_conn`. On pre-P0 code, catch_up_obs receives `conn` (the world conn). The assertion would fail. **Test correctly distinguishes pre-/post-P0.**

**Gap: ROT tests use stub conn that has `data_coverage` schema** (`_make_forecasts_mem_conn` L73-84). This MASKS the B-1 crash — production conn does NOT have data_coverage. A "post-P0 deploy smoke test" against a real forecasts.db file would have caught B-1 and is missing from the P0 scope.

### PASS C verdict: **PASS with one gap** (no end-to-end smoke test against real forecasts.db; ROT stubs hide B-1).

---

## PASS D — data_coverage decision adjudication

**Question.** Fix in P0 (Option a)? Punt to P1 (Option b)? Hot-mitigate (Option c)?

**Empirical answer: B-1 crash WILL fire on first post-P0 tick. Therefore Option b is unsafe.**

**Option (b) safety condition** ("if `data_coverage` is never updated in the same SAVEPOINT today, Option b is safe"): the answer is the SAVEPOINT updates `data_coverage` on EVERY observation write — every single WU / HKO / Ogimet daily_tick invocation. Option (b) is fundamentally unsafe.

**Option (a) — fix in P0 with `get_forecasts_connection_with_world()`.** This means: extend `src/state/db.py` with a new helper that opens forecasts.db as MAIN and ATTACHes world.db at canonical lock order. The SAVEPOINT can then span both DBs (verified by executor in report L88: "SQLite SAVEPOINT spans ATTACHed DBs"). Bare `data_coverage` reference resolves to world.db via ATTACH lookup; bare `observations` reference resolves to MAIN forecasts.db. This is the same pattern PLAN §1.3 sketches for ConnectionTriple but pre-figured here.

Cost: ~30 LOC for the new helper + 2 callsite changes (`_k2_daily_obs_tick` L226 + `obs_conn` L355) + flock acquisition (must acquire both `zeus-forecasts.db.writer-lock.bulk` AND `zeus-world.db.writer-lock.bulk` since the SAVEPOINT writes to both; PLAN §1.2 and CRITIC_REVIEW_REV2 B-NEW flock-fix). Tests: 1-2 additional end-to-end smoke tests using a real on-disk `:tmp_path:`-backed forecasts.db + world.db pair.

**Option (c) — punt with hot-mitigation in P0.** Split the SAVEPOINT: observations go to forecasts conn; `record_written` (data_coverage write) goes through a separately-opened world conn. This breaks the atomicity contract that `_write_atom_with_coverage` was designed to enforce (per L562-565 docstring: "The savepoint guarantees that either both land or neither does, per row"). Reintroduces the S1 hazard the SAVEPOINT was designed to fix. **Rejected** — solves the wrong problem.

**Decision: Option (a) is the only acceptable choice for push readiness.**

Justification:
1. Option (b) is empirically unsafe — B-1 crashes the daemon.
2. Option (c) sacrifices the SAVEPOINT atomicity antibody.
3. Option (a) preserves atomicity, restores routing correctness, and pre-figures the P1 ConnectionTriple shape with a minimal helper.
4. Option (a) cost (~30 LOC + tests) is well within P0's scope.
5. The executor identified the issue and proposed Option (a) (report L84-87) but did not implement it. The push is one helper-function away from sound.

---

## PASS E — Push readiness verdict

**Verdict: REJECT — REQUIRES ONE MORE EXECUTOR PASS.**

The implementation as-shipped (commit `341a3ab32f`) will crash the ingest daemon on the first post-reload daily-obs tick. The crash is reproducible by reasoning from code paths verified during this review (`daily_obs_append.py:550-594` + `data_coverage.py:148-175` + empirical `sqlite_master` absence of `data_coverage` on forecasts.db). Routing tests pass because they stub `data_coverage` in-memory; production has no such stub.

**Required fix before push (Option a):**
1. Add `get_forecasts_connection_with_world(write_class="bulk")` to `src/state/db.py` — opens forecasts.db as MAIN, ATTACHes world.db at `ZEUS_WORLD_DB_PATH`, acquires flocks on BOTH DBs in canonical alphabetical order (`zeus-forecasts.db.writer-lock.bulk` first, then `zeus-world.db.writer-lock.bulk`).
2. `src/ingest_main.py:226` swap `get_forecasts_connection` → `get_forecasts_connection_with_world`.
3. `src/ingest_main.py:355` swap `get_forecasts_connection` → `get_forecasts_connection_with_world` for `obs_conn`.
4. Add one new test: `tests/data/test_daily_obs_routing.py::test_daily_obs_savepoint_atomicity_across_dbs` — open a real tmp-path forecasts.db + world.db pair, run `_k2_daily_obs_tick` against a stub city, assert one row landed on forecasts.observations AND one row landed on world.data_coverage in the same SAVEPOINT. This is the relationship test per Fitz #3 that the stub-based ROT-1 / ROT-2 do not cover.
5. Update the deploy runbook (P0_IMPLEMENTATION_REPORT.md L112-122) to remove the "verify at deploy" CRITICAL caveat — the helper resolves it pre-deploy.

**Severity calibration**: B-1 is HIGH for code review purposes but is operationally CRITICAL because the daemon would silently stop ingesting daily observations from the moment of P0 deploy. The pre-P0 data-loss bug accumulates wrong-DB rows at a rate of ~46 cities × 1 tick/day. The post-P0 bug accumulates ZERO rows on EITHER DB. Net data-loss rate is WORSE after the unfixed P0 ships than before.

**Realist check applied.** Could B-1 be downgraded? No: (a) the call path is the per-tick happy path, not an edge case; (b) detection is fast (one tick after deploy, ~minutes) but the daemon won't self-heal; (c) mitigation requires operator intervention (revert or push the helper). The blast radius is "no observation ingest until operator notices and acts." Severity stays HIGH.

**Bonus low-severity notes (do not block push by themselves, but worth addressing in the same fix-pass):**

- N1 (MINOR): `tests/data/test_daily_obs_routing.py:73` stubs `data_coverage` on the in-memory forecasts conn for test convenience. This is the exact pattern that hid B-1 from the test suite. After Option (a) lands, this stub should be REMOVED and the test redirected to assert against an ATTACHed real conn pair.
- N2 (MINOR): `scripts/migrate_world_observations_to_forecasts.py:33-34` ROLLBACK section says "rollback not needed. The world.db rows are preserved (not deleted)." After P3's ghost-table drop, this comment becomes misleading. Add a 90-day forward-ref note: "post-P3, world.observations is dropped; rollback semantics change at that point."
- N3 (MINOR): The new migration script is in `scripts/` WLA allowlist (conftest.py:267) which is correct, but the entry comment says "operator-mediated" — should also mention it requires daemon-quiesce per PLAN §4.1 (and the script's own header L20-31 already does).

---

## Summary table

| Pass | Result |
|---|---|
| A — PLAN conformance | PASS (1 minor gap: topology_doctor admission not cited) |
| B — Code correctness | **1 CRITICAL** (B-1 data_coverage SAVEPOINT crash) + 4 SOUND |
| C — Test coverage | PASS (1 gap: ROT tests stub data_coverage, hide B-1) |
| D — data_coverage decision | **Option (a)** — fix in P0 via `get_forecasts_connection_with_world` helper |
| E — Push readiness | **REJECT** — fix B-1 before push |

