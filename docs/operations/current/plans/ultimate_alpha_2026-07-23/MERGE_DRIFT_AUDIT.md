# Merge drift audit — origin/live hotfixes vs one-law exit (2026-07-24)

Read-only semantic classification for merging `claude/ultimate-alpha`
(HEAD `4960080741`, base `86b1342f3`) with `origin/live` (`5db9285020`, +52
commits). Merge-base == declared base `86b1342f3` — clean lineage, no divergence.

## The consumption surface (what the one-law reads)

`Position.evaluate_exit` (portfolio.py:1015) → `predicted_bin_law.exit_decision`
reads exactly four inputs:

- `q_lcb`  ← `_held_side_robust_lower` ← `current_ci[0]`, `fresh_prob`,
  `fresh_prob_is_fresh`, `belief_available`  (portfolio.py:939-972)
- `lock`   ← `_settlement_preimage_lock` ← `day0_zero_probability_exit_authority`,
  `fresh_prob`  (portfolio.py:974-993)
- `bid_breakpoints` ← `_exit_bid_breakpoints` ← `best_bid`  (portfolio.py:995-1013)
- RED ← `self.exit_reason == "red_force_exit"`

`_build_exit_context` (cycle_runtime.py:5048) fills `current_ci` **only** from a
finite held-side bootstrap CI (edge_ctx.confidence_band_*), `fresh_prob` from the
monitor probability, `day0_zero_probability_exit_authority` from the position's
absorbing-hard-fact flag. It never sets `belief_available` (defaults True), so the
degraded-belief signal reaches the law through `current_ci is None`, not a flag.

**Consequence:** any hotfix that improves the belief probability content upstream
(monitor_refresh / event_reactor_adapter / position_belief) is SUBSUMED — the law
consumes the improved `fresh_prob`/`current_ci` each tick. Any hotfix in a file
our branch never touched is PRESERVED-BY-KEEP.

## Classification table

| sha | invariant | verdict | evidence |
|---|---|---|---|
| 11ed0dcfa Day0 sells→temporal EV | Day0 hold on point-q terminal EV not UCB tail; immature Day0 statistical authority must HOLD | CONFLICT-TEXTUAL→OURS; point-vs-UCB SUBSUMED-BY-DESIGN; immature-HOLD SUBSUMED-WITH-CAVEAT | law uses `q_lcb` (lower bound) uniformly, stricter than their point-q → UCB pathology impossible. +2 ExitContext fields auto-merge but law never reads them (orphaned) |
| 2ef364bda use current prob bounds | exit reads CURRENT fresh bootstrap belief, not entry/stale | SUBSUMED + PRESERVED-BY-KEEP | is the law's design (`_held_side_robust_lower` reads `current_ci[0]`); monitor_refresh/position_belief untouched |
| 628a37d57 restore provisional Day0 temporal authority | Day0 provisional temporal prob keeps authority through monitor | SUBSUMED + PRESERVED-BY-KEEP | feeds fresh_prob/current_ci; files disjoint |
| 2aa419659 preserve prob content across handoff | probability content survives monitor handoff | SUBSUMED + PRESERVED-BY-KEEP | cycle_runtime edits (5314/5463) disjoint from our _build_exit_context; auto-merged |
| 1e6bd04ae token-typed exit value | (a) compare executable sale before edge-magnitude threshold; (b) dedup selects held token by direction | (a) SUBSUMED-BY-DESIGN; (b) PRESERVED-BY-KEEP | (a) law has no edge/neg_edge threshold, direct L(x) vs x·q⁻+M_x; (b) dedup fns untouched (portfolio.py:3048-3116, evaluator _has_same_token_blocking_open_db) |
| d1e47e3f0 honor degraded monitor reservations | degraded monitor with no progress keeps budget reservation | PRESERVED-BY-KEEP | cycle_runtime ~5949 disjoint, auto-merged; monitor scheduling upstream of law |
| 93faef3ed monitor budget on full held book | reservation count spans full held book | PRESERVED-BY-KEEP | cycle_runtime ~129/5712-5951 disjoint, auto-merged |
| f8730f18d retain no-order exits until bid returns | decided SELL with no fillable order retained, not released | PRESERVED-BY-KEEP | exit_lifecycle.py untouched; post-decision execution, orthogonal |
| ad128aabe bind SELL authority to current prob | batch SELL authority bound to current probability | SUBSUMED + PRESERVED-BY-KEEP | global_batch_runtime untouched; aligns with law consuming current q_lcb |
| 3a017ee88 pre-observation Day0 continuity | pre-observation Day0 prob continuous | SUBSUMED + PRESERVED-BY-KEEP | event_reactor_adapter/monitor_refresh untouched; feeds fresh_prob |
| 909d71dce fractional Kelly to family targets | family sizing targets get fractional-Kelly haircut | PRESERVED-BY-KEEP | solver.py untouched, no kelly import; different sizing layer |
| 589ec6aab exclude unresolved claims from SELL inventory | unresolved claims out of SELL inventory | PRESERVED-BY-KEEP | global_auction_universe untouched; orthogonal |
| b39f3a277 price provisional Day0 remaining window | provisional Day0 prob prices remaining window to terminal | SUBSUMED + PRESERVED-BY-KEEP | upstream producer of terminal q⁻ the law mandates; exit-side re-multiply prohibition not violated |
| 71bb2a9bc freeze Day0 temporal evidence epoch | Day0 temporal evidence epoch frozen within decision | SUBSUMED + PRESERVED-BY-KEEP | event_reactor_adapter untouched; feeds fresh_prob |
| ea38fabc2 restore continuous probability authority | continuous (not step) Day0 probability authority | SUBSUMED + PRESERVED-BY-KEEP | day0_fast_obs/event_reactor_adapter/reactor/ingest_main untouched; feeds fresh_prob |

## LOST list

**None fully lost.** The only invariant with no arithmetic equivalent in the
law is **11ed0dcfa's immature-Day0-statistical-authority HOLD** (keyed off
`day0_exit_authority_status ∈ {immature, unavailable}`, a field that fed only the
deleted Day0 exit branch and maps to neither `current_ci` nor
`day0_zero_probability_exit_authority`). This is a **deliberate FINAL_SPEC kill**
(§离场律: no Day0 permission gate; current q⁻ already integrates the full
remaining window), not an accidental loss — reclassified **SUBSUMED-WITH-CAVEAT**,
not LOST. See merge decision #2.

## Textual conflicts

**Count: 1 file** (`src/state/portfolio.py`), 2 diff3 hunks, both inside the
`evaluate_exit` region:

- Hunk A `@@ -1079,+1081 @@` — our SELL_REVERSAL branch vs their old exit tree
  (best_bid/forward_edge/day0_active + 11ed0dcfa immature-HOLD + point-vs-UCB).
- Hunk B `@@ -1089,+1342 @@` — our HOLD tail vs their `ci_separation_gate` /
  1e6bd04ae reordered CI_SEPARATED_REVERSAL.
- (Non-conflict) `ExitContext` +day0_exit_authority_status/reason auto-merges.

**All other overlapping files auto-merge:** cycle_runtime.py, evaluator.py,
edli_position_bridge.py, command_recovery.py, db.py, and 3 test files.

## Merge-procedure recommendation

1. Resolve `portfolio.py` = **OURS** for the entire `evaluate_exit` body. Both
   conflict hunks are dead branches under the one-law.
2. **Conscious decision on 11ed0dcfa immature-HOLD:** verify monitor_refresh sets
   `current_ci=None` / `fresh_prob_is_fresh=False` whenever
   `day0_exit_authority_status ∈ {immature, unavailable}`. If a valid CI can
   coexist with immature status, the one-law will SELL where the hotfix held —
   accept as deliberate under FINAL_SPEC, or fold the maturity signal into
   `belief_available` in `_build_exit_context` to preserve the hold.
3. **Drop** the orphaned `day0_exit_authority_status` / `day0_exit_authority_reason`
   ExitContext fields — the law never reads them.
4. **Keep theirs verbatim** for all monitor_refresh / event_reactor_adapter /
   position_belief / solver / exit_lifecycle / global_* hotfixes and 1e6bd04ae's
   dedup token-typing (all in files/regions we never touched).
5. Kelly deletion verified clean: merged evaluator.py/strategy_profile.py retain
   no reference to deleted `observed_target_day_fraction` / `city_kelly_multiplier`.

**Coverage:** exit-law reconciliation and both-sides exit files (cycle_runtime,
evaluator — confirmed disjoint) audited. edli_position_bridge/command_recovery/db.py
+ test files confirmed textual auto-merge only, NOT semantic-clean — run the suite
green before cutover.
