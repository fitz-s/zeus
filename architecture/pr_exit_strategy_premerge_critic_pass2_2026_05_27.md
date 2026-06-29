# Pre-Merge Critic Pass 2 — Exit Strategy Pure-Math PR

Status: ARCHIVED_REFERENCE
Commit: 7fd471a4ed (amended from aa14f1b466)
Auditor: opus critic
Date: 2026-05-27
Mode: THOROUGH (no escalation needed; pass-1 BLOCKERs all resolved)

## Verdict
**APPROVE**

All four pass-1 BLOCKER/SEV-2 findings cleanly resolved. The F-1 fix is correctly direction-aware in both EV and contradiction branches; F-2 reuses canonical `polymarket_fee` with proper boundary clamping; F-3 docstring matches impl; F-4 hurdle contract is locked by an additivity test. New tests cover the exact regression classes — 94 pass, zero brittle. Integration plan amendment is precise. Math layer is safe to build on.

## Re-probes reproduced

### (a) New BLOCKER class from F-1 fix? — PASSED, no new BLOCKER
- **Branch coverage**: I verified the three branch dispatches now interact cleanly. Branch (a) impossibility short-circuit is buy_yes-only when `is_impossible`. Branch (b) contradiction-fail-closed is buy_yes-only when `contradiction`. Branch (c) EV cash-out handles everything else, with direction-aware `held_p`. For buy_no legs, branches (a) and (b) skip — branch (c) takes over.
- **Contradiction-feasible-bin coverage for buy_yes**: Reachable. A 3-bin family with two impossible bins holding all the mass (`p = [0.5, 0.5, 0.0]`, observed=80 leaving bin 2 feasible) produces `contradiction=True, mask=(T,T,F)`. A buy_yes leg on bin 2 falls into branch (b) and gets `OBSERVATION_CONTRADICTION_FAIL_CLOSED SELL_FULL`. The rewritten `test_contradiction_buy_yes_feasible_zero_p_bin_sells_full` (line 156-186 of test file) exercises exactly this path and asserts the right reason.
- **buy_no under contradiction**: Falls through to EV. `p_obs = 0` for every bin (contradiction zeros them), so `held_p = 1 - 0 = 1.0` and `hold_value = shares × 1.0`. For any bid in (0.05 ... 0.99), HOLD_DOMINANT fires. Test `test_contradiction_buy_no_defers_to_ev_does_not_fail_close_sell` locks this at bid=0.90.
- **LOW market sister case**: Reproduced. Observed=20 on LOW market makes (40,41) impossible. buy_no on that bin yields `action=HOLD, reason=HOLD_DOMINANT, hold_value=100, sell_value=85`. F-1 fix is metric-symmetric.

### (b) F-2 {0.0, 1.0} clamp: is "no fee" correct or should bid=0 also raise? — PASSED, current behavior is correct
- `bid=0.0` returns `sell_value = shares × 0 = 0` with no fee. Two reasons this is right (not a raise):
  1. Callers compute `sell_value` on every leg for trace consistency, even no-bid legs. The `has_executable_bid` gate at line 268 already filters `bid < min_exit_bid (0.01)`, so any bid=0 reaching branch (c) cash-out would not be executable anyway.
  2. The semantic of "execute at price 0" is well-defined (you receive nothing), unlike negative prices. Raising would force the caller to special-case the trace path.
- `bid=1.0` returns `shares × 1.0` with no fee. Polymarket fee is `rate × p × (1-p)` which equals 0 at p=1 (no spread at certainty). The clamp mirrors `HoldValue.compute_with_exit_costs:184-197` exactly, preserving the entry/exit symmetry the project established in PR #348.
- Verdict: the {0,1} clamp at zero fee is *the* right invariant. Don't tighten to raise.

### (c) Should `daily_hurdle_dollars` be deprecated in favor of single-source `hold_cost_extras`? — Defer; current additivity is defensible
- The two parameters represent different concepts:
  - `hold_cost_extras` is a **per-leg deduction** from `hold_value` (computed by HoldValue.compute_with_exit_costs: per-position time-cost + per-position fee_cost + per-position crowding).
  - `daily_hurdle_dollars` is a **family-level $ floor** — a minimum profit margin the EV cash-out must clear before triggering a sell. It's the operator's "I don't want to churn for tiny gains across the whole family" knob.
- Collapsing into one parameter would force the caller to either (i) pre-compute and stuff the family hurdle into each leg's `hold_cost_extras` (losing the family-level abstraction), or (ii) drop the family hurdle entirely (losing operator control).
- The new docstring + `TestHurdleComposability` test lock the additive contract clearly. The deprecation question is real but the answer is "keep both; the contract is now explicit." If a future caller wants single-source-of-truth, they pass `daily_hurdle_dollars=0.0` and put everything in `hold_cost_extras`. That option is preserved.
- Verdict: no action. The pass-2 amendment is sufficient.

### (d) Regressions in mass conservation / contradiction propagation / impossibility short-circuit / advisory-never-impossible? — PASSED, all four invariants hold
- **Mass conservation**: 4-bin family with one impossible bin → `sum(p_obs) = 1.0` within float epsilon. Unchanged.
- **Contradiction propagation**: All-impossible case → `p_obs = (0,0,...)`, `contradiction_flag=True`. Unchanged.
- **Impossibility short-circuit count (buy_yes)**: 4-bin family, observed=65 makes bins (60,61) and (62,63) impossible. Optimizer correctly emits 2 `OBSERVATION_IMPOSSIBLE_HIGH SELL_FULL` verdicts and 2 `HOLD_DOMINANT` (the feasible bins have low p_obs vs low bid; both branches consistent).
- **Advisory never deterministic**: ADVISORY constraint + mixed buy_yes/buy_no legs → `any_deterministic_exit()=False`, no leg has IMPOSSIBLE-prefixed or CONTRADICTION_FAIL_CLOSED reason. Unchanged.

## Nits worth noting (not blocking)

- **N-1 [defensive validation]**: `optimize_exit_family` does not validate `leg.shares > 0`. Negative shares (`shares=-10.0`) on an impossible buy_yes bin yields `action=SELL_FULL, sell_shares=-10.0`, which is GIGO — the optimizer would propagate a negative sell order to the next layer. The `Position` upstream should never produce negative shares, so this is a hypothetical, but a one-line `if shares < 0: raise ValueError(...)` at the top of the per-leg loop would close it. Same call shape as `_per_leg_sell_value`'s validation.

- **N-2 [conservative under contradiction]**: For buy_no under contradiction, `p_obs=0` forces `held_p=1.0` regardless of where the true posterior would have placed mass. This is the *correct fail-closed* direction (errs toward HOLD, doesn't sell guaranteed-winner candidates), and matches operator §3 spirit, but it does mean buy_no legs may HOLD forever under persistent contradiction without operator intervention. The follow-up integration PR should ensure `Position.evaluate_exit`'s standard cash-out / settlement-imminent gates still get to decide on these legs (the docstring on lines 246-250 already promises this, but verify the seam in the wiring PR).

- **N-3 [doc nit]**: The `optimize_exit_family` docstring at line 241-243 still says "only buy_yes legs participate in the impossibility short-circuit … the family optimizer leaves buy_no legs to standard evaluate_exit unless contradiction." The "unless contradiction" qualifier is now inaccurate — buy_no legs are ALSO deferred from the contradiction branch per F-1. Suggest: "… leaves buy_no legs to direction-aware EV cash-out (branch c); per-position cash-out / settlement-imminent / panic gates run after."

- **N-4 [trace consistency]**: `feasibility="feasible_or_current"` is logged for any non-impossible, non-advisory bin (line 281). This conflates D1's `contains_current_record` and `feasible` verdicts. The trace consumer loses information that could be useful for post-mortem ("did this leg sell because of cash-out on a current-record bin, or on a still-reachable bin?"). Not a math defect; cleanup for the integration PR if trace quality is reviewed.

## Lock-ins worth preserving (now stronger)

In addition to the five locks called out in pass 1:
- **Direction flip is at the family-decision boundary**, not the leg input. The caller passes raw YES-side `p_obs` from D2; the optimizer applies `_held_probability(p_obs, direction)`. This is the right separation — D2 stays YES-side only, callers don't need to pre-flip, and the regression site is the *single* `_per_leg_hold_value` call.
- **Fee primitive reuse, not reimplementation**. The new `_per_leg_sell_value` imports canonical `polymarket_fee` and lets it raise for interior prices; clamps only the {0,1} degenerate boundary. Future agents tempted to "inline the fee math for speed" must now re-derive PR #348's invariant from scratch and explain why they're breaking it.
- **Hurdle composability test as antibody**. `TestHurdleComposability` walks three hurdle values across the SELL/HOLD boundary so a double-counting regression flips at least one assertion. Cannot be passed by a no-op or by silently zeroing one of the two inputs.

## Final verdict

`result: APPROVE — 0 BLOCKER, 0 SEV-1, 0 SEV-2, 0 SEV-3, 4 NITs (N-1 to N-4, all optional cleanup; none block merge or live-trading integration).`

Math layer is safe to land. The follow-up integration PR (D5 short-circuit, D3 monitor wiring, D4 forward-edge) can build on these primitives with the F-1 antibody contracts intact.
