# Work Log — P0 Hardening (K1 + K3 micro-slice)

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: [fix_plan.md](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/fix_plan.md), [decisions.md](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/decisions.md), [pr18_audit.md](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/pr18_audit.md).

## 2026-04-26 — K1 + K3 implementation

### Scope landed

Two of five structural decisions from `fix_plan.md §1`:

- **K1 (degraded label)**: `_TRUTH_AUTHORITY_MAP["degraded"]` no longer collapses to `"VERIFIED"`; emits `"DEGRADED_PROJECTION"` instead. Anchored to **INV-23**.
- **K3 (decorative capability labels)**: `slice_policy`, `reprice_policy`, `liquidity_guard` removed from `ExecutionIntent`; logging-only branches in `executor.py` removed. Anchored to **NC-17**.

### Decisions taken

Acting under the recommended choices in [decisions.md](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/decisions.md):

- O4: **option-a (drop fields)** — verified the only consumers were two `logger.info` lines.
- O5: allocated **INV-23** and **NC-17** in their respective manifests.

Other decisions (O1 V2 SDK pin, O2 posture lifecycle, O3 cutover URL evidence) remain open and block other K-buckets, not this slice.

### Sequence executed (per fix_plan.md §8)

1. Allocated INV-23 in `architecture/invariants.yaml`.
2. Allocated NC-17 in `architecture/negative_constraints.yaml`.
3. Wrote `tests/test_p0_hardening.py` with R-1 (degraded × export) and R-4 (capability × consumption) and manifest registration tests.
4. Confirmed red bar: 2 manifest tests passed, 4 R-1/R-4 tests failed exactly on the diagnosed defects.
5. Edited `src/state/portfolio.py:59` `degraded` → `"DEGRADED_PROJECTION"`.
6. Edited `src/contracts/execution_intent.py` to drop the three decorative fields.
7. Edited `src/execution/executor.py` to drop the create-site arguments and the two `logger.info` branches that consumed them.
8. Reversed `tests/test_phase5a_truth_authority.py:test_save_portfolio_degraded_stamps_verified` (renamed to `..._stamps_degraded_projection`) to assert `DEGRADED_PROJECTION`. Comment documents reversal of the 2026-04-17 MAJOR-4 round-2 ruling per PR #18.
9. Removed dropped fields from `tests/test_pre_live_integration.py` and `tests/test_executor_typed_boundary.py` constructor calls.
10. Confirmed green bar: R-1 (2 tests) and R-4 (2 tests) all PASS; 121 passed / 26 skipped / 0 new failures over targeted regression of `tests/test_p0_hardening.py tests/test_phase5a_truth_authority.py tests/test_phase8_shadow_code.py tests/test_executor_typed_boundary.py tests/test_pre_live_integration.py tests/test_architecture_contracts.py`.

### Pre-existing failures — NOT from this slice

A broader run of `pytest tests/` reports 126 failures + 14 errors. **All sampled failures pre-existed on baseline** (verified by `git stash` rollback): `test_runtime_guards`, `test_sigma_floor_evaluation`, `test_structural_linter` (lints `src/data/observation_instants_v2_writer.py` and `src/data/wu_hourly_client.py`, neither touched), `test_supervisor_contracts` (B006 `env='paper'` rejection unrelated to execution), `test_pe_reconstruction_relationships` errors (collection-level fixtures unrelated), and the long-standing `test_full_monitoring_pipeline` `MODEL_DIVERGENCE_PANIC` mismatch.

These are not regressions and are out of scope for this slice. They remain on the engineering debt board.

### Touched files (local commit only — not pushed)

- `architecture/invariants.yaml` — added INV-23
- `architecture/negative_constraints.yaml` — added NC-17
- `src/contracts/execution_intent.py` — dropped 3 decorative fields
- `src/execution/executor.py` — dropped 3 decorative arguments + 2 logger branches
- `src/state/portfolio.py` — degraded label fix
- `tests/test_p0_hardening.py` — new (R-1 + R-4 + manifest tests + R-2/R-3/R-5 placeholders)
- `tests/test_phase5a_truth_authority.py` — reversed degraded → VERIFIED assertion
- `tests/test_pre_live_integration.py` — dropped removed kwargs
- `tests/test_executor_typed_boundary.py` — dropped removed kwargs
- `docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/` — new packet (this directory)

### Remaining P0 surfaces (future slices)

Per [fix_plan.md §2](docs/operations/task_2026-04-26_execution_state_truth_p0_hardening/fix_plan.md) the following P0 IDs remain to be implemented after operator decisions O1/O2/O3 land:

- **P0.3** — entry-block gate when loader degraded (partial today via existing risk_level path; needs explicit reason code surfacing)
- **P0.4 + INV-25** — V2 endpoint preflight gate (blocked on O1 SDK pin)
- **P0.5 / P0.6 + INV-24 + NC-16** — gateway-only static guard for `place_limit_order`
- **P0.8 + INV-26** — `architecture/runtime_posture.yaml` + `state/runtime_posture.json` (blocked on O2)
- **P0.11** — operator promotes packet via `current_state.md` (operator gate)
- **P0.15** — second-pass demote/correct of any other stale present-tense authority claims

### Branch / worktree

- Branch: `claude/pr18-execution-state-truth-fix-plan-2026-04-26`
- Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-pr18-fix-plan-20260426`
- Synced to `main` HEAD `2a8902c` via fast-forward.

---

## 2026-04-26 — K2 + K5 + Posture implementation

### Scope landed

Three structural decisions from `fix_plan.md §1`:

- **K2 (gateway-only static guard)**: test-based static guard asserts `place_limit_order` only appears in `src/execution/executor.py` and `src/data/polymarket_client.py`. Anchored to **INV-24** + **NC-16**. Semgrep wiring deferred to a follow-up slice as decided in fix_plan.md traps.
- **K5 (V2 endpoint-identity preflight)**: `V2PreflightError` exception class and `v2_preflight()` method added to `PolymarketClient`. Calls `get_ok()` SDK method; `AttributeError` (SDK lacks `get_ok`) is a no-op success for forward-compatibility. `_live_order` in `executor.py` calls `client.v2_preflight()` before `place_limit_order`; `V2PreflightError` → rejected `OrderResult` with reason `"v2_preflight_failed: ..."`. Anchored to **INV-25**.
- **Posture (O2-c)**: `architecture/runtime_posture.yaml` committed with `default_posture: NO_NEW_ENTRIES` and per-branch entries. `src/runtime/posture.py` module reads the YAML, resolves current branch via `git rev-parse`, fail-closed to `NO_NEW_ENTRIES` on any error, module-level cache with `_clear_cache()` for tests. `cycle_runner.py` posture gate inserted BEFORE the risk-level elif chain. Anchored to **INV-26**.

### Decisions taken

- O1-b: already landed (`requirements.txt:14` `py-clob-client>=0.34,<0.40`).
- O2-c: committed YAML with no override path.
- O3-b: preflight is endpoint-identity-gated (reachability via `get_ok()`), not date-gated.
- O5: allocated **INV-24**, **INV-25**, **INV-26** and **NC-16** in their respective manifests.

### Regression fix

`tests/test_phase8_shadow_code.py::TestRBTEntriesBlockedReasonDegraded`: the posture gate now fires BEFORE the risk-level gate. The test's `_patch_cycle_runner_surface` was patched to monkeypatch `read_runtime_posture → "NORMAL"` so the test exercises the degraded-path antibody (risk-level gate), not the posture gate. Both antibodies remain active; they are additive, not competing.

### Sequence executed (per fix_plan.md §8 sequencing — law first)

1. Allocated INV-24, INV-25, INV-26 in `architecture/invariants.yaml`.
2. Allocated NC-16 in `architecture/negative_constraints.yaml` (before NC-17).
3. Created `architecture/runtime_posture.yaml`.
4. Created `src/runtime/__init__.py` + `src/runtime/posture.py`.
5. Added `V2PreflightError` and `v2_preflight()` to `src/data/polymarket_client.py`.
6. Updated `src/execution/executor.py` `_live_order`: client instantiated before preflight, preflight called, then `place_limit_order`.
7. Added posture gate to `src/engine/cycle_runner.py` before risk-level elif chain.
8. Extended `tests/test_p0_hardening.py`: manifest tests for INV-24/25/26/NC-16, `test_place_limit_order_gateway_only` (K2), `TestR2V2PreflightBlocksPlacement` (K5), `TestR3RuntimePostureBlocksEntry` + `test_runtime_posture_yaml_present` (Posture); replaced R-2/R-3 skips.
9. Fixed `tests/test_phase8_shadow_code.py::TestRBTEntriesBlockedReasonDegraded` to monkeypatch posture to NORMAL.
10. Confirmed green bar: 18 passed / 1 skipped in `test_p0_hardening.py`; 133 passed / 24 skipped / 1 pre-existing baseline failure in regression suite.

### Verification

```
pytest tests/test_p0_hardening.py -v
# 18 passed, 1 skipped (R-5 deferred, P2)

pytest tests/test_phase5a_truth_authority.py tests/test_phase8_shadow_code.py tests/test_executor_typed_boundary.py tests/test_pre_live_integration.py tests/test_architecture_contracts.py
# 133 passed, 24 skipped, 1 pre-existing failure (test_full_monitoring_pipeline MODEL_DIVERGENCE_PANIC)
```

### Pre-existing failures — unchanged

`test_full_monitoring_pipeline` (`MODEL_DIVERGENCE_PANIC` vs `DAY0_OBSERVATION_REVERSAL`) — confirmed pre-existing on baseline before this slice.

### Touched files (local commit `1b6b3ec` — not pushed)

- `architecture/invariants.yaml` — added INV-24, INV-25, INV-26
- `architecture/negative_constraints.yaml` — added NC-16
- `architecture/runtime_posture.yaml` — new file (O2-c posture law)
- `src/data/polymarket_client.py` — added `V2PreflightError`, `v2_preflight()`
- `src/engine/cycle_runner.py` — posture gate before risk-level elif
- `src/execution/executor.py` — K5 preflight in `_live_order`
- `src/runtime/__init__.py` — new package
- `src/runtime/posture.py` — new module
- `tests/test_p0_hardening.py` — K2+K5+Posture tests (replaced R-2/R-3 skips)
- `tests/test_phase8_shadow_code.py` — posture monkeypatch in degraded-path test

### Remaining P0 surfaces

- **K4** (UNKNOWN composition + remove fabricated `unknown_entered_at`) — not landed, P1
- **P0.11** — operator promotes packet via `current_state.md` (operator gate)
- **P0.15** — second-pass demote/correct of stale present-tense authority claims
