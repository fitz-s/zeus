# CALIBRATION_HARDENING BATCH 2 Review — Critic-Harness Gate (28th cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-batch baseline: 170/22/0 (post BATCH 1; cycle 27 LOCKED)
Post-batch baseline: 181/22/0 — INDEPENDENTLY REPRODUCED (with ZEUS_MODE=live env)
Scope: BATCH 2 detect_parameter_drift ratio detector + ParameterDriftVerdict (commit 85f1e7b)

## Verdict

**APPROVE-WITH-CAVEATS** (1 LOW citation drift; 0 BLOCK; 0 REVISE)

Per-coefficient ratio test cleanly mirrors WP BATCH 2 strict-greater-than threshold + inclusive critical-cutoff semantics. All 8 SEMANTIC concerns + 11 ATTACK probes verified PASS independently via Python REPL. Cycle-27 LOW-CITATION-CALIBRATION-1-1 RESOLVED (cite updated to manager.py:172-189 model-fallback-load at both module docstring L292 and L320 comment; verified manager.py L172-189 IS the model-load surface). Per-coefficient evidence (drifting_coefficients list + per-(A,B,C) ratios + max_ratio + n_windows) provides operator-grade observability mirroring WP BATCH 2 multi-axis surfacing. K1 maintained (0 INSERT/UPDATE/DELETE in BATCH 2 additions). Co-tenant safety preserved (3 files exactly).

1 NEW LOW: dispatch + commit cite "ws_poll_reaction.py:447" but actual `severity = "critical" if ratio >= critical_ratio_cutoff else "warn"` is at L459 (boundary off by 12 lines). Sibling-pattern of the BATCH 1 cite drift; non-blocking.

## Pre-review independent reproduction

```
$ ZEUS_MODE=live pytest tests/test_calibration_observation.py
25 passed in 0.18s

$ ZEUS_MODE=live pytest 11-file baseline
181 passed, 22 skipped in 5.34s
```

## ATTACK 1 — 181/22/0 baseline + 25 calibration_observation tests [VERDICT: PASS]

25/25 tests PASS in 0.18s. 11-file baseline reproduced 181/22/0. Hook BASELINE_PASSED=181 honored. PASS.

## ATTACK 2 — Boundary semantics: ratio==1.5 within / ratio==2.0 critical [VERDICT: PASS]

Independent verification via Python REPL:
- trailing=[-1,1,-1,1] → mean=0, pop_std=1.0
- current=1.5 → ratio=1.5 EXACTLY → `within_normal` ✓ (strict > threshold; ratio==threshold does NOT trigger)
- current=2.0 → ratio=2.0 EXACTLY → `drift_detected` severity=`critical` ✓ (>= cutoff inclusive)

Implementation verified at L536-541 (strict > for drifting_coefficients append) + L557 (`>=` for critical severity). LOW-CAVEAT-EO-2-2 lesson honored. Tests 6+7 pin both boundaries explicitly.

PASS.

## ATTACK 3 — Per-coefficient evidence completeness [VERDICT: PASS]

Synthetic probe with 5-window history where ONLY param_B drifts (jump from baseline≈1.0 to 5.0 with low spread):
```
drifting=['param_B']
A_ratio: 0.707  ← still in evidence even though A doesn't drift
B_ratio: 56.569 ← the load-bearing drift
C_ratio: 0.707  ← still in evidence even though C doesn't drift
```

Evidence dict at L513-521 ALWAYS surfaces all 3 coefficient sub-dicts (current + trailing_mean + ratio per coeff). Test 8 (test_drift_per_coefficient_evidence_surfaces_all_three) pins this. Operator-observability strong; matches WP BATCH 2 multi-axis precedent.

PASS.

## ATTACK 4 — insufficient_data graceful: trailing_std<=0 → no false drift [VERDICT: PASS]

Independent probe with all-equal history `[w(1), w(1), w(1), w(1), w(1)]`:
```
insufficient_data reason=all_trailing_stds_non_positive
```

`_coefficient_ratio` at L439-440 returns ratio=None when `trailing_std is None or trailing_std <= 0`. `valid_ratios = [r for r in (a_ratio, b_ratio, c_ratio) if r is not None]` at L523. `if not valid_ratios:` → emit insufficient_data with `reason=all_trailing_stds_non_positive`. Test 2 pins this.

NO false drift_detected when constants. PASS.

## ATTACK 5 — min_windows guard: n < min_windows → insufficient regardless of std [VERDICT: PASS]

Probe with n=3 < min_windows=4: `kind=insufficient_data, reason=n_windows_below_min`. Implementation at L491-501 returns early BEFORE std computation. Test 1 pins this.

Empty list also returns insufficient_data via same guard (n=0 < 4).

PASS.

## ATTACK 6 — Defaults match siblings 1.5 / 2.0 / 4 [VERDICT: PASS]

Sibling-coherence verified via grep:
| Constant | EO BATCH 2 | WP BATCH 2 | Calibration BATCH 2 |
|---|---|---|---|
| threshold_multiplier | 0.5 (DECAY direction) | 1.5 (INFLATION) | 1.5 (DRIFT magnitude) |
| critical_ratio_cutoff | 0.3 (cycle 24) | 2.0 | 2.0 |
| min_windows | 4 | 4 | 4 |

EO direction-inverted (decay = lower-is-worse → 0.5/0.3); WP + Calibration are magnitude-direction (higher-is-worse → 1.5/2.0). Sibling-coherent direction discipline preserved.

Test 11 (test_drift_defaults_match_sibling_packets) explicitly pins this.

PASS.

## ATTACK 7 — Per-call kwarg override works [VERDICT: PASS]

Probe: trailing=[-1,1,-1,1], current=1.4 → ratio=1.4
- Default thr=1.5 → `within_normal` (1.4 not > 1.5) ✓
- Override thr=1.2 → `drift_detected` severity=`warn` (1.4 > 1.2 + 1.4 < 2.0) ✓

evidence.drift_threshold_multiplier reflects override value via L517. Test 9 pins this.

Override mechanism mechanically clean. BATCH 3 weekly runner can wire per-bucket thresholds via this kwarg. PASS.

## ATTACK 8 — K1 compliance [VERDICT: PASS]

`grep -E "^\+" | grep -cE "INSERT|UPDATE|DELETE|json.dump"` on BATCH 2 diff returns 0. Pure read-side detector over BATCH 1 dict outputs. K1 contract preserved.

PASS.

## ATTACK 9 — LOW-CITATION-CALIBRATION-1-1 cite-drift fix verified [VERDICT: PASS]

Cycle-27 LOW-CITATION-CALIBRATION-1-1: BATCH 1 cited "manager.py L42-62 v2-then-legacy fallback dedup" but L42-62 is the WARNING dedup helper.

Verification of cycle-28 fix:
- L292 docstring (BATCH 1 area): "Mirrors src/calibration/manager.py L172-189 v2-then-legacy fallback model-load pattern (NOT the L42-62 warning dedup helper — per LOW-CITATION-CALIBRATION-1-1 fix from critic 27th cycle review)."
- L320 inline comment: "Sibling-coherent with manager.py L172-189 v2-then-legacy model-fallback-load (per LOW-CITATION-CALIBRATION-1-1 fix: the L42-62 helper in manager.py is the WARNING dedup, NOT the model-load; this comment cites the model-load precedent)."

Independently re-verified manager.py L172-189: contains the actual `load_platt_model_v2(...) → if model_data is None and temperature_metric == "high": load_platt_model(...)` v2-then-legacy pattern at L171-181, plus the warning emission at L188-189. The cycle-28 fix is honest and correct.

PASS.

## ATTACK 10 — ParameterDriftVerdict shape mirrors ReactionGapVerdict [VERDICT: PASS]

Side-by-side comparison:
| Field | ReactionGapVerdict (WP) | ParameterDriftVerdict (Calibration) |
|---|---|---|
| kind | Literal[3-value] | Literal[3-value] (drift_detected/within_normal/insufficient_data) |
| identity | strategy_key | bucket_key |
| severity | Literal[warn,critical] \| None | Literal[warn,critical] \| None |
| evidence | dict[str, Any] = field(default_factory=dict) | dict[str, Any] = field(default_factory=dict) |

Schema-level mirror; only the identity field name differs (strategy_key vs bucket_key) — appropriate for the per-axis identity scope. PASS.

## ATTACK 11 — Co-tenant safety on commit 85f1e7b [VERDICT: PASS]

`git show 85f1e7b --name-only` confirms EXACTLY 3 files (matches dispatch claim):
1. `.claude/hooks/pre-commit-invariant-test.sh`
2. `src/state/calibration_observation.py`
3. `tests/test_calibration_observation.py`

`git status -s` shows 15 unstaged co-tenant edits + 1 untracked task dir + 1 untracked critic markdown (cycle 27 review). Executor correctly left ALL of these unstaged.

PASS.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| LOW-CITATION-CALIBRATION-2-1 | LOW (citation drift; sibling-pattern with 27th cycle catch) | Module docstring + commit cite "ws_poll_reaction.py:447" for `severity = "critical" if ratio >= critical_ratio_cutoff else "warn"` precedent. Actual location in WP BATCH 2 source is L459 (the conditional sits at L457-459; the dispatch + commit said L447). 12-line drift on the cite. The semantic precedent is correct (>= for critical-tier) — only the line number is off. | Update module docstring L355 + commit-message references to point at ws_poll_reaction.py:457-459 OR drop the line-specific cite and reference function/contract instead. Operator-readable; non-blocking. | Executor BATCH 3 or post-packet hardening |

This LOW is sibling-pattern with cycle-27 LOW-CITATION-CALIBRATION-1-1 (cite-drift on a precedent line range). The cycle-28 fix correctly resolved the BATCH 1 cite; this is a NEW citation drift in the BATCH 2 area pointing to the WP precedent. Suggest a discipline note: when citing precedent lines, grep + paste the actual line number at write-time (10-min citation-rot risk per memory `feedback_grep_gate_before_contract_lock`).

Cycle-27 LOW-NUANCE-CALIBRATION-1-2 (bootstrap_count vs usable count) DEFERRED to BATCH 3 per dispatch.

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The LOW citation drift on the WP precedent is real but minor (semantic precedent correct; only line number off).

Notable rigor:
- INDEPENDENTLY reproduced all 8 boundary + edge SEMANTIC tests via Python REPL probe (not trusted dispatch claims)
- Constructed synthetic data with specific math properties (trailing=[-1,1,-1,1] → mean=0, pop_std=1.0) to verify boundary at EXACTLY 1.5 and 2.0 (rather than approximate)
- Verified per-coefficient evidence completeness with B-only-drifts probe; confirmed A+C ratios still surfaced even when not drifting
- Verified PARTIAL-std case (only A has trailing variance) yields correct drift_detected with B+C ratios None — important edge case for early-fit data
- Verified LOW-CITATION-CALIBRATION-1-1 fix by reading manager.py L172-189 directly + confirming it IS the model-fallback-load
- Caught NEW LOW-CITATION-CALIBRATION-2-1 (ws_poll_reaction.py:447 cite drift, actual L459) via grep on the cited symbol — 12-line drift
- Sibling defaults grep-verified across EO + WP + Calibration packets (1.5/2.0/4 magnitude-direction; EO 0.5/0.3 decay-direction inverted)
- Bidirectional grep CLEAN: ZERO references to detect_parameter_drift / ParameterDriftVerdict outside calibration_observation.py + tests
- K1 verified via "+lines only" grep (excludes pre-existing INSERT/UPDATE/DELETE noise from base file)

I have NOT written "narrow scope self-validating" or "pattern proven without test." 28 cycles of anti-rubber-stamp discipline maintained.

28th critic cycle. Cycle metrics: 28 cycles, 3 clean APPROVE, 22 APPROVE-WITH-CAVEATS, 1 REVISE earned + resolved cleanly, 0 BLOCK.

## Final verdict

**APPROVE-WITH-CAVEATS** — CALIBRATION_HARDENING BATCH 2 lands cleanly with sibling-coherent ratio-test detector + per-coefficient operator-observable evidence; cycle-27 LOW-CITATION-CALIBRATION-1-1 RESOLVED; 11 ATTACK probes pass; 1 NEW LOW citation drift (WP precedent line number) tracks forward to BATCH 3 or post-packet hardening.

Authorize push of 85f1e7b → CALIBRATION_HARDENING BATCH 2 LOCKED. Ready for GO_BATCH_3 dispatch (weekly runner + AGENTS.md + e2e tests).

End CALIBRATION_HARDENING BATCH 2 review.
End 28th critic cycle.
