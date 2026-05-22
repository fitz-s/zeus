# PromotionReadinessValidator — Adversarial Critic
# Reviewer: critic | Date: 2026-05-22 | Mode: THOROUGH (no escalation; advisory module, fail-closed)

> **RESOLUTION (PR #284):** FIXED. The contract-#4 finding and both MINORs below were addressed
> in PR #284 — the promotion predicate is single-sourced as `promotion_predicate()` in
> `live_readiness_tribunal.py` (both adjudicate() and the validator call it), the operator_ref
> guard fires only on live-tier crossings, and `tier_required_for_live` is no longer a local
> literal default. This is a FROZEN review record; findings below describe pre-fix state.

VERDICT: FIX_REQUIRED *(historical — fixed in PR #284)*

Test repro: `python -m pytest tests/analysis/test_promotion_readiness.py -q` → 14 passed in 1.20s.

## Discriminating finding (contract #4)

MAJOR — Second source of tier-promotion truth.
src/analysis/promotion_readiness.py:261-293 (`_eval_tribunal_signal`) inline re-implements
`adjudicate()`'s promotion gate (src/analysis/live_readiness_tribunal.py:166-170). Verified by
domain sweep (all EvidenceTier × ci∈{None,0.30,0.549,0.55,0.5501,0.62,0.99}): signal (a)
`_eval_ci_signal` and signal (b) `_eval_tribunal_signal` return IDENTICAL pass/fail at every
point — the DEMOTE branch (286-290) only relabels rationale, never flips pass/fail. So the
advertised "READY requires ALL THREE signals" is really TWO independent predicates (CI
inequality + settlement gate); signal (b) is a rationale-only twin of (a).

Why it matters: `adjudicate()`'s predicate is the live authority and can evolve (n_settled
floor, no_trade_events gate, regime-conditioned cost_of_capital). When it does, the validator's
operator-facing READY/NOT_READY silently diverges from what the tribunal would actually emit,
and the 14 tests stay green. This is exactly the provenance-divergence class flagged for this
codebase (Fitz #4) and by #279 finding #1.

Fix (small, preserves the no-DB-write contract): extract a pure
`def promotion_predicate(tier_current, tier_required, ci_lower, breakeven, cost_of_capital) -> bool`
in live_readiness_tribunal.py; have BOTH `adjudicate()` and the validator's signal (b) call it.
Makes the divergence category impossible without touching the write path.

## Contract findings (all others PASS)

1. READY-requires-all-three (#1): PASS. all_pass = AND of three booleans (line 196); no single
   signal can yield READY. Settlement-gate exception PROPAGATES (probed: BadConn → RuntimeError,
   not swallowed into READY). None ci_lower → both CI+tribunal FAIL. Fail-loud / fail-closed.
2. Read-only (#2): PASS. No tier write; signal (b) is pure logic, never calls adjudicate().
   T3 (test:248-269) exercises the would-promote path then asserts 0 rows in
   evidence_tier_assignments. Settlement gate is SELECT-only.
3. Operator-gate fail-closed (#3): PASS. Raises ValueError (not warn) when tier_target >=
   LIVE_PILOT_TINY without operator_ref (line 207-212); T4 tests:285-331 exercise the raise
   incl. whitespace-only ref.
4. Authority alignment (#4): see MAJOR above.
5. Test adequacy (#5): MOSTLY PASS. 14 tests pin their docstring invariants non-vacuously
   (spot-broke ci>breakeven mentally → T2a/T1 would fail). Gaps in What's-Missing below.

## MINOR

- promotion_readiness.py:120 — constructor default `tier_required_for_live=LIVE_PILOT_TINY`
  is a fainter instance of #279 finding #1. A LIVE_LIMITED_HAIRCUT/LIVE_NORMAL-required
  strategy gets the loosest gate if the caller forgets to pass the per-strategy value from
  StrategyProfile.evidence_tier_required_for_live. Fail-soft (operator_ref still gates the
  raise). Recommend: no default, or document the caller MUST pass the profile value.
- promotion_readiness.py:207-214 — NOT_READY for a strategy ALREADY AT a live tier still
  raises ValueError demanding operator_ref (probed: LIVE_PILOT_TINY & LIVE_NORMAL, ci=0.30,
  NOT_READY → raises), because tier_target==tier_current>=LIVE_PILOT_TINY. Conflates
  "recommending promotion into live" with "current tier happens to be live." A routine
  read-only health check on a live strategy throws unless given a dummy ref. Fail-closed
  direction (safe), but surprising. Recommend gating the raise on
  (verdict==READY AND tier_target>tier_current) rather than tier_target>=LIVE_PILOT_TINY alone.

## What's Missing (untested invariants — list, low-sev)

- No test for NOT_READY-at-already-live-tier operator_ref raise (the MINOR above).
- No test that a settlement-gate exception propagates rather than degrading to READY
  (contract #1 corner; verified manually here).
- No boundary test for ci_lower == breakeven exactly (verified manually: FAIL, strict `>` — correct).
- No test for tier_target ceiling at LIVE_NORMAL(7) (min(7,...) cap, line 200).

## Realist Check

MAJOR (not CRITICAL): validator is NOT runtime-wired (grep src/ → zero importers outside its
own file); it is operator-advisory only and cannot auto-promote. Present predicate values are
byte-identical to adjudicate(); the divergence is a future-drift risk, not a present false-READY.
Fail-closed via operator_ref. Hence MAJOR + FIX_REQUIRED (own the contract-#4 framing), not a
ship-blocking CRITICAL.
