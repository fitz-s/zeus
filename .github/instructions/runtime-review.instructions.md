---
applyTo: "src/execution/**,src/venue/**,src/main.py,src/engine/cycle_runner.py,src/engine/evaluator.py,src/engine/monitor_refresh.py,src/contracts/settlement_semantics.py,src/contracts/execution_price.py,src/contracts/venue_submission_envelope.py,src/contracts/fx_classification.py,src/state/lifecycle_manager.py,src/state/chain_reconciliation.py,src/state/db.py,src/state/ledger.py,src/state/projection.py,src/state/collateral_ledger.py,src/state/venue_command_repo.py,src/state/readiness_repo.py,src/riskguard/**,src/control/**,src/supervisor_api/**,migrations/**,tests/test_*invariant*.py,tests/test_architecture_contracts.py"
---

# Runtime review — Tier 0 + invariant tests

This file applies to live-money / runtime-safety surfaces and the
invariant test bed. These are the paths most worth your review budget.

## Default severity is Critical or Important on this surface

A change here is rarely a Nit. If you genuinely have only style/naming
feedback on these files, suppress and move on. Use review budget on
runtime hazards.

## What to check first

1. **Identity preservation across boundaries.** market_id, condition_id,
   token_id, YES/NO side, price-direction (BUY rounds UP, SELL rounds DOWN),
   `strategy_key`, `temperature_metric`, `physical_quantity`,
   `observation_field`, `data_version`, `LifecyclePhase` enum values.
2. **Settlement semantics.** Every DB write of a settlement value must
   gate through `SettlementSemantics.assert_settlement_value()`.
   `wmo_half_up` is default for WU/NOAA/CWA stations; `oracle_truncate`
   is HKO-only. Swapping these silently mismatches the oracle.
3. **Fail-closed paths.** RED must cancel pending and sweep active
   (INV-19). Authority-loss must degrade to read-only, not RuntimeError
   (INV-20). Void requires CHAIN_EMPTY (INV-18); never void on
   CHAIN_UNKNOWN. non-NORMAL `runtime_posture` blocks new entry (INV-26).
4. **Venue command journaling.** `place_limit_order` is gateway-only
   (INV-24). Every venue side effect needs a `venue_commands` row
   first (INV-28, INV-30). V2 preflight failure → no live
   `place_limit_order` (INV-25). Cycle start scans for unresolved
   command states (INV-31).
5. **Transaction boundaries.** Event append + projection fold must be
   in one transaction (INV-08). DB COMMIT must precede any derived JSON
   export (INV-17). Append-first discipline (INV-03).
6. **Limit orders only.** Market orders are forbidden in execution.
7. **Lifecycle grammar.** Phase strings come only from `LifecyclePhase`
   enum (INV-07). No invented phase strings; transitions go through
   `lifecycle_manager.apply_transition()`.

## Test-bed rules

For `tests/test_architecture_contracts.py` and
`tests/test_*invariant*.py`: weakening a contract test is at least
Important, often Critical. "Activating" an `xfail` requires citing the
exact mark being removed. "Extending" requires the new assertion be
named.

## What to ignore

Style. Naming. Generic refactor preferences. These are Nits and they
should be suppressed if any Critical/Important finding exists.

Deeper context: `REVIEW.md`, `docs/review/code_review.md`,
`architecture/invariants.yaml`.
