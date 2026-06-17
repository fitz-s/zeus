# Created: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md lines 906-941;
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md

# Wave 4 / Stage 10 — `liquidation_value` implementation report

## Module

`liquidation_value` — exit value = max liquidation value over the family position
vector; `exit_family_optimizer` resurrected as input. GREENFIELD: new files only,
no live-file edits. Stage 10 wiring into the reactor/exit tick is deferred to
integration/Wave 5 (drift ledger).

## Files written

| Path | Symbols |
|---|---|
| `src/execution/liquidation_value.py` | `PositionVector`, `LiquidationRoute`, `LiquidationDecision`, `RouteLeg`, `LiquidationValueEngine`, `LiquidationValueError`, `direct_sell_value`, `conversion_basket_sell_value`, `hold_to_redeem_value`, `liquidation_decision`, `position_vectors_from_portfolio`, `position_vector_hash`, `CONVERSION_PRIMITIVE_ABSENT`, `_route_selection_key`, `_leaf_sell_unit_value`, `_run_optimizer_for_trace` |
| `tests/execution/test_liquidation_value_engine.py` | `test_direct_sell_is_one_route_not_authority`, `test_no_position_chooses_conversion_basket_when_more_valuable`, `test_hold_to_redeem_selected_when_all_sell_routes_worse`, `test_position_vector_assembled_by_family_key_grouping` |

## Spec lines implemented (EXACT field names)

- **PositionVector** (spec 910-914): `family_id`, `quantities_by_instrument`,
  `payoff_vector_by_instrument`. Plus a non-spec `directions_by_instrument`
  (default `{}`) to carry the held side per instrument so the redeem payoff
  (YES wins on its own bin; NO wins on every other bin) and the sell ladder
  (`sell_yes`/`sell_no`) are unambiguous without re-deriving the side. It is
  additive (keyword default) so the spec's three-field constructor still works.
- **LiquidationRoute** (spec 916-922): `route_type`
  (`Literal["DIRECT_SELL","CONVERT_TO_BASKET_SELL","HOLD_TO_REDEEM"]`),
  `value_usd` (`Decimal`), `executable` (`bool`), `legs` (`tuple[RouteLeg, ...]`),
  `reason` (`str | None`).
- **LiquidationDecision** (spec 924-928): `chosen`, `alternatives`,
  `position_vector_hash`.
- **Exit algorithm** (spec 930-938): `direct = direct_sell_value(position,
  family_book)`; `convert = conversion_basket_sell_value(position, family_book,
  venue_primitives)`; `hold = hold_to_redeem_value(position, joint_q,
  time_to_resolution, risk_policy)`; `chosen = max([direct, convert, hold],
  key=lambda r: r.value_usd if r.executable else -inf)`. Implemented literally in
  `LiquidationValueEngine.decide` via `_route_selection_key` (executable→value,
  non-executable→`-inf`).
- **Line 940 contract**: the current single-token `ExitIntent`/`place_sell_order`
  path "becomes one route under LiquidationValueEngine, not the exit authority" —
  `DIRECT_SELL` is one of three routes; there is no branch returning it without
  comparing to `hold`.

## Corrected transformation (operator law — no detector/gate/clamp)

The defect (spec line 940) is that the live exit treats the direct sell of the one
current token as THE exit. The fix is structural, not a guard:

- `LiquidationDecision.chosen` is the literal `max(...)` over the routes. There is
  no code path that returns `direct` without comparing its value to `hold`, so
  "direct sell is the exit authority" is **unconstructable**. Whenever
  hold-to-redeem beats the direct bid sell (the deep-discount-bid loss the old
  per-token sell would have realized), the chosen route is `HOLD_TO_REDEEM` by
  construction.
- A non-executable route scores `-inf` in `_route_selection_key`, so
  `CONVERT_TO_BASKET_SELL` (no venue primitive) is **structurally excluded** from
  the argmax — recorded as a receipt alternative, not gated out after a wrong
  pick. Marking conversion `executable=False` honors the venue BLOCKER (an absent
  transform has no realized value); it is NOT a cap on a fabricated basket value.
- `direct_sell_value` keeps cost **leaf-only**: it hands each leg's native bid
  ladder (from the `FamilyBook` `MarketBook`) to the leaf `executable_cost` walker
  (`sell_yes`/`sell_no`), so the midpoint / last-trade / NO-complement bans apply
  to every sell leg. The engine composes; it never walks a book.
- `hold_to_redeem_value` reads the held-side win probability as the Arrow-Debreu
  payoff row dotted with the ONE normalized joint q (`q_held = payoff · q`). The
  YES/NO flip is in the payoff row (YES = 1 on its own bin; NO = 1 on every other
  bin), so there is no place to re-derive the side wrong.

## Drift resolved (toward the live type)

| Drift | Severity | Resolution |
|---|---|---|
| `exit_family_optimizer` resurrect-as-input, not rewrite (drift ledger :32) | MAJOR | `optimize_exit_family(legs=...)` is the per-leg direct-sell computer. The engine shapes the family vector into `ExitLegInput[]` (exactly what the optimizer consumes), runs it under an `ADVISORY_ONLY` `SettlementProgressConstraint` + an advisory `ObservationConstrainedPosterior` (zero p_obs, matching length) so it emits per-leg `sell_value` decisions WITHOUT re-deciding hold (hold is owned by the engine from joint_q per spec 936). `_per_leg_sell_value`'s canonical fee math is preserved (the route leg's realized value is the leaf-priced net-of-fee bid; the optimizer cross-checks the family vector). Not rewritten. |
| Family position-vector ABSENT at the exit site (drift ledger :33) | MAJOR | CREATED by `position_vectors_from_portfolio`, grouping portfolio positions on the `family_exclusive_dedup` `WeatherFamilyKey` (via `_family_key`) — the SAME grouping primitive the entry-side family gate uses. One `PositionVector` per `(city, target_date, metric[, market_family_id])` family. |
| convert/merge/split venue primitive ABSENT (drift ledger VENUE-PRIMITIVE VERDICT) | BLOCKER | `CONVERT_TO_BASKET_SELL` built with `executable=False`, `value_usd=0`, `reason=CONVERSION_PRIMITIVE_ABSENT`. Scored `-inf`, never chosen. `direct_sell_value` (native bid sell via leaf) and `hold_to_redeem_value` (redeem wired at `polymarket_v2_adapter.py:872/1496`) are executable. `_venue_has_conversion_primitive` returns True only when a real convert/merge/split callable is supplied — no env/flag flips it. |
| `RouteLeg` source module (`negrisk_routes.py`, spec line 654) not yet built | resolved toward live (greenfield) | `negrisk_routes.py` does not exist in this tree, so this module defines its own `RouteLeg` (new-file-only; cannot import from a non-existent module). To be unified at integration/Wave 5 when `negrisk_routes` lands. |
| RED test name self-inconsistency (spec :64 vs :1216) | MINOR | Canonical name `test_no_position_chooses_conversion_basket_when_more_valuable` (spec :1216), per the drift ledger recommendation. |
| Redeem is "FORBIDDEN to submit" (operator law 2026-06-10) | clarified | Zeus never submits a redeem tx (third-party auto-redeem owns it). HOLD_TO_REDEEM is the **value** of holding to resolution and being redeemed externally — accounting, not a submit. The route's realized value (`q · payoff`) is the held-to-resolution payout, which is real; no submit path is invoked. |

## RED-on-revert verification

Each spec-named test fails if the corrected transform is reverted to the broken
behavior the spec replaces (verified by temporary in-place reverts, then restored):

- **Revert 1** — make `chosen = direct` (direct is the exit authority):
  `test_direct_sell_is_one_route_not_authority` and
  `test_hold_to_redeem_selected_when_all_sell_routes_worse` both turn RED
  (`AssertionError: 'DIRECT_SELL' == 'HOLD_TO_REDEEM'`).
- **Revert 2** — ignore executability in `_route_selection_key` (score by raw
  value) with the absent shadow basket carrying a dominating notional:
  `test_no_position_chooses_conversion_basket_when_more_valuable` turns RED
  (the unexecutable basket becomes the chosen route).

Both reverts were applied to a backed-up copy, observed RED, then the original
restored; the suite is GREEN on the restored module.

## Test results

```
$ /Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/execution/test_liquidation_value_engine.py
....                                                                     [100%]
4 passed in 0.83s
```

Money-path / live_inference regression (the module is new-files-only, so this
confirms zero live regression):

```
$ /Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/money_path tests/strategy/live_inference
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.15s
```

## Constraints honored

- NEW FILES ONLY — touched no live file. Not wired into the reactor (Wave 5).
- No gate/cap/clamp/haircut/sanity-check that catches a bad value and leaves a
  broken transform in place. The "direct-as-authority" and "unexecutable basket
  chosen" bad outputs are made mathematically impossible by the argmax + `-inf`
  selection key, not detected after the fact. The `executable=False` on conversion
  honors the venue-primitive BLOCKER, not a cap.
- Did not commit / did not `git add` (orchestrator commits).
