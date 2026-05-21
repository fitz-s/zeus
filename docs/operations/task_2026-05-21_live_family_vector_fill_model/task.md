# Live Family Vector / Fill Model Task Ledger

This packet exists to prevent compaction drift. It references the two user
analysis packets that define the complete scope:

- `analysis_1_family_selection_economic_floor.md`
- `analysis_2_live_endpoint_asymmetry.md`

Do not claim this task complete until every OPEN row below is either fixed with
tests or explicitly moved to a new operator-approved packet.

## PR Classification

| PR | Branch | Scope | Status |
| --- | --- | --- | --- |
| #246 | `fix/live-family-selection-econ-floor-20260521` | Pre-Kelly Stage-A family selection and strategy economic floor first slice | MERGED |
| #249 | `fix/live-family-selection-complete-20260521` | Family authority object, no-trade enum persistence, trade-DB family exposure, passive fill context enforcement | REVIEW FIXES PUSHED; CHECKS PENDING |
| next | `fix/live-family-vector-fill-model-20260521` | Remaining complete implementation from both analyses | IN PROGRESS |

## Bug Progress Ledger

| ID | Source | Bug / requirement | Status | Evidence / next action |
| --- | --- | --- | --- | --- |
| A1-P0-1 / A2-P0-2 | analysis 1 + 2 | Family selection must happen before scalar Kelly; dropped family siblings must not mutate exposure/heat | PARTIAL | #246/#249 added pre-Kelly single-leg selection and tests; remaining: vector family optimizer and explicit current-heat regression |
| A1-P0-2 / A2-P0-3 | analysis 1 + 2 | Venue minimum must not be strategy/economic minimum; penny orders need economic floors and tail authorization | PARTIAL | #246/#249 added strategy notional/entry/fill gates; remaining: source-quality/tail coupling and schema-backed additional no-trade reasons |
| A1-P0-3 | analysis 1 | Runtime family survivor must rank by utility, not `size_usd` | PARTIAL | #249 uses expected-net-profit proxy; remaining: full payoff-vector expected log-growth optimizer |
| A1-P0-4 / A2-P0-4 | analysis 1 + 2 | Passive maker must have real fill probability / adverse-selection context | PARTIAL | #249 requires context; remaining: model that estimates fill probability, queue depth, adverse selection from facts/snapshots |
| A1-P1-1 / A2-P1-3 | analysis 1 + 2 | Family exposure must read command/order/fill truth, not only portfolio projection | PARTIAL | #249 reads trade DB command/order/trade plus position_current; remaining: position_lots and unresolved reconcile findings |
| A1-P1-2 / A2-P2-1 | analysis 1 + 2 | Family dedup and important blockers must persist to no_trade_events | PARTIAL | #249 adds `MUTUALLY_EXCLUSIVE_FAMILY_DEDUP`; #249 review fix adds stale CHECK rebuild and fallback; remaining additional enums |
| A1-P1-3 | analysis 1 | MarketAnalysisVNext must become authority or be marked telemetry-only | OPEN | Wire VNext into passive fill/selection authority or mark explicitly telemetry-only |
| A1-P1-4 | analysis 1 | Ultra-low guard must be strategy policy, not center-buy-only | PARTIAL | #246/#249 added strategy floors; remaining: central policy registry alignment |
| A2-P0-1 | analysis 2 | Scanner/snapshot `active=False` negRisk child semantic mismatch | OPEN | Fix `capture_executable_market_snapshot` to use `_market_child_is_tradable`; add relationship test |
| A2-P0-5 | analysis 2 | Snapshot availability must not define full family support topology | OPEN | Introduce topology/overlay separation or fail-closed support completeness check |
| A2-P1-1 | analysis 2 | Global cycle lock hides business-plane starvation | OPEN | Add per-mode liveness counters/health first; staged locks if route admits |
| A2-P1-2 / A2-Debt-3 | analysis 2 | Recovery needs monotonic order-fact reducer/lattice | OPEN | Implement `VenueOrderTruthReducer` and route command/reconcile callers through it |
| A2-P1-4 | analysis 2 | Partial source readiness needs strategy gating and tail restrictions | OPEN | Add partial-source strategy gating / no-trade reason |
| A2-Debt-1 | analysis 2 | `evaluate_candidate` needs object boundary before scalar sizing | PARTIAL | Family selector exists; remaining extraction can stay focused around vector selector |
| A2-Debt-2 | analysis 2 | `cycle_runtime` overload | OPEN | Do not broad-rewrite; extract only authority slices needed for above fixes |

## Current Work Order

1. Finish #249 review lifecycle: monitor checks; merge only if checks pass and no
   new actionable threads appear.
2. In this branch, implement remaining live-money blockers:
   scanner/snapshot active semantics, vector family portfolio, passive fill
   model, topology/overlay guard, order truth reducer, source-quality gating,
   no-trade enum completion, and business-plane liveness telemetry.
3. Run focused relationship tests for each fixed boundary.
4. Open a ready PR for this branch; do not mark draft.

