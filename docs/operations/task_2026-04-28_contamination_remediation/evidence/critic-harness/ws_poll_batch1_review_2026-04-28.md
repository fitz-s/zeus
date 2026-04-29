# WS_OR_POLL_TIGHTENING BATCH 1 Review — Critic-Harness Gate (22nd cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-batch baseline: 128/22/0
Post-batch baseline: 137/22/0 — INDEPENDENTLY REPRODUCED

## Verdict

**REVISE** (1 MED defect on SEMANTIC-2 row-multiplication; 3 LOW caveats; verdict-direction stands; 1 fix required before BATCH 2)

The PATH A latency-only design is architecturally honest and well-defended. SEMANTIC-1 (negative clipping), SEMANTIC-3 (n_with_action JOIN), SEMANTIC-4 (PATH A scope framing) all verify correct. **HOWEVER**: SEMANTIC-2 (multi-position-per-token JOIN behavior) has a REAL OVER-COUNT defect empirically reproduced — when 2 positions of the SAME strategy hold the SAME token, 1 tick contributes n_signals=2 (over-counted 2x), skewing p50/p95 sample size with duplicated latency values.

I articulate WHY REVISE (not BLOCK):
- 9/9 cited tests pass + 137/22/0 baseline holds
- SEMANTIC-2 defect is real but bounded: production frequency depends on whether Zeus holds multi-position-per-token-per-strategy. Schema permits it; whether it OCCURS in production is unverified
- Fix is small (~5 lines): add `SELECT DISTINCT` or pre-aggregate per (token_id, strategy_key) in the SQL JOIN
- BATCH 2 (detect_reaction_gap ratio test) consumes BATCH 1's p50/p95 — if those are inflated by duplicated samples, BATCH 2's threshold logic is unreliable
- REVISE-not-BLOCK because: behavior is documented (no fix attempted yet); fix is straightforward; baseline still holds; can be addressed in BATCH 2 as a precondition

1 MED REVISE + 3 LOW caveats below.

## Pre-review independent reproduction

```
$ pytest tests/test_ws_poll_reaction.py
9 passed in 0.14s

$ pytest 9-file baseline
137 passed, 22 skipped in 3.86s

$ math: 73+6+4+7+15+4+15+4+9 = 137 ✓
```

EXACT MATCH. PASS.

## ATTACK 1 — All cited tests pass + 137/22/0 [VERDICT: PASS]

9/9 pass; baseline reproduced; arithmetic verified. PASS.

## ATTACK 2 — SEMANTIC-1 negative-latency clipping [VERDICT: PASS-WITH-NUANCE]

Implementation at L202-203: `latency_ms = max(0.0, float(zeus_ms - source_ms))`. Test `test_negative_latency_clipped_to_zero` (L290-310): inserts 1 negative + 1 positive (200ms), asserts sorted=[0, 200] → p50=100 (not [-100, 200] → p50=50). Honest about clock-skew defense.

**LOW-CAVEAT-WP-1-1 (NUANCE)**: silent clipping HIDES the negative-latency signal. Per dispatch question: an alternative (emit `negative_latency_count` as separate field) would surface clock-skew issues for operator visibility. The current design treats negatives as sensor noise; the alternative would treat them as observable clock-skew measurements.

**Recommendation**: defer to BATCH 3 CLI runner — surface count of clipped ticks in JSON report (not in core record but in metadata). Non-blocking for BATCH 1 because the core latency aggregation is correct; the clipping is a defensible default.

PASS-WITH-NUANCE.

## ATTACK 3 (CRITICAL per dispatch §SEMANTIC-2) — multi-position-per-token JOIN [VERDICT: REVISE]

Schema verification: `position_current.position_id` is PRIMARY KEY but `token_id` is NOT unique. Multiple position_ids CAN share same token_id (real production scenario: averaging-in, settled-then-re-entry, hedged positions).

SQL at L171-182: `JOIN position_current pc ON pc.token_id = tpl.token_id` produces row multiplication.

**Empirical over-count reproduction** (independent test):

| Scenario | Result |
|---|---|
| 1 token + 2 positions DIFFERENT strategies + 1 tick | each strategy n_signals=1 (defensible — "strategy X saw this tick") |
| 1 token + 2 positions SAME strategy + 1 tick | strategy n_signals=**2** (DEFECT — same tick counted twice) |

The same-strategy-multi-position case **OVER-COUNTS** by N (number of positions sharing the token). For p50/p95, this means a single high-latency tick on a 2-position-shared token contributes TWO samples at the same latency value, inflating sample_quality and biasing the percentile distribution.

**MED-REVISE-WP-1-1**: SQL JOIN at L171-182 needs deduplication. Recommended fix:
```sql
SELECT DISTINCT
    tpl.token_id,
    tpl.source_timestamp,
    tpl.timestamp AS zeus_timestamp,
    pc.strategy_key
FROM token_price_log tpl
JOIN (SELECT DISTINCT token_id, strategy_key FROM position_current
      WHERE strategy_key IS NOT NULL) pc ON pc.token_id = tpl.token_id
WHERE tpl.timestamp IS NOT NULL
```
OR: collect a SET of (zeus_ms, strategy_key) tuples in Python before appending to latencies_by_strategy, ensuring each (tick × strategy) is counted once even if multiple positions match.

**Note**: position_id is also dropped from the dedup (since we're aggregating per strategy not per position) — but ticks_by_strategy collection at L205 needs position_id retained for n_with_action computation. Fix needs care to preserve n_with_action's per-position semantic while deduping latency contributions.

**Test gap**: NO existing test exercises multi-position-per-token. Would require:
- `test_multi_position_same_strategy_same_token_does_not_overcount` (same-strategy duplicate → n_signals=1)
- `test_multi_position_different_strategies_same_token_attributes_to_each` (cross-strategy duplicate → each strategy n_signals=1)

BLOCK candidate but downgraded to REVISE because: (a) production frequency unknown (schema permits but actual occurrence depends on Zeus's position management), (b) fix is small + bounded, (c) BATCH 2 ratio test would amplify the defect — fix BEFORE BATCH 2 lands. If BATCH 2 dispatch is held pending this fix, no harm done.

REVISE.

## ATTACK 4 — SEMANTIC-3 n_with_action conflation check [VERDICT: PASS]

Re-read implementation L207-239 carefully:
- L215: `position_ids = sorted({pid for _, pid in ticks})` — collect unique position_ids from ticks
- L219-223: `SELECT position_id, occurred_at FROM position_events WHERE position_id IN (...)` — query events for THOSE position_ids only
- L233-238: for each tick (with its position_id), check if ANY events.occurred_at exists in `[tick_ms, tick_ms + 30s]` for the SAME position_id

This DOES tie tick → SAME position_id within 30s window. NOT generic position-level activity. Operator semantics (a) "Zeus reacted to THIS tick on THIS position within 30s" is what's measured.

Note: subject to SEMANTIC-2 over-count above — if a tick is row-multiplied via 2 positions sharing token, n_with_action also counts twice (once per position_id). After SEMANTIC-2 fix, n_with_action correctness depends on whether dedup retains position_id-level semantics.

PASS for n_with_action SEMANTIC; depends on SEMANTIC-2 fix outcome.

## ATTACK 5 — SEMANTIC-4 PATH A scope honesty [VERDICT: PASS]

Module docstring L18-37 verbatim:
- "PATH A 'latency-only' was chosen (PATH B heuristic-WS-vs-poll inference EXPLICITLY REJECTED per methodology §5.Z2 default-deny on heuristic-without-grounding; PATH C extending the writer is deferred to a future 'WS_PROVENANCE_INSTRUMENTATION' packet)"
- "The detector measures END-TO-END LATENCY (Zeus persist time minus venue source time) but CANNOT ATTRIBUTE individual ticks to WebSocket vs REST poll because token_price_log lacks an `update_source` column."
- "ws_share and poll_share are NOT in the return shape."

Strong framing: explicit about what the detector measures + what it does NOT measure + which packet would unlock the missing fields. Mirrors AD's KNOWN-LIMITATIONS pattern (operator-empathy section preventing misread).

PASS — framing is honest.

## ATTACK 6 — _percentile load-bearing math pin [VERDICT: PASS]

`_percentile` at L99-110 implements linear interpolation. Test `test_percentile_unit_helper` (L127-134) covers:
- Empty list → None
- Single value [42.0] → 42.0
- Two values [10.0, 20.0] @ 50% → 15.0
- Five values @ 50% → 30.0 (middle)
- Five values @ 95% → 48.0 (rank 0.95×4=3.8 → 40 + 0.8×10 = 48)

Comprehensive boundary coverage for the load-bearing math. PASS.

## ATTACK 7 — K1 compliance [VERDICT: PASS]

`grep -nE "INSERT|UPDATE|DELETE|json\.dump"` returns ZERO. Read-only SQL with `conn.execute(SELECT ...)` only. K1 contract honored.

PASS.

## ATTACK 8 — Co-tenant safety on commit 3091514 [VERDICT: PASS]

`git show 3091514 --stat` shows 6 files; all 6 packet-scoped:
- 1 NEW src/state/ws_poll_reaction.py
- 1 NEW tests/test_ws_poll_reaction.py
- 1 NEW evidence/executor/ws_poll_reaction_boot.md
- 3 EXISTING file edits (source_rationale.yaml +8 lines; test_topology.yaml +1 line; pre-commit hook BASELINE_PASSED + TEST_FILES update)

No co-tenant absorption. Commit boundary clean.

PASS.

## ATTACK 9 — Reuse of EO/AD canonical patterns + lesson carry-forward [VERDICT: PASS]

Cross-batch lesson carry-forward verified:
- Imports consolidated at top L51-58 (EO LOW-CAVEAT-EO-2-1 lesson)
- Module docstring L15-16 SELF-CITES "Tier 2 Phase 4 LOW-CAVEAT-EO-2-1 (cited by name above; mid-file imports with noqa are an anti-pattern)" ✓
- Reuses `_classify_sample_quality` from edge_observation (L58) — single source of truth for sample boundaries
- `_empty_strategy_latency_record` schema-symmetric with non-empty case (per AD pattern)
- `quarantine unknown strategy` pattern reused (L191-194)
- `window filter` pattern reused (L200-201)
- AGENTS.md framework will mirror EO/AD per BATCH 3 dispatch (will verify in BATCH 3 review)

Cross-packet pattern fidelity preserved.

PASS.

## ATTACK 10 — Beyond-dispatch findings [VERDICT: PASS-WITH-LOW]

**LOW-CAVEAT-WP-1-2 (n_with_action subtle correctness)**: implementation at L237 uses `tick_ms <= ev_ms <= tick_ms + action_window_ms` (CLOSED interval; tick at 12:00:00 with event at exactly 12:00:30.000 counts as "acted on"). Test at L327 uses 5s gap (well within window) and 60s gap (well outside) — boundary at exactly 30s NOT explicitly tested. Symmetric to EO BATCH 2 LOW-CAVEAT-EO-2-2 boundary-test gap pattern.

**LOW-CAVEAT-WP-1-3 (WS_PROVENANCE_INSTRUMENTATION packet anchor)**: future packet name cited in module docstring L23 — recommend pre-creating placeholder dispatch doc OR adding to round3_verdict §3 deferred packets list to prevent this commitment from being forgotten across sessions.

PASS-WITH-LOW.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| MED-REVISE-WP-1-1 | MED | SQL JOIN row-multiplication: 2 same-strategy positions on same token → 1 tick over-counted as n_signals=2 (empirically reproduced); inflates p50/p95 sample size + biases distribution | Add SELECT DISTINCT or pre-aggregate (token_id, strategy_key) tuples; preserve position_id retention for n_with_action; add 2 new tests (same-strategy + different-strategy multi-position-per-token cases) | **REVISE before BATCH 2** |
| LOW-CAVEAT-WP-1-1 | LOW | Negative-latency silent-clip hides clock-skew signal; alternative is `negative_latency_count` field for operator visibility | Defer to BATCH 3 CLI runner: surface count in JSON report metadata | BATCH 3 |
| LOW-CAVEAT-WP-1-2 | LOW | n_with_action 30s boundary at exactly tick+30000ms not tested; symmetric to EO BATCH 2 LOW-CAVEAT-EO-2-2 | Add test_n_with_action_boundary_at_exactly_30_seconds | BATCH 2 or 3 |
| LOW-CAVEAT-WP-1-3 | LOW | WS_PROVENANCE_INSTRUMENTATION future packet cited in docstring; no anchor in dispatch registry | Add to round3_verdict §3 deferred packets OR create placeholder TOPIC.md | Operator (post-packet) |

## Anti-rubber-stamp self-check

I have written REVISE, not APPROVE. The MED-REVISE-WP-1-1 is a real semantic defect — empirically reproduced via independent in-memory DB test where 2 same-strategy positions on shared token caused 1 tick to over-count as n_signals=2.

This is the FIRST REVISE in 22 cycles. Cycle history: 1 clean APPROVE + 17 APPROVE-WITH-CAVEATS + 4 APPROVE (from earlier rounds) + 0 REVISE + 0 BLOCK → now 1 REVISE.

The defect would have downstream impact:
- BATCH 2 detect_reaction_gap consumes p50/p95 → ratio threshold logic unreliable on inflated samples
- BATCH 3 weekly report would surface biased distribution to operator
- Schema permits the over-count case (token_id NOT unique on position_current)

Engaged each attack at face value with independent reproduction:
- Direct schema CHECK reading (token_id non-unique)
- 2 empirical over-count tests (different-strategy + same-strategy cases)
- Code path tracing through SQL JOIN row-multiplication
- Cross-batch impact analysis (BATCH 2 ratio test would amplify the defect)

22nd critic cycle in this run pattern. Same discipline applied — REVISE is appropriate when independent reproduction surfaces an empirical defect that's not in the cited tests.

## Final verdict

**REVISE** — fix MED-REVISE-WP-1-1 (SQL row-multiplication dedupe + 2 new tests) BEFORE BATCH 2 dispatch. After fix lands, re-run baseline + this critic gate; expect APPROVE-WITH-CAVEATS once dedup is in.

End WS_OR_POLL_TIGHTENING BATCH 1 review.
