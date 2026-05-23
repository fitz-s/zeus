# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: operator P0 live-money repair request 2026-05-21; Zeus AGENTS topology planning-lock

# Live Family Selection Economic Floor Plan

## Scope

Repair normal live entry safety without production DB mutation, schema migration,
daemon restart, or venue side effects.

Allowed implementation surfaces:

- `src/engine/evaluator.py`
- `src/engine/cycle_runtime.py`
- `src/strategy/family_exclusive_dedup.py`
- `src/strategy/strategy_profile.py`
- `architecture/strategy_profile_registry.yaml`
- `tests/test_inv_family_exclusive_sizing.py`
- `tests/test_strategy_profile_registry.py`

## Invariants

- Mutually-exclusive weather bins for one `(city, target_date, metric)` family
  are not independent executable orders.
- FDR-selected hypotheses must collapse to one Stage-A family leg before scalar
  Kelly/risk/min-order sizing.
- Venue min order is only venue authority. Strategy live-quality minimum
  notional, price, and expected profit are separate gates.
- Post-only passive low-price entries are not live-authorized without explicit
  tail authority and fill modeling.

## Acceptance

- Pre-Kelly family preselection keeps one edge and produces auditable dropped
  siblings before projected exposure can mutate.
- Existing cycle-runtime family dedup remains as a second-line execution guard.
- One-cent to three-cent live entries for center/opening/imminent strategies are
  rejected even when venue min notional would pass.
- Strategy live-quality floors are read from the strategy profile registry.
- Targeted py_compile and focused tests pass under the Zeus venv.

## Explicit Non-Scope

- No `NoTradeReason` enum/schema migration.
- No first-class `ExclusiveOutcomePortfolio` Stage-B object in this PR.
- No production DB writes, backfills, daemon restarts, or live venue submissions.
