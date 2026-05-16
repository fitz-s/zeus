# Wave33: Passive Maker Fee Authority

Created: 2026-05-08
Authority basis: object-meaning invariance mainline; topology route `pricing semantics authority cutover`; Polymarket fee and post-only order documentation.

## Invariant

`post_only_passive_limit` denotes an order that can fill only as maker. Polymarket platform fees are taker-only; a post-only order that would take is rejected instead of filled. Therefore a passive maker-only executable cost basis must not carry the market taker fee as its realized/economic fee object.

## Scope

Patch only the corrected executable cost-basis boundary:

- `src/contracts/execution_intent.py`
- `src/engine/cycle_runtime.py` only if needed to expose preserved fee authority in the runtime shadow payload
- relationship tests in `tests/test_executable_market_snapshot_v2.py` and `tests/test_runtime_guards.py`

No live DB writes, migrations, backfills, venue mutation, or replay/report promotion.

## Failure

`ExecutableCostBasis.from_snapshot()` read snapshot fee metadata and applied `_fee_adjusted_price(...)` for all order policies. This made a `post_only_passive_limit` maker-only cost object look like a taker-costed object whenever the market snapshot had a nonzero taker fee.

## Repair Plan

1. Add an order-policy fee authority transform: marketable/conservative policies keep snapshot taker fee; `post_only_passive_limit` uses applicable platform maker fee `0`.
2. Preserve source authority by tagging the passive fee source as a maker-only exemption over the original snapshot fee source.
3. Make wrong construction unrepresentable: a post-only passive cost basis or final intent with nonzero applicable fee rate fails closed.
4. Add relationship tests proving passive maker-only cost basis keeps expected fill price, fee rate `0`, and runtime shadow semantics even when the underlying snapshot has a nonzero taker fee.

## Verification

- `py_compile` for changed source/tests: PASS.
- `tests/test_executable_market_snapshot_v2.py::test_order_policy_requires_matching_depth_proof`,
  `tests/test_executable_market_snapshot_v2.py::test_final_execution_intent_rejects_taker_fee_on_post_only_passive_policy`,
  and `tests/test_runtime_guards.py::test_executable_snapshot_repricing_updates_edge_and_size`: PASS.
- `tests/test_executable_market_snapshot_v2.py`: PASS, 67 tests.
- Focused runtime shadow tests:
  `tests/test_runtime_guards.py::test_executable_snapshot_repricing_updates_edge_and_size`
  and `tests/test_runtime_guards.py::test_live_reprice_rejects_passive_without_maker_only_support`: PASS.
- `tests/test_execution_price.py` plus evaluator fee-rate focused tests:
  PASS, 27 passed / 1 xfailed.
- `tests/test_runtime_guards.py`: PASS, 236 passed / 2 skipped.
- Planning-lock, map-maintenance, and diff check: PASS.
- Critic pass 1: REVISE. Findings were that passive candidate sizing still
  consumed `decision.execution_fee_rate`, and direct construction could pass
  a prefix-only fee source. Both were repaired: passive initial sizing now uses
  maker fee `0` and only marketable depth sizing uses taker fee; passive
  `fee_source` must be `post_only_maker_fee_exempt:<nonempty snapshot source>`.
- Added marketable-depth control coverage so non-passive
  `marketable_limit_depth_bound` still consumes taker fee for size,
  `candidate_fee_adjusted_execution_price`, `fee_rate`, and `fee_source`.
- Critic pass 2: APPROVE. No Critical or Important findings for the Wave33
  slice; critic noted unrelated dirty worktree changes were not reviewed.

## Topology Note

Source routing admitted the repair slice. Packet creation admitted this PLAN
and the `docs/operations/AGENTS.md` registry row. Updating
`docs/to-do-list/known_gaps.md` after the wave still routes as advisory-only /
out-of-scope despite the local to-do AGENTS registry; leave that ledger update
until topology admits active-checklist closeout edits or an operator explicitly
overrides the route.

## Residual

This wave does not add builder-fee economics. Builder fees are a distinct external authority not represented by current snapshot fee details; if introduced, they need separate provenance and order-route fields rather than reusing the platform taker-fee field.
