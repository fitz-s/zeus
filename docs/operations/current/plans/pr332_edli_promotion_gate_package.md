# PR332 EDLI Promotion Gate Package

Created: 2026-05-26
Authority basis: PR332 live-after-merge review packet; user requested implementation of remaining promotion packages.

## Scope

Implement the remaining non-deploying EDLI promotion capabilities inside PR332:

- Runtime user-channel/reconcile processor behind existing disabled-by-default flags.
- DB-backed, machine-readable EDLI live canary proof gate.
- Event-bound realized-edge/profit audit projection and DB-verified scaleout artifact support.
- Forecast-only Day0 scope fail-closed rule.
- Manifest and schema registration required by topology and money-path semantic CI.

## Non-Goals

- No daemon restart.
- No live canary execution.
- No real submit config flip.
- No Day0 hard-fact DAG in this PR; Day0 is explicitly forecast-only/fail-closed.

## Verification Plan

- Targeted runtime and gate tests:
  - `tests/events/test_live_order_reconcile.py`
  - `tests/events/test_live_profit_audit.py`
  - `tests/scripts/test_check_edli_live_canary_gate.py`
  - `tests/money_path/test_edli_online_invariants.py`
- Schema version/hash gate:
  - `python scripts/check_schema_version.py`
- Money-path semantic classifier:
  - `python scripts/ci/semantic_diff_classifier.py --base origin/main --head HEAD --objects architecture/money_path_objects.yaml --mapping architecture/money_path_ci.yaml --fail-on-unregistered`
- Canary gate:
  - `python scripts/check_edli_live_canary_gate.py --artifact state/edli_live_canary_artifact.json --world-db state/zeus-world.db --verify-db --json`
- Promotion artifact:
  - `edli_live` boot recomputes the promotion summary from canonical world DB rows and rejects scalar, stale, mismatched, or pending-reconcile artifacts.
- Required money-path gates before completion claim.
