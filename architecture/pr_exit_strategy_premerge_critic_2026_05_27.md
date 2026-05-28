# Pre-Merge Critic — Exit Strategy Pure-Math PR
Commit: aa14f1b466
Auditor: opus critic
Date: 2026-05-27
Mode: ADVERSARIAL (escalated after F-1 surfaced; expanded scope to contradiction branch + docstring/spec layer)

## Verdict
**BLOCK**

The pure-math layer ships with a load-bearing direction-semantic bug on `buy_no` legs that the test suite affirmatively locks in. A single root cause — "optimizer assumes `p_obs[bin_index]` is the held-side win probability for all directions" — manifests in two branches (EV cash-out + contradiction fail-closed) and would actively liquidate guaranteed-winner NO legs the moment the D3 wiring PR turns this on. Additionally, the fee primitive silently accepts invalid prices (regressing the entry-side `polymarket_fee()` invariant), and the module's authoring docstring carries a wrong formula. Math foundations must be correct before integration builds on them; "pure-math can't hurt live trading on its own" is true but not sufficient when the next PR consumes these primitives.

## Findings

### F-1 — `buy_no` legs receive YES-side win probability in hold_value (root cause; affects both EV and contradiction branches)
- ID: F-1
- Severity: **BLOCKER**
- Area: D3
- Probe that surfaced it: #6 + #7 (same root cause)
- Finding:
  `src/strategy/exit_family_optimizer.py:227` computes `hold_value = shares × p_obs - extras` using `p_obs[leg.bin_index]`, which is by D2's contract the YES-side posterior mass at that bin. For a `buy_no` position on an impossible YES bin, the holder's true win probability is `1 − p_obs ≈ 1.0` (NO side wins because YES is impossible), so the correct `hold_value` is `shares × ~1.0`, not `shares × 0`. The existing Zeus invariant (`src/state/portfolio.py:391`) explicitly states: *"For buy_no: P(NO) and NO market price. This invariant is established once at entry and never flipped."* The optimizer breaks that invariant at the family-decision boundary.

  Live consequence (verified by execution):
  - bin (60,61) impossible at observed=63, p=[0.30,0.70], buy_no leg, 100 shares, bid=0.90
  - Optimizer: `action=SELL_FULL, reason=EV_CASH_OUT, sell_value=72.0, hold_value=0.0`
  - Truth: NO side wins at settlement → realized value = `100 × 1.00 = $100`
  - **Net loss from blind cash-out: $28 per 100 shares.** Scales linearly with size.

  Same root cause manifests in the contradiction-fail-closed branch (probe #7): all-YES-bins-impossible contradiction triggers `SELL_FULL OBSERVATION_CONTRADICTION_FAIL_CLOSED` for every buy_no leg at any bid ≥ min_exit_bid, when those legs are by construction guaranteed winners.

  The commit message at `aa14f1b466` says *"buy_no on an impossible YES bin is the WINNING side; optimizer defers to standard cash-out (operator §5 nuance)."* The author correctly captured intent in prose, correctly skipped the impossibility short-circuit for buy_no, then implemented the EV cash-out for buy_no anyway with the wrong probability. Test `test_buy_no_on_impossible_yes_bin_falls_through_to_evaluate_exit` at `tests/test_exit_family_optimizer.py:124-151` affirmatively asserts `reason != "OBSERVATION_IMPOSSIBLE_HIGH"` and silently accepts `EV_CASH_OUT` as a valid landing — **locking the bug in as antibody**. This is exactly the failure mode the operator's commit-message prose was trying to prevent.
- Proposed fix:
  Option A (preferred — pure-math layer): make hold_value direction-aware:
  ```python
  held_p = p_obs if leg.direction == "buy_yes" else (1.0 - p_obs)
  hold_value = _per_leg_hold_value(leg.shares, held_p, leg.hold_cost_extras)
  ```
  Apply same flip wherever `p_obs` is folded into a buy_no decision (EV + contradiction branches). Then revise `test_buy_no_on_impossible_yes_bin_falls_through_to_evaluate_exit` to assert `action=HOLD, reason=HOLD_DOMINANT` and add a relationship test that locks `hold_value_buy_no = shares × (1 - p_obs) - extras` against an analytical scenario.

  Option B (defer): the optimizer truly cannot decide buy_no legs at this layer (it lacks the NO-side market context) — short-circuit ALL buy_no legs to `HOLD reason=DEFER_TO_EVALUATE_EXIT` and let the per-position pipeline handle them. The current code's `buy_no on impossibility short-circuit` skip already does this for branch (a); extend it to branches (b) and (c).

  Either way: contradiction-fail-closed branch must also not sell guaranteed-winner buy_no legs. The "fail-closed" semantic is "don't trust hold_value to decide HOLD" — not "sell regardless of which side will pay."

### F-2 — `_polymarket_maker_fee` silently accepts invalid prices, regressing entry-side invariant
- ID: F-2
- Severity: **BLOCKER**
- Area: D3 (fee primitive)
- Probe that surfaced it: #5
- Finding:
  `src/strategy/exit_family_optimizer.py:124-135` reimplements the Polymarket maker fee with silent zero-return on invalid inputs:
  ```python
  def _polymarket_maker_fee(price: float, fee_rate: float) -> float:
      if fee_rate <= 0.0:
          return 0.0
      if not (0.0 < price < 1.0):
          return 0.0
      return fee_rate * price * (1.0 - price)
  ```
  The canonical `src/contracts/execution_price.py:259-278` does the same math but **raises ValueError on price ∉ (0,1)** and on non-finite inputs — established by P9-D3 (memory: PR #348 fee semantics audit). With no validation in `_per_leg_sell_value`, the new fee primitive yields:
  - `bid=1.5` → `sell_value=150` (impossible price, no error)
  - `bid=-0.1` → `sell_value=-10` (negative proceeds fed into EV comparison)
  - `bid=1.0` or `bid=0.0` → silent zero fee (entry side raises; symmetry breaks)

  Two separate harms:
  (i) Garbage-in becomes garbage-decisions in the EV branch (`sell_value > hold_value`) without any fail-closed signal.
  (ii) Reintroduces a fee model the codebase has already deprecated — the `T6.4-hardening` clamp at `hold_value.py:184-197` exists precisely because the project committed to "canonical `polymarket_fee()` raises; callers clamp at boundary."
- Proposed fix:
  Reuse the canonical:
  ```python
  from src.contracts.execution_price import polymarket_fee as _canonical_polymarket_fee

  def _per_leg_sell_value(shares, bid, fee_rate):
      if shares <= 0.0:
          return 0.0
      if not math.isfinite(bid) or not (0.0 <= bid <= 1.0):
          raise ValueError(f"_per_leg_sell_value: bid {bid!r} outside [0,1]")
      if fee_rate <= 0.0 or bid <= 0.0 or bid >= 1.0:
          return float(shares) * float(bid)
      return float(shares) * (float(bid) - _canonical_polymarket_fee(bid, fee_rate))
  ```
  Or use `HoldValue.compute_with_exit_costs`'s clamp pattern (lines 184-197) if boundary tolerance for `{0.0, 1.0}` is required.

### F-3 — Module docstring publishes a wrong sell-value formula
- ID: F-3
- Severity: **SEV-2**
- Area: D3
- Probe that surfaced it: #5 (secondary)
- Finding:
  `src/strategy/exit_family_optimizer.py:17` states:
  ```
  sell_value_i = x_i · b_i · (1 - fee_rate · b_i · (1 - b_i))
  ```
  Numerically: 100×0.5×(1 − 0.02×0.5×0.5) = 49.75.
  The impl (lines 138-143) computes `x · (b − fee_rate·b·(1−b))` = 100×(0.5 − 0.005) = 49.50.
  These are not equal: the docstring puts the per-share fee inside a multiplicative `1 − …` envelope; the impl subtracts a per-share fee from the per-share price (correct per PR #348).

  Per Fitz constraint #2 ("Translation Loss is a Thermodynamic Limit") and the project's "Code shows function. Code NEVER shows logic" doctrine, the docstring IS the math spec future agents will read when porting / refactoring. Shipping a wrong spec next to correct code primes the next refactor to "fix the code to match the docstring," reversing the PR #348 win.
- Proposed fix:
  Replace docstring line with: `sell_value_i = x_i · (b_i - fee_rate · b_i · (1 - b_i))` (or, equivalently, `x_i · b_i · (1 - fee_rate · (1 - b_i))`).

### F-4 — `hold_cost_extras` + `daily_hurdle_dollars` API contract is undefined; double-counting risk at integration
- ID: F-4
- Severity: **SEV-2**
- Area: D3 + integration-plan
- Probe that surfaced it: #8 + #9
- Finding:
  `optimize_exit_family` has TWO orthogonal hurdle inputs:
  1. Per-leg `ExitLegInput.hold_cost_extras` (default 0.0): docstring says "$ hold-side time + crowding cost from HoldValue."
  2. Family-level `daily_hurdle_dollars` (default 0.0): docstring says hurdle for the cash-out comparison.

  `HoldValue.compute_with_exit_costs` (`src/contracts/hold_value.py:122-135`) takes a `daily_hurdle_rate` (a rate) and internally produces `fee_cost + time_cost + correlation_crowding` as dollar costs summed into `net_value`. The integration-plan implies `hold_cost_extras` will be the sum of HoldValue's `fee_cost + time_cost + crowding` (probe brief #9). If the caller ALSO passes `daily_hurdle_dollars > 0`, the time-cost is **double-counted**: once inside HoldValue's `time_cost` and again in the optimizer's hurdle.

  The integration plan is silent on this. There is no docstring assertion of "must be zero when `hold_cost_extras` carries time_cost," and no unit test exercises both inputs simultaneously. The follow-up wiring PR will hit this at the seam.
- Proposed fix:
  In the optimizer docstring (and ExitLegInput docstring), declare ONE canonical contract: e.g., *"`hold_cost_extras` MUST contain the total dollar deduction (fee + time + crowding); `daily_hurdle_dollars` is an additional family-level minimum profit margin and is composable additively with `hold_cost_extras`. Time-cost goes in `hold_cost_extras`, never both places."* Add a unit test that locks the contract: a leg with both inputs set to nonzero values produces `hurdle = hold_cost_extras + daily_hurdle_dollars` (not double-counted).

### F-5 — Test count claim mismatch in commit message (numerical mismatch, not behavioral)
- ID: F-5
- Severity: **NIT**
- Area: meta
- Probe that surfaced it: (orientation)
- Finding:
  Commit message claims "37 D1 + 11 D2 + 15 D3 + 15 cross-module relationship = 78 tests". Actual `def test_` counts are 23 + 11 + 15 + 11 = 60. After parametrize expansion, `--collect-only` yields exactly 78 tests, so the headline number is correct but the per-file breakdown is not the count an auditor running `grep -c 'def test_'` will see. Minor but adds friction.
- Proposed fix:
  Update commit message (or PR body when opened) to per-file `def test_` counts (60) with a note "78 with parametrize."

### F-6 — `Position.evaluate_exit` short-circuit defaulting ON at the integration PR is *conditionally* defensible
- ID: F-6
- Severity: **SEV-3** (forward-looking; integration-plan concern)
- Area: integration-plan
- Probe that surfaced it: #15
- Finding:
  `architecture/exit_strategy_integration_plan_2026_05_27.md:84-92` declares D5 short-circuit ships default-ON and only D3 monitor wiring + D4 forward-edge ship default-OFF. Default-ON for D5 is defensible IF and ONLY IF the buy_no semantic bug (F-1) is fixed first — otherwise D5 (which short-circuits on impossibility) is fine for buy_yes legs but the same wiring PR's D3 monitor-grouping path will fold buy_no legs through the broken EV branch. As written, the priority order is right but the dependency is implicit.
- Proposed fix:
  Add to the integration plan: *"D5 default-ON is contingent on F-1 (buy_no direction semantics) being resolved in this pure-math PR. If F-1 ships unresolved, ship D5 default-OFF on canary, the entire family-grouping path stays OFF, and the only safety improvement that lands is the per-leg impossibility short-circuit on buy_yes — which can be ported into evaluate_exit directly without touching D2/D3."*

## Probes reproduced

1. **D1 feasibility shoulder math (HIGH/LOW × upper/lower shoulder × None vs +/-inf)** — PASSED. `None` and `±inf` both normalize through inequalities correctly; obs-on-bin-edge yields `contains_current_record` (correct per operator §2); strict-less-than at the impossibility boundary correctly excludes equality.
2. **D1 authority gating completeness (6-gate)** — PASSED. `freshness_status="FRESH"` correctly trusts upstream (cycle_runtime stamps FRESH only when `obs_coverage == "OK"`, see `cycle_runtime.py:3649`). The `observation_time_utc` is intentionally not double-checked at D1; this is by design per separation-of-concerns and acknowledged in integration plan Q1. Defensible.
3. **D2 renormalisation correctness** — PASSED. Mass conservation, NaN/negative/length-mismatch all raise ValueError correctly. Input p_family is not mutated (tests/test_exit_constrained_posterior.py:177).
4. **D2 contradiction epsilon (1e-9 vs Polymarket tick 0.01)** — PASSED. Asymmetric risk: false-positive contradiction triggers fail-closed exit-only, which is the correct safety response. 1e-9 is a defensible floor; no operator-level magnitude was specified.
5. **D3 fee model** — **FAILED.** See F-2 + F-3.
6. **D3 buy_no nuance** — **FAILED.** See F-1.
7. **D3 contradiction-fail-closed direction** — **FAILED.** See F-1 (same root cause).
8. **D3 hurdle semantics** — partially FAILED. See F-4.
9. **D3 hold_cost_extras source / unit consistency** — partially FAILED. See F-4.
10. **D1/D2/D3 typing strictness** — PASSED (defensively coerces via `float(...)` and explicit `_REQUIRED_METRICS` set; lowercase `"ok"` for coverage_status is upper-cased; integer truthy 1 is required exactly via `!= 1`).
11. **Relationship test 1: mutation resistance** — PASSED. Inverting the impossible/feasible mask would flip `(True, False, False)` → `(False, True, True)` and fail the assertion.
12. **Relationship test 2: bid/p gap robustness under hurdle** — PASSED. The 0.75 bid vs 0.7 pre-obs / 0.778 post-obs gap survives hurdle=0.01.
13. **Mass conservation regression catch** — PASSED. Wrong-denominator regression (dividing by `sum(p)` instead of `feasible_mass`) would break `sum(p_obs) == 1.0` on `test_mass_sums_to_one_under_partial_truncation`.
14. **Audit doc accuracy** — PASSED. Audit doc citations match the actual code (`portfolio.py:739-742`, `cycle_runtime.py:3111`, `:3558-3700`, etc., all verified). Integration plan open questions Q1-Q4 are real (not pseudo-questions); Q4 (RED vs OBSERVATION_IMPOSSIBLE precedence) is the highest-risk open item for the follow-up PR.
15. **D5 default-ON for follow-up PR** — partially PASSED, see F-6.
16. **Import-isolation of math layer** — PASSED. `grep -rn "exit_observation_constraint|exit_constrained_posterior|exit_family_optimizer" src/` returns only the 3 modules' internal cross-imports. No production wiring path imports these symbols. Confirms the safety boundary of the split.

All 78 tests pass (`pytest tests/test_exit_observation_constraint.py tests/test_exit_constrained_posterior.py tests/test_exit_family_optimizer.py tests/test_exit_strategy_relationship.py`).

## Lock-ins worth holding

These the PR gets right and a future "simplification" must not regress:

- **Authority-status authority is a hard gate, not a soft signal.** `ADVISORY_ONLY` returns `"unknown"` for every bin, and D2 returns input untouched. The relationship test `TestAdvisoryNeverYieldsDeterministicExit` locks this; a future refactor that decides "advisory should still produce some impossibility hints" must rewrite that test (visible diff).
- **D2 contradiction → all-zero `p_obs`, no fabricated distribution.** No "renormalise tiny remaining mass to 1" temptation. The `p_obs = (0.0, 0.0, ...)` invariant is locked by `test_all_bins_impossible_flags_contradiction_and_returns_zeros`.
- **D2 input non-mutation.** `test_input_p_family_is_not_mutated` locks the exit-only contract — entry sizing cannot accidentally pick up a truncated posterior via shared list reference. Hold this regardless of any future "performance" rewrite to numpy in-place ops.
- **Per-bin shoulder math survives both `None` and `±inf` Bin shoulders.** The implicit-via-arithmetic path is fragile-looking but actually robust (both `None` and `inf` collapse to the same inequality semantics); a future "clean up the bin shoulder representation" refactor must not accidentally change the comparator direction.
- **Strict inequality at the impossibility boundary.** `hi < obs` impossible vs `lo <= obs <= hi` contains_current_record. The boundary tests `test_bin_upper_edge_equals_observed_is_current_record` and `_lower_edge_` lock this; a future agent simplifying to `<=` for "cleaner code" must re-derive the operator §2 contract.

## Recommended pre-open actions

In order; (1)-(3) are blocking, (4)-(6) are highly recommended same-PR:

1. **Fix F-1 (buy_no semantics).** Make hold_value direction-aware in EV branch AND contradiction branch, OR short-circuit all buy_no legs to `HOLD reason=DEFER_TO_EVALUATE_EXIT` and let `Position.evaluate_exit` decide (cleaner separation; aligns with commit message's "defer to standard cash-out" promise). Replace the locked-in test `test_buy_no_on_impossible_yes_bin_falls_through_to_evaluate_exit` with one that asserts the correct verdict, then add a new relationship test that locks the buy_no contract: `hold_value_buy_no == shares × (1 - p_obs) - extras`.
2. **Fix F-2 (fee primitive).** Replace `_polymarket_maker_fee` with the canonical `polymarket_fee` from `src/contracts/execution_price.py`. Either let it raise on invalid bids (forcing the caller to clamp like `HoldValue` does) or add explicit input validation to `_per_leg_sell_value`. Add tests for `bid in {0.0, 1.0, -0.1, 1.5, math.nan, math.inf}` — every one of these is currently silently accepted.
3. **Fix F-3 (docstring formula).** One-line correction. Cheap; high value because future agents will treat the docstring as authority.
4. **Fix F-4 (hurdle contract).** Add docstring clarification + one composability test. Avoids a near-certain integration-PR bug.
5. **Fix F-5 (commit message numbers).** When opening the PR body, replace "37+11+15+15" with the actual per-file `def test_` counts (60 functions, 78 with parametrize).
6. **Amend integration plan re: F-6.** Add the dependency callout so the follow-up PR author cannot ship D5 default-ON if F-1 was deferred.

After (1)-(3) ship, the math layer is genuinely "safe to build on." The current code is not. Re-request critic after the fixes; expect the next pass to be terse.

---
*Pre-commitment predictions:* I predicted (i) bin-shoulder strict-vs-nonstrict boundary bug, (ii) 1e-9 epsilon too aggressive, (iii) fee divergence from PR #348, (iv) contradiction-direction ambiguity, (v) hurdle unit mix, (vi) stale-obs gate hole, (vii) default-ON for D5 wrong on canary. Of these: (i)(ii)(vi) PASSED contrary to prediction; (iii)(iv)(v)(vii) FAILED as predicted — with the largest find being a buy_no direction-flip that wasn't on my pre-list at all but emerged from probe #6. Adversarial-mode escalation triggered after F-1 surfaced; expanded scope to the contradiction branch (same root cause, +1 finding) and the docstring/spec layer (+1 finding).
