# B4 — Pytest Suite Triage & CI Gate Expansion (Execution Plan)

**Filed**: 2026-05-01 by test-engineer (B4 final review item, ultrareview25_remediation)
**Branch**: `live-prep-2026-05-01` (HEAD `118d61c8`)
**Pre-commit baseline at start**: 245 passed / 22 skipped / 0 failed across 17 file groups
**Full-suite reality**: `pytest -m ""` → **149 failed / 4326 passed / 111 skipped / 2 xfailed** (819s, 47 test files)

> Audit basis: full-suite run completed 13:23 UTC, written to `bfsfy8e47.output`; failure list pulled
> via `pytest -m "" --tb=no -q | grep ^FAILED` and 11-test deep-sample of failing assertions across
> the 7 dominant clusters (2026-05-01 13:30–13:50 UTC).

---

## 1. Master Failure List (149 total, 47 files)

Full list captured in this session at the operator's direction; grouped by file with cluster size:

| File | # | Dominant Failure Mode |
|---|---|---|
| `test_topology_doctor.py` | 18 | Topology drift: 86 unregistered files vs `architecture/AGENTS.md` registry |
| `test_pnl_flow_and_audit.py` | 17 | Stub fixtures drift; SIGNAL_QUALITY now blocks before RISK_REJECTED can fire |
| `test_replay_time_provenance.py` | 9 | Schema NOT NULL: `ensemble_snapshots.temperature_metric` (PR `13cbf68c`) |
| `test_calibration_unification.py` | 9 | `_build_all_bins` now raises ValueError on missing market_id (was: returned single-bin fallback) |
| `test_run_replay_cli.py` | 8 | Same temperature_metric NOT NULL upstream |
| `test_healthcheck.py` | 7 | `result["healthy"]` returns False where True expected — degrade logic re-tightened |
| `test_z0_plan_lock.py` | 5 | Stale paths: `task_2026-04-26_ultimate_plan/r3/_phase_status.yaml` & `polymarket_clob_v2_migration/plan.md` deleted |
| `test_rebuild_pipeline.py` | 5 | NOT NULL temperature_metric chain |
| `test_polymarket_error_matrix.py` | 5 | Operator changed exit-error semantics: 4xx/5xx/timeout now `rejected`, not `unknown_side_effect` |
| `test_calibration_v2_fallback_alerting.py` | 5 | Lambda fixtures don't accept new `data_version` kwarg added to `load_platt_model_v2` |
| `test_runtime_guards.py` | 4 | Same SIGNAL_QUALITY-blocks-RISK_REJECTED issue (shared with pnl_flow) |
| `test_phase10d_closeout.py` | 4 | temperature_metric column existence checks/writes drift |
| `test_p0_hardening.py` | 4 | Decision-source-integrity gate blocks v2_preflight before it can fire |
| `test_riskguard.py` | 3 | Asserts `DATA_DEGRADED` but operator commit `df5ce642` intentionally changed empty/stale to `GREEN` |
| `test_phase6_day0_split.py` | 3 | Day0 split fixture drift |
| `test_discovery_idempotency.py` | 3 | Idempotency lookup contract change |
| `test_calibration_bins_canonical.py` | 3 | R11/R13 calibration provenance writes |
| `test_phase3_observation_closure.py` | 2 | low_so_far typed-context contract |
| `test_k2_live_ingestion_relationships.py` | 2 | main.py k2 function/job_id refs |
| `test_k1_slice_d.py` | 2 | extract_outcomes filtering |
| `test_db.py` | 2 | NOT NULL temperature_metric on direct SQL inserts |
| `test_data_rebuild_relationships.py` | 2 | r2 canonical rebuild status surface |
| `test_config.py` | 2 | Market-scanner LA/configured-city metadata |
| 24 single-failure files | 24 | Mixed: structural_linter, semantic_linter, k7/k3.5/k2.x slices, neg_risk, tick_size, source_health, command_grammar, etc. |

---

## 2. Per-Failure Categorization (by ROOT CAUSE, not test name)

Each cluster classified using the 6-category scheme from the request, grounded in failing-assertion evidence
(sampled 14 representative tests; rest classified by transitive root-cause grouping).

### Category A — STALE_FIXTURE / STALE_TEST_LOGIC: schema migration `temperature_metric NOT NULL`

**Origin**: PR `13cbf68c` ("Daemon refactor: dual-pipeline forecasts, K1 obs schema, 6 antibodies")
added `temperature_metric TEXT NOT NULL CHECK IN ('high','low')` to `ensemble_snapshots`
(`architecture/2026_04_02_architecture_kernel.sql:129`).

**Evidence**: `tests/test_db.py:947` and `tests/test_replay_time_provenance.py:16` both raise
`sqlite3.IntegrityError: NOT NULL constraint failed: ensemble_snapshots.temperature_metric` — INSERTs in
test fixtures predate the column.

**Affected (≈30 failures)**:
- `test_db.py` (2)
- `test_replay_time_provenance.py` (9)
- `test_run_replay_cli.py` (8)
- `test_rebuild_pipeline.py` (5)
- `test_phase10d_closeout.py` (4) — partial; some are MISSING_ENFORCEMENT
- `test_ensemble_snapshots_bias_corrected_schema.py` (1)
- `test_tigge_snapshot_p_raw_backfill.py` (1)

**Verdict**: **STALE_FIXTURE** (mechanical) — every test fixture that INSERTs into `ensemble_snapshots` must
add `temperature_metric='high'` (or `'low'` per scenario semantics). Total: ~30 INSERT statements to
amend across 7 files.

**Effort**: 5–10 min/test × 30 = **2.5–5 hours mechanical.**

### Category B — STALE_TEST_LOGIC: operator-decision-driven semantic changes

**Origin**: Three operator commits intentionally changed test-asserted behavior.

**B1. RiskGuard cold-start GREEN, not DATA_DEGRADED** (commit `df5ce642`)
- `test_riskguard.py::TestRiskGuardTrailingLossSemantics` × 3 — assert `DATA_DEGRADED`, code returns `GREEN`.
- **Verdict**: **STALE_TEST_LOGIC** — operator authority. Update assertions to `GREEN`.

**B2. Polymarket exit error matrix: rejected, not unknown_side_effect** (commit `c701c8aa` "Live smoke part 2")
- `test_polymarket_error_matrix.py::TestExecuteExitOrderErrorMatrix` × 5 — assert `unknown_side_effect`,
  code returns `rejected` for 429/500/503/timeout/network.
- **Verdict**: **STALE_TEST_LOGIC** — semantics tightened (rejection is now claimable since no side-effect
  hit Polymarket). Update assertions.

**B3. Decision-source-integrity gate fires before v2_preflight** (commit `13cbf68c`)
- `test_p0_hardening.py::TestR2V2PreflightBlocksPlacement` × 2 — fixtures lack `decision_source_context`,
  so the integrity gate (newer; runs first) blocks before v2_preflight can. Returns
  `decision_source_integrity:missing_decision_source_context` instead of `v2_preflight_failed`.
- **Verdict**: **STALE_FIXTURE** — fixtures must populate `decision_source_context`; assertion stays.
- `test_p0_hardening.py::TestR3RuntimePostureBlocksEntry` × 2 — likely same shape (sample one to confirm).

**Effort**: 5 min/test × 12 = **~1 hour.**

### Category C — INFRASTRUCTURE: deleted/moved planning docs

**Origin**: Path-referencing tests reference docs that no longer exist on disk.

**Affected**:
- `test_z0_plan_lock.py` × 5: references `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
  AND `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/plan.md` — both directories absent.
- Likely partial: `test_command_grammar_amendment.py`, `test_assumptions_validation.py`,
  `test_neg_risk_passthrough.py` may share same shape (verify per-test).

**Verdict mix**:
- If the plan was completed/superseded → **DEAD_FEATURE** (delete the test).
- If plan is still active and the file just moved → **INFRASTRUCTURE** (update path constant).
- Operator must answer per-doc; defaults below in §6.

**Effort**: 10 min/test × 5–8 = **~1 hour.**

### Category D — INFRASTRUCTURE: topology_doctor unregistered files

**Origin**: 86 files newly tracked (`tests/conftest.py`, recent scripts, recent tests, recent architecture
yamls) but not added to `architecture/AGENTS.md` / `tests/AGENTS.md` / `scripts/AGENTS.md` registries.

**Affected**: `test_topology_doctor.py` × 18 (every mode test fails because the strict residual is non-zero).

**Verdict**: **INFRASTRUCTURE** — bulk-mechanical: register each tracked file under the right AGENTS.md
section. NOT a real test logic problem; no behavior changed.

**Caveat**: Some of these 86 files might genuinely be deletable (not registered = candidates for cleanup).
A quick scan should split: register-keep vs delete-untracked. **Default**: register all 86, do cleanup in a
follow-up slice.

**Effort**: 1–2 min/file × 86 = **~2 hours**, or 10 min if scripted.

### Category E — STALE_FIXTURE: signal-quality gate moved earlier in pipeline

**Origin**: Order of evaluation changed — `SIGNAL_QUALITY` now rejects before `RISK_REJECTED` can fire,
because v2 ensemble snapshots without `temperature_metric` (or stub fixtures without the new K1 obs schema)
fail signal-quality first.

**Affected (≈21 failures)**:
- `test_pnl_flow_and_audit.py` × 17
- `test_runtime_guards.py` × 4 (`test_strategy_gate_blocks_trade_execution` got
  `SIGNAL_QUALITY` instead of `STRATEGY_REJECTED`)

**Verdict**: **STALE_FIXTURE** — fixtures need to populate enough of the K1 obs / ensemble surface that
SIGNAL_QUALITY clears, so the test can reach the deeper stage it actually exercises.

**Caveat**: A subset (estimate: 3–5 of 17 in pnl_flow) may be **STALE_TEST_LOGIC** — they were testing
the old short-circuit ordering and the gate-precedence change is itself the antibody. Triage per test.

**Effort**: 15 min/test × 21 = **~5 hours** (more than mechanical because each fixture upgrade requires
understanding which K1/ensemble fields the test actually wants to exercise).

### Category F — STALE_FIXTURE: lambda mocks missing new `data_version` kwarg

**Origin**: `src/calibration/manager.py:187` now calls `load_platt_model_v2(..., data_version=...)`;
test mocks built as `lambda *a, **kw: ...` without `data_version` raise TypeError.

**Affected**: `test_calibration_v2_fallback_alerting.py` × 5.

**Verdict**: **STALE_FIXTURE** — mock signatures need `data_version=None` kwarg. Mechanical.

**Effort**: 2 min/test × 5 = **15 min.**

### Category G — MISSING_ENFORCEMENT or STALE_TEST_LOGIC: calibration_unification ValueError

**Origin**: `src/engine/monitor_refresh.py:124,129` raises `ValueError("support topology unavailable")` /
`("support topology stale: market_scan_authority=NEVER_FETCHED")` — fail-fast behavior.

The 9 tests in `test_calibration_unification.py` were written assuming `_build_all_bins` returned a
single-bin fallback. Now it raises.

**Verdict**: AMBIGUOUS — depends on operator intent.
- If the fail-fast is the new contract (fixture is supposed to provide a real market_id / fresh
  scan_authority) → **STALE_TEST_LOGIC**: tests should set up a real held market or use
  `pytest.raises(ValueError)` with message-asserts instead of `bins, idx = _build_all_bins(...)`.
- If `_build_all_bins` should still have a graceful degradation path → **MISSING_ENFORCEMENT**: the
  ValueError replaces logic that was supposed to fall back.

**Recommended verdict**: **STALE_TEST_LOGIC** — fail-fast on missing topology aligns with INV-19a
DATA_DEGRADED-not-silent-fallback principles. Fixture should set up a real held market_id (or assert raises).

**Effort**: 15 min/test × 9 = **~2 hours.**

### Category H — INFRASTRUCTURE / STALE_TEST_LOGIC: assorted single-failure files (24 tests)

Per-failure triage required. Initial grouping by sampled assertion patterns:

- `test_structural_linter.py::test_entire_repo_passes_linter` — verifier said this was 10 governance
  violations; **REAL_BUG_FIX or STALE_TEST_LOGIC** (operator must classify governance scope).
- `test_semantic_linter.py::test_no_bare_calibration_pairs_select` — similar to structural; **REAL_BUG_FIX**
  if a non-allowlisted query exists, **STALE_TEST_LOGIC** if allowlist needs widening.
- `test_topology_doctor.py` overlap — already in Category D.
- `test_z0_plan_lock.py` overlap — already in Category C.
- `test_healthcheck.py` × 7 — `healthy` returns False; sample shows degrade-logic re-tightened. Need to
  see what new fields healthcheck requires; likely **STALE_FIXTURE** (mock isn't populating the new
  `current_auto_pause_reason` from control plane).
- Remaining 13 single-failure files: per-test triage, default class **STALE_FIXTURE** with operator review.

**Effort**: 20 min/test avg × 24 = **~8 hours.**

---

## 3. Effort Summary

| Category | Class | Count | Avg Effort | Total Effort | Owner |
|---|---|---|---|---|---|
| A. temperature_metric INSERT fixtures | STALE_FIXTURE | ~30 | 5–10 min | 2.5–5 h | Claude executor batch |
| B. Operator-driven semantic changes | STALE_TEST_LOGIC | 12 | 5 min | 1 h | Claude executor batch (B1, B2 mechanical; B3 stale-fixture) |
| C. Deleted plan docs | INFRASTRUCTURE / DEAD_FEATURE | ~8 | 10 min | 1 h | Operator-led (decide alive/dead) + Claude exec |
| D. AGENTS.md unregistered files | INFRASTRUCTURE | 18 (tests) / 86 (registrations) | 1–2 min/reg | 2 h | Claude executor batch (mechanical) |
| E. Signal-quality gate ordering | STALE_FIXTURE | ~21 | 15 min | 5 h | Claude executor (slow — context-heavy) |
| F. data_version mock signature | STALE_FIXTURE | 5 | 2 min | 15 min | Claude executor batch |
| G. _build_all_bins ValueError | STALE_TEST_LOGIC | 9 | 15 min | 2 h | Claude executor batch |
| H. Assorted singletons | Mixed | ~24 | 20 min | 8 h | Claude executor (per-file triage required) |
| linter + structural | REAL_BUG_FIX or STALE | 2 | 1 h | 2 h | Operator-side or Opus delegation |
| **TOTAL** | | **~129** | | **~25 h** | |

(20 of 149 covered by D's 18-test cluster being a single fix; net is ~129 unique fix actions.)

---

## 4. Sequenced Execution Plan (Phase-Gated)

Each phase MUST land a working commit before next phase starts. Each phase tightens the pre-commit gate.

### Phase 1 — INFRASTRUCTURE quick wins (≈3 hours, +20 to baseline)

**Scope**: Categories C + D + F.

**Actions**:
1. Run `git ls-files | xargs -I{} grep -L "{}" architecture/AGENTS.md tests/AGENTS.md scripts/AGENTS.md`
   to confirm 86-file gap; register each into the appropriate AGENTS.md section.
2. Decide per-doc: are `task_2026-04-26_ultimate_plan/` and `polymarket_clob_v2_migration/` plans active
   or superseded? If superseded → delete the failing assertions in `test_z0_plan_lock.py` (or replace with
   a check that the new active plan exists). If active but moved → update the path constant.
3. Add `data_version=None` kwarg to the 5 lambda fixtures in `test_calibration_v2_fallback_alerting.py`.

**Expected delta**: +18 (topology_doctor) + 5 (z0_plan_lock) + 5 (calibration_v2_fallback) = **+28 passes**.

**Pre-commit hook update**: After verifying these tests pass standalone, append to `TEST_FILES`:
- `tests/test_topology_doctor.py`
- `tests/test_calibration_v2_fallback_alerting.py`
- `tests/test_z0_plan_lock.py` (if path-update path was taken)

Bump `BASELINE_PASSED` from 245 to ~273.

**Commit message**: "B4 Phase 1: register 86 tracked files in AGENTS.md, fix data_version mock kwarg, update plan-lock paths".

### Phase 2 — STALE_TEST_LOGIC operator-decision updates (≈1 hour, +12 to baseline)

**Scope**: Category B (B1 RiskGuard cold-start, B2 Polymarket error matrix).

**Actions**:
1. `test_riskguard.py::TestRiskGuardTrailingLossSemantics` × 3: change `DATA_DEGRADED` → `GREEN` per
   commit `df5ce642`. Update test docstring to cite the commit.
2. `test_polymarket_error_matrix.py::TestExecuteExitOrderErrorMatrix` × 5: change `unknown_side_effect`
   → `rejected` for 429/500/503/timeout/network. Update test docstring to cite commit `c701c8aa`.

**Expected delta**: **+8 passes** (3 + 5).

**Pre-commit hook update**: Append to `TEST_FILES`:
- `tests/test_riskguard.py`
- `tests/test_polymarket_error_matrix.py`

Bump `BASELINE_PASSED` to ~281.

**Commit message**: "B4 Phase 2: align test_riskguard + polymarket_error_matrix to operator-decided semantics (df5ce642, c701c8aa)".

### Phase 3 — STALE_FIXTURE bulk: temperature_metric NOT NULL (≈4 hours, +30 to baseline)

**Scope**: Category A.

**Actions**:
1. Grep all INSERTs into `ensemble_snapshots` in `tests/`: `grep -rn "INTO ensemble_snapshots" tests/`.
2. For each, add `temperature_metric` to columns + `'high'` (or `'low'` per test intent) to values.
3. The semantic choice 'high' vs 'low' usually mirrors the city/scenario: city in 'New York' / 'Chicago'
   summer = HIGH; winter cold-snap = LOW. If ambiguous, use 'high' (active-trading default).

**Expected delta**: **+30 passes** across `test_db.py`, `test_replay_time_provenance.py`,
`test_run_replay_cli.py`, `test_rebuild_pipeline.py`, partial `test_phase10d_closeout.py`,
`test_ensemble_snapshots_bias_corrected_schema.py`, `test_tigge_snapshot_p_raw_backfill.py`.

**Pre-commit hook update**: Append:
- `tests/test_db.py`
- `tests/test_replay_time_provenance.py`
- `tests/test_run_replay_cli.py`
- `tests/test_rebuild_pipeline.py`
- `tests/test_phase10d_closeout.py`
- `tests/test_ensemble_snapshots_bias_corrected_schema.py`
- `tests/test_tigge_snapshot_p_raw_backfill.py`

Bump `BASELINE_PASSED` to ~311.

**Commit message**: "B4 Phase 3: refresh ensemble_snapshots fixtures for temperature_metric NOT NULL (post-13cbf68c)".

### Phase 4 — STALE_TEST_LOGIC + STALE_FIXTURE: calibration_unification + B3 (≈3 hours, +13 to baseline)

**Scope**: Categories G + B3.

**Actions**:
1. Categories G — `test_calibration_unification.py` × 9: rewrite each test to either
   (a) populate fixture with a real held `market_id` and `market_scan_authority='FRESH'`, or
   (b) use `pytest.raises(ValueError, match="support topology")` if testing the fail-fast itself.
   Default to (a) for the 7 fallback-shape tests; (b) only for 2 tests that actually verify the raise.
2. Category B3 — `test_p0_hardening.py` × 4: populate `decision_source_context` in the fixtures so the
   integrity gate clears and v2_preflight (the actual gate under test) can fire.

**Expected delta**: **+13 passes** (9 + 4).

**Pre-commit hook update**: Append:
- `tests/test_calibration_unification.py`
- `tests/test_p0_hardening.py`

Bump `BASELINE_PASSED` to ~324.

**Commit message**: "B4 Phase 4: calibration_unification fixtures + p0_hardening decision-source context".

### Phase 5 — STALE_FIXTURE: signal-quality gate ordering (≈5 hours, +21 to baseline)

**Scope**: Category E.

**Actions**:
1. `test_pnl_flow_and_audit.py` × 17 + `test_runtime_guards.py` × 4: each fixture needs the K1
   observation snapshot or ensemble snapshot populated to a level where SIGNAL_QUALITY clears. Pattern:
   build a helper `_make_signal_quality_passing_state(...)` in the test file's conftest, call it from
   each test that intends to reach RISK_REJECTED / STRATEGY_REJECTED.
2. Per-test verification: re-run; if it now passes the gate but fails on the actual stage assertion, the
   test was correct — fix is mechanical. If it was actually testing the OLD gate-ordering, mark the test
   `@pytest.mark.xfail(strict=True, reason="gate ordering changed in 13cbf68c; new test pending")`
   and create one new test that exercises the new ordering.

**Expected delta**: **+18 passes** (3–5 fall to xfail, not delete).

**Pre-commit hook update**: Append:
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`

Bump `BASELINE_PASSED` to ~342.

**Commit message**: "B4 Phase 5: pnl_flow + runtime_guards fixtures pass signal-quality gate (post-13cbf68c)".

### Phase 6 — Assorted singletons + linters (≈8 hours, +24 to baseline)

**Scope**: Category H + structural_linter + semantic_linter.

**Actions**: per-file triage. For each remaining failing file, sample the assertion (1–2 representative
failures) and route to one of the prior categories or to **REAL_BUG_FIX** if the test correctly identifies
prod-code drift.

**Verdict pivot**: structural_linter + semantic_linter MUST be diagnosed individually — verifier's earlier
note ("10 governance violations") is older; current may be larger or smaller. If REAL_BUG_FIX, file under
operator-side; do not auto-edit prod code without operator sign-off.

**Expected delta**: **+24 passes** (or fewer if some xfail with deadlines).

**Pre-commit hook update**: Append the single-file groups whose tests now pass; leave structural_linter /
semantic_linter outside the gate until prod-side fix lands (avoid blocking commits on a known governance
gap until operator decides scope).

Bump `BASELINE_PASSED` to ~366.

**Commit message**: "B4 Phase 6: per-file triage of remaining 24 singletons".

### Phase 7 — Final gate flip + nightly job (≈1 hour)

**Scope**: After Phases 1–6, only `live_topology`-marked tests + intentional xfails remain outside the
default suite.

**Actions**:
1. Remove `pytest.ini::addopts = -m "not live_topology"` exclusion OR keep it default-off but add an
   explicit `nightly-full-suite` GitHub Actions job that runs `pytest -m "" --strict-markers`.
2. Update `.github/workflows/architecture_advisory_gates.yml` `law-gate-tests` job to invoke the broader
   `TEST_FILES` set from the pre-commit hook (single source of truth: bash-export the var, both consume).
3. Add CI-level rules from CI_GATE_TRIAGE_PROPOSAL.md Phase 4: fail on `pytest.skip(...)` without
   `reason=`, fail on `xfail` without `strict=True`, fail on baseline regression.

**Commit message**: "B4 Phase 7: full-suite CI gate + nightly drift watcher".

---

## 5. Pre-commit `TEST_FILES` Trajectory

```
Now (HEAD 118d61c8): 17 files, 245 passed
After Phase 1:       20 files, ~273 passed
After Phase 2:       22 files, ~281 passed
After Phase 3:       29 files, ~311 passed
After Phase 4:       31 files, ~324 passed
After Phase 5:       33 files, ~342 passed
After Phase 6:       ~38 files, ~366 passed
After Phase 7:       full suite, ~370+ passed (149 → ≤5 known-xfail)
```

After each phase, run the precommit script's pytest invocation locally; if pass-count matches predicted
delta within ±2 → commit. If not → re-investigate before committing.

---

## 6. Stop Conditions — When B4 closes

**Recommendation**: **Option (a)+(b) hybrid — run all 6 phases, accept up to 10 xfails.**

- Pure (a) — all 149 fail-or-die — risks deleting tests that exposed real bugs that should be fixed.
- Pure (b) — keep the current 17-file baseline — leaves the gate covering ~30% of money-path surface
  with 149 silent rotting tests.
- Hybrid — fix what's mechanically fixable (≈140 of 149); xfail-with-deadline what's prod-code-defect
  pending operator decision (≈9 — namely the structural/semantic linter gap and any genuinely-broken
  prod logic surfaced during Phase 6).

**B4 closes when**:
1. All 6 phases committed.
2. `pytest -m ""` passes with ≤10 xfails (each tagged `strict=True` + tracking ticket reference in
   `tests/SKIP_LEDGER.md`).
3. `BASELINE_PASSED` in pre-commit hook ≥ 350.
4. CI workflow expansion landed (Phase 7).
5. `tests/SKIP_LEDGER.md` filed listing every remaining xfail with deadline.

**Total elapsed effort**: ~25 hours of focused executor work, sequenceable across 4–6 sessions
(0.5–1 day per phase).

---

## 7. Risks & Failure Modes

- **Risk 1**: Phase 5 (signal-quality gate) may require deeper prod-code understanding than estimated; if
  fixtures can't be made to pass without copying half of the cycle_runtime setup, escalate to
  Opus-delegated planner for a `tests/conftest.py` shared-fixture refactor.
- **Risk 2**: Phase 6 may discover real prod bugs (e.g., `test_assumptions_validation.py` failing because
  manifest IS desynchronized from code). Do NOT silently delete tests that fail this way; route to
  REAL_BUG_FIX and operator-side.
- **Risk 3**: temperature_metric fixture choice (`'high'` vs `'low'`) may semantically change a test
  intent (e.g., a test specifically about LOW-side calibration uses 'high' default). Mitigate: each
  Phase-3 commit must include a 30-second per-test sanity check that the test's name/docstring still
  matches its assertion.
- **Risk 4**: AGENTS.md registry expansion may be the wrong fix — some unregistered files are genuinely
  dead (cleanup candidates). Phase 1 default is "register all"; a Phase 1.5 cleanup follow-up pass should
  identify which to delete instead. Do not block Phase 1 on this.

---

## 8. Why this plan is finishable

- **Mechanical bulk dominates**: Categories A + D + F + B together = ~63 of 129 fixes, all trivial
  edits with high confidence and low operator-burden.
- **Per-phase commit boundary**: every phase yields a passing pre-commit hook with a higher baseline,
  so partial completion still moves the gate forward.
- **No prod-code edits required for Phases 1–5**: only Phase 6's structural/semantic linter cluster
  may surface REAL_BUG_FIX needs.
- **Concrete enough for executor agent**: each phase names file lists, edit shapes, expected pass deltas,
  and pre-commit hook updates. An executor can pick up Phase 1 cold and finish it in ≤3 hours.

---

## References

- `docs/operations/repo_review_2026-05-01/CI_GATE_TRIAGE_PROPOSAL.md` — earlier framing (120 failures pre-`13cbf68c`)
- `.claude/hooks/pre-commit-invariant-test.sh` — current `BASELINE_PASSED=245`, 17-file `TEST_FILES`
- `architecture/2026_04_02_architecture_kernel.sql:129` — `temperature_metric NOT NULL` schema migration
- Commits: `df5ce642` (RiskGuard cold-start), `c701c8aa` (Polymarket error matrix), `13cbf68c` (K1 obs schema)
- Background pytest output: `bfsfy8e47.output` (149 failed / 4326 passed / 111 skipped, 819s)
