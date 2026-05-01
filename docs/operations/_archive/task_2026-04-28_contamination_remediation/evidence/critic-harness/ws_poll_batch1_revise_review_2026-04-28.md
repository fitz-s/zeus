# WS_OR_POLL_TIGHTENING BATCH 1 REVISE Re-Review — Critic-Harness Gate (23rd cycle)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-revise baseline: 137/22/0 (BATCH 1 close pre-revise)
Post-revise baseline: 139/22/0 — INDEPENDENTLY REPRODUCED
Scope: narrow re-review of MED-REVISE-WP-1-1 fix; 3 LOW caveats from 22nd cycle remain track-forward

## Verdict

**APPROVE** (clean — no caveats; MED-REVISE-WP-1-1 fix verified via independent re-reproduction of both my 22nd-cycle empirical defect cases)

The fix correctly addresses the row-multiplication defect with surgical scope. Both my 22nd-cycle empirical reproductions now PASS:
- **Same-strategy multi-position** (the defect case): `n_signals=1` (was 2 pre-fix)
- **Different-strategy multi-position** (the defensible case): each strategy `n_signals=1` (legitimate cross-strategy attribution preserved — fix did NOT collapse it)

n_with_action ANY-of-set semantic verified via 2 additional independent edge-case tests:
- Tick + 2 positions, only p1 has event within 30s → `n_with_action=1` ✓
- Tick + 2 positions, neither has event within 30s → `n_with_action=0` ✓

## Pre-review independent reproduction

```
$ pytest tests/test_ws_poll_reaction.py
11 passed in 0.18s

$ pytest 9-file baseline
139 passed, 22 skipped in 4.53s

$ math: 73+6+4+7+15+4+15+4+11 = 139 ✓
```

## ATTACK 1 — 11 ws_poll tests + 139/22/0 baseline [VERDICT: PASS]

11/11 pass; baseline reproduced; arithmetic verified. PASS.

## ATTACK 2 — SELECT DISTINCT semantics: empirical reproduction of 22nd-cycle defect [VERDICT: PASS]

Independently re-ran both 22nd-cycle in-memory DB tests against FIXED code:

| Case | Pre-fix | Post-fix | Status |
|---|---|---|---|
| 2 same-strategy positions + same token + 1 tick | n_signals=2 (defect) | n_signals=1 ✓ | FIXED |
| 2 different-strategy positions + same token + 1 tick | each n_signals=1 (defensible) | each n_signals=1 ✓ | PRESERVED |

The fix correctly:
- Deduplicates same-(token, strategy) combinations via SELECT DISTINCT on (token_id, source_timestamp, zeus_timestamp, strategy_key)
- Preserves cross-strategy attribution (different strategies still get their own n_signals=1 from a shared tick)

This is the load-bearing verification — fix mechanically resolves the over-count without breaking the legitimate attribution semantics.

PASS.

## ATTACK 3 — n_with_action ANY-of-set semantic [VERDICT: PASS]

Implementation refactor (L259-280 of REVISE diff):
- Builds `pos_map: (token_id, strategy_key) → set[position_id]` via separate query L233-242
- For each tick, attaches candidate position_ids: `ticks_by_strategy[strategy_key].append((zeus_ms, pids))`
- ANY-of-set check L267-275: iterates pid_set; if any candidate position has event in `[tick_ms, tick_ms + action_window_ms]`, sets `acted = True`

Independent edge-case verification:
- 2 positions same strategy/token + 1 tick + p1 event 5s later (within 30s) + p2 event 60s later (outside) → `n_with_action=1` (ANY-of-set: p1 satisfied)
- 2 positions same strategy/token + 1 tick + only p2 event 60s later (outside) → `n_with_action=0`

ANY-of-set correctly preserves position-level granularity for n_with_action while avoiding latency-inflation. Per executor commit message: "preserves the position-level granularity n_with_action legitimately requires while avoiding the latency-inflation defect" — verified accurate.

PASS.

## ATTACK 4 — K1 compliance maintained [VERDICT: PASS]

`grep -nE "INSERT|UPDATE|DELETE|json\.dump"` on src/state/ws_poll_reaction.py returns ZERO. Both queries (latency JOIN + position map) are SELECT-only. K1 contract preserved.

PASS.

## ATTACK 5 — Original 9 BATCH 1 tests still pass [VERDICT: PASS]

Pytest verification: 11 collected (9 original BATCH 1 + 2 NEW MED-REVISE regression). All 11 PASS in 0.18s. No semantic regression introduced by the refactor beyond the targeted fix.

PASS.

## ATTACK 6 — Co-tenant safety on commit 3a10f1a [VERDICT: PASS]

`git show 3a10f1a --stat` shows EXACTLY 3 files:
- `.claude/hooks/pre-commit-invariant-test.sh` (BASELINE_PASSED 137→139)
- `src/state/ws_poll_reaction.py` (+85/-24)
- `tests/test_ws_poll_reaction.py` (+82 — 2 new regression tests)

No co-tenant absorption. Surgical commit boundary. PASS.

## ATTACK 7 — 3 LOW caveats from 22nd cycle remain track-forward [VERDICT: PASS]

Per dispatch §7: 3 LOWs (negative_latency_count BATCH 3 metadata; 30s boundary test BATCH 2/3; WS_PROVENANCE_INSTRUMENTATION operator anchor) NOT in REVISE scope. Executor commit message confirms: "3 LOW caveats from critic carry-forward to BATCH 2/3 (NOT in this REVISE scope per dispatch); placeholder task #39 already created" for WS_PROVENANCE_INSTRUMENTATION.

Discipline correct. PASS.

## ATTACK 8 — New defects introduced by fix? [VERDICT: PASS]

Audit of REVISE diff for new risks:
- SELECT DISTINCT adds query cost — acceptable (single distinct on small windowed result set)
- pos_map construction is O(positions); negligible
- ticks_by_strategy now stores set[str] instead of str — slightly more memory; acceptable
- Empty pid_set handled at L266 (`if not pid_set: continue` would be NEEDED if empty was possible, but `pids = pos_map.get((token_id, strategy_key), set())` always returns a set, and the inner ANY check on empty set yields False → no event match → tick NOT counted as acted; this is correct behavior, not a regression)

Edge case worth noting: the latency JOIN at L171-186 still requires `pc.strategy_key IS NOT NULL` so a token with NO position_current row will not contribute any tick (this is unchanged from pre-fix). The pos_map at L234-241 also requires `strategy_key IS NOT NULL` — symmetric. No semantic gap.

No new defects introduced. PASS.

## Anti-rubber-stamp self-check

I have written APPROVE — narrow re-review scope, fix mechanically clean, both my 22nd-cycle empirical tests now PASS, edge cases verified.

Notable rigor:
- Re-ran my 22nd-cycle empirical defect reproductions against the FIXED code (didn't just trust the executor's regression tests; ran the SAME tests that earned the REVISE)
- Added 2 NEW edge cases for n_with_action ANY-of-set (tick + p1-acts-within-30s vs tick + neither-acts) to verify the refactor doesn't break the action-correlation semantic
- Verified diff scope (3 files; no co-tenant absorption)
- Confirmed 3 LOW caveats correctly tracked-forward per dispatch (NOT scope-creep into REVISE)

23rd critic cycle in this run pattern. Pattern: 22nd cycle earned the first REVISE via empirical-DB-reproduction; 23rd cycle confirms the fix via re-reproduction of the SAME empirical cases. This is the methodology §5 critic-gate workflow operating end-to-end (defect-found → fix-landed → re-verified).

## Final verdict

**APPROVE** — MED-REVISE-WP-1-1 fix verified clean. Authorize push of BOTH commits 3091514 + 3a10f1a → BATCH 1 LOCKED. GO_BATCH_2 detect_reaction_gap can proceed.

End WS_OR_POLL_TIGHTENING BATCH 1 REVISE re-review.
End 23rd critic cycle.
