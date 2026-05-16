# T1F Verification (independent reproduction)

Verifier: verifier-sonnet (subagent a62719f768a7c2478, 2026-05-05)
HEAD at verification: 1116d827482253445c285d13948e50150cf3cc5a
Python resolved: /opt/homebrew/bin/python3 (no .venv at repo root; homebrew Python used, same as T1A B2 verification)

---

## Step 1 — test_venue_envelope_live_bound.py

Command: `python3 -m pytest -q tests/test_venue_envelope_live_bound.py`
exit_code: 0
output (last 30 lines):
```
.......                                                                  [100%]
7 passed in 0.03s
EXIT_CODE: 0
```

## Step 2 — test_polymarket_adapter_submit_safety.py

Command: `python3 -m pytest -q tests/test_polymarket_adapter_submit_safety.py`
exit_code: 0
output (last 30 lines):
```
......                                                                   [100%]
6 passed in 0.04s
EXIT_CODE: 0
```

## Step 3 — test_v2_adapter.py (pre-existing)

Command: `python3 -m pytest -q tests/test_v2_adapter.py`
exit_code: 0
output (last 30 lines):
```
.............................                                            [100%]
29 passed in 0.13s
EXIT_CODE: 0
```

## Step 4 — T1A regression sweep

Command: `python3 -m pytest -q tests/test_settlement_commands.py tests/test_settlement_commands_schema.py`
exit_code: 0
output (last 30 lines):
```
.........                                                                [100%]
9 passed in 0.20s
EXIT_CODE: 0
```
(8 from test_settlement_commands + 1 from test_settlement_commands_schema = 9 total)

## Step 5 — Diff-stat scope discipline

Command: `git diff --stat`
output:
```
 architecture/test_topology.yaml    | 38 +++++++++++++++++++++++++++++++++
 src/venue/polymarket_v2_adapter.py | 43 ++++++++++++++++++++++++++++++++++++++
 tests/test_v2_adapter.py           | 18 +++++++---------
 3 files changed, 88 insertions(+), 11 deletions(-)
```

Command: `git status --short`
output:
```
 M architecture/test_topology.yaml
 M src/venue/polymarket_v2_adapter.py
 M tests/test_v2_adapter.py
?? .claude/orchestrator/
?? .zeus/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/
?? docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/
?? tests/test_polymarket_adapter_submit_safety.py
?? tests/test_venue_envelope_live_bound.py
```

Scope assessment:
- 3 tracked modified files: `architecture/test_topology.yaml`, `src/venue/polymarket_v2_adapter.py`, `tests/test_v2_adapter.py`. All within expected scope.
- 2 untracked new test files: `tests/test_venue_envelope_live_bound.py`, `tests/test_polymarket_adapter_submit_safety.py`. Expected.
- Untracked phases dirs (T1BD, T1C, T1E, T1F, T1G, T1H): orchestrator artifacts, not src/ changes.
- No unexpected src/ files modified. SCOPE CLEAN.

Note: `docs/operations/.../scope.yaml` is NOT showing as modified in `git diff --stat`. The executor's claimed scope.yaml update is either pre-existing or staged differently. This is a minor discrepancy from the expected file list in the dispatch (which listed scope.yaml as expected). Risk: LOW — the critical scope constraint (no unexpected src/ files) is satisfied.

## Step 6 — Adapter ordering check

Command: `python3 -c "import ast, sys; tree = ast.parse(open('src/venue/polymarket_v2_adapter.py').read()); print('parsed_ok')"`
exit_code: 0
output:
```
parsed_ok
```

Command: `grep -n "assert_live_submit_bound\|create_and_post_order\|create_order\|post_order\|COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE" src/venue/polymarket_v2_adapter.py`
output:
```
319:            envelope.assert_live_submit_bound()
361:        if callable(getattr(client, "create_and_post_order", None)):
362:            raw_response = client.create_and_post_order(
369:        elif callable(getattr(client, "create_order", None)) and callable(getattr(client, "post_order", None)):
371:                signed_order = client.create_order(order_args, options=options)
380:            raw_response = client.post_order(
581:                error_code="COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE",
```

Assertion ordering: `assert_live_submit_bound()` at line 319 precedes all SDK call sites (361, 362, 369, 371, 380). ASSERTION BEFORE SDK CONFIRMED.

`COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE` at line 581 is in the compat-quarantine path (error_code field), consistent with the quarantine surface described in phase.json.

## Step 7 — AMD-T1F amendment ledger sanity

Command: `tail -5 .claude/orchestrator/runs/zeus-may3-remediation-20260504/state/invariants.jsonl`
output:
```
{"invariant":"T1A-DDL-SINGLE-SOURCE","status":"ASSERTED","phase":"T1A","batch":"B2","evidence":"git grep CREATE TABLE IF NOT EXISTS settlement_commands → 1 match in src/execution/settlement_commands.py:28","critic":"a03538fb0b5f999ed","verifier":"a62719f768a7c2478","asserted_at":"2026-05-05T04:55:00Z"}
{"invariant":"T1A-DB-IMPORTS-SCHEMA","status":"ASSERTED","phase":"T1A","batch":"B2","evidence":"src/state/db.py:1395-1397 function-scope import of SETTLEMENT_COMMAND_SCHEMA + executescript; no circular import","critic":"a03538fb0b5f999ed","verifier":"a62719f768a7c2478","asserted_at":"2026-05-05T04:55:00Z"}
{"invariant":"T1A-NO-BEHAVIOR-CHANGE","status":"ASSERTED","phase":"T1A","batch":"B2","evidence":"tests/test_settlement_commands.py 8/8 pass post-edit; db_py_other_lines_touched=0; T1E surface untouched","critic":"a03538fb0b5f999ed","verifier":"a62719f768a7c2478","asserted_at":"2026-05-05T04:55:00Z"}
{"ts":"2026-05-05T00:10:00Z","phase":"T1F","action":"amendment_authorized","amendment_id":"AMD-T1F-1","authority":"coordinator (per user directive 不打扰我 + reality-answered)","scope_extension":"tests/test_v2_adapter.py — 4 named tests only","permitted_edit":"add kwarg _allow_compat_for_test=True to .submit_limit_order(...) calls; no other change","named_tests":["test_submit_limit_order_snapshot_failure_is_typed_pre_submit_rejection","test_submit_limit_order_rejects_before_sdk_submit_when_fee_bps_missing","test_submit_limit_order_rejects_before_sdk_submit_when_fee_bps_none","test_legacy_sell_compatibility_hashes_final_side_and_size"],"rationale":"T1F invariant T1F-COMPAT-SUBMIT-LIMIT-ORDER-REJECTS-OR-FAKE option-b explicitly designs the test-flag path; the 4 tests are its natural consumers (testing downstream-of-gate validation logic). Amendment is mechanical, single-kwarg-per-call.","executor_reuse":"a18a286184cefb3e3"}
{"ts":"2026-05-05T00:25:00Z","phase":"T1F","action":"amendment_authorized","amendment_id":"AMD-T1F-2","authority":"coordinator (reality-answered: T1F invariant T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK supersedes legacy test assertion)","scope_extension":"tests/test_v2_adapter.py::test_legacy_sell_compatibility_hashes_final_side_and_size — surgical rewrite","permitted_edits":["DROP fake_client.create_order call_count assertion (T1F makes the call_count=0 by design)","REPLACE submit(envelope) invocation with direct construction via _create_compat_submission_envelope or equivalent helper","KEEP hash-correctness assertions on lines 492-512 (these inspect envelope side/size hash fields, T1F-orthogonal)","KEEP envelope.is_compatibility_placeholder assertion","ADD inline comment citing T1F invariant + AMD-T1F-2"],"forbidden":"any other test in the file; any source file; any new helper or fixture","rationale":"Fitz #4 (make category impossible): T1F renders 'placeholder envelope reaches SDK' unwritable. Test's create_order assertion encoded the pre-T1F invariant and must be retired. Hash-correctness logic (the test's actual subject) is testable without SDK contact via direct envelope inspection.","executor_reuse":"a18a286184cefb3e3"}
```

AMD-T1F-1 entry: PRESENT (ts: 2026-05-05T00:10:00Z)
AMD-T1F-2 entry: PRESENT (ts: 2026-05-05T00:25:00Z)

Command: `grep -c "_allow_compat_for_test=True" tests/test_v2_adapter.py`
output: `3`
EXIT_CODE: 0

AMD-T1F-1 named 4 tests for the kwarg addition; AMD-T1F-2 permitted the surgical rewrite of `test_legacy_sell_compatibility_hashes_final_side_and_size` which REPLACES the submit() call (removing the kwarg site). Net: 4 kwarg additions (AMD-T1F-1) - 1 removed by rewrite (AMD-T1F-2) = 3 sites. Count of 3 is consistent with both amendments applied in sequence. CONSISTENT.

---

## T1F Verifier Verdict

VERIFIER_DONE_T1F
verdict: PASS
all_seven_checks_pass: yes
test_envelope_live_bound_pass: yes (count: 7)
test_adapter_safety_pass: yes (count: 6)
test_v2_adapter_pass: yes (count: 29)
test_settlement_commands_still_pass: yes
test_settlement_commands_schema_still_pass: yes
diff_stat_scope_clean: yes
adapter_assertion_before_sdk: yes
amd_t1f_1_kwarg_count: 3
amd_t1f_ledger_entries_present: yes
ready_for_close: yes
