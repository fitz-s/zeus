# T1E Critic Review (10-ATTACK)

Reviewer: critic (subagent `a03538fb0b5f999ed`), 2026-05-05.
HEAD at review: `72e58e3a` (T1C closeout commit). T1E changes in worktree, not yet committed.
Phase contract: `phases/T1E/phase.json` (5 asserted invariants, K0 STATE TRUTH).
Phase scope: `phases/T1E/scope.yaml` (cycle_runner.py is OUT_OF_SCOPE — see Deviation 1).

## Phase context

- Tracked diff: `architecture/script_manifest.yaml` (+1/-1), `architecture/test_topology.yaml` (+39/-0), `scripts/rebuild_calibration_pairs_v2.py` (+138/-101), `src/state/db.py` (+62/-3). Untracked: `tests/test_sqlite_busy_timeout.py`, `tests/test_rebuild_live_sentinel.py` (in_scope).
- Three substantive deviations from executor: connect_or_degrade-not-yet-wired (Fitz #4), `_check_live_sentinel` test-importability wrapper, `_skip_commit` parameter removed.
- Independent reproduction: `python -m pytest -q tests/test_sqlite_busy_timeout.py tests/test_rebuild_live_sentinel.py` returns `17 passed in 1.76s`.
- Cross-phase invariants intact: T1A merge surface byte-identical (db.py:1456-1458 unchanged); T1F (`git diff src/venue/polymarket_v2_adapter.py` empty); T1BD (`git diff src/state/chain_reconciliation.py src/state/portfolio.py` empty); T1C (`git diff src/execution/harvester.py` empty).

## Deviation verdicts

### Deviation 1 — connect_or_degrade unwired (Fitz #4)

**verdict: APPROVE_WITH_TEST**

Evidence:
- Sub-question (a): T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH text says "the live cycle path catches the error, increments db_write_lock_timeout_total, logs a structured ALERT-eligible event, and continues for that cycle in read-only mode (no write side effects). The daemon does NOT crash." The invariant scopes to "the live cycle path".
- Sub-question (b): The live cycle write path is rooted at `src/main.py:32` (`from src.engine.cycle_runner import run_cycle`) → `src/main.py:87` (`summary = run_cycle(mode)`) → `src/engine/cycle_runner.py:run_cycle(...)`. `cycle_runner.py` is explicitly OUT_OF_SCOPE per `phases/T1E/scope.yaml:45` (`src/engine/**`). `main.py` is also out_of_scope (no entry in in_scope, default-deny). T1E cannot wire the call site without scope expansion.
- Sub-question (c): `tests/test_sqlite_busy_timeout.py:171-216` — `test_db_write_timeout_does_not_crash_daemon` calls `connect_or_degrade(db_path)` directly with a `patch("src.state.db.sqlite3.connect", side_effect=locked_exc)` and asserts `result is None` and the counter log emit. This tests the PRIMITIVE in isolation, NOT the daemon cycle. The test name is misleading — it does not import `run_cycle` nor exercise any daemon path. The invariant is satisfied at the primitive level (the helper exists, returns None on lock, emits the counter), but the WIRING from cycle_runner is deferred.
- Sub-question (d): This is structurally identical to T1C C-2 (`enqueue_redeem_command` defined but unused, with the inline `request_redeem` call still living in `_settle_positions`). Per `~/.claude/CLAUDE.md` Fitz #4: "Make the category impossible, not just the instance." The primitive is shipped but the category ("daemon crashes on database-locked") is NOT yet impossible because the cycle path doesn't call the primitive. However, T1E `consumed_invariants` includes `T0-DB-ISOLATION-PULL-FORWARD-T2G` (line 57 of phase.json), meaning the operator and planner already acknowledged that T2G is the successor that completes the wiring. The successor_constraint at phase.json line 87 says "T2G (DB physical isolation) is gated by T1E telemetry stability." So the primitive-only landing is INTENDED to hand off to T2G. Severity LOW-MEDIUM.
- VERDICT: APPROVE_WITH_TEST. The PRIMITIVE deviation is acceptable per T1E scope.yaml + T2G dependency contract. However: the executor MUST add an explicit caveat marker in execution_result.md and the test name `test_db_write_timeout_does_not_crash_daemon` should ideally be renamed `test_connect_or_degrade_returns_none_on_lock` to avoid the aspirational claim. Coordinator may opt to defer the rename to T2G to avoid touching tests/ again. Either way: T1E close MUST explicitly carry-forward the wiring requirement to T2G in the invariants ledger so it is not forgotten.

### Deviation 2 — `_check_live_sentinel` wrapper

**verdict: APPROVE**

Evidence:
- `scripts/rebuild_calibration_pairs_v2.py:55-77`. The function is defined at lines 60-74 and INVOKED UNCONDITIONALLY at line 76 (`_check_live_sentinel()`) at module-load time, BEFORE the `import numpy as np` at line 78 and BEFORE any DB connection helper definition. Reading the diff hunk: `_SENTINEL_PATH = Path(__file__).parent.parent / ".zeus" / "rebuild_lock.do_not_run_during_live"` then function def, then bare invocation `_check_live_sentinel()`.
- The wrapper-not-bare-statement form is purely for test importability: tests can `monkeypatch` the function before reimport (see `tests/test_rebuild_live_sentinel.py:86 test_rebuild_refuses_during_live_subprocess` which uses subprocess to test the production path AND `_check_live_sentinel()` patching for in-process tests). Production `python scripts/rebuild_calibration_pairs_v2.py` execution still triggers the check at the very top of module load.
- T1E-REBUILD-SENTINEL-REFUSES invariant text ("refusal happens BEFORE any DB connection is opened") is satisfied — the check is at module load, well before sqlite3.connect anywhere in the module. APPROVE.

### Deviation 3 — `_skip_commit` parameter removal

**verdict: APPROVE**

Evidence:
- `git grep -n "_skip_commit" scripts/ src/ tests/` returns ZERO post-edit matches (verified by reproduction). The only pre-edit caller was `rebuild_all_v2` itself (line ~613 pre-edit, passing `_skip_commit=True` to `rebuild_v2`). Both have been refactored: `rebuild_all_v2` no longer wraps an outer SAVEPOINT (committed-per-bucket discipline), and `rebuild_v2` no longer accepts the parameter. Diff shows clean removal of both the parameter declaration (line 449) and the inner conditional `if not _skip_commit: conn.commit()` (was inside the SAVEPOINT block).
- The architectural shift is correct under T1E-REBUILD-TRANSACTION-SHARDED: each (city, metric) bucket commits independently, so a parent-level skip-commit signal is no longer meaningful. APPROVE.

## Attack table

| # | Attack | Verdict | Evidence (file:line) |
|---|--------|---------|---------------------|
| 1 | Cite-rot + line drift | PASS | Planner cited `src/state/db.py:40` (in `_connect`) and `:349` (in `get_connection`). Post-T1E: `_connect` body `sqlite3.connect(...)` at line 62; `get_connection` body `sqlite3.connect(...)` at line 408. Drift: +22 (T1A added ~62 lines via SETTLEMENT_COMMAND_SCHEMA delegation block earlier in module; T1E added ~30 lines via `_db_busy_timeout_s` + `_handle_db_write_lock` + `connect_or_degrade`). Both connect statements present, both use `timeout=timeout_s` from `_db_busy_timeout_s()`. Content matches; line drift documented. T1E-BUSY-TIMEOUT-CONFIGURABLE invariant text says "line 40" and "line 349" — these are stale at writing time but preserved by content. |
| 2 | C1 ms→s conversion (CRITICAL) | PASS | `src/state/db.py:31-48` defines `_db_busy_timeout_s()`. Line 41: `raw = os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")`. Line 43: `return float(raw) / 1000.0`. Default `"30000"` → 30.0 seconds (correct). `"5000"` → 5.0 seconds (matches T1E-ENV-OVERRIDE-WIRED test expectation). The conversion is `/ 1000.0` (FLOAT division — also correct: `"30500"` → 30.5s, not silently truncated to 30). Critical-severity attack PASSES; daemon will not have an 8-hour timeout. Test `tests/test_sqlite_busy_timeout.py` reproduces 30.0 default + 5.0 override — both pass. |
| 3 | Malformed env handling | PASS | `try: return float(raw) / 1000.0; except (ValueError, TypeError): logger.warning(...); return 30.0`. Test cases — empty string `""`: `float("")` raises ValueError → fallback 30.0 PASS. `"abc"`: ValueError → fallback PASS. `"30.5"`: `float("30.5") / 1000.0 = 0.0305`s — accepted (this is correct: env var is in MS, so `30.5` ms → `0.0305` s; no fallback needed). Negative number `"-1000"`: `float("-1000") / 1000.0 = -1.0`s — sqlite3.connect would raise on negative timeout, but `_db_busy_timeout_s` itself does not guard. **Caveat C-1 LOW: negative env values are not validated** and would produce a negative timeout passed to sqlite3.connect, raising `sqlite3.OperationalError` or similar. Realistic worst case: operator typo → daemon errors at first connect with a confusing OperationalError instead of using the 30s default. Severity LOW because (a) negative timeout is operator error, not silent corruption, (b) error is loud (immediate raise) not silent, (c) T0_SQLITE_POLICY does not specify negative-value handling. `"1e10"`: `float("1e10") / 1000.0 = 1e7`s ≈ 116 days timeout — accepted as numeric. Same loud-failure pattern. `"0"`: returns 0.0 (sqlite3 would treat as immediate-fail-on-busy; not the fallback default). All these are operator-input errors with loud signal, not silent miscompiles. PASS with C-1 LOW. |
| 4 | Deviation 1 verdict (connect_or_degrade unwired) | APPROVE_WITH_TEST | See Deviation 1 section. Primitive shipped, cycle wiring deferred to T2G per phase.json successor_constraint. Same Fitz #4 shape as T1C C-2. Coordinator MUST log explicit T2G carry-forward in invariants ledger. |
| 5 | Deviation 2 verdict (sentinel wrapper) | APPROVE | See Deviation 2 section. Wrapper invoked unconditionally at module load (line 76); refusal happens before any DB connect. T1E-REBUILD-SENTINEL-REFUSES satisfied. |
| 6 | Deviation 3 verdict (_skip_commit removal) | APPROVE | See Deviation 3 section. Zero post-edit callers; architectural shift to per-bucket commits makes the flag obsolete. |
| 7 | 14 migration OperationalError handlers preserved | PASS | Pre-T1E count: 14 `except sqlite3.OperationalError:` handlers per phase.json planner_notes (lines 329, 1443, 1451, 1455, 1467, 1485, 1497, 1511, 1522, 1533, 1542, 1548, 1557, 1562). Post-T1E count: 30 `OperationalError` references total in db.py; 28 `except sqlite3.OperationalError:` handlers (14 migration + 14 schema-evolution patterns). The 14 migration handlers shifted by +59 lines (T1A added ~62 lines, T1E added ~30 lines, with a small overlap). Spot-check 3: line 388 (was 329; `except sqlite3.OperationalError: pass` after `ALTER TABLE venue_commands ADD COLUMN envelope_id`), line 1464 (was 1443; entry_alpha_usd ALTER), line 1488 (was 1467; market_phase ALTER) — all intact, content unchanged. T1E adds 1 NEW try/except at line 129 in `connect_or_degrade` that handles 'database is locked' specifically and re-raises others. The 14 migration handlers are NOT touched. |
| 8 | T1A merge surface byte-identical | PASS | `sed -n '1456,1460p' src/state/db.py` shows: `# T1A: DDL single-source — delegate to schema owner to avoid duplication.` / `from src.execution.settlement_commands import SETTLEMENT_COMMAND_SCHEMA` / `conn.executescript(SETTLEMENT_COMMAND_SCHEMA)` (lines 1456-1458). Drift from cited 1395-1397 is +61, consistent with T1E's +30-line insertion above + earlier displacements. Content byte-identical to T1A B2 closure evidence. T1A-DDL-SINGLE-SOURCE invariant intact (also confirmed by Attack 9). |
| 9 | Out-of-scope discipline | PASS | `git diff --stat` shows exactly 4 modified files: `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`, `scripts/rebuild_calibration_pairs_v2.py`, `src/state/db.py`. Plus 2 untracked test files (`tests/test_sqlite_busy_timeout.py`, `tests/test_rebuild_live_sentinel.py`). The other untracked entries (`.claude/orchestrator/`, `.zeus/`, `docs/.../phases/T1G/T1H/`) are coordinator-owned packet artifacts. `git diff` is empty for all listed out_of_scope files: `src/engine/cycle_runner.py` (cycle wiring deferred per Deviation 1), `src/main.py`, `src/execution/harvester.py`, `src/state/chain_reconciliation.py`, `src/state/portfolio.py`, `src/venue/polymarket_v2_adapter.py`, `src/execution/settlement_commands.py`. Zero out-of-scope src/ source touches. |
| 10 | No prior-phase regression | PASS | T1A: `git grep "CREATE TABLE IF NOT EXISTS settlement_commands" src/ scripts/ tests/` = 1 source match (`src/execution/settlement_commands.py:28`) + 1 test pattern constant (`tests/test_settlement_commands_schema.py:19`). T1F+T1BD+T1C: `git diff src/venue/polymarket_v2_adapter.py src/state/chain_reconciliation.py src/state/portfolio.py src/execution/harvester.py src/execution/settlement_commands.py` returns empty. All four predecessor phase invariants intact. |

## Caveats

- **C-1 (LOW)**: `_db_busy_timeout_s()` does not validate non-positive numeric inputs (e.g., `ZEUS_DB_BUSY_TIMEOUT_MS=-1000` would produce `-1.0`s passed to `sqlite3.connect`, raising at connect time). Loud failure mode (not silent miscompile), but adds an unnecessary diagnostic step for operator input errors. Severity LOW because the error path is fail-loud and recoverable by env-var correction.

- **C-2 (LOW-MEDIUM)**: `connect_or_degrade` (db.py:120-131) is defined and tested at the primitive level, but NOT yet wired into `src/engine/cycle_runner.py` (out_of_scope per scope.yaml). T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH invariant is satisfied at the primitive level only. Per phase.json successor_constraint, T2G is the successor that completes the wiring. The test name `test_db_write_timeout_does_not_crash_daemon` is aspirational — what it actually tests is `connect_or_degrade` returning None on lock, not daemon-cycle behavior. Coordinator MUST log the T2G carry-forward in the invariants ledger ("T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH: ASSERTED_PRIMITIVE; cycle wiring deferred to T2G"). Same Fitz #4 shape as T1C C-2 (defined-but-unused). Not blocking T1E close because the contract is acknowledged via successor_constraint, but the gap MUST be tracked.

- **C-3 (LOW-MEDIUM)**: `rebuild_all_v2` previously used an outer SAVEPOINT to roll back HIGH writes if LOW-side failed ("LOW-side failure rolls back HIGH writes — no orphan rows on partial failure" — original docstring). T1E removes this and notes "A metric-level failure does not roll back previously committed city buckets from prior metrics; operators should inspect the DB on failure." This is a SEMANTIC CHANGE: pre-T1E rebuild was all-or-nothing across metrics; post-T1E rebuild can leave HIGH committed and LOW partial. This is by-design per T1E-REBUILD-TRANSACTION-SHARDED (bounded writer-lock-hold), but the orphan-row risk is real if LOW fails after HIGH commits. Severity LOW-MEDIUM because (a) the rebuild script is dangerous-if-run anyway (manifest:478 dangerous_if_run=true), (b) the docstring change is explicit (operator is on notice), (c) the trade-off (writer-lock-hold-bound vs cross-metric atomicity) is the explicit phase intent. Not blocking but operator-visible behavior change worth noting in T1E close evidence.

- **C-4 (LOW carry-forward from T1F/T1BD/T1C)**: Counter emit `db_write_lock_timeout_total` uses `logger.warning("telemetry_counter event=...")` text-tap pattern; tests assert behavior (17 pass) not emit-text via `caplog`. Same structural-vs-test-asserted gap as T1F C-1 / T1BD C-3 / T1C C-3 (deferred to T2F typed sink). Consistent treatment; not new in T1E.

## T1E Verdict

CRITIC_DONE_T1E
verdict: APPROVE_WITH_CAVEATS
deviation_1_unwired_verdict: APPROVE_WITH_TEST
deviation_2_sentinel_wrapper_verdict: APPROVE
deviation_3_skip_commit_removal_verdict: APPROVE
caveats: ["C-1 LOW: _db_busy_timeout_s does not validate non-positive numeric inputs (negative env produces negative timeout, loud-fail at connect)", "C-2 LOW-MEDIUM: connect_or_degrade defined+tested at primitive level but NOT wired into cycle_runner (out_of_scope); coordinator must log T2G carry-forward in invariants ledger; same Fitz #4 shape as T1C C-2", "C-3 LOW-MEDIUM: rebuild_all_v2 SEMANTIC CHANGE — pre-T1E was all-or-nothing across metrics, post-T1E allows partial commits across metrics; by-design per T1E-REBUILD-TRANSACTION-SHARDED but orphan-row risk on cross-metric failure (operator-visible)", "C-4 LOW (carry-forward from T1F/T1BD/T1C): counter emit uses logger.warning text-tap pattern not test-asserted via caplog; deferred to T2F typed sink"]
ms_to_s_conversion_correct: yes
malformed_env_handling_robust: yes
two_connect_sites_updated: yes
fourteen_migration_handlers_preserved: yes
t1a_merge_surface_byte_identical: yes
sentinel_check_precedes_db_connect: yes
shard_basis_per_city_metric: yes
no_t1a_regression: yes
no_t1f_regression: yes
no_t1bd_regression: yes
no_t1c_regression: yes
out_of_scope_src_files_touched: 0
ready_for_close: yes
