# Live Side-Specific Entry Authority Plan

## Goal

Repair the live entry substrate/evaluator contract exposed after PR #253: a persisted weather outcome with an executable BUY ask must not be rejected before edge evaluation merely because the YES token has no bid depth.

## Invariant

BUY entry price authority is the selected token's best ask. Two-sided VWMP remains preferred when bids and asks exist, but missing bid depth alone is not a BUY-entry blocker. Missing asks remain fail-closed.

## Scope

- `src/data/polymarket_client.py`: expose ask-only BUY quote helper.
- `src/engine/evaluator.py`: use ask-only fallback only for missing-bid orderbook errors on BUY entry quote construction.
- `scripts/live_health_probe.py`: surface degraded composite live-health state so process/status green cannot mask business-plane failure.
- Focused relationship tests in `tests/test_executable_market_snapshot_v2.py`, `tests/test_market_scanner_provenance.py`, `tests/test_runtime_guards.py`, and `tests/test_live_health_probe_forecast_owner.py`.

## Non-goals

- No production DB mutation.
- No daemon restart from this branch.
- No new family portfolio optimizer.
- No change to SELL/exit bid requirements.
- No relaxation of stale substrate or incomplete topology fail-closed behavior.

## Verification

- `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_executable_market_snapshot_v2.py tests/test_market_scanner_provenance.py`
- `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py::test_entry_evaluator_uses_ask_only_buy_quote_when_yes_bid_is_absent tests/test_runtime_guards.py::test_passive_economic_floor_uses_fill_adjusted_expected_profit tests/test_runtime_guards.py::test_passive_economic_floor_uses_adverse_selection_penalty tests/test_runtime_guards.py::test_passive_economic_floor_passes_positive_fill_adjusted_net_ev tests/test_runtime_guards.py::test_executable_snapshot_repricing_uses_native_no_snapshot_for_buy_no tests/test_live_health_probe_forecast_owner.py`
- `PYTHONPATH=. python -m py_compile src/data/polymarket_client.py src/engine/evaluator.py scripts/live_health_probe.py tests/test_executable_market_snapshot_v2.py tests/test_market_scanner_provenance.py tests/test_runtime_guards.py tests/test_live_health_probe_forecast_owner.py`

## Rollback

Revert this branch. The previous behavior fails closed on any one-sided YES book by returning `MARKET_EMPTY_ORDERBOOK`; rollback is conservative but can block valid BUY-entry candidates.
