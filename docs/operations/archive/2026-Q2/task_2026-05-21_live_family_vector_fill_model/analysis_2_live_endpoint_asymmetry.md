# Analysis 2: Live Endpoint Asymmetry Review

Captured from the user's follow-up review packet for this task. This file is the
durable source reference for the second bug list and repair packet.

## Executive Verdict

The second review reframed the live instability as endpoint semantic asymmetry
across scanner, executable snapshot capture, FDR/evaluator/family selection,
venue minimum versus economic minimum, scheduler liveness, recovery truth, and
source readiness.

Most likely unstable live chain:

```text
market substrate refresh / snapshot capture unstable
-> evaluator/kelly/family gate selects low-price passive maker orders
-> passive orders do not fill and enter stale/no-fill/recovery chain
-> command/reconcile/governor oscillates or reduce-only blocks
-> scheduler/health shows process liveness but business-plane trading is not sustained
```

## Findings

### A2-P0-1: Scanner And Snapshot Capture Disagree On negRisk child.active

File named in review:

- `src/data/market_scanner.py`

Problem: `_market_child_is_tradable()` treats negRisk child `active=False` as a
routing label when `closed=False`, `acceptingOrders=True`, and
`enableOrderBook=True`; `capture_executable_market_snapshot()` separately
required `active=True` and rejected the same child as not tradable.

Required repair: use one tradeability authority function in snapshot capture and
let CLOB facts be final execution authority. Do not require `active=True` in the
weather negRisk child runtime path unless redefined as a different typed field.

Required test:

- `test_negrisk_child_active_false_accepting_true_capture_snapshot_admits`

### A2-P0-2: Family Dedup After Kelly Pollutes Sizing

Same as A1-P0-1, with emphasis on current heat and risk throttle side effects.

Required tests:

- `test_family_preselection_before_kelly_sizing`
- `test_dropped_family_edges_do_not_increment_projected_exposure`
- `test_current_heat_not_affected_by_family_edges_that_will_be_deduped`

### A2-P0-3: Venue Minimum Is Not Strategy/Economic Minimum

Same as A1-P0-2, with explicit config fields:

- `strategy_min_economic_notional_usd`
- `min_expected_profit_usd`
- `min_entry_price`
- `tail_arbitrage: explicit_only`

### A2-P0-4: Passive Maker Has Fake Microstructure Risk

Same as A1-P0-4, with required `PassiveMakerExecutionContext` fields:

- `quote_age_ms`
- `best_bid`
- `best_ask`
- `spread_usd`
- `queue_depth_ahead`
- `expected_fill_probability`
- `adverse_selection_score`
- `cancel_after_seconds`

Policy: no passive maker live submit unless fill probability is known,
fill-adjusted expected profit passes the floor, quote age is bounded, and stale
order cleanup capacity is healthy.

### A2-P0-5: Snapshot Availability Defines Persisted Support

Problem: live persisted reader derives available market family support from
fresh executable snapshots. Snapshot refresh has a default outcome cap and can
truncate support. This can create partial family topology while still returning
`authority="VERIFIED"`.

Required repair: separate `MarketFamilyTopology` from
`ExecutableSnapshotOverlay`. Full parent-event topology defines support;
executable snapshots overlay price/orderbook truth by support index. Incomplete
topology must fail closed; partial executable overlay may limit submit ability
but must not define probability support.

### A2-P1-1: Global Cycle Lock Fragilizes Business-Plane Liveness

Problem: `_run_mode()` uses one process-wide `_cycle_lock`, so one long mode can
starve other modes while scheduler/process health still looks alive.

Required repair: staged locks by domain, priority/yield policy, mode skip
counters, and health fields for business-plane liveness.

### A2-P1-2: Recovery Needs A Monotonic Order-Fact Lattice

Problem: recent recovery PRs show order truth is still repaired by incident
rules. Terminal and partial facts need one monotonic reducer instead of scattered
latest-wins seams.

Required repair: introduce `VenueOrderTruthReducer.reduce(facts, trades,
open_orders, point_order) -> CanonicalOrderTruth` with proof classes:

- `TERMINAL_NO_FILL`
- `TERMINAL_FILLED`
- `PARTIAL_WITH_REMAINDER`
- `LIVE_RESTING`
- `UNKNOWN_SIDE_EFFECT`
- `REVIEW_REQUIRED`

Acceptance:

- terminal zero-remainder fact cannot regress to partial/live
- partial with positive trade cannot become no-fill
- absence from open orders alone cannot prove no exposure

### A2-P1-3: Family Exposure Must Read Full Order/Command Surface

Same as A1-P1-1, with explicit blocking sources:

- `venue_commands`
- latest `venue_order_facts`
- `venue_trade_facts`
- `position_current`
- `position_lots`
- unresolved reconcile findings

### A2-P1-4: Partial Source Readiness Can Destabilize Probability Regime

Problem: source runs with `SUCCESS/PARTIAL` and `COMPLETE/PARTIAL` may remain
live eligible when member floors pass. Low-price tail orders should not be
allowed on partial source regimes.

Required repair: add strategy quality gates:

- `partial_source_run_allowed_by_strategy`
- `min_members_floor_by_strategy`
- `complete_required_for_tail_orders`
- `partial_run_kelly_haircut`
- `partial_run_no_trade_event`

### A2-P2-1: Important Live Safety Events Are Fail-Soft Telemetry

Problem: important protection reasons must be schema-backed and persistable.

Required enum promotions:

- `MUTUALLY_EXCLUSIVE_FAMILY_DEDUP`
- `STRATEGY_ECONOMIC_FLOOR`
- `PASSIVE_FILL_MODEL_MISSING`
- `ULTRA_LOW_PRICE_NOT_AUTHORIZED`
- `SUBSTRATE_TOPOLOGY_INCOMPLETE`
- `SNAPSHOT_CAPTURE_SEMANTIC_MISMATCH`

### A2-Debt-1: evaluate_candidate Is Too Broad

Required extraction sequence:

- `HypothesisScanner`
- `FDRSelector`
- `FamilyPortfolioSelector`
- `ScalarSizer`
- `RiskAdmission`
- `DecisionEmitter`

Immediate target: extract `FamilyPortfolioSelector` before scalar sizing.

### A2-Debt-2: cycle_runtime Has Too Many Authorities

Required future split:

- `CycleOrchestrator`
- `CandidateEvaluator`
- `SnapshotAuthority`
- `FinalIntentBuilder`
- `OrderSubmitter`
- `DecisionTelemetryWriter`

### A2-Debt-3: command_recovery / exchange_reconcile Need Reducer Boundary

Same as A2-P1-2; rule landfill should be replaced by a single reducer/lattice.

## Release Gate From Analysis 2

Normal live entry remains unsafe until:

1. Scanner/snapshot `active=False` contradiction is fixed.
2. Family selection happens before scalar Kelly sizing.
3. Strategy economic floors are separate from venue minimum.
4. Passive maker requires fill model or is shadow-only.
5. Family exposure reads command/order/fill truth.
6. Scheduler health exposes business-plane liveness.
7. Recovery uses monotonic order-fact reducer.

