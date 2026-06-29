# PR #348 "market-cost seam" — Pre-merge deep review (reviewer + critic combined)

Status: ARCHIVED_REFERENCE

**Created:** 2026-05-27
**Reviewer:** critic agent (adversarial, read-only)
**Branch:** claude/market-cost-seam-2026-05-27  HEAD=ad2a160740
**Full PR diff:** cb4541da70..HEAD   **This-turn diff:** b23f5f0c36..HEAD
**Verdict:** REVISE — Stage-0 default behaviour is safe and merge-ready; the K5/Wave-6 math contract is NOT fully wired for Stage-2 promotion (one dead seam). See SEV-1.

---

## VERDICT

**ACCEPT for Stage-0 merge / REVISE if "ships the K5 single-count contract" is the merge criterion.**

All seven operator blockers B1–B7 are genuinely resolved in current source (not stubs). Stage-0
default (both env flags OFF) is behaviour-preserving. However the Wave-6 ci_width-haircut collapse —
the heart of K5 / INV-40 — is **dead from the live sizing path**: the only live `dynamic_kelly_mult`
call site never threads the per-edge `market_uncertainty_in_lcb` flag, so at Stage-2 the ci_width
haircut would NOT collapse while the EffectiveKellyContext haircut WOULD. Half the contract lands.
This is latent (Stage-2 is operator-deferred) but it defeats the INV-40 "exactly once" guarantee the
PR exists to deliver, and the operator's planned replay-validation gate will read wrong numbers.

---

## BLOCKER VERIFICATION (B1–B7) — all confirmed REAL in current source

| B | Claim | Status | Evidence |
|---|---|---|---|
| B1 | `ExecutionPrice.__eq__` must NOT equal a bare float (hash/transitivity) | RESOLVED | `src/contracts/execution_price.py:239-250` — `__eq__` returns `NotImplemented` for non-EP, full-field tuple eq for EP; `__hash__` matches the same 4-tuple. hash/eq consistent, no float-equality. |
| B2 | cost_uncertainty RSS uses ABSOLUTE slippage (fill−best_ask), not bps ratio | RESOLVED | `src/contracts/entry_quote_evidence.py:252-271` — `slippage_abs = max(0, fill_price_walk − best_ask)`; `slippage_bps` explicitly excluded from RSS (kept for audit only). All terms in probability units. Dimensionally consistent. |
| B3 (b23f) | edge/forward_edge use all-in cost not p_market | REAL | `src/strategy/market_analysis.py:481-488` (`entry_cost_mean = eqe.all_in_entry_price` when EQE present), `:522 forward_edge=edge_yes`. NO-side mirror `:603-610`. p_market retained for trace only (`:517,521`). |
| B4 (b23f) | unified-budget collapse gated PER-EDGE not global | REAL | `kelly.py:476-479` `_collapse_ci_width_haircut = enabled() AND market_uncertainty_in_lcb`; `evaluator.py:1594-1596` same for EffectiveKellyContext. `_unified_uncertainty_budget_enabled()` also enforces Wave-5.5 prereq ordering (`kelly.py:414-430`). |
| B5 (this turn) | THIN_BOOK/CROSSED hard-veto + severity CROSSED>THIN_BOOK>STALE>ASK_ONLY>LIVE_OK + ASK_ONLY σ floor | RESOLVED | Severity order `entry_quote_evidence.py:158-166`; hard-veto `market_analysis.py:454-474` (yes) + `:582-601` (no); ASK_ONLY floor `entry_quote_evidence.py:59,257`. |
| B6 (b23f) | Kelly size capped to depth-walked authority | REAL | `evaluator.py:1665-1675` caps `fee_adjusted_size` to `max_executable_shares * price`; call site passes `edge.entry_quote_evidence.depth_at_target_size` (`:5897-5901`). |
| B7 (b23f) | live max_legs>1 refused until full-family ELG | REAL | `family_exclusive_dedup.py:102-115` — live tier HARD-CAPS to 1 regardless of env, WARNING logged; shadow tier uncapped. |

Rank-vs-size divergence (operator's explicit concern): NOT present. Edge ranked off `entry_cost_mean`
(all-in cost); Kelly sized off `edge.entry_price` (= EQE all-in `fee_adjusted` ExecutionPrice) at
`evaluator.py:5884-5886`. Same cost basis on both sides. No-EQE legacy path stamps `vwmp`
(fee_deducted=False) → `with_taker_fee` applied once at boundary `:1644`. Consistent.

---

## FINDINGS

### SEV-1 — Wave-6 ci_width-haircut collapse is dead from the live sizing path (K5/INV-40 half-wired)

**File:** `src/engine/evaluator.py:5734` (live), `src/engine/replay.py:1706` (replay)
**Defect:** `dynamic_kelly_mult(...)` is called WITHOUT the `market_uncertainty_in_lcb` argument. The
parameter defaults to `False` (`kelly.py:444`), so the in-function collapse predicate
`_collapse_ci_width_haircut = enabled() AND market_uncertainty_in_lcb` (`kelly.py:476-479`) can NEVER
be True from production. The ci_width haircuts (D1 #2/#3) therefore stay applied at ALL stages.

Meanwhile the sibling collapse — `EffectiveKellyContext.haircut()` inside
`_size_at_execution_price_boundary` — IS correctly per-edge wired (`evaluator.py:5893-5895`,
consumed `:1594-1599`).

**Cross-module relationship broken (Fitz §2):** σ_market is produced in `market_analysis` bootstrap
and stamped onto `BinEdge.market_cost_uncertainty_applied`. The boundary consumes that flag; the
multiplier path silently does not. At Stage-2 (both env flags ON) the EffectiveKellyContext haircut
collapses but ci_width does not → ci_width counted twice (multiplier + edge_LCB) → **INV-40 "exactly
once" violated**. Plan §Wave6 line 237 ("REMOVE ci_width haircuts #2/#3") is not delivered to the
live path.

**Why the tests didn't catch it (PR #309-graded-F pattern, see MEMORY):** every Wave-6 collapse test
(`tests/test_wave6_unified_uncertainty_budget.py:110,111,118,119,144-151`) passes
`market_uncertainty_in_lcb=True` DIRECTLY to `dynamic_kelly_mult`. They prove the function collapses
when the flag is passed; none traces the evaluator call site that fails to pass it. Green unit tests,
dead production seam.

**Severity framing (operator picks):**
- Merge-blocking SEV-1 if the criterion is "this PR ships the K5 single-count contract".
- SEV-2 if the criterion is "this PR ships Stage-0 only; Stage-2 multiplier wiring is an explicit
  follow-up". Stage-0 default is unaffected (flag gate returns False regardless), so no live-capital
  behaviour changes today. Direction at Stage-2 is conservative (under-size), not money-loss — but
  the operator's `size_unified/size_legacy ∈ [1.0,1.2]` replay gate (math spec §15.8) will produce
  wrong numbers and cost a debugging session.

**Fix:** thread the per-edge flag at both call sites:
`dynamic_kelly_mult(..., market_uncertainty_in_lcb=bool(getattr(edge,"market_cost_uncertainty_applied",False)))`
at `evaluator.py:5734` and `replay.py:1706`. Add a relationship test that calls `evaluate_candidate`
(not `dynamic_kelly_mult` directly) with an EQE-bearing edge under Stage-2 env and asserts the
ci_width haircut is absent from the realised size.

### SEV-2 — replay double-charges the Polymarket fee on EQE edges

**File:** `src/engine/replay.py:1718`
**Defect:** `_replay_execution_price = edge.entry_price + polymarket_fee(edge.entry_price, fee_rate)`.
When EQE is present, `edge.entry_price` is already `to_execution_price()` → `fee_adjusted`,
`value=all_in_entry_price` (fee included, `entry_quote_evidence.py:112-125`). Adding
`polymarket_fee(...)` again applies the fee a SECOND time. `edge.entry_price` coerces to float via
`__float__` so it runs silently.
**Relationship broken:** the fee-inclusion provenance carried by ExecutionPrice
(`fee_deducted=True`) is ignored by the replay arithmetic, which assumes a raw price.
**Stage-0 safe:** with EQE flag OFF, `entry_price` is `vwmp` (fee_deducted=False) → single fee. Bug
manifests only at Stage-1+ and only in replay/backtest (not live capital), so backtest accuracy
degrades, not trading.
**Fix:** branch on `edge.entry_price.fee_deducted` — skip the manual `polymarket_fee` add when already
fee-adjusted (mirror the boundary's `ep if ep.fee_deducted else ep.with_taker_fee` logic at
`evaluator.py:1644`).

### SEV-3 (nit) — bootstrap clips EQE c_b to [P_CLAMP_LOW, P_CLAMP_HIGH] but the raw-edge gate does not

**File:** `src/strategy/market_analysis.py:488` (raw edge off un-clipped `entry_cost_mean`) vs
`:809` (bootstrap c_b clipped). For an `all_in_entry_price` outside the Platt clamp band the raw-edge
accept-gate and the bootstrap CI use slightly different cost. Edge-case only (all_in near 0 or >0.999,
which the THIN_BOOK/price-floor gates already mostly exclude). Conservative direction. No action
required; note for awareness.

---

## SURFACES REVIEWED

- `src/contracts/execution_price.py` (full) — B1, dunders, hash/eq.
- `src/contracts/entry_quote_evidence.py` (full) — B2, B5, σ_market RSS, reliability order, ASK_ONLY floor.
- `src/data/orderbook_depth_walk.py` (full) — depth walk math, slippage sign.
- `src/strategy/market_analysis.py` (full) — B3, B4, B5 veto, bootstrap c_b sampling (yes+no).
- `src/strategy/kelly.py` (full) — B4 per-edge gate, unified-budget flag + ordering guard.
- `src/strategy/family_exclusive_dedup.py:82-1071` — B7 live cap, _edge_cost / selection score.
- `src/engine/evaluator.py` — boundary (1554-1678), EQE wiring (287-368, 4355-4474, 4937), km call (5734), boundary call (5884-5902), entry_price float-coercion sites (195,1239,1275,1485).
- `src/engine/replay.py:1698-1722` — replay sizing seam.
- `src/types/market.py` (full) — BinEdge typed entry_price + legacy coercion + new fields.
- Serialization: `src/state/decision_chain.py:215,236`, evaluator float() sites — no ExecutionPrice truncation.
- STALE reorder blast radius: `freshness_registry`, `truth_authority`, `main.py` — EQE "STALE" is a separate taxonomy, no live consumer reads it; reorder benign.
- Test baseline: full targeted suite (218 passed / 1 xfailed / 1 failed).
- Test file headers (CLAUDE.md provenance rule): all new test files compliant (Created / Last audited / Authority basis present).

## SURFACES NOT REVIEWED

- `scripts/audit_market_price_semantics.py` (X8 regex limitation already operator-acknowledged).
- `src/engine/cycle_runtime.py` W2/W3/W4 final-haircut application (where EffectiveKellyContext is
  finalised) — read only the boundary contract, not the cycle_runtime application path. **Recommend a
  follow-up trace** because that is the other half of where the Stage-2 collapse must land; SEV-1's fix
  may also need a cycle_runtime touch if the ci_width factor is re-derived there.
- `architecture/invariants.yaml` INV-38/39/40 text (read plan summary, not the yaml line-by-line).
- Non-seam evaluator surfaces unrelated to market-cost.

## TEST BASELINE (reproduced on HEAD, not trusted from operator)

`218 passed, 1 xfailed, 1 failed`. The single failure is
`tests/test_fdr.py::TestSelectionFamilySubstrate::test_full_family_selection_uses_one_candidate_family_across_strategies`
(`shoulder_impossible_tail_capture` vs expected `shoulder_sell`). Reproduced on parent b23f; PR diff
touches NO shoulder-strategy files. **Confirmed pre-existing, independent of PR #348.** Operator's
claim is correct.

## KNOWN-DEFERRED Stage-2 / Stage-B promotion gates (NOT defects)

- **X6** target_shares = min-order floor (chicken-and-egg with Kelly size); deferred to Wave 8 post-Kelly re-walk. Conservative direction.
- **X8** audit script regex vs AST; Wave 8 cleanup.
- **P0-5** full-outcome family ELG optimiser; live max_legs hard-capped to 1 until it ships.
- Mandatory shadow + replay before Stage-2 live promotion (operator gate, plan §Wave6 + §Staged promotion contract).
- ASK_ONLY 0.02 σ floor (200 bps absolute) is sane and conservative on one-sided books — explicitly NOT a finding.

## RECOMMENDATION

Merge-ready for **Stage-0 only** (default flags OFF; bit-identical to legacy except typed
serialization, which is verified safe). Before declaring the K5/Wave-6 contract delivered or
promoting to Stage-2, fix SEV-1 (thread the per-edge flag into the live `dynamic_kelly_mult` call +
add an evaluator-level relationship test) and SEV-2 (replay fee double-charge). SEV-1 is the
highest-value catch: the operator reviewed the parent commit and the unit tests are green, so this
dead seam would otherwise reach Stage-2 promotion undetected.

---

## FIX VERIFICATION (commit c444787828, verified against git diff ad2a160740..c444787828)

**SEV-1 RESOLVED.** All 5 haircut-bearing call sites now forward the per-edge gate from the single
source `edge.market_cost_uncertainty_applied`: evaluator.py:5747 (dynamic_kelly_mult), replay.py:1716
(dynamic_kelly_mult) + :1749 (boundary, also gains the depth cap it previously lacked),
cycle_runtime.py:1218 `_market_unc_in_lcb` → W2/W3/W4 boundaries :1228/:1271/:1351. Re-grep confirms
no haircut call site missed. cycle_runtime W2/W3/W4 (the path I flagged un-reviewed) is now correctly
touched — `decision.edge` is the right per-edge source at that scope. Both halves of the Wave-6
collapse (ci_width in dynamic_kelly_mult, EKC at the live microstructure boundary) are now wired.

**No-op at flag-OFF confirmed:** every collapse still ANDs with `_unified_uncertainty_budget_enabled()`
(kelly.py:476-479, evaluator.py:1594-1596), which returns False at Stage-0/1 default. Stage-0/1 live
behaviour bit-identical.

**SEV-2 RESOLVED.** replay.py:1730-1741 branches on `edge.entry_price.fee_deducted`: skips the
`polymarket_fee` re-add when already fee-adjusted (EQE path), keeps the single fee for the legacy
implied-probability path. Correct; legacy path intact.

**Antibody quality:** `tests/test_pr348_unified_budget_seam_wiring.py` is an AST wiring contract over
the 3 production files — asserts every `dynamic_kelly_mult` / `_size_at_execution_price_boundary` call
node forwards the gate kwarg, and counts cycle_runtime boundary calls (>=3). This is the structural
antibody (Fitz §3) the green-unit-test failure mode demanded — it catches a future refactor that adds
a call site and forgets the gate. Not a direct-function-call test.

**Tests:** 31 passed (seam-wiring 3, wave6 18, R3 4, executable_ev_replay 5, money_path_lifecycle 1).

**Residual:** none from SEV-1/SEV-2. Prior SEV-3 nit (raw-edge gate vs bootstrap clip band) unchanged,
still edge-case/conservative, no action.

**MERGE-READY for Stage-0 and Stage-1.** Stage-2 (unified-budget single-count) and Stage-B
(`max_legs>1` full-outcome ELG) remain documented operator-gated follow-ups (X6, X8, P0-5, mandatory
shadow+replay), not defects.
