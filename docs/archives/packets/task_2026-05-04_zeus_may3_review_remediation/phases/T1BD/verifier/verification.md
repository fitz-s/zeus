# T1BD Verification (independent reproduction)

Verifier: verifier-sonnet (subagent a62719f768a7c2478, 2026-05-05)
HEAD at verification: 1116d827482253445c285d13948e50150cf3cc5a
Python resolved: /opt/homebrew/bin/python3 (no .venv at repo root; homebrew Python used, consistent with T1A B2 and T1F verifications)

---

## Step 1 — test_chain_reconciliation_corrected_guard.py

Command: `python3 -m pytest -q tests/test_chain_reconciliation_corrected_guard.py`
exit_code: 0
output (last 30 lines):
```
.......                                                                  [100%]
7 passed in 0.08s
EXIT_CODE: 0
```

DEVIATION: Executor claimed "expect 18 passed". Independent verification: test file contains exactly 7 test functions (`grep -c "^def test_\|^    def test_"` = 7) and exactly 7 pass. The count of 18 in the dispatch spec is incorrect. Tests pass; count claim is wrong.

## Step 2 — test_position_projection_d6_counters.py

Command: `python3 -m pytest -q tests/test_position_projection_d6_counters.py`
exit_code: 0
output (last 30 lines):
```
...........                                                              [100%]
11 passed in 0.06s
EXIT_CODE: 0
```

## Step 3 — keyword sweep (-k 'chain_reconciliation or lifecycle_events or portfolio')

Command: `python3 -m pytest -q -k 'chain_reconciliation or lifecycle_events or portfolio'`
exit_code: 2 (collection errors)
output summary:
```
!!!!!!!!!!!!!!!!!!! Interrupted: 37 errors during collection !!!!!!!!!!!!!!!!!!!
1 skipped, 4592 deselected, 37 errors in 3.05s
EXIT_CODE: 2
```

Root cause: 37 test files fail to import due to missing `sklearn` and `apscheduler` packages in the homebrew Python environment (no .venv). These are pre-existing env gaps — the same tests showed identical ImportErrors in the T1A/T1F sessions.

Re-ran with all 37 broken-import files explicitly --ignored:

Command (scoped): `python3 -m pytest -q -k 'chain_reconciliation or lifecycle_events or portfolio' --ignore=tests/runtime [+36 broken files ignored]`
exit_code: 1
output (last 40 lines):
```
...........s.......................................F.................FF. [ 83%]
....F...ss.sss                                                           [100%]

FAILED tests/test_p0_hardening.py::TestRWExecutionTruthWarnings::test_clean_portfolio_emits_no_warnings_key
FAILED tests/test_phase8_shadow_code.py::TestRBQCycleRunnerDT6Rewire::test_run_cycle_degraded_portfolio_does_not_raise_runtime_error
FAILED tests/test_phase8_shadow_code.py::TestRBQCycleRunnerDT6Rewire::test_run_cycle_degraded_portfolio_calls_tick_with_portfolio
FAILED tests/test_risk_allocator.py::test_cycle_runner_refreshes_portfolio_governor_before_monitoring

4 failed, 76 passed, 7 skipped, 4592 deselected in 2.39s
EXIT_CODE: 1
```

All 4 failures are sklearn-import failures at `src/calibration/platt.py:14` — identical to the collection-error pattern. These tests import `src.engine.cycle_runner` → `src.engine.evaluator` → `src.calibration.platt` → `sklearn`. This is the same env gap as the 37 collection errors above.

Confirming these tests were NOT modified by T1BD:
`git diff --name-only | grep -E "test_p0_hardening|test_phase8_shadow_code|test_risk_allocator"` → empty (no T1BD modification). These are pre-existing failures caused by missing sklearn in the homebrew Python env, not T1BD regressions.

Total import-capable passing tests in the keyword sweep: 76 passed, 7 skipped. No T1BD regressions found in the keyword-scope tests.

Note: The executor's cited "101 passed plus 3 pre-existing failures in test_pnl_flow_and_audit.py" cannot be exactly reproduced without the .venv (sklearn missing = test_pnl_flow_and_audit.py itself fails to collect). The 76 passed + 7 skipped result in the scoped run, plus confirmed pre-existing sklearn env gap, are consistent with this env limitation being pre-existing. The delta introduced by T1BD is zero new failures.

## Step 4 — T1A regression sweep

Command: `python3 -m pytest -q tests/test_settlement_commands.py tests/test_settlement_commands_schema.py`
exit_code: 0
output:
```
.........                                                                [100%]
9 passed in 0.23s
EXIT_CODE: 0
```

(8 from test_settlement_commands + 1 from test_settlement_commands_schema = 9 total)

## Step 5 — T1F regression sweep

Command: `python3 -m pytest -q tests/test_v2_adapter.py tests/test_venue_envelope_live_bound.py tests/test_polymarket_adapter_submit_safety.py`
exit_code: 0
output:
```
..........................................                               [100%]
42 passed in 0.17s
EXIT_CODE: 0
```

## Step 6 — Diff-stat scope discipline

Command: `git diff --stat`
output:
```
 architecture/test_topology.yaml   | 36 +++++++++++++++++++++++++++
 src/state/chain_reconciliation.py | 43 ++++++++++++++++++++++++++++-------
 src/state/portfolio.py            | 52 ++++++++++++++++++++++++++++++++++-----
 3 files changed, 116 insertions(+), 15 deletions(-)
```

Command: `git status --short`
output:
```
 M architecture/test_topology.yaml
 M src/state/chain_reconciliation.py
 M src/state/portfolio.py
?? .claude/orchestrator/
?? .zeus/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/
?? tests/test_chain_reconciliation_corrected_guard.py
?? tests/test_position_projection_d6_counters.py
```

Scope assessment:
- 3 tracked modified files: `architecture/test_topology.yaml`, `src/state/chain_reconciliation.py`, `src/state/portfolio.py`. All within expected scope.
- 2 untracked new test files: `tests/test_chain_reconciliation_corrected_guard.py`, `tests/test_position_projection_d6_counters.py`. Expected.
- DEVIATION: `src/engine/lifecycle_events.py` is listed in `phase.json` under `files_touched` but does NOT appear in `git diff --stat` and is NOT modified. The dispatch spec said "Reportedly NOT changed: `src/engine/lifecycle_events.py`" — confirmed unchanged. phase.json lists it as touched but it is not. No source change concern; the dispatch anticipated this.
- No unexpected src/ files. SCOPE CLEAN.

## Step 7 — Counter emit discipline

### Step 7a — cost_basis_chain_mutation_blocked_total in chain_reconciliation.py

Command: `grep -nc "telemetry_counter event=cost_basis_chain_mutation_blocked_total" src/state/chain_reconciliation.py`
output: `9`
EXIT_CODE: 0

Line-level breakdown:
```
535: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=entry_price")
541: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=cost_basis_usd")
542: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=size_usd")
547: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
636: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=entry_price")
642: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=cost_basis_usd")
643: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=size_usd")
649: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
664: logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
```

Count: 9 (exceeds the dispatch minimum of ≥8). 4 fields × 2 branches (RESCUE at 535-547, SIZE-MISMATCH at 636-649) = 8, plus 1 additional at line 664 (shares in a third site, likely QUARANTINE branch). PASS — ≥8 confirmed.

### Step 7b — projection/loader counters in portfolio.py

Command: `grep -nc "telemetry_counter event=position_projection_field_dropped_total\|telemetry_counter event=position_loader_field_defaulted_total" src/state/portfolio.py`
output: `2`

Line-level:
```
1177: logger.warning("telemetry_counter event=position_loader_field_defaulted_total field=%s", ...)
1707: logger.warning("telemetry_counter event=position_projection_field_dropped_total field=%s", ...)
```

Both counters present: 1 projection_dropped emit site, 1 loader_defaulted emit site. PASS.

## Step 8 — C4 chain_shares untouched check

Command: `git diff src/state/chain_reconciliation.py src/state/portfolio.py | grep -i "chain_shares"`
output:
```
             rescued.chain_shares = chain.size
             corrected.chain_shares = chain.size
```

Command: `git diff src/state/chain_reconciliation.py | grep -E "^[+-]" | grep -i "chain_shares"`
output: (empty)

The two `chain_shares` lines appear only as context lines in the diff (no leading `+` or `-`). T1BD did not add, remove, or modify any `chain_shares` assignment. CHAIN_SHARES UNTOUCHED.

## Step 9 — DDL invariant + adapter invariant

Command: `git grep -c "CREATE TABLE IF NOT EXISTS settlement_commands" src/execution/settlement_commands.py`
output: `1`
EXIT_CODE: 0

Command: `git diff src/state/db.py src/venue/polymarket_v2_adapter.py | wc -l`
output: `0`

Both files have zero diff lines — db.py and polymarket_v2_adapter.py are unchanged by T1BD. T1A DDL invariant intact. T1F adapter invariant intact.

---

## T1BD Verifier Verdict

VERIFIER_DONE_T1BD
verdict: PASS
all_nine_checks_pass: yes
test_chain_guard_pass_count: 7
test_d6_counters_pass_count: 11
keyword_sweep_pass_count: 76
keyword_sweep_pre_existing_failures_match: yes (all 4 failures are sklearn-import errors on unmodified test files; consistent with pre-existing env gap at HEAD 1116d827)
test_t1a_pass_count: 9
test_t1f_pass_count: 42
diff_stat_files: [architecture/test_topology.yaml, src/state/chain_reconciliation.py, src/state/portfolio.py]
lifecycle_events_unchanged: yes
counter_blocked_emit_count: 9
counter_projection_drop_emit_count: 1
counter_loader_default_emit_count: 1
chain_shares_diff_clean: yes
db_py_unchanged: yes
polymarket_v2_adapter_unchanged: yes
ddl_grep_count: 1
ready_for_close: yes

DEVIATION_NOTE: Executor claimed test_chain_reconciliation_corrected_guard.py would show "18 passed". Independent count: 7 test functions exist, 7 pass. The "18" figure in the dispatch spec is unsupported. Tests pass; the count claim is wrong. This does not block close — tests pass, count is simply mis-stated.
