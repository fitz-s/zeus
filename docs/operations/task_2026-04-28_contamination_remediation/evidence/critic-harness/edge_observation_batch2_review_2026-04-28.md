# EDGE_OBSERVATION BATCH 2 Review — Critic-Harness Gate (17th cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-batch baseline: 96/22/0 (BATCH 1 close)
Post-batch baseline: 104/22/0 — INDEPENDENTLY REPRODUCED

## Verdict

**APPROVE-WITH-CAVEATS** (2 LOW; 0 BLOCK)

BATCH 2 lands cleanly. detect_alpha_decay algorithm is semantically correct, K1-compliant, well-documented in code comment block (L181-198), and tested across 8 scenarios covering all 4 verdict states (alpha_decay_detected critical/warn, within_normal_range, insufficient_data 3 reasons). BATCH 1 LOW-REVISE-EO-1 verified FIXED in commit 6b35846.

I articulate WHY APPROVE-WITH-CAVEATS:
- 14/14 cited tests pass (8 BATCH 2 + 6 BATCH 1) in 0.10s
- 104/22/0 baseline reproduced (matches executor's claim exactly; arithmetic 73+6+4+7+14=104)
- Algorithm choice (ratio test vs OLS) defensible; rationale in code at L189-198 explicit
- Threshold semantics correct: strict less-than at decay_ratio_threshold (test_threshold_boundary)
- Insufficient-data 3 reasons (n_windows<min, usable<min, trailing_mean<=0) all tested + structurally exhaustive
- DriftVerdict dataclass schema (kind/strategy_key/severity/evidence) matches dispatch §2 design
- K1 compliance: zero DB writes/cache/sqlite3 in BATCH 2 additions (L181-333)
- Bidirectional grep clean (only test_edge_observation references the new symbols)
- LOW-REVISE-EO-1 fix verified at L102-110 (docstring now explicitly explains metric_ready vs is_degraded split + cites my BATCH 1 review by name)

2 LOW caveats below.

## Pre-review independent reproduction

```
$ pytest tests/test_edge_observation.py
14 passed in 0.10s

$ pytest 5-file baseline (architecture+settlement+inv_prototype+digest+edge_observation)
104 passed, 22 skipped in 3.51s

$ math: 73+6+4+7+14 = 104 ✓
```

EXACT MATCH. Executor claim verified.

## ATTACK 1 — All 14 cited tests pass [VERDICT: PASS]

14 passed in 0.10s. Zero failures. PASS.

## ATTACK 2 — detect_alpha_decay semantic correctness [VERDICT: PASS]

Algorithm flow (L246-333):
1. n < min_windows → insufficient_data (n_windows_below_min) ✓
2. usable_windows < min_windows → insufficient_data (usable_windows_below_min) ✓
3. trailing_mean ≤ 0 → insufficient_data (trailing_mean_non_positive) ✓
4. ratio = current/trailing_mean
5. ratio < decay_ratio_threshold → alpha_decay_detected
   - ratio < CRITICAL_RATIO_CUTOFF (0.3) → critical
   - else → warn
6. ratio ≥ threshold → within_normal_range

All 4 outcome paths exercised by tests:
- test_decay_detected_on_sudden_drop (critical: 0.02/0.10=0.2 < 0.3)
- test_decay_detected_warn_severity_at_intermediate_ratio (warn: 0.04/0.10=0.4)
- test_within_normal_range_when_steady (within: 0.09/0.10=0.9)
- test_threshold_boundary_exactly_at_cutoff (within at exactly 0.5; strict less-than)
- test_insufficient_history_below_min_windows (n<4)
- test_insufficient_when_too_many_low_sample_windows (usable<4)
- test_insufficient_when_trailing_mean_non_positive
- test_per_call_threshold_override (strict 0.8 catches 0.7 ratio)

Math verified for each: e.g., test_decay_detected_warn — trailing=[0.10,0.10,0.10,0.10], current=0.04 → ratio=0.4. 0.3 ≤ 0.4 < 0.5 → warn. ✓

PASS.

## ATTACK 3 — Algorithm choice (ratio test vs OLS) sound? [VERDICT: PASS]

Code comment L189-198 lists 3 reasons:
1. Short noisy weekly series (4-12 windows typical); OLS slope has low statistical power on small N
2. Trading-domain question is "is recent much worse than recent baseline?" not "what's the slope?"; ratio answers directly
3. trailing_mean ≤ 0 case is explicitly handled rather than producing false alarms

Independent assessment:
- For N=4-12 short series, OLS slope confidence intervals would be wide; ratio is more interpretable
- Ratio is the natural metric for "decay" framing (what fraction of baseline remains)
- The trailing_mean ≤ 0 handling IS the structural improvement over OLS (slope of all-negative series can still be "positive" in misleading ways)

Trade-off accepted: ratio test loses the ability to distinguish gradual decay from sudden drop, treating both as the same "current vs trailing" comparison. For the FIRST edge packet at coarse weekly granularity, this is appropriate. If finer-grained pattern recognition needed in future, OLS or change-point detection could be added without breaking the ratio surface.

Algorithm choice well-defended. PASS.

## ATTACK 4 — _is_window_usable_for_decay logic [VERDICT: PASS]

L235-243:
```python
def _is_window_usable_for_decay(window: dict[str, Any]) -> bool:
    if window.get("edge_realized") is None:
        return False
    if window.get("sample_quality") == "insufficient":
        return False
    return True
```

Two filters:
1. `edge_realized is None` — N=0 windows (or N>0 but all rows skipped due to metric_ready=False/missing fields) → reject
2. `sample_quality == "insufficient"` (n<10 trades) → reject (avoid noise-dominated detection)

Edge cases:
- Window with `n_trades=0` → edge_realized=None per L66 → rejected ✓
- Window with `n_trades=5` (low sample) → sample_quality="insufficient" → rejected ✓
- Window with `n_trades=10` → sample_quality="low" → ACCEPTED (boundary correct per BATCH 1 SAMPLE_QUALITY_BOUNDARIES) ✓

Test_insufficient_when_too_many_low_sample_windows uses n_trades=5 to trigger insufficient; verifies 3-of-5 unusable yields usable_windows_below_min. ✓

PASS.

## ATTACK 5 — trailing_mean_non_positive handling [VERDICT: PASS]

L300-310: when `trailing_mean <= 0`, returns `insufficient_data` with reason="trailing_mean_non_positive" + evidence containing trailing_mean + current_edge + n_trailing.

Rationale at L298-299: "A strategy that never had positive edge cannot meaningfully 'decay'." Mathematically sound — ratio with non-positive denominator is undefined or sign-flipping.

Test_insufficient_when_trailing_mean_non_positive: trailing=[-0.05, -0.02, 0.0, -0.01], mean = -0.02 → returns insufficient with correct reason. ✓

Edge case: `trailing_mean == 0` exactly — also caught by `<= 0` (not `< 0`). Defensive. ✓

PASS.

## ATTACK 6 — Per-call threshold override [VERDICT: PASS]

L250: `decay_ratio_threshold: float = DEFAULT_DECAY_RATIO_THRESHOLD` (kwarg with default).

Test_per_call_threshold_override: trailing=0.10, current=0.07 → ratio=0.7
- Default threshold 0.5 → 0.7 ≥ 0.5 → within_normal_range ✓
- Override threshold=0.8 → 0.7 < 0.8 → alpha_decay_detected with severity=warn (0.7 > 0.3 critical cutoff) ✓
- Evidence contains overridden threshold value (0.8) ✓

PASS.

## ATTACK 7 — K1 compliance [VERDICT: PASS]

`grep -nE "INSERT|UPDATE|DELETE|conn\.|cache|persist|json\.dump|sqlite3"` on BATCH 2 additions (L181-333) returns: only L7 (a docstring mention "no cache").

No DB writes. No JSON persistence. No parallel cache. Pure-Python algorithm consuming a list of dicts. K1-compliant. ✓

PASS.

## ATTACK 8 — DriftVerdict dataclass schema [VERDICT: PASS]

L207-226:
```python
@dataclass
class DriftVerdict:
    kind: DriftKind  # alpha_decay_detected | within_normal_range | insufficient_data
    strategy_key: str
    severity: DriftSeverity | None = None  # info | warn | critical, only on alpha_decay
    evidence: dict[str, Any] = field(default_factory=dict)
```

Matches dispatch §2 design. Fields:
- kind: 3-state Literal — closed enum ✓
- strategy_key: str — caller's input echoed back for downstream attribution ✓
- severity: optional Literal — only set when kind==alpha_decay_detected ✓
- evidence: dict — numeric inputs preserved for review ✓

Field defaults via `field(default_factory=dict)` correctly avoid mutable-default anti-pattern. ✓

PASS.

## ATTACK 9 — Docstring vs implementation coherence (LOW-REVISE-EO-1 type drift check) [VERDICT: PASS]

BATCH 1 fix verified at L102-110: docstring now explicitly:
- Says `metric_ready=False` filter (matches implementation L138)
- Notes `is_degraded=True` rows with valid metric_ready ARE included
- Cites critic-harness BATCH 1 review (LOW-REVISE-EO-1) as the source of the fix
- Explains measurement-vs-learning rationale

BATCH 2 docstrings:
- DriftVerdict class docstring (L208-222): kind/severity/evidence semantics match implementation logic ✓
- detect_alpha_decay function docstring (L253-267): args + returns + threshold defaults match impl ✓
- _is_window_usable_for_decay (L236-238): docstring matches L239-243 logic ✓
- Module-level comment block L181-198: algorithm rationale + K1 promise match implementation ✓

No docstring-vs-impl drift detected in BATCH 2.

PASS.

## ATTACK 10 — Beyond dispatch: late-import block + style audit [VERDICT: PASS-WITH-LOW-CAVEATS]

**LOW-CAVEAT-EO-2-1 (style)**: L200-201 has imports mid-file with `noqa: E402`:
```python
from dataclasses import dataclass, field   # noqa: E402 — appended after top imports
from typing import Literal                  # noqa: E402
```

`Any` is already imported at top (L35). Standard Python convention is consolidating all imports at top. The noqa explicitly waives PEP-8 / flake8 E402 ("module level import not at top of file").

Defensible: BATCH 2 was an additive append, the comment "appended after top imports" maintains batch boundary visibility, and the lint waiver is honest. But it's unusual style.

**Recommend (non-blocking)**: in a future cleanup pass, move BATCH 2 imports to the top alongside `Any`. Single import block is more maintainable.

**LOW-CAVEAT-EO-2-2 (semantic edge case)**: at L322 `severity = "critical" if ratio < CRITICAL_RATIO_CUTOFF else "warn"` — boundary at exactly ratio=CRITICAL_RATIO_CUTOFF (0.3) yields warn (strict less-than; consistent with the threshold-boundary discipline at L321). Not tested explicitly.

Test_decay_detected_on_sudden_drop uses ratio=0.2 (clearly critical); test_decay_detected_warn uses ratio=0.4 (clearly warn). The boundary at exactly 0.3 is not exercised. **Consider adding a test for exactly ratio==0.3 → warn (matching the threshold_boundary pattern).** Non-blocking.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| LOW-CAVEAT-EO-2-1 | LOW (style) | Mid-file imports at L200-201 with noqa: E402; Any already at top | Consolidate to single top-level import block in future cleanup | Engineering executor (next pass) |
| LOW-CAVEAT-EO-2-2 | LOW (test gap) | Boundary at exactly ratio==CRITICAL_RATIO_CUTOFF (0.3) not explicitly tested; matches threshold_boundary discipline elsewhere | Add test_critical_cutoff_boundary_exactly_at_0_3 → warn | Engineering executor (next pass) |

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The 2 LOW caveats are real:
- LOW-CAVEAT-EO-2-1: noqa-waived mid-file imports are unusual style; standard Python practice consolidates imports
- LOW-CAVEAT-EO-2-2: critical-vs-warn boundary at exactly ratio==0.3 not tested — symmetric gap to the threshold_boundary test (which IS present at exactly 0.5)

I have NOT written "narrow scope self-validating" or "pattern proven without test." I engaged the strongest claims (algorithm choice + semantic correctness + K1 compliance) at face value and verified each via:
- Direct source code read of L181-333
- Algorithm trace through 4 verdict outcome paths
- Math verification for each test scenario (ratio computations + threshold/cutoff comparisons)
- BATCH 1 LOW-REVISE-EO-1 fix confirmed at L102-110 (with explicit critic-name citation)
- K1 compliance grep
- Bidirectional grep on new symbols
- Hook arithmetic 73+6+4+7+14=104 verified

17th critic cycle in this run pattern. Same discipline applied throughout. The BATCH 1 → BATCH 2 sequence shows healthy critic-execution loop: caveat surfaced → fix landed → docstring now self-cites the lesson.

## Final verdict

**APPROVE-WITH-CAVEATS** — BATCH 2 closes cleanly; algorithm choice well-defended; K1 compliance maintained; no docstring drift in BATCH 2 additions; BATCH 1 caveat verified fixed. Recommend engineering executor address 2 LOW caveats in BATCH 3 or follow-up pass.

End EDGE_OBSERVATION BATCH 2 review.
