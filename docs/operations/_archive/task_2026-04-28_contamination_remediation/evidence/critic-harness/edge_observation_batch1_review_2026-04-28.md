# EDGE_OBSERVATION BATCH 1 Review — Critic-Harness Gate (16th cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine; reviewing files at executor's worktree at /Users/leofitz/.openclaw/workspace-venus/zeus/)
Pre-batch baseline: 90/22/0 (4-file critic baseline at my HEAD `0a9ec93`)
Post-batch baseline (executor side): 96/22/0 — INDEPENDENTLY REPRODUCED

## Verdict

**APPROVE-WITH-CAVEATS** (1 LOW REVISE on docstring drift; 0 BLOCK conditions)

The CRITICAL DESIGN INSIGHT (metric_ready vs is_degraded semantic split) is **VERIFIED CORRECT** — executor got it right. Implementation is K1-compliant; phantom-PnL antibody (dedupe-trade-twice) tested; all 6 tests pass + 96/22/0 baseline reproduced. 1 LOW caveat on a function-docstring-vs-implementation drift that contradicts the module docstring.

I articulate WHY APPROVE-WITH-CAVEATS:
- 6/6 cited tests pass independently in 0.10s
- 96/22/0 baseline reproduced (matches executor's claim exactly)
- metric_ready vs is_degraded semantic split — verified correct via direct source-code read of `_normalize_position_settlement_event` (db.py:3283-3348)
- 4 enforcement surfaces validated (canonical query path, dedup, schema CHECK, K1 read-only)
- Hook BASELINE_PASSED=96 arithmetic correct (73+6+4+7+6=96)
- source_rationale.yaml + test_topology.yaml mesh maintenance complete

1 LOW caveat below.

## Pre-review independent reproduction

```
$ pytest tests/test_edge_observation.py
6 passed in 0.10s

$ pytest 5-file baseline (4 critic + edge_observation)
96 passed, 22 skipped in 3.57s
```

EXACT MATCH 96/22/0. Executor claim verified.

## ATTACK 1 — All 6 cited tests pass [VERDICT: PASS]

6 passed in 0.10s. Zero failures. PASS.

## ATTACK 2 — Edge formula correctness [VERDICT: PASS]

`compute_realized_edge_per_strategy` at L92-171 implements:
- **Window**: `[end - window_days, end]` half-open via SQL `not_before` filter + post-filter `settled_at[:10] > window_end` exclusion (L153-155)
- **Per-strategy aggregation**: `edge_sums[strategy] += float(outcome) - float(p_post)` (L159) — exact `outcome - p_posterior` per row
- **Mean**: `rec["edge_realized"] = edge_sums[sk] / n` (L167)
- **Win rate**: `rec["win_rate"] = rec["n_wins"] / n` where wins counted at L160-161

`test_per_strategy_aggregation_correctness` (L114-154) covers the math: 3 settlement_capture trades → edge_sum 0.4 + (-0.4) + 0.5 = 0.5; mean = 0.5/3 ✓; n_wins=2; win_rate=2/3 ✓.

PASS.

## ATTACK 3 — Sample quality boundaries 10/30/100 [VERDICT: PASS]

`SAMPLE_QUALITY_BOUNDARIES` at L44-49: insufficient<10, low<30, adequate<100, high>=100.

`_classify_sample_quality` at L52-60 implements correct boundary semantics: `n < 10 → insufficient`, `n < 30 → low`, `n < 100 → adequate`, else `high`.

`test_sample_quality_boundaries` at L157-166 verifies exact boundaries (0, 9, 10, 29, 30, 99, 100, 1000). PASS.

## ATTACK 4 (CRITICAL) — metric_ready vs is_degraded semantic [VERDICT: PASS]

**Independent verification of the executor's load-bearing design insight via direct source-code read of db.py:3283-3348**:

`_normalize_position_settlement_event` defines TWO orthogonal flags:
- L3326 `metric_ready=False` set ONLY when `missing_required` from `AUTHORITATIVE_SETTLEMENT_ROW_REQUIRED_FIELDS` is non-empty (L3314-3319)
- L3340 `is_degraded=True` set when `degraded_reasons` non-empty, which includes `missing_decision_snapshot_id` (L3337-3338) — a snapshot field NOT in the required list

Required fields list at L89-99 includes: trade_id, city, target_date, range_label, direction, **p_posterior**, **outcome**, pnl, settled_at. Note `decision_snapshot_id` is NOT in the required list.

So:
- Row missing `outcome` → metric_ready=False (cannot measure) AND is_degraded=True
- Row missing only `decision_snapshot_id` → metric_ready=True, is_degraded=True (cannot learn but CAN measure)
- Row missing nothing → metric_ready=True, is_degraded=False

Executor's filter `not row.get("metric_ready")` at L138 is the CORRECT MEASUREMENT-vs-LEARNING semantic split. Filtering wider on `is_degraded=True` would EXCLUDE rows with valid outcome+p_posterior but missing decision_snapshot_id, losing data unnecessarily for the edge measurement use case.

This is the right call. The executor's design insight is **VERIFIED HONEST**.

PASS.

## ATTACK 5 — Phantom-PnL trap (dedupe-trade-twice) [VERDICT: PASS]

`test_per_strategy_aggregation_correctness` at L138-140 inserts the SAME `position_id="p1"` twice (with `seq_no=1` and `seq_no=2`). Test asserts at L144 `n_trades == 3` (NOT 4). This verifies `query_authoritative_settlement_rows` dedupe via `ROW_NUMBER()` keeps only the most-recent.

K1 read-only verified: grep for INSERT/UPDATE/DELETE/cache/persist/json.dump in edge_observation.py returns ZERO matches in code (only in docstring declarations of NO write/cache).

PASS.

## ATTACK 6 — Schema CHECK enforcement [VERDICT: PASS]

`test_strategy_filter_only_4_known` at L237-240 uses `pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed")` to verify the schema CHECK at architecture/2026_04_02_architecture_kernel.sql:53-58 rejects unknown strategy_key.

Independent verification: schema file L53-58 has `strategy_key TEXT NOT NULL CHECK (strategy_key IN ('settlement_capture', 'shoulder_sell', 'center_buy', 'opening_inertia'))`. ✓

PASS.

## ATTACK 7 — Mesh maintenance correctness [VERDICT: PASS]

- `architecture/source_rationale.yaml` entry for `src/state/edge_observation.py`: zone K2_runtime, authority_role derived_edge_projection, full why with K1 contract description + history_lore reference + metric_ready filter explanation. Hazards block + companion_scripts. Rich entry following BATCH-D pattern.
- `architecture/test_topology.yaml` entry registers `tests/test_edge_observation.py` with created+last_used dates. ✓

Both YAML edits valid (pytest baseline preserved → no parse errors). PASS.

## ATTACK 8 — Hook BASELINE_PASSED 79→96 arithmetic [VERDICT: PASS]

Hook at `.claude/hooks/pre-commit-invariant-test.sh`:
- TEST_FILES extended to 5: test_architecture_contracts + test_settlement_semantics + test_digest_profiles_equivalence + test_inv_prototype + test_edge_observation
- BASELINE_PASSED=96

Math: 73 (test_architecture_contracts) + 6 (test_settlement_semantics) + 4 (test_digest_profiles_equivalence) + 7 (test_inv_prototype) + 6 (test_edge_observation) = **96** ✓

Independent baseline run reproduces 96/22/0 exactly. PASS.

## ATTACK 9 — K1 compliance + bidirectional grep [VERDICT: PASS]

Bidirectional grep:
- Forward: `edge_observation.py` imports only `query_authoritative_settlement_rows` from db.py — single dependency on canonical query surface ✓
- Reverse: only test_edge_observation.py references the new module (no callers yet — first edge packet) ✓

K1 compliance verified — module is read-only with no parallel cache, no JSON persistence, no write path. Module docstring at L9-22 explicitly states this contract.

PASS.

## ATTACK 10 — Function docstring drift vs implementation [VERDICT: REVISE]

**LOW-REVISE-EO-1**: Function docstring at L99-102 of `compute_realized_edge_per_strategy` says:
> "Skips degraded rows (is_degraded=True) and rows missing outcome or p_posterior."

But the implementation at L138 actually uses:
```python
if not row.get("metric_ready"):
    continue
```

**Drift**: function docstring says `is_degraded=True` filter; implementation uses `metric_ready=False` filter. The module docstring at L14-18 IS correct (mentions metric_ready and explicitly explains the split). The discrepancy is between the module docstring (correct) and the function docstring (wrong, contradicts the module docstring).

This is the same kind of comment-vs-code drift that the executor's load-bearing design insight (metric_ready vs is_degraded SEMANTIC SPLIT) is exactly designed to PREVENT. A future agent reading only the function docstring would believe the filter is on is_degraded, then write code based on wrong assumption. Per Fitz Constraint #2 (translation loss thermodynamic) — the next session reading only the function docstring loses the design insight.

**RECOMMEND**: update function docstring at L99-102 to:
```
"K1-compliant read-only projection. Reads canonical SETTLED events via
query_authoritative_settlement_rows (which dedupes by position_id and
normalizes via _normalize_position_settlement_event). Skips rows with
metric_ready=False (missing required fields like outcome/p_posterior);
keeps metric_ready=True rows even if is_degraded=True (e.g., missing
only decision_snapshot_id) — those are valid for edge MEASUREMENT
even though invalid for Platt re-fit / LEARNING. See module docstring
above for the measurement-vs-learning rationale."
```

Non-blocking; recommended fix in next pass. The implementation behavior is correct; only the function-level documentation is inconsistent with itself (module docstring vs function docstring).

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| LOW-REVISE-EO-1 | LOW | Function docstring at L99-102 contradicts module docstring at L14-18 (says is_degraded filter; implementation uses metric_ready filter; module doc is correct) | Update function docstring per recommended text above | Engineering executor (next pass; non-blocking) |

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The LOW-REVISE-EO-1 caveat is a real comment-vs-code drift — and ironically it contradicts the very design insight executor flagged for my review. The function docstring under-documents what the module docstring correctly explains.

The CRITICAL design insight challenge from dispatch §4 — I independently read db.py:3283-3348, traced the AUTHORITATIVE_SETTLEMENT_ROW_REQUIRED_FIELDS list at L89-99, and confirmed that `outcome` IS in required (so `is_degraded_payload=True` test fixture removing outcome correctly triggers metric_ready=False). The semantic split is verified honest.

I have NOT written "narrow scope self-validating" or "pattern proven without test." I engaged the strongest claim (executor's metric_ready vs is_degraded split is the right call) at face value and verified via:
- Direct source code read of `_normalize_position_settlement_event`
- Required-fields list inspection
- Trace of test fixture's `is_degraded_payload` parameter through the normalization path
- 6/6 test pass independent reproduction
- Hook arithmetic verification
- K1 compliance grep
- Bidirectional grep for callers

16th critic cycle in this run pattern. Same discipline applied throughout.

## Final verdict

**APPROVE-WITH-CAVEATS** — BATCH 1 lands cleanly; design insight verified honest; baseline preserved; recommend engineering executor fix the function-docstring drift in next pass for full coherence.

End EDGE_OBSERVATION BATCH 1 review.
