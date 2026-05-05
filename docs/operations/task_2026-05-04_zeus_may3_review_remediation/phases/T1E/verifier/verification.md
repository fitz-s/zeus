# T1E Verification (independent reproduction)

Verifier: verifier-sonnet (subagent a62719f768a7c2478, 2026-05-05)
HEAD at verification: 1116d827482253445c285d13948e50150cf3cc5a

Python resolution note: `test_sqlite_busy_timeout.py` imports only `src.state.db` — no sklearn dependency — and runs cleanly with homebrew Python 3.14. `test_rebuild_live_sentinel.py` imports `scripts.rebuild_calibration_pairs_v2` which imports `src.calibration.manager` -> `platt` -> `sklearn`. Steps 1 and 2 therefore split: step 1 uses homebrew python3, step 2 requires zeus venv. Steps 3-7 (T1A/T1F/T1BD/T1C regressions) all use homebrew python3 except step 6 (T1C harvester tests) which requires zeus venv. Same env pattern established in T1BD and T1C verifications.

---

## Step 1 — test_sqlite_busy_timeout.py

Command: `python3 -m pytest -q tests/test_sqlite_busy_timeout.py`
exit_code: 0
output:
```
..........                                                               [100%]
10 passed in 0.05s
EXIT_CODE: 0
```

## Step 2 — test_rebuild_live_sentinel.py

First attempt with homebrew python3:
```
3 passed, 4 errors in 0.13s
EXIT_CODE: 1
```
Root cause: 4 tests require `rebuild_mod` fixture which imports `scripts.rebuild_calibration_pairs_v2` -> `src.calibration.manager` -> `platt` -> `sklearn`. Same pre-existing env gap as T1BD/T1C.

Re-run with zeus venv (`/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python`):
exit_code: 0
output:
```
.......                                                                  [100%]
7 passed in 0.98s
EXIT_CODE: 0
```

## Step 3 — T1A regression

Command: `python3 -m pytest -q tests/test_settlement_commands.py tests/test_settlement_commands_schema.py`
exit_code: 0
output:
```
.........                                                                [100%]
9 passed in 0.19s
EXIT_CODE: 0
```

## Step 4 — T1F regression

Command: `python3 -m pytest -q tests/test_v2_adapter.py tests/test_venue_envelope_live_bound.py tests/test_polymarket_adapter_submit_safety.py`
exit_code: 0
output:
```
..........................................                               [100%]
42 passed in 0.14s
EXIT_CODE: 0
```

## Step 5 — T1BD regression

Command: `python3 -m pytest -q tests/test_chain_reconciliation_corrected_guard.py tests/test_position_projection_d6_counters.py`
exit_code: 0
output:
```
..................                                                       [100%]
18 passed in 0.09s
EXIT_CODE: 0
```

## Step 6 — T1C regression

Command: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest -q tests/test_harvester_settlement_redeem.py tests/test_harvester_learning_authority.py`
exit_code: 0
output:
```
...................                                                      [100%]
19 passed in 0.97s
EXIT_CODE: 0
```
(7 from test_harvester_settlement_redeem + 12 from test_harvester_learning_authority = 19 total)

## Step 7 — CRITICAL: ms→s conversion sanity

Command: `grep -n "_db_busy_timeout_s\|/ 1000\.0\|/1000\.0\|float.*1000\|timeout.*1000\|1000.*timeout" src/state/db.py | head -20`
output:
```
34:def _db_busy_timeout_s() -> float:
42:        return float(raw) / 1000.0
62:    timeout_s = _db_busy_timeout_s()
409:    timeout_s = _db_busy_timeout_s()
```

Command: `sed -n '40,55p' src/state/db.py`
output:
```
    raw = os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")
    try:
        return float(raw) / 1000.0
    except (ValueError, TypeError):
        _startup_logger = logging.getLogger(__name__)
        _startup_logger.warning(
            "ZEUS_DB_BUSY_TIMEOUT_MS=%r is not a valid number; "
            "falling back to default 30000 ms (30 s)",
            raw,
        )
        return 30.0
```

Pattern: `float(raw) / 1000.0` — correct. Uses `float()` cast (not `int()`); explicit `1000.0` float literal (not bare `1000` which would still produce float in Python 3 but is less explicit). Return type annotation is `float`. Fallback is `30.0` (float). PASS.

## Step 8 — Diff-stat scope discipline

Command: `git diff --stat`
output:
```
 architecture/script_manifest.yaml       |   2 +-
 architecture/test_topology.yaml         |  39 ++++++
 scripts/rebuild_calibration_pairs_v2.py | 239 ++++++++++++++++++--------------
 src/state/db.py                         |  65 ++++++++-
 4 files changed, 240 insertions(+), 105 deletions(-)
```

Command: `git status --short`
output:
```
 M architecture/script_manifest.yaml
 M architecture/test_topology.yaml
 M scripts/rebuild_calibration_pairs_v2.py
 M src/state/db.py
?? .claude/orchestrator/
?? .zeus/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/
?? tests/test_rebuild_live_sentinel.py
?? tests/test_sqlite_busy_timeout.py
```

Scope assessment:
- 4 tracked modified files: `src/state/db.py`, `scripts/rebuild_calibration_pairs_v2.py`, `architecture/test_topology.yaml`, `architecture/script_manifest.yaml`. All within expected scope.
- 2 untracked new test files: `tests/test_sqlite_busy_timeout.py`, `tests/test_rebuild_live_sentinel.py`. Expected.
- Out-of-scope src/ check: `git diff --name-only | grep "^src/" | grep -v "src/state/db.py"` → empty. No unexpected src/ files touched.
- NOT modified (confirmed absent): `cycle_runner.py`, `main.py`, `harvester.py`, `chain_reconciliation.py`, `portfolio.py`, `polymarket_v2_adapter.py`, `settlement_commands.py`. SCOPE CLEAN.

## Step 9 — T1A merge surface preserved at new location

Command: `git diff src/state/db.py | grep -A 3 "T1A: DDL single-source"`
output: (empty — the T1A block is NOT in the T1E diff; it was not touched)

Command: `grep -n "T1A: DDL single-source\|from src.execution.settlement_commands import SETTLEMENT_COMMAND_SCHEMA\|conn.executescript(SETTLEMENT_COMMAND_SCHEMA)" src/state/db.py`
output:
```
1456:    # T1A: DDL single-source — delegate to schema owner to avoid duplication.
1457:    from src.execution.settlement_commands import SETTLEMENT_COMMAND_SCHEMA
1458:    conn.executescript(SETTLEMENT_COMMAND_SCHEMA)
```

T1A content is present and intact. Line numbers shifted from original 1395-1397 to 1456-1458 due to T1E additions earlier in db.py (the timeout function and `timeout_s` wiring added ~61 lines). The T1A diff not appearing in `git diff | grep "T1A"` confirms the T1A block was NOT modified by T1E — it is pure context carried forward. Content is byte-identical to the T1A B2 verified state. PASS.

## Step 10 — Sentinel + sharding sanity

Command: `grep -n "rebuild_lock.do_not_run_during_live\|_check_live_sentinel\|SystemExit\|sys.exit" scripts/rebuild_calibration_pairs_v2.py | head -10`
output:
```
58:_SENTINEL_PATH = Path(__file__).parent.parent / ".zeus" / "rebuild_lock.do_not_run_during_live"
61:def _check_live_sentinel() -> None:
62:    """Raise SystemExit(1) if the live-rebuild sentinel file exists.
74:        sys.exit(1)
77:_check_live_sentinel()
786:    sys.exit(main())
```

Sentinel ordering: `_check_live_sentinel()` called at line 77 (module-level, executes on import). First `sqlite3.connect` call is at line 756, inside `main()` which runs at line 786. Sentinel at line 77 precedes all DB connections. SENTINEL PRECEDES DB CONNECT.

Command: `grep -n "conn.commit\|\.commit()" scripts/rebuild_calibration_pairs_v2.py | head -10`
output:
```
590:        conn.commit()
```

Context around line 590 (from `sed -n '580,600p'`):
```
            conn.execute("RELEASE SAVEPOINT v2_rebuild_bucket")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT v2_rebuild_bucket")
            conn.execute("RELEASE SAVEPOINT v2_rebuild_bucket")
            raise

        # Commit after each (city, metric) bucket — bounded writer-lock hold.
        conn.commit()
```

The single `conn.commit()` is inside the per-city/metric-bucket loop with per-bucket SAVEPOINT (`SAVEPOINT v2_rebuild_bucket` at line 556). Each bucket gets its own SAVEPOINT + commit, explicitly replacing the previous "monolithic outer SAVEPOINT design" (per line 38 and 455 docstrings). This IS the sharding pattern: bounded per-bucket commits limit writer-lock-hold duration. SHARDING COMMITS PRESENT.

---

## T1E Verifier Verdict

VERIFIER_DONE_T1E
verdict: PASS
all_ten_checks_pass: yes
test_busy_timeout_pass_count: 10
test_rebuild_sentinel_pass_count: 7
test_t1a_pass_count: 9
test_t1f_pass_count: 42
test_t1bd_pass_count: 18
test_t1c_pass_count: 19
ms_to_s_conversion_grep_present: yes
diff_stat_files: [src/state/db.py, scripts/rebuild_calibration_pairs_v2.py, architecture/test_topology.yaml, architecture/script_manifest.yaml]
out_of_scope_src_files_touched: 0
t1a_merge_surface_byte_identical: yes
sentinel_precedes_db_connect: yes
sharding_commits_present: yes
ready_for_close: yes
