# Stage 7c — negrisk_routes Implementation Report

**Module:** `negrisk_routes`
**Date:** 2026-06-14
**Worktree:** `/Users/leofitz/zeus/.claude/worktrees/qkernel-rebuild` (isolated; live daemon runs a different tree)
**Authority:** `docs/rebuild/consult_build_spec.md` lines 654-732 (the `Create src/execution/negrisk_routes.py` block) reconciled against `docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md` (VENUE-PRIMITIVE VERDICT §7-19 + the `:728-732` BLOCKER row).

---

## What was built

The family **route engine** over the captured `FamilyBook`: given a complete (or partial) family book and a target buy size, it enumerates and prices SIZE-AWARE every executable (or shadow) way to acquire a YES_i or NO_i claim, and runs the family arbitrage checks.

The cost path is the **leaf** `executable_cost` walker only — this module composes leaf costs, it never walks an order book and never invents a midpoint / last-trade / NO-complement price. A synthetic basket's `avg_cost` is the share-weighted sum of its legs' leaf costs; an arb's clearing test is a difference of leaf costs.

### Files written (NEW only — no live file touched)

| File | Symbols |
|---|---|
| `src/execution/negrisk_routes.py` | `RouteLeg` (drift-resolved), `RouteCost` (spec 658-676), `NegRiskRouteSet` (spec 677-685, + `best_no_route` dominance helper), `RouteType` literal, `NegRiskRouteError`, `CONVERSION_VENUE_PRIMITIVE_ABSENT` reason const, `build_negrisk_route_set` (entry point), and the private route builders `_direct_yes_route` / `_direct_no_route` / `_synthetic_not_i_route` / `_pair_arb_route` / `_full_yes_basket_arb_route` / `_conversion_route`, plus leaf-cost helpers `_leg_cost` / `_max_depth_shares` / `_zero_cost`. |
| `tests/execution/test_negrisk_route_set.py` | 3 spec-named RED-on-revert tests + 6 supporting contract tests. |

### Dataclasses — EXACT spec field names

- **`RouteCost`** (spec 658-676): `route_id`, `route_type` (the verbatim 6-literal set), `instrument`, `shares`, `avg_cost: ExecutionPrice`, `max_shares`, `legs: tuple[RouteLeg, ...]`, `executable`, `reason: str | None`.
- **`NegRiskRouteSet`** (spec 677-685): `direct_yes`, `direct_no`, `synthetic_not_i`, `pair_arbs`, `full_basket_arbs`, `conversion_routes`. (`best_no_route` added as a helper that applies route dominance; it is not a stored field.)

### Algorithm — implemented EXACTLY per spec

- **YES_i buy** (688-690): route = direct YES_i ask, walked size-aware.
- **NO_i buy** (692-699):
  - negRisk=False → only the direct NO_i route exists (`_synthetic_not_i_route` returns `None`).
  - negRisk=True → both the direct NO_i and the synthetic sibling-YES basket are built; the synthetic route buys equal shares of every OTHER sibling's YES (= the NO_i payoff vector `1 - e_i`); its `max_shares` is the **minimum** depth-supported shares across siblings (spec 699); its `avg_cost` is `Σ_{j≠i} yes_ask_cost(j, s)`.
- **Route dominance** (722-726): `best_no_route(bin_id)` = `min(direct_no_cost(i,s), synthetic_yes_basket_cost(i,s))` over the executable candidates — the only producer of the chosen NO cost.
- **Pair arb** (703-707): `ask_yes_i(s) + ask_no_i(s) + fees < 1.0`, size-aware. (The leaf already applies the taker fee per side on a buy, so the combined per-unit cost *is* the fees-inclusive total.)
- **Full YES basket arb** (709-713): `Σ_i ask_yes_i(s) + fees < 1.0`, size-aware; requires a complete book.
- **Conversion** (715-720, 728-732): enumerated as `CONVERSION_SELL_BASKET`, always `executable=False`.

---

## Operator-law compliance (corrected transformation, not a detector)

- **NO mispricing is structurally impossible, not caught by a gate.** The dominance `min(direct_no, synthetic_yes_basket)` *is* the transformation that produces the NO cost. There is no "price off the direct NO ladder, then if it looks expensive try the basket" detector bolted on top of a direct-only transform — the comparison is the only path that yields `not_i_cost`. A NO that is genuinely a sibling-YES basket can never be over-priced off one direct ladder.
- **No silent clamp.** A route that cannot fill the requested size is `executable=False` with an honest `NO_DEPTH`-class reason — never quietly filled at a smaller size. (`test_depth_starved_route_is_non_executable_not_clamped`.)
- **Conversion shadow = honoring the venue BLOCKER, not a cap.** Conversion routes are `executable=False` because the on-chain convert/merge/split venue primitive is genuinely ABSENT — there is no transform that could execute them — not because a sanity-check rejects an otherwise-working route. The exception is explicitly sanctioned by the module brief ("conversion/CONVERT routes marked shadow is honoring the venue-primitive BLOCKER, not a cap").
- **BASKET_COST_OVER_UNITY** is *not* a clamp that hides a bad value: a synthetic basket whose summed per-share cost exceeds 1.0 is marked non-executable so it can never win dominance (a direct NO ≤ 1.0 dominates it); the true summed cost is recorded in the reason. The bad output (a >1.0 NO cost masquerading as valid) is made impossible — `ExecutionPrice` in probability units cannot exceed 1.0, and the route is honestly non-executable rather than clamped to 1.0 and served.

---

## Venue-primitive verification (spec line 730, re-run for this build)

```
grep -rnE "def convert|mergePositions\(|convertPositions\(|splitPosition\(|def merge_positions|def split_position" src/venue/ src/execution/
→ ZERO executable hits (exit 1).
```

Confirms the drift-ledger VERDICT: only redeem/`redeemPositions` (`polymarket_v2_adapter.py:226/:872/:1496/:2669/:2733`) + submit/cancel are wired. The broad `convert|merge|split` strings elsewhere are unrelated in-memory Python helpers (`family_exclusive_dedup.py:748`, `bankroll_provider.py:363`). `test_conversion_routes_stay_shadow_when_venue_primitive_absent` re-runs this grep inside the test so the shadow status is grounded in the live tree — if a convert/merge/split method ever lands, the grep hits and the test flags that conversion routes must be re-evaluated.

**Consequence honored:** `CONVERSION_SELL_BASKET` / `conversion_routes` stay SHADOW (`executable=False`, reason = `CONVERSION_VENUE_PRIMITIVE_ABSENT`). `DIRECT_YES`, `DIRECT_NO`, `SYNTHETIC_NOT_I_YES_BASKET`, `PAIR_ARB`, `FULL_YES_BASKET_ARB` proceed live via independent native `submit()` orders (each a `RouteLeg`).

---

## Drift resolved (recorded per operator law)

1. **`RouteLeg` is undefined in the spec.** The spec writes `legs: tuple[RouteLeg, ...]` at lines 673 and 921 but never defines `RouteLeg` anywhere in `consult_build_spec.md`, and no live `src/` type by that name exists (grep: zero hits). **Resolution toward the live execution model:** a `RouteLeg` is one INDEPENDENT native order the venue `submit()` lane already executes — `(condition_id, bin_id, token_id, direction, shares, leg_cost: ExecutionPrice)`. It is the atomic unit a route decomposes into (a direct route is one leg; a synthetic NO_i basket is one `buy_yes` leg per OTHER sibling; an arb is one leg per crossed side), and its `leg_cost` is the leaf `executable_cost` `ExecutionPrice` for that single native ladder walk. No conversion leg is ever emitted live.

2. **Directory drift (ledger MINOR `:654`):** the ledger notes `src/decision/` does not exist, but `negrisk_routes.py` belongs under the existing `src/execution/` package — so no new package was needed and none was created. (The `src/decision/` gap is a different module's concern.)

3. **`OutcomeSpace` has no `index` method** — same drift the Stage-7a `instruments.py` already resolved. This module derives the sibling set by iterating `omega.bins` and comparing `bin_id`, never calling a non-existent `omega.index(...)`.

No other spec/live disagreement was encountered. All imported dependencies (`FamilyBook`/`MarketBook`/`ExecutableLadder`, `executable_cost`/`NativeQuoteBook`, `Instrument`, `ExecutionPrice`) matched their real on-disk shapes and were reused unchanged.

---

## RED-on-revert proof (each spec-named test genuinely fails when the fix is reverted)

| Test | Revert applied | Result |
|---|---|---|
| `test_synthetic_yes_basket_dominates_expensive_direct_no` | `best_no_route` → `return direct` (direct-only, ignore basket) | **FAILED** — `assert 'DIRECT_NO' == 'SYNTHETIC_NOT_I_YES_BASKET'` |
| `test_negrisk_routes_disabled_when_flag_false` | flag gate `if not enable_negrisk_routes: continue` → ignored | **FAILED** — `synthetic_not_i` non-empty with flag OFF |
| `test_conversion_routes_stay_shadow_when_venue_primitive_absent` | conversion route `executable=False` → `True`, reason dropped | **FAILED** — `assert True is False` |

Each revert was applied, the test confirmed RED, then the module was restored and byte-diffed clean against the pre-revert backup.

---

## Test results

### Module tests — `tests/execution/test_negrisk_route_set.py`

```
........                                                                 [100%]
8 passed in 0.90s
```

Tests: the 3 spec-named RED tests above, plus `test_non_negrisk_market_has_no_synthetic_route`, `test_pair_arb_executable_only_when_combined_ask_below_one`, `test_full_yes_basket_arb_size_aware`, `test_depth_starved_route_is_non_executable_not_clamped`, `test_non_positive_shares_refused`.

### Money-path regression — `tests/money_path tests/strategy/live_inference`

```
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.11s
```

Unaffected (expected: this is a NEW file imported nowhere live yet; it is wired into the reactor at integration/Wave 5).
