# critic-dave cycle 1 re-verify — P9C ITERATE-fix at d516e6b

**Verdict: PASS.** Cycle 1 complete. P9C closes with commit `d516e6b`.

## Probe results

### P1 — Fix #2 (R-CC.3) is structural, not checkbox [PASS]

Surgical-revert probe, exactly the cycle-1 MAJOR-1 scenario:

- Replaced `src/engine/evaluator.py:738-741` (`v2_snapshot_meta = _read_v2_snapshot_metadata(...)`) with `v2_snapshot_meta = {}`.
- Ran `TestRCCBoundaryGateWired::test_dt7_gate_fires_via_evaluate_candidate_end_to_end`.
- Result: **FAILED** with the expected `AssertionError: R-CC.3 (critic-dave MAJOR-1 fix): evaluate_candidate must CALL _read_v2_snapshot_metadata inside its body` — `helper_calls_in_body == []`.
- Restored. Re-ran. Result: **PASSED** in 1.11s.

R-CC.3 is load-bearing. The AST-walk inspects `evaluate_candidate`'s function body (not just module imports), so the cycle-1 silent-replacement bypass is now caught. This is the structural antibody cycle-1 said was missing.

### P2 — Fix #1 (`_fit_from_pairs` guard) is load-bearing [PASS via AST]

R-CG.2 cannot force the guard to be executed end-to-end (empty DB → `len(pairs) < level3` early-return fires before `save_platt_model`, so simply deleting the guard would still pass that test via coincidence — dave's briefing flagged this). Fallback per brief: AST-walk confirmed the `if temperature_metric != "high": return None` is the **first non-docstring statement** in `_fit_from_pairs`'s body (`manager.py:263-270`). Call-sites at `manager.py:171-173` and `192-194` both pass `temperature_metric=temperature_metric`. Guard is structurally positioned before any pair-fetch or save path. R-CG.1/2/3: 3/3 PASSED in 1.00s.

### P3 — Regression math [PASS]

`python -m pytest -q --tb=no`:

```
144 failed, 1873 passed, 93 skipped, 7 subtests passed in 40.34s
```

Exact match to claim (144/1873/93). Delta vs pre-P9C.1 baseline 144/1869/93 = +4 passed, zero new failures; the +4 corresponds to R-CC.3 + R-CG.1 + R-CG.2 + R-CG.3.

### P4 — Redundancy scan for other LOW→legacy-save leaks [PASS]

`grep save_platt_model(` across `src/` and `scripts/`: sole production call-site of metric-blind legacy `save_platt_model` is `src/calibration/manager.py:314` — now inside the guarded `_fit_from_pairs`. All other writers route through metric-aware `save_platt_model_v2` (`scripts/refit_platt_v2.py:188`). Test call-sites in `tests/test_calibration_manager.py` / `tests/test_authority_gate.py` are fixture setup, not production paths. No secondary write-path leaks LOW into `platt_models`.

## Updated direct answer: Is dual-track main line actually closed?

**Yes.** Both seams of the L3 dual-track metric spine are now guarded in production and covered by structural antibodies:

- **READ side** (cycle-0): `get_calibrator` metric-aware + `load_platt_model_v2` filter — R-BZ + cycle-0 antibodies.
- **WRITE side** (cycle-1 fix): `_fit_from_pairs` early-returns None for non-HIGH before reaching `save_platt_model` — R-CG.1/2/3 + AST-verified guard position.
- **DT#7 evaluator wire** (cycle-1 fix): AST antibody detects any silent bypass of the `_read_v2_snapshot_metadata → boundary_ambiguous_refuses_signal → DT7 rejection` chain inside `evaluate_candidate` — R-CC.3 (surgical-revert confirmed load-bearing).

Forward-log items (on-the-fly LOW-to-v2 writer, B2 kelly_size migration, B4 truth-authority semantics, DT#7 clauses 1+2) remain out-of-scope for P9C per commit message; they are tracked, not gaps in the main-line closure.

P9C: **closed**.
