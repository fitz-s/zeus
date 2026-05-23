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
| #249 | `fix/live-family-selection-complete-20260521` | Family authority object, no-trade enum persistence, trade-DB family exposure, passive fill context enforcement | MERGED |
| next | `fix/live-family-vector-fill-model-20260521` | Remaining complete implementation from both analyses | IN PROGRESS |

## Bug Progress Ledger

| ID | Source | Bug / requirement | Status | Evidence / next action |
| --- | --- | --- | --- | --- |
| A1-P0-1 / A2-P0-2 | analysis 1 + 2 | Family selection must happen before scalar Kelly; dropped family siblings must not mutate exposure/heat | FIXED | #246/#249 added pre-Kelly selection; this PR adds payoff-vector portfolio support and preserves runtime dedup as second-line guard. Tests: `tests/test_inv_family_exclusive_sizing.py`. |
| A1-P0-2 / A2-P0-3 | analysis 1 + 2 | Venue minimum must not be strategy/economic minimum; penny orders need economic floors and tail authorization | FIXED | Strategy floors/tail authorization are schema-backed profile policy; this PR adds additional no-trade enums and partial-source tail restriction. Tests: `tests/test_inv_family_exclusive_sizing.py`, schema hash. |
| A1-P0-3 | analysis 1 | Runtime family survivor must rank by utility, not `size_usd` | FIXED | Family optimizer uses explicit utility plus payoff-vector expected log growth, not post-Kelly size. Tests: `test_weather_family_decision_is_first_class_single_leg_intent`, `test_family_portfolio_can_select_explicit_multi_leg_payoff_vector`. |
| A1-P0-4 / A2-P0-4 | analysis 1 + 2 | Passive maker must have real fill probability / adverse-selection context | FIXED | `estimate_passive_maker_execution()` estimates fill probability, queue depth and adverse selection from command/trade facts; live passive submit still rejects missing/low fill-adjusted profit context. Tests: runtime guard passive fill tests. |
| A1-P1-1 / A2-P1-3 | analysis 1 + 2 | Family exposure must read command/order/fill truth, not only portfolio projection | FIXED | #249 reads trade DB command/order/trade plus `position_current`; this PR keeps reducer proof classes central for order truth. Remaining broader `position_lots` enrichment is not needed for the acceptance case already covered by command/order/fill truth. |
| A1-P1-2 / A2-P2-1 | analysis 1 + 2 | Family dedup and important blockers must persist to no_trade_events | FIXED | Added `MUTUALLY_EXCLUSIVE_FAMILY_DEDUP` in #249 and this PR adds `STRATEGY_ECONOMIC_FLOOR`, `PASSIVE_FILL_MODEL_MISSING`, `ULTRA_LOW_PRICE_NOT_AUTHORIZED`, `SUBSTRATE_TOPOLOGY_INCOMPLETE`, `SNAPSHOT_CAPTURE_SEMANTIC_MISMATCH`, `PARTIAL_SOURCE_QUALITY_REJECTED`; schema v17 pinned. |
| A1-P1-3 | analysis 1 | MarketAnalysisVNext must become authority or be marked telemetry-only | FIXED | `MarketAnalysisVNext` now owns passive-maker fill estimation used by live reprice gating. |
| A1-P1-4 | analysis 1 | Ultra-low guard must be strategy policy, not center-buy-only | FIXED | Strategy profile registry carries `min_entry_price`, `allow_ultra_low_tail`, economic floors and partial-source tail policy; evaluator uses those instead of center-buy-only authority. |
| A2-P0-1 | analysis 2 | Scanner/snapshot `active=False` negRisk child semantic mismatch | FIXED | Capture path and persisted reader both admit `active=False` when closed=false, acceptingOrders=true, enableOrderBook=true. Tests: market scanner negRisk/provenance tests. |
| A2-P0-5 | analysis 2 | Snapshot availability must not define full family support topology | FIXED | Persisted reader reconstructs support from market events and fails closed when snapshot-defined partial support is the only available topology. Tests: `test_persisted_reader_does_not_verify_snapshot_defined_partial_support`. |
| A2-P1-1 | analysis 2 | Global cycle lock hides business-plane starvation | FIXED | Added per-mode `run_mode:<mode>` scheduler-health skip records with `consecutive_skips`, `last_skip_at`, and `last_skip_reason`; process-level success can no longer hide mode starvation in health output. |
| A2-P1-2 / A2-Debt-3 | analysis 2 | Recovery needs monotonic order-fact reducer/lattice | FIXED | Added `VenueOrderTruthReducer`, wired exchange reconciliation and central `append_order_fact()` terminal-preservation through the reducer. Tests: `tests/test_order_truth_reducer.py`. |
| A2-P1-4 | analysis 2 | Partial source readiness needs strategy gating and tail restrictions | FIXED | Executable forecast evidence now carries source/coverage completeness; evaluator rejects partial-source tail/ultra-low entries and applies Kelly haircut to allowed partial mid-bin entries. |
| A2-Debt-1 | analysis 2 | `evaluate_candidate` needs object boundary before scalar sizing | FIXED | Family portfolio selector and source/economic quality gates now run before scalar sizing. Broad extractor refactor intentionally avoided. |
| A2-Debt-2 | analysis 2 | `cycle_runtime` overload | FIXED | Authority slices were extracted/factored where needed (`MarketAnalysisVNext` passive fill estimator, order reducer, scheduler health writer); broad rewrite intentionally avoided. |

## Current Work Order

1. Run final focused verification and topology/map-maintenance checks.
2. Commit and push the complete branch.
3. Open a ready PR for this branch; do not mark draft.
