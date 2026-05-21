# Live Contract Authority Pass Plan

## Goal

Repair the live-money authority seams identified in the operator full analysis so B1/B2/B3 consume shared truth instead of reconstructing inconsistent local authority.

This plan does not complete the live goal by itself. Completion still requires merge to `origin/main`, live root fast-forward, daemon restart, loaded-SHA proof, and sustained source/forecast/settlement/evaluator/sizing/venue/reconcile/redeem progress with traceable real order truth.

## Scope

Initial admitted slice:
- `src/data/market_scanner.py`
- `src/contracts/executable_market_snapshot_v2.py`
- `tests/test_executable_market_snapshot_v2.py`

Contract A invariant:
- NegRisk child `active=False` is not a tradability blocker when Gamma and CLOB prove `closed=False`, `acceptingOrders=True`, and `enableOrderBook=True`.
- Scanner, snapshot capture, and submit authorization must use the same tradability semantics.
- `active` remains an audit field, not an execution blocker for this child-market shape.

Later slices require separate topology/planning-lock checks:
- Passive maker fill-adjusted economics.
- Canonical order truth reducer.
- Family exposure from command/envelope/snapshot identity.
- Business-plane health progress counters.

## Relationship Tests First

Add tests before implementation:
- Capture succeeds for `active=False`, `closed=False`, `acceptingOrders=True`, `enableOrderBook=True`.
- Submit authorization succeeds for a fresh snapshot with `active=False` but accepting orderbook.
- Existing negative controls continue to reject `closed=True`, `enableOrderBook=False`, `acceptingOrders=False`, stale snapshots, token mismatch, and CLOB identity mismatch.

## Verification

Run focused tests first:

```bash
PYTHONPATH=. pytest -q tests/test_executable_market_snapshot_v2.py
```

Then run the route-required broader gate slices selected by topology before PR.

## Stop Conditions

Stop and re-plan before editing:
- schema files;
- live root;
- production DB;
- daemon launch/restart files;
- command recovery or family exposure code in this Contract A slice.

